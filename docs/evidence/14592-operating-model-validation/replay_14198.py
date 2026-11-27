#!/usr/bin/env python
"""#14198 — replay S5 on the DOC-05 SHAPE the #14592 receipt recorded.

No LLM, no database, no `uv run` needed for the question this answers. S5
(`extractor_v2.execute_embed`) is a PURE function: it maps an `embed_list` + the
S3 `search` result to the Layer-1 commit payload. So the loss can be reproduced,
and the fix measured, entirely from the committed receipt.

WHAT IS REPLAYED, AND WHY IT IS FAITHFUL
----------------------------------------
`out/ingest-receipt.json` records doc-05's emitted `points` (id + text) and the
`warnings` of the run. Two of those warnings are the finding:

    operator endpoint HELD not minted (#10997 S3 HOLD): NAND dst named
      'ColdPath Logistics holds a validated cold-chain certificate' ...
    operator dropped: src/dst did not resolve to an emitted point/event
      ('The cold-chain audit failed ColdPath Logistics last week'
       → 'ColdPath Logistics holds a validated cold-chain certificate')

and doc-01's point `pt_fc8b3e47…` IS that certificate claim. So the replay
builds exactly those three inputs:

  * `embed_list["points"]`  = doc-05's emitted points, verbatim from the receipt
  * `embed_list["operators"]` = the dropped NAND, with the two refs the
    receipt's own warning names
  * `search["points"]`      = doc-01's emitted points, verbatim from the receipt
    (this is what S3 returns as PRIORS: the claims that existed before this
    capture — `_resolve_point_supersede`'s own docstring says so)

Nothing is invented: every content string and id below is read out of the
receipt by id, and the run refuses rather than substituting a value it could not
find.

BEFORE / AFTER
--------------
`--arm before` replays the pipeline as it ships today (the receiver is the tree
at the commit named on the report line). `--arm after` replays this worktree.
Both print the payload's `supersessions` and the warnings that mention the
cross-capture pair, so the difference is the artifact rather than a claim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECEIPT = HERE / "out" / "ingest-receipt.json"

#: The two ids the receipt's own warnings name. Read by ID, never pasted as
#: content, so the replay cannot drift from the run it is replaying.
DOC01_CERT = "pt_fc8b3e47bb7e0599328d968073671eef8c9cecbc376ca2bbd6e05476f2c078"
DOC05_AUDIT = "pt_9af28546e85d11f414e05b658580e4afe46f2c21ec9a9e58dce89b8a288aa5"

#: The two refs, verbatim from the receipt's own HELD/dropped warnings.
NAND_DST_REF = "ColdPath Logistics holds a validated cold-chain certificate"


def _points_by_id(receipt: dict, index: int) -> dict[str, dict]:
    rows = receipt["documents"][index]["result"]["points"]
    return {str(r["id"]): r for r in rows}


def build_inputs(receipt: dict, *, coverage: str = "partial") \
        -> tuple[dict, dict, str, str]:
    """(embed_list, search, new_point_id, old_point_id) — all read by id."""
    doc01 = _points_by_id(receipt, 0)
    doc05 = _points_by_id(receipt, 4)
    old = doc01.get(DOC01_CERT)
    new = doc05.get(DOC05_AUDIT)
    if old is None or new is None:
        raise SystemExit(
            f"replay REFUSED: the receipt does not carry {DOC01_CERT!r} / "
            f"{DOC05_AUDIT!r} — do not substitute a value, re-read the run")
    if str(old["text"]).strip() != NAND_DST_REF:
        raise SystemExit(
            "replay REFUSED: the doc-01 certificate claim's text is no longer "
            f"{NAND_DST_REF!r} (got {old['text']!r}) — the receipt's dropped "
            "NAND warning named that exact string")
    embed_list = {
        "entities": [], "events": [],
        "points": [
            {"id": str(r["id"]), "content": str(r["text"]),
             "pointKind": str(r.get("kind") or "statement"),
             "about_entities": [], "search_keys":
                 list((r.get("props") or {}).get("search_keys") or []),
             "quote": str((r.get("props") or {}).get("quote") or "")}
            for r in doc05.values()
        ],
        "operators": [
            {"op_type": "NAND",
             "src": str(new["text"]),
             "dst": NAND_DST_REF,
             "stated_predicate": "refutes"},
        ],
        "retractions": [],
    }
    # S3 returns PRIORS: the claims that existed BEFORE this capture. doc-01 is
    # entirely prior to doc-05 by the corpus's own dates/order.
    #
    # ⛔ The PARTIAL coverage report is not decoration — it is the state the
    # recorded run was in, and it is what makes this a replay rather than a
    # different run. doc-05's receipt recorded `S3 identity partial: the
    # existing-item lookup did not run for 11 of 25 candidate(s)` and
    # `held_reasons: {"partial": 6}`, and `_mint_endpoint`'s #10997 gate holds
    # (rather than mints) an endpoint whenever the lookup is not `resolved`.
    # Replaying with a `resolved` lookup instead would exercise the MINT arm and
    # quietly answer a different question: the loss under test happens on the
    # HELD arm, which is the arm the corpus actually took.
    search = {
        "points": [
            {"id": str(r["id"]), "content": str(r["text"]),
             "kind": str(r.get("kind") or "statement")}
            for r in doc01.values()
        ],
        "query_coverage": {
            "derived": {"points": 25},
            # partial = the RECORDED state (11 of 25 did not run);
            # resolved = the counterfactual, used only to measure what the
            # other lookup state does with the same declaration.
            "ran": {"points": 25 if coverage == "resolved" else 14},
            "failed": {"points": 0},
        },
    }
    return embed_list, search, str(new["id"]), str(old["id"])


def main(argv: list[str]) -> int:
    arm = "this-worktree"
    if "--arm" in argv:
        arm = argv[argv.index("--arm") + 1]
    from tortoise.extractor_v2 import execute_embed  # the arm under test

    receipt = json.loads(RECEIPT.read_text())
    coverage = "partial"
    if "--coverage" in argv:
        coverage = argv[argv.index("--coverage") + 1]
    embed_list, search, new_id, old_id = build_inputs(receipt,
                                                     coverage=coverage)
    out = execute_embed(embed_list, search, session_id="replay-14198-doc05",
                        session_date="2026-03-05")
    supers = out.get("supersessions") or []
    cross = [s for s in supers
             if str(s.get("superseded")) == old_id
             and str(s.get("supersedes_by")) == new_id]
    warnings = out.get("warnings") or []
    named = [w for w in warnings
             if "earlier-capture" in w.lower()
             or (NAND_DST_REF[:40] in w and "dropped" in w)]

    print(f"# replay arm: {arm}")
    print(f"# lookup state: {coverage}")
    print(f"# extractor_v2 from: {__import__('tortoise').extractor_v2.__file__}")
    print(f"# new claim (this capture) : {new_id}")
    print(f"# older claim (prior)      : {old_id}")
    print()
    print(f"supersessions recorded      : {len(supers)}")
    print(f"supersessions naming the pair: {len(cross)}")
    for s in cross:
        print(f"  superseded={s['superseded']}  supersedes_by={s['supersedes_by']}")
        print(f"  evidence={s.get('evidence')!r}")
    print()
    print("warnings naming the pair:")
    for w in named:
        print(f"  - {w}")
    if not named:
        print("  (none)")
    print()
    ops = (out.get("payload") or {}).get("operators") or []
    cross_ops = [x for x in ops
                 if old_id in (str(x.get("src")), str(x.get("dst")))
                 and new_id in (str(x.get("src")), str(x.get("dst")))]
    print(f"operators naming the pair : {len(cross_ops)}")
    for x in cross_ops:
        print(f"  {x.get('op_type')}: {str(x.get('src'))[:20]} -> {str(x.get('dst'))[:20]}")
    print()
    verdict = "RECORDED" if cross else "NOT RECORDED"
    print(f"VERDICT: the cross-document contradiction is {verdict}")
    return 0 if cross else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
