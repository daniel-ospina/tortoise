"""#3634 Task 1: the ONE graph-name ownership vocabulary.

`tests/_embedded.py` expresses the test-graph prefix vocabulary in several
copies. The ownership semantics are NOT uniform — a prefix is not ownership; a
journal record is — so this file pins the distinctions that keep the narrower
copies narrow:

* `_SERVER_WIPE_PREFIXES` (input: ``GRAPH.LIST``, no attribution) must stay a
  strict SUBSET of `_SWEEP_OWNED_PREFIXES` (input: the journal);
* the opt-in residue set must be disjoint from every owned family, so an
  opted-in reclamation can never become a third copy of ``wipe_server``;
* the residue predicate is deny-safe (non-``str``, the literal ``tortoise``,
  the URI default, snapshots, and every owned family are refused), and its
  APPROVE side is pinned for every declared residue family;
* every NAMED prefix constant on the declared surface is tied to the register
  BY OBJECT IDENTITY, and a new named constant fails this file;
* an OFF-SURFACE consumer of the vocabulary (`tests/_live_utils.py`) imports
  the canonical symbol instead of restating it, and its gate consults the LIVE
  canonical set — a narrowing of `_SERVER_WIPE_PREFIXES` must narrow it too;
* the AST pin for AC3 — the opt-in legacy sweep (Task 3) has NO default call
  site.

HERMETIC — no DB, no lane, no fixture.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import tests._embedded as embedded

REPO = Path(__file__).resolve().parent.parent

from tests import _live_utils  # noqa: E402
from tests._embedded import (  # noqa: E402
    _DIVERGENCE_REGISTER,
    _LEGACY_RESIDUE_PREFIXES,
    _SERVER_WIPE_PREFIXES,
    _SWEEP_OWNED_PREFIXES,
    is_legacy_residue,
)

# The exact #3634 census cohort, held as a GOLDEN list. A loop over the live
# tuple cannot detect its own shrinkage, so this pins the tuple's CONTENT: 18
# of the 26 literals below are otherwise unpinned (the deny-safe test's approve
# loop exercises one name per family), and each could be deleted with the suite
# still green. Order matches the declaration in `tests/_embedded.py`.
EXPECTED_RESIDUE_PREFIXES = (
    "registry_test_",
    "v10fix_c0", "v10fix_c1", "v10fix_c2", "v10fix_c3", "v10fix_c4",
    "v10fix_c5", "v10fix_c6", "v10fix_c7", "v10fix_c8", "v10fix_c9",
    "v10_smoke",
    "ttm_a1", "ttm_a1_fresh1", "ttm_a1_fresh2", "ttm_a3",
    "ttm_batch1", "ttm_batch2", "ttm_batch3", "ttm_batch4", "ttm_wave2",
    "review_rw_probe",
    "askshape_b6_live_1_33760_21",
    "legbudget_25979_txrx",
    "tt4524_probe",
    "probe_d10_doc_fts",
)


def test_the_register_is_tied_to_the_real_symbols():
    """The register is only an index if it points at the LIVE objects.

    An identical-but-redeclared tuple (`is` False) is exactly the drift this
    register exists to catch, so the tie is asserted by object identity.
    """
    for name, entry in _DIVERGENCE_REGISTER.items():
        assert entry["set"] is getattr(embedded, name), name
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
    assert not is_legacy_residue("tortoise", default_graph="tortoise_test_matrix")
    # The APPROVE side, pinned for EVERY declared residue family — without
    # this, `return False` would satisfy every refusal above.
    for n in ("registry_test_c_control_plane", "v10fix_c1", "ttm_a1",
              "review_rw_probe", "askshape_b6_live_1_33760_21",
              "legbudget_25979_txrx", "tt4524_probe", "probe_d10_doc_fts"):
        assert is_legacy_residue(n, default_graph="tortoise_test_matrix"), n


def test_the_residue_census_cohort_is_exact():
    """The declared cohort equals the golden list, and every name approves.

    `EXPECTED_RESIDUE_PREFIXES` is a hand-copy of the implementation tuple, so
    the set equality below pins COPY-vs-CODE, not census-vs-code: it catches a
    literal being dropped from or added to the declaration, and it does NOT by
    itself prove every entry is real (that provenance comes from the archived
    census on #3634). A loop over the live tuple cannot detect its own
    shrinkage, so the equality is what pins the CONTENT here, and the per-name
    approve assertion pins that each literal is actually reachable through
    `is_legacy_residue`.
    """
    assert set(_LEGACY_RESIDUE_PREFIXES) == set(EXPECTED_RESIDUE_PREFIXES)
    for n in EXPECTED_RESIDUE_PREFIXES:
        assert is_legacy_residue(n, default_graph=None), n


def test_the_owned_and_production_refusals_fire_when_a_residue_prefix_overlaps(monkeypatch):
    """The owned and literal-production refusals are load-bearing, not incidental.

    `test_owned_name` and `tortoise` are both refused today because no residue
    prefix matches them — with or without the guard, since the residue
    fall-through also refuses them. An assertion that does not force an
    overlap therefore cannot tell whether the guard fired. Patch an
    OVERLAPPING prefix onto the module (importing the module as an object so
    the patch is visible to `is_legacy_residue`) to pin each guard itself.
    """
    monkeypatch.setattr(embedded, "_LEGACY_RESIDUE_PREFIXES", ("test_",))
    assert not embedded.is_legacy_residue("test_owned_name", default_graph=None)
    monkeypatch.setattr(embedded, "_LEGACY_RESIDUE_PREFIXES", ("tortoise",))
    assert not embedded.is_legacy_residue("tortoise", default_graph=None)


def test_the_tortoise_restored_guard_fires_when_a_residue_prefix_overlaps(monkeypatch):
    """The `tortoise_restored` refusal is load-bearing, not incidental.

    `tortoise_restored_20260101` is refused today because no residue prefix
    matches it — not because the guard fired. Pin the guard itself by
    monkeypatching an OVERLAPPING prefix onto the module: import the module as
    an object (not the function name) so the patch is visible to
    `is_legacy_residue`.
    """
    monkeypatch.setattr(embedded, "_LEGACY_RESIDUE_PREFIXES",
                        ("tortoise_restored",))
    assert not embedded.is_legacy_residue("tortoise_restored_20260101",
                                          default_graph=None)


def test_the_default_graph_refusal_is_isolated_from_the_owned_refusal():
    """Pin the `default_graph` branch with a name that is residue and NOT owned.

    `tortoise_test_matrix` is ALSO refused by the ownership branch, so the
    `default_graph` branch is unreachable with it. `registry_test_shared` is
    residue and unowned, isolating the branch.
    """
    assert not is_legacy_residue("registry_test_shared",
                                 default_graph="registry_test_shared")
    assert is_legacy_residue("registry_test_shared", default_graph=None)


def test_a_sixth_copy_of_the_vocabulary_fails_here():
    """AC1's enforcement, SCOPE: NAMED prefix constants on the declared surface.

    The declared surface is `tests/_embedded.py`, `tortoise/sdk.py`,
    `tortoise/projection/__init__.py`, and `tests/_live_utils.py` (added with
    #8746's `drop_test_graph`, whose fail-closed delete gate must not become a
    fifth NAMED copy of the vocabulary). The scanner matches any named constant
    ending in `PREFIXES`, at any indentation (`^\\s*…`) and with either `=` or
    a `:` annotation, so an indented or annotated constant is caught too.
    OUT OF SCOPE: numeric-suffixed or differently-named constants
    (`_PREFIXES_2`, `_MY_PREFIX`), and anonymous literals
    (`startswith(("test_", ...))` in `tortoise/sdk.py`,
    `tests/test_derived_names.py`, `tests/test_pre_migration_safety.py`) —
    they remain governed by the DIVERGENCE comment at the declaration. Do not
    claim a guarantee broader than this scanner.
    """
    seen = set()
    for rel in ("tests/_embedded.py", "tortoise/sdk.py",
                "tortoise/projection/__init__.py", "tests/_live_utils.py"):
        src = (REPO / rel).read_text()
        for m in re.finditer(r"^\s*([A-Za-z_][A-Za-z_0-9]*PREFIXES)\s*[:=]", src, re.M):
            seen.add(m.group(1))
    # POSITIVE CONTROL — mirror `tests/test_ci_selection.py`'s `assert workflows`.
    # Without it a rename/move that stops the regex matching makes the scan find
    # NOTHING, so `unregistered` is empty and this test passes for the wrong
    # reason. Every constant the register declares must be found by the scan.
    assert set(_DIVERGENCE_REGISTER) <= seen, (
        f"scan missed declared constant(s): "
        f"{sorted(set(_DIVERGENCE_REGISTER) - seen)} — it would pass vacuously")
    unregistered = {n for n in seen if n not in _DIVERGENCE_REGISTER}
    assert unregistered == set(), f"prefix constant(s) not in the register: {unregistered}"


def test_the_routed_delete_helper_consults_the_live_canonical_set(monkeypatch):
    """#8746 review P2: `drop_test_graph`'s gate is the CANONICAL set, not a copy.

    `tests/_live_utils.py::drop_test_graph` gates an IRREVERSIBLE
    ``GRAPH.DELETE`` on a prefix family. A NAMED local restatement of
    ``_SERVER_WIPE_PREFIXES`` would sit outside every drift guard, and the
    unsafe drift direction is the one nobody notices: when the canonical set is
    NARROWED (as ``#7795`` deliberately narrowed it), the copy stays broad and
    the helper keeps deleting a family the canonical GRAPH.LIST set no longer
    authorises — fail-OPEN on the delete gate.

    So narrow the canonical symbol only and require the helper to REFUSE. On
    the restated copy this fails: ``test_*`` still passes its own gate and
    reaches the guard's presence read.
    """
    calls: list[tuple] = []

    class _Db:
        def list_graphs(self):
            calls.append(("list_graphs",))
            return []

    proj = SimpleNamespace(graph_name="test_review_p2_canonical_ab12cd34", db=_Db())
    # Snapshot/restore the session record: this test DRIVES a refusal, and a
    # synthetic refusal must not fork into a real run's
    # `tempdir-hygiene-end.json` section (#7735 `_discard_tolerated_leaks`
    # precedent; #8746 review r3 P3, artifact name corrected in r6 P3 — the record
    # rides the artifact the session finalizer already dumps, NOT a graph-delete-
    # specific file, which this PR deliberately did not create).
    snapshot = _live_utils.graph_delete_events()
    # PRECONDITION — the canonical set admits this name, so the gate OPENS and
    # the guard's presence read is what answers (the graph is absent → False).
    # Without this the refusal below could pass for the wrong reason.
    assert _live_utils.drop_test_graph(proj) is False
    assert calls and all(c == ("list_graphs",) for c in calls), (
        "the gate refused a name the canonical set authorises — the "
        "precondition of this test is broken, so it would pass vacuously")
    calls.clear()
    assert _live_utils.graph_delete_events() == snapshot  # absent → not recorded
    monkeypatch.setattr(embedded, "_SERVER_WIPE_PREFIXES", ("tortoise_test",))
    try:
        assert _live_utils.drop_test_graph(proj) is False
        assert calls == [], (
            "drop_test_graph reached the delete path for a name the canonical "
            "_SERVER_WIPE_PREFIXES no longer authorises — its prefix gate is a "
            "COPY of the vocabulary, which is fail-OPEN on an irreversible "
            "GRAPH.DELETE (#8746 review P2)")
    finally:
        _live_utils._restore_graph_delete_events(snapshot)


# ── AC3 AST pin (Task 1 establishes the helper; Task 3 wires it up) ────────
_DEFAULT_PATH_FILES = ("tests/_embedded.py", "tests/conftest.py",
                       "tests/test_tripwire.py")
# Expected call sites: NONE. The guard fails loudly if this set changes
# (the tests/test_write_ahead_mint.py `_MINT_SITES` contract).
_LEGACY_CALL_SITES: set[tuple[str, int]] = set()

_LEGACY_SWEEP_NAME = "_sweep_legacy_strays"


def _calls_in(path: Path) -> list[int]:
    """Line numbers of every Call to the legacy sweep in ``path``.

    A RECURSIVE walk over every child node that matches either a bare ``Name``
    (``_sweep_legacy_strays(...)``) or an ``Attribute``
    (``_embedded._sweep_legacy_strays(...)``). The walk recurses into lambda
    bodies, so an ``atexit.register(lambda: _sweep_legacy_strays(...))`` call
    site is not missed.
    """
    tree = ast.parse(path.read_text())
    found: list[int] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                f = child.func
                if ((isinstance(f, ast.Name) and f.id == _LEGACY_SWEEP_NAME)
                        or (isinstance(f, ast.Attribute)
                            and f.attr == _LEGACY_SWEEP_NAME)):
                    found.append(child.lineno)
            walk(child)

    walk(tree)
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
