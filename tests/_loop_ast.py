"""Shared AST walkers for the on-loop / off-loop guards (#3718, #4625).

``test_read_routes_loop_responsiveness`` (#3718) and
``test_health_ready_nonblocking`` (#4625) both have to answer the same
question about a locally defined ``def``: does its body run ON the event
loop? The rule — from the #4455 review of ``patch_onboarding_state``'s
``_read_node`` closure — is that a nested def is on the loop only when
something INVOKES it by a bare call; one merely handed to an offload
boundary as a callable REFERENCE runs in the worker and must not be scanned.

The walkers live here ONCE so the two guards cannot drift apart. Guard A was
blind to exactly the case Guard B handled: it skipped every nested body
(a blocking call "belongs to that function's own inventory entry") while its
reachability closure walked only module-level ``tree.body`` — and a LOCAL
function has no inventory entry — so a blocking call inside a closure the
route invokes went unreported (#4625 review F1).
"""
from __future__ import annotations

import ast


def callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def offload_boundary_eager_children(call: ast.Call):
    """Arguments of an offload-boundary call that are evaluated EAGERLY.

    A callable REFERENCE (Lambda / Name / Attribute) is handed to the worker
    and skipped — at ANY position, since the callable sits at index 0 for
    ``to_thread`` but at index 1 for ``run_in_executor`` / ``_run_with_close``.
    Every other argument (a Call, a comprehension, ...) is evaluated on the
    loop, so the caller must still scan it.
    """
    for arg in call.args:
        if isinstance(arg, (ast.Lambda, ast.Name, ast.Attribute)):
            continue
        yield arg
    for kw in call.keywords:
        if isinstance(kw.value, (ast.Lambda, ast.Name, ast.Attribute)):
            continue
        yield kw.value


def direct_nested_invocations(statements, nested_names: set[str],
                              descend: set[str],
                              boundaries: frozenset[str]) -> set[str]:
    """Nested-def names INVOKED (``name(...)``) on the loop.

    Only a bare CALL is an invocation: a callable REFERENCE handed to an
    offload boundary (``asyncio.to_thread(_read)``) runs in the worker and is
    not one — which is what keeps an off-loaded closure out of the scan. The
    walk descends into the bodies of nested defs already known to run on the
    loop (``descend``) so a chain of nested invocations is followed.
    """
    found: set[str] = set()

    def walk(current: ast.AST) -> None:
        if isinstance(current, ast.Call):
            if callee_name(current.func) in boundaries:
                for child in offload_boundary_eager_children(current):
                    walk(child)
                return
            name = callee_name(current.func)
            if name in nested_names:
                found.add(name)
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                if getattr(child, "name", None) in descend:
                    for stmt in child.body:
                        walk(stmt)
                continue
            walk(child)

    for stmt in statements:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if getattr(stmt, "name", None) in descend:
                for inner in stmt.body:
                    walk(inner)
            continue
        walk(stmt)
    return found


def nested_defs_invoked_on_loop(node, nested_names: set[str],
                                boundaries: frozenset[str]) -> set[str]:
    """Nested defs of ``node`` whose body RUNS ON THE LOOP (fixpoint).

    The route body runs on the loop, so a nested def it invokes by name runs
    there too — and so do the defs that one invokes. A def reached only as a
    callable REFERENCE to an offload boundary never enters the set.
    """
    on_loop: set[str] = set()
    while True:
        invoked = direct_nested_invocations(
            node.body, nested_names, on_loop, boundaries)
        newly = invoked - on_loop
        if not newly:
            return on_loop
        on_loop |= newly
