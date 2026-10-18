"""Read-only census of a logical graph dump — #8882 **Phase 1**.

#8882's Phase 1 is one sentence long and it is the whole reason this module
exists:

> "**measure the noise, do not assume its signature.** Classify the population
> by *measurable* provenance, not by taste: point kind (``Point`` / ``Object`` /
> ``Subject`` / episodic), origin/source credibility field, degree (does
> anything reference it), near-duplicate density, and the extraction pathway
> that produced it. Output: counts per class, plus a proposed keep/drop
> predicate, plus a random sample a human can eyeball. The predicate is the
> deliverable — *'what's useful'* becomes a rule with numbers behind it instead
> of a guess."

That is what this module computes, and nothing else. It is **not** a classifier,
a labeler or a decision: it never drops anything, never writes to a graph, and
never ranks one node above another by judgement. It reports a partition of a
*snapshot* into field-defined classes, evaluates a named predicate over that
partition, and states each rule's independent hit count so a different
composition can be costed without re-measuring.

## Why it is a separate module from :mod:`tortoise.retype`

``retype`` (Phase 2) rewrites labels from a decisions file. This module answers
the question that *precedes* it — which nodes are worth spending a decision on —
and the two must not be conflated: a census that could also drop would make a
measurement disagreement look like a migration, and the migration's own
reconciliation ("every node exactly once") has to be counted against an
untouched input.

## Division of labour, stated the same way the issue states it

* the **predicate is the deliverable**, so it is expressed as data (``rules``,
  each with an id, the field test it applies, and the count of nodes it hits);
* the **survivor set is reported, not produced** — ``predicate.kept_by_label``
  and ``predicate.kept_knowledge_edges`` say what a re-typing pass would then
  receive, including whether the topology that makes the asset valuable
  survives the cut;
* a rule the caller did not ask for is not silently applied — ``rules=`` is an
  explicit subset, and an unknown id is refused rather than ignored.

## Measurability, and the one thing that is NOT measurable here

Every rule is a test over a property the node already carries or over the dump's
own edge list. Nothing is inferred from prose. Consequences worth stating:

* **``extracted`` is a provenance claim, not a quality claim.** It fires on a
  node that carries a pointer to the artifact it was captured from
  (``provenanceSource`` / ``extractedFrom`` property, or an outgoing
  ``extractedFrom`` edge). Whether the *extractor* was good is a different
  question — #8882's owner ruling puts the extractor fix in its own epic — and a
  good extraction from a curated document would be hit by this rule too. The
  rule says *"this node is a leaf of a capture, not an assertion an agent made"*,
  which is exactly the population the drop predicate is about.
* **``isolated`` counts KNOWLEDGE edges only.** A provenance pointer edge is not
  topology: a claim whose only edge is ``extractedFrom`` participates in nothing
  — it is a leaf of a document, and no query walks to it from a neighbouring
  claim. Counting pointer edges as degree would report every extracted leaf as
  "referenced" and hide the whole class.
* **``duplicate`` is a normalisation, not a semantic judgement.** It compares
  lower-cased, whitespace-collapsed text with trailing punctuation removed.
  Two paraphrases are two nodes to this rule; two copies of one sentence are
  one. That under-counts duplicates and never over-counts, which is the safe
  direction for a rule that proposes a DROP.
* **``fragment`` is a length threshold, and length is a proxy.** It is exposed
  as a parameter (``fragment_max_chars``) and reported with its threshold in the
  output, so a reader can re-cost the predicate at a different cut instead of
  arguing with a number whose provenance is hidden.

## Determinism (the issue's evidence bar)

> "the predicate ... reproduces the same partition twice."

Every list this module emits is sorted, every mapping is keyed by a string and
serialised with sorted keys, and no clock, no RNG and no set iteration order
reaches the output. ``report["census_sha256"]`` is a digest of the whole report
**except** the human-readable ``samples`` block, so two runs on one snapshot can
be compared with a single string — the sample is evidence for a person, the
partition is the measurement.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from tortoise.retype import KIND_PROP, RETYPE_TARGETS, node_key

#: Report format discriminator. Bumped when a key's MEANING changes, so a reader
#: (or a diff) can tell two reports' schemas apart. ``diff_census`` cannot meet
#: two CENSUS_FORMATs — both sides are produced by :func:`census` in one call — so
#: the like-for-like refusal it applies is over the DUMPS' own ``format`` /
#: ``dump_revision``, which genuinely can differ between two inputs.
CENSUS_FORMAT = "tortoise-graph-census-v1"

#: Dump-level fields that must MATCH before two dumps are like-for-like. A diff
#: across dump formats (or across revisions of one format) compares unlike
#: shapes, and its "moved" rows would attribute a schema change to the data.
DUMP_IDENTITY_FIELDS: tuple[str, ...] = ("format", "dump_revision")

#: Placeholder for an absent value. Never ``None``: a JSON ``null`` and a
#: missing key are indistinguishable to a consumer, and both must be countable.
ABSENT = "<none>"

#: Properties a node may carry a provenance claim in.
PROVENANCE_POINTER_PROPS: tuple[str, ...] = ("provenanceSource", "extractedFrom")
PROVENANCE_PROPS: tuple[str, ...] = (
    *PROVENANCE_POINTER_PROPS,
    "authoredBy",
    "origin",
    "sourceId",
    "quote",
    "content_hash",
)

#: Edge types that record WHERE a node came from. Excluded from knowledge
#: degree — see the module docstring.
PROVENANCE_EDGE_TYPES: frozenset[str] = frozenset({"extractedFrom"})

#: Rule ids. Order is the canonical evaluation order (first hit wins) and is
#: also the order they are reported in.
RULE_EXTRACTED = "extracted"
RULE_UNTYPED = "untyped"
RULE_FRAGMENT = "fragment"
RULE_DUPLICATE = "duplicate"
RULE_ISOLATED = "isolated"

DEFAULT_RULES: tuple[str, ...] = (
    RULE_EXTRACTED,
    RULE_UNTYPED,
    RULE_FRAGMENT,
    RULE_DUPLICATE,
    RULE_ISOLATED,
)

#: Why each rule proposes a drop. Each names the field it reads, because a
#: predicate a reader cannot re-derive is a taste judgement wearing a number.
RULE_REASONS: dict[str, str] = {
    RULE_EXTRACTED: (
        "carries a provenance pointer to the artifact it was captured from "
        "(provenanceSource / extractedFrom property, or an extractedFrom edge "
        "to a :Source) — a leaf of a capture, not an assertion an agent made"
    ),
    RULE_UNTYPED: (
        f"carries no kind property ({', '.join(KIND_PROP.values())}) and is not "
        "an operator — it never came through the typed write path, so nothing "
        "has ever asserted WHAT it is"
    ),
    RULE_FRAGMENT: (
        "its text is shorter than the fragment threshold — a conversational "
        "fragment with no standalone meaning"
    ),
    RULE_DUPLICATE: (
        "its normalised text duplicates another node in the graph — this rule "
        "fires on the NON-survivor, and the survivor (best knowledge degree, "
        "then all degree, then node key) is chosen BEFORE the rules run, so an "
        "earlier rule may drop the survivor too"
    ),
    RULE_ISOLATED: (
        "has no knowledge edge in either direction (IMPL / NAND / INPUT and "
        "every non-provenance type) — nothing walks to it from another claim"
    ),
}

#: Default fragment threshold, in characters. Measured on the curated graph at
#: HEAD: the non-operator content-length distribution has p5=24, p10=28,
#: p25=40, p50=64 — i.e. 40 chars is the QUARTILE, not a taste cut, and it hits
#: ~24% of non-operators. Exposed so a reader can re-cost the predicate.
FRAGMENT_MAX_CHARS = 40

#: Characters a normalised duplicate key may end with, removed.
_TRAILING = " \t\n.,;:!?\"'`)]}"

#: Excerpt length in the human-readable sample.
_EXCERPT = 140

_WS = re.compile(r"\s+")


class CensusError(RuntimeError):
    """The census cannot be produced. ``problems`` holds every one."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__(
            f"census refused ({len(self.problems)} problem(s)): "
            + "; ".join(self.problems[:10])
            + ("" if len(self.problems) <= 10 else f" (+{len(self.problems) - 10} more)")
        )


