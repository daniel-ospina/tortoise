"""R1 granularity micro-test — the run protocol's step-2 sweep (#1540, epic
#1509).

Runs the LongMemEval runner across the three sweep points
``chunk_turns ∈ {1, 2, 4}`` (one very low, two middle — the owner-specified
3-point sweep) with all other knobs fixed, and emits a comparison table + a
deterministic winner per the D2 selection rule.

**Selection rule (D2):** v2 mode — maximize ``evidence_recall@10`` (the
extracted-point evidence; the denominator is granularity-neutral per D5),
with ``chunk_evidence_recall@10`` and ``context_tokens_mean`` recorded
alongside, subject to ``context_tokens_mean <= context_token_cap``; tie-break
→ smaller ``chunk_turns`` (finer evidence localization, less bloat).
Deterministic mode: the selection metric is knob-insensitive (chunks
unmarked, D3 — turn recall over uncapped turn points), so the deterministic
cell is a context-token/underfill view only, NOT a granularity selector.

**Run-protocol tie-in:** the selected granularity feeds the pilot (step 3)
and the 500-Q run (step 5); the report's methodology records the chosen
value (D7 — ``chunk_turns`` in the report methodology).

``--mock`` implies deterministic ingest when ``--ingest-mode`` is unset, and a
mock run needs no provider keys; real v2 sweeps need ``--extractor-model`` /
provider keys. ``--mock`` does **NOT** waive the dense-leg gate (#4718) —
``--skip-preflight`` does — so since #4821 a mock run on a host with no
loadable embedder stops before the dataset load unless that waiver is passed.

**Dense-leg contract (#4821, R3 #1542, #4718).** The sweep runs the SAME
dense-leg (embedder) gate as ``run.py``'s ``run_main`` — ``gate_dense_leg`` /
``run_measured_evaluation`` — so it cannot be the entry point that skips it.
The dense leg is REQUIRED: no embedder → ``SystemExit(1)`` before the dataset
load, naming the reason and stating that no measurement was produced.
``--skip-preflight`` is the ONE explicit waiver (debugging / offline wiring)
and must not be confused with ``--mock``: it lets the sweep CONTINUE when the
embedder cannot load, and the outcome is then recorded rather than implied —
every cell carries ``vector_strategy`` from its own report methodology and
``embedding_coverage`` rolled up from its own PER-QUESTION outcomes
(``unavailable`` / ``0.0`` when the leg could not load), the table prints
``NOT MEASURED``, and the winner line never says SELECTED. A waiver taken on
a host where the embedder DOES load is a no-op on all of that: the cells
record ``enabled``, because that is what ran.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/sweep_granularity.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python -m tools.longmem_eval.sweep_granularity`"
    )

import argparse
import json
from pathlib import Path

from . import dataset as ds
from .judge import MockJudge, build_judge
from .reader import MockReader, build_reader
from .retrieve import (
    DEFAULT_CONTEXT_TOKEN_CAP,
    DEFAULT_MAX_CHUNKS_PER_SESSION,
    DEFAULT_POOL_SIZE,
)
from .run import _positive_int, gate_dense_leg, run_measured_evaluation

DEFAULT_SWEEP_POINTS = (1, 2, 4)
DEFAULT_SWEEP_KS = (5, 10, 20)


def _selection_metric_label(ingest_mode: str) -> str:
    return ("evidence_recall@10" if ingest_mode == "v2"
            else "turn_recall@10 (view-only)")


def _aggregate_embedding_coverage(outcomes: list[dict]) -> float | None:
    """Roll the PER-QUESTION write-time coverage up into ONE cell receipt.

    #4821: ``embedding_coverage`` is a per-question metric (R3 #1542 D3 —
    produced in ``retrieve.py``, projected per outcome in ``run.py``). The
    report's AGGREGATE ``report["retrieval"]`` does not carry it (its
    dense-adjacent key is ``evidence_coverage``, a different M6/D6 ingest
    metric), so reading it from there returned ``None`` for EVERY sweep and
    the field could never testify — ``None`` for coverage ``1.0`` and ``0.0``
    alike.

    Aggregating the per-question values is what an eval harness does with a
    per-sample metric at the aggregate level (the aggregate is derived FROM
    the per-item records, never carried separately). Point-weighted, so it is
    the write-time coverage of the cell's points: ``1.0`` when every point was
    embedded, ``0.0`` when none was. Questions that carry no coverage data
    (a pre-R3 checkpoint resume) are excluded rather than counted as zero —
    an unknown is not a miss. ``None`` when the cell knows about no points at
    all, which is the honest shape for "not checked" (D5).

    Derived from :func:`_points_census`, so this number and the published
    census can never disagree (#4821 review P3). The old body fell back to
    averaging the per-question ``embedding_coverage`` values whenever the
    census looked empty, which is unreachable from a real report —
    ``retrieve.py`` emits ``points_total`` / ``points_embedded`` /
    ``embedding_coverage`` from ONE query, so a non-``None`` coverage always
    implies a truthy ``points_total``. The fallback was also the one path
    that could publish a NUMERIC coverage while ``_points_census`` reported
    the cell unknown, which would have silently defeated
    ``_dense_leg_verified``'s zero-coverage arm.

    The number is over the records the census ADMITTED. A record the pair
    rule refused contributes to neither half, so this ratio describes the
    admitted population only — and the DIRECTION of that exclusion is NOT
    determined (#10356 review P2). ``embedded > total`` — the shape the rule
    names first — carries a per-record ratio ABOVE 1, so dropping it
    DEFLATES the published number: measured, an admitted ``(10, 0)`` beside
    a refused ``(3, 7)`` publishes ``0.0`` where including both sums to
    ``7/13``. A negative half moves it the other way, so there is no safe
    direction to round in and the number cannot be trusted AT ALL once a
    record was refused. (The sentence this replaces said the exclusion "can
    raise it, never lower it", which the ``(3, 7)`` shape refutes.) That is
    why the verdict is not read off this number alone:
    ``_dense_leg_verified`` consumes ``records_refused`` and fails the cell
    closed (#10355 review P3, refused-record fail-open)."""
    census = _points_census(outcomes)
    if not census["points_total"]:
        return None
    return round(census["points_embedded"] / census["points_total"], 4)


def _impossible(half) -> bool:
    """Is this census half a value the producer had no business emitting?

    A point count is a non-``bool`` ``int`` that is not negative, so an
    impossible half is a ``bool`` (``True`` IS an ``int``, which is exactly
    the lie the pair rule catches), a negative, or a value that is not an
    integer at all. ``None`` (the key is absent, or explicitly null) is an
    UNKNOWN rather than an impossibility — that distinction is the whole
    split :func:`_points_census` documents."""
    if half is None:
        return False
    if isinstance(half, bool):
        return True
    return not isinstance(half, int) or half < 0


def _points_census(outcomes: list[dict]) -> dict:
    """The raw point census behind a cell's rolled-up coverage.

    Derived as ONE pair, never two independent sums (#4821 review P2): only an
    outcome that knows BOTH halves contributes, so a cell whose records mix
    known and unknown censuses cannot publish a coverage assembled from two
    different populations. The pair must also be POSSIBLE — two ints, not
    bools, with ``0 <= points_embedded <= points_total`` — because a pair that
    is impossible per record (``(3, 7)``, ``(True, 5)``, ``(-5, 5)``) rolls up
    to a coverage above 1.0 or below 0.0, and ``_dense_leg_verified``'s
    zero-coverage arm only fires on ``== 0.0`` with a positive total, so an
    impossible receipt would be certified as a dense MEASUREMENT — the exact
    class this receipt exists to close (#10355 review P2). A record that fails
    either half of the rule is excluded as a record; it is never half-counted.

    What the rule BUYS, stated exactly (#10355 review P3). Excluding a record
    is not by itself enough, and an earlier version of this docstring claimed
    a closed class it did not close. Dropping an impossible record leaves
    ``None/None`` — which the coverage arm does not refute (an unknown
    population is not a miss) — so a cell whose ONLY record was impossible
    still read MEASURED; and in a mixed cell the refused points vanished from
    the population, so the published coverage is taken over a smaller one in
    a direction nobody can predict (see ``_aggregate_embedding_coverage``:
    ``embedded > total`` DEFLATES it, a negative half inflates it). That is
    fail-open whichever way the number moves. So the refusal is CARRIED OUT
    of this function as ``records_refused`` and consumed as a refutation by
    ``_dense_leg_verified``: the cell is then NOT MEASURED, and the exclusion
    stays auditable in the row (the census is the artifact published *for*
    auditability). This is the standard split — an UNKNOWN (a half-known pair,
    an absent key, a pre-R3 resume) is excluded from the aggregate, the way
    ``SQL`` aggregates skip ``NULL``; an IMPOSSIBLE value is a value the
    producer had no business emitting, and the convention for that is to
    fail loudly rather than to silently ignore it (why ``numpy.nanmean`` is a
    separate function from ``numpy.mean``). Kept apart deliberately: the
    first is an absence of data, the second is a broken producer.
    The sum of ``points_embedded`` is ``0`` whenever
    the cell's outcomes carry no census at all — so pairing it with ``points_total: None`` published
    ``{points_total: None, points_embedded: 0}``, where a consumer summing
    ``points_embedded`` across cells reads "0 embedded" for a population it
    does not know. That is indistinguishable from "N points, none embedded",
    which is the exact ambiguity the receipt exists to remove.

    A cell that knows about no points therefore reports ``None`` for BOTH
    halves, and a cell that embedded none of N reports ``(N, 0)`` — a real
    zero, which ``_dense_leg_verified`` reads as not-measured.

    One honest wrinkle (#4821 review P3). The ``None/None`` shape covers four
    states, and this function alone cannot tell them apart: (a) a cell whose
    outcomes all report ``points_total == 0`` — a KNOWN zero; (b) a cell whose
    records carry no census at all; (c) a cell that knows its total but not how
    many of its points were embedded — excluded as a pair, so the half it does
    know is not published as a whole; and (d) a record whose pair is
    impossible (``points_embedded > points_total``, a bool, a negative) or
    whose single reported half is impossible — refused.
    ``records_refused`` is what separates (d) from (a)-(c), and that separation
    is now load-bearing rather than cosmetic: a cell with no points can neither
    reach the context cap nor produce evidence recall, so it is never a winner,
    and (a)-(c) are absences of data — the coverage arm leaves them un-refuted
    instead of inventing a zero, which is the documented rule for an unknown
    population (see ``_dense_leg_verified``). (d) is unreachable from a real
    report — ``retrieve.py`` derives both halves from ONE Cypher row
    (``count(p), count(p.embedding)``), so ``0 <= points_embedded <=
    points_total`` holds by construction — but if it ever happens, the producer
    is lying about a number it published, and the cell is REFUTED rather than
    quietly shrunk."""
    # ONE pair per outcome: an outcome that knows its total but not how many
    # of its points were embedded is excluded from BOTH halves, never counted
    # on one side only — that is what "one pair, never two independent sums"
    # buys, and it is why this sums a filtered list instead of summing two
    # independently filtered lists. The pair is also checked for POSSIBILITY
    # (ints, not bools, 0 <= embedded <= total): a per-record lie rolls up to
    # a coverage outside [0, 1] that the zero-coverage arm cannot see, i.e. an
    # impossible receipt certified as a MEASUREMENT (#10355 review P2).
    #
    # A refused record is COUNTED OUT, not just dropped (#10355 review P3):
    # ``records_refused`` rides the published row so ``_dense_leg_verified``
    # can refute the cell and an auditor can see the population a published
    # coverage was computed over. A record is refused when EITHER
    #   * it claims a PAIR (both halves integers — ``bool`` included, since
    #     ``True`` is exactly the lie the value rule catches) and that pair is
    #     impossible (``embedded > total``, a bool, a negative), OR
    #   * the single half it does report is itself impossible. The second arm
    #     is not symmetry for its own sake (#10356 review P3): a row carrying
    #     ``{'points_total': -5}`` with no ``points_embedded`` is a producer
    #     publishing a value it had no business emitting, and before this arm
    #     it fell through as an UNKNOWN — refused ``0`` — so the cell still
    #     read MEASURED. An impossible half is a lie whether or not its
    #     sibling arrived.
    # A half-known pair whose reported half IS a plausible count stays an
    # UNKNOWN and is not refused: that is an absence of data (a pre-R3
    # resume), not a false one.
    pairs = [(o.get("points_total"), o.get("points_embedded")) for o in outcomes]
    known: list[tuple[int, int]] = []
    records_refused = 0
    for total, embedded in pairs:
        claims_pair = isinstance(total, int) and isinstance(embedded, int)
        if claims_pair and not isinstance(total, bool) \
                and not isinstance(embedded, bool) and 0 <= embedded <= total:
            known.append((total, embedded))
        elif claims_pair or _impossible(total) or _impossible(embedded):
            records_refused += 1
    known_total = sum(t for t, _ in known)
    known_embedded = sum(e for _, e in known)
    if not known_total:
        return {"points_total": None, "points_embedded": None,
                "records_refused": records_refused}
    return {"points_total": known_total, "points_embedded": known_embedded,
            "records_refused": records_refused}


def _dense_leg_verified(results: list[dict]) -> bool:
    """Was the dense leg VERIFIED USABLE for every cell of this sweep?

    Read from the per-cell receipts, never from a flag the caller passed
    (#4821): each cell's ``vector_strategy`` comes from that cell's report
    methodology (``enabled`` iff the gate's status said ``available``), so
    the answer cannot disagree with what the artifact claims.

    Narrower than "did this sweep use the dense leg": a deterministic-ingest
    cell is keyword-only by construction (D3) and still reports ``enabled``
    when the embedder is simply available. Nothing gates on that distinction
    (the deterministic cell selects no winner), so this answers the question
    the labels actually ask — is a degraded leg being passed off as a
    measurement?

    BOTH witnesses must agree (#4821 review P1). ``vector_strategy`` is a
    PRE-FLIGHT answer: ``report.py`` derives it as ``"enabled" if
    embedder_status.get("available")``, i.e. "the gate's probe succeeded",
    NOT "a vector was written". The embedder can load and still write zero
    vectors — ``compute_embeddings`` degrades to ``[None] * n`` when
    ``model.encode`` raises, ``encode_batch_for_store`` does the same on a
    short batch (#4280), and ``_degrade_to_width`` drops every row on a width
    mismatch. In each of those the pre-flight says ``enabled`` while the
    write-time ``embedding_coverage`` is ``0.0``, so a predicate reading only
    ``vector_strategy`` would print
    ``vector_strategy=enabled  embedding_coverage=[0.0]  (MEASURED)`` — a line
    that contradicts itself — and then hand the SELECTED granularity to the
    pilot and the 500-Q run. That is #4821's failure mode re-entering through
    the field added to prevent it.

    So: ``vector_strategy`` must be ``enabled`` for EVERY cell, AND a cell
    that knows about points must not have embedded none of them. Partial
    coverage stays MEASURED — that is a real hybrid run, and its number is
    published; zero-of-N is not a measurement of the dense leg. A cell that
    knows nothing about its points (``points_total is None``, a pre-R3
    resume) is not refuted by this arm; the ``vector_strategy`` arm still
    governs it.

    An UNKNOWN census and a REFUSED one are different states, and only the
    second refutes the cell (#10355 review P3). ``records_refused`` is
    ``_points_census``'s count of records that claim a PAIR that cannot be
    true (``embedded > total``, a bool, a negative) or that report a single
    half which is itself impossible (``{'points_total': -5}``). Excluding
    such a record would leave the cell's published coverage computable over
    the records that happened to survive — in a direction nobody can predict
    (``embedded > total`` DEFLATES the published ratio: measured, an admitted
    ``(10, 0)`` beside a refused ``(3, 7)`` publishes ``0.0`` where including
    both sums to ``7/13``; a negative half inflates it) — while a cell whose
    only record was impossible would report ``None/None`` and slip past the
    zero-coverage arm. A number no one can read is not a measurement in
    either direction, so this arm fails it closed. (Absences stay uncounted:
    a half-known pair whose reported half is a plausible count, or an absent
    pair, is data the cell does not have, not data that is wrong.)

    KNOWN RESIDUAL (recorded, not implied — #4821 review P3). This predicate
    reads the WRITE-time witness only. A cell with full write coverage whose
    QUERY-side encode degrades (the vector leg skipped, an FTS-only pool)
    still reads MEASURED, because the retrieval-time witness is not consulted
    here. That witness exists and is already projected per question
    (``run.py``'s ``legs`` / ``match_source_counts``), so closing this is a
    matter of threading it into this one predicate — a follow-up, not a
    silent gap. Same class as the write-side defect this arm closes, reached
    from the read side."""
    if not results:
        return False
    for r in results:
        if r.get("vector_strategy") != "enabled":
            return False
        total = r.get("points_total")
        if r.get("embedding_coverage") == 0.0 and isinstance(total, int) \
                and total > 0:
            return False
        # A census that REFUSED a record refutes the cell (#10355 review P3).
        # An impossible pair (``embedded > total``, a bool, a negative) is a
        # producer contract violation, and excluding it is not neutral in any
        # predictable direction: the refused points leave the population, and
        # whether the published ratio moves up or DOWN depends on the shape.
        # ``embedded > total`` DEFLATES it — an admitted (10, 0) beside a
        # refused (3, 7) publishes 0.0, where including both is 7/13 — and a
        # negative half inflates it (#10356 review P2: the sentence here used
        # to claim the exclusion could only INFLATE, i.e. one direction of a
        # move that is not determined). A cell whose only record was
        # impossible also reports ``None/None``, which the arms above cannot
        # see. Failing the cell closed is what makes the census rule close the
        # class it names, instead of moving the lie into a smaller number of
        # unknown bias.
        if r.get("records_refused"):
            return False
    return True


def _select_winner(results: list[dict], *, ingest_mode: str,
                   context_cap: int) -> dict | None:
    """The D2 selection rule. ``None`` in deterministic mode (knob-
    insensitive view) or when no config is eligible."""
    if ingest_mode != "v2":
        return None
    eligible = [
        r for r in results
        if r["context_tokens_mean"] <= context_cap
        and r["evidence_recall@10"] is not None
    ]
    if not eligible:
        return None
    # maximize evidence_recall@10, tie-break → smaller chunk_turns
    return max(eligible, key=lambda r: (r["evidence_recall@10"], -r["chunk_turns"]))


def run_sweep(
    instances: list[dict],
    *,
    chunk_turns_values: tuple[int, ...] = DEFAULT_SWEEP_POINTS,
    context_cap: int = DEFAULT_CONTEXT_TOKEN_CAP,
    max_chunks_per_session: int = DEFAULT_MAX_CHUNKS_PER_SESSION,
    ingest_mode: str = "v2",
    extractor_model=None,
    reader=None,
    judge=None,
    ks: tuple[int, ...] = DEFAULT_SWEEP_KS,
    work_dir: str | None = None,
    limit: int | None = None,
    dense_leg_required: bool = True,
    embedder_status: dict | None = None,
) -> tuple[list[dict], dict | None]:
    """Run the 3-point sweep over ``instances`` with fixed other knobs.

    Returns ``(results, winner)`` where each result records the per-config
    aggregates (evidence/chunk-evidence/session/turn recall@10,
    context_tokens_mean, context_point_count_mean) **plus the dense-leg
    receipt fields** — ``vector_strategy`` from that cell's report
    methodology, and ``embedding_coverage`` (with its ``points_total`` /
    ``points_embedded`` census) rolled up from that cell's PER-QUESTION
    outcomes, which is the only place D3 records it (see
    :func:`_aggregate_embedding_coverage`) — and the winner is the D2
    selection (None in deterministic mode or when no config is eligible).

    #4821 — the dense-leg gate. This function used to call ``run_evaluation``
    directly, so it never ran the pre-flight that ``run.py``'s ``run_main``
    gates every run with (R3 #1542, fail-closed since #4718): a sweep in an
    env with no embedder completed KEYWORD-ONLY and printed
    ``SELECTED granularity: chunk_turns = X``, the only artifact an operator
    sees (per-cell reports are not saved) — a degraded instrument silently
    choosing the knob that feeds the pilot and the 500-Q run.

    The gate now runs HERE, through the same :func:`gate_dense_leg` /
    :func:`run_measured_evaluation` seam ``run_main`` uses, and it is
    REQUIRED by default. ``dense_leg_required=False`` is the explicit
    ``--skip-preflight`` waiver; it continues (the documented debugging /
    offline escape hatch) but the status is recorded on every cell, so the
    receipt can never read as a dense measurement.
    """
    if ingest_mode not in ("deterministic", "v2"):
        raise ValueError(f"ingest_mode must be deterministic|v2, got {ingest_mode!r}")
    status = (embedder_status if embedder_status is not None
              else gate_dense_leg(dense_leg_required=dense_leg_required))
    subset = instances[:limit] if limit is not None else instances
    results: list[dict] = []
    for turns in chunk_turns_values:
        outcomes, report = run_measured_evaluation(
            subset, embedder_status=status,
            reader=reader, judge=judge, ks=ks, top_k=20,
            split="s", work_dir=work_dir, ingest_mode=ingest_mode,
            extractor_model=extractor_model,
            chunk_turns=turns, max_context_tokens=context_cap,
            max_chunks_per_session=max_chunks_per_session,
        )
        ret = report["retrieval"]
        methodology = report.get("methodology") or {}
        results.append({
            "chunk_turns": turns,
            "evidence_recall@10": (ret.get("evidence_recall@k") or {}).get("10"),
            "chunk_evidence_recall@10": (
                ret.get("chunk_evidence_recall@k") or {}).get("10"),
            "session_recall@10": (ret.get("session_recall@k") or {}).get("10"),
            "turn_recall@10": (ret.get("turn_recall@k") or {}).get("10"),
            "context_tokens_mean": ret.get("context_tokens_mean", 0.0),
            "context_point_count_mean": ret.get("context_point_count_mean", 0.0),
            "n_completed": len(outcomes),
            # #4821: the dense-leg receipt rides the per-cell row. An entry
            # point that scores a keyword-only run as if it were hybrid has
            # to lie in these two fields, not merely forget a flag.
            #
            # ``vector_strategy`` comes from the methodology (where D5 puts
            # it). ``embedding_coverage`` is PER QUESTION (D3) — the
            # aggregate ``report["retrieval"]`` does not carry it, so it is
            # rolled up from this cell's own outcomes. Reading it off the
            # aggregate returned ``None`` for every cell, a witness that
            # could never testify.
            "vector_strategy": methodology.get("vector_strategy"),
            "embedding_coverage": _aggregate_embedding_coverage(
                report.get("outcomes") or []),
            # The raw census, so the rolled-up number stays auditable back to
            # the points it was computed over (None on a pre-R3 resume).
            **_points_census(report.get("outcomes") or []),
        })
    return results, _select_winner(results, ingest_mode=ingest_mode,
                                   context_cap=context_cap)


def _print_table(results: list[dict], *, ingest_mode: str,
                 context_cap: int, max_chunks_per_session: int) -> None:
    metric = _selection_metric_label(ingest_mode)
    print("=" * 88)
    print("R1 granularity sweep (run protocol step 2) — "
          f"ingest_mode={ingest_mode}")
    print(f"context_token_cap={context_cap}  "
          f"max_chunks_per_session={max_chunks_per_session}  "
          f"pool_depth={DEFAULT_POOL_SIZE} (knob TORTOISE_LME_POOL_SIZE)")
    # #4821: the dense-leg posture is printed WITH the table, not only in a
    # stderr warning an operator can scroll past — the table above is what a
    # reader takes as a measurement.
    strategies = sorted({str(r.get("vector_strategy")) for r in results})
    coverages = [r.get("embedding_coverage") for r in results]
    verdict = ("MEASURED" if _dense_leg_verified(results)
               else "NOT MEASURED")
    # #10355 review P3: a refused census record makes the cell NOT MEASURED
    # while its coverage can still read numeric, so the reason is printed
    # beside the verdict instead of leaving the operator to guess why.
    refused = [r.get("records_refused") for r in results]
    refused_note = (f"  records_refused={refused}"
                    if any(refused) else "")
    print(f"dense leg: vector_strategy={','.join(strategies)}  "
          f"embedding_coverage={coverages}  ({verdict}){refused_note}")
    print("=" * 88)
    header = (f"{'chunk_turns':>11}  {'ev_recall@10':>13}  "
              f"{'chunk_ev@10':>12}  {'sess_rec@10':>12}  "
              f"{'turn_rec@10':>12}  {'ctx_tokens':>11}  "
              f"{'ctx_points':>10}")
    print(header)
    print("-" * 88)
    for r in results:
        ev = f"{r['evidence_recall@10']:.3f}" if r["evidence_recall@10"] is not None else "N/A"
        cev = (f"{r['chunk_evidence_recall@10']:.3f}"
               if r["chunk_evidence_recall@10"] is not None else "N/A")
        tr = f"{r['turn_recall@10']:.3f}" if r["turn_recall@10"] is not None else "N/A"
        print(f"{r['chunk_turns']:>11}  {ev:>13}  {cev:>12}  "
              f"{r['session_recall@10']:.3f}  {tr:>12}  "
              f"{r['context_tokens_mean']:>11.1f}  "
              f"{r['context_point_count_mean']:>10.2f}")
    print("-" * 88)
    print(f"selection metric: maximize {metric} subject to "
          f"context_tokens_mean <= {context_cap}; tie-break smaller "
          f"chunk_turns (D2 #1540)")


# The causes of ``measured=False``, as FACTS ABOUT THE RESULT ROWS rather than
# about the code path that produced them. They are named because the operator
# line is built from them: before the refused-census arm landed, ``measured``
# was False for exactly two causes and BOTH implied keyword-only recall, so the
# line could assert that story unconditionally. The third cause (#10355 review
# P3) implies the opposite — the embedder ran and the recall is a hybrid run's
# — so the clauses are gated on the cause that actually holds.
UNMEASURED_EMBEDDER = "embedder_unavailable"
UNMEASURED_ZERO_COVERAGE = "no_vectors_written"
UNMEASURED_CENSUS = "census_refused"


def _unmeasured_causes(results: list[dict]) -> set[str]:
    """Which arms of :func:`_dense_leg_verified` refuted this sweep.

    Read from the SAME per-cell receipts the predicate reads and mirroring its
    arms EXACTLY, on purpose: the reason the winner line prints must not be
    able to drift from the verdict that produced it. If the predicate gains an
    arm, it belongs here in the same commit or the operator is told a cause
    that is not the one that fired.

    Empty is a legitimate answer for one state — a sweep with no result rows
    at all (``_dense_leg_verified`` refuses an empty list) — and the caller
    then prints the cause-agnostic warning alone rather than inventing a
    cause. A MEASURED sweep also returns no causes, which is why the caller
    must read this only under ``not measured``.
    """
    causes: set[str] = set()
    for r in results:
        if r.get("vector_strategy") != "enabled":
            causes.add(UNMEASURED_EMBEDDER)
        total = r.get("points_total")
        if r.get("embedding_coverage") == 0.0 and isinstance(total, int) \
                and total > 0:
            causes.add(UNMEASURED_ZERO_COVERAGE)
        if r.get("records_refused"):
            causes.add(UNMEASURED_CENSUS)
    return causes


def _publishes_a_ratio(row: dict) -> bool:
    """Does this ONE cell publish a real coverage ratio on the ``dense leg:``
    line? ``None`` (an absent census) and ``bool`` (not a ratio) do not."""
    coverage = row.get("embedding_coverage")
    return isinstance(coverage, (int, float)) and not isinstance(coverage, bool)


def _census_refusal_coverage(results: list[dict]) -> tuple[int, int]:
    """Of the cells that REFUSED a census record, how many still publish a
    real ratio, and how many publish none? ``(with_ratio, without_ratio)``.

    PER CELL, on purpose (#10355 review P3). The question the winner line asks
    here is "which refusing cell has no ratio to distrust", and that is a
    per-record question: ``all(...)`` over the sweep — the gate this replaces —
    answers "no cell failed to publish a ratio", which is a DIFFERENT claim
    and cannot name the record that failed. It is the same distinction SQL
    draws between ``bool_and``/``every`` (one boolean over the set, naming no
    row: "true if all non-null input values are true") and
    ``count("any")`` ("the number of input rows in which the input value is
    not null"), and why attributing an exclusion takes a per-row FILTER rather
    than an aggregate. Concretely, a sweep mixing an ABSENT census
    (``embedding_coverage=None``, ``records_refused=0`` — a pre-R3 resume) with
    a REFUSED one (numeric coverage, ``records_refused=1``) has
    ``_unmeasured_causes(...) == {UNMEASURED_CENSUS}`` and a sweep-wide
    ``all(...)`` of ``False``, so the sweep-wide gate printed the "a cell whose
    only record was refused publishes NO ratio" sentence about a run with no
    such cell — pointing the operator at ``records_refused`` for a cell whose
    ``records_refused`` is ``0``.

    Mirroring ``_dense_leg_verified``'s census arm in scope: only a cell whose
    ``records_refused`` is truthy is counted here, so an un-refused absence can
    never be mistaken for a refusal. ``(0, 0)`` means no cell reported a
    refusal — the source of the row set is then what to look at, not a cell.
    """
    refused = [r for r in results if r.get("records_refused")]
    with_ratio = sum(1 for r in refused if _publishes_a_ratio(r))
    return with_ratio, len(refused) - with_ratio


def _print_winner(winner: dict | None, *, ingest_mode: str,
                  context_cap: int, measured: bool,
                  results: list[dict]) -> None:
    """Print the winner line — the ONLY artifact an operator sees.

    ``measured`` is a REQUIRED keyword-only argument (PEP 3102: no default
    makes it required, and it still binds by name so it can never be swept
    into ``**kwargs``). There is deliberately no default: a caller that
    forgets would get the SELECTED vocabulary — handing a knob to the pilot
    (step 3) and the 500-Q run (step 5) on no evidence — which is exactly the
    permissive default this module exists to remove. #4821 review P2.

    ``results`` is required for the same kind of reason (#10355 review P3):
    ``measured=False`` has THREE causes now, and they call for opposite
    sentences and opposite remedies, so the line cannot be written from the
    flag alone. Passing the rows the verdict was computed over is what lets
    :func:`_unmeasured_causes` name the one that actually fired instead of
    defaulting to the keyword-only story — a default that would tell an
    operator their embedder is missing about a run whose embedder worked.
    """
    if ingest_mode != "v2":
        print("\nDeterministic mode: the selection metric is knob-insensitive "
              "(chunks unmarked, D3) — no winner; this cell is a "
              "context-token/underfill view only.")
        return
    if winner is None:
        print("\nNO ELIGIBLE WINNER: no config satisfied "
              "context_tokens_mean <= cap with a real evidence_recall@10. "
              "Raising — do not proceed to the pilot with an unvalidated "
              "granularity.", file=sys.stderr)
        return
    # #4821: the winner line is the ONLY artifact an operator sees
    # (per-cell reports are not saved). It must never print the SELECTED
    # vocabulary for a run that did not measure the dense leg — otherwise a
    # keyword-only sweep hands a choice to the pilot + the 500-Q run.
    if not measured:
        causes = _unmeasured_causes(results)
        # The headline is cause-AGNOSTIC on purpose (#10355 review P3). It
        # used to assert "the recall values above are keyword-only and are not
        # comparable to a hybrid run" as fact — true for the two original
        # causes, FALSE for a refused census, where the embedder ran and the
        # values are a hybrid run's. The clauses that are cause-specific now
        # follow, each gated on the cause `_dense_leg_verified` actually fired.
        print("\n⚠ DENSE LEG NOT MEASURED: this sweep is NOT A MEASUREMENT — "
              "do not read the recall values above as one, and do not feed "
              "them anywhere. The `dense leg:` line above carries the "
              "reason; the causes are a --skip-preflight waiver / no "
              "embedder, no vectors written, or a census the producer "
              "contradicted (records_refused).", file=sys.stderr)
        print(f"UNMEASURED candidate granularity: chunk_turns = "
              f"{winner['chunk_turns']} "
              f"(evidence_recall@10 = {winner['evidence_recall@10']:.3f}, "
              f"context_tokens_mean = {winner['context_tokens_mean']:.1f})")
        if causes & {UNMEASURED_EMBEDDER, UNMEASURED_ZERO_COVERAGE}:
            print("   the dense leg wrote no vectors for at least one cell "
                  "(no embedder, a waived pre-flight, or a vector leg that "
                  "was never exercised), so the recall values above are "
                  "keyword-only and are not comparable to a hybrid run.",
                  file=sys.stderr)
        if UNMEASURED_CENSUS in causes:
            print("   at least one cell REFUSED a census record "
                  "(records_refused on the `dense leg:` line): the producer "
                  "reported a points value that cannot be true "
                  "(points_embedded > points_total, a bool, a negative). "
                  "Nothing this cell publishes can be trusted: a refused "
                  "record's points leave the population in a direction the "
                  "exclusion does not determine, and a cell whose ONLY "
                  "record was refused publishes no ratio at all "
                  "(embedding_coverage=None). The defect is in the census "
                  "PRODUCER, so re-running cannot change this verdict.",
                  file=sys.stderr)
            if UNMEASURED_EMBEDDER not in causes \
                    and UNMEASURED_ZERO_COVERAGE not in causes:
                # "its coverage is numeric" is NOT implied by the cause set,
                # and neither is "the refusing cell published no ratio"
                # (#10355 review P3): a cell whose ONLY record was refused
                # publishes no ratio at all, and its cause is this same one.
                # Claim exactly what the cause set proves — and claim it of the
                # cells that REFUSED, not of the sweep: the sweep-wide gate
                # this replaces printed the no-ratio sentence for a run whose
                # refusing cell published a ratio and whose ratio-less cell
                # refused nothing, which is the same "the diagnostic asserts a
                # fact its gate does not prove" class this PR removes.
                #
                # The same standard applies to the two absent causes (#10356
                # review P3). "The embedder ran and no cell wrote zero vectors"
                # asserted more than the gate proves: the zero-coverage arm
                # fires only on a NUMERIC ``0.0`` over a positive ``int``
                # total, and a REFUSED census returns ``None/None``, so that
                # arm cannot fire — while the refused record itself can report
                # ``points_embedded: 0`` (measured: ``{"points_total": -5,
                # "points_embedded": 0}`` is refused, and this branch then
                # claimed no cell wrote zero vectors about exactly that cell).
                refused_with_ratio, refused_without_ratio = \
                    _census_refusal_coverage(results)
                if refused_with_ratio and not refused_without_ratio:
                    coverage_note = (
                        "Each cell that refused a record still publishes a "
                        "real ratio — its coverage is numeric — so what is "
                        "contradicted is the census, never the leg.")
                elif refused_without_ratio and not refused_with_ratio:
                    coverage_note = (
                        "A cell whose only record was refused publishes NO "
                        "ratio at all (embedding_coverage=None), so read "
                        "records_refused rather than embedding_coverage "
                        "for it.")
                elif refused_without_ratio:
                    coverage_note = (
                        "The cells that refused a record are MIXED: some "
                        "still publish a numeric coverage, others publish "
                        "NO ratio at all (embedding_coverage=None) — read "
                        "records_refused per cell: a numeric one was "
                        "computed over the population its refused record "
                        "was removed from, and a None one has no ratio to "
                        "trust.")
                else:
                    coverage_note = (
                        "Read records_refused per cell on the `dense leg:` "
                        "line to see which census was refused.")
                print("   the other two causes do NOT hold here: the "
                      "embedder reported available for every cell and no "
                      "cell published a zero-coverage receipt, so these "
                      "recall values ARE a hybrid run's — they are refused "
                      "because the census contradicts itself, not because "
                      "the leg was missing. "
                      + coverage_note,
                      file=sys.stderr)
        remedy = ("→ NOT selected: do NOT feed this to the pilot (step 3) or "
                  "the 500-Q run (step 5).")
        if causes & {UNMEASURED_EMBEDDER, UNMEASURED_ZERO_COVERAGE}:
            remedy += (" Install the pinned embedder (uv sync --group dev "
                       "--extra embeddings) and re-run without "
                       "--skip-preflight.")
        if UNMEASURED_CENSUS in causes:
            remedy += (" Fix the census producer that emitted the impossible "
                       "pair and re-run — installing the embedder, or "
                       "re-running as it stands, reproduces this verdict "
                       "byte for byte.")
        print(remedy, file=sys.stderr)
        return
    print(f"\nSELECTED granularity: chunk_turns = {winner['chunk_turns']} "
          f"(evidence_recall@10 = {winner['evidence_recall@10']:.3f}, "
          f"context_tokens_mean = {winner['context_tokens_mean']:.1f})")
    print("→ the pilot (run protocol step 3) and the 500-Q run (step 5) use "
          "this value; the report methodology records it (D7).")
    if winner["context_tokens_mean"] > context_cap:
        print("⛔ winner's context_tokens_mean EXCEEDS the cap — raising.",
              file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.sweep_granularity",
        description="R1 3-point granularity sweep (run protocol step 2): "
                    "selects chunk_turns ∈ {1,2,4} for the pilot + 500-Q run.")
    p.add_argument("--split", default=ds.DEFAULT_SPLIT,
                   choices=sorted(ds.SPLIT_FILES),
                   help="dataset split (default s)")
    p.add_argument("--limit", type=int, default=20,
                   help="questions per config (default 20)")
    p.add_argument("--data", default=None,
                   help="local dataset JSON/JSONL path (skips download)")
    p.add_argument("--ingest-mode", default=None,
                   choices=["deterministic", "v2"],
                   help="ingestion mode (default v2 — the V3 primary path; "
                        "--mock implies deterministic when unset)")
    p.add_argument("--mock", action="store_true",
                   help="offline mode: MockReader + MockJudge, no API keys "
                        "(implies deterministic ingest when --ingest-mode "
                        "unset). Does NOT waive the dense-leg gate (#4718) — "
                        "use --skip-preflight for that")
    p.add_argument("--chunk-turns", default="1,2,4",
                   help="comma-separated sweep points (default 1,2,4)")
    p.add_argument("--context-cap", type=_positive_int,
                   default=DEFAULT_CONTEXT_TOKEN_CAP,
                   help=f"reader context token budget (default {DEFAULT_CONTEXT_TOKEN_CAP})")
    p.add_argument("--max-chunks-per-session", type=_positive_int,
                   default=DEFAULT_MAX_CHUNKS_PER_SESSION,
                   help="per-session raw-chunk cap (default 2)")
    p.add_argument("--extractor-model", default=None,
                   help="extractor model spec for v2 real runs "
                        "(default: the production router)")
    p.add_argument("--output", default=None,
                   help="JSON output path for the sweep results")
    p.add_argument("--skip-preflight", action="store_true",
                   help="WAIVE the dense-leg (embedder) pre-flight — debugging "
                        "and offline wiring checks ONLY. The sweep continues "
                        "even when the embedder cannot load, and every cell "
                        "records the leg's REAL status (vector_strategy / "
                        "embedding_coverage), so a degraded receipt can never "
                        "read as a measurement (#4821)")
    p.add_argument("--work-dir", default=None,
                   help="temp dir for per-question graphs")
    args = p.parse_args(argv)

    # --mock implies deterministic ingest when the mode is unset (a mock run
    # needs no provider keys; real v2 sweeps need --extractor-model/keys). It
    # does NOT waive the dense-leg gate (#4718) — --skip-preflight does (#4821).
    ingest_mode = args.ingest_mode or ("deterministic" if args.mock else "v2")
    if args.mock and ingest_mode == "v2":
        raise SystemExit("--mock implies deterministic ingest; pass "
                         "--ingest-mode v2 without --mock for a real v2 sweep")
    chunk_points = tuple(int(x.strip()) for x in args.chunk_turns.split(",")
                         if x.strip())
    if not chunk_points or any(v < 1 for v in chunk_points):
        raise SystemExit(f"--chunk-turns must be >= 1 values, got {args.chunk_turns!r}")

    # #4821: the dense-leg gate runs BEFORE the dataset load (fail before the
    # ~tens-of-MB download), through the same gate `run_main` uses. Required
    # by default; `--skip-preflight` is the explicit waiver (mirroring
    # `run.py`), and the status is threaded into run_sweep so no cell can
    # claim a dense leg this run did not measure.
    embedder_status = gate_dense_leg(
        dense_leg_required=not args.skip_preflight)

    instances = ds.load_dataset(args.split, limit=None, data_path=args.data,
                                download=True)

    if args.mock:
        reader, judge = MockReader(), MockJudge()
    else:
        reader, judge = build_reader(), build_judge()
    extractor_model = None
    if ingest_mode == "v2" and not args.mock:
        from tests.model_adapters import build_extractor_model
        extractor_model = build_extractor_model(max_tokens=None,
                                                temperature=0.0)

    results, winner = run_sweep(
        instances, chunk_turns_values=chunk_points,
        context_cap=args.context_cap,
        max_chunks_per_session=args.max_chunks_per_session,
        ingest_mode=ingest_mode, extractor_model=extractor_model,
        reader=reader, judge=judge, work_dir=args.work_dir,
        limit=args.limit,
        dense_leg_required=not args.skip_preflight,
        embedder_status=embedder_status,
    )
    measured = _dense_leg_verified(results)
    _print_table(results, ingest_mode=ingest_mode,
                 context_cap=args.context_cap,
                 max_chunks_per_session=args.max_chunks_per_session)
    _print_winner(winner, ingest_mode=ingest_mode,
                  context_cap=args.context_cap, measured=measured,
                  results=results)

    payload = {
        "selection_rule": ("v2: maximize evidence_recall@10 subject to "
                           "context_tokens_mean <= context_token_cap; "
                           "tie-break smaller chunk_turns (D2 #1540); "
                           "deterministic cell is view-only (knob-insensitive)"),
        "ingest_mode": ingest_mode,
        "context_token_cap": args.context_cap,
        "max_chunks_per_session": args.max_chunks_per_session,
        "pool_depth": DEFAULT_POOL_SIZE,
        "results": results,
        # #4821 review P3: a not-measured run must not hand out a knob in the
        # MACHINE-readable artifact either. `dense_leg.measured` and the exit
        # code already say so, but `payload.winner.chunk_turns` is the machine
        # equivalent of an operator reading SELECTED — a usable value with no
        # null to notice. When the leg was not measured the knob is withheld
        # and the candidate is named for what it is.
        "winner": winner if measured else None,
        **({} if (measured or winner is None)
           else {"unmeasured_candidate": winner}),
        # #4821: the dense-leg receipt. A sweep can be waived, but it can never
        # be READ as a measurement: `measured` is derived from the per-cell
        # vector_strategy (each cell's report methodology), and the gate's own
        # status rides here so the reason is attributable.
        "dense_leg": {
            "required": not args.skip_preflight,
            "waived": bool(args.skip_preflight),
            "measured": measured,
            "embedder": embedder_status,
            "gate": ("gate_dense_leg (tools/longmem_eval/run.py) — REQUIRED "
                     "unless --skip-preflight"),
        },
        "note": ("winner None in deterministic mode (D2: knob-insensitive), "
                 "when no config is eligible, or when the dense leg was not "
                 "MEASURED (then `unmeasured_candidate` holds what would "
                 "have won) — see selection_rule"),
    }
    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"\nsweep JSON saved to: {args.output}")

    # Gate: the run protocol step-2 gate exits non-zero when no granularity
    # can be selected under the cap (never silently proceed with an
    # unvalidated knob) — and, since #4821, when the dense leg was not
    # MEASURED: a keyword-only sweep is not a selection, it is a debugging
    # view, and no script may land it as a result.
    if ingest_mode == "v2" and (winner is None or not measured):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
