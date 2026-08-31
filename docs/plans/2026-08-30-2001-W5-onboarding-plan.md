<!-- research-path: docs/epics/2026-08-29-agent-driven-onboarding-1976/02-research-brief.md -->

# W5 Graph-Held OnboardingState Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
> **Issue:** #2001 (W5 of epic #1976, agent-driven onboarding) · **Branch:** feat/2001-W5-onboarding

**Goal:** Move onboarding FLOW state into a graph-held OnboardingState node per org behind a wire-compatible merged GET, with idempotent keyed-MERGE writes, a canonical step module, grandfathered backfill, a Setup-guide card mirror, and the cross-W E2E slice — without re-onboarding a single existing org or breaking any live consumer.

**Team:** epistemic-team
**Role:** (unavailable)

**Architecture:** Per-org `OnboardingState` node in the team's data-plane graph (same graph as TeamMeta + the Organization Subject — required for the `onboards` edge), initialized in the same eager Cypher statement as TeamMeta wherever TeamMeta exists (create_team, sdk.team_create, register_user ×2, CLI) plus post-RPC hooks on `provision_team`/`provision_team_with_token` and a write-time single-statement-MERGE create-on-write seam for absent-node orgs. FLOW keys (fork/status/version/completed-step edges/member_progress/last_decide_attempt/compact) live ONLY on the node; OPERATIONAL keys (github_*, session_recording, receipts, probes, scopes) stay in `teams.onboarding_state` jsonb where their ~17 live consumers read them. The raw reader `_get_onboarding_state` is untouched; a new read-only `_get_onboarding_projection` merges raw + graph (node-aware completion; graph-down → FLOW `status:'unavailable'` markers, never fabricated defaults). One shared writer routes per-key-type (operational→jsonb RMW; FLOW→graph keyed MERGE; branch before the allowlist filter; FLOW never enters jsonb). Completion is fork-aware via `completion_gate_satisfied` in the shared module; status is server-owned, gate-written, monotonic. Rail-1 sub-step order: plumbing (T1-T4) → W2/W3 land → migration/mirror/completion (T5-T7).

### Pattern Research

