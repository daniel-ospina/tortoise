"""Capability model for the tool-surface guards (#4113).

The guards in ``tests/test_tool_registry.py`` and
``tests/test_abuse_integration.py`` protect *properties of operations* — does
an operation create/mutate graph nodes, reach the filesystem, or mutate the
control plane — but they used to assert those properties by comparing
hardcoded **tool-name strings**.  A rename or a merge in the #3863/#3994
surface cutover then made the assertions trivially true or trivially false.

This module derives the properties from the code itself (AST analysis), keyed
on the *operation* (the SDK method / the handler's call graph), so a rename or
a merge cannot blind a guard.  Every derivation accepts an injectable
``source``/``tree`` so a falsifiability test can feed a synthetic violation and
prove the predicate can fail — an assertion that survives its own bug is dead.

Nothing here imports production state mutably; it is read-only introspection.

Sibling name-keyed guards in ``tests/test_mcp_http.py`` are the same class but
are filed separately (#4121) and out of scope here.
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import NamedTuple

import tortoise.mcp_server as mcp_server
import tortoise.sdk as sdk_module

# ── Declared sets (each with a reason; exactness is asserted by the guards) ──

# Operator-only operations whose exclusion from tenant HTTP is a *policy*
# decision, not a structural capability (#329).  Keyed on the operation, not
# the tool name.
HTTP_EXCLUDED_SDK_METHODS: dict[str, str] = {
    "backfill_v25": "whole-schema migration",
    "dream": "CPU-heavy whole-graph EP (#329)",
}

# Operations that DO write on an internal self-heal / cache-refresh path but
# are deliberately read-classified (a read-only MCP key may call them).  This
# is a recorded posture (get_confidence/compute_confidence cite #1157; the
# read-through decision is tracked in #4122).  The guard documents it and does
# NOT override it.
READ_THROUGH_WRITE_METHODS: dict[str, str] = {
    "compute_confidence": "persists computed `SET n.confidence` (#1157/#4122)",
    "get_confidence": "lazy `dream(dirty_only=True)` writes n.confidence (#1157/#4122)",
    "get_tenant_packs": "self-heal `ensure_tenant_packs` MERGE (:PackInstall) (#4122)",
}

# Graph writers reached through helpers OUTSIDE the sdk.py derivation boundary
# (pack_manifest_store.py, mining.py, hosted_api.py).  Each must be bound to a
# writer-annotated tool that is in WRITE_TOOL_NAMES.
NON_SDK_WRITER_OPERATIONS: dict[str, str] = {
    "upsert_tenant_manifest": "MERGE (:PackManifest)/(:PackInstall) — "
                              "tortoise/pack_manifest_store.py",
}

# The TOOL that carries each non-SDK writer operation.  #4035 corrected the
# `tortoise_pack_install` declaration to the handler-served idiom
# (`sdk_method=""`; the helper is reached from `mcp_server.py`), so the
# operation is no longer a declared SDK label — the binding is by TOOL NAME.
NON_SDK_WRITER_BINDINGS: dict[str, str] = {
    "upsert_tenant_manifest": "tortoise_pack_install",
}

# Reads reached through helpers OUTSIDE the sdk.py derivation boundary.  A
# read-through op that WRITES on a self-heal path (`get_tenant_packs` →
# `ensure_tenant_packs` MERGE (:PackInstall)) is covered by
# READ_THROUGH_WRITE_METHODS; it lands here for the *declared-binding* half,
# because #4035 blanked that method's only declaration
# (`tortoise_packs_list.sdk_method=""` — handler-served, #4035) and a
# declared-set entry with no declaring registry row is otherwise unchecked.
NON_SDK_READ_OPERATIONS: dict[str, str] = {
    "get_tenant_packs": "pack_state.get_tenant_packs → self-heal "
                        "`ensure_tenant_packs` MERGE (:PackInstall) (#4122)",
}

# The TOOL that carries each non-SDK read operation, mirroring
# NON_SDK_WRITER_BINDINGS.  A blanked declaration removes the op from the
# `by_method` index, so the read-classification property must also be asserted
# per TOOL NAME or it silently stops being checked (#4035 review).
NON_SDK_READ_BINDINGS: dict[str, str] = {
    "get_tenant_packs": "tortoise_packs_list",
}

# Registry `sdk_method` labels that do not resolve to a TortoiseSDK attribute.
# These are the #3994 `fix-declaration` rows (declared-SDK-link drift, not
# missing features).  Kept exact so a *new* dangling declaration fails.
# #4035 blanked `analyze`/`entity_profile`/`upsert_tenant_manifest` and
# `get_tenant_packs`, so only the retired `tortoise_health` (`health`) is still a
# registry row.  `get_tenant_packs` moved to NON_SDK_READ_OPERATIONS: its only
# declaration is gone, so keeping it here would exempt a *future* row that
# re-declares it from resolution — a re-declaration on a handler-served tool is
# the defect, not an exemption.
DANGLING_SDK_DECLARATIONS: frozenset[str] = frozenset({"health"})

class DeclaredBindingDivergence(NamedTuple):
    """One recorded declared-binding divergence (#4337).

    Records the WHOLE divergence — the declared method, the public destinations the
    handler actually reaches, and why — not just the tool name.  A name-only ledger
    would be a blanket exemption: the tool could re-diverge to a *different* method
    and stay green.  Recording both sides means the entry cannot outlive the
    specific defect it records.
    """
    declared: str
    reached: frozenset[str]
    reason: str


# Entries whose handler does NOT reach the SDK method the entry declares — the
# DECLARED-BINDING DIVERGENCE class (#4337).  A declaration that merely RESOLVES is
# not a declaration that is USED.  Both entries passed every check here, because no
# arm compared the DECLARATION against the operations the handler reaches.
#
# KNOWN BOUND, inherited from `handler_operations`: it records an attribute
# REFERENCE to an SDK method, not a call.  A handler that merely names its declared
# method in dead code (`if False: return _get_org_sdk().query`) would satisfy the
# arm.  Both real defects are genuine calls to *other* methods with no mention of
# the declared one, so both are caught; tightening the reach semantics is a
# `handler_operations` change that would move every other check that consumes it,
# and is deliberately not done here.
DECLARED_BINDING_DIVERGENCES: dict[str, DeclaredBindingDivergence] = {
    "tortoise_operator_action": DeclaredBindingDivergence(
        "operator_action", frozenset({"annotate_operator", "mitigate_operator"}),
        "declares operator_action; the handler branches on action= to "
        "mitigate_operator / annotate_operator and never calls it"),
    "tortoise_traverse": DeclaredBindingDivergence(
        "traverse", frozenset(),
        "declares traverse; the handler calls navigation.tortoise_traverse(proj.db, "
        "...) and reaches no public SDK method"),
}

# Operations a handler reaches that are reads with no registry binding (they
# need no binding).  Enumerated so the binding-resolution guard is scoped.
UNBOUND_READ_OPERATIONS: frozenset[str] = frozenset({
    "recall_gaps", "recall_subgraph",
})

# Filesystem-api users on internal/derived paths — NOT caller-supplied walks,
# so they are not "reaches a filesystem path" for the HTTP-exclusion guard.
INTERNAL_PATH_READERS: frozenset[str] = frozenset({
    "_probe_embedded_busy", "session_index_health",
})

# The 8 wrap sites that pass `abuse_weight` (R1 point-create metering).  Every
# other `_quota_gated` site must NOT.
WEIGHT_BEARING_METHODS: frozenset[str] = frozenset({
    "create_point", "create_operator", "mitigate_operator", "file_decision",
    "file_human_approval", "diary_write", "ingest", "checkpoint",
})

# The DECLARED write surface: operation -> surface label.  This is the one
# place a tool name is declared (not looked up) — the issue's inverse assertion
# ("every mapped write method must still have a wrap site") is meaningful only
# against a declared map.  Staleness fails loudly (both directions + name
# liveness).
DECLARED_WRITE_SURFACE_MAP: dict[str, str] = {
    "create_point": "tortoise_create_point",
    "update_point": "tortoise_update_point",
    "create_operator": "tortoise_create_operator",
    "mitigate_operator": "tortoise_mitigate_operator",
    "file_decision": "tortoise_file_decision",
    "file_human_approval": "tortoise_file_human_approval",
    "invalidate_point": "tortoise_invalidate",
    "supersede": "tortoise_supersede",
    "retract_point": "tortoise_retract_point",
    "checkpoint": "tortoise_checkpoint",
    "diary_write": "tortoise_diary_write",
    "create_entity": "tortoise_create_entity",
    "update": "tortoise_update",
    "annotate_operator": "tortoise_operator_action",
    "create_subject": "tortoise_create_subject",
    "create_object": "tortoise_create_object",
    "create_event": "tortoise_create_event",
    "create_document": "tortoise_create_document",
    "create_source": "tortoise_create_source",
    "assess_source": "tortoise_assess_source",
    "update_entity": "tortoise_update_entity",
    "create_edge": "tortoise_create_edge",
    "ingest": "tortoise_ingest",
    "index_directory": "tortoise_index_files",
}

# Tenant-scoped control-plane labels: a registry mutation whose labels are
# ENTIRELY within this set is a tenant graph-admin write (HTTP-allowed by
# design), not an operator-only operation.
TENANT_SCOPED_LABELS: frozenset[str] = frozenset({":Graph"})

# Registry entries with an EMPTY `sdk_method` whose write happens in hosted_api
# (outside the sdk.py derivation).  Writer ones must be writer-annotated +
# WRITE_TOOL_NAMES; the reads are the declared read side.  Kept exact so a new
# empty-binding entry must be declared.
NON_SDK_WRITER_TOOLS: frozenset[str] = frozenset({
    "tortoise_session_capture", "tortoise_graph_set_recording",
    "tortoise_onboarding_demo_create", "tortoise_onboarding_seed",
    "tortoise_onboarding_session_recording",
    "tortoise_onboarding_github_connect", "tortoise_onboarding_github_index",
    # #4035: handler-served writer (reaches pack_manifest_store.upsert_tenant_manifest)
    "tortoise_pack_install",
})
NON_SDK_READ_TOOLS: frozenset[str] = frozenset({
    "tortoise_overview", "tortoise_get", "tortoise_onboarding_state",
    "tortoise_onboarding_github_status",
    # #4035: handler-served reads (declaration corrected to sdk_method="")
    "tortoise_packs_list", "tortoise_entity_profile", "tortoise_analyze",
})

# Writer-annotated tools that are NOT in WRITE_TOOL_NAMES because they are
# HTTP-excluded (operator-only) and self-guard with _http_excluded_error().
NON_HTTP_WRITER_TOOLS: frozenset[str] = frozenset({
    "tortoise_backfill_v25", "tortoise_dream", "tortoise_index_sessions",
    "tortoise_ingest_corpus", "tortoise_org_create",
})


def served_registry() -> list:
    """Every entry the server can RESOLVE — live plus retired (#3883).

    A retired name is not advertised, but it is still served (through the
    warning shim) and still callable by name, so the capability guards must
    cover it: an HTTP-excluded retired writer is exposed by exactly the same
    path as a live one, and exempting it from the guard removes the only
    tripwire on the self-guard that keeps it off the tenant surface.
    """
    from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY

    return [*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY]

# Filesystem-walk API method names.  A call to one of these (on a non-projection
# receiver) marks the operation as reaching a filesystem path.
_FS_ATTRS: frozenset[str] = frozenset({
    "walk", "scandir", "listdir", "rglob", "glob", "iterdir", "read_text",
    "read_bytes", "open",
})

# Cypher clause shape + mutating keyword.
_MUTATING = re.compile(r"\b(CREATE|MERGE|DETACH\s+DELETE|DELETE|SET|REMOVE|DROP)\b")
# A decisive mutating clause (`CREATE (`, `MERGE (`, `DETACH DELETE`) is
# sufficient on its own — a bare `CREATE (g:Graph {...})` has no MATCH hint.
# Schema DDL (CREATE INDEX/CONSTRAINT) is deliberately NOT matched: _get_registry()
# lazily runs _ensure_registry_indexes(), so counting DDL as mutation would make
# every control-plane READ (org_list, graph_list, apikey_list, …) a "mutator"
# and false-red a legitimate read tool after the cutover.  The schema bootstrap
# is not a tool-surface node write.
_MUTATING_STRONG = re.compile(r"\b(CREATE|MERGE)\s*[\(\{]|DETACH\s+DELETE")
_CYPHER_HINT = re.compile(r"\b(MATCH|RETURN|WHERE|UNWIND|CALL|WITH)\b")
_LABEL = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

# Projection write helpers — a call to one of these mutates the graph.
_PROJ_WRITE = re.compile(
    r"^_?(apply$|create_|update_|delete_|merge_|link_|add_|remove_|set_|upsert_|journal_)"
)

# Attribute receivers treated as NOT filesystem roots.
_NON_FS_RECEIVERS = frozenset({"proj", "reg", "registry", "g", "log", "logger"})


# ── Source loading ──────────────────────────────────────────────────────────


def sdk_source() -> str:
    return pathlib.Path(sdk_module.__file__).read_text()


def mcp_source() -> str:
    return pathlib.Path(mcp_server.__file__).read_text()


def _tree(source: str | None, default: str) -> ast.Module:
    return ast.parse(source if source is not None else default)


@lru_cache(maxsize=8)
def _mcp_tree(source: str | None) -> ast.Module:
    return _tree(source, mcp_source())


@lru_cache(maxsize=8)
def _sdk_tree(source: str | None) -> ast.Module:
    return _tree(source, sdk_source())


def _first_docstring_ids(node: ast.AST) -> set[int]:
    body = getattr(node, "body", None)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return {id(body[0].value)}
    return set()


# ── SDK method derivations ──────────────────────────────────────────────────


def _sdk_methods(tree: ast.Module) -> tuple[dict[str, ast.AST], dict[str, ast.AST]]:
    """Return (TortoiseSDK methods, module-level functions) from an SDK tree."""
    methods: dict[str, ast.AST] = {}
    funcs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[item.name] = item
    return methods, funcs


def _is_projection(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in ("proj", "projection")
    if isinstance(node, ast.Call):
        return _is_projection(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr == "_get_proj"
    return False


def _scan_body(node: ast.AST, const_map: dict[str, str] | None = None) \
        -> tuple[bool, bool, bool, set, set[str]]:
    """Scan one function body.

    Returns (mutates_directly, reaches_fs_directly, uses_registry,
             callees: set[(receiver_id|None, attr)], labels: set[str]).
    """
    doc_ids = _first_docstring_ids(node)
    mutates = False
    reaches_fs = False
    uses_registry = False
    labels: set[str] = set()
    callees: set[tuple[str | None, str]] = set()

    const_map = const_map or {}
    for sub in ast.walk(node):
        text: str | None = None
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                and id(sub) not in doc_ids:
            text = sub.value
        elif isinstance(sub, ast.JoinedStr):
            text = ast.unparse(sub)
        elif isinstance(sub, ast.Name) and sub.id in const_map:
            # Cypher held in a module-level constant and passed by name
            text = const_map[sub.id]
        if text is not None and _MUTATING.search(text) and (
                _CYPHER_HINT.search(text) or _MUTATING_STRONG.search(text)):
            mutates = True
            labels.update(":" + m for m in _LABEL.findall(text))
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Attribute):
                receiver = getattr(fn.value, "id", None)
                callees.add((receiver, fn.attr))
                if fn.attr == "_get_registry":
                    uses_registry = True
                if fn.attr in _FS_ATTRS and receiver not in _NON_FS_RECEIVERS:
                    reaches_fs = True
                if _is_projection(fn.value) and _PROJ_WRITE.match(fn.attr):
                    mutates = True
            elif isinstance(fn, ast.Name):
                callees.add((None, fn.id))
                if fn.id in _FS_ATTRS:
                    reaches_fs = True
    return mutates, reaches_fs, uses_registry, callees, labels


def _closure(
    seeds: set[str],
    calls: dict[str, set[tuple[str | None, str]]],
    module_funcs: set[str],
) -> set[str]:
    """Transitive closure over `self.<m>()` and module-level `<f>()` calls."""
    result = set(seeds)
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            if name in result:
                continue
            for receiver, attr in callees:
                if receiver == "self" and attr in result:
                    result.add(name)
                    changed = True
                    break
                if receiver is None and f"mod:{attr}" in module_funcs \
                        and f"mod:{attr}" in result:
                    result.add(name)
                    changed = True
                    break
    return result


@lru_cache(maxsize=8)
def _sdk_analysis(source: str | None) -> dict:
    tree = _sdk_tree(source)
    methods, funcs = _sdk_methods(tree)
    calls: dict[str, set] = {}
    direct_mut: set[str] = set()
    direct_fs: set[str] = set()
    registry_users: set[str] = set()
    method_labels: dict[str, set[str]] = {}

    const_map: dict[str, str] = {}
    for item in tree.body:
        if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant) \
                and isinstance(item.value.value, str):
            for t in item.targets:
                if isinstance(t, ast.Name):
                    const_map[t.id] = item.value.value

    all_nodes = {**{f"mod:{k}": v for k, v in funcs.items()}, **methods}
    for name, node in all_nodes.items():
        mut, fs, reg, callees, labels = _scan_body(node, const_map)
        calls[name] = callees
        if mut:
            direct_mut.add(name)
        if fs:
            direct_fs.add(name)
        if reg:
            registry_users.add(name)
        method_labels[name] = labels

    module_funcs = set(f"mod:{k}" for k in funcs)
    mutators = _closure(direct_mut, calls, module_funcs)
    fs_methods = _closure(direct_fs, calls, module_funcs)
    # registry reach is TRANSITIVE too: a public wrapper that delegates the
    # control-plane mutation to a private helper must still be operator-only.
    registry_users = _closure(registry_users, calls, module_funcs)

    # transitive label union
    transit_labels: dict[str, set[str]] = {}
    for name in all_nodes:
        seen: set[str] = set()
        own: set[str] = set()
        stack = [name]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            own |= method_labels.get(cur, set())
            for receiver, attr in calls.get(cur, set()):
                nxt = attr if receiver == "self" else (f"mod:{attr}" if receiver is None else None)
                if nxt and nxt in all_nodes:
                    stack.append(nxt)
        transit_labels[name] = own

    operator_only: set[str] = set()
    for name in mutators:
        if name not in registry_users:
            continue
        labels = transit_labels.get(name, set())
        if not labels:
            # registry mutation with no literal label → fail closed (operator-only)
            operator_only.add(name)
            continue
        if not labels.issubset(TENANT_SCOPED_LABELS):
            operator_only.add(name)

    return {
        "methods": set(methods),
        "module_funcs": set(funcs),
        "calls": calls,
        "mutators": mutators,
        "fs_methods": fs_methods,
        "operator_only": operator_only,
    }


def sdk_graph_mutators(source: str | None = None) -> frozenset[str]:
    """Operations whose body creates/mutates graph nodes (transitively).

    Members are TortoiseSDK method names, plus module-level helpers as
    ``mod:<name>`` (the closure traverses them).  Schema DDL (CREATE
    INDEX/CONSTRAINT) is NOT a node mutation and is excluded.
    """
    return frozenset(_sdk_analysis(source)["mutators"])


def sdk_filesystem_methods(source: str | None = None) -> frozenset[str]:
    """Operations reaching a filesystem walk (transitively; ``mod:<name>``
    entries included), minus the declared INTERNAL_PATH_READERS."""
    return frozenset(_sdk_analysis(source)["fs_methods"] - INTERNAL_PATH_READERS)


def sdk_operator_only_mutators(source: str | None = None) -> frozenset[str]:
    """Graph mutators that mutate the control-plane registry (fail-closed)."""
    return frozenset(_sdk_analysis(source)["operator_only"])


def sdk_method_names(source: str | None = None) -> frozenset[str]:
    return frozenset(_sdk_analysis(source)["methods"])


# ── MCP handler analysis ────────────────────────────────────────────────────


@dataclass(frozen=True)
class HandlerOperations:
    operations: frozenset[str]
    unresolved: tuple[str, ...]  # human-readable dynamic-dispatch sites


def _module_level_funcs(tree: ast.Module) -> dict[str, ast.AST]:
    """Only MODULE-LEVEL defs — `register_all` resolves `globals()[name]`, so a
    nested def or a class method is not a handler."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
    return out


def _mcp_functions(tree: ast.Module) -> dict[str, ast.AST]:
    funcs: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.setdefault(node.name, node)
    return funcs


def _is_sdk_factory(call: ast.AST) -> bool:
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id in ("_get_org_sdk", "_get_sdk")
    )


def _recv_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _param_names(node: ast.AST) -> set[str]:
    args = getattr(node, "args", None)
    if not isinstance(args, ast.arguments):
        return set()
    return {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}


def _dynamic_dispatch(node: ast.AST) -> list[str]:
    """Detect dispatch shapes whose target cannot be statically resolved.

    Covers the immediate forms (`_HANDLERS[name](...)`, `getattr(x, y)(...)`,
    `partial(..)(...)`) AND the resolve-then-call form (`h = _TABLE[name];
    h()`, `fn = getattr(sdk, "create_point"); fn()`) — the merge shape a
    name-keyed dispatch table produces.
    """
    def _has_lookup(value: ast.AST) -> bool:
        for n in ast.walk(value):
            if isinstance(n, ast.Subscript):
                return True
            if isinstance(n, ast.Call) and (
                    (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
                    in ("get", "setdefault", "pop", "getattr", "partial")):
                return True
        return False

    found: list[str] = []
    dynamic: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Assign, ast.AnnAssign)):
            value = sub.value
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            if value is not None and _has_lookup(value):
                for t in targets:
                    for elt in (t.elts if isinstance(t, ast.Tuple) else [t]):
                        key = _recv_key(elt)
                        if key:
                            dynamic.add(key)
        elif isinstance(sub, ast.NamedExpr):
            if _has_lookup(sub.value):
                key = _recv_key(sub.target)
                if key:
                    dynamic.add(key)
        elif isinstance(sub, ast.For):
            it = ast.unparse(sub.iter)
            if ".values()" in it or ".items()" in it or ".keys()" in it:
                for n in ast.walk(sub.target):
                    if isinstance(n, ast.Name):
                        dynamic.add(n.id)
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        # an immediately-invoked computed value: `_HANDLERS[k]()`,
        # `_HANDLERS.get(k)()`, `getattr(x, y)()`, `partial(...)()`
        if isinstance(fn, ast.Subscript):
            found.append(f"line {sub.lineno}: subscript-call dispatch")
        elif isinstance(fn, ast.Call):
            found.append(f"line {sub.lineno}: call-of-call dispatch")
        elif isinstance(fn, ast.Name) and fn.id in dynamic:
            found.append(f"line {sub.lineno}: resolve-then-call dispatch ({fn.id})")
        elif isinstance(fn, ast.Attribute) and fn.attr in ("getattr", "partial"):
            found.append(f"line {sub.lineno}: {fn.attr}() dispatch")
    return found


