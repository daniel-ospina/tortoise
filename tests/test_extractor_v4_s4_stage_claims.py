"""#8500 — the live S4 stage's divergence from the v4 design must not rot silently.

THE DEFECT THIS PINS
--------------------
`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` states *"There is no S4"* and
lists eight steps (S0·S1·S2.1·S2.2·S2.3·S3·S5·S6). `tortoise/extractor_v2.py`
runs a stage under the S4 slot whenever a story exists, and that stage's output
is unioned into the embed list that gets committed.

The design carries a drift marker for the divergence (#8498, `#8500`) — **but a
marker is prose.** Nothing in the tree failed when the code changed, so the
marker's behavioural claims were free to rot, and its line citations had already
moved by the time the marker landed (`4c801fb18` figures vs its own tree at
`9ffc5e8c1`: `merge_embed_lists` def `:2673` vs `:2750`, `run_s4` `:7396` vs
`:7473`). This is the #8506 citation-rot class — *a stale claim recording a past
state and never revalidated* — applied to the passage #8506's guard does not
reach (`test_extractor_v4_citation_rot.py` checks that a quoted SYMBOL exists;
it does not check a single behavioural claim).

The claims below are the ones a lane would act on DESTRUCTIVELY: reading *"there
is no S4"* and deleting `run_s4`, or reaching for `TORTOISE_S4_DELTA=0` to turn
S4 off and silently shipping the legacy contract to production instead.

WHAT THIS PINS
--------------
1. The design's position is unchanged, its marker — read as a SCOPED block —
   still names `#8500`, and it still tells a reader not to delete `run_s4` on
   the document's authority alone: the divergence is RECORDED, not tidied
   away. (A document-wide read is vacuous; the marker passage is what counts.)
2. `extractor_v2.py`'s module docstring carries the `G5 record — the live S4
   stage (#8500)` block the S4 call site points at (before this change the call
   site named a record that did not exist).
3. The shipped-code facts, resolved with `ast` so whitespace reformatting
   cannot red them:
   * `run_s4` is called under `if story:` and under NO OTHER guard at all —
     an ALLOW-LIST over the enclosing `If` tests, not a deny-list of flag
     spellings (a nested `if s4_delta:` makes `TORTOISE_S4_DELTA=0` a real
     off switch while a spelling check reports clean);
   * `_s4_delta_enabled()` defaults True, and its value is threaded as the
     call's `delta` OUTPUT-contract kwarg — not used as a call gate;
   * `merge_embed_lists`'s union is ASSIGNED and is STILL the merged value when
     it reaches `result["embed_list"]` — no bare rebinding of the merge target,
     and no clearing of the S4 result, in between (assignment to a name with a
     matching spelling alone left a replace-not-merge green; #8601 review);
   * the three side-effect bypass paths exist ON THE ROUTE THAT REACHES
     `extract_session_v2` (the `sdk.py` v1/`summary=` dispatch — pinned as a
     ROUTING to the S4-less path, not merely as a predicate — and the
     empty-conversation early return before S1);
4. and — the NEGATIVE half — a SOURCE-ORDER scan of `extract_session_v2`:
   no early `return`/`raise` appears before the S4 call site other than the
   enumerated empty-conversation one, so a fourth exit on the entry→call path
   reds the guard instead of landing invisibly (the #8601 review proved a new
   early return left the positive checks above green). This is a source-order
   scan, NOT a reachability analysis: an exit on the `not story` side of the
   call's own `if story:` guard is deliberately outside it, because S4 is
   already gated off on that path and the exit is therefore not an S4 bypass.
5. The successor family: the `G5 record` and the design marker keep their
   measured halves (the fold's stages are present but flag-gated OFF, and
   neither can emit a candidate a coverage check found missing), the
   `check_batch` / `classify_items` return-key ALLOW-LISTs still contain no
   item-carrying signal, `apply_vet`'s ONE append path is the Rule-4 entity
   restore from `prior` — never a narrative-sourced candidate — and the
   conclusion stays scoped to the FOLD (`run_s4` is itself a shipped stage that
   emits the candidates the folded stages cannot, so "no shipped stage" is
   false and self-refuting).

NOT promised: line numbers. The doc's own rule is that the symbol is
authoritative and the number a convenience; this file resolves symbols and
behaviour, never offsets.

Mostly static and stdlib-only. Three tests are BEHAVIOURAL: they import the
shipped modules in-process (`tortoise.vet_gate`, `tortoise.extractor_v2`) and
call them, because the claims they pin are about what those functions DO, not
about their text. Neither import touches the network, the DB or the graph.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR = ROOT / "tortoise" / "extractor_v2.py"
SDK = ROOT / "tortoise" / "sdk.py"
DOC = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"

#: The anchor the S4 call site names ("see the ``G5 record — the live S4 stage
#: (#8500)`` in the module docstring"). Pinned as a literal because a rename is
#: exactly what breaks the pointer.
RECORD_ANCHOR = "G5 record — the live S4 stage (#8500)"
#: The design's position on S4, and the two sentences that keep the divergence
#: from being read as an invitation to delete the stage.
DESIGN_POSITION = "There is no S4 — do not restore one."
MARKER_NEVER_DELETE = "Never delete `run_s4` on the strength of this document alone."
GUARD_NAME = "test_extractor_v4_s4_stage_claims.py"
#: The unique opening of the design's S4 drift marker — the blockquote whose own
#: tracker reference the guard must pin. Scoping matters: the bare string
#: `#8500` occurs many times in the document (the pinned literals here are each
#: unique today), so a document-wide membership check can be satisfied by an
#: unrelated mention while THIS passage loses its tracker line — the #8601
#: review rewrote only the marker's tracker line and the document-wide check
#: stayed green. The scoping itself is proven by
#: `test_the_marker_tracker_check_is_scoped_to_the_marker`.
MARKER_START = "⛔ DRIFT MARKER"
#: The marker's OWN tracker reference. Pinned verbatim (through `_flatten`, so a
#: reflow is fine): `#8500` also occurs in the record-anchor sentence inside the
#: same blockquote, so a plain `"#8500" in marker` would still be vacuous.
MARKER_TRACKER_REF = "tracked at **`#8500`**"


def _call_name(node: ast.expr) -> str:
    """The bare name of a call target (`run_s4`, `mod.fn` -> `fn`)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _flatten(text: str) -> str:
    """`> ` quote markers and hard wraps removed, so a literal phrase check
    pins MEANING rather than layout.

    The marker's sentences wrap across lines, so a literal phrase check against
    the raw text would red on a reflow that changed nothing — the same
    brittleness `test_extractor_v4_citation_rot.py` avoids by pinning meaning,
    not layout.
    """
    return re.sub(r"\s+", " ", text.replace("\n> ", " "))


def _flat_doc() -> str:
    """The design doc through `_flatten`."""
    return _flatten(DOC.read_text(encoding="utf-8"))


def _marker_block(text: str | None = None) -> str:
    """The drift marker's blockquote, flattened — the PASSAGE, not the document.

    A document-wide check for the marker's claims is vacuous: `#8500` occurs at
    several other places, so rewriting only the marker's own tracker line left
    `"#8500" in doc` green (the #8601 review proved it). This scopes the read to
    the marker blockquote.
    """
    source = DOC.read_text(encoding="utf-8") if text is None else text
    lines = source.splitlines()
    start = next((i for i, line in enumerate(lines) if MARKER_START in line), None)
    if start is None:
        raise AssertionError(
            "the design's DRIFT MARKER blockquote is gone — the divergence is no "
            "longer recorded where a reader of the step list will find it."
        )
    block: list[str] = []
    for line in lines[start:]:
        if line.startswith(">"):
            block.append(line)
        elif block:
            break
    return _flatten("\n".join(block))


def _module_docstring(source: str) -> str:
    """`extractor_v2.py`'s MODULE docstring — the home the S4 call site names.

    Scoped deliberately: the call site says *"see the ``G5 record — the live S4
    stage (#8500)`` in the module docstring"*, and `config/ci-surfaces.yml` and
    the design doc say the same. A FILE-WIDE membership check cannot see the
    record MOVE out of the docstring — the #8601 review relocated it verbatim
    to a `#` comment at end of file and every assertion stayed green, leaving
    the pointer dangling again (the defect this pins). Returns `""` when there
    is no module docstring, so the caller's `RECORD_ANCHOR in doc` assertion is
    what reports it.
    """
    return ast.get_docstring(ast.parse(source)) or ""


def _dict_constant(expr: ast.expr, key: str) -> ast.expr | None:
    """The value bound to `key` in the first dict in `expr`'s subtree that has
    it. Structural, so `"degraded" in body` cannot pass for `degraded: False`."""
    for node in ast.walk(expr):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values, strict=True):
            if isinstance(k, ast.Constant) and k.value == key:
                return v
    return None


def _env_flag_default_true(call: ast.expr) -> bool:
    """`env_flag("TORTOISE_S4_DELTA", True)` — as a structure, not a spelling.

    `ast.unparse` renders the string with single quotes, so a source-literal
    comparison would red on the renderer rather than on the code.
    """
    return (
        isinstance(call, ast.Call)
        and _call_name(call.func) == "env_flag"
        and len(call.args) == 2
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "TORTOISE_S4_DELTA"
        and isinstance(call.args[1], ast.Constant)
        and call.args[1].value is True
    )


