"""#14219 (F4): the batch-ingest layer — acceptance, per-item state, dedup,
resume, cancel, estimate and the isolation bound.

Every test here is written against PRE-FIX code that has no
``tortoise.batch_ingest`` module at all, so the module import alone fails them
(non-vacuity by construction); each one then pins a behaviour the brief names as
acceptance, and the docstring says which.
"""
from __future__ import annotations

import os
import re
import threading

import pytest

from tortoise import batch_ingest as bi_module
from tortoise.batch_ingest import (
    ACCEPT_COPY,
    ACKNOWLEDGEMENT_TOKEN_THRESHOLD,
    BATCH_ITEMS_IN_FLIGHT,
    BATCH_LABEL,
    BATCH_RECLAIM_AFTER_S,
    ITEM_LABEL,
    OVERAGE_USD_PER_MILLION_TOKENS,
    PROVIDER_MECHANICS_WORDS,
    SECONDS_PER_DOCUMENT,
    TERMINAL_BATCH_STATES,
    TERMINAL_ITEM_STATES,
    TOKENS_PER_MEDIAN_DOCUMENT,
    Batch,
    BatchCoordinator,
    BatchItem,
    BatchItemState,
    BatchState,
    BatchWorker,
    GraphBatchStore,
    IllegalTransition,
    InMemoryBatchStore,
    ItemOutcome,
    acceptance_receipt,
    content_key,
    customer_copy,
    deferred_count,
    estimate_batch,
    item_counts,
    normalise_text,
    runner_result_to_outcome,
    status_body,
)
from tortoise.batch_ingest import (
    _age_seconds as bi_age,
)

ORG = "org_test"


# ── the state machine ──────────────────────────────────────────────────────
def test_terminal_states_have_no_outgoing_edges():
    """A terminal item can never be moved again — including by a resume.

    Pinned because the failure it prevents is silent: an `indexed` item moved
    back to `queued` re-extracts and re-spends, and an item moved out of
    `cancelled` resurrects work the customer stopped.
    """
    from tortoise.batch_ingest import ITEM_TRANSITIONS

    for state in TERMINAL_ITEM_STATES:
        assert ITEM_TRANSITIONS[state] == frozenset(), (
            f"{state} is terminal but declares outgoing edges")


def test_the_resume_edge_exists_and_only_from_in_flight_states():
    from tortoise.batch_ingest import ITEM_TRANSITIONS

    assert BatchItemState.QUEUED in ITEM_TRANSITIONS[BatchItemState.EXTRACTING]
    assert BatchItemState.QUEUED in ITEM_TRANSITIONS[BatchItemState.EMBEDDED]
    # …and NOT from queued itself (that would be a silent spin) or terminal.
    assert BatchItemState.QUEUED not in ITEM_TRANSITIONS[BatchItemState.QUEUED]


def test_an_illegal_transition_is_refused_loudly_not_coerced():
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    _, items = coord.submit(ORG, ["a"])
    coord.advance(ORG, items[0], BatchItemState.CANCELLED, reason="stop")
    with pytest.raises(IllegalTransition):
        coord.advance(ORG, items[0], BatchItemState.EXTRACTING)


def test_a_noop_transition_is_accepted_and_writes_nothing():
    """A retried write after an ambiguous failure must not be an error."""
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    _, items = coord.submit(ORG, ["a"])
    before = coord.status(ORG, items[0].batch_id)[1][0].updated_at
    coord.advance(ORG, items[0], BatchItemState.QUEUED)
    after = coord.status(ORG, items[0].batch_id)[1][0].updated_at
    assert before == after


# ── content identity (brief item 3) ────────────────────────────────────────
def test_identical_bytes_are_identical_and_a_changed_word_is_not():
    """The dedup definition: sha256 of the normalised UTF-8 text.

    The second half is the #13451 trap — a CHANGED file must not collide with
    the old one, because a collision here means "skip" over a document whose
    stored vector still describes the previous text.
    """
    a = content_key("# Title\n\none two three\n")
    assert a == content_key("# Title\n\none two three\n")
    assert a != content_key("# Title\n\none two four\n")
    # Any byte difference that is not a line ending is a difference.
    assert a != content_key("# Title\n\none  two three\n")


def test_line_endings_are_not_content():
    """CRLF vs LF is the same document — the repo's own rule.

    ``file_indexer.hash_text`` hashes the buffer as given and documents that the
    CRLF immunity lives on the universal-newline read; a batch that hashed the
    raw bytes would re-extract every Windows-uploaded file forever.
    """
    lf = "# T\n\nalpha beta\n"
    crlf = "# T\r\n\r\nalpha beta\r\n"
    assert normalise_text(crlf) == lf
    assert content_key(crlf) == content_key(lf)


def test_content_key_matches_the_repo_precedent(tmp_path):
    """One definition, not three: the key equals what the INDEXER writes.

    ⛔ Compared against the indexer's REAL READ PATH, not against
    ``hash_text(normalise_text(text))`` (#14415 review round 5, P3): that
    comparison re-applied this module's own normalisation on BOTH sides, so any
    sha256 body passed it — including one with no normalisation at all, which is
    exactly the divergence the test exists to catch (``hash_text`` does NOT
    normalise; ``compute_file_hash``'s text-mode read does).

    If these diverge, the batch reports "new" for documents the indexer calls
    "unchanged" (and vice versa) — two answers to one question.
    """
    from tortoise.file_indexer import compute_file_hash

    path = tmp_path / "doc.md"
    # CRLF on disk: the indexer normalises through its text-mode read, and the
    # batch must key the same document to the same hash.
    path.write_bytes(b"# Doc\r\n\r\nbody\r\n")
    stored = compute_file_hash(str(path))
    assert stored is not None
    assert content_key("# Doc\r\n\r\nbody\r\n") == stored
    assert content_key("# Doc\n\nbody\n") == stored, (
        "a CRLF file and its LF twin must be one document")


# ── duplicate-submit spends zero tokens ────────────────────────────────────
def test_a_settled_duplicate_never_calls_the_runner(tmp_path):
    """ACCEPTANCE: duplicate-submit spends ZERO tokens.

    The runner IS the spend (it is the extraction), so "the runner was not
    called" is the strongest available statement of "zero tokens".
    """
    store = InMemoryBatchStore()
    calls: list[str] = []

    def runner(item: BatchItem) -> ItemOutcome:
        calls.append(item.ref)
        return ItemOutcome(BatchItemState.INDEXED, tokens_in=64_000)

    coord = BatchCoordinator(store, runner)
    _, first = coord.submit(ORG, ["/docs/contract.md"])
    first_item = coord.status(ORG, first[0].batch_id)[1][0]
    coord.advance(ORG, first_item, BatchItemState.EXTRACTING)
    coord.advance(ORG, first_item, BatchItemState.EMBEDDED)
    coord.advance(ORG, first_item, BatchItemState.INDEXED,
                  content_key=content_key("# Contract\n\nterms\n"))

    # A SECOND batch, submitting the same bytes (the caller supplies the text,
    # so the identity is known at acceptance and no read is needed to see it).
    _, second = coord.submit(
        ORG, ["/other/path/to/the-same-contract.md"],
        contents=["# Contract\n\nterms\n"],
        stage_base=str(tmp_path))
    dup = coord.status(ORG, second[0].batch_id)[1][0]
    assert dup.content_key == content_key("# Contract\n\nterms\n")
    settled = coord.run_item(ORG, dup)

    assert settled.state is BatchItemState.SKIPPED_DUPLICATE
    assert "already indexed" in settled.reason
    assert settled.tokens_in == 0 and settled.tokens_out == 0
    assert calls == [], (
        "the runner must not have been called for the duplicate")


def test_the_dedup_check_precedes_the_runner_structurally(tmp_path):
    """Not a rule the runner remembers — an ordering the coordinator enforces.

    Pinned by a runner that FAILS the test if it is ever reached for a
    duplicate: a future refactor that moves the dedup check after the call
    cannot pass while this exists.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)

    def runner(item: BatchItem) -> ItemOutcome:
        raise AssertionError("runner called for a duplicate")

    _, items = coord.submit(ORG, ["/a.md"])
    it = coord.status(ORG, items[0].batch_id)[1][0]
    coord.advance(ORG, it, BatchItemState.EXTRACTING)
    coord.advance(ORG, it, BatchItemState.INDEXED,
                  content_key=content_key("same"))
    _, items2 = coord.submit(ORG, ["/b.md"], contents=["same"],
        stage_base=str(tmp_path))
    dup = coord.status(ORG, items2[0].batch_id)[1][0]
    assert coord.run_item(ORG, dup, runner).state is (
        BatchItemState.SKIPPED_DUPLICATE)


# ── one failing item leaves the batch usable (AX) ──────────────────────────
def test_one_failing_item_does_not_fail_the_batch():
    """ACCEPTANCE: a per-item failure is an OUTCOME, never a refusal.

    One bad file must not fail the other 499: the other items still reach a
    terminal success, and the batch reports `partial` — never `failed`.
    """
    store = InMemoryBatchStore()

    def runner(item: BatchItem) -> ItemOutcome:
        if item.ref == "/bad.md":
            return ItemOutcome(BatchItemState.FAILED,
                               reason="non-UTF-8 content (decode failure)")
        return ItemOutcome(BatchItemState.INDEXED)

    coord = BatchCoordinator(store, runner)
    batch, items = coord.submit(ORG, ["/ok1.md", "/bad.md", "/ok2.md"])
    for it in items:
        coord.run_item(ORG, it)

    settled = coord.settle(ORG, batch.batch_id)
    _, after = coord.status(ORG, batch.batch_id)
    counts = item_counts(after)
    assert counts["indexed"] == 2
    assert counts["failed"] == 1
    assert settled.state is BatchState.PARTIAL
    # AX: the failure carries a reason a customer can act on.
    bad = next(i for i in after if i.ref == "/bad.md")
    assert "decode" in bad.reason


def test_an_escaped_exception_is_a_failed_item_not_a_crash():
    store = InMemoryBatchStore()

    def runner(item: BatchItem) -> ItemOutcome:
        raise RuntimeError("the embedder exploded")

    coord = BatchCoordinator(store, runner)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    for it in items:
        coord.run_item(ORG, it)
    _, after = coord.status(ORG, batch.batch_id)
    assert all(i.state is BatchItemState.FAILED for i in after)
    assert "RuntimeError" in after[0].reason
    assert coord.settle(ORG, batch.batch_id).state is BatchState.PARTIAL


# ── resume (brief item 4) ──────────────────────────────────────────────────
def test_restart_requeues_in_flight_items_and_says_it_is_resuming():
    """ACCEPTANCE: restart resumes.

    An item found `extracting` at boot belongs to a process that is gone; its
    claim is not evidence of completion, so it returns to `queued` (attempt
    count KEPT, so a poison item cannot spin forever) and the batch state says
    `resuming` — the brief's "the batch's own status says what it is resuming".
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)

    def blocking(item: BatchItem) -> ItemOutcome:  # never actually used
        return ItemOutcome(BatchItemState.INDEXED)

    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, items[1], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, items[1], BatchItemState.INDEXED)

    # Simulate a restart: a NEW coordinator over the same durable store.
    # stale_after_s=0 says "treat any claim as void" — the restart case the test
    # is about. The DEFAULT window is exercised by the test below, and is what
    # stops a poll on one instance stealing another instance's live claim.
    resumed = BatchCoordinator(store, blocking).recover(ORG, stale_after_s=0)

    assert resumed == [batch.batch_id]
    fresh_batch, after = BatchCoordinator(store).status(ORG, batch.batch_id)
    assert fresh_batch.state is BatchState.RESUMING
    by_ref = {i.ref: i for i in after}
    assert by_ref["/a.md"].state is BatchItemState.QUEUED
    assert by_ref["/a.md"].attempts == 1, "the attempt count must survive"
    assert "resumed after a process restart" in by_ref["/a.md"].reason
    assert by_ref["/b.md"].state is BatchItemState.INDEXED, (
        "a terminal item must not be re-run by a resume")


def test_a_cancelled_batch_is_not_resumed_by_a_restart():
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.cancel(ORG, batch.batch_id)

    assert BatchCoordinator(store).recover(ORG) == []


def test_a_completed_batch_is_not_reported_as_pending():
    """A settled batch is never resumable — and an UNSETTLED one always is.

    ⛔ The definition is "unfinished is EITHER half" (#14415 review round 2): a
    batch is pending when its ROW is non-terminal, or when any item is. The
    second half is what lets `recover()` repair a row that lagged its items (a
    settle write that timed out, a process killed between the last item write
    and the settle) — that row used to be invisible to every path, including a
    fresh boot, and `cancel()` was the only call that would move it. So the
    assert below is the SETTLED state, which is the property that matters: after
    `settle()` nothing re-derives the batch, ever.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, items[0], BatchItemState.INDEXED)
    # Every item is terminal but the row has not settled yet: still pending, by
    # design — that is the lagging state `recover()` must be able to see.
    assert store.pending_batches(ORG) == [batch.batch_id]
    assert coord.settle(ORG, batch.batch_id).state is BatchState.COMPLETED
    assert store.pending_batches(ORG) == [], (
        "a settled batch must never be re-derived, or recovery would re-open "
        "finished work for ever")


def test_a_batch_that_never_ran_is_not_relabelled_resuming():
    """#14415 review round 13: `RESUMING` is minted only by a scan that RESUMED something.

    The scan used to write the batch row whenever it found any non-terminal item,
    so the FIRST sweep over a freshly accepted batch (every item `queued`,
    `attempts=0`, nothing claimed) relabelled it `resuming`: measured on the wire,
    `POST` answered `accepted` and the first `GET` answered `resuming`. `RESUMING`'s
    whole job is "you can tell whether work was lost"; minting it over work that has
    simply not started is a status lie on a customer-visible surface.

    ⛔ BOTH DIRECTIONS, because either arm alone is satisfiable by a wrong fix:
      * "never relabel anything" is refuted by the dead-claim half below;
      * "never report it at all" is refuted by the batch still being RETURNED by
        the scan, which is what makes a later pass visit it.

    Measured as unpinned before this test: reinstating the pre-fix INNER condition
    (`if moved or any(i.state is BatchItemState.QUEUED for i in items):` — the outer
    gate was and is `if moved or any(not i.terminal for i in items):`) left every
    test in this file green (#14415 review round 14, MEDIUM; re-worded in cycle 2,
    LOW, because the first cut of this sentence named the OUTER gate, which the
    change never touched, and a test count the file no longer carries).
    """
    store = InMemoryBatchStore()
    batch, items = BatchCoordinator(store).submit(ORG, ["/a.md", "/b.md"])
    assert all(i.state is BatchItemState.QUEUED for i in items), (
        "the fixture must be a batch nothing has claimed — work that has not "
        "started, not work to recover")

    # NOTHING moved, and the batch is still REPORTED: "never report it" is not
    # the fix, it would strand the batch.
    resumed = BatchCoordinator(store).recover(ORG, stale_after_s=0)
    assert resumed == [batch.batch_id], (
        f"the scan stopped reporting a batch with queued work: {resumed}")
    fresh, after = BatchCoordinator(store).status(ORG, batch.batch_id)
    assert fresh.state is BatchState.ACCEPTED, (
        "the first sweep relabelled a batch that never ran as `resuming` — the "
        f"customer is told work is being RECOVERED when it has not started: {fresh.state}")
    assert all(i.state is BatchItemState.QUEUED for i in after)

    # …and the SAME batch with a genuinely dead claim DOES say `resuming`, so the
    # rule above cannot be satisfied by never relabelling anything.
    dead = store.get_item(ORG, batch.batch_id, items[0].item_id)
    assert dead is not None
    dead.state = BatchItemState.EXTRACTING
    dead.updated_at = "2020-01-01T00:00:00+00:00"
    store.put_item(ORG, dead)
    assert BatchCoordinator(store).recover(ORG, stale_after_s=0) == [batch.batch_id]
    recovered, after = BatchCoordinator(store).status(ORG, batch.batch_id)
    assert recovered.state is BatchState.RESUMING, (
        "a batch whose dead claim WAS requeued must say `resuming` — otherwise "
        f"the customer cannot tell work was recovered: {recovered.state}")
    by_ref = {i.ref: i for i in after}
    assert by_ref["/a.md"].state is BatchItemState.QUEUED
    assert by_ref["/b.md"].state is BatchItemState.QUEUED


# ── cancel (brief item 6) ──────────────────────────────────────────────────
def test_cancel_stops_new_work_and_keeps_completed_work():
    """ACCEPTANCE: cancel stops new work and keeps completed work.

    The billing rule this pins: an item is charged iff a runner RAN for it. A
    queued item is cancelled with zero tokens; an already-indexed item keeps
    its spend and its result (hiding a real spend would be the worse defect).
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/done.md", "/todo1.md", "/todo2.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, items[0], BatchItemState.INDEXED,
                  tokens_in=40_000, tokens_out=20_000)

    cancelled, after = coord.cancel(ORG, batch.batch_id, reason="customer stop")

    assert cancelled.state is BatchState.CANCELLED
    by_ref = {i.ref: i for i in after}
    assert by_ref["/done.md"].state is BatchItemState.INDEXED
    assert by_ref["/done.md"].tokens_in + by_ref["/done.md"].tokens_out == 60_000
    assert by_ref["/todo1.md"].state is BatchItemState.CANCELLED
    assert by_ref["/todo2.md"].state is BatchItemState.CANCELLED
    assert by_ref["/todo1.md"].tokens_in == 0
    assert "customer" in by_ref["/todo1.md"].reason


def test_a_cancel_during_an_in_flight_item_keeps_that_item_s_result():
    """The documented billing rule, pinned: an item a runner RAN is kept.

    Cancel stops FUTURE work. An item already inside the model call has spent
    its tokens; discarding its result would spend them and throw the answer
    away, which is worse for both sides. The batch therefore stays `cancelled`
    while this one item completes and is charged.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/slow.md", "/never.md"])

    def slow(item: BatchItem) -> ItemOutcome:
        # The customer cancels WHILE the model call is in flight.
        coord.cancel(ORG, batch.batch_id, reason="stopped mid-run")
        return ItemOutcome(BatchItemState.INDEXED, tokens_in=40_000,
                           tokens_out=20_000)

    out = coord.run_item(ORG, items[0], slow)
    assert out.state is BatchItemState.INDEXED
    assert out.tokens_in + out.tokens_out == 60_000
    after = {i.ref: i for i in coord.status(ORG, batch.batch_id)[1]}
    assert after["/slow.md"].state is BatchItemState.INDEXED
    assert after["/never.md"].state is BatchItemState.CANCELLED
    assert after["/never.md"].tokens_in == 0
    assert coord.settle(ORG, batch.batch_id).state is BatchState.CANCELLED


def test_orphan_adoption_never_wipes_the_estimate_the_ack_carried():
    """⛔ RECUT REVIEW P2: the FOURTH writer of the batch row was still unconditional.

    `submit()` writes the item rows BEFORE the batch row on purpose (an ACK must
    never name a document we did not record), and `pending_batches()` UNIONs the
    ITEM arm — so a recovery scan running while an acceptance is inside its item
    loop sees items with no batch row and reads `get_batch -> None`. It then
    constructed a `Batch(estimate={}, corpus_name="")` and called `put_batch`, an
    UNCONDITIONAL `MERGE ... SET` over `estimate_json`, `corpus_name`,
    `cancelled_at`: the estimate owner §4 requires on the ACK was WIPED and the
    batch relabelled `resuming` over work that never stopped. The three other
    writers of that row were each hardened to a compare-and-set; this one was not.

    The stale read is injected (`get_batch` returns `None` once, exactly what the
    scan saw), so this reproduces the window without threads and FAILS on the
    pre-fix code.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    # ⛔ The estimate must be NON-EMPTY or this test is vacuous: `submit()` with no
    # estimate already stores `{}`, so comparing `{} == {}` would pass over the
    # very wipe it exists to catch (measured: it did).
    estimate = {"items": 2, "tokens_per_item": 64799,
                "estimated_tokens": 129598, "included_tokens": None,
                "overage_tokens": 0, "overage_usd": 0.0}
    batch, _items = coord.submit(ORG, ["/d/a.md", "/d/b.md"],
                                 corpus_name="handbook", estimate=estimate)
    before = store.get_batch(ORG, batch.batch_id)
    assert before is not None
    assert before.estimate, "the premise: the ACK wrote a non-empty estimate"
    assert before.corpus_name == "handbook", "the premise: the ACK wrote a corpus"

    real_get = store.get_batch
    fired = {"done": False}

    def stale_get(org_id, batch_id):
        if not fired["done"]:
            fired["done"] = True
            return None          # the read the scan took BEFORE the row was written
        return real_get(org_id, batch_id)

    store.get_batch = stale_get  # type: ignore[method-assign]
    coord.recover(ORG)

    assert fired["done"], "the stale read did not fire — the test proves nothing"
    after = real_get(ORG, batch.batch_id)
    assert after is not None, "the adoption DELETED the batch row"
    assert after.estimate == before.estimate, (
        "the adoption overwrote the ACK's estimate with an empty one — owner §4 "
        f"requires it on the record: {before.estimate!r} -> {after.estimate!r}")
    # ⛔ NOT asserted: `state is not RESUMING`. A genuine recovery of an accepted
    # batch that still has `queued` items SHOULD report `resuming` — that is the
    # feature, not the defect. What the adoption must never do is rewrite the
    # fields the ACK put on the record from an orphan-shaped (empty) Batch. The
    # measurement that isolates it is the estimate, the corpus name and the
    # creation stamp.
    assert after.corpus_name == before.corpus_name, (
        f"the adoption overwrote corpus_name: {before.corpus_name!r} -> "
        f"{after.corpus_name!r}")
    assert after.created_at == before.created_at, (
        "the adoption re-stamped created_at from an orphan item's updated_at, so "
        "the batch's own age is lost")


