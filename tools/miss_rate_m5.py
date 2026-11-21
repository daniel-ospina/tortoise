#!/usr/bin/env python
"""M5 — the miss rate per tenant at a window, and the marginal value of each MB (#14015, §9).

WHAT THIS ANSWERS, AND WHY IT IS A MEASUREMENT RATHER THAN AN ESTIMATE
----------------------------------------------------------------------
``docs/architecture/SEARCH-ARCHITECTURE.md`` §9 M5 asks for *"the miss rate per
tenant"*, and its stated purpose is *"the compute/IOPS/egress tier"*. The owner's
acceptance criterion for the window size (``#13989``, 2026-11-20, verbatim) is:

    "it's basically an amount that's good enough the system works and they don't
     have to increase what they pay all the time ... so ideally we're not nudging
     them to pay more and more and more after only a week or two."

That is a claim about a **curve**: how much miss rate a tier's window buys, and
where the buying stops. Until it is measured, "77 MB" and "615 MB" are numbers
with no answer attached to them.

⛔ **This is NOT ``STORAGE-ARCHITECTURE.md`` §15's M5.** That M5 asked *where the
vector index lives* and was answered 2026-11-07. This one asks how often a
tenant's read misses the resident window. A bare "M5" must be resolved against
the document that prints it (``SEARCH-ARCHITECTURE.md`` §9 says so itself).

WHAT A "MISS" IS, AND WHY IT IS THE COST QUANTITY AND NOT THE QUALITY ONE
------------------------------------------------------------------------
``#13103`` arms the window **with fault-in**: a demoted region is evicted and
restored on demand (measured **0.03 s** index-dropped, 1.585 s with the fulltext
index kept). So a query whose canonical node has been demoted is **not** an
error — it is a **record read**: the graph missed, and the answer comes from
Postgres. That is why the miss rate is the *cost* input (a Postgres read is
network + IOPS + egress on the record side) and it is exactly what
``STORAGE-ARCHITECTURE.md`` §17.4.3 says is *"PRICED but conditional on a miss
RATE no tenant exists to provide"*.

So there are **two** quantities and they must never be added into one number:

 ``fault_in_rate``   the share of queries whose canonical is NOT resident. **THE
                     M5 NUMBER.** With fault-in armed this is a record read.
                     It is what sizes the compute/IOPS/egress tier, and what a
                     larger window buys down.
 ``miss_resident``   the share of queries whose canonical IS resident and is
                     still not returned at depth. **A FLOOR NO WINDOW CAN BUY
                     UNDER.** M2 measured it on ``:Point`` at 28.7 % on a
                     COMPLETE graph (its ``found@deep`` ceiling 0.7133), because
                     the lexical leg ranks a document holding every query term
                     below ones holding common subsets (no coordination factor,
                     no length normalisation — §6.2). Past the point where
                     ``fault_in_rate`` falls to that floor, **a bigger window
                     buys nothing at all**, and that is the flattening point the
                     allowance needs.

THE CURVE, ITS ONE ASSUMPTION, AND HOW THAT ASSUMPTION IS BOUNDED
------------------------------------------------------------------
The window keeps `F` of the tenant's graph resident. ``#13989``'s policy is that
residency is scored by **USAGE** (what was searched/served), not by creation. So
the resident set is the highest-usage nodes, and the fault-in rate is

    fault_in_rate(F) = 1 − (the share of QUERY MASS carried by the top-F nodes)

which is the complement of that population's **Lorenz curve**. The curve is
therefore a property of **how concentrated a tenant's reads are**, and that is
the whole question: under uniform reads every extra MB buys a proportional
reduction and the curve never flattens; under concentrated reads the curve
flattens hard and a modest window covers most of the demand.

⛔ **THE ASSUMPTION, NAMED: a tenant's query mass is proportional to how often a
node's identity RECURS across that tenant's real captured work.** There is no
read ledger on this box to do better with (``§17.4.6`` forbids the tenant-wide
read ledger as a product input anyway), so recurrence is the proxy. It is a
PROXY and not an observation of reads. Three things keep it honest:

 1. **It is computed from real captures** — ``~/.tortoise/session-events/*.jsonl``,
    the same corpus M2 and ``#11901`` used — not from a synthetic distribution.
    The recurrence unit is the session RECORD for ``:Object`` (the harvest's own
    ``records`` count, one owner of that definition) and the turn for ``:Point``
    (a majority-of-terms match over the same turns the population is built from).
 2. **The mass is a RELATIVE weight within a label.** Its unit differs between
    labels and is never compared across them.
 3. **The assumption's leverage is measured, not asserted** — ``sensitivity`` in
    the artifact re-runs the curve over a Zipf family fitted around the observed
    concentration, so the flattening point is reported as a RANGE and a reader
    can see how far the answer moves if usage is flatter or steeper than
    recurrence suggests.

⚠️ **And a control that keeps the assumption from being smuggled in silently:**
every arm is also measured under **``uniform`` query weighting** (M2's sampler —
a query drawn uniformly from the population). Under that weighting
``fault_in_rate == 1 − F`` **by arithmetic**, so the curve is a straight line and
nothing ever flattens. That is not a defect of this instrument, it is what M2's
sampler could see: M2 drew its queries uniformly at random, so its finding that
no eviction order beats any other
(``found@deep`` **equals** the resident share in every ``:Object`` arm) is a
property of the SAMPLER and cannot be evidence about a usage-ordered window.
M5 measures both, so the difference between "how a window behaves when reads are
uniform" and "how it behaves when reads are concentrated" is visible instead of
assumed.

⚠️ **What M2 measured and this does not restate.** M2 measured retrieval QUALITY
at a window; M5 measures the miss rate that window produces. M2's numbers
(recall@3 88.3 % → 46.0 % at the solo target) are a different instrument's output
and are quoted here only as the ceiling input, with their own citation.

THE LABEL SET, NAMED (``#13989``: "per label, never aggregate")
---------------------------------------------------------------
 ``point``   ``:Point`` — the claim population, the one the window demotes (§8's
             byte target) and the label with a measured ceiling below 1.0.
 ``object``  ``:Object`` — the entity dictionary, the label §7's residency BOOSTER
             exists for (``#13989`` criterion 2).

⚠️ **Not measured here, and named so the gap is not read as coverage:**
``:Subject`` shares ``:Object``'s harvest class and leg and is not separately
populated (its mass distribution is the same generator); ``:Source`` and
``:Event`` are anchored/key reads rather than set-shaped searches, so a window
does not move them the same way. No aggregate across labels is published
anywhere in this artifact — that is the ``#13989`` rule.

THE MB AXIS, AND WHY IT DOES NOT DEPEND ON THE PER-NODE BYTE DISPUTE
-------------------------------------------------------------------
The shipped windows are ``product/pricing.json``'s ``max_graph_storage_mb``:
solo **77**, pro **102**, team **615** (the 30 %-of-price rule, ``§8``). A tenant
whose graph is ``S`` MB at window ``W`` is resident at ``F = min(1, W/S)`` — so

    **the coverage↔MB map depends only on ``S``, never on bytes-per-node.**

That is worth stating because ``STORAGE-ARCHITECTURE.md`` §17.4.2 records that
``cost_basis.bytes_per_node = 1,024`` **matches no measured regime** (measured:
**1,672 B per RESIDENT node**, 143 MiB ÷ 89,701; 6,003 B per capped node;
4,701 B marginal). The byte dispute moves NODE COUNTS and the corpus's own size
in MB — both reported — and it **cannot** move the curve, because ``F`` is
dimensionless. This artifact therefore quotes the curve without a byte model and
quotes a corpus size WITH one, labelled.

⚠️ ``S`` is not a single number in reality: §8's "complete graph" is an ESTIMATE
of **~150 MB**, and that estimate is a sampling reading with known omissions
(blind to the HNSW ``vectorset``, excludes per-graph overhead) that bias it LOW.
So the shipped-window table is published over a RANGE of ``S`` (75 / 150 / 300 /
600 / 1200 MB), because an allowance is exactly the question "for a tenant of
size S, what does W buy?" — and ``S`` for a real tenant is unknown to us.

INVARIANTS, ASSERTED IN `run()` — because they falsify the INSTRUMENT
--------------------------------------------------------------------
 1. THE SAMPLER, THE WINDOW AND THE LEG NAME THE SAME NODES. Every query's
    canonical id is a member of the population this instrument BUILT, and every
    id the lexical leg RETURNS is one of them too. Without it, a leg whose rows
    stop carrying ``id`` under that key (a rename, a wrapper, an adapter) reports
    every query as a miss — a 100 % ``miss_resident`` published from a BROKEN
    READ with every other check green — so a COMPLETE arm that misses every
    query is refused as well (``#14162`` F5).
 2. A query whose canonical is resident is never counted as a fault-in, and a
    demoted canonical is never RETURNED at depth (``found_deep_of_demoted == 0``)
    — the same per-demoted detector M2 uses, kept because the window requirement
    it enforces (§7: a window may not serve a node it demoted, on ANY leg) is
    unchanged by this measurement.
 3. ``miss == fault_in_rate + miss_resident`` — the decomposition ADDS UP. A
    reader who quotes one half as "the miss rate" would otherwise be quoting a
    quantity the artifact never defined.
 4. The graph holds EXACTLY the intended resident set after eviction (over- or
    under-eviction makes every rate a figure about a graph state that did not
    exist).

INVARIANT 5, ASSERTED IN THE PURE ARITHMETIC (`lorenz`) — no graph needed
------------------------------------------------------------------------
 5. ``covered_mass(mass_ordered) >= covered_mass(uniform_ordered)`` at every
    coverage — the Lorenz curve is above the identity by definition, so a
    violation means the ordering was inverted somewhere and the whole curve is
    meaningless. ``lorenz()`` compares the descending-order curve against the one
    built from an equal-mass vector of the same length, which IS the identity;
    ``tests/test_miss_rate_m5.py`` drives it BOTH ways so the check cannot become
    a tautology.

⛔ ONE THING HERE IS NOT AN INVARIANT AND MUST NOT BE RE-ADDED AS ONE.
``fault_in_rate == 1 − resident_query_share`` is ALGEBRA on this code: both
sides are counts over complementary slices of the SAME ``rows`` list, so any edit
that moves one moves the other and NO mutation can make the check fire. It was
asserted here until ``#14162`` F3, and a guard that cannot fail is dead weight
that reads as a detector. What it claimed to detect — the sampler and the
residency test disagreeing about which node a query names — is what INVARIANT 1
now cross-checks against the POPULATION instead of against itself.

An instrument that cannot fail its own arithmetic is not evidence.

USAGE
    # 1. a lane container, and export its four seams
    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"
    # 2. measure (NOT via `test_lane.py run`, which executes pytest)
    #    ⛔ `--out` points somewhere OTHER than the committed evidence record:
    #    re-running over it would overwrite the artifact you are comparing
    #    against. Reproduce into a temp path and compare the two.
    uv run python tools/miss_rate_m5.py \
        --points 900 --objects 1000 --sample 250 \
        --out /tmp/m5-miss-rate.json

Requires a live FalkorDB in ``TORTOISE_DB_URI`` and REFUSES without one rather
than falling back to the embedded engine — a figure measured on a backend the
product cannot use is not a figure.

EXIT CODES: 0 ok · 2 usage · 3 no reachable graph, or a refused run (never a
silent skip).
"""
from __future__ import annotations

