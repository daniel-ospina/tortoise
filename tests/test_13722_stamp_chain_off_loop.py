"""#13722 — the capture provenance stamp chain must not run on the event loop.

WHAT THIS PINS. Issue #13722 is a private-beta launch blocker: `/health`'s
`loop_blocks` reported `max_age_s` 53.041, i.e. the single uvicorn worker's
event loop was blocked for 53 s, and the stack's leaf was the record fold
(`record_projection._ordered_column_ops` -> `record_writer.write_live_record`
-> `record_projection.replay`).

The measured cause is not a parse and not a prewarm: every live record write is
blocking Postgres round trips taken on the CALLER's thread (two tenant-prologue
statements, the cursor-presence read, the plan's statements, one commit), and the
capture handler's provenance stamp chain emits one record per stamped id. ⛔ THE
COUNT IS PER RECORD TYPE and the stamp's type is `PointRevised`, whose plan is TWO
statements (`UPDATE public.point` + the `journal_event` row) — SIX round trips,
not the SEVEN of the `PointAdded` shape, whose EXTRA plan statement is the
`node_identity` insert — the FIRST of that shape's three (identity row, point
row, `journal_event`), because `node_identity` is the composite-FK parent
(measured through `record_projection.replay_plan` at this
head: `PointRevised` -> 2, `PointAdded` -> 3). At N = 6: 53.041 s / (6 x
round-trip latency) = 1921 / 58 / 36.7 records at the 4.6 / 152.5 / 240.7 ms
latencies measured on the live box (1647 / 49.7 / 31.5 at N = 7);
`workload_in_flight` was observed at 19-32, so at the worst latency the wedge is
a few dozen records — the in-flight population's order, i.e. the stamp chains of
the captures in flight rather than a hidden blocker.

So the fix is to take that chain OFF the loop, and the pin is structural rather
than a wall-clock assertion (a timing assertion in a test rots into a pass, and
this one would also need a real Postgres). It asserts, for every
`_stamp_capture_provenance(...)` call in `hosted_api` — in CALL position, under a
bare name or as an `obj.name` attribute, since the door population resolves its
callee through the same predicate every other arm uses — and, separately, that no
STATIC reference this pass can see binds, aliases, passes or otherwise reads that
name outside a call, since a `_prov = _stamp_capture_provenance; _prov(…)` alias, a
`getattr(…)` or a `.__call__(…)` is the same call under a spelling this walk cannot
follow. ⛔ THAT
SEAL IS STATIC-ONLY, exactly as the chain-name paragraph below states: a name
assembled from an unfoldable string (`eval("".join(["_stamp_capture",
"_provenance"]))`) or a computed `getattr` is OUTSIDE this pin and needs the
run-time guard in #14528:

1. the function that contains the call (its innermost enclosing function) is
   handed to `_run_off_loop(_CAPTURE_EXECUTOR, …)` — the #10893/#14086 house
   contract: the capture pool, bounded workers, contextvars copied per attempt,
   NOT `asyncio.to_thread`'s shared default executor (see `_run_off_loop`'s own
   comment) — so the per-id record writes do not run on the loop; and
2. that submission is AWAITED, so a raise in the worker still reaches the
   submitter's own `except` (#8501's non-fatal warning arm); and
3. the containing function is a SYNC `def`, because `_run_off_loop` runs `fn`
   synchronously inside the worker (`monitoring.confine_context`) — handed an
   `async def` it returns an unawaited coroutine object and NOTHING runs: no
   record, no append, no warning, no exception, and a green pin; and
4. the containing function is referenced **only as that submission's callable**
   — any other reference (an inline `_stamp_chain()`, a
   `_CAPTURE_EXECUTOR.submit(_stamp_chain)`) is a second way to run the chain
   and reds, however many submissions also exist elsewhere in the module; and
5. every submission of that function is REACHABLE — when ALL of them sit under a
   guard whose value is STATICALLY FALSE, no submission is ever reached and the
   chain runs nowhere. A dead-guarded duplicate beside a live submission is inert
   (it runs nothing) and stays green.

   ⛔ THE CRITERION IS "always false", NOT "CPython constant-folds it", and the
   difference is deliberate: a fixer disables a leg by making the guard false, so
   `[]` / `{}` / `{*()}` are as dead as `False` even though CPython evaluates them
   at run time (`dis` folds only a literal constant such as `False` / `0` / `()`).
   Graded, i.e. decided by `_literal_truth`: falsy and empty-collection literals
   (an unpack is decided by its OPERAND, so `{*()}` is empty and `{*{1}}` is not),
   signed numeric literals (`-0.0`), `f''`, a zero-argument builtin constructor
   (`set()`, `dict()`, `list()`, …), a comparison of literals (`1 < 0`, `1 == 2`).
   For `is`/`is not` identity, graded only where the PROGRAM decides it: a
   singleton operand (`None`/`True`/`False`), or two operands that cannot be the
   same object (`1 is 2`, `(1, 2) is (1, 3)`, `0.0 is 0`) — never two EQUAL
   non-singleton literals (`'ab' is 'ab'`, `() is ()`), whose identity is an
   interning detail that CPython happens to answer True and another
   implementation need not,
   `not X`, `and`/`or` over them, a conditional expression whose test — or whose
   two branches — decide it (`` X if False else Y ``, `` X if PAYLOAD else X ``,
   and an `if`-expression IN test position such as `` if (0 if True else 1): ``),
   a `match` case's `if` guard (`case _ if False:`), a `for`/`async for`
   iterable (`for _ in ():` never enters its body), a comprehension's
   generator iterables and `if` clauses (`[await … for _ in ()]`,
   `[await … for _ in _xs if False]`), and the ELSE branch of an always-TRUE
   `if`/`if`-expression (`if True: … else: await …` never runs the `else`).
   Ungraded: anything
   `_literal_truth` answers `None` for — a run-time constant (`if _FEATURE:`), an
   unpack of a run-time value, an arbitrary call, two equal non-singleton
   literals compared by `is`, and an expression-position short circuit
   (`False and await …`), where the submission sits inside the `BoolOp` rather
   than under an `if`/`while`/`for`/`if`-expression/match/comprehension guard.
   ⛔ ALSO UNGRADED, and the line is drawn at GUARDS on purpose: control flow
   that is not a boolean expression around the body — a `return`/`raise` before
   the submission, a `break`/`continue`, an `assert False` — is an
   unreachable-body question an expression grader cannot answer, so it is
   neither graded here nor implied by these arms; the run-time guard in #14528
   is its home. The same is true of a `while`'s `else` in BOTH polarities: it
   runs when the loop completes WITHOUT a `break`, so deciding it needs break
   analysis rather than an expression walk.

⛔ NESTING IS NOT THE PROPERTY, and a first cut of this file asserted it: the
chain already lives in `_report_capture`, a nested `async def`, and nesting a
coroutine changes nothing about which thread runs it (it is `await`ed on the
loop). That assertion was falsified against `origin/main` before it landed.

WHAT WOULD RED IT. Reverting the chain to the loop (the pre-#13722 shape, no
submission at all) reds 1; a submission to a different pool reds 1; dropping the
`await` (`asyncio.ensure_future`, a bare un-awaited `_run_off_loop(...)`) reds 2;
turning the containing function into a coroutine function reds 3; and any second
reference to the chain — an inline `_stamp_chain()` beside a surviving (even
unreachable) submission, an extra inline call next to the real one, or
`_CAPTURE_EXECUTOR.submit(_stamp_chain)` — reds 4.

⛔ WHY ARM 4 EXISTS AT ALL (review finding, measured on `/tmp` mutants): arms 1
and 2 asked whether an awaited submission **of that name** exists ANYWHERE, which
is satisfied by a submission on a dead branch or an unreachable path. Three
mutants that put the chain back on the loop therefore stayed GREEN — a leftover
`if … and False: await _run_off_loop(_CAPTURE_EXECUTOR, _stamp_chain)` above an
inline `_stamp_chain()`, the intended submission plus an extra inline call (run
twice, once on the loop), and `_CAPTURE_EXECUTOR.submit(_stamp_chain)` beside the
intended submission. Arm 4 closes that: it grades every REFERENCE to the chain,
not the existence of some submission.

⛔ THE ARMS ARE ASSERTED IN THAT ORDER, and the order is load-bearing. Property 3
is checked AFTER the submission and await arms — not first, and deliberately not
last (the mention count, the stray-reference and the dead-guard arms follow it) —
because the PRE-#13722 chain lives in `_report_capture`, a
nested `async def`, so a plain revert satisfies "the owner is an `async def`" and
a 3-check written first reds THERE — reporting "the chain silently does NOT run"
about a shape where it runs, inline on the loop, which is the 53 s wedge. The
submission arm must speak first for a revert; the `async def` arm must only speak
once submission and await hold (an `async def` that IS submitted and awaited still
reaches it, so nothing is lost by the reorder).

WHAT IT DELIBERATELY DOES NOT PIN. The number of round trips per record. That
number is a property of `record_projection.replay`'s plan and is measured (not
asserted) by `tests/test_9558_record_write_cost.py`'s prologue assertion and by
#13722's own before/after figures in the PR; #13722 item 2 (batching the plan's
per-record statements) would move it, and this file must not red when it does.
"""

from __future__ import annotations

import ast
import operator
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

HOSTED_API = Path(__file__).resolve().parent.parent / "tortoise" / "hosted_api.py"

#: The stamp door the chain calls (imported at module scope by `hosted_api`).
_STAMP_DOOR = "_stamp_capture_provenance"

#: The binary comparison operators a literal `Compare` can carry, as Python
#: functions — so `1 < 0` is decided by EVALUATION rather than by shape.
_COMPARE_OP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

#: Zero-argument builtin constructors whose result is always EMPTY (falsy).
_EMPTY_CONSTRUCTORS = frozenset({
    "set", "frozenset", "dict", "list", "tuple", "str", "bytes", "int",
    "float", "complex", "bool",
})

#: `ast.FormattedValue.conversion` → the function the interpreter applies for it
#: (`-1` is "no conversion", and is absent from this map on purpose).
_FSTRING_CONVERSIONS: dict[int, Callable[[str], str]] = {
    ord("s"): str,
    ord("r"): repr,
    ord("a"): ascii,
}

#: The off-loop submission helper and the pool it must be given.
_OFF_LOOP = "_run_off_loop"
_OFF_LOOP_POOL = "_CAPTURE_EXECUTOR"


def _module() -> ast.Module:
    return ast.parse(HOSTED_API.read_text(encoding="utf-8"))


def _enclosing_functions(tree: ast.Module, target: ast.AST) -> list[str]:
    """The names of the functions enclosing ``target``, OUTERMOST first.

    ⛔ THE DOCSTRING USED TO SAY "innermost first", which is the inverse of what
    this returns (review cycle 12, P2, measured): ``walk`` appends a function's
    name as it DESCENDS, so the list grows outside-in and ``<target>`` is appended
    last — `['_capture_session_impl', '_report_capture', '_stamp_chain',
    '<target>']`. The submission arm keys on ``chain[-2]``, i.e. on this being
    outermost-first, so a reader trusting the old line would invert the invariant
    the assertion rests on. ``hosted_api.py``'s own comment states the same
    convention as "innermost LAST"; this docstring now agrees with it.
    """

    chain: list[str] = []

    def walk(node: ast.AST) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain.append(child.name)
                if walk(child):
                    return True
                chain.pop()
            else:
                if child is target:
                    chain.append("<target>")
                    return True
                if walk(child):
                    return True
        return False

    walk(tree)
    return chain


