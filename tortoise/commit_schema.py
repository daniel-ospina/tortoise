"""Layer-1 commit contract — the SHARED schema module (epic #909, slice 5a).

The single source of truth for the POST /v1/sessions/commit payload
(plan §6.1, epic docs/epics/2026-08-11-epic909-value-first-mining/04-plan.md).
The local value-first extractor MIRRORS this module (payload serialization +
client_commit_id computation) and the hosted endpoint VALIDATES with it
(Layer-1 422s) — §5.2 item 4 ("kind sources collapse to ONE") holds because
both sides import the same Pydantic models and the same canonicalization.

Module contents:
  1. Pydantic models for the full §6.1 payload (CommitPayload and nested).
  2. Closed-vocab compilation from PackRegistry at RUNTIME — NOT snapshotted
     (seam note: #949/#950/#951 land later without re-opening this module;
     ``get_vocab()`` reflects the installed packs, ``refresh_vocab()``
     recompiles without a code change).
  3. Layer-1 deterministic validation — required-fields split (400 class) vs
     shape/semantic violations (422 class with field reasons, §4.5 table).
  4. Canonicalization — deterministic JSON + client_commit_id recompute via
     the existing ``ids.content_hash()`` (plan §6.1 canonicalization note;
     :CommitRecord EXTENDS the IngestKey/begin_ingest pattern).
  5. L2 reconciliation computed IN MEMORY (no writes — the write path is the
     endpoint's job, #953 slice 5b): points by pt_<sha>, entities by
     (name, kind), operators by (src, dst, op_type) — net-new non-episodic
     delta = the budget numerator.
  6. Budget accounting (THE AUTHORITATIVE §6.1 block): soft 15 → WARN /
     >25 → held[] (first adjudication only, PL3) / >50 → 402 fail-closed /
     supersede-only deltas exempt (R-14) / MERGE + dedup burn zero.

400 vs 422 (cap resolution — plan §4.5 + issue #952 decision): the per-type
payload caps (MAX_PAYLOAD_POINTS / MAX_ENTITIES / MAX_OPERATORS) are Layer-1
→ 422; the 400 status is RESERVED for missing required payload fields.
``validate_payload_dict`` returns code ``missing_required_fields`` for the
400 class; everything else is 422-class.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .ids import content_hash
from .pack_registry import (
    CANONICAL_POINT_KINDS,
    DECISION_POINT_KINDS,
    KNOWN_SOURCE_TYPES,
    PackRegistry,
)
from .quota import (
    MAX_ENTITIES,
    MAX_OPERATORS,
    MAX_PAYLOAD_POINTS,
    MAX_VALUE_POINTS_PER_SESSION,
)
from .source_credibility import SOURCE_KIND_DEFAULTS

_logger = logging.getLogger(__name__)

__all__ = [
    # constants
    "BUDGET_SOFT", "BUDGET_HARD", "BUDGET_CEILING",
    "REQUIRED_FIELDS", "VALID_COMMIT_STATUSES",
    # vocab
    "Vocab", "CORE_POINT_KINDS", "CORE_SOURCE_KINDS",
    "compile_vocab", "get_vocab", "refresh_vocab",
    # models
    "ProvenanceRef", "Source", "Entity", "Point", "OperatorTarget",
    "Operator", "ExtractorInfo", "TelemetryExtractor", "TelemetryModel",
    "TelemetryCounts", "Telemetry", "CommitPayload",
    # Layer-1
    "Layer1Result", "Layer1Error", "missing_required_fields", "shape_errors",
    "validate_layer1", "validate_payload_dict", "atomicity_violations",
    # domain integrity rules (#405)
    "validate_domain_rules", "domain_block_warnings", "_infer_payload_domain",
    # canonicalization
    "canonical_payload", "compute_client_commit_id", "point_content_id",
    # reconciliation
    "GraphState", "PointReconcile", "EntityReconcile", "OperatorReconcile",
    "ReconcileResult", "reconcile_payload",
    # budget + L1 record semantics
    "BudgetDecision", "adjudicate_budget", "CommitRecordState",
    "is_l1_replay", "is_first_adjudication", "plan_commit", "CommitPlan",
]


# ── Budget constants (authoritative §6.1 block — imported from quota.py) ──

BUDGET_SOFT = MAX_VALUE_POINTS_PER_SESSION["soft"]       # 15 → WARN telemetry
BUDGET_HARD = MAX_VALUE_POINTS_PER_SESSION["hard"]       # 25 → held[] (PL3)
BUDGET_CEILING = MAX_VALUE_POINTS_PER_SESSION["ceiling"]  # 50 → 402 fail-closed


# ── Closed vocab (compiled from PackRegistry at RUNTIME — seam note) ──────
# The pointKind/sourceKind closed vocabs are compiled from the installed
# expansion packs every time the process needs them. Nothing here is
# snapshotted: #949/#950/#951 (pack manifests landing in later slices) change
# the compiled vocab WITHOUT touching this module. Core values mirror
# ONTOLOGY §5 (pointKind incl. `event` — amendment §4.3 #9; sourceKind —
# the extensible source TYPE vocabulary incl. `agentSession`, amendment #6).

CORE_POINT_KINDS: frozenset[str] = frozenset(
    CANONICAL_POINT_KINDS | {"humanApproval", "event"}
)
CORE_SOURCE_KINDS: frozenset[str] = frozenset(
    KNOWN_SOURCE_TYPES | set(SOURCE_KIND_DEFAULTS) | {"agentSession"}
)


@dataclass(frozen=True)
class Vocab:
    """The compiled closed vocabulary the Layer-1 gate validates against."""

    point_kinds: frozenset[str]
    source_kinds: frozenset[str]

    def __contains__(self, kind: str) -> bool:  # pragmatic: Vocab ⊇ kind
        return kind in self.point_kinds or kind in self.source_kinds


def compile_vocab(packs_dir: Path | str | None = None) -> Vocab:
    """Compile the closed vocab from PackRegistry at RUNTIME.

    Point kinds = core §5 point kinds (incl. ``humanApproval`` + ``event``)
    ∪ pack-declared pointKinds in BOTH bare and ``ns:kind`` namespaced form
    (the value brief may serialize either; the registry's canonical form is
    the namespaced one — pack_registry.list_all_kinds). Source kinds =
    ontology §5 source-type vocabulary (KNOWN_SOURCE_TYPES ∪ registered
    SOURCE_KIND_DEFAULTS incl. tier forms ∪ ``agentSession``) ∪ pack-declared
    extraction sourceTypes.
    """
    if packs_dir is None:
        packs_dir = Path(__file__).resolve().parent.parent / "packs"
    registry = PackRegistry(packs_dir)
    registry.load_all()
    pack_point: set[str] = set()
    pack_sources: set[str] = set()
    for ns, pack in registry.packs.items():
        pack_point.update(pack.point_kinds)
        pack_point.update(f"{ns}:{k}" for k in pack.point_kinds)
        pack_sources.update(pack.extraction.get("sourceTypes") or [])
    return Vocab(
        point_kinds=frozenset(CORE_POINT_KINDS | pack_point),
        source_kinds=frozenset(CORE_SOURCE_KINDS | pack_sources),
    )


_vocab_lock = threading.Lock()
_vocab_cache: Vocab | None = None


def get_vocab() -> Vocab:
    """Lazily compiled closed vocab (thread-safe). Runtime, not snapshotted."""
    global _vocab_cache
    if _vocab_cache is None:
        with _vocab_lock:
            if _vocab_cache is None:
                _vocab_cache = compile_vocab()
    return _vocab_cache


def refresh_vocab() -> Vocab:
    """Recompile the vocab (packs installed/updated at runtime)."""
    global _vocab_cache
    with _vocab_lock:
        _vocab_cache = compile_vocab()
    return _vocab_cache


# ── Pydantic models — the full §6.1 payload ──────────────────────────────

# extra="forbid" everywhere: the §6.1 contract is exact; a stray field is
# client drift and must 422, not be silently dropped (the local mirror uses
# the same models, so drift is caught on the client side too).


class ProvenanceRef(BaseModel):
    """Local file provenance — path is BASENAME only (privacy, W-7)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    spans: list[str] = Field(default_factory=list)