import sys

# #5128 (the repo's entry-point contract): refuse a <3.12 interpreter HERE,
# before the rest of the imports, so a stale local `python3` fails LOUDLY with a
# remedy instead of with whichever 3.12-only construct happens to trip first.
# `tests/test_entry_point_python_guard.py` asserts structurally that this guard
# precedes every other import and behaviourally that it names the floor, the
# interpreter it got, and the invocation that works.
if sys.version_info < (3, 12):  # noqa: UP036 -- intentional RUNTIME guard
    print(
        f"tools/miss_rate_m5.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/miss_rate_m5.py`",
        file=sys.stderr,
    )
    raise SystemExit(70)

import argparse
import itertools
import json
import os
import random
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: The shipped windows, read from the price artifact of record. ⛔ Never a copy:
#: ``product/pricing.json`` is the owner's (``#11758``), and a window quoted from
#: a constant here would drift from the number the product actually sells.
PRICING = Path(__file__).resolve().parent.parent / "product" / "pricing.json"

#: The tiers whose window this measurement is about. ``free``/``anon`` are the
#: declared exception (hot capacity 0 — there is no window to measure), so they
#: are excluded rather than swept as a zero row.
TIERS: tuple[str, ...] = ("solo", "pro", "team")

#: The tenant-graph sizes the shipped-window table is published over, in decimal
#: MB. 150 is §8's own ESTIMATE of a complete graph (a sampling reading with
#: omissions that bias it LOW); the rest bracket a real tenant's spread. ⛔ ``S``
#: is NOT measured here and cannot be — a lane has no tenant census.
SIZES_MB: tuple[int, ...] = (75, 150, 300, 600, 1200)

#: The fine coverage grid the analytic curve is emitted at. Dense where the head
#: of the mass distribution lives (the first 20 % of nodes carry most of the
#: query mass, so that is where a curve is decided), coarse in the tail.
CURVE_COVERAGES: tuple[float, ...] = (
    0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50,
    0.60, 0.70, 0.80, 0.90, 0.95, 1.00,
)

#: The coverages the GRAPH arms are measured at — fewer than the analytic grid
#: because each arm is a fresh graph. These validate the curve; the grid above is
#: what the curve is read off.
GRAPH_COVERAGES: tuple[float, ...] = (1.0, 0.75, 0.50, 0.25, 0.10)

#: The two residency orders:
#:  ``mass``     keep the highest-usage nodes — ``#13989``'s criterion 1.
#:  ``uniform``  a seeded uniform shuffle — the control, and the shape a
#:               creation-recency window approximates when age is unrelated to use.
POLICIES: tuple[str, ...] = ("mass", "uniform")

#: The two query weightings: how a query is DRAWN from the population.
#:  ``mass``     ∝ the same recurrence weight — the model of a tenant reading what
#:               it works on. THE M5 CASE.
#:  ``uniform``  M2's sampler. Kept as the control because it is exactly the
#:               sampler whose blindness made M2's policy comparison unreadable.
WEIGHTS: tuple[str, ...] = ("mass", "uniform")

#: The depth of the single read each query makes. Same value and same reason as
#: M2: ``limit`` truncates and never reorders, so one read at this width answers
#: "is the canonical in the store at all" for every query.
DEEP_LIMIT = 50

#: Node-id prefixes: eviction and every post-eviction verification scope on these,
#: so a run cannot measure a neighbouring population on a reused graph.
ID_PREFIX = {"point": "pt-14015", "object": "obj-14015"}

#: The offset applied to the eviction stream's seed. ⛔ M2's measured defect,
#: inherited rather than rediscovered: the query sample and the ``uniform``
#: eviction order would otherwise be permutations of the SAME list drawn from the
#: SAME seed, making the retained suffix and the sampled prefix DISJOINT by
#: construction (M2's first run reported ``resident_share`` 0.667 / 0.167 / 0.000
#: — true about the graph, useless as a control).
EVICT_SEED_OFFSET = 1_000_003

#: A Point's indexed text, capped — the same cap M2 used, so the two populations
#: are the same shape of node.
POINT_TEXT_LIMIT = 1200

#: The threshold that DEFINES "the curve flattens", stated as a number because a
#: flattening point without a criterion is an opinion. A window has flattened
#: where the next 10 MB remove less than **1 percentage point** of fault-in rate,
#: i.e. below ``0.10 pp/MB``, and stay below it to full coverage.
FLAT_PP_PER_MB = 0.10

#: The absolute miss-rate bars the shipped-window table is also read against, so
#: a reader with their own definition of "good enough the system works" can find
#: the MB for it without adopting this document's threshold.
MISS_BARS: tuple[float, ...] = (0.20, 0.10, 0.05, 0.02, 0.01)

#: ⛔ THE SCALE-FREE MARGINAL, and the reason ``flattening_point``'s pp/MB form is
#: not the primary reading. ``pp/MB`` is S-DEPENDENT BY CONSTRUCTION: a bigger
#: tenant spends more MB per unit coverage, so the SAME gap is fewer pp/MB and the
#: knee appears earlier — the criterion measures the tenant, not the curve. What
#: the owner's question actually asks is *"will they have to pay more and more"*,
#: which is a DOUBLING: give twice the allowance and measure what that buys. That
#: is dimensionless, so one number describes every tenant size.
DOUBLING_GRID: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50)

