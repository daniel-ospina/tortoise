#!/usr/bin/env python
"""#14691 T1-T5 — step 3: score the five noise tests from the artifacts.

Pure function of the two committed receipts (`out/ingest-receipt.json`,
`out/readback.json`) and the committed corpus fixture. No graph, no network, no
model call — which is what makes it re-runnable by a reviewer and pinnable by the
bundle's own suite.

The five tests are the issue's, quoted as they are written there:

  T1  duplicate multiplication — ingest twice, counts must not grow.
  T2  same fact, two wordings  — +0 entities, +1 source link.
  T3  orphan share             — how much of what was created has no edges.
  T4  type collapse            — the ontology's types must be USED, not collapsed.
  T5  invented connections     — every relation must be stated or implied by text.

Each verdict is a RULE over numbers printed beside it, not a judgement: a reader
who disagrees with the rule can recompute the verdict from the numbers without
reading this file's prose. The rules are named `RULE_*` so the bundle's suite can
pin them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

#: The five tests' rules. A rule change is a claim change, so they live here in one
#: place and the report quotes them verbatim.
RULE_T1 = ("PASS iff the second ingestion adds no RE-STATED knowledge: 0 new "
           "Objects, 0 new Subjects, 0 same-name entities, and no Point whose text "
           "re-states an existing Point (>= 0.8 content-token overlap with a "
           "pass-1 Point). Events are reported SEPARATELY and are not counted as "
           "noise on the object axis: a re-ingestion IS an occurrence, so one "
           "Event per capture is expected — but an event-kind POINT re-minted for "
           "a sentence that was already stated is noise, and is counted.")
RULE_T2 = ("PASS iff the restatement (doc-06) adds 0 new ENTITIES and every "
           "doc-06 statement of the goal resolves to an EXISTING doc-03 point id — a "
           "complete merge, one thing carrying two sources, not a second copy (this "
           "is a resolution test, not a count: the goal fact is two sentences per "
           "wording, so a complete merge shares TWO ids and a partial one shares one); a "
           "second same-meaning entity (an unlinked second point) is FAIL. 'Entities' "
           "here is Object + Subject, NOT Event: doc-06's first ingest is one capture, "
           "and RULE_T1 states that one Event per capture is expected rather than "
           "noise, so counting it here would fail T2 on an axis this bundle declares "
           "clean. Overlap is content-token overlap (lowercased alphanumeric tokens "
           "of >2 characters, common English function words removed). ⛔ The rule's "
           "\"carrying two sources\" leg is NOT graded: the sources door answers "
           "per-Source POINT COUNTS, not point-to-Source links, so \"does this point "
           "carry two sources\" cannot be asked of the committed readback at all — "
           "recorded as gap D7 rather than assumed.")
RULE_T3 = ("Reported as a share, with an explicit population. PASS iff the "
           "majority of created entities are NOT orphans AND every orphan named "
           "is standalone in the corpus text — graded as: its NAME is stated in one "
           "document, verbatim or at >= 0.8 content-token overlap with that document "
           "(content tokens are lowercased alphanumeric tokens of >2 characters with "
           "common English function words removed; the same near-match band T1 uses "
           "for a restatement); otherwise FAIL, with the door's own answer reported "
           "beside the write receipt's so a door defect cannot be laundered as an "
           "extractor defect.")
RULE_T4 = ("PASS iff none of the ontology's types is collapsed: the flat "
           "(Point/Event/Subject/Object/Source) distribution is non-degenerate "
           "(Event, Subject and Object all non-zero, and at least one Source per "
           "document ingested), a decision is represented by a decision-kind "
           "ENTITY, roles land as Subject-layer role entities, and documents land "
           "as Sources. ⛔ The decision's SENTENCES are Points and land as "
           "`statement` kind; this rule grades the decision ENTITY, and the "
           "Points' kinds are reported beside the verdict rather than graded.")
RULE_T5 = ("PASS iff every sampled relation is stated or directly implied by the "
           "corpus text: both ENDPOINTS named in one document — verbatim, or the "
           "extractor's re-wording at >= 0.6 content-token overlap with that "
           "document. 'Content-token overlap' is lowercased alphanumeric tokens of "
           ">2 characters with common English function words removed. The POPULATION "
           "is every relation either pass's write receipt "
           "says it wrote: pass 2 is where re-extraction invents names (§4), so "
           "excluding it would be sampling away the defect. One relation whose "
           "endpoints are never co-stated fails the test. ⛔ The PREDICATE axis is "
           "NOT graded: the corpus is prose and the graph's predicates are ontology "
           "names (`ownedBy`, `related`, …), so a wording test would score a correct "
           "normalisation as an invention — the count of rows whose predicate "
           "wording appears in its co-stated documents is reported as gap D6 "
           "instead of being passed silently.")


def load(name: str) -> dict:
    path = OUT / name
    if not path.exists():
        raise SystemExit(f"missing artifact {path} — run run_ingest.py/run_readback.py first")
    return json.loads(path.read_text())


def rows(reply) -> list:
    """Any door's reply normalised to a row list (dict / list / JSON string)."""
    if isinstance(reply, str):
        try:
            return rows(json.loads(reply))
        except (ValueError, TypeError):
            return []
    if isinstance(reply, dict):
        for key in ("result", "results", "relationships", "nodes", "items",
                    "rows", "entities", "sources"):
            if isinstance(reply.get(key), list):
                return [r for r in reply[key] if isinstance(r, dict)]
        return []
    if isinstance(reply, list):
        return [r for r in reply if isinstance(r, dict)]
    return []


