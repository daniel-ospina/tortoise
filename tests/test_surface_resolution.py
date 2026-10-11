"""Node A scoreboard (#4282, objectives 5+9) — every registered surface entry resolves.

The requirement (directive §8): **execute the resolution and assert the RESOLVED
behaviour**. Do NOT pin source text. A guard pinned to source text is a false
PASS — reverting the thing it guards leaves the text (and the guard) green,
because the text can stay while the behaviour goes. This module therefore never
greps the registry, never calls ``inspect.getsource``, and never compares string
tables. It RESOLVES each registry entry to the object its declaration actually
names and asserts that object is callable:

* ``entry.sdk_method != ""`` → ``getattr(TortoiseSDK, entry.sdk_method)``  (the SDK endpoint)
* ``entry.sdk_method == ""`` → ``getattr(mcp_server, entry.name)``  (custom MCP handler — the
  entry's own declared surface; these are the entries whose tool body lives in mcp_server.py)

The LIVE registered surface (``mcp._list_tools()`` — the transform-free
component list every registry entry must be on, i.e. the stdio agent surface;
the HTTP listing is NARROWER BY DESIGN and is not what this file pins — see
accepted residual 3) is resolved a second, independent way, so "resolves to a live
method" is asserted on the surface an agent calls, not only on the declaration.

GREEN TODAY (#4035 / PR #4043): the four entries this instrument recorded dead —
``tortoise_packs_list``, ``tortoise_pack_install``, ``tortoise_entity_profile``,
``tortoise_analyze`` — were REPAIRED by declaring them handler-served
(``sdk_method=""``, resolved via ``getattr(mcp_server, name)``), so
``_DEAD_LINKS_AWAITING_4282`` is now EMPTY and every case above passes outright.
The dead thing had always been the DECLARED SDK BINDING, not the capability: the
behaviour already existed one import away (``pack_state.get_tenant_packs``,
``pack_manifest_store.upsert_tenant_manifest``, ``navigation.entityProfile``,
``analyze.analyze``) and the MCP handlers already called it. #4282 remains the
epic for the wider canonical-list work; the four rows repaired here are done.

Those entries carried ``xfail(strict=True)``, and that marker is what closed the
gate in BOTH directions:

* an UNLISTED dead entry is not in the ledger below, so it **FAILS the build** —
  the surface cannot rot further while the curated list is pending; and
* **repairing** one turns its case into XPASS, and ``strict=True``
  **reds the build** — no expected-failure marker outlives the defect it records.

**Why retiring those four is NOT the suppression the ledger's rule forbids.**
The rule bars deleting a name to hide a FAILURE. Here the repair actually landed,
and the very marker the rule protects is what FORCES the retirement —
``strict=True`` turns a repaired entry into XPASS and reds the build, so an
un-retired ledger entry is now itself the build failure, not a safety net. The
ledger tracks PENDING repairs and must shrink when one lands; leaving the names
in would convert the pending-decision record into exactly the rubber stamp it was
built to prevent. The two directions are not symmetric: you may never delete a
name to silence a red, and you must always delete one whose red has been repaired.

The case set is guarded in two layers: the collection gate below runs at IMPORT
time (during collection, before pytest applies ``-k``/``-m``/node-id selection, so
no invocation can hide a shrunken or orphaned case set), and
``test_resolution_exercises_every_registry_entry`` adds the run receipt — the
declared cases must actually EXECUTE, not merely be declared or collected.

So this file is red evidence by construction while the dead set is NON-EMPTY: it
is green only while the dead set is exactly the set recorded in
``_DEAD_LINKS_AWAITING_4282``. Do NOT add a name to that ledger to silence a
failure, and do NOT remove a name to SILENCE a failure either — a name is retired
ONLY when its repair has actually landed, and ``strict=True`` makes that
self-enforcing (an un-retired repair XPASSes and reds). The ledger is the
pending-decision record, not a suppression list.

The reds below are of two KINDS and must not read alike. A GENUINE defect is an
entry that used to resolve and stopped. The EXPECTED surface change of #4282 is
the canonical redesign — 98 -> 25 MCP tools and 150 -> 40 SDK methods — which
rewrites the registry this instrument pins (82 entries today; the 99-entry
figure below is `_BASELINE_ENTRY_COUNT`, the PRE-#4282 size the shrink is
measured from, not the registry as it stands) and legitimately leaves entries
unresolved. The failure messages therefore append the #4282 rendezvous
context ONLY when the run's shape matches the redesign, gated on the registry
having SHRUNK by >= ``_RESHAPE_MIN_ENTRIES`` (a defect does not rewrite the
registry), so no small-scale genuine regression can be pre-excused as "the
redesign". When the rendezvous opens, the instruction is to RETARGET the
instrument at Phase 0.4 — never to weaken or delete the INSTRUMENT to make it green.
The rendezvous MACHINERY itself is transition-scoped and is deleted at that retarget.

Accepted residuals (#3905). The four false-green / false-red paths found while
scoping #3900 are each closed here or recorded with the mechanism that keeps them
accepted — an unrecorded residual is how a guard silently stops guarding:

1. ``handler_only`` LOCATION COUPLING — ACCEPTED (owner: the registration seam,
   #2210 / #454). ``_resolve`` resolves an empty ``sdk_method`` through
   ``getattr(mcp_server, entry.name)`` — a module-GLOBAL name — while
   ``FastMCPAdapter.register_all`` is handed ``{t.name: globals()[t.name] …}``
   (``mcp_server.py``, the module-bottom ``_adapter.register_all``) and merely
   WARNS when a name is absent (``register_all`` — "no handler — skipped"). A
   handler moved to a submodule while the adapter still resolves it is a
   legitimate refactor that reds the resolution case for that entry. The
   instrument cannot see the adapter's own handler map, so closing the coupling
   means closing the seam — #2210/#454's change, not this one. The BEHAVIOUR is
   covered independently of the module-global name:
   ``test_live_mcp_surface_registers_every_entry`` proves the entry is served and
   ``test_live_mcp_tools_are_callable`` proves the served tool has a callable
   ``fn``.

2. ``hosted_only`` DECLARED BUT UNWIRED — ACCEPTED (owner: the #1935 wiring
   change). ``ToolDefinition.hosted_only`` is declared (``tool_registry.py``) and
   set on ``tortoise_pack_install``; NOTHING reads it, so every registry entry is
   registered on every surface and the both-direction equality in
   ``test_live_mcp_surface_registers_every_entry`` is the correct assertion
   TODAY. The moment #1935 wires the flag, a self-host run legitimately serves
   one fewer entry and that equality would red a CORRECT deployment, so the
   wiring change must carry the one-directional form — the missing set contained
   in ``{e.name for e in TOOL_REGISTRY if e.hosted_only}`` while the extra set
   stays empty — and must not reach for a hide-by-transform implementation:
   ``_HTTPToolFilter`` is the precedent for exactly that, and it drops the 7
   ``http_policy=False`` entries (measured, see residual 3), which ARE on the
   stdio MCP surface. Weakening the equality NOW to pre-empt the wiring would
   instead buy a permanent false GREEN on every hosted_only entry while the flag
   is inert — the wrong trade, and why this is recorded rather than pre-applied.

3. PRIVATE fastmcp API — ACCEPTED: the public swap was TESTED AND REJECTED. The
   preference was to read the PUBLIC ``mcp.list_tools()``; it is not adoptable on
   this tree, and ``mcp._list_tools()`` is KEPT, because the public call applies
   the instance's transforms and ``create_http_app()`` attaches ``_HTTPToolFilter``
   to the shared module-level ``mcp`` (``mcp_server.py``, registered once),
   executed at import by ``tortoise/hosted_api.py`` — which the test lane pulls
   in. Measured under pytest: ``list_tools()`` and
   ``list_tools(run_middleware=False)`` BOTH return 75 of 82 names, missing
   exactly the 7 ``http_policy=False`` entries, while ``_list_tools()`` returns
   82. In a BARE interpreter the filter is not yet attached and both calls return
   82 — so the public swap would be green locally and red in the lane that runs
   it, the worst kind of coupling: a guard whose subject changes with the
   process. The issue's "no transform is attached to the global mcp" premise is
   stale for this tree.
   What the residual actually requires is the coupling COMMENT and the
   dep-upgrade TRIPWIRE — both present — plus correcting this instrument's own
   claim to name the transform-free REGISTRATION surface rather than the
   HTTP-filtered listing. The private-call coupling is commented at the
   assertions and pinned by ``test_live_surface_dependency_tripwire``.

4. COVERAGE-PREDICATE FRAGILITY — CLOSED, with the ``-k``/xdist residual stated.
   The case set is read from the decorator's OWN argument list
   (``_parametrized_cases`` → ``pytestmark`` argvalues), never from ``_TARGETS``
   and never from a naive ``item.name`` filter — which is EMPTY for parametrized
   ids and would false-RED. The import-time gate is not deselectable and the
   in-test receipt covers EXECUTION; the residual that a deselected or
   xdist-partial run can shorten ``_EXECUTED`` is stated on
   ``test_resolution_exercises_every_registry_entry`` and can only be a false RED,
   never a false GREEN. The skip vector is closed without a checked-in nodeid
   list: the skip-guard manifest is EMITTED from ``--collect-only`` over the same
   file list the run uses (``tools/skip-guard.py --emit-manifest``), so these
   node-ids are expected automatically (verified: 86 expected — 82 parametrized +
   4 plain — emitted for this file).
"""
from __future__ import annotations

