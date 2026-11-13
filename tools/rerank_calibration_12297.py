#!/usr/bin/env python3
"""#12297 — does the cross-encoder rerank earn its latency on USER SEARCH?

The noun-matching prior is calibrated by ``tools/candidate_recall_11901.py
--rerank`` (the real capture corpus, the real fused read). This tool covers the
SECOND surface the issue names — user search — and it is deliberately a separate
harness, because the metric is different: the noun path asks "is the right
candidate in the list", user search asks "is the right passage FIRST".

WHAT IS REAL AND WHAT IS CONSTRUCTED — stated before any number
---------------------------------------------------------------
**Real:** every passage written to the graph is a real captured turn from this
box's own corpus (``~/.tortoise/session-events``); every retrieval call is the
shipped ``tortoise_fts_query`` on a real FalkorDB, through the real fusion.

**Constructed, and this is the honest gap:** this box has **no fixed
user-search query set**. The corpus holds agent-harness turns, and its
question-shaped turns are conversational follow-ups (``"Now the CI failures: are
those two green?"``) that carry no standalone referent — they are not answerable
against a haystack, so using them would measure noise. The query set is therefore
**built from the gold passage itself**, by the two declared rules below, exactly
as #11901 declares its 419 variant pairs GENERATED. The consequence is stated
rather than left for a reader to infer:

  ⚠️ A query drawn from its own gold passage is an EASIER task than a real user
  question. What it measures is **the reranker's ability to promote the correct
  passage above near-duplicates the retrieval legs already ranked ahead of it** —
  which is precisely the lever #12297 asks about, and nothing more. It is NOT an
  estimate of production search quality and it is NOT comparable to any
  published retrieval benchmark.

THE TWO QUERY RULES (declared, deterministic, in ``build_queries``)
-------------------------------------------------------------------
``prefix``   the FIRST ``--span`` words of the gold passage — "I remember how it
             starts".
``midspan``  a ``--span``-word window from the MIDDLE — deliberately not the
             opening, so a reranker that only rewards prefix overlap cannot pass
             on the prefix case alone.

Both are the same fixed query set on every run, so before/after is paired.

METRICS
-------
``ndcg@10``  1/log2(rank+2) for the 0-based ``rank`` this code carries, one gold
per query (IDCG = 1). A query whose gold
             never entered the pool scores 0 and STAYS in the denominator —
             dropping it would let a reranker raise nDCG by losing queries.
``recall@10`` gold present in the first 10.
``mrr``      mean 1/(rank+1), 0 when absent.
``gained`` / ``lost``  the paired counterfactual on the SAME pool, so a net
             delta cannot be explained by a population difference.

⛔ The measured configuration is the WIRED one, not a second implementation:
the pool is fetched at ``--limit 40`` (== ``rerank.DEFAULT_FUSED_RERANK_POOL``,
and the first 40 of the pool in fused order == ``result_ids[:40]`` in
``tortoise_fts_query``), ``rerank.rerank_pool`` reorders it, and the cut is the
caller's own ``[:10]``.

USAGE
    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"                      # two steps on purpose — see test_lane's USAGE
    uv sync --extra embeddings --extra parity
    uv run python tools/rerank_calibration_12297.py --records 400 \\
        --out /tmp/rerank-user-search-12297.json

EXIT CODES: 0 ok · 2 usage · 3 REFUSED — a mis-shaped run, never a
silent skip: either the read's own rerank gate is armed (the baseline would
already be reranked, so the comparison is rerank-vs-rerank — pass
`--allow-reranked-baseline` to measure that on purpose) or no reachable
FalkorDB is configured. Not exhaustive — an empty harvest, passages that did not
land, and a cross-encoder that will not load also refuse with 3
"""
from __future__ import annotations

import sys

# #5128 (the repo's entry-point contract): refuse a <3.12 interpreter HERE,
# before the rest of the imports, so a stale local `python3` fails LOUDLY.
if sys.version_info < (3, 12):  # noqa: UP036 -- intentional RUNTIME guard
    print(
        f"tools/rerank_calibration_12297.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/rerank_calibration_12297.py`",
        file=sys.stderr,
    )
    raise SystemExit(70)

