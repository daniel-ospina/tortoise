"""#14219 (plan Track **F4**): the bulk-ingest product surface — a customer hands
us a BATCH of documents.

⛔ **NAMING — measured, not a preference.** ``/v1/packs`` is already taken and
it is a LIVE PUBLIC NOUN: ``GET /v1/packs`` (``hosted_api.py::list_packs``,
#318/#9583) returns the tenant's active ``:PackInstall`` packs and
``POST /v1/packs/manifests`` (``hosted_api.py::upload_pack_manifest``,
#1935/epic #1891) uploads a business/kind pack. A "pack" in this product is a
**business pack**, never a bundle of documents. So the bulk-ingest surface is
named **batch** here and on the wire (``/v1/batches``, ``batch_id``); a client
generated from our OpenAPI must not have to guess which "pack" it is holding.

## What this module is, and what it is not

It is the **product layer** the plan's F1/F2/F3 do not provide: batch identity,
a durable per-item row, a per-item state machine, content-hash idempotency,
resumability, a declared concurrency ceiling, a proactive estimate and a cancel
verb. F1 (embedder seam + micro-batching) and F2 (projection on a worker loop)
make bulk work *survivable*; they do not make it a *product*.

It is **not** a second ingest pipeline. Per-item work is delegated to an
injected :class:`ItemRunner`. In production that runner is the repo's EXISTING
per-file indexer unit (:meth:`tortoise.sdk.TortoiseSDK._index_process_unit`,
the body of the ``index_sources_from_directory`` walk, #1726), so a batch item
is filed through the same door, with the same guards, the same pack/kind
resolution and the same ``:Source`` identity as any other document. ⛔ A bulk
path that wrote raw points would produce exactly the bloat the capability audit
(#13083) is removing.

## The decisions this module implements, quoted

(a) ⛔ **the 10 s wait bound is not raised** (owner decision #3834). A batch
    ACKs immediately — :meth:`BatchCoordinator.submit` only writes durable rows
    and returns; nothing waits for extraction. There is no join window here at
    all, so ``_CAPTURE_ACK_DEADLINE_S``'s problem (a compatibility join) does
    not arise: the ack is a few graph writes, and the work is behind a worker.

(b) ⛔ **one token budget, sessions AND documents** (owner, #14275). Items are
    metered into the SAME ``capture_extraction`` dimension
    (``max_extraction_tokens``, ``allowance_gate.ALLOWANCE_KEYS``) through the
    same ledger writer (``metering.record_capture_usage``) — never a second
    meter. :func:`estimate_batch` reads that dimension's own meter; it does not
    define a new one.

(c) ⛔ **batch document processing is OFF-PEAK ONLY**; on-demand is for fewer
    than ~5–10 documents' equivalent (owner, 2026-11-23). This layer makes
    scheduling POSSIBLE (every item is a durable ``queued`` row that any worker
    may pick up at any hour, and :func:`estimate_batch` publishes the expected
    completion window instead of pretending to be interactive). It does not
    implement the scheduler: see :func:`defer_until_off_peak` for the one seam a
    scheduler plugs into, and the declared residual in the PR body.

(d) ⛔ **never expose provider pricing mechanics in customer copy.** The
    acceptance/estimate wording is :data:`ACCEPT_COPY` — *"batch processed,
    completion expected in X hours, cancel any time"*. The off-peak mechanism is
    an implementation detail; :func:`customer_copy` is unit-tested to contain no
    provider-rate vocabulary.

(e) ⛔ **graph storage and the token allowance are SEPARATE budgets** (owner,
    2026-11-23). Nothing here trades one for the other: the estimate reports
    tokens against the token allowance only, and the graph cap is the storage
    door's own gate.

(f) **content-hash dedup BEFORE spending** tokens (``billing.cost_basis.dedup =
    content_hash``; ``tortoise/idempotency.py``'s ``IngestKey``). The key is
    derived from the TEXT (:func:`content_key`) and there are exactly TWO
    enforcement points, both ahead of any spend:

    * **items whose text the caller supplied** (an upload) are keyed at
      acceptance, so a duplicate is settled ``skipped-duplicate`` by the
      coordinator WITHOUT the runner being called — the layer's structural
      check, and the one a test pins with a runner that fails if reached;
    * **path items** are keyed by the INDEXER once it has read the file (its
      stored ``:Source.contentHash`` vs the file's hash). That is the SAME rule
      (:func:`content_key` is asserted equal to ``file_indexer.hash_text``), and
      it is still decided before the three LLM passes — the layer cannot pre-read
      a file it has not opened, and doing so would double every read. What the
      layer does guarantee there is that the verdict is *surfaced* as
      ``skipped-duplicate`` rather than silently reported as success
      (``runner_result_to_outcome``).

    See :func:`content_key` for what "identical" means and how a CHANGED file is
    distinguished from a duplicate (#13451's stale-vector trap).

(g) ``#4010``: no capture caps — items are never dropped for lack of budget.
    When the allowance is exhausted the document is **stored, not extracted**
    (``_CAPTURE_EXTRACTION_DISABLED_MODE`` shape, #4258) and the item says so;
    a bulk ingest must never lose a customer's document to a token budget.

(h) ⛔ **no MCP/SDK surface change** without Daniel's approval. This module adds
    a REST route family only — no tool, no public SDK method.
"""
from __future__ import annotations

import contextlib
import hashlib
import math
import threading
import time
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

# ── the measured unit (#14295 / #14343, the fact behind #14275's prior) ──────
#: Measured tokens spent per MEDIAN document by the real ``extract_session_v2``
#: pipeline at the production model: 45,503 constant input + 2.159T input and
#: 22,802 constant output + 0.180T, summed at T = 5,211 tokens.
#:
#: ⛔ This is the number the owner ruled the estimate must be built from
#: ("≈64,799 tokens per median document"), and it is a SPEND figure — not the
#: document's own size (5,211), which is the trap #14295 exists to correct: a
#: small item is not "relatively expensive", it is *entirely* overhead, because
#: S2 and S4 do not scale with the document at all (R² = 0.000 / 0.009).
TOKENS_PER_MEDIAN_DOCUMENT = 64_799

#: Measured wall clock per document, cold arm (#14295): 56.25 s. This is what
#: makes 10 documents ≈ 9–10 minutes — the latency half of the owner's on-demand
#: threshold (c), not a cost argument.
SECONDS_PER_DOCUMENT = 56.25

#: The overage rate the owner confirmed (#14275 comment, 2026-11-23): $1.00 per
#: 1M tokens, which sits 2.4× above measured cost at peak and 4.8× off-peak.
#: ⛔ Reported as a figure, never as a cost narrative in customer copy (d).
OVERAGE_USD_PER_MILLION_TOKENS = 1.00

#: The on-demand threshold (owner, 2026-11-23): "on-demand is for < ~5–10
#: documents' equivalent; batch document processing is off-peak only". Published
#: so the estimate can say which side of it a batch is on: at or below this many
#: items the work is small enough to be interactive, above it the customer is
#: told it is batch-processed.
ON_DEMAND_MAX_ITEMS = 10

#: The largest inline payload ONE batch request may carry (the sum of its items'
#: text). ⛔ The same constant is registered as the route's body cap in
#: ``hosted_api``'s ``CappedBodyMiddleware`` (``path_caps["/v1/batches"]``), so the
#: bound the handler advertises is REACHABLE: without that entry the middleware's
#: 256 KiB default refused first and the handler's "send it as several batches"
#: guidance was dead code (#14219 review).
BATCH_MAX_INLINE_BYTES = 4 * 1024 * 1024
BATCH_INLINE_413_DETAIL = (
    "batch request body exceeds the size cap (4 MiB) — send it as several "
    "batches; nothing is dropped")

#: Above this many tokens of expected spend, the acceptance requires an explicit
#: acknowledgement (owner, 2026-11-23 §4: "for a large processing batch we should
#: pro-actively calculate the amount and tell them they'll need X overage"). The
#: threshold is one included allowance's worth of a SOLO month (3M, #14275) — a
#: batch that could exceed a month's included amount is the case the owner is
#: describing, and anything smaller is already visible in the same receipt.
ACKNOWLEDGEMENT_TOKEN_THRESHOLD = 3_000_000


# ── the item state machine ──────────────────────────────────────────────────
class BatchItemState(StrEnum):
    """Every state a batch item can be observed in (the wire vocabulary).

    ``queued`` → ``extracting`` → ``embedded`` → ``indexed`` is the happy path.
    The three off-ramps are all TERMINAL and all carry a reason string:
    ``failed`` (this item is broken), ``skipped_duplicate`` (this item is
    already in the graph — a no-op that spent nothing), ``cancelled`` (the
    customer stopped the batch; nothing more will be attempted).

    ⛔ ``failed`` is an ITEM outcome, never a batch refusal (the AX rule): one
    bad file must not fail the other 499. The batch's own summary publishes
    ``partial`` when any item failed and at least one succeeded.
    """

    QUEUED = "queued"
    EXTRACTING = "extracting"
    EMBEDDED = "embedded"
    INDEXED = "indexed"
    FAILED = "failed"
    SKIPPED_DUPLICATE = "skipped-duplicate"
    CANCELLED = "cancelled"


TERMINAL_ITEM_STATES: frozenset[BatchItemState] = frozenset({
    BatchItemState.INDEXED,
    BatchItemState.FAILED,
    BatchItemState.SKIPPED_DUPLICATE,
    BatchItemState.CANCELLED,
})

