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
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    TOOL_REGISTRY,
    FastAPIRouterAdapter,
)

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
    block alone (unioning it would re-admit the very shadow Python says wins), and EXCEPT a
    DESTRUCTURING target, where the reading that preceded this PR ignored the target entirely
    and therefore kept a binding the assignment overwrites: `A, b = object, A` … `A(app)` is not
    a site here and WAS one there, because `A` is `object`. That is a false BLOCK the
    PRE-`#13585` reader (`888eda1368^`) emits and this head does not — measured, and pinned so
    the element-wise reading cannot decay back into "ignore the target". It is NOT `origin/main`
    today that emits it: measured on the base `#13628` was written against (`66ddcf305a`, two
    commits after `#13585` merged), `main` answers `[]` for the same source too, because `#13585`
    had already replaced the target-skipping reader; on `origin/main` today the same source still
    answers `[]`, so the mirror row is not where the series' difference shows.

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
    #: Whether a node runs on only SOME executions of its block — inside an `if`/`try`/`match`/
    #: loop/`with` arm. A binding there is a JOIN, not an overwrite (`_bind`).
    conditional: dict[int, bool] = {}
    #: The statements that make their subtree conditional. `Try` marks its body, handlers and
    #: `orelse` alike: over-approximating an arm that cannot be attributed costs a re-read of
    #: the pin, while under-approximating one is the false PASS this reader exists to prevent.
    branches = (ast.If, ast.Try, ast.TryStar, ast.While, ast.For, ast.AsyncFor,
                ast.With, ast.AsyncWith, ast.ExceptHandler, ast.Match, ast.match_case)

    def _chain(node: ast.AST) -> tuple[ast.AST, ...]:
        return chains.get(id(node), ())

    def _walk(node: ast.AST, stack: tuple[ast.AST, ...], branched: bool = False) -> None:
        chains[id(node)] = stack
        conditional[id(node)] = branched
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # Decorators and default values are EVALUATED in the enclosing block, so they are
            # attributed there; the body and the parameters are the new block.
            for decorator in getattr(node, "decorator_list", ()):
                _walk(decorator, stack, branched)
            for default in node.args.defaults:
                _walk(default, stack, branched)
            for default in node.args.kw_defaults:
                if default is not None:
                    _walk(default, stack, branched)
            inner = (*stack, node)
            # a new BLOCK starts unconditional: conditional-ness is relative to the block that
            # contains the binding, never inherited across a block boundary.
            for child in (node.body if isinstance(node.body, list) else [node.body]):
                _walk(child, inner, False)
            return
        if isinstance(node, ast.ClassDef):
            for decorator in node.decorator_list:
                _walk(decorator, stack, branched)
            for base in [*node.bases, *(keyword.value for keyword in node.keywords)]:
                _walk(base, stack, branched)
            inner = (*stack, node)
            for child in node.body:
                _walk(child, inner, False)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # ⛔ A COMPREHENSION IS A BLOCK (`#13585 review c2a6c706` P2). Python compiles it to
            # an implicit function (PEP 709 inlines it from 3.12, but the SCOPE is unchanged), so
            # its generator targets do NOT leak into the enclosing block. Attributing them there
            # made the whole enclosing block look like it rebound the name, so a module that
            # wires the adapter on the line AFTER a comprehension reported `[]` while `main`
            # reported the site — the false PASS this reader exists to prevent.
            # The FIRST iterable is evaluated in the ENCLOSING scope; every later iterable, every
            # target and every `if` runs inside the comprehension's own block.
            _walk(node.generators[0].iter, stack, branched)
            inner = (*stack, node)
            for index, generator in enumerate(node.generators):
                if index:
                    _walk(generator.iter, inner, False)
                _walk(generator.target, inner, False)
                for condition in generator.ifs:
                    _walk(condition, inner, False)
            if isinstance(node, ast.DictComp):
                _walk(node.key, inner, False)
                _walk(node.value, inner, False)
            else:
                _walk(node.elt, inner, False)
            return
        deeper = branched or isinstance(node, branches)
        for child in ast.iter_child_nodes(node):
            _walk(child, stack, deeper)

    _walk(tree, ())

    MODULE: int | None = None  # the module block's key (no other key can be None)
    CLASS_BINDING = "class"
    MODULE_BINDING = "module"
    SHADOWED = "shadowed"

    def _bind(env: dict[str, set[str]], name: str, kind: str, node: ast.AST,
              join: bool = False) -> None:
        """Record that `name` may hold `kind` after `node` runs.

        ⛔ A CONDITIONAL BINDING IS A JOIN, NOT AN OVERWRITE (`#13585 review round 4` F1). The
        env holds the SET of values a name may hold on some path, so an assignment inside an
        `if`/`try`/`except`/`match`/loop arm ADDS a possibility and an UNCONDITIONAL one
        REPLACES the set. That is the ordinary join at a control-flow merge, and it answers the
        branch-conditional class BY CONSTRUCTION rather than one shape at a time.

        What it fixes, measured: the import guard this tree actually uses —

            try:
                from tortoise.tool_registry import FastAPIRouterAdapter
            except ImportError:                      # pragma: no cover
                FastAPIRouterAdapter = None
            def wire(app, handlers):
                return FastAPIRouterAdapter(app).register_all(TOOL_REGISTRY, handlers)

        — reported `[]` at the pre-join head, because the ordered replay applied the `except`
        assignment AFTER the import and dropped the name, while `except ImportError` arms are
        live in this repo (`tortoise/__main__.py`). The two arms are mutually exclusive and NO
        source order chooses between them, so the honest reading of the name after the guard is
        `{class, shadowed}` — and a call of it may well be the adapter, which is the direction
        this pin must never get wrong. A conditional shadow of an adapter binding is therefore
        REPORTED (the branch where the shadow does not run is a real wiring), and the
        UNCONDITIONAL shadow rows assert the other direction so this cannot decay into
        "always report".
        """
        if join or conditional.get(id(node), False):
            env[name] = env.get(name, set()) | {kind}
        else:
            env[name] = {kind}

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

    def _env_key(name: str, chain: tuple[ast.AST, ...]) -> int | None:
        """The block whose ENV a binding's value lands in, honouring `global` / `nonlocal`.

        ⛔ The name and its VALUE must be filed in the SAME block (`#13585 review c2a6c706` P3).
        `_note_binding` already redirects `global` to the module and `nonlocal` to the enclosing
        FUNCTION block that owns `binds`, but the value side wrote into whatever block the
        statement sat in — so the two disagreed and the reading broke in both directions:

            def outer():
                tr = None
                def inner():
                    nonlocal tr
                    tr = tool_registry      # value -> inner's env, name -> outer's binds
                inner()
                return tr.FastAPIRouterAdapter(x)   # outer reads {shadowed} -> `[]` (main `[8]`)

        is a false PASS and a regression against `main`, while the `global` mirror kept the
        module env holding the import and so reported a call of `object(x)`.
        """
        if name in declared_global.get(_key(chain), ()):
            return MODULE
        if name in declared_nonlocal.get(_key(chain), ()):
            for block in reversed(chain[:-1]):
                if isinstance(block, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    return id(block)
            return MODULE
        return _key(chain)

    #: The names each block binds ANYWHERE (so a same-block shadow shadows the module name for
    #: every read in that block, exactly as Python's compile-time scoping does), and the value
    #: each of those names holds as the walk sees it.
    binds: dict[int | None, set[str]] = {MODULE: set()}
    #: name -> the SET of binding kinds it may hold (see `_bind`)
    envs: dict[int | None, dict[str, set[str]]] = {MODULE: {}}

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
        """The names a binding target actually BINDS — and only those.

        ⛔ AN ATTRIBUTE/SUBSCRIPT STORE BINDS NOTHING (`#13585 review 402d71d8` P2). `ast.walk`
        on `A.handlers = {}` yields `Name('A')`, so the store was recorded as a REBINDING of
        `A` and `_apply_assignment` then dropped the alias for the whole block — reporting `[]`
        for a module that really wires the adapter AFTER some attribute bookkeeping. That is the
        false PASS this reader exists to prevent, and a REGRESSION against `main` (whose target
        loop skipped every non-`Name` target). The language reference is explicit: only a
        `Name` target binds (`a.b = …` stores an ATTRIBUTE on the object `a` already names; it
        does not rebind `a` — CPython's execution model §4.2.1).

        So recurse over the BINDING shapes — `Name`, the elements of `Tuple`/`List`, and
        `Starred` — and answer `[]` for `Attribute`/`Subscript`.

        ⛔ THE TWO CALLERS NO LONGER SHARE THIS ONE FUNCTION (`#13585` P2, the value-side fix).
        `_note_binding` (the block-locality pass) asks this function WHICH NAMES a target
        binds; `_apply_assignment` asks `_binding_pairs` WHICH VALUE each name is bound to. The
        shapes therefore differ in exactly one place, deliberately: this function answers the
        names INSIDE a `Starred` (block locality needs them), while `_binding_pairs` skips a
        starred target ELEMENT because its middle slice has no static value. So a binding shape
        added here does NOT reach `_apply_assignment` — extend `_binding_pairs` with it, or the
        value side silently stops pairing.
        """
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for element in target.elts for name in _target_names(element)]
        if isinstance(target, ast.Starred):
            return _target_names(target.value)
        return []

    def _binding_pairs(target: ast.AST, value: ast.AST | None) -> list[tuple[str, ast.AST | None]]:
        """Each name a target BINDS, paired with the VALUE it is bound to where that is static.

        ⛔ THE VALUE SIDE OF THE SAME FIX (`#13585` P2, measured after the store fix). A
        DESTRUCTURING assignment binds element by element — `A, b = A, object` binds `A` to the
        adapter — but `_dotted` answers only a dotted EXPRESSION, so a `Tuple` value was `None`
        and every name in the target was recorded `SHADOWED`. That lost the alias, so
        `A, b = A, object` … `A(app)` reported `[]` at the head while the PRE-`#13585` reader —
        which skipped every non-`Name` target and therefore never dropped the import binding —
        reported the site. (Measured: on the base `#13628` was written against (`66ddcf305a`,
        two commits after `#13585` merged) `main` answers `[]` too, so naming `main` there would
        be FALSE — `#13585` had already removed that reader; on `origin/main` today the same
        source answers `[3]`, because this series' value-side fix landed at `9d1b1eba`.) Same
        false PASS as the store arm, one level down: the pin's headline claim goes green over a
        module that really wires the adapter.

        So pair a destructuring target with a destructuring value whenever the arity matches
        (recursing through nested tuples/lists, and through ONE `Starred`, whose middle slice is
        skipped because a slice has no static value: the elements AFTER it are paired from the
        right, `a, *b, c = (1, 2, 3, 4)` → `c` is `4`). That parenthesised arm is the only
        starred arm that PAIRS. A bare starred target also reaches this function: `ast.parse`
        accepts `*a = [1]` as a `Starred` target even though the COMPILER rejects it (`SyntaxError:
        starred assignment target must be in a list or tuple`) — and this reader parses with
        `ast.parse`, which is exactly why the arm is reachable from source that could never run.
        Either form makes NO CLAIM (the bare form reaches the `return []` arm; the comma form
        `*A, = [object]`, which IS valid Python, passes the arity guard and returns zero pairs),
        so `*A, = [object]` … `A(app)` reports a site it should not. That is the DECLARED
        over-read direction (a false BLOCK, never a false PASS) and it is recorded here so the
        shape is a decision a reader can see rather than an untested accident.

        ⛔ AN UNPAIRABLE DESTRUCTURING MAKES NO CLAIM AT ALL (`return []`), rather than binding
        every name `SHADOWED`. The value is determinate at run time but unreadable here, so
        `SHADOWED` would be a claim the source does not support — and it is exactly the shape
        that lost sites: `A, b = build_pair()` … `A(app)` is a wiring the pre-`#13585` reader
        reported and the `SHADOWED` fallback dropped. Making NO claim leaves any earlier binding
        in force (the name is still recorded as a block-local by `_note_binding`, so it does not
        leak an enclosing block's meaning).

        ⛔ THAT "LEAVES THE EARLIER BINDING IN FORCE" IS BLOCK-LOCAL. The earlier cut stated it
        unconditionally (and attributed it to `main`, which was the PRE-`#13585` reader); the
        review at `4a91cbc19293` measured the difference (recorded in #13628's post-merge note,
        P2). It holds when the earlier binding
        shares the assignment's block. Across a block boundary `_resolve` answers `SHADOWED` for
        a name that is bound-but-unvalued, so the SAME source answers differently by scope —
        measured with this head, the base `#13628` was written against (`66ddcf305a`) and the
        pre-`#13585` reader:

            module    `A, b = build_pair()` … `A(app)`         head [3]  base []  pre [3]
            function  `def wire(app): A, b = build_pair(); …`   head []   base []  pre [4]

        The function row is the FALSE-PASS direction and it is a KNOWN residual, recorded here
        rather than claimed away: a `SHADOWED` there is also defensible (Python really does
        rebind the name to an unreadable value), and settling it is not this PR's change. It is
        filed as `#13657` — `_resolve`'s bound-but-unvalued `SHADOWED` default, the two rows
        above, and the latitude (`1330` destructuring assignments in `tortoise/`, `0` targeting
        an adapter/module alias today). The module row is what keeps the shape this PR closes
        reachable.
        """
        if isinstance(target, ast.Name):
            return [(target.id, value)]
        if isinstance(target, (ast.Tuple, ast.List)):
            value_elts = value.elts if isinstance(value, (ast.Tuple, ast.List)) else None
            stars = [i for i, e in enumerate(target.elts) if isinstance(e, ast.Starred)]
            if (
                value_elts is not None
                # ⛔ TWO STARS REFUSE TO PAIR, AND THIS *IS* REACHABLE FROM SOURCE — not only
                # from a hand-built AST (`#13628` review finding 2, `len(stars) <= 1`, the one
                # clause of that finding `main` left unpinned). MEASURED on Python 3.12:
                # `ast.parse("*b, A, *c = 1, object, 2")` SUCCEEDS — only `compile()` rejects
                # two stars ("multiple starred expressions in assignment") — and this reader
                # walks `ast.parse`, so a source file it really sees can reach this branch.
                # Refusing to pair makes NO claim: the earlier binding stands and the call is
                # still REPORTED. Pairing instead places `A` beside `object` and answers `[]`,
                # which is a LOST site in the false-PASS direction (AGENTS.md A/B). Pinned by
                # the two-star row at the end of the starred block below.
                and len(stars) <= 1
                and not any(isinstance(e, ast.Starred) for e in value_elts)
                and len(value_elts) >= len(target.elts) - len(stars)
            ):
                pairs: list[tuple[str, ast.AST | None]] = []
                for index, element in enumerate(target.elts):
                    if isinstance(element, ast.Starred):
                        continue  # the middle slice has no static value; paired below
                    # before the star: positional from the left; after it: from the right
                    offset = index if not stars or index < stars[0] else (
                        len(value_elts) - (len(target.elts) - index))
                    pairs.extend(_binding_pairs(element, value_elts[offset]))
                return pairs
            return []  # an unreadable sequence value: make no claim (see above)
        return []  # an Attribute/Subscript STORE binds nothing, and a bare `Starred` target's
        # slice has no static value — see `_target_names`

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
                # ⛔ the target belongs to the COMPREHENSION's block, not the enclosing one
                # (`#13585 review c2a6c706` P2) — read it off the target node's own chain, which
                # `_walk` sets to the comprehension.
                for name in _target_names(generator.target):
                    _note_binding(name, _chain(generator.target))
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

    def _resolve(name: str, chain: tuple[ast.AST, ...], env_map: dict) -> set[str] | None:
        """The SET of kinds `name` may hold as seen from `chain`, or None if unbound.

        A name bound in an INNER block stops the search even when none of its possible values
        is the adapter (`{SHADOWED}`) — that is Python's own shadowing rule, and it is what
        stops a module-level import from lending its meaning to a call whose own block rebinds
        the name. Within ONE block the value is a SET because a branch may bind it on only some
        paths (`_bind`), and the block that binds it is the block that owns every read of it.
        """
        for key in _lookup_keys(chain):
            if name in binds.get(key, ()):
                return env_map.get(key, {}).get(name, {SHADOWED})
        return None

    def _kind(dotted: str | None, chain: tuple[ast.AST, ...], env_map: dict) -> set[str] | None:
        """The possible kinds of the dotted name as seen from `chain` (None when unbound)."""
        if dotted is None:
            return None
        root, _, rest = dotted.partition(".")
        kind = _resolve(root, chain, env_map)
        if rest:
            return ({CLASS_BINDING}
                    if (kind is not None and MODULE_BINDING in kind
                        and rest == _ADAPTER_CLASS)
                    else None)
        return kind

    def _resolves_to_class(dotted: str | None, chain: tuple[ast.AST, ...], env_map: dict) -> bool:
        if dotted is None:
            return False
        head, _, tail = dotted.rpartition(".")
        if tail == _ADAPTER_CLASS and _is_module_name(head):
            # a fully dotted path with no bound root (`tortoise.tool_registry.FastAPIRouterAdapter`)
            return True
        kind = _kind(dotted, chain, env_map)
        return kind is not None and CLASS_BINDING in kind

    def _resolves_to_module(dotted: str | None, chain: tuple[ast.AST, ...], env_map: dict) -> bool:
        if dotted is None:
            return False
        if "." in dotted and _is_module_name(dotted):
            return True  # `tortoise.tool_registry` spelled out
        kind = _kind(dotted, chain, env_map)
        return kind is not None and MODULE_BINDING in kind

    def _apply_import_or_definition(node: ast.AST, chain: tuple[ast.AST, ...]) -> None:
        if isinstance(node, ast.ClassDef):
            # ⛔ The DEFINING module is where a bare-name wiring is most likely, so the class
            # definition is itself a binding. An import-only reading made
            # `FastAPIRouterAdapter(app)` inside `tool_registry.py` invisible — the one module
            # the docstring above names as the legitimate wiring site.
            _bind(envs.setdefault(_env_key(node.name, chain), {}), node.name,
                  CLASS_BINDING if node.name == _ADAPTER_CLASS else SHADOWED, node)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                key = _env_key(bound, chain)
                env = envs.setdefault(key, {})
                join = key != _key(chain)
                if alias.name == _ADAPTER_CLASS:
                    _bind(env, bound, CLASS_BINDING, node, join=join)
                elif _is_module_name(alias.name):
                    # `from tortoise import tool_registry as tr` — the spelling this very file
                    # uses for `mcp_server` (`from tortoise import mcp_server as ms`), so it is
                    # a realistic regression path rather than an exotic one.
                    _bind(env, bound, MODULE_BINDING, node, join=join)
                else:
                    _bind(env, bound, SHADOWED, node, join=join)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                key = _env_key(bound, chain)
                _bind(envs.setdefault(key, {}), bound,
                      MODULE_BINDING if _is_module_name(alias.name) else SHADOWED,
                      node, join=key != _key(chain))

    def _apply_assignment(node: ast.Assign | ast.AnnAssign, chain: tuple[ast.AST, ...]) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for name, bound in _binding_pairs(target, node.value):
                bound_value = _dotted(bound) if bound is not None else None
                class_bound = _resolves_to_class(bound_value, chain, envs)
                module_bound = _resolves_to_module(bound_value, chain, envs)
                # ⛔ THE VALUE LANDS IN THE BLOCK THAT OWNS THE NAME (`#13585 review c2a6c706`
                # P3) — `global` goes to the module, `nonlocal` to the enclosing function — and a
                # redirected write is a JOIN, because whether it takes effect at all depends on
                # whether this function runs. Over-reporting there is the declared direction (a
                # re-read of the pin answers it); under-reporting would be the false PASS this
                # reader exists to prevent.
                key = _env_key(name, chain)
                env = envs.setdefault(key, {})
                join = key != _key(chain)
                if class_bound:
                    _bind(env, name, CLASS_BINDING, node, join=join)
                elif module_bound:
                    _bind(env, name, MODULE_BINDING, node, join=join)
                else:
                    # a reassignment that resolves to neither DROPS the name (see docstring)…
                    # unless it sits in a BRANCH, where it only ADDS a possibility (`_bind`).
                    _bind(env, name, SHADOWED, node, join=join)

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
            # unconditional BY DEFINITION — every parameter binds when the block is entered,
            # so this replaces rather than joins.
            env[name] = {resolvable.get(name, SHADOWED)}

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
    final_envs: dict[int | None, dict[str, set[str]]] = {
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
    # ⛔ `tortoise_delete_point` / `tortoise_delete_entity` left this map in #11609 lane P6:
    # the owner's #9660 ruling executes the `delete_knowledge` row by KEEPING
    # `tortoise_delete` and widening it, so their destination became a live door (the kept
    # entry) and both retired FORWARD into it. `tortoise_delete` itself stays — it IS the
    # kept entry — and it is the one member here whose "not a door" claim is about a NAME
    # (`delete_knowledge`) rather than about the capability.
    "tortoise_delete": "delete_knowledge",
    "tortoise_create_edge": "link_entities",
    "tortoise_create_operator": "link_entities",
    "tortoise_events_poll": "poll_events",
    "tortoise_ingest": "sdk:write_knowledge_batch",
    "tortoise_org_create": "tenancy:create_memory_graph",
    "tortoise_list_graphs": "tenancy:list_memory_graphs",
}