import argparse
import contextlib
import glob
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: The cut a caller asks for. 10 is the issue's named metric (nDCG@10).
CUT = 10
#: The pool width the WIRED path scores (``rerank.DEFAULT_FUSED_RERANK_POOL``).
POOL = 40
#: A passage must be long enough to yield a query and short enough to read.
MIN_CHARS, MAX_CHARS = 120, 900
#: …and long enough in WORDS to be a passage rather than a fragment. Named, not
#: an inline literal, because the brief's cost table describes this population
#: and a restated floor there cannot be derived from a bare ``40``.
MIN_WORDS = 40
#: Fixed query-set size per construction rule.
QUERY_RULES: tuple[str, ...] = ("prefix", "midspan")

_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_'./-]*")


def harvest_passages(sessions_dir: Path, limit: int) -> list[dict]:
    """Real captured turns -> reading passages, deterministically ordered.

    ⛔ One class of turn is EXCLUDED on purpose: the harness's own dispatch
    briefs and tool transcripts. They are long, templated and mutually
    near-identical, so a pool built mostly from them would measure duplicate
    suppression rather than retrieval — a population property, not a reranker
    property. The filter is the declared one below, not a hand-pick.
    """
    out: list[dict] = []
    for path in sorted(glob.glob(str(sessions_dir / "*.jsonl"))):
        try:
            fh = open(path, encoding="utf-8", errors="replace")  # noqa: SIM115
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                for msg in (rec.get("conversation") or []):
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("role") != "assistant":
                        continue
                    text = (msg.get("content") or "").strip()
                    if not (MIN_CHARS <= len(text) <= MAX_CHARS):
                        continue
                    if "```" in text or "`" in text[:40]:
                        continue
                    words = _WORD.findall(text)
                    if len(words) < MIN_WORDS:
                        continue
                    out.append({
                        "content": text,
                        "session_id": str(rec.get("session_id") or ""),
                        "n_words": len(words),
                    })
    # Deterministic order: by content digest, so the sample does not depend on
    # filesystem order and two runs sample the SAME population.
    out.sort(key=lambda r: hashlib.sha256(
        r["content"].encode()).hexdigest())
    # De-duplicate by digest prefix of the normalised text — the corpus repeats
    # whole turns across records, and a pool of copies would inflate every hit.
    seen: set[str] = set()
    uniq: list[dict] = []
    for row in out:
        key = hashlib.sha256(
            re.sub(r"\s+", " ", row["content"].lower()).encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(row)
        if len(uniq) >= limit:
            break
    return uniq


def build_queries(passage: str, span: int) -> dict[str, str]:
    """The two DECLARED query constructions — deterministic, no randomness."""
    words = _WORD.findall(passage)
    if len(words) < span * 3:
        return {}
    prefix = " ".join(words[:span])
    mid = (len(words) - span) // 2
    return {"prefix": prefix, "midspan": " ".join(words[mid:mid + span])}


def _ndcg(rank: int | None, k: int = CUT) -> float:
    if rank is None or rank >= k:
        return 0.0
    return 1.0 / math.log2(rank + 2)


def _rr(rank: int | None, k: int = CUT) -> float:
    if rank is None or rank >= k:
        return 0.0
    return 1.0 / (rank + 1)


def _arm_block() -> dict:
    return {"queries": 0, "pool_absent": 0, "rerank_skipped_read": 0,
            "recall_at_10_before": 0, "recall_at_10_after": 0,
            "ndcg10_before": 0.0, "ndcg10_after": 0.0,
            "mrr_before": 0.0, "mrr_after": 0.0,
            "gained": 0, "lost": 0,
            "mean_rank_before": 0.0, "mean_rank_after": 0.0,
            "_rank_sum_before": 0, "_rank_sum_after": 0,
            "_found_before": 0, "_found_after": 0}


#: Reasons ``rerank_pool`` reports BEFORE the scorer is consulted: the read
#: handed it nothing to score. They are READ outcomes, not rerank failures, and
#: the instrument already models them — the gold stays in the denominator,
#: ``_ndcg(None)`` is 0, and the block counts ``pool_absent``. Refusing on them
#: would abort a ~46-minute run over a single query whose derived text matched
#: nothing, and would blame the reranker for what the read returned.
#: ⛔ ``"disabled"`` is NOT a member: it is a GATE state, not a read outcome, and
#: this set exists to say "the read handed the scorer nothing" — the reason the
#: instrument may count rather than abort. ``score_pool`` forces ``enabled=True``
#: (a calibration run measures the lever, it does not ask whether it is armed), so
#: the member was unreachable — and it was the FAIL-OPEN member: the day that call
#: site stopped forcing the gate, every query of a gate-off run would carry
#: ``"disabled"``, be tolerated as a read outcome, and emit an artifact of unmixed
#: before-only figures with no refusal at all. A dead member that would invert the
#: guard if it ever lived is worse than no member.
READ_SIDE_DEGRADES = frozenset({"empty_pool", "empty_query"})


def score_pool(query: str, rows: list[dict], *, model: str,
               limit: int) -> tuple[list[dict], dict]:
    """The TIMED call, in one place so the model it is handed is pinnable.

    ⛔ A seam rather than an inline call: the claim "the load check and the timed
    call use the SAME resolved id" was pinned by grepping ``run``'s source, and
    that was satisfied by a docstring naming the identifiers (measured) — the
    artifact then recorded the resolved id behind an ``ms_per_pair`` produced by
    a different model. Imported here so a test can patch
    ``tortoise.rerank.rerank_pool`` and read the kwarg.
    """
    from tortoise.rerank import rerank_pool
    return rerank_pool(query, rows, text_key="content",
                       pool=limit, enabled=True, model=model)


def record_stats(block: dict, stats: dict) -> None:
    """Count a READ-side skip on the arm; a scorer degrade is refused elsewhere.

    A pure helper, because the alternative pin was "the increment statement
    exists" — and inverting the guard to ``if applied and <read-side>`` made the
    counter unreachable while every test stayed green (measured). Tolerating a
    read-side skip without counting it is a silent hole: the arm would report a
    query it never scored as if the reranker had run and changed nothing.
    """
    if stats.get("applied"):
        return
    if (stats.get("degrade_reason") or "") in READ_SIDE_DEGRADES:
        block["rerank_skipped_read"] += 1


def resolve_arm_model(args) -> str:
    """The cross-encoder this run measures — the one the PRODUCT would load.

    ⛔ ``args.model`` is None on an ordinary run, and the SCORING call resolves
    its own default through ``rerank_pool`` → ``load_scorer(model or
    fused_rerank_model())``, which honours ``TORTOISE_FUSED_RERANK_MODEL``.
    Writing ``args.model or RERANK_MODEL_DEFAULT`` into the artifact therefore
    records a name the scorer may not have loaded when that knob is set — an
    artifact that misnames its model is worse than one that says nothing.

    A function so the pin on it is BEHAVIOURAL: a source grep is satisfied by the
    prose above (measured), and a test of this helper alone is satisfied by a
    hard-coded id at the call site (measured in the sibling tool), so both are
    pinned.
    """
    if args.model:
        return args.model
    from tortoise.rerank import fused_rerank_model
    return fused_rerank_model()


def rerank_degrade_refusal(stats: dict) -> str | None:
    """The stderr line when the arm DEGRADED, else ``None``.

    ⛔ ``rerank_pool`` returns ``applied: False`` — the order untouched — on a
    score exception, a length-mismatched score vector, a non-numeric or
    non-finite score. A degraded pass is still a PERMUTATION, so the wiring
    asserts below pass, and the arm then reports ``gained 0 / lost 0`` with
    identical nDCG: indistinguishable in the artifact from a real zero, on the
    one instrument whose job is to decide whether the lever earns its gate. The
    ambient-gate refusal exists because of exactly this shape; a degrade needs
    the same treatment, and the scorer LOADING successfully does not prove it
    scored anything.

    A pure function so the refusal is testable without a store — the run path
    needs a DB before it can reach a real degrade.
    """
    if stats.get("applied"):
        return None
    if (stats.get("degrade_reason") or "") in READ_SIDE_DEGRADES:
        return None
    return (f"REFUSED: the rerank DEGRADED on this query "
            f"({stats.get('degrade_reason') or 'no reason given'}) — the arm did "
            f"not measure the reranker. A degraded pass leaves the order "
            f"untouched, so it would report a gained/lost 0 that looks exactly "
            f"like a real one, and the permutation asserts cannot see it.")


def run(args: argparse.Namespace) -> int:
    # ⛔ THE AMBIENT GATE WOULD SILENTLY FALSIFY THE BASELINE, and this check runs
    # FIRST because it needs no store — a mis-shaped run should fail before paying
    # for one. "Before" is the read's own fused order; the arm then reranks it
    # explicitly (``enabled=True``). If ``TORTOISE_FUSED_RERANK`` is armed in the
    # shell, the read has ALREADY reranked the pool, so "before" and "after" are
    # two rerank passes and the artifact reports gained/lost 0 with identical
    # nDCG — a false zero that looks exactly like a real one, on an instrument
    # whose whole job is to decide whether the lever earns its gate. Refuse, in
    # the tool's own declared style (never a silent skip), and record the gate in
    # the artifact so an intended armed run stays auditable.
    from tortoise.rerank import rerank_pool_enabled

    _ambient_gate = rerank_pool_enabled()
    if _ambient_gate and not args.allow_reranked_baseline:
        print("REFUSED: TORTOISE_FUSED_RERANK is armed, so the read's own order "
              "is ALREADY reranked and \"before\" is not the fused baseline — "
              "the run would report a rerank-vs-rerank zero. Unset it "
              "(TORTOISE_FUSED_RERANK=) or pass --allow-reranked-baseline to "
              "measure an armed read on purpose.", file=sys.stderr)
        return 3

    uri = os.environ.get("TORTOISE_DB_URI") or ""
    if not uri.startswith(("docker://", "redis://")):
        print("REFUSED: no live FalkorDB in TORTOISE_DB_URI — S3 itself refuses "
              "the embedded backend, and a retrieval figure measured on a "
              "backend the product cannot use is not a figure.\n"
              "  start one:  uri=\"$(uv run python tools/test_lane.py uri)\" && "
              "eval \"$uri\"", file=sys.stderr)
        return 3

    from tortoise.rerank import (
        DEFAULT_FUSED_RERANK_POOL,
        get_scorer,
    )
    from tortoise.sdk import TortoiseSDK

    # ⛔ #12297, MEASURED ON THIS BOX: torch defaults to one thread per core (16
    # here), and on a shared box already at load ~24 that pathologically thrashes
    # — 100 ms/pair vs 5.3 ms/pair at one thread, a 20x difference on the SAME
    # input. A latency figure taken with the default pool would describe the
    # scheduler, not the model. The count is pinned here and RECORDED in the
    # artifact; it is a property of this measurement, not a product setting.
    if args.threads > 0:
        import torch
        torch.set_num_threads(args.threads)

    t0 = time.time()
    sessions = Path(os.path.expanduser(args.sessions_dir))
    passages = harvest_passages(sessions, args.records)
    if not passages:
        print("REFUSED: the harvest produced no passages", file=sys.stderr)
        return 3

    # ⛔ RUN-SCOPED GRAPH, for the reason `candidate_recall_11901.py` records:
    # a reused graph keeps the previous run's content at a shifted id.
    graph_name = args.graph or f"rerank12297_{int(time.time())}_{os.getpid()}"
    digest = hashlib.sha256(
        "\n".join(p["content"] for p in passages).encode()).hexdigest()[:16]
    sdk = TortoiseSDK(namespace=args.namespace, graph_name=graph_name)
    try:
        proj = sdk._get_proj()
        for i, row in enumerate(passages):
            row["id"] = f"rp-12297{i:06d}"
            proj.g.query(
                "CREATE (n:Point {id:$id, content:$c, pointKind:'core:claim', "
                "createdAt:'2026-11-01T00:00:00Z'})",
                params={"id": row["id"], "c": row["content"]})
        stored = {r[0] for r in proj.g.query(
            "MATCH (n:Point) WHERE n.id STARTS WITH 'rp-12297' RETURN n.id",
            params={}).result_set}
        missing = [p["id"] for p in passages if p["id"] not in stored]
        if missing:
            print(f"REFUSED: {len(missing)} of {len(passages)} passages did not "
                  f"land (first: {missing[:3]!r})", file=sys.stderr)
            return 3
        # One cache-warming read, so the first measured query is not paying the
        # full-scan cold cost (§14.3 V3: the label is unindexed).
        sdk.tortoise_fts_query("warm", entity_type="point", limit=1)

        # ⛔ ONE resolver, used by BOTH the load and the report. ``args.model`` is
        # None on an ordinary run, and the SCORING call resolves its own default
        # through ``rerank_pool`` → ``load_scorer(model or fused_rerank_model())``,
        # which honours ``TORTOISE_FUSED_RERANK_MODEL``. Writing
        # ``args.model or RERANK_MODEL_DEFAULT`` into the artifact therefore
        # records a name the scorer may not have loaded when that knob is set —
        # an artifact that misnames its model is worse than one that says nothing.
        model = resolve_arm_model(args)
        scorer, reason = get_scorer(model)
        if scorer is None:
            print(f"REFUSED: the cross-encoder would not load ({reason}) — a "
                  f"rerank figure needs the model the product loads",
                  file=sys.stderr)
            return 3

        arms = {rule: _arm_block() for rule in QUERY_RULES}
        pairs_scored = 0
        score_s = 0.0
        per_query: list[dict] = []

        for row in passages:
            queries = build_queries(row["content"], args.span)
            if not queries:
                continue
            for rule in QUERY_RULES:
                query = queries.get(rule)
                if not query:
                    continue
                block = arms[rule]
                block["queries"] += 1
                # The pool the WIRED path scores: the fused order at the pool
                # width. `limit` and `pool_size` are the shipped knobs (#12297
                # changes neither default).
                pool_rows = sdk.tortoise_fts_query(
                    query, entity_type="point", limit=args.limit,
                    pool_size=args.pool_size)
                ids = [r.get("id") for r in pool_rows]
                if row["id"] not in ids:
                    block["pool_absent"] += 1
                before = next((i for i, x in enumerate(ids)
                               if x == row["id"]), None)

                t1 = time.perf_counter()
                reordered, stats = score_pool(
                    query, pool_rows, model=model, limit=args.limit)
                score_s += time.perf_counter() - t1
                record_stats(block, stats)
                _refusal = rerank_degrade_refusal(stats)
                if _refusal:
                    print(_refusal, file=sys.stderr)
                    return 3
                pairs_scored += stats.get("scored") or 0
                after_ids = [r.get("id") for r in reordered]
                after = next((i for i, x in enumerate(after_ids)
                              if x == row["id"]), None)

                if not args.no_assert_permutation:
                    assert len(after_ids) == len(ids), "rerank cut the list"
                    assert sorted(after_ids) == sorted(ids), \
                        "rerank changed the row SET (it must be a permutation)"

                for tag, rank in (("before", before), ("after", after)):
                    block[f"ndcg10_{tag}"] += _ndcg(rank)
                    block[f"mrr_{tag}"] += _rr(rank)
                    if rank is not None:
                        block[f"_found_{tag}"] += 1
                        block[f"_rank_sum_{tag}"] += rank + 1
                    if rank is not None and rank < CUT:
                        block[f"recall_at_10_{tag}"] += 1
                in_before = before is not None and before < CUT
                in_after = after is not None and after < CUT
                block["gained"] += int(in_after and not in_before)
                block["lost"] += int(in_before and not in_after)
                if args.per_query:
                    per_query.append({
                        "gold": row["id"], "rule": rule,
                        "rank_before": before, "rank_after": after,
                        "pool": len(ids),
                    })

        # ⛔ A run that scored NOTHING measured no latency: every query was a
        # read-side skip, so the artifact would report ``ms_per_pair: null``
        # beside a pool of zero-scored arms — a figure-shaped hole that reads as
        # "the reranker was cheap here". Refuse instead.
        if not pairs_scored:
            print("REFUSED: no pair was ever scored — every query was skipped "
                  "before the scorer (empty pool / empty query), so this run "
                  "measures no latency at all. Check the harvest and the pool "
                  "width, not the model.", file=sys.stderr)
            return 3

        _skipped = sum(b["rerank_skipped_read"] for b in arms.values())

        for block in arms.values():
            n = block["queries"] or 1
            for tag in ("before", "after"):
                block[f"recall_pct_{tag}"] = round(
                    100.0 * block[f"recall_at_10_{tag}"] / n, 1)
                block[f"ndcg_mean_{tag}"] = round(block[f"ndcg10_{tag}"] / n, 4)
                block[f"mrr_mean_{tag}"] = round(block[f"mrr_{tag}"] / n, 4)
                found = block[f"_found_{tag}"] or 1
                block[f"mean_rank_{tag}"] = round(
                    block[f"_rank_sum_{tag}"] / found, 2) if block[
                        f"_found_{tag}"] else None

        report = {
            "issue": 12297,
            "surface": "user search (fused point read)",
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "corpus": {
                "dir": str(sessions), "passages": len(passages),
                "population_digest": digest, "graph": graph_name,
                "elapsed_s": round(time.time() - t0, 1),
            },
            "declared_query_construction": {
                "rules": list(QUERY_RULES),
                "span_words": args.span,
                "why": ("no fixed user-search query set exists on this box; the "
                        "corpus's question-shaped turns are conversational "
                        "follow-ups with no standalone referent. Queries are "
                        "built from their own gold passage — declared, and NOT "
                        "an estimate of production search quality."),
            },
            "config": {
                "cut": CUT,
                "pool_scored": args.limit,
                "pool_scored_is_wired_default": (
                    args.limit == DEFAULT_FUSED_RERANK_POOL),
                "retrieval_limit": args.limit,
                "pool_size": args.pool_size,
                # ⛔ The RESOLVED id, not the flag: ``args.model`` is None on
                # every ordinary run (the scorer is resolved internally by
                # ``get_scorer``), and an artifact that says ``null`` cannot
                # tell a reader which cross-encoder produced its ms/pair —
                # the sibling noun-path artifact records the default by name.
                "model": model,
                "model_was_explicit": args.model is not None,
                "model_resolver": "fused_rerank_model()",
                "torch_threads": args.threads,
                # ⛔ The read path's OWN gate decides what the baseline IS. It is
                # False on every legitimate run (a True here means the caller
                # opted into a rerank-vs-rerank comparison via
                # ``--allow-reranked-baseline``), and recording it is what makes
                # that decidable from the artifact instead of from memory.
                "fused_rerank_gate_armed": _ambient_gate,
                # ⛔ DERIVED from the arms, never a literal: the loop refuses
                # (exit 3) on the first scorer degrade, so ``degraded_queries``
                # is 0 BY CONSTRUCTION — but a READ-side skip is tolerated and
                # counted, so "applied on every query" is FALSE for a run that
                # skipped any. Written as a literal once, this said True beside
                # ``rerank_skipped_read > 0`` in the same artifact.
                "rerank_applied_on_every_query": _skipped == 0,
                "rerank_skipped_read": _skipped,
                "degraded_queries": 0,
                "loadavg_1m": round(os.getloadavg()[0], 2),
            },
            "arms": arms,
            "latency": {
                "pairs_scored": pairs_scored,
                "score_s": round(score_s, 3),
                "ms_per_pair": (round(1000.0 * score_s / pairs_scored, 3)
                                if pairs_scored else None),
                "ms_per_query": (round(1000.0 * score_s / max(
                    sum(b["queries"] for b in arms.values()), 1), 1)),
                "device": "cpu (this box has no GPU)",
            },
        }
        if args.per_query:
            report["per_query"] = per_query
        payload = json.dumps(report, indent=1, sort_keys=True)
        if args.out:
            Path(args.out).write_text(payload + "\n")
        print(payload)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sessions-dir", default="~/.tortoise/session-events")
    p.add_argument("--records", type=int, default=400,
                   help="distinct real passages to write into the pool")
    p.add_argument("--span", type=int, default=8,
                   help="words per constructed query (default 8)")
    p.add_argument("--limit", type=int, default=POOL,
                   help=f"pool width scored AND the caller's retrieval limit "
                        f"(default {POOL} = rerank.DEFAULT_FUSED_RERANK_POOL)")
    p.add_argument("--pool-size", type=int, default=None,
                   help="per-leg retrieval depth; default = the product default "
                        "(120), i.e. the shipped configuration")
    p.add_argument("--model", default=None,
                   help="cross-encoder; default = the model rerank.py loads")
    p.add_argument("--threads", type=int, default=1,
                   help="torch intra-op thread count for the measurement; "
                        "0 = leave torch's default. Default 1: on this box the "
                        "default (16) thrashes under fleet load and reports "
                        "100 ms/pair where one thread reports 5.3 ms/pair. "
                        "Recorded in the artifact either way.")
    p.add_argument("--namespace", default="rerank12297")
    p.add_argument("--graph", default=None,
                   help="graph to write the pool to; default is RUN-SCOPED")
    p.add_argument("--per-query", action="store_true",
                   help="also emit the per-query rank table")
    p.add_argument("--no-assert-permutation", action="store_true",
                   help="skip the in-run check that the rerank never changes "
                        "the row SET (kept ON by default: it is the contract)")
    p.add_argument("--allow-reranked-baseline", action="store_true",
                   help="PERMIT the run while TORTOISE_FUSED_RERANK is armed. "
                        "Off by default because an armed read is already "
                        "reranked, so \"before\" is not the fused order and the "
                        "comparison is rerank-vs-rerank (a false zero). The "
                        "artifact records `fused_rerank_gate_armed` either way.")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    if args.limit < CUT:
        p.error(f"--limit {args.limit} is below the cut {CUT}")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
