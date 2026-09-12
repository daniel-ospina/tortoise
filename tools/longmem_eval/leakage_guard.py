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
B/C builder      ``tools/longmem_eval/context_assembly_arms.py`` →
(arms B/C)       ``build_context_arm`` (**B/C branches only** — the arm-A
                 branch is excluded, see below), ``turns_by_point``
seed             ``tools/longmem_eval/retrieve.py`` → ``vector_search``
seed backends    ``tortoise/search_engine.py`` → ``run_vector_query``,
                 ``run_fts_query`` (the frozen BM25 fallback)
confidence       ``tools/longmem_eval/ep_activation.py`` →
(the renderer's   ``read_confidence``
dependency)
===============  ====================================================

The B/C builder is scanned by **entry point**, never whole-module: the
metrics layer in the same file legitimately names the gold-evidence artifact
for metrics 5/9, so a file-level scan would be a false positive. Arm A's and
arm D's branches of ``build_context_arm`` are **deliberately out of scope**
(see ``CODE_PATHS[...].branch_exclusions``): arm A is the gold-verbatim
oracle ceiling and reads ``answer_session_ids`` by design (spec §3), and arm
D is the no-context control that calls the legacy lane renderer. Only the
B/C branches are in scope. :func:`scan_code_paths` reports both the scanned
functions and the excluded regions, so the coverage claim is auditable.

Three detection channels per scanned function:

1. **identifiers** — ``Name``/``Attribute``/argument/import/def names whose
   lowercased text contains a gold marker (``has_answer``,
   ``answer_session``, ``session_index``, ``gold``). Any gold loader symbol
   is caught here, because every gold loader/constant lives under a ``gold``
   name.
2. **constant strings (after constant folding)** — non-docstring string
   *values* containing a gold marker. The scan resolves constant string
   expressions before matching, so a reference assembled from fragments is
   caught in its assembled form: ``"answer" + "_session_ids"``,
   ``"has_" + "answer"``, ``f"has_{'answer'}"``,
   ``getattr(props, "answer" + "_session_ids")``,
   ``"_".join(["answer", "session_ids"])``,
   ``"has_{}".format("answer")`` and ``"has_%s" % "answer"`` all resolve to
   their concrete value and are matched. A bytes literal (``b"has_answer"``)
   and ``b"has_answer".decode()`` are decoded and matched on the same
   channel. This also catches the artifact path/filename and any
   string-keyed property read.
3. **comments** — ``tokenize`` comments in the scanned line range containing
   a gold marker.

Docstrings are deliberately exempt: the spec's frozen modules document the
leakage rule *by name* ("never reads … the gold annotation"), and naming the
forbidden thing in prose is not referencing it in code. The literal property
names and the artifact path never appear in a docstring.

Known limits — what this guard does NOT catch
---------------------------------------------
This is a static, constant-folding assertion; it performs no dataflow or
runtime analysis. The following still evade the **static** check and are the
responsibility of the §10.1 gold-field perturbation run and human review:

* a name/value assembled at runtime from non-constant input
  (``"has_" + user_key``, ``props.get(k)`` where ``k`` is a parameter);
* **cross-statement constant propagation** — ``part = "answer"`` then
  ``"has_" + part`` never resolves, because locals are not tracked;
* a join/format whose arguments are not literals
  (``"_".join(parts)``, ``template.format(name)``);
* ``exec`` / ``eval`` / ``compile`` of assembler text,
  ``getattr(props, name)`` / ``props.__dict__`` reflection, and opening the
  gold artifact through a computed path;
* a gold *value* reaching a mapping through any key that is not a literal or
  a resolvable constant expression.

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
    "BranchExclusion",
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
    excluded: tuple[str, ...] = ()  # deliberately out-of-scope regions (auditable)

    @property
    def clean(self) -> bool:
        return not self.findings


@dataclass(frozen=True)
class BranchExclusion:
    """An arm/branch subtree that is deliberately **out of scan scope**.

    When ``build_context_arm`` is scanned, the guard skips every
    ``if <parameter> == <value>:`` subtree inside ``function`` (and the
    comment lines it spans) and reports the exclusion by name. Arm A is the
    gold-verbatim oracle ceiling (spec §3) and reads ``answer_session_ids``
    by design; only the B/C branches are part of the B/C render path the
    §10.2 static assertion names.
    """

    function: str
    parameter: str
    value: str
    reason: str

    def describe(self, code_path: str) -> str:
        return (
            f"{code_path}::{self.function} — branch {self.parameter} == "
            f"{self.value!r} skipped: {self.reason}"
        )