@lru_cache(maxsize=8)
def _handler_operations_cached(tool_name: str, source: str | None) -> HandlerOperations:
    tree = _mcp_tree(source)
    funcs = _mcp_functions(tree)
    start = funcs.get(tool_name)
    if start is None:
        # Fail closed: a registry entry with no resolvable handler (register_all
        # silently skips one — #2210) must not silently yield "no operations".
        return HandlerOperations(frozenset(), (f"no handler for {tool_name}",))

    # module-level simple aliases: `_alias = _helper` (a merge-time refactor)
    func_alias: dict[str, str] = {}
    module_aliases: set[str] = set()  # `import tortoise.sdk as s`
    for item in tree.body:
        if isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name) and isinstance(item.value, ast.Name) \
                        and item.value.id in funcs:
                    func_alias[t.id] = item.value.id
        if isinstance(item, ast.Import):
            for alias in item.names:
                if alias.name in ("tortoise.sdk", "tortoise.sdk.sdk"):
                    module_aliases.add(alias.asname or alias.name.split(".")[-1])
                    module_aliases.add(alias.asname or ".".join(alias.name.split(".")[:-1]))
        if isinstance(item, ast.ImportFrom) and item.module == "tortoise":
            for alias in item.names:
                if alias.name == "sdk":
                    module_aliases.add(alias.asname or "sdk")

    sdk_methods = sdk_method_names()
    ops: set[str] = set()
    unresolved: list[str] = []
    seen: set[str] = set()
    stack: list[ast.AST] = [start]
    while stack:
        node = stack.pop()
        unresolved.extend(_dynamic_dispatch(node))
        params = _param_names(node)
        # function-local imports (`import tortoise.sdk as s` inside the body)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                for alias in sub.names:
                    if alias.name == "tortoise.sdk":
                        module_aliases.add(alias.asname or "sdk")
            if isinstance(sub, ast.ImportFrom) and sub.module == "tortoise":
                for alias in sub.names:
                    if alias.name == "sdk":
                        module_aliases.add(alias.asname or "sdk")
        aliases: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                value = sub.value
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                if value is not None and _is_sdk_factory(value):
                    for t in targets:
                        if isinstance(t, ast.Tuple):
                            for elt in t.elts:
                                key = _recv_key(elt)
                                if key:
                                    aliases.add(key)
                        else:
                            key = _recv_key(t)
                            if key:
                                aliases.add(key)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute):
                receiver = sub.value
                key = _recv_key(receiver)
                if _is_sdk_factory(receiver) or (key is not None and key in aliases):
                    ops.add(sub.attr)
                elif key is not None and key in params and sub.attr in sdk_methods:
                    # an SDK handle passed into a helper: `_do(_get_org_sdk())`
                    ops.add(sub.attr)
                elif key in module_aliases and sub.attr in sdk_methods:
                    # `import tortoise.sdk as s; s.create_point(...)`
                    ops.add(sub.attr)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                callee_name = sub.func.id
                if callee_name in sdk_methods and callee_name not in funcs:
                    # `from tortoise.sdk import create_point; create_point(...)`
                    ops.add(callee_name)
                callee = func_alias.get(callee_name, callee_name)
                if callee in funcs and callee not in seen:
                    seen.add(callee)
                    stack.append(funcs[callee])
    # Private (`_`-prefixed) attributes are kept: a directly-reached private
    # mutator (e.g. `_get_org_sdk()._graph_create`) is a hidden write.  The
    # binding guard filters `_`-internals itself.
    return HandlerOperations(frozenset(ops), tuple(unresolved))


