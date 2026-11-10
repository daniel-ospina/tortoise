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
* **`tools/merge_throughput.py`** and **`tools/queue_window_observe.py`** are also
  deliberately 3.9-IMPORTABLE (`RUNTIME_39`). Both are stdlib-only and suppress
  `datetime.UTC` modernization (`# noqa: UP017 - must import on 3.9`) precisely so
  the merge-throughput plan's §11 criteria commands — `python3
  tools/merge_throughput.py …` and `python3 tools/queue_window_observe.py …`,
  documented in `docs/ci/merge-throughput-measurements.md` — keep working on the
  ambient `python3` (3.9.6 here). Guarding `merge_throughput.py` additionally
  reds a test in main rather than converting a crash into a named refusal:
  `tests/test_merge_throughput.py::test_tool_runs_under_the_documented_system_python3`
  RUNS it under `/usr/bin/python3` and asserts the contract's exit `2` (a guard
  exits `1`), and `…_for_triage` imports the module under the same interpreter.
  `test_the_runtime_39_exclusion_is_accurate` re-derives both 3.9-runnable claims.
* **`tools/queue_conflict_census.py`** is also deliberately 3.9-RUNNABLE
  (`RUNTIME_39`): it is stdlib-only and 3.9-clean, and its own `USAGE` block
  documents the bare-`python3` form (`python3 tools/queue_conflict_census.py`,
  the ambient 3.9.6) for the daily census series. Guarding it would convert a
  working documented command into a refusal with no diagnostic benefit.
  `test_the_runtime_39_exclusion_is_accurate` re-derives the 3.9-runnable claim.
* **`tools/**/__init__.py`** are package markers, not entry points.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The corpus the guards were applied to (see module docstring).
CORPUS_DIRS = ("tools", "graph-scripts")

#: Every root the doc/usage sweep walks — `CORPUS_DIRS` plus the docs tree. One
#: definition, consumed by the sweep AND by the non-vacuity leg below, so the
#: root LIST cannot drift between them (the leg asks the same containment
#: question, below): a leg that restated this tuple would report a widened
#: root's entries as "outside the swept roots" (false red) and would keep
#: certifying entries a narrowed sweep no longer walks (false pass, in the leg
#: whose whole purpose is catching entries that permit nothing).
DOC_SWEPT_ROOTS = (*CORPUS_DIRS, "docs")

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
    "tools/merge_throughput.py": (
        "deliberately 3.9-IMPORTABLE: stdlib-only, with `datetime.UTC` "
        "modernization suppressed (`# noqa: UP017 - must import on 3.9`) so the "
        "merge-throughput plan's §11 criteria (documented `python3 "
        "tools/merge_throughput.py …`) run on the ambient 3.9 interpreter — "
        "`tests/test_merge_throughput.py::"
        "test_tool_runs_under_the_documented_system_python3` RUNS it under "
        "`/usr/bin/python3` and asserts exit 2, so a guard would red a test in "
        "main rather than convert a crash into a named refusal"
    ),
    "tools/queue_window_observe.py": (
        "deliberately 3.9-IMPORTABLE (mirrors tools/merge_throughput.py): same "
        "suppressed `datetime.UTC` modernization and the same documented "
        "`python3 tools/queue_window_observe.py …` invocations in "
        "docs/ci/merge-throughput-measurements.md — the guard would remove a "
        "working documented command rather than name a crash"
    ),
    "tools/queue_conflict_census.py": (
        "deliberately 3.9-RUNNABLE: stdlib-only and 3.9-clean, and its own "
        "`USAGE` block documents `python3 tools/queue_conflict_census.py` (the "
        "ambient 3.9 interpreter) for the daily census series — the guard would "
        "turn a working documented command into a refusal with no diagnostic "
        "benefit"
    ),
}

#: A small floor: the corpus is the measured tracked `tools/**/*.py` +
#: `graph-scripts/*.py` set. A glob that silently stopped matching must fail,
#: never pass vacuously. (147 at the #5136 rebased head: 155 tracked `.py` in the
#: corpus dirs, minus 3 `__init__.py` package markers, minus `UNGUARDABLE`, minus
#: the 4 `RUNTIME_39` entries.)
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


def _raise_message(node: ast.Raise) -> ast.expr | None:
    """The MESSAGE argument of a `raise SystemExit(<one message>)`, else None.

    An int argument is an exit CODE, not a message: `SystemExit(3)` carries no
    text, so a guard that raised only a code would tell the operator nothing
    about the floor, the interpreter they got, or the invocation that works --
    the unattributed failure #5128 was filed for.

    This rejects the cheap, common case (a NON-STRING `ast.Constant`) and makes
    no attempt to decide an arbitrary expression statically. The BEHAVIOURAL
    half of this harness is what proves the message actually reaches the
    operator, and it answers that for every shape, so the division of labour is:
    this detector catches the obvious mistake early, that test decides.
    """
    exc = node.exc
    if not (
        isinstance(exc, ast.Call)
        and isinstance(exc.func, ast.Name)
        and exc.func.id == "SystemExit"
        and len(exc.args) == 1
        and not exc.keywords
    ):
        return None
    message = exc.args[0]
    if isinstance(message, ast.Constant) and not isinstance(message.value, str):
        return None
    return message


