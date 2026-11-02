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
one's own decision is two ``len()`` calls over lists the read already holds, and
it also asks the retrieval path for a per-leg trace when the caller supplied
none (``sdk.py`` allocates ``status_trace`` under the sink; ``degradation_chain``
gates its per-leg lists on ``trace_active``) — an append-only audit record, one
entry per asked leg, that never changes which rows are returned. That proof is
not optional: a leg that was ASKED and never answered drops rows exactly as a
cap does, and without the trace the marker cannot see it. So the cost is
bounded, not free — and gating the marker OFF by default would still preserve
exactly the defect it fixes: a caller who did not know to opt in would still be
served a silent subset. It is always emitted.
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
three seams, and the caller can reach NONE of them: at the cap
(``result_ids[:limit]``), downstream of it (a ``min_confidence`` floor, a hit
whose entity read came back empty), or before it — a retrieval leg that was
ASKED and never answered (it raised, missed the collective deadline, or was
refused by its own circuit breaker), so the rows it would have contributed were
never in the candidate set at all. Every one of those is unreachable by any
subsequent call, which is the whole reason the marker exists. So the verdict is
computed from the rows that actually SHIP, and it answers *"is the delivered set
the whole set this read matched?"*, not *"did the cap fire?"*. Narrowing it to
the cap would make a 3-match read whose entity fetch failed answer
``truncated: false`` over 2 rows — a positive claim of completeness on an
incomplete answer, the defect this module exists to make impossible. This is the
same distinction Elasticsearch draws by signalling partial results at all
(``allow_partial_search_results``, ``timed_out`` / ``_shards.failed``): a partial
answer must be MARKED, and the marker is not qualified by which stage dropped
the row. ``returned`` is correspondingly measured at delivery, never at the cut
— the two only differ when something downstream dropped a row, and that is
exactly the case the marker must not read as complete.

⛔ **A leg that was never ASKED is not one of those three seams.** With no
embedder installed, the dense leg has no question to ask: nothing this read
matched was dropped, and the capability gap belongs to ``status: degraded``
(the recorded vocabulary), not here. The two are deliberately kept apart —
marking every read of a keyword-only deployment ``truncated`` would make the
field meaningless and train callers to ignore it.

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

#: The recorded judgment on which leg verdicts prove the store ANSWERED —
#: imported, never re-declared, so the completeness proof cannot drift from the
#: read path's own vocabulary. ⚠️ IT ANSWERS A DIFFERENT QUESTION than the proof
#: below: ``read_status`` asks whether the store was REACHABLE, where
#: ``index_missing`` is legitimately an answer ("the server replied: no such
#: index"). The completeness proof asks whether a leg that was ASKED dropped
#: matches this read would otherwise have delivered, and for the LEXICAL leg
#: ``index_missing`` is the opposite answer — see
#: :data:`LEG_ANSWERED_REASONS`.
from .read_status import _ANSWERED_REASONS

#: The response field naming incompleteness. Always present on a marked read.
TRUNCATED_FIELD = "truncated"

#: Which verdicts prove an ASKED leg ANSWERED, keyed on ``(leg, reason)`` — the
#: keyed form is required because one reason means different things per leg.
#:
#: ``fts`` — ``index_missing`` is **NOT** an answer. `run_fts_query` raises the
#: fulltext index call, records ``index_missing`` and returns `[]`
#: (``search_engine.py:1741``). The rows exist and that index is the ONLY way to
#: match them, so they are unreachable by any follow-up call — exactly the class
#: this module forbids (see the three seams above). Publishing the survivors'
#: length as an exact total over that read is a lower bound wearing a total's
#: clothes: 20 Points matching a term, no fulltext index, a dense leg answering 3
#: -> ``total_available: 3``, 17 short.
#:
#: Every other leg keeps the recorded set. Their ``index_missing`` means
#: something else: the structural leg is a plain `MATCH` scan, so an index error
#: there means the label is absent (nothing to lose), and the dense leg records
#: ``index_missing`` only on its brute-force scan path — the vector index is not
#: the only route, and ``no_embeddings`` (no vectors exist) is an answer by
#: construction. No measurement covers narrowing those, so they are not narrowed
#: here.
LEG_ANSWERED_REASONS: dict[str, frozenset[str]] = {
    "fts": _ANSWERED_REASONS - {"index_missing"},
}

#: The fallback for a leg the keyed table above does not name.
DEFAULT_LEG_ANSWERED_REASONS: frozenset[str] = _ANSWERED_REASONS

#: The response field naming the EXACT number of matches the read could see.
#: Present ONLY when it is exact — absence means "unknown", never "zero".
TOTAL_AVAILABLE_FIELD = "total_available"