def counts(overview: dict | None) -> dict:
    """The countable axes of one `graph_overview()` reply."""
    overview = overview or {}
    taxonomy = overview.get("taxonomy") or {}
    structure = overview.get("structure") or {}
    pointkinds = overview.get("pointkinds") or {}
    if isinstance(pointkinds, list):
        # `graph_overview("pointkinds")` answers a LIST of {kind, count, pack}
        # while the full overview answers {total, by_kind}: one normaliser, so a
        # reply shape change cannot silently zero T4's distribution.
        pointkinds = {"by_kind": {str(r.get("kind")): int(r.get("count") or 0)
                                  for r in pointkinds if isinstance(r, dict)}}
    sources = overview.get("sources") or {}
    if isinstance(sources, list):
        sources = {"total": len(sources),
                   "with_points": sum(1 for r in sources
                                      if isinstance(r, dict) and r.get("points"))}
    return {
        "taxonomy": {k: int(v or 0) for k, v in taxonomy.items()},
        "structure_total": int(structure.get("total") or 0),
        "operators": int(structure.get("operators") or 0),
        "points_by_kind": {k: int(v or 0) for k, v in
                           (pointkinds.get("by_kind") or {}).items()},
        "source_rows": int(sources.get("total") or 0),
        "sources_with_points": int(sources.get("with_points") or 0),
    }


def delta(a: dict, b: dict) -> dict:
    """`b - a` over the axes, flattened for the report."""
    out: dict = {}
    for axis in ("taxonomy", "points_by_kind"):
        # ⛔ `sorted()`, not the raw set: iterating a set of strings makes the emitted
        # dict's KEY ORDER depend on `PYTHONHASHSEED`, so two runs over identical
        # artifacts produced byte-different JSON while every VALUE agreed. That breaks the
        # one property the bundle sells — a reviewer re-runs the scorer and diffs it against
        # the committed file — and nothing graded it. Pinned by
        # `test_the_artifact_is_byte_identical_under_a_different_hash_seed`.
        for key in sorted(set(a.get(axis) or {}) | set(b.get(axis) or {})):
            d = int((b.get(axis) or {}).get(key) or 0) - int((a.get(axis) or {}).get(key) or 0)
            if d:
                out[f"{axis}.{key}"] = d
    for key in ("structure_total", "operators", "source_rows", "sources_with_points"):
        d = int(b.get(key) or 0) - int(a.get(key) or 0)
        if d:
            out[key] = d
    return out


def relation_rows(receipt: dict, pass_no: int | None = None) -> list[dict]:
    """Every edge the WRITE door says it wrote, tagged with its document."""
    rows: list[dict] = []
    for p in receipt.get("passes") or []:
        if pass_no is not None and p.get("pass") != pass_no:
            continue
        for doc in p.get("documents") or []:
            rw = ((doc.get("result") or {}).get("relation_write") or {})
            for edge in rw.get("edges") or []:
                rows.append({**edge, "pass": p.get("pass"),
                             "doc_id": doc.get("doc_id")})
    return rows


def points(receipt: dict, pass_no: int | None = None) -> list[dict]:
    rows: list[dict] = []
    for p in receipt.get("passes") or []:
        if pass_no is not None and p.get("pass") != pass_no:
            continue
        for doc in p.get("documents") or []:
            for pt in ((doc.get("result") or {}).get("points") or []):
                rows.append({**pt, "pass": p.get("pass"), "doc_id": doc.get("doc_id")})
    return rows


def doc_texts(corpus: dict) -> dict[str, str]:
    return {d["doc_id"]: " ".join(t["content"] for t in d["turns"])
            for d in corpus["documents"]}


