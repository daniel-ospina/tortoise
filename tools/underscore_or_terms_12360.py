#!/usr/bin/env python
"""Before/after for #12360's `_`-preserving OR term, on the REAL path, BOTH surfaces.

WHAT THIS MEASURES
------------------
`#12298` measured the arm in a harness and found candidate recall@3
**0.7133 -> 0.9633** on a pinned 300-name population. This tool re-measures it
with the arm **shipped** — i.e. with `build_or_query` emitting the composite and
no harness-side term injection — because the falsifier on `#12360` is about the
production tokeniser, not about a replica of it:

    "If, on the real path (not a harness), the gain is not >= 10 points on
     candidate recall and it is not visible on both surfaces, revert."

The two arms are the SHIPPED code with `TORTOISE_UNDERSCORE_TERMS` off and on.
Nothing else differs — same graph, same ids, same window, same process.

⚠️ **Why that switch exists at all.** `run_fts_query` REBUILDS its query through
`build_or_query` (`search_engine.py`, the R2/#1541 leg), so a harness cannot hand
it a term set: `#12298`'s first arm passed `search|web|web_search` and measured
**+0.33 pts**, the function re-tokenising it straight back. That trap is why the
repair lives in the builder, and why the switch — not a harness arm — is what
toggles it here.

SURFACES (all three go through `run_fts_query`; two are product doors)
---------------------------------------------------------------------
``fts_leg``        ``search_engine.run_fts_query`` directly — the FTS leg in
                   ISOLATION, which is the number `#12298` published, so this
                   run can be compared to it.
``mcp_search``     ``sdk.tortoise_fts_query`` — the door the MCP tool
                   ``tortoise_search`` is registered to
                   (``tool_registry.py``: ``sdk_method="tortoise_fts_query"``)
                   and the one REST ``GET /v1/search`` uses. The FUSED read.
``capture_prior``  ``extractor_v2._fts_rows`` — the capture-time
                   link-before-create prior (``entity_type="object"``,
                   terminal-inclusive, order re-owned downstream). The FUSED
                   read, through the write-path door.

⛔ ``capture_prior`` runs at ``limit=KMAX``, not the production 3, because
recall@k is a WINDOW SWEEP and a limit of 3 cannot report a rank of 4. The
production window is 3, so its **recall@3** is the number to quote for it; the
wider ks are the shape. Identical call, wider window — declared, not hidden.

THE POPULATION IS `#12298`'s, PINNED AND IMPORTED
-------------------------------------------------
``population.json`` is a verbatim copy of the file PR `#12349` pinned (sha256 in
the artifact), so these numbers are directly comparable to the 0.7133/0.9633
already on the record. Its class quotas are a deliberate OVERSAMPLE (ident 30 %,
path 10 %, vs a measured production share of ~2 % identifiers), so the composite
headline is **stratified, never class-share weighted** — quote the per-class
table for any one class.

METRICS
-------
``recall@k``  the gold record's rank is in the top k. The population writes ONE
              record per name and queries that name, so this is candidate
              reachability, exactly `#11901`'s/#12298's definition.
``nDCG@10``   graded relevance with ONE relevant document, so
              ``DCG = 1/log2(rank+1)`` and ``IDCG = 1``: i.e. a rank-decay
              measure that agrees with recall@10 on membership and rewards depth
              inside the window. Stated because "nDCG" usually implies a
              multi-graded label set this population does not have.
Paired exact two-sided McNemar on the DISCORDANT ids at k=3, as `#12298` did.

HAZARDS IT REFUSES
------------------
(a) The fulltext index builds on a BACKGROUND cycle — a query straight after the
    writes books a present name as a miss (the wrong direction). Waits for
    ``numDocuments == n`` and REFUSES on timeout.
(b) ``Object.name`` is an ON CREATE write (`#8919`), so a reused graph silently
    measures a previous population. A graph whose stored names differ from the
    population is a REFUSAL, never a re-run.
(c) The dense leg is not installed on a plain lane (``sentence_transformers``
    absent), so the two FUSED surfaces are measured in their KEYWORD-ONLY shape
    — a real product shape, recorded in the artifact as ``dense_leg``.

HOW TO RUN
----------
    uv run python tools/underscore_or_terms_12360.py run \\
        --graph u12360-und${RANDOM} --out docs/research/2026-11-13-12360-underscore-or-terms/artifact.json

The graph is minted fresh by the caller (a random suffix) and closed on exit;
`--population-file` defaults to the committed pin.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POPULATION = (REPO_ROOT / "docs" / "research"
                      / "2026-11-13-12360-underscore-or-terms" / "population.json")

#: The widest window measured. recall@k for every k <= KMAX is a prefix of the
#: one ranking, so ONE query per (arm, surface, name) is enough — and it keeps
#: the arms comparable (a re-query per k would re-enter the read path k times).
KMAX = 15
KS = (1, 3, 5, 10, 15)

#: The production window of the capture prior (`_fts_rows` is called with 3 by
#: the capture pipeline) — the k to quote for that surface.
PRIOR_WINDOW = 3

SURFACES = ("fts_leg", "mcp_search", "capture_prior")
SURFACE_NOTES = {
    "fts_leg": "search_engine.run_fts_query, entity_type='object' — the FTS leg "
               "in isolation (#12298's published number)",
    "mcp_search": "sdk.tortoise_fts_query, entity_type='object' — the door "
                  "`tortoise_search` is registered to and REST /v1/search uses "
                  "(fused read)",
    "capture_prior": "extractor_v2._fts_rows, entity_type='object' — the "
                     "capture link-before-create prior (fused read); its "
                     "production window is 3",
}

FIXTURES = [
    # (stored name, query) — #12298 §3.5's identifier fixture, the acceptance
    # cases the shipped leg fails. `abc_def` returns ZERO rows for its own name.
    ("ERR-4032", "ERR-4032"),
    ("INV-2024-0091", "INV-2024-0091"),
    ("abc_def", "abc_def"),
    ("test_7949_capstone_verdict_check", "test_7949_capstone_verdict_check"),
    ("ERR-4032 retry", "ERR-4032"),
]


# ── population ─────────────────────────────────────────────────────────────

def _repo_relative(path: Path) -> str:
    """A repo-relative path for the artifact, never this box's worktree path."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_population(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    doc = json.loads(raw)
    # The pinned file's shape: {names: [{id, name, cls, records}, …], …}. The
    # ``population`` key is the HARVEST size (an int), not the sample — read the
    # sample by its own key and refuse a shape that is not it, rather than
    # guessing (a wrong key here would measure a different sample silently).
    if isinstance(doc, dict):
        row_key = next((k for k in ("names", "rows", "sample_rows")
                        if isinstance(doc.get(k), list)), None)
        if row_key is None:
            raise SystemExit(f"REFUSED: {path} carries no row list "
                             f"(keys: {sorted(doc)})")
        rows = doc[row_key]
    else:
        rows = doc
    if not rows or not all({"id", "name", "cls"} <= set(r) for r in rows):
        raise SystemExit(f"REFUSED: {path} rows need id/name/cls")
    return rows, hashlib.sha256(raw).hexdigest()


