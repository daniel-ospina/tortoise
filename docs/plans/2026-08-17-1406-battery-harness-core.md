---
title: "Implementation Plan — Battery Harness Core (#1406)"
type: decisions
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-17
aboutSubjects: tortoise
---

<!-- research-path: docs/plans/2026-08-17-1406-scope.md -->

# Battery Harness Core Implementation Plan (#1406)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Build the battery harness core — episode runner (trajectory logging, seed pinning, model-call outcome tracking, batch scenario setup), CLI (`run|parity|calibrate|validate-judge|report` + exit codes 0/1/2/3/4/5), and `battery/config/` YAML loaders — extending `tools/longmem_eval` patterns, contract-first for child issues.

**Team:** epistemic-team
**Role:** implementer
**Issue:** #1406 (epic #1402)

**Architecture:** `battery/` package tree per epic plan §3 (prototype), with only #1406-owned sub-packages implemented: `cli.py`, `config/`, `runner/`, `arms/base.py` (protocol only). `parity`/`calibrate`/`validate-judge`/`report` subcommands register + parse their plan-§6 flags but dispatch to stubs (exit 1 "not implemented — owned by #N"). Contracts (ArmAdapter protocol, ModelCallOutcome/EpOutcome/ExitCode/Tier enums, run_artifact/summary schemas, contract exceptions) ship here and are referenced by constant/enum name — child issues implement to them.

### Pattern Research

> **Findings date:** 2026-08-17
> **Gate skipped:** the plan introduces ZERO new third-party dependencies — pyyaml (already a repo dependency, used by `config/`-adjacent code + requirements.txt), argparse (stdlib), the in-repo Tortoise SDK + redislite (both already used by `tools/longmem_eval` and the test suite). Per writing-plans workflow/02 Sub-step B skip rule ("in-repo wrapper exclusively / zero third-party deps"), no multi-call Perplexity gate fires. External best-practice findings are inherited from the scoping artifact's `### Axis Research` (Inspect AI task/solver/scorer + episode transcripts; Anthropic transcript-as-ground-truth + trial isolation; TraceCore seeded-determinism spec; SWE-bench per-instance artifacts; Polarity temp-0/pitfalls) and are re-applied here at the concrete level:

**Library version & API surface** — skipped (zero new deps; pyyaml 6.0.3 already pinned).

**Idiomatic usage patterns** — skipped (in-repo precedent: `tools/longmem_eval/run.py` per-question error isolation + report provenance; `tools/longmem_eval/report.py` COMPLETED-only aggregation; `judge_harness.py`/`kappa.py` exit-code convention 0/1/2; `tortoise.sdk.TortoiseSDK(db_path=...)` embedded pattern in tests; `sys.path.insert` import pattern in tests).

**Library/framework pitfalls** — skipped (deps used identically elsewhere in the codebase; the known pitfalls are pinned in the scope: SDK URI-first DB precedence, in-function `compute_embedding` import, `_probe_embedded_busy` daemon hazard, create_operator endpoint validation + ULID non-injectability, NAND→unidirectional direction canonicalization).

### Integration Surface Map

From test-design #1404 (integration-surface map) + issue #1406 verification checklist:

| Surface | Type | Test Layer | Coverage in this slice |
|---|---|---|---|
| S1 Tortoise SDK write path | DB (FalkorDBLite, embedded) | Integration (hermetic) | Batch setup ≤2 round-trips/scenario at the `g.query` boundary (RoundTripCounter); naive baseline; batch==naive graph-state equivalence (content_hash key + node props + negative-path validation parity); idempotent setup |
| S3 Agent LLM runtime | External service | Integration (mock caller in CI) | Model-call outcome enum {ok, rate_limited, timeout, fallback_cached, failed} recorded per call; episode_trace records real calls (turns/tool_calls/tokens); temp-0 + seed recorded; deterministic failure-injection double seeded by episode seed |
| S7 Run artifacts | Filesystem | Unit (schema) + integration (re-run reproducibility) | run_artifact.json schema v1.0 + summary.json schema v1.0 (schema tests); run_id = seed+arm+scenario random-free; E2E-7.1 determinism (two subprocesses, |Δ|≤1e-6) |
| S8 Harness config | Config | Unit | corpus/thresholds/arms/budget YAML schema; [cal] table lock + cal_table_hash; gold sha256 verify at load; empty-corpus guard in loader (exit 5 at dispatch); budget guard (exit 1) |

