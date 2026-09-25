"""Every tools/ + graph-scripts/ entry point refuses a <3.12 interpreter (#5128).

Scope: tracked ``tools/**/*.py`` + ``graph-scripts/*.py`` — **not "every repo
entry point"**. ``.github/scripts/*.py`` and ``validation/*.py`` are invoked by
CI workflows and are declared residue, not silently assumed covered. Two
reasoned, re-derived exclusions are subtracted (``UNGUARDABLE``,
``RUNTIME_39``).

Why
---
The repo pins Python 3.12 (`pyproject` `requires-python`, `.python-version`), but
the documented invocation is bare ``python3 tools/x.py``, and the host's ambient
``python3`` can be older (observed 2026-09-24: `/usr/bin/python3` is Apple CLT
3.9.6, and `~/.local/bin` has `python3.12` but no unversioned `python3`). A tool
that touches a 3.11+ stdlib name then dies with an UNATTRIBUTED error —
``tools/branch_reaper.py`` ← ``datetime.UTC`` → ``branch_reaper: INTERNAL
ERROR — AttributeError("module 'datetime' has no attribute 'UTC'")`` — which
reads as "this tool is broken" rather than "your interpreter is too old". A lane
that concludes "broken" falls back to a naive `ahead=N` / `git branch --merged`
judgement, which badly undercounts on this squash-merged repo.

What this file is
-----------------
The class-level half of #5128's fix. Each entry point carries an inline guard
(the shape of **D9**, ``tools/longmem_eval/run.py``); this file reds when one is
missing, so the fix cannot regress one tool at a time. That is the difference
between fixing #5128 and fixing the class (#5128, #4848, and the next tool).

Corpus == scope, by construction
--------------------------------
``_corpus()`` is the filesystem glob **intersected with `git ls-files`**, so the
corpus IS the tracked, reviewable scope: an untracked stray (`tools/scratch.py`)
cannot silently join the guard assertions — ``test_every_corpus_file_is_tracked``
reds on it instead. Two reasoned exclusions are then subtracted, each re-derived
by its own test below so neither can outlive its reason.

What it verifies (per entry point, no old interpreter needed)
-------------------------------------------------------------
1. ``import sys`` and the guard are the FIRST statements after the module
   docstring and the ``__future__`` import — i.e. BEFORE every other import.
   That position is load-bearing: 7 corpus files have a module-level
   ``from datetime import UTC`` (3.11+), which raises ``ImportError`` on 3.9
   before any later-placed guard could run.
2. The guard tests exactly ``sys.version_info < (3, 12)`` and raises
   ``SystemExit``.
3. EXECUTING the guard node with ``sys.version_info`` monkeypatched to
   ``(3, 9, 6)`` raises ``SystemExit`` whose message names the floor, the
   interpreter it actually got, the file, and the invocation that ACTUALLY
   works: ``uv run python -m tools.<pkg>.<mod>`` for a package module, the
   script form for a loose script, and NO run clause for a library module with
   no CLI. A refusal that names an invocation which itself dies is a SECOND
   unattributed error — the exact harm #5128 was filed for. At ``(3, 12, 0)``
   the guard does not raise. The node is compiled and exec'd, so the assertion
   is on BEHAVIOUR, not on the guard's text.

Declared bounds — what this file does NOT verify
------------------------------------------------
* **Reach.** That the guard is on the path a real invocation takes is not
  verified by running the tool (each does real work); statement 1 pins its
  position as the property that matters, and a guard behind `if False` at that
  position is not something a static check can separate. The acceptance test on
  #5128 runs one guarded tool under the ambient interpreter instead.
* **CI selection reach.** A PR that changes ONLY a `tools/*.py` path (not
  `tools/longmem_eval/`, not a `TOOL_CARVEOUTS`/`SOURCE_PATTERNS`-listed tool,
  and no test file) drops to tier-1 smoke and would not run this file. That is
  the pre-existing #3261/#3362/#4115 silent-drop class, not introduced here; the
  per-file carve-outs in `tools/ci_selection.TOOL_CARVEOUTS` are the existing
  remedy and a corpus-wide one is filed separately.
* **`graph-scripts/profile_395_local_ep.py`** is UNGUARDABLE and excluded below:
  it uses 3.12-only SYNTAX (PEP 701 nested same-quote f-strings), so it does not
  COMPILE under the floor and no statement in it can run. Its failure is a
  SyntaxError carrying a line number, not an unattributed one.
  `test_the_unguardable_exclusion_is_accurate` re-derives that reason.
* **`tools/tmpdir_sweep.py`** is deliberately 3.9-RUNNABLE (`RUNTIME_39`) and so
  carries no guard. It does not crash on an old interpreter — it runs clean —
  and its documented scheduled invocation is `/usr/bin/python3 …` (the only
  interpreter a minimal-PATH cron is guaranteed). Guarding it would convert a
  working scheduled job into a refusal with no diagnostic benefit.
  `test_the_runtime_39_exclusion_is_accurate` re-derives the 3.9-clean claim.
* **`tools/**/__init__.py`** are package markers, not entry points.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The corpus the guards were applied to (see module docstring).
CORPUS_DIRS = ("tools", "graph-scripts")

#: Package markers, not entry points: never invoked as a script.
EXCLUDED_NAMES = frozenset({"__init__.py"})

#: The repo contract the guard pins — `pyproject requires-python`, `.python-version`.
FLOOR = (3, 12)

#: The one corpus file no inline guard can protect, with the reason. Kept as a
#: mapping so the exclusion is named AND justified, and re-derived by
#: `test_the_unguardable_exclusion_is_accurate` so a stale entry reds.
UNGUARDABLE: dict[str, str] = {
    "graph-scripts/profile_395_local_ep.py": (
        "3.12-only SYNTAX (PEP 701 nested same-quote f-strings) — the file does "
        "not compile under the floor, so no statement in it can ever run"
    ),
}

#: Files the guard is deliberately NOT applied to because guarding them would
#: REMOVE a working capability rather than convert a crash into a named refusal.
#: Re-derived by `test_the_runtime_39_exclusion_is_accurate`, which actually RUNS
#: each entry under a real <3.12 interpreter — so the moment a file stops being
#: 3.9-clean this reds and names it, and the exclusion cannot outlive its reason.
RUNTIME_39: dict[str, str] = {
    "tools/tmpdir_sweep.py": (
        "deliberately 3.9-runnable: it is 3.9-clean and its documented scheduled "
        "invocation is `/usr/bin/python3 tools/tmpdir_sweep.py` (the only "
        "interpreter a minimal-PATH cron is guaranteed) — the guard would turn a "
        "working scheduled job into a refusal with no diagnostic benefit"
    ),
}

#: A small floor: the corpus is the measured tracked `tools/**/*.py` +
#: `graph-scripts/*.py` set. A glob that silently stopped matching must fail,
#: never pass vacuously. (142 at the #5128 head: 144 tracked entry points, minus
#: `UNGUARDABLE`, minus `RUNTIME_39`.)
MIN_CORPUS = 140


def _git_lines(*args: str) -> list[str]:
    """`git <args>` lines from ROOT. Loud on failure: a corpus derived from an
    unreadable index must never silently fall back to a filesystem glob."""
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)} failed:\n{proc.stderr}"
    return [line for line in proc.stdout.splitlines() if line]


def _tracked() -> set[str]:
    """Every tracked .py under the corpus dirs — the reviewable scope."""
    return {rel for rel in _git_lines("ls-files", "--", *CORPUS_DIRS) if rel.endswith(".py")}


def _globbed() -> list[Path]:
    files: list[Path] = []
    for base in CORPUS_DIRS:
        files.extend(sorted((ROOT / base).rglob("*.py")))
    return files


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _corpus() -> list[Path]:
    """corpus == glob ∩ tracked scope, minus the reasoned exclusions.

    `git ls-files` is what makes the corpus reviewable scope: an untracked stray
    cannot silently join the guard assertions (`test_every_corpus_file_is_tracked`
    reds on one instead).
    """
    tracked = _tracked()
    files: list[Path] = []
    for path in _globbed():
        if "__pycache__" in path.parts or path.name in EXCLUDED_NAMES:
            continue
        rel = _rel(path)
        if rel not in tracked:
            continue
        if rel in UNGUARDABLE or rel in RUNTIME_39:
            continue
        files.append(path)
    return sorted(files)


def _package_module(rel: str) -> str | None:
    """The dotted path this file is imported under, or None if it is a loose script.

    A file is a PACKAGE MODULE when it sits below a directory chain in which every
    directory has an ``__init__.py`` — i.e. ``tools.<pkg>.<mod>`` is a real import
    path. Its only working invocation is ``python -m``: run as a script,
    ``sys.path[0]`` is the module's own directory, so a relative import raises
    ``ImportError`` and an absolute ``tools.*`` import raises ``ModuleNotFoundError``
    (both measured at head, #5136). ``tools/experiments/extractor-v2/*.py`` is NOT a
    package (no ``__init__.py``, and the directory name is not an identifier), so it
    keeps the script form.
    """
    parts = Path(rel).parts
    if parts[0] != "tools" or len(parts) < 3:
        return None
    for depth in range(2, len(parts)):
        if not (ROOT / Path(*parts[:depth]) / "__init__.py").is_file():
            return None
    return ".".join((*parts[:-1], Path(rel).stem))


def _has_cli(tree: ast.Module) -> bool:
    """A module-level ``if __name__ == "__main__":`` — the file is runnable."""
    return any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )


def _expected_invocation(rel: str, tree: ast.Module) -> str | None:
    """The invocation that ACTUALLY works, or None for a library module with no CLI
    (the refusal must then carry NO run clause).

    A PACKAGE module is addressed by its dotted path: ``-m`` when it has a CLI,
    and no run clause at all when it is a library. A LOOSE script is run by path,
    whether or not it gates on ``__main__`` — many ``graph-scripts/`` files are
    one-shot scripts that do their work at module level.

    `test_expected_invocation_matches_the_working_form` pins this decision, and
    `test_the_dash_m_invocation_the_refusal_names_actually_resolves` proves the
    ``-m`` path it names resolves.
    """
    dotted = _package_module(rel)
    if dotted is not None:
        return f"uv run python -m {dotted}" if _has_cli(tree) else None
    return f"uv run python {rel}"


def _dash_m_corpus() -> list[Path]:
    return [path for path in _corpus() if _package_module(_rel(path)) is not None]


# ── the detector ─────────────────────────────────────────────────────────────


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_future_import(node: ast.stmt) -> bool:
    return isinstance(node, ast.ImportFrom) and node.module == "__future__"


def _is_bare_sys_import(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Import)
        and len(node.names) == 1
        and node.names[0].name == "sys"
        and node.names[0].asname is None
    )


def _guard_index(body: list[ast.stmt]) -> int | None:
    """Index of the guard `if`, or None if the module preamble is not exactly

        [docstring] [__future__ imports…] `import sys` <guard>

    Requiring that exact preamble is what pins the guard BEFORE every other
    import (statement 1 of the module docstring), without a second rule.
    """
    index = 0
    if body and _is_docstring(body[0]):
        index = 1
    while index < len(body) and _is_future_import(body[index]):
        index += 1
    if index >= len(body) or not _is_bare_sys_import(body[index]):
        return None
    index += 1
    if index >= len(body) or not isinstance(body[index], ast.If):
        return None
    return index


def _floor_in_test(node: ast.If) -> tuple[int, int] | None:
    """`sys.version_info < (major, minor)` — the tuple it compares against, else None."""
    test = node.test
    if not (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Lt)
        and len(test.comparators) == 1
    ):
        return None
    left = test.left
    if not (
        isinstance(left, ast.Attribute)
        and left.attr == "version_info"
        and isinstance(left.value, ast.Name)
        and left.value.id == "sys"
    ):
        return None
    right = test.comparators[0]
    if not isinstance(right, ast.Tuple):
        return None
    values = []
    for element in right.elts:
        if not (isinstance(element, ast.Constant) and isinstance(element.value, int)):
            return None
        values.append(element.value)
    return tuple(values) if len(values) == 2 else None


def _system_exit_message(node: ast.If) -> ast.expr | None:
    """The `SystemExit(...)` argument from a one-statement `raise` body, else None."""
    if len(node.body) != 1 or not isinstance(node.body[0], ast.Raise):
        return None
    exc = node.body[0].exc
    if not (
        isinstance(exc, ast.Call)
        and isinstance(exc.func, ast.Name)
        and exc.func.id == "SystemExit"
        and len(exc.args) == 1
        and not exc.keywords
    ):
        return None
    return exc.args[0]


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=_rel(path))


def _guard_or_fail(path: Path) -> ast.If:
    """The guard node, with a message that says exactly which property failed."""
    body = _parse(path).body
    index = _guard_index(body)
    assert index is not None, (
        f"{_rel(path)}: the #5128 interpreter guard is not the first statement "
        "after the docstring / `__future__` import and `import sys`. It must "
        "precede every other import — a module-level 3.11+-only import "
        "(`from datetime import UTC`) raises ImportError on an older "
        "interpreter before a later guard could run"
    )
    guard = body[index]
    floor = _floor_in_test(guard)
    assert floor == FLOOR, (
        f"{_rel(path)}: the guard must test `sys.version_info < {FLOOR}` "
        f"(the repo contract); got {floor!r}"
    )
    assert _system_exit_message(guard) is not None, (
        f"{_rel(path)}: the guard must raise `SystemExit(<one message>)`"
    )
    return guard


# ── the corpus and the exclusions ────────────────────────────────────────────


def test_the_corpus_is_not_vacuously_empty():
    """A glob that stopped matching must red, never pass vacuously."""
    files = _corpus()
    assert len(files) >= MIN_CORPUS, (
        f"the entry-point corpus collapsed to {len(files)} files (floor "
        f"{MIN_CORPUS}) — a glob that matched nothing would make every "
        "guard assertion below vacuous"
    )
    assert len(files) == len(set(files)), "the corpus must not contain duplicates"


def test_every_corpus_file_is_tracked():
    """The corpus is the SCOPE — an untracked stray must not silently join it.

    Both directions are real assertions now (the old version globbed the
    filesystem and then asserted those same paths were files — it could never
    fail). An untracked, non-ignored ``.py`` under the corpus dirs reds by name,
    and a tracked file that has vanished from disk reds because the corpus can
    no longer be asserted for it.
    """
    strays = [
        rel
        for rel in _git_lines(
            "ls-files", "--others", "--exclude-standard", "--", *CORPUS_DIRS
        )
        if rel.endswith(".py") and Path(rel).name not in EXCLUDED_NAMES
    ]
    assert not strays, (
        f"untracked .py under {CORPUS_DIRS} is not reviewable scope: {strays} — "
        "commit it (so its guard is reviewed) or move it out of tools/ + graph-scripts/"
    )
    missing = [
        rel for rel in sorted(_tracked()) if rel.endswith(".py") and not (ROOT / rel).is_file()
    ]
    assert not missing, f"tracked corpus files absent from disk: {missing}"


def test_the_runtime_39_exclusion_is_accurate():
    """The deliberate 3.9-runnable carve-out must still RUN under a real <3.12
    interpreter.

    This is the reason the guard is NOT applied to ``tools/tmpdir_sweep.py``: it
    is 3.9-clean and its documented scheduled invocation is
    ``/usr/bin/python3 tools/tmpdir_sweep.py`` — the only interpreter a
    minimal-PATH cron is guaranteed. Guarding it would convert a working
    scheduled job into a refusal. Re-derived, not asserted: the moment the file
    stops being 3.9-clean this reds and names it, so the exclusion cannot
    outlive its reason.

    Declared bound: on a 3.12-only runner there is no pre-3.12 interpreter and
    this SKIPS visibly rather than passing.
    """
    old = _pre_312_interpreter()
    if old is None:
        pytest.skip(
            "no <3.12 interpreter on this box: cannot re-derive the deliberately "
            "3.9-runnable exclusion (it runs wherever one exists)"
        )
    for rel, reason in RUNTIME_39.items():
        assert reason, f"{rel}: the exclusion must carry its reason"
        probe = subprocess.run(
            [old, str(ROOT / rel), "--help"], capture_output=True, text=True
        )
        assert probe.returncode == 0, (
            f"{rel} is excluded as deliberately 3.9-runnable ('{reason}') but {old} "
            f"could not run it (rc={probe.returncode}) — guard it and delete the "
            f"exclusion, or restate the exclusion:\n{probe.stderr}"
        )


def test_the_unguardable_exclusion_is_accurate():
    """The single exclusion must still be excluded for the STATED reason.

    Re-derived, not asserted — with the REAL pre-3.12 interpreter when the box
    has one (this machine's `/usr/bin/python3` is Apple CLT 3.9.6, the
    interpreter that filed #5128). `ast.parse(feature_version=(3, 9))` cannot
    stand in: CPython's 3.12 parser does not gate PEP 701 on it (verified —
    (3, 8) through (3, 12) all accept the file). If the excluded file is ever
    made parseable, this reds and names the entry; the correct response is to
    guard it and delete the exclusion, not to widen it.

    Declared bound: on a 3.12-only runner there is no pre-3.12 interpreter to
    re-derive with, and this SKIPS visibly rather than passing. The exclusion
    still has to be justified, and every other corpus file is still asserted
    guarded.
    """
    for rel, reason in UNGUARDABLE.items():
        assert reason, f"{rel}: the exclusion must carry its reason"
        old = _pre_312_interpreter()
        if old is None:
            pytest.skip(
                "no <3.12 interpreter on this box: cannot re-derive the PEP-701 "
                f"exclusion for {rel} (it runs wherever one exists)"
            )
        probe = subprocess.run(
            [old, "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())", str(ROOT / rel)],
            capture_output=True,
            text=True,
        )
        assert probe.returncode != 0 and "SyntaxError" in probe.stderr, (
            f"{rel} is excluded as unguardable ('{reason}') but {old} PARSED it "
            f"(rc={probe.returncode}) — guard the file and delete the exclusion"
        )


# ── the detector's own mutation checks ───────────────────────────────────────


def _body_of(source: str) -> list[ast.stmt]:
    return ast.parse(source).body


def test_detector_rejects_an_unguarded_entry_point():
    """Mutation self-check: no guard at all → no anchor."""
    assert _guard_index(_body_of("import sys\nimport os\n")) is None


def test_detector_rejects_a_guard_behind_the_imports():
    """Mutation self-check: the guard AFTER the import block is not the anchor —
    on a 3.9 interpreter the import would already have failed."""
    body = _body_of(
        "import sys\nimport os\n"
        "if sys.version_info < (3, 12):  # noqa: UP036\n"
        "    raise SystemExit('x')\n"
    )
    assert _guard_index(body) is None


def test_detector_rejects_a_wrong_floor_and_a_non_raising_guard():
    """Mutation self-check: shape alone is not enough — the floor and the raise
    are both asserted."""
    wrong_floor = _body_of(
        "import sys\n"
        "if sys.version_info < (3, 11):  # noqa: UP036\n"
        "    raise SystemExit('x')\n"
    )
    assert _floor_in_test(wrong_floor[1]) == (3, 11)
    assert _floor_in_test(wrong_floor[1]) != FLOOR

    no_raise = _body_of(
        "import sys\n"
        "if sys.version_info < (3, 12):  # noqa: UP036\n"
        "    print('old')\n"
    )
    assert _system_exit_message(no_raise[1]) is None


def test_detector_accepts_the_real_guard_shape():
    """The positive control: the shape the corpus actually carries is accepted."""
    body = _body_of(
        '"""doc."""\n'
        "from __future__ import annotations\n"
        "\n"
        "import sys\n"
        "\n"
        "if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard\n"
        "    raise SystemExit(\n"
        '        f"tools/x.py requires Python >= 3.12 (got "\n'
        '        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "\n'
        '        f"`uv run python tools/x.py`"\n'
        "    )\n"
        "\n"
        "import argparse\n"
    )
    index = _guard_index(body)
    assert index == 3
    assert _floor_in_test(body[index]) == FLOOR
    assert _system_exit_message(body[index]) is not None


# ── the properties, per entry point ──────────────────────────────────────────


@pytest.mark.parametrize("path", _corpus(), ids=_rel)
def test_entry_point_guards_the_interpreter_before_its_imports(path: Path):
    """★ #5128: `import sys` + the guard, then the imports.

    This is the structural half: it fails, naming the file, when a guard is
    removed or moved below the import block.
    """
    _guard_or_fail(path)


@pytest.mark.parametrize("path", _corpus(), ids=_rel)
def test_guard_refuses_an_old_interpreter_and_passes_the_floor(path: Path, monkeypatch):
    """★ #5128: the guard's BEHAVIOUR under a monkeypatched `sys.version_info`.

    The guard node is compiled and executed on its own, so this needs no old
    interpreter and runs none of the tool's real work. It asserts the refusal
    names the floor, the interpreter it actually got, the file, and the
    invocation that works — which is the whole point of the fix (an
    UNATTRIBUTED failure is the harm #5128 was filed for).
    """
    guard = _guard_or_fail(path)
    module = ast.Module(body=[guard], type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, _rel(path), "exec")
    namespace = {"__name__": "__main__", "sys": sys}
    rel = _rel(path)

    monkeypatch.setattr(sys, "version_info", (3, 9, 6))
    with pytest.raises(SystemExit) as refusal:
        # executing the file's OWN guard node, compiled on its own: no tool's
        # real work runs, and no old interpreter is needed.
        exec(code, namespace)
    message = str(refusal.value)
    assert ">= 3.12" in message, f"{rel}: refusal must name the floor: {message!r}"
    assert "(got 3.9)" in message, f"{rel}: refusal must name the interpreter it got: {message!r}"
    assert rel in message, f"{rel}: refusal must name the file: {message!r}"

    invocation = _expected_invocation(rel, _parse(path))
    dotted = _package_module(rel)
    if invocation is None:
        assert "run it as" not in message and "uv run python" not in message, (
            f"{rel}: a library module with no CLI must not be told to RUN it — "
            f"that invocation does not work: {message!r}"
        )
    else:
        assert invocation in message, (
            f"{rel}: refusal must name the invocation that WORKS "
            f"(`{invocation}`): {message!r}"
        )
    if dotted is not None:
        # #5136 P1: for a package module the SCRIPT form is a second
        # unattributed error (ImportError / ModuleNotFoundError) — never name it.
        assert f"uv run python {rel}" not in message, (
            f"{rel}: refusal names the script form, which does NOT work for a "
            f"package module (its relative / `tools.*` import dies): {message!r}"
        )

    monkeypatch.setattr(sys, "version_info", (*FLOOR, 0))
    exec(code, namespace)  # at the floor the guard must be inert


@pytest.mark.parametrize("path", _dash_m_corpus(), ids=_rel)
def test_the_dash_m_invocation_the_refusal_names_actually_resolves(path: Path):
    """★ #5136 P1: every `-m <dotted>` the refusal names must RESOLVE.

    A refusal that names an invocation which itself dies is a SECOND
    unattributed error — the exact harm #5128 was filed for. `find_spec` resolves
    the dotted path from the repo root without importing the module (or its heavy
    deps), so this is cheap and total: a renamed, moved, or un-packaged module
    reds here instead of at the operator's terminal.
    """
    rel = _rel(path)
    dotted = _package_module(rel)
    assert dotted is not None, f"{rel}: not a package module"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util, sys; "
            "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) is not None else 1)",
            dotted,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, (
        f"{rel} refusal names `uv run python -m {dotted}`, but that dotted path "
        f"does not resolve from the repo root:\n{probe.stderr}"
    )


def test_expected_invocation_matches_the_working_form():
    """Pin the decision the refusals are generated from.

    `tools/longmem_eval/run.py` — package module with a CLI → `-m`.
    `tools/longmem_eval/report.py` — package module with NO CLI → no run clause.
    `tools/branch_reaper.py` — loose script → the script form.
    """
    assert _expected_invocation(
        "tools/longmem_eval/run.py", _parse(ROOT / "tools/longmem_eval/run.py")
    ) == "uv run python -m tools.longmem_eval.run"
    assert (
        _expected_invocation(
            "tools/longmem_eval/report.py", _parse(ROOT / "tools/longmem_eval/report.py")
        )
        is None
    )
    assert _expected_invocation(
        "tools/branch_reaper.py", _parse(ROOT / "tools/branch_reaper.py")
    ) == "uv run python tools/branch_reaper.py"


def _pre_312_interpreter() -> str | None:
    """A real `python3` on this box older than the floor, or None.

    This is the interpreter class #5128 is about (bare `python3` → `/usr/bin/
    python3` on macOS). It is used only to RE-DERIVE the documented exclusion,
    never to run a tool.
    """
    candidates = ["/usr/bin/python3", shutil.which("python3")]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run(
            [candidate, "-c", "import sys; sys.exit(0 if sys.version_info < (3, 12) else 1)"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return candidate
    return None


def test_a_named_refusal_beats_an_internal_error_under_a_real_old_interpreter():
    """★ The #5128 acceptance shape, exercised against a REAL refusal path.

    `features` above use a monkeypatched `sys.version_info` because the suite
    must run on 3.12; this one pins the ORDER of the two failures that produced
    #5128 — a 3.11+ stdlib name is unreachable when the guard runs FIRST. It
    executes the real guard of a tool that uses `datetime.UTC`, ahead of that
    name, and asserts the named refusal rather than the AttributeError.
    """
    observed = ROOT / "tools" / "branch_reaper.py"
    assert "datetime.UTC" in observed.read_text(), (
        "the #5128 observed case moved: tools/branch_reaper.py no longer uses "
        "datetime.UTC, so pick the current observed case rather than deleting this"
    )
    guard = _guard_or_fail(observed)
    module = ast.Module(body=[guard], type_ignores=[])
    ast.fix_missing_locations(module)
    version = (3, 9, 6)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "version_info", version)
        with pytest.raises(SystemExit) as refusal:
            exec(compile(module, _rel(observed), "exec"), {"sys": sys})
    message = str(refusal.value)
    assert "INTERNAL ERROR" not in message
    assert "AttributeError" not in message
    assert "requires Python >= 3.12" in message
