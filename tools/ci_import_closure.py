#!/usr/bin/env python3
"""Static import-closure derivation for CI test selection (#10132).

WHY THIS EXISTS
    `surfaces.core` in `config/ci-surfaces.yml` is the bluntest instrument in the
    selector: any change to a core module selects the whole `core` set. The map is
    HAND-WRITTEN (`tools/ci_selection.py::SOURCE_PATTERNS`) while the real
    dependency between a test file and the package is IMPORTS. This tool derives
    the real thing — for each core test file, the transitive set of `tortoise/**`
    modules it imports — and a validation harness that asks the only question
    that matters for a narrower selection: *given a historical diff, would the
    derived mapping have selected every test that ACTUALLY failed on it?*

    It changes NO selection. `tools/ci_selection.py` is untouched by design (its
    unknown-path arm returns `_full_selection()` "fail-closed by design"), and a
    narrowing that could let a real failure through is not a candidate for
    adoption inside this issue. The tool exists to produce the evidence for — or
    against — a later, separately-reviewed change.

STATIC ANALYSIS ONLY
    Modules under analysis are NEVER imported. Importing to inspect them has
    side effects (this package opens stores, starts reapers, touches redislite)
    and would measure the wrong thing — what happens to load, not what the source
    says. Every edge here comes from `ast` over the file's text.

THE FOUR SHAPES THAT SILENTLY UNDER-SELECT, AND WHAT THIS TOOL DOES WITH THEM
    A closure that only reads top-level imports looks complete while missing
    edges, which is exactly how a derived selection ships a false green. So each
    shape is handled explicitly and NAMED in the output:

    1. **Function-local imports** — WALKED (`ast.walk`-equivalent recursion over
       the whole tree, tracking nesting) and tagged `in_function`. The suite uses
       them heavily (a helper imported at call time is still a dependency of any
       test that calls it).
    2. **`TYPE_CHECKING` blocks** — COLLECTED AND DEPENDED ON, tagged
       `type_checking`. This is the opposite of what a boundary tool does: `tach`
       skips `TYPE_CHECKING` imports by default because its question is *runtime
       coupling*, where a type-only import is legitimately not a dependency. Our
       question is *would a change here be observable by this test*, and the safe
       direction is over-approximation — a type-only module that starts being
       imported for real is a real edge, and one extra test is cheaper than a
       false green. `--exclude-type-checking` reproduces tach's polarity.
    3. **`from __future__ ...`** — NOT a dependency (no `tortoise/**` target can
       match it); counted and named separately so the exclusion is visible.
    4. **Re-export shims** — `tortoise/__init__.py` is reached by every
       `import tortoise.x` at runtime, because importing a submodule first imports
       the package. That edge is modelled explicitly (`package-init`) rather than
       left implicit, because it is the single biggest threat to a file-level
       split: if most tests transitively depend on `__init__`, splitting by
       closure cannot help. `tortoise/__all__.py` is a data module of approved
       names, not a re-export surface; it is reported when a test imports it.

    Anything the analysis CANNOT resolve is reported as `UNKNOWN` with a reason
    (`syntax`, `unresolved-name`, `dynamic-import-call`, `dynamic-module-attr`,
    `module-loader`) and never dropped. A file with any UNKNOWN is `tainted`:
    its closure may be incomplete, so it is counted separately and its misses in
    the validation harness are reported as `tainted-closure` rather than
    `no-import-path`.

VERBS
    closure    derive the per-test closure; print a summary table; `--json PATH`.
    validate   replay a window of merged PRs: for each, the tests that ACTUALLY
               failed (from the run's `junit.xml` artifacts) vs the derived
               selection; report per-PR misses.
    report     emit the report data as JSON (stdout; `--json PATH` to also
               write it). It does NOT render `docs/ci/core-import-closure.md` —
               that document is hand-written and checked in.

WHY junit AND NOT "the job was red"
    A red job is not a failing test. Measured on #10070 (`test (f)`): the job
    concluded `failure` while `junit.xml` recorded 2 907 tests and ZERO failures —
    the red came from a post-test hygiene step ("Assert no redislite orphans").
    Treating a red job as ground truth would inject phantom misses into the
    harness and condemn a derivation that is fine. The harness therefore reports
    `job_failed_without_test_failure` separately, and only a `<failure>`/`<error>`
    testcase is a missed test.

    ⛔ NO NETWORK IN `closure`. Only `validate` calls `gh`, and it caches every
    artifact, every per-PR ground truth, every diff list and every MERGED PR's
    metadata under `.ci-selection/closure-cache/` (gitignored), so a re-run of an
    explicit `--prs` window is offline. Two reads are deliberately NOT cached
    because they describe a LIVE thing: the window listing itself
    (`merged_pr_numbers` — a re-paged window must see merges that happened since)
    and an OPEN PR's head, which moves on every push (#10143 review P3).

Usage:
    uv run python tools/ci_import_closure.py                    # summary table
    uv run python tools/ci_import_closure.py --json out.json
    uv run python tools/ci_import_closure.py --module tortoise/sdk.py
    uv run python tools/ci_import_closure.py validate --window 12
    uv run python tools/ci_import_closure.py report
"""

from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter BEFORE the imports below. This file is
# an entry point under `tools/`, so it is in the corpus `tools/../
# tests/test_entry_point_python_guard.py` asserts over — and a module-level
# 3.12+-only construct would otherwise raise ImportError/AttributeError first,
# with no attribution. `import sys` is the ONE import the preamble allows ahead
# of the guard, because the guard has to read `sys.version_info`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        "tools/ci_import_closure.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — the repo floor is "
        "`requires-python` / `.python-version`. Run it as "
        "`uv run python tools/ci_import_closure.py`."
    )

import argparse
import ast
import json
import os
import statistics
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO / ".ci-selection" / "closure-cache"

# Bump when the GROUND-TRUTH EXTRACTION changes shape. The cache exists so a
# re-validation is offline and reproducible — but a truth written by an older
# extractor is a CACHED TRUNCATION, and `pr_ground_truth` short-circuits on
# `cached.exists()`, so a fixed extractor would keep reading the old answer
# forever. #10143 review P2 (v1 → v2): v1 dropped an artifact that carried no
# `junit.xml` and an artifact whose `junit.xml` did not parse, both WITHOUT an
# `artifact_errors` entry, and then cached the result because `artifact_errors`
# was empty — so "we could not read the ground truth" was indistinguishable from
# "nothing failed", permanently. Nothing reads a v1 file now.
# #10143 review P2 (v2 → v3): the truth gained `runs_seen` / `test_legs_seen` and
# the sibling-leg masking check, so a v2 file cannot answer "did I see any ground
# truth at all". Nothing reads a v2 file now.
# #10143 review P3 (v3 → v4): the EXTRACTION changed twice without a bump — the
# per-run leg scoping + the `executed`/zero-testcase gate (a red leg with an
# empty junit is now unread, not clean) and the candidate artifact names +
# `junit-<label>.xml` member matching (`ARTIFACT_NAME_OVERRIDES`,
# `_is_junit_member`). A v3 file was written by the reader that reported three
# workflow legs' own artifacts as absent and could not open `junit-d14.xml` at
# all, so a warm v3 cache re-answers the OLD question forever — which is exactly
# why the re-derivation of §3 must not be allowed to short-circuit on one.
# #10143 review P2 (v4 → v5): the EXTRACTION changed again, one granularity down,
# and the KEY SET did not — which is why the shape pin above could not see it.
# The zero-testcase gate became PER ARTIFACT (a leg whose junit described 0
# testcases now writes its own `artifact_errors` entry; before, a sibling leg in
# the same RUN with 500 testcases masked it entirely), so a v4 file written by the
# pre-fix reader reports `artifact_errors: []`/`[]` for a state that is now
# unread, and `pr_ground_truth` short-circuits on `cached.exists()`. A warm v4
# cache therefore re-answers the OLD question forever — including on this lane's
# own machine, where every earlier round wrote one, `.ci-selection/` being
# gitignored at `.gitignore:69`. Nothing reads a v4 file now.
#
# `test_the_truth_cache_shape_is_pinned_to_its_version` pins the coupling: the
# shape the truth writes is recorded per version, so a shape change without a
# bump fails, and a bump without a recorded shape fails too.
# `test_a_new_artifact_errors_producer_forces_a_ground_truth_version_bump` pins
# the other half — the RULES. A shape key set cannot see this class at all (v4 →
# v5 added no key), so the number of `artifact_errors` producers is recorded per
# version too: adding one without a bump fails, and bumping without re-recording
# fails.
GROUND_TRUTH_VERSION = 5

# The package whose modules a closure REPORTS. Other in-repo roots (`tests/`,
# `battery/`, `tools/`) are indexed and TRAVERSED — a test that imports
# `tests/_live_utils` inherits whatever that helper imports — but only modules
# under `REPORTED_ROOT` appear in a closure, because those are the paths the
# selector keys on.
REPORTED_ROOT = "tortoise"

# Directories a static analysis must never descend into, PRUNED (never walked
# into) by the fallback enumeration. The list is the venv / VCS / dependency
# trees; everything else in the repo IS part of the index, because a bounded
# root list silently drops a package an import can legitimately name.
_SKIP_DIR_PARTS = frozenset({
    ".venv", ".git", ".worktrees", "node_modules", "battery-out", "__pycache__",
    "site-packages", "dist", "build",
})

# Names that resolve to the `importlib` machinery for a dynamic import call.
_DYNAMIC_IMPORT_ATTRS = frozenset({
    "import_module", "__import__", "run_module", "load_module",
    "spec_from_file_location", "module_from_spec", "SourceFileLoader",
})
# The subset that loads a module from a PATH rather than a dotted name.
_LOADER_NAMES = frozenset({"spec_from_file_location", "module_from_spec", "SourceFileLoader"})


# ---------------------------------------------------------------------------
# UNKNOWN reasons — every one of these is a way the closure could UNDER-select.
# ---------------------------------------------------------------------------
UNKNOWN_SYNTAX = "syntax"
UNKNOWN_UNRESOLVED = "unresolved-name"
UNKNOWN_DYNAMIC_CALL = "dynamic-import-call"
UNKNOWN_DYNAMIC_ATTR = "dynamic-module-attr"
UNKNOWN_LOADER = "module-loader"

# Edge kinds.
DIRECT = "direct"
PACKAGE_INIT = "package-init"
FROM_SUBMODULE = "from-submodule"
DYNAMIC_LITERAL = "dynamic-literal"