# ── the index cycle ────────────────────────────────────────────────────────

def wait_indexed(proj, expected: int, label: str = "Object",
                 timeout_s: float = 120.0) -> tuple[int, float]:
    """Block until the fulltext index reports ``expected`` documents.

    A timeout is a REFUSAL, never a proceed: measuring against a partially built
    index understates the leg, and an understated leg is indistinguishable from a
    real one in the artifact.
    """
    t0 = time.time()
    last = -1
    while time.time() - t0 < timeout_s:
        for row in proj.g.query("CALL db.indexes()").result_set:
            if not row or row[0] != label:
                continue
            # The LAST dict carrying ``numDocuments`` is the indexer's own stats.
            # Found BY KEY, never by position — a positional read is how a
            # version bump turns this guard into a silent no-op.
            stats = next((c for c in row
                          if isinstance(c, dict) and "numDocuments" in c), None)
            last = int(stats["numDocuments"]) if stats else -1
            break
        if last >= expected:
            return last, round(time.time() - t0, 2)
        time.sleep(0.2)
    raise SystemExit(
        f"REFUSED: the {label} fulltext index reports {last} documents of "
        f"{expected} after {timeout_s}s — a query against a partially built "
        f"index books present names as misses.")


# ── statistics ─────────────────────────────────────────────────────────────