def _stamp_calls(tree: ast.Module) -> list[ast.Call]:
    """Every `_stamp_capture_provenance(...)` call in the WHOLE module.

    ⛔ THE CALL SHAPE IS RESOLVED THE SAME WAY EVERY OTHER ARM RESOLVES IT — all of
    them through `_callable_name`, which `_off_loop_submissions` now asks too (it
    used to hand-roll the same two shapes, and this docstring's first cut said it
    already asked). This walk matched `ast.Name` only, while an ATTRIBUTE is
    accepted elsewhere — so a second run of the stamp door spelled
    `hosted_api._stamp_capture_provenance(…)`, or `module._stamp_capture_provenance`
    after a rename-the-import, fell out of the graded population and this pin stayed
    GREEN with that call's record writes back on the event loop (measured on a
    `/tmp` copy of `hosted_api.py`: GREEN before this change, RED after). A
    `getattr`-built callee stays outside a static walk, as the header declares.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _callable_name(node.func) == _STAMP_DOOR
    ]


def _door_references_outside_call_position(
    tree: ast.Module,
) -> dict[int, str]:
    """`line -> node type` for every `_STAMP_DOOR` reference the call resolver did NOT grade.

    ⛔ A DOOR REACHED BY A SPELLING THE CALL WALK CANNOT RESOLVE IS STILL THE DOOR,
    and this is the ONE place that decides it. `_stamp_calls` resolves a call the
    same way every other arm in this file resolves a callable — through
    `_callable_name`, i.e. a bare `name` OR an `obj.name` attribute — so the door
    may appear as the `func` of a call in EITHER spelling; every other reference
    to it (bound to a variable, passed as an argument, read as `.__call__`) is a
    second way to reach the door that no arm above can follow and reds.

    ⛔ LIFTED OUT OF THE TEST SO IT CAN BE MEASURED (the widening of
    `_stamp_calls` to attributes was pinned by NOTHING). `c3955990` made this walk
    resolve `hosted_api._stamp_capture_provenance(…)` as well as a bare name —
    closing a real fail-open, since `_off_loop_submissions` already accepted an
    attribute — but the only population it ran over was the real `hosted_api.py`,
    which is ALL bare-name call positions. Nothing in the suite could tell the two
    cuts apart: reverting to the pre-`c3955990` `isinstance(node.func, ast.Name)`
    left `84 passed`, i.e. the next lane could silently re-open it.
    `test_the_door_arm_grades_every_spelling_the_call_resolver_accepts` is the
    population the shipped module cannot supply.
    """
    door_call_nodes = {id(node.func) for node in _stamp_calls(tree)}
    return {
        node.lineno: type(node).__name__
        for node in ast.walk(tree)
        if _refers_to(node, _STAMP_DOOR) and id(node) not in door_call_nodes
    }


def _off_loop_submissions(
    tree: ast.Module,
) -> list[tuple[int, str | None, str | None, bool]]:
    """Every `_run_off_loop(...)` in the module: (line, pool, fn, awaited)."""
    awaited = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    }
    out: list[tuple[int, str | None, str | None, bool]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == _OFF_LOOP
        ):
            continue
        args = list(node.args)
        pool = args[0].id if args and isinstance(args[0], ast.Name) else None
        fn = None
        awaited_here = False
        if len(args) > 1:
            # ⛔ THE CALLABLE SPELLING IS ASKED, NOT RE-SPELLED. This expression used
            # to hand-roll `Name`/`Attribute` here while `_callable_name` spelled the
            # same two shapes for the other arms — so a shape added to one side and
            # not the other would have made a legal attribute submission read as
            # `fn=None`, i.e. "never submits to `_run_off_loop`", a false RED on a
            # correct tree. One predicate, asked by every arm.
            fn = _callable_name(args[1])
            awaited_here = id(node) in awaited
        out.append((node.lineno, pool, fn, awaited_here))
    return out


def _callable_name(node: ast.AST) -> str | None:
    """The NAME a reference node spells — a bare `name`, or an `obj.name` — else ``None``.

    ⛔ ONE PREDICATE, ASKED BY BOTH ARMS (review note N1, measured).
    `_submitted_names`' docstring claimed its shapes were "resolved through
    `_refers_to`" while it spelled `(ast.Name, ast.Attribute)` on its own: two
    spellings that agreed by inspection only, so a shape added to one side would
    stop being a reference at all on the other and the grading would go silently
    fail-open. Both arms now resolve through THIS function, and
    `test_the_arms_agree_on_what_a_reference_is` measures the agreement on
    synthetic sources rather than trusting the prose.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _refers_to(node: ast.AST, name: str) -> bool:
    """Does `node` NAME the callable `name` — as a bare name OR as an attribute?

    ⛔ THIS EXISTS SO THE TWO ARMS CANNOT DISAGREE (measured FAIL-OPEN).
    `_off_loop_submissions` resolves its callable argument through
    `_callable_name`, so it accepts an ATTRIBUTE shape — `await
    _run_off_loop(_CAPTURE_EXECUTOR, holder._stamp_chain)` — as a submission, while
    the reference walk below matched `ast.Name` only. The two then disagreed: a
    mutant that moves the chain into a class method, calls `holder._stamp_chain()`
    INLINE and also submits it by attribute satisfied arms 1-3 and produced ZERO
    references, so this pin went GREEN with the chain running on the loop
    (measured: `1 passed`, rc=0). A walk that cannot see a reference the submission
    arm accepts is not a gate.
    """
    return _callable_name(node) == name


def _submitted_names(tree: ast.Module) -> list[ast.Name | ast.Attribute]:
    """The callable nodes that ARE an awaited off-loop submission's second argument.

    i.e. the second positional argument of every awaited
    `_run_off_loop(_CAPTURE_EXECUTOR, <name>)` — a bare name or an attribute whose
    `.attr` is the name (the same shapes `_off_loop_submissions` accepts, resolved
    through `_callable_name` so the two arms cannot drift apart — the sentence here
    used to name `_refers_to`, which this function did not call).
    """
    awaited = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    }
    out: list[ast.Name | ast.Attribute] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == _OFF_LOOP
            and id(node) in awaited
        ):
            continue
        args = list(node.args)
        if (
            len(args) > 1
            and isinstance(args[0], ast.Name)
            and args[0].id == _OFF_LOOP_POOL
            and _callable_name(args[1]) is not None
        ):
            out.append(args[1])
    return out


def _off_loop_references(tree: ast.Module, name: str) -> list[tuple[int, bool]]:
    """Every REFERENCE to `name` in the module: (line, is_the_submitted_one).

    ⛔ The legal reference is the second positional argument of an awaited
    `_run_off_loop(_CAPTURE_EXECUTOR, name)` — a bare name OR an attribute that
    resolves to it (see `_refers_to`) — and nothing else.
    `name(…)` is an inline run on the caller's thread; `_CAPTURE_EXECUTOR.submit(
    name)` is the same fire-and-forget defect under another helper (it skips
    `_run_off_loop`'s `confine_context` snapshot and is never awaited). Both are
    the #13722 wedge regardless of how many submissions also exist elsewhere in
    the module, so this grades EVERY reference rather than only calls.
    """
    submitted = {id(node) for node in _submitted_names(tree)}
    return sorted(
        (node.lineno, id(node) in submitted)
        for node in ast.walk(tree)
        if _refers_to(node, name)
    )


def _string_value(node: ast.AST) -> str | None:
    """The string a CONSTANT-FOLDABLE expression evaluates to, or ``None``.

    ⛔ SPLITTING THE LITERAL IS STILL SPELLING IT (review finding, P2, measured).
    The string arm below started as an exact `ast.Constant` compare, and the name
    assembled from fragments — `locals()["_stamp_" + "chain"]()`,
    `eval(f"{'_stamp'}{'_chain'}")()` — contains a literal equal to NO whole owner,
    so it stayed invisible to both this arm and the reference walk and the chain
    ran inline on the loop with the pin GREEN (measured on `/tmp` copies: `1
    passed` for each of five spellings). Folding what the interpreter would fold
    closes the whole family: `+` of string constants, and f-strings whose parts are
    themselves literal. Anything it cannot fold returns ``None``.

    ⛔ WHAT IS GRADED, AND WHAT IS NOT — stated because two earlier drafts of this
    paragraph claimed more than the code does. MEASURED on `/tmp` copies, all of
    them spliced beside the legal submission and grep-proven applied:

      RED (every spelling this pass can constant-fold) — the whole literal, `+` of
      literals, and a literal-part f-string:
        locals().get("_stamp_chain")()
        locals()["_stamp_" + "chain"]()
        eval(f"{'_stamp'}{'_chain'}")()
        eval(f"{chr(95)}_stamp_chain")()   # the whole literal survives inside

      GREEN (no arm sees it; do not read this as coverage) — any composition by an
      operator or call this pass does not fold, or any run-time-computed part:
        eval("%s%s" % ("_stamp", "_chain"))()
        eval("".join(["_stamp", "_chain"]))()
        eval("_stamp{}".format("_chain"))()
        eval("_stamp" + chr(95) + "chain")()

    So the boundary is "everything `_string_value` folds reds", NOT "every static
    spelling reds" — Python has unbounded ways to assemble a string, and a static
    pass cannot enumerate them. Closing the class properly needs a RUN-TIME guard
    (the chain must never execute on the loop thread), which is a different
    mechanism — filed as #14528 rather than claimed here.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_value(node.left)
        right = _string_value(node.right)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue) and value.format_spec is None:
                inner = _string_value(value.value)
                if inner is None:
                    return None
                # ⛔ APPLY THE CONVERSION, DO NOT IGNORE IT (review note N2,
                # measured). `!r`/`!s`/`!a` run at RUNTIME, so folding the raw inner
                # value is wrong in BOTH directions: `f"{'_stamp_chain'!r}"`
                # evaluates to `"'_stamp_chain'"` — WITH quotes, so it does not name
                # the chain — while `f"{'_stamp_chain'!s}"` evaluates to the name
                # itself and does. Ignoring `conversion` reported RED on the first
                # (an over-block) and GREEN on the second (a fail-open of the arm
                # that exists to close fail-opens). `str`/`repr`/`ascii` are exactly
                # what the interpreter applies, computed here on the folded constant,
                # so the comparison is against the string the module actually holds.
                convert = _FSTRING_CONVERSIONS.get(value.conversion)
                parts.append(inner if convert is None else convert(inner))
            else:
                return None
        return "".join(parts)
    return None


def _identical_literals(left: object, right: object) -> bool | None:
    """Is `left is right` decided by the PROGRAM — and if so, which way?

    ⛔ `ast.literal_eval` hands back the AST's own `Constant` objects, so asking
    `left is right` of THEM measures the AST's object graph, not the program:
    `'ab' is 'ab'` is False of those objects while CPython shares one
    `LOAD_CONST` slot and RUNS the body — the false RED this arm was fixed for
    (review P2, measured).

    Decided, because no interning accident can change the answer:
      * either operand is a singleton (`None`/`True`/`False`) — their identity is
        the program's, so `None is None` is True and `None is not None` is False;
      * the operands CANNOT be the same object — their types differ (`0.0 is 0`)
        or their values differ (`1 is 2`, `(1, 2) is (1, 3)`). Identity implies
        equality, so `is` there is ALWAYS False and `is not` ALWAYS True, which
        is the "always false" criterion this file grades by. ⛔ Grading these
        `None` is a FAIL-OPEN, not caution: the pre-fix code reddened them
        correctly and a bare singleton test un-graded them (measured: `if 1 is
        2:` around the only submission → GREEN).

    Undecided (`None`): two EQUAL non-singleton literals — `'ab' is 'ab'`,
    `(1, 2) is (1, 2)`, `() is ()`. CPython merges equal constants into one code
    object so those bodies usually RUN, but the language does not require it (3.8
    emits a `SyntaxWarning` for `is` on literals for exactly this reason).
    ⛔ NOT CLOSED, and declared here so the arm is not over-read: `'ab' is not
    'ab'` is DEAD in CPython and stays GREEN, because grading it False would red a
    body that runs wherever the constants are not merged.
    """
    if left is None or left is True or left is False:
        return right is left
    if right is None or right is True or right is False:
        return False
    if type(left) is not type(right):
        return False
    # `==` is reflexive for every literal `ast.literal_eval` can build (it cannot
    # produce a NaN — `float("nan")` is a Call, not a literal), so "unequal ⇒ not
    # one object" is sound here; the reflexivity guard keeps that honest rather
    # than assumed.
    if left != left or right != right:  # pragma: no cover - unreachable for literals
        return None
    return None if left == right else False


def _bound_names(tree: ast.Module) -> frozenset[str]:
    """Every name this module BINDS — so a shadowed builtin is never a builtin.

    ⛔ A NAME IS NOT A BUILTIN (review P2, measured): `_literal_truth` answered
    "always false" for a zero-arg call of `list`/`set`/`dict`/… by NAME, so
    `def f(list): if list(): g()` — where the CALLER's `list` decides the guard —
    reported the body dead (measured: `[(3, 'list()')]`), the false-diagnosis
    class this arm exists to remove. The graded module binds none of the eleven
    names today (AST scan: zero), which is precisely why the hole would survive a
    refactor; the bound set is threaded into `_literal_truth` instead of relying on
    that census staying true.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                names.add(arg.arg)
            if args.vararg is not None:
                names.add(args.vararg.arg)
            if args.kwarg is not None:
                names.add(args.kwarg.arg)
            if not isinstance(node, ast.Lambda):
                names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            # ⛔ A `match` CAPTURE PATTERN BINDS A NAME WITH NO `ast.Name(Store)`
            # NODE (review P2, measured): `case _ as set:` left `set` unbound, so
            # `if set():` inside that case reported the body dead although the
            # matched value decides it — the same false RED this set exists for.
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        elif isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
            # PEP 695: `def f[set]():` binds `set` in the type-parameter scope.
            names.add(node.name)
    return frozenset(names)


