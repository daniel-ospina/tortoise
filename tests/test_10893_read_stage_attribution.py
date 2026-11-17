"""#10893 — a breached wait budget must NAME the stage it died in.

The defect was never only the latency. A breached request came back with one
sentence — *"The server's wait budget for this request was exceeded before a
response was ready. The work may still complete on the server."* — and that
sentence cannot distinguish:

* this request's own read path being slow (the encode? the graph round trip?),
* this request's read path being FINE and the event loop blocked by some other
  request's work while this one waited for its first instruction,
* the answer being already assembled and merely late.

Every re-measurement recorded on the issue picked a different one of those from
the same text, from outside the process. ``tortoise/read_stages.py`` makes the
server say which one it is, on the refusal, on the breach telemetry and on the
log line.

What this file pins, in order of what would break silently:

1. the SHARED-MUTABLE-OBJECT property — the stages are marked on a read-lane
   worker reached through a context COPY, and the timeline the transport seam
   reads is the one the worker mutated. If the ContextVar ever carried a VALUE
   instead of a reference, every breach would report ``pre_handler`` and the
   instrument would be a confident lie;
2. the CLOSED VOCABULARY — an unknown stage name never publishes;
3. the ADDITIVE-ONLY wiring — a request with no timeline bound is
   byte-identical to the pre-#10893 refusal, and the refusal's IDENTITY
   (``code`` + the exact neutral ``message``) never moves on any arm, because
   ``tests/_verdict.py`` classifies a wait-bound refusal by exactly that pair;
4. the ALLOWLIST registration — an unregistered analytics prop is stripped in
   silence (#3821), which here would rebuild the blindness the change removes.
"""
from __future__ import annotations

