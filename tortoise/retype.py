"""Offline re-typing of a logical graph dump — #8882 Phase 2 *mechanics*.

The curated knowledge lives in a LOCAL graph the extractor never touched: agents
hand-authored it, so it is well connected, and its one real defect is that
everything was flattened into ``:Point``. #8882's migration therefore does not
filter the hosted byproduct — it re-models the curated graph on the way in:
*what is an Object / Subject / Event becomes one; only genuine statements stay
Points*. That decision is a CLASSIFICATION, and this module deliberately does
**not** make it.

## Division of labour (the issue's own words)

> "the model should label and the script should only do mechanics (batch,
> validate, assert a partition invariant: every node exactly once, counts
> reconcile to the total)"

So:

* the **labels** arrive from outside (``decisions`` — one entry per node, a
  model's or a human's call, reviewable as a file);
* this module does the **mechanics**: rewrite the node's label set, relocate
  the kind property, satisfy the target layer's identity contract, drop props
  that are provably wrong for the new layer, and REFUSE anything it cannot
  prove — a partition hole, an off-vocabulary kind, a multi-label node, an
  operator re-typed away from ``:Point``.
* ``propose()`` emits a starter decisions file for review. Its rules are the
  ONTOLOGY's own stated kind→layer relations (§5 ``docs/ONTOLOGY.md``), never a
  guess, and each one carries its citation; a node no rule covers is proposed
  as ``needs-decision`` rather than silently defaulted.

## Why offline, and what "offline" means here

Both entry points are pure functions over a ``tortoise-logical-dump-v1`` dict
(``tortoise.hosted_backup.dump_graph``): no graph connection, no writes, no
clock. The same dump in produces the same dump out, byte for byte — so a wrong
pass costs a re-run, never a repair (the property #8882 relies on when it puts
re-typing on the snapshot instead of on the live graph).

``dump_id`` — the dump's own node identity, which edges reference — is PRESERVED
for every node, so every edge survives untouched (``restore_graph`` links edges
by ``dump_id``). Only the label set and the property bag of a moved node change.

Relationship to ``tortoise/migrate_kinds.py`` (not a duplicate, not a
replacement): that tool ``SET``s a kind PROPERTY in place over a live graph and
its ``_do_migrate`` derives the label from a fixed table, so it cannot relocate
a node between labels — its own comments say so ("deciding point→object
relocation is … follow-up"). This module only rewrites a SNAPSHOT, and its whole
job is the layer relocation the other one cannot do.

## Fail-closed invariants (each raises :class:`RetypeError` with every problem)

1. **Partition.** Every node in the dump has exactly one decision; no unknown
   keys, no duplicates.
2. **Only ``:Point`` moves.** The insight is that the curated graph flattened
   everything into ``:Point``. A node already typed ``:Object`` / ``:Subject`` /
   ``:Event`` is moved by nothing here — it is already what it is.
3. **One label among the four.** A multi-label node (``:Point:Object``) has no
   single layer to re-type *from*; it needs a decision, not a rewrite.
4. **Kind legality.** The target kind must exist in the target layer's
   vocabulary (``CANONICAL_OBJECT_KINDS`` / ``CORE_POINT_KINDS`` /
   ``EVENT_KINDS`` / ``subject_binding.subject_kind_names``). Minting an
   off-vocabulary kind is a live defect class (#5202 hosted door persists
   ``objectKind`` verbatim; #5475 the approval set is writable through it) —
   this refuses to be another door. An EMPTY vocabulary means *nothing is
   legal*, never *anything goes*: a kind outside the declared set has to be
   named explicitly through ``extra_kinds`` (pack kinds have no
   installed-namespace context offline).
5. **Operator nodes stay ``:Point``.** ONTOLOGY §5: "*the operator node itself
   stays* ``:Point {is_operator:true}``; only its children and targets may be
   any of the four labels". A decision that moves an operator is refused.
6. **The target layer's identity contract.** ``:Event`` is keyed on
   ``eventId`` (``_upsert_event``'s MERGE key) while ``:Point``/``:Object``/
   ``:Subject`` are keyed on ``id``; ``:Object``/``:Subject`` merge on a
   writable ``name`` and an ``:Event``'s FULLTEXT index is ``(subject, name)``,
   so a name-less node is unaddressable or unsearchable in all three.
   ``eventId`` is derived mechanically (from ``id``); a ``name`` is NOT
   invented — it must be supplied, because naming a thing is the judgement the
   mechanics do not have. A name is also **unique among the nodes that end the
   plan** in a name-MERGEed layer (``:Object``/``:Subject``, #7369): two nodes
   sharing one name are ONE identity to the write path — which is why the
   collision is refused even when neither node is moved by the plan, and for a
   multi-label node under EVERY label it keeps (a ``keep`` preserves the label
   set verbatim, so a ``:Point:Object`` node the plan cannot move is still an
   ``:Object`` afterwards).
7. **Counts reconcile, against a BOUND snapshot.** Nodes and edges in == out;
   the output's id set is compared against the ids the PLAN declares (the moves'
   ``dump_id``s plus the ids ``keeps`` names), not against the input it was
   built from; the moves and the keeps sum to the node count; every edge
   endpoint is proved to exist in the output (``edges_preserved`` is asserted,
   not restated). And the plan carries a fingerprint of the dump it was
   validated against (``RetypePlan.dump_sha256``: each node's ``dump_id`` +
   identity + labels + kind + ``name`` + ``is_operator``), which ``apply_plan``
   recomputes and refuses on mismatch, while matching moves by *identity* and
   not only by position. ``dump_id`` is the
   store's INTERNAL id — positional and reused after deletions — so a refreshed
   snapshot of the same size maps the same ids onto different nodes; without the
   binding the run would apply A's kinds and names onto B's nodes and still
   report clean.

## Props this module drops, and why

* every other layer's kind property — a lingering ``pointKind`` on an
  ``:Object`` is the stale-property class #8872 / #8166 report for status;
* ``is_episodic`` on an ``:Object`` / ``:Subject`` (a Point's episodic flag is
  meaningless on a state object);
* ``embedding`` on an ``:Object`` / ``:Subject``, **by default**: both layers
  encode their vector FROM ``name`` (#7845 — ``_upsert_object`` and
  ``_upsert_subject`` both call ``encode_for_store(name)``), so a migrated
  content-derived vector is a vector derived from text the node no longer holds
  — exactly the #8419 defect. Dropping it is visible in the report; ``keep
  _embedding=True`` opts out for a caller who would rather have a stale-but-
  findable vector, and a re-embed pass after load is the documented follow-up.

The report also counts moved nodes that are endpoints of epistemic edges
(``IMPL`` / ``NAND`` / ``MITIGATES``). That is legal — #7813's owner ruling
allows all four labels as endpoints — but the residual ``:Point``-only EP reads
(#7927) will no longer see them, so the number is surfaced rather than assumed
away.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tortoise.commit_schema import CORE_POINT_KINDS, EVENT_KINDS
from tortoise.pack_registry import CANONICAL_OBJECT_KINDS

#: The four labels a curated ``:Point`` may legitimately become. ``:Subject`` is
#: included because ONTOLOGY §1 puts subjects in the same closed set; ``Source``
#: and ``Tag`` are deliberately absent — a document is a ``:Source`` (D10) and a
#: Tag is not a layer a flattened Point was hiding.
RETYPE_TARGETS: tuple[str, ...] = ("Point", "Object", "Subject", "Event")

#: The single kind property each layer carries (ONTOLOGY §4 property tables).
KIND_PROP: dict[str, str] = {
    "Point": "pointKind",
    "Object": "objectKind",
    "Subject": "subjectKind",
    "Event": "eventKind",
}

#: Each layer's identity property (``projection._CANONICAL_ENTITY_ID_PROPS``).
#: ``:Point``/``:Object``/``:Subject`` are keyed on ``id``; ``:Event`` on
#: ``eventId`` (``_upsert_event``'s MERGE key).
IDENTITY_PROP: dict[str, str] = {
    "Point": "id",
    "Object": "id",
    "Subject": "id",
    "Event": "eventId",
}

#: Layers that are unaddressable without a ``name``. ``:Object``/``:Subject``
#: MERGE on its name (#7369); an ``:Event``'s FULLTEXT index is ``(subject,
#: name)`` (``projection`` FULLTEXT label fields), so a name-less Event is
#: findable by id and invisible to the Event search leg. ``:Point`` is absent
#: on purpose: a Point's searchable text IS its ``content``.
NAME_REQUIRED_TARGETS: frozenset[str] = frozenset({"Object", "Subject", "Event"})

#: Layers whose stored vector is encoded FROM ``name`` (#7845).
NAME_EMBEDDED_TARGETS: frozenset[str] = frozenset({"Object", "Subject"})

#: Layers whose write path MERGEs on ``name`` (#7369) — two nodes sharing one
#: name are ONE identity in the target graph, so a name must be unique among
#: them. ``:Event`` is deliberately absent: ``_upsert_event`` MERGEs on
#: ``eventId``, so a duplicated Event name is search ambiguity (its FULLTEXT
#: index is ``(subject, name)``), not an identity break.
NAME_MERGE_TARGETS: frozenset[str] = frozenset({"Object", "Subject"})

#: Why a name is required, per layer — the refusal says which mechanism bites.
NAME_REQUIRED_REASON: dict[str, str] = {
    "Object": "a :Object MERGEs on its name (#7369)",
    "Subject": "a :Subject MERGEs on its name (#7369)",
    "Event": "the Event FULLTEXT index is (subject, name) — without one it is "
    "findable by eventId and invisible to the Event search leg",
}

#: Operator / epistemic edge types (the EP engine's vocabulary).
EPISTEMIC_EDGE_TYPES: frozenset[str] = frozenset({"IMPL", "NAND", "MITIGATES"})

KEEP = "keep"
NEEDS_DECISION = "needs-decision"

#: ONTOLOGY §5's STATED kind → layer relations (``docs/ONTOLOGY.md``, the
#: "Point Kind Vocabulary" + "State-centric alignment (2026-08-12)" block).
#: Each row is ``(source pointKind, target label, target kind, citation)``.
#:
#: Deliberately absent: ``vision``. ONTOLOGY's prose lists it with the STATE
#: kinds that become Objects ("vision/strategy/plan/goal/target are STATE kinds
#: → Object kinds"), but ``CANONICAL_OBJECT_KINDS`` — the set the ontology's own
#: §5 object table enumerates — has no ``vision`` member, so there is no legal
#: target kind to assign. A rule here would mint an off-vocabulary
#: ``objectKind: vision``; instead ``propose()`` reports it as needing a
#: decision and names the divergence.
ONTOLOGY_LAYER_RULES: tuple[tuple[str, str, str, str], ...] = (
    (
        "decision",
        "Event",
        "decision",
        "ONTOLOGY §5: `decision` is a TIMELINE kind → Event {eventKind:'decision'}",
    ),
    (
        "humanApproval",
        "Event",
        "humanApproval",
        "ONTOLOGY §5: `humanApproval` is a TIMELINE kind → Event",
    ),
    (
        "event",
        "Event",
        "occurrence",
        "ONTOLOGY §5: legacy pointKind `event` is removed — episodic records are "
        "Event nodes with eventKind `occurrence`/`turn`",
    ),
    ("strategy", "Object", "strategy", "ONTOLOGY §5: `strategy` is a STATE kind → Object kind"),
    ("plan", "Object", "plan", "ONTOLOGY §5: `plan` is a STATE kind → Object kind"),
    ("goal", "Object", "goal", "ONTOLOGY §5: `goal` is a STATE kind → Object kind"),
    ("target", "Object", "target", "ONTOLOGY §5: `target` is a STATE kind → Object kind"),
    (
        "observation",
        "Point",
        "statement",
        "ONTOLOGY §5: `observation` is folded into the logic layer's one kind, `statement`",
    ),
    (
        "hypothesis",
        "Point",
        "statement",
        "ONTOLOGY §5: `hypothesis` is folded into CONFIDENCE semantics; the kind is `statement`",
    ),
    ("statement", KEEP, "", "ONTOLOGY §5: `statement` IS the logic layer — nothing to re-type"),
)

_RULES_BY_KIND: dict[str, tuple[str, str, str]] = {
    src: (label, kind, why) for src, label, kind, why in ONTOLOGY_LAYER_RULES
}

#: Name proposal length cap — a Point's first line, not a summary.
NAME_PROPOSAL_MAX = 120


class RetypeError(RuntimeError):
    """The plan is not mechanically producible. ``problems`` holds every one."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__(
            f"re-typing refused ({len(self.problems)} problem(s)): "
            + "; ".join(self.problems[:10])
            + ("" if len(self.problems) <= 10 else f" (+{len(self.problems) - 10} more)")
        )


