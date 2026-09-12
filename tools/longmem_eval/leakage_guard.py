"""#3011 Track F — gold-leakage guard for the context-assembly experiment.

Implements the §10 / §3 **static-reference assertion** (part 2 of the
gold-field leakage test) and the hermetic helpers the §10 **gold-field
perturbation run** (part 1) is built from.

Spec (frozen, authoritative):
``docs/experiments/2026-09-11-abc-context-assembly-experiment.md``:

* §3 "Labeling rule (hard)" — the subgraph must be built from graph content
  and the question only, **never** from the gold annotation.
* §3/§10 leakage test — "A static check (grep/AST) asserts that the seed,
  traversal, ranking, and B/C render code paths never reference `has_answer`,
  `answer_session_ids`, `lme_session_index`, **or the gold-evidence claim
  list** — neither its loader symbol nor its artifact path
  (``docs/experiments/artifacts/2026-09-11-abc-context-assembly/gold-evidence-claims.json``).
  Any reference fails."

Either failure is a **hard stop**: a leaked arm invalidates the whole
experiment, so :func:`scan_code_paths` walks the real call graph of the four
named code paths and :func:`assert_code_paths_clean` raises on the first
finding.

Scope — the four code paths and their real callees
--------------------------------------------------
The guard does **not** grep whole files. ``tools/longmem_eval/retrieve.py``
legitimately reads the gold fields in unrelated helpers (ingest helpers,
evidence-recall metrics, ``resolve_answer_session_indices``); a file-level
grep would be both a false positive and, worse, would teach the reader to
ignore the guard. Instead the guard resolves the **transitive call graph**
from the declared entry points, following only repo-internal module-level
functions, and scans exactly the reached functions plus (for the two
single-purpose modules) their module-level statements and comments:

===============  ====================================================
code path        entry point(s)
===============  ====================================================
traversal/rank   ``tortoise/subgraph.py`` (whole module;
seed entry       ``build_subgraph`` → ``_fetch_seeds``)
B/C render       ``tortoise/subgraph_render.py`` (whole module)
seed             ``tools/longmem_eval/retrieve.py`` → ``vector_search``
seed backends    ``tortoise/search_engine.py`` → ``run_vector_query``,
                 ``run_fts_query`` (the frozen BM25 fallback)
confidence       ``tools/longmem_eval/ep_activation.py`` →
(the renderer's   ``read_confidence``
dependency)
===============  ====================================================

Three detection channels per scanned function:

1. **identifiers** — ``Name``/``Attribute``/argument/import/def names whose
   lowercased text contains a gold marker (``has_answer``,
   ``answer_session``, ``session_index``, ``gold``). This catches source
   assembled from fragments and any gold loader symbol (all live under a
   ``gold`` name).
2. **string literals** — non-docstring constants containing a gold marker;
   this catches the artifact path/filename and any string-keyed property
   read.
3. **comments** — ``tokenize`` comments in the scanned line range containing
   a gold marker.

Docstrings are deliberately exempt: the spec's frozen modules document the
leakage rule *by name* ("never reads … the gold annotation"), and naming the
forbidden thing in prose is not referencing it in code. The literal property
names and the artifact path never appear in a docstring.

Perturbation helpers (part 1 of the leakage test) live here too so the test
and any future runner share one definition of "the perturbation":
:func:`fixed_session_index_permutation`, :func:`perturb_gold_props` and
:class:`RecordingMapping` (records every property key a render path reads, so
a gold read is caught even when it does not change the output bytes).
"""

from __future__ import annotations

import ast
import io
import random
import tokenize
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "CODE_PATHS",
    "GOLD_EVIDENCE_ARTIFACT_DIR",
    "GOLD_EVIDENCE_ARTIFACT_FILENAME",
    "GOLD_EVIDENCE_ARTIFACT_PATH",
    "GOLD_EVIDENCE_LOADER_SYMBOLS",
    "GOLD_PROPERTY_TOKENS",
    "CodePath",
    "LeakFinding",
    "RecordingMapping",
    "ScanReport",
    "assert_code_paths_clean",
    "fixed_session_index_permutation",
    "is_gold_property_key",
    "perturb_gold_props",
    "render_perturbation_pair",
    "scan_code_paths",
    "scan_source",
]

#: Repo root — ``<root>/tools/longmem_eval/leakage_guard.py``.
REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Frozen forbidden artifacts (spec §3 labeling rule / §10) ──────────────