def _system_exit_message(node: ast.If) -> ast.expr | None:
    """The refusal MESSAGE expression from a guard body, else None.

    Two shapes satisfy #5128, and both deliver an attributable refusal:

      * `raise SystemExit(<one message>)` — the historical shape. CPython
        prints a string argument to stderr, but the process then exits 1.
      * `print(<message>, file=sys.stderr)` followed by
        `raise SystemExit(<int>)` — for a guard whose EXIT CODE is itself
        meaningful (#4053). `SystemExit(<str>)` always exits 1 and
        `SystemExit(<int>)` carries no message, so a non-1 status can only be
        expressed by emitting the message explicitly.

    The invariant #5128 protects is that the operator can ATTRIBUTE the
    refusal — not which statement carries it. A `print` with no argument, or a
    `print` not followed by a raise, is not a refusal and is rejected.
    """
    body = node.body
    if len(body) == 1 and isinstance(body[0], ast.Raise):
        return _raise_message(body[0])
    if (
        len(body) == 2
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Call)
        and isinstance(body[0].value.func, ast.Name)
        and body[0].value.func.id == "print"
        and body[0].value.args
        and isinstance(body[1], ast.Raise)
    ):
        return body[0].value.args[0]
    return None


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
        f"{_rel(path)}: the guard must refuse with an ATTRIBUTABLE message — "
        "either `raise SystemExit(<one message>)` or a `print(<message>)` "
        "followed by `raise SystemExit(<int>)`"
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

    Three directions, all real assertions (#6937 P2-2 added the third):

    * *untracked -> corpus*: an untracked, non-ignored ``.py`` under the corpus
      dirs reds by name.
    * *tracked -> disk*: a tracked file that has vanished reds, because the
      corpus can no longer be asserted for it.
    * *tracked -> glob*: a tracked, on-disk file that ``_globbed()`` does not
      reach drops out of ``_corpus()`` and therefore receives **no guard
      assertion at all** — silently. Measured ``∅`` when this was added, so it
      was a LATENT fail-open rather than a live symptom: a symlinked directory
      or a rename the ``rglob`` cannot follow is the way it triggers, and the
      file would go unguarded with every test still green.
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
    # #6937 P2-2: tracked -> glob. Without this, `_tracked()` is consumed only by
    # `missing` above and by `_corpus()`, which INTERSECTS it with the glob — so a
    # tracked file the glob cannot see is silently subtracted from the corpus and
    # never asserted on.
    globbed = {_rel(path) for path in _globbed()}
    unreached = [
        rel for rel in sorted(_tracked()) if rel.endswith(".py") and rel not in globbed
    ]
    assert not unreached, (
        f"tracked corpus files _globbed() never reaches, so they receive no guard "
        f"assertion at all: {unreached} — _globbed() expands {CORPUS_DIRS} with "
        "rglob; a symlinked directory or a rename it cannot follow is the usual "
        "cause, and the file would otherwise go unguarded with every test green"
    )


#: #6937 P2-1: the guarded form, and the carve-outs where the bare form is the
#: DOCUMENTED, WORKING invocation (those tools are never guarded, so `python3`
#: is correct for them — sweeping them would break a scheduled job or a test
#: that RUNS them under `/usr/bin/python3`).
#:
#: Review round 1 widened this from `python3 ` to also catch the absolute-path
#: (`/usr/bin/python3 `) and `./`-prefixed spellings, which the guard refuses
#: just as hard. Review round 2 added the ELIDED form (`python3 ...`): a USAGE
#: block that elides the path still teaches the refused invocation, and it is
#: exactly how `graph-scripts/2146_falkordb_graph_cleanup.py` kept one after its
#: siblings were swept. The elided branch has no path, so no carve-out can
#: exempt it — which is correct, because a reader substitutes a real path.
_BARE_INVOCATION = re.compile(
    r"(?<![\w./])(?:/usr/bin/)?python3\s+"
    r"(?:(?:\./)?(tools|graph-scripts)/([A-Za-z0-9_./-]+\.py)|\.\.\.)"
)
_CARVE_OUTS = frozenset(UNGUARDABLE) | frozenset(RUNTIME_39)

#: Text-bearing files a reader copies an invocation FROM.
_DOC_SUFFIXES = frozenset({".py", ".md", ".sh", ".txt", ".yml", ".yaml"})

#: SURFACES DELIBERATELY OUT OF SCOPE, with reasons — a declared bound, not an
#: oversight. Declared as DATA so the bound is greppable and widening the seam
#: means editing a named thing.
#:
#: Review round 4 reviewed this seam as production code and recommended exactly
#: this: the `.github/workflows` surface had, by then, generated EVERY defect in
#: rounds 1-4 (the "any `.yml`" sweep, the path-substring predicate, the echo
#: arm's over-match) while protecting ZERO live lines — its only bare occurrence
#: was a record already exempted here. A surface whose filter is more fragile
#: than the class it guards is negative machinery, so it is gone rather than
#: patched a fifth time.
_SURFACES_OUT_OF_SCOPE: dict[str, str] = {
    ".github/": (
        "a `run:` line executes on the RUNNER's interpreter, and every workflow "
        "that INVOKES A GUARDED TOOL pins 3.12 via setup-python (measured: all "
        "11 that do). Bare `python3` on the unpinned runners elsewhere in "
        "`.github/` does NOT execute a guarded tool, so no conversion is due. "
        "This also drops `.github/ISSUE_TEMPLATE/*.yml` (human prose; "
        "`bug_report.yml:97` was fixed to the guarded form in review round 3) "
        "from the guard — stated explicitly so the gap is a DECISION, not an "
        "oversight: a future revert there is accepted silently, and that is the "
        "price of not carrying a workflow-text filter whose fragility cost "
        "four review rounds."
    ),
    "config/": (
        "its 3 occurrences are DESCRIPTION; `ci-surfaces.yml:1364` quotes the "
        "bare form as the PROBLEM the guard fixes (\"the documented `python3 "
        "tools/x.py` invocation otherwise dies with an unattributed INTERNAL "
        "ERROR\"), so rewriting it would invert the meaning, and the other two "
        "state what a CI step runs"
    ),
    "tests/": "fixtures, not docs; ~85 hits, none instruction-bearing",
    "root *.md + website/": "measured at 0 hits",
    ".husky/ + extensionless scripts": (
        "keyed out by `_DOC_SUFFIXES`; the hook invokes through its `py_tool` "
        "wrapper, never a bare `python3`, so it carries the guarantee by "
        "construction"
    ),
    "the watched PATH grammar": (
        "`_BARE_INVOCATION` matches only `tools/` and `graph-scripts/` — "
        "`CORPUS_DIRS`, which is the set `_corpus()` defines the guard over. A "
        "GUARDED entry point living outside those two directories would be "
        "invisible here. None exists today; note that an UNGUARDED script under "
        "`docs/runbook/` (e.g. `4107_preference_abstention_diagnostic.py`) is "
        "NOT a miss: with no `sys.version_info` guard there is no contradiction "
        "for bare `python3` to create, which is the only thing this seam is for."
    ),
}

#: Lines where the bare form is DELIBERATELY correct because the text RECORDS or
#: DESCRIBES what a machine did, rather than instructing a reader to run it:
#:
#:   * a verbatim CI log quote — rewriting it makes the receipt describe a
#:     command the runner never printed;
#:   * a `$`-prompted terminal transcript with its captured output;
#:   * a "receipt of record" for a recorded measurement;
#:   * a comment stating what a CI job runs (`python-ci.yml` really does invoke
#:     `python3 tools/mergify_config_guard.py --static` on the runner's 3.12).
#:
#: THE BOUNDARY (review round 2): a captured machine transcript or log is a
#: RECORD and keeps the form that ran. A *reproduction instruction* with expected
#: output — a docs command block, a plan doc's smoke step — is an INSTRUCTION and
#: takes the guarded form, because its purpose is that a reader re-runs it. That
#: is why `docs/plans/2026-09-26-5042-…:209` and
#: `docs/ci/merge-throughput-measurements.md:524` stay `uv run python` while the
#: four entries below do not.
#:
#: #11744 (main red at `3173971283f9`): the `11689` plan doc's entry is the
#: `$`-prompted transcript WITH its captured `FAIL — 1 problem(s): …` output, under
#: "Measured, not argued — the re-take made the required check fail" — the second
#: record bullet above, not a plan-doc smoke step. The paragraph beneath it reads as
#: a receipt too (`main` passes the same command; only the re-take reds it), so the
#: text records what a machine printed rather than asking a reader to reproduce it.
#:
#: Rewriting a record FALSIFIES evidence provenance, which is strictly worse than
#: the doc/guard contradiction this test exists to prevent. Keyed by
#: (path, stripped line) rather than by line NUMBER, so an edit above cannot
#: silently slide an exemption onto a different line: change the text and the
#: entry stops matching, re-flagging it.
_DELIBERATE_BARE_FORM: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "tools/embedder_provision.py",
            "#     22:25:59.10  Run python3 tools/embedder_provision.py --attempts 3 --backoff 5",
        ),
        (
            "docs/research/2026-09-25-4503-edge-accounting/measurement.md",
            "**Raw stage readings, n=5,000 nodes / 4,999 edges** — the shipped "
            "tool's receipt of record, verbatim (`python3 tools/edge_census.py "
            "probe --n 5000 --json`):",
        ),
        (
            "docs/research/2026-09-25-4503-edge-accounting/measurement.md",
            "$ python3 tools/edge_census.py census --uri "
            "'docker://:falkordb@localhost:6379' \\",
        ),
        # ⛔ REVIVED, by #11757's own rule. That PR removed this key as dead and said
        # how it comes back: "an exemption can only be revived by restoring the line
        # it names — never by re-declaring the key." The line IS back: `#11759`
        # (`6ba6fac9844a`) restored the record to the form that ran and stated, in the
        # plan doc itself, that "the line is exempted in
        # `tests/test_entry_point_python_guard.py`'s `_DELIBERATE_BARE_FORM`" — a
        # sentence that was FALSE on `main` until this key returned.
        #
        # The key is neither a mistake nor a re-declaration, which is why the record may
        # rely on it (#11774 review, finding 1). This preamble used to claim "both PRs were
        # correct at their own head and red together"; measured at each PR's OWN head that
        # is false — `7c45d5af1f` (#11756) and `900d4aafc6` (#11757) were EACH red at its own
        # head, so the sentence endorsed the deletion pattern this block exists to warn
        # against (#11777 review round 8, finding 7: the earlier phrasing of this
        # correction was unparseable):
        #   · `#11756` (`7c45d5af1f`) rewrote the record to `uv run python`, leaving
        #     this key matching no live line;
        #   · `#11757` deleted a key that was LIVE at its MERGE PARENT — `215e7d9d8b`
        #     already contained `#11759` (restored 37 s earlier; `#11757` merged 3 min
        #     after it) and the file was GREEN there, so the removal CREATED the doc-scan
        #     red rather than clearing a dead key. It is not the model to copy (#11774).
        #     "Merge parent" is the measured anchor and no other: the key is present (1) at
        #     `#11757`'s own base `24e53f2b22`, where the doc line was already `uv run
        #     python` so the key matched no live line (DEAD), and ABSENT (0) at its own head
        #     `64726782f2` and at its merge commit `900d4aafc6` — so "live at its own head" is
        #     false under both readings of "its own head" this block uses (#11777 review
        #     round 10);
        #   · `#11759` restored the `python3` record, and this key with it, at 15:18:13Z
        #     (`179e688661`) — 15 min after `#11756` merged (15:03:02Z) — and its own merge
        #     (15:45:12Z) preceded `#11757`'s merge parent `215e7d9d8b` (15:45:49Z, PR
        #     #11753's `mergedAt`; the commit's own clock reads 15:45:48Z, which would make the
        #     37 s above a 36 s);
        #     `#11757` removed the key 3 min 35 s after `#11759`'s merge, on a base where it
        #     had just been revived — so the record relied on a key that was live when it was
        #     written, not one "already removed" (#11777 review round 8: an earlier phrasing of
        #     this bullet re-anchored bullet 2's 37 s to `#11756`, where the true gap is 42 min).
        # The merged tree therefore advertised the refused form with nothing exempting
        # it: `test_no_doc_or_usage_advertises_the_form_the_guard_refuses` red on
        # `origin/main` at `900d4aafc61a`, with no branch content involved.
        #
        # Keeping the record verbatim is `#11759`'s recorded decision (THE BOUNDARY
        # above: rewriting a RECORD falsifies evidence provenance), so the key returns
        # rather than the line being rewritten — the resolution that satisfies both
        # PRs' stated rules instead of reversing either.
        (
            "docs/plans/2026-11-10-issue-11689-e1e2-owned-floors.md",
            "$ python3 tools/capstone_preregistration_check.py --dir "
            "docs/evidence/7949-capstone \\",
        ),
    }
)