import ast
import asyncio
import contextvars
import json
import os
import threading
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from tortoise import hosted_api as ha
from tortoise.mcp_auth import _TRANSPORT_WAIT_BOUND_MESSAGE
from tortoise.read_stages import (
    STAGE_EMBED_ENCODE,
    STAGE_EMBED_GET,
    STAGE_EP_ANNOTATION,
    STAGE_ORDERING,
    STAGE_PRE_HANDLER,
    STAGE_RESPONSE,
    STAGE_RETRIEVAL,
    STAGE_SET,
    STAGE_UNMARKED,
    STAGES,
    begin,
    mark,
    request_stages,
    stage_view,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SDK = _REPO_ROOT / "tortoise" / "sdk.py"


# ── helpers ─────────────────────────────────────────────────────────────────

def _collect_send(messages: list):
    async def send(message):
        messages.append(message)
    return send


def _decode(messages: list) -> dict:
    return json.loads(messages[-1]["body"].decode())


# ── 1. the shared-mutable-object property (the load-bearing one) ────────────

def test_a_mark_inside_a_copied_context_reaches_the_transport_seam():
    """The read path runs on a worker through a context COPY.

    ``hosted_api._read_offload`` submits ``tortoise_fts_query`` to the read lane
    under ``monitoring.confine_context``, i.e. a FRESH ``Context``. A stage
    written into the ContextVar from that copy would never be visible to the
    seam that reads it at the deadline. It is visible because the ContextVar
    carries a REFERENCE to one ``ReadStages`` and the worker mutates the object.

    This is the test that would red if someone "simplified" the module to store
    the current stage in the ContextVar itself.
    """
    timeline = begin()

    # Bound on the "request" thread; the worker runs a COPY of that context —
    # exactly what ``_read_offload``'s ``monitoring.confine_context`` does. ⛔ The
    # copy is taken INSIDE the binding and on the REQUEST thread, for two
    # separate reasons: taken outside it, the copy carries no timeline; taken
    # inside a ``threading.Thread``, it would copy the thread's OWN empty
    # context (a new thread does not inherit its parent's), and the assertion
    # below would fail for a reason that has nothing to do with the property
    # under test.
    def _worker():
        ctx.run(mark, STAGE_EMBED_GET)
        ctx.run(mark, STAGE_EMBED_ENCODE)

    with request_stages(timeline):
        ctx = contextvars.copy_context()
        t = threading.Thread(target=_worker)
        t.start()
        t.join()

    # Read from the ORIGINAL context, exactly as the middleware does.
    assert timeline.current() == STAGE_EMBED_ENCODE
    assert STAGE_EMBED_GET in timeline.spent_ms()


def test_the_open_stage_is_distinct_from_the_spent_stages():
    """``spent_ms`` reports FINISHED stages; ``current`` reports the incident."""
    timeline = begin()
    with request_stages(timeline):
        mark(STAGE_EMBED_GET)
        mark(STAGE_RETRIEVAL)
        snapshot = stage_view()

    assert snapshot["stage"] == STAGE_RETRIEVAL
    # The open stage's elapsed time is NOT an observation — it is the incident.
    assert STAGE_RETRIEVAL not in snapshot["stages_ms"]
    assert STAGE_EMBED_GET in snapshot["stages_ms"]



def test_remecking_the_same_stage_folds_rather_than_duplicating():
    """A dense EXPANSION pass re-enters retrieval; the total must not double-count.

    ⛔ THIS CASE IS NOT THE FOLD GUARD'S FALSIFIER, and it does not claim to be.
    Round 1 added an assertion here for the guard (finding F3) and round 2 showed
    it cannot fail for that claim: the guard's only write is to the RE-MARKED
    bucket, so nothing it does can move ``embed_get``, and the key set is
    identical with and without the guard. The case that does redden under
    ``delete `if stage == self._stage: return` `` is the sibling below,
    ``test_the_fold_guard_is_observable_while_the_restage_is_still_open``
    (observed: WITH ``(True, 'retrieval')`` / MUTANT ``(False, 'retrieval')``).

    What THIS case pins is the outcome that made the guard necessary in the
    first place: a re-mark must not manufacture a stage that no wall clock
    supports. ``response`` is marked last, so it is the open stage and must be
    absent from ``spent_ms()`` — a stage that is still running is never reported
    as finished.
    """
    timeline = begin()
    with request_stages(timeline):
        mark(STAGE_EMBED_GET)
        mark(STAGE_RETRIEVAL)
        mark(STAGE_RETRIEVAL)  # the #2518 expansion pass — a no-op re-mark
        mark(STAGE_RESPONSE)
        spent = timeline.spent_ms()

    # `pre_handler` legitimately appears: a few µs elapse between `begin()` and
    # the first mark, and on a real request that interval is the auth/middleware
    # cost — the very thing "the bound expired before any stage ran" reports.
    assert set(spent) == {STAGE_PRE_HANDLER, STAGE_EMBED_GET, STAGE_RETRIEVAL}
    # The terminal stage is still OPEN at the snapshot, so it is not spent.
    assert STAGE_RESPONSE not in spent


def test_the_graph_ordering_branch_still_reaches_the_terminal_stage():
    """#10893 review F1: ``order_by="graph"`` returns from its own branch.

    Round 1 marked ``response`` only on the fall-through path, so the graph
    branch returned before the terminal mark and a breach during response
    assembly published ``ep_annotation``/``ordering`` — a stage that had already
    finished. That is the misattribution class this module exists to remove, so
    the fix is pinned by control flow, not by the presence-only AST scan below:
    the mark must precede the branch's own ``return``.
    """
    source = (_REPO_ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    branch = source.index('if order_by == "graph":')
    terminal = source.index("return _with_read_status(_belief_label(", branch)
    window = source[branch:terminal]
    assert "_mark_read_stage(STAGE_RESPONSE)" in window, (
        "the order_by='graph' branch returns without marking the terminal "
        "stage — a breach after it finished would be blamed on the rerank"
    )
    # And the ordering pass itself is a named stage, not charged to the EP read.
    assert source.index("_mark_read_stage(STAGE_ORDERING)") < branch


def _post_retrieval_returns():
    """``(returns, fn)`` — every ``return`` in ``tortoise_fts_query``, paired with
    the nearest ``_mark_read_stage`` in **its own block**, plus the function node.

    The CALLER filters to the post-retrieval ones (a line comparison against the
    ``retrieval`` mark): the pre-retrieval returns are the argument guards and
    carry no retrieval work.

    The block matters (round-3 review F3 discipline): a global "nearest preceding
    mark" scan passes when a *different arm's* terminal mark sits earlier in the
    function, so deleting one arm's mark still read as green. A return's own
    enclosing block is what actually decides which stage is open when it runs.

    Nested functions are excluded: a ``return`` inside a closure is not a return
    of the read path.
    """
    tree = ast.parse(_SDK.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "tortoise_fts_query")

    def mark_name(stmt):
        if isinstance(stmt, ast.Expr):
            call = stmt.value
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "_mark_read_stage" and call.args
                    and isinstance(call.args[0], ast.Name)):
                return call.args[0].id
        return None

    def enclosing_block(stmt, parent):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(parent, field, None)
            if isinstance(block, list) and any(s is stmt for s in block):
                return block
        return None

    found: list[tuple[int, str | None]] = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return):
                block = enclosing_block(child, node)
                before = [mark_name(s) for s in (block or [])[: (block.index(child) if block else 0)]]
                before = [name for name in before if name]
                found.append((child.lineno, before[-1] if before else None))
            visit(child)

    visit(fn)
    return found, fn


