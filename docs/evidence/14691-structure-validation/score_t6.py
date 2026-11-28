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
# ⛔ THE IMPORT MUST NOT DEPEND ON THE CALLER'S `PYTHONPATH` (#14691 review, round
# 6, finding 4). `--check` is run from the bundle directory, where the repo root is
# two levels up but NOT importable by default — so `DIRECTIONS` silently became {}
# and the derivation dropped `produces`, making `--check` disagree with a committed
# artifact that was produced WITH the table. Measured: the same bundle, same files,
# exit 1 under `python score_t6.py --check` and exit 0 under
# `PYTHONPATH=../../.. python score_t6.py --check`. The repo root is put on
# `sys.path` HERE, and an unavailable table is now FATAL rather than silent.
_REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents
                   if (parent / "tortoise" / "pack_registry.py").is_file()), None)
if _REPO_ROOT is not None and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
try:
    from tortoise.pack_registry import SEMANTICS_DIRECTIONS as DIRECTIONS
    DIRECTIONS_SOURCE = "tortoise.pack_registry.SEMANTICS_DIRECTIONS"
except Exception as exc:
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


def is_error_reply(reply) -> bool:
    """True when a reply is an ERROR envelope rather than an ANSWER.

    ⛔ A door that FAILED and a door that answered EMPTY are two different
    measurements, and `rows()` maps both to `[]` (#14691 review, 5th cycle, N1).
    Left alone, that collapses a transport failure into "the door says there is
    nothing" — and for D4 that means an `{"error": …}` witness reply CREATES the
    finding "expand_relationships returns [] for a connected node", which is a
    claim about the product the measurement never made. Every reader that decides
    emptiness or ownership from a reply must ask this first.
    """
    # ⛔ A PLAIN-TEXT ERROR IS STILL AN ERROR (#14691 review, round 5, P1).
    # The producer's own failure text — `["Error: timeout while expanding
    # relationships"]` — is not JSON, so this returned False and the door read as
    # ANSWERED-EMPTY: a failed call published as the product defect D4, with the
    # artifact re-deriving byte-identically. Measured on `ae2bcfc864`: replacing all
    # seven witness replies with that shape left 73 passed and D4 standing.
    _ERROR_TEXT = re.compile(
        r"^\s*(?:error|exception|traceback|failed|failure|timeout|refused|"
        r"unavailable|unauthori[sz]ed)\b", re.IGNORECASE)
    while isinstance(reply, str):
        try:
            reply = json.loads(reply)
        except (ValueError, TypeError):
            return bool(_ERROR_TEXT.match(reply))
    # a row list of PLAIN STRINGS (never dicts) carrying failure text is an
    # error envelope, not rows
    if (isinstance(reply, list) and reply
            and all(isinstance(item, str) for item in reply)):
        return any(_ERROR_TEXT.match(item) for item in reply)
    if isinstance(reply, dict):
        # ⛔ THE MCP SPEC'S OWN ERROR SHAPE (#14691 review, 6th round, P2-1).
        # MCP 2025-06-18 reports a tool EXECUTION error inside the RESULT as
        # `{"content": [...], "isError": true}` — no `error` key, no `ok` key — so
        # this guard could not see it. Measured: replacing both bare-kind probes,
        # or all seven witness `expand_relationships` replies, with that envelope
        # left 39 passed and D3/D4 in place: the findings were sourced from calls
        # that never answered, which is the exact shape this guard exists for.
        if reply.get("isError") is True or reply.get("is_error") is True:
            return True
        content = reply.get("content")
        if isinstance(content, list) and any(
                isinstance(item, dict) and item.get("isError") is True
                for item in content):
            return True
        return bool(reply.get("error")) or reply.get("ok") is False
    return False


def call_failed(call) -> bool:
    """True when a witness CALL — or the `reply` it carries — is an error.

    ⛔ THE ENVELOPE IS THE CALL, NOT THE `reply` INSIDE IT (#14691 review, 5th
    cycle, N1, second pass). A failure spelled `{"ok": false, "error": …}` may
    carry NO `reply` key at all — and a failure may equally carry
    `"reply": []`, which satisfies both the "the door was asked" arm and
    `rows(...) == []`. Both shapes are errors.
    """
    if not isinstance(call, dict):
        return False
    return is_error_reply(call) or is_error_reply(call.get("reply"))


def measured_empty(call) -> bool:
    """True only when a door ANSWERED and the answer was EMPTY.

    The third state this bundle turns on, beside "content" and "not asked": a
    FAILED call is not a measured empty, and every finding here depends on the
    difference (#14691 review, 5th cycle, N1).
    """
    # ⛔ ABSENCE IS NOT EMPTINESS (#14691 review, 6th round, P3-7). `call.get(
    # "reply")` is None for a call that was never asked, and `rows(None) == []`,
    # so a reply-less call read as "the door answered nothing" — the third state
    # this function's own docstring distinguishes, inverted. `"reply" in call` is
    # the distinction: asked-and-empty versus not asked.
    return (isinstance(call, dict) and "reply" in call
            and not call_failed(call) and not rows(call.get("reply")))


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
    # ⛔ NO shipped pack declares a `component` kind. The set below is the
    # DECLARED architecture/source concepts a component can legitimately land in;
    # the catch-all `core:other` is deliberately NOT accepted, because accepting
    # it would let "everything else" count as the right type and hide the
    # absence this note records.
    "component": {"dev:code", "dev:software", "dev:infrastructure", "dev:api",
                  "dev:database", "product-strategy:architecture"},
}


def _same_name(got: str, want: str) -> str | None:
    """`exact` / `prefix` / None — how a reported endpoint names a declared one.

    The extractor welds the corpus's collective noun onto a name: the corpus says
    "Dana Whitlock owns the ingest-gateway component", and the node is stored as
    `ingest-gateway component`. A matcher that only accepts the exact string
    reports a MISSING edge where the edge exists (and that is a scoring bug, not
    a finding) — so the prefix arm exists, and the variance it tolerates is
    reported separately as the naming observation it is.

    ⛔ THE ARM IS ONE-DIRECTIONAL: the REPORTED name may be the LONGER one. The
    reverse arm (`want.startswith(got)`) was removed because it bought nothing
    the data needs and cost the only check that guards a COLLAPSE — measured, a
    graph holding the declared products as ONE node named `Lumen` matched both
    `Lumen Core` and `Lumen Field` under it, so A3 reported `passes: True` on the
    exact failure A3 exists to catch (`_all_match({"lumen"}, {"lumen core",
    "lumen field"})` -> True). A shorter reported name than the declared one is
    not a weld to tolerate; it is the collapse, and it must MISS.
    """
    g, w = _norm(got), _norm(want)
    if g == w:
        return "exact"
    if g.startswith(w):
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