#: The threshold that DEFINES "the doubling stops paying": once twice the window
#: removes less than this many percentage points of fault-in rate, the curve has
#: flattened for allowance purposes. Declared, so the answer is a reading and not
#: an opinion — and the FULL gains table is published beside it so a reader with a
#: different bar can re-read the curve without adopting this one.
FLAT_PP_PER_DOUBLING = 2.0

#: The Zipf exponents the sensitivity sweep re-runs the curve at, around a
#: concentration FITTED to the observed masses. ``observed`` is the fit; the
#: others bracket it so the flattening point is published as a range. Declared
#: here, one owner, so the record and the artifact cannot disagree.
SENSITIVITY_EXPONENTS: tuple[float, ...] = (0.6, 0.8, 1.0, 1.2)


# ── pure arithmetic (unit-tested without a graph) ────────────────────────────

def mass_order(masses: dict[str, int], policy: str, seed: int) -> list[str]:
    """Ids ordered COLDEST FIRST, under ``policy``.

    ``mass``     lowest usage first — so the retained suffix is the hottest
                 ``F`` share, which is ``#13989``'s criterion 1 expressed as an
                 eviction order. Ties break by id so the order is TOTAL (a
                 ``Counter``'s insertion order is stable within a run but is not
                 a property to depend on across runs).
    ``uniform``  a seeded shuffle — the control.

    ⛔ One ordering, one call site: retention is derived from the EVICTION order
    (``resident_ids``), never by sorting twice. Two orderings can disagree, and
    the disagreement would silently invert which half of the population a window
    keeps.
    """
    if policy == "mass":
        return sorted(masses, key=lambda i: (masses[i], i))
    if policy == "uniform":
        ids = list(masses)
        random.Random(seed + EVICT_SEED_OFFSET).shuffle(ids)
        return ids
    raise ValueError(f"unknown policy {policy!r}")


def resident_ids(ordered_ids: list[str], coverage: float) -> list[str]:
    """The ids a window of ``coverage`` keeps, from a COLDEST-FIRST order."""
    if not 0.0 <= coverage <= 1.0:
        raise ValueError(f"coverage must be in [0, 1], got {coverage!r}")
    n = len(ordered_ids)
    keep = round(n * coverage)
    # A coverage that rounds to 0 would measure "the graph is empty", which is a
    # different experiment; and ``complete`` must keep everything exactly.
    if coverage > 0.0 and keep == 0:
        keep = 1
    return ordered_ids[n - keep:]


def weighted_sample(rows: list[dict], n: int, weight_key: str | None,
                    seed: int) -> list[dict]:
    """A query set of ``n`` rows, drawn WITHOUT replacement ∝ ``weight_key``.

    ``weight_key=None`` is the uniform control (M2's sampler). Weighted draws use
    the Efraimidis–Spirakis key ``u ** (1/w)``, keeping the ``n`` largest: one
    pass, exactly ``n`` distinct rows, and deterministic under ``seed`` (which is
    what makes a re-run comparable rather than merely similar).

    ⛔ The weight is clamped to >= 1 so a node with zero observed recurrence is
    still DRAWN-able at a low rate rather than becoming impossible. A zero-mass
    node that can never be queried would make ``fault_in_rate`` a rate over a
    population narrowed by the instrument rather than by the corpus.
    """
    if n >= len(rows):
        return list(rows)
    if weight_key is None:
        out = list(rows)
        random.Random(seed).shuffle(out)
        return out[:n]
    rng = random.Random(seed)
    keyed = []
    for row in rows:
        w = max(1, int(row.get(weight_key) or 0))
        keyed.append((rng.random() ** (1.0 / w), row["id"], row))
    keyed.sort(key=lambda t: (-t[0], t[1]))
    return [row for _k, _i, row in keyed[:n]]


def covered_mass(ordered_masses: list[int], coverage: float) -> float:
    """The share of total query mass the hottest ``coverage`` of nodes carries.

    ``ordered_masses`` must be DESCENDING (hottest first). The complement of this
    number is the model's ``fault_in_rate`` — see the module docstring.
    """
    if not 0.0 <= coverage <= 1.0:
        raise ValueError(f"coverage must be in [0, 1], got {coverage!r}")
    total = sum(ordered_masses)
    if total <= 0:
        raise ValueError("covered_mass needs a positive total mass")
    if coverage >= 1.0:
        return 1.0
    n = len(ordered_masses)
    keep = round(n * coverage)
    if coverage > 0.0 and keep == 0:
        keep = 1
    return sum(ordered_masses[:keep]) / total


def lorenz(ordered_masses: list[int],
           coverages: tuple[float, ...] = CURVE_COVERAGES) -> list[dict]:
    """The curve: fault-in rate and its marginal value, at each coverage.

    ⛔ INVARIANT 5 IS ASSERTED HERE, in the pure arithmetic, so it is exercised
    without a graph: the Lorenz curve of a DESCENDING mass vector is never below
    the identity. ``covered_mass`` over an equal-mass vector of the same length
    IS the identity (each retained node carries the mean), so that is the
    comparison — and it is not the same thing as ``covered >= coverage``, because
    ``covered_mass`` rounds the retained node COUNT, so an equal-mass vector can
    sit slightly below ``f``. A violation means the mass ordering was inverted
    somewhere and every rate on this curve is meaningless.
    """
    uniform = [1] * len(ordered_masses)
    out: list[dict] = []
    for f in coverages:
        covered = covered_mass(ordered_masses, f)
        identity = covered_mass(uniform, f)
        # ⛔ INVARIANT 5 — the curve is never below the identity.
        if covered + 1e-12 < identity:
            raise RuntimeError(
                f"INVARIANT 5: covered_mass={covered} is BELOW the identity "
                f"{identity} at coverage {f} — the mass ordering is inverted, so "
                f"every rate on this curve is meaningless")
        out.append({"coverage": f,
                    "covered_mass": round(covered, 6),
                    "fault_in_rate": round(1.0 - covered, 6)})
    return out


def coverage_for(window_mb: float, size_mb: float) -> float:
    """``F = min(1, W/S)`` — the coverage a window of ``window_mb`` gives a tenant
    holding ``size_mb``. ⛔ No bytes-per-node anywhere: see the module docstring."""
    if size_mb <= 0:
        raise ValueError(f"size_mb must be positive, got {size_mb!r}")
    return min(1.0, window_mb / size_mb)


def marginal_pp_per_mb(curve: list[dict], size_mb: float) -> list[dict]:
    """pp of fault-in rate removed per MB, between consecutive curve points.

    ``W = F × S``, so the marginal is ``Δpp / (ΔF × S)``. Reported between
    ADJACENT grid points, labelled at the interval's MIDPOINT (``w_mb``) — a
    marginal has no single-endpoint reading, and quoting one at an endpoint is how
    a mid-interval slope gets mistaken for a local derivative.
    """
    out: list[dict] = []
    for prev, nxt in itertools.pairwise(curve):
        dw = (nxt["coverage"] - prev["coverage"]) * size_mb
        if dw <= 0:
            continue
        dpp = (prev["fault_in_rate"] - nxt["fault_in_rate"]) * 100.0
        out.append({"w_mb": round((prev["coverage"] + nxt["coverage"]) / 2 * size_mb, 3),
                    "from_coverage": prev["coverage"],
                    "to_coverage": nxt["coverage"],
                    "pp_removed": round(dpp, 4),
                    "pp_per_mb": round(dpp / dw, 6)})
    return out


def flattening_point(curve: list[dict], size_mb: float,
                     threshold: float = FLAT_PP_PER_MB) -> dict | None:
    """Where the curve stops paying **in MB**, at a tenant of ``size_mb``.

    Reported as the MB past which every remaining step removes less than
    ``threshold`` pp per MB, and the ``coverage`` there, so the number can be
    re-read at any ``S`` — ``W = F × S``.

    ⛔ **THIS CRITERION IS S-DEPENDENT BY CONSTRUCTION, and that is not a defect
    of the curve.** ``W = F × S``, so one unit of coverage costs ``S`` MB: a bigger
    tenant spends more MB per unit ``F``, the same pp gap is fewer pp/MB, and the
    knee therefore appears at a LOWER coverage. Read it as *"for a tenant this
    size, the window has flattened by W MB"* and never as a property of the
    distribution. ``flattening_coverage`` below is the scale-free companion, and
    it is the one to quote about the SHAPE.

    ``None`` when no interval qualifies — a real answer meaning "this curve never
    flattens under this criterion" (the uniform-query control is exactly that
    case: its marginal is constant). It is never reported as 0.
    """
    marg = marginal_pp_per_mb(curve, size_mb)
    if not marg:
        return None
    for i in range(len(marg)):
        if all(m["pp_per_mb"] < threshold for m in marg[i:]):
            row = marg[i]
            return {
                "w_mb": round(row["to_coverage"] * size_mb, 3),
                "coverage": row["to_coverage"],
                "pp_per_mb_at_interval": row["pp_per_mb"],
                "threshold_pp_per_mb": threshold,
                "criterion": (f"the first point past which every remaining step "
                              f"removes < {threshold} pp of fault-in rate per MB"),
            }
    return None


