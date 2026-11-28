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

WHAT THE PRIOR SET IS, AND WHAT IT THEREFORE PROVES
---------------------------------------------------
`search["points"]` is a **RECONSTRUCTION**, not a recording. ⛔ The receipt does
NOT carry doc-05's S3 prior set — its `result` keys are
`points, turns, warnings, stats, surfaced, graph_ops, …`, with no search/prior
dump — and doc-05's own warning says the identity lookup was PARTIAL
(`S3 identity partial: 25 candidates, 11 did not run`, `held_reasons:
{"partial": 6}`). doc-01 is prior to doc-05 by the corpus's own order, so its
emitted points are the only evidence of what that prior set held.

So the replay can only demonstrate what THIS tree's arm does with the receipt's
own declaration — and on this head that is a DECLINE, not a closure (see the ⚠️
below): the receipt's point rows carry no `when`, so neither rival's start is an
instant and the gate refuses to order them. It is therefore a strictly WEAKER
statement than *"the shipped pipeline closes this corpus's contradiction"*: a
hold is not a closure, and where the prior set was partial the endpoint can
still be HELD. The shipped number requires a fresh-graph re-ingest (the #14198
owner comment's 5-document run, reading doc-05's `supersessions`), and this file
does not stand in for it.

⚠️ It also cannot exercise a CLOSURE: the receipt's point rows carry no `when`
(props are `quote`/`search_keys`/`source_turn_id`), so neither rival's start is
an instant and the arm DECLINES (there is no arrival-order fallback any more —
see `test_an_unordered_pair_declines_instead_of_closing_on_arrival_order`). The
closure is covered by
`test_a_world_time_ordering_is_decided_on_instants_not_date_prefixes`, and this
file is not a substitute for it.

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


def _arm_is_fixed() -> bool:
    """Does the IMPORTED ``extractor_v2`` carry the #14198 arm?

    ⛔ ``--arm`` used to be a printed LABEL: it named the tree but did not read
    it, so `--arm before` on the fixed tree printed a "before" banner over an
    "after" run (review F4 of #14794). The label is now checked against the
    module actually imported, and a disagreement REFUSES rather than mislabels.
    """
    from tortoise import extractor_v2

    src = Path(extractor_v2.__file__).read_text()
    return "_close_prior_contradiction" in src


def main(argv: list[str]) -> int:
    arm = "this-worktree"
    if "--arm" in argv:
        arm = argv[argv.index("--arm") + 1]
    # ⛔ `--tree` selects WHICH CHECKOUT is imported, and it is ALWAYS explicit:
    # the default is the worktree this script lives in, never the ambient
    # import path. This box's venv has the MAIN checkout installed, so a bare
    # `python replay_14198.py` resolves `/home/cmux/tortoise` and measures a
    # tree nobody named — `_arm_is_fixed` then REFUSES `--arm before`, which is
    # safe but leaves the before-arm unreproducible. With `--tree` the
    # pre-fix half is a one-liner:
    #   git worktree add /tmp/before-14198 <BASE_SHA> --detach
    #   python replay_14198.py --arm before --tree /tmp/before-14198
    tree = str(HERE.parents[2])
    if "--tree" in argv:
        tree = argv[argv.index("--tree") + 1]
    if not (Path(tree) / "tortoise" / "extractor_v2.py").is_file():
        raise SystemExit(
            f"tree {tree!r} carries no tortoise/extractor_v2.py — refusing to "
            "label a run with a tree it did not import")
    sys.path.insert(0, str(Path(tree).resolve()))  # FIRST, before the import
    from tortoise.extractor_v2 import execute_embed  # the arm under test

    if arm in ("before", "after"):
        fixed = _arm_is_fixed()
        if fixed != (arm == "after"):
            raise SystemExit(
                f"replay REFUSED: --arm {arm} names an arm the imported module "
                f"is not (the cross-capture arm is "
                f"{'present' if fixed else 'absent'} in "
                f"{__import__('tortoise').extractor_v2.__file__}) — point "
                "PYTHONPATH at the tree you mean instead of relabelling it")

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
    # ⛔ A DECLINE MUST NAME ITSELF (#14198 review: the arm can now DECLINE a
    # declaration instead of closing it, and the first filter printed NOT
    # RECORDED with no reason at all). "not closed" is the decline's marker;
    # "earlier-capture" the closure's; the drop line is the pre-arm outcome.
    named = [w for w in warnings
             if "earlier-capture" in w.lower() or "not closed (#14198)" in w
             or (NAND_DST_REF[:40] in w and "dropped" in w)]

    print(f"# replay arm: {arm}")
    print(f"# tree: {tree}")
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
