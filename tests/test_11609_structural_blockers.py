"""#11609 lane P4 — the three structural blockers, pinned where they are made FALSE.

WHY THIS FILE EXISTS (and why it is not a restatement of the plan)
------------------------------------------------------------------
`docs/architecture/11609-STRUCTURAL-BLOCKERS.md` decides that twelve `PROTECTED` doors are
held by three structural contracts, and that only one of them can be unblocked by a
mechanical change. That document is prose; this file is what stops the prose from rotting,
and it pins the ONE thing a future lane is most likely to get wrong:

  **annotating a list container is not free.** It looks free — the container returns `Any`,
  the door declares `list[dict]`, so "give the container the envelope" reads like a
  type-hint change. It is a WIRE change: FastMCP selects the wrap envelope from the
  annotation (`fastmcp/tools/base.py:363-369`), so any annotation that declares the
  envelope re-wraps every already-dict arm from `<dict>` to `{"result": <dict>}`. A lane
  that annotates, sees the suite green (it pins the doors, not the containers) and ships,
  has silently changed `structuredContent` on six live tools — the #8482 class of defect
  arrived at from the other side.

WHAT IS PINNED, AND WHAT IS DELIBERATELY NOT
--------------------------------------------
  · the eight envelope doors still declare `list[dict]`, and their containers still have
    NO advertised schema — i.e. the blocker is still LIVE, so removing a name from
    `PROTECTED` would be reversing a decision rather than recording a changed fact;
  · the union form does carry the envelope, so the design in the plan is actually available;
  · the union DOES re-wrap an existing dict arm (the cost that gates S1 on approval);
  · `-> dict` cannot be the answer (it refuses a list at the SDK boundary);
  · the three REST routes are derived from the registry row, so retiring the row deletes
    the route — the property the `rest_spec` half of the plan must remove;
  · the nine `NO_CONTAINER_YET` destinations are still not MCP doors.

⛔ NOT pinned here: the retired-forward population, the pointer one-hop rule, and the
`PROTECTED`/`NO_CONTAINER_YET` table sizes — `tests/test_11609_door_retirement.py` owns
those, and duplicating them would give a future lane two places to update.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest
from fastmcp import FastMCP

from tortoise import mcp_server as ms
from tortoise.tool_registry import TOOL_REGISTRY, FastAPIRouterAdapter

#: The eight doors `PROTECTED` holds for the #8482 array-envelope reason.
ENVELOPE_DOORS = (
    "tortoise_search_sessions",
    "tortoise_suggest_entry_points",
    "tortoise_list_batches",
    "tortoise_list_namespaces",
    "tortoise_diary_read",
    "tortoise_expand_relationships",
    "tortoise_check_structure",
    "tortoise_calibrate_summary",
)

#: The containers S1 would annotate, and the reason each is `Any` measured: its arms do
#: not share one shape. `list_knowledge` is the sharp case — its `claim` arm returns
#: ``{results, total, hasMore}`` while `namespace`/`diary` return bare lists.
LIST_CONTAINERS = (
    "tortoise_search_knowledge",
    "tortoise_list_knowledge",
    "tortoise_graph_overview",
    "tortoise_check_confidence",
    "tortoise_explore_connections",
    "tortoise_review_link_candidates",
)

#: The `rest_spec` doors, with the route each row owns. Retiring the row deletes the route.
REST_SPEC_DOORS = {
    "tortoise_issue_insight": ("GET", "/v1/issue-insight"),
    "tortoise_session_context": ("GET", "/v1/context"),
    "tortoise_search": ("GET", "/v1/search"),
}

#: All nine `NO_CONTAINER_YET` destinations. A `tenancy:`/`sdk:` prefix is not an MCP door.
NO_CONTAINER_DESTINATIONS = {
    "tortoise_delete": "delete_knowledge",
    "tortoise_delete_point": "delete_knowledge",
    "tortoise_delete_entity": "delete_knowledge",
    "tortoise_create_edge": "link_entities",
    "tortoise_create_operator": "link_entities",
    "tortoise_events_poll": "poll_events",
    "tortoise_ingest": "sdk:write_knowledge_batch",
    "tortoise_org_create": "tenancy:create_memory_graph",
    "tortoise_list_graphs": "tenancy:list_memory_graphs",
}


def _live() -> dict:
    return {t.name: t for t in TOOL_REGISTRY}


def _schema(name: str) -> dict | None:
    """The tool's advertised `output_schema`, read from the LIVE server."""
    async def _get():
        tool = await ms.mcp.get_tool(name)
        return getattr(tool, "output_schema", None)

    return asyncio.run(_get())


# ── the blockers are still live ──────────────────────────────────────────────

