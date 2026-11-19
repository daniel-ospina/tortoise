#!/usr/bin/env python
"""#13732 — the CLAIM population's recall, PAIRED across dense-leg states.

WHAT THIS ANSWERS, AND WHAT IT CANNOT
-------------------------------------
`#12186` M2 measured the claim (`:Point`) population at **recall@3 15.7 %** /
**found@deep 71.3 %** on a complete graph with `retrieval.mode = keyword_only` —
the dense leg did not run, because that box had no `embeddings` extra
(``docs/evidence/12186-m2-windowed-quality/README.md``, Finding 7). `#12349` (M3)
then measured the TEXT scorer and closed it: *"the text index does not move, and
the gap was never the scorer."* `#13345` (live writes) and `#13210` (backfill)
have since landed, so the record can serve a dense leg it could not serve then.

This instrument measures that, in the shape the question needs: **the same
queries, the same ids, the same graph, in ONE run, across arms that differ only
in where the dense leg comes from** (or whether it runs at all). Paired, so a
small effect is resolvable at n = 786 instead of being lost in between-run
variance.

⛔ **What it does NOT do, deliberately:**
  * It does not build a retrieval mechanism. It calls the shipped read
    (``sdk.tortoise_fts_query``) exactly as M2 did — same function, same
    ``limit``, same sanitiser — so the arms differ in the LEG, never in the
    query shape.
  * It does not invent record rows. The record's population is a PRECONDITION
    (``#13210`` is the backfill's tool); if the record does not hold the
    tenant's claims, the instrument REFUSES rather than seeding a second writer
    for the record's schema.
  * It does not arm anything new. ``TORTOISE_RECORD_VECTOR_LEG`` is already
    default-ON since ``#13610``; what this measures is whether serving the dense
    leg from the record MOVES claim recall, which is a question about the leg,
    never about a flag.

WHY THE M2 INSTRUMENT COULD NOT HAVE SEEN THE DENSE LEG
-------------------------------------------------------
This is the finding that motivates the file, and it is checkable from the code,
not inferred: ``window_quality_m2.build_point_population`` writes its population
through ``proj._upsert_point_props({"id", "content"})`` — **no embedding key** —
so neither store holds a vector for those nodes. Two consequences, both silent:

1. no ``query_vec`` (no embedder on that box) means the vector strategy is never
   submitted, which the artifact declares as ``keyword_only``; and
2. on a box WITH an embedder, the same population still yields a dense leg that
   can find nothing, because no node carries an ``embedding`` to be near.

So the claim population must be EMBEDDED AND WRITTEN before a dense arm can be
measured at all, and this instrument's seeding step is that prerequisite — the
one thing M2's harness lacked. ⚠️ ``record_vector.coverage`` compares
``record_vectors >= graph_vectors``, and **0 >= 0 is VERIFIED** — so an unseeded
population does not fail the coverage gate, it *passes* it and then serves an
empty record cohort as ``mechanism=pgvector, reason=ok``. That is why step
:func:`require_seeded` asserts a non-zero vector count on BOTH sides before any
arm runs; a gate that can be satisfied by two zeros is not a gate.

THE ARMS (paired; every query is asked in all three)
----------------------------------------------------
``lexical``       the dense leg is NOT submitted — the M2 posture. Reproduced in
                  process by making ``EmbeddingModel.get()`` answer ``None`` for
                  the duration of the arm, which is the documented
                  designed-absence path (``no_embedder``), not a reimplementation
                  of the read. Asserted: the vector leg's trace entry must say
                  ``ran=False``.
``graph_dense``   ``TORTOISE_RECORD_VECTOR_LEG=0`` — the pre-cutover posture: the
                  dense leg answers from the GRAPH (``index`` on an indexed
                  label). This is the control that isolates *where* the leg came
                  from, and it is the arm a record claim must beat to be about
                  the record rather than about having a dense leg at all.
``record_dense``  the arm is ON and the record's coverage is verified, so the
                  dense leg answers from the RECORD. Asserted: the vector
                  entry's ``mechanism`` must be ``pgvector`` — a ``scan`` or
                  ``scan_fallback`` means the record did not answer and the graph
                  did, which is the graph arm wearing the record arm's name.

A FALLBACK IS NOT A FIGURE. The assertion above is the difference between "the
record served this read" and "the graph served it and the trace knew": a
``scan_fallback`` on the ``record_dense`` arm REFUSES the run (exit 3) instead of
publishing it under the record's name, because the whole question is which store
answered.

THE STATISTIC, PRE-REGISTERED BEFORE THE NUMBERS
------------------------------------------------
``recall@3`` is the product's width (``limit=3`` on the capture prior,
``extractor_v2._fts_rows``). The test is the EXACT two-sided McNemar / binomial
on the DISCORDANT pairs only — imported from ``tools/m3_fts_leg_recall.py``
rather than copied, so the two measurements cannot drift in their statistic.

⛔ And the DETECTABLE EFFECT IS STATED BEFORE THE VERDICT, because a paired
delta that the population cannot resolve is not a result. At n = 786 with 80 %
power and α = 0.05 two-sided, ``delta_min = (1.96 + 0.842)·sqrt(p_d / n)``:

============================  ==========
discordance ``p_d``           detectable Δ (pp)
============================  ==========
0.05                          2.24
0.10                          3.16
0.20                          4.47
0.30                          5.47
============================  ==========

So on this population a gain of **≈ 3–5.5 points** is what the test can see, and
anything smaller must be reported as UNRESOLVED rather than as a small win. The
figure is printed by ``--preregister`` (which needs no store, no embedder and no
corpus) and again, with the OBSERVED discordance, before each verdict.

⚠️ ``found@deep`` rides beside every delta because recall@3 alone cannot tell a
better ORDER from a smaller COVERAGE: a leg that gains 3 points at k=3 while
losing rows the depth-50 read used to hold has traded, not won. Such a pair is
labelled ``TRADE`` by :func:`classify`, never ``GAIN``.

USAGE
-----
::

    # the pre-registration — no store, no embedder, no corpus required:
    uv run python tools/claim_dense_leg_13732.py --preregister --population 786

    # the measurement (needs a corpus, an embedder, and a record at that tenant):
    uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
    TORTOISE_RECORD_DSN=... TORTOISE_RECORD_SINGLE_TENANT=1 \\
    TORTOISE_RECORD_JOURNAL=/path/to/journal \\
    uv run python tools/claim_dense_leg_13732.py --points 900 --deep 50 \\
        --out /tmp/claim-dense-13732.json

Exit codes: ``0`` ran · ``2`` precondition refused (named) · ``3`` an arm's trace
contradicted its own declaration · ``4`` argument error.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

#: The production prior's width (`extractor_v2._fts_rows`, `limit=3`). THE
#: decisive k — the other widths are reported so a reader can see the shape.
K_PRIMARY = 3

#: Every k reported. `1` is the misbind axis, `10` is where M2's curve turns.
KS: tuple[int, ...] = (1, 3, 5, 10)

#: M2's depth-50 complete-graph ceiling (71.3 %) was measured at this depth, so
#: `found@deep` here is comparable with it. One read per query at this width:
#: `limit` truncates and never reorders, so every recall@k is a slice of it.
DEEP_LIMIT_DEFAULT = 50

#: What each arm's leg trace MUST show. A run whose trace disagrees is refused
#: (exit 3) — see "A FALLBACK IS NOT A FIGURE" above.
ARM_LEXICAL = "lexical"
ARM_GRAPH_DENSE = "graph_dense"
ARM_RECORD_DENSE = "record_dense"
ARMS: tuple[str, ...] = (ARM_LEXICAL, ARM_GRAPH_DENSE, ARM_RECORD_DENSE)

#: The mechanisms the GRAPH's dense leg can legitimately report (`#4999`).
GRAPH_MECHANISMS: frozenset[str] = frozenset({"index", "scan", "scan_fallback"})

#: The ONE mechanism that proves the RECORD answered (`record_vector`,
#: `search_engine.MECHANISM_PGVECTOR`).
RECORD_MECHANISM = "pgvector"

#: Power/α for the pre-registered detectable effect. Stated as constants so the
#: artifact carries the same numbers the docstring quotes.
ALPHA = 0.05
POWER = 0.80

#: Discordances the pre-registration tabulates. `0.20` is the mid assumption a
#: reader should carry into an unknown corpus; the run re-states the OBSERVED one.
DISCORDANCES: tuple[float, ...] = (0.05, 0.10, 0.20, 0.30)


class Refused(RuntimeError):
    """A precondition that could not be met — named, never silently skipped.

    ``code`` is a stable token so an artifact or a test can key on the reason
    instead of on prose (the shape ``record_vector``'s verdicts and
    ``bounded_refusal`` use in this tree).
    """

    def __init__(self, code: str, message: str, *, exit_code: int = 2) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.exit_code = exit_code


# ── the pre-registered statistic ───────────────────────────────────────────


def _z(p: float) -> float:
    """Standard normal quantile via the inverse error function."""
    return math.sqrt(2.0) * _erfinv(2.0 * p - 1.0)


def _erfinv(x: float) -> float:
    """Inverse error function (Newton on ``math.erf``), |x| < 1.

    Written out rather than imported because the ONE dependency this instrument
    should not have is the statistics stack: `scipy` is not in the base install
    (`uv sync` without the `embeddings`/`parity` extras drops it), and an
    instrument that cannot compute its own pre-registration on the box where the
    measurement is refused is an instrument nobody can pre-register on.
    """
    if x <= -1.0:
        return -math.inf
    if x >= 1.0:
        return math.inf
    # A decent closed-form seed, then Newton — converges in 3-4 steps here.
    ln = math.log(1.0 - x * x)
    t = 2.0 / (math.pi * 0.147) + 0.5 * ln
    y = math.copysign(math.sqrt(math.sqrt(t * t - ln / 0.147) - t), x)
    for _ in range(8):
        err = math.erf(y) - x
        y -= err / (2.0 / math.sqrt(math.pi) * math.exp(-y * y))
    return y


def detectable_effect_pp(
    n: int, discordance: float, alpha: float = ALPHA, power: float = POWER
) -> float:
    """The smallest paired Δ (percentage points) this n can resolve.

    ``delta = (z_{1-α/2} + z_{power}) · sqrt(p_d / n)`` — the standard paired
    (McNemar) power expression with continuity ignored, which is the
    conventional approximation and is deliberately NOT tuned to look precise.

    ⛔ Quoted BEFORE a verdict, every time. A 2-point move at n = 786 with 20 %
    discordance is inside the resolution of the test, and reporting it as a gain
    would be a claim about noise.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0.0 < discordance <= 1.0:
        raise ValueError("discordance must be in (0, 1]")
    return 100.0 * (_z(1.0 - alpha / 2.0) + _z(power)) * math.sqrt(discordance / n)


