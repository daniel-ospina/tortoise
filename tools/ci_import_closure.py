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
    report     emit the markdown report (`docs/ci/core-import-closure.md`).

WHY junit AND NOT "the job was red"
    A red job is not a failing test. Measured on #10070 (`test (f)`): the job
    concluded `failure` while `junit.xml` recorded 2 907 tests and ZERO failures —
    the red came from a post-test hygiene step ("Assert no redislite orphans").
    Treating a red job as ground truth would inject phantom misses into the
    harness and condemn a derivation that is fine. The harness therefore reports
    `job_failed_without_test_failure` separately, and only a `<failure>`/`<error>`
    testcase is a missed test.

    ⛔ NO NETWORK IN `closure`. Only `validate` calls `gh`, and it caches every
    artifact under `.ci-selection/closure-cache/` (gitignored) so a re-run is
    offline.

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
GROUND_TRUTH_VERSION = 2

# The package whose modules a closure REPORTS. Other in-repo roots (`tests/`,
# `battery/`, `tools/`) are indexed and TRAVERSED — a test that imports
# `tests/_live_utils` inherits whatever that helper imports — but only modules
# under `REPORTED_ROOT` appear in a closure, because those are the paths the
# selector keys on.
REPORTED_ROOT = "tortoise"

# In-repo roots are discovered from the index, so this is only the anchor that
# tells us which directories to look at when `git ls-files` is unavailable.
FALLBACK_ROOTS = ("tortoise", "tests", "battery", "tools")

