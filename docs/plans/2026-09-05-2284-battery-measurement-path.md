<!-- research-path: docs/epics/1402-eval-battery/02-research-brief.md + #2284 issue body & Amendments 1-5 (authoritative) -->

# #2284 — Battery real-run measurement path: schema v1.1 event log → loader fidelity → I-1 seed fix → parity hash → report writers → exposure study → TVDE executor

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the #1402 battery's real-run measurement path honest — a schema-v1.1 typed event log with per-field emitter coverage, loader fidelity (no silent scenario drops), the I-1 seeded-graph/R1-confound fix, parity protocol-hash migration, live report writers, then the budget-guarded two-part exposure study and the Shared Tool-Verb Decide Executor (TVDE) that drives real model + product read/write/EP surfaces — so #1416 can run a real trace and file an honest verdict.

**Team:** epistemic-team
**Architecture:** Five coupled components, each validated against ground truth BEFORE the executor transport (the original framing) is built — root cause is "no real trace has ever been scored," not "mock episodes":
1. **Schema v1.1 typed event log** (I-4): `RunArtifact` gains a typed `event_log`; every probe field declares an emitter ∈ {tool_event, state_event, envelope, judge_annotation, gold_store, derived}; an emitter-less field forces `report_status=incomplete` — never fabricated (Amend 1 event-ontology reuse: graph ops = product GraphEvent refs, harness records eval metadata only).
2. **Loader fidelity** (I-5): restore dropped scenario content (session_scripts/evidence_tiers/drift/graph_script), single agent-visible render rule, evidence emitted, dual-reader hash equivalence locked.
3. **I-1 seeded-graph fix**: ¬A absent pre-k for ALL arms; A4 pre-k memory = adopted claim_a + evidence only; bct-* benign FP controls; no-leak test over the full rendered policy surface.
4. **Parity + determinism re-scope**: `methodology_hashes` gains a protocol hash; E2E-7.1 → transcript-locked derived fields + measured tolerances (TBD(EXPOSURE) markers).
5. **TVDE executor** (built LAST): one harness-owned deliberation scaffold `ALIGN → (CHALLENGE → DEEPEN → REVISE) → CONVERGE`, byte-identical across arms/families; structured envelope per content boundary; realism gate (zero fabricated turns); v1 probe tier → v2 streams/differential (L4 load-bearing). Siblings A (#2291 A4 SDK/EP channel) + B (#2292 rubrics/model/budget) land before exposure part 1's EP/judge legs; #2293 (event-id determinism) is the event-store alignment owner, never forked (Amend 5).

### Pattern Research

> **Findings date:** 2026-09-05
> Gate skipped: plan touches zero NEW third-party dependencies — model calls ride in-repo wrappers used 2+ times (`tortoise/model_adapters.py`, `tools/longmem_eval/judge.py` gpt-4o default, `battery/runner/model_calls.py`); vendor arms dispatch mock↔real in-repo; embedded FalkorDBLite in-repo. PRIOR_RESEARCH: #2284 issue-scoping v5.1 Phase 1.5 `### Axis Research` (executor-harness canon: MemoHarness arXiv 2607.14159, Agent-Memory manifest, DeepEval/OpenJudge tool-trace metrics; determinism: temp-0 ≠ bit-deterministic arXiv 2606.26185/2602.14349; judge reliability: task/tool rubrics low-reliability arXiv 2606.29920 → arm-neutral rubric, pre-exposure validation) + `### Integration Docs` (all in-repo surfaces enumerated) + Amendments 1-5 (event-ontology reuse, sandbox model, rubric/model/budget decisions, #2104 Phase-C EP-on-ingest MERGED, #2293 alignment). External evals grounded: gbrain Cat-35 receipts/guards (`garrytan/gbrain-evals eval/runner/cat35-*.ts`) + LongMemEval judge shape (`tools/longmem_eval/judge.py`). Model/budget decisions for the exposure legs belong to sibling B (#2292) — not re-researched here.

### Integration Surface Map

Derived from test-design #1404 (S1-S8) + the #2284 scoping wiring table; surfaces touched by THIS plan:

| Surface | Boundary | Test layer | Where | Bug-pattern flags |
|---|---|---|---|---|
| I-4 event log schema/emitter registry | pure (runner/artifacts + new emit module) | unit (deep per-field) | Task 1 `tests/test_battery_schema_v11.py` | emitter-less field must FAIL, never default |
| I-5 corpus loader (yaml + corpus_loader dual readers) | pure config | unit hash-equivalence | Task 2 `tests/test_battery_loader_fidelity.py` | silent drop = data loss (dual-reader drift) |
| I-1 seed graph (runner/setup raw UNWIND/MERGE) | embedded DB (carve-out) | integration + locked no-leak | Task 4 `tests/test_battery_r1_seed.py` | ¬A present pre-k / leak via policy surface = confound |
| Report writers (assemble glob → family_*/recall.json) | fs | unit + integration | Task 5 `tests/test_battery_report_writers.py` | dead aggregation path = empty matrix |
| Parity protocol hash | pure | unit migration | Task 6 `tests/test_battery_parity_hash.py` | decide-loop change invisible to parity |
| Determinism/budget thresholds | config | unit | Task 7 `tests/test_battery_thresholds.py` | test-local tolerance constants (forbidden) |
| S3 model caller + usage capture + error translation | real API (OpenRouter, pinned, temp-0) | integration (real, budget-guarded) | Task 9 (executor v1) | silent fallback (E2E-1.5), no token usage = unmeasurable budget |
| S1/S2 SDK write + EP read (A4) | embedded | integration | Sibling A #2291 (not this plan) | raw Cypher, claims[0], confidence=None |
| S6 judge gate + rubrics | external | integration + validation record | Sibling B #2292 (not this plan) | rubric-scored leg without validated record = blocked |
| S7 artifacts/telemetry | unit schema | Task 1/5 | | re-run reproducibility (E2E-7.1) |
| Event-store determinism | embedded | integration | #2293 (alignment, not forked) | duplicated event-id work |

### Verification Plan

test-routing (domain-aware, carve-out lane `TORTOISE_TEST_CARVE_OUT=1`): code domain, complexity complex → unit + integration + locked no-leak tests; E2E-7.1 determinism re-scope is doc/config wording (Task 7) + locked derived-field test; UX RATING = low, zero UI files → no UX checks; content/config deferred to sibling B (rubrics/corpus re-seal); embedded-only lane (epic #2200 sanction); docker lane untouched by Tasks 1-7. Mechanism-liveness + oracle positive-control legs land in exposure part 2 (Task 8) BEFORE any comparator differential is interpretable (devil's-advocate reordering, adopted in scoping Phase 7).

### UX Design Decisions

| # | Decision Type | User Choice | Rationale |
|---|---|---|---|
| — | UX gate skipped | n/a | Zero UI files; pure measurement-path/backend/config. |

### Execution notes (lanes + sequencing)

12 tasks (> 8) → parallel-session execution handoff. Hard edges: T1 → T4 (registry shapes), T3 → T4 (bct twins needed by the no-leak lock), T2/T4 + T5 precede Tasks 8-10. Optional parallel lanes when dispatched as separate sessions/worktrees: {T2 ∥ T3} (T3's corpus-lock edits land first; whichever merges second re-runs build_corpus --check + the shared corpus test file), {T5 ∥ T6 ∥ T7} (T6's arms.py/arms.yaml schema edit lands first; whichever merges second re-runs tests/test_battery_config.py). Tasks 8-10 are TBD(EXPOSURE) and MUST NOT start until siblings land — #2291 (A4 SDK/EP channel) + #2292 (rubrics/model/budget) CLOSED, #2293 merged, and Tasks 1-7 green. If a sibling is open at Task 8's gate, STOP and surface to the owner (record `skipped→recommendation` only on explicit owner deferral). Sibling-A (#2291) shares the A4 seed/setup surface with Task 4 (setup.py seed path, a4_tortoise.py): merge order pinned — Task 4 lands first with the hermetic seed_mode content-absence contract; sibling A then swaps the verb channel (sdk.ingest / SDK verbs) over the same seed_mode contract. No parallel silent edits to setup.py/a4_tortoise.py.

---
### Task 0: Baseline verification (worktree already created)

**Intent:** Edit base off origin/main tip (`ef5d8421` at plan time; re-anchor if main moved) with a green battery start so RED flips in Tasks 1/2/4/6 are attributable to the change.
**Acceptance:** worktree `feat/2284-battery-measurement-path` exists (hub-worktree.sh), base verified, battery carve-out suite green.
**Files:** none (environment)

**Step 0.1** — `git merge-base HEAD origin/main` → the plan-commit base; plan-doc commit(s) ride the branch tip into the PR.
**Step 0.2** — Baseline: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_run.py tests/test_battery_setup.py tests/test_battery_probes.py tests/test_battery_report.py tests/test_battery_parity.py tests/test_battery_corpus.py tests/test_battery_determinism.py -q` → PASS (≥35 at plan time).

### Task 1 (RED→GREEN): Schema v1.1 typed event log + emitter registry + locked tests (I-4, FIRST)

**Intent:** The trace→probe→report contract is dead because no typed, emitter-declared event log exists. Schema v1.1 makes every probe-consumed semantic field declare an emitter ∈ {tool_event, state_event, envelope, judge_annotation, gold_store, derived} — an emitter-less consumed field forces `report_status=incomplete`, never fabrication. (Scoping §Plan I-4; Amend 1: graph ops become product GraphEvent references — the event log carries eval-metadata events only.)
**Acceptance:** `SCHEMA_VERSION == "1.1"`; `build_run_artifact()` emits an `event_log` key (list); `battery/runner/emit.py` ships `EmitterKind`, `FIELD_EMITTERS`, `EXPECTATION` (per-field `mandatory|conditional`), `validate_event_entry()` (deep per-field + kind-binding), `validate_emitter_coverage(log, *, expected)`. **Emission expectation (P0, cycle-3):** envelope/derived/state-terminal fields (stated_confidence, stated_undecided, stated_defeat_conditions, ep_outcome, decide_cycles) are MANDATORY on every real episode; outcome_correct/confidences/outcomes are family-conditional (calibration/decision); behavioral `tool_event` fields (contradiction_surfaced, explicit_resolution, surfaced_within_turn) + `ep_contested` are CONDITIONAL — absence = legitimate non-occurrence (measured 0.00), never a gap. SINGLE RULE: MANDATORY always gaps when missing; CONDITIONAL (behavioral tool_event + ep_contested) NEVER gaps even when in `expected` — absence is measured 0.00 (a0's comparator and bct's FP pool depend on it); SCENARIO/FAMILY-conditional fields (injection_turn when the scenario plants a ¬A k-turn — never bct twins; outcome_correct/confidences/outcomes for calibration/decision; coverage_subscore for R2 judge legs) gap only when the scorer seam put them in `expected` for THIS episode. No arm-capability term exists — the a0 exclusion is fully handled by CONDITIONAL semantics. **TWO-PHASE COVERAGE (P0, cycle-5):** derived/gold_store/judge_annotation entries (flip_flopped, outcome_correct, control_verdict, confidences/outcomes, coverage_subscore) can only EXIST after the derive/judge passes run at scoring time — so the honesty gate is split: (1) a PRE-SCORING gate on the episode checks only log-emitted fields that exist at episode end (MANDATORY + envelope/state/behavioral — no gold_store in phase 1); (2) a FINAL coverage validation at artifact assembly validates the post-derivation log against the full expected set. The no-data sentinel NEVER fires on fields the derive pass has not run yet. The derive/judge emission pass (runner derive step appending derived + gold-store + judge_annotation entries into the episode log before probe scoring) is owned in Task 5 (probe_scorer) and Task 9 (judge leg). The registry-global mode is a schema-test fixture only. This is what lets a0 (no-store arm, retrieve=()/record=no-op) legitimately measure R1 = 0.00 — its expected set excludes behavioral tool_event fields. Existing "1.0" asserts updated to "1.1" (exactly the 2 in `tests/test_battery_run.py:85,93`); mock runs keep empty `event_log` (allowed — never claimed real).
**Files:**
- Create: `battery/runner/emit.py`, `tests/test_battery_schema_v11.py`
- Modify: `battery/runner/artifacts.py` (SCHEMA_VERSION, `_ARTIFACT_KEYS` + "event_log", build_run_artifact + param), `battery/probes/r1_contradiction.py`, `battery/probes/r2_coverage.py`, `battery/probes/r3_calibration.py`, `battery/probes/r4_defeat.py`, `battery/probes/r5_update.py` (CONSUMED_FIELDS constants), `tests/test_battery_run.py:85,93` ("1.0"→"1.1")

**Step 1.1** — Write the failing schema test first:
```python
# tests/test_battery_schema_v11.py
"""Schema v1.1 event log + emitter registry (issue #2284, Task 1)."""
from __future__ import annotations
import pytest
import yaml
from battery.runner import emit
from battery.runner.emit import EmitterKind, validate_emitter_coverage

def _mini_scenarios(tmp_path) -> list:
    """Tiny authored no-gold corpus via the RUN-path loader (mirrors
    tests/test_battery_run.py::_config_dir — hand-constructing Scenario is
    forbidden: ctor shape is lock-heavy and Task 2 extends it)."""
    from battery.config.corpus import load_corpus
    cfg = tmp_path / "cfg"; cfg.mkdir()
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
    from battery.probes import r1_contradiction, r2_coverage,\
            r3_calibration, r4_defeat, r5_update
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
    log = emit.FIXTURE_FULL_LOG + [
        {"type": "derived", "event": "flip_flop", "at": 9,
         "field": "stated_confidence", "payload": {"value": 0.2}}]
    with pytest.raises(ValueError):
        emit.validate_emitter_coverage(log, expected=set(emit.MANDATORY))

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
```

**Step 1.2** — Run: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_schema_v11.py -v` → FAIL (`module battery.runner.emit does not exist`, SCHEMA_VERSION is "1.0").

**Step 1.3** — Create `battery/runner/emit.py` (minimal-complete):
```python
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
```

**Step 1.4** — Modify `battery/runner/artifacts.py`: `SCHEMA_VERSION = "1.1"`; add `"event_log"` to `_ARTIFACT_KEYS`; `build_run_artifact(..., event_log: list[dict[str, Any]] | None = None)` → include `"event_log": event_log or []`. Update the two asserts: `tests/test_battery_run.py:85,93` → `"1.1"`.

**Step 1.5** — Add `CONSUMED_FIELDS: tuple[str, ...]` to each probe module listing its `trace.get(...)` semantic keys, matching REAL probe reads (verified r2_coverage reads `coverage_subscore`; r5 does NOT read flip_flopped — that is r1's): r1_contradiction: contradiction_surfaced/flip_flopped/explicit_resolution/false_positive/surfaced_within_turn/injection_turn; r2_coverage: coverage_subscore/decide_cycles (coverage_subscore = the gated-judge field → registry kind judge_annotation); r3_calibration: stated_confidence/confidences/outcomes/outcome_correct/ep_outcome/stated_undecided; r4_defeat: stated_defeat_conditions/real_defeat_conditions; r5_update: update_correct_direction/over_reacted. Purely declarative — probe behavior unchanged until Task 9 re-points reads onto the registry-emitted log. Completeness test unions these; an honest r2 declaration therefore forces `coverage_subscore: judge_annotation` into FIELD_EMITTERS (Step 1.3), which is the point.
**Step 1.6** — Run: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_schema_v11.py tests/test_battery_probes.py tests/test_battery_run.py -v` → PASS (new + existing probe tests + both touched "1.1" asserts).
**Step 1.7** — Commit via `commit-workflow` (`feat: schema v1.1 typed event log + emitter registry (#2284)`).

### Task 2 (RED→GREEN): Loader fidelity — Scenario preserves the full production schema + single render rule + per-field survival lock (I-5)

**Intent:** `_coerce_scenario` (corpus.py) discards top-level `question` (appends it as an unlabeled user turn), ALL `session_scripts` turns/questions (xs-* L4 AND int-* L1 load as system-only packs), `evidence_tiers`, `drift`, `graph_script` — so L4 `xs-*` collapses to one turn and cal/lp/L5 content never reaches Scenario. Also `render_reader_prompt` (the ONLY sanctioned renderer) never emits evidence content. Fix at the TRUE drop point (Scenario conversion), not by comparing raw file bytes (yaml carries `gold:`; corpus.json redacts to `gold_sha256` by design — whole-dict byte equality is unsatisfiable). (Scoping §Plan I-5; Amend 1 render rule.)
**Acceptance (two legs — corpus_loader stays dict-typed; NO dict→Scenario conversion):** (a) sealed-JSON leg asserts every authored semantic field survives in corpus.json (allowlist = `{gold, gold_sha256}` ONLY — `hostile`/`attack_type`/`matched_control_for` are render-excluded but json-PRESERVED, so they must survive and are asserted); (b) Scenario leg (run-path YAML loader) asserts typed preservation + renders. `Scenario` preserves the production-schema content it currently drops (incl. `retraction` for ret-* — ret-* carries its ONLY k inside `retraction`, no top-level k — and `drift` in its REAL shape `{decision, offsets: [...]}`) — typed `session_scripts` (per-session `{session, turns, question}`), `evidence_tiers` (authored `{tier, claim, valence}` triples), `drift`, `graph_script` (the REAL shape is a dict `{nodes, nand_edges, contested_pair}` — type it as such, never a str), explicit `question: str` (currently flattened lossily into prompt_pack as an unlabeled trailing turn); the reader-visible projection is reduced to the single render rule (`render_reader_prompt(scenario-render-dict, session)` output + retrieved Memories, never gold); a per-field SURVIVAL test locks the chain yaml → sealed corpus.json → Scenario with an explicit redaction allowlist (gold→gold_sha256 + derived enrichments); no-silent-drop asserts cover xs-*, int-*, cal-*, drift-*, ret-*, lp-*; multi-session render tests (session=N content present, session>N absent); evidence IS emitted for cal-* from authored tier/claim content (never synthesized numbers); `graph_script` is NEVER rendered (reader isolation, existing loader rule) and its SETUP-side consumption is explicitly deferred (Task 4 Step 4.3 references `derive_scenario_graph` honoring `graph_script` — see Task 4 acceptance); the dataclass gains `to_render_dict()` producing the corpus.json-shaped dict `render_reader_prompt` already consumes (no double renderer).
**Files:**
- Create: `tests/test_battery_loader_fidelity.py`
- Modify: `battery/config/corpus.py` (Scenario fields + `to_render_dict`), `battery/config/corpus_loader.py` (evidence emission + `render_from_scenario(scenario)` convenience = `render_reader_prompt(scenario.to_render_dict(), session=...)`; signature of `render_reader_prompt` unchanged), `tests/test_battery_corpus.py` (existing render/corpus tests keep passing — renderer signature unchanged)

**Step 2.1** — Write the failing tests (per-field survival, not byte equality):
```python
# tests/test_battery_loader_fidelity.py
"""Loader fidelity — per-field survival yaml→json→Scenario + single render
rule (issue #2284 T2). TWO legs: (a) sealed-JSON leg over corpus_loader
dicts (the reader surface stays dict-typed — NO conversion), (b) Scenario
leg over the run-path YAML loader on a tmp-authored no-gold mini corpus."""
from __future__ import annotations
import yaml, pytest
from battery.config.corpus import load_corpus as load_yaml_scenarios
from battery.config.corpus_loader import load_corpus as load_sealed_json
from battery.config.corpus_loader import render_reader_prompt

def _mini(tmp_path, *, extra_meta=None):
    cfg = tmp_path / "cfg"; cfg.mkdir()
    corpus = {"meta": {"corpus_version": "1.1", "sealed": True},
              "scenarios": [
        {"id": "cal-mini", "tier": "probe", "family": "R3",
         "task_type": "calibration", "attack_type": "cal", "split": "train",
         "evidence_tiers": [{"tier": "T1", "claim": "green canary sings",
                             "valence": "supports"}],
         "prompt": {"system": "sys", "question": "is the canary green?",
                    "turns": []}},
        {"id": "xs-mini", "tier": "stream", "family": "L4",
         "task_type": "cross_session_contradiction", "attack_type": "xs",
         "split": "train", "k": 2,
         "prompt": {"system": "sys",
                    "session_scripts": [
                        {"session": 1, "question": "s1 question late-probe",
                         "turns": [{"role": "user", "content": "s1 turn"}]},
                        {"session": 2, "question": "s2 question LATE-S2",
                         "turns": [{"role": "user", "content": "s2 turn"}]}]}},
        {"id": "drift-mini", "tier": "stream", "family": "L5",
         "task_type": "decision_drift", "attack_type": "drift", "split": "train",
         "drift": {"decision": "opt", "offsets": ["7d", "21d"]},
         "prompt": {"system": "sys", "question": "still hold?", "turns": []}},
        {"id": "ret-mini", "tier": "probe", "family": "R5",
         "task_type": "retraction", "attack_type": "ret", "split": "train",
         "retraction": {"k": 2, "claim": "retracted claim"},
         "prompt": {"system": "sys", "question": "retracted?", "turns": []}},
        {"id": "lp-mini", "tier": "probe", "family": "R3",
         "task_type": "loopy_contested", "attack_type": "lp", "split": "train",
         "graph_script": {"nodes": [
             {"id": "p", "claim_or_turn_ref": 0},
             {"id": "q", "claim_or_turn_ref": 1},
             {"id": "r", "claim_or_turn_ref": 2}],
             "nand_edges": [["p", "q"], ["q", "r"], ["r", "p"]],
             "contested_pair": {"a": "claim-a text", "neg_a": "claim-b text",
                                "a_ref": "p", "neg_a_ref": "q"}},
         "prompt": {"system": "sys", "question": "loopy?", "turns": []}},
    ]}
    (cfg / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    return load_yaml_scenarios(cfg / "corpus.yaml"), corpus

def test_json_leg_fields_survive_seal():
    """Authored keys survive yaml -> sealed corpus.json (allowlist = ONLY
    gold redaction). corpus_loader stays dict-typed; no conversion."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    authored = yaml.safe_load((root / "battery/config/corpus.yaml").read_text())
    sealed = {sc["id"]: sc for sc in load_sealed_json().scenarios}
    for raw in authored["scenarios"]:
        sid = raw["id"]; sc = sealed[sid]
        for key in raw:
            if key in ("gold",):          # gold -> gold_sha256 (intentional)
                continue
            assert key in sc, f"{sid}: authored key {key!r} dropped by seal"
        if sid.startswith(("cal-", "lp-", "xs-", "int-")):
            # semantic content survives (not just key presence)
            if sid.startswith("lp-"):
                assert sc["graph_script"].get("nand_edges") is not None
            if sid.startswith(("xs-", "int-")):
                assert (sc.get("prompt") or {}).get("session_scripts")

def test_scenario_leg_preserves_fields(tmp_path):
    scs, _ = _mini(tmp_path)
    by = {sc.id: sc for sc in scs}
    cal, xs, lp, drift, ret = (by["cal-mini"], by["xs-mini"], by["lp-mini"],
                               by["drift-mini"], by["ret-mini"])
    assert cal.evidence_tiers and cal.evidence_tiers[0]["claim"] == "green canary sings"
    assert cal.question == "is the canary green?"      # never reconstructed by position
    assert xs.session_scripts and len(xs.session_scripts) == 2
    assert xs.session_scripts[1]["question"] == "s2 question LATE-S2"
    assert drift.drift == {"decision": "opt", "offsets": ["7d", "21d"]}
    assert ret.retraction["k"] == 2 and "retraction" in ret.to_render_dict()
    # graph_script: REAL lp-* sub-shape (1-char node ids, INT turn refs,
    # contested_pair carries claim TEXT + node-id refs — corpus lp-001..012)
    assert lp.graph_script["nodes"][0] == {"id": "p", "claim_or_turn_ref": 0}
    assert lp.graph_script["nand_edges"] == [["p", "q"], ["q", "r"], ["r", "p"]]
    assert lp.graph_script["contested_pair"]["a_ref"] == "p"
    assert isinstance(lp.graph_script["contested_pair"]["a"], str)

def test_multi_session_render_by_session(tmp_path):
    scs, _ = _mini(tmp_path)
    xs = next(sc for sc in scs if sc.id == "xs-mini")
    # via Scenario.to_render_dict — sessions are 1-based (authored session
    # ids 1..N; real xs-* are 1..6; render(session=0) KeyErrors on real data)
    s1r = render_reader_prompt(xs.to_render_dict(), session=1)
    s2r = render_reader_prompt(xs.to_render_dict(), session=2)
    assert "LATE-S2" in s2r and "LATE-S2" not in s1r
    assert "s1 turn" in s1r

def test_render_emits_authored_evidence(tmp_path):
    scs, _ = _mini(tmp_path)
    cal = next(sc for sc in scs if sc.id == "cal-mini")
    out = render_reader_prompt(cal.to_render_dict())
    assert "green canary sings" in out and "Evidence" in out

def test_graph_script_never_rendered(tmp_path):
    scs, _ = _mini(tmp_path)
    lp = next(sc for sc in scs if sc.id == "lp-mini")
    out = render_reader_prompt(lp.to_render_dict())
    assert "nand_edges" not in out and "contested_pair" not in out

def test_single_render_rule_projection(tmp_path):
    scs, _ = _mini(tmp_path)
    ctx = scs[0].to_episode_context()
    assert "gold" not in ctx and "contradiction_pairs" not in ctx
    assert "render" in ctx and "retrieved" in ctx
```
**Step 2.2** — Run → FAIL (fields dropped; `to_render_dict` missing; render lacks evidence).
**Step 2.3** — Implement in `corpus.py`: add `SessionScript`-shaped preservation — `session_scripts: tuple[dict[str, Any], ...] = ()`, `evidence_tiers: tuple[dict[str, Any], ...] = ()`, `drift: dict[str, Any] = field(default_factory=dict)`, `graph_script: dict[str, Any] = field(default_factory=dict)` (real shape `{nodes, nand_edges, contested_pair}`), `question: str = ""` (from `prompt.question` — never reconstruct by position from prompt_pack); in `_coerce_scenario`, when the source has `prompt.session_scripts`/`prompt.question`/`evidence`/`drift`/`graph_script`, coerce and copy them — a present-but-empty coercion raises `ConfigError` (fidelity assertion). `Scenario.to_render_dict()` returns the corpus.json-shaped dict (nested `prompt {system,turns,question,session_scripts}` + evidence) so `render_reader_prompt` needs no new renderer. `to_episode_context()` → `{"id","tier","family","task_type","attack_type","split","render": render_reader_prompt(self.to_render_dict()), "retrieved": []}` (retrieved filled by the arm at run time — never scenario content). QUESTION DE-DUP: `_coerce_scenario` stores raw `prompt.turns` and `question` SEPARATELY — the flattened question is REMOVED from `prompt_pack` (prompt_pack = turns only) so `to_render_dict` never double-renders; the render test asserts the question appears exactly once with no empty `"question: "` line; prompt_pack consumers (setup.py, arms) see turns-only (state the delta in the commit). In `corpus_loader.py`, `render_reader_prompt` emits an `Evidence:` block from authored tier/claim content for packs carrying it (no synthesis).
**Step 2.4** — Run new + corpus + probes: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_loader_fidelity.py tests/test_battery_corpus.py tests/test_battery_probes.py -v` → PASS. (Note: existing `render_reader_prompt` call sites in `validate.py`/runner must keep passing — the renderer signature is unchanged; only Scenario feeding changes.)
**Step 2.5** — Commit via `commit-workflow` (`feat: loader fidelity — Scenario preserves production schema; per-field survival lock (#2284)`).

### Task 3: Corpus v1.1 — bct-* benign FP controls (surface twins) + validation-lock re-lock

> **P0-rescope note (plan-review cycles 1-2, facts verified against corpus.yaml/json + probes):** `d-001..d-015` are family R2/R4 DECISION benign controls (`matched_control_for: ct-00N` per d-*; d-016..d-020 family R4) consumed by the R2 coverage delta path — NOT an R1 FP population; nothing in the run path feeds `r1_contradiction.false_positive_rate` from d-* today (only a unit test with synthetic lists). **R1's FP denominator has NO existing population — `bct-*` is a NEW population** (surface twins of ct-* — policy render identical EXCEPT the ¬A block and the substituted benign question) needed by Task 4's policy-surface no-leak lock and the R1 FP gate. the corpus-lock invariants live in CODE, not thresholds.yaml: `battery/config/schema.py` (CORPUS_VERSION, TASK_TYPES, PACK_COUNTS, PACK_SPLITS) + `battery/config/validate.py` (_ID_PATTERNS, validate_controls, _validate_contradiction_bindings), enforced by `battery/config/validate.py` (`validate_pack_counts`, `validate_pack_splits`, `validate_controls` — 1:1 `matched_control_for` ownership bijection) and locked by `tests/test_battery_corpus.py` (`test_pack_counts`, `test_pack_splits`, `test_controls_bijection`). Adding bct-* without touching these = build refused. The previous "PACK_COUNTS in thresholds.yaml" wording is withdrawn (thresholds.yaml holds no pack counts).

**Intent:** R1's FP ≤ 5% gate needs a measurable benign population with a byte-identical policy surface to the ct-* it controls. Author `bct-001..bct-006` (surface twins of the six smoke ct seeds ct-001..ct-006: identical system prompt / tool surface / envelope schema / turn skeleton, `matched_control_for` link, NO ¬A claim, benign question), through the corpus VALIDATION machinery (new CONTROL_SET kind + version bump), re-seal corpus.json, re-lock the count/split/control tests. FP denominator = bct episodes pooled across arms x runs (>=2 arms => 36 episodes; at n=18 a single FP = 5.55% already fails the <=5% gate, so 18 is never acceptable).
**Acceptance:** `bct-001..bct-006` present in corpus.yaml + sealed corpus.json; `meta.corpus_version` bumped to "1.1" + `schema.CORPUS_VERSION` synced; placement pinned: task_type `contradiction`, family R1, id pattern via `_ID_PATTERNS["contradiction"]` alternation (validate.py — NOT schema.py) and control-set scenarios EXEMPTED from `_validate_contradiction_bindings` (benign twins have no planted pair / no counter-claim at k-1 / need not be `ct-\d{3}`); `validate_controls` per-set semantics: bct 1:1 exactly-once over domain {ct-001..ct-006} + completeness sweep + ≤2 owners/ct across sets, and the d-* ct-domain EXCLUDES bct ids (bct must not inflate the d-* completeness domain); pack counts re-locked (bct under `contradiction`: 15→21, total 134→140); ALL count/split asserts in tests/test_battery_corpus.py re-locked — `test_pack_counts`, `test_pack_splits`, `test_controls_bijection`, `test_enum_schema` (==134), `test_pack_splits_arithmetic` (totals dict + ==134), `test_splits_partition` (==134), `test_corpus_filter` (task_type contradiction ==15→21); bct golds authored + sealed (`gold_sha256` + store entry per bct id — the sealed-store invariants `test_gold_sha256_matches_store`/`test_render_guard_no_gold_substring` enforce); shared `surface_diff(ct-N, bct-N)` predicate defined ONCE in a named test-support module (e.g. `battery/config/control_diff.py`) that Tasks 3 AND 4 import (normalizes the question slot + the ¬A turn; never raw render_hash equality); FP denominator = bct episodes across arms x runs (>=2 arms => 36 episodes).
**Files:**
- Create: `tests/test_battery_corpus_v11.py`, `battery/config/control_diff.py` (shared `surface_diff` + delta-slot constants — the SINGLE home Tasks 3/4 import)
- Modify: `battery/config/schema.py` (CORPUS_VERSION "1.1"; PACK_COUNTS 15→21 / 134→140; PACK_SPLITS), `battery/config/validate.py` (`_ID_PATTERNS` + `bct-\d{3}`; control-set exemption from `_validate_contradiction_bindings`; `validate_controls` per-set semantics + d-* domain excluding bct), `battery/config/corpus.yaml` (+6 bct-* with golds + meta bump), `battery/config/corpus.json` + `.gold_store` (re-sealed), `battery/config/build_corpus.py` (if control-kind plumbing needed), `tests/test_battery_corpus.py` (ALL count/split/control/gold relocks listed in acceptance)

**Step 3.1** — Author `bct-001..bct-006` in corpus.yaml as surface twins (clone ct-001..006 policy text verbatim; delete the ¬A claim + counter-claim + k injection; benign question). Set `matched_control_for: ct-00N` under a `control_set: bct` marker.
**Step 3.2** — validate.py: change `_ID_PATTERNS["contradiction"]` to the alternation `r"(?:ct|bct)-\d{3}"` (a new key is unreachable — TASK_TYPES is closed; pattern applies to every contradiction-task_type id) + test that ct-001 AND bct-001 pass fullmatch while bcx-001 fails; exempt `control_set` scenarios from `_validate_contradiction_bindings`; extend `validate_controls` to per-set semantics (bct exactly-once over {ct-001..ct-006} + completeness sweep; d-* ct-domain excludes bct); bump `CORPUS_VERSION`/`meta.corpus_version`; PACK_COUNTS bct under `contradiction` (15→21, total 140); PACK_SPLITS re-locked (bct all train).
**Step 3.3** — Re-seal: `uv run python -m battery.config.build_corpus` (regenerates corpus.json + `.gold_store/golds.json` — gitignored, absent on fresh clones). Run-path scoring reads golds from the yaml loader (`Scenario.inline_gold` — no gold_ref scenarios exist), so the `.gold_store` vs `battery/golds` split is NOT a scoring seam today. A PRE-RUN FRESHNESS GATE lands in Task 5 (corpus.json manifest + gold_sha256 digests vs the yaml source; refuses BEFORE attempt-dir creation; zero artifacts on stale/absent; carve-out fixture re-seal step) — no store-path rewiring in this task. Re-lock the count/split/control/gold tests from the manifest output.
**Step 3.4** — Tests: policy-surface equality THROUGH the shared `surface_diff` predicate from `battery/config/control_diff.py` (normalizes question slot + ¬A turn — never raw render_hash equality), bct loads + per-set control bijection green, FP denominator fixture (bct pool >= 36 episodes at >=2 arms).
**Step 3.5** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_corpus_v11.py tests/test_battery_corpus.py tests/test_battery_config.py -v` → PASS.
**Step 3.6** — Commit via `commit-workflow` (`feat(corpus): bct-* surface-twin FP controls + v1.1 validation re-lock (#2284)`).

### Task 4 (RED→GREEN): I-1 seeded-graph fix — ¬A absent pre-k for ALL arms; A4 seed_mode; no-leak over the full policy surface

**Intent:** The seeded-graph R1 confound: contradiction claims + k + NAND are pre-seeded from scenario metadata, and A4 `retrieve()` returns them as ordinary memories → R1 (≥90% surfaced) could pass by structure-reading — a confound only the graph arm enjoys. Fix: ¬A arrives in-context at turn k for EVERY arm (never pre-seeded); A4 pre-k memory = adopted claim_a + evidence ONLY (`seed_mode`); surfacing = agent `register_conflict`/`file_nand` on a retrieved-set target ≤ k+1; EP `contested_after_surfacing` (variance > [cal] ep-variance row) = objective marker, NOT the R1 metric — the [cal] row is owned by sibling A's (#2291) calibration posture (thresholds cal-table re-lock), referenced here, never invented. (Scoping §Plan I-1; #2291 Amend 3 §1 read-surface mapping. Probe re-pointing onto the registry-emitted log + no-data sentinel = Task 9; this task fixes WHAT is seeded upstream — acceptance below states the upstream contract, not a probe rewrite.)
**Acceptance:** the runner never seeds claim_b/k/NAND for contradiction scenarios in `seed_mode` (A4 pre-k = claim_a + evidence only); `derive_scenario_graph` honors `graph_script` (dict shape) for lp-* (setup-side consumption — Task 2 restored the field; this task wires it so R3-for-lp has a real EP surface); a locked no-leak test asserts ¬A content is ABSENT from every arm's real pre-k surface, PARAMETRIZED over the FULL ct population (all 15 ct ids read from the corpus + bct-001..006), fresh AND warm stores (batch_setup MERGE never deletes → seed_mode re-setup over a stale full graph must fail closed or purge, never silently retain ¬A); the seed_mode x stream-mode boundary is specified: the warm guard refuses ONLY pre-fix stale contradiction content; re-setup over a CLEAN seed_mode graph from a prior session accumulates (no refuse, no duplicate claim_a) — Task 10's stream default, locked by a Task 10 test; ct-bct equality via the shared `surface_diff` predicate (Task 3) apart from the ¬A block + benign-question slot.
**Files:**
- Create: `tests/test_battery_r1_seed.py`, `battery/testing/__init__.py`, `battery/testing/seeds.py` (setup_seed_mode / seed_full_legacy / real_prek_surface + pre-k projection helpers — the SINGLE test-support home Task 4/10 import)
- Modify: `battery/runner/setup.py` (seed_mode + derive_scenario_graph graph_script wiring + seeder-owned seed-manifest marker), `battery/arms/base.py`, `battery/arms/a4_tortoise.py` (seed-mode setup; verb channel itself = sibling A #2291)

**Step 4.1** — Write the failing tests against REAL corpus content and REAL scenario ids (ct-001..ct-015; ¬A fragments = the actual authored claim text of a pinned ct scenario's counter-claim, read from `Scenario` — never a concatenated search string):
```python
# tests/test_battery_r1_seed.py — I-1 seeded-graph confound fix
"""Run-path loader only (battery.config.corpus.load_corpus -> Scenario list,
yaml source — mirrors run.py:153). corpus_loader is the DICT reader and is
never used here. Helpers defined in this module or a named support module
(battery/testing/seeds.py) — never left to executing-plans to invent."""
from __future__ import annotations
import yaml, pytest
from pathlib import Path
from battery.config.corpus import load_corpus           # run-path loader
from battery.config.arms import load_arms
from battery.config.control_diff import surface_diff, NEG_A_DELTA_SLOTS
from battery.exceptions import ConfigError

_CONFIG = Path(__file__).resolve().parents[1] / "battery/config"
_CORPUS_YAML = _CONFIG / "corpus.yaml"
_ARMS_YAML = _CONFIG / "arms.yaml"

def _cts() -> list:
    return [sc for sc in load_corpus(_CORPUS_YAML) if sc.id.startswith("ct-")]

def _fragments(sc) -> list:
    """¬A fragments from ALL planted pairs (empty for benign bct twins)."""
    return [p.claim_b[:40] for p in sc.contradiction_pairs]

def _search(store, fragments):            # each fragment separately
    return [(fr, store.find_content(fr)) for fr in fragments]

def test_claim_b_never_preseeded_in_seed_mode(tmp_path):
    sc = _cts()[0]
    store = seeds.setup_seed_mode(tmp_path, sc.id)   # battery/testing/seeds.py
    hits = _search(store, _fragments(sc))
    assert not [h for _, h in hits if h], f"¬A leaked pre-k: {hits}"

def test_retrieve_pre_k_has_only_claim_a_evidence(tmp_path):
    sc = _cts()[0]
    store = seeds.setup_seed_mode(tmp_path, sc.id)
    mems = store.retrieve(sc.to_episode_context()["render"][:200])
    texts = " ".join(str(m) for m in mems)
    assert not any(f in texts for f in _fragments(sc))
    assert sc.contradiction_pairs[0].claim_a[:40] in texts or any(
        sc.contradiction_pairs[0].claim_a[:40] in str(m.get("content", ""))
        for m in mems)

def test_no_leak_full_policy_surface_all_arms(tmp_path):
    """¬A absent from every arm's pre-k surface over the FULL ct population.
    Surface composition per arm (pinned, not a tuple): a4 = seeded hermetic
    store + rendered policy truncated BEFORE turn k (pre-k projection — the
    renderer must support session/turn cutoff; a helper in seeds.py builds
    it); mock/a0/a1 + vendor arms (a2/a2b/a3) = policy surface only unless a
    hermetic carve-out capability key exists in arms.yaml."""
    for arm_cfg in load_arms(_ARMS_YAML).values():
        for sc in _cts():
            surface = seeds.real_prek_surface(arm_cfg.arm_id, sc.id)
            assert not any(f in surface for f in _fragments(sc)), \
                f"{arm_cfg.arm_id} leaked ¬A for {sc.id}"
    # ct-bct equality via the SHARED predicate (bct twins: benign pair,
    # empty fragments — excluded from the loop above by _cts filter)
    assert surface_diff("ct-001", "bct-001") <= NEG_A_DELTA_SLOTS

def test_seed_mode_warm_store_fails_closed_on_stale(tmp_path):
    """seed_mode over a stale PRE-FIX full graph refuses (seeder-owned
    marker distinguishes stale-seeder content from agent-filed content:
    the guard tests a seed-manifest marker written by the seeder, never raw
    content presence). Agent-filed claim_b content (Task 9/10) must NOT
    false-refuse — locked in Task 10."""
    store = seeds.seed_full_legacy(tmp_path, "ct-001")   # pre-fix seeding
    with pytest.raises(ConfigError):
        seeds.setup_seed_mode(tmp_path, "ct-001")
```
**Step 4.2** — Run → FAIL (claim_b/NAND currently pre-seeded; warm-store retention unhandled).
**Step 4.3** — Implement `seed_mode` in `setup.py`: for contradiction task_type seed ONLY claim_a + evidence (+ cal priors via sibling A when landed); `derive_scenario_graph` creates statement points ONLY for prompt_pack turns BEFORE the injection turn (k) — the injection-turn statement point (whose content contains the ¬A phrase) and the pair claim_b point are NEVER seeded (¬A arrives in-context at k). Note the Task-2 question de-dup interplay (the flattened question currently sits at prompt_pack index 6; after Task 2 it is gone — index semantics shift; the k-gate must key off the authored turn list, never positional index). Check test_battery_setup.py expectations that rely on ct-* operators/NAND sources being present at setup (e.g. source-promotion tests) and relock them for seed_mode defaults. Task 4 KEEPS the current hermetic batch-seed path and enforces content absence through it; the `sdk.ingest` verb-channel swap is sibling A's (#2291), applied over the same seed_mode contract after Task 4 lands (Execution-notes merge order). Warm-store guard: `seed_mode` setup detects contradiction content (claim_b/k/NAND) already present in the namespace → refuses with `ConfigError` (fail-closed; guard keys on a seeder-owned seed-manifest marker, never raw content presence). `derive_scenario_graph` additionally consumes `scenario.graph_script` (nodes + nand_edges) so lp-* builds its EP surface. A4 `setup_scenarios` uses seed_mode by default for contradiction scenarios.
**Step 4.3b** (RED→GREEN): `derive_scenario_graph` on a REAL lp-* scenario (graph_script dict sub-shape: node-id NAND triangle + contested_pair refs) emits the 3 NAND edges + contested-pair binding so R3-for-lp has a real EP surface (test in tests/test_battery_r1_seed.py).
**Step 4.4** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_r1_seed.py tests/test_battery_setup.py tests/test_battery_run.py -v` → PASS (incl. existing batch/naive_setup equivalence tests with the new seed_mode default).
**Step 4.5** — Commit via `commit-workflow` (`fix: I-1 seed_mode — ¬A never pre-seeded; warm-store fail-closed; lp graph_script wiring (#2284)`).

### Task 5: Live report writers — family/recall files written from a real run + emitter-gap honesty gate wired + report_status home fixed

> **plan-review cycle-1 rescopes:** (a) `report_status` constants live in `battery/report/assemble.py` (`REPORT_STATUS_OK`/`REPORT_STATUS_INCOMPLETE` + Profile), NOT classify.py (leaf cell classifier — importing assemble would cycle). New statuses go in assemble.py; only the per-cell `insufficient_n` rule goes in classify.py. (b) Writers must be CALLED by the runner and READ by the CLI — dead writers reproduce the dead path. (c) The smoke command must use real CLI flags/ids. (d) The schema-v1.1 honesty gate (`validate_emitter_coverage`) must reach the artifact/classify boundary NOW, not only in the gated Task 9: a real-run artifact whose consumed fields are emitter-less flips `report_status=incomplete_emitter_gap` and probes never produce measured values from an uncovered log (no-data sentinel).

**Intent:** kill the dead aggregation path: run end writes per-family JSON + `recall.json` into the attempt dir; `battery report`/`calibrate` read them via attempt-dir resolution; profile.json is populated when families exist; the emitter-gap gate closes the I-4 honesty loop pre-executor.
**Acceptance:** after a run, the attempt dir contains one JSON per scored family (family, n, per-metric values + cells `measured|insufficient_n`) and `recall.json` (per-episode retrieved Memories + EP markers), written atomically (tmp+os.replace) by the runner path; `cli._cmd_report`/`_cmd_calibrate` resolve the LATEST attempt dir (`attempt_dir_resolve(out_dir)`) instead of globbing top-level only; cross-attempt isolation: attempt-2 without R1 does NOT inherit attempt-1's R1 (no vacuous pass); emitter-gap: the artifact gains an explicit `run_mode: mock|real` discriminator; the episode's scored family is threaded through the scorer seam so `build_run_artifact` computes the per-episode EXPECTED set (MANDATORY x scenario/family-conditional per the Task-1 expectation rule — no arms.yaml capability term; default HarnessScorer runs have empty expected => gap empty, mock/real neutral) and TWO-PHASE gate: (1) pre-scoring check on the episode covers pre-derivation fields only (MANDATORY + envelope/state/behavioral); (2) a final coverage validation at artifact assembly covers the post-derivation log (derived/gold/judge entries appended by the derive pass in probe_scorer / Task 9 judge leg). EXCLUDED episodes (recorded `excluded_reason`) are exempt from the mandatory gap, but their expected-vs-emitted snapshot is recorded in the exclusion record (an honest exclusion is NEVER mislabeled an emission bug, and the exemption cannot become a gap-gate bypass). assemble maps a non-empty post-derivation emitter_gap on a REAL artifact to `report_status=incomplete_emitter_gap`; conditional/behavioral absence is never a gap (P0 cycle-3: a0 R1 measures 0.00, load-bearing comparator intact); run-level status precedence: all-excluded/cap-stopped real run → `incomplete_real_no_episodes`; partial run (some measured, rest excluded/over-budget) → `incomplete_real_partial`; over-budget stop → `incomplete_real_over_budget`; each driven by run_mode + summary exit_code + per-episode statuses composed into the profile — NEVER conflated with the mock status; a pre-run FRESHNESS gate lives here too (run.py, before attempt-dir creation): corpus.json manifest + gold_sha256 digests vs the yaml source — absent/stale refuses cleanly with ZERO artifacts, locked test, carve-out fixture re-seal step; mock runs stay at `incomplete_missing_metrics` even with a probe scorer wired (all cells `insufficient_n` — locked: mock never false-flags `incomplete_emitter_gap`); report = LATEST-attempt-only via `attempt_dir_resolve`, which filters dirs on `summary.json` presence (completion marker; a crashed/cap-stopped dir never shadows an older complete attempt — crash-shadow test locks it); per-family JSON schema pinned (family, n, values: {metric: [v...]}, cells: {metric: measured|insufficient_n}); attempt dirs named timestamp+run_id, deterministic ordering under same-second collisions.
**Files:**
- Create: `tests/test_battery_report_writers.py`, `battery/runner/probe_scorer.py` (Probe-to-Scorer adapter + no-data sentinel)
- Modify: `battery/report/assemble.py` (writers, `attempt_dir_resolve`, new REPORT_STATUS branches, emitter-gap), `battery/report/classify.py` (per-cell `insufficient_n` rule only), `battery/runner/run.py` (invoke writers at run end; run_battery passes run_mode), `battery/cli.py` (`_cmd_report`/`_cmd_calibrate` attempt-dir resolution), `battery/runner/artifacts.py` (emitter_gap + run_mode fields), `tests/test_battery_run.py` (writer files change its count-based asserts — same task relocks)

**Step 5.1** — Write the failing tests:
```python
# tests/test_battery_report_writers.py
def test_run_writes_family_and_recall(tmp_run):        # real CLI defaults
    # battery run --mock --arms a0 --out OUT --max-episodes 2 (valid flags)
    # + a probe scorer wired on the command line for one family
    out = tmp_run()
    assert (out / "family_R1.json").exists()
    assert (out / "recall.json").exists()

def test_report_reads_latest_attempt_dir(tmp_run, monkeypatch):
    run1 = tmp_run(families={"R1"})                     # attempt 1 measures R1
    run2 = tmp_run(families=set())                      # attempt 2 does NOT
    report = invoke_report(out_dir=runs_root())         # default --out
    # latest attempt content present; attempt-2's missing R1 must NOT wipe
    # or vacuous-pass attempt-1's R1 (cross-attempt isolation)

def test_emitter_gap_flips_report_status(tmp_run_with_partial_log):
    art = tmp_run_with_partial_log(emit_only={"stated_confidence"})
    assert art["emitter_gap"]                       # uncovered consumed fields
    assert profile_status(art) == "incomplete_emitter_gap"

def test_probe_no_data_sentinel_on_uncovered_log():
    assert r1_compute(covered_log()) is not None
    assert r1_compute(gapped_log()) is None          # never a measured 0.0

def test_stale_corpus_json_refuses_before_attempt_dir(tmp_run):
    # tamper a gold_sha256 in corpus.json -> pre-run freshness gate refuses
    # cleanly with ZERO artifacts (no attempt dir created)
    out = tmp_run_root()
    tamper_seal()                                  # helper: corrupt one digest
    code = run_battery_guard(RunConfig(out_dir=out))
    assert code == REFUSED and not any(out.glob("attempt-*"))

def test_family_writes_atomic(tmp_run):
    # a partial/corrupt family file (no tmp+replace atomicity) must never be
    # readable as a measured cell
    attempt = tmp_run()
    (attempt / "family_R1.json").write_text('{"cells":', encoding="utf-8")
    assert read_cells(attempt / "family_R1.json") is None

# Helper contract (module-level, defined in this test file; tmp_run-style
# fixtures mirror tests/test_battery_run.py::_config_dir):
#   profile_status(art) -> status string
#   r1_compute(log) -> value | None  (None when expected coverage is gapped)
#   covered_log() / gapped_log() -> schema-v1.1 event logs
#   invoke_report(out_dir) -> parsed profile dict
#   tmp_run(families=...) / tmp_run_with_partial_log(emit_only=...)
```
**Step 5.2** — Run → FAIL (no writers, no attempt-dir resolution in cli, no emitter_gap).
**Step 5.3** — Implement in `assemble.py`: `attempt_dir_resolve(out_dir)` — dirs must contain `summary.json` (completion marker; crashed/cap-stopped dirs never shadow a prior complete attempt; crash-shadow test locks it); per-family + recall writers (tmp+os.replace); extend `REPORT_STATUS_*` with the FULL run-level set: `incomplete_emitter_gap`, `incomplete_real_no_episodes`, `incomplete_real_partial` (some measured, rest excluded/over-budget), `incomplete_real_over_budget` (each run_mode + summary exit_code + per-episode status composed — a real zero-real-episodes run is NEVER the mock status). PRE-RUN FRESHNESS GATE in run.py (before attempt-dir creation): corpus.json manifest + gold_sha256 digests vs the yaml source; refuses with zero artifacts; carve-out fixture re-seal step. NEW `battery/runner/probe_scorer.py`: Probe-to-Scorer adapter (probes implement `score(trace, gold, threshold)`; the run seam is `Scorer.score(episode, scenario, rubric_id)` in scorers.py — `resolve_scorer("battery.probes.r1_contradiction")` crashes today) bridging them + returning the no-data sentinel (None to `insufficient_n` cell) when the episode's expected-coverage set is gapped. Expected set computed on the episode BEFORE scoring via the scorer seam (scoring precedes artifact construction); writers invoked after scoring in run.py. In `cli.py`, `_cmd_report`/`_cmd_calibrate` resolve via `attempt_dir_resolve` (root fallback for legacy).
**Step 5.4** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_report_writers.py tests/test_battery_report.py tests/test_battery_schema_v11.py tests/test_battery_cli.py tests/test_battery_run.py -v` → PASS (incl. relocked count-based artifact asserts in test_battery_run).
**Step 5.5** — Commit via `commit-workflow` (`feat(report): family/recall writers wired to run+CLI; emitter-gap honesty gate; status branches (#2284)`).

### Task 6 (RED→GREEN): Parity protocol hash — methodology_hashes gains seed/model/temp/event-schema/tool-surface

**Intent:** `methodology_hashes` covers only reader_prompt + rubric_id (#1414) — decide-loop/protocol changes (seed, model, temp, event schema v1.1, tool surface) would be invisible to parity. A changed protocol must trip the parity unchanged-check + require baseline re-record (#1144-gated; baseline absent today — parity stays gated until #1144, but the hash contract + migration test land now).
**Acceptance:** `methodology_hashes` returns a protocol-hash third element (sha of {seed, model_pin, temp, SCHEMA_VERSION, tool_surface_ids}); `run_parity` compares all three; migration test: old 2-tuple records (reader_prompt_hash+judge_rubric_id_hash) still match when protocol_hash is absent-or-matching (back-compat) AND a protocol change (schema 1.0→1.1, model change) trips `match=False`; parity README/verification docs note the #1144 baseline-record requirement.
**Files:**
- Create: `tests/test_battery_parity_hash.py`
- Modify: `battery/parity/runner.py` (methodology_hashes/run_parity + protocol), `battery/config/arms.py` + `arms.yaml` (add `model_pin`/`temperature` fields to ArmConfig — checked-in flash-class placeholders; sibling B re-locks them with its measured pin), `battery/cli.py` (`_cmd_parity`: derive protocol inputs from arms.yaml model pin + temp + `SCHEMA_VERSION` + tool-surface ids; baseline record gains `protocol_hash`), `tests/test_battery_parity.py` (existing call sites — plan runs this file at Step 6.4); coordination note: the #1144 baseline-record PRODUCER (tools/longmem_eval/report.py) must emit `protocol_hash` for the real unchanged-check to see protocol deltas — cli backfills it on read; producer edit is out of battery scope

**Step 6.1** — Write failing tests (protocol delta trips; back-compat 2-tuple; schema bump trips).
**Step 6.2** — Run → FAIL (2-tuple only).
**Step 6.3** — Implement (load_arms data path closed): `battery/config/arms.py::load_arms` extended to parse `model_pin`/`temperature` into `ArmConfig` (the ONLY place new fields can populate); `def protocol_hash(*, seed: int, model: dict, event_schema: str, tool_surface: tuple[str, ...]) -> str`; `methodology_hashes(reader_prompt, judge_rubric_id, *, protocol)` → 3-tuple (each element 16-hex, element order fixed, compared independently — collision policy documented next to `_sha256`); `run_parity` compares all three with back-compat (baseline without `protocol_hash` → compare 2 + warn); `cli._cmd_parity` LOADS arms.yaml via `load_arms` (today it hardcodes arm "a4" and reads no config — this task wires it) and computes protocol inputs from the pinned arm's `model_pin` + `temperature` + `artifacts.SCHEMA_VERSION` + tool-surface ids, so a protocol change (schema bump, model change) trips the unchanged-check end-to-end; back-compat 2-tuple warn path gets a locked test + the protocol-unknown state persisted into the parity record (forcing the #1144 re-record rather than leaving drift invisible).
**Step 6.4** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_parity_hash.py tests/test_battery_parity.py -v` → PASS.
**Step 6.5** — Commit via `commit-workflow` (`feat(parity): protocol hash — seed/model/temp/event-schema/tool-surface (#2284)`).

### Task 7: E2E-7.1 determinism/budget re-scope wording + thresholds tolerance block (TBD(EXPOSURE) markers)

**Intent:** E2E-7.1 `|Δ|≤1e-6` on real-path model-generated fields is infeasible (temp-0 ≠ bit-deterministic; arXiv 2606.26185/2602.14349). Re-scope to transcript-locked derived/objective fields with measured per-metric tolerances + a nondeterminism fingerprint for model-text/judged; budget wording gains TBD(EXPOSURE) markers (800 tok/ep guess → measured from probe data). (Scoping §Plan E2E-7.1 re-scope + Budget.)
**Acceptance:** `docs/epics/1402-eval-battery/04-plan.md` §7 E2E-7.1 restated (ownership vs sibling B #2292: THIS task seeds the tolerance table from determinism-test measured values + restates §7 wording; sibling B re-locks with exposure-measured numbers OVER this seed. Coordination note for both workstreams: thresholds.yaml/04-plan edits must merge after Task 7 lands — no parallel silent edits to the same rows.) (transcript-locked |Δ|≤1e-6 on derived/objective; measured tolerances for model-text/judged; fingerprint recorded); `battery/config/thresholds.yaml` gains `determinism.tolerances` block (per-metric, initialized from determinism-test measured values — never a test-local constant) + `budget` wording TBD(EXPOSURE); `determinism` test asserts epsilon path + tolerance table folded into the cal-table hash (`calibrate --print` route); corpus/budget `arms.yaml` tokens annotated `measured_after_exposure` (values stay until exposure part 1 lands).
**Files:**
- Modify: `docs/epics/1402-eval-battery/04-plan.md` §7, `battery/config/thresholds.yaml`, `battery/config/arms.yaml`, `tests/test_battery_determinism.py`, `tests/test_battery_config.py`

**Step 7.1** — Edit 04-plan.md §7 E2E-7.1 + budget rows (restate; mark TBD(EXPOSURE)).
**Step 7.2** — thresholds.yaml: add `determinism.tolerances` seed block (epsilon 1e-6 for derived; tolerance map initialized to determinism-test measured deltas).
**Step 7.3** — Config tests assert: tolerance constants resolve from thresholds.yaml (no test-local constant); arms.yaml token fields carry the `measured_after_exposure` note (provisional until exposure).
**Step 7.4** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_determinism.py tests/test_battery_config.py -v` → PASS.
**Step 7.5** — Commit via `commit-workflow` (`docs+config: E2E-7.1 determinism re-scope + tolerance table (TBD(EXPOSURE)) (#2284)`).
### Task 8 (SPEC, TBD(EXPOSURE) — gated): Two-part exposure study

> **PRECONDITION (executing-plans must verify before Task 8 starts):** siblings landed — #2291 (A4 SDK/EP channel + lane matrix) and #2292 (arm-neutral rubric JSONs + judge validation record + model pin + measured budget numbers) CLOSED; #2293 merged (event-id determinism); Task 1-7 green. If any sibling is open, STOP — do not start exposure (its EP/judge/token legs are unvalidated without them). Record gate as `skipped→recommendation` only if owner explicitly defers.

**Intent:** Retire the 4 executor risks on a budget-guarded, measured basis BEFORE building the executor transport: (1) R1 vacuity delta (does the instrument see surfaced-vs-not at all?), (2) EP reachability (agent-filed NAND moves `compute_confidence` on target ≥ [cal] amount — sibling A path), (3) judge signal (arm-neutral rubric items discriminate — sibling B validation), (4) token economics (arms.yaml measured, not the 800 tok/ep guess). (Scoping §Plan exposure 1+2; devil's-advocate reordering adopted: mechanism-liveness + oracle sensitivity land in part 2 BEFORE executor v2/load-bearing differentials.)
**Acceptance (part 1 — envelope/token/judge legs):** a MINIMAL usage-capture slice ships FIRST inside part 1 (the caller-bridge seam from Task 9 step 2, pulled forward: in-repo caller records per-call token usage — without it tokens are unmeasurable and the token-economics risk cannot be retired with data); smoke run of 3-5 scenarios (decision/contradiction/calibration, 1 per family × A0+A4) under the budget guard produces: measured tokens/ep per family (re-locks arms.yaml), envelope field-fill rate, judge-leg agreement on validated rubric items (sibling B record), and the exposure report `exposure-part1.md` in the attempt dir with the 4 risk verdicts.
**Acceptance (part 2 — cadence + mechanism-liveness + oracle):** progressive per-user-turn delivery cadence observed on the real scaffold (early-exit at n≥3 ceiling respected); **mechanism-liveness leg**: on a synthetic EP smoke graph, agent files a true NAND → `compute_confidence` on the target moves ≥ [cal] amount AND the moved value is visible on the next product retrieve (sibling A surface); **oracle sensitivity leg**: a "super-A4" positive control through R1/R3/R5 — the instrument must see the planted effect at max strength; report statuses exercise the new branches (`incomplete_mechanism_unobserved` when the EP smoke leg fails; per-cell `insufficient_n` instead of vacuous passes — R3 zero-contested never returns 1.0). Budget guard: dollar cap honored with 3-5× thinking-model headroom (sibling B numbers).
**Files:** exposure study harness scripts under `battery/exposure/` (new package — include `battery/exposure/__init__.py`), `battery/config/arms.yaml` (measured re-lock), `battery/report/assemble.py` (real-no-episodes / mechanism status branches — statuses live in assemble.py per cycle-1 #11), `battery/runner/model_calls.py` + `battery/runner/run.py` + `battery/config/budget.py` (usage capture + mid-run accumulator + cap check — Task 8's RED steps need these files in-scope), tests for liveness/oracle/judge-failure legs.
**Steps (RED-first per leg; commits via commit-workflow):**
1. Mechanism-liveness test (RED): synthetic graph + file NAND → assert EP move ≥ the [cal] ep-variance row READ FROM thresholds.yaml (sibling-A re-locked row — never a literal constant) on next retrieve. → sibling A must make this pass; if the product path fails → FILE product issue (never opt-out).
2. Oracle sensitivity test (RED): super-A4 through R1/R3/R5 → assert planted effect at max strength registers.
3. Usage-capture slice (RED→GREEN): caller records per-call tokens + dollar cost; locked test (usage sums equal arms.yaml re-lock values — estimate-vs-measured delta surfaced, never silently overwritten).
4. Mid-run budget-cap test (RED→GREEN): a caller whose accumulated spend exceeds the dollar cap mid-run stops the run with an over-budget artifact/status (never silent continuation) — budget.py's pre-run estimate alone is insufficient. The dollar cap lives in `budget.yaml` (`max_estimated_cost_usd`) — the override uses the FIXTURE-CONFIG-DIR mechanism (budget.py's own documented test pattern; load_budget overlay), NOT thresholds.yaml (Task 8 never edits it; no test-local constant). JUDGE spend is explicitly metered: judge-call tokens/cost count toward the same cap (or excluded with a documented budget-reserve line — sibling B owns the reserve) + a judge-failure injection test asserts a recorded per-call outcome + degraded-but-complete exposure report (never a crash).
5. Budget-guarded 3-5-scenario smoke (real model, pinned, temp-0, judge validated): collect tokens/envelope/judge-leg → exposure-part1.md with 4 risk verdicts; re-lock arms.yaml measured numbers.
6. Report the 4 risk retirements on #2284; executor v1 (Task 9) may only start with all 4 retired or explicitly waived by owner.

### Task 9 (SPEC, TBD(EXPOSURE) — gated): Executor v1 — TVDE probe tier (R1-R5), A0 first

> **PRECONDITION:** Task 8 part 1+2 passed (4 risks retired or waived); sibling A+B landed; Task 1-7 green.

**Intent:** The real executor — one harness-owned deliberation scaffold `ALIGN → (CHALLENGE → DEEPEN → REVISE) → CONVERGE`, byte-identical across arms/families (family variation lives in scenario content only), progressive per-user-turn delivery, one schema-validated structured envelope per content boundary `{position, stated_confidence, undecided, defeat_conditions, intents[register_conflict], citations}`, CONVERGE reopenable, early-exit at n≥3. Harness-authored (never claimed as product semantics), grounded in the decide.py one-shot filing flow + the 7-step tortoise-decide skill (ALIGN≡refine/scope · CHALLENGE≡research+check+connect · DEEPEN≡file findings + wire truth/relevance · REVISE≡re-file · CONVERGE≡compute_confidence+rank+envelope). (Scoping §Chosen approach TVDE; #2284 Amendment 1/2.)
**Acceptance:** `battery run --arms a0 --tier 1 --max-episodes 3` (probe tier — existing flags; no new --bounded) executes REAL model turns rendered from `render_reader_prompt`; trace = schema-v1.1 event log with EXPECTATION-scoped coverage (Task 1: MANDATORY x scenario/family-conditional; behavioral/conditional absence on a no-store arm (a0) or a benign twin (bct) = measured 0.00, never a gap — a0-first is coherent because a0's R1 0.00 is the load-bearing comparator); **realism gate**: zero fabricated turns in any artifact claimed real (assert: every turn content non-empty, distinct per scenario, and matches the model call outcome recorded; no "turn N (seed S)" placeholders — E2E-1.1 real half); model calls via in-repo caller (sibling B pin; temp-0; UNCAPPED output; usage capture + error-class translation layer to battery RateLimited/CallTimeout; per-call outcome recorded honestly, silent fallback impossible — E2E-1.5); envelope is the ONLY scalar channel (no prose mining); `decide_cycles` = harness-side counter reported-not-scored; R2 coverage = the only gated-judge field in R1-R5 (sibling B rubric + validation record required before any rubric-scored leg runs — JudgeClient real mode, never raw); the R2 judge leg appends the coverage_subscore judge_annotation entry to the episode log BEFORE artifact assembly so the phase-2 final coverage validation sees the post-derivation log; R1 reads tool_event surfacing + ep_contested marker; **emission-loss-proof executor (SECOND-MODEL-GATE P1 residual):** the executor must GUARANTEE surfacing tool_event emissions are complete/loss-free — a contradiction the agent genuinely surfaced must always produce its `contradiction_surfaced`/`surfaced_within_turn` entry; the emission seam must fail closed (never silently drop) on a surfacing event, so CONDITIONAL absence is provably non-occurrence rather than a lost emission (absence-as-0.00 stays the sanctioned a0/bct comparator only when emissions are provably complete); parity protocol hash active (Task 6).
**Files:** `battery/runner/executor.py` (TVDE scaffold + envelope schema), `battery/runner/caller_bridge.py` (adapter→caller + usage capture + error translation), `battery/runner/model_calls.py` (extend usage capture), `battery/runner/run.py` + `battery/cli.py` (real-model runner plumbing + run_mode), `battery/arms/a0_plain.py` (A0PlainArm) wiring via the adapter seam, `battery/report/assemble.py` (realism/incomplete status branches — home per cycle-1 #11), `tests/test_battery_executor_v1.py`, `tests/test_battery_realism.py`.
**Steps (RED-first per slice; commits via commit-workflow):**
1. Envelope schema + validation (RED→GREEN): declare/validate envelope dict; locked test (unknown intent → ValueError).
2. Caller bridge (RED→GREEN): mock-outcome caller records usage; error translation table (RateLimited/CallTimeout classes exist in model_calls); silent-fallback impossible test.
2b. Mid-episode terminal failure honesty (RED→GREEN, E2E-1.5 at episode level): inject RateLimited/CallTimeout beyond retries at a mid-scaffold turn AND at the first turn (zero-real-turn boundary) → artifact contains EXACTLY the real turns (no placeholder continuation to reach CONVERGE), terminal outcome recorded in model_call_outcomes, episode excluded with reason, that family's cell reports insufficient_n (never a measured value from a truncated trace), and ep_outcome is NOT CONVERGED unless the scaffold genuinely converged.
2c. Real-seam failure-mode mapping (RED→GREEN, network-free): each concrete real-provider failure (429, 5xx, empty-200 content, timeout) maps to the translated battery exception at the REAL adapter seam — not just the mock caller — so real-path terminal failures cannot bypass the honesty branch. decide_cycles gets a max ceiling (default 8, counting decide_cycle_inc events — never scaffold iterations) + a cap test; AT the cap the scaffold EXITS NOT-converged with a recorded terminal outcome + excluded reason + insufficient_n cell — it NEVER force-reports CONVERGED (fabrication) and never silently truncates.
3. TVDE scaffold on A0 with mock caller (RED→GREEN): 3-turn deliberation, envelope per boundary, decide_cycles increments, early-exit; byte-identical scaffold across families test.
4. Realism gate (RED→GREEN): real-model smoke (2 scenarios, budget-guarded) → artifacts carry real content + full EXPECTED coverage for the run's scenarios, validated phase-2 at artifact assembly against the post-derivation log (MANDATORY + the scenario/family-conditionals the 2 scenarios actually expect — never registry-global); zero-placeholder assert.
5. Probe-tier wiring R1-R5 consume the log through Task-1 emitter registry (fields already declared); report matrix non-empty (Task 5 writers).
6. Exposure re-check + commit chain; gate executor v2 on this task's acceptance.

### Task 10 (SPEC, TBD(EXPOSURE) — gated): Executor v2 — streams/differential tier (L1-L6/D2-D4); L4 = load-bearing surfacing

> **PRECONDITION:** Task 9 green; sibling A+B landed; #1144 parity baseline OR owner waiver recorded (parity leg still gated).

**Intent:** Extend TVDE to streams (multi-session L1-L6 via session accumulation in the per-scenario graph — the graph IS durable memory, one graph per test) and differentials (D2-D4); **L4 becomes the load-bearing surfacing construct** (cross-session contradiction surfaced at the right session — R1 single-session stays a reported diagnostic, never load-bearing); per-cell `insufficient_n` discipline (no vacuous passes); report_status branches (`incomplete_l4_underpopulated` when L4 n is below threshold). (Scoping §Plan executor v2; #2291 Amend 3 §3.)
**Acceptance:** stream episodes accumulate across sessions in the one per-scenario graph (no reset mid-stream); re-setup at each session boundary over a CLEAN seed_mode graph accumulates (no ConfigError, no duplicate claim_a) while a stale pre-fix graph refuses (Task 4 guard) — locked test; session-1 retrieval shows NO session-2 content and session-2 retrieval surfaces it (L4 A/¬A across sessions via the product read surface); differentials D2/D4 run under the same byte-identical scaffold; report matrix populated per cell with measured/insufficient_n; E2E-7.1 transcript-locked derived fields hold on the real path (Task 7 tolerances) + cross-run event_log comparison uses ISOLATED fresh per-run namespaces and compares session-relative event ORDER (absolute ns:seq restarts per namespace by design) with an explicit run-2 pre-k contamination assert (no run-1 memories retrievable) — #2293 owns ingest ordering; this is the consumption test; zero fabricated turns across ALL tiers.
**Files:** `battery/runner/executor.py` (stream mode), `battery/report/assemble.py` (incomplete_l4_underpopulated run-level status branch — statuses live in assemble.py; classify.py holds only the per-cell insufficient_n rule), `tests/test_battery_executor_v2.py`, `tests/test_battery_streams_real.py`.
**Steps (RED-first; commits via commit-workflow):** 1) stream-mode session open/accumulate/close test; 2) seed_mode re-setup-over-clean-graph accumulates + session-0/session-1 content isolation (RED until isolated); 3) L4 cross-session surfacing test on the real scaffold (RED until surfaced); 4) differential tier smoke; 5) full-tier budget-guarded smoke + report matrix; 6) E2E-7.1 re-run with Task-7 tolerances + cross-run event_log sequence equality.

### Task 11: Full verification + PR + handoff

**Intent:** Prove the whole chain green under the carve-out lane, close doc/registration gaps, open the PR for review, and hand off to #1416.
**Acceptance:** full battery carve-out suite green; docs updated (epic 04-plan §7 E2E rows + spec §R1-R5 anchors + thresholds tolerance block per Task 7; this plan's header research-path resolves); PR opened via commit-workflow (auto code-review gate); plan doc carries the `<!-- plan-review: status=clean -->` signature; issue #2284 → `planned`.
**Steps:**
1. Full suite: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery*.py -q` → PASS.
2. Doc sweep: stale §R1-R5 anchors / E2E-7.1 wording greps clean.
3. PR + plan-review signature; apply `planned` label to #2284.
4. Post handoff note on #2284: `#1416` deps updated (real-run gated on #2284 v1+v2 + siblings A/B; parity leg gated on #1144).

---

## Review Changelog

### plan-review cycle 1 (2026-09-05, 3 of 4 reviewers survived; #4 completed the failure-mode dimension)
Reviewers returned 1 P0 + 14 P1 + ~9 P2. Controller (fixer role) applied all fixes to the plan; re-dispatch in cycle 2.
| # | Issue | Severity | Location | Fix Applied |
|---|-------|----------|----------|-------------|
| 1 | Task 3 corpus re-seal collides with committed validation invariants (PACK_COUNTS/PACK_SPLITS live in schema.py + validate.py control bijection + relocked tests; d-* controls already exist) | P0 | Task 3 | Re-scoped: bct-* as surface twins w/ control-set bijection + CORPUS_VERSION bump + real file list + test relocks; withdrawn "PACK_COUNTS in thresholds.yaml" |
| 2 | Step 1.1 snippet unbuildable (episode=None + Scenario ctor) | P1 | Task 1 | Snippet now builds via real loader scenario + EpisodeResult |
| 3 | Dual-reader byte-hash unsatisfiable by design (gold redaction) | P1 | Task 2 | Replaced w/ per-field survival yaml→json→Scenario + redaction allowlist; multi-session/int-* covered |
| 4 | Scenario cannot preserve session content (render bridge has no data) | P1 | Task 2 | Typed session_scripts/evidence_tiers/drift/graph_script + to_render_dict adapter |
| 5 | Amend-1 seam: tool_event product-verb payloads = parallel log | P1 | Task 1 | tool_event entries carry event_ref only; seam pinned in emit.py validate |
| 6 | Registry incomplete vs real probe fields; test self-referential | P1 | Task 1 | Full union FIELD_EMITTERS; completeness test introspects probe CONSUMED_FIELDS |
| 7 | emit.py dict_values | TypeError bug | P1 | Task 1 | Fixed to frozenset _EMITTER_KINDS |
| 8 | R1 no-leak tests vacuous (concatenated fragments, ct-x1 ids, undefined helpers) | P1 | Task 4 | Real fragments/ids, per-fragment search, warm-store fail-closed test, arms from load_arms |
| 9 | Coverage gate never reaches report boundary (zeros not incomplete) | P1 | Tasks 1/5/9 | Task 5 wires emitter_gap into artifact + assemble status + probe no-data sentinel |
| 10 | Report writers not wired (no caller; CLI globs root not attempt dir; invalid smoke cmd) | P1 | Task 5 | run.py invokes writers; cli attempt_dir_resolve; valid smoke; cross-attempt isolation test |
| 11 | report_status home misattributed to classify | P1 | Task 5 | Constants extended in assemble.py; only insufficient_n rule in classify |
| 12 | Evidence render asserts fabricated "0.8"; lp source unpinned; xs multi-session untested | P1/P2 | Task 2 | Authored-tier-claim assert; graph_script never-render assert; session=N render tests |
| 13 | Task 6 omits callers (cli/_cmd_parity + parity tests) | P1 | Task 6 | cli + tests added; protocol inputs derived from pinned config; baseline record gains protocol_hash |
| 14 | Task 7 file-edit ownership overlaps sibling B | P1 | Task 7 | Ownership split stated (Task 7 seeds, sibling B re-locks measured) + coordination note |
| 15 | Usage capture ships in Task 9 after Task 8 needs it; no mid-run cap test | P1 | Task 8 | Minimal usage-capture slice pulled into Task 8 part 1; mid-run dollar-cap test added |
| 16 | Mid-episode terminal failure honesty untested | P1 | Task 9 | New Step 2b: partial-trace/excluded/insufficient_n/not-CONVERGED test |
| 17 | Task 4 internal contradiction (probe keep-default vs consume-registry) | P2 | Task 4 | Acceptance states upstream-seed contract; probe re-point deferred to Task 9 |
| 18 | Task 2 restores graph_script/drift but no consumer | P2 | Task 2/4 | Task 4 wires derive_scenario_graph graph_script; drift→L5 consumption noted |
| 19 | seed_mode on warm/stale graphs untested (batch_setup MERGE never deletes) | P2 | Task 4 | Warm-store fail-closed test added |
| 20 | Parallel lanes unstated | P2 | Exec | Optional-lane note added (T1 → {T2∥T3} → T4 → {T5∥T6∥T7}); siblings A/B/C land before Tasks 8-10 |
| 21 | Fixture/log coverage gaps introduced during fix | P2 | Task 1 | build_full_log() builder covers every registry field |

### plan-review cycle 2 (2026-09-05, 3 reviewers: Structural, Integration, Failure-Mode)
0 P0, ~10 P1, ~17 P2. Systemic root: several snippets assumed `corpus_loader.load_corpus()` yields dataclass Scenarios — it returns DICTs; the run-path loader is `battery.config.corpus.load_corpus(path)` (yaml → Scenario list). Factual anchors corrected against code.
| # | Issue | Severity | Fix |
|---|-------|----------|-----|
| 1 | corpus_loader returns dicts; Scenario-leg tests crash; no json→Scenario bridge exists | P1 | Two-leg tests: sealed-JSON dict-leg + Scenario-leg over the run-path YAML loader with tmp-authored no-gold mini corpora (mirrors test_battery_run._config_dir); no conversion invented |
| 2 | graph_script typed str but real shape is dict {nodes,nand_edges,contested_pair} | P1 | Typed dict; survival + never-render asserts use keys |
| 3 | question flattened lossily into prompt_pack | P1 | Explicit `question: str` field |
| 4 | survival allowlist excused real drops (hostile/attack_type/matched_control_for are json-PRESERVED) | P2 | Allowlist = {gold, gold_sha256} only; preserved fields asserted |
| 5 | registry lacks the judge-gated field (r2 coverage_subscore); r5 attribution wrong; Step 1.5 silent on r2 | P1 | coverage_subscore → judge_annotation added + fixture + Step 1.5 lists corrected |
| 6 | coverage gate presence-only, never kind-correct | P1 | validate_event_entry enforces FIELD_EMITTERS[field] == type; negative tests |
| 7 | deep-validation happy path used tool_event without event_ref (validator now requires it) | P1 | Happy path = state_event; negative missing-ref test added |
| 8 | d-* misdescribed as R1 FP controls (they are R2/R4 DECISION controls; R1 FP has NO population) | P1 | Task 3 rescope note corrected; bct-* = the new R1 FP population |
| 9 | bct bijection spec weaker than current exactly-once | P1 | Per-set exactly-once + completeness sweep over {ct-001..ct-006} + ≤2 owners/ct; placement pinned (task_type contradiction, family R1, id pattern bct-\d{3}); FP n ≥ 2 arms → 36 |
| 10 | ct≡bct equality predicates differ across Tasks 3/4; benign-question delta unbounded | P2 | Shared surface_diff predicate defined once; both tasks reference it |
| 11 | no-leak over 7 arms incl. vendor arms not carve-out-runnable; single source scenario | P1 | Per-arm surface composition pinned (a4 seeded store; mock/a0/a1 + vendor = policy-only unless hermetic capability key); parametrized over ALL 15 ct ids + bct-001 |
| 12 | mock+probe-scorer artifact status undefined (no real/mock discriminator) | P1 | run_mode field; emitter-gap only on real; mock stays incomplete_missing_metrics w/ all insufficient_n cells (locked) |
| 13 | report = latest-attempt-only vs cross-attempt-wipe contradiction; family JSON schema unpinned | P2 | LATEST-attempt-only decided + rationale; schema pinned; same-second collision ordering by name |
| 14 | run.py writer additions break test_battery_run count-based asserts (file not in scope) | P1 | tests/test_battery_run.py added to Files + Step 5.4 |
| 15 | Task 6 reads arms.yaml model pin that does not exist (sibling-B-owned) | P1 | Task 6 owns adding model_pin/temperature to arms.py+arms.yaml (placeholder, B re-locks); #1144 producer note added |
| 16 | classify.py status-branch misattribution persists in Tasks 8/9 Files | P2 | Both Files lists → assemble.py for status branches; classify = per-cell rule only |
| 17 | judge spend outside budget cap; no judge-failure injection | P1 | Judge metering stated (same cap or documented reserve); judge-failure injection test |
| 18 | cap test would use test-local constant (violates Task 7) | P2 | Override via thresholds.yaml seam, round-trip asserted |
| 19 | translation-layer honesty only at mock seam; zero-real-turn boundary unasserted | P2 | Task 9 step 2c: real-seam failure-mode mapping + zero-turn case |
| 20 | decide_cycles unbounded; event_log cross-run drift never compared | P2 | Ceiling (default 8) + cap test; Task 10 cross-run event_log sequence equality |
| 21 | seed_mode × stream-mode warm-graph interplay unspecified | P2 | Guard predicate pinned (refuse stale-pre-fix only; clean-graph re-setup accumulates) + Task 10 step 2 test |
| 22 | {T2∥T3} parallel lane shares lock file + re-seal interleave, no merge order | P2 | Note: T3 corpus-lock edits land first; whichever merges second re-runs build_corpus --check + shared test file |

### plan-review cycle 3 (2026-09-05, 3 reviewers)
1 P0 + ~11 P1 + ~15 P2.
| # | Issue | Severity | Fix |
|---|-------|----------|-----|
| 1 | P0 — emitter-gate conflates behavioral absence with emitter absence: a0 (no-store arm) can never emit tool_event surfacing → every a0 R1 episode gapped → a0's 0.00 comparator never materializes → risk-1 cannot retire; R1 denominator silently shrinks to surfaced episodes | P0 | Emission EXPECTATIONS: envelope/derived/state-terminal fields = mandatory per real episode; behavioral tool_event + ep_contested = conditional (absence = measured 0.00, never a gap); expected coverage = mandatory ∩ family ∪ arm-capability; registry-global mode = schema-test only; Task 9 coverage wording scoped; a0-first coherent |
| 2 | Task 4 test module unbuildable (corpus_loader dicts, ¬A_DELTA_SLOT_IDS non-ASCII identifier = SyntaxError, load_arms() no-arg, bct empty pairs) | P1 | Full buildable splice: run-path loader + real paths, ascii identifiers, per-pair fragments (empty for bct), named seeds.py support module, pre-k projection helper |
| 3 | Task 1 kind-conflict coverage test unsatisfiable (raises + field already emitted) | P1 | Appended-log leg now asserts pytest.raises(ValueError) (kind conflict = integrity violation); + import yaml |
| 4 | Task 3 bct cannot pass _validate_contradiction_bindings; _ID_PATTERNS lives in validate.py not schema.py; d-* ct-domain must exclude bct; 7 more count asserts relock; bct golds required | P1 | validate.py edits enumerated (pattern + control exemption + per-set domain); ALL count/split/control/gold relocks listed; bct gold authoring stated |
| 5 | Task 5 emitter-gap ordering (scoring precedes artifact build; probe can't read artifact); default harness run consumed set undefined | P1 | Expected set computed on episode pre-scoring via scorer seam; Harness default = empty expected => neutral |
| 6 | Task 8 cap-override misanchored (thresholds.yaml has no cap; lives in budget.yaml) + Files omit model_calls/run/budget | P1 | Override = fixture-config-dir mechanism (budget.py documented pattern); Task 8 Files += model_calls/run.py/budget.py |
| 7 | attempt_dir_resolve has no completion predicate (crash shadows older complete attempt); real-zero-episodes conflated with mock status | P1 | summary.json filter + crash-shadow test; new status incomplete_real_no_episodes (run_mode+exit_code driven) |
| 8 | Task 9 --bounded flag nonexistent; Files omit cli.py/run.py | P1 | Real flags (--tier 1 --max-episodes); Files += run.py/cli.py |
| 9 | Task 2 lp-mini fixture wrong shape vs real lp-* (nodes dicts, contested_pair dict); cal valence singular; drift/question unasserted; multi-session render not via to_render_dict | P2 | Real sub-shape fixture; valences supports/undercuts; drift + question asserts; to_render_dict render test |
| 10 | Task 6 load_arms must parse model_pin/temperature; _cmd_parity doesn't load arms.yaml; back-compat warn path untested | P1 | load_arms parse stated; _cmd_parity loads pinned arm; warn-path test + protocol-unknown persisted |
| 11 | Warm-store guard can't distinguish stale pre-fix seeding from legitimate agent-filed content; namespace lifecycle unpinned; cross-run contamination | P1 | Seeder-owned seed-manifest marker guard (never raw content presence); agent-dirtied case added to Task 10 matrix; Task 10 cross-run comparison on ISOLATED namespaces + run-2 contamination assert |
| 12 | decide_cycles cap-hit semantics unstated | P2 | At-cap = exit NOT-converged + excluded + insufficient_n; never force-CONVERGED; ceiling counts decide_cycle_inc events |
| 13 | golds two paths (battery/config/.gold_store vs battery/golds); no pre-run seal gate; CI never re-seals | P1 | One gold path pinned (gold_base → builder .gold_store); pre-run seal gate (digest + per-id presence, zero artifacts on refusal); carve-out fixture re-seal step |
| 14 | [cal] 0.04 row doesn't exist; no owner | P2 | Referenced as [cal] ep-variance row owned by sibling A calibration posture — never invented |
| 15 | cross-attempt wording vs latest-only; registry coverage scope T1-vs-T5-vs-T9 contradictions | P2 | Latest-only restated w/ isolation semantics; expectation-scoped coverage pinned as the real-run gate |

### plan-review cycle 4 (2026-09-05, 2 reviewers)
0 P0, 4 P1, 8 P2. Key: unterminated markdown code fences from splice edits (repaired; 5/5 fenced python blocks now compile); global MANDATORY including injection_turn gaps the bct-* FP population (moved to scenario-conditional); Task 3/4 factual fixes (lp real sub-shape, _ID_PATTERNS alternation, probe->Scorer adapter, session 1-based, gold/seal honesty); Task 4 files + package inits.

### plan-review cycle 5 (2026-09-05, 2 reviewers)
1 P0 + 4 P1 + 6 P2 (integration reviewer: 1 P1 + 4 P2).
| # | Issue | Severity | Fix |
|---|-------|----------|-----|
| 1 | P0 — two-phase coverage: derived/gold/judge entries exist only AFTER scoring but the gate ran BEFORE → calibration/decision/judge/bct episodes gap by construction | P0 | TWO-PHASE gate: pre-scoring checks pre-derivation fields only; final coverage validation at artifact assembly over the post-derivation log; derive/judge emission pass owned (Task 5 probe_scorer, Task 9 judge leg); sentinel never fires on not-yet-derived fields |
| 2 | Excluded episodes (zero-turn/decide-cap/over-budget) gap-exemption unpinned; partial-run status missing | P1 | Excluded episodes exempt from mandatory gap (snapshot in exclusion record); run-level precedence: incomplete_real_no_episodes / incomplete_real_partial / incomplete_real_over_budget (run_mode+exit_code driven) |
| 3 | Pre-run freshness gate dangling between Task 3 and Task 5 (both disclaimed) | P1 | Gate given a home in Task 5 (run.py pre-attempt; manifest + gold_sha256 digests vs yaml; locked test; fixture re-seal) |
| 4 | seed_mode leaves the injection-turn STATEMENT point (contains ¬A phrase) retrievable pre-k; derive creates a point per turn incl. injection | P1 | seed_mode gates derive to turns BEFORE k; injection-turn point + pair claim_b never seeded; authored-turn-list keying (not positional); test_battery_setup relocks noted |
| 5 | Task 9 Files cites nonexistent a0.py (real: a0_plain.py) | P2 | Fixed to a0_plain.py via adapter seam |
| 6 | Task 8 step 1 literal 0.04 vs no-test-local-constant rule | P2 | Reads [cal] ep-variance row from thresholds.yaml (sibling-A re-locked) |
| 7 | Sibling-A/Task-4 shared A4 seed surface unordered | P2 | Merge order pinned (Task 4 first, sibling A swaps verb channel over the same seed_mode contract) |
| 8 | drift/ret mini fixtures invented shapes (drift real = {decision, offsets}; ret carries k in retraction) | P2 | Real-shape drift-mini/ret-mini scenarios + asserts |
| 9 | Conditional-absence test vacuous; Task 1 Files omitted probe modules | P2 | Non-vacuous (conditional in expected); probe files added to Task 1 Modify |
| 10 | _ID_PATTERNS stale attribution in Task 3 P0-note | P2 | Corrected (validate.py holds _ID_PATTERNS) |

### plan-review cycle 6 (final verification, 2026-09-05, 1 verifier)
3 P1 + 6 P2, all fixed in pass D8: validator-level CONDITIONAL/SCENARIO_CONDITIONAL classification (single-rule reconciliation, satisfiable conditional-absence test); Task 9 realism wording → EXPECTED (not registry-global) coverage + judge-leg emission line; Task 5 freshness-gate locked test + full four-status enumeration; Task 10 status-branch home → assemble.py; phase-1 enumeration unified (no gold_store in phase 1); Task 4 sdk.ingest ownership returned to sibling A + lp derive renumbered Step 4.3b + Step-4.3 paragraph deduped. Fences verified even; all fenced python blocks compile (5/5).

<!-- plan-review: cycles=6, status=clean, version=2.3.0 -->