def test_a_state_cas_never_rewrites_the_estimate_the_ack_wrote():
    """⛔ RECUT-2 REVIEW P1: the estimate was erased one statement AFTER the fix.

    `_resume_batch` builds its `Batch` from an ORPHAN-ITEM snapshot (estimate `{}`,
    corpus `""`) whenever the batch row was not there when it read. Re-reading
    before adopting closed the `put_batch` window — but the resume's own
    compare-and-set then ran with that same empty object, and
    `put_batch_if_state` used to `SET b.estimate_json=$estimate,
    b.corpus_name=$corpus` FROM THE CALLER. An acceptance landing after the
    re-read was MATCHED (`submit()` writes `ACCEPTED`) and then overwritten with
    `{}` — the arithmetic owner §4 requires on the record, gone permanently, and
    `GET /v1/batches` reporting `estimate: {}`.

    ⛔ THIS TEST WAS VACUOUS ON ITS FIRST CUT AND THE FIX IS WHY IT NOW ISN'T. The
    resume CAS only runs when the scan actually MOVED something (`if moved:`), so
    an all-`queued` batch — the obvious fixture — never reaches
    `put_batch_if_state` at all: instrumented, the first cut measured
    `{'gets': 2, 'items': 2, 'cas': 0}`, and reinstating the pre-fix CAS left it
    green. One item is therefore left `EXTRACTING` with an ancient stamp so the
    requeue makes `moved` true, and `cas >= 1` is ASSERTED — otherwise this test
    silently becomes a no-op again the next time `_resume_batch` is reordered.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    estimate = {"items": 2, "tokens_per_item": 64799,
                "estimated_tokens": 129598, "included_tokens": None,
                "overage_tokens": 0, "overage_usd": 0.0}
    batch, items = coord.submit(ORG, ["/d/a.md", "/d/b.md"],
                                corpus_name="handbook", estimate=estimate)
    # ⛔ MODEL THE REAL CRASH, not just a stubbed read (#14415 cycle-2, HIGH).
    # The first cut left the batch ROW in the store and only made `get_batch`
    # answer `None`, so `put_batch_if_absent` found the row PRESENT and returned
    # False — `_resume_batch` then re-read the row the ACK wrote and its CAS carried
    # the REAL estimate, which is why reverting the fix left this test green
    # (measured: the whole file green). The shape being pinned is a submit that
    # wrote its ITEM rows and died before its batch row, so the row must genuinely
    # be absent here.
    del store._batches[(ORG, batch.batch_id)]
    # A dead claim, old enough that the resume window takes it — this is what
    # makes the scan call the CAS at all.
    stale_item = store.get_item(ORG, batch.batch_id, items[0].item_id)
    stale_item.state = BatchItemState.EXTRACTING
    stale_item.updated_at = "2020-01-01T00:00:00+00:00"
    store.put_item(ORG, stale_item)
    # ⛔ AND THE OTHER ITEM MUST BE STALE TOO (#14415 review round 22). The
    # adoption now refuses an orphan carrying any NON-TERMINAL item younger than
    # the reclaim window (that is an acceptance in flight, not a crash residue —
    # the root cause of the takeover this file pins elsewhere). A genuine crash
    # orphan is stale by construction, so a fixture that means "the process died"
    # backdates every row it left behind.
    still_queued = store.get_item(ORG, batch.batch_id, items[1].item_id)
    still_queued.updated_at = "2020-01-01T00:00:00+00:00"
    store.put_item(ORG, still_queued)

    real_items = store.items
    real_get = store.get_batch
    real_cas = store.put_batch_if_state
    state = {"gets": 0, "items": 0, "cas": 0}

    def counting_cas(b, expected, _real=real_cas):
        state["cas"] += 1
        state["caller_estimate"] = dict(b.estimate)
        return _real(b, expected)

    def stale_get(org_id, batch_id):
        state["gets"] += 1
        if state["gets"] <= 2:
            return None          # both the read and the re-read: row not seen yet
        return real_get(org_id, batch_id)

    def items_then_ack(org_id, batch_id):
        state["items"] += 1
        rows = real_items(org_id, batch_id)
        if state["items"] == 2:
            # The acceptance's own write lands here — after the adoption wrote,
            # before the resume CAS.
            store.put_batch(batch)
        return rows

    store.get_batch = stale_get  # type: ignore[method-assign]
    store.items = items_then_ack  # type: ignore[method-assign]
    store.put_batch_if_state = counting_cas  # type: ignore[method-assign]
    try:
        coord.recover(ORG)
    finally:
        store.put_batch_if_state = real_cas  # type: ignore[method-assign]

    assert state["gets"] >= 2 and state["items"] >= 2, (
        f"the injected window did not fire — proves nothing: {state}")
    assert state["cas"] >= 1, (
        "the resume compare-and-set never ran, so this test cannot see the very "
        f"write it exists for (the first cut of it was vacuous for this reason): {state}")
    assert state.get("caller_estimate") == {}, (
        "the CAS was reached with a NON-empty caller estimate, so the write under "
        "test is not the one this fixture means to pin — a later reordering could "
        f"restore the vacuous shape silently: {state}")
    after = real_get(ORG, batch.batch_id)
    assert after is not None
    assert after.estimate == estimate, (
        "a state compare-and-set rewrote the ACK's estimate from the caller's "
        f"orphan snapshot — owner §4 requires it on the record: {after.estimate!r}")
    assert after.corpus_name == "handbook", (
        f"the state CAS blanked corpus_name: {after.corpus_name!r}")


def test_the_batch_state_cas_does_not_name_the_record_identity():
    """The statement-text half of the same pin (#14415 recut-2 review, P1).

    The behavioural test above needs `moved` to be true to reach the CAS, which is
    a fixture property a reordering could silently remove. This one reads the
    emitted statement instead: a state transition must not SET `estimate_json` or
    `corpus_name` AT ALL, so re-adding them is red whatever the fixture does.
    """
    cas = [c for c, _p in _every_store_statement_pairs()
           if "MATCH (b:" in c and "WHERE b.state IN" in c]
    assert cas, "the batch state compare-and-set statement was not emitted"
    for cypher in cas:
        assert "estimate_json" not in cypher, (
            "a batch STATE change writes the estimate from the caller, which is "
            f"how an orphan-shaped snapshot erased the ACK's arithmetic: {cypher}")
        assert "corpus_name" not in cypher, (
            f"a batch STATE change writes the corpus name from the caller: {cypher}")


def test_a_restart_settles_what_a_cancelled_batch_left_in_flight():
    """A crash AFTER a cancel must not leave a permanently non-terminal row."""
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.cancel(ORG, batch.batch_id)
    # …the process dies here, leaving /a.md mid-flight inside a cancelled batch.
    # `stale_after_s=0` states the boot case explicitly: this process is gone, so
    # ITS claim is void no matter how young the stamp looks. (The default window
    # is for a PEER's claim, which a status poll cannot prove dead — see
    # `abandon_dead_claims`.)
    assert BatchCoordinator(store).recover(ORG, stale_after_s=0) == []
    # ⛔ ASSERT THE SETTLEMENT THIS CALL IS SUPPOSED TO MAKE (#14415 review
    # round 11, P3). This used to assert only `== []` and then age a row the call
    # had already settled — so it held whether or not that call did anything, and
    # its own comment ("a 0 s claim is deliberately left alone") described the
    # opposite of its own statement order. `stale_after_s=0` is the explicit
    # "the holder is gone" case (this process died), so the row must ALREADY be
    # settled here; a mutation making `recover()` ignore the argument left the old
    # version green.
    after_zero = {i.ref: i for i in coord.status(ORG, batch.batch_id)[1]}
    assert after_zero["/a.md"].state is BatchItemState.CANCELLED, (
        "the zero window is the boot case — nothing is running, so the claim is "
        f"void and must settle now: {after_zero['/a.md'].state}")
    assert after_zero["/a.md"].tokens_in == 0
    assert store.pending_batches(ORG) == []


def test_settle_never_resurrects_a_cancelled_batch():
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, _ = coord.submit(ORG, ["/a.md"])
    coord.cancel(ORG, batch.batch_id)
    assert coord.settle(ORG, batch.batch_id).state is BatchState.CANCELLED


# ── the estimate (brief item 6, owner §4) ──────────────────────────────────
def test_estimate_uses_the_measured_spend_unit_not_the_document_size():
    est = estimate_batch(100)
    assert est.tokens_per_item == TOKENS_PER_MEDIAN_DOCUMENT
    assert est.estimated_tokens == 100 * TOKENS_PER_MEDIAN_DOCUMENT
    assert est.estimated_hours == pytest.approx(
        100 * SECONDS_PER_DOCUMENT / 3600.0)


def test_estimate_reports_the_overage_against_the_same_allowance():
    """Decision (b): ONE budget — the estimate is measured against it."""
    # 46 documents is what a 3M allowance buys at the measured unit (#14295).
    est = estimate_batch(46, included_tokens=3_000_000)
    assert est.overage_tokens == 0
    est2 = estimate_batch(100, included_tokens=3_000_000)
    assert est2.overage_tokens == 100 * TOKENS_PER_MEDIAN_DOCUMENT - 3_000_000
    assert est2.overage_usd == pytest.approx(
        est2.overage_tokens / 1_000_000 * OVERAGE_USD_PER_MILLION_TOKENS)


def test_estimate_subtracts_what_the_window_already_spent():
    est = estimate_batch(46, included_tokens=3_000_000,
                         tokens_already_used=2_900_000)
    assert est.overage_tokens == 46 * TOKENS_PER_MEDIAN_DOCUMENT - 100_000


def test_an_undecided_allowance_is_null_not_zero_and_not_unlimited():
    """#14275's included amounts are still an open decision.

    No tier carries ``max_extraction_tokens`` in ``product/pricing.json`` yet,
    so the honest answer is ``null`` with no invented overage — NOT "unlimited"
    and not a made-up cap.
    """
    est = estimate_batch(10, included_tokens=None)
    assert est.included_tokens is None
    assert est.overage_tokens == 0
    assert est.overage_usd == 0.0


def test_a_large_batch_requires_acknowledgement_and_says_it_is_batched():
    small = estimate_batch(3)
    assert small.batch_processed is False
    assert small.acknowledgement_required is False
    big = estimate_batch(500)
    assert big.batch_processed is True
    assert big.acknowledgement_required is True
    assert big.estimated_tokens >= ACKNOWLEDGEMENT_TOKEN_THRESHOLD


def test_negative_inputs_are_refused():
    with pytest.raises(ValueError):
        estimate_batch(-1)


# ── customer copy (decision (d)) ───────────────────────────────────────────
def test_copy_is_the_owners_sentence_and_leaks_no_provider_mechanics():
    sentence = customer_copy(2.4)
    assert "batch processed" in sentence
    assert "completion expected in 3 hours" in sentence
    assert "cancel any time" in sentence
    lowered = sentence.lower()
    for word in PROVIDER_MECHANICS_WORDS:
        assert word not in lowered, (
            f"customer copy must never expose provider pricing mechanics "
            f"({word!r}) — owner ruling on #14275")
    # …and the template itself is auditable.
    lowered_template = ACCEPT_COPY.lower()
    for word in PROVIDER_MECHANICS_WORDS:
        assert word not in lowered_template


def test_copy_never_promises_less_than_an_hour():
    """A sub-hour estimate rounds UP, never down: a late promise beats a broken one."""
    assert "in 1 hour," in customer_copy(0.1), (
        "the sub-hour batch a customer actually sends must not read "
        f"'1 hours': {customer_copy(0.1)!r}")
    assert "in 2 hours," in customer_copy(1.2)
    assert "2 hours" in customer_copy(1.01)


def test_the_acceptance_receipt_is_an_accepted_write_verb():
    batch = Batch(batch_id="batch_x", org_id=ORG,
                  estimate=estimate_batch(20).as_dict())
    receipt = acceptance_receipt(batch, [], status_url="/v1/batches/batch_x")
    assert receipt["status"] == "accepted"
    assert receipt["protocol_version"] == "memory_write_v1"
    assert receipt["batch_id"] == "batch_x"
    assert receipt["status_url"] == "/v1/batches/batch_x"
    assert "batch processed" in receipt["message"]


# ── per-item status surface (brief item 2) ─────────────────────────────────
def test_status_body_counts_are_derived_from_the_item_rows():
    batch = Batch(batch_id="batch_x", org_id=ORG)
    items = [
        BatchItem(batch_id="batch_x", item_id="i0", ref="/a",
                  state=BatchItemState.INDEXED),
        BatchItem(batch_id="batch_x", item_id="i1", ref="/b",
                  state=BatchItemState.FAILED, reason="decode failure",
                  tokens_in=10, tokens_out=5),
        BatchItem(batch_id="batch_x", item_id="i2", ref="/c",
                  state=BatchItemState.QUEUED),
    ]
    body = status_body(batch, items)
    assert body["counts"]["indexed"] == 1
    assert body["counts"]["failed"] == 1
    assert body["counts"]["pending"] == 1
    assert body["counts"]["terminal"] == 2
    assert body["tokens_spent"] == 15
    # Every item is present with its reason, which is the AX requirement.
    assert [i["status"] for i in body["items"]] == [
        "indexed", "failed", "queued"]
    assert body["items"][1]["reason"] == "decode failure"


# ── acceptance is durable and fast (brief item 1, decision (a)) ────────────
def test_acceptance_writes_one_row_per_item_and_does_no_work():
    """500 items are ACKed without any extraction running.

    The runner is one that would record a call; acceptance must not reach it,
    which is what makes the ack a few writes instead of 500 model calls — the
    reason the 10 s bound (#3834) is never approached and never raised.
    """
    store = InMemoryBatchStore()
    calls: list[str] = []
    coord = BatchCoordinator(store, lambda i: calls.append(i.ref))
    batch, items = coord.submit(ORG, [f"/doc-{n}.md" for n in range(500)])
    assert len(items) == 500
    assert calls == []
    assert batch.state is BatchState.ACCEPTED
    assert len(store.items(ORG, batch.batch_id)) == 500
    assert all(i.state is BatchItemState.QUEUED for i in items)


def test_item_ids_are_unique_and_stable_within_a_batch():
    coord = BatchCoordinator(InMemoryBatchStore())
    _, items = coord.submit(ORG, ["/a.md", "/a.md", "/b.md"])
    ids = [i.item_id for i in items]
    assert len(set(ids)) == 3


# ── isolation bound (brief item 5) ─────────────────────────────────────────
def test_the_batch_worker_cannot_use_the_capture_pool():
    """The isolation claim, made structural.

    A batch item submitted to ``_CAPTURE_EXECUTOR`` would queue ahead of
    customer captures, and the "no read regression" claim would be prose. The
    worker takes its pool by construction, so a caller cannot smuggle the
    capture pool in through a default.
    """
    from concurrent.futures import ThreadPoolExecutor

    from tortoise import hosted_api

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        worker = BatchWorker(pool)
        assert worker.executor is pool
        # ⛔ AND THE PRODUCTION WORKER IS ASSERTED TOO (#14415 review round 4,
        # non-vacuity). The `is not _CAPTURE_EXECUTOR` arm below is about the
        # LOCAL object this test built, so no production edit could falsify it:
        # `_BATCH_WORKER = BatchWorker(_CAPTURE_EXECUTOR, …)` would have left
        # this test green. The object that names the claim is the module's own.
        assert hosted_api._BATCH_WORKER.executor is not (
            hosted_api._CAPTURE_EXECUTOR), (
            "the PRODUCTION batch worker was built on the capture pool")
        # ⛔ and the pool is ORDERED BEFORE the ceiling is enforced, so 2 is a
        # real bound rather than an intention.
        assert worker.ceiling == BATCH_ITEMS_IN_FLIGHT >= 1
    finally:
        pool.shutdown(wait=True)


def test_the_ceiling_defers_instead_of_dropping_or_refusing():
    """Backpressure: at the ceiling an item stays `queued`, nothing is lost."""
    from concurrent.futures import ThreadPoolExecutor

    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        worker = BatchWorker(pool, ceiling=1)
        started = threading.Event()

        def hold() -> None:
            started.set()
            release.wait(5)

        assert worker.submit(hold) is True
        assert started.wait(5)
        # At the ceiling: refused, but as a DEFERRAL (the item is durable).
        assert worker.submit(hold) is False
        release.set()
        pool.shutdown(wait=True)
    finally:
        pool.shutdown(wait=True)


def test_the_off_peak_gate_defers_and_is_counted():
    """Owner (c): batch work is off-peak only — and a deferral is observable.

    A gate that refuses every hour must not be indistinguishable from a worker
    that is not running (the inert-enforcer shape), so the deferral is counted.
    """
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=1)
    before = deferred_count()
    try:
        worker = BatchWorker(pool, ceiling=1, gate=lambda: False)
        assert worker.submit(lambda: None) is False
        assert deferred_count() == before + 1
    finally:
        pool.shutdown(wait=True)


def test_a_raising_gate_defers_rather_than_spending():
    from concurrent.futures import ThreadPoolExecutor

    def boom() -> bool:
        raise RuntimeError("scheduler down")

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        worker = BatchWorker(pool, ceiling=1, gate=boom)
        assert worker.submit(lambda: None) is False
    finally:
        pool.shutdown(wait=True)


# ── the indexer translation (one meaning of "duplicate") ───────────────────
def test_indexer_results_map_to_item_states_without_drift():
    assert runner_result_to_outcome(
        {"status": "indexed"}).state is BatchItemState.INDEXED

    changed = runner_result_to_outcome({"status": "updated"})
    assert changed.state is BatchItemState.INDEXED
    assert changed.content_changed is True, (
        "#13451: changed text is RE-WRITTEN and re-embedded, never skipped")

    dup = runner_result_to_outcome(
        {"status": "skipped", "skipped_reason": "unchanged"})
    assert dup.state is BatchItemState.SKIPPED_DUPLICATE
    assert "no tokens spent" in dup.reason

    held = runner_result_to_outcome(
        {"status": "skipped", "skipped_reason": "lock-held", "retryable": True})
    assert held.state is BatchItemState.QUEUED, (
        "a retryable skip is NOT a terminal outcome — dropping it loses the file")

    failed = runner_result_to_outcome(
        {"status": "failed", "reason": "non-UTF-8 content"})
    assert failed.state is BatchItemState.FAILED
    assert "non-UTF-8" in failed.reason


# ── the graph store speaks the tenant's own labels ─────────────────────────
class _FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class _Recorder:
    """A query callable that records statements and answers from a script."""

    def __init__(self, answers=None):
        self.statements: list[str] = []
        self.params: list[dict] = []
        self._answers = list(answers or [])

    def __call__(self, cypher, params=None, timeout=None):
        self.statements.append(cypher)
        self.params.append(dict(params or {}))
        if self._answers:
            return _FakeResult(self._answers.pop(0))
        return _FakeResult([])


def test_graph_store_writes_and_reads_the_batch_with_the_org_scope():
    rec = _Recorder()
    store = GraphBatchStore(rec)
    batch = Batch(batch_id="batch_1", org_id=ORG, created_at="2026-11-23T00:00:00Z",
                  updated_at="2026-11-23T00:00:00Z",
                  estimate={"items": 2})
    store.put_batch(batch)
    cypher, params = rec.statements[-1], rec.params[-1]
    assert "IngestBatch" in cypher
    assert params["org"] == ORG and params["bid"] == "batch_1"
    assert "estimate_json" in cypher

    rec2 = _Recorder(answers=[[["accepted", "t0", "t0", '{"items":2}',
                                None, None, "corpus"]]])
    got = GraphBatchStore(rec2).get_batch(ORG, "batch_1")
    assert got and got.batch_id == "batch_1"
    assert got.estimate == {"items": 2}


def test_graph_store_resume_query_screens_out_terminal_states():
    rec = _Recorder()
    GraphBatchStore(rec).pending_batches(ORG)
    cypher, params = rec.statements[-1], rec.params[-1]
    assert "NOT i.state IN" in cypher
    assert set(params["terminal"]) == {str(s) for s in TERMINAL_ITEM_STATES}


def test_the_resume_query_selects_the_lagging_batch_row_too():
    """⛔ #14415 review round 2: "unfinished" is EITHER half, in the GRAPH too.

    The in-memory store and the graph store must answer the same question or the
    repair only works in tests. A batch whose items are all terminal but whose
    row is not is unfinished (the settle write timed out, or the process died
    before it), and it is invisible to an item-keyed query — which is how a
    charged, fully-indexed batch came to be relabelled by `cancel()`.
    """
    rec = _Recorder()
    GraphBatchStore(rec).pending_batches(ORG)
    cypher, params = rec.statements[-1], rec.params[-1]
    assert BATCH_LABEL in cypher and "b.state" in cypher, (
        "the batch row must be selected, not only the item rows")
    assert set(params["batch_terminal"]) == {str(s) for s in TERMINAL_BATCH_STATES}
    assert set(params["terminal"]) == {str(s) for s in TERMINAL_ITEM_STATES}


def test_an_unknown_stored_state_reads_as_queued_not_as_terminal():
    """Version skew must never silently settle an item as done."""
    rec = _Recorder(answers=[[[
        "item_1", "/a.md", "", "from-a-future-release", "", "", 0, 0, 0,
        False, "t0"]]])
    items = GraphBatchStore(rec).items(ORG, "batch_1")
    assert items[0].state is BatchItemState.QUEUED


# ── in-batch repeats: marked at acceptance, settled when the twin lands ────
def test_an_in_batch_repeat_settles_only_after_its_twin_is_indexed(tmp_path):
    """The same bytes twice in one upload is one document — but a SKIP is only
    safe once the first copy actually landed (otherwise the document is lost)."""
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    _batch, items = coord.submit(ORG, ["/a.md", "/a-copy.md"],
                                 contents=["same text", "same text"],
        stage_base=str(tmp_path))
    assert items[1].duplicate_of == items[0].item_id

    # While the twin is still queued, the copy PROCEEDS (not a duplicate of an
    # existing document yet).
    assert coord.run_item(ORG, items[1], lambda i: ItemOutcome(
        BatchItemState.INDEXED, content_key=content_key("same text"))
    ).state is BatchItemState.INDEXED


def test_an_in_batch_repeat_is_skipped_when_the_twin_indexed(tmp_path):
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    _batch, items = coord.submit(ORG, ["/a.md", "/a-copy.md"],
                                 contents=["same text", "same text"],
        stage_base=str(tmp_path))
    coord.run_item(ORG, items[0], lambda i: ItemOutcome(
        BatchItemState.INDEXED, content_key=content_key("same text")))
    calls: list[str] = []
    out = coord.run_item(ORG, items[1], lambda i: calls.append(i.ref) or (
        ItemOutcome(BatchItemState.INDEXED)))
    assert out.state is BatchItemState.SKIPPED_DUPLICATE
    assert calls == []
    assert out.tokens_in == 0


def test_a_copy_is_not_skipped_when_its_twin_failed(tmp_path):
    """Never lose a document to a dedup rule: a failed twin makes the copy live."""
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    _batch, items = coord.submit(ORG, ["/a.md", "/a-copy.md"],
                                 contents=["same text", "same text"],
        stage_base=str(tmp_path))
    coord.run_item(ORG, items[0], lambda i: ItemOutcome(
        BatchItemState.FAILED, reason="decode failure"))
    out = coord.run_item(ORG, items[1], lambda i: ItemOutcome(
        BatchItemState.INDEXED))
    assert out.state is BatchItemState.INDEXED


# ── inline (uploaded) text is staged before the row ────────────────────────
def test_inline_text_is_written_before_the_row_exists(tmp_path):
    """An ACK must never name a document we cannot read back.

    Acceptance stages the bytes under the ingest base FIRST and then writes the
    row, so a crash between the two leaves an orphan file (harmless) rather than
    a row pointing at nothing.
    """
    from pathlib import Path

    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["upload-1.md"], contents=["# Doc\n\nhi\n"],
                                stage_base=str(tmp_path))
    staged = Path(items[0].staged_path)
    assert staged.is_file()
    assert staged.read_text(encoding="utf-8") == "# Doc\n\nhi\n"
    assert str(tmp_path) in str(staged)
    # …and the path is derived from OUR ids, never from caller input: a hostile
    # item_id/name cannot escape the base.
    assert staged.parent.name == batch.batch_id


def test_a_path_item_is_not_staged(tmp_path):
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    _batch, items = coord.submit(ORG, ["/corpus/a.md"], stage_base=str(tmp_path))
    assert items[0].staged_path == ""
    assert list(tmp_path.rglob("*.md")) == []


def test_inline_text_without_storage_is_refused_rather_than_acked(tmp_path):
    """No base = nowhere durable to put the text = refuse, never accept."""
    coord = BatchCoordinator(InMemoryBatchStore())
    with pytest.raises(ValueError, match="TORTOISE_INGEST_BASE_DIR"):
        coord.submit(ORG, ["a.md"], contents=["text"], stage_base=None)


# ── a drain is linear in items, not quadratic ──────────────────────────────
def test_the_coordinator_reads_one_item_row_per_step_not_the_whole_batch(
        monkeypatch):
    """A 500-item batch must not re-list 500 rows per item.

    `_fresh` re-reads the item it is about to write (a cancel or a peer drain may
    have moved it). Doing that by listing the batch is ~250 000 materialised rows
    for 500 items; one row per call is what makes the batch scale.
    """
    store = InMemoryBatchStore()
    calls = {"get_item": 0, "items": 0}
    real_get = store.get_item
    real_items = store.items

    def _get(org_id, batch_id, item_id):
        calls["get_item"] += 1
        return real_get(org_id, batch_id, item_id)

    def _items(org_id, batch_id):
        calls["items"] += 1
        return real_items(org_id, batch_id)

    monkeypatch.setattr(store, "get_item", _get)
    monkeypatch.setattr(store, "items", _items)

    coord = BatchCoordinator(store)
    _batch, items = coord.submit(ORG, [f"/d/{n}.md" for n in range(50)])
    for it in items:
        coord.run_item(ORG, it, lambda i: ItemOutcome(BatchItemState.INDEXED))

    assert calls["items"] <= 2, (
        f"the batch was listed {calls['items']} times for 50 items — the drain "
        "must read rows, not re-list the batch")
    assert calls["get_item"] >= 50


# ── #14415 review round 1: the four blocking findings, pinned ──────────────
def test_recover_does_not_requeue_a_batch_a_live_drain_owns():
    """⛔ B1: a STATUS POLL must not destroy the in-flight item's outcome.

    "On boot" is implemented by the caller as "the first time this process
    touches the tenant's batch surface", and the first touch in the documented
    flow is the customer polling `/v1/batches/{id}` — while item 1 is inside the
    runner call and its row reads `extracting`. Requeueing that claim rewrote the
    row to `queued`, so when the runner returned, `queued -> indexed` was an
    illegal edge, the outcome was DISCARDED (tokens spent, document in the graph,
    row reporting pending with zero tokens) and nothing re-drove it.

    The premise of the resume edge is "the process that claimed this is gone", so
    the caller now names the batches a live drain owns and those claims are not
    void.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    live_batch, live_items = coord.submit(ORG, ["/live.md"])
    dead_batch, dead_items = coord.submit(ORG, ["/dead.md"])
    coord.advance(ORG, live_items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, dead_items[0], BatchItemState.EXTRACTING, attempt=True)

    # `stale_after_s=0`: the dead batch's claim is void *now* — the window only
    # matters cross-process, where a fresh claim may belong to a live peer (see
    # `test_recover_does_not_steal_a_live_claim`).
    resumed = coord.recover(ORG, live_batches={live_batch.batch_id},
                           stale_after_s=0)

    assert resumed == [dead_batch.batch_id], (
        "only the batch whose claim belongs to a process that is gone")
    by_ref = {i.ref: i for i in store.items(ORG, live_batch.batch_id)}
    assert by_ref["/live.md"].state is BatchItemState.EXTRACTING, (
        "a live drain's claim must be left exactly as the live drain left it")
    assert by_ref["/live.md"].reason == "", "no resume reason invented for it"

    # …and the live item's outcome still lands, which is the whole point.
    coord.advance(ORG, store.items(ORG, live_batch.batch_id)[0],
                  BatchItemState.INDEXED, tokens_in=7)
    assert coord.status(ORG, live_batch.batch_id)[1][0].state is (
        BatchItemState.INDEXED)


def test_a_retryable_skip_is_retried_but_a_condition_that_never_clears_settles_failed(
        monkeypatch):
    """⛔ B2's ceiling: a retry must be bounded, and its end must be VISIBLE.

    `runner_result_to_outcome` maps a `retryable` skip to `queued` on purpose (a
    held index lock is not a failure). Without a ceiling the drain re-claims that
    item for ever — a token per pass, reporting `pending` with no error, which is
    the inert row this layer exists to prevent.
    """
    from tortoise import batch_ingest as bi

    assert bi.BATCH_MAX_ATTEMPTS >= 2, "a ceiling of 1 is not a retry at all"
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, _items = coord.submit(ORG, ["/locked.md"])

    outcomes = []

    def locked(item: BatchItem) -> ItemOutcome:
        outcomes.append(item.attempts)
        return ItemOutcome(BatchItemState.QUEUED, reason="index lock held")

    for _ in range(bi.BATCH_MAX_ATTEMPTS + 2):
        coord.run_item(ORG, store.items(ORG, batch.batch_id)[0], locked)

    final = store.items(ORG, batch.batch_id)[0]
    assert final.state is BatchItemState.FAILED
    assert f"{bi.BATCH_MAX_ATTEMPTS} attempt" in final.reason
    assert "re-submit" in final.reason
    assert len(outcomes) == bi.BATCH_MAX_ATTEMPTS, (
        "the runner must run exactly the ceiling's number of times — the "
        f"remaining passes must settle the item, not run it: {outcomes}")


def test_cancelling_a_settled_batch_reports_it_unchanged_rather_than_relabelling_it():
    """⛔ B3: a status that lies on a billing-visible surface.

    Cancel used to fall through unconditionally, so a cancel arriving after the
    last item landed relabelled a fully successful `completed` batch as
    `cancelled` and INVENTED a `cancelled_at`/`cancel_reason` — while every item
    stayed `indexed` and charged. The customer then reads "work stopped, not
    charged" over a batch they were charged for.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    for it in items:
        coord.advance(ORG, it, BatchItemState.EXTRACTING, attempt=True)
        coord.advance(ORG, it, BatchItemState.INDEXED, tokens_in=11)
    settled = coord.settle(ORG, batch.batch_id)
    assert settled.state is BatchState.COMPLETED
    charged = sum(i.tokens_in for i in store.items(ORG, batch.batch_id))

    after, after_items = coord.cancel(ORG, batch.batch_id, reason="too late")

    assert after.state is BatchState.COMPLETED, (
        "cancelling cannot un-spend tokens; the settled state is the truth")
    assert not after.cancelled_at and after.cancel_reason == "", (
        "no cancel facts may be invented for a batch that was never cancelled")
    assert all(i.state is BatchItemState.INDEXED for i in after_items)
    assert sum(i.tokens_in for i in after_items) == charged

    # A repeat cancel of a genuinely cancelled batch stays idempotent too.
    store2 = InMemoryBatchStore()
    coord2 = BatchCoordinator(store2)
    b2, _ = coord2.submit(ORG, ["/c.md"])
    first, _ = coord2.cancel(ORG, b2.batch_id, reason="first")
    second, _ = coord2.cancel(ORG, b2.batch_id, reason="second")
    assert first.cancelled_at == second.cancelled_at
    assert second.cancel_reason == "first", (
        "a second cancel must not rewrite the recorded reason")


# ── #14415 review round 2: the repair paths, pinned ───────────────────────
def test_a_batch_row_that_lagged_its_items_is_repaired_by_recovery():
    """⛔ ROUND-2 BLOCKING 1 arm 2 + BLOCKING 2: a lagging ROW wedged for ever.

    `settle()`'s own write can time out, and a process can die between the last
    item write and the settle. Every item is then terminal and charged while the
    batch row still reads `accepted` — and because `pending_batches()` selected
    on ITEM state, that row was invisible to every path, including a fresh boot.
    The only call that would ever move it was `cancel()`, which relabelled it
    `cancelled` (B3's wrong outcome, reached with no injected fault).
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    for it in items:
        coord.advance(ORG, it, BatchItemState.EXTRACTING, attempt=True)
        coord.advance(ORG, it, BatchItemState.INDEXED, tokens_in=5)
    # …the process dies here, before `settle()`.
    assert store.get_batch(ORG, batch.batch_id).state is not BatchState.COMPLETED
    assert store.pending_batches(ORG) == [batch.batch_id], (
        "an unsettled row must be VISIBLE to recovery, or nothing ever derives it")

    # A fresh boot's recovery pass settles it from the rows.
    assert BatchCoordinator(store).recover(ORG) == [], (
        "nothing to RESUME — the work is done; the row is what was stale")
    assert store.get_batch(ORG, batch.batch_id).state is BatchState.COMPLETED
    assert store.pending_batches(ORG) == []


def test_cancel_derives_the_truth_over_a_lagging_row_instead_of_relabelling_it():
    """⛔ ROUND-2 BLOCKING 2: the guard is on remaining WORK, not on the row.

    Keying only on ``batch.state`` reproduced B3's exact outcome — a fully
    indexed, charged batch relabelled `cancelled` with an invented
    `cancelled_at` — whenever the row lagged its items.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    for it in items:
        coord.advance(ORG, it, BatchItemState.EXTRACTING, attempt=True)
        coord.advance(ORG, it, BatchItemState.INDEXED, tokens_in=5)
    # The row lags: no settle() was written.
    after, after_items = coord.cancel(ORG, batch.batch_id, reason="too late")

    assert after.state is BatchState.COMPLETED, (
        "there is nothing left to stop, so the derived state is the truth")
    assert not after.cancelled_at and after.cancel_reason == ""
    assert all(i.state is BatchItemState.INDEXED for i in after_items)
    assert sum(i.tokens_in for i in after_items) == 10


def test_the_live_drain_test_is_asked_per_batch_not_snapshotted():
    """⛔ ROUND-2 NB4: a snapshot taken before `recover()`'s reads left a window.

    A drain that claimed the batch INSIDE that window still had its claim
    requeued, so the same discarded outcome survived, narrowed to one query's
    round trips. The seam is now a predicate evaluated at the moment of the
    decision — this pin is about WHEN it is asked, not what it answers.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    first, first_items = coord.submit(ORG, ["/a.md"])
    second, second_items = coord.submit(ORG, ["/b.md"])
    for items in (first_items, second_items):
        coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)

    probed: list[str] = []

    def is_live(batch_id: str) -> bool:
        probed.append(batch_id)
        # The claim lands only after the FIRST probe: a snapshot taken before
        # the reads would have answered "not live" for both batches.
        return batch_id == second.batch_id and len(probed) >= 1

    resumed = coord.recover(ORG, live_batches=is_live, stale_after_s=0)

    assert set(probed) == {first.batch_id, second.batch_id}, (
        f"the predicate must be asked per candidate batch, got {probed}")
    assert resumed == [first.batch_id], (
        "the batch whose claim appeared after the first probe must be skipped")
    assert store.items(ORG, second.batch_id)[0].state is (
        BatchItemState.EXTRACTING)


def test_a_lost_cancel_is_derived_cancelled_not_partial():
    """⛔ ROUND-3 NB4: `settle()`'s docstring promised a state it never wrote.

    A cancel whose own `put_batch` was lost leaves every item `cancelled` over a
    row that derived `partial` — with an empty `cancel_reason`, so the route's
    copy told the customer "this batch had already finished, so nothing was
    stopped" over a batch they cancelled.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    for it in items:
        coord.advance(ORG, it, BatchItemState.CANCELLED, reason="customer stop")

    settled = coord.settle(ORG, batch.batch_id)

    assert settled.state is BatchState.CANCELLED
    assert settled.cancelled_at, "a derived cancel still records when"
    assert settled.cancel_reason == "customer stop"


# ── the cancel/claim interleavings (#14415 review round 4, BLOCKING 1) ──────
def test_a_cancel_between_the_read_and_the_claim_does_not_run_the_runner():
    """⛔ DIRECTION A, reproduced on the pre-fix tree: an item the customer
    stopped is INDEXED and CHARGED anyway.

    `run_item` reads the row, then claims it. A cancel landing inside that window
    used to be overwritten by the claim (an unconditional write), the runner ran,
    and 60 000 tokens were spent on a document the customer cancelled — against
    this module's own promise that `queued` → `cancelled` costs "zero tokens".

    The claim is a compare-and-set now, so the cancel wins and the runner is
    never called. The competing write is fired from inside the storage wrapper,
    between the read and the write — the exact window, without threads.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, _items = coord.submit(ORG, ["/d/a.md"])

    ran: list[str] = []

    def runner(item: BatchItem) -> ItemOutcome:
        ran.append(item.item_id)
        return ItemOutcome(BatchItemState.INDEXED, tokens_in=60_000)

    real_cas = store.put_item_if_state
    fired = {"done": False}

    def cancelling_cas(org_id, item, expected, expected_attempts=None,
                       token_delta=None):
        if (not fired["done"] and expected is BatchItemState.QUEUED
                and item.state is BatchItemState.EXTRACTING):
            fired["done"] = True
            BatchCoordinator(store).cancel(
                org_id, item.batch_id, reason="cancelled by the customer")
        return real_cas(org_id, item, expected)

    store.put_item_if_state = cancelling_cas  # type: ignore[method-assign]
    coord.run_item(ORG, store.items(ORG, batch.batch_id)[0], runner)

    assert fired["done"], "the interleaving did not fire — the test proves nothing"
    assert ran == [], (
        "the runner ran for an item the customer had ALREADY cancelled — that is "
        "a document indexed and charged after a stop")
    final = store.items(ORG, batch.batch_id)[0]
    assert final.state is BatchItemState.CANCELLED
    assert final.tokens_in == 0 and final.tokens_out == 0


def test_a_cancel_that_saw_queued_cannot_relabel_a_claim_that_landed(monkeypatch):
    """⛔ DIRECTION B, reproduced on the pre-fix tree: work that REALLY ran is
    reported as never done (tokens spent, unbilled, result discarded).

    `cancel()` read the items, the drain claimed and settled one, and cancel then
    wrote its STALE `cancelled` copy over the settled row. The batch row was
    relabelled too — "work stopped, not charged" over an `indexed`, charged item.

    Every write in that loop is a compare-and-set now, so a cancel whose snapshot
    is stale stops nothing — and, since it stopped nothing, it must not relabel
    the batch either; the row is derived from the items instead.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, _items = coord.submit(ORG, ["/d/a.md"])
    stale = store.items(ORG, batch.batch_id)      # what cancel() reads
    assert stale[0].state is BatchItemState.QUEUED

    coord.run_item(ORG, store.items(ORG, batch.batch_id)[0],
                   lambda item: ItemOutcome(BatchItemState.INDEXED,
                                            tokens_in=60_000))
    settled = store.items(ORG, batch.batch_id)[0]
    assert settled.state is BatchItemState.INDEXED   # premise: the claim landed

    monkeypatch.setattr(store, "items", lambda org_id, batch_id: stale)
    cancelled, _ = coord.cancel(ORG, batch.batch_id, reason="customer cancelled")
    monkeypatch.undo()

    final = store.items(ORG, batch.batch_id)[0]
    assert final.state is BatchItemState.INDEXED, (
        "a cancel whose snapshot predates the claim overwrote the item row — the "
        "runner's outcome and its token counts were thrown away")
    assert final.tokens_in == 60_000
    assert cancelled.state is not BatchState.CANCELLED, (
        "the batch row was relabelled `cancelled` while every item was indexed "
        "and charged — the status lies on a billing-visible surface")


def test_graph_cas_is_a_match_on_the_expected_state_not_a_merge():
    """The DB-side compare-and-set, pinned on the statement itself.

    ⛔ This is the PRODUCTION path and the in-memory store cannot prove it: a
    `MERGE` here would RECREATE the pattern row and silently undo the competing
    writer, which is the exact defect the CAS exists to close. So the statement
    must be a `MATCH` carrying the expected state and the tenant scope, and the
    verdict must be the RESULT SET — empty means "someone else moved it", and a
    non-atomic implementation that returned True regardless would be the same
    defect wearing a better name.
    """
    item = BatchItem(batch_id="batch_1", item_id="batch_1-00000", ref="/d/a.md",
                     state=BatchItemState.EXTRACTING, updated_at="t")

    lost = _Recorder(answers=[[]])
    assert GraphBatchStore(lost).put_item_if_state(
        ORG, item, BatchItemState.QUEUED) is False
    cypher, params = lost.statements[-1], lost.params[-1]
    assert cypher.lstrip().startswith("MATCH"), (
        "the CAS must be a MATCH — a MERGE would recreate the row it failed to "
        f"match, undoing the other writer: {cypher[:120]}")
    assert "MERGE" not in cypher.split("SET", 1)[0]
    assert params["expect"] == "queued"
    assert params["org"] == ORG and params["bid"] == "batch_1"
    assert params["iid"] == "batch_1-00000"

    won = _Recorder(answers=[[["batch_1-00000"]]])
    assert GraphBatchStore(won).put_item_if_state(
        ORG, item, BatchItemState.QUEUED) is True
    assert won.params[-1]["state"] == "extracting"


# ── round 2: the layer's own compare-and-set, claims and bounds ─────────────
def test_cancel_cannot_move_a_row_a_runner_already_claimed():
    """BLOCKING 4: `advance` is a compare-and-set on the row's STATE.

    `cancel()` decides from a snapshot. A drain that claims the row in that window
    (`queued → extracting`) used to be overwritten by the stale `queued` copy
    walking the legal `queued → cancelled` edge — cancelling an item whose model
    call had ALREADY spent its tokens and then discarding the result, which the
    cancel docstring explicitly rejects.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md"])

    stale = store.items(ORG, batch.batch_id)[0]      # what cancel() snapshotted
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)

    moved = coord.advance(ORG, stale, BatchItemState.CANCELLED, reason="customer")
    assert moved.state is BatchItemState.EXTRACTING, (
        "a stale copy cancelled an in-flight item")
    row = store.get_item(ORG, batch.batch_id, items[0].item_id)
    assert row.state is BatchItemState.EXTRACTING
    assert row.attempts == 1, "the attempt counter must not be reset by a stale write"

    # …and the runner's result still lands, because the row was never cancelled.
    coord.advance(ORG, store.get_item(ORG, batch.batch_id, items[0].item_id),
                  BatchItemState.INDEXED, tokens_in=100)
    assert store.items(ORG, batch.batch_id)[0].state is BatchItemState.INDEXED


def test_recover_does_not_steal_a_live_claim():
    """BLOCKING 5: a poll on one instance must not requeue another's live work."""
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)

    # The claim was just taken: it looks LIVE, which is the whole point. The
    # claim itself is NOT taken — that is the safety property.
    # ⛔ `== []` would be the WRONG contract (#14415 review round 5, BLOCKING 1):
    # "not ours to TAKE" is not "not ours to REPORT". Skipping the batch discards
    # the decision instead of deferring it, and the item is then stranded until
    # the reclaim window expires with nothing looking again — which
    # is exactly the ordinary redeploy (a runner in flight, a replacement booting
    # a second later). The batch is reported so a later pass revisits it; the
    # drain re-checks the window itself (`run_item`) and leaves the claim alone.
    assert BatchCoordinator(store).recover(ORG) == [batch.batch_id]
    row = store.items(ORG, batch.batch_id)[0]
    assert row.state is BatchItemState.EXTRACTING, (
        "the live claim must not be requeued")
    assert row.attempts == 1, "and must not be counted as one of our attempts"

    # …and once it is older than the window it IS void and gets taken back.
    assert BatchCoordinator(store).recover(ORG, stale_after_s=0) == [batch.batch_id]
    assert store.items(ORG, batch.batch_id)[0].state is BatchItemState.QUEUED