**Bug Pattern Flags (owned here):**
- Silent function skips: model-call outcomes per call; episodes with any terminal non-ok outcome excluded from aggregates + counted (never silent).
- N+1 queries: `--batch-setup` → 2 round-trips/scenario (2·N total) vs naive 4+/item.
- Conditional guards: empty-corpus (exit 5) + budget (exit 1) both sides tested.

### Verification Plan

test-routing: domain=code, Architecture=standard, UX=low, Ontology=low → unit + integration (embedded, hermetic) + 1 E2E-style determinism test. All tests run under `uv run pytest tests/test_battery_*.py -v` with FalkorDBLite embedded (no Docker, no API keys — mock caller). Subprocess determinism test pins `PYTHONHASHSEED=0`, `TORTOISE_DB_URI=""`, `TORTOISE_DB_PATH=<tmpdir>`.

**Tech Stack:** Python 3.12, stdlib argparse, pyyaml, tortoise SDK, pytest, FalkorDBLite (embedded).

---

## Task 1: Package skeleton + enums + exceptions

**Intent:** Create the `battery/` package per epic prototype §3 with empty skeleton sub-packages (so #1408–#1415 imports resolve without merge collisions) and the constant-by-name contract surface (enums + contract exceptions) every child issue references.

**Acceptance:** `battery/` imports cleanly; `Tier`, `ExitCode`, `EpOutcome`, `ModelCallOutcome` enums + contract exceptions exist with exact values; skeleton packages (arms, probes, streams, differential, judge, recall, parity, report) import; `.gitignore` gains `battery/golds/sealed/*`.

**Files:**
- Create: `battery/__init__.py`, `battery/enums.py`, `battery/exceptions.py`, `battery/arms/__init__.py`, `battery/probes/__init__.py`, `battery/streams/__init__.py`, `battery/differential/__init__.py`, `battery/judge/__init__.py`, `battery/recall/__init__.py`, `battery/parity/__init__.py`, `battery/report/__init__.py`, `battery/golds/README.md`, `tests/test_battery_enums.py`
- Modify: `.gitignore`

**Step 1:** Write `tests/test_battery_enums.py` asserting enum membership + exit-code mapping + tier mapping (1→probe, 2→stream, 3→differential) and exception hierarchy (`ConfigError` + `GoldVerificationError` subclasses exist — loader failures map to exit 1 at dispatch).

**Step 2:** Run `uv run pytest tests/test_battery_enums.py -v` → FAIL (no battery module).

**Step 3:** Implement `battery/enums.py` (Enum base classes: `Tier`, `ExitCode`, `EpOutcome`, `ModelCallOutcome`), `battery/exceptions.py` (EmptyCorpus, JudgeGateBlocked, InconclusiveRun, ScoreUnavailable, **ConfigError, GoldVerificationError, IsolationBreach** — battery domain errors; **EmptyCorpus is NOT a ConfigError subclass and the dispatcher catches EmptyCorpus BEFORE ConfigError so exit 5 is never masked into exit 1**), skeleton `__init__.py` files, `battery/__init__.py`, `.gitignore` entry `battery/golds/sealed/*`, `battery/golds/README.md`. **Also register the package: extend `pyproject.toml` `[tool.setuptools.packages.find]` include to `["tortoise*", "benchmarks*", "battery*"]`** (verified: current include omits battery → CI/wheel installs would silently drop it; verify `pip install -e .` makes `python -m battery` importable).

**Step 4:** Run tests → PASS.

## Task 2: config/ package — loaders + schema + defaults

**Intent:** YAML loaders for corpus/thresholds/arms/budget with the pinned schemas; gold verification; empty-corpus guard; [cal] hash; budget estimate.

**Acceptance:** loaders validate schemas (type-only), verify gold sha256 (mismatch/missing → exit-1 class error), raise `EmptyCorpus` on zero scenarios; cal_table_hash stable; budget estimate formula applied; smoke corpus (2 scenarios, committed fixtures) loads.

**Files:**
- Create: `battery/config/__init__.py`, `battery/config/corpus.py`, `battery/config/thresholds.py`, `battery/config/arms.py`, `battery/config/budget.py`, `battery/config/corpus.yaml`, `battery/config/thresholds.yaml`, `battery/config/arms.yaml`, `battery/config/budget.yaml`, `battery/golds/fixtures/gold-r1-001.txt`, `battery/golds/fixtures/gold-r1-002.txt`, `tests/test_battery_config.py`

**Step 1:** Write `tests/test_battery_config.py` — scenario schema (valid/missing-field/type-violation), tier enum values, split enum, contradiction_pairs/evidence_scripts optional, gold verify (present-ok / sha-mismatch / missing-file → `GoldVerificationError`), EmptyCorpus on empty list, cal hash canonical stability (same rows → same hash; different order → same hash), budget estimate formula + over-budget flag.

**Step 2:** Run → FAIL.

**Step 3:** Implement loaders: `Scenario` dataclass (full plan §4 model: id, tier, family, task_type, attack_type, split, prompt_pack, gold_ref, k, contradiction_pairs, evidence_scripts) with `golds()` scoring-only surface; `load_corpus(path, gold_base)` → list[Scenario], raising `EmptyCorpus` on 0 and verifying gold sha256; `ThresholdsConfig` (determinism.epsilon + cal rows, `cal_table_hash()` canonical serialization); `ArmsConfig` (per-arm price_per_1k_usd, expected_tokens_per_episode); `BudgetConfig` (`estimate_cost(scenarios, arms)` = Σ scenarios × tokens × price; `over_budget()`).

**Step 4:** Create default YAMLs (smoke corpus referencing committed fixtures; thresholds with determinism.epsilon 1e-6 + cal: {}; arms a0/mock + a4/tortoise; budget caps). Fixture gold files.

**Step 5:** Run tests → PASS.

## Task 3: arms/ — ArmAdapter protocol + mock arm

**Intent:** The sealed-adapter contract (#1408 implements real arms to it) + a deterministic mock arm for this slice's tests.

**Acceptance:** Protocol conformance test passes; mock arm emits seed-derived trajectory (≥1 turn, deterministic tool_calls/tokens/re_derivations); failure-injection double seeded by episode seed produces the exact outcome schedule.

**Files:**
- Create: `battery/arms/base.py`, `battery/arms/mock.py`, `tests/test_battery_arms.py`

**Step 1:** Write tests — Memory/AgentContext dataclass fields; ArmAdapter protocol signature (retrieve/record/setup_scenarios/isolation_namespace); ArmUnavailable; mock arm determinism (same seed → same trajectory; different seed → different); injection policy schedule deterministic + outcome ∈ enum; **golds-absent-from-episode-context: run a mock episode and assert the episode/agent context contains no gold text and `Scenario.golds()` is the only access surface (scope DD2/AC6 pin).**

**Step 2:** Run → FAIL.

**Step 3:** Implement `base.py` (Memory, AgentContext dataclasses; ArmAdapter Protocol; ArmUnavailable) and `mock.py` (MockArm — seeded RNG trajectory generator; `InjectionPolicy` — per-call outcome from seeded schedule).

**Step 4:** Run tests → PASS.

## Task 4: runner/ — episode executor + trajectory + model-call outcomes + scorers

**Intent:** The episode execution core: run a scenario × arm, record the trajectory (turns/tool_calls/tokens/per-call outcomes), apply the retry table, classify episode outcome, and compute emission metrics via the Scorer seam.

**Acceptance:** EpisodeResult carries full trajectory; model-call outcome recorded per call with retry semantics (≤2 rate_limited / ≤1 timeout / no retry failed+fallback_cached); HarnessScorer emits the pinned metric set {n_turns, n_tool_calls, total_tokens, re_derivations} + outcome counts; aggregation excludes episodes with any terminal non-ok outcome and counts them.

**Files:**
- Create: `battery/runner/__init__.py`, `battery/runner/episode.py`, `battery/runner/model_calls.py`, `battery/runner/scorers.py`, `battery/runner/aggregate.py`, `tests/test_battery_runner.py`

**Step 1:** Write tests — outcome enum recorded per call; retry table (rate_limited retries ≤2 then terminal; timeout ≤1; failed no retry) with an **injectable backoff seam (tests pass a zero/nulled sleeper — no real sleeps in CI)**; episode classification (any terminal non-ok → excluded, counted); **ArmUnavailable raised inside retrieve/record → runner serves deterministic cached response recorded `fallback_cached` (cache exists) or `failed` (no cache), never raises through, never silent, episode excluded + counted (scope DD8)**; HarnessScorer metric set exact {n_turns, n_tool_calls, total_tokens, re_derivations} + outcome counts; **aggregation excludes ANY terminal non-ok outcome (fallback_cached/failed/rate_limited/timeout after retries) and reports the count (AC6, DD8)**; EpisodeResult fields populated; **multi-scorer merge: two scorers returning the same metric_id → hard error at load (scope DD3)**.

**Step 2:** Run → FAIL.

**Step 3:** Implement: `episode.py` (EpisodeResult dataclass, EpisodeTracker accumulating turns), `model_calls.py` (ModelCaller protocol + outcome-recording wrapper + retry table with `backoff_fn`/`sleep` injectable for tests + fallback-cache semantics), `scorers.py` (Scorer Protocol, ScorerResult{Metrics, ep_outcome}, MetricValue, HarnessScorer, deterministic stub scorer, multi-scorer merge → duplicate metric_id error), `aggregate.py` (exclusion + counts).

**Step 4:** Run tests → PASS.

## Task 5: runner/setup.py — RoundTripCounter + batcher + equivalence

**Intent:** The `--batch-setup` N+1 fix: batch scenario graph writes to ≤2 DB round-trips per scenario at the query boundary, with batch==naive equivalence.

**Acceptance:** RoundTripCounter wraps `FalkorProjection.g.query`; batch path = 2 UNWIND queries/scenario (points + operators) with endpoint MATCH guards (SDK ValueError parity), idempotent guarded CREATE, call-time `compute_embedding` import; equivalence test (monkeypatched deterministic embedding stub) shows identical graph state (content_hash key, node props incl. status/promote_source/direction/label/embedding); naive baseline = SDK path with deterministic ids; negative-path parity (missing endpoint fails identically); scale test ≥50 scenarios.

**Files:**
- Create: `battery/runner/setup.py`, `tests/test_battery_setup.py`

**Step 1:** Write tests — counter increments per g.query; batch path ≤2 queries/scenario for a 10-item scenario; equivalence naive==batch (points keyed by content_hash, operators keyed by (op_type,source,targets), node props compared: content_hash, status, direction, label, embedding — **timestamps explicitly excluded: both paths write real call-time createdAt/updatedAt which never match at microsecond resolution; the comparison never looks at them; the compared-prop set is a documented prop-SUBSET of the SDK's (event-emission divergence + is_operator handled as documented divergences, not compared)**); **NAND direction parity: the naive path calls `create_operator(op_type, src, targets, direction=None)` — the SDK's `_canonical_direction` fires ONLY on explicit None (default kwarg is "bidirectional"; verified sdk.py:4024) — so BOTH paths store `unidirectional` for NAND (never "fix" the batch to bidirectional)**; negative path (missing endpoint fails identically on both paths); idempotency (batch_setup twice → node + edge counts unchanged, no duplicate points/operators — **BATCH path only: `create_operator` has no dedup/deterministic id (fresh ulid per call, verified sdk.py:3266–3308), so the NAIVE path is idempotent for points only (dedup=True) and NOT for operators — scope the assertion to batch, never claim both**); scale ≥50 scenarios (batch = 2·N ≤ cap, naive ≫); **tagged real-model embedding test `@pytest.mark.slow` (skip when `compute_embedding` returns None — no model installed): batch path with the REAL embedding fn produces non-None embeddings on both paths (scope DD5 pin)**.

**Step 2:** Run → FAIL.

**Step 3:** Implement `setup.py`: `RoundTripCounter` proxy; **scenario→entity derivation rule (pinned — the graph shape #1409's probes read): each scenario materializes (1) one `statement` point per prompt_pack turn (pointKind `statement` — canonical kind, verified — content = turn text), (2) per contradiction_pair: two `statement` points + one NAND operator between them — **k = the pair's `injection_turn`, stored as a point prop on BOTH claim statement points** (scenario-level k is not stored), (3) per evidence_script: one `evidence` point (registered kind), (4) operator targets ordered by scenario order — all ids via the deterministic id scheme**; `batch_setup(proj, scenarios, *, embedding_fn)` — per-scenario namespace, 2 UNWIND queries: points query (deterministic ids + `content_hash` + `pointKind` + **`is_operator:false` explicit** (verified create_point writes it; a NULL is_operator breaks SDK dedup MATCHes and #1409's `is_operator=false` filters) + **`status:'draft'` (create_point default, sdk.py:1400 — the equivalence test compares status; the guarded CREATE must not re-SET status on a re-run so a promoted live point is never downgraded to draft)** + props + `createdAt`/`updatedAt` + embedding via call-time import); operators query (**idempotent via MERGE on deterministic id; node CREATE mirroring sdk.py L3311 — id, is_operator, op_type, direction, [label], [status], NO timestamps/content_hash; **edges idempotent too: `MERGE (o)-[:NAND {idx:$i}]->(s)` — idx inside the pattern so re-runs match existing edges without dropping input-order fidelity** (plain edge CREATE would duplicate edges on re-run); endpoint MATCH guards with Point|Event acceptance; promote_source flips only Point sources with `status IS NULL OR 'draft'`, never Events/terminal — all verified sdk.py create_operator**) — **NOTE: the guarded points CREATE is genuinely net-new Cypher (no in-repo UNWIND CREATE/MERGE-node precedent); model the params-array/UNWIND mechanics on the in-repo precedents ep.py:140 / dream.py:230 / sdk.py:7335 (batch write-back UNWIND params pattern)**; `naive_setup(sdk, scenario, ids)` — SDK create_point (`dedup=True, id=<deterministic>`) / **create_operator(..., direction=None)**; **event-emission divergence documented: batch path skips :GraphEvent emission — the equivalence comparison uses keyed point/operator lookups, never graph-wide node counts**. Deterministic id helper: `scenario_entity_id(kind, content)` → `sha256(content)[:26]` with kind prefix, mirroring the SDK's `_entity_name_id` precedent; **operator id content pinned: `scenario_entity_id("nand", "<op_type>|<source>|<targets>")` (canonical serialization)**.

**Step 4:** Run tests → PASS.

## Task 6: runner/run.py — orchestration + artifacts

**Intent:** The run command core: seed pinning (base + index), batch-setup flag wiring, budget guard, per-scenario run_artifact.json + summary.json emission, exit-code computation after artifacts.

**Acceptance:** run_id = seed+arm+scenario (random-free); artifacts written to `<out>/<attempt_ts>/<run_id>.json` + summary.json (schema v1.0, schema test); ep_outcome = converged (mock); excluded counts present; all-failed → artifacts written THEN exit 4; arm-init failure → summary-only exit 4; budget over → exit 1 before run; setup mode recorded.

**Files:**
- Create: `battery/runner/run.py`, `battery/runner/artifacts.py`, `tests/test_battery_run.py`

**Step 1:** Write tests — artifact schema validation (run_artifact v1.0 + summary v1.0, concrete field sets from scope DD4/DD15 + the two deliberate additive fields below); run_id composition (seed+arm+scenario, random-free); per-scenario emission (2 scenarios × 2 arms → 4 artifacts); excluded counts; deterministic seed ordering; budget refusal (exit 1 class, before run) **for cost-over-budget AND for `--max-episodes > budget.max_episodes` (fixture with a cap < requested — budget wins, DD12 precedence)**; all-failed → artifacts written THEN exit 4 (b1); **arm-init failure: mock arm whose `setup_scenarios` raises ArmUnavailable → summary written (arm_present=false), ZERO episode artifacts, exit 4 (b2, scope DD7)**; **multi-arm mixed: arm A init-fails, arm B completes → exit 4, absent marked in summary, arm B's artifacts present (b2/mixed tests inject arm instances directly at run_battery() level — never via --mock/--arms, which select only resolvable battery.arms modules)**; **harness-batcher DB error (projection g.query raises) → operational exit 1, not 4**; summary schema.

**Step 2:** Run → FAIL.

**Step 3:** Implement `run.py` (run_battery orchestration: **load config (EmptyCorpus raises AT LOAD → exit 5) → budget guard → per (arm, scenario): arm.setup_scenarios(scenarios) (MockArm = no-op; harness batcher invoked by run.py when `--batch-setup`, its round-trips feed artifact setup.round_trips; setup namespace `team_<scenario>` is DISTINCT from the arm's isolation_namespace) → episode → score → artifacts → summary; exit-code computation**; **`--mock` semantics pinned: `--mock` sets arms=[mock]; `--arms` takes precedence when both are given (no usage error)**) + `artifacts.py` (schema-validated writers). **stdout contract: on success, print the attempt dir path `<out>/<attempt_ts>` as the LAST stdout line** (owned here; the determinism test parses it). **attempt_ts format pinned here: `%Y%m%d-%H%M%S-%f` (sub-second — two sequential runs can land in the same second; identical stamps would let the determinism test silently compare a dir to itself)**. **Default budget caps are sized to PASS the smoke corpus** (over-budget refusal is tested only via a fixture config).

**Schema note (pinned here):** run_artifact v1.0 fields — run_id, seed, arm, scenario_id, tier, model {provider, model_id, temperature}, determinism {seed, execution_order, python_hash_seed}, episode_trace {turns[{turn, role, tool_calls, tokens, model_call_outcome, content}], n_turns, n_tool_calls, re_derivations, total_tokens}, metric_values, model_call_outcomes {ok, rate_limited, timeout, fallback_cached, failed}, ep_outcome, **isolation_breach (bool, default false — DD7(d) flag; trigger wiring #1408)**, excluded {count, episode_ids, reason}, setup {mode, round_trips}, timestamps, provenance {git_sha, config_files, cal_table_hash}, schema_version. summary v1.0 fields — per-arm {arm_id, arm_present, scenarios, valid_episodes, excluded {count, episode_ids, reason}}, run-level {exit_code, run_ids, artifacts[], seed, timestamp}, schema_version. (Additive vs scope DD4/DD15: `turns[].turn` index + both `schema_version` fields — deliberate schema-lock updates, noted.)

**Step 4:** Run tests → PASS.

## Task 7: cli.py — subcommand surface + exit codes

**Intent:** The plan-§6 CLI contract: subcommands `run|parity|calibrate|validate-judge|report`, full flag surfaces, exit-code mapping (contract exceptions → codes), stubs for child-owned subcommands.

**Acceptance:** All 5 subcommands parse their pinned flags; `run` works end-to-end (mock); empty corpus → exit 5; unknown flag/subcommand → exit 1 (argparse exit-2 remapped); contract exceptions raised at dispatch → correct codes (JudgeGateBlocked→2, InconclusiveRun→3, ArmUnavailable(run-level)→4, EmptyCorpus→5); stubs exit 1 with ownership message.

**Files:**
- Create: `battery/cli.py`, `tests/test_battery_cli.py`, `battery/__main__.py`

**Step 1:** Write tests — subcommand surface (each parses its flags); exit codes end-to-end (run ok 0, empty corpus 5, budget 1, all-failed 4); dispatch mapping via injected exceptions (2/3/4: JudgeGateBlocked→2, InconclusiveRun→3, **ArmUnavailable(run-level)→4, IsolationBreach→4**); stubs exit 1 with ownership message; argparse remap (unknown flag → exit 1); **unknown arm `--arms nope` → exit 1; bad `--scorer does.not.exist` → exit 1 (scope DD3/DD16)**; ConfigError/GoldVerificationError → exit 1; **`--mock --arms a0` → a0 WINS (no usage error; Task 6 pins --arms precedence), exactly one arm (a0) in summary**; **EmptyCorpus caught before ConfigError → exit 5, never masked to 1**.

**Step 2:** Run → FAIL.

**Step 3:** Implement `cli.py` (argparse with subparsers; `_main()` returning ExitCode; exception→exit mapping table; stubs). `__main__.py` for `python -m battery`.

**Step 4:** Run tests → PASS.

## Task 8: Determinism E2E-7.1 test

**Intent:** Prove re-run reproducibility: same seed → |Δ| ≤ 1e-6 across metric_values, in separate subprocesses with pinned hash seed + isolated DB.

**Acceptance:** `tests/test_battery_determinism.py` runs the CLI twice via subprocess (PYTHONHASHSEED=0, TORTOISE_DB_URI="", TORTOISE_DB_PATH=<attempt tmpdir>), compares metric_values across attempt dirs with |Δ| ≤ 1e-6.

**Files:**
- Create: `tests/test_battery_determinism.py`

**Step 1:** Write the test — subprocess `[sys.executable, "-m", "battery", "run", "--mock", "--seed", "7", "--config", <abs battery/config>, "--out", <abs attempt dir>]` run TWICE with env `{**os.environ, "PYTHONHASHSEED": "0", "TORTOISE_DB_URI": "", "TORTOISE_DB_PATH": <abs tmpdir per attempt>}` and `cwd=repo_root, timeout=120` (abs paths + `sys.executable` + timeout pinned — bare `python` may not be the uv venv; defaults resolve against CWD). CLI prints the attempt dir path on stdout (Task 6 contract); the test parses both, **ASSERTS THE TWO ATTEMPT DIRS DIFFER** (attempt_ts pinned to sub-second precision `%Y%m%d-%H%M%S-%f` — two sequential runs can land in the same second; identical dirs would silently compare a dir to itself), and compares metric_values across them with |Δ| ≤ 1e-6. Mark `@pytest.mark.slow` (each subprocess boots a fresh redislite daemon).

**Step 2:** Run → FAIL on first execution (expected: subprocess env isolation or metric divergence — debug the REAL failure, not a missing CLI) → PASS.

**Step 2b (URI-neutralization assertion, scope DD6 pin):** with `TORTOISE_DB_URI=""` + `TORTOISE_DB_PATH=<tmpdir>` set in a subprocess, the SDK resolves to the tmpdir store (store file created at that path); two runs with distinct paths share no graph state — a direct assertion, not just the |Δ| gate.

**Step 3:** Run full suite `uv run pytest tests/test_battery_*.py -v` → all PASS.

**Step 4:** Run broader suite `uv run pytest tests/ -v -m "not slow"` → no regressions in touched areas (longmem_eval, sdk). **Append a CHANGELOG entry under `## [Unreleased]` → `### Added — battery harness core (#1406)`** matching the repo's per-feature format.

---
<!-- plan-review: status=clean, gate=task-workflow-standard plan-verify (2 parallel verifiers × 4 cycles — P1s: scorer seam/ep_outcome transport, batch round-trip reconciliation, equivalence keying, EpisodeResult, gold store, DB isolation, NAND direction, attempt_ts, exit-4 ordering, --mock/--arms precedence; cycle 4 final confirm CONVERGED) -->