def handler_operations(tool_name: str, source: str | None = None) -> HandlerOperations:
    return _handler_operations_cached(tool_name, source)


def all_handler_operations(source: str | None = None) -> dict[str, HandlerOperations]:
    # The SERVED set (#3883): a retired name is still resolvable and callable by name,
    # so a guard that reads the live list alone leaves a retired entry's handler
    # unchecked. `served_registry()` is live + retired.
    return {e.name: handler_operations(e.name, source) for e in served_registry()}


# ── Wrap-site analysis ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class WrapSites:
    # (method, lineno) -> has abuse_weight
    sites: tuple[tuple[str, int, bool], ...]
    unresolved: tuple[str, ...]


@lru_cache(maxsize=8)
def _wrap_sites_cached(source: str | None) -> WrapSites:
    tree = _mcp_tree(source)
    sites: list[tuple[str, int, bool]] = []
    unresolved: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_quota_gated"):
            continue
        if not node.args:
            unresolved.append(f"line {node.lineno}: _quota_gated with no positional arg")
            continue
        first = node.args[0]
        method: str | None = None
        if isinstance(first, ast.Attribute):
            if _is_sdk_factory(first.value):
                method = first.attr
            elif isinstance(first.value, ast.Name):
                method = first.attr  # aliased sdk handle
        if method is None:
            unresolved.append(f"line {node.lineno}: _quota_gated(<unresolvable>)")
            continue
        has_weight = any(k.arg == "abuse_weight" for k in node.keywords)
        sites.append((method, node.lineno, has_weight))
    return WrapSites(tuple(sites), tuple(unresolved))


