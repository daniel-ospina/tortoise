"""#14192 — measure the READ PATH's latency, per stage and per wave member.

The issue this exists for asks for one comparison: *"a measured p50/p95 of a
search with the legs parallel vs the current chain, on the same box and
corpus"*, plus a proof that the fused ranking did not move. This tool is the
measurement half of that, and it is deliberately boring:

  * it seeds ONE deterministic corpus (``benchmarks.synthetic_corpus`` — the
    #316 generator) into a real FalkorDB, so both arms run on the same rows;
  * it runs a FIXED query set (``benchmarks/query_mix.json``) through the real
    product read (``TortoiseSDK._tortoise_fts_query`` — what ``/v1/search``
    and the MCP ``tortoise_search`` call);
  * per query it records the wall time, the ``read_stages`` table (the SAME
    instrument production publishes on a breached request, #10893), the
    per-function cost of every stage that can be re-scheduled, the declared
    legs from ``leg_trace``, and the ORDERED result ids;
  * it compares two JSONs (``--baseline``) and prints the delta — including a
    rank-identity check on the ordered ids, which is what "the fusion result is
    rank-identical to today for a fixed query set" means.

⛔ WHY THE ORDER IS RECORDED, NOT A SCORE. ``rrf_fusion`` is rank-based
(``tortoise/ranking.py``), so the fused order IS the read's answer. A
scheduling change that leaves every number looking healthy but permutes one
page has changed the product, and only the ordered ids can show that.

⛔ THIS TOOL DOES NOT BOUND ANYTHING. No deadline is introduced, no wait budget
is read or changed (`#3834` — the budget is not this issue's, and raising it is
explicitly out of scope). It measures.

Usage (the private lane is the correct target on a shared box, #5084/#10399):

    uv run python tools/test_lane.py run -- tools/read_wave_latency.py \
        --points 2000 --queries 24 --out docs/evidence/14192-read-wave/baseline.json

    # …then after the change, against the SAME corpus on the same container:
    uv run python tools/test_lane.py run -- tools/read_wave_latency.py \
        --points 2000 --queries 24 --reuse \
        --out docs/evidence/14192-read-wave/after.json \
        --baseline docs/evidence/14192-read-wave/baseline.json
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/read_wave_latency.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it with `uv run`"
    )

import argparse
import json
import os
import random
import statistics
import subprocess
import threading
import time
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# The measurement drives the SDK directly; a hosted client would put a network
# hop in front of the thing being measured.
os.environ.pop("TORTOISE_API_URL", None)
os.environ.pop("TORTOISE_API_KEY", None)

QUERY_MIX = Path(_REPO_ROOT) / "benchmarks" / "query_mix.json"

#: Functions whose cost is worth a column of its own. Every entry is a member of
#: a wave the read path runs, so the table shows what a re-schedule could
#: overlap rather than only which named stage won.
#: ``(module_attr, function_name, column_name)``
TIMED = [
    ("tortoise.search_engine", "run_fts_query", "leg_fts"),
    ("tortoise.search_engine", "run_vector_query", "leg_vector"),
    ("tortoise.search_engine", "run_structural_query", "leg_structural"),
    ("tortoise.search_engine", "degradation_chain", "legs_wave"),
    ("tortoise.search_engine", "rrf_fusion", "fusion"),
    ("tortoise.search_engine", "annotate_ep_batch", "post_ep_annotate"),
    ("tortoise.search_engine", "get_relationships_bounded", "post_relationships"),
    ("tortoise.search_engine", "fetch_point_epistemic_state", "post_epistemic"),
]

#: The stage columns this tool always prints, in the order a read enters them.
STAGE_ORDER = [
    "pre_handler", "embed_get", "embed_encode", "retrieval", "fusion",
    "ep_annotation", "ordering", "response",
]


class Recorder:
    """Thread-safe per-query accumulator (the legs run on worker threads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ms: dict[str, float] = {}
        self.calls: dict[str, int] = {}

    def add(self, name: str, ms: float) -> None:
        with self._lock:
            self.ms[name] = self.ms.get(name, 0.0) + ms
            self.calls[name] = self.calls.get(name, 0) + 1

    def reset(self) -> None:
        with self._lock:
            self.ms = {}
            self.calls = {}


RECORDER = Recorder()


def install_timers() -> None:
    """Wrap the wave members with wall-clock timers (no product change)."""
    import importlib

    grouped: dict[str, list[tuple[str, str]]] = {}
    for mod_name, fn_name, column in TIMED:
        grouped.setdefault(mod_name, []).append((fn_name, column))

    for mod_name, targets in grouped.items():
        mod = importlib.import_module(mod_name)
        for fn_name, column in targets:
            orig = getattr(mod, fn_name, None)
            if orig is None:
                continue

            def make(orig, column):
                def timed(*a, **k):
                    t = time.perf_counter()
                    try:
                        return orig(*a, **k)
                    finally:
                        RECORDER.add(column, (time.perf_counter() - t) * 1000.0)

                return timed

            setattr(mod, f"_{fn_name}_unwrapped_14192", orig)
            setattr(mod, fn_name, make(orig, column))


def stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "max": 0.0, "mean": 0.0}
    s = sorted(xs)
    return {
        "n": len(s),
        "p50": round(s[len(s) // 2], 3),
        "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 3),
        "max": round(s[-1], 3),
        "mean": round(statistics.fmean(s), 3),
    }


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, check=False,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — provenance only
        return "unknown"


def seed(proj, points_n: int, corpus_seed: int) -> int:
    """Seed the deterministic corpus ONCE; a re-run reuses what is there."""
    from benchmarks.synthetic_corpus import (
        generate_points, seed_corpus, seed_operator_edges,
    )

    existing = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
    if existing >= points_n:
        return int(existing)
    points, _fp = generate_points(points_n, seed=corpus_seed)
    seed_corpus(proj.g, points)
    rng = random.Random(corpus_seed ^ 0x14192)
    seed_operator_edges(proj.g, rng, n_edges_per_op=200, max_total=5000)
    return int(proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0])


def run(args) -> dict:
    from tortoise import read_stages
    from tortoise.sdk import TortoiseSDK

    install_timers()
    sdk = TortoiseSDK()
    proj = sdk._get_proj()
    n_points = seed(proj, args.points, args.corpus_seed)

    mix = json.loads(QUERY_MIX.read_text())["queries"]
    queries = mix[: args.queries] if args.queries else mix

    # Warm-up: the embedder load and the projection's index caches are setup,
    # not this issue's chain (#10893's ``embed_get``).
    sdk._tortoise_fts_query(query=queries[0]["query"], limit=10)

    per_query = []
    for q in queries:
        RECORDER.reset()
        stages = read_stages.begin()
        t0 = time.perf_counter()
        with read_stages.request_stages(stages):
            trace: list[dict] = []
            results = sdk._tortoise_fts_query(
                query=q["query"], limit=10, leg_trace=trace)
        total_ms = (time.perf_counter() - t0) * 1000.0
        per_query.append({
            "qid": q["id"],
            "query": q["query"],
            "total_ms": round(total_ms, 3),
            "stages": stages.spent_ms(),
            "members": dict(RECORDER.ms),
            "member_calls": dict(RECORDER.calls),
            "legs": [
                {"leg": e.get("leg"), "ran": e.get("ran"),
                 "degraded": e.get("degraded"), "reason": e.get("reason"),
                 "count": e.get("count")}
                for e in trace if e.get("leg")
            ],
            "order": [getattr(r, "id", None) for r in results],
        })
        print(f"  {q['id']}: total={total_ms:7.1f}ms "
              f"members={RECORDER.ms.get('legs_wave', 0.0):6.1f}ms "
              f"ep={RECORDER.ms.get('post_ep_annotate', 0.0):5.1f}ms "
              f"rel={RECORDER.ms.get('post_relationships', 0.0):5.1f}ms")

    stage_table = {
        name: stats([pq["stages"].get(name, 0.0) for pq in per_query])
        for name in STAGE_ORDER
    }
    member_table = {
        name: stats([pq["members"].get(name, 0.0) for pq in per_query])
        for _m, _f, name in TIMED
    }
    declared = {}
    for pq in per_query:
        for leg in pq["legs"]:
            key = f"{leg['leg']}:{leg['reason'] or 'ok'}"
            declared[key] = declared.get(key, 0) + 1

    return {
        "arm": args.label,
        "git_sha": _git_sha(),
        "db_uri": os.environ.get("TORTOISE_DB_URI", ""),
        "points": n_points,
        "corpus_seed": args.corpus_seed,
        "queries": len(per_query),
        "stage_table": stage_table,
        "member_table": member_table,
        "legs_declared": declared,
        "per_query": per_query,
    }


def compare(new: dict, base: dict) -> dict:
    """Delta on the totals + the rank-identity verdict (#14192's Done-when)."""
    new_tot = [pq["total_ms"] for pq in new["per_query"]]
    base_tot = [pq["total_ms"] for pq in base["per_query"]]
    if (new["points"] != base["points"]
            or new["corpus_seed"] != base["corpus_seed"]
            or new["queries"] != base["queries"]):
        return {"comparable": False,
                "reason": "corpus or query set differs between the arms"}

    base_by_qid = {pq["qid"]: pq for pq in base["per_query"]}
    rank_diff = []
    for pq in new["per_query"]:
        other = base_by_qid.get(pq["qid"])
        if other is None:
            continue
        if other["order"] != pq["order"]:
            first = next(
                (i for i, (a, b) in enumerate(zip(pq["order"], other["order"]))
                 if a != b), None)
            rank_diff.append({
                "qid": pq["qid"],
                "first_divergence_idx": first,
                "baseline": other["order"][:10],
                "after": pq["order"][:10],
            })
    stage_delta = {}
    for name in STAGE_ORDER:
        b = base["stage_table"].get(name, {}).get("p50", 0.0)
        a = new["stage_table"].get(name, {}).get("p50", 0.0)
        stage_delta[name] = {"baseline_p50": b, "after_p50": a,
                             "delta_ms": round(a - b, 3)}
    member_delta = {}
    for _m, _f, name in TIMED:
        b = base["member_table"].get(name, {}).get("p50", 0.0)
        a = new["member_table"].get(name, {}).get("p50", 0.0)
        member_delta[name] = {"baseline_p50": b, "after_p50": a,
                              "delta_ms": round(a - b, 3)}
    return {
        "comparable": True,
        "baseline_total": stats(base_tot),
        "after_total": stats(new_tot),
        "total_delta_p50_ms": round(
            stats(new_tot)["p50"] - stats(base_tot)["p50"], 3),
        "total_delta_p95_ms": round(
            stats(new_tot)["p95"] - stats(base_tot)["p95"], 3),
        "stage_delta": stage_delta,
        "member_delta": member_delta,
        "rank_identical": not rank_diff,
        "rank_divergent_queries": rank_diff,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", type=int, default=2000)
    ap.add_argument("--queries", type=int, default=24,
                    help="0 = the whole query mix")
    ap.add_argument("--corpus-seed", type=int, default=42)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="14192-read-wave.json")
    ap.add_argument("--baseline", default="",
                    help="a previous run's JSON — prints the delta and the "
                         "rank-identity verdict")
    args = ap.parse_args()
    if not args.label:
        args.label = Path(args.out).stem

    report = run(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))

    print(f"\n=== {args.label} · head {report['git_sha']} · "
          f"{report['points']} Points · {report['queries']} queries ===")
    print(f"{'stage':<16} {'p50':>9} {'p95':>9} {'max':>9}")
    for name in STAGE_ORDER:
        s = report["stage_table"][name]
        print(f"{name:<16} {s['p50']:>9.1f} {s['p95']:>9.1f} {s['max']:>9.1f}")
    print(f"\n{'wave member':<24} {'p50':>9} {'p95':>9} {'max':>9}")
    for _m, _f, name in TIMED:
        s = report["member_table"][name]
        print(f"{name:<24} {s['p50']:>9.1f} {s['p95']:>9.1f} {s['max']:>9.1f}")
    tot = stats([pq["total_ms"] for pq in report["per_query"]])
    print(f"\nTOTAL  p50={tot['p50']:.1f}ms  p95={tot['p95']:.1f}ms  "
          f"max={tot['max']:.1f}ms")
    print(f"legs declared: {report['legs_declared']}")

    if args.baseline:
        base = json.loads(Path(args.baseline).read_text())
        verdict = compare(report, base)
        report["vs_baseline"] = verdict
        out.write_text(json.dumps(report, indent=2))
        print(f"\n=== delta vs {args.baseline} ===")
        if not verdict["comparable"]:
            print(f"⛔ NOT COMPARABLE: {verdict['reason']}")
            return
        print(f"total p50  {verdict['baseline_total']['p50']:9.1f} -> "
              f"{verdict['after_total']['p50']:9.1f}  "
              f"({verdict['total_delta_p50_ms']:+.1f} ms)")
        print(f"total p95  {verdict['baseline_total']['p95']:9.1f} -> "
              f"{verdict['after_total']['p95']:9.1f}  "
              f"({verdict['total_delta_p95_ms']:+.1f} ms)")
        for name, d in verdict["member_delta"].items():
            print(f"  {name:<24} {d['baseline_p50']:9.1f} -> "
                  f"{d['after_p50']:9.1f}  ({d['delta_ms']:+.1f})")
        if verdict["rank_identical"]:
            print("fused order: RANK-IDENTICAL on every query")
        else:
            print(f"⛔ fused order moved on "
                  f"{len(verdict['rank_divergent_queries'])} query(ies):")
            for d in verdict["rank_divergent_queries"][:5]:
                print(f"  {d['qid']} first divergence at index "
                      f"{d['first_divergence_idx']}")
                print(f"    baseline: {d['baseline']}")
                print(f"    after   : {d['after']}")

    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