def _live() -> dict:
    return {t.name: t for t in TOOL_REGISTRY}


def _any_entry(name: str) -> object | None:
    """The entry for `name` from EITHER registry — live first, then retired.

    ⛔ The route pins below read this, not `_live()`, and that is the point of the
    retirement they document (#11609 lane P6): a door may leave the ADVERTISED surface while
    its hand-written REST route keeps serving, and the customer promise is about the ROUTE.
    Reading only `_live()` would make these pins red the moment the door retired — which
    would either block a correct retirement or, worse, invite deleting the pin that proves
    the route survives. A name that is in NEITHER registry still fails, so this is not a
    relaxation of "the row exists".
    """
    from tortoise.tool_registry import RETIRED_TOOL_REGISTRY

    return _live().get(name) or {t.name: t for t in RETIRED_TOOL_REGISTRY}.get(name)


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
    """S1's SUFFICIENCY: annotating S1's doors unblocks exactly the no-arm doors.

    Without this edge, the two populations are pinned independently and nothing notices a
    door re-pointed at a container outside S1 — the door would keep its `list[dict]`,
    that other container would keep its `None` schema, and S1 would silently stop
    unblocking it while every pin stayed green. `tools/bridge_table.py`'s `DESTINATION` is
    the map that decides it, so it is read rather than restated.

    ⛔ It iterates `S1_DOORS`, NOT `ENVELOPE_DOORS`: the plan's S2 arithmetic (CITED, NEVER
    RESTATED — the count it rests on is asserted at
    `tests/test_11609_door_retirement.py:638`, and the derivation lives in
    `docs/architecture/11609-STRUCTURAL-BLOCKERS.md` §4) rests on `tortoise_search` landing
    in one of S1's containers, and a review round found that edge unpinned — re-pointing
    `search` in `DESTINATION` left every pin green while the count went wrong.

    ⛔ THE FIGURE IS CITED, NEVER RESTATED (#13585 review P3#2 — carried into a second round
    because the first fix kept the digits while forbidding them). `main` moved the tree twice
    under the numbers this docstring used to carry (`#13556` retired
    `create_point`/`diary_write` from the no-arm class, `#13550` retired
    `compute_confidence`/`recall`, so that class is now empty), which is exactly why no count
    belongs here: a count written twice is a count that drifts, and a marker that forbids
    restating a figure must not restate it.
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

    The registry recorded that *"retiring the registry entry deletes its REST route with
    it"*, and `PROTECTED` listed three doors on that basis. It is not true on `main`:

      · `FastAPIRouterAdapter` — the only thing that would derive a route from a `rest_spec` —
        is instantiated **nowhere** in product code (only in tests). Measured, below.
      · the served routes are hand-written decorators on FastAPI apps that call the **SDK**
        directly; `hosted_api.py` does not read `TOOL_REGISTRY` for routes at all.

    So this pin asserts the fact that MAKES the reason stale — and #11609 lane P6 carried it
    out: `tortoise_issue_insight` and `tortoise_session_context` retired FORWARD, and this pin
    still reads their rows (from `RETIRED_TOOL_REGISTRY`) and their served routes, which is
    how the customer promise is kept checkable after the registry row leaves `tools/list`.
    If a future change wires the adapter (the #3875 direction), this test reds — which is
    correct: the reason would then become true again.
    """
    from tortoise import hosted_api, selfhost_api

    method, path = spec
    entry = _any_entry(door)
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
    # documents for `FastAPIRouterAdapter` (:188-189), so this scan is an AST read too.
    # What it still catches: `e.rest_spec.path`, `getattr(e, "rest_spec")`, `e["rest_spec"]`,
    # `vars(e)["rest_spec"]` (a constant subscript — the residual list claimed otherwise
    # until #13585 review P3), and a constant-KEY method call (`d.get("rest_spec")` — added
    # in `#13585 review 402d71d8` P3#2, after the AST arms proved NARROWER than the substring
    # scan they replaced).
    # What it therefore does NOT catch, stated rather than hidden: a field name built at
    # runtime (`getattr(e, "rest" + "_spec")`) or behind a COMPUTED key (`d[field]`,
    # `d.get(field)`), neither of which a static read can answer.
    # ⛔ THE SEAM *AND THE STATEMENT THAT CONSUMES IT* ARE PINNED (#13585 review P2#1). The
    # predicate had its own test and this wiring had none: reverting it to the pre-PR
    # `"rest_spec" in source.read_text(...)` comprehension reded NO row, because the scanned
    # population's only mention of the field sits in the one module that comprehension excludes
    # by PATH. Glob + path exemption + predicate live in ONE function (`_rest_spec_scan`), and
    # the assertion that consumes it lives in `_assert_no_product_rest_spec_reads(root)` — which
    # takes the ROOT, so a falsifier drives that same call over a stub tree where the two
    # predicates DISAGREE. Both halves are falsified by name — the seam by the
    # `…_scan_is_wired_to_the_attribute_reader` falsifier, and the call by
    # `test_the_product_assertion_is_reachable_from_a_stub_root` — rather than declared
    # unfalsifiable.
    _assert_no_product_rest_spec_reads(repo)
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
    # ⛔ BRANCH-CONDITIONAL BINDINGS (`#13585 review round 4` F1). The import guard below is the
    # idiom this tree actually uses (`except ImportError` arms are live in
    # `tortoise/__main__.py`), and the ordered replay applied the `except` assignment AFTER the
    # import — dropped the name, reported `[]` for a module that really derives its routes. The
    # env now holds the SET of values a name may hold, so an arm that no source order can choose
    # between ADDS a possibility instead of overwriting one. Measured on the pre-join head: `[]`;
    # the control WITHOUT the guard: `[3]`.
    assert _adapter_instantiation_sites(
        "try:\n"
        "    from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "except ImportError:                      # pragma: no cover\n"
        "    FastAPIRouterAdapter = None\n"
        "def wire(app, handlers):\n"
        "    return FastAPIRouterAdapter(app).register_all(TOOL_REGISTRY, handlers)\n"
    ), ("an `except ImportError: FastAPIRouterAdapter = None` guard dropped the class for a later "
        "REAL wiring — the two arms are mutually exclusive and no source ORDER chooses between "
        "them, so the name is possibly the adapter after the guard and the call is a site")
    assert _adapter_instantiation_sites(
        "if _HAS_FASTAPI:\n"
        "    from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "else:\n"
        "    FastAPIRouterAdapter = None\n"
        "A = FastAPIRouterAdapter(app)\n"
    ), "an `if`/`else` binding of the adapter reports NO instantiation — the same join the " \
       "`try`/`except` row needs, and the reason the fix is a VALUE SET rather than one more " \
       "shape-specific exception"
    # …and a CONDITIONAL shadow OVER-reports rather than under-reports, deliberately: on the
    # branch where the shadow does not run the call IS the adapter, so reporting it is right and
    # a re-read of the pin answers it. The UNCONDITIONAL shadow rows below are the mirror that
    # keeps this from decaying into "always report".
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "if _DEBUG:\n"
        "    A = object\n"
        "A(app)\n"
    ), ("a CONDITIONAL shadow of an adapter binding dropped the name for the whole module — an "
        "arm that may not run cannot erase the binding that holds on the other arm")
    # the mirror, and the one that keeps the join honest: an UNCONDITIONAL shadow DOES win.
    assert _adapter_instantiation_sites(
        "try:\n"
        "    from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "except ImportError:\n"
        "    FastAPIRouterAdapter = None\n"
        "FastAPIRouterAdapter = dict\n"
        "FastAPIRouterAdapter(x)\n"
    ) == [], ("an UNCONDITIONAL rebinding after a guarded import still reads as an instantiation "
              "— the join must replace on a path that always runs, not accumulate forever")
    # ⛔ AN ATTRIBUTE / SUBSCRIPT STORE DOES NOT REBIND ITS BASE (`#13585 review 402d71d8` P2).
    # `ast.walk` on `A.handlers = {}` yields `Name('A')`, so the store was recorded as a
    # REBINDING of `A` and the alias was dropped for the whole block — `[]` for a module that
    # really wires the adapter after the store, while `main` reported the site. Each row below
    # was MEASURED to flip from `[n]` on `main`'s reader to `[]` on the pre-fix head, so they
    # are a regression pin, not a stylistic preference. The last row is the mirror that keeps
    # the fix from over-reaching: a store is TRANSPARENT, so a real `Name` rebinding before it
    # still governs.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import TOOL_REGISTRY, FastAPIRouterAdapter\n"
        "FastAPIRouterAdapter.handlers = {}\n"
        "def install(app, handlers):\n"
        "    return FastAPIRouterAdapter(app).register_all(TOOL_REGISTRY, handlers)\n"
    ), ("an ATTRIBUTE STORE on the adapter erased a real wiring AFTER it — `ast.walk` read "
        "the store's root name as a rebinding (`main` reported this site)")
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry as tr\n"
        "tr.mount = {}\n"
        "def install(app):\n"
        "    return tr.FastAPIRouterAdapter(app)\n"
    ), "an attribute store on a MODULE alias erased a real wiring — the module spelling of the above"
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "A[0] = 1\n"
        "A(app)\n"
    ), "a SUBSCRIPT store on an alias erased the call after it"
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "def build(app):\n"
        "    A = FastAPIRouterAdapter\n"
        "    A.handlers = {}\n"
        "    return A(app)\n"
    ), ("a SAME-BLOCK attribute store erased the alias bound in that very block "
        "(measured `[]` at the pre-fix head, `[5]` on `main`)")
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "A = object\n"
        "A.handlers = {}\n"
        "A(app)\n"
    ) == [], ("a NAME rebinding before the attribute store was forgotten — a store must be "
              "TRANSPARENT, not a reset to the module binding")
    # ⛔ A COMPREHENSION IS ITS OWN BLOCK (`#13585 review c2a6c706` P2). Python compiles it to an
    # implicit function (PEP 709 inlines it in 3.12+, but the SCOPE is unchanged), so its target
    # does NOT leak. Filing the target in the enclosing block made that whole block look like it
    # rebound the name, so a module wiring the adapter on the line AFTER a comprehension reported
    # `[]` while `main` reported the site — a false PASS, measured in all four comprehension
    # shapes. The first iterable is still evaluated in the ENCLOSING scope.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "def wire(app):\n"
        "    picked = [FastAPIRouterAdapter for FastAPIRouterAdapter in _cands()]\n"
        "    return FastAPIRouterAdapter(app).register_all(TOOL_REGISTRY, handlers)\n"
    ), ("a comprehension target leaked into the ENCLOSING block and erased the wiring after it "
        "— the comprehension is its own scope (`main` reported this site)")
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry as tr\n"
        "def wire(app):\n"
        "    picked = [tr for tr in _candidates()]\n"
        "    return tr.FastAPIRouterAdapter(app)\n"
    ), "the module-alias spelling of the comprehension leak"
    # …without trading it for a blind spot: a call INSIDE the comprehension body still resolves
    # against the enclosing block (only the comprehension's TARGETS are scoped to it).
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "def build(apps):\n"
        "    return [FastAPIRouterAdapter(app) for app in apps]\n"
    ), "a call INSIDE a comprehension body stopped resolving against the enclosing block"
    # ⛔ `global` / `nonlocal` REDIRECT WHERE THE VALUE LANDS (`#13585 review c2a6c706` P3). The
    # name was filed in the block that OWNS it while the value stayed in the block the statement
    # sat in, so the two disagreed and the reading broke: `nonlocal` left the owner reading
    # `{shadowed}` and lost a real wiring that `main` reported.
    assert _adapter_instantiation_sites(
        "from tortoise import tool_registry\n"
        "def outer():\n"
        "    tr = None\n"
        "    def inner():\n"
        "        nonlocal tr\n"
        "        tr = tool_registry\n"
        "    inner()\n"
        "    return tr.FastAPIRouterAdapter(x)\n"
    ), ("a `nonlocal` binding's VALUE was filed in the inner block — the enclosing block kept "
        "reading `{shadowed}` and the real wiring after it was lost (`main` reported it)")
    # …and the `global` arm is the DECLARED OVER-READ, pinned rather than hidden: a module-level
    # name written through `global` may or may not have been rebound, depending on whether that
    # function ran, so the name keeps BOTH possibilities and the call is still reported.
    # Over-reporting is the sanctioned direction (A/B: a false PASS is the one that must never
    # ship, an over-block is a re-read); the row pins the disclosure, not a silence.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter\n"
        "def reset():\n"
        "    global FastAPIRouterAdapter\n"
        "    FastAPIRouterAdapter = object\n"
        "reset()\n"
        "FastAPIRouterAdapter(x)\n"
    ), ("a `global` rebinding is a JOIN over 'did that function run', so the call stays "
        "REPORTED — the declared over-read (AGENTS.md A/B), deliberately not a silent drop")
    # ⛔ THE VALUE SIDE OF THE SAME FIX (`#13585` P2, measured after the store rows above).
    # `_dotted` answers only a dotted EXPRESSION, so a DESTRUCTURING value read as `None` and
    # every name in the target was bound `SHADOWED` — the alias was lost, and a module that
    # really wires the adapter through `A, b = A, object` reported `[]`. The PRE-`#13585` reader
    # reported the site (it skipped every non-`Name` target, so it never dropped the import
    # binding), which makes this a REGRESSION in the false-PASS direction against THAT reader,
    # and the mirror row below is the polar control: there too it reported a site, and that
    # report was wrong — `A` is `object`. Element-wise pairing answers both directions; ignoring
    # the target, which is what the pre-`#13585` reader did, answers neither. MEASURED, so the
    # name is not a guess: on the base `#13628` was written against (`66ddcf305a`, two commits
    # after `#13585` merged) BOTH rows answer `[]` — that tree carries the false PASS, not the
    # false BLOCK. On `origin/main` TODAY the value row answers `[3]` and the mirror `[]`: the
    # value row now agrees with the pre-`#13585` reader (`[3]`), and the mirror does not (it was
    # `[3]` there and is `[]` today) — that removal is the false BLOCK the element-wise reading
    # closes.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "A, b = A, object\n"
        "A(app)\n"
    ), ("a DESTRUCTURING value erased the alias — `A, b = A, object` binds `A` to the adapter "
        "and the head reported `[]` (the pre-`#13585` reader reported this site)")
    assert _adapter_instantiation_sites(
        "import tortoise.tool_registry as tr\n"
        "x, tr = 1, tr\n"
        "def wire(app):\n"
        "    return tr.FastAPIRouterAdapter(app)\n"
    ), "a DESTRUCTURING value erased a MODULE alias — the module spelling of the row above"
    # NESTING: the adapter is in the INNER element (index 1, `(b, c)`), so this row is
    # load-bearing on the RECURSION at `_binding_pairs`'s tuple arm — replacing it with a
    # no-claim for nested elements (`(n, None) for n in _target_names(element)`) binds `b`
    # `SHADOWED` and reds this row. (The adapter in the OUTER element would leave it green, so
    # that shape is covered by the row above, not this one.)
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "(a1, (b, c)) = (object, (A, 2))\n"
        "b(app)\n"
    ), ("a NESTED destructuring value erased the alias paired into it — `b` is bound to `A` "
        "through the inner tuple, so the recursion into nested elements is what answers here")
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "A, b = object, A\n"
        "A(app)\n"
    ) == [], ("`A, b = object, A` reads as a site — element-wise pairing must place the value "
              "where it BELONGS, not report every destructuring (the pre-`#13585` reader reported "
              "a site here, and that report was wrong)")
    # ⛔ AN UNPAIRABLE DESTRUCTURING MAKES NO CLAIM (`_binding_pairs`'s `return []`). `A` IS in
    # the target here — that is the whole point: the value is a CALL, so the pair is
    # undeterminable and the reader must leave the import binding in force rather than claim
    # `SHADOWED`. Restoring the pre-fix claim (`[(name, None) for name in _target_names(target)]`)
    # binds `A` `SHADOWED` and reds this row — the documented false PASS (the pre-`#13585`
    # reader reported the site) would otherwise return with every row green.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "A, b = build_pair()\n"
        "A(app)\n"
    ), ("an UNREADABLE destructuring value made a claim it cannot support: `A, b = "
        "build_pair()` determines nothing statically, so `A`'s import binding must stand")
    # …the orthogonal control: a destructuring that does not NAME `A` cannot touch it either.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "b, c = build_pair()\n"
        "A(app)\n"
    ), "an unrelated destructuring (`b, c = build_pair()`) touched `A`'s binding"
    # ⛔ THE STARRED HALF (`#13585` P2). The two offset formulas only diverge when the star
    # actually CAPTURES, so the value list must be longer than the target: with a 2-element
    # value `A, *rest = A, 2` pairs the same under either formula. These rows red the two most
    # likely rot points — `offset = index` (right-anchored elements mispaired) and an
    # unconditional right-anchor (left-anchored elements mispaired).
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "A, *rest = A, 2, 3\n"
        "A(app)\n"
    ), ("a STAR before the tail misplaced the left-anchored element — `A` is `A` in "
        "`A, *rest = A, 2, 3`, so an unconditional right-anchor loses the site")
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "*rest, A = 1, 2, 3, A\n"
        "A(app)\n"
    ), ("a STAR before the tail misplaced the right-anchored element — `A` is the LAST value "
        "in `*rest, A = 1, 2, 3, A`, so a left-anchored offset loses the site")
    # the arity guard: with one star the value needs at least `n - 1` items. This row is the
    # EXACT-BOUNDARY shape — a longer TARGET with a short value (`A, b, *rest = 2, 3` binds
    # `A = 2`, `rest = []`), so the star captures nothing and only `>=` (not `>`) admits the
    # pairing. Pairing it honestly stops the call being a site; falling through the guard makes
    # no claim, which leaves the import binding in force and would REPORT the site — so the
    # `== []` reds a guard that drops the boundary. MEASURED for `>=` → `>`: this row AND every
    # star-free equal-arity row that asserts `== []` red that same `return []` path, and the
    # FIRST failure observed is the nested row above, NOT this one — so this row is
    # the boundary pin, not the only discriminator.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "A, b, *rest = 2, 3\n"
        "A(app)\n"
    ) == [], ("an EMPTY star skipped the arity check — `A` is `2` in `A, b, *rest = 2, 3`, "
              "so the call is not a site (falling through the guard would report one)")
    # …and a STARRED VALUE is unreadable here (`(*x, 1)` has no static element at the star), so
    # the value-side guard must refuse to pair rather than pair `A` with the `Starred` node.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "x = (object,)\n"
        "A, *rest = (*x, 1)\n"
        "A(app)\n"
    ), ("a STARRED VALUE was paired element-wise — `(*x, 1)` has no static element at the "
        "star, so no claim may be made and `A`'s import binding must stand")
    # ⛔ TWO STARRED TARGETS (`#13628` review finding 2, the `len(stars) <= 1` clause). The shape
    # is COMPILER-invalid but PARSE-reachable, so it is a real source file to this reader rather
    # than a hand-built AST: measured, `ast.parse("*b, A, *c = 1, object, 2")` succeeds and only
    # `compile()` raises "multiple starred expressions in assignment". With the guard, pairing is
    # refused and `A`'s import binding stands, so the call is REPORTED. Drop the guard (`<= 1` →
    # `<= 2`) and the offset arithmetic pairs `A` with `object`: it binds `SHADOWED` and the row
    # answers `[]` — the false-PASS direction, silently.
    assert _adapter_instantiation_sites(
        "from tortoise.tool_registry import FastAPIRouterAdapter as A\n"
        "*b, A, *c = 1, object, 2\n"
        "A(app)\n"
    ), ("TWO stars were paired element-wise — `*b, A, *c = 1, object, 2` is compiler-invalid, "
        "so no claim may be made and `A`'s import binding must stand (pairing loses the site)")
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
    attribute read must still be caught in each of its static spellings.
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
    assert reads("row.rest_spec = spec\n") == [], (
        "a STORE (`row.rest_spec = spec`) counts as a read — it is not one, and it derives no "
        "route; the predicate gates on `ast.Load` since #13585 review P3#3")
    assert reads("entry['rest_spec'] = spec\n") == [], (
        "a subscript STORE counts as a read — only a `Load` is a read")
    # ⛔ A CONSTANT-KEY METHOD CALL (`#13585 review 402d71d8` P3#2). The substring scan this
    # replaced caught `vars(entry).get("rest_spec")`; the three AST arms alone did not, so the
    # reader was NARROWER than the scan it stands in for — in the false-PASS direction, and
    # incoherently, since the same read spelled as a SUBSCRIPT was caught. All three rows below
    # were measured `[]` on the pre-fix head.
    assert reads("spec = vars(entry).get('rest_spec')\n"), (
        "`vars(entry).get('rest_spec')` — a constant-key read — is not caught, though the "
        "substring scan this replaced caught it, and the subscript spelling is caught")
    assert reads("spec = entry.__dict__.get('rest_spec')\n"), (
        "`entry.__dict__.get('rest_spec')` is a constant-key read and is not caught")
    assert reads("spec = row.pop('rest_spec')\n"), "`row.pop('rest_spec')` is not caught"
    assert reads("spec = d.get(field)\n") == [], (
        "a COMPUTED key (`d.get(field)`) counts as a read — no static pass can answer it, so it "
        "belongs in the residual list rather than in an over-blocking arm")


