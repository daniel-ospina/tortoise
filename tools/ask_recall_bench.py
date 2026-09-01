#!/usr/bin/env python3
"""#2070 Step 0 — ask-lane recall@k baseline harness (long-haystack subset).

The falsification gate of the scoping package: measure whether the ask
lane's retrieval actually misses the gold turns on long haystacks, on BOTH
lanes, before/after the A-levers, with eval-parity seeding.

WHAT IT MEASURES (retrieval only — no reader, no provider keys):

  * gold-in-pool@120      — the gold turn id is among the fused pool the
                            retrieval call returns at limit=120 (the pool
                            floor is 120, so this IS the full fused pool).
  * gold-in-context@cap   — the gold id is among the ASSEMBLED context ids
                            under the historical caps (limit 40, item cap 40,
                            8k tokens, 32 KiB bytes — the ask lane's default).
  * gold-in-context@120   — the same assembly under the A6-raised caps
                            (limit 120, item cap 120) — what the cap review
                            would buy for in-pool gold.

SEEDING PARITY (verifier-fix): ingestion mirrors ``tools/longmem_eval/
ingest.py`` (search_keys + has_answer + embeddings + session props + the
deterministic turn→point-id map ``lme:{qid}:s{si}:t{ti}``) — NOT
``ask_spotcheck._seed_memory``'s bare ``create_point("statement", content)``
(which writes no search_keys/has_answer/id map → A4/A5 would have zero
material and gold turns would be unidentifiable).

LANES: ``--lane embedded`` (today's degraded reality — no FTS index, TF-IDF
fallback, vector leg absent unless ``--embedder`` injects a probe) or
``--lane docker`` (FalkorDB FTS + HNSW vector when the embeddings extra is
installed; ``TORTOISE_DB_URI`` must be set).

LEVERS: ``--levers on`` enables the ask-lane knobs (A1 numeric tokens,
A4 search_keys PRF, A5 evidence boost, A3 fusion weights) exactly the way
``ask()`` resolves them; ``--levers off`` (default) disables them for the
baseline. ``--cap-limit N``/``--cap-item N`` set the A6 measurement caps.

A2 EMBEDDER PROBE: ``--embedder <name>`` injects a same-384-dim probe model
via ``tools.embedder_probe`` with a FRESH graph per candidate
(fresh-seed-per-candidate — cross-dim scoring is invalid, and re-embedding
needs a clean graph). Requires the ``embeddings`` extra + HF network.

USAGE:
    # docker lane, 4 recorded failures, baseline (levers off):
    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
      uv run python tools/ask_recall_bench.py --lane docker
    # levers on, A6-raised caps, vector leg via the bge-small probe:
    ... --levers on --cap-limit 120 --cap-item 120 --embedder bge-small
    # scan for MORE long-haystack questions (gold > 40) beyond the 4:
    ... --scan --scan-limit 100
    # embedded lane (carve-out env):
    TORTOISE_TEST_CARVE_OUT=1 uv run python tools/ask_recall_bench.py --lane embedded
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tortoise.sdk import TortoiseSDK  # noqa: E402

logger = logging.getLogger("ask_recall_bench")

#: The four recorded retrieval-gap failures (issue #2070) — the default
#: subset. Scoped: ceb54acb (rank/cap — in-pool at 120, cut at 40),
#: 1de5cff2 (thin overlap), gpt4_d84a3211 (numeric-invisible + dilution),
#: 1d4e3b97 (thin overlap + stem mismatch on the embedded lane).
RECORDED_FAILURES = ["ceb54acb", "1de5cff2", "gpt4_d84a3211", "1d4e3b97"]

#: Historical ask-lane caps (the 8k/40/32KiB budget) and the A6-raised
#: measurement caps.
CONTEXT_TOKEN_CAP = 8000
BYTE_CAP = 32768


def _dataset_path() -> str:
    return os.path.expanduser(
        "~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json")


def _load_dataset() -> list[dict]:
    path = _dataset_path()
    if not os.path.exists(path):
        logger.error("cached dataset missing: %s", path)
        sys.exit(2)
    with open(path) as f:
        data = json.load(f)
    return data


def _gold_turn_ids(question: dict) -> list[str]:
    """Deterministic turn→point-id map for the evidence turns (ingest
    parity: ``lme:{qid}:s{si}:t{ti}`` — mirror tools/longmem_eval/ingest.py's
    id scheme so the bench's recall keys match the graph the ingest wrote)."""
    qid = question["question_id"]
    ids: list[str] = []
    for si, session in enumerate(question.get("haystack_sessions") or []):
        for ti, turn in enumerate(session):
            if turn.get("has_answer"):
                ids.append(f"lme:{qid}:s{si}:t{ti}")
    return ids


def _seed_question(sdk, question: dict,
                   search_keys_map: dict[str, list[str]] | None = None) -> None:
    """Eval-parity seeding: ``ingest_haystack`` (search_keys property + 
    has_answer + embeddings + session props + deterministic turn ids) then an
    optional search_keys augmentation (fixture/bench material — the dataset
    turns carry no search_keys; the extractor's coverage is a separate axis)."""
    from tools.longmem_eval.ingest import ingest_haystack
    ingest_haystack(sdk, question)
    if search_keys_map:
        _apply_search_keys(sdk, search_keys_map)


def _apply_search_keys(sdk, search_keys_map: dict[str, list[str]]) -> None:
    proj = sdk._get_proj()
    for pid, aliases in (search_keys_map or {}).items():
        if not aliases:
            continue
        flat = " ".join(str(a) for a in aliases if a)
        if not flat.strip():
            continue
        try:
            proj.g.query(
                "MATCH (p:Point {id: $pid}) SET p.search_keys = $sk",
                params={"pid": pid, "sk": flat},
            )
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("search_keys write failed for %s: %s", pid, e)


def _new_sdk(lane: str, embedder: str | None) -> TortoiseSDK:
    """A FRESH graph per question (the axis-2 protocol: each question's
    haystack is an independent memory — no cross-question contamination).
    Embedded: a unique tempfile per call. Docker: a unique ``test_askbench_*``
    namespace per call (the shared server graph must never accumulate
    cross-question state)."""
    import uuid

    from tortoise.sdk import TortoiseSDK
    if lane == "embedded":
        db = os.path.join(tempfile.mkdtemp(prefix="ask_bench_"), "t.db")
        return TortoiseSDK(db)
    return TortoiseSDK(
        namespace=f"test_askbench_{uuid.uuid4().hex[:12]}")


def _close_sdk(sdk) -> None:
    with contextlib.suppress(Exception):
        sdk.close()


def _retrieve_pipeline(sdk, question: str, *, limit: int, item_cap: int,
                       keep_numeric: bool, search_keys_prf: bool,
                       fusion_weights: dict | None, fusion_k: int,
                       evidence_boost: bool,
                       question_date: str | None = None) -> list[dict]:
    """Replicate the ask lane's retrieval→annotation→dedup→A5 boost→assemble
    pipeline (no reader call — the bench grades retrieval). Returns the
    assembled context hits (ids are the recall surface)."""
    from tortoise.retrieval import (
        DEFAULT_MAX_CHUNKS_PER_SESSION,
        apply_evidence_boost,
        assemble_context,
        dedup_pool,
        resolve_ask_boost_multipliers,
    )
    hits = sdk.tortoise_fts_query(
        question, limit=limit, pool_size=None, include_terminal=True,
        keep_numeric=keep_numeric, search_keys_prf=search_keys_prf,
        fusion_weights=fusion_weights, fusion_k=fusion_k)
    annotated = sdk.annotate_ask_hits(hits)

    def _ask_session_key(h: dict) -> str:
        return (h.get("session_id") or h.get("session_date")
                or f"idx:{h.get('lme_session_index', -1)}")

    deduped = dedup_pool(
        annotated, max_chunks_per_session=DEFAULT_MAX_CHUNKS_PER_SESSION,
        session_key=_ask_session_key)
    if evidence_boost:
        mult = resolve_ask_boost_multipliers()
        deduped, _ = apply_evidence_boost(
            deduped,
            boost_answer_string=mult["answer_string"],
            boost_verbatim=mult["verbatim"],
            boost_source=mult["source"])
    return assemble_context(
        deduped, top_k=item_cap,
        max_context_tokens=CONTEXT_TOKEN_CAP,
        question_date=question_date,
        context_item_cap=item_cap, byte_cap=BYTE_CAP)


def _recall(gold: set[str], hits: list[dict]) -> float:
    if not gold:
        return float("nan")
    return len(gold & {h["id"] for h in hits}) / len(gold)


def _measure_question(sdk, question: dict, *, keep_numeric: bool,
                      search_keys_prf: bool, fusion_weights: dict | None,
                      fusion_k: int, cap_limit: int, cap_item: int,
                      evidence_boost: bool) -> dict:
    q = question["question"]
    gold = set(_gold_turn_ids(question))
    # pool@120 — the full fused pool (pool floor = 120; limit=120 returns it)
    pool_hits = sdk.tortoise_fts_query(
        q, limit=120, pool_size=None, include_terminal=True,
        keep_numeric=keep_numeric, search_keys_prf=search_keys_prf,
        fusion_weights=fusion_weights, fusion_k=fusion_k)
    pool_ids = [h["id"] for h in pool_hits]
    gold_pool = gold & set(pool_ids)
    gold_pool_rank = [
        (i + 1) for i, pid in enumerate(pool_ids) if pid in gold]
    # context@40 (historical caps) + context@cap_item (A6 measurement caps)
    ctx40 = _retrieve_pipeline(
        sdk, q, limit=40, item_cap=40, keep_numeric=keep_numeric,
        search_keys_prf=search_keys_prf, fusion_weights=fusion_weights,
        fusion_k=fusion_k, evidence_boost=evidence_boost,
        question_date=question.get("question_date") or None)
    ctxN = _retrieve_pipeline(
        sdk, q, limit=cap_limit, item_cap=cap_item,
        keep_numeric=keep_numeric, search_keys_prf=search_keys_prf,
        fusion_weights=fusion_weights, fusion_k=fusion_k,
        evidence_boost=evidence_boost,
        question_date=question.get("question_date") or None)
    return {
        "question_id": question["question_id"],
        "gold_turns": len(gold),
        "gold_in_pool_120": gold == gold_pool,
        "gold_pool_rank": gold_pool_rank or None,
        "recall_pool_120": _recall(gold, pool_hits),
        "recall_context_40": _recall(gold, ctx40),
        "recall_context_cap": _recall(gold, ctxN),
        "context_cap": cap_item,
        "n_pool": len(pool_ids),
        "n_ctx40": len(ctx40),
        "n_ctx_cap": len(ctxN),
    }


def _scan_for_failures(data: list[dict], *, scan_limit: int,
                       keep_numeric: bool, search_keys_prf: bool) -> list[str]:
    """Scan the dataset for questions whose gold turns rank >40 in the pool
    (the long-haystack subset definition — the 4 recorded failures are
    seeded first). Expensive (ingest + retrieval per question); bounded by
    ``scan_limit`` (default 100 — the dataset is 500)."""
    from tortoise.sdk import TortoiseSDK
    found: list[str] = []
    for i, question in enumerate(data):
        if i >= scan_limit:
            break
        if question["question_id"] in RECORDED_FAILURES:
            continue
        sdk2 = None
        try:
            qid = question["question_id"]
            logger.info("scan [%d/%d] %s", i + 1, scan_limit, qid)
            db = os.path.join(tempfile.mkdtemp(prefix="ask_scan_"), "t.db")
            sdk2 = TortoiseSDK(db)
            _seed_question(sdk2, question)
            hits = sdk2.tortoise_fts_query(
                question["question"], limit=120, pool_size=None,
                include_terminal=True, keep_numeric=keep_numeric,
                search_keys_prf=search_keys_prf)
            ranks = [j + 1 for j, h in enumerate(hits)
                     if h["id"] in set(_gold_turn_ids(question))]
            if ranks and max(ranks) > 40:
                found.append(qid)
                logger.info("  -> gold rank %s (>40): ADDED", ranks)
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("scan question failed: %s", e)
        finally:
            if sdk2 is not None:
                with contextlib.suppress(Exception):
                    sdk2.close()
    return found


def _levers_env(levers: str) -> None:
    """Set the ask-lane env knobs to the requested lever posture so the bench
    measures exactly what ``ask()`` would resolve (single source of truth)."""
    if levers == "off":
        os.environ["TORTOISE_ASK_NUMERIC_TOKENS"] = "0"
        os.environ["TORTOISE_ASK_SEARCH_KEYS_PRF"] = "0"
        os.environ["TORTOISE_ASK_EVIDENCE_BOOST"] = "0"
    else:
        os.environ["TORTOISE_ASK_NUMERIC_TOKENS"] = "1"
        os.environ["TORTOISE_ASK_SEARCH_KEYS_PRF"] = "1"
        os.environ["TORTOISE_ASK_EVIDENCE_BOOST"] = "1"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lane", choices=["embedded", "docker"],
                    default=None, help="default: docker when TORTOISE_DB_URI set")
    ap.add_argument("--questions", default=",".join(RECORDED_FAILURES),
                    help="comma-separated question ids (default: the 4 recorded)")
    ap.add_argument("--scan", action="store_true",
                    help="scan the dataset for MORE gold-rank>40 questions")
    ap.add_argument("--scan-limit", type=int, default=100)
    ap.add_argument("--search-keys-map", default=None,
                    help="JSON {turn_id: [aliases]} augmentation file")
    ap.add_argument("--levers", choices=["off", "on"], default="off",
                    help="off=baseline (default); on=enable A1/A3/A4/A5")
    ap.add_argument("--cap-limit", type=int, default=120,
                    help="A6 measurement retrieval-window limit (default 120)")
    ap.add_argument("--cap-item", type=int, default=120,
                    help="A6 measurement context item cap (default 120)")
    ap.add_argument("--embedder", default=None,
                    help="A2 same-384-dim probe model (fresh graph per candidate)")
    args = ap.parse_args()

    lane = args.lane
    if lane is None:
        lane = "docker" if os.environ.get("TORTOISE_DB_URI") else "embedded"

    if args.embedder:
        try:
            from tools.embedder_probe import inject_model
        except ImportError as e:
            logger.error("embedder probe unavailable: %s", e)
            return 2
        try:
            inject_model(args.embedder)
            logger.info("A2 probe injected: %s", args.embedder)
        except Exception as e:  # noqa: BLE001, RUF100
            logger.error("embedder probe injection failed: %s", e)
            return 2

    _levers_env(args.levers)

    data = _load_dataset()
    by_id = {q["question_id"]: q for q in data}
    qids = [q.strip() for q in args.questions.split(",") if q.strip()]
    missing = [qid for qid in qids if qid not in by_id]
    if missing:
        logger.error("unknown question ids: %s", missing)
        return 2

    search_keys_map = None
    if args.search_keys_map:
        with open(args.search_keys_map) as f:
            search_keys_map = json.load(f)

    sdk = _new_sdk(lane, args.embedder)
    results = []
    try:
        if args.scan:
            extra = _scan_for_failures(
                data, scan_limit=args.scan_limit,
                keep_numeric=(args.levers == "on"),
                search_keys_prf=(args.levers == "on"))
            for qid in extra:
                if qid not in qids:
                    qids.append(qid)
        for qid in qids:
            question = by_id[qid]
            logger.info("measuring %s (%s lane)", qid, lane)
            _seed_question(sdk, question, search_keys_map)
            try:
                row = _measure_question(
                    sdk, question,
                    keep_numeric=(args.levers == "on"),
                    search_keys_prf=(args.levers == "on"),
                    fusion_weights=None, fusion_k=60,
                    cap_limit=args.cap_limit, cap_item=args.cap_item,
                    evidence_boost=(args.levers == "on"))
            finally:
                # fresh graph per question (no cross-question contamination —
                # the axis-2 protocol)
                _close_sdk(sdk)
                sdk = _new_sdk(lane, args.embedder)
            results.append(row)
    finally:
        _close_sdk(sdk)
        if args.embedder:
            try:
                from tools.embedder_probe import reset
                reset()
            except Exception:  # noqa: BLE001, RUF100
                pass

    print(f"\n=== ask recall bench — {lane} lane, levers={args.levers} "
          f"(cap {args.cap_limit}/{args.cap_item}), embedder={args.embedder} ===")
    print(f"{'qid':<16} {'gold':>4} {'pool@120':>9} {'pool_rank':>12} "
          f"{'ctx@40':>9} {'ctx@cap':>9} {'n_pool':>6} {'n_ctx':>5}")
    agg = {"pool120": [], "ctx40": [], "ctxcap": []}
    for r in results:
        rank = r["gold_pool_rank"][0] if r["gold_pool_rank"] else None
        print(f"{r['question_id']:<16} {r['gold_turns']:>4} "
              f"{r['gold_in_pool_120']!s:>9} "
              f"{rank!s:>12} "
              f"{r['recall_context_40']:>9.2f} "
              f"{r['recall_context_cap']:>9.2f} "
              f"{r['n_pool']:>6} {r['n_ctx_cap']:>5}")
        for k, key in (("pool120", "recall_pool_120"),
                       ("ctx40", "recall_context_40"),
                       ("ctxcap", "recall_context_cap")):
            if r[key] == r[key]:  # not NaN
                agg[k].append(r[key])
    for k in ("pool120", "ctx40", "ctxcap"):
        vals = agg[k]
        mean = (sum(vals) / len(vals)) if vals else float("nan")
        print(f"aggregate recall[{k}] = {mean:.3f} "
              f"({len(vals)}/{len(results)} questions with gold)")

    # machine-readable JSON (the runbook's before/after record)
    out = {
        "lane": lane, "levers": args.levers, "embedder": args.embedder,
        "cap_limit": args.cap_limit, "cap_item": args.cap_item,
        "results": results,
    }
    with open("/tmp/ask_recall_bench_result.json", "w") as f:
        json.dump(out, f, indent=2)
    print("\nJSON record: /tmp/ask_recall_bench_result.json")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")
    sys.exit(main())
