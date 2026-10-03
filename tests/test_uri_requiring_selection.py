"""#6884: URI-requiring modules must not be handed to a URI-LESS (tier-2) leg.

The defect this pins: `python-ci.yml`'s tier-2 PR legs are URI-unset BY DESIGN
(the empty URI is the E2E-6 tripwire signal — `python-ci.yml:600-602` and
`1367-1368`, epic #1647 Task 9 + cycle-6 P2-8), so "provision the URI" is a
reversal of a recorded decision, not a fix. A test module that module-skips when
`TORTOISE_DB_URI` is absent therefore collects ZERO tests in such a leg;
`pytest --collect-only` exits 5 and the fail-closed manifest guard kills the leg
BEFORE any test runs. That failure has no `FAILED <nodeid>`, so the merge rail
cannot attribute it and refuses for want of a failure identity (#6798 shape) —
#6390 was `mergeable=true` with a clean review record AT HEAD and still could
not be landed.

The fix is the MIRROR of the existing `files -= carve` subtraction: those files
cannot run in a URI-SET leg, these cannot run in a URI-UNSET one. Three arms,
each load-bearing:

* the declaration in `config/ci-surfaces.yml` is re-derived from the tree, so a
  NEW module-skipping file reds here instead of silently reintroducing the
  defect (the #2944 discipline: structural, not name-based);
* the selection arms prove the filter actually runs on every tier-2 exit — both
  the docs-only early return and the main surface path — plus the slow lane;
* the derivation arms pin the predicate against the shapes that would otherwise
  slip past it, since a predicate that misses a shape is how this defect returns
  (see `test_derivation_covers_every_module_skipping_shape`).
"""
from __future__ import annotations

import ast
import copy
import functools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_selection import (  # noqa: I001
    classify_test_file, load_manifest, on_demand_files, select,
    slow_leg_by_surface, uri_requiring_files,
)

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

# The real vector: #6390 is a one-file dashboard comment change (`+4/-3`) whose
# tier-2 leg selected `test_onboarding_state_split.py` and died on rc=5. Kept
# verbatim so the regression is pinned against the input that actually failed.
DASHBOARD_ONLY_CHANGE = ["website/apps/dashboard/src/harnesses.js"]
DOCS_ONLY_CHANGE = ["docs/00_index.md"]
URI_MENTION = "TORTOISE_DB_URI"


# --------------------------------------------------------------------------
# The derivation. It must answer exactly one question: "can importing this
# module abort collection?" — because that is what turns a leg red with no
# attributable nodeid. Module-level skip calls abort; a skip inside a function
# that import never calls does not.
# --------------------------------------------------------------------------
def _is_module_level_skip(call: ast.Call) -> bool:
    """A `skip(..., allow_module_level=True)` call, whatever it is named.

    Name-agnostic on purpose: `from pytest import skip as pskip` must not evade
    the guard, and neither must a re-exported wrapper.
    """
    return any(
        kw.arg == "allow_module_level"
        and isinstance(kw.value, ast.Constant)
        # any truthy non-string constant: `True` is the normal spelling, but
        # `allow_module_level=1` aborts collection just the same, so rejecting
        # it would be a detection gap rather than strictness.
        and bool(kw.value.value)
        and not isinstance(kw.value.value, str)
        for kw in call.keywords
    )


def _has_skip(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and _is_module_level_skip(n) for n in ast.walk(node)
    )


def _module_scope_nodes(node: ast.AST):
    """`node` plus every descendant reachable at MODULE scope (run on import).

    Descends into module-level `if`/`try`/`with`/`for` bodies, but NOT into
    function/class/lambda bodies: a skip inside a function that import never
    calls does not abort collection, and counting it would subtract a
    perfectly collectible file from the tier-2 legs — a real coverage loss in
    the opposite direction.
    """
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        yield from _module_scope_nodes(child)


def _called_at_module_scope(names: set[str], tree: ast.AST) -> bool:
    for node in _module_scope_nodes(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else ""
            )
            if name in names:
                return True
    return False


