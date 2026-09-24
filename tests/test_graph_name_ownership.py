"""#3634 Task 1: the ONE graph-name ownership vocabulary.

`tests/_embedded.py` expresses the test-graph prefix vocabulary in several
copies. The ownership semantics are NOT uniform — a prefix is not ownership; a
journal record is — so this file pins the distinctions that keep the narrower
copies narrow:

* `_SERVER_WIPE_PREFIXES` (input: ``GRAPH.LIST``, no attribution) must stay a
  strict SUBSET of `_SWEEP_OWNED_PREFIXES` (input: the journal);
* the opt-in residue set must be disjoint from every owned family, so an
  opted-in reclamation can never become a third copy of ``wipe_server``;
* the residue predicate is deny-safe (non-``str``, the URI default, snapshots,
  and every owned family are refused);
* every NAMED prefix constant on the declared surface is tied to the register
  BY OBJECT IDENTITY, and a new named constant fails this file;
* the AST pin for AC3 — the opt-in legacy sweep (Task 3) has NO default call
  site.

HERMETIC — no DB, no lane, no fixture.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

from tests._embedded import (  # noqa: E402
    _DIVERGENCE_REGISTER,
    _LEGACY_RESIDUE_PREFIXES,
    _PRODUCT_GRAPH_PREFIXES,
    _SERVER_WIPE_PREFIXES,
    _SWEEP_OWNED_PREFIXES,
    is_legacy_residue,
)


def test_the_register_is_tied_to_the_real_symbols():
    """The register is only an index if it points at the LIVE objects.

    An identical-but-redeclared tuple (`is` False) is exactly the drift this
    register exists to catch, so the tie is asserted by object identity.
    """
    for name, symbol in (
        ("_SWEEP_OWNED_PREFIXES", _SWEEP_OWNED_PREFIXES),
        ("_SERVER_WIPE_PREFIXES", _SERVER_WIPE_PREFIXES),
        ("_PRODUCT_GRAPH_PREFIXES", _PRODUCT_GRAPH_PREFIXES),
        ("_LEGACY_RESIDUE_PREFIXES", _LEGACY_RESIDUE_PREFIXES),
    ):
        assert _DIVERGENCE_REGISTER[name]["set"] is symbol, name
    for name, entry in _DIVERGENCE_REGISTER.items():
        assert entry["reason"].strip(), f"{name} registered without a reason"


def test_the_wipe_literal_is_a_subset_of_the_journal_set():
    """#7795: GRAPH.LIST authorises strictly less than the journal does.

    Strictness matters — equality would mean the journal-blind global sweep
    had grown to the journal's own reach (product namespaces included).
    """
    assert set(_SERVER_WIPE_PREFIXES) < set(_SWEEP_OWNED_PREFIXES)


def test_residue_is_disjoint_from_every_owned_family():
    """Overlap would make the residue pass a third copy of wipe_server."""
    assert not set(_LEGACY_RESIDUE_PREFIXES) & set(_SWEEP_OWNED_PREFIXES)


def test_the_residue_predicate_is_deny_safe():
    """Every reason to refuse must refuse, and a non-str must not raise."""
    for name in ("registry_tortoise", "registry_control_plane", "test_a",
                 "tortoise_test_matrix", "tortoise_restored_20260101",
                 "org_x", "team_y", "totally_unrelated"):
        assert not is_legacy_residue(name, default_graph="tortoise_test_matrix"), name
    assert not is_legacy_residue(None, default_graph=None)     # non-str must not raise


def test_a_sixth_copy_of_the_vocabulary_fails_here():
    """AC1's enforcement, SCOPE: NAMED prefix constants on the declared surface.

    The declared surface is `tests/_embedded.py`, `tortoise/sdk.py`, and
    `tortoise/projection/__init__.py`. Anonymous literals
    (`startswith(("test_", ...))` in `tortoise/sdk.py`,
    `tests/test_derived_names.py`, `tests/test_pre_migration_safety.py`) are
    NOT covered — they remain governed by the DIVERGENCE comment at the
    declaration. Do not claim a guarantee broader than this scanner.
    """
    seen = set()
    for rel in ("tests/_embedded.py", "tortoise/sdk.py",
                "tortoise/projection/__init__.py"):
        src = (REPO / rel).read_text()
        for m in re.finditer(r"^(_?[A-Z_]*PREFIXES)\s*[:=]", src, re.M):
            seen.add(m.group(1))
    unregistered = {n for n in seen if n not in _DIVERGENCE_REGISTER}
    assert unregistered == set(), f"prefix constant(s) not in the register: {unregistered}"


# ── AC3 AST pin (Task 1 establishes the helper; Task 3 wires it up) ────────
_DEFAULT_PATH_FILES = ("tests/_embedded.py", "tests/conftest.py",
                       "tests/test_tripwire.py")
# Expected call sites: NONE. The guard fails loudly if this set changes
# (the tests/test_write_ahead_mint.py `_MINT_SITES` contract).
_LEGACY_CALL_SITES: set[tuple[str, int]] = set()

_LEGACY_SWEEP_NAME = "_sweep_legacy_strays"


def _calls_in(path: Path) -> list[int]:
    """Line numbers of every Call to the legacy sweep in ``path``.

    A bare ``ast.Call`` has no parent link, so the enclosing definition cannot
    be recovered from ``ast.walk`` alone. This mirrors
    ``tests/test_write_ahead_mint.py``'s ``_collect_sites``: a RECURSIVE walk
    that carries the innermost definition's name down the tree. Matches both a
    bare ``Name`` (``_sweep_legacy_strays(...)``) and an ``Attribute``
    (``_embedded._sweep_legacy_strays(...)``).
    """
    tree = ast.parse(path.read_text())
    found: list[int] = []

    def walk(node: ast.AST, def_name: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
                continue
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, ast.Call):
                f = child.func
                if ((isinstance(f, ast.Name) and f.id == _LEGACY_SWEEP_NAME)
                        or (isinstance(f, ast.Attribute)
                            and f.attr == _LEGACY_SWEEP_NAME)):
                    found.append(child.lineno)
            walk(child, def_name)

    walk(tree, None)
    return found


def test_legacy_sweep_has_no_default_call_site():
    """AC3: ``_sweep_legacy_strays`` must have NO default call site.

    SCOPE: this scanner covers the three files in ``_DEFAULT_PATH_FILES``
    only. A default call site in any other module is NOT caught — widen that
    tuple deliberately if the teardown paths change.
    """
    found = {(rel, lineno) for rel in _DEFAULT_PATH_FILES
             for lineno in _calls_in(REPO / rel)}
    assert found == _LEGACY_CALL_SITES, f"wired into a default path: {sorted(found)}"