@dataclass(frozen=True)
class Decision:
    """One node's classification. ``label is None`` = not decided yet."""

    key: str
    label: str | None
    kind: str | None = None
    name: str | None = None
    rule: str | None = None  # proposal provenance only; ignored on apply

    @property
    def needs_decision(self) -> bool:
        return self.label is None or self.label == NEEDS_DECISION


@dataclass(frozen=True)
class Move:
    """One validated node rewrite."""

    key: str
    dump_id: int
    src_label: str
    dst_label: str
    kind: str
    name: str | None = None
    derives_event_id: bool = False
    kind_only: bool = False


@dataclass(frozen=True)
class RetypePlan:
    """A validated, total plan: every node is in exactly one of the two maps."""

    moves: tuple[Move, ...]
    keeps: tuple[str, ...]
    decisions_sha256: str
    #: The snapshot this plan was validated against (``_dump_fingerprint``).
    #: ``apply_plan`` recomputes it and refuses a mismatch — an empty value is
    #: an unbound plan and is refused too (fail closed).
    dump_sha256: str = ""


# ── dump access ─────────────────────────────────────────────────────────────


def _props(node: Mapping[str, Any]) -> dict:
    props = node.get("props")
    return props if isinstance(props, dict) else {}


def _labels(node: Mapping[str, Any]) -> list[str]:
    labels = node.get("labels")
    return [str(x) for x in labels] if isinstance(labels, list) else []