#: The three gold properties the seed/traversal/ranking/render paths must
#: never read or reference (spec §3, §10.2).
GOLD_PROPERTY_TOKENS: tuple[str, ...] = (
    "has_answer",
    "answer_session_ids",
    "lme_session_index",
)

#: The gold-evidence claim-list artifact directory (spec §4 Storage).
GOLD_EVIDENCE_ARTIFACT_DIR = "docs/experiments/artifacts/2026-09-11-abc-context-assembly"

#: The gold-evidence claim-list artifact filename (spec §4 Storage).
GOLD_EVIDENCE_ARTIFACT_FILENAME = "gold-evidence-claims.json"

#: The full repo-relative artifact path named by the §10 static assertion.
GOLD_EVIDENCE_ARTIFACT_PATH = f"{GOLD_EVIDENCE_ARTIFACT_DIR}/{GOLD_EVIDENCE_ARTIFACT_FILENAME}"

#: Canonical loader-symbol names for the gold-evidence claim list. Track E
#: (the artifact builder / metric-5 scorer) owns the loader; the seed,
#: traversal, ranking and render paths must never name it. The guard also
#: flags **any** identifier whose lowercased text contains ``gold``, so a
#: differently-named loader is caught without editing this set; these names
#: pin the contract and make the intent explicit.
GOLD_EVIDENCE_LOADER_SYMBOLS = frozenset(
    {
        "load_gold_evidence_claims",
        "load_gold_evidence",
        "gold_evidence_claims",
        "gold_evidence_claim_list",
        "gold_claims",
        "load_gold_claims",
        "GOLD_EVIDENCE_CLAIMS",
        "GOLD_EVIDENCE_CLAIMS_PATH",
    }
)

#: Marker substrings for identifier names (case-insensitive). ``gold`` is
#: deliberately broad: every gold loader/constant lives under a ``gold``
#: name, and the two in-scope single-purpose modules contain no legitimate
#: ``gold`` identifier.
_GOLD_IDENTIFIER_MARKERS: tuple[str, ...] = (
    "has_answer",
    "answer_session",
    "session_index",
    "gold",
)

#: Marker substrings for non-docstring string literals.
_GOLD_STRING_MARKERS: tuple[str, ...] = (
    "has_answer",
    "answer_session",
    "session_index",
    "gold",
    "2026-09-11-abc-context-assembly",
)

#: Marker substrings for comments in a scanned line range.
_GOLD_COMMENT_MARKERS: tuple[str, ...] = (
    "has_answer",
    "answer_session",
    "lme_session_index",
    "session_index",
    "gold_evidence",
    "gold-evidence",
    "2026-09-11-abc-context-assembly",
)


# ── Findings + report ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class LeakFinding:
    """One forbidden gold reference found in a scanned code path."""

    code_path: str  # repo-relative module path
    function: str  # enclosing function, or "<module>"
    line: int
    kind: str  # identifier | string | comment
    detail: str

    def __str__(self) -> str:  # pragma: no cover - presentation only
        return (
            f"GOLD LEAK [{self.kind}] {self.code_path}:{self.line} ({self.function}): {self.detail}"
        )


@dataclass(frozen=True)
class ScanReport:
    """Result of :func:`scan_code_paths`."""

    findings: tuple[LeakFinding, ...]
    visited: tuple[str, ...]  # "path::function" for every scanned function
    modules: tuple[str, ...]  # every module reached by the call-graph walk

    @property
    def clean(self) -> bool:
        return not self.findings


# ── Declared code-path scope ──────────────────────────────────────────────


@dataclass(frozen=True)
class CodePath:
    """A declared code path: a module plus its entry-point functions.

    ``entry_points == ("*",)`` scans the whole module (every function plus
    module-level statements/comments) — correct for the two single-purpose
    modules that *are* the code path.
    """

    path: str  # repo-relative
    entry_points: tuple[str, ...]
    label: str


#: The four named code paths (spec §10.2) + the renderer's confidence
#: dependency. Order is documentation order, not significance.
CODE_PATHS: tuple[CodePath, ...] = (
    CodePath(
        "tortoise/subgraph.py",
        ("*",),
        "traversal + ranking + seed entry (Track A)",
    ),
    CodePath(
        "tortoise/subgraph_render.py",
        ("*",),
        "arm B/C render (Track B)",
    ),
    CodePath(
        "tools/longmem_eval/retrieve.py",
        ("vector_search",),
        "seed (Track A §3 seed policy)",
    ),
    CodePath(
        "tortoise/search_engine.py",
        ("run_vector_query", "run_fts_query"),
        "seed backends (vector + frozen BM25 fallback)",
    ),
    CodePath(
        "tools/longmem_eval/ep_activation.py",
        ("read_confidence",),
        "render dependency: EP posterior read (Track B/C)",
    ),
)


