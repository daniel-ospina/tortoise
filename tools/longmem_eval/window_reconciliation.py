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
retrieval pool (``DEFAULT_POOL_SIZE`` = 120 > any raised ``top_k``): it
is a WINDOW change, which is exactly the variable P3 measures.

## The per-question budget cap

The cap bounds the re-run's OWN marginal spend (raw tokens; priced at
report time — ``costing.py`` is deliberately report-time-only, so no USD
ever enters the runner). Admission is pre-computed from the one-shot
envelope via :func:`marginal_bound` — ``factor ×`` the one-shot reader
tokens + the judge tokens, a conservative upper bound on the re-ask —
and the actual marginal spend is measured after the pass via
:func:`envelope_delta`. A pass admitted under the bound whose ACTUAL
spend exceeded the cap is still recorded, flagged ``cap_exceeded``: the
census is honest about overshoot instead of hiding it, and the bound is
what a future tightening tunes. Admission never reads the ingest burn —
the cap is a re-run budget, not a per-question whole-run budget.
"""
from __future__ import annotations

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

#: The max number of chunks the reader window may hold after the raise.
#: A window beyond this is not a measurement of the same mechanism any
#: more (it is a different instrument) — the clamp is a guard, not a knob.
MAX_RAISED_CONTEXT_ITEM_CAP = 400
MAX_RAISED_TOP_K = 400


def is_failing_class_type(question_type: str | None) -> bool:
    """True for the question types the failing-class predicate may fire on."""
    return (question_type or "") in FAILING_CLASS_QUESTION_TYPES


def is_abs_question(question_id: str | None) -> bool:
    """The official ``_abs`` suffix convention (abstention questions are
    excluded from the failing class entirely — their reader MUST abstain)."""
    return isinstance(question_id, str) and "_abs" in question_id


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

    ``top_k`` and ``context_item_cap`` are clamped to the guard maxima so
    a pathological one-shot config cannot produce an unbounded window.
    """
    if factor < 1:
        raise ValueError(f"raise factor must be >= 1, got {factor}")
    return {
        "top_k": min(max(1, int(top_k)) * factor, MAX_RAISED_TOP_K),
        "context_item_cap": min(
            max(1, int(context_item_cap)) * factor,
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


def marginal_bound(envelope: dict | None,
                   factor: int = DEFAULT_WINDOW_RAISE_FACTOR) -> int:
    """A conservative upper bound on a re-run's marginal LLM tokens,
    estimated from the one-shot question's own envelope: the raised
    reader sees at most ``factor ×`` what the reader saw, plus one more
    judge pass. Ingest/extractor stages are excluded (the re-run does not
    ingest — the cap is a re-run budget, not a whole-run budget)."""
    return (factor * stage_tokens(envelope, "reader")
            + stage_tokens(envelope, "judge"))


def envelope_delta(before: dict | None, after: dict | None) -> int:
    """The re-run's ACTUAL marginal reader+judge tokens (never negative:
    a collector that lost rows reports 0, not a credit)."""
    return max(0, stage_tokens(after, "reader", "judge")
               - stage_tokens(before, "reader", "judge"))


def admit_reconciliation(*, bound: int, cap: int) -> bool:
    """The pre-pass admission rule: fire the re-run ONLY when the
    conservative bound on its marginal spend fits the per-question cap."""
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
        questions (the mechanism's own signal: did the raise actually
        widen what the reader saw?).
      ``pool_drift`` — questions whose raised-pass ``session_recall@k``
        differs from the one-shot value. The raise is a WINDOW change
        (the pool depth is unchanged), so any drift is re-execution
        variance and is surfaced rather than silently blended.
    """
    fired = [o for o in outcomes if _stats(o).get("fired") is True]
    admitted = [o for o in fired if _stats(o).get("admitted") is True]
    graded = [o for o in admitted if _variant_label(o) is not None]
    pairs = [(o, _outcome_label(o), _variant_label(o))
             for o in graded if _outcome_label(o) is not None]

    one_shot_acc = _mean([1.0 if a else 0.0 for _o, a, _b in pairs])
    variant_acc = _mean([1.0 if b else 0.0 for _o, _a, b in pairs])
    recovered = sum(1 for _o, a, b in pairs if not a and b)
    lost = sum(1 for _o, a, b in pairs if a and not b)

    surfaces_before: list[float] = []
    surfaces_after: list[float] = []
    for o in admitted:
        s = _stats(o)
        before = s.get("reader_surface_before")
        after = s.get("reader_surface_after")
        if isinstance(before, (int, float)) and not isinstance(before, bool):
            surfaces_before.append(float(before))
        if isinstance(after, (int, float)) and not isinstance(after, bool):
            surfaces_after.append(float(after))

    drift = 0
    for o in admitted:
        s = _stats(o)
        b = s.get("session_recall_before")
        a = s.get("session_recall_after")
        if (isinstance(a, (int, float)) and not isinstance(a, bool)
                and isinstance(b, (int, float)) and not isinstance(b, bool)
                and float(a) != float(b)):
            drift += 1

    caps = [s.get("cap") for s in (_stats(o) for o in fired)
            if isinstance(s.get("cap"), int)]
    marginal = [s.get("marginal_tokens") for s in (_stats(o) for o in admitted)
                if isinstance(s.get("marginal_tokens"), int)]

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
            1 for o in fired if _stats(o).get("admitted") is not True),
        "n_graded": len(pairs),
        "recovered": recovered,
        "lost": lost,
        "net_flips": recovered - lost,
        "cap_exceeded_n": sum(
            1 for o in admitted if _stats(o).get("cap_exceeded") is True),
        "token_cap": caps[0] if caps else None,
        "marginal_tokens_total": (sum(marginal) if marginal else 0),
        f"reader_surface@{k}": {
            "before": _mean(surfaces_before),
            "after": _mean(surfaces_after),
            "n": min(len(surfaces_before), len(surfaces_after)),
        },
        "pool_drift_n": drift,
        "official_invariant": (
            "official recall_all@k derives from the one-shot "
            "session_recall@k only — the re-run never mutates the one-shot "
            "retrieval result, so no official number can be derived from "
            "this block"),
    }
