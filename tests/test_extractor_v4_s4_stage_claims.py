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
1. The design's position is unchanged, its marker still names `#8500`, and it
   still tells a reader not to delete `run_s4` on the document's authority
   alone — i.e. the divergence is RECORDED, not tidied away.
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
     dispatch and the empty-conversation early return before S1).

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


def _call_name(node: ast.expr) -> str:
    """The bare name of a call target (`run_s4`, `mod.fn` -> `fn`)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _flat_doc() -> str:
    """The design doc with its hard wraps and `> ` quote markers removed.

    The marker's sentences wrap across lines, so a literal phrase check against
    the raw text would red on a reflow that changed nothing — the same
    brittleness `test_extractor_v4_citation_rot.py` avoids by pinning meaning,
    not layout.
    """
    return re.sub(r"\s+", " ", DOC.read_text(encoding="utf-8").replace("\n> ", " "))


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


def _is_v1_dispatch(test: ast.expr) -> bool:
    """`extractor == "v1" or summary is not None` — the sdk v1 route."""
    if not isinstance(test, ast.BoolOp) or not isinstance(test.op, ast.Or):
        return False
    left, right = test.values[:2]
    return (
        isinstance(left, ast.Compare)
        and isinstance(left.left, ast.Name)
        and left.left.id == "extractor"
        and isinstance(left.comparators[0], ast.Constant)
        and left.comparators[0].value == "v1"
        and isinstance(right, ast.Compare)
        and isinstance(right.left, ast.Name)
        and right.left.id == "summary"
    )


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


def _scan() -> _StageScan:
    tree = ast.parse(EXTRACTOR.read_text(encoding="utf-8"))
    scan = _StageScan()
    scan.visit(_function(tree, "extract_session_v2"))
    return scan


# ── the divergence is RECORDED ─────────────────────────────────────────────


def test_the_design_still_states_its_position_and_the_marker_naming_8500():
    """The marker must not be tidied away: it is what stops *"there is no S4"*
    from being read as an instruction about the shipped tree."""
    text = DOC.read_text(encoding="utf-8")
    flat = _flat_doc()
    assert DESIGN_POSITION in text, (
        "the v4 design's position on S4 changed — if S4 was re-admitted to the "
        "step list, this issue's whole premise moved and the guard must be "
        "re-derived, not deleted."
    )
    assert MARKER_NEVER_DELETE in flat, (
        "the S4 drift marker's instruction not to delete `run_s4` on the "
        "document's authority alone is gone — a lane reading only this document "
        "would then delete a live stage."
    )
    assert "#8500" in text, (
        "the marker must name the issue that owns the S4 clause (#8500), so the "
        "decision has a home a reader can reach."
    )
    assert GUARD_NAME in flat, (
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
    If one disappears the marker's enumeration is wrong — and if a NEW one
    appears, S4 has an off switch nobody documented."""
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
    empty_guards = [
        node
        for node in ast.walk(_function(ast.parse(EXTRACTOR.read_text(encoding="utf-8")),
                                       "extract_session_v2"))
        if isinstance(node, ast.If) and ast.unparse(node.test) == "not edus"
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