#: A PRIVATE relay: did this read PROVE the candidate population is the whole
#: match set? NOT a response field (``truncation_field`` never republishes it) —
#: it exists so a consumer ABOVE the read can relay the marker's own proof
#: instead of re-deriving it from ``truncated``, which is not equivalent:
#: ``truncated: False`` is also what a read reports when it COMPLETED but had to
#: WITHHOLD exactness (a post-window filter shrank the union, so the survivors
#: are no longer the whole population). A consumer that read ``not truncated``
#: as ``exact`` would publish that floor as a total — the same defect one level
#: up. Reading the recorded proof is the only sound route.
EXACT_KEY = "_exact"


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
                    nothing was cut from the visible set — a retrieval leg
                    filled its own window (so matches beyond it were never
                    read), or an ASKED leg never answered at all. Without
                    this, a saturated-then-filtered read, or a read whose
                    vector leg timed out while the sparse leg answered
                    little, reports COMPLETE and the never-read matches are
                    unreachable: the same defect with an extra step. Such a
                    read takes the third state (unknown total), never
                    ``complete``.

    Writes ``truncated`` on EVERY call (so a complete read is positively
    marked complete, not merely un-marked) and ``total_available`` only when
    ``exact`` — see the module docstring for why a lower bound rendered as a
    total is the same defect one level down.
    """
    if sink is None:
        return
    truncated = (candidates > returned) or incomplete
    sink[TRUNCATED_FIELD] = truncated
    # The exactness PROOF, recorded so a consumer relays it rather than
    # re-deriving it (see EXACT_KEY). A private key: ``truncation_field``
    # publishes only the two response fields, so this never reaches a caller.
    sink[EXACT_KEY] = bool(exact)
    # Hygiene, not decoration: a sink is CALLER-OWNED and may be REUSED, so a
    # stale ``total_available`` from an earlier call would ride a later verdict —
    # a complete (or unprovable) answer advertising an exact total, which is the
    # defect one level down. Clear it unconditionally before the conditional set.
    sink.pop(TOTAL_AVAILABLE_FIELD, None)
    if truncated and exact:
        sink[TOTAL_AVAILABLE_FIELD] = candidates


def lost_leg_owners(asked_legs: dict[str, bool], status_trace, *,
                    skip_from: int | None = None) -> set[str]:
    """The ASKED legs whose own verdict proves they never ANSWERED the read.

    A leg that was asked and never answered (it raised, missed the collective
    deadline, or was refused by its circuit breaker) contributed no rows at all,
    so the matches it would have found are unreachable — the third seam in this
    module's docstring. Key ABSENCE cannot prove that (a leg that ran and found
    nothing leaves no key either), which is why the trace, not the result dict,
    is the evidence.

    ``asked_legs``     ``{leg: was it asked?}``, from the SAME predicate the
                       retrieval chain uses to submit it. A leg that was never
                       asked is a declared capability gap, not a drop.
    ``status_trace``   the leg-trace entries recorded so far.
    ``skip_from``      ignore entries at or after this index. The trace carries
                       ONE slot per leg but the lexical slot can be written by a
                       SECOND scorer (the #8488 in-process BM25 arm, which
                       records under ``leg="fts"``); its entries are appended
                       after the chain returns, and its contract is *"the leg
                       DEGRADES, never fails a read"* — the shipped leg's rows
                       survive untouched, so its failure must not be judged as
                       the shipped leg's. Provenance (where the entry was
                       appended), not a marker key, is the discriminator: the
                       arm's ``breaker_open`` branch writes no ``mechanism`` and
                       no ``declined`` key at all, so key-based attribution
                       would leave exactly that branch mis-read.

    An ARM entry cannot make a leg LOST, and one of them can prove one was
    SERVED: an arm that answered ``ok`` produced rows for the slot it shares, so
    a missing index on the shipped leg cost this read nothing. Without that
    half, a store whose fulltext index is absent — the benign embedded state
    ``run_fts_query`` documents — and whose arm re-read the same slot from the
    graph is marked ``truncated: true`` on EVERY read, all rows delivered: the
    same false positive as judging the arm's own failure, one step further on.
    """
    lost: set[str] = set()
    served_by_an_arm: set[str] = set()
    for index, entry in enumerate(status_trace or ()):
        if not isinstance(entry, dict):
            continue
        leg = entry.get("leg")
        if not asked_legs.get(leg):
            continue
        if skip_from is not None and index >= skip_from:
            # This entry is the ARM's own verdict about the slot it shares.
            if entry.get("reason") == "ok":
                served_by_an_arm.add(leg)
            continue
        if not entry.get("degraded"):
            continue
        answered = LEG_ANSWERED_REASONS.get(leg, DEFAULT_LEG_ANSWERED_REASONS)
        if entry.get("reason") not in answered:
            lost.add(leg)
    return lost - served_by_an_arm


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
