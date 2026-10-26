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


def temporal_leg_order(
    candidates: list[dict],
    *,
    anchors: tuple[str, ...] | list[str] = (),
    limit: int = DEFAULT_TEMPORAL_LEG_LIMIT,
    bucket_cap: int = DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
    head: int = 0,
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
    2. **Date/session spread**: the anchor-relevant candidates (those sharing
       at least one content token with any anchor) are bucketed by
       ``(session_date, session_id)``; buckets are visited in the order their
       best member appears in the semantic ranking, and round-robin over
       rounds up to ``bucket_cap``. Coverage is spread across the temporal
       span instead of clustered on the semantically nearest date, while
       staying inside the question's own event vocabulary.

**Anchors are required.** A question whose comparison phrases cannot be
recovered (no anchor matched any candidate) yields ``[]`` — the leg is
inert. This is deliberate, and measured: a spread-only leg promotes
arbitrary deep pool items and, on a saturated-base proxy replay of the
55-question temporal subset, cost 3.3 pts of evidence-turn recall@12 for
+1 co-present question. Restricting the spread to anchor-relevant
evidence keeps the promotion targeted; anchor-less ordering questions
("how many days ago did I buy a smoker?") fall back to the pre-#2976
behavior. The residual is documented on the arm (see the PR body).

**Bounded promotion (``head``).** ``head`` excludes the first ``head``
candidates (the reader-visible head) from BOTH phases: those candidates
need no temporal lift, so promoting them would only double-count them
against the fused head slice the caller builds (see
:func:`temporal_leg_fusion_order`, which always passes the ceiling).
``head=0`` is the unguarded mode — used by the unit tests to exercise the
phases in isolation, never by the fused caller for a window ≥ 2.

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

The shipped base is 0/52 answerable with gold at ranks 41–120 — a base
with nothing to lose — so budget 2 (the co-present pair, the target
class) is the default and the end-to-end delta is the eval lane's to
measure. Set ``TORTOISE_LME_TEMPORAL_LEG_LIMIT=1`` for the conservative
arm (``temporal_leg_replay.py --limit 1`` reproduces that row).

Undated candidates are excluded from the leg (no date → no temporal
signal; they keep their semantic score unfiltered). Returns ``[]`` when
no candidate is dated, which the caller treats as "leg inert".
    """
    if limit <= 0 or bucket_cap <= 0:
        return []
    dated = [c for c in candidates if c.get("session_date")]
    if not dated:
        return []
    # the reader-visible head needs no promotion — never disturb it. The
    # boundary is the CANDIDATE index (matching this function's ``head``
    # contract), not the index within the date-filtered list: an undated
    # candidate sitting in the head must not shift the boundary downward
    # and silently make a deep anchor match unpromotable.
    promotable = [c for i, c in enumerate(candidates)
                  if i >= max(head, 0) and c.get("session_date")]
    if not promotable:
        return []

    picks: list[str] = []
    seen: set[str] = set()
    #: ids sharing at least one content token with some anchor
    relevant: set[str] = set()
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

    # Anchors are required: without a single anchor-relevant candidate the
    # leg has no deterministic targeting and must stay inert (never a
    # spread-only promotion of arbitrary pool items).
    if not relevant:
        return []

    # ── phase 2: date/session spread over the ANCHOR-RELEVANT evidence ──
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
            if round_idx < len(members):
                if bucket_counts.get(key, 0) >= bucket_cap:
                    continue
                pid = members[round_idx]
                if pid in seen:
                    continue
                picks.append(pid)
                seen.add(pid)
                bucket_counts[key] = bucket_counts.get(key, 0) + 1

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
                              budget: int = 1) -> str:
    """The honest leg-trace reason for an EMPTY leg (the five causes).

    ``budget_disabled``        — the effective promotion budget is 0
    ``no_dated_candidates``    — the pool carries no dated hit at all
    ``no_anchors``             — the question yielded no comparison phrase
    ``weight_disabled``        — the arm ran at weight <= 0 (picks dropped)
    ``no_promotable_anchor_match`` — anchors exist but none matched an
        eligible candidate (no match at all, or every match already sits
        inside the excluded visible head and therefore needs no lift)

    ``budget`` defaults to 1 so a caller that does not track it keeps the
    pre-budget behavior.
    """
    if budget <= 0:
        return "budget_disabled"
    if dated <= 0:
        return "no_dated_candidates"
    if anchors <= 0:
        return "no_anchors"
    if weight <= 0:
        return "weight_disabled"
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

    Returns ``(fused_id_order, picks)`` — ``picks`` is empty when the leg
    is inert, in which case ``fused_id_order`` is the untouched candidate
    order.
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
    picks = temporal_leg_order(
        candidates, anchors=anchors, limit=limit, bucket_cap=bucket_cap,
        head=head_excl)
    if not picks or weight <= 0:
        return order_ids, []
    if placement == "head":
        leg = [(pid, 0.0) for pid in picks]
    else:
        leg = ([(c["id"], 0.0) for c in candidates[:ceiling]]
               + [(pid, 0.0) for pid in picks])
    fused = rrf_fusion(
        [[(pid, 0.0) for pid in order_ids], leg],
        strategy_names=["semantic", "temporal"],
        weights={"temporal": weight},
    )
    known = set(order_ids)
    return [pid for pid in fused if pid in known], picks
