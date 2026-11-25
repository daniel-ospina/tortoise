#!/usr/bin/env python3
"""#13906 — does the record's dense leg RETURN what the graph's dense leg returns?

THE GAP THIS FILLS, stated as the three instruments that do NOT answer it:

* ``tools/entity_vector_parity_census.py`` (#13629) answers *"does the record
  HOLD a vector for every graph node that has one"* — a COPY census, no query;
* ``tests/test_4997_vector_index_parity.py`` compares index-vs-scan **inside**
  the graph — never the record against the graph;
* ``tests/test_11642_vector_move_live.py`` executes the record's SQL and its
  shortfall assertion — it never compares its answer to the leg it replaced.

So the one question the flip (#11642 / #13610) has never had a gate for is the
one the decision's own §6.1 makes load-bearing: **for the same tenant data and
the same query, do the two legs agree on ids, on order, and on recall@k?**

## The four readers, and why ground truth is a THIRD computation

``truth`` is computed in this process, in numpy, over the corpus THIS HARNESS
WROTE. Neither store's index is involved, so no store can bias the oracle
(the research rung is explicit that ground truth must not be regenerated from
the destination index — see ``docs/research/2026-11-20-13906-vector-parity/``).

| reader | how it answers |
|---|---|
| ``truth_cosine`` | numpy exact scan, cosine distance, ``(distance ASC, key)`` |
| ``truth_euclidean`` | numpy exact scan, euclidean, same ordering rule |
| ``record`` | ``record_vector.vector_search`` → pgvector HNSW, **cosine** |
| ``graph_scan`` | ``search_engine.run_vector_query`` → the graph's brute-force fallback, **euclidean** |
| ``graph_index`` | the same call with an HNSW index created first, **cosine** |

## ⛔ THE METRIC IS NOT THE SAME ON THE TWO GRAPH BRANCHES, AND THAT IS A FINDING

The graph's INDEX branch scores cosine (`similarityFunction: 'cosine'`, and it
converts ``1 - d``); its brute-force SCAN branch scores **euclidean**
(``vec.euclideanDistance``, converted ``1/(1+d)``). Our embeddings are
un-normalised model output (``embeddings.compute_embeddings``: *"the same
un-normalised model output"*), so cosine and euclidean orderings are **not**
guaranteed to agree. This harness measures both rather than assuming either:

* ``record`` and ``graph_index`` are both compared to ``truth_cosine`` — the
  apples-to-apples parity test for the migration (the index the record replaces
  was COSINE);
* ``graph_scan`` is compared to ``truth_euclidean`` — *what production does
  today* on a store with no vector index (measured: the live graph has none);
* ``graph_scan`` vs ``truth_cosine`` is reported as ``metric_divergence``,
  INFORMATIONAL — the number of ids the current production path would rank
  differently from the metric the decision names canonical.

## Usage

    uv run python tools/test_lane.py uri                # a FalkorDB for this lane
    uv run python tools/vector_parity_13906.py --record-dsn "$TEST_RECORD_DB_URI"

    # prove the harness can FAIL (this is a gate requirement, not a demo)
    uv run python tools/vector_parity_13906.py ... --poison id
    uv run python tools/vector_parity_13906.py ... --poison order
    uv run python tools/vector_parity_13906.py ... --poison recall
    uv run python tools/vector_parity_13906.py ... --poison vector

Exit codes: ``0`` parity within policy · ``1`` a required comparison diverged ·
``2`` cannot run (no store, no schema, no driver) — never reported as parity ·
``3`` a poisoned run measured everything and the poison moved NOTHING (HARNESS
BLIND). ⛔ 3 rather than 1 on purpose: §6's repro recipe is one `echo exit=$?`
per poison, and a blind harness that shares `1` with "diverged" reads as
"poison confirmed" — the one bit the falsifier's proof rests on ·
``4`` cannot run here: the #5128 interpreter guard refused (Python < 3.12),
with the message on stderr — see the guard below `__future__`. A separate code
from ``2`` on purpose: 2 is a state the tool REPORTS after it loaded, and
0/1/2/3 are each a reading §6's one-bit recipe already consumes, so 4 is the
only non-answer left. This table and §6 of the artifact README are the same
contract and are pinned EQUAL by
`test_every_exit_code_the_tool_can_take_is_documented_in_both_tables`.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12-only import would fail first, and the operator would get an unattributed
# traceback instead of this sentence (the `tools/` entry-point contract;
# `tests/test_entry_point_python_guard.py` pins it for the corpus). This guard is
# PROTECTIVE, not documentary: nothing below it may use 3.12-only SYNTAX, or the file
# would die in the tokenizer and never reach this statement.
#
# ⛔ EXIT **4**, NOT the historical `raise SystemExit(<message>)` — that exits 1,
# and this module's exit table (see `CannotRun` above) spends 1 on "a required
# comparison diverged". The falsifier's repro loop is one `echo exit=$?` per
# poison (§6), so a guard wearing 1 would make `--poison id` read **"poison
# confirmed"** on an interpreter that measured nothing — the same false PASS
# `CannotRun` was introduced to close, one indirection out. 0/1/2/3 are all
# spent here (PARITY / DIVERGED / CANNOT-RUN / BLIND), so 4 is the code this
# tool can spare; `SystemExit(<int>)` carries no message, hence the explicit
# `print` to stderr. Registered in `tests/test_entry_point_python_guard.py`'s
# `REFUSAL_CODES`/`REFUSAL_WHY` so the decision survives the next rebase.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/vector_parity_13906.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/vector_parity_13906.py`",
        file=sys.stderr,
    )
    raise SystemExit(4)

import argparse
import contextlib
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

#: Imported, not restated: the one vocabulary the read surfaces use. The
#: `Row.eligible` predicate below is the CALLERS' rule, so it reads the rule from
#: its owner (`live._terminal_excluded` / `search_engine._status_vocab_for`)
#: rather than growing a second copy that could drift from it.
from tortoise.live import TERMINAL_EXCLUDED_STATUSES as TERMINAL_STATUSES

DIM = 384

#: Which REQUIRED arm each poison can actually move. `id|order|recall` rewrite the
#: RECORD candidate list (`rec_pairs = poison(…, rec_ids, …)`); `vector` perturbs
#: the rows the GRAPH is seeded with, so it reaches the graph "scan" arm and never
#: the record one. ⛔ A poison is "detected" only when the arm IT reaches measured
#: a divergence: a divergence on some OTHER arm is real, but it certifies nothing
#: about whether this instrument can see the corruption the run applied — the same
#: self-certification the round-7 fix closed one door over (see `_verdict`).
POISON_REACH: dict[str, tuple[str, ...]] = {
    "id": ("record",),
    "order": ("record",),
    "recall": ("record",),
    "vector": ("graph_scan",),
}


class CannotRun(RuntimeError):
    """The run could not measure — exit 2, which is NOT "a comparison diverged".

    ⛔ `raise SystemExit("cannot run: …")` exits **1**, and this module's own exit
    table defines 1 as *"a required comparison diverged"*. The falsifier's repro
    loop (`--poison id; echo exit=$?`) would then read a missing pgvector
    extension, a refused non-local DSN or a never-ready index as **"falsifier
    confirmed"** when nothing was measured at all. Measured in review.
    """

#: The record's application role — `NOBYPASSRLS`, the role `scope_connection`
#: will accept (`tortoise/record_serve.py` names it as the runbook's role).
#: A connect-time `options=-c role=` is libpq's spelling of `SET ROLE`; the
#: query string is escaped with `quote`, never `urlencode`'s default `+`, because
#: libpq does not decode `+` as a space (measured, `#9600` cycle 7).
APP_ROLE = "tortoise_record_app"

#: Point is the label this harness seeds. ⛔ NOT an arbitrary choice: `:Point`
#: holds the pre-existing corpus this migration is about (16,693 of the ~35,312
#: measured nodes in #13906) and it is the label the armed cutover owns. The
#: other four served labels are structurally identical on the record side and
#: pinned by their own live tests (`test_11642_vector_move_live.py`,
#: `test_13422_object_subject_dense_leg.py`); adding their seeding here would be
#: four more INSERT shapes for no new question.
LABEL = "point"
LABEL_CYPHER = "Point"

#: Default tie tolerance for a *reported distance*. Cosine on unit-basis
#: synthetic rows is exact; real vectors carry float noise, so a tie test must
#: be a tolerance, not equality.
#: ⛔ THIS NUMBER IS A GATE, not a housekeeping detail: `compare`'s `order`
#: verdict excuses a permutation inside one bucket of this width, so widening it
#: turns a genuine cross-distance re-rank into a `tie` — i.e. into a PASS. It
#: left 30/30 green when widened to 1e-3 (measured in review), because nothing
#: read it. Pinned by
#: `test_the_tie_tolerance_is_tight_enough_to_see_a_re_rank`.
TIE_TOL = 1e-6

#: The REQUIRED `graph_scan_vs_euclidean` arm's distance tolerance — looser than
#: the other arms' 1e-4, and that is arithmetic rather than indulgence: the scan
#: reports `1/(1+d)` and this harness inverts it, so a relative float32 error
#: `eps` in the score becomes `(1+d)·eps/s ≈ (1+d)²·eps` in the distance. At
#: d≈100 that is ~1e-3, i.e. the record arm's 1e-4 tolerance would report a FALSE
#: RED on a corpus whose rows are far apart. The record and index arms carry a
#: distance directly (`<=>`, `1-d`) and keep 1e-4.
#: ⛔ ALSO A GATE: widening this literal makes the required arm's value check
#: inert while every falsifier stays green (measured at 10.0 in review), so the
#: call site reads this constant — never a literal — and
#: `test_the_scan_arm_reads_its_tolerance_from_the_one_constant` pins both.
SCAN_DISTANCE_TOL = 1e-3


# ── corpus ──────────────────────────────────────────────────────────────────

@dataclass
class Row:
    key: str
    embedding: list[float] | None
    status: str | None = None
    outdated: bool | None = None
    updated_at: str = "2026-01-01"
    tenant: str = "ours"

    def eligible(self, dim: int) -> bool:
        """The population BOTH legs must serve, restated from the callers' rules.

        Three rules, each one owned elsewhere and imported-by-reference rather
        than re-decided here:

        * a width other than the store's is not ANN material — record side
          `public.record_embedding_vector` returns NULL, graph side the shared
          read seam drops the row (`stored_embedding_vector_expr`);
        * a TERMINAL status is excluded by `live.TERMINAL_EXCLUDED_STATUSES`
          (`_status_vocab_for` is the one dispatcher);
        * the legacy `outdated=true` flag is excluded for Points.
        """
        if self.embedding is None or len(self.embedding) != dim:
            return False
        if self.status in TERMINAL_STATUSES:
            return False
        return not self.outdated


def _unit(hot: int, dim: int = DIM) -> list[float]:
    values = [0.0] * dim
    values[hot] = 1.0
    return values


def build_corpus(seed: int = 13906, n_random: int = 48) -> list[Row]:
    """A corpus that exercises the edges this migration can fail on.

    ⛔ Un-normalised on purpose. `embeddings.compute_embeddings` stores the
    model's raw output, so a corpus of unit vectors would hide the
    cosine-vs-euclidean divergence this harness exists to measure — and hide it
    in the direction that reads as "parity".
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    rows: list[Row] = []

    for i in range(n_random):
        vec = rng.normal(size=DIM).astype(float)
        # Deliberately NOT unit length, and NOT the same length as its peers:
        # a norm that varies is what makes cosine and euclidean disagree.
        vec *= float(rng.uniform(0.35, 2.5))
        rows.append(Row(key=f"r{i:03d}", embedding=vec.tolist()))

    # A TIE GROUP of three byte-identical vectors. Any comparison that ignores
    # ties will call a legitimate permutation a divergence — and a comparison
    # that ignores ties in the OTHER direction would call a real re-order a pass.
    tie = rows[0].embedding
    for j in range(3):
        rows.append(Row(key=f"tie{j}", embedding=list(tie)))

    # The query anchor: an exact match the top-1 assertion can name.
    rows.append(Row(key="anchor", embedding=_unit(0)))

    # Edge: a caller-owned 768-wide vector (PR #3018 stores these VERBATIM).
    # It must be stored on BOTH sides and ANN-invisible on BOTH sides.
    rows.append(Row(key="wide768", embedding=_unit(1, dim=768)))

    # Edge: no embedding at all.
    rows.append(Row(key="no_embedding", embedding=None))

    # Edge: terminal rows whose vector IS the query — if the #689 cage fails on
    # either leg, these rank FIRST and the diff is loud rather than subtle.
    rows.append(Row(key="retracted", embedding=_unit(0), status="retracted"))
    rows.append(Row(key="outdated", embedding=_unit(0), outdated=True))

    # Edge: another tenant, same vectors as the query. A tenancy leak on the
    # record leg ranks FOREIGN first.
    #
    # ⛔ RECORD-ONLY, and the comment used to claim otherwise ("the graph side gets
    # its own graph"): `seed_foreign_tenant()` exists only on `RecordStore`, and
    # `GraphStore.seed` SKIPS every `tenant != "ours"` row — no second cypher graph
    # is ever created, so a tenancy leak on the graph leg is UNTESTED. Stated here
    # rather than silently implied by the corpus: the edge is a property of the
    # record leg only, which is the leg the migration moves TO.
    for j in range(3):
        rows.append(Row(key=f"other{j}", embedding=_unit(0), tenant="other"))

    return rows


# ── ground truth ────────────────────────────────────────────────────────────

def _cosine_distance(a, b) -> float:
    import numpy as np

    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        # A zero vector has no direction: cosine is undefined. The stores both
        # treat it as maximal distance rather than crashing, so truth does too.
        return 1.0
    return 1.0 - float(np.dot(a, b) / denom)


def _euclidean_distance(a, b) -> float:
    import numpy as np

    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


def truth(rows: list[Row], query: list[float], k: int, *, metric: str,
          tenant: str = "ours",
          dim: int | None = None) -> list[tuple[str, float]]:
    """The oracle: an exact scan over the corpus this harness wrote.

    ``dim`` is the store's own width for this read, and it is taken from the
    QUERY vector rather than a literal — ``run_vector_query`` does exactly that
    (``len(query_vec)``), and #4280 is the reason it may not hardcode 384: an
    index-less store reads back whatever width its own encoder produced. Passing
    ``None`` (the default) applies that same rule here, so the oracle and the
    leg can never disagree about which rows are ANN material.
    """
    dim = len(query) if dim is None else dim
    fn = _cosine_distance if metric == "cosine" else _euclidean_distance
    scored = [
        (row.key, fn(row.embedding, query))
        for row in rows
        if row.tenant == tenant and row.eligible(dim)
    ]
    # `(distance ASC, key ASC)` — a STABLE, unique tiebreak so the oracle is
    # reproducible. The stores' own tie rule is recency-then-id, which is why
    # `compare` treats a within-tie permutation as acceptable rather than red.
    scored.sort(key=lambda item: (item[1], item[0]))
    return scored[:k]