def test_a_stale_copy_of_a_terminal_item_never_runs_the_runner():
    """NB2: only the FRESH row may decide that an item is terminal."""
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, items[0], BatchItemState.INDEXED)

    stale = BatchItem(batch_id=batch.batch_id, item_id=items[0].item_id,
                      ref="/a.md", state=BatchItemState.QUEUED)
    calls: list[int] = []
    out = coord.run_item(ORG, stale,
                         lambda i: calls.append(1) or ItemOutcome(BatchItemState.INDEXED))
    assert calls == [], "a completed document was extracted a second time"
    assert out.state is BatchItemState.INDEXED


def test_a_rejected_acceptance_discards_its_own_rows_and_staged_files(tmp_path):
    """BLOCKING 2: the rollback, at the layer's own seam."""
    store = InMemoryBatchStore()
    seen = {"n": 0}

    # ⛔ THE FAILURE IS INJECTED AT the BATCH ROW, NOT AT THE ITEMS (#14415 review
    # cycle 2, MEDIUM). The acceptance writes its item set in ONE `UNWIND`
    # statement now, so patching `put_items` makes the FIRST write raise — no item
    # row ever lands, `pending_batches()` is empty by construction and
    # `assert ... == []` below is unfalsifiable. MEASURED: with this seam, deleting
    # `submit`'s `self._store.discard(org_id, bid)` left every test green, so the
    # item-row half of the rollback — whose ONLY production caller is that discard —
    # was ungraded. Patching `put_batch` instead fails the SECOND write, which is
    # also the real shape the rollback exists for: items landed, the ACK row did
    # not. Same seam, one statement later.
    def _flaky(row):
        seen["n"] += 1
        raise TimeoutError("graph timeout")

    # ⛔ THE ACK ROW IS CREATED THROUGH THE CREATE-IF-ABSENT (#14415 review round
    # 17), so `put_batch` is no longer on the acceptance path at all: patching it
    # injected the failure into a call that never happens and this pin silently
    # stopped exercising the rollback (DID NOT RAISE). The seam moves with the code.
    store.put_batch_if_absent = _flaky  # type: ignore[method-assign]
    coord = BatchCoordinator(store)
    base = tmp_path / "ingest"
    base.mkdir()

    with pytest.raises(TimeoutError):
        coord.submit(ORG, ["/a.md", "b.md"], contents=[None, "# B"],
                     stage_base=str(base))

    assert store.pending_batches(ORG) == [], (
        "item rows from a rejected acceptance survive — the resume scan would "
        "adopt and work documents the caller was told were refused")
    staged = base / "_batches"
    assert not staged.exists() or not any(staged.iterdir()), (
        "the rejected batch's staged files were left on disk")


