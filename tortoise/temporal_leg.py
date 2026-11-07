"""Temporal retrieval leg (#2976): deterministic event-ordering retrieval.

The measured defect (issue #2976): on the LongMemEval temporal-reasoning
subset the gold evidence IS in the candidate pool but sits at ranks 41–120,
while the reader window holds ~12–24 items of pure semantic RRF. Oracle
context answers 42/52 of the answerable questions, so the failure is
retrieval ordering, not reasoning. The pre-existing TR machinery
(``detect_time_constraint`` → ``_apply_time_window``) is INERT on this class:
the questions reference *events* ("which happened first, the X or the Y",
"how many days between A and B"), not explicit dates, so no window is ever
computed and no re-rank happens.

This module implements the missing leg. The mechanism follows the state of
the art (see ``docs/research/2026-09-08-temporal-reasoning-decomposition-assembler.md``):

* §1 — comparison/ordering morphology ("which came first", "before/after",
  "earlier than") is **closed-form deterministic structure** (TEQUILA
  lineage, TempQuestions templates), so no LLM decomposition is needed;
* §2 (Mem0 read side, HIGH vendor) — temporal intent must **never
  pre-filter** the candidate pool (pre-filtering silently drops imprecise /
  undated memories); it is an **additive rerank** in which "semantic
  relevance always dominates";
* §1/§2 — for ordering/comparison questions the deterministic equivalent of
  a state_key co-retrieval is to fetch **both halves of the comparison**;
  this leg therefore promotes the best content match for each anchor *by
  construction* instead of relying on semantic similarity to surface both.

Design invariant: the leg NEVER filters. It only produces a *ranked list*
that is fused into the existing RRF as one more leg (``rrf_fusion``), so a
question with no temporal constraint — or a pool with no dated candidates —
yields an empty leg and a byte-identical fused order.

Publication surface: pure functions over already-annotated hits; no DB
access, no I/O, no LLM calls. The caller (the eval harness today) supplies
the dated session metadata.

Residual (NOT covered here — recorded so it is not mistaken for verified)

* The glue in ``tools/longmem_eval/retrieve.py`` (arm resolution, the
  ``annotated`` reorder, the ``legs`` trace entry, ``temporal_leg_stats``)
  has no DB-free test: the components and the shared fusion helper are
  tested, the wiring is covered by review only.
* Anchor recovery is regex-based and bounded (``.{3,120}?``); anaphoric
  second sides with fewer than two content tokens ("...and the day I
  received it" → ``{received}``) are DROPPED, not used as weak anchors — a
  one-token anchor is claimed by any candidate carrying that word, which
  is the noise promotion this leg must avoid. Such a question keeps its
  other side or goes inert.
* The measured table below is a TF-IDF proxy over raw turns, not the
  shipped point pool — see the caveat there.
"""

from __future__ import annotations

import re

from .coverage_loop import _calendar_date
from .retrieval import (
    DEFAULT_MAX_CHUNKS_PER_SESSION,
    dedup_pool,
    is_raw_chunk,
)
from .search_engine import rrf_fusion

# ── defaults (documented, knob-exposed by the caller) ────────────────────────

#: Promotion BUDGET: how many temporal picks are fused. Two is the
#: principled size of the target class — an ordering/comparison question
#: needs exactly the TWO compared instances co-present (research §1/§2),
#: and the budget is what lands them in the window tail. Two is the
#: co-present pair the ordering/comparison class needs (research §1/§2).
#: The tuning replay measured the trade at this budget: on a near-ceiling
#: TF-IDF base, budget 1 is strictly no-harm (every admission metric
#: unchanged) while budget 2 admits one more all-sessions-co-present
#: question at the cost of ~1.9 pts evidence-turn recall@12. The proxy's
#: base has nothing to fix (recall 0.94) whereas the shipped base is
#: 0/52 answerable with gold at ranks 41–120, so the budget is chosen for
#: the target class and the residual is measured on the eval lane, not
#: here. ``TORTOISE_LME_TEMPORAL_LEG_LIMIT=1`` is the conservative arm.
DEFAULT_TEMPORAL_LEG_LIMIT = 2

#: Per-date/session cap: one, so the date-spread fill comes from a
#: DIFFERENT session/date than an anchor pick — the "coverage is not
#: clustered" requirement in its strongest form. This is a TRUE cap across
#: BOTH phases (a phase-1 anchor pick consumes its bucket's slot, so phase 2
#: cannot add a second pick from the same date/session).
DEFAULT_TEMPORAL_LEG_BUCKET_CAP = 1

#: RRF weight for the temporal leg. 1.0 = equal footing with the fused
#: semantic leg (the diagnosis is a hard retrieval failure: 0/52 answerable).
#: ``rrf_fusion`` multiplies the leg term, so the DIRECT caller can soften
#: (<1.0) or dominate (>1.0). NOTE the harness env path
#: (``TORTOISE_LME_TEMPORAL_LEG_WEIGHT``) resolves through
#: ``rerank._env_float``, which accepts only [0.0, 1.0] and falls back to
#: this default otherwise — so ``>1.0`` is kwarg-only, and the env value
#: 0.0 is the weight-off switch (``weight_disabled`` in the trace).
DEFAULT_TEMPORAL_LEG_WEIGHT = 1.0

#: #10538 NON-EVICTION GUARD — ON by default, and it is a HARD CONDITION of
#: the shipped arm rather than a preference. Owner ruling 2026-11-07
#: ("window-exit semantics"): *"expansion must never evict base evidence"*,
#: with the three guards stated as conditions of the fix — dedupe to unique
#: sessions, a per-session cap, never crop the anchor. The measurement that
#: makes it a gate rather than a caveat: at the shipped geometry the UNGUARDED
#: tail splice is net-zero on distinct evidence sessions and can trade a gold
#: session for a non-gold one (``BASE distinct 3 / FUSED distinct 3``,
#: ``EVICTED ['b1','c1']`` — #10538 comment 6574179020). ``False`` restores the
#: pre-ruling splice BYTE-FOR-BYTE on the same pool, which is what keeps the
#: committed retention receipt (`docs/scoping/receipts/2026-11-03-10538-*`)
#: reproducible; the harness switch is
#: ``TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION=0``.
#:
#: ⛔ **GRANULARITY, stated so the prose and the code agree.** The condition is
#: SESSION-level: no *session* may lose its last visible slot, i.e. the reader
#: window may trade a redundant slot and may never delete a session. That is
#: deliberately not slot-level — "never displace any id" would forbid every
#: tail splice at the shipped geometry and make the arm inert everywhere, and
#: session survival is the unit the ruling's three conditions and
#: :func:`window_arm_retention`'s ``deleted_sessions`` are written in. A pick
#: that displaces a redundant slot (its session keeps another one visible) is
#: ADMITTED, and the window then holds one MORE distinct session; a pick that
#: re-delivers a session the reader already has is REFUSED however harmless it
#: looks, because it buys no new session.
DEFAULT_TEMPORAL_LEG_NON_EVICTION = True

#: Minimum anchor-token overlap fraction for an anchor to accept a match.
#: Short anchors (1–2 content tokens, e.g. "received it") need one token;
#: longer ones need half, so a single common word cannot claim an anchor.
#: One-token anchors (an anaphoric "...and the day I received it" →
#: {received}) are claimed by ANY candidate carrying that single word, which
#: is a noise-promotion risk. They are not dropped — measured, they carry
#: the co-present win on the target class (see the module docstring) — but
#: such a token must be RARE in the candidate pool to be eligible
#: (document-frequency gate below). ``_anchor_min_overlap`` needs half the
#: tokens for the multi-token anchors.
_ANCHOR_DF_MAX = 10
_ANCHOR_DF_FRACTION = 0.10


def _anchor_min_overlap(n_tokens: int) -> int:
    """Overlap needed for an eligible anchor: half its tokens (ceil)."""
    return 1 if n_tokens <= 2 else (n_tokens + 1) // 2