# ── comparison ──────────────────────────────────────────────────────────────

def compare(truth_rows: list[tuple[str, float]], candidate_ids: list[str],
            *, candidate_distances: list[float] | None = None,
            k: int | None = None, distance_tol: float = 1e-4) -> dict:
    """Diff one candidate list against the oracle.

    Three questions, kept separate because they fail differently:

    * ``ids`` — set agreement, i.e. recall@k. A miss here is a row the leg
      cannot see; no ordering policy can excuse it.
    * ``order`` — positional agreement. A permutation is a divergence ONLY if
      it moves ids across DIFFERENT distances: a swap inside a tie group is the
      research rung's "tie-equivalence" case and is reported as ``tie``.
    * ``recall@k`` — ``|truth ∩ candidate| / |truth|``.

    ``candidate_distances`` adds a fourth: the VALUE, not just the order. It is
    not redundant — measured on this harness, a `--poison vector` that negated
    one stored vector moved its distance from 0.0 to 2.0 while the corpus'
    other rows were far enough away that the returned IDS AND ORDER did not
    change, so an id-only comparison called a genuinely different store
    "parity". A distance check is what makes the harness able to fail there.
    """
    k = k if k is not None else len(truth_rows)
    truth_ids = [key for key, _ in truth_rows]
    # ⛔ NOT truncated to `k`. A leg that returns MORE than it was asked for is a
    # defect, and truncating the candidate list here is exactly how a comparator
    # hides it (pinned by `test_an_extra_id_is_reported_even_when_recall_is_full`).
    cand = list(candidate_ids)
    cand_set, truth_set = set(cand), set(truth_ids)
    overlap = cand_set & truth_set
    # ⛔ MULTIPLICITY IS PART OF THE ANSWER. `set(cand) == set(truth)` is TRUE for
    # `["a","b","c","a"]` against `["a","b","c"]`, and the positional loop is
    # bounded by the shorter list — so a leg that returns `k+1` rows with one key
    # repeated (a remainder appended to a primary answer, a fold join without
    # DISTINCT) used to print PARITY with recall 1.0. Measured in review; the
    # docstring above promised the opposite and the `extra id` pin could not see
    # it because a REPEATED key is neither missing nor novel.
    # `Counter`, not `cand.count(key)` in a comprehension: `cand` is whatever the
    # leg returned and is deliberately NOT truncated, so a leg that ignored
    # `LIMIT` and returned the production corpus (~35,312 rows) would make the
    # quadratic form ~1.2e9 comparisons per arm per query — the loud red becomes
    # a hang, in exactly the case the comparison exists to report.
    from collections import Counter

    duplicates = sorted(key for key, n in Counter(cand).items() if n > 1)
    count_equal = len(cand) == len(truth_ids)

    distance_of = {key: dist for key, dist in truth_rows}
    # ⛔ BUCKETING, AND ITS ERROR IS DELIBERATELY FAIL-CLOSED. The bucket key is
    # STRICTER — not coarser — than "within `TIE_TOL` of each other": a bucket
    # spans exactly `TIE_TOL`, so bucket equality is a STRICT SUBSET of the
    # tolerance relation and excuses FEWER pairs, never more. Two distances
    # 2e-10 apart that straddle a bucket boundary (4.999e-7 and 5.001e-7 round to
    # 0 and 1) land in different buckets, so a swap between them reports
    # `diverged` — measured on this harness, and NOT a rare corner: a same-bucket
    # span is at most `TIE_TOL`, while about half of the in-tolerance pairs
    # straddle a boundary (measured 99,771 of 200,000 samples).
    #
    # The error is one-directional, and THAT is the point: a bucket is exactly
    # `TIE_TOL` wide, so anything bucketed TOGETHER is inside the tolerance, which
    # means this rounding can produce a false RED but never a false `tie`.
    #
    # ⛔ AND THE REPAIR THAT WOULD BREAK IT IS **WIDENING**, not "comparing
    # directly". Replacing the buckets with `abs(da - db) <= TIE_TOL` only ever
    # ADDS `tie`s for pairs already inside the tolerance, so it cannot excuse a
    # re-rank wider than `TIE_TOL` (measured: it leaves every genuine
    # cross-distance pin green). The fail-open risk is multiplying this bucket's
    # width — `round(dist / (2 * TIE_TOL))` excuses a re-rank between distances one
    # whole `TIE_TOL` apart as a `tie`, i.e. as a PASS. Pinned from both sides by
    # `test_a_straddling_pair_fails_CLOSED_never_as_a_tie`.
    tie_groups: dict[float, set[str]] = {}
    for key, dist in truth_rows:
        bucket = round(dist / TIE_TOL) if TIE_TOL else 0.0
        tie_groups.setdefault(bucket, set()).add(key)

    order = "exact"
    mismatches: list[dict] = []
    for pos in range(min(len(truth_ids), len(cand))):
        if truth_ids[pos] == cand[pos]:
            continue
        a, b = truth_ids[pos], cand[pos]
        # A permutation inside ONE distance bucket is a legitimate tie.
        same_bucket = any(a in g and b in g for g in tie_groups.values())
        if a in overlap and b in overlap and same_bucket:
            order = "tie" if order == "exact" else order
            continue
        order = "diverged"
        mismatches.append({"position": pos, "truth": a, "candidate": b,
                           "truth_distance": distance_of.get(a),
                           "candidate_distance": distance_of.get(b)})
        if len(mismatches) >= 5:  # bounded: a report, not a transcript
            break

    distance = {"ok": None, "max_delta": None, "worst": None, "unmeasured": 0}
    if candidate_distances is not None and len(candidate_distances) != len(cand):
        # ⛔ A SHORT DISTANCE LIST IS A DEFECT, NOT A PASS. `zip` stops at the
        # shorter side, so a length mismatch used to leave `ok: True` having
        # compared only its first element (measured in review).
        distance = {"ok": False, "max_delta": None, "worst": None,
                    "reason": f"{len(candidate_distances)} distances for "
                              f"{len(cand)} candidate ids"}
    elif candidate_distances is not None and not cand:
        # ⛔ ZERO ROWS COMPARED IS NOT A PASSED VALUE CHECK. `zip([], [])` runs
        # zero times, so `worst_delta` stayed `0.0` and an EMPTY candidate list
        # published `distance.ok: True, max_delta: 0.0` — which is how the record
        # leg's own failure path (`run()` answers a raise with `rec_answers[qid]
        # = []`) produced a green distance check, and how `_verdict` then read
        # that arm as MEASURED. Measured in review: the reachable state for a
        # raised record leg was `ids_equal False, recall 0.0, distance.ok True`.
        # `ok` stays `None` (UNMEASURED) so any consumer that requires a positive
        # measurement fails on it.
        distance = {"ok": None, "max_delta": None, "worst": None,
                    "unmeasured": 0,
                    "reason": "no candidate row was compared, so there is no "
                              "distance to verify"}
    elif candidate_distances is not None:
        worst, worst_delta = None, 0.0
        # `strict=True` because the branch above already returned when the two
        # lengths disagree — so it is free here, and it doubles as a second guard
        # rather than a second spelling of the same truncation.
        for key, dist in zip(cand, candidate_distances, strict=True):
            if key not in distance_of or dist is None or not math.isfinite(float(dist)):
                continue
            delta = abs(float(dist) - float(distance_of[key]))
            if delta > worst_delta:
                worst, worst_delta = key, delta
        # ⛔ A `None` OR NON-FINITE DISTANCE IS NOT A VERIFIED ONE. The loop above
        # `continue`s on both, so an all-None list left `ok: True, max_delta: 0.0`
        # — the SAME fail-open class the short-list branch one line up refuses.
        #
        # ⛔ AND THE REACHABLE ONE IS NaN, which is why the test is `isfinite` and
        # not `is None`: pgvector answers **NaN**, not NULL, for a zero-norm vector
        # (measured on this box: `SELECT '[0,0,0]'::vector <=> '[1,0,0]'::vector`
        # → `NaN`). A NaN delta fails `delta > worst_delta` silently, so an
        # all-NaN arm published `ok: True, max_delta: 0.0` and `main()` printed
        # PARITY — a required arm turned green by the one input it cannot score.
        #
        # The `None` branch is DEFENSIVE, and an earlier draft of this comment
        # said it was reachable through the record leg: it is not.
        # `record_vector.vector_search` builds
        # `WHERE t.embedding_vec IS NOT NULL` (`tortoise/record_vector.py:2813`),
        # so `(embedding_vec <=> %s)` cannot be NULL for a served row. It is kept
        # because "no distance" from ANY leg must never read as a verified one,
        # and because a future `WHERE` is not this comparator's to assume.
        def _unmeasurable(d) -> bool:
            return d is None or not math.isfinite(float(d))

        unmeasured = sum(1 for _k, d in zip(cand, candidate_distances, strict=True)
                         if _unmeasurable(d) or _k not in distance_of)
        distance = {"ok": worst_delta <= distance_tol and unmeasured == 0,
                    "max_delta": worst_delta, "worst": worst,
                    "unmeasured": unmeasured, "tolerance": distance_tol}

    return {
        "distance": distance,
        "count_equal": count_equal,
        "duplicates": duplicates,
        "ids_equal": cand_set == truth_set and count_equal and not duplicates,
        "order": order,
        # ⛔ `1.0` for an empty truth set was an empty measurement reported as a
        # perfect one — `--k 0` made every comparison pass and the tool print
        # PARITY having measured nothing. `main` refuses `k < 1` now, and an
        # empty oracle is reported as `None` (UNMEASURED) so the verdict fails.
        "recall": (len(overlap) / len(truth_set)) if truth_set else None,
        "missing": sorted(truth_set - cand_set)[:10],
        "extra": sorted(cand_set - truth_set)[:10],
        "order_mismatches": mismatches,
        "candidate_ids": cand,
        "truth_ids": truth_ids,
    }


def _scan_arm(t_euc: list[tuple[str, float]], scan_ids: list[str],
              scan_dists: list[float], *, k: int) -> dict:
    """The REQUIRED `graph_scan_vs_euclidean` arm, at ITS tolerance.

    ⛔ A NAMED SEAM, not a tidiness pass. The verdict for this arm rests on
    ``SCAN_DISTANCE_TOL``, and while that number was a literal inside `run()` no
    DB-free test could reach it: widening it 1e-3 → 10.0 left all 30 pins green
    (measured in review), i.e. the required arm's value check could be made inert
    with every falsifier asleep. ``compare`` already REPORTs the tolerance it
    was called with, so this seam makes the number the arm actually used
    assertable without a store.
    """
    return compare(t_euc, scan_ids, k=k, distance_tol=SCAN_DISTANCE_TOL,
                   candidate_distances=scan_dists)


def poison(pairs: list[tuple[str, float | None]],
           truth_rows: list[tuple[str, float]], kind: str
           ) -> list[tuple[str, float | None]]:
    """Deliberately corrupt a candidate list so the harness must report a diff.

    ⛔ THIS IS THE FALSIFIER, and it is why the harness is allowed to say "pass".
    A comparison that has never returned ``False`` is not evidence about either
    store; it is evidence about the comparison's own blind spots.

    ⛔ IT OPERATES ON `(id, distance)` PAIRS, and that is a correctness fix, not a
    tidiness one. The earlier ids-only version permuted the ids while the
    distances stayed in their original positions, so the comparator's `zip`
    paired id *i* with distance *j* and reported a **0.8967** `distance_delta`
    that no store ever produced (`poison-order.json`, `q_anchor`) — an artifact
    of misalignment presented as the falsifier's evidence. A re-order now moves
    the row, distance included, exactly as a store returning the same rows in a
    different order would.
    """
    out = [(key, dist) for key, dist in pairs]
    if kind == "id":
        # Replace the best hit with a key that is NOT a hit, keeping ITS distance.
        if out:
            out[0] = ("r999_absent_from_truth", out[0][1])
    elif kind == "order":
        # Swap two rows that do NOT share a distance BUCKET, not merely two rows
        # with different keys: on an answer that is one tie group, a same-bucket
        # swap is classified `tie` by the comparator, i.e. the falsifier is
        # MASKED by the very policy it is testing. Measured on the live run:
        # `poison-order.json` reports `order="tie"` for `q_tie` and `q01`.
        buckets = {key: round(dist / TIE_TOL) for key, dist in truth_rows}
        i = next(
            (n for n in range(1, len(out))
             if out[n][0] != out[0][0]
             and buckets.get(out[n][0]) != buckets.get(out[0][0])), None)
        if i is not None:
            out[0], out[i] = out[i], out[0]
    elif kind == "recall":
        # Return fewer rows than the oracle does.
        out = out[:-1] if out else out
    else:
        raise ValueError(f"unknown poison {kind!r}")
    return out


def perturb_for_vector_poison(rows: list[Row], victim: str = "anchor") -> list[Row]:
    """The graph-side store divergence for `--poison vector`: seed DIFFERENT data.

    ⛔ NOT A NEGATION. `-unit` sits 2.0 from the query while this corpus'
    unrelated rows are up to ~48 away, so a negated anchor stays in the top-k and
    an id-only comparison sees nothing — measured, and it is why the first
    `--poison vector` reported POISON NOT DETECTED. `-100` on the first axis is
    beyond every corpus row, so the victim leaves the window under BOTH metrics.

    It lives here, as a pure function, so the DB-free suite can pin the one
    poison whose mechanism is not in `poison()`.
    """
    out: list[Row] = []
    for r in rows:
        if r.key == victim and r.embedding:
            embedding = [-100.0 if i == 0 else 0.0 for i in range(len(r.embedding))]
        else:
            embedding = r.embedding
        out.append(Row(key=r.key, embedding=embedding, status=r.status,
                       outdated=r.outdated, updated_at=r.updated_at,
                       tenant=r.tenant))
    return out


# ── stores ──────────────────────────────────────────────────────────────────

#: Loopback by construction, plus `host.docker.internal`: that name resolves to
#: the HOST machine, which is where the lane's own database listens when this
#: harness runs INSIDE a container — local to the run, not adjacent to it. It is
#: not a loopback ADDRESS, so it is named here rather than inferred.
LOOPBACK = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}


def _assert_loopback(host: str, what: str, allow_remote: bool) -> None:
    """Refuse to SEED a non-loopback store without an explicit opt-in.

    ⛔ This harness WRITES: it creates an organization, a graph row, node
    identities and `point` rows, then deletes them. Handed a production DSN by
    accident, the writes land in the record — so the default is that both stores
    are local, the refusal names which one is not, and `--allow-remote-writes` is
    the deliberate act. A guard that only warned would be a guard that gets
    scrolled past.
    """
    if allow_remote or host in LOOPBACK:
        return
    raise CannotRun(
        f"refusing to seed a NON-LOCAL {what} ({host}): this harness writes "
        f"(org, graph, node_identity, point rows) and deleting them afterwards "
        f"does not make the write safe if the run is interrupted. Pass "
        f"--allow-remote-writes if you really mean it.")


