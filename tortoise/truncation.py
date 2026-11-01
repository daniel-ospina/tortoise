"""The TRUNCATION marker — one home for "this result was CUT", #8447.

The contract this module exists to enforce:

    A bounded operation must either return complete results, or mark the result
    as INCOMPLETE. It must never return a silently truncated result that is
    indistinguishable from a complete one.

⛔ **THIS IS ITS OWN FIELD, NEVER A FIFTH STATUS TERM.** The four recorded
read terms (``available`` / ``empty`` / ``degraded`` / ``unconfigured``,
``tortoise/status_vocabulary.py``) answer *"did the store answer, and did it
have anything?"*. A truncated read answered, and had plenty — it just was not
all delivered. ``status`` is therefore reported ``available`` on a truncated
read, correctly, and truncation rides here. Same rule the completeness marker
follows (``tortoise/residency.py``, #7952 §17.2: "its OWN field, never a fifth
term of the status vocabulary"), which forbids one, so no term is minted.

**Why the marker is not flag-gated.** ``status`` and ``residency`` are opt-in
because they cost something (a classified trace; a bounded store probe). This
one costs two ``len()`` calls over lists the read already holds — so gating it
OFF by default would preserve exactly the defect it fixes: a caller who did not
know to opt in would still be served a silent subset. It is always emitted.
``OVERRIDES:`` the #1745 fail-safe convention (a new read lever ships OFF until
a measured delta adopts it) — the lever here is a marker on an existing
response, not a change to what is retrieved, so there is no retrieval delta to
measure and a default-OFF marker is the bug. Precedent: #8487's recorded
``OVERRIDES`` on ``_valid_time_rerank``.

**The two fields, and the three states a caller can be in.**

    truncated=False                    -> COMPLETE. Nothing was dropped.
    truncated=True  + total_available  -> INCOMPLETE, and the exact number of
                                          matches this read could see.
    truncated=True  (no total_available) -> INCOMPLETE, exact total UNKNOWN.

``total_available`` is emitted ONLY when it is EXACT, and its absence is
load-bearing: the candidate pool behind a read is itself bounded
(``pool_size`` / ``TORTOISE_POOL_FLOOR``), so a count taken from it is a LOWER
BOUND whenever a retrieval leg filled its own window. A lower bound rendered as
a total is the same defect one level down — the caller cannot tell "there are
exactly 120" from "at least 120". This is the converging practice read two
ways: Elasticsearch/OpenSearch carry ``hits.total.relation`` = ``eq`` | ``gte``
to say which one a total is, and Google's Custom Search API documents its
``totalResults`` as an *estimate*. Where those APIs attach a relation, this one
omits the field, so a number that IS present is always exact and needs no
qualifier.

**What ``truncated: true`` does NOT mean: "only the cap".** A row can be lost at
the cap (``result_ids[:limit]``) or downstream of it (a ``min_confidence``
floor, a hit whose entity read came back empty) — and the caller cannot reach
EITHER: the dropped row is unreachable by any subsequent call, which is the
whole reason the marker exists. So the verdict is computed from the rows that
actually SHIP, and it answers *"is the delivered set the whole set this read
matched?"*, not *"did the cap fire?"*. Narrowing it to the cap would make a
3-match read whose entity fetch failed answer ``truncated: false`` over 2 rows —
a positive claim of completeness on an incomplete answer, the defect this module
exists to make impossible. This is the same distinction Elasticsearch draws by
signalling partial results at all (``allow_partial_search_results``,
``timed_out`` / ``_shards.failed``): a partial answer must be MARKED, and the
marker is not qualified by which stage dropped the row. ``returned`` is
correspondingly measured at delivery, never at the cut — the two only differ
when something downstream dropped a row, and that is exactly the case the
marker must not read as complete.

**How this is told apart from legitimate pagination.** A caller walking pages is
NOT truncating, and marking every paged response would train readers to ignore
the marker. The distinguishing property is not the shape of the response, it is
whether the dropped rows are REACHABLE: the offset/cursor paths
(``TortoiseSDK.paginated_query`` -> ``{results, total, hasMore}``, the hosted
cursor walks) already report ``total``/``hasMore`` and hand the caller the next
page, so they are never marked here. The limit-capped search path has no
offset and no cursor — a row dropped at ``result_ids[:limit]`` is unreachable
by any subsequent call — so that is the only place the marker is written.
"""
from __future__ import annotations

#: The response field naming incompleteness. Always present on a marked read.
TRUNCATED_FIELD = "truncated"

#: The response field naming the EXACT number of matches the read could see.
#: Present ONLY when it is exact — absence means "unknown", never "zero".
TOTAL_AVAILABLE_FIELD = "total_available"


def mark_truncation(sink: dict | None, *, returned: int,
                    candidates: int, exact: bool = False,
                    incomplete: bool = False) -> None:
    """Record whether a read DROPPED rows at its cap, on the caller's sink.

    ``candidates``  rows the read held after every filter and BEFORE the
                    ``limit`` cut — the honest population the cap was applied
                    to.
    ``returned``    rows actually delivered.
    ``exact``       the producer PROVED the candidate set is the whole match
                    population (no retrieval leg filled its own window). The
                    caller must pass True only on that evidence; the default
                    is the conservative answer, never the flattering one.
    ``incomplete``  the producer proved the answer is incomplete EVEN THOUGH
                    nothing was cut from the visible set — the case where a
                    retrieval leg filled its own window and a filter then
                    shrank the result below ``limit``. Without this, a
                    saturated-then-filtered read reports COMPLETE and the
                    never-read matches are unreachable: the same defect with
                    an extra step. Such a read takes the third state (unknown
                    total), never ``complete``.

    Writes ``truncated`` on EVERY call (so a complete read is positively
    marked complete, not merely un-marked) and ``total_available`` only when
    ``exact`` — see the module docstring for why a lower bound rendered as a
    total is the same defect one level down.
    """
    if sink is None:
        return
    truncated = (candidates > returned) or incomplete
    sink[TRUNCATED_FIELD] = truncated
    # Hygiene, not decoration: a sink is CALLER-OWNED and may be REUSED, so a
    # stale ``total_available`` from an earlier call would ride a later verdict —
    # a complete (or unprovable) answer advertising an exact total, which is the
    # defect one level down. Clear it unconditionally before the conditional set.
    sink.pop(TOTAL_AVAILABLE_FIELD, None)
    if truncated and exact:
        sink[TOTAL_AVAILABLE_FIELD] = candidates


def truncation_field(sink: dict | None) -> dict:
    """Read the marker back off a sink, refusing to guess when it is absent.

    A missing verdict is a BUG in the producer, not a state to paper over: a
    defaulted ``False`` here would report every read complete if the producer
    regressed, which is precisely the silent-completeness this module exists
    to make impossible. Fail loud instead (the ``read_status`` precedent: "The
    SDK sets ``status`` on every return path; a missing term is a bug, not a
    reason to emit null").
    """
    if not sink or TRUNCATED_FIELD not in sink:
        raise RuntimeError(
            "truncation marker missing from the read sink — the producing read "
            "path did not record whether it truncated, so completeness cannot "
            "be asserted (#8447)")
    out = {TRUNCATED_FIELD: sink[TRUNCATED_FIELD]}
    if TOTAL_AVAILABLE_FIELD in sink:
        out[TOTAL_AVAILABLE_FIELD] = sink[TOTAL_AVAILABLE_FIELD]
    return out
