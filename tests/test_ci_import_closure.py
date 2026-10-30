"""Tests for the static import-closure derivation (#10132).

Three things are being protected here, in order of what would break the most:

1. **The validation harness can REPORT a miss.** A harness that always says
   "clean" is worse than none — it would green-light a narrowing that silently
   drops a failing test. `test_harness_reports_a_miss_when_the_closure_drops_an_
   edge` is the mutation check: a deliberately-wrong closure (one import edge
   removed) must turn the harness red.
2. **The four shapes that silently UNDER-select.** Function-local imports,
   `TYPE_CHECKING` blocks, `from __future__`, and the `tortoise/__init__.py`
   package-init edge are each asserted, because each one is a way the closure
   looks complete while missing edges — the direction that ships a false green.
3. **Every unresolvable file is REPORTED, never dropped** — with a reason, so
   `UNKNOWN` is evidence rather than silence.

No network: every `gh`-shaped path is exercised through pure functions
(`parse_junit_failures`, `artifact_names_for_job`, `misses_for`).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_import_closure import (  # noqa: I001
    DIRECT, MISS_NO_PATH, MISS_NOT_PYTHON, MISS_TAINTED, PACKAGE_INIT,
    REPO as _REPO,
    Closure, ModuleIndex, Scanner, UNKNOWN_DYNAMIC_ATTR, UNKNOWN_DYNAMIC_CALL,
    UNKNOWN_LOADER, UNKNOWN_SYNTAX, UNKNOWN_UNRESOLVED, artifact_names_for_job,
    classify_miss, compute_closures, core_test_files, derived_core_selection,
    misses_for, module_name, parse_junit_failures, summarise,
)


# ---------------------------------------------------------------------------
# Synthetic trees
# ---------------------------------------------------------------------------
def _index(*files: str) -> ModuleIndex:
    return ModuleIndex(list(files))


def _scan(index: ModuleIndex, rel: str, source: str):
    return Scanner(index).scan(rel, source)


def test_module_name_maps_modules_packages_and_init():
    assert module_name("tortoise/sdk.py") == "tortoise.sdk"
    assert module_name("tortoise/onboarding/__init__.py") == "tortoise.onboarding"
    assert module_name("tests/eval/harness/test_x.py") == "tests.eval.harness.test_x"


def test_import_of_a_submodule_adds_the_package_init_edge():
    """`import tortoise.sdk` runs `tortoise/__init__.py` first — model it.

    This is the single biggest threat to a file-level split: if the shim's own
    closure is large, every test importing any submodule inherits it.
    """
    idx = _index("tortoise/__init__.py", "tortoise/sdk.py")
    scan = _scan(idx, "tests/test_a.py", "import tortoise.sdk\n")
    kinds = {(d.target, d.kind) for d in scan.deps}
    assert ("tortoise.sdk", DIRECT) in kinds
    assert ("tortoise", PACKAGE_INIT) in kinds


def test_from_package_import_name_descends_into_the_submodule():
    idx = _index("tortoise/__init__.py", "tortoise/hosted_api.py", "tortoise/config.py")
    scan = _scan(idx, "tests/test_a.py", "from tortoise import hosted_api\n")
    targets = {d.target for d in scan.deps}
    assert "tortoise.hosted_api" in targets
    assert "tortoise" in targets
    # A NAME that is not a module is not an edge — `from tortoise import FalkorDB`
    # reaches the shim only, and reporting a phantom module would be a lie.
    scan2 = _scan(idx, "tests/test_b.py", "from tortoise import FalkorDB\n")
    assert {d.target for d in scan2.deps} == {"tortoise"}
    assert not scan2.unknown


def test_function_local_imports_are_walked_and_tagged():
    """`tortoise/sdk.py` carries 28 module-level in-repo imports and 61 that arrive
    ONLY from a function body (89 distinct targets; 13 are reached both ways, so
    a top-level-only reader would miss the hub entirely)."""
    idx = _index("tortoise/abuse.py", "tortoise/sdk.py")
    src = (
        "def f():\n"
        "    from tortoise.abuse import something\n"
        "    return something\n"
    )
    scan = _scan(idx, "tortoise/sdk.py", src)
    assert [(d.target, d.in_function) for d in scan.deps] == [("tortoise.abuse", True)]


def test_relative_imports_resolve_from_module_and_package_context():
    idx = _index(
        "tortoise/__init__.py", "tortoise/a/__init__.py",
        "tortoise/a/b.py", "tortoise/a/c.py", "tortoise/d.py",
    )
    # inside tortoise/a/b.py: `.` anchors at tortoise.a, `..` at tortoise
    scan = _scan(idx, "tortoise/a/b.py", "from . import c\nfrom .. import d\n")
    targets = {d.target for d in scan.deps}
    assert {"tortoise.a.c", "tortoise.d"} <= targets
    # inside a PACKAGE __init__, `.` anchors at the package itself
    scan2 = _scan(idx, "tortoise/a/__init__.py", "from . import c\n")
    assert "tortoise.a.c" in {d.target for d in scan2.deps}


def test_type_checking_is_a_dependency_by_default_and_can_be_excluded():
    """DECISION (documented in the tool's docstring): include.

    `tach` skips `TYPE_CHECKING` by default because its question is runtime
    coupling. Ours is "would a change here be observable", where the safe
    direction is over-approximation: one extra test beats a false green.
    """
    idx = _index("tortoise/__init__.py", "tortoise/typing_only.py",
                 "tortoise/runtime_dep.py")
    src = (
        "from typing import TYPE_CHECKING\n"
        "from tortoise.runtime_dep import x\n"
        "if TYPE_CHECKING:\n"
        "    from tortoise.typing_only import T\n"
    )
    scan = _scan(idx, "tests/test_a.py", src)
    tc = {d.target: d.type_checking for d in scan.deps}
    assert tc["tortoise.runtime_dep"] is False
    assert tc["tortoise.typing_only"] is True
    assert scan.unknown == []


def test_future_import_is_named_and_is_not_a_dependency():
    idx = _index("tortoise/__init__.py")
    scan = _scan(idx, "tests/test_a.py", "from __future__ import annotations\n")
    assert scan.future_imports == ["annotations"]
    assert scan.deps == []
    assert scan.unknown == []


def test_dynamic_import_with_a_literal_is_resolved_not_tainted():
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py")
    scan = _scan(
        idx, "tests/test_a.py",
        "import importlib\nimportlib.import_module('tortoise.lazy')\n",
    )
    assert "tortoise.lazy" in {d.target for d in scan.deps}
    assert scan.unknown == []


def test_dynamic_import_with_a_computed_name_is_unknown_not_dropped():
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py")
    scan = _scan(
        idx, "tests/test_a.py",
        "import importlib\nname = 'tortoise.' + 'lazy'\n"
        "importlib.import_module(name)\n",
    )
    assert [u.reason for u in scan.unknown] == [UNKNOWN_DYNAMIC_CALL]
    assert scan.tainted


def test_an_ALIASED_importlib_call_still_resolves_its_literal():
    """P2 (#10143): `import importlib as X` binds the SAME module, so the call is
    as static as the unaliased one — matching the root against the literal string
    `"importlib"` dropped it into silence (no edge, no `UNKNOWN`).

    This is not hypothetical: `tortoise/projection.py:16` is
    `import importlib as _importlib` and `:23` is
    `_importlib.import_module('tortoise.projection')`, the file's only edge.
    """
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py")
    scan = _scan(
        idx, "tests/test_a.py",
        "import importlib as _importlib\n"
        "_importlib.import_module('tortoise.lazy')\n",
    )
    assert "tortoise.lazy" in {d.target for d in scan.deps}, (
        "an aliased importlib call is the same call: its literal must resolve")
    assert scan.unknown == []


def test_an_ALIASED_from_import_of_the_import_family_still_resolves():
    """The other aliasing shape: `from importlib import import_module as _im`.

    `_im(...)` is an `ast.Name`, so the old Name arm — which matched only the
    literal function names — did not fire either. Same hole, other spelling.
    """
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py")
    scan = _scan(
        idx, "tests/test_a.py",
        "from importlib import import_module as _im\n_im('tortoise.lazy')\n",
    )
    assert "tortoise.lazy" in {d.target for d in scan.deps}
    assert scan.unknown == []


def test_an_aliased_importlib_call_with_a_COMPUTED_name_is_unknown():
    """The silence had a second face: a computed name must be `UNKNOWN`, and an
    alias that matched nothing left the file reading as a complete empty closure
    — the under-selecting direction the tool exists to rule out.
    """
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py")
    scan = _scan(
        idx, "tests/test_a.py",
        "import importlib as _importlib\nname = 'tortoise.' + 'lazy'\n"
        "_importlib.import_module(name)\n",
    )
    assert [u.reason for u in scan.unknown] == [UNKNOWN_DYNAMIC_CALL]
    assert scan.tainted


def test_importlib_dunder_import_is_the_import_family_too():
    """`importlib.__import__('tortoise.lazy')` was dropped by the ATTRIBUTE arm.

    The bare-Name arm matched every name in `_DYNAMIC_IMPORT_ATTRS`, but the
    attribute arm listed three of the four by hand and omitted `__import__` — so
    `importlib.__import__('tortoise.lazy')` produced no edge and no `UNKNOWN`,
    the same silence the aliased-root finding was about, one line away.
    """
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py")
    scan = _scan(
        idx, "tests/test_a.py",
        "import importlib\nimportlib.__import__('tortoise.lazy')\n",
    )
    assert "tortoise.lazy" in {d.target for d in scan.deps}
    assert scan.unknown == []


def test_a_repo_module_that_defines_import_module_is_still_not_stdlib():
    """PRECISION, pinned so the alias fix cannot trade one hole for another.

    The alias sets are built from a real import of the stdlib `importlib`, so a
    repo module's own `import_module` is not misread as the import machinery: no
    edge, no `UNKNOWN`, exactly as before.
    """
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py", "tortoise/mine.py")
    scan = _scan(
        idx, "tests/test_a.py",
        "from tortoise.mine import import_module as _mine\n_mine('tortoise.lazy')\n",
    )
    assert "tortoise.lazy" not in {d.target for d in scan.deps}
    assert scan.unknown == []


def test_getattr_on_an_in_repo_module_taints_but_on_a_third_party_one_does_not():
    """Taint must mean "may be MISSING a tortoise module" or it says nothing.

    Over-tainting collapses the signal in a densely connected graph: one unknown
    marks the whole component. So `getattr(os, ...)` is not a blind spot about
    `tortoise/**`, while `getattr(tortoise_mod, <computed>)` is.
    """
    idx = _index("tortoise/__init__.py", "tortoise/registry.py")
    src = (
        "import os\n"
        "from tortoise import registry\n"
        "getattr(os, 'environ')\n"
        "getattr(os, name_var)\n"
    )
    clean = _scan(idx, "tests/test_a.py", src)
    assert clean.unknown == []

    dirty = _scan(idx, "tests/test_a.py", src + "getattr(registry, name_var)\n")
    assert [u.reason for u in dirty.unknown] == [UNKNOWN_DYNAMIC_ATTR]

    resolved = _scan(idx, "tests/test_a.py", src + "getattr(registry, 'x')\n")
    assert resolved.unknown == []
    # …and it adds NO EDGE either: asserting only `unknown == []` would pass for an
    # implementation that invented a phantom edge, which is half of what the
    # comment claims (#10143 review P2).
    assert [(d.target, d.kind) for d in resolved.deps] == [
        (d.target, d.kind) for d in clean.deps]


def test_getattr_resolves_a_literal_attr_from_the_binding_s_dotted_target():
    """P2: the candidate must come from the dotted target, not the local name.

    `module_bindings` used to hold the LOCAL binding, so `from tortoise import
    projection` made `getattr(projection, 'in_memory')` build
    `projection.in_memory` — never a module — and the branch then `continue`d
    with no edge AND no `UNKNOWN`: a real submodule dropped silently, in the
    under-selecting direction the tool exists to rule out. The shape the old pin
    covered (`from tortoise import registry`, where the local name equals the
    module name) agreed by accident.
    """
    idx = _index(
        "tortoise/__init__.py", "tortoise/sdk/__init__.py",
        "tortoise/sdk/hosted_api.py", "tortoise/projection/__init__.py",
        "tortoise/projection/in_memory.py",
    )
    from_name = _scan(
        idx, "tests/test_a.py",
        "from tortoise import projection\ngetattr(projection, 'in_memory')\n",
    )
    assert "tortoise.projection.in_memory" in {d.target for d in from_name.deps}
    assert from_name.unknown == []

    aliased = _scan(
        idx, "tests/test_b.py",
        "import tortoise.sdk as s\ngetattr(s, 'hosted_api')\n",
    )
    assert "tortoise.sdk.hosted_api" in {d.target for d in aliased.deps}
    assert aliased.unknown == []

    # A local name that is NOT a module attribute still yields no edge — the
    # literal is static, so this is silence about a non-module, not a drop.
    dead = _scan(
        idx, "tests/test_c.py",
        "import tortoise.sdk as s\ngetattr(s, 'no_such_submodule')\n",
    )
    assert dead.unknown == []
    assert not {d.target for d in dead.deps} - {"tortoise", "tortoise.sdk"}


def test_unparseable_file_is_reported_with_a_reason():
    idx = _index("tortoise/__init__.py")
    scan = _scan(idx, "tests/test_broken.py", "def f(:\n")
    assert [u.reason for u in scan.unknown] == [UNKNOWN_SYNTAX]


def test_unresolvable_in_repo_name_is_reported():
    idx = _index("tortoise/__init__.py")
    scan = _scan(idx, "tests/test_a.py", "from tortoise.nope import thing\n")
    assert [u.reason for u in scan.unknown] == [UNKNOWN_UNRESOLVED]
    assert "tortoise.nope" in scan.unknown[0].detail


def test_closure_is_transitive_and_reports_only_the_package():
    """A `tests/` helper is TRAVERSED but never reported — only `tortoise/**` is."""
    idx = _index(
        "tortoise/__init__.py", "tortoise/a.py", "tortoise/b.py",
        "tests/_helper.py",
    )
    scans = {
        "tests/test_t.py": _scan(idx, "tests/test_t.py", "from tests._helper import h\n"),
        "tests/_helper.py": _scan(idx, "tests/_helper.py", "import tortoise.a\n"),
        "tortoise/a.py": _scan(idx, "tortoise/a.py", "from tortoise.b import B\n"),
        "tortoise/__init__.py": _scan(idx, "tortoise/__init__.py", ""),
        "tortoise/b.py": _scan(idx, "tortoise/b.py", ""),
    }
    clo = compute_closures(scans, idx, ["tests/test_t.py"])["tests/test_t.py"]
    assert set(clo.modules) == {"tortoise/__init__.py", "tortoise/a.py", "tortoise/b.py"}
    assert clo.tainted is False


# ---------------------------------------------------------------------------
# The validation harness — including the MUTATION CHECK
# ---------------------------------------------------------------------------
CLOSURE_DEPENDS_ON_LEAK = Closure(
    rel="tests/test_leak_guard.py",
    modules=("tortoise/__init__.py", "tortoise/retention.py"),
    tainted=False, reasons=(),
)


def test_harness_reports_a_miss_when_the_closure_drops_an_edge():
    """⛔ THE MUTATION CHECK — a harness that cannot report a miss is worthless.

    A historical PR changed `tortoise/retention.py` and `test_leak_guard.py`
    actually failed on it. With the real edge the test is selected and the window
    is clean; DROP that one edge (exactly what a wrong derivation does) and the
    harness must report the miss.
    """
    core = ["tests/test_leak_guard.py", "tests/test_other.py"]
    current = set(core) | {"tests/test_api.py"}
    failed = {"tests/test_leak_guard.py"}

    right = {"test_leak_guard.py": CLOSURE_DEPENDS_ON_LEAK,
             "test_other.py": Closure("tests/test_other.py", (), False, ())}
    kept = derived_core_selection(
        ["tortoise/retention.py"], core,
        {f"tests/{k}": v for k, v in right.items()}, _index("tortoise/__init__.py"),
        keep_empty_closures=True)
    assert "tests/test_leak_guard.py" in kept["selected"]
    clean = misses_for(current, set(kept["selected"]), failed)
    assert clean["misses"] == []
    assert clean["safe"] is True

    # ⛔ MUTATION: drop `tortoise/retention.py` from the failing test's closure.
    mutated = {"tests/test_leak_guard.py": Closure(
        "tests/test_leak_guard.py", ("tortoise/__init__.py",), False, ()),
        "tests/test_other.py": Closure("tests/test_other.py", (), False, ())}
    broken = derived_core_selection(
        ["tortoise/retention.py"], core, mutated, _index("tortoise/__init__.py"),
        keep_empty_closures=True)
    red = misses_for(current, set(broken["selected"]), failed)
    assert red["misses"] == ["tests/test_leak_guard.py"]
    assert red["safe"] is False


def test_harness_does_not_blame_the_derivation_for_a_test_the_selector_never_runs():
    """A failing test outside today's selection is a gap in the EXISTING
    selector, not in the derived one — scoring it as a miss would condemn a
    derivation that is fine."""
    current = {"tests/test_a.py"}
    result = misses_for(current, current, {"tests/test_a.py", "tests/test_slow.py"})
    assert result["misses"] == []
    assert result["not_in_current"] == ["tests/test_slow.py"]


def test_empty_closure_files_survive_only_when_asked_to():
    """120 of 756 core files import nothing from the package: they guard a
    CONFIG/WORKFLOW/DOC file, which no import edge can carry (#10070, #10120)."""
    core = ["tests/test_config_guard.py", "tests/test_imports_pkg.py"]
    closures = {
        "tests/test_config_guard.py": Closure("tests/test_config_guard.py", (), False, ()),
        "tests/test_imports_pkg.py": CLOSURE_DEPENDS_ON_LEAK,
    }
    idx = _index("tortoise/__init__.py")
    naive = derived_core_selection([".github/workflows/ci.yml"], core, closures, idx,
                                   keep_empty_closures=False)
    safe = derived_core_selection([".github/workflows/ci.yml"], core, closures, idx,
                                  keep_empty_closures=True)
    assert naive["selected"] == []
    assert safe["selected"] == ["tests/test_config_guard.py"]


def test_a_changed_test_file_is_always_selected():
    core = ["tests/test_a.py"]
    closures = {"tests/test_a.py": Closure("tests/test_a.py", (), True, ())}
    out = derived_core_selection(["tests/test_a.py"], core, closures,
                                 _index("tortoise/__init__.py"))
    assert out["selected"] == ["tests/test_a.py"]


def test_miss_classes_are_mechanically_distinguishable():
    tainted = {"tests/t.py": Closure("tests/t.py", (), True, ({"reason": "x"},))}
    empty = {"tests/e.py": Closure("tests/e.py", (), False, ())}
    normal = {"tests/n.py": CLOSURE_DEPENDS_ON_LEAK}
    assert classify_miss("tests/t.py", [], tainted) == MISS_TAINTED
    assert classify_miss("tests/e.py", [], empty) == MISS_NOT_PYTHON
    assert classify_miss("tests/n.py", ["tortoise/other.py"], normal) == MISS_NO_PATH
    # A file the derivation never analysed is its own class — never silently
    # folded into "no intersection".
    assert classify_miss("tests/gone.py", [], normal) == "unknown-file"


# ---------------------------------------------------------------------------
# junit parsing — the ground truth, and the "red job != failing test" lesson
# ---------------------------------------------------------------------------
def _junit(*cases: str) -> bytes:
    body = "\n".join(cases)
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        f"{body}</testsuite></testsuites>"
    ).encode()


def test_junit_failures_are_extracted_by_file():
    xml = _junit(
        '<testcase classname="tests.test_a.TestX" name="t1" file="tests/test_a.py" '
        'line="3"><failure message="boom">trace</failure></testcase>',
        '<testcase classname="tests.test_b" name="t2" file="tests/test_b.py" '
        'line="9"><error message="boom">trace</error></testcase>',
        '<testcase classname="tests.test_c" name="t3" file="tests/test_c.py" line="1"/>',
    )
    parsed = parse_junit_failures(xml)
    assert parsed["files"] == ["tests/test_a.py", "tests/test_b.py"]
    assert parsed["executed"] == 3


def test_a_red_job_with_zero_test_failures_yields_no_failures():
    """Measured on #10070: `test (f)` concluded `failure` at a post-test hygiene
    step ("Assert no redislite orphans") while junit recorded 2 907 tests and
    ZERO failures. Treating the job as ground truth injects phantom misses."""
    xml = _junit(
        '<testcase classname="tests.test_c" name="t3" file="tests/test_c.py" line="1"/>',
        '<testcase classname="tests.test_c" name="t4" file="tests/test_c.py" line="2"/>',
    )
    parsed = parse_junit_failures(xml)
    assert parsed["files"] == []
    assert parsed["executed"] == 2


def test_unparseable_junit_is_reported_not_silently_empty():
    parsed = parse_junit_failures(b"<not-xml")
    assert parsed["files"] == []
    assert "error" in parsed


def test_junit_classname_fallback_resolves_the_module_not_a_stub_path():
    """P2: `file` exists ONLY under `junit_family=xunit1`; `xunit2` is the ini
    default. The old fallback did `classname.rpartition('.')`, which turns the
    module-level `tests.test_ci_import_closure` into **`tests.py`** — a path that
    intersects nothing, so the failure is scored `not_in_current`, the one branch
    that absolves the derivation."""
    module_level = _junit(
        '<testcase classname="tests.test_ci_import_closure" name="test_foo">'
        '<failure message="boom">trace</failure></testcase>'
    )
    assert parse_junit_failures(module_level)["files"] == [
        "tests/test_ci_import_closure.py"]

    class_based = _junit(
        '<testcase classname="tests.test_ci_import_closure.TestX" name="t1">'
        '<failure message="boom">trace</failure></testcase>'
    )
    assert parse_junit_failures(class_based)["files"] == [
        "tests/test_ci_import_closure.py"]


def test_a_failing_test_with_no_resolvable_file_is_recorded_not_dropped():
    """The silent alternative is worse than a partial window: an unattributable
    failure is either dropped (under-counting misses) or attributed to a bogus
    path that reads as "the derivation is safe"."""
    xml = _junit(
        '<testcase classname="nowhere.test_gone" name="t1">'
        '<failure message="boom">trace</failure></testcase>'
    )
    parsed = parse_junit_failures(xml)
    assert parsed["files"] == []
    assert parsed["unresolved"] == ["nowhere.test_gone::t1"]


def test_ground_truth_reports_a_junit_whose_failures_have_no_file(monkeypatch, tmp_path):
    """Producer half of the fallback fix: the PARSE result must reach
    `artifact_errors` (and so block the cache write), not just `unresolved`."""
    from tools import ci_import_closure as module
    junit = _junit(
        '<testcase classname="nowhere.test_gone" name="t1">'
        '<failure message="boom">trace</failure></testcase>'
    )
    _fake_ground_truth(monkeypatch, module, junit=junit,
                       listing_names=["pytest-log-test-a"])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["artifact_errors"]
    assert not (_truth_cache(tmp_path, "deadbeef")).exists()


def test_artifact_name_contract_for_shard_and_plain_jobs():
    """The four shapes that already matched keep their OWN name first."""
    assert artifact_names_for_job("test (f)")[0] == "pytest-log-test-f"
    assert artifact_names_for_job("test-slow (c)")[0] == "pytest-log-test-slow-c"
    assert artifact_names_for_job("test-carve-out (a)")[0] == "pytest-log-test-carve-out-a"
    assert artifact_names_for_job("test-concurrency-falkor")[0] == (
        "pytest-log-test-concurrency-falkor")


def test_artifact_names_cover_the_three_legs_whose_upload_is_not_the_job_name():
    """#10143 review P2: `artifact_names_for_job` returned a name that exists for
    NONE of these three legs, so each recorded a FALSE `artifact_errors` entry (a
    permanent PARTIAL, exit 2) AND never had its junit opened.

    The check run carries the job's DISPLAY name; the artifact carries whatever
    the step's `with: name:` evaluates to, which `upload-artifact` documents as an
    arbitrary user label.
    """
    # python-ci.yml:3733 has no `name:`; :3856 uploads the LITERAL — no `test-`.
    assert "pytest-log-record-postgres" in artifact_names_for_job(
        "test-record-postgres")
    # same shape: job :3167 (no `name:`), literal upload at :3327.
    assert "pytest-log-d14-hosted-api" in artifact_names_for_job(
        "test-d14-hosted-api")
    # bare matrix name = shard 0: `name:` suffix '' but artifact label 'a'.
    assert "pytest-log-test-carve-out-a" in artifact_names_for_job("test-carve-out")


def test_the_bare_carve_out_artifact_label_matches_the_selector():
    """The `-a` above is NOT a guess about shard 0 — it is read off the selector.

    `python-ci.yml:3160` names the upload with `matrix.label`, while the job's
    display name uses `matrix.suffix`; `carve_shard_entries` sets `suffix=''` and
    `label='a'` for shard 0. Pin the two together, so a future re-labelling of
    shard 0 breaks HERE (one line) instead of silently mis-naming three legs.
    """
    from tools import ci_import_closure as module
    sel = module._selection_module()
    include = sel.carve_matrix_include(
        sel.carve_shard_entries(sel.load_manifest()))["include"]
    assert include, "the manifest must expand at least one carve-out shard"
    first = include[0]
    assert first["suffix"] == "", "shard 0 must keep the BARE job name"
    assert f"pytest-log-test-carve-out-{first['label']}" in artifact_names_for_job(
        "test-carve-out")


@pytest.mark.parametrize("job,artifact,member", [
    ("test-record-postgres", "pytest-log-record-postgres", "junit.xml"),
    ("test-d14-hosted-api", "pytest-log-d14-hosted-api", "junit-d14.xml"),
    ("test-carve-out", "pytest-log-test-carve-out-a", "junit.xml"),
])
def test_a_leg_whose_upload_is_not_the_job_name_is_read_with_no_false_partial(
        monkeypatch, tmp_path, job, artifact, member):
    """End-to-end for both #10143 artifact-name findings at once.

    A red leg whose artifact name differs from its check-run name, beside a
    readable SIBLING leg, must (a) record NO `artifact_errors` entry and (b)
    read ITS OWN junit — not the sibling's. Pre-fix, (a) failed with a false
    "uploaded no …" entry and (b) failed silently: `selected` was non-empty (the
    sibling), so the wide fallback never fired and this leg's junit was never
    opened. The d14 row carries its payload as `junit-d14.xml`, so it also pins
    the `_junit_from_zip` member rule.
    """
    from tools import ci_import_closure as module
    sibling = "pytest-log-test-a"
    # REAL files: a classname that resolves to nothing is recorded as an
    # unattributable failure, which would blur the two states under test.
    own = _junit(
        '<testcase classname="tests.test_ci_import_closure" name="t1">'
        '<failure message="boom">trace</failure></testcase>')
    other = _junit(
        '<testcase classname="tests.test_ci_selection" name="t1">'
        '<failure message="boom">trace</failure></testcase>')
    _fake_ground_truth(monkeypatch, module, junit=None,
                       listing_names=[sibling, artifact], job=job,
                       junits={artifact: own, sibling: other}, member=member)
    truth = module.pr_ground_truth("o/r", "cafe01", tmp_path)
    assert truth["artifact_errors"] == [
    ], (f"a leg that DID upload {artifact} must not be reported as having "
        "uploaded nothing — that entry is a permanent false PARTIAL")
    assert truth["failed_files"] == ["tests/test_ci_import_closure.py"], (
        "the leg's OWN junit must be the one read; the sibling's clean-for-us "
        "junit standing in is the masking state this check exists to prevent")


def test_a_junit_member_under_a_caller_chosen_name_is_read(tmp_path):
    """#10143 review P2: `--junitxml` takes a caller-chosen PATH.

    `_junit_from_zip` matched only a member ending in `junit.xml`, so the d14
    leg's `junit-d14.xml` was unreadable BY CONSTRUCTION. `_junit_from_zip` had
    no pin at all before this one.
    """
    import zipfile

    from tools import ci_import_closure as module

    def _zip(name: str, members: dict[str, bytes]) -> Path:
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as zf:
            for member, payload in members.items():
                zf.writestr(member, payload)
        return path

    assert module._junit_from_zip(
        _zip("d14.zip", {"junit-d14.xml": b"<d14/>"})) == b"<d14/>"
    assert module._junit_from_zip(
        _zip("both.zip", {"junit-d14.xml": b"<d14/>",
                          "junit.xml": b"<exact/>"})) == b"<exact/>", (
        "an exact junit.xml wins when an artifact carries both, and the choice "
        "must not depend on zip order")
    assert module._junit_from_zip(
        _zip("nested.zip", {"reports/junit-3.xml": b"<nested/>"})) == b"<nested/>"
    assert module._junit_from_zip(
        _zip("none.zip", {"pytest.log": b"log"})) is None
    assert module._junit_from_zip(
        _zip("decoy.zip", {"myjunit.xml": b"<not-junit/>"})) is None, (
        "`endswith('junit.xml')` also accepted `myjunit.xml`; the member must "
        "be a junit FILE, not a name that merely ends the same way")


# ---------------------------------------------------------------------------
# Real repo: the derivation runs, and the #10132 mechanism is pinned
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def repo_closure():
    from tools import ci_import_closure as ci

    manifest = ci._selection_module().load_manifest()
    files = ci._git_py_files(ci.REPO)
    index = ModuleIndex(files)
    scans = ci.scan_all(index, files)
    core = core_test_files(manifest)
    closures = compute_closures(scans, index, core)
    return manifest, index, scans, core, closures


def test_real_suite_core_surface_is_derived(repo_closure):
    manifest, index, scans, core, closures = repo_closure
    del index, scans
    expected = len([f for f in manifest["surfaces"]["core"] if f.endswith(".py")])
    # EXACTLY, not `>= expected * 0.95` (#10143 review P2). The comment claimed
    # exact basename resolution while the assertion tolerated 5 % — at 756
    # members that is up to 38 core files silently ceasing to resolve with every
    # test still green, and every published percentage is a ratio over this
    # count. Measured at `427037b36`: all 756 resolve. `core_test_files` now reports an
    # unresolvable member instead of dropping it, so the failure names the file.
    unresolved: list[str] = []
    core = core_test_files(manifest, unresolved)
    assert not unresolved, f"core member(s) resolved to no test file: {unresolved}"
    assert len(core) == expected
    assert len(closures) == len(core) > 700
    # No file is silently omitted: every core file has a closure entry.
    assert all(rel in closures for rel in core)


def test_package_init_reaches_the_sdk_hub_through_config(repo_closure):
    """THE #10132 MECHANISM, pinned as evidence rather than prose.

    `tortoise/__init__.py` -> (module level) `tortoise.config` ->
    (function-local) `tortoise.sdk`, the 42k-line hub -> 187 of 259 modules.
    Because importing ANY `tortoise.*` submodule imports the package first, that
    chain lands in ~85% of core closures, which is why a file-level split cannot
    narrow anything. If this assertion starts failing, the chain was broken —
    good news, and `docs/ci/core-import-closure.md` must be re-measured.
    """
    _manifest, index, scans, _core, closures = repo_closure
    init = compute_closures(scans, index, ["tortoise/__init__.py"])["tortoise/__init__.py"]
    assert "tortoise/sdk.py" in init.modules
    assert "tortoise/config.py" in init.modules
    assert len(init.modules) > 100
    sdks = [c for c in closures.values() if "tortoise/sdk.py" in c.modules]
    assert len(sdks) > len(closures) * 0.5


def test_a_package_owns_its_dotted_name_over_a_same_named_module():
    """CPYTHON PRECEDENCE, and an under-selection bug this measurement found.

    `tortoise/projection/__init__.py` and `tortoise/projection.py` both map to
    `tortoise.projection`. Resolving to the module (the alphabetically first
    path) drops every module that only the package reaches. On this tree the
    pair no longer changes a core closure, so this pin is SYNTHETIC on purpose:
    it proves the precedence rule and the `shadowed` record, not a live delta.
    The shadowed file is still scanned and reported, never silently discarded.
    """
    files = ["tortoise/projection.py", "tortoise/projection/__init__.py"]
    idx = _index(*files)
    assert idx.resolve("tortoise.projection") == "tortoise/projection/__init__.py"
    assert idx.shadowed == [{
        "module": "tortoise.projection",
        "ignored": "tortoise/projection.py",
        "owner": "tortoise/projection/__init__.py",
    }]
    scans = {
        "tests/test_t.py": _scan(idx, "tests/test_t.py", "import tortoise.projection\n"),
        "tortoise/projection/__init__.py": _scan(
            idx, "tortoise/projection/__init__.py",
            "from tortoise.projection import only_in_the_package\n"),
        # the shadowed module imports NOTHING: following it would lose the edge
        "tortoise/projection.py": _scan(idx, "tortoise/projection.py", ""),
    }
    idx.by_module["tortoise.projection.only_in_the_package"] = "tortoise/x.py"
    clo = compute_closures(scans, idx, ["tests/test_t.py"])["tests/test_t.py"]
    assert "tortoise/projection/__init__.py" in clo.modules
    assert "tortoise/projection.py" not in clo.modules


def test_the_LIVE_shim_records_its_importlib_edge(repo_closure):
    """P2 (#10143): the finding's own evidence file, pinned in the real tree.

    `tortoise/projection.py` reaches the package ONLY through
    `_importlib.import_module('tortoise.projection')` (an aliased `importlib`), so
    the synthetic tests above prove the rule and this one proves the scan of the
    actual file. Before the alias fix its scan carried NO dependency at all: the
    file's whole closure was empty, and `classify_miss` would have called a
    failure here `non-python-dependency` — "no import edge can ever exist" — for
    a file whose only edge is exactly that call.

    If the deprecated shim is ever deleted, delete this pin with it: its subject
    is that file's shape, not a general rule.
    """
    _manifest, _index, scans, _core, _closures = repo_closure
    assert "tortoise/projection.py" in scans, (
        "the deprecated `tortoise/projection.py` shim is gone — if it was "
        "deleted on purpose, delete this pin too")
    deps = {d.target for d in scans["tortoise/projection.py"].deps}
    assert "tortoise.projection" in deps, (
        "an aliased `_importlib.import_module('tortoise.projection')` must "
        "resolve to the edge it statically names")
    assert scans["tortoise/projection.py"].unknown == []


def test_import_time_only_reproduces_the_naive_reader_that_under_selects():
    """`--import-time-only` is the trap, kept measurable.

    On this repo it reports a median core closure of 19 modules instead of 187 —
    a ">89 % reduction" that would ship a false green, because `tortoise/sdk.py`
    reaches 61 of its 89 in-repo modules ONLY from a function body.
    """
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py")
    scans = {
        "tests/test_t.py": _scan(
            idx, "tests/test_t.py",
            "def f():\n    from tortoise.lazy import thing\n    return thing\n"),
        "tortoise/__init__.py": _scan(idx, "tortoise/__init__.py", ""),
        "tortoise/lazy.py": _scan(idx, "tortoise/lazy.py", ""),
    }
    full = compute_closures(scans, idx, ["tests/test_t.py"],
                            follow_function_local=True)["tests/test_t.py"]
    naive = compute_closures(scans, idx, ["tests/test_t.py"],
                             follow_function_local=False)["tests/test_t.py"]
    assert "tortoise/lazy.py" in full.modules
    assert naive.modules == ()


# ---------------------------------------------------------------------------
# #10143 review round: the four defects found AFTER the first clean verdict.
# Each test below fails on the code as it was and passes on the fix, so the
# defect cannot come back silently. They are grouped here, not scattered, so the
# next reviewer can see exactly what was wrong and re-verify it.
# ---------------------------------------------------------------------------
def test_a_target_imported_both_ways_keeps_the_module_level_edge():
    """P1/P2: the dedupe must not make the survivor depend on the READER.

    `_imports_with_context` is a LIFO stack, so for a target imported both at
    module level and inside a function the FUNCTION-LOCAL occurrence is yielded
    first. The old `_dedupe_deps` keyed on `(target, kind)` and compared
    `type_checking` only, so the function-local edge won and the module-level
    edge was DISCARDED — and then `--import-time-only` filtered the survivor
    out, leaving the file looking as if it never imported the target at all.

    Collapsing the two CONTEXT axes into one survivor is reader-DEPENDENT and no
    ordering can fix it: keep the module-level `TYPE_CHECKING` edge and
    `--exclude-type-checking` drops it; keep the function-local runtime edge and
    `--import-time-only` drops it. So the key is
    `(target, kind, type_checking, in_function)` — only EXACT duplicates
    collapse — and every reader filters the edges that are its own. Both
    occurrences survive here, each with its own context.
    """
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py")
    scans = {
        "tests/test_t.py": _scan(
            idx, "tests/test_t.py",
            "from tortoise.lazy import at_module_level\n"
            "def f():\n"
            "    from tortoise.lazy import in_a_function\n"
            "    return in_a_function\n"),
        "tortoise/__init__.py": _scan(idx, "tortoise/__init__.py", ""),
        "tortoise/lazy.py": _scan(idx, "tortoise/lazy.py", ""),
    }
    deps = scans["tests/test_t.py"].deps
    lazy = [d for d in deps if d.target == "tortoise.lazy"]
    assert len(lazy) == 2, f"one edge per CONTEXT, got {lazy}"
    module_level = [d for d in lazy if d.in_function is False]
    assert len(module_level) == 1 and module_level[0].line == 1, (
        "the MODULE-LEVEL occurrence must survive the dedupe as its own edge")
    # the consequence the bug had: BOTH readers must still see the target
    naive = compute_closures(scans, idx, ["tests/test_t.py"],
                             follow_function_local=False)["tests/test_t.py"]
    assert "tortoise/lazy.py" in naive.modules
    full = compute_closures(scans, idx, ["tests/test_t.py"],
                            follow_function_local=True)["tests/test_t.py"]
    assert "tortoise/lazy.py" in full.modules


def test_dedupe_keeps_a_runtime_edge_beside_a_type_checking_one():
    """`TYPE_CHECKING` is a separate CONTEXT, not a tiebreak between rivals."""
    idx = _index("tortoise/a.py")
    scan = _scan(
        idx, "tests/test_t.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from tortoise.a import thing\n"
        "from tortoise.a import other\n")
    a = [d for d in scan.deps if d.target == "tortoise.a"]
    assert len(a) == 2, (
        "the runtime and type-only occurrences are different contexts and both "
        "must survive, so neither reader loses the target")
    assert sorted(d.type_checking for d in a) == [False, True]


def test_summarise_reports_the_shim_closure_for_the_reader_that_was_used():
    """P2: `summarise` recomputed the shim closure with the DEFAULT reader.

    `--import-time-only --json` therefore published `package_init_closure: 186`
    — the all-nesting number — next to a median of 21, so the one field that
    answers the issue's own question contradicted the mode the run was in.
    """
    idx = _index("tortoise/__init__.py", "tortoise/config.py", "tortoise/sdk.py")
    scans = {
        "tests/test_t.py": _scan(idx, "tests/test_t.py", "import tortoise.config\n"),
        # the shim's own edges, module level
        "tortoise/__init__.py": _scan(
            idx, "tortoise/__init__.py", "from tortoise.config import x\n"),
        # the deferred (cycle-breaking) edge that drags the hub in
        "tortoise/config.py": _scan(
            idx, "tortoise/config.py",
            "def f():\n    from tortoise.sdk import g\n    return g\n"),
        "tortoise/sdk.py": _scan(idx, "tortoise/sdk.py", ""),
    }
    closures = compute_closures(scans, idx, ["tests/test_t.py", "tortoise/__init__.py"])
    full = summarise(closures, idx, scans, follow_function_local=True)
    naive = summarise(closures, idx, scans, follow_function_local=False)
    assert full["package_init_closure"] == 3      # the shim + config + sdk
    assert naive["package_init_closure"] == 2     # the deferred sdk edge is gone


# ---------------------------------------------------------------------------
# The verdict — the ONE answer this lane exists to produce
# ---------------------------------------------------------------------------
def _verdict_summary(*, hub_tests: int, core_files: int,
                     median_closure_size: int, tortoise_modules: int,
                     hub_module: str = "tortoise/config.py") -> dict:
    """A `summarise`-shaped payload carrying only what the verdict reads."""
    return {
        "core_files": core_files,
        "top_hubs": [{"module": hub_module, "tests": hub_tests}],
        "median_closure_size": median_closure_size,
        "tortoise_modules": tortoise_modules,
        "distinct_closure_sets": 3,
    }


def test_the_split_verdict_can_say_YES():
    """#10143 review P3: `split_justified` is the lane's whole product and NO
    test asserted it — `verdict_from_summary` merely executed inside a test that
    read `summary["core_files"]`.

    The rule must be able to answer YES, or "No" is not a measurement. Inverting
    either comparison (`<` → `>`) turns this into False and reds here.
    """
    from tools import ci_import_closure as module
    summary = _verdict_summary(hub_tests=100, core_files=752,
                               median_closure_size=90, tortoise_modules=258)
    verdict = module.verdict_from_summary(summary, {}, module.ModuleIndex([]))
    assert verdict["hub_share"] == round(100 / 752, 4), verdict
    assert verdict["median_closure_share_of_package"] == round(90 / 258, 4), verdict
    assert verdict["hub_module"] == "tortoise/config.py"
    assert verdict["split_justified"] is True, verdict


def test_the_split_verdict_says_NO_when_one_module_dominates():
    """The published answer — `docs/ci/core-import-closure.md` §6 *"Is a
    file-level split … justified by the data? No."* — is this comparison.

    `hub_share` 0.60 sits ABOVE the 0.5 rule and BELOW 0.95, which is what makes
    the pin discriminating: the surviving mutation at the review head
    (`POSITION_HUB_SHARE = 0.95`) flips this to True and reds here, while a
    case at 0.9 could not tell the constant from the rule.
    """
    from tools import ci_import_closure as module
    summary = _verdict_summary(hub_tests=451, core_files=752,
                               median_closure_size=90, tortoise_modules=258)
    verdict = module.verdict_from_summary(summary, {}, module.ModuleIndex([]))
    assert verdict["hub_share"] == round(451 / 752, 4), verdict
    assert verdict["split_justified"] is False, verdict


def test_the_split_verdict_says_NO_when_the_TYPICAL_change_still_hits_half():
    """The MEDIAN conjunct, isolated: a tiny hub beside a median change that
    still selects over half the package.

    Without this, deleting `median_share < POSITION_HUB_SHARE` from the rule is
    invisible — the hub term alone answers False on the dominating-hub case.
    """
    from tools import ci_import_closure as module
    summary = _verdict_summary(hub_tests=10, core_files=752,
                               median_closure_size=200, tortoise_modules=258)
    verdict = module.verdict_from_summary(summary, {}, module.ModuleIndex([]))
    assert verdict["hub_share"] == round(10 / 752, 4), verdict
    assert verdict["median_closure_share_of_package"] == round(200 / 258, 4), verdict
    assert verdict["split_justified"] is False, (
        "hub_share is ~0, so only the median term can answer No: "
        f"{verdict}")


def test_the_published_rule_states_the_share_it_was_measured_at():
    """`cmd_closure` prints `verdict["rule"]` beside the verdict, and the doc
    restates it. Pin the share INTO the sentence so the printed verdict cannot
    disagree with the document (#10143 review P3)."""
    from tools import ci_import_closure as module
    summary = _verdict_summary(hub_tests=451, core_files=752,
                               median_closure_size=90, tortoise_modules=258)
    verdict = module.verdict_from_summary(summary, {}, module.ModuleIndex([]))
    assert "60%" in verdict["rule"], verdict["rule"]
    assert "752 core files" in verdict["rule"], verdict["rule"]
    assert "more than half" in verdict["rule"], verdict["rule"]


def test_the_verdict_and_summarise_publish_the_SAME_hub_share():
    """`hub_share` is computed in TWO places — `summarise` publishes it and
    `verdict_from_summary` recomputes it from `top_hubs[0]` — so the number the
    report shows and the number the verdict used can drift apart silently
    (#10143 review P3).

    Restricted to the shape where the shim IS the busiest hub (the real tree's
    shape, 636 of 756), because `summarise` measures `tortoise/__init__.py`
    specifically while the verdict takes the top hub: this pin must not assert an
    equality the two are not supposed to have.
    """
    from tools import ci_import_closure as module
    mods = ("tortoise/__init__.py",)
    closures = {
        f"tests/test_{i}.py": module.Closure(f"tests/test_{i}.py", mods, False, ())
        for i in range(4)
    }
    idx = _index("tortoise/__init__.py")
    summary = summarise(closures, idx, None)
    assert summary["top_hubs"][0]["module"] == "tortoise/__init__.py", summary
    verdict = module.verdict_from_summary(summary, closures, idx)
    assert verdict["hub_share"] == summary["hub_share"], (
        f"the published share {summary['hub_share']} and the one the verdict "
        f"used {verdict['hub_share']} disagree")


def test_summarise_ranks_top_hubs_by_dependant_count():
    """The verdict reads `top_hubs[0]`, so `summarise`'s ORDERING decides the
    answer. Ranked ascending (or unsorted) it would report the LEAST busy module
    as the hub and flip a split-justified verdict to YES (#10143 review P3)."""
    from tools import ci_import_closure as module
    closures = {
        # `tortoise/quiet.py` has 1 dependant, `tortoise/busy.py` has 3.
        "tests/test_1.py": module.Closure("tests/test_1.py",
                                          ("tortoise/busy.py",), False, ()),
        "tests/test_2.py": module.Closure("tests/test_2.py",
                                          ("tortoise/busy.py",), False, ()),
        "tests/test_3.py": module.Closure("tests/test_3.py",
                                          ("tortoise/busy.py",
                                           "tortoise/quiet.py"), False, ()),
    }
    idx = _index("tortoise/busy.py", "tortoise/quiet.py")
    summary = summarise(closures, idx, None)
    assert summary["top_hubs"][0] == {"module": "tortoise/busy.py", "tests": 3}, (
        summary["top_hubs"])



def _truth_cache(cache: Path, sha: str, repo: str = "o/r") -> Path:
    """The truth cache path — keyed by REPO **and** sha (#10143 review P2).

    Keyed on the sha alone, `--repo` (a first-class flag) could reuse another
    repo's truth: PR numbers collide across repos and a fork shares the commit
    sha, so a diff from one repo was paired with the ground truth of another — a
    state that can only HIDE a miss, records no error, and was cached.
    """
    from tools import ci_import_closure as module
    return cache / (f"truth-v{module.GROUND_TRUTH_VERSION}-"
                    f"{module._slug(repo)}-{sha}.json")


def _fake_ground_truth(monkeypatch, module, *, junit: bytes | None,
                       listing_names: list[str], job: str = "test (a)",
                       junits: dict[str, bytes] | None = None,
                       member: str = "junit.xml"):
    """Drive `pr_ground_truth` with a fake check-run + fake artifacts.

    `job` is the CHECK-RUN name (the leg's display name) and `listing_names` the
    run's artifact listing — two things that are allowed to disagree, which is
    the whole of the #10143 `artifact_names_for_job` finding. `junits` maps an
    ARTIFACT NAME to its own payload so a sibling leg can carry a DIFFERENT
    junit from the red leg's: that is what makes "the right artifact was read"
    assertable instead of assumed. `member` is the payload's filename inside the
    zip (the d14 leg uploads `junit-d14.xml`).
    """
    import io
    import zipfile

    run = {"name": job, "conclusion": "failure", "id": 99,
           "details_url": "https://github.com/o/r/actions/runs/99/job/1"}
    monkeypatch.setattr(module, "_check_runs", lambda repo, sha: [run])
    monkeypatch.setattr(module, "_run_id", lambda r: 99)
    # The listing seam is `_artifacts` (per RUN, paginated); `_gh_json` is the
    # PR-metadata seam and stubbing it here left the artifact listing unstubbed.
    monkeypatch.setattr(
        module, "_artifacts",
        lambda repo, run_id: [{"id": 7 + i, "name": n}
                              for i, n in enumerate(listing_names)])

    blobs: dict[int, bytes] = {}
    for i, name in enumerate(listing_names):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            payload = (junits or {}).get(name, junit)
            if payload is not None:
                zf.writestr(member, payload)
            else:
                zf.writestr("pytest.log", "some log\n")
        blobs[7 + i] = buf.getvalue()
    monkeypatch.setattr(
        module, "_download_artifact",
        lambda repo, aid, cache: _write_tmp(cache, aid, blobs[aid]))


def _write_tmp(cache: Path, aid: int, blob: bytes) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    p = cache / f"artifact-{aid}.zip"
    p.write_bytes(blob)
    return p


def _zip_of(junit: bytes, member: str = "junit.xml") -> bytes:
    """An artifact zip carrying one junit payload under `member`.

    `_download_artifact` returns a ZIP path and `_junit_from_zip` reads a MEMBER
    out of it, so a test that hands back raw XML exercises the "no junit.xml in
    artifact" arm instead of the one it means to.
    """
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, junit)
    return buf.getvalue()


def test_ground_truth_records_an_artifact_that_carries_no_junit(monkeypatch, tmp_path):
    """P2: a red leg may upload only `pytest.log`; that is NOT "nothing failed"."""
    from tools import ci_import_closure as module
    _fake_ground_truth(monkeypatch, module, junit=None, listing_names=["pytest-log-test-a"])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["artifact_errors"], (
        "an artifact with no junit.xml must be REPORTED — silently dropping it "
        "makes 'could not read' indistinguishable from 'nothing failed'")
    assert not (_truth_cache(tmp_path, "deadbeef")).exists(), (
        "a PARTIAL ground truth must never be cached as truth")


def test_ground_truth_records_an_unparseable_junit(monkeypatch, tmp_path):
    """P2: `parse_junit_failures` returns the parse error in `error`; read it."""
    from tools import ci_import_closure as module
    _fake_ground_truth(monkeypatch, module, junit=b"<not-xml",
                       listing_names=["pytest-log-test-a"])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["artifact_errors"], "unparseable junit must be reported, not dropped"
    assert not (_truth_cache(tmp_path, "deadbeef")).exists()


def test_ground_truth_records_a_red_run_that_uploaded_no_artifact(monkeypatch, tmp_path):
    """P1: drive the PRODUCER of the third drop path, not the string it hands over.

    The two tests above feed an artifact and break it. This one breaks the layer
    above: a red run whose artifact listing is EMPTY, which the harness used to
    `continue` past with no `artifact_errors` entry — and then CACHED, so the
    truncation looked like a clean run on every later invocation. Both halves are
    asserted: the entry exists, and nothing is written to the cache.
    """
    from tools import ci_import_closure as module
    _fake_ground_truth(monkeypatch, module, junit=None, listing_names=[])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["artifact_errors"], (
        "a red run with an EMPTY artifact listing is an unread run, not a clean one")
    assert truth["artifacts"] == 0
    assert not (_truth_cache(tmp_path, "deadbeef")).exists(), (
        "a partial ground truth must never be cached as truth")


def test_ground_truth_counts_a_non_failure_red_conclusion(monkeypatch, tmp_path):
    """P2: the red-class set, not equality with `conclusion == "failure"`.

    A `test*` leg concluding `timed_out` — a killed suite that may well have failed
    tests and uploaded no junit — was dropped before `failed_jobs` existed, so
    neither `artifact_errors` nor the window's unread-PR guard could see it and a
    window could still exit 0/1. `cancelled` stays NON-red (repo polarity), so both
    directions are asserted. `None` (a run GitHub has not concluded yet — a
    re-trigger at the same sha) is RED: it sat in the skip set while the comment
    above it said an unnamed value is red, which is the same fail-open shape.
    """
    from tools import ci_import_closure as module
    _fake_ground_truth(monkeypatch, module, junit=None, listing_names=[])

    for conclusion, is_red in (("timed_out", True), ("startup_failure", True),
                               (None, True), ("cancelled", False)):
        monkeypatch.setattr(module, "_check_runs", lambda repo, sha, c=conclusion: [
            {"name": "test (a)", "conclusion": c, "id": 99,
             "details_url": "https://github.com/o/r/actions/runs/99/job/1"}])
        truth = module.pr_ground_truth("o/r", f"sha-{conclusion}", tmp_path)
        if is_red:
            assert truth["jobs"] == ["test (a)"], f"{conclusion} must be RED"
            assert truth["artifact_errors"], (
                f"a red {conclusion} leg with nothing read must be reported")
        else:
            assert truth["jobs"] == [], f"{conclusion} must stay NON-red"


def test_validate_window_default_is_unset_so_an_explicit_prs_list_is_authoritative():
    """P2: `--window` defaulted to 10, so `if args.prs and args.window` was ALWAYS
    true and an explicit `--prs` list was silently clipped to its first 10 ids —
    making the report's own 40-PR table unreproducible by any documented call."""
    from tools import ci_import_closure as module
    parser = module.build_parser()
    a = parser.parse_args(["validate", "--prs", "1", "2", "3"])
    assert a.window is None, "an unpassed --window must not masquerade as 10"
    b = parser.parse_args(["validate", "--window", "40"])
    assert b.window == 40
    c = parser.parse_args(["validate", "--prs", "1", "2", "3", "--window", "2"])
    assert c.window == 2, "an EXPLICIT --window may still clip"


# ---------------------------------------------------------------------------
# The CLI certification path — the two fixes that shipped without a pin
#
# Both were documented in their own fix comments as "measured" defects
# (`--module --json` exited 0 having written nothing; a window it could not read
# exited 0 and read as clean), and neither has a pin: the tests above exercise
# the pure functions, while the defect lived in the CONSUMER. The lesson is the
# one the absent-junit pin already records — pin the consumer, not the value it
# was handed. Without these, `validate`'s exit code — the harness's ONLY
# certification signal — can regress to `0` with every test still green.
# ---------------------------------------------------------------------------
def _cli_world():
    """A synthetic tree: the shim reaches `lazy` ONLY from a function body.

    The shim also carries one genuine MODULE-LEVEL import (`base`), so its
    closure is non-trivial and the difference between the two readings is
    attributable to the deferred edge alone (`Closure.modules` holds DEPENDENCY
    paths only, so a shim with no module-level import would read 0 vs 1 without
    exercising the flag's effect on a real set).
    """
    idx = ModuleIndex(["tortoise/__init__.py", "tortoise/lazy.py",
                       "tortoise/base.py"])
    scans = {
        "tortoise/__init__.py": _scan(
            idx, "tortoise/__init__.py",
            "import tortoise.base\n"
            "def _f():\n    from tortoise.lazy import thing\n    return thing\n"),
        "tortoise/lazy.py": _scan(idx, "tortoise/lazy.py", ""),
        "tortoise/base.py": _scan(idx, "tortoise/base.py", ""),
        "tests/test_t.py": _scan(idx, "tests/test_t.py", "import tortoise.lazy\n"),
    }
    closures = compute_closures(scans, idx, ["tests/test_t.py"])
    return idx, scans, closures


def test_closure_module_report_writes_the_json_it_was_asked_for(tmp_path, monkeypatch):
    """`closure --module X --json out.json` spent its whole life writing no file.

    The JSON block sat after the `--module` early return, so the documented flag
    combination printed the module report, exited 0, and left no artifact — a
    documented flag that silently does nothing, which is the failure shape this
    tool exists to rule out.
    """
    import argparse
    import json as _json

    from tools import ci_import_closure as ci

    idx, scans, closures = _cli_world()
    data = {"scans": scans, "closures": closures, "core": ["tests/test_t.py"]}
    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))

    out = tmp_path / "closure.json"
    args = argparse.Namespace(
        json=str(out), exclude_type_checking=False, import_time_only=False,
        module="tortoise/lazy.py", limit=25, show_tainted=False,
    )
    assert ci.cmd_closure(args) == 0
    assert out.exists(), "the --json path must be written on the module exit too"
    assert _json.loads(out.read_text())["summary"]["core_files"] == 1


