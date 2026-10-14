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
``(:Pipeline)-[:HAS_STAGE]->(:PipelineStage)`` edge, so pipeline configuration
survives ``rebuild_all`` intact.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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

#: Object props the fold writes. One home so ``advance_stage`` and
#: ``current_stage`` cannot drift.
STAGE_PROPS: tuple[str, ...] = (
    "pipelineStageId", "pipelineSlot", "pipelineOutcome",
    "pipelineStageSince", "pipelineStageKey", "isClosed",
)
#: Raw (unrecognised) stage label parked on the Object for review — the
#: open-world capture of indicator 4 (never dropped, never auto-adopted).
PENDING_PROP = "pipelineStagePending"

_TERMINAL_SLOT = "terminal"


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
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            errors.append(f"pipeline.stages[{i}] must be a map")
            continue
        bad = sorted(set(stage) - {"id", "label", "order", "slot", "outcome"})
        if bad:
            errors.append(f"pipeline.stages[{i}] has unknown key(s) {bad} "
                          "(allowed: 'id', 'label', 'order', 'slot', 'outcome')")
        sid = stage.get("id")
        if not isinstance(sid, str) or not sid.strip():
            errors.append(f"pipeline.stages[{i}]: 'id' must be a non-empty string")
        else:
            if sid in ids:
                errors.append(f"pipeline.stages[{i}]: duplicate stage id {sid!r}")
            ids.append(sid)
        label = stage.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"pipeline.stages[{i}]: 'label' must be a non-empty string")
        order = stage.get("order")
        if not isinstance(order, int) or isinstance(order, bool) or order < 0:
            errors.append(f"pipeline.stages[{i}]: 'order' must be an int >= 0")
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
        """Resolve a stage by id (exact) or label (case-insensitive)."""
        if not isinstance(ref, str) or not ref.strip():
            return None
        probe = ref.strip()
        hit = self.by_id(probe)
        if hit is not None:
            return hit
        low = probe.casefold()
        for s in self.stages:
            if s.label.casefold() == low:
                return s
        return None

    def active_stages(self) -> list[PipelineStage]:
        return sorted((s for s in self.stages if s.status == STAGE_ACTIVE),
                      key=lambda s: (s.order, s.stage_id))


@dataclass
class StageMove:
    """Outcome of one stage-transition attempt."""

    applied: bool
    reason: str = ""               # "" | unknown-stage | regression-guard | terminal-guard
    stage: PipelineStage | None = None
    pending: str | None = None     # raw label parked for review (unknown-stage)

    def __bool__(self) -> bool:  # convenience: `if move:`
        return self.applied


# ── Graph reads ───────────────────────────────────────────────────────────


def _row_to_stage(props: dict) -> PipelineStage:
    outcome = props.get("outcome")
    return PipelineStage(
        stage_id=props.get("stageId", ""),
        label=props.get("label", ""),
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
        "s.retiredAt",
        params={"k": pipeline_key}).result_set
    stages = [_row_to_stage({
        "stageId": r[0], "label": r[1], "order": r[2], "slot": r[3],
        "outcome": r[4], "status": r[5], "retiredAt": r[6]}) for r in srows]
    return Pipeline(pipeline_key, default_id or None, source or "graph-override",
                    stages)