def preregistration(population: int) -> dict:
    """The block a reader gets BEFORE any number exists.

    It is data, not prose: `--out` writes it into the artifact under
    ``preregistered`` so a later reader can tell a pre-declared threshold from
    one chosen after seeing the result.
    """
    return {
        "population_n": population,
        "primary_k": K_PRIMARY,
        "ks_reported": list(KS),
        "alpha": ALPHA,
        "power": POWER,
        "statistic": "exact two-sided McNemar/binomial on discordant pairs",
        "detectable_delta_pp": {
            f"{d:.2f}": round(detectable_effect_pp(population, d), 2) for d in DISCORDANCES
        },
        "reading": (
            "a paired gain below the detectable delta FOR THE OBSERVED "
            "discordance is UNRESOLVED, not a small win"
        ),
        "trade_rule": (
            "a gain at k=3 that lowers found@deep is TRADE, never GAIN: the "
            "depth-50 read is the coverage the leg had before"
        ),
        "arms": list(ARMS),
        "declared_mechanisms": {
            ARM_LEXICAL: "vector leg ran=False (no query_vec)",
            ARM_GRAPH_DENSE: sorted(GRAPH_MECHANISMS),
            ARM_RECORD_DENSE: [RECORD_MECHANISM],
        },
    }


# ── paired analysis (pure; the half this box CAN run) ──────────────────────