def _stub_selection_module(select_files=()):
    from types import SimpleNamespace

    return SimpleNamespace(
        TESTS_DIR=Path(__file__).resolve().parent,
        select=lambda changed, event, manifest, explain: {
            "test_files": list(select_files), "slow_files": [], "surfaces": [],
        },
    )


def test_validate_exits_two_when_it_could_not_read_the_window(
    tmp_path, monkeypatch, capsys,
):
    """`0 misses` must not certify a window whose junit was never read.

    An unreadable artifact can only UNDER-count misses, so the exit code — not a
    parenthetical — is what distinguishes "clean" from "I read nothing". Same
    defect as `test_ground_truth_records_an_artifact_that_carries_no_junit`, one
    layer up: there the dict lost the signal, here the exit code did.

    ⚠️ ISOLATED, on purpose (#10143 P2). This stub used to omit `runs_seen` /
    `test_legs_seen` and set `artifacts: 0`, so `unseen`, `unassessed` AND `unread`
    each forced exit 2 on their own and the test certified nothing about the arm
    its name pointed at. It now carries the healthy counts and an EMPTY
    `artifact_errors`, so `unread` (`failed_jobs and not artifacts`) is the only
    condition that can fire. The `errors` arm has its own isolated pin:
    `test_validate_exits_two_when_ONE_artifact_was_unreadable`.
    """
    import argparse

    from tools import ci_import_closure as ci

    idx, scans, closures = _cli_world()
    data = {"scans": scans, "closures": closures,
            "core": ["tests/test_t.py"]}
    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))
    # `cmd_validate` mkdirs `CACHE_DIR` = REPO/.ci-selection/closure-cache, so an
    # un-redirected test writes into the CHECKOUT (#10143 review P2).
    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ci, "_selection_module", _stub_selection_module)
    monkeypatch.setattr(ci, "merged_pr_numbers", lambda repo, window: [1])
    monkeypatch.setattr(ci, "_gh_json", lambda argv: {"head": {"sha": "deadbeef"}})
    monkeypatch.setattr(ci, "changed_files",
                        lambda repo, pr, cache, head_sha=None: ["tortoise/lazy.py"])
    monkeypatch.setattr(ci, "pr_ground_truth", lambda repo, sha, cache: {
        "failed_files": [], "failed_tests": [], "jobs": ["test (a)"],
        "job_failed_without_test_failure": [], "artifacts": 0,
        "runs_seen": 3, "test_legs_seen": 3,
        "artifact_errors": [],
        "junit_missing": [], "ground_truth_complete": False,
    })

    args = argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[], import_time_only=False,
    )
    assert ci.cmd_validate(args) == 2, "an unread window must not exit 0"
    out = capsys.readouterr().out
    assert "uploaded no readable artifact" in out, (
        "the `unread` arm must name itself; the unconditional `artifacts "
        "unreadable: N` tally line is printed on EVERY run and cannot stand in "
        "for it")
    assert "artifacts unreadable: 0" in out, (
        "this stub must not also trip the `errors` arm — that arm has its own pin")