import asyncio

import pytest

import tortoise.mcp_server as mcp_server
from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import TOOL_REGISTRY


def _resolve(entry):
    """Resolve one registry entry to the object its declaration names.

    Returns ``(target, where)`` — the resolved object (or ``None`` when the
    declared target is absent) and a human-readable name for it. This is the
    resolution the failure message reports; it is NOT a text comparison.
    """
    if entry.sdk_method:
        return getattr(TortoiseSDK, entry.sdk_method, None), f"TortoiseSDK.{entry.sdk_method}"
    return getattr(mcp_server, entry.name, None), f"mcp_server.{entry.name}"


# --- the pending ledger (#4282) --------------------------------------------
# EMPTY as of #4035 / PR #4043. The four entries this instrument originally
# recorded dead — ``tortoise_packs_list`` (declared ``get_tenant_packs``),
# ``tortoise_pack_install`` (``upsert_tenant_manifest``),
# ``tortoise_entity_profile`` (``entity_profile``) and ``tortoise_analyze``
# (``analyze``) — were REPAIRED by declaring them handler-served: ``sdk_method=""``
# and the resolution target is ``getattr(mcp_server, name)`` (see ``_resolve``).
# This ledger was the pending-decision record for that repair; the repair landed,
# so the record is retired. Leaving the names in would XPASS under
# ``strict=True`` and red the build — that is the point of ``strict``.
#
# `tortoise_health` was never retired here: #3883 removed the NAME from the
# advertised surface (#3863's curation), so it has no case in this instrument at
# all. Its dead declared binding stays visible in the retired half of the
# baseline (`config/surface-manifest.yml::retired`).
#
# Re-populate this ledger ONLY for a newly-discovered dead DECLARED binding, with
# the same pending-decision reason — never to silence a failure.
_DEAD_LINKS_AWAITING_4282: frozenset[str] = frozenset()