def test_the_record_exemptions_all_still_match_a_live_line():
    """Review round 4: `_DELIBERATE_BARE_FORM` is keyed on exact line text, so a
    reword or reflow of a legitimate record silently DEADENS its exemption — the
    line is re-flagged, but worse, an entry can also linger for text that no
    longer exists while the occurrence it was written for is now uncovered.
    Assert every key still matches a line in its file, so the set cannot rot.

    NON-VACUITY (#11744): matching a live line is not enough for an entry to do
    any WORK. An entry is INERT — it exempts nothing and reads as though the
    sweep permits something that it does not — when its file is outside the
    swept roots (moved), when its suffix is one the walk skips, when the sweep's
    `git ls-files` intersection does not carry it (untracked), when
    `_BARE_INVOCATION` never matches the line (a stray key), or when EVERY
    invocation on it is already a `RUNTIME_39`/`UNGUARDABLE` carve-out
    (redundant). An inert entry is the same defect as a dead one, one level
    quieter: the set reads as though it guards every record it lists while it
    permits fewer, and nothing else in the file would notice.

    The carve-out branch tests EVERY match, not the first: the sweep exempts the
    whole LINE, so an entry that exempts a second, guarded invocation on the same
    line is doing real work even when the first invocation is a carve-out (#11753
    review). Reporting that entry inert would be a false red.
    """
    dead: list[str] = []
    tracked = set(_git_lines("ls-files", "--", *DOC_SWEPT_ROOTS))
    for rel, line in _DELIBERATE_BARE_FORM:
        path = ROOT / rel
        if not path.is_file():
            dead.append(f"{rel} (file gone)")
            continue
        if line not in {ln.strip() for ln in path.read_text().splitlines()}:
            dead.append(f"{rel} :: {line[:70]}")
            continue
        #: Test containment the way the sweep WALKS it: `rglob` from each root
        #: yields DESCENDANTS only — never the root itself — so "under root X"
        #: means `rel` starts with `X/`, at any nesting depth, and NOT `rel == X`.
        #: That exclusion is load-bearing: a root that is a FILE is walked by
        #: nothing, so an entry keyed on it would exempt nothing while passing
        #: every other branch here. A first-segment test
        #: (`rel.split("/", 1)[0] == root`) agrees only while every root is a
        #: single path segment, and silently false-reds a nested root's entries
        #: (`#11753` review rounds 3-4).
        if not any(
            rel.startswith(root.rstrip("/") + "/") for root in DOC_SWEPT_ROOTS
        ):
            dead.append(f"{rel} (outside the swept roots {DOC_SWEPT_ROOTS})")
        elif path.suffix not in _DOC_SUFFIXES:
            dead.append(f"{rel} (suffix {path.suffix!r} is not walked)")
        elif rel not in tracked:
            dead.append(f"{rel} (untracked: the sweep skips it via `git ls-files`)")
        else:
            matches = list(_BARE_INVOCATION.finditer(line))
            if not matches:
                dead.append(f"{rel} :: {line[:70]} (not a bare invocation)")
            elif all(
                m.group(1) and f"{m.group(1)}/{m.group(2)}" in _CARVE_OUTS
                for m in matches
            ):
                dead.append(f"{rel} :: {line[:70]} (already a carve-out)")
    assert not dead, (
        "these `_DELIBERATE_BARE_FORM` entries no longer match a live line, or "
        f"match one that their exemption does nothing for: {dead}"
    )


_CARDINALITY_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _comment_region_above(prefix: str) -> list[str]:
    """The comment region immediately above the text `prefix` ends at.

    Collected from the bottom up so the region is bounded by the first line that is
    NOT part of a comment block: a non-blank line that does not start with `#`. That
    bound is what keeps the "exactly one occurrence" assertion below meaningful — a
    copy of the sentence higher in the file, past the code that ends the block, is
    outside the region and cannot satisfy the pin.

    Reading the REGION rather than the last contiguous run of `#:` lines is the
    round-2 correction (#11754 review). A blank line, or a bare `#` separator,
    anywhere in the block is a re-wrap of the paragraph — not a deletion of the
    sentence — but scoping to a contiguous `#:` run made every break INSIDE the
    region below the sentence read as *"the comment block no longer carries the
    sentence"*, across a sentence that was present and correct. That message invites
    the author to reword the pattern, i.e. to weaken the pin, so a false red here is
    the failure mode this guard exists to avoid.
    """
    region: list[str] = []
    for line in reversed(prefix.splitlines()):
        if line.strip() and not line.lstrip().startswith("#"):
            break
        region.append(line)
    return list(reversed(region))


def _normalise_prose(prose: str) -> str:
    """`prose` with the round's TWO markdown emphasis markers and its re-wrap removed.

    Exactly two markers are stripped — `*` (which covers `*four*` and `**four**`) and the
    backtick — and runs of whitespace are collapsed to one space. Both are formatting, not
    wording: *"while the four entries below do not"* states the same set size as *"while the
    `four` entries below do not"* or *"while the **four** entries below do not"*.
    Capitalisation is folded downstream, by `_cardinality_of`, so a sentence-initial `Four`
    is read rather than dropped.

    What it does NOT strip, stated rather than implied (#11777 review round 7, finding 2):
    underscore emphasis (`_four_`) and HTML emphasis (`<em>four</em>`) both survive as
    wording and would read as *"the sentence is gone"*. That is a deliberate bound, not an
    oversight — this comment block uses `*`/`**` exclusively (`:595` is the live example,
    a few lines above the sentence), so the other two notations would be a NEW idiom, and
    the marker set is a list a reader can check against the block rather than a claim about
    markdown in general.

    This matters more than it looks: a false red here reports *"the sentence is gone"* and
    hands the author the one remedy that disables the property — *"update the pattern in this
    test"* — so reddening on formatting costs the pin itself (#11775).
    """
    return re.sub(r"\s+", " ", re.sub(r"[*`]", "", prose)).strip()


def _stated_cardinalities(prose: str) -> list[str]:
    """Every `<N>` in a `while the <N> entries below do not` claim in `prose`.

    Matching is deliberately narrow — the sentence, not a fuzzy "a number near the
    word *entries*" — because the pin's value is that a REWORDED sentence reads as
    absent rather than as an approximate hit. `[A-Za-z]+` (rather than `[a-z]+`) is
    what lets `_normalise_prose` leave capitalisation to `_cardinality_of`'s
    lower-casing instead of silently dropping the claim (#11775).
    """
    return re.findall(r"while the ([A-Za-z]+|\d+) entries below do not", prose)


def _cardinality_of(token: str) -> int | None:
    """A cardinality token as `int` — a word from `_CARDINALITY_WORDS`, or digits.

    Digits are accepted so that a set which outgrows the word table is not reported
    as *"the sentence is gone"* when the sentence is present and correct (#11754
    review, finding 4: an 11-member set with prose `eleven` reddened with a message
    telling the author to restore a sentence they had written). Case is folded for
    the same reason — a sentence-initial *Four* is the same claim (#11775).
    """
    return int(token) if token.isdigit() else _CARDINALITY_WORDS.get(token.lower())


def _cardinality_claims_in(source: str) -> list[str]:
    """Every cardinality claim in the `#:` block above `_DELIBERATE_BARE_FORM`.

    ONE seam, deliberately: the region walk, the `#:` filtering, the formatting
    normalisation and the claim match all happen here, so the pieces cannot drift apart
    the way they did when the pin called two helpers inline. Which of those four the
    tests actually MEASURE is stated per arm below, rather than claimed for all four at
    once (#11777 review round 2, findings 1 and 2):

      * region scoping — measured by the decoy arm: a `#:` copy of the sentence ABOVE the
        non-comment line that ends the block, which the whole-file head would satisfy;
      * `#:` filtering — measured by the BARE `#` line INSIDE the region carrying its own
        claim (`seven`), which only the filter excludes. Region scoping does NOT cover
        this one: the bare line is inside the region either way, so dropping the filter
        alone leaves `['seven', 'four']` and reds the arm (measured, #11777 round 3);
      * normalisation and the claim match — measured by the formatting and reword rows.

    The pin's CALL SITE is measured too, and by behaviour rather than by shape — but by a test
    of its OWN, not by the tolerance test. `_assert_the_prose_names_the_set_size` is where the
    pin's assertions live and takes its source as a parameter, so the tolerance test drives that
    function's BODY with synthetic sources; it cannot see the pin TEST's body, so an extraction
    inlined there leaves this seam dead code while the whole file stays green (measured at
    `354ef36c0a`, #11777 review round 7, finding 1: the file was GREEN, and a `**four**` in the
    live sentence then reds with *"no longer carries the sentence"* — the #11775 false red,
    back, with CI green and nothing announcing it). The call site is held the way the body is
    held, by execution: `test_the_prose_pin_is_the_code_path_the_artifact_runs` replaces this
    seam for the duration of one REAL run of the pin test, so a body that reads through the seam
    records the source it was handed and a body that re-implements the extraction records
    nothing and reds there.

    The region is the COMMENT REGION immediately above the definition — bounded by the
    first line that is not part of the comment block — which is what keeps a decoy
    earlier in the file from satisfying the pin while the block that actually describes
    the set drifts (measured, #11754 review: decoy above + prose `three` over a
    four-member set PASSED).
    """
    before = source.partition("_DELIBERATE_BARE_FORM: frozenset")[0]
    prose = _normalise_prose(
        " ".join(
            line[2:].strip()
            for line in _comment_region_above(before)
            if line.startswith("#:") and line[2:].strip()
        )
    )
    return _stated_cardinalities(prose)