def test_validate_exits_two_on_an_empty_window(tmp_path, monkeypatch, capsys):
    """A window of ZERO PRs used to score as a clean window and exit 0."""
    import argparse

    from tools import ci_import_closure as ci

    idx, scans, closures = _cli_world()
    data = {"scans": scans, "closures": closures,
            "core": ["tests/test_t.py"]}
    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))
    # `cmd_validate` mkdirs `CACHE_DIR` = REPO/.ci-selection/closure-cache, so an
    # un-redirected test writes into the CHECKOUT (#10143 review P2).
    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ci, "_selection_module", _stub_selection_module)
    monkeypatch.setattr(ci, "merged_pr_numbers", lambda repo, window: [])

    args = argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[], import_time_only=False,
    )
    assert ci.cmd_validate(args) == 2
    assert "no PRs" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# #10143 review round 2 (head `1ef26facd`): the six findings at that head.
# Each of these fails on the code as it was at `1ef26facd` and passes on the
# fix, so the defect cannot come back silently. Grouped, not scattered, so the
# next reviewer can see exactly what was wrong and re-verify it.
# ---------------------------------------------------------------------------
def test_a_red_pr_that_uploaded_no_artifact_is_not_a_clean_window(
    tmp_path, monkeypatch, capsys,
):
    """P1 fail-open: the THIRD "could not read the ground truth" path.

    Two drop paths (download failed, no `junit.xml`, unparseable XML) already
    appended an `artifact_errors` entry; the `if not selected: continue` branch
    did not. A red leg whose artifact listing is EMPTY — an ordinary state:
    artifacts expire after 90 days by default and deleting a run deletes its
    artifacts — therefore produced an empty `artifact_errors`, printed
    `artifacts unreadable: 0`, and `cmd_validate` returned **0**: the strongest
    possible false green for a harness whose job is to be unfoolable.

    The stub here deliberately carries `artifact_errors: []` WITH
    `artifacts: 0`, which is the state the old exit condition scored as clean.
    """
    import argparse

    from tools import ci_import_closure as ci

    idx, scans, closures = _cli_world()
    data = {"scans": scans, "closures": closures,
            "core": ["tests/test_t.py"]}
    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))
    # `cmd_validate` mkdirs `CACHE_DIR` = REPO/.ci-selection/closure-cache, so an
    # un-redirected test writes into the CHECKOUT (#10143 review P2).
    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ci, "_selection_module", _stub_selection_module)
    monkeypatch.setattr(ci, "merged_pr_numbers", lambda repo, window: [1])
    monkeypatch.setattr(ci, "_gh_json", lambda argv: {"head": {"sha": "deadbeef"}})
    monkeypatch.setattr(ci, "changed_files",
                        lambda repo, pr, cache, head_sha=None: ["tortoise/lazy.py"])
    monkeypatch.setattr(ci, "pr_ground_truth", lambda repo, sha, cache: {
        "failed_files": [], "failed_tests": [], "jobs": ["test (a)"],
        "job_failed_without_test_failure": [], "artifacts": 0,
        "artifact_errors": [],          # ← the FAIL-OPEN state: no entry at all
        "junit_missing": [], "ground_truth_complete": False,
    })

    args = argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[], import_time_only=False,
    )
    assert ci.cmd_validate(args) == 2, (
        "a red PR whose legs uploaded nothing must not exit 0"
    )
    out = capsys.readouterr().out
    assert "PARTIAL" in out and "no readable artifact" in out