def _literal_truth(
    node: ast.expr, bound: frozenset[str] = frozenset(),
) -> bool | None:
    """The statically decidable truth of a literal-ish expression, or None.

    ⛔ `bound` IS THE MODULE'S BOUND-NAME SET (`_bound_names`), and it exists so a
    NAME is never mistaken for a builtin (review P2, measured): `_literal_truth`
    answered False for a zero-arg call of `list`/`set`/`dict`/… by NAME, so
    `def f(list): if list(): g()` — where the CALLER's `list` decides the guard —
    reported the body dead (measured: `[(3, 'list()')]`), the false-diagnosis class
    this arm exists to remove. The default keeps every direct call (and the unit
    rows) unchanged.

    ⛔ `None` means "not statically decidable" and the caller must NOT read it as a
    polarity. ⛔ AN UNPACK IS NOT "NO ITEM": `{*()}` is empty because the OPERAND is
    empty, `{*{1}}` is NOT empty, and `{*PAYLOAD}` is undecidable — so a starred
    element (and a dict's `**`, whose key is `None`) is decided by recursing on its
    value. Deciding it from the node's shape alone got both directions wrong in
    earlier cuts: `{*()}` read as truthy (so `not {*()}` redded although its body
    runs) and `{*{1}}` read as empty (so `if not {*{1}}:` greened although nothing
    runs there).
    """
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        # ⛔ AN UNPACK CAN MAKE A COLLECTION NON-EMPTY, so `any(not Starred)` is
        # wrong in BOTH directions: `{*{1}}` is non-empty (the operand is), and
        # `{*PAYLOAD}` is not decidable at all. Decide from the operand: any
        # plain element makes it non-empty; otherwise every unpack must be
        # statically EMPTY for the whole collection to be.
        members = [(_literal_truth(e.value, bound) if isinstance(e, ast.Starred) else True)
                   for e in node.elts]
        if any(m is True for m in members):
            return True
        return False if all(m is False for m in members) else None
    if isinstance(node, ast.Dict):
        spreads = [_literal_truth(value, bound)
                   for key, value in zip(node.keys, node.values, strict=True)
                   if key is None]
        if any(key is not None for key in node.keys):
            return True
        if any(s is True for s in spreads):
            return True
        return False if all(s is False for s in spreads) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = _literal_truth(node.operand, bound)
        return None if operand is None else not operand
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        try:
            return bool(ast.literal_eval(node))
        except (ValueError, TypeError, SyntaxError):
            return None
    if isinstance(node, ast.JoinedStr):
        # `f''` has no values and is always the empty string; `f'{x}'` is not
        # decidable without evaluating `x`.
        return False if not node.values else None
    if isinstance(node, ast.Call):
        # ⛔ A ZERO-ARG BUILTIN CONSTRUCTOR IS A LITERAL IN DISGUISE: `if set():`
        # and `if dict():` are always falsy, and they are just as plausible a
        # "disable" as `if {}:` — measured GREEN before this arm existed. Any
        # argument, or any other callee, is undecidable.
        if (
            not node.args and not node.keywords
            and isinstance(node.func, ast.Name)
            and node.func.id in _EMPTY_CONSTRUCTORS
            and node.func.id not in bound
        ):
            return False
        return None
    if isinstance(node, ast.Compare):
        # A comparison of LITERALS is decided at all heads (`1 < 0` is never true),
        # even though CPython does not fold it — the criterion is "always false",
        # not "folded". `ast.literal_eval` alone does NOT accept a `Compare`, so the
        # operands are evaluated here and the chain applied explicitly; a chained
        # comparison (`0 < x < 1`) is evaluated pairwise, as Python does.
        try:
            left = ast.literal_eval(node.left)
            rights = [ast.literal_eval(c) for c in node.comparators]
        except (ValueError, TypeError, SyntaxError):
            return None
        operators = [type(op) for op in node.ops]
        comparators = [_COMPARE_OP.get(kind) for kind in operators]
        if any(fn is None for fn in comparators):
            return None
        # ⛔ `is` IS NOT DECIDED BY VALUE — see `_identical_literals` for the
        # criterion and for the two directions it has been wrong in: grading
        # equal literals from `literal_eval`'s objects false-REDded a body that
        # runs, and grading only singletons un-graded `1 is 2`, which is always
        # dead (review P2, measured both ways).
        answer = True
        for fn, kind, right in zip(comparators, operators, rights, strict=True):
            if kind is ast.Is or kind is ast.IsNot:
                identical = _identical_literals(left, right)
                if identical is None:
                    return None
                holds = identical if kind is ast.Is else not identical
            else:
                try:
                    holds = bool(fn(left, right))  # type: ignore[misc]
                except TypeError:
                    return None
            if not holds:
                answer = False
                break
            left = right
        return answer
    if isinstance(node, ast.IfExp):
        behaviour = _literal_truth(node.test, bound)
        if behaviour is not None:
            return _literal_truth(node.body if behaviour else node.orelse, bound)
        # Both branches deciding the SAME polarity decide the expression, whatever
        # the test is: `X if PAYLOAD else Y` with X and Y both false is always false.
        body, orelse = _literal_truth(node.body, bound), _literal_truth(node.orelse, bound)
        return body if body is not None and body == orelse else None
    if isinstance(node, ast.BoolOp):
        values = [_literal_truth(v, bound) for v in node.values]
        if isinstance(node.op, ast.And):
            if any(v is False for v in values):
                return False
            return True if all(v is True for v in values) else None
        if any(v is True for v in values):
            return True
        return False if all(v is False for v in values) else None
    return None


def _statically_false(
    test: ast.expr, bound: frozenset[str] = frozenset(),
) -> bool:
    """True when ``test`` is ALWAYS false — the body can never run.

    ⛔ THE CRITERION IS "always false", NOT "CPython constant-folds it". A fixer
    disables a leg by making the guard false, and `[]`/`{}`/`{*()}` are just as
    dead as `False` even though CPython evaluates them at run time (`dis` folds only
    a literal constant such as `False`/`0`/`()`); grading them is what makes the arm
    catch the disable a fixer actually writes. `_literal_truth` is the one place
    that decides, so `not X` and `X` cannot disagree.
    """
    return _literal_truth(test, bound) is False


