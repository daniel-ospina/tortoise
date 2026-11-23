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
    # ⛔ The claim is aged FIRST: the reclaim window applies to every caller
    # (#14415 review round 10), so a 0 s claim is deliberately left alone and a
    # later scan settles it. This test is about the row not stranding, which is
    # what the stale claim proves.
    stale = store.get_item(ORG, batch.batch_id, items[0].item_id)
    stale.updated_at = _ancient_stamp()
    store.put_item(ORG, stale)
    assert BatchCoordinator(store).recover(ORG) == []
    after = {i.ref: i for i in coord.status(ORG, batch.batch_id)[1]}
    assert after["/a.md"].state is BatchItemState.CANCELLED
    assert after["/a.md"].tokens_in == 0
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

    def cancelling_cas(org_id, item, expected, expected_attempts=None):
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
    real_put = store.put_item
    seen = {"n": 0}

    def _flaky(org_id, item):
        seen["n"] += 1
        if seen["n"] == 2:
            raise TimeoutError("graph timeout")
        return real_put(org_id, item)

    store.put_item = _flaky  # type: ignore[method-assign]
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

    def racing_cas(org_id, item, expected, expected_attempts=None):
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
def _every_store_statement() -> list[str]:
    """Every Cypher statement `GraphBatchStore` can emit, by driving all of it.

    A recording `query` returns an empty result set, so nothing is asserted about
    RESULTS here — only about the STATEMENTS this module hands to FalkorDB. That
    is deliberate: the fake `query` in the rest of this suite accepts ANY string,
    which is exactly why a malformed statement went unnoticed.
    """
    seen: list[str] = []

    class _Res:
        def __init__(self) -> None:
            self.result_set: list = []

    def _record(cypher, params=None, timeout=None, _seen=seen):
        _seen.append(cypher)
        return _Res()

    store = GraphBatchStore(_record)
    batch, items = BatchCoordinator(InMemoryBatchStore()).submit(
        ORG, ["/a.md", "/b.md"])
    store.put_batch(batch)
    store.get_batch(ORG, batch.batch_id)
    store.put_item(ORG, items[0])
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
    return seen


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
    assert len(seen) >= 14, f"only {len(seen)} statements reached the store"
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
    batch, items = coord.submit(ORG, ["/a.md"])
    stale = store.get_item(ORG, batch.batch_id, items[0].item_id)
    stale.updated_at = _ancient_stamp()  # a genuinely dead claim
    store.put_item(ORG, stale)

    real_put = store.put_batch_if_state

    def _cancel_first(b, expected):
        BatchCoordinator(store).cancel(ORG, batch.batch_id, reason="customer")
        return real_put(b, expected)

    store.put_batch_if_state = _cancel_first  # type: ignore[method-assign]
    # The return value is allowed to list the batch (the peer's design reports it
    # so `_drain_batch` visits and settles it); what must NOT happen is the write
    # landing over the cancel, so that is what is asserted.
    coord.recover(ORG)

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
    finally:
        graph.query("MATCH (n) DETACH DELETE n")
        graph.delete()


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
    filtered = [s for s in seen
                if s.lstrip().startswith(("MATCH", "MERGE"))
                and (BATCH_LABEL in s or ITEM_LABEL in s)]
    assert filtered, "no statement names a batch label — the assertion is vacuous"
    missing = [s for s in filtered if "org_id:$org" not in s]
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
