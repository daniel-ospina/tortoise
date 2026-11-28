#!/usr/bin/env python
"""#14304 — replay the recorded `#14592` emission through the FIXED write seam.

WHAT THIS PROVES, AND WHAT IT DOES NOT
-------------------------------------
The `#14592` run (`#14606`, merged) wrote its 8 `ownedBy` rows through
`relation_write.apply_relation_edges` on a fresh graph. Its receipt keeps the
RESULT (`relation_write.edges[]`: the ids, predicate and write path it wrote) but
**not the stated `relations[]` record** — the names the extractor emitted are
gone, so a byte-exact replay of the LLM's output is impossible from the bundle
alone, and re-running the extractor would spend ~150k tokens to produce a
DIFFERENT (non-deterministic) emission.

So this script does the narrower thing that is fully evidenced:

  * it takes the receipt's OWN rows — `src_id`/`dst_id`/`predicate` — which are
    the emission as the write seam resolved it (the extractor's `relations[]`
    rows arrive name-keyed and are resolved to exactly these ids by
    `plan_relation_edges` itself), and
  * takes the endpoint KINDS the committed reads resolved
    (`out/census.json` → `.recall_people.reply.results[].subject.kind` and
    `.related_objects[].entity_type`), and
  * runs the FIXED `plan_relation_edges` over a `{name: [(id, kind)]}` index
    built from them.

Rows whose endpoint kinds are NOT in the committed reads are reported as
`unresolved` — the guard abstains on them, exactly as it does in production, and
this script does not invent a kind to make the count look better.

Run:  uv run python docs/evidence/14304-ownedby-direction/replay_14592_relations.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tortoise.relation_write import plan_relation_edges  # noqa: E402

BUNDLE = ROOT / "docs/evidence/14592-operating-model-validation/out"


def _read(name: str) -> dict:
    return json.loads((BUNDLE / name).read_text())


def _index_from_census(census: dict) -> dict[str, list[tuple[str, str]]]:
    """`{name: [(id, kind)]}` from the ONE committed read that resolves both ends.

    `recall_people` is the read the `#14592` report itself cites for endpoint
    identity (its §G2 table), so the kinds here are the report's own evidence,
    not a re-derivation.
    """
    index: dict[str, list[tuple[str, str]]] = {}
    for result in (census.get("recall_people", {}).get("reply") or {}).get("results") or []:
        subject = result.get("subject") or {}
        if subject.get("id") and subject.get("name") and subject.get("kind"):
            index.setdefault(subject["name"], []).append(
                (subject["id"], subject["kind"]))
        for obj in result.get("related_objects") or []:
            # `related_objects` returns NODES of either layer: the subject itself
            # (`entity_type: "subject"`) and its Object neighbours
            # (`entity_type: "object"`). Only the object half carries a kind here
            # — the census records `entity_type`, which is the NODE LABEL, and
            # that is exactly what `_endpoint_is_label` consumes first.
            if (obj.get("id") or "").startswith("1a21036f") and \
                    obj.get("entity_type") == "subject" and obj.get("content") == subject.get("name"):
                continue
            if obj.get("id") and obj.get("content"):
                index.setdefault(obj["content"], []).append(
                    (obj["id"], obj.get("entity_type") or ""))
    return index


def main() -> int:
    receipt = _read("ingest-receipt.json")
    census = _read("census.json")
    index = _index_from_census(census)
    by_id: dict[str, tuple[str, str]] = {}
    for name, holders in index.items():
        for node_id, kind in holders:
            by_id[node_id] = (name, kind)

    print(f"resolved endpoints from the committed reads: {len(by_id)}")
    for node_id, (name, kind) in sorted(by_id.items()):
        print(f"  {node_id}  {name!r}  kind={kind!r}")
    print()

    by_predicate: dict[str, list[dict]] = {}
    for doc in receipt.get("documents") or []:
        for row in ((doc.get("result") or {}).get("relation_write") or {}).get("edges") or []:
            by_predicate.setdefault(row.get("predicate") or "", []).append(row)

    repaired = unchanged = unresolved = 0
    for predicate in ("ownedBy", "managedBy"):
        rows = by_predicate.get(predicate) or []
        print(f"── {predicate}: {len(rows)} row(s) written by the #14592 run")
        for row in rows:
            src_id, dst_id = row.get("src_id"), row.get("dst_id")
            src = by_id.get(src_id)
            dst = by_id.get(dst_id)
            if src is None or dst is None:
                unresolved += 1
                missing = "src" if src is None else "dst"
                print(f"   {src_id} -> {dst_id}: UNRESOLVED {missing} kind — "
                      f"the guard abstains (no committed read resolved it)")
                continue
            # The emission as the seam receives it: entity NAMES it must resolve,
            # which is what `plan_relation_edges` maps to the receipts ids.
            edges, drops = plan_relation_edges(
                [{"src": src[0], "dst": dst[0], "predicate": predicate}], index)
            assert not drops, drops
            got = edges[0]
            orientation = got.orientation
            if orientation:
                repaired += 1
                state = "REPAIRED"
                detail = (f"{orientation['stated']['from']} -> "
                          f"{orientation['stated']['to']}  ==>  "
                          f"{orientation['written']['from']} -> "
                          f"{orientation['written']['to']}  "
                          f"[{orientation['rule']}, evidence="
                          f"{orientation['evidence']}]")
            else:
                unchanged += 1
                state = "unchanged"
                detail = f"{got.src_id} -> {got.dst_id}"
            print(f"   {state:9s} {detail}")
            checks = (got.src_id, got.dst_id)
            expected = (dst_id, src_id) if orientation else (src_id, dst_id)
            assert checks == expected, (checks, expected)
        print()

    print(f"TOTAL  repaired={repaired}  unchanged={unchanged}  "
          f"unresolved(abstained)={unresolved}")
    print("(the receipt's own rows are the PRE-FIX record: each `ownedBy` row "
          "above was written Subject -> Object, i.e. inverted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
