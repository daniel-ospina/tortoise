"""Node A scoreboard (#3863, objectives 5+9) — every registered surface entry resolves.

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

RED BY DESIGN for the five known-dead entries (#3863): their ``sdk_method``
names an attribute ``TortoiseSDK`` does not have. The dead thing is the DECLARED
SDK BINDING, not the capability — this is registry drift, not a missing feature:
the behaviour already exists one import away (``pack_state.get_tenant_packs``,
``pack_manifest_store.upsert_tenant_manifest``, ``navigation.entityProfile``,
``monitoring.metrics``, ``analyze.analyze``) and the MCP handlers already call it.
Do NOT fix, stub, rename or delete them here — the registry surface is FROZEN
until the owner approves the
curated list, and this test is the instrument that keeps the surface measurable
meanwhile. #3835 and #3838 were superseded by #3863 and are closed; #3863 is the
binding, and it stays open until the list is approved.

Those five carry ``xfail(strict=True)``, and that marker is what closes the gate
in BOTH directions:

* a **sixth** dead entry is not in the ledger below, so it **FAILS the build** —
  the surface cannot rot further while the curated list is pending; and
* **repairing** one of the five turns its case into XPASS, and ``strict=True``
  **reds the build** — no expected-failure marker outlives the defect it records.

So this file is red evidence by construction: it is green only while the dead set
is exactly the five recorded in ``_DEAD_LINKS_AWAITING_3863``. Do NOT add a name
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


# --- the pending ledger (#3863) --------------------------------------------
# The five entries whose declared ``sdk_method`` names an attribute TortoiseSDK
# does not have. #3835 and #3838 were SUPERSEDED by #3863 and are CLOSED; the
# binding belongs to #3863, the owner's curation issue, which stays open until the
# curated list is approved. See the module docstring for the two-directional
# fail-closed property these markers implement.
_DEAD_LINKS_AWAITING_3863 = frozenset({
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
    if entry.name not in _DEAD_LINKS_AWAITING_3863:
        return ()
    return (
        pytest.mark.xfail(
            strict=True,
            reason=(
                f"#3863: {entry.name} declares sdk_method={entry.sdk_method!r}, which "
                f"TortoiseSDK does not have. Build-vs-delete is the owner's call on the "
                f"curated list (#3863). Repairing it XPASSes this case, which "
                f"strict=True reds; a sixth dead entry is not listed here and fails."
            ),
        ),
    )


# Resolved once for parametrization: the decorator consumes this list, and the
# coverage assertion pins the cases pytest was actually GIVEN (the decorator's
# argvalues) rather than this declared list.
_TARGETS = [(entry, *_resolve(entry)) for entry in TOOL_REGISTRY]


@pytest.mark.parametrize(
    "entry, target, where",
    [pytest.param(e, t, w, marks=_marks(e), id=e.name) for e, t, w in _TARGETS],
)
def test_every_registered_entry_resolves_to_a_live_method(entry, target, where):
    """Every registered entry resolves to a callable — the scoreboard.

    Mutation that REDs this assertion: rename/delete the target it names (e.g.
    rename ``TortoiseSDK.create_point`` in ``tortoise/sdk.py``) — the getattr
    then returns ``None`` and this case fails. The five ``#3863`` entries below
    are the first red cases — carried as ``xfail(strict=True)``, so repairing one
    reds the build and a sixth dead entry fails it.
    """
    assert callable(target), (
        f"{entry.name} does not resolve: the registry names {where!r}, which is "
        f"{'absent' if target is None else type(target).__name__!r}. The dead thing is the "
        f"DECLARED SDK BINDING — registry drift, not a missing capability. For the #3863 "
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
    the count correct when the run deselects items, which would empty
    ``session.items`` of those cases.
    """
    for mark in test_every_registered_entry_resolves_to_a_live_method.pytestmark:
        if mark.name == "parametrize":
            return list(mark.args[1])
    return []


def _collected_entry_names(session):
    """Names of the registry entries pytest actually COLLECTED a case for."""
    names = []
    for item in session.items:
        if item.function is not test_every_registered_entry_resolves_to_a_live_method:
            continue
        entry = item.callspec.params.get("entry")
        if entry is not None:
            names.append(entry.name)
    return names


def test_resolution_exercises_every_registry_entry(request):
    """The scoreboard must cover ALL entries — a sample (or a truncated list) is
    a false PASS.

    The case set is pinned on what pytest was actually GIVEN — the ``parametrize``
    argvalues the decorator was handed — never on ``_TARGETS`` alone: the
    decorator-only mutation ``_TARGETS[:5]`` leaves ``_TARGETS`` full while the
    parametrised collection drops to 5, so a check against ``_TARGETS`` stays
    green. The collected cross-check is UNCONDITIONAL (fail-closed): a run that
    did not collect every case is not evidence of coverage, whether that is
    because a decorator shrank the set or because the invocation deselected
    cases. Running this file with ``-k`` on a subset therefore REDs by design.

    Mutations that RED this assertion:
    * truncate the decorator's parametrize list (``for e, t, w in _TARGETS[:5]``)
      — the parametrised case set no longer covers the registry; or
    * delete a registry entry (the delete arm of #3863) while its name stays in
      ``_DEAD_LINKS_AWAITING_3863`` — the ledger entry is then consumed by no
      case.
    """
    assert TOOL_REGISTRY, "empty registry — nothing to resolve (fail-closed)"
    names = [param.values[0].name for param in _parametrized_cases()]
    assert len(names) == len(TOOL_REGISTRY), (
        f"the scoreboard PARAMETRISED {len(names)} cases for {len(TOOL_REGISTRY)} "
        f"registry entries — a sample (or a truncated decorator list) is not a scoreboard"
    )
    assert names == [e.name for e in TOOL_REGISTRY], (
        "parametrised case ids do not cover the registry in order — a sample is not a scoreboard"
    )
    collected = _collected_entry_names(request.session)
    assert collected == names, (
        f"the run COLLECTED {len(collected)} cases but the decorator parametrised "
        f"{len(names)} — a run that did not collect the scoreboard is not coverage"
    )
    orphans = sorted(_DEAD_LINKS_AWAITING_3863 - set(names))
    assert not orphans, (
        f"orphaned ledger entries — recorded dead in _DEAD_LINKS_AWAITING_3863 but "
        f"consumed by no collected case: {orphans}"
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
    assert not (expected - registered), (
        f"registry entries absent from the live MCP surface: {sorted(expected - registered)}"
    )
    assert registered == expected, (
        f"live MCP surface drift — extra: {sorted(registered - expected)}"
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
