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
#: ⛔ The METHOD is pinned here as a LITERAL, not read back off the row. Taking it from
#: `entry.rest_spec.method` makes the pin measure whatever the row now says, so a row
#: re-pointed from GET to POST reports green against the POST operation that happens to
#: share the path (measured: `/v1/context` serves `session_context` GET at
#: `hosted_api.py:37761` AND `volunteer_context` POST at `:37939`, so a mutated method made
#: the handler pin read the WRONG handler while all 38 cases stayed green).
REST_SPEC_DOORS = {
    "tortoise_issue_insight": ("GET", "/v1/issue-insight"),
    "tortoise_session_context": ("GET", "/v1/context"),
    "tortoise_search": ("GET", "/v1/search"),
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


def _imported_names(source: str, module: str) -> set[str]:
    """The local names `source` binds with `from <module> import …` (alias-aware).

    AST, not a regex, for the same reason as `_mounted_router_names`: a commented-out import
    line is not a binding, and a text match cannot tell the two apart.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.asname or alias.name for alias in node.names)
    return names


#: The class whose instantiation would make `PROTECTED`'s `rest_spec` reason TRUE again.
_ADAPTER_CLASS = "FastAPIRouterAdapter"
#: The module that both DEFINES it and is the only legitimate place it may be wired.
_ADAPTER_MODULE = "tool_registry"
#: The registry field whose READ in a product module would re-derive routes from a row.
_REST_SPEC_FIELD = "rest_spec"


def _dotted(node: ast.AST) -> str | None:
    """`a.b.c` for a Name/Attribute chain, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _adapter_instantiation_sites(source: str) -> list[int]:
    """Line numbers where `source` INSTANTIATES `FastAPIRouterAdapter` — alias-aware.

    ⛔ AST, not `"FastAPIRouterAdapter(" in source` (the reading this replaced). A literal
    substring is wrong in BOTH directions, and both were measured:

    * **false PASS** — `from tortoise.tool_registry import FastAPIRouterAdapter as RTA` …
      `RTA(app).register_all(TOOL_REGISTRY, handlers)` derives every route from a registry
      row while the literal never appears, so `PROTECTED`'s `rest_spec` reason is TRUE again
      and every arm of that pin stayed green.
    * **false BLOCK** — a docstring or comment that merely NAMES `FastAPIRouterAdapter(`
      reded the pin with "REST routes ARE registry-derived again", a claim about a string.

    Matched: the bare name, a `from … import FastAPIRouterAdapter as X` alias, an
    `import … tool_registry as tr` / `import tortoise.tool_registry` attribute chain,
    `from … import tool_registry as tr`, `getattr(mod, "FastAPIRouterAdapter")`,
    `A = FastAPIRouterAdapter; A(app)`, `mod = tool_registry; mod.FastAPIRouterAdapter(app)`,
    a rebinding THROUGH a module attribute (`RTA = tr.FastAPIRouterAdapter; RTA(app)`), and a
    default-argument binding (`def build(app, cls=FastAPIRouterAdapter): cls(app)`). The class
    DEFINITION in the module that defines it is a binding too, because that module is the one
    place a bare-name wiring is expected.

    Rebindings are resolved **PER POSITION** (in Python's own sense): each call is graded
    against the bindings that hold where it stands, so a reassignment that resolves to neither
    the class nor the module DROPS the name for the calls AFTER it, and leaves the calls BEFORE
    it alone. Both halves were measured:

    * without the drop, the reading inherited the opposite defect from the literal it replaced
      — a FALSE BLOCK, `A = FastAPIRouterAdapter` … `A = object` … `A()` reded the pin with
      "REST routes ARE registry-derived again" for code that derives nothing;
    * with the drop applied to a FINAL, FILE-GLOBAL map, one `A = something_else` ANYWHERE in a
      module erased a real wiring earlier in that same module — a product module that does
      `A = FastAPIRouterAdapter` … `A(app)` and later reuses the bare name `A` for a local
      reported `[]`, so the pin's headline claim stayed green while routes WERE registry-derived.
      That is the false PASS this whole reader exists to prevent, so a per-position read is the
      only shape that holds both directions.

    The per-position pass is SEEDED with a forward-reference pre-pass (two ordered passes over
    the assignments), because Python resolves a name at CALL time: `B = A` … `A =
    FastAPIRouterAdapter` … `B(app)` instantiates the adapter even though `B` is bound before
    `A`. Seeding with the pre-pass result makes those resolve, and the ordered walk then
    re-applies every binding at its own position, so a later shadow still cannot resurrect a
    call it precedes.

    ⛔ IT FAILS CLOSED: a call is a site if it resolves **at its position** OR **under the
    module-final map** (`#13585 review P2`). Position alone is not sound, because this walk has
    position but NO SCOPES: it replays assignments collected by `ast.walk` from every block, so
    a drop written in one scope was applied to a call in another — `tr = _placeholder()` at
    module level, `def wire(app): return tr.FastAPIRouterAdapter(app)`, `tr = tool_registry`
    after the `def` reported `[]` at the pre-union head while `main`'s final-map read reported
    the site. The two arms are each other's blind spot: the final map is scope-blind and loses
    the calls a later shadow erased (F1), the positional walk resolves each call where it
    stands but cannot see a binding settled after it in a DIFFERENT block. Everything comparable
    resolves names through a scope-aware symbol table rather than a flat replay (CPython's
    `symtable.c` collects binding facts per block; ruff's `ruff_python_semantic::model` grades a
    reference against the `Scope` holding the `Binding`; the language reference says assignments
    make a name local to a block, so a binding in one block does not shadow a read in another) —
    a union is the sound minimal shape until this helper carries one. Neither arm alone invents
    or loses a site: the union is a strict superset of both, so no wiring either reading found
    can go unreported. A binding written inside a FUNCTION BODY is also not replayed against the
    module-level names (same reason: it is another block's local, and it cannot erase the module
    binding a sibling function's call resolves against).

    DELIBERATELY not matched, stated rather than hidden: an instantiation reached through a
    dict or a dynamic import the AST cannot resolve (`registry["FastAPIRouterAdapter"](…)`,
    `importlib.import_module(...)`, `__import__`), a `functools.partial` of it, an
    instantiation of a SUBCLASS, and a `from … import *` (lint-blocked in this tree: `F403`
    is not ignored). A regex cannot see those either, and at least the AST cannot be fooled
    by prose.
    """
    tree = ast.parse(source)
    local_names: set[str] = set()
    module_names: set[str] = set()

    def _is_module_name(dotted: str) -> bool:
        return dotted.rsplit(".", 1)[-1] == _ADAPTER_MODULE

    def _resolves_to_class(dotted: str | None, local: set[str] | frozenset[str],
                           module: set[str] | frozenset[str]) -> bool:
        if dotted is None:
            return False
        if dotted in local:
            return True
        head, _, tail = dotted.rpartition(".")
        if tail != _ADAPTER_CLASS:
            return False
        return head in module or _is_module_name(head)

    def _resolves_to_module(dotted: str | None, module: set[str] | frozenset[str]) -> bool:
        return dotted is not None and (dotted in module or _is_module_name(dotted))

    def _apply_import_or_definition(node: ast.AST) -> None:
        if isinstance(node, ast.ClassDef) and node.name == _ADAPTER_CLASS:
            # ⛔ The DEFINING module is where a bare-name wiring is most likely, so the class
            # definition is itself a binding. An import-only reading made
            # `FastAPIRouterAdapter(app)` inside `tool_registry.py` invisible — the one module
            # the docstring above names as the legitimate wiring site.
            local_names.add(node.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name == _ADAPTER_CLASS:
                    local_names.add(bound)
                elif _is_module_name(alias.name):
                    # `from tortoise import tool_registry as tr` — the spelling this very file
                    # uses for `mcp_server` (`from tortoise import mcp_server as ms`), so it is
                    # a realistic regression path rather than an exotic one.
                    module_names.add(bound)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_module_name(alias.name):
                    module_names.add(alias.asname or alias.name.split(".", 1)[0])

    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.ImportFrom, ast.Import)):
            _apply_import_or_definition(node)

    def _apply_assignment(node: ast.Assign | ast.AnnAssign) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        bound_value = _dotted(node.value) if node.value is not None else None
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if _resolves_to_class(bound_value, local_names, module_names):
                local_names.add(target.id)
                module_names.discard(target.id)
            elif _resolves_to_module(bound_value, module_names):
                module_names.add(target.id)
                local_names.discard(target.id)
            else:
                # a reassignment that resolves to neither DROPS the name (see docstring).
                local_names.discard(target.id)
                module_names.discard(target.id)

    # FORWARD-REFERENCE PRE-PASS. Two ordered passes so a chain declared below its use
    # (`A = B` before `B = FastAPIRouterAdapter`) still resolves, while staying deterministic:
    # an unordered fixpoint would oscillate the moment a later shadow undoes an earlier add.
    assignments = sorted(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.Assign, ast.AnnAssign))),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for _ in range(2):
        for node in assignments:
            _apply_assignment(node)

    # Default-argument bindings: `def build(app, cls=FastAPIRouterAdapter): cls(app)` is a
    # statically readable instantiation, not a dynamic one.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        positional = list(node.args.posonlyargs) + list(node.args.args)
        if node.args.defaults:
            for param, default in zip(positional[-len(node.args.defaults):],
                                      node.args.defaults, strict=False):
                if _resolves_to_class(_dotted(default), local_names, module_names):
                    local_names.add(param.arg)
        for param, default in zip(node.args.kwonlyargs, node.args.kw_defaults,
                                  strict=False):
            if default is not None and _resolves_to_class(
                    _dotted(default), local_names, module_names):
                local_names.add(param.arg)

    # ⛔ THE SECOND ARM OF THE UNION: main's module-final map, snapshotted BEFORE the ordered
    # walk mutates the live sets. This is `#13585 review P2` — the positional walk alone reports
    # `[]` for a wiring whose binding is settled in a scope the walk cannot attribute, and that
    # direction is the false PASS this reader exists to prevent. Keeping main's semantics as its
    # own arm means the union can never lose a site the pre-F1 read found.
    final_local_names = frozenset(local_names)
    final_module_names = frozenset(module_names)

    # A binding written inside a FUNCTION BODY belongs to that block, so it cannot erase the
    # module-level name a call elsewhere resolves against (Python's own scoping: an assignment
    # in a block makes the name local to THAT block). Collected by id, because the events below
    # carry the same node objects.
    nested_bindings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, (ast.Assign, ast.AnnAssign)):
                    nested_bindings.add(id(child))

    def _is_instantiation(node: ast.Call) -> bool:
        dotted = _dotted(node.func)
        if dotted is not None and (
            # where it stands (the per-position arm)
            _resolves_to_class(dotted, local_names, module_names)
            # …or where the module settles (the fail-closed arm)
            or _resolves_to_class(dotted, final_local_names, final_module_names)
        ):
            return True
        return (
            dotted == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == _ADAPTER_CLASS
        )

    # ⛔ ONE SOURCE-ORDERED WALK, seeded with the pre-pass, NOT a final-map lookup (#13537
    # review F1). The sets above are the pre-pass RESULT; walking the bindings again at their
    # own positions is what makes a call answer against the bindings that hold WHERE IT
    # STANDS, so a later `A = object` cannot erase an earlier `A(app)` and an earlier one
    # cannot lend its meaning to a later call.
    events: list[tuple[int, int, int, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Call, ast.Import,
                             ast.ImportFrom, ast.ClassDef)):
            # `ast.walk` yields parents before children, and a nested call shares its
            # parent's line — so the column orders them, and the type rank keeps an
            # assignment/import ahead of a call that starts at the same column. The import and
            # class-definition bindings are replayed here too, not just consumed by the seed
            # above: without them the walk would start from the PRE-PASS RESULT (which a later
            # shadow has already emptied) and the first binding in the file would count for
            # nothing.
            rank = 0 if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Import,
                                         ast.ImportFrom, ast.ClassDef)) else 1
            events.append((node.lineno, node.col_offset, rank, node))
    events.sort(key=lambda event: (event[0], event[1], event[2]))
    sites: list[int] = []
    for _lineno, _col, _rank, node in events:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if id(node) not in nested_bindings:
                _apply_assignment(node)
        elif isinstance(node, (ast.ClassDef, ast.ImportFrom, ast.Import)):
            _apply_import_or_definition(node)
        elif _is_instantiation(node):
            sites.append(node.lineno)
    return sorted(sites)