def test_the_rest_spec_scan_is_wired_to_the_attribute_reader(tmp_path):
    """Falsifier for the SEAM the product scan runs through (#13585 review P2#1).

    The predicate had its own test above, but the wiring had none: reverting the call to the
    pre-PR inline `"rest_spec" in source.read_text(...)` comprehension reded NO row on the real
    tree, because the scanned population's only mention of the field sits in the one module that
    comprehension excludes by name. So the guard's contract could be reverted silently — the
    class of defect this PR exists to remove.

    Glob + name exclusion + attribute predicate now live in ONE function (`_rest_spec_scan`),
    and this drives THAT function over a stub root, so a revert of any of the three reds a row
    instead of leaving the file green. `tortoise/sub/` is deliberate: a non-recursive walk would
    report "nothing" for a claim about the whole product, and that revert must red here too.
    """
    product = tmp_path / "tortoise"
    (product / "sub").mkdir(parents=True)
    (product / "comment_only.py").write_text(
        "# the registry rest_spec is unrelated to this container\nx = 1\n", encoding="utf-8")
    (product / "sub" / "reads.py").write_text(
        "for e in TOOL_REGISTRY:\n    app.add_api_route(e.rest_spec.path, handler)\n",
        encoding="utf-8")
    # The DEFINING module carries a REAL read (the adapter's own — legitimate, and the reason
    # it is exempt at all), so dropping the exemption reports it and REDS this row: the exclusion
    # is the scan's own, and this row pins it instead of crediting the real pin.
    (product / "tool_registry.py").write_text(
        "class FastAPIRouterAdapter:\n"
        "    def register(self, entry):\n"
        "        return entry.rest_spec.path\n", encoding="utf-8")
    # ⛔ THE EXEMPTION IS BOUND TO THE ONE MODULE, NOT TO A BASENAME (`#13585 review round 4`
    # F3). A basename test exempted EVERY file of that name under `tortoise/`, while the stricter
    # reader that stands in for the exemption is called on ONE exact path — so a second
    # `tool_registry.py` in a subpackage was graded by NEITHER arm: excluded by name from the
    # scan, never opened by the adapter-scoped read. That no-man's-land is exactly where a
    # hand-rolled route loop would live, so the exemption names the defining module's PATH and
    # anything else of that name is scanned like any other file. Measured at the pre-fix head:
    # this read was reported by neither reader (`[]` from the scan); with the path-bound
    # exemption it is reported by the scan, and reverting to the basename test drops it again.
    (product / "sub" / "tool_registry.py").write_text(
        "for e in TOOL_REGISTRY:\n    app.add_api_route(e.rest_spec.path, handler)\n",
        encoding="utf-8")

    assert _rest_spec_scan(tmp_path) == ["tortoise/sub/reads.py",
                                        "tortoise/sub/tool_registry.py"], (
        "the `rest_spec` scan is not wired to the ATTRIBUTE reader over a RECURSIVE walk — a "
        "comment-only module was reported, the real attribute read was missed, a subpackage "
        "was never reached, the DEFINING module's exclusion was lost, or a second "
        "`tool_registry.py` was exempted by BASENAME while the reader that replaces the "
        "exemption is bound to one exact path — so the F2/F3 fixes could be undone silently "
        "with every row green"
    )