def test_the_post_retrieval_ordering_pass_has_its_own_stage():
    """The ordering pass is bracketed BETWEEN ``ep_annotation`` and the graph
    branch, not merely somewhere in the function (round-3 review F3).

    Round 2 pinned only ``index(ORDERING) < index('if order_by == "graph":')``,
    which holds equally if ``ordering`` is marked where ``ep_annotation`` is —
    i.e. the ordering RELATIONSHIP between them, the entire content of finding
    F1, was unpinned. Swapping the two marks (reintroducing the defect) left
    every row green; this pin reddens.
    """
    source = _SDK.read_text(encoding="utf-8")
    annotation = source.index("_mark_read_stage(STAGE_EP_ANNOTATION)")
    ordering = source.index("_mark_read_stage(STAGE_ORDERING)")
    branch = source.index('if order_by == "graph":')
    assert annotation < ordering < branch, (
        "ep_annotation must close before the ordering pass opens, and the "
        "ordering pass must be open before the graph branch returns"
    )


def test_fusion_brackets_only_the_rrf_call_and_no_graph_round_trip():
    """#10893 review F1: ``fusion`` is documented as PURE CPU.

    At round 2 it was opened and left open across ~10 graph round trips (recency
    weights, kind/status/claim filters, the echo fold, the rerank pool fetch), so
    a breach inside the ``exclude_status`` query published ``stage=fusion`` with
    ``retrieval`` already closed and healthy — the operator rules out the graph
    and blames the CPU. The window is pinned structurally: between the fusion
    mark and the next mark it must contain ``rrf_fusion(`` and NO ``graph.query(``.
    """
    source = _SDK.read_text(encoding="utf-8")
    start = source.index("_mark_read_stage(STAGE_FUSION)")
    nxt = source.index("_mark_read_stage(", start + 1)
    window = source[start:nxt]
    assert "rrf_fusion(" in window, "fusion must bracket the RRF call itself"
    assert "graph.query(" not in window, (
        "a graph round trip sits inside the fusion window — a breach there would "
        "be published as 'the CPU fusion is stuck'"
    )


def test_every_return_after_the_retrieval_mark_reaches_the_terminal_stage():
    """#10893 review F2: ``response``'s contract says EVERY return path reaches it.

    Round 2 marked only the graph branch, so four degraded/failure returns
    (relevance floor, TF-IDF snapshot tier, legacy fallback, no_fallback) left
    ``retrieval`` open — a stage that had already returned (``degradation_chain``
    is above them). Those arms are reachable in the incident class this PR is
    about: they are entered when every leg failed, which is what a store stall
    or a per-leg deadline produces.

    The pin is block-scoped: each post-retrieval return must carry a
    ``STAGE_RESPONSE`` mark in its OWN enclosing block. A global nearest-mark
    scan would pass on a mutant that deletes one arm's mark, because another
    arm's mark sits earlier in the function — verified: that weaker version
    stayed green, this one reddens.
    """
    found, fn = _post_retrieval_returns()
    retrieval_mark = min(
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_mark_read_stage" and n.args
        and isinstance(n.args[0], ast.Name) and n.args[0].id == "STAGE_RETRIEVAL"
    )
    post = [(line, name) for line, name in found if line > retrieval_mark]
    assert len(post) >= 4, (
        "expected the degraded/failure returns behind the retrieval mark; found "
        f"only {post} — this pin is only meaningful while they exist"
    )
    for line, name in post:
        assert name == "STAGE_RESPONSE", (
            f"tortoise_fts_query returns at line {line} with {name!r} in its own "
            "block — a breach there would be blamed on a stage that had already "
            "returned"
        )