def _aborts_collection(tree: ast.AST) -> bool:
    """True when importing this module can abort collection (pytest rc=5)."""
    # (a) the skip is on a module-scope path (directly, or inside a
    #     module-level if/try/with/for)
    if any(
        isinstance(n, ast.Call) and _is_module_level_skip(n)
        for n in _module_scope_nodes(tree)
    ):
        return True
    # (b) the skip lives in a helper that the module CALLS at import time —
    #     `if not env: _require()` where `_require` calls skip. The call-site
    #     condition is what makes this reachable, which is why an UNCALLED
    #     helper containing a skip must NOT count.
    helpers = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _has_skip(n)
    }
    return bool(helpers) and _called_at_module_scope(helpers, tree)


def _uri_gated_source(src: str) -> bool:
    """The whole predicate, on a source string (so it is testable directly)."""
    if URI_MENTION not in src:
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    return _aborts_collection(tree)


@functools.lru_cache(maxsize=1)
def _derived_uri_requiring() -> frozenset[str]:
    """Re-derive the declared set from the tree.

    Memoised: the full-tree walk is the expensive part and three tests call it.
    Unmemoised it cost ~16s of this file's runtime and, being a new file with no
    measured duration, it packs into a shard at the ~2s default weight.
    """
    found: set[str] = set()
    for path in sorted(TESTS.rglob("*.py")):
        rel = path.relative_to(TESTS)
        if rel.parts[0] == "e2e":
            # e2e is exempt from `integrity()` (#1349) and is not selectable, so
            # an entry here could never do anything.
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _uri_gated_source(src):
            found.add(str(rel))
    return frozenset(found)


# --------------------------------------------------------------------------
# Derivation arms
# --------------------------------------------------------------------------
def test_declaration_is_not_vacuous() -> None:
    """A predicate that silently matched nothing would make every other arm
    here pass for the wrong reason."""
    derived = _derived_uri_requiring()
    assert derived, (
        "the derivation found NO module-skipping file — the predicate has "
        "rotted (it found 8 on 2026-10-03), which would make this whole "
        "module vacuous"
    )


def test_declared_list_matches_the_tree() -> None:
    """A new module-skipping file must be REGISTERED, not discovered in CI."""
    declared = uri_requiring_files(load_manifest())
    derived = set(_derived_uri_requiring())
    assert declared == derived, (
        "config/ci-surfaces.yml `uri_requiring` has drifted from the tree.\n"
        f"  declared but NOT derived: {sorted(declared - derived)}\n"
        f"  derived but NOT declared: {sorted(derived - declared)}\n"
        "If a name is on the FIRST line the likely cause is a DERIVATION GAP, "
        "not a stale entry: the file probably still aborts collection in a "
        "URI-less leg and the predicate simply missed its shape (see "
        "test_derivation_covers_every_module_skipping_shape). Deleting the "
        "entry would silently re-leak it. Only a name that genuinely no longer "
        "module-skips belongs off the declaration."
    )


def test_declared_entries_exist_and_are_classified() -> None:
    """Every declared name must be a real, manifest-classified test file.

    A dead entry (a file no surface owns) is a no-op that protects nothing: it
    would be subtracted from a selection that never contained it.
    """
    manifest = load_manifest()
    for name in sorted(uri_requiring_files(manifest)):
        assert (TESTS / name).is_file(), f"uri_requiring names a missing file: {name}"
        assert classify_test_file(name, manifest) is not None, (
            f"uri_requiring names an UNCLASSIFIED file: {name} — no surface owns "
            "it, so no leg ever selects it and the entry protects nothing"
        )


def test_lane_is_disjoint_from_the_uri_unset_only_lanes() -> None:
    """carve-out / tier-1 / on-demand are URI-unset or leg-less by design.

    `slow_files` is deliberately NOT asserted disjoint: the slow lane subtracts
    the lane itself (pinned by test_slow_lane_subtracts_uri_requiring), so a
    future relocation into `slow_files` is handled rather than forbidden.
    """
    manifest = load_manifest()
    uri = uri_requiring_files(manifest)
    assert not (uri & set(manifest.get("carve_out", []))), (
        "a uri_requiring file is also carve_out — the carve-out job is URI-unset "
        "too, so it would abort there instead (#6884)"
    )
    assert not (uri & set(manifest.get("tier1", []))), (
        "a uri_requiring file is in the tier-1 smoke set, which every PR "
        "selection starts from — the guard test itself must not depend on tier1 "
        "staying URI-free"
    )
    assert not (uri & on_demand_files(manifest))