# ── round 5: the claim's IDENTITY, and a cancel over a dead claim ──────────
def test_two_reclaimers_cannot_both_win_the_same_stale_row(monkeypatch):
    """⛔ ROUND-5 REVIEW P1, reproduced by interleaving INSIDE the reclaim.

    A state-only compare on a re-claim matches the value it is about to write
    back, so two drains over one stale row both matched, both ran the runner, and
    one document was extracted twice and BILLED TWICE (measured: 2 runner calls,
    120 000 tokens charged for 60 000 spent).

    ⛔ The interleave fires inside the CAS, not between two sequential `run_item`
    calls: the first call settles the row on its own, and a second `run_item` then
    returns at the terminal check having written nothing — a green test that
    never races. (That is exactly how the first cut of this test was vacuous.)
    """
    store = InMemoryBatchStore()
    batch, items = BatchCoordinator(store).submit(ORG, ["/a.md"])
    claimed = BatchCoordinator(store).advance(
        ORG, items[0], BatchItemState.EXTRACTING, attempt=True,
        expected=BatchItemState.QUEUED, expected_attempts=0)
    stale = store.get_item(ORG, batch.batch_id, claimed.item_id)
    stale.updated_at = _ancient_stamp()      # the claim's runner died long ago
    store.put_item(ORG, stale)

    calls: list[str] = []
    coord = BatchCoordinator(store)
    real_cas = store.put_item_if_state
    fired = {"done": False}

    def racing_cas(org_id, item, expected, expected_attempts=None,
                   token_delta=None):
        if (not fired["done"] and expected is BatchItemState.QUEUED
                and item.state is BatchItemState.EXTRACTING):
            fired["done"] = True
            # A PEER takes the claim between our void and our claim.
            other = BatchCoordinator(store)
            other.advance(org_id, store.get_item(org_id, item.batch_id,
                                                 item.item_id),
                          BatchItemState.EXTRACTING, attempt=True,
                          expected=BatchItemState.QUEUED,
                          expected_attempts=expected_attempts)
        return real_cas(org_id, item, expected,
                        expected_attempts=expected_attempts)

    monkeypatch.setattr(store, "put_item_if_state", racing_cas)
    coord.run_item(ORG, store.get_item(ORG, batch.batch_id, claimed.item_id),
                   lambda i: calls.append(i.item_id) or ItemOutcome(
                       BatchItemState.INDEXED, tokens_in=60_000))

    assert fired["done"], "the interleaving never fired — the test proves nothing"
    assert calls == [], (
        "one document was run twice over two drains — the peer's claim was not "
        f"respected (calls={calls}), which bills the customer twice")
    row = store.items(ORG, batch.batch_id)[0]
    assert row.state is BatchItemState.EXTRACTING, (
        "this pass must not settle a claim it does not hold")


def test_put_item_if_state_compares_the_claim_identity_in_the_inmemory_store():
    """The compare, on the store that the concurrency tests actually drive."""
    store = InMemoryBatchStore()
    batch, items = store_items_helper(store)
    row = store.get_item(ORG, batch.batch_id, items[0].item_id)
    assert row.attempts == 0
    row.attempts = 1
    store.put_item(ORG, row)          # the ROW's identity has moved
    assert store.put_item_if_state(ORG, row, BatchItemState.QUEUED,
                                   expected_attempts=0) is False, (
        "the CAS matched a row whose attempt count had already moved")
    assert store.put_item_if_state(ORG, row, BatchItemState.QUEUED,
                                   expected_attempts=1) is True


def test_the_graph_cas_names_the_attempt_count_only_when_asked():
    """The production compare, pinned on the statement: identity in the pattern."""
    item = BatchItem(batch_id="b1", item_id="b1-0", ref="/d/a.md",
                     state=BatchItemState.EXTRACTING, attempts=1,
                     updated_at="t")
    rec = _Recorder(answers=[[]])
    GraphBatchStore(rec).put_item_if_state(ORG, item, BatchItemState.QUEUED,
                                           expected_attempts=0)
    cypher = rec.statements[-1]
    assert "i.attempts = $expect_attempts" in cypher, cypher
    assert rec.params[-1]["expect_attempts"] == 0

    rec2 = _Recorder(answers=[[["b1-0"]]])
    GraphBatchStore(rec2).put_item_if_state(ORG, item, BatchItemState.QUEUED)
    assert "expect_attempts" not in rec2.statements[-1]
    assert "expect_attempts" not in rec2.params[-1]


def test_a_cancel_stops_a_dead_claim_instead_of_reporting_the_batch_finished(
        monkeypatch):
    """⛔ ROUND-5 REVIEW P1, reproduced: `attempts == 0` is UNREACHABLE.

    Every writer of `extracting` increments `attempts`, so the documented
    "dead claim → stopped for free" arm never ran: a batch whose only
    non-terminal item was a dead claim cancelled to `stopped = False`, the route
    answered "already finished", and the next status poll's `recover()` requeued
    and RAN the document — charging tokens after the customer cancelled.

    The test is the claim's AGE now, so a stale claim is stopped and a fresh one
    is left alone (its tokens are already being spent).
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/dead.md", "/live.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, items[1], BatchItemState.EXTRACTING, attempt=True)

    rows = store.items(ORG, batch.batch_id)
    stale = rows[0]
    stale.updated_at = _ancient_stamp()
    store.put_item(ORG, stale)

    stopped_batch, after = coord.cancel(ORG, batch.batch_id, reason="customer")
    by_ref = {i.ref: i for i in after}
    assert by_ref["/dead.md"].state is BatchItemState.CANCELLED, (
        "a dead claim survived a cancel — the batch would run and charge after "
        "the customer stopped it")
    assert by_ref["/dead.md"].tokens_in == 0
    assert by_ref["/live.md"].state is BatchItemState.EXTRACTING, (
        "a LIVE claim must be left alone: its tokens are already being spent")
    assert stopped_batch.state is BatchState.CANCELLED
    assert stopped_batch.cancelled_at, "the row says nothing was stopped"


def _ancient_stamp() -> str:
    """A claim stamp far older than any reclaim window."""
    from datetime import UTC, datetime, timedelta
    return (datetime.now(UTC) - timedelta(days=3)).isoformat()


def store_items_helper(store):
    coord = BatchCoordinator(store)
    return coord.submit(ORG, ["/a.md"])


def test_record_item_spend_returns_the_tokens_it_recorded(monkeypatch):
    """⛔ ROUND-5 REVIEW P1: the success path of the meter was DEAD.

    It ended `return spent_in + spent_out` — locals that do not exist — so every
    non-zero charge raised `NameError` AFTER writing the ledger. `_drain_batch`
    catches it inside the per-item try, so the money landed and every metered
    item emitted a false item-failure log, and the documented return contract was
    unreachable. This test is the one that reds: nothing else called it.
    """
    from tortoise import metering

    calls: list[dict] = []
    monkeypatch.setattr(metering, "record_capture_usage",
                        lambda org_id, **kw: calls.append({"org": org_id, **kw}))
    from tortoise.batch_ingest import record_item_spend

    assert record_item_spend(ORG, tokens_in=700, tokens_out=30) == 730
    assert calls == [{"org": ORG, "calls": 0, "tokens_in": 700,
                      "tokens_out": 30}]
    # The zero case is still a no-op (no phantom capture row, #3359).
    assert record_item_spend(ORG, tokens_in=0, tokens_out=0) == 0
    assert len(calls) == 1
# ── round 8: the order the rotation needs, the receipt/ledger equality ─────
def test_pending_batches_is_ordered_by_the_caller_not_by_cypher():
    """⛔ BLOCKING 1: a `UNION` followed by `ORDER BY` is IGNORED by FalkorDB.

    Measured: the real query came back in insertion order, byte-identical to the
    same query without the clause, three runs out of three (the single-arm control
    sorted correctly). The rotating recovery window is only fair over a
    DETERMINISTIC list, so the order is imposed in Python — an inert Cypher clause
    is exactly the "assertion that cannot fail" class this branch keeps removing.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    ids = [coord.submit(ORG, [f"/d/{n}.md"])[0].batch_id for n in range(6)]
    # The consumer sorts, so the window is deterministic whatever the store's
    # natural order is (the in-memory store is insertion-ordered, the graph store
    # sorts because FalkorDB ignores the clause).
    assert sorted(coord._store.pending_batches(ORG)) == sorted(ids)
    import random

    shuffled = list(ids)
    random.Random(7).shuffle(shuffled)
    resumed = []
    for offset in range(0, len(ids), 2):
        resumed += coord.recover(ORG, offset=offset, limit=2)
    assert set(ids) <= set(resumed), (
        f"{sorted(set(ids) - set(resumed))} were never reached by the rotation")


def test_a_future_stamp_is_fresh_within_one_window_and_ancient_beyond_it():
    """NB1: both failure modes are real, and they point opposite ways.

    `max(0.0, …)` read a stamp months ahead as age 0 → the item was never
    re-claimed, the ceiling never incremented, the document non-terminal for ever.
    A 60 s cliff then read ORDINARY two-box skew as ancient and re-ran the runner
    on a claim seconds old. Inside one reclaim window it is skew (fresh); beyond
    it, the stamp is not a clock and the item is treated as ancient.
    """
    from datetime import UTC, datetime, timedelta

    def stamp(offset_s: float) -> str:
        return (datetime.now(UTC) + timedelta(seconds=offset_s)).isoformat()

    assert bi_age(stamp(-5)) > 4, "a past stamp is an ordinary age"
    assert bi_age(stamp(30)) == 0.0, "a minute of skew must read as FRESH"
    assert bi_age(stamp(BATCH_RECLAIM_AFTER_S + 30)) == float("inf"), (
        "beyond a window the stamp is not a clock: believing it strands the "
        "document for ever")
    assert bi_age("not-a-timestamp") == float("inf"), (
        "an unreadable claim time is ancient, never permanently fresh")


def test_a_receipt_and_the_meter_agree_when_the_row_moved_on():
    """⛔ BLOCKING 2: the row must record what the meter bills.

    Two arms have a runner call that really spent while the row could not take its
    outcome: the row went terminal first, and the compare-and-set lost. Both used
    to leave the spend off the row — the ledger billed 735 against a receipt that
    said 5, and in the second arm 730 appeared on NEITHER surface.
    """
    for variant in ("terminal", "cas-lost"):
        store = InMemoryBatchStore()
        assert store is not None
        coord = BatchCoordinator(store)
        batch, items = coord.submit(ORG, ["/a.md"])

        def runner(item, _v=variant, _store=store):
            other = BatchCoordinator(_store)
            peer = _store.get_item(ORG, item.batch_id, item.item_id)
            if _v == "terminal":
                other.advance(ORG, peer, BatchItemState.EXTRACTING, attempt=True)
                other.advance(ORG, peer, BatchItemState.INDEXED, tokens_in=5)
            return ItemOutcome(BatchItemState.INDEXED, tokens_in=700,
                               tokens_out=30)

        billed_arms: list[str] = []
        if variant == "cas-lost":
            # The row moves BETWEEN the coordinator's `_fresh` and its write, so
            # the compare-and-set loses and the outcome cannot be applied.
            #
            # ⛔ THE PEER'S MOVE MUST CHANGE THE COMPARED STATE (#14415 review
            # round 5, P2). The first cut advanced `EXTRACTING → EXTRACTING` — the
            # very state the terminal compare expects — so the write LANDED and
            # the lost-CAS arm (`_bill` on a lost compare) was never exercised:
            # deleting it left the test green, in both variants. A customer
            # cancel moves the row to `CANCELLED`, which genuinely loses the
            # compare.
            # ⛔ AND `cancel()` ALONE IS NOT ENOUGH TO LOSE THE COMPARE (#14415
            # review round 9). With no QUEUED sibling, `cancel()` leaves an
            # all-in-flight batch's row `running` and its claimed item untouched —
            # so the compared state is unchanged and the write LANDS. Moving the
            # compared row is the whole premise of this variant, so it is done
            # directly here.
            real = coord._advance_landed

            def _advance_landed(org_id, item, new_state, _store=store,
                                _real=real, **kw):
                if new_state is BatchItemState.INDEXED:
                    peer = _store.get_item(org_id, item.batch_id, item.item_id)
                    peer.state = BatchItemState.CANCELLED
                    _store.put_item(org_id, peer)
                return _real(org_id, item, new_state, **kw)

            coord._advance_landed = _advance_landed  # type: ignore[method-assign]
            real_bill = coord._bill

            def _spy(org_id, item, tokens_in, tokens_out, _arms=billed_arms,
                     _real_bill=real_bill):
                _arms.append("_bill")
                return _real_bill(org_id, item, tokens_in, tokens_out)

            coord._bill = _spy  # type: ignore[method-assign]

        out = coord.run_item(ORG, items[0], runner)
        if variant == "cas-lost":
            assert billed_arms == ["_bill"], (
                "the compare never lost, so the arm this variant exists for was "
                f"not entered ({billed_arms} — the row is "
                f"{store.get_item(ORG, batch.batch_id, items[0].item_id).state})")
        billed = out.charge_in + out.charge_out
        row = store.get_item(ORG, batch.batch_id, items[0].item_id)
        assert billed == 730, f"{variant}: our call spent 730, billed {billed}"
        peer_spend = 5 if variant == "terminal" else 0
        assert row.tokens_in + row.tokens_out == billed + peer_spend, (
            f"{variant}: the meter bills {billed} (+{peer_spend} by the peer) but "
            f"the row reports {row.tokens_in + row.tokens_out} — the receipt must "
            "match the bill")