def test_a_module_level_dynamic_import_is_not_tagged_function_local():
    """P2: the two dynamic-import sites hardcoded `in_function=True`.

    `_scan_dynamic` was invoked with no nesting context, so a MODULE-LEVEL
    `importlib.import_module('tortoise.lazy')` — the shape
    `test_dynamic_import_with_a_literal_is_resolved_not_tainted` writes — was
    tagged function-local and then DELETED by `--import-time-only`. That biases
    the naive-reader figures in the direction the report's argument uses (a
    smaller naive closure = a sharper reduction). Pinned on the edge's own flag,
    and again on the closure, because the flag is only observable through it.
    """
    idx = _index("tortoise/__init__.py", "tortoise/lazy.py")
    scans = {
        "tests/test_t.py": _scan(
            idx, "tests/test_t.py",
            "import importlib\nimportlib.import_module('tortoise.lazy')\n"),
        "tortoise/__init__.py": _scan(idx, "tortoise/__init__.py", ""),
        "tortoise/lazy.py": _scan(idx, "tortoise/lazy.py", ""),
    }
    lazy_edges = [d for d in scans["tests/test_t.py"].deps
                  if d.target == "tortoise.lazy"]
    assert lazy_edges, "the literal dynamic import must resolve to an edge"
    assert all(d.in_function is False for d in lazy_edges), (
        "a module-level dynamic import must not be tagged function-local"
    )
    naive = compute_closures(scans, idx, ["tests/test_t.py"],
                             follow_function_local=False)["tests/test_t.py"]
    assert "tortoise/lazy.py" in naive.modules, (
        "--import-time-only must not delete a module-level dynamic import"
    )


def test_a_loader_call_with_a_computed_path_is_reported_as_unknown_loader():
    """P3: `UNKNOWN_LOADER` / `_scan_loader_call` were exercised by nothing.

    The reason is named in the report as one of the five reported UNKNOWN
    classes and advertised as a shipped decision (§3.2), so a test must hold it
    or the branch can be inverted with every test still green.
    """
    idx = _index("tortoise/__init__.py")
    scan = _scan(
        idx, "tests/test_a.py",
        "import importlib.util\n"
        "path = 'tortoise/' + 'lazy.py'\n"
        "importlib.util.spec_from_file_location('m', path)\n",
    )
    assert [u.reason for u in scan.unknown] == [UNKNOWN_LOADER], (
        "a module loaded from a computed path is outside the AST graph"
    )
    assert scan.tainted