# ── node reads ──────────────────────────────────────────────────────────────


def _props(node: Mapping[str, Any]) -> dict:
    props = node.get("props")
    return props if isinstance(props, dict) else {}


def _labels(node: Mapping[str, Any]) -> list[str]:
    labels = node.get("labels")
    return [str(x) for x in labels] if isinstance(labels, list) else []


def scan_nodes(
    dump: Mapping[str, Any],
) -> tuple[list[dict], dict[str, list[int]], list[str]]:
    """Validated node rows, ``{node_key: [dump_id, ...]}``, and any problems.

    Fail-closed on STRUCTURAL corruption — a non-integer ``dump_id``, a repeated
    ``dump_id``, a non-dict prop bag, an absent or non-list label set: these make
    the measurement meaningless, because two rows would stand for one node, or a
    node's own type was lost and ``RULE_UNTYPED`` would propose dropping it.

    Deliberately NOT fail-closed on **ambiguous node keys**. ``node_key`` prefers
    ``id`` / ``eventId`` / ``url`` / ``name``, and a real graph can carry two
    distinct nodes whose human key coincides — the curated snapshot at HEAD has
    447 such pairs, all ``:Source`` records for one captured document registered
    twice (once as a ``documentKind`` record, once as a ``sourceKind`` one).
    Refusing here would make this instrument unable to measure the very snapshot
    Phase 1 exists to describe, and would hide a real finding behind an error.
    The collision is REPORTED (``identities``) instead: it is a measurement, and
    it is the reason a re-typing decisions file cannot address those nodes.
    """
    raw = dump.get("nodes")
    if not isinstance(raw, list):
        return [], {}, [f"dump has no node list ({type(raw).__name__})"]
    problems: list[str] = []
    rows: list[dict] = []
    seen: set[int] = set()
    for index, node in enumerate(raw):
        if not isinstance(node, dict) or "dump_id" not in node:
            problems.append(f"node #{index} carries no dump_id ({type(node).__name__})")
            continue
        dump_id = node["dump_id"]
        if isinstance(dump_id, bool) or not isinstance(dump_id, int):
            problems.append(
                f"node #{index} dump_id {dump_id!r} is not an integer "
                f"({type(dump_id).__name__}) — edges reference dump_id"
            )
            continue
        if dump_id in seen:
            problems.append(f"duplicate dump_id {dump_id!r} in the node list")
            continue
        seen.add(dump_id)
        # ``is not None``, NOT ``or {}``: an empty list / ``0`` / ``False`` /
        # ``""`` is a falsy NON-dict, and normalising it to ``{}`` would accept
        # corruption as "no props" — which then makes the node ``untyped`` and
        # has RULE_UNTYPED propose dropping it.
        prop_bag = node.get("props")
        if prop_bag is not None and not isinstance(prop_bag, dict):
            problems.append(
                f"node dump_id {dump_id!r} carries a non-dict prop bag "
                f"({type(prop_bag).__name__}: {prop_bag!r}) — that is CORRUPTION"
            )
            continue
        if node.get("labels") is None:
            # ``dump_graph`` always emits ``"labels": [...]`` (possibly empty), so
            # an absent/``null`` label set is a field the export LOST. Accepting it
            # as "no labels" makes the node ``untyped``, and ``RULE_UNTYPED`` then
            # proposes DROPPING it — corruption converted into a drop
            # recommendation, indistinguishable from a legitimately kind-less row.
            problems.append(
                f"node dump_id {dump_id!r} carries no label set (labels is "
                f"{type(node.get('labels')).__name__}) — that is CORRUPTION"
            )
            continue
        if not isinstance(node.get("labels"), list):
            problems.append(
                f"node dump_id {dump_id!r} carries a non-list label set "
                f"({type(node.get('labels')).__name__}) — that is CORRUPTION"
            )
            continue
        rows.append(node)
    rows.sort(key=lambda node: node["dump_id"])
    keys: dict[str, list[int]] = {}
    for node in rows:
        keys.setdefault(node_key(node), []).append(int(node["dump_id"]))
    return rows, keys, problems