# ── module parsing / import resolution ────────────────────────────────────


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _module_functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Module-level functions + class methods, keyed by ``name``/``Class.name``."""
    funcs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs[f"{node.name}.{sub.name}"] = sub
    return funcs


def _resolve_module_path(module: str | None, level: int, current: str) -> str | None:
    """Resolve an ImportFrom module reference to a repo-relative ``.py`` path."""
    current_path = REPO_ROOT / current
    if level:
        base = current_path.parent
        for _ in range(level - 1):
            base = base.parent
        parts = module.split(".") if module else []
        candidate = base.joinpath(*parts)
    else:
        if not module:
            return None
        candidate = REPO_ROOT.joinpath(*module.split("."))
    for probe in (candidate.with_suffix(".py"), candidate / "__init__.py"):
        if probe.is_file():
            return str(probe.relative_to(REPO_ROOT))
    return None


def _submodule_path(module_path: str, name: str) -> str | None:
    """Resolve ``name`` as a submodule of the package at ``module_path``."""
    probe = (REPO_ROOT / module_path).parent / f"{name}.py"
    if probe.is_file():
        return str(probe.relative_to(REPO_ROOT))
    return None


def _import_table(tree: ast.Module, current: str) -> dict[str, tuple[str, str | None]]:
    """``local name → (repo-relative module path, symbol | None)``.

    A ``None`` symbol means the local name is bound to the module itself
    (``import x`` / ``from pkg import submodule``), so ``local.func`` is a
    module-attribute call.
    """
    imports: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _resolve_module_path(alias.name, 0, current)
                if target is not None:
                    imports[alias.asname or alias.name.split(".")[0]] = (target, None)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                target = _resolve_module_path(node.module, node.level, current)
                if target is None:
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    imports[alias.asname or alias.name] = (target, alias.name)
            else:
                # ``from . import submodule`` — each name is a submodule.
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    target = _resolve_module_path(alias.name, node.level, current)
                    if target is not None:
                        imports[alias.asname or alias.name] = (target, None)
    return imports


# ── scanning channels ─────────────────────────────────────────────────────


def _check_identifier(
    name: str | None,
    *,
    code_path: str,
    function: str,
    line: int,
    findings: list[LeakFinding],
) -> None:
    if not name:
        return
    lowered = name.lower()
    for marker in _GOLD_IDENTIFIER_MARKERS:
        if marker in lowered:
            findings.append(
                LeakFinding(
                    code_path=code_path,
                    function=function,
                    line=line,
                    kind="identifier",
                    detail=f"{name!r} matches marker {marker!r}",
                )
            )
            return


def _check_string(
    value: str,
    *,
    code_path: str,
    function: str,
    line: int,
    findings: list[LeakFinding],
) -> None:
    lowered = value.lower()
    for marker in _GOLD_STRING_MARKERS:
        if marker in lowered:
            findings.append(
                LeakFinding(
                    code_path=code_path,
                    function=function,
                    line=line,
                    kind="string",
                    detail=f"string literal matches marker {marker!r}: {value[:120]!r}",
                )
            )
            return


def _docstring_ids(tree: ast.Module) -> set[int]:
    """``id()`` of every docstring constant node (exempt from the scan)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            out.add(id(first.value))
    return out


def _comments(source: str) -> list[tuple[int, str]]:
    """``(line, text)`` for every comment token in ``source``."""
    out: list[tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):  # pragma: no cover
        pass
    return out