def test_exclude_type_checking_narrows_the_closure_it_is_asked_to_narrow():
    """P3: `follow_type_checking` was referenced by no test.

    The scan-level `type_checking` flag is pinned; the FILTER that consumes it in
    `compute_closures` — the branch `--exclude-type-checking` feeds — was not, so
    inverting it left every test green. DECISION stays "include by default"
    (safe direction); this pins that the flag actually does something both ways.
    """
    idx = _index("tortoise/__init__.py", "tortoise/typing_only.py")
    scans = {
        "tests/test_t.py": _scan(
            idx, "tests/test_t.py",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from tortoise.typing_only import T\n"),
        "tortoise/__init__.py": _scan(idx, "tortoise/__init__.py", ""),
        "tortoise/typing_only.py": _scan(idx, "tortoise/typing_only.py", ""),
    }
    full = compute_closures(scans, idx, ["tests/test_t.py"],
                            follow_type_checking=True)["tests/test_t.py"]
    excluded = compute_closures(scans, idx, ["tests/test_t.py"],
                                follow_type_checking=False)["tests/test_t.py"]
    assert "tortoise/typing_only.py" in full.modules, (
        "TYPE_CHECKING imports are dependencies by DEFAULT (our polarity)"
    )
    assert "tortoise/typing_only.py" not in excluded.modules, (
        "--exclude-type-checking must actually exclude the type-only edge"
    )


def test_core_members_that_do_not_resolve_are_reported_not_dropped():
    """P2: the denominator could shrink silently.

    `out.extend(by_basename.get(member, []))` contributes nothing for a member
    that no longer resolves, and said nothing — contradicting this module's
    contract that an unresolvable input is REPORTED. Every published percentage
    is a ratio over this count, so the drop has to be visible.
    """
    manifest = {"surfaces": {"core": ["test_ci_import_closure.py",
                                      "test_a_member_that_does_not_exist.py"]}}
    unresolved: list[str] = []
    core = core_test_files(manifest, unresolved)
    assert "tests/test_ci_import_closure.py" in core
    assert unresolved == ["test_a_member_that_does_not_exist.py"], (
        "an unresolvable core member must be reported, never silently dropped"
    )


def test_a_green_pr_with_no_artifacts_still_exits_zero(monkeypatch, capsys,
                                                       tmp_path):
    """The partial-window test must be `red legs && nothing read`, NOT `== 0`.

    `artifacts` counts junit blobs read off RED `test*` legs only, so a fully
    green PR — most of any window — always has `artifacts == 0`. Measured on the
    §3 window: 24 of 40 PRs are green. Keying the PARTIAL verdict on
    `artifacts == 0` therefore reports every window as truncated, and a
    fail-closed check that ALWAYS fires carries no information. Pinned so the
    broader (wrong) condition cannot come back.
    """
    import argparse

    from tools import ci_import_closure as ci

    idx, scans, closures = _cli_world()
    data = {"scans": scans, "closures": closures,
            "core": ["tests/test_t.py"]}
    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))
    # `cmd_validate` mkdirs `CACHE_DIR` = REPO/.ci-selection/closure-cache, so an
    # un-redirected test writes into the CHECKOUT (#10143 review P2).
    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ci, "_selection_module", _stub_selection_module)
    monkeypatch.setattr(ci, "merged_pr_numbers", lambda repo, window: [1])
    monkeypatch.setattr(ci, "_gh_json", lambda argv: {"head": {"sha": "deadbeef"}})
    monkeypatch.setattr(ci, "changed_files",
                        lambda repo, pr, cache, head_sha=None: ["tortoise/lazy.py"])
    monkeypatch.setattr(ci, "pr_ground_truth", lambda repo, sha, cache: {
        "failed_files": [], "failed_tests": [], "jobs": [],   # ← green PR
        "job_failed_without_test_failure": [], "artifacts": 0,
        "runs_seen": 3, "test_legs_seen": 3,
        "artifact_errors": [], "junit_missing": [], "ground_truth_complete": True,
    })

    args = argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[], import_time_only=False,
    )
    assert ci.cmd_validate(args) == 0, (
        "a green PR that uploaded nothing is a clean PR, not a partial window"
    )
    assert "PARTIAL" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# #10143 review round at `cbe0aecb8`: the three fail-open paths that survived it.
#
# Each pin fails on the code as it stood at that head and passes on the fix, so
# the defect cannot come back silently. Grouped, like the round above, so the
# next reviewer can see exactly what was wrong and re-verify it.
# ---------------------------------------------------------------------------
def test_ground_truth_reports_a_leg_masked_by_a_sibling_legs_artifact(
    monkeypatch, tmp_path,
):
    """P2: a SIBLING leg's junit must never stand in for the red leg's own.

    `_run_id` parses the workflow RUN out of `details_url`, and `test`,
    `test-slow`, `test-carve-out` and the always-on legs are matrix jobs inside
    ONE run — so the artifact listing carries every leg's `pytest-log-*`. The
    wide fallback (a rename mitigation) fired whenever the red leg's OWN artifact
    was absent, which is ordinary: the upload step is `if: always()` +
    `if-no-files-found: ignore`, so a leg that early-exits uploads nothing. The
    sibling then parsed clean, the row read `job red, no test-level failure` (the
    genuine #10070 zero-failure shape), `artifact_errors` stayed empty — and the
    truncated truth was CACHED, so every re-run read the false answer forever.

    The two states must be told apart: the fallback still reads the sibling (the
    rename mitigation is kept) AND the substitution is recorded, which is what
    makes the window PARTIAL and the result uncacheable.
    """
    from tools import ci_import_closure as module
    _fake_ground_truth(
        monkeypatch, module,
        junit=b'<testsuite><testcase classname="tests.test_x" name="t"/>'
              b"</testsuite>",
        listing_names=["pytest-log-test-b"],   # ← a SIBLING leg, not `test (a)`
    )
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["artifact_errors"], (
        "the failed leg's own artifact is absent and a sibling's was substituted "
        "— that is an unread leg, and it must be reported")
    assert any("test (a)" in e for e in truth["artifact_errors"]), (
        "the entry must name the LEG whose junit was substituted — pinning the "
        "wording instead of the job let a reworded message fail a correct fix "
        "(#10143 review P2)")
    assert not (_truth_cache(tmp_path, "deadbeef")).exists(), (
        "a masked leg is PARTIAL truth and must never be cached")


def test_a_read_leg_whose_own_artifact_is_present_records_nothing(monkeypatch, tmp_path):
    """The masking check must not fire when the red leg's own junit IS there.

    The control for the pin above: an always-firing fail-closed check carries no
    information (the same argument as the green-PR pin), so a leg whose expected
    artifact exists must leave `artifact_errors` empty and be cached.
    """
    from tools import ci_import_closure as module
    _fake_ground_truth(
        monkeypatch, module,
        junit=b'<testsuite><testcase classname="tests.test_x" name="t"/></testsuite>',
        listing_names=["pytest-log-test-a", "pytest-log-test-b"],
    )
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["artifact_errors"] == []
    assert (_truth_cache(tmp_path, "deadbeef")).exists()


def test_ground_truth_counts_the_check_runs_it_saw_and_caches_only_a_read_one(
    monkeypatch, tmp_path,
):
    """P2: "I saw no check run at all" was byte-identical to a green PR.

    Completed runs are deletable and artifacts expire; a `GITHUB_TOKEN`-created
    run can complete with zero check runs (`docs/00_index.md`'s #8375 row). In
    that state `jobs: []`, `artifacts: 0`, `artifact_errors: []` reads exactly
    like a clean PR, so `validate` could exit 0 on a PR it never assessed — and
    the empty truth was cached, which is precisely what
    `.github/workflows/ci-check-repair.yml` re-triggers against.
    """
    from tools import ci_import_closure as module
    monkeypatch.setattr(module, "_check_runs", lambda repo, sha: [])
    monkeypatch.setattr(module, "_artifacts", lambda repo, run_id: [])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["runs_seen"] == 0
    assert not (_truth_cache(tmp_path, "deadbeef")).exists(), (
        "an unassessed PR must not be cached as a clean one")


def _stub_validate(monkeypatch, ci, truth_stub, *, changed=("tortoise/lazy.py",),
                   select_files=(), cache: Path | None = None):
    """Drive `cmd_validate` over one synthetic PR and return its exit code.

    `cache` is not decoration: `cmd_validate` runs
    `CACHE_DIR.mkdir(parents=True, exist_ok=True)` with
    `CACHE_DIR = REPO/.ci-selection/closure-cache`, so a test that does not
    redirect it writes into the CHECKOUT — while declaring a `tmp_path` that
    reads as isolation. Pass `cache=tmp_path`.

    ⚠️ `core` here is `["tests/test_t.py"]`, which is NOT the real manifest's shape
    for an always-on leg: `derived` starts from `current_rel - core_rel`, so a pin
    whose subject must survive that subtraction has to put the file IN `core` — and
    give it a closure. A pin about `core` membership therefore builds its own world
    (#10143 review at `a746139ba`, F2).
    """
    import argparse

    idx, scans, closures = _cli_world()
    data = {"scans": scans, "closures": closures,
            "core": ["tests/test_t.py"]}
    if cache is not None:
        monkeypatch.setattr(ci, "CACHE_DIR", cache)
    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))
    monkeypatch.setattr(
        ci, "_selection_module",
        lambda: _stub_selection_module(list(select_files)))
    monkeypatch.setattr(ci, "merged_pr_numbers", lambda repo, window: [1])
    monkeypatch.setattr(ci, "_gh_json", lambda argv: {"head": {"sha": "deadbeef"}})
    monkeypatch.setattr(ci, "changed_files",
                        lambda repo, pr, cache, head_sha=None: list(changed))
    monkeypatch.setattr(ci, "pr_ground_truth", lambda repo, sha, cache: dict(truth_stub))
    args = argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[], import_time_only=False,
    )
    return ci.cmd_validate(args)


def _truth(**over):
    base = {
        "failed_files": [], "failed_tests": [], "jobs": [],
        "job_failed_without_test_failure": [], "artifacts": 0,
        "runs_seen": 3, "test_legs_seen": 3, "artifact_errors": [],
    }
    base.update(over)
    return base


def test_validate_exits_two_when_ONE_artifact_was_unreadable(monkeypatch, capsys,
                                                             tmp_path):
    """P2 (#10143): the `errors` arm of the certification gate was UNPINNED.

    `errors` is the only arm that fires when a red leg WAS partly read
    (`artifacts > 0`) and one artifact was unusable — the state §3.4 measures 8
    times across 5 PRs, and the state the doc's headline "the window is PARTIAL
    and `validate` exits 2" depends on. Everything else is healthy here
    (`runs_seen`/`test_legs_seen` set, a `test*` leg present, the failure in
    model), so exit 0 is the only other possible answer: delete `errors or` at
    the gate and this test goes red, while the legacy stub — which also forced 2
    through `unseen`/`unassessed`/`unread` — stayed green.
    """
    from tools import ci_import_closure as ci

    rc = _stub_validate(monkeypatch, ci, _truth(
        jobs=["test (a)"], artifacts=1,
        artifact_errors=["run 1: pytest-log-test (a) uploaded no junit"],
    ), cache=tmp_path)
    out = capsys.readouterr().out
    assert "PARTIAL" in out and "unreadable" in out
    assert rc == 2, (
        "a partly-read red leg can only UNDER-count misses, so the window is "
        "PARTIAL — no other arm of the gate can catch it")


def test_validate_exits_two_when_a_pr_yielded_no_check_run(monkeypatch, capsys,
                                                           tmp_path):
    """P2: `runs_seen == 0` must be PARTIAL, not a green PR."""
    from tools import ci_import_closure as ci
    rc = _stub_validate(monkeypatch, ci, _truth(runs_seen=0, test_legs_seen=0),
                        cache=tmp_path)
    assert rc == 2, "a PR nothing was read for must not certify the window"
    out = capsys.readouterr().out
    assert "PARTIAL" in out and "NO check run" in out


def test_validate_exits_two_when_a_failure_falls_outside_the_modelled_selection(
    monkeypatch, capsys, tmp_path,
):
    """P2: `not_in_current` was an ABSOLUTION, and it is only sound when the
    model covers every leg the workflow runs.

    A failing file the modelled selection does not contain was reported on one
    console line and then dropped from every condition, so `validate` printed
    `TOTAL MISSES 0` and exited 0 on a window whose real failure it had just
    declared out of scope. `ALWAYS_ON_LEG_FILES` now covers the legs that can be
    modelled from a hand-list; whatever is left is a hole in the harness's model
    (`test-d14-hosted-api`'s marker selection is deliberately not guessed at),
    and a hole means PARTIAL, never clean.
    """
    from tools import ci_import_closure as ci
    rc = _stub_validate(monkeypatch, ci, _truth(
        failed_files=["tests/test_not_selected_anywhere.py"],
        jobs=["test (a)"], artifacts=1,
    ), cache=tmp_path)
    assert rc == 2, "an unmodelled failure must not certify the window"
    out = capsys.readouterr().out
    assert "PARTIAL" in out and "fall outside the modelled selection" in out


def test_an_always_on_legs_failure_is_scored_not_absolved(monkeypatch, capsys,
                                                          tmp_path):
    """P2: the always-on legs are unioned into today's selection — AND into the
    derived one, which is where the shipped code dropped them (F2 at `a746139ba`).

    `test-record-postgres` runs four files on EVERY PR (`python-ci.yml:3733`, the
    literals at `:3819-3822`), and the harness reads its junit as ground truth. A
    docs-only or `tools/`-only diff takes the tier-1 early return, so those files
    were outside `current_rel` before — and `misses_for` called the failure
    `not_in_current`, i.e. it blamed the existing selector for a test the selector
    DID run and left the derivation unscored. Measured reachability:
    `test_9555_narrative_carrier` is on `api` + `core` but NOT on `tier1`.

    ⛔ The synthetic world here is the REAL manifest's shape — the file IS a
    `core` member WITH a closure of its own — because that is what made the first
    version of this pin a false friend: `base_rel = current_rel - core_rel`
    subtracts every `core` member, so with the file outside `core` (or absent from
    `closures`, which `derived_core_selection` keeps) the subtraction never ran and
    the pin certified behaviour that does not ship. Verified non-vacuous: with
    `base_rel = current_rel - core_rel` restored this pin returns exit 1 and
    prints `MISS[naive] pr#1 test_9555_narrative_carrier.py`.
    """
    import argparse

    from tools import ci_import_closure as ci
    always_on = "tests/test_9555_narrative_carrier.py"
    assert always_on in ci.ALWAYS_ON_LEG_FILES
    # A docs-only/workflow-only diff: `changed_modules` is empty, so nothing in
    # the derivation can select the file — only the unconditional leg runs it.
    changed = [".github/workflows/ci.yml"]
    idx, scans, _closures = _cli_world()
    scans = dict(scans)
    scans[always_on] = _scan(idx, always_on, "import tortoise.base\n")
    closures = compute_closures(scans, idx, ["tests/test_t.py", always_on])
    data = {"scans": scans, "closures": closures,
            "core": ["tests/test_t.py", always_on]}
    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))
    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ci, "_selection_module",
                        lambda: _stub_selection_module())
    monkeypatch.setattr(ci, "merged_pr_numbers", lambda repo, window: [1])
    monkeypatch.setattr(ci, "_gh_json", lambda argv: {"head": {"sha": "deadbeef"}})
    monkeypatch.setattr(ci, "changed_files", lambda repo, pr, cache, head_sha=None: changed)
    monkeypatch.setattr(ci, "pr_ground_truth", lambda repo, sha, cache: _truth(
        failed_files=[always_on],
        jobs=["test-record-postgres"], artifacts=1,
    ))
    args = argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[], import_time_only=False,
    )
    rc = ci.cmd_validate(args)
    out = capsys.readouterr().out
    assert "fall outside the modelled selection" not in out, (
        "an always-on leg's file is IN the model, so it must not read as a hole")
    assert "outside today's selection" not in out, (
        "the leg runs it on every PR, so the existing selector DID run it — "
        "scoring it `not_in_current` blamed the selector for the model's gap")
    assert rc != 2, (
        "a failure the harness can place must not turn the window PARTIAL")
    # Both sides select it: it is in `current`, and `(current - core) | always_on`
    # keeps it in `derived` too — which is the honest result, because the
    # derivation narrows `core`, it does not remove an unconditional leg.
    assert rc == 0 and "MISS[" not in out, (
        "a `core` always-on leg must stay in the DERIVED set too: subtracting it "
        "with the rest of `core` scored an unconditional leg's failure as a "
        "derivation miss (#10143 review at `a746139ba`, F2)")


def test_the_window_ranks_by_merge_time_and_stops_once_the_cutoff_passes(
    monkeypatch,
):
    """P3 at `a746139ba`: the window ranked by CREATION time.

    `/pulls` has no merge-time ordering (`created` is the default, and
    `sort=merged` is ignored — measured 2026-10-29 it returns the `created` order
    verbatim), so `/pulls?state=closed&sort=created` + "keep the first `window`
    merged" silently omits PRs that merged AFTER entries it contains: measured 11
    of them, e.g. #10108 created 2026-10-26 and merged 2026-10-28. A merge IS an
    update, so the walk now asks by `updated` and ranks the merged set by
    `merged_at`, stopping once the page's oldest `updated_at` precedes the oldest
    `merged_at` the window holds.

    Page 1 here is creation-new/merge-old and page 2 is creation-old/merge-new, so
    the two orders disagree: the old implementation returns [100, 101].
    """
    import re

    from tools import ci_import_closure as ci

    pages = {
        1: [  # newest-CREATED, but merged early
            {"number": 100, "merged_at": "2026-10-01T00:00:00Z",
             "updated_at": "2026-10-05T00:00:00Z"},
            {"number": 101, "merged_at": "2026-10-02T00:00:00Z",
             "updated_at": "2026-10-06T00:00:00Z"},
        ],
        2: [  # older-created, but these merged LAST
            {"number": 102, "merged_at": "2026-10-09T00:00:00Z",
             "updated_at": "2026-10-09T00:00:00Z"},
            {"number": 103, "merged_at": "2026-10-08T00:00:00Z",
             "updated_at": "2026-10-08T00:00:00Z"},
            {"number": 104, "merged_at": "2026-10-07T00:00:00Z",
             "updated_at": "2026-10-07T00:00:00Z"},
        ],
    }
    asked: list[tuple[int, bool]] = []

    def fake_gh(argv):
        url = argv[1]
        asked.append((int(re.search(r"&page=(\d+)", url).group(1)),
                      "sort=updated" in url))
        return pages.get(asked[-1][0], [])

    monkeypatch.setattr(ci, "_gh_json", fake_gh)
    assert ci.merged_pr_numbers("o/r", 2) == [102, 103], (
        "the window is the newest MERGED PRs — creation order is not merge order")
    assert [page for page, _ in asked] == [1, 2], (
        "paging must stop once the newest two merges cannot be displaced: page "
        "3 is unreachable and asking for it is a wasted round trip")
    assert all(by_update for _, by_update in asked), (
        "`/pulls` has no merge-time sort, so the only order a merge MOVES is "
        "`updated` — asking by `created` is the defect this pin is about")