def _marks(entry):
    """``xfail(strict=True)`` for a known-dead entry, and nothing for any other.

    Returning NO mark for an unlisted entry is the fail-closed half: an unlisted dead
    entry reaches the assertion unmarked and FAILS the build.
    """
    if entry.name not in _DEAD_LINKS_AWAITING_4282:
        return ()
    # The declared target differs by SHAPE: an empty ``sdk_method`` resolves
    # through the entry's own MCP handler (``mcp_server.<name>``), any other
    # value through ``TortoiseSDK.<method>``. The reason must name the binding
    # that is actually dead — a blanket "declares sdk_method=..." renders the
    # false claim "declares sdk_method=''" for a handler-served entry.
    where = _resolve(entry)[1]  # the binding actually named by the declaration
    return (
        pytest.mark.xfail(
            strict=True,
            reason=(
                f"#4282: {entry.name} declares a binding that does not resolve — "
                f"the registry names {where!r}, which is absent. "
                f"Repairing it is #4282's work on the "
                f"owner-approved canonical list (docs/product/canonical-mcp-tools.md). "
                f"Repairing it XPASSes this case, which "
                f"strict=True reds; an unlisted dead entry is not listed here and fails. "
                f"When the #4282 redesign lands ({_EXPECTED_MCP_TOOLS} MCP tools from 98, "
                f"{_EXPECTED_SDK_METHODS} SDK methods from 150), re-point this ledger at "
                f"the new surface and RETARGET the instrument at Phase 0.4 — do NOT "
                f"weaken or delete the test to make it green."
            ),
        ),
    )


