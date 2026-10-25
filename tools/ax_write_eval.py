#!/usr/bin/env python3
"""The write-pipeline eval instrument for `how-to-use-tortoise` (#9675).

WHAT THIS MEASURES, and why it is mechanical
--------------------------------------------
Issue #9675 requires that the AX document be **derived from measured agent
failures**, not written from imagination (Step 1: "run ten representative
inputs against an agent with the tools and no skill, recording per input:
which layer each item was written as, which kinds, which edges, whether a
mitigation was used at all, whether a source was attached"). Step 3's
acceptance is the same measurement re-run: "the acceptance is the delta, per
input, not a review verdict".

So the record must come from the GRAPH the agent wrote, never from the agent's
own summary of what it wrote. An agent that mislabels a Subject as a Point is
not a reliable narrator of that fact — the label is read off the node.

Two subcommands:

``record --db <path> --out <json>``
    Read a graph (embedded FalkorDBLite db path, or a ``docker://`` URI) and
    write the structural record: every node with its LABEL (the layer), its
    kind property, the operator points and their op_type, every edge with its
    relation and endpoints, and the Sources. Nothing is scored here.

``score --db <path> --gold <inputs.json> --id <input id> [--out <dir>]``
    Record, then compare against the FROZEN gold for that input, emitting
    per-item findings. The gold is frozen before any run
    (``eval/how-to-use-tortoise/step1/inputs.json``), so the delta cannot be
    retrofitted to whatever the agent happened to do.

    ``--out`` names a DIRECTORY and writes TWO files, because a verdict without
    its evidence cannot be audited: ``score.json`` (the per-item verdicts) and
    ``graph.json`` (the structural record it was read from — nodes, labels,
    kinds, edges). The committed run directories hold both.

    ``--graph <path>`` re-scores a COMMITTED ``graph.json`` with no database at
    all. That is the audit path, and it is exact: scoring a committed record
    reproduces its committed ``score.json`` (`tests/test_ax_write_eval.py` pins
    it for all ten runs).

The matching is deliberately dumb (token overlap over the node's name/content)
because a clever matcher would hide the very failure being measured: a
"Northwind Logistics Ltd" that became a SECOND node must show as a duplicate,
not be silently unified with the first by a fuzzy matcher.

Usage:
    uv run python tools/ax_write_eval.py record --db /tmp/eval/portfolio_update.db
    uv run python tools/ax_write_eval.py score  --db /tmp/eval/portfolio_update.db \
        --gold eval/how-to-use-tortoise/step1/inputs.json --id portfolio_update
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — this tool imports
# `tortoise.*` (3.12 floor) and the eval's commands are documented as a bare
# system-interpreter invocation, where the ambient interpreter can be older.
# Without this the run dies with an UNATTRIBUTED error that reads as "the eval is
# broken" (#5128, D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/ax_write_eval.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ax_write_eval.py`"
    )

import argparse
import json
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLD = REPO_ROOT / "eval" / "how-to-use-tortoise" / "step1" / "inputs.json"

# The four epistemic/semantic labels, plus Source (provenance, §4.6). Anything
# else on a node (`Meta`, `Session`, …) is infrastructure and is NOT scored.
LAYERS = ("Point", "Subject", "Object", "Event", "Source")

# Kind property per label — §4.1 pointKind, §4.2 subjectKind, §4.3 objectKind,
# §4.5 eventKind, §4.6 sourceKind. A layer whose kind property is absent is a
# finding in its own right ("a kind nothing declares is a kind nothing routes
# to", #9675 step 2).
KIND_PROP = {
    "Point": "pointKind",
    "Subject": "subjectKind",
    "Object": "objectKind",
    "Event": "eventKind",
    "Source": "sourceKind",
}

# The read order of the kind columns in `record()`'s single query. Derived from
# KIND_PROP so a new layer cannot be added to one and forgotten in the other.
KIND_PROPS = tuple(KIND_PROP[label] for label in LAYERS)

# ── the declared vocabularies ─────────────────────────────────────────────
#
# A kind is judged against what the product ACTUALLY declares, at four levels,
# and the four are NOT the same finding:
#
#   CORE        — declared by an ONTOLOGY §5 core set (`CANONICAL_*`, imported).
#   PACK        — declared by a loaded expansion pack (bare or namespaced —
#                 `disbursement` and `venture:disbursement` are one declaration,
#                 and the packs are the product's own extension point).
#   LEGACY      — declared by the product's own registry: the SDK's module-level
#                 `register_kind` calls (`option`, `evidence`, `criterion`, …)
#                 and the legacy flat vocabulary behind `known_kinds(bucket)`.
#                 The hosted write validator accepts these, so calling them
#                 inventions is a false failure (#9695 review P1).
#   UNDECLARED  — the product's own predicate does not know it on this layer, so
#                 nothing routes to, finds or classifies it. This is the failure
#                 #9675 names ("a kind nothing declares is a kind nothing routes
#                 to").
#
# Re-spelling the core sets here would let the instrument drift from the product,
# and IGNORING the packs (or the registry) would manufacture failures out of
# writes that followed a declared vocabulary — so the core sets are imported, the
# pack registry is loaded, and the product's own `kind_is_known` predicate is
# consulted, with the active pack set recorded beside every verdict.

_PACK_CACHE: dict | None = None


def _pack_vocab() -> dict[str, set[str]]:
    """Every kind the LOADED packs declare, per layer, namespaced and bare.

    Cached: the registry parses one manifest per pack in `packs/` — the count
    follows the tree and is deliberately NOT restated here, because a new pack
    would silently invalidate the number (a stale flag in the instrument's own
    docstring is the failure class this eval exists to remove) — and the eval
    scores ten graphs. A registry that cannot load is NOT swallowed — an empty
    pack vocabulary would report every pack-following write as undeclared, which
    is the false failure this function exists to prevent.
    """
    global _PACK_CACHE
    if _PACK_CACHE is not None:
        return _PACK_CACHE
    from tortoise.pack_registry import PackRegistry

    registry = PackRegistry(REPO_ROOT / "packs")
    registry.load_all()
    by_layer: dict[str, set[str]] = {layer: set() for layer in LAYERS}
    # The manifest is a dataclass, not a dict: the declared kind lists are
    # read off its own attributes (`object_kinds` / `event_kinds` / …), so a
    # rename in the manifest schema fails loudly here instead of quietly
    # reporting every pack kind as undeclared.
    # The manifest schema declares object/event/point kinds (plus a document
    # `document_kinds` genre axis it does not use for nodes); it has NO
    # subject-kind or source-kind list, so those two layers simply have no pack
    # axis — stated here rather than silently asking for an attribute that does
    # not exist.
    attrs = {
        "Object": "object_kinds",
        "Event": "event_kinds",
        "Point": "point_kinds",
    }
    for pack in registry.packs.values():
        ns = pack.namespace
        for layer, attr in attrs.items():
            declared = getattr(pack, attr, None)
            if not declared:
                continue
            for kind in declared:
                by_layer[layer].add(str(kind))
                by_layer[layer].add(f"{ns}:{kind}")
        # `kindDefs` carries lifecycle/retry metadata (`description`, `synonyms`,
        # `statuses`, `nearMisses`, `enforcement`) — measured across every shipped
        # `packs/*/manifest.yaml` — and says nothing about which LAYER a kind
        # belongs to. A kindDefs-only name therefore has no node axis to be
        # credited to here; the block below can only fire for an entry that spells
        # `name` plus `layer`/`type`, which no manifest does today. It is NOT the
        # declaration route for a kind with no list membership (#9695 review).
        for kind_def in getattr(pack, "kind_defs", None) or {}:
            name = kind_def.get("name") if isinstance(kind_def, dict) else None
            label = (kind_def.get("layer") or kind_def.get("type")) if isinstance(kind_def, dict) else None
            if not name:
                continue
            for layer in LAYERS:
                if label and label.lower() == layer.lower():
                    by_layer[layer].update({str(name), f"{ns}:{name}"})
    _PACK_CACHE = {"by_layer": by_layer, "packs": sorted(registry.packs)}
    return _PACK_CACHE


def _vocab() -> dict[str, frozenset[str]]:
    """The CORE declared vocabularies, imported — never re-spelled here."""
    from tortoise.pack_registry import (
        CANONICAL_EVENT_KINDS,
        CANONICAL_OBJECT_KINDS,
        CANONICAL_POINT_KINDS,
        registered_source_types,
    )
    from tortoise.source_credibility import SOURCE_KIND_DEFAULTS
    from tortoise.subject_binding import subject_kind_names

    # Subject: the product's own declared set (`subject_kind_names()`), because
    # the canonical spelling of a §5 Subject kind is `core:team` and the bare
    # form is an accepted alias (`subject_binding.is_subject_kind`). Hardcoding
    # the bare forms here reported `core:organization` — the DECLARED key — as an
    # invention.
    subject_kinds = frozenset(subject_kind_names()) | frozenset(
        kind.split(":", 1)[1] for kind in subject_kind_names() if kind.startswith("core:")
    )
    return {
        # `documentKind` is its OWN axis (genre, §5) and is deliberately not a
        # pointKind: folding `CANONICAL_DOCUMENT_KINDS` in here would bless the
        # exact confusion the research input measures.
        "Point": frozenset(CANONICAL_POINT_KINDS),
        "Object": canonical_object_kinds(CANONICAL_OBJECT_KINDS),
        "Event": frozenset(CANONICAL_EVENT_KINDS),
        "Source": registered_source_types() | frozenset(SOURCE_KIND_DEFAULTS),
        "Subject": subject_kinds,
    }


def canonical_object_kinds(kinds) -> frozenset[str]:
    """Core object kinds are matched case-insensitively (``Project`` vs ``project``).

    The §5 object vocabulary deliberately mixes cases (`Project`, `WorkItem`,
    `Problem`, `tag`), and the write path stores what the caller passed, so an
    exact-case comparison would fail a write that used the documented spelling
    with different capitalisation. The comparison set is lowercased; the finding
    still quotes the kind as written.
    """
    return frozenset(k.lower() for k in kinds)


def _kind_matches(got: str, allowed: list[str]) -> bool:
    """Does a written kind match the gold's allowed set?

    ``core:organization`` and ``organization`` are ONE kind — the namespaced
    form is the declared key and the bare form its accepted alias
    (``subject_binding.is_subject_kind``). Comparing raw strings reported the
    canonical spelling as a missed kind.
    """
    candidates = {got.lower(), got.split(":", 1)[-1].lower()}
    return bool(candidates & {a.split(":", 1)[-1].lower() for a in allowed})


def _own_vocabulary(layer: str, raw: str) -> str:
    """``CORE`` / ``PACK`` if THIS layer's own sets declare ``raw``, else ``""``.

    Split out of `kind_status` so the cross-axis probe below can ask the same
    question of the OTHER layers without recursing back into `kind_status`.
    """
    core = {k.lower() for k in _vocab().get(layer, frozenset())}
    if raw.lower() in core:
        return "CORE"
    pack_kinds = _pack_vocab()["by_layer"].get(layer, set())
    if raw in pack_kinds or raw.lower() in {p.lower() for p in pack_kinds}:
        return "PACK"
    return ""


def other_axes_declaring(layer: str, kind: str) -> list[str]:
    """The OTHER layers whose vocabulary declares ``kind`` (sorted, may be empty).

    Used for the honest wording of the cross-axis case: `meeting` is an Event
    kind, and the legacy flat registry accepts it on the point bucket only by
    accident (`domain_loader` says so itself: the `meeting` overlap "was never
    ruled on by #2767"). Accepting the registry's answer is right — the write
    validator does — but calling the result "declared" would hide exactly the
    confusion this eval exists to measure.
    """
    raw = kind.strip()
    if not raw:
        return []
    return sorted(
        other
        for other in LAYERS
        if other != layer
        and (_own_vocabulary(other, raw) or _registry_declares(other, raw))
    )


def kind_status(layer: str, kind: str) -> str:
    """``CORE`` / ``PACK`` / ``LEGACY`` / ``OTHER-AXIS`` / ``UNDECLARED``.

    The five-way answer is the point: "wrong kind" (declared, but not the kind
    this item is) and "kind nothing declares" are different lessons for the
    document, and collapsing them teaches neither.

    ``LEGACY`` is the fourth source and it is NOT a corner case: the product
    declares kinds through ``domain_loader.register_kind`` (``option`` /
    ``evidence`` / ``criterion``, registered by ``tortoise.sdk`` itself) and
    through the legacy flat registry, and ``known_kinds(bucket)`` — the
    predicate the hosted write validator uses — accepts both. Reading only the
    §5 canonical set made the decide flow's OWN kinds read as inventions and put
    them in the document's outline as failures (#9695 review P1, measured on
    `decision_request`: `option`, `criterion` ×2).

    ``OTHER-AXIS`` is the residue that answer must not swallow. The legacy
    bucket is FLAT, so it also accepts kinds that belong to another axis
    (`meeting`, `issue`, `incident`, `workflow`) — a canonical Event kind on a
    Point is the exact failure #9675 quotes ("events are classified as
    objects"), and blessing it as "declared" would blind the graph-wide census
    to it. So: declared, but declared for another layer, which is a finding with
    its own text (#9695 review round 2, P2).
    """
    if not kind:
        return "NONE"
    raw = kind.strip()
    own = _own_vocabulary(layer, raw)
    if own:
        return own
    if layer == "Subject":
        from tortoise.subject_binding import is_subject_kind

        if is_subject_kind(raw):
            return "CORE"
    if _registry_declares(layer, raw):
        return "OTHER-AXIS" if other_axes_declaring(layer, raw) else "LEGACY"
    return "UNDECLARED"


# The node buckets `domain_loader.known_kinds()` answers for. `subjectKind` has
# no pack category and `sourceKind` is not a bucket at all, so those two layers
# keep the vocabularies `_vocab()` imports.
_REGISTRY_BUCKETS = {"Point": "pointKind", "Object": "objectKind", "Event": "eventKind"}


def _registry_declares(layer: str, kind: str) -> bool:
    """Does the PRODUCT's own vocabulary know this kind on this layer?

    The single predicate behind the hosted write validator
    (``domain_loader.kind_is_known(kind, bucket)``), not a second list spelled
    here — a per-layer whitelist of the canonical sets is what let two declared
    kinds through as inventions (#9695 review P1). ``tortoise.sdk`` is imported
    before the question is asked because its module-level ``register_kind`` calls
    ARE part of the declaration; without them the answer would depend on whether
    some other code path had imported the SDK first.
    """
    bucket = _REGISTRY_BUCKETS.get(layer)
    if bucket is None:
        return False
    import tortoise.sdk  # noqa: F401 — importing it is what registers its kinds
    from tortoise.domain_loader import kind_is_known

    return kind_is_known(kind, bucket)


_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "we", "our",
    "is", "are", "was", "were", "be", "been", "with", "by", "at", "as", "it",
    "its", "that", "this", "from", "into", "over", "after", "before", "has",
    "have", "had", "will", "would", "should", "must", "not", "no", "their",
    "they", "them", "she", "he", "his", "her", "only", "still",
}


# ── graph read ─────────────────────────────────────────────────────────────


def _open(uri_or_path: str):
    """Open the graph through the product SDK — the same seam the eval agent used.

    Accepts an embedded db path (the eval lane) or a ``docker://`` URI. The env
    is set rather than passed so the SDK's own resolver decides, exactly as it
    does for a real writer.
    """
    if uri_or_path.startswith(("docker://", "redis://", "rediss://")):
        os.environ["TORTOISE_DB_URI"] = uri_or_path
        os.environ.pop("TORTOISE_DB_PATH", None)
    else:
        # ⛔ A mistyped path must never be answered with a graph (#9695 review
        # P2). The embedded lane CREATES a missing file/dir and hands back an
        # empty graph, so scoring a typo produced a full-finding record
        # indistinguishable from "the agent wrote nothing" — an artifact a
        # Step-3 lane would commit as evidence. `setup.py` already refuses a
        # target it cannot positively identify; this is the same refusal on the
        # READ side, which is where fabricated evidence would enter.
        if not Path(uri_or_path).exists():
            raise SystemExit(
                f"no graph at {uri_or_path!r}: the embedded lane would CREATE an "
                f"empty one and score it as a total-failure run, which cannot be "
                f"told apart from an agent that wrote nothing. Fix the path "
                f"(`--db`), or score an already-recorded graph with --graph."
            )
        os.environ.pop("TORTOISE_DB_URI", None)
        os.environ["TORTOISE_DB_PATH"] = uri_or_path

    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK()
    return sdk, sdk._get_proj()


def _rows(proj, cypher: str) -> list[list]:
    return list(proj.query(cypher).result_set)


def _node_from_row(
    labels, text, kind_values, is_operator, op_type, status, url, node_id=""
):
    """Shape one graph row into a record node. Pure, so the kind-axis rule is
    pinnable without a database (#9695 review P2).

    ``kind`` is the layer's OWN property (``KIND_PROP``); every other layer's
    kind property present on the same node is kept as ``foreign_kinds`` instead
    of being coalesced into ``kind``. A `subjectKind` written on an Object then
    reads as a finding in its own right, instead of as a legal-looking Object
    kind — one `coalesce` across all five cannot say WHICH property carried it,
    so `eventKind: meeting` on an Object passed silently.

    Returns ``None`` for infrastructure labels (Meta/Session/registry) — those
    are not written knowledge nodes.
    """
    labels = labels if isinstance(labels, list) else [labels]
    layer = next((label for label in LAYERS if label in labels), None)
    if layer is None:
        return None
    own = KIND_PROP[layer]
    kinds = {prop: (value or "").strip() for prop, value in kind_values.items()}
    return {
        "layer": layer,
        "labels": labels,
        "text": (text or "").strip(),
        "kind": kinds.get(own, ""),
        "kind_prop": own,
        "foreign_kinds": {p: v for p, v in kinds.items() if p != own and v},
        "is_operator": bool(is_operator),
        "op_type": (op_type or "").strip(),
        "status": (status or "").strip(),
        "url": (url or "").strip(),
        "id": str(node_id or "").strip(),
    }


def record(uri_or_path: str) -> dict:
    """Read the graph and return the structural record (no scoring)."""
    sdk, proj = _open(uri_or_path)
    # One query, all the props we need; FalkorDB returns positional rows. The
    # five kind properties come back as SEPARATE columns so the record can say
    # which property carried the kind (see KIND_PROP).
    kind_cols = ", ".join(f"coalesce(n.{prop}, '')" for prop in KIND_PROPS)
    rows = _rows(
        proj,
        "MATCH (n) RETURN labels(n), "
        "coalesce(n.name, n.content, n.title, n.url, ''), "
        f"{kind_cols}, n.is_operator, coalesce(n.op_type, ''), "
        "coalesce(n.status, ''), coalesce(n.url, ''), coalesce(n.id, n.eventId, '')",
    )
    nodes = []
    for row in rows:
        labels, text = row[0], row[1]
        kind_values = dict(zip(KIND_PROPS, row[2 : 2 + len(KIND_PROPS)], strict=True))
        tail = row[2 + len(KIND_PROPS) :]
        node = _node_from_row(labels, text, kind_values, *tail)
        if node is not None:
            nodes.append(node)

    edges = []
    for rel, src, dst, sl, dl, a_id, b_id in _rows(
        proj,
        "MATCH (a)-[e]->(b) RETURN type(e), coalesce(a.name, a.content, ''), "
        "coalesce(b.name, b.content, ''), labels(a), labels(b), "
        "coalesce(a.id, a.eventId, ''), coalesce(b.id, b.eventId, '')",
    ):
        sl = sl if isinstance(sl, list) else [sl]
        dl = dl if isinstance(dl, list) else [dl]
        edges.append(
            {
                "relation": rel,
                "from": (src or "").strip(),
                "to": (dst or "").strip(),
                "from_layer": next((lab for lab in LAYERS if lab in sl), None),
                "to_layer": next((lab for lab in LAYERS if lab in dl), None),
                "from_id": str(a_id or "").strip(),
                "to_id": str(b_id or "").strip(),
            }
        )
    sdk.close()

    operator_ids = {n["id"] for n in nodes if n["is_operator"] and n["id"]}
    counts: dict[str, int] = {layer: 0 for layer in LAYERS}
    for node in nodes:
        counts[node["layer"]] += 1
    return {
        "nodes": nodes,
        "edges": edges,
        "counts": counts,
        "operators": [n for n in nodes if n["is_operator"]],
        "sources": [n for n in nodes if n["layer"] == "Source"],
        "relations": sorted({e["relation"] for e in edges}),
        # A mitigation is the relevance dampener on an OPERATOR: `mitigate_operator`
        # writes `mitigated_by`, and an INVALIDATES edge is the same lane under its
        # epistemic name. Both are "relevance attacked", never "truth attacked" —
        # AND one endpoint must be the operator node, or the count says nothing
        # about relevance (a mitigation edge between two plain claims would satisfy
        # a bare relation-name test while damping nothing).
        "mitigations": [
            e
            for e in edges
            if ("MITIGAT" in e["relation"].upper() or "INVALID" in e["relation"].upper())
            and (e["from_id"] in operator_ids or e["to_id"] in operator_ids)
        ],
    }


# ── scoring ────────────────────────────────────────────────────────────────


_SUFFIXES = ("ings", "ing", "ied", "ies", "ed", "es", "al", "ly", "s")


def _stem(word: str) -> str:
    """Crude stem, so `join`/`joins`/`joined` and `renew`/`renewal` compare equal.

    Without it the matcher reads "joined Northwind Logistics" (item) against an
    Event named "… joins Northwind Logistics …" as a MISS and reports a layer
    failure on a write that was right — measured on three committed runs. A real
    stemmer is a dependency this eval does not need; the failure mode it removes
    is a plural/tense mismatch, and that is what this covers.

    The final `-e` drop is what makes the `-e` noun class work: `es` is stripped
    before a word's own trailing `e` is considered, so `values` stemmed to
    `valu` while `value` stayed `value` and the two shared NO token — a correct
    write could then fall under the 0.5 acceptance floor and be reported as a P0
    ABSENT item, the harshest verdict the instrument emits, for a plural/tense
    difference (`value`, `estimate`, `deadline`, `slide`, `principle`, …).
    Canonicalising BOTH sides to the `e`-dropped form fixes the class without
    inflating either side's token count — which an "add the other form too" fix
    would do, silently moving every partial-overlap ratio (#9695 review P1).
    """
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def _tokens(text: str) -> set[str]:
    """Significant tokens of a node's text.

    Everything that is not alphanumeric (or ``$``/``%``) SEPARATES tokens, so
    ``frozen.``, ``frozen`` and ``(frozen)`` are one token and ``sub-5-minute``
    splits exactly as ``sub 5 minute`` does. Leaving punctuation attached
    silently makes an expectation whose word ends a sentence — or is hyphenated
    in the node — score as a miss (both were measured on committed runs).

    Every stem that ENDS in ``e`` is canonicalised to its ``e``-dropped form by
    `_stem`, so a plural and its singular meet (`value`/`values` → `valu`).
    """
    words = re.findall(r"[a-z0-9$%]+", text.lower())
    return {_stem(w) for w in words if len(w) >= 2 and w not in _STOP}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _match(
    expect_item: str, nodes: list[dict], want_layer: str
) -> tuple[dict | None, float, list[dict]]:
    """Find the ONE node that best answers the expected item.

    Returns ``(best, score, duplicates)``. The best node is the single closest
    candidate, ranked by: exact name equality, then a node whose text CONTAINS
    every significant token of the expectation (the tighter node wins — a
    300-character Point that happens to mention "Dana" is not the node that
    represents Dana), then partial overlap at half weight.

    Deliberately ONE best, not "every node that mentions it": judging a layer
    off the union of every node that mentions a name is how a matcher
    manufactures layer-confusion noise out of correct writes. A fuzzy matcher
    would also hide the very failure being measured — a ``Northwind Logistics
    Ltd`` that became a SECOND node must read as a duplicate.
    """
    want = _tokens(expect_item)
    if not want:
        return None, 0.0, []
    scored: list[tuple[float, dict]] = []
    for node in nodes:
        have = _tokens(node["text"])
        if not have:
            continue
        if want <= have:
            exact = _norm(node["text"]) == _norm(expect_item)
            # Containment scores ABOVE any partial overlap but below an exact
            # name match: a 20-word Point that reports the figure is a real
            # candidate for the figure, it just has to lose to a node whose
            # whole job is that item.
            scored.append((1.0 if exact else 0.6 + 0.4 * len(want) / len(have), node))
        else:
            hit = want & have
            if hit:
                scored.append((0.5 * len(hit) / len(want), node))
    # A TIE must not be resolved by list order. The record query has no ORDER BY
    # and a live run's row order is not something the instrument controls, so an
    # `_match` that sorted on the score alone let two equally-scored nodes swap
    # the winner — and the winner picks the layer the verdict is computed from.
    # `_expected_layer_twin` already breaks its ties explicitly; this is the same
    # key: score, then the expected layer, then not-superseded, then the tighter
    # text, then the text itself so even a full tie is deterministic (#9695
    # review P2).
    scored.sort(
        key=lambda pair: (
            -pair[0],
            pair[1]["layer"].lower() != want_layer.lower(),
            pair[1]["status"].lower() == "superseded",
            len(pair[1]["text"]),
            pair[1]["text"],
        )
    )
    if not scored or scored[0][0] < 0.5:
        return None, scored[0][0] if scored else 0.0, []
    # SAME-LAYER PREFERENCE — WITH A FLOOR. Layer confusion means the item is
    # represented on a DIFFERENT layer and NOT on the expected one. If a node on
    # the expected layer ALSO represents it (the claim AND its Object both name
    # the figure), the item was filed correctly and the duplicate representation
    # is the finding.
    #
    # The floor is load-bearing: without it a same-layer candidate scoring 0.08
    # (one token of six, on a node that merely mentions the subject) outranks a
    # different-layer containment match at 0.83, and the instrument then reports
    # an unrelated node as the item — accusing a node the agent never wrote the
    # item as, and losing the real layer failure underneath. Measured on two
    # committed runs before this guard existed.
    same = [
        pair
        for pair in scored
        if pair[1]["layer"].lower() == want_layer.lower() and pair[0] >= 0.5
    ]
    if same and (scored[0][0] < 0.6 or same[0][0] >= scored[0][0] - 0.2):
        scored = same
    best, best_score = scored[0][1], scored[0][0]
    # Duplicates: another node carrying the SAME identity. This is `score()`'s
    # own rule (`_same_identity`, below), not a looser second definition: a bare
    # prefix test made "Acme" and "Acme Capital" one identity, so a graph that
    # minted nothing twice could read as a duplicate-identity P1 (the realistic
    # portfolio/company case). One instrument, one meaning (#9695 review P2).
    dups = [
        node
        for _, node in scored[1:]
        if node is not best
        and node["layer"] == best["layer"]
        and _same_identity(node["text"], best["text"])
    ]
    return best, best_score, dups


# Legal-form suffixes that do NOT make a second entity: "Northwind Logistics"
# and "Northwind Logistics Ltd" are one company.
_LEGAL_FORM_SUFFIXES = frozenset(
    {
        "ltd", "limited", "inc", "incorporated", "plc", "llc", "gmbh",
        "corp", "corporation", "co", "sa", "bv", "ag",
    }
)


def _same_identity(text: str, target: str) -> bool:
    """Is ``text`` the SAME entity name as ``target``?

    Same normalised name, or one name plus a legal-form suffix — and a name that
    merely STARTS with the other ("Northwind Logistics confirmed …", "Acme" vs
    "Acme Capital") is a different entity or a claim ABOUT it, never a duplicate.
    The word boundary is what keeps the two apart, so it is shared by the
    duplicate-identity rule and `_match` rather than spelled twice.
    """
    norm, target_norm = _norm(text), _norm(target)
    if not norm or not target_norm:
        return False
    if norm == target_norm:
        return True
    for form in (target_norm, norm):
        other = norm if form == target_norm else target_norm
        if other.startswith(form + " "):
            residual = set(other[len(form) + 1 :].split()) - _LEGAL_FORM_SUFFIXES
            if not residual:
                return True
    return False


def _edge_endpoints(spec: dict, item: str, input_id: str) -> dict[str, set[str]]:
    """The endpoint shape a gold EDGE item states, or a refusal to grade it.

    An edge is a claim about BOTH ends: `reportsTo` written Point -> Point (or
    `ownedBy` between two Points) satisfies a relation-name test while saying
    the opposite of what the item is about — a false PASS in the one number
    Step 3 reads as its acceptance delta (#9695 review P1). The gold already
    states the shape in prose (`"an ORG edge between Subjects"`); this reads it
    as a declaration. A gold edge item that declares NEITHER end, or only ONE
    of them, is refused rather than graded by name alone — refusing to answer
    beats answering the wrong question, and the refusal names the missing side.

    ⛔ BOTH sides are required, not merely whichever sides are present (#9695
    review P1). A one-sided declaration silently un-grades the other end: `owned
    by` stated only `to: [Subject]`, so an `ownedBy` written Point -> Subject
    scored `OK` — exactly the false PASS correction 29 was added to close.
    Requiring both is what makes this class's claim ("graded on the ENDS") true
    for every gold edge item instead of for most of them.
    """
    ends = spec.get("endpoints")
    known = {layer.lower() for layer in LAYERS}
    wanted: dict[str, set[str]] = {}
    problem = None if isinstance(ends, dict) else f"expected an object, got {ends!r}"
    for side in ("from", "to") if isinstance(ends, dict) else ():
        raw = ends.get(side)
        if raw is None:
            continue
        values = raw if isinstance(raw, list) else [raw]
        got = {str(v).strip().lower() for v in values if str(v).strip()}
        unknown = got - known
        if problem is None and (not got or unknown):
            problem = f"{side}: {sorted(got)} (unknown layer(s) {sorted(unknown)})"
        wanted[side] = got
    stray = sorted(set(ends) - {"from", "to"}) if isinstance(ends, dict) else []
    if problem is None and stray:
        problem = f"unknown key(s) {stray} — only `from` / `to` are endpoint sides"
    missing = [side for side in ("from", "to") if not wanted.get(side)]
    if problem is None and missing:
        problem = (
            "missing side(s) "
            + ", ".join(f"`{side}`" for side in missing)
            + " — an edge has two ends and BOTH are graded"
        )
    if problem is not None:
        raise SystemExit(
            f"gold item {item!r} (input {input_id!r}) is an EDGE item with no usable "
            f"`endpoints`: {problem}. An edge is a claim about both ends, and a "
            f"relation-name test alone passes when the relationship is wired between "
            f"the wrong layers. State the shape the item's `why` describes, naming a "
            f"layer: " + ", ".join(LAYERS) + '. e.g. "endpoints": '
            '{"from": ["Subject"], "to": ["Subject"]}'
        )
    return wanted


def _edge_fits(edge: dict, wanted: dict[str, set[str]]) -> bool:
    """True when the edge's endpoints are the layers the gold item requires."""
    return all(
        (edge.get(f"{side}_layer") or "").lower() in allowed
        for side, allowed in wanted.items()
    )


def _expected_layer_twin(item: str, nodes: list[dict], layer: str) -> dict | None:
    """A node on the EXPECTED layer that genuinely carries this item, if any.

    The bar is two thirds of the item's stems (and at least two of them), not a
    third — compared as ``hit * 3 >= 2 * len(want)``, never as a rounded float,
    so "0.6" cannot stand in for "two thirds" on a five-stem item: "the expected layer also names it" is a claim that the graph
    DISPROVES an absence, so a node that merely shares a couple of topic words
    must not qualify. Measured: at a third, the meeting Event "Pricing &
    self-serve decision meeting" qualified for the item "killing the self-serve
    tier" (2 of 4 stems) and turned a real absence into a weaker finding.

    Ties break toward the node that is not superseded and then toward the
    tighter text — on the product-spec input both plan versions name the ship
    date, and the REPLACED plan is not the one holding it.
    """
    want = _tokens(item)
    if len(want) < 2:
        return None
    best_key: tuple | None = None
    best_node: dict | None = None
    for node in nodes:
        if node["layer"].lower() != layer.lower() or node["is_operator"]:
            continue
        hit = len(want & _tokens(node["text"]))
        ratio = hit / len(want)
        if hit < 2 or hit * 3 < 2 * len(want):
            continue
        key = (ratio, node["status"].lower() != "superseded", -len(node["text"]))
        if best_key is None or key > best_key:
            best_key, best_node = key, node
    return best_node


def score(record_doc: dict, gold: dict, input_id: str) -> dict:
    entry = next((i for i in gold["inputs"] if i["id"] == input_id), None)
    if entry is None:
        raise SystemExit(f"no gold entry with id {input_id!r} in the gold file")
    nodes = record_doc["nodes"]
    findings: list[dict] = []
    item_rows: list[dict] = []

    def add(sev: str, item: str, what: str, fix: str) -> None:
        findings.append(
            {"severity": sev, "item": item, "finding": what, "fix": fix}
        )

    for exp in entry.get("expect", []):
        item = exp["item"]
        layer = exp["layer"]
        best, best_score, dups = _match(item, nodes, layer)
        fuzzy = best is not None and best_score < 0.75

        if layer == "edge":
            rels = [r.lower() for r in exp.get("kind", [])]
            # The endpoint constraint the gold states in its `why` is scored, not
            # just the relation name (`_edge_endpoints` explains the false PASS
            # this closes).
            wanted = _edge_endpoints(exp, item, input_id)
            named = [e for e in record_doc["edges"] if e["relation"].lower() in rels]
            got = [e for e in named if _edge_fits(e, wanted)]
            if not got and named:
                bad = named[0]
                shape = " / ".join(
                    f"{side} ∈ {sorted(allowed)}" for side, allowed in sorted(wanted.items())
                )
                add(
                    "P0",
                    item,
                    f"EDGE-LAYER confusion: {bad['relation']} is written "
                    f"{bad['from_layer'] or '?'} -> {bad['to_layer'] or '?'} "
                    f"({bad['from'][:40]!r} -> {bad['to'][:40]!r}), but the gold "
                    f"requires {shape} — {exp.get('why','')}",
                    "wire the relationship between the layers §3 names: the "
                    "endpoints are part of the claim, and a relation name between "
                    "the wrong pair answers a question nobody asked",
                )
                item_rows.append(
                    {
                        "item": item,
                        "expected": f"edge {bad['relation']} ({shape})",
                        "written": f"{bad['from_layer'] or '?'}:[{bad['relation']}]-> "
                        f"{bad['to_layer'] or '?'}",
                        "verdict": "EDGE-LAYER",
                    }
                )
            elif not got:
                add(
                    "P0",
                    item,
                    f"expected an EDGE with relation in {rels} — none written; "
                    f"relations present: {record_doc['relations']}",
                    "write the relationship as a graph edge (§3), not as a node "
                    "or a string property",
                )
                item_rows.append(
                    {"item": item, "expected": "edge", "written": None, "verdict": "MISSING"}
                )
            else:
                item_rows.append(
                    {
                        "item": item,
                        "expected": f"edge {got[0]['relation']}",
                        "written": f"{got[0]['from'][:40]} -[{got[0]['relation']}]-> {got[0]['to'][:40]}",
                        "verdict": "OK",
                    }
                )
            continue

        if best is None:
            add(
                "P0",
                item,
                f"expected {layer} — no node represents it (best overlap {best_score:.2f})",
                "the item was absorbed into prose or written under a name nothing "
                "can find",
            )
            item_rows.append(
                {"item": item, "expected": layer, "written": "(nothing)", "verdict": "MISSING"}
            )
            continue

        if best["layer"].lower() != layer.lower():
            # TWO HOMES vs ONE WRONG HOME. If the expected layer ALSO carries a
            # node naming this item, the item was not filed on the wrong layer —
            # it was filed TWICE, and neither copy answers alone (the Object can
            # never be "what is true now" while a Point holds the same fact, and
            # vice versa). That is a real finding, but a weaker one than "the
            # occurrence has no Event at all", and reporting it as an absence
            # accuses the agent of something its graph disproves.
            twin = _expected_layer_twin(item, nodes, layer)
            if twin is not None:
                add(
                    "P1",
                    item,
                    f"two homes: {best['layer']} {best['text'][:50]!r} holds this "
                    f"item, and so does {layer} {twin['text'][:50]!r} — one fact, two "
                    f"layers, so neither answers alone",
                    f"keep the {layer} node and express the {best['layer']} reading "
                    f"as a claim ABOUT it (§3 edges), never as a second home",
                )
                item_rows.append(
                    {
                        "item": item,
                        "expected": layer,
                        "written": f"BOTH {layer}:{twin['kind'] or '(none)'} and "
                        f"{best['layer']}:{best['kind'] or '(none)'}",
                        "node": best["text"][:80],
                        "verdict": "TWO-HOMES",
                    }
                )
                continue
            add(
                "P0",
                item,
                f"LAYER confusion: expected {layer}, written as {best['layer']} "
                f"({best['text'][:70]!r}) — {exp.get('why','')}",
                f"re-file as {layer}; a wrong layer cannot be rescued downstream "
                f"(kind_classifier partitions by type before choosing a kind)",
            )
            item_rows.append(
                {
                    "item": item,
                    "expected": layer,
                    "written": f"{best['layer']}:{best['kind'] or '(none)'}",
                    "node": best["text"][:80],
                    "verdict": "LAYER",
                }
            )
            continue

        allowed = [k.lower() for k in exp.get("kind", [])]
        # AS WRITTEN, never lowercased (#9695 review P2). The finding text, the
        # `kind` field and the doc all quote the spelling the GRAPH carries
        # (`customerCommitment`). Lowercasing here made one record state two
        # spellings of one kind — `UNDECLARED kind 'customercommitment'` beside
        # `kind_sources: {"customerCommitment": …}` — and contradicted
        # `canonical_object_kinds`'s own docstring. Matching stays
        # case-insensitive, inside `_kind_matches`.
        got_kind = best["kind"] or ""
        own_prop = best.get("kind_prop") or KIND_PROP.get(best["layer"], "kind")
        foreign = best.get("foreign_kinds") or {}
        # A kind written to a DIFFERENT layer's property is its own finding. The
        # word may be perfectly legal on the other axis (`meeting` is an
        # eventKind; an Object carrying `eventKind: meeting` is still invisible
        # to every Object reader), so calling it "undeclared" would be false —
        # and one `coalesce` across all five properties hid it completely
        # (#9695 review P2).
        if foreign:
            carried = ", ".join(f"{p}={v!r}" for p, v in sorted(foreign.items()))
            add(
                "P1",
                item,
                f"CROSS-AXIS kind: {best['layer']} {best['text'][:50]!r} carries "
                f"{carried} — that property belongs to another layer"
                + (f" and its own {own_prop} is empty" if not got_kind else "")
                + f", so nothing routes to, finds or classifies it as a {best['layer']}",
                f"write the kind to {own_prop} (§4 {best['layer']} / §5)",
            )
        status = kind_status(best["layer"], best["kind"])
        # ONE ACT, ONE FINDING. When the node's OWN kind property is empty and its
        # kind sits on another layer's property, the cross-axis finding above has
        # already charged that one write: `kind_status("")` is "NONE" and "" matches
        # no allowed kind, so the checks below would fire a second P1 whose text
        # ("declared, but not the kind this item is" / "written ''") is false in its
        # own terms — nothing was written on this layer's property at all. The
        # census skips such a node for this exact reason (#9695 review P2).
        cross_axis_charged = bool(foreign) and not got_kind
        if cross_axis_charged:
            verdict = "KIND-CROSS-AXIS"
        elif not got_kind and allowed:
            # The other half of the same invariant: an EMPTY own property with NO
            # foreign kind. It used to fall into the branch below and be told it
            # had written a kind "declared, but not the kind this item is" — false
            # in its own terms, since nothing was written — and `unkinded` then
            # charged the same node a second time (#9695 review P1/P2). One
            # finding, with honest text, and `unkinded` skips gold-named nodes.
            add(
                "P1",
                item,
                f"no kind written on {own_prop} for {best['layer']} "
                f"{best['text'][:60]!r} — the item expects one of {exp['kind']}, "
                f"so nothing routes to, finds or classifies it as a "
                f"{best['layer']}",
                f"write the kind to {own_prop} (§4 {best['layer']} / §5)",
            )
            verdict = "KIND-MISSING"
        elif status == "UNDECLARED":
            findings.append(
                {
                    "severity": "P1",
                    "item": item,
                    "layer": best["layer"],
                    "kind": got_kind,
                    "finding": (
                        f"UNDECLARED kind {got_kind!r} on a {best['layer']} "
                        f"({best['text'][:50]!r}) — not a core §5 kind, not one the "
                        f"SDK registers, and not one any loaded pack declares; "
                        f"nothing routes to, finds or classifies it"
                    ),
                    "fix": (
                        f"use a declared {best['layer']} kind (§5 core, or a pack "
                        f"kind spelled as the pack declares it)"
                    ),
                }
            )
            verdict = "KIND-UNDECLARED"
        elif allowed and not _kind_matches(got_kind, allowed):
            add(
                "P1",
                item,
                f"kind outside the frozen set: expected one of {exp['kind']}, "
                f"written {got_kind!r} ({best['text'][:60]!r}) — declared, but "
                f"not the kind this item is",
                "pick a kind the active vocabulary declares for this layer (§5)",
            )
            verdict = "KIND"
        else:
            verdict = "OK"
        if dups:
            add(
                "P1",
                item,
                f"duplicate nodes for one identity: {len(dups) + 1} "
                f"({[n['text'][:40] for n in [best, *dups]]})",
                "a re-mention must UPDATE the existing node, never mint a second one",
            )
            verdict = verdict if verdict != "OK" else "DUP"
        item_rows.append(
            {
                "item": item,
                "expected": f"{layer}:{'|'.join(exp.get('kind', []))}",
                "written": f"{best['layer']}:{best['kind'] or '(none)'}",
                "node": best["text"][:80],
                "fuzzy": fuzzy,
                "verdict": verdict,
            }
        )

    # Provenance — §4.6. A Source node is the floor; the edge is the standard.
    if entry.get("expect_source"):
        if not record_doc["sources"]:
            add(
                "P0",
                "(provenance)",
                "no Source node written; every claim is uncheckable",
                "write the source (§4.6) and tie claims to it with extractedFrom",
            )
        else:
            # One endpoint must BE the Source. A bare relation-name test passes on
            # any `references`/`wasDerivedFrom` edge between two claim nodes, which
            # says nothing about provenance.
            provenance_edges = [
                e
                for e in record_doc["edges"]
                if e["relation"].lower()
                in {"extractedfrom", "citedfrom", "sourcedfrom", "references", "wasderivedfrom"}
                and "Source" in (e["from_layer"], e["to_layer"])
            ]
            if not provenance_edges:
                add(
                    "P1",
                    "(provenance)",
                    f"{len(record_doc['sources'])} Source node(s) but ZERO claim→source "
                    f"edges (relations present: {record_doc['relations']})",
                    "attach each extracted claim to its source; a Source with no edge "
                    "is a scrapbook entry, not provenance",
                )

    # Truth vs relevance — §3.1 / step 5 of the write pipeline.
    nand_present = any(
        (op.get("op_type") or "").upper() == "NAND" for op in record_doc["operators"]
    )
    if entry.get("expect_nand") is False and nand_present:
        add(
            "P1",
            "(truth-vs-relevance)",
            "a NAND was written where nothing contradicts — a contradiction "
            "operator on a claim that was never attacked computes a wrong "
            "confidence, not a missing one",
            "NAND is for a claim that is WRONG; anything else is either plain "
            "support (IMPL) or a relevance question (mitigation on the operator)",
        )
    if entry.get("expect_nand") and not nand_present:
        # ONE ACT, ONE FINDING. A contradiction answered with a dampener instead
        # of a NAND is a single mistake: charging it a P0 for the missing NAND
        # AND a second P0 for the mitigation counted one write twice in the
        # aggregate a Step-3 delta is read against (#9695 review P1). The
        # dampener is named inside this finding, and the `elif` below covers the
        # genuinely separate case (a NAND written where none belongs, or a
        # dampener on an input that has no contradiction at all).
        mitigated = record_doc["mitigations"]
        dampened = " — relevance was dampened where truth was at issue" if mitigated else ""
        operators = [o["op_type"] for o in record_doc["operators"]] or "none"
        add(
            "P0",
            "(truth-vs-relevance)",
            "a contradicting claim was filed with NO NAND against the attacked "
            f"Point (operators present: {operators}; mitigations: "
            f"{len(mitigated)}{dampened}) — truth was not attacked",
            "a contradiction attacks the TRUTH of the Point (NAND); mitigation "
            "dampens an operator's RELEVANCE (w_eff = w x (1-strength))",
        )
    # Three-state, deliberately: ABSENT means "not scored" (most inputs neither
    # need nor forbid a dampener — the research input legitimately mitigates an
    # under-evidenced inference, and a boilerplate `false` on every input turned
    # that sanctioned write into a P0). Only an explicit `false` forbids one, and
    # only when no NAND-less contradiction already accounts for the write above:
    # truth being at issue is what makes a dampener a substitute for a NAND.
    elif entry.get("expect_mitigation") is False and record_doc["mitigations"]:
        add(
            "P0",
            "(truth-vs-relevance)",
            f"{len(record_doc['mitigations'])} INVALIDATES/mitigation edge(s) written on an "
            f"input that needs none — relevance dampening applied where truth is at issue",
            "do not dampen an operator when the claim itself is wrong; NAND the Point",
        )
    if entry.get("expect_supersede") and not any(
        n["status"].lower() == "superseded" for n in nodes
    ):
        add(
            "P0",
            "(change)",
            f"expected supersession ({entry['expect_supersede']}) — no node carries "
            f"status=superseded, so 'what did we believe then' is unanswerable",
            "supersede with edge transfer (§4.1 supersede_point), never edit in place",
        )

    # Duplicate identity — the gold's own measurable for the duplicate input.
    dup_rule = entry.get("duplicate_expected")
    if dup_rule:
        # A second node for one identity means the NAME is duplicated — a
        # sentence that merely STARTS with the name ("Northwind Logistics
        # confirmed …") is a claim about it, not a second entity. So: same
        # normalised name, or the name plus a legal-form suffix ("Ltd", "Inc"),
        # and only on an entity layer. Without those two constraints the check
        # reports every claim mentioning the company as a duplicate. The
        # predicate is module level and shared with `_match`, so the two halves
        # of the instrument cannot disagree about what "one identity" means.
        same_identity = [
            n
            for n in nodes
            if n["layer"] in ("Object", "Subject")
            and _same_identity(n["text"], dup_rule["of"])
        ]
        if len(same_identity) > dup_rule["max_nodes"]:
            add(
                "P0",
                dup_rule["of"],
                f"{len(same_identity)} nodes carry one identity "
                f"({[n['text'][:40] for n in same_identity]}) — a re-mention minted "
                f"a second node, so nothing ever updates",
                "match the existing node and update it (alias on the same node)",
            )

    # Graph-wide kind census — INDEPENDENT of the gold. Score() otherwise audits
    # only the nodes the gold names, so a run can read "clean" while writing
    # undeclared kinds the gold never asked about (measured: the correction run,
    # whose two gold items both landed and whose other nodes carried undeclared
    # kinds). The census keeps "clean" honest.
    census: dict[str, list[str]] = {}
    wrong_axis: list[str] = []
    cross_axis: list[str] = []
    # The census exists to catch what the GOLD did not name, so it skips the
    # nodes a gold item already resolved to (`item_rows[*]["node"]`). Keying it
    # on kind-bearing findings instead let a gold-named node — already reported
    # as a layer P0 — contribute its kind to the census, which overstated the
    # class on two inputs.
    named_texts = {
        row.get("node") for row in item_rows if row.get("node")
    }
    for node in nodes:
        if node["is_operator"]:
            continue
        if node["text"][:80] in named_texts:
            continue
        foreign = node.get("foreign_kinds") or {}
        if foreign:
            # A kind on another layer's property is invisible to every reader of
            # this layer, so it belongs in the census that keeps "no undeclared
            # kind" honest — a single coalesce used to hide it (#9695 review P2).
            carried = ", ".join(f"{p}={v!r}" for p, v in sorted(foreign.items()))
            cross_axis.append(f"{node['layer']} {node['text'][:30]!r} carries {carried}")
            # …and it is NOT a reason to stop looking at the node's OWN kind: a
            # node can carry an undeclared own kind AND a foreign one, and the
            # item-level rule charges both (the own kind on its own property is a
            # second, separate write). The `continue` that used to sit here made
            # the census the one place that rule was inverted (#9695 review P2).
        status = kind_status(node["layer"], node["kind"])
        if status == "OTHER-AXIS":
            # The flat legacy registry accepts a kind that belongs to ANOTHER
            # axis (`meeting` on a Point). The write validator does accept it, so
            # it is not "invented" — but nothing on THIS layer reads it, and
            # "events classified as points" is the failure #9675 quotes, so it
            # must not vanish into the LEGACY bucket (#9695 review round 2, P2).
            axes = ", ".join(other_axes_declaring(node["layer"], node["kind"]))
            wrong_axis.append(
                f"{node['layer']} {node['kind']!r} is declared for {axes}"
            )
            continue
        if status != "UNDECLARED":
            continue
        census.setdefault(node["layer"], []).append(node["kind"] or "(none)")
    if census or cross_axis or wrong_axis:
        parts = []
        if census:
            parts.append(
                "; ".join(
                    f"{layer}: {sorted(set(kinds))}" for layer, kinds in census.items()
                )
            )
        if wrong_axis:
            parts.append("declared for another axis: " + "; ".join(wrong_axis))
        if cross_axis:
            parts.append("cross-axis: " + "; ".join(cross_axis))
        add(
            "P1",
            "(kinds)",
            "graph-wide: kinds no vocabulary reads on the layer they were written "
            "to, beyond the gold items — " + " | ".join(parts),
            "every node needs a kind on ITS OWN layer's property, from the core "
            "§5 vocabulary, the SDK's registered kinds, or a loaded pack",
        )

    # Kind completeness — a node with no kind on its own layer routes nowhere.
    # A node carrying a kind on the WRONG property is reported by the census
    # above (it does carry one); counting it here too would charge one write
    # twice.
    #
    # A node whose missing kind was ALREADY charged at item level (verdict
    # KIND-MISSING) is skipped for the same reason: `score()` billed that one
    # write once, with text that says what happened, and counting it again here
    # reported the same node twice — the double-charge class this PR fixed for
    # the cross-axis case, still open for the plain empty-kind case (#9695
    # review P1/P2). The skip is keyed on the CHARGE, not on "the gold named
    # it": a gold-named node whose gold declares no kind at all was never
    # charged above, so this rule must still report it rather than fail open.
    charged_missing = {
        row["node"] for row in item_rows if row.get("verdict") == "KIND-MISSING"
    }
    unkinded = [
        n
        for n in nodes
        if not n["kind"]
        and not n["is_operator"]
        and not n.get("foreign_kinds")
        and n["text"][:80] not in charged_missing
    ]
    if unkinded:
        add(
            "P1",
            "(kinds)",
            f"{len(unkinded)} node(s) carry no kind property: "
            f"{[n['text'][:30] for n in unkinded][:5]}",
            "every node needs a kind from the active vocabulary (§5)",
        )

    return {
        "input": input_id,
        "cell": entry.get("cell"),
        "confuses": entry.get("confuses", []),
        "counts": record_doc["counts"],
        "item_rows": item_rows,
        "findings": findings,
        "relations": record_doc["relations"],
        "operators": [n["op_type"] or "(none)" for n in record_doc["operators"]],
        "sources": [s["url"] or s["text"] for s in record_doc["sources"]],
        # Reported, never silently dropped: a dampener on an operator whose
        # support is thin is a SANCTIONED write (write-pipeline item 5), so it is
        # evidence for the delta even when it is not scored as a finding.
        "mitigations": [
            f"{m['relation']}: {(m.get('from') or '?')[:40]} -> {(m.get('to') or '?')[:40]}"
            for m in record_doc["mitigations"]
        ],
        "kind_census": census,
        "wrong_axis_kinds": wrong_axis,
        "cross_axis_kinds": cross_axis,
        # The vocabulary the run was scored UNDER, per kind it actually used —
        # provenance, and the diagnostic that tells a future lane WHICH change
        # moved a committed verdict (a product-side vocabulary change vs. an
        # edit to the instrument). The reproduction gate still re-scores against
        # the live tree; this map is what makes a red there name its cause
        # instead of accusing the evidence of drift (#9695 review P2).
        "kind_sources": {
            layer: {
                kind: status
                for kind, status in sorted(
                    {
                        node["kind"]: kind_status(layer, node["kind"])
                        for node in nodes
                        if node["layer"] == layer and node["kind"]
                    }.items()
                )
            }
            for layer in LAYERS
            if any(node["layer"] == layer and node["kind"] for node in nodes)
        },
        "packs": _pack_vocab()["packs"],
        "severity_counts": {
            # P2 is a RESERVED slot, not a dead rule (#9695 review round 8, P2).
            # The instrument emits only P0/P1 today — the review skills' two
            # severities that gate a merge — but every record carries the counter
            # so a future P2 rule lands in a stable schema rather than forcing
            # all ten committed artifacts to be rewritten. The doc says
            # "P0/P1 findings" accordingly.
            sev: sum(1 for f in findings if f["severity"] == sev)
            for sev in ("P0", "P1", "P2")
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="write the structural record for a graph")
    rec.add_argument("--db", required=True, help="embedded db path or docker:// URI")
    rec.add_argument("--out", default=None)

    sc = sub.add_parser("score", help="score a graph against the frozen gold")
    sc.add_argument("--db", default=None, help="embedded db path or docker:// URI")
    sc.add_argument(
        "--graph",
        default=None,
        help=(
            "re-score a COMMITTED graph.json instead of reading a database — the "
            "audit path: it must reproduce that run's score.json exactly"
        ),
    )
    sc.add_argument("--gold", default=str(DEFAULT_GOLD))
    sc.add_argument("--id", required=True, help="gold input id")
    sc.add_argument(
        "--out",
        default=None,
        help="directory: writes score.json (verdicts) and graph.json (the record read)",
    )

    args = ap.parse_args(argv)
    if args.cmd == "record":
        doc = record(args.db)
        text = json.dumps(doc, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text + "\n")
            print(f"recorded {len(doc['nodes'])} nodes / {len(doc['edges'])} edges -> {args.out}")
        else:
            print(text)
        return 0

    if bool(args.db) == bool(args.graph):
        ap.error("score needs exactly one of --db (read a graph) or --graph (re-score a record)")
    gold = json.loads(Path(args.gold).read_text())
    doc = record(args.db) if args.db else json.loads(Path(args.graph).read_text())
    result = score(doc, gold, args.id)
    text = json.dumps(result, indent=2)
    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "score.json").write_text(text + "\n")
        (out_dir / "graph.json").write_text(json.dumps(doc, indent=2) + "\n")
        print(f"scored {args.id}: P0={result['severity_counts']['P0']} "
              f"P1={result['severity_counts']['P1']} -> {out_dir}")
    print(text)
    return 1 if result["severity_counts"]["P0"] else 0


if __name__ == "__main__":
    sys.exit(main())
