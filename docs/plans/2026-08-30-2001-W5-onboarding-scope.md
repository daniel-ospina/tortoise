---
title: "Scope — #2001 (W5): graph-held OnboardingState + migration + Setup-guide mirror + cross-W E2E"
type: decisions
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-30
aboutSubjects: tortoise
aboutObjects: tortoise
---

<!-- issue-scoping: v5.1 double diamond + verify -->

# Scope — Issue #2001 (W5, epic #1976 agent-driven onboarding)

> **Epic plan:** `docs/epics/2026-08-29-agent-driven-onboarding-1976/06-plan.md` §1 J1/J6, §2 WF-4, §4 DM-1/DM-2, §5 A-2/A-3, §6 I-1, §7 DE2E-1/6/12, §8 risk register.
> **Test design:** `04-test-design.md` (surfaces 1/2/17 = W5's). **Decomposition:** `07-decompose.md` (ownership pins).
> **Gates:** problem-verify 4 cycles CLEAN · solution-verify 2 cycles CLEAN · second-model coherence CLEAN · Phase-7 parallel review 9 cycles CLEAN (no P0/P1; P2/P3 pins absorbed).

---

# 1. Confirmed Problem

**Make the FalkorDB `OnboardingState` node the single authoritative store for onboarding FLOW state** — `fork`, `version`, the canonical completed-step EDGE set (first-write-wins keyed MERGE on {org_id, step_id}), `member_progress` (JSON-string-encoded per the #498 / FalkorDB maps-not-storable rule; wire shape {user_id: {steps[]}}), `last_decide_attempt` (last-write-wins, enum failed|dismissed|null) — **behind a wire-compatible merged GET** that preserves the `{onboarding, email}` envelope and the `onboarding_complete` wire with **node-aware precedence** (node present → `node.status`; node absent → jsonb legacy, grandfathered), **write paths that can never round-trip FLOW keys into jsonb** (per-key-type routing; raw reader stays raw; read-only projection), **eager idempotent keyed-MERGE init in every TeamMeta lane** plus a **write-time create-on-write seam for absent-node orgs** (single-statement MERGE, race-free), a **graph-down FLOW `status:'unavailable'` render** with bounded read (never fabricated defaults), an **idempotent grandfathered backfill** (operational keys untouched), **backup-safe persistence** (node exported by default — NOT in `_EXPORT_SKIP_LABELS`), the **Setup-guide card mirror** (card-subset ⊆ canonical), and the **cross-W full-journey E2E slice (DE2E-12)**. **The difficulty is the split's write/read/error contract — not the node schema.**

# 2. Verification Gates (record)

| Gate | Cycles | Result |
|---|---|---|
| problem-verify (2 parallel verifiers + controller) | 4 | CLEAN — 3 P1s fixed (write-funnel isolation, backfill restoration, mixed-PATCH graph-failure), 16+ P2/P3s absorbed |
| solution-verify (2 parallel verifiers + controller) | 2 | CLEAN — 2 P1s fixed (MCP-gate consumer sweep loudness, P1-B node-aware completion via wizardComplete transient), P2s absorbed (writer-name seam, read-only graph leg, 409 enumeration, member_progress residual) |
| second-model coherence (deepseek-v4-pro) | 1 | CLEAN — no P0/P1; 5 P2 documentation pins absorbed |
| Phase-7 parallel review (4 agents × 9 cycles) | 9 | CLEAN — P1s fixed (MCP raw-read paths, node-aware UNION→node-aware precedence, agent_signup lane, per-step write-surface ownership, compact discriminator + write surface, checkpoint-triggered gate eval, T7 recompute sweep, DM-2 supersession, monotonic gate eval, sdk.team_create × test flip), ~45 P2/P3 pins absorbed |

# 3. Solution (axis decisions — quality over convenience)

| Axis | Choice | Rejected (why) |
|---|---|---|
| A — step storage | **A1 per-step OnboardingStep nodes + COMPLETED_STEP edges** (W11 created-signal via MERGE stats; backup round-trip; `completed_by` extensibility) | A2 rel-carried edges (rel-prop MERGE unverified, weaker querying) · A3 JSON-array (OFF-PIN: no edges, no W11 signal, second store) |
| B — init | **B1 shared helper + same-statement eager MERGE in every TeamMeta lane + post-RPC hooks on BOTH provision_team and provision_team_with_token + write-time create-on-write seam** (explicit, testable, drift-resistant) | B2 embedded per-lane Cypher (drift) · B3 chokepoints only (agent_signup registry lane bypasses both) |
| C — backfill | **C1 importable function + graph-scripts/ `--apply` DRY-RUN wrapper** (auditable, re-run no-op, house precedent) | C2 in-app endpoint (prod-process blast radius) · C3 read-time lazy materialization (side-effecting GET, inverts migration-last, violates no-lazy-init) |
| D — write path | **D2′ ONE shared writer (keeps module name `_update_onboarding_state`) as per-key-type router** (operational→jsonb RMW; FLOW→state.py writers; branches BEFORE the allowlist filter; jsonb-first graph-second; echo = merged projection) | D1 same architecture less explicit · D3 dual-store PATCH (OFF-PIN) |
| E — read topology | **E1 projection COMPOSES the raw reader via module-attribute lookup** (test seams survive; write funnel raw; sub-team guard + session-recording gate stay raw; registry auto-init-write untouched) | E2 in-place projection (pollution risk at registry auto-init; guard fails open under graph-down) · E3 pure function above both (dual-merge drift, highest re-point cost) |
| F — checkpoint auth | **F1 auth-context team** (existing membership-validated `?team_id=` seam hosted_api.py:1443-1449; body never carries org_id; member_progress session-only → key-auth non-UUID 403; graph-down 503) | F2 body team_id (redundant trust path) · F3 graph-only (drops the mixed-write contract) |
| G — canonical list | **G1 server-driven** (canonical list served via merged GET; pure JS `setupGuide.js` derivation module + node --test; Python parity test JS-subset ⊆ canonical) | G2 mirrored JS constant + parity test (fragile) · G3 dedicated endpoint (ceremony) |
| H — tests | **H1 hosted-e2e scripted agent (RUN_HOSTED_E2E) + H2 docker-lane integration (native graph-down, honest MERGE stats) + H3 node --test card states** | H2-only (can't claim full journey over real HTTP) · H3-only (no backend truth) |

# 4. Pinned Contract Decisions (verified through 9 review cycles)

## 4.1 Store split & node schema
1. **Node** (tenant graph `team_{team_id}`, same graph as TeamMeta + Organization Subject — required for the onboards edge): `{org_id, fork: 'self'|'build'|null, status: 'active'|'complete' (SERVER-OWNED), version: 1, member_progress: JSON-string, last_decide_attempt: LWW enum, compact: bool}` + `[:onboards]->(:Subject {subjectKind:'organization'})` (W3 writes org_subject_id at seed; predicate registered in the pack manifest + ONTOLOGY.md) + per-step `(:OnboardingStep {org_id, step_id})` nodes + `[:COMPLETED_STEP]` edges (canonical; `completed_steps[]` = read projection, NEVER a second store).
2. **OPERATIONAL keys stay jsonb** (github_*, session_recording, receipts, probes, scopes, team_created, demo_created, prompt_pasted, onboarding_complete-legacy) — live consumers: session_recording gate (hosted_api.py:4613), sub-team re-entry guard (10864), install probes (5154), GitHub connect (11302), scope persistence (11798+), PATCH allowlist.
3. **FLOW keys never enter jsonb.** `_ONBOARDING_DEFAULT_STATE` stays operational-only; the router branches per-key-type BEFORE the allowlist filter; `_write_onboarding_state` strips FLOW keys defensively. Interim carve-out (T3-T6): `onboarding_complete` → jsonb for ALL orgs (legacy contract), inert-on-read post-T7 for node-present orgs (never migrated — that migration is the poisoned-true).

## 4.2 Read surface
4. **Raw reader `_get_onboarding_state` UNCHANGED** (jsonb/registry Team node; registry auto-init-write at 10566 stays a jsonb-store behavior). **`_get_onboarding_projection`** (new, READ-ONLY) composes it via module-attribute lookup + one bounded graph read: graph up + node present → FLOW from node; graph up + node absent → defaults (NO write); graph exception/slow → FLOW `status:'unavailable'` markers (200; operational keys still served). Consumers: GET /v1/onboarding/state, `_team_onboarding_complete` (coerces non-bool → False — fail-open), `tortoise_onboarding_state` MCP tool, PATCH/checkpoint echo. **Single projection site** (gate + wire cannot diverge).
5. **NODE-AWARE completion:** wire `onboarding_complete = node.status=='complete' if node present else jsonb.onboarding_complete` (grandfathered/pre-backfill). Ships at T7 (issue-body "completion projection LAST"). MCP gate fail-open: 'unavailable'/None → False (tools stay visible during outages).

## 4.3 Write surfaces
6. **Shared writer** keeps module name `_update_onboarding_state` as the per-key-type router: operational → jsonb RMW; FLOW (fork, compact, last_decide_attempt, status-internal, step edges) → state.py graph writers; unknown → drop (fail-closed, never default-to-FLOW). Echo = merged projection (writer-return-composed; operational-only PATCHes never touch the graph — DB-free fixture preserved).
7. **PATCH /v1/onboarding/state:** FLOW/step keys = `{catalog-presented}` ONLY (step-edge MERGE; W1/W8 first catalog render). ALL other FLOW keys (fork, status, version, member_progress, last_decide_attempt, compact, team-named, harness-connected, first-points-filed, decide-completed, capture-disclosed) DECLARED + REJECTED (403/422 — server-owned on the PATCH surface; no silent-ignore). Operational jsonb keys + wire-compat (underscore→hyphen `_PATCH_FIELD_TO_STATE_KEY`, team_created strip, email pop, harness/section analytics pop) UNCHANGED. `onboarding_complete`: interim → jsonb all orgs; T7+ → accept-and-drop (node-present) / jsonb (node-absent grandfathered). Mixed-key PATCH: jsonb-first graph-second; graph failure after jsonb success → 500 fail-closed retry-safe.
8. **POST /v1/onboarding/state/checkpoint** (new, agent/internal): dual-auth (`get_current_team_session_ungated`); team from auth context (membership-validated `?team_id=` seam; body NEVER org_id); per-step write-surface ownership — steps {harness-connected (W2), first-points-filed (W3 seed), decide-completed (W3 decide — REAL decide protocol per W2 SKILL.md; procedural trust, self-harm-only residual documented), capture-disclosed (W6), catalog-presented (W8)} dual-auth; fork/compact set-once (same-value replay 200, changed 409); last_decide_attempt LWW (conditional: skip 'failed' if the decide-completed edge exists); member_progress session-only (key-auth non-UUID → 403) under the per-org in-process lock (cross-process residual documented; per-user nodes = W7-volume follow-up); unknown step → 422; status → 403 (server-owned); `extra="forbid"`; graph-down (hard unavailability) → 503 before any write; response includes `{created_steps, noop_steps}` (W11 edge-new-creation contract — W5 exposes the signal, W11 emits).
9. **Per-key-type semantics table** (in the shared module): step-edge keys FWW 200-noop · fork/compact set-once (changed 409) · last_decide_attempt LWW · member_progress user-scoped map-merge · status server-owned monotonic. 409 ONLY node-level (unreachable by construction — per-tenant-graph + auth) + set-once. 3 existing 409s preserved + enumerated (session-recording-off 4616, dup-name 6235/6287/6328/6345, sub-team re-entry 10866, already_registered 3357+).

## 4.4 Init, completion, migration
10. **Eager init** (same statement as TeamMeta where it exists): create_team Supabase pre-RPC (:6261), sdk.team_create (:11003, covers registry create_team + onboarding-sub-team registry + CLI), CLI re-seed (:4918), register_user pre-RPC (:3418) + registry (:3499), provision_tenant (:964, W12-scope-excluded selfhost — documented). `agent_signup` Supabase = post-RPC hook on `provision_team_with_token` (NEW hook — currently none). Write-time **create-on-write** (any absent-node org; single-statement MERGE ON CREATE byte-identical to the eager statement incl. the team-named edge; seeds status from the mirror — jsonb onboarding_complete=true → complete, one-directional, never clobber). Lane-coverage unit test (node exists after every provision path; SDK-lane init assertion CI-visible).
11. **Eager-init write set:** {org_id, fork (subsequent org: inherited from creator's earliest team node, null/read-failure → 'self'; first org: null → fork card asked once, set-once persists), status 'active', version 1, compact (CREATOR's prior memberships > 0)} + **team-named step edge** (name REQUIRED at create — the self gate's team-named component satisfied at init). W9 (#2005) owns trigger/entry + door-skip READ + per-org override UI decision; the ONLY checkpoint fork writes = first-org fork card + grandfathered opt-in. J5 "per-org override" for inherited forks: OUT OF SCOPE ("never re-asks the fork card" wins; recorded disposition).
12. **completion_gate_satisfied(completed_steps, fork, compact)** compact-first, fork=None→'self': self = team-named + harness-connected + first-points-filed + decide-completed · build = first-points-filed + harness-connected + catalog-presented (decide excluded) · compact = first-points-filed + harness-connected (seed-lite; the plan's alias: 'first point' = the org-anchor Subject). W5 evaluates POST-WRITE on step/fork/compact writes (never member_progress) + `write_status('complete')` — **MONOTONIC: complete→active impossible; a grandfathered org's first FLOW write → eval no-op (status stays complete — no re-onboarding)**. W3 owns seed/decide gate evaluation (calls the same helper). Fork-aware render: build hides the decide row; compact renders the reduced checklist.
13. **T7 staging** (issue-body "completion projection LAST"): T1-T6 = plumbing + FLOW-key reads with jsonb completion; T7 = (a) node-aware wire projection, (b) **recompute sweep** over existing node-present orgs (gate eval → write_status; orgs completed via the agent flow during the window get complete; legacy-wizard completers with no edges stay active — correct), (c) accept-and-drop activation, (d) cross-W E2E. Interim jsonb onboarding_complete=true on node-present orgs = INERT post-T7 (documented one-way door; never migrated).
14. **Backfill** (migration LAST): importable function + `graph-scripts/backfill_onboarding_state.py --apply` (DRY-RUN default); both source lanes (Supabase jsonb → node; registry Team-node JSON-string → node, graph→graph); **absent-node-only reconciliation** (jsonb onboarding_complete=true → status='complete', one-directional; never clobber a node-present org's status; never jsonb-false → complete; never status → jsonb); fork stays null (J6 read-time default — persisted only on explicit opt-in); exclusions (placeholder teams.id='' + soft-deleted deleted_at non-null); re-run no-op. Wire stable across backfill/materialization/flip for completed grandfathered orgs (DE2E-6).
15. **Graph-down contract:** merged GET (READ) graph-down → 200 {operational keys unchanged, FLOW status:'unavailable'} · checkpoint/FLOW-bearing PATCH (WRITE) → 503 before any write (fail-loud, retry-safe) · operational-only PATCH → 200 (never touches the graph in Supabase mode). DM-2 "sweep reconciles flow keys from jsonb" is SUPERSEDED (recorded): no jsonb FLOW copy exists to reconcile; replacement = fail-loud + idempotent retry (divergence negative asserts retry-convergence).

## 4.5 Card, backup, tests
16. **Setup-guide card mirror** (W5 owns): server-driven canonical list + FLOW keys in the merged GET; pure JS `setupGuide.js` derivation (node --test, captureStatus.js pattern); N-of-M = |completed_steps ∩ card_subset| (capture-disclosed before decide must NOT render "4 of 4"); status-collapsed for compact/grandfathered; fork/compact-aware; DEGRADED (graph exception → 'unavailable') vs defaults (node absent) — never a false checklist; LOADING = client fetch transient (no new wire field); one shared component (MemorySources precedent); Python parity test (JS card-subset ⊆ canonical); `card-subset ⊆ canonical` unit assertion in the shared module.
17. **Backup-safe:** OnboardingState/OnboardingStep exported by default (NOT added to `_EXPORT_SKIP_LABELS` = {GraphEventMeta, TeamMeta, EpMeta}); restore node-count verification (dump-driven, same predicate) self-adjusts; restore round-trip test asserts fork/completed_steps survive. Orphan handling: create-on-write covers missing nodes; A-3 orphan sweep noted (alongside `_journal_append_product` — test-session-gated; prod = backup_sweep seam).
18. **MCP gate:** `_team_onboarding_complete` + `_onboarding_state` re-point to the projection (fail-open: graph-down → not-complete → tools stay; created-signal invalidates the 60s TTL cache on completion). Agent tool reads fork/status/completed_steps for W2's fork-known detection.

# 5. Test Strategy (surfaces 1/2/17)

- **Surface 1 (node)** — docker lane + unit: keyed-MERGE idempotency (replay 200 no-op, no version/completed_at bump); concurrent org-creates/checkpoint-writes on absent node → exactly ONE node; unknown step 422; card-subset ⊆ canonical; member_progress user-scoped + session-only + cross-org 403 negative; last_decide_attempt LWW + conditional; fork/compact set-once (same-value 200, changed 409); onboards edge asserted after seed; graph-DOWN → merged GET FLOW 'unavailable' + jsonb keys intact + gate fail-open; orphan (graph up, node absent) → defaults + no write; export/restore round-trip includes node + step edges; SDK-lane init assertion (node exists after sdk.team_create + every provision path).
- **Surface 2 (migration)** — docker lane + regression: backfill re-run no-op; grandfathered keep onboarding_complete (wire stable pre/post materialization + T7 flip + fork-opt-in); operational keys stay jsonb + still work (session-recording gate, probes, PATCH allowlist); envelope preserved; node-aware completion (4 wire cases: poisoned-new-org negative post-backfill, grandfathered pre/post, node-complete, node-less-new-org); PATCH wire-compat (underscore→hyphen, team_created strip); fork defaults at read, persisted only on opt-in; divergence negative (jsonb-ok/graph-fail → retry converges, no lost FLOW keys); **existing completion tests migrated to node-aware wire semantics at T7** (test_onboarding_integration.py, test_onboarding_health_flip.py, test_onboarding_endpoints.py — named); registration test split (round-trip vs declared-rejected vs accepted-dropped classes; FLOW-absent-from-jsonb negatives).
- **Surface 17 (cross-W E2E)** — hosted-e2e (RUN_HOSTED_E2E=1) + docker-lane: DE2E-1 (node version=1 + completed_steps match + onboards edge), DE2E-6 (legacy backfill setup), DE2E-12 (signup→org→fork→connect→seed→decide one sitting; fork-aware gates per self/build/compact; dismissal alone never completes; org B never re-asks the fork card) + wire cases (poisoned-new-org neg, grandfathered stable, node-complete, build-late-catalog, edge-function lane node materialization, forged keys — PATCH decide-completed 422 + checkpoint status 403 + mixed-PATCH partial/4xx pinned, legacy-dashboard-completion-noop).

# 6. Rejected Alternatives (recorded)

- **jsonb-only with fork/version keys:** agents write the graph directly (W2/W3 MCP); jsonb unreachable from the agent; flat dict can't hold per-user member_progress without the same RMW hazards; no W11 edge-new-creation signal; selfhost has no Supabase (DE2E-10).
- **Registry/control-plane placement:** the onboards edge → Organization Subject requires the data-plane graph (FalkorDB edges cannot cross graphs); the registry is pinned never-written by the edge function/agent_signup by design.
- **Stored `status` as the sole authority:** completed-step EDGES are canonical (WF-4 "not a second store"); status is the gate-written wire marker; within-graph precedence pinned (edges canonical for the card, status the wire marker; divergence transient accepted).
- **Sweep-based recovery / jsonb WAL:** the jsonb reconciliation source is empty by construction (FLOW keys never in jsonb); fail-loud retry satisfies the DM-2 divergence negative; a WAL would violate the no-FLOW-keys-in-jsonb invariant (outbox rejected per the dual-write literature's single-authority principle).
- **Literal "no lazy-init":** unachievable for no-TeamMeta lanes (agent_signup, tenant-provision edge fn) without breaking pinned no-graph contracts; replaced by write-time single-statement MERGE create-on-write (read-path materialization remains banned — the no-lazy-init invariant is READ-side).
- **v4 OR-union completion:** node-aware precedence (node present → node.status) is the faithful I-1 reading and kills the wizardComplete poisoned-true window regardless of W1 timing.

# 7. Wiring Check

| Touch Point | Type | Covered By |
|---|---|---|
| FalkorDB tenant graph (OnboardingState/OnboardingStep/edges/onboards) | DB | T1/T2/T4 + lane-coverage test |
| Supabase teams.onboarding_state jsonb (operational) | DB | preserved (raw reader + router) |
| GET /v1/onboarding/state | API | T3 (projection re-point) |
| PATCH /v1/onboarding/state | API | T4 (router retarget) |
| POST /v1/onboarding/state/checkpoint | API | T4 (new) |
| POST /v1/teams · /v1/register · /v1/onboarding/team · agent_signup · sdk.team_create | API/SDK | T2 (eager init + hooks + seam) |
| POST /v1/onboarding/session-recording · /v1/demo · /v1/sessions · github connect | API | unchanged (raw) |
| MCP gate (_team_onboarding_complete) + tortoise_onboarding_state | MCP | T3 (projection re-point + coercion) |
| Dashboard main.jsx (refreshOnboarding, wizardComplete, card) | UI | T3/T6 (card mirror; transient documented) |
| _EXPORT_SKIP_LABELS + restore verification | backup | no change (exported by default) + round-trip test |
| W11 (created-signal) · W3 (gate) · W9 (compact/fork) · W7 (member_progress) | cross-W | contract surfaces pinned (T4/T6/T7) |
| test seams (5 named suites) + registration test + 3 existing 409s | tests | T4/T7 (split + migration) |

# 8. Complexity (domain-aware)

| Domain | Rating |
|--------|--------|
| Architecture | complex |
| Ontology | standard |
| UX | low (card render contract; no new design language) |

# 9. Plan Comment Reference

The scoping comment posted to issue #2001 carries the Confirmed Problem, verification-gate record, plan summary, clarifications (none required — all decisions pinned by the epic plan), axis research (FalkorDB datatypes/concurrency/MERGE + dual-write precedence), rejected alternatives, wiring table, and complexity ratings. Implementation planning follows in `docs/plans/2026-08-30-2001-W5-onboarding-plan.md` (writing-plans + plan-review).
