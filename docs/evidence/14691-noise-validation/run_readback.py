#!/usr/bin/env python
"""#14691 T1-T5 — step 2: read the graph back through the product's own doors.

Every read here is an SDK method (`graph_overview`, `search_knowledge`,
`get_entity`, `explore_connections`, `list_knowledge`) — the same surface the MCP
tools map onto. No Cypher is built here and no HTTP is hand-rolled: a question this
file cannot answer through those doors is recorded as a GAP (REPORT §6), never
worked around.

What it produces, per question the five tests ask:

  `entities`   one row per entity discovered by the typed FULL SCAN, per kind, from
               the vocabulary `pack_registry` installs (`CORE_SUBCLASSES` plus every
               pack's declared kinds). ⛔ NOT from `graph_overview("namespaces")`: that
               reply carries `namespace` + `name` + `kind_count` and no kind NAMES, so a
               scan driven by it enumerates nothing — it is recorded as door finding D3.
               The rows also carry `url` naming their document, which is what T4's
               per-document Source coverage is graded on. This is T1/T2's population
               (does the count grow? do two wordings land as two names?).
  `neighbours` for every entity the WRITE receipt named, the door's own answer to
               "how many neighbours does this have" (`explore_connections`) plus the
               entity's own fields (`get_entity`). T3's door leg.
  `sources`    `list_knowledge(kind="source")` — the document population T4 asserts
               ("documents land as Sources") and T2's "+1 source link" is counted on.
  `probes`     for T2, the retrieval answer to the goal question with the ids it
               returns, so "one thing, two sources" is checkable at the answer level
               and not only by comparing counts.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
WT = HERE.parent.parent.parent

GRAPH = "xv14691_noise"

#: T2 turns on this question: if the two wordings merged into ONE entity, the answer
#: names one thing; if they multiplied, the answer surfaces both.
T2_PROBES = (
    "What is the goal of restaurant accounts?",
    "How many restaurant accounts do we need by December?",
)

#: Keys any of the doors may use for its row list. Three shapes, one unwrap — a
#: reply that carries its rows under a name not listed here would read as "no rows",
#: which is why every raw reply is committed beside the normalised read.
ROW_KEYS = ("result", "results", "relationships", "nodes", "items", "connected",
            "rows", "entities", "sources")


def rows(reply, keys=ROW_KEYS) -> list:
    """Normalise any door's reply to a list of dict rows (never raises)."""
    if isinstance(reply, str):
        try:
            return rows(json.loads(reply), keys)
        except (ValueError, TypeError):
            return []
    if isinstance(reply, dict):
        for key in keys:
            if isinstance(reply.get(key), list):
                return [r for r in reply[key] if isinstance(r, dict)]
        return []
    if isinstance(reply, list):
        return [r for r in reply if isinstance(r, dict)]
    return []


def norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def elide_vectors(node):
    """Drop `embedding` values from a recorded reply, keeping every other field.

    ⛔ STATED, NOT SILENT. A `get_entity` reply carries a 1,536-float embedding
    (~90 KB per entity) — it is not evidence for any of T1-T5 (no test here
    compares a vector), and carrying 30 of them made this artifact 5 MB of floats.
    Every OTHER field is recorded verbatim: the elision is one named key, applied
    recursively, so a reader can see exactly what was removed.
    """
    if isinstance(node, dict):
        return {k: ("<elided: not evidence for T1-T5>" if k == "embedding"
                    else elide_vectors(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [elide_vectors(v) for v in node]
    return node


def _provenance() -> dict:
    body = Path(__file__).read_bytes()
    return {
        "probe_path": str(Path(__file__).resolve().relative_to(WT)),
        "probe_sha256": hashlib.sha256(body).hexdigest(),
        "graph": GRAPH,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def kind_vocabulary() -> tuple[list[tuple[str, str]], dict]:
    """`("object"|"subject"|"event", "[ns:]kind")` for every kind that may be stored.

    The vocabulary comes from the PRODUCT's own declarations, never from a list
    this file invents: `pack_registry.CORE_SUBCLASSES` (core object/subject/event
    kinds) union `PackRegistry.list_all_kinds()` (every installed pack's declared
    kinds). A scan over kinds nobody installed would report "0 rows" and read like
    a finding about the extractor.

    ⛔ WHY NOT `graph_overview("namespaces")`: it returns `namespace` + `kind_count`
    and NO kind names (measured on this graph), so a scan driven by it enumerates
    nothing — the first cut of this probe did exactly that and reported "entity
    population by full scan: 0", which is a fact about the door, not about the
    extractor. That door gap is recorded as finding D3.
    """
    diag: dict = {}
    pairs: list[tuple[str, str]] = []
    from tortoise.pack_registry import CORE_SUBCLASSES, PackRegistry, default_packs_dir

    for layer in ("Object", "Subject", "Event"):
        for kind in CORE_SUBCLASSES.get(layer, ()):
            pairs.append((layer.lower(), f"core:{kind}"))
    diag["core_kinds"] = len(pairs)
    try:
        registry = PackRegistry(default_packs_dir())
        registry.load_all()
        kinds = registry.list_all_kinds()
        diag["packs"] = {axis: len(names) for axis, names in kinds.items()}
        for qualified in kinds.get("objectKinds") or []:
            pairs.append(("object", qualified))
        for qualified in kinds.get("eventKinds") or []:
            pairs.append(("event", qualified))
        diag["total"] = len(pairs)
    except Exception as exc:                      # recorded, never swallowed
        diag["error"] = f"{type(exc).__name__}: {exc}"
    return pairs, diag


def kind_vocabulary_from_namespaces(namespaces) -> list[tuple[str, str]]:
    """`(layer, "ns:kind")` pairs a `namespaces` reply can yield.

    Kept because it is the door a caller reaches for first, and it is the door that
    cannot answer: this graph's reply carries `kind_count` per namespace and no kind
    names, so this returns `[]` on every real reply. Recorded as finding D3 rather
    than left as a mystery.
    """
    pairs: list[tuple[str, str]] = []
    for ns in rows(namespaces if not isinstance(namespaces, dict)
                   else {"namespaces": namespaces.get("namespaces")}):
        namespace = ns.get("namespace")
        if not namespace:
            continue
        for kind in (ns.get("object_kinds") or ns.get("kinds") or []):
            name = kind.get("kind") if isinstance(kind, dict) else kind
            if name:
                pairs.append(("object", f"{namespace}:{name}"))
        for kind in (ns.get("subject_kinds") or []):
            name = kind.get("kind") if isinstance(kind, dict) else kind
            if name:
                pairs.append(("subject", f"{namespace}:{name}"))
    return pairs



def edge_endpoints(receipt: dict) -> list[str]:
    """Every id the WRITE receipt says it used as an endpoint, in first-seen order."""
    ids: list[str] = []
    for p in receipt.get("passes") or []:
        for doc in p.get("documents") or []:
            rw = ((doc.get("result") or {}).get("relation_write") or {})
            for edge in rw.get("edges") or []:
                for key in ("src_id", "dst_id"):
                    val = edge.get(key)
                    if isinstance(val, str) and val not in ids:
                        ids.append(val)
    return ids


def main() -> int:
    from tortoise.sdk import TortoiseSDK

    receipt = json.loads((OUT / "ingest-receipt.json").read_text())
    sdk = TortoiseSDK(graph_name=GRAPH,
                      event_log_path=str(OUT / "events-readback.jsonl"))
    report: dict = {
        "graph_name": GRAPH,
        "db_uri": os.environ.get("TORTOISE_DB_URI", "").split("@")[-1],
        "provenance": _provenance(),
        "overview": elide_vectors(sdk.graph_overview()),
        "overview_sections": {},
    }
    for section in ("topics", "sources", "namespaces", "audit", "taxonomy",
                    "pointkinds", "structure", "status"):
        try:
            report["overview_sections"][section] = elide_vectors(
                sdk.graph_overview(section))
        except Exception as exc:
            report["overview_sections"][section] = {
                "error": f"{type(exc).__name__}: {exc}"}

    # ── (a) the typed FULL SCAN: the entity population, per declared kind ──
    vocab, vocab_diag = kind_vocabulary()
    report["vocabulary_source"] = vocab_diag
    report["namespace_scan_pairs"] = [
        f"{layer}:{kind}" for layer, kind in
        kind_vocabulary_from_namespaces(report["overview_sections"]["namespaces"])]
    report["kind_vocabulary"] = [f"{layer}:{kind}" for layer, kind in vocab]
    report["scans"] = {}
    seen: dict[str, dict] = {}
    # ⛔ THE LAYER IS PROBED, NOT ASSERTED. A kind's layer is not knowable from its
    # declaration: `core:organization` is scanned as a Subject and stored as an
    # Object on this graph (`ColdPath Logistics` -> `objectKind: core:organization`),
    # so a scan that trusted the declaration resolved 19 of the 29 stored Objects
    # and called the missing 10 "0 rows" — a door-shaped hole reported as a fact
    # about the extractor (#14691 T6's D3, reproduced here). Each candidate kind is
    # therefore asked of all three entity layers and the answering layer is
    # recorded; the coverage block below compares what this resolved against the
    # graph's own taxonomy counts, so an incomplete population is a NUMBER in the
    # artifact and never an assumption.
    for _declared_layer, qualified in vocab:
        for layer in ("object", "subject", "event"):
            key = f"{layer}:{qualified}"
            if key in report["scans"]:
                continue
            try:
                reply = sdk.search_knowledge(kind=qualified, entity_type=layer,
                                             limit=200)
                ok, err = True, None
            except Exception as exc:
                reply, ok, err = None, False, f"{type(exc).__name__}: {exc}"
            got = rows(reply)
            report["scans"][key] = {"ok": ok, "error": err, "count": len(got),
                                    "rows": got if got else []}
            if got:
                print(f"=== scan {key}: {len(got)} row(s) ok={ok} {err or ''} ===")
            for row in got:
                rid = row.get("id") or row.get("point_id")
                if not rid:
                    continue
                seen.setdefault(rid, {"id": rid, "kind": qualified,
                                      "layer": layer,
                                      "name": row.get("content") or row.get("name") or "",
                                      "row": row})
    report["entities"] = list(seen.values())
    taxonomy_counts = (report["overview"] or {}).get("taxonomy") or {}
    by_layer: dict[str, int] = {}
    for ent in seen.values():
        by_layer[ent["layer"]] = by_layer.get(ent["layer"], 0) + 1
    report["population_coverage"] = {
        "resolved": len(seen),
        "resolved_by_layer": by_layer,
        "taxonomy": taxonomy_counts,
        "unresolved": {layer: int(taxonomy_counts.get(layer.capitalize()) or 0)
                       - by_layer.get(layer, 0)
                       for layer in ("object", "subject", "event")},
    }
    print(f"=== entity population by full scan: {len(seen)} "
          f"{by_layer} of taxonomy {taxonomy_counts} ===")

    # ── (b) the door's answer, for every entity the WRITE receipt named ─────
    # ...and for every entity the FULL SCAN resolved. T3 measures the orphan share
    # over the whole created population, so reading the door only for
    # receipt-named endpoints would have silently defined the population as "the
    # things already known to be connected" — the orphan share would be 0 by
    # construction, which is the measurement laundering this bundle must not do.
    report["neighbours"] = {}
    population = edge_endpoints(receipt) + [eid for eid in seen
                                            if eid not in edge_endpoints(receipt)]
    for eid in population:
        entry: dict = {}
        # `node=eid` (a default argument), not a closure over the loop variable:
        # a late-binding lambda here would read the LAST id for every entity and the
        # per-entity door answer would be one node repeated (#ruff B023 — a real bug
        # in the first cut of this probe, caught by the linter rather than by a
        # reviewer noticing 30 identical replies).
        for label, call in (
            ("get_entity", lambda node=eid: sdk.get_entity(node)),
            ("walk", lambda node=eid: sdk.explore_connections(node_id=node, hops=1,
                                                             include_content=False)),
        ):
            try:
                entry[label] = {"ok": True, "reply": elide_vectors(call())}
            except Exception as exc:
                entry[label] = {"ok": False,
                                "error": f"{type(exc).__name__}: {exc}"}
        walk = entry["walk"].get("reply") if entry["walk"].get("ok") else None
        connected = (walk or {}).get("connected") if isinstance(walk, dict) else None
        entry["connected_counts"] = {k: len(v or [])
                                     for k, v in (connected or {}).items()} \
            if isinstance(connected, dict) else {}
        entry["connected_total"] = sum(entry["connected_counts"].values())
        report["neighbours"][eid] = entry
        entity = (entry["get_entity"].get("reply") or {}) \
            if entry["get_entity"].get("ok") else {}
        label = entity.get("name") or entity.get("content") or ""
        print(f"=== neighbour door {eid[:18]}: {label[:44]!r} "
              f"kind={entity.get('objectKind') or entity.get('subjectKind') or ''} "
              f"connected={entry['connected_total']} "
              f"({entry['connected_counts']}) ===")

    # ── (c) the document population (T4: documents land as Sources) ─────────
    try:
        report["sources"] = {"ok": True,
                            "reply": elide_vectors(
                                sdk.list_knowledge(kind="source", limit=50))}
    except Exception as exc:
        report["sources"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(f"=== sources: {len(rows(report['sources'].get('reply')))} row(s) ===")

    # ── (d) retrieval, at the answer level (T2's corroborating read) ────────
    report["probes"] = {}
    for query in T2_PROBES:
        try:
            reply = sdk.search_knowledge(query=query, limit=8)
            ok, err = True, None
        except Exception as exc:
            reply, ok, err = None, False, f"{type(exc).__name__}: {exc}"
        report["probes"][query] = {"ok": ok, "error": err,
                                   "reply": elide_vectors(reply),
                                   "ids": [r.get("id") for r in rows(reply)]}
        print(f"=== probe {query!r}: {len(rows(reply))} row(s) ===")

    sdk.close()
    (OUT / "readback.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'readback.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