def quota_gated_wrap_sites(source: str | None = None) -> WrapSites:
    return _wrap_sites_cached(source)


# ── Registry helpers ────────────────────────────────────────────────────────


def registry_entries_by_method() -> dict[str, list]:
    # Served set (#3883) — see all_handler_operations().
    out: dict[str, list] = {}
    for entry in served_registry():
        if entry.sdk_method:
            out.setdefault(entry.sdk_method, []).append(entry)
    return out


def tools_reaching(operation: str, source: str | None = None) -> set[str]:
    """Served tool names whose handler reaches `operation` (retired included, #3883)."""
    return {
        e.name for e in served_registry()
        if operation in handler_operations(e.name, source).operations
    }


@lru_cache(maxsize=8)
def _self_guard_cached(tool_name: str, source: str | None) -> bool:
    """True iff the handler's first COMPOUND statement is the HTTP guard — simple
    statements (assignments) may precede it.

    An `http_policy=False` tool is still callable by name over HTTP (only
    `tools/list` is filtered), so it must reject HTTP calls itself. Production
    uses a leading `if _transport_mode.get() == "http": return
    _http_excluded_error()`. The test must be a POSITIVE transport-is-http check
    (`_transport... == "http"` or `*_transport*http*()`), so a negated,
    conjunctive or dead guard does not count.
    """
    tree = _mcp_tree(source)
    # module-level only: a nested stub is NOT a real HTTP check
    funcs = _module_level_funcs(tree)
    start = funcs.get(tool_name)
    if start is None:
        return False
    body = [s for s in getattr(start, "body", [])
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    # Find the leading HTTP `If` guard, allowing only simple (non-compound)
    # statements before it — a benign assignment must not cause a false-red,
    # while a nested/dead guard (a compound first statement, or a test that
    # does not reference the transport signal) is still rejected.
    guard = None
    for stmt in body:
        if isinstance(stmt, ast.If):
            guard = stmt
            break
        if isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try, ast.With,
                             ast.Return, ast.Raise, ast.AsyncFor, ast.AsyncWith)):
            return False
    if guard is None or not _positive_http_test(guard.test, funcs):
        return False
    return _returns_directly_guards(guard.body, funcs)