def doubling_gain(curve: list[dict],
                  grid: tuple[float, ...] = DOUBLING_GRID) -> list[dict]:
    """What TWICE the window buys, at each coverage — the scale-free marginal.

    ``F`` and ``2F`` are coverages, so the answer is dimensionless: it is the same
    number for a 150 MB tenant and a 1200 MB one, and it is what "they don't have
    to increase what they pay all the time" is actually a question about.
    """
    out: list[dict] = []
    for f in grid:
        twice = f * 2
        if twice > 1.0 + 1e-12:
            continue
        pp = (_interp(curve, f) - _interp(curve, twice)) * 100.0
        out.append({"from_coverage": f, "to_coverage": round(twice, 6),
                    "pp_removed": round(pp, 4)})
    return out


def flattening_coverage(curve: list[dict],
                        threshold: float = FLAT_PP_PER_DOUBLING,
                        grid: tuple[float, ...] = DOUBLING_GRID) -> dict | None:
    """Where the curve flattens **in coverage** — the scale-free knee.

    The first grid point past which EVERY remaining doubling removes less than
    ``threshold`` pp. ``None`` means the curve never flattens under that bar, and
    for a label whose miss decays log-linearly that is the honest answer: a
    constant pp per doubling is a curve with NO knee, and reporting a knee for it
    would invent one.
    """
    gains = doubling_gain(curve, grid)
    if not gains:
        return None
    for i, gain in enumerate(gains):
        if all(g["pp_removed"] < threshold for g in gains[i:]):
            return {
                "coverage": gain["from_coverage"],
                "pp_removed_per_doubling": gain["pp_removed"],
                "threshold_pp_per_doubling": threshold,
                "criterion": (f"the first coverage past which every further "
                              f"DOUBLING of the window removes < {threshold} pp "
                              f"of fault-in rate"),
            }
    return None


def mb_for_miss(curve: list[dict], size_mb: float, bar: float) -> float | None:
    """The smallest window (MB) at which the fault-in rate is <= ``bar``.

    ``None`` when the curve never reaches the bar — again a real answer, and the
    reason the ``:Point`` floor exists: a target below ``miss_resident`` is not
    reachable at ANY window.
    """
    for point in curve:
        if point["fault_in_rate"] <= bar + 1e-12:
            return round(point["coverage"] * size_mb, 3)
    return None


def zipf_masses(n: int, exponent: float) -> list[int]:
    """A Zipf(``exponent``) mass vector of ``n`` nodes, DESCENDING.

    The sensitivity sweep's generator. Integer masses are produced by scaling and
    rounding up to >= 1, so a tiny rank never vanishes into a zero weight (which
    ``weighted_sample`` would then clamp anyway — the two must agree).
    """
    if n < 1:
        raise ValueError("zipf_masses needs n >= 1")
    if exponent <= 0:
        raise ValueError("exponent must be > 0")
    raw = [(1.0 / (rank ** exponent)) for rank in range(1, n + 1)]
    scale = n / raw[-1] if raw[-1] > 0 else 1.0
    return [max(1, round(v * scale)) for v in raw]


def fit_exponent(masses: list[int]) -> float:
    """The Zipf exponent whose curve best matches an observed mass distribution.

    Fit on the CONCENTRATION, not on a regression of log rank on log mass: what
    the curve depends on is how much mass the head carries, so the fit minimises
    the total absolute error of ``covered_mass`` at the curve's own grid. A
    log-log least-squares fit would weight the tail (which the curve barely
    reads) and under-fit the head (which decides it).
    """
    obs = sorted(masses, reverse=True)
    grid = (0.05, 0.10, 0.25, 0.50, 0.75)
    target = [covered_mass(obs, f) for f in grid]
    best, best_err = None, None
    for i in range(41):
        exponent = 0.40 + i * 0.05  # 0.40 .. 2.40
        cand = zipf_masses(len(obs), exponent)
        err = sum(abs(covered_mass(cand, f) - t) for f, t in zip(grid, target, strict=True))
        if best_err is None or err < best_err:
            best, best_err = exponent, err
    return round(best, 2)


# ── INVARIANT 1: the sampler, the window and the leg name the SAME nodes ──────
# ⛔ These three live at module level, NOT inside `run()`, and that is a
# correctness property rather than tidiness: a guard nested in a closure can only
# be exercised against a live FalkorDB, so nothing could prove it FIRES. Each is a
# pure function over ids, so `tests/test_miss_rate_m5.py` drives it with a
# fabricated population and pins that it refuses — the shape whose absence was
# #14162 F3/F5.

def check_query_ids(kind: str, query_rows: list[dict],
                    population_ids: set[str]) -> None:
    """INVARIANT 1a — every query's canonical id is a node of the POPULATION.

    The population is what the instrument BUILT, never the arm's graph: a check
    against the arm would agree with itself (the defect INVARIANT 1 replaces).
    """
    stray = [r["id"] for r in query_rows if r["id"] not in population_ids]
    if stray:
        raise RuntimeError(
            f"{kind}: {len(stray)} of {len(query_rows)} queries name a node outside "
            f"the population (e.g. {stray[0]!r}) — the sampler and the population "
            f"disagree about which node a query names")


def check_leg_ids(kind: str, query_id: str, ids: list,
                  population_ids: set[str]) -> None:
    """INVARIANT 1b — the leg returns nodes of this population, each with an id.

    ``None`` is the case this exists for: when the leg's rows stop carrying ``id``
    under that key (a rename, a wrapper, an adapter), every query reads as a MISS
    and the artifact publishes ``miss_resident = 1.0`` from a broken read. An
    unidentifiable row is not a miss — it is a read that cannot be interpreted.
    """
    outside = [i for i in ids if i not in population_ids]
    if outside:
        raise RuntimeError(
            f"{kind}: the leg returned {len(outside)} id(s) outside the population "
            f"for query {query_id!r} (e.g. {outside[0]!r}) — a read whose rows do "
            f"not identify this population's nodes is not a measurement")


def check_arm(kind: str, n: int, miss: float, resident: set[str],
              population_ids: set[str]) -> None:
    """INVARIANT 1c — a COMPLETE arm cannot miss every query.

    "Every query missed a graph that holds every node" is a broken instrument,
    never a measurement. Aggregate form of :func:`check_leg_ids`, so it also
    catches a leg that returns a well-formed id for a node it never searched.
    """
    if population_ids <= resident and miss >= 1.0:
        raise RuntimeError(
            f"{kind}: the COMPLETE arm missed EVERY one of {n} queries on a graph "
            f"holding all {len(population_ids)} population nodes — the leg is not "
            f"reading this arm's graph, so its rates are not a measurement")


# ── the measurement ──────────────────────────────────────────────────────────

def _git_head() -> str | None:
    """The commit this artifact describes, or None (never a fabricated sha)."""
    try:
        import subprocess
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=10, check=False,
        ).stdout.strip() or None
    except Exception:
        return None


def _sha256_file(path: Path) -> str:
    """sha256 of a file's bytes — provenance that binds a figure to its producer."""
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def windows_table(curve_at_size: list[dict], sizes: tuple[int, ...],
                  windows: dict[str, int]) -> dict:
    """The shipped-window table: fault-in rate per tier, per tenant graph size.

    This is the table ``#13989`` quotes. Read a CELL, never the row's shape: a
    team tenant is fully resident at every ``S`` below 615 MB, so its miss rate is
    **0 by construction** and not a measurement of anything — the interesting
    cells are the ones where ``W < S``.
    """
    out: dict = {}
    for size in sizes:
        row: dict = {}
        for tier, window in windows.items():
            f = coverage_for(window, size)
            # The curve is a grid; interpolate linearly between the bracketing
            # points. Declared as interpolation, because a rate read off a grid
            # without saying so is a rate presented at a precision the grid does
            # not carry.
            miss = _interp(curve_at_size, f)
            row[tier] = {"window_mb": window, "coverage": round(f, 6),
                         "fault_in_rate": round(miss, 6)}
        out[str(size)] = row
    return out


