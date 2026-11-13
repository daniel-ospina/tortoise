"""#11469 — curation of our own (dogfood) memory: select the signal out of the old
graph and re-file it through the packs into a clean graph.

THE RULING THIS MODULE OBEYS (owner, 2026-11-07, `#11469`)
----------------------------------------------------------
1. the old graph is KEPT — a historic artifact; it is not pruned, not "cleaned",
   not backfilled;
2. the useful data is MIGRATED OUT of it into the clean, pack-filed graph;
3. relocation/relabelling it as an archive is OPTIONAL, after (2);
4. deletion happens only after the new extractor has been measured (`#10175` step 5).

Therefore, in code:

* **The old graph is only ever READ.** Nothing here calls a delete, a retract, a
  supersede or an update on the source. A migration that can destroy the only
  copy of a tenant's data is not a migration.
* **The new graph is only ever ADDED to.** Writes are points/entities/edges; no
  pass over the target removes anything, so a half-finished run leaves the
  target usable and a re-run simply continues.
* **The run is idempotent twice over.** Every carried row gets a stable
  ``migrationKey`` derived from its content hash (a) so the target's own
  content-hash dedup folds a repeated write, and (b) so this module's ledger can
  skip a row it already migrated *before* it reaches the wire. The ledger is
  **scoped to its target graph**: the same row migrated into graph A has not been
  migrated into graph B, and a second target must not silently receive nothing.
* **A write is only ever recorded as written when the target ACCEPTED it.** The
  writer raises on a refusal and reports the keys it accepted; the ledger is
  written from that report, never from the attempt. A ledger that records
  attempts is worse than no ledger — it is permanent silent loss of the only copy.
* **It can be switched off.** ``TORTOISE_CURATE_OFF=1`` (or ``--off``) turns
  every write into a report line. The switch is checked before the run AND
  before each write batch, so flipping it mid-run stops the run at the next batch.

``source_graph == target_graph`` is refused outright — including the case where the
two are the same graph under different accepted spellings (graph id, ``g_<id>``
namespace, ``stable_id``, or the case-insensitive graph NAME the operator actually
types). That check needs the workspace's graph rows, so an ``--apply`` run refuses
when it cannot resolve both refs to a canonical id (fail closed).

Nothing in this module is exported from ``tortoise/__all__`` and no SDK method is
added — the public surface is unchanged (#4282: surface changes need the owner's
approval, and this issue grants none).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from tortoise.env_truthy import is_truthy

#: Env flag that disarms every write. Truthy values only; anything else leaves
#: the run armed, because a switch that silently disarms on a typo is worse than
#: no switch (the safe default is the documented one: armed, and idle unless
#: ``--apply`` is passed).
OFF_SWITCH_ENV = "TORTOISE_CURATE_OFF"

#: Prefix on every ``migrationKey`` this module writes. It makes a migrated row
#: identifiable in the target graph after the fact, without a local ledger.
MIGRATION_KEY_PREFIX = "curate-11469:"

#: Dispositions. ``carry`` = migrate, ``not-carried`` = deliberately left behind
#: (the row is still IN the old graph — nothing is deleted), ``undecidable`` =
#: needs a human/merge decision and is reported, never guessed.
CARRY = "carry"
NOT_CARRIED = "not-carried"
UNDECIDABLE = "undecidable"

DISPOSITIONS = (CARRY, NOT_CARRIED, UNDECIDABLE)

#: Kinds whose instances are owner-level decisions or the strategy artifacts
#: hung off them. These are the rows a curated graph exists to keep. Compared
#: against the BARE kind, because the source carries both spellings
#: (``decision`` and ``core:decision``).
SIGNAL_KINDS = frozenset({
    "decision", "option", "finding", "jobToBeDone", "useCase",
    "userJourney",
})
#: NOT pointKinds, so deliberately absent above: `requirement` is declared ONCE, as
#: an **objectKind**, by the product-strategy manifest — `packs/dev/manifest.yaml`
#: records the #7848 ruling that it is NOT a point kind and that product-strategy
#: owns the declaration — and
#: `workflow` is in `pack_registry.CANONICAL_OBJECT_KINDS` /
#: `extractor_v2.CORE_OBJECT_KEYS` (`core:workflow`) and in no pack's pointKind
#: list. Carrying either as a Point would type a claim with an object kind.

#: Agent-narration markers: a transcript turn, not a claim. Deliberately narrow
#: and quoted literally — a broad "sounds like narrative" heuristic would put
#: real findings in the drop bucket, and the brief's rule is that "messy" is not
#: "disposable".
NARRATIVE_MARKERS = (
    "the user said",
    "the user asked",
    "user: ",
    "assistant: ",
    "tool result",
    "let me ",
    "i'll ",
    "i will now ",
    "<function_calls>",
    "<tool_use>",
)


class CurationRefused(RuntimeError):
    """A guard refused the run, or the target refused a write. Never swallowed.

    ``written_keys`` carries the keys a writer had already had ACCEPTED when the
    refusal happened, so a partial run reports what landed instead of zero.
    """

    def __init__(self, message: str, *, written_keys: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.written_keys = list(written_keys)


def normalise_tool_result(res: Any) -> Any:
    """Unwrap an MCP tool result (fastmcp ``CallToolResult``) into plain JSON.

    The client returns an OBJECT (``content`` / ``structuredContent`` /
    ``isError``), not a dict, so a refusal delivered as an error envelope inside a
    successful result is invisible to any code that only reads dicts — and that is
    exactly how a refused batch gets recorded as a migration. Every consumer of a
    tool result in this change goes through here.
    """
    if isinstance(res, (dict, list, str, int, float)) or res is None:
        return res
    # fastmcp's CallToolResult carries SNAKE_CASE fields (is_error /
    # structured_content); the camelCase spellings are accepted too so a client
    # upgrade in either direction keeps reading the same object.
    is_err = getattr(res, "is_error", getattr(res, "isError", None))
    sc = getattr(res, "structured_content", getattr(res, "structuredContent", None))
    if not sc:
        data = getattr(res, "data", None)
        if isinstance(data, (dict, list)):
            sc = data
    if sc:
        payload: Any = sc.get("result", sc) if isinstance(sc, dict) else sc
    else:
        parts = [getattr(p, "text", "") for p in (getattr(res, "content", None) or [])]
        # Parse PER PART: a write result carries the payload as its first text
        # block and #10084's graph-resolution disclosure as a SECOND one, so a
        # join of the parts is not JSON and would look like an unreadable result.
        payload = None
        for text in parts:
            try:
                payload = json.loads(text)
                break
            except json.JSONDecodeError:
                continue
        if payload is None:
            # Nothing parseable: refuse rather than assume the call succeeded.
            raise CurationRefused(f"MCP tool result was not JSON: {''.join(parts)[:400]}")
    if is_err:
        raise CurationRefused(f"MCP tool error: {str(payload)[:400]}")
    return payload


# ── the normalised row ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Row:
    """One node as a read surface returned it, normalised to what curation needs.

    ``props`` is the source row MINUS the embedding vectors, carried unchanged. No
    classification rule reads it today (a grep for ``props`` in this module finds the
    field and nothing else), so it is NOT what makes a decision re-derivable — that
    is ``content`` + ``stable_key``, both of which ``Decision.as_dict`` emits. It is
    kept as the read-surface record a later rule (or an audit of "what did the source
    actually say") would need, and re-assembly into a valid ingest item uses
    ``content``, which ingest REQUIRES on every point item (``sdk._check_item_shape``)
    and which a bundle without is refused wholesale for.
    """

    node_id: str
    node_type: str  # point | operator | object | event | source | subject
    kind: str | None = None
    content: str = ""
    content_hash: str | None = None
    props: Mapping[str, Any] = field(default_factory=dict)

    @property
    def bare_kind(self) -> str | None:
        """The kind with any pack namespace stripped (``dev:bug`` → ``bug``)."""
        if not self.kind:
            return None
        namespace, sep, bare = self.kind.partition(":")
        return bare if sep and namespace else self.kind

    @property
    def stable_key(self) -> str:
        """Content-addressed key: the same row read twice yields the same key.

        ⛔ EXCEPT for an operator, whose identity is NOT its text. ``create_operator``
        identifies a relationship by ``(op_type, source, targets, label)``, and the
        layer's meaning is its EDGES — the read surface that enumerates it in bulk
        (``tortoise_search(entity_type="operator")``) serves the operator's ``label``
        as ``content`` and exposes no endpoints at all. Content-addressing that class
        folds every label-less operator onto ONE keeper the moment the labels are
        empty (measured on live-v4, 2026-11-13: the layer's first row is an ``IMPL``
        with ``content: ""``), so 3,727 distinct relationships were reported as one
        row plus 3,726 "exact content duplicates" — the reasoning layer summarised as
        a single claim. Nothing is written from this class (it is ``undecidable`` —
        see :func:`classify_point`), so an id-scoped key costs no idempotency: this
        key never reaches a ``migrationKey`` or the ledger.
        """
        if self.node_type == "operator":
            return f"{MIGRATION_KEY_PREFIX}{self.node_type}:{self.kind or '-'}:id:{self.node_id}"
        basis = self.content_hash or hashlib.sha256(
            (self.content or "").strip().encode("utf-8")
        ).hexdigest()
        return f"{MIGRATION_KEY_PREFIX}{self.node_type}:{self.kind or '-'}:{basis}"


# ── pack routing ──────────────────────────────────────────────────────────


def load_pack_kind_map() -> tuple[dict[tuple[str, str], str], str | None]:
    """``({(bucket, kind): pack namespace}, note)`` from the installed pack catalog.

    "File it against the pack that owns its kind" has a real schema behind it —
    the manifests — so the mapping is read from them rather than invented here.
    A pack load failure still degrades to an empty map instead of raising: an
    inventory must not be blocked by a missing catalog. But the failure is
    RETURNED, not merely logged — the census reports it (``pack_catalog_note`` /
    ``rows_without_pack``), because an empty map and a healthy one are otherwise
    indistinguishable in the aggregate report. (Round-29 review finding 3: this
    docstring used to claim ``pack: None`` was "visible in the census" while
    ``census()`` had no pack dimension at all, so a TOTAL catalog failure built a
    fully pack-less plan stamped ``pack: null`` on every row and reported a
    census byte-identical to a healthy run.)
    """
    out: dict[tuple[str, str], str] = {}
    try:
        from tortoise.pack_registry import PackRegistry, default_packs_dir

        reg = PackRegistry(default_packs_dir())
        reg.load_all()
    except Exception as exc:  # degrade visibly, never silently
        return out, (
            f"pack catalog unavailable ({type(exc).__name__}: {exc}) — every row "
            "reports pack: None, so no row is filed against the pack that owns its kind"
        )
    buckets = (
        ("pointKind", "point_kinds"),
        ("objectKind", "object_kinds"),
        ("eventKind", "event_kinds"),
        ("documentKind", "document_kinds"),
        ("actionKind", "action_kinds"),
    )
    for namespace, pack in sorted(reg.packs.items()):
        for bucket, attr in buckets:
            for kind in getattr(pack, attr, ()) or ():
                out.setdefault((bucket, kind), namespace)
    return out, None


def pack_kind_map() -> dict[tuple[str, str], str]:
    """The mapping alone, for callers that do not report on catalog health.

    ``load_pack_kind_map`` is the one that can tell a caller WHY the map is
    empty; this wrapper exists so an existing caller keeps its shape.
    """
    return load_pack_kind_map()[0]


def pack_for(kind: str | None, node_type: str, mapping: Mapping[tuple[str, str], str]) -> str | None:
    """The owning pack namespace for a node's kind, or None when nobody owns it.

    Pack kinds travel either bare (``code``) or namespaced (``dev:code``); both
    are resolved, because the source graph carries both spellings.
    """
    if not kind:
        return None
    bucket = {
        "object": "objectKind",
        "event": "eventKind",
        "source": "documentKind",
    }.get(node_type, "pointKind")
    if ":" in kind:
        namespace, _, bare = kind.partition(":")
        known = {ns for (_, _k), ns in mapping.items() if ns}
        if namespace and namespace in known:
            return namespace
        # An UNKNOWN prefix (`core:decision` — there is no `core` pack) is not an
        # owner: returning it would stamp a migrated node with pack ownership that
        # does not exist. Fall through to the bare kind's owner instead.
        kind = bare
    return mapping.get((bucket, kind))


# ── classification ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Decision:
    node_id: str
    node_type: str
    kind: str | None
    disposition: str
    reason: str
    pack: str | None
    stable_key: str
    content: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "kind": self.kind,
            "disposition": self.disposition,
            "reason": self.reason,
            "pack": self.pack,
            "stable_key": self.stable_key,
            # The TEXT the reason is about. Without it `plan --rows` reports a
            # disposition and a reason over a row the reviewer cannot look up, so
            # "the decision can be re-derived from the report alone" was a claim the
            # artifact did not support (round-36 review, P3-4).
            "content": self.content,
        }


def _narrative(content: str) -> str | None:
    low = content.lower()
    for marker in NARRATIVE_MARKERS:
        if low.startswith(marker) or f"\n{marker}" in low:
            return marker
    return None


def classify_point(
    row: Row,
    packs: Mapping[tuple[str, str], str],
    *,
    duplicate_of: str | None = None,
    keeper_id: str | None = None,
    content_mismatch: bool = False,
) -> Decision:
    """A Point or an operator's disposition.

    The judgement is deliberately conservative: a row is only moved out of the
    ``undecidable`` bucket when a rule the report can quote decides it. Session
    *provenance* alone is NOT noise — the claim extracted from a session may be
    the most durable thing in the graph — so it never decides the disposition.

    A ``duplicate_of`` row follows its KEEPER's disposition rather than a rule of
    its own: "the keeper carries it" must be true, or the report asserts something
    the plan contradicts. ``keeper_id`` is that row's node id, and the reason
    NAMES it — interpolating the keeper's *disposition* instead left a reader of
    ``plan --rows`` with ``(not-carried)`` where the identity belonged, which is
    the one question ``duplicate_of`` exists to answer (round-29 review finding 2).
    """
    pack = pack_for(row.kind, row.node_type, packs)
    key = row.stable_key
    # The keeper's IDENTITY, or an honest admission that the caller did not supply
    # one. Never the disposition — that is what the reason already says.
    who = keeper_id if keeper_id is not None else "a row this pass did not name"

    if duplicate_of is not None and row.node_type != "operator" and content_mismatch:
        # An operator is never folded on content: its key is id-scoped (its text is a
        # label, routinely empty), so a `duplicate_of` on ONE operator row can only come
        # from a shared node id — and a "content hash" reason would then assert a
        # relation that was never computed (round-5 review). The operator branch below
        # decides every operator row unconditionally.
        # A content hash that TWO rows share while their CONTENT differs is not a
        # duplicate relation — it is a hash that cannot be trusted for this pair. Rows
        # are folded by hash alone, so the second text used to be reported as an "exact
        # content duplicate" of a row holding DIFFERENT text, with the reason asserting
        # that keeper was "the row of record for this content" — a false claim over a
        # unique text that the carry set then left behind (round-34 finding 2). The
        # conflict is a per-row decision, not a rule's, so it is UNDECIDABLE.
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        f"shares a content hash with row {who}, whose CONTENT differs — a hash "
                        "that disagrees with the text cannot establish a duplicate, so this "
                        "text has no row of record and needs a per-row decision",
                        pack, key, row.content)

    # ⛔ Reachability of the `!= "operator"` clauses in this block, so none of them
    # reads as protection it does not provide (round-7 review, corrected round-2). ALL
    # FOUR are DEAD for an operator row — each carries `row.node_type != "operator"`,
    # so the operator branch at the bottom decides every operator row unconditionally.
    # `build_plan` is the only caller that passes `duplicate_of`, and it passes a
    # KEEPER's disposition; an operator's keeper can only be another operator under the
    # id-scoped key, which is classified `UNDECIDABLE`, so an operator's `duplicate_of`
    # would be `UNDECIDABLE` — and even that never reaches the `UNDECIDABLE` arm below.
    # They are kept as DEFENCE IN DEPTH, for the day the operator branch moves or is
    # narrowed: without them an operator would silently inherit a content fold (a
    # "duplicate" reason computed over a label) or a keeper's `CARRY`. The
    # `content_mismatch` arm ABOVE and the `UNDECIDABLE` and generic arms here are
    # pinned by the synthetic `duplicate_of=` calls in
    # `test_two_operator_rows_sharing_an_id_do_not_get_a_content_duplicate_reason`; the
    # `CARRY` arm is pinned there too, by its own call — it is the one this block's
    # first wording wrongly called "live", and pinning it is what keeps the claim true.
    if duplicate_of == CARRY and row.node_type != "operator":
        return Decision(row.node_id, row.node_type, row.kind, NOT_CARRIED,
                        "exact content duplicate of a carried row (same content hash); "
                        f"the keeper {who} carries it. MERGE of near-duplicates is #5006's scope, "
                        "not this pass's", pack, key, row.content)
    if duplicate_of == UNDECIDABLE and row.node_type != "operator":
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        "exact content duplicate of a row that is itself undecidable — the "
                        f"content has no carry candidate yet (the keeper {who} is undecidable "
                        "too), so this row is not carried either",
                        pack, key, row.content)
    if duplicate_of is not None and row.node_type != "operator":
        # The keeper was not carried: neither is its duplicate, and the reason names
        # the keeper rather than claiming a carry that did not happen.
        body = (
            f"exact content duplicate of a row that was not carried ({duplicate_of})"
        )
        if str(row.content or "").strip():
            # Only claim the keeper is the ROW OF RECORD when it actually holds the
            # content. The promotion loop guarantees it does for a content-bearing
            # duplicate, but an all-empty duplicate group has no holder at all, and
            # asserting one would be the round-30 finding 2 lie in the other
            # direction.
            body = f"{body}; the keeper {who} is the row of record for this content"
        else:
            body = f"{body}; this row carries no content either"
        return Decision(row.node_id, row.node_type, row.kind, NOT_CARRIED,
                        body, pack, key, row.content)

    if row.node_type == "operator":
        # ⛔ REACHED, and NOT a carrier. Two corrections to what this branch used to
        # say, both measured on live-v4 2026-11-13 and recorded in
        # `docs/architecture/CURATION-11469.md`:
        #
        # 1. The layer IS readable. `tortoise_search(entity_type="operator",
        #    limit=5000)` answers **3,727 rows in one call**, an exact match with
        #    `structure.operators`, with no vector leg. The old note's escape hatch —
        #    "unless an operator kind is requested" — returns `total: 0` for both
        #    `kind="operator"` and `kind="IMPL"`, so it named a door that is not a
        #    door. `tools/curate_graph.py` reads the layer now.
        # 2. Reading it does NOT make it carryable. An operator's meaning is its
        #    ENDPOINTS — `label` + `op_type` + the source/target ids its edges bind —
        #    and the bulk ENUMERATION does not carry them: `relationships` is decorated
        #    only for `entity_type="point"` (`sdk.py` step 7.5), so the empty list on an
        #    operator row is a construction artifact, not a measurement, and
        #    `audit.edge_count` is a count only. The endpoints ARE readable one operator
        #    at a time (`tortoise_expand_relationships`), which is 3,727 calls this pass
        #    does not fan out. A carried operator therefore arrives in the clean graph
        #    as a FLOATING node, which is the one shape this issue forbids ("Every
        #    retained fact is connected ... not a floating node"). The write side is not
        #    short of a section either: `ingest` has had `connections` since #888 W4 —
        #    what is missing is the endpoint data to build them from, at this fan-out.
        #
        # Checked BEFORE the empty-content gate on purpose: an operator's `content` is
        # its label, and an empty label says nothing about whether the relationship
        # matters — so "nothing to carry" is the wrong answer for it. It is undecidable
        # until the endpoints are readable.
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        "operator (IMPL/NAND/composedOf) — the reasoning layer, and NOT "
                        "carried: its meaning is its endpoints, and the bulk enumeration "
                        "does not carry them (relationships are decorated for "
                        "entity_type='point' only, and audit.edge_count is a count only); "
                        "they are readable one operator at a time via "
                        "tortoise_expand_relationships, which this pass does not fan out. "
                        "Carrying it as a point would arrive as a floating node. Needs "
                        "endpoint data #888 W4's ingest `connections` section writes from",
                        pack, key, row.content)

    if not (row.content or "").strip():
        return Decision(row.node_id, row.node_type, row.kind, NOT_CARRIED,
                        "empty content — nothing to carry (checked before every kind rule: an "
                        "empty row would otherwise be carried as an empty point)", pack, key, row.content)

    if row.bare_kind in SIGNAL_KINDS:
        return Decision(row.node_id, row.node_type, row.kind, CARRY,
                        f"{row.bare_kind} — an owner-level artifact the curated graph exists to keep",
                        pack, key, row.content)

    if row.bare_kind == "diary":
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        "agent diary — narration shaped like a narrative, but it is an authored "
                        "artifact, so neither rule decides it", pack, key, row.content)

    if row.bare_kind == "evidence":
        return Decision(row.node_id, row.node_type, row.kind, CARRY,
                        "evidence — carried; its Source tier must be resolved at ingest "
                        "(the source layer is 100% session-backed, see the inventory)",
                        pack, key, row.content)

    marker = _narrative(row.content or "")
    if marker:
        return Decision(row.node_id, row.node_type, row.kind, NOT_CARRIED,
                        f"turn narration, not a claim (matched marker {marker!r})",
                        pack, key, row.content)

    return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                    "statement with no deciding rule: carrying it is a per-row judgement "
                    "(reported for review, not guessed)", pack, key, row.content)


#: The census' own drop-shape rule (#5059 / #11021 / #11211): a noun phrase with
#: a definite article, a path, a symbol or an identifier is a mention, not an
#: entity with a referent.
def is_bare_definite(name: str) -> bool:
    low = (name or "").strip()
    return (
        low.startswith("the ")
        or "/" in low
        or "#" in low
        or ".py" in low
        or "_" in low
    )


def classify_object(row: Row, packs: Mapping[tuple[str, str], str]) -> Decision:
    pack = pack_for(row.kind, row.node_type, packs)
    key = row.stable_key
    if is_bare_definite(row.content):
        return Decision(row.node_id, row.node_type, row.kind, NOT_CARRIED,
                        "bare definite description / path / identifier — a mention, not an "
                        "entity (#11021/#11211); it refers to nothing to connect to",
                        pack, key, row.content)
    return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                    "object with a referent-shaped name: whether it is an entity the curated "
                    "graph needs is a judgement, not a rule", pack, key, row.content)


def classify_source(row: Row, packs: Mapping[tuple[str, str], str]) -> Decision:
    pack = pack_for(row.kind, row.node_type, packs)
    key = row.stable_key
    if str(row.content).startswith("session:") or row.kind == "agentSession":
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        "session transcript used as a Source: §9.1/D1 (#10000) says the raw "
                        "tier lives in Postgres, not in the graph — but this tenant's record "
                        "holds 0 rows (#10175), so the graph is the only copy and 'do not "
                        "carry it' would leave the transcript with no home. Needs a decision, "
                        "not a rule", pack, key, row.content)
    if not str(row.content or "").strip():
        return Decision(row.node_id, row.node_type, row.kind, NOT_CARRIED,
                        "empty url — a :Source is keyed by its url, and ingest refuses a "
                        "source with no url (whole-section refusal)", pack, key, row.content)
    if not row.kind:
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        "source with no sourceKind — a :Source requires one at ingest, so it "
                        "cannot be carried until the kind is resolved", pack, key, row.content)
    return Decision(row.node_id, row.node_type, row.kind, CARRY,
                    "a real provenance artifact (not a session transcript)", pack, key, row.content)


def classify_event(row: Row, packs: Mapping[tuple[str, str], str]) -> Decision:
    pack = pack_for(row.kind, row.node_type, packs)
    key = row.stable_key
    if row.kind in (None, "", "session", "agentSession") or str(row.content).startswith("session:"):
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        "agent-session Event: the raw/episodic tier is supposed to live outside "
                        "the graph (#10000), but the record holds 0 rows for this tenant, so "
                        "dropping it here has no safe destination", pack, key, row.content)
    return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                    "a domain Event, but this harness's write path has no `events` section "
                    "today — counting it as carried while nothing sends it is the silent "
                    "no-op this pass must not have (follow-up on #11469)", pack, key, row.content)


def classify(
    row: Row,
    packs: Mapping[tuple[str, str], str],
    *,
    duplicate_of: str | None = None,
    keeper_id: str | None = None,
    content_mismatch: bool = False,
) -> Decision:
    if row.node_type in ("point", "operator"):
        return classify_point(row, packs, duplicate_of=duplicate_of,
                              keeper_id=keeper_id, content_mismatch=content_mismatch)
    if row.node_type == "object":
        return classify_object(row, packs)
    if row.node_type == "source":
        return classify_source(row, packs)
    if row.node_type == "event":
        return classify_event(row, packs)
    return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                    "no classifier for this node type in this pass",
                    pack_for(row.kind, row.node_type, packs), row.stable_key, row.content)


# ── the plan ──────────────────────────────────────────────────────────────


@dataclass
class Plan:
    """A re-derivable migration plan: every source row with its disposition."""

    source_graph: str
    target_graph: str
    decisions: list[Decision] = field(default_factory=list)
    #: Why the pack mapping is empty, when it is empty because the catalog could
    #: not be read. ``None`` when a mapping was supplied or the catalog loaded.
    pack_catalog_note: str | None = None

    def census(self) -> dict[str, Any]:
        """Counts per (node_type, kind, disposition) — the inventory table.

        It also carries the PACK dimension the pack routing can fail on, because
        without it a total pack-catalog failure was unreportable: every row came
        out with ``pack: None`` and the census was byte-identical to a healthy
        run's (round-29 review finding 3).

        ``rows_without_pack`` counts **CARRIED** rows only, and that population is
        the whole point: it is the set that will actually be WRITTEN, so it is the
        set a pack loss can damage. Counting every decision made the field a
        function of the population that is deliberately left behind instead —
        measured against the real catalog, a HEALTHY plan over the documented
        carry set reported 125 unfiled of 127 (only the strategy kinds resolve to
        ``product-strategy``), so "the catalog is gone" and "the catalog is fine"
        differed by 1.6% and the field reported nothing (round-30 review finding
        1).
        """
        by: dict[str, dict[str, int]] = {}
        for d in self.decisions:
            cls = f"{d.node_type}:{d.kind or '-'}"
            slot = by.setdefault(cls, {k: 0 for k in DISPOSITIONS})
            slot[d.disposition] = slot.get(d.disposition, 0) + 1
        totals = {k: 0 for k in DISPOSITIONS}
        for slot in by.values():
            for k, v in slot.items():
                totals[k] = totals.get(k, 0) + v
        carried = self.carry_rows()
        unfiled = sum(1 for d in carried if not d.pack)
        return {
            "source_graph": self.source_graph,
            "target_graph": self.target_graph,
            "total_rows": len(self.decisions),
            "totals": totals,
            "by_class": dict(sorted(by.items())),
            "carried_rows": len(carried),
            "rows_without_pack": unfiled,
            "pack_catalog_note": self.pack_catalog_note,
        }

    def carry_rows(self) -> list[Decision]:
        return [d for d in self.decisions if d.disposition == CARRY]

    def digest(self) -> str:
        """A digest of the plan's carry-set: two runs of the same source agree."""
        basis = "\n".join(sorted(d.stable_key for d in self.carry_rows()))
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def bundle(self) -> dict[str, Any]:
        """The additive write bundle: a ``points`` section and a ``sources`` section.

        ``content`` is not optional — ``sdk._check_item_shape`` refuses a point
        item without it (Phase 1, so a whole bundle is rejected, not one row).
        Connection wiring (which claim serves as evidence for which) is NOT built
        here: the bundle is rows, not edges. ⚠️ That is no longer because the write
        side is missing — ``ingest`` has carried a ``connections`` section since #888
        W4, which landed — but because the READ side does not carry the endpoints the
        connections would be built from in bulk: the operator layer enumerates (3,727
        rows, 2026-11-13) with its ``label`` as content, ``relationships`` is decorated
        for ``entity_type="point"`` only, and ``audit.edge_count`` is a count only. An
        operator is therefore ``undecidable`` (see :func:`classify_point`), never a
        floating point. Never a deletion.
        """
        representable = {"point", "operator", "source"}
        strays = sorted({d.node_id for d in self.carry_rows() if d.node_type not in representable})
        if strays:
            # Fail closed. A carried row with no wire section reports as migrated and
            # is never sent — the round-5 defect, one node type over. Counting it is
            # worse than refusing to build the bundle.
            raise ValueError(
                f"carried rows with no wire section: {strays[:5]} — refusing to build a "
                "bundle that would silently drop them")
        return {
            "migrationKeyPrefix": MIGRATION_KEY_PREFIX,
            "sourceGraph": self.source_graph,
            "digest": self.digest(),
            # NO `ref` at all. A bundle-local ref is expanded wherever it appears
            # as a field VALUE, so ANY ref we mint can collide with a row whose
            # content or id happens to equal it — `ref == migrationKey` made every
            # item expand into itself, and a ref like "cur0" collides with a row
            # whose content is "cur0" — and ingest then refuses the WHOLE bundle
            # ("ref expansion exceeded depth 32"). Refs exist to address one item
            # from another (a connection); this bundle mints none, which makes the
            # collision impossible rather than unlikely.
            #
            # SOURCES GO IN THEIR OWN SECTION: a carried Source is provenance
            # (D10 — a document IS a :Source). Writing it as a Point would re-type
            # it and give it a document-vocabulary pointKind.
            "points": [
                {
                    "migrationKey": d.stable_key,
                    # VERBATIM, including a pack-namespaced spelling (`core:decision`) and
                    # an undeclared kind (`finding`). Deliberate, and RECORDED rather than
                    # re-shaped (round-33 review follow-up 2): a strip here would silently
                    # re-type rows the source declared namespaced, on a write path whose
                    # whole contract is fidelity. Ingest does not silently drop either
                    # spelling, but by TWO mechanisms — a bare unrecognised pointKind is
                    # WARNED about and stored, a kind naming a pack the target has not
                    # installed is REFUSED (`_refuse_unapproved_kind`), so a namespaced
                    # spelling is not free. ⚠️ Classification is NOT spelling-invariant:
                    # `classify_point` uses `bare_kind`, but `classify_source` and
                    # `classify_event` compare the EXACT `row.kind` — a `core:agentSession`
                    # source is `carry` where a bare `agentSession` is `undecidable`. What
                    # the clean target's kind vocabulary should accept is the clean graph's
                    # decision (#10175 step 2), not this pass's.
                    "kind": d.kind or "statement",
                    "content": d.content,
                    "sourceNodeId": d.node_id,
                    "pack": d.pack,
                }
                for d in self.carry_rows()
                if d.node_type in ("point", "operator")
            ],
            # A :Source accepts only DECLARED props (#3998 drops undeclared ones
            # silently), so the migration key is NOT written onto the node: the
            # local ledger carries identity, and the target merges Sources by url.
            "sources": [
                {"url": d.content, "sourceKind": d.kind}
                for d in self.carry_rows()
                if d.node_type == "source"
            ],
        }


def build_plan(
    rows: Iterable[Row],
    *,
    source_graph: str,
    target_graph: str,
    packs: Mapping[tuple[str, str], str] | None = None,
) -> Plan:
    """Classify every row, then fold exact content duplicates onto a keeper.

    Two passes on purpose: a duplicate's disposition must follow its KEEPER's
    (``classify_point(duplicate_of=...)``), which cannot be known while streaming.
    The keeper for a stable key is the first row seen for it, so the plan is
    deterministic for a fixed input order — the reader's order, which the tool
    reports rather than claims to sort.
    """
    if packs is None:
        mapping, pack_note = load_pack_kind_map()
    else:
        mapping, pack_note = packs, None
    rows = list(rows)
    plan = Plan(
        source_graph=source_graph,
        target_graph=target_graph,
        pack_catalog_note=pack_note,
    )

    # The keeper is identified by its INDEX in `rows`, never by `row.node_id`. Point ids
    # are not unique — the reader admits same-id nodes with different content as a legal
    # shape (`tools/curate_graph.py`, `test_two_nodes_sharing_an_id_in_one_page_are_not_a
    # _pagination_shift`) — so a node_id-keyed keeper made every row sharing that id look
    # like the keeper ITSELF (`keepers[key] == row.node_id`), append the keeper's decision
    # a second time, and drop the second text with no decision, no reason and no
    # `--rows` line, while `_pending_sections` reported the pair as a plan-level
    # `duplicate_rows` repeat. The artifact then said "this plan repeats a row" about a
    # unique text that was simply left behind (round-35 review finding 1).
    keepers: dict[str, int] = {}
    first_pass: dict[str, Decision] = {}
    for index, row in enumerate(rows):
        key = row.stable_key
        if key in keepers:
            continue
        keepers[key] = index
        first_pass[key] = classify(row, mapping)
    # A keeper that carries NOTHING cannot be "the row of record" for the content:
    # preferring a keeper whose own disposition carries means a real-content row is
    # never dropped in favour of an empty one that happens to share its hash.
    #
    # Round-30 review finding 2: that invariant only held when the real-content
    # row's OWN rule carried. When it returned UNDECIDABLE the empty keeper
    # survived, and the duplicate's reason then asserted "the keeper <id> is the row
    # of record for this content" about a row holding no content — while the only
    # copy of the text was left behind and `plan --rows` (which does not carry
    # `content`) could not reveal it. So a row that HOLDS content displaces an
    # incumbent that holds none, whatever that row's own disposition is; the
    # duplicate then inherits a TRUE disposition (``undecidable`` — we cannot say
    # this content is safely left behind, which R8 requires) instead of a
    # confident `not-carried`.
    for index, row in enumerate(rows):
        key = row.stable_key
        incumbent = first_pass[key]
        # Index identity, not `node_id`: a DIFFERENT row that happens to share the
        # keeper's id is still a candidate to hold the content.
        if keepers[key] == index:
            continue
        if not str(row.content or "").strip():
            continue
        if not str(incumbent.content or "").strip():
            # The incumbent witnesses nothing: this row becomes the row of record.
            keepers[key] = index
            first_pass[key] = classify(row, mapping)
            continue
        # The incumbent holds the content: only a CARRYing candidate improves on it.
        if incumbent.disposition == CARRY:
            continue
        candidate = classify(row, mapping)
        if candidate.disposition == CARRY:
            keepers[key] = index
            first_pass[key] = candidate

    for index, row in enumerate(rows):
        key = row.stable_key
        keeper_index = keepers[key]
        keeper = first_pass[key]
        if keeper_index == index:
            plan.decisions.append(keeper)
            continue
        # A hash shared by two rows whose CONTENT differs proves nothing about them,
        # so the duplicate claim — and the "row of record" the reason used to assert —
        # is withheld rather than guessed (round-34 finding 2). This now covers the
        # same-id shape too: the check is the row's IDENTITY against the keeper's, so a
        # row that merely shares the keeper's `node_id` no longer short-circuits onto the
        # keeper's decision and its text reaches the artifact as `undecidable`.
        mine, theirs = str(row.content or "").strip(), str(keeper.content or "").strip()
        plan.decisions.append(
            classify(
                row,
                mapping,
                duplicate_of=keeper.disposition,
                keeper_id=rows[keeper_index].node_id,
                content_mismatch=bool(mine and theirs and mine != theirs),
            )
        )
    return plan


# ── guards ────────────────────────────────────────────────────────────────


#: The tenant graph's `graph_id`: a marker, not an identity.
_DEFAULT_SENTINEL = "default"


def _tail(g: str) -> str:
    """One graph's identity, from any accepted spelling of it.

    Three shapes reach this function and they must collapse together:

    * ``org_<id>_g_<gid>`` — a custom graph: the identity is ``<gid>``;
    * ``g_<gid>`` / ``<gid>`` — the same graph by id;
    * ``org_<id>`` and ``team_<id>`` — the TENANT/default graph, which is spelled
      both ways by the product ("``org_{org_id}`` post-rename and ``team_{org_id}``
      before it … both spellings are the calling org's own graph"), so the
      identity is ``<id>``. Missing this made ``--graph org_<id> --target
      team_<id>`` pass the guard and run in place.

    ⚠️ The ``_g_`` split is on the LAST occurrence, so a graph id that itself contains
    ``_g_`` is TRUNCATED (``org_o1_g_x_g_y`` and ``g_x_g_y`` both yield ``y``). That
    truncation is fail-closed by construction, not by accident: it collapses every
    spelling of one id to the SAME tail (so the guard never splits one graph in two),
    and its only failure direction is that two DIFFERENT graphs whose ids share that
    tail compare equal — which refuses a correct run, and can never pass an in-place
    write (round-33 review follow-up 3, recorded rather than re-shaped: the split is
    what makes the two spellings of one graph collapse, and narrowing it would have to
    know which ids may contain ``_g_`` — which no surface here guarantees).
    """
    g = (g or "").strip().lower()
    if not g:
        return ""
    if "_g_" in g:
        t = g.split("_g_")[-1]
        return t[2:] if t.startswith("g_") and len(t) > 2 else t
    for prefix in ("org_", "team_", "g_"):
        if g.startswith(prefix) and len(g) > len(prefix):
            return g[len(prefix):]
    return g


def _canonical_from_row(row: Mapping[str, Any]) -> str | None:
    # `graph_id` is preferred, EXCEPT the "default" sentinel: that value names the
    # tenant graph without identifying it, so a row carrying it must fall through to
    # a spelling that does (`stable_id`, then the namespace). Without this, two rows
    # describing ONE graph — `{"namespace": "team_x"}` and
    # `{"graph_id": "default", "namespace": "team_x"}` — canonicalised to "x" and
    # "default", the guard PASSED, and `--graph default --target team_x --apply`
    # wrote in place.
    fields = [row.get("graph_id"), row.get("stable_id"), row.get("namespace"),
              row.get("name")]
    ident = next((f for f in fields if f and str(f).casefold() != _DEFAULT_SENTINEL), None)
    if not ident:
        # ONLY the sentinel remains: it names the tenant graph without identifying it,
        # so it is NOT an identity. Returning "default" let a row set of
        # `[{"graph_id": "default"}, {"namespace": "team_x"}]` canonically distinguish
        # the tenant graph from `team_x` — the in-place fail-open, one row shape over.
        return None
    # EVERY row collapses to its id tail. Returning the namespace string itself
    # (while `g_<id>` returns the bare id) made two spellings of one graph compare
    # UNEQUAL — a fail-open on the exact guard that exists to stop an in-place run.
    # An EMPTY tail is not a spelling of anything (an id ending in `_g_`, or an empty
    # gid): `_tail` returns "" and the old `or <raw spelling>` fallback handed back the
    # namespace VERBATIM, so `org_o1_g_x_g_` and `g_x_g_` canonicalised differently and
    # the guard PASSED — the one failure direction `_tail`'s truncation is documented
    # never to have. Refuse instead (round-33 review follow-up 3's guarantee, made true).
    return _tail(str(ident)) or None


def _identity_shape(row: Mapping[str, Any]) -> frozenset[str]:
    """Which identity FIELDS this row exposes (empty values do not count).

    Rows that describe one graph but expose DIFFERENT fields make the ref→row mapping
    per-row, which is how two spellings of one graph compare unequal. A row where
    `graph_id` is the `default` sentinel still COUNTS that field — the sentinel is a
    normal thing for the tenant row to carry, not a missing field.
    """
    fields = ("graph_id", "stable_id", "namespace", "name")
    return frozenset(f for f in fields if row.get(f))


def _shared_identity_value(
    a: Mapping[str, Any], b: Mapping[str, Any]
) -> tuple[str, str] | None:
    """An identity field/value these two rows AGREE on, or None.

    Two rows that share one identity value describe one graph — so if their canonicals
    differ, the row set is self-contradictory and identity is unprovable.
    """
    for fname in ("graph_id", "stable_id", "namespace", "name"):
        va, vb = a.get(fname), b.get(fname)
        if va and vb and str(va).strip().casefold() == str(vb).strip().casefold():
            return fname, str(va)
    return None


def _namespace_shaped(value: Any) -> bool:
    """Does this value look like a graph NAMESPACE rather than a bare name?

    Accepts the three PREFIXED spellings the product uses — ``org_<id>_g_<gid>``
    (anything containing ``_g_``), ``g_<gid>``, ``org_<id>``/``team_<id>``. A bare id
    tail, and a bare name (``live-v4``), are deliberately NOT accepted: `MCPReader.graphs()`
    wraps every string the surface returns as ``{"namespace": s}``, so a name in the
    namespace field is indistinguishable from a namespace VALUE, and a row set holding
    one would map two spellings of ONE graph to two rows with disjoint values — the
    last-clause check cannot see it (the shapes are identical and nothing is shared)
    and the guard passes an in-place write. Measured: `rows = [{"namespace":
    "org_o1_g_old"}, {"namespace": "live-v4"}]` with `--graph live-v4 --target
    org_o1_g_old --apply` returned 0 and wrote once.
    """
    text = str(value or "").strip()
    if not text:
        return False
    low = text.casefold()
    if "_g_" in low:
        return True
    return any(low.startswith(p) and len(low) > len(p) for p in ("org_", "team_", "g_"))


def _matched_row(ref: str, graph_rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The row ``ref`` RESOLVES through — the same one `canonical_graph_id` uses.

    ⛔ Not "the first row that names the ref in any field". That version matched the
    `name` field first-row-wins, so a row set could hand the two checks below the SAME
    row for two different spells of ONE graph: `g_t0000000000000` resolved through
    `{"stable_id": "g_t0000000000000", "name": "default"}` while `default` resolved
    through `{"graph_id": "default", "namespace": "team_o1"}` — and because
    `_matched_row` returned row 1 for BOTH, `ra is not rb` short-circuited the only
    clause that could see the contradiction. Measured: `plan --graph g_t…
    --target default --apply` exited 0 and issued the ingest, in place on the tenant
    graph.

    The field order and the comparison rules mirror `canonical_graph_id` exactly:
    `graph_id` / `stable_id` / `namespace` are matched **exactly** (not casefolded, as
    `mcp_auth` does) and in that scan order — so a ref that lands on the `default`
    sentinel DOES return that row, because that row is where the canonical came from —
    and `name` alone is matched casefolded, only when no identity field matched.
    """
    wanted = (ref or "").strip()
    if not wanted:
        return None
    for row in graph_rows:
        for fname in ("graph_id", "stable_id", "namespace"):
            val = row.get(fname)
            # EXACT, like `canonical_graph_id` (and `mcp_auth`): casefolding an id here
            # made the guard claim a ref as one row's id while the write path resolved
            # it as another row's name.
            if not val or str(val).strip() != wanted:
                continue
            return row
    for row in graph_rows:
        if (row.get("name") or "").casefold() == wanted.casefold():
            return row
    return None


def canonical_graph_id(ref: str, graph_rows: Sequence[Mapping[str, Any]] | None) -> str | None:
    """Resolve any accepted spelling of a graph to one canonical id.

    The `graph` argument resolves through ``mcp_auth.resolve_graph_target``, which
    accepts a graph **id**, a ``stable_id``, a namespace (``org_…_g_<id>``) and a
    case-insensitive graph **name**. A guard that only understands ids would wave
    ``--graph live-v4 --target org_…_g_2b7e4c9d1a6f8035`` straight into the capped
    archive, so the comparison runs on the canonical identity — and returns None
    when it cannot establish it, which the caller treats as a refusal.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    if graph_rows:
        # ⛔ Identity fields are compared EXACTLY, not casefolded: `mcp_auth` matches
        # graph id / stable_id / namespace with `==` and casefolds ONLY the name
        # (`mcp_auth.py:490-513`). Casefolding the ids here let the guard claim a ref as
        # one row's ID while the write path resolved it as another row's NAME —
        # `--graph Default` casefolded onto the tenant row's `graph_id: default` while
        # `resolve_graph_target` read it as the NAME of the `g_deadbeef…` row, so the
        # guard's two canonicals differed for ONE graph and the run wrote in place.
        wanted = ref.casefold()
        hits: list[str] = []
        for row in graph_rows:
            for field in ("graph_id", "stable_id", "namespace"):
                val = row.get(field)
                if val and str(val).strip() == ref.strip():
                    canon = _canonical_from_row(row)
                    if canon is None:
                        # ⛔ A row that MATCHES the ref but cannot be identified does
                        # NOT contribute a canonical, and it must not be silently
                        # skipped either: skipping let a SECOND row resolve the same
                        # ref through its own spelling, so the guard's canonical for a
                        # ref could diverge from the row `mcp_auth` actually resolves
                        # it to. The reviewer's measured input: rows
                        # `{graph_id: b, name: default}`, `{stable_id: default,
                        # namespace: org_o1_g_, name: a}`, `{stable_id: g_x_g_a,
                        # namespace: org_o1_g_}` pass the guard for
                        # (`default`, `org_o1_g_`) — two canonicals `b` and `a` — while
                        # the write path resolves BOTH refs to the MIDDLE row, i.e.
                        # an in-place write. Unprovable is a refusal, like everywhere
                        # else in this function.
                        return None
                    if canon not in hits:
                        hits.append(canon)
                    break
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            # Two rows describe the ref and disagree about WHICH graph it is: never
            # pick one, because picking wrong is how an in-place write happens.
            # ⛔ This refusal (and the ambiguous-name one below) is REDUNDANT TODAY
            # with the `return None` catch-all at the end of this block, and that is
            # deliberate defence in depth, not dead code (round-33 review follow-up 5):
            # it cannot be pinned by a test — deleting it is behaviour-neutral — but
            # its whole point is to survive the day the catch-all is refactored, when
            # falling through would first-match and become the in-place fail-open.
            return None
        # Names are matched SEPARATELY so an ambiguous name is a refusal, not a
        # first-match: two graphs with one name must never resolve to whichever
        # row happened to come first.
        by_name = [r for r in graph_rows if (r.get("name") or "").casefold() == wanted]
        if len(by_name) == 1:
            return _canonical_from_row(by_name[0])
        if len(by_name) > 1:
            return None
    if graph_rows:
        # ROWS ARE THE AUTHORITY. An id-shaped spelling that no row names is
        # UNRESOLVED, not resolved-by-shape: the surface hands out NAMESPACE strings
        # (`tortoise_list_graphs() -> list[str]`), and `mcp_auth` accepts a
        # `stable_id` that a namespace-only row does not expose — so
        # `team_<org>` (the tenant graph) and `g_<tenant-gid>` are ONE graph while a
        # shape-only tail comparison says they differ. Verified fail-open before this
        # line: `--graph g_<tenant-gid> --target team_<org>` passed the guard, wrote
        # to the SOURCE graph in place, and recorded the rows as migrated.
        return None
    # Without rows, only an id-shaped spelling is resolvable.
    if "_g_" in ref or ref.startswith("g_"):
        return _tail(ref) or None
    return None



def assert_distinct_graphs(
    source_graph: str,
    target_graph: str,
    *,
    graph_rows: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """Refuse a migration whose target is its own source.

    The source sits at its node cap, so the only write it could accept is a
    refusal — and if the cap were ever lifted, an in-place run would write
    curated rows back into the archive the ruling protects. Because the identity
    check is only as good as the spellings it understands, an unresolvable ref is
    a REFUSAL (fail closed), not a pass.
    """
    if not target_graph:
        raise CurationRefused("no target graph named: a curation pass needs a distinct target")
    if (source_graph or "").strip().casefold() == (target_graph or "").strip().casefold():
        raise CurationRefused(
            f"source and target are the same graph ({source_graph!r}); the old graph is a "
            "read-only historic artifact (#11469)")
    ca = canonical_graph_id(source_graph, graph_rows)
    cb = canonical_graph_id(target_graph, graph_rows)
    if ca is None:
        raise CurationRefused(
            f"cannot resolve the source graph {source_graph!r} to a graph id — refusing to run "
            "a migration whose source identity is unproven (pass a spelling a graph row exposes — "
            "the namespace, or a graph id the surface lists)")
    if cb is None:
        raise CurationRefused(
            f"cannot resolve the target graph {target_graph!r} to a graph id — refusing to run: "
            "an unproven target could be the archive itself (pass a spelling a graph row exposes)")
    if ca == cb:
        raise CurationRefused(
            f"source and target are the same graph ({ca!r}); the old graph is a "
            "read-only historic artifact (#11469)")
    if graph_rows:
        # ⛔ A row set that puts a BARE NAME in `namespace` cannot map refs to graphs at
        # all: `MCPReader.graphs()` wraps every string the surface returns as
        # `{"namespace": s}`, so `live-v4` and `org_o1_g_old` arrive as two
        # shape-identical rows with disjoint values — nothing is shared, the shapes
        # match, and the two checks below cannot fire while the pair is ONE graph.
        # Measured: that row set passed and `--apply` wrote to the source in place.
        # Fail closed: an untrustworthy row set proves nothing.
        bad = [(r.get("namespace")) for r in graph_rows
               if r.get("namespace") and not _namespace_shaped(r.get("namespace"))]
        if bad:
            raise CurationRefused(
                f"a graph row exposes {bad[0]!r} in `namespace`, which is not a namespace "
                "spelling — the row set cannot prove which graph a spelling names, and a "
                "pair that is really ONE graph would pass. Re-run against a surface that "
                "returns namespaces (or pass the graph id / namespace)")
        # LAST line of the identity argument. Two refs can resolve through DIFFERENT
        # rows that expose different identity FIELDS for the SAME graph — e.g. one
        # entry `{"graph_id": "default", "stable_id": "g_<tenant>", "name": "default"}`
        # and another `{"namespace": "team_<org>"}` — and then their canonicals differ
        # (`_canonical_from_row` can only use the fields its own row has). Identity is
        # unprovable in that case, so it is a REFUSAL, not a pass.
        ra, rb = _matched_row(source_graph, graph_rows), _matched_row(target_graph, graph_rows)
        if (ra is not None and rb is not None and ra is not rb
                and (_identity_shape(ra) != _identity_shape(rb)
                     # OR the two rows SHARE an identity value while their canonicals
                     # differ: a self-contradictory row set that spells ONE graph two
                     # ways in the same field (two rows both `graph_id: default` with
                     # `stable_id` g_a / g_b and names na / nb). Each ref then matches
                     # exactly one row, so `canonical_graph_id` never sees `hits > 1`
                     # and cannot refuse; the shapes are identical, so this check
                     # would pass — and the run writes to the tenant graph in place,
                     # which is the one thing the guard exists to stop. Measured: 7 of
                     # 2162 oracle-resolved random row sets passed before this clause.
                     or _shared_identity_value(ra, rb) is not None)):
            raise CurationRefused(
                    f"cannot prove {source_graph!r} and {target_graph!r} are distinct graphs: the "
                    "graph rows expose different identity fields, so the two refs resolve through "
                    "different rows and may name ONE graph — pass a spelling whose row exposes the "
                    "same fields (or the graph id both rows agree on)")


def off_switch_active(explicit: bool = False) -> bool:
    # The vocabulary is `tortoise.env_truthy`'s, not a local copy (#4097): the
    # scanner in `tests/test_env_truthy.py` reds on an ad-hoc literal here, and a
    # second copy of the vocabulary is exactly the divergence that guard exists
    # to prevent. `is_truthy` treats unset/blank/falsy/garbage as False, which is
    # this switch's documented default (armed on anything but a truthy spelling).
    if explicit:
        return True
    return is_truthy(os.environ.get(OFF_SWITCH_ENV))


# ── writing (additive, idempotent) ────────────────────────────────────────


@runtime_checkable
class PlanWriter(Protocol):
    """The only write surface a curation run is allowed to use.

    Deliberately single-verb: there is no ``delete``, ``retract`` or ``update``
    on this protocol, so "the pass cannot destroy anything" is a property of the
    type rather than of the caller's discipline. ``write_sections`` returns ONLY the
    keys the target accepted, and raises on a refusal.
    """

    def write_sections(
        self,
        target_graph: str,
        sections: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> Mapping[str, Any]:
        ...


def _source_ledger_key(item: Mapping[str, Any]) -> str:
    """The ledger key for a Source row.

    ONE definition, used by both `_pending_sections` and `IngestWriter`, because two
    derivations that disagree mean the ledger never recognises an already-migrated
    Source. The pair is HASHED rather than delimiter-joined: a `kind` may contain the
    delimiter (`article:https` + `//x/y:z` and `article` + `https://x/y:z` derive the
    same string when joined by `:`).
    """
    pair = hashlib.sha256(json.dumps(
        [item.get("sourceKind") or "", item.get("url") or ""],
        sort_keys=True).encode("utf-8")).hexdigest()
    return f"{MIGRATION_KEY_PREFIX}source:{pair}"


def _fallback_key(section: str, item: Mapping[str, Any]) -> str:
    """A ledger key for an item a caller passed without one."""
    if section == "sources":
        return _source_ledger_key(item)
    basis = hashlib.sha256(json.dumps(
        [section, item.get("kind") or "", item.get("content") or ""],
        sort_keys=True).encode("utf-8")).hexdigest()
    return f"{MIGRATION_KEY_PREFIX}{section}:{basis}"


def _ingest_refused(res: Any) -> str | None:
    """The reason a tool result means "not accepted", or None when it was accepted.

    ``ingest`` reports refusals as an error envelope (``{"error": ...``,
    ``{"violations": [...]``) rather than by raising, so counting the attempt as a
    success is how a ledger ends up claiming rows that do not exist.
    """
    if not isinstance(res, Mapping):
        # `normalise_tool_result` passes str/list/int through untouched, so a result
        # this pass cannot interpret is NOT evidence of a write — treat it as a
        # refusal rather than counting the attempt as migrated.
        return (f"target returned an uninterpretable result ({type(res).__name__}): "
                "the write is not confirmed")
    for key in ("error", "violations", "detail"):
        val = res.get(key)
        if val:
            return f"target refused the batch: {str(val)[:300]}"
    if res.get("ok") is False:
        return "target reported ok=false"
    return None


class IngestWriter:
    """``PlanWriter`` over the hosted ``tortoise_ingest`` tool (#888 W4).

    ``ingest`` dedups points by (content hash + kind) and merges Sources by url,
    so a repeated write folds instead of duplicating. It is called with
    ``promotion_policy='auto'`` because a curated row should arrive live, and
    ``granularity='bulk'`` to keep one call per batch.
    """

    TOOL = "tortoise_ingest"

    def __init__(
        self,
        call_tool: Callable[[str, Mapping[str, Any]], Any],
        *,
        batch_size: int = 100,
        armed: Callable[[], bool] = lambda: not off_switch_active(),
    ):
        self._call = call_tool
        self._batch = max(1, batch_size)
        self._armed = armed

    def write_sections(
        self,
        target_graph: str,
        sections: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> Mapping[str, Any]:
        """Write each bundle SECTION in batches, reporting only accepted keys.

        A Source is not a Point: sending one in the `points` section re-types it
        (D10), and a bundle that carried Sources but only ever sent `points`
        reported success while never writing them — the silent-no-op shape this
        module exists to avoid.
        """
        written: list[str] = []
        for section in ("points", "sources"):
            items = list(sections.get(section) or [])
            for i in range(0, len(items), self._batch):
                if not self._armed():
                    return {"written": written, "stopped": "off-switch engaged"}
                batch = items[i:i + self._batch]
                started = list(batch)
                # The accounting keys come from the PRE-strip list: reading them back
                # off the item after the target accepted the write would turn a real
                # success into a crash (a caller may legally pass a source item with no
                # `migrationKey`, which is what `plan.bundle()` emits). The strip that
                # reaches the wire lives in `_call_one`, which is why nothing here
                # rewrites `batch` — a dead copy there read as the live strip and invited
                # exactly that regression.
                keys = [str(p.get("migrationKey") or _fallback_key(section, p)) for p in started]
                self._call_one(target_graph, section, started, written)
                written.extend(keys)
        return {"written": written}

    def _call_one(self, target_graph: str, section: str, batch: Sequence[Mapping[str, Any]],
                  written: list[str]) -> Any:
        try:
            raw = self._call(self.TOOL, {
                "graph": target_graph,
                "promotion_policy": "auto",
                "granularity": "bulk",
                # `migrationKey` travels on a POINT because the target KEEPS it —
                # `_sanitize_props` has no declared-prop whitelist for Points, so
                # `migrationKey`/`sourceNodeId`/`pack` all land on the migrated node
                # (measured), which is what makes a migrated row identifiable after
                # the fact. A :Source DOES have one (#3998 drops undeclared props),
                # so the key is stripped from that section alone.
                "bundle": {section: ([{k: v for k, v in p.items() if k != "migrationKey"}
                                      for p in batch] if section == "sources" else list(batch))},
            })
            # Unwrapping is INSIDE the guard: `normalise_tool_result` raises on the
            # `isError` path, and a refusal there must still report the batches
            # that already landed.
            res = normalise_tool_result(raw)
        except CurationRefused as exc:
            raise CurationRefused(str(exc), written_keys=written) from exc
        except Exception as exc:  # transport/timeout AFTER earlier batches landed
            raise CurationRefused(f"target call failed: {type(exc).__name__}: {exc}",
                                  written_keys=written) from exc
        refused = _ingest_refused(res)
        if refused:
            raise CurationRefused(refused, written_keys=written)
        return res

#: The fields a ledger may resolve or record a target through. ⛔ `name` is NOT one of
#: them, and neither is `_tail`'s derived identity: `mcp_auth.resolve_graph_target` is
#: field-major and treats a name as the LAST resort, so a bare token that a row carries
#: only as a NAME resolves to a different graph than the ledger row it was matched
#: against — measured 2026-11-08, the shipped `graphs` section (`{"namespace": s}`) loses
#: `graph_id`/`stable_id`, so a name equal to a graph whose id is not exposed looked
#: unique and two DIFFERENT graphs shared one scope (round-37 P2, second instance).
#: A name is not an identity, for the same reason `_canonical_from_row` refuses the
#: `default` sentinel: it may name a graph without identifying it.
_LEDGER_REF_FIELDS = ("graph_id", "stable_id", "namespace")


def _ledger_row(
    ref: str,
    graph_rows: Sequence[Mapping[str, Any]] | None,
) -> Mapping[str, Any] | None:
    """The row whose EXACT id fields name ``ref`` — never a `name` match.

    ⛔ Deliberately NOT `_matched_row`: that helper falls back to a casefolded `name`
    match, which is the LAST resort in `mcp_auth.resolve_graph_target`'s field-major
    order. The two disagree exactly when a graph's NAME equals another graph's ID —
    and then `_matched_row("g_beta")` returns ALPHA (whose name is `g_beta`) while the
    write path resolves the same ref to BETA. The identity guard's error direction is a
    refusal; the LEDGER's is a skip, so it must not inherit that ambiguity: a ref the
    exact fields do not name gets NO row here, and the caller falls back to the literal
    spelling. ⛔ That fallback is NOT "the guard refuses it" in every case: a spelling
    only a NAME resolves (identity-bearing rows, e.g. `{"graph_id": "g_x", "name":
    "new"}`) is ACCEPTED by the guard, and the ledger then scopes it to the literal
    string. The direction that matters is preserved — a literal scope can never
    intersect ANOTHER graph's scope (a row whose exact field equals the spelling is
    matched here first), so a name-spelled target can never SKIP; `run_plan`'s
    disjoint-scope check turns a mismatched pair into a loud refusal instead. The cost
    is a re-SEND when one graph was already recorded under another spelling (either
    order; target-side content-hash dedup absorbs it, and `tortoise_ingest` is not
    declared idempotent). None of it is reachable on the SHIPPED surface, which
    exposes namespaces and no names — see `MCPReader.graphs()`.
    Measured 2026-11-08 (round-38 P2): with the shipped `graphs` shape losing
    `graph_id`/`stable_id`, ALPHA's `name: g_beta` made the two graphs share one ledger
    scope, so a run into BETA skipped every row and a run spelled by BETA's id recorded
    its keys under ALPHA.
    """
    wanted = (ref or "").strip()
    if not wanted or not graph_rows:
        return None
    # FIELD-MAJOR, matching `mcp_auth.resolve_graph_target`'s scan order: every
    # `graph_id` before any `stable_id`, then `namespace`. A ROW-major walk returns a
    # row whose LATER field equals the ref before one whose EARLIER field does, so the
    # ledger's notion of the target could disagree with the write path's — measured
    # 2026-11-08 (round-39 P3): rows `[{graph_id: g_alpha, namespace: LIVE},
    # {graph_id: LIVE, namespace: org_o1_g_beta}]` resolved `LIVE` to BETA for the write
    # and to ALPHA here, recording BETA's keys under ALPHA.
    for fname in _LEDGER_REF_FIELDS:
        for row in graph_rows:
            val = row.get(fname)
            if val is not None and str(val).strip() == wanted:
                return row
    return None


def _refs_unique_to(
    row: Mapping[str, Any],
    graph_rows: Sequence[Mapping[str, Any]],
) -> set[str]:
    """This row's own ID fields that name NO OTHER row in the set, sentinel excluded.

    A value is included only when EXACTLY ONE row carries it in one of
    `_LEDGER_REF_FIELDS` — the fields the resolver matches exactly. Two exclusions, both
    required:

    * a value ANOTHER row also carries is not this row's (a row set that spells one
      graph two ways must not unify, and must not let two graphs share a scope — the
      wrong direction here is a SKIP, not a refusal);
    * the `default` sentinel, which names the tenant graph without identifying it and is
      workspace-universal while the ledger path is per-user.
    """
    out: set[str] = set()
    for fname in _LEDGER_REF_FIELDS:
        val = row.get(fname)
        v = str(val).strip() if val is not None else ""
        if not v or v.casefold() == _DEFAULT_SENTINEL:
            continue
        holders = [r for r in graph_rows
                   if any(str(r.get(f) or "").strip() == v for f in _LEDGER_REF_FIELDS)]
        if len(holders) == 1 and holders[0] is row:
            out.add(v)
    return out


def ledger_scope(
    target_graph: str,
    graph_rows: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """The value a ledger record is WRITTEN under for ``target_graph``.

    Scoping the ledger by the LITERAL spelling the operator typed let one graph have
    several scopes: ``--target org_x_g_new`` on day one and ``--target g_new`` on day
    two read as two different targets, so every row was re-sent and the half of
    idempotency that "skips a migrated key before it reaches the wire" was gone (the
    guarantee then rested entirely on target-side dedup, and ``tortoise_ingest`` is
    explicitly NOT declared idempotent).

    The recorded key is the resolved ROW's own ``graph_id`` (else ``stable_id``, else
    ``namespace``) — a REF, never `_tail`'s derived identity. ⛔ An id-space value in
    this position is a fail-OPEN: `mcp_auth` resolves a bare token by graph NAME, so
    recording graph A under its tail ``x`` made a later ``--target x`` (which resolves
    to a DIFFERENT graph named `x`) share A's scope and skip every row into it.

    Falls back to the spelling when no row resolves it — an unresolvable ref must not
    silently inherit another graph's ledger, and ``assert_distinct_graphs`` is what
    refuses an unproven identity before any write.
    """
    row = _ledger_row(target_graph, graph_rows)
    if row is not None:
        unique = _refs_unique_to(row, graph_rows)
        for fname in _LEDGER_REF_FIELDS:
            val = row.get(fname)
            v = str(val).strip() if val is not None else ""
            if v and v in unique:
                return v
    if target_graph.strip().casefold() == _DEFAULT_SENTINEL:
        # ⛔ `default` is NOT a scope: it is workspace-universal while the ledger file is
        # per-user, so recording it let one workspace's rows be skipped in another
        # (`applied: True, written: 0`, exit 0). A target whose only identity is the
        # sentinel is unidentifiable — refuse, the way the identity guard refuses an
        # unresolvable ref, rather than write a key that answers for every workspace.
        raise CurationRefused(
            f"cannot scope the ledger for target {target_graph!r}: it is the `default` "
            "sentinel, which names the tenant graph without identifying it, and the "
            "ledger is per-user while `default` is workspace-universal — pass the graph "
            "id or namespace instead")
    return target_graph


def ledger_scopes(
    target_graph: str,
    graph_rows: Sequence[Mapping[str, Any]] | None = None,
) -> frozenset[str]:
    """Every spelling a ledger must MATCH for ``target_graph``.

    Matching is a set intersection, not equality, on purpose: one side of a comparison
    may carry ``graph_rows`` and the other may not (a `Ledger` built directly in a
    test, a run whose `graphs` section was unreadable), and equality on a single
    computed key would then refuse a ledger that is in fact this graph's — a false
    block on the one guard that protects the "skipped before the wire" half of R3.

    ⛔ THE SET IS REFS, NOT IDENTITIES. It is the literal spelling plus the spelling
    fields of the row that spelling RESOLVES THROUGH (`_ledger_row`, which
    matches EXACT identity fields only — NOT `_matched_row`, whose name fallback would
    reinstate the cross-graph skip this scope exists to prevent) — the refs
    `mcp_auth.resolve_graph_target` accepts for that one graph, and nothing derived.
    Putting `_tail`'s identity in the set inverted this guard's error direction: a bare
    tail is id-SHAPED but is resolved by the real path as a NAME, so graph A's tail
    ``x`` matched a request for the different graph NAMED ``x`` and the run reported
    `applied: True, written: 0` over a target that owed every row (round-37 review,
    P2). Row fields cannot do that: two different rows have different
    ``graph_id``/``stable_id``/``namespace``, so their sets are disjoint.
    """
    refs = {target_graph}
    row = _ledger_row(target_graph, graph_rows)
    if row is not None:
        # `_refs_unique_to`, NOT every field verbatim: a value TWO rows carry is not
        # this row's, so putting it in the set let graph A's scope intersect graph B's
        # (round-37 P2, second instance). And a `name` never reaches this set at all
        # (round-38 P2): `_LEDGER_REF_FIELDS` are the exact id fields only.
        refs |= _refs_unique_to(row, graph_rows)
    return frozenset(refs)


class Ledger:
    """Local record of what this pass already migrated **into this target**.

    The ledger is the half of idempotency that does not depend on the target
    server: a key it holds is never sent again, whatever the target would do with
    it. It is scoped by target graph — the same row migrated into graph A has not
    been migrated into graph B — append-only, and fsynced, because a ledger that
    can be lost (or that answers for the wrong graph) is worse than none.

    The scope MATCHES any accepted spelling of the target (``ledger_scopes``) and
    RECORDS under the canonical one, so a re-run under a different spelling still
    skips the rows the first run landed.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        target: str,
        *,
        graph_rows: Sequence[Mapping[str, Any]] | None = None,
    ):
        self.path = str(path)
        self.target = target
        self.scopes = ledger_scopes(target, graph_rows)
        self.write_scope = ledger_scope(target, graph_rows)
        self._keys: set[tuple[str, str]] = set()
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        self._keys.add((row["target"], row["migrationKey"]))
                    except (json.JSONDecodeError, KeyError, TypeError, AttributeError,
                            ValueError) as exc:
                        # NOT "skip and continue": a damaged line means the keys it held
                        # (and any appended after a torn tail) are unknowable, and a row
                        # the target ACCEPTED would be silently re-sent — the exact
                        # "the ledger can be lost" failure its own docstring forbids.
                        raise CurationRefused(
                            f"ledger {self.path!r} is damaged at a line that is not valid "
                            f"JSON ({type(exc).__name__}): the keys it recorded are "
                            "unknowable, so rows it held would be re-sent. Inspect the "
                            "file (truncate to the last complete line) before re-running") from exc
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError) as exc:
            # ⛔ `UnicodeDecodeError` comes from ITERATING an undecodable file, so it is
            # raised outside the per-line handler above and would escape `main()` as a
            # traceback (exit 1) instead of the documented refusal + remedy.
            # `--ledger <a directory>` / an unreadable file: a usage error on the
            # operator's own path, so it owes the same refusal + remedy as a damaged
            # line rather than an IsADirectoryError traceback out of main().
            raise CurationRefused(
                f"ledger {self.path!r} cannot be read ({type(exc).__name__}: {exc}); "
                "point --ledger at a writable FILE path, or remove the obstacle"
            ) from exc

    def __contains__(self, key: object) -> bool:
        return any((scope, key) in self._keys for scope in self.scopes)

    def __len__(self) -> int:
        return sum(1 for t, _ in self._keys if t in self.scopes)

    def record(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # A torn tail (killed process, ENOSPC mid-flush) leaves a line with no
        # newline; appending straight onto it CONCATENATES the new record with the
        # torn one, destroying BOTH keys. Terminate the last line first.
        needs_nl = False
        if os.path.exists(self.path) and os.path.getsize(self.path):
            with open(self.path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                needs_nl = probe.read(1) != b"\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            if needs_nl:
                fh.write("\n")
            for k in keys:
                fh.write(json.dumps({"target": self.write_scope, "migrationKey": k}) + "\n")
                self._keys.add((self.write_scope, k))
            fh.flush()
            os.fsync(fh.fileno())


@dataclass
class RunResult:
    """What a run did, as the JSON a scripted driver reads.

    ``applied`` is True only when the apply leg actually TOOK: either something was
    accepted, or the plan owed the target nothing at all (``pending == 0``, an
    idempotent re-run). A FULLY-refused apply, or an apply that was STOPPED before its
    first batch accepted anything — nothing accepted, everything still pending — reports
    ``applied: False`` with ``refused`` or ``stopped`` set, because the exit code
    alone is not what a driver reads: `"applied": true, "written": 0` over a refusal
    (or over a stop that landed nothing) is how a migration that wrote nothing reads as
    done (round-33 review follow-up 1). A partial run — accepted keys, then a refusal
    or a stop — keeps ``applied: True`` and reports the accepted keys in ``written``.
    """

    applied: bool
    off: bool
    skipped_known: int
    written: int
    target_graph: str
    digest: str
    refused: str | None = None
    stopped: str | None = None
    identity: str | None = None
    duplicate_rows: int = 0
    pending: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "off": self.off,
            "skipped_known": self.skipped_known,
            "written": self.written,
            "target_graph": self.target_graph,
            "digest": self.digest,
            "refused": self.refused,
            "stopped": self.stopped,
            "identity": self.identity,
            "duplicate_rows": self.duplicate_rows,
            "pending": self.pending,
        }


def _pending_sections(
    bundle: Mapping[str, Any], ledger: Ledger
) -> tuple[dict[str, list[Mapping[str, Any]]], int, int]:
    """Split a bundle into what this target has NOT seen, and tote up the rest.

    Returns ``(sections, skipped_known, duplicate_rows)``. `skipped_known` is a
    LEDGER fact; `duplicate_rows` counts rows this PLAN repeats (the same Source
    twice — not producible by `sdk.list_sources`, which groups by
    `(url, sourceKind)`, but reachable from a hand-built plan or another reader).
    Conflating the two is how a row never sent got reported as "already migrated".
    """
    # BOTH sections are pending-tracked: a carried Source that never reaches the
    # wire is exactly the silent no-op this pass must not have.
    sections: dict[str, list[Mapping[str, Any]]] = {}
    total = 0
    duplicates = 0
    for section in ("points", "sources"):
        items = list(bundle.get(section) or [])
        total += len(items)
        keyed: list[Mapping[str, Any]] = []
        queued: set[str] = set()
        for item in items:
            # `not key`, not `is None`: the writer falls back on ANY falsy key
            # (`p.get("migrationKey") or _fallback_key(...)`), so `""`/`0`/`False` here
            # would give the planner and the writer two different keys — the round-25
            # defect one spelling over, with the same two consequences.
            # A truthy key is STRINGIFIED because the writer does
            # (`str(p.get("migrationKey") or …)`): a non-string key (5) was looked up
            # as `5` and recorded as `"5"`, so the row stayed pending forever and a
            # re-run re-sent it — the round-26 divergence, one type over.
            key = str(item["migrationKey"]) if item.get("migrationKey") else None
            if key is None:
                # Sources carry no key on the node (#3998), so their ledger key is
                # derived from the url, which is what the target merges on.
                # kind AND url: two Source rows can share a url with different
                # kinds (create_source merges by url, list_sources groups by
                # (url, kind)), and a url-only key silently collapsed them onto one
                # ledger entry — reported as skipped_known on a FIRST run.
                #
                # A POINT/TIMELINE item with no key must NOT come through here: with
                # neither sourceKind nor url, the source derivation is a CONSTANT, so
                # (a) two keyless point rows collapse onto one key and the second is
                # dropped from `sections` and never reaches the writer, and (b) the
                # writer records a per-row key the planner can never re-derive, so a
                # re-run re-sends the row. Both are the defect classes this pass
                # exists to avoid, so the derivation is the WRITER's, per section.
                key = _fallback_key(section, item)   # == the writer's own derivation
            if key in ledger:
                continue
            if key in queued:
                # A DUPLICATE within this read, not a row already in the target. The
                # live read path groups Sources by (url, sourceKind) — `sdk.list_sources`
                # returns `count(p)` — so this cannot arise from `read_sources`; it is
                # reachable from a hand-built plan or another reader, and counting it as
                # `skipped_known` would claim a migration that never happened.
                duplicates += 1
                continue
            queued.add(key)
            keyed.append({**item, "migrationKey": key})
        sections[section] = keyed
    pending_count = sum(len(v) for v in sections.values())
    # `skipped_known` is a LEDGER fact, so it is reported identically in a dry run
    # and in an apply — otherwise the only mode runnable today reports 0/0 over a
    # ledger that already holds rows, and the numbers cannot be reconciled.
    skipped = total - pending_count - duplicates
    return sections, skipped, duplicates


def run_plan(
    plan: Plan,
    *,
    writer: PlanWriter,
    ledger: Ledger,
    apply: bool = False,
    off: bool = False,
    graph_rows: Sequence[Mapping[str, Any]] | None = None,
) -> RunResult:
    """Execute a plan. Dry by default; additive and idempotent when applied.

    ``apply=False`` is a plan-only run: it reports what *would* be written and
    touches nothing — that is the mode to use against a target that does not
    exist yet.
    """
    # Compare SCOPE SETS, so the same graph under two accepted spellings
    # (``org_x_g_new`` / ``g_new`` / a ``stable_id``) shares one ledger instead of
    # re-sending every row into it. A refusal only when the two sets are disjoint:
    # one side may legitimately lack the `graph_rows` the other used.
    plan_scopes = ledger_scopes(plan.target_graph, graph_rows)
    if ledger.scopes.isdisjoint(plan_scopes):
        raise CurationRefused(
            f"ledger is scoped to {ledger.target!r} but the plan targets "
            f"{plan.target_graph!r} — a ledger answering for another graph would skip rows "
            "this target never received")
    identity_note: str | None = None
    # The census of what this plan still owes the target, computed ONCE and carried
    # by EVERY return path — including the refusals that happen before the write, or
    # a refused run reports 0/0/0 over a plan of rows.
    # It is LEDGER-DERIVED, not the raw carry count: a refused run over a plan whose
    # rows already landed owes nothing, and saying "N pending" would be wrong.
    _sections_all, skipped_all, dups_all = _pending_sections(plan.bundle(), ledger)
    pending_all = sum(len(v) for v in _sections_all.values())
    if off_switch_active(off):
        # BEFORE the identity guard: a disarmed run writes nothing, so it owes no
        # identity proof — and reporting "REFUSED: cannot resolve the target graph"
        # for a run the operator explicitly disarmed is the round-22 defect one arm
        # over (a driver pages on a deliberate no-op).
        return RunResult(False, True, skipped_all, 0, plan.target_graph, plan.digest(),
                         refused="off-switch active", duplicate_rows=dups_all,
                         pending=pending_all)
    try:
        assert_distinct_graphs(plan.source_graph, plan.target_graph, graph_rows=graph_rows)
    except CurationRefused as exc:
        if apply:
            return RunResult(False, False, skipped_all, 0, plan.target_graph, plan.digest(),
                             refused=str(exc), duplicate_rows=dups_all, pending=pending_all)
        # A dry run writes nothing, so an unproven identity is reported rather than
        # blocking the plan — the guard is strict exactly where a write can happen.
        identity_note = str(exc)
    if not apply:
        # Accounting happens BEFORE this return: a dry run over a ledger that already
        # holds rows must not report 0 written / 0 skipped, which cannot be
        # reconciled with a census of 127 carry rows.
        _sections, skipped, dups = _pending_sections(plan.bundle(), ledger)
        pending = sum(len(v) for v in _sections.values())
        return RunResult(False, False, skipped, 0, plan.target_graph, plan.digest(),
                         refused="dry run (--apply not given)", identity=identity_note,
                         duplicate_rows=dups, pending=pending)

    bundle = plan.bundle()
    sections, skipped, duplicate_rows = _pending_sections(bundle, ledger)
    pending_count = sum(len(v) for v in sections.values())
    if not pending_count:
        return RunResult(True, False, skipped, 0, plan.target_graph, plan.digest(),
                         duplicate_rows=duplicate_rows, pending=pending_count)
    try:
        result = writer.write_sections(plan.target_graph, sections)
    except CurationRefused as exc:
        # Only the batches the target actually ACCEPTED are recorded; the refused
        # attempt is not. A partial run reports what landed.
        accepted_partial = list(getattr(exc, "written_keys", ()) or [])
        ledger.record(accepted_partial)
        # `applied` follows what the target ACCEPTED, not the fact that --apply was
        # given: a batch refused before the first key landed wrote nothing, and
        # reporting it as applied is the field a driver actually reads
        # (`"applied": true, "written": 0`) lying about a refused migration.
        # Round-33 review follow-up 1; the exit code was already right (3).
        return RunResult(bool(accepted_partial), False, skipped, len(accepted_partial),
                         plan.target_graph, plan.digest(), refused=str(exc),
                         duplicate_rows=duplicate_rows, pending=pending_count)
    accepted = [k for k in (result.get("written") or [])]
    ledger.record(accepted)
    # `applied` is the SAME question here as on the refusal arm: did the apply leg
    # TAKE? A `stopped` result that landed no key answers NO — the target still owes
    # every row. Without this, a run stopped before its first batch accepted anything
    # reported `"applied": true, "written": 0, "pending": 1`, which is the exact
    # reading this change exists to remove, one arm over (round-33 follow-up 1).
    return RunResult(bool(accepted), False, skipped, len(accepted), plan.target_graph,
                     plan.digest(), stopped=result.get("stopped"),
                     duplicate_rows=duplicate_rows, pending=pending_count)
