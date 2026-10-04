"""#7014 / #5718 — the SYMMETRIC heavy-import guard.

The race being guarded (mechanism documented in ``tortoise/heavy_imports.py``) is
symmetric: a cold ``sklearn``/``scipy`` import must not overlap an in-flight
``torch`` import — and a torch import must not overlap a cold scipy one. A
one-sided tripwire (torch-only, or sklearn-only) cannot see the mirror case,
which is exactly why the residual claim on #7015 sent a reviewer looking. So this
guard checks BOTH families against ONE rule:

    every ``torch`` / ``sentence_transformers`` / ``transformers`` / ``scipy`` /
    ``sklearn`` import in the ``tortoise`` package must be taken under the shared
    heavy-import lock, and that lock is only ever taken in
    ``tortoise/heavy_imports.py`` — the sanctioned set of lock-taking helpers.

This is an ALLOW-LIST of the helpers, not a denylist of words: the only thing it
can ever flag is a heavy-dependency import, which is never innocent in library
code. Scope is the library package (``tortoise/``) on purpose. Non-library
harness/tool sites that still import these modules directly are DISCLOSED on
#7015, not gated here — gating them would fire on legitimate test/tool code and
the guard would be deleted rather than fixed.

The check is a source parse (AST), not a regex: it sees ``from scipy import
sparse`` and ``import torch`` the same way, and it also catches the dynamic
``importlib.import_module("...")`` / ``__import__("...")`` forms a regex on
statements would miss.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TORTOISE = _REPO_ROOT / "tortoise"
_LEAF_MODULE = "tortoise/heavy_imports.py"

#: The racing families, by top-level module name -> human-readable family. A cold
#: member of either family must not overlap an in-flight member of the other.
_HEAVY_ROOTS = {
    "torch": "torch",
    "sentence_transformers": "torch",
    "transformers": "torch",
    "scipy": "scipy/sklearn",
    "sklearn": "scipy/sklearn",
}

#: The ONE shared lock. Only this name may guard a heavy import.
_LOCK_NAMES = {"_HEAVY_IMPORT_LOCK"}


def _top(module: str | None) -> str:
    return (module or "").split(".")[0]


def _is_lock_ctx(expr: ast.expr) -> bool:
    """True for ``_HEAVY_IMPORT_LOCK`` (a bare name or an attribute)."""
    if isinstance(expr, ast.Call):
        expr = expr.func
    if isinstance(expr, ast.Name):
        return expr.id in _LOCK_NAMES
    if isinstance(expr, ast.Attribute):
        return expr.attr in _LOCK_NAMES
    return False


class _Scan(ast.NodeVisitor):
    """Collect every heavy import plus whether a heavy-lock ``with`` encloses it.

    The ``with``-item may be ``with _HEAVY_IMPORT_LOCK:`` in any namespace — the
    name is what is checked, and the second test pins that namespace to the one
    leaf module, so the two together are exact.
    """

    def __init__(self) -> None:
        self._lock_depth = 0
        #: (lineno, module, locked)
        self.heavy: list[tuple[int, str, bool]] = []

    def visit_With(self, node: ast.With) -> None:
        locked = any(_is_lock_ctx(item.context_expr) for item in node.items)
        self._lock_depth += int(locked)
        self.generic_visit(node)
        self._lock_depth -= int(locked)

    visit_AsyncWith = visit_With

    def _record(self, node: ast.AST, module: str | None) -> None:
        if _top(module) in _HEAVY_ROOTS:
            self.heavy.append((node.lineno, module or "", self._lock_depth > 0))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(node, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._record(node, node.module)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        dynamic = (
            isinstance(func, ast.Attribute) and func.attr == "import_module"
        ) or (isinstance(func, ast.Name) and func.id == "__import__")
        if dynamic and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                self._record(node, arg0.value)
        self.generic_visit(node)


def _scan_package() -> list[tuple[str, int, str, bool]]:
    rows: list[tuple[str, int, str, bool]] = []
    py_files = sorted(_TORTOISE.rglob("*.py"))
    assert py_files, f"no Python files under {_TORTOISE} — wrong checkout?"
    for path in py_files:
        rel = path.relative_to(_REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        scan = _Scan()
        scan.visit(tree)
        rows.extend((rel, lineno, module, locked) for lineno, module, locked in scan.heavy)
    return rows


def _report(rows: list[tuple[str, int, str, bool]]) -> str:
    return "\n".join(f"  {rel}:{lineno}  {module}" for rel, lineno, module, _ in rows)


def test_the_scan_actually_sees_the_heavy_imports():
    """A missing check is not a green check: prove the scan is not vacuous.

    If this fails, the guard found nothing to check — fix the guard, do not
    interpret the empty result as compliance.
    """
    rows = _scan_package()
    assert rows, "heavy-import scan found NOTHING in tortoise/ — the guard is broken"


def test_no_heavy_import_runs_outside_the_shared_lock():
    """#7014: neither family may be imported without the one shared lock.

    This is the symmetric half of the guard: it fails on an unlocked cold
    sklearn/scipy import exactly as it fails on an unlocked torch import.
    """
    unlocked = [r for r in _scan_package() if not r[3]]
    assert not unlocked, (
        "heavy import(s) outside the shared heavy-import lock re-open the #5718 "
        "torch ↔ sklearn/scipy race (the exact pair is irrelevant — either one "
        "mid-import breaks the other). Route them through a lock-taking helper in "
        f"{_LEAF_MODULE}:\n" + _report(unlocked)
    )


def test_heavy_imports_live_in_the_one_leaf_module():
    """The sanctioned set is the helpers in ``tortoise/heavy_imports.py``.

    That module is a leaf (imports only ``threading``), so the lock can never be
    caught in an import cycle. A heavy import anywhere else — even a correctly
    locked one — moves the lock's import surface back into the package graph and
    re-opens that risk, so this pins the single home.
    """
    off_home = [r for r in _scan_package() if r[0] != _LEAF_MODULE]
    assert not off_home, (
        f"heavy imports must live only in {_LEAF_MODULE} (the one sanctioned home "
        "for the shared lock — a leaf module, so the lock cannot become part of an "
        "import cycle). Call a helper from there instead:\n" + _report(off_home)
    )
