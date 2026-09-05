"""Schema v1.1 typed event log — emitter registry + validation (issue #2284).

Every probe-consumed semantic field declares ONE emitter kind. An
emitter-less consumed field forces report_status=incomplete — the log is
eval-metadata only; product graph ops are GraphEvent references (Amend 1).
"""
from __future__ import annotations

from typing import Any, Literal

EmitterKind = Literal["tool_event", "state_event", "envelope",
                      "judge_annotation", "gold_store", "derived"]
_EMITTER_KINDS: frozenset[str] = frozenset(
    {"tool_event", "state_event", "envelope", "judge_annotation",
     "gold_store", "derived"})

#: field -> emitter. ADDITIVE-ONLY. FULL union of every probe-consumed
#: semantic field (derived by unioning each probe's CONSUMED_FIELDS — see
#: Task 1 Step 1.5). An emitter-less consumed field => report_status=incomplete.
FIELD_EMITTERS: dict[str, str] = {
    # agent actions ON THE PRODUCT STORE (tool_event entries carry an
    # event-store REFERENCE {event_ref: "ns:seq"} per Amend 1 — the product
    # writes the GraphEvent; the log never re-records product op payloads)
    "contradiction_surfaced": "tool_event",
    "explicit_resolution": "tool_event",
    # product/state read-outs (EP snapshots, harness counters)
    "ep_outcome": "state_event",
    "ep_contested": "state_event",
    "decide_cycles": "state_event",          # harness-side counter, reported-not-scored
    "injection_turn": "state_event",         # scenario-authored ¬A turn marker
    "surfaced_within_turn": "state_event",
    # gated-judge rubric leg (arm-neutral R2 coverage — sibling B rubric)
    "coverage_subscore": "judge_annotation",
    # structured envelope scalars (only scalar channel — no prose mining)
    "stated_confidence": "envelope",
    "stated_undecided": "envelope",
    "stated_defeat_conditions": "envelope",
    # scorer-derived from log
    "flip_flopped": "derived",
    "false_positive": "derived",            # control verdict (bct vs ct)
    "outcome_correct": "derived",
    "update_correct_direction": "derived",
    "over_reacted": "derived",
    # gold-side truth (reader-safe gold store; never rendered agent-side)
    "real_defeat_conditions": "gold_store",
    "outcomes": "gold_store",
    "confidences": "gold_store",
}
_EVENT_KEYS = ("type", "event", "at", "payload")
_SUBTYPE_OK = {
    "tool_event": {"file_nand", "register_conflict", "mitigate", "supersede",
                   "create_point", "create_operator"},
    "state_event": {"ep_snapshot", "decide_cycle_inc", "session_open",
                    "session_close", "retrieve", "injection_seen"},
    "envelope": {"declared"},
    "judge_annotation": {"rubric_item", "correctness"},
    "gold_store": {"expected"},
    "derived": {"flip_flop", "contested_after_surfacing", "ep_delta",
                "correctness_delta", "direction_ok", "control_verdict"},
}

def validate_event_entry(entry: dict[str, Any]) -> None:
    for k in _EVENT_KEYS:
        if k not in entry:
            raise ValueError(f"event entry missing {k!r}: {entry}")
    if entry["type"] not in _EMITTER_KINDS:
        raise ValueError(f"unknown emitter kind {entry['type']!r}")
    if entry["event"] not in _SUBTYPE_OK[entry["type"]]:
        raise ValueError(f"unknown {entry['type']} subtype {entry['event']!r}")
    if entry["type"] in ("tool_event",) and "event_ref" not in entry.get("payload", {}):
        # Amend-1 seam: tool events REFERENCE product event-store rows; they
        # never re-record product op payloads.
        raise ValueError(f"tool_event must carry event_ref: {entry}")
    field = entry.get("field")
    if field is not None and FIELD_EMITTERS.get(field) != entry["type"]:
        raise ValueError(
            f"field {field!r} must be emitted as {FIELD_EMITTERS.get(field)!r}, "
            f"got {entry['type']!r}")

#: mandatory on every real episode (envelope/derived/state-terminal); all
#: other registered fields are CONDITIONAL (absence = legitimate non-
#: occurrence, measured, never a gap — see P0 cycle-3).
#: mandatory on EVERY real episode (truly universal): absence ALWAYS gaps.
MANDATORY: frozenset[str] = frozenset({
    "stated_confidence", "stated_undecided", "stated_defeat_conditions",
    "ep_outcome", "decide_cycles",
})

