"""#13722 — the capture provenance stamp chain must not run on the event loop.

WHAT THIS PINS. Issue #13722 is a private-beta launch blocker: `/health`'s
`loop_blocks` reported `max_age_s` 53.041, i.e. the single uvicorn worker's
event loop was blocked for 53 s, and the stack's leaf was the record fold
(`record_projection._ordered_column_ops` -> `record_writer.write_live_record`
-> `record_projection.replay`).

The measured cause is not a parse and not a prewarm: every live record write is
SEVEN blocking Postgres round trips (two tenant-prologue statements, the
cursor-presence read, three plan statements and one commit) taken on the
CALLER's thread, and the capture handler's provenance stamp chain emits one
record per stamped id. 53.041 s / (7 x round-trip latency) = 1647 / 49.7 / 31.5
records at the 4.6 / 152.5 / 240.7 ms latencies measured on the live box, and
`workload_in_flight` was observed at 19-32 — the wedge IS the in-flight
population, i.e. one capture's stamp chain.

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
   handler's own `except` (#8501's non-fatal warning arm); and
3. the containing function is a SYNC `def`, because `_run_off_loop` runs `fn`
   synchronously inside the worker (`monitoring.confine_context`) — handed an
   `async def` it returns an unawaited coroutine object and NOTHING runs: no
   record, no append, no warning, no exception, and a green pin.

⛔ NESTING IS NOT THE PROPERTY, and a first cut of this file asserted it: the
chain already lives in `_report_capture`, a nested `async def`, and nesting a
coroutine changes nothing about which thread runs it (it is `await`ed on the
loop). That assertion was falsified against `origin/main` before it landed.

WHAT WOULD RED IT. Reverting the chain to the loop (the pre-#13722 shape, no
submission at all) reds 1; a submission to a different pool reds 1; dropping the
`await` (`asyncio.ensure_future`, a bare un-awaited `_run_off_loop(...)`) reds 2;
turning the containing function into a coroutine function reds 3.

WHAT IT DELIBERATELY DOES NOT PIN. The number of round trips per record. That
number is a property of `record_projection.replay`'s plan and is measured (not
asserted) by `tests/test_9558_record_write_cost.py`'s prologue assertion and by
#13722's own before/after figures in the PR; #13722 item 2 (batching the plan's
per-record statements) would move it, and this file must not red when it does.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

HOSTED_API = Path(__file__).resolve().parent.parent / "tortoise" / "hosted_api.py"

#: The capture handler that owns the provenance stamp chain.
_HANDLER = "_capture_session_impl"

#: The stamp door the chain calls (imported at module scope by `hosted_api`).
_STAMP_DOOR = "_stamp_capture_provenance"

#: The off-loop submission helper and the pool it must be given.
_OFF_LOOP = "_run_off_loop"
_OFF_LOOP_POOL = "_CAPTURE_EXECUTOR"


def _module() -> ast.Module:
    return ast.parse(HOSTED_API.read_text(encoding="utf-8"))


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {HOSTED_API.name}")


def _enclosing_functions(tree: ast.Module, target: ast.AST) -> list[str]:
    """The names of the functions enclosing ``target``, innermost first."""

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


def _stamp_calls(handler: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == _STAMP_DOOR
    ]


def _off_loop_submissions(
    handler: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[int, str | None, str | None, bool]]:
    """Every `_run_off_loop(...)` in the handler: (line, pool, fn, awaited)."""
    awaited = {
        id(node.value)
        for node in ast.walk(handler)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    }
    out: list[tuple[int, str | None, str | None, bool]] = []
    for node in ast.walk(handler):
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
    # `_HANDLER` while the header claimed every stamp call in `hosted_api`, so a
    # stamp appended to any OTHER function of this module was never looked at and
    # the stated population was wider than the measured one (a `/tmp` mutant that
    # added an inline stamp elsewhere stayed green). All four calls live in the
    # handler today; the scan does not assume it.
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

    # ⛔ SYNC, NOT `async def` (review finding 3): `_run_off_loop` hands `fn` to
    # `_submit_off_loop`, which runs it SYNCHRONOUSLY inside the worker
    # (`monitoring.confine_context`'s `ctx.run(fn, ...)`). Given a coroutine
    # function it returns an unawaited coroutine OBJECT — the future resolves to
    # that object, `_run_off_loop` returns it, and the chain never executes: no
    # record, no append, no warning, and every assertion below still passes.
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
            f"{_HANDLER} submits {owner!r} to {_OFF_LOOP} but never awaits the "
            "submission — the handler's own `except` is what keeps a stamp "
            "failure non-fatal (#8501/#1529), and a detached task moves that "
            "failure where nothing reports it (#13722)")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
