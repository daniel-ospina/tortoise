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

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_import_closure import (  # noqa: I001
    DIRECT, MISS_NO_PATH, MISS_NOT_PYTHON, MISS_TAINTED, PACKAGE_INIT,
    Closure, ModuleIndex, Scanner, UNKNOWN_DYNAMIC_ATTR, UNKNOWN_DYNAMIC_CALL,
    UNKNOWN_SYNTAX, UNKNOWN_UNRESOLVED, artifact_names_for_job,
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
    """`tortoise/sdk.py` carries 16 module-level in-repo imports and 74 function-local
    ones (89 distinct targets, one reached both ways) — a top-level-only reader would
    miss the hub entirely."""
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
    assert resolved.unknown == []  # a literal attr is static: not a module, no edge


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
    """114 of 742 core files import nothing from the package: they guard a
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


def test_artifact_name_contract_for_shard_and_plain_jobs():
    assert artifact_names_for_job("test (f)") == ["pytest-log-test-f"]
    assert artifact_names_for_job("test-slow (c)") == ["pytest-log-test-slow-c"]
    assert artifact_names_for_job("test-carve-out (a)") == ["pytest-log-test-carve-out-a"]
    assert artifact_names_for_job("test-concurrency-falkor") == [
        "pytest-log-test-concurrency-falkor"]


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
    assert len(core) >= expected * 0.95  # basenames resolve to exactly one path
    assert len(closures) == len(core) > 700
    # No file is silently omitted: every core file has a closure entry.
    assert all(rel in closures for rel in core)


def test_package_init_reaches_the_sdk_hub_through_config(repo_closure):
    """THE #10132 MECHANISM, pinned as evidence rather than prose.

    `tortoise/__init__.py` -> (module level) `tortoise.config` ->
    (function-local) `tortoise.sdk`, the 42k-line hub -> 186 of 258 modules.
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


def test_import_time_only_reproduces_the_naive_reader_that_under_selects():
    """`--import-time-only` is the trap, kept measurable.

    On this repo it reports a median core closure of 21 modules instead of 186 —
    a ">88 % reduction" that would ship a false green, because `tortoise/sdk.py`
    reaches 74 of its 89 in-repo modules through function-local imports.
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
    """P1: the dedupe kept whichever occurrence was YIELDED first.

    `_imports_with_context` is a LIFO stack, so for a target imported both at
    module level and inside a function the FUNCTION-LOCAL occurrence is yielded
    first (verified: linelines (3, True) then (1, False)). The old
    `_dedupe_deps` compared `type_checking` only, so the function-local edge won
    and the module-level edge was DISCARDED — and then `--import-time-only`
    filtered the survivor out, leaving the file looking as if it never imported
    the target at all. That under-selection is the exact false green this tool
    exists to expose, so it must be produced by the naive READER, never by a bug
    in the edge set it is reading.
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
    assert len(lazy) == 1, f"one edge per (target, kind), got {lazy}"
    assert lazy[0].in_function is False, (
        "the MODULE-LEVEL occurrence must survive the dedupe; the function-local "
        "one is the weaker edge and is what `--import-time-only` filters out")
    # and the consequence the bug had: the module-level reader must SEE it
    naive = compute_closures(scans, idx, ["tests/test_t.py"],
                             follow_function_local=False)["tests/test_t.py"]
    assert "tortoise/lazy.py" in naive.modules


def test_dedupe_prefers_a_runtime_edge_over_a_type_checking_one():
    """The other half of the ranking: `TYPE_CHECKING` is the weaker context."""
    idx = _index("tortoise/a.py")
    scan = _scan(
        idx, "tests/test_t.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from tortoise.a import thing\n"
        "from tortoise.a import other\n")
    a = [d for d in scan.deps if d.target == "tortoise.a"]
    assert len(a) == 1 and a[0].type_checking is False


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


def _fake_ground_truth(monkeypatch, module, *, junit: bytes | None,
                       listing_names: list[str]):
    """Drive `pr_ground_truth` with a fake check-run + one fake artifact."""
    import io
    import zipfile

    run = {"name": "test (a)", "conclusion": "failure", "id": 99,
           "details_url": "https://github.com/o/r/actions/runs/99/job/1"}
    monkeypatch.setattr(module, "_check_runs", lambda repo, sha: [run])
    monkeypatch.setattr(module, "_run_id", lambda r: 99)
    monkeypatch.setattr(
        module, "_gh_json",
        lambda args: {"artifacts": [{"id": 7, "name": n} for n in listing_names]})

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if junit is not None:
            zf.writestr("junit.xml", junit)
        else:
            zf.writestr("pytest.log", "some log\n")
    blob = buf.getvalue()
    monkeypatch.setattr(
        module, "_download_artifact",
        lambda repo, aid, cache: _write_tmp(cache, aid, blob))


def _write_tmp(cache: Path, aid: int, blob: bytes) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    p = cache / f"artifact-{aid}.zip"
    p.write_bytes(blob)
    return p


def test_ground_truth_records_an_artifact_that_carries_no_junit(monkeypatch, tmp_path):
    """P2: a red leg may upload only `pytest.log`; that is NOT "nothing failed"."""
    from tools import ci_import_closure as module
    _fake_ground_truth(monkeypatch, module, junit=None, listing_names=["pytest-log-test-a"])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["artifact_errors"], (
        "an artifact with no junit.xml must be REPORTED — silently dropping it "
        "makes 'could not read' indistinguishable from 'nothing failed'")
    assert not (tmp_path / f"truth-v{module.GROUND_TRUTH_VERSION}-deadbeef.json").exists(), (
        "a PARTIAL ground truth must never be cached as truth")


def test_ground_truth_records_an_unparseable_junit(monkeypatch, tmp_path):
    """P2: `parse_junit_failures` returns the parse error in `error`; read it."""
    from tools import ci_import_closure as module
    _fake_ground_truth(monkeypatch, module, junit=b"<not-xml",
                       listing_names=["pytest-log-test-a"])
    truth = module.pr_ground_truth("o/r", "deadbeef", tmp_path)
    assert truth["artifact_errors"], "unparseable junit must be reported, not dropped"
    assert not (tmp_path / f"truth-v{module.GROUND_TRUTH_VERSION}-deadbeef.json").exists()


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