#: ⛔ Common English FUNCTION words, removed before any overlap is computed. Without
#: this, `the wholesale push` scored 0.667 against doc-05 on `the` + `wholesale` alone and
#: cleared T5's 0.6 paraphrase band — an endpoint name the corpus never says, declared
#: supported on a function word. A `content-token` overlap that counts `the` is not a
#: content-token overlap, and every band below (T5's 0.6, T1's 0.8, T3's 0.8, T2's 0.45)
#: is stated in those terms.
STOPWORDS = frozenset((
    "a", "an", "the", "and", "or", "but",
    "if", "then", "than", "so", "of", "to",
    "in", "on", "at", "for", "with", "by",
    "from", "as", "is", "are", "was", "were",
    "be", "been", "being", "has", "have", "had",
    "do", "does", "did", "not", "no", "nor",
    "up", "out", "into", "onto", "about", "over",
    "under", "above", "below", "more", "most", "less",
    "least", "also", "just", "only", "all", "any",
    "some", "each", "every", "per", "after", "before",
    "between", "while", "when", "where", "which", "who",
    "whom", "what", "how", "why", "its", "it",
    "this", "that", "these", "those", "we", "our",
    "you", "your", "they", "their", "he", "she",
    "his", "her", "them", "us", "there", "here",
    "can", "cannot", "could", "may", "might", "must",
    "shall", "should", "will", "would",
))


def norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def tokens(text) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", norm(text))
            if len(t) > 2 and t not in STOPWORDS}


def overlap(text, reference: str) -> float:
    """Share of `text`'s content tokens that occur in `reference` (0..1)."""
    a, b = tokens(text), tokens(reference)
    return (len(a & b) / len(a)) if a else 0.0


# ── T1 ──────────────────────────────────────────────────────────────────────
def t1(receipt: dict) -> dict:
    passes = receipt["passes"]
    p1, p2 = passes[0], passes[1]
    c_before = counts(p1["overview_before"])
    c_after_p1 = counts(p1["overview_after"])
    c_after_p2 = counts(p2["overview_after"])
    rel1 = relation_rows(receipt, 1)
    rel2 = relation_rows(receipt, 2)

    def entity_total(c: dict) -> int:
        return sum(v for k, v in (c.get("taxonomy") or {}).items()
                   if k in ("Object", "Subject", "Event"))

    # Same-name entities: the same thing counted twice. Names are read off the
    # typed full scan, so a nameless row cannot hide a duplicate.
    readback = load("readback.json")
    by_name: dict[str, list] = {}
    for ent in readback.get("entities") or []:
        by_name.setdefault(norm(ent.get("name")), []).append(ent["id"])
    dup_names = {name: ids for name, ids in by_name.items() if name and len(ids) > 1}

    # Second-pass attachment: did pass 2's points dedup onto what existed, or did
    # they mint fresh rows? The product reports its own verdict per point, so the
    # number is the product's, not this script's guess.
    p2_points = points(receipt, 2)
    dedup_hist: dict[str, int] = {}
    for pt in p2_points:
        key = str(pt.get("dedup") or "unspecified")
        dedup_hist[key] = dedup_hist.get(key, 0) + 1
    p2_stats = [(d.get("result") or {}).get("stats") or {}
                for d in p2["documents"]]
    merges = sum(int(s.get("merges") or 0) for s in p2_stats)
    deletions = sum(int(s.get("deletions") or 0) for s in p2_stats)
    new_points_p2 = sum(int(s.get("points") or 0) for s in p2_stats)

    d_entities = entity_total(c_after_p2) - entity_total(c_after_p1)
    d_rel = len(rel2) - len(rel1)
    d_sources = c_after_p2["source_rows"] - c_after_p1["source_rows"]

    # ⛔ THE MECHANISM NUMBER: how many pass-2 points merely re-state a pass-1
    # point. Counting graph deltas says the graph grew; this says WHAT grew, which
    # is what a fix has to act on. The product's own `dedup` verdict is recorded
    # beside it — a content_hash hit means it recognised the duplication and
    # created the row anyway, which is a different defect from not recognising it.
    p1_points = points(receipt, 1)
    p1_texts = [str(pt.get("text") or "") for pt in p1_points]
    restated = []
    for pt in p2_points:
        text = str(pt.get("text") or "")
        best, best_ref = 0.0, ""
        for ref in p1_texts:
            score = overlap(text, ref)
            if score > best:
                best, best_ref = score, ref
        if best >= 0.8:
            restated.append({"point_id": pt.get("point_id") or pt.get("id"),
                             "doc_id": pt.get("doc_id"),
                             "dedup": pt.get("dedup"),
                             "overlap": round(best, 2),
                             "text": text[:180],
                             "restates": best_ref[:180]})

    # The endpoints pass 2 introduced that pass 1 never named: the new-entity
    # population, resolved to names through the readback's own door reads.
    p1_ids = {x for e in relation_rows(receipt, 1)
              for x in (e.get("src_id"), e.get("dst_id")) if x}
    p2_ids = {x for e in relation_rows(receipt, 2)
              for x in (e.get("src_id"), e.get("dst_id")) if x}
    new_ids = sorted(p2_ids - p1_ids)
    neighbours = readback.get("neighbours") or {}
    new_entities = []
    for eid in new_ids:
        reply = ((neighbours.get(eid) or {}).get("get_entity") or {}).get("reply") or {}
        new_entities.append({
            "id": eid,
            "name": str(reply.get("name") or reply.get("content") or ""),
            "kind": str(reply.get("objectKind") or reply.get("subjectKind") or ""),
            "type": reply.get("type"),
        })

    d_pass_2 = delta(c_after_p1, c_after_p2)
    object_delta = d_pass_2.get("taxonomy.Object", 0)
    subject_delta = d_pass_2.get("taxonomy.Subject", 0)
    verdict = "PASS" if (object_delta <= 0 and subject_delta <= 0
                         and not dup_names and not restated) else "FAIL"
    return {
        "test": "T1",
        "rule": RULE_T1,
        "counts": {
            "empty_before": c_before,
            "after_pass_1": c_after_p1,
            "after_pass_2": c_after_p2,
            "delta_pass_1": delta(c_before, c_after_p1),
            "delta_pass_2": delta(c_after_p1, c_after_p2),
        },
        "entities": {
            "object_subject_event_pass_1": entity_total(c_after_p1),
            "object_subject_event_pass_2": entity_total(c_after_p2),
            "delta": d_entities,
            "object_delta": object_delta,
            "subject_delta": subject_delta,
            "new_endpoints_pass_2": new_entities,
        },
        "restated_points_pass_2": {
            "pass_2_points": len(p2_points),
            "restating_a_pass_1_point": len(restated),
            "share": round(len(restated) / len(p2_points), 3) if p2_points else None,
            "rows": restated[:40],
        },
        "relations": {
            "written_pass_1": len(rel1),
            "written_pass_2": len(rel2),
            "delta": d_rel,
            "dropped_pass_1": sum(len(((d.get("result") or {}).get("relation_write")
                                       or {}).get("drops") or []) for d in p1["documents"]),
            "dropped_pass_2": sum(len(((d.get("result") or {}).get("relation_write")
                                       or {}).get("drops") or []) for d in p2["documents"]),
        },
        "sources": {"pass_1": c_after_p1["source_rows"],
                    "pass_2": c_after_p2["source_rows"], "delta": d_sources},
        "second_pass_product_accounting": {
            "points_reported": new_points_p2,
            "dedup_verdicts": dedup_hist,
            "merges": merges,
            "deletions": deletions,
        },
        "same_name_entities_after_both_passes": dup_names,
        "verdict": verdict,
    }