# ── round 9: the malformed statement, the cancel a rescan erased, and the
#    liveness rule's third site ────────────────────────────────────────────
class _Created:
    """The engine's answer to `MERGE … ON CREATE SET … RETURN b.create_token`.

    A fake that answers `[]` to that read makes every create look REFUSED, which is
    not what the engine does: on CREATE the returned value IS the per-call token the
    caller minted. Modelling it is what lets the acceptance's insert-if-absent be
    driven hermetically (#14415 review round 17).
    """

    def __init__(self, token):
        self.result_set = [[token]] if token is not None else []


def _every_store_statement_pairs() -> list[tuple[str, dict]]:
    """Every `(statement, params)` pair `GraphBatchStore` hands to the engine.

    A recording `query` returns an empty result set, so nothing is asserted about
    RESULTS here — only about what this module EMITS. That is deliberate: the
    fake `query` in the rest of this suite accepts ANY string, which is exactly
    why a malformed statement went unnoticed.

    ⛔ THE PARAMS ARE COLLECTED TOO. Braces are half of "does the engine accept
    this"; the other half is the parameters, and a statement that names `$x`
    without supplying it is FalkorDB's `Missing parameters` — a RUN-time failure
    invisible to every hermetic test (#14415 recut review, P0, measured on the
    live round-trip).
    """
    pairs: list[tuple[str, dict]] = []

    class _Res:
        def __init__(self) -> None:
            self.result_set: list = []

    def _record(cypher, params=None, timeout=None, _pairs=pairs):
        _pairs.append((cypher, dict(params or {})))
        return _Res()

    store = GraphBatchStore(_record)
    batch, items = BatchCoordinator(InMemoryBatchStore()).submit(
        ORG, ["/a.md", "/b.md"])
    store.put_batch(batch)
    store.get_batch(ORG, batch.batch_id)
    store.put_item(ORG, items[0])
    # ⛔ `put_items` IS the acceptance write, and it carried an UNBALANCED BRACE
    # through a green suite (#14415 review round 14, P1): this pin claims "every
    # Cypher statement `GraphBatchStore` can emit" but drove only `put_item`, so
    # neither the brace guard below nor the live round-trip could reach the one
    # statement EVERY acceptance emits — which is what made a production-only
    # syntax error invisible while the feature was dead on arrival.
    store.put_items(ORG, items)
    store.put_item_if_state(ORG, items[0], BatchItemState.QUEUED)
    store.put_item_if_state(ORG, items[0], BatchItemState.QUEUED,
                            expected_attempts=1)
    store.put_batch_if_state(batch, (BatchState.ACCEPTED,))
    store.items(ORG, batch.batch_id)
    store.get_item(ORG, batch.batch_id, items[0].item_id)
    store.list_recent_batches(ORG)
    store.pending_batches(ORG)
    store.batch_counts(ORG, [batch.batch_id])
    store.find_settled_by_content_key(ORG, "k")
    store.discard(ORG, batch.batch_id)
    # ⛔ `add_item_tokens` is the ONE statement whose failure is SWALLOWED
    # (`_bill` logs and carries on: "the ledger is charged, the row's total is
    # short"), so a malformed emission here is exactly the bill/receipt
    # disagreement this branch fixed twice — and this pin, whose docstring claims
    # "every Cypher statement `GraphBatchStore` can emit", did not drive it, so
    # neither the brace guard nor the live round-trip could reach it (#14415
    # recut review, P3). A pin wider than its reach is the no-op-gate class.
    store.add_item_tokens(ORG, batch.batch_id, items[0].item_id,
                          tokens_in=1, tokens_out=1)
    # ⛔ TWO MORE the pin claimed but did not reach (#14415 recut-2 review,
    # note 1). `cancel_queued_items` is the round-7 replacement for the per-item
    # cancel loop, and the `token_delta` form of `put_item_if_state` is the
    # claim/settle MONEY path — the one place a dropped `$param` would bill the
    # wrong number rather than fail loudly. A pin whose prose is wider than its
    # reach is the no-op-gate class this file keeps re-learning.
    store.put_item_if_state(ORG, items[0], BatchItemState.QUEUED,
                            token_delta=(1, 1))
    store.cancel_queued_items(ORG, batch.batch_id, reason="stopped",
                              updated_at="t")
    # ⛔ AND THE CREATE-IF-ABSENT (#14415 review round 12, P2). The orphan
    # adoption's write — the one this PR hardened over three rounds — was graded by
    # NEITHER emission guard and by no live test: doubling a brace in it, or naming
    # a `$param` it does not supply, left the whole suite green while FalkorDB
    # refused the statement on `_resume_batch`.
    store.put_batch_if_absent(
        Batch(batch_id="batch_absent", org_id=ORG, state=BatchState.RESUMING,
              created_at="2026-01-01T00:00:00+00:00",
              updated_at="2026-01-01T00:00:00+00:00"))
    # ⛔ AND THE BULK ACCEPTANCE WRITE (#14415 review cycle 2, BLOCKING). `put_items`
    # is the round-13 replacement for the per-item loop, and this pin — whose
    # docstring claims "every statement `GraphBatchStore` can emit" — never drove
    # it. MEASURED: its closing `}}` sat on a PLAIN literal line, so `UNWIND … MERGE
    # (i:…}}) SET …` reached FalkorDB verbatim and EVERY acceptance failed with
    # `Invalid input '}': expected ')'` — the round-9 defect reintroduced, green
    # here because the braces are BALANCED and a fake `query` accepts any string.
    store.put_items(ORG, items)
    return pairs


def _every_store_statement() -> list[str]:
    """The statements alone, for the arms that grade shape and not parameters."""
    return [cypher for cypher, _params in _every_store_statement_pairs()]


def test_every_emitted_statement_is_brace_balanced():
    """⛔ BLOCKING: `put_item_if_state`'s pattern closed with TWO braces.

    The `MATCH` pattern escapes its opening brace as `{{`, so the matching `}}`
    only closes correctly inside an f-string. One line of the statement was a
    PLAIN literal, so the emitted Cypher read
    `... org_id:$org, state:$expect}}) WHERE ...` and FalkorDB refused it —
    `Invalid input '}': expected ')'`. Measured on the docker lane: EVERY item
    state transition raised, so `run_item` never indexed a document, `cancel`
    always answered 503, and the batch sat `resuming` for ever, while 135
    hermetic tests stayed green because `InMemoryBatchStore` and every fake
    `query` accept any string.

    This asserts the shape rather than the text, so it catches the class (a
    doubled brace anywhere in any statement) and not only that one line.
    """
    seen = _every_store_statement()
    assert len(seen) >= 18, f"only {len(seen)} statements reached the store"
    # ⛔ THREE SHAPES, NOT ONE (#14415 review round 11, LOW). Balanced braces are
    # not enough: an `f` prefix dropped from `f"WHERE 1=1 {attempts_clause}"`
    # leaves the braces balanced and emits the literal text `{attempts_clause}`,
    # which only the ENGINE rejects (`'attempts_clause' not defined`) — and in a
    # lane with no FalkorDB the engine-dependent test skips, so this guard is the
    # only protection there. A surviving placeholder is the same class as a
    # doubled brace: a statement this module did not mean to emit.
    bad = [s for s in seen
           if "{{" in s or "}}" in s
           or s.count("{") != s.count("}")
           # ⛔ CASE-INSENSITIVE AND DOTTED (#14415 review round 12, LOW): the
           # first predicate was lower-case only, so `{ITEM_LABEL}` — a spelling
           # this module does use in other f-strings — would have survived it.
           # Inert today (every other interpolation sits on a line that also
           # carries a literal `{`, caught by the arm above), which is exactly
           # why a guard that over-claims its coverage gets fixed before it is
           # relied on.
           or re.search(r"\{[A-Za-z_][A-Za-z0-9_.]*\}", s)]
    assert not bad, (
        "a statement this module emits is malformed Cypher (unbalanced, doubled "
        f"braces, or an un-interpolated placeholder): {bad[0] if bad else ''}")
    assert any("state:$expect}" in s for s in seen), (
        "the compare-and-set statement was not reached — the assertion above "
        "would be vacuous")
    # The predicate's own reach: it must name an un-interpolated label too, or its
    # prose ("a surviving {placeholder}") is wider than its coverage.
    assert re.search(r"\{[A-Za-z_][A-Za-z0-9_.]*\}",
                     "MATCH (i:Ingest {{x:1}} {{ITEM_LABEL}})"), (
        "the placeholder predicate does not catch an upper-case label")


def test_a_rescan_never_settles_a_fresh_claim_of_a_cancelled_batch():
    """⛔ HIGH: `abandon_dead_claims` released a claim that was SECONDS old.

    `recover()` is not a boot-only hook — it is a throttled re-scan re-entered
    from every batch read, and on a ROLLING DEPLOY a new replica's first scan
    runs while the old replica is still inside the runner. An earlier cut let a
    "first scan in this process" settle fresh claims, on the theory that a first
    scan means every claim belongs to a dead predecessor; measured false (claim
    age 0.00 s), and the live runner's outcome then became unreachable so its 730
    tokens were billed for a dropped document.

    ⛔ SO THE WINDOW IS THE ONLY ARM: every caller leaves a FRESH claim alone, and
    a LATER scan settles it once it is stale. Both halves are asserted below,
    because an earlier version of this test called no windowed scan at all — it
    passed with the age check deleted.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    # TWO items: `cancel()` records the cancellation only when it can settle a
    # queued sibling (`/b.md`); with every item in flight it leaves the row
    # `running` and the in-flight work alone ("their result is kept").
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.cancel(ORG, batch.batch_id)
    assert store.get_batch(ORG, batch.batch_id).state is BatchState.CANCELLED

    # 1. A FRESH claim survives a full scan, however many times it runs.
    for _ in range(3):
        BatchCoordinator(store).recover(ORG)
    live = store.get_item(ORG, batch.batch_id, items[0].item_id)
    assert live.state is BatchItemState.EXTRACTING, (
        "a scan settled a FRESH claim: the peer holding it loses its outcome and "
        "the customer is billed for a dropped document")

    # 2. The SAME claim, once stale, is settled by the same call — otherwise the
    #    row would strand non-terminal for ever and "no settlement" would be a
    #    different bug wearing this fix's clothes.
    stale = store.get_item(ORG, batch.batch_id, items[0].item_id)
    stale.updated_at = _ancient_stamp()
    store.put_item(ORG, stale)
    BatchCoordinator(store).recover(ORG)
    settled = store.get_item(ORG, batch.batch_id, items[0].item_id)
    assert settled.state is BatchItemState.CANCELLED, (
        "a STALE claim was not settled — the cancelled batch strands its row")
    assert settled.tokens_in == 0 and settled.tokens_out == 0
    assert store.pending_batches(ORG) == []


def test_a_cancel_landing_during_the_resume_scan_is_not_erased():
    """⛔ HIGH: the `resuming` write was unconditional, so it erased a cancel.

    `_resume_batch` read the row, walked the items and wrote `resuming` with a
    plain `put_batch`. A `cancel()` landing in that window was overwritten —
    `cancelled_at`/`cancel_reason` cleared, the row non-terminal again,
    `pending_batches()` re-selected it and the items were re-driven and charged
    AFTER the customer cancelled. `cancel()` and `settle()` had already been
    hardened with a compare-and-set; this third writer had not.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    # ⛔ A CLAIMED item, because the `resuming` write only happens for a scan that
    # actually MOVED a row (`moved`) — a batch of `queued` items that never ran is
    # reported, not relabelled (round 13, LOW). So the interleaving under test is
    # "the scan requeues a dead claim while the customer cancels".
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    stale = store.get_item(ORG, batch.batch_id, items[0].item_id)
    stale.updated_at = _ancient_stamp()  # a genuinely dead claim
    store.put_item(ORG, stale)

    real_put = store.put_batch_if_state
    fired = {"done": False}

    def _cancel_first(b, expected):
        # ⛔ FIRE ONCE (#14415 review round 9). `cancel()` is itself a
        # compare-and-set now, so an unguarded hook re-enters itself through the
        # cancel's own write: the recursion ends in a swallowed RecursionError and
        # the row is left `accepted`, which made this test red for a reason that
        # had nothing to do with the resume scan.
        if not fired["done"]:
            fired["done"] = True
            BatchCoordinator(store).cancel(ORG, batch.batch_id, reason="customer")
        return real_put(b, expected)

    store.put_batch_if_state = _cancel_first  # type: ignore[method-assign]
    # The return value is allowed to list the batch (the peer's design reports it
    # so `_drain_batch` visits and settles it); what must NOT happen is the write
    # landing over the cancel, so that is what is asserted.
    coord.recover(ORG)

    assert fired["done"], (
        "the interleaving never fired — the cancel is issued FROM the resume CAS "
        "hook, so this can only fail if the hook is gone. It is a DIAGNOSTIC, not "
        "added coverage: the state assertion below already reddens in that case "
        "(measured both ways, #14415 review cycle 2). It names the cause in one "
        "line instead of reporting a `resuming` row as if the CAS had lost.")
    row = store.get_batch(ORG, batch.batch_id)
    assert row.state is BatchState.CANCELLED, (
        "the resume scan overwrote the customer's cancel")
    assert row.cancelled_at and row.cancel_reason == "customer"
    assert store.pending_batches(ORG) == [], (
        "the cancelled batch is drainable again — its items would be re-driven "
        "and charged after the cancel")


def test_the_in_memory_store_never_hands_back_a_charge():
    """⛔ MEDIUM: a non-persisted charge field came back out of the store.

    `charge_in`/`charge_out` are documented as "cannot be re-read and charged a
    second time", and `GraphBatchStore.put_item` honours that by never setting
    them — but the in-memory store copied every dataclass field, so the ONE store
    every other guarantee in this suite is proven against was the one that broke
    the invariant (`charge_in` 735 read back from a stored row).
    """
    store = InMemoryBatchStore()
    batch, items = store_ok = BatchCoordinator(store).submit(ORG, ["/a.md"])
    assert store_ok is not None
    row = store.get_item(ORG, batch.batch_id, items[0].item_id)
    row.charge_in, row.charge_out = 735, 0
    store.put_item(ORG, row)
    back = store.get_item(ORG, batch.batch_id, items[0].item_id)
    assert (back.charge_in, back.charge_out) == (0, 0), (
        "the in-memory store round-trips a charge mark, so a caller that "
        "re-reads the row would bill the same spend twice")


def test_the_graph_store_speaks_real_cypher():
    """⛔ BLOCKING, AND ONLY A REAL ENGINE CATCHES IT (#14415 review round 9).

    `put_item_if_state`'s `MATCH` pattern closed with two braces on one line
    because that line was a plain literal in an implicit concatenation, so the
    statement FalkorDB received read `... state:$expect}}) ...` and was refused:
    `Invalid input '}': expected ')'`. Measured on the docker lane, EVERY item
    transition raised — `run_item` indexed nothing, `cancel()` always answered
    503, and the batch sat `resuming` for ever — while the hermetic suite stayed
    green because `InMemoryBatchStore` and every fake `query` accept ANY string.

    This drives the production store against the live engine: accept, claim,
    settle, read the counts and cancel, i.e. exactly the statements the feature
    depends on. It skips VISIBLY (never early-return-green) where the docker lane
    is not configured, the same rule `tests/_live_utils` applies to its peers.
    """
    from urllib.parse import urlparse

    from _live_utils import _skip_unless_live_uri

    _skip_unless_live_uri()
    uri = os.environ["TORTOISE_DB_URI"]
    from falkordb import FalkorDB

    parsed = urlparse(uri.replace("docker://", "http://"))
    db = FalkorDB(host=parsed.hostname, port=parsed.port,
                  password=parsed.username or None)
    graph_name = parsed.path.strip("/") or "tortoise"
    graph = db.select_graph(f"{graph_name}_batch_store_probe")
    graph.query("MATCH (n) DETACH DELETE n")

    def _query(cypher, params=None, timeout=None):
        return graph.query(cypher, params or {}, timeout=timeout)

    try:
        store = GraphBatchStore(_query)
        coord = BatchCoordinator(store)
        batch, items = coord.submit(ORG, ["/live-a.md", "/live-b.md"])
        assert [i.state for i in items] == [BatchItemState.QUEUED] * 2
        assert store.pending_batches(ORG) == [batch.batch_id]

        out = coord.run_item(
            ORG, store.get_item(ORG, batch.batch_id, items[0].item_id),
            lambda i: ItemOutcome(BatchItemState.INDEXED, tokens_in=700,
                                  tokens_out=30))
        assert out.state is BatchItemState.INDEXED
        row = store.get_item(ORG, batch.batch_id, items[0].item_id)
        assert (row.state, row.tokens_in, row.tokens_out) == (
            BatchItemState.INDEXED, 700, 30)
        assert store.batch_counts(ORG, [batch.batch_id])[batch.batch_id][
            "indexed"] == 1

        stopped, after = coord.cancel(ORG, batch.batch_id, reason="customer")
        assert stopped.state is BatchState.CANCELLED
        assert after[1].state is BatchItemState.CANCELLED
        assert store.pending_batches(ORG) == []
        assert BatchCoordinator(store).recover(ORG) == []
        store.discard(ORG, batch.batch_id)
        assert store.list_recent_batches(ORG) == []

        # ⛔ THE ADOPTION PATH, ON THE ENGINE (#14415 review cycle 2, HIGH). The
        # `MERGE … ON CREATE SET` that rounds 11-13 spent their effort on was never
        # EXECUTED here — the `recover()` above only ever saw a row that existed —
        # so a blind `SET` in its place left every test green. This is the orphan
        # shape: ITEM rows with no batch row, exactly what a submit that died before
        # its `put_batch` leaves behind.
        orphan_batch, orphan_items = coord.submit(ORG, ["/live-orphan.md"])
        store.discard(ORG, orphan_batch.batch_id)
        store.put_item(ORG, BatchItem(
            batch_id=orphan_batch.batch_id, item_id=orphan_items[0].item_id,
            ref=orphan_items[0].ref, name=orphan_items[0].name,
            state=BatchItemState.QUEUED,
            updated_at="2020-01-01T00:00:00+00:00"))
        assert BatchCoordinator(store).recover(ORG) == [orphan_batch.batch_id]
        adopted = store.get_batch(ORG, orphan_batch.batch_id)
        assert adopted is not None, (
            "`MERGE … ON CREATE SET` did not create the adopted row on the engine "
            "— the orphan items are unreachable and the batch is never driven")
        assert store.pending_batches(ORG) == [orphan_batch.batch_id]
        assert [i.item_id for i in store.items(ORG, orphan_batch.batch_id)] == [
            orphan_items[0].item_id]
        store.discard(ORG, orphan_batch.batch_id)

        # ⛔ AND THE ARM `MERGE` ALONE CANNOT SHOW: a create-if-absent over a row
        # that ALREADY EXISTS. `MERGE` matches, so `ON CREATE SET` runs nothing —
        # while a blind `SET` overwrites the row with the caller's orphan snapshot,
        # which is the acceptance-erasing write rounds 11-12 fixed. Proven here
        # because the raced interleaving cannot be staged on an engine: the
        # acceptance is written first, deliberately.
        kept, kept_items = coord.submit(ORG, ["/live-kept.md"],
                                        corpus_name="handbook",
                                        estimate={"items": 1,
                                                  "estimated_tokens": 64799})
        created = store.put_batch_if_absent(Batch(
            batch_id=kept.batch_id, org_id=ORG, state=BatchState.RESUMING,
            created_at=kept_items[0].updated_at,
            updated_at=kept_items[0].updated_at))
        assert created is False, (
            "creating over an EXISTING row reported CREATED — the caller then "
            "trusts its orphan-shaped object instead of re-reading the ACK")
        survivor = store.get_batch(ORG, kept.batch_id)
        assert survivor.estimate.get("estimated_tokens") == 64799, (
            "the create-if-absent BLANKED an acceptance's estimate on the engine — "
            f"this is the round-11 erasure: {survivor.estimate!r}")
        assert survivor.corpus_name == "handbook", (
            f"the create-if-absent blanked corpus_name: {survivor.corpus_name!r}")
        store.discard(ORG, kept.batch_id)
    finally:
        graph.query("MATCH (n) DETACH DELETE n")
        graph.delete()


