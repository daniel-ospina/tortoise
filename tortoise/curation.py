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
    "requirement", "workflow", "userJourney",
})

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

    ``props`` keeps the source row (minus the embedding vectors) so a decision can
    be re-derived from the report alone, and so a carried row can be re-assembled
    into a valid ingest item — ingest REQUIRES ``content`` on every point item
    (``sdk._check_item_shape``), and a bundle without it is refused wholesale.
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
        """Content-addressed key: the same row read twice yields the same key."""
        basis = self.content_hash or hashlib.sha256(
            (self.content or "").strip().encode("utf-8")
        ).hexdigest()
        return f"{MIGRATION_KEY_PREFIX}{self.node_type}:{self.kind or '-'}:{basis}"


# ── pack routing ──────────────────────────────────────────────────────────


def pack_kind_map() -> dict[tuple[str, str], str]:
    """``{(bucket, kind): pack namespace}`` from the installed pack catalog.

    "File it against the pack that owns its kind" has a real schema behind it —
    the manifests — so the mapping is read from them rather than invented here.
    A pack load failure degrades to an empty map (every row then reports
    ``pack: None``, which is visible in the census) instead of raising: an
    inventory must not be blocked by a missing catalog.
    """
    out: dict[tuple[str, str], str] = {}
    try:
        from tortoise.pack_registry import PackRegistry, default_packs_dir

        reg = PackRegistry(default_packs_dir())
        reg.load_all()
    except Exception:  # degrade visibly, never silently
        return out
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
    return out


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
        if namespace:
            return namespace
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
        }


def _narrative(content: str) -> str | None:
    low = content.lower()
    for marker in NARRATIVE_MARKERS:
        if low.startswith(marker) or f"\n{marker}" in low:
            return marker
    return None