class Source(BaseModel):
    """EXTERNAL referenced artifact (R4 chain) — the session Source itself
    is derived from provenance_refs server-side, not sent."""

    model_config = ConfigDict(extra="forbid")

    sourceKind: str = Field(min_length=1)
    url: str = Field(min_length=1)
    credibilityTier: str | None = None
    contentHash: str | None = None


class Entity(BaseModel):
    """Object node payload form (mapped to objectKind at write, §4.1)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    passes_frequency_gate: bool = True


class Point(BaseModel):
    """A single extracted point — content-addressed id, closed kind vocab."""

    model_config = ConfigDict(extra="forbid")

    id: str
    content: str = Field(min_length=1, max_length=1000)
    pointKind: str = Field(min_length=1)
    reason: Literal["NEW", "REVISES"]  # CONNECTS/RESOLVES CUT in v1 (§6.1)
    confidence: float
    c_cal: float
    about_entities: list[str] = Field(default_factory=list)
    source_ref: str = Field(min_length=1)  # REQUIRED — resolves to a Source
    quote: str = Field(default="", max_length=200)
    status: Literal["live", "draft"] = "draft"

    @field_validator("id")
    @classmethod
    def _content_addressed_id(cls, v: str) -> str:
        if not v.startswith("pt_"):
            raise ValueError(
                "point id must use the content-addressed pt_<sha> form"
            )
        return v


class OperatorTarget(BaseModel):
    """MITIGATES edge-identity triple — the operator MERGE key (PL1)."""

    model_config = ConfigDict(extra="forbid")

    src: str
    dst: str
    op_type: Literal["IMPL"] = "IMPL"


class Operator(BaseModel):
    """Epistemic operator — IMPL / NAND (direction REQUIRED) / MITIGATES
    (target + strength [0.10, 0.50] REQUIRED). No op_<sha> ids (PL1)."""

    model_config = ConfigDict(extra="forbid")

    src: str
    dst: str
    op_type: Literal["IMPL", "NAND", "MITIGATES"]
    direction: Literal["unidirectional", "bidirectional"] | None = None
    target: OperatorTarget | None = None
    strength: float | None = Field(default=None, ge=0.10, le=0.50)


class ExtractorInfo(BaseModel):
    """Top-level extractor block (SINGLE copy, §6.1)."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    calibration_version: str = Field(min_length=1)


class TelemetryExtractor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    calibration_version: str | None = None  # T11 (#1272): persist the calibration stamp


class TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    id: str = Field(min_length=1)
    cfg_hash: str | None = None


class TelemetryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kept: int = 0
    candidate: int = 0
    segment: int = 0
    window: int = 0
    empty_windows: int = 0


class Telemetry(BaseModel):
    """Extraction telemetry — NO conversation content, NO graph-side counts
    (server derives merge/supersede/held/draft/live from Session counters)."""

    model_config = ConfigDict(extra="forbid")

    extractor: TelemetryExtractor
    model: TelemetryModel
    counts: TelemetryCounts = Field(default_factory=TelemetryCounts)
    keep_ratio: float | None = None   # T11: None = not measured (server derives)
    dedup_hits: int | None = None     # T11: None = not measured (server derives)
    frontier_calls: int = 0
    llm_cost_usd: float | None = None
    extraction_ms: int = 0
    retry_count: int = 0
    last_error_code: str | None = None
    confidence_histogram: list[int] | None = None  # T11: None = not measured

    @field_validator("confidence_histogram")
    @classmethod
    def _ten_buckets(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        if len(v) != 10:
            raise ValueError(
                "confidence_histogram must have exactly 10 buckets (0.1 steps)"
            )
        return v


class CommitEvent(BaseModel):
    """An episodic record — an Event NODE (issue #1013: NEVER a point with
    pointKind event). eventKind from the ontology vocabulary."""

    model_config = ConfigDict(extra="forbid")

    id: str
    eventKind: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=1000)
    edu_index: int | None = None
    confidence: float = 0.9
    about_entities: list[str] = Field(default_factory=list)
    source_ref: str = Field(min_length=1)  # REQUIRED — resolves to a Source
    captured_at: str | None = None

    @field_validator("id")
    @classmethod
    def _content_addressed_id(cls, v: str) -> str:
        if not v.startswith("ev_"):
            raise ValueError(
                "event id must use the content-addressed ev_<sha> form"
            )
        return v


