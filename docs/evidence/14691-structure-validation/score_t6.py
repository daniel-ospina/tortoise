#!/usr/bin/env python
"""#14691 T6 — step 3: score the five assertions from the receipts, not by eye.

Everything here is DERIVED from two committed artifacts:

  * `out/ingest-receipt.json` — what the product's write door reported writing
    (`relation_write.edges`, `warnings[]`, `stats.llm`)
  * `out/readback.json`      — what the product's MCP doors answered

No number in this file is typed by hand: the declared structure comes from the
corpus fixture (`corpus/structure-corpus.json` → `_declared_structure`), the
orientation semantics come from the product's OWN table
(`tortoise/pack_registry.SEMANTICS_DIRECTIONS`, imported — not restated), and the
edge list is read out of the write receipt.

⛔ This is the #14606 review's rule applied: *"No exit code can stop an operator
from committing a receipt a failing run wrote"* — so the contradiction has to be
INSIDE the artifact. Every assertion carries its own numbers, its verdict, and
the evidence rows the verdict came from.

WHY EACH ORIENTATION IS WHAT IT IS (read before changing a rule here):
  * `ownedBy` `->` — ONTOLOGY §3.6: "`->` = the TARGET owns the source
    (`investment --ownedBy--> fund`)". So a correct `ownedBy` row has the OWNED
    THING as `src` and the OWNER as `dst`.
  * `managedBy` `->` — §3.6: `Entity → Subject`, "operational responsibility".
    Correct row: managed entity as `src`, responsible Subject as `dst`.
  * `dependsOn` `->` — §8: `->` (source→target). Correct row: the DEPENDENT as
    `src`, the DEPENDENCY as `dst`.
  * `produces` `<-` — §8: the TARGET brought the source into existence. Correct
    row: the PRODUCED thing as `src`, the PRODUCER as `dst`.
  * `hasPart` `<->` — composition, both ways; the DECLARED pair
    (`product-strategy`: `contains`, `product → feature`) fixes which end is the
    parent, and this scorer checks that separately from the arrow token.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

CORPUS = json.loads((HERE / "corpus" / "structure-corpus.json").read_text())
DECL = CORPUS["_declared_structure"]
RECEIPT = json.loads((OUT / "ingest-receipt.json").read_text())
READBACK = json.loads((OUT / "readback.json").read_text())

CORPUS_TEXT = "\n".join(turn["content"]
                        for doc in CORPUS["documents"] for turn in doc["turns"])


def _norm(text) -> str:
    return " ".join(str(text or "").lower().split())


CORPUS_NORM = _norm(CORPUS_TEXT)

#: The product's own ruled table. Imported, never restated: a local copy would be
#: a second opinion about a contract `pack_registry` owns (#11548's finding).
try:
    from tortoise.pack_registry import SEMANTICS_DIRECTIONS as DIRECTIONS
    DIRECTIONS_SOURCE = "tortoise.pack_registry.SEMANTICS_DIRECTIONS"
except Exception as exc:  # noqa: BLE001 — a read that fails is RECORDED
    DIRECTIONS = {}
    DIRECTIONS_SOURCE = f"UNAVAILABLE: {type(exc).__name__}: {exc}"

#: Which end the arrow must point at, per predicate, given the direction token.
#: `at` names the role the DST must play for the row to assert the corpus fact.
_DST_ROLE = {
    "ownedBy": "owner",
    "managedBy": "owner",
    "dependsOn": "dependency",
    "produces": "producer",
}


def rows(reply, keys=("result", "results", "relationships", "nodes", "items",
                      "connected")):
    """Normalise an MCP reply to a list of rows (three shapes, one unwrap)."""
    if isinstance(reply, str):
        try:
            return rows(json.loads(reply), keys)
        except (ValueError, TypeError):
            return []
    if isinstance(reply, dict):
        for key in keys:
            if isinstance(reply.get(key), list):
                return reply[key]
        return []
    if isinstance(reply, list):
        if len(reply) == 1 and isinstance(reply[0], str):
            return rows(reply[0], keys)
        return [row for row in reply if isinstance(row, dict)]
    return []


# ── the entity map: id → {name, kind} ─────────────────────────────────────
def entity_map() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for eid, call in (READBACK.get("edge_endpoints") or {}).items():
        props = call.get("reply") if isinstance(call.get("reply"), dict) else {}
        out[eid] = {
            "name": props.get("name") or props.get("content")
                    or props.get("canonical_name") or eid,
            "kind": props.get("objectKind") or props.get("subjectKind") or "",
            "label": props.get("type") or props.get("label") or "",
        }
    # The typed scans resolved the same nodes under their DECLARED kind — fill any
    # gap the endpoint read left (an id can be absent from get_entity while the
    # scan still returned it, and the reverse).
    for group in ("object_kinds", "subject_kinds"):
        for qualified, call in (READBACK.get(group) or {}).items():
            for row in rows(call.get("reply")):
                eid = row.get("id")
                if not eid:
                    continue
                rec = out.setdefault(eid, {})
                rec.setdefault("name", row.get("content") or row.get("name"))
                rec["kind"] = rec.get("kind") or qualified
    return out


ENTITIES = entity_map()


def name_of(eid: str) -> str:
    return (ENTITIES.get(eid) or {}).get("name") or eid


# ── the edge list the WRITE door reported ─────────────────────────────────
def edges() -> list[dict]:
    out: list[dict] = []
    for doc in RECEIPT.get("documents") or []:
        res = doc.get("result") or {}
        rw = res.get("relation_write") or {}
        for edge in rw.get("edges") or []:
            src, dst = edge.get("src_id"), edge.get("dst_id")
            out.append({
                "doc": doc.get("doc_id"),
                "predicate": edge.get("predicate"),
                "write_path": edge.get("write_path"),
                "src_id": src, "dst_id": dst,
                "src": name_of(src), "dst": name_of(dst),
                "src_kind": (ENTITIES.get(src) or {}).get("kind", ""),
                "dst_kind": (ENTITIES.get(dst) or {}).get("kind", ""),
            })
    return out


EDGES = edges()

#: The kind→role map the assertions are scored against. `component` is not a
#: declared kind anywhere in the shipped packs, so every kind a component can
#: land in is accepted as a component HERE — the absence of the kind is scored
#: under assertion 1, not smuggled into the edge checks.
_ROLE_KINDS = {
    "product": {"product-strategy:product"},
    "feature": {"product-strategy:feature"},
    "customer": {"product-strategy:customer"},
    "customerSegment": {"product-strategy:customerSegment"},
    "team": {"core:team"},
    "role": {"core:role"},
    "person": {"core:naturalPerson"},
    "component": {"dev:code", "dev:software", "dev:infrastructure", "dev:api",
                  "dev:database", "product-strategy:architecture",
                  "core:workflow", "core:other"},
}


def _same_name(got: str, want: str) -> str | None:
    """`exact` / `prefix` / None — how a reported endpoint names a declared one.

    The extractor welds the corpus's collective noun onto a name: the corpus says
    "Dana Whitlock owns the ingest-gateway component", and the node is stored as
    `ingest-gateway component`. A matcher that only accepts the exact string
    reports a MISSING edge where the edge exists (and that is a scoring bug, not
    a finding) — so the prefix arm exists, and the variance it tolerates is
    reported separately as the naming observation it is.
    """
    g, w = _norm(got), _norm(want)
    if g == w:
        return "exact"
    if g.startswith(w) or w.startswith(g):
        return "prefix"
    return None


def connects(a_name: str, b_name: str) -> list[dict]:
    """Every reported edge between these two names, either direction."""
    out = []
    for e in EDGES:
        fwd = _same_name(e["src"], a_name) and _same_name(e["dst"], b_name)
        rev = _same_name(e["src"], b_name) and _same_name(e["dst"], a_name)
        if fwd or rev:
            out.append({**e, "name_match": fwd or rev})
    return out


def _compact(edge: dict) -> dict:
    return {k: edge.get(k) for k in ("doc", "predicate", "src", "dst",
                                     "src_kind", "dst_kind", "write_path")}


# ── assertion 1: every declared structure appears, with the right type ────
def a1_type_coverage() -> dict:
    declared = ([(n, "product") for n in DECL["products"]]
                + [(n, "feature") for n in DECL["features"]]
                + [(n, "team") for n in DECL["teams"]]
                + [(n, "role") for n in DECL["roles"]]
                + [(n, "person") for n in DECL["people"]]
                + [(n, "component") for n in DECL["components"]]
                + [(n, "customer") for n in DECL["customers"]]
                + [(n, "customerSegment") for n in DECL["customerSegments"]])
    by_name = [(v.get("name"), {"id": k, "kind": v.get("kind")})
               for k, v in ENTITIES.items()]
    per_type: dict[str, dict] = {}
    missing: list[dict] = []
    mis_typed: list[dict] = []
    name_variants: list[dict] = []
    for name, role in declared:
        hit = None
        match_kind = None
        for got, rec in by_name:
            m = _same_name(got, name)
            if m:
                hit, match_kind = rec, m
                break
        if hit is not None and match_kind == "prefix":
            name_variants.append({"declared": name, "stored": got,
                                  "role": role})
        bucket = per_type.setdefault(role, {"declared": 0, "present": 0,
                                            "right_kind": 0})
        bucket["declared"] += 1
        if not hit:
            missing.append({"name": name, "role": role})
            continue
        bucket["present"] += 1
        ok = hit.get("kind") in _ROLE_KINDS.get(role, set())
        if ok:
            bucket["right_kind"] += 1
        else:
            mis_typed.append({"name": name, "role": role,
                              "arrived_as": hit.get("kind")})
    present = sum(b["present"] for b in per_type.values())
    right = sum(b["right_kind"] for b in per_type.values())
    return {
        "declared": len(declared), "present": present, "right_kind": right,
        "per_type": per_type, "missing": missing, "mis_typed": mis_typed,
        "name_variants": name_variants,
        "passes": present == len(declared) and right == len(declared),
        "kind_population": {
            "object": {k: [r.get("content") or r.get("name")
                           for r in rows(v.get("reply"))]
                       for k, v in (READBACK.get("object_kinds") or {}).items()
                       if rows(v.get("reply"))},
            "subject": {k: [r.get("content") or r.get("name")
                            for r in rows(v.get("reply"))]
                        for k, v in (READBACK.get("subject_kinds") or {}).items()
                        if rows(v.get("reply"))},
        },
    }


# ── assertion 2: containment/ownership edges present AND oriented ─────────
def a2_edges() -> dict:
    # (a) containment product→feature
    containment = []
    for product, feature in DECL["containment"]:
        found = connects(product, feature)
        # The DECLARED pair is `product → feature`; `hasPart` is `<->`, so the
        # check is which END is the parent, not which way the arrow token runs.
        correct = [e for e in found
                   if _norm(e["src"]) == _norm(product)
                   and _norm(e["dst"]) == _norm(feature)]
        containment.append({
            "product": product, "feature": feature,
            "present": bool(found),
            "predicates": sorted({e["predicate"] for e in found}),
            "parent_is_src": bool(correct), "edges": [_compact(e) for e in found],
        })

    # (b) membership person→team / person→role
    membership = []
    for person, team in DECL["membership"]:
        found = connects(person, team)
        membership.append({"person": person, "team": team,
                           "present": bool(found),
                           "predicates": sorted({e["predicate"] for e in found}),
                           "edges": [_compact(e) for e in found]})
    roles = []
    for role, person in zip(DECL["roles"], DECL["people"]):
        found = connects(role, person)
        roles.append({"role": role, "person": person,
                      "present": bool(found),
                      "predicates": sorted({e["predicate"] for e in found}),
                      "edges": [_compact(e) for e in found]})
    # team→role containment, the class the assertion names explicitly
    team_role = []
    for team in DECL["teams"]:
        for role in DECL["roles"]:
            found = connects(team, role)
            if found:
                team_role.append({"team": team, "role": role,
                                  "predicates": sorted({e["predicate"]
                                                        for e in found})})

    # (c) ownership thing→owner, oriented
    ownership = []
    for thing, owner in DECL["ownership"]:
        found = [e for e in connects(thing, owner)
                 if e["predicate"] in ("ownedBy", "managedBy")]
        verdicts = []
        for e in found:
            direction = DIRECTIONS.get(e["predicate"], "?")
            src_is_owner = bool(_same_name(e["src"], owner))
            if direction == "->" and _DST_ROLE.get(e["predicate"]) == "owner":
                oriented = not src_is_owner  # dst must be the owner
            else:  # pragma: no cover — only reached if the table moves
                oriented = None
            verdicts.append({"edge": _compact(e), "direction": direction,
                             "src_is_owner": src_is_owner,
                             "orientation": ("correct" if oriented
                                             else "INVERTED")})
        ownership.append({"thing": thing, "owner": owner,
                          "present": bool(found),
                          "n_edges": len(found),
                          "orientation": sorted({v["orientation"]
                                                 for v in verdicts}),
                          "verdicts": verdicts})

    # (d) dependencies dependent→dependency, oriented
    dependency = []
    for dependent, dependency_on in DECL["dependencies"]:
        found = [e for e in connects(dependent, dependency_on)
                 if e["predicate"] == "dependsOn"]
        verdicts = []
        for e in found:
            correct = bool(_same_name(e["dst"], dependency_on))
            verdicts.append({"edge": _compact(e),
                             "orientation": ("correct" if correct
                                             else "INVERTED")})
        dependency.append({"dependent": dependent,
                           "dependency": dependency_on,
                           "present": bool(found), "n_edges": len(found),
                           "orientation": sorted({v["orientation"]
                                                  for v in verdicts}),
                           "verdicts": verdicts})

    # (e) other structural predicates, for the orientation record
    other = []
    for pred in ("produces", "related", "managedBy", "IMPL", "NAND"):
        for e in EDGES:
            if e["predicate"] != pred:
                continue
            direction = DIRECTIONS.get(pred, "?")
            row = {"edge": _compact(e), "direction": direction}
            if pred == "produces":
                # `<-`: the TARGET brought the source into existence, so a
                # correct row has the PRODUCED thing as src and the PRODUCER
                # as dst. A team→component row therefore reads backwards.
                src_is_producer = (e["src_kind"] in _ROLE_KINDS["team"]
                                   | _ROLE_KINDS["person"])
                row["src_is_producer"] = src_is_producer
                row["orientation"] = ("INVERTED" if src_is_producer
                                      else "correct")
            other.append(row)

    contained = sum(1 for c in containment if c["present"])
    mem = sum(1 for m in membership if m["present"])
    role_links = sum(1 for r in roles if r["present"])
    own_ok = sum(1 for o in ownership
                 if o["orientation"] == ["correct"])
    own_inv = sum(1 for o in ownership if "INVERTED" in o["orientation"])
    dep_ok = sum(1 for d in dependency
                 if d["present"] and d["orientation"] == ["correct"])
    return {
        "containment": containment, "membership": membership,
        "role_links": roles, "team_role_containment": team_role,
        "ownership": ownership, "dependency": dependency, "other": other,
        "totals": {
            "containment_present": f"{contained}/{len(containment)}",
            "containment_parent_is_product":
                f"{sum(1 for c in containment if c['parent_is_src'])}/{len(containment)}",
            "membership_present": f"{mem}/{len(membership)}",
            "role_links_present": f"{role_links}/{len(roles)}",
            "team_to_role_edges": len(team_role),
            "ownership_present": f"{sum(1 for o in ownership if o['present'])}"
                                 f"/{len(ownership)}",
            "ownership_oriented_correct": own_ok,
            "ownership_inverted": own_inv,
            "dependency_present_and_correct": f"{dep_ok}/{len(dependency)}",
        },
        "passes": (contained == len(containment)
                   and sum(1 for c in containment if c["parent_is_src"])
                   == len(containment)
                   and own_inv == 0
                   and dep_ok == len(dependency)),
    }


# ── assertion 3: hierarchy not collapsed ─────────────────────────────────
def a3_hierarchy() -> dict:
    has_part = [e for e in EDGES if e["predicate"] == "hasPart"]
    distinct = {(e["src"], e["dst"]) for e in has_part}
    parents = {_norm(e["src"]) for e in has_part}
    children = {_norm(e["dst"]) for e in has_part}
    products = {_norm(p) for p in DECL["products"]}
    features = {_norm(f) for f in DECL["features"]}
    return {
        "declared": {"products": len(products), "features": len(features),
                     "edges": len(DECL["containment"])},
        "reported": {"hasPart_write_rows": len(has_part),
                     "hasPart_distinct_pairs": len(distinct),
                     "rewritten_by_a_later_doc": len(has_part) - len(distinct),
                     "distinct_parents": len(parents),
                     "distinct_children": len(children),
                     "parents_that_are_products": sorted(parents & products),
                     "children_that_are_features": sorted(children & features)},
        "flat_list": len(parents) == 0,
        "passes": (len(distinct) == len(DECL["containment"])
                   and parents == products and children == features),
        "edges": [_compact(e) for e in has_part],
    }


# ── assertion 4: nothing invented ────────────────────────────────────────
_QUOTE_KEY = re.compile(r"[^a-z0-9 ]+")


def _squash(text: str) -> str:
    return _QUOTE_KEY.sub("", _norm(text))


def a4_invention() -> dict:
    # (a) every structural ENTITY name appears in the corpus text
    structural = {}
    for group in ("object_kinds", "subject_kinds"):
        for qualified, call in (READBACK.get(group) or {}).items():
            names = [r.get("content") or r.get("name")
                     for r in rows(call.get("reply"))]
            if names:
                structural[qualified] = names
    untraceable = []
    for qualified, names in structural.items():
        for nm in names:
            if _squash(nm) not in _squash(CORPUS_TEXT):
                untraceable.append({"kind": qualified, "name": nm})

    # (b) every emitted QUOTE that claims to be verbatim, checked verbatim
    corpus_sents = [_norm(s) for s in re.split(r"(?<=[.!?])\s+|\n",
                                               CORPUS_TEXT) if s.strip()]

    def best_overlap(quote: str) -> float:
        """Share of the quote's words present in its best-matching corpus
        sentence. A PARAPHRASE keeps the content words; a claim the text never
        makes does not."""
        words = set(re.findall(r"[a-z]{3,}", _norm(quote)))
        if not words:
            return 0.0
        best = 0.0
        for sent in corpus_sents:
            hit = sum(1 for w in words if w in sent)
            best = max(best, hit / len(words))
        return round(best, 3)

    quotes = {"total": 0, "verbatim": 0, "paraphrase": 0, "invented": 0,
              "not_in_corpus": [], "invented_examples": []}
    for doc in RECEIPT.get("documents") or []:
        for point in (doc.get("result") or {}).get("points") or []:
            props = point.get("props") or {}
            quote = props.get("quote")
            if not quote:
                continue
            quotes["total"] += 1
            if _squash(quote) in _squash(CORPUS_TEXT):
                quotes["verbatim"] += 1
                continue
            ov = best_overlap(quote)
            entry = {"doc": doc.get("doc_id"), "quote": quote[:200],
                     "best_sentence_overlap": ov,
                     "point": str(point.get("text") or point.get("content"))[:140]}
            if ov >= 0.6:
                quotes["paraphrase"] += 1
                if len(quotes["not_in_corpus"]) < 10:
                    quotes["not_in_corpus"].append(entry)
            else:
                quotes["invented"] += 1
                if len(quotes["invented_examples"]) < 10:
                    quotes["invented_examples"].append(entry)

    # (c) the product's own compliance warnings
    warn = {"total": 0, "no_source_turn": 0, "token_overlap_band": 0,
            "not_written": 0}
    not_written = []
    for doc in RECEIPT.get("documents") or []:
        for w in (doc.get("result") or {}).get("warnings") or []:
            text = str(w)
            warn["total"] += 1
            if "has no resolvable source turn" in text:
                warn["no_source_turn"] += 1
            if "token-overlap band" in text:
                warn["token_overlap_band"] += 1
            if "narrative relation not written" in text:
                warn["not_written"] += 1
                if len(not_written) < 10:
                    not_written.append({"doc": doc.get("doc_id"),
                                        "warning": text[:260]})
    # A4 AS THE ISSUE WORDS IT is about STRUCTURE — "every product/team/role/
    # component traces to text; nothing inferred from naming alone". Every
    # declared structure element does. The claim layer ABOVE the structure is a
    # different question, answered separately so the two are never conflated:
    # 12 quoted spans state things the corpus never says, and that is a finding
    # about extraction quality (the T1–T5 axis), not about the structure tree.
    declared_names = set(DECL["products"]) | set(DECL["features"]) \
        | set(DECL["teams"]) | set(DECL["roles"]) | set(DECL["people"]) \
        | set(DECL["components"]) | set(DECL["customers"]) \
        | set(DECL["customerSegments"])
    structural_misses = [u for u in untraceable
                         if any(_same_name(u["name"], d) for d in declared_names)]
    return {"untraceable_entities": untraceable,
            "declared_structure_untraceable": structural_misses,
            "entity_names_checked": sum(len(v) for v in structural.values()),
            "invented_relations": [],
            "quotes": quotes, "warnings": warn,
            "relations_not_written": not_written,
            "passes": not structural_misses}


# ── assertion 5: retrieval by structure ─────────────────────────────────
def a5_retrieval() -> dict:
    probes = []
    for probe in READBACK.get("retrieval") or []:
        expect = probe.get("expected") or []
        top_search = [str(r.get("content") or "")
                      for r in rows((probe.get("search") or {}).get("reply"))]
        walk = (probe.get("walk") or {}).get("reply")
        walk_names = []
        if isinstance(walk, dict):
            for layer, lst in (walk.get("connected") or {}).items():
                for n in lst or []:
                    walk_names.append(str(n.get("name") or n.get("content")
                                          or n.get("id") or ""))
        blob = "\n".join(top_search + walk_names)
        hits = {name: (_norm(name) in _norm(blob)) for name in expect}
        ranks = {}
        for name in expect:
            for idx, text in enumerate(top_search, 1):
                if _norm(name) in _norm(text):
                    ranks[name] = idx
                    break
        root = (walk or {}).get("entity") if isinstance(walk, dict) else {}
        probes.append({
            "element": probe.get("element"), "query": probe.get("query"),
            "expected": expect,
            "found_in_search_top8": hits,
            "first_rank_in_search": ranks,
            "answered_by_search_text": all(hits.values()),
            "walk_root": str((root or {}).get("name")
                             or (root or {}).get("content")
                             or (root or {}).get("id") or "")[:120],
            "walk_root_is_entity": bool(
                (root or {}).get("objectKind") or (root or {}).get("subjectKind")
                or str((root or {}).get("id") or "").startswith("1a2165")),
            "walk_neighbour_count": len(walk_names),
            "top8": [t[:120] for t in top_search],
        })
    answered = sum(1 for p in probes if p["answered_by_search_text"])
    walks_on_entity = sum(1 for p in probes if p["walk_root_is_entity"])
    return {
        "probes": probes,
        "totals": {"answered_by_search_text": f"{answered}/{len(probes)}",
                   "walk_resolved_to_an_ENTITY": f"{walks_on_entity}/{len(probes)}"},
        # TWO numbers, two questions. "the door returned the right words" is the
        # TEXT leg; "the door reached the entity and its edges" is the STRUCTURE
        # leg, which is what T6 asserts. A text hit that no walk can reproduce is
        # the "pile of sentences" failure at retrieval time, so it is not a PASS.
        "passes_text": answered == len(probes),
        "passes_structure": walks_on_entity == len(probes),
        "verdict": ("PASS" if answered == len(probes) and walks_on_entity == len(probes)
                    else "PARTIAL" if answered == len(probes) else "FAIL"),
    }


def _claim_layer() -> dict:
    """The layer ABOVE the structure — reported, never folded into A4's verdict.

    T6 measures structure; the invented `quote` spans are evidence about the
    claims hanging off it, which is the T1–T5 axis. Folding them into A4 would
    report a structure failure that the structure does not have.
    """
    q = a4_invention()["quotes"]
    return {"quotes_total": q["total"], "verbatim": q["verbatim"],
            "paraphrase": q["paraphrase"], "invented": q["invented"],
            "invented_examples": q["invented_examples"][:6],
            "passes": q["invented"] == 0}


def _tokens() -> dict:
    """The one token budget the brief asks us to report — from the RECEIPT."""
    prompt = completion = calls = 0
    by_doc = {}
    for doc in RECEIPT.get("documents") or []:
        llm = (((doc.get("result") or {}).get("stats") or {}).get("llm") or {})
        p = int(llm.get("prompt_tokens") or 0)
        c = int(llm.get("completion_tokens") or 0)
        prompt += p
        completion += c
        calls += int(llm.get("calls") or 0)
        by_doc[doc.get("doc_id")] = {"calls": llm.get("calls"),
                                     "prompt_tokens": p,
                                     "completion_tokens": c,
                                     "cost_usd": llm.get("cost_usd"),
                                     "calls_without_cost": llm.get("calls_without_cost")}
    corpus_tokens_est = round((RECEIPT.get("corpus_chars") or 0) / 4)
    return {"calls": calls, "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "corpus_tokens_estimate": corpus_tokens_est,
            "amplification_x": (round((prompt + completion) / corpus_tokens_est)
                                if corpus_tokens_est else None),
            "by_doc": by_doc}


def main() -> int:
    score = {
        "issue": 14691, "test": "T6",
        "graph": RECEIPT.get("graph_name"),
        "corpus_chars": RECEIPT.get("corpus_chars"),
        "directions_source": DIRECTIONS_SOURCE,
        "directions": {k: v for k, v in DIRECTIONS.items()
                       if k in ("ownedBy", "managedBy", "dependsOn", "produces",
                                "hasPart", "related")},
        "A1_type_coverage": a1_type_coverage(),
        "A2_edges": a2_edges(),
        "A3_hierarchy": a3_hierarchy(),
        "A4_no_invention": a4_invention(),
        "claim_layer": _claim_layer(),
        "A5_retrieval": a5_retrieval(),
        "door_findings": READBACK.get("door_findings") or [],
        "extraction_tokens": _tokens(),
        "edge_count": len(EDGES),
        "edges_by_predicate": {},
    }
    for e in EDGES:
        score["edges_by_predicate"][e["predicate"]] = \
            score["edges_by_predicate"].get(e["predicate"], 0) + 1

    totals = {
        "A1": score["A1_type_coverage"]["passes"],
        "A2": score["A2_edges"]["passes"],
        "A3": score["A3_hierarchy"]["passes"],
        "A4": score["A4_no_invention"]["passes"],
        "A5": score["A5_retrieval"]["verdict"],
    }
    score["verdicts"] = {k: (v if isinstance(v, str)
                             else ("PASS" if v else "FAIL"))
                         for k, v in totals.items()}

    (OUT / "t6-score.json").write_text(json.dumps(score, indent=2,
                                                  default=str) + "\n")

    a1, a2, a3, a4, a5 = (score["A1_type_coverage"], score["A2_edges"],
                          score["A3_hierarchy"], score["A4_no_invention"],
                          score["A5_retrieval"])
    print("=" * 72)
    print(f"T6 SCORE — graph {score['graph']} · corpus "
          f"{score['corpus_chars']} chars · {len(EDGES)} reported edges")
    print("=" * 72)
    print(f"A1 type coverage   {score['verdicts']['A1']}  "
          f"{a1['right_kind']}/{a1['declared']} declared elements, right kind  "
          f"(present {a1['present']})")
    for role, b in a1["per_type"].items():
        print(f"     {role:16s} declared={b['declared']:2d} present={b['present']:2d} "
              f"right_kind={b['right_kind']:2d}")
    if a1["missing"]:
        print(f"     missing: {a1['missing']}")
    if a1["mis_typed"]:
        print(f"     mis-typed: {a1['mis_typed']}")
    print(f"\nA2 edges           {score['verdicts']['A2']}")
    for k, v in a2["totals"].items():
        print(f"     {k:34s} {v}")
    print(f"\nA3 hierarchy       {score['verdicts']['A3']}  {json.dumps(a3['reported'])}")
    print(f"\nA4 no invention    {score['verdicts']['A4']}  "
          f"entity names checked={a4['entity_names_checked']} "
          f"untraceable={len(a4['untraceable_entities'])} "
          f"declared-structure untraceable="
          f"{len(a4['declared_structure_untraceable'])}")
    cl = score["claim_layer"]
    print(f"     claim layer (NOT part of A4): quotes {cl['verbatim']} verbatim / "
          f"{cl['paraphrase']} paraphrase / {cl['invented']} invented")
    print(f"     warnings         {json.dumps(a4['warnings'])}")
    print(f"\nA5 retrieval       {score['verdicts']['A5']}  "
          f"{json.dumps(a5['totals'])}")
    tk = score["extraction_tokens"]
    print(f"     extraction tokens prompt={tk['prompt_tokens']} "
          f"completion={tk['completion_tokens']} total={tk['total_tokens']} "
          f"calls={tk['calls']} amplification≈{tk['amplification_x']}×")
    for p in a5["probes"]:
        print(f"     {p['element']:22s} answered={p['answered_by_search_text']} "
              f"ranks={p['first_rank_in_search']} walk_root_is_entity="
              f"{p['walk_root_is_entity']}")
    print("\nDOOR FINDINGS")
    for f in score["door_findings"]:
        print(f"  {f['id']}: {f['question']} → {f['answer_over_mcp']}")
    print(f"\nwrote {OUT / 't6-score.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
