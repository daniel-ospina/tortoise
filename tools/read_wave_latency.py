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

    # …then after the change, against the SAME corpus on the same container —
    # no flag turns the reuse on: `seed()` counts the existing Points and keeps
    # them when the corpus is already there, so the second run re-uses it as-is.
    # (An earlier cut of this block documented a reuse switch that no
    # `add_argument` declares; copying that command exited 2 before measuring
    # anything, so it is spelled out here rather than left as a trap.)
    uv run python tools/test_lane.py run -- tools/read_wave_latency.py \
        --points 2000 --queries 24 \
        --out docs/evidence/14192-read-wave/after.json \
        --baseline docs/evidence/14192-read-wave/baseline.json
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/read_wave_latency.py requires Python >= 3.12 (got {sys.version_info[0]}."
        f"{sys.version_info[1]}) — run it as `uv run python tools/read_wave_latency.py`"
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


_TIMERS_INSTALLED = False


def install_timers() -> None:
    """Wrap the wave members with wall-clock timers (no product change)."""
    global _TIMERS_INSTALLED
    if _TIMERS_INSTALLED:
        return
    _TIMERS_INSTALLED = True
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
    except Exception:  # provenance only
        return "unknown"


_AB_MODULE = "tortoise._ab_14192_old_search_engine"


def _load_pre_change_search_engine(rev: str):
    """Import ``tortoise/search_engine.py`` AS IT STOOD AT ``rev``.

    ⛔ WHY NOT WRITE IT INTO ``tortoise/``. The obvious way to get a
    package-relative module (``from .graph_ops import …``) off a git revision
    is to drop the source into the package directory under a private name and
    import it. That materialises a file inside the TRACKED tree, and any
    failure between the write and the cleanup leaves an untracked
    ``tortoise/_ab_14192_old_search_engine.py`` in the package, which the
    import-closure and declaration guards then read as product source. It
    cleaned up on the recorded run (``finally``), so this is a latent hazard
    rather than an observed one — which is exactly why it is worth removing
    instead of relying on the window staying small.

    The module NAME stays dotted, so ``__package__`` is ``tortoise`` and the
    relative imports resolve against the already-imported package; only the
    FILE lives outside the tree, in a directory that outlives this call
    (``__file__`` is still readable afterwards, which some modules touch). The
    cleared temp directory is registered for removal at interpreter exit.
    """
    import atexit
    import importlib.machinery
    import importlib.util
    import shutil
    import tempfile

    src = subprocess.run(
        ["git", "show", f"{rev}:tortoise/search_engine.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True).stdout
    tmp = Path(tempfile.mkdtemp(prefix="rv14192-ab-"))
    atexit.register(shutil.rmtree, tmp, True)
    path = tmp / "search_engine.py"
    path.write_text(src)
    loader = importlib.machinery.SourceFileLoader(_AB_MODULE, str(path))
    spec = importlib.util.spec_from_loader(_AB_MODULE, loader)
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE execution: the module body's own imports look their
    # package up in ``sys.modules``, and a module absent from it is
    # re-executed by a cyclic import.
    sys.modules[_AB_MODULE] = module
    try:
        loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_AB_MODULE, None)
        raise
    return module


def seed(proj, points_n: int, corpus_seed: int) -> int:
    """Seed the deterministic corpus ONCE; a re-run reuses what is there."""
    from benchmarks.synthetic_corpus import (
        generate_points,
        seed_corpus,
        seed_operator_edges,
    )

    existing = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
    if existing >= points_n:
        return int(existing)
    points, _fp = generate_points(points_n, seed=corpus_seed)
    seed_corpus(proj.g, points)
    rng = random.Random(corpus_seed ^ 0x14192)
    seed_operator_edges(proj.g, rng, n_edges_per_op=200, max_total=5000)
    return int(proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0])


def _result_id(r):
    """The hit's id from either shape the read can return (dict or object).

    ⛔ ``_tortoise_fts_query`` returns DICTS (``r.to_dict()`` at sdk.py:49073),
    so ``getattr(r, "id", None)`` yielded ``None`` for every hit and the
    rank-identity verdict compared two lists of nulls — it could never have gone
    red. A verdict that cannot fail is not a verdict; ``compare`` below also
    refuses an arm whose ids did not record.
    """
    if isinstance(r, dict):
        return r.get("id") or r.get("point_id") or r.get("eventId")
    return getattr(r, "id", None) or getattr(r, "point_id", None)


def run(args) -> dict:
    from tortoise import read_stages
    from tortoise.sdk import TortoiseSDK

    install_timers()
    if args.no_embed:
        # AN ARM WITHOUT A WARM EMBEDDER. This box's embedder is contended
        # (measured: ``embed_get`` bimodal between 0 ms and ~330 ms, and the
        # dense leg DECLARED ``vector:load_in_flight`` on most queries), which
        # swamps a p50/p95 comparison of the SCHEDULING change being measured.
        # ⛔ WHAT THIS FLAG ACTUALLY CONTROLS, and what it does not: it patches
        # ``EmbeddingModel.get``, but the read resolves its decline FIRST through
        # ``EmbeddingModel.await_read_ready_reason(...)`` (sdk.py, the read
        # path's first step after the stage mark), which answers
        # ``load_in_flight`` when the model has not warmed inside the read's
        # budget — so on a cold box the patched ``get`` is never reached and the
        # DECLARATION comes from the box, not from this flag. That is why the
        # recorded arms declare ``vector:load_in_flight`` rather than
        # ``no_embedder``: both arms, same declaration, same box, which is what
        # makes the comparison fair — not this flag. It is not a claim about
        # production latency.
        from tortoise.embeddings import EmbeddingModel
        EmbeddingModel.get = staticmethod(lambda *_a, **_k: None)  # type: ignore[method-assign]
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
            "order": [_result_id(r) for r in results],
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
    # ⛔ REFUSE A VERDICT ON AN UNRECORDED ORDER. An arm whose hits carry no
    # ids compares as "identical" however the pages are permuted, so the check
    # is on the ARTIFACT (are there ids at all?), not on the comparison.
    for label, arm in (("baseline", base), ("after", new)):
        missing = [pq["qid"] for pq in arm["per_query"]
                   if not any(pq["order"])]
        if missing:
            return {"comparable": False,
                    "reason": f"{label} arm recorded no result ids for "
                              f"{len(missing)} query(ies) — the rank-identity "
                              f"verdict would be vacuous (first: {missing[0]})"}
    rank_diff = []
    for pq in new["per_query"]:
        other = base_by_qid.get(pq["qid"])
        if other is None:
            continue
        if other["order"] != pq["order"]:
            # ⛔ ``strict=False``, and the truncation it implies is neutralised
            # rather than tolerated. A short page is EXPECTED here: ``limit=10``
            # is a cap, so a page is ``min(10, hits)`` long, and the two arms
            # returning a different NUMBER of hits is one of the answer changes
            # this verdict exists to report — not a state to raise on, which
            # would destroy both arms' measurement in ``--ab-full-read-from``
            # (the ``compare`` call precedes the artifact write there). So the
            # shorter list is not an accident, but a truncating compare would
            # hide it: when one page is a prefix of the other no pair differs,
            # so the divergence is recorded at ``min(len)`` — the index where
            # the shorter page ends — instead of as "none".
            la, lb = len(pq["order"]), len(other["order"])
            first = next(
                (i for i, (a, b) in enumerate(
                    zip(pq["order"], other["order"], strict=False))
                 if a != b), min(la, lb) if la != lb else None)
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


def ab_relationships(args) -> dict:
    """Interleave OLD vs NEW ``get_relationships_bounded`` on the same id set.

    WHY A SECOND MEASUREMENT EXISTS. The full-read comparison is the product
    question, but it carries this box's embedder contention and every other
    stage. This mode isolates the ONE function #14192 changed: it loads the
    pre-change implementation out of a git revision (``--ab-relationships-from``),
    runs both against the same graph and the same ids, ALTERNATING them so a
    load spike cannot land on one arm, and checks that the two answers are
    equal. The output objects must be identical — a scheduling change that
    moves an entry is an answer change.
    """
    from tortoise import search_engine
    from tortoise.sdk import TortoiseSDK

    rev = args.ab_relationships_from
    old_mod = _load_pre_change_search_engine(rev)

    sdk = TortoiseSDK()
    proj = sdk._get_proj()
    seed(proj, args.points, args.corpus_seed)
    ids = [r[0] for r in proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator = false RETURN n.id "
        f"LIMIT {args.ab_ids}").result_set]

    old_ms: list[float] = []
    new_ms: list[float] = []
    for _ in range(args.ab_rounds):
        for fn, bucket in ((old_mod.get_relationships_bounded, old_ms),
                           (search_engine.get_relationships_bounded, new_ms)):
            t = time.perf_counter()
            fn(proj.g, ids)
            bucket.append((time.perf_counter() - t) * 1000.0)

    old_out = old_mod.get_relationships_bounded(proj.g, ids)
    new_out = search_engine.get_relationships_bounded(proj.g, ids)
    return {
        "arm": f"ab-relationships-from-{rev}",
        "git_sha": _git_sha(),
        "ids": len(ids),
        "rounds": args.ab_rounds,
        "entries": sum(len(v) for v in new_out.values()),
        "identical": dict(old_out) == dict(new_out),
        "old_decoration_error": getattr(old_out, "decoration_error", ""),
        "new_decoration_error": getattr(new_out, "decoration_error", ""),
        "old_ms": stats(old_ms),
        "new_ms": stats(new_ms),
        "delta_p50_ms": round(stats(new_ms)["p50"] - stats(old_ms)["p50"], 3),
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
    ap.add_argument("--no-embed", action="store_true",
                    help="run without a warm embedder: both arms then take the "
                         "SAME declined dense leg, so the comparison isolates "
                         "the scheduling change. ⛔ It patches "
                         "EmbeddingModel.get only — on a cold box the read "
                         "declines earlier, via "
                         "await_read_ready_reason, and the declaration comes "
                         "from the box (see run())")
    ap.add_argument("--ab-relationships-from", default="",
                    help="a git revision: interleave its "
                         "get_relationships_bounded against the working tree's "
                         "on the same ids, and assert the two answers are equal")
    ap.add_argument("--ab-full-read-from", default="",
                    help="a git revision: run the WHOLE read twice — once with "
                         "that revision's get_relationships_bounded — and "
                         "compare the totals and the fused order")
    ap.add_argument("--ab-ids", type=int, default=12)
    ap.add_argument("--ab-rounds", type=int, default=7)
    args = ap.parse_args()
    if args.label:
        pass
    else:
        args.label = Path(args.out).stem

    if args.ab_relationships_from:
        report = ab_relationships(args)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        print(f"\nwrote {out}")
        return

    if args.ab_full_read_from:
        # The whole read, twice, in ONE process against ONE corpus: arm 1 has
        # the pre-change ``get_relationships_bounded`` patched in at the module
        # attribute the SDK's call-site import resolves, arm 2 the working
        # tree's. The two arms therefore differ in exactly the change under
        # test, and ``compare`` reports both the totals and the rank verdict.
        from tortoise import search_engine

        rev = args.ab_full_read_from
        old_mod = _load_pre_change_search_engine(rev)

        new_fn = search_engine.get_relationships_bounded
        search_engine.get_relationships_bounded = old_mod.get_relationships_bounded
        base = run(args)
        search_engine.get_relationships_bounded = new_fn
        # ``install_timers`` is idempotent (it must be: the calls inside
        # ``run()`` would otherwise be double-wrapped and this arm's cost
        # counted twice), so the restored NEW function has to be wrapped here
        # or arm 2's ``post_relationships`` column would read a false 0.0.
        def _timed_new(*a, **k):
            t = time.perf_counter()
            try:
                return new_fn(*a, **k)
            finally:
                RECORDER.add("post_relationships",
                             (time.perf_counter() - t) * 1000.0)

        search_engine.get_relationships_bounded = _timed_new
        after = run(args)
        search_engine.get_relationships_bounded = new_fn
        verdict = compare(after, base)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"baseline": base, "after": after,
                                   "verdict": verdict}, indent=2))
        print(json.dumps(verdict, indent=2)[:4000])
        print(f"\nwrote {out}")
        return

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
