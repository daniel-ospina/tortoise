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
not inferred: ``window_quality_m2.write`` writes its population through
``proj._upsert_point_props({"id", "content"})`` (``window_quality_m2.py:850``;
``build_point_population`` only BUILDS the dicts, it writes nothing) — **no
embedding key** — and on the box M2 ran on, no node carried a vector.

⛔ **THE REASON IS THE EMBEDDER, NOT THE PAYLOAD.** ``_upsert_point_props``
DERIVES an embedding from ``content`` whenever the payload owns no ``embedding``
key: ``tortoise/projection/entities.py:2968-2976`` — ``elif not op and
p.get("content"): encode_for_row(p.get("content", ""),
self.required_embedding_dim)``, inside an ``except Exception: pass``. So on a box
WITH an installable embedder, M2's exact payload would have carried vectors; it
did not because that box had none, ``encode_for_row`` raised, and the blanket
``except`` swallowed it. The tree says so about itself at
``tortoise/record_writer.py:1815``: *"the graph fold derives it and writes it
straight onto the node, #7845"*. Consequences, both silent:

1. no ``query_vec`` (no embedder on that box) means the vector strategy is never
   submitted, which the artifact declares as ``keyword_only``; and
2. no node carries an ``embedding`` to be near, because the derive path failed
   silently — a fact about the BOX, not about the harness.

So the claim population must be EMBEDDED AND WRITTEN before a dense arm can be
measured at all, and this instrument's seeding step is that prerequisite — but
what it buys is a vector under **this** run's embedder, written unconditionally
by the instrument itself rather than inherited from the fold's best-effort
branch, and therefore not dependent on the EMBED gate, on the adapters, or on
the graph's prior state. It is not a repair of a payload M2 wrote "wrong": M2's
payload was the production one, and it was right.

⚠️ ``record_vector.coverage`` compares
``record_vectors >= graph_vectors``, and **0 >= 0 is VERIFIED** — so an unseeded
population does not fail the coverage gate, it *passes* it and then serves an
empty record cohort as ``mechanism=pgvector, reason=ok``. That is why
:func:`require_seeded` asserts a non-zero vector count on BOTH sides before any
arm runs; a gate that can be satisfied by two zeros is not a gate.

