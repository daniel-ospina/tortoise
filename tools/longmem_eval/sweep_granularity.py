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
    ``_dense_leg_verified``'s zero-coverage arm."""
    census = _points_census(outcomes)
    if not census["points_total"]:
        return None
    return round(census["points_embedded"] / census["points_total"], 4)


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

    Exclusion ALONE does not close that class, and the first version of this
    rule said it did (#10355 review P3). Dropping an impossible record leaves
    the cell's coverage ``None`` — or, beside an honest record, a coverage
    computed over a SMALLER population — and ``None`` is exactly what the
    zero-coverage arm cannot see, so a cell whose only census record was
    impossible still came back MEASURED. Two things close it, and the count in
    the returned dict is the second: an impossible pair is COUNTED in
    ``points_refused``, and :func:`_dense_leg_verified` refuses a cell whose
    ``points_refused`` is non-zero. Counting it is also what keeps this
    function auditable for the mixed case (``[(1, 1), (3, 7)]`` sums to a
    ONE-point population): the refused record is visible in ``points_refused``
    instead of vanishing from the population the ratio was computed over. The
    sum of ``points_embedded`` is ``0`` whenever
    the cell's outcomes carry no census at all — so pairing it with ``points_total: None`` published
    ``{points_total: None, points_embedded: 0}``, where a consumer summing
    ``points_embedded`` across cells reads "0 embedded" for a population it
    does not know. That is indistinguishable from "N points, none embedded",
    which is the exact ambiguity the receipt exists to remove.

    A cell that knows about no points therefore reports ``None`` for BOTH
    halves, and a cell that embedded none of N reports ``(N, 0)`` — a real
    zero, which ``_dense_leg_verified`` reads as not-measured.

    One honest wrinkle (#4821 review P3). ``None/None`` beside a ZERO
    ``points_refused`` covers three UNKNOWN states this function cannot tell
    apart: (a) a cell whose outcomes all report ``points_total == 0`` — a KNOWN
    zero; (b) a cell whose records carry no census at all; and (c) a cell that
    knows its total but not how many of its points were embedded — excluded as
    a pair, so the half it does know is not published as a whole. Safe in every
    one of them: a cell with no points can neither reach the context cap nor
    produce evidence recall, so it is never a winner, and (c) still has its
    ``vector_strategy`` witness. Recorded rather than implied: the coverage arm
    treats all three as un-refuted instead of inventing a zero, which is the
    documented rule for an unknown population (see ``_dense_leg_verified``) and
    is what stops this function publishing a ratio nobody measured.

    The fourth state is NOT one of those and is NOT left un-refuted — a record
    that supplies a value for BOTH halves and lies about it (a value that is
    not a valid int, like ``True``, or ``points_embedded > points_total`` or a
    negative). It is counted in ``points_refused`` and the predicate refuses
    the cell, because the surviving ratio was computed over a population the
    record was silently removed from.
    It is unreachable from a real report — ``retrieve.py`` derives both halves
    from ONE Cypher row (``count(p), count(p.embedding)``), so
    ``0 <= points_embedded <= points_total`` holds by construction — which is
    why this is a contract for the NEXT producer rather than a live path."""
    # ONE pair per outcome: an outcome that knows its total but not how many
    # of its points were embedded is excluded from BOTH halves, never counted
    # on one side only — that is what "one pair, never two independent sums"
    # buys, and it is why this sums a filtered list instead of summing two
    # independently filtered lists. The pair is also checked for POSSIBILITY
    # (ints, not bools, 0 <= embedded <= total): a per-record lie rolls up to
    # a coverage outside [0, 1] that the zero-coverage arm cannot see, i.e. an
    # impossible receipt certified as a MEASUREMENT (#10355 review P2).
    #
    # Two different exclusions, deliberately not conflated: a record that does
    # NOT supply both halves (absent or ``None``) is UNKNOWN — it says nothing
    # about the ratio, so it refutes nothing — while a record that supplies a
    # value which is not a valid int (a bool, a float) or a ratio outside
    # ``0 <= embedded <= total`` is a LIE about the ratio: counted, and refused
    # by the predicate that certifies the leg.
    pairs = [(o.get("points_total"), o.get("points_embedded")) for o in outcomes]
    known: list[tuple[int, int]] = []
    refused = 0
    for t, e in pairs:
        if t is None or e is None:
            continue
        if not (isinstance(t, int) and not isinstance(t, bool)
                and isinstance(e, int) and not isinstance(e, bool)):
            refused += 1
            continue
        if 0 <= e <= t:
            known.append((t, e))
        else:
            refused += 1
    known_total = sum(t for t, _ in known)
    known_embedded = sum(e for _, e in known)
    if not known_total:
        return {"points_total": None, "points_embedded": None,
                "points_refused": refused}
    return {"points_total": known_total, "points_embedded": known_embedded,
            "points_refused": refused}


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

    AND a cell that REFUSED a census record (``points_refused`` non-zero) is
    not a measurement either (#10355 review P3). Refused means the record
    supplied BOTH halves and the ratio was impossible, so the coverage that
    survived was computed over a population that record was removed from —
    an exclusion that can only INFLATE the ratio, and whose absence the
    zero-coverage arm cannot see. That is a different state from the unknown
    cell above (which refutes nothing) and is what makes the census's
    POSSIBILITY rule close the class it names rather than only the publishing
    half of it.

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
        if r.get("points_refused"):
            return False
        total = r.get("points_total")
        if r.get("embedding_coverage") == 0.0 and isinstance(total, int) \
                and total > 0:
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
    print(f"dense leg: vector_strategy={','.join(strategies)}  "
          f"embedding_coverage={coverages}  "
          f"({'MEASURED' if _dense_leg_verified(results) else 'NOT MEASURED'})")
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


def _print_winner(winner: dict | None, *, ingest_mode: str,
                  context_cap: int, measured: bool) -> None:
    """Print the winner line — the ONLY artifact an operator sees.

    ``measured`` is a REQUIRED keyword-only argument (PEP 3102: no default
    makes it required, and it still binds by name so it can never be swept
    into ``**kwargs``). There is deliberately no default: a caller that
    forgets would get the SELECTED vocabulary — handing a knob to the pilot
    (step 3) and the 500-Q run (step 5) on no evidence — which is exactly the
    permissive default this module exists to remove. #4821 review P2.
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
        print("\n⚠ DENSE LEG NOT MEASURED (--skip-preflight waiver, or no "
              "embedder): this sweep is NOT A MEASUREMENT — the recall "
              "values above are keyword-only and are not comparable to a "
              "hybrid run.", file=sys.stderr)
        print(f"UNMEASURED candidate granularity: chunk_turns = "
              f"{winner['chunk_turns']} "
              f"(evidence_recall@10 = {winner['evidence_recall@10']:.3f}, "
              f"context_tokens_mean = {winner['context_tokens_mean']:.1f})")
        print("→ NOT selected: do NOT feed this to the pilot (step 3) or the "
              "500-Q run (step 5). Install the pinned embedder (uv sync "
              "--group dev --extra embeddings) and re-run without "
              "--skip-preflight.", file=sys.stderr)
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
                  context_cap=args.context_cap, measured=measured)

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