# Resolved once for parametrization: the decorator consumes this list, and the
# import-time gate below pins the cases pytest was actually GIVEN (the decorator's
# argvalues) rather than this declared list.
_TARGETS = [(entry, *_resolve(entry)) for entry in TOOL_REGISTRY]

# --- the #4282 rendezvous, and the gate that keeps it from being an excuse ---
# A blanket "this is expected" on every red would turn this instrument into a
# rubber stamp and let a GENUINE defect be waved through as "the redesign". The
# note is therefore gated on SHAPE, and the gate is deliberately conservative:
# it can only open when the registry itself has SHRUNK by >= _RESHAPE_MIN_ENTRIES
# from its pinned pre-#4282 size. A defect does not rewrite the registry —
# renaming one SDK method leaves the entry count where it was — so no small-scale
# genuine regression can ever pick up the note. Inside that gate the note
# additionally requires the surface to be HOLLOWED (>= _HOLLOWED_MIN_DEAD entries
# unresolved in absolute terms, or >= a quarter of whatever the registry now is,
# at a floor of 5) or the curated ledger to be ORPHANED (its recorded names gone
# from the registry).
#
# Residuals, stated rather than hidden:
#  * a STAGED redesign that rewrites tortoise/sdk.py before the registry still
#    has the pinned size, so it is indistinguishable from a mass rename and stays
#    a plain defect report — the intended direction of the error;
#  * once the gate opens it opens for the WHOLE run, so a genuine regression
#    running alongside the redesign also carries the note — which is why the note
#    withholds the excuse and tells the reader to still read the specific id;
#  * the predicate keys on SHAPE, not on the change's identity, so an unrelated
#    >= _RESHAPE_MIN_ENTRIES-entry shrink would be reported under #4282's name; and
#  * _BASELINE_ENTRY_COUNT and _RESHAPE_MIN_ENTRIES are both expressed against the
#    PRE-#4282 registry, so this rendezvous machinery is transition-scoped and the
#    Phase 0.4 retarget must DELETE it rather than re-baseline it: resetting the
#    baseline to the new registry size makes `shrunk` (n <= baseline - 25)
#    unsatisfiable, while leaving it at 99 keeps a shrink test that no longer
#    describes anything under it. Re-baselining is not a fix.
# Investigate the diff; do not assume.
_BASELINE_ENTRY_COUNT = 99       # the pre-#4282 registry this instrument pins
_EXPECTED_MCP_TOOLS = 25         # #4282: 98 -> 25
_EXPECTED_SDK_METHODS = 40       # #4282: 150 -> 40
_RESHAPE_MIN_ENTRIES = 25        # a defect does not remove a quarter of the surface
_HOLLOWED_MIN_DEAD = 20          # a large absolute hole / a mass break
_HOLLOWED_FRACTION = 0.25        # ...or a quarter of the (possibly shrunken) registry,
_HOLLOWED_FRACTION_FLOOR = 5     #     at a floor of 5 entries

_DEAD_TARGETS = tuple(e.name for e, target, _where in _TARGETS if not callable(target))

