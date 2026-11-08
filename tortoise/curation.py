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
  skip a row it already migrated *before* it reaches the wire. The second half
  is what makes "run it twice, nothing changes" true even for a target that
  does not dedup.
* **It can be switched off.** ``TORTOISE_CURATE_OFF=1`` (or ``--off``) turns
  every write into a report line. The switch is checked before each write, not
  once at start-up, so flipping it mid-run stops the run.

``source_graph == target_graph`` is refused outright: with the source at its
node cap the only thing such a run could do is write noise into the archive.

Nothing in this module is exported from ``tortoise/__all__`` and no SDK method
is added — the public surface is unchanged (#4282: surface changes need
the owner's approval, and this issue grants none).
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
#: hung off them. These are the rows a curated graph exists to keep.
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


# ── the normalised row ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Row:
    """One node as a read surface returned it, normalised to what curation needs.

    ``props`` keeps the whole source row so a decision can be re-derived and
    audited from the report alone.
    """

    node_id: str
    node_type: str  # point | operator | object | event | source | subject
    kind: str | None = None
    content: str = ""
    content_hash: str | None = None
    props: Mapping[str, Any] = field(default_factory=dict)

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
    except Exception:
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
    """
    pack = pack_for(row.kind, row.node_type, packs)
    key = row.stable_key
    node_type = row.node_type

    if node_type == "operator":
        return Decision(row.node_id, node_type, row.kind, CARRY,
                        "operator (IMPL/NAND/composedOf) — the reasoning layer; carried "
                        "with its endpoints so the claim does not arrive as a floating node",
                        pack, key)

    if duplicate_of:
        return Decision(row.node_id, node_type, row.kind, NOT_CARRIED,
                        f"exact content duplicate of {duplicate_of} (same content hash); "
                        "the keeper carries it. MERGE of near-duplicates is #5006's scope, "
                        "not this pass's", pack, key)

    if row.kind in SIGNAL_KINDS:
        return Decision(row.node_id, node_type, row.kind, CARRY,
                        f"{row.kind} — an owner-level artifact the curated graph exists to keep",
                        pack, key)

    if row.kind == "diary":
        return Decision(row.node_id, node_type, row.kind, UNDECIDABLE,
                        "agent diary — narration shaped like a narrative, but it is an authored "
                        "artifact, so neither rule decides it", pack, key)

    if row.kind == "evidence":
        return Decision(row.node_id, node_type, row.kind, CARRY,
                        "evidence — carried; its Source tier must be resolved at ingest "
                        "(the source layer is 100% session-backed, see the inventory)", pack, key)

    marker = _narrative(row.content or "")
    if marker:
        return Decision(row.node_id, node_type, row.kind, NOT_CARRIED,
                        f"turn narration, not a claim (matched marker {marker!r})", pack, key)

    if not (row.content or "").strip():
        return Decision(row.node_id, node_type, row.kind, NOT_CARRIED,
                        "empty content — nothing to carry", pack, key)

    return Decision(row.node_id, node_type, row.kind, UNDECIDABLE,
                    "statement with no deciding rule: carrying it is a per-row judgement "
                    "(reported for review, not guessed)", pack, key)


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
                        pack, key)
    return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                    "object with a referent-shaped name: whether it is an entity the curated "
                    "graph needs is a judgement, not a rule", pack, key)


def classify_source(row: Row, packs: Mapping[tuple[str, str], str]) -> Decision:
    pack = pack_for(row.kind, row.node_type, packs)
    key = row.stable_key
    if str(row.content).startswith("session:") or row.kind == "agentSession":
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        "session transcript used as a Source: §9.1/D1 (#10000) says the raw "
                        "tier lives in Postgres, not in the graph — but this tenant's record "
                        "holds 0 rows (#10175), so the graph is the only copy and 'do not "
                        "carry it' would leave the transcript with no home. Needs a decision, "
                        "not a rule", pack, key)
    return Decision(row.node_id, row.node_type, row.kind, CARRY,
                    "a real provenance artifact (not a session transcript)", pack, key)


def classify_event(row: Row, packs: Mapping[tuple[str, str], str]) -> Decision:
    pack = pack_for(row.kind, row.node_type, packs)
    key = row.stable_key
    if row.kind in (None, "", "session", "agentSession") or str(row.content).startswith("session:"):
        return Decision(row.node_id, row.node_type, row.kind, UNDECIDABLE,
                        "agent-session Event: the raw/episodic tier is supposed to live outside "
                        "the graph (#10000), but the record holds 0 rows for this tenant, so "
                        "dropping it here has no safe destination", pack, key)
    return Decision(row.node_id, row.node_type, row.kind, CARRY,
                    "a domain Event (not a session container)", pack, key)


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
                    "no classifier for this node type in this pass", pack_for(row.kind, row.node_type, packs),
                    row.stable_key)


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
        """The additive write bundle: points only, each with its migrationKey.

        Concrete connection-building (which claims imply which edges) is the
        ingest contract's job (#888 W4): this bundle carries the rows and the
        keys, and never a deletion.
        """
        return {
            "migrationKeyPrefix": MIGRATION_KEY_PREFIX,
            "sourceGraph": self.source_graph,
            "digest": self.digest(),
            "points": [
                {
                    "ref": d.stable_key,
                    "migrationKey": d.stable_key,
                    "kind": d.kind or "statement",
                    "nodeType": d.node_type,
                    "sourceNodeId": d.node_id,
                    "pack": d.pack,
                }
                for d in self.carry_rows()
            ],
        }


def build_plan(
    rows: Iterable[Row],
    *,
    source_graph: str,
    target_graph: str,
    packs: Mapping[tuple[str, str], str] | None = None,
) -> Plan:
    """Classify every row, folding exact content duplicates onto a keeper.

    The first row seen for a given ``stable_key`` is the keeper — rows arrive in
    a stable order (the reader sorts by node id), so the same input always
    produces the same plan.
    """
    mapping = pack_kind_map() if packs is None else packs
    plan = Plan(source_graph=source_graph, target_graph=target_graph)
    keepers: dict[str, str] = {}
    for row in rows:
        key = row.stable_key
        dup = keepers.get(key)
        if dup is None and row.node_type in ("point", "operator"):
            keepers[key] = row.node_id
        plan.decisions.append(classify(row, mapping, duplicate_of=dup))
    return plan


# ── guards ────────────────────────────────────────────────────────────────


class CurationRefused(RuntimeError):
    """A guard refused the run. Never caught into a silent no-op."""


def _same_graph(a: str, b: str) -> bool:
    """True when two spellings name the same graph.

    The surface offers three accepted spellings for one graph — namespace
    (``org_<org>_g_<id>``), graph id (``g_<id>``) and the bare id ``<id>`` — so a
    naive string comparison would let a caller target its own source by spelling
    it differently. The comparison therefore runs on the resolved id tail.
    """

    def tail(g: str) -> str:
        g = (g or "").strip().lower()
        if not g:
            return ""
        t = g.split("_g_")[-1]
        if t.startswith("g_") and len(t) > 2:
            t = t[2:]
        return t

    na, nb = (a or "").strip().lower(), (b or "").strip().lower()
    if not na or not nb:
        return False
    return na == nb or tail(na) == tail(nb)


def assert_distinct_graphs(source_graph: str, target_graph: str) -> None:
    """Refuse a migration whose target is its own source.

    The source sits at its node cap, so the only write it could accept is a
    refusal — and if the cap were ever lifted, an in-place run would write
    curated noise back into the archive the ruling protects.
    """
    if not target_graph:
        raise CurationRefused("no target graph named: a curation pass needs a distinct target")
    if _same_graph(source_graph, target_graph):
        raise CurationRefused(
            f"source and target are the same graph ({source_graph!r}); the old graph is a "
            "read-only historic artifact (#11469)"
        )


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
    type rather than of the caller's discipline.
    """

    def write_points(self, target_graph: str, points: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        ...


class IngestWriter:
    """``PlanWriter`` over the hosted ``tortoise_ingest`` tool (#888 W4).

    ``ingest`` dedups points by (content hash + kind) and merges Sources by url,
    so a repeated write folds instead of duplicating. It is called with
    ``promotion_policy='auto'`` because a curated row should arrive live, and
    ``granularity='bulk'`` to keep one call per batch.
    """

    TOOL = "tortoise_ingest"

    def __init__(self, call_tool: Callable[[str, Mapping[str, Any]], Any], *, batch_size: int = 100):
        self._call = call_tool
        self._batch = max(1, batch_size)

    def write_points(self, target_graph: str, points: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        written: list[str] = []
        results: list[Any] = []
        for i in range(0, len(points), self._batch):
            batch = list(points[i:i + self._batch])
            res = self._call(self.TOOL, {
                "graph": target_graph,
                "promotion_policy": "auto",
                "granularity": "bulk",
                "bundle": {"points": batch},
            })
            results.append(res)
            written.extend(p["migrationKey"] for p in batch)
        return {"written": written, "results": results}


class Ledger:
    """Local record of what this pass already migrated.

    The ledger is the half of idempotency that does not depend on the target
    server: a key it holds is never sent again, whatever the target would do
    with it. It is append-only and written atomically, because a ledger that can
    be lost mid-run is the same as no ledger.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = str(path)
        self._keys: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._keys.add(json.loads(line)["migrationKey"])
                    except (json.JSONDecodeError, KeyError):
                        continue  # a torn tail line is skipped, not fatal
        except FileNotFoundError:
            pass

    def __contains__(self, key: object) -> bool:
        return key in self._keys

    def __len__(self) -> int:
        return len(self._keys)

    def record(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            for k in keys:
                fh.write(json.dumps({"migrationKey": k}) + "\n")
                self._keys.add(k)
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "off": self.off,
            "skipped_known": self.skipped_known,
            "written": self.written,
            "target_graph": self.target_graph,
            "digest": self.digest,
            "refused": self.refused,
        }


def run_plan(
    plan: Plan,
    *,
    writer: PlanWriter,
    ledger: Ledger,
    apply: bool = False,
    off: bool = False,
) -> RunResult:
    """Execute a plan. Dry by default; additive and idempotent when applied.

    ``apply=False`` is a plan-only run: it reports what *would* be written and
    touches nothing — that is the mode to use against a target that does not
    exist yet.
    """
    assert_distinct_graphs(plan.source_graph, plan.target_graph)
    if off_switch_active(off):
        return RunResult(False, True, 0, 0, plan.target_graph, plan.digest(), refused="off-switch active")
    if not apply:
        return RunResult(False, False, 0, 0, plan.target_graph, plan.digest(), refused="dry run (--apply not given)")

    points = plan.bundle()["points"]
    pending = [p for p in points if p["migrationKey"] not in ledger]
    skipped = len(points) - len(pending)
    if not pending:
        return RunResult(True, False, skipped, 0, plan.target_graph, plan.digest())
    result = writer.write_points(plan.target_graph, pending)
    ledger.record(result.get("written") or [p["migrationKey"] for p in pending])
    return RunResult(True, False, skipped, len(pending), plan.target_graph, plan.digest())