def _comparison_kind(node: ast.expr) -> str:
    """Label one operand of the sdk v1 dispatch, or `""` for anything else.

    The OPERATOR is part of the label. An operator-blind match accepted the
    semantics-inverting rewrites `extractor != "v1"` and `summary is None`,
    which would leave the marker's bypass paths (i)/(ii) false while the guard
    reported clean (the #8601 review proved it).
    """
    if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
        return ""
    if len(node.comparators) != 1 or len(node.ops) != 1:
        return ""
    op = node.ops[0]
    comparator = node.comparators[0]
    if (
        node.left.id == "extractor"
        and isinstance(op, ast.Eq)
        and isinstance(comparator, ast.Constant)
        and comparator.value == "v1"
    ):
        return "extractor_v1"
    if (
        node.left.id == "summary"
        and isinstance(op, ast.IsNot)
        and isinstance(comparator, ast.Constant)
        and comparator.value is None
    ):
        return "summary_not_none"
    return ""


def _is_v1_dispatch(test: ast.expr) -> bool:
    """`extractor == "v1" or summary is not None` — the sdk v1 route.

    Order-insensitive: the operands are matched as a SET, because swapping the
    two logically-equivalent comparisons is behaviour-preserving and must not
    red the guard (the #8601 review proved the positional match did).
    """
    if not isinstance(test, ast.BoolOp) or not isinstance(test.op, ast.Or):
        return False
    kinds = {_comparison_kind(value) for value in test.values}
    return kinds == {"extractor_v1", "summary_not_none"}


#: The callee each arm of the v1 dispatch MUST route to. The `v1`/`summary=`
#: arm is bypass (i)/(ii): it must land on the S4-less v1 path, and the
#: fall-through on the S4-running v2 path. Pinned because the #8601 review
#: swapped them and every test stayed green while the bypass claims went false.
V1_BYPASS_CALLEE = "_commit_session_v1"
V2_FALLTHROUGH_CALLEE = "_commit_session_v2"


def _iter_stmt_lists(node: ast.AST) -> list[list[ast.stmt]]:
    """Every statement list inside `node`, NOT descending into nested defs.

    Needed because `ast.walk` gives no parent: to read the statement that
    FOLLOWS the dispatch `If` (the v2 fall-through) it must be located inside
    its own sibling list.
    """
    lists: list[list[ast.stmt]] = []
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            lists.append(block)
    for handler in getattr(node, "handlers", []) or []:
        lists.extend(_iter_stmt_lists(handler))
    for case in getattr(node, "cases", []) or []:
        lists.extend(_iter_stmt_lists(case))
    for block in list(lists):
        for stmt in block:
            if isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            lists.extend(_iter_stmt_lists(stmt))
    return lists


def _enclosing_stmt_list(
    tree: ast.Module, node: ast.If
) -> tuple[list[ast.stmt], int] | None:
    """The statement list holding `node`, and `node`'s index in it."""
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        if not (fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno)):
            continue
        for body in _iter_stmt_lists(fn):
            if node in body:
                return body, body.index(node)
    return None


def _return_callee(stmts: list[ast.stmt]) -> str:
    """The callee of the first `return <call>(...)` in this statement list.

    `""` when there is no such return, so the caller reports a missing route
    rather than silently accepting it.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
            return _call_name(stmt.value.func)
    return ""


def _v1_dispatch_routing_violations(source: str) -> list[str]:
    """Every reason the `v1`/`summary=` dispatch is not a BYPASS of S4.

    The #8601 review proved that matching only the PREDICATE left the ROUTING
    unconstrained: swapping the two callees at `sdk.py:6934` sent
    ``TORTOISE_EXTRACTOR=v1`` and ``summary=`` into the S4-RUNNING pipeline
    while every test in this file stayed green — falsifying the record's bypass
    paths (i)/(ii). So the branch bodies are pinned, not just the condition.
    """
    tree = ast.parse(source)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If) and _is_v1_dispatch(node.test)
    ]
    if not matches:
        return [
            "the `v1` / `summary=` dispatch (the record's bypass paths (i) and "
            "(ii)) is gone or was rewritten — re-derive the record's bypass list."
        ]
    if len(matches) > 1:
        return [
            f"{len(matches)} `v1` / `summary=` dispatch sites exist — the record "
            "names one route, so re-derive the bypass list rather than letting "
            "an unrelated look-alike satisfy the pin"
        ]
    node = matches[0]
    site = _enclosing_stmt_list(tree, node)
    if site is None:
        return ["the `v1` / `summary=` dispatch is no longer inside a function body"]
    body, index = site
    violations: list[str] = []
    bypass = _return_callee(node.body)
    if bypass != V1_BYPASS_CALLEE:
        violations.append(
            "the `v1` / `summary=` arm routes to "
            f"`{bypass or '<no return>'}` instead of `{V1_BYPASS_CALLEE}` — "
            "`TORTOISE_EXTRACTOR=v1` / `summary=` would reach the S4-RUNNING path"
        )
    fallthrough = _return_callee(body[index + 1:])
    if fallthrough != V2_FALLTHROUGH_CALLEE:
        violations.append(
            "the fall-through after the `v1` / `summary=` arm routes to "
            f"`{fallthrough or '<no return>'}` instead of "
            f"`{V2_FALLTHROUGH_CALLEE}` — the S4-RUNNING v2 route moved"
        )
    return violations


def _mentions_name(call: ast.Call, name: str) -> bool:
    """`name` appears as a bare Name anywhere in `call`'s arguments."""
    for arg in [*call.args, *(kw.value for kw in call.keywords)]:
        if any(
            isinstance(sub, ast.Name) and sub.id == name
            for sub in ast.walk(arg)
        ):
            return True
    return False


def _union_consumption_violations(source: str) -> list[str]:
    """Every reason the E4 union is NOT the value that reaches the output.

    The #8601 review proved that pinning NAME IDENTITY (`merge_target` is not
    None and `result["embed_list"]` reads a name with the same spelling) stays
    green when the merged value is REBOUND on the way out:

      * `complete_list = s4` — replace-not-merge; S2 items S4 omitted are
        dropped, exactly the state the assertion's own message names;
      * `complete_list = embed_list` — union computed, then discarded;
      * `s4 = {}` after the call — the S4 half of the union emptied.

    The property pinned here is the EDGE, not the spelling: between the merge
    and the output, every rebinding of the merge target must be a CALL that
    still reads the merge value (the VET / entity-resolution / chain passes do
    exactly that), and the S4 result must not be rebound before the merge.
    """
    scan = _scan(source)
    if scan.merge_call is None:
        return ["`merge_embed_lists` is no longer called"]
    if not scan.merge_target or scan.result_embed_list_lineno is None:
        return [
            "the union or the `result[\"embed_list\"]` output assignment is gone"
        ]
    merge_line = scan.merge_call.lineno
    output_line = scan.result_embed_list_lineno
    s4_line = scan.s4_call.lineno if scan.s4_call is not None else -1
    violations: list[str] = []
    for name, lineno, value in scan.assignment_sites:
        if name == scan.s4_result and s4_line < lineno < merge_line:
            violations.append(
                f"`{name}` is REBOUND at line {lineno} after `run_s4` and before "
                "the merge, so the union consumes a value that is not S4's output"
            )
        if name != scan.merge_target or not (merge_line < lineno < output_line):
            continue
        if not isinstance(value, ast.Call) or not _mentions_name(
            value, scan.merge_target
        ):
            violations.append(
                f"`{scan.merge_target}` is rebound at line {lineno} to "
                f"`{ast.unparse(value)}` before the output — the merged list does "
                "not reach `result[\"embed_list\"]`"
            )
    return violations


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() is gone from {EXTRACTOR.name} — re-verify the claim")


class _StageScan(ast.NodeVisitor):
    """Collect the S4 call, the guards enclosing it, and the E4 union.

    Guards are tracked as a stack of `If` test expressions so the assertion is
    structural: a future `if <flag> and story:` reds because a flag test appears
    in the stack, not because a line of text moved. The `orelse` branch is
    visited with the test NEGATED, because a call there is gated by `not story`
    (the #8601 review proved that visiting it with the positive test made
    `if story: … else: s4 = run_s4(…)` report clean).
    """

    def __init__(self) -> None:
        self.guard_stack: list[ast.expr] = []
        self.s4_call: ast.Call | None = None
        self.s4_result: str | None = None
        self.s4_guards: list[ast.expr] = []
        self.merge_call: ast.Call | None = None
        self.merge_target: str | None = None
        self.result_embed_list: ast.expr | None = None
        self.result_embed_list_lineno: int | None = None
        self.assignments: dict[str, ast.expr] = {}
        #: Every `name = value` site, so a REBINDING between two anchors is
        #: visible. The #8601 review proved name identity is not consumption:
        #: `complete_list = s4` after the merge kept both spellings equal while
        #: the union went dead, so the check must see the rebinding itself.
        self.assignment_sites: list[tuple[str, int, ast.expr]] = []

    def visit_If(self, node: ast.If) -> None:
        self.guard_stack.append(node.test)
        for child in node.body:
            self.visit(child)
        self.guard_stack.pop()
        # The `orelse` branch is gated by the NEGATION of the test: a call in
        # `else:` sits under `if (not story):`, not under `if story:`. Pushing
        # the positive test again would report it as the documented gate.
        self.guard_stack.append(ast.UnaryOp(op=ast.Not(), operand=node.test))
        for child in node.orelse:
            self.visit(child)
        self.guard_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        for target in targets:
            self.assignments[target] = node.value
            self.assignment_sites.append((target, node.lineno, node.value))
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "result"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "embed_list"
            ):
                self.result_embed_list = node.value
                self.result_embed_list_lineno = node.lineno
        if isinstance(node.value, ast.Call):
            called = _call_name(node.value.func)
            if called == "run_s4" and self.s4_call is None:
                self.s4_call = node.value
                self.s4_guards = list(self.guard_stack)
                self.s4_result = targets[0] if targets else None
            elif called == "merge_embed_lists" and self.merge_call is None:
                self.merge_call = node.value
                self.merge_target = targets[0] if targets else None
        self.generic_visit(node)