def test_the_product_assertion_is_reachable_from_a_stub_root(tmp_path):
    """Falsifier for the STATEMENT the pin runs, not only for the scan (#13585 review P2#1).

    The seam test above drives `_rest_spec_scan`; this drives
    `_assert_no_product_rest_spec_reads(root)` — the assertion the pin actually executes — over
    a stub root. That is what makes the CALLER falsifiable. Reverting the call inside it to the
    pre-PR inline `"rest_spec" in source.read_text(...)` comprehension now reports the
    comment-only module and reds the first row here; while the assertion was inlined in the
    test that only ever sees the product tree, the same revert reded 0 of 46 rows, because the
    real population's only mention of the field sits in the module the comprehension excludes
    by name.

    Both directions are asserted: silence on a tree that only NAMES the field, and a refusal
    once a module actually reads it.
    """
    product = tmp_path / "tortoise"
    product.mkdir()
    (product / "tool_registry.py").write_text("x = 1\n", encoding="utf-8")
    (product / "comment_only.py").write_text(
        "# the registry rest_spec is unrelated to this container\nx = 1\n", encoding="utf-8")
    # prose is not a read: a false BLOCK here would red the pin on a docs-only edit
    _assert_no_product_rest_spec_reads(tmp_path)

    (product / "reads.py").write_text(
        "for e in TOOL_REGISTRY:\n    app.add_api_route(e.rest_spec.path, handler)\n",
        encoding="utf-8")
    with pytest.raises(AssertionError, match=r"now read `rest_spec`"):
        _assert_no_product_rest_spec_reads(tmp_path)


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
    """The ONE predicate for a static `rest_spec` read: attribute, `getattr`, constant subscript,
    and a constant-key `.get`/`.pop`/`.setdefault`.

    ⛔ Shared by BOTH readers on purpose (`#13585 review` P3 — the EXCLUDED module was graded
    by a weaker spelling reader). `_rest_spec_readers` exempts the DEFINING module's path
    (not a basename, `#13585 review round 4` F3)
    because `_rest_spec_use_outside_the_adapter` is supposed to be the STRICTER read for it;
    while each held its own predicate, that claim was false: the replacement matched
    `ast.Attribute(attr="rest_spec")` and `add_api_route` CALL nodes only, so a read spelled
    `getattr(e, "rest_spec")` or `vars(e)["rest_spec"]` outside the adapter was caught by
    NEITHER arm — excluded by name from one, invisible to the other — in the one module the
    pin's own comment calls "where the route-deriving code LIVES". An exclusion is only as good
    as the reader that replaces it, and two predicates are free to drift; one cannot.
    ⛔ A WRITE IS NOT A READ (#13585 review P3#3): `row.rest_spec = spec` carries the same
    `ast.Attribute(attr="rest_spec")` shape as `row.rest_spec.path`, so BOTH arms gate on
    `ast.Load`. While they did not, every STORE was flagged while this docstring said "read" —
    over-blocking rather than under-blocking, so no row was falsely green, but the copy and the
    code disagreed, and a store derives no route. The store case is now a measured row in the
    falsifier below rather than an assertion in prose.
    """
    if (
        isinstance(node, ast.Attribute)
        and node.attr == _REST_SPEC_FIELD
        and isinstance(node.ctx, ast.Load)
    ):
        return True
    if (
        isinstance(node, ast.Call)
        and _dotted(node.func) == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == _REST_SPEC_FIELD
    ):
        return True
    # ⛔ A CONSTANT-KEY `.get`/`.pop` IS A READ TOO (`#13585 review 402d71d8` P3#2). The three
    # arms above missed `vars(entry).get("rest_spec")` and `row.pop("rest_spec")`, which the
    # substring scan this replaced DID catch — a coverage NARROWING in the false-PASS direction,
    # and an incoherent one: `vars(e)["rest_spec"]` is matched by the subscript arm, so the same
    # read spelled as a method call was invisible. The KEY must be a literal; a computed key
    # (`d.get(field)`) stays in the residual list, because no static pass can answer it.
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "pop", "setdefault"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == _REST_SPEC_FIELD
    ):
        return True
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == _REST_SPEC_FIELD
    )


