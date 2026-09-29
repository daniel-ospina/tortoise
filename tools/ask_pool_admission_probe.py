#!/usr/bin/env python3
"""#4235 deterministic admission probe — the measurement the issue names.

Issue #4235 defines Option B as ``pool = max(env, limit*2)`` and says, of it:
*"It changes the measured window, so it needs the deterministic admission
probe re-run plus a reader run."* This is that admission probe. It is
DETERMINISTIC and reader-free: it calls the same ``tortoise_fts_query`` the
ask lane calls, over the same frozen fixture, on a real FalkorDB, and records
which candidates the widened pool admits.

Two arms, both against a real FalkorDB (``--db-uri``):

  * ``fixture`` — the frozen D3 fixture (``tests/fixtures/
    ask_spotcheck_composition.json``, the 21-question composition the issue
    names). The five long-gold questions are seeded ONE PER SCRATCH GRAPH
    (the fixture's haystacks are independent; a shared graph would let every
    question's haystack leak into the others' ranking), then queried at each
    pool. This is the arm that reproduces the issue's recorded fused ranks.

  * ``synthetic`` — a 450-point corpus in which every point matches the
    query, so the pool is the ONLY bound on the candidate set. This is the
    corpus shape the review used to measure the COST of the widening (the
    frozen fixture's per-leg counts can be smaller than the pool).

For each pool it records: the returned top-``limit`` id list (the admission
surface — ``tortoise_fts_query`` cuts at ``result_ids[:limit]``), the order,
the per-leg candidate counts (``leg_trace``), the gold-turn ranks, and the
median wall-clock over ``--repeats`` warm calls. It then diffs the top-limit
SET between pools: a non-empty diff means the widening is production-visible
in the returned set, not merely in the tail.

NO reader is called and NO provider key is needed — the reader cannot
discriminate these options (the issue records it flipping 4/21 on
byte-identical code), so the admission property is the discriminating
measurement, and this probe is the instrument for it.

Usage:
    TORTOISE_ASK_POOL_PROBE_DB_URI='docker://:falkordb@localhost:6379' \
      uv run python tools/ask_pool_admission_probe.py --out /tmp/adm.json
    ... --embed            # also seed/measure the dense leg (needs the extra)
    ... --synthetic-only   # the 450-point cost arm alone
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "ask_spotcheck_composition.json"
#: The five long-gold window-miss questions the issue's evidence names.
LONG_GOLD = ["0a995998", "1d4e3b97", "1de5cff2", "ceb54acb", "e9327a54"]
#: The ask lane's shipped window (#4105) and the two pool depths under test:
#: Option A (pool == limit) and Option B (the SDK's ``limit*2`` floor).
FROZEN_LIMIT = 200
FROZEN_POOLS = (200, 400)
#: The synthetic arm's shape (the reviewer's 450-point corpus).
SYNTHETIC_POINTS = 450


def gold_turn_ids(q: dict) -> list[str]:
    ids = q.get("haystack_session_ids") or []
    out: list[str] = []
    for i, sess in enumerate(q.get("haystack_sessions") or []):
        for j, turn in enumerate(sess or []):
            if turn.get("has_answer"):
                out.append(f"{ids[i]}_t{j}")
    return out


def _ids_of(hits: list[dict]) -> list[str]:
    return [str(h.get("id") or h.get("point_id") or h.get("pointId")
                or h.get("node_id") or "") for h in hits]


def _graph_uri(base: str, graph: str) -> str:
    return f"{base.rstrip('/')}/{graph}"


def _measure_pools(sdk, query: str, *, limit: int, pools, repeats: int) -> dict:
    """Warm-then-repeat query at each pool; returns the per-pool record."""
    per: dict = {}
    for pool in pools:
        trace: list[dict] = []
        sdk.tortoise_fts_query(query, limit=limit, pool_size=pool,
                               include_terminal=True, leg_trace=trace,
                               keep_numeric=True, search_keys_prf=True)
        lat: list[float] = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            hits = sdk.tortoise_fts_query(
                query, limit=limit, pool_size=pool, include_terminal=True,
                keep_numeric=True, search_keys_prf=True)
            lat.append((time.perf_counter() - t0) * 1000.0)
        per[pool] = {
            "n_returned": len(hits),
            "ids": _ids_of(hits),
            "leg_counts": {t["leg"]: t.get("count") for t in trace},
            "latency_ms": [round(x, 1) for x in lat],
            "latency_ms_median": round(statistics.median(lat), 1),
        }
    lo, hi = min(pools), max(pools)
    a, b = set(per[lo]["ids"]), set(per[hi]["ids"])
    per["_diff"] = {
        "pool_lo": lo, "pool_hi": hi,
        "n_added": len(b - a), "n_dropped": len(a - b),
        "top_set_changed": sorted(a ^ b),
        "order_changed": per[lo]["ids"] != per[hi]["ids"],
    }
    return per


def run_fixture(base: str, *, embed: bool, repeats: int, limit: int,
                pools) -> dict:
    from tools.ask_spotcheck import _seed_memory
    from tortoise.sdk import TortoiseSDK

    with open(FIXTURE) as f:
        data = json.load(f)
    questions = data["questions"] if isinstance(data, dict) else data
    by_id = {q["question_id"]: q for q in questions}
    run = uuid.uuid4().hex[:8]
    out: dict = {"seeding_mode": (
        "_seed_memory(embed=True) — the product's own turn vector"
        if embed else
        "_seed_memory(embed=False) — keyword-only store, no dense leg"),
        "arm": "fixture", "questions": {}}
    for i, qid in enumerate(LONG_GOLD):
        q = by_id[qid]
        graph = f"ask_pool_probe_{run}_{'e' if embed else 'k'}_{i}"
        os.environ["TORTOISE_DB_URI"] = _graph_uri(base, graph)
        sdk = TortoiseSDK(None)
        try:
            _seed_memory(sdk, q, embed=embed)
            per = _measure_pools(sdk, q["question"], limit=limit, pools=pools,
                                 repeats=repeats)
            gold = gold_turn_ids(q)
            ranks: dict = {}
            for p in pools:
                ids = per[p]["ids"]
                ranks[str(p)] = {g: (ids.index(g) + 1 if g in ids else None)
                                 for g in gold}
            out["questions"][qid] = {
                "gold_turns": gold,
                "gold_ranks": ranks,
                "per_pool": {str(p): {k: v for k, v in per[p].items()
                                      if k != "ids"} for p in pools},
                "_diff": per["_diff"],
            }
            d = per["_diff"]
            print(f"{qid:12s} added={d['n_added']:3d} dropped={d['n_dropped']:3d} "
                  f"order_changed={d['order_changed']} "
                  f"lat={per[min(pools)]['latency_ms_median']}/"
                  f"{per[max(pools)]['latency_ms_median']}ms "
                  f"gold={ranks}", flush=True)
        finally:
            sdk.close()
    return out


def run_synthetic(base: str, *, embed: bool, repeats: int, limit: int,
                  pools, n_points: int = SYNTHETIC_POINTS) -> dict:
    from tools.ask_spotcheck import seed_capture_turn_store
    from tortoise.sdk import TortoiseSDK

    run = uuid.uuid4().hex[:8]
    os.environ["TORTOISE_DB_URI"] = _graph_uri(
        base, f"ask_pool_probe_{run}_syn")
    sdk = TortoiseSDK(None)
    try:
        # 45 sessions x 10 turns, every turn matching the query; the query
        # term's frequency varies so the per-point score is distinct and no
        # tie can mask a set difference.
        per_session = 10
        for s in range(n_points // per_session):
            sess = []
            for t in range(per_session):
                i = s * per_session + t
                sess.append({"role": "user",
                             "content": ("needle " * (1 + i % 17))
                                        + f" filler{i}"})
            seed_capture_turn_store(sdk, f"synth_s{s}", sess, embed=embed)
        per = _measure_pools(sdk, "needle", limit=limit, pools=pools,
                             repeats=repeats)
        return {
            "arm": "synthetic",
            "n_points": n_points,
            "seeding_mode": "embedded" if embed else "keyword-only",
            "per_pool": {str(p): {k: v for k, v in per[p].items()
                                  if k != "ids"} for p in pools},
            "_diff": per["_diff"],
        }
    finally:
        sdk.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-uri", default=os.environ.get(
        "TORTOISE_ASK_POOL_PROBE_DB_URI", ""),
        help="FalkorDB base URI; a run-unique graph is appended per measure")
    ap.add_argument("--out", required=True, help="JSON receipt path")
    ap.add_argument("--limit", type=int, default=FROZEN_LIMIT)
    ap.add_argument("--pools", default=",".join(str(p) for p in FROZEN_POOLS))
    ap.add_argument("--repeats", type=int, default=8)
    ap.add_argument("--embed", action="store_true",
                    help="also seed and measure the dense leg")
    ap.add_argument("--synthetic-only", action="store_true")
    ap.add_argument("--fixture-only", action="store_true")
    args = ap.parse_args(argv)
    if not args.db_uri:
        print("ABORT: --db-uri (or TORTOISE_ASK_POOL_PROBE_DB_URI) is required: "
              "this probe measures a REAL FalkorDB, not the embedded fallback",
              file=sys.stderr)
        return 2
    pools = tuple(int(p) for p in args.pools.split(",") if p.strip())
    receipt: dict = {"limit": args.limit, "pools": list(pools),
                     "repeats": args.repeats}
    if not args.synthetic_only:
        receipt["fixture"] = run_fixture(
            args.db_uri, embed=args.embed, repeats=args.repeats,
            limit=args.limit, pools=pools)
    if not args.fixture_only:
        receipt["synthetic"] = run_synthetic(
            args.db_uri, embed=args.embed, repeats=args.repeats,
            limit=args.limit, pools=pools)
    with open(args.out, "w") as f:
        json.dump(receipt, f, indent=1, default=str)
    print("receipt:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
