#!/usr/bin/env python3
"""W7A supplementary — the ASSEMBLY BUDGET actually filled (tokens of the
~8000-token ask-lane cap), in the two seeding arms the W7A switch defines.

This is the W7A successor to ``w6c_assembly_budget_diagnostic.py``. W6C had to
ATTACH turn vectors by hand (``_attach_dense_vectors``) because the seeder
wrote none; W7A makes the dense arm native — the ONLY difference between the
arms here is the seeder's ``embed`` switch, so the measurement cannot drift from
what the seeder actually stores:

  * ``backlog`` — ``_seed_memory(embed=False)``: the pre-#4194 / no-embedder
    store (#4197). Dense leg inert.
  * ``dense`` — ``_seed_memory(embed=True)``: the repaired DEFAULT, where the
    seeder stores the product's own turn vector through #4304's store seam.

The real ask-lane retrieval + dedup + boost + rerank + assembly runs; only the
READER TRANSPORT is a deterministic stub that returns a fixed non-empty string,
so the lane reaches its response and exposes ``context_tokens`` with ZERO paid
reader calls. No quality claim is made from this run.

Usage: w7a_assembly_budget_diagnostic.py <out.json> [all]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from w7a_gold_rank_diagnostic import FIVE, FIXTURE  # noqa: E402

from tools.ask_spotcheck import _seed_memory, _to_iso_date  # noqa: E402
from tortoise import ask_lane as ask_lane_mod  # noqa: E402
from tortoise.retrieval import resolve_ask_retrieval_caps  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402


class StubReader:
    """Deterministic non-empty transport — the lane's own assembly path runs;
    the reader adds nothing (this run measures the CONTEXT, not the answer)."""

    model = "w7a-stub-reader"
    provider = "w7a-stub"
    last_completion_tokens = 1
    last_prompt_tokens = 1
    last_finish_reason = "stop"

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        del system, user, max_tokens
        return "Stub transport answer (assembly-budget measurement only)."

    def incr_inflight(self) -> None:
        pass

    def decr_inflight(self) -> None:
        pass

    def close(self) -> None:
        pass


def measure(q: dict, *, embed: bool) -> dict:
    db = os.path.join(tempfile.mkdtemp(prefix="w7a_asm_"), "t.db")
    sdk = TortoiseSDK(db)
    saved = ask_lane_mod._default_ask_reader_factory
    ask_lane_mod._default_ask_reader_factory = lambda: StubReader()
    ask_lane_mod._reset_ask_reader_cache_for_tests()
    try:
        _seed_memory(sdk, q, embed=embed)
        n_embedded = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.embedding IS NOT NULL "
            "RETURN count(p)").result_set[0][0]
        res = ask_lane_mod.run_ask_lane(
            sdk, q["question"],
            question_date=_to_iso_date(q.get("question_date") or ""))
        caps = resolve_ask_retrieval_caps()
        return {
            "arm": "dense" if embed else "backlog",
            "seeded_embedded_points": n_embedded,
            "context_tokens": res.get("context_tokens"),
            "context_token_cap": caps["context_token_cap"],
            "budget_filled_pct": round(
                100.0 * (res.get("context_tokens") or 0)
                / caps["context_token_cap"], 1),
            "evidence_bytes": len((res.get("evidence") or "").encode("utf-8")),
            "byte_cap": 32768,
            "retrieval_degraded": res.get("retrieval_degraded"),
            "n_retrieved_sessions": len(res.get("retrieved_session_ids") or []),
        }
    finally:
        ask_lane_mod._default_ask_reader_factory = saved
        ask_lane_mod._reset_ask_reader_cache_for_tests()
        sdk.close()


def main() -> int:
    out_path = sys.argv[1]
    with open(FIXTURE) as f:
        data = json.load(f)
    questions = data["questions"] if isinstance(data, dict) else data
    by_id = {q["question_id"]: q for q in questions}
    scope = ([q["question_id"] for q in questions]
             if (len(sys.argv) > 2 and sys.argv[2] == "all") else FIVE)
    result: dict = {
        # A receipt that does not name its seeding mode is not evidence.
        "seeding_mode": {
            "dense": "_seed_memory(embed=True) — product's own turn vector via "
                     "encode_batch_for_store/required_embedding_dim "
                     "(#4194/#4304); the DEFAULT",
            "backlog": "_seed_memory(embed=False) — pre-#4194/no-embedder "
                       "store (#4197)",
        },
        "scope": scope,
        "questions": {},
    }
    for qid in scope:
        entry = {}
        for embed in (False, True):
            m = measure(by_id[qid], embed=embed)
            entry[m["arm"]] = m
            print(f"{qid:12s} {m['arm']:8s} "
                  f"ctx_tokens={m['context_tokens']:>5} "
                  f"({m['budget_filled_pct']}% of {m['context_token_cap']}) "
                  f"bytes={m['evidence_bytes']} "
                  f"embedded={m['seeded_embedded_points']} "
                  f"degraded={m['retrieval_degraded']}", flush=True)
        result["questions"][qid] = entry
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print("receipt:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