# ---------------------------------------------------------------------------
# Module index
# ---------------------------------------------------------------------------
def _git_py_files(repo: Path) -> list[str]:
    """Tracked `*.py` paths, repo-relative POSIX strings.

    `git ls-files` is index-bounded (it never walks `.worktrees/`,
    `node_modules/`, or the venv), which is the difference between a 3 s scan and
    a 60 s one. Falls back to a pruning walk of the WHOLE repo.

    ⛔ THE FALLBACK IS NOT A BOUNDED ROOT LIST. #10143 review P2: it used to walk
    `("tortoise", "tests", "battery", "tools")` only, so any in-repo package
    outside those roots was invisible — and an import naming one was silently
    treated as THIRD-PARTY, with no `UNKNOWN` recorded. MEASURED: the two
    enumerations differed by 150 tracked files, and exactly one core closure
    changed answer — `tests/test_8491_cal_attendee_grounding.py` imports
    `integrations.crm.twenty.bridge`, so the walk reported a 0-module,
    UNTAINTED closure (187 → 0) and the file would have been re-classed
    `non-python-dependency`. A different answer to the same question, silently,
    is the one thing this tool must never produce. Pruning the whole repo makes
    the two paths enumerate the same package set; the only remaining difference
    is a file git does not track (absent in CI, where the tree is clean).
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--", "*.py"],
            capture_output=True, check=True, timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return _walk_py_files(repo)
    return sorted(p for p in out.decode().split("\0") if p)


def _walk_py_files(repo: Path) -> list[str]:
    """Every `*.py` under `repo`, pruning the trees a static analysis must not
    descend into (`.git`, `.worktrees`, `node_modules`, the venv, build output)."""
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIR_PARTS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                paths.append(Path(dirpath, name).relative_to(repo).as_posix())
    return sorted(paths)


def module_name(rel: str) -> str:
    """`tortoise/sdk.py` -> `tortoise.sdk`; `tortoise/x/__init__.py` -> `tortoise.x`."""
    parts = rel.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class ModuleIndex:
    """Dotted name -> repo-relative file, plus the reverse map.

    Namespace packages (a directory with no `__init__.py`, e.g. `tests/`) are
    resolvable but contribute no file: `tests.foo` resolves to `tests/foo.py`
    while `tests` itself is only a prefix.

    ⛔ PACKAGE PRECEDENCE MATCHES CPYTHON. When a package and a module share a
    dotted name (`tortoise/projection/__init__.py` beside `tortoise/projection.py`
    — one such pair exists in this repo), `import tortoise.projection` binds the
    PACKAGE: `FileFinder` tries the directory's `__init__` before the same-named
    `.py`. Resolving to the module instead UNDER-SELECTS by every module that
    only the package reaches. (How many is a property of the tree, not a
    constant: on this tree the one shadowing pair no longer changes any core
    closure, which is why the pin is the SYNTHETIC case in
    `tests/test_ci_import_closure.py` rather than a recorded delta.) The
    shadowed file is recorded in `shadowed`, not silently discarded: it is still
    scanned (it may be imported by path), it just does not own the dotted name.
    """

    def __init__(self, files: list[str]) -> None:
        self.by_module: dict[str, str] = {}
        self.by_file: dict[str, str] = {}
        self.shadowed: list[dict] = []
        self.namespace_dirs: set[str] = set()
        self.in_repo_roots: set[str] = set()
        packages = [rel for rel in files if rel.rsplit("/", 1)[-1] == "__init__.py"]
        modules = [rel for rel in files if rel.rsplit("/", 1)[-1] != "__init__.py"]
        for rel in modules + packages:  # packages LAST: they win
            name = module_name(rel)
            if not name:
                continue
            prior = self.by_module.get(name)
            if prior is not None:
                self.shadowed.append({"module": name, "ignored": prior, "owner": rel})
            self.by_module[name] = rel
            self.by_file[rel] = name
            self.in_repo_roots.add(name.split(".")[0])
        # A dotted name whose directory exists but has no `__init__.py` is a
        # namespace package: resolvable, no file of its own.
        for rel in files:
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            while parent:
                if parent.replace("/", ".") not in self.by_module:
                    self.namespace_dirs.add(parent.replace("/", "."))
                parent = parent.rsplit("/", 1)[0] if "/" in parent else ""

    def resolve(self, dotted: str) -> str | None:
        """Repo-relative file for `dotted`, or None when it is not a module file."""
        return self.by_module.get(dotted)

    def exists(self, dotted: str) -> bool:
        return dotted in self.by_module or dotted in self.namespace_dirs

    def is_in_repo_root(self, dotted: str) -> bool:
        return dotted.split(".")[0] in self.in_repo_roots

    def ancestors(self, dotted: str) -> list[str]:
        """Every ancestor package, outermost first (`a.b.c` -> `a`, `a.b`)."""
        parts = dotted.split(".")
        return [".".join(parts[:i]) for i in range(1, len(parts))]


# ---------------------------------------------------------------------------
# AST scanning
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Dep:
    """One resolved import edge out of a file."""

    target: str          # dotted module name
    kind: str            # DIRECT / PACKAGE_INIT / FROM_SUBMODULE / DYNAMIC_LITERAL
    line: int
    type_checking: bool = False
    in_function: bool = False

    def as_dict(self) -> dict:
        d = {"target": self.target, "kind": self.kind, "line": self.line}
        if self.type_checking:
            d["type_checking"] = True
        if self.in_function:
            d["in_function"] = True
        return d


@dataclass
class Unknown:
    """Something the static analysis could not resolve — never silently dropped."""

    reason: str
    line: int
    detail: str

    def as_dict(self) -> dict:
        return {"reason": self.reason, "line": self.line, "detail": self.detail}


@dataclass
class FileScan:
    """The import edges (and unknowns) of one file."""

    rel: str
    module: str
    deps: list[Dep] = field(default_factory=list)
    unknown: list[Unknown] = field(default_factory=list)
    future_imports: list[str] = field(default_factory=list)

    @property
    def tainted(self) -> bool:
        return bool(self.unknown)


def _is_type_checking_guard(expr: ast.expr) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` (and any conjunction of it).

    ⛔ `if not TYPE_CHECKING:` IS NOT A TYPE-CHECKING BLOCK (#10143 review P2).
    `ast.walk` found the `TYPE_CHECKING` name ANYWHERE in the test, so the NEGATED
    form — the standard idiom for a runtime import that a type checker cannot
    see — classified its body as type-only, and `--exclude-type-checking` then
    DELETED a genuinely runtime edge. That is the same axis inversion the
    `_dedupe_deps` fix removed: a reader must never drop the other axis's edges.
    """
    return _is_type_only_test(expr)


def _is_type_only_test(expr: ast.expr) -> bool:
    """Is this test expression TYPE-CHECKING-ONLY (never true at runtime)?

    ⛔ NOT `any(TypeError name in the test)` (#10143 review P3). `ast.walk` accepted
    ANY expression that merely CONTAINED the name, so `if TYPE_CHECKING or
    <runtime term>:` tagged its body type-only — and `--exclude-type-checking`
    then deleted a body of runtime edges, the same under-selection the `not` arm
    above was fixed for. The question is what the expression is FALSE-ALWAYS at
    runtime, which is a property of the OPERATOR, not of a name occurring in it:

    * a bare `TYPE_CHECKING` name/attribute: false at runtime → type-only;
    * `A and B`: false as soon as ANY operand is type-only (so the suite cannot
      run at runtime — `TYPE_CHECKING and x` is type-only, whatever `x` is);
    * `A or B`: false at runtime only if EVERY operand is type-only — one
      runtime operand and the body DOES execute;
    * `not X`: never type-only (the negated guard is the runtime idiom);
    * anything else (a call, a comparison, a subscript that merely mentions the
      name): NOT type-only. Over-tagging drops real edges under
      `--exclude-type-checking`; under-tagging only over-selects, which is the
      safe direction for a selector.
    """
    if isinstance(expr, ast.Name):
        return expr.id == "TYPE_CHECKING"
    if isinstance(expr, ast.Attribute):
        return expr.attr == "TYPE_CHECKING"
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return False
    if isinstance(expr, ast.BoolOp):
        if isinstance(expr.op, ast.And):
            return any(_is_type_only_test(v) for v in expr.values)
        if isinstance(expr.op, ast.Or):
            return bool(expr.values) and all(
                _is_type_only_test(v) for v in expr.values)
    return False


def _nodes_with_context(tree: ast.AST):
    """Yield `(node, in_type_checking, in_function)` for EVERY node in the tree.

    A recursive walk rather than `ast.walk` because both pieces of context are
    what the closure needs: a `TYPE_CHECKING` import is type-only, and a
    function-local import only executes on call — but for a TEST file the call
    happens, and for a module reached through a test the safe direction is to
    count it. Skipping either would under-select.

    EVERY node, not only import nodes, because a dynamic import
    (`importlib.import_module`, `spec_from_file_location`) is an `ast.Call` and
    needs the same two axes: hardcoding them (the pre-#10143-review shape) tagged
    a MODULE-LEVEL dynamic import as function-local, which
    `--import-time-only` then dropped.
    """
    stack: list[tuple[ast.AST, bool, bool]] = [(tree, False, False)]
    while stack:
        node, in_tc, in_fn = stack.pop()
        yield node, in_tc, in_fn
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            # ⛔ THE TEST ITSELF IS RUNTIME CODE (#10143 review P3). `continue`
            # here dropped `node.test` from the walk entirely, and `_scan_dynamic`
            # walks this same generator — so an `if TYPE_CHECKING or
            # importlib.import_module('tortoise.lazy'):` guard hid a real
            # dynamic import with NO edge and NO `UNKNOWN`, in a function whose
            # contract is "anything the analysis CANNOT resolve is reported as
            # `UNKNOWN` … and never dropped". The test is evaluated in the
            # ENCLOSING context (module level at import time, function-local
            # inside a body), never type-only, so it inherits `in_tc`/`in_fn`.
            stack.append((node.test, in_tc, in_fn))
            for child in node.body:
                stack.append((child, True, in_fn))
            for child in node.orelse:
                stack.append((child, in_tc, in_fn))
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # ⛔ ONLY THE BODY IS FUNCTION-LOCAL (#10143 review P2). Decorators,
            # default values, annotations and the return annotation are evaluated
            # where the `def` EXECUTES — module import time — so tagging them
            # `in_function=True` made `--import-time-only` drop real import-time
            # edges, which skews the very number that flag exists to demonstrate.
            body = node.body if isinstance(node.body, list) else [node.body]
            body_ids = {id(b) for b in body}
            for child in ast.iter_child_nodes(node):
                stack.append(
                    (child, in_tc, True if id(child) in body_ids else in_fn))
            continue
        for child in ast.iter_child_nodes(node):
            stack.append((child, in_tc, in_fn))