def _scan_tree(
    node: ast.AST,
    *,
    source: str,
    code_path: str,
    docstrings: set[int],
    comments: list[tuple[int, str]],
    findings: list[LeakFinding],
    function: str = "<module>",
    restrict_to: set[str] | None = None,
) -> None:
    """Recursively scan ``node``, attributing findings to the enclosing def.

    ``restrict_to`` (a set of function names) stops descent into functions
    that are outside the declared scope; ``None`` scans everything.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if restrict_to is not None and child.name not in restrict_to:
                continue
            _check_identifier(
                child.name,
                code_path=code_path,
                function=child.name,
                line=child.lineno,
                findings=findings,
            )
            for arg in (
                *getattr(child.args, "posonlyargs", []),
                *child.args.args,
                *child.args.kwonlyargs,
            ):
                _check_identifier(
                    arg.arg,
                    code_path=code_path,
                    function=child.name,
                    line=getattr(arg, "lineno", child.lineno),
                    findings=findings,
                )
            if child.args.vararg:
                _check_identifier(
                    child.args.vararg.arg,
                    code_path=code_path,
                    function=child.name,
                    line=child.lineno,
                    findings=findings,
                )
            if child.args.kwarg:
                _check_identifier(
                    child.args.kwarg.arg,
                    code_path=code_path,
                    function=child.name,
                    line=child.lineno,
                    findings=findings,
                )
            _scan_tree(
                child,
                source=source,
                code_path=code_path,
                docstrings=docstrings,
                comments=comments,
                findings=findings,
                function=child.name,
                restrict_to=None,
            )
            continue

        if isinstance(child, ast.ClassDef):
            _check_identifier(
                child.name,
                code_path=code_path,
                function=function,
                line=child.lineno,
                findings=findings,
            )
            _scan_tree(
                child,
                source=source,
                code_path=code_path,
                docstrings=docstrings,
                comments=comments,
                findings=findings,
                function=function,
                restrict_to=None,
            )
            continue

        if isinstance(child, ast.alias):
            # Check BOTH the module/import name and the alias — an aliased
            # gold import (``... import load_gold_evidence_claims as g``)
            # must not evade the scan.
            for imported_name in (child.name, child.asname):
                _check_identifier(
                    imported_name,
                    code_path=code_path,
                    function=function,
                    line=getattr(child, "lineno", 1),
                    findings=findings,
                )
        elif isinstance(child, ast.Name):
            _check_identifier(
                child.id,
                code_path=code_path,
                function=function,
                line=child.lineno,
                findings=findings,
            )
        elif isinstance(child, ast.Attribute):
            _check_identifier(
                child.attr,
                code_path=code_path,
                function=function,
                line=child.lineno,
                findings=findings,
            )
        elif isinstance(child, ast.keyword):
            _check_identifier(
                child.arg,
                code_path=code_path,
                function=function,
                line=child.lineno,
                findings=findings,
            )
        elif (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and id(child) not in docstrings
        ):
            _check_string(
                child.value,
                code_path=code_path,
                function=function,
                line=child.lineno,
                findings=findings,
            )

        _scan_tree(
            child,
            source=source,
            code_path=code_path,
            docstrings=docstrings,
            comments=comments,
            findings=findings,
            function=function,
            restrict_to=None,
        )


def scan_source(source: str, code_path: str = "<synthetic>") -> tuple[LeakFinding, ...]:
    """Scan a whole synthetic/real module source string (self-test seam)."""
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    comments = _comments(source)
    findings: list[LeakFinding] = []
    _scan_tree(
        tree,
        source=source,
        code_path=code_path,
        docstrings=docstrings,
        comments=comments,
        findings=findings,
    )
    for line, text in comments:
        lowered = text.lower()
        for marker in _GOLD_COMMENT_MARKERS:
            if marker in lowered:
                findings.append(
                    LeakFinding(
                        code_path=code_path,
                        function="<module>",
                        line=line,
                        kind="comment",
                        detail=f"comment matches marker {marker!r}: {text.strip()[:120]}",
                    )
                )
                break
    seen: set[tuple] = set()
    unique: list[LeakFinding] = []
    for finding in findings:
        key = (finding.code_path, finding.function, finding.line, finding.kind, finding.detail)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return tuple(unique)


# ── call-graph closure ────────────────────────────────────────────────────


@dataclass
class _Module:
    path: str
    source: str
    tree: ast.Module
    funcs: dict[str, ast.AST]
    imports: dict[str, tuple[str, str | None]]
    docstrings: set[int]
    comments: list[tuple[int, str]]


def _load(path: str, cache: dict[str, _Module]) -> _Module:
    if path not in cache:
        source = _read(path)
        tree = ast.parse(source)
        cache[path] = _Module(
            path=path,
            source=source,
            tree=tree,
            funcs=_module_functions(tree),
            imports=_import_table(tree, path),
            docstrings=_docstring_ids(tree),
            comments=_comments(source),
        )
    return cache[path]


def _call_target(
    call: ast.Call,
    module: _Module,
) -> tuple[str, str] | None:
    """Resolve a call to ``(repo-relative module path, function name)``."""
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in module.funcs:
            return (module.path, func.id)
        if func.id in module.imports:
            target_path, symbol = module.imports[func.id]
            return (target_path, symbol) if symbol else None
        return None

    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        base, attr = func.value.id, func.attr
        if f"{base}.{attr}" in module.funcs:
            return (module.path, f"{base}.{attr}")
        if base in module.imports:
            target_path, symbol = module.imports[base]
            if symbol is None:
                return (target_path, attr)
            submodule = _submodule_path(target_path, symbol)
            if submodule is not None:
                return (submodule, attr)
        return None
    return None


def _scan_function(
    module: _Module,
    node: ast.AST,
    function_name: str,
    findings: list[LeakFinding],
) -> None:
    _scan_tree(
        node,
        source=module.source,
        code_path=module.path,
        docstrings=module.docstrings,
        comments=module.comments,
        findings=findings,
        function=function_name,
        restrict_to=None,
    )
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    for line, text in module.comments:
        if start <= line <= end:
            lowered = text.lower()
            for marker in _GOLD_COMMENT_MARKERS:
                if marker in lowered:
                    findings.append(
                        LeakFinding(
                            code_path=module.path,
                            function=function_name,
                            line=line,
                            kind="comment",
                            detail=f"comment matches marker {marker!r}: {text.strip()[:120]}",
                        )
                    )
                    break


def scan_code_paths() -> ScanReport:
    """Walk the real call graph of the four named code paths; return findings.

    Only repo-internal module-level functions are followed (methods such as
    ``graph.query`` / ``sdk.dream`` / ``logger.warning`` are not), so the
    closure is exactly the seed/traversal/ranking/render surface plus the
    pure helpers those functions call.
    """
    cache: dict[str, _Module] = {}
    findings: list[LeakFinding] = []
    visited: list[str] = []
    modules_seen: list[str] = []
    scanned: set[tuple[str, str]] = set()

    frontier: list[tuple[str, str | None]] = []
    for code_path in CODE_PATHS:
        if not (REPO_ROOT / code_path.path).is_file():
            findings.append(
                LeakFinding(
                    code_path=code_path.path,
                    function="<scope>",
                    line=0,
                    kind="scope",
                    detail="declared code path does not exist — the guard is stale",
                )
            )
            continue
        if code_path.entry_points == ("*",):
            frontier.append((code_path.path, None))
        else:
            frontier.extend((code_path.path, name) for name in code_path.entry_points)

    while frontier:
        path, name = frontier.pop()
        module = _load(path, cache)
        if path not in modules_seen:
            modules_seen.append(path)

        if name is None:
            # Whole-module scan: every function + module-level statements.
            _scan_tree(
                module.tree,
                source=module.source,
                code_path=module.path,
                docstrings=module.docstrings,
                comments=module.comments,
                findings=findings,
                function="<module>",
                restrict_to=None,
            )
            for line, text in module.comments:
                lowered = text.lower()
                for marker in _GOLD_COMMENT_MARKERS:
                    if marker in lowered:
                        findings.append(
                            LeakFinding(
                                code_path=module.path,
                                function="<module>",
                                line=line,
                                kind="comment",
                                detail=f"comment matches marker {marker!r}: {text.strip()[:120]}",
                            )
                        )
                        break
            for func_name, node in module.funcs.items():
                key = (module.path, func_name)
                if key in scanned:
                    continue
                scanned.add(key)
                visited.append(f"{module.path}::{func_name}")
                _scan_function(module, node, func_name, findings)
                for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
                    target = _call_target(call, module)
                    if target and target not in scanned:
                        frontier.append(target)
            continue

        node = module.funcs.get(name)
        if node is None:
            findings.append(
                LeakFinding(
                    code_path=module.path,
                    function=name,
                    line=0,
                    kind="scope",
                    detail="declared entry point not found — the guard is stale",
                )
            )
            continue
        key = (module.path, name)
        if key in scanned:
            continue
        scanned.add(key)
        visited.append(f"{module.path}::{name}")
        _scan_function(module, node, name, findings)
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            target = _call_target(call, module)
            if target and target not in scanned:
                frontier.append(target)

    seen: set[tuple] = set()
    unique: list[LeakFinding] = []
    for finding in findings:
        key = (finding.code_path, finding.function, finding.line, finding.kind, finding.detail)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return ScanReport(
        findings=tuple(unique),
        visited=tuple(visited),
        modules=tuple(modules_seen),
    )


def assert_code_paths_clean() -> ScanReport:
    """Raise ``AssertionError`` listing every finding when the paths leak."""
    report = scan_code_paths()
    if report.findings:
        joined = "\n".join(f"  - {finding}" for finding in report.findings)
        raise AssertionError(
            "GOLD LEAKAGE — the #3011 seed/traversal/ranking/render paths "
            f"reference a gold artifact ({len(report.findings)} finding(s)):\n{joined}"
        )
    return report


# ── perturbation helpers (spec §10.1 gold-field perturbation run) ─────────


def fixed_session_index_permutation(n: int, *, seed: int = 0xD1CE) -> dict[int, int]:
    """One fixed permutation of ``range(n)`` — the spec's perturbation seed."""
    order = list(range(n))
    random.Random(seed).shuffle(order)
    return {index: order[index] for index in range(n)}