def _positive_http_test(test: ast.AST, funcs: dict[str, ast.AST]) -> bool:
    """A POSITIVE check that the transport IS http — an inverted
    (`if not _transport...`) or conjunctive guard must NOT count."""
    if isinstance(test, (ast.UnaryOp, ast.BoolOp)):
        return False
    if isinstance(test, ast.Compare):
        left = ast.unparse(test.left)
        return "_transport" in left and any(
            isinstance(c, ast.Constant) and c.value == "http" for c in test.comparators)
    if isinstance(test, ast.Call):
        name = ast.unparse(test.func)
        if "transport" in name and "http" in name:
            # must be a MODULE-LEVEL callee — a nested stub returning False is
            # not a real HTTP check.
            return isinstance(test.func, ast.Name) and test.func.id in funcs
        return False
    return False


def _returns_directly_guards(body: list, funcs: dict, _depth: int = 0) -> bool:
    """True if `body` directly returns `_http_excluded_error()` (possibly via a
    module-level helper that returns it)."""
    if _depth > 4:
        return False
    for stmt in body:
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            value = stmt.value
            if isinstance(value, ast.Call):
                callee = getattr(value.func, "id", None)
                if callee == "_http_excluded_error":
                    return True
                helper = funcs.get(callee) if callee else None
                if helper is not None and _returns_directly_guards(
                        getattr(helper, "body", []), funcs, _depth + 1):
                    return True
    return False


