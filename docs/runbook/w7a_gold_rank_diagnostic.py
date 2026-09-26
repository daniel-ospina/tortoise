#!/usr/bin/env python3
"""W7A supplementary diagnostic — per-question GOLD-TURN RANK of the five
window-miss questions, on the REPAIRED seeder.

The W7A repair makes ``tools.ask_spotcheck.seed_capture_turn_store`` store
the product's OWN turn embedding by default. This diagnostic shows what that
changes for the five questions whose gold turns the keyword-only ranking
placed beyond the 40-item cut. It measures the SAME retrieval the instrument
uses (``TortoiseSDK.tortoise_fts_query``, the ask lane's pool), in two arms:

  * ``backlog`` — ``_seed_memory(..., embed=False)``: the pre-#4194 /
    no-embedder store (what the old default seeded, and what a capture made
    before #4194 actually has). The dense leg is inert.
  * ``dense`` — ``_seed_memory(...)`` with the repaired DEFAULT
    (``embed=True``): the seeder stores the product's own vector, so the
    dense leg sees captured turns.

The ONLY difference between the arms is the seeder's ``embed`` switch — there
is no manual vector attachment (W6C's diagnostic had to attach vectors by
hand; the repair makes that native). Read-only; zero paid reader calls. It
changes NOTHING in ``tools/ask_shape_rate.py`` (legs, thresholds, fixture,
reader pin, pre-registered rule) — it is a new diagnostic, not the ruler.

Usage: w7a_gold_rank_diagnostic.py <out.json>
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from tools.ask_spotcheck import _seed_memory  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402

FIXTURE = os.path.join(_REPO_ROOT, "tests", "fixtures",
                       "ask_spotcheck_composition.json")
FIVE = ["0a995998", "1d4e3b97", "1de5cff2", "ceb54acb", "e9327a54"]
LIMIT = 120        # the ask-lane pool depth (max(80,120)) — M1's n_pool120
CUT = 40           # the assembled reader-window cut
LEG_DEPTH = 120


def gold_turn_ids(q: dict) -> list[str]:
    ids = q.get("haystack_session_ids") or []
    sessions = q.get("haystack_sessions") or []
    out: list[str] = []
    for i, sess in enumerate(sessions):
        for j, turn in enumerate(sess or []):
            if turn.get("has_answer"):
                out.append(f"{ids[i]}_t{j}")
    return out


def _ids_of(hits: list[dict]) -> list[str]:
    out = []
    for h in hits:
        out.append(str(h.get("id") or h.get("point_id") or h.get("pointId")
                       or h.get("node_id") or ""))
    return out


def measure(q: dict, *, embed: bool) -> dict:
    db = os.path.join(tempfile.mkdtemp(prefix="w7a_rank_"), "t.db")
    sdk = TortoiseSDK(db)
    try:
        _seed_memory(sdk, q, embed=embed)
        n_embedded = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.embedding IS NOT NULL "
            "RETURN count(p)").result_set[0][0]
        leg_trace: list[dict] = []
        hits = sdk.tortoise_fts_query(
            q["question"], limit=LIMIT, pool_size=LEG_DEPTH,
            include_terminal=True, leg_trace=leg_trace,
            keep_numeric=True, search_keys_prf=True,
            fusion_weights=None, fusion_k=60)
        order = _ids_of(hits)
        ranks = {}
        for gid in gold_turn_ids(q):
            ranks[gid] = (order.index(gid) + 1) if gid in order else "ABSENT"
        return {
            "arm": "dense" if embed else "backlog",
            "seeded_embedded_points": n_embedded,
            "n_hits": len(order),
            "gold_turn_ranks": ranks,
            "gold_in_cut40": {g: (r != "ABSENT" and r <= CUT)
                              for g, r in ranks.items()},
            "leg_trace": leg_trace,
        }
    finally:
        sdk.close()


def main() -> int:
    out_path = sys.argv[1]
    with open(FIXTURE) as f:
        data = json.load(f)
    questions = data["questions"] if isinstance(data, dict) else data
    by_id = {q["question_id"]: q for q in questions}
    result: dict = {
        # A receipt that does not name its seeding mode is not evidence: this
        # diagnostic measures BOTH arms, and only the ``dense`` arm is the
        # product's post-#4194 shape. The ranks are only comparable within an
        # arm.
        "seeding_mode": {
            "dense": "_seed_memory(embed=True) — product's own turn vector "
                     "via encode_batch_for_store/required_embedding_dim "
                     "(#4194/#4304); the DEFAULT",
            "backlog": "_seed_memory(embed=False) — pre-#4194/no-embedder "
                       "store (#4197)",
        },
        "questions": {},
    }
    for qid in FIVE:
        q = by_id[qid]
        entry = {"question": q["question"],
                 "gold_sessions": q.get("answer_session_ids"),
                 "gold_turns": gold_turn_ids(q)}
        for embed in (False, True):
            m = measure(q, embed=embed)
            entry[m["arm"]] = m
            print(f"{qid:12s} {m['arm']:8s} gold={m['gold_turn_ranks']} "
                  f"n_hits={m['n_hits']} embedded={m['seeded_embedded_points']}",
                  flush=True)
        result["questions"][qid] = entry
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print("receipt:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