#: Content-token stoplist. Deliberately small: it removes function words and
#: the temporal vocabulary that is shared by *every* anchor phrase (so
#: "the day I bought" contributes {bought}, not {day, bought}).
_STOPWORDS = frozenset([
    "a", "about", "after", "all", "also", "am", "an", "and", "any", "are",
    "as", "at", "be", "been", "before", "being", "between", "but", "by",
    "can", "could", "day", "days", "did", "do", "does", "doing", "done",
    "for", "from", "had", "has", "have", "having", "he", "her", "here",
    "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it",
    "its", "just", "like", "many", "me", "might", "more", "most", "much",
    "must", "my", "no", "not", "of", "on", "once", "only", "or", "other",
    "our", "out", "over", "own", "said", "same", "she", "should", "since",
    "so", "some", "such", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "those", "through", "time", "times",
    "to", "today", "tomorrow", "too", "under", "until", "up", "us", "very",
    "was", "we", "week", "weeks", "were", "what", "when", "where", "which",
    "while", "who", "whom", "why", "will", "with", "would", "year",
    "years", "yesterday", "you", "your",
])

_TOKEN_RE = re.compile(r"[a-z0-9']+")


def content_tokens(text: str) -> frozenset[str]:
    """Content tokens of ``text`` (lowercased, stopwords dropped).

    Deterministic and order-free — the anchor/overlap surface. Tokens shorter
    than 3 chars are dropped (they carry no anchor signal and blow up
    false matches).
    """
    return frozenset(
        t for t in _TOKEN_RE.findall((text or "").lower())
        if len(t) >= 3 and t not in _STOPWORDS
    )


def _in_date_window(value: object, date_window: tuple[str, str]) -> bool:
    """#10538: is this session date inside the question's derived range?

    Both sides resolve through the ONE calendar-date resolver the product loop
    already uses (:func:`tortoise.coverage_loop._calendar_date`), so an ingest
    ``2023/04/10 (Mon) 17:50`` compares correctly against an ISO bound —
    comparing RAW strings is what made ``_apply_time_window`` inert on the
    frozen dataset (#9182). An undated candidate cannot be proven in-window.

    An unparseable BOUND is not a licence to compare strings: it returns
    False (nothing is provably in-window), which fails toward an inert leg
    rather than toward a raw-string comparison that #9182 already measured as
    silently wrong.
    """
    lo = _calendar_date(date_window[0])
    hi = _calendar_date(date_window[1])
    if lo is None or hi is None:
        return False
    day = _calendar_date(value)
    if day is None:
        return False
    return lo <= day <= hi


def _session_clustered(candidates: list[dict], head: int, limit: int,
                       window: int = 0) -> bool:
    """#10538: does ONE session own half or more of the visible slots?

    This is the defect's OWN condition, not a second gate: the issue defines
    the failure as retrieval that "clusters on the single most-similar session
    and starves the others". Without it, a window-targeted spread is
    UNTARGETED — and the pick can displace a correct rank-(k-1) gold on a
    question that was never starved. That is the shape #2976 measured at -3.3
    pts (see the module docstring), so the spread must fire only where the
    evidence really is starved.

    ⚠️ HONEST SCOPE (review cycle 2, #10549): the window does NOT do the
    targeting work, and could not — the wired caller derives the very bounds
    ``_apply_time_window`` already filtered on, so every surviving hit is
    in-window and the window keeps 100% of the promotable set. THIS clustering
    condition is the sole targeting signal; ``date_window`` only scopes which
    sessions are eligible when the caller passes a span the filter has not
    already applied. Stated plainly so the next reader does not credit the
    window with a selection it cannot perform.

    The sample is the caller's READER WINDOW, ``candidates[:max(head,
    window)]`` — NOT ``head`` alone. ``head`` is the caller's PROMOTION
    boundary, and the fused caller passes ``ceiling = window - limit``: the
    ``limit`` slots the promotion actually displaces are missing from it. At
    the shipped geometry (``window=12, limit=2``) sampling ``[:head]`` read
    10 of the 12 visible slots and omitted exactly the two at risk, so the
    guard could only OVER-fire — measured on a pool where one session owns 5
    of the first 10 slots, indices 10/11 are two other sessions and index 12
    a third: the ceiling sample says clustered (``5*2 >= 10``) while the
    documented rule over the visible 12 declines (``5*2 < 12``), and the
    resulting pick at index 12 evicted a reader-visible slot (review cycle 8,
    #10549). It could also never fire at all for ``window=3`` (ceiling 2, a
    2-slot sample is below the ``< 3`` floor, so the arm was dead there).

    ``head == 0`` is the unguarded test mode AND the ``placement="head"``
    counterfactual; there ``window`` (the real reader window the caller
    holds) stands in for it when supplied, and only when BOTH ``head`` and
    ``window`` are 0 (a direct caller with no window in hand) does the
    promotion ``limit`` size the sample. That last fallback is non-vacuous
    only because of the ``< 3`` guard below — at the default ``limit=2`` a
    2-slot sample makes ``max(counts) * 2 >= len(visible)`` vacuously true,
    which is what let a four-distinct-session pool promote before the guard
    existed (review cycles 2-3, #10549).

    A hit with no ``session_id`` is NOT a session and never counts toward
    the majority (it also never adds to the denominator it is already in):
    collapsing every absent id into one group would let "most hits carry no
    session at all" read as "one session owns the window", which is an
    over-fire — the budget spent displacing an in-window pick on evidence
    that does not exist. With no real session in the sample the answer is
    ``False``, i.e. the arm stays inert (review cycle 10, #10549).
    """
    if head > 0 or window > 0:
        visible = candidates[:max(head, window)]
    else:
        visible = candidates[:max(limit, 1)]
    # A sample below three slots cannot distinguish clustered from spread:
    # with two, `max >= 1` makes the test vacuously true.
    if len(visible) < 3:
        return False
    counts: dict[str, int] = {}
    for c in visible:
        # A hit with NO session id is not a session. Counting them together
        # under one "" key would let "most hits carry no session at all"
        # satisfy "one session owns half the window" -- an OVER-fire that
        # spends the budget displacing an in-window pick for a spread there
        # is no evidence for (review cycle 10, #10549). Fail closed: if no
        # real session survives, the window arm stays inert.
        key = str(c.get("session_id") or "")
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return False
    return max(counts.values()) * 2 >= len(visible)