def _dead_guards(tree: ast.Module, target: ast.AST) -> list[tuple[int, str]]:
    """`(line, source)` of every enclosing guard that is ALWAYS FALSE.

    ⛔ THE CRITERION IS "always false", NOT "CPython folds it away" (the header
    states why), and the shapes graded are the GUARDS a submission can sit under:
    an `if`/`while` test, an `if`-expression's test, a `match` case's `if` guard,
    a `for`/`async for` iterable, and a comprehension's generator iterables and
    `if` clauses. The polarity comes from `_literal_truth` and nowhere else.

    ⛔ THAT LIST IS THE WHOLE CLAIM, and it is enumerated rather than summarised
    (review P2 at `343e7ce1`, measured): this docstring used to say "every shape a
    submission can hide under" while `for _ in (): await _run_off_loop(…)` — the
    exact silent-loss state the arm exists for, in loop form — stayed GREEN. A
    claim of coverage the walk does not have is the defect class this PR removes,
    so what is graded is listed, and what is NOT graded is declared: control flow
    that is not a guard expression (a `return`/`raise` before the submission, a
    `break`/`continue`, an `assert False`) is #14528's run-time guard, not this
    arm's, and anything `_literal_truth` answers `None` for is ungraded by
    construction.

    ⛔ `orelse` is NOT descended with the guard pushed: the else-branch of a
    statically-false `if` is exactly the branch that RUNS, and a `for` whose
    iterable is empty still runs its `else` (the else runs when the loop completes
    without `break`), so a submission in either is live and must not be reported.
    """
    dead: list[tuple[int, str]] = []
    bound = _bound_names(tree)

    def walk(node: ast.AST, guards: list[tuple[int, ast.expr]]) -> None:
        if isinstance(node, ast.Match):
            # ⛔ A `case _ if False:` GUARD IS A DISABLE TOO (Python 3.12 shape, and
            # measured: the body's submission is never reached while the pin stayed
            # green). Each case's guard applies to that case's body only.
            walk(node.subject, guards)
            for case in node.cases:
                case_guards = guards
                if case.guard is not None:
                    walk(case.guard, guards)
                    case_guards = [*guards, (case.guard.lineno, case.guard)]
                for child in case.body:
                    walk(child, case_guards)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            # ⛔ A `for` HEADER IS A GUARD TOO (review P2 at `343e7ce1`, measured):
            # `for _ in ():` never enters its body, so a submission inside it is as
            # unreachable as one under `while False:` — the same silent-loss state,
            # in the loop form a fixer is most likely to write when disabling a
            # leg. The iterable is graded by `_literal_truth`, so `()`/`[]`/`set()`
            # are dead and `_xs`/`range(n)` are not.
            walk(node.iter, guards)
            for child in node.body:
                walk(child, [*guards, (node.iter.lineno, node.iter)])
            for child in node.orelse:
                walk(child, guards)
            return
        if isinstance(
            node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            # ⛔ …AND SO IS A COMPREHENSION'S CLAUSE (same finding):
            # `[await _run_off_loop(…) for _ in ()]` builds an empty list and the
            # submission never runs, and `… if False` filters every element. Each
            # generator's `iter` is evaluated at its own nesting level — the first
            # in the enclosing scope, a later one only once the earlier clauses
            # pass — so every clause is pushed as a guard for the ELEMENT (and for
            # every LATER clause), while each clause expression ITSELF is walked
            # under the guards already in force, because it runs before its own
            # condition is applied. ⛔ THE ELEMENT IS NOT THE ONLY PLACE A
            # SUBMISSION CAN SIT, and an earlier cut of this comment said it was
            # (review P2 at `f229af1`, measured): a call in a clause expression is
            # graded too, and the rows below put one in each — the nested-`iter`
            # row reds if a later clause is walked without the earlier guards, and
            # `[x for _ in () if (g(),)]` reds if clause expressions stop being
            # walked at all (the bare `if g()` row alone cannot: it expects `[]`
            # either way).
            element_guards = list(guards)
            for generator in node.generators:
                walk(generator.iter, element_guards)
                element_guards.append((generator.iter.lineno, generator.iter))
                for condition in generator.ifs:
                    walk(condition, element_guards)
                    element_guards.append((condition.lineno, condition))
            parts = (
                [node.key, node.value]
                if isinstance(node, ast.DictComp)
                else [node.elt]
            )
            for part in parts:
                walk(part, element_guards)
            return
        if isinstance(node, (ast.If, ast.IfExp)):
            # ⛔ `IfExp` is graded too: CPython folds `X if False else Y` to `Y` and
            # drops the `X` branch, so a submission in `X` is as gone as one under a
            # dead `if`.
            #
            # ⛔ AND THE OTHER BRANCH CAN BE THE DEAD ONE (review P2 at `f229af1`,
            # measured): when the test is ALWAYS TRUE the `else` is what never runs
            # — `if True: pass` / `else: await _run_off_loop(…)` is the same
            # silent-loss state with the polarity flipped, and walking `orelse`
            # unguarded reported NOTHING for it. The guard pushed there is the
            # NEGATION of the test, so the one `_statically_false` predicate decides
            # both directions instead of a second polarity rule.
            walk(node.test, guards)
            body = node.body if isinstance(node.body, list) else [node.body]
            orelse = node.orelse if isinstance(node.orelse, list) else [node.orelse]
            for child in body:
                walk(child, [*guards, (node.test.lineno, node.test)])
            else_guards = guards
            if _literal_truth(node.test, bound) is True:
                negated = ast.UnaryOp(op=ast.Not(), operand=node.test)
                ast.copy_location(negated, node.test)
                else_guards = [*guards, (node.test.lineno, negated)]
            for child in orelse:
                walk(child, else_guards)
            return
        if isinstance(node, ast.While):
            # ⛔ A `while`'s `else` is NOT graded in either polarity: it runs when
            # the loop completes WITHOUT a `break`, so deciding it needs break
            # analysis, which an expression grader cannot do. Declared ungraded in
            # the header rather than half-graded here.
            walk(node.test, guards)
            for child in node.body:
                walk(child, [*guards, (node.test.lineno, node.test)])
            for child in node.orelse:
                walk(child, guards)
            return
        if node is target:
            dead.extend(
                (line, ast.unparse(test))
                for line, test in guards
                if _statically_false(test, bound)
            )
            return
        for child in ast.iter_child_nodes(node):
            walk(child, guards)

    walk(tree, [])
    return dead


def _every_submission_dead_guarded(
    tree: ast.Module, owners: set[str],
) -> dict[str, list[tuple[int, str]]]:
    """`owner -> every enclosing dead guard`, for owners ALL of whose submissions are dead.

    ⛔ EVERY SUBMISSION, NOT ANY ONE OF THEM — and this function exists because
    that polarity was UNOBSERVABLE in the committed suite (review P1 at
    `343e7ce1`, measured). The previous cut inlined the decision as
    `if per and all(per)` inside the test, and the only population it ran over was
    the real `hosted_api.py`, which has exactly ONE submission and no enclosing
    guard: `per == [[]]`, where `any([[]])` and `all([[]])` are both `False`. So
    reverting the fix this PR's §1 is about — `all(per)` → `any(per)` — red no row,
    and the false RED it removed came straight back: a functionally CORRECT tree
    (a live awaited submission plus a leftover `if False:` duplicate beside it) was
    reported as "EVERY submission … sits under a STATICALLY-FALSE guard … the
    chain silently does NOT run". The fixture in
    `test_the_dead_guard_arm_fires_only_when_every_submission_is_dead` is the
    population the shipped module cannot supply: two submissions on one owner.

    An owner is ABSENT from the result when at least one of its submissions is
    reachable, because a dead-guarded duplicate runs nothing and is inert — it is
    not a second way to run the chain, which is what the reference arm grades.
    """
    by_owner: dict[str, list[ast.AST]] = {}
    for node in _submitted_names(tree):
        for owner in sorted(owners):
            if _refers_to(node, owner):
                by_owner.setdefault(owner, []).append(node)
    reported: dict[str, list[tuple[int, str]]] = {}
    for name, nodes in by_owner.items():
        per = [_dead_guards(tree, node) for node in nodes]
        # `per` holds one list per submission and `all(per)` is the decision: every
        # submission dead ⇒ True; one live submission (empty list) ⇒ False.
        if per and all(per):
            reported[name] = [guard for guards in per for guard in guards]
    return reported


def _unaccounted_mentions(
    tree: ast.Module, owners: set[str], source: str,
) -> dict[str, tuple[int, int]]:
    """`owner -> (textual mentions, graded AST nodes)` for every mismatch.

    ⛔ WHOLE-WORD, NOT SUBSTRING (review P2-2, measured): a substring count made
    `_stamp_chain_retry = None` — an identifier that merely EXTENDS the chain's
    name, the plausible refactor — add one occurrence with no node to grade, so a
    CORRECT tree redded with "builds the callable some way this pin cannot follow".
    `\b` counts the name where it is the name (`"_stamp_chain"` in a string, the
    `def` line, a reference) and not where it is a prefix of a longer identifier.

    Fail-closed where it is declared to be: a mention this walk cannot see as a
    node (a comment, a docstring, a string-built `locals().get("…")`) still
    counts. ⛔ NOT closed, and stated so the arm is not over-read: CONCATENATION
    (`"_stamp" + "_chain"`) has no contiguous occurrence to count.
    """
    unaccounted: dict[str, tuple[int, int]] = {}
    for owner in sorted(owners):
        graded = (
            [line for line, _submitted in _off_loop_references(tree, owner)]
            + [node.lineno for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == owner]
        )
        occurrences = len(re.findall(rf"\b{re.escape(owner)}\b", source))
        if occurrences != len(graded):
            unaccounted[owner] = (occurrences, len(graded))
    return unaccounted


def _check_stamp_chain_off_loop(
    tree: ast.Module, source: str, label: str,
) -> None:
    """`_stamp_capture_provenance` must be called from a leg submitted off-loop.

    ⛔ THIS IS THE GATE ITSELF, LIFTED OUT OF THE TEST so a fixture can supply
    the failure populations the shipped module does not contain (the same reason
    `_every_submission_dead_guarded` and
    `_door_references_outside_call_position` were lifted). `tree`/`source` are the
    module under test (`source` is the text the mention count reads) and `label`
    names it in the messages.

    ⛔ NESTING IS NOT THE PROPERTY — this gate started as "the chain must not be
    in the handler's own body", and that assertion was FALSIFIED before it was
    committed: the stamp chain already lives in `_report_capture`, a nested
    `async def` inside `_capture_session_impl`, and nesting a coroutine changes
    nothing about which thread runs it (it is `await`ed on the loop). So the
    predicate is the SUBMISSION: the innermost function that contains a stamp
    call must be handed to `_run_off_loop(_CAPTURE_EXECUTOR, …)` AND awaited.

    The pre-#13722 shape (stamp calls reached inline from the loop) has no such
    submission, so it reds here; so does a submission to a different pool
    (`asyncio.to_thread`, the shared default executor `_run_off_loop`'s own
    comment explains why capture work must not ride), and so does a
    fire-and-forget submission (`async_ensure_future`, or an un-awaited call),
    which would lose the #8501 non-fatal warning arm and the ordering the
    handler relies on.
    """
    # ⛔ MODULE-WIDE ON PURPOSE (review cycle 4, P2): the walk used to start at
    # `_capture_session_impl` while the header claimed every stamp call in
    # `hosted_api`, so a stamp appended to any OTHER function of this module was
    # never looked at and the stated population was wider than the measured one
    # (a `/tmp` mutant that added an inline stamp elsewhere stayed green). All
    # four calls live in the handler today; the scan does not assume it.
    calls = _stamp_calls(tree)
    assert calls, (
        f"{label} no longer calls {_STAMP_DOOR} — if the stamp chain "
        "moved, this pin must move with it rather than be deleted (#13722)")

    owners = set()
    for call in calls:
        chain = _enclosing_functions(tree, call)
        # ⛔ A clean failure, not an `IndexError` (review residual 2): a stamp call
        # reachable outside any function leaves no enclosing function to submit,
        # so `chain[-2]` would raise before any assertion could name the reason.
        # Still RED either way; this only makes the message the useful one.
        assert len(chain) >= 2, (
            f"{_STAMP_DOOR} is called outside any function of {label}, so "
            f"there is no enclosing function to submit to {_OFF_LOOP}({_OFF_LOOP_POOL}, …) — "
            "the chain would run on the event loop, which is the #13722 wedge. Move "
            "it into a sync `def` and submit that.")
        owners.add(chain[-2])
    submissions = _off_loop_submissions(tree)
    submitted = {(pool, fn) for _line, pool, fn, _aw in submissions}
    awaited = {(pool, fn) for _line, pool, fn, aw in submissions if aw}

    # ⛔ THE NAME MUST RESOLVE TO ONE DEFINITION (#13722 review cycle 7, P2). The
    # match above is on the function NAME, so a SECOND def with the same name — the
    # natural thing when a handler is cloned — is graded by the FIRST one's
    # submission. Measured on a `/tmp` copy: a module-level `def _stamp_chain()`
    # carrying an inline, never-submitted stamp left this pin GREEN, i.e. a copy of
    # the chain could run its per-id record writes on the loop with no gate
    # noticing. Uniqueness is what makes the name a key at all; the deeper fix
    # (keying on the definition node) needs a submission identifier the AST does not
    # carry, so this asserts the precondition instead of pretending to check it.
    def_counts = Counter(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    ambiguous = sorted(name for name in owners if def_counts[name] != 1)
    assert not ambiguous, (
        f"{_STAMP_DOOR} is called from {ambiguous}, whose name(s) resolve to "
        f"{ {name: def_counts[name] for name in ambiguous} } definitions in "
        f"{label} — a submission matches by NAME, so the other "
        "definition's submission would be accepted for this one and a second copy "
        "of the chain could run on the event loop unseen. Rename it, or re-derive "
        "this pin to key on the definition node (#13722)")

    for owner in sorted(owners):
        assert (_OFF_LOOP_POOL, owner) in submitted, (
            f"{_STAMP_DOOR} is called from {owner!r}, which no function of "
            f"{label} ever submits to {_OFF_LOOP}({_OFF_LOOP_POOL}, …) — "
            "so its per-id record writes (two prologue statements, the "
            "cursor-presence read, two plan statements and one commit = SIX "
            "blocking Postgres round trips for a `PointRevised`) run on the "
            "event loop, which is the 53 s `loop_blocks` "
            f"wedge #13722 exists to remove. Submissions found: {submissions}")
        assert (_OFF_LOOP_POOL, owner) in awaited, (
            f"{owner!r} is submitted to {_OFF_LOOP}({_OFF_LOOP_POOL}, …) but that "
            "submission is never awaited — the handler's own `except` is what "
            "keeps a stamp failure non-fatal (#8501/#1529), and a detached task "
            "moves that failure where nothing reports it (#13722)")

    # ⛔ SYNC, NOT `async def` (review finding 3) — ASSERTED AFTER SUBMISSION+AWAIT,
    # NOT FIRST, AND NOT LAST (the mention-count, stray-reference and dead-guard
    # arms follow it; review P3 at `807a1014` measured that this comment said
    # "LAST" about an arm three arms from the end). `_run_off_loop` hands
    # `fn` to `_submit_off_loop`, which runs it SYNCHRONOUSLY inside the worker
    # (`monitoring.confine_context`'s `ctx.run(fn, ...)`). Given a coroutine
    # function it returns an unawaited coroutine OBJECT — the future resolves to
    # that object, `_run_off_loop` returns it, and the chain never executes: no
    # record, no append, no warning, and every assertion above still passes, which
    # is why this arm is the ONLY thing that catches it.
    #
    # ⛔ WHY IT CANNOT GO FIRST: the pre-#13722 chain lives in `_report_capture`, a
    # nested `async def`, so checking this before the submission arm made a plain
    # REVERT red here with "the chain silently does NOT run" — the opposite of what
    # a revert does (it runs, inline on the loop, which is the 53 s wedge) — and
    # pointed a fixer at `_report_capture`'s signature instead of at the lost
    # submission. Asserting it after submission+await costs no coverage: an
    # `async def` that IS submitted and awaited passes the two arms above and still
    # reds here (mutation-verified).
    async_owners = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in owners
    }
    assert not async_owners, (
        f"the stamp chain lives in {sorted(async_owners)}, an `async def` — "
        f"{_OFF_LOOP} runs its callable SYNCHRONOUSLY in the worker, so an "
        "async one is never awaited and the chain silently does NOT run "
        "(#13722)")

    # ⛔ EVERY REFERENCE MUST BE THE SUBMITTED ONE (review finding, P2, measured
    # on `/tmp` mutants). The arms above ask whether an awaited submission OF THAT
    # NAME exists SOMEWHERE — `submitted`/`awaited` are sets of `(pool, fn)` pairs,
    # so a submission on a dead branch (`if … and False: await _run_off_loop(…)`)
    # or on an unreachable path satisfies them while the chain runs inline. Three
    # mutants stayed GREEN against the three arms above: leftover submission +
    # inline `_stamp_chain()`, the intended submission PLUS an inline call (run
    # twice, once on the loop), and `_CAPTURE_EXECUTOR.submit(_stamp_chain)`
    # beside the intended submission (off this thread, but un-awaited and outside
    # `confine_context`).
    #
    # This arm needs no dataflow: the only legal reference to an owner in this
    # module is the callable argument of `_run_off_loop(_CAPTURE_EXECUTOR, owner)`
    # — a bare `ast.Name` OR an attribute, since `_refers_to` accepts both and the
    # two arms must not disagree about what a submission is — so every other
    # reference it finds is a second way to run the chain and reds.
    # All three mutants above red here; the shipped tree has exactly one reference
    # per owner, at the submission.
    # ⛔ INDIRECTION THE AST CANNOT SEE (same review finding, second measured
    # mutant): `_again = locals().get("_stamp_chain"); _again()` and
    # `getattr(self, "_stamp_chain")` build the callable from a STRING, so no
    # `ast.Name`/`ast.Attribute` node refers to it and the arm below reported zero
    # references — measured GREEN with the chain run a second time, inline, on the
    # loop. The defence is counting: every textual occurrence of an owner's name in
    # this module must be an AST node we graded (its definition, or a reference
    # above), so a string occurrence is an UNACCOUNTED one and reds. A COMMENT or
    # docstring mentioning the name in this module reds too — fail-closed on
    # purpose: a name worth mentioning is a name worth grading, and the message
    # says so.
    # ⛔ WHOLE-WORD ON PURPOSE (review P2-2, measured): the count is `\b…\b`, not
    # a substring count, so `_stamp_chain_retry = None` beside an untouched
    # submission stays GREEN. The first cut counted substrings and redded that
    # correct tree with "the extra mention builds the callable some way this pin
    # cannot follow" — a false report on the plausible refactor, i.e. the defect
    # class this PR exists to remove. What remain covered are the shapes the
    # message names: a string-built callable and a comment.
    unaccounted = _unaccounted_mentions(
        tree, owners, source)
    assert not unaccounted, (
        f"{_STAMP_DOOR}'s chain is named {unaccounted} time(s) in "
        f"{label} (textual, graded AST node) — the extra mention builds "
        "the callable some way this pin cannot follow as a reference (a string in "
        "`locals().get(…)`/`getattr(…)`, or a comment), so a second, on-loop run of "
        "the chain would be invisible here. Call it by name at its single "
        "submission, or re-derive this pin to follow that indirection (#13722)")

    stray = {
        owner: [line for line, submitted_here in _off_loop_references(tree, owner)
                if not submitted_here]
        for owner in sorted(owners)
    }
    stray = {owner: lines for owner, lines in stray.items() if lines}

    # ⛔ THE DOOR UNDER ANOTHER NAME IS STILL THE DOOR (review finding, P2,
    # measured). `_stamp_calls` resolves its callee through `_callable_name`, so a
    # bare `ast.Name` AND an `obj.name` attribute in call position are both in the
    # graded population (`:1078-1081` says why: the attribute spelling was a
    # measured fail-open until `c3955990`). What this arm rejects is a reference to
    # the door OUTSIDE a call: `_prov = _stamp_capture_provenance; _prov(…)` inline
    # in the handler put the per-id record writes back on the loop with this pin
    # GREEN (`1 passed`, 4 of the 5 invocations seen); `getattr(mod,
    # "_stamp_capture_provenance")(…)` and `_stamp_capture_provenance.__call__(…)`
    # were green too. Every ARM above keys on the chain NAME, so a door reached
    # from a name they never see is invisible to all of them. This arm closes the
    # spelling: the door may appear ONLY as the `func` of a call — resolved, as
    # every other arm resolves a callable, through `_callable_name`, so a bare
    # `ast.Name` AND an `obj.name` attribute are both graded (an attribute call was
    # a measured fail-open until `c3955990`, and `_off_loop_submissions` had
    # accepted that shape all along). Binding it to a variable, passing it as an
    # argument, or reading `.__call__` reds. Measured cost today: zero (the four
    # calls are all bare-name call positions; the attribute spelling is pinned on a
    # synthetic source by `test_the_door_arm_grades_every_spelling_the_call_resolver_
    # accepts`, because the shipped module supplies no attribute call to grade).
    door_spellings = _door_references_outside_call_position(tree)
    # ⛔ AND NOT BY RENAMING IT AT THE IMPORT (same finding, the case the arm above
    # cannot see). `_refers_to` matches expression nodes, so `import … _stamp_
    # capture_provenance AS _prov` is invisible to it — the alias is an `ast.alias`,
    # not a Name — and a one-token edit then calls the door inline on the loop with
    # this pin GREEN (measured: `1 passed`; the plain import must stay legal, so
    # only an alias with an `asname` reds).
    aliased_imports = {
        node.lineno: node.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.alias)
        and node.name.split(".")[-1] == _STAMP_DOOR
        and node.asname
    }
    assert not aliased_imports, (
        f"{_STAMP_DOOR} is IMPORTED UNDER ANOTHER NAME at {aliased_imports} "
        "(line: asname) — every arm here keys on the spelling it can regex, so the "
        "aliased call is the same door under a name none of them watch, and its "
        "per-id record writes could run on the event loop unseen. Import it under "
        "its own name, or re-derive this pin to follow the alias (#13722; the "
        "run-time half is #14528)")
    assert not door_spellings, (
        f"{_STAMP_DOOR} is referenced OUTSIDE a call position at "
        f"{sorted(door_spellings.items())} (line: node) — an alias, a "
        "`getattr(…)`, or a `.__call__` is the SAME call under a spelling the arms "
        "above cannot follow, so its per-id record writes could run on the event "
        "loop with this pin GREEN. Call it in call position (a bare name or "
        "`obj.name` — both are graded), or re-derive "
        "this pin to follow that binding (#13722; the run-time half is #14528)")

    # ⛔ A CALLABLE BUILT FROM A STRING IS STILL A WAY TO RUN THE CHAIN (review
    # finding, P2, measured on `/tmp` mutants). The reference walk above sees
    # `ast.Name`/`ast.Attribute` NODES, so `locals().get("_stamp_chain")()` and
    # `getattr(_holder, "_stamp_chain")()` — both valid inline runs on the loop —
    # leave it reporting ONLY the legal submission, and the pin stayed GREEN
    # (`1 passed`, rc=0) for both. This arm closes the shape the walk cannot
    # follow: the owner's name must not appear as a STRING LITERAL in the module
    # at all — folded to its value first (`_string_value`), so `"_stamp_" +
    # "chain"` and a literal f-string count as the name. Fail-closed on purpose — a diagnostic string naming the chain reds
    # too, and a name worth spelling in a string is a name worth grading
    # explicitly. Measured cost today: zero (`grep` finds the name at its `def`
    # and at its submission, and nowhere else).
    # ⛔ THE FOLD AND THE WHOLE-WORD COUNT ARE COMPLEMENTARY, NOT RIVALS: the
    # count above catches a mention the walk cannot grade (including a comment),
    # and this arm catches the composed string a count of the WHOLE name cannot see
    # (`"_stamp_" + "chain"` has no contiguous occurrence). Both are kept.
    string_built = {
        owner: [node.lineno for node in ast.walk(tree)
                if _string_value(node) == owner]
        for owner in sorted(owners | {_STAMP_DOOR})
    }
    string_built = {owner: lines for owner, lines in string_built.items() if lines}
    assert not string_built, (
        f"{_STAMP_DOOR}'s chain name appears as a STRING LITERAL at "
        f"{string_built} (owner: lines) — a callable built from a string "
        "(`locals().get(…)`, `getattr(obj, …)`) is an inline run this pin's "
        "reference walk cannot see, so it could put the per-id record writes back "
        "on the event loop with this gate GREEN. Spell the call by name at its "
        "single submission, or re-derive this pin to follow that indirection "
        "(#13722)")
    assert not stray, (
        f"{_STAMP_DOOR}'s chain is referenced OUTSIDE its off-loop submission at "
        f"{stray} (owner: lines) — it runs on the caller's thread there, i.e. on "
        "the uvicorn event loop, which is the 53 s `loop_blocks` wedge #13722 "
        f"removes. A surviving {_OFF_LOOP}({_OFF_LOOP_POOL}, …) submission "
        "elsewhere does not make that path off-loop: pass the function to the "
        "awaited submission and delete every other reference (#13722)")

    # ⛔ A SUBMISSION UNDER A STATICALLY-FALSE GUARD IS NOT A SUBMISSION (review
    # finding, P2, measured on `/tmp` mutants). Arms 1-4 are all satisfied by a
    # module whose ONLY submission sits under `if True and False:` — the reference
    # is legal, awaited, on the right pool, and the owner is a sync `def` — while
    # the interpreter never reaches it: the chain never runs, so there is no
    # record, no `PointRevised`, no eventId on minted points and no non-fatal
    # warning, and every arm above stays green. That is the same silent-loss state
    # arm 3 rejects for `async def`, reached by "disabling" the offload instead of
    # mis-typing it. Run-time-constant guards stay ungraded (see the header).
    #
    # ⛔ EVERY SUBMISSION OF THE OWNER, NOT ANY ONE OF THEM (review finding, P2):
    # a first cut redded as soon as ONE submission sat under a folded-false guard,
    # so a functionally CORRECT tree — a live awaited submission plus a leftover
    # `if False:` duplicate beside it — was reported as "the chain silently does
    # NOT run", which is false there, and it sends a fixer hunting an unreachable
    # submission that does not exist. The silent-loss state is exactly "NO
    # submission of this owner is reachable", so the arm fires only when every one
    # of them is dead-guarded. A dead duplicate beside a live submission runs
    # nothing and is therefore inert — it is not a second way to run the chain,
    # which is what the reference arm grades.
    # ⛔ AND THE POLARITY IS PINNED, NOT JUST IMPLEMENTED (review P1 at
    # `343e7ce1`, measured): the decision lives in
    # `_every_submission_dead_guarded` because the shipped module — ONE unguarded
    # submission — cannot distinguish `all` from `any`; the two-submission fixture
    # below is that missing population, and `all`→`any` reds it.
    dead_guards = _every_submission_dead_guarded(tree, owners)
    assert not dead_guards, (
        f"EVERY submission of {_STAMP_DOOR}'s chain sits under a STATICALLY-FALSE "
        f"guard at {dead_guards} (owner: [(line, test)]) — each of those guards is "
        "ALWAYS FALSE (`_literal_truth` decided it), so no submission is ever "
        "reached and the chain silently does NOT run: no record, no "
        "`PointRevised`, no warning "
        "(#13722). Remove the dead guard rather than leaving the offload "
        "unreachable")