def _rest_spec_scan(root: Path) -> list[str]:
    """The `rest_spec` scan AS THE PRODUCT RUNS IT: recursive glob, then the attribute reader.

    ⛔ ONE seam, driven from both ends (#13585 review P2#1). The predicate had a test and the
    call line did not: reverting `readers = _rest_spec_readers(scanned, repo)` to the pre-PR
    inline `"rest_spec" in source.read_text(...)` comprehension red NO row, because the scanned
    population's only mention of the field sits in the one module that comprehension excludes
    by name — so the whole F2 fix could be undone with this file green. Extracting glob and the
    name exclusion TOGETHER with the predicate means the falsifier drives the same code the pin
    does, and a revert of any of the three reds a row.

    Stated rather than hidden: no test can falsify its OWN call statement, so the pin's
    one-line invocation of the assertion is un-falsifiable by construction — but that line is a
    call of the ASSERTION, not a re-implementation of it, and the assertion's own call of this
    scan is driven from a stub root by
    `test_the_product_assertion_is_reachable_from_a_stub_root`. A revert of the reading logic
    therefore reds a row instead of leaving the file green.
    """
    scanned = sorted((root / "tortoise").rglob("*.py"))
    return _rest_spec_readers(scanned, root)


def _assert_no_product_rest_spec_reads(root: Path) -> None:
    """The pin's `rest_spec` assertion, root-parameterised so the CALL is falsifiable.

    ⛔ #13585 review P2#1 landed on exactly this call. The pin used to invoke the scan inline
    (`readers = _rest_spec_readers(scanned, repo)`) and no falsifier could reach it: reverting
    it to the pre-PR `"rest_spec" in source.read_text(...)` comprehension reded 0 of 46 rows,
    because the real population's only mention of the field sits in the module that
    comprehension excludes by name. Keeping the ASSERTION and its call in a function that
    takes the ROOT — instead of leaving them as the tail of a test that only ever sees the
    product — lets `test_the_product_assertion_is_reachable_from_a_stub_root` drive the same
    statement over a stub tree where the two predicates disagree.
    """
    readers = _rest_spec_scan(root)
    if readers:
        raise AssertionError(
            f"product module(s) {readers} now read `rest_spec` — routes may be "
            "registry-derived again; re-derive §1.5 before trusting the `rest_spec` half of "
            "the plan")


