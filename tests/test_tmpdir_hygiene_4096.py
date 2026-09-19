"""#4096 — class-wide guard: no pytest fixture in ``tests/`` may leak a mkdtemp tree.

`tests/test_validity_windows.py` `mkdtemp`-ed a tree per test and its finalizer only
closed the SDK, so 5,725 `tortoise_validity_test_*` dirs accumulated on the dev box
(and 51 sibling fixtures leaked the same way across 47 files, including two that
create their tree in a same-module helper).

This is the regression guard for the class: an AST scan over ``tests/`` that fails
if any ``@pytest.fixture`` reaches ``tempfile.mkdtemp`` without also reaching a
reclaim primitive — ``rmtree``, ``TemporaryDirectory``, or the session-deferred
``register_session_tmpdir`` (``tests/_embedded.py``; the session-scoped shared trees
are reclaimed at the very end of the run by ``conftest.py::_reclaim_session_tmpdirs``
rather than locally, because the hygiene sweeps need their socket/pid evidence).

Same-module call chains are resolved (a fixture is clean if a helper it calls
reclaims), including nested ``def``s.

Known gap, documented rather than silent: cross-module helper resolution is not
implemented — a fixture whose tree is created by a helper imported from another
module would not be detected. No such fixture exists today; if one appears, resolve
the import to its defining module here.
"""
from __future__ import annotations

import ast
import pathlib

TESTS_DIR = pathlib.Path(__file__).resolve().parent
SELF = pathlib.Path(__file__).resolve()

# Primitives that (together) constitute "this fixture's tree is reclaimed".
RECLAIM_CALLS = {"rmtree", "register_session_tmpdir"}
RECLAIM_NAMES = {"TemporaryDirectory"}
_CREATE_CALLS = {"mkdtemp"}


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _is_fixture(node: ast.AST) -> bool:
    for d in getattr(node, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
    return False


def _calls_any(node: ast.AST, names: set[str]) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, (ast.Name, ast.Attribute)) and (
                    (f.id if isinstance(f, ast.Name) else f.attr) in names):
                return True
    return False


def _references_any(node: ast.AST, names: set[str]) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in names:
            return True
        if isinstance(n, ast.Attribute) and n.attr in names:
            return True
    return False


def offenders_in(path: pathlib.Path) -> list[str]:
    """Return ``<path>:<line> <fixture>`` for every leaking fixture in one file."""
    src = path.read_text()
    tree = ast.parse(src, filename=str(path))
    module_funcs = {
        n.name: n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    out: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_fixture(node):
            continue
        seen: set[int] = set()
        stack = [node]
        creates = False
        reclaims = False
        while stack:
            fn = stack.pop()
            if id(fn) in seen:
                continue
            seen.add(id(fn))
            if _calls_any(fn, _CREATE_CALLS):
                creates = True
            if _calls_any(fn, RECLAIM_CALLS) or _references_any(fn, RECLAIM_NAMES):
                reclaims = True
            for name in _called_names(fn):
                callee = module_funcs.get(name)
                if callee is not None:
                    stack.append(callee)
        if creates and not reclaims:
            out.append(f"{path.relative_to(TESTS_DIR.parent)}:{node.lineno} {node.name}")
    return out


def test_no_pytest_fixture_leaks_a_temp_tree():
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.resolve() == SELF:
            continue
        offenders.extend(offenders_in(path))
    assert offenders == [], (
        "#4096: pytest fixtures that mkdtemp a tree and never reclaim it:\n  "
        + "\n  ".join(offenders)
    )
