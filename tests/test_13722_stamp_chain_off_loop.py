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
`_stamp_capture_provenance(...)` call in `hosted_api` SPELLED as that bare name in
call position — and, separately, that no STATIC reference this pass can see binds,
aliases, passes or otherwise reads that name, since an alias (`_prov =
_stamp_capture_provenance; _prov(…)`), a `getattr(…)`, a `.__call__(…)` or an
attribute form is the same call under a spelling this walk cannot follow. ⛔ THAT
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
   and reds, however many submissions also exist elsewhere in the module.

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

⛔ ARMS 1 AND 2 COME BEFORE ARM 3, and THAT order is load-bearing (arms 4, 5, 6
and the door-spelling arm follow arm 3; "in that order" would be false). Property 3
is checked AFTER them on purpose: the PRE-#13722 chain lives in `_report_capture`,
a nested `async def`, so a plain revert satisfies "the owner is an `async def`" and
an arm-3-first check reds THERE — reporting "the chain silently does NOT run"
about a shape where it runs, inline on the loop, which is the 53 s wedge. The
submission arm must speak first for a revert; the `async def` arm must only speak
once submission and await hold (an `async def` that IS submitted and awaited still
reaches it, so nothing is lost by the reorder).

WHAT IS PINNED DIRECTLY, WITHOUT USING `hosted_api.py` AS THE FIXTURE. The three
predicates the main test rests on — `_string_value`'s constant fold, the shape set
`_refers_to`/`_submitted_names` agree on, and `_statically_false`/`_dead_guards`
(the dead-guard arm) — are ALSO graded on synthetic sources below. The shipped
module uses only the bare-name spelling, carries no split spelling, no attribute
submission for this owner, and no statically-false guard, so those arms were
otherwise reachable only through it: reverting any of the three predicates left
the WHOLE SUITE GREEN (measured twice for the first two, review P2 at `4fd3fc8c`
and again at `1d59c806` on `/tmp` copies; re-measured for the third at
`74e6a0f0`, where neutering `_statically_false` — or `_dead_guards` itself — still
left `16 passed`). A gate whose only encoding of its own arm is prose is a habit,
not a gate. `test_dead_guards_reports_only_the_guards_the_interpreter_skips`
closes the third.

WHAT IT DELIBERATELY DOES NOT PIN. The number of round trips per record. That
number is a property of `record_projection.replay`'s plan and is measured (not
asserted) by `tests/test_9558_record_write_cost.py`'s prologue assertion and by
#13722's own before/after figures in the PR; #13722 item 2 (batching the plan's
per-record statements) would move it, and this file must not red when it does.

Nor does it pin the submission's RUNTIME reachability: a guard that is false only
at run time (`if _FEATURE: …`) is not statically decidable, so a chain submitted
only under such a flag reads as reachable here. What it DOES reject is a guard the
interpreter itself folds away — a literal-false test (`if False:`, `if True and
False:`, `if _flag and False:`) around the only submission — because that is a
shape a fixer writes on purpose to "disable" the offload, and the result is the
same silent loss as the `async def` arm: no record, no `PointRevised`, no warning,
and a green suite.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

HOSTED_API = Path(__file__).resolve().parent.parent / "tortoise" / "hosted_api.py"

#: The stamp door the chain calls (imported at module scope by `hosted_api`).
_STAMP_DOOR = "_stamp_capture_provenance"

#: The off-loop submission helper and the pool it must be given.
_OFF_LOOP = "_run_off_loop"
_OFF_LOOP_POOL = "_CAPTURE_EXECUTOR"