⛔ **AND THE COUNT IS NOT THE WHOLE GUARD — the IDS are.** ``coverage`` asks a
LABEL-WIDE question by design (*"does the record hold as many vectors as the
graph serves?"*, ``#11395`` step 2), so on any tenant holding enough embedded
``Point`` rows it answers VERIFIED for a population whose OWN ids are in neither
store: the ``record_dense`` arm would then rank none of them, report
``recall@3 = 0.0``, and publish *"the record's dense leg collapses claim recall"*
with a valid McNemar p — a figure about the seeding step wearing the leg's name.
That is the ``0 >= 0`` trap one level deeper, and it is why this instrument asks a
SECOND, id-scoped question:
:func:`record_population_vectors` counts how many of the POPULATION's own ids the
record holds a vector for, and :func:`require_population_in_record` REFUSES the
``record_dense`` ARM when that is not all of them — the arm, not the run: the
``lexical`` and ``graph_dense`` arms need no record cohort and still publish.
``coverage`` is left as it is — changing it
would reverse a documented decision and break every other label's reader; the
id-set half belongs here, where the population is known.

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
0.05                          2.23
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
    TORTOISE_RECORD_ORG_ID=<org> TORTOISE_RECORD_GRAPH_ID=<graph> \\
    TORTOISE_RECORD_JOURNAL=/path/to/journal \\
    uv run python tools/claim_dense_leg_13732.py --points 900 --deep 50 \\
        --out /tmp/claim-dense-13732.json

⛔ ``TORTOISE_RECORD_ORG_ID``/``TORTOISE_RECORD_GRAPH_ID`` are NOT optional and
not implied by ``TORTOISE_RECORD_SINGLE_TENANT``: :func:`tortoise.record_writer.record_tenant`
returns the env PAIR and refuses a HALF-configured one, so exporting only the
declaration yields ``no_tenant`` before any arm runs (measured: ``record_tenant()
-> None`` with the declaration alone). The tenant is never guessed.

⛔ THE ``record_dense`` ARM NEEDS A STEP THIS TREE DOES NOT HAVE YET. This
instrument writes the population to the GRAPH only (:func:`_seed_graph` is a
direct projection write that emits NO journal record), while the record is fed
**exclusively** by a journal fold (``tortoise/record_vector.py:247``: *"the record
is a journal fold with NO backfill"*), folded by ``tools/record_runner.py apply``.
The population's ids are minted by the M2 harness (``pt-12186…``), so nothing else
in the tree writes them. Consequently :func:`record_population_vectors` returns
``0`` on **every** tenant today and the instrument REFUSES
``record_population_absent`` — unless the population is first written through a
**journaled** door and folded (``python -m tortoise record-reconcile`` /
``tools/record_runner.py apply``) BEFORE this run. The guard is deliberately not
relaxed: publishing a ``record_dense`` figure over ids the record does not hold is
exactly the ``#5090`` Q2 false PASS the id-scoped half exists to stop.

Exit codes: ``0`` ran · ``2`` precondition refused (named) · ``3`` an arm's trace
contradicted its own declaration, **or an arm was BLOCKED by a precondition**
(recorded by name in ``arms``/``not_measured``, with the artifact still written)
· ``4`` argument error **for the two the tool validates itself** (--ks, --deep).
⚠️ An argparse-typed flag (--population, --points, --min-population, --deep,
--ks non-str) fails through ``parser.error()``, which exits **2** with usage and
NO ``REFUSED[...]`` marker — so ``2`` covers both "a precondition refused" and
"you typed a flag wrong"; read the stderr marker, not the code, to tell them
apart. Rule: exit ``3`` alone means "published, but not the complete paired
answer" — never read it as a measurement.
"""

from __future__ import annotations

import argparse
import contextlib
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

#: ⛔ THE BAR RULE, STATED ONCE — as a value, because the artifact EMITS it.
#: `detectable_effect_pp` DECREASES in n, so neither bar is universally harder:
#: the pre-registered bar (n = 786) is the larger one only when the run built MORE
#: claims than that, and `--min-population` is 600, so `600 <= built_n < 786` is
#: reachable from the documented recipe. `classify` and :func:`gated_bar_pp`
#: therefore take the STRICTER of the two, and every statement of the rule — the
#: docstring, the emitted note, and the emitted `detectable_pp_gated` field — says
#: that instead of "the pre-registered one decides", which held only for the
#: larger-n half of the range.
BAR_RULE = (
    "the STRICTER of the pre-registered bar and this run's own"
    " (`max`, because the detectable effect falls as n grows, so neither is"
    " always the harder one)"
)


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

    ⛔ AVAILABILITY IS NOT THE REASON IT IS HAND-ROLLED — an earlier cut of this
    docstring said `scipy` was absent from the base install, and that is false:
    `scipy>=1.10` is a DIRECT dependency (``pyproject.toml``, ``[project]
    dependencies``), so ``scipy.stats.norm.ppf`` is importable wherever this
    instrument's tests run. The reason is proportion: the pre-registration needs
    TWO fixed standard-normal quantiles, and paying a statistics-stack import for
    them in an instrument whose only other statistic is already inside the tree
    (``_binom_two_sided``, stdlib ``math.comb``) is not a trade this file makes.
    The arithmetic is pinned against ``math.erf`` by
    ``test_erfinv_inverts_erf``, and that the file imports no such stack by
    ``test_the_preregistration_needs_no_statistics_stack``.
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


def paired(
    a: dict[str, int | None],
    b: dict[str, int | None],
    k: int,
    preregistered_n: int | None = None,
) -> dict:
    """Paired comparison of two arms over the SAME queries (b against a).

    ``only_b`` are the queries b retrieves at k that a did not (the gain,
    n = 786 - recall of a); ``only_a`` the ones it loses. The p-value is on
    ``only_b`` of ``only_a + only_b`` — the discordant pairs, and nothing else,
    because the concordant ones carry no information about the difference.

    ``found_at_deep`` is compared on the SAME pairing, so a delta that only
    re-orders the same candidate set is tellable from one that shrinks coverage.

    ⛔ TWO THRESHOLDS, AND **BOTH** BIND — the bar is the ``max`` of them (see
    :data:`BAR_RULE`; :func:`classify` is the one implementation).
    ``detectable_pp`` is the bar for the n THIS run built;
    ``detectable_pp_preregistered`` is the bar committed in
    ``preregistration.json`` before any number existed. Gating on the OBSERVED one
    alone would let a corpus change tighten the evidence needed and still call the
    same absolute move a ``GAIN`` — the pre-registration would bind nothing.
    Gating on the PRE-REGISTERED one alone has the opposite hole: the detectable
    effect FALLS as n grows, so a run that built FEWER claims than the
    pre-registration assumed cannot resolve a delta the committed bar calls
    resolvable, and the label would over-claim for its own population. Neither bar
    is universally the harder one, so neither alone is the gate.
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
        "detectable_pp_preregistered": (
            round(detectable_effect_pp(preregistered_n, n_disc / len(ids)), 2)
            if n_disc and preregistered_n
            else None
        ),
        "preregistered_n": preregistered_n,
    }


def _mcnemar_p(only_b: int, n_disc: int) -> float:
    """M3's exact two-sided McNemar, IMPORTED from its one home.

    ⛔ Not re-implemented: `#12349`'s instrument is where this tree's paired
    statistic lives, and a second copy here is a second answer to "how do we
    decide two arms differ".
    """
    from tools.m3_fts_leg_recall import _binom_two_sided

    return round(_binom_two_sided(only_b, n_disc), 6)


def gated_bar_pp(cmp: dict) -> float | None:
    """The resolution bar the label ACTUALLY used — the ``max`` of the two.

    ⛔ ONE HOME, because the artifact EMITS this (`detectable_pp_gated`) and an
    emitted field must not be free to disagree with the gate (see
    :data:`BAR_RULE`). It did: the field carried the pre-registered bar
    unconditionally while `classify` gated on `max`, so on the `600 <= built_n <
    786` band — the exact band the `max()` fix was written for — the artifact
    named a bar the label had not used.
    """
    bars = [
        b
        for b in (cmp.get("detectable_pp_preregistered"), cmp.get("detectable_pp"))
        if b is not None
    ]
    return max(bars) if bars else None


def classify(cmp: dict) -> str:
    """One word for a paired result — the label the PR body quotes.

    ``GAIN`` / ``LOSS`` need BOTH significance and resolution: a significant
    delta below the detectable effect is ``UNRESOLVED`` (the test found an
    ordering, not a magnitude this population can price). ``TRADE`` is a
    resolved recall@k move that costs ``found@deep`` — real, and not a win.

    The resolution bar is ``max(pre-registered, observed)`` — see the comment
    below on why "the pre-registered one" alone is not conservative.
    """
    p = cmp["mcnemar_p_two_sided"]
    # ⛔ THE BAR IS THE STRICTER OF THE TWO, AND THAT IS NOT THE SAME AS "THE
    # PRE-REGISTERED ONE". `detectable_effect_pp` DECREASES in n, so the
    # pre-registered bar (n = 786) is the LARGER one only when the run built
    # MORE claims than that. `--min-population` is 600, so 600..785 is
    # reachable: at n = 700 the run's own resolution is 4.74 pp while the
    # pre-registered bar is 4.47, and gating on the pre-registered bar alone
    # would publish a delta the run cannot resolve as a `GAIN` — the inverse of
    # the class this gate exists to close. `max()` requires resolution at BOTH
    # n: the one the data has, and the one declared before the data.
    bar = gated_bar_pp(cmp)
    resolved = bar is not None and abs(cmp["delta_pp"]) >= bar
    if p >= ALPHA or not resolved:
        return "UNRESOLVED"
    # ⛔ ``TRADE`` IS A GAIN THAT COSTS COVERAGE — never a move that loses BOTH.
    # Requiring only ``found_deep < 0`` labelled a double-negative a ``TRADE``,
    # which is the softer word for a plain regression and contradicts the
    # committed ``trade_rule`` ("a GAIN at k=3 that lowers found@deep"). It is
    # the field the artifact quotes as the verdict, so the softer word is the one
    # mislabel that flatters the arm.
    if cmp["delta_pp"] > 0 and cmp["found_deep_delta_pp"] < 0:
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

    ⛔ ``ran`` ALONE IS NOT ENOUGH — ``degraded`` DECIDES TOO. The engine's shape
    for a FAILED dense leg is ``ran=True, degraded=True, reason="query_failed"``
    *with the mechanism still set* (``search_engine.py:4153`` record leg,
    ``:4742`` graph scan leg), which is precisely how ``require_hybrid_read``
    reads a degraded leg: *"a ``degraded=True`` vector leg contributed no semantic
    results, so the read is keyword-only"* — and that gate is deliberately
    STRICTER than the ``ran``-only capability gate (``:2749-2757``). A ``ran``-only
    check here accepts a run in which every record read declined, and
    ``paired()`` then scores those queries as guaranteed misses — the arm
    publishes *"the record's dense leg collapses claim recall"* with a valid
    McNemar p, when the truth is *"the leg failed"*. The same applies to a MIXED
    trace (some queries healthy, some failed): its mechanics are all
    ``pgvector``, so the mechanism gate cannot see it.
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
    # ⛔ AND A LEG THAT RAN *AND DEGRADED* DID NOT RUN FOR THIS PURPOSE. Every
    # entry reaching here carries `ran=True`; a failed dense read carries
    # `degraded=True` beside it (`search_engine.py:4153`/`:4742`), so the
    # `all(ran)` test above passes over exactly the state this arm must refuse.
    degraded_entries = [e for e in entries if e.get("degraded")]
    if degraded_entries:
        reasons = sorted({str(e.get("reason") or "unstated") for e in degraded_entries})
        raise Refused(
            "dense_arm_degraded_read",
            f"the `{arm}` arm's dense leg RAN but DEGRADED on "
            f"{len(degraded_entries)} of {len(entries)} vector entries "
            f"(reason(s): {', '.join(reasons)}) — a degraded leg contributes no "
            f"semantic results, so every query it touched would enter the "
            f"paired counts as a guaranteed miss; that is a figure about the "
            f"failure, not about the record. `require_hybrid_read` is "
            f"deliberately stricter than a `ran`-only gate for this reason",
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

    try:
        model = EmbeddingModel.get()
    except Exception as exc:  # EmbedderUnavailableError when REQUIRED is armed
        # #4861: `get()` RAISES, rather than answering None, when
        # `TORTOISE_EMBEDDING_MODEL_REQUIRED` is truthy — that raise is the
        # flag's whole point on a request path. Here it must stay a NAMED
        # precondition: an uncaught traceback would present as a crash (exit 1)
        # instead of the refusal this artifact promises (exit 2), for an input
        # the operator controls.
        raise Refused(
            "embedder_required_unavailable",
            f"TORTOISE_EMBEDDING_MODEL_REQUIRED is armed and the model cannot be "
            f"loaded ({exc.__class__.__name__}) — the flag makes `EmbeddingModel.get()` "
            f"raise instead of answering None, so no dense arm can run. Either "
            f"install the extras (`uv sync --extra embeddings --extra parity`) or "
            f"unset the flag; this instrument will not publish a keyword-only arm "
            f"under a dense arm's name",
        ) from exc
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


def _resolve_dsn(explicit: str) -> str | None:
    """The record's DSN from the module that OWNS the arming surface, or a NAMED refusal.

    ⛔ ``record_vector`` does not declare the DSN — it declares the ARM
    (``ARMING_ENV``/``cutover_armed``). The reader lives in
    :func:`tortoise.record_writer.record_dsn`, which is where ``tortoise.__main__``
    and ``record_serve.open_record`` get it; asking ``record_vector`` for it was an
    ``AttributeError`` one expression before the ``no_record_dsn`` refusal this
    file promises, i.e. a traceback where a named precondition belongs — and the
    flag's default is ``""``, so the documented recipes (``export
    TORTOISE_RECORD_DSN=...``) took exactly that path.

    The seam also REFUSES a configured-but-unusable value (wrong scheme, no host,
    whitespace) by raising ``RecordWriteError``. That is a misconfiguration, not
    an unarmed record, so it is converted here rather than escaping as a
    traceback ``main()`` does not catch.
    """
    from tortoise.record_writer import RecordWriteError, record_dsn

    try:
        return record_dsn(explicit or None)
    except RecordWriteError as exc:
        raise Refused(
            "record_dsn_unusable",
            f"the record DSN is set but unusable ({exc}) — fix the value or unset "
            f"it; an unusable record is not an unarmed one, and this instrument "
            f"will not measure the graph's leg under the record's name",
        ) from exc


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


def record_population_vectors(ids: list[str], scope, record_dsn: str | None) -> int | None:
    """How many of THIS population's ids does the record hold a VECTOR for?

    The id-SCOPED half of the precondition :func:`require_seeded` can only state
    as a label-wide count, and the one number that tells *"the record serves this
    cohort"* from *"the record serves some other cohort of the same label"*.

    ⛔ The predicates are :func:`tortoise.record_vector._record_vector_count`'s,
    verbatim — ``org_id``/``graph_id`` from the ONE tenant prologue
    (``record_projection.scope_connection``), the table and id column from that
    module's :data:`LABEL_SERVED`, and ``embedding_vec IS NOT NULL``, which is the
    column the serve slices. A guard that admitted a row the serve cannot rank
    would be the same false PASS one level down.

    ``None`` for every failure, never ``0``: a count that could not be taken is
    not an empty record — the rule ``record_vector`` itself states, kept here.
    """
    if not ids:
        return None
    from tortoise.record_projection import scope_connection
    from tortoise.record_serve import open_record
    from tortoise.record_vector import LABEL_SERVED

    table, id_column = LABEL_SERVED["point"][:2]
    try:
        conn = open_record(record_dsn)
    except Exception:  # unmeasurable, not zero
        return None
    try:
        cur = scope_connection(conn, scope.org_id)
        try:
            sql = (
                f"SELECT count(*) FROM public.{table} AS t "
                "WHERE t.org_id = %s AND t.graph_id = %s "
                f"  AND t.{id_column} = ANY(%s) "
                "  AND t.embedding_vec IS NOT NULL"
            )
            cur.execute(sql, (scope.org_id, scope.graph_id, list(ids)))
            row = cur.fetchone()
            return int(row[0]) if row else None
        finally:
            with contextlib.suppress(Exception):
                cur.close()
    except Exception:  # noqa: BLE001, RUF100 — unmeasurable, not zero
        return None
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def require_population_in_record(matched: int | None, population_n: int) -> None:
    """The record must hold a VECTOR for every id this run would score.

    ⛔ ``coverage``/``require_seeded`` count the whole ``point`` label, so on any
    tenant holding enough embedded claims they answer VERIFIED for a population
    whose own ids are in NEITHER store. The ``record_dense`` arm would then rank
    none of them and publish ``recall@3 = 0.0`` — with a valid McNemar p-value —
    as *"the record's dense leg collapses claim recall"*, when the truth is
    *"this population is not in the record"*. A population id the record cannot
    rank makes every delta on that arm a statement about the seeding step.

    This REFUSES rather than seeding: the record is a journal fold with no
    backfill from this process (``#13210`` is the backfill's tool), and a second
    writer for the record's population is the one thing this instrument promised
    not to be.
    """
    if matched is None:
        raise Refused(
            "record_population_unmeasurable",
            "how many of this population's ids the record holds a vector for "
            "could not be measured — unmeasured is not zero, and it is not a "
            "verdict",
        )
    if matched < population_n:
        raise Refused(
            "record_population_absent",
            f"the record holds a vector for {matched} of the {population_n} "
            f"population id(s) this run would score — the `record_dense` arm "
            f"could rank {matched} of them and would report 0.0 recall for the "
            f"rest, which is a figure about the seed step and not about the leg. "
            f"The record's cohort is a PRECONDITION (#13210 is the backfill's "
            f"tool); this instrument does not seed node rows and will not fake "
            f"a cohort",
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


def _seed_graph(proj, population: list[dict], encode_many=None) -> int:
    """Write the claim population to the GRAPH with an explicit vector.

    ⛔ THE VECTOR IS THE POINT OF THIS STEP, but NOT for the reason the first cut
    of this file gave: ``_upsert_point_props`` DERIVES an embedding from
    ``content`` on a box that has an embedder
    (``projection/entities.py:2968-2976``), so M2's ``{"id", "content"}``
    payload would have carried one there — it carried none because M2's box had
    no embedder and the derive path's ``except Exception: pass`` ate the failure.
    What this step buys is a vector written **unconditionally, by this
    instrument, under this run's embedder** — not inherited from a best-effort
    branch whose behaviour depends on the EMBED gate, the adapter, and whatever
    the node held before. The write goes through the real projection door
    (``_upsert_point_props``), so the property shape is the production one and
    the full-text index the lexical arm reads is the production index.

    ⛔ THE COMPOSITION IS THE WRITE PATH'S, NOT THIS FILE'S (#13734 review,
    finding 3). Every write-side embedder in this tree goes through
    :func:`tortoise.embeddings.compute_embeddings`, which (a) TRUNCATES the text
    to ``EMBED_WORD_BUDGET`` (512) words via ``_truncate_for_embedding`` before
    encoding and (b) returns ``vec.tolist()`` — Python floats, which is the value
    type production hands to Cypher. Seeding with a bare ``model.encode(content)``
    wrote neither: an UNTRUNCATED text's vector (a composition production never
    makes — and ``POINT_TEXT_LIMIT`` is 1200 chars, so 512 words is reachable on
    real turn text) as ``list(numpy_row)``, i.e. a list of ``numpy.float32``
    scalars. Both are fidelity gaps in the one step whose whole purpose is that
    the seeded vector IS the production one, so this delegates to the shipped
    composition instead of re-deriving it.

    ``encode_many`` is the injection seam the tests patch; production never
    passes it. A ``None`` vector (the composition's answer where the encode did
    not happen) is a NAMED refusal, never a written row: seeding a hole would
    make the arm's recall a figure about the hole.

    ⚠️ A GRAPH WRITE ONLY. This is a direct projection write and emits NO journal
    record, so it cannot put this population's ids in the record — see
    :func:`record_population_vectors` and the README's runbook for the step that
    must, and is absent.
    """
    from tortoise.embeddings import compute_embeddings

    encode_many = encode_many or compute_embeddings
    vectors = encode_many([row["content"] for row in population])
    if len(vectors) != len(population):
        raise Refused(
            "seed_vectors_missing",
            f"the embedder answered {len(vectors)} vector(s) for "
            f"{len(population)} rows — the seed would write a population whose "
            f"width is not the one it measured",
            exit_code=2,
        )
    missing = [row["id"] for row, vec in zip(population, vectors, strict=True) if vec is None]
    if missing:
        raise Refused(
            "seed_vectors_missing",
            f"{len(missing)} of {len(population)} claims could not be embedded "
            f"(`compute_embeddings` answers None where the encode did not "
            f"happen) — the first is {missing[0]}; a seeded hole would make the "
            f"`record_dense` arm's recall a figure about the hole",
            exit_code=2,
        )
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
    # The named precondition (`no_embedder` / `embedder_required_unavailable`)
    # still belongs HERE, before the corpus is built; the vector itself is
    # composed by the write path's own seam inside `_seed_graph`.
    require_embedder()

    # ⛔ THE RECORD'S PRECONDITIONS BELONG TO THE `record_dense` ARM, NOT TO THE
    # RUN — the scoping principle the population guard carries one level down,
    # applied to the DSN/driver/tenant gates. `lexical` and `graph_dense` touch the
    # record NOWHERE (`_read_arm` → `_query` → `tortoise_fts_query`; the graph arm
    # sets `ARMING_ENV=0`), so a run-level `no_record_dsn` / `no_record_driver` /
    # `record_leg_disarmed` / `no_tenant` refused the WHOLE run on a box that could
    # still reproduce M2's lexical baseline — publishing nothing while looking
    # like a strict gate. Resolving them here also keeps the tenant check AHEAD of
    # the graph seed, so a refused run leaves no seeded graph behind.
    record_blocked: Refused | None = None
    dsn: str | None = None
    scope = None
    try:
        dsn = require_record(_resolve_dsn(args.dsn))
        scope = record_vector._resolve_scope()
        if scope is None:
            raise Refused(
                "no_tenant",
                "no record tenant resolved for this process — export the PAIR "
                "TORTOISE_RECORD_ORG_ID and TORTOISE_RECORD_GRAPH_ID *and* "
                "TORTOISE_RECORD_SINGLE_TENANT=1 (record_tenant() refuses a half-"
                "configured pair rather than completing it); without it the record's "
                "RLS policies read a GUC nobody set and every read returns zero rows",
            )
    except Refused as exc:
        record_blocked = exc

    # ⛔ THE PRE-REGISTERED n — the bar committed in `preregistration.json`
    # BEFORE any number existed. It is ONE of the two bars that decide; the bar
    # actually applied is their ``max`` (see :data:`BAR_RULE`), and the run's own
    # n is carried beside it so a reader can see which one bound.
    preregistered_n = args.population

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
    written = _seed_graph(proj, population)

    graph_vectors = len(
        [
            1
            for r in proj.g.query(
                f"MATCH (n:Point) WHERE n.id STARTS WITH '{point_prefix}' "
                "AND n.embedding IS NOT NULL RETURN n.id"
            ).result_set
        ]
    )

    # ⛔ THE ID-SCOPED HALF (see the docstring): `coverage` is a LABEL-WIDE count
    # by design, so it verifies a tenant whose record holds SOME embedded points
    # while this population's own ids are in neither store. Asking only the
    # label-wide question publishes a 0.0 `record_dense` recall as a finding
    # about the leg, when it is a finding about the seed step.
    #
    # ⛔ IT GATES THE `record_dense` ARM, NOT THE RUN — and `#13734`'s review
    # round had to move it there. The population's ids are minted by THIS
    # instrument and reach the GRAPH only: `_seed_graph` writes through
    # `proj._upsert_point_props`, which is the apply-fold and emits NO journal
    # record, while the record is a journal fold. So a run-level refusal here is
    # UNSATISFIABLE for every measurement this tool can make, and it also blocked
    # the `lexical` and `graph_dense` arms, which need no record cohort at all —
    # a run that refuses before its first arm publishes nothing while looking
    # like a strict gate. The other two arms still score, and the artifact
    # carries the refusal BY NAME against the one arm it blocks (exit 3) rather
    # than skipping it silently.
    population_ids = [row["id"] for row in population]
    coverage: dict = {}
    population_vectors: int | None = None
    # ⛔ THE GRAPH HALF STAYS RUN-LEVEL. `graph_vectors <= 0` is a fact about the
    # population THIS TOOL just seeded, not about the record — every arm would be
    # measuring the lexical leg, so it refuses the run by name (the case
    # `projection/entities.py` produces by warning and leaving `n.embedding`
    # unset on an embedding-width mismatch, after `_seed_graph` looked fine).
    # Caught into `record_blocked` it would have recorded a GRAPH fact as a
    # record block and stopped refusing the one case it was written for.
    if graph_vectors <= 0:
        raise Refused(
            "graph_unseeded",
            f"the graph holds {graph_vectors} vector-bearing claim(s) — the "
            f"population was written without embeddings, so no dense leg can "
            f"find anything and every arm would measure the lexical leg",
        )
    if record_blocked is None:
        try:
            coverage = require_coverage(
                record_vector.coverage("point", scope, graph_vectors=graph_vectors, record_dsn=dsn)
            )
            require_seeded(graph_vectors, coverage["record_vectors"])
            # ⛔ DROP THE CACHE `coverage` JUST WROTE. The cached verdict is keyed
            # by LABEL and stored stickily, and this call supplied a POPULATION
            # count as the label's denominator — so the shipped read path, which
            # asks the label-wide question, would receive this population-scoped
            # answer instead of measuring for itself.
            record_vector.reset_coverage_cache()
            population_vectors = record_population_vectors(population_ids, scope, dsn)
            coverage["population_n"] = len(population_ids)
            coverage["population_vectors"] = population_vectors
        except Refused as exc:
            record_blocked = exc
            coverage = {}
            population_vectors = None

    queries = [(row["id"], row["query"]) for row in population]
    arms: dict[str, dict] = {}
    blocked: Refused | None = None
    for arm in ARMS:
        if arm == ARM_RECORD_DENSE:
            gate = record_blocked
            if gate is None:
                try:
                    require_population_in_record(population_vectors, len(population_ids))
                except Refused as exc:
                    gate = exc
            if gate is not None:
                blocked = gate
                arms[arm] = {"arm": arm, "blocked": gate.code, "message": gate.message}
                continue
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
        if ARM_RECORD_DENSE in (base, cand) and blocked is not None:
            # A comparison against an arm that was blocked by name is
            # UNMEASURED — never an empty delta a reader could mistake for 0.0.
            comparisons[key] = {"label": "UNMEASURED", "blocked": blocked.code}
            continue
        per_k = {
            f"k{k}": paired(arms[base]["_ranks"], arms[cand]["_ranks"], k, preregistered_n)
            for k in KS
        }
        cmp = per_k[f"k{K_PRIMARY}"]
        comparisons[key] = {
            "per_k": per_k,
            "primary": cmp,
            "label": classify(cmp),
            # ⛔ THE BAR THAT DECIDED, not a number that merely looks like it.
            # This field used to carry the bar at a FIXED assumed p_d = 0.20
            # (always 4.47) while `classify` gated on the bar at the OBSERVED
            # discordance (5.47 at p_d = 0.30, 2.23 at 0.05) — so a reader quoting
            # it could conclude GAIN where the label said UNRESOLVED. The deciding
            # bar is `primary`'s, and it is the `max` of the two bars `classify`
            # binds — naming only the pre-registered one re-stated the superseded
            # rule in the field a reader is pointed at, and made the emitted note's
            # "`detectable_pp_gated` names the one that decided" FALSE on the
            # `600 <= built_n < 786` band (`#13734` round 4). The fixed-p_d figure
            # is kept under a name that says what it is.
            "detectable_pp_gated": gated_bar_pp(cmp),
            "detectable_pp_at_assumed_pd_0.20": round(
                detectable_effect_pp(preregistered_n, DISCORDANCES[2]), 2
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
        # ⛔ A RUN THAT REFUSES AFTER THE SEED LEAVES THE GRAPH BEHIND, AND NOTHING
        # DROPS IT. The tenant/DSN gates now resolve BEFORE `_seed_graph`, so the
        # most common refusal leaves nothing; the ones that need a seeded graph to
        # be answerable (coverage, the id-scoped cohort) cannot, so the artifact
        # declares the leftover rather than letting the next lane discover it —
        # `window_quality_m2` commits the same field for the same reason.
        "cleanup": {
            "graph": graph_name,
            "dropped_by_this_run": False,
            "note": (
                "the population is written to this graph and NOT dropped; on the "
                "lane container it dies with the container, but with `--graph` "
                "pointed at a real tenant it is a leftover. Drop it by hand or "
                "reuse the name."
            ),
        },
        "corpus": {"files": len(files), "stats": corpus_stats, "point_build": funnel},
        "population": {"built": len(population), "written": written},
        "coverage": coverage,
        "preregistered": preregistration(preregistered_n),
        "population_mismatch": (
            None
            if len(population) == preregistered_n
            else {
                "preregistered_n": preregistered_n,
                "built_n": len(population),
                "note": (
                    "the run built a different n than the pre-registration "
                    "declares. BOTH bars bind: the verdict was gated on "
                    f"{BAR_RULE}, so a corpus "
                    "change can only make the bar STRICTER — a smaller run "
                    "cannot buy an easier bar (its own bar is the larger one "
                    "below n = 786) and a larger run cannot lower the declared "
                    "one. `detectable_pp_gated` names the one that decided; "
                    "`detectable_pp_preregistered` and `detectable_pp` carry "
                    "the two inputs"
                ),
            }
        ),
        "deep_limit": args.deep,
        "arms": arms,
        "comparisons": comparisons,
        "not_measured": [
            "the `:Object` population (#11901's capture prior) — unchanged here",
            "any end-to-end answer quality: the queries are verbatim spans of "
            "the claim's own text (#12186's declared self-retrieval bound)",
        ]
        + (
            [
                # ⛔ THE SENTENCE IS THE REFUSAL'S OWN. `blocked` can be any of a
                # dozen codes (no_record_dsn, no_tenant, coverage_unverified,
                # record_unseeded, graph_unseeded, …), and only the cohort pair
                # supports prose about a cohort the record does not hold.
                # Keying on the code family wrote a false cause for the rest.
                f"the `{ARM_RECORD_DENSE}` arm — BLOCKED[{blocked.code}]: {blocked.message}"
            ]
            if blocked is not None
            else []
        ),
    }
    out = Path(args.out) if args.out else None
    text = json.dumps(artifact, indent=2, sort_keys=True)
    if out:
        out.write_text(text + "\n")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)
    # ⛔ THE REFUSAL AND THE EXIT CODE LIVE OUTSIDE THE `if out:` BRANCH.
    # Nesting them under `else:` — where this sat — made the DOCUMENTED
    # invocation (`--out …`, README §5 and the PR body) return 0 on a BLOCKED
    # arm: it wrote the artifact, printed only `wrote …`, and reported success.
    # The one signal the run promises was dead on every documented invocation,
    # and it is the false-PASS class this file exists to close.
    if blocked is not None:
        # ⛔ THE ARTIFACT IS WRITTEN FIRST, THEN THE REFUSAL IS NAMED. The arms
        # that need no record cohort are real measurements and are kept; the
        # exit says the run is INCOMPLETE, so no caller can mistake it for the
        # paired answer the pre-registration exists to price.
        print(f"REFUSED[{blocked.code}]: {blocked.message}", file=sys.stderr)
    return _exit_code(blocked)


def _exit_code(blocked: Refused | None) -> int:
    """The run's exit: ``0`` complete, ``3`` incomplete-but-published.

    ⛔ A PURE HELPER BECAUSE IT IS LOAD-BEARING AND WAS UNPINNED. Inline, the
    ``return 3`` could become ``return 0`` with the whole suite green — which
    would let a partial artifact (two arms measured, `record_dense` BLOCKED)
    publish as the paired answer the pre-registration exists to price.
    """
    return 3 if blocked is not None else 0


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