# ── Declared code-path scope ──────────────────────────────────────────────


@dataclass(frozen=True)
class CodePath:
    """A declared code path: a module plus its entry-point functions.

    ``entry_points == ("*",)`` scans the whole module (every function plus
    module-level statements/comments) — correct for the two single-purpose
    modules that *are* the code path.

    ``branch_exclusions`` carve deliberately out-of-scope branches (e.g. arm
    A of the B/C builder) out of an otherwise-scanned entry point.
    ``excluded_functions`` name functions that are out of scope even when a
    scanned function calls them (they are not entered by the call-graph
    walk). Both are surfaced in :class:`ScanReport` so the guard's coverage
    claim states exactly what is and is not asserted.
    """

    path: str  # repo-relative
    entry_points: tuple[str, ...]
    label: str
    branch_exclusions: tuple[BranchExclusion, ...] = ()
    excluded_functions: tuple[str, ...] = ()


#: Arm A of the B/C builder is the gold-verbatim oracle ceiling (spec §3):
#: it reads ``answer_session_ids`` **by design**, so its branch is excluded
#: from the B/C static assertion. Only the B/C branches are in scope.
_ARM_A_EXCLUSION = BranchExclusion(
    function="build_context_arm",
    parameter="arm",
    value="A",
    reason=(
        "arm A is the gold-verbatim oracle ceiling (spec §3) and reads "
        "answer_session_ids by design; only the B/C branches are asserted"
    ),
)

#: Arm D is the no-context control (question + date header only): it is not a
#: B/C render and not a gold path, but it calls the **legacy lane renderer**
#: ``tortoise.retrieval.render_context``, which legitimately reads
#: ``lme_session_index`` and is not part of the §10.2 B/C assertion. Excluding
#: the branch keeps the guard from following that call graph.
_ARM_D_EXCLUSION = BranchExclusion(
    function="build_context_arm",
    parameter="arm",
    value="D",
    reason=(
        "arm D is the no-context control (question + date header only) and "
        "calls the legacy lane renderer; neither it nor its callees are part "
        "of the B/C render path the §10.2 assertion names"
    ),
)


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
        "tools/longmem_eval/context_assembly_arms.py",
        ("build_context_arm", "turns_by_point"),
        "arm B/C context builder (Track B/C) — arm A and arm D branches excluded",
        branch_exclusions=(_ARM_A_EXCLUSION, _ARM_D_EXCLUSION),
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


def _constant_str(node: ast.AST | None) -> str | None:
    """Resolve a **constant** string expression, or ``None`` when not constant.

    Resolution is recursive, so an assembly of 3+ fragments folds to one
    value. Covered forms:

    * ``ast.Constant`` str;
    * ``ast.BinOp`` ``+`` over constant strings, and ``%`` over a constant
      string and a literal right operand;
    * ``ast.JoinedStr`` (f-string) whose parts are all constant;
    * ``"_".join(["a", "b"])`` over a constant list/tuple of constants;
    * ``"has_{}".format("answer")`` with a constant template and constant
      args.

    **Not** covered (see the module docstring's *Known limits*): locals are
    not tracked, so a value assembled from a name never resolves.
    """
    if node is None:
        return None

    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            # A bytes literal is the same reference in another encoding.
            return node.value.decode("utf-8", "replace")
        return None

    if isinstance(node, ast.BinOp):
        left = _constant_str(node.left)
        if left is None:
            return None
        if isinstance(node.op, ast.Add):
            right = _constant_str(node.right)
            return None if right is None else left + right
        if isinstance(node.op, ast.Mod):
            try:
                rhs = ast.literal_eval(node.right)
            except (ValueError, SyntaxError, TypeError):
                return None
            try:
                return left % rhs
            except (TypeError, ValueError):
                return None
        return None

    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = _constant_str(value.value)
                if resolved is None:
                    return None
                parts.append(resolved)
            else:
                return None
        return "".join(parts)

    if isinstance(node, ast.Call):
        func = node.func
        if not isinstance(func, ast.Attribute) or node.keywords:
            return None
        target = _constant_str(func.value)
        if target is None:
            return None
        args = [_constant_str(arg) for arg in node.args]
        if func.attr == "join":
            # ``sep.join(iterable)`` — one iterable of constant strings.
            if len(node.args) != 1 or not isinstance(node.args[0], (ast.List, ast.Tuple)):
                return None
            items = [_constant_str(elt) for elt in node.args[0].elts]
            if any(item is None for item in items):
                return None
            return target.join(items)
        if func.attr == "format":
            if any(arg is None for arg in args):
                return None
            try:
                return target.format(*args)
            except (IndexError, KeyError, ValueError):
                return None
        if func.attr == "decode" and not node.args and not node.keywords:
            # ``b"...".decode()`` — closes the bytes-literal bypass.
            if isinstance(func.value, ast.Constant) and isinstance(func.value.value, bytes):
                return func.value.value.decode("utf-8", "replace")
            return None
        return None

    return None