def _interp(curve: list[dict], coverage: float) -> float:
    """Linear interpolation of ``fault_in_rate`` at ``coverage`` on a sorted grid."""
    if coverage >= curve[-1]["coverage"]:
        return curve[-1]["fault_in_rate"]
    if coverage <= curve[0]["coverage"]:
        return curve[0]["fault_in_rate"]
    for a, b in itertools.pairwise(curve):
        if a["coverage"] <= coverage <= b["coverage"]:
            span = b["coverage"] - a["coverage"]
            if span <= 0:
                return a["fault_in_rate"]
            t = (coverage - a["coverage"]) / span
            return a["fault_in_rate"] + t * (b["fault_in_rate"] - a["fault_in_rate"])
    return curve[-1]["fault_in_rate"]


def coverage_for_miss(curve: list[dict], bar: float) -> float | None:
    """The SMALLEST coverage at which the fault-in rate is <= ``bar``.

    Interpolated between the bracketing curve points (a rate read off a grid
    without saying so is a rate presented at a precision the grid does not carry),
    and ``None`` when the curve never reaches the bar — the honest answer for a
    target below what ANY window can deliver.
    """
    for a, b in itertools.pairwise(curve):
        if b["fault_in_rate"] <= bar + 1e-12:
            if a["fault_in_rate"] <= bar + 1e-12:
                return a["coverage"]
            span = a["fault_in_rate"] - b["fault_in_rate"]
            if span <= 0:
                return b["coverage"]
            t = (a["fault_in_rate"] - bar) / span
            return a["coverage"] + t * (b["coverage"] - a["coverage"])
    return None


def largest_tenant_mb(curve: list[dict], window_mb: float, bar: float) -> float | None:
    """The biggest tenant graph a ``window_mb`` window still serves at ``bar``.

    ``F = W/S`` inverted: ``S = W/F``. This is the number ``#13989`` needs, because
    the owner's criterion is not "what miss rate does this window give" but *"how
    big a tenant is this allowance enough for, so they are not nudged in week two"*
    — and that is a tenant SIZE, not a rate.

    ``None`` when the bar is unreachable at ANY window on this label (a target below
    the curve's own limit), never a fabricated 0 or an infinity.
    """
    f = coverage_for_miss(curve, bar)
    if f is None:
        return None
    if f <= 0.0:
        return None  # every window serves it: an unbounded answer, not a number
    return round(window_mb / f, 1)


def _label(kind: str) -> str:
    """The Cypher label for a population kind. One map, so a query cannot name a
    label the writer did not use."""
    return {"point": "Point", "object": "Object"}[kind]


#: The characters stripped from BOTH sides of the recurrence match.
#: ⛔ ONE definition, shared by the corpus units and the query span. Stripping only
#: one side left `first.` never matching `first` — and `row["query"]` is by
#: construction the RAW token run from `query_span`, so punctuated spans are the
#: normal case, not the edge (#14162 F4). The bias has a knowable direction: a
#: flatter `:Point` mass puts the curve closer to the diagonal, i.e. fewer/no
#: flattening intervals — bias toward the null "this curve never flattens", which
#: is exactly the answer #13989 would then quote.
TERM_STRIP = ".,;:!?()[]{}'\"`"

#: The floor below which a token is not a term, the same one M2's query builder
#: uses: a span reduced to noise would make every point maximally "popular".
TERM_MIN_LEN = 3


def terms_of(text: str) -> set[str]:
    """The normalised term set of ``text`` — the ONE spelling both sides use.

    Used for the corpus units AND for a query span, so a term cannot match on one
    side and miss on the other because of a trailing full stop (#14162 F4).
    """
    return {t for t in (w.strip(TERM_STRIP) for w in text.split())
            if len(t) >= TERM_MIN_LEN}


def _span_mass(span: str, units: list[set[str]], majority: bool = True) -> int:
    """How many corpus units a query span's TERMS recur in.

    ``majority`` matches a unit sharing at least half of the span's terms
    (ceil); otherwise any term. Terms shorter than ``TERM_MIN_LEN`` characters are
    dropped, the same floor M2's query builder uses — a span reduced to noise
    would make every point maximally "popular". ⛔ The span is normalised by
    ``terms_of``, the SAME function ``run()`` normalises the corpus units with, so
    the two sides of ``len(terms & unit)`` cannot be spelled differently.

    ⛔ This is the ``:Point`` recurrence proxy, and it is the SAME DEFINITION SHAPE
    as the harvest's ``records`` for ``:Object``: "in how many units of the
    corpus does this identity recur". The UNIT differs (a turn vs a session
    record) and the docstring says so; the mass is a relative weight within a
    label and is never compared across labels.
    """
    terms = terms_of(span)
    if not terms:
        return 1
    need = (len(terms) + 1) // 2 if majority else 1
    return max(1, sum(1 for unit in units if len(terms & unit) >= need))


