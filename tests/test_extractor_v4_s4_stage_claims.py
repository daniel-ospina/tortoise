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
   * `run_s4` is called under `if story:` and under NO flag guard;
   * `_s4_delta_enabled()` defaults True, and its value is threaded as the
     call's `delta` OUTPUT-contract kwarg — not used as a call gate;
   * `merge_embed_lists`'s union is ASSIGNED (consumed), not discarded;
   * the three side-effect bypass paths exist (the `sdk.py` v1/`summary=`
     dispatch and the empty-conversation early return before S1);
4. and — the NEGATIVE half — `extract_session_v2` has no early `return`/`raise`
   before the S4 call site other than the enumerated empty-conversation one, so
   a FOURTH bypass reds the guard instead of landing invisibly (the #8601
   review proved a new early return left the positive checks above green).

NOT promised: line numbers. The doc's own rule is that the symbol is
authoritative and the number a convenience; this file resolves symbols and
behaviour, never offsets.

Static, stdlib-only, no network / DB / graph.
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
#: tracker reference the guard must pin. Scoping matters: `#8500` (and the
#: never-delete sentence) also occur elsewhere in the document, so a
#: document-wide membership check cannot fail when THIS passage loses them.
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


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() is gone from {EXTRACTOR.name} — re-verify the claim")


class _StageScan(ast.NodeVisitor):
    """Collect the S4 call, the guards enclosing it, and the E4 union.

    Guards are tracked as a stack of `If` test expressions so the assertion is
    structural: a future `if <flag> and story:` reds because a flag test appears
    in the stack, not because a line of text moved.
    """

    def __init__(self) -> None:
        self.guard_stack: list[ast.expr] = []
        self.s4_call: ast.Call | None = None
        self.s4_result: str | None = None
        self.s4_guards: list[ast.expr] = []
        self.merge_call: ast.Call | None = None
        self.merge_target: str | None = None
        self.assignments: dict[str, ast.expr] = {}

    def visit_If(self, node: ast.If) -> None:
        self.guard_stack.append(node.test)
        self.generic_visit(node)
        self.guard_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        for target in targets:
            self.assignments[target] = node.value
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
    """Early `return`/`raise` in `extract_session_v2` BEFORE the S4 call that
    the record does not enumerate — i.e. an undocumented way to skip S4.

    Documented here: (iii) the empty-conversation return. Documents elsewhere
    and therefore not an early exit of THIS function: (i)/(ii) the `sdk.py` v1
    dispatch, which never enters `extract_session_v2`; and a falsy `story`,
    which does not leave the function at all.
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
    the anchor string was not in the module before this change."""
    text = EXTRACTOR.read_text(encoding="utf-8")
    assert RECORD_ANCHOR in text, (
        f"the S4 call site names the record {RECORD_ANCHOR!r} in the module "
        "docstring; it is no longer there, so the pointer is dangling again."
    )
    assert "v4 MIGRATION DELIVERABLE" in text, (
        "the record must state the removal-ordering obligation: `run_s4` is a "
        "v4 migration deliverable, not a cleanup."
    )


# ── the shipped-code facts ─────────────────────────────────────────────────


def test_run_s4_is_called_under_if_story_with_no_flag_gate():
    scan = _scan()
    assert scan.s4_call is not None, (
        "`run_s4` is no longer called from `extract_session_v2`. If the stage "
        "was genuinely migrated out, DELETE this guard together with the marker "
        "and the docstring record — do not let them describe a stage that is gone."
    )
    guard_text = [ast.unparse(test) for test in scan.s4_guards]
    assert any(
        isinstance(test, ast.Name) and test.id == "story" for test in scan.s4_guards
    ), (
        "the S4 call is no longer guarded by `if story:` — re-verify the marker's "
        f"'runs whenever a story exists' claim. Enclosing guards: {guard_text}"
    )
    joined = " ".join(guard_text)
    for flag_ish in ("TORTOISE_S4_DELTA", "environ", "env_flag", "_s4_delta_enabled"):
        assert flag_ish not in joined, (
            "an S4 call gate was added. The marker states S4 has NO flag gate "
            "and that the only skips are side effects of other paths; either the "
            "code or the marker is now false. "
            f"Flag-ish token {flag_ish!r} found in guards: {guard_text}"
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


def test_the_three_side_effect_bypass_paths_exist():
    """The marker states these are the ONLY skips, and that none is an S4 switch.
    If one disappears the marker's enumeration is wrong; the NEGATIVE half — a
    fourth, undocumented bypass — is pinned by
    `test_no_undocumented_early_exit_skips_s4`."""
    sdk_tree = ast.parse(SDK.read_text(encoding="utf-8"))
    dispatch = [
        node.test
        for node in ast.walk(sdk_tree)
        if isinstance(node, ast.If)
    ]
    assert any(_is_v1_dispatch(test) for test in dispatch), (
        "the `v1` / `summary=` dispatch (the marker's bypass paths (i) and (ii)) "
        "is gone or was rewritten — re-derive the marker's bypass list."
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
        "the empty-conversation early return (the marker's bypass path (iii)) is "
        "gone — S4 can now be reached with no content, contradicting the marker."
    )
    assert min(node.lineno for node in empty_guards) < scan.s4_call.lineno, (
        "the empty-conversation return must run BEFORE the S4 call, or it is not "
        "a bypass of S4 at all."
    )
    body = " ".join(ast.unparse(node) for node in empty_guards[0].body)
    assert "degraded" in body, (
        "the empty-conversation early return must report `degraded: True` — the "
        "marker's path (iii) describes that degraded return, not a silent `{}`."
    )


def test_no_undocumented_early_exit_skips_s4():
    """NEGATIVE half of the bypass enumeration (#8601 review finding 2).

    The positive checks above only prove the three NAMED paths still exist; a
    FOURTH bypass was invisible to all of them. This asserts that the only way
    `extract_session_v2` can leave BEFORE the S4 call is the enumerated
    empty-conversation return.
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


def test_the_marker_tracker_check_is_scoped_to_the_marker():
    """#8601 review finding (P3) — `"#8500" in doc` was vacuous.

    The string occurs at several places in the document, so rewriting only the
    marker's OWN tracker line kept the document-wide check green. The scoped
    `_marker_block()` read must red on exactly that mutation.
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