_RENDEZVOUS_NOTE = (
    "\n"
    "  \u26a0\ufe0f IF THIS DIFF LANDS THE #4282 SURFACE REDESIGN, THIS RED IS EXPECTED.\n"
    "  Otherwise it is a GENUINE DEFECT and this note does not excuse it.\n"
    "  The #4282 redesign lands 25 MCP tools (from 98) and 40 SDK methods (from 150),\n"
    f"  so the pre-#4282 registry of {_BASELINE_ENTRY_COUNT} entries is rewritten and\n"
    "  entries legitimately stop resolving — a RENDEZVOUS, not an instrument fault.\n"
    "    * RETARGET POINT: retarget this instrument at Phase 0.4, when\n"
    "      tortoise/__init__.py gains __all__ and there is a real declaration to pin.\n"
    "      This rendezvous machinery is TRANSITION-SCOPED and must be DELETED there.\n"
    "      _BASELINE_ENTRY_COUNT and _RESHAPE_MIN_ENTRIES are measured against the\n"
    "      PRE-#4282 registry, so re-baselining is not a fix: resetting the baseline to\n"
    "      the new size makes the shrink test unsatisfiable (n <= baseline - 25), while\n"
    "      leaving it at 99 leaves a shrink test that no longer describes anything.\n"
    "    * DO NOT weaken, skip or delete this test to make it green. A red that reads\n"
    "      like a bug gets worked around; a red that reads like a rendezvous gets acted\n"
    "      on. Re-point the ledger at the new surface instead.\n"
    "    * This note is attached to the entry-resolution, collection-gate and\n"
    "      live-surface failures that arise in this shape — including any genuine\n"
    "      regression running alongside the redesign — so still read the specific\n"
    "      unresolved id above."
)


def _looks_like_4282(orphans: int = 0) -> bool:
    """True only when this run's shape matches the #4282 redesign.

    The hard invariant: the note cannot open unless the registry has shrunk by
    >= ``_RESHAPE_MIN_ENTRIES``. A single-entry (or handful) genuine regression
    leaves the registry at ``_BASELINE_ENTRY_COUNT`` and therefore can never be
    pre-excused; it keeps the plain defect report.
    """
    n = len(TOOL_REGISTRY)
    dead = len(_DEAD_TARGETS)
    shrunk = n <= _BASELINE_ENTRY_COUNT - _RESHAPE_MIN_ENTRIES
    hollowed = dead >= _HOLLOWED_MIN_DEAD or (
        dead >= _HOLLOWED_FRACTION_FLOOR and dead >= _HOLLOWED_FRACTION * n
    )
    ledger_gone = orphans > 0
    return shrunk and (hollowed or ledger_gone)


def _rendezvous(orphans: int = 0) -> str:
    """The #4282 context, or ``""`` when this red reads as a plain defect."""
    return _RENDEZVOUS_NOTE if _looks_like_4282(orphans) else ""


# Cases that actually EXECUTED, recorded by the scoreboard as each case runs. The
# import-time gate proves the case set was declared; only this proves it RAN.
_EXECUTED: set[str] = set()


@pytest.mark.parametrize(
    "entry, target, where",
    [pytest.param(e, t, w, marks=_marks(e), id=e.name) for e, t, w in _TARGETS],
)
def test_every_registered_entry_resolves_to_a_live_method(entry, target, where):
    """Every registered entry resolves to a callable — the scoreboard.

    Mutation that REDs this assertion: rename/delete the target it names (e.g.
    rename ``TortoiseSDK.create_point`` in ``tortoise/sdk.py``) — the getattr
    then returns ``None`` and this case fails. An entry the ledger marks
    known-dead carries ``xfail(strict=True)``, so repairing one reds the build
    and an unlisted dead entry fails it — both directions fail closed. (The
    ledger is EMPTY today; see the module docstring.)
    """
    _EXECUTED.add(entry.name)
    assert callable(target), (
        f"{entry.name} does not resolve: the registry names {where!r}, which is "
        f"{'absent' if target is None else type(target).__name__!r}. Unresolved this run: "
        f"{len(_DEAD_TARGETS)} of {len(TOOL_REGISTRY)} registry entries. The dead thing is "
        f"the DECLARED SDK BINDING — registry drift, not a missing capability. Check what "
        f"the declaration actually names before repairing it: a ``sdk_method`` resolves on "
        f"``TortoiseSDK`` and an empty one through the entry's own MCP handler "
        f"(``mcp_server.<name>``); when the capability lives on a helper instead, declaring "
        f"the entry handler-served is the repair. "
        f"Declared sdk_method={entry.sdk_method!r}."
        f"{_rendezvous()}"
    )