def temporal_leg_order(
    candidates: list[dict],
    *,
    anchors: tuple[str, ...] | list[str] = (),
    limit: int = DEFAULT_TEMPORAL_LEG_LIMIT,
    bucket_cap: int = DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
    head: int = 0,
    date_window: tuple[str, str] | None = None,
    window: int = 0,
    targeting_trace: dict | None = None,
) -> list[str]:
    """Rank a temporal leg over already-ranked candidates → list of ids.

    ``candidates`` MUST be in the existing fused (semantic) rank order —
    the leg reads that order as the semantic-relevance prior and never
    re-sorts by date ("semantic relevance always dominates", research §2).
    Each candidate is a mapping with ``id``, ``session_date`` (ISO or ""),
    ``session_id``, and ``content``.

    The returned order is built in two deterministic phases:

    1. **Co-present anchor coverage** (the 32/52 ordering/comparison class):
       for each anchor phrase, the candidate with the greatest content-token
       overlap — ties broken by the EARLIER semantic rank — is promoted to
       the head of the leg. Both halves of a comparison therefore reach the
       fusion together, which is the deterministic stand-in for a state_key
       co-retrieval (research §1/§2).
    2. **Date/session spread**: the TARGETED candidates -- anchor-relevant (those
       sharing at least one content token with any anchor) or, on the #10538
       window path, in-window (no anchor needed) -- are bucketed by
       ``(session_date, session_id)``; buckets are visited in the order their
       best member appears in the semantic ranking, and round-robin over
       rounds up to ``bucket_cap``. Coverage is spread across the temporal
       span instead of clustered on the semantically nearest date, while
       staying inside the question's own event vocabulary.

**Targets are required — an anchor, or (#10538) a date window.** A question
whose comparison phrases cannot be recovered (no anchor matched any candidate)
yields ``[]`` **unless** a ``date_window`` is supplied *and* the visible slots
are session-clustered, in which case the window stands in as the target (see
:func:`_session_clustered`). Both restrictions exist for the same measured
reason: a spread-only leg promotes
arbitrary deep pool items and, on a saturated-base proxy replay of the
55-question temporal subset, cost 3.3 pts of evidence-turn recall@12 for
+1 co-present question. Restricting the spread to anchor-relevant **or
date-window-scoped, cluster-confirmed** evidence keeps the promotion targeted;
anchor-less, window-less ordering questions
("how many days ago did I buy a smoker?") fall back to the pre-#2976
behavior. The residual is documented on the arm (see the PR body).

**Bounded promotion (``head``).** ``head`` excludes the first ``head``
candidates from BOTH phases. It is the caller's PROMOTION boundary, NOT its
reader window: :func:`temporal_leg_fusion_order` passes ``ceiling = window −
limit`` (where its picks START), which reaches ``limit − 1`` slots INTO the
window. That is deliberate and measured for phase 1 (the anchor path is
measured AS-IS, #2976); the window branch does not rely on it — its own
:data:`held_buckets` exclusion is what keeps a pick off an id the reader
already sees. ``head=0`` is the unguarded mode: the unit tests use it to
exercise the phases in isolation, and ``placement="head"`` reaches it through
THIS caller (``head_excl = 0 if placement == "head" else ceiling``), which is
the counterfactual the retention baseline measures at the shipped window. The
default (``placement="tail"``) never passes ``head=0`` at a window >= 2.

**Budget clamping.** :func:`temporal_leg_fusion_order` clamps the budget
via :func:`effective_promotion_budget` to ``max(1, min(limit,
max(window // 3, 1)))`` (and ``0`` when ``limit <= 0``, i.e. OFF) so no
knob value can push the picks back to leg ranks 0..N (the takeover) — with
the default 2 and a window of 12 the picks land in slots 10–11. The caller
must report the CLAMPED value in its telemetry
(``effective_promotion_budget`` is exported for that).

**Window-tail placement (the caller's contract).** Because this leg is
fused with ``limit`` = a small promotion BUDGET, the caller places it
AFTER the window's head in the fused leg list. ``temporal_leg_fusion_order``
below is that caller — it computes ``ceiling = max(window − limit, 0)``,
passes it as ``head`` so a pick is strictly OUTSIDE the head slice (never
double-counted in the leg list), and fuses ``[head ids] + [picks]`` so a
pick enters at the window TAIL rather than at rank 0. The leg therefore
cannot scramble the visible head — it only backfills the weakest visible
slots.

Placement is a STRUCTURAL choice, not a measured win. The proxy below
cannot adjudicate it: at the default budget the rank-0 counterfactual
scores equal-or-better there (recall@12 0.9506 vs 0.9228, all-gold-turns
48/54 vs 45/54, same co-present 53/54), because its baseline is already
near-ceiling and so cannot represent the shipped failure. The tail stays
the default on the argument the proxy cannot test: the shipped base is
0/52 answerable, so the reader window's only working signal is the semantic
head, and a rank-0 temporal swap can only trade that away for an
anchor-matched turn. ``--placement head`` on the replay keeps the
counterfactual reproducible for the eval lane, where the base is not
near-ceiling — that A/B belongs there, not in this default.

Measured effect — ``tools/longmem_eval/temporal_leg_replay.py``, the
pinned 55-Q temporal subset (54 answerable), window = 12, a TF-IDF
semantic proxy over the dataset's own turns. THIS IS NOT THE SHIPPED
POOL (points + bge-small + FTS + structural), and its baseline is
near-ceiling, so it can only bound the no-harm direction:

| placement | budget | evidence-turn recall@12 | all gold turns | all gold sessions co-present |
|-----------|--------|------------------------|----------------|------------------------------|
| base      | —      | 0.9414                 | 47/54          | 52/54                        |
| tail      | 1      | 0.9414 (no-harm)       | 47/54          | 52/54                        |
| tail      | 2      | 0.9228                 | 45/54          | 53/54                        |
| head      | 2      | 0.9506                 | 48/54          | 53/54                        |

Rows `head` are the rank-0 counterfactual (`--placement head`); they are
why placement cannot be settled here (see above). The leg fires on 49 of
the 54 answerable questions.

RE-DERIVED at #10538's head (2026-11-02) with
``uv run python -m tools.longmem_eval.temporal_leg_replay --json`` (plus
``--limit 1`` and ``--placement head --limit 2``): every row above
reproduces EXACTLY, so the table is not stale — but read its SCOPE, which
is narrower than "the leg's effect". The pinned subset's own constraint
class is uniform: ``detect_time_constraint`` returns ``ordering`` for **all
55** questions, and ``facet_window_bounds`` has no computable bound for
``ordering``, so the ``date_window`` #10538 threads through the proxy is
``None`` on every question. The replay therefore exercises the ANCHOR path
only: it bounds the no-harm direction there and says nothing about the
#10538 window arm (which fires on the anchor-less ``interval``/``recency``
class this subset does not contain). Do not read these rows as a
measurement of the window arm; that one belongs to the eval lane (#9097),
for the same reason the placement question does.

The shipped base is 0/52 answerable with gold at ranks 41–120 — a base
with nothing to lose — so budget 2 (the co-present pair, the target
class) is the default and the end-to-end delta is the eval lane's to
measure. Set ``TORTOISE_LME_TEMPORAL_LEG_LIMIT=1`` for the conservative
arm (``temporal_leg_replay.py --limit 1`` reproduces that row).

Undated candidates are excluded from the leg (no date → no temporal
signal; they keep their semantic score unfiltered). Returns ``[]`` when
no candidate is dated, which the caller treats as "leg inert".

``targeting_trace`` — optional mutable mapping that receives
``{"targeting": "anchor" | "window" | "none"}``: WHICH target selected the
picks. ``"window"`` means the #10538 date-range branch supplied them (no
anchor matched, and the visible slots were session-clustered); ``"anchor"``
means phase 1's comparison anchors did; ``"none"`` means no target existed.
It exists because the two arms carry DIFFERENT contracts — the anchor path is
the #2976 arm measured AS-IS, while the window path is the #10538 arm the
non-eviction guard governs — so :func:`temporal_leg_fusion_order` must be able
to tell them apart without re-implementing this function's targeting logic.
An out-parameter, because the ``list[str]`` return is unpacked by a large
pinned test surface.
    """
    # ⚠️ #10538 (review cycle 4): stamp the trace's INERT value up front, not
    # only on the successful exit below. Every "no target existed" path returns
    # before the end of this function (no limit, no dated candidate, nothing
    # promotable), so a trace written only at the end left the documented
    # ``"none"`` variant with NO producer: the receipt's
    # ``non_eviction_targeting`` came out JSON ``null`` and a consumer keying on
    # ``"none"`` missed every anchor-less row — the one field that makes
    # ``applied: False`` legible. Writing it here covers those returns AND any
    # future one; the end-of-function write still names the arm that fired.
    if targeting_trace is not None:
        targeting_trace["targeting"] = "none"
    if limit <= 0 or bucket_cap <= 0:
        return []
    dated = [c for c in candidates if c.get("session_date")]
    if not dated:
        return []
    # the head slice needs no promotion — never disturb it. The
    # boundary is the CANDIDATE index (matching this function's ``head``
    # contract), not the index within the date-filtered list: an undated
    # candidate sitting in the head must not shift the boundary downward
    # and silently make a deep anchor match unpromotable.
    #
    # ⚠️ ``head`` is the caller's PROMOTION boundary, not its reader window:
    # the fused caller passes ``ceiling = window - limit`` (where its picks
    # START), so this slice reaches ``limit - 1`` slots INTO the window. That
    # is deliberate for phase 1 (the anchor path is measured as-is, #2976) and
    # the window branch's own exclusion — :data:`held_buckets` — is what keeps
    # the window path from re-emitting an id the reader already sees. For the
    # same reason the window branch's clustering guard samples the READER
    # WINDOW (``max(head, window)``), never this slice: sampling `head` alone
    # both over-fired and, at ``window=3``, could never fire at all (review
    # cycle 8, #10549).
    promotable = [c for i, c in enumerate(candidates)
                  if i >= max(head, 0) and c.get("session_date")]
    if not promotable:
        return []

    picks: list[str] = []
    seen: set[str] = set()
    #: #10538: which target selected the picks — see ``targeting_trace``.
    targeting = "none"
    #: the ids phase 2 is allowed to spread over: every candidate sharing at
    #: least one content token with some anchor (phase 1), OR — when no anchor
    #: matched anything and the visible slots are session-clustered — every
    #: PROMOTABLE candidate whose session date is inside the question's
    #: ``date_window`` (#10538). The window branch runs precisely because no
    #: anchor matched, so its members need not share an anchor token.
    relevant: set[str] = set()
    #: #10538: the (date, session) buckets the reader ALREADY holds in
    #: the visible window — never spent on a pick by the window path. See the
    #: window branch below for why this is the whole slice and not just the
    #: bucket sitting at the promotion ceiling. ⚠️ ``candidates`` here is in
    #: ANNOTATED index space; the reader's visible window is
    #: ``pool[:effective_top_k]`` taken AFTER ``_dedup_pool`` drops
    #: ``session-transcript`` raw chunks beyond ``max_chunks_per_session``, so
    #: a TR pool carrying a flood session's raw chunks can make the reader's
    #: window reach DEEPER than ``candidates[:window]``. Inherited from #2976's
    #: ``ceiling`` (not introduced here); the exclusion is therefore a
    #: bucket-level no-re-emit guarantee on the annotated pool, not a hard
    #: bound on what the reader ends up seeing.
    held_buckets: set[tuple[str, str]] = set()
    #: true per-(date, session) cap accounting ACROSS both phases — a
    #: phase-1 anchor pick consumes its bucket's slot.
    bucket_counts: dict[tuple[str, str], int] = {}

    def _bucket_key(c: dict) -> tuple[str, str]:
        return (str(c.get("session_date") or ""),
                str(c.get("session_id") or ""))

    # ── phase 1: co-present anchor coverage ──
    for anchor in anchors:
        need = content_tokens(anchor)
        if not need:
            continue
        # A one-token anchor matches on a single common word; admit it only
        # when that word is rare in THIS pool (a pool-local IDF gate).
        if len(need) == 1 and len(promotable) > 0:
            tok = next(iter(need))
            df = sum(1 for c in promotable
                     if tok in content_tokens(c.get("content") or ""))
            df_cap = max(1, min(_ANCHOR_DF_MAX,
                                int(_ANCHOR_DF_FRACTION * len(promotable))))
            if df > df_cap:
                continue
        min_ov = _anchor_min_overlap(len(need))
        best: dict | None = None
        best_ov = 0
        for c in promotable:  # semantic rank order → first max wins
            toks = content_tokens(c.get("content") or "")
            hit = need & toks
            if hit:
                relevant.add(c["id"])
            if len(hit) > best_ov:
                best_ov, best = len(hit), c
        if (best is not None and best_ov >= min_ov
                and best["id"] not in seen):
            picks.append(best["id"])
            seen.add(best["id"])
            key = _bucket_key(best)
            bucket_counts[key] = bucket_counts.get(key, 0) + 1

    # Targets are required: without a deterministic target the leg must stay
    # inert (never a spread-only promotion of ARBITRARY pool items — the
    # measured 3.3-pt cost of that on the 55-question temporal subset).
    #
    # #10538: a question may name its target as a DATE RANGE instead of
    # comparison anchors ("what did I do between 2023-04-10 and 2023-04-20").
    # The range is the question's own vocabulary — the same targeting property
    # an anchor has — BUT it is only a target when the visible slots are
    # session-CLUSTERED (the issue's own definition of the failure): after
    # ``_apply_time_window`` every remaining hit is in-window, so without that
    # condition the window selects the whole pool and the pick can displace a
    # correct rank-(k-1) gold on a question that was never starved.
    if not relevant:
        if date_window and _session_clustered(candidates, head, limit, window):
            #: the DATE-RANGE branch is the #10538 arm — the one the
            #: non-eviction guard governs — recorded before picks are built.
            targeting = "window"
            relevant = {c["id"] for c in promotable
                        if _in_date_window(c.get("session_date"),
                                           date_window)}
            # Every bucket the reader ALREADY holds in the visible head slice
            # is excluded wholesale, so no pick re-emits a bucket (a
            # ``(session_date, session_id)`` pair, the key ``_bucket_key``
            # builds) that the reader can already see. That is a BUCKET-level
            # guarantee, not a session-level one: it collapses to "a session
            # the reader does NOT already have" only because the producer
            # writes one ``session_date`` per session (``retrieve.py``'s
            # ``dates[si]``), and two hits of the same session carrying
            # different raw date strings are two buckets. Slice includes the
            # candidate sitting
            # at the promotion ceiling (index ``head``): a pick from that
            # bucket re-emits an id already in the head slice at its own
            # index, so the fused order comes out byte-identical while the
            # stats report ``applied``/``picks=1`` (review cycle 3, #10549 —
            # the ``TORTOISE_LME_TEMPORAL_LEG_LIMIT=1`` conservative arm did
            # nothing at all).
            #
            # ⚠️ The ceiling bucket ALONE is not enough (review cycle 5): with
            # the shipped geometry ``window=12, limit=2`` (``ceiling=10``) and
            # a head slice of ``sA``×8 + ``sB``×2, the ceiling bucket is
            # ``sB`` — so the SECOND half of the budget was spent re-adding
            # ``sA``, the session owning 8 of the 10 visible slots, and it
            # EVICTED ``b3``/``sB`` (a different session) from the window: the
            # inverse of the unit's objective. Every bucket the head holds is
            # therefore excluded, not just the one at the boundary.
            #
            # ⚠️ And the slice must be the READER WINDOW, not the promotion
            # ceiling (review cycle 7). ``head`` is ``ceiling = window -
            # limit`` — where the caller's picks start — while the reader sees
            # ``candidates[:window]`` (``retrieve.py`` truncates the pool to
            # ``effective_top_k``). Excluding only ``[:head + 1]`` left the
            # window's own tail slots (``head + 1 .. window - 1``, ``limit - 1``
            # of them) promotable, so at the shipped geometry a pick was spent
            # re-emitting ``candidates[window - 1]`` — an id the reader already
            # has — while a genuinely new bucket below the window went
            # unpromoted: measured ``window=12, limit=2`` on a ``sA``×10 head
            # with ``b1``/``sB`` at 10, ``c1``/``sC`` at 11, ``d1``/``sD`` at
            # 12, ``e1``/``sE`` at 13 gave ``['c1','d1']`` (one wasted pick)
            # where this slice gives ``['d1','e1']``. ``window`` only participates
            # when the caller supplies it; a direct caller passing a bare
            # ``head`` keeps the ``head + 1`` slice it has always had.
            held_buckets = {_bucket_key(c)
                            for c in candidates[:max(head + 1, window)]}
        if not relevant:
            return []
    else:
        #: phase 1's comparison anchors selected the targets — the #2976 arm,
        #: measured AS-IS and NOT the arm #10538's guard governs.
        targeting = "anchor"

    # ── phase 2: date/session spread over ``relevant`` (anchor-relevant, or
    # #10538 in-window) evidence ──
    # dict preserves insertion order → buckets are ordered by the semantic
    # rank of their first member (deterministic). ``bucket_cap`` is a true
    # cap across both phases: a bucket that already supplied a phase-1
    # anchor pick is skipped once its count reaches the cap.
    buckets: dict[tuple[str, str], list[str]] = {}
    for c in promotable:
        if c["id"] not in relevant:
            continue
        buckets.setdefault(_bucket_key(c), []).append(c["id"])
    for round_idx in range(bucket_cap):
        for key, members in buckets.items():
            if key in held_buckets:
                continue
            if round_idx < len(members):
                if bucket_counts.get(key, 0) >= bucket_cap:
                    continue
                pid = members[round_idx]
                if pid in seen:
                    continue
                picks.append(pid)
                seen.add(pid)
                bucket_counts[key] = bucket_counts.get(key, 0) + 1

    if targeting_trace is not None:
        targeting_trace["targeting"] = targeting
    return picks[:limit]