def _scan(source: str | None = None) -> _StageScan:
    text = EXTRACTOR.read_text(encoding="utf-8") if source is None else source
    scan = _StageScan()
    scan.visit(_function(ast.parse(text), "extract_session_v2"))
    return scan


def _s4_call_gate_violations(source: str) -> list[str]:
    """Every reason `run_s4`'s call site is not gated ONLY by `if story:`.

    POLARITY, not another deny-list (the #8601 review proved the four-spelling
    deny-list let a NESTED gate through): a bare `story` name survived in the
    guard stack and `s4_delta` was not one of the spellings, so
    ``if s4_delta: s4 = run_s4(...)`` kept the guard green while making
    ``TORTOISE_S4_DELTA=0`` a real S4 off switch — the destructive misreading
    this file exists to prevent. The property is an ALLOW-LIST: EVERY `If`
    test enclosing the call must be exactly the bare ``Name('story')``.
    """
    scan = _scan(source)
    if scan.s4_call is None:
        return ["`run_s4` is no longer called from `extract_session_v2`"]
    violations: list[str] = []
    if not any(
        isinstance(test, ast.Name) and test.id == "story"
        for test in scan.s4_guards
    ):
        violations.append(
            "the S4 call is no longer guarded by the bare `if story:` — "
            f"enclosing guards: {[ast.unparse(t) for t in scan.s4_guards]}"
        )
    for test in scan.s4_guards:
        if not (isinstance(test, ast.Name) and test.id == "story"):
            violations.append(
                "the S4 call is gated by "
                f"`{ast.unparse(test)}`, not the bare `if story:` the record "
                "states — a non-`story` gate is an S4 OFF SWITCH"
            )
    return violations


def _own_exits(body: list[ast.stmt]) -> list[ast.stmt]:
    """Every `return`/`raise` in THIS statement list, ignoring nested defs.

    A `return` inside a nested function is not an exit from the entry point.
    """
    exits: list[ast.stmt] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Return, ast.Raise)):
            exits.append(node)
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list):
                exits.extend(_own_exits(block))
        for case in getattr(node, "cases", []) or []:
            exits.extend(_own_exits(case.body))
        for handler in getattr(node, "handlers", []) or []:
            exits.extend(_own_exits(handler.body))
    return exits


def _own_ifs(body: list[ast.stmt]) -> list[ast.If]:
    """Every `If` in THIS statement list, ignoring nested defs."""
    found: list[ast.If] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.If):
            found.append(node)
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list):
                found.extend(_own_ifs(block))
        for case in getattr(node, "cases", []) or []:
            found.extend(_own_ifs(case.body))
        for handler in getattr(node, "handlers", []) or []:
            found.extend(_own_ifs(handler.body))
    return found


def _empty_conversation_returns(fn: ast.FunctionDef) -> list[ast.stmt]:
    """The `if not edus: return {...}` early exit — the marker's bypass (iii)."""
    returns: list[ast.stmt] = []
    for node in _own_ifs(fn.body):
        if ast.unparse(node.test) == "not edus":
            returns.extend(n for n in node.body if isinstance(n, ast.Return))
    return returns


def _undocumented_exits(source: str) -> list[ast.stmt]:
    """Early `return`/`raise` in `extract_session_v2` BEFORE the S4 call IN
    SOURCE ORDER that the record does not enumerate — an undocumented exit on
    the entry→call path.

    Documented here: (iii) the empty-conversation return. Documents elsewhere
    and therefore not an early exit of THIS function: (i)/(ii) the `sdk.py` v1
    dispatch, which never enters `extract_session_v2`.

    Deliberately NOT a reachability analysis (the #8601 review proved the older
    wording over-claimed it). An exit on the `not story` side of the call's own
    `if story:` guard is textually AFTER the call and so is never returned here;
    it is not a missed S4 bypass either, because S4 is already gated off on that
    path — the record's 'runs whenever a story exists' claim is about a truthy
    `story`, and the call's guard already encodes it.
    """
    fn = _function(ast.parse(source), "extract_session_v2")
    s4 = _scan(source).s4_call
    if s4 is None:
        return []
    documented = {node.lineno for node in _empty_conversation_returns(fn)}
    return [
        node for node in _own_exits(fn.body)
        if node.lineno < s4.lineno and node.lineno not in documented
    ]


# ── the divergence is RECORDED ─────────────────────────────────────────────


def test_the_design_still_states_its_position_and_the_marker_naming_8500():
    """The marker must not be tidied away: it is what stops *"there is no S4"*
    from being read as an instruction about the shipped tree.

    Resolved through `_flat_doc()` — the #8601 review proved a RAW-text check
    here reds on a pure reflow that changed nothing.
    """
    flat = _flat_doc()
    assert DESIGN_POSITION in flat, (
        "the v4 design's position on S4 changed — if S4 was re-admitted to the "
        "step list, this issue's whole premise moved and the guard must be "
        "re-derived, not deleted."
    )
    marker = _marker_block()
    assert MARKER_NEVER_DELETE in marker, (
        "the S4 drift marker's instruction not to delete `run_s4` on the "
        "document's authority alone is gone — a lane reading only this document "
        "would then delete a live stage."
    )
    assert MARKER_TRACKER_REF in marker, (
        "the marker must name the issue that owns the S4 clause (#8500) in its "
        "own tracker line, so the decision has a home a reader can reach. "
        "Pinned verbatim: a plain `#8500` membership test is satisfied by the "
        "record-anchor mention in the same block, not by the tracker."
    )
    assert GUARD_NAME in marker, (
        f"the marker must name its guard ({GUARD_NAME}) — an unguarded marker "
        "is the stale claim this file exists to prevent."
    )


def test_the_module_docstring_carries_the_record_the_call_site_points_at():
    """Before #8500's fix the call site said *"see the G5 record in the module
    docstring"* and there was no such record — a dangling pointer. Non-vacuity:
    the anchor string was not in the module before this change.

    Scoped to the DOCSTRING, not the file: the call site, `ci-surfaces.yml`
    and the design doc all name the module docstring as the record's home, and
    a file-wide read cannot see the record move out of it while the call site's
    pointer is left dangling (#8601 review, P3).
    """
    doc = _module_docstring(EXTRACTOR.read_text(encoding="utf-8"))
    assert RECORD_ANCHOR in doc, (
        f"the S4 call site names the record {RECORD_ANCHOR!r} in the module "
        "docstring; it is no longer there, so the pointer is dangling again."
    )
    assert "v4 MIGRATION DELIVERABLE" in doc, (
        "the record must state the removal-ordering obligation: `run_s4` is a "
        "v4 migration deliverable, not a cleanup."
    )
    assert "OPEN QUESTION" in doc, (
        "the record must leave #8500's precedence question OPEN — which side is "
        "authoritative until the migration lands is the OWNER's ruling, not an "
        "agent-authored claim (the #8601 review flagged the previous 'shipped "
        "code is authoritative' sentence as answering a question #8500 reserves "
        "to the owner)."
    )
    assert "never enter this extractor at all" in doc, (
        "the record must SCOPE its bypass enumeration to the route that reaches "
        "`extract_session_v2`, and name the other `capture_session` lanes that "
        "never enter it (keyless/no-provider, already-captured replay, "
        "`TORTOISE_SESSION_EXTRACTOR=m2`). The unqualified 'three paths' claim "
        "was contradicted by those lanes, which the #8601 review proved."
    )


def test_the_docstring_scope_check_reds_when_the_record_leaves_the_docstring():
    """#8601 review finding (P3) — the check read the WHOLE FILE, so the record
    could be cut out of the module docstring and re-appended verbatim as a `#`
    comment at end of file while every assertion stayed green, leaving the S4
    call site's *"in the module docstring"* pointer dangling again.

    The mutation demotes the module docstring to a plain assignment: the record
    text stays in the FILE but leaves the DOCSTRING. A file-wide read cannot
    see that; the docstring-scoped read must.
    """
    source = EXTRACTOR.read_text(encoding="utf-8")
    mutated = source.replace('"""', '_relocated_record = """', 1)
    assert mutated != source, "the module docstring moved — re-point this fixture"
    assert RECORD_ANCHOR in mutated, (
        "the mutation must leave the record's text in the FILE, or it exercises "
        "deletion rather than relocation — not the vacuity this closes"
    )
    assert RECORD_ANCHOR in _module_docstring(source), (
        "the unmutated tree no longer carries the record in the docstring, so "
        "this fixture cannot attribute the detection to the mutation"
    )
    scoped = _module_docstring(mutated)
    assert RECORD_ANCHOR not in scoped, (
        "a file-wide membership check cannot see the record LEAVE the module "
        "docstring; the docstring-scoped read must, or the S4 call site can "
        "point at a record that is not there while the guard reports clean"
    )
    assert "v4 MIGRATION DELIVERABLE" not in scoped
    assert "OPEN QUESTION" not in scoped


# ── the shipped-code facts ─────────────────────────────────────────────────


def test_run_s4_is_called_under_if_story_with_no_flag_gate():
    """The record states S4 has NO off switch; this is an ALLOW-LIST over the
    guards enclosing the call, not a deny-list of flag spellings (#8601 review).

    A deny-list is satisfied by any spelling it does not enumerate — the review
    proved a nested `if s4_delta:` gate made `TORTOISE_S4_DELTA=0` disable S4
    while every check here stayed green.
    """
    source = EXTRACTOR.read_text(encoding="utf-8")
    assert _scan(source).s4_call is not None, (
        "`run_s4` is no longer called from `extract_session_v2`. If the stage "
        "was genuinely migrated out, DELETE this guard together with the marker "
        "and the docstring record — do not let them describe a stage that is gone."
    )
    violations = _s4_call_gate_violations(source)
    assert not violations, (
        "an S4 call gate was added. The marker states S4 has NO flag gate and "
        "that the only skips are side effects of other paths; either the code "
        "or the marker is now false. " + "; ".join(violations)
    )