def run(args: argparse.Namespace) -> int:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    if not uri.startswith(("docker://", "redis://")):
        print("REFUSED: no live FalkorDB in TORTOISE_DB_URI — a window figure "
              "measured on a backend the product cannot use is not a figure.\n"
              "  start one:  uri=\"$(uv run python tools/test_lane.py uri)\" && eval \"$uri\"\n"
              "  (test_lane.py `run` executes PYTEST, not this tool — see USAGE)",
              file=sys.stderr)
        return 3

    # ⛔ IMPORTED, never re-implemented: the harvest rules and the population
    # builders have ONE owner (#11901 / #12186). A second copy here could drift
    # from the one whose figures this epic already quotes.
    from tools.candidate_recall_11901 import _has_embedder, build_population, harvest
    from tools.window_quality_m2 import build_point_population, harvest_turn_texts

    # The SAME sanitiser production hands the leg, from its own home.
    from tortoise.extractor_v2 import _sanitise_query_text
    from tortoise.sdk import TortoiseSDK

    sessions = Path(os.path.expanduser(args.sessions_dir))
    artifact_stats: dict = {}

    # ── the shipped windows, read from the price artifact of record ───────────
    pricing = json.loads(PRICING.read_text())
    windows = {t: int(pricing["tiers"][t]["max_graph_storage_mb"]) for t in TIERS}
    for t, w in windows.items():
        if w <= 0:
            print(f"REFUSED: tier {t} publishes max_graph_storage_mb={w}; a "
                  f"non-positive window has no curve to measure", file=sys.stderr)
            return 3

    # ── population construction ──────────────────────────────────────────────
    texts, text_stats = harvest_turn_texts(sessions, args.points * 4)
    artifact_stats["corpus"] = text_stats
    point_funnel: dict = {}
    pops: dict[str, list[dict]] = {
        "point": build_point_population(texts, args.points,
                                        sanitise=_sanitise_query_text,
                                        stats=point_funnel),
    }
    artifact_stats["point_build"] = point_funnel

    # The turn term-sets the ``:Point`` recurrence proxy is counted over. Built
    # ONCE from the same turns the population was built from, so the mass and the
    # node cannot describe two different corpora — and with the SAME `terms_of`
    # normalisation `_span_mass` applies to the span, because two spellings of "a
    # term" is how `first.` stopped matching `first` (#14162 F4).
    turn_terms = [terms_of(t) for t in texts]

    _input_stats: dict = {}
    harvested = harvest(sessions, stats=_input_stats)
    obj_pop = build_population(harvested, args.objects)
    for i, row in enumerate(obj_pop):
        row["id"] = f"{ID_PREFIX['object']}{i:06d}"
        row["query"] = row["name"]
        # ``records`` IS the recurrence count (the number of session records the
        # name appeared in) — one owner of that definition, in the harvest.
        row["mass"] = max(1, int(row.get("records") or 0))
    pops["object"] = obj_pop
    artifact_stats["names_harvested"] = {c: len(v) for c, v in harvested.items()}

    # ⛔ The ids are RE-PREFIXED here, and this is not cosmetic. `build_point_population`
    # stamps M2's prefix (`pt-12186`), while every scoped read below — eviction,
    # the held-set verification, `ids_now` — scopes on THIS instrument's prefix.
    # Measured on the smoke run: 88 nodes landed and the verification read back
    # 0, so the run refused with "88 of 88 nodes did not land" while the graph was
    # in fact correctly populated. One prefix, assigned once, here.
    for i, row in enumerate(pops["point"]):
        row["id"] = f"{ID_PREFIX['point']}{i:06d}"
        row["mass"] = _span_mass(row["query"], turn_terms)
    for kind, pop in pops.items():
        if not pop:
            print(f"REFUSED: the {kind} population is empty", file=sys.stderr)
            return 3

    masses = {kind: {r["id"]: int(r["mass"]) for r in pop}
              for kind, pop in pops.items()}

    # ── the ANALYTIC curve, from the full harvested population ───────────────
    # ⚠️ The curve is emitted over the FULL harvest per class, not only the sampled
    # population, because the mass distribution is what the curve IS and the
    # sample is one draw from it. Both are published so a reader can see the shape
    # is not an artefact of the sample size.
    full_curves: dict = {}
    for cls, counter in harvested.items():
        ordered = sorted(counter.values(), reverse=True)
        if not ordered:
            continue
        full_curves[cls] = {
            "nodes": len(ordered),
            "total_mass": int(sum(ordered)),
            "curve": lorenz(ordered),
            "fitted_zipf_exponent": fit_exponent(ordered),
        }

    # ── ONE FRESH GRAPH PER ARM ──────────────────────────────────────────────
    graph_name = args.graph or f"m5miss_{int(time.time())}_{os.getpid()}"
    print(f"m5: graph prefix {graph_name} (point={len(pops['point'])}, "
          f"object={len(pops['object'])}; windows={windows})", file=sys.stderr)

    legs_seen: dict[str, bool] = {}
    graphs: list[str] = []
    cur: dict = {"sdk": None, "proj": None}

    def fresh(kind: str, tag: str) -> None:
        name = f"{graph_name}_{kind}_{tag.replace('.', 'x')}"
        graphs.append(name)
        sdk = TortoiseSDK(namespace=args.namespace, graph_name=name)
        cur["sdk"], cur["proj"] = sdk, sdk._get_proj()

    def invalidate_degraded_snapshot() -> None:
        """Invalidate the degraded-fallback corpus snapshot after a graph edit.

        ⛔ REQUIRED, and M2 measured why: ``fallback_snapshot`` keeps a 60 s
        in-memory copy of the corpus and serves it from the DEGRADED path. It is
        invalidated by ``SDK._mark_dirty``, which every normal write surface calls
        — and which a raw Cypher ``DELETE`` does NOT. The arms here demote by
        writing only the resident set into a FRESH graph through the projection
        writer, which bypasses the SDK hook exactly as M2's do, so an arm that
        skipped this call would read a demoted node coming back and count it as
        resident. ``_mark_dirty([])`` is the hook itself: with an empty id list it
        does the snapshot invalidation and returns.

        ⚠️ This is a WINDOW requirement, not a test detail — §7's window may not
        serve a node it has demoted, on ANY leg.
        """
        try:
            cur["sdk"]._mark_dirty([])
        except Exception as exc:
            raise RuntimeError(
                f"could not invalidate the degraded-fallback snapshot ({exc}) — "
                f"an arm measured against a stale snapshot is not a windowed graph"
            ) from exc

    def ids_now(kind: str) -> set[str]:
        return {r[0] for r in cur["proj"].g.query(
            f"MATCH (n:{_label(kind)}) WHERE n.id STARTS WITH '{ID_PREFIX[kind]}' "
            "RETURN n.id", params={}).result_set}

    def write(kind: str, rows: list[dict]) -> None:
        for row in rows:
            if kind == "point":
                cur["proj"]._upsert_point_props({"id": row["id"],
                                                 "content": row["content"]})
            else:
                cur["proj"]._upsert_object({"id": row["id"], "name": row["name"],
                                            "object_kind": "core:other"})

    def query(kind: str, text: str, k: int) -> list[str]:
        """EXACTLY the leg ``_fts_rows`` runs, at width ``k``, returning ids."""
        trace: list[dict] = []
        rows = cur["sdk"]._tortoise_fts_query(
            _sanitise_query_text(text), entity_type=kind, limit=k,
            include_terminal=kind in ("object", "subject"),
            _record_reads=False, _valid_time_rerank=False, leg_trace=trace)
        for entry in trace or []:
            if not isinstance(entry, dict):
                continue
            leg = entry.get("leg")
            if leg:
                legs_seen[leg] = bool(legs_seen.get(leg)) or bool(entry.get("ran"))
        return [r.get("id") for r in rows]

    def measure(kind: str, resident: set[str], query_rows: list[dict],
                population_ids: set[str]) -> dict:
        """One arm's rates. ⛔ Every rate here is a count over THIS loop's rows, so
        ``miss`` and its two halves cannot drift from each other.

        ``population_ids`` is the population the instrument BUILT, not the arm's
        graph — that is the point of INVARIANT 1: the arm is measured against a
        set the arm itself cannot move, so a broken read cannot make the
        cross-check agree with itself.
        """
        # ⛔ INVARIANT 1a — the SAMPLER names nodes this population holds.
        check_query_ids(kind, query_rows, population_ids)
        rows: list[dict] = []
        for row in query_rows:
            ids = query(kind, row["query"], DEEP_LIMIT)
            # ⛔ INVARIANT 1b — the LEG returns identified nodes of this population.
            check_leg_ids(kind, row["id"], ids, population_ids)
            found = row["id"] in ids[:DEEP_LIMIT]
            rows.append({"id": row["id"], "resident": row["id"] in resident,
                         "found_deep": found, "empty": not ids})
        n = len(rows)
        if n == 0:
            raise RuntimeError(f"{kind}: empty query set")
        demoted = [r for r in rows if not r["resident"]]
        resident_rows = [r for r in rows if r["resident"]]

        def rate(num: int, den: int) -> float:
            return round(num / den, 6) if den else 0.0

        fault_in_rate = rate(len(demoted), n)
        miss_resident = rate(sum(1 for r in resident_rows if not r["found_deep"]),
                             len(resident_rows))
        miss = rate(sum(1 for r in rows if not r["found_deep"]), n)
        out = {
            "queries": n,
            "resident_queries": len(resident_rows),
            "resident_query_share": rate(len(resident_rows), n),
            "fault_in_rate": fault_in_rate,
            "miss": miss,
            #: The floor. A rate over RESIDENT queries only, so it is the leg's
            #: own unreachability and not the window's.
            "miss_resident": miss_resident,
            "found_deep_of_demoted": rate(
                sum(1 for r in demoted if r["found_deep"]), len(demoted)),
            "empty_rate": rate(sum(1 for r in rows if r["empty"]), n),
        }
        # ⛔ The identity ``fault_in_rate == 1 − resident_query_share`` is NOT
        # asserted here, and must not be re-added: both sides are counts over
        # complementary slices of this same ``rows`` list, so NO mutation can make
        # the check fire (#14162 F3). What it claimed to detect is INVARIANT 1's
        # job, and INVARIANT 1 does it against the POPULATION rather than against
        # the identity's own other half.
        # ⛔ INVARIANT 1c — a COMPLETE arm (a graph holding the WHOLE population)
        # cannot miss every query (#14162 F5).
        check_arm(kind, n, out["miss"], resident, population_ids)
        # ⛔ INVARIANT 3 — the decomposition ADDS UP.
        # The band is 2e-6, not 1e-6, because every operand is already a `round(…, 6)`
        # result and one of them is a PRODUCT of two of them, so the summed rounding
        # error can exceed a single operand's half-ulp (#14162 review F5). Measured
        # worst case over legal states: 4.96e-7 at n=250 (the committed `--sample`),
        # but 1.41e-6 at n=711 and 1.30e-6 at n=251 — so a 1e-6 band would refuse a
        # CORRECT run at any `--sample >= 251` with a message that reads like a real
        # decomposition failure. 2e-6 is twice the operand count's rounding budget and
        # still catches the ~1e-2 drift a genuinely broken decomposition produces.
        if abs(miss - (fault_in_rate + miss_resident * out["resident_query_share"])) > 2e-6:
            raise RuntimeError(
                f"{kind}: miss {miss} != fault_in + miss_resident*resident_share "
                f"({fault_in_rate} + {miss_resident}*{out['resident_query_share']})")
        return out

    def reset(kind: str) -> None:
        cur["proj"].g.query(f"MATCH (n:{_label(kind)}) WHERE n.id STARTS WITH "
                            f"'{ID_PREFIX[kind]}' DETACH DELETE n", params={})
        invalidate_degraded_snapshot()

    def run_population(kind: str) -> dict:
        pop = pops[kind]
        mass = masses[kind]
        fresh(kind, "complete")
        write(kind, pop)
        invalidate_degraded_snapshot()
        landed = ids_now(kind)
        if landed != {row["id"] for row in pop}:
            raise RuntimeError(
                f"{kind}: {len(pop) - len(landed)} of {len(pop)} nodes did not land")

        # The two FIXED query sets — one per weighting, each sampled ONCE and
        # reused by every arm that reads it. §9 says "on a fixed query set", and
        # an arm that re-sampled would compare two populations rather than two
        # graph states.
        query_sets = {
            "mass": weighted_sample(pop, min(args.sample, len(pop)), "mass", args.seed),
            "uniform": weighted_sample(pop, min(args.sample, len(pop)), None, args.seed),
        }
        for rows in query_sets.values():
            if rows:
                query(kind, rows[0]["query"], 1)  # warm: constant across every arm

        all_ids = {row["id"] for row in pop}
        arms: dict = {}
        # The COMPLETE baseline, per query weighting. Its ``miss`` IS
        # ``miss_resident`` (nothing is demoted), and it is the floor this label
        # cannot go below at ANY window — the number that makes a flattening point
        # different from a zero.
        for weight, rows in query_sets.items():
            arm = measure(kind, all_ids, rows, all_ids)
            arms[f"complete|{weight}"] = arm
            print(f"  {kind:6s} complete|{weight:7s} miss {arm['miss']:6.1%} "
                  f"(floor; no demotion)", file=sys.stderr)
        if ids_now(kind) != all_ids:
            raise RuntimeError(f"{kind}: the complete arm lost nodes")

        for policy in POLICIES:
            order = mass_order(mass, policy, args.seed)
            for coverage in GRAPH_COVERAGES:
                if coverage >= 1.0:
                    continue  # the shared baseline above
                keep = set(resident_ids(order, coverage))
                # A FRESH graph written in POPULATION order (not eviction order),
                # so the write sequence is identical in every arm and cannot itself
                # move a tie in the lexical leg.
                fresh(kind, f"{policy}_{coverage:g}")
                write(kind, [r for r in pop if r["id"] in keep])
                invalidate_degraded_snapshot()
                held = ids_now(kind)
                # ⛔ INVARIANT 4 — the arm's graph holds EXACTLY the set it names.
                if held != keep:
                    raise RuntimeError(
                        f"{kind}/{policy}@{coverage:g}: intended {len(keep)} "
                        f"resident ids, graph holds {len(held)} "
                        f"({len(keep - held)} missing, {len(held - keep)} unexpected)")
                for weight, rows in query_sets.items():
                    arm = measure(kind, held, rows, all_ids)
                    arm["coverage_requested"] = coverage
                    arm["coverage_delivered"] = round(len(held) / len(pop), 6)
                    # ⛔ INVARIANT 2 — a demoted node is never returned at any
                    # rank of the depth-50 read. The strong form, and the one that
                    # does not hide under a ceiling below 1.0.
                    if arm["found_deep_of_demoted"] != 0.0:
                        raise RuntimeError(
                            f"{kind}/{policy}@{coverage:g}|{weight}: "
                            f"found_deep_of_demoted={arm['found_deep_of_demoted']} "
                            f"— a demoted node was returned at depth")
                    arms[f"{policy}@{coverage:g}|{weight}"] = arm
                    print(f"  {kind:6s} {policy}@{coverage:<5g}|{weight:7s} "
                          f"faultin {arm['fault_in_rate']:6.1%} "
                          f"miss {arm['miss']:6.1%} "
                          f"miss_res {arm['miss_resident']:6.1%}",
                          file=sys.stderr)
        reset(kind)

        # ── the curve, and what it says ──────────────────────────────────────
        ordered = sorted(mass.values(), reverse=True)
        curve = lorenz(ordered)
        floor = arms["complete|mass"]["miss_resident"]
        # ⛔ The curve's own ceiling is the label's floor: past the point where
        # ``fault_in_rate`` falls to ``miss_resident``, another MB buys nothing a
        # window can buy. Published as its own row so it cannot be mistaken for a
        # threshold anyone chose.
        flat = {str(s): flattening_point(curve, s) for s in SIZES_MB}

        # ── the value of the POLICY, at each coverage ─────────────────────────
        # The same mass-weighted query set read against a mass-ordered window and
        # against a random one. This is not the value of a MEGABYTE; it is the
        # value of ``#13989``'s criterion 1 itself, and it is the larger number.
        policy_delta = {}
        for coverage in GRAPH_COVERAGES:
            if coverage >= 1.0:
                continue
            as_mass = arms.get(f"mass@{coverage:g}|mass")
            as_uniform = arms.get(f"uniform@{coverage:g}|mass")
            if not as_mass or not as_uniform:
                continue
            policy_delta[f"{coverage:g}"] = {
                "fault_in_mass_ordered": as_mass["fault_in_rate"],
                "fault_in_random_ordered": as_uniform["fault_in_rate"],
                "pp_removed_by_usage_ordering": round(
                    (as_uniform["fault_in_rate"] - as_mass["fault_in_rate"]) * 100, 4),
            }
        return {
            "nodes": len(pop),
            "query_sets": {w: len(rows) for w, rows in query_sets.items()},
            "mass": {
                "total": int(sum(ordered)),
                "max": int(max(ordered)),
                "min": int(min(ordered)),
                "top_1pct_share": round(covered_mass(ordered, 0.01), 6),
                "top_10pct_share": round(covered_mass(ordered, 0.10), 6),
                "fitted_zipf_exponent": fit_exponent(ordered),
            },
            "curve": curve,
            "floor": {
                "miss_resident": floor,
                "source": "the complete-graph arm's miss (nothing is demoted, so "
                          "it is the leg's own unreachability)",
                "note": "no window can buy a miss rate below this on this label, "
                        "AT THIS POPULATION SIZE",
                # ⛔ THE SCALE CAVEAT, and it is load-bearing. `found@deep` is a
                # rate at a fixed DEEP_LIMIT, so it depends on how many documents
                # compete for those slots: the floor measured on this population
                # is NOT the production floor, and it is WORSE at production scale
                # (more documents competing for the same 50 ranks). The
                # fault_in_rate — the M5 number — is a RESIDENCY question and is
                # not affected this way, which is why the curve is the deliverable
                # and the floor is a side finding.
                "population_scale_caveat": (
                    f"measured on {len(pop)} nodes at depth {DEEP_LIMIT}. At "
                    f"production scale (tens of thousands of nodes) MORE documents "
                    f"compete for the same {DEEP_LIMIT} ranks, so this is a LOWER "
                    f"bound on the production ceiling, not an estimate of it. Read "
                    f"the DIRECTION and the ORDER of magnitude, never the decimal."),
                "depth_limit": DEEP_LIMIT,
            },
            "arms": arms,
            "policy_delta": policy_delta,
            "windows": windows_table(curve, SIZES_MB, windows),
            "flattening": {
                "scale_free": {
                    "why": "a DOUBLING of the window is dimensionless, so this is "
                           "the knee in the SHAPE of the curve, independent of "
                           "tenant size — quote this one about the shape",
                    "point": flattening_coverage(curve),
                    "threshold_pp_per_doubling": FLAT_PP_PER_DOUBLING,
                    "gains": doubling_gain(curve),
                },
                "per_tenant_size": {
                    "why": "pp/MB is S-dependent by construction (W = F x S), so "
                           "this answers 'for a tenant this size, by which MB has "
                           "the window flattened' — never a property of the curve",
                    "rows": flat,
                    "threshold_pp_per_mb": FLAT_PP_PER_MB,
                },
            },
            "mb_for_miss": {
                str(s): {f"{bar:.2f}": mb_for_miss(curve, s, bar) for bar in MISS_BARS}
                for s in SIZES_MB
            },
            "largest_tenant_mb": {
                "why": "inverts mb_for_miss: the LARGEST tenant graph a tier's "
                       "window still keeps at or below a miss bar. This is the "
                       "'#13989 not nudged in week two' number — a tenant SIZE, "
                       "not a rate. None = the bar is unreachable at any window.",
                "per_window": {
                    f"{bar:.2f}": {tier: largest_tenant_mb(curve, w, bar)
                                   for tier, w in windows.items()}
                    for bar in MISS_BARS
                },
            },
            "marginal": {str(s): marginal_pp_per_mb(curve, s) for s in SIZES_MB},
            "sensitivity": {
                "why": "the mass is a RECURRENCE proxy, not an observed read ledger; "
                       "this re-runs the curve over a Zipf family bracketing the "
                       "fitted concentration so the flattening point is a RANGE",
                "fitted_exponent": fit_exponent(ordered),
                "rows": {
                    str(exp): {
                        "fitted_curve_covered_mass": {
                            str(f): round(covered_mass(zipf_masses(len(ordered), exp), f), 6)
                            for f in (0.10, 0.25, 0.50)
                        },
                        "flattening_at_150mb": flattening_point(
                            lorenz(zipf_masses(len(ordered), exp)), 150),
                    }
                    for exp in SENSITIVITY_EXPONENTS
                },
            },
        }

    result: dict = {}
    for kind in ("point", "object"):
        result[kind] = run_population(kind)

    # ── the control that keeps the assumption honest ─────────────────────────
    # Under uniform query weighting ``fault_in_rate == 1 − F`` BY ARITHMETIC, so
    # no curve flattens. Computed here rather than measured because it is an
    # identity, and published because it is exactly what M2's uniform sampler
    # could see — the reading M2's "no eviction order is a lever" rests on.
    uniform_control = {
        "why": "uniform query weighting makes fault_in_rate == 1 - F exactly; the "
               "curve is a straight line and nothing flattens. M2 drew its queries "
               "uniformly at random, so this — not the window — is what its "
               "policy comparison measured.",
        "curve": [{"coverage": f, "fault_in_rate": round(1.0 - f, 6)}
                  for f in CURVE_COVERAGES],
        "flattening_coverage": flattening_coverage(
            [{"coverage": f, "fault_in_rate": round(1.0 - f, 6)}
             for f in CURVE_COVERAGES]),
        "flattening_point_at_150mb": flattening_point(
            [{"coverage": f, "fault_in_rate": round(1.0 - f, 6)}
             for f in CURVE_COVERAGES], 150),
        "doubling_gain": doubling_gain(
            [{"coverage": f, "fault_in_rate": round(1.0 - f, 6)}
             for f in CURVE_COVERAGES]),
    }

    artifact = {
        "measurement": "M5",
        "issue": 14015,
        "document": "docs/architecture/SEARCH-ARCHITECTURE.md §9",
        "consumed_by": "#13989 (the working-set allowance)",
        "⛔not_this": "STORAGE-ARCHITECTURE.md §15's M5 (where the vector index "
                      "lives, answered 2026-11-07) — a different question",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": _git_head(),
        "windows": {
            "source": "product/pricing.json tiers.<t>.max_graph_storage_mb",
            "mb": windows,
            "derivation": "the 30 %-of-price rule, SEARCH-ARCHITECTURE.md §8",
            "note": "solo 77 / pro 102 / team 615 are the SHIPPED values; this "
                    "artifact measures what they buy and never changes them",
        },
        "coverage_map": {
            "formula": "F = min(1, W/S)",
            "why_no_bytes_per_node": "F is dimensionless, so the coverage<->MB map "
                                     "is independent of the per-node byte figure "
                                     "(§17.4.2: the declared 1,024 B matches no "
                                     "measured regime; measured 1,672 B/resident "
                                     "node). The byte dispute moves NODE COUNTS, "
                                     "not this curve.",
        },
        "params": {
            "points": args.points, "objects": args.objects, "sample": args.sample,
            "deep_limit": DEEP_LIMIT, "graph_coverages": list(GRAPH_COVERAGES),
            "curve_coverages": list(CURVE_COVERAGES), "policies": list(POLICIES),
            "query_weights": list(WEIGHTS), "sizes_mb": list(SIZES_MB),
            "seed": args.seed, "evict_seed_offset": EVICT_SEED_OFFSET,
            "graph_prefix": graph_name, "point_text_limit": POINT_TEXT_LIMIT,
            "flat_pp_per_mb": FLAT_PP_PER_MB,
            "flat_pp_per_doubling": FLAT_PP_PER_DOUBLING,
            "doubling_grid": list(DOUBLING_GRID),
            "miss_bars": list(MISS_BARS),
            "sensitivity_exponents": list(SENSITIVITY_EXPONENTS),
        },
        "labels_measured": {
            "point": ":Point — the claim population; the label the window demotes",
            "object": ":Object — the entity dictionary; the label the residency "
                      "booster exists for",
            "⛔not_measured": ":Subject shares :Object's harvest class and leg and is "
                             "not separately populated; :Source and :Event are "
                             "anchored/key reads and a window does not move them "
                             "the same way. No aggregate across labels is published.",
        },
        "corpus": artifact_stats,
        "retrieval": {
            "has_embedder": _has_embedder(),
            "legs_seen": dict(sorted(legs_seen.items())),
            #: ⛔ A `keyword_only` run is a figure about the LEXICAL leg, never
            #: about the fused read.
            "mode": "hybrid" if _has_embedder() else "keyword_only",
        },
        "full_corpus_curves": full_curves,
        "labels": result,
        "uniform_query_control": uniform_control,
        "provenance": {
            #: ⛔ A DRIFT CHECK, NOT PRODUCTION EVIDENCE. sha256 of this
            #: instrument's bytes AS COMMITTED, and it is pinned by
            #: `tests/test_miss_rate_m5.py::test_the_artifacts_tool_sha_matches_the_committed_instrument`
            #: — which recomputes this digest against the committed
            #: `tools/miss_rate_m5.py` and reds when an edit to `tools/` leaves the
            #: artifact pointing at bytes that no longer exist. ⛔ That pin was
            #: DECLARED ABSENT while the bundle was not yet committed (a control
            #: that cannot run, `#14162` F2's class); the bundle IS committed now,
            #: the declared-absence guard is GONE, and
            #: `test_the_committed_bundle_makes_the_artifact_backed_pins_run` holds
            #: the removal so a withdrawn bundle reds instead of the pins quietly
            #: sleeping. It cannot attest that a run produced these numbers — only
            #: a re-run can — and the record says so in those words.
            "tool_sha256": _sha256_file(Path(__file__).resolve()),
        },
        "cleanup": {
            "graphs_created": len(graphs),
            "graphs_dropped": 0,
            "note": "each arm is measured on its own graph and NO graph is dropped. "
                    "`reset()` runs once per population and acts on the graph the "
                    "last arm left current, so one graph per population is emptied "
                    "and the rest are left POPULATED. On a lane container "
                    "`test_lane.py down` disposes of them, which is the intended "
                    "path; a shared server accumulates one full set per run.",
        },
    }

    out = Path(os.path.expanduser(args.out))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"m5: wrote {out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="M5 — the miss rate per tenant at a window, and the marginal "
                    "value of each MB (#14015 §9). Needs a live FalkorDB in "
                    "TORTOISE_DB_URI.")
    p.add_argument("--points", type=int, default=900,
                   help=":Point nodes built from real captured turns (default 900)")
    p.add_argument("--objects", type=int, default=1000,
                   help=":Object nodes from the harvest (default 1000)")
    p.add_argument("--sample", type=int, default=250,
                   help="queries in each FIXED query set, per population (default 250)")
    p.add_argument("--sessions-dir", default="~/.tortoise/session-events")
    p.add_argument("--namespace", default="default")
    p.add_argument("--graph", default=None,
                   help="graph name; default is run-scoped. A REUSED graph must be "
                        "empty — `name`/`content` are ON CREATE writes (#8919)")
    p.add_argument("--seed", type=int, default=14015,
                   help="seed for the query samples AND (offset) the `uniform` order")
    p.add_argument("--out", default="m5-miss-rate.json")
    args = p.parse_args(argv)
    if args.points < 1 or args.objects < 1 or args.sample < 1:
        print("REFUSED: --points, --objects and --sample must be >= 1", file=sys.stderr)
        return 2
    try:
        return run(args)
    except RuntimeError as exc:
        # Every invariant failure lands here: the run is REFUSED, never published
        # with a warning, because a rate from a graph state that did not exist is
        # worse than no rate at all.
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