def effective_promotion_budget(window: int, limit: int) -> int:
    """The clamped promotion budget actually used by the fused caller.

    Exposed so the caller's telemetry (and the tests) report the REAL
    budget rather than the requested knob: at most a third of the window
    (and at least one), which is what keeps ``ceiling`` leaving both a head
    and a tail for ``window >= 2``. ``limit <= 0`` means OFF (0 → the fused
    caller promotes nothing), so the knob and the arm layer agree on what
    "zero" means for DIRECT callers; note the harness env path
    (``_env_int``) floors values below 1 to the default, so the env way to
    disable the leg is ``TORTOISE_LME_TEMPORAL_LEG=0``, not ``LIMIT=0``.
    """
    if int(limit) <= 0:
        return 0
    return max(1, min(int(limit), max(int(window) // 3, 1)))


def temporal_leg_empty_reason(*, dated: int, anchors: int, weight: float,
                              budget: int = 1,
                              date_window: bool = False,
                              guard_rejected: int = 0) -> str:
    """The honest leg-trace reason for an EMPTY leg (the seven causes).

    ``budget_disabled``        — the effective promotion budget is 0
    ``no_dated_candidates``    — the pool carries no dated hit at all
    ``no_anchors``             — the question yielded no comparison phrase
    ``weight_disabled``        — the arm ran at weight <= 0 (picks dropped)
    ``non_eviction_guard``     — #10538: the leg SELECTED picks and the
        non-eviction guard rejected **every one of them** (``guard_rejected``
        > 0). This is the ruling working, not a miss: the arm had candidates
        to promote and promoting them would have cost the reader window an
        evidence session, so it correctly left the fused order byte-identical
        to the semantic one. Reported separately because the alternative is
        that the guard's own refusals read as "the leg found nothing" — which
        would make the guard invisible on exactly the surface an operator uses
        to reconstruct ON/OFF, and would invite "fixing" a working guard.
        Ordered AFTER ``weight_disabled`` (a weight-0 arm promotes nothing, so
        it never reached the guard) and BEFORE the window/anchor branches (a
        guard refusal is a true statement about the run whatever targeted it).
    ``no_promotable_anchor_match`` — anchors exist but none matched an
        eligible candidate (no match at all, or every match already sits
        inside the excluded visible head and therefore needs no lift)
    ``no_window_candidates``   — #10538: no anchors, a date window WAS
        supplied, and the window path promoted nothing. THREE reachable
        causes, and the trace cannot tell them apart: (a) it selected no
        in-window promotable hit at all; (b) the visible slots were not
        session-clustered, so the spread correctly declined to fire; (c) it
        DID select candidates and the slots WERE clustered, but every
        in-window bucket is one the reader already holds in the visible head
        slice (``held_buckets``), so the exclusion starved the pick set —
        measured on a single-session pool (14 × one session, ``window=12``),
        where (a) and (b) are both FALSE. Named here rather than left
        implicit because this is the ON/OFF reconstruction surface and a
        windowed miss must not be laundered as an anchor miss. Distinct from
        ``no_anchors`` on purpose: the two are different code paths. ⚠️ It is
        reported only when ``anchors <= 0``; an anchor-PRESENT question whose
        window path starves reports ``no_promotable_anchor_match``, which is
        why the trace is a reconstruction surface and not an attribution one
        (review cycle 8, #10549).

    ``budget`` defaults to 1 so a caller that does not track it keeps the
    pre-budget behavior; ``guard_rejected`` defaults to 0 so a caller that
    predates the #10538 guard keeps its pre-guard reasons.
    """
    if budget <= 0:
        return "budget_disabled"
    if dated <= 0:
        return "no_dated_candidates"
    # ``weight_disabled`` is checked BEFORE the window/anchor branches: a
    # windowed question on a weight-0 arm must not report a window miss
    # (review cycle 2, #10549).
    if weight <= 0:
        return "weight_disabled"
    if guard_rejected > 0:
        return "non_eviction_guard"
    if anchors <= 0 and date_window:
        return "no_window_candidates"
    if anchors <= 0:
        return "no_anchors"
    return "no_promotable_anchor_match"


def temporal_leg_fusion_order(
    candidates: list[dict],
    *,
    anchors: tuple[str, ...] | list[str] = (),
    window: int,
    limit: int = DEFAULT_TEMPORAL_LEG_LIMIT,
    bucket_cap: int = DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
    weight: float = DEFAULT_TEMPORAL_LEG_WEIGHT,
    placement: str = "tail",
    date_window: tuple[str, str] | None = None,
    non_eviction: bool = DEFAULT_TEMPORAL_LEG_NON_EVICTION,
    guard_trace: dict | None = None,
    max_chunks_per_session: int | None = DEFAULT_MAX_CHUNKS_PER_SESSION,
) -> tuple[list[str], list[str]]:
    """Fuse the temporal leg into the existing RRF — the window-tail wiring.

    This is the ONE place the placement contract from
    :func:`temporal_leg_order` is implemented, so the eval arm and the
    tests exercise the same code path (a test that re-implements the
    formula cannot catch a wiring regression).

    ``candidates`` MUST already be in the existing fused (semantic) rank
    order, and its ``id`` values MUST be unique (the fusion builds a
    ``dict`` keyed by id; a duplicated id inside the head slice would be
    counted twice and could outrank rank 0 — unreachable through the
    harness, whose hits are dict-merged by id).

    ``placement``: ``"tail"`` (shipped) fuses ``[head ids] + [picks]`` so a
    pick enters at the window tail; ``"head"`` is the COUNTERFACTUAL
    diagnostic that fuses the picks at leg ranks 0..N — the placement this
    default avoids, kept reproducible for the eval lane
    (``temporal_leg_replay.py --placement head``; equal-or-better on the
    near-ceiling proxy, rejected here on the structural argument in the
    module docstring).

    ``non_eviction`` (#10538, owner ruling 2026-11-07 — **ON by default**) is
    the guard that ruling made a HARD CONDITION of the arm, applied to the
    SHIPPED ``tail`` placement **of the DATE-WINDOW arm** (the ``#10538``
    branch: no anchor matched, the visible slots are session-clustered, and the
    question's own range supplied the target): a pick may not cost the reader
    window an evidence SESSION. Three conditions, evaluated per pick in the
    leg's own deterministic order; a pick that fails any of them is DROPPED —
    never re-placed and never swapped for another:

    ⛔ **Scope, declared rather than implied:** the ANCHOR arm (``#2976``) is NOT
    guarded. That arm is measured as-is and carries a published proxy table of
    its own; the same defect is present there (a tail pick can evict a session),
    but extending the guard across it would move numbers no decision covers and
    would fold two arms into one attribution. It is recorded as remaining scope
    on #10538, not silently changed here.

    1. **Dedupe to unique sessions** — a pick whose session is already visible
       in the reader window adds no new session and spends a slot, so it is
       rejected (``session_already_visible``).
    2. **Per-session cap** — two picks from one session are two deliveries of
       one source; the later one is rejected (``duplicate_session``).
    3. **Never crop the anchor / non-eviction** — the trial fusion is computed
       and :func:`window_arm_retention` is asked (with the RUN's
       ``max_chunks_per_session``, i.e. the window the reader actually gets)
       whether ANY base-window SESSION would lose its last visible slot; if one
       would, the pick is rejected (``evicts:<session_id>``). Granularity is
       SESSION, not slot — see :data:`DEFAULT_TEMPORAL_LEG_NON_EVICTION` for why
       slot-level would make the arm inert everywhere.

    Why the guard is the arm's GATE and not a caveat on it: measured at the
    shipped geometry (``window=12``, ``limit=2`` → ceiling 10) the UNGUARDED
    tail splice is **net-zero on distinct evidence sessions and can trade a
    gold session for a non-gold one** — ``BASE distinct 3 / FUSED distinct 3``,
    ``EVICTED ['b1','c1']``, the sole representatives of two sessions (#10538
    comment ``6574179020``). The canonical expansion implementations forbid
    exactly that (*"if a parent is truncated before its relevant child, the
    expansion can perversely remove the very evidence that caused the
    retrieval hit"*), and the comparable whose benchmark lead this issue cites
    keeps source-episode material in its own bounded scope rather than
    injecting it into the fact ranking. The guard is that rule, enforced.

    ``placement="head"`` is EXEMPT: it is the rank-0 COUNTERFACTUAL the module
    docstring parks on the eval lane, and it exists precisely to MEASURE the
    displacement this guard forbids. Guarding it would answer the placement
    question by construction and destroy the counterfactual's only purpose. It
    is never the shipped arm, so no number produced under it ships.

    ``guard_trace`` — optional mutable mapping the caller supplies to receive
    the guard's telemetry (this module never reads it): ``{"on": bool,
    "applied": bool, "targeting": …, "rejected": [{"id": …, "session": …,
    "reason": …}], "accepted": […]}}``. ``applied`` is the ONLY field a
    receipt should read to decide whether the guard ran on a row (``on`` is the
    resolved switch, which is true even on the anchor arm and the head
    counterfactual where the guard does not run). An out-parameter on purpose:
    the ``(fused_id_order, picks)`` return tuple is unpacked by a large pinned
    test surface, so widening it would break callers for a diagnostic.

    ``max_chunks_per_session`` — the reader's raw-chunk cap, and it must be the
    RUN's cap, not this module's default: the guard judges eviction on the
    window the reader actually receives (``retrieve.py`` fuses and THEN dedups
    with ``--max-chunks-per-session``), so a guard measuring a different cap
    than the run's would admit a pick that deletes a session from the real
    window — the false PASS this guard exists to prevent. ``None`` is the
    pre-reader view (no cap) and is for measurement only.

    Returns ``(fused_id_order, picks)`` — ``picks`` is empty when the leg
    is inert, or when the guard rejected every pick, in which case
    ``fused_id_order`` is the untouched candidate order.
    """
    order_ids = [c["id"] for c in candidates]
    # Clamp the budget so no knob value can push the picks back to leg ranks
    # 0..N (the rank-0 counterfactual): at most a third of the window, and at
    # least one pick, so ``ceiling`` leaves both a head and a tail for
    # ``window >= 2``. At ``window == 1`` there is no room for a head and
    # the single pick necessarily replaces the only visible item — the
    # documented boundary (``window=1`` is only reachable by an explicit
    # ``--tr-top-k 1``, which no pinned arm uses).
    limit = effective_promotion_budget(window, limit)
    # Fixed promotion ceiling: the picks live strictly BELOW the head slice,
    # and the head slice keeps its base order — so a pick cannot be
    # double-counted in the fused leg list and cannot outrank the head.
    ceiling = max(window - limit, 0)
    head_excl = 0 if placement == "head" else ceiling
    #: #10538: which arm supplied the picks. The guard governs the WINDOW arm
    #: (``"window"``); the anchor arm is the #2976 path measured AS-IS, and
    #: re-scoping its behaviour here would silently move a published table for
    #: an arm no ruling covers — the same reason a fix carries its OWN
    #: mechanism-level test rather than a batch number.
    _targeting: dict[str, str] = {}
    picks = temporal_leg_order(
        candidates, anchors=anchors, limit=limit, bucket_cap=bucket_cap,
        head=head_excl, date_window=date_window, window=window,
        targeting_trace=_targeting)
    if not picks or weight <= 0:
        if guard_trace is not None:
            guard_trace.update({"on": bool(non_eviction), "applied": False,
                                "targeting": _targeting.get("targeting"),
                                "rejected": [], "accepted": []})
        return order_ids, []

    def _fuse(selected: list[str]) -> list[str]:
        if placement == "head":
            leg = [(pid, 0.0) for pid in selected]
        else:
            leg = ([(c["id"], 0.0) for c in candidates[:ceiling]]
                   + [(pid, 0.0) for pid in selected])
        fused = rrf_fusion(
            [[(pid, 0.0) for pid in order_ids], leg],
            strategy_names=["semantic", "temporal"],
            weights={"temporal": weight},
        )
        known = set(order_ids)
        return [pid for pid in fused if pid in known]

    # #10538: the non-eviction guard (owner ruling 2026-11-07) — the tail arm
    # may widen the visible evidence-session SET, never trade one for another.
    # Scoped to the DATE-WINDOW arm (``_targeting == "window"``) for the reason
    # stated above it; ``placement="head"`` is exempt because it is the
    # counterfactual that MEASURES the displacement this guard forbids.
    _guard_runs = (non_eviction and placement != "head"
                   and _targeting.get("targeting") == "window")
    if _guard_runs:
        picks = _non_eviction_filter(
            candidates, picks, window=window, fuse=_fuse, trace=guard_trace,
            max_chunks_per_session=max_chunks_per_session)
        if not picks:
            return order_ids, []
    elif guard_trace is not None:
        guard_trace.update({"on": bool(non_eviction), "applied": False,
                            "targeting": _targeting.get("targeting"),
                            "rejected": [], "accepted": list(picks)})

    return _fuse(picks), picks


def _non_eviction_filter(
    candidates: list[dict],
    picks: list[str],
    *,
    window: int,
    fuse,
    trace: dict | None = None,
    max_chunks_per_session: int | None = DEFAULT_MAX_CHUNKS_PER_SESSION,
) -> list[str]:
    """Drop the picks that would cost the reader window an evidence SESSION.

    The three conditions, and why they are the ruling's own words, are in
    :func:`temporal_leg_fusion_order`'s docstring. This function only
    MECHANICS them; it introduces no threshold of its own. ``window`` is the
    reader's ``effective_top_k`` — the surface :func:`window_arm_retention`
    measures — because the guard must judge the arm on what the READER
    receives (post-chunk-cap), not on the pre-cap splice index space.

    A rejected pick is DROPPED, in the leg's own pick order, and the remaining
    picks are still evaluated — against the base window, which no acceptance
    may narrow, so a rejection can never make a later pick look harmless.
    """
    session_of = {c["id"]: str(c.get("session_id") or "") for c in candidates}
    base_window = _reader_order(
        candidates, [c["id"] for c in candidates],
        max_chunks_per_session=max_chunks_per_session,
    )[:max(int(window), 0)]
    visible_sessions = {session_of[i] for i in base_window} - {""}

    accepted: list[str] = []
    rejected: list[dict] = []
    claimed: set[str] = set()
    for pid in picks:
        sid = session_of.get(pid, "")
        reason = None
        if sid and sid in visible_sessions:
            # (1) dedupe to unique sessions: the reader already holds this
            # source, so the slot buys nothing new.
            reason = "session_already_visible"
        elif sid and sid in claimed:
            # (2) per-session cap: one session is one delivery.
            reason = "duplicate_session"
        else:
            trial = [*accepted, pid]
            retention = window_arm_retention(
                candidates, fuse(trial), picks=trial, window=window,
                max_chunks_per_session=max_chunks_per_session)
            if retention["deleted_sessions"]:
                # (3) never crop the anchor — the slot is not spare.
                reason = "evicts:" + ",".join(retention["deleted_sessions"])
        if reason is not None:
            rejected.append({"id": pid, "session": sid, "reason": reason})
            continue
        accepted.append(pid)
        if sid:
            claimed.add(sid)
    if trace is not None:
        trace.update({"on": True, "applied": True, "targeting": "window",
                      "rejected": rejected, "accepted": accepted})
    return accepted


def _reader_order(
    candidates: list[dict],
    order: list[str],
    *,
    max_chunks_per_session: int | None,
) -> list[str]:
    """The id order a READER actually receives for ``order`` — ``dedup_pool`` applied.

    ⛔ THE HARNESS DEDUPS **AFTER** IT FUSES (``tools/longmem_eval/retrieve.py``:
    fusion reassigns ``annotated``, and the next statement is ``pool =
    _dedup_pool(annotated, …)``; ``"hits": pool``). So the fused index space the
    splice works in is NOT the index space the reader — and therefore
    ``recall_all@5`` — grades. On a CHUNK-FREE pool the two are identical
    (``dedup_pool`` is a no-op when no hit is a raw ``session-transcript``
    chunk, so points/turn points are never capped); on a CHUNK-BEARING pool they
    are not, and a pick spliced at fused rank 10 can land inside the reader's
    top 5 once the raw chunks above it are capped.

    Ids the caller's order carries that ``candidates`` does not know are passed
    through (they carry no ``point_kind``, so they are never capped) — this
    function must never silently DROP a slot, which would misreport the window.
    """
    if max_chunks_per_session is None:
        return list(order)
    by_id = {c["id"]: c for c in candidates}
    hits = [by_id.get(pid, {"id": pid}) for pid in order]
    if not any(is_raw_chunk(h) for h in hits):
        return list(order)
    return [h["id"] for h in
            dedup_pool(hits, max_chunks_per_session=max_chunks_per_session)]


def window_arm_retention(
    candidates: list[dict],
    fused_ids: list[str],
    *,
    picks: tuple[str, ...] | list[str] = (),
    window: int,
    limit: int = DEFAULT_TEMPORAL_LEG_LIMIT,
    max_chunks_per_session: int | None = DEFAULT_MAX_CHUNKS_PER_SESSION,
) -> dict:
    """Measure what a promotion DID to the reader window — retention, not order.

    WHY THIS EXISTS (#10538). The fused caller above is a **post-cut splice**: it
    re-orders ids AFTER the reader's membership cut (``retrieve.py``'s additive-
    rerank contract — *"the semantic leg still supplies the candidate membership
    … It NEVER filters"*). A tail splice therefore moves items INSIDE a fixed
    window; it cannot add a piece without displacing the one already there. So
    the only questions that can be asked of the arm are window-level ones —
    *did the set of evidence sessions the reader can see get wider, or merely
    different?* — and until this function existed the repo measured neither.
    Every figure the arm was ever justified with lived in issue prose and in
    ``/tmp`` probe scripts that die with the box (see
    ``tools/longmem_eval/window_arm_baseline.py``, which is the reproducible
    instrument over this function).

    ⛔ **This function MEASURES; it does not itself decide.** Read the scope
    exactly, because this paragraph used to promise the opposite and a stale
    "it does not gate" claim is how a bypass gets argued later: the question it
    was written under — whether an arm MAY pay for a slot by evicting base
    evidence inside the reader window (``window-exit`` vs ``ab-head-ab``) —
    **has been answered**. The owner ruled ``window-exit`` on 2026-11-07
    (#10538, comment ``6711158625``): expansion may WIDEN the window and may
    never remove evidence the reader already had, with the three conditions
    stated as conditions of the fix.

    So exactly ONE consumer branches on a value returned here —
    :func:`_non_eviction_filter`, which refuses a promotion when
    ``deleted_sessions`` is non-empty, under that ruling and on that arm
    only (see :func:`temporal_leg_fusion_order`'s scope declaration).
    Everything else here is still REPORT-ONLY, and turning a **different**
    field into a gate — ``net``, ``evicted_ids``, ``additive`` as a proxy for a
    rule this ruling does not state — still needs its own ruling, because none
    of them says which session left. :func:`_non_eviction_filter` is
    deliberately written against ``deleted_sessions`` for that reason: it is
    the one field whose definition ("a session absent from the fused window")
    IS the ruling's words, so the gate cannot drift from the measurement it is
    justified by.

    ``candidates`` is the caller's semantic pool in rank order; ``fused_ids`` is
    what :func:`temporal_leg_fusion_order` returned; ``picks`` is its second
    return value (default ``()`` — an inert arm). ``window`` is the reader's
    ``effective_top_k``.

    ⛔ **THE READER'S POOL IS THE DEDUPED ONE, AND THIS FUNCTION MODELS IT**
    (``max_chunks_per_session``, default the product cap
    ``tortoise.retrieval.DEFAULT_MAX_CHUNKS_PER_SESSION``). The harness fuses and
    THEN dedups — ``retrieve.py`` reassigns ``annotated`` to the fused order,
    then ``pool = _dedup_pool(annotated, …)``, then ``"hits": pool`` — so the
    fused index space this arm splices into is NOT the index space
    ``recall_all@5`` grades (``hits[:5]``). ``dedup_pool`` caps RAW
    ``session-transcript`` chunks per session at that number and never caps
    point/turn points, so on a **chunk-free** pool the two index spaces are
    identical and every field below is unchanged; on a **chunk-bearing** pool
    the cap removes slots ABOVE a pick, which lifts it into (or out of) the
    graded top 5. Pass ``max_chunks_per_session=None`` to measure the raw splice
    geometry instead — that is the PRE-reader view and must never be published as
    what the metric grades.

    Returns a JSON-serialisable mapping:

    ``base_ids`` / ``fused_ids``   the two reader windows (``[:window]`` of the
                                   DEDUPED orders — what the reader receives)
    ``reader_pool_depth`` / ``reader_pool_depth_fused``  how many slots survived
                                   the chunk cap in each order, so a shift in the
                                   fields below can be attributed to the cap
                                   rather than to the arm
    ``base_sessions`` / ``fused_sessions``  DISTINCT real ``session_id`` values
                                   in them. A hit with NO ``session_id`` is not a
                                   session — the same fail-closed rule
                                   :func:`_session_clustered` applies — so it
                                   counts toward neither, and its slots are
                                   reported SEPARATELY PER WINDOW as
                                   ``base_sessionless_slots`` /
                                   ``fused_sessionless_slots``. Collapsing every
                                   absent id into one bucket would let "the
                                   window dropped a session" fire on hits that
                                   belong to no session at all; reporting one
                                   count for both windows would under-report,
                                   because a sessionless hit CAN be promoted
                                   (it carries a ``session_date``, so it forms
                                   its own ``(date, "")`` bucket).
    ``net``                        ``fused_sessions - base_sessions`` — a
                                   CARDINALITY delta: ``> 0`` widened, ``< 0``
                                   narrowed. ``0`` means only that the COUNT did
                                   not grow — it covers a swap AND an arm that
                                   never fired at all (the committed receipt
                                   carries 24 ``net: 0`` rows with
                                   ``fired: false`` and ``pick_ranks: []`` — 20
                                   in the ``by_pool`` sweep, 4 in the
                                   shallow-pool control), so read
                                   ``evicted_ids`` / ``pick_ranks`` to tell them
                                   apart. It is silent about WHICH sessions left,
                                   and it is NOT equivalent to ``additive`` in
                                   either direction (the receipt contains
                                   ``net 1`` with ``additive False`` and
                                   ``net 0`` with ``additive True``).
    ``evicted_ids`` / ``evicted_sessions``  base-window ids (and their real
                                   sessions) absent from the fused window
    ``deleted_sessions``           the sessions present in the base window and
                                   ABSENT from the fused one — a session is
                                   deleted when EVERY slot it had is displaced,
                                   and that is the harm this arm can do.
                                   Source-of-truth for ``additive``.
    ``evicted_sole_representatives``  the narrower, SLOT-level diagnostic: the
                                   evicted ids whose SESSION held exactly ONE
                                   base-window slot. ⚠️ The count is per
                                   ``session_id``, NOT per the ``(session_date,
                                   session_id)`` bucket ``held_buckets`` uses:
                                   "sole representative" is a property of the
                                   SESSION's visibility, so a session split
                                   across two raw date strings counts as TWO
                                   slots and NEITHER of its evicted ids is a
                                   sole representative. (Expect LESS from this
                                   field than a bucket-keyed count would give —
                                   not more; pinned by
                                   ``test_the_sole_representative_count_is_per_session_not_per_bucket``.)
                                   ⚠️ It is NEITHER sufficient NOR necessary for
                                   a deletion — read ``deleted_sessions`` for the
                                   verdict:
                                   * not necessary: a session holding two slots
                                     disappears when both are displaced, with no
                                     sole representative at all (measured on the
                                     ``two_slot_tail_session`` pool);
                                   * not sufficient: a sole representative can be
                                     displaced while its session stays visible
                                     through a DIFFERENT bucket. The enabler is
                                     ``held_buckets``' exclusion, which is keyed
                                     on the ``(session_date, session_id)``
                                     BUCKET: a promotion may therefore re-add a
                                     session the reader already holds under a
                                     different raw date. That shape is reachable
                                     through the shipped caller — and a
                                     bucket-keyed sole-rep count reaches it too,
                                     so this count's key is not what causes it.
    ``additive``                   ``not deleted_sessions`` — no session leaves
                                   the reader window. It is SESSION-level, so it
                                   says nothing about displaced NON-sessions: a
                                   displaced sessionless base slot leaves it
                                   ``True`` and is reported only in
                                   ``evicted_ids`` and the
                                   ``base_sessionless_slots`` /
                                   ``fused_sessionless_slots`` pair. An INERT arm
                                   also reports ``True`` trivially (it displaced
                                   nothing), so pair it with ``pick_ranks`` and
                                   ``evicted_ids`` before reading it as a win.
    ``pick_ranks``                 each pick's index in the caller's FULL fused
                                   order AFTER the reader's chunk cap — note
                                   this is the reader's order, not the returned
                                   ``fused_ids`` slice (which is ``[:window]``);
                                   a pick at index ``>= window`` is outside the
                                   reader window, so the two are reported apart
    ``pick_ranks_before_dedup``    the same picks' indices in the caller's FULL
                                   ``fused_ids`` — i.e. where the SPLICE put
                                   them, before the chunk cap. Equal to
                                   ``pick_ranks`` on a chunk-free pool; when
                                   they differ, the difference IS the cap's
                                   work on the graded surface (a pick spliced at
                                   rank 10 can be graded at rank 3)
    ``picks_dropped_by_reader_dedup``  picks present in the caller's
                                   ``fused_ids`` but removed by the chunk cap —
                                   the promotion paid for a slot the reader never
                                   receives. Reported apart from
                                   ``picks_absent_from_fused`` because the two
                                   are different faults (a lost pick vs. a
                                   capped one) and an emptied ``pick_ranks``
                                   cannot tell them apart
    ``picks_absent_from_fused``    picks that appear in NO position of the
                                   caller's ``fused_ids``. Reported rather than
                                   silently dropped: an emptied ``pick_ranks``
                                   would otherwise be indistinguishable from an
                                   INERT arm, i.e. a lost pick would read as no
                                   harm. Unreachable through the shipped caller
                                   (it filters to ids it knows), reachable by any
                                   direct caller.
    ``ceiling``                    ``max(window - effective_promotion_budget(
                                   window, limit), 0)`` — where a TAIL-placement
                                   pick STARTS in the PRE-CAP fused order. It is
                                   computed from ``window``/``limit`` alone (this
                                   function has no ``placement`` argument), so
                                   under ``placement='head'`` the picks start at
                                   rank 0 and this is the tail ceiling the
                                   budget WOULD use, not the head row's pick
                                   position — read ``pick_ranks`` for that. A
                                   pick at rank ``>= 5`` can never be graded by
                                   ``recall_all@5``. The clamped budget is
                                   recomputed from ``limit``, never from
                                   ``len(picks)``, so an inert arm reports the
                                   geometry it WOULD have used. ⛔ It is the
                                   SPLICE geometry, not the reader's rank: read
                                   ``pick_ranks`` (post-cap) for the graded
                                   position and ``pick_ranks_before_dedup`` to
                                   see the splice itself.
    ``graded_by_recall_all_at_5``  how many picks land at a READER index
                                   ``< 5`` of the DEDUPED pool —
                                   ``recall_all@5`` grades ``hits[:5]``
                                   (``retrieve.py``: ``top = hits[:k]``), and
                                   this field counts ``pick_ranks`` (post-cap)
                                   ``< 5``. It carries NO ``window`` term: the
                                   graded surface has no reader-window
                                   truncation, so a pick the window hides can
                                   still be graded — visibility is its own
                                   question, reported by
                                   ``picks_outside_window``. ⚠️ The surface
                                   DOES carry a ``top_k`` cut that this field
                                   does not model: with rerank on, ``hits`` is
                                   ``pool[:top_k]`` or the selected-only list,
                                   and on the pool-only arm ``pool =
                                   pool[:top_k]`` (``retrieve.py`` R6 block),
                                   so at ``top_k < 5`` a rank-3 pick is not
                                   gradable. Every number in the receipt is
                                   measured on the plain path (no rerank, no
                                   pool override), where ``hits`` IS the
                                   deduped pool untruncated.
    ``picks_outside_window``       picks whose index is ``>= window`` (invisible
                                   to the reader at all)
    ``base_graded_evicted``        evicted ids that were themselves inside
                                   ``[:5]`` — a direct top-5 loss
    """
    width = max(int(window), 0)
    session_of = {c["id"]: str(c.get("session_id") or "") for c in candidates}
    # ⛔ TWO INDEX SPACES, KEPT APART ON PURPOSE. The splice works in the caller's
    # PRE-cap fused order; the reader (and `recall_all@5`) sees the POST-cap one.
    # `splice_rank_of` is geometry, `rank_of` is what the metric grades.
    splice_rank_of = {pid: i for i, pid in enumerate(fused_ids)}
    reader_fused_all = _reader_order(
        candidates, fused_ids, max_chunks_per_session=max_chunks_per_session)
    reader_base_all = _reader_order(
        candidates, [c["id"] for c in candidates],
        max_chunks_per_session=max_chunks_per_session)
    rank_of = {pid: i for i, pid in enumerate(reader_fused_all)}
    base_ids = reader_base_all[:width]
    fused_window = reader_fused_all[:width]
    fused_set = set(fused_window)

    base_sessions = {session_of.get(i, "") for i in base_ids} - {""}
    fused_sessions = {session_of.get(j, "") for j in fused_window} - {""}
    deleted_sessions = sorted(base_sessions - fused_sessions)

    slots_per_session: dict[str, int] = {}
    for i in base_ids:
        key = session_of.get(i, "")
        if not key:
            continue
        slots_per_session[key] = slots_per_session.get(key, 0) + 1

    evicted_ids = [i for i in base_ids if i not in fused_set]
    evicted_sole = [i for i in evicted_ids
                    if slots_per_session.get(session_of.get(i, ""), 0) == 1]
    pick_list = [p for p in picks if p in rank_of]
    return {
        "window": width,
        "reader_pool_depth": len(reader_base_all),
        "reader_pool_depth_fused": len(reader_fused_all),
        "base_ids": base_ids,
        "fused_ids": fused_window,
        "base_sessions": len(base_sessions),
        "fused_sessions": len(fused_sessions),
        "net": len(fused_sessions) - len(base_sessions),
        "base_sessionless_slots": sum(1 for i in base_ids
                                      if not session_of.get(i, "")),
        "fused_sessionless_slots": sum(1 for j in fused_window
                                      if not session_of.get(j, "")),
        "evicted_ids": evicted_ids,
        "evicted_sessions": sorted({session_of[i] for i in evicted_ids
                                    if session_of.get(i, "")}),
        "deleted_sessions": deleted_sessions,
        "evicted_sole_representatives": evicted_sole,
        "additive": not deleted_sessions,
        "pick_ranks": [rank_of[p] for p in pick_list],
        "pick_ranks_before_dedup": [splice_rank_of[p] for p in pick_list],
        "picks_absent_from_fused": [p for p in picks
                                    if p not in splice_rank_of],
        "picks_dropped_by_reader_dedup": [p for p in picks
                                          if p in splice_rank_of
                                          and p not in rank_of],
        "picks_outside_window": [p for p in pick_list if rank_of[p] >= width],
        "ceiling": max(
            width - effective_promotion_budget(width, int(limit)), 0),
        "graded_by_recall_all_at_5": sum(
            1 for p in pick_list if rank_of[p] < 5),
        "base_graded_evicted": [i for i in evicted_ids
                                if base_ids.index(i) < 5],
    }