def test_the_terminal_mark_never_covers_a_graph_round_trip():
    """#10893 review F4: ``response`` claims "every marked stage finished … the
    work is done, the answer is late" — so it must not cover a graph fetch, or
    the instrument reports the work as DONE exactly when the store is the
    blocking call.

    ⛔ THE PIN IS AST-SHAPED, AND THAT IS THE POINT. Round 4 shipped a STRING
    window here (``source[start:source.find("return ", start)]``) and round 5
    showed it was VACUOUS on the very defect it names: a ``return``'s expression
    is evaluated AFTER the preceding statement, so in

        _mark_read_stage(STAGE_RESPONSE)
        return _with_read_status(_decorate_fallback_hits(snap_hits, graph))

    the window stops at the ``return`` token and is whitespace only — the graph
    call it is supposed to exclude sits *inside* the expression, past where the
    scan ends. Measured: that string pin reported 0 failures on the surgically
    reverted source. A pin that cannot fail on its own defect is worse than none.

    So: for every ``STAGE_RESPONSE`` mark, take everything from the NEXT statement
    in its own enclosing block up to and including the statement that LEAVES that
    block (a ``return``/``raise``). Nothing in that span may mention ``graph`` —
    no ``Attribute``/``Name`` reference anywhere in its expression tree. That is
    exactly the "decorate first, then mark" rule.

    ⛔ THE SPAN, NOT THE RETURN'S VALUE. Checking only a ``return`` that
    *immediately* follows the mark leaves a second blind spot of the same family:

        _mark_read_stage(STAGE_RESPONSE)
        graph.query("RETURN 1")
        return _with_read_status(hits)

    is GREEN under a value-only check (the next statement is not a ``Return``, so
    the site is skipped), while it publishes ``response`` over a store round trip
    just as surely as the round-3 shape did. Round 4's string window happened to
    catch it; the AST rewrite must not trade one blind spot for another, so the
    whole mark-to-exit span is walked.

    ⛔ A MARK THAT CLOSES ITS BLOCK IS NOT A COVERED SITE, AND IS NOT SILENTLY
    SKIPPED. When a ``STAGE_RESPONSE`` mark is the LAST statement of a block,
    execution continues in the ENCLOSING block with ``response`` still open, so
    the time it covers is real — this pin just does not model that shape (the
    span it walks has no next statement to start from). A bare ``continue``
    would make that a blind spot of exactly the family the rounds above closed:
    a shape the pin cannot see, reading as ``green``. So the sites are counted
    and asserted empty, naming their lines — no site has that shape today (all
    six mark-then-leave), and if one appears this reddens for a human to extend
    the walk instead of under-covering it.

    ⛔ WHAT THE ROUND-TRIP TEST RECOGNISES, AND WHAT IT DOES NOT (round-9 review
    P3, measured). It matched only a ``Name``/``Attribute`` rooted at the local
    ``graph`` parameter, but the method also reaches the store through
    ``self.query(...)`` — so the SAME defect re-introduced through a handle-based
    call would have stayed green. Both shapes are now recognised: a mutant that
    inserts ``self.query(kind="Point")`` between a terminal mark and its return
    is GREEN under the graph-only test and RED under this one. NOT covered, and
    stated so this is not read as complete: an alias binding (``g = graph``) and
    a store call made inside a callee the span merely hands ``graph`` to — a
    name-based walk can resolve neither.
    """
    found, fn = _post_retrieval_returns()  # reuse the parse, ignore the returns
    assert found, "tortoise_fts_query must parse for this pin to mean anything"

    def is_terminal_mark(stmt):
        return (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "_mark_read_stage"
            and stmt.value.args
            and isinstance(stmt.value.args[0], ast.Name)
            and stmt.value.args[0].id == "STAGE_RESPONSE"
        )

    def mentions_store(expr):
        """Any reference in ``expr`` that can reach the store.

        Two shapes, both live in ``tortoise_fts_query`` today::

            _decorate_fallback_hits(snap_hits, graph)   # the `graph` handle
            points = self.query(kind=kind, ...)         # a handle-based call

        The first cut matched only the ``graph`` handle, which left the second
        shape a silent blind spot (round-9 review P3) — see the pin's docstring
        for the mutant that demonstrates it and for what this still cannot see.
        """
        for node in ast.walk(expr):
            if isinstance(node, ast.Name) and node.id == "graph":
                return True
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == "graph":
                    return True
                # `self.query(...)` / `graph.query(...)` — a store round trip
                # however the handle was obtained. Matching the ATTRIBUTE name
                # (not just a `graph` base) is what closes the P3 gap.
                if node.attr == "query":
                    return True
        return False

    checked = 0
    block_closing = []
    offenders = []
    for node in ast.walk(fn):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block):
                if not is_terminal_mark(stmt):
                    continue
                nxt = block[i + 1] if i + 1 < len(block) else None
                if nxt is None:
                    # Last statement of its block: the mark DOES cover time (the
                    # enclosing block keeps running with `response` open), but
                    # this pin has no span to walk from here. Record the site and
                    # redden below rather than skipping it silently.
                    block_closing.append(stmt.lineno)
                    continue
                # Every statement between the mark and the block's EXIT runs with
                # `response` open — the exit's argument list included, because a
                # `return`'s expression is evaluated after the mark. `exit_at`
                # bounds the span; without an exit the tail of the block is the
                # span (the time is still charged to `response`).
                exit_at = next(
                    (j for j in range(i + 1, len(block))
                     if isinstance(block[j], (ast.Return, ast.Raise))),
                    None,
                )
                span = block[i + 1: (exit_at + 1 if exit_at is not None else len(block))]
                if isinstance(nxt, ast.Return):
                    checked += 1  # the population guard counts the export arms
                if any(mentions_store(s) for s in span):
                    offenders.append(nxt.lineno)
    assert checked >= 4, (
        f"only {checked} terminal-mark-then-return sites found — this pin is only "
        "meaningful while the degraded arms exist"
    )
    assert not block_closing, (
        f"the STAGE_RESPONSE mark at line(s) {block_closing} is the LAST statement "
        "of its block — `response` stays open in the enclosing block, so this pin "
        "cannot see the span that follows. Extend the walk to the enclosing "
        "block's exit instead of letting the site be skipped"
    )
    assert not offenders, (
        f"the block exiting at line(s) {offenders} runs a graph round trip with "
        "`response` already open — a breach blocked in the store would be "
        "published as 'the work is done, the answer is late'"
    )