def _pipeline_from_decl(pipeline_key: str, decl: dict) -> Pipeline:
    stages = [
        PipelineStage(
            stage_id=s["id"], label=s["label"], order=s["order"],
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
    """The Object's current stage state (one answer, no chain-walking)."""
    rows = graph.query(
        "MATCH (o:Object) WHERE o.name = $n OR o.id = $n "
        "RETURN o.name, o.pipelineStageId, o.pipelineSlot, o.pipelineOutcome, "
        "o.pipelineStageSince, o.isClosed, o.pipelineStagePending "
        "LIMIT 1",
        params={"n": object_ref}).result_set
    if not rows:
        return {}
    r = rows[0]
    return {
        "name": r[0], "stageId": r[1], "slot": r[2], "outcome": r[3] or None,
        "since": r[4], "isClosed": bool(r[5]) if r[5] is not None else False,
        "pending": r[6],
    }


def unknown_stage_candidates(graph, *, pipeline_key: str | None = None) -> list[dict]:
    """The review queue for unrecognised stage labels (indicator 4).

    A candidate is an Object carrying ``pipelineStagePending`` — the raw label
    extraction reported that is not in the effective pipeline. It is retained
    (journaled with the write) and surfaced here; it is NEVER auto-adopted.
    """
    if pipeline_key:
        rows = graph.query(
            "MATCH (o:Object) WHERE o.pipelineStagePending IS NOT NULL "
            "AND o.pipelineStageKey = $k "
            "RETURN o.name, o.pipelineStagePending, o.pipelineStagePendingAt",
            params={"k": pipeline_key}).result_set
    else:
        rows = graph.query(
            "MATCH (o:Object) WHERE o.pipelineStagePending IS NOT NULL "
            "RETURN o.name, o.pipelineStagePending, o.pipelineStagePendingAt, "
            "o.pipelineStageKey").result_set
    if pipeline_key:
        return [{"name": r[0], "rawLabel": r[1], "seenAt": r[2]}
                for r in rows]
    return [{"name": r[0], "rawLabel": r[1], "seenAt": r[2], "pipelineKey": r[3]}
            for r in rows]


# ── Graph writes (pipeline configuration) ─────────────────────────────────


def _write_stage(graph, pipeline_key: str, stage: PipelineStage,
                 *, created_at: str | None = None) -> None:
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


def _require_override(graph, pipeline_key: str, declared: dict | None,
                      *, actor: str) -> Pipeline:
    if declared is None:
        raise ValueError(
            f"no pipeline is declared for {pipeline_key!r} and the graph has "
            "no override — nothing to edit")
    return seed_pipeline(graph, pipeline_key, declared, actor=actor)


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
    if not stage_id or not stage_id.strip():
        raise ValueError("stage_id must be non-empty")
    if not label or not label.strip():
        raise ValueError("label must be non-empty")
    pipe = _require_override(graph, pipeline_key, declared, actor=actor)
    if pipe.by_id(stage_id) is not None:
        raise ValueError(f"stage id {stage_id!r} already exists in {pipeline_key!r}")
    if order is None:
        order = max((s.order for s in pipe.stages), default=0) + 1
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
    pipe = _require_override(graph, pipeline_key, declared, actor=actor)
    stage = pipe.by_id(stage_id) or pipe.resolve(stage_id)
    if stage is None:
        raise ValueError(f"unknown stage {stage_id!r} in {pipeline_key!r}")
    graph.query(
        "MATCH (s:PipelineStage {stageKey:$sk}) SET s.label=$label",
        params={"sk": _stage_key(pipeline_key, stage.stage_id), "label": label})
    _touch_pipeline(graph, pipeline_key, actor=actor)
    return stage


def reorder_stages(graph, pipeline_key: str, ordered_stage_ids: list[str], *,
                   declared: dict | None = None, actor: str = "") -> list[PipelineStage]:
    """Set the order of the ACTIVE stages from ``ordered_stage_ids``.

    Every active stage must appear exactly once — a partial order would leave
    ties that make the forward/regression guard non-deterministic."""
    pipe = _require_override(graph, pipeline_key, declared, actor=actor)
    active = {s.stage_id for s in pipe.active_stages()}
    if len(ordered_stage_ids) != len(active) or set(ordered_stage_ids) != active:
        raise ValueError(
            "ordered_stage_ids must list every active stage exactly once "
            f"(active={sorted(active)}, got={ordered_stage_ids})")
    out: list[PipelineStage] = []
    for i, sid in enumerate(ordered_stage_ids, start=1):
        stage = pipe.by_id(sid)
        graph.query(
            "MATCH (s:PipelineStage {stageKey:$sk}) SET s.order=$order",
            params={"sk": _stage_key(pipeline_key, sid), "order": i})
        out.append(PipelineStage(stage.stage_id, stage.label, i, stage.slot,
                                 stage.outcome, stage.status, stage.retired_at))
    _touch_pipeline(graph, pipeline_key, actor=actor)
    return out


def retire_stage(graph, pipeline_key: str, stage_id: str, *,
                 declared: dict | None = None, actor: str = "") -> PipelineStage:
    """Retire a stage — it stops being offered, but Objects sitting in it keep
    their stage (never silently moved). The default stage cannot be retired."""
    pipe = _require_override(graph, pipeline_key, declared, actor=actor)
    stage = pipe.by_id(stage_id) or pipe.resolve(stage_id)
    if stage is None:
        raise ValueError(f"unknown stage {stage_id!r} in {pipeline_key!r}")
    if pipe.default_stage_id == stage.stage_id:
        raise ValueError(
            f"cannot retire the default stage {stage.stage_id!r}; set another "
            "default first")
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
    pipe = _require_override(graph, pipeline_key, declared, actor=actor)
    stage = pipe.by_id(stage_id) or pipe.resolve(stage_id)
    if stage is None or stage.status != STAGE_ACTIVE:
        raise ValueError(f"unknown or retired stage {stage_id!r} in {pipeline_key!r}")
    graph.query(
        "MATCH (p:Pipeline {pipelineKey:$k}) SET p.defaultStageId=$d",
        params={"k": pipeline_key, "d": stage.stage_id})
    _touch_pipeline(graph, pipeline_key, actor=actor)


# ── Stage movement ────────────────────────────────────────────────────────


def _resolve_object(graph, ref: str) -> tuple[str, dict] | None:
    rows = graph.query(
        "MATCH (o:Object) WHERE o.name = $n OR o.id = $n "
        "RETURN o.id, o.name, o.pipelineStageId, o.pipelineSlot, "
        "o.pipelineStageSince, o.pipelineStageKey LIMIT 1",
        params={"n": ref}).result_set
    if not rows:
        return None
    r = rows[0]
    return r[0] or r[1], {
        "name": r[1], "stageId": r[2], "slot": r[3], "since": r[4],
        "pipelineKey": r[5]}


def advance_stage(sdk, object_ref: str, pipeline_key: str, stage_ref: str, *,
                  at: str | None = None, source: str = "", reopen: bool = False,
                  correction: bool = False, reason: str = "",
                  declared: dict | None = None) -> StageMove:
    """Apply one observed stage transition to an Object.

    The write goes through ``sdk.update_entity`` — the ONE generic entity write
    — so it is journaled (``EntityMutated``) and replayed on every rebuild, and
    the history is the dated record sequence.

    Guards (the resurrection vector the design names):
    - **unknown stage** — not resolved by id or label → the raw label is parked
      on ``pipelineStagePending`` for review and the state is NOT changed.
    - **terminal guard** — a terminal Object is not reopened by a passing
      mention; only an explicit ``reopen=True`` to a non-terminal stage moves it.
    - **regression guard** — a backward move is recorded as history but not
      applied unless ``correction=True``.
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

    pipe = effective_pipeline(graph, pipeline_key, declared=declared)
    if not pipe.stages:
        # A MISSING pipeline is a configuration error, distinct from an
        # unrecognised stage: parking every value as a candidate would hide a
        # graph that was never configured (or whose stages were all retired).
        raise ValueError(
            f"no pipeline is configured for {pipeline_key!r} — declare one in "
            "the pack or override one on this graph before advancing stages")
    target = pipe.resolve(stage_ref)
    if target is None or target.status != STAGE_ACTIVE:
        # Open-world capture: park the RAW reported label, never drop it and
        # never auto-adopt it as a stage.
        raw = str(stage_ref).strip()
        if raw:
            sdk.update_entity(oid, pipelineStagePending=raw,
                              pipelineStagePendingAt=stage_ts,
                              pipelineStageKey=pipeline_key)
        return StageMove(applied=False, reason="unknown-stage", pending=raw or None)

    cur_stage = None
    if cur.get("stageId") and cur.get("pipelineKey") == pipeline_key:
        # Only the SAME pipeline's current stage can gate a move; a stage id
        # from another pipeline must not be read as this one's current stage.
        cur_stage = pipe.by_id(cur["stageId"])
    if cur_stage is not None and not correction:
        # Valid-time ordering (the CRM bitemporal shape): a meeting held in
        # January but processed in March must not regress a February close.
        prev_t, in_t = _as_utc(cur.get("since")), _as_utc(stage_ts)
        if prev_t is not None and in_t is not None and in_t < prev_t:
            return StageMove(applied=False, reason="stale-transition", stage=target)
    if cur_stage is not None:
        if cur_stage.is_terminal and not (
                reopen and not target.is_terminal):
            return StageMove(applied=False, reason="terminal-guard", stage=target)
        if target.order < cur_stage.order and not correction and not reopen:
            return StageMove(applied=False, reason="regression-guard", stage=target)

    props: dict[str, Any] = {
        "pipelineStageId": target.stage_id,
        "pipelineSlot": target.slot,
        "pipelineOutcome": target.outcome or "",
        "pipelineStageSince": stage_ts,
        "pipelineStageKey": pipeline_key,
        "isClosed": target.is_terminal,
        PENDING_PROP: None,
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