#: CONDITIONAL = legitimate non-occurrence: absence NEVER gaps, even when the
#: field sits in `expected` (measured 0.00 — the a0 comparator + bct FP pool
#: depend on this). Behavioral tool actions + contested EP marker only.
CONDITIONAL: frozenset[str] = frozenset({
    "contradiction_surfaced", "explicit_resolution", "surfaced_within_turn",
    "ep_contested",
})

#: SCENARIO/FAMILY-conditional: expected ONLY when scenario/family semantics
#: require (injection_turn when the scenario plants a ¬A k-turn — NEVER for
#: bct twins; outcome_correct/confidences/outcomes for calibration/decision;
#: coverage_subscore for R2 judge legs). Absence gaps when expected. The
#: scorer seam builds the per-episode `expected` set from these rules.
SCENARIO_CONDITIONAL: frozenset[str] = frozenset({
    "injection_turn", "outcome_correct", "confidences", "outcomes",
    "coverage_subscore", "false_positive", "update_correct_direction",
    "over_reacted", "flip_flopped", "real_defeat_conditions",
})

def validate_emitter_coverage(log: list[dict[str, Any]],
                              *,
                              expected: set[str] | None = None) -> set[str]:
    """Uncovered = expected - emitted. expected=None is the SCHEMA-FIXTURE mode (validates the universal MANDATORY
    set); the real-run gate (Task 5) passes the per-episode expected set built
    by the scorer seam (MANDATORY x scenario/family-conditional)."""
    for e in log:
        validate_event_entry(e)
    emitted = {e.get("field") for e in log if e.get("field")}
    want = set(MANDATORY) if expected is None else expected
    return {f for f in want if f not in emitted and f not in CONDITIONAL}

#: per-kind default entry factory (schema tests consume; every registry field
#: appears exactly once => validate_emitter_coverage(log) == set()).
_FIELD_DEFAULTS: dict[str, dict[str, str]] = {
    "contradiction_surfaced": {"type": "tool_event", "event": "file_nand"},
    "explicit_resolution": {"type": "tool_event", "event": "supersede"},
    "ep_outcome": {"type": "state_event", "event": "ep_snapshot"},
    "ep_contested": {"type": "state_event", "event": "ep_snapshot"},
    "decide_cycles": {"type": "state_event", "event": "decide_cycle_inc"},
    "injection_turn": {"type": "state_event", "event": "injection_seen"},
    "surfaced_within_turn": {"type": "state_event", "event": "retrieve"},
    "stated_confidence": {"type": "envelope", "event": "declared"},
    "stated_undecided": {"type": "envelope", "event": "declared"},
    "stated_defeat_conditions": {"type": "envelope", "event": "declared"},
    "flip_flopped": {"type": "derived", "event": "flip_flop"},
    "false_positive": {"type": "derived", "event": "control_verdict"},
    "outcome_correct": {"type": "derived", "event": "correctness_delta"},
    "update_correct_direction": {"type": "derived", "event": "direction_ok"},
    "over_reacted": {"type": "derived", "event": "direction_ok"},
    "coverage_subscore": {"type": "judge_annotation", "event": "rubric_item"},
    "real_defeat_conditions": {"type": "gold_store", "event": "expected"},
    "outcomes": {"type": "gold_store", "event": "expected"},
    "confidences": {"type": "gold_store", "event": "expected"},
}

def build_full_log() -> list[dict[str, Any]]:
    log: list[dict[str, Any]] = []
    for i, (field, spec) in enumerate(sorted(_FIELD_DEFAULTS.items())):
        payload = {"value": True} if spec["event"] == "declared" else {}
        if spec["type"] == "tool_event":
            payload = {"event_ref": f"ns:seq:{i}"}
        if spec["event"] == "ep_snapshot" and field == "ep_contested":
            payload = {"variance": 0.12}
        if field == "decide_cycles":
            payload = {"count": 3}
        if field == "injection_turn":
            payload = {"k": 3}
        entry: dict[str, Any] = {"type": spec["type"], "event": spec["event"],
                                 "at": i, "payload": payload}
        entry["field"] = field
        log.append(entry)
    return log

FIXTURE_FULL_LOG = build_full_log()