def test_every_emitted_statement_receives_every_parameter_it_names():
    """⛔ P0: a statement that names `$attempts` without supplying it is DEAD on
    FalkorDB and green everywhere else.

    Measured at the recut head: a SET-block rewrite dropped
    `"attempts": int(item.attempts)` from `put_item_if_state`'s params while the
    statement still wrote `i.attempts=$attempts`. FalkorDB answered
    `redis.exceptions.ResponseError: Missing parameters` — so EVERY item write
    (claim, settle, cancel) failed on the production store, and the batch sat
    `resuming` for ever. The whole hermetic suite was green: a fake `query`
    accepts any string, and the brace guard grades braces. Only the live
    round-trip saw it.

    This pins the class hermetically: every `$name` the emitted Cypher references
    must be present in the params handed to the same call.
    """
    import re as _re

    missing: list[tuple[str, str]] = []
    for cypher, params in _every_store_statement_pairs():
        for name in set(_re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", cypher)):
            if name not in params:
                missing.append((name, cypher.split("\n")[0][:110]))
    assert not missing, (
        "a statement names a parameter it never supplies — FalkorDB answers "
        f"`Missing parameters` at run time: {missing}")


def test_the_store_statements_carry_the_tenant_filter():
    """⛔ NOTHING PINNED THE ROW-LEVEL TENANT FILTER (#14415 review round 12).

    Measured by the reviewer: deleting `org_id:$org` from `get_batch`,
    `put_batch_if_state` and `items` left **139 of 139** batch tests green — the
    live test used one org, and no hermetic test read the statements. Hosted mode
    gives each org its own graph, so this filter is defence in depth rather than
    the only guard (and `_batch_org_root`'s path bound IS pinned) — but the layer
    where it is implemented had zero protection, so a refactor that dropped it
    would read another tenant's batch with nothing red.

    Two halves: the statements must NAME the parameter (hermetic, so it holds in a
    lane with no FalkorDB), and the live engine must refuse a foreign org
    (semantics, in :func:`test_a_foreign_org_cannot_reach_a_batch`).
    """
    seen = _every_store_statement()
    # ⛔ SELECTED BY LABEL, NEVER BY LEADING KEYWORD (#14415 review round 16, P2).
    # The acceptance write starts `UNWIND $rows AS row MERGE (i:…`, so a
    # `startswith(("MATCH", "MERGE"))` filter skipped the ONE statement EVERY
    # acceptance emits — deleting that statement's tenant field left this pin green
    # and the whole hermetic suite green, while every accepted item row would be
    # written with no `org_id`: invisible to `items()`, `get_item()`,
    # `pending_batches()` and `batch_counts()`, i.e. an ACKed batch permanently
    # unreachable and never drained. The live half below skips without a URI, so
    # this half is the only guard.
    filtered = [s for s in seen if BATCH_LABEL in s or ITEM_LABEL in s]
    assert filtered, "no statement names a batch label — the assertion is vacuous"
    # Both spellings are a tenant filter: a literal `$org` parameter, or the
    # `UNWIND` row field it is bound from.
    missing = [s for s in filtered
               if "org_id:$org" not in s and "org_id:row.org" not in s]
    assert not missing, (
        f"{len(missing)} statement(s) do not filter by tenant: {missing[0]}")


def test_a_foreign_org_cannot_reach_a_batch():
    """The live half of the tenant-filter pin — real engine, two orgs."""
    from urllib.parse import urlparse

    from _live_utils import _skip_unless_live_uri

    _skip_unless_live_uri()
    uri = os.environ["TORTOISE_DB_URI"]
    from falkordb import FalkorDB

    parsed = urlparse(uri.replace("docker://", "http://"))
    db = FalkorDB(host=parsed.hostname, port=parsed.port,
                  password=parsed.username or None)
    graph = db.select_graph(
        f"{parsed.path.strip('/') or 'tortoise'}_batch_tenant_probe")
    graph.query("MATCH (n) DETACH DELETE n")

    def _query(cypher, params=None, timeout=None):
        return graph.query(cypher, params or {}, timeout=timeout)

    try:
        store = GraphBatchStore(_query)
        coord = BatchCoordinator(store)
        owner, foreign = "org-owner", "org-foreign"
        batch, items = coord.submit(owner, ["/tenant.md"])

        assert store.get_batch(foreign, batch.batch_id) is None
        assert store.get_item(foreign, batch.batch_id, items[0].item_id) is None
        assert store.items(foreign, batch.batch_id) == []
        assert store.pending_batches(foreign) == []
        assert store.batch_counts(foreign, [batch.batch_id])[batch.batch_id][
            "total"] == 0
        for call in (lambda: BatchCoordinator(store).status(
                         foreign, batch.batch_id),
                     lambda: BatchCoordinator(store).cancel(
                         foreign, batch.batch_id)):
            with pytest.raises(KeyError):
                call()
        # …and the owner's row is untouched by any of that.
        assert store.get_batch(owner, batch.batch_id).state is BatchState.ACCEPTED
        assert len(store.items(owner, batch.batch_id)) == 1
        store.discard(owner, batch.batch_id)
    finally:
        graph.query("MATCH (n) DETACH DELETE n")
        graph.delete()


def test_a_settle_write_failure_still_bills_the_spend_it_made():
    """⛔ ROUND-7 REVIEW P2: a bookkeeping failure is not a lost spend.

    `BATCH_QUERY_TIMEOUT_MS`'s comment promises that a bookkeeping write which
    cannot finish inside the bound "FAILS the item (visible)". It did not: the
    exception left `run_item`, `_drain_batch`'s per-item handler only logged it, no
    `charge_in/charge_out` was ever set, so `record_item_spend` was never called
    and `_report_unmetered` never fired — the runner had already spent, and the
    spend landed on neither the row, the ledger, nor the operator surface while the
    row stayed `extracting` for a later reclaim to run again.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/d/a.md"])
    from tortoise import batch_ingest as batch_ingest_module

    charged: list[int] = []
    real_record = batch_ingest_module.record_item_spend

    def record(org_id, *, tokens_in=0, tokens_out=0, **kwargs):
        charged.append(int(tokens_in) + int(tokens_out))
        return real_record(org_id, tokens_in=tokens_in, tokens_out=tokens_out,
                           **kwargs)

    real_cas = store.put_item_if_state

    def failing_settle(org_id, item, expected, **kwargs):
        if item.state is BatchItemState.INDEXED:
            raise RuntimeError("put_item_if_state timeout")
        return real_cas(org_id, item, expected, **kwargs)

    store.put_item_if_state = failing_settle
    batch_ingest_module.record_item_spend = record
    try:
        out = coord.run_item(
            ORG, store.get_item(ORG, batch.batch_id, items[0].item_id),
            lambda i: ItemOutcome(BatchItemState.INDEXED, tokens_in=700))
        if out.charge_in or out.charge_out:
            batch_ingest_module.record_item_spend(
                ORG, tokens_in=out.charge_in, tokens_out=out.charge_out)
    finally:
        store.put_item_if_state = real_cas
        batch_ingest_module.record_item_spend = real_record

    assert charged == [700], (
        "a settle write that failed dropped a real spend from the ledger — the "
        f"runner's 700 tokens were billed as {charged}")


def test_two_settles_to_the_same_state_both_accumulate_tokens():
    """⛔ ROUND-7 REVIEW P2: the settle write must ADD, not write back a total.

    The settle path's compare is on STATE, so a runner reporting a retryable
    `extracting` over a row already `extracting` matches it — and `_advance_landed`
    rebases onto the row it READ. Two writers that both read before either writes
    therefore each compute `read + 100` and the second write OVERWRITES the first:
    200 spent and 200 billed, receipt `100`. The peer's write is made to land
    INSIDE our read→write window here, which is the only arrangement where the two
    implementations differ (sequentially the second call simply reads the first's
    result and accumulates either way — measured green under the mutation).
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/d/a.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    stale = store.get_item(ORG, batch.batch_id, items[0].item_id)

    real_cas = store.put_item_if_state
    peer = {"done": False}

    def peer_writes_first(org_id, item, expected, **kwargs):
        if not peer["done"] and item.state is BatchItemState.EXTRACTING:
            peer["done"] = True
            # A peer's settle lands between THIS call's read and its write.
            coord._advance_landed(
                ORG, stale, BatchItemState.EXTRACTING, tokens_in=100,
                expected=BatchItemState.EXTRACTING)
        return real_cas(org_id, item, expected, **kwargs)

    store.put_item_if_state = peer_writes_first
    try:
        _, landed = coord._advance_landed(
            ORG, stale, BatchItemState.EXTRACTING, tokens_in=100,
            expected=BatchItemState.EXTRACTING)
    finally:
        store.put_item_if_state = real_cas

    assert peer["done"] and landed, (
        "both settles must land for this test to prove anything")
    row = store.get_item(ORG, batch.batch_id, items[0].item_id)
    assert row.tokens_in == 200, (
        "the second settle wrote its own read+delta over the first: 200 tokens "
        f"spent, the row records {row.tokens_in}")


def test_a_status_poll_settle_cannot_rewrite_a_cancel_stamp():
    """⛔ ROUND-7 REVIEW P2: `settle()`'s derived-cancel compare must exclude CANCELLED.

    Including it made the compare MATCH the row a `cancel()` had just written:
    `settle()` reads the batch as `running`, the cancel lands its own `put_batch`
    (`cancelled_at=T`, `cancel_reason="cancelled by the customer"`), and the
    derived write then replaced BOTH with `self._now()` and the first item's
    reason. `cancelled_at` is the audit half of a billing-visible act, so the field
    that says who asked and when was rewritten by a derivation.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/d/a.md"])
    # The items are already stopped, so `settle()` will take its derived-cancel
    # arm — and the BATCH row is still `accepted`, so its copy carries no stamp.
    row = store.get_item(ORG, batch.batch_id, items[0].item_id)
    row.state = BatchItemState.CANCELLED
    row.reason = "cancelled by the customer"
    store.put_item(ORG, row)

    stamp = "2026-01-01T00:00:09+00:00"
    real_cas = store.put_batch_if_state
    fired = {"done": False}

    def cancel_lands_first(batch_row, expected):
        if not fired["done"]:
            fired["done"] = True
            # The cancel's own write, INSIDE settle's read→write window.
            real = store.get_batch(ORG, batch_row.batch_id)
            real.state = BatchState.CANCELLED
            real.cancelled_at = stamp
            real.cancel_reason = "cancelled by the customer"
            store.put_batch(real)
        return real_cas(batch_row, expected)

    store.put_batch_if_state = cancel_lands_first
    try:
        coord.settle(ORG, batch.batch_id)
    finally:
        store.put_batch_if_state = real_cas

    after = store.get_batch(ORG, batch.batch_id)
    assert fired["done"], "the interleaving never fired — the test proves nothing"
    assert after.cancelled_at == stamp, (
        "a status poll's derived write rewrote the cancel's own audit stamp: "
        f"{stamp} -> {after.cancelled_at!r}")
    assert after.cancel_reason == "cancelled by the customer"


def test_a_cancel_stops_the_queued_set_in_one_statement():
    """⛔ ROUND-7 REVIEW P2: the cancel loop was two round trips PER ITEM.

    `cancel()` walked the list and each iteration cost `get_item` +
    `put_item_if_state`. At the documented maximum batch (1 000 items) that is
    ~2 000 sequential statements inside one request on a two-worker executor: a
    504 over a cancel that keeps writing, with the customer's next status read
    queued behind it. The `queued` class is now stopped by ONE set-based
    compare-and-set; only a STALE claim still takes the per-item path.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    refs = [f"/d/{i}.md" for i in range(50)]
    batch, _items = coord.submit(ORG, refs)

    per_item: list[str] = []
    real_cas = store.put_item_if_state

    def counting_cas(org_id, item, expected, **kwargs):
        per_item.append(item.item_id)
        return real_cas(org_id, item, expected, **kwargs)

    set_based: list[int] = []
    real_bulk = store.cancel_queued_items

    def counting_bulk(org_id, batch_id, **kwargs):
        set_based.append(1)
        return real_bulk(org_id, batch_id, **kwargs)

    store.put_item_if_state = counting_cas
    store.cancel_queued_items = counting_bulk
    try:
        _, after = coord.cancel(ORG, batch.batch_id, reason="stopped")
    finally:
        store.put_item_if_state = real_cas
        store.cancel_queued_items = real_bulk

    assert len(set_based) == 1, "the queued set was not stopped by one statement"
    assert per_item == [], (
        f"the cancel still took {len(per_item)} per-item round trips")
    assert all(i.state is BatchItemState.CANCELLED for i in after)


def test_a_non_accumulating_cas_cannot_erase_a_peer_bill(monkeypatch):
    """⛔ ROUND-8 REVIEW P2: a state CAS must not write token totals at all.

    `put_item_if_state` wrote the caller's read back ABSOLUTELY on every path, and
    its compare is on state (plus attempts) — which `add_item_tokens` changes
    neither. So an accumulation landing inside the caller's read→write window was
    erased: measured, a cancel read `tokens_in=5`, the drain lost its settle and
    `_bill`ed its real 60 000 (`add_item_tokens` → 60 005), the cancel's CAS still
    matched and wrote `SET i.tokens_in=5` — 60 000 gone for good, receipt `5`
    against a ledger of 60 005.
    """
    # The claim must be STALE, so `cancel()` takes its stale-claim arm — the one
    # branch that still compares per item.
    monkeypatch.setattr(bi_module, "BATCH_RECLAIM_AFTER_S", 0)
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/d/a.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)

    real_cas = store.put_item_if_state
    fired = {"billed": False}

    def peer_bills_first(org_id, item, expected, **kwargs):
        if not fired["billed"] and expected is BatchItemState.EXTRACTING:
            fired["billed"] = True
            # The drain's `_bill`: an accumulation that changes NO compared field.
            store.add_item_tokens(ORG, item.batch_id, item.item_id,
                                  tokens_in=60_000, updated_at=item.updated_at)
        return real_cas(org_id, item, expected, **kwargs)

    # A stale claim is voided by `cancel()`: state + attempts compared, no tokens.
    store.put_item_if_state = peer_bills_first
    try:
        coord.cancel(ORG, batch.batch_id, reason="cancelled by the customer")
    finally:
        store.put_item_if_state = real_cas

    assert fired["billed"], "the interleaving never fired — the test proves nothing"
    row = store.get_item(ORG, batch.batch_id, items[0].item_id)
    assert row.tokens_in == 60_000, (
        "a state CAS wrote its stale read back over a peer's bill: the ledger "
        f"charged 60 000 and the row reports {row.tokens_in}")


def test_a_retryable_db_failure_is_requeued_not_failed():
    """⛔ ROUND-8 REVIEW P2: the indexer's retryable `failed` shape settled terminal.

    `retryable` was consulted only on the `skipped` arm. The indexer's other
    retryable shape is `{"status": "failed", "retryable": True, "db_failure": True,
    "db_reason": …}` — a classified DB write failure, the class
    `_INDEX_DB_RETRY_BUDGET` exists for — and it fell through to the terminal arm:
    a transient graph abort marked the item `failed` for ever,
    `BATCH_MAX_ATTEMPTS` never applied, the document was silently not ingested, and
    the batch reported `partial` with the generic "the indexer reported a failure"
    (that shape carries `error`/`db_reason` and no `reason`).
    """
    from tortoise import batch_ingest as batch_ingest_module

    out = batch_ingest_module.runner_result_to_outcome({
        "status": "failed", "retryable": True, "db_failure": True,
        "db_reason": "graph-abort",
        "error": {"file": "/d/a.md", "error": "db write failure (graph-abort): "
                                              "the graph aborted the write",
                  "retryable": True, "cause": "db"}})
    assert out.state is BatchItemState.QUEUED, (
        f"a transient DB failure settled {out.state} — no retry, document dropped")
    assert "graph-abort" in out.reason or "db write failure" in out.reason, (
        f"the reason must name the failure the customer can act on: {out.reason!r}")

    # A genuinely structural failure is still terminal.
    terminal = batch_ingest_module.runner_result_to_outcome({
        "status": "failed", "retryable": False,
        "error": {"error": "not valid UTF-8", "retryable": False}})
    assert terminal.state is BatchItemState.FAILED


def test_a_non_accumulating_graph_cas_names_no_token_column():
    """⛔ ROUND-8 REVIEW P2, the PRODUCTION half: the statement itself.

    The in-memory store cannot prove the graph one, and the graph form is where
    the defect cost the row its total: `SET i.tokens_in=$tin` on a state CAS writes
    the caller's stale read back. A non-accumulating CAS must not NAME the column
    at all, and an accumulating one must be RELATIVE (`coalesce(...) + $delta`),
    never absolute.
    """
    item = BatchItem(batch_id="batch_1", item_id="batch_1-00000", ref="/d/a.md",
                     state=BatchItemState.EXTRACTING, tokens_in=9, updated_at="t")

    plain = _Recorder(answers=[[["batch_1-00000"]]])
    GraphBatchStore(plain).put_item_if_state(
        ORG, item, BatchItemState.EXTRACTING, expected_attempts=1)
    cypher, params = plain.statements[-1], plain.params[-1]
    assert "tokens_in" not in cypher.split("RETURN", 1)[0], (
        "a non-accumulating CAS names a token column — that is the write-back "
        f"that erased a peer's bill: {cypher}")
    assert "tin" not in params and "tout" not in params

    adding = _Recorder(answers=[[["batch_1-00000"]]])
    GraphBatchStore(adding).put_item_if_state(
        ORG, item, BatchItemState.EXTRACTING, token_delta=(700, 0))
    cypher, params = adding.statements[-1], adding.params[-1]
    assert "coalesce(i.tokens_in, 0) + $tin" in cypher, (
        f"an accumulating CAS must ADD, not write a total: {cypher}")
    assert params["tin"] == 700 and params["tout"] == 0