def test_the_s4_flag_selects_the_output_contract_not_whether_s4_runs():
    """`TORTOISE_S4_DELTA=0` is the misreading this pins: it is NOT an off switch."""
    tree = ast.parse(EXTRACTOR.read_text(encoding="utf-8"))
    helper = _function(tree, "_s4_delta_enabled")
    returns = [n for n in ast.walk(helper) if isinstance(n, ast.Return)]
    assert returns, "`_s4_delta_enabled()` no longer returns — the toggle moved"
    assert any(_env_flag_default_true(n.value) for n in returns if n.value is not None), (
        "`_s4_delta_enabled()` must resolve `TORTOISE_S4_DELTA` through "
        "`env_flag(..., True)` — the DEFAULT-ON delta contract, never a call "
        f"gate. It returns {[ast.unparse(n.value) for n in returns if n.value]}."
    )

    scan = _scan()
    assert scan.s4_call is not None
    kwargs = {k.arg: k.value for k in scan.s4_call.keywords if k.arg is not None}
    assert "delta" in kwargs, (
        "`run_s4` no longer receives the `delta` contract kwarg — the marker's "
        "claim that the flag selects the OUTPUT contract has become false."
    )
    assert isinstance(kwargs["delta"], ast.Name) and kwargs["delta"].id == "s4_delta", (
        "`delta=` must be the per-session value read once from "
        f"`_s4_delta_enabled()`; it is {ast.unparse(kwargs['delta'])}."
    )
    resolved = scan.assignments.get("s4_delta")
    assert isinstance(resolved, ast.Call) and _call_name(resolved.func) == "_s4_delta_enabled", (
        "`s4_delta` must be assigned from `_s4_delta_enabled()` — the marker "
        "states the toggle is read ONCE per session and threaded into the call."
    )


def test_the_e4_union_is_consumed_not_discarded():
    """The union must be what REACHES the output, not merely assigned to a name
    with a matching spelling.

    The #8601 review proved name identity is not consumption: `complete_list =
    s4` (replace-not-merge) and `complete_list = embed_list` (union discarded)
    inserted after the merge kept every assertion here green. The
    consumption-edge check below closes that, and
    `test_the_union_consumption_check_sees_a_rebinding` pins its non-vacuity.
    """
    scan = _scan()
    assert scan.merge_call is not None, (
        "`merge_embed_lists` is no longer called — the E4 'merges, not replaces' "
        "property the design DEPENDS ON (§15's `§1 v2 → v3 → v4` row) is gone."
    )
    assert scan.s4_result and len(scan.merge_call.args) == 2, (
        "`merge_embed_lists` must take the S2 list and the S4 result: "
        f"{ast.unparse(scan.merge_call)}"
    )
    second = scan.merge_call.args[1]
    assert isinstance(second, ast.Name) and second.id == scan.s4_result, (
        "the union no longer merges the S4 result into the S2 base — the marker "
        f"claims S4's output IS consumed. Call: {ast.unparse(scan.merge_call)}"
    )
    assert scan.merge_target is not None, (
        "the union's result is DISCARDED (a bare expression statement): a merge "
        "that is computed and thrown away loses the capability just as surely "
        "as deleting it."
    )
    output = scan.result_embed_list
    assert isinstance(output, ast.Name) and output.id == scan.merge_target, (
        "the union is ASSIGNED but never reaches the function's output — "
        "`result[\"embed_list\"]` must carry the merged list, or S2 items S4 "
        "omitted are silently dropped (the E4 capability loss the marker claims "
        "is guarded). Output is "
        f"{ast.unparse(output) if output is not None else '<no result[embed_list] assignment>'} "
        f"but the merge target is {scan.merge_target!r}."
    )
    violations = _union_consumption_violations(EXTRACTOR.read_text(encoding="utf-8"))
    assert not violations, (
        "the merged list is REBOUND on its way to the output — a "
        "replace-not-merge (`complete_list = s4`) drops every S2 item S4 "
        "omitted, which is the E4 capability loss this test exists to catch; "
        "a name-spelling check cannot see it (the #8601 review proved that). "
        + "; ".join(violations)
    )


def test_the_three_side_effect_bypass_paths_exist():
    """The record states these are the ONLY skips ON THE ROUTE THAT REACHES
    `extract_session_v2`, and that none is an S4 switch. If one disappears the
    record's enumeration is wrong; the NEGATIVE half — a fourth, undocumented
    bypass — is pinned by `test_no_undocumented_early_exit_skips_s4`.

    The `sdk.py` arm is pinned as a ROUTING, not merely as a predicate: the
    #8601 review swapped the two callees and every test stayed green while
    bypass paths (i)/(ii) went false.
    """
    routing = _v1_dispatch_routing_violations(SDK.read_text(encoding="utf-8"))
    assert not routing, (
        "the `v1` / `summary=` dispatch (the record's bypass paths (i) and (ii)) "
        "is gone, was rewritten, or no longer BYPASSES S4 — the predicate alone "
        "is not the bypass, the routing must reach the S4-less path. "
        + "; ".join(routing)
    )

    scan = _scan()
    assert scan.s4_call is not None
    fn = _function(
        ast.parse(EXTRACTOR.read_text(encoding="utf-8")), "extract_session_v2")
    empty_guards = [
        node for node in _own_ifs(fn.body)
        if ast.unparse(node.test) == "not edus"
    ]
    assert empty_guards, (
        "the empty-conversation early return (the record's bypass path (iii)) is "
        "gone — S4 can now be reached with no content, contradicting the record."
    )
    assert min(node.lineno for node in empty_guards) < scan.s4_call.lineno, (
        "the empty-conversation return must run BEFORE the S4 call, or it is not "
        "a bypass of S4 at all."
    )
    returns = [n for n in empty_guards[0].body if isinstance(n, ast.Return)]
    assert returns, (
        "the empty-conversation guard no longer returns — path (iii) is gone"
    )
    degraded = _dict_constant(returns[0].value, "degraded")
    assert isinstance(degraded, ast.Constant) and degraded.value is True, (
        "the empty-conversation early return must report the VALUE `degraded: "
        "True` — the marker's path (iii) describes that degraded return, not a "
        "silent `{}` and not `degraded: False` (a substring check cannot tell "
        "them apart, which is what the #8601 review proved). Found "
        f"{ast.unparse(degraded) if degraded is not None else '<no degraded key>'}"
    )


def test_no_undocumented_early_exit_skips_s4():
    """NEGATIVE half of the bypass enumeration (#8601 review finding 2).

    The positive checks above only prove the three NAMED paths still exist; a
    FOURTH exit on the entry→call path was invisible to all of them. This
    asserts that the only way `extract_session_v2` leaves BEFORE the S4 call in
    SOURCE ORDER is the enumerated empty-conversation return. It is not a
    reachability analysis — an exit on the `not story` side of the call's own
    guard is after the call and is not an S4 bypass (S4 is already gated off
    there); see `_undocumented_exits`.
    """
    undocumented = _undocumented_exits(EXTRACTOR.read_text(encoding="utf-8"))
    assert not undocumented, (
        "a new early `return`/`raise` now skips S4 without being enumerated in "
        "the G5 record and the design marker. Either route it through a "
        "documented path or extend the marker's bypass list (and this guard): "
        f"{[(n.lineno, ast.unparse(n)) for n in undocumented]}"
    )


def test_the_undocumented_exit_scan_is_not_vacuous():
    """Non-vacuity for the negative half: the fourth-bypass shape the #8601
    review proved landed invisibly must red `_undocumented_exits`."""
    source = EXTRACTOR.read_text(encoding="utf-8")
    anchor = (
        "    # ── S4: review gaps → complete embed list (E4: merges-not-replaces) ───"
    )
    assert anchor in source, (
        "the S4 section anchor moved — re-point this non-vacuity fixture"
    )
    injected = (
        "    if story and not _s4_wanted(story):\n"
        "        return {'session_id': session_id, 'story_arc': story,"
        " 'embed_list': embed_list}\n"
    )
    mutated = source.replace(anchor, injected + anchor, 1)
    assert mutated != source, "the injection did not apply — fixture is stale"
    baseline = _undocumented_exits(source)
    assert not baseline, (
        "the unmutated tree already has an undocumented exit — this fixture "
        "cannot attribute the detection to the injection"
    )
    undocumented = _undocumented_exits(mutated)
    assert undocumented, (
        "the scan failed to see a new early return before the S4 call, so the "
        "negative half of the guard is vacuous"
    )
    assert any(
        "'story_arc': story" in ast.unparse(node) for node in undocumented
    ), f"the injected early return was not the one detected: {undocumented}"


def test_the_design_position_check_survives_an_honest_reflow():
    """#8601 review finding 1 — a literal check against the RAW doc reds when the
    position sentence is re-wrapped (a no-op layout change). `_flatten` must make
    the check layout-insensitive; the sample is the exact reflow the review used.
    """
    reflowed = (
        "**⚠️ There is no S4 —\n"
        "> do not restore one.**\n"
        "\n"
        "> tracked at **`#8500`**, pinned by\n"
        "> `test_extractor_v4_s4_stage_claims.py`.\n"
    )
    assert DESIGN_POSITION not in reflowed, (
        "the fixture must actually be reflowed, or this proves nothing about "
        "layout-insensitivity"
    )
    flat = _flatten(reflowed)
    assert DESIGN_POSITION in flat
    assert "#8500" in flat
    assert GUARD_NAME in flat


