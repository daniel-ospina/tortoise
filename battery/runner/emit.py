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
    if field is not None and FIELD_SUBTYPES.get(field) is not None \
            and entry["event"] != FIELD_SUBTYPES[field]:
        # Round-4 P2: field->KIND was enforced but field->SUBTYPE was not —
        # a state_event "retrieve" tagged ep_outcome (canonical subtype
        # ep_snapshot) or a derived "flip_flop" tagged false_positive
        # (canonical control_verdict) passed the registry check. Every
        # emitter field has exactly one canonical emission subtype (see
        # _FIELD_DEFAULTS); a registry field emitted under another subtype
        # is an integrity violation, never a silent pass.
        raise ValueError(
            f"field {field!r} must be emitted as subtype "
            f"{FIELD_SUBTYPES[field]!r}, got {entry['event']!r}")
    if field is not None:
        # Payload-shape checks for the list-typed envelope fields (round-4
        # P2): a declared scalar under the wrong type is an integrity
        # violation. stated_defeat_conditions carries a LIST, stated_
        # confidence a real number (never a bool), stated_undecided a bool.
        value = (entry.get("payload") or {}).get("value")
        if field == "stated_defeat_conditions":
            if not isinstance(value, list):
                raise ValueError(
                    f"stated_defeat_conditions payload value must be a list, "
                    f"got {value!r}: {entry}")
        elif field == "stated_confidence":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"stated_confidence payload value must be a real number, "
                    f"got {value!r}: {entry}")
        elif field == "stated_undecided" and not isinstance(value, bool):
            raise ValueError(
                f"stated_undecided payload value must be a bool, "
                f"got {value!r}: {entry}")

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
#: (Round-4 P2 DEFERRAL note: the [SECOND-MODEL-GATE] P1 — R1 surfaced_rate
#: derives from CONDITIONAL fields with absence-as-0.0 as the sanctioned
#: a0/bct comparator semantics — stands; the executor-era emission-loss-proof
#: requirement is a Task-9 acceptance item, NOT a phase-1 change, because
#: real mode is unreachable without a real emission seam.)
CONDITIONAL: frozenset[str] = frozenset({
    "contradiction_surfaced", "explicit_resolution", "surfaced_within_turn",
    "ep_contested",
})

#: SCENARIO/FAMILY-conditional: expected ONLY when scenario/family semantics
#: require (injection_turn when the scenario plants a ¬A k-turn — NEVER for
#: bct twins; outcome_correct/confidences/outcomes for calibration/decision;
#: coverage_subscore for R2 judge legs). Absence gaps when expected. The
#: scorer seam builds the per-episode `expected` set from these rules.
#:
#: LIVE (round-4 P2): consumed by battery/runner/probe_scorer.py —
#: ``expected_coverage_for`` unions (family-truth ∩ SCENARIO_CONDITIONAL)
#: so every expected truth term is a member of this set, plus the scenario
#: rule (injection_turn when planted). ``false_positive`` is the FP-control
#: verdict term: expected ONLY for CONTROL-population episodes (benign bct)
#: whose derived control verdict was actually emitted (post-derivation
#: phase-2 gate in probe_scorer) — planted ct episodes never expect a
#: control verdict, and an absent verdict on a control episode keeps the
#: no-verdict sentinel (never a fabricated pass).
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
    # NOTE: keep 1:1 with FIELD_EMITTERS — every registry field carries a
    # canonical default subtype; validate_event_entry enforces it.
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

#: field -> canonical SUBTYPE (derived from the _FIELD_DEFAULTS event
#: values — every emitter field has exactly ONE canonical emission subtype;
#: round-4 P2 field->subtype enforcement, see validate_event_entry).
FIELD_SUBTYPES: dict[str, str] = {
    field: spec["event"] for field, spec in _FIELD_DEFAULTS.items()}

def build_full_log() -> list[dict[str, Any]]:
    log: list[dict[str, Any]] = []
    for i, (field, spec) in enumerate(sorted(_FIELD_DEFAULTS.items())):
        if spec["event"] == "declared":
            # payload-shape-correct fixture scalars (validate_event_entry
            # now checks the envelope shapes below).
            if field == "stated_defeat_conditions":
                payload = {"value": []}
            elif field == "stated_confidence":
                payload = {"value": 0.8}
            elif field == "stated_undecided":
                payload = {"value": False}
            else:
                payload = {"value": True}
        else:
            payload = {}
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