def _rest_spec_readers(paths: list[Path], root: Path) -> list[str]:
    """Root-relative paths of product modules that READ the `rest_spec` field.

    The PER-FILE half of the scan. `_rest_spec_scan` is the seam the pin drives (#13585 review
    P2#1) — this docstring called ITSELF "the scan's call site", which was true only before the
    seam existed, and labelled the finding "P3#1" while the recorded review numbers it P2.
    `tool_registry.py` is excluded
    here because it is read as its own AST below (`_rest_spec_use_outside_the_adapter`),
    which is stricter: legitimate uses must sit INSIDE `FastAPIRouterAdapter`, and it flags every
    static read spelling this reader does (`_is_rest_spec_read`, shared by both — the exclusion
    is only as good as the reader that replaces it).
    ⛔ THE EXEMPTION IS ONE PATH, NOT ONE BASENAME (`#13585 review round 4` F3). It used to read
    `source.name != "tool_registry.py"`, which exempted every file of that name anywhere under
    `tortoise/`, while the stricter reader that stands in for the exemption is called on exactly
    `tortoise/tool_registry.py` — so a second `tool_registry.py` in a subpackage was graded by
    NEITHER arm, excluded by name from this one and never opened by that one. A no-man's-land is
    the wrong failure for a pin whose claim is "nothing but that adapter derives a route from a
    row", so the exemption names the defining module's PATH.
    """
    exempt = (root / "tortoise" / "tool_registry.py").resolve()
    return [
        str(source.relative_to(root))
        for source in paths
        if source.resolve() != exempt and _rest_spec_attribute_reads(source)
    ]