def handler_self_guards(tool_name: str, source: str | None = None) -> bool:
    """True if the handler rejects HTTP calls (an HTTP-excluded tool is still
    callable by name; only `tools/list` is filtered)."""
    return _self_guard_cached(tool_name, source)


# ── Guard predicates ────────────────────────────────────────────────────────
# Each returns a list of human-readable violations; the real tests assert the
# list is empty, and the falsifiability tests feed a synthetic input and assert
# it is NOT empty.  Pure functions — no global registry state — so a synthetic
# entry list can be evaluated.


def privileged_exposure_violations(entries, mcp_src: str | None = None) -> list[str]:
    """Operator-only / filesystem operations must be HTTP-excluded, and an
    HTTP-excluded tool must self-guard (it is still callable by name)."""
    by_method: dict[str, list] = {}
    for e in entries:
        if e.sdk_method:
            by_method.setdefault(e.sdk_method, []).append(e)
    operator_only = (sdk_filesystem_methods() | sdk_operator_only_mutators()
                     | set(HTTP_EXCLUDED_SDK_METHODS))
    out: list[str] = []
    for e in entries:
        reached = handler_operations(e.name, mcp_src).operations & operator_only
        declares = e.sdk_method in operator_only
        if (declares or reached) and e.http_policy:
            out.append(
                f"{e.name}: binds/reaches operator-only or filesystem operation "
                f"{sorted(reached or {e.sdk_method})} but is HTTP-exposed")
        if (declares or reached) and e.http_policy is False \
                and not handler_self_guards(e.name, mcp_src):
            out.append(
                f"{e.name}: HTTP-excluded but its handler does not "
                f"self-guard (_http_excluded_error) — callable by name over HTTP")
    return out


def write_classification_violations(entries, mcp_src: str | None = None) -> list[str]:
    """Every handler-reached graph mutation must be write-classified (or the
    carrying tool must be a self-guarding HTTP-excluded tool), and every
    writer-annotated tool must be in WRITE_TOOL_NAMES."""
    import tortoise.mcp_server as _ms

    mutators = sdk_graph_mutators()
    by_method: dict[str, list] = {}
    for e in entries:
        if e.sdk_method:
            by_method.setdefault(e.sdk_method, []).append(e)
    out: list[str] = []
    for e in entries:
        reached_writes = (
            handler_operations(e.name, mcp_src).operations & mutators
        ) - set(READ_THROUGH_WRITE_METHODS)
        if reached_writes:
            if e.http_policy:
                if e.annotations is None or e.annotations.readOnlyHint is not False:
                    out.append(
                        f"{e.name}: read-only annotation but handler reaches "
                        f"write(s) {sorted(reached_writes)}")
                if e.name not in _ms.WRITE_TOOL_NAMES:
                    out.append(f"{e.name}: reaches a write but not in WRITE_TOOL_NAMES")
            elif not handler_self_guards(e.name, mcp_src):
                out.append(
                    f"{e.name}: HTTP-excluded writer whose handler does not self-guard")
        # 2b — writer-annotated must be write-classified or self-guarded
        writer_annotated = (
            e.annotations is not None and e.annotations.readOnlyHint is False
        )
        if writer_annotated and e.name not in _ms.WRITE_TOOL_NAMES and not (
                e.http_policy is False and handler_self_guards(e.name, mcp_src)):
            out.append(
                f"{e.name}: writer-annotated but not in WRITE_TOOL_NAMES "
                f"and not a self-guarding HTTP-excluded tool")
        # 2c — empty sdk_method must be declared
        if not e.sdk_method and e.name not in (NON_SDK_WRITER_TOOLS | NON_SDK_READ_TOOLS):
            out.append(f"{e.name}: empty sdk_method but not declared non-SDK")
    # non-SDK writer operations must be bound to a writer tool in WRITE_TOOL_NAMES.
    # TWO arms, deliberately.  The TOOL-NAME arm (NON_SDK_WRITER_BINDINGS) is what
    # survives the #4035 declaration blanking; the OP-KEYED arm below still fires
    # for any OTHER entry that *declares* a non-SDK writer op — the name-keyed arm
    # alone checked one named tool and let a second read-annotated declarer pass
    # both this function and the resolution exemption (#4035 review).
    by_name = {e.name: e for e in entries}
    for operation, tool_name in sorted(NON_SDK_WRITER_BINDINGS.items()):
        e = by_name.get(tool_name)
        if e is None:
            # liveness (the bound tool exists) is asserted by
            # declared_set_violations against the SERVED set; here `entries`
            # may be a synthetic subset, so a missing tool is skipped.
            continue
        if e.name not in _ms.WRITE_TOOL_NAMES or (
                e.annotations is None or e.annotations.readOnlyHint is not False):
            out.append(
                f"{e.name}: non-SDK writer operation {operation!r} not "
                f"writer-classified in WRITE_TOOL_NAMES")
    for operation in sorted(NON_SDK_WRITER_OPERATIONS):
        for e in by_method.get(operation, []):
            if e.name not in _ms.WRITE_TOOL_NAMES or (
                    e.annotations is None or e.annotations.readOnlyHint is not False):
                out.append(
                    f"{e.name}: non-SDK writer operation {operation!r} not "
                    f"writer-classified in WRITE_TOOL_NAMES")
    return out