class _LiveIncludeRouter(ast.NodeVisitor):
    """The router arguments of `include_router(…)` calls in LIVE statements only."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_If(self, node: ast.If) -> None:
        # `if False: app.include_router(…)` IS a Call node in the tree but never runs, and it
        # is one of the forms a lane reaches for to disable a mount, so the dead branch is
        # not visited.
        if isinstance(node.test, ast.Constant) and not node.test.value:
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "include_router" \
                and node.args:
            target = node.args[0]
            if isinstance(target, ast.Name):
                self.names.add(target.id)
            elif isinstance(target, ast.Attribute):
                self.names.add(target.attr)
        self.generic_visit(node)


def _mounted_router_names(source: str) -> set[str]:
    """The router names `source` hands to a LIVE `include_router(…)` call.

    ⛔ AST, not `re.findall(r"include_router\\(\\s*([\\w.]+)", …)`: that regex is unanchored
    and comment-blind, so COMMENTING THE MOUNT OUT — the first move a human makes to disable
    a REST surface — leaves the match in place while `selfhost.py` serves `/v1/*` no longer
    (measured on the pre-fix reading: the mutant `# app.include_router(_rest_router)` left
    all three parametrized rows GREEN). Only a live call can produce the edge, so a comment,
    a docstring, and a statically-false branch none of them read as a mount —
    `test_the_mount_reading_is_not_comment_blind` keeps each form as a falsifier.
    """
    visitor = _LiveIncludeRouter()
    visitor.visit(ast.parse(source))
    return visitor.names


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
        # ⛔ Not `getattr(…, None)`: a FastMCP rename of `meta` would read as `None`, and
        # `None` is the value this file treats as "no marker" — the vacuity `_schema`
        # guards one level up.
        assert hasattr(result, "meta"), (
            "the result object has no `meta` — FastMCP changed shape, and the `_meta` wire "
            "fact this file pins would silently report 'no marker' for every annotation")
        out[(arm, "meta")] = result.meta
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


def test_a_wrapped_result_also_stamps_the_meta_marker():
    """⛔ §1.1's FOURTH wire fact: `_meta` — the delta a client sees without the schema.

    `fastmcp/tools/base.py:368` stamps `meta={"fastmcp": {"wrap_result": True}}` whenever the
    annotation selects the wrap, and `None` otherwise (both measured here). An `Any`
    annotation stamps NO `_meta`; every annotation that carries the envelope stamps the
    marker. ⛔ One of the six containers already stamps it BY HAND (`tortoise_graph_overview`'s
    row-section arms, `_row_section_envelope`, `mcp_server.py:7842`), so the four-fact set is
    the contract — for that one tool the marker is not a delta. A client that inspects `_meta`
    — FastMCP's own record that a result was wrapped — therefore sees a FOURTH delta, which
    §1.1 originally listed as three. The count is pinned so a future lane cannot re-derive
    the stale "three".
    """
    marker = {"fastmcp": {"wrap_result": True}}
    plain = _probe(Any)             # the containers' annotation today: NO wrap, NO marker
    wrapped = _probe(list[dict] | dict)

    assert plain[("namespace", "meta")] is None, (
        f"an `Any` tool now stamps `_meta` ({plain[('namespace', 'meta')]!r}) — the "
        "containers already carry the marker, so the fourth wire fact is not a delta")
    assert plain[("claim", "meta")] is None, plain[("claim", "meta")]
    for arm in ("namespace", "claim"):
        assert wrapped[(arm, "meta")] == marker, (
            f"the wrapped annotation's {arm} arm stamps {wrapped[(arm, 'meta')]!r}, not "
            f"{marker!r} — if FastMCP stopped stamping `_meta`, §1.1's fourth wire fact "
            "must be re-measured before it is cited")


def test_the_hand_built_row_envelope_stamps_the_same_marker():
    """§1.1(iv)'s ONE exception, pinned where it can rot: `graph_overview` stamps `_meta` BY HAND.

    The four-wire-fact answer says the `_meta` marker IS a delta for every container S1 would
    annotate, and is NOT one for `tortoise_graph_overview`, whose row-section arms reach the
    same envelope through `_row_section_envelope` (`mcp_server.py:7806`, marker at `:7842`,
    #10085). That exception is the qualifier that keeps §1.1(iv) and §2.1 true, so it has to be
    ASSERTED rather than cited.

    ⛔ It was cited in three places and pinned NOWHERE until this test existed, and the gap is
    silent by construction: `meta={"fastmcp": {"wrap_result": True}}` reads like dead weight to
    a reader who does not know #10085, so the plausible "cleanup" is `meta=None` — which left
    every pin in this file green (measured: 41 passed with the line deleted) while §1.1(iv)'s
    qualifier, §1.1's "not a universal" note and §2.1's exception all became FALSE. The sibling
    pin `test_a_wrapped_result_also_stamps_the_meta_marker` measures FastMCP's CONTRACT on a
    synthetic tool; it cannot see this helper at all, which is why the two are not the same pin.
    """
    marker = {"fastmcp": {"wrap_result": True}}
    rows = [{"ns": "a"}]

    envelope = ms._row_section_envelope(rows)
    assert envelope.structured_content == {"result": rows}, (
        f"the hand-built envelope now ships {envelope.structured_content!r} — the ROW arm "
        "would reach a client as a bare text block again, which is the #10085 defect itself")
    assert envelope.meta == marker, (
        f"`_row_section_envelope` now stamps meta={envelope.meta!r}, not {marker!r}. §1.1(iv) "
        "and §2.1 both say THIS tool already carries the marker, so for it the marker is not a "
        "delta (its dict arms and the other five containers are) — re-measure that qualifier "
        "before deleting the claim, because a client that unwraps keys off this marker")
    # A MAPPING section is returned UNTOUCHED (no output schema ⇒ already structured), so the
    # exception is scoped to the ROW arms and §1.1(iv)'s delta still holds for the dict arms.
    assert ms._row_section_envelope({"a": 1}) == {"a": 1}, (
        "the mapping arm is now wrapped — that nests a caller's payload one level deeper and "
        "changes a shape that already worked")


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


@pytest.mark.parametrize("door,spec", sorted(REST_SPEC_DOORS.items()))
def test_the_served_route_is_hand_written_and_nothing_reads_the_row(door, spec):
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

    method, path = spec
    entry = _live().get(door)
    assert entry is not None, f"{door} is not advertised — re-derive this pin"
    assert entry.rest_spec is not None, (
        f"{door} lost its `rest_spec`, which is the only thing `PROTECTED` cites for it")
    assert (entry.rest_spec.method, entry.rest_spec.path) == (method, path), (
        f"{door}'s `rest_spec` moved to {entry.rest_spec.method} {entry.rest_spec.path}, "
        f"not {method} {path}")

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
    offenders = {
        str(source.relative_to(repo)): lines
        for source in scanned
        if (lines := _adapter_instantiation_sites(
            source.read_text(encoding="utf-8", errors="replace")))
    }
    assert offenders == {}, (
        "FastAPIRouterAdapter is instantiated in product module(s) "
        f"{sorted(offenders.items())} — REST routes ARE registry-derived again, so "
        "`PROTECTED`'s `rest_spec` reason became TRUE and §1.5 / S3 must be re-derived. "
        "(The class is DEFINED in `tool_registry.py` as `class FastAPIRouterAdapter:`, "
        "which this reading does not match — only a CALL does, under any of the names the "
        "module binds it to.)")

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
        bound = _imported_names(selfhost_src, "tortoise.selfhost_api")
        mounted = _mounted_router_names(selfhost_src)
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
    # ⛔ AN ATTRIBUTE READ, NOT A SUBSTRING (#13537 review F2). `"rest_spec" in text` is
    # wrong in the direction that matters for a PRODUCT module: a comment or a docstring that
    # merely NAMES the field reded these three rows with "routes may be registry-derived
    # again", a claim about a string — measured: one comment line above `def tortoise_query(`
    # in `tortoise/mcp_server.py` was enough. That is the same defect the sibling reader above
    # documents for `FastAPIRouterAdapter` (:190-196), so this scan is an AST read too.
    # What it still catches: `e.rest_spec.path`, `getattr(e, "rest_spec")`, `e["rest_spec"]`,
    # and `vars(e)["rest_spec"]` (a constant subscript — the residual list claimed otherwise
    # until #13585 review P3).
    # What it therefore does NOT catch, stated rather than hidden: a field name built at
    # runtime (`getattr(e, "rest" + "_spec")`), which no static read can answer.
    # ⛔ THE WIRING IS EXTRACTED SO IT CAN BE FALSIFIED (#13585 review P3#1). The predicate had
    # its own test, but nothing pinned the CALL SITE — and reverting this comprehension to
    # `"rest_spec" in source.read_text(...)` reded no row, because the scanned population is one
    # file (`tortoise/tool_registry.py`) and the line below excludes exactly that name. A guard
    # whose contract can be reverted silently is the defect this PR exists to remove.
    readers = _rest_spec_readers(scanned, repo)
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


def test_the_mount_reading_is_not_comment_blind():
    """The mount arm's own falsifier: only a LIVE `include_router(…)` is a mount.

    Measured on the pre-fix reading (`re.findall(r"include_router\\(\\s*([\\w.]+)", src)`):
    replacing `app.include_router(_rest_router)` with `# app.include_router(_rest_router)` in
    `tortoise/selfhost.py` — the most natural way a lane disables the REST surface — left
    `mounted == {"_rest_router"}` and all three parametrized rows green, while `/v1/*` was no
    longer served at all. A comment is not a mount; neither is a docstring, nor a
    statically-false branch.
    """
    repo = Path(__file__).resolve().parent.parent
    source = (repo / "tortoise" / "selfhost.py").read_text(
        encoding="utf-8", errors="replace")
    live_line = "app.include_router(_rest_router)"
    assert live_line in source, (
        "`tortoise/selfhost.py` no longer carries the live mount this pin reads — re-derive "
        "the mount arm before trusting it")

    # the POSITIVE arm is not vacuous: the real file's mount is found, under the alias its
    # import binds (`selfhost.py:1050 router as _rest_router`).
    assert _mounted_router_names(source) >= {"_rest_router"}, (
        "the AST reading misses the REAL mount in `tortoise/selfhost.py` — that would fail "
        "a served route, so the reading is broken rather than strict")
    assert _imported_names(source, "tortoise.selfhost_api") >= {"_rest_router"}, (
        "the alias-aware import reading lost `_rest_router` — the intersection this pin "
        "takes would then be empty for a served route")

    commented = source.replace(live_line, f"# mutation: {live_line} removed")
    assert commented != source
    assert _mounted_router_names(commented) == set(), (
        "commenting the mount out still reads as mounted — a lane that disables the REST "
        "surface with a comment would keep this pin green while no route is served")
    assert _mounted_router_names(f'"""{live_line}"""\n') == set(), (
        "a docstring naming the mount reads as a mount")
    assert _mounted_router_names(f"if False:\n    {live_line}\n") == set(), (
        "a statically-false `if` branch reads as a mount")


def test_the_adapter_reading_is_not_spelling_blind():
    """The instantiation arm's own falsifier: an ALIASED instantiation is still one.

    Measured on the pre-fix reading (`"FastAPIRouterAdapter(" in source`): a module that
    binds the class under another name and calls it — `from tortoise.tool_registry import
    FastAPIRouterAdapter as RTA` … `RTA(app).register_all(TOOL_REGISTRY, handlers)` —
    derives every route from a registry row while the literal never appears, so `PROTECTED`'s
    `rest_spec` reason is TRUE again and every arm of that pin stayed green. The same literal
    fired the wrong way too: a docstring NAMING the call reded the pin with a claim about a
    string. Both directions are asserted here, and so is the real tree under the new reading.
    """
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as RTA\n"
        "def wire(app):\n"
        "    RTA(app).register_all(TOOL_REGISTRY, handlers)\n"
    ), "an aliased import → instantiation reads as NO instantiation — the pin would stay " \
       "green while routes ARE registry-derived"
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "app = FastAPIRouterAdapter(app)\n"
    ), "the bare name after a `from … import` is not read"
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry as tr\n"
        "app = tr.FastAPIRouterAdapter(app)\n"
    ), "a module-attribute instantiation under an import alias is not read"
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry\n"
        "app = tortoise.tool_registry.FastAPIRouterAdapter(app)\n"
    ), "a fully-dotted module-attribute instantiation is not read"
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry as tr\n"
        "app = getattr(tr, 'FastAPIRouterAdapter')(app)\n"
    ), "`getattr(mod, 'FastAPIRouterAdapter')(…)` binds the same class without naming it"
    assert _adapter_instantiation_sites(
        "from tortoise import tool_registry as tr\n"
        "app = tr.FastAPIRouterAdapter(app)\n"
    ), "`from pkg import tool_registry as tr` is not read as a module binding — the spelling " \
       "this very test file uses for `mcp_server`"
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "A = FastAPIRouterAdapter\n"
        "A = FastAPIRouterAdapter\n"
        "app = A(app)\n"
    ), "a rebound local alias (`A = FastAPIRouterAdapter; A(app)`) is not resolved"
    assert _adapter_instantiation_sites(
        "from tortoise import tool_registry\n"
        "mod = tool_registry\n"
        "app = mod.FastAPIRouterAdapter(app)\n"
    ), "a rebound module alias (`mod = tool_registry; mod.FastAPIRouterAdapter(app)`) is " \
       "not resolved"
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry as tr\n"
        "RTA = tr.FastAPIRouterAdapter\n"
        "app = RTA(app)\n"
    ), "a rebinding THROUGH a module attribute (`RTA = tr.FastAPIRouterAdapter`) is not resolved"
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "def build(app, cls=FastAPIRouterAdapter):\n"
        "    return cls(app)\n"
    ), "a default-argument binding (`def build(app, cls=FastAPIRouterAdapter)`) is statically " \
       "readable and is not resolved"

    # the OTHER direction: a name rebound or shadowed to something that is NOT the adapter is
    # not an instantiation, or the pin reds with a false claim about the product.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "A = object\n"
        "A()\n"
    ) == [], "a name rebound away from the adapter still reads as an instantiation"
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "del A\n"
        "A = dict\n"
        "A(x=1)\n"
    ) == [], "a name deleted then rebound to `dict` still reads as an instantiation"
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry as tr\n"
        "FastAPIRouterAdapter = _fake_router_class()\n"
        "FastAPIRouterAdapter(tr)\n"
    ) == [], "a function-local shadow over the module import reads as an instantiation"

    # ⛔ A LATER SHADOW MUST NOT ERASE AN EARLIER WIRING (#13537 review F1). The name sets used
    # to be resolved once, file-globally, so one `A = <something else>` ANYWHERE in a module
    # emptied the map for every call in it — a product module that really wires the adapter
    # reported `[]` and the 3 served-route rows stayed green, which is the false PASS this
    # reader exists to prevent.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "app = A(app)\n"
        "A = object\n"
    ), "a rebinding AFTER the call erased it — the per-position read is file-global again"
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry as tr\n"
        "def wire(app):\n"
        "    return tr.FastAPIRouterAdapter(app)\n"
        "def unrelated():\n"
        "    tr = _fake_thing()\n"
        "    return tr\n"
    ), "a shadow in an UNRELATED function erased a real wiring elsewhere in the module"
    # ⛔ THE MIRROR OF THE TWO ABOVE (`#13585 review P2`): a drop written in one block must not
    # erase a call in ANOTHER block. The first two snippets were MEASURED to report `[]` at the
    # pre-union head while `main`'s final-map read reported `[4]`, so they are a REGRESSION
    # against `main` in the one direction this file calls "the false PASS this whole reader
    # exists to prevent"; the union arm is what restores them. The third reported `[]` on BOTH
    # readings (a flat replay has no scopes to attribute the shadow to) and is closed by the
    # block rule: a function-body binding cannot unbind the module-level name.
    assert _adapter_instantiation_sites(
        "from tortoise import tool_registry\n"
        "tr = _fake_thing()\n"
        "def wire(app):\n"
        "    return tr.FastAPIRouterAdapter(app)\n"
        "tr = tool_registry\n"
    ), "a placeholder bound BEFORE the `def` erased a call in its body — the module-final `tr` " \
       "is what that call resolves against at CALL time (the union arm)"
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "_wire = None\n"
        "def build(app):\n"
        "    return _wire(app)\n"
        "_wire = FastAPIRouterAdapter\n"
    ), "a `_wire = None` placeholder before the `def` erased the wiring settled AFTER it — the " \
       "bare-name spelling of the same defect"
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry as tr\n"
        "def unrelated():\n"
        "    tr = _fake_thing()\n"
        "    return tr\n"
        "def wire(app):\n"
        "    return tr.FastAPIRouterAdapter(app)\n"
    ), "a sibling function's local shadow, textually BEFORE the wiring, erased it — a binding " \
       "inside a function body cannot unbind the module-level name another function reads"
    # and the forward-reference half must survive the per-position read: Python resolves a name
    # at CALL time, so a chain declared below its use still instantiates the adapter.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "C = B\n"
        "B = A\n"
        "A = FastAPIRouterAdapter\n"
        "C(app)\n"
    ), "a three-link chain declared below its use stopped resolving — the per-position read " \
       "lost the forward-reference seed"
    # the class DEFINED in the module that defines it, wired by bare name: the one module the
    # helper's own docstring names as the legitimate wiring site.
    assert _adapter_instantiation_sites(
        "class FastAPIRouterAdapter:\n"
        "    def __init__(self, app): self.app = app\n"
        "def build_routes(app, handlers):\n"
        "    adapter = FastAPIRouterAdapter(app)\n"
        "    adapter.register_all(TOOL_REGISTRY, handlers)\n"
    ), "a bare-name instantiation inside the DEFINING module is invisible — the one place a " \
       "wiring is expected"

    # the DEFINITION is not a call — `tool_registry.py` legitimately declares the class.
    assert _adapter_instantiation_sites(
        "class FastAPIRouterAdapter:\n    def register_all(self, *a):\n        pass\n"
    ) == [], "a class DEFINITION reads as an instantiation"

    # prose is not a call, so a docstring/comment edit cannot red this pin.
    assert _adapter_instantiation_sites(
        '"""Never call FastAPIRouterAdapter(app) in product code."""\n'
        "# FastAPIRouterAdapter(app)\n"
    ) == [], (
        "a docstring/comment naming the call reads as an instantiation — a pure prose edit "
        "would red this pin with a claim about the product")

    # and the REAL tree is clean under the new reading, not merely never-exercised.
    repo = Path(__file__).resolve().parent.parent
    offenders = {
        str(path.relative_to(repo)): lines
        for path in sorted((repo / "tortoise").rglob("*.py"))
        if (lines := _adapter_instantiation_sites(
            path.read_text(encoding="utf-8", errors="replace")))
    }
    assert offenders == {}, offenders


def test_the_rest_spec_read_is_an_attribute_not_a_substring(tmp_path):
    """Falsifier for the repo-wide `rest_spec` scan (#13537 review F2).

    The scan this replaced asked `"rest_spec" in source`, which is right in one direction and
    wrong in the other: ONE comment or docstring naming the field in any product module reded
    the three served-route rows with "routes may be registry-derived again" — a claim about a
    string, on a tree that derives nothing. Both directions are asserted here, and a real
    attribute read must still be caught in each of its three static spellings.
    """
    def reads(text: str) -> list[int]:
        probe = tmp_path / "probe.py"
        probe.write_text(text, encoding="utf-8")
        return _rest_spec_attribute_reads(probe)

    assert reads("# the registry rest_spec is unrelated to this container\n"
                 "x = 1\n") == [], (
        "a COMMENT naming the field counts as a read — this is the false BLOCK that reded "
        "three rows on a pure prose edit")
    assert reads('"""`rest_spec` is recorded as the route coupling, measured FALSE."""\n'
                 "x = 1\n") == [], "a DOCSTRING naming the field counts as a read"
    assert reads("for e in TOOL_REGISTRY:\n"
                 "    app.add_api_route(e.rest_spec.path, handler)\n"), (
        "an ATTRIBUTE read (`e.rest_spec`) is not caught — the route-deriving vector this arm "
        "exists to see would pass")
    assert reads("spec = getattr(entry, 'rest_spec')\n"), (
        "`getattr(entry, 'rest_spec')` is not caught")
    assert reads("spec = entry['rest_spec']\n"), "a constant subscript is not caught"
    assert reads("spec = vars(entry)['rest_spec']\n"), (
        "`vars(entry)['rest_spec']` is a constant subscript and IS statically readable, so the "
        "residual list that called it unreachable understated this reader's coverage")


def test_the_rest_spec_scan_is_wired_to_the_attribute_reader(tmp_path):
    """Falsifier for the CALL SITE, not just the predicate (#13585 review P3#1).

    The helper had its own test above, but nothing pinned the wiring: reverting the scan's
    `and _rest_spec_attribute_reads(source)` to `and "rest_spec" in source.read_text(...)`
    reded NO row on the real tree, because the scanned population is exactly one file
    (`tortoise/tool_registry.py`) and the same comprehension excludes that name. So the guard's
    contract could be reverted silently — the class of defect this PR exists to remove.

    Measured against a population that makes the two predicates disagree instead of relying on
    the product tree: a module whose ONLY mention of the field is a comment must not be
    reported, and an attribute reader must be.
    """
    product = tmp_path / "product"
    product.mkdir()
    (product / "comment_only.py").write_text(
        "# the registry rest_spec is unrelated to this container\nx = 1\n", encoding="utf-8")
    (product / "reads.py").write_text(
        "for e in TOOL_REGISTRY:\n    app.add_api_route(e.rest_spec.path, handler)\n",
        encoding="utf-8")
    # the exempt-by-name module is NOT exempt here: the scan's filename exclusion is the
    # caller's, and this falsifies the caller's arm too (a substring predicate would report
    # `comment_only.py` regardless of that exclusion).
    (product / "tool_registry.py").write_text("x = 1  # rest_spec\n", encoding="utf-8")

    assert _rest_spec_readers(sorted(product.glob("*.py")), tmp_path) == [
        "product/reads.py"
    ], (
        "the `rest_spec` scan is not wired to the ATTRIBUTE reader — a comment-only module was "
        "reported, or the real attribute read was missed — so reverting the predicate to a "
        "substring would red no row and the F2 fix could be undone silently"
    )


def _rest_spec_readers(paths: list[Path], root: Path) -> list[str]:
    """Root-relative paths of product modules that READ the `rest_spec` field.

    The scan's call site, extracted so it is falsifiable (#13585 review P3#1) — see
    `test_the_rest_spec_scan_is_wired_to_the_attribute_reader`. `tool_registry.py` is excluded
    by name here because it is read as its own AST below (`_rest_spec_use_outside_the_adapter`),
    which is stricter: legitimate uses must sit INSIDE `FastAPIRouterAdapter`.
    """
    return [
        str(source.relative_to(root))
        for source in paths
        if source.name != "tool_registry.py" and _rest_spec_attribute_reads(source)
    ]


def _rest_spec_attribute_reads(source: Path) -> list[int]:
    """Line numbers where `source` READS the `rest_spec` field.

    Three shapes count as a read, and prose counts as none: an attribute access
    (`e.rest_spec`), a `getattr(e, "rest_spec")` call, and a constant subscript
    (`e["rest_spec"]`). A comment or a docstring is not an `ast.Attribute`, so it cannot red
    the pin — which is the whole point (#13537 review F2; the sibling reader's docstring above
    records the identical false BLOCK for the literal `FastAPIRouterAdapter(`).

    Deliberately NOT matched, stated rather than hidden: a field name assembled at runtime
    (`getattr(e, "rest" + "_spec")`), which no static pass can answer.

    ⛔ `vars(e)["rest_spec"]` IS matched, by the `ast.Subscript` arm below — it is a constant
    subscript, not a dynamic read. This paragraph listed it as NOT matched until
    `#13585 review P3`, and a residual that claims LESS coverage than the code has is the same
    species of drift this file exists to remove.
    """
    tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
    lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            (isinstance(node, ast.Attribute) and node.attr == _REST_SPEC_FIELD)
            or (
                isinstance(node, ast.Call)
                and _dotted(node.func) == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == _REST_SPEC_FIELD
            )
            or (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == _REST_SPEC_FIELD
            )
        ):
            lines.add(node.lineno)
    return sorted(lines)


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


@pytest.mark.parametrize("door,spec", sorted(REST_SPEC_DOORS.items()))
def test_the_serving_handler_reaches_the_sdk_not_the_mcp_door(door, spec):
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
    # ⛔ From the LITERAL table, not off the row: a row re-pointed to another METHOD shares
    # the path with a different operation (`/v1/context` is GET `session_context` and POST
    # `volunteer_context`), so reading the method back would examine the wrong handler.
    method, path = spec
    assert (entry.rest_spec.method, entry.rest_spec.path) == (method, path), (
        f"{door}'s row is {entry.rest_spec.method} {entry.rest_spec.path}, not "
        f"{method} {path} — this pin would otherwise read a different route's handler")

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
    # The LITERAL table, not the row: a row whose method changed would make this pin assert
    # whatever the mutated row now says, and `/v1/context` shows the path is not unique per
    # verb, so both the method and the path have to come from the literal.
    method, path = REST_SPEC_DOORS["tortoise_issue_insight"]
    assert (entries[0].rest_spec.method, entries[0].rest_spec.path) == (method, path), (
        f"tortoise_issue_insight's row is {entries[0].rest_spec.method} "
        f"{entries[0].rest_spec.path}, not {method} {path}")

    full = _RecordingRouter()
    all_handlers = {e.name: (lambda: None) for e in TOOL_REGISTRY if e.rest_spec is not None}
    FastAPIRouterAdapter(full).register_all(TOOL_REGISTRY, all_handlers)
    assert any(p == path and m == (method,) for p, m, _ in full.routes), (
        f"no {method} {path} route was registered by the adapter — {full.routes}")

    # …and without THIS row, with a handler for every surviving route-bearing row, exactly
    # that one route disappears. (A short handler map would register nothing at all and so
    # would not measure the removal.)
    survivors = [e for e in TOOL_REGISTRY if e.name != entries[0].name]
    without_row = _RecordingRouter()
    FastAPIRouterAdapter(without_row).register_all(survivors, all_handlers)
    assert not any(p == path and m == (method,) for p, m, _ in without_row.routes), (
        without_row.routes)
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

def _doors_missing_the_prefix(live) -> list[str]:
    """Live door names that do NOT carry the `tortoise_` prefix.

    Every destination comparison below asks about `tortoise_<destination>`, which is the
    door's real name only under this convention — so the convention is measured here
    rather than asserted in a comment.
    """
    return sorted(name for name in live if not name.startswith("tortoise_"))


def test_every_live_door_carries_the_prefix_the_destination_pins_compare_against():
    """The NAME CONVENTION, pinned because the destination comparisons rest on it.

    `test_the_recorded_destination_is_still_not_an_mcp_door` asks whether
    `tortoise_<destination>` is a live door. That is the door's name only if EVERY live
    door carries the prefix: a door landed under the bare destination name
    (`delete_knowledge`) would be invisible to the comparison, which then stays green
    while `NO_CONTAINER_YET` keeps a door that became retirable — the container's landing
    goes unnoticed, which is the drift the nine-door pin exists to catch. A review round
    measured exactly that mutant green, so the convention is part of the pin.
    """
    missing = _doors_missing_the_prefix(_live())
    assert missing == [], (
        f"live MCP door(s) {missing} do not carry the `tortoise_` prefix — the "
        "`tortoise_<destination>` comparisons in this file no longer name every door, so a "
        "destination landing under its bare name would leave them green. Either pin the "
        f"comparison to the real name(s) or restore the convention")

    # …and the reading detects the shape it exists for, so the assertion above cannot pass
    # by measuring nothing.
    assert _doors_missing_the_prefix({"delete_knowledge": object()}) == ["delete_knowledge"]


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
        # never fire even after the container landed. Measured — and the convention itself
        # is pinned by `test_every_live_door_carries_the_prefix_the_destination_pins_…`,
        # so this prefixed comparison is not a bare assumption.
        assert f"tortoise_{rest}" not in live, (
            f"{door}'s destination `{destination}` is now a live MCP door "
            f"(tortoise_{rest}) — {door} became retirable in the batch that landed it, and "
            "`NO_CONTAINER_YET` should lose the name there")
        return
    assert f"tortoise_{destination}" not in live, (
        f"{destination} IS now a live MCP door (tortoise_{destination}) — {door} became "
        "retirable in the batch that landed it, and `NO_CONTAINER_YET` should lose the name")