# ---------------------------------------------------------------------------
# #10143 review round at `3934648ae`: the fourth fresh-context round.
#
# Two classes of defect, both of the kind that certifies a window it never read:
#   * paths that DROP a red leg's ground truth without recording it
#     (no run id, zero-testcase junit, an unattributed `test*` leg, an empty
#     `core` surface, a cache keyed without the repo), and
#   * pins whose SUBJECT was degenerate, so the shipped bug and the fix agreed by
#     accident (the classname fallback's nesting, `cmd_validate`'s exit 1, the
#     `not TYPE_CHECKING` branch, a decorator's import-time edge).
#
# Each test below fails on the code as it stood at `3934648ae`.
# ---------------------------------------------------------------------------
def test_validate_exits_one_when_the_derivation_really_misses(
    monkeypatch, capsys, tmp_path,
):
    """P1: NOTHING pinned `cmd_validate` returning 1 — its only positive signal.

    Every CLI assertion was `== 0` or `== 2`, so replacing
    `return 0 if total_naive == 0 and total_safe == 0 else 1` with `return 0`
    left all 53 tests green while a window with real misses certified as clean.
    The `misses_for` unit tests do not cover it: they never travel through the row
    loop, the totals, or the exit code.
    """
    from tools import ci_import_closure as ci

    rc = _stub_validate(
        monkeypatch, ci,
        _truth(failed_files=["tests/test_t.py"], jobs=["test (a)"], artifacts=1),
        # `tortoise/hub.py` is OUTSIDE `test_t`'s closure, and today's selector
        # DID run `test_t` — so this is a genuine miss of the derivation, not an
        # out-of-model failure.
        changed=("tortoise/hub.py",),
        select_files=["test_t.py"],
        cache=tmp_path,
    )
    out = capsys.readouterr().out
    assert "MISS[naive] pr#1 test_t.py" in out, out   # tests/-relative, like `select()`
    assert rc == 1, "a real miss must exit 1 — never 0"


def test_junit_classname_fallback_resolves_a_NESTED_module(monkeypatch, tmp_path):
    """P1: the longest-first loop was never exercised by its own pin.

    Both shipped cases (`tests.test_x`, `tests.test_x.TestX`) resolve on the FIRST
    candidate, so a naive `"/".join(parts[:-1]) + ".py"` — the bug's exact shape,
    one component short — passed them and mis-resolved every genuinely nested test
    file, which under-counts misses. The `harness.py` decoy is what makes the
    precedence load-bearing: a short-split implementation returns it instead.
    """
    from tools import ci_import_closure as module

    (tmp_path / "tests/eval/harness").mkdir(parents=True)
    (tmp_path / "tests/eval/harness/test_x.py").write_text("def test_a(): pass\n")
    (tmp_path / "tests/eval/harness.py").write_text("")
    monkeypatch.setattr(module, "REPO", tmp_path)
    xml = _junit(
        '<testcase classname="tests.eval.harness.test_x" name="test_a">'
        '<failure message="boom">trace</failure></testcase>'
    )
    assert parse_junit_failures(xml)["files"] == ["tests/eval/harness/test_x.py"]


def test_ground_truth_records_a_red_leg_with_no_run_id(monkeypatch, tmp_path):
    """P1: `if rid:` had no `else`, so an unattributable red leg VANISHED.

    It never reached `run_ids`, so its artifacts were never read AND
    `artifact_errors` stayed empty; beside a readable sibling red leg,
    `artifacts > 0` also disabled the unread-PR guard, so a window could exit 0
    over a leg whose ground truth was never opened.
    """
    from tools import ci_import_closure as module
    monkeypatch.setattr(module, "_check_runs", lambda repo, sha: [
        {"name": "test (a)", "conclusion": "failure", "id": 99,
         "details_url": "https://example.invalid/not-an-actions-url"}])
    monkeypatch.setattr(module, "_artifacts", lambda repo, run_id: [])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["jobs"] == ["test (a)"]
    assert any("no Actions run id" in e for e in truth["artifact_errors"]), truth
    assert not _truth_cache(tmp_path, "deadbeef").exists(), (
        "a leg nothing was read for must not be cached as truth")


def test_ground_truth_records_a_junit_with_zero_testcases(monkeypatch, tmp_path):
    """P2: `executed` was computed by the parser and NEVER read by the caller.

    A red leg whose junit described zero `<testcase>` elements — the suite did not
    run: collection aborted, a marker selected nothing, exit 5 — parsed as clean,
    `artifact_errors` stayed empty, the row read "job red, no test-level failure"
    (the genuine #10070 zero-failure shape) and the truncated truth was cached.
    """
    from tools import ci_import_closure as module
    _fake_ground_truth(monkeypatch, module, junit=b"<testsuite/>",
                       listing_names=["pytest-log-test-a"])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert any("0 testcases" in e for e in truth["artifact_errors"]), truth
    assert not _truth_cache(tmp_path, "deadbeef").exists()


def test_a_zero_testcase_junit_is_recorded_per_LEG_not_masked_by_its_SIBLING(
    monkeypatch, tmp_path,
):
    """#10143 review P3: the zero-testcase check was a PER-RUN aggregate.

    `rid` is the workflow RUN and `selected` is one artifact per RED LEG in it
    (`test`, `test-slow`, `test-carve-out` and the always-on legs are matrix jobs
    inside ONE run). Summing `executed` across the run meant a leg whose junit
    described ZERO `<testcase>` elements — a collection abort, `exit 5` — was
    masked by a sibling leg in the same run that described 500: `executed_total`
    was non-zero, no `artifact_errors` entry was written, the truncated truth was
    CACHED, and `cmd_validate` could return 0 or 1 over a window whose first
    leg's ground truth was never read.

    That is the sibling-artifact masking this PR already fixed one granularity
    up, left in its COUNTING form. On the pre-fix code this test fails on both
    counts: no error entry AND the truncated truth is written to the cache.
    """
    from tools import ci_import_closure as module

    # TWO red legs, ONE run (99) — the ordinary matrix shape.
    legs = [
        {"name": "test (a)", "conclusion": "failure", "id": 1,
         "details_url": "https://github.com/o/r/actions/runs/99/job/1"},
        {"name": "test (b)", "conclusion": "failure", "id": 2,
         "details_url": "https://github.com/o/r/actions/runs/99/job/2"},
    ]
    monkeypatch.setattr(module, "_check_runs", lambda repo, sha: legs)
    monkeypatch.setattr(module, "_artifacts", lambda repo, rid: [
        {"id": 7, "name": "pytest-log-test-a"},
        {"id": 8, "name": "pytest-log-test-b"},
    ])

    good = _junit(
        '<testcase classname="tests.test_b" name="t1" file="tests/test_b.py"/>',
        '<testcase classname="tests.test_b" name="t2" file="tests/test_b.py"/>',
        '<testcase classname="tests.test_b" name="t3" file="tests/test_b.py"/>',
    )
    # `_download_artifact` hands back a ZIP; leg (a) described ZERO testcases.
    blobs = {7: _zip_of(b"<testsuite/>"), 8: _zip_of(good)}
    monkeypatch.setattr(module, "_download_artifact",
                        lambda repo, aid, cache: _write_tmp(cache, aid, blobs[aid]))

    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert any("0 testcases" in e and "pytest-log-test-a" in e
               for e in truth["artifact_errors"]), (
        "leg (a)'s empty junit must be recorded against ITS OWN artifact, not "
        f"masked by leg (b)'s 3 testcases: {truth['artifact_errors']}")
    assert truth["artifacts"] == 2, truth
    assert not _truth_cache(tmp_path, "deadbeef").exists(), (
        "a window whose first leg was never read must never be cached")


def test_a_zero_testcase_junit_records_the_LEG_and_the_RUN_when_both_are_empty(
    monkeypatch, tmp_path,
):
    """The control for the pin above: when EVERY selected leg is empty this is a
    stronger, run-level statement and it must still be made — the per-leg fix
    narrows the check, it does not replace it."""
    from tools import ci_import_closure as module
    monkeypatch.setattr(module, "_check_runs", lambda repo, sha: [
        {"name": "test (a)", "conclusion": "failure", "id": 1,
         "details_url": "https://github.com/o/r/actions/runs/99/job/1"},
        {"name": "test (b)", "conclusion": "failure", "id": 2,
         "details_url": "https://github.com/o/r/actions/runs/99/job/2"},
    ])
    monkeypatch.setattr(module, "_artifacts", lambda repo, rid: [
        {"id": 7, "name": "pytest-log-test-a"},
        {"id": 8, "name": "pytest-log-test-b"},
    ])
    monkeypatch.setattr(module, "_download_artifact",
                        lambda repo, aid, cache: _write_tmp(
                            cache, aid, _zip_of(b"<testsuite/>")))

    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert any("run 99" in e for e in truth["artifact_errors"]), (
        f"every leg's junit was empty — record the run: {truth['artifact_errors']}")
    assert not _truth_cache(tmp_path, "deadbeef").exists()


def test_ground_truth_scopes_each_red_leg_to_its_own_run(monkeypatch, tmp_path):
    """P2: `failed_jobs` was GLOBAL across runs while the listing is PER RUN.

    A red leg in run A was re-checked against run B's artifact listing, so an
    honest two-workflow window (a red `python-ci` `test*` leg beside a red
    `ci.yml` job — different RUNS) recorded a substitution entry for a leg whose
    own junit was right there. A fail-closed check that always fires carries no
    information.
    """
    import io
    import zipfile

    from tools import ci_import_closure as module

    monkeypatch.setattr(module, "_check_runs", lambda repo, sha: [
        {"name": "test (a)", "conclusion": "failure", "id": 1,
         "details_url": "https://github.com/o/r/actions/runs/11/job/1"},
        {"name": "test-isolation", "conclusion": "failure", "id": 2,
         "details_url": "https://github.com/o/r/actions/runs/22/job/2"},
    ])
    listings = {
        11: [{"id": 7, "name": "pytest-log-test-a"}],
        22: [{"id": 8, "name": "isolation-report"}],
    }
    monkeypatch.setattr(module, "_artifacts", lambda repo, rid: listings[rid])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("junit.xml", _junit(
            '<testcase classname="tests.test_a" name="t1" file="tests/test_a.py">'
            '<failure message="boom">trace</failure></testcase>'))
    blob = buf.getvalue()
    monkeypatch.setattr(module, "_download_artifact",
                        lambda repo, aid, cache: _write_tmp(cache, aid, blob))

    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert not any("test (a)" in e for e in truth["artifact_errors"]), (
        "run 11's leg uploaded its OWN junit — checking it against run 22's "
        f"listing is the cross-run bug: {truth['artifact_errors']}")
    assert any("test-isolation" in e for e in truth["artifact_errors"]), (
        "run 22's leg genuinely carries no `pytest-log-*`, so it is an UNREAD leg "
        "and must still be recorded — the fix narrows the check, it does not "
        "silence it")


def test_the_artifacts_listing_is_paginated(monkeypatch):
    """P2: `?per_page=100` with no `--paginate` truncated the listing at 100.

    A red leg's artifact beyond page 1 then looked ABSENT, which fires the
    sibling-substitution fallback — substituting a clean sibling's junit for the
    red leg's own. `_check_runs` already paginated; this path did not.
    """
    from tools import ci_import_closure as module
    seen: list[list[str]] = []

    def _run_gh(argv):
        seen.append(list(argv))
        return (b'{"id": 1, "name": "pytest-log-test-a"}\n'
                b'{"id": 2, "name": "pytest-log-test-b"}\n')

    monkeypatch.setattr(module, "_run_gh", _run_gh)
    arts = module._artifacts("o/r", 99)
    assert [a["name"] for a in arts] == ["pytest-log-test-a", "pytest-log-test-b"]
    assert "--paginate" in seen[0], "the listing must ask for every page"


def test_the_diff_cache_is_keyed_by_the_head_sha(monkeypatch, tmp_path):
    """P2: keyed by the PR alone, so a MOVED head was scored on the OLD diff.

    `pr_ground_truth` is keyed by sha precisely because a cache that can only
    HIDE a miss is the failure this harness exists to prevent (§3.3). The diff
    cache had the mirror-image defect: `GET /pulls/{pr}/files` has no ref
    selector (its only params are `pull_number`, `per_page`, `page`), so the list
    it returns belongs to the PR's CURRENT head — cached under `files-…-<pr>`,
    head A's list was read back at head B and paired with B's ground truth. The
    derived set is then computed over a diff the truth never saw: it can only
    UNDER-count misses, records no `artifact_errors` entry, and the row exits
    clean. On the pre-fix code the second call below returns head A's list.
    """
    from tools import ci_import_closure as module

    fetched: list[str] = []

    def _run_gh(argv):
        fetched.append(argv[1])
        return f"tortoise/head{len(fetched)}.py\n".encode()

    monkeypatch.setattr(module, "_run_gh", _run_gh)

    first = module.changed_files("o/r", 7, tmp_path, head_sha="aaaa")
    assert first == ["tortoise/head1.py"]
    # The SAME head is served from the cache — the cache still works.
    assert module.changed_files("o/r", 7, tmp_path, head_sha="aaaa") == first
    assert len(fetched) == 1, "a re-run at the same head must not re-fetch"

    second = module.changed_files("o/r", 7, tmp_path, head_sha="bbbb")
    assert second == ["tortoise/head2.py"], (
        "the PR moved to head `bbbb`; head `aaaa`'s file list describes a diff "
        "this head's ground truth was never produced from")
    assert len(fetched) == 2, "a moved head must re-fetch"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "files-o__r-aaaa.json", "files-o__r-bbbb.json"], (
            "the key is the head the list describes — never the PR number, which "
            "names a stream of different trees")


def test_a_diff_is_never_cached_under_a_key_that_names_no_head(monkeypatch,
                                                             tmp_path):
    """P2: no sha means no identity — so nothing is written, not even a PR key.

    A fallback to `files-<repo>-<pr>.json` on the un-keyed path would reinstate
    exactly the stale read the sha key removes, one branch further in. The
    fail-closed answer is to leave the list uncached.
    """
    from tools import ci_import_closure as module

    fetched: list[str] = []

    def _run_gh(argv):
        fetched.append(argv[1])
        return b"tortoise/lazy.py\n"

    monkeypatch.setattr(module, "_run_gh", _run_gh)

    assert module.changed_files("o/r", 7, tmp_path) == ["tortoise/lazy.py"]
    assert module.changed_files("o/r", 7, tmp_path) == ["tortoise/lazy.py"]
    assert len(fetched) == 2, "with no head sha the list cannot be cached"
    assert list(tmp_path.iterdir()) == [], (
        f"an un-keyed list was cached anyway: {list(tmp_path.iterdir())}")


def test_validate_hands_the_diff_the_head_it_keyed_the_TRUTH_on(monkeypatch,
                                                                tmp_path):
    """P2: the helper's key is only as good as the CALL SITE's argument.

    Direction-aware on purpose: `cmd_validate` fetches `meta["head"]["sha"]` to
    key the ground truth, then reads the diff. If it reads the diff without
    handing that sha over, the two describe different trees and nothing records
    it. The invariant asserted is not "the argument exists" but "the diff's head
    IS the truth's head".
    """
    import argparse

    from tools import ci_import_closure as ci

    idx, scans, closures = _cli_world()
    data = {"scans": scans, "closures": closures, "core": ["tests/test_t.py"]}
    seen: dict[str, str | None] = {}

    def _changed(repo, pr, cache, head_sha=None):
        seen["diff_sha"] = head_sha
        return ["tortoise/lazy.py"]

    def _ground_truth(repo, sha, cache):
        seen["truth_sha"] = sha
        return _truth()

    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))
    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ci, "_selection_module", _stub_selection_module)
    monkeypatch.setattr(ci, "merged_pr_numbers", lambda repo, window: [1])
    monkeypatch.setattr(ci, "_gh_json",
                        lambda argv: {"head": {"sha": "headB"}})
    monkeypatch.setattr(ci, "changed_files", _changed)
    monkeypatch.setattr(ci, "pr_ground_truth", _ground_truth)

    ci.cmd_validate(argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[], import_time_only=False,
    ))
    assert seen.get("truth_sha") == "headB"
    assert seen.get("diff_sha") == seen["truth_sha"], (
        f"the diff was read for {seen.get('diff_sha')!r} while the ground truth "
        f"is {seen['truth_sha']!r} — a mismatch that can only HIDE a miss")


