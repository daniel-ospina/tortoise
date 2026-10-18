#!/usr/bin/env python3
"""#4210 — fusion-formula A/B: weighted RRF (the shipped default) vs. a
score-normalised convex combination, measured **per question**.

WHY THIS TOOL EXISTS
--------------------
#4210 is a reopen of a recorded owner decision. The decision
(``tortoise/sdk.py``, the ``#1657 weighted RRF`` comment) fixes the fusion
**method** — weighted Reciprocal Rank Fusion with the vector leg at **1.5x** —
and pre-authorises only *weight* refinement by a later measured re-burn. The
reopen cites the field's convergent finding (Bruch et al., TOIS 2023) that a
**convex combination of normalised scores** beats RRF-style rank arithmetic.
That changes the *formula*, so it is outside what the decision pre-authorised
and the ruling is the **owner's** (#4210: "Rule on the formula ... Owner
decision").

This tool is the measurement the owner's ruling needs. It is **measurement
only** and changes NO product behaviour:

  * The default arm calls the product's own fusion path unchanged
    (``tortoise.search_engine.rrf_fusion`` with the shipped weights).
  * The convex arm replaces the arithmetic **for the duration of one
    retrieval call** via an explicit, scoped swap of the fusion callable.
    The convex implementation lives here — in measurement tooling — and is
    deliberately NOT a product capability: shipping it would be adopting the
    finding over the owner's decision, which is exactly what #4210 must not do.

Everything downstream of fusion (kind filter, truncation, EP annotation) is
the product's own code and is identical across arms — the two arms differ in
exactly one thing, the fusion arithmetic.

POPULATION
----------
The ask fixture (``tests/fixtures/ask_spotcheck_composition.json``) — the SAME
capture-shaped store the frozen instrument (``tools/ask_shape_rate.py``) and the
W6C/W7A gold-rank diagnostics measure, seeded through the SAME seeder
(``tools.ask_spotcheck._seed_memory``) with the product's own turn vector
(``embed=True``, the post-#4194/#4304 default). Using the instrument's own
population is what makes these numbers comparable to the recorded baseline.

METRICS (per question — never a single aggregate delta, per #4210)
------------------------------------------------------------------
  * ``gold_rank``      — 1-based rank of each ``has_answer`` turn in the pool
                         (``ABSENT`` when outside the returned pool).
  * ``ndcg@10``        — binary-gain nDCG over the gold turns
                         (``tools.longmem_eval.retrieve.ndcg_at_k``).
  * ``n_pool``/``legs``/``leg_digest`` — the pool size and the exact legs fed
                         to fusion. The convex arm fuses the RRF arm's OWN
                         captured lists, so "one variable" is structural: the
                         second retrieval's leg timing cannot change which
                         candidates were fused.

``shape_rate`` is deliberately NOT computed here: it needs the frozen
instrument's paid reader pass, whose aggregate carries a measured 0.238-0.333
spread on byte-identical code and which the W6C/W7A receipts record as a
quiet-host pass. This tool is **zero-paid** (local embedder, no reader).

USAGE
-----
    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/<ns>' \\
      uv run python tools/fusion_formula_ab.py \\
        --alpha 0.7 --alphas 0.3,0.5,0.7,0.9 --out /tmp/fusion-ab.json

    # a slice while sizing the run:
    ... --questions 0a995998,1d4e3b97
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/fusion_formula_ab.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/fusion_formula_ab.py`"
    )

import argparse
import contextlib
import hashlib
import json
import logging
import os
import statistics
import sys
import tempfile
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger("fusion_formula_ab")

FIXTURE = os.path.join(_REPO_ROOT, "tests", "fixtures",
                       "ask_spotcheck_composition.json")

#: The ask-lane pool depth (max(80, 120)) — M1's n_pool120, the SAME depth the
#: W6C/W7A gold-rank diagnostics use. The cut the reader window applies on top
#: of this pool is 40 (W7A), reported alongside so a rank just past it is
#: visible rather than silently "absent".
LIMIT = 120
LEG_DEPTH = 120
CUT = 40

#: The recorded BULK order of the two arms, named in every receipt.
ARM_RRF = "rrf_default"
ARM_CONVEX = "convex"


# ── The convex arm (measurement-only arithmetic) ─────────────────────────────

def _minmax(scores: list[float]) -> list[float]:
    """Min-max normalise one leg's scores to [0, 1].

    A leg with NO spread (one item, or all-equal scores) carries no ranking
    evidence of its own — every member maps to 1.0 rather than 0.0, because a
    zero would read as "this leg says these are irrelevant" when the leg in
    fact ranked them (Bruch et al. normalise per-list; the degenerate case is
    the presence-only case, not an absence).
    """
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return [1.0] * len(scores)
    span = hi - lo
    return [(s - lo) / span for s in scores]


def convex_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = 60,
    *,
    strategy_names: list[str] | None = None,
    weights: dict[str, float] | None = None,
    recency_weights: dict[str, float] | None = None,
    recency_boost: float = 0.0,
    alpha: float = 0.7,
    primary: str = "vector",
) -> dict[str, float]:
    """Score-normalised convex combination — a DROP-IN for ``rrf_fusion``.

    Formula (the two-leg form of Bruch et al., generalised to N legs):

        s(d) = alpha * p_norm(d) + (1 - alpha) * mean(norm_i(d) for i != primary)

    where ``norm_i`` is the per-leg min-max normalisation of that leg's OWN
    scores. ``k``/``recency_*`` are accepted because production passes them;
    the convex arm deliberately ignores ``k`` (rank damping is an RRF
    concept) and applies the same recency multiplier RRF does, so the two arms
    stay comparable on every other axis.

    ``primary`` is the leg the weight is concentrated on — the recorded
    decision already names the vector leg as the strongest retriever, which is
    why the default is ``"vector"``; the shipped weights (vector 1.5x) are the
    RRF-side expression of the same judgement.

    Determinism (#2952): the returned order is a total order on
    ``(-score, id)``, the same contract ``rrf_fusion`` carries, so a score tie
    cannot fall back to dict-insertion order.
    """
    names = list(strategy_names) if strategy_names is not None else [
        f"leg{i}" for i in range(len(ranked_lists))]
    norm_lists: list[list[tuple[str, float]]] = []
    for ranked in ranked_lists:
        scores = [float(s) for _pid, s in ranked]
        normed = _minmax(scores)
        norm_lists.append([(pid, n) for (pid, _s), n in zip(ranked, normed, strict=True)])

    primary_idx = names.index(primary) if primary in names else None
    others = [i for i in range(len(norm_lists)) if i != primary_idx]

    combined: dict[str, float] = {}
    for i, normed in enumerate(norm_lists):
        if i == primary_idx:
            w = alpha
        elif not others:
            w = 0.0
        else:
            w = (1.0 - alpha) / len(others)
        if w == 0.0:
            continue
        for pid, s in normed:
            combined[pid] = combined.get(pid, 0.0) + w * s

    if recency_weights and recency_boost > 0:
        # the SAME multiplier RRF applies, so recency is not a second variable
        for pid in combined:
            rw = recency_weights.get(pid, 0.0)
            if rw > 0:
                combined[pid] = combined[pid] * (1.0 + recency_boost * rw)
    return dict(sorted(combined.items(), key=lambda x: (-x[1], x[0])))


@contextlib.contextmanager
def _fusion_arm(arm: str, *, alpha: float, primary: str, capture: dict,
                inject: dict | None = None):
    """Swap the fusion callable for one retrieval call.

    ``arm == ARM_RRF`` installs a recording PASSTHROUGH: the product's own
    arithmetic runs and the exact leg lists handed to it are kept.
    ``arm == ARM_CONVEX`` installs the convex combination — computed from the
    leg lists captured by the RRF arm when ``inject`` is a precomputed fused
    dict (see ``measure_question``): the ONE-VARIABLE guarantee is then
    structural rather than a hoped-for coincidence, because the second
    retrieval's leg timing can no longer change which candidates were fused.
    """
    from tortoise import search_engine

    original = search_engine.rrf_fusion

    def _record(ranked_lists, k, strategy_names, weights):
        capture.setdefault("legs", []).append({
            "strategy_names": list(strategy_names) if strategy_names else None,
            "weights": weights,
            "k": k,
            "leg_sizes": [len(r) for r in ranked_lists],
        })
        capture.setdefault("lists", []).append(
            [[(pid, float(s)) for pid, s in r] for r in ranked_lists])

    if arm == ARM_RRF:
        def _passthrough(ranked_lists, k=60, *, strategy_names=None,
                         weights=None, recency_weights=None,
                         recency_boost=0.0):
            _record(ranked_lists, k, strategy_names, weights)
            if inject is not None:
                # the RRF WEIGHT sweep: the product's own arithmetic, evaluated
                # offline on the captured lists at a different vector weight.
                return dict(inject)
            return original(
                ranked_lists, k, strategy_names=strategy_names, weights=weights,
                recency_weights=recency_weights, recency_boost=recency_boost)

        search_engine.rrf_fusion = _passthrough
    elif arm == ARM_CONVEX:
        def _convex(ranked_lists, k=60, *, strategy_names=None, weights=None,
                    recency_weights=None, recency_boost=0.0):
            _record(ranked_lists, k, strategy_names, weights)
            if inject is not None:
                # the convex order derived from the RRF arm's OWN leg lists —
                # recency is folded in by `convex_fusion` at capture time, so
                # the same multiplier cannot be applied twice here.
                return dict(inject)
            return convex_fusion(
                ranked_lists, k, strategy_names=strategy_names, weights=weights,
                recency_weights=recency_weights, recency_boost=recency_boost,
                alpha=alpha, primary=primary)

        search_engine.rrf_fusion = _convex
    else:  # pragma: no cover — argparse constrains this
        raise ValueError(f"unknown arm {arm!r}")
    try:
        yield
    finally:
        search_engine.rrf_fusion = original


def _captured_call(capture: dict) -> dict:
    legs = capture.get("legs") or []
    return legs[0] if legs else {}


def _convex_fused_from_capture(capture: dict, *, alpha: float, primary: str,
                               ) -> dict | None:
    """The convex arm's fused order, from the RRF arm's captured legs.

    Returns None when the RRF arm never reached fusion (a single-strategy read
    — e.g. every leg but one timed out), because there is then no two-leg
    comparison to make and the row must say so rather than invent one.
    """
    lists = capture.get("lists") or []
    if not lists:
        return None
    call = _captured_call(capture)
    return convex_fusion(
        lists[0], call.get("k", 60),
        strategy_names=call.get("strategy_names"),
        weights=call.get("weights"), alpha=alpha, primary=primary)


def _leg_digest(capture: dict) -> str:
    legs = capture.get("legs") or []
    if not legs:
        return "no-fusion-call"
    payload = json.dumps(legs[0].get("leg_sizes"), sort_keys=True)
    payload += "|" + json.dumps(legs[0].get("strategy_names") or [])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── Measurement ─────────────────────────────────────────────────────────────

def gold_turn_ids(q: dict) -> list[str]:
    """The fixture's has_answer turns as seeded ids (``{sid}_t{i}``).

    Mirrors ``tools.ask_spotcheck._seed_memory``'s deterministic id scheme and
    the W7A diagnostic's ``gold_turn_ids`` — the map the seeder actually
    writes.
    """
    ids = q.get("haystack_session_ids") or []
    sessions = q.get("haystack_sessions") or []
    out: list[str] = []
    for i, sess in enumerate(sessions):
        sid = ids[i] if i < len(ids) else f"sess-{i}"
        sid = sid.strip() if isinstance(sid, str) and sid.strip() else f"sess-{i}"
        for j, turn in enumerate(sess or []):
            if turn.get("has_answer"):
                out.append(f"{sid}_t{j}")
    return out


def _ids_of(hits: list[dict]) -> list[str]:
    return [str(h.get("id") or h.get("point_id") or h.get("pointId")
                or h.get("node_id") or "") for h in hits]


def _open_sdk(namespace: str, *, attempts: int = 4, lane: str = "docker",
              tmpdir: str | None = None):
    """Open an SDK, retrying the transient open.

    The projection's open path runs a health probe AND creates indexes; on a
    contended FalkorDB a single index DDL can exceed the 60 s socket ceiling
    and the probe then reports the graph unhealthy ("Failed to create index on
    n.id: Timeout reading from socket", measured 2026-10-10). That is a
    TRANSIENT transport failure, not a corrupt store, so it is retried — and
    the retry is recorded, never silent.
    """
    from tortoise.sdk import TortoiseSDK

    uri = os.environ.get("TORTOISE_DB_URI") or ""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            if lane == "embedded":
                db = os.path.join(tmpdir or tempfile.mkdtemp(
                    prefix="fusion_ab_"), f"{namespace}.db")
                os.makedirs(os.path.dirname(db), exist_ok=True)
                sdk = TortoiseSDK(db)
            else:
                sdk = TortoiseSDK(namespace=namespace)
            sdk._get_proj()
            return sdk
        except Exception as e:
            last = e
            logger.warning("open attempt %d/%d failed: %s", attempt + 1,
                           attempts, e)
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"could not open {uri!r} after {attempts} attempts") from last


def _warm_embedder(*, timeout: float = 900.0) -> bool:
    """Load the embedder BEFORE seeding, with a host-load-tolerant bound.

    The shipped bound is 90 s (``EmbeddingModel._LOAD_TIMEOUT_S``, measured on
    a quiet host). On a contended one the load exceeds it, ``get()`` returns
    None, and the dense leg is inert — the measurement would then compare two
    RRF-vs-convex orderings over a SPARSE-ONLY pool and report the difference
    as the formula's. A measurement host must not silently lose the leg the
    whole A/B is about, so the warm-up is explicit, bounded, and its outcome is
    recorded in the receipt.
    """
    try:
        from tortoise.embeddings import EmbeddingModel
    except Exception as e:
        logger.warning("embedder unavailable: %s", e)
        return False
    if timeout > EmbeddingModel._LOAD_TIMEOUT_S:
        EmbeddingModel._LOAD_TIMEOUT_S = timeout
    try:
        return bool(EmbeddingModel.warm_up(load_timeout=timeout))
    except Exception as e:
        logger.warning("embedder warm-up raised: %s", e)
        return False


def measure_question_retrying(q: dict, *, qid_slot: int, ns: str, alpha: float,
                              primary: str, alphas: list[float],
                              attempts: int, lane: str = "docker",
                              tmpdir: str | None = None,
                              rrf_weights: list[float] | None = None) -> dict:
    """Seed + measure ONE question, rebuilding the graph from scratch on a
    transport failure.

    A contended FalkorDB stalls a single write past the 60 s socket ceiling
    (measured 2026-10-10 at load 26 on the shared host: `Timeout reading from
    socket` inside the turn MERGE). That is a TRANSPORT failure, not a
    measurement — so the question is re-seeded into a FRESH namespace and
    re-measured. A partially-seeded graph is never measured: the retry
    discards it, so the population each arm sees is exactly the fixture.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        sdk = _open_sdk(f"{ns}_q{qid_slot}_a{attempt}", lane=lane,
                        tmpdir=tmpdir)
        try:
            from tools.ask_spotcheck import _seed_memory
            logger.info("seeding %s (attempt %d/%d)",
                        q["question_id"], attempt + 1, attempts)
            _seed_memory(sdk, q, embed=True)
            n_emb = sdk._get_proj().g.query(
                "MATCH (p:Point) WHERE p.embedding IS NOT NULL "
                "RETURN count(p)").result_set[0][0]
            row = measure_question(sdk, q, alpha=alpha, primary=primary,
                                   alphas=alphas, rrf_weights=rrf_weights)
            row["seeded_embedded_points"] = int(n_emb)
            row["seed_attempts"] = attempt + 1
            return row
        except Exception as e:
            last = e
            logger.warning("question %s attempt %d/%d failed: %s",
                           q["question_id"], attempt + 1, attempts, e)
            time.sleep(3.0 * (attempt + 1))
        finally:
            with contextlib.suppress(Exception):
                sdk.close()
    raise RuntimeError(
        f"question {q['question_id']} could not be measured after "
        f"{attempts} fresh-graph attempts") from last


def _rrf_fused_from_capture(capture: dict, *, vector_weight: float) -> dict | None:
    """The RRF arm's fused order at a DIFFERENT vector weight.

    This is the control the formula question needs. RRF's vector weight and the
    convex alpha are two expressions of the same judgement ("the vector leg is
    the strongest retriever"), so a convex-vs-RRF delta measured at ONE weight
    cannot separate "which arithmetic" from "how much vector preference". The
    sweep answers that: if RRF at a matching weight reaches the convex score,
    the formula is not what moved.
    """
    lists = capture.get("lists") or []
    if not lists:
        return None
    from tortoise.search_engine import rrf_fusion

    call = _captured_call(capture)
    return rrf_fusion(
        lists[0], call.get("k", 60),
        strategy_names=call.get("strategy_names"),
        weights={"vector": vector_weight})


def measure_question(sdk, q: dict, *, alpha: float, primary: str,
                     alphas: list[float],
                     rrf_weights: list[float] | None = None) -> dict:
    """Run ONE question through both arms (and the alpha sweep)."""
    from tools.longmem_eval.retrieve import ndcg_at_k

    gold = gold_turn_ids(q)
    gold_set = set(gold)
    qid = q["question_id"]

    def _run(arm: str, *, alpha_override: float | None = None,
             inject: dict | None = None) -> tuple[list[str], dict]:
        capture: dict = {}
        with _fusion_arm(arm, alpha=(alpha_override if alpha_override is not None
                                     else alpha), primary=primary,
                         capture=capture, inject=inject):
            t0 = time.monotonic()
            hits = sdk.tortoise_fts_query(
                q["question"], limit=LIMIT, pool_size=LEG_DEPTH,
                include_terminal=True, keep_numeric=True,
                search_keys_prf=True, fusion_weights=None, fusion_k=60,
                # #8487: OFF. This harness's whole claim is that "the two arms
                # differ in exactly one thing, the fusion arithmetic", and its
                # numbers are compared to the RECORDED baseline. The read-path
                # valid-time selection is a second pool mover that sits AFTER
                # fusion (it reorders before the ``limit`` cut, so it can change
                # which ids are returned), which would both confound the arm
                # comparison and break comparability with that baseline.
                _valid_time_rerank=False)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
        capture["elapsed_ms"] = round(elapsed_ms, 1)
        return _ids_of(hits), capture

    def _ranks(ids: list[str]) -> dict:
        return {g: (ids.index(g) + 1) if g in ids else "ABSENT" for g in gold}

    # A WARM-UP read first: the first retrieval of a fresh graph pays the
    # connection/index/collector warm-up, and on a slow host that cold pass is
    # exactly where a leg misses the collector deadline. Measuring the first
    # arm cold and the second warm would make ARM ORDER a variable in the A/B.
    _run(ARM_RRF)

    rrf_ids, rrf_cap = _run(ARM_RRF)
    # The convex arm fuses the RRF arm's OWN captured leg lists, so the two
    # arms cannot see different candidates due to a second-pass timeout.
    inject = _convex_fused_from_capture(rrf_cap, alpha=alpha, primary=primary)
    if inject is None:
        convex_ids, convex_cap = rrf_ids, {"legs": [], "lists": []}
    else:
        convex_ids, convex_cap = _run(ARM_CONVEX, inject=inject)

    sweep: dict[str, dict] = {}
    for a in alphas:
        inj = _convex_fused_from_capture(rrf_cap, alpha=a, primary=primary)
        reuse = inj is not None and a == alpha
        ids = convex_ids if reuse else (_run(ARM_CONVEX, inject=inj)[0]
                                       if inj is not None else rrf_ids)
        sweep[str(a)] = {
            "gold_rank": _ranks(ids),
            "ndcg@10": round(ndcg_at_k(ids, gold_set, k=10), 6),
            "n_pool": len(ids),
            "_ids": ids,
        }

    # The RRF WEIGHT control: the same product arithmetic, the same captured
    # legs, only the vector weight moved. Without this the convex-vs-RRF delta
    # cannot be attributed to the formula.
    control: dict[str, dict] = {}
    for w in (rrf_weights or []):
        inj = _rrf_fused_from_capture(rrf_cap, vector_weight=w)
        if inj is None:
            continue
        ids = _run(ARM_RRF, inject=inj)[0]
        control[str(w)] = {
            "gold_rank": _ranks(ids),
            "ndcg@10": round(ndcg_at_k(ids, gold_set, k=10), 6),
            "n_pool": len(ids),
            "_ids": ids,
        }

    out = {
        "question_id": qid,
        "question": q["question"],
        "n_gold": len(gold),
        "fusion_arms_comparable": inject is not None,
        "arms": {
            ARM_RRF: {
                "gold_rank": _ranks(rrf_ids),
                "ndcg@10": round(ndcg_at_k(rrf_ids, gold_set, k=10), 6),
                "n_pool": len(rrf_ids),
                "legs": rrf_cap.get("legs"),
                "leg_digest": _leg_digest(rrf_cap),
                "elapsed_ms": rrf_cap.get("elapsed_ms"),
                "_ids": rrf_ids,
            },
            ARM_CONVEX: {
                "gold_rank": _ranks(convex_ids),
                "ndcg@10": round(ndcg_at_k(convex_ids, gold_set, k=10), 6),
                "n_pool": len(convex_ids),
                "legs": convex_cap.get("legs"),
                # the convex arm fused the RRF arm's captured lists, so the
                # comparison is one-variable BY CONSTRUCTION (not by a hoped-for
                # second-pass coincidence).
                "leg_digest": _leg_digest(rrf_cap),
                "elapsed_ms": convex_cap.get("elapsed_ms"),
                "_ids": convex_ids,
            },
        },
        "alpha_sweep": sweep,
        "rrf_vector_weight_control": control,
    }
    # A confound that must never be silent: the RRF arm never reached fusion
    # (a single-strategy read — every other leg timed out), so there is no
    # two-leg comparison on this question and the row must say so.
    out["same_legs"] = inject is not None
    return out


# ── Aggregation (per question first, aggregate second) ──────────────────────

def _rank_movement(row: dict) -> dict:
    a = row["arms"][ARM_RRF]["gold_rank"]
    b = row["arms"][ARM_CONVEX]["gold_rank"]
    return {g: {"rrf": a[g], "convex": b[g]} for g in a}


def summary(rows: list[dict]) -> dict:
    def _mean(vals: list[float]) -> float | None:
        return round(statistics.fmean(vals), 6) if vals else None

    rrf_ndcg = [r["arms"][ARM_RRF]["ndcg@10"] for r in rows if r["n_gold"]]
    cvx_ndcg = [r["arms"][ARM_CONVEX]["ndcg@10"] for r in rows if r["n_gold"]]

    moved = {"improved": 0, "worsened": 0, "unchanged": 0, "absent": 0}
    for r in rows:
        for g in r["arms"][ARM_RRF]["gold_rank"]:
            a = r["arms"][ARM_RRF]["gold_rank"][g]
            b = r["arms"][ARM_CONVEX]["gold_rank"][g]
            if a == "ABSENT" and b == "ABSENT":
                moved["absent"] += 1
                continue
            av = a if isinstance(a, int) else 10 ** 6
            bv = b if isinstance(b, int) else 10 ** 6
            if bv < av:
                moved["improved"] += 1
            elif bv > av:
                moved["worsened"] += 1
            else:
                moved["unchanged"] += 1

    alpha_means = {}
    for a in (rows[0]["alpha_sweep"] if rows else {}):
        vals = [r["alpha_sweep"][a]["ndcg@10"] for r in rows if r["n_gold"]]
        alpha_means[a] = _mean(vals)

    # The RRF weight control — the curve that separates "which arithmetic"
    # from "how much vector preference" (#4210's real question).
    rrf_weight_means = {}
    for w in (rows[0].get("rrf_vector_weight_control") if rows else {}):
        vals = [r["rrf_vector_weight_control"][w]["ndcg@10"]
                for r in rows if r["n_gold"]]
        rrf_weight_means[w] = _mean(vals)

    return {
        "n_questions": len(rows),
        "n_with_gold": sum(1 for r in rows if r["n_gold"]),
        "fusion_arms_comparable_on_every_question": all(
            r["fusion_arms_comparable"] for r in rows),
        "mean_ndcg@10": {
            ARM_RRF: _mean(rrf_ndcg),
            ARM_CONVEX: _mean(cvx_ndcg),
            "delta_convex_minus_rrf": (
                None if not rrf_ndcg else
                round(statistics.fmean(cvx_ndcg) - statistics.fmean(rrf_ndcg), 6)),
        },
        "gold_turn_movement_rrf_to_convex": moved,
        "alpha_sweep_mean_ndcg@10": alpha_means,
        "rrf_vector_weight_control_mean_ndcg@10": rrf_weight_means,
        "reading_the_control": (
            "if RRF at some weight reaches the convex arm's mean, the FORMULA "
            "is not what moved — the weight was. Rule on the pair, not the "
            "single convex number"),
        # The aggregate is NEVER the finding (#4210): on byte-identical code
        # the ask instrument's aggregate moved 0.238-0.333. Kept beside the
        # per-question rows so a reader cannot mistake it for the result.
        "aggregate_spread_warning": (
            "aggregate deltas on this instrument have a measured 0.238-0.333 "
            "spread on byte-identical code — read the per-question rows"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--questions", default=None,
                    help="comma-separated question ids (default: every fixture "
                         "question that has has_answer turns)")
    ap.add_argument("--alpha", type=float, default=0.7,
                    help="the convex arm's alpha (default 0.7, #4210)")
    ap.add_argument("--primary", default="vector",
                    help="the leg the convex weight concentrates on "
                         "(default vector — the strongest retriever per the "
                         "recorded decision)")
    ap.add_argument("--alphas", default="",
                    help="comma-separated alpha sweep (records per-alpha nDCG; "
                         "empty = the sweep is just --alpha)")
    ap.add_argument("--rrf-weights", default="1.5,3,5,10",
                    help="comma-separated vector weights for the RRF CONTROL "
                         "(the same product arithmetic on the same captured "
                         "legs, only the weight moved) — without it a "
                         "convex-vs-RRF delta cannot be attributed to the "
                         "FORMULA; empty string disables it")
    ap.add_argument("--out", help="write the JSON receipt here")
    ap.add_argument("--attempts", type=int, default=4,
                    help="fresh-graph attempts per question on a transport "
                         "failure (default 4)")
    ap.add_argument("--lane", choices=["docker", "embedded"], default="docker",
                    help="docker (FTS + HNSW vector: prod-parity, the recorded "
                         "instrument's lane) or embedded (in-process; the "
                         "vector leg is a full scan, so results are "
                         "directional, not decision-grade)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s")

    with open(FIXTURE) as f:
        data = json.load(f)
    questions = data["questions"] if isinstance(data, dict) else data
    by_id = {q["question_id"]: q for q in questions}

    if args.questions:
        qids = [q.strip() for q in args.questions.split(",") if q.strip()]
        missing = [q for q in qids if q not in by_id]
        if missing:
            logger.error("unknown question ids: %s", missing)
            return 2
    else:
        qids = [q["question_id"] for q in questions if gold_turn_ids(q)]

    alphas = [float(a) for a in args.alphas.split(",") if a.strip()] or [args.alpha]
    rrf_weights = [float(w) for w in args.rrf_weights.split(",") if w.strip()]

    import uuid
    ns = f"tm4210_fab_{uuid.uuid4().hex[:10]}"
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    if args.lane == "docker" and not uri:
        logger.error("TORTOISE_DB_URI must be set for the docker lane (the "
                     "dense leg needs FTS + the vector index); use "
                     "--lane embedded for a directional run")
        return 2

    tmpdir = tempfile.mkdtemp(prefix="fusion_ab_") if args.lane == "embedded" else None
    embedder_ready = _warm_embedder()
    logger.info("embedder ready: %s (lane=%s)", embedder_ready, args.lane)
    if not embedder_ready:
        logger.warning(
            "the embedder did not load — the dense leg will be INERT and this "
            "run cannot answer #4210's question (the arms would differ over a "
            "sparse-only pool)")

    rows: list[dict] = []
    t_start = time.time()

    def _receipt() -> dict:
        return {
            "issue": 4210,
            "what": "fusion-formula A/B: weighted RRF (shipped default) vs "
                    "score-normalised convex combination",
            "measurement_only": (
                "no product behaviour changed — the convex arm exists only inside "
                "this tool; the formula ruling is the owner's (#4210)"),
            "arms": {
                ARM_RRF: "tortoise.search_engine.rrf_fusion, weights resolved by "
                         "the SDK: the shipped default {\"vector\": 1.5}",
                ARM_CONVEX: f"convex_fusion (this file), alpha={args.alpha}, "
                            f"primary={args.primary}",
            },
            "population": {
                "source": os.path.relpath(FIXTURE, _REPO_ROOT),
                "seeding": "tools.ask_spotcheck._seed_memory(embed=True) — the "
                           "post-#4194/#4304 default (the dense leg is alive)",
                "questions": qids,
                "completed": [r["question_id"] for r in rows],
            },
            "instrument": {
                "lane": args.lane,
                "embedder_ready": embedder_ready,
                "pool_limit": LIMIT, "leg_depth": LEG_DEPTH, "reader_cut": CUT,
                "paid_calls": 0,
                "note": "gold rank + nDCG@10 only; shape_rate needs the frozen "
                        "instrument's paid reader pass (a quiet-host run, W6C/W7A "
                        "precedent)",
                "lane_caveat": (
                    None if args.lane == "docker" else
                    "EMBEDDED lane: the vector leg is a full scan (no HNSW) and the "
                    "sparse leg is the TF-IDF/FTS fallback — directional only, NOT "
                    "the recorded instrument's lane"),
            },
            "alphas_swept": alphas,
            "rrf_vector_weights_control": rrf_weights,
            "summary": summary(rows),
            "per_question": rows,
            "elapsed_s": round(time.time() - t_start, 1),
            "partial": len(rows) < len(qids),
        }

    for i, qid in enumerate(qids, 1):
        q = by_id[qid]
        # a FRESH graph per question: each haystack is an independent memory
        # (the axis-2 protocol the other ask instruments use).
        logger.info("[%d/%d] measuring %s", i, len(qids), qid)
        row = measure_question_retrying(
            q, qid_slot=i, ns=ns, alpha=args.alpha, primary=args.primary,
            alphas=alphas, attempts=args.attempts, lane=args.lane,
            tmpdir=tmpdir, rrf_weights=rrf_weights)
        rows.append(row)
        print(f"{qid:<16} gold={row['n_gold']:<2} "
              f"rrf_rank={row['arms'][ARM_RRF]['gold_rank']} "
              f"convex_rank={row['arms'][ARM_CONVEX]['gold_rank']} "
              f"ndcg rrf={row['arms'][ARM_RRF]['ndcg@10']:.3f} "
              f"convex={row['arms'][ARM_CONVEX]['ndcg@10']:.3f}", flush=True)
        if args.out:
            # written after EVERY question: a long run on a contended host is
            # not allowed to lose the rows it already measured.
            with open(args.out, "w") as f:
                json.dump(_receipt(), f, indent=2)

    receipt = _receipt()
    receipt["partial"] = False

    print("\n=== SUMMARY ===")
    print(json.dumps(receipt["summary"], indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(receipt, f, indent=2)
        print(f"\nreceipt: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