def _text(props: Mapping[str, Any]) -> str:
    """The node's own statement text: ``content`` for a Point, ``name`` else."""
    for prop in ("content", "name", "subject"):
        value = props.get(prop)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def normalize_text(text: str) -> str:
    """The duplicate key: case-folded, whitespace-collapsed, no trailing punctuation."""
    return _WS.sub(" ", text.strip()).casefold().rstrip(_TRAILING)


def kind_prop_for(labels: Iterable[str]) -> str | None:
    """The kind property an epistemic label set carries, or ``None``."""
    for label in labels:
        prop = KIND_PROP.get(label)
        if prop is not None:
            return prop
    return None


def _kind_value(node: Mapping[str, Any]) -> tuple[str, str]:
    """``(kind property name or ABSENT, value or ABSENT)`` for a node."""
    labels = _labels(node)
    prop = kind_prop_for(labels)
    if prop is None:
        return ABSENT, ABSENT
    raw = _props(node).get(prop)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return prop, ABSENT
    return prop, str(raw)


def _pointer(props: Mapping[str, Any]) -> bool:
    for prop in PROVENANCE_POINTER_PROPS:
        value = props.get(prop)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (list, tuple)) and value:
            return True
    return False


def _non_empty_string(props: Mapping[str, Any], prop: str) -> str | None:
    """The property's value if it is a string with non-whitespace content, else ``None``.

    One normalisation for every "is this field present" question, so two counters
    over the same field cannot disagree: ``field_presence`` and the provenance
    ``matrix`` both read a whitespace-only value as ABSENT (a bare truthiness test
    counted ``"   "`` as an authored node in one and not the other).
    """
    raw = props.get(prop)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _is_operator(props: Mapping[str, Any]) -> bool:
    """Whether this node is an operator — the SAME test :mod:`tortoise.retype` applies.

    ``retype`` decides "operator, never move" with truthiness
    (``props.get("is_operator")``), so this module must not be stricter: a dump
    carrying a truthy non-boolean (``1``, ``"true"``) is an operator to the
    Phase-2 consumer, and a census that called it ``untyped`` would propose
    DROPPING exactly the topology ``retype`` refuses to move. The two read one
    dump contract; a divergence here fails open in the direction that loses edges.
    """
    return bool(props.get("is_operator"))


#: Every provenance class this instrument can assign, in evaluation order.
PROVENANCE_CLASSES: tuple[str, ...] = ("operator", "extracted", "untyped", "typed")


def classify_provenance(
    node: Mapping[str, Any], *, from_edge: bool = False
) -> str:
    """One named class per node, from its fields and edges only.

    Order is stated and deliberate, because each earlier class WINS:

    1. ``operator`` — a truthy ``is_operator`` (the template's ``true``), the SAME
       test :mod:`tortoise.retype` applies. Structural, never droppable, and
       checked FIRST so no provenance rule can swallow the topology.
    2. ``extracted`` — a provenance pointer (property, or an ``extractedFrom``
       edge). A leaf of a capture.
    3. ``untyped`` — no kind property. It never came through the typed write
       path, so nothing has ever asserted what it is.
    4. ``typed`` — a kind property and no pointer: the population an agent
       asserted deliberately, and the population a re-typing pass targets.
    """
    props = _props(node)
    if _is_operator(props):
        return "operator"
    if from_edge or _pointer(props):
        return "extracted"
    prop = kind_prop_for(_labels(node))
    if prop is None:
        return "untyped"
    raw = props.get(prop)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "untyped"
    return "typed"