def node_key(node: Mapping[str, Any]) -> str:
    """The reviewable key of a dump node: its own identity, else its dump id."""
    props = _props(node)
    for prop in ("id", "eventId", "url", "name"):
        value = props.get(prop)
        if isinstance(value, str) and value.strip():
            return value
    return f"dump_id:{node.get('dump_id')}"


def _dump_fingerprint(dump: Mapping[str, Any]) -> str:
    """sha256 over each node's identity AND the props the invariants decide from.

    ``dump_id`` is the store's INTERNAL id (``hosted_backup.dump_graph`` emits
    ``int(internal_id)``), i.e. POSITIONAL and reused after deletions. Bound
    only by the id set, a plan built for one snapshot applies to a refreshed
    snapshot of the same size that names different nodes — it would write A's
    kinds and NAMES onto B's nodes and every count would reconcile. Pairing the
    id with the node's own identity makes that rotation a mismatch instead.

    The identity is not enough on its own: ``node_key`` returns ``props['id']``
    whenever a node has one, so a normal ``:Object``'s ``name`` and any node's
    ``is_operator`` never reach the identity — yet invariant 5 is decided from
    ``is_operator`` at build time and invariant 8's uniqueness from a KEPT
    node's ``name``. Both are hashed explicitly, so a refreshed dump that
    differs ONLY in one of them is a mismatch rather than a stale plan applied
    to a node that has become an operator (or has taken a name a move claims).
    """
    nodes = dump.get("nodes")
    rows: list[list[str]] = []
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                rows.append(["<malformed>", repr(node)])
                continue
            props = _props(node)
            rows.append(
                [
                    str(node.get("dump_id")),
                    node_key(node),
                    ",".join(sorted(_labels(node))),
                    *(str(props.get(prop) or "") for prop in KIND_PROP.values()),
                    str(props.get("name") or ""),
                    str(props.get("is_operator") or ""),
                ]
            )
    rows.sort()
    blob = json.dumps(rows, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def index_dump(dump: Mapping[str, Any]) -> dict[str, dict]:
    """``{node_key: node}`` for every node, refusing an ambiguous index."""
    nodes = dump.get("nodes")
    if not isinstance(nodes, list):
        raise RetypeError([f"dump has no node list ({type(nodes).__name__})"])
    problems: list[str] = []
    index: dict[str, dict] = {}
    seen_dump_ids: set[int] = set()
    for node in nodes:
        if not isinstance(node, dict) or "dump_id" not in node:
            problems.append(f"malformed node row (no dump_id): {node!r:.80}")
            continue
        dump_id = node["dump_id"]
        # The id's TYPE is part of the same contract: ``restore_graph`` coerces
        # with ``_coerce_dump_id`` (hosted_backup, #3895 review P1), which refuses
        # a non-int/bool. A bare ``int()`` here would coerce ``"3"`` (and
        # ``True`` → ``1``), report a clean run, and hand Phase 4 a dump the
        # loader then refuses.
        if isinstance(dump_id, bool) or not isinstance(dump_id, int):
            problems.append(
                f"node dump_id {dump_id!r} is not an integer id "
                f"({type(dump_id).__name__}) — restore_graph's _coerce_dump_id "
                "calls that corruption"
            )
            continue
        if dump_id in seen_dump_ids:
            problems.append(f"duplicate dump_id {dump_id!r} in node list")
        seen_dump_ids.add(dump_id)
        # Container types are part of the fail-closed contract — the same two
        # checks ``restore_graph`` makes (hosted_backup, #3895 review P1). A
        # non-dict prop bag normalised to ``{}`` would emit a property-less node
        # and report a clean run, HIDING from the loader the corruption it would
        # have refused; a non-list label set is the same class.
        raw_props = node.get("props") or {}
        if not isinstance(raw_props, dict):
            problems.append(
                f"node dump_id {dump_id!r} carries a non-dict prop bag "
                f"({type(raw_props).__name__}: {raw_props!r:.60}) — restore_graph "
                "calls that CORRUPTION; it is not an empty bag to normalise"
            )
            continue
        if not isinstance(node.get("labels") or [], list):
            problems.append(
                f"node dump_id {dump_id!r} carries a non-list label set "
                f"({type(node.get('labels')).__name__}: {node.get('labels')!r:.60}) — "
                "restore_graph calls that CORRUPTION"
            )
            continue
        key = node_key(node)
        if key in index:
            problems.append(
                f"two nodes share the key {key!r} (dump_id {index[key]['dump_id']} "
                f"and {dump_id}) — a re-typing decision could not address either "
                "unambiguously"
            )
            continue
        index[key] = node
    if problems:
        raise RetypeError(problems)
    return index


def _current_label(node: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    """``(single epistemic label or None, ALL labels)`` for a dump node."""
    all_labels = _labels(node)
    epistemic = [x for x in all_labels if x in RETYPE_TARGETS]
    return (epistemic[0] if len(epistemic) == 1 else None), epistemic


# ── vocabulary ──────────────────────────────────────────────────────────────


def _subject_kind_vocab() -> frozenset[str]:
    """The declared §5 Subject kinds, in the forms the LIVE gate accepts.

    ``subject_binding.subject_kind_names()`` IS the constant ONTOLOGY §5's prose
    describes (the keys of ``extractor_v2.SUBJECTS``, drift-pinned by
    ``tests/test_subject_binding_1370.py``) and ``is_subject_kind()`` is the gate
    on the live write doors (``sdk.py`` / ``hosted_api.py``). Seeding from the
    same declaration keeps this offline tool from being a second, looser door:
    the forms accepted here are exactly the forms ``is_subject_kind`` accepts —
    a declared key (``core:team``) or, only for a ``core:`` key, its bare form
    (``team``, which is what the extractor and the fixture store). ``other`` is
    deliberately absent: it is the fail-closed NIL bucket, not an identity.

    Imported lazily (as ``subject_binding`` itself does) so the mechanics module
    stays importable without dragging the extractor's declaration in.
    """
    from tortoise.subject_binding import subject_kind_names

    declared = subject_kind_names()
    forms = set(declared)
    forms.update(k.split(":", 1)[1] for k in declared if k.startswith("core:"))
    return frozenset(forms)


def kind_vocab(
    extra_kinds: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, frozenset[str]]:
    """Legal kind values per target layer.

    Core sets are the ontology's own (``pack_registry`` / ``commit_schema`` /
    ``subject_binding``). Pack-declared kinds are NOT resolved: offline there is
    no graph, so no installed-namespace gate to compile against — a pack kind
    must be named explicitly through ``extra_kinds``.
    """
    vocab: dict[str, frozenset[str]] = {
        "Point": frozenset(CORE_POINT_KINDS),
        "Object": frozenset(CANONICAL_OBJECT_KINDS),
        "Subject": _subject_kind_vocab(),
        "Event": frozenset(EVENT_KINDS),
    }
    for label, kinds in (extra_kinds or {}).items():
        if label not in vocab:
            raise RetypeError([f"extra_kinds names unknown layer {label!r}"])
        vocab[label] = vocab[label] | frozenset(str(k) for k in kinds)
    return vocab


# ── proposal (the labeler's starting point) ─────────────────────────────────


def _proposed_name(node: Mapping[str, Any]) -> str | None:
    """A Point's first line, capped — a PROPOSAL, never applied silently."""
    props = _props(node)
    name = props.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    content = props.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    first = content.strip().splitlines()[0].strip()
    if len(first) > NAME_PROPOSAL_MAX:
        first = first[:NAME_PROPOSAL_MAX].rstrip()
    return first or None


def propose(dump: Mapping[str, Any]) -> list[dict]:
    """Emit a starter decisions file: every node, rules cited, gaps named.

    Deterministic (sorted by key) so two runs on one snapshot produce the same
    file — the #8882 evidence bar asks the partition to "reproduce twice".
    """
    index = index_dump(dump)
    proposals: list[dict] = []
    for key in sorted(index):
        node = index[key]
        props = _props(node)
        label, epistemic = _current_label(node)
        row: dict[str, Any] = {"key": key}
        if len(epistemic) > 1:
            row.update(
                {
                    "label": NEEDS_DECISION,
                    "why": f"multi-label node {epistemic} — no single layer to re-type from",
                }
            )
        elif label is None:
            row.update(
                {
                    "label": KEEP,
                    "why": "not an epistemic layer node (:Source/:Tag/"
                    "bookkeeping) — nothing to re-type",
                }
            )
        elif label != "Point":
            row.update({"label": KEEP, "why": f"already typed :{label} — not a flattened Point"})
        elif props.get("is_operator"):
            row.update(
                {
                    "label": KEEP,
                    "why": "operator node stays :Point {is_operator:true} (ONTOLOGY §5)",
                }
            )
        else:
            point_kind = props.get("pointKind")
            rule = _RULES_BY_KIND.get(str(point_kind)) if point_kind else None
            if rule is None:
                why = (
                    f"pointKind {point_kind!r} has no ONTOLOGY §5 layer rule"
                    if point_kind
                    else "node carries no pointKind"
                )
                if point_kind == "vision":
                    why = (
                        "ONTOLOGY §5 prose puts `vision` with the STATE kinds "
                        "that become Objects, but CANONICAL_OBJECT_KINDS has "
                        "no `vision` member — no legal target kind exists"
                    )
                row.update({"label": NEEDS_DECISION, "why": why})
            elif rule[0] == KEEP:
                row.update({"label": KEEP, "why": rule[2]})
            else:
                target_label, target_kind, why = rule
                row.update({"label": target_label, "kind": target_kind, "rule": why})
                if target_label in NAME_REQUIRED_TARGETS:
                    name = _proposed_name(node)
                    if name:
                        row["name"] = name
                        row["name_proposed"] = True
                    else:
                        row["label"] = NEEDS_DECISION
                        row["why"] = (
                            f"{why} — but {NAME_REQUIRED_REASON[target_label]} "
                            "and this node has no name/content to "
                            "propose one from"
                        )
        proposals.append(row)
    return proposals


# ── plan ────────────────────────────────────────────────────────────────────


def _parse_decisions(raw: Any) -> list[Decision]:
    """Accept ``{"decisions": [...]}`` or a bare list of decision rows."""
    rows = raw.get("decisions") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise RetypeError(["decisions payload is neither a list nor {'decisions': [...]}"])
    out: list[Decision] = []
    for row in rows:
        if not isinstance(row, dict) or "key" not in row:
            raise RetypeError([f"malformed decision row: {row!r:.80}"])
        label = row.get("label")
        if label is not None:
            label = str(label)
            if label != KEEP and label != NEEDS_DECISION and label not in RETYPE_TARGETS:
                raise RetypeError(
                    [
                        f"decision {row.get('key')!r}: unknown label {label!r} "
                        f"(expected one of {', '.join(RETYPE_TARGETS)}, {KEEP!r}, "
                        f"{NEEDS_DECISION!r})"
                    ]
                )
        out.append(
            Decision(
                key=str(row["key"]),
                label=label,
                kind=(str(row["kind"]) if row.get("kind") else None),
                name=(str(row["name"]) if row.get("name") else None),
                rule=(
                    str(row.get("rule") or row.get("why"))
                    if (row.get("rule") or row.get("why"))
                    else None
                ),
            )
        )
    return out


def _decisions_digest(decisions: Sequence[Decision]) -> str:
    payload = [
        {"key": d.key, "label": d.label, "kind": d.kind, "name": d.name}
        for d in sorted(decisions, key=lambda d: d.key)
    ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _name_collision_problems(index: Mapping[str, dict], moves: Sequence[Move]) -> list[str]:
    """Every node that ENDS the plan in a name-MERGEed layer, grouped by name.

    Invariant 6 requires a name on each moved node; requiring one is not enough,
    because ``:Object``/``:Subject`` MERGE on it (#7369) — two nodes sharing a
    name are ONE identity to the write path, not two rows. The collision can be
    with a node the plan never touches (a curated graph routinely already holds
    a same-named Object), so kept nodes are grouped in too — under EVERY
    ``:Object``/``:Subject`` label they keep, not under "the single epistemic
    label". A multi-label node cannot be moved (invariant 3) and a keep
    preserves its label set verbatim, so a ``:Point:Object`` node IS still an
    ``:Object`` after the plan; grouping by ``_current_label(node)[0]`` — which
    is ``None`` for a multi-label node — dropped exactly those from the check.
    """
    by_move = {move.key: move for move in moves}
    by_name: dict[tuple[str, str], list[str]] = {}
    for key, node in index.items():
        move = by_move.get(key)
        if move is not None:
            labels: set[str] = {move.dst_label}
            name = move.name
        else:
            labels = set(_labels(node))
            name = _props(node).get("name")
        if not (isinstance(name, str) and name.strip()):
            continue
        for label in sorted(labels & NAME_MERGE_TARGETS):
            by_name.setdefault((label, name.strip()), []).append(key)
    problems: list[str] = []
    for (label, name), keys in sorted(by_name.items()):
        if len(keys) < 2:
            continue
        problems.append(
            f"{', '.join(repr(k) for k in sorted(keys))} would all end as "
            f":{label} name={name!r} — a :{label} MERGEs on its name (#7369), so "
            "the target graph would hold them as ONE identity (the write path "
            "folds both into whichever it finds); give each its own name"
        )
    return problems


def build_plan(
    dump: Mapping[str, Any],
    decisions: Any,
    *,
    extra_kinds: Mapping[str, Iterable[str]] | None = None,
) -> RetypePlan:
    """Validate every decision against every invariant. Raises ALL problems."""
    index = index_dump(dump)
    parsed = _parse_decisions(decisions)
    vocab = kind_vocab(extra_kinds)

    problems: list[str] = []
    by_key: dict[str, Decision] = {}
    for decision in parsed:
        if decision.key in by_key:
            problems.append(f"duplicate decision for {decision.key!r}")
            continue
        by_key[decision.key] = decision

    unknown = sorted(set(by_key) - set(index))
    if unknown:
        problems.append(
            f"{len(unknown)} decision(s) name node keys the dump does not carry: "
            + ", ".join(repr(k) for k in unknown[:10])
            + ("" if len(unknown) <= 10 else f" (+{len(unknown) - 10} more)")
        )
    undecided = sorted(set(index) - set(by_key))
    if undecided:
        problems.append(
            f"{len(undecided)} node(s) have no decision — the partition must be "
            "total: "
            + ", ".join(repr(k) for k in undecided[:10])
            + ("" if len(undecided) <= 10 else f" (+{len(undecided) - 10} more)")
        )

    moves: list[Move] = []
    keeps: list[str] = []
    for key in sorted(by_key):
        decision = by_key[key]
        node = index.get(key)
        if node is None:
            continue  # already reported
        if decision.needs_decision:
            problems.append(
                f"{key!r} still needs a decision ({NEEDS_DECISION})"
                + (f": {decision.rule}" if decision.rule else "")
            )
            continue
        label, epistemic = _current_label(node)
        props = _props(node)
        if decision.label == KEEP:
            keeps.append(key)
            continue
        target = str(decision.label)
        # Invariant 3 — one label among the four.
        if len(epistemic) > 1:
            problems.append(
                f"{key!r} carries multiple epistemic labels {epistemic} — a "
                "multi-label node has no single layer to re-type from"
            )
            continue
        # Invariant 2 — only :Point moves.
        if label != "Point":
            problems.append(
                f"{key!r} is labelled {label or 'non-epistemic'}, not :Point — "
                "only a flattened :Point may be re-typed"
            )
            continue
        # Invariant 5 — an operator node stays :Point.
        if props.get("is_operator"):
            problems.append(
                f"{key!r} is an operator node (:Point {{is_operator:true}}) — "
                "ONTOLOGY §5 keeps operators :Point; only its children and "
                "targets may carry the other labels"
            )
            continue
        # Invariant 4 — kind legality.
        kind = decision.kind or (str(props.get("pointKind") or "") if target == "Point" else "")
        if not kind:
            problems.append(
                f"{key!r} → :{target} needs an explicit `kind` (the node's own "
                f"pointKind={props.get('pointKind')!r} is not a :{target} kind)"
            )
            continue
        # An EMPTY vocabulary is "nothing is legal", never "anything goes":
        # every layer here has a declared set, and a pack kind arrives through
        # ``extra_kinds``. Gating on ``allowed and …`` left a layer with no
        # declared set accepting ANY kind — the off-vocabulary-kind class this
        # invariant exists to close (#5202/#5475).
        allowed = vocab[target]
        if kind not in allowed:
            problems.append(
                f"{key!r} → :{target} {KIND_PROP[target]}={kind!r} is not in the "
                f"{target} vocabulary ({len(allowed)} core kinds); name a "
                f"pack-declared kind explicitly if that is what it is"
            )
            continue
        # Invariant 6 — the target layer's identity contract.
        name = decision.name or (props.get("name") if isinstance(props.get("name"), str) else None)
        if target in NAME_REQUIRED_TARGETS and not (name and name.strip()):
            problems.append(
                f"{key!r} → :{target} needs a `name` ({NAME_REQUIRED_REASON[target]}); "
                "naming a thing is the judgement the mechanics do not have"
            )
            continue
        # The target layer's identity property must be satisfiable.
        # Point → Point is a KIND move; everything else is a LABEL move.
        kind_only = target == label
        identity_prop = IDENTITY_PROP[target]
        identity = props.get(identity_prop) or props.get("id")
        if not (isinstance(identity, str) and identity.strip()):
            problems.append(
                f"{key!r} → :{target} needs a `{identity_prop}` (the layer's "
                "identity property); the node carries none"
            )
            continue
        derives_event_id = target == "Event" and not props.get(identity_prop)
        if kind_only and kind == props.get("pointKind"):
            keeps.append(key)  # a no-op decision is a keep, not a silent move
            continue
        moves.append(
            Move(
                key=key,
                dump_id=int(node["dump_id"]),
                src_label=label,
                dst_label=target,
                kind=kind,
                name=(name.strip() if name else None),
                derives_event_id=derives_event_id,
                kind_only=kind_only,
            )
        )

    # Invariant 6 — a name is required AND unique among the nodes that end in a
    # name-MERGEed layer. Collected UNCONDITIONALLY: the invariants each raise
    # with the FULL problem list, so a run holding an off-vocabulary kind AND a
    # name collision must report both. Gating this on ``not problems`` turned a
    # large snapshot into an iterate-until-quiet loop (fix the kinds, re-run,
    # learn about the names).
    problems.extend(_name_collision_problems(index, moves))
    if problems:
        raise RetypeError(problems)
    return RetypePlan(
        moves=tuple(moves),
        keeps=tuple(sorted(keeps)),
        decisions_sha256=_decisions_digest(parsed),
        dump_sha256=_dump_fingerprint(dump),
    )


# ── apply ───────────────────────────────────────────────────────────────────


def _epistemic_edge_census(dump: Mapping[str, Any]) -> dict[int, set[str]]:
    """``{dump_id: {edge types}}`` for epistemic edges — advisory only."""
    census: dict[int, set[str]] = {}
    edges = dump.get("edges")
    if not isinstance(edges, list):
        return census
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        etype = edge.get("type")
        if etype not in EPISTEMIC_EDGE_TYPES:
            continue
        for endpoint in (edge.get("src"), edge.get("dst")):
            if isinstance(endpoint, int):
                census.setdefault(endpoint, set()).add(str(etype))
    return census


def apply_plan(
    dump: Mapping[str, Any],
    plan: RetypePlan,
    *,
    keep_embedding: bool = False,
) -> tuple[dict, dict]:
    """Return ``(retyped_dump, report)``. Pure: ``dump`` is never mutated."""
    nodes_in = dump.get("nodes")
    edges_in = dump.get("edges")
    if not isinstance(nodes_in, list) or not isinstance(edges_in, list):
        raise RetypeError(["dump carries no node/edge lists — nothing to re-type"])

    # Shape/dump_id validation before a single node is rewritten (the loader's
    # own container contract: a non-dict prop bag, a non-list label set or a
    # non-integer ``dump_id`` is corruption, a duplicate dump_id is an
    # ambiguity), plus the id → identity map the plan is bound to.
    index = index_dump(dump)
    identity_by_id = {int(node["dump_id"]): key for key, node in index.items()}

    # Invariant 7 — the plan belongs to THIS snapshot. Matching on dump_id alone
    # is POSITIONAL (see ``_dump_fingerprint``), so both halves are checked: the
    # fingerprint over the whole node set, and per-move identity. Either alone
    # leaves a silent mislabel: the fingerprint is what catches a rotation of the
    # KEPT nodes, the per-move check names exactly which moves went astray.
    problems: list[str] = []
    fingerprint = _dump_fingerprint(dump)
    if plan.dump_sha256 != fingerprint:
        problems.append(
            "plan was built for a different snapshot: dump fingerprint "
            f"{plan.dump_sha256[:12] or '<none>'} != {fingerprint[:12]} — re-run "
            "build_plan against this dump (its ids are positional, so a plan for "
            "another snapshot of the same size would apply to the wrong nodes)"
        )
    misapplied = [move for move in plan.moves if identity_by_id.get(move.dump_id) != move.key]
    if misapplied:
        problems.append(
            f"{len(misapplied)} plan move(s) land on a DIFFERENT node in this dump: "
            + ", ".join(
                f"{m.key!r} @ dump_id {m.dump_id} is {identity_by_id.get(m.dump_id)!r}"
                for m in misapplied[:10]
            )
            + ("" if len(misapplied) <= 10 else f" (+{len(misapplied) - 10} more)")
        )
    unknown_keeps = sorted(set(plan.keeps) - set(identity_by_id.values()))
    if unknown_keeps:
        problems.append(
            f"{len(unknown_keeps)} keep(s) name node(s) this dump does not carry: "
            + ", ".join(repr(k) for k in unknown_keeps[:10])
            + " — the partition must close against this snapshot's identities"
        )
    if problems:
        raise RetypeError(problems)

    moves_by_id = {m.dump_id: m for m in plan.moves}
    census = _epistemic_edge_census(dump)
    out_nodes: list[dict] = []
    moved_ids: set[int] = set()
    by_move: dict[str, int] = {}
    kind_moves: dict[str, int] = {}
    derived_event_ids = 0
    dropped_is_episodic = 0
    dropped_embedding = 0
    dropped_kind_props = 0
    epistemic_endpoints: list[str] = []

    for node in nodes_in:
        dump_id = int(node["dump_id"])
        move = moves_by_id.get(dump_id)
        if move is None:
            keep = dict(node)
            keep["labels"] = _labels(node)
            keep["props"] = dict(_props(node))
            out_nodes.append(keep)
            continue
        props = dict(_props(node))
        labels = _labels(node)
        src_kind = props.get(KIND_PROP[move.src_label], "")

        # The kind property: target layer's one, other layers' none.
        for other, prop in KIND_PROP.items():
            if other != move.dst_label and prop in props:
                props.pop(prop)
                dropped_kind_props += 1
        props[KIND_PROP[move.dst_label]] = move.kind

        # The target layer's identity contract.
        identity_prop = IDENTITY_PROP[move.dst_label]
        if move.dst_label == "Event" and not props.get(identity_prop):
            props[identity_prop] = props.get("id")
            derived_event_ids += 1
        if move.dst_label in NAME_REQUIRED_TARGETS:
            props["name"] = move.name
        if move.dst_label in NAME_EMBEDDED_TARGETS:
            if props.pop("is_episodic", None) is not None:
                dropped_is_episodic += 1
            if not keep_embedding and props.pop("embedding", None) is not None:
                dropped_embedding += 1

        # Layer set: swap the source label, never accumulate one.
        new_labels = [move.dst_label if x == move.src_label else x for x in labels]
        if move.dst_label not in new_labels:
            new_labels.append(move.dst_label)

        moved_ids.add(dump_id)

        out_nodes.append({"dump_id": dump_id, "labels": new_labels, "props": props})
        bucket = f"{move.src_label}->{move.dst_label}"
        by_move[bucket] = by_move.get(bucket, 0) + 1
        pair = f"{move.src_label}:{src_kind}->{move.dst_label}:{move.kind}"
        kind_moves[pair] = kind_moves.get(pair, 0) + 1
        if dump_id in census and not move.kind_only:
            epistemic_endpoints.append(move.key)

    # Invariant 7 — counts reconcile, checked against the output itself.
    out_ids = [int(n["dump_id"]) for n in out_nodes]
    if len(moved_ids) != len(plan.moves):
        problems.append(
            f"plan does not apply to this dump: {len(plan.moves)} move(s), "
            f"{len(moved_ids)} node id(s) matched"
        )
    # The output carries one row per input row and ``identity_by_id`` is a map
    # over that same input, so comparing the output id set with
    # ``set(identity_by_id)`` compares the input WITH ITSELF — it cannot
    # disagree. The comparison that can is against the ids the PLAN declares:
    # the moves' dump_ids plus the ids of the nodes ``plan.keeps`` names. Those
    # are frozen into the plan, so a plan that does not describe this dump's
    # output is refused here even if the counts happen to line up.
    keep_ids = {dump_id for dump_id, key in identity_by_id.items() if key in set(plan.keeps)}
    planned_ids = {move.dump_id for move in plan.moves} | keep_ids
    if len(out_nodes) != len(nodes_in) or set(out_ids) != planned_ids:
        problems.append(
            f"node set changed: the plan declares {len(planned_ids)} id(s), this "
            f"dump has {len(nodes_in)} node(s) and the output carries "
            f"{len(out_nodes)} row(s) / {len(set(out_ids))} distinct id(s)"
        )
    if len(plan.moves) + len(plan.keeps) != len(nodes_in):
        problems.append(
            f"partition does not close: {len(plan.moves)} move(s) + "
            f"{len(plan.keeps)} keep(s) != {len(nodes_in)} node(s)"
        )
    malformed_edges = sum(1 for edge in edges_in if not isinstance(edge, dict))
    if malformed_edges:
        problems.append(
            f"{malformed_edges} edge row(s) are not objects — restore_graph calls that malformed"
        )
    id_set = set(out_ids)
    missing_edge_ids = sorted(
        {
            endpoint
            for edge in edges_in
            if isinstance(edge, dict)
            for endpoint in (edge.get("src"), edge.get("dst"))
            if endpoint not in id_set
        },
        key=str,
    )
    if missing_edge_ids:
        # ``edges_preserved`` is only true when every endpoint survived. dump_id
        # is preserved by construction, but the DUMP may already be dangling and
        # this tool must not certify that as preserved.
        problems.append(
            f"{len(missing_edge_ids)} edge endpoint dump_id(s) no output node "
            "carries: "
            + ", ".join(repr(k) for k in missing_edge_ids[:10])
            + " — the dump is already dangling, so edges_preserved cannot be "
            "asserted"
        )
    if problems:
        raise RetypeError(problems)

    out = dict(dump)
    out["nodes"] = out_nodes
    out["edges"] = list(edges_in)
    # Edges are untouched by construction (dump_id is preserved), so the dump's
    # own edge bookkeeping stays true — assert it rather than trust it.
    if out.get("edge_count") not in (None, len(edges_in)):
        raise RetypeError(
            [
                f"dump edge_count {out.get('edge_count')!r} disagrees "
                f"with its {len(edges_in)} edge row(s)"
            ]
        )

    report: dict[str, Any] = {
        "tool": "retype",
        "report_version": 1,
        "source": {
            "format": dump.get("format"),
            "graph_name": dump.get("graph_name"),
            "dumped_at": dump.get("dumped_at"),
            "node_count": dump.get("node_count"),
            "edge_count": dump.get("edge_count"),
            "dump_revision": dump.get("dump_revision"),
        },
        "decisions_sha256": plan.decisions_sha256,
        "nodes_in": len(nodes_in),
        "nodes_out": len(out_nodes),
        "edges_preserved": len(edges_in),
        "keeps": len(plan.keeps),
        "moves_total": len(plan.moves),
        "moves_by_layer": dict(sorted(by_move.items())),
        "moves_by_kind": dict(sorted(kind_moves.items())),
        "derived": {"eventId_from_id": derived_event_ids},
        "dropped": {
            "stale_kind_props": dropped_kind_props,
            "is_episodic": dropped_is_episodic,
            "embedding": dropped_embedding,
        },
        "epistemic_endpoint_moves": {
            "count": len(epistemic_endpoints),
            "nodes": sorted(epistemic_endpoints),
        },
        "warnings": [],
    }
    if dropped_embedding and not keep_embedding:
        report["warnings"].append(
            f"{dropped_embedding} moved node(s) lost their `embedding`: a "
            ":Object/:Subject vector is encoded FROM `name` (#7845), so the "
            "Point's content-derived vector would be text the node no longer "
            "holds (#8419). Re-embed after load if vector recall matters for "
            "them, or pass keep_embedding=True to carry the stale vector."
        )
    if epistemic_endpoints:
        report["warnings"].append(
            f"{len(epistemic_endpoints)} moved node(s) are endpoints of "
            "IMPL/NAND/MITIGATES edges. Legal (#7813 allows all four labels), "
            "but residual :Point-only EP reads (#7927) stop seeing them."
        )
    return out, report


# ── convenience ─────────────────────────────────────────────────────────────


def retype(
    dump: Mapping[str, Any],
    decisions: Any,
    *,
    extra_kinds: Mapping[str, Iterable[str]] | None = None,
    keep_embedding: bool = False,
) -> tuple[dict, dict]:
    """``build_plan`` + ``apply_plan`` in one call."""
    plan = build_plan(dump, decisions, extra_kinds=extra_kinds)
    return apply_plan(dump, plan, keep_embedding=keep_embedding)