RECORD_DSN_NO_HOST = "<host not resolvable — cannot PROVE it is local>"
#: Two parsers, two answers: Python ends the URL at `#`, libpq has no fragment
#: concept and keeps reading, so `…/db#?hostaddr=<remote>` is query-less to
#: `urlsplit` and a real `hostaddr=` to libpq. Refused rather than guessed.
RECORD_DSN_AMBIGUOUS = (
    "<a `#` before the query string — Python and libpq disagree about where "
    "the host is, so it cannot be PROVEN local>")

#: A DSN naming a libpq SERVICE delegates the dialled host to `pg_service.conf`,
#: which this harness cannot read before connecting — so it is as unknowable as an
#: absent host and is refused the same way.
RECORD_DSN_SERVICE = ("<service — the dialled host is whatever pg_service.conf "
                      "says, so it cannot be PROVEN local>")


def _is_uri_dsn(dsn: str) -> bool:
    """Does libpq read this as a URI DSN? — by PREFIX, never by containment.

    ⛔ `"://" in dsn` ANSWERS A DIFFERENT QUESTION. libpq's own dispatch
    (`connectOptions1`) tests whether the string STARTS with `postgresql://` or
    `postgres://`; anything else is a keyword/conninfo string. So a conninfo whose
    ANY VALUE happens to contain `://` —
    `host=db.prod.example.com password=vault://localhost/secret` — was parsed here
    as a URI, the "authority" was read out of the password, and the REAL host was
    never examined: the guard passed a DSN that dials and then seeds/deletes in a
    remote record (measured with `PQconninfoParse` + `PQhostaddr`).
    """
    return dsn.lstrip().startswith(("postgresql://", "postgres://"))


def _split_host_list(value: str) -> list[str]:
    """libpq accepts a COMMA-SEPARATED host list in one `host=`/`hostaddr=` value.

    ``host=127.0.0.1,db.prod.example.com`` is a failover list, so the remote entry
    is a store this run may reach — every member is kept, none is dropped.
    """
    out: list[str] = []
    for part in value.split(","):
        host = part.strip()
        if host.startswith("[") and host.endswith("]"):  # libpq's `host=[::1]`
            host = host[1:-1]
        if host:
            out.append(host)
    return out


def _conninfo_pairs(dsn: str) -> list[tuple[str | None, str | None]]:
    """(keyword, value) pairs of a conninfo, parsed by LIBPQ's grammar.

    ⛔ ONE WALKER, TWO CALLERS, because a regex cannot express this grammar and a
    split between the two interpretations is how a fail-open hides. libpq allows
    whitespace on BOTH sides of `=`, single-quoted values with backslash escapes,
    and `\\<space>` inside an unquoted value — and it SKIPS the whitespace after
    `=`, so `dbname= host=127.0.0.1` is ONE pair whose value is the whole string.
    A flat `(host|hostaddr|service)` regex with `\\s*=\\s*` inventing a `host=` there
    certified a store libpq dialled elsewhere: with `PGHOST=203.0.113.9` set,
    `dbname= host=127.0.0.1` is `dbname='host=127.0.0.1'` to libpq (NO host), so
    the environment supplies the remote host — while the regex both reported a
    loopback host AND suppressed the environment fallback (the "key present" set),
    and the guard PASSED. Measured in review; 1,111 of 49,171 conninfo × env pairs.

    ``(None, value)`` marks a bare token (no `=`), which libpq rejects and the
    rebuild re-emits verbatim.

    ⛔ A BACKSLASH WITH NOTHING LEFT TO ESCAPE IS **DROPPED**, because that is what
    libpq does with it: `PQconninfoParse('host=127.0.0.1 dbname=x' + chr(92))`
    answers `dbname='x'` (measured on libpq.so.5). Keeping it — the pre-round-22
    behaviour — made this scanner certify a DIFFERENT database than libpq reads,
    and the rebuild then emitted the value `x` + a backslash (parseable, and a
    database the caller never named).
    """
    pairs: list[tuple[str | None, str | None]] = []
    i, n = 0, len(dsn)
    while i < n:
        if dsn[i].isspace():
            i += 1
            continue
        word_start = i
        while i < n and not dsn[i].isspace() and dsn[i] != "=":
            i += 1
        word = dsn[word_start:i]
        j = i
        while j < n and dsn[j].isspace():
            j += 1
        if not word:
            # ⛔ THE SCAN STOPPED ON `=` ITSELF, AND THIS BRANCH MUST ADVANCE.
            # `word` is empty here (`=x host=127.0.0.1`, a leading `=`, a bare `=`
            # between keywords) — libpq parses those far enough to report `host`,
            # so the locality guard CERTIFIES them and they reach `app_role_dsn`.
            # A branch that re-emitted `dsn[word_start:i]` (the empty string) and
            # `continue`d without moving `i` left `while i < n` in an UNBOUNDED
            # SPIN: no refusal, no traceback, no exit, and a lane watchdog kills
            # it with no diagnosis (measured at `49d056291d`:
            # `app_role_dsn("host=127.0.0.1 dbname=x = ")` never returned; killed
            # by `timeout 5`, exit 124 — the defect `f18cc736bc` fixed in the
            # token loop this walker replaced, so it must be fixed here too).
            # The whole `=value` run becomes ONE bare token, which is what libpq
            # reports (an empty keyword) and what the rebuild re-emits verbatim.
            start = i
            i += 1
            while i < n and (dsn[i] == "\\" or not dsn[i].isspace()):
                i += 2 if dsn[i] == "\\" else 1
            pairs.append((None, dsn[start:i]))
            continue
        if j >= n or dsn[j] != "=":
            pairs.append((None, word))
            continue
        j += 1                                    # consume `=`
        while j < n and dsn[j].isspace():         # libpq skips these
            j += 1
        value_chars: list[str] = []
        if j < n and dsn[j] == "'":
            j += 1
            while j < n:
                if dsn[j] == "\\":
                    if j + 1 < n:
                        value_chars.append(dsn[j + 1])
                    j += 2
                    continue
                if dsn[j] == "'":
                    j += 1
                    break
                value_chars.append(dsn[j])
                j += 1
        else:
            while j < n and (dsn[j] == "\\" or not dsn[j].isspace()):
                if dsn[j] == "\\":
                    if j + 1 < n:
                        value_chars.append(dsn[j + 1])
                        j += 2
                        continue
                    # ⛔ A DANGLING BACKSLASH ESCAPES NOTHING, SO LIBPQ DROPS IT
                    # (`dbname=x\` -> `dbname='x'`, measured with
                    # `PQconninfoParse`). Keeping it certified a value libpq never
                    # reads: the rebuild then closed it as `dbname='x\\'`, i.e. a
                    # DSN naming database `x\` where the caller named `x`.
                    j += 1
                    continue
                value_chars.append(dsn[j])
                j += 1
        pairs.append((word.lower(), "".join(value_chars)))
        i = j
    return pairs


def _conninfo_hosts(dsn: str) -> tuple[list[str], set[str]]:
    """(hosts, keys) named by libpq's whitespace conninfo form.

    ⛔ THE GRAMMAR IS LIBPQ'S, NOT A REGEX'S — see `_conninfo_pairs`, which is the
    one parser both this and the rebuild use.
    """
    hosts: list[str] = []
    keys: set[str] = set()
    for key, value in _conninfo_pairs(dsn):
        if key is None:
            continue
        keys.add(key)
        if key == "service" and value:
            # The host comes from pg_service.conf, which this harness cannot read
            # before connecting — unknowable, so never loopback.
            hosts.append(RECORD_DSN_SERVICE)
        elif key in ("host", "hostaddr"):
            hosts.extend(_split_host_list(value or ""))
    return hosts, keys


def _authority_hosts(netloc: str) -> list[str]:
    """Hosts in a URI authority — including libpq's comma-separated FAILOVER LIST.

    ⛔ ``urlsplit(...).hostname`` answers HALF the question. For
    ``postgresql://127.0.0.1:1,203.0.113.9/db`` libpq's ``host`` is
    ``'127.0.0.1,203.0.113.9'`` — a failover list whose SECOND entry it dials when
    the first refuses (measured: ``PQhostaddr`` → ``203.0.113.9``) — while
    ``urlsplit().hostname`` returns only ``'127.0.0.1'`` and ``.port`` even RAISES
    (``Port could not be cast to integer value as '1,203.0.113.9'``). Trusting the
    half-answer passed a DSN that reached a remote record.
    """
    hosts: list[str] = []
    for entry in netloc.split(","):
        entry = entry.strip()
        if "@" in entry:                       # userinfo is not a host
            # ⛔ FIRST `@`, matching `_split_uri` and libpq: for
            # `postgresql://a@b@127.0.0.1/db` libpq's host is `b@127.0.0.1`
            # (measured), so a last-`@` split would disagree with its own guard.
            entry = entry.split("@", 1)[1]
        if entry.startswith("["):              # [::1]:5432
            end = entry.find("]")
            hosts.append(entry[1:end] if end != -1 else entry[1:])
        elif ":" in entry:
            hosts.append(entry.rsplit(":", 1)[0])
        elif entry:
            hosts.append(entry)
    return [unquote(host) for host in hosts if host]


def _split_uri(dsn: str) -> tuple[str, str]:
    """(text-before-the-query, query) split by LIBPQ's rule, not Python's.

    ⛔ TWO BOUNDARIES, IN THIS ORDER — measured with `PQconninfoParse` (and
    `psql` dials what it reports), never read off a grammar:

    1. the USERINFO ends at the FIRST `@` in the region before the first `/`;
    2. the AUTHORITY ends at the first `/` AFTER that userinfo, **or** at the
       first `?` after it when no `/` intervenes — that `?` starts the query.

    So a `?` INSIDE the userinfo/password is NOT a separator, while a pathless
    authority is STILL followed by a query:

        postgresql://127.0.0.1:p?ss@203.0.113.9:1/db
            -> user='127.0.0.1', password='p?ss', host='203.0.113.9'  (query: none)
        postgresql://127.0.0.1:5432?host=db.prod.example.com
            -> host='db.prod.example.com'                            (a query!)
        postgresql://u@localhost?host=203.0.113.9&password=p@localhost
            -> user='u', password='p@localhost', host='203.0.113.9'

    ⛔ THE `@` THAT ENDS USERINFO IS THE **FIRST** ONE, not the last: an `@` in a
    password (`&password=p@localhost`) or in a host (`postgresql://a@b@h/db` ->
    host `b@h`) is part of what FOLLOWS. Taking the LAST `@` moves the boundary
    into the query, the query then looks like userinfo, and the remote `?host=`
    becomes invisible — the same bypass one `@` further along.

    ⛔ EACH HALF-FIX IS A BUG. Splitting at the first `?` anywhere truncates the
    authority inside a password, so the guard certifies a REMOTE store as local.
    Treating the authority as "to the first `/` or the end" makes a PATHLESS
    URI's `?host=` / `?hostaddr=` / `?service=` invisible, which does the same
    thing one spelling over. Both directions are pinned.
    """
    scheme_end = dsn.find("://")
    rest = dsn[scheme_end + 3:] if scheme_end != -1 else dsn
    offset = scheme_end + 3 if scheme_end != -1 else 0
    slash = rest.find("/")
    pre_slash = rest if slash == -1 else rest[:slash]
    at = pre_slash.find("@")
    after_userinfo = 0 if at == -1 else at + 1
    query_at = rest.find("?", after_userinfo)
    if query_at == -1:
        return dsn, ""
    return dsn[: offset + query_at], rest[query_at + 1:]


def _uri_hosts(dsn: str) -> tuple[list[str], set[str]]:
    """(hosts, keys) named by libpq's URI form.

    ⛔ `urlsplit` IS THE WRONG PARSER FOR A libpq URI, twice over:

    * it treats `#…` as a FRAGMENT, while libpq has no fragment concept — and
      libpq resolves the AUTHORITY (to the first `/`) BEFORE it looks for the
      `?` separator, so a `?` inside userinfo is not a separator at all. So
      `postgresql://127.0.0.1/db#?hostaddr=203.0.113.9` has an EMPTY query to
      `urlsplit` and a LIVE `hostaddr` to libpq — measured: `psql` dials
      203.0.113.9 while this function's caller returned only the authority.

    ⛔ THE ANSWER IS TO SPLIT THE QUERY THE WAY LIBPQ DOES (`_split_uri`: authority
    to the first `/`, then the first `?` after it) rather than to refuse every `#`,
    which would also over-block
    a legal DSN whose userinfo carries one. That split is what makes the
    fragment-hidden query visible; the pinned mutant is reverting it to
    `urlsplit(dsn).query`, which sees an empty query and passes the DSN.
    """
    hosts: list[str] = []
    keys: set[str] = set()
    base, query_string = _split_uri(dsn)
    if query_string:
        # TWO PARSERS, TWO ANSWERS: Python ends the URL at `#`, libpq has no
        # fragment concept. A `#` BEFORE the query means they disagree about
        # where the authority ends, so the shape is refused, not resolved.
        if "#" in base:
            return [RECORD_DSN_AMBIGUOUS], set()
        # ⛔ NOT `parse_qs`: it DECODES `+` AS A SPACE, which libpq does not.
        # `?host=127.0.0.1+` is host `127.0.0.1+` to libpq (measured: PQhostaddr
        # gives 127.0.0.1) and `127.0.0.1` to `parse_qs` — a different dialled
        # name, and a DNS search domain can make them different hosts. Split on
        # `&` and percent-decode only, exactly the decoding libpq does.
        query: dict[str, list[str]] = {}
        for pair in query_string.split("&"):
            if not pair:
                continue
            name, _, raw = pair.partition("=")
            query.setdefault(unquote(name), []).append(unquote(raw))
        for key in ("hostaddr", "host", "service"):
            for value in query.get(key, []):
                keys.add(key)
                if key == "service" and value:
                    hosts.append(RECORD_DSN_SERVICE)
                else:
                    hosts.extend(_split_host_list(value))
        # libpq REJECTS an uppercase query key (`invalid URI query parameter`), so
        # no dial happens — but the DSN is still not one to reason about: treat it
        # as specifying the key so an environment value cannot slip in behind it.
        for key in query:
            if key.lower() in ("host", "hostaddr", "service"):
                keys.add(key.lower())
    # ⛔ NOT `authority.hostname`: the authority is a comma-separated FAILOVER LIST
    # to libpq, and `.hostname` returns only its first member.
    authority_hosts = _authority_hosts(base.split("//", 1)[1].split("/", 1)[0]
                                       if "//" in base else "")
    if authority_hosts:
        hosts.extend(authority_hosts)
        keys.add("host")
    return hosts, keys


