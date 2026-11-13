"""#11739 — WHY the gate failed: quote IDENTITY across distinct facts.

This is the second artifact of the #11739 gate run. ``composed_gate.py`` produces the three gate
verdicts; this probe produces the ROOT-CAUSE counts quoted in the bundle README, so every number
on the issue has an artifact and a command behind it rather than a hand-run.

What it measures, all from the same cached live-graph rows and the REAL composer:

* how many distinct composed texts the live quoted population composes to (names arm empty — the
  live graph carries no neighbour material, see ``composed_gate.py``), and therefore how many
  Points share a composed text with another Point;
* for every pair inside a shared-composed-text group: whether their ``content`` is the same, and
  their RAW cosine (the graph's stored vectors) — i.e. exactly the pairs the composition turns into
  a cosine-1.000 "duplicate" that raw space did not see;
* the largest groups, and the count of pre-existing raw exact-duplicate pairs (cos >= 0.99999),
  so the README does not attribute a pre-existing raw duplicate to the composition.

Usage
-----
    uv run python docs/evidence/11739-composed-gate/probes/quote_identity.py \
        --rows /tmp/11739-rows.jsonl \
        --out docs/evidence/11739-composed-gate/quote_identity.json

Exit codes: 0 measured · 2 usage / unwritable --out · 3 no rows / no composed text (never a
silent zero).

⚠️ ``--rows`` is the row cache written by ``composed_gate.py`` (the live, quoted+embedded subset;
re-fetch it with that probe, which is the one owner of the paging contract).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import itertools
import json
import os
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent


def _say(msg: str) -> None:
    sys.stderr.write(msg)


def measure(rows: list[dict]) -> dict:
    from tortoise.embed_compose import compose_embedding_text, quote_text

    kept = [r for r in rows
            if isinstance(r.get("embedding"), list) and r["embedding"]
            and quote_text(r.get("quote"))]
    if not kept:
        _say("IDENTITY-EMPTY: no live quoted+embedded rows in the cache.\n")
        raise SystemExit(3)
    composed = [compose_embedding_text(r.get("quote"), (), content=r.get("content"))
                for r in kept]
    raw = np.asarray([r["embedding"] for r in kept], dtype=np.float32)
    raw /= np.clip(np.linalg.norm(raw, axis=1, keepdims=True), 1e-12, None)

    groups: dict[str, list[int]] = collections.defaultdict(list)
    for i, text in enumerate(composed):
        groups[text].append(i)
    shared = {t: v for t, v in groups.items() if len(v) > 1}

    pairs = 0
    same_content = 0
    below = 0
    at_or_above = 0
    examples: list[dict] = []
    for text, idx in shared.items():
        for a, b in itertools.combinations(idx, 2):
            pairs += 1
            if kept[a].get("content") == kept[b].get("content"):
                same_content += 1
            r = float(raw[a] @ raw[b])
            if r < 0.89:
                below += 1
                if len(examples) < 8:
                    examples.append({
                        "raw_similarity": round(r, 4),
                        "composed_similarity": 1.0,
                        "composed_text": text[:180],
                        "content_a": (kept[a].get("content") or "")[:160],
                        "content_b": (kept[b].get("content") or "")[:160],
                    })
            else:
                at_or_above += 1

    rr = raw @ raw.T
    np.fill_diagonal(rr, -1.0)
    exact = int(np.count_nonzero(np.triu(rr >= 0.99999, 1)))

    sizes = sorted((len(v) for v in shared.values()), reverse=True)
    return {
        "measured_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                  __import__("time").gmtime()),
        "rows_in_cache": len(rows),
        "measured_points": len(kept),
        "distinct_composed_texts": len(groups),
        "points_without_a_distinct_composed_text": len(kept) - len(groups),
        "points_without_distinct_text_pct": round(
            100.0 * (len(kept) - len(groups)) / len(kept), 2),
        "shared_composed_text_groups": len(shared),
        "points_in_shared_groups": sum(len(v) for v in shared.values()),
        "points_in_shared_groups_pct": round(
            100.0 * sum(len(v) for v in shared.values()) / len(kept), 2),
        "pairs_composing_to_identical_text": pairs,
        "of_those_with_DIFFERENT_content": pairs - same_content,
        "of_those_with_same_content": same_content,
        "of_those_raw_below_0_89": below,
        "of_those_raw_at_or_above_0_89": at_or_above,
        "largest_groups": [{"points": n} for n in sizes[:6]],
        "largest_group_points": sizes[0] if sizes else 0,
        "raw_exact_duplicate_pairs_ge_0_99999": exact,
        "examples": examples,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", default="/tmp/11739-rows.jsonl")
    ap.add_argument("--out", default="-")
    args = ap.parse_args(argv[1:])
    try:
        rows = [json.loads(line) for line in open(args.rows, encoding="utf-8")]
    except OSError as e:
        _say(f"IDENTITY-NO-ROWS({args.rows}): {e}\n")
        return 3
    report = measure(rows)
    report["provenance"] = {
        "probe_path": str(pathlib.Path(__file__).relative_to(HERE)),
        "probe_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "rows_source": args.rows,
        "composed_encoder": "BAAI/bge-small-en-v1.5 (only via the composer's word budget; "
                            "this section encodes nothing)",
    }
    text = json.dumps(report, indent=2) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
        return 0
    try:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
    except OSError as e:
        _say(f"PROBE-WRITE({args.out}): {e}\n")
        return 2
    _say(f"wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