def test_the_fold_guard_is_observable_while_the_restage_is_still_open():
    """The guard's ONE observable effect: a breach arriving mid-re-mark.

    With the guard, the re-mark is a no-op, so the re-marked stage is still the
    OPEN one and appears in NEITHER ``spent_ms()`` nor ``current()``'s second
    bucket. Without it the stage would have been closed and reopened, so a
    snapshot taken between the two marks would report it as SPENT — claiming a
    stage that had not finished, which is the confusion the module's "only
    FINISHED stages appear" contract exists to prevent.
    """
    timeline = begin()
    with request_stages(timeline):
        mark(STAGE_EMBED_GET)
        mark(STAGE_RETRIEVAL)
        mark(STAGE_RETRIEVAL)
        mid = timeline.spent_ms()
    assert STAGE_RETRIEVAL not in mid
    assert timeline.current() == STAGE_RETRIEVAL


# ── 2. the closed vocabulary ────────────────────────────────────────────────

def test_an_unknown_stage_is_refused_rather_than_published():
    """The vocabulary is CLOSED: a dashboard groups by stage, so a typo must not
    become a new one. ``mark`` is on the read path — it must refuse SILENTLY,
    never raise into a request."""
    timeline = begin()
    with request_stages(timeline):
        mark("embed_encode_typo")
        mark(STAGE_EMBED_ENCODE)
        snapshot = stage_view()

    assert snapshot["stage"] == STAGE_EMBED_ENCODE
    assert "embed_encode_typo" not in json.dumps(snapshot)


def test_the_vocabulary_is_a_closed_set_of_named_stages():
    assert tuple(dict.fromkeys(STAGES)) == STAGES, "duplicate stage name"
    assert frozenset(STAGES) == STAGE_SET
    # Every stage the read path marks is a member — a `mark` call naming a
    # non-member is a silent no-op, which is exactly how an instrument dies.
    assert {STAGE_PRE_HANDLER, STAGE_EMBED_GET, STAGE_EMBED_ENCODE,
            STAGE_RETRIEVAL, STAGE_RESPONSE} <= STAGE_SET
    # The two stages this PR's review rounds added, each for a defect the
    # original vocabulary could not express: `ordering` (the graph rerank is a
    # read, not annotation) and the correction that the EP decoration read is
    # not the last graph round trip. A stage that is not a member is a silent
    # no-op at its call site, so membership is asserted, not assumed.
    assert STAGE_ORDERING in STAGE_SET
    assert STAGE_EP_ANNOTATION in STAGE_SET
    assert STAGES.index(STAGE_EP_ANNOTATION) < STAGES.index(STAGE_ORDERING) \
        < STAGES.index(STAGE_RESPONSE), "the timeline order is the read order"