def _assert_the_prose_names_the_set_size(source: str) -> None:
    """The `_DELIBERATE_BARE_FORM` prose pin over `source` — its body AND its call site.

    Taking the source as a PARAMETER is what makes this function's BODY drivable by the
    tolerance test below — synthetic sources, no read of this file at that level — and it
    replaces the shape pin that four review rounds failed to get right. A shape pin has to answer *"does
    this function read through the seam?"* by reading its syntax, and every phrasing of that
    question was answered wrongly by a NON-adversarial edit: a text search was defeated by its
    own marker string (round 3); a bare substring check was satisfied by a comment naming the
    seam (round 4); a name-presence check was satisfied by a dead `assert` (round 5); the
    assignment check that replaced it accepted a dead `_dead = _cardinality_claims_in(...)`
    beside an inline copy, while REJECTING a one-line wrapper that legitimately reads the seam
    (round 7, measured in both directions). The lesson is not "tighten the predicate again": a
    shape pin is a second copy of the pin's grammar, so it drifts against the pin exactly as an
    inlined extraction drifts against the seam.

    Here there is one copy and it is EXECUTED. The tolerance test below drives THIS function
    with synthetic sources, so an edit that re-implements the extraction inline in THIS body —
    however it is spelled, wrapped or named — reds on the first `**four**` variant, PROVIDED the
    copy drops any part of the behaviour: the emphasis or whitespace normalisation, the region
    scoping, or the `#:` filter. That is the copy #11775 is about, and it is measured (a verbatim
    `re.findall` inline reds with *"no longer carries the sentence"*).

    This body is NOT the whole call site, and an earlier draft of this docstring said it was:
    the extraction can equally be inlined into the pin TEST's body, which leaves this function
    dead and the tolerance test still green on it, so the file is GREEN with the seam covered by
    nothing — and a `**four**` in the live sentence then reds as *"no longer carries the
    sentence"*, the #11775 false red restored behind a green CI (#11777 review round 7, finding
    1, measured at `354ef36c0a`: inline copy → 553 passed, 2 skipped; inline copy + `**four**`
    → 1 failed, 552 passed). The pin test's body is therefore held separately and the same way,
    by execution: `test_the_prose_pin_is_the_code_path_the_artifact_runs` replaces
    `_cardinality_claims_in` for two real runs of the pin test, so a body that reads through the
    seam takes its VALUE — a deliberately wrong claim must make the pin raise — while a body
    that re-implements the extraction never consults the stub. Call-presence alone is not the
    property: one vestigial `_dead = _cardinality_claims_in(...)` beside an inline copy
    satisfied the earlier version of that test (measured, #11777 review at `a1809fde`).

    The boundary, stated rather than glossed: a copy that re-assembles the same helpers in the
    same order is BEHAVIOURALLY IDENTICAL to the seam, so no behavioural test can tell them
    apart — and telling them apart is not what this property needs. The property is that the
    pin tolerates formatting and reds on drift; a duplicate that preserves both keeps it. What
    a duplicate costs is that `_cardinality_claims_in` stops being the one place the extraction
    lives, which is a reviewable diff choice, not a silently-lost guarantee — and it is why the
    seam's docstring claims one seam rather than claiming a duplicate is impossible.

    Nor does the property disappear with the suite green. The tolerance test below asserts the
    artifact's own prose as well as its synthetic arms, and the pin asserts it through this
    function, so DELETING EITHER test still leaves the other measuring the real file (measured,
    #11777 review round 8, finding 1: before that second call, deleting the pin left the suite
    green with the prose unguarded). Defeating every arm therefore takes TWO edits: an inline
    extraction in this body or in the pin test's body, AND the removal of the test that measures
    THAT body — the tolerance test below, or the call-site test beside it — and, for the
    artifact's own prose, both tests. A visibly larger diff than any edit that leaves one arm
    standing.
    """
    matches = _cardinality_claims_in(source)
    assert matches, (
        "the `_DELIBERATE_BARE_FORM` comment block no longer carries the sentence "
        "'while the <N> entries below do not'. This pin must NOT rot into passing "
        "on absence, because that sentence is what tells a reader how big the "
        "exemption set is. If you REWORDED it, update the pattern in this test to "
        "match the new wording — do not drop the sentence."
    )
    # EXACTLY one. Scoping to the block above is not enough on its own: a second
    # copy of the sentence inside the SAME block satisfies a leftmost `re.search`
    # while the real one drifts, which is a false PASS of the property this test
    # exists for (measured, #11754 review finding 3). Two statements of a set's
    # size is also just ambiguous prose, so requiring one is honest on its own.
    assert len(matches) == 1, (
        "the `_DELIBERATE_BARE_FORM` comment states its cardinality "
        f"{len(matches)} times ({matches}) — the sentence must appear exactly "
        "once, so that a stale copy cannot satisfy this pin while the sentence "
        "describing the set says something else."
    )
    stated = _cardinality_of(matches[0])
    assert stated is not None, (
        f"the `_DELIBERATE_BARE_FORM` comment says 'while the {matches[0]} "
        "entries below do not', which this test cannot read as a number. Write it "
        "as a word from `_CARDINALITY_WORDS` or as digits."
    )
    assert stated == len(_DELIBERATE_BARE_FORM), (
        f"the `_DELIBERATE_BARE_FORM` comment says '{matches[0]}' ({stated}) "
        f"but the set holds {len(_DELIBERATE_BARE_FORM)} entries — the prose "
        "describes a set that no longer exists. Update the word when you add or "
        "remove an entry."
    )


def test_the_record_exemption_prose_counts_the_set_it_describes():
    """The `_DELIBERATE_BARE_FORM` comment states the exemption set's cardinality
    in prose — *"…stay `uv run python` while the <N> entries below do not"*.
    Nothing pinned that number, so it can drift silently: the set gains or loses an
    entry while the prose goes on describing the set it used to be. That is not
    hypothetical — it is what this test was written for, and it is also why the
    prose said "four" over a set whose fourth member had gone dead (#11756 rewrote
    the doc line at source rather than keep the exemption).
    ⛔ Measured at source, because two rounds have now proposed "corrections" to that sentence
    and BOTH invert it: `#11756` (`7c45d5af1f`) rewrote
    `docs/plans/2026-11-10-issue-11689-e1e2-owned-floors.md:310` from `$ python3 …` to
    `$ uv run python …` and KEPT the exemption entry, which then matched no live line (the
    key IS present at that head); `#11757` (`900d4aafc6`) deleted the entry and moved the
    prose `four` -> `three`. Claims that `#11756` "removed the member" or that
    `900d4aafc6` "rewrote the doc line" are false — do not re-apply them (#11777: round 5's
    note made that inversion and is falsified at the named commits; this lane's round-9 edit
    made it again and was reverted).

    The set's own description is the only cardinality a reader has, so it must
    describe THIS set — the same non-rot rule
    `test_the_record_exemptions_all_still_match_a_live_line` applies to the keys.
    """
    _assert_the_prose_names_the_set_size(Path(__file__).read_text(encoding="utf-8"))