# Directories a static analysis must never descend into.
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
    a 60 s one. Falls back to a bounded walk of the in-repo roots.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--", "*.py"],
            capture_output=True, check=True, timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        paths: list[str] = []
        for root in FALLBACK_ROOTS:
            base = repo / root
            if not base.is_dir():
                continue
            for p in base.rglob("*.py"):
                rel = p.relative_to(repo)
                if not (_SKIP_DIR_PARTS & set(rel.parts)):
                    paths.append(rel.as_posix())
        return sorted(paths)
    return sorted(p for p in out.decode().split("\0") if p)


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
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` (and any conjunction of it)."""
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and node.id == "TYPE_CHECKING":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING":
            return True
    return False


def _imports_with_context(tree: ast.AST):
    """Yield `(import_node, in_type_checking, in_function)` for every import node.

    A recursive walk rather than `ast.walk` because both pieces of context are
    what the closure needs: a `TYPE_CHECKING` import is type-only, and a
    function-local import only executes on call — but for a TEST file the call
    happens, and for a module reached through a test the safe direction is to
    count it. Skipping either would under-select.
    """
    stack: list[tuple[ast.AST, bool, bool]] = [(tree, False, False)]
    while stack:
        node, in_tc, in_fn = stack.pop()
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for child in node.body:
                stack.append((child, True, in_fn))
            for child in node.orelse:
                stack.append((child, in_tc, in_fn))
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node, in_tc, in_fn
            continue
        child_fn = in_fn or isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        )
        for child in ast.iter_child_nodes(node):
            stack.append((child, in_tc, child_fn))


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

        module_bindings: set[str] = set()
        for node, in_tc, in_fn in _imports_with_context(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._add_import(scan, alias.name, node.lineno, in_tc, in_fn)
                    bound = alias.asname or alias.name.split(".")[0]
                    if self.index.is_in_repo_root(alias.name):
                        module_bindings.add(bound)
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
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    sub = f"{base}.{alias.name}" if base else alias.name
                    if self.index.exists(sub):
                        self._add_import(scan, sub, node.lineno, in_tc, in_fn,
                                         kind=FROM_SUBMODULE)
                        if self.index.is_in_repo_root(sub):
                            module_bindings.add(alias.asname or alias.name)

        self._scan_dynamic(tree, scan, module_bindings, pkg_parts)
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
        self, tree: ast.AST, scan: FileScan, module_bindings: set[str],
        pkg_parts: list[str],
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
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kind, name = _dynamic_callable(node.func)
            if kind == "import":
                self._scan_import_call(node, scan, pkg_parts, name)
            elif kind == "loader":
                self._scan_loader_call(node, scan, name)

            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "getattr"):
                continue
            if not node.args:
                continue
            base = node.args[0]
            if not (isinstance(base, ast.Name) and base.id in module_bindings):
                continue
            attr_node = node.args[1] if len(node.args) > 1 else None
            if isinstance(attr_node, ast.Constant) and isinstance(attr_node.value, str):
                # A literal attribute name is fully static: either it names a
                # submodule (a resolvable edge) or it does not (no edge at all).
                sub = f"{base.id}.{attr_node.value}"
                if self.index.exists(sub):
                    self._add_import(scan, sub, node.lineno, False, False,
                                     kind=DYNAMIC_LITERAL)
                continue
            scan.unknown.append(Unknown(
                UNKNOWN_DYNAMIC_ATTR, node.lineno,
                f"getattr({base.id}, <computed>) — a computed attribute on an "
                f"in-repo module reaches submodules the AST cannot see",
            ))

    def _scan_import_call(
        self, node: ast.Call, scan: FileScan, pkg_parts: list[str], name: str,
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
            self._add_import(scan, target, node.lineno, False, True, kind=DYNAMIC_LITERAL)
            return
        scan.unknown.append(Unknown(
            UNKNOWN_DYNAMIC_CALL, node.lineno,
            f"{name}(...) with a computed module name — the target cannot be "
            f"determined statically",
        ))

    def _scan_loader_call(self, node: ast.Call, scan: FileScan, name: str) -> None:
        """A module loaded from a PATH (`spec_from_file_location`, loader classes).

        `module_from_spec` is skipped: its spec comes from a call already handled
        (or already reported) at the spec site, so counting it again would double
        the taint for one dynamic load.
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
                                     False, True, kind=DYNAMIC_LITERAL)
                    return
            if not rel.endswith(".py"):
                return  # a wheel / tarball / non-python path, not a module load
        scan.unknown.append(Unknown(
            UNKNOWN_LOADER, node.lineno,
            f"{name}(...) loads a module from a computed path — its imports are "
            f"outside the AST graph",
        ))


def _dynamic_callable(func: ast.expr) -> tuple[str, str]:
    """Classify a call target: `("import"|"loader"|"", name)`.

    `importlib.import_module(...)` is matched through its `importlib` ROOT, so a
    repo module that happens to define `import_module` is not misread as the
    stdlib one — while `from importlib import import_module` (a bare Name) still
    matches, the over-inclusive direction, which is the safe one.
    """
    if isinstance(func, ast.Name):
        if func.id in _DYNAMIC_IMPORT_ATTRS:
            return ("loader" if func.id in _LOADER_NAMES else "import"), func.id
        return "", ""
    if not isinstance(func, ast.Attribute):
        return "", ""
    root = func
    while isinstance(root, ast.Attribute):
        root = root.value
    if not (isinstance(root, ast.Name) and root.id == "importlib"):
        if func.attr in _LOADER_NAMES:
            return "loader", func.attr
        return "", ""
    if func.attr in _LOADER_NAMES:
        return "loader", func.attr
    if func.attr in {"import_module", "run_module", "load_module"}:
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
    """One edge per (target, kind), keeping the STRONGEST occurrence.

    Strongest = the occurrence that survives the most restrictive reader: a
    module-level import beats a function-local one, and a runtime import beats a
    `TYPE_CHECKING` one. This is not cosmetic. The closure is computed ONCE and
    then filtered per reader (`follow_function_local`, `follow_type_checking`),
    so if the function-local edge won the dedupe, an `--import-time-only` run
    would filter it out and lose the module-level edge ENTIRELY — the file would
    look as if it never imported the target at all, which is exactly the
    under-selection the naive reader is supposed to demonstrate, not to be
    produced by a bug.

    #10143 review P1: this compared `type_checking` only, and
    `_imports_with_context` is a LIFO stack (`stack.pop()`), so the FUNCTION-LOCAL
    occurrence is normally yielded BEFORE the module-level one and won by
    accident. Measured on the `tortoise/b`-imported-both-ways shape: the survivor
    was `in_function=True`, module-level-only closure came back empty.
    Ties break on `line` (lowest first) so the reported location is stable.
    """
    best: dict[tuple[str, str], Dep] = {}
    for dep in deps:
        key = (dep.target, dep.kind)
        cur = best.get(key)
        if cur is None or (dep.type_checking, dep.in_function, dep.line) < (
                cur.type_checking, cur.in_function, cur.line):
            best[key] = dep
    return sorted(best.values(), key=lambda d: (d.kind, d.target, d.line))


# ---------------------------------------------------------------------------
# Closure
# ---------------------------------------------------------------------------
def merged_pr_numbers(repo: str, window: int) -> list[int]:
    """The newest `window` MERGED PRs, from the REST API.

    ⛔ NOT `gh pr list --state merged`: measured on this repo it returned 24 PRs
    while #10102, #10103, #10104 and #10106 — all `state=closed, merged=true` —
    were absent from it, so the window would silently skip real merges.
    `/pulls?state=closed` with a `merged_at` filter is authoritative.
    """
    out: list[int] = []
    page = 1
    while len(out) < window and page <= 10:
        batch = _gh_json([
            "api",
            f"repos/{repo}/pulls?state=closed&sort=created&direction=desc"
            f"&per_page=100&page={page}",
        ])
        if not isinstance(batch, list) or not batch:
            break
        for pr in batch:
            if pr.get("merged_at"):
                out.append(pr["number"])
                if len(out) >= window:
                    break
        page += 1
    return out


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
    committed tool: on this tree it yields a median core closure of 21 modules
    instead of 186, which would look like an 88 % reduction and would be a false
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


def core_test_files(manifest: dict) -> list[str]:
    """Repo-relative paths of every `core`-surface member that is a test file.

    Members are registered as basenames (with a few subdir paths); resolve them
    against the tree the same way `classify_test_file` does, so a subdir entry
    keeps working.
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
            out.extend(by_basename.get(member, []))
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
    return int(tail.split("/")[0])


def artifact_names_for_job(job_name: str) -> list[str]:
    """The `pytest-log-*` artifact a job uploads (python-ci.yml's naming contract).

    Shim: `test (f)` uploads `pytest-log-test-f`; a non-matrix job uploads
    `pytest-log-<job>`. The caller falls back to "all pytest-log artifacts in the
    run" when the expected name is absent, so a future rename degrades to a wider
    (never narrower) read.
    """
    if "(" in job_name and job_name.endswith(")"):
        base, _, label = job_name.rpartition(" (")
        return [f"pytest-log-{base}-{label.rstrip(')')}"]
    return [f"pytest-log-{job_name}"]


def parse_junit_failures(xml_bytes: bytes) -> dict:
    """Failing test FILES from a `junit.xml` (xunit1 `file` attr, classname fallback)."""
    import xml.etree.ElementTree as ET

    files: set[str] = set()
    names: list[str] = []
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
            classname = case.get("classname") or ""
            if "." in classname:
                head, _, tail = classname.rpartition(".")
                if head.startswith("tests") or tail.startswith("test"):
                    path = head.replace(".", "/") + ".py"
        if path:
            path = path.replace("\\", "/")
            if path.startswith("./"):
                path = path[2:]
            files.add(path)
        names.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return {"files": sorted(files), "names": names, "executed": executed}


def _download_artifact(repo: str, artifact_id: int, cache: Path) -> Path:
    target = cache / f"artifact-{artifact_id}.zip"
    if target.exists() and target.stat().st_size > 0:
        return target
    data = _run_gh(["api", f"repos/{repo}/actions/artifacts/{artifact_id}/zip"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _junit_from_zip(zip_path: Path) -> bytes | None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [n for n in zf.namelist() if n.endswith("junit.xml")]
            if not names:
                return None
            return zf.read(names[0])
    except (zipfile.BadZipFile, OSError):
        return None


def pr_ground_truth(repo: str, sha: str, cache: Path) -> dict:
    """Test-level failures on one PR head, straight from the run's junit artifacts.

    Returns `{"failed_files": [...], "failed_tests": [...], "jobs": [...],
    "job_failed_without_test_failure": [...], "artifacts": N,
    "artifact_errors": [...]}`. Cached per head sha, so a second run of a window
    is offline and instant — the measurement has to be reproducible.
    """
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / f"truth-v{GROUND_TRUTH_VERSION}-{sha}.json"
    if cached.exists():
        try:
            return json.loads(cached.read_text())
        except (OSError, ValueError):
            pass
    failed_jobs: list[str] = []
    run_ids: set[int] = set()
    for run in _check_runs(repo, sha):
        if run.get("conclusion") != "failure":
            continue
        name = run.get("name") or ""
        if not name.startswith("test"):
            continue
        failed_jobs.append(name)
        rid = _run_id(run)
        if rid:
            run_ids.add(rid)

    artifacts_seen = 0
    artifact_errors: list[str] = []
    failed_files: set[str] = set()
    failed_tests: list[str] = []
    no_test_failure: list[str] = []
    for rid in sorted(run_ids):
        listing = _gh_json([
            "api", f"repos/{repo}/actions/runs/{rid}/artifacts?per_page=100",
        ])
        arts = listing.get("artifacts", []) if isinstance(listing, dict) else []
        wanted_names = {n for job in failed_jobs for n in artifact_names_for_job(job)}
        selected = [a for a in arts if a.get("name") in wanted_names]
        if not selected:
            selected = [a for a in arts if str(a.get("name", "")).startswith("pytest-log-")]
        if not selected:
            continue
        pr_found = False
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
            if parsed["files"]:
                pr_found = True
                failed_files.update(parsed["files"])
                failed_tests.extend(parsed["names"])
        if not pr_found:
            no_test_failure.append(f"run {rid}")

    result = {
        "failed_files": sorted(failed_files),
        "failed_tests": sorted(set(failed_tests)),
        "jobs": sorted(failed_jobs),
        "job_failed_without_test_failure": no_test_failure,
        "artifacts": artifacts_seen,
        "artifact_errors": artifact_errors,
    }
    if not artifact_errors:
        # Do not cache a partial ground truth: a cached truncation would look
        # like a clean run on every later invocation.
        cached.write_text(json.dumps(result, indent=1))
    return result


def changed_files(repo: str, pr: int, cache: Path | None = None) -> list[str]:
    if cache is not None:
        cached = cache / f"files-{pr}.json"
        if cached.exists():
            try:
                return json.loads(cached.read_text())
            except (OSError, ValueError):
                pass
    out = _run_gh([
        "api", f"repos/{repo}/pulls/{pr}/files?per_page=100", "--paginate",
        "--jq", ".[].filename",
    ])
    files = sorted({line for line in out.decode().splitlines() if line.strip()})
    if cache is not None:
        (cache / f"files-{pr}.json").write_text(json.dumps(files, indent=1))
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
    114 of the 742 core files import nothing from the package — they assert
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
    core = core_test_files(manifest)
    closures = compute_closures(scans, index, core,
                                follow_type_checking=follow_type_checking,
                                follow_function_local=follow_function_local)
    return manifest, {"scans": scans, "closures": closures, "core": core}, index


def cmd_closure(args: argparse.Namespace) -> int:
    _manifest, data, index = _load_all(not args.exclude_type_checking,
                                       not getattr(args, "import_time_only", False))
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
        meta = _gh_json(["api", f"repos/{args.repo}/pulls/{pr}"])
        sha = meta["head"]["sha"]
        changed = changed_files(args.repo, pr, CACHE_DIR)
        truth = pr_ground_truth(args.repo, sha, CACHE_DIR)
        explain: dict = {}
        selection = ci.select(changed, "pull_request", manifest, explain)
        if selection["test_files"] == "ALL":
            current_rel = {_tests_rel(p) for p in all_tests} | {
                _tests_rel(p) for p in selection.get("slow_files", [])
            }
        else:
            current_rel = {_tests_rel(p) for p in selection["test_files"]} | {
                _tests_rel(p) for p in selection.get("slow_selected", [])
            }
        # TWO derived mappings, because the difference between them IS the
        # finding: the naive one (drop every core file whose closure misses the
        # diff) and the one that keeps the provably non-Python-dependency files.
        changed_rel = [_tests_rel(c) for c in changed]
        naive_info = derived_core_selection(changed, core, closures, index,
                                            keep_empty_closures=False)
        safe_info = derived_core_selection(changed, core, closures, index,
                                           keep_empty_closures=True)
        base_rel = current_rel - core_rel
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
    if errors:
        # An unreadable artifact can only UNDER-count misses, so a zero-miss
        # window with unreadable artifacts is not a clean window. Say so in the
        # exit code rather than in a parenthetical (#10143 review P2).
        print(f"::error:: {errors} artifact(s) unreadable — the window is PARTIAL, "
              "not clean")
        return 2
    return 0 if total_naive == 0 and total_safe == 0 else 1


def cmd_report(args: argparse.Namespace) -> int:
    manifest, data, index = _load_all(not args.exclude_type_checking)
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
             "false-green trap in the report is measurable (median 21 vs 186)")

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