def _rest_spec_attribute_reads(source: Path) -> list[int]:
    """Line numbers where `source` READS the `rest_spec` field.

    Three shapes count as a read, and prose counts as none: an attribute access
    (`e.rest_spec`), a `getattr(e, "rest_spec")` call, and a constant subscript
    (`e["rest_spec"]`). A fourth is a constant-KEY method call (`d.get("rest_spec")`,
    `d.pop("rest_spec")` — see the arm below). A comment or a docstring is not an
    `ast.Attribute`, so it cannot red
    the pin — which is the whole point (#13537 review F2; the sibling reader's docstring above
    records the identical false BLOCK for the literal `FastAPIRouterAdapter(`).

    ⛔ "Access" means `ast.Load`: a WRITE (`row.rest_spec = spec`, `entry["rest_spec"] = spec`)
    has the same node shape and is NOT a read, so both arms gate on it (#13585 review P3#3).

    Deliberately NOT matched, stated rather than hidden: a field name assembled at runtime
    (`getattr(e, "rest" + "_spec")`), and a keyed read whose KEY is computed rather than
    literal (`d[field]`, `d.get(field)`) — neither is answerable by a static pass, and both are
    the same shape as the runtime-assembled name above.

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
    entry = _any_entry(door)
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
    entries = [e for e in (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY)
               if e.name == "tortoise_issue_insight"]
    assert entries, (
        "tortoise_issue_insight left BOTH registries — re-derive this pin")
    # The LITERAL table, not the row: a row whose method changed would make this pin assert
    # whatever the mutated row now says, and `/v1/context` shows the path is not unique per
    # verb, so both the method and the path have to come from the literal.
    method, path = REST_SPEC_DOORS["tortoise_issue_insight"]
    assert (entries[0].rest_spec.method, entries[0].rest_spec.path) == (method, path), (
        f"tortoise_issue_insight's row is {entries[0].rest_spec.method} "
        f"{entries[0].rest_spec.path}, not {method} {path}")

    full = _RecordingRouter()
    all_handlers = {e.name: (lambda: None)
                    for e in (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY)
                    if e.rest_spec is not None}
    FastAPIRouterAdapter(full).register_all(
        (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY), all_handlers)
    assert any(p == path and m == (method,) for p, m, _ in full.routes), (
        f"no {method} {path} route was registered by the adapter — {full.routes}")

    # …and without THIS row, with a handler for every surviving route-bearing row, exactly
    # that one route disappears. (A short handler map would register nothing at all and so
    # would not measure the removal.)
    # ⛔ The SERVED set, not `TOOL_REGISTRY`: #11609 lane P6 retired this row, and a probe
    # over the advertised set alone would report the route as never derivable — a FALSE RED
    # on a retirement whose whole premise is that the ROW still carries the route.
    survivors = [e for e in (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY)
                 if e.name != entries[0].name]
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
    `poll_events`, `list_memory_graphs`) does NOT make these retirable — and a
    `tenancy:`/`sdk:` prefix is a router/SDK path, not a door at all. (#11609 lane P6
    retired two of the original nine — `delete_point` / `delete_entity` — because the
    #9660 ruling executes the `delete_knowledge` row by KEEPING `tortoise_delete`, which
    makes that destination a live door under its own name.)
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