def test_the_prose_pin_is_the_code_path_the_artifact_runs(monkeypatch):
    """The PIN TEST's body must read through `_cardinality_claims_in` — measured by CALLING it.

    The tolerance test drives `_assert_the_prose_names_the_set_size`, so it measures that
    function's BODY. It cannot see the pin TEST's body: inline the extraction there and
    `_assert_the_prose_names_the_set_size` becomes dead code that the tolerance test goes on
    exercising, so the whole file stays green — measured at `354ef36c0a` (#11777 review round 7,
    finding 1), and with the inline copy in place a `**four**` in the live sentence reds with
    *"no longer carries the sentence"*, i.e. the #11775 false red is back while CI is green.

    So the call site is held the way the body is held: by execution. `_cardinality_claims_in`
    is replaced for the duration of one real run of the pin test. A body that reads through the
    seam takes its VALUE and reds when the value disagrees; a body that re-implements the
    extraction never consults the stub at all, and reds on ARM 1 below — with the remedy,
    since the tolerance test cannot fail in that direction.

    ⛔ ARM 1 exists because call-presence is NOT the property, and the first version of this
    test asserted only that (`assert seen`, `assert seen[0] == …`). A pin body that keeps ONE
    vestigial `_dead = _cardinality_claims_in(source)` call beside an inline copy satisfies
    both of those while reading nothing from the seam — measured, #11777 review at `a1809fde`:
    `1 passed` with the inline copy shipped, and with `**four**` in the live sentence the pin
    went GREEN on a stray quote elsewhere in this file instead of reding, i.e. exactly the
    cover the round-7 finding said this test removes. A stub whose VALUE must drive the
    outcome cannot be satisfied by a call whose value is discarded.

    The stubbed claim is derived from `len(_DELIBERATE_BARE_FORM)` — the set the prose
    describes — so these arms measure the CALL SITE and nothing else; whether the live prose
    states that size is the pin test's own job, above.
    """
    seen: list[str] = []
    stated = str(len(_DELIBERATE_BARE_FORM))

    # ARM 1 — a WRONG claim from the seam must make the pin RAISE. An inline copy never asks
    # the stub, sees the live (correct) prose, and so never raises: this arm reds, by name.
    monkeypatch.setattr(
        sys.modules[__name__],
        "_cardinality_claims_in",
        lambda source: [str(len(_DELIBERATE_BARE_FORM) + 1)],
    )
    with pytest.raises(AssertionError, match="but the set holds"):
        test_the_record_exemption_prose_counts_the_set_it_describes()

    # ARM 2 — the CORRECT claim must let it pass, and must be handed THIS file's source.
    def record_and_answer(source: str) -> list[str]:
        seen.append(source)
        return [stated]

    monkeypatch.setattr(sys.modules[__name__], "_cardinality_claims_in", record_and_answer)
    test_the_record_exemption_prose_counts_the_set_it_describes()

    assert seen, (
        "the cardinality pin's body no longer reads through `_cardinality_claims_in`. A copy "
        "that re-implements the extraction there leaves the seam dead while every other test "
        "keeps passing (measured, #11777 review round 7), so restore the call to "
        "`_assert_the_prose_names_the_set_size` — that call IS the pin's one path through it."
    )
    assert seen[0] == Path(__file__).read_text(encoding="utf-8"), (
        "the pin reads through the seam, but not with THIS FILE's source — it must hand over "
        "`Path(__file__).read_text(...)`, or the pin measures a sample instead of the artifact."
    )


def test_the_prose_pin_reads_through_formatting_but_not_wording():
    """Non-vacuity of the tolerance, both directions, through the seam AND the pin's body.

    The pin reds through `_cardinality_claims_in`, and the reason the tolerance matters
    is that the pin's failure message prescribes the one remedy that disables it —
    *"update the pattern in this test"*. So a false red here costs the property itself,
    and the tolerance cannot be left untested: without it `**four**`, `` `four` `` and a
    sentence-initial `Four` all reddened as *"the sentence is gone"* while the sentence
    was present and correct (measured, #11754 review F5).

    Two levels, on purpose. The first half drives `_cardinality_claims_in` — the seam — and
    labels each arm with the mechanism it measures. The second half drives
    `_assert_the_prose_names_the_set_size` — the pin's BODY, not its call site — with synthetic
    sources: an inline extraction in THAT body reds here on the first `**four**` variant,
    because an inline copy cannot read emphasis (measured: replacing the body's seam call with
    a non-tolerant `_stated_cardinalities` walk leaves `2 failed` in the targeted selection).
    The pin TEST's body is a DIFFERENT surface that this half cannot see — with the extraction
    inlined there and this seam intact the whole file stayed GREEN at `354ef36c0a` (measured,
    #11777 review round 7, finding 1), which is why
    `test_the_prose_pin_is_the_code_path_the_artifact_runs` holds that surface separately, by
    execution.

    The opposite direction matters just as much — a REWORDED sentence must still read as
    absent, or the pin becomes a fuzzy match that passes on prose it never read.
    """

    def claims(*comment_lines: str) -> list[str]:
        block = "".join(f"#: {line}\n" for line in comment_lines)
        return _cardinality_claims_in(f"{block}_DELIBERATE_BARE_FORM: frozenset({{}})")

    def claims_in_source(source: str) -> list[str]:
        return _cardinality_claims_in(source)

    for variant in (
        "while the four entries below do not.",
        "while the **four** entries below do not.",
        "while the `four` entries below do not.",
        "while the Four entries below do not.",
        "while the FOUR entries below do not.",
    ):
        stated = claims(variant)
        assert stated, f"{variant!r}: a formatting difference must not read as absence"
        assert _cardinality_of(stated[0]) == 4, f"{variant!r} -> {stated}"

    # A re-wrap — the sentence split across lines of the comment block — is formatting
    # too, and the round-2 correction this seam descends from exists for exactly this
    # case. The middle line is an empty `#:` line, which is what the artifact's real
    # paragraph breaks look like (`:601`); a BARE `#` separator is the other shape the
    # region walk tolerates, so both are exercised (#11777 review round 2, finding 2: an
    # earlier version of this comment called an empty `#:` line "a bare `#` separator").
    assert _cardinality_of(claims("while the **four**", "", "entries below do not.")[0]) == 4
    assert _cardinality_of(
        claims_in_source(
            "#: while the **four**\n"
            "#\n"
            "#: entries below do not.\n"
            "_DELIBERATE_BARE_FORM: frozenset({})"
        )[0]
    ) == 4
    # WHITESPACE is the other half of the normalisation and has its own carrier: extra
    # spaces inside a `#:` line, which the `" ".join` does not collapse (#11777 review
    # round 5, finding 1 — this branch had no arm; without it a re-spaced sentence reads as
    # *gone*, which is the false red that hands the author the de-pinning remedy).
    respaced = claims_in_source(
        "#:  while  the four entries below do not.\n_DELIBERATE_BARE_FORM: frozenset({})"
    )
    assert respaced, "a re-spaced sentence must still be READ, not reported as absent"
    assert _cardinality_of(respaced[0]) == 4

    assert claims("while the entries below are exempt") == [], (
        "a reworded sentence must read as ABSENT, not as an approximate hit"
    )
    # A word outside the table is still a CLAIM: the sentence is found and the number is
    # unreadable — reported that way, never silently treated as absent.
    unknown = claims("while the **some** entries below do not.")
    assert unknown == ["some"], unknown
    assert _cardinality_of(unknown[0]) is None
    assert _cardinality_of("fourteen") is None
    # The DIGITS branch, which had no arm of its own before (#11777 review round 4,
    # non-blocking finding 3): a set that outgrows the word table is written `13`, and a
    # pin that could not read a numeral would report the sentence missing while it sat in
    # front of it — the false red this whole change exists to remove.
    assert _cardinality_of("13") == 13
    assert claims("while the 4 entries below do not.") == ["4"]
    assert _cardinality_of(claims("while the 4 entries below do not.")[0]) == 4
    # A duplicate INSIDE the block is two claims — the pin's `len(matches) == 1` arm is
    # what rejects it. (This is the duplicate arm, NOT the scoping arm.)
    assert len(claims("while the four entries below do not.", "while the four entries below do not.")) == 2
    # SCOPING and FILTERING, and the reason they are measured here rather than trusted:
    # a `#:` copy of the sentence ABOVE the code that ends the comment block is a
    # different block and may not be read, and a bare `#` line inside the block is prose
    # the seam does not parse. Dropping EITHER the region walk or the `#:` filter makes
    # this RED — which is what pins those two mechanisms, rather than claiming them
    # measured (#11777 review round 2, finding 1).
    assert claims_in_source(
        "#: while the nine entries below do not.\n"
        "ROOT = Path(__file__).resolve().parent\n"
        "# a bare hash line: while the seven entries below do not.\n"
        "#: while the four entries below do not.\n"
        "_DELIBERATE_BARE_FORM: frozenset({})"
    ) == ["four"], "neither the decoy above the block nor the bare `#` line is a claim"

    # ------------------------------------------------------------------
    # The second level: the PIN ITSELF, with the source as a parameter.
    #
    # Every arm below runs `_assert_the_prose_names_the_set_size` — the body the real pin
    # executes — so what is measured is the code path the artifact goes through, not a
    # helper beside it. Sizes are derived from the live set, so the arms above and the
    # digits ones below do not need editing when the set gains or loses an entry; only the
    # prose does.
    #
    # One conditional limit, stated because a silent coverage loss is the failure this whole
    # change is about (#11777 review round 9, finding 1): `word` is a WORD only while the set
    # fits `_CARDINALITY_WORDS` (twelve). Past that it is `str(size)`, and the two case arms
    # (`Four`/`FOUR`) then degenerate into copies of the plain digits arm, because digits have
    # no case — so case-folding at the CALL SITE stops being measured. The seam-level arms
    # above still measure it, with hard-coded `Four`/`FOUR` and no dependency on the set size
    # (verified RED at size 13, where the call-site arms are the ones that go quiet), so the
    # fold itself is never unpinned; only the duplicate-copy detection loses that one signal.
    # ------------------------------------------------------------------
    size = len(_DELIBERATE_BARE_FORM)
    # `str(size)` when the set outgrows the word table: the digits arms below then carry the
    # coverage, which is the case `_cardinality_of`'s digits branch exists for. Without the
    # default this raises `StopIteration` — an opaque failure on a CORRECT change, which is
    # the false-red-with-a-misleading-signal family this whole PR is about (#11777 round 8,
    # findings 2 and 5).
    word = next((w for w, n in _CARDINALITY_WORDS.items() if n == size), str(size))

    def pin_source(*comment_lines: str) -> str:
        block = "".join(f"#: {line}\n" for line in comment_lines)
        return f"{block}_DELIBERATE_BARE_FORM: frozenset({{}})"

    # Tolerated: the pin must PASS on every formatting of the same claim. Anything here
    # that raises is a false red, and its message would prescribe de-pinning the pattern.
    for variant in (
        f"while the {word} entries below do not.",
        f"while the **{word}** entries below do not.",
        f"while the `{word}` entries below do not.",
        f"while the {word.capitalize()} entries below do not.",
        f"while the {word.upper()} entries below do not.",
        f"while the {size} entries below do not.",
        f"while the **{size}** entries below do not.",
    ):
        _assert_the_prose_names_the_set_size(pin_source(variant))

    # A truly BLANK line inside the block is the third re-wrap carrier, and the one no arm
    # covered before (#11777 round 8, finding 3: reverting `_comment_region_above` to stop the
    # region at a blank line left this whole test green). A legitimate paragraph break must
    # not read as *"the sentence is gone"*.
    _assert_the_prose_names_the_set_size(
        f"#: while the {word}\n\n#: entries below do not.\n_DELIBERATE_BARE_FORM: frozenset({{}})"
    )

    # The artifact's OWN prose, asserted here as well as in the pin, so that deleting EITHER
    # test leaves the other measuring the real file rather than a suite that is green because
    # nothing checks it (#11777 round 8, finding 1 — measured: one deletion used to suffice).
    _assert_the_prose_names_the_set_size(Path(__file__).read_text(encoding="utf-8"))

    # …including a re-wrap (an empty `#:` line between the halves) and a re-spaced line,
    # through the pin: these are the two carriers the seam-level arms above pin
    # individually, and running them here is what makes the pin's own read of them real.
    _assert_the_prose_names_the_set_size(pin_source(f"while the **{word}**", "", "entries below do not."))
    _assert_the_prose_names_the_set_size(
        f"#:  while  the {word} entries below do not.\n_DELIBERATE_BARE_FORM: frozenset({{}})"
    )

    # SCOPING and FILTERING at the CALL SITE, not only in the seam: a pin copy that walked
    # from the top of the file would read the decoy, and one that parsed bare `#` lines would
    # read two claims. Both are measured here through the pin itself, because the seam-level
    # arm above cannot see a copy that re-assembles the seam (measured, #11777 round 7).
    _assert_the_prose_names_the_set_size(
        f"#: while the nine entries below do not.\n"
        "ROOT = Path(__file__).resolve().parent\n"
        f"#: while the {word} entries below do not.\n"
        "_DELIBERATE_BARE_FORM: frozenset({})"
    )
    _assert_the_prose_names_the_set_size(
        f"#: while the {word} entries below do not.\n"
        "# a bare hash line: while the nine entries below do not.\n"
        "_DELIBERATE_BARE_FORM: frozenset({})"
    )

    # REJECTED, and each with the pin's own message — so the pin is exercised in the
    # direction that protects the property, not only in the direction that tolerates edits.
    with pytest.raises(AssertionError, match="no longer carries the sentence"):
        _assert_the_prose_names_the_set_size(pin_source("while the entries below are exempt"))
    with pytest.raises(AssertionError, match="states its cardinality 2 times"):
        _assert_the_prose_names_the_set_size(
            pin_source(f"while the {word} entries below do not.", f"while the {word} entries below do not.")
        )
    with pytest.raises(AssertionError, match="cannot read as a number"):
        _assert_the_prose_names_the_set_size(pin_source("while the some entries below do not."))
    # The drift this whole change is about: prose one above the set it describes.
    with pytest.raises(AssertionError, match="but the set holds"):
        _assert_the_prose_names_the_set_size(pin_source(f"while the {size + 1} entries below do not."))