def test_empty_lane_does_not_crash_the_changes_job() -> None:
    """`uri_requiring:` with NO entries parses to None, not [] (YAML's empty
    value) — `set(None)` would TypeError in the `changes` job, the single path
    all PR CI goes through. Reachable at the lane's terminal state: the day the
    last URI-gated module is fixed."""
    manifest = copy.deepcopy(load_manifest())
    manifest["uri_requiring"] = None
    assert uri_requiring_files(manifest) == set()
    for changed in (DOCS_ONLY_CHANGE, DASHBOARD_ONLY_CHANGE):
        sel = select(changed, "pull_request", manifest)  # must not raise
        assert isinstance(sel["test_files"], list)


# --------------------------------------------------------------------------
# The predicate's own coverage — the shapes a naive AST check misses
# --------------------------------------------------------------------------
_ABORTS = "aborts"
_CLEAN = "clean"
_SKIP = 'pytest.skip("needs docker", allow_module_level=True)'
_URI = f'os.environ.get("{URI_MENTION}")'


@pytest.mark.parametrize(
    "label,src,expected",
    [
        # --- must be DERIVED: every one of these aborts collection (rc=5) ---
        ("literal in the if-test", f"import os, pytest\nif not {_URI}:\n    {_SKIP}\n", _ABORTS),
        ("helper predicate", f'import os, pytest\ndef absent():\n    return not {_URI}\nif absent():\n    {_SKIP}\n', _ABORTS),
        ("renamed alias", f'import os\nfrom pytest import skip as pskip\nif not {_URI}:\n    pskip("x", allow_module_level=True)\n', _ABORTS),
        ("local wrapper called at module scope", f'import os, pytest\ndef req():\n    {_SKIP}\nif not {_URI}:\n    req()\n', _ABORTS),
        ("nested inner if", f'import os, pytest\nif True:\n    if not {_URI}:\n        {_SKIP}\n', _ABORTS),
        ("try-wrapped", f'import os, pytest\ntry:\n    if not {_URI}:\n        {_SKIP}\nexcept Exception:\n    pass\n', _ABORTS),
        ("uri name in a module constant", f'import os, pytest\nK = "{URI_MENTION}"\nif not os.environ.get(K):\n    {_SKIP}\n', _ABORTS),
        ("else-branch", f'import os, pytest\nif {_URI}:\n    pass\nelse:\n    {_SKIP}\n', _ABORTS),
        ("truthy 1 for allow_module_level", f'import os, pytest\nif not {_URI}:\n    pytest.skip("x", allow_module_level=1)\n', _ABORTS),
        # --- must NOT be derived ---
        ("no URI mention at all", f'import os, pytest\nif not os.environ.get("OTHER"):\n    {_SKIP}\n', _CLEAN),
        ("skipif marker collects items (rc=0)", f'import os, pytest\npytestmark = pytest.mark.skipif(not {_URI}, reason="docker")\ndef test_x():\n    assert True\n', _CLEAN),
        ("importorskip is not URI-gated (fails every leg)", 'import pytest\nmod = pytest.importorskip("nope")\n', _CLEAN),
        ("UNcalled helper containing a skip", f'import os, pytest\ndef helper():\n    {_SKIP}\nif {_URI}:\n    pass\n', _CLEAN),
        ("function-level skip (items still collect)", f'import os, pytest\ndef test_x():\n    if not {_URI}:\n        pytest.skip("needs docker")\n', _CLEAN),
    ],
)
def test_derivation_covers_every_module_skipping_shape(
    label: str, src: str, expected: str
) -> None:
    """A predicate that misses a shape is HOW this defect returns.

    Each row is a shape the guard must classify correctly; the `aborts` rows all
    produce the identical unattributable rc=5 failure in a URI-less leg, and the
    `clean` rows must NOT be subtracted (subtracting them would remove real
    coverage / hide a file that fails everywhere).
    """
    got = _uri_gated_source(src)
    assert got == (expected == _ABORTS), (
        f"{label!r}: derived={got}, expected={expected}"
    )


# --------------------------------------------------------------------------
# Selection arms — every tier-2 exit
# --------------------------------------------------------------------------
# Every tier-2 exit reachable by a PR: the docs/website-only EARLY return, the
# main surface path, and the slow lane. Parametrised so a fix applied to only
# one exit cannot pass.
TIER2_VECTORS = [
    ("docs-only (early return)", DOCS_ONLY_CHANGE),
    ("website-only (early return)", ["website/index.html"]),
    ("dashboard surface (#6390)", DASHBOARD_ONLY_CHANGE),
    ("onboarding surface", ["tortoise/onboarding/SKILL.md"]),
    ("core surface", ["config/ci-surfaces.yml"]),
]