# ── T2 ──────────────────────────────────────────────────────────────────────
def t2(receipt: dict, corpus: dict) -> dict:
    fact = corpus["_declared_facts"]["goal"]
    texts = doc_texts(corpus)
    wording_a = norm(fact["wording_a"])
    wording_b = norm(fact["wording_b"])

    # The measured window is doc-03 -> doc-06 inside PASS 1: that is where "+0
    # entities" is a per-document number rather than an aggregate.
    p1 = receipt["passes"][0]
    by_doc = {d["doc_id"]: d for d in p1["documents"]}
    doc06 = by_doc["doc-06"]
    c_before = counts(doc06["overview_before"])
    c_after = counts(doc06["overview_after"])
    d = delta(c_before, c_after)

    # The goal fact's points, from both wording documents.
    scored = []
    for pt in points(receipt, 1):
        if pt["doc_id"] not in fact["stated_in"]:
            continue
        text = str(pt.get("text") or "")
        best = max(overlap(text, wording_a), overlap(text, wording_b),
                   overlap(fact["wording_a"], text))
        if best >= 0.45:
            scored.append({"doc_id": pt["doc_id"], "point_id": pt.get("point_id")
                           or pt.get("id"), "kind": pt.get("kind"),
                           "dedup": pt.get("dedup"), "text": text[:220],
                           "overlap": round(best, 2)})

    # Two wordings landing as ONE point = the id repeats across the two documents;
    # two points = the fact was counted twice. Distinct ids are necessary but not
    # sufficient — a dedup'd second wording would keep the id, a re-minted one
    # would not, and the product's own `dedup` verdict says which happened.
    ids_a = {row["point_id"] for row in scored
             if row["doc_id"] == "doc-03" and row["point_id"]}
    ids_b = {row["point_id"] for row in scored
             if row["doc_id"] == "doc-06" and row["point_id"]}
    shared = ids_a & ids_b

    readback = load("readback.json")
    probe_ids = {q: v.get("ids") for q, v in (readback.get("probes") or {}).items()}

    # RULE_T2 states THREE legs, and each is now either GRADED or DISCLOSED:
    #   * the entity delta — graded;
    #   * the goal resolving to ONE thing — graded as a RESOLUTION (every doc-06
    #     candidate reuses a doc-03 id), never as a count. It was `len(shared) == 1`,
    #     which INVERTED the signal on this corpus: the fact is two sentences per
    #     wording, so a complete 1:1 merge shares TWO ids (FAIL) and a HALF merge
    #     shares one (PASS) — the acceptance gate was open on a partial fix and shut
    #     on the fix it asks for (review round 3, finding 2);
    #   * "carrying two sources" — NOT gradeable from this artifact. The `sources`
    #     door answers per-Source POINT COUNTS, not point->Source links, so the
    #     question cannot be put to the committed readback at all. Recorded as gap
    #     D7 and reported beside the verdict instead of being silently implied.
    # ⛔ `taxonomy.Event` is deliberately NOT in this sum — see RULE_T2: one Event per
    # capture is what RULE_T1 calls expected, and doc-06's window is a single capture. A
    # fix that removed the duplicate Object and merged the points would otherwise still
    # fail T2 on an axis this bundle declares clean.
    entity_delta = (d.get("taxonomy.Object", 0) + d.get("taxonomy.Subject", 0))
    unresolved = sorted(ids_b - ids_a)
    one_goal = bool(ids_b) and bool(ids_a) and not unresolved
    verdict = "PASS" if (entity_delta == 0 and one_goal) else "FAIL"
    return {
        "test": "T2",
        "rule": RULE_T2,
        "pair": fact["stated_in"],
        "wording_a": fact["wording_a"],
        "wording_b": fact["wording_b"],
        "delta_across_doc_06_ingest": d,
        "entities_before": c_before, "entities_after": c_after,
        "goal_point_candidates": scored,
        "goal_point_ids": {"doc_03": sorted(ids_a), "doc_06": sorted(ids_b),
                           "shared": sorted(shared)},
        "legs": {"entity_delta_zero": entity_delta == 0,
                 "entity_axes": "Object + Subject (Event excluded — RULE_T1 declares one "
                                "Event per capture expected)",
                 "goal_resolves_to_one_thing": one_goal,
                 "doc_06_ids_unresolved_in_doc_03": unresolved,
                 "shared_count": len(shared),
                 "granularity": ("the goal fact is stated in two sentences per wording, "
                                 "so a COMPLETE merge resolves two ids — a count of shared "
                                 "ids asks the wrong question in both directions")},
        "source_link_leg": {
            "graded": False,
            "why": ("D7 — the `sources` door answers per-Source POINT COUNTS, not "
                    "point->Source links, so 'the shared point carries two sources' "
                    "cannot be asked of the committed readback. RULE_T2 grades the "
                    "entity delta and the shared id; this is the gap."),
        },
        "sources": {"before": c_before["source_rows"], "after": c_after["source_rows"],
                    "delta": c_after["source_rows"] - c_before["source_rows"]},
        "retrieval_probe_ids": probe_ids,
        "probe_texts": {q: [str(r.get("content") or r.get("name") or "")[:200]
                            for r in rows(v.get("reply"))[:4]]
                        for q, v in (readback.get("probes") or {}).items()},
        "probe_sessions": {q: [r.get("sessionId") or r.get("session_id")
                               for r in rows(v.get("reply"))[:8]]
                           for q, v in (readback.get("probes") or {}).items()},
        "corpus_overlap": {"wording_a_vs_doc03_text":
                           round(overlap(fact["wording_a"], texts["doc-03"]), 2),
                           "wording_b_vs_doc06_text":
                           round(overlap(fact["wording_b"], texts["doc-06"]), 2),
                           "wording_b_vs_wording_a":
                           round(overlap(fact["wording_b"], wording_a), 2)},
        "verdict": verdict,
    }