def classify_point(row: Row, packs: Mapping[tuple[str, str], str], *, duplicate_of: str | None = None) -> Decision:
    """A Point or an operator's disposition.

    The judgement is deliberately conservative: a row is only moved out of the
    ``undecidable`` bucket when a rule the report can quote decides it. Session
    *provenance* alone is NOT noise — the claim extracted from a session may be
    the most durable thing in the graph — so it never decides the disposition.

    A ``duplicate_of`` row follows its KEEPER's disposition rather than a rule of
    its own: "the keeper carries it" must be true, or the report asserts something
    the plan contradicts.
    """
    pack = pack_for(row.kind, row.node_type, packs)
    key = row.stable_key

    if duplicate_of == CARRY:
        return Decision(row.node_id, row.node_type, row.kind, NOT_CARRIED,
                        "exact content duplicate of a carried row (same content hash); "
                        "the keeper carries it. MERGE of near-duplicates is #5006's scope, "
                        "not this pass's", pack, key, row.content)
    if duplicate_of == UNDECIDABLE:
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        "exact content duplicate of a row that is itself undecidable — the "
                        "content has no carry candidate yet, so this row is not carried either",
                        pack, key, row.content)
    if duplicate_of is not None:
        # The keeper was not carried: neither is its duplicate, and the reason names
        # the keeper rather than claiming a carry that did not happen.
        return Decision(row.node_id, row.node_type, row.kind, NOT_CARRIED,
                        f"exact content duplicate of a row that was not carried ({duplicate_of}); "
                        "the keeper is the row of record for this content", pack, key, row.content)

    if row.node_type == "operator":
        # NOTE: the MCP read path excludes operator Points unless an operator
        # kind is requested (sdk._operator_scope_clauses, #7108), so this branch
        # is unreachable from `tools/curate_graph.py` today. It stays because the
        # rule is right, and it is marked unreachable in the inventory rather
        # than presented as a measured count.
        return Decision(row.node_id, row.node_type, row.kind, CARRY,
                        "operator (IMPL/NAND/composedOf) — the reasoning layer; the row is "
                        "carried as a claim, its edges are the ingest contract's job (#888 W4)",
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
    return Decision(row.node_id, row.node_type, row.kind, CARRY,
                    "a domain Event (not a session container)", pack, key, row.content)


def classify(row: Row, packs: Mapping[tuple[str, str], str], *, duplicate_of: str | None = None) -> Decision:
    if row.node_type in ("point", "operator"):
        return classify_point(row, packs, duplicate_of=duplicate_of)
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

    def census(self) -> dict[str, Any]:
        """Counts per (node_type, kind, disposition) — the inventory table."""
        by: dict[str, dict[str, int]] = {}
        for d in self.decisions:
            cls = f"{d.node_type}:{d.kind or '-'}"
            slot = by.setdefault(cls, {k: 0 for k in DISPOSITIONS})
            slot[d.disposition] = slot.get(d.disposition, 0) + 1
        totals = {k: 0 for k in DISPOSITIONS}
        for slot in by.values():
            for k, v in slot.items():
                totals[k] = totals.get(k, 0) + v
        return {
            "source_graph": self.source_graph,
            "target_graph": self.target_graph,
            "total_rows": len(self.decisions),
            "totals": totals,
            "by_class": dict(sorted(by.items())),
        }

    def carry_rows(self) -> list[Decision]:
        return [d for d in self.decisions if d.disposition == CARRY]

    def digest(self) -> str:
        """A digest of the plan's carry-set: two runs of the same source agree."""
        basis = "\n".join(sorted(d.stable_key for d in self.carry_rows()))
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def bundle(self) -> dict[str, Any]:
        """The additive write bundle: points only, each with its ``content``.

        ``content`` is not optional — ``sdk._check_item_shape`` refuses a point
        item without it (Phase 1, so a whole bundle is rejected, not one row).
        Connection wiring (which claim serves as evidence for which) is the ingest
        contract's job (#888 W4), and is NOT built here: the bundle is rows, not
        edges. Never a deletion.
        """
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
                    "kind": d.kind or "statement",
                    "content": d.content,
                    "sourceNodeId": d.node_id,
                    "pack": d.pack,
                }
                for d in self.carry_rows()
                if d.node_type in ("point", "operator")
            ],
            "sources": [
                {
                    "migrationKey": d.stable_key,
                    "url": d.content,
                    "sourceKind": d.kind,
                    "pack": d.pack,
                }
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
    mapping = pack_kind_map() if packs is None else packs
    rows = list(rows)
    plan = Plan(source_graph=source_graph, target_graph=target_graph)

    keepers: dict[str, str] = {}
    first_pass: dict[str, Decision] = {}
    for row in rows:
        key = row.stable_key
        if key in keepers:
            continue
        keepers[key] = row.node_id
        first_pass[key] = classify(row, mapping)

    for row in rows:
        key = row.stable_key
        if keepers.get(key) == row.node_id:
            plan.decisions.append(first_pass[key])
        else:
            plan.decisions.append(
                classify(row, mapping, duplicate_of=first_pass[key].disposition)
            )
    return plan


# ── guards ────────────────────────────────────────────────────────────────


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
    ident = (row.get("graph_id") or row.get("stable_id") or row.get("namespace")
             or row.get("name"))
    if not ident:
        return None
    # EVERY row collapses to its id tail. Returning the namespace string itself
    # (while `g_<id>` returns the bare id) made two spellings of one graph compare
    # UNEQUAL — a fail-open on the exact guard that exists to stop an in-place run.
    return _tail(str(ident)) or str(ident).strip().casefold() or None


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
        wanted = ref.casefold()
        for row in graph_rows:
            for field in ("graph_id", "stable_id", "namespace"):
                val = row.get(field)
                if val and str(val).casefold() == wanted:
                    return _canonical_from_row(row)
        # Names are matched SEPARATELY so an ambiguous name is a refusal, not a
        # first-match: two graphs with one name must never resolve to whichever
        # row happened to come first.
        by_name = [r for r in graph_rows if (r.get("name") or "").casefold() == wanted]
        if len(by_name) == 1:
            return _canonical_from_row(by_name[0])
        if len(by_name) > 1:
            return None
    # Only an id-shaped spelling is resolvable without a row that names it.
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
            "a migration whose source identity is unproven (pass the graph id or namespace)")
    if cb is None:
        raise CurationRefused(
            f"cannot resolve the target graph {target_graph!r} to a graph id — refusing to run: "
            "an unproven target could be the archive itself (pass the graph id or namespace)")
    if ca == cb:
        raise CurationRefused(
            f"source and target are the same graph ({ca!r}); the old graph is a "
            "read-only historic artifact (#11469)")