def rank_of(ids: list[str], target: str) -> int | None:
    """1-based rank of ``target`` in ``ids``, or ``None`` when absent.

    Absent is not rank 0 and not rank infinity: ``found@deep`` and ``recall@k``
    are different questions and conflating them is how a coverage loss gets
    published as a ranking win.
    """
    for i, got in enumerate(ids):
        if got == target:
            return i + 1
    return None


def hit(rank: int | None, k: int) -> bool:
    """Is ``rank`` inside the window ``k``?

    Ranks are 1-based and ``None`` is absence; a ``0`` is neither a hit nor an
    absence, so it is refused as nothing rather than admitted by ``rank <= k``.
    """
    return rank is not None and 1 <= rank <= k


def summarise(ranks: dict[str, int | None], k: int) -> dict:
    """One arm's rates over the SAME id set every other arm is scored on."""
    n = len(ranks)
    if n == 0:
        raise ValueError("empty population")
    return {
        "n": n,
        f"recall@{k}": round(sum(1 for r in ranks.values() if hit(r, k)) / n, 4),
        "found_at_deep": round(sum(1 for r in ranks.values() if r is not None) / n, 4),
        "empty": round(sum(1 for r in ranks.values() if r is None) / n, 4),
    }


def paired(a: dict[str, int | None], b: dict[str, int | None], k: int) -> dict:
    """Paired comparison of two arms over the SAME queries (b against a).

    ``only_b`` are the queries b retrieves at k that a did not (the gain,
    n = 786 - recall of a); ``only_a`` the ones it loses. The p-value is on
    ``only_b`` of ``only_a + only_b`` — the discordant pairs, and nothing else,
    because the concordant ones carry no information about the difference.

    ``found_at_deep`` is compared on the SAME pairing, so a delta that only
    re-orders the same candidate set is tellable from one that shrinks coverage.
    """
    if set(a) != set(b):
        raise ValueError("paired comparison over different id sets")
    ids = sorted(a)
    only_a, only_b = 0, 0
    deep_a, deep_b = 0, 0
    for i in ids:
        ra, rb = a[i], b[i]
        ha, hb = hit(ra, k), hit(rb, k)
        if ha and not hb:
            only_a += 1
        elif hb and not ha:
            only_b += 1
        if ra is not None:
            deep_a += 1
        if rb is not None:
            deep_b += 1
    n_disc = only_a + only_b
    return {
        "n": len(ids),
        "k": k,
        "a_recall": round(sum(1 for r in a.values() if hit(r, k)) / len(ids), 4),
        "b_recall": round(sum(1 for r in b.values() if hit(r, k)) / len(ids), 4),
        "delta_pp": round(100.0 * (only_b - only_a) / len(ids), 2),
        "only_a": only_a,
        "only_b": only_b,
        "discordant": n_disc,
        "mcnemar_p_two_sided": _mcnemar_p(only_b, n_disc),
        "found_deep_a": deep_a,
        "found_deep_b": deep_b,
        "found_deep_delta_pp": round(100.0 * (deep_b - deep_a) / len(ids), 2),
        "detectable_pp": round(detectable_effect_pp(len(ids), n_disc / len(ids)), 2)
        if n_disc
        else None,
    }