def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p over the discordant pairs (b, c)."""
    n = b + c
    if n == 0:
        return 1.0
    # Two-sided exact binomial, p = 0.5, summed over |X - n/2| >= |b - n/2|.
    from math import comb
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def ndcg10(rank: int | None) -> float:
    """nDCG@10 with ONE relevant document (IDCG = 1 at rank 1)."""
    import math
    if rank is None or rank > 10:
        return 0.0
    return 1.0 / math.log2(rank + 1)


# ── the arms ───────────────────────────────────────────────────────────────

def _query_ids(surface: str, ctx: dict, query: str, limit: int) -> list[str]:
    if surface == "fts_leg":
        rows = ctx["run_fts_query"](ctx["graph"], query,
                                    entity_type="object", limit=limit)
        return [pid for pid, _s in rows]
    if surface == "mcp_search":
        rows = ctx["sdk"].tortoise_fts_query(query, entity_type="object",
                                             limit=limit)
        return [r.get("id", "") for r in rows or []]
    if surface == "capture_prior":
        rows = ctx["fts_rows"](ctx["sdk"], "object", query, limit=limit)
        return [r.get("id", "") for r in rows or []]
    raise AssertionError(surface)


def measure(rows: list[dict], graph, *,
            surface: str, arm: str, ctx: dict) -> dict:
    per_k = {k: {"in_window": 0, "rank_sum": 0, "rows_zero": 0} for k in KS}
    by_class: dict[str, dict] = collections.defaultdict(
        lambda: {"n": 0, **{str(k): 0 for k in KS}})
    ranks: dict[str, int | None] = {}
    ndcg: list[float] = []
    t0 = time.time()
    n_q = 0
    for row in rows:
        q = row["name"]
        out = _query_ids(surface, ctx, q, KMAX)
        n_q += 1
        by_class[row["cls"]]["n"] += 1
        if not out:
            for k in KS:
                per_k[k]["rows_zero"] += 1
        rank = out.index(row["id"]) + 1 if row["id"] in out else None
        ranks[row["id"]] = rank
        ndcg.append(ndcg10(rank))
        for k in KS:
            if rank is not None and rank <= k:
                per_k[k]["in_window"] += 1
                per_k[k]["rank_sum"] += rank
                by_class[row["cls"]][str(k)] += 1
    n = len(rows)
    return {
        "surface": surface,
        "arm": arm,
        "n": n,
        "recall": {str(k): round(per_k[k]["in_window"] / n, 4) for k in KS},
        "rows_zero": {str(k): per_k[k]["rows_zero"] for k in KS},
        "mean_rank": {str(k): (round(per_k[k]["rank_sum"] / per_k[k]["in_window"], 3)
                               if per_k[k]["in_window"] else None) for k in KS},
        "ndcg@10": round(sum(ndcg) / n, 4),
        "by_class": {c: {"n": v["n"],
                         **{str(k): round(v[str(k)] / v["n"], 4) for k in KS}}
                     for c, v in sorted(by_class.items())},
        "ranks": ranks,
        "queries": n_q,
        "elapsed_s": round(time.time() - t0, 2),
        "note": SURFACE_NOTES[surface],
    }


def paired(before: dict, after: dict, k: int = 3) -> dict:
    """Paired McNemar on the discordant ids, for ONE k."""
    b = c = 0
    gains: list[str] = []
    losses: list[str] = []
    for pid, rb in before["ranks"].items():
        ra = after["ranks"].get(pid)
        hit_b = rb is not None and rb <= k
        hit_a = ra is not None and ra <= k
        if hit_a and not hit_b:
            b += 1
            gains.append(pid)
        elif hit_b and not hit_a:
            c += 1
            losses.append(pid)
    delta = (after["recall"][str(k)] - before["recall"][str(k)]) * 100
    p = mcnemar_exact(b, c)
    return {"k": k, "gains": b, "losses": c, "delta_pts": round(delta, 2),
            "p": p,
            # 2*(0.5**75) is ~5e-23: a float that renders as 0.0 at any sane
            # rounding. The bound is the honest read and it is what a reviewer
            # will quote, so it is carried as its own field rather than
            # silently printed as `p=0`.
            "p_display": (f"<1e-{int(-__import__('math').log10(p))}"
                          if 0 < p < 1e-12 else f"{p:.4g}"),
            "gain_ids": gains[:20], "loss_ids": losses[:20]}


# ── main ───────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    from tortoise.extractor_v2 import _fts_rows
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK
    from tortoise.search_engine import run_fts_query
    from tortoise.sparse import UNDERSCORE_ENV, underscore_run_terms

    pop_path = Path(args.population_file)
    rows, pop_sha = load_population(pop_path)
    # Re-key the pinned ids under this tool's namespace. The pin's ids are
    # `m3-…` (PR #12349's graph); a distinct prefix is what makes the
    # fresh-graph guard below, and the stale-name check after the writes,
    # answer questions about THIS run's rows rather than a peer's.
    rows = [{"id": f"u12360-{i:06d}", "name": r["name"], "cls": r["cls"],
             "records": r.get("records")} for i, r in enumerate(rows)]

    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        raise SystemExit("REFUSED: set TORTOISE_DB_URI (or use "
                         "`uv run python tools/test_lane.py run -- ...`)")

    proj = FalkorProjection.from_uri(uri, graph_name=args.graph)
    sdk = TortoiseSDK(namespace=args.namespace, graph_name=args.graph)

    # (b) `o.name` is an ON CREATE write — a reused graph silently measures a
    # previous population. Refuse rather than report a stale number.
    existing = proj.g.query(
        "MATCH (o:Object) WHERE NOT o.id STARTS WITH 'u12360-' "
        "RETURN count(o)").result_set
    if existing and int(existing[0][0] or 0) > 0:
        raise SystemExit(
            f"REFUSED: graph {args.graph!r} already holds "
            f"{existing[0][0]} non-fixture Object rows — pass a FRESH --graph "
            f"(names are ON CREATE, #8919)")

    for row in rows:
        proj._upsert_object({"id": row["id"], "name": row["name"],
                             "object_kind": "core:other"})
    stored = {r[0]: r[1] for r in proj.g.query(
        "MATCH (o:Object) WHERE o.id STARTS WITH 'u12360-' RETURN o.id, o.name",
        params={}).result_set}
    stale = [r["name"] for r in rows if stored.get(r["id"]) != r["name"]]
    if stale:
        raise SystemExit(
            f"REFUSED: {len(stale)} of {len(rows)} names did not land (first: "
            f"{stale[:3]!r}) — `o.name` is ON CREATE (#8919); pass a fresh --graph")

    # One warm query so the first measured query does not pay the cold
    # full-scan cost (the #4997 unindexed-label scan), then WAIT for the
    # background index cycle.
    run_fts_query(proj.g, "warm", entity_type="object", limit=1)
    indexed, waited = wait_indexed(proj, len(rows))

    ctx = {"graph": proj.g, "sdk": sdk, "run_fts_query": run_fts_query,
           "fts_rows": _fts_rows}

    result: dict = {
        "issue": "12360",
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "graph": args.graph,
        "population": {"path": _repo_relative(pop_path), "sha256": pop_sha,
                       "n": len(rows),
                       "classes": dict(collections.Counter(r["cls"] for r in rows)),
                       "source": "#12298's pinned 300-name sample (verbatim copy)"},
        "index": {"label": "Object", "numDocuments": indexed,
                  "wait_s": waited},
        "ks": list(KS), "kmax": KMAX,
        "dense_leg": "unavailable on this box (sentence_transformers not "
                     "installed) — the two fused surfaces are measured in their "
                     "KEYWORD-ONLY shape, a real product shape",
        "arms": {},
        "fixtures": {},
    }

    # ── the paired sweep: OFF then ON, same graph, same ids, same process ──
    for surface in SURFACES:
        for arm, flag in (("off", "0"), ("on", "1")):
            os.environ[UNDERSCORE_ENV] = flag
            result["arms"].setdefault(surface, {})[arm] = measure(
                rows, proj.g, surface=surface, arm=arm, ctx=ctx)

    # ── the #8933 identifier fixture, AFTER the sweep so it cannot move a
    #    document frequency under a measured query ──
    for i, (stored_name, _q) in enumerate(FIXTURES):
        proj._upsert_object({"id": f"u12360-fx-{i}", "name": stored_name,
                             "object_kind": "core:other"})
    wait_indexed(proj, len(rows) + len(FIXTURES))
    for surface in SURFACES:
        per_arm: dict[str, list] = {}
        for arm, flag in (("off", "0"), ("on", "1")):
            os.environ[UNDERSCORE_ENV] = flag
            fx = []
            for i, (stored_name, q) in enumerate(FIXTURES):
                out = _query_ids(surface, ctx, q, PRIOR_WINDOW)
                wide = _query_ids(surface, ctx, q, 15)
                fid = f"u12360-fx-{i}"
                fx.append({"stored": stored_name, "query": q,
                           "rank": out.index(fid) + 1 if fid in out else None,
                           "rank_wide": wide.index(fid) + 1 if fid in wide else None,
                           # ⛔ The switch decides this, or the artifact lies:
                           # recording the composite for the OFF arm would say
                           # "it was sent and still missed", which inverts the
                           # finding.
                           "terms_sent": (underscore_run_terms(q)
                                          if os.environ[UNDERSCORE_ENV] == "1"
                                          else [])})
            per_arm[arm] = fx
        result["fixtures"][surface] = {
            "stored_names": [s for s, _q in FIXTURES],
            "off": per_arm["off"], "on": per_arm["on"],
            "reachable": {
                a: sum(1 for r in per_arm[a] if r["rank_wide"] is not None)
                for a in ("off", "on")},
        }

    # ── the paired comparison at every k, per surface ──
    for surface in SURFACES:
        result["arms"][surface]["paired"] = {
            str(k): paired(result["arms"][surface]["off"],
                           result["arms"][surface]["on"], k)
            for k in KS}

    # ── the falsifier, stated as a verdict so the artifact answers it ──
    verdict: dict = {}
    for surface in SURFACES:
        k = PRIOR_WINDOW if surface == "capture_prior" else 3
        p = result["arms"][surface]["paired"][str(k)]
        verdict[surface] = {
            "k": k,
            "delta_pts": p["delta_pts"],
            "p": p["p"],
            "p_display": p["p_display"],
            "regressions": p["losses"],
            "passes_10pts": p["delta_pts"] >= 10.0,
            "zero_regressions": p["losses"] == 0,
        }
    verdict["falsifier"] = (
        "#12360: PASS iff candidate recall moves >= +10 pts on the real path AND "
        "the movement is visible on BOTH product surfaces. Judged on each "
        "surface's own production window (mcp_search/prior k=3, fts_leg k=3).")
    verdict["passes"] = bool(
        verdict["mcp_search"]["passes_10pts"]
        and verdict["capture_prior"]["passes_10pts"])
    verdict["query_population_substitution"] = (
        "The contract asks for the #11901 corpus on the capture prior and the "
        "DECLARED USER-SEARCH QUERY SET on the MCP/SDK door. That declared set "
        "contains NO `_` query, so the mechanism cannot move it — and must not "
        "(byte-identity). The gain is therefore measured with the #11901 name "
        "population on BOTH doors (the substitution), and the declared set is "
        "measured for INERTNESS on both doors (identical ordered ids, identical "
        "emitted bytes) rather than for a gain it structurally cannot have.")
    result["verdict"] = verdict

    # ── the declared user-search query set: inertness, not a gain (see the
    #    function's docstring for why that is the CORRECT answer there) ──
    try:
        result["user_search_set"] = measure_declared_user_search(ctx)
    except Exception as exc:
        result["user_search_set"] = {"available": False,
                                     "error": f"{type(exc).__name__}: {exc}"}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")

    # ── the human render ──
    print(f"population: {len(rows)} names  sha256:{pop_sha[:16]}  "
          f"index numDocuments={indexed} (waited {waited}s)")
    for surface in SURFACES:
        print(f"\n── {surface}  ({SURFACE_NOTES[surface]})")
        for arm in ("off", "on"):
            a = result["arms"][surface][arm]
            rec = " ".join(f"@{k}={a['recall'][str(k)]:.4f}" for k in KS)
            print(f"  {arm:>3}: {rec}  nDCG@10={a['ndcg@10']:.4f}  "
                  f"zero_rows@15={a['rows_zero']['15']}  ({a['elapsed_s']}s)")
        for k in KS:
            p = result["arms"][surface]["paired"][str(k)]
            print(f"   k={k:<2} Δ={p['delta_pts']:+.2f} pts  "
                  f"gains={p['gains']:<3} losses={p['losses']:<3} "
                  f"p={p['p_display']}")
        fx = result["fixtures"][surface]
        print(f"   identifier fixture reachable@15: off={fx['reachable']['off']}/5 "
              f"on={fx['reachable']['on']}/5")
    print("\nVERDICT:")
    for surface in SURFACES:
        v = verdict[surface]
        print(f"  {surface}: Δ={v['delta_pts']:+.2f} pts @k={v['k']}, "
              f"p={result['arms'][surface]['paired'][str(v['k'])]['p_display']}, "
              f"regressions={v['regressions']}, "
              f">=10pts={v['passes_10pts']}")
    print(f"  passes={verdict['passes']}")
    uss = result.get("user_search_set") or {}
    if uss.get("available"):
        print(f"\n── declared user-search query set ({uss['n']} queries, "
              f"{len(uss['with_underscore'])} with `_`):")
        for door, row in uss["arms"].items():
            print(f"   {door:>13}: identical ordered ids on "
                  f"{row['identical_rank_vectors']}/{row['n']}  "
                  f"Δ={row['delta_pts']}")
        print(f"   emitted query bytes identical: "
              f"{uss['emitted_bytes_identical']}/{uss['n']}")
    else:
        print(f"\n── declared user-search query set: UNAVAILABLE "
              f"({(uss or {}).get('error')})")
    print(f"\nwrote {out}")
    return 0


# ── the declared user-search query set (the contract's OTHER surface) ─────

def measure_declared_user_search(ctx: dict) -> dict:
    """The DECLARED user-search query set, both arms, both doors.

    `#12360` asks for two surfaces: *"the `#11901` corpus for the capture prior,
    and **the declared user-search query set** for MCP/SDK search"*. The set is
    the repo's own (`tests/eval/retrieval/queries.py::AUTHORED_QUERIES`, the
    `#1144` authored slice: 50 natural-language queries over this product's
    real domain).

    ⚠️ What this CAN and CANNOT report, stated rather than implied:

    * It **cannot** report a recall@k GAIN, because none of these queries is
      identifier-shaped (`_`-carrying), so the mechanism has nothing to recover
      — and that is the CORRECT result: the change is byte-identical for every
      query without an inner `_`. A recall number here would be a score over
      labels this lane does not hold.
    * It **can** report, and does, the thing that matters for that surface: the
      two arms return the **identical ordered id list** for every query, on both
      doors, and the emitted query BYTES are identical — so recall@k and
      nDCG@10 are equal for ANY labelling whatsoever, without needing the labels.
      That is a proof of inertness, not an absence of a measurement.
    * The gain this issue is about is therefore measured on the population that
      CONTAINS the shapes it repairs (`#11901`'s 300 names) — recorded as
      ``query_population_substitution`` in the artifact, because the contract's
      two-surface wording assumes the declared set would exercise the mechanism
      and it does not.
    """
    from tortoise.sparse import UNDERSCORE_ENV, build_or_query

    try:
        from tests.eval.retrieval.queries import AUTHORED_QUERIES as decl
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    queries = [q["query"] if isinstance(q, dict) else str(q) for q in decl]
    out: dict = {
        "available": True,
        "source": "tests/eval/retrieval/queries.py::AUTHORED_QUERIES (#1144)",
        "n": len(queries),
        "with_underscore": [q for q in queries if "_" in q],
        "arms": {},
    }
    per_arm: dict[str, list[list[str]]] = {}
    bytes_identical = 0
    for arm, flag in (("off", "0"), ("on", "1")):
        os.environ[UNDERSCORE_ENV] = flag
        per_arm[arm] = [_query_ids("mcp_search", ctx, q, 15) for q in queries]
    for q in queries:
        os.environ[UNDERSCORE_ENV] = "0"
        before = build_or_query(q)
        os.environ[UNDERSCORE_ENV] = "1"
        if build_or_query(q) == before:
            bytes_identical += 1
    for door in ("mcp_search", "capture_prior"):
        if door == "capture_prior":
            for arm, flag in (("off", "0"), ("on", "1")):
                os.environ[UNDERSCORE_ENV] = flag
                per_arm[f"prior_{arm}"] = [
                    [r.get("id", "") for r in (ctx["fts_rows"](
                        ctx["sdk"], "object", q, limit=15) or [])]
                    for q in queries]
            off, on = per_arm["prior_off"], per_arm["prior_on"]
        else:
            off, on = per_arm["off"], per_arm["on"]
        identical = sum(1 for a, b in zip(off, on, strict=True) if a == b)
        out["arms"][door] = {
            "n": len(queries),
            "identical_rank_vectors": identical,
            "delta_pts": 0.0 if identical == len(queries) else None,
            "note": ("identical ordered ids on every query -> recall@k and "
                     "nDCG@10 are equal for any labelling"),
        }
    out["emitted_bytes_identical"] = bytes_identical
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="measure both arms on both surfaces")
    r.add_argument("--graph", required=True)
    r.add_argument("--namespace", default="u12360")
    r.add_argument("--population-file", default=str(DEFAULT_POPULATION))
    r.add_argument("--out", required=True)
    r.set_defaults(func=run)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