def test_no_doc_or_usage_advertises_the_form_the_guard_refuses():
    """#6937 P2-1 — the RECURRENCE seam, not a re-assertion of the sweep.

    A guarded entry point REFUSES `python3 <path>` on a pre-3.12 ambient
    interpreter, so any doc or USAGE text advertising that form contradicts the
    guard: the reader copies it and gets a refusal the docs nowhere predict.

    SCOPE, stated exactly (review round 1: the first draft's walk covered only
    three roots while the name claimed "any doc or USAGE text" — a name that
    overclaims its scope is the defect, so the walk was WIDENED to match the
    name rather than the name narrowed to flatter the walk):

      * `tools/` + `graph-scripts/` + `docs/` in full — every tracked
        text-bearing file. The walk is intersected with `git ls-files` (review
        round 4), so a scratch note dropped under a walked root cannot red an
        unrelated commit — measured when a reviewer's probe did exactly that.

    Surfaces deliberately OUT of scope are declared, with reasons, in
    `_SURFACES_OUT_OF_SCOPE` — a bound, not an oversight.

    The one-time sweep fixed the occurrences in place. Without this assertion it
    would simply be a fact about today, re-established by the next person who
    writes a doc — which is exactly the #5128/#5136 failure the guard was created
    for. Re-deriving it here makes the drift unrepresentable.

    The `RUNTIME_39`/`UNGUARDABLE` carve-outs are exempt BY DESIGN and asserted
    as such below: those tools are never guarded, so `python3 <path>` IS their
    working form, and sweeping one would turn a working scheduled cron job into
    a refusal (`test_the_runtime_39_exclusion_is_accurate` runs them to prove
    it).
    """
    offenders: list[str] = []
    tracked = set(_git_lines("ls-files", "--", *DOC_SWEPT_ROOTS))
    for base in DOC_SWEPT_ROOTS:
        for path in sorted((ROOT / base).rglob("*")):
            if not path.is_file() or path.suffix not in _DOC_SUFFIXES:
                continue
            where = _rel(path)
            if where not in tracked:
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            if "python3" not in text:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for match in _BARE_INVOCATION.finditer(line):
                    # The RECORD exemption is checked FIRST (review round 3): it is
                    # keyed on the LINE, not on a tool path, so an elided
                    # `python3 ...` record must be able to use it too. Checking it
                    # only after the elided branch made it unreachable there.
                    if (where, line.strip()) in _DELIBERATE_BARE_FORM:
                        continue
                    tool, rel = match.group(1), match.group(2)
                    if tool is not None and f"{tool}/{rel}" in _CARVE_OUTS:
                        continue
                    offenders.append(f"{where}:{lineno}: {line.strip()[:90]}")
    assert not offenders, (
        "these files advertise `python3 <path>`, the form the entry-point guard "
        "REFUSES on a pre-3.12 interpreter, for a tool that IS guarded. Use "
        f"`uv run python <path>` (or add the tool to RUNTIME_39 with a reason): "
        f"{offenders}"
    )


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


def test_detector_accepts_the_explicit_print_then_exit_shape():
    """The positive control for #4053's shape: print, then `raise SystemExit(int)`.

    `SystemExit(<str>)` always exits 1 and `SystemExit(<int>)` carries no
    message, so a guard whose EXIT CODE is meaningful must emit the message
    itself. That is still an attributable refusal — the #5128 invariant — so the
    detector must accept it rather than report the guard as missing. Without
    this control the acceptance added for #4053 is itself unpinned.
    """
    body = _body_of(
        '"""doc."""\n'
        "from __future__ import annotations\n"
        "\n"
        "import sys\n"
        "\n"
        "if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard\n"
        "    print(\n"
        '        f"collision-preflight: requires Python >= 3.12",\n'
        "        file=sys.stderr,\n"
        "    )\n"
        "    raise SystemExit(3)\n"
        "\n"
        "import argparse\n"
    )
    index = _guard_index(body)
    assert index == 3
    assert _floor_in_test(body[index]) == FLOOR
    assert _system_exit_message(body[index]) is not None


def test_detector_rejects_a_print_with_no_message():
    """A `print()` carrying no message is not an attributable refusal."""
    body = _body_of(
        "import sys\n"
        "if sys.version_info < (3, 12):  # noqa: UP036\n"
        "    print(file=sys.stderr)\n"
        "    raise SystemExit(3)\n"
    )
    assert _system_exit_message(body[1]) is None


