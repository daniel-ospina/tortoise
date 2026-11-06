"""No module or package under `tests/` may shadow a stdlib top-level name (#10060).

Why this file exists on its own, rather than inside the eval suite that found the
defect: the property it guards is REPO-WIDE (`tests/**`), but a pin is only ever
as wide as the CI surface that SELECTS it. The first cut of this guard lived in
`tests/test_eval_metrics.py` — registered on `core` ALONE (the pin at the bottom
of this file names that as the hole) — and that is
narrower than the property in exactly the case that matters: a named-surface
match REPLACES the `core` fallback in `tools/ci_selection.py::select()`
(#2938/#7951 idiom, stated at the multi-registered entries in
`config/ci-surfaces.yml`), so a diff confined to one surface's tree (measured on
this tree: `tests/eval/ask/corpus.py` -> surfaces `['eval']`) could introduce
`tests/<that surface>/json.py` with the guard asleep unless the guard is
registered for THAT surface. Hence: registered in EVERY surface (review note
N1), and the registration is itself pinned at the bottom of this file, because
registration alone is CLASSIFICATION (#4348) — it satisfies `ci_selection.py
--integrity` without making any diff select the file.

The selection claim is stated as a FALSIFIABLE recipe and NOT as a count,
deliberately: a count of the selection RESULT has no derivation in the tree, only
a measurement, and this one was measured two different ways in review — while the
list it counts moves with every edit to that surface's registrations, so a quoted
figure here could go stale without anything going red. The recipe, with `tools/`
on `sys.path` as this file puts it there:

    from ci_selection import load_manifest, select
    select(['tests/eval/ask/corpus.py'], 'pull_request', load_manifest())['test_files']
      -> contains 'test_stdlib_shadow_guard.py'
    ... the same call with this guard's `eval` registration removed:
      -> does NOT contain it   (the `core` fallback is not consulted, because a
         named-surface match REPLACES it — the whole point of this paragraph)

`test_the_guard_is_selected_on_every_surface_that_can_shadow`, at the bottom of
this file, asserts the same claim GENERALIZED: one subject per `SOURCE_PATTERNS`
surface, each resolving to THAT surface ALONE so the `core` fallback cannot
satisfy it, plus an arm for the `core` fallback itself (`tortoise/log.py`).
`classify`'s registration is NOT pinned — it has no `SOURCE_PATTERNS` entry, so a
classify-owned diff resolves `['classify','core']` and the `core` arm already
co-satisfies it; that test's own docstring states this.

Two shapes shadow identically for a child interpreter handed the containing
directory on `sys.path` (`PYTHONPATH` precedes the stdlib):

* a MODULE — `tests/eval/types.py`: the file's stem becomes the name;
* a REGULAR PACKAGE — `tests/eval/types/__init__.py`: the directory's name
  becomes the name. Scanning files alone misses this shape entirely, because the
  `__init__.py` filter skips the only file in it and its stem is `__init__`, not
  `types`.

Why the defect is invisible in-process and total in a child: stdlib `types` is
already in `sys.modules`, so nothing re-resolves it here; a FRESH interpreter
handed that root on `PYTHONPATH` resolves the shadow instead of the stdlib and
dies — WHEN depends on the interpreter, and both shapes are measured below with
the real shadow file. Measured 2026-10-26, CI `test (g)`: both `TestCliRender` pins
in `tests/test_capture_disclosure_template.py` went red only in the full suite,
while a single-file run passed — collection of `tests/eval` is what puts that
directory on the path.

⚠️ WHICH of two mechanisms fires depends on the interpreter — and the ORIGINAL
telling of this file ("`site`-init") was RIGHT for CI, so an earlier revision of
this PR that flatly retracted it was wrong. Both are stated here with their
measurements, taken with the shadow file this rename moved
(`tests/eval/eval_types.py` restored as `types.py`), CPython 3.12:

* The interpreter a `python-ci` job that installs the package EDITABLE uses fails AT
  STARTUP. Those jobs install it editable — `pip install -e .` or `pip install -e '<extras>'`,
  in an `Install…` step whose exact name and extras spec vary — so
  site-packages carries setuptools' **import-based editable finder**
  (`__editable___tortoise_graph_*_finder.py`,
  forced by this repo's package mapping) and `site.addpackage` imports it before
  any user code runs -> `importlib.util` -> ... -> `types`:

      PYTHONPATH=<root>/eval <editable-install python> -c pass
      Fatal Python error: init_import_site: Failed to import the site module
      ImportError: cannot import name 'MappingProxyType' from partially
      initialized module 'types' (/root/eval/types.py)          # exit 1

  That `MappingProxyType ... partially initialized` message is the 2026-10-26 CI
  signature, and it is a STARTUP failure — not an import-chain one.
* A **bare** interpreter survives (`/usr/bin/python3 -c pass` -> exit 0) and
  dies later, on the first import chain that reaches `types` — `import yaml` ->
  `codecs` -> ... -> `_weakrefset` -> `from types import GenericAlias`, so the
  missing attribute there is `GenericAlias`.

So the missing NAME depends on the chain (`MappingProxyType` on the site-init
chain, `GenericAlias` on the `yaml` chain), while the harmless SHAPE question is
the interpreter: with an editable install the child never starts at all. The
remedy (the rename) is the same either way, and this guard asserts the child's
EXIT CODE rather than any message, precisely so it does not encode one chain.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = TESTS_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# `ci_selection` is a `tools/` MODULE, not a package: a regular package needs the
# directory CONTAINING it on `sys.path`, a module needs its OWN directory (docs
# .python.org/3/reference/import.html 5.2 — "all packages are modules, but not
# all modules are packages"; a regular package is "a directory containing an
# `__init__.py` file"). So `tools/` itself goes on `sys.path` and the
# registration pin below imports it by bare name — the same shape, at module
# scope, that `tests/test_batch_gate.py:72,75` uses (`sys.path.insert(0, str(ROOT
# / "tools"))` then `import ci_selection as cs`), and the same bare name
# `tests/test_dod_5090_probe.py:639` imports.
#
# ⚠️ `tests/test_ci_selection.py` is NOT such a consumer and must not be cited as
# one (it does `from tools import ci_selection as cs`, resolved through the repo
# root, never the bare name) — the earlier telling of
# this comment named it and was wrong on the half that justifies the insert
# above.
if str(_REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "tools"))


def _stdlib_shadowing_paths(tests_root: Path) -> list[str]:
    """Every path under `tests_root` that shadows a stdlib top-level name.

    Directory hits carry a trailing `/` so the failure message names the shape it
    found. A directory is reported ONLY when it holds an `__init__.py`, and that
    is a measurement rather than caution: a directory WITHOUT one is a namespace
    PORTION, and a namespace portion loses to a real module found later on
    `sys.path`, so it never shadows (PEP 420: the scan "records" the directory
    and "continues with the next directory in the parent path"). Measured with a
    `types/` directory holding `payload.py` and no `__init__.py`, on
    `PYTHONPATH`:

        python -c "import types; print(types.__file__)"
        -> /usr/lib/python3.12/types.py          # the stdlib won

    while the same directory WITH `__init__.py` breaks a child's imports the same
    way (`ImportError: cannot import name 'MappingProxyType' from 'types'
    (.../types/__init__.py)`), which is why both shapes are reported.

    Reporting the namespace shape would red this guard on a fixture directory
    that cannot shadow, and tell its owner to rename something harmless.

    The scan is deliberately BROADER than "directories pytest puts on
    `sys.path`" (review note N2): it walks every `*.py` under `tests/`, including
    data/fixture trees pytest never inserts, so a future `tests/gold/token.py`
    would red it even though nothing imports it. That is accepted rather than
    narrowed, and measured at 0 such paths on the tree that ships this file:
    under-reporting a real shadow costs a red full suite and a debugging session
    that starts from the wrong interpreter, while over-reporting costs a rename
    of a file whose name was a footgun anyway. Narrowing the scan to "the basedir
    pytest would insert" would need to model `__init__.py` walking, conftest
    collection and every `PYTHONPATH` export in the suite — three ways to
    re-open the hole this guard exists to close.

    ⚠️ This is a SECOND, independent implementation of "does this directory carry
    a stdlib top-level name", and it deliberately disagrees with the first:
    `tests/test_capture_disclosure_template.py::_shadows_the_stdlib` returns True
    for a bare directory whose name is a stdlib name, `__init__.py` or not. Both
    were left as they are in #10060, and the divergence is recorded here rather
    than silently resolved, because the two have different jobs and different
    failure directions:

    * THIS one ASSERTS (it reds a build), so it must be exact — a false positive
      sends an owner to rename a directory that cannot break a child, which is
      the over-broad rule #10060's own follow-up had to retract.
    * That one FILTERS (it drops a root from a child's `PYTHONPATH`), so its
      disagreement is over-block only: it still catches both real shapes, and
      nothing fails open.

    So do not "fix" either by copying the other. If the filter should be narrowed
    to this measurement, that is its own change with its own pin, in its own file.
    """
    stdlib_names = set(sys.stdlib_module_names)
    hits = {
        str(path.relative_to(tests_root.parent))
        for path in tests_root.rglob("*.py")
        if path.name != "__init__.py" and path.stem in stdlib_names
    }
    hits |= {
        f"{path.parent.relative_to(tests_root.parent)}/"
        for path in tests_root.rglob("*.py")
        if path.name == "__init__.py" and path.parent.name in stdlib_names
    }
    return sorted(hits)


def test_no_test_module_shadows_a_stdlib_top_level_name():
    """A file under `tests/` named after a stdlib module shadows it for any FRESH
    interpreter given its directory on `sys.path` (#10060).

    Why this cannot be caught by merely importing here: this process NEVER sees
    the shadow, because the stdlib module is already in `sys.modules`. A CHILD
    does see it, and it fails: the editable-install interpreter this test spawns
    (`sys.executable` under `pip install -e .`) never starts at all — exit 1,
    `init_import_site: Failed to import the site module` — while a bare
    interpreter survives `-c "pass"` and fails later on `import yaml`. The
    assertion below is on the exit code, so it holds for both. Measured at
    `main` before the fix, with `tests/eval/types.py` present, both ways:

        PYTHONPATH=repo:repo/tests/eval <editable-install python> -c pass
        Fatal Python error: init_import_site: Failed to import the site module
        ImportError: cannot import name 'MappingProxyType' from partially
        initialized module 'types' (.../tests/eval/types.py)          # exit 1

        PYTHONPATH=repo:repo/tests/eval /usr/bin/python3 -c "import yaml"
        ImportError: cannot import name 'GenericAlias' from partially
        initialized module 'types' (via .../_weakrefset.py)             # exit 1

    and either command WITHOUT the shadow root succeeds — so the directory,
    not the interpreter, was the cause. `tests/eval` reaches `sys.path` at
    collection time (measured with a `pytest_collection_finish` hook: collecting
    `tests/eval/ask/test_ask_corpus.py` leaves `.../tests/eval` on `sys.path`,
    because `tests/eval/ask/__init__.py` exists and `tests/eval` does not, so
    `tests/eval` is the basedir pytest prepends). The defect is therefore live
    for any child a test spawns, and it greens in a single-file run and reds only
    in the full suite.

    Two assertions on purpose. The structural one names every offending path, so
    a second shadow cannot be introduced quietly. The behavioural one proves the
    harm on a real child interpreter instead of asserting the theory.
    """
    shadowing = _stdlib_shadowing_paths(TESTS_ROOT)
    assert not shadowing, (
        f"path(s) under tests/ shadow a stdlib top-level name: {shadowing}. "
        f"pytest puts a directory without __init__.py verbatim on sys.path, so a "
        f"child interpreter handed it imports the shadow instead of the stdlib "
        f"and dies on the first import that reaches it (#10060). Rename the "
        f"module (e.g. tests/eval/types.py -> tests/eval/eval_types.py) or the "
        f"package directory (a trailing `/` marks a directory hit)."
    )

    # The behavioural half: the exact child that used to die. Kept after the
    # structural check so a future shadow reports the file that caused it; kept
    # at all so the guard cannot be satisfied by merely renaming this test.
    #
    # ⛔ The subject directory is ASSERTED to exist first. CPython silently
    # ignores a non-existent `PYTHONPATH` entry, so without this line a rename or
    # move of `tests/eval` would leave the child starting cleanly and
    # `returncode == 0` — the exact vacuous pass this half exists to prevent
    # (measured: `PYTHONPATH=/nonexistent python3 -c pass` exits 0).
    assert (TESTS_ROOT / "eval").is_dir(), (
        "the behavioural arm's subject directory `tests/eval` is gone — derive "
        "it from the scan instead of letting a missing root pass as a clean child"
    )
    child = subprocess.run(
        [sys.executable, "-c", "import types; types.GenericAlias"],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [str(TESTS_ROOT.parent), str(TESTS_ROOT / "eval")]
            ),
        },
        capture_output=True,
        text=True,
    )
    assert child.returncode == 0, (
        "a fresh interpreter given tests/eval on PYTHONPATH cannot use stdlib "
        f"`types`: {child.stderr.strip()[-500:]}"
    )


def test_the_stdlib_shadow_scan_sees_a_package_directory_not_just_a_file(tmp_path):
    """The scan is a property guard, so it must see BOTH shadow shapes.

    A file named `types.py` and a regular PACKAGE named `types/` shadow the
    stdlib identically for a child handed the containing directory on `sys.path`,
    and the directory shape survives the `__init__.py` filter untouched — so
    without this pin a scan that had been narrowed back to files alone would
    still pass, and the guard's claim ("a second shadow cannot be introduced
    quietly") would be false.

    The last arm is the opposite direction, and it is why the directory shape is
    spelled `path.name == "__init__.py"` rather than "any directory holding
    Python": a directory WITHOUT `__init__.py` is a namespace PORTION, which a
    real module later on `sys.path` beats, so it does not shadow. Reporting it
    would red this guard on a fixture directory that cannot break a child.
    """
    tests_root = tmp_path / "tests"
    (tests_root / "eval").mkdir(parents=True)
    (tests_root / "eval" / "fine.py").write_text("x = 1\n")
    assert _stdlib_shadowing_paths(tests_root) == []

    (tests_root / "eval" / "types.py").write_text("x = 1\n")
    assert _stdlib_shadowing_paths(tests_root) == ["tests/eval/types.py"]

    (tests_root / "eval" / "types.py").unlink()
    (tests_root / "eval" / "types").mkdir()
    (tests_root / "eval" / "types" / "__init__.py").write_text("x = 1\n")
    assert _stdlib_shadowing_paths(tests_root) == ["tests/eval/types/"]

    # A namespace portion — same name, Python content, NO `__init__.py` — cannot
    # shadow, so it must not be reported (measured: `import types` still resolves
    # to the stdlib with such a directory first on PYTHONPATH).
    (tests_root / "eval" / "types" / "__init__.py").unlink()
    (tests_root / "eval" / "types" / "payload.py").write_text("x = 1\n")
    assert _stdlib_shadowing_paths(tests_root) == []


def test_the_guard_is_selected_on_every_surface_that_can_shadow():
    """The guard must RUN on a diff it guards, or it is not a guard (#4348).

    Registration alone classifies the file (`ci_selection.py --integrity` stops
    complaining); it does not make any diff SELECT it. Since the property is
    repo-wide, every surface that a named match can RESOLVE must select it — and
    this pin is what fails if a future edit registers it on `core` alone, which is
    the exact hole the first cut had (a named-surface match REPLACES the `core`
    fallback, so an `eval`-only, `api`-only, `sdk`-only … diff ran the guard only
    if that surface's list named it).

    Scope, stated exactly: this pins the six `SOURCE_PATTERNS` surfaces plus the
    `core` fallback. It does NOT — and cannot — pin `classify`, because `classify`
    has no `SOURCE_PATTERNS` entry: a classify-owned diff resolves
    `surfaces=['classify','core']` (measured: `--changed-files tests/test_kind_index.py`),
    so the `core` registration above already co-selects this guard and removing the
    `classify` entry reds nothing. The `classify` registration is therefore
    redundant belt-and-braces, not a pinned requirement.
    """
    import ci_selection

    manifest = ci_selection.load_manifest()
    surfaces = ci_selection.SOURCE_PATTERNS
    assert surfaces, "no SOURCE_PATTERNS resolved — the manifest shape changed"

    for surface, patterns in surfaces.items():
        # Pick a subject that resolves to THIS surface ALONE. `patterns[0]` is not
        # enough, and the gap was MEASURED: `api`'s first pattern
        # (`tortoise/hosted_api.py`) co-resolves `['api','onboarding']`, so
        # `onboarding`'s registration alone satisfied the api assertion — deleting
        # the `api` registration left this pin GREEN while an api-only diff
        # (`tortoise/acl_graph_users.py` -> `surfaces=['api']`) stopped selecting
        # the guard. That is the exact hole this test exists to close, one surface
        # over, so the subject is chosen for ISOLATION and not by position.
        chosen = None
        for subject in patterns:
            candidate = ci_selection.select([subject], "pull_request", manifest)
            if candidate["surfaces"] == [surface]:
                chosen, selected = subject, candidate
                break
        assert chosen is not None, (
            f"no {surface}-only subject resolves to [{surface!r}] — this pin can no "
            f"longer ISOLATE that surface, so its registration would go unchecked; "
            f"add a subject whose diff selects it alone")
        assert "test_stdlib_shadow_guard.py" in selected["test_files"], (
            f"a {chosen}-only diff ({surface} surface) does not select this "
            f"guard — a shadow introduced under tests/{surface}/ would land "
            f"unchecked: surfaces={selected['surfaces']} "
            f"full={selected['full']}"
        )

    # The `core` fallback gets its own arm: a diff matching no named surface.
    selected = ci_selection.select(["tortoise/log.py"], "pull_request", manifest)
    assert "test_stdlib_shadow_guard.py" in selected["test_files"], (
        "the core-fallback diff path does not select this guard: "
        f"surfaces={selected['surfaces']} full={selected['full']}"
    )
