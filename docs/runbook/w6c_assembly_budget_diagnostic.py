#!/usr/bin/env python3
"""W6C supplementary — the ASSEMBLY BUDGET actually filled (tokens of the
RESOLVED ask-lane token cap) for the five window-miss questions, in the two
arms (capture-shape / dense-alive) defined in w6c_gold_rank_diagnostic.py (the
``capture`` arm is pinned ``embed=False``, since W7A made the seeder's default
``embed=True``).

The caps are read from ``resolve_ask_retrieval_caps()`` — the SAME resolution
the lane it drives enforces — so the emitted receipt cannot pair a live token
cap with a stale byte cap. NOTE: the recorded 2026-09-19 receipt
(``w6c-assembly-budget-2026-09-19.json``) PREDATES #4105 and was measured
against the historical 8000-token / 32 KiB caps. The post-#4105 DEFAULTS
are 16000 tokens / 128000 bytes, but the value is resolved at run time —
``TORTOISE_ASK_CONTEXT_TOKEN_CAP`` / ``TORTOISE_ASK_CONTEXT_BYTE_CAP``
override it, so the receipt's own ``context_token_cap`` / ``byte_cap``
fields are the only authoritative record of what a given run used.

The real ask-lane retrieval + dedup + boost + rerank + assembly runs; only the
READER TRANSPORT is a deterministic stub that returns a fixed non-empty string,
so the lane reaches its response and exposes ``context_tokens`` with ZERO paid
reader calls. No quality claim is made from this run.

Usage: w6c_assembly_budget_diagnostic.py <out.json> [all]
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

from w6c_gold_rank_diagnostic import FIVE, FIXTURE, _attach_dense_vectors  # noqa: E402

from tools.ask_spotcheck import _seed_memory, _to_iso_date  # noqa: E402
from tortoise import ask_lane as ask_lane_mod  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402


class StubReader:
    """Deterministic non-empty transport — the lane's own assembly path runs;
    the reader adds nothing (this run measures the CONTEXT, not the answer)."""

    model = "w6c-stub-reader"
    provider = "w6c-stub"
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


def measure(q: dict, dense: bool) -> dict:
    db = os.path.join(tempfile.mkdtemp(prefix="w6c_asm_"), "t.db")
    sdk = TortoiseSDK(db)
    saved = ask_lane_mod._default_ask_reader_factory
    ask_lane_mod._default_ask_reader_factory = lambda: StubReader()
    ask_lane_mod._reset_ask_reader_cache_for_tests()
    try:
        _seed_memory(sdk, q, embed=False)
        if dense:
            _attach_dense_vectors(sdk)
        res = ask_lane_mod.run_ask_lane(
            sdk, q["question"],
            question_date=_to_iso_date(q.get("question_date") or ""))
        from tortoise.retrieval import resolve_ask_retrieval_caps
        caps = resolve_ask_retrieval_caps()
        return {
            "arm": "dense" if dense else "capture",
            "context_tokens": res.get("context_tokens"),
            "context_token_cap": caps["context_token_cap"],
            "budget_filled_pct": round(
                100.0 * (res.get("context_tokens") or 0)
                / caps["context_token_cap"], 1),
            "evidence_bytes": len((res.get("evidence") or "").encode("utf-8")),
            # The RESOLVED ceiling, never the historical 32 KiB literal — a
            # receipt that pairs a live token cap with a stale byte cap is
            # the lying-cap class #4105 removes.
            "byte_cap": caps["context_byte_cap"],
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
    result: dict = {"questions": {}}
    scope = [q["question_id"] for q in questions] if (
        len(sys.argv) > 2 and sys.argv[2] == "all") else FIVE
    result["scope"] = scope
    for qid in scope:
        entry = {}
        for dense in (False, True):
            m = measure(by_id[qid], dense)
            entry[m["arm"]] = m
            print(f"{qid:12s} {m['arm']:8s} ctx_tokens={m['context_tokens']:>5} "
                  f"({m['budget_filled_pct']}% of {m['context_token_cap']}) "
                  f"bytes={m['evidence_bytes']} "
                  f"degraded={m['retrieval_degraded']}", flush=True)
        result["questions"][qid] = entry
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print("receipt:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