def test_detector_rejects_a_bare_exit_code_as_the_refusal():
    """`raise SystemExit(3)` alone is an exit CODE, not an attributable refusal.

    The single-statement shape must carry a MESSAGE. `SystemExit(<int>)`
    carries none, so a guard raising only the code tells the operator nothing
    about the floor, the interpreter they got, or the invocation that works —
    the unattributed failure #5128 exists to prevent. The behavioural test also
    catches this case, but the structural detector must not accept it either.
    """
    body = _body_of(
        "import sys\n"
        "if sys.version_info < (3, 12):  # noqa: UP036\n"
        "    raise SystemExit(3)\n"
    )
    assert _system_exit_message(body[1]) is None


# ── the properties, per entry point ──────────────────────────────────────────


@pytest.mark.parametrize("path", _corpus(), ids=_rel)
def test_entry_point_guards_the_interpreter_before_its_imports(path: Path):
    """★ #5128: `import sys` + the guard, then the imports.

    This is the structural half: it fails, naming the file, when a guard is
    removed or moved below the import block.
    """
    _guard_or_fail(path)


@pytest.mark.parametrize("path", _corpus(), ids=_rel)
def test_guard_refuses_an_old_interpreter_and_passes_the_floor(
    path: Path, monkeypatch, capsys,
):
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
    # The message arrives one of two ways (#4053): as the SystemExit ARGUMENT in
    # the historical `raise SystemExit(<message>)` shape, or on stderr from an
    # explicit `print` when the guard's exit code is itself meaningful
    # (`SystemExit(<int>)` carries no message). Join both so the attribution
    # assertions below hold for either shape; the refusal must be ascribable to
    # the file and the interpreter in BOTH cases, which is the #5128 invariant.
    captured = capsys.readouterr()
    message = str(refusal.value) + captured.out + captured.err
    assert "3.12" in message or "SystemExit" in message, (
        f"{rel}: the guard refused with no message at all: {message!r}"
    )
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

    # ★ #11342: the refusal must not report SUCCESS. The assertions above pin
    # what the message SAYS; without this one a guard that prints the full,
    # attributable message and then `raise SystemExit(0)` passes every check in
    # this file while telling every caller the tool ran clean — the fail-OPEN
    # shape this whole census exists to prevent (measured: `SystemExit(0)`
    # satisfied `_guard_or_fail` and every attribution assertion). Shape-generic
    # on purpose: it stays silent about WHICH non-zero code a file chooses
    # (`test_the_refusal_exit_code_is_the_one_the_tool_can_spare` pins it for the
    # three files below, because which code each can spare is a per-tool contract
    # decision). Other corpus guards still refuse with a bare
    # `SystemExit(<message>)` — exit 1 — where 1 is also the tool's own
    # DRIFT/failure code, `tools/bridge_table.py`, `tools/mcp_rename_table.py`
    # and `tools/sdk_surface.py` among them; that residue predates this PR and is
    # not pinned here.
    #
    # The status is DERIVED, not read off `.code`, and it is a statement about the
    # PROCESS. `SystemExit()` and `SystemExit(None)` leave `.code` as `None` and exit
    # 0 while `None != 0` is True, so `assert code != 0` PASSES them (measured) — that
    # is the fail-open this derivation closes. `SystemExit(False)` also exits 0, and
    # `assert code != 0` happens to RED it, because `False != 0` is False; but that is
    # only because `bool` is an `int`, not because `False` is a status, so the red is
    # an accident of comparison and not something to rely on. CPython turns
    # `.code` into a status with `0 if code is None else (code if isinstance(code, int)
    # else 1)` and the OS then keeps the low 8 bits — so `SystemExit(256)`, `(512)`,
    # `(-256)` and `(768)` leave the process at **0** while the PRE-MASK value is not
    # (measured). Both steps are applied here.
    #
    # Declared bound, because the derivation cannot be total: for an int outside C
    # `long` range CPython passes -1 to exit(), so the PROCESS status is 255 whatever
    # the derivation says (`SystemExit(2**63)` derives 0, `(2**63 + 1)` derives 1 —
    # measured). No guard writes such a code, and the property this assertion checks
    # survives it: a status of 0 is reachable only from `None`, `False`, 0 or an int
    # ≡ 0 (mod 256), and every one of those derives 0.
    # `int.__and__` rather than `&` on purpose: an `int` SUBCLASS may override
    # `__and__`, and a guard could then leave the process at status 0 while this
    # derivation returned non-zero — the false PASS this assertion exists to close
    # (measured with `class Evil(int): __and__ -> 1`). The unbound call is the int
    # arithmetic the OS will apply, whatever the subclass declares.
    _code = refusal.value.code
    _status = (
        0
        if _code is None
        else (int.__and__(_code, 0xFF) if isinstance(_code, int) else 1)
    )
    assert _status != 0, (
        f"{rel}: the guard refused but the PROCESS exits {_status} — a refusal "
        f"that reports success is indistinguishable from the tool having run: "
        f"{message!r}"
    )

    monkeypatch.setattr(sys, "version_info", (*FLOOR, 0))
    exec(code, namespace)  # at the floor the guard must be inert


#: The refusal code each of the three files below CHOOSES (`#4053`, `#11342`) —
#: their guards landed in earlier PRs, and this file only PINS them — and the
#: verdict code it spares, or reuses on purpose. `SystemExit(<message>)` exits 1 in
#: CPython, so for two of these files an old interpreter was reported as the
#: tool's own verdict — `gen_placement_table --check` prints `STALE` and returns
#: 1, and `test_design_map_check` documents 1 as "violations" while reserving 2
#: for the map that could not be read. `cache_evict` is the exception BECAUSE its 1 is
#: already a usage code (`CACHE_EVICTION_EXIT_USAGE`), its verdicts being 2/3/4.
#:
#: The census above cannot assert this: which non-zero code a file picks is a
#: per-tool contract decision, not a property of the guard shape. Pinning it here
#: is what makes the decision survive the next rebase — #11342 was filed after a
#: branch rewrite silently reverted this exact fix, with the whole census green.
REFUSAL_CODES: dict[str, int] = {
    "tools/gen_placement_table.py": 2,
    "tools/test_design_map_check.py": 2,
    "tools/cache_evict.py": 1,
    "tools/ci_runner_topology.py": 4,
    "tools/capstone_ledger.py": 1,
    "tools/build_site_icons.py": 4,
}

#: Why THAT code is the right one for THAT file — quoted in the failure message, so
#: a red tells a fixer which contract it is breaking. Deliberately per-entry: a
#: single generic sentence is wrong for `cache_evict` (whose 1 IS the expected
#: value, not the collision), and a diagnostic that argues against the correct
#: answer sends the next fixer to undo it.
REFUSAL_WHY: dict[str, str] = {
    "tools/gen_placement_table.py": (
        "1 is `--check`'s STALE (main() returns 0/1), so an environment refusal "
        "must not wear it; 2 is never RETURNED by main() (argparse takes it for a "
        "bad flag), so it cannot collide with a verdict"
    ),
    "tools/test_design_map_check.py": (
        "1 is \"violations found\", so an environment refusal must not wear it; "
        "2 is the tool's OWN `Refusal` code (main() returns 0/1/2), reused on purpose"
    ),
    "tools/cache_evict.py": (
        "1 IS this tool's usage code (CACHE_EVICTION_EXIT_USAGE) — its verdicts are "
        "2/3/4 — so exit 1 is CORRECT here, whatever shape the guard uses to reach "
        "it, and it must not be normalised to 2"
    ),
    "tools/ci_runner_topology.py": (
        "1/2/3 are VERDICTS a wrapper branches on (EXIT_FAILED=1 is literally \"a write "
        "did not take, the read-back failed, … a violation survived\", EXIT_USAGE, "
        "EXIT_OWNER_GATED), so an environment refusal must not wear any of them; the "
        "guard takes 4, which the tool otherwise never returns"
    ),
    "tools/capstone_ledger.py": (
        "the set 0-4 is CLOSED and fully spent (CLEAN/INVALID/UNCOVERED/DISABLED/"
        "ADVISORY), so there is no spare code; 1 (EXIT_INVALID) is reused on purpose "
        "— it is the fail-closed neighbour, and closing a row is not reaching a "
        "verdict. Adding 5 to the tool's set would change a documented public "
        "contract to satisfy a lint, which is the wrong trade"
    ),
    "tools/build_site_icons.py": (
        "1 is this tool's OWN failure status — every OTHER refusal is `raise "
        "SystemExit(<message>)` (CPython maps that to status 1), `main()` returns 0 "
        "only and argparse takes 2, so an environment refusal must not wear 1 or a "
        "caller cannot tell 'the tool refused' from 'the interpreter is too old'; "
        "4 is never returned by anything but the guard. Deliberately stated as the "
        "SHAPE, not as a list of line numbers — a count in this field rots silently"
    ),
}


