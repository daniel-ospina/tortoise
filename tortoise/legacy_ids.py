"""W1b — normalise legacy node ids onto the canonical id contract (#8607).

The contract (``docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md``
§1.1/§1.4, pinned by ``tests/test_identity_contract.py``) fixes **one stable key
per label** and states that an id which does not match a minted shape is
**opaque and valid** — it does not require existing ids to match a format. Two
stores cannot agree on a node whose key is not agreed, though, so the live ids
``STORAGE-ARCHITECTURE.md`` §17.5 measured in "two incompatible formats" are the
migration debt this module mechanises.

## What the census actually finds (and why the frame needed correcting)

§17.5 and #8607 both describe **~9,700 legacy ids** as ``8,700 <hex>-<hex>`` +
``1,000 p-NNNNNNN`` + ``~30 EV-*``. ``<hex>-<hex12>`` is **the form
``tortoise/ids.py::ulid()`` mints** (``sdk._ULID_RE``), so those 8,700 are
already canonical by the contract's own predicate: the census counted two
*formats*, and only one of them is non-conforming. :func:`census` reports the
split instead of inheriting the label, so the real population of ids that need
a rename is the ``p-NNNNNNN`` and ``EV-*`` groups — and the number a lane plans
against is the measured one, never the carried one.

## Division of labour — the same one ``tortoise/retype.py`` states

The issue's own words: *"the model should label and the script should only do
mechanics (batch, validate, assert a partition invariant: every node exactly
once, counts reconcile to the total)"*. Here there is **no classification to
supply**: the contract already decides what is canonical (it is a predicate,
:func:`is_canonical`), so the mechanics are the whole job — census the
snapshot, plan a total single-valued rename, refuse anything unprovable.

## Offline, because a wrong pass must cost a re-run and not a repair

Both entry points are pure functions over a ``tortoise-logical-dump-v1`` dict
(``tortoise.hosted_backup.dump_graph``): no graph connection, no writes, no
clock. The same dump in produces the same dump out, byte for byte.

``dump_id`` — the dump's own node identity, which edges reference — is
**PRESERVED** for every node, so every edge survives untouched
(``restore_graph`` re-links by ``dump_id``). Only the identity property's value
changes. That is also what makes the pass **reversible**: the plan carries
``old``/``new`` per node, and :func:`apply_plan` with ``revert=True`` puts the
original keys back.

## Scope, declared rather than assumed

Only labels whose contract key is a **minted id** are rewritten — today
``:Point``/``:Event``/``:Session`` (``id``/``eventId``/``id``). The
``name``/``url``-keyed labels (``:Subject``/``:Object``/``:Source``) are
**reported and never rewritten**: their key is semantic content, and §1.2
clause 2 is explicit — *"a rename is a new node"*, not a re-key of this one.

## The mapping, and why it is not a new id scheme

``new = entity_name_id(label, old)`` — the **existing** canonical minter
(``tortoise/ids.py``), applied to the node's already-unique key. §1.4 rule 1:
*"the legacy id is already the node's unique key; the rewrite is a rename of
the key value, not a merge"*. So the target is one a reader can recompute, and
there is no second minter to drift. The resulting handle is canonical by
:func:`is_canonical`, which is what makes a second run a no-op.

## Fail-closed invariants (each raises :class:`LegacyIdError` with EVERY one)

1. **Partition.** Every node is classified into exactly one bucket, and every
   id-keyed node is either canonical (untouched) or renamed exactly once. A
   node that cannot be classified — no labels, an ambiguous identity, an
   unknown label, an id-keyed label with no key — is a problem, never a
   silent skip.
2. **Single-valued.** Two distinct ``(label, legacy id)`` pairs never map to
   one canonical id: two nodes becoming one key is a merge, and §1.4 forbids
   exactly that. (Also checked against the dump as a whole, because two nodes
   may carry the SAME legacy id — that is the duplicate-identity case
   ``tools/graph_census.py`` reports — and renaming both would merge them.)
3. **Non-colliding.** A mapped canonical id must not already exist in the
   snapshot. A real pass over a live graph cannot contain one; a plan that
   does is built from the wrong snapshot or from a minter that drifted.
4. **Fixed point.** The canonical predicate is closed under the rename, so
   re-planning the OUTPUT yields an empty plan (#8607 indicator 2). §1.2
   clause 1 — *"minted once, never changed"* — is therefore not bent: it
   governs ids that match a minted shape, and :func:`is_canonical` is what
   decides that, so a canonical id is never a rename candidate.
5. **Counts reconcile.** Nodes in == out; every node keeps exactly one key;
   the renames plus the untouched ids account for every node in the snapshot;
   every edge is preserved verbatim (endpoints are ``dump_id``, which nothing
   here touches); the dump's own ``node_count``/``edge_count`` still match the
   lists it carries.
6. **Bound to its snapshot.** The plan carries ``dump_sha256`` over each node's
   ``dump_id``, labels and contract key, and :func:`apply_plan` recomputes it
   and refuses a mismatch. ``dump_id`` is the store's INTERNAL id — positional
   and reused after deletions — so a plan applied to a refreshed snapshot of
   the same size would rewrite a different node's key and every count would
   still reconcile.

## What this module does NOT do, and says so

* **It does not touch the record.** A node's Postgres row is a projection of
  the journal; a rename reaches it through the write path, not through a
  second writer here (``#7951``/``#7969`` own that direction).
* **It does not journal the rename.** §1.4 rule 2 requires the LIVE pass to be
  a journal event so ``replay(journal)`` reproduces it — a write-path change
  (``#7951``), not a snapshot transform. The plan this module produces **is**
  the payload that pass would journal: the old→new pairs, one per node.
* **It does not decide whether the pass should run.** The owner's 2026-10-10
  scope ruling and the launch criterion put it at P1 (``#8607``); this module
  is the mechanics, not the go-ahead.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tortoise.cache_projection import KEY_PROPERTY
from tortoise.ids import entity_name_id
from tortoise.sdk import _CROCKFORD_ULID_RE, _ULID_RE, _is_canonical_entity_id

#: Census buckets.
CANONICAL = "canonical"
LEGACY = "legacy"
NAME_KEYED = "name-keyed"

#: The contract's key properties whose VALUE is a minted id (Iface E §1.1:
#: ``:Point``→``id``, ``:Event``→``eventId``, ``:Session``→``id``). The other
#: two spellings — ``:Subject``/``:Object``→``name`` and ``:Source``→``url`` —
#: are content, not a minted handle, and a rename of them is a new node
#: (§1.2 clause 2), so they are out of this pass by declaration.
MINTED_KEY_PROPERTIES: frozenset[str] = frozenset({"id", "eventId"})


class LegacyIdError(RuntimeError):
    """The plan is not mechanically producible. ``problems`` holds every one."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__(
            f"legacy id normalisation refused ({len(self.problems)} problem(s)): "
            + "; ".join(self.problems[:10])
            + ("" if len(self.problems) <= 10 else f" (+{len(self.problems) - 10} more)")
        )