def _parametrized_cases():
    """The argvalues the scoreboard's ``parametrize`` decorator was handed.

    Read from the function's own ``pytestmark`` — the case set pytest actually
    parametrised — rather than from ``_TARGETS``. The decorator consumes
    ``_TARGETS``, so a decorator-only mutation (``_TARGETS[:5]``) shrinks the
    collection while ``_TARGETS`` itself stays full: an assertion on ``_TARGETS``
    is tautological in exactly the case that matters. Reading the marks also keeps
    the count correct when the run deselects items.

    This is the closure of #3905 residual 4, and it is deliberately NOT a
    ``pytest_collection_modifyitems`` hook keyed on ``item.function.__name__`` /
    ``hasattr(item, "callspec")``: a hook runs AFTER ``-k``/``-m``/node-id
    selection (and per xdist worker sees only its assigned slice), where pytest's
    ``item.name`` for a parametrized id is ``"<test>[<param>]"`` — a naive filter
    is EMPTY and false-REDs. The decorator's own argvalues are the collected case
    set, are available during collection, and cannot be deselected.
    """
    for mark in test_every_registered_entry_resolves_to_a_live_method.pytestmark:
        if mark.name == "parametrize":
            return list(mark.args[1])
    return []


# --- collection gate: fail-closed and NON-DESELECTABLE ----------------------
# Validated at IMPORT time, i.e. during collection and BEFORE pytest applies
# `-k`, `-m` or node-id selection. A shrunken or orphaned case set therefore
# cannot be hidden by deselecting the test that reports it — collection errors
# out instead, whatever the invocation selects. This is the declaration half of
# the guard; ``test_resolution_exercises_every_registry_entry`` adds the run
# receipt (the declared cases must EXECUTE, not merely be declared).
_CASES = [param.values[0].name for param in _parametrized_cases()]
assert _CASES, "empty scoreboard — no registry entries to resolve (fail-closed)"
assert [e.name for e in TOOL_REGISTRY] == _CASES, (
    f"the scoreboard PARAMETRISED {len(_CASES)} cases for {len(TOOL_REGISTRY)} registry "
    f"entries — a sample (or a truncated decorator list) is not a scoreboard. "
    f"Unresolved in the full registry: {len(_DEAD_TARGETS)} of {len(TOOL_REGISTRY)}."
    f"{_rendezvous()}"
)
_ORPHANS = sorted(_DEAD_LINKS_AWAITING_4282 - set(_CASES))
assert not _ORPHANS, (
    f"orphaned ledger entries — recorded dead in _DEAD_LINKS_AWAITING_4282 but "
    f"consumed by no case: {_ORPHANS}. Either the registry renamed/removed those "
    f"entries without re-pointing the ledger, or entries were deleted from the "
    f"registry to silence their cases. The registry now declares "
    f"{len(TOOL_REGISTRY)} entries."
    f"{_rendezvous(len(_ORPHANS))}"
)


def test_resolution_exercises_every_registry_entry():
    """The scoreboard must EXECUTE every entry — a declared sample is a false PASS.

    The declared case set is pinned by the import-time gate above, which no
    invocation can deselect. This test adds the run receipt: the declared cases
    must actually have RUN. A ``skip`` marker, a subset selection or a shrunken
    collection leaves ``_EXECUTED`` short of ``_CASES`` and FAILS, where an
    assertion on the collected/declared set alone would stay green.

    ``_EXECUTED`` is populated by the scoreboard cases as they execute, so this
    depends on them running first (definition order in this module). That
    dependency is fail-closed: if they did not run, ``_EXECUTED`` is empty and
    this test FAILS — it can cause a false RED, never a false GREEN.

    Residual, stated rather than hidden: pytest collects no hooks from test
    modules, so an invocation that deselects THIS test (``-k``/``-m``/node-id)
    does not execute the run receipt. The declaration is still validated at import
    then, and CI runs the module whole.
    """
    not_executed = sorted(set(_CASES) - _EXECUTED)
    assert not not_executed, (
        f"the scoreboard EXECUTED {len(_EXECUTED)} of {len(_CASES)} declared cases — "
        f"collected or declared is not coverage. Never executed: {not_executed}"
    )