# ── T3 ──────────────────────────────────────────────────────────────────────
def t3(receipt: dict, corpus: dict) -> dict:
    texts = doc_texts(corpus)
    readback = load("readback.json")
    entities = readback.get("entities") or []
    linked_ids = {eid for eid in
                  {x for row in relation_rows(receipt) for x in
                   (row.get("src_id"), row.get("dst_id")) if x}}
    neighbours = readback.get("neighbours") or {}

    rows = []
    for ent in entities:
        eid = ent["id"]
        door = neighbours.get(eid) or {}
        rows.append({
            "id": eid,
            "name": ent.get("name"),
            "kind": ent.get("kind"),
            "linked_by_write_receipt": eid in linked_ids,
            "door_connected_total": door.get("connected_total"),
            "door_connected_counts": door.get("connected_counts"),
        })
    orphans_receipt = [r for r in rows if not r["linked_by_write_receipt"]]
    orphans_door = [r for r in rows if r["door_connected_total"] in (0, None)]
    named_in_corpus = []
    for row in orphans_receipt:
        name = norm(row.get("name"))
        if not name:
            continue
        hits = [doc for doc, text in texts.items() if name and name in norm(text)]
        # ⛔ The band is RULE_T3's band — "content-token overlap ... the same near-match
        # band T1 uses" — so it is `overlap()`, NOT a second tokenisation. This used to
        # filter to `len(t) > 4` while the rule printed `>2`, which changed the
        # denominator and the ANSWER: `the ownership map` tokenises to {ownership, map},
        # scores 0.500 against doc-04 at the rule's own band, and 1.000 with `map`
        # dropped by the extra filter (review round 3, finding 1).
        scored = sorted(((overlap(name, text), doc) for doc, text in texts.items()),
                        key=lambda pair: (-pair[0], pair[1]))
        best = scored[0] if scored else (0.0, None)
        partial = [doc for score, doc in scored if score >= 0.8]
        named_in_corpus.append({"name": row.get("name"), "kind": row.get("kind"),
                                "exact_doc_hits": hits, "near_doc_hits": partial,
                                "best_overlap": {"doc": best[1],
                                                 "share": round(best[0], 3)}})
    # RULE_T3 has TWO conjuncts and BOTH are graded: the share, AND that every orphan
    # named is explainable as a standalone fact in the corpus text. The second was
    # computed and then dropped before #14812's review — the rule quoted beside the
    # verdict was not the rule that produced it, and by the quoted rule T3 was FAIL
    # (two orphans match no document at all). `>= 0.8` of an orphan's own long tokens
    # is the same near-match band the T1 restatement test uses, so "traceable" means
    # the same thing in both places.
    # The second conjunct's predicate is NAME PRESENCE, and `RULE_T3` now says so with
    # its band: exact, or >= 0.8 of the name's own >4-character tokens. ⛔ That band is
    # T1's RESTATEMENT band, not T5's NAMING band (0.6), and the two are not
    # interchangeable: at 0.6 `chilled-goods operation` (0.667 against doc-02) would be
    # traceable, at 0.8 it is not. ⛔ It is also a NAME test, not the rule's full
    # "standalone in the corpus text" — `restaurant account` is counted explained because
    # the name is verbatim, although doc-03 relates it ("Every new restaurant account
    # needs a morning delivery slot"). So this conjunct catches an invented NAME; it does
    # not catch an entity that is real but not standalone. Both limits are stated in
    # REPORT §5 beside the count rather than left for a reader to assume away.
    unexplained = [r for r in named_in_corpus
                   if not r["exact_doc_hits"] and not r["near_doc_hits"]]
    share_receipt = (len(orphans_receipt) / len(rows)) if rows else None
    share_door = (len(orphans_door) / len(rows)) if rows else None
    # A MAJORITY is MORE than half, so "the majority are not orphans" needs a strict
    # `share < 0.5`. It was spelled as the ORPHAN side (`share > 0.5`), which returns
    # PASS at exactly half — where NEITHER side is a majority, so the conjunct this
    # rule prints is false and the verdict must be FAIL (review round 2, finding 2).
    # The boundary is graded by `test_t3_share_leg_is_graded_at_the_majority_boundary`.
    majority_are_not_orphans = share_receipt is not None and share_receipt < 0.5
    every_orphan_explained = not unexplained
    if not rows:
        verdict = "NO-DATA"   # an empty population satisfies neither side of the share
    elif not majority_are_not_orphans or not every_orphan_explained:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    return {
        "test": "T3",
        "rule": RULE_T3,
        "population": {
            "entities_enumerated_by_full_scan": len(rows),
            "note": ("population is every ENTITY the typed full scan resolves; "
                     "Points are excluded because a Point's edges are epistemic "
                     "bindings the write receipt does not enumerate — recorded "
                     "as a gap (REPORT §8)"),
        },
        "orphans_by_write_receipt": {"count": len(orphans_receipt),
                                     "share": None if share_receipt is None
                                     else round(share_receipt, 3),
                                     "rows": orphans_receipt},
        "orphans_by_door": {"count": len(orphans_door),
                            "share": None if share_door is None
                            else round(share_door, 3),
                            "rows": [{"name": r["name"], "kind": r["kind"]}
                                     for r in orphans_door]},
        "orphan_traceability": named_in_corpus,
        "orphan_legs": {
            "majority_are_not_orphans": majority_are_not_orphans,
            "every_orphan_explained_in_corpus": every_orphan_explained,
            "unexplained": [{"name": r["name"], "kind": r["kind"]}
                            for r in unexplained],
            "note": ("both conjuncts of RULE_T3 are graded; a row here has no exact "
                     "or near document hit, so the corpus never says it"),
        },
        "verdict": verdict,
    }