def _mcnemar_p(only_b: int, n_disc: int) -> float:
    """M3's exact two-sided McNemar, IMPORTED from its one home.

    ⛔ Not re-implemented: `#12349`'s instrument is where this tree's paired
    statistic lives, and a second copy here is a second answer to "how do we
    decide two arms differ".
    """
    from tools.m3_fts_leg_recall import _binom_two_sided

    return round(_binom_two_sided(only_b, n_disc), 6)


def classify(cmp: dict) -> str:
    """One word for a paired result — the label the PR body quotes.

    ``GAIN`` / ``LOSS`` need BOTH significance and resolution: a significant
    delta below the pre-registered detectable effect is ``UNRESOLVED`` (the test
    found an ordering, not a magnitude this population can price). ``TRADE`` is
    a resolved recall@k move that costs ``found@deep`` — real, and not a win.
    """
    p = cmp["mcnemar_p_two_sided"]
    resolved = cmp["detectable_pp"] is not None and abs(cmp["delta_pp"]) >= cmp["detectable_pp"]
    if p >= ALPHA or not resolved:
        return "UNRESOLVED"
    if cmp["found_deep_delta_pp"] < 0:
        return "TRADE"
    return "GAIN" if cmp["delta_pp"] > 0 else "LOSS"


# ── the arm's own claim about itself ───────────────────────────────────────


def vector_entries(trace: list[dict] | None) -> list[dict]:
    """Every leg entry the trace carries for the DENSE leg.

    The entry's name has been ``leg`` since R3 and ``strategy`` in older
    artifacts; both are read so a trace shape change cannot make an arm look
    unasserted (an empty list here is itself a refusal, never a pass).
    """
    out = []
    for entry in trace or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("leg") or entry.get("strategy")
        if name == "vector":
            out.append(entry)
    return out