**Axis Architecture (high)**
- **FalkorDB property-value types** (canonical: https://docs.falkordb.com/datatypes.html): "Maps cannot be stored as property values"; storable = strings, booleans, ints, floats, geospatial, temporal, arrays (no graph-entity/null elements). → member_progress = JSON-string property (string is storable); wire shape stays {user_id: {steps[]}}. Confirms codebase precedent hosted_api.py:10593 (#498).
- **FalkorDB concurrency** (canonical: https://docs.falkordb.com/design/concurrency): per-graph reader-writer model — write queries serialized FIFO per graph, every write query atomic, readers see snapshot isolation. → keyed MERGE {org_id, step_id} is race-free; concurrent inits converge to one node; JSON-string RMW in ONE query is atomic. Embedded FalkorDBLite re-fire caveat (sdk.py:694-745: concurrent same-key MERGEs re-fire ON CREATE and report "created:1" for both) → per-org in-process lock (`_step_write_locks`, the `_source_merge_locks` pattern) so the W11 created-signal is honest in the embedded lane; docker lane (bolt://) stats are honest natively.
- **MERGE ON CREATE SET / ON MATCH SET** (canonical: https://docs.falkordb.com/cypher/merge.html): idempotent first-write-wins directly expressible → W11 edge-new-creation signal via MERGE stats (created vs no-op).
- **Dual-write migration precedence** (pitfalls + canonical: Google Cloud "Online Database Migration by Dual-Write"; LaunchDarkly "3 Best Practices For Zero-Downtime Database Migrations"; sujeet.pro "Zero-Downtime Data Migrations"; AWS Keyspaces dual-write guide): ONE authoritative source per entity per phase; "split authority is dangerous"; read preference shifts to the new store at cutover. → node-aware completion (node present → node.status; absent → jsonb grandfathered) + per-key-type routing (no per-key dual-write). DM-2's "sweep reconciles from jsonb" is SUPERSEDED (recorded): jsonb never holds FLOW keys for new orgs, so the reconciliation source is empty by construction; fail-loud + idempotent retry satisfies the divergence negative.
- > Deduplicated from epic brief §1 (migration context: one-org special case, grandfathering) + §4 (architecture patterns: graph-held state, idempotent #398, versioned, edge-derived steps).

**Axis Ontology (standard)**
- > Deduplicated: epic plan §4 DM-1 pins the node schema + onboards edge + canonical step list; ONTOLOGY.md §3.6 memberOf precedent. No fresh queries fired (in-repo precedent: #452 name-MERGE, Subject organization/naturalPerson).

> **Findings date:** 2026-08-30

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract |
|---|---------|------|-----------|-----------|----------|
| 1 | OnboardingState node (new, FalkorDB) | DB (graph) | Both | Integration (docker lane) + unit | Node inited graph-side in the SAME eager statement as TeamMeta (or post-RPC hook / single-statement write-time create-on-write for no-TeamMeta lanes); keyed MERGE {org_id, step_id} first-write-wins (200 no-op replay; 409 only node-level/set-once); canonical step list in ONE shared module + unknown step_id 422; member_progress user-scoped (session-only, key-auth non-UUID 403) + cross-org 403; last_decide_attempt LWW (conditional); onboards edge → Organization Subject; graph-DOWN → merged GET FLOW 'unavailable' + degraded card (never false checklist); orphan (graph up, node absent) → defaults + no write |
| 2 | Legacy store migration (jsonb → node backfill) | DB (Supabase) + graph | Both | Integration (docker lane) + regression | Backfill idempotent (re-run no-op); grandfathered orgs keep onboarding_complete (wire stable pre/post materialization + T7 flip); operational keys stay jsonb + live consumers work; envelope preserved on GET; node-aware completion (4 wire cases); PATCH wire-compat (underscore→hyphen, team_created strip); fork defaults at READ time, persisted only on explicit opt-in; divergence negative (jsonb-ok/graph-fail → retry converges, no lost FLOW keys) |
| 17 | Cross-W full-journey E2E slice (DE2E-12) | E2E | Both | Hosted-e2e (RUN_HOSTED_E2E=1) + docker-lane | signup→org→fork→connect→seed→decide one sitting (scripted/mock agent via checkpoint calls + graph reads); fork-aware gates per self/build/compact; dismissal alone never completes; org B never re-asks the fork card; W11 events fire once via edge new-creation; graph-down degraded render |

**Bug Pattern Flags**
- Silent function skips (HIGH): completion set without the fork-aware gate → monotonic server-owned status + per-step write-surface ownership (PATCH = {catalog-presented} only; server-owned steps 403/422; status 403) + forge-negative E2Es.
- Race conditions (MEDIUM): concurrent org-creates → exactly one node (single-statement MERGE + per-graph write serialization + concurrent-init test); embedded MERGE re-fire → per-org in-process lock.
- Contract drift (MEDIUM): FLOW keys added to jsonb defaults would persist via the whole-dict RMW → router branches BEFORE the allowlist filter + `_write_onboarding_state` defensive strip + registration-split negative assertions.
- Graph-down false checklist (MEDIUM): graph EXCEPTION → 'unavailable' (never defaults); node-absent → defaults (distinct).

**Checklist Notes**
- Atomicity: org-create + OnboardingState init graph-side (one Cypher statement); checkpoint step + member writes single-query.
- Idempotency: keyed MERGE (#398, never-overwrite); backfill re-run no-op; W11 dedup via edge new-creation.
- Boundary values: fork render 0/1/2 (per-org-once); unknown step_id; set-once changed-409 vs same-value-200; graph-down read vs write.

### Verification Plan

- **Docker lane (default):** `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/ -v` — graph surfaces (node init, MERGE idempotency, checkpoint authz, backfill, graph-down, created-signal) + full regression.
- **Embedded carve-out:** `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embedded_lifecycle.py tests/test_guard.py -v` + shared-module unit tests.
- **Hosted E2E (opt-in):** `RUN_HOSTED_E2E=1 uv run pytest tests/e2e/hosted/ -q -rs` (DE2E-12 module).
- **Dashboard:** `cd website/apps/dashboard && node --test src/setupGuide.test.js` (+ full `src/*.test.js` glob via CI).
- **UX verification (UX=low):** card reuses existing components (`.cards`/`.card` grid, `.wizard-progress` semantics, `.banner`) — no new design language; component-catalog check only.
- **Backfill:** `uv run python graph-scripts/backfill_onboarding_state.py` DRY-RUN (no-op on owner orgs); `--apply` idempotency verified by re-run; `--recompute` sweep invocation (T7) verified DRY-RUN then applied.

### Journey Test Map

### Journey: First-run one-sitting (DE2E-1/12) — signup → org → fork → connect → seed → decide
1. **Step:** create org (name required) → **Acceptance:** OnboardingState node exists (version=1, team-named edge) → **Test:** test_onboarding_state_split.py::test_org_create_inits_node
2. **Step:** pick fork (self) → **Acceptance:** fork set-once persisted; replay 409/same-value 200 → **Test:** test_onboarding_state_split.py::test_fork_set_once
3. **Step:** agent connects (harness-connected checkpoint) → **Acceptance:** step edge created, created-signal true, replay no-op → **Test:** test_onboarding_state_split.py::test_checkpoint_idempotent
4. **Step:** seed + decide (W3 mock via checkpoint) → **Acceptance:** gate satisfied → status complete → wire onboarding_complete true → **Test:** test_onboarding_state_split.py::test_completion_gate
5. **Step:** re-enter → **Acceptance:** resumes at current step; Setup-guide card mirrors state → **Test:** test_onboarding_state_split.py::test_resumption

> Graph-read assertions (node version, completed_steps match, onboards edge) live in the docker-lane leg (direct graph access); the hosted-e2e leg asserts merged GET states + checkpoint signals only (substrate single-writer constraint).

### Journey: Legacy-org migration (DE2E-6)
1. **Step:** backfill legacy jsonb → **Acceptance:** node created, status from jsonb complete, re-run no-op, fork null, operational keys untouched → **Test:** test_onboarding_state_split.py::test_backfill_idempotent
2. **Step:** first Settings open → **Acceptance:** fork defaults at read (not persisted); card collapse status-driven → **Test:** test_onboarding_state_split.py::test_fork_defaults_at_read

### Failure Modes
- Graph down at read → **Expected:** merged GET 200 {operational keys, FLOW 'unavailable'}; card DEGRADED (never "N of 4") → **Test:** test_onboarding_state_split.py::test_graph_down_read
- Graph down at checkpoint write → **Expected:** 503 fail-loud, retry-safe → **Test:** test_onboarding_state_split.py::test_checkpoint_graph_down
- Cross-org checkpoint → **Expected:** 403 (auth-context derivation) → **Test:** test_onboarding_state_split.py::test_checkpoint_cross_org_403
- Forged decide via PATCH → **Expected:** 422, no edge, no completion, no event → **Test:** test_onboarding_state_split.py::test_patch_forge_rejected

**Tech Stack:** Python 3.12 (FastAPI, FalkorDB via existing SDK), Supabase jsonb (control plane), React (dashboard SPA), node --test (dashboard modules), pytest (docker lane).

---

## Task 1: Canonical module — `tortoise/onboarding/state.py`

**Intent:** ONE shared source of truth for the onboarding state machine — canonical step list, card subset, per-key-type semantics, step validation, the fork-aware completion gate, and the graph write/read primitives the endpoints, agents (W2/W3), and card all consume.

**Acceptance:** The module exports `ONBOARDING_STEPS` (6), `CARD_STEPS` (4, ⊆ canonical), `PER_KEY_SEMANTICS`, `completion_gate_satisfied`, `validate_step_id`, graph writers (`ensure_onboarding_state_node`, `write_completed_step`, `write_fork`, `write_compact`, `write_last_decide_attempt`, `write_member_progress`, `write_status`), `read_onboarding_node`. Unit tests: unknown step rejected; card-subset ⊆ canonical; gate logic per fork (self/build/compact, compact-first, fork=None→'self'); set-once/LWW semantics table complete.

**Files:**
- Create: `tortoise/onboarding/state.py`
- Create: `tests/test_onboarding_state.py`

**Step 1: Write the failing unit tests** (canonical list, card-subset ⊆ canonical, unknown step, gate logic, semantic table).
**Step 2: Run to verify fail** — `uv run pytest tests/test_onboarding_state.py -x` → FAIL (module missing).
**Step 3: Implement the module** (constants + pure functions first — no SDK wiring; writers take a `sdk`/graph param).
**Step 4: Run to verify pass** — `uv run pytest tests/test_onboarding_state.py -v` → PASS.
**Step 5: Commit** (via commit-workflow).

## Task 2: Eager init + hooks + create-on-write seam

**Intent:** Every org has exactly one OnboardingState node, initialized graph-side at org-create (same statement as TeamMeta) with a deterministic write set; no-TeamMeta lanes and absent-node orgs converge via idempotent single-statement MERGE.

**Acceptance:** Node exists immediately after every MINT path (lane-coverage test incl. SDK-lane CI-visible assertion); eager-init writes {org_id, fork (inherited/'self' fallback, null first org), status 'active', version 1, compact (creator's prior memberships > 0)} + team-named edge; write-time create-on-write mirrors jsonb onboarding_complete → status (never clobber); concurrent creates → one node; export/restore round-trip preserves fork + completed_steps; OnboardingState/OnboardingStep NOT added to `_EXPORT_SKIP_LABELS` (asserted).

**Files:**
- Modify: `tortoise/onboarding/state.py` (`onboarding_node_init_cypher()`, `ensure_onboarding_state_node()`)
- Modify: `tortoise/hosted_api.py` (register_user pre-RPC ~3418 + registry ~3499, `_create_team_supabase_lane` ~6266 (TeamMeta statement at 6261 — eager wire in the SAME statement), `_create_onboarding_team_lane` ~10886 — Supabase branch rides create-on-write (no TeamMeta statement exists there today; no behavior change), agent_signup ~8944 → post-RPC hook, `provision_tenant` ~964 (5th TeamMeta mint statement — eager wire; W12-scope selfhost excluded but the statement is in this file))
- Modify: `tortoise/sdk.py` (`team_create` ~11003)
- Modify: `tortoise/supabase_control.py` (post-RPC hooks after provision_team ~1588 + provision_team_with_token ~1625 — NEW hook for the latter)
- Modify: `tortoise/__main__.py` (~4918 CLI re-seed) — coverage scoped to MINT paths only; existing-team re-seed rides create-on-write
- Test: `tests/test_onboarding_state_split.py::TestNodeInit` (lane coverage + concurrent-init) + backup round-trip test (fork/completed_steps survive export→restore; no-skip-labels assertion; completed_steps re-asserted once T4 checkpoint edges exist)

**Steps:** write failing lane-coverage test → implement `onboarding_node_init_cypher()` + `ensure_onboarding_state_node()` in state.py → wire eager fragment into the 5 TeamMeta statements (provision_tenant 964, register_user 3418/3499, create_team Supabase 6261, sdk.team_create 11003) + both provision hooks + create-on-write in the writer → concurrent-init test → backup round-trip test → run docker-lane tests → commit.

## Task 3: Read projection + consumers

**Intent:** The merged GET serves FLOW keys from the graph and operational keys from jsonb with the envelope preserved; graph-down degrades to 'unavailable' markers (never fabricated defaults); the MCP gate and agent tool read the same projection.

**Acceptance:** `_get_onboarding_state` byte-unchanged (raw; test seams + registry auto-init + sub-team guard + session gate intact); `_get_onboarding_projection` composes it (graph leg strictly read-only; node-absent → defaults, no write; graph exception → FLOW 'unavailable', 200); GET re-pointed; `_team_onboarding_complete` coerces non-bool → False (fail-open); `tortoise_onboarding_state` re-pointed; 5 pinned test seams pass unmodified — named: test_mcp_http.py (gate), test_onboarding_analytics_patch.py (DB-free echo), test_onboarding_health_flip.py (wire), test_onboarding_endpoints.py (registration), test_onboarding_integration.py (complete-flag).

**Files:**
- Modify: `tortoise/hosted_api.py` (GET handler ~10705, new `_get_onboarding_projection`)
- Modify: `tortoise/mcp_server.py` (`_team_onboarding_complete` ~2409, `_onboarding_state` ~2436)
- Test: `tests/test_onboarding_state_split.py::TestProjection`, `tests/test_mcp_http.py` (seams)

**Steps:** TDD the projection (raw compose, FLOW merge, precedence, 'unavailable', read-only) → re-point consumers → run test_mcp_http + test_onboarding_analytics_patch + health_flip to confirm seams → commit.

## Task 4: Shared writer + PATCH retarget + POST checkpoint + W11 created-signal

**Intent:** ONE per-key-type write path — PATCH retargeted (operational→jsonb, FLOW {catalog-presented}→graph, server-owned keys rejected) and the new auth-context checkpoint — both echoing the merged projection; the edge-new-creation signal exposed for W11.

**Acceptance:** `_update_onboarding_state` becomes the router (name preserved); PATCH wire-compat preserved (underscore→hyphen, team_created strip, email/harness/section pops); FLOW keys never in jsonb (defensive strip = the 7 FLOW keys ONLY — fork, status, version, completed_steps, member_progress, last_decide_attempt, compact; `onboarding_complete` is a LEGACY jsonb key, NOT FLOW, until the T7 flip — both the mcp_http seam and the integration complete-flag seam stay green through T4→T7; the guard's jsonb-true trigger grandfathers any T4→T7 jsonb-true org, incl. self-forged PATCH completions — pre-existing surface, indistinguishable from wizard completers — accepted); registration-split negatives; checkpoint: dual-auth, auth-context team, unknown step 422, fork/compact set-once (same-value 200, changed 409), LWW conditional, member_progress session-only 403, status 403, extra="forbid", graph-down 503, `{created_steps, noop_steps}` response; post-write gate eval (monotonic); per-org in-process lock for embedded re-fire; mixed-key PATCH partial-failure (jsonb-first graph-second; graph failure after jsonb success → 500 fail-closed retry-safe); MCP TTL-cache invalidation on completion (created-signal); preserved-409 regression (session-recording-off, dup-name, sub-team re-entry, already_registered all still 409).

**Files:**
- Modify: `tortoise/onboarding/state.py` (writers + eval)
- Modify: `tortoise/hosted_api.py` (`_update_onboarding_state` ~10612 router, PATCH ~10720, `OnboardingStatePatchRequest`, new POST /v1/onboarding/state/checkpoint, `_write_onboarding_state` strip)
- Modify: `tortoise/mcp_server.py` (invalidate `_onboarding_state_cache` on created-signal completion)
- Test: `tests/test_onboarding_state_split.py::TestWriter` + `TestCheckpoint` + `TestPatchRouting` (incl. preserved-409 regression + mixed-key 500 negative); extend `tests/test_onboarding_endpoints.py` (registration split: round-trip vs declared-rejected vs accepted-dropped + FLOW-absent-from-jsonb negatives); keep `tests/test_mcp_http.py::TestOnboardingToolGating` on the JSONB seam through T7 (per-phase semantics: T4 keeps jsonb seam — tests write `onboarding_complete=True` via `_update_onboarding_state`, gate jsonb-based until T7 — no red window; T7 flips both gate and suite to node status); `tests/test_onboarding_analytics_patch.py` (echo contract); `tests/test_onboarding_integration.py` (grandfathered fixture via direct registry Team-node write + node-present positive)

**Steps:** TDD the router + strip → PATCH retarget + model changes → checkpoint endpoint → gate eval wiring → seam updates → docker-lane regression → commit.

## Task 5: Grandfathered backfill (migration — LAST)

**Intent:** Idempotently backfill FLOW-relevant legacy fields (completion status) into nodes for grandfathered orgs; operational keys untouched; re-run no-op.

**Acceptance:** `backfill_onboarding_state()` importable + `graph-scripts/backfill_onboarding_state.py --apply` (DRY-RUN default); both source lanes (Supabase jsonb → node; registry Team-node JSON → node); absent-node-only (never clobber node-present; never jsonb-false→complete; never status→jsonb); fork null; exclusions (placeholder teams.id='' + soft-deleted); re-run no-op; wire stable across materialization.

**Files:**
- Create: `graph-scripts/backfill_onboarding_state.py`
- Modify: `tortoise/onboarding/state.py` (backfill fn)
- Test: `tests/test_onboarding_state_split.py::TestBackfill`

**Steps:** TDD the fn (both lanes + exclusions + idempotency) → the --apply wrapper (backfill_pack_installs precedent) → DRY-RUN against fixtures → commit.

## Task 6: Setup-guide card mirror

**Intent:** The dashboard renders the same graph-held state — card-subset checklist, current step, fork-aware rows, DEGRADED never a false count.

**Acceptance:** `setupGuide.js` pure derivation module + node --test (N-of-M = ∩ card-subset; fork-aware; status-collapsed; DEGRADED); card component in main.jsx (one shared component; reentry card defers); Python parity test (JS CARD_STEPS ⊆ canonical); LOADING = fetch transient.

**Files:**
- Create: `website/apps/dashboard/src/setupGuide.js` + `setupGuide.test.js`
- Modify: `website/apps/dashboard/src/main.jsx` (card mount in the Overview grid ~4556)
- Test: `tests/test_onboarding_state.py` (parity)

**Steps:** TDD the derivation module (node --test) → parity test → card component → `node --test src/*.test.js` → commit.

## Task 7: Completion projection (T7 staging) + cross-W E2E

**Intent:** At T7 the wire flips to node-aware completion with a recompute sweep; the cross-W E2E slice proves DE2E-1/6/12 with a scripted/mock agent.

**Acceptance:** Node-aware wire (node present → node.status; node absent → jsonb) with the grandfathered-window guard (node present, status 'active', ZERO completed-step edges, jsonb onboarding_complete=true → wire true — one-directional, self-terminating: first step edge → node governs; kills the poisoned-false window for orgs completing via the legacy wizard during T2→T7); accept-and-drop activation ORDERED AFTER W1 (#1997) removes wizardComplete (cross-PR ordering pin — interim carve-out extends until W1 merges; the node-aware precedence for agent-flow orgs is independent and ships at T7); recompute sweep over existing node-present orgs — GRANDFATHERED BRANCH RUNS BEFORE GATE EVAL (zero edges + jsonb onboarding_complete=true → status stays/writes 'complete', skip gate eval — never active; then gate eval → status for edge-bearing orgs, monotonic; the grandfathered 'complete' write mirrors a real legacy completion, monotonic-up, consistent with server-owned status); named completion suites migrated to node-aware wire semantics: test_onboarding_integration.py (test_e2e_onboarding_complete_flag), test_onboarding_health_flip.py, test_onboarding_endpoints.py, test_mcp_http.py::TestOnboardingToolGating (T7 flips gate + suite to node status; test_onboarding_analytics_patch.py re-checked — DB-free → node-absent → jsonb fallback → unaffected); poisoned-false + poisoned-new-org negatives in TestCompletionWire; DE2E-12 green; graph-down + divergence negatives green.

**Files:**
- Modify: `tortoise/onboarding/state.py` + `tortoise/hosted_api.py` (projection flip + sweep runner)
- Modify: `graph-scripts/backfill_onboarding_state.py` (add `--recompute` flag — sweep runner; DRY-RUN default; backfill_pack_installs precedent)
- Create: `tests/e2e/hosted/test_14_onboarding_journey.py` (DE2E-12, RUN_HOSTED_E2E opt-in; calls `skip_unless_hosted_e2e()`)
- Test: `tests/test_onboarding_state_split.py::TestCompletionWire` (4 wire cases + poisoned-false + poisoned-new-org negatives) + docker-lane journey leg (TestClient + real graph: DE2E-1 node version=1, completed_steps match, onboards edge → Organization Subject constructed via SDK — W5 asserts its read surface; W3's seed-write contract is #1999's test)

**Steps:** TDD the wire flip (node-aware + grandfathered-window guard) → recompute sweep via the `--recompute` runner → migrate the 4 named completion suites → DE2E-12: docker-lane journey leg (direct graph assertions) + hosted-e2e HTTP leg (merged GET states, checkpoint `{created_steps, noop_steps}` created-signal — W11 event-level assertion deferred to #2006, W5 exposes only the signal; fork semantics; dismissal-never-completes; org B never re-asks fork card; NO direct DB reads in hosted-e2e — substrate single-writer constraint) → full docker-lane run → commit.

> **Cross-W consumer pin (P3-1):** W3 (#1999) reads `projection.status` (node) — never wire `onboarding_complete` — during the T4→T7 interim (wire stays jsonb until T7).

---

## Task Template Fields (executing-plans handoff)

Each task above carries Intent/Acceptance/Files/Steps. Executing-plans Step 2.5 (Fidelity Gate) compares planned Files vs the git diff. Reference the scope doc `docs/plans/2026-08-30-2001-W5-onboarding-scope.md` for the full verified pin set (18 contract decisions).