def _record_dsn_hosts(dsn: str) -> list[str]:
    """Every host libpq would DIAL from this DSN — and from the PG* environment.

    ⛔ THE QUESTION IS NOT "WHAT DOES THIS STRING SAY", it is "what would
    `psycopg2.connect(dsn)` dial" — and THREE rounds of this guard have each
    closed one indirection of that gap, every one of them measured with libpq
    itself rather than read out of the docs:

    * a `host=`/`hostaddr=` in the URI QUERY overrides the authority
      (`postgresql://127.0.0.1/db?host=db.prod.example.com`);
    * `service=` / `PGSERVICE` hand the dial to `pg_service.conf`, whose
      `hostaddr` this harness cannot read;
    * the conninfo grammar allows SPACES around `=`, so `hostaddr = 203.0.113.9`
      is a keyword that a one-token `key=value` parser cannot see;
    * a `#` before the `?` is a FRAGMENT to `urlsplit` and NOTHING to libpq, so
      the query it hides is live;
    * and libpq fills every option the conninfo leaves UNSET from `PGHOSTADDR` /
      `PGHOST` / `PGSERVICE` — so a plain loopback DSN is remote under
      `PGHOSTADDR=203.0.113.9`.

    Every host this returns must be loopback. `[]` means the DSN (and the
    environment) name no host at all — the caller refuses that
    (`RECORD_DSN_NO_HOST`); it does not treat it as local. Two shapes are refused
    the same way rather than resolved (this sentence previously carried two
    contradictory clauses after a hand merge, which is exactly the kind of record
    that invites the next lane to "tidy" the refusal away):

    * a `#` BEFORE the query — `RECORD_DSN_AMBIGUOUS`, because Python and libpq
      disagree about where the authority ends;
    * a conninfo the keyword scanner cannot resolve — `RECORD_DSN_NO_HOST`.

    A `#` AFTER the `?` is NOT special to libpq: the query is read from the first
    `?` to the end of the string, so a parameter that a `urlsplit`-based reader
    would hide behind a fragment is still seen here.
    """
    try:
        if _is_uri_dsn(dsn):
            hosts, keys = _uri_hosts(dsn)
        else:
            hosts, keys = _conninfo_hosts(dsn)
    except ValueError:  # nothing proven, so nothing waved through
        return [RECORD_DSN_NO_HOST]
    # ⛔ THE ENVIRONMENT, BY LIBPQ'S OWN PRECEDENCE: a `PG*` variable supplies an
    # option only when the DSN left it UNSET. `PGHOSTADDR` is the address dialled,
    # which is why it is the one that matters most and the one that was missing.
    for key, value in (("hostaddr", os.environ.get("PGHOSTADDR")),
                       ("host", os.environ.get("PGHOST")),
                       ("service", os.environ.get("PGSERVICE"))):
        if not value or key in keys:
            continue
        if key == "service":
            hosts.append(RECORD_DSN_SERVICE)
        else:
            hosts.extend(_split_host_list(value))
    return hosts


def _assert_local_record_dsn(dsn: str | None, allow_remote: bool) -> None:
    """Refuse a record DSN that is not PROVABLY local — including one we cannot parse.

    ⛔ THE DECISION, not just the leaf. ``urlsplit(...).hostname`` answered a
    different question than the one that matters. It is ``None`` for two legal
    libpq spellings which BOTH reach a remote host — a URI carrying the host in
    its query string (``postgresql:///db?host=db.example.com``) and the
    whitespace conninfo form (``host=db.example.com dbname=x``; PostgreSQL's own
    §34.1.1 admits either) — so written as ``host = …; if host:`` the guard
    SKIPPED exactly when it could not decide. And where it did answer, it could
    be WRONG: a loopback authority with ``?host=``/``?hostaddr=`` in the query
    string reads as local here while libpq dials the query's host (see
    ``_record_dsn_hosts`` for the measurement).

    Both failures sit on the path where ``run()`` seeds that store and
    ``teardown()`` deletes by ``org_id`` — a no-op on the destructive path the
    README advertises as safety property R1.

    ``tortoise.config.is_loopback_uri`` is this repo's fail-closed predicate for
    the same question (an absent hostname is NOT loopback, ``tortoise/config.py:375``);
    this restates its rule with the refusal that names ``--allow-remote-writes``,
    so an unresolvable host is refused unless the caller opts in deliberately.
    """
    # ⛔ `None` means NO record store was configured — nothing to guard. The EMPTY
    # STRING is different: libpq reads PGHOST/PGHOSTADDR/PGSERVICE for it, so
    # short-circuiting on `not dsn` would certify a `""` as provably local on a box
    # whose environment points at a production server.
    if dsn is None:
        return
    for host in _record_dsn_hosts(dsn) or [RECORD_DSN_NO_HOST]:
        if host.startswith("/"):
            # A unix-socket PATH is local by construction — refusing
            # `?host=/var/run/postgresql` made the operator pass
            # `--allow-remote-writes`, a flag whose name asserts the opposite of
            # what they are doing.
            continue
        _assert_loopback(host, "record database", allow_remote)


def _drop_options_pairs(dsn: str) -> str:
    """Re-emit a conninfo without its `options` pair, every other pair intact.

    ⛔ IT PARSES WITH THE SAME WALKER THE GUARD USES (`_conninfo_pairs`). Two
    independent interpretations of one grammar is how a rebuild certified a DSN
    libpq rejects: a regex version truncated a backslash-escaped space and deleted
    `options=` from INSIDE another keyword's quoted value (libpq: `missing "="
    after "-c"`; one password silently became `'a   c'`), and a token loop that
    understood only `key = value` destroyed the one-sided spacings.

    An EMPTY value re-emits QUOTED (`dbname=''`): libpq skips whitespace after
    `=`, so a bare `dbname=` followed by ` options=…` would take `options='-c` as
    dbname's value (`invalid connection option "role"`).
    """
    pairs = _conninfo_pairs(dsn)
    # ⛔ IF NOTHING NEEDS REPAIR, DO NOT REWRITE — return the caller's bytes.
    # A rebuild is only ever needed to (a) delete an `options` pair or (b) quote
    # an EMPTY value (see below). Normalising a string that needs neither is how
    # a "byte-preserving" rebuild still corrupts a legal spelling: `" ".join`
    # collapses the one-sided and doubled spacings libpq admits, so
    # `host=127.0.0.1  =  dbname=x` came back as `host=127.0.0.1 = dbname=x`.
    # ⛔ …AND NOT WHEN THE STRING ENDS IN AN **ODD** RUN OF BACKSLASHES. The caller
    # (`app_role_dsn`) APPENDS ` options='-c role=…'` to whatever this returns, and
    # in libpq's whitespace form an unquoted value's trailing `\` escapes the very
    # next character — the separating space — so `dbname=x\ options='-c
    # role=…'` is parsed as `dbname = "x options='-c"` plus a bogus keyword
    # `role=…'`, and libpq rejects the whole string (`invalid connection option
    # "role"`). Measured on this box: the INPUT `host=127.0.0.1 dbname=x\` parses
    # fine (Connection refused at 127.0.0.1), and the guard CERTIFIES it — so the
    # operator is told the DSN is good and every record query then carries a
    # `record_error`, i.e. no measurement at all. Falling through to the rebuild
    # quotes the value (`dbname='x\\'`), which libpq reads back as `x\`.
    # ⛔ COUNTED ON THE **STRIPPED** TAIL, because `app_role_dsn` appends after
    # `dsn.strip()`: for `dbname=x\ ` (backslash then a space) the UNSTRIPPED tail
    # is whitespace — an even, zero-length run — so the early return handed back
    # the bytes verbatim and the subsequent `.strip()` removed exactly the space
    # the trailing `\` was escaping, reinstating T1 one spelling over. Trailing
    # whitespace is not hypothetical here: that `.strip()` exists because
    # env-var- and file-sourced DSNs carry it.
    tail = dsn.rstrip()
    trailing_backslashes = len(tail) - len(tail.rstrip("\\"))
    if (not any(key == "options" or (key is not None and not value)
                for key, value in pairs)
            and trailing_backslashes % 2 == 0):
        return dsn
    out: list[str] = []
    for key, value in pairs:
        if key is None:
            # A BARE TOKEN (`=x`, `=`) — libpq rejects it, but DROPPING it is the
            # M/N class this rebuild exists to avoid: a different DSN handed to
            # psycopg2 than the one that was certified. Re-emit it verbatim.
            out.append(value or "")
            continue
        if key == "options":
            continue
        out.append(f"{key}={_quote_conninfo_value(value)}")
    return " ".join(part for part in out if part)


def _quote_conninfo_value(value: str | None) -> str:
    """Quote one conninfo VALUE the way libpq reads it — only when it must be.

    libpq's whitespace form takes `keyword = value`, where the value is quoted
    with single quotes (backslash escaping) when it is empty or carries
    whitespace, a quote or a backslash. Re-joining bare tokens loses that quoting,
    so `password='p w'` came back as `password=p w` and libpq refused the string
    (`missing "=" after "w"`) — a DSN the guard had just certified as local.
    `None` is a bare keyword (no `=`), which re-emits as itself.
    """
    if value is None:
        return ""
    if value == "" or any(char.isspace() or char in ("'", "\\") for char in value):
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"
    return value


def app_role_dsn(dsn: str) -> str:
    """The DSN with `-c role=<app>` added, in the SPELLING it arrived in.

    ⛔ TWO SPELLINGS, TWO SYNTAXES, and the one-line `urlunsplit` knew neither.
    For an authority-less URI it emits `postgresql:/db?…` — `postgresql` is not in
    `urllib.parse.uses_netloc`, so an empty netloc loses a slash and libpq rejects
    the whole string (`invalid connection option "postgresql:/tortoise_record?host"`).
    For the whitespace conninfo there is no `?` at all, so a `?options=…` suffix
    lands inside the LAST keyword's value (`host=127.0.0.1?options=…`), which then
    does not resolve. Both spellings are DSNs `_assert_local_record_dsn` accepts,
    so the tool could certify a store it then could not read one row from — and
    every query would carry a `record_error`, i.e. a cannot-measure run.
    """
    options = f"-c role={APP_ROLE}"
    if _is_uri_dsn(dsn):
        # ⛔ THE SAME SPLIT AS THE GUARD: a `?` inside the authority is part of the
        # userinfo, not a query separator — `partition("?")` would cut the DSN in
        # the middle of the password and hand libpq a different connection.
        head, raw_query = _split_uri(dsn)
        # `head` already EXCLUDES the query, so the rebuild below always adds
        # exactly one `?` — a pathless URI (`postgresql://127.0.0.1:5432?host=x`)
        # used to keep its own `?` in `head` and receive a second one, which libpq
        # rejects with `extra key/value separator`.
        # ⛔ `parse_qsl` DECODES `+` AS A SPACE; libpq does not. A password or an
        # `application_name` carrying a literal `+` would silently change under the
        # app's own connection, so the pairs are split here and percent-decoded
        # only — exactly the decoding libpq does.
        params = []
        for pair in raw_query.split("&") if raw_query else []:
            if not pair:
                continue
            key, _, value = pair.partition("=")
            if unquote(key) == "options":
                continue
            params.append((unquote(key), unquote(value)))
        params.append(("options", options))
        # `head` is kept VERBATIM — never re-serialized — so `postgresql:///db`
        # keeps all three slashes.
        return f"{head}?{urlencode(params, quote_via=quote)}"
    # ⛔ DO NOT TOKENISE AND REBUILD — REPLACE ONLY `options`. Every hand-rolled
    # tokeniser here was wrong in a different way, and libpq's conninfo grammar
    # admits spaces on EITHER side of `=`, so a `key =value` pair alone (or a
    # `key= value` one) defeated each successive version: measured with real
    # `PQconninfoParse`, 8 of 15 legal spellings were destroyed —
    # `host =127.0.0.1` came back as `host= =127.0.0.1` (host `"=127.0.0.1"`),
    # `host= 127.0.0.1` as `host='' 127.0.0.1=` (`invalid connection option`),
    # `dbname ='tortoise record'` as `missing "=" after "record'"`.
    #
    # The caller's string is already a DSN libpq accepts — that is what the guard
    # certified. So the only thing this function may change is the `options`
    # keyword it owns: delete that one pair and append its own. Every OTHER
    # keyword and value is re-emitted exactly as parsed (quoted values intact, a
    # backslash-escaped space kept inside its value); only the whitespace BETWEEN
    # keywords is normalised, which libpq does not read. That is the property the
    # pin asserts, via `shlex` rather than this scanner's own helpers.
    stripped = _drop_options_pairs(dsn).strip()
    return f"{stripped} options={_quote_conninfo_value(options)}"


def _require_hnsw_indexdef(indexdefs: list[str], *, label: str = "point",
                           column: str = "embedding_vec") -> str:
    """Return the HNSW indexdef that makes the record arm the leg it CLAIMS.

    ⛔ THE COLUMN IS NOT THE INDEX, AND pgvector DOES NOT ERROR WITHOUT ONE.
    `ORDER BY embedding_vec <=> q LIMIT k` without an index is a perfectly valid
    EXACT SEQUENTIAL SCAN — it returns no error, and because the oracle in this
    harness is itself an exact scan, an index-less record answers the oracle
    VERBATIM. So `arms.record.driver = "pgvector HNSW, cosine"` was a literal
    string nothing verified: on a database where the column exists and the index
    does not (creation failed after the column, the index was dropped, or the
    schema was hand-built) the record arm would report `recall 1.000, order
    exact` — measured by a SEQ SCAN and published under the index's name.

    That is the same failure class the GRAPH side already refuses: `_require_index_arm`
    raises when an "indexed" arm's reported mechanism is not `["index"]`, and
    `wait_index_ready` exists because "a harness that measures the fallback while
    claiming to measure the index is the failure class this whole file is about".
    The `record_error` machinery cannot catch this one — an exact scan has no
    shortfall and no error.

    The decision lives HERE, on data (`pg_indexes.indexdef` strings), so it is
    pinnable without a database; `RecordStore.require_schema` only supplies the
    rows. Quoting is normalised because `indexdef` may carry either form.
    """
    for indexdef in indexdefs:
        normalised = (indexdef or "").replace('"', "").lower()
        if "using hnsw" not in normalised or f"({column}" not in normalised:
            continue
        # ⛔ HNSW IS BUILT FOR **ONE** OPERATOR CLASS, AND THE RECORD ARM READS
        # WITH `<=>` (COSINE). An `hnsw (embedding_vec vector_l2_ops)` or
        # `vector_ip_ops` index cannot serve a cosine `ORDER BY`, so pgvector does
        # not use it and the read falls back to an EXACT SCAN — the very state this
        # guard exists to refuse, one opclass over. The repo says the same thing
        # about the same index (`20261109000001_record_embedding_vector.sql:88-90`:
        # "`vector_l2_ops` here would silently change the ranking, which is the one
        # thing the issue forbids"). An index matched without its opclass is an
        # index this guard has not verified.
        if "vector_cosine_ops" not in normalised:
            continue
        # ⛔ AND NOT A PARTIAL ONE: `… WHERE (status IS NULL)` indexes a subset, so
        # the unfiltered read cannot be answered from it either.
        if " where " in normalised:
            continue
        return indexdef
    raise CannotRun(
        f"cannot run: no HNSW index on public.{label}({column}) — the record leg "
        f"would answer by an EXACT SEQUENTIAL SCAN, which returns this harness's "
        f"own oracle and would publish a perfect match under the index's name. "
        f"Indexes seen: {sorted(indexdefs) or 'none'}")