#: Kept as the internal spelling the census loop reads.
_classify = classify_provenance


# ── edges ───────────────────────────────────────────────────────────────────


def _edge_rows(dump: Mapping[str, Any]) -> tuple[list[dict], list[str]]:
    """Validated edge rows plus any problems. Fail-closed, like ``index_dump``.

    A missing (or JSON ``null``) edge container is REFUSED, exactly as a missing
    node list is: ``hosted_backup.dump_graph`` always emits ``"edges": [...]`` and
    ``retype.apply_plan`` refuses the same shape, so its absence means the
    artifact lost it. Accepting it as "a graph with no edges" is the worst case
    for this instrument — every node has knowledge degree 0, so the default
    predicate proposes dropping every claim, while ``knowledge_edges == 0`` makes
    the topology verdict vacuously ``true`` and silently disables both warnings.
    """
    raw = dump.get("edges")
    if raw is None:
        return [], [f"dump has no edge list ({type(raw).__name__})"]
    if not isinstance(raw, list):
        return [], [f"dump edges is not a list ({type(raw).__name__})"]
    problems: list[str] = []
    rows: list[dict] = []
    for index, edge in enumerate(raw):
        if not isinstance(edge, dict):
            problems.append(f"edge #{index} is not an object ({type(edge).__name__})")
            continue
        src, dst, etype = edge.get("src"), edge.get("dst"), edge.get("type")
        for name, value in (("src", src), ("dst", dst)):
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(
                    f"edge #{index} {name} is not an integer dump_id "
                    f"({value!r}: {type(value).__name__}) — a dump edge is keyed by dump_id"
                )
        if not isinstance(etype, str) or not etype:
            problems.append(f"edge #{index} has no relationship type ({etype!r})")
        rows.append({"src": src, "dst": dst, "type": etype})
    return rows, problems


# ── census ──────────────────────────────────────────────────────────────────


def _validate_rules(rules: Sequence[str]) -> tuple[str, ...]:
    if not rules:
        raise CensusError(["no rules selected — an empty predicate keeps everything by accident"])
    unknown = [r for r in rules if r not in DEFAULT_RULES]
    if unknown:
        raise CensusError(
            [
                f"unknown rule id(s) {sorted(unknown)!r}; known ids are {list(DEFAULT_RULES)!r} "
                "— an id this instrument does not implement would silently select nothing"
            ]
        )
    if len(set(rules)) != len(rules):
        raise CensusError([f"duplicate rule id(s) in {list(rules)!r}"])
    # Canonical evaluation order, whatever order the caller named.
    return tuple(r for r in DEFAULT_RULES if r in rules)


def _stats(values: list[int]) -> dict:
    if not values:
        return {"nodes": 0, "min": 0, "median": 0, "max": 0}
    return {
        "nodes": len(values),
        "min": min(values),
        "median": round(float(statistics.median(values)), 2),
        "max": max(values),
    }