# ── 3. the boundary: nothing bound ⇒ nothing changes ───────────────────────

def test_unbound_marks_are_a_noop_and_publish_nothing():
    assert stage_view() is None
    assert mark(STAGE_RETRIEVAL) is None          # does not raise
    assert stage_view() is None


def test_the_wire_view_withholds_an_attribution_for_a_read_that_never_started():
    """``unmarked`` + no accrued time is the operator's answer, not the caller's:
    it says only that NO stage was recorded, and a caller has no lever in it.
    Publishing it would also put noise on a wire contract three shipped tests pin
    to exactly ``{"detail": …}``."""
    timeline = begin()
    with request_stages(timeline):
        # Nothing marked — either the bound expired before the read path started,
        # or this route records no stages at all. The vocabulary says exactly
        # that and no more (review finding F4).
        assert timeline.wire_snapshot() is None
        assert stage_view() == {"stage": STAGE_UNMARKED, "stages_ms": {}}


def test_pre_handler_is_only_claimed_once_the_read_actually_started():
    """The F4 distinction: ``unmarked`` is an ABSENCE of an observation.

    Once the read starts, the interval before it is a real measurement and is
    published as ``pre_handler`` — that is the "8.1 s went by before the read
    began" signal, and it is the only form in which the claim is supportable.
    """
    timeline = begin()
    with request_stages(timeline):
        mark(STAGE_EMBED_GET)
        spent = timeline.spent_ms()
        current = timeline.current()
    assert current == STAGE_EMBED_GET
    assert STAGE_PRE_HANDLER in spent
    assert STAGE_UNMARKED not in spent


def test_the_wire_view_publishes_once_a_stage_has_been_marked():
    timeline = begin()
    with request_stages(timeline):
        mark(STAGE_EMBED_ENCODE)
        wire = timeline.wire_snapshot()
    assert wire is not None
    assert wire["stage"] == STAGE_EMBED_ENCODE


def test_the_binding_does_not_leak_out_of_its_block():
    timeline = begin()
    with request_stages(timeline):
        assert stage_view() is not None
    assert stage_view() is None


# ── 4. the refusal is ADDITIVE on both surfaces ────────────────────────────

def test_the_rest_refusal_is_byte_identical_when_no_timeline_is_bound():
    """The unbound shape must not move at all: callers that know nothing about
    stages are the overwhelming majority and #3834's contract is theirs."""
    messages: list = []
    asyncio.run(ha._send_wait_bound_refusal(
        _collect_send(messages), {"type": "http", "headers": [], "method": "GET"},
        "/v1/search"))
    body = _decode(messages)
    assert set(body) == {"detail"}
    assert body["detail"] == _TRANSPORT_WAIT_BOUND_MESSAGE


def test_the_rest_refusal_names_the_stage_when_one_is_bound():
    messages: list = []
    asyncio.run(ha._send_wait_bound_refusal(
        _collect_send(messages), {"type": "http", "headers": [], "method": "GET"},
        "/v1/search",
        stages={"stage": STAGE_EMBED_ENCODE,
                "stages_ms": {STAGE_EMBED_GET: 12.0}}))
    body = _decode(messages)
    # The shipped `detail` is unchanged — the stage rides ADDITIVELY beside it.
    assert body["detail"] == _TRANSPORT_WAIT_BOUND_MESSAGE
    assert body["stage"] == STAGE_EMBED_ENCODE
    assert body["stages_ms"] == {STAGE_EMBED_GET: 12.0}


def test_the_rest_body_is_untouched_for_a_timeline_that_never_started():
    """The gate the shipped pinned-shape tests rely on: a timeline whose read
    never marked a stage must NOT reach the body."""
    timeline = begin()
    with request_stages(timeline):
        view = timeline.wire_snapshot()
    messages: list = []
    asyncio.run(ha._send_wait_bound_refusal(
        _collect_send(messages), {"type": "http", "headers": [], "method": "GET"},
        "/v1/search", stages=view))
    assert set(_decode(messages)) == {"detail"}