def _require_valid_hnsw(
    index_rows: list[tuple[str, str, bool]], *, label: str = "point",
    column: str = "embedding_vec",
) -> tuple[str, str]:
    """(name, indexdef) of the VALID HNSW cosine index, or `CannotRun`.

    ⛔ `pg_indexes` DOES NOT CARRY `indisvalid` (PG 16 §54.11 exposes `indexdef`
    and nothing about validity), and `pg_index.indisvalid` is the field the server
    itself consults: *"False means the index is possibly incomplete … it cannot
    safely be used for queries"* (§53.26). So an index left invalid by a failed
    or crashed build carries a PERFECTLY HEALTHY-LOOKING `indexdef`, which the
    listing check accepted — the guard proved the index was LISTED, and listing
    was never the claim. Validity is checked FIRST, then the shape: an invalid
    index is refused by name, because the remedy (REINDEX) differs from the
    remedy for absence (CREATE INDEX), and a message that names the wrong one
    costs a reader a diagnosis.

    Takes the rows (`(name, indexdef, indisvalid)`) rather than a connection so
    the decision is pinnable without Postgres — see
    `test_an_INVALID_index_is_not_an_index_the_record_arm_may_use`.
    """
    valid = [(name, definition) for name, definition, is_valid in index_rows
             if is_valid]
    invalid = [(name, definition) for name, definition, is_valid in index_rows
               if not is_valid]
    try:
        matched = _require_hnsw_indexdef([d for _n, d in valid],
                                         label=label, column=column)
    except CannotRun as refused:
        try:
            _require_hnsw_indexdef([d for _n, d in invalid],
                                   label=label, column=column)
        except CannotRun:
            raise refused from None
        raise CannotRun(
            f"cannot run: the HNSW index on public.{label}({column}) is INVALID "
            f"(pg_index.indisvalid = false — a failed or crashed build, which "
            f"Postgres itself refuses to use for queries) while its `indexdef` "
            f"reads perfectly healthy, so a listing check certified an index no "
            f"read can use. REINDEX it before measuring. Invalid indexes seen: "
            f"{sorted(d for _n, d in invalid)}") from None
    return (next(name for name, definition in valid if definition == matched),
            matched)


class RecordStore:
    def __init__(self, dsn: str, org: str, graph: str):
        import psycopg2

        self.org, self.graph = org, graph
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True
        self.app_dsn = app_role_dsn(dsn)
        self.record_hnsw_index: str | None = None
        self.record_hnsw_index_name: str | None = None
        #: The connection the PLAN is read on: the APP role's, because the app
        #: role is the one the record leg actually reads with.
        self._plan_conn = None

    def require_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='point' "
                "AND column_name='embedding_vec'")
            if not cur.fetchone():
                raise CannotRun(
                    "cannot run: public.point.embedding_vec is absent — the "
                    "pgvector migration has not been applied to this database, "
                    "so every comparison would report the graph fallback as a "
                    "clean answer")
            # ⛔ …AND THE INDEX, not just the column — including its VALIDITY.
            # The decision is `_require_valid_hnsw`'s so it is pinnable without a
            # database (the seam pattern `_require_index_arm` sets out).
            # ⛔ `c` IS THE INDEXED TABLE — THE INDEX NAME IS `ic.relname`.
            # Joining `pg_class` on `i.indrelid` and selecting `c.relname` returns
            # `'point'`, the TABLE, for every row. `assert_index_scan` then compares
            # that against the plan's `Index Name` (the index RELATION, e.g.
            # `idx_point_embedding_vec_hnsw`) and can never match — relations share
            # one namespace, so no index may be named `point` while the table exists.
            # Fail-closed, but it refused EVERY database: the record arm was
            # unreachable and the tool could not produce the measurement it exists
            # for. `test_the_index_name_comes_from_the_index_relation` pins the join.
            cur.execute(
                "SELECT ic.relname, pg_get_indexdef(i.indexrelid), i.indisvalid "
                "FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_class ic ON ic.oid = i.indexrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname='public' AND c.relname='point'")
            name, definition = _require_valid_hnsw(
                [(str(n_), str(d_), bool(v_)) for n_, d_, v_ in cur.fetchall()])
        self.record_hnsw_index = definition
        self.record_hnsw_index_name = name

    def environment(self) -> dict:
        """The versions the run actually measured on — recorded, not remembered.

        ⛔ A version quoted in a write-up that the artifact does not carry is
        unreproducible by the next reader, and the next reader is the one who has
        to decide whether the numbers still apply.
        """
        out: dict = {}
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
                row = cur.fetchone()
                out["pgvector"] = row[0] if row else None
                cur.execute("SHOW server_version")
                out["postgres"] = cur.fetchone()[0]
        except Exception as e:  # noqa: BLE001, RUF100
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    def seed(self, rows: list[Row]) -> None:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, name) "
                        "VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                        (self.org, self.org))
            cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                        "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                        (self.graph, self.org, self.graph))
            for row in rows:
                if row.tenant != "ours":
                    continue
                cur.execute(
                    "INSERT INTO public.node_identity "
                    "(org_id, graph_id, node_label, node_key) "
                    "VALUES (%s, %s, 'Point', %s) ON CONFLICT DO NOTHING",
                    (self.org, self.graph, row.key))
                cur.execute(
                    "INSERT INTO public.point (org_id, graph_id, node_key, id, "
                    "status, outdated, embedding, is_operator, op_type, "
                    "updated_at, created_at, point_kind) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)",
                    (self.org, self.graph, row.key, row.key, row.status,
                     row.outdated,
                     None if row.embedding is None else json.dumps(row.embedding),
                     None, None, row.updated_at, row.updated_at, "claim"))

    def seed_foreign_tenant(self, rows: list[Row]) -> None:
        """The SAME vectors under a second org — a leak would rank it first."""
        other_org = f"{self.org}_foreign"
        other_graph = f"{self.graph}_foreign"
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, name) "
                        "VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                        (other_org, other_org))
            cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                        "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                        (other_graph, other_org, other_graph))
            for row in rows:
                if row.tenant != "other":
                    continue
                cur.execute(
                    "INSERT INTO public.node_identity "
                    "(org_id, graph_id, node_label, node_key) "
                    "VALUES (%s, %s, 'Point', %s) ON CONFLICT DO NOTHING",
                    (other_org, other_graph, row.key))
                cur.execute(
                    "INSERT INTO public.point (org_id, graph_id, node_key, id, "
                    "status, outdated, embedding, is_operator, op_type, "
                    "updated_at, created_at, point_kind) "
                    "VALUES (%s, %s, %s, %s, NULL, NULL, %s::jsonb, NULL, NULL, "
                    "%s, %s, 'claim')",
                    (other_org, other_graph, row.key, row.key,
                     json.dumps(row.embedding), row.updated_at, row.updated_at))

    def assert_index_scan(self, query: list[float], k: int) -> str:
        """The PLAN the record READ gets — the index must ANSWER, not merely exist.

        ⛔ `require_schema` PROVES A LISTING; THIS PROVES A PLAN, and the two are
        different claims (the whole point of round 20). Two independent holes
        remained after the listing check, both measured:

          * `pg_index.indisvalid` — now checked in `require_schema` — because an
            invalid index's `indexdef` reads healthy;
          * pgvector's OWN documented behaviour: *"if the table is small, a table
            scan may be faster"*, and *"By default, pgvector performs exact
            nearest neighbor search, which provides perfect recall"*
            (README → Troubleshooting → *"Why isn't a query using an index?"*).
            This harness seeds ~55 `public.point` rows — the small-table case — so
            a verified-THEN-ignored index is reachable, and the oracle here is
            itself an exact scan: a Seq Scan reproduces it VERBATIM and would
            publish `recall 1.000` under the index's name. That is the K5 class
            one indirection further out, and it is why `recall=1.000` in
            `parity-run.json` was equally consistent with "the ANN leg never ran".

        ⛔ THE STATEMENT IS `record_vector.knn_statement`'s, not a copy. An EXPLAIN
        of a re-spelled SELECT proves something about the re-spelling; the builder
        is the ONE definition `vector_search` also uses, so the plan read here is
        the plan of the statement the leg executes.

        Raises `CannotRun` on a Seq Scan of `public.point`, or when the verified
        index is not the one the plan used. Returns the plan node type, which
        `run()` records as `arms.record.plan`.
        """
        from tortoise import record_vector

        statement = record_vector.knn_statement(
            LABEL, record_vector._Scope(org_id=self.org, graph_id=self.graph),
            query, k)
        if statement is None:
            raise CannotRun(
                f"cannot run: `record_vector` would not build a kNN statement for "
                f"label {LABEL!r}, so there is no plan to check")
        sql, params = statement
        if self._plan_conn is None:
            import psycopg2
            # ⛔ THE APP ROLE'S CONNECTION, because the app role is the one the
            # record leg READS with — planning as the owner would answer for a
            # connection this harness never reads through.
            self._plan_conn = psycopg2.connect(self.app_dsn)
            self._plan_conn.autocommit = True
        with self._plan_conn.cursor() as cur:
            # The same session setting the read installs, or the plan measured
            # here is not the plan the read produced (see `HNSW_ITERATIVE_SCAN`).
            # ⛔ `is_local = FALSE`, NOT `TRUE`: `_plan_conn` is AUTOCOMMIT, so a
            # transaction-local setting is discarded with the statement that set it
            # — `SHOW hnsw.iterative_scan` then answers the default, and the plan
            # would be measured under a setting the read never used.
            cur.execute("SELECT set_config('hnsw.iterative_scan', %s, false)",
                        (record_vector.HNSW_ITERATIVE_SCAN,))
            cur.execute("EXPLAIN (FORMAT JSON) " + sql, params)
            plan = cur.fetchone()[0]
        return _require_index_plan(_plan_nodes(plan), self.record_hnsw_index_name)

    def read(self, query: list[float], k: int) -> list[tuple[str, float, object]]:
        from tortoise import record_vector

        scope = record_vector._Scope(org_id=self.org, graph_id=self.graph)
        return record_vector.vector_search(
            scope, LABEL, query, k, record_dsn=self.app_dsn) or []

    def teardown(self) -> None:
        with self.conn.cursor() as cur:
            for org in (self.org, f"{self.org}_foreign"):
                cur.execute("DELETE FROM public.point WHERE org_id = %s", (org,))
                cur.execute("DELETE FROM public.node_identity WHERE org_id = %s",
                            (org,))
                cur.execute("DELETE FROM public.graphs WHERE org_id = %s", (org,))
                cur.execute("DELETE FROM public.organizations WHERE id = %s", (org,))

    def close(self) -> None:
        if self._plan_conn is not None:
            self._plan_conn.close()
            self._plan_conn = None
        self.conn.close()


def _require_index_plan(nodes: list[dict], verified_name: str | None, *,
                        label: str = "point") -> str:
    """The plan node that served the read, or `CannotRun` — the DB-free decision.

    ⛔ AN INDEX THAT IS LISTED, VALID AND CORRECT IS STILL NOT EVIDENCE THAT IT
    WAS USED. pgvector's own README (Troubleshooting → *"Why isn't a query using
    an index?"*) says *"if the table is small, a table scan may be faster"* and
    *"By default, pgvector performs exact nearest neighbor search, which provides
    perfect recall"*. This harness seeds ~55 rows, and its ORACLE is itself an
    exact scan — so a Seq Scan reproduces the oracle VERBATIM and would publish
    `recall 1.000` under the index's name. Measured in review: a verified
    `record_hnsw_index` plus a record leg answering the oracle gave `EXIT: 0`,
    PARITY, with the ANN leg possibly never running.

    A plan is GRADED HERE, not eyeballed: the verified index must appear as the
    `Index Name` of an `Index Scan`, and a `Seq Scan` of the table refuses —
    which is the fail-closed direction (it converts an unmeasurable arm into
    `cannot run`, exit 2, never into a green).
    """
    seq = [n for n in nodes
           if n.get("Node Type") == "Seq Scan"
           and n.get("Relation Name") == label]
    used = {(n.get("Node Type"), n.get("Index Name")) for n in nodes
            if n.get("Index Name")}
    if seq or (verified_name is not None
               and verified_name not in {name for _t, name in used}):
        raise CannotRun(
            f"cannot run: the record read's own statement is answered by a "
            f"SEQ SCAN of public.{label} (or by an index other than the one this "
            f"run verified, {verified_name!r}) — so the arm would publish the "
            f"index's name over a plan the index did not serve. pgvector does this "
            f"BY DESIGN on a small table (*\"if the table is small, a table scan "
            f"may be faster\"*), and its exact scan reproduces this harness's own "
            f"oracle verbatim. Plan nodes: {sorted(used) or 'none'}"
        )
    return ", ".join(sorted({f"{t} on {n}" for t, n in used}))


def _plan_nodes(plan: object) -> list[dict]:
    """Every plan node in an `EXPLAIN (FORMAT JSON)` result, flattened.

    ⛔ RECURSIVE, because the node that answers the ORDER BY need not be the root:
    a `Limit` → `Index Scan` is the shape pgvector produces, and a non-recursive
    read of the root would see only `Limit` and conclude nothing about the index
    — the same "checked the wrong level" defect one layer down.
    """
    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if "Node Type" in node:
            found.append(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value)

    walk(plan)
    return found