def test_a_cancel_cannot_erase_a_settlement_that_landed_first(monkeypatch):
    """⛔ ROUND-9 REVIEW P2: `cancel()`'s final write was the last unconditional one.

    It was a plain `put_batch` (`MERGE … SET`, no compare), so a `settle()` that
    derived a TERMINAL state inside `cancel()`'s read→write window was erased for
    good. Measured: item A `queued` (which the cancel stops) and item B
    `extracting`; the drain settles B `indexed` with 60 000 tokens and `settle()`
    writes `partial`; this write stamped `cancelled` with an INVENTED
    `cancelled_at` over a charged document — and nothing repairs it, because
    `cancelled` is terminal, `pending_batches()` drops the row and a later
    `settle()` returns it unchanged.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, items[0], BatchItemState.INDEXED, tokens_in=60_000)
    # Item 1 stays `queued`, so the cancel has something to stop and takes its
    # `stopped` arm.

    real_cas = store.put_batch_if_state
    fired = {"done": False}

    def settle_lands_first(row, expected):
        if not fired["done"] and row.state is BatchState.CANCELLED:
            fired["done"] = True
            # The drain's `settle()` derives the terminal truth first.
            real = store.get_batch(ORG, row.batch_id)
            real.state = BatchState.PARTIAL
            real.updated_at = "2026-01-01T00:00:05+00:00"
            store.put_batch(real)
        return real_cas(row, expected)

    store.put_batch_if_state = settle_lands_first
    try:
        out_batch, _ = coord.cancel(ORG, batch.batch_id, reason="customer")
    finally:
        store.put_batch_if_state = real_cas

    assert fired["done"], "the interleaving never fired — the test proves nothing"
    row = store.get_batch(ORG, batch.batch_id)
    assert row.state is BatchState.PARTIAL, (
        "the cancel overwrote a settlement that landed first — the status now "
        f"claims work was stopped over a charged document: {row.state}")
    assert row.cancelled_at == "", (
        f"the cancel invented a cancellation stamp over a finished batch: {row.cancelled_at}")
    assert out_batch.state is BatchState.PARTIAL, (
        "the returned row must be the truth, not this call's intent")


def test_a_failed_post_runner_read_still_bills_the_spend():
    """⛔ ROUND-10 REVIEW P1: the post-runner READ was outside every `try`.

    It runs AFTER the runner has spent. One graph read failure there (this module's
    own measured 1,505 ms probe / the 10 s statement timeout) raised out of
    `run_item`; `_drain_batch` only logged it, no charge record was set, so
    `record_item_spend` was never called and `_bill` was never reached — and the
    row stayed `extracting`, so the claim went stale and a reclaim ran the runner
    AGAIN. The arms beside this read promise exactly the opposite: a bookkeeping
    failure is not a lost spend.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/d/a.md"])

    real_get = store.get_item
    ran = {"done": False}

    def failing_get(org_id, batch_id, item_id):
        # ⛔ ONLY the read AFTER the runner is made to fail. Failing an earlier one
        # exercises a different path entirely — measured: with the read failure
        # armed from the start, `run_item` returned at its liveness guard over a
        # fresh claim and the runner never ran, so the assertion held over code
        # that had not executed.
        if ran["done"]:
            raise RuntimeError("graph probe timeout")
        return real_get(org_id, batch_id, item_id)

    def runner(item):
        ran["done"] = True
        return ItemOutcome(BatchItemState.INDEXED, tokens_in=900)

    # The row `run_item` is HANDED, so the claim read is the first failing one
    # only after the runner has run.
    handed = real_get(ORG, batch.batch_id, items[0].item_id)
    store.get_item = failing_get  # type: ignore[method-assign]
    try:
        out = coord.run_item(ORG, handed, runner)
    except RuntimeError:
        raise AssertionError(
            "a read failure after the runner spent escaped run_item — the spend "
            "is then on neither the row nor the ledger") from None
    finally:
        store.get_item = real_get  # type: ignore[method-assign]

    assert (out.charge_in, out.charge_out) == (900, 0), (
        "the spend of a completed runner call was dropped because a read failed: "
        f"{(out.charge_in, out.charge_out)}")


def test_a_cancel_that_stopped_nothing_returns_the_rows_it_read():
    """⛔ ROUND-10 REVIEW P1: the `not stopped` arm returned the PRE-write snapshot.

    `items` was read before the set-based cancel, so a row the snapshot saw as
    `queued` was reported `queued` after a drain had settled it `indexed` with
    60 000 tokens: measured `status: completed`, `complete: true`,
    `tokens_spent: 0`, `items[0].status: "queued"`, `counts.pending: 1` — payload
    and route copy both asserting nothing was charged over a charged document.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/d/a.md"])
    # The snapshot `cancel()` takes sees `queued`; the row is settled before its
    # set-based write runs, so the write matches nothing and `stopped` is False.
    real_bulk = store.cancel_queued_items

    def settle_then_stop(org_id, batch_id, **kwargs):
        coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
        coord.advance(ORG, items[0], BatchItemState.INDEXED, tokens_in=60_000)
        return real_bulk(org_id, batch_id, **kwargs)

    store.cancel_queued_items = settle_then_stop  # type: ignore[method-assign]
    try:
        cancelled_batch, returned = coord.cancel(ORG, batch.batch_id,
                                                reason="customer")
    finally:
        store.cancel_queued_items = real_bulk  # type: ignore[method-assign]

    assert cancelled_batch.state is BatchState.COMPLETED
    assert [i.state for i in returned] == [BatchItemState.INDEXED], (
        "the returned rows are the PRE-write snapshot — a `queued` item reported "
        f"beside a settled, charged one: {[i.state for i in returned]}")
    assert sum(i.tokens_in for i in returned) > 0, (
        "the summary the route renders must carry the spend it reports beside "
        "`complete: true`")


def test_a_cancel_over_finished_work_reports_the_rows_it_re_read():
    """⛔ ROUND-11 REVIEW P2: the two early arms returned their SNAPSHOT too.

    `_bill` writes tokens with `add_item_tokens`, which changes NO compared field,
    so a peer's accumulation can land on a row this call already read as terminal.
    The arms that return without writing returned the snapshot, and `status_body()`
    then summed it: `tokens_spent: 0` beside a ledger that was charged.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/d/a.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, items[0], BatchItemState.INDEXED, tokens_in=100)

    real_items = store.items
    fired = {"done": False}

    def items_then_bill(org_id, batch_id):
        rows = real_items(org_id, batch_id)
        if not fired["done"]:
            fired["done"] = True
            # A peer's `_bill` lands after this call read its snapshot.
            store.add_item_tokens(org_id, batch_id, items[0].item_id,
                                  tokens_in=59_900, updated_at="t")
        return rows

    store.items = items_then_bill  # type: ignore[method-assign]
    try:
        _b, returned = coord.cancel(ORG, batch.batch_id, reason="customer")
    finally:
        store.items = real_items  # type: ignore[method-assign]

    assert fired["done"], "the interleaving never fired — the test proves nothing"
    assert sum(i.tokens_in for i in returned) == 60_000, (
        "the cancel's payload is its PRE-write snapshot: the sum reports "
        f"{sum(i.tokens_in for i in returned)} over a ledger charged 60 000")


def test_an_orphan_adoption_cannot_blank_an_acceptance_that_landed_first(monkeypatch):
    """⛔ ROUND-11 REVIEW P2: the adoption wrote the batch row unconditionally.

    `_resume_batch` adopts orphan ITEM rows and wrote the batch row with a plain
    `put_batch` — a `MERGE … SET` of state, created_at, updated_at, estimate_json,
    cancelled_at, cancel_reason and corpus_name. An acceptance landing in that
    window had its estimate and corpus BLANKED (owner §4's arithmetic gone from the
    record) and the row relabelled `resuming` over work that never stopped. The
    create is now `MERGE … ON CREATE SET`, which cannot touch an existing row.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    estimate = {"items": 1, "estimated_tokens": 64_799}

    # An ORPHAN item row: an acceptance that wrote items and died before its row.
    store.put_item(ORG, BatchItem(batch_id="batch_orphan",
                                  item_id="batch_orphan-00000", ref="/d/a.md",
                                  state=BatchItemState.QUEUED,
                                  updated_at=_ancient_stamp()))

    real_absent = store.put_batch_if_absent
    fired = {"done": False}

    def acceptance_lands_first(batch):
        if not fired["done"]:
            fired["done"] = True
            store.put_batch(Batch(batch_id=batch.batch_id, org_id=ORG,
                                  state=BatchState.ACCEPTED,
                                  created_at="2026-01-01T00:00:01+00:00",
                                  updated_at="2026-01-01T00:00:01+00:00",
                                  estimate=dict(estimate),
                                  corpus_name="handbook"))
            return False   # the row now exists; the create lost
        return real_absent(batch)

    store.put_batch_if_absent = acceptance_lands_first  # type: ignore[method-assign]
    try:
        coord.recover(ORG)
    finally:
        store.put_batch_if_absent = real_absent  # type: ignore[method-assign]

    assert fired["done"], "the interleaving never fired — the test proves nothing"
    row = store.get_batch(ORG, "batch_orphan")
    assert row is not None
    assert row.estimate == estimate, (
        f"the adoption blanked the ACK's estimate: {row.estimate!r}")
    assert row.corpus_name == "handbook", (
        f"the adoption blanked the corpus name: {row.corpus_name!r}")


def test_duplicate_of_round_trips_through_the_production_store():
    """⛔ ROUND-12/13 REVIEW P2: `duplicate_of` must survive a real ROUND TRIP.

    `submit()` sets it, `as_dict()` puts it on the wire and `run_item` branches on
    it, so it has to come back OUT of the store — and the first cut of this pin only
    grepped the CYPHER TEXT, which is exactly the half that was already right:
    `RETURN` listed `i.duplicate_of` while the constructor stopped at `r[10]`, so
    `get_item` always yielded `""` and `run_item`'s twin branch was unreachable in
    production. The fake `query` therefore answers with a FULL row and the assertion
    is on the OBJECT.
    """
    from tortoise.batch_ingest import GraphBatchStore

    # `get_item` projects (ref, name, state, reason, key, tin, tout, attempts,
    # changed, updated, staged, duplicate); `items()` prefixes the two ids.
    row_get = ["/d/b.md", "b.md", "indexed", "", "k", 5, 0, 1, False, "t",
               "/d/b.md", "b-00000"]
    row_items = ["b-00001", "/d/b.md", "b.md", "indexed", "", "k", 5, 0, 1,
                 False, "t", "/d/b.md", "b-00000"]

    class _Rec:
        def __init__(self):
            self.cyphers: list[str] = []

        def __call__(self, cypher, params=None, **kw):
            self.cyphers.append(cypher)
            # `ORDER BY`/list reads project the ids; a single-row read does not.
            rows = [row_items if "ORDER BY" in cypher else row_get]

            class _Res:
                result_set = rows

            return _Res()

    rec = _Rec()
    store = GraphBatchStore(rec)
    got = store.get_item(ORG, "b", "b-00001")
    assert got is not None and got.duplicate_of == "b-00000", (
        "get_item asks the engine for duplicate_of and then drops it, so the wire "
        f"always reports an empty twin: {got.duplicate_of if got else None!r}")

    listed = store.items(ORG, "b")
    assert listed and listed[0].duplicate_of == "b-00000", (
        "items() drops duplicate_of from the row it returns, so a list read "
        "reports every repeat as its own document")

    store.put_item(ORG, BatchItem(batch_id="b", item_id="b-00001", ref="/d/b.md",
                                  duplicate_of="b-00000", updated_at="t"))
    assert "duplicate_of=$duplicate" in rec.cyphers[-1], (
        f"put_item never writes the column: {rec.cyphers[-1][:160]}")


def test_acceptance_writes_the_whole_item_set_in_one_statement():
    """⛔ ROUND-13 REVIEW P2: 1 000 items must not be 1 000 round trips.

    Acceptance wrote one statement per item on the two-worker bookkeeping pool, so
    the documented maximum needed ~1 001 sequential statements inside ONE request.
    At this file's own measured contention that exceeds the transport budget, and
    the customer then gets a 504 over an acceptance that COMMITTED — while the route
    mints its own `batch_id` with no idempotency key, so the retry creates a SECOND
    batch: every document ingested and charged twice.

    ⛔ AND IT MUST BE PINNED THROUGH `submit` (#14415 review round 16, P2). The
    first cut called `store.put_items` directly, so restoring the per-item loop in
    `_submit_rows` — the exact regression this docstring names — left every one of
    the ~1 000 statements coming back with nothing red: the coordinator tests use
    `InMemoryBatchStore` and count no statements, and both API fixtures replace the
    store. Two statements for 200 items is the whole claim.
    """
    from tortoise.batch_ingest import BatchCoordinator, GraphBatchStore

    class _Rec:
        def __init__(self):
            self.cyphers: list[str] = []

        def __call__(self, cypher, params=None, **kw):
            self.cyphers.append(cypher)
            if "RETURN b.create_token" in cypher:
                # The engine returns the per-call token ONLY when `ON CREATE SET`
                # ran; a fake that answers `[]` makes every create look refused.
                return _Created((params or {}).get("token"))

            class _Res:
                def __init__(self) -> None:
                    self.result_set: list = []

            return _Res()

    rec = _Rec()
    coord = BatchCoordinator(GraphBatchStore(rec))
    coord.submit(ORG, [f"/d/{n}.md" for n in range(200)])
    # THREE, NOT ONE PER ITEM: the id-existence read, the item set in one `UNWIND`,
    # and the create-if-absent row. The count is CONSTANT in batch size, which is
    # the whole claim — 1 000 items must cost the same three statements.
    assert len(rec.cyphers) == 3, (
        "200 items took "
        f"{len(rec.cyphers)} statements, not 3 (id check + item set + batch row): "
        f"{[c[:60] for c in rec.cyphers[:5]]}")
    unwinds = [c for c in rec.cyphers if c.startswith("UNWIND $rows AS row")]
    assert len(unwinds) == 1, f"{len(unwinds)} item statements, expected exactly 1"


def test_a_taken_id_is_refused_and_destroys_nothing_it_did_not_create(tmp_path):
    """⛔ THE REFUSAL MUST NOT DELETE THE BATCH IT IS PROTECTING (#14415 round 18, P1).

    The pre-existing-id check landed INSIDE `_submit_rows`, i.e. inside `submit`'s
    own `try`, so its `ValueError` reached the compensating rollback — which calls
    `store.discard(org_id, bid)` and `_discard_staged(stage_base, bid)`. `bid` is
    the id of the batch that ALREADY EXISTS, so the refusal deleted the accepted
    batch outright: its row, every item row, and every staged document. That is
    strictly worse than the overwrite the guard replaced, because a reset is
    resumable and a deletion is not.

    Pinned on every half, because the old code satisfied none of them: the refusal
    must raise, the existing batch must survive unchanged, and the refused attempt
    must not have overwritten the existing batch's staged bytes on its way to being
    refused (staging ran before the check, into the same per-batch directory).
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md", "/b.md"],
                                contents=["first text", "second text"],
                                batch_id="batch_fixed", stage_base=str(tmp_path))
    staged_dir = tmp_path / "_batches" / "batch_fixed"
    assert sorted(p.name for p in staged_dir.iterdir()) == [
        "batch_fixed-00000.md", "batch_fixed-00001.md"]

    with pytest.raises(ValueError, match="already exists"):
        coord.submit(ORG, ["/c.md"], contents=["third text"],
                     batch_id="batch_fixed", stage_base=str(tmp_path))

    after, after_items = coord.status(ORG, "batch_fixed")
    assert after.state is batch.state, "the refusal changed the existing batch's state"
    assert [i.item_id for i in after_items] == [i.item_id for i in items], (
        "the refusal removed or replaced the existing batch's item rows")
    assert sorted(p.name for p in staged_dir.iterdir()) == [
        "batch_fixed-00000.md", "batch_fixed-00001.md"], (
        "the refusal deleted, added or overwrote the existing batch's staged documents")
    assert (staged_dir / "batch_fixed-00000.md").read_text() == "first text", (
        "the refused attempt staged its bytes over the existing batch's document")


def test_losing_the_create_to_a_concurrent_writer_does_not_discard_the_winner(tmp_path):
    """The loser must not DISCARD the winner's batch (#14415 round 18, P1).

    When `put_batch_if_absent` loses to a concurrent writer, `bid` belongs to the
    WRITER THAT WON. Tidy-up must not `discard(org_id, bid)` — that removes the
    winner's batch row AND its item rows, and a deletion is unrecoverable.

    ⛔ THE SCOPE OF THIS PIN IS EXACTLY "NO DISCARD", AND NOTHING WIDER. Round 19
    measured that the loser has already run `put_items` (blind `MERGE … SET`) and
    `stage_text` (same `<base>/_batches/<bid>/` names) before it learns it lost, so
    the winner's accepted item is still reset to `queued` with `attempts`/`tokens_*`
    zeroed and its staged bytes replaced — re-drained and re-charged. That hazard is
    real, is NOT undone here, and is tracked as #14498; this pin asserts only that
    the strictly-worse deletion does not also happen. Do not read it as evidence the
    concurrent path is safe.
    """
    class _LoserStore(InMemoryBatchStore):
        def __init__(self) -> None:
            super().__init__()
            self.discarded: list[str] = []

        def put_batch_if_absent(self, batch) -> bool:
            return False

        def discard(self, org_id, batch_id):
            self.discarded.append(batch_id)
            return super().discard(org_id, batch_id)

    store = _LoserStore()
    coord = BatchCoordinator(store)
    with pytest.raises(ValueError, match="created concurrently"):
        coord.submit(ORG, ["/a.md"], contents=["text"],
                     batch_id="batch_race", stage_base=str(tmp_path))

    assert store.discarded == [], (
        "the losing writer discarded a batch id that belongs to the winner: "
        f"{store.discarded}")


def test_a_caller_supplied_batch_id_cannot_escape_the_staging_root(tmp_path):
    """⛔ `batch_id` IS A PATH COMPONENT, AND NOTHING VALIDATED IT (fix lane).

    `stage_text` joins `batch_id` into `<base>/_batches/<batch_id>` and its
    docstring claimed "no customer-supplied path component can reach the
    filesystem". That was FALSE: `BatchCoordinator.submit` takes a caller-supplied
    `batch_id` keyword and passes it through verbatim, and `os.path.join` does not
    sanitise — the kernel resolves `..` at syscall time.

    Measured on the pre-fix code, this exact call wrote a document OUTSIDE the
    tenant's storage root:

        stage_text(base, "../../victim", "ok-00000", "ATTACKER BYTES")
        -> <base>/_batches/../../victim/ok-00000.md

    Pinned three ways, because refusing-by-raising alone would not prove the bytes
    stayed put: the call must refuse, nothing may appear in the outside directory,
    and the legitimate spelling must still stage normally (a guard that broke every
    batch would pass the first two).
    """
    base = tmp_path / "ingest"
    base.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "PRECIOUS.txt").write_text("unrelated tenant data")

    for hostile in ("../../victim", "..", "../victim"):
        with pytest.raises(ValueError, match="does not resolve inside"):
            bi_module.stage_text(str(base), hostile, "ok-00000", "ATTACKER BYTES")

    assert sorted(p.name for p in victim.iterdir()) == ["PRECIOUS.txt"], (
        "a hostile batch id wrote outside the staging root: "
        f"{sorted(p.name for p in victim.iterdir())}")

    # The legitimate spelling is untouched by the guard.
    staged = bi_module.stage_text(str(base), "batch_abc123", "batch_abc123-00000", "hello")
    assert staged == str(base.resolve() / "_batches" / "batch_abc123" / "batch_abc123-00000.md")
    assert (victim / "PRECIOUS.txt").read_text() == "unrelated tenant data"


def test_discard_staged_never_deletes_outside_the_batch_root(tmp_path):
    """⛔ THE DESTRUCTIVE HALF: THE ROLLBACK DELETED AN UNRELATED DIRECTORY.

    `_discard_staged` unlinks EVERY child of the resolved directory and then
    removes it, and its only input beyond the base is `batch_id`. Measured on the
    pre-fix code with `batch_id="../../victim"`:

        victim/PRECIOUS.txt  -> unlinked
        victim/              -> rmdir'd

    i.e. the compensating rollback in `submit` — the path that runs when an
    acceptance FAILS — could delete data it had never written, in a directory the
    caller named. A guard here cannot raise (the function's contract is "best
    effort, never raises"); it must simply refuse to act.
    """
    base = tmp_path / "ingest"
    (base / "_batches").mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "PRECIOUS.txt").write_text("unrelated tenant data")

    bi_module._discard_staged(str(base), "../../victim")

    assert victim.is_dir(), "a hostile batch id made _discard_staged remove the directory"
    assert (victim / "PRECIOUS.txt").read_text() == "unrelated tenant data", (
        "a hostile batch id made _discard_staged unlink unrelated data")

    # …and it still cleans up after a batch it really owns.
    bi_module.stage_text(str(base), "batch_own", "batch_own-00000", "x")
    bi_module._discard_staged(str(base), "batch_own")
    assert not (base / "_batches" / "batch_own").exists()