#: The ONLY legal edges. Anything else raises :class:`IllegalTransition`, which
#: is what makes "an item never silently regresses" (a duplicate settled back to
#: `extracting`, an `indexed` item re-run) a property rather than a hope.
#:
#: ``extracting`` → ``queued`` is legal and load-bearing: it is the RESUME edge
#: (#14219 item 4). An item found in ``extracting`` at boot belongs to a process
#: that died mid-call; its claim is not evidence the work finished, so the item
#: returns to ``queued`` (with its attempt count incremented) rather than being
#: abandoned as lost or guessed terminal.
ITEM_TRANSITIONS: Mapping[BatchItemState, frozenset[BatchItemState]] = {
    BatchItemState.QUEUED: frozenset({
        BatchItemState.EXTRACTING, BatchItemState.SKIPPED_DUPLICATE,
        BatchItemState.CANCELLED, BatchItemState.FAILED,
    }),
    # ``extracting`` admits the terminal successes DIRECTLY, and that is not a
    # hole in the machine: ``embedded`` is a PROGRESS state, not a gate. The
    # production runner is the repo's per-file indexer unit, which embeds and
    # files in one call and reports ``indexed`` — while a staged runner may
    # report ``embedded`` first. Both are the same pipeline observed at
    # different resolutions, so both edges exist. ``skipped-duplicate`` from
    # ``extracting`` is the one that matters most: a path item's content is only
    # known AFTER it is read, so the indexer (not this layer) is what discovers
    # "unchanged" — and that verdict must be able to settle the item without
    # pretending it was never attempted.
    BatchItemState.EXTRACTING: frozenset({
        BatchItemState.EMBEDDED, BatchItemState.INDEXED,
        BatchItemState.SKIPPED_DUPLICATE, BatchItemState.FAILED,
        BatchItemState.QUEUED,          # the resume edge (see above)
    }),
    BatchItemState.EMBEDDED: frozenset({
        BatchItemState.INDEXED, BatchItemState.SKIPPED_DUPLICATE,
        BatchItemState.FAILED,
        BatchItemState.QUEUED,          # resume: died between embed and index
    }),
    BatchItemState.INDEXED: frozenset(),
    BatchItemState.FAILED: frozenset(),
    BatchItemState.SKIPPED_DUPLICATE: frozenset(),
    BatchItemState.CANCELLED: frozenset(),
}


class IllegalTransition(RuntimeError):
    """An item-state edge the machine does not have. Never swallowed."""


class BatchState(StrEnum):
    """The batch's own status.

    ``resuming`` exists because #14219 item 4 demands that a resumed batch SAY
    it is resuming ("the batch's own status says what it is resuming") — a batch
    that silently re-enters ``running`` after a restart is indistinguishable
    from one that never stopped, and the customer cannot tell whether work was
    lost.
    """

    ACCEPTED = "accepted"
    RUNNING = "running"
    RESUMING = "resuming"
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


TERMINAL_BATCH_STATES: frozenset[BatchState] = frozenset({
    BatchState.COMPLETED, BatchState.PARTIAL, BatchState.CANCELLED,
})


# ── content identity: what "identical" means ────────────────────────────────
def normalise_text(text: str) -> str:
    """Universal-newline normalisation — the buffer that is hashed.

    ``\\r\\n`` and a lone ``\\r`` become ``\\n``. Nothing else is touched: no
    whitespace trimming, no case folding, no Unicode normalisation. A re-saved
    file (CRLF vs LF) is the same document; a file with one changed word is not.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def content_key(text: str) -> str:
    """The dedup identity of one item: SHA-256 of its normalised UTF-8 text.

    ⛔ **"Identical" means: identical bytes after universal-newline
    normalisation.** That is deliberately the repo's EXISTING rule, not a new
    one — ``file_indexer.hash_text`` (which writes ``:Source.contentHash``) and
    ``ids.content_hash`` (which keys ``idempotency.IngestKey``) are both
    ``sha256(text.encode("utf-8"))``, and the indexer's own read path compares
    the stored hash to the file's hash to decide ``skipped/unchanged``
    (``sdk._index_process_unit`` → ``hash_unchanged``). A batch that invented a
    third definition would re-extract documents the rest of the product
    considers unchanged, and report documents the rest of the product considers
    new as duplicates.

    The line-ending normalisation is the CALLER's obligation in
    ``file_indexer.hash_text`` ("hash_text hashes the buffer AS GIVEN"), which
    is why :func:`normalise_text` is applied here, once, at the one place this
    module derives a key.

    ⛔ **How a CHANGED file is distinguished (#13451).** A changed file has a
    different key, so it is NOT a duplicate: it is queued and re-extracted, and
    the extraction's write path supersedes/revises the ``:Source`` generation —
    which is what refreshes the vector. #13451 is the trap where a *cold re-write
    of changed text left a stale vector*; the invariant that prevents it here is
    that the key is derived from the TEXT, so "same key, different text" cannot
    occur. The dangerous shape would be keying on the item's *name* or *path*
    (a re-upload of the same filename with new content), and this function is
    what refuses it.
    """
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def stage_text(base: str, batch_id: str, item_id: str, text: str) -> str:
    """Write an uploaded item's text under ``base`` and return its path.

    ``base`` is the tenant's document storage root (``TORTOISE_INGEST_BASE_DIR``)
    — the only place the ingest doors are allowed to read from (#329), so an
    uploaded document becomes an ordinary document on disk rather than a second
    storage mechanism. Written atomically (tmp + ``os.replace``) into a
    per-batch directory, so a crash mid-accept leaves either no file or a whole
    one, and two batches can never collide.

    ⛔ Everything here is derived from ids this layer minted (``batch_…`` /
    ``batch_…-00001``), never from caller input, so no customer-supplied path
    component can reach the filesystem.
    """
    import os
    import tempfile

    directory = os.path.join(os.path.realpath(base), "_batches", batch_id)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    target = os.path.join(directory, f"{item_id}.md")
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".stage-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return target


# ── the estimate (owner requirement, #14275 §4 / brief item 6) ──────────────
@dataclass(frozen=True)
class BatchEstimate:
    """The proactive arithmetic shown at acceptance, in the customer's terms."""

    items: int
    tokens_per_item: int
    estimated_tokens: int
    #: The org's included amount on the ``capture_extraction`` dimension
    #: (``max_extraction_tokens``), or ``None`` when the tier publishes none.
    included_tokens: int | None
    tokens_already_used: int
    overage_tokens: int
    overage_usd: float
    estimated_hours: float
    #: True when this batch is too big to be presented as interactive (owner
    #: threshold, ``ON_DEMAND_MAX_ITEMS``).
    batch_processed: bool
    #: True when the customer must acknowledge the expected overage before the
    #: batch is accepted as-is.
    acknowledgement_required: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": self.items,
            "tokens_per_item": self.tokens_per_item,
            "estimated_tokens": self.estimated_tokens,
            "included_tokens": self.included_tokens,
            "tokens_already_used": self.tokens_already_used,
            "overage_tokens": self.overage_tokens,
            "overage_usd": round(self.overage_usd, 2),
            "estimated_hours": round(self.estimated_hours, 2),
            "batch_processed": self.batch_processed,
            "acknowledgement_required": self.acknowledgement_required,
        }


def estimate_batch(
    items: int,
    *,
    included_tokens: int | None = None,
    tokens_already_used: int = 0,
    tokens_per_item: int = TOKENS_PER_MEDIAN_DOCUMENT,
    seconds_per_item: float = SECONDS_PER_DOCUMENT,
    overage_usd_per_million: float = OVERAGE_USD_PER_MILLION_TOKENS,
) -> BatchEstimate:
    """``items × tokens/item``, the expected allowance impact and the overage.

    ⛔ The unit is a SPEND figure (``TOKENS_PER_MEDIAN_DOCUMENT``), and it is
    published as an **estimate**, never a promise — a 128k-token document is
    ~5× the median, and the receipt says so (:func:`customer_copy`).

    ``included_tokens is None`` means the tier publishes no included amount
    (``product/pricing.json`` currently carries none for any tier — the #14275
    decision is still open). That is reported as ``overage_tokens == 0`` and
    ``overage_usd == 0.0`` with the allowance shown as ``null`` — ⛔ NOT as
    "unlimited", and not as an invented figure. A batch is then accepted on the
    estimate alone, which is exactly what the owner asked for ("tell them
    they'll need X overage"), minus the part the pricing artifact has not
    decided yet.
    """
    if items < 0:
        raise ValueError("items must be >= 0")
    if tokens_per_item < 0 or seconds_per_item < 0:
        raise ValueError("tokens_per_item and seconds_per_item must be >= 0")
    estimated = int(items) * int(tokens_per_item)
    if included_tokens is None:
        overage_tokens = 0
        overage_usd = 0.0
    else:
        headroom = max(0, int(included_tokens) - max(0, int(tokens_already_used)))
        overage_tokens = max(0, estimated - headroom)
        overage_usd = overage_tokens / 1_000_000.0 * float(overage_usd_per_million)
    return BatchEstimate(
        items=int(items),
        tokens_per_item=int(tokens_per_item),
        estimated_tokens=estimated,
        included_tokens=None if included_tokens is None else int(included_tokens),
        tokens_already_used=max(0, int(tokens_already_used)),
        overage_tokens=overage_tokens,
        overage_usd=overage_usd,
        estimated_hours=(int(items) * float(seconds_per_item)) / 3600.0,
        batch_processed=items > ON_DEMAND_MAX_ITEMS,
        acknowledgement_required=estimated >= ACKNOWLEDGEMENT_TOKEN_THRESHOLD,
    )


#: ⛔ The customer-facing sentence (owner, #14275 §3, verbatim: *"rather this will
#: be batch processed and expect completion in X hours, with option to cancel
#: too"*). The off-peak mechanism NEVER appears here or in any error string.
ACCEPT_COPY = (
    "batch processed, completion expected in {hours} hours, cancel any time")

#: Vocabulary that would leak provider pricing mechanics into customer copy
#: (owner ruling (d)). Kept as data so :func:`customer_copy` is testable rather
#: than merely reviewed.
PROVIDER_MECHANICS_WORDS: tuple[str, ...] = (
    "off-peak", "off peak", "peak", "provider", "token rate", "cache hit",
    "cache-hit", "deepseek", "openrouter", "per million tokens", "$/m",
)


def customer_copy(hours: float) -> str:
    """The acceptance sentence, with the window rounded UP to whole hours.

    Rounding up is the honest direction: "completion expected in 2 hours" for a
    1.2-hour batch is a slightly late promise, never a broken one. A sub-hour
    batch still says one hour, because the copy must not promise minutes a
    batch worker (off-peak only, owner (c)) has not agreed to.
    """
    return ACCEPT_COPY.format(hours=max(1, math.ceil(hours)))


# ── records ────────────────────────────────────────────────────────────────
def new_batch_id() -> str:
    """``batch_`` + 12 hex, the same spelling family as ``session_``.

    One spelling, minted in one place (the ``#5416`` class: four spellings of
    one value is how a caller reading one cannot tell whether the others agree).
    """
    return f"batch_{uuid.uuid4().hex[:12]}"


@dataclass
class BatchItem:
    batch_id: str
    item_id: str
    #: What the customer handed us, echoed back verbatim on the status surface.
    ref: str
    #: Optional display name (a filename, a title).
    name: str = ""
    #: Where this item's text was STAGED when the caller uploaded it inline
    #: (``text`` on the request). Acceptance writes the bytes under the ingest
    #: base BEFORE the row exists, so the document is durable the moment the ACK
    #: is returned — an ACK over text that lived only in the request would be a
    #: receipt for something we did not record (§2.0). Empty for a path item.
    staged_path: str = ""
    state: BatchItemState = BatchItemState.QUEUED
    #: Present iff ``state`` is not a success state (AX: a non-success always
    #: carries a reason string, never a bare state).
    reason: str = ""
    #: SHA-256 of the normalised text, once known. Absent until the item's text
    #: has been read; a ``path`` item may be keyed by the indexer instead.
    content_key: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    attempts: int = 0
    #: True when this item's content differs from the generation already in the
    #: graph — i.e. it REPLACED a Source rather than duplicating one (#13451's
    #: change-vs-duplicate distinction, made visible on the wire).
    content_changed: bool = False
    #: The EARLIER item in this same batch with identical content, when the
    #: caller supplied the text (so the duplicate is visible at acceptance
    #: instead of after two reads). It is a HINT, not a verdict: the item is
    #: still queued, and it only settles as ``skipped-duplicate`` once that
    #: earlier item is actually INDEXED. Skipping it outright would lose the
    #: document whenever the first copy fails.
    duplicate_of: str = ""
    updated_at: str = ""

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_ITEM_STATES

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "ref": self.ref,
            "name": self.name,
            "status": str(self.state),
            "reason": self.reason,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_in + self.tokens_out,
            "attempts": self.attempts,
            "content_changed": self.content_changed,
            "duplicate_of": self.duplicate_of,
            "updated_at": self.updated_at,
        }