def orientation_verdict(predicate: str, src: str, dst: str,
                        role_target: str | None) -> str:
    """Apply the predicate's DECLARED direction to one reported row.

    `role_target` is the party the declaration says the arrow points AT:
    the **owner** for `ownedBy`/`managedBy`, the **dependency** for `dependsOn`,
    the **producer** for `produces`. Both readings of the table converge on one
    test — `->` (§3.6: "`investment --ownedBy--> fund`" — the fund owns it) and
    `<-` (§8: "the TARGET brought the source into existence" — `A --produces-->
    B` says B produced A) both require the DST end to be that party. That is why
    this is one rule and not a per-predicate branch: two branches here is how the
    census and the writer came to disagree about the same row (#11548).

    Returns ``correct`` / ``INVERTED`` / ``symmetric`` (`hasPart` `<->`) /
    ``no-direction`` (`related` `-`) / ``unknown`` (the table did not load).
    """
    direction = DIRECTIONS.get(predicate)
    if direction in ("<->",):
        return "symmetric"
    if direction in ("-", None) and predicate not in _DST_ROLE:
        return "no-direction"
    if role_target is None:
        return "unknown"
    if _same_name(dst, role_target) and not _same_name(src, role_target):
        return "correct"
    if _same_name(src, role_target) and not _same_name(dst, role_target):
        return "INVERTED"
    return "unknown"


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
        "accepted_kinds": {role: sorted(kinds)
                           for role, kinds in _ROLE_KINDS.items()},
        "component_kind_declared": False,
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
        # `hasPart` is `<->` (composition, both traversals equivalent), so the
        # ASSERTION here is "the two ends are connected as a composition", not
        # which end the arrow token starts at. `parent_is_src` is recorded as an
        # OBSERVATION — it is what the declared pack pair spells — never as the
        # pass condition, because requiring a direction of a symmetric predicate
        # would fail a row the ontology considers correct.
        parent_is_src = [
            e for e in found
            if _same_name(e["src"], product) and _same_name(e["dst"], feature)]
        containment.append({
            "product": product, "feature": feature,
            "present": bool(found),
            "predicates": sorted({e["predicate"] for e in found}),
            "parent_is_src": bool(parent_is_src),
            "edges": [_compact(e) for e in found],
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
    for role, person in DECL.get("holdsRole") or []:
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
            verdicts.append({
                "edge": _compact(e),
                "direction": DIRECTIONS.get(e["predicate"], "?"),
                "src_is_owner": bool(_same_name(e["src"], owner)),
                "orientation": orientation_verdict(e["predicate"], e["src"],
                                                   e["dst"], owner),
            })
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
            verdicts.append({
                "edge": _compact(e),
                "orientation": orientation_verdict(e["predicate"], e["src"],
                                                   e["dst"], dependency_on),
            })
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
                # The declared shape puts the PRODUCED thing first and the
                # PRODUCER at the dst end (§8: `<-`, "the TARGET brought the
                # source into existence"). So a row whose SRC is an agent (a
                # team or a person) reads backwards, whichever component sits
                # at the other end. Checked on KIND, not on a name, because the
                # producer is what the kind identifies.
                agent_kinds = _ROLE_KINDS["team"] | _ROLE_KINDS["person"]
                src_kind = e.get("src_kind") or ""
                row["src_kind_is_an_agent"] = src_kind in agent_kinds
                # ⛔ A ROW THIS SCORER CANNOT CLASSIFY IS NOT A `correct` ROW
                # (#14691 review, 5th cycle, N6). An absent or unreadable
                # `src_kind` used to fall through the `else` and be graded
                # `correct`, so ONE unreadable row moved
                # `asymmetric_rows_correct` 7 -> 8 — the tally A2's verdict is
                # read from. It now gets its own bucket and counts in neither
                # arm; `asymmetric_rows_unclassifiable` states the size of it.
                row["orientation"] = ("INVERTED" if row["src_kind_is_an_agent"]
                                      else ("correct" if src_kind
                                            else "UNCLASSIFIABLE"))
            elif pred == "managedBy":
                # §3.6 types `managedBy` as `Entity → Subject`: the RESPONSIBLE
                # PARTY is the dst. Checked on KIND (a Subject end), because the
                # declaration constrains the kinds, not a particular name.
                # ⛔ THE MIRROR OF N6 (#14691 review, 6th cycle, finding 6). The
                # `produces` arm got its UNCLASSIFIABLE bucket; this one did not,
                # so an absent or unreadable `dst_kind` graded the row INVERTED —
                # asserting the row says the REVERSE of the corpus from a kind the
                # scorer never read — and moved the very tallies A2's paragraph is
                # pinned to (`asymmetric_rows_inverted` 15 -> 16, distinct pairs
                # 13 -> 14). Measured on the peer head: one unreadable
                # `managedBy` row did exactly that and nothing reddened.
                subject_kinds = (_ROLE_KINDS["person"] | _ROLE_KINDS["team"]
                                 | _ROLE_KINDS["role"] | {"core:organization"})
                dst_kind = e.get("dst_kind") or ""
                src_kind = e.get("src_kind") or ""
                row["dst_kind_is_a_subject"] = dst_kind in subject_kinds
                row["src_kind_is_a_subject"] = src_kind in subject_kinds
                if row["dst_kind_is_a_subject"]:
                    row["orientation"] = "correct"
                elif row["src_kind_is_a_subject"]:
                    row["orientation"] = "INVERTED"
                elif dst_kind or src_kind:
                    # one end was READ and is not a Subject end: the row is
                    # genuinely backwards (the committed rows land here — a
                    # person src, a customer dst — which is the finding)
                    row["orientation"] = "INVERTED"
                else:
                    row["orientation"] = "UNCLASSIFIABLE"
            other.append(row)

    # (e2) the OWNERSHIP DOOR's own answer — the one product read that can
    # witness the inversion from the graph side: `type="governance"` reads
    # `(s:Subject)<-[:ownedBy]-(e)`, the declared orientation.
    owners = {_norm(o) for _t, o in DECL["ownership"]}
    ownership_door = {}
    ownership_door_unasked = []
    for name, w in (READBACK.get("link_witness") or {}).items():
        if _norm(name) in owners:
            probe = (w.get("governance_owned_by") or {})
            if probe.get("reply") is None:
                # Same distinction as D6 above: an owner never asked is recorded
                # as NOT ASKED, never as a `0`-row answer.
                ownership_door_unasked.append(name)
                continue
            ownership_door[name] = rows(probe.get("reply"))

    contained = sum(1 for c in containment if c["present"])
    mem = sum(1 for m in membership if m["present"])
    role_links = sum(1 for r in roles if r["present"])
    own_ok = sum(1 for o in ownership
                 if o["orientation"] == ["correct"])
    own_inv = sum(1 for o in ownership if "INVERTED" in o["orientation"])
    dep_ok = sum(1 for d in dependency
                 if d["present"] and d["orientation"] == ["correct"])

    # One line a reader can quote: over every ASYMMETRIC structural row this run
    # wrote (hasPart is symmetric, `related` has no direction), how many assert
    # what the corpus says? Rows and distinct pairs are different units — 6
    # `ownedBy` rows are 4 distinct pairs — so both are stated.
    asymmetric_correct = asymmetric_inverted = asymmetric_unclassified = 0
    inverted_pairs: set[tuple[str, str, str]] = set()
    for group in (ownership, dependency):
        for item in group:
            for v in item["verdicts"]:
                if v["orientation"] == "correct":
                    asymmetric_correct += 1
                elif v["orientation"] == "INVERTED":
                    asymmetric_inverted += 1
                    inverted_pairs.add((v["edge"]["predicate"],
                                        _norm(v["edge"]["src"]),
                                        _norm(v["edge"]["dst"])))
    for row in other:
        if row.get("orientation") == "correct":
            asymmetric_correct += 1
        elif row.get("orientation") == "INVERTED":
            asymmetric_inverted += 1
            inverted_pairs.add((row["edge"]["predicate"],
                                _norm(row["edge"]["src"]),
                                _norm(row["edge"]["dst"])))
        elif row.get("orientation") == "UNCLASSIFIABLE":
            asymmetric_unclassified += 1
    asymmetric = (asymmetric_correct, asymmetric_inverted)
    return {
        "containment": containment, "membership": membership,
        "role_links": roles, "team_role_containment": team_role,
        "ownership": ownership, "dependency": dependency, "other": other,
        "ownership_door": {
            "door": "tortoise_get_entity(id, type='governance') -> "
                    "sdk._get_owned_entities -> MATCH (s)<-[:ownedBy]-(e)",
            "owners_asked": sorted(ownership_door),
            "rows_returned": {k: len(v) for k, v in ownership_door.items()},
            "total_rows": sum(len(v) for v in ownership_door.values()),
            # an owner the witness never asked is reported as such, never folded
            # into `total_rows: 0` (a `0` is a measurement; absence is not)
            "owners_not_asked": sorted(ownership_door_unasked),
        },
        "totals": {
            "containment_present": f"{contained}/{len(containment)}",
            "containment_parent_spelled_product_first (observation)":
                f"{sum(1 for c in containment if c['parent_is_src'])}/{len(containment)}",
            "membership_present": f"{mem}/{len(membership)}",
            "role_links_present": f"{role_links}/{len(roles)}",
            "team_to_role_edges": len(team_role),
            "ownership_present": f"{sum(1 for o in ownership if o['present'])}"
                                 f"/{len(ownership)}",
            "ownership_oriented_correct": own_ok,
            "ownership_inverted": own_inv,
            "dependency_present_and_correct": f"{dep_ok}/{len(dependency)}",
            "asymmetric_rows_correct": asymmetric[0],
            "asymmetric_rows_inverted": asymmetric[1],
            "asymmetric_rows_unclassifiable": asymmetric_unclassified,
            "asymmetric_pairs_inverted": len(inverted_pairs),
        },
        # PRESENCE is part of the assertion, not just the orientation: a graph
        # with no ownership edges has no inverted ones either, and "0 inverted"
        # must never be reached by that route.
        "passes": (contained == len(containment)
                   and mem == len(membership)
                   and role_links == len(roles)
                   and sum(1 for o in ownership if o["present"]) == len(ownership)
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

    def _all_match(got: set[str], want: set[str]) -> bool:
        """Every declared name is matched by some reported name and vice versa.

        Prefix-tolerant in ONE direction — the REPORTED name may be the longer
        one, because the extractor welds a collective noun onto a name
        (`ingest-gateway component`). The reverse direction is deliberately NOT
        tolerated (`_same_name` is forward-only): a reported name SHORTER than a
        declared one is a collapsed node, not a naming variance, and this is the
        only term that can red for it.
        """
        return (all(any(_same_name(g, w) for g in got) for w in want)
                and all(any(_same_name(g, w) for w in want) for g in got))
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
                   and _all_match(parents, products)
                   and _all_match(children, features)),
        "edges": [_compact(e) for e in has_part],
    }


# ── assertion 4: nothing invented ────────────────────────────────────────
_QUOTE_KEY = re.compile(r"[^a-z0-9 ]+")


def _squash(text: str) -> str:
    return _QUOTE_KEY.sub("", _norm(text))


#: The KIND nouns the corpus's own declared GROUPS are named after. These are the
#: words a writer can WELD onto an entity name (`ingest-gateway component`), so
#: they are the only words the name-variance retry may drop.
#: ⛔ DERIVED FROM THE DECLARED GROUPS, THROUGH AN EXPLICIT MORPHOLOGY TABLE
#: (#14723 review, round 10, P3). The previous revision CALLED itself derived
#: ("derived from `_declared_structure`, never a hand-typed list") while being a
#: hand-typed tuple, and a naive derivation — singularise each key — does not even
#: reproduce it: `people` → `person` and `customerSegments` → `customer segment`
#: are not reachable from the key. The table is explicit, and it is CHECKED
#: against the declared structure at import, so a declared group with no kind
#: word raises here instead of leaving a silent gap in the retry.
_KIND_OF_GROUP = {
    "products": "product", "features": "feature", "teams": "team",
    "roles": "role", "people": "person", "components": "component",
    "customers": "customer", "customerSegments": "customer segment",
}

#: The groups in `_declared_structure` whose values are NAMES. The edge tables
#: (`containment`, `membership`, `ownership`, `dependencies`, …) hold pairs and
#: are deliberately not part of the kind-word population.
_DECLARED_GROUPS = {key for key, value in DECL.items()
                    if isinstance(value, list) and value
                    and all(isinstance(item, str) for item in value)}


def _kind_words(decl: dict) -> set[str]:
    """The trailing KIND words a declared structure allows, FAIL-CLOSED.

    Raises when a declared name group has no kind word, so a corpus that adds a
    group cannot leave a silent gap in `_unweld` (and the raise is reachable — it
    is exercised by the suite with a group that is not in the table).
    """
    groups = {key for key, value in decl.items()
              if isinstance(value, list) and value
              and all(isinstance(item, str) for item in value)}
    missing = groups - set(_KIND_OF_GROUP)
    if missing:
        raise ValueError(
            f"no kind word for the declared name group(s) {sorted(missing)} — "
            "add them to _KIND_OF_GROUP beside the corpus group they name")
    stale = set(_KIND_OF_GROUP) - groups
    if stale:
        raise ValueError(
            f"_KIND_OF_GROUP names group(s) the corpus does not declare: "
            f"{sorted(stale)}")
    return {_squash(kind) for kind in _KIND_OF_GROUP.values()}


_KIND_WORDS = _kind_words(DECL)

#: Every name the corpus DECLARES, squashed. A retry may only un-weld a word when
#: what is left is one of these — see `_unweld`.
_DECLARED = {_squash(name) for value in DECL.values() if isinstance(value, list)
             for name in value if isinstance(name, str)}


def _unweld(name) -> str:
    """Drop ONE trailing kind word welded to a name (`ingest-gateway component`).

    ⛔ AND ONLY WHEN THE REMAINDER IS A DECLARED NAME (#14723 review, round 10,
    P1). Every declared team ENDS with its kind word (`Platform team`,
    `Product team`, `Customer team`), so dropping the word TRUNCATES a legitimate
    name to a bare common noun — `Customer team` → `Customer`, which the corpus
    also says on its own — and two edges the corpus never states
    (`Grid Map --managedBy--> Customer team`, `Asset Register --managedBy-->
    Customer team`) then cleared A4 on the retry alone (`passes: True`,
    `relations_not_co_stated: []`). That is the truncation the round-9 fix was
    meant to remove, one word narrower. The un-welded form must BE a declared
    name and the welded form must not be, so
    `ingest-gateway component → ingest-gateway` still passes and
    `Customer team → Customer` does not. Names without a kind suffix are returned
    unchanged, so this is a name-variance allowance, not a substring search.
    """
    parts = str(name or "").split()
    if len(parts) > 1 and _squash(parts[-1]) in _KIND_WORDS:
        welded, unwelded = _squash(name), _squash(" ".join(parts[:-1]))
        if unwelded in _DECLARED and welded not in _DECLARED:
            return " ".join(parts[:-1])
    return str(name or "")


#: Example lists are capped for artifact size. A cap is a DISPLAY concern and
#: never a count, so each list records when its cap bit. Module-level so the
#: falsifier suite can FORCE the cap and observe that the flag it declares is the
#: flag that fires (#14723 review, round 10, P2) — at the default cap this corpus
#: never truncates a summary sample, so the setter's name was unobservable.
CAP = 10


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
    #: The corpus's OWN paragraph unit. A turn is the smallest span the corpus
    #: states one coherent thing in, so it is the widest unit a quote may borrow
    #: support from.
    corpus_turns = [_norm(t["content"])
                    for d in CORPUS["documents"] for t in d["turns"]]
    #: The SAME units, per DOCUMENT. A4 asks whether the BUSINESS states a claim,
    #: not whether the document the extractor attributed it to does — so the
    #: decider stays corpus-wide. What must not stay unstated is WHERE the support
    #: came from: `best_own_doc_turn_overlap` records the doc-scoped figure beside
    #: the corpus-wide one, and `supported_outside_own_doc` counts the rows that
    #: clear the band only through another document (#14723 review, round 10, P2).
    own_turns_by_doc = {d["doc_id"]: [_norm(t["content"]) for t in d["turns"]]
                        for d in CORPUS["documents"]}

    def _best_overlap(quote: str, units: list[str]) -> float:
        """Share of the quote's content words present in its best-matching UNIT."""
        words = set(re.findall(r"[a-z]{3,}", _norm(quote)))
        if not words:
            return 0.0
        best = 0.0
        for unit in units:
            hit = sum(1 for w in words if w in unit)
            best = max(best, hit / len(words))
        return round(best, 3)

    def sentence_overlap(quote: str) -> float:
        """Share of the quote's words in its best-matching corpus SENTENCE."""
        return _best_overlap(quote, corpus_sents)

    def turn_overlap(quote: str) -> float:
        """Share of the quote's words in its best-matching corpus TURN.

        ⛔ THE DECIDER IS TURN-SCOPED, NOT SENTENCE-SCOPED (#14723 review, round
        10, P2). A quote is a span the extractor attributed to ONE turn, and a
        faithful MULTI-FACT summary can never put ≥60% of its words into a single
        sentence: `Three teams exist: Platform (engineering), Product (product
        management), and Customer (commercial).` scores 0.30 sentence-scoped and
        0.80 against its own turn. Scoring the SENTENCE therefore graded a
        correctly reported fact as `invented`, and the artifact's gloss — "the
        corpus never says it" — asserted a property the measure could not
        establish. MEASURED on the committed receipt: 12 rows fall below the
        sentence band but only 9 are unsupported anywhere in the corpus; the
        other 3 are multi-fact summaries the corpus does state (doc-02's three
        teams, doc-03's seven-component list, doc-04's enterprise-renewals row).
        The turn unit is BOUNDED on purpose: support cannot be assembled across
        unrelated paragraphs, so a recombined claim still scores low (`invented`
        stays 9, and the claim-layer FAIL direction is unchanged).
        ⛔ AND THE SEARCH SPAN IS CORPUS-WIDE, WHICH THE ROWS NOW STATE (#14723
        review, round 10, P2). The three summaries are the corpus stating them,
        but one of them — doc-04's enterprise-renewals row — clears the band only
        on a doc-02 turn (0.714 corpus-wide, 0.571 inside its own doc), so a
        reader told "supported by its own turn" of every row would be told
        something the measure never established. Doc-scoping the decider instead
        would re-grade that row `invented`, which is a different (and wrong)
        claim: the business DOES state it. Both figures are therefore recorded per
        row, and the rows that depend on another document are counted.
        """
        return _best_overlap(quote, corpus_turns)

    #: The paraphrase band: above it the content words survive and only the
    #: wording changed; below it the corpus does not state the thing at all.
    BAND = 0.6
    quotes = {"total": 0, "verbatim": 0, "paraphrase": 0, "invented": 0,
              # ⛔ A THIRD BUCKET, because two could not describe the population
              # (#14723 review, round 10, P2): a quote whose support exists only
              # across a turn is neither a sentence paraphrase nor an invention.
              "summary_in_one_turn": 0,
              # ⛔ THE COUNT IS `below_sentence_band`, AND IT IS NOT `invented`
              # (#14723 review, round 10, P2). It used to be called
              # `not_in_corpus` — which asserted exactly the thing the measure
              # cannot establish: 3 of the 12 rows it counted ARE stated by the
              # corpus, just across a turn rather than inside one sentence. The
              # name now states the scope, and `invented` carries the finding.
              # The round-5 fix this REPLACES is kept: it is a COUNT, never the
              # length of a capped sample list (that was the original defect).
              # It is also the figure `out/t6-quote-audit.json` counts as its own
              # `not_in_corpus`, because that probe made the same sentence-scoped
              # measurement.
              "below_sentence_band": 0,
              "paraphrase_examples": [], "invented_examples": [],
              "summary_examples": [],
              # ⛔ THE SCOPE OF THE SUPPORT SEARCH, AS A NUMBER (#14723 review,
              # round 10, P2): rows that clear the turn band only through a
              # document OTHER than the one they are attributed to. A count and a
              # capped sample, like every other population here — the count is the
              # claim, the sample is what a reader can check.
              "supported_outside_own_doc": 0,
              "supported_outside_own_doc_examples": [],
              "supported_outside_own_doc_examples_truncated": False,
              # ONE FLAG PER LIST (#14691 review, 5th cycle, NEW-4): a single
              # shared boolean cannot say WHICH list's cap bit, so a consumer
              # still cannot tell which of the two samples is complete.
              # ⛔ A PERSISTED HISTOGRAM, because prose quoted a threshold the
              # artifact did not carry (#14691, 10th review, finding 2). REPORT
              # said "(11 below 0.5)" while the only band the scorer defined was
              # 0.6 — so the figure was unbacked AND ambiguous, colliding with
              # `claim_layer.verbatim` (a disjoint bucket that also happens to be
              # 11). Any prose figure about the band now reads this.
              "bands": {"below_0_5": 0, "from_0_5_to_0_6": 0, "at_or_above_0_6": 0},
              #: The same histogram over the DECIDER (turn scope), so the two
              #: scopes are both readable and neither is inferred from the other.
              "bands_turn_scope": {"below_0_5": 0, "from_0_5_to_0_6": 0,
                                   "at_or_above_0_6": 0},
              "paraphrase_examples_truncated": False,
              "invented_examples_truncated": False,
              "summary_examples_truncated": False}
    #: bucket -> its own example list (a shared name would make a cap ambiguous)
    quotes["_EXAMPLES"] = {"paraphrase": quotes["paraphrase_examples"],
                           "summary_in_one_turn": quotes["summary_examples"]}
    #: bucket -> the truncation FLAG its list is declared under.
    #: ⛔ THE FLAG'S NAME IS NOT THE BUCKET'S (#14723 review, round 10, P2). The
    #: setter derived the flag from the bucket (`f"{bucket}_examples_truncated"`)
    #: while the initialiser — and the arm that reads it — spell
    #: `summary_examples_truncated`, so a capped summary sample set a key nobody
    #: reads and the consumer was told the sample was COMPLETE. Masked only because
    #: this corpus holds 3 summaries: measured with the cap forced to 0,
    #: `summary_examples_truncated` stayed `False`.
    quotes["_EXAMPLES_TRUNCATED"] = {
        "paraphrase": "paraphrase_examples_truncated",
        "summary_in_one_turn": "summary_examples_truncated"}
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
            ov = sentence_overlap(quote)
            t_ov = turn_overlap(quote)
            own_ov = _best_overlap(quote, own_turns_by_doc.get(doc.get("doc_id"), []))
            entry = {"doc": doc.get("doc_id"), "quote": quote[:200],
                     "best_sentence_overlap": ov,
                     "best_turn_overlap": t_ov,
                     "best_own_doc_turn_overlap": own_ov,
                     "point": str(point.get("text") or point.get("content"))[:140]}
            if ov < 0.5:
                quotes["bands"]["below_0_5"] += 1
            elif ov < BAND:
                quotes["bands"]["from_0_5_to_0_6"] += 1
            else:
                quotes["bands"]["at_or_above_0_6"] += 1
            if t_ov < 0.5:
                quotes["bands_turn_scope"]["below_0_5"] += 1
            elif t_ov < BAND:
                quotes["bands_turn_scope"]["from_0_5_to_0_6"] += 1
            else:
                quotes["bands_turn_scope"]["at_or_above_0_6"] += 1
            if ov < BAND:
                quotes["below_sentence_band"] += 1
            if t_ov >= BAND:
                # supported by the corpus — the only question is WHERE
                if own_ov < BAND:
                    quotes["supported_outside_own_doc"] += 1
                    if (len(quotes["supported_outside_own_doc_examples"])
                            < CAP):
                        quotes["supported_outside_own_doc_examples"].append(entry)
                    else:
                        quotes["supported_outside_own_doc_examples_truncated"] = True
                bucket = "paraphrase" if ov >= BAND else "summary_in_one_turn"
                quotes[bucket] += 1
                examples = quotes["_EXAMPLES"][bucket]
                if len(examples) < CAP:
                    examples.append(entry)
                else:
                    quotes[quotes["_EXAMPLES_TRUNCATED"][bucket]] = True
            else:
                quotes["invented"] += 1
                if len(quotes["invented_examples"]) < CAP:
                    quotes["invented_examples"].append(entry)
                else:
                    quotes["invented_examples_truncated"] = True

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
    # quoted spans the corpus does not state are a finding about extraction
    # quality (the T1–T5 axis), not about the structure tree — and the number is
    # `a4_invention()["quotes"]["invented"]`, never the sentence-scope figure.
    # A relation is "stated by the text" when both endpoints are named in the
    # SAME DOCUMENT. Document scope, not sentence scope, is the right unit here:
    # a corpus legitimately connects two things across two adjacent sentences
    # ("Both customers are in the mid-market utilities segment, and Jonas Bauer
    # owns enterprise renewals for both"), and a sentence-scoped test reports
    # those as inventions — which is a scoring artifact, not a finding. `strict`
    # (sentence-scoped) is recorded alongside so the difference is visible.
    doc_texts = [("\n".join(t["content"] for t in doc["turns"]))
                 for doc in CORPUS["documents"]]
    sents = [s for s in re.split(r"(?<=[.!?])\s+|\n", CORPUS_TEXT) if s.strip()]
    invented_relations = []
    strict_relations = []
    name_variance = []
    for e in EDGES:
        a, b = _squash(e["src"]), _squash(e["dst"])
        stated = any(a in _squash(d) and b in _squash(d) for d in doc_texts)
        if not any(a in _squash(s) and b in _squash(s) for s in sents):
            strict_relations.append(_compact(e))
        # A welded name (`ingest-gateway component`) is a NAME variance, not a
        # missing fact: retry with the KIND WORD dropped, and nothing else.
        # ⛔ NOT THE FIRST WORD (#14723 review, round 10, P2). The retry used to
        # be `str(name).split(" ")[0]` — it truncated EVERY name to its first
        # word, so `Northwind Logistics --dependsOn--> Lumen Field` was graded
        # STATED because `northwind` and `lumen` both occur in doc-01, and A4
        # still reported `passes: True` with `relations_not_co_stated: []`
        # (MEASURED on the pre-fix code). Truncating a name is not the same
        # operation as un-welding a kind word: the first word of `Northwind
        # Logistics` is a different entity's name, while `ingest-gateway` IS the
        # entity the corpus names. The corpus's own declared groups supply the
        # kind words, so this list is derived, not typed.
        if not stated:
            short_a = _squash(_unweld(e["src"]))
            short_b = _squash(_unweld(e["dst"]))
            stated = any(short_a and short_b and short_a in _squash(d)
                         and short_b in _squash(d) for d in doc_texts)
            if stated:
                name_variance.append(_compact(e))
        if not stated:
            invented_relations.append(_compact(e))

    declared_names = set(DECL["products"]) | set(DECL["features"]) \
        | set(DECL["teams"]) | set(DECL["roles"]) | set(DECL["people"]) \
        | set(DECL["components"]) | set(DECL["customers"]) \
        | set(DECL["customerSegments"])
    # ⛔ THE VERDICT IS SCOPED BY KIND, NOT BY THE DECLARED NAME LIST (#14691 F5).
    # Scoring `passes` over `declared_names` alone made the "nothing invented"
    # verdict STRUCTURALLY UNABLE to fail on an invention: an invented node is by
    # definition absent from the declared list, so `_same_name(u, d)` is false for
    # every `d`, `structural_misses` stays `[]`, and A4 reported PASS on the exact
    # population the assertion names. MEASURED on the pre-fix code: adding a
    # fabricated `Lumen Cloud` (kind `product-strategy:product`) to the readback
    # left `passes: True`.
    # The population is therefore every untraceable entity whose KIND is one of
    # the structure kinds — the mechanical definition of "structure" this file
    # already scores A1 against — with the declared-name arm kept as a second
    # net for a correctly-named row that arrived under an unexpected kind.
    # ⛔ The narrowing this REPLACES is not widened away: a knowledge-layer object
    # (`dev:architectureDoc`, `agent-ops:rule`) is not a structure element, and
    # the claim layer above the structure is scored separately (see `quotes`), so
    # this verdict still does not red for one. Both scopes are RECORDED below, so
    # the number a reader sees is the one each scope covers.
    structural_kinds = {k for kinds in _ROLE_KINDS.values() for k in kinds}
    structural_kind_misses = [u for u in untraceable
                              if u["kind"] in structural_kinds]
    structural_misses = [u for u in untraceable
                         if u["kind"] in structural_kinds
                         or any(_same_name(u["name"], d) for d in declared_names)]
    return {"untraceable_entities": untraceable,
            "declared_structure_untraceable": [
                u for u in untraceable
                if any(_same_name(u["name"], d) for d in declared_names)],
            "structural_kind_untraceable": structural_kind_misses,
            #: rows that only clear the check by DROPPING A WELDED KIND WORD —
            #: recorded so the load-bearing fallback is visible instead of implied
            "relation_name_variance": name_variance,
            "untraceable_counts": {
                "all_entity_names": len(untraceable),
                "structural_kinds": len(structural_kind_misses),
                "declared_structure": len([
                    u for u in untraceable
                    if any(_same_name(u["name"], d) for d in declared_names)]),
            },
            "entity_names_checked": sum(len(v) for v in structural.values()),
            "relations_not_co_stated": invented_relations,
            "relations_not_co_stated_at_SENTENCE_scope": len(strict_relations),
            # the bucket -> example-list map is an INTERNAL handle, never artifact
            "quotes": {k: v for k, v in quotes.items() if not k.startswith("_")},
            "warnings": warn,
            "relations_not_written": not_written,
            # ⛔ BOTH AXES, because the assertion names both (#14691 review, 2nd cycle).
            # The issue's target is "nothing inferred from naming alone", and the
            # report states "0 of 46 relations are unstated" under this same PASS
            # heading — so a verdict computed from ENTITIES only left the relation
            # axis unreddable: measured, adding `Asset Register --dependsOn-->
            # Jonas Bauer` (no document co-states the pair) left `passes: True`
            # while `relations_not_co_stated` carried the row. An invented EDGE is
            # the worse failure of the two, so it must be able to red the verdict
            # that claims it did not happen.
            "passes_entities": not structural_misses,
            "passes_relations": not invented_relations,
            "passes": not structural_misses and not invented_relations}


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
            for _layer, lst in (walk.get("connected") or {}).items():
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
            # A root counts as an ENTITY only when a KIND says so. Nothing here
            # may infer it from an id shape: a prefix literal is not a receipt
            # value, and on a graph whose ids differ it would silently answer.
            "walk_root_kind": ((root or {}).get("objectKind")
                               or (root or {}).get("subjectKind")
                               or (root or {}).get("kind") or ""),
            "walk_root_is_entity": bool((root or {}).get("objectKind")
                                        or (root or {}).get("subjectKind")
                                        or (root or {}).get("kind")),
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


def _ownership_empty_answers() -> dict:
    """The witnesses whose OWNERSHIP probe answered an empty list — not an error.

    ONE reading of the door, used by both the finding (`_door_findings`) and the
    withheld record (`_withheld_findings`), so the two can never disagree about
    which witnesses answered nothing (#14723 review, round 10, P3).
    """
    owned = {}
    for name, w in (READBACK.get("link_witness") or {}).items():
        probe = w.get("governance_owned_by")
        if not isinstance(probe, dict) or "reply" not in probe:
            continue
        if call_failed(probe):
            # same distinction as D4: a failed call is NOT a measured empty, so
            # it can neither witness the finding nor be counted as an answer.
            continue
        if rows(probe["reply"]) == []:
            owned[name] = w
    return owned


def _door_findings() -> list[dict]:
    """The door findings, from the readback's own witness rows.

    D4/D5 are derived HERE from `link_witness` rather than asserted: the witness
    block carries, per node, what each door answered, so a reader can re-derive
    the verdict without trusting this function's prose.
    """
    found = list(READBACK.get("door_findings") or [])
    witness = READBACK.get("link_witness") or {}

    # ⛔ A PROSE FINDING MUST AGREE WITH ITSELF (#14691 review, round 6, P1).
    # D1 and D5 arrive from `readback.json` entirely as prose — their
    # `answer_over_mcp` was copied through unread. Measured on `ecf4fd5d92`:
    # reversing D1's answer to "YES — … its direction is visible too" left 79
    # passed, SELF-CONTRADICTING the `the_read_that_can` field beside it in the same
    # object (that key exists *because* no MCP door reports direction). A prose
    # finding therefore keeps its place only while its answer's polarity matches
    # what its own evidence says, and D4 keeps its place only while a NON-ERRORED
    # witness is still silent — the append path below only ever ADDS, so a stale
    # prose D4 outlived the measurement that created it.
    _EXPECTED_POLARITY = {
        "D1": "NO",   # evidence names a read outside MCP as the only one that can
        "D4": "NO",   # evidence lists witnesses that answered `[]`
        "D5": "NO",   # evidence records "Entity … not found" from every probe
    }

    def _polarity(text) -> str:
        head = str(text or "").lstrip("*` ").upper()
        for word in ("NO", "YES", "PARTIAL"):
            if head.startswith(word):
                return word
        return "UNKNOWN"

    def _silent_witnesses() -> dict:
        return {name: {"expand_rows": 0,
                       "explore_neighbours": w.get("explore_neighbour_count")}
                for name, w in witness.items()
                if "reply" in (w.get("expand_relationships") or {})
                and not rows((w.get("expand_relationships") or {}).get("reply"))
                and not call_failed(w.get("expand_relationships"))
                and (w.get("explore_neighbour_count") or 0) > 0}

    kept = []
    for finding in found:
        fid = finding.get("id")
        want = _EXPECTED_POLARITY.get(fid)
        if want is None:
            kept.append(finding)
            continue
        if _polarity(finding.get("answer_over_mcp")) != want:
            continue
        if not (finding.get("evidence") or {}):
            continue
        if fid == "D4" and not _silent_witnesses():
            continue
        kept.append(finding)
    found = kept

    # ⛔ D3 RESTS ON `bare_kind_probes`, SO IT MAY NOT OUTLIVE THEM (#14691
    # review, 5th cycle, N1 — the D3 leg). D1/D2/D3/D5 arrive from
    # `readback.json` as prose, and until now nothing re-read the probes they cite:
    # with BOTH probes rewritten to `{"ok": false, "error": …}` the suite stayed
    # green and D3 — "a bare kind returns an EMPTY list" — was still reported, a
    # claim about the product made from two calls that never answered. A finding
    # needs at least one MEASURED empty behind it; a failed call is not one.
    probes = READBACK.get("bare_kind_probes") or {}
    measured_empties = [name for name, call in probes.items()
                        if measured_empty(call)]
    if probes and len(measured_empties) < len(probes):
        found = [f for f in found if f.get("id") != "D3"]
        if measured_empties:
            found.append({
                "id": "D3",
                "question": "does the full-scan door accept the **bare** kind the "
                            "packs declare?",
                "answer_over_mcp": "NO for the probes that ANSWERED an empty list; "
                                   "the rest never answered, so they are not counted",
                "evidence": {"measured_empty": measured_empties,
                             "not_measured": sorted(set(probes) - set(measured_empties))},
            })

    # ⛔ AND D2 OWNS ITS EVIDENCE TOO (3rd fresh-context review, 2nd cycle, P2).
    # D1/D2 were the last two findings still copied from `readback.json` prose with
    # nothing re-reading the rows they cite. Measured: setting
    # `readback.json.unresolved_elements = []` — the artifact literally asserting
    # "0 of 32 unresolved" while the report's D2 row said "NO for 2 of 32" — kept
    # the suite GREEN. D3 got this guard, D4/D6 got one; D2 did not.
    #
    # The answer is DERIVED from the two committed evidence blocks (`elements` is
    # the 32-row walk, `unresolved_elements` the names it could not resolve), which
    # reproduces the artifact's own wording exactly on the committed data — so this
    # is a re-derivation, not a new claim. (The readback receipt cannot be
    # regenerated: REPORT §8 records that decision, and its sha is the artifact's
    # provenance binding.)
    # ⛔ A MISSING EVIDENCE SHAPE IS NOT "THE FINDING STANDS" (#14691, 4th
    # fresh-context review, NEW-2). The first cut guarded the whole re-derivation
    # with `if isinstance(unresolved, list) and walked:`, so a readback WITHOUT
    # either field silently kept the producer's prose with no evidence behind it —
    # the fail-open this block exists to close, one shape-change away. A finding
    # needs its rows; without them it is WITHDRAWN, exactly as D3 is when its
    # probes did not answer. (`enumerate`/`next` are computed once so the delete
    # cannot shift the index it is working on.)
    unresolved = READBACK.get("unresolved_elements")
    walked = READBACK.get("elements") or []
    idx = next((i for i, f in enumerate(found) if f.get("id") == "D2"), None)
    if idx is not None:
        if isinstance(unresolved, list) and walked and unresolved:
            found[idx] = {
                "id": "D2",
                "question": found[idx].get("question"),
                "answer_over_mcp": (f"NO for {len(unresolved)} of {len(walked)}"),
                "evidence": {"unresolved_elements": list(unresolved),
                             "walked": len(walked),
                             "elements": [e.get("name") for e in walked]},
            }
        else:
            # either nothing is unresolved (the artifact denies its own claim) or
            # the rows that would support it are absent (nothing measured it)
            del found[idx]

    if not any(f.get("id") == "D4" and witness for f in found):
        # ⛔ AN ERROR IS NOT SILENCE (#14691 review, 5th cycle, N1). An
        # `{"error": …}` reply satisfied `not rows(...)` and MANUFACTURED D4 —
        # "it returns [] for a node that has edges" — from a call that never
        # answered. Measured: replacing all seven witness `expand_relationships`
        # replies with error envelopes left the suite green and the finding in
        # place. `call_failed` tests the CALL as well as its `reply`, because a
        # failure that carries no `reply` key at all is the same measurement gap.
        silent = {name: {"expand_rows": 0,
                         "explore_neighbours": w.get("explore_neighbour_count")}
                  for name, w in witness.items()
                  if "reply" in (w.get("expand_relationships") or {})
                  and not rows((w.get("expand_relationships") or {})
                               .get("reply"))
                  and not call_failed(w.get("expand_relationships"))
                  and (w.get("explore_neighbour_count") or 0) > 0}
        if silent:
            found.append({
                "id": "D4",
                "question": "does `expand_relationships` report the STRUCTURAL "
                            "edges of an entity the way it reports the epistemic "
                            "ones?",
                "answer_over_mcp": "NO — it returns [] for a node that has edges, "
                                   "so a structurally-connected entity reads as "
                                   "isolated",
                "evidence": silent,
            })
    # ⛔ BOTH SPELLINGS OF AN EMPTY ANSWER, and absence is still not one of them
    # (#14691 review, 4th cycle). The previous arm required the BARE list `== []`,
    # which is one of the two shapes this readback actually uses — six other doors
    # in the same file answer `{"result": []}` (e.g. `structure_check`), and the
    # pin beside this code accepted both. Measured: rewriting the seven witness
    # replies to the envelope spelling dropped D6 from the artifact while all 18
    # tests stayed green, so the bundle's headline finding could vanish through a
    # shape change alone. Normalising through `rows()` here is the same unwrap
    # `a2_edges()` already does, and it keeps the distinction this fix exists for:
    # a MISSING probe has no `reply` key at all and is still NOT a measured empty.
    owned = _ownership_empty_answers()
    #: The owners asked, from the CORPUS's declared people — not a typed list.
    #: ⛔ #14723 review, round 10, P3: this set used to be hardcoded
    #: (`{"Amara Osei", "Dana Whitlock", "Tomas Ferreira", "Jonas Bauer"}`), so a
    #: witness population that named anyone else would have made `asked_owners`
    #: empty and SILENTLY WITHDRAWN the bundle's headline finding — the same
    #: fail-open D2/D3/D4 were given guards against. The declared people are what
    #: the corpus says the owners are, so they are the population the question is
    #: about.
    declared_owners = set(DECL["people"])
    asked_owners = [n for n in owned if n in declared_owners]
    #: The row count the answer quotes, from the WRITE RECEIPT (`EDGES`) — never
    #: typed, or a re-run with a different population would emit a figure
    #: contradicting the artifacts while the REPORT↔artifact arm drifted with it.
    owned_rows = [e for e in EDGES if e["predicate"] == "ownedBy"]
    if len(asked_owners) >= 2:
        found.append({
            "id": "D6",
            "question": "does the product's OWNERSHIP door — "
                        "`tortoise_get_entity(id, type='governance')` — return "
                        "what each person owns?",
            "answer_over_mcp": f"NO — it returns [] for all "
                               f"{len(asked_owners)} owners asked, on a graph "
                               f"holding {len(owned_rows)} ownedBy rows",
            "evidence": {"owners_asked": sorted(asked_owners),
                         "owned_by_rows": len(owned_rows),
                         "rows_returned": {n: 0 for n in sorted(asked_owners)},
                         "code_path": "mcp_server.tortoise_get(type='governance') "
                                      "-> sdk._get_owned_entities: "
                                      "MATCH (s)<-[:ownedBy]-(e) "
                                      "(sdk.py:62838-62858), i.e. the DECLARED "
                                      "orientation — the edges point the other way"},
        })

    not_found = {}
    for name, w in witness.items():
        reply = (w.get("graph_overview_topics") or {}).get("reply")
        text = json.dumps(reply, default=str)
        if "not found" in text:
            not_found[name] = w.get("id")
    if not_found:
        # ⛔ "FOR EVERY NODE" IS A QUANTIFIER, SO IT MUST COUNT (#14691 review,
        # round 12, P3-1). The sentence was a literal: with 5 of 7 `list_topics`
        # replies rewritten `not found` → `was found`, the artifact claimed EVERY
        # node failed while its own `reported_not_found` held two. The sentence is
        # now derived from the two populations it quantifies over, and it says "for
        # all N of the M asked about" rather than a bare "every".
        asked_total = sum(1 for w in witness.values()
                          if (w.get("graph_overview_topics") or {}).get("reply")
                          is not None or "not found" in str(
                              (w.get("graph_overview_topics") or {}).get("reply")))
        found.append({
            "id": "D5",
            "question": "does `graph_overview(section='topics', entity_id=…)` "
                        "resolve an entity that `get_entity` and "
                        "`explore_connections` both resolve?",
            "answer_over_mcp": (f"NO — it answers 'Entity … not found' for "
                                f"{len(not_found)} of the {asked_total} nodes asked "
                                f"about, including nodes those two doors return"),
            "evidence": {"reported_not_found": not_found,
                         "nodes_asked": asked_total},
        })
    return found


def _withheld_findings() -> list:
    """The door findings this run could NOT emit, and WHY — never a silence.

    ⛔ A FINDING THAT CANNOT BE DERIVED MUST SAY SO (#14723 review, round 10,
    P3). D6 is emitted only when at least two DECLARED owners were asked, and with
    `DECL["people"] = []` it simply disappeared from `door_findings` —
    `['D1','D2','D3','D4','D6','D5']` → `['D1','D2','D3','D4','D5']` — with
    nothing recording that the bundle's headline ownership finding had been
    withdrawn, so a reader could not tell a withdrawn finding from one the run
    never asked. A2 already states its own unasked population
    (`ownership_door.owners_not_asked`); this is the same shape for the findings
    list, and it is DERIVED from the same reading `_door_findings` uses
    (`_ownership_empty_answers`), so the two cannot disagree.
    """
    owned = _ownership_empty_answers()
    asked = [name for name in owned if name in set(DECL["people"])]
    if len(asked) >= 2:
        return []
    return [{
        "id": "D6",
        "reason": "fewer than two DECLARED owners answered an empty reply, so "
                  "'it returns [] for all N owners' has no population to be "
                  "about — the finding is WITHDRAWN, not absent",
        "owners_asked": sorted(asked),
        "declared_people": sorted(DECL["people"]),
        "witnesses_answering_empty": sorted(owned),
    }]


def _claim_layer() -> dict:
    """The layer ABOVE the structure — reported, never folded into A4's verdict.

    T6 measures structure; the invented `quote` spans are evidence about the
    claims hanging off it, which is the T1–T5 axis. Folding them into A4 would
    report a structure failure that the structure does not have.
    """
    q = a4_invention()["quotes"]
    return {"quotes_total": q["total"], "verbatim": q["verbatim"],
            "paraphrase": q["paraphrase"],
            #: quotes whose support exists only ACROSS a turn — a multi-fact
            #: summary, not an invention (#14723 review, round 10, P2)
            "summary_in_one_turn": q["summary_in_one_turn"],
            #: the population the corpus does not state anywhere. THIS is the
            #: claim-layer finding, and it is what `passes` is computed from.
            "invented": q["invented"],
            #: the stricter SINGLE-SENTENCE population (the peer audit's own
            #: number). Recorded, never read as the invented count.
            "below_sentence_band": q["below_sentence_band"],
            "bands": dict(q["bands"]),
            "bands_turn_scope": dict(q["bands_turn_scope"]),
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


def build_score() -> dict:
    """The whole score, DERIVED from the committed artifacts and nothing else.

    Split out of `main()` so the falsifier suite can RE-DERIVE this artifact and
    compare it to the committed one (#14691 review, 6th cycle, finding 2).
    Without the split, everything a test did not happen to name was free: the
    EVIDENCE rows — `corpus_chars`, `edge_count`, `entity_names_checked`, the
    warnings block, every `ownership[].verdicts`, every A5 probe's ranks and
    `extraction_tokens.by_doc` — could be rewritten while the suite stayed green,
    and two of them then contradicted REPORT.md, which also stayed green. A
    function that returns the score without writing is what makes the artifact
    checkable rather than trusted.
    """
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
        "door_findings": _door_findings(),
        # findings the run could not derive, with the reason — never a silence
        "withheld_findings": _withheld_findings(),
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
    return score


def main(argv: list[str] | None = None) -> int:
    """Write the score, or with `--check` compare and REFUSE to overwrite.

    ⛔ `--check` WAS A NO-OP (#14691 review, round 6, finding 4). `sys.argv` was
    never parsed: the scorer always rewrote `out/t6-score.json` and exited 0, so
    "run the scorer `--check`" — the gate the review brief names — would happily
    repair a corrupted artifact and report success. It now compares the committed
    bytes to a fresh derivation and exits 1 on any difference, writing nothing.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in argv
    if not DIRECTIONS:
        # ⛔ A DEGRADED DERIVATION IS NOT A DERIVATION. With the ruled table
        # unavailable the scores silently lose every predicate's direction, so the
        # artifact written (or the comparison made) would be wrong in a way nothing
        # downstream can see.
        print(f"⛔ the direction table is unavailable ({DIRECTIONS_SOURCE}) from "
              f"{_REPO_ROOT}; refusing to score against a partial contract",
              file=sys.stderr)
        return 1
    score = build_score()
    rendered = json.dumps(score, indent=2, default=str) + "\n"
    artifact = OUT / "t6-score.json"
    if check:
        # ⛔ COMPARE THE SCORE, NOT ITS FORMATTING. The committed file's byte
        # layout belongs to whichever tool wrote it; the CLAIM is the parsed
        # object. A byte comparison reds on a re-indent (measured: 29 bytes of
        # whitespace here) and would teach the next lane to regenerate a 55 KB
        # artifact to fix a space — the drift §8 exists to disclose.
        try:
            committed = json.loads(artifact.read_text()) if artifact.exists() else None
        except ValueError as exc:
            print(f"⛔ out/t6-score.json is not JSON: {exc}", file=sys.stderr)
            return 1
        if committed != json.loads(rendered):
            print("⛔ out/t6-score.json is NOT the scorer's derivation",
                  file=sys.stderr)
            return 1
        print("✅ out/t6-score.json is exactly what its inputs derive")
        return 0
    artifact.write_text(rendered)

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