def test_every_refusal_code_carries_its_reason():
    """★ the two tables must describe the SAME files, or the red loses its diagnostic.

    `test_the_refusal_exit_code_is_the_one_the_tool_can_spare` quotes
    ``REFUSAL_WHY[rel]`` **inside its failure message**, so an entry added to
    ``REFUSAL_CODES`` without a matching reason turns that test's red into a bare
    ``KeyError`` — the row still fails, and the sentence naming the contract that
    was broken is exactly what is lost. The mirror case is a ``REFUSAL_WHY`` row
    no code pins: a reason that reads as enforcement while nothing enforces it.

    Pinned as its own row rather than an `assert` between the tables so the
    failure is a counted FAILED test naming both directions, not a collection
    error that takes the other 500-odd rows with it.
    """
    missing_reasons = sorted(set(REFUSAL_CODES) - set(REFUSAL_WHY))
    reasons_without_a_code = sorted(set(REFUSAL_WHY) - set(REFUSAL_CODES))
    assert not missing_reasons and not reasons_without_a_code, (
        f"REFUSAL_CODES entries with no REFUSAL_WHY reason: {missing_reasons}; "
        f"REFUSAL_WHY reasons for a file no code pins: {reasons_without_a_code}"
    )


#: Run a tool as a script under a faked pre-3.12 interpreter. `version_info` is
#: replaced before `runpy` executes the file, so the file's OWN guard node runs
#: and nothing below it does — no old interpreter and no real work needed.
_FORCED_39 = (
    "import runpy, sys\n"
    "sys.version_info = (3, 9, 6, 'final', 0)\n"
    "runpy.run_path(sys.argv[1], run_name='__main__')\n"
)


@pytest.mark.parametrize("rel", sorted(REFUSAL_CODES))
def test_the_refusal_exit_code_is_the_one_the_tool_can_spare(rel):
    """★ #11342: an environment refusal must not wear a VERDICT's exit code.

    Measured as a real process, not by reading the guard node's `.code` alone: the
    whole point is the status a CI step or a lane branches on. The expected code
    is asserted exactly rather than "non-zero", because for the two drift/
    violation gates the collision is specifically with **1** — an exact pin is
    what reds if the `#4053` shape is ever reverted to
    `raise SystemExit(<message>)`, whose CPython exit status is 1.
    """
    expected = REFUSAL_CODES[rel]
    probe = subprocess.run(
        [sys.executable, "-c", _FORCED_39, str(ROOT / rel)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == expected, (
        f"{rel}: an old interpreter must exit {expected}, got {probe.returncode} "
        f"(stderr={probe.stderr!r}) — {REFUSAL_WHY[rel]}"
    )
    assert ">= 3.12" in probe.stderr and rel in probe.stderr, (
        f"{rel}: the refusal must stay attributable on stderr: {probe.stderr!r}"
    )
    assert "Traceback" not in probe.stderr, (
        f"{rel}: a refusal must not be a traceback: {probe.stderr!r}"
    )


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


def test_the_site_icon_guard_names_an_invocation_that_actually_resolves(
        monkeypatch, capsys):
    """#11507 review (round 7) — the message's INVOCATION is a claim no leg read.

    The census pins that a refusal names an invocation at all
    (`test_guard_refuses_an_old_interpreter_and_passes_the_floor` asserts the
    `uv run python <path>` prefix is present), and that prefix is satisfied by BOTH
    the bare path and the `--help` form. Measured: reverting this tool's message to
    the pre-fix bare path left the whole census at `536 passed, 2 skipped` — the one
    unpinned claim of the change, and the one whose reversion ships the exact
    SECOND unattributed error #5128 was filed for.

    So it is pinned by PROVING the claim rather than by reading the string alone:
    the form the refusal names must resolve, and the bare path must NOT — this tool
    takes `--source` as `required=True`, so running it bare exits 2 on argparse's
    own usage error. A future guard whose advice is a usage error reds here.
    """
    path = Path(__file__).resolve().parents[1] / "tools" / "build_site_icons.py"
    guard = _guard_or_fail(path)
    module = ast.Module(body=[guard], type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, _rel(path), "exec")

    monkeypatch.setattr(sys, "version_info", (3, 11, 9))
    with pytest.raises(SystemExit):
        exec(code, {"__name__": "__main__", "sys": sys})
    captured = capsys.readouterr()
    message = captured.out + captured.err

    assert "tools/build_site_icons.py --help" in message, (
        "the refusal must name the `--help` form, which needs no `--source` "
        f"token: {message!r}")

    # The claim, proved rather than asserted: run what the message names, and the
    # form it deliberately does NOT name.
    resolved = subprocess.run([sys.executable, str(path), "--help"],
                              capture_output=True, text=True)
    assert resolved.returncode == 0, (
        "the invocation the refusal names must resolve; it exited "
        f"{resolved.returncode}: {resolved.stderr[-400:]!r}")
    bare = subprocess.run([sys.executable, str(path)],
                          capture_output=True, text=True)
    assert bare.returncode == 2, (
        "the bare path must stay a USAGE error (argparse, `--source` is "
        f"required) — if it stops being one, this guard's advice is no longer the "
        f"only form that resolves: rc={bare.returncode}, {bare.stderr[-300:]!r}")


def test_the_residual_scan_guard_names_an_invocation_that_actually_resolves(
        monkeypatch, capsys):
    """#11744 review (round 3) — the same unpinned-claim defect, one tool over.

    `tools/embedding_residual_scan.py` gained the `#5128` guard with a refusal
    naming the BARE path, which does not resolve: the tool takes `--org`,
    `--graph-id` and `--graph-name` as `required=True`, so the bare form exits 2
    on argparse's own usage error. The census pins only that an invocation is
    named at all (`test_guard_refuses_an_old_interpreter_and_passes_the_floor`
    asserts the `uv run python <path>` prefix), and that prefix is satisfied by
    BOTH the bare path and the `--help` form — so reverting this message to the
    bare path leaves the whole census green, which is exactly the shape
    `test_the_site_icon_guard_names_an_invocation_that_actually_resolves`
    (#11507 round 7) was written for.

    Pinned by PROVING the claim rather than by reading the string alone: the form
    the refusal names must resolve, and the bare path must NOT.

    ⚠️ `rc == 2` alone does not measure "a USAGE error" here: `2` is also this
    tool's documented NO-VERDICT (`tortoise/embedding_residual_scan.py:78`
    `EXIT_UNAVAILABLE = 2`, returned when `--graph-uri` is absent), so making all
    three required arguments optional would leave the bare form parsing, refusing
    the graph half, and still exiting 2 — green on the code while the property the
    message states is false. (One of the three alone is not enough: the other two
    still fail at parse time, which is why the assert looks redundant until you
    read the tool's exit contract.) The CAUSE is pinned too (`#11744` review,
    round 4).
    """
    path = (Path(__file__).resolve().parents[1]
            / "tools" / "embedding_residual_scan.py")
    guard = _guard_or_fail(path)
    module = ast.Module(body=[guard], type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, _rel(path), "exec")

    monkeypatch.setattr(sys, "version_info", (3, 11, 9))
    with pytest.raises(SystemExit) as refusal:
        exec(code, {"__name__": "__main__", "sys": sys})
    # `raise SystemExit(<str>)` carries the message on the exception, not on
    # stdout/stderr — the census reads it the same way (`str(refusal.value)`).
    captured = capsys.readouterr()
    message = str(refusal.value) + captured.out + captured.err

    assert "tools/embedding_residual_scan.py --help" in message, (
        "the refusal must name the `--help` form, which needs no `--org` / "
        f"`--graph-id` / `--graph-name` token: {message!r}")

    resolved = subprocess.run([sys.executable, str(path), "--help"],
                              capture_output=True, text=True)
    assert resolved.returncode == 0, (
        "the invocation the refusal names must resolve; it exited "
        f"{resolved.returncode}: {resolved.stderr[-400:]!r}")
    bare = subprocess.run([sys.executable, str(path)],
                          capture_output=True, text=True)
    assert bare.returncode == 2, (
        "the bare path must stay a USAGE error (argparse, `--org` is "
        f"required) — if it stops being one, this guard's advice is no longer the "
        f"only form that resolves: rc={bare.returncode}, {bare.stderr[-300:]!r}")
    # `2` is not unique to argparse on this tool (it is also `EXIT_UNAVAILABLE`),
    # so the CODE above cannot distinguish "usage error" from "a store could not
    # be read". Pin argparse's own text, which only the missing-token path emits.
    assert "the following arguments are required" in bare.stderr, (
        "the bare path exited 2, but not on argparse's missing-token error — `2` "
        "is also this tool's NO-VERDICT exit, so the code alone does not prove "
        f"the bare path is unusable: {bare.stderr[-300:]!r}")