@pytest.mark.parametrize(
    "changed", [v[1] for v in TIER2_VECTORS], ids=[v[0] for v in TIER2_VECTORS]
)
def test_no_tier2_path_leaks_a_uri_requiring_file(changed: list[str]) -> None:
    sel = select(changed, "pull_request", load_manifest())
    assert sel["full"] is False, "vector must stay a tier-2 selection"
    uri = uri_requiring_files(load_manifest())
    leaked = uri & set(sel["test_files"])
    assert not leaked, (
        f"a URI-less tier-2 leg was handed URI-requiring file(s) {sorted(leaked)} "
        "— each module-skips at import there, collects zero tests, and reds the "
        "leg with no attributable failure (#6884)"
    )
    leaked_slow = uri & set(sel["slow_selected"])
    assert not leaked_slow, (
        f"the URI-less test-slow leg was handed {sorted(leaked_slow)} (#6884)"
    )


def test_dashboard_vector_is_load_bearing() -> None:
    """The #6390 vector must really exercise the filter.

    Without this, `test_no_tier2_path_leaks_a_uri_requiring_file` could pass
    vacuously the day this change stops selecting the onboarding surface.
    """
    sel = select(DASHBOARD_ONLY_CHANGE, "pull_request", load_manifest())
    owned: set[str] = set()
    for surface in sel["surfaces"]:
        owned.update(load_manifest()["surfaces"].get(surface, []) or [])
    assert uri_requiring_files(load_manifest()) & owned, (
        "the #6390 vector no longer selects a surface owning a URI-requiring "
        "file, so the filter is no longer exercised here — pick another real "
        "vector rather than deleting the assertion"
    )


def test_docs_only_early_return_subtracts_the_lane() -> None:
    """A plain docs-only vector cannot fail: `tier1 ∩ uri_requiring == []`, so
    the output is byte-identical with and without the early return's
    subtraction. Inject one into tier-1 to make the exit provable."""
    manifest = copy.deepcopy(load_manifest())
    victim = sorted(uri_requiring_files(manifest))[0]
    manifest["tier1"] = sorted(set(manifest["tier1"]) | {victim})
    sel = select(DOCS_ONLY_CHANGE, "pull_request", manifest)
    assert victim in set(manifest["tier1"]), "setup: injection must be visible"
    assert victim not in set(sel["test_files"]), (
        "the docs-only early return did not subtract `uri_requiring` — it "
        "returns before the main tier-2 subtraction (#6884)"
    )


def test_slow_lane_subtracts_the_lane() -> None:
    """The test-slow legs are URI-unset on a tier-2 PR too, so a relocated
    `uri_requiring` file reproduces the identical unattributable rc=5."""
    manifest = copy.deepcopy(load_manifest())
    victim = "test_eval_ingest_cache.py"
    assert victim in uri_requiring_files(manifest), "setup: known lane member"
    manifest["slow_files"] = sorted(set(manifest["slow_files"]) | {victim})
    assert victim in (set(manifest["slow_files"]) - set(manifest["carve_out"])), (
        "setup: the file must otherwise reach the slow leg"
    )
    legs: set[str] = set()
    for members in slow_leg_by_surface(manifest).values():
        legs.update(members)
    assert victim not in legs, (
        "a uri_requiring file relocated into `slow_files` reaches the URI-less "
        "test-slow leg (#6884)"
    )


def test_full_selection_keeps_uri_requiring_files() -> None:
    """The push lane HAS the URI, so the subtraction must not leak into it.

    `_full_selection` reports `test_files` as the sentinel `"ALL"` (every fast
    file runs), so the assertion is on the sentinel — proving the tier-2
    subtraction was not applied to the full path.
    """
    sel = select(DASHBOARD_ONLY_CHANGE, "push", load_manifest())
    assert sel["full"] is True, "a push must be a full selection"
    assert sel["test_files"] == "ALL", (
        "a full/push selection has the URI and MUST keep running every fast "
        f"file (including the `uri_requiring` ones); got {sel['test_files']!r}, "
        "so the tier-2 subtraction leaked into the full path (#6884)"
    )