def test_every_stamp_call_runs_off_the_loop() -> None:
    """The shipped module satisfies EVERY property `_check_stamp_chain_off_loop` grades.

    ⛔ THE ARMS ARE PINNED SEPARATELY, because this module supplies none of the
    failure shapes: it has one live, awaited, unguarded submission, one reference
    and no alias, string or stray spelling. Deleting any ONE arm's `assert` from
    the checker therefore left this suite GREEN — measured, ELEVEN of eleven arms
    survived deletion (`stray`, `dead_guards`, `aliased_imports`, `ambiguous`,
    `string_built`, `door_spellings`, `submitted`, `awaited`, `async_owners`,
    `unaccounted`, and the `calls` arm that decides a stamp call exists at all),
    which is the same "the arm is pinned by
    nothing" class the review filed against `all(per)`. The checker is a FUNCTION
    so that population can be supplied by a fixture — the shipped module can only
    ever prove the arms do not false-RED — and
    `test_every_arm_reds_its_own_mutant` is where each one is proven to fire.
    """
    _check_stamp_chain_off_loop(
        _module(), HOSTED_API.read_text(encoding="utf-8"), HOSTED_API.name,
    )


#: The smallest source `_check_stamp_chain_off_loop` PASSES: one sync `def` chain
#: carrying one stamp call, one awaited submission of it on the capture pool, and
#: no other reference to the name. Every mutant below is this source with exactly
#: ONE violation spliced in, so the row that fires is the arm that owns it.
_VALID_OFF_LOOP_SOURCE = '''
def _stamp_chain(point):
    _stamp_capture_provenance(point)

async def handler():
    await _run_off_loop(_CAPTURE_EXECUTOR, _stamp_chain)
'''

