#!/usr/bin/env python3
"""Measure the reflex hook's injected payload: depth, and what it costs (#14607).

WHY A SCRIPT AND NOT A LINE IN THE PR BODY. The owner's requirement is *"not too
shallow … we want a bit of context around our entities (some of the business
logic)"*, and the counter-requirement is #14275's one token budget — the hook
spends it on EVERY prompt. A claim about either one that is not measured is a
claim a reviewer cannot check, and the next change to `build_block` would move the
number silently.

So this prints, for a REAL graph built by the product's own SDK:

* the GATE's decision on prompts that need no memory (must be a no-op, not an
  empty block);
* the candidate pool K and the condensation rule, from the module's own constants;
* the full injected block for a real entity — the entity, its relations WITH
  their predicates, and the source excerpt (the acceptance artifact);
* p50 / p95 injected tokens over a prompt set, with the raw bytes beside the
  estimate so the number can be re-derived with any tokenizer.

Offline: an embedded FalkorDB file in a temp dir. No hosted call, no key.

    uv run python tools/measure_recall_tokens.py          # human-readable
    uv run python tools/measure_recall_tokens.py --json   # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import recall_neighborhood
from tortoise.sdk import TortoiseSDK
from tortoise.volunteer import (
    DEFAULT_MAX_POINTERS,
    RESOLVE_POOL_MAX,
    RESOLVE_POOL_MIN,
)

#: Prompts that SHOULD fire, and one set that should NOT. The no-fire set is not
#: decoration: a gate that fires on "thanks" spends the shared budget on nothing,
#: and the comparables' first decision is whether memory is needed at all.
FIRING_PROMPTS = [
    "What's the status of the Acme security review?",
    "Has the Acme security review shipped yet?",
    "Why did we pick FalkorDB over Neo4j for the storage layer?",
    "What blocks the billing migration?",
    "Who owns the extractor rewrite, and what does it depend on?",
    "What did the pricing decision end up being, and what supports it?",
]
QUIET_PROMPTS = [
    "thanks!",
    "ok",
    "sounds good, continue",
]


def build_graph(sdk: TortoiseSDK) -> dict:
    """A small, realistic slice: two decisions, their supports, a conflict, quotes."""
    def point(kind: str, content: str, quote: str | None = None) -> dict:
        created = sdk._create_point(kind, content)
        if quote:
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) SET n.quote=$q",
                params={"id": created["id"], "q": quote})
        return created

    def posterior(pid: str, alpha: float, beta: float) -> None:
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
            "n.posterior_alpha=$a, n.posterior_beta=$b",
            params={"id": pid, "a": alpha, "b": beta,
                    "c": round(alpha / (alpha + beta), 4)})

    # Decision 1 — the storage choice, with two supports and one attack.
    storage = point(
        "decision", "We chose FalkorDB as the graph store for the tortoise memory layer",
        "Decision log, 2026-08-14: after benchmarking both, we selected FalkorDB "
        "because its Redis lineage keeps ops simple and its Cypher dialect covers "
        "the traversals the extractor emits.")
    bench = point("evidence", "FalkorDB latency benchmark: p95 41 ms on the 2-hop traversal corpus",
                  "Benchmarks/graph-traversal-2026-08/results.md: FalkorDB p95 41 ms "
                  "versus Neo4j p95 96 ms on the same 10k-run corpus.")
    ops = point("evidence", "FalkorDB deploys as a single container with no JVM tuning needed",
                "Runbook: the FalkorDB container starts in 2 s and needs no heap "
                "configuration, unlike the JVM-based alternative.")
    neo4j_risk = point("statement", "Neo4j's JVM heap tuning consumed two engineer-days during the spike",
                       "Incident note: heap tuning on the Neo4j spike took two "
                       "engineer-days and was still unstable under load.")
    sdk._create_operator("IMPL", bench["id"], [storage["id"]])
    sdk._create_operator("IMPL", ops["id"], [storage["id"]])
    sdk._create_operator("NAND", neo4j_risk["id"], [storage["id"]])
    posterior(storage["id"], 14.0, 2.0)
    posterior(bench["id"], 12.0, 2.0)
    posterior(ops["id"], 10.0, 2.0)
    posterior(neo4j_risk["id"], 6.0, 3.0)

    # Decision 2 — Acme renewal: the contested one (needs a counter-argument shown).
    acme = point("decision", "Acme renewal is due May 1 and the security review has not shipped",
                 "CRM note, 2026-04-22: renewal call booked for May 1; the security "
                 "questionnaire is still with Acme's infosec team, so we cannot "
                 "confirm the review has shipped.")
    shipped = point("statement", "Acme's security review shipped on April 30 per the release log",
                    "Release log, 2026-04-30: security review v2 delivered to Acme.")
    sdk._create_operator("NAND", shipped["id"], [acme["id"]])
    posterior(acme["id"], 5.0, 2.0)
    posterior(shipped["id"], 9.0, 1.0)

    # A third entity so the pool is not trivially small: the pricing decision.
    pricing = point("decision", "Pricing decision: usage-based tiers instead of per-seat billing",
                    "Pricing memo: per-seat billing was rejected because agent fleets "
                    "have wildly different call volumes per seat.")
    competitor = point("evidence", "Three competitors bill per-seat and all three publish overage complaints",
                       "Competitive teardown: per-seat overage complaints appear in all "
                       "three vendors' public issue trackers.")
    sdk._create_operator("IMPL", competitor["id"], [pricing["id"]])
    posterior(pricing["id"], 11.0, 2.0)
    posterior(competitor["id"], 8.0, 2.0)

    return {"storage": storage["id"], "acme": acme["id"], "pricing": pricing["id"]}


def run(prompts: list[str], *, sdk: TortoiseSDK) -> list[dict]:
    rows: list[dict] = []
    for prompt in prompts:
        window = [{"role": "user", "content": prompt}]
        result = sdk._volunteer_context(window, session_id="measure-14607", why=True)
        block = result.get("block") or ""
        rows.append({
            "prompt": prompt,
            "pointers": len(result.get("pointers") or []),
            "block": block,
            **recall_neighborhood.measure(block),
        })
    return rows


def percentile(values: list[int], fraction: float) -> int:
    """Nearest-rank percentile — no interpolation, no hidden assumption."""
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1,
                       round(fraction * (len(ordered) - 1))))
    return ordered[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args(argv)

    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_14607_"), "measure.db")
    sdk = TortoiseSDK(db_path)
    ids = build_graph(sdk)

    fired = run(FIRING_PROMPTS, sdk=sdk)
    quiet = run(QUIET_PROMPTS, sdk=sdk)
    firing_rows = [r for r in fired if r["pointers"]]
    tokens = [r["tokens"] for r in firing_rows]

    # The acceptance artifact: the FULL block for the entity we planted.
    artifact = next(
        (r for r in fired
         if any(f"point/{ids['acme']}" in (r["block"] or "") for _ in (0,))),
        None,
    ) or (firing_rows[0] if firing_rows else None)

    report = {
        "candidate_pool": {
            "k_min": RESOLVE_POOL_MIN,
            "k_max": RESOLVE_POOL_MAX,
            "k_default": max(RESOLVE_POOL_MIN,
                             min(RESOLVE_POOL_MAX, DEFAULT_MAX_POINTERS * 6)),
            "condensed_to": DEFAULT_MAX_POINTERS,
            "source": ("Supermemory: conventional top-K is 10-20, aggregated into "
                       "2-3 synthesized results"),
        },
        "budget": {
            "token_budget": recall_neighborhood.NEIGHBORHOOD_TOKEN_BUDGET,
            "max_relations_per_pointer":
                recall_neighborhood.MAX_RELATIONS_PER_POINTER,
            "excerpt_max_chars": recall_neighborhood.EXCERPT_MAX_CHARS,
            "estimator": "ceil(utf8_bytes / 4)",
        },
        "fired": [{k: r[k] for k in ("prompt", "pointers", "tokens", "bytes",
                                     "chars")} for r in fired],
        "quiet": [{k: r[k] for k in ("prompt", "pointers", "tokens")}
                  for r in quiet],
        "tokens": {
            "n": len(tokens),
            "p50": percentile(tokens, 0.50),
            "p95": percentile(tokens, 0.95),
            "mean": int(statistics.fmean(tokens)) if tokens else 0,
            "max": max(tokens) if tokens else 0,
        },
        "artifact": artifact,
    }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print("# #14607 · recall payload measurement\n")
    pool = report["candidate_pool"]
    print(f"Candidate pool K        : {pool['k_min']}..{pool['k_max']} "
          f"(default {pool['k_default']})  — {pool['source']}")
    print(f"Condensed to            : {pool['condensed_to']} items "
          f"(the comparables' 2-3 synthesis step)")
    budget = report["budget"]
    print(f"Token budget (estimate) : {budget['token_budget']}  "
          f"[estimator {budget['estimator']}, "
          f"{budget['max_relations_per_pointer']} relations/pointer, "
          f"{budget['excerpt_max_chars']}-char excerpt]")
    print()
    print("## The gate: prompts that should not spend the budget")
    for row in report["quiet"]:
        status = "no-op (no block)" if not row["pointers"] else "FIRED"
        print(f"  {row['prompt']!r:28} → {row['pointers']} pointers, "
              f"{row['tokens']} tokens — {status}")
    print()
    print("## Firing prompts")
    for row in report["fired"]:
        print(f"  {row['tokens']:>4} tok ({row['bytes']:>5} B) · "
              f"{row['pointers']} ptr · {row['prompt']}")
    stats = report["tokens"]
    print()
    print(f"p50 = {stats['p50']} tokens · p95 = {stats['p95']} tokens · "
          f"mean = {stats['mean']} · max = {stats['max']} · n = {stats['n']}")
    if artifact:
        print()
        print("## Full injected block for a real entity (the depth artifact)\n")
        print("```")
        print(artifact["block"])
        print("```")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