@pytest.mark.parametrize("name", ENVELOPE_DOORS)
def test_the_door_still_declares_the_array_envelope(name):
    """`eval_str=True`: `mcp_server` defers annotations, so the raw value is a STRING."""
    annotation = inspect.signature(getattr(ms, name), eval_str=True).return_annotation
    assert annotation == list[dict], (
        f"{name} declares {annotation!r}. If it no longer does, the #8482 reason in "
        "`PROTECTED` has been made false — move it out of that table in the SAME change, "
        "and only after the container carries the envelope (S1/S2 of the plan)")


@pytest.mark.parametrize("name", LIST_CONTAINERS)
def test_the_container_still_advertises_no_envelope(name):
    """`typing.Any` ⇒ `output_schema is None` ⇒ no wrap ⇒ no #8482 repair on that door.

    A non-`None` schema here means S1 landed. That is not a failure — it is the signal
    that the eight doors above became retirable, and this pin is where that is noticed.
    """
    assert _schema(name) is None, (
        f"{name} now advertises an output schema ({_schema(name)!r}) — the container has "
        "gained an envelope, so the eight `PROTECTED` envelope doors may now be retired "
        "(and their callers' `structuredContent` has moved one level down — see S1)")


# ── the design is available, and it costs what the plan says ─────────────────

def _probe(annotation) -> dict[tuple[str, str], object]:
    """Call a throwaway tool declared `annotation`; return (arm, field) -> value.

    Isolated on its own `FastMCP` on purpose: this measures FastMCP's contract, not the
    product's, so it never touches the registry or a graph.
    """
    def tool(kind: str = "namespace"):
        if kind == "namespace":
            return [{"ns": "a"}]
        return {"results": [{"id": 1}], "total": 1, "hasMore": False}

    tool.__name__ = "envelope_probe"
    tool.__annotations__["return"] = annotation
    server = FastMCP("envelope-probe")
    server.add_tool(tool)

    out: dict[tuple[str, str], object] = {}
    for arm in ("namespace", "claim"):
        result = asyncio.run(server.call_tool("envelope_probe", {"kind": arm}))
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            structured = getattr(result, "structured_content", None)
        out[(arm, "structured")] = structured
    tool_obj = asyncio.run(server.get_tool("envelope_probe"))
    out[("schema", "output")] = tool_obj.output_schema
    return out


def test_the_union_annotation_carries_the_envelope():
    """S1's design: `list[dict] | dict` keeps the wrap envelope (`tortoise_query`'s shape)."""
    schema = _probe(list[dict] | dict)[("schema", "output")]
    assert isinstance(schema, dict), schema
    assert schema.get("x-fastmcp-wrap-result") is True, schema
    assert schema.get("required") == ["result"], schema
    any_of = schema["properties"]["result"]["anyOf"]
    assert {a.get("type") for a in any_of} == {"array", "object"}, any_of


def test_the_union_rewraps_an_already_dict_arm():
    """⛔ THE COST THAT GATES S1 ON APPROVAL — measured, not asserted from prose.

    Today `list_knowledge(kind="claim")` ships the SDK's own `{results, total, hasMore}` as
    `structuredContent` (a `typing.Any` tool has no schema, so FastMCP passes a dict
    through untouched). Under the union it ships `{"result": {results, total, hasMore}}`.
    A client reading `structuredContent["results"]` raises `KeyError` after S1.

    This is the reason S1 is not implemented in a mechanical lane: it is a customer-visible
    `structuredContent` change on six live tools, which needs the owner's approval under
    *MCP/SDK Surface Approval*.
    """
    plain = _probe(Any)             # `Any` is the containers' annotation today: NO schema
    union = _probe(list[dict] | dict)

    assert plain[("schema", "output")] is None, (
        "`typing.Any` no longer means 'no output schema' — the premise of the whole "
        "decision (a container has no envelope today) changed, re-derive S1")
    assert plain[("claim", "structured")] == {
        "results": [{"id": 1}], "total": 1, "hasMore": False}, (
        "the dict arm is no longer passed through flat — the premise of the cost changed, "
        "re-derive S1 before trusting the plan")
    assert union[("claim", "structured")] == {
        "result": {"results": [{"id": 1}], "total": 1, "hasMore": False}}, (
        "the union no longer re-wraps the dict arm — if FastMCP changed this, S1 may have "
        "become free, and that is a reason to re-measure the whole decision")


def test_the_list_arm_does_not_change_for_a_migrating_caller():
    """Why the union is the SMALLEST change: a retiring door already answered wrapped.

    The door is `list[dict]`, so it shipped `{"result": [...]}`. The container's list arm
    under the union also ships `{"result": [...]}` — so the eight doors in S1 can retire
    with NO change for the caller the pointer migrates, and the cost lands only on the
    containers' dict arms (pinned above).
    """
    door_shape = _probe(list[dict])
    container_shape = _probe(list[dict] | dict)
    assert door_shape[("namespace", "structured")] == {"result": [{"ns": "a"}]}
    assert container_shape[("namespace", "structured")] == door_shape[("namespace", "structured")]