def check_arm(arm: str, trace: list[dict] | None) -> dict:
    """Assert the arm's trace says what the arm claims. Refuses on mismatch.

    This is the falsifier the brief names: ``record_dense`` must show
    ``mechanism: pgvector`` — from the RECORD, not ``scan_fallback`` — and the
    lexical arm must show a dense leg that did not run. Without this, "the
    record lifted recall" could be a graph answer wearing a record's name.
    """
    entries = vector_entries(trace)
    mechanisms = [e.get("mechanism") for e in entries if e.get("mechanism")]
    ran = [bool(e.get("ran")) for e in entries]
    if not entries:
        raise Refused(
            "trace_missing_vector_leg",
            f"the `{arm}` arm's trace carries no vector leg entry — the arm's "
            f"own claim cannot be checked, so the run is published as nothing",
            exit_code=3,
        )
    if arm == ARM_LEXICAL:
        if any(ran):
            raise Refused(
                "lexical_arm_ran_dense",
                f"the `{arm}` arm's dense leg RAN ({mechanisms or 'no mechanism'}) "
                f"— a hybrid read reported as the keyword-only baseline",
                exit_code=3,
            )
        return {"arm": arm, "ran": False, "mechanisms": mechanisms}
    if not all(ran):
        raise Refused(
            "dense_arm_did_not_run",
            f"the `{arm}` arm's dense leg did not run — a degraded read is not "
            f"an arm (see `require_hybrid_read`)",
            exit_code=3,
        )
    if arm == ARM_RECORD_DENSE:
        if RECORD_MECHANISM not in mechanisms:
            raise Refused(
                "record_dense_not_pgvector",
                f"the `{arm}` arm answered with mechanism(s) {mechanisms} — the "
                f"RECORD did not serve this read, the graph did, so a delta "
                f"from it would be about having a dense leg, not about the "
                f"record",
                exit_code=3,
            )
        if any(m != RECORD_MECHANISM for m in mechanisms):
            raise Refused(
                "record_dense_mixed_mechanism",
                f"the `{arm}` arm mixed mechanisms {mechanisms} across queries — "
                f"a MIXED run cannot be published as one arm",
                exit_code=3,
            )
        return {"arm": arm, "ran": True, "mechanisms": sorted(set(mechanisms))}
    if arm == ARM_GRAPH_DENSE:
        if RECORD_MECHANISM in mechanisms:
            raise Refused(
                "graph_arm_answered_from_record",
                f"the `{arm}` arm reported `{RECORD_MECHANISM}` — "
                f"the disarming value did not take effect, and the control is "
                f"not a control",
                exit_code=3,
            )
        unknown = sorted(set(mechanisms) - GRAPH_MECHANISMS)
        if unknown:
            raise Refused(
                "graph_arm_unknown_mechanism",
                f"the `{arm}` arm reported mechanism(s) {unknown}, none of which "
                f"this instrument knows how to attribute",
                exit_code=3,
            )
        return {"arm": arm, "ran": True, "mechanisms": sorted(set(mechanisms))}
    raise ValueError(f"unknown arm {arm!r}")


# ── preconditions (each one refused by NAME) ───────────────────────────────


def require_corpus(sessions_dir: Path) -> list[Path]:
    """The capture corpus, or a refusal naming the directory that was read."""
    files = sorted(sessions_dir.expanduser().glob("*.jsonl"))
    if not files:
        raise Refused(
            "no_corpus",
            f"{sessions_dir} holds no *.jsonl capture files — the claim "
            f"population is built from REAL turn text (`build_point_population`), "
            f"and a synthetic population would make every rate in the artifact a "
            f"statement about the fixture",
        )
    return files


def require_embedder():
    """The embedder, or a refusal — a dense arm without one does not exist."""
    from tortoise.embeddings import EmbeddingModel

    model = EmbeddingModel.get()
    if model is None:
        raise Refused(
            "no_embedder",
            "no embedding model on this box (a load needs huggingface, which this "
            "box cannot reach) — the dense leg cannot be submitted, so "
            "`graph_dense` and `record_dense` would both be the keyword-only arm "
            "under another name. Install the extras: uv sync --extra embeddings "
            "--extra parity",
        )
    return model