def test_the_v1_dispatch_match_is_order_insensitive():
    """#8601 review finding 1, second instance — operand order in the `sdk.py`
    dispatch is behaviour-preserving; matching positionally red the guard."""
    canonical = ast.parse('if extractor == "v1" or summary is not None:\n    pass\n')
    swapped = ast.parse('if summary is not None or extractor == "v1":\n    pass\n')
    assert _is_v1_dispatch(canonical.body[0].test) is True
    assert _is_v1_dispatch(swapped.body[0].test) is True, (
        "the logically-equivalent operand swap must not red the guard"
    )
    unrelated = ast.parse("if other or extra:\n    pass\n")
    assert _is_v1_dispatch(unrelated.body[0].test) is False


def test_the_v1_dispatch_match_is_operator_sensitive():
    """#8601 review finding — the label asserted semantics the code never checked.

    An operator-blind `_comparison_kind` accepted these semantics-inverting
    rewrites, so the marker's bypass paths (i)/(ii) could go false while the
    guard reported clean. Each of these must red.
    """
    inverted = (
        'extractor != "v1" or summary is not None',
        'extractor == "v1" or summary is None',
        'extractor is not "v1" or summary is not None',
    )
    for source in inverted:
        test = ast.parse(f"if {source}:\n    pass\n").body[0].test
        assert _is_v1_dispatch(test) is False, (
            "a comparison operator was ignored — the dispatch match accepted the "
            f"semantics-inverting form: {source}"
        )


def test_the_v1_dispatch_routing_is_pinned():
    """#8601 review finding (P2) — the guard pinned the PREDICATE, not the bypass.

    Swapping the two callees at `sdk.py:6934` (`_commit_session_v1` <->
    `_commit_session_v2`) sends `TORTOISE_EXTRACTOR=v1` and `summary=` into the
    S4-RUNNING pipeline while the predicate is untouched — falsifying the
    record's bypass paths (i)/(ii) behind a green guard. The ROUTING, not just
    the condition, must be pinned.
    """

    def synthetic(bypass: str, fallthrough: str) -> str:
        return (
            "class S:\n"
            "    def commit_session(self, extractor, summary, conversation):\n"
            '        extractor = (extractor or "v2").lower()\n'
            '        if extractor == "v1" or summary is not None:\n'
            f"            return self.{bypass}(conversation)\n"
            f"        return self.{fallthrough}(conversation)\n"
        )

    clean = synthetic(V1_BYPASS_CALLEE, V2_FALLTHROUGH_CALLEE)
    assert _v1_dispatch_routing_violations(clean) == [], (
        "the unmutated dispatch shape must stay clean — or this fixture proves "
        "nothing"
    )
    swapped = synthetic(V2_FALLTHROUGH_CALLEE, V1_BYPASS_CALLEE)
    violations = _v1_dispatch_routing_violations(swapped)
    assert violations, (
        "a routing inversion (the v1 arm landing on the S4-RUNNING path, and "
        "the `v1` / `summary=` bypass becoming false) must red the guard — "
        "pinning only the predicate let it through"
    )
    assert any("S4-RUNNING" in v for v in violations), (
        f"the routing inversion was not the violation reported: {violations}"
    )

    gone = (
        "class S:\n"
        "    def commit_session(self, extractor, conversation):\n"
        "        return self._commit_session_v2(conversation)\n"
    )
    assert _v1_dispatch_routing_violations(gone), (
        "a missing `v1` dispatch must red — the record's bypass list would be "
        "false"
    )


def test_the_marker_tracker_check_is_scoped_to_the_marker():
    """#8601 review finding (P3) — `"#8500" in doc` was vacuous, and the
    scoping the earlier fixture claimed to prove was itself unfalsifiable.

    The string occurs at several places in the document, so rewriting only the
    marker's OWN tracker line kept the document-wide check green. But the
    earlier fixture rewrote the pinned literal GLOBALLY, so its negative
    assertion (`MARKER_TRACKER_REF not in _marker_block(mutated)`) was
    satisfied by ANY read of ANY scope — replacing `_marker_block()` with a
    whole-document read still passed. The synthetic two-blockquote source below
    is the positive half: it carries a SECOND blockquote with a DIFFERENT
    tracker reference, which only a genuinely scoped read excludes.
    """
    source = DOC.read_text(encoding="utf-8")
    mutated = source.replace(MARKER_TRACKER_REF, "tracked at **`#9999`**", 1)
    assert mutated != source, "the marker's tracker line moved — re-point this fixture"
    assert "#8500" in _flatten(mutated), (
        "the mutation must leave `#8500` elsewhere in the document, or it would "
        "not exercise the vacuity this test exists to close"
    )
    assert MARKER_TRACKER_REF not in _marker_block(mutated), (
        "the scoped marker read still found the tracker reference after it was "
        "rewritten — the check is still vacuous"
    )

    two_blocks = (
        f"> {MARKER_START} — the S4 divergence\n"
        f"> {MARKER_TRACKER_REF}\n"
        "\n"
        "> a later, unrelated blockquote still says #8500 and `#9999`\n"
    )
    scoped = _marker_block(two_blocks)
    assert MARKER_TRACKER_REF in scoped, (
        "the scoped read must return the marker block itself"
    )
    assert "#9999" not in scoped, (
        "the marker read is still document-wide: it picked up a SECOND "
        "blockquote, so the scoping the guard claims is not what the check does"
    )


def test_the_undocumented_exit_scan_sees_a_match_case_body():
    """#8601 review finding (P3) — `_own_exits` did not recurse into `match`.

    A `return` inside an `ast.Match` case is an exit from `extract_session_v2`,
    but the scan could not see it, so a fourth bypass of that shape would land
    invisibly while the design marker claims it is caught.
    """
    source = EXTRACTOR.read_text(encoding="utf-8")
    anchor = (
        "    # ── S4: review gaps → complete embed list (E4: merges-not-replaces) ───"
    )
    assert anchor in source, "the S4 section anchor moved — re-point this fixture"
    injected = (
        "    match story:\n"
        "        case None:\n"
        "            return {'session_id': session_id}\n"
    )
    mutated = source.replace(anchor, injected + anchor, 1)
    assert mutated != source, "the injection did not apply — fixture is stale"
    assert not _undocumented_exits(source), (
        "the unmutated tree already has an undocumented exit — this fixture "
        "cannot attribute the detection to the injection"
    )
    undocumented = _undocumented_exits(mutated)
    assert undocumented, (
        "the exit scan missed a `return` inside a `match` case body, so a fourth "
        "bypass of this shape would land invisibly"
    )


def test_the_union_consumption_check_reds_when_the_merge_does_not_reach_output():
    """#8601 review finding — `merge_target is not None` stayed green when the
    merge was renamed away or the output bypassed it. Both mutations must be
    visible to the scan, or the marker's `union IS consumed` claim is unguarded."""
    source = EXTRACTOR.read_text(encoding="utf-8")
    output_bypassed = source.replace(
        '    result["embed_list"] = complete_list\n',
        '    result["embed_list"] = embed_list\n',
        1,
    )
    assert output_bypassed != source, "fixture is stale (output assignment moved)"
    scan = _scan(output_bypassed)
    assert scan.merge_target == "complete_list"
    assert not (
        isinstance(scan.result_embed_list, ast.Name)
        and scan.result_embed_list.id == scan.merge_target
    ), "the scan cannot see that the merged list is no longer the output"

    merge_discarded = source.replace(
        "                complete_list = merge_embed_lists(embed_list, s4)\n",
        "                _discarded_union = merge_embed_lists(embed_list, s4)\n",
        1,
    )
    assert merge_discarded != source, "fixture is stale (merge assignment moved)"
    scan2 = _scan(merge_discarded)
    assert scan2.merge_target == "_discarded_union"
    assert not (
        isinstance(scan2.result_embed_list, ast.Name)
        and scan2.result_embed_list.id == scan2.merge_target
    ), "a merge renamed away from the output must red the consumption check"


def test_the_union_consumption_check_sees_a_rebinding():
    """#8601 review finding (P2) — name identity is not consumption.

    The review's three mutations each left every test in this file green: a
    replace-not-merge (`complete_list = s4`), a discard (`complete_list =
    embed_list`), and S4's output cleared after the call (`s4 = {}`). The first
    is precisely the state `test_the_e4_union_is_consumed_not_discarded`'s own
    failure message names (S2 items S4 omitted silently dropped). All three
    must red the consumption-edge check.
    """
    source = EXTRACTOR.read_text(encoding="utf-8")
    merge = "                complete_list = merge_embed_lists(embed_list, s4)\n"
    assert merge in source, "the merge assignment moved — re-point this fixture"
    assert _union_consumption_violations(source) == [], (
        "the unmutated tree already reds the consumption check — this fixture "
        "cannot attribute the detection to the mutation"
    )
    for label, insertion in (
        ("replace-not-merge", "                complete_list = s4\n"),
        ("discard-the-union", "                complete_list = embed_list\n"),
    ):
        mutated = source.replace(merge, merge + insertion, 1)
        assert mutated != source, f"the {label} injection did not apply"
        violations = _union_consumption_violations(mutated)
        assert violations, (
            f"the consumption check cannot see a `{label}` rebinding, so the "
            "record's `union IS consumed` claim is unguarded"
        )
        assert any("complete_list" in v for v in violations), (
            f"the `{label}` rebinding was not the violation reported: {violations}"
        )

    s4_call_end = "                        core_only=classify_later, delta=s4_delta)\n"
    assert s4_call_end in source, "the S4 call tail moved — re-point this fixture"
    cleared = source.replace(s4_call_end, s4_call_end + "            s4 = {}\n", 1)
    assert cleared != source, "the s4-clearing injection did not apply"
    violations = _union_consumption_violations(cleared)
    assert violations, (
        "the consumption check cannot see S4's output cleared after the call, "
        "so the S4 half of the union can be emptied while the guard is green"
    )
    assert any("REBOUND" in v for v in violations), (
        f"the S4 rebinding was not the violation reported: {violations}"
    )


