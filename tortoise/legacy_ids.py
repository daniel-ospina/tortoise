"""W1b — normalise legacy node ids onto the canonical id contract (#8607).

The contract (``docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md``
§1.1/§1.4, pinned by ``tests/test_identity_contract.py``) fixes **one stable key
per label** and states that an id which does not match a minted shape is
**opaque and valid** — it does not require existing ids to match a format. Two
stores cannot agree on a node whose key is not agreed, though, so the legacy ids
``STORAGE-ARCHITECTURE.md`` §17.5 measured are the migration debt this module
mechanises.

## What the census actually finds, and which label it is safe to re-key

§17.5 and #8607 both describe **~9,700 legacy ids** as ``8,700 <hex>-<hex>`` +
``1,000 p-NNNNNNN`` + ``~30 EV-*``. ``<hex>-<hex12>`` is **the form
``tortoise/ids.py::ulid()`` mints** (``sdk._ULID_RE``), so those 8,700 are
already canonical — **one group on the minted form, two off it** (``p-NNNNNNN``
and ``EV-*``; both ``shape() == LEGACY``), not "two formats, one violation".
:func:`census` reports the split instead of inheriting the label.

⚠️ **The label attribution is an inference, not a measurement.** §17.5 records
the three COUNTS without naming labels; that ``p-NNNNNNN`` sits on ``:Point``
and ``EV-*`` on ``:Event`` is read from the prefixes and from #8607's scoping
census, and it is **load-bearing** — an ``EV-*`` on a ``:Point`` would be in
scope and the rename population would be larger. **What settles it is
:attr:`Census.by_label`** (``label -> {canonical, legacy, ...}``), which IS per
label. ⚠️ **``legacy_shapes`` is not per label and cannot be**: it buckets by
:func:`shape`, whose whole point is that an off-contract id is opaque, so every
one of them lands under the single key ``legacy``. On the fixture that reads
``legacy_shapes == {'legacy': 1002}`` while ``by_label['Point'] ==
{'canonical': 8700, 'legacy': 1002}`` — the FORM MIX inside that 1,002 is not
recoverable from the census, so a real dump whose ``:Point`` ids are both
``p-NNNNNNN`` and near-ULID spellings reports one number. Read ``by_label``
first; treat ``legacy_shapes`` as a total, never a breakdown.

**Scope is a declared decision, not a property name.** Only ``:Point`` is
re-keyed here, and the reason is on the label, in :data:`REKEY_SCOPE`.
:data:`DECLARED_OUT_OF_SCOPE` carries the reason for every other label —
``:Event`` in particular, whose ``eventId`` is the journal-keyed identity with
**no sanctioned re-keying path** — ``sdk.py`` says so in the guard's own
comment, and the refusal it raises is *"'eventId' is server-managed (Event
identity) and cannot be set via props."* (:data:`DECLARED_OUT_OF_SCOPE` cites
both) — and whose ``id`` **mirrors**
``eventId`` (§1.1), so renaming one of them half-way leaves the legacy id
addressable. Those labels are **counted and reported, never rewritten**, and a
label the record's contract does not key at all (``:GraphEvent``,
``Meta`` — both present in a real ``dump_graph`` artifact) is counted as
out-of-contract rather than refused: a census that cannot be produced on the
snapshot it documents is not a census.

## Division of labour — the same one ``tortoise/retype.py`` states

The issue's own words: *"the model should label and the script should only do
mechanics (batch, validate, assert a partition invariant: every node exactly
once, counts reconcile to the total)"*. Here there is **no classification to
supply**: the contract already decides what is canonical (it is a predicate,
:func:`is_canonical`), and which label may be re-keyed is declared above. The
mechanics are the whole job — census the snapshot, plan a total single-valued
rename, refuse anything unprovable.

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

## The mapping, and why it is not a new id scheme

``new = _derived_legacy_handle(label, old)`` — a MIGRATION-ONLY handle defined
**in this module**, applied to the node's already-unique key. §1.4 rule 1:
*"the legacy id is already the node's unique key; the rewrite is a rename of
the key value, not a merge"*. So the target is one a reader can recompute, and
there is no second MINTER to drift. The resulting handle is canonical by
:func:`is_canonical`, which is what makes a second run a no-op.

### ⚠️ What that target IS, stated plainly: a ``:Point`` key is then `poi-<hash>`

The handle ``_derived_legacy_handle`` emits is ``label[:3]-<sha256[:26]>``, so a
re-keyed ``:Point`` key is **``poi-<hash>``** — and that is **not** the
``<timestamp-hex>-<uuid12>`` shape ``create_point`` mints, nor the one
``STORAGE-ARCHITECTURE`` §17.5 records `:Point` ids are minted in. Three
consequences a reader must not have to infer from :func:`is_canonical`'s union:

* **After a pass the label carries TWO canonical spellings at once.** New
  points are ``minted-ulid``; the re-keyed population is ``entity-handle``.
  :func:`census` REPORTS that split (``canonical_shapes == {"minted-ulid":
  <untouched>, "entity-handle": <renamed>}``) rather than reporting one
  "canonical" number, and
  ``tests/test_8607_legacy_id_normaliser.py::test_a_pass_leaves_two_canonical_shapes_on_point_and_says_so``
  pins it. This is a deliberate key-space exception, not a claim that the pass
  reproduced ``create_point``'s spelling.
* **``create_point``'s non-ULID warning does not describe a normalised key.**
  ``sdk.create_point`` logs *"create_point received non-ULID id=… — canonical
  format is <timestamp-hex>-<uuid12>"* for an **explicit id overriding the
  auto-mint at creation**. A normalised key is not minted there: it is a §1.4
  rule-1 rename of an existing key value, applied to a dump, and nothing in this
  module calls ``create_point``. The warning is still the reason the sentence
  *"`:Point` ids are ULIDs"* is false after a pass — say the exception instead
  of relying on the predicate's union to blur it.
* **The alternative is not available.** Aiming at the minted spelling needs a
  deterministic ``<ts-hex>-<uuid12>`` function; ``ulid()`` takes the clock and a
  random UUID, so a plan built from it could not be recomputed from the input,
  reviewed by a second lane, or verified on a revert. A deterministic,
  recomputable target is what makes this pass an auditable transform, and
  ``_derived_legacy_handle`` is the only minter that provides one, and it exists
  for this migration alone (#3590 retired the spelling as an entity id).

What is NOT true, and must not be claimed: that this restores *the* canonical
``:Point`` spelling. It gives one deterministic, contract-recognised spelling to
the legacy population.

## Fail-closed invariants (each raises :class:`LegacyIdError` with EVERY one)

1. **Partition.** Every node is classified into exactly one bucket — in scope
   and canonical, in scope and renamed, declared out of scope, or
   out-of-contract — and every in-scope node is either canonical (untouched) or
   renamed exactly once. A node that cannot be classified *within* the in-scope
   labels (no key, ambushed by another label's key, surrounding whitespace) is
   a problem, never a silent skip.
2. **Single-valued.** Two distinct legacy ids never map to one canonical id:
   two nodes becoming one key is a merge, and §1.4 forbids exactly that. Checked
   both for a repeated legacy value and for a repeated ``dump_id``.
3. **Non-colliding.** A mapped canonical id must not already exist in the
   snapshot. A real pass over a live graph cannot contain one; a plan that does
   is built from the wrong snapshot or from a minter that drifted.
4. **Fixed point.** The canonical predicate is closed under the rename, so
   re-planning the OUTPUT yields an empty plan (#8607 indicator 2). §1.2 clause
   1 — *"minted once, never changed"* — is a deliberate exception for exactly
   the population §1.4 names (legacy keys), and is not an exception for an id
   that already matches a minted shape: such an id is never a rename candidate,
   which is what the predicate decides.
5. **Counts reconcile.** Nodes in == out; every in-scope node keeps exactly one
   key; the renames plus the untouched canonical ids account for every in-scope
   node; the edge list is preserved verbatim (compared as a digest, not by
   identity) and a non-list ``edges`` is refused rather than read as empty; the
   dump's own ``node_count``/``edge_count`` still match the lists it carries.
6. **Bound to its snapshot.** The plan carries ``dump_sha256`` over each node's
   ``dump_id``, labels and contract key **and over the edge list**, and
   :func:`apply_plan` recomputes it and refuses a mismatch. ``dump_id`` is the
   store's INTERNAL id — positional and reused after deletions — so a plan
   applied to a refreshed snapshot of the same size would rewrite a different
   node's key and every count would still reconcile; and a revert must restore
   the original *topology*, not only the original keys.

## What this module does NOT do, and says so

* **It does not touch the record.** A node's Postgres row is a projection of
  the journal; a rename reaches it through the write path, not through a second
  writer here (``#7951``/``#7969`` own that direction).
* **It does not journal the rename.** §1.4 rule 2 requires the LIVE pass to be
  a journal event so ``replay(journal)`` reproduces it — a write-path change
  (``#7951``), not a snapshot transform. The plan this module produces **is**
  the payload that pass would journal: the old→new pairs, one per node. ⚠️ A
  snapshot rewritten by this tool and restored into a graph is one whose ids the
  journal does not yet carry, so ``rebuild == live`` holds only once that door
  exists.
* **It does not re-key ``:Event`` or ``:Session``** — see :data:`DECLARED_OUT_OF_SCOPE`.
* **It does not decide whether the pass should run.** The owner's 2026-10-10
  scope ruling and the launch criterion put it at P1 (``#8607``); this module
  is the mechanics, not the go-ahead.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tortoise.cache_projection import KEY_PROPERTY
from tortoise.sdk import _CROCKFORD_ULID_RE, _ULID_RE, _is_canonical_entity_id


def _derived_legacy_handle(label: str, legacy: str) -> str:
    """``label[:3]-<sha256[:26]>`` for a LEGACY key — migration-only (#8607).

    ⛔ #3590 retired this spelling as an ENTITY id: nothing derives identity from
    a name any more, because ``obj-<sha26(name)>`` made a rename mint a second
    entity and a delete+re-create resurrect the first one. This function exists
    for exactly one job, and it is not a create path: it maps an ALREADY-EXISTING
    legacy node key (``p-NNNNNNN``, ``EV-*``) to a deterministic, recomputable
    handle so the offline re-key is idempotent and reviewable. It is defined
    HERE — not in ``tortoise/ids.py`` — so no entity writer can reach for it.

    Never call this from a write path. Use ``ids.ulid()`` (a minted opaque id).
    """
    digest = hashlib.sha256(f"{label}:{legacy}".encode()).hexdigest()[:26]
    return f"{label[:3].lower()}-{digest}"


#: Census buckets.
CANONICAL = "canonical"
LEGACY = "legacy"
OUT_OF_SCOPE = "out-of-scope"
#: The bucket key for a node whose labels the record's key contract does not
#: key at all. A real ``dump_graph`` artifact carries these (``:GraphEvent``,
#: ``Meta``), so they are COUNTED, never refused.
UNCONTRACTED = "<out-of-contract>"
#: :func:`scope_of` values. ``UNDECLARED`` is a label the record keys with no
#: recorded decision — never silently treated as out of scope.
REKEYED = "rekey"
DECLARED_OUT = "declared-out"
UNDECLARED = "undeclared"
#: The bucket for a node :func:`_node_label` cannot resolve at all (no key,
#: ambiguous identity, a structural defect). Its own field, because a node that
#: is counted in NO bucket breaks the ledger identity :meth:`Census.ledger` states.
UNRESOLVED = "<unresolved>"

#: The labels this pass may re-key, each with the reason it is in scope.
#:
#: **The scope is a recorded decision, not a side effect of a property name.**
#: An earlier revision keyed scope on the property name (``id``/``eventId``),
#: which silently swept in ``:Session`` — whose ``id`` is caller-supplied — and
#: declared ``:Event`` re-keyable, whose ``eventId`` has no sanctioned re-keying
#: path at all. Both are refused here, with the reason in
#: :data:`DECLARED_OUT_OF_SCOPE`.
REKEY_SCOPE: dict[str, str] = {
    "Point": (
        "identity `id` minted by `tortoise/ids.py::ulid()`, and §1.4 rule 1 is "
        "the recorded authorisation to rename a legacy key value"
    ),
}

#: Every other label, with the reason it is counted and never rewritten.
DECLARED_OUT_OF_SCOPE: dict[str, str] = {
    "Event": (
        "`eventId` is the journal-keyed Event identity and there is NO "
        "sanctioned re-keying path — `sdk.py` states it in the guard's comment "
        "and the guard raises “'eventId' is server-managed (Event identity) "
        "and cannot be set via props.” — and `id` MIRRORS `eventId` (§1.1) so "
        "a one-sided rename leaves the legacy id addressable; the journaled "
        "pass (#7951) must own it"
    ),
    "Session": (
        "`id` is the caller-supplied MERGE key (`session_<hex12>` when the SDK "
        "mints the default, otherwise the customer's own id), i.e. content the "
        "harness will supply again — §1.4: opaque and valid"
    ),
    # ⚠️ The key cited here is `id`, NOT `name`. §1.1's `:Subject`/`:Object` row is
    # the pre-#3590 key and carries a CORRECTION (2026-10-24, #9881): #3590 made
    # `name` a MUTABLE natural key two live entities may share, and #9881 moved the
    # record with it — `public.subject.node_key` / `public.object.node_key` ARE the
    # `id` (the `node_key = id` conjunct of
    # `CHECK (name IS NOT NULL AND id IS NOT NULL AND node_key = id)`), and
    # ``cache_projection.KEY_PROPERTY`` reads `id` for both labels. So they are
    # re-keyable BY THE CONTRACT and are excluded by the SCOPE RULING alone —
    # quoting the retired ground would leave a reader unable to tell whether the
    # exclusion is a decision or a stale inference. It is a decision (2026-10-10).
    "Subject": (
        "excluded by the 2026-10-10 scope ruling, not by its key: §1.1's name-keyed "
        "row is superseded (#9881) and the contract key is `id`, so a rename here "
        "would be in-contract — it is out of THIS pass by decision"
    ),
    "Object": (
        "excluded by the 2026-10-10 scope ruling, not by its key: §1.1's name-keyed "
        "row is superseded (#9881) and the contract key is `id`, so a rename here "
        "would be in-contract — it is out of THIS pass by decision"
    ),
    "Source": "keyed on `url` (§1.1)",
}


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
        ``label[:3]-<sha256[:26]>``, what ``_derived_legacy_handle`` emits
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

    ⚠️ This is only applied to labels in :data:`REKEY_SCOPE`. It is **not** a
    census of every minting surface: ``:Event``'s ``ev_<sha256[:62]>``
    (``_session_capture_event_id``) does not match it, which is one more reason
    ``:Event`` is out of scope by declaration rather than by predicate.

    ⚠️ It is the label-agnostic UNION (``_is_ulid or _CANONICAL_ENTITY_ID_RE``),
    so after a pass a ``:Point`` population reads canonical under TWO spellings
    — the minted ULID and the ``poi-`` entity handle (§ "What that target IS").
    That union is what makes a second run a no-op; it is not evidence that the
    pass produced the minted spelling.
    """
    return isinstance(value, str) and shape(value) != LEGACY


def canonical_id(label: str, legacy: str) -> str:
    """The canonical id a legacy key is renamed to — ``_derived_legacy_handle``'s.

    Deterministic and total (one value per ``(label, legacy)``), which is what
    makes the pass idempotent and the plan reviewable: a reader recomputes it.

    ⚠️ The shape is ``label[:3]-<sha256[:26]>`` (``poi-…`` for ``:Point``), i.e.
    the **entity-handle** spelling — NOT the ``<ts-hex>-<uuid12>`` a created
    point is minted with. See the module docstring, "What that target IS".
    """
    return _derived_legacy_handle(label, legacy)


# ── dump access (the same accessors ``retype`` uses, so both read one shape) ─


def scope_of(label: str) -> str:
    """``"rekey"`` · ``"declared-out"`` · ``"undeclared"`` — the label's decision.

    A label the record keys but that neither :data:`REKEY_SCOPE` nor
    :data:`DECLARED_OUT_OF_SCOPE` names has **no recorded decision**, so it is
    its own bucket (and a refusal, in :func:`build_plan`) rather than being
    absorbed into "out of scope" — silently absorbing it would attribute a
    future label to a decision nobody made.
    """
    if label in REKEY_SCOPE:
        return REKEYED
    if label in DECLARED_OUT_OF_SCOPE:
        return DECLARED_OUT
    return UNDECLARED


def _props(node: Mapping[str, Any]) -> dict:
    props = node.get("props")
    return props if isinstance(props, dict) else {}


def _labels(node: Mapping[str, Any]) -> list[str]:
    labels = node.get("labels")
    return [str(x) for x in labels] if isinstance(labels, list) else []


def _node_label(node: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """``(label, problem)`` — the ONE contract label this node is keyed by.

    * a label set the record's contract does not key at all →
      ``(UNCONTRACTED, None)``: counted, never refused (a real dump carries
      ``:GraphEvent`` and ``Meta``);
    * two labels that carry **different** key properties, each present →
      a problem (the identity is genuinely undecidable);
    * no key present and more than one contract label → a problem, whatever the
      order of the ``labels`` array (a fallback to ``known[0]`` would make the
      same malformed node a problem or a silent skip depending on list order);
    * exactly one label claims a key, or exactly one contract label →
      that label.
    """
    dump_id = node.get("dump_id")
    labels = _labels(node)
    if not labels:
        return None, f"dump_id {dump_id!r} carries no label"
    known = [label for label in labels if label in KEY_PROPERTY]
    if not known:
        return UNCONTRACTED, None
    props = _props(node)
    claiming = [
        label
        for label in known
        if isinstance(props.get(KEY_PROPERTY[label]), str)
        and str(props.get(KEY_PROPERTY[label])).strip()
    ]
    # A multi-label node that claims two DIFFERENT key properties is ambiguous.
    # `:Point` + `:Session` both key on `id`, which is one key, not two.
    if len({KEY_PROPERTY[label] for label in claiming}) > 1:
        return None, (
            f"dump_id {dump_id!r} is ambiguous: labels {sorted(labels)} carry "
            "different contract keys ("
            + ", ".join(f"{lab}.{KEY_PROPERTY[lab]}" for lab in claiming)
            + ")"
        )
    # An IN-SCOPE label present but keyless is a problem even when another
    # label supplies a key: a `:Point` with no `id` is unaddressable in the
    # record, and whether it is refused must not depend on a co-label being
    # name-keyed (that is the "problem or a silent skip by list order" defect).
    unkeyed_in_scope = [label for label in known if label in REKEY_SCOPE and label not in claiming]
    if unkeyed_in_scope:
        legend = unkeyed_in_scope[0]
        return None, (
            f"dump_id {dump_id} is a :{legend} with no `{KEY_PROPERTY[legend]}` — "
            "the layer's identity property; it cannot be normalised or "
            f"addressed (labels {sorted(labels)})"
        )
    # ⛔ SCOPE IS DECIDED OVER THE LABEL SET, NEVER BY ARRAY ORDER. A node that
    # carries ANY out-of-scope label's key is out of scope: `:Point` + `:Session`
    # share ONE key (`id`), and that id is the caller-supplied session key, so
    # renaming it is a client migration (§1.4 rule 3) whichever way the dump
    # happens to order its labels. `dump_graph` preserves FalkorDB's order, so a
    # `claiming[0]` fallback here made the verdict — and the census bucket —
    # depend on that order.
    out_claiming = [label for label in claiming if label not in REKEY_SCOPE]
    if out_claiming:
        return out_claiming[0], None
    if claiming:
        return claiming[0], None
    if len(known) > 1:
        return None, (
            f"dump_id {dump_id!r} carries {sorted(labels)} and none of their "
            "contract keys — its identity is undecidable"
        )
    return known[0], None


def _edge_digest(edges: object) -> str:
    """sha256 over the edge list, order-insensitively.

    Folded into the snapshot fingerprint: a revert must restore the original
    **topology**, not only the original keys, and `restore_graph` links edges by
    `dump_id`, so a mutated edge list is a different artifact with identical
    node keys.
    """
    rows = []
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                rows.append(["<malformed>", repr(edge)])
                continue
            try:
                # Mixed-type keys and non-JSON values are not reachable from a
                # PARSED dump, but a Python-constructed one can carry them — and
                # a traceback is not the refusal this module promises.
                keys = sorted(edge, key=repr)
                rows.append(
                    [str(k) for k in keys] + [json.dumps(edge[k], sort_keys=True) for k in keys]
                )
            except (TypeError, ValueError):
                rows.append(["<unserialisable>", repr(edge)])
    rows.sort()
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()


def _dump_fingerprint(dump: Mapping[str, Any]) -> str:
    """sha256 over each node's ``dump_id``, labels and contract key, plus edges.

    ``dump_id`` alone is not a snapshot identity (positional, and reused after
    deletions); the node's own key is what the plan rewrites, so binding both
    turns a stale plan into a MISMATCH instead of a silent rewrite of a
    different node whose counts would still reconcile. The edge digest is here
    for the same reason on the other half of the artifact.
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
                    ""
                    if label is None or label not in KEY_PROPERTY
                    else str(props.get(KEY_PROPERTY[label]) or ""),
                ]
            )
    rows.sort()
    blob = json.dumps(rows, separators=(",", ":")) + "|" + _edge_digest(dump.get("edges"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── the census ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Census:
    """What is in the snapshot, counted — the number a pass is planned against."""

    nodes: int = 0
    #: Nodes whose label is in :data:`REKEY_SCOPE` — the population the plan and
    #: its ledger are over.
    in_scope: int = 0
    canonical: int = 0
    legacy: int = 0
    canonical_shapes: dict[str, int] = field(default_factory=dict)
    legacy_shapes: dict[str, int] = field(default_factory=dict)
    #: ``label -> count`` for the labels :data:`DECLARED_OUT_OF_SCOPE` names.
    out_of_scope: dict[str, int] = field(default_factory=dict)
    #: ``label set -> count`` for nodes the record's key contract does not key.
    uncontracted: dict[str, int] = field(default_factory=dict)
    #: ``label -> count`` for a label the record keys that NEITHER
    #: :data:`REKEY_SCOPE` nor :data:`DECLARED_OUT_OF_SCOPE` names — a missing
    #: decision, never silently "out of scope".
    undeclared: dict[str, int] = field(default_factory=dict)
    #: Nodes :func:`_node_label` cannot resolve (no key, ambiguous identity).
    #: Counted here so the ledger identity holds for a malformed snapshot too:
    #: ``nodes == in_scope + Σout_of_scope + Σuncontracted + Σundeclared
    #: + Σunresolved``.
    unresolved: dict[str, int] = field(default_factory=dict)
    #: ``label -> {canonical|legacy|out-of-scope|undeclared: count}``
    by_label: dict[str, dict[str, int]] = field(default_factory=dict)

    def ledger(self) -> dict[str, int]:
        """Every node in exactly one bucket — ``nodes`` must equal ``accounted``.

        A census whose parts do not sum to the whole is not a count, it is a
        sample with an unnamed remainder. ``in_scope`` equals
        ``canonical + legacy`` (a node enters it once and is then sorted).
        """
        accounted = (
            self.in_scope
            + sum(self.out_of_scope.values())
            + sum(self.uncontracted.values())
            + sum(self.undeclared.values())
            + sum(self.unresolved.values())
        )
        return {"nodes": self.nodes, "accounted": accounted, "balanced": self.nodes == accounted}

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "in_scope": self.in_scope,
            "canonical": self.canonical,
            "legacy": self.legacy,
            "out_of_scope": dict(sorted(self.out_of_scope.items())),
            "uncontracted": dict(sorted(self.uncontracted.items())),
            "undeclared": dict(sorted(self.undeclared.items())),
            "unresolved": dict(sorted(self.unresolved.items())),
            "ledger": self.ledger(),
            "canonical_shapes": dict(sorted(self.canonical_shapes.items())),
            "legacy_shapes": dict(sorted(self.legacy_shapes.items())),
            "by_label": {
                label: dict(sorted(counts.items()))
                for label, counts in sorted(self.by_label.items())
            },
        }


def _bump(bucket: dict[str, int], key: str) -> None:
    bucket[key] = bucket.get(key, 0) + 1


def census(dump: Mapping[str, Any]) -> Census:
    """Count the snapshot's identity keys by bucket and by label.

    Reads, never rewrites. Uses the **same** key accessor as
    :func:`build_plan`, so the two can never disagree about what is legacy.
    """
    nodes = dump.get("nodes")
    if not isinstance(nodes, list):
        raise LegacyIdError(["the dump carries no `nodes` list — nothing to census"])
    canon_shapes: dict[str, int] = {}
    legacy_shapes: dict[str, int] = {}
    out_of_scope: dict[str, int] = {}
    uncontracted: dict[str, int] = {}
    undeclared: dict[str, int] = {}
    unresolved: dict[str, int] = {}
    by_label: dict[str, dict[str, int]] = {}
    in_scope = canonical = legacy = 0
    for node in nodes:
        if not isinstance(node, dict):
            _bump(unresolved, "<malformed-row>")
            _bump(by_label.setdefault(UNRESOLVED, {}), OUT_OF_SCOPE)
            continue
        label, problem = _node_label(node)
        if label is None:
            _bump(unresolved, problem or "unresolved")
            _bump(by_label.setdefault(UNRESOLVED, {}), OUT_OF_SCOPE)
            continue
        if label == UNCONTRACTED:
            _bump(uncontracted, ",".join(sorted(_labels(node))) or "<no-labels>")
            _bump(by_label.setdefault(UNCONTRACTED, {}), OUT_OF_SCOPE)
            continue
        scope = scope_of(label)
        if scope == UNDECLARED:
            _bump(undeclared, label)
            _bump(by_label.setdefault(label, {}), UNDECLARED)
            continue
        if scope != REKEYED:
            _bump(out_of_scope, label)
            _bump(by_label.setdefault(label, {}), OUT_OF_SCOPE)
            continue
        in_scope += 1
        raw = _props(node).get(KEY_PROPERTY[label])
        value = raw.strip() if isinstance(raw, str) else ""
        if not value:
            # `_node_label` already refuses an in-scope label with no key, so
            # this is unreachable; it is kept as a counted hole rather than a
            # silent `continue` in case that rule ever narrows.
            _bump(unresolved, f"{label}.{KEY_PROPERTY[label]} missing")
            _bump(by_label.setdefault(UNRESOLVED, {}), OUT_OF_SCOPE)
            in_scope -= 1
            continue
        name = shape(value)
        if name == LEGACY:
            legacy += 1
            _bump(legacy_shapes, name)
            _bump(by_label.setdefault(label, {}), LEGACY)
        else:
            canonical += 1
            _bump(canon_shapes, name)
            _bump(by_label.setdefault(label, {}), CANONICAL)
    return Census(
        nodes=len(nodes),
        in_scope=in_scope,
        canonical=canonical,
        legacy=legacy,
        canonical_shapes=canon_shapes,
        legacy_shapes=legacy_shapes,
        out_of_scope=out_of_scope,
        uncontracted=uncontracted,
        undeclared=undeclared,
        unresolved=unresolved,
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
        """True iff every in-scope node is already canonical — a fixed point."""
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
    seen_dump_ids: dict[int, str] = {}

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
        if dump_id in seen_dump_ids:
            problems.append(
                f"dump_id {dump_id} is carried by two nodes — the dump is not "
                "addressable, so every edge on it is ambiguous"
            )
            continue
        seen_dump_ids[dump_id] = str(node.get("labels"))
        label, problem = _node_label(node)
        if problem is not None:
            problems.append(problem)
            continue
        if label is None or label == UNCONTRACTED:
            # The record does not key it — counted by `census`, never rewritten.
            continue
        scope = scope_of(label)
        if scope == UNDECLARED:
            problems.append(
                f"label {label!r} is keyed by the record's contract "
                f"(`{KEY_PROPERTY[label]}`) but is in neither REKEY_SCOPE nor "
                "DECLARED_OUT_OF_SCOPE — record the decision before normalising"
            )
            continue
        if scope != REKEYED:
            # Declared out of scope, with a reason in DECLARED_OUT_OF_SCOPE —
            # counted by `census`, never rewritten.
            continue
        key_prop = KEY_PROPERTY[label]
        raw = _props(node).get(key_prop)
        if not isinstance(raw, str) or not raw.strip():
            problems.append(
                f"dump_id {dump_id} is a :{label} with no `{key_prop}` — the "
                "layer's identity property; it cannot be normalised or addressed"
            )
            continue
        if raw != raw.strip():
            # Trimming IS a rename of the key value, and this plan only records
            # renames it can prove: a silent trim would leave a revert unable to
            # restore the artifact, and a re-export would re-introduce it.
            problems.append(
                f"dump_id {dump_id} carries {key_prop}={raw!r} with surrounding "
                "whitespace — fix it in the source before normalising, because a "
                "silent trim is a key change this plan cannot prove on a revert"
            )
            continue
        value = raw
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

    if not problems:
        _check_dump_counts(dump, problems)
    # Invariant 5, the ledger's own halves: the renames and the untouched nodes
    # must account for the whole in-scope population.
    reported = census(dump)
    if not problems:
        if reported.in_scope != len(renames) + reported.canonical:
            problems.append(
                f"partition hole: {reported.in_scope} in-scope nodes but "
                f"{len(renames)} renames + {reported.canonical} canonical — "
                "some node is counted in neither bucket"
            )
        if len(renames) != reported.legacy:
            problems.append(
                f"partition hole: {len(renames)} renames planned for {reported.legacy} legacy ids"
            )

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
    population that is not the graph's. A non-list ``edges``/``nodes`` is
    refused here too: reading it as empty is the silent-drop this module exists
    not to be.
    """
    for declared, key in (("node_count", "nodes"), ("edge_count", "edges")):
        if key in dump and not isinstance(dump.get(key), list):
            problems.append(
                f"the dump's `{key}` is not a list ({type(dump.get(key)).__name__}) "
                "— reading it as empty would silently drop it"
            )
            continue
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
    problems: list[str] = []
    if not isinstance(nodes_in, list):
        raise LegacyIdError(["the dump carries no `nodes` list"])
    edges_in = dump.get("edges", [])
    if not isinstance(edges_in, list):
        raise LegacyIdError(
            [
                f"the dump's `edges` is not a list ({type(edges_in).__name__}) — "
                "reading it as empty would silently drop every edge"
            ]
        )
    # A SNAPSHOT of the edges, taken before any node is rewritten. `out` below
    # shares the input's `edges` list object, so re-digesting that object after
    # the loop cannot detect an in-place edit to an edge row — only a copy can.
    edges_before = copy.deepcopy(edges_in)

    by_dump_id: dict[int, Rename] = {rename.dump_id: rename for rename in plan.renames}
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
        if rename.label not in _labels(node):
            problems.append(
                f"dump_id {dump_id} does not carry the planned label "
                f"{rename.label!r} (labels {_labels(node)}) — the plan names a "
                "different node"
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
    # Invariant 5: nodes in == out, edges unchanged, and the counts the dump
    # declares still hold. The edge half is a DIGEST over a pre-pass copy, not
    # an identity comparison: `out["edges"] is edges_in`, so an in-place edit
    # during the loop is invisible to `==` and visible here.
    if len(out_nodes) != len(nodes_in):
        problems.append(f"{len(nodes_in)} nodes in, {len(out_nodes)} out")
    if _edge_digest(out.get("edges", [])) != _edge_digest(edges_before):
        problems.append("the edge list changed — a key rename must not touch edges")
    if "nodes" in dump and not isinstance(dump.get("nodes"), list):
        problems.append("the dump's `nodes` is not a list")
    if "edges" in dump and not isinstance(dump.get("edges"), list):
        problems.append("the dump's `edges` is not a list")
    for declared, rows in (("node_count", out_nodes), ("edge_count", edges_in)):
        if declared not in out:
            continue
        try:
            declared_n = int(out[declared])
        except (TypeError, ValueError):
            problems.append(f"the dump's {declared} is not an integer: {out[declared]!r}")
            continue
        if declared_n != len(rows):
            problems.append(
                f"the output's {declared} ({declared_n}) no longer matches its {len(rows)} row(s)"
            )
    if revert:
        # ⛔ A revert whose plan carries NO renames is not an undo — it is a
        # no-op wearing one. `apply_plan` would apply nothing, and the proof
        # below then compares the unchanged input against a plan built FROM that
        # same input, so it "passes": the run exits 0 and reports a restore that
        # never happened (measured: run documented step 2 twice — the second
        # pass renames nothing and REWRITES the report with `renames: []` — and
        # step 3 then "restores" the normalised dump while the original ids
        # survive in no artifact, because `_derived_legacy_handle` is a sha256 and they
        # are not re-derivable). Refused instead.
        if not plan.renames:
            problems.append(
                "the report carries no renames — it is a no-op pass, not a plan "
                "to undo; the original ids exist only in the report that DOES "
                "carry them, so this revert would report a restore it never made"
            )
        # A revert's own proof: the restored artifact must BE the snapshot the
        # plan was built from, not merely look like it (node keys AND edges).
        restored = _dump_fingerprint(out)
        if restored != plan.dump_sha256:
            problems.append(
                f"the reverted snapshot fingerprints to {restored[:16]}…, not the "
                f"plan's {plan.dump_sha256[:16]}… — it is not the original artifact"
            )

    if problems:
        raise LegacyIdError(problems)

    before = census(dump)
    after = census(out)
    report = {
        "mode": "revert" if revert else "apply",
        "dump_sha256": plan.dump_sha256,
        "nodes_in": len(nodes_in),
        "nodes_out": len(out_nodes),
        "edges_in": len(edges_in),
        "edges_out": len(out.get("edges", [])),
        # The ledger, both directions and both buckets — a caller reconciles on
        # these and never has to infer a count from another.
        "in_scope_in": before.in_scope,
        "in_scope_out": after.in_scope,
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