#: `(id, source, the substring of the message the arm that must fire produces)`.
#: ⛔ ONE ROW PER ARM OF THE CHECKER, and the substring is the arm's own words —
#: so a row pins WHICH arm fired, not merely that something did.
_OFF_LOOP_ARM_MUTANTS: list[tuple[str, str, str]] = [
    (
        # arm 1 — there is a stamp call at all (a deleted chain is not a pass).
        "no-stamp-call-left",
        '''
def _stamp_chain(point):
    point.touch()

async def handler():
    await _run_off_loop(_CAPTURE_EXECUTOR, _stamp_chain)
''',
        "no longer calls",
    ),
    (
        # arm 2 — the call has an enclosing function to submit (`chain[-2]`).
        "stamp-call-outside-any-function",
        '''
def _stamp_chain(point):
    _stamp_capture_provenance(point)

async def handler():
    await _run_off_loop(_CAPTURE_EXECUTOR, _stamp_chain)

_stamp_capture_provenance(0)
''',
        "is called outside any function",
    ),
    (
        # arm 3 — the owner name resolves to ONE definition, or a submission
        # matches the wrong one (the `all(per)` bug in name form).
        "second-definition-of-the-chain-name",
        '''
def _stamp_chain(point):
    _stamp_capture_provenance(point)

def _stamp_chain(point):  # noqa: F811 — the fixture's whole point
    point.touch()

async def handler():
    await _run_off_loop(_CAPTURE_EXECUTOR, _stamp_chain)
''',
        "definitions in",
    ),
    (
        # arm 4 — the chain is submitted to the capture pool at all.
        "chain-never-submitted",
        '''
def _stamp_chain(point):
    _stamp_capture_provenance(point)

async def handler():
    pass
''',
        "ever submits to",
    ),
    (
        # arm 5 — the submission is awaited, so a raise still reaches the caller.
        "submission-not-awaited",
        '''
def _stamp_chain(point):
    _stamp_capture_provenance(point)

async def handler():
    _run_off_loop(_CAPTURE_EXECUTOR, _stamp_chain)
''',
        "never awaited",
    ),
    (
        # arm 6 — the chain is a SYNC def (`_run_off_loop` runs it synchronously).
        "chain-turned-into-a-coroutine-function",
        '''
async def _stamp_chain(point):
    _stamp_capture_provenance(point)

async def handler():
    await _run_off_loop(_CAPTURE_EXECUTOR, _stamp_chain)
''',
        "an `async def`",
    ),
    (
        # arm 7 — the door appears only as the `func` of a call, in either
        # spelling; binding it to a variable is an inline run the arms cannot see.
        "door-bound-to-another-name",
        _VALID_OFF_LOOP_SOURCE
        + '''
_prov = _stamp_capture_provenance
''',
        "referenced OUTSIDE",
    ),
    (
        # arm 8 — nor may the import rename it (`_refers_to` cannot see an alias).
        "door-imported-under-another-name",
        _VALID_OFF_LOOP_SOURCE
        + '''
from tortoise.hosted_api import _stamp_capture_provenance as _prov
''',
        "IMPORTED UNDER ANOTHER NAME",
    ),
    (
        # arm 9 — the chain's name assembled as a foldable string. ⛔ It must be
        # the FRAGMENTED form: the whole literal is already caught by the
        # whole-word count, which is asserted earlier, so only `+`-folding
        # reaches this arm.
        "chain-name-assembled-from-fragments",
        _VALID_OFF_LOOP_SOURCE
        + '''
def _again(obj):
    getattr(obj, "_stamp_" + "chain")()
''',
        "appears as a STRING LITERAL",
    ),
    (
        # arm 10 — no SECOND reference to the chain (an inline call runs it on
        # the loop, however many submissions also exist).
        "additional-inline-reference",
        '''
def _stamp_chain(point):
    _stamp_capture_provenance(point)

async def handler(point):
    await _run_off_loop(_CAPTURE_EXECUTOR, _stamp_chain)
    _stamp_chain(point)
''',
        "referenced OUTSIDE its off-loop submission",
    ),
    (
        # arm 11 — the submission is REACHABLE (every one of them, not any one).
        "only-submission-under-a-dead-guard",
        '''
def _stamp_chain(point):
    _stamp_capture_provenance(point)

async def handler():
    if False:
        await _run_off_loop(_CAPTURE_EXECUTOR, _stamp_chain)
''',
        "STATICALLY-FALSE",
    ),
    (
        # arm 12 — the name is not mentioned where the walk cannot grade it (a
        # comment here; the count is the fail-closed backstop for indirection).
        "chain-named-where-the-walk-cannot-grade-it",
        _VALID_OFF_LOOP_SOURCE
        + '''
# _stamp_chain is also reached some other way, allegedly
''',
        "time(s) in",
    ),
]


def test_the_checker_passes_the_minimal_valid_fixture() -> None:
    """The control: `_VALID_OFF_LOOP_SOURCE` violates nothing.

    ⛔ WITHOUT THIS ROW `test_every_arm_reds_its_own_mutant` COULD BE SATISFIED
    BY A CHECKER THAT RAISES ALWAYS — the mutants would all still red, and the
    "expected substring" assertion would be met by an arm that fires for the
    wrong reason only if the message matched. This row is the other side: the
    same checker, on a source with no violation, must return silently.
    """
    tree = ast.parse(_VALID_OFF_LOOP_SOURCE)
    _check_stamp_chain_off_loop(tree, _VALID_OFF_LOOP_SOURCE, "<fixture>")


@pytest.mark.parametrize(
    ("source", "expected"),
    [(source, expected) for _id, source, expected in _OFF_LOOP_ARM_MUTANTS],
    ids=[_id for _id, _source, _expected in _OFF_LOOP_ARM_MUTANTS],
)
def test_every_arm_reds_its_own_mutant(source: str, expected: str) -> None:
    """Every arm of the checker FIRES on its own violation — one row per arm.

    ⛔ THE PIN THE SHIPPED MODULE CANNOT SUPPLY (measured): `hosted_api.py` has
    one live, awaited, unguarded submission, so the arms that reject a stray
    reference, an aliased door, a string-built callable, a duplicate definition,
    a dead-guarded submission, an un-awaited submission, a coroutine-function
    chain and a mention the walk cannot grade all passed —
    and deleting each of ELEVEN assertions in turn left this suite GREEN,
    99 passed, at `9fd0ac474b`.
    A gate whose arms can be deleted one by one with nothing going red protects
    only the arms that happen to fire on today's tree.

    Each row carries the arm's OWN message fragment, so a row also pins which
    arm fired: two arms sharing one message would satisfy a row for the wrong
    reason.
    """
    tree = ast.parse(source)
    with pytest.raises(AssertionError) as raised:
        _check_stamp_chain_off_loop(tree, source, "<fixture>")
    assert expected in str(raised.value), (
        f"the arm named by {expected!r} did not fire; the checker said: "
        f"{str(raised.value)[:400]}")


#: A minimal module carrying one dead-guarded and one undecidable-guarded stamp
#: call behind a `match` — the shape an `if`-only walk cannot see.
_MATCH_SOURCE = '''
def dead(value):
    match value:
        case _ if False:
            _stamp_capture_provenance(1)

def live(value):
    match value:
        case _ if _flag:
            _stamp_capture_provenance(2)
'''


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        # A collection is decided by its OPERAND, not by its shape (P2-1).
        ("{*()}", False),
        ("{*{1}}", True),
        ("[*[1]]", True),
        ("{**{}}", False),
        ("{**{1: 2}}", True),
        ("{*_x}", None),
        ("{**_x}", None),
        # Literal shapes that are falsy without being folded by CPython.
        ("f''", False),
        ("f'{_x}'", None),
        ("set()", False),
        ("dict()", False),
        ("set(_x)", None),
        ("1 < 0", False),
        ("1 == 1", True),
        ("'a' in 'abc'", True),
        ("1 < _x", None),
        # `is`/`is not`: graded where the PROGRAM decides identity — a singleton
        # operand, or operands that cannot be one object — and nowhere else.
        # ⛔ NON-VACUOUS BOTH WAYS, measured: grading equal literals from
        # `ast.literal_eval`'s own Constant objects answered False for
        # `'ab' is 'ab'`, whose body CPython RUNS (a false RED, the defect class
        # this PR removes); a bare singleton test then answered `None` for
        # `1 is 2`, which is ALWAYS dead (a fail-open — `if 1 is 2:` around the
        # only submission went GREEN).
        ("None is None", True),
        ("None is not None", False),
        ("True is False", False),
        ("True is 1", False),
        ("1 is 2", False),
        ("(1, 2) is (1, 3)", False),
        ("0.0 is 0", False),
        ("'a' is 1", False),
        ("1 is 2 is 3", False),
        # Equal NON-singleton literals: CPython merges the constants so the body
        # runs, but the language does not require it — undecided, never False.
        ("'ab' is 'ab'", None),
        ("(1, 2) is (1, 2)", None),
        ("1000 is 1000", None),
        ("'ab' is not 'ab'", None),
        ("-0.0", False),
        ("(0 if True else 1)", False),
        ("None if False else None", False),
        # An `if`-expression decided by its TWO BRANCHES agreeing rather than by
        # its test (review P1 at `807a1014`: these two lines were the one arm with
        # no assertion, so deleting them left the suite green).
        ("0 if _x else 0", False),
        ("1 if _x else 1", True),
        ("0 if _x else 1", None),
        ("not {*()}", True),
        # Undecidable — the caller must NOT read these as a polarity.
        ("_x", None),
    ],
)
def test_literal_truth_decides_the_shapes_the_dead_guard_arm_relies_on(
    expr: str, expected: bool | None,
) -> None:
    """The dead-guard arm is only as good as `_literal_truth`'s answers.

    ⛔ NON-VACUOUS, measured: on the pre-fix cuts every f-string, zero-arg
    constructor and literal comparison below answered `None`, so a submission
    under `if set():` / `if f'':` / `if 1 < 0:` stayed GREEN although its body can
    never run — the silent-loss state the arm exists to refuse. The `{*…}`/
    `{**…}` rows are the P2-1 fix, which read a spread from its operand instead of
    treating it as empty. The `0 if _x else 0` / `1 if _x else 1` rows pin the
    branch-agreement arm (deleting its two lines reds them AND
    `test_an_if_expression_guard_is_graded_as_a_guard`, whose dead leg is built from
    the same `0 if _x else 0` — measured: 3 failed), and
    `0 if _x else 1` is its control: disagreeing branches stay undecidable. The
    `None` rows pin the other direction: nothing may grade a run-time value, or a
    correct tree reds with a false diagnosis.
    """
    assert _literal_truth(ast.parse(expr, mode="eval").body) is expected


#: An `if`-expression guard around a submission whose test is a SAME-POLARITY
#: conditional — the shape `_dead_guards`' `IfExp` branch exists to grade.
_IFEXP_SOURCE = '''
def dead():
    _run_off_loop(_CAPTURE_EXECUTOR, chain) if (0 if _x else 0) else None

def live():
    _run_off_loop(_CAPTURE_EXECUTOR, chain) if (0 if _x else 1) else None
'''


def test_an_if_expression_guard_is_graded_as_a_guard() -> None:
    """An `if`-expression test is a guard, and a same-polarity one is ALWAYS false.

    ⛔ NON-VACUOUS, measured: dropping the `ast.IfExp` arm from `_dead_guards`
    (review P3 at `807a1014`: its docstring still said "every enclosing
    `if`/`while`") leaves the `dead` submission unguarded, so this reds. The
    `live` control pins the other direction — branches that DISAGREE are
    undecidable and must not be read as a polarity.
    """
    tree = ast.parse(_IFEXP_SOURCE)
    targets = {
        _enclosing_functions(tree, node)[-2]: node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == _OFF_LOOP
    }
    assert sorted(targets) == ["dead", "live"], targets
    assert [source for _line, source in _dead_guards(tree, targets["dead"])] == [
        "0 if _x else 0"]
    assert _dead_guards(tree, targets["live"]) == []


#: An identity guard around a submission: `1 is 2` cannot be one object, so that
#: body is ALWAYS dead and the arm must grade it; `'ab' is 'ab'` is the shape whose
#: body RUNS (one shared `LOAD_CONST`), so grading it would be a false RED.
_IDENTITY_SOURCE = '''
async def dead():
    if 1 is 2:
        await _run_off_loop(_CAPTURE_EXECUTOR, chain)

async def live():
    if 'ab' is 'ab':
        await _run_off_loop(_CAPTURE_EXECUTOR, chain)
'''


