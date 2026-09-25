#!/usr/bin/env python3
"""W6C supplementary diagnostic — per-question GOLD-TURN RANK of the five
window-miss questions, measured twice on the SAME graph shape:

  * ``capture`` arm — the frozen instrument's own seeder
    (``ask_spotcheck._seed_memory``) seeded ``embed=False``: the pre-#4194 /
    un-backfilled store shape. Pinned EXPLICITLY because since W7A the
    seeder's default is ``embed=True`` — an un-pinned call would silently
    collapse this arm into the ``dense`` one.
  * ``dense`` arm — the identical graph, with the turn Points' ``embedding``
    set by the PRODUCT'S OWN ``tortoise.embeddings.compute_embeddings`` over
    the PRODUCT'S OWN stored-turn text (``sdk._capture_turn_texts``) — i.e.
    exactly what the real capture write path (#4202 / d51306c51) now stores.
    The dense leg is the ONLY difference between the two arms.

This is a NEW read-only diagnostic. It does NOT change
``tools/ask_shape_rate.py``'s legs, thresholds, fixture, reader pin or
pre-registered rule.

Usage: w6c_gold_rank_diagnostic.py <out.json>
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
from tortoise.embeddings import compute_embeddings  # noqa: E402
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


def _attach_dense_vectors(sdk) -> dict:
    """Set ``p.embedding`` on every episodic turn Point from the product's own
    ``compute_embeddings`` over the product's own stored-turn text."""
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.is_episodic = true "
        "RETURN p.id, p.content ORDER BY p.id").result_set
    ids = [r[0] for r in rows]
    texts = [r[1] or "" for r in rows]
    vecs = compute_embeddings(texts)
    written = 0
    # strict=True: compute_embeddings is length-preserving by contract (one
    # entry per input text, None where the model is unavailable), and ids/vecs
    # are both built 1:1 from the same query rows — a length mismatch means a
    # violated embedder contract and would silently under-populate the dense
    # arm this diagnostic exists to measure. Raise instead.
    for pid, vec in zip(ids, vecs, strict=True):
        if vec is None:
            continue
        sdk._get_proj().g.query(
            "MATCH (p:Point {id:$id}) SET p.embedding = vecf32($e)",
            params={"id": pid, "e": vec})
        written += 1
    return {"turn_points": len(ids), "vectors_written": written}


def _ids_of(hits: list[dict]) -> list[str]:
    out = []
    for h in hits:
        out.append(str(h.get("id") or h.get("point_id") or h.get("pointId")
                       or h.get("node_id") or ""))
    return out


def measure(q: dict, dense: bool) -> dict:
    db = os.path.join(tempfile.mkdtemp(prefix="w6c_rank_"), "t.db")
    sdk = TortoiseSDK(db)
    try:
        _seed_memory(sdk, q, embed=False)
        attach = _attach_dense_vectors(sdk) if dense else None
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
            "arm": "dense" if dense else "capture",
            "attach": attach,
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
    result: dict = {"questions": {}}
    for qid in FIVE:
        q = by_id[qid]
        entry = {"question": q["question"],
                 "gold_sessions": q.get("answer_session_ids"),
                 "gold_turns": gold_turn_ids(q)}
        for dense in (False, True):
            m = measure(q, dense)
            entry[m["arm"]] = m
            print(f"{qid:12s} {m['arm']:8s} gold={m['gold_turn_ranks']} "
                  f"n_hits={m['n_hits']}", flush=True)
        result["questions"][qid] = entry
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print("receipt:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