def test_the_ground_truth_cache_is_keyed_by_repo(monkeypatch, tmp_path):
    """P2: keyed on the sha alone, so `--repo fork/x` reused upstream's truth.

    `--repo` is a first-class flag; PR numbers collide across repos and a fork
    shares the commit sha, so a diff from one repo was paired with the ground
    truth of another — a state that can only HIDE a miss, records no error, and
    was then cached.
    """
    import io
    import zipfile

    from tools import ci_import_closure as module

    # The truth must be CACHEABLE for this pin to mean anything: a red leg whose
    # own junit parses clean, so `artifact_errors` stays empty and the result is
    # written. (Counting reads with an uncacheable truth is degenerate — nothing is
    # written, so every call re-reads and the pin cannot fail.)
    junit_for = {
        "o/r": _junit('<testcase classname="tests.test_a" name="t1" '
                      'file="tests/test_a.py"><failure message="boom">t</failure>'
                      '</testcase>'),
        "fork/r": _junit('<testcase classname="tests.test_b" name="t1" '
                         'file="tests/test_b.py"><failure message="boom">t</failure>'
                         '</testcase>'),
    }
    calls: list[str] = []

    def _check_runs(repo, sha):
        calls.append(repo)
        return [{"name": "test (a)", "conclusion": "failure", "id": 99,
                 "details_url": "https://github.com/o/r/actions/runs/99/job/1"}]

    def _download(repo, aid, cache):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("junit.xml", junit_for[repo])
        return _write_tmp(cache, aid, buf.getvalue())

    monkeypatch.setattr(module, "_check_runs", _check_runs)
    monkeypatch.setattr(module, "_run_id", lambda r: 99)
    monkeypatch.setattr(module, "_artifacts",
                        lambda repo, rid: [{"id": 7 if repo == "o/r" else 8,
                                            "name": "pytest-log-test-a"}])
    monkeypatch.setattr(module, "_download_artifact", _download)

    first = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    second = module.pr_ground_truth("fork/r", "deadbeef", tmp_path)
    assert first["failed_files"] == ["tests/test_a.py"]
    assert second["failed_files"] == ["tests/test_b.py"], (
        "the fork reused the upstream repo's cached TRUTH for the same sha — its "
        "own head was never read")
    assert calls == ["o/r", "fork/r"], (
        "the second repo must READ again instead of reusing the first repo's cache")


def test_truth_is_not_cached_when_no_test_leg_was_seen(monkeypatch, tmp_path):
    """P1: `test_legs_seen == 0` was recorded for exactly this rule and gated nowhere.

    Runs exist but no `test*` leg among them, so nothing was assessed. Caching it
    made a re-trigger at the same sha short-circuit on the emptiness instead of
    re-reading it — and `cmd_validate` scored the row as a clean PR.
    """
    from tools import ci_import_closure as module
    monkeypatch.setattr(module, "_check_runs", lambda repo, sha: [
        {"name": "lint", "conclusion": "success", "id": 1, "details_url": ""}])
    monkeypatch.setattr(module, "_artifacts", lambda repo, rid: [])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["runs_seen"] == 1 and truth["test_legs_seen"] == 0
    assert not _truth_cache(tmp_path, "deadbeef").exists()


def test_validate_exits_two_when_no_test_leg_was_assessed(
    monkeypatch, capsys, tmp_path,
):
    """P1: `runs_seen > 0` with `test_legs_seen == 0` was a fully green window.

    Every row then has `jobs: []`, `failed_jobs: []`, `artifacts: 0`,
    `artifact_errors: []` — which `unseen` cannot see (it reads `runs_seen`) and
    `unread` cannot see (it requires `failed_jobs`). Reachable whenever the leg
    names change in `python-ci.yml`, or a PR's only check runs are lint/typecheck
    legs.
    """
    from tools import ci_import_closure as ci
    rc = _stub_validate(monkeypatch, ci, _truth(runs_seen=3, test_legs_seen=0),
                        cache=tmp_path)
    out = capsys.readouterr().out
    assert rc == 2 and "NO `test*` leg" in out, out


def test_load_all_flags_an_empty_core_surface(monkeypatch):
    """P1 producer: a renamed/absent `core` key must be FLAGGED, not derived over.

    `manifest["surfaces"].get("core") or []` turned a missing key into an empty
    surface: `unresolved_core` stayed empty, `core_rel` empty, `derived = current`
    (nothing is ever dropped), so every real failure was `covered` and the window
    reported "TOTAL MISSES 0" — a clean window certified by an analysis that
    examined nothing.
    """
    from types import SimpleNamespace

    from tools import ci_import_closure as module

    stub = SimpleNamespace(
        load_manifest=lambda: {"surfaces": {}},
        _surface_members=lambda members: list(members or []),
        TESTS_DIR=Path(__file__).resolve().parent,
    )
    monkeypatch.setattr(module, "_selection_module", lambda: stub)
    monkeypatch.setattr(module, "_git_py_files", lambda root: [])
    _manifest, data, _index = module._load_all()
    assert data["core"] == []
    assert data["core_empty"] is True
    assert module._refuse_an_empty_core(data) is True


def test_validate_refuses_an_empty_core_surface(monkeypatch, capsys, tmp_path):
    """P1 consumer: the verb that PUBLISHES the miss counts must refuse."""
    import argparse

    from tools import ci_import_closure as ci

    idx, scans, closures = _cli_world()
    data = {"scans": scans, "closures": closures, "core": [], "core_empty": True}
    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))
    monkeypatch.setattr(ci, "_selection_module", _stub_selection_module)
    monkeypatch.setattr(ci, "merged_pr_numbers", lambda repo, window: [1])
    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path)

    args = argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[], import_time_only=False,
    )
    assert ci.cmd_validate(args) == 2
    assert "ZERO test files" in capsys.readouterr().out


def test_a_not_type_checking_guard_is_a_RUNTIME_edge():
    """P2: `if not TYPE_CHECKING:` is the runtime branch, not a type-only one.

    `ast.walk` found the `TYPE_CHECKING` name anywhere in the test, so the negated
    form — the standard idiom for a runtime import a type checker cannot see — was
    classified as type-only, and `--exclude-type-checking` then DELETED a
    genuinely runtime edge. That is the same axis inversion the `_dedupe_deps` fix
    removed: a reader must never drop the other axis's edges.
    """
    idx = _index("tortoise/lazy.py")
    scan = _scan(
        idx, "tests/test_a.py",
        "from typing import TYPE_CHECKING\n"
        "if not TYPE_CHECKING:\n"
        "    from tortoise.lazy import thing\n"
        "    thing\n",
    )
    deps = {d.target: d for d in scan.deps}
    assert "tortoise.lazy" in deps
    assert deps["tortoise.lazy"].type_checking is False, (
        "a runtime edge must not be tagged type-only — `--exclude-type-checking` "
        "would delete it")


def test_a_decorator_and_a_default_are_import_time_edges():
    """P2: decorators and defaults execute where the `def` runs — at import.

    `child_fn` was applied to EVERY child of a `FunctionDef`, so an import inside a
    decorator or a default value was tagged function-local and
    `--import-time-only` dropped it. That is the reader this tool says it ships to
    expose the trap, so an under-selection there skews the number the flag exists
    to publish.
    """
    idx = _index("tortoise/lazy.py", "tortoise/base.py", "tortoise/other.py")
    decorated = _scan(
        idx, "tests/test_a.py",
        "import importlib\n"
        "@importlib.import_module('tortoise.lazy')\n"
        "def f():\n"
        "    pass\n",
    )
    assert [(d.target, d.in_function) for d in decorated.deps] == [
        ("tortoise.lazy", False)], (
        "a decorator evaluates at module import time, not when the function is "
        "called")

    defaulted = _scan(
        idx, "tests/test_b.py",
        "import importlib\n"
        "def g(x=importlib.import_module('tortoise.base')):\n"
        "    from tortoise.other import y\n"
        "    return x, y\n",
    )
    assert {(d.target, d.in_function) for d in defaulted.deps} == {
        ("tortoise.base", False), ("tortoise.other", True)}, (
        "the default is import-time; only the BODY is function-local")


def test_a_dynamic_import_in_a_type_checking_guard_test_is_not_dropped():
    """P3: the guard's TEST is evaluated at runtime — it must be walked.

    `_nodes_with_context` pushed the guard's body and `orelse` and then
    `continue`d, so `node.test` never entered the walk — and `_scan_dynamic`
    walks the same generator, so a dynamic import inside a guard's test expression
    was invisible to BOTH walkers: no edge, no `UNKNOWN`, in a function whose
    stated contract is "anything the analysis CANNOT resolve is reported as
    `UNKNOWN` … and never dropped". This is the aliased-`importlib` hole one code
    path away.

    Both operators are pinned, because they fail for DIFFERENT reasons and only
    together prove the walk: the `or` form is also a real runtime import (it must
    not be tagged type-only at all), while the `and` form is still a type-only
    guard whose TEST must nevertheless be read.
    """
    idx = _index("tortoise/lazy.py")
    disjunction = _scan(
        idx, "tests/test_a.py",
        "import importlib\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING or importlib.import_module('tortoise.lazy'):\n"
        "    pass\n",
    )
    deps = {d.target: d for d in disjunction.deps}
    assert "tortoise.lazy" in deps, (
        "a dynamic import in a guard's test expression was silently dropped — "
        "no edge and no UNKNOWN")
    assert deps["tortoise.lazy"].type_checking is False, (
        "with a runtime operand the `or` may be true at runtime, so the import "
        "is a runtime dependency — `--exclude-type-checking` would delete it")

    conjunction = _scan(
        idx, "tests/test_b.py",
        "import importlib\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING and importlib.import_module('tortoise.lazy'):\n"
        "    pass\n",
    )
    assert "tortoise.lazy" in {d.target for d in conjunction.deps}, (
        "the guard is type-only, but its TEST is still code the walker must read "
        "— pushing only body/orelse dropped it")


def test_a_disjunction_with_a_runtime_operand_is_not_a_type_only_guard():
    """P3: `if TYPE_CHECKING or <runtime term>:` is NOT a type-only block.

    `_is_type_checking_guard` walked the test for the NAME, so any expression
    that merely contained `TYPE_CHECKING` classified its body as type-only — and
    `--exclude-type-checking` then DELETED a body of genuinely runtime edges. That
    is the same under-selection the `if not TYPE_CHECKING:` fix removed, one
    operator over. What decides the polarity is the OPERATOR, not a name
    occurring anywhere in the test: an `and` chain containing the guard is
    false-always at runtime (type-only, whatever the other operands are), an `or`
    chain is not unless EVERY operand is, and an expression that only mentions the
    name (a call, a comparison) decides nothing — so it is not tagged.
    """
    idx = _index("tortoise/lazy.py")

    def kind(src: str) -> bool:
        scan = _scan(idx, "tests/test_a.py", src)
        deps = {d.target: d for d in scan.deps}
        return deps["tortoise.lazy"].type_checking

    body = "    from tortoise.lazy import thing\n"
    # `or` with a runtime operand: the body CAN run at runtime → runtime edge.
    assert kind("from typing import TYPE_CHECKING\nimport os\n"
                "if TYPE_CHECKING or os.environ.get('X'):\n" + body) is False
    # `and`: false-always at runtime → still type-only (precision counter-pin).
    assert kind("from typing import TYPE_CHECKING\nimport os\n"
                "if TYPE_CHECKING and os.environ.get('X'):\n" + body) is True
    # a bare `or` of two guards is still false-always at runtime.
    assert kind("from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING or TYPE_CHECKING:\n" + body) is True
    # merely MENTIONING the name decides nothing about runtime truth.
    assert kind("from typing import TYPE_CHECKING\n"
                "if bool(TYPE_CHECKING):\n" + body) is False


def test_the_truth_cache_shape_is_pinned_to_its_version(monkeypatch, tmp_path):
    """P3: the version constant must move when the EXTRACTION's shape moves.

    `GROUND_TRUTH_VERSION`'s own comment states the invariant — "a truth written
    by an older extractor is a CACHED TRUNCATION, and `pr_ground_truth`
    short-circuits on `cached.exists()`" — and three commits changed the ground
    truth's extraction while the constant stayed at 3. The pin is the COUPLING, not
    the number: the shape `pr_ground_truth` writes is recorded per version, so
    adding or removing a truth key without a bump FAILS, and bumping without
    recording the new shape fails too. (The version is also in the cache FILENAME,
    which is what makes a bump invalidate every existing file at once.)
    """
    import json

    from tools import ci_import_closure as module

    shape_by_version = {
        # v4: the per-run leg scoping + the `executed`/zero-testcase gate.
        4: frozenset({
            "failed_files", "failed_tests", "jobs",
            "job_failed_without_test_failure", "artifacts",
            "runs_seen", "test_legs_seen", "artifact_errors",
        }),
        # v5: the zero-testcase gate moved from PER RUN to PER ARTIFACT. The key
        # set is deliberately IDENTICAL — that is the whole reason this pin could
        # not see the change, and why the RULES are pinned separately by
        # `test_a_new_artifact_errors_producer_forces_a_ground_truth_version_bump`.
        5: frozenset({
            "failed_files", "failed_tests", "jobs",
            "job_failed_without_test_failure", "artifacts",
            "runs_seen", "test_legs_seen", "artifact_errors",
        }),
    }
    assert module.GROUND_TRUTH_VERSION in shape_by_version, (
        "the version moved without recording the shape it writes — record the "
        f"new key set for v{module.GROUND_TRUTH_VERSION} here")
    _fake_ground_truth(
        monkeypatch, module,
        junit=_junit('<testcase classname="tests.test_a" name="t1" '
                     'file="tests/test_a.py" line="1"/>'),
        listing_names=["pytest-log-test-a"])
    module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    cached = _truth_cache(tmp_path, "deadbeef")
    assert cached.exists(), (
        "a fully-read truth must be cached, or this pin asserts nothing")
    written = json.loads(cached.read_text())
    assert set(written) == shape_by_version[module.GROUND_TRUTH_VERSION], (
        "the ground truth's SHAPE changed — bump GROUND_TRUTH_VERSION and record "
        "the new key set, or every warm cache answers with the old rules forever")


def test_a_new_artifact_errors_producer_forces_a_ground_truth_version_bump():
    """P2: the truth's SHAPE was pinned; its RULES were not.

    `96a1cd871` changed what `pr_ground_truth` RECORDS — the zero-testcase gate
    moved from one entry per RUN to one per ARTIFACT — and added no truth key, so
    `test_the_truth_cache_shape_is_pinned_to_its_version` stayed green while a
    warm `truth-v4-*.json` written by the pre-fix reader kept being served as the
    answer. The extraction's whole contract is *which states produce an
    `artifact_errors` entry*, because that field alone makes the window PARTIAL
    and blocks the cache (`if not artifact_errors and check_runs and
    test_legs_seen: cached.write_text(...)`).

    So the PRODUCERS are pinned per version, not the key set: adding a state that
    can now be recorded unread, or removing one, fails here until the version
    moves AND the count is re-recorded. This is the `v1 → v2 → v3 → v4` history
    made mechanical — each of those bumps existed because a producer changed
    while the constant did not.

    Counted with `ast`, not a text search: `artifact_errors` appears ~30 times in
    this module as prose and as a read-only key, and a substring count would pin
    the comments. A refactor that moves an append into a helper should re-record
    the count deliberately (that is the point) rather than delete the pin.
    """
    import ast

    from tools import ci_import_closure as module

    producers_by_version = {
        # v5: the per-artifact zero-testcase entry is the ninth producer (the
        # per-run entry it sits beside is kept, deliberately not deduped).
        5: 9,
    }

    tree = ast.parse(Path(module.__file__).read_text())
    producers = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "artifact_errors"
    ]
    assert module.GROUND_TRUTH_VERSION in producers_by_version, (
        "the version moved without recording how many states it can record as "
        "unread — record the producer count for "
        f"v{module.GROUND_TRUTH_VERSION} here")
    assert len(producers) == producers_by_version[module.GROUND_TRUTH_VERSION], (
        f"pr_ground_truth now has {len(producers)} `artifact_errors` producers, "
        "but this version was recorded with "
        f"{producers_by_version[module.GROUND_TRUTH_VERSION]}: a WARM CACHE "
        "written by the previous reader reports `artifact_errors: []` for a "
        "state that is now unread, and `pr_ground_truth` short-circuits on "
        "`cached.exists()`, so a fixed extractor would keep answering the old "
        "question forever. Bump GROUND_TRUTH_VERSION and record the new count "
        "(bumping is what re-names every cache file and invalidates them at once)")