# ── the contract's own predicate ────────────────────────────────────────────


def shape(value: str) -> str:
    """The SHAPE NAME of ``value`` — the contract's spellings, not a new regex.

    ``minted-ulid``
        ``<hex>-<hex12>``, what ``tortoise/ids.py::ulid()`` emits
        (``sdk._ULID_RE``). ⚠️ **This is the group §17.5's census counted as
        8,700 "``<hex>-<hex>``" ids — the MINTED form**, so those are already
        canonical.
    ``crockford-ulid``
        a standard 26-char Crockford base32 ULID (``_CROCKFORD_ULID_RE``).
    ``entity-handle``
        ``label[:3]-<sha256[:26]>``, what ``_entity_name_id`` emits
        (``sdk._CANONICAL_ENTITY_ID_RE``).
    ``legacy``
        anything else — the only bucket that is a rename candidate.
    """
    if _ULID_RE.match(value):
        return "minted-ulid"
    if _CROCKFORD_ULID_RE.match(value):
        return "crockford-ulid"
    if _is_canonical_entity_id(value):
        return "entity-handle"
    return LEGACY


def is_canonical(value: object) -> bool:
    """True iff ``value`` is a key the contract's minting surfaces could emit.

    Deliberately the *strict* predicate (``_is_canonical_entity_id``), not the
    lenient about*-seam one (``_is_entity_id``): the lenient shape
    (``_ENTITY_ID_RE``, ``[a-z]{2,8}-<16..64 hex>``) exists to answer "may an
    UNRESOLVED value be treated as a name?", and using it here would declare a
    large part of the legacy population canonical.
    """
    return isinstance(value, str) and shape(value) != LEGACY