def test_a_marked_rest_refusal_is_still_recognised_by_the_verdict_contract():
    """Review finding F2 — the assertion this diff was missing.

    ``tests/_verdict.py::is_transport_wait_bound_refusal`` used EXACT body
    equality for the REST arm. A breached **/v1/search** that had already marked
    a stage is the one request class this instrument exists for, and it emitted
    two extra keys — so the detector stopped recognising it and handed the
    refusal to the caller's assertions as if it were an ANSWER: a false FAIL
    charged to the property under test, which is the failure class that module's
    rule-1 INCONCLUSIVE arm exists to prevent.

    Driven through the REAL builder and the REAL detector, not a hand-written
    body, because the defect was precisely that the two disagreed.
    """
    from tests import _verdict

    class _Resp:
        status_code = 504

        def __init__(self, body: bytes) -> None:
            self.content = body

    messages: list = []
    asyncio.run(ha._send_wait_bound_refusal(
        _collect_send(messages), {"type": "http", "headers": [], "method": "GET"},
        "/v1/search",
        stages={"stage": STAGE_RETRIEVAL,
                "stages_ms": {STAGE_EMBED_GET: 40.0}}))
    body = messages[-1]["body"]
    assert "stage" in body.decode(), "precondition: the body carries the attribution"
    assert _verdict.is_transport_wait_bound_refusal(_Resp(body)) is True


def test_an_unrelated_extra_key_is_still_not_the_refusal():
    """The widening is a documented ALLOWLIST, not a wildcard: the #8579 rule —
    "never the message text alone" — still holds."""
    from tests import _verdict

    class _Resp:
        status_code = 504
        content = json.dumps({"detail": _TRANSPORT_WAIT_BOUND_MESSAGE,
                              "something_else": 1}).encode()

    assert _verdict.is_transport_wait_bound_refusal(_Resp()) is False


def test_the_jsonrpc_refusal_keeps_its_envelope_and_gains_the_stage():
    """``code`` + the exact neutral ``message`` is the refusal's IDENTITY
    (``tests/_verdict.py::is_transport_wait_bound_refusal``). The stage goes in
    ``data``, beside ``retry_after``, and must not touch either half."""
    messages: list = []
    asyncio.run(ha._send_wait_bound_refusal(
        _collect_send(messages), {"type": "http", "headers": [], "method": "POST"},
        "/mcp", stages={"stage": STAGE_RETRIEVAL, "stages_ms": {}}))
    error = _decode(messages)["error"]
    assert error["code"] == -32009
    assert error["message"] == _TRANSPORT_WAIT_BOUND_MESSAGE
    assert error["data"]["stage"] == STAGE_RETRIEVAL
    assert "retry_after" in error["data"]


def test_the_mcp_tool_refusal_carries_the_stage_without_touching_its_envelope():
    from tortoise.mcp_server import _wait_bound_refusal_result

    plain = _wait_bound_refusal_result("tortoise_search")
    staged = _wait_bound_refusal_result(
        "tortoise_search",
        stages={"stage": STAGE_EMBED_ENCODE, "stages_ms": {STAGE_EMBED_GET: 4.0}})

    plain_err = plain.structured_content["error"]
    staged_err = staged.structured_content["error"]
    assert staged_err["code"] == plain_err["code"]
    assert staged_err["message"] == plain_err["message"] == _TRANSPORT_WAIT_BOUND_MESSAGE
    assert "stage" not in plain_err["data"]      # additive only
    assert staged_err["data"]["stage"] == STAGE_EMBED_ENCODE
    assert staged_err["data"]["stages_ms"] == {STAGE_EMBED_GET: 4.0}
    assert staged_err["data"]["retry_after"] == plain_err["data"]["retry_after"]


def test_a_write_refusal_carries_the_stage_beside_its_committed_verdict():
    """``stage`` is a fact about the WAIT; ``committed`` is a fact about the
    WRITE. They cannot contradict, so both ride — the write arm included."""
    from tortoise.mcp_server import _wait_bound_refusal_result

    staged = _wait_bound_refusal_result(
        "tortoise_create_point", dispatched=False,
        stages={"stage": STAGE_PRE_HANDLER, "stages_ms": {}})
    data = staged.structured_content["error"]["data"]
    assert data["stage"] == STAGE_PRE_HANDLER
    assert data["committed"] == "none"


# ── 5. the analytics row ────────────────────────────────────────────────────