def require_record(dsn: str | None):
    """An armed, reachable record — the dense leg's source, or nothing to measure."""
    from tortoise import record_vector

    if not dsn:
        raise Refused(
            "no_record_dsn",
            "no TORTOISE_RECORD_DSN (or --dsn) — the record is the store this "
            "lane exists to measure, and it must hold the tenant's claims first "
            "(#13210 is the backfill's tool, not this instrument)",
        )
    try:
        import psycopg2  # noqa: F401
    except Exception as exc:
        raise Refused(
            "no_record_driver",
            f"psycopg2 is not importable ({type(exc).__name__}) — every record "
            f"read in this tree goes through it, so the coverage gate and the "
            f"arms could not be measured",
        ) from exc
    if not record_vector.cutover_armed():
        raise Refused(
            "record_leg_disarmed",
            f"{record_vector.ARMING_ENV} is an explicit falsy — the arm is the "
            f"ROLLBACK path, and the `record_dense` arm would be the graph's",
        )
    return dsn


def require_seeded(graph_vectors: int, record_vectors: int | None) -> None:
    """Both stores must hold vectors, or the coverage gate is satisfied by zeros.

    ⛔ ``record_vector.coverage`` compares ``record_vectors >= graph_vectors`` and
    **0 >= 0 is verified**. An unseeded population therefore PASSES the gate and
    then serves an empty cohort as ``mechanism=pgvector, reason=ok`` — the
    failure mode that would have made M2's population look like a dense leg with
    no recall rather than a population with no vectors.
    """
    if graph_vectors <= 0:
        raise Refused(
            "graph_unseeded",
            f"the graph holds {graph_vectors} vector-bearing claim(s) — the "
            f"population was written without embeddings, so no dense leg can "
            f"find anything and every arm would measure the lexical leg",
        )
    if record_vectors is None:
        raise Refused(
            "record_unmeasurable",
            "the record's claim vector count could not be measured — unmeasured "
            "is not zero, and it is not a verdict",
        )
    if record_vectors <= 0:
        raise Refused(
            "record_unseeded",
            f"the record holds {record_vectors} claim vector(s) for this tenant "
            f"— run the backfill (#13210) for the tenant being measured; this "
            f"instrument does not seed node rows and will not fake a cohort",
        )


def require_coverage(verdict) -> dict:
    """The per-label coverage verdict must be VERIFIED for the claim label."""
    if not getattr(verdict, "verified", False):
        raise Refused(
            "coverage_unverified",
            f"the record's `point` coverage is not proven ({getattr(verdict, 'reason', '?')}) "
            f"— the dense leg declines to the graph on an unproven label, so a "
            f"`record_dense` arm would silently be the `graph_dense` arm",
        )
    return {
        "label": "point",
        "verified": True,
        "record_vectors": getattr(verdict, "record_vectors", None),
        "graph_vectors": getattr(verdict, "graph_vectors", None),
    }


# ── the run ────────────────────────────────────────────────────────────────


def _read_arm(
    sdk, arm: str, queries: list[tuple[str, str]], deep: int, sanitise
) -> tuple[dict[str, int | None], list[dict], dict]:
    """Run one arm over every query: ranks per id, the traces, the arm verdict.

    ⛔ ONE read per (query, arm) at ``deep``, and `limit` only truncates — so
    every recall@k is a slice of the same window and the arms are comparable.
    A second read could return a different order and would make the pairing a
    statement about the store's tie-breaking rather than about the leg.
    """
    from tortoise import record_vector

    ranks: dict[str, int | None] = {}
    traces: list[list[dict]] = []
    _saved = os.environ.get(record_vector.ARMING_ENV)
    try:
        if arm == ARM_GRAPH_DENSE:
            os.environ[record_vector.ARMING_ENV] = "0"
        elif arm == ARM_RECORD_DENSE:
            os.environ[record_vector.ARMING_ENV] = "1"
        for point_id, query in queries:
            trace: list[dict] = []
            rows = _query(sdk, arm, sanitise(query), deep, trace)
            ranks[point_id] = rank_of([r.get("id", "") for r in rows], point_id)
            traces.append(trace)
    finally:
        if _saved is None:
            os.environ.pop(record_vector.ARMING_ENV, None)
        else:
            os.environ[record_vector.ARMING_ENV] = _saved
    verdict = check_arm(arm, [e for t in traces for e in t])
    return ranks, traces, verdict


