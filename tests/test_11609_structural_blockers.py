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
  · the doors S1 would unblock still declare `list[dict]`, and their containers still have
    NO advertised schema — i.e. the blocker is still LIVE, so removing a name from
    `PROTECTED` would be reversing a decision rather than recording a changed fact;
  · the union form does carry the envelope, so the design in the plan is actually available;
  · the union DOES re-wrap an existing dict arm (the cost that gates S1 on approval);
  · `-> dict` cannot be the answer (FastMCP refuses a list return);
  · the three REST routes are NOT derived from the registry row — nothing in product code
    instantiates the only thing that would derive one (`FastAPIRouterAdapter`), and the
    served routes are hand-written decorators that call the SDK. `PROTECTED`'s `rest_spec`
    reason is therefore STALE, and THAT is what is pinned below;
  · the nine `NO_CONTAINER_YET` destinations are still not MCP doors.

⛔ NOT pinned here: the retired-forward population, the pointer one-hop rule, and the
`PROTECTED`/`NO_CONTAINER_YET` table sizes — `tests/test_11609_door_retirement.py` owns
those, and duplicating them would give a future lane two places to update.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

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

#: The NINE doors S1 unblocks: the eight envelope-only ones plus `tortoise_search`, which
#: `PROTECTED` holds under TWO reasons — the envelope *and* the (stale) `rest_spec` row. S1's
#: sufficiency claim is about all nine, so the loop that measures it iterates this tuple; a
#: loop over `ENVELOPE_DOORS` alone left `search`'s door→container edge unpinned (§4: 9).
S1_DOORS = (*ENVELOPE_DOORS, "tortoise_search")

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

#: The `rest_spec` doors, with the route the row NOMINALLY owns. NOT the route that serves:
#: the served routes are hand-written and never read this row (see the pins below).
REST_SPEC_DOORS = {
    "tortoise_issue_insight": ("/v1/issue-insight"),
    "tortoise_session_context": ("/v1/context"),
    "tortoise_search": ("/v1/search"),
}

#: A hand-written route decorator: `@app.get("/v1/search")` / `@router.post("/context")`.
#: Shared by the text reading and the AST reading so the two cannot disagree on what a
#: "declared route" is — an AST arm that matches nothing is a SKIP, not a pass.
_DECORATOR = re.compile(r'^\s*@(?:app|router)\.(get|post|put|patch|delete)\(\s*"([^"]+)"')

#: The app modules that serve `REST_SPEC_DOORS`. ⛔ The mount prefix is READ from each module
#: (`_declared_prefix`), never hard-coded: `hosted_api` mounts at the full path while
#: `selfhost_api`'s router carries `prefix="/v1"` (`selfhost_api.py:28`) and spells only the
#: suffix. A hard-coded prefix is the way a module-sized hole opens silently — change one app's
#: prefix (or its decorators' spelling) and its arm matches nothing while the other app keeps a
#: shared `assert found` non-empty, so the whole handler-body loop for that module never runs.
def _serving_modules():
    from tortoise import hosted_api, selfhost_api

    return (hosted_api, selfhost_api)


def _declared_prefix(app_module) -> str:
    """The `APIRouter(prefix=…)` the module declares, or `""` for a full-path `app`."""
    match = re.search(
        r'APIRouter\(\s*prefix\s*=\s*["\']([^"\']+)["\']',
        inspect.getsource(app_module))
    return match.group(1) if match else ""


def _declared_route_paths(app_module, method: str) -> set[str]:
    """Every absolute path the module's SOURCE declares for `method`.

    BOTH spellings are admitted — the literal decorator argument and that argument under the
    module's declared prefix — because the two are the same route and an app may switch from
    one convention to the other. Admitting only the convention it uses TODAY would turn a
    legitimate spelling change into an arm that silently measures nothing.
    """
    prefix = _declared_prefix(app_module)
    paths: set[str] = set()
    for line in inspect.getsource(app_module).splitlines():
        match = _DECORATOR.match(line)
        if match and match.group(1).upper() == method.upper():
            paths.add(match.group(2))
            paths.add(prefix + match.group(2))
    return paths