def test_the_breach_telemetry_carries_the_stage_attribution(monkeypatch):
    """The row that exists to explain ``latency_ms`` must also explain WHAT the
    latency was. Captured at the one emit site's own writer."""
    captured: dict = {}

    real = ha._track_analytics_event

    def _fake(org_id, event, props):
        captured["event"] = event
        captured["props"] = dict(props)
        return real(org_id, event, props)

    # The emit dispatches to the telemetry pool; drive it synchronously instead
    # of racing a daemon thread.
    monkeypatch.setattr(ha, "_track_analytics_event", _fake)
    monkeypatch.setattr(
        "tortoise.monitoring.control_plane_worker",
        lambda _name: type("W", (), {"submit": staticmethod(
            lambda fn: (fn(), _Done())[1])})())

    ha._emit_wait_bound_breach(
        "org", "/v1/search", "GET", 24000,
        stages={"stage": STAGE_EMBED_ENCODE,
                "stages_ms": {STAGE_EMBED_GET: 8123.0}})

    assert captured["props"]["latency_ms"] == 24000
    assert captured["props"]["read_stage"] == STAGE_EMBED_ENCODE
    assert captured["props"]["read_stages_ms"] == {STAGE_EMBED_GET: 8123.0}


def test_the_breach_telemetry_emits_no_stage_keys_when_none_is_bound(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: captured.update(props=dict(props)))
    monkeypatch.setattr(
        "tortoise.monitoring.control_plane_worker",
        lambda _name: type("W", (), {"submit": staticmethod(
            lambda fn: (fn(), _Done())[1])})())

    ha._emit_wait_bound_breach("org", "/v1/search", "GET", 24000)

    assert "read_stage" not in captured["props"]
    assert "read_stages_ms" not in captured["props"]


def test_the_stage_props_are_allowlisted():
    """An unregistered prop is stripped in silence (#3821) — the #3359 loss
    mode. These two carry the whole point of the change."""
    assert "read_stage" in ha._ALLOWED_ANALYTICS_PROPS
    assert "read_stages_ms" in ha._ALLOWED_ANALYTICS_PROPS
    assert STAGES, "the vocabulary must be non-empty for the claim above"


class _Done:
    """Stand-in for the future ``_emit_wait_bound_breach`` tracks."""

    def add_done_callback(self, _cb):
        return None

    def cancelled(self):
        return False

    def exception(self):
        return None


# ── 6. the read path actually marks ────────────────────────────────────────

def _marks_inside_fts_query() -> set[str]:
    """AST pass: the ``_mark_read_stage(...)`` call sites in ``tortoise_fts_query``.

    Structural on purpose. The alternative is to drive a real hybrid read, which
    needs a live graph AND the embeddings extra — and would then pass on a tree
    where the encode mark had been dropped from the one path that matters, if the
    test's own fixture happened to take a different branch. What this pins is the
    shape a refactor silently loses.
    """
    tree = ast.parse(_SDK.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "tortoise_fts_query":
            marks: set[str] = set()
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "_mark_read_stage"
                        and inner.args
                        and isinstance(inner.args[0], ast.Name)):
                    marks.add(inner.args[0].id)
            return marks
    raise AssertionError("tortoise_fts_query not found in tortoise/sdk.py")


def test_the_search_path_marks_every_stage_the_issue_asked_about():
    """The issue's own question — "which stage exceeds the budget: FTS, the
    embedding call, the vector query, or the RRF merge" — is answerable only if
    each of those is marked. A missing mark is a stage the breach can never name."""
    marked = _marks_inside_fts_query()
    for constant in ("STAGE_EMBED_GET", "STAGE_EMBED_ENCODE", "STAGE_RETRIEVAL",
                     "STAGE_FUSION", "STAGE_EP_ANNOTATION", "STAGE_ORDERING",
                     "STAGE_RESPONSE"):
        assert constant in marked, f"{constant} is never marked in tortoise_fts_query"


def test_the_middleware_binds_the_timeline_around_the_handler_task():
    """The binding must straddle ``ensure_future`` — a timeline bound AFTER the
    task is created is not in the task's context, and every breach would then
    publish ``pre_handler`` for a read that ran perfectly."""
    source = (_REPO_ROOT / "tortoise" / "hosted_api.py").read_text(encoding="utf-8")
    bind = source.index("_bind_read_stages(request_stages)")
    spawn = source.index(
        "task = asyncio.ensure_future(self.app(scope, receive, _guarded_send))")
    assert bind < spawn, "the read-stage binding must precede the task creation"
