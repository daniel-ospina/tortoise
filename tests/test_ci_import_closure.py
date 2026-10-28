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
    """115 of 745 core files import nothing from the package: they guard a
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
    # python-ci.yml:3584 has no `name:`; :3707 uploads the LITERAL — no `test-`.
    assert "pytest-log-record-postgres" in artifact_names_for_job(
        "test-record-postgres")
    # same shape: job :3018 (no `name:`), literal upload at :3178.
    assert "pytest-log-d14-hosted-api" in artifact_names_for_job(
        "test-d14-hosted-api")
    # bare matrix name = shard 0: `name:` suffix '' but artifact label 'a'.
    assert "pytest-log-test-carve-out-a" in artifact_names_for_job("test-carve-out")


def test_the_bare_carve_out_artifact_label_matches_the_selector():
    """The `-a` above is NOT a guess about shard 0 — it is read off the selector.

    `python-ci.yml:3011` names the upload with `matrix.label`, while the job's
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
    # exact basename resolution while the assertion tolerated 5 % — at 745
    # members that is up to 37 core files silently ceasing to resolve with every
    # test still green, and every published percentage is a ratio over this
    # count. Measured today: all 745 resolve. `core_test_files` now reports an
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
                        lambda repo, pr, cache: ["tortoise/lazy.py"])
    monkeypatch.setattr(ci, "pr_ground_truth", lambda repo, sha, cache: {
        "failed_files": [], "failed_tests": [], "jobs": ["test (a)"],
        "job_failed_without_test_failure": [], "artifacts": 0,
        "artifact_errors": ["run 1: no pytest-log-* artifact uploaded"],
        "junit_missing": [], "ground_truth_complete": False,
    })

    args = argparse.Namespace(
        json="", exclude_type_checking=False, repo="o/r",
        window=None, prs=[], import_time_only=False,
    )
    assert ci.cmd_validate(args) == 2, "an unread window must not exit 0"
    assert "unreadable" in capsys.readouterr().out


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
                        lambda repo, pr, cache: ["tortoise/lazy.py"])
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
                        lambda repo, pr, cache: ["tortoise/lazy.py"])
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
                        lambda repo, pr, cache: list(changed))
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
    """P2: the always-on legs are unioned into today's selection.

    `test-record-postgres` runs four files on EVERY PR (`python-ci.yml:3640`),
    and the harness reads its junit as ground truth. A docs-only or `tools/`-only
    diff takes the tier-1 early return, so those files were outside
    `current_rel` before — and `misses_for` called the failure `not_in_current`,
    i.e. it blamed the existing selector for a test the selector DID run and left
    the derivation unscored. Measured reachability: `test_9555_narrative_carrier`
    is on `api` + `core` but NOT on `tier1`.
    """
    from tools import ci_import_closure as ci
    assert "tests/test_9555_narrative_carrier.py" in ci.ALWAYS_ON_LEG_FILES
    rc = _stub_validate(monkeypatch, ci, _truth(
        failed_files=["tests/test_9555_narrative_carrier.py"],
        jobs=["test-record-postgres"], artifacts=1,
    ), cache=tmp_path)
    out = capsys.readouterr().out
    assert "fall outside the modelled selection" not in out, (
        "an always-on leg's file is IN the model, so it must not read as a hole")
    assert "outside today's selection" not in out, (
        "the leg runs it on every PR, so the existing selector DID run it — "
        "scoring it `not_in_current` blamed the selector for the model's gap")
    assert rc != 2, (
        "a failure the harness can place must not turn the window PARTIAL")
    # Both sides select it (it is in `current` and in `base = current - core`), so
    # the derived mapping is scored as COVERING it — which is the honest result:
    # the derivation narrows `core`, it does not remove an always-on leg.
    assert rc == 0 and "MISS[" not in out


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