def off_switch_active(explicit: bool = False) -> bool:
    if explicit:
        return True
    return str(os.environ.get(OFF_SWITCH_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


# ── writing (additive, idempotent) ────────────────────────────────────────


@runtime_checkable
class PlanWriter(Protocol):
    """The only write surface a curation run is allowed to use.

    Deliberately single-verb: there is no ``delete``, ``retract`` or ``update``
    on this protocol, so "the pass cannot destroy anything" is a property of the
    type rather than of the caller's discipline. ``write_points`` returns ONLY the
    keys the target accepted, and raises on a refusal.
    """

    def write_points(self, target_graph: str, points: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        ...


def _ingest_refused(res: Any) -> str | None:
    """The reason a tool result means "not accepted", or None when it was accepted.

    ``ingest`` reports refusals as an error envelope (``{"error": ...``,
    ``{"violations": [...]``) rather than by raising, so counting the attempt as a
    success is how a ledger ends up claiming rows that do not exist.
    """
    if not isinstance(res, dict):
        return None if res is not None else "no result from the target"
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

    def write_points(self, target_graph: str, points: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        written: list[str] = []
        for i in range(0, len(points), self._batch):
            if not self._armed():
                # Mid-run stop: what was accepted so far is reported (and ledgered),
                # the rest is simply not attempted.
                return {"written": written, "stopped": "off-switch engaged"}
            batch = list(points[i:i + self._batch])
            try:
                raw = self._call(self.TOOL, {
                    "graph": target_graph,
                    "promotion_policy": "auto",
                    "granularity": "bulk",
                    "bundle": {"points": batch},
                })
                # Unwrapping is INSIDE the guard: `normalise_tool_result` raises on
                # the `isError` path, and a refusal there must still report the
                # batches that already landed.
                res = normalise_tool_result(raw)
            except CurationRefused as exc:
                raise CurationRefused(str(exc), written_keys=written) from exc
            except Exception as exc:  # transport/timeout AFTER earlier batches landed
                raise CurationRefused(f"target call failed: {type(exc).__name__}: {exc}",
                                      written_keys=written) from exc
            refused = _ingest_refused(res)
            if refused:
                # The earlier batches were accepted, so they are reported — a
                # partial run must not claim it wrote nothing.
                raise CurationRefused(refused, written_keys=written)
            written.extend(p["migrationKey"] for p in batch)
        return {"written": written}


class Ledger:
    """Local record of what this pass already migrated **into this target**.

    The ledger is the half of idempotency that does not depend on the target
    server: a key it holds is never sent again, whatever the target would do with
    it. It is scoped by target graph — the same row migrated into graph A has not
    been migrated into graph B — append-only, and fsynced, because a ledger that
    can be lost (or that answers for the wrong graph) is worse than none.
    """

    def __init__(self, path: str | os.PathLike[str], target: str):
        self.path = str(path)
        self.target = target
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
                    except (json.JSONDecodeError, KeyError):
                        continue  # a torn tail line is skipped, not fatal
        except FileNotFoundError:
            pass

    def __contains__(self, key: object) -> bool:
        return (self.target, key) in self._keys

    def __len__(self) -> int:
        return sum(1 for t, _ in self._keys if t == self.target)

    def record(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            for k in keys:
                fh.write(json.dumps({"target": self.target, "migrationKey": k}) + "\n")
                self._keys.add((self.target, k))
            fh.flush()
            os.fsync(fh.fileno())


@dataclass
class RunResult:
    applied: bool
    off: bool
    skipped_known: int
    written: int
    target_graph: str
    digest: str
    refused: str | None = None
    stopped: str | None = None
    identity: str | None = None

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
        }


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
    if ledger.target != plan.target_graph:
        raise CurationRefused(
            f"ledger is scoped to {ledger.target!r} but the plan targets "
            f"{plan.target_graph!r} — a ledger answering for another graph would skip rows "
            "this target never received")
    identity_note: str | None = None
    try:
        assert_distinct_graphs(plan.source_graph, plan.target_graph, graph_rows=graph_rows)
    except CurationRefused as exc:
        if apply:
            return RunResult(False, False, 0, 0, plan.target_graph, plan.digest(), refused=str(exc))
        # A dry run writes nothing, so an unproven identity is reported rather than
        # blocking the plan — the guard is strict exactly where a write can happen.
        identity_note = str(exc)
    if off_switch_active(off):
        return RunResult(False, True, 0, 0, plan.target_graph, plan.digest(), refused="off-switch active")
    if not apply:
        return RunResult(False, False, 0, 0, plan.target_graph, plan.digest(),
                         refused="dry run (--apply not given)", identity=identity_note)

    points = plan.bundle()["points"]
    pending = [p for p in points if p["migrationKey"] not in ledger]
    skipped = len(points) - len(pending)
    if not pending:
        return RunResult(True, False, skipped, 0, plan.target_graph, plan.digest())
    try:
        result = writer.write_points(plan.target_graph, pending)
    except CurationRefused as exc:
        # Only the batches the target actually ACCEPTED are recorded; the refused
        # attempt is not. A partial run reports what landed.
        accepted_partial = list(getattr(exc, "written_keys", ()) or [])
        ledger.record(accepted_partial)
        return RunResult(True, False, skipped, len(accepted_partial), plan.target_graph,
                         plan.digest(), refused=str(exc))
    accepted = [k for k in (result.get("written") or [])]
    ledger.record(accepted)
    return RunResult(True, False, skipped, len(accepted), plan.target_graph, plan.digest(),
                     stopped=result.get("stopped"))
