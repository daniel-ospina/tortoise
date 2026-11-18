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
    can go unreported — EXCEPT that a name a block on the call's chain BINDS is answered by that
    block alone (unioning it would re-admit the very shadow Python says wins).

    ⛔ EVERY BINDING IS REPLAYED INTO THE BLOCK THAT OWNS IT, AND A CALL IS RESOLVED THROUGH THE
    WHOLE CHAIN (`#13585 review` P3). A FLAT replay cannot say which block a binding belongs to,
    and that was wrong in BOTH available shapes: applying a shadow everywhere let one function's
    local erase a real wiring elsewhere (the F1 defect), and skipping it everywhere meant a
    shadow inside the graded call's OWN block never dropped the name for the calls after it —

        from tortoise.tool_registry import FastAPIRouterAdapter
        def build():
            FastAPIRouterAdapter = object
            return FastAPIRouterAdapter(x)      # `object(x)` — the name is LOCAL to this block

    — which the pre-fix head reported as a site (`[4]`) where `main` reported `[]`: a FALSE
    BLOCK, the pin claiming "routes ARE registry-derived again" about a module that derives
    nothing. The language reference settles it (Execution model §4.2.1, `docs.python.org/3/
    reference/executionmodel.html`; corroborated by PEP 227): *"If a name is bound in a block,
    it is a local variable of that block, unless declared as nonlocal or global."* So a name
    bound in a block resolves inside that block ONLY to that block's binding, and — because a
    nested function CLOSES OVER an enclosing function's local — the same is true for every
    enclosing FUNCTION block: `def outer(): A = object` … `def inner(): A(x)` instantiates
    `object`, not the module's `A`, and the reverse (`outer` binds the MODULE) is a real wiring
    a per-block seed would miss. That chain is why resolution walks `_lookup_keys` rather than a
    single block's sets: grading one block against the module-final map alone reported `[5]` for
    the closure shadow and `[]` for the closure wiring (measured).

    DELIBERATELY not matched, stated rather than hidden: an instantiation reached through a
    dict or a dynamic import the AST cannot resolve (`registry["FastAPIRouterAdapter"](…)`,
    `importlib.import_module(...)`, `__import__`), a `functools.partial` of it, an
    instantiation of a SUBCLASS, and a `from … import *` (lint-blocked in this tree: `F403`
    is not ignored). A regex cannot see those either, and at least the AST cannot be fooled
    by prose.
    """
    tree = ast.parse(source)

    def _is_module_name(dotted: str) -> bool:
        return dotted.rsplit(".", 1)[-1] == _ADAPTER_MODULE

    # ── SCOPE ATTRIBUTION ────────────────────────────────────────────────────────────────
    # Every node is attributed to its BLOCK CHAIN: the innermost
    # `FunctionDef`/`AsyncFunctionDef`/`Lambda`/`ClassDef` first, then its enclosing blocks,
    # then the module (the empty chain). Python binds a name to the block that assigns it —
    # *"a name bound anywhere within a function block is treated as local to that block"*
    # (language reference, Execution model / Naming and binding; PEP 227) — so a call is graded
    # against ITS OWN chain and never against a sibling block's locals.
    #
    # ⛔ THIS IS `#13585 review P3`. The flat replay skipped every function-body assignment to
    # keep a sibling function's local from unbinding the module name — but skipping the replay
    # also meant a shadow in the SAME block never dropped the name, so
    # `def build(): FastAPIRouterAdapter = object; return FastAPIRouterAdapter(x)` reported a
    # site for `object(x)`: a FALSE BLOCK the pin did not previously emit.
    chains: dict[int, tuple[ast.AST, ...]] = {}

    def _chain(node: ast.AST) -> tuple[ast.AST, ...]:
        return chains.get(id(node), ())

    def _walk(node: ast.AST, stack: tuple[ast.AST, ...]) -> None:
        chains[id(node)] = stack
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # Decorators and default values are EVALUATED in the enclosing block, so they are
            # attributed there; the body and the parameters are the new block.
            for decorator in getattr(node, "decorator_list", ()):
                _walk(decorator, stack)
            for default in node.args.defaults:
                _walk(default, stack)
            for default in node.args.kw_defaults:
                if default is not None:
                    _walk(default, stack)
            inner = (*stack, node)
            for child in (node.body if isinstance(node.body, list) else [node.body]):
                _walk(child, inner)
            return
        if isinstance(node, ast.ClassDef):
            for decorator in node.decorator_list:
                _walk(decorator, stack)
            for base in [*node.bases, *(keyword.value for keyword in node.keywords)]:
                _walk(base, stack)
            inner = (*stack, node)
            for child in node.body:
                _walk(child, inner)
            return
        for child in ast.iter_child_nodes(node):
            _walk(child, stack)

    _walk(tree, ())

    MODULE: int | None = None  # the module block's key (no other key can be None)
    CLASS_BINDING = "class"
    MODULE_BINDING = "module"
    SHADOWED = "shadowed"

    def _key(chain: tuple[ast.AST, ...]) -> int | None:
        return id(chain[-1]) if chain else MODULE

    def _lookup_keys(chain: tuple[ast.AST, ...]) -> list[int | None]:
        """The block keys a name is resolved through, innermost first, then the module.

        A CLASS body is its own namespace but is NOT in a nested function's or lambda's lookup
        chain (Python's LEGB does not consult a class namespace from a method), so it is
        skipped unless the call sits directly in that class body.
        """
        keys: list[int | None] = []
        for position, block in enumerate(reversed(chain)):
            if isinstance(block, ast.ClassDef) and position > 0:
                continue
            keys.append(id(block))
        keys.append(MODULE)
        return keys

    #: The names each block binds ANYWHERE (so a same-block shadow shadows the module name for
    #: every read in that block, exactly as Python's compile-time scoping does), and the value
    #: each of those names holds as the walk sees it.
    binds: dict[int | None, set[str]] = {MODULE: set()}
    envs: dict[int | None, dict[str, str]] = {MODULE: {}}

    declared_global: dict[int | None, set[str]] = {}
    declared_nonlocal: dict[int | None, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            bucket = declared_global if isinstance(node, ast.Global) else declared_nonlocal
            bucket.setdefault(_key(_chain(node)), set()).update(node.names)

    def _note_binding(name: str, chain: tuple[ast.AST, ...]) -> None:
        key = _key(chain)
        if name in declared_nonlocal.get(key, ()):
            return  # bound in an enclosing FUNCTION block; not this one's local
        if name in declared_global.get(key, ()):
            key = MODULE
        binds.setdefault(key, set()).add(name)

    def _target_names(target: ast.AST) -> list[str]:
        return [node.id for node in ast.walk(target) if isinstance(node, ast.Name)]

    def _parameter_names(args: ast.arguments) -> list[str]:
        names = [param.arg for param in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                names.append(extra.arg)
        return names

    for node in ast.walk(tree):
        chain = _chain(node)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _target_names(target):
                    _note_binding(name, chain)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for name in _target_names(node.target):
                _note_binding(name, chain)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for name in _target_names(item.optional_vars):
                        _note_binding(name, chain)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                _note_binding(node.name, chain)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for generator in node.generators:
                for name in _target_names(generator.target):
                    _note_binding(name, chain)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                _note_binding(alias.asname or alias.name.split(".", 1)[0], chain)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if not isinstance(node, ast.Lambda):
                _note_binding(node.name, chain)
            inner = (*chain, node)
            for name in _parameter_names(node.args):
                _note_binding(name, inner)
        elif isinstance(node, ast.ClassDef):
            _note_binding(node.name, chain)

    def _resolve(name: str, chain: tuple[ast.AST, ...], env_map: dict) -> str | None:
        """The value `name` holds as seen from `chain`: a binding kind, or None if unbound.

        A name bound in an INNER block stops the search even when its value is not the adapter
        (`SHADOWED`) — that is Python's own shadowing rule, and it is what stops a module-level
        import from lending its meaning to a call whose own block rebinds the name.
        """
        for key in _lookup_keys(chain):
            if name in binds.get(key, ()):
                return env_map.get(key, {}).get(name, SHADOWED)
        return None

    def _kind(dotted: str | None, chain: tuple[ast.AST, ...], env_map: dict) -> str | None:
        """CLASS_BINDING / MODULE_BINDING / None for the dotted name as seen from `chain`."""
        if dotted is None:
            return None
        root, _, rest = dotted.partition(".")
        kind = _resolve(root, chain, env_map)
        if rest:
            return CLASS_BINDING if (kind == MODULE_BINDING and rest == _ADAPTER_CLASS) else None
        return kind

    def _resolves_to_class(dotted: str | None, chain: tuple[ast.AST, ...], env_map: dict) -> bool:
        if dotted is None:
            return False
        head, _, tail = dotted.rpartition(".")
        if tail == _ADAPTER_CLASS and _is_module_name(head):
            # a fully dotted path with no bound root (`tortoise.tool_registry.FastAPIRouterAdapter`)
            return True
        return _kind(dotted, chain, env_map) == CLASS_BINDING

    def _resolves_to_module(dotted: str | None, chain: tuple[ast.AST, ...], env_map: dict) -> bool:
        if dotted is None:
            return False
        if "." in dotted and _is_module_name(dotted):
            return True  # `tortoise.tool_registry` spelled out
        return _kind(dotted, chain, env_map) == MODULE_BINDING

    def _apply_import_or_definition(node: ast.AST, chain: tuple[ast.AST, ...]) -> None:
        env = envs.setdefault(_key(chain), {})
        if isinstance(node, ast.ClassDef):
            # ⛔ The DEFINING module is where a bare-name wiring is most likely, so the class
            # definition is itself a binding. An import-only reading made
            # `FastAPIRouterAdapter(app)` inside `tool_registry.py` invisible — the one module
            # the docstring above names as the legitimate wiring site.
            env[node.name] = CLASS_BINDING if node.name == _ADAPTER_CLASS else SHADOWED
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                if alias.name == _ADAPTER_CLASS:
                    env[bound] = CLASS_BINDING
                elif _is_module_name(alias.name):
                    # `from tortoise import tool_registry as tr` — the spelling this very file
                    # uses for `mcp_server` (`from tortoise import mcp_server as ms`), so it is
                    # a realistic regression path rather than an exotic one.
                    env[bound] = MODULE_BINDING
                else:
                    env.setdefault(bound, SHADOWED)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                env[bound] = MODULE_BINDING if _is_module_name(alias.name) else SHADOWED

    def _apply_assignment(node: ast.Assign | ast.AnnAssign, chain: tuple[ast.AST, ...]) -> None:
        env = envs.setdefault(_key(chain), {})
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        bound_value = _dotted(node.value) if node.value is not None else None
        class_bound = _resolves_to_class(bound_value, chain, envs)
        module_bound = _resolves_to_module(bound_value, chain, envs)
        for target in targets:
            for name in _target_names(target):
                if class_bound:
                    env[name] = CLASS_BINDING
                elif module_bound:
                    env[name] = MODULE_BINDING
                else:
                    # a reassignment that resolves to neither DROPS the name (see docstring).
                    env[name] = SHADOWED

    def _apply_parameters(node: ast.AST, chain: tuple[ast.AST, ...]) -> None:
        """Bind a function's parameters in ITS block.

        Default-argument bindings are statically readable (`def build(app,
        cls=FastAPIRouterAdapter): cls(app)` instantiates the adapter), so a parameter whose
        default resolves to the adapter stands for it. Every parameter still BINDS in the
        function's block — a parameter with any other default shadows the module name for the
        whole body, which is why it is recorded as `SHADOWED` rather than skipped.
        """
        env = envs.setdefault(id(node), {})
        args = node.args
        positional = [*args.posonlyargs, *args.args]
        defaults = list(args.defaults)
        pairs = list(zip(positional[-len(defaults):] if defaults else [], defaults, strict=False))
        pairs += list(zip(args.kwonlyargs, args.kw_defaults, strict=False))
        resolvable: dict[str, str] = {}
        for param, default in pairs:
            if default is None:
                continue
            resolved = _dotted(default)
            if _resolves_to_class(resolved, chain, envs):
                resolvable[param.arg] = CLASS_BINDING
            elif _resolves_to_module(resolved, chain, envs):
                resolvable[param.arg] = MODULE_BINDING
        for name in _parameter_names(args):
            env[name] = resolvable.get(name, SHADOWED)

    # FORWARD-REFERENCE PRE-PASS. Two ordered passes so a chain declared below its use
    # (`A = B` before `B = FastAPIRouterAdapter`) still resolves, while staying deterministic:
    # an unordered fixpoint would oscillate the moment a later shadow undoes an earlier add.
    # Interleaved with the parameter pass, because a default value may name an alias declared
    # elsewhere in its own block.
    assignments = sorted(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.Assign, ast.AnnAssign))),
        key=lambda node: (node.lineno, node.col_offset),
    )
    definitions = sorted(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.ClassDef, ast.ImportFrom, ast.Import))),
        key=lambda node: (node.lineno, node.col_offset),
    )
    parameterisable = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    ]
    for _ in range(2):
        for node in definitions:
            _apply_import_or_definition(node, _chain(node))
        for node in assignments:
            _apply_assignment(node, _chain(node))
        for node in parameterisable:
            _apply_parameters(node, _chain(node))

    # ⛔ THE SECOND ARM OF THE UNION: the module-final map, snapshotted BEFORE the ordered walk
    # mutates the live environments. This is `#13585 review P2` — the positional walk alone
    # reports `[]` for a wiring whose binding is settled in a scope the walk cannot attribute,
    # and that direction is the false PASS this reader exists to prevent. Arm 2 resolves through
    # the SAME scope chain as arm 1, so it restores every site the positional arm loses while
    # still honouring a same-block shadow (which is what `#13585 review P3` measured).
    final_envs: dict[int | None, dict[str, str]] = {
        key: dict(env) for key, env in envs.items()
    }

    def _is_instantiation(node: ast.Call) -> bool:
        chain = _chain(node)
        dotted = _dotted(node.func)
        if dotted is not None and (
            # where it stands (the per-position arm)
            _resolves_to_class(dotted, chain, envs)
            # …or where its blocks settle (the fail-closed arm)
            or _resolves_to_class(dotted, chain, final_envs)
        ):
            return True
        return (
            dotted == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == _ADAPTER_CLASS
        )

    # ⛔ ONE SOURCE-ORDERED WALK, seeded with the pre-pass, NOT a final-map lookup (#13537
    # review F1). The environments above are the pre-pass RESULT; replaying the bindings at
    # their own positions is what makes a call answer against the bindings that hold WHERE IT
    # STANDS, so a later `A = object` cannot erase an earlier `A(app)` and an earlier one
    # cannot lend its meaning to a later call.
    events: list[tuple[int, int, int, ast.AST]] = []
    binders = (ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom, ast.ClassDef,
               ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    for node in ast.walk(tree):
        if isinstance(node, (*binders, ast.Call)):
            # `ast.walk` yields parents before children, and a nested call shares its
            # parent's line — so the column orders them, and the type rank keeps a
            # BINDING ahead of a call that starts at the same column. The import and
            # class-definition bindings are replayed here too, not just consumed by the seed
            # above: without them the walk would start from the PRE-PASS RESULT (which a later
            # shadow has already emptied) and the first binding in the file would count for
            # nothing.
            rank = 1 if isinstance(node, ast.Call) else 0
            events.append((node.lineno, node.col_offset, rank, node))
    events.sort(key=lambda event: (event[0], event[1], event[2]))
    sites: list[int] = []
    for _lineno, _col, _rank, node in events:
        chain = _chain(node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            _apply_assignment(node, chain)
        elif isinstance(node, (ast.ClassDef, ast.ImportFrom, ast.Import)):
            _apply_import_or_definition(node, chain)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            _apply_parameters(node, chain)
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
    # ⛔ …AND THE MIRROR OF THE BLOCK RULE (`#13585 review` P3): a shadow written INSIDE the
    # graded call's OWN block MUST drop the name for the calls after it. The language reference
    # makes a name bound anywhere in a function block local to that whole block, so the call is
    # `object(x)` — not the adapter. The head SKIPPED every function-body binding, so it reported
    # `[4]` here while `main` reported `[]`: a FALSE BLOCK asserting "routes ARE registry-derived
    # again" about a module that derives nothing.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "def build():\n"
        "    FastAPIRouterAdapter = object\n"
        "    return FastAPIRouterAdapter(x)\n"
    ) == [], (
        "a shadow in the call's OWN block reads as the adapter — every function-body binding "
        "was skipped, so the local rule was applied to NO block")
    # …and it is the WHOLE block, not just the statements after the binding: Python makes the
    # name local from the block's first line, so even an earlier call resolves to the local (and
    # raises `UnboundLocalError` at runtime) — never to the module adapter. Asserting this is what
    # keeps the fix from being a positional patch that only moves the hole.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "def build():\n"
        "    return FastAPIRouterAdapter(x)\n"
        "    FastAPIRouterAdapter = object\n"
    ) == [], (
        "a call BEFORE a same-block shadow resolved to the module adapter — the name is local "
        "to the whole block, not only after the binding statement")
    # …without turning the block rule into a blind spot: a legitimate IN-FUNCTION import, and a
    # function-local ALIAS of the module-level import, still resolve — otherwise the fix trades
    # a false BLOCK for a false PASS.
    assert _adapter_instantiation_sites(
        "def wire(app):\n"
        "    from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "    return FastAPIRouterAdapter(app)\n"
    ), "an in-function import of the adapter stopped resolving — the block rule became a blind spot"
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "def build(app):\n"
        "    cls = FastAPIRouterAdapter\n"
        "    return cls(app)\n"
    ), "a function-local ALIAS of the module-level adapter stopped resolving"
    # ⛔ …AND THE SAME RULE THROUGH THE ENCLOSING CHAIN, not just the call's own block. A nested
    # function CLOSES OVER an enclosing function's local, so a shadow written in the ENCLOSING
    # block governs the nested call — grading the inner block against the module-final map alone
    # reported `[5]` here (a FALSE BLOCK on `object(x)`), which is the same species as the row
    # above and the reason resolution walks the whole chain.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "def outer():\n"
        "    FastAPIRouterAdapter = object\n"
        "    def inner():\n"
        "        return FastAPIRouterAdapter(x)\n"
        "    return inner\n"
    ) == [], (
        "a shadow in an ENCLOSING block did not reach the nested call — the enclosing local is "
        "what the closure reads, not the module name")
    # …and the dangerous direction of the same chain: an enclosing block that binds the MODULE
    # makes a nested call a REAL wiring, which a per-block seed resolved to `[]` (a FALSE PASS —
    # the direction this whole reader exists to prevent, so it is the one that matters most).
    assert _adapter_instantiation_sites(
        "from tortoise import tool_registry\n"
        "tr = _fake_thing()\n"
        "def outer():\n"
        "    tr = tool_registry\n"
        "    def inner():\n"
        "        return tr.FastAPIRouterAdapter(x)\n"
        "    return inner\n"
    ), "a wiring bound in an ENCLOSING block was missed by the nested call — the closure reads " \
       "that local, and reporting `[]` here would leave the pin green over a real registry-derived " \
       "route"
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


def test_the_excluded_module_is_graded_by_the_same_predicate(tmp_path):
    """The exemption's replacement reader covers the SAME spellings (#13585 review P3).

    `_rest_spec_readers` exempts `tool_registry.py` by name on the claim that
    `_rest_spec_use_outside_the_adapter` reads it "stricter". While that arm held its own
    `ast.Attribute`-only predicate the claim was FALSE: an out-of-adapter read spelled
    `getattr(e, "rest_spec")` or `vars(e)["rest_spec"]` was caught by NEITHER reader — the module
    is excluded by name from one and invisible to the other — in the one module where §1.5's
    route-deriving loop would live. Both readers now share `_is_rest_spec_read`, so an exemption
    cannot be weaker than the scan it stands in for, and the two cannot drift apart again.
    """
    probe = tmp_path / "tool_registry.py"
    # the legitimate population: the field's DECLARATION (not a read) and the adapter's own body
    legitimate = (
        "class FastAPIRouterAdapter:\n"
        "    def register_all(self):\n"
        "        for entry in TOOL_REGISTRY:\n"
        "            if entry.rest_spec is None:\n"
        "                continue\n"
        "            self.app.add_api_route(entry.rest_spec.path, handler)\n"
        "\n"
        "class Row:\n"
        "    rest_spec: Optional[RestSpec] = None\n"
    )
    probe.write_text(legitimate, encoding="utf-8")
    assert _rest_spec_use_outside_the_adapter(probe) == [], (
        "the adapter's own body or the field's declaration red the pin — legitimate uses must "
        "sit INSIDE `FastAPIRouterAdapter`, and a declaration is not a read")

    for spelling in (
        'routes.append(APIRoute(getattr(entry, "rest_spec").path, handler))\n',
        'routes.append(APIRoute(vars(entry)["rest_spec"].path, handler))\n',
        'routes.append(APIRoute(entry["rest_spec"].path, handler))\n',
        'if entry.rest_spec is not None:\n    routes.append(entry.rest_spec.path)\n',
    ):
        probe.write_text(legitimate + spelling, encoding="utf-8")
        assert _rest_spec_use_outside_the_adapter(probe), (
            f"a `rest_spec` read OUTSIDE the adapter spelled {spelling.strip()!r} is invisible "
            "to the reader that replaces the filename exclusion — the exemption is weaker than "
            "the scan it stands in for")

    probe.write_text(legitimate + "app.add_api_route('/v1/x', handler)\n", encoding="utf-8")
    assert _rest_spec_use_outside_the_adapter(probe), (
        "an out-of-adapter `add_api_route` CALL is no longer flagged")


def _is_rest_spec_read(node: ast.AST) -> bool:
    """The ONE predicate for a static `rest_spec` read: attribute, `getattr`, constant subscript.

    ⛔ Shared by BOTH readers on purpose (`#13585 review` P3 — the EXCLUDED module was graded
    by a weaker spelling reader). `_rest_spec_readers` exempts `tool_registry.py` by name
    because `_rest_spec_use_outside_the_adapter` is supposed to be the STRICTER read for it;
    while each held its own predicate, that claim was false: the replacement matched
    `ast.Attribute(attr="rest_spec")` and `add_api_route` CALL nodes only, so a read spelled
    `getattr(e, "rest_spec")` or `vars(e)["rest_spec"]` outside the adapter was caught by
    NEITHER arm — excluded by name from one, invisible to the other — in the one module the
    pin's own comment calls "where the route-deriving code LIVES". An exclusion is only as good
    as the reader that replaces it, and two predicates are free to drift; one cannot.
    """
    if isinstance(node, ast.Attribute) and node.attr == _REST_SPEC_FIELD:
        return True
    if (
        isinstance(node, ast.Call)
        and _dotted(node.func) == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == _REST_SPEC_FIELD
    ):
        return True
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == _REST_SPEC_FIELD
    )


def _rest_spec_readers(paths: list[Path], root: Path) -> list[str]:
    """Root-relative paths of product modules that READ the `rest_spec` field.

    The scan's call site, extracted so it is falsifiable (#13585 review P3#1) — see
    `test_the_rest_spec_scan_is_wired_to_the_attribute_reader`. `tool_registry.py` is excluded
    by name here because it is read as its own AST below (`_rest_spec_use_outside_the_adapter`),
    which is stricter: legitimate uses must sit INSIDE `FastAPIRouterAdapter`, and it flags every
    static read spelling this reader does (`_is_rest_spec_read`, shared by both — the exclusion
    is only as good as the reader that replaces it).
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
    return sorted(
        node.lineno for node in ast.walk(tree) if _is_rest_spec_read(node))


def _rest_spec_use_outside_the_adapter(source: Path) -> list[int]:
    """The line numbers where `tool_registry.py` uses `rest_spec` OUTSIDE its adapter.

    Text-scanning this module cannot answer the question: the field is DEFINED here, every
    registry row declares it as a keyword, and `FastAPIRouterAdapter` reads it by design.
    What §1.5 claims is narrower — that nothing DERIVES a route from a row except that one
    adapter — so this is an AST walk that tolerates the declarations and the adapter's own
    body while flagging every other `rest_spec` READ and every other `add_api_route` CALL.
    A hand-rolled loop appended to this module is exactly the vector a filename exclusion
    could not see (measured: it passed the text scan the other modules get).

    ⛔ The read arm is `_is_rest_spec_read` — the SAME predicate `_rest_spec_attribute_reads`
    uses — so "read as its own AST below, which is stricter" is true of the ESCAPE this walk
    checks for (`add_api_route`, and placement inside the adapter) and no longer false about
    coverage: an `e.rest_spec`, a `getattr(e, "rest_spec")` and a `vars(e)["rest_spec"]`
    outside the adapter are all flagged here (`#13585 review` P3: while this arm held its own
    Attribute-only predicate, the two spellings were caught by NEITHER reader, because this
    module is excluded by name from the other one).
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
        and (_is_rest_spec_read(node)
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