#: `ast.FormattedValue.conversion` → the function the interpreter applies for it
#: (`-1` is "no conversion", and is absent from this map on purpose).
_FSTRING_CONVERSIONS: dict[int, Callable[[str], str]] = {
    ord("s"): str,
    ord("r"): repr,
    ord("a"): ascii,
}


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
    `_off_loop_submissions` resolves its callable argument through `getattr(arg,
    "attr", None)`, so it accepts an ATTRIBUTE shape — `await
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


def _statically_false(test: ast.expr) -> bool:
    """True when the interpreter itself would fold ``test`` to a false value.

    Only the constant foldings are graded — a literal `False`, `not` of a literal
    truth, an `and` with any false operand, an `or` whose operands are all false.
    A run-time-constant guard (`if _FEATURE:`) is deliberately NOT modelled: it is
    not statically decidable, and this pin says so in its header instead of
    pretending to grade it.
    """
    if isinstance(test, ast.Constant):
        return not test.value
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        operand = test.operand
        return isinstance(operand, ast.Constant) and bool(operand.value)
    if isinstance(test, ast.BoolOp):
        folded = [_statically_false(v) for v in test.values]
        return any(folded) if isinstance(test.op, ast.And) else all(folded)
    return False


def _dead_guards(tree: ast.Module, target: ast.AST) -> list[tuple[int, str]]:
    """`(line, source)` of every enclosing `if`/`while` that folds to false.

    ⛔ `orelse` is NOT descended with the guard pushed: the else-branch of a
    statically-false `if` is exactly the branch that RUNS, so a submission there is
    live and must not be reported.
    """
    dead: list[tuple[int, str]] = []

    def walk(node: ast.AST, guards: list[tuple[int, ast.expr]]) -> None:
        if isinstance(node, (ast.If, ast.While)):
            walk(node.test, guards)
            for child in node.body:
                walk(child, [*guards, (node.lineno, node.test)])
            for child in node.orelse:
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

    # ⛔ SYNC, NOT `async def` (review finding 3) — ASSERTED AFTER 1 AND 2, NOT LAST
    # (cycle-10 finding 1, falsified against `origin/main`): `_run_off_loop` hands
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
    # (a bare name or an attribute resolving to it — `_refers_to`),
    # so every other reference it finds is a second way to run the chain and reds.
    # All three mutants above red here; the shipped tree has exactly one reference
    # per owner, at the submission.
    stray = {
        owner: [line for line, submitted_here in _off_loop_references(tree, owner)
                if not submitted_here]
        for owner in sorted(owners)
    }
    stray = {owner: lines for owner, lines in stray.items() if lines}

    # ⛔ THE DOOR UNDER ANOTHER NAME IS STILL THE DOOR (review finding, P2,
    # measured). `_stamp_calls` above matches ONE SPELLING — `ast.Name` in call
    # position — so `_prov = _stamp_capture_provenance; _prov(…)` inline in the
    # handler put the per-id record writes back on the loop and this pin stayed
    # GREEN (`1 passed`, 4 of the 5 invocations seen); `getattr(mod,
    # "_stamp_capture_provenance")(…)` and `_stamp_capture_provenance.__call__(…)`
    # were green too. Every ARM above keys on the chain NAME, so a door reached
    # from a name they never see is invisible to all of them. This arm closes the
    # spelling: the door may appear ONLY as the `func` of a call, and only through
    # a bare `ast.Name` — binding it to a variable, passing it as an argument,
    # reading `.__call__`, or any attribute form reds. Measured cost today: zero
    # (the four calls are all bare-name call positions).
    door_call_nodes = {
        id(node.func) for node in _stamp_calls(tree)
    }
    door_spellings = {
        node.lineno: type(node).__name__
        for node in ast.walk(tree)
        if _refers_to(node, _STAMP_DOOR) and id(node) not in door_call_nodes
    }
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
        f"{_STAMP_DOOR} is referenced OUTSIDE a bare-name call at "
        f"{sorted(door_spellings.items())} (line: node) — an alias, a "
        "`getattr(…)`, or a `.__call__` is the SAME call under a spelling the arms "
        "above cannot follow, so its per-id record writes could run on the event "
        "loop with this pin GREEN. Call it by name in call position, or re-derive "
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
    # ⛔ `_refers_to`'s NAME, not `node.id`: the submitted node may be an ATTRIBUTE
    # (`holder._stamp_chain`), which carries `.attr` and no `.id` — reading `.id`
    # here raised `AttributeError` on a shape the arms above deliberately accept.
    dead_guards = {
        name: guards
        for name, guards in (
            (getattr(node, "id", None) or getattr(node, "attr", None),
             _dead_guards(tree, node))
            for node in _submitted_names(tree)
            if (getattr(node, "id", None) or getattr(node, "attr", None)) in owners
        )
        if guards
    }
    assert not dead_guards, (
        f"{_STAMP_DOOR}'s chain is submitted only under a STATICALLY-FALSE guard "
        f"at {dead_guards} (owner: [(line, test)]) — the interpreter folds that "
        "test away, so the submission is never reached and the chain silently "
        "does NOT run: no record, no `PointRevised`, no warning (#13722). Remove "
        "the dead guard rather than leaving the offload unreachable")


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
        # ─── the interpreter folds these to a FALSE value: the body never runs ───
        ("False", True),
        ("0", True),
        ('""', True),
        ("not True", True),
        ("True and False", True),
        ("_flag and False", True),
        ("False or False", True),
        # ─── NOT statically false: the dead-guard arm must not claim them ───
        ("True", False),
        ("not _flag", False),
        ("_flag", False),
        ("True and _flag", False),
        ("True or _flag", False),
        # `1 == 2` is constant-false to a human and NOT folded here on purpose:
        # `_statically_false` models only what the INTERPRETER folds, and its
        # docstring says a `Compare` is out of scope.
        ("1 == 2", False),
    ],
)
def test_statically_false_folds_exactly_what_the_interpreter_folds(
    source: str, folded: bool
) -> None:
    """`_statically_false` answers only what the INTERPRETER would fold away.

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


def test_dead_guards_reports_only_the_guards_the_interpreter_skips() -> None:
    """`_dead_guards` returns the enclosing folds-to-false guards — and not live ones.

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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