def _imports_with_context(tree: ast.AST):
    """Yield `(import_node, in_type_checking, in_function)` for every import node."""
    for node, in_tc, in_fn in _nodes_with_context(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node, in_tc, in_fn


class Scanner:
    """Scans one file into a :class:`FileScan`."""

    def __init__(self, index: ModuleIndex) -> None:
        self.index = index

    def scan(self, rel: str, source: str | None = None) -> FileScan:
        module = self.index.by_file.get(rel) or module_name(rel)
        scan = FileScan(rel=rel, module=module)
        is_package_init = rel.endswith("/__init__.py") or rel == "__init__.py"
        pkg_parts = module.split(".") if is_package_init else module.split(".")[:-1]

        if source is None:
            try:
                source = (REPO / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                scan.unknown.append(Unknown(UNKNOWN_SYNTAX, 0, f"unreadable: {exc}"))
                return scan
        try:
            tree = ast.parse(source, filename=rel)
        except (SyntaxError, ValueError) as exc:
            scan.unknown.append(
                Unknown(UNKNOWN_SYNTAX, getattr(exc, "lineno", 0) or 0, str(exc))
            )
            return scan

        # local binding name -> the DOTTED module it is bound to. The dotted
        # target is what a `getattr(<binding>, "<literal>")` candidate must be
        # built from: `import tortoise.sdk as s` binds `s` to `tortoise.sdk`,
        # and resolving from the LOCAL name built `s.hosted_api`, which can
        # never be a module — a real submodule then vanished with no edge and no
        # `UNKNOWN` (#10143 review P2).
        module_bindings: dict[str, str] = {}
        # Local names that ARE the stdlib `importlib` module, and local names that
        # ARE one of its dynamic-import functions (`from importlib import
        # import_module as im`). `import importlib as _importlib` binds the SAME
        # module under a different local name, so an aliased call is exactly as
        # dynamic as `importlib.import_module(...)` — resolving the family by
        # ROOT NAME alone dropped it with no edge and no `UNKNOWN` (#10143 P2).
        # Populated ONLY from a real import of the stdlib
        # `importlib`, so a repo module that happens to define `import_module`
        # still is not misread as the stdlib one.
        importlib_names: set[str] = {"importlib"}
        importlib_attrs: dict[str, str] = {}
        for node, in_tc, in_fn in _imports_with_context(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._add_import(scan, alias.name, node.lineno, in_tc, in_fn)
                    if alias.name == "importlib":
                        importlib_names.add(alias.asname or "importlib")
                    bound = alias.asname or alias.name.split(".")[0]
                    if self.index.is_in_repo_root(alias.name):
                        # `import tortoise.x` binds the ROOT package; an alias
                        # binds the whole dotted path.
                        module_bindings[bound] = (
                            alias.name if alias.asname
                            else alias.name.split(".")[0]
                        )
            else:
                base = _resolve_relative(node, pkg_parts)
                if base == "__future__":
                    # Not a dependency: no `tortoise/**` target can match it, and
                    # `annotations` is a compiler flag, not an import. NAMED here
                    # (and counted in the JSON) so the exclusion is visible
                    # rather than an accident of resolution.
                    scan.future_imports.extend(a.name for a in node.names)
                    continue
                if base:
                    self._add_import(scan, base, node.lineno, in_tc, in_fn)
                if base == "importlib":
                    for alias in node.names:
                        if alias.name in _DYNAMIC_IMPORT_ATTRS:
                            importlib_attrs[alias.asname or alias.name] = alias.name
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    sub = f"{base}.{alias.name}" if base else alias.name
                    if self.index.exists(sub):
                        self._add_import(scan, sub, node.lineno, in_tc, in_fn,
                                         kind=FROM_SUBMODULE)
                        if self.index.is_in_repo_root(sub):
                            module_bindings[alias.asname or alias.name] = sub

        self._scan_dynamic(tree, scan, module_bindings, pkg_parts,
                           importlib_names, importlib_attrs)
        # Dedupe edges: a module imported at module level AND inside a function
        # is one dependency; keep the strongest context (module-level, non-TC).
        scan.deps = _dedupe_deps(scan.deps)
        return scan

    def _add_import(
        self, scan: FileScan, dotted: str, line: int, in_tc: bool, in_fn: bool,
        kind: str = DIRECT,
    ) -> None:
        if not dotted:
            return
        dotted = dotted.strip(".")
        if not dotted:
            return
        if not self.index.is_in_repo_root(dotted):
            return  # stdlib / third party — out of the analysed tree by design
        for ancestor in self.index.ancestors(dotted):
            init = self.index.resolve(ancestor)
            if init:
                scan.deps.append(Dep(ancestor, PACKAGE_INIT, line, in_tc, in_fn))
        if self.index.exists(dotted):
            scan.deps.append(Dep(dotted, kind, line, in_tc, in_fn))
        else:
            scan.unknown.append(Unknown(
                UNKNOWN_UNRESOLVED, line,
                f"'{dotted}' is rooted in an in-repo package but resolves to no "
                f"module file (dynamic submodule, or a stale import)",
            ))

    def _scan_dynamic(
        self, tree: ast.AST, scan: FileScan, module_bindings: dict[str, str],
        pkg_parts: list[str],
        importlib_names: set[str] | None = None,
        importlib_attrs: dict[str, str] | None = None,
    ) -> None:
        """`importlib`-shaped and `getattr`-shaped dynamic access.

        PRECISION IS THE POINT. A constant argument is RESOLVED — it is still
        static information — and only a COMPUTED one makes the file UNKNOWN.
        That is the difference between "this file imports `tortoise.x` in an odd
        way" (a resolvable edge) and "this file can reach any module and the
        closure cannot know which" (a real blind spot).

        Taint also has to mean something: "this closure may be MISSING a
        `tortoise/**` module". So `getattr(os, 'environ')` does not taint (an
        attribute of a third-party module cannot be a `tortoise` module), and a
        bare `importlib.import_module` ATTRIBUTE that is never called does not
        either — only the call does. Over-tainting is not conservatism here: it
        collapses the signal, because one unknown in a densely-connected graph
        marks the whole component and the report stops distinguishing anything.
        """
        for node, in_tc, in_fn in _nodes_with_context(tree):
            if not isinstance(node, ast.Call):
                continue
            kind, name = _dynamic_callable(node.func, importlib_names, importlib_attrs)
            if kind == "import":
                self._scan_import_call(node, scan, pkg_parts, name, in_tc, in_fn)
            elif kind == "loader":
                self._scan_loader_call(node, scan, name, in_tc, in_fn)

            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "getattr"):
                continue
            if not node.args:
                continue
            base = node.args[0]
            if not (isinstance(base, ast.Name) and base.id in module_bindings):
                continue
            bound = module_bindings[base.id]
            attr_node = node.args[1] if len(node.args) > 1 else None
            if isinstance(attr_node, ast.Constant) and isinstance(attr_node.value, str):
                # A literal attribute name is fully static: either it names a
                # submodule (a resolvable edge) or it does not (no edge at all).
                # The candidate comes from the binding's DOTTED target, never its
                # local name: after `from tortoise import projection`,
                # `getattr(projection, 'in_memory')` is `tortoise.projection.
                # in_memory` — `projection.in_memory` is not a module at all.
                sub = f"{bound}.{attr_node.value}"
                if self.index.exists(sub):
                    self._add_import(scan, sub, node.lineno, in_tc, in_fn,
                                     kind=DYNAMIC_LITERAL)
                continue
            scan.unknown.append(Unknown(
                UNKNOWN_DYNAMIC_ATTR, node.lineno,
                f"getattr({base.id}, <computed>) — a computed attribute on an "
                f"in-repo module reaches submodules the AST cannot see",
            ))

    def _scan_import_call(
        self, node: ast.Call, scan: FileScan, pkg_parts: list[str], name: str,
        in_tc: bool = False, in_fn: bool = False,
    ) -> None:
        if not node.args:
            scan.unknown.append(Unknown(
                UNKNOWN_DYNAMIC_CALL, node.lineno, f"{name}() called with no argument",
            ))
            return
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            target = arg.value
            if target.startswith("."):
                target = _resolve_relative_name(target, pkg_parts)
            self._add_import(scan, target, node.lineno, in_tc, in_fn,
                             kind=DYNAMIC_LITERAL)
            return
        scan.unknown.append(Unknown(
            UNKNOWN_DYNAMIC_CALL, node.lineno,
            f"{name}(...) with a computed module name — the target cannot be "
            f"determined statically",
        ))

    def _scan_loader_call(
        self, node: ast.Call, scan: FileScan, name: str,
        in_tc: bool = False, in_fn: bool = False,
    ) -> None:
        """A module loaded from a PATH (`spec_from_file_location`, loader classes).

        `module_from_spec` is skipped: its spec comes from a call already handled
        (or already reported) at the spec site, so counting it again would double
        the taint for one dynamic load.

        `in_fn` defaults to **False**, not True: a module-level call that omitted
        it used to be tagged function-local and then DELETED by
        `--import-time-only` (#10143 P2). Both call sites pass the real context;
        the default must not reintroduce the bug for a future one.
        """
        if name == "module_from_spec":
            return
        path_arg = node.args[1] if len(node.args) > 1 else (node.args[0] if node.args else None)
        if isinstance(path_arg, ast.Constant) and isinstance(path_arg.value, str):
            rel = path_arg.value.split("__pycache__", 1)[0].replace("\\", "/")
            candidates = [rel.lstrip("./")]
            marker = "/tortoise/"
            if marker in rel:
                candidates.append("tortoise/" + rel.split(marker, 1)[1])
            for cand in candidates:
                if cand in self.index.by_file:
                    self._add_import(scan, self.index.by_file[cand], node.lineno,
                                     in_tc, in_fn, kind=DYNAMIC_LITERAL)
                    return
            if not rel.endswith(".py"):
                return  # a wheel / tarball / non-python path, not a module load
        scan.unknown.append(Unknown(
            UNKNOWN_LOADER, node.lineno,
            f"{name}(...) loads a module from a computed path — its imports are "
            f"outside the AST graph",
        ))


def _dynamic_callable(
    func: ast.expr,
    importlib_names: set[str] | None = None,
    importlib_attrs: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Classify a call target: `("import"|"loader"|"", name)`.

    `importlib.import_module(...)` is matched through its `importlib` ROOT, so a
    repo module that happens to define `import_module` is not misread as the
    stdlib one — while `from importlib import import_module` (a bare Name) still
    matches, the over-inclusive direction, which is the safe one.

    AN ALIAS IS THE SAME MODULE. `importlib.import_module` is an ATTRIBUTE of the
    module object, so `import importlib as _importlib` (and
    `from importlib import import_module as _im`) binds the very same callable —
    the module-level import statement binds a local NAME, it does not copy, and
    nothing about the call becomes non-static. Matching the root by the literal
    string `"importlib"` therefore under-counted a real alias into silence: no
    edge, no `UNKNOWN`, and the file read as a complete empty closure (#10143
    P2). `tortoise/projection.py:16,23` is that shape in the tree (`import
    importlib as _importlib` → `_importlib.import_module('tortoise.projection')`).
    The alias sets come from the caller's OWN imports of the stdlib `importlib`,
    so precision is untouched: a repo module named `import_module` is still not
    the stdlib one, because nothing imported `importlib` into that file.
    """
    names = importlib_names if importlib_names is not None else {"importlib"}
    attrs = importlib_attrs or {}
    if isinstance(func, ast.Name):
        if func.id in _DYNAMIC_IMPORT_ATTRS:
            return ("loader" if func.id in _LOADER_NAMES else "import"), func.id
        target = attrs.get(func.id)
        if target:
            return ("loader" if target in _LOADER_NAMES else "import"), target
        return "", ""
    if not isinstance(func, ast.Attribute):
        return "", ""
    root = func
    while isinstance(root, ast.Attribute):
        root = root.value
    if not (isinstance(root, ast.Name) and root.id in names):
        if func.attr in _LOADER_NAMES:
            return "loader", func.attr
        return "", ""
    if func.attr in _LOADER_NAMES:
        return "loader", func.attr
    if func.attr in _DYNAMIC_IMPORT_ATTRS:
        # The SAME set the bare-Name arm above uses. `importlib.__import__('x')`
        # is an import-family attribute exactly as `import_module` is, and the
        # hand-written three-name tuple that used to sit here dropped it — one
        # more silent hole in a function whose contract is that nothing is
        # dropped. Root is still `importlib` (or a recorded alias), so precision
        # is unchanged.
        return "import", func.attr
    return "", ""


def _resolve_relative(node: ast.ImportFrom, pkg_parts: list[str]) -> str:
    """`from .x import y` inside `tortoise/a/b` -> `tortoise.a.x`."""
    if not node.level:
        return node.module or ""
    drop = node.level - 1
    base = list(pkg_parts)
    if drop:
        base = base[:-drop] if drop <= len(base) else []
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _resolve_relative_name(name: str, pkg_parts: list[str]) -> str:
    """Resolve a leading-dot module string (as `import_module(".x", __name__)`)."""
    dots = len(name) - len(name.lstrip("."))
    rest = name.lstrip(".")
    drop = dots - 1
    base = list(pkg_parts)
    if drop:
        base = base[:-drop] if drop <= len(base) else []
    if rest:
        base.extend(rest.split("."))
    return ".".join(base)


def _dedupe_deps(deps: list[Dep]) -> list[Dep]:
    """One edge per (target, kind, type_checking, in_function) — exact context
    duplicates collapse, and nothing else does.

    The ORIGINAL contract was "one edge per (target, kind), keeping the STRONGEST
    occurrence", and it was WRONG: it made the survivor depend on the reader (see
    the carriage below). A module-level and a function-local occurrence of the
    same target BOTH survive now, and each reader filters its own.
    Ties break on `line` (lowest first) so the reported location is stable.

    #10143 review P1: this compared `type_checking` only, and
    `_imports_with_context` is a LIFO stack (`stack.pop()`), so the FUNCTION-LOCAL
    occurrence is normally yielded BEFORE the module-level one and won by
    accident. Measured on the `tortoise/b`-imported-both-ways shape: the survivor
    was `in_function=True`, module-level-only closure came back empty.
    """
    # ⛔ THE KEY IS (target, kind, type_checking, in_function) — NOT
    # (target, kind).
    #
    # #10143 review P2: collapsing the two CONTEXT axes into one survivor is
    # reader-DEPENDENT, so no single ordering can be right. `type_checking` and
    # `in_function` are each a switch a reader filters on, and the cross-axis
    # pair (a module-level import inside `TYPE_CHECKING` vs a runtime import
    # inside a function) loses the target under ONE reader whichever edge wins:
    # keep the module-level TYPE_CHECKING edge and `--exclude-type-checking`
    # drops it; keep the function-local runtime edge and `--import-time-only`
    # drops it — the exact "the file looks as if it never imported the target"
    # failure this function exists to prevent. Keeping one edge per CONTEXT
    # makes the dedupe reader-independent: each reader filters the edges that
    # are its own and the surviving set is the same either way.
    #
    # Measured at the head this changed: one cross-axis site
    # (`tortoise/ask_lane.py` → `tortoise.assembly`), and two core closures
    # under `--import-time-only` were losing 3 modules each before the fix.
    best: dict[tuple[str, str, bool, bool], Dep] = {}
    for dep in deps:
        key = (dep.target, dep.kind, dep.type_checking, dep.in_function)
        cur = best.get(key)
        if cur is None or dep.line < cur.line:
            best[key] = dep
    return sorted(best.values(), key=lambda d: (d.kind, d.target, d.line))


# ---------------------------------------------------------------------------
# Closure
# ---------------------------------------------------------------------------
def merged_pr_numbers(repo: str, window: int) -> list[int]:
    """The newest `window` MERGED PRs, ranked by MERGE time, from the REST API.

    ⛔ NOT `gh pr list --state merged`: measured on this repo it returned 24 PRs
    while #10102, #10103, #10104 and #10106 — all `state=closed, merged=true` —
    were absent from it, so the window would silently skip real merges.
    `/pulls?state=closed` with a `merged_at` filter is authoritative.

    ⛔ AND NOT `sort=created` (#10143 review at `a746139ba`, finding 3). `/pulls`
    has NO merge-time ordering — the documented values are `created` (the
    DEFAULT), `updated`, `popularity` and `long-running` — and `sort=merged` is
    not one of them: measured 2026-10-29 it returns the `created` order verbatim,
    so the endpoint silently ignores an unknown value. Ranking by CREATION time
    therefore dropped merges that happened AFTER entries the window contained
    (measured: 11 of them, e.g. #10108 created 2026-10-26 and merged 2026-10-28,
    a full day after several PRs inside the window) — the under-count direction
    this harness exists to prevent, on the documented `--window 40` reproduce
    path.

    A merge IS an update, so `updated_at >= merged_at` for every merged PR: walk
    `sort=updated` descending and rank the merged set by `merged_at`. Paging stops
    once the newest `window` merges are known AND the page's oldest `updated_at`
    is older than the oldest `merged_at` in that set — every later entry then has
    `merged_at <= updated_at < cutoff` and cannot displace it. A page without
    `updated_at` never stops the walk early (that would be the same silent
    shortening by another name).
    """
    collected: list[tuple[str, int]] = []          # (merged_at, number)

    def _ranked() -> list[tuple[str, int]]:
        # Tie-break on the number so a same-second merge is DETERMINISTIC.
        return sorted(collected, key=lambda t: (t[0], t[1]), reverse=True)

    page = 1
    while page <= 10:
        batch = _gh_json([
            "api",
            f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc"
            f"&per_page=100&page={page}",
        ])
        if not isinstance(batch, list) or not batch:
            break
        for pr in batch:
            if pr.get("merged_at"):
                collected.append((pr["merged_at"], pr["number"]))
        newest = _ranked()
        oldest_updated = batch[-1].get("updated_at") or ""
        if (len(newest) >= window and oldest_updated
                and oldest_updated < newest[window - 1][0]):
            break
        page += 1
    return [number for _, number in _ranked()[:window]]


@dataclass
class Closure:
    """Transitive `tortoise/**` dependency set of one file."""

    rel: str
    modules: tuple[str, ...]          # repo-relative `tortoise/**` paths
    tainted: bool
    reasons: tuple[dict, ...]         # the unknown records that tainted it

    def as_dict(self) -> dict:
        d: dict = {"modules": list(self.modules), "tainted": self.tainted,
                   "size": len(self.modules)}
        if self.reasons:
            d["reasons"] = list(self.reasons)
        return d


def compute_closures(
    scans: dict[str, FileScan], index: ModuleIndex, roots: list[str],
    *, follow_type_checking: bool = True, follow_function_local: bool = True,
) -> dict[str, Closure]:
    """Transitive closure for each of `roots` (repo-relative paths).

    `follow_function_local=False` reproduces the NAIVE reader (module-level
    imports only). It exists to make the trap in the report measurable with the
    committed tool: on this tree it yields a median core closure of 19 modules
    instead of 187, which would look like a 90 % reduction and would be a false
    green (see `docs/ci/core-import-closure.md` §1.1).
    """
    out: dict[str, Closure] = {}
    # Traverse by MODULE, and resolve a module back to its file through the scans
    # we already hold — not through `index.resolve()`. A closure root that is
    # absent from the index would otherwise yield an EMPTY closure, which reads
    # as "this test imports nothing" (the under-selecting direction this tool
    # exists to prevent). Found by `test_closure_is_transitive_and_reports_only_
    # the_package`, whose synthetic index deliberately omits the test files.
    module_to_rel = {scan.module: rel for rel, scan in scans.items()
                     if not rel.endswith("/__init__.py")}
    # Same precedence rule as the index: a package owns its dotted name.
    for rel, scan in scans.items():
        if rel.endswith("/__init__.py"):
            module_to_rel[scan.module] = rel
    for rel in roots:
        scan = scans.get(rel)
        if scan is None:
            scan = Scanner(index).scan(rel)
            scans[rel] = scan
            module_to_rel[scan.module] = rel
        visited: set[str] = set()
        tainted_by: list[dict] = []
        queue = [scan.module]
        seen_modules: set[str] = {scan.module}
        if scan.tainted:
            tainted_by.extend({**u.as_dict(), "module": rel} for u in scan.unknown)
        while queue:
            current = queue.pop()
            dep_rel = module_to_rel.get(current)
            if dep_rel is None:
                resolved = index.resolve(current)
                if resolved and resolved not in scans:
                    scans[resolved] = Scanner(index).scan(resolved)
                dep_rel = resolved
            dep_scan = scans.get(dep_rel) if dep_rel else None
            if dep_scan is None:
                continue
            for dep in dep_scan.deps:
                if dep.type_checking and not follow_type_checking:
                    continue
                if dep.in_function and not follow_function_local:
                    continue
                if dep.target not in seen_modules:
                    seen_modules.add(dep.target)
                    queue.append(dep.target)
                path = index.resolve(dep.target)
                if path:
                    visited.add(path)
            if dep_scan.tainted:
                tainted_by.extend(
                    {**u.as_dict(), "module": dep_scan.rel} for u in dep_scan.unknown
                )
        modules = sorted(p for p in visited if p.startswith(REPORTED_ROOT + "/"))
        out[rel] = Closure(
            rel=rel, modules=tuple(modules), tainted=bool(tainted_by),
            reasons=tuple(tainted_by),
        )
    return out


def scan_all(index: ModuleIndex, files: list[str]) -> dict[str, FileScan]:
    scanner = Scanner(index)
    return {rel: scanner.scan(rel) for rel in files}


# ---------------------------------------------------------------------------
# Surfaces / manifest helpers
# ---------------------------------------------------------------------------
def _selection_module():
    """Import `tools.ci_selection` for the manifest + `select()` (single source)."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from tools import ci_selection

    return ci_selection


def core_test_files(manifest: dict, unresolved: list[str] | None = None) -> list[str]:
    """Repo-relative paths of every `core`-surface member that is a test file.

    Members are registered as basenames (with a few subdir paths); resolve them
    against the tree the same way `classify_test_file` does, so a subdir entry
    keeps working.

    ⛔ A MEMBER THIS CANNOT RESOLVE IS REPORTED, NOT DROPPED (#10143 review P2).
    `out.extend(by_basename.get(member, []))` contributes nothing for a renamed
    or deleted member and used to say nothing, which contradicts this module's
    stated contract ("anything the analysis CANNOT resolve is reported … and
    never dropped") and silently shrinks the denominator every published
    percentage is a ratio over (`docs/ci/core-import-closure.md` §1 carries the
    current set) — with no red anywhere. Pass `unresolved` to collect them; the
    caller surfaces them.
    """
    ci = _selection_module()
    manifest_members = ci._surface_members(manifest["surfaces"].get("core")) if hasattr(
        ci, "_surface_members") else list(manifest["surfaces"].get("core") or [])
    all_tests = sorted(p.relative_to(REPO).as_posix() for p in ci.TESTS_DIR.rglob("*.py"))
    by_basename: dict[str, list[str]] = {}
    for rel in all_tests:
        by_basename.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
    out: list[str] = []
    for member in manifest_members:
        if not member.endswith(".py"):
            continue
        if (REPO / "tests" / member).exists():
            out.append(f"tests/{member}")
        else:
            resolved = by_basename.get(member, [])
            if not resolved and unresolved is not None:
                unresolved.append(member)
            out.extend(resolved)
    return sorted(set(out))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
POSITION_HUB_SHARE = 0.5


def summarise(closures: dict[str, Closure], index: ModuleIndex,
              scans: dict[str, FileScan] | None = None, *,
              follow_type_checking: bool = True,
              follow_function_local: bool = True) -> dict:
    """The numbers a split/no-split verdict is decided on.

    The two `follow_*` flags MUST be threaded through from the caller: the shim's
    own closure (`package_init_closure`) is recomputed here, and recomputing it
    with the DEFAULT reader inside an `--import-time-only` run would publish the
    all-nesting number as the answer to the module-level question (#10143 review
    P2: `--import-time-only --json` reported `package_init_closure: 186` next to
    a median of 21).
    """
    complete = {k: v for k, v in closures.items() if not v.tainted}
    sizes = sorted(len(v.modules) for v in closures.values())
    complete_sizes = sorted(len(v.modules) for v in complete.values())
    sets: dict[tuple[str, ...], list[str]] = {}
    for rel, clo in closures.items():
        sets.setdefault(clo.modules, []).append(rel)

    # How many tests depend on each module — the real granularity of a change.
    dependants: dict[str, int] = {}
    for clo in closures.values():
        for module in clo.modules:
            dependants[module] = dependants.get(module, 0) + 1

    def _hub(rel_path: str) -> dict:
        return {"module": rel_path, "tests": dependants.get(rel_path, 0)}

    init_paths = ["tortoise/__init__.py", "tortoise/__all__.py"]
    total = len(closures) or 1
    # The shim's OWN closure is the mechanism behind the package-wide block: if
    # `tortoise/__init__.py` reaches most of the package, every test that imports
    # any submodule (which imports the package first) inherits that set, and no
    # file-level split can separate them. Naming its direct edges keeps the
    # finding checkable instead of anecdotal.
    init_closure: int | None = None
    init_scan = (scans or {}).get("tortoise/__init__.py")
    if init_scan is not None:
        init_clo = compute_closures(
            scans, index, ["tortoise/__init__.py"],
            follow_type_checking=follow_type_checking,
            follow_function_local=follow_function_local,
        )["tortoise/__init__.py"]
        init_closure = len(init_clo.modules)

    return {
        "core_files": len(closures),
        "tainted_files": len(closures) - len(complete),
        "distinct_closure_sets": len(sets),
        "median_closure_size": statistics.median(sizes) if sizes else 0,
        "mean_closure_size": round(statistics.fmean(sizes), 1) if sizes else 0,
        "max_closure_size": max(sizes) if sizes else 0,
        "min_closure_size": min(sizes) if sizes else 0,
        "median_complete_closure_size": (
            statistics.median(complete_sizes) if complete_sizes else 0
        ),
        "tortoise_modules": sum(
            1 for rel in index.by_file if rel.startswith(REPORTED_ROOT + "/")
        ),
        "reaches_package_init": {
            p: {"tests": dependants.get(p, 0),
                "share": round(dependants.get(p, 0) / total, 4)}
            for p in init_paths
        },
        "top_hubs": [_hub(rel) for rel in
                     sorted(dependants, key=lambda r: -dependants[r])[:10]],
        "largest_sets": [
            {"size": len(mods), "tests": sorted(rel)[:5], "count": len(rel)}
            for mods, rel in sorted(sets.items(), key=lambda kv: -len(kv[0]))[:5]
        ],
        # A file-level split can do no better than the busiest module's dependant
        # count: if HALF the core files depend on one module, splitting the
        # surface moves nothing for a change to that module.
        "hub_share": round(dependants.get(init_paths[0], 0) / total, 4),
        "package_init_closure": init_closure,
        "package_init_direct_edges": (
            [{"target": d.target, "in_function": d.in_function}
             for d in init_scan.deps] if init_scan is not None else []
        ),
    }


def verdict_from_summary(summary: dict, closures: dict[str, Closure],
                         index: ModuleIndex) -> dict:
    """Decide whether the DATA justifies a file-level split — mechanically.

    The decision rule, stated so the report cannot be read as taste:

    * A split is worth building only if a typical module change selects far fewer
      tests than the whole surface, AND no single module dominates.
    * The strongest single signal against a split is a module that MORE THAN HALF
      of the core files depend on: every change to it keeps the surface-wide
      selection, so the split buys nothing for the busiest lever.
    """
    total = summary["core_files"] or 1
    hub = summary["top_hubs"][0] if summary["top_hubs"] else {"module": "", "tests": 0}
    hub_share = hub["tests"] / total
    median_share = summary["median_closure_size"] / max(1, summary["tortoise_modules"])
    split_justified = hub_share < POSITION_HUB_SHARE and median_share < POSITION_HUB_SHARE
    return {
        "split_justified": split_justified,
        "hub_module": hub["module"],
        "hub_tests": hub["tests"],
        "hub_share": round(hub_share, 4),
        "median_closure_share_of_package": round(median_share, 4),
        "rule": (
            "no split while one module is in more than half of the closures "
            f"(observed hub share {hub_share:.0%} of {total} core files)"
        ),
        "distinct_sets": summary["distinct_closure_sets"],
        "median_closure_size": summary["median_closure_size"],
    }


# ---------------------------------------------------------------------------
# Validation harness
# ---------------------------------------------------------------------------
def _run_gh(args: list[str], attempts: int = 4) -> bytes:
    """Run `gh` with retries — the API drops connections intermittently.

    A `gh` call that fails on a transient `error connecting to api.github.com`
    would otherwise abort a 12-PR window after 11 successful PRs, discarding the
    whole measurement. Three of these were observed in one window; each is
    retried serially with an increasing pause rather than treated as a result.
    """
    last: str = ""
    for attempt in range(attempts):
        proc = subprocess.run(["gh", *args], capture_output=True, timeout=300)
        if proc.returncode == 0:
            return proc.stdout
        last = proc.stderr.decode(errors="replace").strip()[:400]
        transient = any(mark in last for mark in (
            "error connecting to api.github.com",
            "error connecting to productionresultssa",
            "blob.core.windows.net",
            "HTTP 502", "HTTP 503", "timeout",
        ))
        if not transient or attempt == attempts - 1:
            break
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"gh {' '.join(args)} failed: {last}")


def _gh_json(args: list[str]) -> object:
    return json.loads(_run_gh(args))


def _check_runs(repo: str, sha: str) -> list[dict]:
    out = _run_gh([
        "api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100&filter=all",
        "--paginate", "--jq", ".check_runs[]",
    ])
    # `--paginate --jq` emits ONE JSON object per page; with `.check_runs[]` each
    # line is a single object, so a line-oriented parse is exact.
    return [json.loads(line) for line in out.decode().splitlines() if line.strip()]


def _run_id(check_run: dict) -> int | None:
    url = check_run.get("details_url") or ""
    marker = "/actions/runs/"
    if marker not in url:
        return None
    tail = url.split(marker, 1)[1]
    head = tail.split("/", 1)[0]
    # A non-numeric head is UNATTRIBUTABLE, not a crash: the caller records the
    # leg as unread (#10143 review P1). `int(...)` used to raise here, which
    # would abort the whole window on one malformed url.
    return int(head) if head.isdigit() else None


def _slug(repo: str) -> str:
    """`owner/name` → a filename-safe component for the caches (#10143 review P2).

    The caches used to be keyed on the head sha / PR number ALONE while `--repo`
    is a first-class flag. PR numbers collide across repos and a commit sha is
    shared by a fork, so an upstream run's truth was reusable for a fork run —
    pairing a diff from one repo with the ground truth of another, which can only
    HIDE a miss, records no error, and was then cached.
    """
    return repo.replace("/", "__")


def _artifacts(repo: str, run_id: int) -> list[dict]:
    """Every artifact of one workflow run, PAGINATED.

    `?per_page=100` with no `--paginate` (and `total_count` ignored) truncated the
    listing at 100, so a red leg's artifact beyond page 1 looked ABSENT — which
    fired the sibling-substitution fallback and could substitute a clean sibling's
    junit for the red leg's own (#10143 review P2). Line-oriented parse because
    `--paginate --jq '.artifacts[]'` emits one object per line, per page.
    """
    out = _run_gh([
        "api", f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100",
        "--paginate", "--jq", ".artifacts[]",
    ])
    return [json.loads(line) for line in out.decode().splitlines() if line.strip()]


# ⛔ THE ARTIFACT NAME IS NOT DERIVABLE FROM THE CHECK-RUN NAME (#10143 review
# P2, round at `960937f5c`).
#
# `upload-artifact`'s `name` is an ARBITRARY user-supplied label — the action's
# own README documents it as "Name of the artifact to upload. Optional. Default
# is 'artifact'" — with no relationship to `github.job` or the job's display
# name. A `pytest-log-<check-run>` rule therefore gets three legs in
# `python-ci.yml` WRONG, and each of the three failed in BOTH directions:
#
#   * a FALSE `artifact_errors` entry for a leg that DID upload — which makes the
#     window PARTIAL forever and `validate` exit 2 on a readable window;
#   * that leg's junit never opened, because a sibling red leg makes `selected`
#     non-empty and disables the wide fallback — the under-count direction this
#     harness exists to prevent.
#
# So a leg has a CANDIDATE SET (most specific first) and the caller takes the
# FIRST candidate present in the run's listing.
ARTIFACT_NAME_OVERRIDES: dict[str, list[str]] = {
    # `test-record-postgres` (`python-ci.yml:3733`) has NO `name:`, so
    # its check run is the job id; `:3856` uploads the LITERAL
    # `pytest-log-record-postgres` — the `test-` prefix is not in the artifact.
    "test-record-postgres": ["pytest-log-record-postgres"],
    # Same shape: job `:3167` (no `name:`) uploads the literal at `:3327`.
    "test-d14-hosted-api": ["pytest-log-d14-hosted-api"],
    # The BARE matrix name is shard 0, whose `name:` suffix is `''` while its
    # ARTIFACT label is `a` (`tools/ci_selection.py:3687-3689`; the upload at
    # `python-ci.yml:3160` uses `matrix.label`). The check run therefore reads
    # `test-carve-out` while the artifact is `pytest-log-test-carve-out-a` — the
    # `-a` is not a guess, it is pinned against the selector itself by
    # `test_the_bare_carve_out_artifact_label_matches_the_selector`.
    "test-carve-out": ["pytest-log-test-carve-out-a"],
}


def _without_test_prefix(job_name: str) -> str:
    """`test-<x>` → `<x>`; the two literal uploads drop exactly that prefix."""
    return job_name[len("test-"):] if job_name.startswith("test-") else job_name


def artifact_names_for_job(job_name: str) -> list[str]:
    """CANDIDATE `pytest-log-*` artifact names for a check-run name, best first.

    Shim: `test (f)` uploads `pytest-log-test-f`; a non-matrix job uploads
    `pytest-log-<job>` — TRUE for every leg except the three in
    `ARTIFACT_NAME_OVERRIDES` and the `test-`-stripped literal uploads, which is
    why this returns candidates rather than one name. The caller falls back to
    "all pytest-log artifacts in the run" when NONE is present, so a future
    rename degrades to a wider (never narrower) read — but it records the
    substitution as PARTIAL first (`pr_ground_truth`), because a SIBLING leg's
    junit is not this leg's truth.
    """
    candidates: list[str] = []

    def add(name: str) -> None:
        if name and name not in candidates:
            candidates.append(name)

    if "(" in job_name and job_name.endswith(")"):
        base, _, label = job_name.rpartition(" (")
        label = label.rstrip(")")
        add(f"pytest-log-{base}-{label}")
        add(f"pytest-log-{_without_test_prefix(base)}-{label}")
        return candidates
    for override in ARTIFACT_NAME_OVERRIDES.get(job_name, []):
        add(override)
    add(f"pytest-log-{job_name}")
    add(f"pytest-log-{_without_test_prefix(job_name)}")
    return candidates


# ⛔ THE LEGS THE SELECTOR DOES NOT MODEL, AND WHOSE JUNIT THIS HARNESS STILL
# READS (#10143 review P2).
#
# `cmd_validate` builds "today's selection" from `select()`. The workflow also
# runs legs that no diff gate produces, and `pr_ground_truth` reads THEIR junit
# as ground truth too, because a red run's listing carries every leg's
# `pytest-log-*` artifact. A failure from such a leg therefore used to land in
# `not_in_current` — the one branch that ABSOLVES the derivation — so it was never
# counted as a miss and never made the window PARTIAL. Measured reachability:
# `tests/test_9555_narrative_carrier.py` is on `api` + `core` but not `tier1`, and
# `test-record-postgres` runs it on every PR, so a docs-only or `tools/`-only diff
# took the tier-1 early return and the file fell outside `current_rel`.
#
# `derived` is `((current - core) | derived_core) | <this set>`, so a file listed
# here stays selected by BOTH sides: modelling the leg is what keeps the comparison
# honest, and NOT modelling it would under-count misses one way or over-cover the
# other. The second union is load-bearing and was missing until the review at
# `a746139ba` (F2): all five files are `core` members, so without it the `- core`
# subtraction re-opened the gap this list exists to close.
#
# Source — `.github/workflows/python-ci.yml` (line numbers as of the `origin/main`
# merge in #10143; grep the job name, the numbers move):
#
#   test-track-b         :2568 job, `FILES="tests/test_ingest_safety.py"` at :2627,
#                             run with `-m track_b`
#   test-record-postgres :3733 job, the four literals at :3819-3822, run
#                             unconditionally
#
# ⚠️ `test-d14-hosted-api` (:3167) is `pytest tests/ -m embedded_only` — a MARKER
# over the whole tree, so its set is not a hand-list, and guessing WIDER is the
# dangerous direction here (everything in `current` is also in `derived`, so
# over-covering a leg would HIDE misses). It is deliberately NOT modelled: a
# failure it contributes lands in `not_in_current`, which now makes the window
# PARTIAL and is named in the console — unread, never absolved.
ALWAYS_ON_LEG_FILES: tuple[str, ...] = (
    "tests/test_ingest_safety.py",                          # test-track-b
    "tests/test_9078_record_written_by_production_path.py",  # test-record-postgres
    "tests/test_9555_narrative_carrier.py",
    "tests/test_9664_capstone_connector_to_record.py",
    "tests/test_9567_record_capstone.py",
)


def _path_from_classname(classname: str) -> str:
    """The module path a junit `classname` was mangled from, or `""`.

    `mangle_test_address()` builds `classname` as the FILE PATH dotted, then the
    class name and/or the test name — so the module is a PREFIX and the number of
    trailing components to drop is not derivable from the string alone. Resolve
    by trying every prefix against the checkout, longest first:

        tests.test_a              -> tests/test_a.py
        tests.test_a.TestX        -> tests/test_a.py
        tests.eval.harness.test_x -> tests/eval/harness/test_x.py

    Longest-first matters: `tests/eval/harness.py` (a package `__init__`) must not
    shadow `tests/eval/harness/test_x.py`, the file that actually failed.

    The previous fallback took `classname.rpartition('.')` and produced **`tests.py`**
    for every module-level test — a path that intersects nothing, so `misses_for()`
    scored the failure as `not_in_current`, the one branch that ABSOLVES the
    derivation (#10143 review P2). `file` exists only under `junit_family=xunit1`;
    `xunit2` is pytest's ini default, so the fallback goes live on any leg that
    omits the repo's `-o junit_family=xunit1` override.
    """
    parts = classname.split(".")
    for n in range(len(parts), 0, -1):
        candidate = "/".join(parts[:n]) + ".py"
        if (REPO / candidate).is_file():
            return candidate
    return ""


def parse_junit_failures(xml_bytes: bytes) -> dict:
    """Failing test FILES from a `junit.xml` (xunit1 `file` attr, classname fallback).

    `unresolved` lists failing tests whose file could be attributed by NEITHER
    key. It is not cosmetic: such a failure would otherwise be dropped from the
    ground truth (under-counting misses) or attributed to a bogus path that
    intersects nothing (which `misses_for()` reads as `not_in_current`, i.e. as
    evidence the derivation is SAFE). The caller turns it into an
    `artifact_errors` entry so the window reads PARTIAL.
    """
    import xml.etree.ElementTree as ET

    files: set[str] = set()
    names: list[str] = []
    unresolved: list[str] = []
    executed = 0
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return {"files": [], "names": [], "executed": 0, "error": f"unparseable junit: {exc}"}
    for case in root.iter("testcase"):
        executed += 1
        bad = [c for c in case if c.tag in ("failure", "error")]
        if not bad:
            continue
        path = case.get("file") or ""
        if not path:
            path = _path_from_classname(case.get("classname") or "")
        if path:
            path = path.replace("\\", "/")
            if path.startswith("./"):
                path = path[2:]
            files.add(path)
        label = f"{case.get('classname', '')}::{case.get('name', '')}"
        if not path:
            unresolved.append(label)
        names.append(label)
    return {"files": sorted(files), "names": names, "executed": executed,
            "unresolved": unresolved}


def _download_artifact(repo: str, artifact_id: int, cache: Path) -> Path:
    target = cache / f"artifact-{artifact_id}.zip"
    if target.exists() and target.stat().st_size > 0:
        return target
    data = _run_gh(["api", f"repos/{repo}/actions/artifacts/{artifact_id}/zip"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _is_junit_member(name: str) -> bool:
    """`junit.xml`, `junit-d14.xml`, `sub/junit.xml` — but not `myjunit.xml`.

    ⛔ `--junitxml` TAKES A CALLER-CHOSEN PATH (#10143 review P2). Matching only
    a member that `endswith("junit.xml")` made the d14 leg unreadable BY
    CONSTRUCTION: `python-ci.yml:3266` runs it with `--junitxml=/tmp/junit-d14.xml`
    and uploads that file, so that leg's ground truth was never parsed — it read
    as "no junit in the artifact" (a permanent false PARTIAL) and its failures
    reached neither the residual arm nor the miss count, which is the opposite of
    what `docs/ci/core-import-closure.md` §3.4 claimed.
    """
    base = name.rsplit("/", 1)[-1].lower()
    return base.startswith("junit") and base.endswith(".xml")


def _junit_member(names: list[str]) -> str | None:
    """The junit payload among a zip's members: exact `junit.xml`, else the first.

    Deterministic (`sorted`) because an artifact can legitimately carry more than
    one junit-shaped member, and a member chosen by zip order would make the
    ground truth depend on upload order.
    """
    candidates = [n for n in names if _is_junit_member(n)]
    if not candidates:
        return None
    exact = [n for n in candidates if n.rsplit("/", 1)[-1].lower() == "junit.xml"]
    return sorted(exact or candidates)[0]


def _junit_from_zip(zip_path: Path) -> bytes | None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            member = _junit_member(zf.namelist())
            if member is None:
                return None
            return zf.read(member)
    except (zipfile.BadZipFile, OSError):
        return None


def pr_ground_truth(repo: str, sha: str, cache: Path) -> dict:
    """Test-level failures on one PR head, straight from the run's junit artifacts.

    Returns `{"failed_files": [...], "failed_tests": [...], "jobs": [...],
    "job_failed_without_test_failure": [...], "artifacts": N,
    "runs_seen": N, "test_legs_seen": N, "artifact_errors": [...]}`. Cached per
    head sha, so a second run of a merged window is offline and instant — the
    measurement has to be reproducible. (The two reads that are deliberately not
    cached — the window listing and an OPEN PR's head — are named in `pr_meta`;
    #10143 review P3.)

    `runs_seen` / `test_legs_seen` exist because "I read nothing" and "nothing
    failed" are otherwise byte-identical (#10143 review P2): a head sha whose
    runs are gone (completed runs are deletable, artifacts expire), or whose
    only check runs are non-`test*`, produced `jobs: []`, `artifacts: 0`,
    `artifact_errors: []` — indistinguishable from a green PR, cached as such,
    and exempt from the window's PARTIAL verdict. Callers must treat a zero as
    "never assessed", never as "clean".
    """
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / f"truth-v{GROUND_TRUTH_VERSION}-{_slug(repo)}-{sha}.json"
    if cached.exists():
        try:
            return json.loads(cached.read_text())
        except (OSError, ValueError):
            pass
    failed_jobs: list[str] = []
    # (run id, job name) — a red leg is checked against ITS OWN run's artifact
    # listing. `failed_jobs` was GLOBAL across runs while the listing was PER RUN,
    # so a red leg in run A was re-checked against run B's artifacts and produced
    # a spurious "uploaded no pytest-log-…" entry even when both legs were
    # perfectly readable — an honest multi-workflow window then read PARTIAL
    # forever. Measured shape: `ci.yml`'s `test-isolation` red beside any red
    # `python-ci` leg (`ci.yml` and `python-ci.yml` are different RUNS).
    failed_legs: list[tuple[int, str]] = []
    run_ids: set[int] = set()
    # Initialised BEFORE the check-run loop: a red leg whose `details_url` carries
    # no run id is recorded up there, and it used to be dropped with no record.
    artifact_errors: list[str] = []
    check_runs = _check_runs(repo, sha)
    test_legs_seen = 0
    for run in check_runs:
        # ⛔ THE RED-CLASS SET, NOT EQUALITY WITH "failure" (#10143 review P2).
        # A `test*` leg concluding `timed_out` was dropped HERE, before
        # `failed_jobs` existed — so neither `artifact_errors` nor the window's
        # unread-PR guard could see it, and a killed suite that may well have
        # failed tests left the window reading "0 misses, exit 0". This is the
        # repo's own recorded polarity: green is exactly {success, neutral,
        # skipped}, cancelled/stale are non-red, and EVERY OTHER CONCLUSION IS
        # RED. A conclusion GitHub has not named yet, and a null one, are red
        # too — an unnamed value must never fall through a gap.
        #
        # `None` is NOT in the skip set: a run GitHub has not concluded yet (a
        # re-trigger at the same sha, queued or in progress) has a null
        # conclusion, and the paragraph above says an unnamed value is red. It
        # was in the skip set here, which put the code back on the fail-open
        # side of its own contract — dropped before `failed_jobs`, so its
        # artifacts were never read, `artifact_errors` stayed empty and a window
        # containing it could still exit 0 (#10143 review P2).
        conclusion = run.get("conclusion")
        name = run.get("name") or ""
        if not name.startswith("test"):
            continue
        test_legs_seen += 1
        if conclusion in {"success", "neutral", "skipped", "cancelled",
                          "stale"}:
            continue
        failed_jobs.append(name)
        rid = _run_id(run)
        if rid:
            run_ids.add(rid)
            failed_legs.append((rid, name))
        else:
            # ⛔ AN UNATTRIBUTABLE RED LEG IS AN UNREAD ONE, NOT AN ABSENT ONE
            # (#10143 review P1). `if rid:` had no `else`: the leg never reached
            # `run_ids`, so its artifacts were never read AND `artifact_errors`
            # stayed empty. Beside a readable sibling red leg, `artifacts > 0`
            # also disabled the unread-PR guard, so the window could exit 0 over a
            # leg whose ground truth was never opened.
            artifact_errors.append(
                f"red leg {name}: no Actions run id in its details_url — its "
                "junit was never read"[:200])

    artifacts_seen = 0
    failed_files: set[str] = set()
    failed_tests: list[str] = []
    no_test_failure: list[str] = []
    for rid in sorted(run_ids):
        # PAGINATED (#10143 review P2) — see `_artifacts`.
        arts = _artifacts(repo, rid)
        jobs_here = [job for r, job in failed_legs if r == rid]
        # ⛔ A SIBLING LEG'S JUNIT IS NOT THIS LEG'S GROUND TRUTH (#10143 review
        # P2). `rid` is the workflow RUN, and `test`/`test-slow`/`test-carve-out`
        # (plus the always-on legs) are matrix jobs inside ONE run, so the
        # listing below carries every leg's `pytest-log-*`. The wide fallback
        # two lines down exists so a RENAME degrades to a wider read — but when
        # it fires because the red leg's OWN artifact is absent (ordinary: the
        # upload step is `if: always()` + `if-no-files-found: ignore`, so a leg
        # that early-exits uploads nothing), it substitutes a sibling's clean
        # junit for the missing one. The sibling parses clean, `pr_found` stays
        # False, the row reads "job red, no test-level failure" — the #10070
        # zero-failure shape — and the truncated truth was then CACHED.
        # Record the substitution per missing job BEFORE the fallback is used:
        # the rename mitigation survives, and the masking state becomes PARTIAL
        # and uncacheable.
        # ⛔ ONE ARTIFACT PER LEG, CHOSEN BY CANDIDATE PRIORITY (#10143 review
        # P2). The candidates are not interchangeable guesses: the first is the
        # leg's own upload wherever the name is derivable, and the rest exist
        # only because `upload-artifact`'s `name` is a free-form label (see
        # `ARTIFACT_NAME_OVERRIDES`). Taking the first HIT per job — instead of
        # a union over every candidate for every job — also stops one leg's
        # candidate from selecting another leg's artifact.
        per_leg: list[dict] = []
        for job in jobs_here:
            candidates = artifact_names_for_job(job)
            hit = next(
                (a for cand in candidates for a in arts
                 if str(a.get("name", "")) == cand), None)
            if hit is None:
                artifact_errors.append(
                    f"run {rid}: job {job} uploaded none of "
                    f"{', '.join(candidates)} — a sibling leg's junit "
                    "must not stand in for it"[:200])
            else:
                per_leg.append(hit)
        selected = per_leg
        if not selected:
            selected = [a for a in arts if str(a.get("name", "")).startswith("pytest-log-")]
        if not selected:
            # ⛔ A RED run that uploaded NO `pytest-log-*` artifact at all is
            # NOT "nothing failed" — it is a leg whose ground truth this
            # harness could not read, and the two round-1 fixes covered only the
            # layers below this one (artifact present but no junit; junit
            # unparseable). Dropping it silently let `validate` exit 0 on a
            # window it never read, which is the false green the PARTIAL exit
            # code exists to prevent.
            artifact_errors.append(f"run {rid}: no pytest-log-* artifact uploaded"[:200])
            continue
        pr_found = False
        executed_total = 0
        for art in selected:
            artifacts_seen += 1
            try:
                zip_path = _download_artifact(repo, art["id"], cache)
            except (RuntimeError, OSError) as exc:
                # A blob-storage failure is NOT evidence that no test failed.
                # Record it and keep going: a partial ground truth can only
                # UNDER-count misses, so the harness must say so out loud rather
                # than report a clean window it could not read.
                artifact_errors.append(f"{art.get('name')}: {exc}"[:200])
                continue
            xml = _junit_from_zip(zip_path)
            if xml is None:
                # #10143 review P2: a red leg may legitimately upload only
                # `pytest.log` (the workflow uploads junit with
                # `if-no-files-found: ignore`). Dropping the artifact silently
                # made "we could not read the ground truth" indistinguishable
                # from "nothing failed", and `artifact_errors` — the only field
                # that records the difference — stayed empty, so the partial
                # truth was then CACHED. Record it here instead.
                artifact_errors.append(
                    f"{art.get('name')}: no junit.xml in artifact"[:200])
                continue
            parsed = parse_junit_failures(xml)
            if parsed.get("error"):
                # Same fail-open one level down: `parse_junit_failures` returns
                # the parse failure in `error` and an EMPTY `files`/`names`, and
                # reading only the latter turned corrupt XML into "no failures".
                artifact_errors.append(
                    f"{art.get('name')}: {parsed['error']}"[:200])
                continue
            if parsed.get("unresolved"):
                # A failing test whose FILE neither key can attribute is a
                # partial ground truth: keep the failures it did attribute, but
                # say so — otherwise the omission reads as "nothing failed
                # here" (and a bogus path would read as "the derivation is
                # safe", the `not_in_current` absolution).
                artifact_errors.append(
                    f"{art.get('name')}: {len(parsed['unresolved'])} failing "
                    f"test(s) with no resolvable file "
                    f"({parsed['unresolved'][0]})"[:200])
            # ⛔ RECORDED PER ARTIFACT, NOT PER RUN (#10143 review P3). `rid`
            # is the workflow RUN and `selected` is one artifact per RED LEG in
            # it (see the sibling-masking block above: `test`/`test-slow`/
            # `test-carve-out` and the always-on legs are matrix jobs inside ONE
            # run). Counting `executed` only as a run-wide total meant a leg
            # whose junit described ZERO testcases was masked by a sibling leg
            # in the same run that described 500 — `executed_total` was non-zero,
            # so no error was written, the truncated truth was CACHED and
            # `cmd_validate` could return 0 or 1 on a window whose first leg's
            # ground truth was never read. That is the sibling-masking defect one
            # granularity down, in its COUNTING form.
            executed = int(parsed.get("executed") or 0)
            if not executed:
                artifact_errors.append(
                    f"{art.get('name')}: junit described 0 testcases — the suite "
                    "did not run, so 'no test-level failure' is not a "
                    "reading"[:200])
            executed_total += executed
            if parsed["files"]:
                pr_found = True
                failed_files.update(parsed["files"])
                failed_tests.extend(parsed["names"])
        # ⛔ A JUNIT WITH ZERO `<testcase>` ELEMENTS IS NOT "NOTHING FAILED"
        # (#10143 review P2). `parse_junit_failures` computed `executed` and this
        # caller never read it: a red leg whose junit described 0 tests (the suite
        # did not run — collection aborted, a marker selected nothing, exit 5)
        # parsed as CLEAN, `artifact_errors` stayed empty, the row read "job red,
        # no test-level failure" (the genuine #10070 zero-failure shape) and the
        # truncated truth was CACHED.
        # The RUN-level summary of the per-artifact entries above: every red leg
        # in this run was unreadable in the same way (a collection abort that
        # stopped the whole matrix), which is a stronger statement than any one
        # leg's entry and must not be silently deduped into it.
        if selected and not executed_total:
            artifact_errors.append(
                f"run {rid}: every selected junit described 0 testcases — the "
                "suite did not run, so 'no test-level failure' is not a "
                "reading"[:200])
        if not pr_found:
            no_test_failure.append(f"run {rid}")

    result = {
        "failed_files": sorted(failed_files),
        "failed_tests": sorted(set(failed_tests)),
        "jobs": sorted(failed_jobs),
        "job_failed_without_test_failure": no_test_failure,
        "artifacts": artifacts_seen,
        "runs_seen": len(check_runs),
        "test_legs_seen": test_legs_seen,
        "artifact_errors": artifact_errors,
    }
    if not artifact_errors and check_runs and test_legs_seen:
        # Do not cache a partial ground truth: a cached truncation would look
        # like a clean run on every later invocation. `check_runs` empty is the
        # same rule one layer up — "I saw no runs at all" is not an answer — and
        # `test_legs_seen == 0` is the third (#10143 review P1): runs exist but no
        # `test*` leg among them, so nothing was assessed. Caching that made a
        # re-trigger at the same sha short-circuit on the emptiness instead of
        # re-reading it.
        cached.write_text(json.dumps(result, indent=1))
    return result


def pr_meta(repo: str, pr: int, cache: Path | None = None) -> dict:
    """One PR's metadata, cached ONLY once the PR is MERGED.

    ⛔ THE PROSE SAYS RE-RUNS ARE OFFLINE, AND THE ROW LOOP BROKE IT (#10143
    review P3). `cmd_validate` fetched `repos/<repo>/pulls/<pr>` for EVERY PR in
    the window before either cache was consulted — one uncached `gh api` call per
    PR, and `gh api` has no local cache — so a warm window still needed network
    and auth, and with `gh` unavailable `_run_gh` raised on the FIRST PR, killing
    the whole window with a traceback after the caller was told the run was
    cached.

    ⛔ CACHING IT BY PR NUMBER ALONE WOULD BE THE WORSE BUG, and it is the same
    one `changed_files` was just fixed for: a PR number names a STREAM of
    different trees (§3.3). So the key names the PR only while the answer cannot
    change — a MERGED PR's head sha is immutable, which is exactly the documented
    `validate --window 40` path and the `--prs <list>` reproduce path §3
    publishes. An OPEN PR is never written to the cache: its head moves on every
    push, and a lane validating its own PR must re-read the head it is validating
    (pinned by `test_an_open_prs_head_is_never_cached`).
    """
    cached = cache / f"meta-{_slug(repo)}-{pr}.json" if cache is not None else None
    if cached is not None:
        # Same contract as `pr_ground_truth`: a cache directory that does not
        # exist yet is created, not an OSError that takes the window down.
        cache.mkdir(parents=True, exist_ok=True)
    if cached is not None and cached.exists():
        try:
            payload = json.loads(cached.read_text())
        except (OSError, ValueError):
            payload = None
        # A cached object with no `merged_at` cannot be trusted to describe a
        # settled head, so it is re-read rather than served (fail OPEN on the
        # FETCH, never on the ANSWER: an open PR is simply re-fetched).
        if isinstance(payload, dict) and payload.get("merged_at"):
            return payload
    meta = _gh_json(["api", f"repos/{repo}/pulls/{pr}"])
    if not isinstance(meta, dict):
        raise RuntimeError(
            f"gh api repos/{repo}/pulls/{pr} answered "
            f"{type(meta).__name__}, not an object — the head sha cannot be read")
    if cached is not None and meta.get("merged_at"):
        cached.write_text(json.dumps(meta, indent=1))
    return meta


def changed_files(repo: str, pr: int, cache: Path | None = None,
                  head_sha: str | None = None) -> list[str]:
    """The PR's changed files, cached under THE HEAD SHA THEY DESCRIBE.

    ⛔ THE KEY MUST NAME THE HEAD (#10143 review P2). `GET
    /repos/{owner}/{repo}/pulls/{pull_number}/files` has NO ref selector — its
    only parameters are `pull_number`, `per_page` and `page`
    (docs.github.com/en/rest/pulls/pulls#list-pull-requests-files) — so the
    response is the diff of the PR's CURRENT head. Keyed by the PR alone, a
    `validate` run at head A left its list in `files-<repo>-<pr>.json`, and a
    later run at head B read it back and paired it with B's ground truth
    (`truth-v<N>-<slug>-<sha>.json`, which IS keyed by sha): the derived set was
    then computed over a diff the truth never saw. That direction can only
    UNDER-count misses, it records no `artifact_errors` entry, so the row reads
    clean and `validate` can exit 0 on it — the same cache-hides-a-miss shape the
    truth cache's sha key exists to prevent (§3.3). Merged windows are latent
    (a merged PR's list is final), but `--prs` accepts any PR id and a lane
    checking its OWN open PR hits it on every push.

    With no `head_sha` there is no identity to key on, so nothing is cached — a
    list is never stored under a key it does not describe. The legacy
    `files-<repo>-<pr>.json` entries are simply never read again.
    """
    cached = cache / f"files-{_slug(repo)}-{head_sha}.json" if (
        cache is not None and head_sha) else None
    if cached is not None and cached.exists():
        try:
            return json.loads(cached.read_text())
        except (OSError, ValueError):
            pass
    out = _run_gh([
        "api", f"repos/{repo}/pulls/{pr}/files?per_page=100", "--paginate",
        "--jq", ".[].filename",
    ])
    files = sorted({line for line in out.decode().splitlines() if line.strip()})
    if cached is not None:
        cached.write_text(json.dumps(files, indent=1))
    return files


MISS_NOT_PYTHON = "non-python-dependency"
MISS_TAINTED = "tainted-closure"
MISS_NO_PATH = "no-closure-intersection"


def derived_core_selection(
    changed: list[str],
    core_members: list[str],
    closures: dict[str, Closure],
    index: ModuleIndex,
    *, keep_empty_closures: bool = True,
) -> dict:
    """Core tests a closure-based mapping would select for `changed`.

    A core test stays selected when
      (a) the file itself changed — today's test-file rule, preserved so a
          rewritten test always runs, or
      (b) its transitive closure contains a changed `tortoise/**` module, or
      (c) `keep_empty_closures` and its closure is EMPTY.

    (c) is not a fudge, it is the one class the closure provably cannot answer:
    120 of the 756 core files import nothing from the package — they assert
    invariants of a CONFIG, WORKFLOW or DOC file, which is exactly why they were
    hand-registered in `SOURCE_PATTERNS`/`CORE_ALSO` in the first place. An
    import closure has no edge to carry that dependency, so a mapping that drops
    them silently loses the guard. Measured consequence of dropping them is in
    the report (`non-python-dependency` misses).
    """
    changed_modules = {
        module_name(c) for c in changed
        if c.startswith(REPORTED_ROOT + "/") and c.endswith(".py")
    }
    changed_set = set(changed)
    selected: list[str] = []
    empty_kept: list[str] = []
    for rel in core_members:
        clo = closures.get(rel)
        if clo is None:
            selected.append(rel)  # unanalysable -> never silently dropped
            continue
        if rel in changed_set:
            selected.append(rel)
            continue
        if not clo.modules and keep_empty_closures:
            selected.append(rel)
            empty_kept.append(rel)
            continue
        if changed_modules & {module_name(m) for m in clo.modules}:
            selected.append(rel)
    return {
        "changed_modules": sorted(changed_modules),
        "selected": sorted(selected),
        "dropped": sorted(set(core_members) - set(selected)),
        "empty_kept": len(empty_kept),
    }


def misses_for(current: set[str], derived: set[str], failed: set[str]) -> dict:
    """The harness's ONLY question: did the derived set run every actual failure?

    `current` is the selection the repository makes today, so a failing test that
    today's selection never runs is NOT counted against the derivation — it is
    reported separately as `not_in_current` (it would be a gap in the existing
    selector, not in this one).
    """
    actual = set(failed) & set(current)
    missed = sorted(actual - set(derived))
    return {
        "misses": missed,
        "not_in_current": sorted(set(failed) - set(current)),
        "covered": sorted(actual - set(missed)),
        "safe": not missed,
    }


def classify_miss(
    rel: str, changed: list[str], closures: dict[str, Closure],
) -> str:
    """Why a missed test failed without an import path from the diff.

    * `tainted-closure` — the derivation could not see all of this file's
      imports, so the miss is a BLINDNESS of the analysis (a real limitation,
      and the only class that a better analysis could fix).
    * `non-python-dependency` — the test imports NOTHING from `tortoise/**`: it
      asserts an invariant of a config, workflow, document, or of a script it
      runs as a subprocess. No import edge can ever exist, so only a hand-written
      registration can carry that dependency.
    * `no-closure-intersection` — the test does import the package, its closure is
      complete, and nothing in it intersects the changed `tortoise/**` modules.
      The failure is therefore not reachable by an import edge from this diff.
      Two sub-causes were observed and are named in the report rather than
      guessed at here: a diff that changes only TEST files (today's surface rule
      is what keeps the rest of the surface running — no import edge couples one
      test to another), and failures on docs/config-only diffs that no import
      path can explain.
    """
    clo = closures.get(rel)
    if clo is None:
        return "unknown-file"
    if clo.tainted:
        return MISS_TAINTED
    if not clo.modules:
        return MISS_NOT_PYTHON
    return MISS_NO_PATH


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _fmt_table(summary: dict) -> str:
    lines = [
        f"core test files analysed          : {summary['core_files']}",
        f"  complete closures               : {summary['core_files'] - summary['tainted_files']}",
        f"  tainted (UNKNOWN, reported)     : {summary['tainted_files']}",
        f"tortoise/** modules in tree       : {summary['tortoise_modules']}",
        f"distinct closure sets             : {summary['distinct_closure_sets']}",
        f"closure size  median/mean/max     : {summary['median_closure_size']} / "
        f"{summary['mean_closure_size']} / {summary['max_closure_size']}",
        "reaches tortoise/__init__.py      : "
        f"{summary['reaches_package_init']['tortoise/__init__.py']['tests']} files "
        f"({summary['reaches_package_init']['tortoise/__init__.py']['share']:.1%})",
        "reaches tortoise/__all__.py       : "
        f"{summary['reaches_package_init']['tortoise/__all__.py']['tests']} files "
        f"({summary['reaches_package_init']['tortoise/__all__.py']['share']:.1%})",
        "",
        "busiest modules (tests that would run on a change to each):",
    ]
    for hub in summary["top_hubs"]:
        share = hub["tests"] / max(1, summary["core_files"])
        lines.append(f"  {hub['module']:<48} {hub['tests']:>5}  ({share:.1%})")
    return "\n".join(lines)


def _load_all(follow_type_checking: bool = True,
              follow_function_local: bool = True) -> tuple[dict, dict, ModuleIndex]:
    ci = _selection_module()
    manifest = ci.load_manifest()
    files = _git_py_files(REPO)
    index = ModuleIndex(files)
    scans = scan_all(index, files)
    unresolved_core: list[str] = []
    core = core_test_files(manifest, unresolved_core)
    # ⛔ A MISSING OR RENAMED `core` SURFACE IS NOT AN EMPTY ONE (#10143 review
    # P1). `manifest["surfaces"].get("core") or []` turns a renamed key (or a
    # failed `_surface_members` fallback) into `core == []`. Then `unresolved_core`
    # stays empty, `core_rel` is empty, `derived = current` (nothing is ever
    # dropped from today's selection), every real failure is `covered`, and
    # `validate` prints "TOTAL MISSES 0" and exits 0 — a clean window certified by
    # an analysis that examined NOTHING. Same hazard as the empty-window bug, one
    # layer up, and it is reachable by a rename nobody would call a failure.
    core_empty = not core
    closures = compute_closures(scans, index, core,
                                follow_type_checking=follow_type_checking,
                                follow_function_local=follow_function_local)
    return manifest, {"scans": scans, "closures": closures, "core": core,
                      "unresolved_core": unresolved_core,
                      "core_empty": core_empty}, index


def _refuse_an_empty_core(data: dict) -> bool:
    """True when the `core` surface resolved to nothing, after saying so (#10143 review P1).

    A derivation over an empty surface selects nothing, covers everything, and
    reports zero misses. It is the strongest possible false green, so every verb
    that publishes a number refuses instead of printing a table of zeros.
    """
    if not data.get("core_empty"):
        return False
    print("::error:: the manifest's `core` surface resolved to ZERO test files — a "
          "derivation over nothing selects nothing and certifies nothing")
    return True


def cmd_closure(args: argparse.Namespace) -> int:
    _manifest, data, index = _load_all(not args.exclude_type_checking,
                                       not getattr(args, "import_time_only", False))
    if _refuse_an_empty_core(data):
        return 2
    if data.get("unresolved_core"):
        # Printed, not merely carried in the JSON: every percentage in the table
        # below is a ratio over this denominator, so a member that resolved to
        # nothing has to be VISIBLE where the numbers are read (#10143 P2).
        print("::error:: core member(s) resolving to NO test file — the "
              "denominator below is short by that many: "
              + ", ".join(data["unresolved_core"][:10]))
    closures = data["closures"]
    summary = summarise(
        closures, index, data["scans"],
        follow_type_checking=not args.exclude_type_checking,
        follow_function_local=not getattr(args, "import_time_only", False),
    )
    verdict = verdict_from_summary(summary, closures, index)

    def _emit_json() -> None:
        """Write `--json` if asked. Called on EVERY exit path.

        #10143 review: `--module X --json out.json` returned 0 from the module
        branch BELOW, so the documented flag silently wrote nothing — measured
        exit 0 with the file never created. A documented flag that no-ops is a
        false record, so the write is now shared by both exits.
        """
        if not args.json:
            return
        payload = {
            "schema": 1,
            "summary": summary,
            "verdict": verdict,
            "core_files": list(closures),
            "unresolved_core": data.get("unresolved_core", []),
            "shadowed_modules": index.shadowed,
            "closures": {rel: clo.as_dict() for rel, clo in sorted(closures.items())},
            "unknown_files": {
                rel: [u.as_dict() for u in scan.unknown]
                for rel, scan in sorted(data["scans"].items()) if scan.unknown
            },
            "future_imports": {
                rel: scan.future_imports
                for rel, scan in sorted(data["scans"].items()) if scan.future_imports
            },
        }
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
        print(f"\njson -> {out} ({out.stat().st_size} bytes)")

    if args.module:
        kept, dropped = [], []
        for rel, clo in closures.items():
            (kept if args.module in clo.modules else dropped).append(rel)
        print(f"# a change to {args.module} would select {len(kept)}/{len(closures)} "
              f"core test files (today: all {len(closures)})")
        for rel in sorted(kept)[:args.limit]:
            print(f"  {rel}")
        if len(kept) > args.limit:
            print(f"  … {len(kept) - args.limit} more")
        _emit_json()
        return 0

    print(_fmt_table(summary))
    print()
    print(f"verdict: split_justified={verdict['split_justified']} — {verdict['rule']}")
    if args.show_tainted:
        for rel, clo in sorted(closures.items()):
            if not clo.tainted:
                continue
            reasons = sorted({r["reason"] for r in clo.reasons})
            print(f"  TAINTED {rel}: {', '.join(reasons)}")

    _emit_json()
    return 0


def _tests_rel(path: str) -> str:
    """Normalise to `tests/`-RELATIVE — `select()`'s own contract.

    `tools/ci_selection.select()` returns manifest members, which are
    `tests/`-relative (`test_config.py`, `eval/harness/test_x.py`). The closure
    keys are repo-relative (`tests/test_config.py`). Comparing the two directly
    is a silent bug that makes every derived selection look LARGER than the real
    one (the subtraction `current - core` matches nothing, so the core set is
    added instead of replaced) — measured as `der 1542 > cur 916` on #10118
    before this normalisation.
    """
    return path[len("tests/"):] if path.startswith("tests/") else path


def cmd_validate(args: argparse.Namespace) -> int:
    ci = _selection_module()
    manifest, data, index = _load_all(not args.exclude_type_checking)
    if _refuse_an_empty_core(data):
        return 2
    if data.get("unresolved_core"):
        # Reported by `closure` and `report` too — and here it matters MOST: the
        # miss counts below are ratios over this denominator, so a member that
        # resolved to no test file must be visible in the verb that publishes
        # them (#10143 review P2).
        print("::error:: core member(s) resolving to NO test file — the "
              "denominator below is short by that many: "
              + ", ".join(data["unresolved_core"][:10]))
    closures = data["closures"]
    core = data["core"]
    core_rel = {_tests_rel(p) for p in core}
    closures_rel = {_tests_rel(k): v for k, v in closures.items()}
    all_tests = sorted(
        p.relative_to(REPO).as_posix() for p in ci.TESTS_DIR.rglob("test_*.py")
    )

    window = 10 if args.window is None else args.window
    prs = list(args.prs) or merged_pr_numbers(args.repo, window)
    if args.prs and args.window is not None:
        prs = prs[:args.window]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    total_naive = 0
    total_safe = 0
    for pr in prs:
        meta = pr_meta(args.repo, pr, CACHE_DIR)
        sha = meta["head"]["sha"]
        # ⛔ THE DIFF IS FETCHED FOR THE HEAD ITS TRUTH IS KEYED ON. Cached by PR
        # alone, an earlier run's list (head A) was scored against this head's
        # ground truth — a mismatch that can only hide a miss (#10143 review P2).
        changed = changed_files(args.repo, pr, CACHE_DIR, head_sha=sha)
        truth = pr_ground_truth(args.repo, sha, CACHE_DIR)
        explain: dict = {}
        selection = ci.select(changed, "pull_request", manifest, explain)
        # ⛔ TODAY'S SELECTION IS EVERY LEG THE WORKFLOW RUNS — and `select()`
        # models three of them, not one. The carve-out leg is SUBTRACTED from
        # `test_files` (a carve-out file cannot run in a URI-set docker leg,
        # ci_selection.py:3055) and reported separately as `carve_out_run`,
        # which the tier-2 PR path runs whenever a matched surface owns a
        # carve-out file. Reading only `test_files`/`slow_selected` therefore
        # scored a carve-out failure as `not_in_current` — i.e. it blamed the
        # EXISTING selector for a test the existing selector DID run, and the
        # derivation was absolved of it. MEASURED on the window as it stood
        # BEFORE this round re-cut it (the 40 PRs ending at #10027): #10134's
        # `test-carve-out (b)`/`(c)` failed 6 files, 5 of them core-registered;
        # as shipped those 6 landed in `not_in_current` and the totals read
        # 9/8 instead of 14/13. On the re-cut window the same defect moved the
        # totals to 10/9 (`docs/ci/core-import-closure.md` §3). `not_in_current`
        # is now PRINTED too — the exclusion was invisible in the console and in
        # the report.
        if selection["test_files"] == "ALL":
            current_rel = {_tests_rel(p) for p in all_tests} | {
                _tests_rel(p) for p in selection.get("slow_files", [])
            }
        else:
            current_rel = {_tests_rel(p) for p in selection["test_files"]} | {
                _tests_rel(p) for p in selection.get("slow_selected", [])
            }
        if selection.get("carve_out_run"):
            current_rel |= {_tests_rel(p) for p in (manifest.get("carve_out") or [])}
        # ⛔ THE WORKFLOW RUNS MORE LEGS THAN `select()` MODELS (#10143 review P2).
        # `test-track-b` and `test-record-postgres` are not diff-gated at all, and
        # their junit is read as ground truth by `pr_ground_truth` — so a failure
        # from them used to land in `not_in_current`, the one branch that absolves
        # the derivation. A leg that cannot be modelled from a hand-list
        # (`test-d14-hosted-api`'s marker selection) is NOT guessed at — see the
        # `not_in_current` PARTIAL gate below.
        #
        # ⛔ AND THEY MUST BE IN `derived` TOO, not only in `current` (#10143
        # review at `a746139ba`, F2). `base_rel = current_rel - core_rel` subtracts
        # EVERY `core` member, and all five files here ARE `core` members, so
        # adding them to `current_rel` alone re-added them to `current` while
        # leaving them out of `base_rel` — they returned to `derived` only through
        # `derived_core_selection`, i.e. only when their closure intersected the
        # diff. On a docs-only or `tools/`-only diff `changed_modules` is empty, so
        # an always-on leg's failure was scored `MISS[…no-closure-intersection]`
        # and drove exit 1 — the comment claimed the opposite and the pin could not
        # see it (its synthetic `core` excluded the file, so the subtraction never
        # happened).
        #
        # The honest reading is the one the comment always stated: these legs run
        # on EVERY PR, so no diff-based mapping can drop them, and a failure there
        # is neither a derivation miss nor a hole in the model. `always_on_rel` is
        # therefore unioned into BOTH sides below.
        always_on_rel = {_tests_rel(p) for p in ALWAYS_ON_LEG_FILES
                         if (REPO / p).is_file()}
        current_rel |= always_on_rel
        # TWO derived mappings, because the difference between them IS the
        # finding: the naive one (drop every core file whose closure misses the
        # diff) and the one that keeps the provably non-Python-dependency files.
        changed_rel = [_tests_rel(c) for c in changed]
        naive_info = derived_core_selection(changed, core, closures, index,
                                            keep_empty_closures=False)
        safe_info = derived_core_selection(changed, core, closures, index,
                                           keep_empty_closures=True)
        # `always_on_rel` is unioned back in: the subtraction above is what removes
        # a `core` member from `derived`, and an unconditional leg is not a member
        # the derivation may remove (see the block that builds `always_on_rel`).
        base_rel = (current_rel - core_rel) | always_on_rel
        naive = base_rel | {_tests_rel(p) for p in naive_info["selected"]}
        safe = base_rel | {_tests_rel(p) for p in safe_info["selected"]}
        failed_rel = {_tests_rel(f) for f in truth["failed_files"]}
        naive_result = misses_for(current_rel, naive, failed_rel)
        safe_result = misses_for(current_rel, safe, failed_rel)
        naive_misses = [
            {"file": rel, "class": classify_miss(rel, changed_rel, closures_rel),
             "variant": "naive"}
            for rel in naive_result["misses"]
        ]
        safe_misses = [
            {"file": rel, "class": classify_miss(rel, changed_rel, closures_rel),
             "variant": "keep-non-python"}
            for rel in safe_result["misses"]
        ]
        total_naive += len(naive_misses)
        total_safe += len(safe_misses)
        rows.append({
            "pr": pr,
            "sha": sha[:10],
            "changed": changed,
            "changed_modules": naive_info["changed_modules"],
            "surfaces": selection["surfaces"],
            "current": len(current_rel),
            "derived_naive": len(naive),
            "derived_keep_non_python": len(safe),
            "derived_core": len(naive_info["selected"]),
            "dropped_core_naive": len(naive_info["dropped"]),
            "dropped_core_keep_non_python": len(safe_info["dropped"]),
            "failed_files": truth["failed_files"],
            "failed_jobs": truth["jobs"],
            "job_failed_without_test_failure": truth["job_failed_without_test_failure"],
            "artifacts": truth["artifacts"],
            "runs_seen": truth.get("runs_seen", 0),
            "test_legs_seen": truth.get("test_legs_seen", 0),
            "artifact_errors": truth["artifact_errors"],
            "misses_naive": naive_misses,
            "misses_keep_non_python": safe_misses,
            "not_in_current": naive_result["not_in_current"],
            "safe_naive": naive_result["safe"],
            "safe_keep_non_python": safe_result["safe"],
        })

    report = {
        "schema": 1,
        "repo": args.repo,
        "window": len(rows),
        "total_misses_naive": total_naive,
        "total_misses_keep_non_python": total_safe,
        "artifact_errors": sum(len(r["artifact_errors"]) for r in rows),
        "unresolved_core": data.get("unresolved_core", []),
        "prs": rows,
    }
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")

    print(f"validation over {len(rows)} merged PR(s) — {args.repo}")
    print(f"{'PR':<8}{'cur':>6}{'derN':>7}{'derS':>7}{'fail':>6}{'missN':>7}{'missS':>7}  detail")
    for row in rows:
        detail = ""
        if row["misses_keep_non_python"]:
            detail = "; ".join(
                f"{m['file']}[{m['class']}]" for m in row["misses_keep_non_python"][:2]
            )
        elif row["misses_naive"]:
            detail = "naive-only: " + "; ".join(
                f"{m['file']}[{m['class']}]" for m in row["misses_naive"][:2]
            )
        elif row["not_in_current"]:
            # A failing test today's selection did NOT run. This is the ONE
            # branch that absolves the derivation, so it must never be silent:
            # a lane the workflow DID run but the model dropped lands here, and
            # that is how a miss becomes invisible (see the carriage at the
            # selection above).
            detail = ("outside today's selection: "
                      + ", ".join(row["not_in_current"][:2]))
        elif not row["failed_files"]:
            detail = ("job red, no test-level failure: "
                      + (", ".join(row["job_failed_without_test_failure"]) or "none"))
        print(f"{row['pr']:<8}{row['current']:>6}{row['derived_naive']:>7}"
              f"{row['derived_keep_non_python']:>7}{len(row['failed_files']):>6}"
              f"{len(row['misses_naive']):>7}{len(row['misses_keep_non_python']):>7}  {detail}")
    print()
    errors = sum(len(r["artifact_errors"]) for r in rows)
    print(f"TOTAL MISSES naive={total_naive} keep-non-python={total_safe}"
          f"   (artifacts unreadable: {errors})")
    if not rows:
        # #10143 review P2: a window of ZERO PRs was scored as a clean window
        # (`exit 0`, "TOTAL MISSES 0") — the strongest possible false green for a
        # harness whose whole job is to be unfoolable. Nothing was read, so
        # nothing is certified.
        print("::error:: the window contained no PRs — nothing was measured")
        return 2
    for label, key in (("naive", "misses_naive"),
                       ("keep-non-python", "misses_keep_non_python")):
        for row in rows:
            for miss in row[key]:
                print(f"  MISS[{label}] pr#{row['pr']} {miss['file']} [{miss['class']}] "
                      f"changed={row['changed_modules'][:4] or row['changed'][:3]}")
    # ⛔ A RED PR WHOSE LEGS WERE NEVER READ IS AN UNREAD PR, NOT A CLEAN ONE
    # (#10143 review P1). `errors` only counts the paths that reached an
    # artifact and could not use it; a run whose artifact listing was EMPTY (or
    # carried no `pytest-log-*`) used to be skipped without an entry at all, so
    # this window condition scored it clean and exited 0. Artifact expiry is
    # ordinary (90 days by default; deleting a run deletes its artifacts), so
    # this is a reachable state, and `validate --prs <ids>` accepts runs of any
    # age.
    #
    # ⚠️ THE CONDITION IS `failed_jobs and not artifacts` — NOT `artifacts == 0`.
    # `artifacts` counts junit blobs read off RED `test*` legs only, so a fully
    # GREEN PR always has `artifacts == 0`. Measured on the §3 window: 24 of 40
    # PRs are green and would have been flagged PARTIAL by the broader test,
    # making `validate` unable to return 0 or 1 on any window — a fail-closed
    # check that always fires says nothing. A red PR with zero artifacts read is
    # the real unread state (every drop path now also records an
    # `artifact_errors` entry, which `errors` catches; this catches the residual
    # where a red leg yielded no run id at all).
    unread = [row["pr"] for row in rows if row["failed_jobs"] and not row["artifacts"]]
    if unread:
        print(f"::error:: pr(s) {', '.join(str(p) for p in unread[:5])} ran a red leg "
              "but uploaded no readable artifact — the window is PARTIAL, not clean")
    # ⛔ TWO MORE "I DID NOT ASSESS THIS PR" STATES, both byte-identical to a
    # green PR before this gate (#10143 review P2).
    #
    # `unseen` — `pr_ground_truth` saw no check run at all. Completed runs are
    # deletable and artifacts expire, and a `GITHUB_TOKEN`-created run can
    # complete with ZERO check runs (docs/00_index.md's #8375 row), so
    # `jobs: []`, `artifacts: 0`, `artifact_errors: []` is reachable and reads
    # exactly like a clean PR. `_check_runs` is now counted (`runs_seen`) and
    # the truth is not cached in that state, so a re-trigger at the same sha is
    # re-read instead of short-circuiting on the truncation.
    #
    # `out_of_model` — a failure whose file is outside the modelled selection.
    # `misses_for` calls that `not_in_current` ("a gap in the existing selector,
    # not in this one") and it ABSOLVES the derivation; that absolution is only
    # sound when the model actually covers every leg. `ALWAYS_ON_LEG_FILES`
    # covers the legs that can be modelled from a hand-list, and the one that
    # cannot (`test-d14-hosted-api`, marker-selected over the whole tree) is
    # deliberately not guessed at — so a residual entry means the harness could
    # not place a real failure, and it must say so instead of certifying.
    unseen = [row["pr"] for row in rows if not row.get("runs_seen", 0)]
    if unseen:
        print(f"::error:: pr(s) {', '.join(str(p) for p in unseen[:5])} yielded NO "
              "check run — nothing was assessed for them, so the window is "
              "PARTIAL, not clean")
    # `unassessed` — the THIRD "I did not assess this PR" state (#10143 review
    # P1). Runs exist (`runs_seen > 0`, so `unseen` cannot see it) and none
    # concluded red (`failed_jobs: []`, so `unread` cannot see it) because NO
    # `test*` leg was among them: `test_legs_seen == 0`. `pr_ground_truth` records
    # that count for exactly this reason and nothing read it, so a window whose
    # leg names changed in `python-ci.yml` — or a PR whose only check runs are
    # lint/typecheck legs — read "0 misses" and exited 0, and the emptiness was
    # CACHED.
    unassessed = [row["pr"] for row in rows if not row.get("test_legs_seen", 0)]
    if unassessed:
        print(f"::error:: pr(s) {', '.join(str(p) for p in unassessed[:5])} yielded NO "
              "`test*` leg — their test-level ground truth was never assessed, so "
              "the window is PARTIAL, not clean")
    out_of_model = sorted({(row["pr"], f) for row in rows
                           for f in row["not_in_current"]})
    if out_of_model:
        print("::error:: "
              f"{len(out_of_model)} failing file(s) fall outside the modelled "
              "selection, so the derivation was never scored on them — the "
              "window is PARTIAL, not clean: "
              + ", ".join(f"pr#{p} {f}" for p, f in out_of_model[:5]))
    if errors or unread or unseen or unassessed or out_of_model:
        # An unreadable artifact can only UNDER-count misses, so a zero-miss
        # window with unreadable artifacts is not a clean window. Say so in the
        # exit code rather than in a parenthetical (#10143 review P2).
        if errors:
            print(f"::error:: {errors} artifact(s) unreadable — the window is "
                  "PARTIAL, not clean")
        return 2
    return 0 if total_naive == 0 and total_safe == 0 else 1


def cmd_report(args: argparse.Namespace) -> int:
    manifest, data, index = _load_all(not args.exclude_type_checking)
    if _refuse_an_empty_core(data):
        return 2
    closures = data["closures"]
    summary = summarise(closures, index, data["scans"],
                        follow_type_checking=not args.exclude_type_checking)
    verdict = verdict_from_summary(summary, closures, index)
    payload = {
        "summary": summary,
        "verdict": verdict,
        "sizes": sorted(len(c.modules) for c in closures.values()),
        "sets": len({c.modules for c in closures.values()}),
        "tainted": sorted(rel for rel, c in closures.items() if c.tainted),
        "unresolved_core": data.get("unresolved_core", []),
    }
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=1, sort_keys=True))
    del manifest
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, extracted from `main` so it is TESTABLE.

    #10143 review P2: the `--window` default of 10 could only be observed by
    running the whole `validate` verb against the network, so nothing pinned it
    and it shipped broken. A parser builder makes the surface itself assertable.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb")

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", default="", help="write JSON to this path")
        p.add_argument("--exclude-type-checking", action="store_true",
                       help="treat TYPE_CHECKING imports as non-dependencies "
                            "(tach's default polarity; OURS is to include them)")

    p_closure = sub.add_parser("closure", help="derive the per-test closure")
    _common(p_closure)
    p_closure.add_argument("--module", default="",
                           help="report which tests a change to this module selects")
    p_closure.add_argument("--limit", type=int, default=25)
    p_closure.add_argument("--show-tainted", action="store_true")
    p_closure.add_argument(
        "--import-time-only", action="store_true",
        help="ignore function-local imports — the NAIVE reader, kept so the "
             "false-green trap in the report is measurable (median 19 vs 187)")

    p_validate = sub.add_parser("validate", help="replay historical PRs")
    _common(p_validate)
    p_validate.add_argument("--repo", default="daniel-ospina/tortoise")
    # #10143 review P2: `default=10` made `args.window` ALWAYS truthy, so the
    # explicit-PR path was clipped to 10 regardless: `--prs <12 ids>` analysed 10
    # and reported a clean window, which made the report's own 40-PR table
    # unreproducible from the artifact by any documented invocation. `None`
    # means "not passed"; only an EXPLICIT --window may clip.
    p_validate.add_argument("--window", type=int, default=None,
                            help="replay the newest N merged PRs (default 10); "
                                 "an explicit --window clips an explicit --prs list")
    p_validate.add_argument("--prs", type=int, nargs="*", default=[],
                            help="explicit PR numbers — authoritative, not clipped "
                                 "unless --window is also given")

    p_report = sub.add_parser("report", help="emit the report data")
    _common(p_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()

    # Bare invocation (`--json x`) means `closure`: argparse would otherwise hand
    # the closure verb's defaults to nobody and `args.json` would not exist.
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or raw[0] not in {"closure", "validate", "report"}:
        raw = ["closure", *raw]
    args = parser.parse_args(raw)
    verb = args.verb or "closure"
    if verb == "closure":
        return cmd_closure(args)
    if verb == "validate":
        return cmd_validate(args)
    if verb == "report":
        return cmd_report(args)
    parser.error(f"unknown verb {verb!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