def is_gold_property_key(key: str) -> bool:
    """True when ``key`` is one of the forbidden gold properties."""
    return key in GOLD_PROPERTY_TOKENS


def perturb_gold_props(
    props: Mapping[str, Any],
    *,
    session_index_map: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    """Return a copy of ``props`` with the gold fields perturbed (§10.1).

    ``has_answer`` → ``False`` on every point; ``lme_session_index`` →
    its image under the fixed permutation. Non-gold fields are untouched, so
    a byte-identical render after this transform proves the render path never
    read the gold fields.
    """
    out = dict(props)
    if "has_answer" in out:
        out["has_answer"] = False
    if "lme_session_index" in out:
        try:
            index = int(out["lme_session_index"])
        except (TypeError, ValueError):
            return out
        if session_index_map is not None:
            out["lme_session_index"] = session_index_map.get(index, index)
    return out


class RecordingMapping(Mapping):
    """Read-through mapping that records every property key accessed.

    Wraps a ``{point_id: props}`` map (and, when ``nest`` is true, each inner
    props dict) so a test can assert a render path never reads a gold key —
    catching a gold read even when it happens not to change the output bytes.

    Caveat: a **bulk copy** of the mapping (``dict(props)``, as
    ``ep_activation.read_confidence`` performs) iterates the keys and fetches
    each value, so every key is recorded. That is a copy, not a gold read, so
    the render-path test relies on the strip-and-compare equivalence check
    plus the perturbation pair rather than on this recorder alone; the
    recorder is exercised directly (a synthetic gold read must be recorded)
    to keep it honest.
    """

    def __init__(
        self,
        data: Mapping,
        accessed: set[str] | None = None,
        *,
        nest: bool = False,
    ) -> None:
        self._data = data
        self.accessed = accessed if accessed is not None else set()
        self._nest = nest

    def _wrap(self, value: Any) -> Any:
        if self._nest and isinstance(value, Mapping) and not isinstance(value, RecordingMapping):
            return RecordingMapping(value, self.accessed)
        return value

    def __getitem__(self, key: str) -> Any:
        self.accessed.add(str(key))
        return self._wrap(self._data[key])

    def get(self, key: str, default: Any = None) -> Any:
        self.accessed.add(str(key))
        if key in self._data:
            return self._wrap(self._data[key])
        return default

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def render_perturbation_pair(
    render_fn,
    props: Mapping[str, Mapping[str, Any]],
    *,
    session_index_map: Mapping[int, int] | None = None,
) -> tuple[str, str]:
    """Render once, then once under the gold perturbation; return both texts.

    ``render_fn`` takes the point-property map and returns an object with a
    ``text`` attribute (a ``RenderResult``). ``props`` is the map the §10.1
    perturbation run mutates: ``has_answer`` → False on every point and one
    fixed permutation applied to ``lme_session_index``. The caller asserts
    ``baseline == perturbed`` byte-for-byte; any difference fails.
    """
    baseline = render_fn(props)
    perturbed_props = {
        pid: perturb_gold_props(point_props, session_index_map=session_index_map)
        for pid, point_props in props.items()
    }
    perturbed = render_fn(perturbed_props)
    return baseline.text, perturbed.text


def _main(argv: list[str] | None = None) -> int:
    """CLI: run the §10.2 static assertion; exit 1 on any finding.

    Track E records the pass/fail in the run manifest; nothing else here.
    """
    report = scan_code_paths()
    print(f"#3011 gold-leakage static assertion — scanned {len(report.visited)} functions ")
    print(f"across {len(report.modules)} modules")
    if report.findings:
        print(f"FAIL — {len(report.findings)} gold reference(s):")
        for finding in report.findings:
            print(f"  - {finding}")
        return 1
    print("PASS — no gold reference in the seed/traversal/ranking/render paths")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