def binding_resolution_violations(entries, mcp_src: str | None = None) -> list[str]:
    """Every declared sdk_method resolves AND IS REACHED, every entry has a
    handler, and no handler hides a call behind dynamic dispatch.

    Resolution alone is not enough (#4337): a name that exists on ``TortoiseSDK``
    can still be a name the handler never calls.  Declaring a method is a claim
    about what a tool does; reaching it is the fact.
    """
    methods = sdk_method_names()
    funcs = set(_module_level_funcs(_mcp_tree(mcp_src)))
    out: list[str] = []
    for e in entries:
        if e.sdk_method and e.sdk_method not in methods and \
                e.sdk_method not in DANGLING_SDK_DECLARATIONS and \
                e.sdk_method not in NON_SDK_WRITER_OPERATIONS:
            out.append(f"{e.name}: sdk_method {e.sdk_method!r} does not resolve")
        if e.name not in funcs:
            out.append(f"{e.name}: no module-level handler (register_all skips it)")
        for site in handler_operations(e.name, mcp_src).unresolved:
            out.append(f"{e.name}: {site}")
    # The declared binding must be REACHED, not merely resolvable (#4337).
    # Independent of the resolution arm above, and exact in both directions.
    for e in entries:
        # A LEDGERED entry is checked even when its declaration does not resolve:
        # otherwise a ledgered binding that drifts to an already-exempt dangling
        # name (or to empty) would be skipped here and pass the liveness arm too,
        # leaving a stale entry green — the exact case this ledger exists to close.
        recorded = DECLARED_BINDING_DIVERGENCES.get(e.name)
        if recorded is None and (not e.sdk_method or e.sdk_method not in methods):
            continue
        # TWO sets, deliberately.  PRESENCE of the declared method is asked of the
        # RAW reach set (a declared private SDK method is still a real binding —
        # filtering first would report it unreached and could never clear its
        # ledger entry).  The ledger's DESTINATION comparison uses the PUBLIC set,
        # because a private helper call (`_get_proj`) is not a binding claim: the
        # filter is load-bearing for `tortoise_traverse` (raw `{_get_proj}` vs
        # public `{}`) and a no-op for `tortoise_operator_action`.
        reached = frozenset(handler_operations(e.name, mcp_src).operations)
        public_reached = frozenset(x for x in reached if not x.startswith("_"))
        if e.name not in funcs:
            # no handler at all — the first arm reports that; asserting a missing
            # handler "never reaches" its declaration is noise, not a finding.
            continue
        if recorded is None:
            if e.sdk_method in reached:
                continue
            out.append(
                f"{e.name}: declares sdk_method {e.sdk_method!r} that its handler never "
                f"reaches (reaches: {sorted(public_reached) or 'nothing public'}) — #4337")
            continue
        # Ledgered: the ledger must still describe THIS divergence exactly.
        if e.sdk_method in reached:
            out.append(
                f"{e.name}: listed in DECLARED_BINDING_DIVERGENCES but its handler "
                f"now reaches {e.sdk_method!r} — delete the ledger entry")
        elif e.sdk_method != recorded.declared or public_reached != recorded.reached:
            out.append(
                f"{e.name}: DECLARED_BINDING_DIVERGENCES records declared="
                f"{recorded.declared!r} reached={sorted(recorded.reached)} but it is now "
                f"declared={e.sdk_method!r} reached={sorted(public_reached)} "
                f"— update the entry")
    # every guard-relevant operation a handler reaches must be bound to a tool
    guard_relevant = (sdk_graph_mutators() | sdk_operator_only_mutators()
                      | sdk_filesystem_methods())
    bound = {e.sdk_method for e in entries if e.sdk_method}
    for e in entries:
        for op in handler_operations(e.name, mcp_src).operations:
            if op.startswith("_"):
                continue
            if op in guard_relevant and op not in bound and \
                    op not in NON_SDK_WRITER_OPERATIONS:
                out.append(
                    f"{e.name}: reaches guard-relevant {op!r} with no tool binding")
    for op in sorted(UNBOUND_READ_OPERATIONS):
        if op in bound:
            out.append(f"{op}: declared an unbound read but is bound to a tool")
        if op in guard_relevant:
            out.append(f"{op}: declared an unbound read but is a write/privileged op")
    return out


def exemption_set_violations(entries) -> list[str]:
    """The tool-level non-HTTP exemption must be exactly NON_HTTP_WRITER_TOOLS."""
    import tortoise.mcp_server as _ms

    exempt = {
        e.name for e in entries
        if e.annotations is not None and e.annotations.readOnlyHint is False
        and e.name not in _ms.WRITE_TOOL_NAMES and e.http_policy is False
    }
    if exempt != set(NON_HTTP_WRITER_TOOLS):
        return [
            f"non-HTTP writer exemption set mismatch: got {sorted(exempt)} "
            f"expected {sorted(NON_HTTP_WRITER_TOOLS)}"]
    return []