#: The record's table/column for one label, and whether a vector index exists.
class GraphStore:
    def __init__(self, host: str, port: int, graph: str):
        from falkordb import FalkorDB

        # #3065/#8118: `falkordb` passes `socket_timeout` straight through to
        # redis-py, whose default is `None` — i.e. UNBOUNDED. Against a stalled
        # engine the process then parks in `recv()` forever at 0% CPU, printing
        # nothing (#3065, #2838's MERGE lock, #2969's defrag loop). The bound
        # comes from the product's ONE resolution point so it is knob-aware
        # (`TORTOISE_DB_SOCKET_TIMEOUT`) rather than a second pair of literals.
        from tortoise.projection import _bounded_server_socket_kwargs

        self.graph_name = graph
        self.client = FalkorDB(host=host, port=port, **_bounded_server_socket_kwargs())
        self.g = self.client.select_graph(graph)

    def reset(self) -> None:
        """Delete this run's graph, then PROVE it is gone (R1, not a claim).

        ⛔ A SWALLOWED DELETE IS AN UNVERIFIED SAFETY CLAIM. `GRAPH.DELETE` on an
        absent graph raises, and absence is the post-condition — so the failure
        was suppressed and NOTHING observed whether the delete happened. A delete
        that failed for any other reason (a transient module error, a concurrent
        writer) was therefore invisible: the run reported as normal and left the
        corpus graph AND its HNSW index behind, which is README §5.1 B1's residue
        in its silent form — the defect B1 says was fixed, still unfalsifiable
        because the only evidence it held was a human's out-of-band `GRAPH.LIST`.
        The same suppression sits on `rebuild_with_ef`'s path, where a failed
        delete makes `seed()` add a SECOND copy of the corpus and the
        `efRuntime=200` diagnostic is then computed over a doubled graph.

        So: attempt the delete, ignore its error (a missing graph is the goal),
        then read the graph catalogue and refuse to continue if this graph is
        still there. The record side already fails loudly (`teardown()` is not
        suppressed); this is the same posture for the graph side.
        """
        delete_error: str | None = None
        try:
            self.g.delete()
        except Exception as e:  # noqa: BLE001, RUF100 — a missing graph raises, and absence is the goal
            delete_error = f"{type(e).__name__}: {e}"
        self.g = self.client.select_graph(self.graph_name)
        try:
            remaining = {name.decode() if isinstance(name, bytes) else str(name)
                         for name in self.client.list_graphs()}
        except Exception as e:  # noqa: BLE001, RUF100 — cannot PROVE absence ⇒ cannot claim R1
            raise CannotRun(
                f"cannot verify that the graph {self.graph_name!r} was deleted "
                f"({type(e).__name__}: {e}) — this run cannot claim it left no "
                f"graph behind") from e
        if self.graph_name in remaining:
            raise CannotRun(
                f"graph {self.graph_name!r} SURVIVED teardown — refusing to report "
                f"a clean run over a graph this harness left behind"
                + (f" (the delete raised {delete_error})" if delete_error else ""))

    def seed(self, rows: list[Row]) -> None:
        for row in rows:
            if row.tenant != "ours":
                continue
            props = {"id": row.key, "key": row.key}
            if row.status is not None:
                props["status"] = row.status
            if row.outdated is not None:
                props["outdated"] = row.outdated
            props["updatedAt"] = row.updated_at
            props["createdAt"] = row.updated_at
            if row.embedding is None:
                self.g.query(
                    "CREATE (n:Point) SET n += $props", params={"props": props})
                continue
            if len(row.embedding) == DIM:
                # The writer's form: a stored vector is `vecf32(...)`.
                self.g.query(
                    "CREATE (n:Point) SET n += $props "
                    "SET n.embedding = vecf32($vec)",
                    params={"props": props, "vec": [float(v) for v in row.embedding]})
            else:
                # A caller-owned width is stored VERBATIM — a plain list, not a
                # `Vectorf32` (PR #3018). Seeding it any other way would test a
                # shape no writer produces, and would trip #8430's declared
                # residual instead of the seam that exists for this case.
                self.g.query(
                    "CREATE (n:Point) SET n += $props SET n.embedding = $vec",
                    params={"props": props,
                            "vec": [float(v) for v in row.embedding]})

    def create_index(self, ef_runtime: int | None = None) -> str:
        extra = f", efRuntime: {int(ef_runtime)}" if ef_runtime else ""
        try:
            self.g.query(
                "CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                f"OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'{extra}}}")
            return "cypher"
        except Exception as e:  # noqa: BLE001, RUF100
            if "already" in str(e).lower():
                return "existing"
            try:
                self.g.query(
                    "CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                    f"OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine', "
                    f"'HNSW'{extra}}}")
                return "procedure"
            except Exception as e2:  # noqa: BLE001, RUF100
                if "already" in str(e2).lower():
                    return "existing"
                raise

    def wait_index_ready(self, probe: list[float], timeout_s: float = 60.0) -> bool:
        """Block until `queryNodes` ANSWERS — the index build is not synchronous.

        ⛔ MEASURED, and it silently corrupts the measurement if skipped: on
        FalkorDB 4.20.6 a freshly-created HNSW index returns ZERO rows for a few
        seconds while it builds, and `run_vector_query` answers that with the
        `#8429` fall-through to the brute-force scan. The arm then reports
        ``mechanisms: ["index", "scan_fallback"]`` and the "indexed" column is
        really a second scan — which is what this harness produced on its first
        run against a real store (`q_anchor` graph_index recall 0.5 = the scan's
        number, not an ANN result). A harness that measures the fallback while
        claiming to measure the index is the failure class this whole file is
        about, so readiness is a WAIT with a loud failure, never a `sleep`.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                res = self.g.query(
                    "CALL db.idx.vector.queryNodes('Point', 'embedding', 1, "
                    "vecf32($q)) YIELD node RETURN node.id",
                    params={"q": [float(v) for v in probe]})
                if res.result_set:
                    return True
            except Exception:  # noqa: BLE001, RUF100 — not built yet, or not registered yet
                pass
            time.sleep(0.25)
        return False

    def environment(self) -> dict:
        out: dict = {}
        try:
            # `MODULE LIST` answers DICTS on this client (`decode_responses`), not
            # the flat tuples an earlier cut assumed — measured, and the flat form
            # silently recorded nothing.
            for entry in self.client.connection.execute_command("MODULE LIST"):
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", ""))
                if name in ("graph", "falkordb"):
                    ver = entry.get("ver")
                    # e.g. 42006 -> "4.20.6", the spelling the module itself uses.
                    text = str(ver)
                    pretty = (f"{text[0]}.{text[1:3]}.{int(text[3:])}"
                              if text.isdigit() and len(text) == 5 else str(ver))
                    out["falkordb"] = f"{pretty} (module ver {ver})"
        except Exception as e:  # noqa: BLE001, RUF100
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    def index_params(self) -> dict:
        """The parameters the store ACTUALLY built the index with.

        ⛔ Read from the engine, never assumed. FalkorDB's default `efRuntime` is
        **10**, and a `queryNodes` call takes exactly FOUR arguments on 4.20.6
        (measured: passing a fifth, `{efRuntime: N}`, is refused with
        ``requires 4 arguments, got 5``), so the ef cannot be raised per query —
        only by re-creating the index. Recording the parameters is what turns a
        recall shortfall into a diagnosis instead of a bug report.
        """
        try:
            # `CALL db.indexes()` row shape on 4.20.6 (measured):
            #   [label, fields, types, OPTIONS, language, stopwords, entity, status, …]
            # — so the index PARAMETERS are column 3, keyed by property. Reading
            # column 2 instead yields `{'embedding': ['VECTOR']}` and an empty
            # params dict, which silently costs the artifact its diagnosis.
            for row in self.g.query("CALL db.indexes()").result_set:
                if row[0] != LABEL_CYPHER or len(row) < 4:
                    continue
                params = row[3] or {}
                if "embedding" in params:
                    return dict(params["embedding"])
        except Exception:  # noqa: BLE001, RUF100
            pass
        return {}

    def rebuild_with_ef(self, ef_runtime: int, rows: list[Row]) -> None:
        """Drop + re-create the index at a higher `efRuntime`, re-seeding rows.

        A re-seed rather than an index DROP because the DROP spelling varies
        across engines and a silently failed drop would leave the OLD index in
        place — i.e. the `efRuntime` diagnostic would measure the default again
        and report the gap as unexplained.
        """
        self.reset()
        self.seed(rows)
        self.create_index(ef_runtime=ef_runtime)

    def read(self, query: list[float], k: int) -> dict:
        from tortoise import search_engine

        trace: list[dict] = []
        rows = search_engine.run_vector_query(
            self.g, query, limit=k, is_embedded=False,
            entity_type=LABEL, leg_trace=trace)
        mechanism = trace[-1].get("mechanism") if trace else None
        # Similarity → distance, PER BRANCH. The index branch converts `1 - d`
        # (cosine); the scan branch converts `1 / (1 + d)` (euclidean). Reading
        # the mechanism from the leg's own trace rather than inferring it is the
        # only way to invert the right one.
        if mechanism == "index":
            distances = [(key, 1.0 - sim) for key, sim in rows]
        else:
            distances = [(key, (1.0 / sim) - 1.0 if sim > 0 else math.inf)
                         for key, sim in rows]
        return {"rows": distances, "mechanism": mechanism, "trace": trace}

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.client.close()


# ── the run ─────────────────────────────────────────────────────────────────

def _require_index_arm(mechanisms: list[str], ready: bool) -> None:
    """Refuse an "indexed" arm that did not answer via the index.

    ⛔ A NAMED SEAM for the same reason `_scan_arm` is one: while this decision
    was an inline `if` inside `run()`, no DB-free test could reach it, and
    reintroducing the inert form `if ready and mechanisms != ["index"]` left
    **35/35 green** (measured in review, round 6). That form was a real shipped
    defect: when `wait_index_ready` TIMED OUT it returned `False`, the conjunct
    was False, and the arm was then published as `run_vector_query HNSW` while
    measuring the `#8429` scan fall-through — exit 0, PARITY, about the SCAN.
    So `ready` decides the MESSAGE and never whether to refuse; the arm's own
    reported mechanism decides that.
    """
    if mechanisms != ["index"]:
        raise CannotRun(
            f"cannot run: the indexed arm answered via {mechanisms} "
            f"(index ready={ready}) — the indexed comparison is not measuring "
            f"the index, so any parity it reports would be about the scan")


def run(args) -> dict:
    rows = build_corpus(seed=args.seed, n_random=args.n_random)
    k = args.k

    run_id = uuid.uuid4().hex[:10]
    org = f"org_13906_parity_{run_id}"
    graph = f"graph_13906_parity_{run_id}"
    graph_name = f"g13906parity{run_id}"

    _assert_local_record_dsn(args.record_dsn, args.allow_remote_writes)
    if args.graph_port:
        _assert_loopback(args.graph_host, "graph store", args.allow_remote_writes)

    record = None
    store = None
    if args.record_dsn:
        record = RecordStore(args.record_dsn, org, graph)
        try:
            # ⛔ INSIDE the connection's lifetime: a `CannotRun` raised here must
            # close the connection, or a run that refused its target leaves one
            # open against the store it just declined to touch.
            record.require_schema()
        except BaseException:
            record.close()
            raise
    if args.graph_port:
        store = GraphStore(args.graph_host, args.graph_port, graph_name)

    if record is None or store is None:
        raise CannotRun(
            "cannot run: both --record-dsn (a Postgres carrying the record "
            "migrations AND pgvector) and --graph-port (a FalkorDB) are "
            "required — a one-sided run is not a parity measurement")

    queries = _queries(rows, args.queries)
    report: dict = {
        "issue": "#13906",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "k": k, "seed": args.seed, "n_rows": len(rows),
        "label": LABEL,
        "poison": args.poison,
        "environment": {"record": record.environment(),
                        "graph": store.environment()},
        "arms": {},
        "queries": [],
    }

    try:
        record.seed(rows)
        record.seed_foreign_tenant(rows)
        store.reset()
        # ⛔ THE `--poison vector` MUTATION HAPPENS AT SEED TIME, not after the
        # arms are read. A store divergence that is applied after `scan_answers`
        # was taken leaves that arm measuring the UNPOISONED data and the poison
        # goes undetected — measured on this harness's first attempt: `vector`
        # reported POISON NOT DETECTED while the other three were caught. The
        # oracle stays on the unperturbed corpus, which is the point.
        store_rows = perturb_for_vector_poison(rows) if args.poison == "vector" else rows
        store.seed(store_rows)

        # ── record arm ────────────────────────────────────────────────────
        # ⛔ A record-leg FAILURE must arrive as a NAMED divergence, not a
        # traceback: an uncaught `VectorShortfall` / `RecordVectorError` /
        # `RecordVectorDeclined` escapes `main` as a stack trace and exits **1**,
        # which this module's table defines as "a comparison diverged" — right
        # answer, no diagnosis, and indistinguishable from a crash in the harness
        # itself. `_verdict` fails any query carrying a `record_error`.
        rec_answers: dict[str, list] = {}
        rec_errors: dict[str, str] = {}
        for qid, qvec in queries:
            try:
                rec_answers[qid] = record.read(qvec, k)
            except Exception as e:  # noqa: BLE001, RUF100 — record_vector's four, by name
                rec_answers[qid] = []
                rec_errors[qid] = f"{type(e).__name__}: {e}"
        # ⛔ THE PLAN THE RECORD READ ACTUALLY GETS, once per query — not the
        # index's existence. Round 20's finding: `require_schema` proved an HNSW
        # index was LISTED, while pgvector answers small tables by EXACT SEQ SCAN
        # *by design* ("if the table is small, a table scan may be faster") and
        # this harness seeds ~55 rows. `assert_index_scan` EXPLAINs
        # `record_vector.knn_statement` — the SAME statement `vector_search` runs
        # — and raises `CannotRun` on a Seq Scan, so an arm that would have
        # published the index's name over a scan cannot report at all. The plans
        # are recorded because a driver name is a claim and this is the evidence.
        record_plans = {qid: record.assert_index_scan(qvec, k)
                        for qid, qvec in queries}
        report["arms"]["record"] = {
            "driver": "pgvector HNSW, cosine",
            # ⛔ THE INDEX ITSELF, from `pg_index` by `require_schema` (which
            # REFUSES without a VALID one) — a driver name is a claim, and this
            # artifact is the evidence.
            "index": getattr(record, "record_hnsw_index", None),
            "index_name": getattr(record, "record_hnsw_index_name", None),
            # query id -> the plan node that served it. All must name the index.
            "plan": record_plans,
        }

        # ── graph arms: scan first (production today), then indexed ──────
        scan_answers = {qid: store.read(qvec, k) for qid, qvec in queries}
        report["arms"]["graph_scan"] = {
            "driver": "run_vector_query brute-force scan, euclidean",
            "mechanisms": sorted({a["mechanism"] for a in scan_answers.values()}),
        }
        index_api = store.create_index()
        # ⛔ The index is built ASYNCHRONOUSLY. Wait for it to ANSWER, or the
        # "indexed" arm silently measures the scan fallback (see
        # `wait_index_ready`). Failing loudly is correct: an index arm that is
        # not the index arm is not a measurement.
        ready = store.wait_index_ready(queries[0][1])
        index_answers = {qid: store.read(qvec, k) for qid, qvec in queries}
        mechanisms = sorted({a["mechanism"] for a in index_answers.values()})
        report["arms"]["graph_index"] = {
            "driver": f"run_vector_query HNSW ({index_api}), cosine",
            "index_ready": ready,
            "mechanisms": mechanisms,
        }
        report["arms"]["graph_index"]["index_params"] = store.index_params()
        # ⛔ THE REFUSAL, through a named seam (`_require_index_arm`) so its
        # inert form can be caught without a store — see the seam's docstring.
        _require_index_arm(mechanisms, ready)

        # ── the efRuntime diagnostic ──────────────────────────────────────
        # Run ONLY when the default-arm recall is short: it exists to explain a
        # shortfall, and a green default arm has nothing to explain. It is
        # INFORMATIONAL (it re-creates an index production would not have) and
        # its purpose is to separate "the graph index is semantically different"
        # from "the graph index answers with efRuntime=10".
        idx_recall = min(
            compare(truth(rows, qvec, k, metric="cosine"),
                    [key for key, _d in index_answers[qid]["rows"]], k=k)["recall"]
            for qid, qvec in queries)
        report["arms"]["graph_index"]["min_recall"] = idx_recall
        ef200_answers: dict[str, dict] = {}
        if idx_recall < 1.0:
            ef = args.ef_diagnostic
            store.rebuild_with_ef(
                ef, [r for r in store_rows if r.tenant == "ours"])
            if not store.wait_index_ready(queries[0][1]):
                raise CannotRun("cannot run: the ef-diagnostic index never answered")
            ef200_answers = {qid: store.read(qvec, k) for qid, qvec in queries}
            report["arms"][f"graph_index_ef{ef}"] = {
                "driver": f"run_vector_query HNSW at efRuntime={ef} (diagnostic)",
                "index_params": store.index_params(),
                "mechanisms": sorted({a["mechanism"] for a in ef200_answers.values()}),
            }

        for qid, qvec in queries:
            t_cos = truth(rows, qvec, k, metric="cosine")
            t_euc = truth(rows, qvec, k, metric="euclidean")

            rec_ids = [key for key, _d, _r in rec_answers[qid]]
            rec_dists = [d for _k2, d, _r in rec_answers[qid]]
            scan_ids = [key for key, _d in scan_answers[qid]["rows"]]
            idx_ids = [key for key, _d in index_answers[qid]["rows"]]

            if args.poison in ("id", "order", "recall"):
                rec_pairs = poison(list(zip(rec_ids, rec_dists, strict=True)),
                                   t_cos, args.poison)
                rec_ids = [key for key, _d in rec_pairs]
                rec_dists = [d for _key, d in rec_pairs]
            if args.poison == "vector":
                # The graph arm is re-read AFTER the rewrite; that is the point.
                index_answers[qid] = store.read(qvec, k)
                # ⛔ AND THE PARAMETERS ARE RE-READ WITH IT: `index_params` was
                # captured from the DEFAULT index, so when the ef-diagnostic
                # rebuild ran this arm published ef=200 answers under efRuntime=10
                # parameters — "a driver name is a claim, the artifact is the
                # evidence" broken from the other side.
                report["arms"]["graph_index"]["index_params"] = store.index_params()
                idx_ids = [key for key, _d in index_answers[qid]["rows"]]

            entry = {
                "query": qid,
                "record_error": rec_errors.get(qid),
                "truth_cosine": [key for key, _ in t_cos],
                # ⛔ DISTANCES ARE ALWAYS PASSED, never conditionally. An earlier
                # cut skipped them whenever the candidate list had been poisoned
                # (`len(answers) != len(ids)`), which left `distance.ok = None` on
                # exactly the runs where a value-level check matters most — and
                # `_verdict` treats "unmeasured" as "not failed". Two lengths that
                # disagree now simply stop the zip, so the ids check (which is the
                # one that fails) still sees the whole poisoned list.
                "record": compare(t_cos, rec_ids, k=k,
                                  candidate_distances=rec_dists),
                "graph_index": compare(
                    t_cos, idx_ids, k=k,
                    candidate_distances=[d for _k2, d in index_answers[qid]["rows"]]),
                # ⛔ A LOOSER TOLERANCE FOR THE SCAN (`SCAN_DISTANCE_TOL`,
                # read from the module constant and pinned at this call site):
                # see the constant's comment for the arithmetic. The record and
                # index arms carry a distance directly (`<=>`, `1-d`) and keep
                # 1e-4.
                "graph_scan_vs_euclidean": _scan_arm(
                    t_euc, scan_ids,
                    [d for _k2, d in scan_answers[qid]["rows"]], k=k),
                "graph_scan_vs_cosine": compare(t_cos, scan_ids, k=k,
                                                candidate_distances=None),
            }
            if ef200_answers:
                entry[f"graph_index_ef{args.ef_diagnostic}"] = compare(
                    t_cos, [key for key, _d in ef200_answers[qid]["rows"]], k=k,
                    candidate_distances=[d for _k2, d in ef200_answers[qid]["rows"]])
            # ⛔ THE UNDER-RETURN COLUMN, and it is why this harness earned its
            # keep. `queryNodes` picks its `$limit` rows BEFORE the `#689`
            # predicate is applied (the predicate rides between `YIELD` and
            # `RETURN`), so a terminal row inside the candidate window shrinks the
            # answer BELOW k and nothing over-fetches or asserts. Measured on the
            # first live run: `k=10` returned **8** rows for a query whose
            # neighbourhood contains a retracted and an outdated Point. The record
            # leg applies the predicate server-side ahead of its LIMIT, so it
            # returns 10 — which is the asymmetry `#5090` Q2 forbids.
            entry["graph_index_returned"] = len(index_answers[qid]["rows"])
            entry["graph_scan_returned"] = len(scan_answers[qid]["rows"])
            entry["record_returned"] = len(rec_answers[qid])
            entry["graph_index_shortfall"] = k - len(index_answers[qid]["rows"])
            entry["record_vs_graph_scan"] = _cross_recall(rec_ids, scan_ids)
            entry["record_vs_graph_index"] = _cross_recall(rec_ids, idx_ids)
            entry["metric_divergence_ids"] = sorted(
                {key for key, _ in t_cos} ^ {key for key, _ in t_euc})
            report["queries"].append(entry)

    finally:
        if args.keep:
            print(f"[keep] org={org} graph={graph} cypher_graph={graph_name}")
        else:
            try:
                record.teardown()
            finally:
                # ⛔ THE CYPHER GRAPH WAS NEVER DELETED, while the record rows
                # were — so every non-`--keep` run left a 55-node graph AND an
                # HNSW index behind on the FalkorDB instance, invisible because
                # `_assert_loopback`'s description of what this harness writes
                # named only the record's tables. Measured in review.
                try:
                    store.reset()
                finally:
                    store.close()
                    record.close()

    report["verdict"] = _verdict(report, poisoned=args.poison)
    return report


def _cross_recall(a: list[str], b: list[str]) -> dict:
    """Overlap of two candidate lists, or `recall: None` over an EMPTY one.

    ⛔ An empty `a` used to publish `recall: 1.0` — the ``an empty answer is not a
    passed check`` class `compare()`'s own recall was already fixed for. A raised
    record leg answers `[]`, so those artifact rows read as PERFECT agreement over
    zero rows. It cannot turn a run green (the required record arm fails first),
    but a field that says 1.0 over nothing is a claim the data does not support.
    Artifact replay is unaffected: every committed run has a non-empty `a`.
    """
    sa, sb = set(a), set(b)
    return {"recall": len(sa & sb) / len(sa) if sa else None,
            "only_a": sorted(sa - sb)[:10], "only_b": sorted(sb - sa)[:10]}


def _arm_failure(q: str, label: str, cmp_: dict, *, oracle: str,
                 error: str | None = None) -> tuple[str | None, bool]:
    """(message, measured) for one REQUIRED arm. `message is None` = it agreed.

    ⛔ `measured` is the whole point of this function: it separates a failure that
    is EVIDENCE from a failure that is an ABSENCE OF EVIDENCE, and the falsifier's
    proof rests on that difference (see `_verdict`'s `poison_status`).

    A failure is MEASURED only when the arm actually produced comparable numbers:

    * the leg did not raise — a raised leg's empty answer is a consequence of the
      exception, not a divergence the poison produced;
    * the oracle was not empty (`recall is None`);
    * at least one candidate row had a scoreable distance. `compare` now reports
      `distance.ok is None` over zero compared rows, and `unmeasured == len(cand)`
      means every reported distance was NaN/inf/absent (pgvector's NaN on a
      zero-norm vector is the reachable case) — neither says anything about what
      the comparison can SEE.

    Everything else is a `measured` divergence: wrong ids, a cross-bucket
    re-order, a distance beyond tolerance over rows that WERE scored, or an
    empty/under-width answer the leg genuinely returned.
    """
    if error:
        return (f"{q}: the {label} leg FAILED — {error}", False)
    dist = cmp_.get("distance") or {}
    dist_ok = dist.get("ok")
    ids_bad = (not cmp_["ids_equal"]) or cmp_["order"] == "diverged"
    oracle_empty = cmp_["recall"] is None
    if not ids_bad and not oracle_empty and dist_ok is True:
        return None, True
    # ⛔ ZERO ROWS IS NOT A MEASUREMENT, and it is caught TWICE here on purpose:
    # `compare` answers `distance.ok is None` over zero compared rows (so `dist_ok
    # is True` is False), and `unmeasured(0) < total(0)` is False as well — which
    # is why there is no separate `total > 0` conjunct: it was dead, and a
    # condition no test can observe is a condition nobody is checking.
    total = len(cmp_.get("candidate_ids") or [])
    measured = (not oracle_empty) and (
        dist_ok is True or int(dist.get("unmeasured") or 0) < total)
    # ⛔ THE TEXT IS PART OF THE COMMITTED EVIDENCE. The five artifacts under
    # `docs/research/2026-11-20-13906-vector-parity/` store the failure strings
    # these arms produced, and they must keep replaying bit-identically through
    # this verdict (checked after every edit here) — so the shape below is the one
    # they were minted with. New diagnostics belong in `compare`'s returned dict,
    # which the artifacts carry whole, not appended to the sentence.
    return (f"{q}: {label} diverged from {oracle} "
            f"(recall={cmp_['recall']}, order={cmp_['order']}, "
            f"distance_ok={dist_ok}, "
            f"distance_delta={dist.get('max_delta')})", measured)


def _verdict(report: dict, *, poisoned: str | None) -> dict:
    """Which comparisons are REQUIRED, and did any fail?

    Two are required, and they answer different questions:

    * ``record`` vs ``truth_cosine`` — **the acceptance for the migration**: the
      leg being moved to, on the metric its index is built with;
    * ``graph_scan_vs_euclidean`` — **the comparator's own sanity check**: the
      graph scan must reproduce ITS oracle, or the harness's eligibility
      predicate or distance inversion is wrong and nothing else here is verified.

    Two are INFORMATIONAL, because they measure the leg being REPLACED and a
    metric drift this issue does not decide:

    * ``graph_index`` vs ``truth_cosine`` — measured on a real store at
      FalkorDB's DEFAULT ``efRuntime: 10``, where it UNDER-RETURNS: `k=10`
      returns **8 rows** on two of six queries because `queryNodes` picks its
      `$limit` before `#689`'s predicate lands between `YIELD` and `RETURN`
      (filed as #13911). Gating on it would red this harness for the state of
      the leg the record REPLACES. ⛔ The ``graph_index_ef200`` diagnostic arm
      does NOT explain it away — it returned the same 8 rows at
      `efRuntime: 200`, i.e. the shortfall is a row COUNT, not an ef. An earlier
      draft of this docstring claimed the diagnostic "shows the shortfall is an
      ef setting"; its own artifact refutes that, and the real cause is the one
      that is REPORTED per query as ``graph_index_shortfall`` — and NOT gated:
      nothing in this verdict reads that field, because the index arm is
      informational (the leg the record REPLACES);
    * ``graph_scan`` vs ``truth_cosine`` — the cosine/euclidean drift.
    """
    failures: list[str] = []
    # ⛔ A FAILURE THAT IS AN ABSENCE OF MEASUREMENT IS NOT A DETECTION, and the
    # falsifier's own proof depends on the difference. `poison_status` used to be
    # `"POISON DETECTED" if not ok`, i.e. EVERY failure counted as a detection —
    # including the cannot-measure failures this verdict already names. Measured
    # in review: six entries each carrying `record_error` (a `VectorShortfall` on
    # every query — exactly the #13911 shape) produced
    # `poison: POISON DETECTED` / exit 1, byte-identical to a genuinely detected
    # poison, from a run in which the poison was never exercised on a gated arm.
    # These strings are collected into BOTH lists, so the status below can tell
    # "the corruption was seen" from "the instrument could not look".
    unmeasured: list[str] = []
    #: message -> the REQUIRED arm that produced it (`None` for report-level
    #: failures: an oracle too short for `k`, an empty report). `detected` reads
    #: this so a divergence can only certify the arm the poison actually reaches.
    arm_of: dict[str, str | None] = {}
    divergences = 0
    # ⛔ AN ORACLE SHORTER THAN `k` IS NOT A MEASUREMENT AT `k`, and `_validate_args`
    # is not the only place that has to say so. Its refusal counts the corpus
    # BEFORE either store is opened (correct: the run must not seed to discover
    # it cannot measure), but the module's own rule — stated on the empty-report
    # guard below — is that a guard which only lives at the argument layer is
    # inherited as a green by the next caller of `run()` that skips validation.
    # Two of the three earlier shape refusals ARE mirrored here (`recall is None`,
    # the empty report); this is the third. Measured before it: a `run()` called
    # with a short oracle reported `ids_equal / order=exact / recall=1.0` and
    # `ok: True` — the under-return shape (#13911) this harness exists to expose.
    # `report["k"]` is always set by `run()`; a caller-built report without it is
    # not measured at a width, so this check abstains rather than inventing one.
    width = report.get("k")
    for entry in report["queries"]:
        q = entry["query"]
        truth_ids = entry.get("truth_cosine")
        if width is not None and truth_ids is not None and len(truth_ids) < width:
            missing = (
                f"{q}: the oracle served {len(truth_ids)} rows for --k {width} — "
                f"the answer was compared over a SHORTER list than either leg "
                f"was asked for, so a full-recall parity here measures no width")
            failures.append(missing)
            unmeasured.append(missing)
            arm_of[missing] = None
        # REQUIRED: the arm the migration is moving TO, against the metric the
        # record's index is built with.
        #
        # ⛔ ONE DECISION FUNCTION, not a hand-written `if` per arm. This arm used
        # to append the "diverged" failure EVEN WHEN THE LEG HAD RAISED (the
        # `record_error` branch above appends its own message, and then this one
        # fired too on the empty answer `run()` substitutes), so the second
        # message landed in `failures` but not in `unmeasured` — and the round-7
        # `detected = any(f … not in unmeasured)` read it as a MEASURED
        # divergence. Measured on the reachable state a raised record leg actually
        # produces (`ids_equal False, recall 0.0, distance.ok None`):
        # `--poison id|order|recall` all printed `POISON DETECTED` and exited 1
        # with the poison never exercised on a gated arm — the exact
        # self-certification round 7 set out to close, one door over, and its pin
        # could not see it because the fixture paired the error with a GREEN arm.
        mine, measured = _arm_failure(
            q, "record", entry["record"], oracle="truth_cosine",
            error=entry.get("record_error"))
        if mine:
            failures.append(mine)
            arm_of[mine] = "record"
            if not measured:
                unmeasured.append(mine)
        # REQUIRED, as the COMPARATOR'S OWN sanity check: the graph scan must
        # reproduce its own oracle. If it does not, the harness's distance
        # inversion or its eligibility predicate is wrong and every other column
        # here is unverified.
        mine, measured = _arm_failure(
            q, "graph_scan", entry["graph_scan_vs_euclidean"],
            oracle="truth_euclidean")
        if mine:
            failures.append(mine)
            arm_of[mine] = "graph_scan"
            if not measured:
                unmeasured.append(mine)
        divergences += len(entry["metric_divergence_ids"])

    # ⛔ AN EMPTY REPORT IS NOT PARITY. `_validate_args` refuses `--queries < 1`,
    # so this is not reachable through `main()` today — but the guard belongs in
    # the verdict, not only at the argument layer, or the next caller of `run()`
    # that skips validation inherits "zero measurements → green".
    if not report["queries"]:
        why = "no queries were measured — an empty report is not parity"
        failures.append(why)
        unmeasured.append(why)
        arm_of[why] = None

    ok = not failures
    if poisoned:
        # The falsifier: a poisoned run MUST fail. A poisoned run that reports
        # parity is a BLIND harness, and saying so is the only useful output it
        # has — so the verdict is red either way, and `poison_status` records
        # WHICH reading it produced.
        #
        # ⛔ THREE READINGS, NOT TWO. "Detected" is a claim about the
        # INSTRUMENT — this comparison CAN see this corruption — so it may only
        # be minted by a failure that came from a MEASURED arm. A run whose only
        # failures are cannot-measure ones saw nothing and must say so; folding
        # it into "detected" is how a falsifier certifies itself from a run that
        # never looked (and it is the reading the repro recipe in the README's
        # §6 collapses: one `echo exit=$?` cannot tell the three apart, so the
        # codes now differ too — see `main`).
        # ⛔ AND THE FAILURE MUST COME FROM THE ARM THE POISON REACHES. The
        # `not in unmeasured` filter above answers "was this MEASURED"; it does not
        # answer "was it measured ON THE THING THE POISON MOVED". `--poison
        # id|order|recall` only ever rewrites the RECORD candidate list, so a
        # graph_scan divergence (a real, measured failure) satisfied the old
        # predicate and printed `POISON DETECTED` for a poison the record arm had
        # not moved — the round-7 self-certification one door over. `POISON_REACH`
        # is the mapping, and both arms are represented in it so the scoping can
        # never collapse into "any measured failure" again.
        detected = any(arm_of.get(f) in POISON_REACH[poisoned]
                       for f in failures if f not in unmeasured)
        if detected:
            status = "POISON DETECTED"
        elif unmeasured:
            # ⛔ THE CANNOT-MEASURE READING IS KEYED ON THE CANNOT-MEASURE FAILURES,
            # not on `failures` being non-empty: a MEASURED divergence on an arm the
            # poison does not reach is neither a detection (nothing proves this
            # corruption is visible) nor an absence of measurement — and calling it
            # "UNMEASURED" would say the instrument could not look when it did look,
            # at the wrong thing. That state falls through to BLIND below, with the
            # other arm's divergence still printed in `failures`.
            status = ("POISON UNMEASURED — the run could not measure the arms "
                      "the poison would have to move")
        else:
            status = "POISON NOT DETECTED — HARNESS BLIND"
        verdict = {"ok": False, "poison": poisoned,
                   "poison_status": status,
                   "failures": failures or [
                       "poison produced no divergence — the comparison is blind"],
                   "informational": {"metric_divergence_ids_total": divergences}}
        if not detected and unmeasured:
            # ⛔ PRESENT ONLY IN THE STATE IT DESCRIBES. `main` reads it to choose
            # exit **2** (cannot run) over 1 (a comparison diverged), so a caller's
            # exit code tells the three readings apart and not only the printed
            # word. It is OMITTED from a measured run so the committed artifacts
            # still replay bit-identically through this verdict — an always-present
            # key would silently re-stale five pieces of evidence that no live
            # re-run is needed to keep true.
            verdict["unmeasured"] = True
        return verdict
    verdict = {"ok": ok, "failures": failures,
               "informational": {"metric_divergence_ids_total": divergences,
                                   "metric_divergence_note":
                                       "ids the graph SCAN (euclidean, production "
                                       "today on an index-less store) ranks "
                                       "differently from the canonical cosine "
                                       "metric — informational, not a gate"}}
    if not ok and failures and all(f in unmeasured for f in failures):
        # ⛔ THE SAME CONFLATION, ONE BRANCH OVER. The `unmeasured` key was only
        # ever set on the POISONED branch, so an ordinary run whose every failure
        # was a cannot-measure one exited **1** — byte-identical to "a required
        # comparison DIVERGED", which is a claim about a measurement this run never
        # made. `main` reads this key to choose exit 2 (cannot run); the key stays
        # ABSENT on a measured run so the committed artifacts replay bit-identically.
        verdict["unmeasured"] = True
    return verdict


def _queries(rows: list[Row], count: int) -> list[tuple[str, list[float]]]:
    import numpy as np

    rng = np.random.default_rng(7919)
    out: list[tuple[str, list[float]]] = [("q_anchor", _unit(0)),
                                          ("q_tie", list(rows[0].embedding))]
    for i in range(max(0, count - len(out))):
        vec = rng.normal(size=DIM).astype(float)
        vec *= float(rng.uniform(0.35, 2.5))
        out.append((f"q{i:02d}", vec.tolist()))
    return out[:count]


# ── CLI ─────────────────────────────────────────────────────────────────────

def _validate_args(args) -> None:
    """Refuse argument shapes that cannot measure, BEFORE either store is touched.

    ⛔ Three shapes used to reach a crash or a false green (measured in review):
    `--n-random 0` raised `IndexError` at `build_corpus`'s `rows[0]`; `--queries 0`
    raised `IndexError` at `queries[0]` after BOTH stores had been seeded; `--k 0`
    asked each leg for `LIMIT 0` and reported PARITY over an empty comparison.

    A fourth is a SHAPE rather than a scalar: a corpus too small to fill `k` (see
    the eligibility count below) reported full-recall parity at an answer width
    neither store was asked for — the under-return class this harness exists to
    expose, silently green. Every refusal here runs BEFORE either store is opened.
    """
    if not args.graph_port:
        raise CannotRun(
            "no --graph-port and no TORTOISE_TEST_DOCKER_PORT — start one with "
            "`uv run python tools/test_lane.py uri`")
    if not args.record_dsn:
        raise CannotRun(
            "no --record-dsn / TEST_RECORD_DB_URI — a one-sided run is not a "
            "parity measurement")
    if args.k < 1:
        raise CannotRun(f"--k {args.k}: limitting to zero compares nothing")
    if args.queries < 1:
        raise CannotRun("--queries 0: no query is measured")
    if args.n_random < 4:
        # The tie group is built FROM `rows[0]`, so the corpus needs at least one
        # random row to derive it from.
        raise CannotRun(
            f"--n-random {args.n_random}: the tie group is derived from the "
            f"first random row, so fewer than 4 leaves nothing to compare")
    # ⛔ A CORPUS TOO SMALL TO FILL `k` IS NOT A PARITY MEASUREMENT, and the
    # `--n-random 4` floor above is NOT the check for it — it is a proxy that
    # happened to be true for the shipped defaults. `--n-random 4 --k 10` seeds
    # 8 eligible rows; `truth()` then returns 8, `compare()` reads `k` only to
    # default it and never validates the width it was handed, so both legs report
    # `ids_equal / order=exact / recall=1.0` over an answer the run never asked
    # for and `_verdict` returns `ok: True` — the exact under-return shape
    # (#13911) this harness exists to EXPOSE, printed as PARITY. Measured in
    # review: `n_random=4` → 8 eligible rows → `_verdict(...)["ok"] is True`,
    # with `graph_index_shortfall` computing the 2 and gating nothing.
    #
    # Counted from `build_corpus` (pure, no store) so the refusal lands BEFORE
    # either store is opened — the same reason the other shapes are here. The
    # tenant filter restates `truth()`'s own `tenant == "ours"` and `DIM` is every
    # query vector's width (`_queries` builds each one at DIM), so this count is
    # the population `truth()` will score.
    eligible = sum(1 for row in build_corpus(args.seed, args.n_random)
                   if row.tenant == "ours" and row.eligible(DIM))
    if eligible < args.k:
        raise CannotRun(
            f"--k {args.k} over a corpus that serves only {eligible} eligible "
            f"rows: the oracle returns {eligible}, so recall/order would be "
            f"measured over a SHORTER answer than either store was asked for "
            f"and a full-recall parity would be reported at a width the run "
            f"never checks. Raise --n-random or lower --k")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record-dsn", default=os.environ.get("TEST_RECORD_DB_URI")
                    or os.environ.get("TORTOISE_RECORD_DSN"))
    ap.add_argument("--graph-host", default="localhost")
    ap.add_argument("--graph-port", type=int,
                    default=int(os.environ.get("TORTOISE_TEST_DOCKER_PORT",
                                               "0") or 0) or None)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--queries", type=int, default=6)
    ap.add_argument("--n-random", type=int, default=48)
    ap.add_argument("--seed", type=int, default=13906)
    ap.add_argument("--ef-diagnostic", type=int, default=200,
                    help="the efRuntime the informational index diagnostic "
                         "re-creates the graph index with (default 200; "
                         "FalkorDB's own default is 10)")
    ap.add_argument("--poison", choices=("id", "order", "recall", "vector"),
                    default=None,
                    help="corrupt the candidate side on purpose; a run that "
                         "still reports parity is a BLIND harness")
    ap.add_argument("--allow-remote-writes", action="store_true",
                    help="permit seeding a NON-loopback store; this harness "
                         "writes (org/graph/point rows) and only its cleanup "
                         "makes that transient")
    ap.add_argument("--keep", action="store_true",
                    help="leave the seeded tenant/graph behind (debugging)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    try:
        _validate_args(args)
        report = run(args)
    except CannotRun as e:
        print(f"cannot run: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001, RUF100 — see the docstring below
        # ⛔ A DRIVER OR SETUP FAILURE MUST NOT EXIT 1. A missing `psycopg2`, an
        # unreachable Postgres, a refused role, a seeding/teardown/index error —
        # each used to escape as a traceback and exit **1**, which this module's
        # table defines as "a comparison diverged". The falsifier's own loop
        # (`--poison id; echo exit=$?`) would then read `exit=1` as "poison
        # confirmed" having measured nothing. The comparison itself never raises
        # for a divergence (it returns a report), so ANY exception here means the
        # run could not measure.
        print(f"cannot run: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2, default=str)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")

    v = report["verdict"]
    print(f"=== #13906 vector parity — k={args.k} "
          f"queries={len(report['queries'])} poison={args.poison} ===")
    for entry in report["queries"]:
        print(f"  {entry['query']}: "
              f"record recall={entry['record']['recall']:.3f} "
              f"order={entry['record']['order']} | "
              f"graph_index recall={entry['graph_index']['recall']:.3f} "
              f"order={entry['graph_index']['order']} | "
              f"graph_scan(euc) recall="
              f"{entry['graph_scan_vs_euclidean']['recall']:.3f} | "
              f"scan-vs-cosine recall="
              f"{entry['graph_scan_vs_cosine']['recall']:.3f}")
    print(f"informational: {v['informational']}")
    if v.get("poison_status"):
        print(f"poison: {v['poison_status']}")
    if v["ok"]:
        # ⛔ THE HEADLINE NAMES ITS GATE. "the two legs agree" would be broader
        # than what was checked: the required arms are each leg against ITS OWN
        # oracle (record↔cosine, graph scan↔euclidean). The record-vs-graph-index
        # column is reported and NOT gated (that leg under-returns — #13911), so
        # a reader must not read exit 0 as "the stores return the same thing".
        print("PARITY: record matches the cosine oracle and graph_scan matches "
              "the euclidean oracle (ids, order, recall@k, distances)")
        print("informational, NOT gated: record-vs-graph_index recall="
              f"{[e['record_vs_graph_index']['recall'] for e in report['queries']]}"
              " — the replaced leg's under-return (#13911); and the "
              "scan-vs-cosine metric drift (#13912)")
        return 0
    for f in v["failures"]:
        print(f"  FAIL {f}", file=sys.stderr)
    print("PARITY: FAILED", file=sys.stderr)
    if v.get("unmeasured"):
        # ⛔ THE FALSIFIER'S EXIT CODE IS EVIDENCE, so it must not conflate the
        # three readings. `--poison` + exit **1** says "the corruption was SEEN on
        # a measured arm"; exit **2** says "this run could not measure what the
        # poison would have to move", which is the same class as every other
        # cannot-run — and a repro loop that ticks `exit=1` as "confirmed" would
        # otherwise certify the harness from a run that never looked. The same
        # holds for an UNPOISONED run: exit 1 there claims a divergence, and a
        # cannot-measure run is not one.
        if v.get("poison"):
            print("cannot run: the poison was NOT measured (see the failures "
                  "above) — this run says nothing about the harness's blindness",
                  file=sys.stderr)
        else:
            print("cannot run: NOTHING was measured (see the failures above) — "
                  "a cannot-measure failure is not a divergence, so this run is "
                  "not a parity verdict", file=sys.stderr)
        return 2
    if (v.get("poison_status") or "").startswith("POISON NOT DETECTED"):
        # ⛔ A BLIND HARNESS IS NOT A CONFIRMED ONE. Everything measured and the
        # poison moved nothing: that is a verdict about the INSTRUMENT, and it must
        # not share the code §6's one-bit recipe reads as "the poison was caught".
        print("blind: every arm measured and the poison moved NOTHING — the "
              "comparison cannot see this corruption; it is NOT confirmed",
              file=sys.stderr)
        return 3
    return 1


if __name__ == "__main__":
    sys.exit(main())