@dataclass
class Batch:
    batch_id: str
    org_id: str
    state: BatchState = BatchState.ACCEPTED
    created_at: str = ""
    updated_at: str = ""
    #: The estimate shown and (above the threshold) ACKNOWLEDGED at acceptance.
    estimate: dict[str, Any] = field(default_factory=dict)
    #: The corpus label the caller asked for. Persisted rather than dropped: it
    #: is an input to the pack/kind resolution, and a field accepted on the wire
    #: and then ignored is the #14263 class (echo the user's meaning).
    corpus_name: str = ""
    #: Who asked for the cancel, and when — the audit half of a billing-visible
    #: act. Empty until cancelled.
    cancelled_at: str = ""
    cancel_reason: str = ""

    def as_dict(self, items: Sequence[BatchItem] = ()) -> dict[str, Any]:
        counts = item_counts(items)
        return {
            "batch_id": self.batch_id,
            "status": str(self.state),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "estimate": dict(self.estimate),
            "corpus_name": self.corpus_name,
            "counts": counts,
            "items_total": len(items),
            "complete": self.state in TERMINAL_BATCH_STATES,
            "cancelled_at": self.cancelled_at,
            "cancel_reason": self.cancel_reason,
            "items": [i.as_dict() for i in items],
        }


def item_counts(items: Iterable[BatchItem]) -> dict[str, int]:
    """Counts per state, plus ``total``/``terminal``/``pending``.

    ``pending`` is derived, not a state: it is every item a worker may still
    pick up, which is what a progress bar and a resume decision both need.
    """
    counts: dict[str, int] = {str(s): 0 for s in BatchItemState}
    total = 0
    terminal = 0
    for it in items:
        counts[str(it.state)] = counts.get(str(it.state), 0) + 1
        total += 1
        if it.terminal:
            terminal += 1
    counts["total"] = total
    counts["terminal"] = terminal
    counts["pending"] = total - terminal
    return counts


@dataclass
class ItemOutcome:
    """What a runner reports for ONE item.

    ⛔ The runner may only report a LEGAL next state; the coordinator validates
    the edge rather than trusting it, because a runner that reports ``indexed``
    for a cancelled item would resurrect work the customer stopped.
    """

    state: BatchItemState
    reason: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    content_changed: bool = False
    #: The identity the runner derived, when it read the content itself (a
    #: ``path`` item's hash is only known after the file is read). Empty when
    #: the runner does not derive one; the layer never invents a key.
    content_key: str = ""


# ── storage: durable rows in the tenant graph ──────────────────────────────
class BatchStore(Protocol):
    """The durable record (§2.0: the record is truth — a batch must be
    re-derivable from it, so the batch lives in the tenant graph beside the
    documents, not in a process-local dict).

    Deliberately narrow and synchronous: every method is one bounded graph
    round trip, so :class:`GraphBatchStore` can be exercised against a fake
    ``query`` callable with no FalkorDB, and the coordinator can be unit-tested
    with :class:`InMemoryBatchStore`.
    """

    def put_batch(self, batch: Batch) -> None: ...

    def get_batch(self, org_id: str, batch_id: str) -> Batch | None: ...

    def list_batches(self, org_id: str, *, limit: int = 20) -> list[Batch]: ...

    def put_item(self, org_id: str, item: BatchItem) -> None: ...

    def items(self, org_id: str, batch_id: str) -> list[BatchItem]: ...

    def get_item(self, org_id: str, batch_id: str, item_id: str
                 ) -> BatchItem | None:
        """ONE item by id — the coordinator's per-item read.

        ⛔ Not `items()` filtered: a drain that re-lists the whole batch for every
        item turns a 500-item batch into ~250 000 materialised rows of work. One
        row per call is the difference between a batch that scales and one that
        is quadratic for no reason.
        """
        ...

    def pending_batches(self, org_id: str) -> list[str]:
        """Batch ids that are unfinished — the resume input.

        ⛔ **UNFINISHED IS EITHER HALF** (#14415 review round 2): a batch whose
        items are all terminal but whose batch ROW is not (the settle write timed
        out, or the process died between the last item write and the settle) is
        unfinished too, and keying the selection on item state alone made that row
        invisible to every path — including a fresh boot, because `recover()`
        reads this list. The row then sat `accepted` with every item `indexed` and
        charged, and the only call that would ever move it was `cancel()`, which
        relabelled it `cancelled`. Select on the batch row as well; `recover()`
        settles a lagging row in its all-items-terminal arm.
        """
        ...

    def batch_counts(self, org_id: str, batch_ids: Sequence[str]
                     ) -> dict[str, dict[str, int]]:
        """Per-batch item counts, for the LIST surface (#14219 review).

        A list that reports ``items_total: 0`` for a 500-item batch is a status
        surface that lies, and shipping the full item array for 20 batches is a
        payload nobody asked for. Counts only, in one query.
        """
        ...

    def find_settled_by_content_key(self, org_id: str, key: str
                                    ) -> BatchItem | None:
        """A previously INDEXED item with this exact content — the cross-batch
        dedup read. Returns ``None`` when the graph has never seen the bytes."""
        ...