def test_the_degraded_check_pins_the_value_not_the_word():
    """#8601 review finding — `assert "degraded" in body` passed for
    `degraded: False`; the VALUE must be pinned."""
    false_arm = ast.parse(
        '{"search": {"mode": "m", "degraded": False}}', mode="eval"
    ).body
    true_arm = ast.parse(
        '{"search": {"mode": "m", "degraded": True}}', mode="eval"
    ).body
    assert _dict_constant(false_arm, "degraded").value is False
    assert _dict_constant(true_arm, "degraded").value is True


def test_a_nested_flag_gate_reds_the_off_switch_check():
    """#8601 review finding — the four-spelling deny-list let a NESTED gate
    through, and a nested gate is behaviourally an off switch.

    The review's proof, reproduced as a fixture: wrapping the live call in
    ``if s4_delta:`` made `TORTOISE_S4_DELTA=0` disable S4, while the bare
    `story` name in the guard stack satisfied the old check and `s4_delta` was
    not one of the four denied spellings. The allow-list must red here.
    """
    source = EXTRACTOR.read_text(encoding="utf-8")
    call = (
        "            s4 = run_s4(model, story, search, embed_list, master,\n"
        "                        session_date=session_date, edus=edus,\n"
        "                        stats=stage_stats,\n"
        "                        core_only=classify_later, delta=s4_delta)"
    )
    assert call in source, "the S4 call text moved — re-point this fixture"
    assert _s4_call_gate_violations(source) == [], (
        "the unmutated tree already reds the off-switch check — this fixture "
        "cannot attribute the detection to the injection"
    )
    nested = (
        "            s4 = None\n"
        "            if s4_delta:\n"
        + "\n".join("    " + line for line in call.splitlines())
        + "\n"
    )
    mutated = source.replace(call, nested, 1)
    assert mutated != source, "the injection did not apply — fixture is stale"
    violations = _s4_call_gate_violations(mutated)
    assert violations, (
        "the off-switch check cannot see a nested `if s4_delta:` gate, so "
        "`TORTOISE_S4_DELTA=0` would disable S4 while the guard reported clean"
    )
    assert any("s4_delta" in v for v in violations), (
        f"the nested gate was not the violation reported: {violations}"
    )


def test_an_else_branch_call_reds_the_off_switch_check():
    """#8601 review finding — `visit_If` kept the POSITIVE test on the stack for
    BOTH branches, so a call in the `else:` of `if story:` — gated by
    `not story` — read as the documented `if story:` gate and stayed clean.

    That rewrite runs S4 exactly when there is NO story, falsifying the record's
    "runs whenever a story exists" claim while the guard reported green.
    """
    positive = (
        "def extract_session_v2(story, embed_list):\n"
        "    if story:\n"
        "        s4 = run_s4(story, embed_list)\n"
        "    else:\n"
        "        s4 = {}\n"
        "    return s4\n"
    )
    assert _s4_call_gate_violations(positive) == [], (
        "the positive `if story:` shape must stay clean — or this fixture "
        "proves nothing"
    )
    negated = (
        "def extract_session_v2(story, embed_list):\n"
        "    if story:\n"
        "        s4 = {}\n"
        "    else:\n"
        "        s4 = run_s4(story, embed_list)\n"
        "    return s4\n"
    )
    violations = _s4_call_gate_violations(negated)
    assert violations, (
        "a call in the `else:` of `if story:` is gated by `not story`, but the "
        "scan reports it as the documented gate — S4 could run only when there "
        "is no story while the off-switch check stayed green"
    )


# ── the fold's successor is ABSENT ─────────────────────────────────────────
#
# #8500's question (3) is the migration ORDER — *"S4 cannot leave the step list
# before its successor lands"* — and question (1) is whether the §4.0 purpose
# test's fold is correct. Both turn on a fact the record above did not state:
# has the successor the design promises actually LANDED?
#
# It has not, and the trap is that the fold's two stages DO exist in the shipped
# tree (`_vet_gate.apply_vet` and `kind_classifier.classify_items`), so "the
# fold's stages are present" reads as "the successor landed" — and a lane then
# deletes `run_s4` believing gap-filling moved. What decides it is the REMEDY
# half: neither stage can ADD an item a coverage check found missing. These
# tests pin that half, so the pre-condition for removal stays measurable
# instead of inferable from a stage list.

#: §4.0's fold: the clause that gives S2.2 VET the coverage question S4's
#: removal relies on. If the design re-assigns that purpose, the record's
#: basis moved and this must be re-derived (not deleted).
FOLD_STATEMENT = "did we account for everything the narrative said?"
#: The clause that closes the "S2.2/S2.3 exist ⇒ successor landed" misreading.
SUCCESSOR_ABSENT = "THE SUCCESSOR HAS NOT LANDED"
#: The SCOPE the successor conclusion must carry. `run_s4` is itself a shipped
#: stage that emits exactly the candidates a coverage check found missing, so an
#: unqualified "no shipped stage can emit …" universal denies the capability its
#: own next clause says is lost by deleting the stage (#8955 review).
CONCLUSION_SCOPE = "folds S4's purpose into"

#: `check_batch`'s DOCUMENTED signals (its docstring: coverage + abstraction).
#: An ALLOW-LIST, because the successor's first appearance would be a new,
#: ACTIONABLE key here (``missing``, ``additions``, ``remedy`` …). A deny-list
#: is satisfied by every spelling it does not name.
BATCH_SIGNAL_ALLOW = frozenset({
    "narrative_statements", "candidates", "by_section",
    "coverage_ratio", "coverage_suspect",
})
#: `classify_items`' documented return keys. A key carrying ITEMS would make
#: classify a candidate producer — the successor under another name.
CLASSIFY_RETURN_ALLOW = frozenset({"assignments", "stats", "warnings"})


def _signal_violations(keys) -> list[str]:
    """New/missing `check_batch` signals — empty means report-only, as documented."""
    unknown = sorted(set(keys) - BATCH_SIGNAL_ALLOW)
    missing = sorted(BATCH_SIGNAL_ALLOW - set(keys))
    problems: list[str] = []
    if unknown:
        problems.append(
            "`check_batch` gained signal(s) " + ", ".join(unknown) + " — if one "
            "of them is ACTIONABLE (a remedy, not a report), the coverage "
            "detector became a gap-filler and the G5 record must stop saying "
            "no shipped stage can add a missing candidate")
    if missing:
        problems.append(
            "`check_batch` lost signal(s) " + ", ".join(missing) + " — the "
            "record's 'reported, never acted on' coverage claim is about these")
    return problems


def _classify_return_violations(keys) -> list[str]:
    unknown = sorted(set(keys) - CLASSIFY_RETURN_ALLOW)
    return ([
        "`classify_items` now returns " + ", ".join(unknown) + " — a return key "
        "carrying ITEMS makes S2.3 a candidate producer, i.e. the gap-filling "
        "successor the ordering obligation is waiting for. Reconcile the G5 "
        "record and this guard (they currently state classify LABELS only)"
    ] if unknown else [])


