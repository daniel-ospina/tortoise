# tests/test_battery_schema_v11.py
"""Schema v1.1 event log + emitter registry (issue #2284, Task 1)."""
from __future__ import annotations

import pytest
import yaml

from battery.runner import emit
from battery.runner.emit import validate_emitter_coverage


def _mini_scenarios(tmp_path) -> list:
    """Tiny authored no-gold corpus via the RUN-path loader (mirrors
    tests/test_battery_run.py::_config_dir — hand-constructing Scenario is
    forbidden: ctor shape is lock-heavy and Task 2 extends it)."""
    from battery.config.corpus import load_corpus
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    corpus = {"scenarios": [{"id": "ct-mini", "tier": "probe",
        "family": "contradiction", "task_type": "contradiction",
        "attack_type": "ct", "split": "train", "k": 3,
        "prompt": {"system": "sys", "turns": [
            {"role": "user", "content": "turn1"}]},
        "gold": {"expected": "yes"}}]}
    (cfg / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    return load_corpus(cfg / "corpus.yaml")

def test_schema_version_is_v11(tmp_path):
    from battery.runner.artifacts import SCHEMA_VERSION, build_run_artifact
    from battery.runner.episode import EpisodeResult
    sc = _mini_scenarios(tmp_path)[0]
    ep = EpisodeResult(scenario_id=sc.id, seed=1, arm="a4")  # zero-turn OK
    assert SCHEMA_VERSION == "1.1"
    art = build_run_artifact(seed=1, arm="a4", scenario=sc, episode=ep,
        metric_values={}, outcomes={}, ep_outcome="CONVERGED",
        excluded={}, setup_info={}, provenance={}, python_hash_seed="0")
    assert "event_log" in art and isinstance(art["event_log"], list)

def _probe_consumed_fields() -> set[str]:
    """Union of CONSUMED_FIELDS declared at the top of each probe module
    (r1..r5) — the registry must cover the REAL consumer surface."""
    from battery.probes import r1_contradiction, r2_coverage, r3_calibration, r4_defeat, r5_update
    out: set[str] = set()
    for mod in (r1_contradiction, r2_coverage, r3_calibration,
                r4_defeat, r5_update):
        out |= set(getattr(mod, "CONSUMED_FIELDS", ()))
    return out

def test_field_emitter_registry_is_complete():
    consumed = _probe_consumed_fields()
    assert consumed, "probes must declare CONSUMED_FIELDS"
    missing = consumed - set(emit.FIELD_EMITTERS)
    assert not missing, f"fields missing emitter: {sorted(missing)}"

def test_event_entry_deep_validation():
    # happy path is a NON-tool event (tool_event now requires event_ref)
    emit.validate_event_entry({"type": "state_event", "event": "ep_snapshot",
        "at": 4, "payload": {}})
    emit.validate_event_entry({"type": "tool_event", "event": "file_nand",
        "at": 2, "payload": {"event_ref": "ns:seq:42"}})
    with pytest.raises(ValueError):
        emit.validate_event_entry({"type": "tool_event"})   # missing keys
    with pytest.raises(ValueError):
        emit.validate_event_entry({"type": "tool_event", "event": "file_nand",
            "at": 2, "payload": {"target_id": "p:1"}})       # no event_ref seam

def test_kind_must_match_registry_field():
    # entry carrying a registry field must declare the registry's KIND
    emit.validate_event_entry({"type": "derived", "event": "flip_flop",
        "at": 5, "field": "flip_flopped", "payload": {"value": True}})
    with pytest.raises(ValueError):
        emit.validate_event_entry({"type": "state_event", "event": "ep_snapshot",
            "at": 5, "field": "flip_flopped", "payload": {}})  # wrong kind
    # kind-conflict log (wrong-kind emission of a registered field) is an
    # integrity violation: coverage validation raises, never silently passes
    log = [*emit.FIXTURE_FULL_LOG,
        {"type": "derived", "event": "flip_flop", "at": 9,
         "field": "stated_confidence", "payload": {"value": 0.2}}]
    with pytest.raises(ValueError):
        emit.validate_emitter_coverage(log, expected=set(emit.MANDATORY))

def test_field_subtype_must_match_canonical_registry():
    # Round-4 P2: field->KIND was enforced but field->SUBTYPE was not — a
    # state_event "retrieve" tagged ep_outcome (canonical subtype
    # ep_snapshot) or a derived "flip_flop" tagged false_positive (canonical
    # control_verdict) passed the registry check. Canonical subtypes now
    # come from the _FIELD_DEFAULTS event values (FIELD_SUBTYPES).
    emit.validate_event_entry({"type": "state_event", "event": "ep_snapshot",
        "at": 4, "payload": {}, "field": "ep_outcome"})
    emit.validate_event_entry({"type": "derived", "event": "control_verdict",
        "at": 5, "payload": {"value": False}, "field": "false_positive"})
    with pytest.raises(ValueError, match="subtype"):
        emit.validate_event_entry({"type": "state_event", "event": "retrieve",
            "at": 4, "payload": {}, "field": "ep_outcome"})
    with pytest.raises(ValueError, match="subtype"):
        emit.validate_event_entry({"type": "derived", "event": "flip_flop",
            "at": 5, "payload": {"value": True}, "field": "false_positive"})

def test_envelope_payload_shapes_validated():
    # Round-4 P2: list-typed envelope fields carry payload-shape checks —
    # stated_defeat_conditions must be a LIST, stated_confidence a REAL
    # number (never a bool), stated_undecided a BOOL.
    emit.validate_event_entry({"type": "envelope", "event": "declared",
        "at": 1, "field": "stated_defeat_conditions",
        "payload": {"value": ["cond A", "cond B"]}})
    emit.validate_event_entry({"type": "envelope", "event": "declared",
        "at": 1, "field": "stated_confidence",
        "payload": {"value": 0.7}})
    emit.validate_event_entry({"type": "envelope", "event": "declared",
        "at": 1, "field": "stated_undecided",
        "payload": {"value": False}})
    # scalar defeat conditions (a str / a number where a list is declared)
    for bad in ("not-a-list", 5, True):
        with pytest.raises(ValueError, match="must be a list"):
            emit.validate_event_entry({"type": "envelope",
                "event": "declared", "at": 1,
                "field": "stated_defeat_conditions",
                "payload": {"value": bad}})
    with pytest.raises(ValueError, match="real number"):
        emit.validate_event_entry({"type": "envelope", "event": "declared",
            "at": 1, "field": "stated_confidence",
            "payload": {"value": "high"}})
    with pytest.raises(ValueError, match="real number"):
        emit.validate_event_entry({"type": "envelope", "event": "declared",
            "at": 1, "field": "stated_confidence",
            "payload": {"value": True}})  # bool is not a confidence scalar
    with pytest.raises(ValueError, match="must be a bool"):
        emit.validate_event_entry({"type": "envelope", "event": "declared",
            "at": 1, "field": "stated_undecided",
            "payload": {"value": "yes"}})

def test_mandatory_coverage_complete():
    # expectation-scoped: a real episode log emitting all MANDATORY fields +
    # its family/arm-required conditionals is covered
    log = emit.FIXTURE_FULL_LOG
    assert validate_emitter_coverage(log,
        expected=set(emit.MANDATORY)) == set()

def test_mandatory_missing_fails_closed():
    log = [e for e in emit.FIXTURE_FULL_LOG
           if e.get("field") != "stated_confidence"]
    uncovered = validate_emitter_coverage(log, expected=set(emit.MANDATORY))
    assert "stated_confidence" in uncovered  # mandatory missing => incomplete

def test_conditional_absence_is_not_a_gap():
    # a no-store arm episode: a CONDITIONAL field that IS in the expected set
    # may be absent -> measured 0.0, NOT a gap (non-vacuous: the conditional
    # is present in `expected` so the check actually exercises the rule)
    log = [e for e in emit.FIXTURE_FULL_LOG
           if e.get("field") not in ("contradiction_surfaced", "ep_contested")]
    uncovered = validate_emitter_coverage(log,
        expected=set(emit.MANDATORY) | {"contradiction_surfaced",
                                        "ep_contested"})
    assert uncovered == set()  # conditional absence never gapped
