"""#4096 — class-wide guard: no pytest fixture in ``tests/`` may leak a mkdtemp tree.

`tests/test_validity_windows.py` `mkdtemp`-ed a tree per test and its finalizer only
closed the SDK, so 5,725 `tortoise_validity_test_*` dirs accumulated on the dev box
(and 52 sibling fixtures leaked the same way across 47 files — 49 direct, 2 that
create their tree in a same-module helper, plus `conftest.py::test_user`, flagged
transitively because it depends on the `provision_test_user` fixture).

This is the regression guard for the class: an AST scan over ``tests/`` that fails
if any ``@pytest.fixture`` reaches ``tempfile.mkdtemp`` without also reaching a
reclaim primitive — ``rmtree``, ``TemporaryDirectory``, ``register_session_tmpdir``,
or ``reclaim_tmpdirs`` (the session-deferred path in ``tests/_embedded.py``; the
session-scoped shared trees are reclaimed at the very end of the run by
``conftest.py::_reclaim_session_tmpdirs`` rather than locally, because the hygiene
sweeps need their socket/pid evidence).

Fixture roots are collected with ``ast.walk``, so fixtures nested in a ``class``
(or any other body) are covered, not just module-level ``def``s. Call chains are
resolved against every function in the module (module-level, class-body, nested),
so a fixture that creates or reclaims its tree through a class-body helper is
resolved too. Resolution is BY NAME, so a same-named helper in an unrelated scope
can satisfy the reclaim check — a false negative, not a false positive.

Known gap, documented rather than silent: cross-module helper resolution is not
implemented — a fixture whose tree is created by a helper imported from another
module would not be detected. No such fixture exists today (the apparent hits are
name collisions with product helpers); if one appears, resolve the import to its
defining module here.

The scanner's discriminating power is itself pinned by
``test_guard_has_positive_and_negative_controls`` — without it the suite-wide
assertion could pass vacuously if the scan silently stopped finding anything.
"""
from __future__ import annotations

import ast
import os
import pathlib
import tempfile

TESTS_DIR = pathlib.Path(__file__).resolve().parent
SELF = pathlib.Path(__file__).resolve()

# Primitives that (together) constitute "this fixture's tree is reclaimed".
RECLAIM_CALLS = {"rmtree", "register_session_tmpdir", "reclaim_tmpdirs"}
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


def scan_source(src: str, label: str) -> list[str]:
    """Return ``<label>:<line> <fixture>`` for every leaking fixture in ``src``."""
    tree = ast.parse(src, filename=label)
    # Index every function (module-level, class-body, nested) so a fixture that
    # creates or reclaims its tree in a class-body helper resolves too.
    module_funcs: dict[str, ast.AST] = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module_funcs.setdefault(n.name, n)
    out: list[str] = []
    for node in ast.walk(tree):
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
            out.append(f"{label}:{node.lineno} {node.name}")
    return out


def offenders_in(path: pathlib.Path) -> list[str]:
    """Scan one file; ``label`` is the repo-relative path for readable output."""
    return scan_source(path.read_text(),
                       str(path.relative_to(TESTS_DIR.parent)))


def test_no_pytest_fixture_leaks_a_temp_tree():
    offenders: list[str] = []
    scanned = 0
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.resolve() == SELF:
            continue
        scanned += 1
        offenders.extend(offenders_in(path))
    # A silent scan regression (bad rglob root, broken filter) would otherwise
    # turn this guard into a permanent green no-op.
    assert scanned > 100, f"guard scanned only {scanned} files — vacuous guard"
    assert offenders == [], (
        "#4096: pytest fixtures that mkdtemp a tree and never reclaim it:\n  "
        + "\n  ".join(offenders)
    )


# ── scanner controls ─────────────────────────────────────────────────────

_LEAKY = '''
import tempfile
import pytest

@pytest.fixture
def leaky():
    tempfile.mkdtemp(prefix="x_")
'''

_LEAKY_CLASS_NESTED = '''
import tempfile
import pytest

class TestX:
    @pytest.fixture
    def leaky(self):
        tempfile.mkdtemp(prefix="x_")
'''

_LEAKY_VIA_HELPER = '''
import tempfile
import pytest

def _mk():
    return tempfile.mkdtemp(prefix="x_")

@pytest.fixture
def leaky():
    yield _mk()
'''

_RECLAIMED = '''
import shutil
import tempfile
import pytest

@pytest.fixture
def ok():
    d = tempfile.mkdtemp(prefix="x_")
    yield d
    shutil.rmtree(d, ignore_errors=True)
'''

_RECLAIMED_VIA_HELPER = '''
import shutil
import tempfile
import pytest

def _mk():
    return tempfile.mkdtemp(prefix="x_")

@pytest.fixture
def ok():
    d = _mk()
    yield d
    shutil.rmtree(d, ignore_errors=True)
'''


def test_guard_has_positive_and_negative_controls():
    """The scanner must flag leaking fixtures — module-level, class-nested, and
    helper-mediated — and must NOT flag reclaiming ones. Without this control the
    suite-wide assertion above could pass while detecting nothing."""
    assert scan_source(_LEAKY, "leaky.py")
    assert scan_source(_LEAKY_CLASS_NESTED, "leaky_class.py")
    assert scan_source(_LEAKY_VIA_HELPER, "leaky_helper.py")
    assert scan_source(_RECLAIMED, "reclaimed.py") == []
    assert scan_source(_RECLAIMED_VIA_HELPER, "reclaimed_helper.py") == []


# ── the session-scoped reclamation path ──────────────────────────────────

def test_reclaim_tmpdirs_removes_the_tree():
    """The primitive `_reclaim_session_tmpdirs` uses must actually delete."""
    from tests._embedded import reclaim_tmpdirs
    d = tempfile.mkdtemp(prefix="tortoise_reclaim_test_")
    assert os.path.isdir(d)
    assert reclaim_tmpdirs([d]) == 1
    assert not os.path.exists(d)


def test_drain_session_tmpdirs_removes_and_clears(monkeypatch):
    """`_reclaim_session_tmpdirs`'s body must remove the registered trees AND
    empty the registry — otherwise a session tree leaks and a later drain
    re-removes a stale path."""
    from tests import _embedded
    d = tempfile.mkdtemp(prefix="tortoise_reclaim_test_")
    monkeypatch.setattr(_embedded, "SESSION_TMPDIRS", [d])
    assert _embedded.drain_session_tmpdirs() == 1
    assert not os.path.exists(d)
    assert _embedded.SESSION_TMPDIRS == []


def test_shared_embedded_db_registers_its_tree(shared_embedded_db):
    """#4096: the session-scoped shared tree must be registered, so the
    end-of-session reclaimer removes it — it cannot reclaim locally, because the
    hygiene sweeps read socket/pid evidence *inside* it."""
    from tests import _embedded
    tree = os.path.dirname(shared_embedded_db)
    assert os.path.isdir(tree)
    assert tree in _embedded.SESSION_TMPDIRS