def _dict_return_key_sets(source: str, func_name: str) -> list[set[str]]:
    """The constant key-sets of `func_name`'s dict-valued `return` statements.

    Resolved by `ast.walk`, not by the top-level `_function` helper: the
    classifier's entry point is a METHOD on `KindClassifier`, not a module-level
    function. Takes SOURCE so a mutation can be judged without touching disk.
    """
    tree = ast.parse(source)
    fns = [node for node in ast.walk(tree)
           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
           and node.name == func_name]
    assert fns, f"{func_name}() is gone — re-verify the claim"
    out: list[set[str]] = []
    for fn in fns:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
                continue
            keys = {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            out.append(keys)
    return out


def _return_dict_key_sets(path: Path, func_name: str) -> list[set[str]]:
    return _dict_return_key_sets(path.read_text(encoding="utf-8"), func_name)


def _successor_clause_violations(doc: str) -> list[str]:
    """The record's successor clause — the measured half, not the obligation.

    Every literal below pins a claim the #8955 review found overstated, so the
    fix cannot silently revert to the broader sentence: the flags that gate the
    two stages, the COVERAGE half that only detects, the Rule-4 restore that
    makes an unqualified "only REMOVES" false, and the SCOPE that makes the
    conclusion's universal true of the FOLD rather than of every shipped stage.
    """
    problems: list[str] = []
    if SUCCESSOR_ABSENT not in doc:
        problems.append(
            "the G5 record no longer states that the successor has NOT landed; "
            "the ordering obligation then reads as satisfiable by S2.2/S2.3 "
            "merely existing, and `run_s4` is deleted believing gap-filling moved")
    for required, why in (
        ("is NOT the test that it has",
         "the record must name the trap: the fold's stages EXIST, which is not "
         "the successor landing"),
        ("apply_vet",
         "the record must name the gate's symbol that cannot add (removal-only)"),
        ("classify_items",
         "the record must name the classifier's symbol that labels only"),
        ("TORTOISE_VET",
         "both stages are wired in behind flags that default OFF, so the "
         "record must not present them as running shipped stages"),
        ("TORTOISE_CLASSIFY_LATER",
         "the classify pass is flag-gated too; naming one flag reads as a "
         "single off-switch for both"),
        ("coverage signal",
         "VET acts (it records DISCARD); only its COVERAGE half reports and "
         "never acts, and an unqualified 'VET only detects' contradicts the "
         "record's own removal sentence"),
        ("Rule 4",
         "`apply_vet` has one append path — the Layer-1 entity restore — so an "
         "unqualified 'only removes' is falsifiable in a single grep and must "
         "name its exception"),
        ("can ADD",
         "the record must state the removal pre-condition as a stage that can "
         "ADD a candidate — not a stage that reviews or labels one"),
        (CONCLUSION_SCOPE,
         "the conclusion must be scoped to the FOLD (and its successor), not "
         "to every shipped stage: `run_s4` IS a shipped stage that emits the "
         "candidates a coverage check found missing, so the unqualified "
         "universal denies the capability its own dash clause says is lost"),
    ):
        if required not in doc:
            problems.append(f"the record lost {required!r}: {why}")
    return problems


def _marker_successor_violations(marker: str) -> list[str]:
    problems: list[str] = []
    for required, why in (
        ("NOT the test that the successor exists",
         "the marker must warn that the fold's stages existing is not the "
         "successor — this is the misreading that deletes a live stage"),
        ("TORTOISE_VET",
         "the marker must say the stages are flag-gated OFF, not running"),
        ("coverage signal",
         "only VET's coverage half detects; the stage itself discards"),
        ("Rule 4",
         "the marker's 'only removes' must name the Layer-1 restore exception"),
        ("can ADD",
         "the marker must state the removal pre-condition as a stage that can "
         "ADD a candidate"),
        (CONCLUSION_SCOPE,
         "the marker's conclusion must be scoped to the FOLD (and its "
         "successor): `run_s4` is a shipped stage that emits the missing "
         "candidates, so 'no shipped stage' is false and self-refuting"),
    ):
        if required not in marker:
            problems.append(f"the drift marker lost {required!r}: {why}")
    return problems


#: §4.0's purpose table — the record's STATED basis, and the only scope that
#: makes the fold check non-vacuous. `FOLD_STATEMENT` occurs TWICE in the
#: document (this table's S2.2 VET row and §S2.2 Level 2's coverage bullet), so a
#: document-wide read stays green when THIS row loses the clause — the same
#: defect `_marker_block`'s docstring records the #8601 review proving.
PURPOSE_ANCHOR = "### 4.0 What each step is FOR"
PURPOSE_END = "### 4.1 The step list"
#: The row §4.0 gives S4's purpose to. S2.3 CLASSIFY has NO fold clause there:
#: its row assigns the SURVIVING candidate its pack kind, so the S2.3 half of
#: the record is named by the record itself, never by this table.
VET_ROW_ANCHOR = "| **S2.2 VET** |"


def _purpose_table(text: str | None = None) -> str:
    """§4.0's purpose-test table alone — sliced, not the whole document."""
    source = DOC.read_text(encoding="utf-8") if text is None else text
    start = source.find(PURPOSE_ANCHOR)
    assert start != -1, (
        f"§4.0 is gone ({PURPOSE_ANCHOR!r} not found) — the fold the G5 record "
        "derives its basis from has moved, so re-derive the record (#8500)")
    end = source.find(PURPOSE_END, start)
    assert end != -1, (
        f"§4.0's end marker {PURPOSE_END!r} is gone — the table can no longer "
        "be delimited, and an undelimited slice is a document-wide read")
    return source[start:end]


def _purpose_row(row_anchor: str, text: str | None = None) -> str:
    """ONE row of §4.0's table, the way `_value_gate_row` scopes its row."""
    table = _purpose_table(text)
    start = table.find(row_anchor)
    assert start != -1, (
        f"§4.0 no longer has a {row_anchor!r} row — that row is the record's "
        "basis for S4's removal, so re-derive the record (#8500)")
    return table[start:table.find("\n", start)]


def _fold_violations(text: str | None = None) -> list[str]:
    """The fold, read from the §4.0 ROW rather than from the document."""
    row = _purpose_row(VET_ROW_ANCHOR, text)
    return [] if FOLD_STATEMENT in row else [
        "§4.0's S2.2 VET row no longer gives the step the coverage question "
        f"({FOLD_STATEMENT!r}) — the G5 record's basis moved, so re-derive the "
        "record rather than keeping a claim about a fold that no longer exists "
        "(#8500). The phrase still occurs at §S2.2 Level 2, which is why a "
        "document-wide read could not see this."
    ]


def test_the_design_gives_s2_2_vet_the_coverage_question():
    """The record's read of §4.0 is the basis on which S4's removal is even
    contemplated. If the design re-assigns that purpose, the record is stale in
    a way its own ordering obligation hides (the obligation is about a successor
    the design no longer names).

    Scoped to the `| **S2.2 VET** |` row, and named for what it checks: §4.0
    carries NO S4↔S2.3 statement (its S2.3 row assigns a SURVIVING candidate its
    pack kind), so the S2.3 half of the record is the record's own reading, not
    a §4.0 fact this test could pin.
    """
    problems = _fold_violations()
    assert not problems, "; ".join(problems)


def test_the_fold_read_is_scoped_to_the_purpose_row():
    """Non-vacuity for the fold read, on the REAL document.

    `FOLD_STATEMENT` has two independent homes (§4.0's row and §S2.2 Level 2), so
    the document-wide check this replaces stayed GREEN on exactly the mutation
    its own failure message advertises — dropping the clause from §4.0 while
    §S2.2 keeps it. That is the vacuous-prose-check shape the #8601 review
    already proved once in this file.
    """
    doc = DOC.read_text(encoding="utf-8")
    assert not _fold_violations(doc), (
        "the unmutated §4.0 row must be clean, or the mutation below cannot be "
        "attributed to it")
    assert doc.count(FOLD_STATEMENT) >= 2, (
        "the phrase must still have more than one home for the scoping to be "
        "the thing under test; if it lives only in §4.0, say so and this "
        "mutation becomes redundant")
    row = _purpose_row(VET_ROW_ANCHOR, doc)
    mutated = doc.replace(row, row.replace(FOLD_STATEMENT, "accounted for"), 1)
    assert mutated != doc, "the mutation did not apply — re-point it at the row"
    assert _fold_violations(mutated), (
        "deleting the coverage clause from §4.0's S2.2 VET row MUST red this "
        "check — a document-wide read stayed green here because §S2.2's "
        "Level-2 bullet still carries the phrase")


def test_the_marker_states_no_delivery_state_even_after_a_reflow():
    """The marker lives in a DESIGN document, and
    `tests/test_extractor_v4_citation_rot.py`'s delivery-state arm
    (`_delivery_hits`) is applied PER LINE.

    A reflow is a meaning-preserving edit this document set treats as normal, so
    a passage that stays clean only because a sentence is split across two
    source lines is one editorial pass away from a reported 'delivery state'
    whose message names the changelog policy rather than anything that changed.
    The passage therefore must not depend on a line break to stay out of that
    arm — which is what the pre-fix wording (`… the successor has` / `landed.`)
    did.
    """
    from tests.test_extractor_v4_citation_rot import _delivery_hits

    assert not _delivery_hits(_marker_block()), (
        "the drift marker states a delivery state — a design document states "
        "the design, and the passage reads clean or red independently of how it "
        "is wrapped")
    # Non-vacuity: the probe CAN fire, and this is the shape the pre-fix wording
    # produced once joined onto one line.
    assert _delivery_hits("is NOT the test that the successor has landed"), (
        "the reflow probe cannot fire at all, so the assertion above is vacuous")


def test_vet_coverage_is_a_detector_and_cannot_gap_fill():
    """S2.2 VET is the design's named successor half. Measured: its COVERAGE
    signal REPORTS, and only fires in the degenerate zero-candidate case — it
    cannot emit the item a gap is missing. Pinned behaviourally, because the
    record's claim is about what the gate can DO, not about a docstring.

    Extends `tests/test_vet_gate_5005.py::test_coverage_suspect_only_on_the_
    unambiguous_loss`, which owns the narrative-less direction; the genuinely
    new half here is the PARTIAL-coverage case (it must NOT fire) and the
    `points`/`events` sections.
    """
    from tortoise import vet_gate as vg

    empty = {"entities": [], "points": [], "events": [], "operators": []}
    batch = vg.check_batch(empty, "One thing. Two things.")
    assert not _signal_violations(batch), "; ".join(_signal_violations(batch))
    assert batch["coverage_suspect"] is True, (
        "the degenerate case (prose in, nothing out) must still be reported — "
        "if even this stopped firing, VET would detect nothing at all and the "
        "record's 'only DETECTS' claim would overstate it"
    )

    # Non-vacuity: partial coverage does NOT fire — `coverage_suspect` is a
    # definite-loss signal, not a threshold. A `True` here would mean the gate
    # claims a loss on every imperfect batch (a different, unmeasured claim).
    partial = vg.check_batch(
        {"entities": [{"name": "the plan doc", "kind": "core:other"}],
         "points": [], "events": [], "operators": []},
        "One thing. Two things.")
    assert partial["coverage_suspect"] is False, (
        "coverage_suspect must stay the unambiguous zero-candidate signal; a "
        "partial batch firing it would be an unmeasured threshold claim"
    )
    assert partial["coverage_ratio"] is not None, (
        "the coverage RATIO is the reported signal — it must be present for the "
        "hand review the detector exists to serve"
    )


def test_apply_vet_cannot_emit_an_item():
    """The removal-only half, pinned as a SET relation over a prior-LESS list so
    it cannot pass vacuously: with no `prior` handed in, `apply_vet` may drop
    items, never introduce one.

    Scoped to `prior=None` deliberately — that is the only configuration in
    which the subset relation holds. The function's ONE append path is Rule 4's
    Layer-1 entity RESTORE, which needs a `prior` removal pool; the second half
    below pins what that restore may add (an entity an EARLIER pass discarded,
    never a candidate the narrative yielded). The record's conclusion survives
    because of exactly that: a restore cannot fire from a coverage gap, since it
    needs a surviving candidate to reference the name.

    Extends `tests/test_vet_gate_5005.py::test_apply_vet_removes_only_explicit_
    discard` (nothing dropped AND nothing added for KEEP/NOOP/MERGE/no-decision)
    and `::test_cross_pass_reference_restores_entity_and_payload_validates` (the
    restore leg, owned there) rather than re-asserting either.
    """
    from tortoise import vet_gate as vg

    def texts(el):
        return [(section, vg._item_text(section, item))
                for section, _i, item in vg._iter_items(el)]

    el = {
        "entities": [{"name": "plan doc", "kind": "core:other"},
                     {"name": "the roadmap", "kind": "core:other"}],
        "points": [{"content": "the plan doc is stale", "pointKind": "statement",
                    "about_entities": ["plan doc"]}],
        "events": [], "operators": [],
    }
    before = texts(el)
    kept, _w = vg.apply_vet(el, {})
    assert set(texts(kept)) <= set(before), (
        "`apply_vet` introduced an item with no verdict and NO `prior` — the "
        "gate is not removal-only any more, so it CAN gap-fill and the G5 "
        "record's successor claim is false"
    )
    assert set(texts(kept)) == set(before), (
        "with NO decisions nothing may be dropped (fail-open) — otherwise the "
        "subset assertion above is not about additions"
    )

    # Non-vacuity: a real DISCARD must SHRINK the set, or the subset relation
    # could hold because apply_vet does nothing at all.
    discards = {vg._item_id("points", i, item): {"outcome": vg.DISCARD}
                for i, item in enumerate(el["points"])}
    shrunk, _w2 = vg.apply_vet(el, discards)
    assert len(texts(shrunk)) < len(before), (
        "the DISCARD fixture must remove something, or this test proves nothing "
        "about apply_vet's removal half"
    )

    # The ONE permitted addition: Rule 4's restore from `prior`. It re-emits an
    # entity an EARLIER pass discarded, because a SURVIVING candidate references
    # it — which is why the record's "no shipped stage can emit a candidate a
    # coverage check found missing" survives: the restored item comes from the
    # removal pool, not from the narrative.
    restored_item = {"name": "pytest", "kind": "core:tool"}
    refs = {
        "entities": [{"name": "the plan doc", "kind": "core:other"}],
        "points": [{"content": "pytest is slow", "pointKind": "statement",
                    "about_entities": ["pytest"]}],
        "events": [], "operators": [],
    }
    refs_before = texts(refs)
    with_prior, prior_warnings = vg.apply_vet(
        refs, {}, prior={"removed_entities": {"pytest": restored_item}})
    added = set(texts(with_prior)) - set(refs_before)
    assert added == {("entities", vg._item_text("entities", restored_item))}, (
        "a prior-carrying apply_vet may add EXACTLY the restored item and "
        f"nothing else — it added {sorted(added)}. An addition sourced from "
        "anywhere but `prior` is the gap-filling successor the G5 record says "
        "does not exist"
    )
    assert any("restored" in w for w in prior_warnings), (
        "the restore must be REPORTED, or it is an invisible emission")

    # …and it cannot conjure the name: with a pool that removed something ELSE
    # the same reference adds nothing, because the restore reads `prior`'s
    # entity entries, never the narrative. (The pool is non-empty so the
    # early-return branch is not what is being measured.)
    empty_pool, empty_warnings = vg.apply_vet(
        refs, {},
        prior={"removed_texts": {"some earlier discarded point"},
               "removed_entities": {}})
    assert set(texts(empty_pool)) <= set(refs_before), (
        "the restore sourced an item from a pool that never held it — it is "
        "reading something other than the earlier pass's removals, i.e. it "
        "can gap-fill from the narrative")
    assert any("cannot be restored" in w for w in empty_warnings), (
        "an unrestorable reference must be reported rather than silently "
        "dropped or silently invented")


def test_classify_labels_and_never_adds_items():
    """S2.3 CLASSIFY is the fold's other named half. Measured: it returns KIND
    ASSIGNMENTS for ids that already exist, and the write-back edits items in
    place — it produces no candidate. That is what makes 'the fold's stages are
    present' different from 'the successor landed'.
    """
    from tortoise import extractor_v2 as v2

    key_sets = _return_dict_key_sets(
        ROOT / "tortoise" / "kind_classifier.py", "classify_items")
    assert key_sets, "`classify_items` has no dict-valued return to inspect"
    for keys in key_sets:
        problems = _classify_return_violations(keys)
        assert not problems, "; ".join(problems)

    el = {"entities": [{"name": "plan doc", "kind": "core:plan"}],
          "points": [], "events": [], "operators": []}
    item = el["entities"][0]
    key = f"{v2._classify_item_id('entities', item)}#0"
    out = v2._apply_classify_kinds(el, {key: {"kind": "core:other"}})
    assert out is el, (
        "`_apply_classify_kinds` must write back IN PLACE — a copy could "
        "silently diverge from the list the pipeline merges"
    )
    lengths = {s: len(out.get(s) or []) for s in ("entities", "points", "events")}
    assert lengths == {"entities": 1, "points": 0, "events": 0}, (
        "the classify write-back changed the item COUNT — it is labelling, not "
        f"candidate production: {lengths}"
    )
    assert item["kind"] == "core:other", (
        "the non-vacuity half: the assignment must actually LAND, or the "
        "count assertion above holds because the function does nothing"
    )


def test_the_record_states_the_successor_has_not_landed():
    doc = _module_docstring(EXTRACTOR.read_text(encoding="utf-8"))
    problems = _successor_clause_violations(doc)
    assert not problems, "; ".join(problems)


def test_the_marker_warns_that_the_fold_stages_existing_is_not_the_successor():
    marker = _marker_block()
    problems = _marker_successor_violations(marker)
    assert not problems, "; ".join(problems)


def test_the_successor_checks_red_on_the_mutations_they_exist_to_catch():
    """Non-vacuity for the two prose checks: each shape they exist to prevent
    must red them, on the REAL text with only that shape removed."""
    source = EXTRACTOR.read_text(encoding="utf-8")
    doc = _module_docstring(source)
    assert not _successor_clause_violations(doc), (
        "the unmutated record must be clean, or the mutations below cannot be "
        "attributed to them"
    )
    # The whole clause cut, and — separately — the trap sentence reworded away
    # while the heading survives (a heading-only membership check stays green).
    cut = source.replace(SUCCESSOR_ABSENT, "")
    assert _successor_clause_violations(_module_docstring(cut)), (
        "removing the successor clause must red the check — otherwise the "
        "ordering obligation can be left reading as satisfiable again"
    )
    reworded = source.replace("is NOT the test that it has",
                              "is the evidence that it has")
    assert _successor_clause_violations(_module_docstring(reworded)), (
        "inverting the trap sentence (existing stages ARE the evidence the "
        "successor landed) must red the check"
    )
    # The SCOPE reverting to the unqualified universal. `run_s4` is a shipped
    # stage that emits exactly the candidates a coverage check found missing, so
    # "no shipped stage" makes the record deny (and then, after the dash, assert
    # the loss of) the same capability — falsifiable in one grep, which is the
    # shape `_successor_clause_violations` exists to catch.
    unqualified = source.replace(
        f"no stage the design {CONCLUSION_SCOPE}", "no shipped stage")
    assert unqualified != source, (
        "the scope mutation did not apply — re-point it at the record's "
        "conclusion sentence")
    assert _successor_clause_violations(_module_docstring(unqualified)), (
        "an unqualified 'no shipped stage' universal MUST red this check: "
        "`run_s4` is the shipped gap-filler, so the unscoped sentence "
        "contradicts its own next clause"
    )

    marker = _marker_block()
    assert not _marker_successor_violations(marker), (
        "the unmutated marker must be clean"
    )
    doc_source = DOC.read_text(encoding="utf-8")
    stripped = doc_source.replace("can ADD", "can review")
    assert _marker_successor_violations(_marker_block(stripped)), (
        "the marker's removal pre-condition must be pinned: weakening 'can ADD' "
        "to 'can review' restores exactly the misreading it exists to stop"
    )
    unscoped = doc_source.replace(
        f"no stage the design {CONCLUSION_SCOPE}", "no shipped stage")
    assert unscoped != doc_source, (
        "the marker scope mutation did not apply — re-point it at the "
        "marker's conclusion sentence")
    assert _marker_successor_violations(_marker_block(unscoped)), (
        "the marker's conclusion must stay scoped to the FOLD — an unqualified "
        "'no shipped stage' is falsified by `run_s4` itself"
    )

    # The ALLOW-LIST checks — the shape a successor would FIRST appear in. Both
    # must red on it, or they are deny-lists that report clean on any spelling
    # they do not happen to name.
    assert _signal_violations(BATCH_SIGNAL_ALLOW | {"missing"}), (
        "a new coverage signal that names what is MISSING must red the allow-list"
    )
    assert _classify_return_violations(CLASSIFY_RETURN_ALLOW | {"items"}), (
        "a `classify_items` return key carrying items must red the allow-list"
    )
    # …and the AST half of the classify check, on a mutated source: adding an
    # item-carrying key to the real return statement must be seen.
    classifier_source = (ROOT / "tortoise" / "kind_classifier.py").read_text(
        encoding="utf-8")
    assert _dict_return_key_sets(classifier_source, "classify_items"), (
        "the unmutated classifier must expose a dict return, or the mutation "
        "below proves nothing"
    )
    mutated = classifier_source.replace(
        'return {"assignments": assignments,',
        'return {"items": list(items), "assignments": assignments,', 1)
    assert mutated != classifier_source, (
        "the classify return statement moved — re-point this mutation fixture"
    )
    mutated_keys = _dict_return_key_sets(mutated, "classify_items")
    assert any(_classify_return_violations(keys) for keys in mutated_keys), (
        "a classify return that also carries ITEMS must red the check — that "
        "is the gap-filling successor appearing under S2.3's name"
    )
