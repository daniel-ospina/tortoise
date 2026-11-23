"""#14219 (F4): the batch-ingest layer — acceptance, per-item state, dedup,
resume, cancel, estimate and the isolation bound.

Every test here is written against PRE-FIX code that has no
``tortoise.batch_ingest`` module at all, so the module import alone fails them
(non-vacuity by construction); each one then pins a behaviour the brief names as
acceptance, and the docstring says which.
"""
from __future__ import annotations

import threading

import pytest

from tortoise.batch_ingest import (
    ACCEPT_COPY,
    ACKNOWLEDGEMENT_TOKEN_THRESHOLD,
    BATCH_ITEMS_IN_FLIGHT,
    OVERAGE_USD_PER_MILLION_TOKENS,
    PROVIDER_MECHANICS_WORDS,
    SECONDS_PER_DOCUMENT,
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


def test_content_key_matches_the_repo_precedent():
    """One definition, not three: the key equals ``file_indexer.hash_text``.

    If these ever diverge, the batch reports "new" for documents the indexer
    calls "unchanged" (and vice versa) — two answers to one question.
    """
    from tortoise.file_indexer import hash_text

    text = "# Doc\n\nbody\n"
    assert content_key(text) == hash_text(normalise_text(text))


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
    resumed = BatchCoordinator(store, blocking).recover(ORG)

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
    store = InMemoryBatchStore()
    coord = BatchCoordinator(store)
    batch, items = coord.submit(ORG, ["/a.md"])
    coord.advance(ORG, items[0], BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG, items[0], BatchItemState.INDEXED)
    assert store.pending_batches(ORG) == []
    assert coord.settle(ORG, batch.batch_id).state is BatchState.COMPLETED


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
    assert "1 hours" in customer_copy(0.1)
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
        assert worker.executor is not hosted_api._CAPTURE_EXECUTOR
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

    resumed = coord.recover(ORG, live_batches={live_batch.batch_id})

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
    _batch, items = coord.submit(ORG, ["/locked.md"])

    outcomes = []

    def locked(item: BatchItem) -> ItemOutcome:
        outcomes.append(item.attempts)
        return ItemOutcome(BatchItemState.QUEUED, reason="index lock held")

    for _ in range(bi.BATCH_MAX_ATTEMPTS + 2):
        coord.run_item(ORG, store.items(ORG, _batch.batch_id)[0], locked)

    final = store.items(ORG, _batch.batch_id)[0]
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