# ---------------------------------------------------------------------------
# #10143 review at head `de2ce5fc9`, D3: the doc promised "re-runs are offline
# and instant" at four sites, and the row loop broke the promise with one
# UNCACHED `gh api` call per PR — made BEFORE either cache was consulted. On a
# warm window that is still N network calls plus auth; with `gh` unavailable the
# first PR raises and the whole window dies with a traceback, after the caller
# was told the run was cached. The fix caches a MERGED PR's metadata (its head
# sha is immutable) and deliberately never caches an OPEN PR's (its head moves).
# ---------------------------------------------------------------------------
def test_validate_does_not_re_read_a_merged_prs_head(monkeypatch, tmp_path):
    """The offline promise, pinned at the CONSUMER — `cmd_validate` twice.

    Fails on the pre-fix code with the first assertion: `_gh_json` was called
    once per PR per run, so the second `cmd_validate` re-fetched the head (2
    calls). A helper-only pin would have missed that, which is the lesson this
    PR has had to learn three times — a correct cache protects nothing if the
    call site does not use it.
    """
    import argparse

    from tools import ci_import_closure as ci

    idx, scans, closures = _cli_world()
    data = {"scans": scans, "closures": closures, "core": ["tests/test_t.py"]}
    calls: list[list[str]] = []

    def _gh(argv):
        calls.append(argv)
        return {"number": 7, "merged_at": "2026-10-28T11:00:00Z", "state": "closed",
                "head": {"sha": "deadbeef"}, "updated_at": "2026-10-28T11:00:00Z"}

    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ci, "_load_all", lambda *a, **k: ({}, data, idx))
    monkeypatch.setattr(ci, "_selection_module",
                        lambda: _stub_selection_module([]))
    monkeypatch.setattr(ci, "_gh_json", _gh)
    monkeypatch.setattr(ci, "changed_files",
                        lambda repo, pr, cache, head_sha=None: [])
    monkeypatch.setattr(ci, "pr_ground_truth", lambda repo, sha, cache: _truth())
    args = argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[7], import_time_only=False,
    )

    ci.cmd_validate(args)
    assert len(calls) == 1, (
        "the first run reads the PR's head exactly once; a second call here "
        "means the cache dir is not the one being written")

    def _boom(argv):
        raise AssertionError(f"network call on a warm re-run: {argv}")

    monkeypatch.setattr(ci, "_gh_json", _boom)
    ci.cmd_validate(args)   # must not reach the network at all — that is the claim


def test_an_open_prs_head_is_never_cached(monkeypatch, tmp_path):
    """The cache must never freeze a head that MOVES.

    Guarding the D3 fix itself: caching by PR number alone would be the
    diff-cache defect one level in — a PR number names a stream of trees (§3.3),
    so a lane validating its own open PR would score the head it pushed two
    commits ago and read the result as this one. Only a MERGED PR (immutable
    head) is written. This pin passes both before and after the fix by design:
    it exists so a later "optimisation" that caches every PR is caught here.
    """
    from tools import ci_import_closure as ci

    heads = ["aaaa", "bbbb"]

    def _gh(argv):
        return {"number": 7, "merged_at": None, "state": "open",
                "head": {"sha": heads.pop(0)}}

    monkeypatch.setattr(ci, "_gh_json", _gh)
    assert ci.pr_meta("o/r", 7, tmp_path)["head"]["sha"] == "aaaa"
    assert ci.pr_meta("o/r", 7, tmp_path)["head"]["sha"] == "bbbb", (
        "an OPEN PR's head was served from the cache — the head moved, so the "
        "second call must re-read it")
    assert list(tmp_path.iterdir()) == [], (
        "an open PR's metadata was written under a key that cannot describe it")


# ---------------------------------------------------------------------------
# The PUBLISHED figures are anchored to a SHA, and re-derived from that sha
# ---------------------------------------------------------------------------
# ⛔ THE DEFECT THIS CLOSES (#10143 review P2, and the SAME class as #7948 §13).
# The document and the `00_index` row stated their figures "at the head of PR
# #10143 — the commit that adds this line". A figure tied to a MOVING ref carries
# no pin: the final `merge(10143)` landed four `core` registrations, four test
# files and one `tortoise/` module, and every count in §1 was then a snapshot of
# the PARENT tree (752 / 1 234 / 258) while the text claimed the head that
# carried it (756 / 1 238 / 259). Re-derived at the head: measured on the parent
# `64356c16` = 752 / 1 234 / 258 and at `427037b36` = 756 / 1 238 / 259, i.e.
# exactly one commit of drift, invisible because nothing re-derived it.
#
# Two arms, because the two halves fail differently:
#   1. TEXT — a count may not be stated against a moving ref; the basis must be
#      a sha. Always runs, in any clone.
#   2. TREE — every "N at `<sha>`" claim is re-derived from that sha's own tree
#      (`git show` / `git ls-tree`), so a hand-edited count reds instead of
#      rotting. SKIPS, with its reason, when the sha is not in the clone (a
#      `fetch-depth: 1` CI checkout, or a squash merge that drops the commit).
_DOC = Path(__file__).resolve().parents[1] / "docs" / "ci" / "core-import-closure.md"
_INDEX = Path(__file__).resolve().parents[1] / "docs" / "00_index.md"

#: The MOVING refs themselves — `at this PR's head`, `at the head of PR #…`, `at
#: the merge head`, `the commit that adds this line`. Written generally on
#: purpose: the two spellings this document actually used are only two of the
#: forms the class takes.
_MOVING_REF = (
    r"(?:\b(?:at|on)\s+[^.;\n]{0,40}?\bhead\b"
    r"|\bthe\s+head\b"
    r"|\bthe\s+commit\s+that\s+adds\s+this\b)"
)

#: A count anchored to a MOVING ref, in EITHER order — the document wrote
#: `752 here … at the head of PR #10143` and the `00_index` row wrote `Measured
#: at PR #10143's head … : 752 core files`. A guard that only sees one direction
#: clears the other, which is why both are matched.
_MOVING_REF_COUNT = re.compile(
    rf"(?:\b\d[\d  ]*\b[^.;\n]{{0,100}}?{_MOVING_REF}"
    rf"|{_MOVING_REF}[^.;\n]{{0,80}}?\b\d[\d ]*\b)",
    re.IGNORECASE,
)

#: The basis must NAME a sha, not a ref: `**Measurement basis:** \`427037b36\``.
_BASIS_SHA = re.compile(r"\*\*Measurement basis:\*\*\s*`([0-9a-f]{7,40})`")

#: An `N at \`<sha>\`` claim — the form the basis paragraph states its counts in.
_SHA_CLAIM = re.compile(r"(\d[\d  ]*)[^\n;.]{0,60}?`([0-9a-f]{7,40})`")


def _doc_text() -> str:
    assert _DOC.exists(), f"{_DOC} is the report these figures belong to"
    return _DOC.read_text()


def _basis_paragraph(text: str) -> str:
    """The `- **Measurement basis:**` bullet, up to the next bullet."""
    start = text.index("**Measurement basis:**")
    end = text.find("\n- ", start)
    return text[start:end if end != -1 else len(text)]


def _unpinned_counts(text: str) -> list[tuple[int, str]]:
    """`(line, fragment)` for every count stated against a moving ref."""
    return [
        (text.count("\n", 0, m.start()) + 1, m.group(0))
        for m in _MOVING_REF_COUNT.finditer(text)
    ]


def test_no_published_count_is_anchored_to_a_moving_ref():
    """A count tied to a branch tip has no pin, so it rots on the next merge.

    Non-vacuous: the pre-fix basis line — *"the head of PR #10143 — the commit
    that adds this line"* — reds here; so does the `00_index` row's *"Measured
    at PR #10143's head"*. Both were true statements about a ref and false
    statements about the numbers next to them.
    """
    text = _doc_text()
    index_row = next(
        line for line in _INDEX.read_text().splitlines()
        if "core-import-closure.md" in line)
    assert _BASIS_SHA.search(_basis_paragraph(text)), (
        "the document states its figures without naming the sha they were "
        "derived at — a count against a moving ref is exactly how §1 came to "
        "describe the parent tree while claiming the head: name the sha")
    unpinned = _unpinned_counts(_basis_paragraph(text)) + _unpinned_counts(index_row)
    assert not unpinned, (
        "a published count is anchored to a MOVING ref, so nothing re-derives "
        "it and it rots on the next merge (measured: 752/1 234/258 were one "
        "commit behind at the head that carried them). Quote the sha it was "
        "measured at:\n  - " + "\n  - ".join(f"{frag!r}" for _line, frag in unpinned))


def test_the_moving_ref_guard_rejects_the_pre_fix_basis_line():
    """The mutation check for the arm above — the text this PR replaced.

    Copied verbatim from `427037b36:docs/ci/core-import-closure.md`, so the
    guard cannot be satisfied by a spelling that only looks pinned.
    """
    pre_fix = (
        "- **Measurement basis:** the head of PR #10143 — the commit that adds "
        "this line. Two things make a re-run differ from these tables: `main` "
        "keeps adding `core` registrations (**752** here, 751 on `main` alone, "
        "and 745 at `3934648ae` — the gap is `main`, not this lane)\n")
    assert not _BASIS_SHA.search(pre_fix), (
        "the pre-fix line named no sha, so the basis arm reds on it")
    assert _unpinned_counts(pre_fix), (
        "the pre-fix basis line stated counts against a moving ref and the "
        "guard did not see it — a guard that cannot reject the text it was "
        "written for is vacuous")
    index_pre_fix = (
        "**Measured at PR #10143's head** (`main` keeps adding `core` "
        "registrations): 752 core files, 59 distinct closure sets")
    assert _unpinned_counts(index_pre_fix), (
        "the `00_index` row used the same moving-ref spelling and must red too")


def _git(*args: str) -> str | None:
    """`git <args>` stdout, or None when the object is not in this clone."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=_REPO, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _core_counts_at(sha: str) -> dict | None:
    """Mirror `core_test_files()` against the tree of `sha`, without a checkout.

    Returns `members` / `resolved` / `unresolved` / `all_tests`, or None when the
    sha (or `config/ci-surfaces.yml` at it) is not available here. `unresolved`
    is carried rather than dropped for the same reason the tool reports it: a
    member that resolves to nothing silently shrinks the denominator every
    published percentage is a ratio over.
    """
    manifest_text = _git("show", f"{sha}:config/ci-surfaces.yml")
    tests_tree = _git("ls-tree", "-r", "--name-only", sha, "--", "tests")
    if manifest_text is None or tests_tree is None:
        return None
    from tools import ci_selection

    manifest = yaml.safe_load(manifest_text)
    members = ci_selection._surface_members(manifest["surfaces"].get("core"))
    all_py = [p for p in tests_tree.split() if p.endswith(".py")]
    by_basename: dict[str, list[str]] = {}
    for rel in all_py:
        by_basename.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
    resolved: set[str] = set()
    unresolved: list[str] = []
    for member in members:
        if not str(member).endswith(".py"):
            continue
        if f"tests/{member}" in set(all_py):
            resolved.add(f"tests/{member}")
        elif by_basename.get(str(member)):
            resolved.update(by_basename[str(member)])
        else:
            unresolved.append(str(member))
    return {
        "members": sum(1 for m in members if str(m).endswith(".py")),
        "resolved": len(resolved),
        "unresolved": unresolved,
        # the `1 238` denominator: `test_*.py` anywhere under tests/, which is
        # what both the document and the index row mean by "test files"
        "all_tests": sum(1 for p in all_py
                         if p.rsplit("/", 1)[-1].startswith("test_")),
        "tortoise": len([p for p in (_git(
            "ls-tree", "-r", "--name-only", sha, "--", "tortoise") or "").split()
            if p.endswith(".py")]),
    }


def test_the_published_counts_match_the_tree_of_the_sha_they_name():
    """§1 and the index row, re-derived from the sha the document names.

    Three tree-level counts (core test files, test files, `tortoise/**` modules)
    are settled by the tree alone — no ast pass — so this arm is cheap enough to
    run on the `core` surface. The aggregates that ride on them (median, bucket
    sizes, 636/84.1 %) come from the tool and are out of scope here; the
    reproduce line in the document is what re-derives those.

    SKIPS (with the reason) in a shallow clone, where the sha is not an object —
    that is the honest state: the text arm above still runs everywhere.
    """
    text = _doc_text()
    basis = _BASIS_SHA.search(_basis_paragraph(text))
    assert basis, (
        "no `**Measurement basis:** `<sha>`` in the document — the figures "
        "cannot be re-derived, which is the defect this arm exists for")
    sha = basis.group(1)

    counts = _core_counts_at(sha)
    if counts is None:
        pytest.skip(
            f"{sha} is not an object in this clone (a `fetch-depth: 1` "
            "checkout, or a squash merge that dropped the commit) — the "
            "counts cannot be re-derived here")

    # the basis paragraph's own `N at <sha>` claims: each is the core member
    # count of THAT tree, so '745 at 3934648ae' is checkable, not decorative
    claims = _SHA_CLAIM.findall(_basis_paragraph(text))
    assert claims, "the basis paragraph states no `N at <sha>` claim"
    for number, claim_sha in claims:
        got = _core_counts_at(claim_sha)
        if got is None:
            pytest.skip(f"{claim_sha} is not an object in this clone")
        assert int(number.replace(" ", "")) == got["members"], (
            f"the document states {number} core test files at `{claim_sha}`, "
            f"whose manifest has {got['members']}")

    table = re.search(r"^\| core test files \| \*\*(\d+)\*\*", text, re.MULTILINE)
    assert table, "§1's `core test files` row is gone — the table below it is a ratio over it"
    assert not counts["unresolved"], (
        "core member(s) resolve to no test file at "
        f"`{sha}`: {counts['unresolved']} — the published denominator is short "
        "by that many")
    assert int(table.group(1)) == counts["resolved"] == counts["members"], (
        f"§1 publishes **{table.group(1)}** core test files; the tree of `{sha}` "
        f"has {counts['resolved']} resolved (of {counts['members']} members)")

    modules = re.search(
        r"^\| `tortoise/\*\*` modules in the tree \| (\d+) \|", text, re.MULTILINE)
    assert modules, "§1's `tortoise/**` modules row is gone"
    assert int(modules.group(1)) == counts["tortoise"], (
        f"§1 publishes {modules.group(1)} `tortoise/**` modules; `{sha}` has "
        f"{counts['tortoise']}")

    index_row = next(
        line for line in _INDEX.read_text().splitlines()
        if "core-import-closure.md" in line)
    row = re.search(
        r"`surfaces\.core` is (\d+) of ([\d ]+) test files \((\d+\.\d) %\)", index_row)
    assert row, "the `00_index` row no longer states the `core` share."
    core_n, all_n = int(row.group(1)), int(row.group(2).replace(" ", ""))
    assert (core_n, all_n) == (counts["resolved"], counts["all_tests"]), (
        "the `00_index` row publishes "
        f"{core_n} of {all_n} test files; the tree of `{sha}` has "
        f"{counts['resolved']} of {counts['all_tests']}")
    assert row.group(3) == f"{round(100 * core_n / all_n, 1):.1f}", (
        f"the `00_index` row states {row.group(3)} % for {core_n}/{all_n} — the "
        f"two halves of one row cannot disagree")


def test_the_tree_arm_skips_instead_of_erroring_when_the_sha_is_absent(monkeypatch):
    """A `fetch-depth: 1` clone must SKIP this arm, not fail the suite.

    The arm shells out to `git show <sha>:config/ci-surfaces.yml`; in a shallow
    CI checkout (or after a squash merge that drops the commit) that object does
    not exist, and an arm that turned "I cannot read this" into a red would block
    every PR for a reason unrelated to it — the fail-open direction this suite's
    harness pins exist to forbid. `_core_counts_at` returns None in that state and
    the arm skips with the reason, so the TEXT arm above stays the one that always
    runs (it needs no git at all).
    """
    from tools import ci_import_closure as ci

    assert ci.REPO.exists()
    assert _core_counts_at("0" * 40) is None, (
        "a sha that is not an object in this clone must report unavailable, not "
        "raise — an exception here would red a shallow checkout")
    monkeypatch.setattr(sys.modules[__name__], "_core_counts_at", lambda sha: None)
    with pytest.raises(pytest.skip.Exception):
        test_the_published_counts_match_the_tree_of_the_sha_they_name()