class CommitPayload(BaseModel):
    """The full POST /v1/sessions/commit request body (§6.1)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    session_id: str = Field(min_length=1)  # client-stable
    client_commit_id: str = Field(min_length=1)  # SHA-256 over canonical JSON
    captured_at: str  # ISO8601 (bi-temporal capture, amendment §4.3 #2)
    extractor: ExtractorInfo
    summary: str = ""  # Document.summary
    story_arc: str = ""  # Document.story_arc (amendment §4.3 #4)
    provenance_refs: list[ProvenanceRef] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    points: list[Point] = Field(default_factory=list)
    events: list[CommitEvent] = Field(default_factory=list)  # #1013: Event nodes
    operators: list[Operator] = Field(default_factory=list)
    telemetry: Telemetry

    @field_validator("captured_at")
    @classmethod
    def _iso8601(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("captured_at must be an ISO 8601 timestamp")
        return v


# ── Layer-1 validation (deterministic) ────────────────────────────────────

# Required fields (plan §4.5) — missing any of these is the 400 class;
# every other Layer-1 violation is 422 (per-type caps resolution, #952).
REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version", "session_id", "client_commit_id", "captured_at",
    "extractor", "points", "telemetry",
)

VALID_COMMIT_STATUSES: tuple[str, ...] = ("fully_written", "held", "partial")


@dataclass
class Layer1Result:
    """Outcome of the Layer-1 gate.

    errors maps field paths → human-readable reasons (the 422
    ``{detail: {field: [reasons], code?}}`` shape); code is
    ``commit_id_mismatch`` / ``calibration_mismatch`` / None.

    warnings (#405) — additive domain-rule violations (intra-payload,
    warn-first): the commit STILL WRITES; warnings ride the 200 response
    (``warnings[]``). Each warning: {rule, kind, ref, message, fix, severity}
    where severity is the resolved chain enforcement (warn/retry/block).
    Block-severity warnings (Phase B, wired-but-inactive in prod — no
    production chain is 'block') reject the commit in the endpoint.
    """

    ok: bool
    errors: dict[str, list[str]] = field(default_factory=dict)
    code: str | None = None
    warnings: list[dict] = field(default_factory=list)


class Layer1Error(ValueError):
    """Raised by helpers that need a Layer-1 result carried on the exception
    (the endpoint catches it → 422)."""

    def __init__(self, result: Layer1Result):
        super().__init__(str(result.errors))
        self.result = result


def missing_required_fields(raw: dict) -> list[str]:
    """400-class check — required payload fields present (plan §4.5)."""
    return [f for f in REQUIRED_FIELDS if raw.get(f) in (None, "")]


def shape_errors(exc: ValidationError) -> dict[str, list[str]]:
    """Pydantic shape violations → {field-path: [reasons]} (422 class).

    List indices render as ``points[0].content`` (matching the semantic
    layer's key convention).
    """
    errors: dict[str, list[str]] = {}
    for e in exc.errors():
        out = ""
        for part in e["loc"]:
            if isinstance(part, int):
                out += f"[{part}]"
            elif out:
                out += f".{part}"
            else:
                out = str(part)
        errors.setdefault(out, []).append(e["msg"])
    return errors


# ── Atomicity shape (deterministic — no coordination cues, ≤1 commissive) ─

_COORDINATION_CUE = re.compile(r"\b(and|but|or)\b", re.IGNORECASE)
_NUMBERED_LIST_ITEM = re.compile(r"\b\d{1,2}[.)]\s*[A-Za-z]")
_COMMISSIVE_PREDICATES = re.compile(
    r"\b(will|shall|commit(?:s|ted|ting)?|agree(?:s|d)?|promise(?:s|d)?|"
    r"pledge(?:s|d)?)\b",
    re.IGNORECASE,
)


def atomicity_violations(content: str, point_kind: str) -> list[str]:
    """Deterministic atomicity-shape check (plan §4.5 / E9 semantics).

    A point must be a single assertion: coordination cues (and/but/or) or a
    serial-list enumeration (≥2 commas, or ≥2 numbered items) indicate a
    compound assertion; a decision-class point commits to at most ONE thing
    (≤1 commissive predicate — DECISION_POINT_KINDS per pack_registry).
    Heuristic by design — the deterministic mirror of the enforcer's E9.

    T5 carve-out (#1272): ``pointKind == "statement"`` (the extraction-only
    kind) skips the coordination-cue and comma checks — LLM-synthesized
    argument sentences naturally carry commas/connectives and the R2
    atomicity contract's purpose is decision-class atomicity (the commissive
    check, which already never fires for statement). The statement carve-out
    is documented, not silent: the construct path emits statement points
    from the summary's argument wiring.
    """
    violations: list[str] = []
    if point_kind == "statement":
        # T5 carve-out (#1272): argument sentences are naturally
        # compound-looking (commas/connectives) and R2's atomicity purpose
        # is decision-class atomicity — the commissive check never fires for
        # statement (∉ DECISION_POINT_KINDS). The carve-out is documented,
        # not silent: construct-path statement points are LLM-synthesized
        # argument wiring from the summary.
        return violations
    if _COORDINATION_CUE.search(content):
        violations.append("coordination cue (and/but/or) — compound, "
                          "non-atomic assertion")
    commas = content.count(",")
    if commas >= 2:
        violations.append(
            f"serial-list enumeration ({commas} comma-separated items) — "
            "compound assertion")
    numbered = _NUMBERED_LIST_ITEM.findall(content)
    if len(set(numbered)) >= 2:
        violations.append("numbered-list enumeration — compound assertion")
    if point_kind in DECISION_POINT_KINDS:
        commits = _COMMISSIVE_PREDICATES.findall(content)
        if len(commits) > 1:
            violations.append(
                f"{len(commits)} commissive predicates on a decision-class "
                "point — compound commitment (max 1)")
    return violations


def _point_key(p: Any) -> str:
    return _f(p, "id")


def _f(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field off a Pydantic model or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def validate_layer1(
    payload: CommitPayload, vocab: Vocab | None = None
) -> Layer1Result:
    """Deterministic Layer-1 semantic validation over a parsed payload.

    Checks (plan §4.5 / §6.1 Layer-1 block): per-type payload caps; closed
    vocab for pointKind + sourceKind (runtime-compiled); referential
    integrity (operator src/dst ∈ emitted point ids; about_entities ⊆
    entities; source_ref ∈ {session source} ∪ emitted sources[]); MITIGATES
    shape (target ∈ emitted IMPL operator keys ∧ strength ∈ [0.10, 0.50]);
    NAND direction REQUIRED; atomicity shape; client_commit_id recomputed and
    matched (→ code commit_id_mismatch). Content/quote caps + scalar enums
    are enforced by the Pydantic models (shape_errors) before this runs.
    """
    vocab = vocab or get_vocab()
    errors: dict[str, list[str]] = {}

    def add(field_key: str, reason: str) -> None:
        errors.setdefault(field_key, []).append(reason)

    # Per-type payload caps — Layer-1 → 422 (issue #952 cap resolution;
    # independent of the budget ceiling which counts NET-NEW → 402).
    if len(payload.points) > MAX_PAYLOAD_POINTS:
        add("points", f"payload point count {len(payload.points)} exceeds "
            f"MAX_PAYLOAD_POINTS ({MAX_PAYLOAD_POINTS})")
    if len(payload.entities) > MAX_ENTITIES:
        add("entities", f"entity count {len(payload.entities)} exceeds "
            f"MAX_ENTITIES ({MAX_ENTITIES})")
    if len(payload.operators) > MAX_OPERATORS:
        add("operators", f"operator count {len(payload.operators)} exceeds "
            f"MAX_OPERATORS ({MAX_OPERATORS})")

    # Atomicity shape (per point).
    for i, pt in enumerate(payload.points):
        for violation in atomicity_violations(pt.content, pt.pointKind):
            add(f"points[{i}].atomicity", violation)

    emitted_point_ids = {p.id for p in payload.points}
    emitted_event_ids = {e.id for e in payload.events}  # A1b (#1272): events are valid operator endpoints
    entity_names = {e.name for e in payload.entities}
    # The session Source identity = provenance path basename (privacy: paths
    # are basename-only; the server derives the Session Source url from it).
    session_source_ids = {Path(r.path).name for r in payload.provenance_refs}
    source_urls = {s.url for s in payload.sources}
    emitted_operator_keys = {
        (o.src, o.dst, o.op_type) for o in payload.operators
    }

    # Events (#1013): eventKind in the closed event vocab; about_entities
    # referential; source_ref required.
    EVENT_KINDS = {"decision", "occurrence", "deployment", "review",
                   "extraction", "meeting", "experiment", "friction", "turn",
                   "sessionCaptured", "AgentSession", "documentCreated",
                   "roleCreated", "pointAdded", "humanApproval"}
    for i, ev in enumerate(payload.events):
        if ev.eventKind not in EVENT_KINDS:
            add(f"events[{i}].eventKind",
                f"eventKind {ev.eventKind!r} not in the ontology event vocabulary")
        for name in ev.about_entities:
            if name not in entity_names:
                add(f"events[{i}].about_entities",
                    f"entity {name!r} not in the emitted entities[]")
        if not ev.source_ref:
            add(f"events[{i}].source_ref", "source_ref is REQUIRED (R4)")

    # Closed vocab — pointKind AND sourceKind (ontology §5, amendment #5/#6).
    for i, pt in enumerate(payload.points):
        if pt.pointKind not in vocab.point_kinds:
            add(f"points[{i}].pointKind",
                f"pointKind {pt.pointKind!r} not in the compiled closed vocab "
                "(refresh the value brief — calibration_version)")
    for i, s in enumerate(payload.sources):
        if s.sourceKind not in vocab.source_kinds:
            add(f"sources[{i}].sourceKind",
                f"sourceKind {s.sourceKind!r} not in the ontology §5 "
                "source-type vocabulary")

    # Referential integrity.
    for i, pt in enumerate(payload.points):
        unknown = sorted({n for n in pt.about_entities if n not in entity_names})
        if unknown:
            add(f"points[{i}].about_entities",
                f"about_entities reference unknown entities: {unknown}")
        if pt.source_ref not in session_source_ids and \
                pt.source_ref not in source_urls:
            add(f"points[{i}].source_ref",
                f"source_ref {pt.source_ref!r} does not resolve to the "
                "session source or an emitted source[] entry")

    for i, op in enumerate(payload.operators):
        if op.src not in emitted_point_ids and op.src not in emitted_event_ids:
            add(f"operators[{i}].src",
                f"src {op.src!r} is not an emitted point or event id")
        if op.dst not in emitted_point_ids and op.dst not in emitted_event_ids:
            add(f"operators[{i}].dst",
                f"dst {op.dst!r} is not an emitted point or event id")
        if op.op_type == "NAND" and not op.direction:
            add(f"operators[{i}].direction",
                "direction is REQUIRED on NAND (extractor default "
                "unidirectional; mutual restatement explicit)")
        if op.op_type == "MITIGATES":
            if op.target is None:
                add(f"operators[{i}].target",
                    "target is REQUIRED on MITIGATES (the edge-identity "
                    "triple {src, dst, op_type: IMPL})")
            elif (op.target.src, op.target.dst, op.target.op_type) \
                    not in emitted_operator_keys:
                tkey = (op.target.src, op.target.dst, op.target.op_type)
                add(f"operators[{i}].target",
                    f"target {tkey} is not an emitted operator of this "
                    "commit (v1: MITIGATES targets must be same-commit)")
            if op.strength is None:
                add(f"operators[{i}].strength",
                    "strength is REQUIRED on MITIGATES ([0.10, 0.50] — "
                    "extractor bias)")

    # client_commit_id recomputed and matched — the id is NOT opaque (§6.1).
    expected = compute_client_commit_id(
        payload.session_id, payload.points, payload.entities,
        payload.operators, payload.summary, payload.story_arc,
        payload.events,   # #1013: events are part of the canonical
    )
    if payload.client_commit_id != expected:
        add("client_commit_id",
            f"recomputed canonical hash {expected} does not match the "
            "submitted client_commit_id — recompute over the canonical "
            "payload (confidence/c_cal/status/reason/timestamps excluded)")

    if not errors:
        return Layer1Result(ok=True)
    # 422 code semantics (DE2E-7): a recompute mismatch is always
    # commit_id_mismatch; vocab violations are a stale-brief signature →
    # calibration_mismatch (client refreshes its value brief).
    code: str | None = None
    if "client_commit_id" in errors:
        code = "commit_id_mismatch"
    elif any(k.endswith(".pointKind") or k.endswith(".sourceKind")
             for k in errors):
        code = "calibration_mismatch"
    return Layer1Result(ok=False, errors=errors, code=code)


def validate_payload_dict(
    raw: dict, vocab: Vocab | None = None, *, domain: str | None = None
) -> tuple[Layer1Result, CommitPayload | None]:
    """Full Layer-1 gate over a raw JSON body.

    400 class → code ``missing_required_fields`` (endpoint maps to 400 —
    reserved for missing required payload fields). Every other violation is
    422 class with field reasons (the endpoint maps to 422).

    ``domain`` (optional, #405): the orchestrator-known domain for the
    payload-local integrity rules; when None the fail-safe kind inference
    runs (no-match/multi-match → rules skipped).
    """
    if not isinstance(raw, dict):
        return Layer1Result(
            ok=False,
            errors={"payload": ["request body must be a JSON object"]},
        ), None
    missing = missing_required_fields(raw)
    if missing:
        return Layer1Result(
            ok=False,
            errors={"required": [
                f"missing required field {f!r}" for f in missing
            ]},
            code="missing_required_fields",
        ), None
    try:
        payload = CommitPayload.model_validate(raw)
    except ValidationError as exc:
        return Layer1Result(ok=False, errors=shape_errors(exc)), None
    result = validate_layer1(payload, vocab=vocab)
    if result.ok:
        # #405: additive, warn-first domain rules (intra-payload only — no
        # graph I/O in the commit path by construction). A valid payload
        # carries warnings[] on the 200; violations never fail the write.
        result.warnings = validate_domain_rules(payload, domain=domain)
    return result, payload


def validate_domain_rules(
    payload: CommitPayload, domain: str | None = None
) -> list[dict]:
    """Run the domain's ``payload_local`` validators (issue #405, Phase A).

    Additive + warn-first: the commit STILL WRITES; results ride the 200
    response as ``warnings[]``. A validator exception is logged and skipped —
    a broken rule must never fail a write (guardrails).

    ``domain`` is passed by the orchestration layer when known; the
    fail-safe fallback infers it from the payload's pointKinds (pack overlap)
    and SKIPS (with a log) when no-match or multi-match — never a wrong
    attribution. Each warning is stamped with ``severity`` — the resolved
    chain enforcement for the rule's chain (warn/retry/block; ``block`` is
    Phase B, wired-but-inactive in prod).
    """
    if domain is None:
        domain = _infer_payload_domain(payload)
    if domain is None:
        return []
    from .domain_loader import SURFACE_PAYLOAD_LOCAL, domain_validators
    from .domain_validators import resolve_rule_severity

    warnings: list[dict] = []
    for spec in domain_validators(domain, surface=SURFACE_PAYLOAD_LOCAL):
        try:
            found = spec["fn"](payload)
        except Exception:
            _logger.exception(
                "payload-local domain validator failed (domain=%s chain=%s) "
                "— skipped", domain, spec.get("chain_id"))
            continue
        for w in found:
            w = dict(w)
            w["severity"] = resolve_rule_severity(
                domain, spec.get("chain_id"), w.get("rule"))
            warnings.append(w)
    return warnings


def domain_block_warnings(warnings: list[dict]) -> list[dict]:
    """Phase B selector — warnings whose resolved severity is 'block' reject
    the commit (4xx). Wired-but-inactive in production: no production chain
    is 'block' today; only synthetic/test rules exercise this path."""
    return [w for w in warnings if w.get("severity") == "block"]


def _infer_payload_domain(payload: CommitPayload) -> str | None:
    """Fail-safe kind-based domain inference for the commit path (#405).

    Scores each loaded pack by how many of the payload's pointKinds (bare
    names) appear in the pack's OWN point kinds. Exactly one unique top
    scorer with score ≥ 1 → that domain. No-match or multi-match (tie) →
    None (skip + log) — never a wrong attribution.
    """
    from .domain_loader import known_domains, pack_kind_overlap

    kinds = [p.pointKind for p in payload.points]
    if not kinds:
        return None
    scored = [(d, pack_kind_overlap(d, "pointKind", kinds))
              for d in known_domains()]
    scored.sort(key=lambda kv: -kv[1])
    if not scored or scored[0][1] == 0:
        return None
    if len(scored) > 1 and scored[1][1] == scored[0][1]:
        _logger.warning(
            "commit domain inference multi-match (kinds=%s) — skipping "
            "payload-local domain rules (#405)", kinds)
        return None
    return scored[0][0]


# ── Canonicalization (deterministic across clients) ───────────────────────

def _round3(value: Any) -> Any:
    """Floats rounded to 3 decimals, recursively (canonical determinism)."""
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {k: _round3(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round3(v) for v in value]
    return value


def _op_canonical(o: Any) -> dict:
    """Operator canonical dict — target normalized to a plain dict."""
    target = _f(o, "target")
    if target is not None and not isinstance(target, dict):
        target = {
            "src": target.src,
            "dst": target.dst,
            "op_type": target.op_type,
        }
    out = {
        "src": _f(o, "src"),
        "dst": _f(o, "dst"),
        "op_type": _f(o, "op_type"),
    }
    direction = _f(o, "direction")
    if direction is not None:
        out["direction"] = direction
    if target is not None:
        out["target"] = target
    strength = _f(o, "strength")
    if strength is not None:
        out["strength"] = strength
    return out


def canonical_payload(
    session_id: str,
    points: Iterable[Any],
    entities: Iterable[Any],
    operators: Iterable[Any],
    summary: str,
    story_arc: str,
    events: Iterable[Any] = (),
) -> str:
    """Deterministic canonical JSON over the §6.1 commitment fields.

    Rules (plan §6.1 canonicalization block): sorted keys at every level
    (json sort_keys), arrays sorted by point/entity/operator id, floats
    rounded to 3 decimals, and confidence/c_cal/status/reason/timestamps
    EXCLUDED (LLM artifacts). client_commit_id = content_hash of this string.
    Accepts CommitPayload models or plain dicts (the local extractor mirrors
    with dicts; the endpoint validates with models).
    """
    canonical = {
        "session_id": session_id,
        "summary": summary or "",
        "story_arc": story_arc or "",
        "points": [
            {
                "id": _f(p, "id"),
                "content": _f(p, "content"),
                "pointKind": _f(p, "pointKind"),
                "about_entities": sorted(_f(p, "about_entities", []) or []),
                "source_ref": _f(p, "source_ref"),
                "quote": _f(p, "quote", "") or "",
            }
            for p in sorted(points, key=_point_key)
        ],
        "entities": [
            {
                "name": _f(e, "name"),
                "kind": _f(e, "kind"),
                "passes_frequency_gate": bool(
                    _f(e, "passes_frequency_gate", True)),
            }
            for e in sorted(entities, key=lambda e: _f(e, "name"))
        ],
        "operators": [
            _op_canonical(o)
            for o in sorted(
                operators,
                key=lambda o: (_f(o, "src"), _f(o, "dst"), _f(o, "op_type")),
            )
        ],
        "events": [
            {
                "id": _f(e, "id"),
                "eventKind": _f(e, "eventKind"),
                "content": _f(e, "content"),
                "about_entities": sorted(_f(e, "about_entities") or []),
            }
            for e in sorted(events, key=lambda x: _f(x, "id"))
        ],
    }
    return json.dumps(_round3(canonical), sort_keys=True, separators=(",", ":"))


def compute_client_commit_id(
    session_id: str,
    points: Iterable[Any],
    entities: Iterable[Any],
    operators: Iterable[Any],
    summary: str,
    story_arc: str,
    events: Iterable[Any] = (),
) -> str:
    """SHA-256 over the canonical payload (ids.content_hash — the existing
    idempotency-key primitive, plan §6.1). Events are part of the canonical
    (issue #1013): changing an event changes the commit id."""
    return content_hash(
        canonical_payload(session_id, points, entities, operators,
                          summary, story_arc, events)
    )


def point_content_id(content: str) -> str:
    """Content-addressed point id — pt_<sha> (amendment §4.3 #10).

    Used for supersede candidates: a re-capture with changed content gets a
    NEW content-addressed id and supersedes the old point (CORRECTS +
    outdated — the write is #953's job; this slice only computes the
    candidate id, plan §3.3).
    """
    return f"pt_{content_hash(content)}"


# ── L2 reconciliation (IN MEMORY — no writes) ─────────────────────────────

@dataclass
class GraphState:
    """Same-session graph state the endpoint loads for L2 (the read surface
    the write path in #953 will use; pure dataclass here so reconciliation
    stays deterministic and unit-testable)."""

    points: dict[str, str] = field(default_factory=dict)  # pt id → content
    entities: set[tuple[str, str]] = field(default_factory=set)  # (name, kind)
    operators: set[tuple[str, str, str]] = field(default_factory=set)
    # (src, dst, op_type) — the operator MERGE key (PL1)
    is_episodic: bool = False  # quota discriminator (amendment §4.3 #13)
    value_nodes_created: int = 0  # prior cumulative budget numerator
    value_nodes_held: int = 0  # held count lives on the Session (telemetry)

    @classmethod
    def empty(cls) -> "GraphState":
        return cls()


@dataclass
class PointReconcile:
    point: Point
    action: str  # "merge" | "supersede" | "new"
    existing_id: str | None = None
    supersede_id: str | None = None  # recomputed pt_<sha> for supersede


@dataclass
class EntityReconcile:
    entity: Entity
    action: str  # "merge" | "new"


@dataclass
class OperatorReconcile:
    operator: Operator
    action: str  # "merge" | "new"


@dataclass
class ReconcileResult:
    """L2 reconciliation — computed IN MEMORY, nothing written (W-3 [3])."""

    points: list[PointReconcile] = field(default_factory=list)
    entities: list[EntityReconcile] = field(default_factory=list)
    operators: list[OperatorReconcile] = field(default_factory=list)

    @property
    def net_new(self) -> int:
        """Net-new non-episodic delta = the budget numerator.

        MERGE/dedup burn zero; supersede-only deltas do NOT increment
        net-new (R-14 exemption, DE2E-7 bump-then-re-capture fixture).
        """
        return (
            sum(1 for p in self.points if p.action == "new")
            + sum(1 for e in self.entities if e.action == "new")
            + sum(1 for o in self.operators if o.action == "new")
        )


def reconcile_payload(
    payload: CommitPayload, state: GraphState
) -> ReconcileResult:
    """L2 MERGE reconciliation (W-3 [3]) — purely in memory.

    - points by pt_<sha>: same id + same content → MERGE bump (zero budget);
      same id + CHANGED content → supersede CANDIDATE (new content-addressed
      id + supersede_point later — the WRITE is #953's, PL2); unknown id →
      new.
    - entities by (name, kind); operators by (src, dst, op_type).
    """
    result = ReconcileResult()
    for pt in payload.points:
        existing = state.points.get(pt.id)
        if existing is None:
            result.points.append(PointReconcile(pt, "new"))
        elif existing == pt.content:
            result.points.append(
                PointReconcile(pt, "merge", existing_id=pt.id))
        else:
            result.points.append(PointReconcile(
                pt, "supersede", existing_id=pt.id,
                supersede_id=point_content_id(pt.content)))
    for e in payload.entities:
        key = (e.name, e.kind)
        result.entities.append(
            EntityReconcile(e, "merge" if key in state.entities else "new"))
    for op in payload.operators:
        key = (op.src, op.dst, op.op_type)
        result.operators.append(
            OperatorReconcile(op,
                              "merge" if key in state.operators else "new"))
    return result


# ── Budget accounting (THE AUTHORITATIVE §6.1 block) ──────────────────────

@dataclass
class BudgetDecision:
    """Budget adjudication for one commit attempt.

    outcome: "ok" (write; warn flag may be set) | "held" (PL3 — nothing
    written, held[] returned) | "fail" (402 — nothing written).
    """

    outcome: str
    cumulative_after: int
    warn: bool = False
    held_point_ids: list[str] = field(default_factory=list)
    reason: str | None = None


def adjudicate_budget(
    *,
    prior_created: int,
    delta: int,
    first_adjudication: bool,
    point_ids: Iterable[str] = (),
) -> BudgetDecision:
    """Per-session cumulative budget check on the RECONCILED net-new delta.

    Authoritative semantics (plan §6.1 BUDGET block + W-4 + PL3):
      - soft 15 → WARN (items still written);
      - >25 → held[] — the hard band applies at FIRST adjudication of a
        client_commit_id ONLY (a commit consuming zero budget — supersede-
        only / merge-only, R-14 — is never held); re-submissions of a
        seen-but-not-fully-written commit (status held|partial) are checked
        against the 50 CEILING only (PL3 promotion semantics — a hold is a
        deferral, not a re-adjudication);
      - >50 → 402 fail-closed, nothing written; a re-submission that would
        push cumulative past 50 ALSO 402s (DE2E-7 Session C — held items
        remain held client-side, never dropped);
      - episodic sessions are exempt (the quota discriminator).
    Order matters (W-3 [4]): the ceiling check runs post-reconciliation,
    pre-write — it counts net-new, which only the reconciliation knows.
    """
    cumulative = prior_created + delta
    if cumulative > BUDGET_CEILING:
        return BudgetDecision(
            "fail", cumulative,
            reason=f"budget ceiling exceeded: {prior_created} + {delta} = "
                   f"{cumulative} > {BUDGET_CEILING} — nothing written (402); "
                   "held items remain held client-side (PL3)",
        )
    # The hard band holds only commits that CONSUME budget (delta > 0): a
    # supersede-only or merge-only commit (net-new 0, R-14) is always
    # written — holding it would trap re-captures after a calibration bump
    # (DE2E-7 R-14 fixture asserts the superseded points are written).
    if delta > 0 and cumulative > BUDGET_HARD and first_adjudication:
        return BudgetDecision(
            "held", cumulative, warn=True,
            held_point_ids=list(point_ids),
            reason=f"hard band exceeded: cumulative {cumulative} > "
                   f"{BUDGET_HARD} — items held, NOT written (PL3); "
                   "re-submission checks the 50-ceiling only",
        )
    warn = delta > 0 and cumulative > BUDGET_SOFT
    return BudgetDecision(
        "ok", cumulative, warn=warn,
        reason=("soft band crossed — WARN telemetry" if warn else None),
    )


# ── :CommitRecord state + L1 replay semantics ─────────────────────────────

@dataclass(frozen=True)
class CommitRecordState:
    """In-memory mirror of the :CommitRecord graph node (§4.1).

    client_commit_id is the UNIQUE MERGE key — one node per commit attempt;
    the MERGE is also the atomic concurrency serialization point (W-3 [2],
    §5.4). status: fully_written | held | partial. Division of labor: the
    record carries per-commit adjudication state + billing; the Session
    counters carry the budget numerator (value_nodes_held lives on the
    Session, NOT here — §4.1).
    """

    client_commit_id: str
    session_id: str
    status: str  # fully_written | held | partial (VALID_COMMIT_STATUSES)
    write_ops_billed: int = 0
    written_at: str | None = None


def is_l1_replay(record: CommitRecordState | None) -> bool:
    """L1 replay: record exists AND status == fully_written → duplicate
    (200 {duplicate: true}, zero writes, zero write-ops billed, PL4). A
    record with status held|partial is NOT a replay (PL3 — re-adjudicated
    against the 50-ceiling only)."""
    return record is not None and record.status == "fully_written"


def is_first_adjudication(record: CommitRecordState | None) -> bool:
    """First adjudication of a client_commit_id — the hard-25 band applies
    here only (PL3). A seen-but-not-fully-written record (held|partial) is
    a deferral, not a re-adjudication."""
    return record is None


@dataclass
class CommitPlan:
    """The full adjudication a commit attempt needs — L1 replay check, L2
    reconciliation and the budget decision, all computed before any write
    (the endpoint #953 executes the plan: writes + counters + metering)."""

    payload: CommitPayload
    duplicate: bool  # L1 replay → zero writes, zero write-ops (PL4)
    first_adjudication: bool
    reconcile: ReconcileResult | None
    budget: BudgetDecision


def plan_commit(
    payload: CommitPayload,
    state: GraphState,
    record: CommitRecordState | None,
) -> CommitPlan:
    """Adjudicate a commit attempt deterministically (W-3 [2]-[4] in memory).

    L1 replay (record fully_written) short-circuits: duplicate=True, no
    reconciliation, budget decision informational. Otherwise: L2 in-memory
    reconciliation → net-new delta → budget check (ceiling pre-write).
    """
    if is_l1_replay(record):
        return CommitPlan(
            payload=payload,
            duplicate=True,
            first_adjudication=False,
            reconcile=None,
            budget=BudgetDecision(
                "ok", state.value_nodes_created,
                reason="L1 replay — duplicate, zero writes, zero write-ops "
                       "billed (PL4)",
            ),
        )
    reconcile = reconcile_payload(payload, state)
    # The budget numerator is net-new NON-EPISODIC nodes (plan §6.1). The
    # Session's own is_episodic flag is the QUOTA discriminator, NOT a budget
    # exemption — value points from the commit endpoint are non-episodic and
    # always count (review fix, PR #953: the previous exemption let a held
    # re-submission bypass the ceiling — Session C).
    delta = reconcile.net_new
    budget = adjudicate_budget(
        prior_created=state.value_nodes_created,
        delta=delta,
        first_adjudication=is_first_adjudication(record),
        point_ids=[
            pr.point.id for pr in reconcile.points
            if pr.action in ("new", "supersede")
        ],
    )
    return CommitPlan(
        payload=payload,
        duplicate=False,
        first_adjudication=record is None,
        reconcile=reconcile,
        budget=budget,
    )