def test_a_literal_identity_guard_is_graded_end_to_end() -> None:
    """`if 1 is 2:` reaches the dead-guard arm; `if 'ab' is 'ab':` must not.

    ⛔ NON-VACUOUS, measured: dropping the identity decision from
    `_literal_truth`'s compare arm (grading `is` with `operator.is_` over
    `literal_eval`'s objects) leaves `dead`'s guard ungraded — so this reds — and
    a singleton-only test reds it too, because `1 is 2` is then undecidable. The
    `live` control pins the other direction: `'ab' is 'ab'` RUNS, so a pin that
    reds it is the false-RED defect this PR exists to remove.
    """
    tree = ast.parse(_IDENTITY_SOURCE)
    targets = {
        _enclosing_functions(tree, node)[-2]: node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == _OFF_LOOP
    }
    assert sorted(targets) == ["dead", "live"], targets
    assert [source for _line, source in _dead_guards(tree, targets["dead"])] == [
        "1 is 2"]
    assert _dead_guards(tree, targets["live"]) == []


def test_a_match_case_guard_is_graded_as_a_guard() -> None:
    """`case _ if False:` is a disable, and an `if`-only walk cannot see it.

    ⛔ NON-VACUOUS, measured: on the pre-fix `_dead_guards` the dead case returned
    NO guards (the walk entered the `match` through `iter_child_nodes` and never
    pushed the case guard), so a submission moved into an unreachable case stayed
    GREEN. The `live` control pins that an undecidable guard is not graded.
    """
    tree = ast.parse(_MATCH_SOURCE)
    targets = {
        node.args[0].value: node
        for node in _stamp_calls(tree)
    }
    assert [source for _line, source in _dead_guards(tree, targets[1])] == ["False"]
    assert _dead_guards(tree, targets[2]) == []


def test_the_mention_count_is_whole_word_and_still_fails_closed() -> None:
    """P2-2: a sibling identifier must not red a CORRECT tree; a string must.

    ⛔ NON-VACUOUS, measured: the pre-fix arm counted SUBSTRINGS, so appending
    `_stamp_chain_retry = None` beside an untouched submission made it RED with
    "the extra mention builds the callable some way this pin cannot follow" —
    false for that input, and the plausible refactor. The string row is the
    declared fail-closed arm that must survive the whole-word change.
    """
    source = HOSTED_API.read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = _enclosing_functions(tree, _stamp_calls(tree)[0])[-2]

    assert _unaccounted_mentions(tree, {owner}, source) == {}
    # A name that EXTENDS the chain's adds no graded node and must add no count.
    assert _unaccounted_mentions(
        tree, {owner}, source + f"\n{owner}_retry = None\n") == {}
    # A callable built from a STRING is invisible to the AST and stays fail-closed.
    unaccounted = _unaccounted_mentions(
        tree, {owner}, source + f'\n_again = locals().get("{owner}")\n')
    assert list(unaccounted) == [owner]
    assert unaccounted[owner][0] - unaccounted[owner][1] == 1


def _expr(source: str) -> ast.expr:
    """The single expression of a one-line source."""
    return ast.parse(source, mode="eval").body


def _call(tree: ast.Module, name: str) -> ast.Call:
    """The single `name(...)` call node of a synthetic module."""
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]
    assert len(calls) == 1, f"expected exactly one `{name}(...)` call, found {len(calls)}"
    return calls[0]


def _tree(lines: list[str]) -> ast.Module:
    """A synthetic module whose LINE NUMBERS are ``1..len(lines)``.

    Building the source from a list rather than a triple-quoted string keeps the
    line of each statement the arithmetic a reader expects, so the assertions below
    can name it (`(2, True)`) instead of being written to match a leading newline.
    """
    return ast.parse("\n".join(lines) + "\n")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # ─── FOLDED: the family the pre-#14522 exact-`ast.Constant` arm missed ───
        ('"_stamp_chain"', "_stamp_chain"),
        ('"_stamp_" + "chain"', "_stamp_chain"),
        ('"_stamp_" "chain"', "_stamp_chain"),
        ('f"{\'_stamp\'}{\'_chain\'}"', "_stamp_chain"),
        # `!s` on a str IS the str, so this one names the chain at run time…
        ('f"{\'_stamp_chain\'!s}"', "_stamp_chain"),
        # ─── NOT FOLDED: the declared boundary, owned by #14528 ───
        ('"%s%s" % ("_stamp", "_chain")', None),
        ('"".join(["_stamp", "_chain"])', None),
        ('"_stamp{}".format("_chain")', None),
        ('"_stamp" + chr(95) + "chain"', None),
        ('f"{chr(95)}_stamp_chain"', None),
        ('f"{\'_stamp_chain\':>20}"', None),
        # ─── …and these ADD characters, so they do NOT name it (review note N2) ───
        ('f"{\'_stamp_chain\'!r}"', repr("_stamp_chain")),
        ('f"{\'_stamp_chain\'!a}"', ascii("_stamp_chain")),
    ],
)
def test_string_value_folds_exactly_what_the_interpreter_folds(
    source: str, expected: str | None
) -> None:
    """`_string_value` returns the string the module would HOLD, or ``None``.

    ⛔ THE PIN THE REVIEW ASKED FOR (P2, measured). Everything `_string_value` can
    fold reds the string arm in the main test; a row that expects a VALUE returns
    ``None`` under the pre-#14522 arm (`isinstance(node, ast.Constant) and
    node.value == owner`), so reverting the fold reds HERE instead of leaving the
    suite green. Verified by mutation, not by reading: with `_string_value`
    restored to the constant compare this test drops to FIVE failures — the `+` row,
    the two-literal f-string row, and the `!s`/`!r`/`!a` rows (the whole-literal and
    the implicitly-concatenated rows are folded by the PARSER into one
    `ast.Constant`, so they pass under both arms; they are here to pin the fold's
    output, not to red on its removal) — and
    `test_a_string_built_callable_is_the_string_arm_that_sees_it` fails with it.

    The ``None`` rows matter in both directions. They pin the boundary the
    docstring declares — so a later "improvement" that starts guessing at run-time
    composition reds here rather than silently claiming coverage — and the `!r`/`!a`
    rows pin that the fold yields the string the interpreter yields, not the inner
    literal: folding `f"{'_stamp_chain'!r}"` to the bare name reported RED on a
    string that does not contain it (review note N2, measured).
    """
    assert _string_value(_expr(source)) == expected


def test_the_arms_agree_on_what_a_reference_is() -> None:
    """The submission arm and the reference walk see the SAME two shapes.

    ⛔ THE SECOND PIN THE REVIEW ASKED FOR (P2, measured). `_refers_to`'s widening
    from `isinstance(node, ast.Name)` — the measured fail-open this PR exists to
    close — and `_submitted_names`' matching widening were reachable only through
    the real module, which carries no attribute submission FOR THIS OWNER and no
    split spelling. A revert of either predicate therefore red nothing. These
    sources are the `tests/test_11609_write_containers.py:743` idiom: the
    Name-vs-Attribute distinction proved on source built for it, with the legal
    shape as the negative control (it must stay GREEN).
    """
    # The legal refactor `_refers_to`'s docstring blesses: an attribute submission.
    legal = _tree([
        "async def g(holder):",
        f"    await {_OFF_LOOP}({_OFF_LOOP_POOL}, holder._stamp_chain)",
    ])
    assert _off_loop_references(legal, "_stamp_chain") == [(2, True)]

    # …and the inline attribute call beside it, i.e. the mutant that ran on the
    # loop while the pre-fix walk reported ZERO references (measured, review m1).
    both = _tree([
        "async def g(holder):",
        f"    await {_OFF_LOOP}({_OFF_LOOP_POOL}, holder._stamp_chain)",
        "    holder._stamp_chain()",
    ])
    assert _off_loop_references(both, "_stamp_chain") == [(2, True), (3, False)]

    # The shipped spelling, for symmetry: a bare-name submission plus an inline call.
    named = _tree([
        "async def g():",
        f"    await {_OFF_LOOP}({_OFF_LOOP_POOL}, _stamp_chain)",
        "    _stamp_chain()",
    ])
    assert _off_loop_references(named, "_stamp_chain") == [(2, True), (3, False)]

    # ⛔ AND THE TWO ARMS MUST NAME THE SAME NODES (review note N1). If
    # `_submitted_names` accepts a shape `_refers_to` cannot see, the submission is
    # a "reference" the walk never reports as submitted — the exact drift the
    # docstring claims is impossible. Measured here rather than asserted in prose.
    for tree in (legal, both, named):
        submitted = sorted(node.lineno for node in _submitted_names(tree))
        reported = sorted(
            line
            for line, is_submission in _off_loop_references(tree, "_stamp_chain")
            if is_submission
        )
        assert submitted and submitted == reported


def test_the_door_arm_grades_every_spelling_the_call_resolver_accepts() -> None:
    """The door is graded in BOTH call spellings `_callable_name` resolves.

    ⛔ THE PIN `c3955990` LACKED (measured). That commit widened `_stamp_calls` from
    `isinstance(node.func, ast.Name)` to `_callable_name(node.func) == _STAMP_DOOR`,
    so `hosted_api._stamp_capture_provenance(…)` is graded instead of falling out of
    the population — a real fail-open, because `_off_loop_submissions` accepted that
    shape all along. But its only population was the real `hosted_api.py`, whose
    four door calls are ALL bare-name call positions, so the widening was
    observable nowhere: reverting it left `84 passed`, i.e. the fail-open could be
    re-opened with nothing red. These sources are the population the module cannot
    supply (same idiom as `test_the_arms_agree_on_what_a_reference_is`).
    """
    graded = _tree([
        "async def g(holder, hosted_api):",
        f"    {_STAMP_DOOR}()",
        f"    holder.{_STAMP_DOOR}()",
        f"    hosted_api.{_STAMP_DOOR}()",
    ])
    # ONE resolver, THREE graded spellings — two are attributes, which the
    # pre-`c3955990` walk dropped from the population entirely.
    assert len(_stamp_calls(graded)) == 3
    assert _door_references_outside_call_position(graded) == {}

    # …and a door the resolver does NOT grade reds: bound to a variable, and read
    # as `.__call__`. These are the shapes this arm exists for.
    rebound = _tree([
        "async def g():",
        f"    _prov = {_STAMP_DOOR}",
        "    _prov()",
    ])
    assert _door_references_outside_call_position(rebound) == {2: "Name"}
    # ⛔ AND THE SAME REBIND IN THE ATTRIBUTE SPELLING, which is a separate row on
    # purpose: this function's docstring says it is "LIFTED OUT OF THE TEST SO IT
    # CAN BE MEASURED", so a Name-only reader of it must be observably wrong here —
    # narrowing the call site above to `isinstance(node, ast.Name)` left every
    # other row green (`92 passed`), i.e. the attribute alias of the door could
    # have re-opened a measured fail-open with nothing red.
    rebound_attr = _tree([
        "async def g(holder):",
        f"    _prov = holder.{_STAMP_DOOR}",
        "    _prov()",
    ])
    assert _door_references_outside_call_position(rebound_attr) == {2: "Attribute"}
    as_call_dunder = _tree([
        "async def g():",
        f"    {_STAMP_DOOR}.__call__()",
    ])
    assert _door_references_outside_call_position(as_call_dunder) == {2: "Name"}

    # ⛔ THE NEGATIVE CONTROL, so the arm is not over-read: a callee built from a
    # STRING is no Name/Attribute node at all, so it stays outside this walk by
    # construction and is caught only by the mention count's fold (the run-time
    # compositions are #14528's). Widening the boundary past this would red the
    # plausible refactor, which is the defect class this PR removes.
    string_built = _tree([
        "async def g(mod):",
        f'    getattr(mod, "{_STAMP_DOOR}")()',
    ])
    assert _door_references_outside_call_position(string_built) == {}


def test_a_string_built_callable_is_the_string_arm_that_sees_it() -> None:
    """The reference walk is blind to a split literal BY DESIGN — the fold sees it.

    Encodes the DIVISION OF LABOUR between the two arms, which was previously only
    stated in comments: `getattr(holder, "_stamp_" + "chain")()` is invisible to
    `_off_loop_references` (it walks Name/Attribute nodes) and is caught only
    because the string arm folds the constant. Deleting the fold turns the second
    assertion into `[]` while the first still passes, so the blind spot cannot
    silently widen (review P3, closed at `1d59c806`; the residual run-time
    compositions remain declared and are #14528's to close).
    """
    tree = _tree([
        "async def g(holder):",
        f"    await {_OFF_LOOP}({_OFF_LOOP_POOL}, _stamp_chain)",
        '    getattr(holder, "_stamp_" + "chain")()',
    ])
    # the walk sees only the legal submission…
    assert _off_loop_references(tree, "_stamp_chain") == [(2, True)]
    # …and the fold is what makes the assembled spelling visible at all.
    assert [
        node.lineno for node in ast.walk(tree) if _string_value(node) == "_stamp_chain"
    ] == [3]


