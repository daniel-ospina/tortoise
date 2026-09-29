#!/usr/bin/env python3
"""#5534 supplementary diagnostic — the A4 (``TORTOISE_ASK_SEARCH_KEYS_PRF``)
A/B on the D3 fixture's seeded store, PER QUESTION.

WHY THIS EXISTS
  #5534 measured the D3/ask-shape instrument as UNABLE to measure A4: its
  seeder (``tools.ask_spotcheck._seed_memory``) wrote a pure capture TURN
  store with zero ``search_keys``, and ``tortoise.sparse.expansion_tokens``
  opens with ``if not aliases: return []``. With no input the FTS leg is
  byte-identical ON vs OFF, so every A4 A/B returned a guaranteed zero. That
  is not a verdict on A4; it is a verdict on the instrument. This diagnostic
  is the instrument-completion: it seeds each question, harvests the aliases
  the expansion actually sees, and compares the ORDERED hit list between the
  two arms.

WHAT IT REPORTS (per question, plus a summary)
  * ``search_keys_points`` — Points carrying a non-empty ``search_keys``
    after seeding (the MECHANISM evidence: #5534's number was 0).
  * ``fts_top5`` — the first-pass top-5 ids and whether each carries
    ``search_keys`` (the harvest surface), plus
    ``expansion_tokens(<harvested>, reserved=<query tokens>)`` (the UNIT
    evidence: must be non-empty).
  * ``hits_off`` / ``hits_on`` — the ordered hit-id lists for the two arms.
  * ``differ`` — the ARM evidence: True iff the ordered lists differ.

It changes NOTHING in the product or in ``tools/ask_shape_rate.py``'s ruler
(legs, thresholds, fixture, reader pin, pre-registered rule). It is a
diagnostic, not the ruler.

Read-only with respect to the product; it seeds through the SAME seeder
(``_seed_memory``) and spins the SAME per-call docker scratch graphs
(``ask_shape_rate._fresh_db``) the instrument uses — so the arms differ ONLY
in the A4 knob.

Usage:
  TORTOISE_ASK_SHAPE_DB_URI='docker://:falkordb@localhost:6379/askshape5534' \\
    python3 docs/runbook/5534_a4_ab_diagnostic.py \\
      [--questions 0100672e,1d4e3b97 | --all] [--limit 120] [--no-embed] \\
      [--no-search-keys] [--out /tmp/a4ab.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from tools.ask_shape_rate import _drop_scratch_graph, _fresh_db  # noqa: E402
from tools.ask_spotcheck import _seed_memory  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402
from tortoise.search_engine import run_fts_query  # noqa: E402
from tortoise.sparse import (  # noqa: E402
    expansion_tokens,
    tokenize_sparse_query,
)

FIXTURE = os.path.join(_REPO_ROOT, "tests", "fixtures",
                       "ask_spotcheck_composition.json")
DEFAULT_LIMIT = 120   # the gold-rank diagnostic / #4593 census depth


def _ids_of(hits: list[dict]) -> list[str]:
    return [str(h.get("id") or h.get("point_id") or "") for h in hits]


def _search_keys_for(sdk, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.id IN $ids "
        "RETURN p.id, coalesce(p.search_keys, '')",
        params={"ids": ids},
    ).result_set
    return {r[0]: (r[1] or "") for r in rows}


def measure_one(q: dict, *, limit: int, embed: bool,
                search_keys: bool) -> dict:
    """One question, one fresh scratch graph, both arms."""
    qid = q["question_id"]
    db = _fresh_db(f"a4_{qid}")
    t0 = time.monotonic()
    sdk = TortoiseSDK(db)
    out: dict = {"question_id": qid, "question": q["question"]}
    try:
        _seed_memory(sdk, q, embed=embed, search_keys=search_keys)
        out["seed_s"] = round(time.monotonic() - t0, 2)
        out["seeded_points"] = sdk._get_proj().g.query(
            "MATCH (p:Point) RETURN count(p)").result_set[0][0]
        out["search_keys_points"] = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.search_keys IS NOT NULL "
            "AND p.search_keys <> '' RETURN count(p)").result_set[0][0]

        t1 = time.monotonic()
        # The FIRST pass A4 harvests from: the raw FTS leg's top-5.
        fts_first = run_fts_query(
            sdk._get_proj().g, q["question"], entity_type="point",
            limit=limit, excluded_statuses=None, keep_numeric=True)
        top5 = [pid for pid, _s in fts_first[:5] if pid]
        sk_map = _search_keys_for(sdk, top5)
        harvested = [v for v in (sk_map.get(pid, "") for pid in top5) if v]
        reserved = set(tokenize_sparse_query(q["question"],
                                             keep_numeric=True))
        expansion = expansion_tokens(harvested, reserved=reserved)
        out["fts_pass_s"] = round(time.monotonic() - t1, 2)
        out["fts_top5"] = [
            {"id": pid, "search_keys": sk_map.get(pid, "")} for pid in top5]
        out["harvested_aliases"] = harvested
        out["expansion_tokens"] = expansion
        out["expansion_terms"] = len(expansion)

        t2 = time.monotonic()
        hits_off = sdk.tortoise_fts_query(
            q["question"], limit=limit, pool_size=limit,
            include_terminal=True, keep_numeric=True, search_keys_prf=False)
        t3 = time.monotonic()
        hits_on = sdk.tortoise_fts_query(
            q["question"], limit=limit, pool_size=limit,
            include_terminal=True, keep_numeric=True, search_keys_prf=True)
        t4 = time.monotonic()
        off_ids = _ids_of(hits_off)
        on_ids = _ids_of(hits_on)
        out["n_hits_off"] = len(off_ids)
        out["n_hits_on"] = len(on_ids)
        out["first_diff_index"] = next(
            (i for i, (a, b) in enumerate(zip(off_ids, on_ids, strict=False))
             if a != b),
            None)
        out["differ"] = off_ids != on_ids
        out["hits_off"] = off_ids
        out["hits_on"] = on_ids
        out["arm_s"] = round(t4 - t3, 2)
        out["retrieval_s"] = round(t4 - t2, 2)
    finally:
        sdk.close()
    out["total_s"] = round(time.monotonic() - t0, 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--questions", default=None,
                    help="comma-separated question ids (default: the first 3)")
    ap.add_argument("--all", action="store_true",
                    help="run all fixture questions")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--embed", dest="embed", action="store_true",
                    default=None, help="seed the product turn vectors")
    ap.add_argument("--no-embed", dest="embed", action="store_false",
                    help="skip vectors (FTS-only store)")
    ap.add_argument("--no-search-keys", dest="search_keys",
                    action="store_false", default=True,
                    help="the #5534 defect arm (mutation control)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not os.environ.get("TORTOISE_ASK_SHAPE_DB_URI", "").strip():
        print("5534: TORTOISE_ASK_SHAPE_DB_URI must name a docker:// base "
              "graph (per-call scratch graphs; the embedded lane has no FTS "
              "index, so A4 has no leg to expand).", file=sys.stderr)
        return 2

    with open(FIXTURE) as f:
        data = json.load(f)
    questions = data["questions"] if isinstance(data, dict) else data
    by_id = {q["question_id"]: q for q in questions}
    if args.all:
        qids = [q["question_id"] for q in questions]
    elif args.questions:
        qids = [s.strip() for s in args.questions.split(",") if s.strip()]
    else:
        qids = [q["question_id"] for q in questions[:3]]
    missing = [q for q in qids if q not in by_id]
    if missing:
        print(f"5534: unknown question ids: {missing}", file=sys.stderr)
        return 2

    embed = bool(args.embed)
    receipt: dict = {
        "diagnostic": "docs/runbook/5534_a4_ab_diagnostic.py",
        "issue": 5534,
        "fixture": os.path.relpath(FIXTURE, _REPO_ROOT),
        "seeding": {"embed": embed, "search_keys": bool(args.search_keys)},
        "limit": args.limit,
        "questions": [],
    }
    t_all = time.monotonic()
    try:
        for i, qid in enumerate(qids):
            print(f"[{i + 1}/{len(qids)}] {qid} …", flush=True)
            rec = measure_one(by_id[qid], limit=args.limit, embed=embed,
                              search_keys=bool(args.search_keys))
            receipt["questions"].append(rec)
            print(f"    search_keys_points={rec['search_keys_points']} "
                  f"expansion={len(rec['expansion_tokens'])} "
                  f"differ={rec['differ']} total={rec['total_s']}s",
                  flush=True)
    finally:
        _drop_scratch_graph()
    receipt["wallclock_s"] = round(time.monotonic() - t_all, 1)
    n_diff = sum(1 for r in receipt["questions"] if r["differ"])
    receipt["summary"] = {
        "n_questions": len(receipt["questions"]),
        "n_differ": n_diff,
        "zero_search_keys_questions": sum(
            1 for r in receipt["questions"] if r["search_keys_points"] == 0),
        "empty_expansion_questions": sum(
            1 for r in receipt["questions"] if not r["expansion_tokens"]),
    }
    print(json.dumps(receipt["summary"], indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(receipt, f, indent=2)
        print(f"receipt: {args.out}")
    return 0 if n_diff else 1


if __name__ == "__main__":
    sys.exit(main())