# ── T4 ──────────────────────────────────────────────────────────────────────
def t4(receipt: dict, corpus: dict) -> dict:
    readback = load("readback.json")
    overview = readback.get("overview") or {}
    c = counts(overview)
    kinds = {}
    for ent in readback.get("entities") or []:
        kinds[ent.get("kind")] = kinds.get(ent.get("kind"), 0) + 1
    scans = {k: v.get("count") for k, v in (readback.get("scans") or {}).items()}

    # The decision leg: a decision must land as an event/decision kind, not as a
    # bare statement. doc-01 and doc-05 both state one.
    decision_points = [pt for pt in points(receipt, 1)
                       if norm(pt.get("kind")) in ("event", "decision", "eventrecord")
                       or re.search(r"\bdecision\b|\bchoose|\brevert\b",
                                    norm(pt.get("text")) or "")]
    # The decision leg: `RULE_T4` asks that a decision be represented by a
    # decision-kind ENTITY. Graded on the ENTITY count, not on a graph-wide tally of
    # event-kind Points: before #14812's second review this read `event_kinds > 0`,
    # which 80 unrelated Event Points satisfied regardless of how the decisions landed.
    decision_entities = [e for e in (readback.get("entities") or [])
                         if "decision" in norm(e.get("kind"))]
    event_kinds = c["points_by_kind"].get("event", 0)

    # The role leg: roles must land as SUBJECT-layer role entities — not merely "some
    # subject-layer entity exists", which any one naturalPerson satisfied.
    role_entities = [e for e in (readback.get("entities") or [])
                     if norm(e.get("kind")).endswith(":role")
                     and e.get("layer") == "subject"]
    sources = c["source_rows"]
    docs = len(corpus["documents"])

    decision_leg = bool(decision_entities)
    role_leg = bool(role_entities)
    layers_leg = (c["taxonomy"].get("Event", 0) > 0
                  and c["taxonomy"].get("Subject", 0) > 0
                  and c["taxonomy"].get("Object", 0) > 0
                  and c["taxonomy"].get("Source", 0) >= docs)
    verdict = "PASS" if (layers_leg and decision_leg and role_leg) else "FAIL"
    return {
        "test": "T4",
        "rule": RULE_T4,
        "flat_distribution": c["taxonomy"],
        "points_by_kind": c["points_by_kind"],
        "structure_layer": {"total": c["structure_total"],
                            "operators": c["operators"]},
        "entity_kinds_seen": kinds,
        "per_kind_full_scan": scans,
        "decision_leg": {"graded": decision_leg,
                         "decision_entities": [{"name": e.get("name"),
                                                "kind": e.get("kind")}
                                               for e in decision_entities],
                         "decisions_stated": len(decision_points),
                         "decision_point_kinds": sorted({p.get("kind") for p in decision_points
                                                         if p.get("kind")}),
                         "events": c["taxonomy"].get("Event", 0),
                         "event_point_kind_rows": event_kinds,
                         "decision_wording_points": [
                             {"doc_id": p.get("doc_id"), "kind": p.get("kind"),
                              "text": str(p.get("text"))[:180]}
                             for p in decision_points[:12]]},
        "role_leg": {"graded": role_leg, "subject_entities": len(role_entities),
                     "examples": [e.get("name") for e in role_entities[:10]]},
        "document_leg": {"sources": sources, "documents_ingested": docs,
                         "sources_with_points": c["sources_with_points"]},
        "verdict": verdict,
    }


