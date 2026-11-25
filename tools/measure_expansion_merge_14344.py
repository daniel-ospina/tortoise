"""#14344 — measure what the two MERGE RULES do to evidence, at the rank level.

**What this measures, stated first because it is easy to over-read.** This is
NOT a recall@k figure on a query set. It is a measurement of the MERGE RULE that
joins an additive second retrieval pass to the leg it expands
(:func:`tortoise.expansion_merge.fuse_expansion_pass`, and the rank concatenation
it replaced). Both rules always return the same candidate SET — the union — so
the only thing they can differ about is ORDER, and order is what a page cut
consumes. The measurement therefore reports, over seeded randomised pools:

  * ``pass1_retained@k``    — how many pass-1 ids (repeat or not) survive in the
                              first ``k`` of the merged leg — the leg's own page;
  * ``pass1_only_retained@k`` — the same count restricted to ids the expansion
                              did NOT repeat, i.e. the mirror of
                              ``new_admitted``. Both are reported because only
                              the second can distinguish "kept what we had" from
                              "kept what we both found";
  * ``new_admitted@k``      — how many ids ONLY the expansion found reach the
                              first ``k`` (does the lever still ADD?);
  * ``pass1_rank0_position`` — where pass 1's own rank-0 row lands in the
                              merged leg. This is the displacement made visible.
                              Under the concatenation the row goes to position
                              ``len(expanded)`` when the expansion did NOT
                              repeat it, and keeps whichever position the
                              expansion gave its OWN copy when it did — so the
                              old arm's mean sits BELOW ``len(expanded)`` in
                              every shape (see the receipt's ``old`` column),
                              never equal to it. Under the fusion the row is
                              bounded by how many candidates out-rank it.

**Why this and not the capstone set.** `docs/evidence/12186-capstone/queries.json`
is a single-hop NAME set: 300 harvested object names, one canonical `:Object`
row each, no sessions, no timestamps, no points. Two consequences, both measured
in this lane and recorded in the PR:

  1. ``recall_all@5`` — "every evidence session present" — has nothing to be
     plural about: there is exactly ONE gold row per query, so the metric
     degenerates to recall@5. The multi-session instrument is the n=71 cohort
     (`tools/longmem_eval/build_cohorts.py`, cohort ``ms_tail``), whose corpus is
     NOT committed ("a 49 MB slice is not committed"), so it cannot be run here.
  2. The C2 lever measured by this merge is POINT-scoped
     (``sdk.py``: ``entity_key_expansion and entity_type == "point"``), and the
     capstone harness builds an ``:Object`` population — so arming the lever at
     that door is a structural no-op, not a measurement of 0.

Run::

    TORTOISE_TEST_CARVE_OUT=1 uv run python tools/measure_expansion_merge_14344.py

Writes ``docs/evidence/14344-expansion-fusion/merge-rule-measurement.json``
(``--out`` to redirect). Deterministic: one seed, no wall-clock input.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would raise first.
if sys.version_info < (3, 12):  # noqa: UP036 — deliberate RUNTIME guard
    raise SystemExit(
        f"tools/measure_expansion_merge_14344.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/measure_expansion_merge_14344.py`")

import argparse
import json
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Deliberately NO inline suppression on this import: ruff does not flag it, so
# the E402 directive this file used to carry was reported UNUSED by RUF100 —
# which is why `agent-infra-ci / lint` was red on this PR's earlier head.
from tortoise.expansion_merge import fuse_expansion_pass

DEFAULT_OUT = Path("docs/evidence/14344-expansion-fusion/merge-rule-measurement.json")
SEED = 14344
TRIALS_PER_SHAPE = 500
#: The leg pages the product cuts at. ``_tortoise_fts_query`` applies its own
#: ``limit`` to the fused result, and the reader window is smaller still, so a
#: row past these positions is a row the answer never shows.
KS = (5, 10)
#: (pass-1 length, expansion length, shared-id fraction). The shapes span a
#: narrow page with a wide expansion (the starvation case the lever exists for)
#: and the symmetric case where both passes see the same size of pool.
SHAPES = (
    (10, 10, 0.2),
    (10, 20, 0.2),
    (20, 10, 0.2),
    (10, 10, 0.6),
    (5, 20, 0.2),
)


def _old_concatenation(pass1: list[tuple[str, float]],
                       expanded: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """The rule this change replaces, kept here as the counterfactual.

    ⛔ Never call this from product code. It is reproduced VERBATIM from the
    three additive passes it used to sit in (``sdk._entity_key_expansion_pass``,
    ``sdk._entity_key_dense_pass``, ``sdk._search_keys_prf_expansion``), so the
    measurement is of the shipped code and not of a paraphrase.
    """
    seen = {pid for pid, _score in expanded}
    merged = list(expanded)
    merged.extend((pid, s) for pid, s in pass1 if pid not in seen)
    return merged


def _pools(rng: random.Random, n_pass1: int, n_expanded: int,
           shared_fraction: float) -> tuple[list[tuple[str, float]],
                                            list[tuple[str, float]]]:
    """Two ranked pools over one id universe, sharing ``shared_fraction`` of the
    smaller pool. Scores descend with rank, as a real leg's do — the merge rules
    below are rank-based, so the score VALUES are irrelevant to them and are
    carried only to keep the shapes honest."""
    ids = [f"id{i:03d}" for i in range(n_pass1 + n_expanded)]
    rng.shuffle(ids)
    shared_n = round(min(n_pass1, n_expanded) * shared_fraction)
    shared = ids[:shared_n]
    pass1_ids = shared + ids[shared_n:shared_n + (n_pass1 - shared_n)]
    rest = ids[shared_n + (n_pass1 - shared_n):]
    expanded_ids = shared + rest[: n_expanded - shared_n]
    rng.shuffle(pass1_ids)
    rng.shuffle(expanded_ids)
    return ([(pid, 1.0 - i / 1000) for i, pid in enumerate(pass1_ids)],
            [(pid, 1.0 - i / 1000) for i, pid in enumerate(expanded_ids)])


def _score_rule(merged: list[tuple[str, float]], pass1_ids: set[str],
                expanded_ids: set[str], k: int,
                pass1_rank0: str) -> dict:
    """Per-trial counters. ``pass1_rank0_position`` is the discriminating one:
    it is measured over the WHOLE merged leg, so it cannot saturate the way a
    "did the top row move" boolean does (under both rules the merged head is
    dominated by whatever that rule puts first, which is why a boolean there
    would read 0.0 for both arms and measure nothing).

    ⛔ Every key returned here is one `measure()` CONSUMES. Two keys that merely
    restated ``pass1_only_retained`` / ``new_admitted`` under a second name sat
    here unused (found in #14619's review): a counter nothing reads is not a
    measurement, and it invites a reader to think it was reported."""
    head = [pid for pid, _s in merged[:k]]
    positions = [pid for pid, _s in merged]
    return {
        "pass1_retained": sum(1 for pid in head if pid in pass1_ids),
        "pass1_only_retained": sum(1 for pid in head
                                   if pid in pass1_ids
                                   and pid not in expanded_ids),
        "new_admitted": sum(1 for pid in head if pid in expanded_ids
                            and pid not in pass1_ids),
        "pass1_rank0_position": positions.index(pass1_rank0),
    }


def measure(trials_per_shape: int = TRIALS_PER_SHAPE,
            seed: int = SEED) -> dict:
    """Run every shape under both merge rules and aggregate per ``k``."""
    rng = random.Random(seed)
    shapes: dict[str, dict] = {}
    totals = {
        "old": {k: {"pass1_retained": 0, "pass1_only_retained": 0,
                    "new_admitted": 0, "pass1_rank0_position": 0, "n": 0}
                for k in KS},
        "new": {k: {"pass1_retained": 0, "pass1_only_retained": 0,
                    "new_admitted": 0, "pass1_rank0_position": 0, "n": 0}
                for k in KS},
    }
    for n_pass1, n_expanded, shared in SHAPES:
        key = f"pass1={n_pass1},expanded={n_expanded},shared={shared}"
        per_shape = {arm: {k: {"pass1_retained": 0, "pass1_only_retained": 0,
                               "new_admitted": 0, "pass1_rank0_position": 0,
                               "n": 0} for k in KS}
                     for arm in ("old", "new")}
        for _ in range(trials_per_shape):
            pass1, expanded = _pools(rng, n_pass1, n_expanded, shared)
            pass1_ids = {pid for pid, _s in pass1}
            expanded_ids = {pid for pid, _s in expanded}
            for arm, merged in (("old", _old_concatenation(pass1, expanded)),
                                ("new", fuse_expansion_pass(pass1, expanded))):
                # the invariant both rules must keep, checked per trial
                assert {pid for pid, _s in merged} == pass1_ids | expanded_ids, (
                    "a merge dropped or invented a candidate — the additive "
                    "contract is the one thing neither rule may break")
                assert len([1 for pid, _s in merged]) == len(
                    {pid for pid, _s in merged})
                for k in KS:
                    row = _score_rule(merged, pass1_ids, expanded_ids, k,
                                      pass1[0][0])
                    for field in ("pass1_retained", "pass1_only_retained",
                                  "new_admitted", "pass1_rank0_position"):
                        per_shape[arm][k][field] += row[field]
                        totals[arm][k][field] += row[field]
                    per_shape[arm][k]["n"] += 1
                    totals[arm][k]["n"] += 1
        shapes[key] = per_shape

    def _rate(arm: str, k: int, field: str) -> float:
        cell = totals[arm][k]
        return round(cell[field] / cell["n"], 4) if cell["n"] else 0.0

    summary = {
        f"k={k}": {
            "pass1_retained_mean": {"old": _rate("old", k, "pass1_retained"),
                                    "new": _rate("new", k, "pass1_retained")},
            "pass1_only_retained_mean": {
                "old": _rate("old", k, "pass1_only_retained"),
                "new": _rate("new", k, "pass1_only_retained")},
            "new_admitted_mean": {"old": _rate("old", k, "new_admitted"),
                                  "new": _rate("new", k, "new_admitted")},
            "pass1_rank0_position_mean": {
                "old": _rate("old", k, "pass1_rank0_position"),
                "new": _rate("new", k, "pass1_rank0_position")},
        }
        for k in KS
    }
    return {
        "issue": "14344",
        "what_this_is": (
            "the MERGE RULE's effect on evidence retained in the leg's first k "
            "positions, over seeded randomised pools — NOT a recall@k figure on "
            "any query set, and not comparable to "
            "docs/evidence/12186-capstone/measurement.json"),
        "seed": seed,
        "trials_per_shape": trials_per_shape,
        "shapes": [f"pass1={a},expanded={b},shared={c}" for a, b, c in SHAPES],
        "ks": list(KS),
        "old_rule": ("expanded + pass-1-only (rank concatenation), as "
                     "sdk._entity_key_expansion_pass / _entity_key_dense_pass "
                     "built it before #14344"),
        "new_rule": ("tortoise/expansion_merge.fuse_expansion_pass -> "
                     "search_engine.rrf_fusion([pass1, expanded])"),
        "note_old_rule_survives": ("A4's sdk._search_keys_prf_expansion still "
                                   "concatenates — a different lever with its "
                                   "own measured arm, deliberately untouched"),
        "summary": summary,
        "per_shape": shapes,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--trials", type=int, default=TRIALS_PER_SHAPE)
    args = p.parse_args(argv)
    receipt = measure(args.trials)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt["summary"], indent=2))
    print(f"\nreceipt: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
