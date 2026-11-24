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

(b) ⛔ **one token budget, sessions AND documents** (owner, #14275). An item
    that spends tokens is charged to the SAME ``capture_extraction`` dimension
    (``max_extraction_tokens``, ``allowance_gate.ALLOWANCE_KEYS``) through the
    same ledger writer (``metering.record_capture_usage``), by
    :func:`record_item_spend` — never a second meter. :func:`estimate_batch`
    reads that dimension's own meter; it does not define a new one.

    ⚠️ **What is actually metered TODAY, stated plainly** (#14219 review round 2,
    BLOCKING 1): the production per-file indexer this route delegates to spends
    no model tokens — it embeds and files — so ``tokens_in``/``tokens_out`` are 0
    on every item and :func:`record_item_spend` makes no ledger call. ⛔ **And
    wiring extraction DOES need a second change** (#14415 review round 5, P3 —
    this paragraph used to claim the opposite): :func:`runner_result_to_outcome`
    maps ``status``/``reason`` only, and the per-file indexer's documented result
    dict (``tortoise/sdk.py``, ``_index_process_unit``) carries **no token keys
    at all**, so an extracting runner must both report counts and have them
    mapped here. What exists today is the METER end of that path: the charge
    happens in ``run_item`` — only for the write that item's own CAS landed, with
    that pass's deltas — so the ledger cannot be charged twice. Until extraction
    is wired, the estimate reports the *ceiling* of what a batch would cost, and
    the ledger moves only for lanes that actually spend.

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
      (a test asserts :func:`content_key` equals ``file_indexer.compute_file_hash``
      over a CRLF file — the indexer's real read path, not a re-derivation), and
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
import logging
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
#: pipeline at the production model: **48,461 input + 16,338 output = 64,799**,
#: the measured median SPLIT (`docs/evidence/14343-amplify/README.md`, #14295).
#: ⛔ The split is cited, not re-derived (#14415 review round 5, P3): this
#: comment used to present the pipeline's FITTED form (45,503 constant + 2.159T
#: input, 22,802 constant + 0.180T output, at T = 5,211) as the source of
#: 64,799, and that sum is 68,305 BEFORE any T — a derivation beside a money
#: figure that its own arithmetic refutes.
#:
#: ⛔ This is the number the owner ruled the estimate must be built from
#: ("≈64,799 tokens per median document"), and it is a SPEND figure — not the
#: document's own size (5,211), which is the trap #14295 exists to correct: a
#: small item is not "relatively expensive", it is *entirely* overhead, because
#: S2 and S4 do not scale with the document at all (R² = 0.000 / 0.009).
_logger = logging.getLogger(__name__)

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
#: text). ⛔ This constant IS the route's body cap: ``hosted_api`` registers it
#: in ``CappedBodyMiddleware``'s ``path_caps["/v1/batches"]``, and the handler has
#: no second check. Without that entry the middleware's 256 KiB default refuses
#: first and this guidance is dead code; with a second check at the same number
#: the handler's copy is unreachable and the env knob is a lie (#14219 review,
#: BLOCKING 6 then NB1). One number, one owner.
BATCH_MAX_INLINE_BYTES = 4 * 1024 * 1024
#: The refusal text names the cap it was CONFIGURED with, not the default: the
#: bound is env-overridable, and a static "(4 MiB)" told a customer with a 1 MiB
#: cap that their 2 MiB body was over 4 MiB (#14415 review round 5, P3).
BATCH_INLINE_413_DETAIL = (
    "batch request body exceeds the size cap ({cap}) — send it as several "
    "batches; nothing is dropped")


def inline_413_detail(cap_bytes: int) -> str:
    """The 413 detail for the RESOLVED cap, not the shipped default."""
    mib = cap_bytes / (1024 * 1024)
    cap = f"{mib:g} MiB" if mib >= 1 else f"{cap_bytes} bytes"
    return BATCH_INLINE_413_DETAIL.format(cap=cap)

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


def _is_single_path_component(value: str) -> bool:
    """True when ``value`` names ONE path component and nothing else.

    Used at the API boundary to refuse a caller-supplied ``batch_id`` before any
    row or byte is written. It is a CHEAP LEXICAL PREDICATE, not the safety
    proof — :func:`_batch_staging_dir` is that, and it holds even for a caller
    this check was never applied to.
    """
    import os

    if not value or "\x00" in value or len(value) > 128:
        return False
    if value in (".", ".."):
        return False
    return not any(sep and sep in value for sep in ("\n", os.sep, os.altsep, "/", "\\"))


def _batch_staging_dir(base: str, batch_id: str) -> str | None:
    """Resolve ``_batches/<batch_id>`` under ``base``, or None when it ESCAPES.

    ⛔ ``batch_id`` is NOT always an id this layer minted. ``BatchCoordinator
    .submit`` accepts a caller-supplied ``batch_id`` keyword, and it is used
    verbatim as a directory name — and ``os.path.join`` does not sanitise: a
    ``..`` component is resolved by the kernel at syscall time, so joining it
    produces a path OUTSIDE the tree while looking like a child of it.

    Measured on the pre-fix code, from the public keyword alone (no route passes
    it today, so this is a defect in a documented method signature, not a live
    remote exploit):

        stage_text(base, "../../victim", "ok-00000", "ATTACKER BYTES")
        -> <base>/_batches/../../victim/ok-00000.md      # written OUTSIDE base
        _discard_staged(base, "../../victim")
        -> victim/ emptied: every file unlinked, then the directory removed

    So the WRITE was an arbitrary-path write and the DISCARD an arbitrary-directory
    delete — unrelated tenant data, from one keyword. Containment is decided by
    :func:`tortoise.security.resolve_under_base` (realpath on BOTH sides, so a
    symlink pointing out of the tree is refused too), never by a lexical check,
    which a ``..`` component or a parent symlink defeats.

    Returning ``None`` — rather than raising — lets :func:`_discard_staged` keep
    its "best-effort, never raises" contract while still refusing to act.
    """
    import os

    from .security import resolve_under_base

    batches_root = os.path.join(os.path.realpath(base), "_batches")
    resolved = resolve_under_base(os.path.join(batches_root, batch_id), batches_root)
    return str(resolved) if resolved is not None else None


def stage_text(base: str, batch_id: str, item_id: str, text: str) -> str:
    """Write an uploaded item's text under ``base`` and return its path.

    ``base`` is the tenant's document storage root (``TORTOISE_INGEST_BASE_DIR``)
    — the only place the ingest doors are allowed to read from (#329), so an
    uploaded document becomes an ordinary document on disk rather than a second
    storage mechanism. Written atomically (tmp + ``os.replace``) into a
    per-batch directory, so a crash mid-accept leaves either no file or a whole
    one, and two batches can never collide.

    ⛔ Both path components are PROVEN to resolve inside ``base`` before anything
    is created, and the write uses the resolved path — see
    :func:`_batch_staging_dir` for the measurement this replaces. The previous
    wording here claimed "no customer-supplied path component can reach the
    filesystem"; that was FALSE for ``batch_id``, which is a documented keyword
    of :meth:`BatchCoordinator.submit`, and a docstring that asserts a guarantee
    the code does not provide is worse than no claim at all.
    """
    import os
    import tempfile

    from .security import resolve_under_base

    directory = _batch_staging_dir(base, batch_id)
    if directory is None:
        raise ValueError(
            f"batch id {batch_id!r} does not resolve inside the document storage "
            "root — refusing to stage outside it")
    target_path = resolve_under_base(
        os.path.join(directory, f"{item_id}.md"), directory)
    if target_path is None:
        raise ValueError(
            f"item id {item_id!r} does not resolve inside batch {batch_id!r} — "
            "refusing to stage outside it")
    target = str(target_path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
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
    published as an **estimate**, never a promise: a 128k-token document is ~5×
    the median, so a batch of long documents will exceed it. The receipt returns
    the arithmetic (``items``, ``tokens_per_item``, ``estimated_tokens``) so the
    multiple is visible, and the completion window is published in HOURS, not as
    a deadline — :func:`customer_copy` deliberately says nothing about per-item
    variance, because a customer-facing hedge ("unless your documents are long")
    reads as an excuse rather than information.

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
#: ⛔ `{unit}` IS A WHOLE UNIT, NOT A SUFFIX (#14415 review round 5, P3): the
#: template said `{hours} hours`, so the single-document batch a customer sends
#: returned *"completion expected in **1 hours**"* — and a test pinned that
#: ungrammatical string, so fixing the copy alone would have gone red.
ACCEPT_COPY = (
    "batch processed, completion expected in {hours} {unit}, cancel any time")

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
    whole = max(1, math.ceil(hours))
    return ACCEPT_COPY.format(hours=whole,
                              unit="hour" if whole == 1 else "hours")


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
    #: ⛔ THE FIGURES THIS PROCESS JUST LANDED, and therefore the ONLY ones that
    #: may be charged — never ``tokens_in/out``, which are the ROW's totals and
    #: may describe a runner another process ran, or a pass that was already
    #: charged (#14415 review round 4, BLOCKING 4). Non-persisted on purpose: it
    #: is a fact about one `run_item` call, not about the document, so it cannot
    #: be re-read and charged a second time. `as_dict` never emits them.
    charge_in: int = field(default=0, compare=False, repr=False)
    charge_out: int = field(default=0, compare=False, repr=False)
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


#: The batch id a caller supplied (or a concurrent writer minted first) is already
#: taken. It subclasses ``ValueError`` so every existing caller that catches the
#: documented refusal keeps working, and it is its OWN type because ``submit``'s
#: compensating rollback must NOT run for it: that rollback discards the batch id,
#: and here that id belongs to a batch this call did not create.
class _BatchIdConflict(ValueError):
    """The batch id is already taken; nothing this call wrote should be removed."""


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

    def put_batch_if_absent(self, batch: Batch) -> bool:
        """Create the row ONLY if the key is absent; returns whether it wrote.

        ⛔ AN ADOPTION IS A CREATE, NOT A WRITE (#14415 review round 11, P2).
        `_resume_batch` adopts orphan ITEM rows (a submit that wrote items and
        died before its batch row), and it built the batch row from that snapshot
        and wrote it with a plain `put_batch` — a `MERGE … SET` of state,
        created_at, updated_at, estimate_json, cancelled_at, cancel_reason and
        corpus_name. The re-read above narrows that window to two statements; it
        does not close it, so an acceptance landing in between had its estimate
        and corpus_name BLANKED (owner §4's arithmetic gone from the record) and
        the row relabelled `resuming` over work that never stopped. Every other
        writer of this row is a compare-and-set; a create has no state to compare,
        so it compares ABSENCE instead — the ACCEPTANCE create included (review
        round 17: it was the last blind overwrite on this row, and a create over an
        existing id reset a cancelled batch's items and re-drained them).
        """
        ...

    def put_batch_if_state(self, batch: Batch,
                           expected: Collection[BatchState]) -> bool:
        """Write ``batch`` only while the stored row is still in ``expected``.

        ⛔ THE ITEM ROW HAS HAD A COMPARE-AND-SET SINCE ROUND 4; THE BATCH ROW
        HAD NONE, and that asymmetry lost a CANCELLATION PERMANENTLY (#14415
        review round 5, P2). `settle()` reads the batch, derives a state from the
        items, and writes — so a `cancel()` landing in that window was
        overwritten by `partial`, and `partial` is TERMINAL: `pending_batches()`
        no longer selected it, so nothing ever repaired it. The customer's
        `cancelled_at`/`cancel_reason` were erased on a billing-visible surface
        with no trace. ``expected`` is a COLLECTION because a settle may legally
        land from any non-terminal state.
        """
        ...

    def get_batch(self, org_id: str, batch_id: str) -> Batch | None: ...

    def list_recent_batches(self, org_id: str, *, limit: int = 20
                            ) -> list[Batch]:
        """The org's most recent batches, newest first.

        ⛔ NOT called `list_batches`: that is a RETIRED public SDK name
        (`#11609` — `tortoise/sdk_retired.py` maps it to `list_knowledge`, and
        `archive/sdk-methods/12901-sdk-final/list_batches.py` holds the body).
        Reusing the spelling on a brand-new store object makes
        `store.list_batches(...)` read as the retired surface coming back, and
        `tools/sdk_privatise.py` cannot type the receiver, so it reports the
        call as residue it deliberately leaves alone. The REST handler keeps
        the route's own name; only this store method avoids it.
        """
        ...

    def put_item(self, org_id: str, item: BatchItem) -> None: ...

    def discard(self, org_id: str, batch_id: str) -> None:
        """Remove a batch's item rows (the rollback of a rejected acceptance).

        ⛔ DECLARED, because `submit`'s ROLLBACK calls it (#14415 review round 15,
        P3). It existed only on the two concrete stores, so a store written to this
        Protocol raised `AttributeError` inside the `except Exception` that logs
        "could not be rolled back" — and the item rows then SURVIVED, which is
        BLOCKING-2 exactly: a 503 saying "nothing was accepted; retry" over rows the
        resume scan adopts and drains, and then the retry ingests them again. A
        narrow contract is only narrow if every call on it is in here.
        """
        ...

    def put_items(self, org_id: str, items: Sequence[BatchItem]) -> None:
        """Create/refresh MANY item rows in ONE statement.

        ⛔ ACCEPTANCE WAS ONE ROUND TRIP PER ITEM (#14415 review round 13, P2).
        `_submit_rows` called `put_item` in its loop, so the documented maximum
        (1 000 items) meant ~1 001 sequential statements inside ONE request on the
        two-worker bookkeeping pool — the same cost the set-based cancel was
        introduced to remove at half the count, and this file's own measured
        contention (a 1 505 ms DB probe) makes it exceed the transport budget. The
        customer then gets a 504 over an acceptance that COMMITTED, and the route
        mints its own `batch_id` with no idempotency key, so the retry creates a
        SECOND batch and every document is ingested and charged twice.
        """
        ...

    def add_item_tokens(self, org_id: str, batch_id: str, item_id: str, *,
                        tokens_in: int = 0, tokens_out: int = 0,
                        updated_at: str = "") -> BatchItem | None:
        """ADD to a row's token totals WHERE THE ROW LIVES; returns the row.

        ⛔ ADDITIVE, NOT READ-MODIFY-WRITE (#14415 review round 5, P3). A caller
        that reads the total, adds, and writes it back loses one accumulation
        whenever two callers read before either writes — and that is exactly the
        shape of `_bill`, the settle path's own recorder: it writes tokens with
        NO state change, so the state compare that serialises `advance` does not
        serialise it, and two replicas can both lose their compare and then both
        read the same total. A RELATIVE `SET` cannot lose an increment, so no
        compare is needed at all. ``None`` means the row is gone.
        """
        ...

    def put_item_if_state(self, org_id: str, item: BatchItem,
                          expected: BatchItemState,
                          expected_attempts: int | None = None,
                          token_delta: tuple[int, int] | None = None) -> bool:
        """COMPARE-AND-SET: write ``item`` only if the stored row is still in
        ``expected``. Returns True when the write landed.

        ⛔ ``expected_attempts`` EXTENDS THE COMPARE TO THE CLAIM IDENTITY, and
        without it a CLAIM cannot lose (#14415 review round 5, BLOCKING 1). A
        re-claim writes ``state = <the state it read>`` — the compare key is the
        value the caller is about to write back — so two re-claimers over one
        stale row BOTH match, both run the runner, and the customer is charged
        twice for one document (reproduced: 2 runner calls, **120 000 tokens
        billed for 60 000 spent**). `attempts` is what actually distinguishes one
        claim from the next, so the claim paths pass it and the counter becomes
        part of the compare instead of a blind SET of a value both writers
        computed from their own read.

        ⛔ WHY THIS EXISTS (#14415 review round 4, BLOCKING 1). A plain
        ``put_item`` is a read-modify-write with the read done by the CALLER, and
        the two writers of an item row are genuinely concurrent: the drain runs on
        ``_BATCH_EXECUTOR`` and a cancel on ``_BATCH_DB_EXECUTOR``. With only an
        unconditional write, a cancel that read ``queued`` could overwrite the
        drain's ``extracting`` claim — and the drain's runner then ran, spent the
        tokens and had its result thrown away — while a cancel landing inside the
        drain's read-to-claim window let the drain claim an item the customer had
        just stopped, indexing and charging a document they cancelled. Both were
        REPRODUCED on the pre-fix tree. The compare belongs where the row lives.

        A store that cannot do this atomically must still refuse rather than
        silently degrade: returning True from a non-atomic implementation is the
        same defect wearing a better name.
        """
        ...

    def items(self, org_id: str, batch_id: str) -> list[BatchItem]: ...

    def cancel_queued_items(self, org_id: str, batch_id: str, *,
                            reason: str, updated_at: str) -> int:
        """CAS a batch's WHOLE `queued` set to `cancelled` in ONE statement.

        ⛔ ONE STATEMENT, NOT ONE PER ITEM (#14415 review round 7, P2). `cancel()`
        walked the item list, and each iteration cost `advance` a `get_item` plus
        a `put_item_if_state` — two sequential graph round trips per item, four
        for a stale claim. A cancel of the documented maximum batch
        (`_BATCH_MAX_ITEMS`, default 1000) was therefore ~2 000 sequential
        statements inside ONE request, on an executor with two workers: at the
        contention this module itself cites (a 1 505 ms DB probe) it cannot answer
        inside the 10 s transport budget, so the client gets a 504 over a cancel
        that keeps writing — and the customer's very next status read queues
        behind it on the second worker, which is exactly what the pool split
        exists to prevent.

        The compare is still a compare: the statement matches `state = queued`
        only, so a claim taken after `cancel()` read the batch is not overwritten.
        Returns how many rows it matched (0 = every row had already moved on).
        """
        ...

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

    def put_batch_if_state(self, batch: Batch,
                           expected: Collection[BatchState]) -> bool:
        # Atomic under the same lock as `put_batch`: compare and write together.
        # ⛔ The RECORD's identity (`estimate`, `corpus_name`) is preserved from
        # the stored row, not taken from the caller (#14415 recut review, P1):
        # `_resume_batch` passes a Batch built from an orphan-item snapshot, so
        # copying it wholesale erased the estimate an acceptance had just
        # written. A state transition writes state.
        with self._lock:
            current = self._batches.get((batch.org_id, batch.batch_id))
            if current is None or current.state not in expected:
                return False
            written = _copy_batch(batch)
            written.estimate = dict(current.estimate)
            written.corpus_name = current.corpus_name
            self._batches[(batch.org_id, batch.batch_id)] = written
            return True

    def put_batch(self, batch: Batch) -> None:
        with self._lock:
            self._batches[(batch.org_id, batch.batch_id)] = _copy_batch(batch)

    def put_batch_if_absent(self, batch: Batch) -> bool:
        with self._lock:
            key = (batch.org_id, batch.batch_id)
            if key in self._batches:
                return False
            self._batches[key] = _copy_batch(batch)
            return True

    def get_batch(self, org_id: str, batch_id: str) -> Batch | None:
        with self._lock:
            b = self._batches.get((org_id, batch_id))
            return _copy_batch(b) if b else None

    def list_recent_batches(self, org_id: str, *, limit: int = 20
                            ) -> list[Batch]:
        with self._lock:
            rows = [b for (o, _), b in self._batches.items() if o == org_id]
        rows.sort(key=lambda b: b.created_at, reverse=True)
        return [_copy_batch(b) for b in rows[:limit]]

    def add_item_tokens(self, org_id: str, batch_id: str, item_id: str, *,
                        tokens_in: int = 0, tokens_out: int = 0,
                        updated_at: str = "") -> BatchItem | None:
        # Atomic under the same lock `put_item` uses: read and add in ONE hold,
        # so no other writer can interleave a read between them.
        with self._lock:
            bucket = self._items.get((org_id, batch_id))
            row = bucket.get(item_id) if bucket else None
            if row is None:
                return None
            row.tokens_in = max(0, row.tokens_in) + max(0, int(tokens_in))
            row.tokens_out = max(0, row.tokens_out) + max(0, int(tokens_out))
            if updated_at:
                row.updated_at = updated_at
            return _copy_item(row)

    def put_items(self, org_id: str, items: Sequence[BatchItem]) -> None:
        with self._lock:
            for item in items:
                bucket = self._items.setdefault((org_id, item.batch_id), {})
                bucket[item.item_id] = _copy_item(item)

    def put_item(self, org_id: str, item: BatchItem) -> None:
        with self._lock:
            bucket = self._items.setdefault((org_id, item.batch_id), {})
            bucket[item.item_id] = _copy_item(item)

    def put_item_if_state(self, org_id: str, item: BatchItem,
                          expected: BatchItemState,
                          expected_attempts: int | None = None,
                          token_delta: tuple[int, int] | None = None) -> bool:
        # Atomic by construction: the compare and the write are inside ONE
        # `self._lock` hold, so no other writer can interleave.
        with self._lock:
            bucket = self._items.get((org_id, item.batch_id))
            current = bucket.get(item.item_id) if bucket else None
            if current is None or current.state is not expected:
                return False
            if (expected_attempts is not None
                    and current.attempts != expected_attempts):
                # The claim identity moved: a peer re-claimed this row between
                # our read and here, so this write would run a second runner.
                return False
            if token_delta is None:
                # ⛔ KEEP THE ROW'S TOKEN COLUMNS (#14415 review round 8, P2).
                # The caller's copy predates any accumulation that landed since it
                # read, so writing it back erased that increment — the same defect
                # the graph store had, and the reason the Protocol now says a
                # non-accumulating CAS does not touch tokens.
                kept = _copy_item(item)
                kept.tokens_in = max(0, current.tokens_in)
                kept.tokens_out = max(0, current.tokens_out)
                bucket[item.item_id] = kept
                item.tokens_in, item.tokens_out = kept.tokens_in, kept.tokens_out
                return True
            # ⛔ THE ADDITION HAPPENS HERE, NOT IN THE CALLER (#14415 review
            # round 7, P2). `item.tokens_in` is the caller's read PLUS its own
            # delta, so writing it back OVERWRITES whatever a writer that matched
            # the same compare added in between — and the settle path's compare is
            # on state alone, which two writers settling to the SAME state both
            # satisfy. Adding the delta to the row this lock is holding cannot
            # lose an increment, and the caller's copy is corrected to the row.
            written = _copy_item(item)
            written.tokens_in = max(0, current.tokens_in) + max(
                0, int(token_delta[0]))
            written.tokens_out = max(0, current.tokens_out) + max(
                0, int(token_delta[1]))
            bucket[item.item_id] = written
            item.tokens_in, item.tokens_out = written.tokens_in, written.tokens_out
            return True

    def cancel_queued_items(self, org_id: str, batch_id: str, *,
                            reason: str, updated_at: str) -> int:
        # One lock hold for the whole set: the compare and every write are
        # indivisible, which is the property the per-item loop only had per item.
        with self._lock:
            bucket = self._items.get((org_id, batch_id), {})
            stopped = 0
            for row in bucket.values():
                if row.state is BatchItemState.QUEUED:
                    row.state = BatchItemState.CANCELLED
                    row.reason = reason
                    row.updated_at = updated_at
                    stopped += 1
            return stopped

    def items(self, org_id: str, batch_id: str) -> list[BatchItem]:
        with self._lock:
            bucket = self._items.get((org_id, batch_id), {})
            return [_copy_item(i) for i in bucket.values()]

    def get_item(self, org_id: str, batch_id: str, item_id: str
                 ) -> BatchItem | None:
        with self._lock:
            found = self._items.get((org_id, batch_id), {}).get(item_id)
            return _copy_item(found) if found else None

    def discard(self, org_id: str, batch_id: str) -> None:
        with self._lock:
            self._items.pop((org_id, batch_id), None)
            self._batches.pop((org_id, batch_id), None)

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
    """A copy carrying the ROW's facts — and NEVER the charge record.

    ⛔ `charge_in`/`charge_out` are the "what THIS call spent" record, and their
    whole point is that they cannot be re-read and charged again (#14415 review
    round 5, P3). A store that round-tripped them let a re-read of a settled row
    re-meter the same spend, in the very store the money tests drive; the graph
    store never writes them, so zeroing here is what keeps the in-memory store
    honest to the same Protocol promise.
    """
    return BatchItem(**{**i.__dict__, "charge_in": 0, "charge_out": 0})


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

    def put_batch_if_absent(self, batch: Batch) -> bool:
        """⛔ CREATE-IF-ABSENT: `MERGE … ON CREATE SET` (#14415 review round 11, P2).

        `MERGE` matches the key, so an existing row is LEFT ALONE — no `SET` runs
        at all, which is what makes this safe for an adoption racing a real
        acceptance. A blind `SET` (what `put_batch` does) overwrote the ACK's
        `estimate`/`corpus_name` with an orphan snapshot's empties.

        Returns True only when THIS call created the row, so the caller knows
        whether it may trust its own object or must re-read.

        ⛔ A PER-CALL TOKEN, NOT `created_at` (#14415 review cycle 2, MEDIUM). The
        verdict compared `b.created_at` to ours — and the caller is `_resume_batch`,
        which derives that stamp from `orphan_items[0].updated_at`, so two adoptions
        of ONE orphan row pass the SAME stamp and the second read as CREATED over a
        row it did not write (MEASURED on the live engine: a pre-existing row with an
        equal stamp returns it). A value only THIS call can produce is the only
        sound test of "did I create it", and it also removes the `not rows`
        fail-open. The token is written only by `ON CREATE SET`, so it never appears
        on a reused row.
        """
        token = uuid.uuid4().hex
        rows = self._q(
            f"MERGE (b:{BATCH_LABEL} {{id:$bid, org_id:$org}}) "
            "ON CREATE SET b.state=$state, b.created_at=$created, "
            "    b.updated_at=$updated, b.estimate_json=$estimate, "
            "    b.cancelled_at=$cancelled_at, b.cancel_reason=$cancel_reason, "
            "    b.corpus_name=$corpus, b.create_token=$token "
            "RETURN b.create_token",
            {"bid": batch.batch_id, "org": batch.org_id,
             "state": str(batch.state), "created": batch.created_at,
             "updated": batch.updated_at,
             "estimate": _json_dumps(batch.estimate),
             "cancelled_at": batch.cancelled_at,
             "cancel_reason": batch.cancel_reason,
             "corpus": batch.corpus_name,
             "token": token},
        )
        return bool(rows) and str(rows[0][0] or "") == token

    def put_batch_if_state(self, batch: Batch,
                           expected: Collection[BatchState]) -> bool:
        """A compare-and-set on the BATCH row — `MATCH … state IN $expect`.

        ⛔ THIS WRITES STATE, NOT THE RECORD'S IDENTITY (#14415 recut review,
        P1). It used to `SET b.estimate_json=$estimate, b.corpus_name=$corpus`
        from the CALLER's object, and the caller is `_resume_batch`, which builds
        its `Batch` from an orphan-item snapshot when the batch row had not been
        written yet. So an acceptance landing between that snapshot and this CAS
        was MATCHED (`submit()` writes `ACCEPTED`) and then OVERWRITTEN with the
        empty estimate — the arithmetic owner §4 requires on the record, erased
        permanently, with nothing that re-derives it, and `GET /v1/batches`
        reporting `estimate: {}`. No caller of this method mutates `estimate` or
        `corpus_name` before calling it (verified: zero assignments to either
        outside the two writers that own them), so a state transition has no
        business rewriting them. The columns are written by `put_batch` alone —
        the acceptance that owns the estimate and the orphan adoption that has
        none to lose.
        """
        rows = self._q(
            f"MATCH (b:{BATCH_LABEL} {{id:$bid, org_id:$org}}) "
            "WHERE b.state IN $expect "
            "SET b.state=$state, b.updated_at=$updated, "
            "    b.cancelled_at=$cancelled_at, b.cancel_reason=$cancel_reason "
            "RETURN b.id",
            {"bid": batch.batch_id, "org": batch.org_id,
             "expect": [str(e) for e in expected],
             "state": str(batch.state), "updated": batch.updated_at,
             "cancelled_at": batch.cancelled_at,
             "cancel_reason": batch.cancel_reason},
        )
        return bool(rows)

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

    def list_recent_batches(self, org_id: str, *, limit: int = 20
                            ) -> list[Batch]:
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

    def add_item_tokens(self, org_id: str, batch_id: str, item_id: str, *,
                        tokens_in: int = 0, tokens_out: int = 0,
                        updated_at: str = "") -> BatchItem | None:
        """⛔ A RELATIVE `SET`, never a read-then-write (#14415 review round 5, P3).

        `MATCH ... SET i.tokens_in = coalesce(i.tokens_in, 0) + $tin` does the
        addition in the database, so two concurrent accumulations BOTH apply.
        A read-modify-write would let them read the same total and lose one — the
        row then disagrees with the ledger, which is a receipt that under-reports
        the bill.

        `MATCH`, deliberately NOT `MERGE`: a MERGE here would CREATE the row it
        failed to match, resurrecting a row that was deleted or never existed.
        """
        rows = self._q(
            f"MATCH (i:{ITEM_LABEL} {{id:$iid, batch_id:$bid, org_id:$org}}) "
            "SET i.tokens_in = coalesce(i.tokens_in, 0) + $tin, "
            "    i.tokens_out = coalesce(i.tokens_out, 0) + $tout, "
            "    i.updated_at = $updated "
            "RETURN i.id",
            {"iid": item_id, "bid": batch_id, "org": org_id,
             "tin": max(0, int(tokens_in)), "tout": max(0, int(tokens_out)),
             # ⛔ `_utc_now()`, NOT `self._now()` (#14415 recut review, P3). `_now`
             # is a `BatchCoordinator` attribute; `GraphBatchStore` never defines
             # it, so the `updated_at=""` default — the signature's own documented
             # form — raised `AttributeError: 'GraphBatchStore' object has no
             # attribute '_now'`. `_bill` always passes a stamp, so the live path
             # never took it, and the pin that was supposed to drive every
             # statement did not drive this one: a latent crash in a public store
             # method, invisible to the whole suite until the pin was widened.
             "updated": updated_at or _utc_now()})
        if not rows:
            return None
        # Read back through the ONE row projection, so this cannot drift from
        # `get_item`'s field list.
        return self.get_item(org_id, batch_id, item_id)

    def put_items(self, org_id: str, items: Sequence[BatchItem]) -> None:
        """⛔ ONE `UNWIND` FOR THE WHOLE SET (#14415 review round 13, P2).

        `UNWIND $rows AS row MERGE (i:…) SET i… ` is the same write as `put_item`
        performed `rows` times, in a single statement — the shape the set-based
        cancel already uses. Order is preserved because the acceptance still writes
        the batch row AFTER this returns.
        """
        if not items:
            return
        self._q(
            f"UNWIND $rows AS row "
            f"MERGE (i:{ITEM_LABEL} {{id:row.iid, batch_id:row.bid, "
            # ⛔ THE `f` PREFIX IS LOAD-BEARING (#14415 review cycle 2, BLOCKING).
            # This line closes the escaped `{{` opened on the line above, so as a
            # PLAIN literal its `}}` reached FalkorDB verbatim: `... row.org}}) SET
            # ...` → `Invalid input '}': expected ')'`. MEASURED on the docker lane
            # against the brand-new bulk write — EVERY acceptance failed, while the
            # hermetic suite stayed green because the brace guard sees a BALANCED
            # statement (two closes for two opens) and a fake `query` accepts any
            # string. This is the round-9 defect, reintroduced by the round-13 bulk
            # rewrite that replaced the per-item loop.
            f"org_id:row.org}}) "
            "SET i.ref=row.ref, i.name=row.name, i.staged_path=row.staged, "
            "    i.state=row.state, i.reason=row.reason, "
            "    i.content_key=row.key, i.tokens_in=row.tin, "
            "    i.tokens_out=row.tout, i.attempts=row.attempts, "
            "    i.content_changed=row.changed, "
            "    i.duplicate_of=row.duplicate, i.updated_at=row.updated",
            {"rows": [{"iid": i.item_id, "bid": i.batch_id, "org": org_id,
                       "ref": i.ref, "name": i.name, "staged": i.staged_path,
                       "state": str(i.state), "reason": i.reason,
                       "key": i.content_key, "tin": int(i.tokens_in),
                       "tout": int(i.tokens_out), "attempts": int(i.attempts),
                       "changed": bool(i.content_changed),
                       "duplicate": i.duplicate_of, "updated": i.updated_at}
                      for i in items]},
        )

    def put_item(self, org_id: str, item: BatchItem) -> None:
        self._q(
            f"MERGE (i:{ITEM_LABEL} {{id:$iid, batch_id:$bid, org_id:$org}}) "
            "SET i.ref=$ref, i.name=$name, i.staged_path=$staged, "
            "    i.state=$state, i.reason=$reason, "
            "    i.content_key=$key, i.tokens_in=$tin, i.tokens_out=$tout, "
            "    i.attempts=$attempts, i.content_changed=$changed, "
            # ⛔ `duplicate_of` IS PART OF THE RECORD (#14415 review round 12,
            # P2). It was the ONE item field the production store never
            # round-tripped: `submit()` sets it, `as_dict()` puts it on the wire
            # and `run_item` branches on it, but this SET listed no column and the
            # read projections returned none — so on the real store the wire always
            # said `duplicate_of: ""` and `run_item`'s twin branch (the one that
            # stops a repeat being SKIPPED when its twin FAILED) was unreachable,
            # while the test that pins it ran against the in-memory store, which
            # does preserve it.
            "    i.duplicate_of=$duplicate, i.updated_at=$updated",
            {"iid": item.item_id, "bid": item.batch_id, "org": org_id,
             "ref": item.ref, "name": item.name, "staged": item.staged_path,
             "state": str(item.state),
             "reason": item.reason, "key": item.content_key,
             "tin": int(item.tokens_in), "tout": int(item.tokens_out),
             "attempts": int(item.attempts),
             "changed": bool(item.content_changed),
             "duplicate": item.duplicate_of,
             "updated": item.updated_at},
        )

    def put_item_if_state(self, org_id: str, item: BatchItem,
                          expected: BatchItemState,
                          expected_attempts: int | None = None,
                          token_delta: tuple[int, int] | None = None) -> bool:
        """COMPARE-AND-SET in the DATABASE — `MATCH … {state:$expect}` then SET.

        ⛔ `MATCH` (not `MERGE`) is the whole point: a row that has moved on no
        longer matches the pattern, so the `SET` applies to ZERO rows and the
        caller learns it lost the race from an empty result. A `MERGE` would
        recreate the pattern row and silently undo the other writer — the exact
        defect this method exists to close.
        """
        # `attempts` joins the pattern only when the caller named it, because it
        # is the claim's IDENTITY: a re-claim compares against a state it is
        # about to write back, so state alone cannot distinguish two claimers.
        attempts_clause = ("AND i.attempts = $expect_attempts "
                           if expected_attempts is not None else "")
        rows = self._q(
            f"MATCH (i:{ITEM_LABEL} {{id:$iid, batch_id:$bid, org_id:$org, "
            # ⛔ THIS LINE MUST BE AN f-STRING (#14415 review round 9, BLOCKING):
            # the sentence above escapes its opening brace as `{{`, so the
            # matching `}}` only closes correctly in an f-string. A plain literal
            # emits a DOUBLE brace and FalkorDB refuses the statement —
            # `Invalid input '}': expected ')'` — which made EVERY item-state
            # write fail on the production store while 135 hermetic tests stayed
            # green (a fake `query` accepts any string). Measured on the docker
            # lane: without the prefix `run_item` and `cancel` both raise and the
            # batch sits `resuming` for ever; with it the CAS behaves exactly as
            # documented. `test_every_emitted_statement_is_brace_balanced` pins
            # the shape so a string this module hands to FalkorDB cannot be
            # malformed again.
            f"state:$expect}}) "
            f"WHERE 1=1 {attempts_clause}"
            "SET i.ref=$ref, i.name=$name, i.staged_path=$staged, "
            "    i.state=$state, i.reason=$reason, "
            "    i.content_key=$key, "
            # ⛔ A RELATIVE WRITE WHEN THIS CALL IS ACCUMULATING SPEND (#14415
            # review round 7, P2). The absolute form is a read-modify-write, and
            # the settle path's compare is on STATE alone — two writers that both
            # settle to the SAME state both match, each writes its own read+delta,
            # and the second OVERWRITES the first instead of adding (200 spent,
            # 200 billed, `tokens_spent: 100`). The addition is done by the
            # database, so both increments apply.
            # ⛔ AND WHEN THIS CALL IS NOT ACCUMULATING, IT WRITES NO TOKEN
            # COLUMN AT ALL (#14415 review round 8, P2). It used to write the
            # totals back absolutely, from the caller's own read — a
            # read-modify-write two round trips wide, and this compare is on
            # state (plus attempts when named), which `add_item_tokens` changes
            # NEITHER. So an accumulation landing inside that window was erased:
            # measured — a cancel read `tokens_in=5`, the drain lost its settle and
            # `_bill`ed its real 60 000 (`add_item_tokens` → 60 005, state and
            # attempts untouched), the cancel's CAS still matched, and it wrote
            # `SET i.tokens_in=5` — 60 000 gone from the row for good, receipt
            # `5` against a ledger of 60 005. Omitting the column cannot lose an
            # increment, because the row keeps whatever it holds.
            + ("i.tokens_in = coalesce(i.tokens_in, 0) + $tin, "
               "i.tokens_out = coalesce(i.tokens_out, 0) + $tout, "
               if token_delta is not None else "")
            + "i.attempts=$attempts, i.content_changed=$changed, "
            "    i.updated_at=$updated "
            "RETURN i.id",
            {"iid": item.item_id, "bid": item.batch_id, "org": org_id,
             "expect": str(expected),
             "ref": item.ref, "name": item.name, "staged": item.staged_path,
             "state": str(item.state),
             "reason": item.reason, "key": item.content_key,
             # Sent only when the SET above names them, so an unused parameter
             # is never handed to the query.
             **({"tin": int(token_delta[0]), "tout": int(token_delta[1])}
                if token_delta is not None else {}),
             # ⛔ `attempts` IS NAMED BY THE SET ABOVE, so it must be in the map
             # (#14415 review round 13). The `token_delta` refactor rebuilt this
             # dict and dropped the key, and the statement then failed at the
             # engine with `Missing parameters` on EVERY compare-and-set — the
             # claim, the settle, the cancel, the resume. Only the live test saw
             # it; the hermetic ones use a fake `query`.
             "attempts": int(item.attempts),
             "changed": bool(item.content_changed),
             "updated": item.updated_at,
             # Sent only when the pattern above names it, so an unused parameter
             # is never passed to the query.
             **({"expect_attempts": int(expected_attempts)}
                if expected_attempts is not None else {})},
        )
        return bool(rows)

    def cancel_queued_items(self, org_id: str, batch_id: str, *,
                            reason: str, updated_at: str) -> int:
        """⛔ `MATCH … SET … RETURN count` — ONE statement for the whole set.

        The compare is the pattern: `state:$expect` matches only rows still
        `queued`, so a claim taken after `cancel()` read the batch is not
        overwritten (the property the per-item compare-and-set had, kept without
        its per-item round trip). No `MERGE`: a SET on a pattern that matches
        nothing is a no-op, which is what a fully-claimed batch must be.
        """
        rows = self._q(
            f"MATCH (i:{ITEM_LABEL} {{batch_id:$bid, org_id:$org, "
            f"state:$expect}}) "
            "SET i.state=$state, i.reason=$reason, i.updated_at=$updated "
            "RETURN count(i)",
            {"bid": batch_id, "org": org_id,
             "expect": str(BatchItemState.QUEUED),
             "state": str(BatchItemState.CANCELLED),
             "reason": reason, "updated": updated_at})
        return int(rows[0][0] or 0) if rows else 0

    def items(self, org_id: str, batch_id: str) -> list[BatchItem]:
        rows = self._q(
            f"MATCH (i:{ITEM_LABEL} {{batch_id:$bid, org_id:$org}}) "
            "RETURN i.id, i.ref, i.name, i.state, i.reason, i.content_key, "
            "       i.tokens_in, i.tokens_out, i.attempts, i.content_changed, "
            "       i.updated_at, i.staged_path, i.duplicate_of ORDER BY i.id",
            {"bid": batch_id, "org": org_id},
        )
        return [BatchItem(
            batch_id=batch_id, item_id=r[0], ref=r[1] or "", name=r[2] or "",
            state=_as_state(BatchItemState, r[3], BatchItemState.QUEUED),
            reason=r[4] or "", content_key=r[5] or "",
            tokens_in=int(r[6] or 0), tokens_out=int(r[7] or 0),
            attempts=int(r[8] or 0), content_changed=bool(r[9]),
            updated_at=r[10] or "",
            staged_path=(r[11] or "") if len(r) > 11 else "",
            duplicate_of=(r[12] or "") if len(r) > 12 else "") for r in rows]

    def get_item(self, org_id: str, batch_id: str, item_id: str
                 ) -> BatchItem | None:
        rows = self._q(
            f"MATCH (i:{ITEM_LABEL} {{id:$iid, batch_id:$bid, org_id:$org}}) "
            "RETURN i.ref, i.name, i.state, i.reason, i.content_key, "
            "       i.tokens_in, i.tokens_out, i.attempts, i.content_changed, "
            "       i.updated_at, i.staged_path, i.duplicate_of",
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
            staged_path=(r[10] or "") if len(r) > 10 else "",
            # ⛔ THE COLUMN IS ASKED FOR, SO IT MUST BE MAPPED (#14415 review
            # round 13, P2). `RETURN` listed `i.duplicate_of` and the constructor
            # stopped at `r[10]`, so `get_item` always yielded `duplicate_of=""` on
            # the production store — and `run_item` reads its row through `_fresh`
            # → `get_item`, which made its TWIN branch (the one that stops a repeat
            # being skipped when its twin failed) unreachable there: the same bytes
            # could be extracted, stored and charged a second time.
            duplicate_of=(r[11] or "") if len(r) > 11 else "")

    def discard(self, org_id: str, batch_id: str) -> None:
        self._q(f"MATCH (i:{ITEM_LABEL} {{org_id:$org, batch_id:$bid}}) "
                "DETACH DELETE i",
                {"org": org_id, "bid": batch_id})
        self._q(f"MATCH (b:{BATCH_LABEL} {{org_id:$org, id:$bid}}) "
                "DETACH DELETE b",
                {"org": org_id, "bid": batch_id})

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
        # ⛔ SORTED HERE, NOT IN CYPHER (#14415 review round 8, BLOCKING 1,
        # measured). A `UNION` with a trailing `ORDER BY` is ACCEPTED by FalkorDB
        # and the clause is then IGNORED — the real query came back in insertion
        # order, byte-identical to the same query without it, three runs out of
        # three (the single-arm control sorts correctly, so the engine honours
        # `ORDER BY` and just not after a union). The rotating recovery window is
        # only fair over a DETERMINISTIC list, so the order is imposed by the
        # caller: an inert clause in the Cypher would have been exactly the
        # "assertion that cannot fail" this branch keeps having to remove.
        return sorted(r[0] for r in rows)

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
    for a batch — the item is retried rather than silently treated as terminal.
    ⛔ Read it as terminal and work is dropped without a trace; that is the one
    direction it must never fail.

    ⚠️ The mismatch is currently NOT surfaced (#14219 review round 2, NB10): the
    fallback state is set but no ``reason`` records the unmapped value, so an
    operator sees a ``queued`` row rather than an explanation. Turning it into a
    ``reason`` needs the write path to know it was a fallback (the state is
    already lost by the time the row is written), so it is a follow-up rather
    than a docstring promise.
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
#: The bound is enforced TWICE, deliberately: the semaphore in
#: :class:`BatchWorker` (never more than N *batches* in flight) and
#: ``max_workers`` on the pool (nothing else can widen it at runtime). A single
#: enforcement point that a future caller can bypass is how a ceiling becomes
#: advisory. ⚠️ It bounds BATCHES, not items — a batch's items run one at a time
#: inside its own drain, so the item-level concurrency is at most this number,
#: reached only when that many batches are genuinely in flight at once.
BATCH_ITEMS_IN_FLIGHT = 2

#: How long an `extracting`/`embedded` claim must be UNTOUCHED before `recover()`
#: may take it back. See :meth:`BatchCoordinator.recover` — this is what stops a
#: poll on one instance from requeueing a document another instance is working.
#: 15 minutes is ~16x the measured per-document wall clock (#14295, 56.25 s).
BATCH_RECLAIM_AFTER_S = 900.0


def record_item_spend(org_id: str, *, tokens_in: int = 0,
                      tokens_out: int = 0) -> int:
    """Charge ONE runner call's MEASURED tokens to the `capture_extraction` meter.

    ⛔ **ONE BUDGET, ONE METER** (owner `#14275`: *"one budget, not a fourth
    metered thing"*). A batch item that spends tokens must move the SAME
    ``capture_extraction`` dimension a session does, through the SAME ledger
    writer — otherwise the overage a customer acknowledged is never realised on
    the bill, and the NEXT batch's estimate is computed against a headroom that
    no batch can ever consume.

    ⛔ **The caller passes the figures it JUST SPENT, never an item row**
    (#14415 review round 4, BLOCKING 4). A row's ``tokens_in/out`` are the
    document's totals: another process may have written them, or an earlier pass
    of this one may already have charged them. Charging from the row therefore
    double-charges a retried item (measured: pass 1 `queued` with 100 tokens, pass
    2 `indexed` with 100 → 200 on the meter while the status surface said 100)
    and re-charges a row a peer settled. :meth:`BatchCoordinator.run_item` puts
    exactly the landed call's figures on ``item.charge_in``/``charge_out``,
    which are not persisted, so the same spend cannot be charged twice.

    ``calls=0``: a batch item is NOT a capture call, and this layer has no
    measured price for one. That keeps ``capture_calls`` (the denominator that
    distinguishes a genuinely-free capture from an unmeasured one, #3359) reading
    true, and it does not add a phantom ``$0`` to the spend ceiling. ⚠️ DECLARED
    CONSEQUENCE: the cost ceiling therefore does not see batch spend — only the
    token dimension moves. That is honest while the per-file indexer spends
    nothing; when the extraction lane is wired here it must pass a real
    ``cost_usd`` through a sibling call rather than let a token figure imply one.

    Returns the tokens recorded (0 when the call spent nothing — the common case
    today). A zero-token call is deliberately NOT made: it would mint a row for a
    capture that never happened.

    A failed increment is REPORTED to the durable operator surface, not merely
    logged — the capture path's own rule (#3824/#3981): a metering loss must be
    visible to the surface that exists for it, or the customer's spend silently
    drifts from the ledger. It never fails a document that was already indexed.
    """
    tokens_in = max(0, int(tokens_in or 0))
    tokens_out = max(0, int(tokens_out or 0))
    if not org_id or (tokens_in + tokens_out) <= 0:
        return 0
    try:
        from tortoise.metering import record_capture_usage

        record_capture_usage(org_id, calls=0, tokens_in=tokens_in,
                             tokens_out=tokens_out)
    except Exception as exc:  # never fail a delivered document
        _logger.warning("batch metering dropped for team %s", org_id,
                        exc_info=True)
        _report_unmetered(org_id, exc)
        return 0
    return tokens_in + tokens_out


def _report_unmetered(org_id: str, exc: BaseException) -> None:
    """Report a dropped token increment to `metering`'s durable surface.

    Mirrors `hosted_api._alert_unmetered`: the same drop class the capture
    ledger uses, so an operator reads ONE surface and a batch loss cannot hide
    behind a log line (#14415 review round 4, NB6). Best-effort: this runs on a
    failure path and must not raise a second time.
    """
    try:
        from tortoise.metering import report_unmetered_increment

        # ⛔ ITS OWN LANE, not `capture_ledger` (#14415 review round 7, N3).
        # `lane` names the SWALLOW SITE so an alert is attributable to the handler
        # that dropped the increment; reusing the capture lane made a batch loss
        # indistinguishable from a capture loss on the surface that exists to
        # attribute it. The keyword form is required — the swallow-site census
        # matches `lane="…"` only, so a positional call would be invisible to the
        # fence that is supposed to enumerate these sites.
        report_unmetered_increment(lane="batch_ingest", org_id=org_id, error=exc)
    except Exception:
        # ⛔ THE FALLBACK STILL ALERTS (#14415 review round 7, N3). A log line on
        # an ephemeral rootfs is the #3677 loss class; when `metering` itself
        # cannot be imported, `operator_alert` is the standing fallback — the same
        # one `hosted_api._alert_unmetered` uses.
        try:
            from tortoise.operator_alert import alert_unmetered_increment

            alert_unmetered_increment("batch_ingest", org_id, exc)
        except Exception:
            _logger.warning("batch metering loss could not be reported for %s",
                            org_id, exc_info=True)


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
#: Every field of :class:`BatchItem`, so one item's facts can be copied onto
#: another object. `advance` writes from the STORED ROW and then updates the
#: caller's own copy in place: callers legitimately chain
#: ``advance(row, A)`` → ``advance(row, B)`` on one object, and a caller holding
#: a copy that silently stopped tracking the row is how a legal second step reads
#: as a stale row.
_ITEM_FIELDS: tuple[str, ...] = tuple(BatchItem.__dataclass_fields__)


def _sync_item(target: BatchItem, source: BatchItem) -> BatchItem:
    """Bring ``target`` up to date from ``source`` IN PLACE, and return target."""
    if target is source:
        return target
    for name in _ITEM_FIELDS:
        setattr(target, name, getattr(source, name))
    return target


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

        ⛔ THAT SENTENCE IS ONLY TRUE BECAUSE OF THE STATEMENT COUNT, and it was
        NOT true when it was first written: the item loop called `put_item` once
        per item, so the documented maximum (1 000) meant ~1 001 sequential round
        trips inside one request on the two-worker bookkeeping pool — the cost the
        set-based cancel exists to remove, at half the count. Exceeding the budget
        there is worse than slow: the customer gets a 504 over an acceptance that
        COMMITTED, and the route mints its own `batch_id` with no idempotency key,
        so the retry opens a SECOND batch and every document is ingested and
        charged twice. The set now goes in ONE `UNWIND` (`put_items`), leaving the
        row writes at two statements regardless of batch size — the only per-item
        work left is staging an inline body to disk, which is a local write and
        not a round trip.

        ⛔ The items are written BEFORE the batch is marked ``accepted``: a batch
        row that said ``accepted`` over items that did not land would be a
        receipt for work we did not record (§2.0 — the record is truth).
        """
        bid = batch_id or new_batch_id()
        # ⛔ A CALLER-SUPPLIED ID IS VALIDATED AT THE BOUNDARY, before any row or
        # byte is written. `bid` is not only a store key: it is the staging
        # directory's name, so an id carrying a path separator or `..` reached the
        # filesystem (measured — see `_batch_staging_dir`). The sink guard is the
        # authority and holds for every caller; this check exists so the caller is
        # told WHICH argument is wrong and before anything has been written.
        if batch_id is not None and not _is_single_path_component(bid):
            raise ValueError(
                f"batch id {bid!r} is not a single path component — a batch id may "
                "not contain a path separator, '.'/'..' or a NUL byte")
        stamp = self._now()
        # ⛔ A PRE-EXISTING ID IS CHECKED HERE, ABOVE THE `try` — never inside it.
        # The compensating rollback below discards `bid`, and for a caller-supplied
        # id that is the id of a batch that ALREADY EXISTS: letting the refusal
        # enter that rollback deleted the batch it was refusing to overwrite (row,
        # every item row, and every staged document — `discard` removes all three,
        # and `_discard_staged` unlinks the bytes). A refusal must leave the tree
        # exactly as it found it, so the check sits before anything is staged or
        # written, and the exception type below is exempted from the rollback.
        if batch_id is not None and self._store.get_batch(org_id, bid) is not None:
            raise ValueError(f"batch id {bid!r} already exists for this org")
        try:
            return self._submit_rows(org_id, bid, stamp, refs, names=names,
                                     contents=contents, stage_base=stage_base,
                                     corpus_name=corpus_name, estimate=estimate)
        except _BatchIdConflict:
            # The id was minted or taken by a CONCURRENT writer between the check
            # above and the insert. Nothing written here is ours to remove: the
            # batch row belongs to the winner, and the item rows share `bid` with
            # it. Discarding would delete the winner's batch, so this path does
            # the only safe thing — it refuses and leaves the winner alone.
            raise
        except Exception:
            # ⛔ COMPENSATE, or the 503 is a lie (#14219 review round 2,
            # BLOCKING 2). A graph write that times out mid-loop, or a disk error
            # on `stage_text`, used to leave item rows behind: the caller was told
            # "nothing was accepted; retry", the rows made the batch visible, and
            # `recover()` adopted and drained them — so the retry ingested every
            # document twice. The rollback is best-effort by necessity (it is the
            # same store that just failed), and its own failure is logged loudly
            # rather than silently leaving a batch the customer never accepted.
            try:
                self._store.discard(org_id, bid)
                _discard_staged(stage_base, bid)
            except Exception:
                _logger.error(
                    "batch %s acceptance FAILED and could not be rolled back — "
                    "the rows survive and will be resumed; the customer was "
                    "told nothing was accepted (possible duplicate with their "
                    "retry)", bid, exc_info=True)
            raise

    def _submit_rows(self, org_id: str, bid: str, stamp: str,
                     refs: Sequence[str], *,
                     names: Sequence[str] | None,
                     contents: Sequence[str | None] | None,
                     stage_base: str | None,
                     corpus_name: str,
                     estimate: dict[str, Any] | None
                     ) -> tuple[Batch, list[BatchItem]]:
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
            items.append(item)
        # ⛔ THE WHOLE SET IN ONE STATEMENT (#14415 review round 13, P2), and it
        # still lands BEFORE the batch row below, so an orphaned acceptance is the
        # case `_resume_batch` adopts rather than a row pointing at nothing.
        # ⛔ THE ROW IS AN INSERT-IF-ABSENT TOO, AND THE ID IS CHECKED **FIRST**
        # (#14415 review round 17, P2). `put_items` is a blind `MERGE … SET` and
        # `put_batch` a blind overwrite, so accepting over a batch id that already
        # exists — reachable through this method's own `batch_id` argument — reset
        # a `cancelled` batch's item rows to `queued`, zeroed `attempts` (defeating
        # `BATCH_MAX_ATTEMPTS`) and `tokens_in/out`, and relabelled the row
        # `accepted` with `cancelled_at`/`cancel_reason` CLEARED: the cancelled work
        # would be re-drained and charged again. The existence check has to come
        # BEFORE the item write, because that write is what does the damage.
        if self._store.get_batch(org_id, bid) is not None:
            raise _BatchIdConflict(f"batch id {bid!r} already exists for this org")
        self._store.put_items(org_id, items)
        batch = Batch(batch_id=bid, org_id=org_id, state=BatchState.ACCEPTED,
                      created_at=stamp, updated_at=stamp,
                      corpus_name=str(corpus_name or ""),
                      estimate=dict(estimate or {}))
        if not self._store.put_batch_if_absent(batch):
            # Lost the create to a concurrent writer with the same minted id (only
            # possible for a caller-supplied one). Do not pretend it is ours — and
            # do NOT discard: `discard(org_id, bid)` removes the batch row AND its
            # item rows, and `bid` belongs to the WRITER THAT WON. Deleting the
            # winner's batch to tidy up after the loser is the same destruction the
            # pre-existing-id check above exists to prevent, one race window later.
            raise _BatchIdConflict(f"batch id {bid!r} was created concurrently")
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
                attempt: bool = False,
                expected: BatchItemState | None = None,
                expected_attempts: int | None = None) -> BatchItem:
        """Move one item and return the ROW (never the caller's intent).

        A thin wrapper over :meth:`_advance_landed`, which also reports whether
        THIS call's write landed — the one fact a caller cannot infer from the
        returned row, because a lost race returns the WINNER's row, which can be
        indistinguishable from a win.
        """
        return self._advance_landed(
            org_id, item, new_state, reason=reason, tokens_in=tokens_in,
            tokens_out=tokens_out, content_key=content_key,
            content_changed=content_changed, attempt=attempt,
            expected=expected, expected_attempts=expected_attempts)[0]

    def _advance_landed(self, org_id: str, item: BatchItem,
                        new_state: BatchItemState, *,
                        reason: str = "", tokens_in: int | None = None,
                        tokens_out: int | None = None,
                        content_key: str | None = None,
                        content_changed: bool | None = None,
                        attempt: bool = False,
                        expected: BatchItemState | None = None,
                        expected_attempts: int | None = None
                        ) -> tuple[BatchItem, bool]:
        """Move one item, validating the edge. Raises :class:`IllegalTransition`.

        Returns ``(row, landed)``. ``landed`` is True iff THIS call's store write
        was the one that took effect, which is what a caller needs to charge the
        spend it caused and to refuse work it does not own (#14415 review round
        5, HIGH).

        ⛔ **IT IS A COMPARE-AND-SET ON THE ROW'S STATE, and that is load-bearing**
        (#14219 review round 2, BLOCKING 4). Every caller holds a COPY read some
        time ago, and two writers act on the same row routinely: a customer
        cancels while a drain is working, or a peer process settles an item this
        one is about to write. Validating the edge against the CALLER's copy let
        a stale ``queued`` copy walk the legal ``queued → cancelled`` edge over a
        row that was already ``extracting`` — cancelling an item whose model call
        had already spent its tokens, then discarding the result. So the stored
        state is re-read here and a mismatch is NOT written over: the current row
        is returned and the caller sees the truth. A state the row no longer
        holds is not ours to move.

        Idempotence: a no-op transition (same state, same facts) is accepted and
        writes nothing — a retried write after an ambiguous failure must not be
        an error, because the alternative is a batch that wedges on a duplicate
        write it cannot distinguish from a conflict.

        ``expected`` names the state the caller believes the row is in; when it is
        omitted the compare falls back to the CALLER's own copy of the state, so
        **every** write here is a compare-and-set (#14415 review round 8, P3). There
        is no unconditional arm: a `put_item` fallback used to sit below for the
        "no row" case, and it was both unreachable (the state compare returns
        first) and a `MERGE`, which would recreate the row it failed to match —
        the resurrection the vanished-row arm above exists to remove. **The
        returned item is the ROW** when the write lands, so a caller that lost the
        race gets the other writer's row rather than its own stale intent.

        ``expected_attempts`` adds the CLAIM IDENTITY to the compare, and the
        CLAIM PATHS MUST PASS IT (#14415 review round 5, BLOCKING 1): a re-claim
        compares against a state it is about to write back, so state alone lets
        two re-claimers both match one stale row — both run the runner, and one
        document is extracted twice and billed twice (reproduced: 120 000 tokens
        charged for 60 000 spent).
        """
        # ONE read, then the write is conditioned on it. `expect` is the state
        # this write is allowed to overwrite: the caller's explicit `expected`
        # when it named one, otherwise the state its own copy carries (a caller
        # that read a `queued` row must not write over an `extracting` one).
        expect = expected if expected is not None else item.state
        caller_item = item
        stored = self._store.get_item(org_id, item.batch_id, item.item_id)
        if stored is None:
            # ⛔ A VANISHED ROW IS A LOST COMPARE, NOT A LICENCE TO RECREATE IT
            # (#14415 review round 6, P2). This arm used to fall back to
            # `put_item` — a `MERGE`, which CREATES the row it failed to match —
            # and report `landed=True`. The premise ("no row to race with") is
            # false: `submit()` writes the item rows BEFORE the batch row, so a
            # mid-loop acceptance failure leaves orphan item rows, a concurrent
            # `_recover_org_batches` (any `GET /v1/batches` from that tenant)
            # adopts and drains them, and the acceptance rollback's `discard()`
            # then deletes the rows under the drain. The MERGE recreated an item
            # belonging to a batch the customer was told was NOT accepted, the
            # drain reported a settlement, and the spend was metered for it.
            # Nothing is written, and the caller sees the truth: not landed.
            return _sync_item(caller_item, caller_item), False
        if stored.state is not expect:
            # Someone moved it between the caller's read and here. THEIR write
            # wins; report the row, write nothing.
            return _sync_item(caller_item, stored), False
        else:
            # Work from the ROW, never from the caller's copy: the copy may
            # predate another writer's fact-only update (tokens, attempts).
            item = stored
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
            # A pure no-op: nothing was written, so nothing "landed" — but it is
            # also not a LOSS, and the row is the caller's own. Reported as landed
            # so an idempotent retry is not mistaken for a race the caller lost.
            return _sync_item(caller_item, item), True
        item.state = new_state
        if reason or new_state in TERMINAL_ITEM_STATES:
            item.reason = reason
        # ⛔ THE ROW ACCUMULATES; it does not overwrite (#14415 review round 7,
        # BLOCKING 2). A retried item has more than one runner call behind it, and
        # the meter bills every one of them — so a row holding only the LAST
        # call's figures made `status_body()["tokens_spent"]` report 100 for a
        # 200-token spend: the bill and the receipt disagreeing about the same
        # document, which is the very measurement this layer's metering docstring
        # narrates as the defect it fixes. `tokens_in/out` are therefore the
        # document's CUMULATIVE spend, which is exactly what the meter sums.
        # ⛔ KEEP THE DELTA, NOT ONLY THE NEW TOTAL (#14415 review round 7, P2).
        # The row's totals are CUMULATIVE, so `item.tokens_in` = the row's figure
        # plus this call's spend. That is what the RETURNED copy must carry — and
        # it is exactly the wrong thing to hand the store: writing it back is a
        # read-modify-write, and this write's compare is on state alone, which
        # two writers settling to the SAME state both satisfy (a runner reporting
        # a retryable `queued` over a row a requeue already left `queued`). Both
        # would then write `read + 100`, so 200 spent and 200 billed appeared as
        # `tokens_spent: 100`. The store adds THIS delta to whatever the row holds
        # when the compare succeeds.
        token_delta = None
        if tokens_in is not None or tokens_out is not None:
            token_delta = (max(0, int(tokens_in or 0)),
                           max(0, int(tokens_out or 0)))
            item.tokens_in = max(0, item.tokens_in) + token_delta[0]
            item.tokens_out = max(0, item.tokens_out) + token_delta[1]
        if content_key is not None:
            item.content_key = content_key
        if content_changed is not None:
            item.content_changed = bool(content_changed)
        if attempt:
            item.attempts += 1
        item.updated_at = self._now()
        if self._store.put_item_if_state(org_id, item, expect,
                                         expected_attempts=expected_attempts,
                                         token_delta=token_delta):
            return _sync_item(caller_item, item), True
        # The row moved between the read above and this write. Report IT — the
        # caller decides whether its own work still applies.
        return _sync_item(caller_item, self._fresh(org_id, item)), False

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
        if fresh.terminal:
            # ⛔ Read the TERMINAL check from the FRESH row, never from the copy
            # the caller passed (#14219 review round 2, NB2): a stale non-terminal
            # copy used to run the runner against an already-`indexed` row, i.e. a
            # second extraction of a completed document.
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
            # of anything that exists, so it proceeds.
            # ⛔ WHAT THE INDEXER'S GATE DOES AND DOES NOT CATCH (#14415 review
            # round 5, P3 — this comment used to claim the gate deduped any two
            # paths to the same bytes). The gate compares a ``:Source``'s stored
            # ``contentHash`` to the file's hash, and the ``:Source`` identity is
            # derived from the PATH — so it dedupes a RE-SUBMITTED path and does
            # NOT dedupe two different paths carrying identical bytes. Inline
            # text is staged per batch (``<base>/_batches/<batch_id>/…``), so two
            # batches uploading the same document are two sources: the
            # content-key check above is the only cross-batch guard, and it is
            # read-before-run, so two batches draining concurrently can both miss
            # it. Content-addressed staging is the fix (``content_key`` is known
            # at acceptance); until then the honest statement is "deduped within
            # a batch and against already-settled items, serially".
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
        # ⛔ THE ONE LIVENESS RULE (#14415 review round 4, BLOCKING 3). A row is
        # CLAIMABLE only if nobody holds it: `queued` (no claimant), or
        # `extracting`/`embedded` whose claim is STALE — the same window
        # `recover()` applies. Everything else (`cancel`, `recover`) may be
        # careful about a live claim, but if the DRAIN is not, the care buys
        # nothing: a drain iterates every non-terminal item of a batch, so a
        # fresh `extracting` row was re-claimed seconds after `recover()` had
        # correctly declined to touch it — a second extraction of a document a
        # peer was inside, its outcome landing as an illegal edge on top.
        # Applying the rule HERE closes it for every caller, because every claim
        # goes through this one arm.
        if fresh.state in (BatchItemState.EXTRACTING, BatchItemState.EMBEDDED
                           ) and _age_seconds(fresh.updated_at) < float(
                               BATCH_RECLAIM_AFTER_S):
            # Someone else's LIVE claim: not ours to run, and not ours to move.
            # The caller drains the rest and reports this row as it is.
            return fresh
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
            claimed, won = self._advance_landed(
                org_id, fresh, BatchItemState.EXTRACTING,
                attempt=True,
                expected=BatchItemState.QUEUED,
                expected_attempts=fresh.attempts)
            # ⛔ THE CLAIM IS A COMPARE-AND-SET, AND A LOST ONE DOES NOT RUN THE
            # RUNNER (#14415 review round 4, BLOCKING 1). A cancel that landed
            # between the read above and this write leaves the row `cancelled`;
            # `advance` then returns the ROW rather than our intent, and running
            # the runner over it would index and CHARGE a document the customer
            # stopped — the module's own promise ("`queued` → `cancelled`, zero
            # tokens") is broken by exactly that interleaving. Reproduced against
            # the pre-fix tree: item `indexed`, 60 000 tokens, batch `cancelled`.
            #
            # ⛔ AND THE TEST IS `won`, NOT THE RETURNED STATE (#14415 review
            # round 5, P1). The claim writes the very state both the winner and
            # the loser then observe, so `claimed.state is EXTRACTING` is TRUE for
            # a lost compare too: the loser fell through and ran the runner on a
            # document a peer was already inside, billing the same 60 000 tokens
            # twice (reproduced). `_advance_landed`'s flag is the only thing that
            # distinguishes my write from theirs.
            if not won or claimed.state is not BatchItemState.EXTRACTING:
                return claimed
            fresh = claimed
        elif fresh.state in (BatchItemState.EXTRACTING,
                             BatchItemState.EMBEDDED):
            # The fresh-claim guard is ABOVE and covers this arm too — one rule,
            # one place (#14415 review round 7, N5: the copy that lived here was
            # unreachable, proven by line-tracing `run_item`).
            #
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
            # ⛔ A RE-CLAIM IS TWO STATE-CHANGING COMPARE-AND-SETS, NOT ONE
            # (#14415 review round 5, BLOCKING 1). A single re-claim writes
            # `state = <the state it read>`, so the compare key is the value being
            # written back: two re-claimers over one stale row BOTH matched, both
            # called the runner, and one document was extracted twice and BILLED
            # TWICE (reproduced: 2 runner calls, 120 000 tokens charged for 60 000
            # spent). Voiding the dead claim first is a REAL state change
            # (`extracting` → `queued`), so exactly one re-claimer can win it —
            # and the pair also makes the loss unambiguous, which an `attempts`
            # comparison could not: the loser's returned row is the winner's, so
            # "did MY write land" is only answerable from a state the loser's own
            # write would have changed.
            attempts_before = fresh.attempts
            voided = self.advance(org_id, fresh, BatchItemState.QUEUED,
                                  reason="re-claimed by a retry pass",
                                  expected=fresh.state,
                                  expected_attempts=attempts_before)
            if voided.state is not BatchItemState.QUEUED:
                # Another drain voided or settled it first: its row is the truth.
                return voided
            claimed, won = self._advance_landed(
                org_id, voided, BatchItemState.EXTRACTING,
                attempt=True,
                expected=BatchItemState.QUEUED,
                expected_attempts=attempts_before)
            # ⛔ `won`, not the state: see the QUEUED claim above — the loser
            # observes the winner's `extracting` row, so the state test cannot
            # tell them apart and the loser would run the runner too.
            if not won or claimed.state is not BatchItemState.EXTRACTING:
                return claimed
            fresh = claimed
        try:
            outcome = active(fresh)
        except Exception as exc:  # an escaped exception is a FAILED ITEM
            return self.advance(
                org_id, fresh, BatchItemState.FAILED,
                reason=f"{type(exc).__name__}: {exc}"[:500])
        # A cancel that landed while the runner was working wins: the item is
        # already terminal and must not be resurrected (the customer stopped it).
        #
        # ⛔ THE READ IS PROTECTED TOO (#14415 review round 10, P1). It sat
        # outside every `try`, and it runs AFTER the runner has spent: one graph
        # read failure here (this module's own measured 1,505 ms probe / the 10 s
        # statement timeout) raised out of `run_item`, `_drain_batch` only logged
        # it, no charge record was set, so `record_item_spend` was never called and
        # `_bill` was never reached. Measured: `run_item` raises
        # `RuntimeError: graph probe timeout`, the row stays `extracting 0 0`, the
        # meter is never called — and the claim then goes stale, so a reclaim runs
        # the runner AGAIN. The arms below promise the opposite; this one read was
        # simply not covered by them.
        try:
            latest = self._fresh(org_id, fresh)
        except Exception:
            _logger.warning("batch item %s: the post-runner read failed, billing "
                            "the spend that was made", fresh.item_id,
                            exc_info=True)
            return self._bill(org_id, fresh, outcome.tokens_in,
                              outcome.tokens_out)
        if latest.terminal:
            # ⛔ OUR CALL STILL SPENT, AND THE ROW MUST SAY SO TOO (#14415 review
            # rounds 7 and 8, N1 then BLOCKING 2). Reaching here means the runner
            # RAN and reported tokens, but the row was settled by someone else
            # first (a peer, or a cancel). The tokens are a real spend of a
            # genuinely separate call, so they are BILLED — and recorded on the
            # row as well, because a meter that bills 735 against a receipt that
            # says 5 is the same bill/receipt disagreement this layer keeps having
            # to remove (measured: ledger 735 / `tokens_spent` 5).
            return self._bill(org_id, latest, outcome.tokens_in,
                              outcome.tokens_out)
        try:
            settled, landed = self._advance_landed(
                org_id, latest, outcome.state, reason=outcome.reason,
                tokens_in=outcome.tokens_in, tokens_out=outcome.tokens_out,
                content_changed=outcome.content_changed,
                content_key=outcome.content_key or None,
                expected=latest.state)
        except IllegalTransition:
            # Another writer settled this item while the runner was working (a
            # cancel, or the same item drained twice across processes). The item's
            # ROW is authoritative — report it rather than crash the drain, which
            # is what would lose every LATER item. ⛔ AND OUR SPEND IS STILL BILLED
            # AND RECORDED (#14415 review round 8, BLOCKING 2b): this arm used to
            # record NOTHING, so tokens that really were spent appeared on neither
            # surface — the same situation as the terminal arm above, one branch
            # away, decided the other way.
            return self._bill(org_id, self._row_or(org_id, fresh),
                              outcome.tokens_in, outcome.tokens_out)
        except Exception:
            # ⛔ A BOOKKEEPING FAILURE IS NOT A LOST SPEND (#14415 review round 7,
            # P2). `BATCH_QUERY_TIMEOUT_MS`'s own comment promises that a write
            # which cannot complete inside the bound "FAILS the item (visible)" —
            # it did not: the exception left `run_item` entirely, `_drain_batch`'s
            # per-item handler only LOGGED it, no `charge_in/charge_out` was ever
            # set so `record_item_spend` was never called and `_report_unmetered`
            # never fired, and the row stayed `extracting` — where a later stale
            # reclaim re-claims it and runs the runner AGAIN. Measured against the
            # promise: the spend landed on neither the row, the ledger, nor the
            # operator surface. The spend is real (the runner already returned), so
            # it is billed here, exactly as the two arms above do.
            _logger.warning("batch item %s: settle write failed, billing the "
                            "spend that was made", fresh.item_id, exc_info=True)
            return self._bill(org_id, self._row_or(org_id, fresh),
                              outcome.tokens_in, outcome.tokens_out)
        if landed:
            # ⛔ THE CHARGE RECORD (#14415 review round 4, BLOCKING 4 / round 5,
            # HIGH). Only a call this process ACTUALLY LANDED may be charged, and
            # only for the figures it just spent — never the row's totals, which a
            # peer may have written or an earlier pass may already have charged.
            # `landed` is `_advance_landed`'s EXPLICIT flag, not "the returned row
            # has the state I wanted": a peer settling to the same state would
            # pass that test and be charged a second time.
            settled.charge_in = max(0, int(outcome.tokens_in or 0))
            settled.charge_out = max(0, int(outcome.tokens_out or 0))
            return settled
        # The write did not land (a peer moved the row first): the row is
        # authoritative, and our spend is still billed and recorded on it.
        return self._bill(org_id, settled, outcome.tokens_in,
                          outcome.tokens_out)

    def _bill(self, org_id: str, item: BatchItem, tokens_in: int | None,
              tokens_out: int | None) -> BatchItem:
        """Record one runner call's spend on the ROW and mark it for the meter.

        ⛔ THE ROW AND THE LEDGER MUST HOLD THE SAME NUMBER (#14415 review round
        8, BLOCKING 2). `tokens_in/out` are the document's cumulative spend, and
        the meter sums every call, so a call whose outcome could not be written
        (the row went terminal, or moved under the compare-and-set) still has to
        accumulate — otherwise the receipt under-reports the bill. The write here
        is additive and best-effort: if it fails, the ledger is still charged (the
        spend is real) and the row is left as it was, which is the visible form of
        the loss rather than a silent one.

        ⛔ THE ACCUMULATION IS ADDITIVE IN THE STORE (#14415 review round 5, P3).
        It used to be a read-modify-write — `item.tokens_in = row.tokens_in + d`
        then an unconditional `put_item` — and the paragraph that stood here
        called that unreachable "from today's call sites". THIS function is a call
        site: it writes tokens with NO state change and never goes through
        `advance`, so the state compare that serialises the settle path does not
        serialise it, and two replicas that both lost their compare could each
        read the same total and write it back, losing one accumulation and
        leaving `tokens_spent` below the ledger — the bill/receipt disagreement
        round 8 closed. `add_item_tokens` does the addition WHERE THE ROW LIVES.
        """
        tokens_in = max(0, int(tokens_in or 0))
        tokens_out = max(0, int(tokens_out or 0))
        item.charge_in = tokens_in
        item.charge_out = tokens_out
        if not (tokens_in or tokens_out):
            return item
        item.updated_at = self._now()
        try:
            settled = self._store.add_item_tokens(
                org_id, item.batch_id, item.item_id,
                tokens_in=tokens_in, tokens_out=tokens_out,
                updated_at=item.updated_at)
            if settled is not None:
                item.tokens_in = max(0, settled.tokens_in)
                item.tokens_out = max(0, settled.tokens_out)
        except Exception:
            _logger.warning("batch spend not recorded on item %s (team %s): the "
                            "ledger is charged, the row's total is short",
                            item.item_id, org_id, exc_info=True)
        return item

    def _row_or(self, org_id: str, item: BatchItem) -> BatchItem:
        """Read the row; fall back to `item` if the read itself fails.

        ⛔ A FAILED READ MUST NOT LOSE THE SPEND (#14415 review round 10, P1).
        `_bill` needs SOME row to bill against, and the caller's copy is a
        perfectly good one: the alternative — letting the read raise after the
        runner already spent — is what dropped a real spend from the ledger and
        left the row claimable again.
        """
        try:
            return self._fresh(org_id, item)
        except Exception:  # pragma: no cover - the fallback IS the point
            return item

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
            #
            # ⛔ AND THE ROWS ARE RE-READ (#14415 review round 11, P2). `items`
            # is the snapshot taken above; `_bill` writes tokens with
            # `add_item_tokens`, which changes NO compared field, so a peer's
            # accumulation can land on a row this call already read as terminal —
            # and `status_body()` then summed the snapshot, reporting
            # `tokens_spent: 0` beside a ledger that was charged. Both arms that
            # return without writing re-read for the same reason the `not stopped`
            # arm does.
            return batch, self._store.items(org_id, batch_id)
        if items and not any(not item.terminal for item in items):
            # ⛔ THE GUARD IS ON REMAINING WORK, NOT ON THE BATCH ROW (#14415
            # review round 2, B2). The row can LAG its items — `settle()`'s write
            # timing out, or a process killed between the last item write and the
            # settle — and then the row reads `accepted` over items that are all
            # `indexed` and charged. Keying only on ``batch.state`` relabelled
            # exactly that record `cancelled`: B3's wrong outcome, reached without
            # any injected fault. There is nothing left to stop, so DERIVE the
            # truth instead of inventing a cancel over it.
            return self.settle(org_id, batch_id), self._store.items(org_id,
                                                                    batch_id)
        stamp = self._now()
        # ⛔ THE `queued` CLASS IS STOPPED BY ONE STATEMENT, NOT ONE PER ITEM
        # (#14415 review round 7, P2). The loop below did `get_item` +
        # `put_item_if_state` per item — ~2 000 sequential round trips for the
        # documented maximum batch, inside one request, on a two-worker executor:
        # a 504 over a cancel that kept writing, with the customer's next status
        # read queued behind it. The SET still matches `state = queued` only, so a
        # claim taken after this call read the batch is NOT overwritten — the
        # compare-and-set property the per-item write had, without the round trip.
        stopped = self._store.cancel_queued_items(
            org_id, batch_id, reason=(reason or "cancelled by the customer"),
            updated_at=stamp) > 0
        for item in items:
            if item.state is BatchItemState.QUEUED:
                # Already stopped above by the set-based write; only a row that
                # moved on between the read and that write is left, and it is not
                # ours to relabel.
                continue
            if (item.state in (BatchItemState.EXTRACTING,
                                 BatchItemState.EMBEDDED)
                  # ⛔ THE TEST IS THE CLAIM'S AGE, NOT ITS ATTEMPT COUNT (#14415
                  # review round 5, BLOCKING 1). `attempts == 0` is UNREACHABLE in
                  # production: every writer of `extracting` increments it (the
                  # claim at `run_item`, and the re-claim), and the resume path
                  # keeps the counter. So the arm the docstring documents — a dead
                  # claim, "not claimed by any live runner", stopped for free —
                  # never ran: a batch whose only non-terminal item was a dead
                  # claim fell through to `stopped = False`, was reported
                  # `already finished`, and the next status poll's `recover()`
                  # requeued and RAN the document, charging tokens AFTER the
                  # customer cancelled. Staleness is what makes a claim dead.
                  and _age_seconds(item.updated_at)
                  >= float(BATCH_RECLAIM_AFTER_S)):
                # Claimed by nobody LIVE: the process that held it is gone, so no
                # runner is spending tokens on it and it can be stopped for free.
                moved = self.advance(org_id, item, BatchItemState.QUEUED,
                                     expected=item.state,
                                     expected_attempts=item.attempts)
                if moved.state is BatchItemState.QUEUED:
                    halted = self.advance(org_id, moved,
                                          BatchItemState.CANCELLED,
                                          reason=(reason or
                                                  "cancelled by the customer"),
                                          expected=BatchItemState.QUEUED)
                    # ⛔ ONLY A ROW THAT ACTUALLY BECAME `cancelled` IS A STOP
                    # (#14415 review round 5, P2). This read `is not QUEUED`,
                    # which counted a LOST compare whose row came back
                    # `extracting`/`indexed` as "we stopped something" — so the
                    # batch was stamped `cancelled` with an invented
                    # `cancelled_at` over work that was still running, and the
                    # route rendered "will not run and is not charged" beside an
                    # item that then ran and was charged.
                    stopped = stopped or halted.state is BatchItemState.CANCELLED
        if not stopped:
            # ⛔ NOTHING WAS ACTUALLY STOPPED, SO DO NOT RELABEL THE BATCH (#14415
            # review round 4, BLOCKING 1 direction B). Every CAS above lost,
            # which means the items moved on between this call's read and its
            # write — a drain claimed them, and they are being worked right now
            # (or are already settled). Stamping `cancelled` then is exactly the
            # lie B3 forbade, reached through a narrower window: the customer
            # would read "work stopped, not charged" over rows that are
            # `indexed` and charged. Derive the truth from the rows instead.
            # ⛔ THE ROWS, RE-READ (#14415 review round 10, P1). `items` is the
            # snapshot taken BEFORE the set-based write, so returning it reported a
            # row as `queued` after a drain had settled it `indexed` with 60 000
            # tokens: measured `status: completed`, `complete: true`,
            # `tokens_spent: 0`, `items[0].status: "queued"` and `counts.pending:
            # 1` — payload and route copy both asserting nothing was charged, over a
            # charged document, with `complete` beside `pending`.
            return self.settle(org_id, batch_id), self._store.items(org_id,
                                                                     batch_id)
        batch.state = BatchState.CANCELLED
        batch.updated_at = stamp
        batch.cancelled_at = stamp
        batch.cancel_reason = reason or "cancelled by the customer"
        # ⛔ THE LAST WRITER OF THIS ROW IS A COMPARE-AND-SET TOO (#14415 review
        # round 9, P2). It was the one remaining unconditional `put_batch` —
        # `MERGE … SET`, no state compare — so a `settle()` that derived a
        # TERMINAL state inside this call's read→write window was erased for good:
        # measured, a batch with one `queued` item and one `extracting` item had
        # its cancel stop the queued one, the drain then settled the other
        # `indexed` with 60 000 tokens and `settle()` wrote `partial` — and this
        # write stamped `cancelled` with an INVENTED `cancelled_at` over a charged
        # document. Nothing repairs that: `cancelled` is terminal,
        # `pending_batches()` drops the row, and a later `settle()` returns it
        # unchanged. A lost compare returns the ROW, which is the truth.
        if not self._store.put_batch_if_state(
                batch, (BatchState.ACCEPTED, BatchState.RUNNING,
                        BatchState.RESUMING)):
            return (self._store.get_batch(org_id, batch_id) or batch,
                    self._store.items(org_id, batch_id))
        return batch, self._store.items(org_id, batch_id)

    # -- resume (brief item 4) -------------------------------------------
    def abandon_dead_claims(
            self, org_id: str, batch_id: str, *,
            reclaim_after: float | None = None) -> None:
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

        ⛔ BUT "VOID" IS THE CLAIM'S AGE, NOT OUR HOPE (#14415 review round 9,
        HIGH). This method had NO age check, so it released a claim that was
        SECONDS old: `_resume_batch` is not a boot-only hook — `recover()` is a
        throttled re-scan re-entered from every batch read — so on multi-replica
        deployment replica B settled replica A's LIVE in-flight row `cancelled`
        (measured: claim age 0.0 s against a 900 s window, released and settled).
        The runner's outcome then could not be applied (`queued`/`indexed` is not
        reachable from `cancelled`), so `run_item`'s terminal arm BILLED the
        spend and dropped the document — the customer charged for work the cancel
        copy promised to keep. A fresh claim is left alone, exactly as `run_item`
        and `_resume_batch` do; the re-scan returns and settles it once it is
        stale, which is the ONE rule this file states.

        ⛔ THE WINDOW APPLIES ON **EVERY** ARM, INCLUDING "BOOT" (#14415 review
        round 9, HIGH, then round 10 HIGH). The first cut of this fix let a
        caller pass ``None`` for "nothing I know of is running" — and the caller
        used it on a tenant's FIRST scan in the process. That premise is false on
        a rolling deploy: the first scan of the NEW replica runs while the OLD
        replica is still inside the runner (measured: claim age 0.00 s, settled,
        the peer's `indexed` became unreachable, its 730 tokens billed and the
        document dropped — the exact defect the arm was added to close). "First
        scan" is not "boot" either: `_BATCH_SCAN` evicts, and an evicted tenant
        re-enters that arm mid-life. So the window is not optional here: a FRESH
        claim is settled by NO caller that has not PROVED the holder is gone, and
        it is settled by a later scan once it is stale — which is the ONE rule this file states, with no
        exception left to get wrong.
        """
        # ⛔ RESOLVED AT CALL TIME, not bound at import (review round 11, LOW):
        # the default used to capture `BATCH_RECLAIM_AFTER_S`'s value when the
        # module loaded, so a test or an operator that narrowed the knob moved the
        # sibling arms and left THIS one at 900 s — the one place where the "ONE
        # rule" was spelled two different ways. `recover()`'s `stale_after_s`
        # already uses this sentinel.
        window = float(BATCH_RECLAIM_AFTER_S if reclaim_after is None
                       else reclaim_after)
        for item in self._store.items(org_id, batch_id):
            if item.state in (BatchItemState.EXTRACTING,
                              BatchItemState.EMBEDDED):
                if _age_seconds(item.updated_at) < window:
                    continue
                released = self.advance(org_id, item, BatchItemState.QUEUED,
                                        reason="cancelled batch, claim released",
                                        expected=item.state,
                                        expected_attempts=item.attempts)
                if released.state is BatchItemState.QUEUED:
                    self.advance(
                        org_id, released, BatchItemState.CANCELLED,
                        reason="batch was cancelled before this item completed",
                        expected=BatchItemState.QUEUED)
            elif item.state is BatchItemState.QUEUED:
                self.advance(org_id, item, BatchItemState.CANCELLED,
                             reason="batch was cancelled")

    def recover(self, org_id: str, *,
                live_batches: Collection[str] | Callable[[str], bool] = (),
                stale_after_s: float | None = None,
                limit: int = 50, offset: int = 0) -> list[str]:
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
        reclaim_after = (float(BATCH_RECLAIM_AFTER_S) if stale_after_s is None
                         else float(stale_after_s))
        # ⛔ TWO independent bounds, and both are needed.
        #
        # `is_live` answers "is a drain in THIS process working this batch right
        # now" — the in-process claim (#14415 review B1). The staleness window
        # answers the question `is_live` cannot: "is the claim on this ROW still
        # young enough that some OTHER process may hold it" (#14219 review
        # round 2, BLOCKING 5). Without the window, any multi-instance
        # deployment has a deterministic path from a tenant's first STATUS POLL
        # to two concurrent extractions of the same document (the file's own
        # isolation evidence measured a second instance producing a 1,505 ms DB
        # probe timeout and 9/12 failed probes). 15 minutes is ~16x the measured
        # per-document wall clock (#14295, 56.25 s).
        #
        # ⚠️ DECLARED RESIDUAL: a single item that legitimately runs longer than
        # the window can be re-claimed. A real lease (a heartbeat on the row)
        # fixes that and needs the drain to refresh it mid-item.
        #
        # ⛔ BOUNDED PER CALL (#14219 review round 2, NB8): this runs on the
        # tenant's first batch touch, from a request, on the 2-worker bookkeeping
        # pool — a tenant with hundreds of unfinished batches must not turn a
        # `GET /v1/batches` into an unbounded scan of all of them.
        #
        # ⛔ AND ROTATING, because a prefix is a STRANDING (#14415 review round 4,
        # BLOCKING 2): `pending_batches()` order is stable, so a plain
        # `[:limit]` re-scanned the same first 50 on every call and batches 51+
        # were NEVER reached by any scan in the process's life — non-terminal for
        # ever, which is the lost-document class the issue forbids. The caller
        # advances `offset` per scan and wraps, so every unfinished batch is
        # reached within `ceil(n / limit)` scans.
        # ⛔ SORTED HERE, for BOTH stores (#14415 review round 8, BLOCKING 1).
        # `GraphBatchStore` sorts its own result (Cypher ignores `ORDER BY` after
        # a `UNION`), and the in-memory store returns insertion order — sorting at
        # the one place that consumes the list makes the rotation's fairness
        # independent of the store.
        pending = sorted(self._store.pending_batches(org_id))
        width = max(1, int(limit))
        start = int(offset) % len(pending) if pending else 0
        # Rotate, then take a window: rotating first means the wrap cannot
        # repeat a batch (a plain slice-plus-prefix appended the head twice, so
        # one batch was reported resumed twice in a single call).
        rotated = pending[start:] + pending[:start]
        for batch_id in rotated[:width]:
            # ⛔ ONE BATCH'S FAULT MUST NOT ABORT THE SCAN (#14415 review round 8,
            # NB3). A row whose own read raises (`get_batch`/`items` on a corrupt
            # or vanished row — `settle()`'s `KeyError`, a malformed
            # `estimate_json`) used to propagate out of `recover()`, so the
            # caller lost the whole window and the failure throttle was written
            # against an offset that never moved: that batch was retried for ever
            # and every batch behind it was never inspected at all. The scan goes
            # on; the fault is logged and the next scan covers it again.
            if is_live(batch_id):
                # A drain in THIS process owns this batch's claim; requeueing it
                # would destroy the outcome of work that is still running.
                continue
            try:
                moved = self._resume_batch(org_id, batch_id,
                                           reclaim_after=reclaim_after,
                                           stamp=stamp)
            except Exception:
                _logger.exception("batch %s: recovery of this batch failed; "
                                  "continuing the scan", batch_id)
                continue
            if moved is not None:
                resumed.append(batch_id)
        return resumed

    def _resume_batch(self, org_id: str, batch_id: str, *,
                      reclaim_after: float, stamp: str) -> bool | None:
        """Re-open ONE batch. Returns ``True`` when reported, ``None`` when not.

        Split out of :meth:`recover` so ONE batch's read fault cannot abort the
        tenant's whole scan (see the caller's note).
        """
        batch = self._store.get_batch(org_id, batch_id)
        if batch is None:
            # ⛔ ORPHAN ROWS, not an inconsistency (#14219 review). Acceptance
            # writes the items BEFORE the batch row (so an ACK never names a
            # document we did not record); a crash between the two leaves items
            # with no batch. Skipping them would strand those rows on every boot,
            # invisible on every surface. Adopt them: the items ARE the record of
            # accepted work.
            orphan_items = self._store.items(org_id, batch_id)
            if not orphan_items:
                return None
            # ⛔ RE-READ BEFORE ADOPTING (#14415 recut review, P2). The orphan shape
            # is not only a crash residue: `submit()` writes the item rows BEFORE
            # the batch row on purpose, and `pending_batches()` UNIONs the ITEM arm —
            # so a recovery scan running while an acceptance is inside its item loop
            # sees this exact shape and read `get_batch -> None` a moment ago. The
            # adoption then called `put_batch`, an UNCONDITIONAL `MERGE ... SET`
            # over `estimate_json`, `corpus_name`, `cancelled_at`: it wiped the
            # estimate the ACK is required to carry (owner §4) and relabelled
            # `accepted` as `resuming` over work that never stopped. The other three
            # writers of this row were each hardened to a compare-and-set; this one
            # is hardened by not writing over a row that has since appeared.
            #
            batch = self._store.get_batch(org_id, batch_id)
            if batch is None:
                # ⛔ THE ADOPTION IS A CREATE-IF-ABSENT, NOT A WRITE (#14415 review
                # round 11, P2). The re-read above narrows the window to two
                # statements; it does not close it, and the write that closed it
                # was a blind `put_batch` (`MERGE … SET` of state, created_at,
                # estimate_json and corpus_name) — so an acceptance landing in
                # between had its estimate and corpus BLANKED (owner §4's
                # arithmetic gone from the record) and the row relabelled
                # `resuming` over work that never stopped. `MERGE … ON CREATE SET`
                # cannot touch an existing row, and the verdict tells this thread
                # whether to trust its own object.
                adopted = Batch(batch_id=batch_id, org_id=org_id,
                                state=BatchState.RESUMING,
                                created_at=orphan_items[0].updated_at,
                                updated_at=orphan_items[0].updated_at)
                if not self._store.put_batch_if_absent(adopted):
                    # The acceptance won the race: its row is the record.
                    batch = self._store.get_batch(org_id, batch_id) or adopted
                else:
                    batch = adopted
        if batch.state is BatchState.CANCELLED:
            # A cancelled batch is NOT resumed: the customer stopped it, and a
            # restart is not a reason to spend their allowance. But an item left
            # non-terminal by a crash AFTER the cancel must not sit non-terminal
            # for ever (it would be re-listed as pending on every boot and never
            # resolved — an inert row). At boot nothing is running, so its claim
            # is void: settle it ``cancelled``, without inventing token counts for
            # a run we cannot observe.
            self.abandon_dead_claims(org_id, batch_id,
                                     reclaim_after=reclaim_after)
            return None
        # ⛔ The `resuming` write below is a compare-and-set against the states a
        # resume may legally start from (review round 5, P1 — the third
        # unconditional writer of the batch row, and the one a status poll
        # reaches), so a cancel landing in this loop is never erased.
        items = self._store.items(org_id, batch_id)
        moved = False
        for item in items:
            if item.state in (BatchItemState.EXTRACTING,
                              BatchItemState.EMBEDDED):
                if _age_seconds(item.updated_at) < reclaim_after:
                    # A LIVE claim: not ours to TAKE — but the batch is still
                    # ours to REPORT (#14415 review round 5, BLOCKING 1). Skipping
                    # the whole batch here discarded the decision instead of
                    # deferring it: an item in flight when the process died is
                    # SECONDS old at the restart that recovers it, so the ordinary
                    # redeploy stranded it for the process lifetime. The drain
                    # re-checks the window itself (`run_item`) and leaves a fresh
                    # claim alone, so listing the batch is safe.
                    continue
                # ⛔ The requeue carries the CLAIM IDENTITY too (review round
                # 5): a peer that re-claimed this row between our read and this
                # write leaves the row at a new `attempts`, so a state-only
                # compare would requeue work somebody is inside the runner call
                # on. And `moved` follows the WRITE, not the call: a lost compare
                # must not report the batch as resumed.
                requeued = self.advance(
                    org_id, item, BatchItemState.QUEUED,
                    reason="resumed after a process restart — "
                           "requeued for a fresh attempt",
                    expected=item.state,
                    expected_attempts=item.attempts)
                moved = moved or requeued.state is BatchItemState.QUEUED
        if moved or any(not i.terminal for i in items):
            # ⛔ BUT ONLY RELABEL A BATCH THIS SCAN GAVE WORK TO (#14415 review
            # round 7, N7). A batch whose only non-terminal item is a FRESH claim
            # may be running in a PEER process right now; writing `resuming` over
            # its row made the customer's status surface say "resuming" over work
            # in progress, until the peer's own `settle()` repaired it — hours for
            # a 500-item batch. Return it (a later pass must revisit it) without
            # touching its state.
            # ⛔ `moved` ALONE (#14415 review round 13, LOW). The `or any(QUEUED)`
            # arm relabelled a batch that had NEVER RUN: a freshly accepted batch
            # (every item `queued`, `attempts=0`, nothing claimed) was written
            # `resuming` by the first scan, which is the status surface telling
            # the customer work is being RECOVERED when work has simply not
            # started — measured on the wire: `POST` → `accepted`, then the first
            # `GET` → `resuming`. `RESUMING`'s whole job is "you can tell whether
            # work was lost"; it must not be minted by a scan that resumed
            # nothing. The batch is still REPORTED below (so a later pass visits
            # it), and its `pending` count already says there is work.
            if moved:
                # ⛔ THE RESUME WRITE IS A COMPARE-AND-SET TOO (#14415 review
                # round 5, P1). This is the THIRD unconditional writer of the
                # batch row, and it is the one a status poll reaches: `batch` was
                # read before `items()`, so a `cancel()` landing in that gap was
                # overwritten with `resuming` + EMPTY `cancelled_at`/
                # `cancel_reason`. That also DEFEATS `settle()`'s own fix one
                # window later: `RESUMING` is in the terminal compare's expected
                # set, so the derived `partial`/`completed` then lands, the row
                # stops being selected by `pending_batches()`, and the customer's
                # cancellation is gone for good (measured: cancel → `resuming` →
                # `partial`, nothing left to repair it).
                batch.state = BatchState.RESUMING
                batch.updated_at = stamp
                if not self._store.put_batch_if_state(
                        batch, (BatchState.ACCEPTED, BatchState.RUNNING,
                                BatchState.RESUMING)):
                    # The row moved on (a cancel, or a peer's own settle). Leave
                    # it alone: `_drain_batch` over a cancelled batch settles it.
                    return True
            return True
        if items:
            # ⛔ A LAGGING BATCH ROW IS REPAIRED HERE (#14415 review round 2, B1
            # arm 2 / B2's root). Every item is terminal but the batch row is not:
            # `settle()`'s own write timed out, or the process died between the
            # last item write and the settle. Nothing else ever re-derived it.
            self.settle(org_id, batch_id)
        return None

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
            # ⛔ `resuming` is a TRANSIENT label, not a second `running`
            # (#14415 review round 4, NB11). It used to persist for the whole
            # life of a resumed batch, because nothing but the terminal write
            # could move it — so the status surface said "resuming" over a batch
            # that had been working for an hour. Once a worker has claimed an
            # item (or any item has settled), the resume is OVER.
            started = any(i.state in (BatchItemState.EXTRACTING,
                                      BatchItemState.EMBEDDED) or i.terminal
                          for i in items)
            if batch.state is not BatchState.RUNNING and (
                    batch.state is not BatchState.RESUMING or started):
                observed = batch.state
                batch.state = BatchState.RUNNING
                batch.updated_at = self._now()
                # ⛔ EVEN THE NON-TERMINAL WRITE IS A COMPARE-AND-SET (#14415
                # review round 5, P1). `batch` was read before `items()`, so a
                # `cancel()` landing in that gap used to be overwritten by
                # `running` WITH EMPTY `cancelled_at`/`cancel_reason` — and
                # `recover()` then no longer recognised the batch as cancelled
                # (`abandon_dead_claims` is skipped only for a row that says so),
                # so the cancelled document was re-driven and BILLED. Measured:
                # cancel recorded → settle wrote `running` with no evidence →
                # `recover()` resumed it → runner ran → 60 000 tokens. The
                # terminal write below was hardened first; this one is the same
                # window one line earlier.
                if not self._store.put_batch_if_state(batch, (observed,
                                                              BatchState.RUNNING,
                                                              BatchState.RESUMING)):
                    return self._store.get_batch(batch.org_id,
                                                 batch.batch_id) or batch
            return batch
        # ⛔ THE TERMINAL WRITE IS A COMPARE-AND-SET (#14415 review round 5, P2).
        # `batch` above was read BEFORE `items()`, so a `cancel()` landing in
        # between wrote CANCELLED and this write overwrote it with `partial` —
        # erasing `cancelled_at`/`cancel_reason` on a billing-visible surface AND
        # hiding the row from `pending_batches()`, so nothing ever repaired it.
        # A cancel is never overwritten by a derived state.
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
            # ⛔ Same window, same rule (#14415 review round 5, P1): a `cancel()`
            # that landed between the row read and here already wrote its own
            # `cancelled_at`/`cancel_reason`, and this derived write would replace
            # them with a guessed stamp.
            #
            # ⛔ SO `CANCELLED` IS **NOT** IN THE EXPECTED SET (#14415 review
            # round 7, P2). It was, and that made the compare MATCH the row the
            # cancel had just written: `settle()` read the batch as `running`,
            # the cancel landed its item writes and its own `put_batch`
            # (`cancelled_at = T`, `cancel_reason = "cancelled by the customer"`),
            # and this derived write then overwrote both with `self._now()` and
            # the first item's reason. `cancelled_at` is documented as "the audit
            # half of a billing-visible act", so the field that says WHO asked and
            # WHEN was being rewritten by a derivation. A row already `cancelled`
            # needs no derivation — its cancel landed, which is the whole point of
            # this branch — so the compare excludes it and a lost compare returns
            # the row untouched.
            if not self._store.put_batch_if_state(
                    batch, (BatchState.ACCEPTED, BatchState.RUNNING,
                            BatchState.RESUMING)):
                return self._store.get_batch(batch.org_id, batch.batch_id) or batch
            return batch
        derived = (BatchState.COMPLETED if ok == len(items)
                   else BatchState.PARTIAL)
        batch.state = derived
        batch.updated_at = self._now()
        # ⛔ A DERIVED TERMINAL STATE MAY NOT OVERWRITE A CUSTOMER CANCEL
        # (#14415 review round 5, P2): the row read at the top of this method may
        # predate a `cancel()` that landed while `items()` was being read, and
        # `partial`/`completed` is terminal — `pending_batches()` would no longer
        # select the row, so nothing would ever repair it and the customer's
        # `cancelled_at`/`cancel_reason` would be gone. On a lost compare the ROW
        # is returned, unmodified.
        # `CANCELLED` is deliberately NOT in the expected set: a cancel that
        # landed in the window is exactly what this compare exists to respect.
        if self._store.put_batch_if_state(
                batch,
                (BatchState.ACCEPTED, BatchState.RUNNING, BatchState.RESUMING,
                 derived)):
            return batch
        return self._store.get_batch(batch.org_id, batch.batch_id) or batch


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
    | ``failed`` + ``retryable`` | ``queued`` (retry; a classified DB write failure) |
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
    # ⛔ A RETRYABLE `failed` IS NOT A TERMINAL FAILURE (#14415 review round 8,
    # P2). `retryable` was consulted ONLY on the `skipped` arm, but the indexer's
    # other retryable shape is `{"status": "failed", "retryable": True,
    # "db_failure": True, "db_reason": …}` (`sdk.py::_index_process_unit` on a
    # failure `_classify_db_failure` recognises — the class `_INDEX_DB_RETRY_BUDGET`
    # exists for). It fell through HERE: a transient graph abort settled the item
    # `failed` for ever, `BATCH_MAX_ATTEMPTS` never applied, the document was
    # silently not ingested, and the batch reported `partial`. The reason was
    # "the indexer reported a failure" too, because that shape carries `error` and
    # `db_reason` and no `reason` key — so the table's own `failed → failed with
    # the indexer's reason` row was false for exactly it. `QUEUED` is bounded by
    # the attempt ceiling, which is what a retryable shape wants.
    if per_file.get("retryable"):
        db_reason = str(per_file.get("db_reason") or "")
        detail = ""
        error = per_file.get("error")
        if isinstance(error, Mapping):
            detail = str(error.get("error") or "")
        return ItemOutcome(
            BatchItemState.QUEUED,
            reason=(reason or detail or db_reason
                    or "the indexer reported a retryable failure"))
    return ItemOutcome(BatchItemState.FAILED,
                       reason=reason or "the indexer reported a failure")


def _discard_staged(stage_base: str | None, batch_id: str) -> None:
    """Remove a rejected batch's staged files. Best-effort, never raises.

    ⛔ Refuses to act at all when the id does not resolve inside the staging
    root (:func:`_batch_staging_dir`). This is the destructive half of the
    measurement there: with ``batch_id="../../victim"`` the old body unlinked
    every file in an UNRELATED directory and then removed it, so a refusal path
    (the rollback in :meth:`BatchCoordinator.submit`) could delete data it had
    never written. A best-effort cleanup must be best-effort about ITS OWN
    artefacts only.
    """
    if not stage_base:
        return
    import os

    target = _batch_staging_dir(stage_base, batch_id)
    if target is None:
        return
    with contextlib.suppress(OSError):
        if os.path.isdir(target):
            for child in os.listdir(target):
                os.unlink(os.path.join(target, child))
            os.rmdir(target)


def _age_seconds(stamp: str) -> float:
    """Seconds since an ISO stamp. An unparseable stamp counts as ANCIENT.

    Ancient is the safe direction here: a row whose claim time cannot be read is
    one whose liveness cannot be argued, and it is already non-terminal — leaving
    it unsettled for ever is worse than retrying it.
    """
    from datetime import UTC, datetime
    try:
        then = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    delta = (datetime.now(UTC) - then).total_seconds()
    if delta < 0:
        # ⛔ A FUTURE STAMP IS FRESH ONLY WITHIN ONE RECLAIM WINDOW (#14415 review
        # round 8, NB1 — measured both ways). The two failure modes are opposite:
        #
        # * `max(0.0, delta)` (the original) read a stamp months in the future as
        #   age 0 — permanently "someone is working on it": never re-claimed, the
        #   retry ceiling never incremented, the document non-terminal for ever.
        # * a 60 s cliff read 61 s of ORDINARY two-box clock skew as ANCIENT and
        #   re-ran the runner on a claim that was seconds old — a second
        #   extraction, a second graph write, a second charge.
        #
        # So: within one reclaim window it is just clock skew (age 0, i.e. FRESH —
        # the safe direction, nobody is robbed); beyond that the stamp is not a
        # clock, it is corruption or a huge jump, and it is treated as ANCIENT
        # because the outcome of believing it is a lost document.
        return 0.0 if (-delta) <= float(BATCH_RECLAIM_AFTER_S) else float("inf")
    return delta


def _utc_now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def elapsed_ms(start: float) -> int:
    """Milliseconds since a ``time.monotonic()`` reading.

    Kept because the ACK bound is a NUMBER, not a slogan — but ⚠️ nothing on the
    route measures it yet (#14219 review round 2, NB10): the 10 s transport bound
    (owner `#3834`) is satisfied *by construction* (acceptance writes rows and
    returns; nothing waits), and there is no instrument asserting the elapsed time
    of a 1000-item acceptance. That measurement is a declared residual, not a
    claim this module can make.
    """
    return int((time.monotonic() - start) * 1000)