# ── T5 ──────────────────────────────────────────────────────────────────────
def t5(receipt: dict, corpus: dict) -> dict:
    texts = doc_texts(corpus)
    readback = load("readback.json")
    names: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for eid, entry in (readback.get("neighbours") or {}).items():
        reply = (entry.get("get_entity") or {}).get("reply") or {}
        if isinstance(reply, dict):
            names[eid] = str(reply.get("name") or reply.get("content") or "")
            kinds[eid] = str(reply.get("objectKind") or reply.get("subjectKind") or "")

    def trace(endpoint: str) -> dict:
        """Where one endpoint's NAME is stated — verbatim first, then paraphrase.

        ⛔ TWO TIERS, because "the endpoint's name is not a substring of the text"
        is not the same finding as "the text never makes this claim". The extractor
        renames (T6 measured `ingest-gateway` stored as `ingest-gateway
        component`), and a single-tier substring test would score a RENAME as an
        invented edge — the inverse of the error this test exists to catch. So a
        paraphrase tier is measured explicitly (best document, content-token
        overlap >= 0.6) and reported beside the verbatim tier, and only an
        endpoint with NEITHER is unsupported.
        """
        if not endpoint:
            return {"verbatim_docs": [], "paraphrase_docs": [], "best_overlap": 0.0}
        exact = [doc for doc, text in texts.items() if norm(endpoint) in norm(text)]
        scores = {doc: overlap(endpoint, text) for doc, text in texts.items()}
        best = max(scores.values()) if scores else 0.0
        para = [doc for doc, score in scores.items() if score >= 0.6]
        return {"verbatim_docs": exact, "paraphrase_docs": para,
                "best_overlap": round(best, 2)}

    rows_out = relation_rows(receipt)
    samples = []
    for edge in rows_out:
        src_id, dst_id = edge.get("src_id") or "", edge.get("dst_id") or ""
        src, dst = names.get(src_id, ""), names.get(dst_id, "")
        predicate = edge.get("predicate") or ""
        a, b = trace(src), trace(dst)
        co_verbatim = sorted(set(a["verbatim_docs"]) & set(b["verbatim_docs"]))
        co_paraphrase = sorted(set(a["verbatim_docs"] + a["paraphrase_docs"])
                               & set(b["verbatim_docs"] + b["paraphrase_docs"]))
        support = "verbatim" if co_verbatim else (
            "paraphrase" if co_paraphrase else "none")
        samples.append({
            "doc_id": edge.get("doc_id"), "pass": edge.get("pass"),
            "predicate": predicate,
            "stated_predicate": edge.get("stated_predicate"),
            "write_path": edge.get("write_path"),
            "src": src or src_id, "dst": dst or dst_id,
            "src_kind": kinds.get(src_id, ""), "dst_kind": kinds.get(dst_id, ""),
            "src_trace": a, "dst_trace": b,
            "co_stated_verbatim": co_verbatim,
            "co_stated_paraphrase": co_paraphrase,
            "support": support,
        })
    unsupported = [s for s in samples if s["support"] == "none"]
    tiers = {t: sum(1 for s in samples if s["support"] == t)
             for t in ("verbatim", "paraphrase", "none")}
    # ── D6: the PREDICATE axis, MEASURED but not graded ────────────────────────
    # `RULE_T5` grades the ENDPOINTS. The predicate is a different claim, and it is
    # NOT graded here for the reason the rule states: the extractor writes ontology
    # predicate names into a corpus of English prose, so a substring test on the
    # predicate's spelling would score a correct normalisation (`ownedBy` for
    # "owns") as an invented edge — the exact rename-vs-invention confusion
    # `trace()`'s own docstring refuses one level down. What the bundle CAN do
    # honestly is measure the gap and hand it to a reader as a number: for each
    # sampled row, do the documents that co-state its endpoints also contain the
    # predicate's own wording? Unmapped rows are counted separately rather than
    # folded into either side, so a predicate the corpus cannot carry is never
    # silently reported as supported OR as an invention.
    predicate_axis = []
    for s in samples:
        docs = s["co_stated_verbatim"] or s["co_stated_paraphrase"]
        wording = {(s["predicate"] or "").lower(),
                   (s["stated_predicate"] or "").lower()} - {""}
        found = sorted(doc for doc in docs
                       if any(w in norm(texts.get(doc, "")) for w in wording))
        predicate_axis.append({"predicate": s["predicate"],
                               "stated_predicate": s["stated_predicate"],
                               "wording_in_co_stated_docs": found})
    predicate_graded_in_corpus = sum(1 for r in predicate_axis
                                    if r["wording_in_co_stated_docs"])
    # The rule's number is the NONE tier: a paraphrase is the extractor's own
    # re-wording of a stated fact, an unsupported row is a claim the corpus never made.
    verdict = "PASS" if samples and not unsupported else ("FAIL" if samples else "NO-DATA")
    return {
        "test": "T5",
        "rule": RULE_T5,
        "sampled": len(samples),
        "tiers": tiers,
        "supported": len(samples) - len(unsupported),
        "unsupported": unsupported,
        "rows": samples,
        "predicate_histogram": {p: sum(1 for s in samples if s["predicate"] == p)
                                for p in sorted({s["predicate"] for s in samples})},
        "predicate_axis": {
            "graded": False,
            "why": ("D6 — the graph's predicates are ontology names and the corpus is "
                    "prose, so a wording test scores a correct normalisation as an "
                    "invention. RULE_T5 grades the endpoints; this is the gap."),
            "sampled": len(samples),
            "predicate_wording_in_co_stated_docs": predicate_graded_in_corpus,
            "rows": predicate_axis,
        },
        "verdict": verdict,
    }