def test_a_dict_annotation_cannot_be_the_answer():
    """`-> dict` deletes the wrap marker and then REFUSES a list return at the SDK boundary.

    Measured, so the plan's rejection of `-> dict` is not a preference: FastMCP raises
    `structured_content must be a dict or None` for the list arm, i.e. the container would
    crash on `kind="namespace"` rather than degrade.
    """
    with pytest.raises(Exception, match="structured_content must be a dict or None"):
        _probe(dict)


# ── the REST route dies with its registry row ────────────────────────────────

class _RecordingRouter:
    """The one method `FastAPIRouterAdapter` calls."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, tuple[str, ...], str]] = []

    def add_api_route(self, path, handler, methods=None, response_model=None, name=None):
        self.routes.append((path, tuple(methods or ()), name))


@pytest.mark.parametrize("door,route", sorted(REST_SPEC_DOORS.items()))
def test_the_route_is_derived_from_the_registry_row(door, route):
    """Question (b): a `rest_spec` route exists only while its row does.

    Both halves of `register_all`'s condition are exercised — a row WITHOUT a handler
    registers nothing too, which is the second way a route silently disappears.
    """
    method, path = route
    entries = [e for e in TOOL_REGISTRY if e.name == door]
    assert entries, f"{door} is not in TOOL_REGISTRY, so this row cannot be measured"
    assert entries[0].rest_spec is not None, (
        f"{door} no longer carries a `rest_spec`, so its REST route is already gone — "
        "that is the S3-S5 change, and this pin should be updated with it")
    assert (entries[0].rest_spec.method, entries[0].rest_spec.path) == (method, path), (
        f"{door} moved its route: {entries[0].rest_spec.method} {entries[0].rest_spec.path}")
    assert inspect.signature(getattr(ms, door), eval_str=True) is not None  # door exists

    with_row = _RecordingRouter()
    FastAPIRouterAdapter(with_row).register_all(entries, {door: lambda: None})
    assert any(p == path and m == (method,) for p, m, _ in with_row.routes), with_row.routes

    # …the same handler, with the row removed: the route is gone. This is the property the
    # `rest_spec` half of the plan must remove, and the reason the door is PROTECTED.
    without_row = _RecordingRouter()
    FastAPIRouterAdapter(without_row).register_all([], {door: lambda: None})
    assert not without_row.routes, without_row.routes

    # …and a row with no handler registers nothing either.
    no_handler = _RecordingRouter()
    FastAPIRouterAdapter(no_handler).register_all(entries, {})
    assert not no_handler.routes, no_handler.routes


def test_the_search_door_carries_both_blockers():
    """`tortoise_search` is the one door needing S1 AND S3/S4 together.

    It is `list[dict]` (envelope) *and* owns `GET /v1/search` (route). Landing either
    mechanism alone leaves the other still holding it — which is why the plan sequences it
    as its own step (S5) rather than grouping it with either family.
    """
    entry = _live()["tortoise_search"]
    assert entry.rest_spec is not None and entry.rest_spec.path == "/v1/search"
    assert inspect.signature(ms.tortoise_search, eval_str=True).return_annotation == list[dict]


# ── the nine are still not doors ─────────────────────────────────────────────

@pytest.mark.parametrize("door,destination", sorted(NO_CONTAINER_DESTINATIONS.items()))
def test_the_recorded_destination_is_still_not_an_mcp_door(door, destination):
    """A retirement pointer must name a LIVE tool (`TestEveryPointerResolvesInOneHop`).

    So a destination that exists as an SDK method (`delete_knowledge`, `link_entities`,
    `poll_events`, `list_memory_graphs`) does NOT make the nine retirable — and a
    `tenancy:`/`sdk:` prefix is a router/SDK path, not a door at all.
    """
    from tools.bridge_table import DESTINATION

    assert DESTINATION.get(door) == destination, (
        f"{door} is mapped to {DESTINATION.get(door)!r}, not {destination!r} — re-derive "
        "the reason in `NO_CONTAINER_YET` before trusting this pin")

    live = _live()
    assert door in live, f"{door} is not live, so it is not a `NO_CONTAINER_YET` member"

    if ":" in destination:   # tenancy:/sdk: — never a tool name
        assert not any(n == f"tortoise_{destination}" for n in live)
        return
    assert destination not in live, (
        f"{destination} IS now a live MCP door — {door} became retirable in the batch that "
        "landed it, and `NO_CONTAINER_YET` should lose the name there")
