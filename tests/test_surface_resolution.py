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

The LIVE registered surface (``mcp._list_tools()`` — the tools an agent is
actually served) is resolved a second, independent way, so "resolves to a live
method" is asserted on the surface an agent calls, not only on the declaration.

RED BY DESIGN for the five known-dead entries (#4282): their ``sdk_method``
names an attribute ``TortoiseSDK`` does not have. The dead thing is the DECLARED
SDK BINDING, not the capability — this is registry drift, not a missing feature:
the behaviour already exists one import away (``pack_state.get_tenant_packs``,
``pack_manifest_store.upsert_tenant_manifest``, ``navigation.entityProfile``,
``monitoring.metrics``, ``analyze.analyze``) and the MCP handlers already call it.
Do NOT fix, stub, rename or delete them here — the owner-approved canonical list
(``docs/product/canonical-mcp-tools.md``, merged ``8375c7921``) names this surface,
and #4282 is the implementation epic that repairs these declarations. Until #4282
lands, this test is the instrument that keeps the surface measurable. #3835 and
#3838 were superseded by #3863, which is now CLOSED.

Those five carry ``xfail(strict=True)``, and that marker is what closes the gate
in BOTH directions:

* a **sixth** dead entry is not in the ledger below, so it **FAILS the build** —
  the surface cannot rot further while the curated list is pending; and
* **repairing** one of the five turns its case into XPASS, and ``strict=True``
  **reds the build** — no expected-failure marker outlives the defect it records.

The case set is guarded in two layers: the collection gate below runs at IMPORT
time (during collection, before pytest applies ``-k``/``-m``/node-id selection, so
no invocation can hide a shrunken or orphaned case set), and
``test_resolution_exercises_every_registry_entry`` adds the run receipt — the
declared cases must actually EXECUTE, not merely be declared or collected.

So this file is red evidence by construction: it is green only while the dead set
is exactly the five recorded in ``_DEAD_LINKS_AWAITING_4282``. Do NOT add a name
to that ledger to silence a failure, and do NOT remove a name to accommodate a
repair — the ledger is the pending-decision record, not a suppression list.
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
# The five entries whose declared ``sdk_method`` names an attribute TortoiseSDK
# does not have. #3835 and #3838 were SUPERSEDED by #3863 and are CLOSED; #3863's
# curation decision landed as the approved list in
# ``docs/product/canonical-mcp-tools.md`` (merged 8375c7921), and the binding for
# repairing these five is now #4282, the implementation epic. See the module
# docstring for the two-directional fail-closed property these markers implement.
_DEAD_LINKS_AWAITING_4282 = frozenset({
    "tortoise_packs_list",
    "tortoise_pack_install",
    "tortoise_entity_profile",
    "tortoise_health",
    "tortoise_analyze",
})


def _marks(entry):
    """``xfail(strict=True)`` for a known-dead entry, and nothing for any other.

    Returning NO mark for an unlisted entry is the fail-closed half: a sixth dead
    entry reaches the assertion unmarked and FAILS the build.
    """
    if entry.name not in _DEAD_LINKS_AWAITING_4282:
        return ()
    return (
        pytest.mark.xfail(
            strict=True,
            reason=(
                f"#4282: {entry.name} declares sdk_method={entry.sdk_method!r}, which "
                f"TortoiseSDK does not have. Repairing it is #4282's work on the "
                f"owner-approved canonical list (docs/product/canonical-mcp-tools.md). "
                f"Repairing it XPASSes this case, which "
                f"strict=True reds; a sixth dead entry is not listed here and fails."
            ),
        ),
    )


# Resolved once for parametrization: the decorator consumes this list, and the
# import-time gate below pins the cases pytest was actually GIVEN (the decorator's
# argvalues) rather than this declared list.
_TARGETS = [(entry, *_resolve(entry)) for entry in TOOL_REGISTRY]

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
    then returns ``None`` and this case fails. The five ``#4282`` entries below
    are the first red cases — carried as ``xfail(strict=True)``, so repairing one
    reds the build and a sixth dead entry fails it.
    """
    _EXECUTED.add(entry.name)
    assert callable(target), (
        f"{entry.name} does not resolve: the registry names {where!r}, which is "
        f"{'absent' if target is None else type(target).__name__!r}. The dead thing is the "
        f"DECLARED SDK BINDING — registry drift, not a missing capability. For the #4282 "
        f"entries the behaviour already exists one import away "
        f"(pack_state.get_tenant_packs, pack_manifest_store.upsert_tenant_manifest, "
        f"navigation.entityProfile, monitoring.metrics, analyze.analyze) and the MCP "
        f"handlers already call it; what is absent is the SDK name the registry declares. "
        f"Declared sdk_method={entry.sdk_method!r}."
    )


def _parametrized_cases():
    """The argvalues the scoreboard's ``parametrize`` decorator was handed.

    Read from the function's own ``pytestmark`` — the case set pytest actually
    parametrised — rather than from ``_TARGETS``. The decorator consumes
    ``_TARGETS``, so a decorator-only mutation (``_TARGETS[:5]``) shrinks the
    collection while ``_TARGETS`` itself stays full: an assertion on ``_TARGETS``
    is tautological in exactly the case that matters. Reading the marks also keeps
    the count correct when the run deselects items.
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
    f"entries — a sample (or a truncated decorator list) is not a scoreboard"
)
_ORPHANS = sorted(_DEAD_LINKS_AWAITING_4282 - set(_CASES))
assert not _ORPHANS, (
    f"orphaned ledger entries — recorded dead in _DEAD_LINKS_AWAITING_4282 but "
    f"consumed by no case: {_ORPHANS}"
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
    """Every registry entry is on the LIVE, agent-facing FastMCP surface.

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
    )


def test_live_mcp_tools_are_callable():
    """Each live tool resolves to a callable ENTRYPOINT — not just a name.

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