def build_result() -> dict:
    """The whole artifact, as a value — `main()` writes exactly this.

    Split out so the bundle's suite can compare the committed file to a fresh build
    BYTE for byte (`json.dumps(build_result(), indent=2, default=str) + "\n"` is what
    `main()` writes). A test that compared parsed JSON round-trips was blind to dict KEY
    ORDER, so an artifact re-sorted in place stayed green while a reviewer's re-run
    produced a diff — the one property the bundle sells.
    """
    receipt = load("ingest-receipt.json")
    corpus = json.loads((HERE / "corpus" / "noise-corpus.json").read_text())

    result = {
        "provenance": {
            "corpus": str((HERE / "corpus" / "noise-corpus.json").relative_to(HERE.parent.parent.parent)),
            "corpus_chars": receipt.get("corpus_chars"),
            "graph": receipt.get("graph_name"),
            "db_uri": receipt.get("db_uri"),
            "scorer": "docs/evidence/14691-noise-validation/score_t1_t5.py",
        },
        "tests": {},
        "rules": {"T1": RULE_T1, "T2": RULE_T2, "T3": RULE_T3,
                  "T4": RULE_T4, "T5": RULE_T5},
    }
    for name, fn in (("T1", lambda: t1(receipt)),
                     ("T2", lambda: t2(receipt, corpus)),
                     ("T3", lambda: t3(receipt, corpus)),
                     ("T4", lambda: t4(receipt, corpus)),
                     ("T5", lambda: t5(receipt, corpus))):
        result["tests"][name] = fn()

    return result


def main() -> int:
    result = build_result()
    (OUT / "t1-t5-score.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n")

    print("\n=== THE FIVE ===")
    for name, row in result["tests"].items():
        print(f"{name}: {row['verdict']}")
    print("\nwrote", OUT / "t1-t5-score.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