class InMemoryBatchStore:
    """A ``BatchStore`` for tests (and for a single-process unit harness).

    ⛔ NOT a production fallback: a batch that lives only in this process is
    exactly the silent loss #14219 forbids, so ``hosted_api`` never builds one.
    It exists so the state machine, the dedup rule and the resume rule are
    testable WITHOUT a database, which is what makes them provable at all.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._batches: dict[tuple[str, str], Batch] = {}
        self._items: dict[tuple[str, str], dict[str, BatchItem]] = {}

    def put_batch(self, batch: Batch) -> None:
        with self._lock:
            self._batches[(batch.org_id, batch.batch_id)] = _copy_batch(batch)

    def get_batch(self, org_id: str, batch_id: str) -> Batch | None:
        with self._lock:
            b = self._batches.get((org_id, batch_id))
            return _copy_batch(b) if b else None

    def list_batches(self, org_id: str, *, limit: int = 20) -> list[Batch]:
        with self._lock:
            rows = [b for (o, _), b in self._batches.items() if o == org_id]
        rows.sort(key=lambda b: b.created_at, reverse=True)
        return [_copy_batch(b) for b in rows[:limit]]

    def put_item(self, org_id: str, item: BatchItem) -> None:
        with self._lock:
            bucket = self._items.setdefault((org_id, item.batch_id), {})
            bucket[item.item_id] = _copy_item(item)

    def items(self, org_id: str, batch_id: str) -> list[BatchItem]:
        with self._lock:
            bucket = self._items.get((org_id, batch_id), {})
            return [_copy_item(i) for i in bucket.values()]

    def get_item(self, org_id: str, batch_id: str, item_id: str
                 ) -> BatchItem | None:
        with self._lock:
            found = self._items.get((org_id, batch_id), {}).get(item_id)
            return _copy_item(found) if found else None

    def pending_batches(self, org_id: str) -> list[str]:
        with self._lock:
            out: list[str] = []
            for (org, bid), bucket in self._items.items():
                if org != org_id or bid in out:
                    continue
                if any(not i.terminal for i in bucket.values()):
                    out.append(bid)
            for (org, bid), batch in self._batches.items():
                if org == org_id and bid not in out and batch.state not in TERMINAL_BATCH_STATES:
                    out.append(bid)
            return out

    def find_settled_by_content_key(self, org_id: str, key: str
                                    ) -> BatchItem | None:
        if not key:
            return None
        with self._lock:
            for (org, _bid), bucket in self._items.items():
                if org != org_id:
                    continue
                for i in bucket.values():
                    if i.state is BatchItemState.INDEXED and i.content_key == key:
                        return _copy_item(i)
        return None

    def batch_counts(self, org_id: str, batch_ids: Sequence[str]
                     ) -> dict[str, dict[str, int]]:
        with self._lock:
            return {bid: item_counts(
                self._items.get((org_id, bid), {}).values())
                for bid in batch_ids}


def _finish_counts(raw: Mapping[str, Any]) -> dict[str, int]:
    """Complete a state->count map with the derived totals `item_counts` emits.

    ONE shape for the counters on every surface: the detail route derives them
    from rows, the list route derives them from a grouped query, and a client
    must not have to know which it is reading.
    """
    counts: dict[str, int] = {str(s_): 0 for s_ in BatchItemState}
    for state, n in raw.items():
        counts[str(state)] = int(n)
    total = sum(v for k, v in counts.items() if k not in ("total", "terminal",
                                                          "pending"))
    terminal = sum(counts[str(s_)] for s_ in TERMINAL_ITEM_STATES)
    counts["total"] = total
    counts["terminal"] = terminal
    counts["pending"] = total - terminal
    return counts


def _copy_item(i: BatchItem) -> BatchItem:
    return BatchItem(**{**i.__dict__})


def _copy_batch(b: Batch) -> Batch:
    return Batch(**{**b.__dict__, "estimate": dict(b.estimate)})


#: The node labels this layer owns. One namespace pair, spelled once: a batch is
#: an ``:IngestBatch`` and an item an ``:IngestBatchItem``, both scoped by
#: ``org_id`` (the tenant graphs are per-org, and the property makes an
#: accidental cross-tenant read visible in the query text).
BATCH_LABEL = "IngestBatch"
ITEM_LABEL = "IngestBatchItem"

#: Per-statement graph timeout for batch bookkeeping. Deliberately SHORT: a
#: bookkeeping write is not the work, and #14219 item 5 (read-path isolation) is
#: measured risk, not theory — a second instance on the shared graph produced a
#: **1,505 ms** DB probe timeout and 9/12 failed probes. A batch write that
#: cannot complete inside this bound FAILS the item (visible) instead of holding
#: a connection that the read path needs.
BATCH_QUERY_TIMEOUT_MS = 10_000

#: How many times ONE item may be CLAIMED before the drain stops re-driving it.
#: ⛔ Without a ceiling, a retry is an infinite loop: `runner_result_to_outcome`
#: maps a ``retryable`` skip to ``queued`` (deliberately — a held index lock is
#: not a failure), so an item whose lock never clears would be re-claimed every
#: retry tick, forever, spending a token each time and never telling anyone.
#: At the ceiling the item goes ``failed`` with a reason naming the attempt count
#: — visible on the status surface, terminal, and actionable (re-submit the
#: item). `recover()` keeps the attempt count across restarts for the same
#: reason: a poison item must not loop by dying repeatedly either.
BATCH_MAX_ATTEMPTS = 3


class GraphBatchStore:
    """``BatchStore`` over the tenant graph, one statement per method.

    ``query`` is injected (``(cypher, params) -> result_set``) so this class is
    testable without FalkorDB AND so the caller keeps control of the pool — the
    batch layer must never pick up the capture pool (see
    :class:`BatchWorker` for why that is the isolation bound).
    """

    def __init__(self, query: Callable[..., Any],
                 *, timeout_ms: int = BATCH_QUERY_TIMEOUT_MS) -> None:
        self._query = query
        self._timeout_ms = timeout_ms

    def _q(self, cypher: str, params: dict) -> Any:
        try:
            return self._query(cypher, params=params,
                               timeout=self._timeout_ms).result_set
        except TypeError:  # a fake with the narrower signature
            return self._query(cypher, params=params).result_set

    def put_batch(self, batch: Batch) -> None:
        self._q(
            f"MERGE (b:{BATCH_LABEL} {{id:$bid, org_id:$org}}) "
            "SET b.state=$state, b.created_at=$created, b.updated_at=$updated, "
            "    b.estimate_json=$estimate, b.cancelled_at=$cancelled_at, "
            "    b.cancel_reason=$cancel_reason, b.corpus_name=$corpus",
            {"bid": batch.batch_id, "org": batch.org_id,
             "state": str(batch.state), "created": batch.created_at,
             "updated": batch.updated_at,
             "estimate": _json_dumps(batch.estimate),
             "cancelled_at": batch.cancelled_at,
             "cancel_reason": batch.cancel_reason,
             "corpus": batch.corpus_name},
        )

    def get_batch(self, org_id: str, batch_id: str) -> Batch | None:
        rows = self._q(
            f"MATCH (b:{BATCH_LABEL} {{id:$bid, org_id:$org}}) "
            "RETURN b.state, b.created_at, b.updated_at, b.estimate_json, "
            "       b.cancelled_at, b.cancel_reason, b.corpus_name",
            {"bid": batch_id, "org": org_id},
        )
        if not rows:
            return None
        state, created, updated, est, cancelled_at, cancel_reason, corpus = rows[0][:7]
        return Batch(
            batch_id=batch_id, org_id=org_id,
            state=_as_state(BatchState, state, BatchState.ACCEPTED),
            created_at=created or "", updated_at=updated or "",
            estimate=_json_loads(est), cancelled_at=cancelled_at or "",
            cancel_reason=cancel_reason or "", corpus_name=corpus or "")

    def list_batches(self, org_id: str, *, limit: int = 20) -> list[Batch]:
        rows = self._q(
            f"MATCH (b:{BATCH_LABEL} {{org_id:$org}}) "
            "RETURN b.id, b.state, b.created_at, b.updated_at, "
            "       b.estimate_json, b.cancelled_at, b.cancel_reason, "
            "       b.corpus_name "
            "ORDER BY b.created_at DESC LIMIT $lim",
            {"org": org_id, "lim": int(limit)},
        )
        out = []
        for r in rows:
            out.append(Batch(
                batch_id=r[0], org_id=org_id,
                state=_as_state(BatchState, r[1], BatchState.ACCEPTED),
                created_at=r[2] or "", updated_at=r[3] or "",
                estimate=_json_loads(r[4]), cancelled_at=r[5] or "",
                cancel_reason=r[6] or "",
                corpus_name=(r[7] or "") if len(r) > 7 else ""))
        return out

    def put_item(self, org_id: str, item: BatchItem) -> None:
        self._q(
            f"MERGE (i:{ITEM_LABEL} {{id:$iid, batch_id:$bid, org_id:$org}}) "
            "SET i.ref=$ref, i.name=$name, i.staged_path=$staged, "
            "    i.state=$state, i.reason=$reason, "
            "    i.content_key=$key, i.tokens_in=$tin, i.tokens_out=$tout, "
            "    i.attempts=$attempts, i.content_changed=$changed, "
            "    i.updated_at=$updated",
            {"iid": item.item_id, "bid": item.batch_id, "org": org_id,
             "ref": item.ref, "name": item.name, "staged": item.staged_path,
             "state": str(item.state),
             "reason": item.reason, "key": item.content_key,
             "tin": int(item.tokens_in), "tout": int(item.tokens_out),
             "attempts": int(item.attempts),
             "changed": bool(item.content_changed),
             "updated": item.updated_at},
        )

    def items(self, org_id: str, batch_id: str) -> list[BatchItem]:
        rows = self._q(
            f"MATCH (i:{ITEM_LABEL} {{batch_id:$bid, org_id:$org}}) "
            "RETURN i.id, i.ref, i.name, i.state, i.reason, i.content_key, "
            "       i.tokens_in, i.tokens_out, i.attempts, i.content_changed, "
            "       i.updated_at, i.staged_path ORDER BY i.id",
            {"bid": batch_id, "org": org_id},
        )
        return [BatchItem(
            batch_id=batch_id, item_id=r[0], ref=r[1] or "", name=r[2] or "",
            state=_as_state(BatchItemState, r[3], BatchItemState.QUEUED),
            reason=r[4] or "", content_key=r[5] or "",
            tokens_in=int(r[6] or 0), tokens_out=int(r[7] or 0),
            attempts=int(r[8] or 0), content_changed=bool(r[9]),
            updated_at=r[10] or "",
            staged_path=(r[11] or "") if len(r) > 11 else "") for r in rows]

    def get_item(self, org_id: str, batch_id: str, item_id: str
                 ) -> BatchItem | None:
        rows = self._q(
            f"MATCH (i:{ITEM_LABEL} {{id:$iid, batch_id:$bid, org_id:$org}}) "
            "RETURN i.ref, i.name, i.state, i.reason, i.content_key, "
            "       i.tokens_in, i.tokens_out, i.attempts, i.content_changed, "
            "       i.updated_at, i.staged_path",
            {"iid": item_id, "bid": batch_id, "org": org_id},
        )
        if not rows:
            return None
        r = rows[0]
        return BatchItem(
            batch_id=batch_id, item_id=item_id, ref=r[0] or "", name=r[1] or "",
            state=_as_state(BatchItemState, r[2], BatchItemState.QUEUED),
            reason=r[3] or "", content_key=r[4] or "",
            tokens_in=int(r[5] or 0), tokens_out=int(r[6] or 0),
            attempts=int(r[7] or 0), content_changed=bool(r[8]),
            updated_at=r[9] or "",
            staged_path=(r[10] or "") if len(r) > 10 else "")

    def pending_batches(self, org_id: str) -> list[str]:
        # ⛔ UNION, not just the item arm: a batch whose ROW is non-terminal is
        # unfinished even when every item is terminal (see the Protocol's note).
        # `UNION` dedupes; a batch can appear in both arms.
        rows = self._q(
            f"MATCH (b:{BATCH_LABEL} {{org_id:$org}}) "
            "WHERE NOT b.state IN $batch_terminal "
            "RETURN DISTINCT b.id AS bid "
            "UNION "
            f"MATCH (i:{ITEM_LABEL} {{org_id:$org}}) "
            "WHERE NOT i.state IN $terminal "
            "RETURN DISTINCT i.batch_id AS bid",
            {"org": org_id, "terminal": [str(s) for s in TERMINAL_ITEM_STATES],
             "batch_terminal": [str(s) for s in TERMINAL_BATCH_STATES]},
        )
        return [r[0] for r in rows]

    def batch_counts(self, org_id: str, batch_ids: Sequence[str]
                     ) -> dict[str, dict[str, int]]:
        rows = self._q(
            f"MATCH (i:{ITEM_LABEL} {{org_id:$org}}) "
            "WHERE i.batch_id IN $bids "
            "RETURN i.batch_id, i.state, count(*)",
            {"org": org_id, "bids": list(batch_ids)},
        )
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            bucket = out.setdefault(r[0], {})
            bucket[str(r[1])] = int(r[2] or 0)
        return {bid: _finish_counts(out.get(bid, {})) for bid in batch_ids}

    def find_settled_by_content_key(self, org_id: str, key: str
                                    ) -> BatchItem | None:
        if not key:
            return None
        rows = self._q(
            f"MATCH (i:{ITEM_LABEL} {{org_id:$org, content_key:$key}}) "
            "WHERE i.state = $indexed "
            "RETURN i.id, i.batch_id, i.ref, i.name LIMIT 1",
            {"org": org_id, "key": key, "indexed": str(BatchItemState.INDEXED)},
        )
        if not rows:
            return None
        return BatchItem(batch_id=rows[0][1], item_id=rows[0][0],
                         ref=rows[0][2] or "", name=rows[0][3] or "",
                         state=BatchItemState.INDEXED, content_key=key)


def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _json_loads(raw: Any) -> dict:
    import json
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def _as_state(enum: Any, value: Any, default: Any) -> Any:
    """Map a stored string back to its enum; an unknown value is NOT a crash.

    A stored state the machine does not know is a version-skew or a corrupt row.
    Reading it as the ``default`` (``queued``/``accepted``) is the SAFE direction
    for a batch — the item is retried rather than silently treated as terminal —
    and the caller can see the mismatch in the item's ``reason`` on the next
    write. Reading it as terminal would be how work is dropped without a trace.
    """
    try:
        return enum(str(value))
    except (ValueError, TypeError):
        return default


# ── scheduling seam (owner (c): off-peak, and this is where a scheduler plugs in) ──
#: A predicate answering "may batch work run right now?". ``None`` disables the
#: seam, which is today's production state and is DECLARED, not hidden: the
#: owner decision is that batch processing is off-peak only, and that decision
#: needs a scheduler this layer does not implement (see the PR's residual).
BatchGate = Callable[[], bool]

_WINDOW_LOCK = threading.Lock()
_WINDOW_DEFERRED = 0


def defer_until_off_peak(gate: BatchGate | None) -> bool:
    """True when the gate said "not now" — the item stays ``queued``.

    The counter (:func:`deferred_count`) exists so "we are deferring" is
    observable: a gate that silently refuses every hour is indistinguishable
    from a worker that is not running, which is the inert-enforcer shape.
    """
    global _WINDOW_DEFERRED
    if gate is None:
        return False
    try:
        allowed = bool(gate())
    except Exception:
        # A gate that raises must not crash the worker NOR spend money: treat it
        # as "not now" and count it. Fail-closed in the direction that costs
        # nothing and loses nothing — the item stays queued and is retried.
        allowed = False
    if allowed:
        return False
    with _WINDOW_LOCK:
        _WINDOW_DEFERRED += 1
    return True


def deferred_count() -> int:
    with _WINDOW_LOCK:
        return _WINDOW_DEFERRED


# ── the worker's isolation bound (brief item 5) ────────────────────────────
#: How many batch items this process will work at once.
#:
#: ⛔ **THE ISOLATION BOUND IS A SEPARATE POOL, NOT A SMALLER SHARE OF THE
#: CAPTURE ONE.** Batch items run on their OWN executor
#: (:data:`BATCH_EXECUTOR`), so a saturated batch can never take a capture slot,
#: and the capture path's ``_CAPTURE_MAX_IN_FLIGHT`` (8, capped at 16) stays
#: entirely available to captures. The default is **2**: with the capture pool at
#: 4 workers and the read path's own pool beside it, two concurrent batch items
#: is the number that leaves the read path's probe budget alone on the shape
#: #14219 measured (a second instance on the shared graph produced a 1,505 ms DB
#: probe timeout and 9/12 failed probes — the failure mode a ceiling exists to
#: avoid, and the reason the number is a bound rather than "as many as we can").
#:
#: The bound is enforced TWICE, deliberately: a semaphore in the coordinator
#: (never more than N items submitted) and ``max_workers`` on the pool (nothing
#: else can widen it at runtime). A single enforcement point that a future
#: caller can bypass is how a ceiling becomes advisory.
BATCH_ITEMS_IN_FLIGHT = 2


class BatchWorker:
    """The batch work queue: a dedicated pool + the off-peak seam.

    ⛔ It cannot borrow the capture pool. That is the whole point of the class:
    ``_CAPTURE_EXECUTOR`` is shared with the hosted capture lane, so a batch
    item submitted there would queue behind (and ahead of) customer captures and
    the isolation claim would be prose. The pool is created here, passed in, or
    injected — never defaulted to the capture executor.
    """

    def __init__(self, executor: ThreadPoolExecutor, *,
                 ceiling: int = BATCH_ITEMS_IN_FLIGHT,
                 gate: BatchGate | None = None) -> None:
        self._executor = executor
        self._ceiling = max(1, int(ceiling))
        self._gate = gate
        self._slots = threading.Semaphore(self._ceiling)
        self._lock = threading.Lock()
        self._submitted = 0
        self._completed = 0
        self._failed = 0

    @property
    def ceiling(self) -> int:
        return self._ceiling

    @property
    def executor(self) -> ThreadPoolExecutor:
        return self._executor

    def submit(self, fn: Callable[..., Any], *args: Any,
               **kwargs: Any) -> bool:
        """Run ``fn`` if a slot and the gate allow it. Returns False when not.

        A refused submit is NOT an error: the item stays ``queued`` and the
        caller retries later. That is the backpressure shape #14219 asks for
        (the capture path's ``capture_capacity_saturated`` is the precedent —
        but there the refusal is a 429 to a client, and here it is a deferral
        of our own worker, so no request is refused).
        """
        if defer_until_off_peak(self._gate):
            return False
        if not self._slots.acquire(blocking=False):
            return False

        def _wrapped() -> None:
            try:
                fn(*args, **kwargs)
                with self._lock:
                    self._completed += 1
            except BaseException:
                with self._lock:
                    self._failed += 1
                raise
            finally:
                self._slots.release()

        try:
            self._executor.submit(_wrapped)
        except Exception:
            # A shut-down or broken pool: release the slot we took and report a
            # deferral. Swallowed deliberately — the item is durable, so the
            # next tick retries it, and a dispatch failure must not look like an
            # item failure (which would be a false terminal state).
            self._slots.release()
            return False
        with self._lock:
            self._submitted += 1
        return True

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "ceiling": self._ceiling,
                "submitted": self._submitted,
                "completed": self._completed,
                "failed": self._failed,
                "deferred": deferred_count(),
                "slots_free": getattr(self._slots, "_value", 0),
            }


# ── the runner seam ────────────────────────────────────────────────────────
class ItemRunner(Protocol):
    """Does the work for ONE item. Production: the per-file indexer unit.

    The runner is called only for items the coordinator has already settled as
    non-duplicates and admitted. For an item whose text the caller supplied that
    is a structural guarantee (the key is known at acceptance); for a PATH item
    the verdict needs the file's bytes, so it is the indexer's own hash gate that
    decides — before the LLM passes — and this layer reports the verdict
    faithfully. It must not raise for an ordinary per-item failure — that is
    ``ItemOutcome(FAILED, reason=...)``, an item outcome (AX). A raise is
    treated as a failed item by :meth:`BatchCoordinator.run_item`, with the
    exception type in the reason, because an escaped exception must never leave
    an item non-terminal.
    """

    def __call__(self, item: BatchItem) -> ItemOutcome: ...


# ── the coordinator: the state machine over the store ──────────────────────
class BatchCoordinator:
    """Accept, advance, cancel and resume — the only writer of item states.

    Everything that mutates a state goes through :meth:`advance`, so
    :data:`ITEM_TRANSITIONS` is enforced in exactly one place. The store is the
    record; this class holds no authoritative state of its own.
    """

    def __init__(self, store: BatchStore, runner: ItemRunner | None = None,
                 *, now: Callable[[], str] | None = None) -> None:
        self._store = store
        self._runner = runner
        self._now = now or _utc_now

    # -- acceptance ------------------------------------------------------
    def submit(self, org_id: str, refs: Sequence[str], *,
               names: Sequence[str] | None = None,
               contents: Sequence[str | None] | None = None,
               stage_base: str | None = None,
               corpus_name: str = "",
               estimate: dict[str, Any] | None = None,
               batch_id: str | None = None) -> tuple[Batch, list[BatchItem]]:
        """Write ONE durable batch row and one durable row per item, then return.

        ⛔ This is the ACK (decision (a)). It performs no extraction, waits for
        nothing, and returns in the time it takes to write the rows — so a batch
        of 500 is acknowledged inside the SAME wait budget a single capture is,
        and the 10 s transport bound is never approached, let alone raised.

        ⛔ The items are written BEFORE the batch is marked ``accepted``: a batch
        row that said ``accepted`` over items that did not land would be a
        receipt for work we did not record (§2.0 — the record is truth).
        """
        bid = batch_id or new_batch_id()
        stamp = self._now()
        items = []
        first_by_key: dict[str, str] = {}
        for idx, ref in enumerate(refs):
            name = (names[idx] if names and idx < len(names) else "") or ""
            text = contents[idx] if contents and idx < len(contents) else None
            key = content_key(text) if text is not None else ""
            staged = ""
            if text is not None:
                # ⛔ The bytes are written BEFORE the row (the order is the
                # promise): an ACK must never name a document we cannot read back.
                if stage_base is None:
                    raise ValueError(
                        "this item carries inline text but the server has no "
                        "document storage configured (TORTOISE_INGEST_BASE_DIR) "
                        "— send it as a path under that base instead")
                staged = stage_text(stage_base, bid, f"{bid}-{idx:05d}", text)
            item = BatchItem(batch_id=bid, item_id=f"{bid}-{idx:05d}",
                             ref=str(ref), name=str(name),
                             state=BatchItemState.QUEUED, updated_at=stamp,
                             staged_path=staged, content_key=key,
                             duplicate_of=first_by_key.get(key, "") if key else "")
            if key and key not in first_by_key:
                first_by_key[key] = item.item_id
            # An in-batch repeat is MARKED here (free — the text was supplied)
            # but settled in `run_item`, once the first copy is known to have
            # landed: skipping it now would lose the document if that copy
            # fails. Cross-batch dedup needs the text too, and happens in the
            # same place for the same reason.
            self._store.put_item(org_id, item)
            items.append(item)
        batch = Batch(batch_id=bid, org_id=org_id, state=BatchState.ACCEPTED,
                      created_at=stamp, updated_at=stamp,
                      corpus_name=str(corpus_name or ""),
                      estimate=dict(estimate or {}))
        self._store.put_batch(batch)
        return batch, items

    # -- reads -----------------------------------------------------------
    def status(self, org_id: str, batch_id: str) -> tuple[Batch, list[BatchItem]]:
        batch = self._store.get_batch(org_id, batch_id)
        if batch is None:
            raise KeyError(batch_id)
        return batch, self._store.items(org_id, batch_id)

    # -- the single mutation point --------------------------------------
    def advance(self, org_id: str, item: BatchItem, new_state: BatchItemState, *,
                reason: str = "", tokens_in: int | None = None,
                tokens_out: int | None = None,
                content_key: str | None = None,
                content_changed: bool | None = None,
                attempt: bool = False) -> BatchItem:
        """Move one item, validating the edge. Raises :class:`IllegalTransition`.

        Idempotence: a no-op transition (same state, same facts) is accepted and
        writes nothing — a retried write after an ambiguous failure must not be
        an error, because the alternative is a batch that wedges on a duplicate
        write it cannot distinguish from a conflict.
        """
        if new_state is not item.state:
            allowed = ITEM_TRANSITIONS.get(item.state, frozenset())
            if new_state not in allowed:
                raise IllegalTransition(
                    f"item {item.item_id}: {item.state} -> {new_state} is not "
                    f"a legal transition (allowed: "
                    f"{sorted(str(s) for s in allowed) or 'none — terminal'})")
        elif not (reason or tokens_in is not None or tokens_out is not None
                  or content_key is not None or content_changed is not None
                  or attempt):
            # A pure no-op: same state, no new facts. Writes NOTHING, so a
            # retried write after an ambiguous failure leaves the row (and its
            # `updated_at`) exactly as it was — the alternative treats a
            # duplicate write it cannot distinguish from a conflict as an error.
            return item
        item.state = new_state
        if reason or new_state in TERMINAL_ITEM_STATES:
            item.reason = reason
        if tokens_in is not None:
            item.tokens_in = max(0, int(tokens_in))
        if tokens_out is not None:
            item.tokens_out = max(0, int(tokens_out))
        if content_key is not None:
            item.content_key = content_key
        if content_changed is not None:
            item.content_changed = bool(content_changed)
        if attempt:
            item.attempts += 1
        item.updated_at = self._now()
        self._store.put_item(org_id, item)
        return item

    # -- one item, end to end -------------------------------------------
    def run_item(self, org_id: str, item: BatchItem,
                 runner: ItemRunner | None = None) -> BatchItem:
        """Advance one item through the runner, honouring the state machine.

        ⛔ **THE DEDUP CHECK COMES BEFORE THE RUNNER** (decision (f)). A settled
        duplicate is reported ``skipped-duplicate`` and the runner is never
        called, so it cannot spend a token — the property is structural, not a
        rule the runner is asked to remember.
        """
        if item.terminal:
            return item
        fresh = self._fresh(org_id, item)
        if fresh.state is BatchItemState.CANCELLED:
            return fresh
        if fresh.duplicate_of:
            twin = self._store.get_item(org_id, fresh.batch_id, fresh.duplicate_of)
            if twin is not None and twin.state is BatchItemState.INDEXED:
                return self.advance(
                    org_id, fresh, BatchItemState.SKIPPED_DUPLICATE,
                    reason=("identical content already indexed by item "
                            f"{twin.item_id} in this batch — no work done, no "
                            "tokens spent"))
            # The twin failed or is still running: this copy is NOT a duplicate
            # of anything that exists, so it proceeds. Two different paths to
            # the same bytes are then deduped by the indexer's own url+hash
            # gate, which is the write-side authority.
        if fresh.content_key:
            prior = self._store.find_settled_by_content_key(org_id,
                                                            fresh.content_key)
            if prior is not None and prior.item_id != fresh.item_id:
                return self.advance(
                    org_id, fresh, BatchItemState.SKIPPED_DUPLICATE,
                    reason=("identical content already indexed by item "
                            f"{prior.item_id} — no work done, no tokens spent"))
        active = runner or self._runner
        if active is None:
            raise RuntimeError("no item runner configured")
        # ⛔ Only a QUEUED item is CLAIMED here. An item already `embedded` was
        # requeued by a runner that reports the staged pipeline (…→EXTRACTING→
        # EMBEDDED→INDEXED) and then resumed: `extracting` is not a legal edge
        # from `embedded`, so re-claiming it would raise IllegalTransition and
        # abort the whole drain (found in review; the current production runner
        # never reports `embedded`, which is the only reason it was invisible).
        if fresh.state is BatchItemState.QUEUED:
            if fresh.attempts >= BATCH_MAX_ATTEMPTS:
                # ⛔ THE RETRY CEILING (#14415 review B2). A ``retryable`` skip is
                # mapped to ``queued`` on purpose, but an item whose condition
                # never clears would be re-claimed on every retry tick for ever —
                # spending a token per pass and reporting ``pending`` with no
                # error, which is the inert row this layer exists to prevent. At
                # the ceiling the item settles ``failed`` with the attempt count
                # in its reason: terminal, visible, and actionable.
                return self.advance(
                    org_id, fresh, BatchItemState.FAILED,
                    reason=(f"gave up after {fresh.attempts} attempt(s), each of "
                            "which ended without settling the item (a "
                            "retryable skip, or a process that stopped "
                            "mid-item); re-submit this item to try again"))
            self.advance(org_id, fresh, BatchItemState.EXTRACTING, attempt=True)
        elif fresh.state in (BatchItemState.EXTRACTING,
                             BatchItemState.EMBEDDED):
            # ⛔ A RE-ENTERED CLAIM IS AN ATTEMPT TOO (#14415 review round 3,
            # BLOCKING 3). The ceiling above only ran for a `queued` item, so an
            # item re-driven from a DEAD claim (`extracting`/`embedded` — its
            # runner died, or its own terminal write kept failing) never
            # incremented `attempts` and never reached the bound: every retry
            # tick called the runner again, spending real tokens that the failed
            # advance never recorded, for as long as the bookkeeping write kept
            # failing. Count it here, and let the same ceiling bound it.
            if fresh.attempts >= BATCH_MAX_ATTEMPTS:
                return self.advance(
                    org_id, fresh, BatchItemState.FAILED,
                    reason=(f"gave up after {fresh.attempts} attempt(s) on an "
                            "unsettled claim — its own bookkeeping write never "
                            "landed; re-submit this item to try again"))
            self.advance(org_id, fresh, fresh.state,
                         reason="re-claimed by a retry pass", attempt=True)
        try:
            outcome = active(fresh)
        except Exception as exc:  # an escaped exception is a FAILED ITEM
            return self.advance(
                org_id, fresh, BatchItemState.FAILED,
                reason=f"{type(exc).__name__}: {exc}"[:500])
        # A cancel that landed while the runner was working wins: the item is
        # already terminal and must not be resurrected (the customer stopped it).
        latest = self._fresh(org_id, fresh)
        if latest.state is BatchItemState.CANCELLED:
            return latest
        try:
            return self.advance(
                org_id, latest, outcome.state, reason=outcome.reason,
                tokens_in=outcome.tokens_in, tokens_out=outcome.tokens_out,
                content_changed=outcome.content_changed,
                content_key=outcome.content_key or None)
        except IllegalTransition:
            # Another writer settled this item while the runner was working (a
            # cancel, or the same item drained twice across processes). The
            # item's row is authoritative — report it rather than crash the
            # drain, which is what would lose every LATER item.
            return self._fresh(org_id, fresh)

    def _fresh(self, org_id: str, item: BatchItem) -> BatchItem:
        stored = self._store.get_item(org_id, item.batch_id, item.item_id)
        return stored if stored is not None else item

    # -- cancel (owner requirement, brief item 6) ------------------------
    def cancel(self, org_id: str, batch_id: str, *, reason: str = ""
               ) -> tuple[Batch, list[BatchItem]]:
        """Stop the batch: every item that has not STARTED becomes ``cancelled``.

        ⛔ **The semantics, stated because they decide a bill** (the brief's
        "work already done not charged"): an item is charged iff a runner
        actually ran for it. So:

        * ``queued`` (and the resume-reset ``extracting``-from-a-dead-process,
          which has not been claimed by any live runner) → ``cancelled``,
          **zero tokens**, nothing charged;
        * an item being worked RIGHT NOW keeps going to its terminal state — it
          has already spent the tokens, and killing the model call mid-flight
          would spend them *and* throw away the result. It is charged, and the
          receipt says so, because hiding a real spend is the worse defect;
        * an item already terminal is untouched.

        The batch itself becomes ``cancelled`` **when there is anything left to
        stop**; over a batch whose items have all reached a terminal state it is
        reported as it is (and, if that state was `cancelled` on every item, the
        batch is DERIVED `cancelled` from the rows — see :meth:`settle`). The
        status body carries the spend half on the summary as ``tokens_spent``
        (the sum over the items; there is no separate
        ``tokens_spent_before_cancel`` field — that name appeared in an earlier
        revision of this docstring and never existed on the wire, #14415 review
        round 3 NB5), so "cancel any time" (the copy, decision (d)) is a
        statement about the FUTURE work, which is the only thing we can honestly
        stop.

        ⛔ A batch already in a TERMINAL state is returned UNCHANGED: cancelling
        cannot un-spend tokens, and relabelling a settled batch would make this
        surface disagree with its own items.
        """
        batch = self._store.get_batch(org_id, batch_id)
        if batch is None:
            raise KeyError(batch_id)
        items = self._store.items(org_id, batch_id)
        if batch.state in TERMINAL_BATCH_STATES:
            # ⛔ A SETTLED BATCH IS NOT REWRITTEN (#14415 review B3). Cancel used
            # to fall through unconditionally, so a cancel arriving after the
            # last item landed relabelled a fully successful ``completed`` batch
            # as ``cancelled`` and INVENTED a ``cancelled_at``/``cancel_reason``
            # for it — while every item stayed ``indexed`` and charged. That is a
            # status that lies on a billing-visible surface: the customer reads
            # "work stopped, not charged" over a batch they were charged for.
            # The record is the truth, so it is reported unchanged (and a repeat
            # cancel is idempotent rather than a rewrite).
            return batch, items
        if items and not any(not item.terminal for item in items):
            # ⛔ THE GUARD IS ON REMAINING WORK, NOT ON THE BATCH ROW (#14415
            # review round 2, B2). The row can LAG its items — `settle()`'s write
            # timing out, or a process killed between the last item write and the
            # settle — and then the row reads `accepted` over items that are all
            # `indexed` and charged. Keying only on ``batch.state`` relabelled
            # exactly that record `cancelled`: B3's wrong outcome, reached without
            # any injected fault. There is nothing left to stop, so DERIVE the
            # truth instead of inventing a cancel over it.
            return self.settle(org_id, batch_id), items
        stamp = self._now()
        for item in items:
            if item.state is BatchItemState.QUEUED:
                self.advance(org_id, item, BatchItemState.CANCELLED,
                             reason=reason or "cancelled by the customer")
            elif item.state in (BatchItemState.EXTRACTING,
                                BatchItemState.EMBEDDED) and item.attempts == 0:
                # Claimed by nobody: the resume edge left it here, no runner ran.
                self.advance(org_id, item, BatchItemState.QUEUED)
                self.advance(org_id, item, BatchItemState.CANCELLED,
                             reason=reason or "cancelled by the customer")
        batch.state = BatchState.CANCELLED
        batch.updated_at = stamp
        batch.cancelled_at = stamp
        batch.cancel_reason = reason or "cancelled by the customer"
        self._store.put_batch(batch)
        return batch, self._store.items(org_id, batch_id)

    # -- resume (brief item 4) -------------------------------------------
    def abandon_dead_claims(self, org_id: str, batch_id: str) -> None:
        """Settle a CANCELLED batch's claims that NO runner is holding.

        ⛔ NOTHING MAY RUN AFTER A CANCEL (#14415 review round 3, BLOCKING 2).
        A cancelled batch can still carry `extracting`/`embedded` rows whose
        runner is gone — it died, or its own terminal write kept failing and the
        customer cancelled a batch that looked stuck. `_drain_batch` refuses to
        work a cancelled batch; without this, those rows would either sit
        non-terminal for ever or (before that refusal) be re-entered by the
        retry tick, which started a NEW runner call after the cancel the
        customer was told would not be charged — measured: three runner calls,
        3,000 tokens spent, row `cancelled`, two items `indexed`.

        The claim is void, so it settles `cancelled` with no token counts
        invented for a run nothing can observe — the same rule the boot path has
        always applied, now in ONE place both callers use.
        """
        for item in self._store.items(org_id, batch_id):
            if item.state in (BatchItemState.EXTRACTING,
                              BatchItemState.EMBEDDED):
                self.advance(org_id, item, BatchItemState.QUEUED,
                             reason="cancelled batch, claim released")
                self.advance(
                    org_id, item, BatchItemState.CANCELLED,
                    reason="batch was cancelled before this item completed")
            elif item.state is BatchItemState.QUEUED:
                self.advance(org_id, item, BatchItemState.CANCELLED,
                             reason="batch was cancelled")

    def recover(self, org_id: str, *, live_batches: Collection[str] | Callable[[str], bool] = ()
                ) -> list[str]:
        """Re-open every batch with unfinished items. Returns the batch ids.

        ⛔ **What "recoverable" has to mean.** An item found ``extracting`` (or
        ``embedded``) at boot was claimed by a process that no longer exists:
        its claim is not evidence the work finished, and it is not evidence the
        work was lost either. So it goes back to ``queued`` — the resume edge —
        *keeping* its attempt count so a poison item cannot loop forever, and the
        batch is marked ``resuming`` so the status surface says so (the brief:
        "the batch's own status says what it is resuming"). Nothing is dropped:
        the row survives the process, which is the whole requirement.

        ⛔ **``live_batches`` is what makes that premise CHECKABLE instead of
        assumed** (#14415 review B1). "On boot" is implemented by the caller as
        "the first time this process touches the tenant's batch surface", and
        the first touch in the documented flow is the customer's STATUS POLL —
        which lands while item 1 is inside the runner call. The row is plainly
        ``extracting``, this method requeued it, and when the live runner
        returned its outcome could no longer be applied (``queued`` ->
        ``indexed`` is not a legal edge), so a completed index was DISCARDED:
        tokens spent, the document in the graph, and the row reporting
        ``pending`` with zero tokens for ever. So the caller names the batches a
        drain in THIS process is working right now, and those claims are not
        void — they belong to a process that is still alive. They are skipped
        here and left to the live drain (whose leftovers are re-driven by
        ``hosted_api._drain_batch``).

        ⛔ **It is asked PER BATCH, at the moment of the decislon, never as a
        snapshot** (#14415 review round 2, NB4). A set captured before this
        method's own graph reads left a window in which a drain could claim a
        batch *after* the snapshot and still have that claim requeued — the
        same discarded outcome, narrowed to one query's round trips. A callable
        closes it; a collection is still accepted (and wrapped) so the seam
        stays testable without a lock.
        """
        is_live: Callable[[str], bool] = (
            live_batches if callable(live_batches)
            else (lambda batch_id, _live=live_batches: batch_id in _live))
        resumed: list[str] = []
        stamp = self._now()
        for batch_id in self._store.pending_batches(org_id):
            if is_live(batch_id):
                # A drain in THIS process owns this batch's claim; see the
                # `live_batches` note above. Requeueing it would destroy the
                # outcome of work that is still running.
                continue
            batch = self._store.get_batch(org_id, batch_id)
            if batch is None:
                # ⛔ ORPHAN ROWS, not an inconsistency (#14219 review). Acceptance
                # writes the items BEFORE the batch row (so an ACK never names a
                # document we did not record); a crash between the two leaves
                # items with no batch. Skipping them would strand those rows on
                # every boot, invisible on every surface. Adopt them: the items
                # ARE the record of accepted work.
                orphan_items = self._store.items(org_id, batch_id)
                if not orphan_items:
                    continue
                batch = Batch(batch_id=batch_id, org_id=org_id,
                              state=BatchState.RESUMING,
                              created_at=orphan_items[0].updated_at,
                              updated_at=orphan_items[0].updated_at)
                self._store.put_batch(batch)
            if batch.state is BatchState.CANCELLED:
                # A cancelled batch is NOT resumed: the customer stopped it, and
                # a restart is not a reason to spend their allowance. But an item
                # left non-terminal by a crash AFTER the cancel must not sit
                # non-terminal for ever (it would be re-listed as pending on
                # every boot and never resolved — an inert row). At boot nothing
                # is running, so its claim is void: settle it ``cancelled``,
                # without inventing token counts for a run we cannot observe.
                self.abandon_dead_claims(org_id, batch_id)
                continue
            items = self._store.items(org_id, batch_id)
            moved = False
            for item in items:
                if item.state in (BatchItemState.EXTRACTING,
                                  BatchItemState.EMBEDDED):
                    self.advance(org_id, item, BatchItemState.QUEUED,
                                 reason="resumed after a process restart — "
                                        "requeued for a fresh attempt")
                    moved = True
            if moved or any(not i.terminal for i in items):
                batch.state = BatchState.RESUMING
                batch.updated_at = stamp
                self._store.put_batch(batch)
                resumed.append(batch_id)
            elif items:
                # ⛔ A LAGGING BATCH ROW IS REPAIRED HERE (#14415 review round 2,
                # B1 arm 2 / B2's root). Every item is terminal but the batch row
                # is not: `settle()`'s own write timed out, or the process died
                # between the last item write and the settle. Nothing else ever
                # re-derived it — `pending_batches()` selected on ITEM state, so
                # such a row was invisible even to a fresh boot, and
                # `cancel()` was the only call that would move it (which is how
                # a charged, fully-indexed batch came to be relabelled
                # `cancelled`). `settle()` is idempotent and derives the terminal
                # state from the rows, so this is the repair.
                self.settle(org_id, batch_id)
        return resumed

    # -- batch settlement -------------------------------------------------
    def settle(self, org_id: str, batch_id: str) -> Batch:
        """Derive the batch's terminal state from its items.

        ``completed`` only when EVERY item reached a success terminal state;
        ``cancelled`` when the customer cancelled; ``partial`` when anything
        failed or was skipped as a duplicate. ⛔ ``partial`` is the honest label
        #2952 requires — a partially-ingested batch says so — and it is derived
        from the rows, never asserted by the worker.
        """
        batch = self._store.get_batch(org_id, batch_id)
        if batch is None:
            raise KeyError(batch_id)
        items = self._store.items(org_id, batch_id)
        if batch.state is BatchState.CANCELLED:
            return batch
        if not items or any(not i.terminal for i in items):
            if batch.state is not BatchState.RESUMING:
                batch.state = BatchState.RUNNING
                batch.updated_at = self._now()
                self._store.put_batch(batch)
            return batch
        ok = sum(1 for i in items if i.state is BatchItemState.INDEXED)
        if not ok and all(i.state is BatchItemState.CANCELLED for i in items):
            # ⛔ ``cancelled`` IS DERIVED, NOT ONLY RECORDED (#14415 review round
            # 3, NB4). This docstring has always promised it, but the only writer
            # of `cancelled` was `cancel()` itself — so a cancel whose own
            # put_batch was lost left every item `cancelled` over a row that
            # derived `partial`, with an empty `cancel_reason` and the route's
            # copy saying "this batch had already finished, so nothing was
            # stopped". An all-cancelled set of rows is exactly a customer
            # cancel; derive it (keeping any reason the rows already carry).
            batch.state = BatchState.CANCELLED
            batch.cancelled_at = batch.cancelled_at or self._now()
            batch.cancel_reason = (
                batch.cancel_reason
                or next((i.reason for i in items if i.reason), ""))
            batch.updated_at = self._now()
            self._store.put_batch(batch)
            return batch
        batch.state = (BatchState.COMPLETED if ok == len(items)
                       else BatchState.PARTIAL)
        batch.updated_at = self._now()
        self._store.put_batch(batch)
        return batch


# ── receipt shape ──────────────────────────────────────────────────────────
def acceptance_receipt(batch: Batch, items: Sequence[BatchItem], *,
                       status_url: str, copy: str | None = None
                       ) -> dict[str, Any]:
    """The ACK body: what was accepted, what it will cost, how to watch it.

    ``status: "accepted"`` deliberately reuses ``write_verb.STATUS_ACCEPTED``'s
    word — a batch acceptance is the same kind of promise as a capture ack
    ("durable, async-confirmed"), and a client that already branches on
    ``memory_write_v1``'s ``accepted`` should not need a second vocabulary.
    """
    from tortoise.write_verb import MEMORY_WRITE_V1, STATUS_ACCEPTED
    est = dict(batch.estimate)
    hours = float(est.get("estimated_hours") or 0.0)
    return {
        "protocol_version": MEMORY_WRITE_V1,
        "status": STATUS_ACCEPTED,
        "batch_id": batch.batch_id,
        "status_url": status_url,
        "items_total": len(items),
        "estimate": est,
        # ⛔ decision (d): batch-processed wording, never provider mechanics.
        "message": copy or customer_copy(hours),
    }


def status_body(batch: Batch, items: Sequence[BatchItem]) -> dict[str, Any]:
    """The per-item status surface (brief item 2).

    Every non-success item carries a ``reason`` (AX): a bare ``failed`` is a
    state a customer cannot act on. The batch-level counters are derived from
    the same rows the items come from, so the summary can never disagree with
    the list.
    """
    body = batch.as_dict(items)
    body["tokens_spent"] = sum(i.tokens_in + i.tokens_out for i in items)
    return body


def runner_result_to_outcome(per_file: Mapping[str, Any]) -> ItemOutcome:
    """Map one ``_index_process_unit`` result to an :class:`ItemOutcome`.

    The indexer's per-file vocabulary is ``indexed|updated|skipped|failed`` plus
    a ``skipped_reason``; this is the ONE translation into the batch's wire
    vocabulary, so the two can never drift into two meanings of "duplicate".

    | indexer | batch item |
    |---|---|
    | ``indexed`` | ``indexed`` |
    | ``updated`` | ``indexed`` + ``content_changed`` (#13451: changed text RE-WRITES and re-embeds; it is not a duplicate) |
    | ``skipped`` + ``unchanged`` | ``skipped-duplicate`` |
    | ``skipped`` + ``lock-held`` / anything retryable | ``queued`` (retry; NOT a terminal failure) |
    | ``skipped`` + other | ``skipped-duplicate`` with the indexer's own reason |
    | ``failed`` | ``failed`` with the indexer's reason |
    """
    status = str(per_file.get("status") or "failed")
    reason = str(per_file.get("reason") or per_file.get("skipped_reason") or "")
    if status == "indexed":
        return ItemOutcome(BatchItemState.INDEXED)
    if status == "updated":
        return ItemOutcome(BatchItemState.INDEXED, content_changed=True,
                           reason="content changed — source re-written")
    if status == "skipped":
        if per_file.get("retryable"):
            return ItemOutcome(BatchItemState.QUEUED,
                               reason=reason or "retryable skip")
        if per_file.get("skipped_reason") == "unchanged":
            return ItemOutcome(
                BatchItemState.SKIPPED_DUPLICATE,
                reason="identical content already indexed — no work done, "
                       "no tokens spent")
        return ItemOutcome(BatchItemState.SKIPPED_DUPLICATE,
                           reason=reason or "skipped by the indexer")
    return ItemOutcome(BatchItemState.FAILED,
                       reason=reason or "the indexer reported a failure")


def _utc_now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def elapsed_ms(start: float) -> int:
    """Helper for the ack-latency measurement the receipt is graded on."""
    return int((time.monotonic() - start) * 1000)
