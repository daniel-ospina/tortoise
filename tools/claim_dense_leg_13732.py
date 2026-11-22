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
  * It does not invent record rows. It SEEDS them (#13830) — through the ONE
    journaled door (``live_write.write``: append → record projection → graph
    fold), so the record receives exactly the records the journal holds. It does
    not write the record a SECOND way, and if the record does not hold this
    population's ids the ``record_dense`` arm is REFUSED by name rather than
    measured over a cohort that is not there.
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
``tortoise/record_writer.py:1814-1815``: *"the graph fold derives it and writes it
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
population does not fail the coverage gate, it *passes* it and is then served by a
leg whose trace never names the two-zero state — ``empty_results`` from the record
where the contract owns the label, ``no_embeddings``/``degraded=True`` from the graph
everywhere else. That is why the
zero-zero state is refused from BOTH sides before an arm runs — by
:func:`require_record_measured` for the record and by
:func:`require_graph_population` for the graph. One function asserting both was
what let a GRAPH fact refuse the RECORD arm; a gate that can be satisfied by two
zeros is not a gate.

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

    # the measurement (needs a corpus, an embedder, a journal, and a record at
    # that tenant — the population is written THROUGH the journaled door, so the
    # journal is where it enters the world):
    uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
    TORTOISE_RECORD_DSN=... TORTOISE_RECORD_SINGLE_TENANT=1 \\
    TORTOISE_RECORD_ORG_ID=<org> TORTOISE_RECORD_GRAPH_ID=<graph> \\
    TORTOISE_RECORD_JOURNAL=/path/to/journal \\
    uv run python tools/claim_dense_leg_13732.py --points 900 --deep 50 \\
        --out /tmp/claim-dense-13732.json

⛔ THE JOURNAL THIS RUN WRITES IS THE TENANT'S JOURNAL, AND THAT IS THE POINT.
``--journal`` / ``TORTOISE_RECORD_JOURNAL`` names it (one of them is required —
``no_journal``), every population row is APPENDED to it, and the same path is what
``record_vector.journal_ready`` compares the tenant's projected positions against.
So point it at a MEASUREMENT tenant: a run adds ``n`` journal records and ``n``
record rows to whatever tenant the env pair names, and re-running adds them again
(the ids collide, so the rows are upserts; the journal only grows).

⛔ ``TORTOISE_RECORD_ORG_ID``/``TORTOISE_RECORD_GRAPH_ID`` are NOT optional and
not implied by ``TORTOISE_RECORD_SINGLE_TENANT``: :func:`tortoise.record_writer.record_tenant`
returns the env PAIR and refuses a HALF-configured one, so exporting only the
declaration yields ``no_tenant`` before any arm runs (measured: ``record_tenant()
-> None`` with the declaration alone). The tenant is never guessed.

⛔ THE ``record_dense`` ARM IS FED BY THIS INSTRUMENT, THROUGH THE JOURNALED DOOR
(#13830). :func:`_seed_population` writes every population row with ONE call of
the ``#13345`` chokepoint (``tortoise/live_write.py::write``): append the journal
record → project it into the record on that write's own transaction → fold the
graph. So the graph the ``lexical``/``graph_dense`` arms read and the record the
``record_dense`` arm reads hold the SAME population, written through ONE path —
which is the only shape in which the three arms are comparable at all.

⛔ AND IT IS NOT A SECOND WRITER. The alternative — a direct projection write for
the graph plus a hand-rolled record seed — IS the dual-write defect ``#13345``
built the chokepoint to remove: two paths that can disagree, measured and then
published under the leg's name. Before #13830 this file was the GRAPH half of
exactly that shape (``_upsert_point_props`` is the apply-fold and emits no journal
record, so the record could never receive an id that never entered the journal);
now it holds no record write at all (``TestContradictionTest`` pins that by AST)
and no direct graph write either (the same pin, one store over).

⛔ AND EVERY GUARD STAYS. :func:`record_population_vectors` still asks the record
for the POPULATION's own ids, and :func:`require_population_in_record` still
REFUSES the ``record_dense`` arm when they are not all there — a seeding step
that was ATTEMPTED proves nothing about one that LANDED, and publishing a
``record_dense`` figure over ids the record does not hold is exactly the ``#5090``
Q2 false PASS the id-scoped half exists to stop. A shortfall is still a refusal
by name (the arm is BLOCKED, the artifact is kept, the run exits 3) and the
repair is still the record's own fold, never a relaxed guard:
``tools/record_runner.py plan --from-checkpoint`` then ``… apply --acknowledge-plan
<N>`` — the artifact emits BOTH, with this run's tenant filled in, as ``seed.fold``
whenever a write came back unprojected. One command would not be runnable: `apply`
requires ``--org-id``/``--graph-id`` on every verb and refuses a writing run without
the count `plan` prints.

⛔ AND THE DSN IS DECLARED TOO, BECAUSE ``--dsn`` ALONE DOES NOT ARM THE WRITE
(#13839 review, finding 1). ``--dsn`` resolved the value every READ used, while
``live_write.write`` asked ``record_writer.record_dsn()`` — the ENVIRONMENT — and
answered "unarmed": nothing was written, no fold was emitted, the artifact
published ``record_armed: false`` for a store the same run could query, and the
refusal that blocked the arm named only FOLD causes. ``run_measurement`` therefore
declares the resolved value into ``TORTOISE_RECORD_DSN`` for the length of the run
(``main()`` restores the caller's, exactly as it does for the journal), and
:func:`_arm_blockage` blocks the ``record_dense`` arm by name
(``record_arm_undeclared``) if a run holding a DSN ever sees its seam answer
unarmed again — so that mismatch can neither recur silently nor be mistaken for a
fold. ⚠️ A caller that invokes :func:`_seed_population` directly must declare the
same variable itself; the seam reads nothing else.

⛔ AND A WRITE THE RECORD *DECLINED* IS THE FOURTH ANSWER, NOT A ZERO (#13839
review, finding 1). ``live_write`` reports the seam's durable DECLINE
(``no_durable_journal``) as ``applied=False, unprojected=0`` — the SAME shape an
UNARMED deployment has — so a seed the record refused outright used to publish as
clean: no count, no repair, nothing to see. It is now ``seed.record_declined``,
published with a ``declined`` note and a stderr warning, and it deliberately gets
NO ``fold``: a decline means there is no durable line for a fold to replay, so a
repair command there could not work. The artifact also carries
``seed.record_dsn_fingerprint`` — the STORE this run wrote, as the tree's own
secret-hygiene handle (#2903/#2796) — because the emitted ``--dsn
"$TORTOISE_RECORD_DSN"`` names the variable, and only the fingerprint tells an
operator whether their shell's value is the record this run measured.

⛔ THE JOURNAL IS A PRECONDITION, DECLARED, NOT DERIVED. The record is
``replay(journal)``, so a seed that appends nowhere is not a seed; ``--journal``
(or ``TORTOISE_RECORD_JOURNAL``, which also tells ``record_vector.journal_ready``
which journal to compare the tenant's positions against) names the one journal
this run writes, and a run with neither refuses ``no_journal``. Two DIFFERENT
declarations are refused as ``journal_conflict`` rather than silently preferring
one: the record arm's readiness gate compares the record against the DECLARED
path, so seeding a different path would make the gate answer about a journal the
population is not in.

Exit codes: ``0`` ran · ``2`` precondition refused (named) · ``3`` an arm's trace
contradicted its own declaration, **or an arm was BLOCKED by a precondition**
(recorded by name in ``arms``/``not_measured``, with the artifact still written)
· ``4`` argument error **for a flag the tool validates ITSELF** (--ks, --deep,
and the positive-integer bounds on --population/--points/--min-population),
**or an unsupported interpreter** — the ``#5128`` guard below exits ``4`` on
Python < 3.12, because ``0``/``3`` are ANSWERS a reader branches on and ``1`` is
an uncaught traceback.
⚠️ An argparse-**typed** flag (--population, --points, --min-population, --deep
with a non-integer) fails through ``parser.error()``, which exits **2** with
usage and NO ``REFUSED[...]`` marker — so ``2`` covers both "a precondition
refused" and "a flag that does not parse"; read the stderr marker, not the
code, to tell them apart. ⚠️ ``--ks`` is NOT argparse-typed (it has no
``type=``), so ``--ks abc`` does NOT take that path: it exits **4** with
``REFUSED: --ks must be comma-separated integers``. Rule: exit ``3`` alone means
"published, but not the complete paired answer" — never read it as a
measurement.
"""

from __future__ import annotations

import sys

# #5128: refuse an old interpreter BEFORE the imports below — a module-level
# 3.11+-only import raises ImportError before a later guard could run.
#
# The refusal wears THIS tool's own "could not run" status, 4 (the docstring's
# exit table: 0 ran, 2 precondition refused, 3 published-but-incomplete, 4 the
# tool's own non-answer status), because 0 and 3 are ANSWERS a caller branches on
# and 1 is an uncaught traceback — an old interpreter would otherwise read as a
# crash or, worse, as a published figure. The table states this fourth meaning of
# 4 too, so a reader of the published contract is not left to infer it here.
# Pinned by `tests/test_entry_point_python_guard.py::REFUSAL_CODES`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/claim_dense_leg_13732.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/claim_dense_leg_13732.py`",
        file=sys.stderr,
    )
    raise SystemExit(4)

import argparse
import contextlib
import json
import math
import os
import shlex
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
        # ⛔ `ran=True` WITH NO MECHANISM IS NOT A CONTROL. The whole point of this
        # arm is to prove WHERE the leg came from; an empty mechanism set passed
        # the checks above vacuously (nothing unknown, nothing from the record) and
        # published `trace_verdict: {"ran": true, "mechanisms": []}` — a control
        # arm certified with no evidence of its source, the one thing it exists to
        # establish (`search_engine.py` states a vector entry "carries a
        # `mechanism` key naming the path taken"). Same fail-open shape this file
        # already closed for `degraded`, one level up.
        if not mechanisms:
            raise Refused(
                "graph_arm_no_mechanism",
                f"the `{arm}` arm's dense leg reported `ran=True` but named no "
                f"mechanism — a control that cannot say which path it took is "
                f"not a control",
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


def require_journal(explicit: str | None = None) -> Path:
    """The journal this run APPENDS the population to — declared, never derived.

    ⛔ THE RECORD IS ``replay(journal)``, so a seed with no journal is not a seed:
    an id that never entered a journal cannot be folded into the record by any
    backfill, which is the whole of #13830. The path is DECLARED because the other
    half of that contract is a declaration too: ``record_vector.journal_ready``
    compares the record's positions against ``TORTOISE_RECORD_JOURNAL``, so the
    journal this run writes must be the journal the record's readiness gate
    measures. A path derived from a pid or a temp dir would be a journal nothing
    ever compares — the population would be in the record and the gate would be
    answering about a different file.

    ⛔ TWO DIFFERENT DECLARATIONS ARE REFUSED, NOT RESOLVED. ``--journal`` and the
    environment naming two paths is an operator error with a silent-wrong-answer
    shape: preferring either one seeds a journal the other half of the run does not
    measure. ``journal_conflict`` names both, and the operator picks.

    ``no_journal`` when neither is set — unnamed, not empty. A default (a temp
    file, a pid-derived name) would make every run a run against a throwaway
    journal that the record's readiness gate cannot see, which would be a "pass"
    the operator could not reproduce.
    """
    from tortoise import record_vector

    given = (explicit or "").strip()
    declared = (os.environ.get(record_vector.RECORD_JOURNAL_ENV) or "").strip()
    if (
        given
        and declared
        and os.path.abspath(os.path.expanduser(given))
        != os.path.abspath(os.path.expanduser(declared))
    ):
        raise Refused(
            "journal_conflict",
            f"--journal ({given}) and {record_vector.RECORD_JOURNAL_ENV} "
            f"({declared}) name DIFFERENT journals — the record arm's readiness "
            f"gate compares the tenant's positions against the DECLARED path, so "
            f"seeding the other one would make the gate answer about a journal "
            f"this population is not in. Name one path in both places",
        )
    chosen = given or declared
    if not chosen:
        raise Refused(
            "no_journal",
            f"no journal to write the population to — set --journal or "
            f"{record_vector.RECORD_JOURNAL_ENV}. The population enters through "
            f"the journaled door (#13345: append → record → fold), so the record "
            f"is ``replay(journal)`` and a seed appended nowhere can never reach "
            f"it (#13830)",
        )
    # ⛔ RESOLVED TO AN ABSOLUTE PATH BEFORE IT IS USED OR DECLARED. The same
    # string goes to `EventLog`, to `record_vector.RECORD_JOURNAL_ENV` and into the
    # `seed.fold` commands an operator pastes — three readers, so a relative
    # path would make each of them depend on its own working directory.
    return Path(os.path.abspath(os.path.expanduser(chosen)))


def require_record_measured(record_vectors: int | None) -> None:
    """The RECORD's count must be a measurement, and a non-zero one.

    ⛔ ``record_vector.coverage`` compares ``record_vectors >= graph_vectors`` and
    **0 >= 0 is verified**. An unseeded population therefore PASSES the gate and is
    then served with a trace that does not name the two zeroes (``empty_results``
    from the record where the contract owns the label, ``no_embeddings`` /
    ``degraded=True`` from the graph everywhere else) — the
    failure mode that would have made M2's population look like a dense leg with
    no recall rather than a population with no vectors.

    ⛔ AND THIS IS THE RECORD HALF ONLY — the zero-zero trap is closed by TWO
    guards now that each own their fact (#13745 review round 2). It used to be
    one function asserting both sides, called inside the record block's
    ``try/except``: a zero-vector GRAPH therefore raised ``graph_unseeded``
    somewhere that caught it as a RECORD block, so the ``record_dense`` arm was
    blocked by a graph fact — under a message ("every arm would measure the
    lexical leg") that is false for an arm reading Postgres — and the headline
    ``record_dense_vs_lexical`` comparison went ``UNMEASURED``. The graph fact
    now refuses where it belongs, on the ``graph_dense`` arm, via
    :func:`require_graph_population`; this function never names the graph.
    """
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

    The id-SCOPED half of the precondition :func:`require_record_measured` can
    only state as a label-wide count, and the one number that tells *"the record serves this
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


def graph_population_vectors(proj, population: list[dict]) -> int | None:
    """How many of THIS population's rows carry a vector AND THIS run's TEXT?

    ⛔ THAT IS THE WHOLE CONTRACT — read the body before quoting it. It asserts
    the write REACHED the row, not that the stored VECTOR is this run's: the fold
    preserves an old vector when the payload's is refused, so a reused ``--graph``
    can pass this count with a foreign vector (see the residual below).

    The graph half of the same question :func:`record_population_vectors` asks
    the record, and it exists because the run-level gate beside the seed asks a
    PREFIX-wide question: ``MATCH (n:Point) WHERE n.id STARTS WITH … AND
    n.embedding IS NOT NULL`` counts whatever the prefix happens to hold, not
    what this run wrote. Ways that count is above zero while the population is
    only partly seeded — and ⛔ ONLY THE TWO THIS WRITE PATH CAN REACH, because
    a message that names an unreachable cause sends the operator to a dead end
    (``#13745`` review round 6, finding 3). :func:`_seed_population` puts
    ``{"id", "content", "embedding"}`` under ``point`` on every journal record,
    and the fold takes its ``owns_embedding`` branch from that payload, so
    ``owns_embedding`` is ALWAYS true and the fold never reaches its
    ``encode_for_row`` derive branch: an operator point, an EMBED-gate refusal
    (``#5008``) and a swallowed encode failure are all UNREACHABLE here (and a
    ``None`` from ``compute_embeddings`` is refused by name as
    ``seed_vectors_missing`` before a single write). What IS reachable:

    * an embedding-WIDTH mismatch — the fold warns and leaves the node's vector
      alone rather than write a vector the index cannot hold (``#5004``);
    * a vector the fold REFUSES as unusable — ``_writable_journalled_vector``
      answers ``None`` for an empty list, an implausibly long one, or one
      carrying a non-finite/non-numeric entry, and the payload then owns a
      field with no usable value, so nothing is written.

    ⛔ AND NOTHING ELSE. A row this run's own write never created cannot be the
    explanation here: the seed appends and folds every population row before this
    guard runs and a failure raises straight out of it, so ``matched < n`` means
    the VECTOR is missing, never the row. The text half of the pairing is still
    kept — it is what would catch a store that lost a row, or a guard reading a
    different graph than the seed wrote — but it is DEFENSIVE, not a cause to
    name at an operator.

    ⛔ WHAT THE PAIRING PROVES, AND WHAT IT DOES NOT. It proves the write
    REACHED the node: the returned row's text must be the text this run asked
    for, so a row the seed did not write — a node absent, or one still holding an
    earlier population's text at that id — is not counted. That is a DEFENSIVE
    guard, not the cause of a shortfall (see "AND NOTHING ELSE" above;
    ``#13745`` review round 3, finding 1: an earlier cut of this docstring
    claimed more than that, and the claim was false).

    It does NOT prove the stored VECTOR is this run's. ``_upsert_point_props``
    rewrites ``n.content`` unconditionally (``"n.content=$content"``) while its
    embedding clause PRESERVES ``n.embedding`` when the payload's vector was
    refused (the width gate, ``#5004``) — so on a reused ``--graph`` (the ids are
    deterministic, ``f"{ID_PREFIX['point']}{index:06d}"``, so every one collides)
    a refused vector leaves THIS run's text beside the OLD vector and this count
    passes.

    Residual, named rather than papered over: same id + THIS run's text + a
    preserved vector is indistinguishable HERE, because this is a RE-READ. It is
    closable without touching the store: ``_upsert_point_props`` already returns
    whether the row's vector was written (``(embedding is not None, …)``,
    ``projection/entities.py:3339``) and the fold path this seed now uses DISCARDS
    that value — capturing it per row would make the count exact, where a re-read
    cannot. Until
    then this guard is an assertion that the population WAS WRITTEN, not that
    every vector was REPLACED.

    A ``graph_dense`` arm over a partially seeded population ranks only the
    seeded part, so its delta against ``record_dense`` is a figure about the
    seed step wearing the leg's name — the same false PASS the record-side guard
    closes, one store over.

    ``None`` for an UNREADABLE graph, never ``0``; an empty result is a real
    measurement (``0``, an empty graph) and the guard refuses it by name.
    """
    if not population:
        return None
    expected = {row["id"]: row.get("content") for row in population}
    try:
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids AND n.embedding IS NOT NULL RETURN n.id, n.content",
            params={"ids": list(expected)},
        ).result_set
        # ⛔ THE WHOLE PAIRING IS INSIDE THE GUARD: a row shape this code does not
        # expect must answer `None` (unmeasurable) like every other failure, not
        # raise out of `run_measurement` as a traceback where the artifact
        # promises `REFUSED[...]` — the record twin converts inside its own
        # `try` for the same reason. AND a short row must not be read as "no
        # nodes": a 1-column answer to a 2-column query is a client shape this
        # code does not understand, and calling that an EMPTY graph would refuse
        # the run with the wrong reason.
        paired = set()
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                raise ValueError(f"unexpected graph row shape: {row!r}")
            if expected.get(str(row[0])) == row[1]:
                paired.add(str(row[0]))
        return len(paired)
    except Exception:  # noqa: BLE001, RUF100 — unmeasurable, not zero
        return None


def require_graph_population(matched: int | None, population_n: int) -> None:
    """Every id the ``graph_dense`` arm would score must carry a vector AND this run's text.

    ⛔ NOT "this run's VECTOR" — the summary said exactly that and claimed more
    than :func:`graph_population_vectors` can see (`#13745` review round 4,
    finding 2). A preserved vector under a refused write passes this guard; the
    residual is named on that function.

    ⛔ IT GATES THE ``graph_dense`` ARM, NOT THE RUN — and the reason is the one
    the record gate beside it already states: a run-level refusal DISCARDS the
    arms that do not depend on the thing that is missing. A partly seeded graph
    does **not** degrade ``lexical``: that arm forces ``EmbeddingModel.get()`` to
    ``None`` and the full-text index it reads is built from ``n.content``, which
    the apply-fold writes UNCONDITIONALLY — only the vector is held back. And it
    does not degrade ``record_dense`` either, which reads Postgres. So the graph
    cohort is a precondition of exactly one arm, and refusing the run over it
    would throw away ``record_dense_vs_lexical`` — the lane's own headline
    question — while looking like a strict gate. That is the failure this file's
    record gate was moved out of, and the graph gate starts where it ended.

    Satisfiable, unlike the record's: this instrument SEEDS the graph itself, so
    the fold has already written the cohort by the time the arm consults
    this. When the graph cannot hold the cohort, the arm is recorded
    ``BLOCKED[graph_population_absent]`` by name and the run exits ``3``.
    """
    if matched is None:
        raise Refused(
            "graph_population_unmeasurable",
            "how many of this population's rows the graph holds THIS RUN'S TEXT "
            "and a vector for could not be measured — unmeasured is not zero, "
            "and it is not a verdict",
        )
    if matched < population_n:
        raise Refused(
            "graph_population_absent",
            f"the graph holds THIS RUN'S TEXT and a vector for {matched} of the "
            f"{population_n} population row(s) this run would score — ⛔ THE COUNT "
            f"IS NOT A VECTOR COUNT: it is the id-AND-text pair, so a row whose "
            f"text is not this run's is not counted even though the graph holds a "
            f"vector for it (that pairing is DEFENSIVE — the seed writes every "
            f"row — so it is not a cause to look for). On this write path the "
            f"reachable causes, and only these: an embedding-WIDTH mismatch (the "
            f"fold leaves the node's "
            f"vector alone rather than write one the index cannot hold, #5004), a "
            f"vector the fold refuses as unusable (`_writable_journalled_vector` "
            f"answers None for empty, over-long, or non-finite values, so nothing "
            f"is written). A "
            f"`graph_dense` arm over that "
            f"population ranks only the seeded part, so its delta against "
            f"`record_dense` is a figure about the seed step and not about the leg",
        )


def require_population_in_record(matched: int | None, population_n: int) -> None:
    """The record must hold a VECTOR for every id this run would score.

    ⛔ ``coverage``/``require_record_measured`` count the whole ``point`` label, so on any
    tenant holding enough embedded claims they answer VERIFIED for a population
    whose own ids are in NEITHER store. The ``record_dense`` arm would then rank
    none of them and publish ``recall@3 = 0.0`` — with a valid McNemar p-value —
    as *"the record's dense leg collapses claim recall"*, when the truth is
    *"this population is not in the record"*. A population id the record cannot
    rank makes every delta on that arm a statement about the seeding step.

    ⛔ IT IS THE POST-CONDITION OF THE SEED, NOT A REFUSAL TO SEED (#13830). The
    population IS written through the journaled door now
    (:func:`_seed_population`), so this guard asks the store that received it
    whether the write LANDED — a seeding step that was attempted proves nothing
    about one that took. It is deliberately asked of the RECORD rather than read
    off the seed's return value: the seed's own count is what the write path
    claimed, and the whole point of this guard is that the claim is not the fact.

    A shortfall is therefore an ORDERING/FOLD fact, not a design limit: the
    journal holds the population and the record has not folded it (a projection
    refusal, an unproven position, or a cut-over graph's enumerated-replay
    refusal). The repair is the record's own fold — ``tools/record_runner.py
    plan --from-checkpoint`` then ``… apply --acknowledge-plan <N>``, which the
    artifact emits as ``seed.fold`` when the seed reported one — never a second
    writer from here and never a relaxed guard.
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
            f"This population WAS written through the journaled door, so the "
            f"reachable causes are the FOLD's, not the writer's — and only these "
            f"(review: a proven-position failure is deliberately NOT among them, "
            f"because that write still lands the row and only its "
            f"`public.journal_event` row): an armed record that REFUSED the "
            f"projection, a row filed under a DIFFERENT tenant than this read's "
            f"scope, an enumerated replay refused on a cut-over graph (all three "
            f"leave no row), or a row whose `embedding` produced no "
            f"`embedding_vec` (row present, not rankable — a width the store's "
            f"converter will not hold). ⛔ PLUS ONE WRITER-SIDE CAUSE THAT IS "
            f"REACHABLE AND IS NOT A FOLD: a write the seam DECLINED — it "
            f"answered, refused and never attempted the row, so there is no "
            f"durable line for a fold to replay. Read the artifact's `seed` block "
            f"to tell them apart: `record_declined > 0` (with its `declined` "
            f"note) IS that arm and means the journal itself was not durable, "
            f"while everything else here is a fold fact. `record_armed: false` on "
            f"a run that resolved a DSN does not reach this message at all — the "
            f"arm is blocked `record_arm_undeclared` instead, so a run whose write "
            f"seam and reads DISAGREE is never reported as a fold shortfall. "
            f"Run the record's own fold over "
            f"the journal this run wrote (`tools/record_runner.py plan "
            f"--from-checkpoint`, then `apply --acknowledge-plan <N>`, "
            f"both carried in the artifact's `seed.fold` with this run's tenant "
            f"filled in), then re-run. This instrument will not write the "
            f"record a second way and will not fake a cohort",
        )


def require_coverage(verdict, denominator: int | None = None) -> dict:
    """The per-label coverage verdict must be VERIFIED for the claim label.

    ⛔ A ZERO DENOMINATOR VERIFIES NOTHING (`#13745` review round 13). The gate
    decides ``record_vectors >= graph_vectors``, so a graph holding no vector
    makes it ``>= 0`` — TAUTOLOGICALLY verified for any record at all — and the
    artifact would publish ``verified: true`` off the exact state this module
    calls "a gate that can be satisfied by two zeros". It is recorded as
    ``verified: None`` with its reason instead: unmeasured is not a pass, and the
    record arm keeps its real protection (the id-scoped cohort guard plus its own
    mechanism check), so the headline comparison still publishes.
    """
    if denominator is not None and denominator <= 0:
        return {
            "label": "point",
            "verified": None,
            "reason": "denominator_zero",
            "note": (
                f"the graph holds {denominator} vector-bearing claim(s), so "
                f"`record_vectors >= graph_vectors` is a tautology — this gate is "
                f"UNVERIFIABLE, not verified; the record arm's cohort is asserted "
                f"by the id-scoped guard instead"
            ),
            "record_vectors": getattr(verdict, "record_vectors", None),
            "graph_vectors": getattr(verdict, "graph_vectors", denominator),
        }
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
        return sdk._tortoise_fts_query(
            text,
            entity_type="point",
            limit=deep,
            include_terminal=False,
            _record_reads=False,
            _valid_time_rerank=False,
            # ⛔ AND THE PAGE RE-PICK, for the SAME reason this file opts out of
            # the valid-time rerank: what this instrument measures is the ORDER
            # (`rank_of`) at a fixed `limit`, so a lever that re-picks which rows
            # survive that limit would make every figure here a composition of
            # the two legs AND the rerank, undisclosed. Both arms opt out, so
            # the comparison stays about the leg — `tests/test_rerank_pool.py`'s
            # census requires that opt-out from every diagnostic that prints an
            # order, as the `docs/runbook` / `docs/evidence` probes already do.
            _rerank_candidates=False,
            leg_trace=trace,
        )
    from tortoise import embeddings

    saved = embeddings.EmbeddingModel.get
    embeddings.EmbeddingModel.get = classmethod(  # type: ignore[assignment]
        lambda cls, *a, **k: None
    )
    try:
        return sdk._tortoise_fts_query(
            text,
            entity_type="point",
            limit=deep,
            include_terminal=False,
            _record_reads=False,
            _valid_time_rerank=False,
            _rerank_candidates=False,   # see the lexical arm's note above
            leg_trace=trace,
        )
    finally:
        embeddings.EmbeddingModel.get = saved  # type: ignore[assignment]


class SeedReport:
    """What ONE seeding pass did, in the three steps the chokepoint fixes.

    ⛔ A PLAIN CLASS RATHER THAN ``@dataclass``, DELIBERATELY. This module is loaded
    by harnesses that build it with ``importlib.util.module_from_spec`` and exec it
    WITHOUT registering it in ``sys.modules`` (this file's own test fixture is one);
    ``dataclasses`` resolves ``from __future__ import annotations`` strings through
    ``sys.modules[cls.__module__]``, so a dataclass here raised ``AttributeError:
    'NoneType' object has no attribute '__dict__'`` at CLASS-DEFINITION time — the
    whole module unimportable for that harness. A tool that can only be imported by
    a loader that registers it is a booby trap for the next loader, and the report
    is small enough that the decorator buys nothing.

    ⛔ THE COUNTS ARE THE POINT, because "the seed ran" and "the record took the
    seed" are different facts and only the second one makes the `record_dense` arm
    measurable (#13830). ``record_unprojected`` > 0 means the journal holds lines the
    record did NOT take — the named disagreement ``live_write`` returns — and the
    repair is the record's own fold, never a relaxed guard.

    ``first_seq``/``last_seq`` are the journal POSITIONS the appends proved, or
    ``None`` when none was proven: a position the append could not prove is
    ``positions_unproven``, and a record row written without one leaves no
    ``public.journal_event`` row — which the readiness gate then reads as a tenant
    that is behind (correctly).

    ⛔ ``record_declined`` IS THE FOURTH STORE ANSWER, and it exists because
    ``applied=False, unprojected=0`` IS TWO FACTS (#13839 review, finding 1).
    ``live_write`` reports the seam's durable DECLINE — its own
    ``no_durable_journal`` token, a scratch buffer that has no journal for a line
    to be missing from — in exactly the shape an UNARMED deployment has. It used
    to be invisible here, so a seed the record refused OUTRIGHT read as clean: 0
    applied, 0 unprojected, no repair. A declined write IS a write the record did
    not take, so it is counted and published — and it is deliberately NOT given a
    ``fold``, because a decline means there is no durable line for a fold to
    replay (:meth:`_fold_needed`).

    ⛔ ``record_dsn_fingerprint`` IS THE STORE THIS RUN WROTE, in the only form of
    a DSN that may be published (``record_writer.dsn_fingerprint``,
    #2903/#2796). The emitted repair names the DSN as a SHELL VARIABLE, which is
    only half of naming the store: the operator still has to know whether their
    ``TORTOISE_RECORD_DSN`` is the record this run measured. The fingerprint is
    that comparison handle — without it, a shell exporting a DIFFERENT record folds
    the population into a store nobody measured (#13839 review, finding 1,
    corollary).
    """

    __slots__ = (
        "first_seq",
        "folded",
        "graph_id",
        "journal",
        "last_seq",
        "org_id",
        "positions_unproven",
        "record_applied",
        "record_armed",
        "record_declined",
        "record_deferred",
        "record_deferred_failures",
        "record_deferred_undrained",
        "record_dsn_fingerprint",
        "record_unprojected",
        "written",
    )

    def __init__(
        self,
        *,
        journal: str,
        org_id: str | None,
        graph_id: str | None,
        written: int,
        record_armed: bool | None,
        record_applied: int,
        record_unprojected: int,
        positions_unproven: int,
        folded: int,
        first_seq: int | None,
        last_seq: int | None,
        record_declined: int = 0,
        record_dsn_fingerprint: str | None = None,
        record_deferred: int = 0,
        record_deferred_failures: int = 0,
        record_deferred_undrained: int = 0,
    ) -> None:
        self.journal = journal
        self.org_id = org_id
        self.graph_id = graph_id
        self.written = written
        self.record_armed = record_armed
        self.record_applied = record_applied
        self.record_unprojected = record_unprojected
        self.positions_unproven = positions_unproven
        self.folded = folded
        self.first_seq = first_seq
        self.last_seq = last_seq
        self.record_declined = record_declined
        self.record_deferred = record_deferred
        self.record_deferred_failures = record_deferred_failures
        self.record_deferred_undrained = record_deferred_undrained
        self.record_dsn_fingerprint = record_dsn_fingerprint

    def _fold_needed(self) -> bool:
        """Is there a record fold to run at all?

        ⛔ NOT MERELY "something went wrong". An UNPROVEN POSITION on an UNARMED
        deployment needs no fold — there is no record for a journal position to be
        missing from — so emitting a repair there would send the operator to a fold
        over a store this run never wrote. An unprojected write always needs one
        (it exists only where the record was armed and refused).

        ⛔ AND A DECLINED WRITE NEEDS NONE (#13839 review, finding 1). The seam's
        ``no_durable_journal`` decline means there is NO durable line to replay — a
        fold over a scratch buffer has nothing to fold — so pointing the operator at
        one would be a repair command that cannot work. It is published as
        ``record_declined``/``declined`` instead: a visible fact, not a silent zero.

        ⛔ AND A DRAINED DEFERRAL NEEDS NONE — BUT AN UNDRAINED ONE DOES (#14222
        review, finding 6). ``record_deferred`` counts writes whose fold was
        QUEUED, so it says nothing about whether the queue reached them: the seed
        drains before reporting, and a converged drain still leaves
        ``record_deferred=N`` with ``applied=0``. Reading ``record_deferred`` here
        would therefore emit a repair for a queue that already ran. What DOES need
        a fold is the remainder a drain that did not finish left behind, and that
        is ``record_deferred_undrained`` — the positions still queued when the
        drain returned ``False``. Without that term the report answered "no fold
        needed" for a record the loop provably had not reached, which is the
        silent-short-record class this file exists to make visible.
        """
        if self.record_unprojected:
            return True
        if self.record_deferred_undrained:
            return True
        # ⛔ AND THE FOLDS THAT RAN AND LOST THE ROW (#14222 review round 16, P2).
        # A drain that CONVERGED proves the queue reached every item; it proves
        # nothing about whether the fold wrote. `circuit_open` — the named outage
        # in this branch's own vocabulary — refuses every deferred fold, drains
        # immediately, and used to report "no fold needed" for a record short by
        # every queued row. The loop's own `failures` delta is the one reading that
        # separates that from a healthy seed.
        if self.record_deferred_failures:
            return True
        return bool(self.positions_unproven) and self.record_armed is not False

    def _fold_note(self) -> str:
        """WHY a fold is named — one sentence per cause.

        The NOTE's order is its own, and deliberately not `_fold_needed()`'s: the
        "ran and lost the row" cause leads here because it is the one an operator
        can do nothing about by re-reading the queue (#14222 review round 17, P3 —
        an earlier revision of this docstring claimed it matched `_fold_needed`'s
        order, which the round that added that cause had already broken).

        ⛔ THE NOTE USED TO BE WRITTEN FOR TWO OF THE THREE TRIGGERS (#14222
        review, P2 of the round that read the undrained arm). It opened
        *"0 write(s) came back unprojected and 0 append(s) proved no position"* —
        true and useless on a run that needs a fold because `record_deferred_
        undrained` is non-zero, which is exactly the state the note exists to
        explain. A reader was told the repair was owed, handed two zero counts and
        no reason. So every cause that can set `_fold_needed()` names itself here,
        and the unproven clause keeps the SAME armed guard `_fold_needed()` uses:
        an unproven position on an unarmed deployment is not a fold, and this note
        is only emitted when a fold IS named.
        """
        causes: list[str] = []
        if self.record_unprojected:
            causes.append(
                f"{self.record_unprojected} write(s) came back unprojected — caught "
                f"by the `record_dense` arm's id-scoped guard")
        if self.record_deferred_undrained:
            causes.append(
                f"{self.record_deferred_undrained} queued position(s) were still "
                f"undrained when the projection loop's drain timed out — the "
                f"journal line committed and the loop had not reached the record "
                f"(#14185)")
        # ⛔ EMITTED AFTER `undrained`, i.e. in ``_fold_needed``'s order (that
        # predicate tests ``unprojected`` → ``deferred_undrained`` →
        # ``deferred_failures``). Round 17 of #14222's review found this block had
        # been inserted ABOVE `undrained`, which made this docstring's ordering
        # claim false; the order is the fix, not the sentence.
        if self.record_deferred_failures:
            causes.append(
                f"{self.record_deferred_failures} deferred fold(s) RAN and did not "
                f"write a row (the loop's own failure counter: `projection_raised`, "
                f"`record_no_outcome` or `projection_refused`) — a converged drain "
                f"is not a written record, so this needs the fold from the journal")
        if self.positions_unproven and self.record_armed is not False:
            causes.append(
                f"{self.positions_unproven} append(s) proved no position — such a "
                f"row lands but not its `public.journal_event` row, so "
                f"`journal_ready` reads the tenant as behind and the arm is "
                f"declined to the graph (refused by the mechanism check)")
        return (
            "The journal holds the population and the record does not (yet): "
            + "; ".join(causes)
            + ". All are healed by `fold`'s two commands, reported here rather "
            "than run in-process because the fold is the record's own door, not a "
            "second writer (#13345)"
        )

    def as_dict(self) -> dict:
        payload = {
            "journal": self.journal,
            "org_id": self.org_id,
            "graph_id": self.graph_id,
            "written": self.written,
            # ⛔ THE THREE-STATE ANSWER, KEPT THREE-STATE. `record_applied == 0` on
            # an UNARMED deployment means "no record in this run", not "the record
            # refused"; and a projection that RAISED leaves arming unmeasured
            # (`None`) rather than false — `LiveWrite.armed`'s own contract, carried
            # here so a reader cannot collapse two facts into one zero.
            "record_armed": self.record_armed,
            "record_applied": self.record_applied,
            "record_unprojected": self.record_unprojected,
            "record_declined": self.record_declined,
            # ⛔ THE FIFTH STORE ANSWER, AND IT IS NOT A LOSS (#14185). With the
            # projection worker armed, `live_write` acknowledges the write and
            # QUEUES the record's fold: `applied` is False and `unprojected` is 1
            # *because the row is not written YET*, not because it was lost. Left
            # unfolded here, a HEALTHY armed seed reported "N write(s) came back
            # unprojected" plus a repair command, and its measurement ran against
            # a record the queue had not drained. So the seed drains the queue
            # before reporting (see `_seed_population`) and counts these apart —
            # a deferred write is neither applied nor unprojected nor declined.
            "record_deferred": self.record_deferred,
            # ⛔ THE PART OF `record_deferred` THE QUEUE DID NOT REACH (#14222
            # review, finding 6). A drain that returned ``False`` leaves positions
            # queued; this is how many, read from the worker's own
            # `pending_positions` (the same quantity `journal_coverage` measures
            # from the record's side). It is what `_fold_needed()` consults, so a
            # report can no longer say "no fold needed" about a record the loop
            # provably had not reached — and a CONVERGED drain still reports 0 here,
            # which is what keeps a healthy armed seed from naming a repair.
            "record_deferred_undrained": self.record_deferred_undrained,
            # ⛔ AND THE PART THE QUEUE REACHED AND LOST (#14222 review round 16,
            # P2). The field above says "the loop did not get there"; this one says
            # "the loop got there and no row came out" — the loop's own failure
            # counter, differenced around this seed. A CONVERGED drain leaves the
            # previous field 0 while every deferred fold was REFUSED
            # (`circuit_open`), so without this the two runs were byte-identical
            # here and both read as healthy.
            "record_deferred_failures": self.record_deferred_failures,
            "positions_unproven": self.positions_unproven,
            "folded": self.folded,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            # ⛔ THE STORE THIS RUN WROTE, AS A COMPARISON HANDLE — never the value
            # (#13839 review, finding 1, corollary). The fold commands name the DSN
            # as a shell variable, so this is what tells the operator whether their
            # `TORTOISE_RECORD_DSN` is the record this run measured; without it, a
            # shell naming a DIFFERENT record folds the population into a store
            # nobody measured, silently. `record_writer.dsn_fingerprint` is the
            # tree's own secret-hygiene form (#2903/#2796).
            "record_dsn_fingerprint": self.record_dsn_fingerprint,
            "door": "tortoise.live_write.write (journal append → record projection → graph fold)",
        }
        # ⛔ THE REPAIR IS TWO COMMANDS, AND BOTH ARE RUNNABLE (review round 2,
        # finding 1). The single `... apply --journal <path>` this emitted first was
        # NOT: `record_runner` requires `--org-id`/`--graph-id` on every verb, and
        # `apply` REFUSES a writing run without `--acknowledge-plan <N>` — a number
        # only `plan` prints (#11469's report-before-drop guarantee, made
        # mechanical). A "repair command" that argparse rejects is the same class of
        # false promise this file keeps closing, so the artifact carries the pair,
        # with the tenant this run already holds filled in.
        #
        # ⛔ AND THE TWO FACES ARE DIFFERENT GATES, so the note names both: an
        # UNPROJECTED write leaves no row and the id-scoped guard catches it, while
        # an UNPROVEN POSITION still lands the row and only its
        # `public.journal_event` row is missing — the id count then PASSES and the
        # arm is declined to the graph and refused by the mechanism check instead.
        if self._fold_needed():
            journal = shlex.quote(self.journal)
            if self.org_id and self.graph_id:
                ident = (
                    f"--org-id {shlex.quote(self.org_id)} --graph-id {shlex.quote(self.graph_id)}"
                )
            else:
                # ⛔ REACHABLE, AND THAT IS WHY IT IS A PLACEHOLDER RATHER THAN AN
                # OMISSION (review round 3, non-blocking). An ARMED record with an
                # UNRESOLVED tenant is a real state: the seam refuses the write
                # `no_tenant`, that refusal sets `unprojected=1`, and this report
                # then has no ids to fill in. Emitting `<org-id>`/`<graph-id>` says
                # "you must supply this" (and the `record_dense` arm is separately
                # blocked `no_tenant`, so no figure depends on it); dropping the
                # flags would emit a command argparse rejects, and guessing an id
                # would be the mis-scoped write `#9192` forbids.
                ident = "--org-id <org-id> --graph-id <graph-id>"
            payload["fold"] = {
                "plan": (
                    f"uv run python tools/record_runner.py plan --from-checkpoint "
                    f"--journal {journal} {ident} --dsn \"$TORTOISE_RECORD_DSN\""
                ),                "apply": (
                    f"uv run python tools/record_runner.py apply --journal {journal} "
                    f"{ident} --dsn \"$TORTOISE_RECORD_DSN\" "
                    f"--acknowledge-plan <N: the count `plan` reports>"
                ),
                # The handle an operator compares their shell against, so "is my
                # $TORTOISE_RECORD_DSN the store this run wrote?" is answerable
                # without a secret in the artifact.
                "dsn_fingerprint": self.record_dsn_fingerprint,
                "why": (
                    "`apply` writes NOTHING without --acknowledge-plan (a number only "
                    "`plan` prints), and `--from-checkpoint` makes the plan report "
                    "what REMAINS rather than the whole journal — the number `apply` "
                    "then wants. Both verbs require --org-id/--graph-id, filled in "
                    "above from this run's tenant. ⛔ `--dsn \"$TORTOISE_RECORD_DSN\"` "
                    "names the store THIS RUN measured, as a shell VARIABLE so no "
                    "secret enters this artifact: `record_runner` otherwise falls back "
                    "to the same environment variable, and with it unset the paste is "
                    "an obvious `no record DSN` refusal — but with it naming a "
                    "DIFFERENT record the fold would write this population into a "
                    "store nobody measured, silently. Export the DSN this run used "
                    "(or pass it on `--dsn`) before pasting. ⚠️ A "
                    "CUT-OVER graph (one holding a `record_cursor` row) refuses an "
                    "ENUMERATED replay whatever the acknowledgement; there the repair "
                    "is the allocator's operator path, not this fold"
                ),
            }
            payload["note"] = self._fold_note()
        # ⛔ A DECLINED WRITE IS PUBLISHED, never left as a clean-looking zero
        # (#13839 review, finding 1). `live_write.write` reports the seam's durable
        # DECLINE as `applied=False, unprojected=0` — the SAME shape an UNARMED
        # deployment has — so without this block a seed the record refused outright
        # is indistinguishable from one it took. It gets NO `fold`: a decline means
        # there is no durable line to replay, so a repair here could not work.
        if self.record_declined:
            payload["declined"] = {
                "count": self.record_declined,
                "why": (
                    f"{self.record_declined} of {self.written} seeded write(s) were "
                    f"DECLINED by the record seam: it answered, refused, and did not "
                    f"attempt the row (`live_write`'s `no_durable_journal`, or another "
                    f"armed DECLINE). The journal append committed, so the population "
                    f"is in the journal and NOT in the record — and NO FOLD HEALS "
                    f"this one, because a decline means there is no durable journal "
                    f"line to replay. Point `--journal` at a real journal path and "
                    f"check the seam's `durable` answer; the id-scoped guard still "
                    f"blocks `record_dense` by name, so no figure depends on it"
                ),
            }
        return payload


def _arm_blockage(dsn: str | None, seed: SeedReport) -> Refused | None:
    """Does the WRITE seam disagree with this run's own arming?

    ⛔ THE MISMATCH THAT MADE THE SEED A SILENT NO-OP, AS A STANDING GUARD
    (`#13839` review, finding 1). A run that holds a DSN has armed the record for
    every READ it makes (`record_vector.coverage`, `record_population_vectors`)
    and every repair it can emit — so a write seam answering *unarmed* is not a
    deployment without a record, it is THIS RUN's two doors reading different
    arming surfaces (`--dsn` resolved a value; `record_writer._live_write_verdict`
    asks the environment). Left unreported, the artifact publishes
    ``record_armed: false`` for a store the same run queries, no fold is emitted,
    and the refusal that eventually blocks the arm names only FOLD causes.

    ⛔ ``armed is None`` IS NOT THIS FAULT. Unmeasured (a projection that raised on
    every row) is a different fact and one this function must not collapse into
    False — it carries `positions_unproven`/`record_unprojected` and its own
    repair. And a run holding NO DSN is legitimately unarmed: the arm is blocked
    by `no_record_dsn` and the other two arms still publish.

    ⛔ A BLOCKED ARM, NOT A REFUSED RUN. The record's preconditions belong to the
    ``record_dense`` arm (see `run_measurement`'s note), so this becomes the arm's
    named blockage and the lexical/graph arms still measure — while the cause the
    operator reads is the real one, not the fold.
    """
    if dsn is not None and seed.record_armed is False:
        return Refused(
            "record_arm_undeclared",
            f"this run holds a record DSN but the write seam answered UNARMED for "
            f"all {seed.written} seeded row(s) — the token this run resolved and "
            f"the token the seam reads are not the same one, so nothing reached "
            f"the record and there is no fold to run. The seam asks the "
            f"ENVIRONMENT (`record_writer.record_dsn()`), so a DSN supplied only "
            f"by flag must be declared into `TORTOISE_RECORD_DSN` for the write "
            f"path (`run_measurement` does this; a caller invoking the seed "
            f"directly must too). ⛔ This is NOT the fold: re-running the fold "
            f"cannot repair a store that was never written, which is why it is "
            f"named here instead of in `require_population_in_record`'s causes",
        )
    return None


def _seed_population(
    proj,
    population: list[dict],
    *,
    journal_path,
    tenant=None,
    encode_many=None,
    record_dsn: str | None = None,
) -> SeedReport:
    """Write the claim population through the ONE journaled door (#13830).

    ⛔ ONE CALL OF THE ``#13345`` CHOKEPOINT PER ROW, and that is the whole design.
    ``live_write.write`` appends the journal record, projects it into the record on
    that write's own transaction, and folds the graph — in that order, from ONE
    function. So the population reaches the record the way every production write
    does (``EventAPI._emit``, ``TortoiseSDK._emit_event``,
    ``ConversationMiner._emit_event`` are the three existing doors), and the graph
    the other two arms read is folded from the SAME journal record the record was
    projected from. There is no second path here to disagree with the record.

    ⛔ THE FAILURE THAT MOTIVATED THIS (#13830). The previous seed called
    ``proj._upsert_point_props`` directly. That is the apply-FOLD: it writes the
    graph and emits **NO journal record**, while the record is fed exclusively by a
    journal fold — so the population's ids (minted here, ``pt-12186…``, written by
    nothing else in the tree) could not reach the record by ANY backfill, and
    ``record_dense`` refused ``record_population_absent`` on every tenant. The
    tempting repair — keep the fold write and hand-insert record rows — is the
    dual-write defect the chokepoint exists to remove, and it is banned structurally
    by ``TestContradictionTest``.

    ⛔ THE VECTOR IS THIS RUN'S, AND THE COMPOSITION IS THE WRITE PATH'S (#13734
    review, finding 3). ``_upsert_point_props`` would DERIVE an embedding from
    ``content`` on a box with an embedder (``projection/entities.py:2968-2976``,
    inside an ``except Exception: pass``), so the vector here is deliberately the
    PAYLOAD's: written unconditionally by this run, under this run's embedder, not
    inherited from a best-effort branch whose behaviour depends on the EMBED gate,
    the adapter and whatever the node held before. And the composition is
    :func:`tortoise.embeddings.compute_embeddings` — TRUNCATING to
    ``EMBED_WORD_BUDGET`` (512) words and returning ``vec.tolist()`` (Python
    floats) — because a bare ``model.encode(content)`` produced an untruncated
    text's vector as ``list(numpy_row)``, a value type no production write makes.
    One encode per row: the payload OWNS the vector, so neither the record's
    derivation nor the graph fold re-encodes it.

    ``encode_many`` is the injection seam the tests patch; production never passes
    it. A ``None`` vector (the composition's answer where the encode did not
    happen) is a NAMED refusal, never a written row: seeding a hole would make the
    arm's recall a figure about the hole.

    ⛔ WHAT A REFUSED RECORD WRITE DOES NOT DO: STOP THE SEED. ``live_write.write``
    never raises after its append (the append is the commit point, #7951), so a
    failed projection leaves a NAMED ``unprojected`` write and the run continues —
    the population is then in the journal and not in the record, which is the
    direction a replay heals. The report counts them (:class:`SeedReport`), the
    artifact publishes the counts, and :func:`require_population_in_record` still
    refuses the arm by name if the record does not hold every id.

    ⛔ ``record_dsn`` DOES NOT ARM ANYTHING — it is the report's, and only the
    report's (#13839 review, finding 1). The door is armed by the run's
    DECLARATION into ``TORTOISE_RECORD_DSN`` (see ``run_measurement``), because the
    seam's predicate asks the environment and ``live_write.write`` has no ``dsn=``
    parameter. What the value is used for here is publishing its FINGERPRINT, so
    the emitted repair can be checked against the store this seed actually wrote.
    """
    from tortoise import live_write
    from tortoise.embeddings import compute_embeddings
    from tortoise.log import EventLog

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
    log = EventLog(journal_path)
    # ⛔ THE LOOP'S LOSS COUNTER, SNAPSHOTTED BEFORE THE FIRST WRITE (#14222 review
    # round 16, P2). `ProjectionStats.failures` is PROCESS-CUMULATIVE, so it must be
    # DIFFERENCED around this seed or an earlier run in the same process would
    # attribute its losses here. Without it the artifact could not tell the two
    # shapes apart that matter most: a converged drain of folds that WROTE, and a
    # converged drain of folds that all FAILED (the `circuit_open` outage, where
    # `_classify_fold_outcome` counts `projection_refused`). Both leave
    # `deferred=N, applied=0, undrained=0`, so `_fold_needed()` answered "no fold
    # needed" for a record that is short by every queued row — the silent-short
    # class this file exists to make visible.
    failures_before = 0
    try:
        from tortoise import projection_worker as _worker_for_baseline

        if _worker_for_baseline.armed():
            failures_before = int(_worker_for_baseline.stats().failures)
    except Exception:      # noqa: BLE001, RUF100 — the worker is optional here
        failures_before = 0
    applied = unprojected = unproven = declined = folded = deferred = 0
    armed_yes = armed_unmeasured = False
    first_seq = last_seq = None
    for row, vec in zip(population, vectors, strict=True):
        # ⛔ THE EVENT SHAPE IS THE PRODUCTION ONE: `PointAdded` with the snapshot
        # under `point`. The record's mutation reads `payload["point"]`
        # (`NodeSnapshot(path="point", key_from="id")`) and the graph fold reads
        # the same carrier (`_apply_one`: `p = ev.get("point")`) — one payload, two
        # stores, so a shape only one of them understood would be a divergence this
        # seed invented.
        event = {
            "type": "PointAdded",
            "point": {
                "id": row["id"],
                "content": row["content"],
                "embedding": list(vec),
            },
        }
        outcome = live_write.write(
            event,
            log=log,
            projection=proj,
            tenant=tenant,
            scope_label="claim_dense_leg_13732.seed",
        )
        if outcome.seq is None and outcome.appended:
            # ⛔ `seq is None` HAS TWO CAUSES (#14002) and only one of them is
            # "unproven". On a cut-over graph the chokepoint does not append at
            # all — the record owns the ordinal — so the outcome has no journal
            # position BY DESIGN and reporting it as an unproven position would
            # turn a healthy record-only write into a false verdict field
            # (`positions_unproven` gates this tool's own report). The
            # discriminator is `appended`, not a guess.
            unproven += 1
        elif outcome.seq is not None:
            first_seq = outcome.seq if first_seq is None else first_seq
            last_seq = outcome.seq
        if outcome.applied:
            applied += 1
        elif getattr(outcome, "deferred", False):
            # ⛔ CHECKED BEFORE `unprojected` (#14185). A deferred write carries
            # `unprojected=1` — truthfully, since the record has not taken the line
            # YET — so the branch below would count a healthy queued write as a
            # lost one. `deferred` is the discriminator `live_write` added for
            # exactly this, and the DRAIN below is what makes the count converged
            # by the time the report is built.
            deferred += 1
        elif outcome.unprojected:
            unprojected += 1
        elif outcome.refusal:
            # ⛔ THE FOURTH ANSWER, AND THE ONE THAT USED TO VANISH (#13839 review,
            # finding 1). Every armed `applied=False` EXCEPT the durable DECLINE
            # sets `unprojected=1` in `live_write.write`, so reaching here means the
            # seam ANSWERED AND DECLINED — `no_durable_journal`, or another armed
            # decline: the record did not take the row and there is no durable line
            # for a fold to replay. Counting it as neither applied nor unprojected
            # made a refused seed read as a clean one.
            declined += 1
        if outcome.armed is True:
            armed_yes = True
        elif outcome.armed is None:
            armed_unmeasured = True
        if outcome.folded:
            folded += 1
    # ⛔ DRAIN THE PROJECTION QUEUE BEFORE REPORTING (#14185). A deferred write is
    # acknowledged before its record row exists, so a report built from the acks
    # alone describes a record the queue has not reached — and
    # `require_population_in_record` downstream would then refuse the arm for a
    # seed that is perfectly healthy, or (worse) the measurement would run against
    # a half-folded population. `wait_for_drain` is the one call that IS correct
    # here: this is an operator tool at the end of a bounded seed, not a read path.
    deferred_undrained = 0
    deferred_failures = 0
    try:
        from tortoise import projection_worker as _worker

        if _worker.armed() and not _worker.wait_for_drain(timeout=120):
            # A drain that did not finish is REPORTED, never assumed: the rows are
            # queued and `live_write`'s own counters name the rest.
            print(
                "WARNING: the projection worker did not drain within 120 s — the "
                "record may be short of positions this seed queued (#14185)",
                file=sys.stderr)
            # ⛔ HOW MANY ARE STILL QUEUED, FROM THE WORKER (#14222 review, finding
            # 6). Without this the report built a `_fold_needed()` from
            # `record_deferred` alone and answered "no fold needed" for a record the
            # loop had provably not reached. `pending_positions` is the queue's own
            # count of journal positions the record has NOT taken. If it cannot be
            # read, fall back to the TOTAL deferred count: a drain that returned
            # `False` leaves at least one, so the conservative answer is the upper
            # bound — a repair suggestion that turns out unnecessary is cheaper than
            # a silently short record.
            try:
                deferred_undrained = max(0, int(_worker.stats().pending_positions))
            except Exception:  # noqa: BLE001, RUF100
                deferred_undrained = deferred
        # ⛔ AND THE LOSSES, whether or not the drain converged (#14222 review round
        # 16, P2): a DRAINED queue is not a WRITTEN record. `wait_for_drain` answers
        # "the folds finished", and a fold that RAISED or was REFUSED finished too —
        # so the delta of the loop's own failure counter is the only reading that
        # separates "the record caught up" from "the loop dropped every row on the
        # floor". Differenced against the pre-write snapshot because the counter is
        # process-cumulative; read inside this try so an unreadable stats object
        # leaves it at zero rather than failing the seed.
        try:
            deferred_failures = max(0, int(_worker.stats().failures) - failures_before)
        except Exception:      # noqa: BLE001, RUF100
            deferred_failures = 0
    except Exception:
        # The worker is OPTIONAL; the seed is not. An unimportable or failing loop
        # leaves the counts below exactly as the inline door reported them.
        pass
    # The STORE this run wrote, in the only publishable form of a DSN
    # (`record_writer.dsn_fingerprint`, #2903/#2796) — and only when a DSN was
    # resolved at all, so an unarmed run publishes `null` rather than a hash of the
    # empty string.
    dsn_fingerprint = None
    if record_dsn:
        from tortoise.record_writer import dsn_fingerprint as _fingerprint

        dsn_fingerprint = _fingerprint(record_dsn)
    return SeedReport(
        journal=str(journal_path),
        org_id=tenant[0] if tenant else None,
        graph_id=tenant[1] if tenant else None,
        written=len(population),
        record_armed=True if armed_yes else (None if armed_unmeasured else False),
        record_applied=applied,
        record_unprojected=unprojected,
        positions_unproven=unproven,
        folded=folded,
        first_seq=first_seq,
        last_seq=last_seq,
        record_declined=declined,
        record_dsn_fingerprint=dsn_fingerprint,
        record_deferred=deferred,
        record_deferred_failures=deferred_failures,
        record_deferred_undrained=deferred_undrained,
    )


def run_measurement(args: argparse.Namespace) -> int:
    """The live run. Every precondition is refused by name, never skipped."""
    from tortoise import record_vector
    from tortoise.extractor_v2 import _sanitise_query_text
    from tortoise.sdk import TortoiseSDK

    sessions = Path(os.path.expanduser(args.sessions_dir))
    files = require_corpus(sessions)
    # The named precondition (`no_embedder` / `embedder_required_unavailable`)
    # still belongs HERE, before the corpus is built; the vector itself is
    # composed by the write path's own seam inside `_seed_population`.
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
        # ⛔ AND THE RESOLVED DSN IS DECLARED FOR THE RUN, because "--dsn arms the
        # reads" is only HALF of arming (#13839 review, finding 1). The write
        # seam's predicate asks the DSN question with NO ARGUMENT —
        # `record_writer._live_write_verdict` reads `record_dsn()`, i.e. the
        # ENVIRONMENT (tortoise/record_writer.py:2754-2759) — so a `--dsn`-only run
        # armed every READ (`record_vector.coverage(record_dsn=dsn)`,
        # `record_population_vectors(..., dsn)`) while `live_write.write` answered
        # "unarmed": nothing written, no fold emitted, `record_armed: false` in the
        # artifact for a store this same run can read, and every named cause in the
        # refusal pointing at the FOLD. The seam cannot be handed a DSN — it asks
        # the environment, by design — so the run declares it, exactly as it already
        # declares the journal below for the same class of reason. The value is the
        # one `--dsn`/`TORTOISE_RECORD_DSN` already resolved (flag wins), never a
        # second reading of the flags; `main()` restores the caller's value.
        from tortoise.record_writer import RECORD_DSN_ENV

        os.environ[RECORD_DSN_ENV] = dsn
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

    # ⛔ THE JOURNAL IS A RUN-LEVEL PRECONDITION, NOT A RECORD-ARM ONE (#13830).
    # Every arm is scored over a population this run WRITES, and the write goes
    # through the journaled door (append → record → fold) — so with no journal
    # there is no seed and therefore no arm, record or not. Resolved here, ahead of
    # `_seed_population`, so a refused run leaves no half-seeded graph behind (the
    # DSN/tenant gates above are ahead of it for the same reason).
    journal_path = require_journal(args.journal)
    # ⛔ AND THE DECLARATION IS MADE EXPLICIT FOR THE READ PATH. `--journal` is
    # enough to WRITE; `record_vector.journal_ready` reads only the env name, so a
    # `--journal`-only run would leave the record arm's per-tenant gate unenforced
    # ("not enforced" + one warning) while this process knows exactly which journal
    # it seeded. Declaring it is the act the arming procedure prescribes; `main()`
    # restores the caller's value when the run returns.
    os.environ[record_vector.RECORD_JOURNAL_ENV] = str(journal_path)

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
    # ⛔ OPENING THE STORE IS A STORE OPERATION TOO, AND IT IS THE ONE A DEAD
    # STORE FAILS ON (`#13745` review round 16). `_get_proj()` runs the OPEN-TIME
    # HEALTH PROBE (`FalkorProjection.from_uri(..., skip_health_check=False)`), so
    # a graph that is simply DOWN raises HERE — before the write guard below ever
    # runs. Guarding only the write therefore left the realistic case producing
    # exit 1, a traceback and no artifact, because `main()` catches only
    # `Refused`; the round-13 comment claimed that case was covered, and it was
    # not. This guard is the one that covers "the store is down".
    try:
        sdk = TortoiseSDK(namespace=args.namespace, graph_name=graph_name)
        proj = sdk._get_proj()
    except Refused:
        raise
    except Exception as exc:
        raise Refused(
            "graph_unopenable",
            f"the graph could not be OPENED ({exc.__class__.__name__}: {exc}) — a "
            f"store that will not answer the open-time health probe cannot be "
            f"measured against, so this run publishes no artifact rather than a "
            f"result about a graph it never reached",
        ) from exc
    # ⛔ AND THE SEED IS A STORE OPERATION TOO (`#13745` review round 13) — and, since
    # #13830, a JOURNAL and a RECORD one as well, because it goes through the ONE
    # door (`live_write.write`: append → record project → graph fold). The guard is
    # therefore about the whole write path, not only the graph: a dead store, an
    # unwritable journal, or an append that cannot be counted all present as one
    # cause from here ("the population was not written"), and the exception text
    # names which step. `except Refused: raise` is what keeps a `Refused` raised
    # INSIDE the seed (the embedder answered no vector) from being re-labelled as a
    # store fault — see `#13745` review round 15.
    tenant = (scope.org_id, scope.graph_id) if scope is not None else None
    try:
        seed = _seed_population(
            proj,
            population,
            journal_path=journal_path,
            tenant=tenant,
            # ⛔ THE REPORT'S OWN (never the door's — the declaration above is what
            # arms it): the artifact has to publish the STORE this seed wrote, or
            # the emitted repair cannot be checked against it (#13839 review,
            # finding 1, corollary).
            record_dsn=dsn,
        )
    except Refused:
        raise
    except Exception as exc:
        # ⛔ THE CODE TOKEN IS KEPT DELIBERATELY. `graph_unwritable` is an existing
        # refusal a caller (and `tests/`) may key on, so the name does not move with
        # the fix; the MESSAGE names the three steps, because the failure can now
        # come from the journal append, the record projection or the graph fold.
        raise Refused(
            "graph_unwritable",
            f"the population could not be written through the journaled door "
            f"({exc.__class__.__name__}: {exc}) — the step that failed is the "
            f"journal append, the record projection or the graph fold, and no arm "
            f"can be measured against a population the write path would not hold, "
            f"so this run publishes no artifact rather than a result about a "
            f"graph that was never seeded",
        ) from exc
    # ⛔ THE WRITE SEAM AND THIS RUN MUST AGREE ABOUT ARMING (#13839 review,
    # finding 1). A run holding a DSN whose seam answered "unarmed" wrote NOTHING
    # to the record, so the `record_dense` arm is blocked by name HERE — at the
    # cause — rather than by `record_population_absent`, whose message would send
    # the operator to a fold that cannot repair a store nobody wrote to. The FIRST
    # blockage wins, so an already-named cause (`no_tenant`, an unreachable
    # record) is not overwritten by this one.
    if record_blocked is None:
        record_blocked = _arm_blockage(dsn, seed)
    if seed.record_unprojected or seed.positions_unproven:
        # ⛔ NOT FATAL, AND NOT SILENT. `live_write.write` never raises after its
        # append (the append is the commit point), so these are lines the RECORD did
        # not take — the direction a replay heals. The run continues so the other
        # arms still publish, the artifact carries the counts, and the id-scoped
        # guard blocks `record_dense` by name if the record is short. The operator
        # gets the exact repair on stderr as well, because an artifact nobody reads
        # until later is not a report.
        print(
            f"WARNING: {seed.record_unprojected} of {seed.written} seeded write(s) "
            f"came back UNPROJECTED and {seed.positions_unproven} proved no journal "
            f"position — repair with `seed.fold.plan` then `seed.fold.apply` "
            f"(the artifact carries both, with this run's tenant filled in)",
            file=sys.stderr,
        )
    if seed.record_declined:
        # ⛔ THE DECLINE IS REPORTED TOO, AND IT IS NOT THE FOLD'S (#13839 review,
        # finding 1). It used to be invisible — `applied=False, unprojected=0` is the
        # shape an UNARMED deployment has — so a seed the record refused outright
        # read as clean. There is no repair command to name: a decline means the seam
        # saw no durable journal line to take.
        print(
            f"WARNING: {seed.record_declined} of {seed.written} seeded write(s) were "
            f"DECLINED by the record seam (it answered and refused, so the row was "
            f"never attempted) — the population is in the journal and not in the "
            f"record, and no fold heals a decline; see `seed.declined` in the "
            f"artifact",
            file=sys.stderr,
        )
    written = seed.written

    # ⛔ THE PREFIX COUNT IS A READ TOO, AND AN UNREADABLE GRAPH MUST BE A NAMED
    # REFUSAL, NOT A TRACEBACK (`#13745` review round 12). This count is the
    # coverage DENOMINATOR: without it there is no gate to consult, so nothing
    # downstream can be measured — `main()` catches only `Refused`, so an
    # exception here gave the operator exit 1, a traceback and NO artifact,
    # bypassing every named refusal the file promises. Refuse by name instead.
    try:
        graph_vectors = len(
            [
                1
                for r in proj.g.query(
                    f"MATCH (n:Point) WHERE n.id STARTS WITH '{point_prefix}' "
                    "AND n.embedding IS NOT NULL RETURN n.id"
                ).result_set
            ]
        )
    except Exception as exc:
        raise Refused(
            "graph_unreadable",
            f"the graph could not be read for the coverage denominator ({exc.__class__.__name__}: "
            f"{exc}) — nothing downstream can be measured against a store that will "
            f"not answer, so this run publishes no artifact rather than a result "
            f"about the wrong store",
        ) from exc

    # ⛔ THE ID-SCOPED HALF (see the docstring): `coverage` is a LABEL-WIDE count
    # by design, so it verifies a tenant whose record holds SOME embedded points
    # while this population's own ids are in neither store. Asking only the
    # label-wide question publishes a 0.0 `record_dense` recall as a finding
    # about the leg, when it is a finding about the seed step.
    #
    # ⛔ IT GATES THE `record_dense` ARM, NOT THE RUN — and `#13734`'s review
    # round had to move it there. Since #13830 the population DOES reach the record
    # (it is written through the journaled door above), so this is no longer an
    # unsatisfiable gate: it is the post-condition of the seed, asked of the store
    # that received it rather than of the seed's own return value. It still gates
    # the ARM because a `record_dense` arm is the only thing that needs the cohort:
    # `lexical` and `graph_dense` read the graph this run folded, and a run-level
    # refusal here would throw them away while looking like a strict gate. The
    # refusal is carried BY NAME against the one arm it blocks (exit 3) rather than
    # skipped silently.
    population_ids = [row["id"] for row in population]
    # ⛔ THE GRAPH HALF IS GUARDED BY THE ID-AND-TEXT QUESTION, AND IT GATES THE
    # `graph_dense` ARM (not the run — see `require_graph_population`). The count
    # above is PREFIX-wide, so it asks *"does this graph hold any embedded
    # claim?"* — not *"did THIS run seed THIS population?"*. It passes over a
    # partly seeded population (a width mismatch, a vector the fold refuses) and
    # over a `--graph` served from a previous run.
    # `graph_population_vectors` asks the id-and-text scoped question; it is
    # computed HERE because it needs the graph, and consumed
    # INSIDE the arms loop, where only `graph_dense` reads graph vectors.
    #
    # ⛔ AND IT REPLACES THE `graph_vectors <= 0` BRANCH THAT USED TO SIT HERE.
    # That branch refused the whole run when the graph held no vector at all, so
    # a box that could still publish the lexical baseline and the record
    # comparison published NOTHING. The zero case is now just the extreme of a
    # partly seeded population: the arm is blocked by name, the other two
    # measure, and the exit says the run is incomplete.
    graph_population = graph_population_vectors(proj, population)
    coverage: dict = {}
    population_vectors: int | None = None
    if record_blocked is None:
        try:
            coverage = require_coverage(
                record_vector.coverage("point", scope, graph_vectors=graph_vectors, record_dsn=dsn),
                denominator=graph_vectors,
            )
            require_record_measured(coverage["record_vectors"])
            # ⛔ DROP THE CACHE `coverage` JUST WROTE. The cached verdict is keyed
            # by LABEL and stored stickily, and this call supplied a POPULATION
            # count as the label's denominator — so the shipped read path, which
            # asks the label-wide question, would receive this population-scoped
            # answer instead of measuring for itself.
            record_vector.reset_coverage_cache()
            # ⛔ AND THE READINESS CACHE, FOR THE SAME CLASS OF REASON (#13830).
            # `journal_ready` memoises per (tenant, journal) for 30 s, and a verdict
            # taken BEFORE the seed would be reused for the arms — a NOT-READY
            # answer from an unseeded record would keep the graph branch and turn
            # `record_dense` into the graph arm (refused as `scan`/`index`), i.e. a
            # refusal caused by this run's own earlier state. It is evicted here,
            # AFTER the seed and BEFORE the first arm.
            record_vector.reset_readiness_cache()
            population_vectors = record_population_vectors(population_ids, scope, dsn)
            coverage["population_n"] = len(population_ids)
            coverage["population_vectors"] = population_vectors
        except Refused as exc:
            record_blocked = exc
            coverage = {}
            population_vectors = None

    queries = [(row["id"], row["query"]) for row in population]
    arms: dict[str, dict] = {}
    # ⛔ ONE BLOCKED-DICT, NOT ONE `blocked` — two arms can be blocked for two
    # different reasons, and a single slot made the LAST one win: the artifact's
    # `not_measured` note, the stderr line and the comparisons all reported one
    # arm's cause as if it were the run's.
    blockages: dict[str, Refused] = {}
    if record_blocked is not None:
        blockages[ARM_RECORD_DENSE] = record_blocked
    for arm in ARMS:
        gate = blockages.get(arm)
        if gate is None:
            try:
                if arm == ARM_RECORD_DENSE:
                    require_population_in_record(population_vectors, len(population_ids))
                elif arm == ARM_GRAPH_DENSE:
                    require_graph_population(graph_population, len(population_ids))
            except Refused as exc:
                gate = exc
        if gate is not None:
            blockages[arm] = gate
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
        if any(a in blockages for a in (base, cand)):
            # A comparison against an arm that was blocked by name is
            # UNMEASURED — never an empty delta a reader could mistake for 0.0.
            blocking = next(blockages[a] for a in (base, cand) if a in blockages)
            comparisons[key] = {"label": "UNMEASURED", "blocked": blocking.code}
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
        # DROPS IT. The tenant/DSN/journal gates resolve BEFORE `_seed_population`,
        # so the most common refusal leaves nothing; the ones that need a seeded
        # graph to be answerable (coverage, the id-scoped cohort) cannot, so the
        # artifact declares the leftover rather than letting the next lane discover
        # it — `window_quality_m2` commits the same field for the same reason.
        # ⚠️ The RECORD rows are the other leftover, and they are NOT dropped by
        # anything: they are the tenant's now, which is what makes the arm
        # measurable. A measurement tenant is the right target for that reason.
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
        # ⛔ THE SEED'S OWN OUTCOME, BESIDE THE COUNTS, BECAUSE THEY ARE DIFFERENT
        # FACTS (#13830). `population.graph_population_vectors` says what the GRAPH
        # received and `coverage.population_vectors` says what the RECORD holds for
        # these ids; this block says how the write path itself answered — whether
        # each append's position was PROVEN, whether the record took the projection,
        # and (only when something was left unprojected) the exact fold command that
        # heals it. `record_unprojected > 0` beside `population_vectors == built` is
        # a coherent pair (the fold already caught up); a shortfall with 0
        # unprojected points at the fold, not at the write.
        "seed": seed.as_dict(),
        "population": {
            "built": len(population),
            "written": written,
            # ⛔ BOTH COUNTS, because they answer different questions and the
            # prefix-wide one passes over a partly seeded population. A reader
            # who sees only `graph_vectors` cannot tell a full seed from a
            # partial one; `graph_population_vectors` is the id-scoped count
            # `require_graph_population` refused on.
            "graph_vectors": graph_vectors,
            "graph_population_vectors": graph_population,
        },
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
        + [
            # ⛔ THE SENTENCE IS THE REFUSAL'S OWN. Each code can be any of a
            # dozen (no_record_dsn, no_tenant, coverage_unverified,
            # record_unseeded, graph_population_absent, …), and only some support
            # prose about a cohort. Keying on the code family wrote a false cause
            # for the rest. ONE NOTE PER BLOCKED ARM: two arms can be blocked for
            # two reasons, and the earlier single-slot version reported whichever
            # came last as if it were the run's only cause.
            f"the `{arm}` arm — BLOCKED[{exc.code}]: {exc.message}"
            for arm, exc in blockages.items()
        ],
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
    for exc in blockages.values():
        # ⛔ THE ARTIFACT IS WRITTEN FIRST, THEN THE REFUSAL IS NAMED. The arms
        # that need no blocked cohort are real measurements and are kept; the
        # exit says the run is INCOMPLETE, so no caller can mistake it for the
        # paired answer the pre-registration exists to price. One line per
        # blocked arm — a run with two different causes names both.
        print(f"REFUSED[{exc.code}]: {exc.message}", file=sys.stderr)
    # ⛔ DERIVED FROM `blockages`, WHICH IS SEEDED FROM `record_blocked` TOO — so
    # this is the aggregate of every gate, not the DSN/tenant lookalike. The
    # single `return _exit_code(blocked)` at the end stays the one exit.
    blocked = next(iter(blockages.values()), None)
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
    r.add_argument(
        "--journal",
        default="",
        help="the journal the population is APPENDED to (default: "
        "TORTOISE_RECORD_JOURNAL; one of them is required — the population enters "
        "through the journaled door, #13830)",
    )
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
    # ⛔ A NON-POSITIVE COUNT IS AN ARGUMENT ERROR, NOT A CRASH. These three are
    # argparse-`type=int`, so argparse rejects a NON-INTEGER (exit 2, usage, no
    # marker) — but it happily accepts `0` and `-1`, which reached
    # `detectable_effect_pp` and raised a bare `ValueError` out of `main()`: an
    # exit **1** with a traceback, the one code a caller cannot distinguish from
    # a crash, on a path the `--preregister` help advertises as touching no
    # store. Validated here so the exit table stays true — `4` means "the tool
    # rejected an argument", which is what this is.
    for flag, value in (
        ("--population", args.population),
        ("--points", args.points),
        ("--min-population", args.min_population),
    ):
        if value < 1:
            print(f"REFUSED: {flag} must be >= 1 (got {value})", file=sys.stderr)
            return 4
    #    ⚠️ And in the MEASUREMENT path a zero NEVER REACHED AN ARTIFACT — so
    #    the fail-open this floor closes is the CRASH, not a mislabel. Two
    #    call sites evaluate the pre-registered n unconditionally:
    #    `detectable_effect_pp(preregistered_n, …)` and
    #    `preregistration(preregistered_n)`, and both raise `ValueError: n must
    #    be positive` — BEFORE `artifact` is assembled, since `main()` wraps only
    #    `run_measurement` in `except Refused`. So the old behaviour was an
    #    uncaught traceback at exit 1, and the `population_mismatch` note was
    #    never emitted at all.
    #    The related function-level hazard is real but UNREACHABLE in a shipped
    #    artifact: with `preregistered_n = 0`, `paired` answers
    #    `detectable_pp_preregistered: None` and `gated_bar_pp` falls back to the
    #    observed bar alone, so the pre-registration would bound nothing while
    #    the note claimed both bars bind. It cannot ship, because the two calls
    #    above raise first. Recorded here as a reason to keep the floor, not as a
    #    bug that was shipping.
    if args.preregister:
        print(json.dumps(preregistration(args.population), indent=2, sort_keys=True))
        return 0
    # ⛔ THE JOURNAL DECLARATION IS THIS RUN'S, FOR THE LENGTH OF THIS RUN (#13830).
    # `run_measurement` sets it (`--journal` is enough to write, but the record arm's
    # readiness gate reads only the env name, so the two must agree); restoring the
    # caller's value here keeps a `main()` CALLER from inheriting a path for a
    # journal this process happened to seed. ⚠️ SCOPE, STATED EXACTLY (the previous
    # sentence claimed `run_measurement` callers too, which is false — the restore
    # lives HERE): a caller that invokes `run_measurement` DIRECTLY still inherits
    # both declarations, which is why a test that drives it directly owns its own
    # `monkeypatch`. The import is LOCAL so `--preregister` stays store-free.
    from tortoise import record_vector
    from tortoise.record_writer import RECORD_DSN_ENV

    saved = os.environ.get(record_vector.RECORD_JOURNAL_ENV)
    # ⛔ THE DSN DECLARATION IS RESTORED FOR THE SAME REASON, AND WITH THE SAME
    # RULE (#13839 review, finding 1): `run_measurement` publishes the run's DSN to
    # the write seam, and a caller that gets its environment back unchanged must
    # not inherit a record this process happened to measure against. A prior
    # declaration is put back VERBATIM — popped when there was none, restored when
    # there was — so a library caller's own arming survives a run.
    saved_dsn = os.environ.get(RECORD_DSN_ENV)
    try:
        return run_measurement(args)
    except Refused as exc:
        print(f"REFUSED[{exc.code}]: {exc.message}", file=sys.stderr)
        return exc.exit_code
    finally:
        if saved is None:
            os.environ.pop(record_vector.RECORD_JOURNAL_ENV, None)
        else:
            os.environ[record_vector.RECORD_JOURNAL_ENV] = saved
        if saved_dsn is None:
            os.environ.pop(RECORD_DSN_ENV, None)
        else:
            os.environ[RECORD_DSN_ENV] = saved_dsn


if __name__ == "__main__":
    raise SystemExit(main())