def _query(sdk, arm: str, text: str, deep: int, trace: list[dict]) -> list[dict]:
    """The SHIPPED read, with the dense leg's availability decided by the arm.

    ``lexical`` patches ``EmbeddingModel.get`` to answer ``None`` for the duration
    of ONE call — the documented designed-absence path (``no_embedder``), so the
    read itself is the production read; what changes is that there is no vector to
    submit, which is exactly M2's posture.
    """
    if arm != ARM_LEXICAL:
        return sdk.tortoise_fts_query(
            text,
            entity_type="point",
            limit=deep,
            include_terminal=False,
            _record_reads=False,
            _valid_time_rerank=False,
            leg_trace=trace,
        )
    from tortoise import embeddings

    saved = embeddings.EmbeddingModel.get
    embeddings.EmbeddingModel.get = classmethod(  # type: ignore[assignment]
        lambda cls, *a, **k: None
    )
    try:
        return sdk.tortoise_fts_query(
            text,
            entity_type="point",
            limit=deep,
            include_terminal=False,
            _record_reads=False,
            _valid_time_rerank=False,
            leg_trace=trace,
        )
    finally:
        embeddings.EmbeddingModel.get = saved  # type: ignore[assignment]


def _seed_graph(proj, population: list[dict], model) -> int:
    """Write the claim population to the GRAPH with its derived vector.

    ⛔ THE VECTOR IS THE POINT OF THIS STEP — M2's population was written with
    ``{"id", "content"}`` and no embedding, which is why its dense leg could not
    have found anything even on a box that had one. The write goes through the
    real projection door (``_upsert_point_props``), so the property shape is the
    production one and the full-text index the lexical arm reads is the
    production index.
    """
    vectors = model.encode([row["content"] for row in population])
    for row, vec in zip(population, vectors, strict=True):
        proj._upsert_point_props(
            {"id": row["id"], "content": row["content"], "embedding": list(vec)}
        )
    return len(population)


def run_measurement(args: argparse.Namespace) -> int:
    """The live run. Every precondition is refused by name, never skipped."""
    from tortoise import record_vector
    from tortoise.extractor_v2 import _sanitise_query_text
    from tortoise.sdk import TortoiseSDK

    sessions = Path(os.path.expanduser(args.sessions_dir))
    files = require_corpus(sessions)
    model = require_embedder()
    dsn = require_record(args.dsn or record_vector.record_dsn())

    sys.path.insert(0, str(REPO_ROOT))
    from tools.window_quality_m2 import (
        ID_PREFIX,
        build_point_population,
        harvest_turn_texts,
    )

    # The population's id namespace, taken from the instrument that builds it —
    # NOT sliced out of an id: a suffix-width change would silently widen the
    # prefix and the vector count below would then include a peer population.
    point_prefix = ID_PREFIX["point"]

    texts, corpus_stats = harvest_turn_texts(sessions, args.points * 4)
    funnel: dict = {}
    population = build_point_population(
        texts, args.points, sanitise=_sanitise_query_text, stats=funnel
    )
    if len(population) < args.min_population:
        raise Refused(
            "population_too_small",
            f"{len(population)} claims built (< --min-population "
            f"{args.min_population}) — the detectable effect at this n is not "
            f"a figure the PR can quote as a claim-population result",
        )

    graph_name = args.graph or f"claimdense_{os.getpid()}"
    sdk = TortoiseSDK(namespace=args.namespace, graph_name=graph_name)
    proj = sdk._get_proj()
    written = _seed_graph(proj, population, model)

    graph_vectors = len(
        [
            1
            for r in proj.g.query(
                f"MATCH (n:Point) WHERE n.id STARTS WITH '{point_prefix}' "
                "AND n.embedding IS NOT NULL RETURN n.id"
            ).result_set
        ]
    )
    scope = record_vector._resolve_scope()
    if scope is None:
        raise Refused(
            "no_tenant",
            "no record tenant resolved for this process — set "
            "TORTOISE_RECORD_SINGLE_TENANT=1 and the SDK's graph, or the "
            "record's RLS policies read a GUC nobody set and every read returns "
            "zero rows",
        )
    coverage = require_coverage(
        record_vector.coverage("point", scope, graph_vectors=graph_vectors, record_dsn=dsn)
    )
    require_seeded(graph_vectors, coverage["record_vectors"])

    queries = [(row["id"], row["query"]) for row in population]
    arms: dict[str, dict] = {}
    for arm in ARMS:
        ranks, _traces, verdict = _read_arm(sdk, arm, queries, args.deep, _sanitise_query_text)
        arms[arm] = {
            "summary": summarise(ranks, K_PRIMARY),
            "summary_at": {f"k{k}": summarise(ranks, k) for k in KS},
            "trace_verdict": verdict,
            "_ranks": ranks,
        }

    comparisons = {}
    for base, cand in (
        (ARM_LEXICAL, ARM_RECORD_DENSE),
        (ARM_LEXICAL, ARM_GRAPH_DENSE),
        (ARM_GRAPH_DENSE, ARM_RECORD_DENSE),
    ):
        key = f"{cand}_vs_{base}"
        per_k = {f"k{k}": paired(arms[base]["_ranks"], arms[cand]["_ranks"], k) for k in KS}
        cmp = per_k[f"k{K_PRIMARY}"]
        comparisons[key] = {
            "per_k": per_k,
            "primary": cmp,
            "label": classify(cmp),
            "detectable_pp_pre_registered": round(
                detectable_effect_pp(cmp["n"], DISCORDANCES[2]), 2
            ),
        }

    for arm in ARMS:
        arms[arm].pop("_ranks", None)

    artifact = {
        "issue": 13732,
        "measured_for": 13732,
        "purpose": (
            "claim (Point) recall, PAIRED across dense-leg states, on the "
            "population #12186 M2 measured at 15.7 % keyword-only"
        ),
        "head": _git_head(),
        "graph": graph_name,
        "corpus": {"files": len(files), "stats": corpus_stats, "point_build": funnel},
        "population": {"built": len(population), "written": written},
        "coverage": coverage,
        "preregistered": preregistration(len(population)),
        "deep_limit": args.deep,
        "arms": arms,
        "comparisons": comparisons,
        "not_measured": [
            "the `:Object` population (#11901's capture prior) — unchanged here",
            "any end-to-end answer quality: the queries are verbatim spans of "
            "the claim's own text (#12186's declared self-retrieval bound)",
        ],
    }
    out = Path(args.out) if args.out else None
    text = json.dumps(artifact, indent=2, sort_keys=True)
    if out:
        out.write_text(text + "\n")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)
    return 0


