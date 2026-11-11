"""#2570 (C3-4 / #2519 P3) — stratified window reconciliation rules.

The P3 mechanism of the #2519 program (`docs/scoping/2026-09-08-2519-
coverage-loop.md` §3(c), §4 P3): for the **failing reader class** — a
non-``_abs`` multi-session / single-session-preference question on which
the reader either abstained or was judged wrong while the
evidence-bearing material was NOT fully inside its window
(``reader_surface@k < 1.0``) — re-run the question with a **raised reader
window** under a **per-question token cap**, and report the result as a
**LABELED VARIANT**.

⚠️ THE OFFICIAL NUMBER IS NEVER TOUCHED (w7 D3 discipline). The official
``recall_all@k`` grades the ONE-SHOT pool's top-k (``session_recall@k``
over the deduped one-shot pool); it is a retrieval metric and a reader
re-run cannot move it. Nothing in this module feeds the official
aggregate — the re-run's own retrieval/reader/judge outputs are recorded
under distinct outcome keys and aggregated only by
:func:`reconciliation_variant_rows`, whose every row is explicitly
labeled. The plan's §3(c) semantics caveat is the reason this arm exists
as a *measurement* and not as an adoption candidate.

This module is PURE (no graph, no LLM, no env reads): the failing-class
predicate, the raised-window arithmetic, the per-question budget
admission rule, the raw-token extraction, and the variant aggregation.
``tools/longmem_eval/run.py`` wires it; ``tools/longmem_eval/w7_publish.
py`` publishes it.

## The failing class (detection from OUTCOME fields only)

``failing_class_reason`` reads only fields the one-shot question already
produced (``question_id``, ``question_type``, the judge ``label``, the
reader ``hypothesis``, ``reader_surface@k``) — no extra model call, no
gold-label access beyond what the graded outcome already carries:

  * ``abstention_non_abs_msr_preference`` — the reader abstained (the
    product's own ``tortoise.reader._looks_abstained`` vocabulary,
    clause-scoped) on a non-``_abs`` question of the failing type class.
    An abstention IS the "the reader did not see enough" signal, so this
    reason fires independently of the surface value (the issue's O).
  * ``judge_miss_reader_surface_lt_1`` — the judge said wrong AND
    ``reader_surface@k < 1.0`` (evidence-bearing content was still
    outside the reader window). A ``None`` surface (vacuity — no
    evidence-bearing content exists) is UNKNOWN, never ``< 1.0``: the
    window cannot be shown to be the cause, so the arm does not spend on
    it (fail-safe, the #1745 default posture).

## The raised window

``raised_window`` scales the ONE-SHOT resolved knobs by a pinned factor
(:data:`DEFAULT_WINDOW_RAISE_FACTOR`): ``top_k`` (the rank window handed
to ``assemble_context``), ``context_item_cap`` (C1's item cap) and
``max_context_tokens`` (the 8000-token assembly budget). All three are
fingerprinted/recorded by the caller, so the raised window a variant was
produced under is reconstructable. Note the raise does NOT deepen the
retrieval pool: it is a WINDOW change, which is exactly the variable P3
measures. ⛔ That holds at the shipped defaults (base ``top_k`` 20 → 40,
pool 120) but NOT for every configuration — the reader window is
``pool[:effective_top_k]``, so the effective raise is
``min(raised_top_k, pool_depth)`` and it saturates once ``2 × base_top_k``
exceeds ``DEFAULT_POOL_SIZE`` (120, i.e. above a base of 60) or when
``TORTOISE_LME_POOL_SIZE`` is lowered. The recorded triple is the REQUESTED
window; above that boundary the pool, not the raise, is the binding
constraint — read a null ``reader_surface@k`` delta there as "this dimension
was clipped", not as "the window is not the cause".

## The per-question budget cap

The cap bounds the re-run's OWN marginal spend (raw tokens; priced at
report time — ``costing.py`` is deliberately report-time-only, so no USD
ever enters the runner). Admission is pre-computed from the RAISED
WINDOW via :func:`marginal_bound`: the raised reader context budget plus
the two declared headroom constants (prompt scaffolding, completion) plus
the question's one-shot judge tokens. ⛔ The bound is denominated on the
window the re-run is ALLOWED to fill, never on what the one-shot reader
happened to spend — an under-filled one-shot context does not cap the
raised pass, so a bound derived from observed spend is not a bound at
all (a re-ask of a 400-token one-shot context may legally consume the
whole 16 000-token raised budget).

The actual marginal spend is measured after the pass via
:func:`envelope_delta`, and BOTH honesty flags ride the outcome: a pass
whose ACTUAL spend exceeded the cap is recorded as ``cap_exceeded``, and
one that exceeded the bound is recorded as ``bound_exceeded`` — the
latter is what would falsify the bound on a live run instead of leaving
it an assertion. Admission never reads the ingest burn — the cap is a
re-run budget, not a per-question whole-run budget.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/window_reconciliation.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]})"
    )

from typing import Any

#: The failing question-type class (the #2519 miss cohort: MSR +
#: single-session-preference). Other types are never re-run.
FAILING_CLASS_QUESTION_TYPES: tuple[str, ...] = (
    "multi-session",
    "single-session-preference",
)

#: Detection reasons (distinct so the census can attribute a fire to the
#: abstention shape vs the low-surface judge-miss shape).
REASON_ABSTENTION = "abstention_non_abs_msr_preference"
REASON_JUDGE_MISS_LOW_SURFACE = "judge_miss_reader_surface_lt_1"

#: The pinned window-raise factor (a WINDOW change, never a pool change).
DEFAULT_WINDOW_RAISE_FACTOR = 2

#: Per-question cap on the re-run's marginal LLM tokens (env
#: ``TORTOISE_LME_WINDOW_RECONCILIATION_CAP`` / CLI). Sized so a raised
#: (2×) re-ask of a full 8000-token reader context — ≈16k prompt tokens
#: plus completion — plus the judge pass fits with headroom.
DEFAULT_RECONCILIATION_TOKEN_CAP = 24000

#: The bound's two declared headrooms, on top of the raised reader-context
#: budget. They are CONSTANTS rather than measurements on purpose: the
#: raised pass's own prompt scaffolding and completion are not knowable
#: before it runs, so the bound states what it assumes instead of silently
#: borrowing the one-shot's shape. ``bound_exceeded`` on any outcome is the
#: live signal that the assumption was too small.
DEFAULT_RECONCILIATION_PROMPT_HEADROOM = 1000
DEFAULT_RECONCILIATION_COMPLETION_HEADROOM = 2000

#: The ceiling on the RAISE, not on the window — ``_raise_dimension`` is
#: ``max(base, min(base * factor, maximum))``, so a base already above the
#: constant keeps its own window (the raise is what is clamped, deliberately:
#: a clamp that could LOWER a dimension would invert the raise). A window
#: beyond this is not a measurement of the same mechanism any more (it is a
#: different instrument) — the clamp is a guard, not a knob.
MAX_RAISED_CONTEXT_ITEM_CAP = 400
MAX_RAISED_TOP_K = 400


def is_failing_class_type(question_type: str | None) -> bool:
    """True for the question types the failing-class predicate may fire on."""
    return (question_type or "") in FAILING_CLASS_QUESTION_TYPES


def is_abs_question(question_id: str | None) -> bool:
    """The official ``_abs`` suffix convention (abstention questions are
    excluded from the failing class entirely — their reader MUST abstain)."""
    return isinstance(question_id, str) and "_abs" in question_id


def recall_at_k_value(recall: Any, k: int | str = 5) -> float | None:
    """The ``session_recall@k`` fraction for ``k``, falling back to any
    recorded k.

    ⛔ Unlike ``reader_surface@k`` this metric IS k-dependent, so a fallback
    reports a DIFFERENT k than the one requested. It is still the right
    behaviour here: without it, a run whose ``--k`` omits the hard-coded
    default reads ``None`` on BOTH sides, the drift loop skips every
    question, and the census publishes ``pool_drift_n: 0`` — an EMPTY
    population reported as a measured "no drift". The aggregate therefore
    also publishes ``n_pool_drift_pairs``, so an unread population is
    visible as such rather than as a zero.
    """
    if not isinstance(recall, dict):
        return None
    v = recall.get(str(k))
    if v is None and recall:
        v = next(iter(recall.values()))
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def reader_surface_value(reader_surface: Any, k: int | str = 5,
                         ) -> float | None:
    """The ``reader_surface@k`` value for ``k`` from an outcome's dict.

    The metric is k-INDEPENDENT by construction (the reader context is one
    list for every k — see ``retrieve.py``), so a missing record for the
    requested k falls back to any recorded k before giving up. Returns
    ``None`` for a malformed/absent/``None`` record (the vacuity case —
    never coerced to a number).
    """
    if not isinstance(reader_surface, dict):
        return None
    v = reader_surface.get(str(k))
    if v is None and reader_surface:
        v = next(iter(reader_surface.values()))
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def failing_class_reason(*, question_id: str | None,
                         question_type: str | None,
                         label: bool | None,
                         hypothesis: str | None,
                         reader_surface: Any = None,
                         k: int | str = 5) -> str | None:
    """The failing-class reason for a graded one-shot outcome, or ``None``.

    Pure. ``None`` means "not in the failing class — do not re-run". See
    the module docstring for the two reasons and their guards.
    """
    if is_abs_question(question_id):
        return None
    if not is_failing_class_type(question_type):
        return None
    if label is None:
        # Not graded (retrieval-only / reader or judge failure): no
        # outcome field to decide from — never fire.
        return None
    # Imported lazily: the product reader is the abstention VOCABULARY
    # authority (judge vocabulary ⊆ product, #2027) — a second, local
    # vocabulary here is how the two drift apart.
    from tortoise.reader import _looks_abstained

    if hypothesis is not None and _looks_abstained(hypothesis):
        # The reader said it could not answer FROM WHAT IT SAW: the
        # window (or the pool) is the suspected cause — reason A fires
        # independently of the surface value (the issue's O).
        return REASON_ABSTENTION
    surface = reader_surface_value(reader_surface, k)
    if not label and surface is not None and surface < 1.0:
        # Judged wrong while evidence-bearing content was still outside
        # the window: the window is the suspected cause.
        return REASON_JUDGE_MISS_LOW_SURFACE
    return None


def raised_window(*, top_k: int, context_item_cap: int,
                  max_context_tokens: int,
                  factor: int = DEFAULT_WINDOW_RAISE_FACTOR) -> dict[str, int]:
    """The raised-window knob triple (all three are window dimensions).

    ``top_k`` and ``context_item_cap`` are clamped to the guard maxima so a
    pathological one-shot config cannot produce an unbounded window — but
    the clamp is applied as ``max(base, min(base*factor, MAX))``, NEVER as a
    bare ``min``. A bare ``min`` INVERTS the mechanism above the guard: base
    400 stays 400 (no raise at all) and base 500 is *reduced* to 400, so a
    run with ``--top-k 500`` would publish a DOWNGRADE as a "raised window"
    and read its ``reader_surface@k`` delta from it. Above the guard the
    dimension simply cannot be raised; the re-run still differs because the
    token budget has no such clamp.
    """
    if factor < 1:
        raise ValueError(f"raise factor must be >= 1, got {factor}")
    def _raise_dimension(base: int, maximum: int) -> int:
        base = max(1, int(base))
        return max(base, min(base * factor, maximum))

    return {
        "top_k": _raise_dimension(top_k, MAX_RAISED_TOP_K),
        "context_item_cap": _raise_dimension(context_item_cap,
                                            MAX_RAISED_CONTEXT_ITEM_CAP),
        "max_context_tokens": max(1, int(max_context_tokens)) * factor,
    }


# ── the per-question token budget (raw tokens; USD at report time) ──────

_STAGE_TOKEN_KEYS = ("prompt_tokens", "completion_tokens")


def stage_tokens(envelope: dict | None, *stages: str) -> int:
    """Raw prompt+completion tokens summed over the named stages of a
    usage envelope (``usage.py`` shape: ``by_stage[stage][provider][model]``).

    Malformed/absent envelopes yield 0 (never an exception — the arm must
    not be able to break a run over a metering shape).

    ⛔ A sum over a bucket whose provider returned NO usage block is a LOWER
    BOUND, not a measurement. Any caller that compares this against a cap or
    a budget MUST pair it with :func:`spend_is_unknown` — otherwise a pass
    whose spend was never measured reads as free (see that function).
    """
    if not isinstance(envelope, dict):
        return 0
    by_stage = envelope.get("by_stage")
    if not isinstance(by_stage, dict):
        return 0
    total = 0
    for stage in stages:
        providers = by_stage.get(stage)
        if not isinstance(providers, dict):
            continue
        for models in providers.values():
            if not isinstance(models, dict):
                continue
            for bucket in models.values():
                if not isinstance(bucket, dict):
                    continue
                for key in _STAGE_TOKEN_KEYS:
                    v = bucket.get(key)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        total += int(v)
    return total


def marginal_bound(envelope: dict | None, *,
                   raised_max_context_tokens: int,
                   prompt_headroom: int = (
                       DEFAULT_RECONCILIATION_PROMPT_HEADROOM),
                   completion_headroom: int = (
                       DEFAULT_RECONCILIATION_COMPLETION_HEADROOM)) -> int:
    """An upper bound on a re-run's marginal LLM tokens, denominated on
    the RAISED WINDOW rather than on what the one-shot reader spent.

    ``raised_max_context_tokens`` (the window the re-run may fill) + the
    two declared headrooms (prompt scaffolding, completion) + the
    question's one-shot judge tokens. The judge term is the one part that
    IS read from the envelope: the re-run's judge call has the same shape
    as the one-shot's, so its one-shot cost is a fair estimator.

    ⛔ Deliberately NOT ``factor × observed_reader_tokens``: the raise
    applies to the CAP, not to observed spend, so an under-filled
    one-shot context (a short preference question: ~450 tokens) admits a
    pass that may then consume the whole ~16 000-token raised budget —
    ≈13× the "bound". Derived from observed spend, the quantity is not an
    upper bound and the cap does not bound anything.

    ``raised_max_context_tokens`` is required (keyword-only) so no caller
    can quietly fall back to the one-shot shape. Ingest/extractor stages
    are excluded (the re-run does not ingest — the cap is a re-run budget,
    not a whole-run budget).
    """
    return (max(0, int(raised_max_context_tokens))
            + max(0, int(prompt_headroom))
            + max(0, int(completion_headroom))
            + stage_tokens(envelope, "judge"))


def envelope_delta(before: dict | None, after: dict | None) -> int:
    """The re-run's ACTUAL marginal reader+judge tokens (never negative:
    a collector that lost rows reports 0, not a credit)."""
    return max(0, stage_tokens(after, "reader", "judge")
               - stage_tokens(before, "reader", "judge"))


def spend_is_unknown(envelope: dict | None, *stages: str) -> bool:
    """Whether ANY call in the named stages lacked a usage block.

    ``usage.py`` keeps ``usage_present`` conservatively False and counts
    ``calls_without_usage`` when a provider response carried no usage block,
    deliberately — its own contract is that *unknown spend is never silently
    priced*. A token SUM over such a bucket is a lower bound on what was
    spent, so a caller certifying "within budget" from :func:`stage_tokens`
    without asking this reports an UNMEASURED pass as a free one.

    Absent/malformed envelopes report ``False`` (nothing known to be
    unknown); a caller that has no envelope at all must treat that case
    itself — see :func:`pass_spend_is_unmetered`, which does.

    ⛔ KNOWN UPSTREAM GAP (pre-existing, #11844): a provider usage block whose
    keys the sanitizer drops ENTIRELY is recorded with ``usage_present``
    True and zero tokens, so it is invisible here. This guard therefore makes
    the arm honest about everything the meter CAN report; it does not repair
    the meter.
    """
    if not isinstance(envelope, dict):
        return False
    by_stage = envelope.get("by_stage")
    if not isinstance(by_stage, dict):
        return False
    for stage in stages:
        providers = by_stage.get(stage)
        if not isinstance(providers, dict):
            continue
        for models in providers.values():
            if not isinstance(models, dict):
                continue
            for bucket in models.values():
                if not isinstance(bucket, dict):
                    continue
                if bucket.get("usage_present") is False:
                    return True
                if bucket.get("calls_without_usage"):
                    return True
    return False


def envelope_calls(envelope: dict | None) -> int:
    """How many metered calls the envelope's ``total`` block reports (0 when
    the envelope is absent — the collector returns ``None`` for a qid with no
    rows yet).

    ⛔ This is the TOTAL across every stage, and the question key is bound
    BEFORE ingest, so on a real run it is never 0 for an admitted pass even
    when the reader and judge were silent. Never use it to decide whether a
    READER/JUDGE pass was watched — use :func:`stage_calls`.
    """
    if not isinstance(envelope, dict):
        return 0
    total = envelope.get("total")
    if not isinstance(total, dict):
        return 0
    return _call_count(total.get("calls"))


def _call_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def stage_calls(envelope: dict | None, *stages: str) -> int:
    """How many calls the NAMED stages report, summed over providers/models.

    The distinction from :func:`envelope_calls` is load-bearing: the qid is
    keyed before ingest, so an envelope carrying only ``ingest`` rows proves
    the question ran, never that its reader/judge pass was watched.
    """
    if not isinstance(envelope, dict):
        return 0
    by_stage = envelope.get("by_stage")
    if not isinstance(by_stage, dict):
        return 0
    total = 0
    for stage in stages:
        providers = by_stage.get(stage)
        if not isinstance(providers, dict):
            continue
        for models in providers.values():
            if not isinstance(models, dict):
                continue
            for bucket in models.values():
                if isinstance(bucket, dict):
                    total += _call_count(bucket.get("calls"))
    return total


def pass_spend_is_unmetered(before: dict | None,
                            after: dict | None) -> bool:
    """Whether an ADMITTED re-run's marginal spend was never measured.

    Two distinct shapes, both of which must refuse to certify "within
    budget":

    1. **The reader/judge pass left no metering rows.** ``spend_is_unknown()``
       on ``None`` is False by design (nothing is *known* to be unknown), so
       an adapter carrying no sink would otherwise price the pass at 0. An
       admitted pass performs at least one reader AND one judge call, so the
       reader+judge call count must GROW across it. Counting ``total.calls``
       instead would be inert on a real run: the question key is bound before
       ingest, so ingest rows alone keep the total non-zero — which is why the
       growth is measured per STAGE.
    2. **Rows that report no usage block** (``usage_present`` False, or
       ``calls_without_usage`` > 0), which make any token sum a lower bound.

    Checked on BOTH envelopes: the delta subtracts ``before`` from ``after``,
    so an unmeasured *one-shot* side corrupts the difference just as much.
    """
    if stage_calls(after, "reader", "judge") \
            <= stage_calls(before, "reader", "judge"):
        return True
    return (spend_is_unknown(before, "reader", "judge")
            or spend_is_unknown(after, "reader", "judge"))


def admit_reconciliation(*, bound: int, cap: int) -> bool:
    """The pre-pass admission rule: fire the re-run ONLY when the bound on
    its marginal spend fits the per-question cap. A raised window that
    cannot fit the cap is refused BEFORE any spend."""
    return int(bound) <= int(cap)


# ── variant reporting (the LABELED block; never the official number) ────

def _stats(outcome: dict) -> dict:
    s = outcome.get("window_reconciliation_stats")
    return s if isinstance(s, dict) else {}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _outcome_label(outcome: dict) -> bool | None:
    v = outcome.get("label")
    return v if isinstance(v, bool) else None


def _variant_label(outcome: dict) -> bool | None:
    v = _stats(outcome).get("variant_label")
    return v if isinstance(v, bool) else None


def reconciliation_variant_rows(outcomes: list[dict], *,
                                k: int | str = 5) -> dict[str, Any]:
    """The LABELED VARIANT block for the stratified window reconciliation.

    Pure over the outcomes list. Every field is derived from the re-run's
    own per-outcome markers — NOTHING here is read by the official
    aggregate (``variant_rows``'s ``official_recall_all@k`` derives from
    the one-shot ``session_recall@k`` alone; the ``official_invariant``
    field states it, and the tests pin it).

    Semantics:
      ``value`` — the variant accuracy over the ADMITTED + GRADED
        questions only (the paired subset), ``one_shot_accuracy`` the
        same subset's one-shot accuracy. The pair is the honest read: a
        variant number over a different population would not be
        comparable to anything.
      ``recovered`` / ``lost`` / ``net_flips`` — the paired flip census
        (the reconstructability guard the #2512/M8 discipline requires;
        a CI alone on this cohort is too coarse).
      ``reader_surface@k`` — the before/after mean over the admitted
        questions **that carry BOTH values**, and ``n`` is that paired
        population (the mechanism's own signal: did the raise actually
        widen what the reader saw?). Members admitted without an ``after``
        (an errored second reader) are excluded from BOTH means — a
        before-mean over more questions than the after-mean would publish
        a delta between two different populations.
      ``pool_drift`` — questions whose raised-pass ``session_recall@k``
        differs from the one-shot value. The raise is a WINDOW change
        (the pool depth is unchanged), so any drift is re-execution
        variance and is surfaced rather than silently blended.
      ``n_errored`` — every outcome carrying a recorded error, WHETHER OR
        NOT it reached ``fired`` (see the census comment below: a
        pre-fire predicate error is a dead arm, not an absent class), and
        ``n_errored_pre_fire`` — the subset that never reached the pass.
    """
    fired = [o for o in outcomes if _stats(o).get("fired") is True]
    admitted = [o for o in fired if _stats(o).get("admitted") is True]
    # ⛔ THE ERROR CENSUS IS NOT GATED ON ``fired`` (#11830 review P2). The
    # total wrapper in ``run.py`` records any non-fatal exception on the
    # pass, and ``failing_class_reason`` runs BEFORE ``fired`` is set — so a
    # predicate that throws (it lazily imports ``tortoise.reader``'s private
    # ``_looks_abstained``) leaves ``fired=False, error="ImportError: ..."``.
    # Counting errors only among ``fired`` published ``n_errored: 0`` — which
    # reads as "the failing class never occurred on this run" over an arm
    # that failed on EVERY question just because the product renamed a
    # private symbol. The counter is incremented at ATTEMPT time, not on
    # completion (the rule Prometheus instrumentation states for a failure
    # counter: a failure that prevents the later stage from running is still
    # a failure, and it is counted).
    errored = [o for o in outcomes if _stats(o).get("error")]
    errored_pre_fire = [o for o in errored
                        if _stats(o).get("fired") is not True]
    graded = [o for o in admitted if _variant_label(o) is not None]
    pairs = [(o, _outcome_label(o), _variant_label(o))
             for o in graded if _outcome_label(o) is not None]

    one_shot_acc = _mean([1.0 if a else 0.0 for _o, a, _b in pairs])
    variant_acc = _mean([1.0 if b else 0.0 for _o, _a, b in pairs])
    recovered = sum(1 for _o, a, b in pairs if not a and b)
    lost = sum(1 for _o, a, b in pairs if a and not b)

    # ⛔ ONE PAIRED POPULATION, never two independently-filtered lists: an
    # ADMITTED pass can lack an ``after`` (its second reader never produced
    # a surface — the pass errored), and averaging ``before`` over a larger
    # population than ``after`` publishes a delta between two different
    # question sets. The pairing is the point of the row; ``n`` is its size.
    surface_pairs: list[tuple[float, float]] = []
    for o in admitted:
        s = _stats(o)
        before = s.get("reader_surface_before")
        after = s.get("reader_surface_after")
        if (isinstance(before, (int, float)) and not isinstance(before, bool)
                and isinstance(after, (int, float))
                and not isinstance(after, bool)):
            surface_pairs.append((float(before), float(after)))
    surfaces_before = [b for b, _a in surface_pairs]
    surfaces_after = [a for _b, a in surface_pairs]

    drift = 0
    drift_pairs = 0
    for o in admitted:
        s = _stats(o)
        b = s.get("session_recall_before")
        a = s.get("session_recall_after")
        if (isinstance(a, (int, float)) and not isinstance(a, bool)
                and isinstance(b, (int, float))
                and not isinstance(b, bool)):
            drift_pairs += 1
            if float(a) != float(b):
                drift += 1

    caps = [s.get("cap") for s in (_stats(o) for o in fired)
            if isinstance(s.get("cap"), int)]
    marginal = [s.get("marginal_tokens") for s in (_stats(o) for o in admitted)
                if isinstance(s.get("marginal_tokens"), int)
                and s.get("marginal_unmetered") is not True]

    return {
        "label": ("LABELED VARIANT (NOT official, P3 #2570): stratified "
                  "window reconciliation — a SECOND reader pass on a raised "
                  "window for the failing class (non-_abs MSR/preference "
                  "abstention, or judge-miss with reader_surface@k < 1.0), "
                  "under a per-question token cap. The official "
                  "recall_all@k grades the one-shot pool's top-k and is "
                  "unchanged by this arm; this accuracy is NOT the "
                  "official accuracy."),
        "value": variant_acc,
        "one_shot_accuracy": one_shot_acc,
        "n_fired": len(fired),
        "n_admitted": len(admitted),
        "n_skipped_token_cap": sum(
            1 for o in fired
            if _stats(o).get("skip_reason") == "token_cap"),
        "n_graded": len(pairs),
        "recovered": recovered,
        "lost": lost,
        "net_flips": recovered - lost,
        "cap_exceeded_n": sum(
            1 for o in admitted if _stats(o).get("cap_exceeded") is True),
        # A pass that raised above the declared bound, or that FAILED: both
        # are census rows, never silent. ``n_errored`` is the containment
        # counter — a failed optional pass must be visible, not vanish — and
        # it counts an outcome carrying ``error`` whether or not the pass
        # reached ``fired`` (a pre-fire error is still a dead arm).
        "bound_exceeded_n": sum(
            1 for o in admitted if _stats(o).get("bound_exceeded") is True),
        "n_errored": len(errored),
        # The subset that never reached the pass at all (the predicate or the
        # pre-admission prologue threw). A NON-ZERO value here means the
        # arm's POPULATION is unknown rather than empty — the difference
        # between "no failing class on this run" and "the arm could not
        # decide", which ``n_fired`` alone cannot express.
        "n_errored_pre_fire": len(errored_pre_fire),
        # A pass admitted on an envelope that reported a call with no usage
        # block spends an UNMEASURED amount: its lower bound is not evidence
        # of staying under the cap, so it is excluded from the total and
        # counted here instead of being priced at face value.
        "n_unmetered": sum(
            1 for o in admitted
            if _stats(o).get("marginal_unmetered") is True),
        "token_cap": caps[0] if caps else None,
        "marginal_tokens_total": (sum(marginal) if marginal else None),
        f"reader_surface@{k}": {
            "before": _mean(surfaces_before),
            "after": _mean(surfaces_after),
            "n": len(surface_pairs),
        },
        "pool_drift_n": drift,
        # ⛔ The HORIZON, not just the population. The drift is read at the
        # run's primary k, which need not be the official row's k (the
        # receipt's official number is `official_recall_all@5`), so a bare
        # count could be a control taken at a different horizon than the
        # number it appears to certify. Self-describing, like the sibling
        # `reader_surface@k` row.
        "pool_drift_k": k,
        # The population the drift count was taken over. ``0`` drift out
        # of 0 pairs is an UNREAD census, not a measured "no drift" —
        # without this an empty population is indistinguishable from a
        # clean one (the same class the surface delta already guards with
        # its own ``n``).
        "n_pool_drift_pairs": drift_pairs,
        "official_invariant": (
            "official recall_all@k derives from the one-shot "
            "session_recall@k only — the re-run never mutates the one-shot "
            "retrieval result, so no official number can be derived from "
            "this block"),
        # ⛔ The RESULT is never mutated; the ENVIRONMENT is not fully
        # isolated, and TWO run-global couplings make that so — not the
        # breaker alone. (a) The re-run's extra retrieval runs through the
        # same vector circuit breaker (tortoise.search_engine._BREAKERS), so
        # a raised pass can trip or heal it and change which LATER questions
        # are dropped as `dropped.breaker_open`. (b) The re-run's spend is
        # observed by the run-global hard stop (`spend_guard`, armed by
        # default at HARD_STOP_USD), so an arm-ON run reaches the ceiling at
        # a DIFFERENT question than an identical arm-OFF run and aborts
        # `spend_hard_stop` there. (b) happens on a perfectly HEALTHY vector
        # leg, which is why the condition is not "healthy leg" but the
        # narrower "no global stop was reached".
        "population_invariant_residual": (
<<<<<<< HEAD
            "the re-run is coupled to two run-global budgets: the vector "
            "circuit breaker (a raised pass can trip or heal it, changing "
            "which LATER questions drop as dropped.breaker_open) and the "
            "spend hard stop (arm-ON reaches the ceiling at a different "
            "question and aborts spend_hard_stop). The one-shot result is "
            "never mutated; population identity across arms holds only "
            "while neither global stop is reached."),
=======
            "the re-run shares TWO run-global budgets with the arm-OFF twin, "
            "so population identity across arms holds only on a healthy run: "
            "(1) the vector circuit breaker — on a DEGRADED vector leg an "
            "arm-ON run can drop or un-drop later one-shot questions (see "
            "the report's dropped.breaker_open); (2) the spend ceiling — the "
            "arm's extra reader/judge rows go through usage.record -> "
            "spend_guard.observe, so under --hard-stop-usd the raised passes "
            "consume the ceiling faster and an arm-ON run can abort EARLIER "
            "than its arm-OFF twin, publishing a smaller population. "
            "Disclosure only: the one-shot result is never mutated and no "
            "measured number is adjusted."),
>>>>>>> 45711fa38f6f (fix(2570): close the #11830 review — golden-shape pin, pre-fire error census)
    }