def canonical_id(label: str, legacy: str) -> str:
    """The canonical id a legacy key is renamed to — ``entity_name_id``'s.

    Deterministic and total (one value per ``(label, legacy)``), which is what
    makes the pass idempotent and the plan reviewable: a reader recomputes it.
    """
    return entity_name_id(label, legacy)


# ── dump access (the same accessors ``retype`` uses, so both read one shape) ─


def _props(node: Mapping[str, Any]) -> dict:
    props = node.get("props")
    return props if isinstance(props, dict) else {}


def _labels(node: Mapping[str, Any]) -> list[str]:
    labels = node.get("labels")
    return [str(x) for x in labels] if isinstance(labels, list) else []


def _node_label(node: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """``(label, problem)`` — the ONE label whose contract key this node carries.

    A node carrying ``:Point:Session`` keys on two different properties, so it
    is ambiguous and is REFUSED rather than resolved by list order (the write
    path would key it by one and the record by the other).
    """
    dump_id = node.get("dump_id")
    labels = _labels(node)
    if not labels:
        return None, f"dump_id {dump_id!r} carries no label"
    known = [label for label in labels if label in KEY_PROPERTY]
    if not known:
        return None, (
            f"dump_id {dump_id!r} has labels {sorted(labels)}, none of which the "
            "record's key contract (public.node_identity) knows"
        )
    props = _props(node)
    claiming = [
        label
        for label in known
        if isinstance(props.get(KEY_PROPERTY[label]), str)
        and str(props.get(KEY_PROPERTY[label])).strip()
    ]
    if len(claiming) > 1:
        return None, (
            f"dump_id {dump_id!r} is ambiguous: labels {sorted(labels)} each "
            "carry their own contract key ("
            + ", ".join(f"{lab}.{KEY_PROPERTY[lab]}" for lab in claiming)
            + ")"
        )
    return (claiming or known)[0], None


def _dump_fingerprint(dump: Mapping[str, Any]) -> str:
    """sha256 over each node's ``dump_id``, labels and contract key.

    ``dump_id`` alone is not a snapshot identity (positional, and reused after
    deletions); the node's own key is what the plan rewrites, so binding both
    is what turns a stale plan into a MISMATCH instead of a silent rewrite of a
    different node whose counts would still reconcile.
    """
    nodes = dump.get("nodes")
    rows: list[list[str]] = []
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                rows.append(["<malformed>", repr(node)])
                continue
            label, _ = _node_label(node)
            props = _props(node)
            rows.append(
                [
                    str(node.get("dump_id")),
                    ",".join(sorted(_labels(node))),
                    label or "",
                    "" if label is None else str(props.get(KEY_PROPERTY[label]) or ""),
                ]
            )
    rows.sort()
    blob = json.dumps(rows, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── the census ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Census:
    """What is in the snapshot, counted — the number a pass is planned against."""

    nodes: int = 0
    id_keyed: int = 0
    canonical: int = 0
    legacy: int = 0
    #: ``canonical shape -> count`` and ``legacy shape -> count``. The legacy
    #: map carries each `legacy` value's own shape name ("legacy"), so the
    #: report names the population instead of implying it.
    canonical_shapes: dict[str, int] = field(default_factory=dict)
    legacy_shapes: dict[str, int] = field(default_factory=dict)
    #: ``label -> {"canonical"|"legacy"|"name-keyed": count}``
    by_label: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def name_keyed(self) -> int:
        """Nodes whose key is content (``name``/``url``) — out of scope."""
        return sum(b.get(NAME_KEYED, 0) for b in self.by_label.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "id_keyed": self.id_keyed,
            "canonical": self.canonical,
            "legacy": self.legacy,
            "name_keyed": self.name_keyed,
            "canonical_shapes": dict(sorted(self.canonical_shapes.items())),
            "legacy_shapes": dict(sorted(self.legacy_shapes.items())),
            "by_label": {
                label: dict(sorted(counts.items()))
                for label, counts in sorted(self.by_label.items())
            },
        }


def census(dump: Mapping[str, Any]) -> Census:
    """Count the snapshot's identity keys by bucket and by label.

    Reads, never rewrites. A node :func:`_node_label` cannot resolve is
    **reported as a problem by** :func:`build_plan`; here it is counted under
    ``by_label["<unresolved>"]`` so the totals still add up and the census of a
    malformed snapshot cannot look smaller than it is.
    """
    nodes = dump.get("nodes")
    if not isinstance(nodes, list):
        raise LegacyIdError(["the dump carries no `nodes` list — nothing to census"])
    canon_shapes: dict[str, int] = {}
    legacy_shapes: dict[str, int] = {}
    by_label: dict[str, dict[str, int]] = {}
    id_keyed = canonical = legacy = 0
    for node in nodes:
        label = None if not isinstance(node, dict) else _node_label(node)[0]
        if label is None:
            by_label.setdefault("<unresolved>", {}).setdefault(LEGACY, 0)
            by_label["<unresolved>"][LEGACY] += 1
            continue
        key_prop = KEY_PROPERTY[label]
        if key_prop not in MINTED_KEY_PROPERTIES:
            by_label.setdefault(label, {}).setdefault(NAME_KEYED, 0)
            by_label[label][NAME_KEYED] += 1
            continue
        value = _props(node).get(key_prop)
        value = value if isinstance(value, str) else ""
        id_keyed += 1
        name = shape(value) if value else "missing"
        if name == LEGACY or name == "missing":
            legacy += 1
            legacy_shapes[name] = legacy_shapes.get(name, 0) + 1
            by_label.setdefault(label, {}).setdefault(LEGACY, 0)
            by_label[label][LEGACY] += 1
        else:
            canonical += 1
            canon_shapes[name] = canon_shapes.get(name, 0) + 1
            by_label.setdefault(label, {}).setdefault(CANONICAL, 0)
            by_label[label][CANONICAL] += 1
    return Census(
        nodes=len(nodes),
        id_keyed=id_keyed,
        canonical=canonical,
        legacy=legacy,
        canonical_shapes=canon_shapes,
        legacy_shapes=legacy_shapes,
        by_label=by_label,
    )


# ── the plan ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rename:
    """One node's key rewrite: the old value, the canonical value, and where."""

    dump_id: int
    label: str
    key_property: str
    old: str
    new: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "dump_id": self.dump_id,
            "label": self.label,
            "key_property": self.key_property,
            "old": self.old,
            "new": self.new,
        }


@dataclass(frozen=True)
class LegacyIdPlan:
    """A validated, total plan: every node is accounted for exactly once."""

    renames: tuple[Rename, ...]
    census: Census
    #: The snapshot this plan was validated against (:func:`_dump_fingerprint`).
    #: ``apply_plan`` recomputes it and refuses a mismatch — an empty value is
    #: an unbound plan and is refused too (fail closed).
    dump_sha256: str = ""

    @property
    def is_empty(self) -> bool:
        """True iff every id-keyed node is already canonical — a fixed point."""
        return not self.renames


def build_plan(dump: Mapping[str, Any]) -> LegacyIdPlan:
    """Validate the whole snapshot and produce the rename plan, or refuse.

    Raises :class:`LegacyIdError` with **every** problem, never a partial plan:
    a partition hole is a node whose key would be silently left un-normalised,
    which is the failure this pass exists to make impossible to miss.
    """
    nodes = dump.get("nodes")
    if not isinstance(nodes, list):
        raise LegacyIdError(["the dump carries no `nodes` list — nothing to plan"])

    problems: list[str] = []
    renames: list[Rename] = []
    #: ``(label, key)`` of every node as it will be AFTER the pass — the
    #: collision universe, and the thing invariant 3 is checked against.
    final_keys: dict[tuple[str, str], int] = {}
    #: ``(label, legacy id) -> dump_id``: two nodes sharing one legacy id under
    #: one label are ONE identity to the write path already, so renaming both
    #: to the same canonical id would merge them (invariant 2).
    legacy_seen: dict[tuple[str, str], int] = {}

    for node in nodes:
        if not isinstance(node, dict):
            problems.append(f"malformed node row: {node!r}")
            continue
        dump_id = node.get("dump_id")
        if not isinstance(dump_id, int) or isinstance(dump_id, bool):
            problems.append(
                f"node has a non-integer dump_id {dump_id!r} — a dump must be "
                "addressable by dump_id for its edges to survive"
            )
            continue
        label, problem = _node_label(node)
        if problem is not None:
            problems.append(problem)
            continue
        assert label is not None
        key_prop = KEY_PROPERTY[label]
        if key_prop not in MINTED_KEY_PROPERTIES:
            # Declared out of scope (§1.2 clause 2) — counted, never rewritten.
            continue
        value = _props(node).get(key_prop)
        if not isinstance(value, str) or not value.strip():
            problems.append(
                f"dump_id {dump_id} is a :{label} with no `{key_prop}` — the "
                "layer's identity property; it cannot be normalised or addressed"
            )
            continue
        value = value.strip()
        if is_canonical(value):
            # Invariant 4: a canonical id is never a candidate. A snapshot that
            # carries the same canonical id twice is a duplicate identity, not
            # a rename.
            if (label, value) in final_keys:
                problems.append(
                    f"duplicate identity: :{label} {value!r} is carried by both "
                    f"dump_id {final_keys[(label, value)]} and {dump_id} — two "
                    "nodes already share one key, which no rename can fix"
                )
            final_keys[(label, value)] = dump_id
            continue
        if (label, value) in legacy_seen:
            problems.append(
                f"duplicate legacy identity: :{label} {value!r} is carried by "
                f"both dump_id {legacy_seen[(label, value)]} and {dump_id}; "
                "renaming both would MERGE two nodes onto one key (§1.4 rule 1)"
            )
            continue
        legacy_seen[(label, value)] = dump_id
        renames.append(
            Rename(
                dump_id=dump_id,
                label=label,
                key_property=key_prop,
                old=value,
                new=canonical_id(label, value),
            )
        )

    # Invariants 2 and 3, checked over the FINAL key space (a mapped id may
    # collide with an untouched canonical id, or with another mapped id).
    for rename in renames:
        target = (rename.label, rename.new)
        if target in final_keys:
            problems.append(
                f"target collision: :{rename.label} {rename.old!r} → "
                f"{rename.new!r} is already carried by dump_id "
                f"{final_keys[target]} — applying this would merge two nodes"
            )
            continue
        final_keys[target] = rename.dump_id
        if not is_canonical(rename.new):
            problems.append(
                f"minter regression: {rename.new!r} (from :{rename.label} "
                f"{rename.old!r}) is not canonical, so the pass could not reach "
                "a fixed point"
            )

    # Invariant 5, the ledger's own halves: the renames and the untouched nodes
    # must account for the whole id-keyed population.
    reported = census(dump)
    if not problems:
        if reported.id_keyed != len(renames) + reported.canonical:
            problems.append(
                f"partition hole: {reported.id_keyed} id-keyed nodes but "
                f"{len(renames)} renames + {reported.canonical} canonical — "
                "some node is counted in neither bucket"
            )
        if len(renames) != reported.legacy:
            problems.append(
                f"partition hole: {len(renames)} renames planned for {reported.legacy} legacy ids"
            )
        _check_dump_counts(dump, problems)

    if problems:
        raise LegacyIdError(problems)
    return LegacyIdPlan(
        renames=tuple(renames),
        census=reported,
        dump_sha256=_dump_fingerprint(dump),
    )


def _check_dump_counts(dump: Mapping[str, Any], problems: list[str]) -> None:
    """The dump's own declared counts vs the lists it actually carries.

    A snapshot whose ``node_count`` disagrees with its ``nodes`` is a TRUNCATED
    export (``#5062``), and normalising a truncated snapshot would rewrite a
    population that is not the graph's.
    """
    for declared, key in (("node_count", "nodes"), ("edge_count", "edges")):
        if declared not in dump:
            continue
        rows = dump.get(key)
        actual = len(rows) if isinstance(rows, list) else 0
        try:
            wanted = int(dump[declared])
        except (TypeError, ValueError):
            problems.append(f"the dump's {declared} is not an integer: {dump[declared]!r}")
            continue
        if wanted != actual:
            problems.append(
                f"the dump's {declared} is {wanted} but it carries {actual} "
                f"{key} — a truncated or corrupt artifact is not measurable"
            )


# ── applying the plan ───────────────────────────────────────────────────────


def apply_plan(
    dump: Mapping[str, Any],
    plan: LegacyIdPlan,
    *,
    revert: bool = False,
) -> tuple[dict, dict[str, Any]]:
    """Rewrite (or with ``revert=True`` un-rewrite) the snapshot's keys.

    Returns ``(out_dump, report)``. Refuses — with every problem — on a stale
    plan, a partition hole, or a count that does not reconcile; it never writes
    a partial artifact.
    """
    if not plan.dump_sha256:
        raise LegacyIdError(["the plan is not bound to a snapshot (no dump_sha256) — rebuild it"])
    if not revert:
        actual = _dump_fingerprint(dump)
        if actual != plan.dump_sha256:
            raise LegacyIdError(
                [
                    f"the plan was built for a different snapshot (plan "
                    f"{plan.dump_sha256[:16]}…, dump {actual[:16]}…) — dump_id is "
                    "positional and is reused after deletions, so this plan may "
                    "name different nodes"
                ]
            )

    nodes_in = dump.get("nodes")
    edges_in = dump.get("edges")
    if not isinstance(nodes_in, list):
        raise LegacyIdError(["the dump carries no `nodes` list"])
    edges = edges_in if isinstance(edges_in, list) else []

    by_dump_id: dict[int, Rename] = {rename.dump_id: rename for rename in plan.renames}
    problems: list[str] = []
    out_nodes: list[dict] = []
    applied = 0
    for node in nodes_in:
        if not isinstance(node, dict):
            out_nodes.append(node)  # unreachable: build_plan refused it
            continue
        dump_id = node.get("dump_id")
        rename = by_dump_id.get(dump_id) if isinstance(dump_id, int) else None
        if rename is None:
            out_nodes.append(node)
            continue
        want, give = (rename.new, rename.old) if revert else (rename.old, rename.new)
        props = dict(_props(node))
        carried = props.get(rename.key_property)
        if carried != want:
            problems.append(
                f"dump_id {dump_id} carries {rename.key_property}={carried!r}, "
                f"but the plan expects {want!r}"
            )
            out_nodes.append(node)
            continue
        props[rename.key_property] = give
        out_nodes.append({**node, "props": props})
        applied += 1

    if applied != len(plan.renames):
        problems.append(
            f"{len(plan.renames)} rename(s) in the plan, {applied} applied — the "
            "plan and the snapshot disagree about which nodes exist"
        )

    out: dict = {**dump, "nodes": out_nodes}
    if "edges" in dump:
        out["edges"] = edges
    # Invariant 5: nodes in == out, and the counts the dump declares still hold.
    if len(out_nodes) != len(nodes_in):
        problems.append(f"{len(nodes_in)} nodes in, {len(out_nodes)} out")
    edges_out = out.get("edges")
    edges_out = edges_out if isinstance(edges_out, list) else []
    if edges_out != edges:
        problems.append("the edge list changed — a key rename must not touch edges")
    for declared, rows in (("node_count", out_nodes), ("edge_count", edges_out)):
        if declared in out and int(out[declared]) != len(rows):
            problems.append(
                f"the output's {declared} ({out[declared]}) no longer matches its "
                f"{len(rows)} row(s)"
            )
    if revert:
        # A revert's own proof: the restored artifact must BE the snapshot the
        # plan was built from, not merely look like it.
        restored = _dump_fingerprint(out)
        if restored != plan.dump_sha256:
            problems.append(
                f"the reverted snapshot fingerprints to {restored[:16]}…, not the "
                f"plan's {plan.dump_sha256[:16]}… — it is not the original artifact"
            )

    if problems:
        raise LegacyIdError(problems)

    after = census(out)
    before = census(dump)
    report = {
        "mode": "revert" if revert else "apply",
        "dump_sha256": plan.dump_sha256,
        "nodes_in": len(nodes_in),
        "nodes_out": len(out_nodes),
        "edges_in": len(edges),
        "edges_out": len(edges_out),
        # The ledger, both directions and both buckets — a caller reconciles on
        # these and never has to infer a count from another.
        "id_keyed_in": before.id_keyed,
        "id_keyed_out": after.id_keyed,
        "canonical_before": before.canonical,
        "canonical_after": after.canonical,
        "legacy_before": before.legacy,
        "legacy_after": after.legacy,
        "legacy_remaining": after.legacy,
        "renamed": applied,
        "census": after.as_dict(),
        "renames": [rename.as_dict() for rename in plan.renames],
    }
    return out, report