def _git_head() -> str:
    try:
        import subprocess

        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


# ── entry point ────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    r = argparse.ArgumentParser(
        prog="claim_dense_leg_13732",
        description="claim-population recall, paired across dense-leg states",
    )
    r.add_argument(
        "--preregister",
        action="store_true",
        help="print the pre-registered statistic and detectable "
        "effects for --population; touches no store",
    )
    r.add_argument(
        "--population",
        type=int,
        default=786,
        help="the PRE-REGISTRATION n (default: M2's 786 built claims)",
    )
    r.add_argument(
        "--points",
        type=int,
        default=900,
        help="claims REQUESTED from the corpus (M2: 900 -> 786 built)",
    )
    r.add_argument(
        "--min-population",
        type=int,
        default=600,
        help="refuse below this n, where the detectable effect is too coarse to call a result",
    )
    r.add_argument("--deep", type=int, default=DEEP_LIMIT_DEFAULT)
    r.add_argument("--ks", default=",".join(str(k) for k in KS))
    r.add_argument("--sessions-dir", default="~/.tortoise/session-events")
    r.add_argument("--dsn", default="", help="the record's DSN (default: TORTOISE_RECORD_DSN)")
    r.add_argument("--namespace", default="claimdense_13732")
    r.add_argument("--graph", default="")
    r.add_argument("--out", default="")
    return r


def main(argv: list[str] | None = None) -> int:
    global KS, K_PRIMARY
    args = _parser().parse_args(argv)
    try:
        ks = tuple(int(k) for k in str(args.ks).split(",") if k.strip())
    except ValueError:
        print("REFUSED: --ks must be comma-separated integers", file=sys.stderr)
        return 4
    if not ks:
        print("REFUSED: --ks is empty", file=sys.stderr)
        return 4
    KS = ks
    if K_PRIMARY not in KS:
        print(f"REFUSED: --ks must include the primary k={K_PRIMARY}", file=sys.stderr)
        return 4
    if args.deep < max(KS):
        print(
            f"REFUSED: --deep {args.deep} is below the widest k "
            f"{max(KS)} — found@deep would be a slice that cannot hold it",
            file=sys.stderr,
        )
        return 4
    if args.preregister:
        print(json.dumps(preregistration(args.population), indent=2, sort_keys=True))
        return 0
    try:
        return run_measurement(args)
    except Refused as exc:
        print(f"REFUSED[{exc.code}]: {exc.message}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