@pytest.mark.parametrize(
    ("source", "folded"),
    [
        # ─── ALWAYS FALSE: the body can never run ───
        ("False", True),
        ("0", True),
        ('""', True),
        ("not True", True),
        ("True and False", True),
        ("_flag and False", True),
        ("False or False", True),
        # ⛔ `1 == 2` IS GRADED HERE, and that is the deliberate criterion change
        # this file's arm 5 states (merged from the `_literal_truth` work): the
        # property is "always false", not "CPython folds it away", because a fixer
        # disables a leg by making the guard false. `1 < 0` is likewise graded — see
        # `test_literal_truth_decides_the_shapes_the_dead_guard_arm_relies_on`.
        ("1 == 2", True),
        # ─── NOT statically false: the dead-guard arm must not claim them ───
        ("True", False),
        ("not _flag", False),
        ("_flag", False),
        ("True and _flag", False),
        ("True or _flag", False),
    ],
)
def test_statically_false_folds_exactly_what_the_interpreter_folds(
    source: str, folded: bool
) -> None:
    """`_statically_false` answers "always false" — not "the interpreter folds it".

    ⛔ THE THIRD PIN ON THE P2 AXIS (measured). The `dead_guards` arm reads this
    predicate, and on the shipped module it passes VACUOUSLY — there is no
    statically-false guard around the submission, so `_dead_guards` returns `[]`
    and the arm never decides anything. Measured at `74e6a0f0`: with this predicate
    neutered to `return False` the WHOLE SUITE still passed (`16 passed`), i.e. the
    arm that exists to reject a "disabled" offload could itself be disabled and no
    assertion noticed. Both directions are pinned above — a fold the interpreter
    performs, and a run-time-constant guard it must NOT claim (claiming it would be
    an over-block: a fixer told to delete a guard that is already correct).
    """
    assert _statically_false(_expr(source)) == folded


def test_dead_guards_reports_only_the_guards_that_are_always_false() -> None:
    """`_dead_guards` returns the enclosing always-false guards — and not live ones.

    The negative controls are the half that matters. A live guard (`if _flag:`) and
    the `else` branch of a false one both RUN, so reporting either would be an
    over-block. The `orelse` rule is the invariant `_dead_guards`' own docstring
    states; it is encoded here rather than only described there.
    """
    dead = _tree([
        "def f():",
        "    if True and False:",
        "        g()",
    ])
    assert _dead_guards(dead, _call(dead, "g")) == [(2, "True and False")]

    live = _tree([
        "def f():",
        "    if _flag:",
        "        g()",
    ])
    assert _dead_guards(live, _call(live, "g")) == []

    # ⛔ the ELSE branch of a statically-false `if` is the branch that RUNS.
    else_branch = _tree([
        "def f():",
        "    if False:",
        "        pass",
        "    else:",
        "        g()",
    ])
    assert _dead_guards(else_branch, _call(else_branch, "g")) == []


def test_the_dead_guard_arm_fires_only_when_every_submission_is_dead() -> None:
    """`all`, not `any`: a dead duplicate beside a live submission is INERT.

    ⛔ THE PIN THE REVIEW ASKED FOR (P1 at `343e7ce1`, measured), and it needs a
    fixture because the shipped module cannot supply the population: `hosted_api`
    has ONE submission and no enclosing guard, so `per == [[]]` and `any` and `all`
    agree there. That is why the decision lives in `_every_submission_dead_guarded`
    and is measured HERE, on two submissions of one owner.

    ⛔ NON-VACUOUS BOTH WAYS. Mutating the helper's `all(per)` to `any(per)`
    reds the first assertion with `{'chain': [(3, 'False')]}` — i.e. it restores
    exactly the false RED this PR's §1 removes: a correct tree reported as "EVERY
    submission … sits under a STATICALLY-FALSE guard … the chain silently does NOT
    run". Mutating it to `if per and False` reds the second, i.e. the silent-loss
    state the arm exists for goes unreported.
    """
    live_plus_dead_duplicate = _tree([
        "async def f():",
        f"    await {_OFF_LOOP}({_OFF_LOOP_POOL}, chain)",
        "    if False:",
        f"        await {_OFF_LOOP}({_OFF_LOOP_POOL}, chain)",
    ])
    # Two submissions, one live: nothing is reported (the duplicate runs nothing).
    assert len(_submitted_names(live_plus_dead_duplicate)) == 2
    assert _every_submission_dead_guarded(live_plus_dead_duplicate, {"chain"}) == {}

    # The only submission dead-guarded: the silent-loss state, REPORTED.
    every_dead = _tree([
        "async def f():",
        "    if False:",
        f"        await {_OFF_LOOP}({_OFF_LOOP_POOL}, chain)",
    ])
    assert _every_submission_dead_guarded(every_dead, {"chain"}) == {
        "chain": [(2, "False")]}

    # Every submission dead — the guards of ALL of them are named, not just the
    # first, so the message points at each place that must be un-disabled.
    both_dead = _tree([
        "async def f():",
        "    if False:",
        f"        await {_OFF_LOOP}({_OFF_LOOP_POOL}, chain)",
        "    if 0:",
        f"        await {_OFF_LOOP}({_OFF_LOOP_POOL}, chain)",
    ])
    assert _every_submission_dead_guarded(both_dead, {"chain"}) == {
        "chain": [(2, "False"), (4, "0")]}

    # ⛔ AND THE ATTRIBUTE SPELLING OF A SUBMISSION, which `_callable_name` resolves
    # and a Name-only reader of this helper would drop — measured: narrowing the
    # call site to `isinstance(node, ast.Name)` kept every other row green
    # (`92 passed`), so the attribute-spelled silent-loss state could have gone
    # unreported with nothing red.
    attribute_dead = _tree([
        "async def f(holder):",
        "    if False:",
        f"        await {_OFF_LOOP}({_OFF_LOOP_POOL}, holder.chain)",
    ])
    assert _every_submission_dead_guarded(attribute_dead, {"chain"}) == {
        "chain": [(2, "False")]}


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # ─── a `for` header: an always-empty iterable never enters the body ───
        (["def f():", "    for _ in ():", "        g()"], ["()"]),
        (["def f():", "    for _ in []:", "        g()"], ["[]"]),
        (["async def f():", "    async for _ in set():", "        g()"], ["set()"]),
        (["def f():", "    for _ in _xs:", "        g()"], []),
        # ⛔ the `else` of a `for` whose iterable is empty STILL RUNS, so that
        # branch is live and must not be reported (the `orelse` rule, for loops).
        (["def f():", "    for _ in ():", "        pass", "    else:", "        g()"], []),
        # ─── a comprehension: its iterables and `if` clauses filter the element ───
        (["def f():", "    [g() for _ in ()]"], ["()"]),
        (["def f():", "    [g() for _ in _xs if False]"], ["False"]),
        (["def f():", "    [g() for _x in _xs for _y in ()]"], ["()"]),
        (["def f():", "    [g() for _ in () for _y in _ys]"], ["()"]),
        (["def f():", "    {k: g() for k in _xs if 1 == 2}"], ["1 == 2"]),
        (["def f():", "    [g() for _ in _xs]"], []),
        (["def f():", "    [g() for _ in _xs if _flag]"], []),
        # ⛔ A CALL INSIDE A CLAUSE IS GRADED TOO, under the guards already in force
        # (review P2 at `f229af1`, measured). The nested-`iter` row is the fail-open
        # direction: with a later clause walked under the ENCLOSING guards only, the
        # inner `g()` runs only if the outer `()` yielded, i.e. never — and that
        # mutant reported nothing. The bare `if g()` row is the other direction: a
        # clause call that DOES run must not be reported.
        (["def f():", "    [x for _ in () for _y in (g(),)]"], ["()"]),
        (["def f():", "    [g() for _ in () if _flag]"], ["()"]),
        (["def f():", "    [x for _ in _xs if g()]"], []),
        # ⛔ CLAUSE CALLS ARE GRADED AT ALL — the row above cannot pin that on its
        # own, because it expects `[]` either way (review P2, measured: making the
        # `ifs` walk a no-op left the whole suite GREEN). Here the element is
        # unreachable (empty iterable) AND the call sits in the clause, so dropping
        # the clause walk stops the target being visited at all and this row reds.
        (["def f():", "    [x for _ in () if (g(),)]"], ["()"]),
        # …and the same for a comprehension's own `iter`: pushing its guard before
        # walking it reports `g()` inside `(0 if g() else 0)`, which RUNS.
        (["def f():", "    [x for _ in (0 if g() else 0)]"], []),
        # ⛔ AND A CALL IN A `for` HEADER RUNS — the iterable is evaluated to DECIDE
        # the loop, so an always-false iterable does not make its own calls dead
        # (review P2 at `f229af1`, measured: pushing the header guard before walking
        # the iterable reported `g()` here, which runs).
        (["def f():", "    for _ in (0 if g() else 0):", "        pass"], []),
        # ⛔ THE `else` OF AN ALWAYS-TRUE `if` IS THE DEAD BRANCH (same finding,
        # measured): walking `orelse` unguarded reported nothing for it.
        (["def f():", "    if True:", "        pass", "    else:", "        g()"],
         ["not True"]),
        (["def f():", "    if False:", "        pass", "    else:", "        g()"], []),
        # ⛔ AND A `while`'s `else` RUNS WHEN THE TEST IS FALSE — it is NOT graded in
        # either polarity (its `else` needs break analysis), so this row pins the
        # DECLARED gap: pushing the test there reported `['False']` for a body that
        # runs (review P2, measured).
        (["def f():", "    while False:", "        pass", "    else:", "        g()"], []),
        # ⛔ A `match` CASE'S GUARD APPLIES TO THAT CASE'S BODY ONLY, and the
        # one-case fixtures cannot measure the scoping (review P2, measured: leaking
        # the guard onto a later case stayed GREEN).
        (["def f(x):",
          "    match x:",
          "        case _ if False:",
          "            pass",
          "        case _:",
          "            g()"], []),
        # ⛔ A NAME IS NOT A BUILTIN: `list` bound as a parameter decides `list()`,
        # so the guard is undecidable rather than always-false (review P2,
        # measured: `[(3, 'list()')]` before `_bound_names`).
        (["def f(list):", "    if list():", "        g()"], []),
        (["def f():", "    if list():", "        g()"], ["list()"]),
        # ⛔ AND A `match` CAPTURE BINDS WITH NO `ast.Name(Store)` NODE (review P2,
        # measured): `case _ as set:` left `set` unbound, so `if set():` inside that
        # case reported the body dead although the matched value decides it. The
        # `case _:` control keeps the genuine builtin graded.
        (["def f(x):", "    match x:", "        case _ as set:", "            if set():",
          "                g()"], []),
        (["def f(x):", "    match x:", "        case _:", "            if set():",
          "                g()"], ["set()"]),
    ],
)
def test_a_loop_header_or_comprehension_clause_is_graded_as_a_guard(
    lines: list[str], expected: list[str],
) -> None:
    """`for _ in ():` and `[… for _ in ()]` are dead bodies, not blind spots.

    ⛔ THE PIN THE REVIEW ASKED FOR (P2 at `343e7ce1`, measured): before this arm,
    `_dead_guards` walked `iter_child_nodes` past every `for`/comprehension, so a
    submission moved into `for _ in ():` — the same silent-loss state as
    `while False:`, in the loop form a fixer writes when disabling a leg — stayed
    GREEN while the docstring claimed "every shape a submission can hide under".
    Deleting the `For`/`AsyncFor` branch or the comprehension branch reds the rows
    above that expect a guard; the `_xs`/`_flag` rows are the negative controls
    that keep an undecidable clause from being read as a polarity.
    """
    tree = _tree(lines)
    assert [
        source for _line, source in _dead_guards(tree, _call(tree, "g"))
    ] == expected


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