def test_submit_refuses_a_batch_id_that_is_not_a_single_path_component(tmp_path):
    """The boundary refusal, before ANY row or byte is written.

    The sink guard alone keeps the filesystem safe, but a caller that passes
    `batch_id="../../victim"` would then fail deep inside staging with an error
    about a directory. It is refused at the boundary instead, and — the part that
    matters — the refusal is raised BEFORE the store is touched, so a refused
    submission leaves no batch row, no item row and no staged byte behind.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "PRECIOUS.txt").write_text("unrelated tenant data")

    with pytest.raises(ValueError, match="not a single path component"):
        coord.submit(ORG, ["/a.md"], contents=["LOSER"],
                     stage_base=str(tmp_path), batch_id="../../victim")

    with pytest.raises(KeyError):
        coord.status(ORG, "../../victim")
    assert sorted(p.name for p in victim.iterdir()) == ["PRECIOUS.txt"], (
        "a refused id wrote into an unrelated directory: "
        f"{sorted(p.name for p in victim.iterdir())}")

    # A single-component caller-supplied id is still accepted (the guard is about
    # SHAPE, not about forbidding caller-supplied ids).
    batch, _items = coord.submit(ORG, ["/a.md"], contents=["ok"],
                                 stage_base=str(tmp_path), batch_id="dup-1")
    assert batch.batch_id == "dup-1"


def test_the_item_cas_writes_and_both_reads_project_every_wire_field():
    """⛔ A DROPPED COLUMN IS SILENT ON A REAL ENGINE (#14415 review round 17, P2).

    The only pin on item-row columns was the `duplicate_of` round-trip, and its fake
    answers with a HAND-BUILT row, so deleting a column from the CAS `SET` (or from
    a read projection) left all 168 tests green — the fake does not care what the
    statement says. On the real engine that is a `failed` item whose `reason` reads
    `""` (`GET /v1/batches/{id}` returns `{"status": "failed", "reason": ""}`) and a
    `duplicate_of` that silently reverts to the round-13 defect.

    The pin is table-driven off `BatchItem`'s own field list, so a field ADDED to
    the record without a column fails here rather than in production.
    """
    from tortoise.batch_ingest import GraphBatchStore

    class _Rec:
        def __init__(self):
            self.cyphers: list[str] = []

        def __call__(self, cypher, params=None, **kw):
            self.cyphers.append(cypher)
            if "RETURN b.create_token" in cypher:
                return _Created((params or {}).get("token"))

            class _Res:
                def __init__(self):
                    self.result_set: list = []

            return _Res()

    rec = _Rec()
    store = GraphBatchStore(rec)
    store.put_item_if_state(ORG, BatchItem(batch_id="b", item_id="b-00001",
                                          ref="/d/a.md", updated_at="t"),
                            BatchItemState.QUEUED)
    cas = rec.cyphers[-1]
    store.items(ORG, "b")
    listed = rec.cyphers[-1]
    store.get_item(ORG, "b", "b-00001")
    single = rec.cyphers[-1]

    # batch_id/item_id are the pattern keys, not columns.
    columns = {
        "ref": "ref", "name": "name", "staged_path": "staged_path",
        "state": "state", "reason": "reason", "content_key": "content_key",
        "duplicate_of": "duplicate_of", "tokens_in": "tokens_in",
        "tokens_out": "tokens_out", "attempts": "attempts",
        "content_changed": "content_changed", "updated_at": "updated_at",
    }
    # `charge_in`/`charge_out` are DELIBERATELY non-persisted — a fact about one
    # `run_item` call, never a property of the document, so `as_dict` does not emit
    # them and re-reading them cannot double-charge.
    missing_fields = (set(BatchItem.__dataclass_fields__)
                      - {"batch_id", "item_id", "charge_in", "charge_out"}
                      - set(columns))
    assert not missing_fields, (
        "a new `BatchItem` field has no store column — add it to `put_item`, "
        f"`put_item_if_state` and both read projections: {sorted(missing_fields)}")
    # ⛔ TWO WRITERS, TWO COLUMN SETS, and the split is deliberate: `tokens_in/out`
    # are written ONLY by an accumulating call (round 8 — naming them absolutely
    # erases a peer's bill) and `duplicate_of` is settled at ACCEPTANCE (`put_item`),
    # never by a state CAS. Every OTHER persisted column belongs to the CAS.
    cas_columns = {k: v for k, v in columns.items()
                   if k not in {"tokens_in", "tokens_out", "duplicate_of"}}
    unset = [c for c in cas_columns.values()
             if f"i.{c}=" not in cas.replace(" ", "")]
    assert not unset, f"the CAS never writes: {unset}\n{cas[:300]}"
    assert "i.tokens_in=" not in cas.replace(" ", ""), (
        "a non-accumulating CAS named a token column — that is the read-modify-"
        "write that erases a peer's bill")

    store.put_item(ORG, BatchItem(batch_id="b", item_id="b-00001", ref="/d/a.md",
                                  updated_at="t"))
    upsert = rec.cyphers[-1].replace(" ", "")
    unset = [c for c in columns.values() if f"i.{c}=" not in upsert]
    assert not unset, (
        f"`put_item` never writes: {unset} — the acceptance's own writer must name "
        "every persisted column")
    unprojected = [c for c in columns.values() if f"i.{c}" not in listed]
    assert not unprojected, f"`items()` never projects: {unprojected}"
    unprojected = [c for c in columns.values() if f"i.{c}" not in single]
    assert not unprojected, f"`get_item()` never projects: {unprojected}"


def test_the_create_if_absent_reports_creation_only_for_this_call():
    """⛔ THE VERDICT MUST BE A FACT ONLY THIS CALL CAN PRODUCE (#14415 cycle-2, MEDIUM).

    Two earlier cuts of this verdict were both wrong, in the same direction:

    * it returned `b.state` and compared that — TRUE for a pre-existing row whose
      state string equals the caller's, and the caller is `_resume_batch`, where
      two adoptions of ONE orphan row both pass `RESUMING`;
    * it then compared `b.created_at` — and `_resume_batch` derives that stamp from
      `orphan_items[0].updated_at`, so the SAME two adoptions also pass the same
      stamp and the second still read as CREATED. MEASURED on the live engine
      before this fix: a pre-existing row with an equal stamp returns it, so the
      old comparison answers `created: True` over a row it did not write.

    The creation test is therefore a per-call token, written only by
    `ON CREATE SET`: a value a reused row cannot carry. An empty RETURN is also
    NOT evidence of creation — the old `not rows or …` failed OPEN.
    """
    from tortoise.batch_ingest import GraphBatchStore

    batch = Batch(batch_id="b", org_id=ORG, state=BatchState.RESUMING,
                  created_at="2026-01-01T00:00:00+00:00",
                  updated_at="2026-01-01T00:00:00+00:00")

    class _Rec:
        """Echoes `echo`; `echo="__mine__"` replays the caller's own token."""

        def __init__(self, echo: str) -> None:
            self.echo = echo
            self.cyphers: list[str] = []
            self.params: list[dict] = []

        def __call__(self, cypher, params=None, **kw):
            self.cyphers.append(cypher)
            self.params.append(dict(params or {}))
            token = (self.params[-1].get("token") if self.echo == "__mine__"
                     else self.echo)

            class _Res:
                def __init__(self) -> None:
                    self.result_set = [[token]]

            return _Res()

    created = _Rec("__mine__")
    assert GraphBatchStore(created).put_batch_if_absent(batch) is True, (
        "the row THIS call created was not reported as created")
    stmt = created.cyphers[-1]
    assert "ON CREATE SET" in stmt and "b.create_token=$token" in stmt, (
        f"the create-if-absent lost its creation marker: {stmt[:200]}")

    # ⛔ THE CASE THE PREVIOUS CUT COULD NOT SEE: the reused row carries the SAME
    # created_at the caller derived from its orphan items, and a token that is not
    # ours. Only the token can tell those apart.
    reused = _Rec("0193f4aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert GraphBatchStore(reused).put_batch_if_absent(batch) is False, (
        "a pre-existing row was reported as CREATED — the caller then trusts its "
        "own (orphan-shaped) object instead of re-reading what the ACK wrote")

    class _Empty(_Rec):
        def __call__(self, cypher, params=None, **kw):
            self.cyphers.append(cypher)
            self.params.append(dict(params or {}))

            class _Res:
                def __init__(self) -> None:
                    self.result_set: list = []

            return _Res()

    assert GraphBatchStore(_Empty("__mine__")).put_batch_if_absent(batch) is False, (
        "an empty RETURN was read as 'I created it' — the verdict fails open")


# ── round 21: a minted id can lose its create, and to OUR OWN recovery scan ──
class _AdoptingStore(InMemoryBatchStore):
    """A store whose `put_items` is followed by the recovery scan's adoption.

    This is the measured interleaving, not a hypothetical: `submit()` writes the
    ITEM rows before the batch row (deliberately), `pending_batches()` UNIONs the
    item arm, and `_resume_batch` adopts item rows whose batch row is absent by
    calling `put_batch_if_absent` — so the RECOVERY SCAN can take the create from
    the acceptance that wrote those very rows.
    """

    def put_items(self, org_id, items):
        super().put_items(org_id, items)
        # The adoption builds its stamp from `orphan_items[0].updated_at`, which
        # is the stamp the acceptance put on the rows it just wrote.
        super().put_batch(Batch(batch_id=items[0].batch_id, org_id=org_id,
                                state=BatchState.RESUMING,
                                created_at=items[0].updated_at,
                                updated_at=items[0].updated_at))


def test_a_minted_submission_survives_its_own_recovery_scan_adopting_it():
    """#14415 round 21 (HIGH): a lost create on a MINTED id is not a conflict.

    The comment at the failing arm said a lost create was "only possible for a
    caller-supplied one". It is not: the recovery scan adopts the item rows this
    acceptance had written a statement earlier. The `_BatchIdConflict` it raised
    is a `ValueError`, which `create_batch` renders as **503 "nothing was
    accepted; retry"** — over rows a drain had already been told to run. Measured
    before the fix: the customer is told nothing landed, the documents index and
    charge anyway, and the retry opens a SECOND batch over the same content.
    """
    store = _AdoptingStore()
    coord = BatchCoordinator(store)
    estimate = {"items": 2, "estimate_tokens": 129_598}
    batch, items = coord.submit(ORG, ["/d/a.md", "/d/b.md"], estimate=estimate)

    # No conflict, and the RECORD is ours: the estimate the ACK is required to
    # carry (owner #14275 §4) must survive the adoption, not be blanked by it.
    assert batch.state is BatchState.ACCEPTED
    assert batch.estimate == estimate
    recorded = store.get_batch(ORG, batch.batch_id)
    assert recorded is not None, "the acceptance reported a batch with no row"
    assert recorded.estimate == estimate, (
        "the estimate was lost to the recovery scan's empty adoption — the ACK "
        "then carries arithmetic the record does not")
    assert [i.item_id for i in store.items(ORG, batch.batch_id)] == [
        i.item_id for i in items]


def test_a_read_fault_while_deciding_ownership_refuses_and_deletes_nothing():
    """⛔ A FAULT IS "NOT PROVABLY OURS" — IT MUST NOT REACH THE ROLLBACK (round 22, MEDIUM).

    The lost-create arm reads the row to decide whether it may take it over, and that
    read sits inside `submit`'s `try`. `submit`'s `except Exception` does
    `self._store.discard(org_id, bid)` + `_discard_staged(...)` — which deletes the
    batch row, its item rows AND its staged bytes. When the read itself fails (graph
    over budget, a broken socket) the call cannot know who owns the id, so rolling
    back deletes a batch it may not own. Measured at `874bcd74`: `submit` raised
    `RuntimeError` and the winner's row was GONE with its items; with only that hunk
    guarded, `_BatchIdConflict` and the winner's row and items are intact.
    """
    class _FaultyReadStore(InMemoryBatchStore):
        """The two id checks read free; the ownership read RAISES."""

        def __init__(self) -> None:
            super().__init__()
            self.reads = 0
            self.discarded: list[str] = []

        def get_batch(self, org_id, batch_id):
            self.reads += 1
            if self.reads <= 2:
                return None            # `submit`'s check and `_submit_rows`' own
            raise RuntimeError("graph over budget: the row could not be read")

        def put_batch_if_absent(self, batch) -> bool:
            return False               # a concurrent writer won the create

        def discard(self, org_id, batch_id):
            self.discarded.append(batch_id)
            return super().discard(org_id, batch_id)

    store = _FaultyReadStore()
    coord = BatchCoordinator(store)
    # The EXCEPTION TYPE is not the point — the DELETION is. Asserted on the store, so
    # this fails for the right reason whichever way the fault surfaces: without the
    # guard the fault escapes into `submit`'s rollback, which discards the id.
    with pytest.raises((ValueError, RuntimeError)):
        # No inline contents: staging would fail first and never reach the arm.
        coord.submit(ORG, ["/d/a.md"], batch_id="owned-by-someone-else")

    assert store.discarded == [], (
        "a READ FAULT was answered by discarding the batch id — the call could not "
        f"know who owned it: {store.discarded}")
    assert store.reads >= 3, (
        f"the ownership read was never reached ({store.reads} reads)")


def test_a_caller_supplied_id_that_was_adopted_is_still_a_conflict():
    """The takeover is scoped to MINTED ids — a caller's clash stays a refusal.

    Two submissions carrying the same caller-supplied id are the #14498 race, and
    the loser must not adopt the winner's row on the strength of this fix.
    """
    store = _AdoptingStore()
    coord = BatchCoordinator(store)
    coord.submit(ORG, ["/d/a.md"], batch_id="dup-1")
    with pytest.raises(ValueError, match="already exists"):
        coord.submit(ORG, ["/d/b.md"], batch_id="dup-1")


# ── round 21: the retry ceiling must bound a PRE-CLAIM fault too ─────────────
class _FlakyReadStore(InMemoryBatchStore):
    """`get_item` raises for one row until it has raised `faults` times."""

    def __init__(self, faults: int = 1) -> None:
        super().__init__()
        self.faults = faults
        self.raises = 0

    def get_item(self, org_id, batch_id, item_id):
        if self.raises < self.faults:
            self.raises += 1
            raise RuntimeError("graph probe timeout")
        return super().get_item(org_id, batch_id, item_id)


def test_a_pre_claim_read_fault_is_counted_and_reaches_the_retry_ceiling():
    """#14415 round 21 (MEDIUM): `BATCH_MAX_ATTEMPTS` bounded nothing here.

    `attempts` is written by one statement — the claim CAS — which a fault BEFORE
    the claim never reaches. `_drain_batch` re-drives while any item is
    non-terminal and its comment says the ceiling "is what bounds the loop", so
    the loop was unbounded for exactly the faults it named: measured on the pre-fix
    code, ten `run_item` calls left `attempts: 0`, `state: queued`, the runner never
    invoked, and a drain pass burned every retry tick for the process lifetime.
    """
    store = _FlakyReadStore(faults=1)
    coord = BatchCoordinator(store)
    _, items = coord.submit(ORG, ["/d/a.md"])
    item = items[0]

    with pytest.raises(RuntimeError):
        coord.run_item(ORG, item, lambda *a, **k: None)

    assert store.get_item(ORG, item.batch_id, item.item_id).attempts == 1, (
        "the failed pass was not counted, so no ceiling can ever be reached")

    # Exhaust the remaining passes; the item must end VISIBLY failed, not spin.
    for _ in range(bi_module.BATCH_MAX_ATTEMPTS + 2):
        store.raises, store.faults = 0, 1      # one fault per pass, before the claim
        with pytest.raises(RuntimeError):
            coord.run_item(ORG, item, lambda *a, **k: None)
    settled = store.get_item(ORG, item.batch_id, item.item_id)
    assert settled.state is BatchItemState.FAILED, (
        "a condition that never clears must settle `failed` visibly, not stay "
        "`queued` for the life of the process")
    assert "retry ceiling" in settled.reason


# ── round 22: the adoption's liveness rule, the takeover's compare, and the ──
# ── ceiling's liveness rule                                                 ──
def test_a_young_orphan_is_an_acceptance_in_flight_and_is_not_adopted():
    """#14415 round 22 (HIGH): the adoption applied no liveness window at all.

    Acceptance writes ITEM rows before the batch row on purpose, and
    `pending_batches()` UNIONs the ITEM arm — so a recovery scan running while an
    acceptance is inside `_submit_rows` sees the orphan shape by construction and
    adopted it, taking the create out from under the acceptance (whose own
    `put_batch_if_absent` then failed and answered "nothing was accepted" over rows
    already queued for a drain). Every other caller here refuses a row inside the
    reclaim window; this one now does too.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/d/a.md"])
    del store._batches[(ORG, batch.batch_id)]        # the crash shape: items, no row

    assert coord.recover(ORG) == [] or store.get_batch(
        ORG, batch.batch_id) is None, (
        "a FRESH orphan was adopted — that is the acceptance's own row being "
        "taken before it can write it")

    # …and the same rows ARE adopted once the window has passed, so a genuine
    # crash orphan is deferred, never stranded.
    stale = store.get_item(ORG, batch.batch_id, items[0].item_id)
    stale.updated_at = "2020-01-01T00:00:00+00:00"
    store.put_item(ORG, stale)
    coord.recover(ORG)
    adopted = store.get_batch(ORG, batch.batch_id)
    assert adopted is not None, (
        "a stale orphan was never adopted — the deferral stranded it")
    assert adopted.state is BatchState.RESUMING


class _AdoptThenCancelStore(_AdoptingStore):
    """Adoption wins the create, and a cancel lands before the takeover writes."""

    def __init__(self) -> None:
        super().__init__()
        self.coord: BatchCoordinator | None = None
        self.fired = False

    def put_batch_if_state(self, batch, expected, *, write_identity=False):
        if not self.fired and self.coord is not None:
            self.fired = True
            self.coord.cancel(batch.org_id, batch.batch_id,
                              reason="customer said stop")
        return super().put_batch_if_state(
            batch, expected, write_identity=write_identity)


# The acceptance's clock, frozen far enough back that its own item rows are
# STALE — which is the only way an adoption may take them now — while still
# being the stamp the acceptance owns (so ownership is provable, as in the real
# interleaving: the adoption copies the acceptance's own item stamp).
_OLD = lambda: "2020-01-01T00:00:00+00:00"  # noqa: E731


def test_the_takeover_never_overwrites_a_cancel_and_never_writes_blind():
    """#14415 round 22 (HIGH): `put_batch` has no compare, so it erased a cancel.

    Measured on the previous cut: a `cancel()` landing in the takeover's
    read->write gap came back `accepted` with `cancelled_at=''` over items
    `['cancelled','cancelled']` — the customer's own audit half of a
    billing-visible act, erased. The identity is now written UNDER the same
    compare as the state, so a cancel wins and nothing is overwritten.
    """
    store = _AdoptThenCancelStore()
    coord = BatchCoordinator(store, now=_OLD)
    store.coord = coord

    batch, _items = coord.submit(ORG, ["/d/a.md"], estimate={"items": 1})
    recorded = store.get_batch(ORG, batch.batch_id)
    assert recorded is not None
    assert recorded.state is BatchState.CANCELLED, (
        f"the takeover overwrote the cancel: {recorded.state}")
    assert recorded.cancel_reason == "customer said stop", (
        f"the cancel's reason was erased: {recorded.cancel_reason!r}")
    assert recorded.cancelled_at, "the cancel's stamp was erased"


def test_a_takeover_writes_the_estimate_when_no_one_else_moved_the_row():
    """The other half: the takeover must still restore the ACK's arithmetic."""
    store = _AdoptingStore()
    coord = BatchCoordinator(store, now=_OLD)
    estimate = {"items": 1, "estimated_tokens": 64_799}
    batch, _items = coord.submit(ORG, ["/d/a.md"], estimate=estimate)
    assert batch.state is BatchState.ACCEPTED
    assert store.get_batch(ORG, batch.batch_id).estimate == estimate, (
        "the takeover reported success but did not write the ACK's arithmetic")


def test_a_live_peers_claim_is_never_settled_by_the_retry_ceiling():
    """#14415 round 22 (MEDIUM): the new counting path skipped the liveness window.

    Measured before the guard: a peer claim `extracting, attempts 2, age 0 s`
    (window 900 s) became `failed` after three local read faults — and
    `ITEM_TRANSITIONS[FAILED]` is empty, so the peer's real outcome has nowhere to
    land and `run_item`'s illegal-edge arm bills the spend and DROPS the document.
    """
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    _, items = coord.submit(ORG, ["/d/a.md"])
    for _ in range(bi_module.BATCH_MAX_ATTEMPTS):
        coord.advance(ORG, store.get_item(
            ORG, items[0].batch_id, items[0].item_id),
            BatchItemState.EXTRACTING, attempt=True)

    for _ in range(bi_module.BATCH_MAX_ATTEMPTS + 3):
        coord._note_failed_pass(ORG, items[0], RuntimeError("graph probe timeout"))

    row = store.get_item(ORG, items[0].batch_id, items[0].item_id)
    assert row.state is BatchItemState.EXTRACTING, (
        "the retry ceiling settled a LIVE peer claim, which makes its outcome "
        f"unlandable (billed-and-dropped): {row.state}")
    assert row.attempts == bi_module.BATCH_MAX_ATTEMPTS, (
        f"a live claim's attempt counter was moved: {row.attempts}")
