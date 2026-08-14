---
title: "#1272 — Calibrate the Shipped Production Extractor: Implementation Plan"
type: log
domain: capability
subjects:
  team: epistemic-team
ownedBy: epistemic-team
doc_status: live
created: 2026-08-14
governingAgreement: "#909, #946, #1272"
---

# #1272 Implementation Plan — Calibrate the Shipped Production Extractor

**Goal:** Make the shipped production extractor (PR #1263 + #1278) honest and committable (Phase A — deterministic repairs), then calibrate it to the owner's rulings on real windows (Phase B — owner-gated).

**Team:** epistemic-team · **Role:** product-implementer

**Architecture:** Phase A repairs the deterministic layer (Layer-1 Event endpoints, chunked-merge summary aggregation, prefix/dedup, enforcer retry, fabricated-telemetry honesty, closed-vocab enforcement, adapter bounds) — unit-testable with mocked models, no owner gate, ships on green. Phase B calibrates the value filter (ADD the missing R1∧R3 discriminator, map owner rulings to production schema, ≥3× runs + regression set per criteria v1 §4) — owner-gated, blocked on Phase A's committable path + window versioning.

### Pattern Research
> **Findings date:** 2026-08-14 · **Gate skipped:** justified-skip — all evidence in-repo; loop protocol in criteria v1 §4; owner rulings verbatim; validation findings code-verified across 5 plan-verify rounds. A1b resolved by owner: events MAY connect to points (ONTOLOGY L120/L148, amended to declare Point→Event).

### Integration Surface Map
| Surface | Layer | Notes |
|---|---|---|
| commit_schema.py | unit | Point requires reason/confidence/c_cal; operator endpoints points-only → ∪ events (T1); atomicity L432-458 (T5); canonical excludes confidence/c_cal/status (replay-safe) |
| hosted_api.py | unit+integration | _execute_commit_writes writes client confidence (T11); _store_commit_telemetry verbatim (T11); create_operator + _load_commit_graph_state Point-only (T2, 6 sites) |
| value_extractor.py | unit | summarize drops session.summary (T7); ch-prefix (T8); silent chunk loss (T9); R4 no repair (T10); no closed-vocab (T12); no R1∧R3 (T16) |
| sdk.py | unit | mappers fabricate (T11); client_commit_id empty (T5); adapter unbounded (T13) |
| tests | unit+round-trip | 11 value_extractor tests, none touch summarize/commit_session/_stream_to_payload (T6/T7 add) |
| ONTOLOGY.md | docs | Event→Point + Point→Point declared; Point→Event added (T1) |
| windows (session-events) | data | reflect-hook truncates 5000 (both paths); w3 + scoping sub-agent sessions = Phase B windows |
| pyproject/fastmcp | infra | duplicate fixed on origin/main (#1279) — verified |

### Verification Plan
- Phase A: unit tests (mocked models) per fix + T6 round-trip guard + T14 smoke. Exit: suite green + round-trip passes on real construct stream.
- Phase B: owner review of summary+graph output on versioned windows; ≥3× runs; regression set; dual-gate per criteria v1 §4.

**Tech Stack:** Python 3.11+, OpenRouter adapter, commit_schema Layer-1, hosted_api commit path.

---

### Task 1: Layer-1 accepts Event operator endpoints (A1b core)
**Intent:** Make the construct path committable — operators may connect Events and Points (owner ruling: "events should be allowed to connect to points").
**Acceptance:** validate_layer1 accepts op.src/dst ∈ emitted_point_ids ∪ emitted_event_ids (symmetric); construct-shaped payload with event-target operators passes; ONTOLOGY §2/§3.1/§3.8/§8 amended to declare Point→Event with inline write-only caveat.
**Files:** Modify: tortoise/commit_schema.py:577-582, docs/ONTOLOGY.md · Test: tests/test_commit_schema.py
**Step 1:** emitted_ids = emitted_point_ids | emitted_event_ids for the operator endpoint check (symmetric, all op types).
**Step 2:** Amend ONTOLOGY §2/§3.1/§3.8/§8: "Operators connect epistemic targets (Event→Point, Point→Event, Point→Point)" with INLINE caveat: "Point→Event operators are recorded argumentation annotations — write-only in v1, no EP propagation; decision semantics remain on the Event timeline; decisions stay non-first-class Points."
**Step 3:** Fixture + test (both directions + MITIGATES targeting event-endpoint edge); run.
**Step 4:** Note: EP reads Point→Point only — event-endpoint operators write-only for v1; construct points land draft + promote_source=False → EP-inert until #785; state confidence reads neutral.

### Task 2: Write path :Event resolution (A1b write half)
**Intent:** No silent drop of event-target operators; replay path idempotent.
**Acceptance:** create_operator + _execute_commit_writes + _load_commit_graph_state resolve :Event endpoints (6 sites); replay-simulation test asserts no duplicate.
**Files:** Modify: tortoise/sdk.py (create_operator L2813), tortoise/hosted_api.py (_execute_commit_writes + _load_commit_graph_state) · Test: tests/test_commit_endpoint.py
**Step 1:** Enumerate SIX Point-only sites: (1) create_operator existence MATCH (n:Point), (2) edge-create MATCH (o:Point),(s:Point), (3) promote clause (no-op for Event src), (4) MITIGATES fallback lookup, (5) _load_commit_graph_state operator read -[r]->(t:Point), (6) MITIGATES reconstruction (s:Point). Extend (1)/(2)/(4)/(5)/(6) to :Event.
**Step 2:** Note operator node stays :Point{is_operator:true} with (o)-[:IMPL]->(ev); EP consumers won't match — write-only-for-v1.
**Step 3:** Tests: (a) event-target operator written, edge exists; (b) REPLAY: commit event-endpoint op → re-run _load_commit_graph_state + plan_commit → reconciles merge (no duplicate, no budget re-count); run.

### Task 3: Fix MITIGATES JSON shape (A1b shape half)
**Intent:** CONSTRUCT_SYSTEM's documented shape must match the schema.
**Acceptance:** CONSTRUCT_SYSTEM emits MITIGATES with target (not target_edge) + required dst; construct stream validates.
**Files:** Modify: tortoise/value_extractor.py (CONSTRUCT_SYSTEM ~L155-177) · Test: tests/test_value_extractor.py
**Step 1:** Read the schema's MITIGATES operator shape (Operator model: target, dst required, strength [0.10,0.50]); correct CONSTRUCT_SYSTEM's documented JSON.
**Step 2:** Fixture asserting the corrected shape validates; run.

### Task 4: Construct enrichment + canonical id re-derivation + operator remap (A1a)
**Intent:** Stream points must carry required fields; ids content-derived (fake ids break MERGE).
**Acceptance:** _stream_to_payload enriches reason/confidence/c_cal + re-derives pt_<sha>/ev_<sha>; operators + MITIGATES targets remapped; payload validates.
**Files:** Modify: tortoise/sdk.py (_stream_to_payload) · Test: tests/test_value_extractor.py
**Step 1 (LOAD-BEARING):** LLM-id → re-derived-id map FIRST; rewrite operators[].src/dst + MITIGATES target{src,dst} to re-derived ids (else referential integrity fails). Shared-content collision → same node.
**Step 2:** Canonical excludes confidence/c_cal/status — replay-safe; assert MERGE-key stability + canonical-hash invariance under enrichment.
**Step 3:** confidence/c_cal = T11 neutral prior (0.5/0.5) — do NOT derive from why/for/against (seeds directional Beta priors); any mapping → Phase B. Set status explicitly (draft).
**Step 4:** Fixture + test; run.

### Task 5: Atomicity + client_commit_id (A1c)
**Intent:** Construct points don't trip atomicity; mapper produces valid client_commit_id.
**Acceptance:** Construct point content passes atomicity (documented statement-kind carve-out) OR a bounded model-repair retry; client_commit_id computed in mapper.
**Files:** Modify: tortoise/sdk.py (mappers), tortoise/commit_schema.py (atomicity carve-out doc) · Test: tests/test_value_extractor.py
**Step 1:** Carve-out scoped to pointKind=="statement" (the extraction-only kind; commissive check already doesn't apply — trips come from coordination cues/≥2 commas); document in commit_schema atomicity docstring. Escalate broader carve-out to owner.
**Step 2:** Compute client_commit_id in mappers (or route round-trip through _post_commit); test.

### Task 6: Round-trip guard test (A1d)
**Intent:** The load-bearing guard.
**Acceptance:** commit_session (mock) → _summary_to_payload(stream=...) → validate_payload_dict → ok=True.
**Files:** Test: tests/test_value_extractor.py
**Step 1:** Mock model returns a construct stream; run commit_session; assert validate_payload_dict ok.
**Step 2:** If fails, fix the mapper/schema gap (T1-T5); do not weaken the test.

### Task 7: Chunked summarize() aggregates session.summary (A2)
**Intent:** >6-EDU sessions commit empty summary.
**Acceptance:** summarize() aggregates per-chunk session blocks (concatenate-with-cap at [:2000]); merges session.type; summarize() test exists.
**Files:** Modify: tortoise/value_extractor.py (merge loop ~L263-274) · Test: tests/test_value_extractor.py
**Step 1:** Merge part["session"] — concatenate summaries capped at 2000 (first-wins discards chunks); merge session.type.
**Step 2:** summarize() test (chunked fixture, non-empty summary + type); run.

### Task 8: Drop ch-prefix + cross-chunk dedup (A3)
**Intent:** Prefixed names poison (name,kind) MERGE permanently.
**Acceptance:** No ch{n}- prefix in names; (name,kind) dedup in merge.
**Files:** Modify: tortoise/value_extractor.py (~L268-271) · Test: tests/test_value_extractor.py
**Step 1:** Remove prefix instruction + application; seen: set[(name,kind)] in merge loop, skip dupes.
**Step 2:** Test (chunked fixture, no prefixed names, no dup (name,kind)); run.

### Task 9: Chunk-loss telemetry (A4)
**Intent:** Failed chunk silently vanishes.
**Acceptance:** failed_chunks counter in result/telemetry.
**Files:** Modify: tortoise/value_extractor.py (summarize loop) · Test: tests/test_value_extractor.py
**Step 1:** Add failed_chunks counter; increment on `if not part`; include in result.
**Step 2:** Test (mock fails one chunk → counter reflects); run. Also: construct_graph silent-degradation (returns empty after 3 fails) → flag.

### Task 10: R4 correction pass (A5)
**Intent:** One R4 violation kills the session — no repair.
**Acceptance:** Bounded repair loop (≤1 retry/item, ≤5/session) feeds validator errors back; or documented two-process internal handling.
**Files:** Modify: tortoise/value_extractor.py, possibly sdk.py · Test: tests/test_value_extractor.py
**Step 1:** Read tools/calibration_harness.py CORRECT_PASS/run_iterative; port a bounded version into extract_session/summarize (BEFORE commit_session's `if errors:` gate); adapt prompt to production schema (decisions/state/logic/issues).
**Step 2:** Implement + test (mock: first pass missing sources, repair fixes, second clean).

### Task 11: Fabricated graph inputs — honest values (A6, fast-track)
**Intent:** Hardcoded confidence/telemetry written into the graph — belief engine consumes invented weights.
**Acceptance:** Both payload builders emit neutral prior 0.5/0.5 + calibration_version persisted to TelemetryExtractor; status draft BOTH paths; passes_frequency_gate inert True (recorded); provenance real-or-empty; server derives keep_ratio/histogram from reconciled delta; client keeps process fields.
**Files:** Modify: tortoise/sdk.py (mappers), tortoise/hosted_api.py (_store_commit_telemetry), tortoise/commit_schema.py (TelemetryExtractor calibration_version) · Test: tests/test_commit_endpoint.py, tests/test_value_extractor.py
**Step 1:** Mappers: confidence/c_cal = 0.5/0.5 (both paths, events 0.9→0.5, points 0.8/0.7→0.5); status:"draft" in LOOSE mapper too (P1-1 — canonical excludes status, replay-safe); calibration_version:"v1" → TelemetryExtractor field (extra="forbid" needs model change); passes_frequency_gate inert True until S5; provenance real-or-empty.
**Step 2:** hosted_api _store_commit_telemetry: pass plan/reconcile; derive keep_ratio/histogram from reconciled delta; ignore client graph-truth (replay-safe: telemetry excluded from canonical). NOT calibration_mismatch (vocab code).
**Step 3:** Tests: graph receives neutral prior (not fabricated); telemetry real counts.

### Task 12: Closed-vocab enforcement (A7)
**Intent:** Non-registered objectKinds write through as :Object kinds.
**Acceptance:** validate_summary gains mode: Literal["fail-closed","warn"]="fail-closed"; aligned 16-kind core (ONTOLOGY §5 incl. commitment-state); fail-closed production / warn proposal-capture Phase B; case + bare/namespaced normalization; missing objectKind → error; 2 broken tests updated.
**Files:** Modify: tortoise/value_extractor.py (compile_value_brief + validate_summary), tortoise/sdk.py (commit_session mode threading) · Test: tests/test_value_extractor.py
**Step 1:** ALIGN compile_value_brief core to ONTOLOGY §5 16 kinds (incl. strategy/plan/goal/target; drop concept; add project/tag/user/skill/agent/agreement); note §4.3/§5/§6 drift. Case normalization (Project vs project); bare vs namespaced (document vs core:document; namespace collision dev:issue vs pm:issue → resolve rule). Missing objectKind → fail-closed reject (mapper default core:concept → core:other).
**Step 2:** mode param threaded through extract_session/commit_session; fail-closed production / warn proposal-capture (Phase B w3 has non-vocab kinds — warn so T17 reachable; criteria v1 §2.2.6); calibration_harness.py OUT OF SCOPE (own validate_stream/VOCAB/CORRECT_PASS — separate follow-up); Layer-1 has NO entity-kind check (extractor-path gate only — endpoint = general Layer-1 API, deliberate scope). Cache the brief.
**Step 3:** Tests: non-vocab → error (fail-closed) / proposal note (warn); EVERY canonical core kind bare+namespaced → clean; UPDATE test_compiles_kinds (core:concept) + test_clean_passes (core:concept); missing objectKind → error.

### Task 13: Production adapter bounds (A8)
**Intent:** Production _model_adapter unbounded — the gate-collapsing model runs unbounded.
**Acceptance:** _model_adapter mirrors bounds (temperature 0.0, per-model max_tokens 2000-8000 — NOT the 500 judge floor); unit test asserts request body.
**Files:** Modify: tortoise/sdk.py (_model_adapter) · Test: tests/test_sdk.py or test_value_extractor.py
**Step 1:** Add max_tokens/temperature to request body; per-model sizing for summary+construct.
**Step 2:** Test (mock requests → assert body bounds).

### Task 14: End-to-end smoke (A9, Phase A exit)
**Intent:** Green = system-green.
**Acceptance:** commit_session (mock) → payload → validate_payload_dict ok → endpoint write path executes (monkeypatch requests.post OR FastAPI TestClient with construct-shaped payload).
**Files:** Test: tests/test_commit_session_smoke.py
**Step 1:** Smoke: mock model → commit_session → validate → (optional) real endpoint.
**Step 2:** Phase A exit gate.

---

## PHASE B (owner-gated — after Phase A ships)

### Task 15: Version the Phase B windows
**Intent:** Calibration needs versioned, truncation-verified windows.
**Acceptance:** w3 (38-EDU design capture) + a scoping sub-agent session (019fffd0/…/019fffdf, decision-rich) versioned into tests/eval/w-1272/; each <5000 chars/turn; 019ffdbb (VGATE) excluded.
**Files:** Create: tests/eval/w-1272/w3-transcript.txt, tests/eval/w-1272/w-scope-transcript.txt
**Step 1:** Rebuild via tools/build_window_transcript.py from session-events; verify truncation.
**Step 2:** Version into tests/eval/w-1272/.

### Task 16: ADD the R1∧R3 decision discriminator to SUMMARY_SYSTEM (B2 first cell)
**Intent:** The production prompt has NO decision gate — the owner's decision=VALUABLE ruling can't be expressed.
**Acceptance:** SUMMARY_SYSTEM gains R1∧R3 conjunction (commissive ∧ product-knowledge-bearing; "should"=recommendation exclusion; decision cue list); fixture asserts prompt content + mock fails if discriminator absent.
**Files:** Modify: tortoise/value_extractor.py (SUMMARY_SYSTEM) · Test: tests/test_value_extractor.py
**Step 1:** Draft R1∧R3 from spec-classification-model.md §1/§2; add to SUMMARY_SYSTEM.
**Step 2:** Fixture: assert R1∧R3 text present in prompt (content assertion, not behavioral); mock fails if absent.

### Task 17: Calibrate the value filter (B2/B3 — owner loop)
**Intent:** The ~40 state items/session bar, claim downgrade, event→ingestion, narration→discard — applied + measured per protocol v2.
**Acceptance:** ≥3× runs per candidate on versioned windows; regression set (w2-960 + w3) rerun; owner reviews summary+graph output; dual-gate (no new error categories incl. proposal/minted set).
**Files:** Modify: tortoise/value_extractor.py (prompts) · Test: tests/eval/w-1272/
**Step 1:** Apply value-filter amendments (single-change) to SUMMARY_SYSTEM.
**Step 2:** Phase B runner — extract_session(model, conv, mode="warn") on each versioned window; capture proposals; ≥3× per window; record variance; regression rerun. Dual-gate "no new error categories" INCLUDES proposal/minted set.
**Step 3:** Owner review of summary+graph output; corrections feed next single change.
**Step 4:** Dual-gate check; post result to epic #909.
