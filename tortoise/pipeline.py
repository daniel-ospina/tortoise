"""#2792 — configurable per-graph pipelines (CRM-style stage lifecycles).

A *pipeline* is the ordered, labelled stage list a stage-driven kind moves
through. It has two tiers, mirroring what every production CRM ships
(Salesforce Opportunity stages / HubSpot deal pipelines / Jira workflows):

- **canonical slots** — the small fixed set of *meanings* the graph must
  understand: ``entry``, ``active``, ``suspended``, ``terminal``. A terminal
  stage additionally declares an ``outcome`` (``won`` / ``lost`` /
  ``withdrawn`` / ``completed``). These live in core and are validated here.
- **tenant stages** — the ordered, labelled names a graph owns. A pack may
  declare sensible defaults (``kindDefs[<kind>].pipeline``); each graph may
  override them (add / rename / reorder / retire) without a code change or a
  pack release.

The **current stage is state** on the Object (``pipelineStageId`` /
``pipelineSlot`` / ``pipelineOutcome`` / ``pipelineStageSince`` / ``isClosed``),
so a query always returns one answer (#2792 indicator 5). **History is the
journal**: a stage move is written through the generic entity write surface
(``TortoiseSDK.update_entity``), which journals an ``EntityMutated`` record the
rebuild path replays, so the prior values are retained as dated history without
a second lifecycle axis (the same doctrine ``Object.status`` follows).

This module deliberately does **not** touch the hardcoded connector status fold
(``projection/entities.py``) — that is #2729's charter. ``Object.status`` keeps
its existence-axis meaning (``live``/``superseded``); the pipeline stage is a
separate property, the separation #2826's C14 recommends.

Storage note (why stages are denormalised, not edge-linked): the durability
registry added by #2814 (``projection/__init__.py::_config_classes``) captures
and restores **nodes only**, not edges. A ``:PipelineStage`` therefore carries
its own ``pipelineKey`` rather than being reached through a
Pipeline→PipelineStage edge (a shape the code deliberately never writes), so
pipeline configuration survives ``rebuild_all`` intact.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tortoise.entity_identity import resolve_entity_id

log = logging.getLogger(__name__)

# Label literals live HERE and are imported by ``projection._config_classes``
# (function-locally, to keep the projection → * → projection import cycle
# intact), so a rename cannot silently de-enrol the config class.
PIPELINE_LABEL = "Pipeline"
PIPELINE_STAGE_LABEL = "PipelineStage"

# Canonical slots + terminal outcomes. Fixing THESE (not the stage list) is the
# two-tier rule: "closed" stays reliable after a tenant renames/reorders.
PIPELINE_SLOTS: tuple[str, ...] = ("entry", "active", "suspended", "terminal")
PIPELINE_OUTCOMES: tuple[str, ...] = ("won", "lost", "withdrawn", "completed")

# Stage lifecycle (tenant-editable list membership, not the Object's stage).
STAGE_ACTIVE = "active"
STAGE_RETIRED = "retired"

#: Object props the fold maintains. One home so ``advance_stage`` (the write)
#: and ``current_stage`` (the read) cannot drift: ``current_stage`` builds its
#: projection from this tuple, through ``_STAGE_READ_KEYS``.
STAGE_PROPS: tuple[str, ...] = (
    "pipelineStageId", "pipelineSlot", "pipelineOutcome",
    "pipelineStageSince", "pipelineStageKey", "isClosed",
)
#: The public read key ``current_stage`` returns each ``STAGE_PROPS`` member
#: under. The keys are the read contract; the props are the storage.
_STAGE_READ_KEYS: dict[str, str] = {
    "pipelineStageId": "stageId",
    "pipelineSlot": "slot",
    "pipelineOutcome": "outcome",
    "pipelineStageSince": "since",
    "pipelineStageKey": "pipelineKey",
    "isClosed": "isClosed",
}
#: Raw (unrecognised) stage label parked on the Object for review — the
#: open-world capture of indicator 4 (never dropped, never auto-adopted).
PENDING_PROP = "pipelineStagePending"
#: WHICH pipeline reported the pending label. A SEPARATE prop on purpose:
#: ``pipelineStageKey`` names the pipeline of the Object's *current* stage, and
#: overwriting it with the reporter's key is exactly what disarmed every guard
#: (#2792 review, P1) — one stray unknown observation for another pipeline made
#: ``cur_stage`` unresolvable, so terminal / regression / stale-time were all
#: skipped. The pending pair is written and cleared together.
PENDING_KEY_PROP = "pipelineStagePendingKey"

_TERMINAL_SLOT = "terminal"

#: A stage id must not contain this character. ``stageKey`` is the composite
#: ``f"{pipeline_key}|{stage_id}"`` — the durability registry's single-property
#: node identity — so an unescaped ``|`` INSIDE a stage id makes the composite
#: ambiguous: ``("venture:deal", "a|b")`` and ``("venture:deal|a", "b")``
#: both yield ``venture:deal|a|b``. The second write then rewrites the shared
#: node's ``pipelineKey`` and the first pipeline SILENTLY LOSES that stage
#: (#2792 review, P2). Forbidding the separator in the id keeps the
#: decomposition total (the id half can never contain one), so the composite
#: is injective. The pipeline key itself may keep a ``|``: with a
#: separator-free id, splitting at the LAST ``|`` is unambiguous.
_STAGE_ID_FORBIDDEN = "|"


def _stage_id_error(stage_id: Any) -> str | None:
    """Why ``stage_id`` is not a legal stage id, else ``None``.

    ONE home for the rule, so the pack-declaration validator, ``add_stage``
    and the stage-row write cannot drift on what an id may contain.
    """
    if not isinstance(stage_id, str) or not stage_id.strip():
        return "'id' must be a non-empty string"
    if _STAGE_ID_FORBIDDEN in stage_id:
        return (
            f"'id' {stage_id!r} must not contain {_STAGE_ID_FORBIDDEN!r} — the "
            "composite stage key (pipeline|stage) uses it as its separator, so "
            "an id containing it would collide with another pipeline's stage")
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _as_utc(ts: Any):
    """Parse an ISO timestamp to an aware UTC datetime, or None if unparsable."""
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


# ── Declaration validation (called by pack_registry) ──────────────────────


def validate_pipeline_decl(decl: Any) -> list[str]:
    """Validate a ``kindDefs[<kind>].pipeline`` declaration.

    Returns a list of human-readable errors (empty == valid). Shape checked
    here; the declaration is optional, so only a present ``pipeline`` pays.
    """
    errors: list[str] = []
    if not isinstance(decl, dict):
        return [f"'pipeline' must be a map, got {type(decl).__name__}"]
    unknown = sorted(set(decl) - {"stages", "defaultStage"})
    if unknown:
        errors.append(
            f"'pipeline' has unknown key(s) {unknown} "
            f"(allowed: 'stages', 'defaultStage')")
    stages = decl.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append("'pipeline.stages' must be a non-empty list")
        return errors
    ids: list[str] = []
    labels: list[str] = []
    orders: list[int] = []
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            errors.append(f"pipeline.stages[{i}] must be a map")
            continue
        bad = sorted(set(stage) - {"id", "label", "order", "slot", "outcome"})
        if bad:
            errors.append(f"pipeline.stages[{i}] has unknown key(s) {bad} "
                          "(allowed: 'id', 'label', 'order', 'slot', 'outcome')")
        sid = stage.get("id")
        sid_error = _stage_id_error(sid)
        if sid_error is not None:
            errors.append(f"pipeline.stages[{i}]: {sid_error}")
        else:
            if sid in ids:
                errors.append(f"pipeline.stages[{i}]: duplicate stage id {sid!r}")
            ids.append(sid)
        label = stage.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"pipeline.stages[{i}]: 'label' must be a non-empty string")
        elif label.strip().casefold() in labels:
            # A label is a stage *reference* (``Pipeline.resolve``), so two
            # stages sharing one makes the reference ambiguous.
            errors.append(
                f"pipeline.stages[{i}]: duplicate stage label {label!r} "
                "(a label is a stage reference, so it must be unique)")
        else:
            labels.append(label.strip().casefold())
        order = stage.get("order")
        if not isinstance(order, int) or isinstance(order, bool) or order < 0:
            errors.append(f"pipeline.stages[{i}]: 'order' must be an int >= 0")
        elif order in orders:
            # The forward/regression judgement is ``target.order <
            # cur_stage.order``, so a move BETWEEN two tied stages is neither
            # forward nor backward and would be applied with no guard at all
            # — the exact shape ``reorder_stages`` refuses. The pack-default
            # path is the only path a graph that never edits its pipeline
            # takes, so the tie must be refused at declaration time too.
            errors.append(
                f"pipeline.stages[{i}]: duplicate stage order {order!r} — "
                "ties make the forward/regression guard non-deterministic")
        else:
            orders.append(order)
        slot = stage.get("slot")
        if slot not in PIPELINE_SLOTS:
            errors.append(
                f"pipeline.stages[{i}]: 'slot' must be one of "
                f"{', '.join(PIPELINE_SLOTS)}, got {slot!r}")
            continue
        outcome = stage.get("outcome")
        if slot == _TERMINAL_SLOT:
            if outcome not in PIPELINE_OUTCOMES:
                errors.append(
                    f"pipeline.stages[{i}]: a terminal stage must declare "
                    f"'outcome' in {', '.join(PIPELINE_OUTCOMES)}, got {outcome!r}")
        elif outcome is not None:
            errors.append(
                f"pipeline.stages[{i}]: 'outcome' is only valid on a terminal "
                f"stage (slot is {slot!r})")
    default = decl.get("defaultStage")
    if default is not None:
        if not isinstance(default, str) or not default.strip():
            errors.append("'pipeline.defaultStage' must be a non-empty string")
        elif default not in ids:
            errors.append(
                f"'pipeline.defaultStage' {default!r} is not one of the "
                f"declared stage ids")
    return errors


def declared_pipeline_for(registry, namespaced_kind: str) -> dict | None:
    """The pack-declared pipeline for a namespaced kind, or None.

    ``namespaced_kind`` is ``{namespace}:{kind}`` (#2767). Iterates the loaded
    packs and returns the first matching ``kindDefs[kind]['pipeline']``.
    """
    if registry is None or ":" not in namespaced_kind:
        return None
    ns, kind = namespaced_kind.split(":", 1)
    pack = registry.packs.get(ns)
    if pack is None:
        return None
    kd = pack.kind_defs.get(kind, {})
    if not isinstance(kd, dict):
        return None
    decl = kd.get("pipeline")
    return decl if isinstance(decl, dict) else None


# ── Data model ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineStage:
    stage_id: str
    label: str
    order: int
    slot: str
    outcome: str | None = None
    status: str = STAGE_ACTIVE
    retired_at: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.slot == _TERMINAL_SLOT


@dataclass
class Pipeline:
    pipeline_key: str
    default_stage_id: str | None
    source: str  # "pack-default" | "graph-override"
    stages: list[PipelineStage] = field(default_factory=list)

    def by_id(self, stage_id: str) -> PipelineStage | None:
        for s in self.stages:
            if s.stage_id == stage_id:
                return s
        return None

    def resolve(self, ref: str) -> PipelineStage | None:
        """Resolve a stage by id (exact) or label (case-insensitive).

        A label is a stage reference, so labels are unique within a pipeline
        (enforced on declaration and by every mutator) and the first match is
        the only match.
        """
        if not isinstance(ref, str) or not ref.strip():
            return None
        probe = ref.strip()
        hit = self.by_id(probe)
        if hit is not None:
            return hit
        low = probe.casefold()
        for s in self.stages:
            # The STORED side is compared trimmed too: labels are canonicalised
            # on write (``add_stage``/``rename_stage``/``_pipeline_from_decl``),
            # and this keeps a pre-existing padded row resolvable rather than
            # parking a stage that exists as an "unknown stage" candidate.
            if s.label.strip().casefold() == low:
                return s
        return None

    def active_stages(self) -> list[PipelineStage]:
        return sorted((s for s in self.stages if s.status == STAGE_ACTIVE),
                      key=lambda s: (s.order, s.stage_id))


@dataclass
class StageMove:
    """Outcome of one stage-transition attempt."""

    applied: bool
    reason: str = ""               # "" | no-stage | unknown-stage |
                                   # unresolvable-current-stage |
                                   # stale-transition | regression-guard |
                                   # terminal-guard
    stage: PipelineStage | None = None
    pending: str | None = None     # raw label parked for review (unknown-stage)

    def __bool__(self) -> bool:  # convenience: `if move:`
        return self.applied


# ── Graph reads ───────────────────────────────────────────────────────────


def _row_to_stage(props: dict) -> PipelineStage:
    outcome = props.get("outcome")
    return PipelineStage(
        stage_id=props.get("stageId", ""),
        label=(props.get("label") or "").strip(),
        order=int(props.get("order") or 0),
        slot=props.get("slot", "entry"),
        outcome=outcome if outcome else None,
        status=props.get("status") or STAGE_ACTIVE,
        retired_at=props.get("retiredAt") or None,
    )


def _read_override(graph, pipeline_key: str) -> Pipeline | None:
    rows = graph.query(
        "MATCH (p:Pipeline {pipelineKey:$k}) RETURN p.defaultStageId, "
        "p.source",
        params={"k": pipeline_key}).result_set
    if not rows:
        return None
    default_id, source = rows[0][0], rows[0][1]
    srows = graph.query(
        "MATCH (s:PipelineStage {pipelineKey:$k}) "
        "RETURN s.stageId, s.label, s.order, s.slot, s.outcome, s.status, "
        "s.retiredAt ORDER BY s.order, s.stageId",
        params={"k": pipeline_key}).result_set
    stages = [_row_to_stage({
        "stageId": r[0], "label": r[1], "order": r[2], "slot": r[3],
        "outcome": r[4], "status": r[5], "retiredAt": r[6]}) for r in srows]
    return Pipeline(pipeline_key, default_id or None, source or "graph-override",
                    stages)


def _pipeline_from_decl(pipeline_key: str, decl: dict) -> Pipeline:
    stages = [
        PipelineStage(
            # Canonicalise on write: ``resolve`` trims the probe and
            # ``_label_owner`` compares trimmed, so an untrimmed stored label
            # passed every uniqueness check but could not be resolved by its
            # natural spelling (a YAML ``label: " Draft "`` reached that state).
            stage_id=s["id"], label=s["label"].strip(), order=s["order"],
            slot=s["slot"], outcome=s.get("outcome") or None)
        for s in decl["stages"]
    ]
    default = decl.get("defaultStage")
    if default is None:
        active = sorted(stages, key=lambda s: (s.order, s.stage_id))
        default = active[0].stage_id if active else None
    return Pipeline(pipeline_key, default, "pack-default", stages)


def effective_pipeline(graph, pipeline_key: str, *,
                       declared: dict | None = None) -> Pipeline:
    """The effective pipeline: the graph's ``:Pipeline`` record when it has
    copied-on-write one, else the pack-declared default.

    Never writes — a graph that has not overridden anything reads the pack
    default on the fly (so pre-existing graphs need no migration).
    """
    override = _read_override(graph, pipeline_key)
    if override is not None:
        return override
    if declared is None:
        return Pipeline(pipeline_key, None, "pack-default", [])
    return _pipeline_from_decl(pipeline_key, declared)


def list_stages(graph, pipeline_key: str, *, declared: dict | None = None,
                include_retired: bool = False) -> list[PipelineStage]:
    pipe = effective_pipeline(graph, pipeline_key, declared=declared)
    stages = pipe.stages if include_retired else pipe.active_stages()
    return sorted(stages, key=lambda s: (s.order, s.stage_id))


def current_stage(graph, object_ref: str) -> dict:
    """The Object's current stage state (one answer, no chain-walking).

    The projection is built from ``STAGE_PROPS``, so a prop the fold writes is
    a prop this read returns — the two cannot drift.

    The coordinate is resolved route-then-refuse (#3633), never by a
    hand-rolled ``name = $n OR id = $n ... LIMIT 1``: same-name coexistence is
    legal (#3590 D2), and the bare ``LIMIT 1`` silently picked one carrier, so
    this read could answer from the wrong Object.
    """
    oid = resolve_entity_id(graph, "Object", object_ref)
    if oid is None:
        return {}
    cols = ", ".join(f"o.{p}" for p in STAGE_PROPS)
    rows = graph.query(
        f"MATCH (o:Object {{id:$oid}}) "
        f"RETURN o.name, {cols}, o.{PENDING_PROP}",
        params={"oid": oid}).result_set
    if not rows:
        return {}
    r = rows[0]
    out: dict[str, Any] = {"name": r[0]}
    for i, prop in enumerate(STAGE_PROPS, start=1):
        out[_STAGE_READ_KEYS[prop]] = r[i]
    out["outcome"] = out["outcome"] or None
    out["isClosed"] = (bool(out["isClosed"])
                       if out["isClosed"] is not None else False)
    out["pending"] = r[len(STAGE_PROPS) + 1]
    return out


def unknown_stage_candidates(graph, *, pipeline_key: str | None = None) -> list[dict]:
    """The review queue for unrecognised stage labels (indicator 4).

    A candidate is an Object carrying ``pipelineStagePending`` — the raw label
    extraction reported that is not in the effective pipeline. It is retained
    (journaled with the write) and surfaced here; it is NEVER auto-adopted.
    """
    if pipeline_key:
        # The reporter's key is ``pipelineStagePendingKey`` — NOT
        # ``pipelineStageKey``, which names the pipeline of the Object's
        # *current* stage and stays untouched by a park (P1).
        rows = graph.query(
            "MATCH (o:Object) WHERE o.pipelineStagePending IS NOT NULL "
            "AND o.pipelineStagePendingKey = $k "
            "RETURN o.name, o.pipelineStagePending, o.pipelineStagePendingAt",
            params={"k": pipeline_key}).result_set
    else:
        rows = graph.query(
            "MATCH (o:Object) WHERE o.pipelineStagePending IS NOT NULL "
            "RETURN o.name, o.pipelineStagePending, o.pipelineStagePendingAt, "
            "o.pipelineStagePendingKey").result_set
    if pipeline_key:
        return [{"name": r[0], "rawLabel": r[1], "seenAt": r[2]}
                for r in rows]
    return [{"name": r[0], "rawLabel": r[1], "seenAt": r[2], "pipelineKey": r[3]}
            for r in rows]


# ── Graph writes (pipeline configuration) ─────────────────────────────────


def _write_stage(graph, pipeline_key: str, stage: PipelineStage,
                 *, created_at: str | None = None) -> None:
    # The write is the LAST line of defence for the composite identity: a
    # direct ``seed_pipeline`` bypasses ``add_stage`` and a hand-built
    # declaration, and a ``|`` in the id would then hijack another pipeline's
    # stage node (see ``_STAGE_ID_FORBIDDEN``).
    stage_id_error = _stage_id_error(stage.stage_id)
    if stage_id_error is not None:
        raise ValueError(stage_id_error)
    # The node identity is COMPOSITE (``pipelineKey|stageId``): the declared
    # ``id`` is pack-local, so two pipelines may legitimately both declare
    # ``stg_contact``. The user-facing ``stageId`` stays per-pipeline; the
    # composite is what makes the durability registry's single-property
    # identity sound.
    graph.query(
        "MERGE (s:PipelineStage {stageKey:$sk}) "
        "SET s.pipelineKey=$k, s.stageId=$sid, s.label=$label, s.order=$order, "
        "s.slot=$slot, s.outcome=$outcome, s.status=$status, "
        "s.retiredAt=$retiredAt, "
        "s.createdAt=coalesce(s.createdAt, $createdAt)",
        params={
            "sk": _stage_key(pipeline_key, stage.stage_id),
            "sid": stage.stage_id, "k": pipeline_key, "label": stage.label,
            "order": stage.order, "slot": stage.slot,
            "outcome": stage.outcome, "status": stage.status,
            "retiredAt": stage.retired_at,
            "createdAt": created_at or _now(),
        })


def _stage_key(pipeline_key: str, stage_id: str) -> str:
    return f"{pipeline_key}|{stage_id}"


def _touch_pipeline(graph, pipeline_key: str, *, actor: str = "") -> None:
    """Bump the override's version and stamp the edit (audit trail, R10)."""
    graph.query(
        "MATCH (p:Pipeline {pipelineKey:$k}) "
        "SET p.version=coalesce(p.version, 0) + 1, p.updatedAt=$at, "
        "p.updatedBy=$by",
        params={"k": pipeline_key, "at": _now(), "by": actor})


def _write_pipeline(graph, pipe: Pipeline, *, actor: str = "") -> None:
    graph.query(
        "MERGE (p:Pipeline {pipelineKey:$k}) "
        "SET p.kind=$kind, p.defaultStageId=$defaultStageId, p.source=$source, "
        "p.version=coalesce(p.version, 0) + 1, p.updatedAt=$at, p.updatedBy=$by",
        params={
            "k": pipe.pipeline_key, "kind": pipe.pipeline_key,
            "defaultStageId": pipe.default_stage_id, "source": pipe.source,
            "at": _now(), "by": actor})


def seed_pipeline(graph, pipeline_key: str, declared: dict, *,
                  actor: str = "") -> Pipeline:
    """Materialise a graph override from the pack-declared default.

    Copy-on-write: this is what the first edit calls, and it is idempotent for
    an already-overridden graph (the existing override is returned untouched).
    """
    existing = _read_override(graph, pipeline_key)
    if existing is not None:
        return existing
    pipe = _pipeline_from_decl(pipeline_key, declared)
    pipe.source = "graph-override"
    for stage in pipe.stages:
        _write_stage(graph, pipeline_key, stage)
    _write_pipeline(graph, pipe, actor=actor)
    return pipe


def _base_pipeline(graph, pipeline_key: str, declared: dict | None) -> Pipeline:
    """The graph's pipeline as it stands NOW, without materialising one.

    ``_require_override`` performs the copy-on-write SEED — a durable write
    (``:Pipeline`` + every ``:PipelineStage``, both enrolled in #2814's
    authoritative config registry). Validating an edit through it meant a
    REFUSED edit still flipped the graph to ``source="graph-override"``, so
    ``effective_pipeline`` short-circuited on the override forever and a later
    pack release no longer reached the graph (and this change exposes no verb
    that returns a graph to ``pack-default``). The mutators therefore validate
    against THIS side-effect-free read and only seed once the edit is known to
    be legal.
    """
    existing = _read_override(graph, pipeline_key)
    if existing is not None:
        return existing
    if declared is None:
        raise ValueError(
            f"no pipeline is declared for {pipeline_key!r} and the graph has "
            "no override — nothing to edit")
    return _pipeline_from_decl(pipeline_key, declared)


def _require_override(graph, pipeline_key: str, declared: dict | None,
                      *, actor: str) -> Pipeline:
    """The graph's override to edit.

    An override already in the graph is editable WITHOUT the pack declaration:
    the pack may be unloaded or renamed, and refusing then made every mutator
    unusable on a graph that owned a perfectly editable pipeline — with a
    message that misstated the reason. The declaration is only required to
    CREATE an override (copy-on-write).
    """
    existing = _read_override(graph, pipeline_key)
    if existing is not None:
        return existing
    if declared is None:
        raise ValueError(
            f"no pipeline is declared for {pipeline_key!r} and the graph has "
            "no override — nothing to edit")
    return seed_pipeline(graph, pipeline_key, declared, actor=actor)


def _label_owner(pipe: Pipeline, label: str, *,
                 exclude_stage_id: str | None = None) -> PipelineStage | None:
    """The stage already carrying ``label`` (case-insensitive), if any.

    A label is a stage reference (``Pipeline.resolve``), so two stages sharing
    one makes the reference ambiguous — and the winner used to depend on the
    store's row order.
    """
    low = label.strip().casefold()
    for s in pipe.stages:
        if s.stage_id == exclude_stage_id:
            continue
        if s.label.strip().casefold() == low:
            return s
    return None


def add_stage(graph, pipeline_key: str, *, stage_id: str, label: str,
              slot: str, outcome: str | None = None, order: int | None = None,
              declared: dict | None = None, actor: str = "") -> PipelineStage:
    """Add a tenant stage. Validated against the canonical slot vocabulary."""
    if slot not in PIPELINE_SLOTS:
        raise ValueError(f"slot must be one of {PIPELINE_SLOTS}, got {slot!r}")
    if slot == _TERMINAL_SLOT and outcome not in PIPELINE_OUTCOMES:
        raise ValueError(
            f"a terminal stage must declare outcome in {PIPELINE_OUTCOMES}, "
            f"got {outcome!r}")
    if slot != _TERMINAL_SLOT and outcome is not None:
        raise ValueError(f"outcome is only valid on a terminal stage, slot={slot!r}")
    stage_id_error = _stage_id_error(stage_id)
    if stage_id_error is not None:
        raise ValueError(stage_id_error)
    if not label or not label.strip():
        raise ValueError("label must be non-empty")
    label = label.strip()
    pipe = _base_pipeline(graph, pipeline_key, declared)
    if pipe.by_id(stage_id) is not None:
        raise ValueError(f"stage id {stage_id!r} already exists in {pipeline_key!r}")
    dup = _label_owner(pipe, label)
    if dup is not None:
        raise ValueError(
            f"label {label!r} is already used by stage {dup.stage_id!r} in "
            f"{pipeline_key!r}; a label is a stage reference, so it must be "
            "unique")
    if order is None:
        order = max((s.order for s in pipe.stages), default=0) + 1
    else:
        # An explicit ``order`` carries the SAME constraint as a declared one.
        # The forward/regression judgement is ``target.order <
        # cur_stage.order``, so a tie makes a move between the two stages
        # neither forward nor backward and it is applied with no guard at all
        # — the shape ``validate_pipeline_decl`` and ``reorder_stages`` both
        # refuse. Without this check the tenant EDIT path re-created the very
        # tie the declaration path had just been hardened against.
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            raise ValueError(f"order must be an int >= 0, got {order!r}")
        if any(s.order == order for s in pipe.stages):
            raise ValueError(
                f"order {order!r} is already used by another stage in "
                f"{pipeline_key!r}; ties make the forward/regression guard "
                "non-deterministic")
    # The edit is legal: only NOW seed the copy-on-write override (a refused
    # edit must not materialise one — see ``_base_pipeline``).
    _require_override(graph, pipeline_key, declared, actor=actor)
    stage = PipelineStage(stage_id=stage_id, label=label, order=order,
                          slot=slot, outcome=outcome)
    _write_stage(graph, pipeline_key, stage)
    _touch_pipeline(graph, pipeline_key, actor=actor)
    return stage


def rename_stage(graph, pipeline_key: str, stage_id: str, label: str, *,
                 declared: dict | None = None, actor: str = "") -> PipelineStage:
    """Rename a stage's label. ``stageId`` is unchanged, so every Object
    sitting in the stage — and every derived flag — is unaffected."""
    if not label or not label.strip():
        raise ValueError("label must be non-empty")
    label = label.strip()
    pipe = _base_pipeline(graph, pipeline_key, declared)
    stage = pipe.by_id(stage_id) or pipe.resolve(stage_id)
    if stage is None:
        raise ValueError(f"unknown stage {stage_id!r} in {pipeline_key!r}")
    dup = _label_owner(pipe, label, exclude_stage_id=stage.stage_id)
    if dup is not None:
        raise ValueError(
            f"label {label!r} is already used by stage {dup.stage_id!r} in "
            f"{pipeline_key!r}; a label is a stage reference, so it must be "
            "unique")
    _require_override(graph, pipeline_key, declared, actor=actor)
    graph.query(
        "MATCH (s:PipelineStage {stageKey:$sk}) SET s.label=$label",
        params={"sk": _stage_key(pipeline_key, stage.stage_id), "label": label})
    _touch_pipeline(graph, pipeline_key, actor=actor)
    # Return the POST-write state: ``stage`` was read before the write, so its
    # label is stale. A caller using the return value (the obvious shape, and
    # the one ``add_stage``/``retire_stage`` honour) would otherwise see the
    # pre-rename label while ``list_stages`` reports the new one.
    return PipelineStage(stage.stage_id, label, stage.order, stage.slot,
                         stage.outcome, stage.status, stage.retired_at)


def reorder_stages(graph, pipeline_key: str, ordered_stage_ids: list[str], *,
                   declared: dict | None = None, actor: str = "") -> list[PipelineStage]:
    """Set the order of the ACTIVE stages from ``ordered_stage_ids``.

    Every active stage must appear exactly once — a partial order would leave
    ties that make the forward/regression guard non-deterministic."""
    pipe = _base_pipeline(graph, pipeline_key, declared)
    active = {s.stage_id for s in pipe.active_stages()}
    if len(ordered_stage_ids) != len(active) or set(ordered_stage_ids) != active:
        raise ValueError(
            "ordered_stage_ids must list every active stage exactly once "
            f"(active={sorted(active)}, got={ordered_stage_ids})")
    # The edit is legal: only now seed the copy-on-write override.
    _require_override(graph, pipeline_key, declared, actor=actor)
    out: list[PipelineStage] = []
    # The renumber must not collide with a RETIRED stage's order. ``retire_stage``
    # leaves those rows (and the Objects sitting in them) in place, so
    # renumbering the active block from 1 blindly re-created the very tie
    # ``validate_pipeline_decl`` and ``add_stage`` refuse — and a tie makes the
    # forward/regression guard (``target.order < cur_stage.order``)
    # non-deterministic, applying the move with NO guard (the same call is
    # refused as ``regression-guard`` without the tie). Allocate the smallest
    # unused positive integers instead, so the result is total; the retired
    # stages keep their own orders (pushing them behind the active block would
    # make every move OUT of a retired stage a backward one).
    used = {s.order for s in pipe.stages if s.status != STAGE_ACTIVE}
    nxt = 1
    for sid in ordered_stage_ids:
        while nxt in used:
            nxt += 1
        order = nxt
        used.add(order)
        nxt += 1
        stage = pipe.by_id(sid)
        graph.query(
            "MATCH (s:PipelineStage {stageKey:$sk}) SET s.order=$order",
            params={"sk": _stage_key(pipeline_key, sid), "order": order})
        out.append(PipelineStage(stage.stage_id, stage.label, order, stage.slot,
                                 stage.outcome, stage.status, stage.retired_at))
    _touch_pipeline(graph, pipeline_key, actor=actor)
    return out


def retire_stage(graph, pipeline_key: str, stage_id: str, *,
                 declared: dict | None = None, actor: str = "") -> PipelineStage:
    """Retire a stage — it stops being offered, but Objects sitting in it keep
    their stage (never silently moved). The default stage cannot be retired."""
    pipe = _base_pipeline(graph, pipeline_key, declared)
    stage = pipe.by_id(stage_id) or pipe.resolve(stage_id)
    if stage is None:
        raise ValueError(f"unknown stage {stage_id!r} in {pipeline_key!r}")
    if pipe.default_stage_id == stage.stage_id:
        raise ValueError(
            f"cannot retire the default stage {stage.stage_id!r}; set another "
            "default first")
    _require_override(graph, pipeline_key, declared, actor=actor)
    at = _now()
    graph.query(
        "MATCH (s:PipelineStage {stageKey:$sk}) "
        "SET s.status=$status, s.retiredAt=$at",
        params={"sk": _stage_key(pipeline_key, stage.stage_id),
                "status": STAGE_RETIRED, "at": at})
    _touch_pipeline(graph, pipeline_key, actor=actor)
    return PipelineStage(stage.stage_id, stage.label, stage.order, stage.slot,
                         stage.outcome, STAGE_RETIRED, at)


def set_default_stage(graph, pipeline_key: str, stage_id: str, *,
                      declared: dict | None = None, actor: str = "") -> None:
    pipe = _base_pipeline(graph, pipeline_key, declared)
    stage = pipe.by_id(stage_id) or pipe.resolve(stage_id)
    if stage is None or stage.status != STAGE_ACTIVE:
        raise ValueError(f"unknown or retired stage {stage_id!r} in {pipeline_key!r}")
    _require_override(graph, pipeline_key, declared, actor=actor)
    graph.query(
        "MATCH (p:Pipeline {pipelineKey:$k}) SET p.defaultStageId=$d",
        params={"k": pipeline_key, "d": stage.stage_id})
    _touch_pipeline(graph, pipeline_key, actor=actor)


# ── Stage movement ────────────────────────────────────────────────────────


def _resolve_object(graph, ref: str) -> tuple[str, dict] | None:
    """The Object coordinate, resolved route-then-refuse (#3633).

    A hand-rolled ``name = $n OR id = $n ... LIMIT 1`` silently picked one of
    two same-name live carriers (#3590 D2), so a stage could be journaled onto
    the WRONG Object and the terminal/regression/stale guards evaluated
    against the wrong current stage. The resolver routes a name to exactly one
    id and REFUSES on ambiguity; the props are then read by that id.
    """
    oid = resolve_entity_id(graph, "Object", ref)
    if oid is None:
        return None
    rows = graph.query(
        "MATCH (o:Object {id:$oid}) "
        "RETURN o.id, o.name, o.pipelineStageId, o.pipelineSlot, "
        "o.pipelineStageSince, o.pipelineStageKey, o.objectKind, o.isClosed",
        params={"oid": oid}).result_set
    if not rows:
        return None
    r = rows[0]
    return r[0] or oid, {
        "name": r[1], "stageId": r[2], "slot": r[3], "since": r[4],
        "pipelineKey": r[5], "objectKind": r[6], "isClosed": r[7]}


def advance_stage(sdk, object_ref: str, pipeline_key: str, stage_ref: str, *,
                  at: str | None = None, source: str = "", reopen: bool = False,
                  correction: bool = False, reason: str = "",
                  declared: dict | None = None) -> StageMove:
    """Apply one observed stage transition to an Object.

    The write goes through ``sdk.update_entity`` — the ONE generic entity write
    — so it is journaled (``EntityMutated``) and replayed on every rebuild, and
    the history is the dated record sequence.

    Guards (the resurrection vector the design names):
    - **kind binding** — the pipeline key must equal the Object's namespaced
      ``objectKind`` (#2767), so no Object can be advanced through a pipeline
      that is not its own; an Object with no namespaced kind cannot be bound
      to any pipeline and is refused.
    - **unresolvable current stage** — an Object whose stored stage does not
      resolve in the effective pipeline is refused, never advanced: skipping
      the guards is the resurrection vector. The refusal has no public
      escape hatch (a repair verb is #2728); a drifted Object is repaired at
      the configuration level, not by forcing the move through.
    - **no stage reported** — a non-string/empty ``stage_ref`` is a no-op (it
      is absence, not an unrecognised label, so it is not parked).
    - **unknown stage** — not resolved by id or label → the raw label is parked
      on ``pipelineStagePending`` for review and the current stage state is NOT
      changed (``pipelineStageKey`` is left alone — see ``PENDING_KEY_PROP``).
    - **terminal guard** — a closed Object is not reopened by a passing
      mention. "Closed" is the Object's durable ``isClosed`` marker **or** a
      terminal current stage, so a pack release that reclassifies the stage's
      slot cannot resurrect it; only an explicit ``reopen=True`` to a
      non-terminal stage moves it.
    - **regression guard** — a backward move is recorded as history but not
      applied unless ``correction=True``. ``reopen`` opts out only for a move
      OUT of a terminal stage (a reopen is inherently backward); it is not a
      regression opt-out for a live Object.
    - **stale-transition guard** — a transition whose valid time precedes the
      Object's current ``pipelineStageSince`` is not applied (out-of-order
      processing), unless ``correction=True``.
    """
    graph = sdk._get_proj().g
    stage_ts = at or _now()
    resolved = _resolve_object(graph, object_ref)
    if resolved is None:
        raise ValueError(f"no Object named or identified by {object_ref!r}")
    oid, cur = resolved

    # A pipeline keys on the NAMESPACED kind (#2767), so the Object's kind is
    # the binding. Without it, any Object could be advanced through any
    # pipeline, and a caller's wrong key was accepted silently — the vector
    # that made the P1 resurrection reachable.
    kind = cur.get("objectKind")
    if not isinstance(kind, str) or not kind.strip():
        # An absent/blank kind cannot bind ANY pipeline, so it must fail
        # closed rather than skipping the binding (a legacy/raw-Cypher Object
        # was otherwise advanceable through any pipeline and permanently
        # stamped with that pipeline's key).
        raise ValueError(
            f"{object_ref!r} carries no namespaced objectKind, so no pipeline "
            f"can be bound to it (refusing {pipeline_key!r})")
    if kind != pipeline_key:
        raise ValueError(
            f"pipeline {pipeline_key!r} does not apply to {object_ref!r}: its "
            f"kind is {kind!r} (a pipeline keys on the namespaced kind)")

    pipe = effective_pipeline(graph, pipeline_key, declared=declared)
    if not pipe.stages:
        # A MISSING pipeline is a configuration error, distinct from an
        # unrecognised stage: parking every value as a candidate would hide a
        # graph that was never configured (or whose stages were all retired).
        raise ValueError(
            f"no pipeline is configured for {pipeline_key!r} — declare one in "
            "the pack or override one on this graph before advancing stages")
    if not isinstance(stage_ref, str) or not stage_ref.strip():
        # "No stage reported" is NOT an unrecognised label: coercing it with
        # ``str()`` parked a review candidate literally named "None". The
        # queue is only trustworthy if it holds labels a human could act on.
        return StageMove(applied=False, reason="no-stage")

    target = pipe.resolve(stage_ref)
    if target is None or target.status != STAGE_ACTIVE:
        # Open-world capture: park the RAW reported label, never drop it and
        # never auto-adopt it as a stage. The park writes ONLY the pending
        # pair — ``pipelineStageKey`` stays the pipeline of the CURRENT stage,
        # or the guards below could not be evaluated against it (P1).
        raw = stage_ref.strip()
        sdk.update_entity(oid, pipelineStagePending=raw,
                          pipelineStagePendingAt=stage_ts,
                          pipelineStagePendingKey=pipeline_key)
        return StageMove(applied=False, reason="unknown-stage", pending=raw)

    cur_key = cur.get("pipelineKey")
    if cur.get("stageId") and cur_key and cur_key != pipeline_key:
        # Reached only for an Object whose kind could not bind it: a stage id
        # from another pipeline must not be read as this one's current stage,
        # and silently skipping the guards is the resurrection vector.
        raise ValueError(
            f"{object_ref!r} is currently on pipeline {cur_key!r}, not "
            f"{pipeline_key!r}; a cross-pipeline move cannot be guarded")
    cur_stage = None
    if cur.get("stageId"):
        if cur_key == pipeline_key:
            cur_stage = pipe.by_id(cur["stageId"])
        if cur_stage is None:
            # FAIL CLOSED. An unresolvable current stage (declaration drift:
            # the pack renamed/dropped the id the Object sits in; a missing
            # ``pipelineStageKey``; a corrupt/legacy value) must NOT fall
            # through to the write. Falling through skipped the terminal,
            # regression and stale-time guards and then rewrote
            # ``isClosed``, resurrecting a closed Object — the same
            # resurrection vector the cross-pipeline branch above refuses.
            # Refuse the move; the persisted state is left untouched.
            return StageMove(applied=False,
                             reason="unresolvable-current-stage", stage=target)
    if cur_stage is not None and not correction:
        # Valid-time ordering (the CRM bitemporal shape): a meeting held in
        # January but processed in March must not regress a February close.
        prev_t, in_t = _as_utc(cur.get("since")), _as_utc(stage_ts)
        if prev_t is not None and in_t is not None and in_t < prev_t:
            return StageMove(applied=False, reason="stale-transition", stage=target)
    # The durable closed marker is the Object's ``isClosed`` prop, NOT the
    # effective declaration's slot. A graph that has not overridden its
    # pipeline reads the pack default on the fly, so a pack release that
    # reclassifies the stage the Object sits in from ``terminal`` to an
    # active slot takes effect immediately; keying stickiness off the
    # resolved slot alone then skipped this guard and the write rewrote
    # ``isClosed=True`` to ``False`` — a durable, journaled resurrection.
    # The two sources must AGREE: an Object the graph records as closed
    # stays closed, whatever the live declaration now says about its slot.
    #
    # The marker is also INDEPENDENT of the current stage: an Object the graph
    # records as closed with no resolvable stage (``isClosed`` is written
    # through the generic entity surface — ``create_object(..., isClosed=
    # True)``, ``update_entity(id, isClosed=True)``, or the #2728 repair verb
    # that clears ``pipelineStageId`` while the marker remains) was reopened by
    # an ordinary later mention, because this check used to live inside
    # ``if cur_stage is not None``. That is the mirror of the
    # ``unresolvable-current-stage`` refusal above and is refused the same way.
    was_closed = bool(cur.get("isClosed")) or (
        cur_stage is not None and cur_stage.is_terminal)
    if was_closed and not (reopen and not target.is_terminal):
        return StageMove(applied=False, reason="terminal-guard", stage=target)
    # ``reopen`` is the TERMINAL guard's escape hatch, not a general
    # regression opt-out: on a live (non-terminal) Object it dropped this
    # guard too and applied a silent backward move. The regression guard's
    # documented opt-out is ``correction``; a reopen still has to travel
    # FROM a terminal stage to be a reopen at all.
    if (cur_stage is not None and target.order < cur_stage.order
            and not correction and not (reopen and cur_stage.is_terminal)):
        return StageMove(applied=False, reason="regression-guard", stage=target)

    props: dict[str, Any] = {
        "pipelineStageId": target.stage_id,
        "pipelineSlot": target.slot,
        "pipelineOutcome": target.outcome or "",
        "pipelineStageSince": stage_ts,
        "pipelineStageKey": pipeline_key,
        "isClosed": target.is_terminal,
        PENDING_PROP: None,
        PENDING_KEY_PROP: None,
        "pipelineStagePendingAt": None,
    }
    if source:
        props["pipelineStageSource"] = source
    if reason:
        props["pipelineStageReason"] = reason
    sdk.update_entity(oid, **props)
    return StageMove(applied=True, stage=target)


def declared_pipelines(registry) -> dict[str, dict]:
    """``{namespaced_kind: pipeline_decl}`` for every pack-declared pipeline.

    The enumerate-the-universe helper a Settings surface (#2728) reads."""
    out: dict[str, dict] = {}
    if registry is None:
        return out
    for p in registry.packs.values():
        for kind, kd in (p.kind_defs or {}).items():
            if isinstance(kd, dict) and isinstance(kd.get("pipeline"), dict):
                out[f"{p.namespace}:{kind}"] = kd["pipeline"]
    return out