def _declares_route(app_module, method: str, path: str) -> bool:
    """Does the module's SOURCE declare a hand-written `method path` decorator?"""
    return path in _declared_route_paths(app_module, method)


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
    """The tool's advertised `output_schema`, read from the LIVE server.

    ⛔ `get_tool` returns **`None`** for a name the server does not serve (measured) — it
    does not raise. So an unknown name must be a NAMED failure here, not `None`: otherwise
    "the container still has no envelope" and "the container was renamed away" read
    identically, and the pin goes vacuously green on the drift it exists to catch.
    """
    async def _get():
        tool = await ms.mcp.get_tool(name)
        assert tool is not None, (
            f"{name} is not served by the MCP server at all (get_tool returned None) — a "
            "container this table pins cannot be measured, which is drift, not a pass")
        # ⛔ `getattr(..., None)` would re-open the same vacuity one level down: a FastMCP
        # rename of `output_schema` would read as `None`, which this file treats as PASS.
        assert hasattr(tool, "output_schema"), (
            f"{name}'s tool object has no `output_schema` — FastMCP changed shape, and this "
            "pin would otherwise report 'no envelope' for a tool it can no longer read")
        return tool.output_schema

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
    that the doors above became retirable, and this pin is where that is noticed.
    """
    advertised = _schema(name)
    assert advertised is None, (
        f"{name} now advertises an output schema ({advertised!r}) — the container has "
        "gained an envelope, so the `PROTECTED` envelope doors may now be retired "
        "(and their callers' `structuredContent` has moved one level down — see S1)")


def test_every_envelope_door_destination_is_one_of_the_six_containers():
    """S1's SUFFICIENCY: annotating these six unblocks exactly those NINE doors.

    Without this edge, the two populations are pinned independently and nothing notices a
    door re-pointed at a container outside S1's six — the door would keep its `list[dict]`,
    that other container would keep its `None` schema, and S1 would silently stop
    unblocking it while every pin stayed green. `tools/bridge_table.py`'s `DESTINATION` is
    the map that decides it, so it is read rather than restated.

    ⛔ It iterates `S1_DOORS` (nine), NOT `ENVELOPE_DOORS` (eight): the plan's headline
    count and `16 → 7` both rest on `tortoise_search` landing in one of these six
    containers, and a review round found that edge unpinned — re-pointing `search` in
    `DESTINATION` left every pin green while the count went wrong.
    """
    from tools.bridge_table import DESTINATION

    for door in S1_DOORS:
        destination = DESTINATION.get(door)
        # `DESTINATION` records the bare method name (`search_knowledge`); S1's list is door
        # names. Normalise rather than keeping a second bare-name list that can drift.
        container = f"tortoise_{destination}" if destination else None
        assert container in LIST_CONTAINERS, (
            f"{door} is recorded as landing on {destination!r}, which is not one of the "
            f"containers S1 annotates ({sorted(LIST_CONTAINERS)}) — S1 would not unblock "
            "it, and the plan's per-door consequence is wrong")


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
        # `ToolResult` exposes `structured_content` only — there is no `structuredContent`
        # alias on it (measured), so a camelCase fallback here would read as if one existed.
        out[(arm, "structured")] = getattr(result, "structured_content", None)
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
    under the union also ships `{"result": [...]}` — so the nine doors in S1 can retire
    with NO change for the caller the pointer migrates, and the cost lands only on the
    containers' dict arms (pinned above).
    """
    door_shape = _probe(list[dict])
    container_shape = _probe(list[dict] | dict)
    assert door_shape[("namespace", "structured")] == {"result": [{"ns": "a"}]}
    assert container_shape[("namespace", "structured")] == door_shape[("namespace", "structured")]


def test_a_dict_annotation_cannot_be_the_answer():
    """`-> dict` declares NO wrap marker and NO `result` property, so there is nothing to
    declare a type on — and FastMCP's own result validation then REFUSES the list arm.

    Measured: the `ValueError` is raised in `ToolResult.__init__`
    (`fastmcp/tools/base.py:148`), reached from `convert_result`'s wrap selection at
    `:365-368` (no wrap marker, so `structured_content=structured` — a list). It is
    FastMCP's refusal, NOT the Tortoise SDK's: `tortoise/sdk.py` raises nothing of the kind.
    """
    with pytest.raises(ToolError, match="structured_content must be a dict or None"):
        _probe(dict)


