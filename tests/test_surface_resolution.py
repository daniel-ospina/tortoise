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
names an attribute ``TortoiseSDK`` does not have. Do NOT fix, stub, rename or
delete them here — the registry surface is FROZEN until the owner approves the
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


# Resolved once for parametrization + the coverage assertion below.
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
        f"{'absent' if target is None else type(target).__name__!r} — an agent calling "
        f"{entry.name} reaches a dead method (#3863). "
        f"Declared sdk_method={entry.sdk_method!r}."
    )


def test_resolution_exercises_every_registry_entry():
    """The scoreboard must cover ALL entries — a sample (or a truncated list) is
    a false PASS.

    Mutation that REDs this assertion: truncate the parametrized set (e.g.
    ``TOOL_REGISTRY[:5]`` in the ``_TARGETS`` comprehension) — the resolved ids
    then stop being a 1:1 cover of the registry.
    """
    assert TOOL_REGISTRY, "empty registry — nothing to resolve (fail-closed)"
    assert len(_TARGETS) == len(TOOL_REGISTRY), (
        f"resolution covers {len(_TARGETS)} of {len(TOOL_REGISTRY)} registry entries"
    )
    assert [e.name for e, _t, _w in _TARGETS] == [e.name for e in TOOL_REGISTRY], (
        "resolved entry names do not cover the registry in order — a sample is not a scoreboard"
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
    """Each live tool resolves to a callable entrypoint — not just a name.

    Mutation that REDs this assertion: register a non-callable placeholder where
    a FastMCP tool is expected (the adapter registers whatever ``register_all``
    is handed).
    """
    tools = asyncio.run(mcp_server.mcp._list_tools())
    assert tools, "live MCP surface is empty (fail-closed)"
    not_callable = [t.name for t in tools if not callable(getattr(t, "run", None))]
    assert not not_callable, f"live MCP tools with no callable entrypoint: {not_callable}"