def test_live_mcp_surface_registers_every_entry():
    """Every registry entry is on the LIVE, transform-free FastMCP surface.

    Read through ``mcp._list_tools()``, NOT the public ``mcp.list_tools()``
    (residual 3, #3905 — the public swap was measured and REJECTED): the public
    call applies the instance's transforms, and ``create_http_app()`` attaches
    ``_HTTPToolFilter`` to this shared module-level ``mcp``, so under the test
    lane ``list_tools()`` returns 75 of 82 — missing exactly the 7
    ``http_policy=False`` entries this assertion must see. ``_list_tools()``
    resolves the components without transforms and returns 82. The private-API
    coupling is deliberate and pinned by
    ``test_live_surface_dependency_tripwire``.

    Mutation that REDs this assertion: make the adapter skip an entry (e.g.
    ``if entry.name == "tortoise_query": continue`` inside
    ``FastMCPAdapter.register_all``) — the entry vanishes from the live tool
    list while the registry still declares it.
    """
    tools = asyncio.run(mcp_server.mcp._list_tools())
    registered = {t.name for t in tools}
    expected = {e.name for e in TOOL_REGISTRY}
    assert registered == expected, (
        f"live MCP surface drift — absent: {sorted(expected - registered)}, "
        f"extra: {sorted(registered - expected)}"
        f"{_rendezvous()}"
    )


def test_live_mcp_tools_are_callable():
    """Each live tool resolves to a callable ENTRYPOINT — not just a name.

    Read through ``mcp._list_tools()`` (residual 3, #3905); see
    ``test_live_mcp_surface_registers_every_entry`` for why the public
    ``list_tools()`` was rejected, and ``test_live_surface_dependency_tripwire``
    for the coupling pin.

    The entrypoint is ``FunctionTool.fn``. Asserting on ``run`` instead (as this
    once did) verifies nothing: ``FunctionTool.run`` is defined on the class, so
    it is callable for every instance ``_list_tools()`` can return — a registered
    tool with a broken entrypoint stays green.

    Mutation that REDs this assertion: give a registered tool a non-callable
    entrypoint (``tools[0].fn = 42``) — the entrypoint list then names it.
    """
    tools = asyncio.run(mcp_server.mcp._list_tools())
    assert tools, "live MCP surface is empty (fail-closed)"
    not_callable = [t.name for t in tools if not callable(getattr(t, "fn", None))]
    assert not not_callable, f"live MCP tools with no callable entrypoint: {not_callable}"


# --- dependency coupling: the private ``_list_tools()`` (residual 3, #3905) ---
# The live-surface assertions resolve the registration surface through
# fastmcp's PRIVATE ``FastMCP._list_tools()``, because the public
# ``list_tools()`` applies the ``_HTTPToolFilter`` that ``create_http_app()``
# attaches to this shared instance (measured: 75 of 82). Three facts about the
# private method are load-bearing: it stays present, it stays async on a
# ``FastMCP`` instance, and each returned tool exposes ``.name`` and ``.fn``. A
# fastmcp upgrade can change any of them, and the breakage would present as a
# guard that resolves nothing rather than as a dependency error — so the version
# below is a deliberate TRIPWIRE, not a pin on the dependency.
_FASTMCP_VERIFIED_VERSION = "3.4.6"


def test_live_surface_dependency_tripwire():
    """TRIPWIRE (#3905 residual 3): re-verify fastmcp's ``_list_tools()`` contract.

    Deliberately REDs on ANY fastmcp upgrade. That is the point: the three facts
    in the comment above are what the live-surface assertions rest on, and a
    silent change to any of them would otherwise turn those assertions into
    no-ops. On a red here, re-verify the facts AND re-measure the public/private
    split (``list_tools()`` vs ``_list_tools()``, 75 vs 82 expected on this
    tree), then move ``_FASTMCP_VERIFIED_VERSION`` and this test together.
    """
    import fastmcp

    assert fastmcp.__version__ == _FASTMCP_VERIFIED_VERSION, (
        f"fastmcp {fastmcp.__version__} != the verified {_FASTMCP_VERIFIED_VERSION}. "
        "Re-verify that FastMCP._list_tools() is present, async, returns objects "
        "exposing 'name' and 'fn', and that the public list_tools() still applies "
        "the _HTTPToolFilter (tests/test_surface_resolution.py, residual 3), then "
        "move _FASTMCP_VERIFIED_VERSION."
    )
    assert callable(getattr(mcp_server.mcp, "_list_tools", None)), (
        "FastMCP no longer exposes _list_tools() — the live-surface assertions in "
        "this module can no longer resolve the registration surface"
    )
