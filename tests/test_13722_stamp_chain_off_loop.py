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
this one would also need a real Postgres). It asserts, for EVERY
`_stamp_capture_provenance(...)` call in `hosted_api`:

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
   (`set()`, `dict()`, `list()`, …), a comparison of literals (`1 < 0`, `1 == 2` — `is`/`is not` only over
   `None`/`True`/`False`, since no other literal has program-determined identity),
   `not X`, `and`/`or` over them, a conditional expression whose test — or whose
   two branches — decide it (`` X if False else Y ``, `` X if PAYLOAD else X ``,
   and an `if`-expression IN test position such as `` if (0 if True else 1): ``),
   and a `match` case's `if` guard (`case _ if False:`). Ungraded: anything
   `_literal_truth` answers `None` for — a run-time constant (`if _FEATURE:`), an
   unpack of a run-time value, an arbitrary call, and an expression-position short
   circuit (`False and await …`), where the submission sits inside the `BoolOp`
   rather than under an `if`/`while`/`if`-expression/match guard.

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

#: The only literals whose `is` identity the program determines.
_SINGLETON = lambda value: value is None or isinstance(value, bool)  # noqa: E731

#: Zero-argument builtin constructors whose result is always EMPTY (falsy).
_EMPTY_CONSTRUCTORS = frozenset({
    "set", "frozenset", "dict", "list", "tuple", "str", "bytes", "int",
    "float", "complex", "bool",
})

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
    """Every `_stamp_capture_provenance(...)` call in the WHOLE module."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == _STAMP_DOOR
    ]


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
            fn = (
                args[1].id
                if isinstance(args[1], ast.Name)
                else getattr(args[1], "attr", None)
            )
            awaited_here = id(node) in awaited
        out.append((node.lineno, pool, fn, awaited_here))
    return out


def _refers_to(node: ast.AST, name: str) -> bool:
    """Does `node` NAME the callable `name` — as a bare name OR as an attribute?

    ⛔ THIS EXISTS SO THE TWO ARMS CANNOT DISAGREE (review finding, P2, measured).
    `_off_loop_submissions` resolves a callable argument through `getattr(arg,
    "attr", None)`, so it accepts an ATTRIBUTE shape (`await _run_off_loop(
    _CAPTURE_EXECUTOR, holder._stamp_chain)`) as a submission — while the
    reference walk below matched `ast.Name` only. The two then disagreed: a
    mutant that moves the chain into a class method, calls it INLINE and also
    submits it by attribute satisfied arms 1-3 and produced ZERO references, so
    the pin went GREEN with the chain running on the loop (measured: `1 passed`,
    rc=0). A walk that cannot see a reference the submission arm accepts is a
    fail-open, so both now answer through this one predicate.
    """
    if isinstance(node, ast.Name):
        return node.id == name
    return isinstance(node, ast.Attribute) and node.attr == name


def _off_loop_references(tree: ast.Module, name: str) -> list[tuple[int, bool]]:
    """Every REFERENCE to `name` in the module: (line, is_the_submitted_one).

    ⛔ The legal reference is the second positional argument of an awaited
    `_run_off_loop(_CAPTURE_EXECUTOR, name)` — a bare name OR an attribute that
    resolves to `name` — and nothing else.
    `name(…)` is an inline run on the caller's thread; `_CAPTURE_EXECUTOR.submit(
    name)` is the same fire-and-forget defect under another helper (it skips
    `_run_off_loop`'s `confine_context` snapshot and is never awaited). Both are
    the #13722 wedge regardless of how many submissions also exist elsewhere in
    the module, so this grades EVERY reference rather than only calls.
    """
    awaited = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    }
    submitted: set[int] = set()
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
            and _refers_to(args[1], name)
        ):
            submitted.add(id(args[1]))
    return sorted(
        (node.lineno, id(node) in submitted)
        for node in ast.walk(tree)
        if _refers_to(node, name)
    )


def _submitted_callables(tree: ast.Module) -> list[ast.AST]:
    """The callable argument node of every awaited off-loop submission.

    i.e. the second positional argument of each awaited
    `_run_off_loop(_CAPTURE_EXECUTOR, …)` — whatever shape it has (`ast.Name` or
    `ast.Attribute`; `_refers_to` decides who it names). Both this and the
    reference arm ask it, so the two cannot disagree about what a submission is.
    """
    awaited = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    }
    out: list[ast.AST] = []
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
        ):
            out.append(args[1])
    return out


def _literal_truth(node: ast.expr) -> bool | None:
    """The statically decidable truth of a literal-ish expression, or None.

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
        members = [(_literal_truth(e.value) if isinstance(e, ast.Starred) else True)
                   for e in node.elts]
        if any(m is True for m in members):
            return True
        return False if all(m is False for m in members) else None
    if isinstance(node, ast.Dict):
        spreads = [_literal_truth(value)
                   for key, value in zip(node.keys, node.values, strict=True)
                   if key is None]
        if any(key is not None for key in node.keys):
            return True
        if any(s is True for s in spreads):
            return True
        return False if all(s is False for s in spreads) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = _literal_truth(node.operand)
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
        # ⛔ `is` IS NOT DECIDED BY VALUE, and `ast.literal_eval` returns the AST's
        # own Constant objects — so grading `'ab' is 'ab'` from them answers False
        # while CPython (which shares one constant slot, `LOAD_CONST` twice) runs
        # the body. Only `None`/`True`/`False` have identity the PROGRAM determines;
        # anything else is undecidable here.
        answer = True
        for fn, kind, right in zip(comparators, operators, rights, strict=True):
            if kind in (ast.Is, ast.IsNot) and not (
                _SINGLETON(left) and _SINGLETON(right)
            ):
                return None
            try:
                if not fn(left, right):  # type: ignore[misc]
                    answer = False
                    break
            except TypeError:
                return None
            left = right
        return answer
    if isinstance(node, ast.IfExp):
        behaviour = _literal_truth(node.test)
        if behaviour is not None:
            return _literal_truth(node.body if behaviour else node.orelse)
        # Both branches deciding the SAME polarity decide the expression, whatever
        # the test is: `X if PAYLOAD else Y` with X and Y both false is always false.
        body, orelse = _literal_truth(node.body), _literal_truth(node.orelse)
        return body if body is not None and body == orelse else None
    if isinstance(node, ast.BoolOp):
        values = [_literal_truth(v) for v in node.values]
        if isinstance(node.op, ast.And):
            if any(v is False for v in values):
                return False
            return True if all(v is True for v in values) else None
        if any(v is True for v in values):
            return True
        return False if all(v is False for v in values) else None
    return None


def _statically_false(test: ast.expr) -> bool:
    """True when ``test`` is ALWAYS false — the body can never run.

    ⛔ THE CRITERION IS "always false", NOT "CPython constant-folds it". A fixer
    disables a leg by making the guard false, and `[]`/`{}`/`{*()}` are just as
    dead as `False` even though CPython evaluates them at run time (`dis` folds only
    a literal constant such as `False`/`0`/`()`); grading them is what makes the arm
    catch the disable a fixer actually writes. `_literal_truth` is the one place
    that decides, so `not X` and `X` cannot disagree.
    """
    return _literal_truth(test) is False


def _dead_guards(tree: ast.Module, target: ast.AST) -> list[tuple[int, str]]:
    """`(line, source)` of every enclosing guard that is ALWAYS FALSE.

    ⛔ THE CRITERION IS "always false", NOT "CPython folds it away" (the header
    states why), and the guards graded are every shape a submission can hide
    under: an `if`/`while` test, an `if`-expression's test, and a `match` case's
    `if` guard. The polarity comes from `_literal_truth` and nowhere else.

    ⛔ `orelse` is NOT descended with the guard pushed: the else-branch of a
    statically-false `if` is exactly the branch that RUNS, so a submission there is
    live and must not be reported.
    """
    dead: list[tuple[int, str]] = []

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
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            # ⛔ `IfExp` is graded too: CPython folds `X if False else Y` to `Y` and
            # drops the `X` branch, so a submission in `X` is as gone as one under a
            # dead `if`. Its `orelse` is the branch that runs, so it is walked
            # unguarded — same rule as `If`.
            walk(node.test, guards)
            body = node.body if isinstance(node.body, list) else [node.body]
            orelse = node.orelse if isinstance(node.orelse, list) else [node.orelse]
            for child in body:
                walk(child, [*guards, (node.test.lineno, node.test)])
            for child in orelse:
                walk(child, guards)
            return
        if node is target:
            dead.extend(
                (line, ast.unparse(test))
                for line, test in guards
                if _statically_false(test)
            )
            return
        for child in ast.iter_child_nodes(node):
            walk(child, guards)

    walk(tree, [])
    return dead


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


def test_every_stamp_call_runs_off_the_loop() -> None:
    """`_stamp_capture_provenance` must be called from a leg submitted off-loop.

    ⛔ NESTING IS NOT THE PROPERTY — this test started as "the chain must not be
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
    tree = _module()
    # ⛔ MODULE-WIDE ON PURPOSE (review cycle 4, P2): the walk used to start at
    # `_capture_session_impl` while the header claimed every stamp call in
    # `hosted_api`, so a stamp appended to any OTHER function of this module was
    # never looked at and the stated population was wider than the measured one
    # (a `/tmp` mutant that added an inline stamp elsewhere stayed green). All
    # four calls live in the handler today; the scan does not assume it.
    calls = _stamp_calls(tree)
    assert calls, (
        f"{HOSTED_API.name} no longer calls {_STAMP_DOOR} — if the stamp chain "
        "moved, this pin must move with it rather than be deleted (#13722)")

    owners = set()
    for call in calls:
        chain = _enclosing_functions(tree, call)
        # ⛔ A clean failure, not an `IndexError` (review residual 2): a stamp call
        # reachable outside any function leaves no enclosing function to submit,
        # so `chain[-2]` would raise before any assertion could name the reason.
        # Still RED either way; this only makes the message the useful one.
        assert len(chain) >= 2, (
            f"{_STAMP_DOOR} is called outside any function of {HOSTED_API.name}, so "
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
        f"{HOSTED_API.name} — a submission matches by NAME, so the other "
        "definition's submission would be accepted for this one and a second copy "
        "of the chain could run on the event loop unseen. Rename it, or re-derive "
        "this pin to key on the definition node (#13722)")

    for owner in sorted(owners):
        assert (_OFF_LOOP_POOL, owner) in submitted, (
            f"{_STAMP_DOOR} is called from {owner!r}, which no function of "
            f"{HOSTED_API.name} ever submits to {_OFF_LOOP}({_OFF_LOOP_POOL}, …) — "
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
        tree, owners, HOSTED_API.read_text(encoding="utf-8"))
    assert not unaccounted, (
        f"{_STAMP_DOOR}'s chain is named {unaccounted} time(s) in "
        f"{HOSTED_API.name} (textual, graded AST node) — the extra mention builds "
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
    by_owner: dict[str, list[ast.AST]] = {}
    for node in _submitted_callables(tree):
        for owner in sorted(owners):
            if _refers_to(node, owner):
                by_owner.setdefault(owner, []).append(node)
    dead_guards = {
        name: [guard for guards in per for guard in guards]
        for name, per in (
            (name, [_dead_guards(tree, node) for node in nodes])
            for name, nodes in by_owner.items()
        )
        if per and all(per)
    }
    assert not dead_guards, (
        f"EVERY submission of {_STAMP_DOOR}'s chain sits under a STATICALLY-FALSE "
        f"guard at {dead_guards} (owner: [(line, test)]) — each of those guards is "
        "ALWAYS FALSE (`_literal_truth` decided it), so no submission is ever "
        "reached and the chain silently does NOT run: no record, no "
        "`PointRevised`, no warning "
        "(#13722). Remove the dead guard rather than leaving the offload "
        "unreachable")


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
    branch-agreement arm (deleting its two lines reds them and nothing else), and
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