# ── the REST route dies with its registry row ────────────────────────────────

class _RecordingRouter:
    """The one method `FastAPIRouterAdapter` calls."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, tuple[str, ...], str]] = []

    def add_api_route(self, path, handler, methods=None, response_model=None, name=None):
        self.routes.append((path, tuple(methods or ()), name))


@pytest.mark.parametrize("door,path", sorted(REST_SPEC_DOORS.items()))
def test_the_served_route_is_hand_written_and_nothing_reads_the_row(door, path):
    """Question (b), MEASURED — and the answer is the opposite of the registry's prose.

    Three separate measurements, because the doc's headline rests on all three:
      (1) nothing in `tortoise/` instantiates the adapter that would derive routes;
      (2) a hand-written decorator serves the route, with the row's own METHOD;
      (3) no product module reads a `rest_spec` to build routes — including the module that
          defines the field, whose only permitted uses are inside `FastAPIRouterAdapter`.
          Without it, no other path derives a route either.

    The registry records that *"retiring the registry entry deletes its REST route with it"*,
    and `PROTECTED` lists three doors on that basis. It is not true on `main`:

      · `FastAPIRouterAdapter` — the only thing that would derive a route from a `rest_spec` —
        is instantiated **nowhere** in product code (only in tests). Measured, below.
      · the served routes are hand-written decorators on FastAPI apps that call the **SDK**
        directly; `hosted_api.py` does not read `TOOL_REGISTRY` for routes at all.

    So this pin asserts the fact that MAKES the reason stale. If a future change wires the
    adapter (the #3875 direction), this test reds — which is correct: the reason would then
    become true again and `PROTECTED` would deserve the coupling it claims.
    """
    from tortoise import hosted_api, selfhost_api

    entry = _live().get(door)
    assert entry is not None, f"{door} is not advertised — re-derive this pin"
    assert entry.rest_spec is not None, (
        f"{door} lost its `rest_spec`, which is the only thing `PROTECTED` cites for it")
    assert entry.rest_spec.path == path, (
        f"{door}'s `rest_spec` moved to {entry.rest_spec.path}, not {path}")

    # (1) the adapter that would derive the route is wired into NO product module.
    # ⛔ Repo-wide, not the two apps: an instantiation in a third module (a startup hook,
    # `hosted_app.py`) would make the recorded reason TRUE again, and scanning only the
    # two files would report "nowhere" for a claim about the whole product.
    # ⛔ RECURSIVE, not a claim about `tortoise/*.py` only: an instantiation in a subpackage
    # (`indexer/`, `connectors/`, `packs/`) would make the recorded reason TRUE again, and a
    # non-recursive glob would report "nowhere" for a claim about the whole product.
    repo = Path(__file__).resolve().parent.parent
    scanned = sorted((repo / "tortoise").rglob("*.py"))
    assert len(scanned) > 250, f"only {len(scanned)} modules found — the scan is not recursive"
    offenders = [
        str(source.relative_to(repo))
        for source in scanned
        if "FastAPIRouterAdapter(" in source.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == [], (
        "FastAPIRouterAdapter is instantiated in product module(s) "
        f"{offenders} — REST routes ARE registry-derived again, so `PROTECTED`'s "
        "`rest_spec` reason became TRUE and §1.5 / S3 must be re-derived. (The class is "
        "DEFINED in `tool_registry.py` as `class FastAPIRouterAdapter:`, which this scan "
        "does not match — only a call does.)")

    # (2) the route is SERVED anyway, by a hand-written decorator. The METHOD and the PATH
    # must both match, on a DECORATOR line. Measured: matching the path alone false-PASSes
    # on a comment (`hosted_api.py:5568` is `# ... @app.post("/v1/context") ...`) and on a
    # DIFFERENT operation sharing the path with another verb (`:37939` is a POST where this
    # row is GET), and on the self-hosted `volunteer_context` POST (`selfhost_api.py:480`).
    # `hosted_api` mounts at the full path; `selfhost_api`'s router carries a `/v1` prefix
    # (`selfhost_api.py:28`) and spells the suffix — both are read from the module, never
    # assumed (see `_declared_route_paths`).
    hosted_declares = _declares_route(hosted_api, entry.rest_spec.method, path)
    selfhost_declares = _declares_route(selfhost_api, entry.rest_spec.method, path)

    # (2b) a DECLARATION is not a SERVING route: `@router.get(…)` in `selfhost_api.py` only
    # reaches a client because `selfhost.py` does `app.include_router(_rest_router)`. Without
    # this arm the pin asserts "a decorator line exists" while the doc and this test's name
    # assert "the route is served" — MEASURED: deleting
    # `selfhost.py:1052 app.include_router(_rest_router)` left all three of these parametrized
    # pins GREEN while `/v1/*` was no longer served at all. (FastAPI's own answer is the
    # runtime route tree, `app.routes` / `iter_route_contexts()`; a source scan is used here
    # because `tortoise/selfhost.py` refuses to import without `TORTOISE_SECRET_PEPPER` — so
    # the include EDGE is pinned instead of the mounted table.)
    if selfhost_declares:
        selfhost_src = (repo / "tortoise" / "selfhost.py").read_text(
            encoding="utf-8", errors="replace")
        # the names bound to `selfhost_api`'s router in the app module (alias-aware)
        bound: set[str] = set()
        for match in re.finditer(r"from\s+tortoise\.selfhost_api\s+import\s+([^\n#]+)",
                                 selfhost_src):
            for part in match.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                bound.add(part.split(" as ")[-1].strip())
        mounted = set(re.findall(r"include_router\(\s*([\w.]+)", selfhost_src))
        assert bound & mounted, (
            f"{path} is declared on `{selfhost_api.__name__}`'s router, but "
            "`tortoise/selfhost.py` never `include_router()`s a router it imports from "
            f"`tortoise.selfhost_api` (imported={sorted(bound) or 'NOTHING'}, "
            f"mounted={sorted(mounted) or 'NOTHING'}) — the decorator is dead code and the "
            "route is NOT served; this pin's premise changed")

    assert hosted_declares or selfhost_declares, (
        f"{path} is not served by a hand-written {entry.rest_spec.method} decorator in "
        f"{hosted_api.__name__} or {selfhost_api.__name__} — if the route is really gone, "
        "this pin's premise changed")

    # (3) and NO product module reads a `rest_spec` to build routes. Without this, a
    # hand-rolled `for e in TOOL_REGISTRY: app.add_api_route(e.rest_spec.path, …)` would
    # leave every assertion above green while making the doc's headline finding false again.
    # ⛔ REPO-WIDE, not the two apps: `tortoise/` carries THREE FastAPI apps
    # (`hosted_api.py:3536`, `selfhost.py:1040`, `__main__.py:17211`), and the vector named
    # here needs only one of them — a two-module scan reported "nothing reads it" for a
    # claim about the product while the third app was unread.
    readers = [
        str(source.relative_to(repo))
        for source in scanned
        if source.name != "tool_registry.py"
        and "rest_spec" in source.read_text(encoding="utf-8", errors="replace")
    ]
    # ⛔ `tool_registry.py` is NOT exempt — it is where the route-deriving code LIVES, so a
    # filename exclusion leaves the one module that could host the loop unwatched (measured:
    # a `for e in TOOL_REGISTRY: app.add_api_route(e.rest_spec.path, …)` appended to that
    # file passed the text scan above untouched). Its legitimate uses are BOTH inside
    # `FastAPIRouterAdapter` — the field's definition and the adapter's own route wiring —
    # so the module is read as an AST and every `.rest_spec` READ and every `add_api_route`
    # call must sit inside that class.
    outside = _rest_spec_use_outside_the_adapter(repo / "tortoise" / "tool_registry.py")
    assert outside == [], (
        f"`tortoise/tool_registry.py` uses `rest_spec` / `add_api_route` OUTSIDE "
        f"`FastAPIRouterAdapter` at line(s) {outside} — that is the registry-derived route "
        "vector §1.5 says does not exist; re-derive the `rest_spec` half of the plan")

    assert readers == [], (
        f"product module(s) {readers} now read `rest_spec` — routes may be "
        "registry-derived again; re-derive §1.5 before trusting the `rest_spec` half of "
        "the plan")


def _rest_spec_use_outside_the_adapter(source: Path) -> list[int]:
    """The line numbers where `tool_registry.py` uses `rest_spec` OUTSIDE its adapter.

    Text-scanning this module cannot answer the question: the field is DEFINED here, every
    registry row declares it as a keyword, and `FastAPIRouterAdapter` reads it by design.
    What §1.5 claims is narrower — that nothing DERIVES a route from a row except that one
    adapter — so this is an AST walk that tolerates the declarations and the adapter's own
    body while flagging every other `.rest_spec` READ and every other `add_api_route` CALL.
    A hand-rolled loop appended to this module is exactly the vector a filename exclusion
    could not see (measured: it passed the text scan the other modules get).
    """
    tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
    inside_adapter: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "FastAPIRouterAdapter":
            inside_adapter.update(id(inner) for inner in ast.walk(node))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if id(node) not in inside_adapter
        and ((isinstance(node, ast.Attribute) and node.attr == "rest_spec")
             or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "add_api_route"))
    ]
    return sorted(offenders)


# ── what the SERVING HANDLER does — the other half of S3's premise ───────────

def _handler_asts(app_module, method: str, path: str) -> list:
    """The AST of every function `app_module` decorates to serve `method path`.

    The SAME predicate the pin above states in text — a DECORATOR node, the row's own
    METHOD, and the decorator's own literal argument under EITHER the module's declared
    prefix or the full path as spelled — so a comment or a look-alike route on another verb
    cannot satisfy it, and a spelling change cannot make it silently match nothing.
    """
    prefix = _declared_prefix(app_module)
    tree = ast.parse(inspect.getsource(app_module))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if dec.func.attr.lower() != method.lower():
                continue
            arg = dec.args[0] if dec.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and (arg.value == path or prefix + arg.value == path):
                found.append(node)
                break
    return found


def _body_source(node) -> str:
    """The handler body as text, with the DOCSTRING dropped.

    The docstring legitimately names the SDK method it mirrors (`TortoiseSDK.issue_insight()`)
    and executes nothing, so keeping it would let prose satisfy the "does this reach the SDK"
    half while the code did not — a docstring mention is not a call site (#3836(c)).
    """
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def _sdk_attributes(node) -> set[str]:
    """Attribute names reached on a name called `sdk` — the SDK-object edges in a body."""
    return {n.attr for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "sdk"}


@pytest.mark.parametrize("door,path", sorted(REST_SPEC_DOORS.items()))
def test_the_serving_handler_reaches_the_sdk_not_the_mcp_door(door, path):
    """S3's premise, MEASURED — retiring the MCP door cannot break the live route.

    The two pins above prove the route EXISTS, that the deriving adapter is unwired, and what
    the registry row says. None of them reads what the handler's own BODY does — and that body
    is the entire basis for retiring `tortoise_issue_insight` / `tortoise_session_context` with
    "no route work": a handler that called the MCP TOOL (or looked its row up in
    `TOOL_REGISTRY`) would break a live public route the moment the tool is retired, while
    every other pin here stayed green. That is the false-PASS shape this file exists to
    prevent, and it was live: the review's mutant — *"make a /v1/issue-insight handler call
    the MCP tool"* — reddened no row.

    Both halves are asserted, so neither is vacuous on its own:
      · the door's MCP name does not appear in the body, and the body names no `TOOL_REGISTRY`;
      · the body DOES reach the SDK object (`sdk.<method>`), which is what makes the route
        independent of the MCP surface.
    """
    entry = _live().get(door)
    assert entry is not None and entry.rest_spec is not None, (
        f"{door} is not advertised with a `rest_spec` — re-derive this pin")
    method = entry.rest_spec.method

    found = []
    for module in _serving_modules():
        # ⛔ `assert found` alone is an AGGREGATE check: a module whose prefix or decorator
        # shape drifted contributes zero nodes while the other module keeps the aggregate
        # non-empty, so the whole `door not in body` / `TOOL_REGISTRY` / `_sdk_attributes`
        # loop for that module never runs and nothing notices. Measured convention: the two
        # apps really do differ (`hosted_api` spells the full path, `selfhost_api` prefixes
        # `/v1`), which is exactly how a silent, module-sized hole would open.
        nodes = _handler_asts(module, method, path)
        if _declares_route(module, method, path):
            assert nodes, (
                f"`{module.__name__}` DECLARES a hand-written {method} {path} decorator but "
                "the AST reading found no handler for it — the reader went blind (decorator "
                "shape, an alias other than `@app`/`@router`, or a multi-line decorator), so "
                "this module's handler body was never examined. That is a skip, not a pass")
        found.extend((module, node) for node in nodes)
    assert found, (
        f"no hand-written {method} decorator serves {path} in "
        f"{', '.join(f'`{m.__name__}`' for m in _serving_modules())} — this pin's "
        "premise changed and S3 must be re-derived")

    for module, node in found:
        where = f"{module.__name__}:{node.name}"
        body = _body_source(node)
        assert door not in body, (
            f"{where} names the MCP door `{door}` — retiring that door would now break the "
            f"live route {path}, so S3 is NOT free and `PROTECTED`'s reason became TRUE")
        assert "TOOL_REGISTRY" not in body, (
            f"{where} reads `TOOL_REGISTRY` — the route would then depend on the registry "
            "row it is supposed to be independent of (§1.5, S3)")
        assert _sdk_attributes(node), (
            f"{where} reaches no `sdk.<method>` at all — either the SDK left this handler, "
            f"or this pin's SDK-edge reading is stale; {path} must be served by an SDK call "
            "for S3's 'no route work' to hold")


def test_the_sdk_edge_reading_is_not_vacuous():
    """The pin above's own falsifier: a body that DOES call the door is detected.

    Kept as a test rather than as a comment because the reading is AST work over a ~50k-line
    module: a silently-empty walk (a FastAPI decorator-shape change, or the docstring being
    mistaken for code) would let every `assert door not in body` pass while measuring nothing.
    """
    mutant = (
        '@router.get("/v1/issue-insight")\n'
        'async def issue_insight(title: str):\n'
        '    """Docstring names TortoiseSDK.issue_insight() legitimately."""\n'
        '    return await mcp.call_tool("tortoise_issue_insight", {"title": title})\n'
    )
    node = next(n for n in ast.walk(ast.parse(mutant))
                if isinstance(n, ast.AsyncFunctionDef))
    body = _body_source(node)
    assert "tortoise_issue_insight" in body, (
        "a handler that calls the MCP door escaped the reading — the pin is vacuous")
    assert _sdk_attributes(node) == set(), (
        "the MCP-door mutant reached something on a name `sdk`, so the two halves do not "
        "discriminate the way the pin assumes")

    # …and the same reading DOES find the SDK edge on the REAL serving handler, which is what
    # keeps the mutant's RED from being an artefact of the measurement.
    from tortoise import hosted_api, selfhost_api

    real = _handler_asts(hosted_api, "GET", "/v1/issue-insight")
    assert real and _sdk_attributes(real[0]), (
        "the real `/v1/issue-insight` handler reaches no `sdk.<method>` — either it moved "
        "onto the MCP door (S3's premise gone) or the reading broke")
    assert "tortoise_issue_insight" not in _body_source(real[0])

    # …and on BOTH serving modules, not the first one that answers: `selfhost_api` serves
    # `/v1/search` under its own `/v1` router prefix, so it is the arm a prefix-only reading
    # loses first. Without this, a reading that found nothing at all in the second module
    # would still be reported as "the reading works" by the assertion above.
    selfhost = _handler_asts(selfhost_api, "GET", "/v1/search")
    assert selfhost and _sdk_attributes(selfhost[0]), (
        "the self-hosted `/v1/search` handler reaches no `sdk.<method>` under its `/v1` "
        "prefix — the per-module reading is blind to the prefixed app, which is the arm "
        "that lets a module-sized hole open silently")


def test_register_all_would_derive_the_route_from_the_row():
    """The adapter's CAPABILITY, kept separate from the product's wiring (above).

    Two distinct facts — "this would happen" and "this does happen" — and collapsing them is
    how a dead adapter reads as the serving path (#3875).
    """
    entries = [e for e in TOOL_REGISTRY if e.name == "tortoise_issue_insight"]
    assert entries, "tortoise_issue_insight left TOOL_REGISTRY — re-derive this pin"
    path = entries[0].rest_spec.path

    full = _RecordingRouter()
    all_handlers = {e.name: (lambda: None) for e in TOOL_REGISTRY if e.rest_spec is not None}
    FastAPIRouterAdapter(full).register_all(TOOL_REGISTRY, all_handlers)
    assert any(p == path for p, _, _ in full.routes), full.routes

    # …and without THIS row, with a handler for every surviving route-bearing row, exactly
    # that one route disappears. (A short handler map would register nothing at all and so
    # would not measure the removal.)
    survivors = [e for e in TOOL_REGISTRY if e.name != entries[0].name]
    without_row = _RecordingRouter()
    FastAPIRouterAdapter(without_row).register_all(survivors, all_handlers)
    assert not any(p == path for p, _, _ in without_row.routes), without_row.routes
    assert len(without_row.routes) == len(full.routes) - 1, (
        "removing one row changed the route count by "
        f"{len(full.routes) - len(without_row.routes)}, not 1")

    # …and a row with no handler registers nothing either.
    no_handler = _RecordingRouter()
    FastAPIRouterAdapter(no_handler).register_all(entries, {})
    assert not no_handler.routes, no_handler.routes


def test_two_distinct_rows_on_one_path_double_register():
    """If the adapter WERE wired, re-homing must retire the leaf, not duplicate the route.

    Measures the real hazard shape — TWO DIFFERENT rows carrying the same `rest_spec` (a leaf
    and the container that absorbed it), not the same row twice. `register_all` does not
    dedupe (measured), and FastAPI serves first-match, so the leaf would still win and the
    "retirement" would silently do nothing.
    """
    import dataclasses

    leaf = next(e for e in TOOL_REGISTRY if e.name == "tortoise_search")
    container = next(e for e in TOOL_REGISTRY if e.name == "tortoise_search_knowledge")
    rehomed = dataclasses.replace(container, rest_spec=leaf.rest_spec)
    assert rehomed.name != leaf.name and rehomed.rest_spec.path == leaf.rest_spec.path

    router = _RecordingRouter()
    FastAPIRouterAdapter(router).register_all(
        [leaf, rehomed], {leaf.name: lambda: None, rehomed.name: lambda: None})
    assert [p for p, _, _ in router.routes].count(leaf.rest_spec.path) == 2, router.routes
    assert next(n for _, _, n in router.routes) == leaf.name, (
        "the leaf no longer wins first-match, so a duplicated route would not silently "
        "shadow the container")


def test_the_search_door_is_held_by_the_envelope_not_the_route():
    """`tortoise_search` was listed under BOTH reasons; only one of them is real.

    Its `rest_spec` is stale (§1.5) — the served `GET /v1/search` is hand-written — so what
    holds it is the `list[dict]` envelope, i.e. S1, not any route work. Losing that
    distinction is what put three unnecessary steps in the plan.
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

    if ":" in destination:   # tenancy:/sdk: — a router/SDK path, never a tool name
        prefix, rest = destination.split(":", 1)
        assert prefix in {"tenancy", "sdk"}, (
            f"{door}'s destination {destination!r} uses an undeclared prefix {prefix!r} — "
            "the only declared non-door prefixes are `tenancy:` and `sdk:`")
        # ⛔ Compare `tortoise_<rest>`, NOT `<rest>`. Every live tool name starts with
        # `tortoise_`, so `rest not in live` is unconditionally TRUE and the assertion could
        # never fire even after the container landed. Measured.
        assert f"tortoise_{rest}" not in live, (
            f"{door}'s destination `{destination}` is now a live MCP door "
            f"(tortoise_{rest}) — {door} became retirable in the batch that landed it, and "
            "`NO_CONTAINER_YET` should lose the name there")
        return
    assert f"tortoise_{destination}" not in live, (
        f"{destination} IS now a live MCP door (tortoise_{destination}) — {door} became "
        "retirable in the batch that landed it, and `NO_CONTAINER_YET` should lose the name")