def census(
    dump: Mapping[str, Any],
    *,
    rules: Sequence[str] = DEFAULT_RULES,
    fragment_max_chars: int = FRAGMENT_MAX_CHARS,
    samples: int = 0,
) -> dict:
    """Measure a ``tortoise-logical-dump-v1`` dict. Pure; never mutates ``dump``.

    ``rules`` selects which of :data:`DEFAULT_RULES` the *proposed* predicate
    applies; every selected rule's independent hit count is reported either way,
    so an unselected rule can be costed from the same run. ``samples`` adds up to
    N dropped nodes per rule to the report for a human to eyeball (``0`` = none).
    """
    selected = _validate_rules(rules)
    if fragment_max_chars < 0:
        raise CensusError(
            [f"fragment_max_chars must be >= 0 (got {fragment_max_chars!r})"]
        )
    if samples < 0:
        raise CensusError([f"samples must be >= 0 (got {samples!r})"])

    rows, keys, problems = scan_nodes(dump)
    edges, edge_problems = _edge_rows(dump)
    problems.extend(edge_problems)
    if problems:
        raise CensusError(problems)

    # Sorted by dump_id (``scan_nodes``), so every downstream list is
    # order-independent: the same snapshot in any node order censuses the same.
    by_id = {int(node["dump_id"]): node for node in rows}
    dangling = [
        edge
        for edge in edges
        if edge["src"] not in by_id or edge["dst"] not in by_id
    ]
    if dangling:
        edge = dangling[0]
        raise CensusError(
            [
                f"{len(dangling)} edge(s) reference a dump_id the dump does not carry "
                f"(first: {edge['src']}->{edge['dst']} {edge['type']}) — a rev-2 dump "
                "cannot contain a dangling edge, so this is corruption, not a "
                "legacy shape, and a degree census over it would be wrong"
            ]
        )

    # ── degree, by edge type ────────────────────────────────────────────────
    edges_by_type: dict[str, int] = {}
    all_degree: dict[int, int] = {dump_id: 0 for dump_id in by_id}
    knowledge_degree: dict[int, int] = {dump_id: 0 for dump_id in by_id}
    pointer_edge: dict[int, bool] = {dump_id: False for dump_id in by_id}
    for edge in edges:
        etype = edge["type"]
        edges_by_type[etype] = edges_by_type.get(etype, 0) + 1
        for endpoint in (edge["src"], edge["dst"]):
            all_degree[endpoint] += 1
        if etype in PROVENANCE_EDGE_TYPES:
            pointer_edge[edge["src"]] = True
        else:
            for endpoint in (edge["src"], edge["dst"]):
                knowledge_degree[endpoint] += 1
    # ── per-node facts ─────────────────────────────────────────────────────
    label_sets: dict[str, int] = {}
    layers: dict[str, int] = {}
    kinds: dict[str, dict[str, int]] = {}
    field_presence: dict[str, int] = {prop: 0 for prop in PROVENANCE_PROPS}
    pointer_values: set[str] = set()
    matrix: dict[str, int] = {}
    classes: dict[str, int] = {}
    node_class: dict[int, str] = {}
    nodes_by_class: dict[str, list[int]] = {}
    normalised: dict[str, list[int]] = {}
    content_hash_groups: dict[str, int] = {}

    for node in rows:
        dump_id = int(node["dump_id"])
        props = _props(node)
        labels = _labels(node)

        label_key = "|".join(sorted(labels)) if labels else ABSENT
        label_sets[label_key] = label_sets.get(label_key, 0) + 1

        epistemic = [label for label in labels if label in RETYPE_TARGETS]
        if len(epistemic) == 1:
            layers[epistemic[0]] = layers.get(epistemic[0], 0) + 1
        elif len(epistemic) > 1:
            layers["multi-label"] = layers.get("multi-label", 0) + 1
        else:
            layers["non-epistemic"] = layers.get("non-epistemic", 0) + 1

        prop, value = _kind_value(node)
        if prop != ABSENT:
            bucket = kinds.setdefault(prop, {})
            bucket[value] = bucket.get(value, 0) + 1

        for provenance_prop in PROVENANCE_PROPS:
            raw = props.get(provenance_prop)
            if isinstance(raw, str) and raw.strip():
                field_presence[provenance_prop] += 1
                if provenance_prop in PROVENANCE_POINTER_PROPS:
                    pointer_values.add(raw.strip())
            elif isinstance(raw, (list, tuple)) and raw:
                field_presence[provenance_prop] += 1

        typed = prop != ABSENT and value != ABSENT
        pointer = pointer_edge[dump_id] or _pointer(props)
        authored = _non_empty_string(props, "authoredBy") is not None
        matrix_key = (
            f"typed={int(typed)} pointer={int(pointer)} authored={int(authored)}"
        )
        matrix[matrix_key] = matrix.get(matrix_key, 0) + 1

        klass = classify_provenance(node, from_edge=pointer_edge[dump_id])
        node_class[dump_id] = klass
        classes[klass] = classes.get(klass, 0) + 1
        nodes_by_class.setdefault(klass, []).append(dump_id)

        text = _text(props)
        if text:
            key = normalize_text(text)
            if key:
                normalised.setdefault(key, []).append(dump_id)
        raw_hash = props.get("content_hash")
        if isinstance(raw_hash, str) and raw_hash.strip():
            content_hash_groups[raw_hash.strip()] = (
                content_hash_groups.get(raw_hash.strip(), 0) + 1
            )

    # ── duplicates: keep the best-connected survivor ───────────────────────
    duplicate_groups = {
        key: ids for key, ids in normalised.items() if len(ids) > 1
    }
    survivors: dict[int, int] = {}  # redundant dump_id -> survivor dump_id
    for key in sorted(duplicate_groups):
        ids = sorted(
            duplicate_groups[key],
            key=lambda dump_id: (
                -knowledge_degree[dump_id],
                -all_degree[dump_id],
                node_key(by_id[dump_id]),
            ),
        )
        survivor = ids[0]
        for redundant in ids[1:]:
            survivors[redundant] = survivor
    duplicate_by_class: dict[str, int] = {}
    for redundant in survivors:
        klass = node_class[redundant]
        duplicate_by_class[klass] = duplicate_by_class.get(klass, 0) + 1

    # ── the proposed predicate ─────────────────────────────────────────────
    def hits(rule: str, dump_id: int) -> bool:
        node = by_id[dump_id]
        props = _props(node)
        if rule == RULE_EXTRACTED:
            return node_class[dump_id] == "extracted"
        if rule == RULE_UNTYPED:
            return node_class[dump_id] == "untyped"
        if rule == RULE_FRAGMENT:
            text = _text(props)
            return 0 < len(text) < fragment_max_chars
        if rule == RULE_DUPLICATE:
            return dump_id in survivors
        if rule == RULE_ISOLATED:
            return knowledge_degree[dump_id] == 0
        raise AssertionError(f"unhandled rule {rule!r}")  # pragma: no cover

    droppable = [dump_id for dump_id in by_id if node_class[dump_id] != "operator"]
    rule_hits: dict[str, int] = {}
    rule_hits_by_class: dict[str, dict[str, int]] = {}
    for rule in DEFAULT_RULES:
        rule_hits[rule] = 0
        rule_hits_by_class[rule] = {}
        for dump_id in droppable:
            if hits(rule, dump_id):
                rule_hits[rule] += 1
                klass = node_class[dump_id]
                bucket = rule_hits_by_class[rule]
                bucket[klass] = bucket.get(klass, 0) + 1

    drop_reason: dict[int, str] = {}
    for dump_id in droppable:
        for rule in selected:
            if hits(rule, dump_id):
                drop_reason[dump_id] = rule
                break
    kept = [dump_id for dump_id in by_id if dump_id not in drop_reason]
    dropped = sorted(drop_reason)

    drop_by_reason: dict[str, int] = {rule: 0 for rule in DEFAULT_RULES}
    drop_by_class: dict[str, int] = {}
    for dump_id in dropped:
        drop_by_reason[drop_reason[dump_id]] += 1
        klass = node_class[dump_id]
        drop_by_class[klass] = drop_by_class.get(klass, 0) + 1

    def label_kind_bucket(ids: Iterable[int]) -> dict[str, int]:
        bucket: dict[str, int] = {}
        for dump_id in ids:
            node = by_id[dump_id]
            labels = _labels(node)
            epistemic = [label for label in labels if label in RETYPE_TARGETS]
            label = epistemic[0] if len(epistemic) == 1 else (
                "multi-label" if epistemic else "non-epistemic"
            )
            _, value = _kind_value(node)
            key = f"{label}/{value}"
            bucket[key] = bucket.get(key, 0) + 1
        return dict(sorted(bucket.items()))

    kept_set = set(kept)
    kept_edges_by_type: dict[str, int] = {}
    for edge in edges:
        if edge["src"] in kept_set and edge["dst"] in kept_set:
            kept_edges_by_type[edge["type"]] = kept_edges_by_type.get(edge["type"], 0) + 1

    # ── what the cut does to the TOPOLOGY (the reason this report exists) ────
    # A drop predicate over a curated graph is not a filter over isolated rows:
    # the value of such a graph is in its EDGES, and an edge survives the cut
    # only if BOTH endpoints do. Reporting only node counts lets a cut that
    # severs every IMPL/NAND/INPUT link read as a clean 77% reduction.
    kept_by_type_degree: dict[str, int] = {}
    for edge in edges:
        if edge["type"] in PROVENANCE_EDGE_TYPES:
            continue
        if edge["src"] in kept_set and edge["dst"] in kept_set:
            kept_by_type_degree[edge["src"]] = kept_by_type_degree.get(edge["src"], 0) + 1
            kept_by_type_degree[edge["dst"]] = kept_by_type_degree.get(edge["dst"], 0) + 1
    knowledge_edges = [
        edge for edge in edges if edge["type"] not in PROVENANCE_EDGE_TYPES
    ]
    severed = [
        edge
        for edge in knowledge_edges
        if (edge["src"] in kept_set) != (edge["dst"] in kept_set)
    ]
    both_dropped = [
        edge
        for edge in knowledge_edges
        if edge["src"] not in kept_set and edge["dst"] not in kept_set
    ]
    told = {
        "knowledge_edges": len(knowledge_edges),
        "kept_knowledge_edges": sum(
            count for t, count in kept_edges_by_type.items() if t not in PROVENANCE_EDGE_TYPES
        ),
        "severed_knowledge_edges": len(severed),
        "both_endpoints_dropped_edges": len(both_dropped),
        "dropped_knowledge_endpoints": len(
            {
                endpoint
                for edge in knowledge_edges
                for endpoint in (edge["src"], edge["dst"])
                if endpoint not in kept_set
            }
        ),
        "kept_nodes_with_kept_knowledge_degree": len(kept_by_type_degree),
        "kept_operators_isolated_by_the_cut": sum(
            1
            for dump_id in kept
            if node_class[dump_id] == "operator" and not kept_by_type_degree.get(dump_id)
        ),
    }
    # ``retains_topology`` is a PRESERVATION verdict: it is true only when EVERY
    # knowledge edge survives the cut. `kept > 0` labelled a cut that severs
    # three of every four knowledge edges the same as one that severs none, and
    # `severed == 0` stopped one step short — an edge whose BOTH endpoints are
    # dropped is in neither ``kept`` nor ``severed``, so that form reported
    # "topology preserved" for a cut that destroyed half the edges. This field is
    # the verdict column an owner picks a rule from, so it reads
    # ``kept == knowledge_edges`` (vacuously true for an edge-free graph).
    # ``kept_knowledge_edges`` / ``severed_knowledge_edges`` /
    # ``both_endpoints_dropped_edges`` still carry the counts.
    told["retains_topology"] = told["kept_knowledge_edges"] == told["knowledge_edges"]
    told["kept_knowledge_edge_fraction"] = (
        round(told["kept_knowledge_edges"] / told["knowledge_edges"], 4)
        if told["knowledge_edges"]
        else 1.0
    )

    degree_by_class = {
        klass: _stats([knowledge_degree[i] for i in ids])
        for klass, ids in sorted(nodes_by_class.items())
    }
    isolated_by_class = {
        klass: sum(1 for i in ids if knowledge_degree[i] == 0)
        for klass, ids in sorted(nodes_by_class.items())
    }

    sample_block: dict[str, list[dict]] = {}
    if samples:
        for rule in selected:
            members = sorted(
                (dump_id for dump_id in dropped if drop_reason[dump_id] == rule),
                key=lambda dump_id: hashlib.sha256(
                    node_key(by_id[dump_id]).encode("utf-8")
                ).hexdigest(),
            )[:samples]
            sample_block[rule] = [
                {
                    "key": node_key(by_id[dump_id]),
                    "class": node_class[dump_id],
                    "labels": sorted(_labels(by_id[dump_id])),
                    "kind": _kind_value(by_id[dump_id])[1],
                    "knowledge_degree": knowledge_degree[dump_id],
                    "content": _text(_props(by_id[dump_id]))[:_EXCERPT],
                }
                for dump_id in members
            ]

    declared_nodes = dump.get("node_count")
    duplicate_key_groups = {
        key: ids for key, ids in keys.items() if len(ids) > 1
    }
    duplicate_key_label_sets: dict[str, int] = {}
    for ids in duplicate_key_groups.values():
        for dump_id in ids:
            label_set = "|".join(sorted(_labels(by_id[dump_id]))) or ABSENT
            duplicate_key_label_sets[label_set] = (
                duplicate_key_label_sets.get(label_set, 0) + 1
            )
    declared_edges = dump.get("edge_count")
    report: dict[str, Any] = {
        "format": CENSUS_FORMAT,
        "snapshot": {
            "dump_format": str(dump.get("format") or ABSENT),
            "dump_revision": dump.get("dump_revision", ABSENT),
            "graph_name": str(dump.get("graph_name") or ABSENT),
            "dumped_at": str(dump.get("dumped_at") or ABSENT),
            "declared_node_count": declared_nodes if isinstance(declared_nodes, int) else ABSENT,
            "counted_node_count": len(rows),
            "declared_edge_count": declared_edges if isinstance(declared_edges, int) else ABSENT,
            "counted_edge_count": len(edges),
        },
        "totals": {"nodes": len(rows), "edges": len(edges)},
        "identities": {
            "node_keys": len(keys),
            "duplicate_key_groups": len(duplicate_key_groups),
            "duplicate_key_nodes": sum(len(ids) for ids in duplicate_key_groups.values()),
            "duplicate_key_by_label_set": dict(sorted(duplicate_key_label_sets.items())),
            "examples": sorted(duplicate_key_groups)[:10],
        },
        "edges_by_type": dict(sorted(edges_by_type.items())),
        "label_sets": dict(sorted(label_sets.items())),
        "layers": dict(sorted(layers.items())),
        "kinds": {prop: dict(sorted(bucket.items())) for prop, bucket in sorted(kinds.items())},
        "provenance": {
            "fields": dict(sorted(field_presence.items())),
            "distinct_pointer_values": len(pointer_values),
            "pointer_edges": sum(
                count for etype, count in edges_by_type.items() if etype in PROVENANCE_EDGE_TYPES
            ),
            "matrix": dict(sorted(matrix.items())),
            "classes": dict(sorted(classes.items())),
        },
        "degree": {
            "isolated_all_edges": sum(1 for dump_id in by_id if all_degree[dump_id] == 0),
            "isolated_knowledge": sum(
                1 for dump_id in by_id if knowledge_degree[dump_id] == 0
            ),
            "by_class": degree_by_class,
            "isolated_by_class": isolated_by_class,
        },
        "duplicates": {
            "groups": len(duplicate_groups),
            "redundant_nodes": len(survivors),
            "largest_group": max(
                (len(ids) for ids in duplicate_groups.values()), default=0
            ),
            "redundant_by_class": dict(sorted(duplicate_by_class.items())),
            "content_hash_groups": len(
                [count for count in content_hash_groups.values() if count > 1]
            ),
        },
        "predicate": {
            "rules_selected": list(selected),
            "rules_available": list(DEFAULT_RULES),
            "fragment_max_chars": fragment_max_chars,
            "rules": [
                {
                    "id": rule,
                    "reason": RULE_REASONS[rule],
                    "selected": rule in selected,
                    "hits": rule_hits[rule],
                    "hits_by_class": dict(sorted(rule_hits_by_class[rule].items())),
                }
                for rule in DEFAULT_RULES
            ],
            "droppable": len(droppable),
            "drop": len(dropped),
            "keep": len(kept),
            "drop_by_reason": dict(sorted(drop_by_reason.items())),
            "drop_by_class": dict(sorted(drop_by_class.items())),
            "kept_by_label": label_kind_bucket(kept),
            "dropped_by_label": label_kind_bucket(dropped),
            "kept_edges_by_type": dict(sorted(kept_edges_by_type.items())),
            "topology": told,
            "operators_kept": classes.get("operator", 0),
        },
    }
    warnings: list[str] = []
    if not told["kept_knowledge_edges"] and told["knowledge_edges"]:
        warnings.append(
            f"the proposed keep-set retains NONE of the {told['knowledge_edges']} "
            "knowledge edges — this cut severs every IMPL/NAND/INPUT link, so the "
            "operator topology that makes the graph valuable does not survive it"
        )
    if told["severed_knowledge_edges"]:
        warnings.append(
            f"{told['severed_knowledge_edges']} knowledge edge(s) have an endpoint "
            "the predicate drops — retains_topology is false"
        )
    if told["both_endpoints_dropped_edges"]:
        warnings.append(
            f"{told['both_endpoints_dropped_edges']} knowledge edge(s) lose BOTH "
            "endpoints to the predicate — neither endpoint is 'severed', so only "
            "this count reports them; retains_topology is false"
        )
    if duplicate_key_groups:
        warnings.append(
            f"{len(duplicate_key_groups)} node key(s) are shared by more than one node "
            f"({len(duplicate_key_label_sets)} distinct label sets) — a decisions file "
            "cannot address those nodes unambiguously"
        )
    # The truncation signature. ``dump_graph`` refuses a torn read by construction
    # (``node_count == source_node_count``), so a dump whose own declared counts
    # disagree with what it carries is a PARTIAL artifact — and a dropped subgraph
    # leaves no dangling edge, so none of the refusals above can see it. Measured
    # silently, it produces a report whose counts describe a smaller population
    # than the re-typing pass will consume.
    for half, declared, counted in (
        ("node", declared_nodes, len(rows)),
        ("edge", declared_edges, len(edges)),
    ):
        if declared is ABSENT or declared == counted:
            continue
        if isinstance(declared, bool) or not isinstance(declared, int):
            continue
        warnings.append(
            f"the dump declares {declared} {half}(s) but carries {counted} — a "
            "declared-vs-counted disagreement is the signature of a TRUNCATED "
            "artifact, so this census measured a population the dump does not "
            "claim to describe"
        )
    if warnings:
        report["warnings"] = warnings
    if samples:
        report["samples"] = sample_block

    body = {k: v for k, v in report.items() if k != "samples"}
    report["census_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return report


# ── diff (the plan's own "and then the diff") ───────────────────────────────


def flatten_counts(value: Any, prefix: str = "") -> dict[str, int | float]:
    """``{dotted.path: number}`` for every number in a report. Deterministic.

    Booleans are NUMBERS here (``0`` / ``1``), not skipped: ``retains_topology``
    is the report's headline verdict, and a diff that dropped it would report
    "nothing moved" when the topology verdict flipped between two graphs — the
    one comparison this instrument's plan exists to make.

    FLOATS are numbers too (``kept_knowledge_edge_fraction``,
    ``degree.by_class.*.median``). An ``int``-only branch measured the report's
    two headline magnitudes as absent, so a diff between two graphs whose
    operator median degree moved 4 → 1 reported "nothing moved".
    """
    out: dict[str, int | float] = {}
    if isinstance(value, bool):
        if prefix:
            out[prefix] = int(value)
        return out
    if isinstance(value, (int, float)):
        out[prefix] = value
        return out
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            out.update(flatten_counts(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(value, list):
        for index, item in enumerate(value):
            out.update(flatten_counts(item, f"{prefix}[{index}]"))
    return out


def diff_census(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    rules: Sequence[str] = DEFAULT_RULES,
    fragment_max_chars: int = FRAGMENT_MAX_CHARS,
) -> dict:
    """Census two dumps and report every count that MOVED.

    The plan asks for exactly this — "a per-label / per-``pointKind`` /
    per-``origin`` census of the snapshot and the same census over the hosted
    graph, then the diff" — because a class that exists in one graph and not the
    other is the only mechanical evidence of what a retire would discard.
    """
    # Like-for-like is a property of the DUMPS, not of the reports: both reports
    # are stamped by this module's own CENSUS_FORMAT in this very call, so a
    # report-format comparison can never disagree (it used to look like a guard
    # and guarded nothing). Format/revision CAN differ between two inputs, and a
    # diff across them would attribute a schema change to the data.
    unlike = [
        field
        for field in DUMP_IDENTITY_FIELDS
        if before.get(field) != after.get(field)
    ]
    if unlike:
        raise CensusError(
            [
                "cannot diff two unlike instruments — "
                + "; ".join(
                    f"{field}: {before.get(field)!r} vs {after.get(field)!r}"
                    for field in unlike
                )
                + f" (a diff across dump {'/'.join(unlike)} compares different "
                "shapes and would report the schema change as moved data)"
            ]
        )
    before_report = census(before, rules=rules, fragment_max_chars=fragment_max_chars)
    after_report = census(after, rules=rules, fragment_max_chars=fragment_max_chars)
    left = flatten_counts(before_report)
    right = flatten_counts(after_report)
    moved: dict[str, dict[str, int | float]] = {}
    for path in sorted(set(left) | set(right)):
        a, b = left.get(path, 0), right.get(path, 0)
        if a != b:
            moved[path] = {"before": a, "after": b, "delta": b - a}
    # Non-numeric rows that moved: the snapshot's own labels. Reported, not
    # refused — a local snapshot and a hosted graph legitimately carry different
    # ``graph_name`` s, and that IS the drift a reader must see.
    moved_labels: dict[str, dict[str, str]] = {}
    for field in ("dump_format", "graph_name"):
        before_value = before_report["snapshot"][field]
        after_value = after_report["snapshot"][field]
        if before_value != after_value:
            moved_labels[f"snapshot.{field}"] = {
                "before": str(before_value),
                "after": str(after_value),
            }
    return {
        "format": f"{CENSUS_FORMAT}+diff",
        "moved": moved,
        "moved_labels": moved_labels,
        "changed_paths": len(moved) + len(moved_labels),
        "before_sha256": before_report["census_sha256"],
        "after_sha256": after_report["census_sha256"],
    }