def declared_set_violations(entries, sdk_src: str | None = None) -> list[str]:
    """Every declared set is live + exact: a stale entry fails loudly.

    Without this, renaming a declared operation (`dream`, `upsert_tenant_manifest`,
    an `INTERNAL_PATH_READERS` member) silently drops it from its check — the same
    name-goes-stale vacuity #4113 exists to remove.
    """
    methods = sdk_method_names(sdk_src)
    by_method: dict[str, list] = {}
    for e in entries:
        if e.sdk_method:
            by_method.setdefault(e.sdk_method, []).append(e)
    out: list[str] = []
    for op in sorted(HTTP_EXCLUDED_SDK_METHODS):
        if op not in methods:
            out.append(f"HTTP_EXCLUDED_SDK_METHODS entry {op!r} does not resolve")
        bound = by_method.get(op, [])
        if not bound:
            out.append(f"HTTP_EXCLUDED_SDK_METHODS entry {op!r} has no tool binding")
        for e in bound:
            if e.http_policy:
                out.append(f"{e.name}: declared HTTP-excluded operation {op!r} is HTTP-exposed")
    by_name = {e.name: e for e in entries}
    for op in sorted(NON_SDK_WRITER_OPERATIONS):
        bound_tool = NON_SDK_WRITER_BINDINGS.get(op)
        if bound_tool is None or bound_tool not in by_name:
            out.append(f"NON_SDK_WRITER_OPERATIONS entry {op!r} has no tool binding")
    for op in sorted(NON_SDK_READ_OPERATIONS):
        bound_tool = NON_SDK_READ_BINDINGS.get(op)
        if bound_tool is None or bound_tool not in by_name:
            out.append(f"NON_SDK_READ_OPERATIONS entry {op!r} has no tool binding")
    for op in sorted(READ_THROUGH_WRITE_METHODS):
        if op not in methods and op not in NON_SDK_WRITER_OPERATIONS \
                and op not in NON_SDK_READ_OPERATIONS \
                and op not in DANGLING_SDK_DECLARATIONS:
            out.append(f"READ_THROUGH_WRITE_METHODS entry {op!r} does not resolve")
        for e in by_method.get(op, []):
            if not e.http_policy or e.annotations is None \
                    or e.annotations.readOnlyHint is not True:
                out.append(
                    f"{e.name}: READ_THROUGH_WRITE_METHODS entry {op!r} bound to a "
                    f"non-read HTTP tool")
        # #4035 split: a read-through op carried by a handler-served tool has no
        # `sdk_method` declaration left for the `by_method` loop above to key on,
        # so it becomes UNREACHABLE and the read-classification property stops
        # being checked at all.  Assert it per TOOL NAME as well — the same repair
        # NON_SDK_WRITER_BINDINGS already carries for the writer half.  This is an
        # ADDITIONAL arm, never a replacement: the op-keyed loop still covers every
        # entry that does declare the op (compute_confidence / get_confidence).
        bound_tool = NON_SDK_READ_BINDINGS.get(op)
        if bound_tool is None:
            continue
        e = by_name.get(bound_tool)
        if e is None:
            continue  # liveness ('has no tool binding') is asserted above
        if not e.http_policy or e.annotations is None \
                or e.annotations.readOnlyHint is not True:
            out.append(
                f"{bound_tool}: READ_THROUGH_WRITE_METHODS entry {op!r} bound to a "
                f"non-read HTTP tool")
    for op in sorted(INTERNAL_PATH_READERS):
        if op not in methods:
            out.append(f"INTERNAL_PATH_READERS entry {op!r} does not resolve")
    # DECLARED_BINDING_DIVERGENCES liveness (#4337).  The divergence arm iterates
    # the ENTRIES, so a ledger key whose registry entry was removed or renamed is
    # never visited and would persist unexamined.  This function is the home for
    # declared-set liveness, and the ledger arm follows the SAME subset semantics
    # the binding arms here already have: the ledger is compared against whatever
    # `entries` it is handed, so a caller passing a filtered registry sees the
    # same treatment for the ledger as for any other declared set.  (Not every arm
    # below is subset-sensitive — `INTERNAL_PATH_READERS` checks only resolution —
    # but `READ_THROUGH_WRITE_METHODS` asks for a tool binding in BOTH the
    # `by_method` and the `NON_SDK_READ_BINDINGS` arms, so it is subset-sensitive
    # too, and a filtered caller is expected to know it passed a filtered set.)
    # The divergence arm cannot host
    # this check: it is also called on probe SUBSETS, where the real ledger keys
    # are legitimately absent.
    known = {e.name for e in entries}
    for name in sorted(set(DECLARED_BINDING_DIVERGENCES) - known):
        out.append(
            f"DECLARED_BINDING_DIVERGENCES entry {name!r} has no registry entry "
            f"— delete the ledger entry")
    return out


def wrap_site_violations(mcp_src: str | None = None) -> list[str]:
    """Every _quota_gated site resolves; the weight partition is exact; every
    wrapped method is bound to a tool."""
    sites = quota_gated_wrap_sites(mcp_src)
    by_method = registry_entries_by_method()
    out: list[str] = list(sites.unresolved)
    for method, lineno, has_weight in sites.sites:
        if (method in WEIGHT_BEARING_METHODS) != has_weight:
            out.append(f"_quota_gated({method})@{lineno}: abuse_weight={has_weight}")
    wrapped = {s[0] for s in sites.sites}
    for method in sorted(wrapped - set(by_method)):
        out.append(f"wrap site for {method!r} has no registry binding")
    return out


def write_surface_map_violations(method_to_tool: dict[str, str] | None = None,
                                 entries=None, mcp_src: str | None = None) -> list[str]:
    """The declared write-surface map is live BOTH ways: every wrap site is
    mapped (forward), every mapped method still has a wrap site (the inverse),
    every mapped name resolves, and every wrap site's tool is write-classified.
    A stale entry fails LOUDLY — never silently stops being checked."""
    import tortoise.mcp_server as _ms

    mapping = DECLARED_WRITE_SURFACE_MAP if method_to_tool is None else method_to_tool
    # Served set (#3883): the default must cover retired entries too, or a retired
    # write tool's binding silently stops being checked.
    entries = list(entries) if entries is not None else list(served_registry())
    wrapped = {m for m, _, _ in quota_gated_wrap_sites(mcp_src).sites}
    out: list[str] = []
    forward = wrapped - set(mapping)
    if forward:
        out.append(f"unmapped _quota_gated wrap sites: {sorted(forward)}")
    inverse = set(mapping) - wrapped
    if inverse:
        out.append(f"declared write methods with no wrap site (stale map): {sorted(inverse)}")
    live = {e.name for e in entries}
    dead = set(mapping.values()) - live
    if dead:
        out.append(f"write-surface map names non-existent tools (update the map): {sorted(dead)}")
    missing = {mapping[m] for m in (wrapped & set(mapping))} - _ms.WRITE_TOOL_NAMES
    if missing:
        out.append(f"write tools missing from WRITE_TOOL_NAMES: {sorted(missing)}")
    return out