def _resolvable_string(node: ast.AST) -> str | None:
    """The assembled constant value of ``node`` when it is worth re-checking.

    ``ast.Constant`` is excluded: plain literals are checked by the dedicated
    constant branch. ``getattr(obj, <resolvable>)`` resolves its attribute
    argument explicitly so the call site (not just the argument) is reported.
    """
    if isinstance(node, (ast.BinOp, ast.JoinedStr)):
        return _constant_str(node)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            return _constant_str(node.args[1])
        return _constant_str(node)
    return None


def _branch_value(test: ast.expr, parameter: str) -> str | None:
    """Return the literal when ``test`` is ``<parameter> == <literal>``."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return None
    if not isinstance(test.ops[0], ast.Eq):
        return None
    left, right = test.left, test.comparators[0]
    for name_node, literal in ((left, right), (right, left)):
        if (
            isinstance(name_node, ast.Name)
            and name_node.id == parameter
            and isinstance(literal, ast.Constant)
            and isinstance(literal.value, str)
        ):
            return literal.value
    return None


def _is_excluded_if(
    node: ast.AST,
    function: str,
    exclusions: tuple[BranchExclusion, ...],
) -> bool:
    """True when ``node`` is an ``if`` matching an in-scope exclusion."""
    if not isinstance(node, ast.If):
        return False
    return any(
        exclusion.function == function
        and _branch_value(node.test, exclusion.parameter) == exclusion.value
        for exclusion in exclusions
    )


def _excluded_line_ranges(
    node: ast.AST,
    function: str,
    exclusions: tuple[BranchExclusion, ...],
) -> list[tuple[int, int]]:
    """Source line ranges of excluded branches inside ``node`` (for comments)."""
    ranges: list[tuple[int, int]] = []
    for child in ast.walk(node):
        if _is_excluded_if(child, function, exclusions):
            start = getattr(child, "lineno", 1)
            ranges.append((start, getattr(child, "end_lineno", start)))
    return ranges


def _line_in_ranges(line: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= line <= end for start, end in ranges)


def _iter_scanned_calls(
    node: ast.AST,
    function: str,
    exclusions: tuple[BranchExclusion, ...],
) -> Iterator[ast.Call]:
    """Yield every ``ast.Call`` in the **scanned** region of ``node``.

    Excluded branch subtrees are not descended into, so a call that lives
    only in an out-of-scope branch (e.g. arm A's ``retrieve_for_question``,
    or arm D's legacy ``render_context``) is not followed by the call-graph
    closure and cannot expand the scan into unrelated modules.
    """
    for child in ast.iter_child_nodes(node):
        if _is_excluded_if(child, function, exclusions):
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _iter_scanned_calls(child, child.name, exclusions)
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from _iter_scanned_calls(child, function, exclusions)


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
    branch_exclusions: tuple[BranchExclusion, ...] = (),
) -> None:
    """Recursively scan ``node``, attributing findings to the enclosing def.

    ``restrict_to`` (a set of function names) stops descent into functions
    that are outside the declared scope; ``None`` scans everything.
    ``branch_exclusions`` skips deliberately out-of-scope branch subtrees.
    """
    for child in ast.iter_child_nodes(node):
        if branch_exclusions and _is_excluded_if(child, function, branch_exclusions):
            continue
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
                branch_exclusions=branch_exclusions,
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
                branch_exclusions=branch_exclusions,
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
            and isinstance(child.value, (str, bytes))
            and id(child) not in docstrings
        ):
            literal = _constant_str(child)
            if literal is not None:
                _check_string(
                    literal,
                    code_path=code_path,
                    function=function,
                    line=child.lineno,
                    findings=findings,
                )

        resolved = _resolvable_string(child)
        if resolved is not None:
            _check_string(
                resolved,
                code_path=code_path,
                function=function,
                line=getattr(child, "lineno", 1),
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
            branch_exclusions=branch_exclusions,
        )


def scan_source(
    source: str,
    code_path: str = "<synthetic>",
    *,
    branch_exclusions: tuple[BranchExclusion, ...] = (),
) -> tuple[LeakFinding, ...]:
    """Scan a whole synthetic/real module source string (self-test seam).

    ``branch_exclusions`` lets a test reproduce the declared scope (e.g. the
    arm-A/arm-D carve-out of the B/C builder) on synthetic source.
    """
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    comments = _comments(source)
    findings: list[LeakFinding] = []
    excluded_ranges: list[tuple[int, int]] = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            excluded_ranges.extend(_excluded_line_ranges(fn, fn.name, branch_exclusions))
    _scan_tree(
        tree,
        source=source,
        code_path=code_path,
        docstrings=docstrings,
        comments=comments,
        findings=findings,
        branch_exclusions=branch_exclusions,
    )
    for line, text in comments:
        if _line_in_ranges(line, excluded_ranges):
            continue
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
    branch_exclusions: tuple[BranchExclusion, ...] = (),
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
        branch_exclusions=branch_exclusions,
    )
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    excluded_ranges = _excluded_line_ranges(node, function_name, branch_exclusions)
    for line, text in module.comments:
        if start <= line <= end and not _line_in_ranges(line, excluded_ranges):
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

    branch_exclusions_by_module: dict[str, tuple[BranchExclusion, ...]] = {}
    excluded_functions_by_module: dict[str, frozenset[str]] = {}
    excluded: list[str] = []
    for code_path in CODE_PATHS:
        if code_path.branch_exclusions:
            branch_exclusions_by_module[code_path.path] = (
                *branch_exclusions_by_module.get(code_path.path, ()),
                *code_path.branch_exclusions,
            )
            excluded.extend(
                exclusion.describe(code_path.path) for exclusion in code_path.branch_exclusions
            )
        if code_path.excluded_functions:
            excluded_functions_by_module[code_path.path] = frozenset(
                {
                    *excluded_functions_by_module.get(code_path.path, frozenset()),
                    *code_path.excluded_functions,
                }
            )
            excluded.extend(
                f"{code_path.path}::{function} — deliberately out of scope (not called)"
                for function in code_path.excluded_functions
            )

    def _is_excluded_target(target: tuple[str, str]) -> bool:
        return target[1] in excluded_functions_by_module.get(target[0], frozenset())

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

        exclusions = branch_exclusions_by_module.get(module.path, ())

        if name is None:
            # Whole-module scan: every function + module-level statements.
            whole_module_excluded: list[tuple[int, int]] = []
            for func_name, func_node in module.funcs.items():
                whole_module_excluded.extend(
                    _excluded_line_ranges(func_node, func_name, exclusions)
                )
            _scan_tree(
                module.tree,
                source=module.source,
                code_path=module.path,
                docstrings=module.docstrings,
                comments=module.comments,
                findings=findings,
                function="<module>",
                restrict_to=None,
                branch_exclusions=exclusions,
            )
            for line, text in module.comments:
                if _line_in_ranges(line, whole_module_excluded):
                    continue
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
                if func_name in excluded_functions_by_module.get(module.path, frozenset()):
                    continue
                key = (module.path, func_name)
                if key in scanned:
                    continue
                scanned.add(key)
                visited.append(f"{module.path}::{func_name}")
                _scan_function(module, node, func_name, findings, exclusions)
                for call in _iter_scanned_calls(node, func_name, exclusions):
                    target = _call_target(call, module)
                    if target and target not in scanned and not _is_excluded_target(target):
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
        _scan_function(module, node, name, findings, exclusions)
        for call in _iter_scanned_calls(node, name, exclusions):
            target = _call_target(call, module)
            if target and target not in scanned and not _is_excluded_target(target):
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
        excluded=tuple(excluded),
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
    for entry in report.visited:
        print(f"  scanned: {entry}")
    for entry in report.excluded:
        print(f"  out of scope: {entry}")
    if report.findings:
        print(f"FAIL — {len(report.findings)} gold reference(s):")
        for finding in report.findings:
            print(f"  - {finding}")
        return 1
    print("PASS — no gold reference in the seed/traversal/ranking/render paths")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
