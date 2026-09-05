<!-- research-path: docs/epics/2026-08-29-agent-driven-onboarding-1976/06-plan.md -->

# W3 Interactive Ontology-Precise Seed Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
> **Issue:** #1999 (W3 of epic #1976, agent-driven onboarding) · **Branch:** feat/1999-W3-onboarding

**Goal:** Ship the interactive ontology-precise seed — exactly two Subjects (Organization/organization + User/naturalPerson linked `memberOf`) with collision detection (never silent merge of distinct identities), person→naturalPerson normalization, no invented identity, an observable decide-completed/last_decide_attempt write path, and fork-aware completion (self = two Subjects + decide + connected; build defers decide to catalog-presented; compact = seed-lite org anchor + connected).

**Team:** epistemic-team

**Architecture:** A shared graph-agnostic seed core (`tortoise/onboarding/seed.py`, mirroring `state.py`'s no-hosted_api hygiene) owns the ontology vocabulary + collision classification + two-Subject MERGE + memberOf link. `tortoise/onboarding/state.py` gains the missing `write_onboards_edge` writer (node.org_subject_id + onboards edge → Organization Subject — the DM-1 node↔anchor link W5 declared "W3 seed writes"). `tortoise/hosted_api.py` gains the interactive `POST /v1/onboarding/seed` endpoint (dual-auth, auth-context anchor data: teams.name + JWT/user/email; multi-call gaps/collisions; writes only when fully resolved). `tortoise/mcp_server.py` + `tortoise/tool_registry.py` gain thin MCP wrappers (`tortoise_onboarding_seed`, `tortoise_onboarding_checkpoint`) in the onboarding group + retirement set so harness agents (W2's SKILL.md consumer) can drive the seed and record decide outcomes over MCP. Decide-attempt recording (LLM-503 → `last_decide_attempt:'failed'`, dismissal `'dismissed'`, success clears + `decide-completed` edge) rides the existing W5 checkpoint writers; W3 proves the fork-aware completion + retry-reachable semantics with docker-lane tests. Self-hosted two-prompt path stays W12-owned; the shared core is written reusable (pure functions take an SDK/graph handle).

### Pattern Research

- **Skipped external gate** — the plan touches zero third-party dependencies (FalkorDB/Cypher, FastAPI, SDK internals — all in-repo). Prior research intake: epic `06-plan.md` §1 J1/J2/J5/J7, §2 WF-2/WF-4, §4 DM-1/DM-3, §6 I-1/I-4, §7 DE2E-1/4/12; test-design `04-test-design.md` surfaces 1/7/8/14/15; W5 scope `docs/plans/2026-08-30-2001-W5-onboarding-scope.md` pins 8/12 (checkpoint surface + gate eval + onboards edge).
- **Subject MERGE-by-name mechanics** (codebase): `_upsert_subject` (projection/entities.py:271) MERGEs on `{name}` with `ON MATCH SET s.id=coalesce($id,s.id), s.subjectKind=coalesce($sk,s.subjectKind)` + `_persist_extra_props` `SET n += $extra` on MATCH — so `sdk.create_subject(name, kind, **refs)` is an idempotent MERGE-with-refs ON MATCH (canonical id preserved). Deterministic id = `sub-<sha256(name)>` (`_entity_name_id`). Collision check MUST run BEFORE create_subject — otherwise a same-name distinct identity would get OUR refs attached (silent identity merge).
- **Structural edges** (§3.6 ONTOLOGY.md): `memberOf` Subject→Subject canonical (member→org) via `sdk.create_edge(relation, from_id, to_id)`; structural edges stay plain (no operator).
- **Subject kinds** (ONTOLOGY.md §4.2/§5): organization, team, role, legalPerson, naturalPerson, other — free-string today (normalize legacy 'person', never validate-block, DM-3).
- **State writers** (state.py): `write_completed_step` FWW keyed-MERGE returns the W11 created-signal; `write_last_decide_attempt` LWW w/ conditional ('failed' skipped once decide-completed exists); `_maybe_apply_completion` (hosted_api) is the monotonic fork-aware gate eval — W3's seed/decide calls it post-write.
- **MCP onboarding-tool pattern** (mcp_server.py:2501+): tools are module-level functions calling hosted_api helpers in-process; registered via `tortoise/tool_registry.py` ToolDefinition + GROUP_BY_NAME + `_ONBOARDING_TOOL_NAMES` (retire on completion) + FastAPI RestSpec.

> **Findings date:** 2026-09-02

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | Tenant graph Subject writes (team_{id} via `_make_sdk(namespace=team_id)`) | DB (graph) | Write | Integration (docker lane) | org Subject {name, subjectKind:'organization', org_id}; person Subject {name, subjectKind:'naturalPerson', user_id?/email}; person−[:memberOf]→org | Wrong kind filed (Object/Statement — B1); silent merge (collision skipped) |
| 2 | OnboardingState node ↔ anchor link (org_subject_id + onboards edge) | DB (graph) | Write | Integration (docker lane) | state.py `write_onboards_edge` sets n.org_subject_id + MERGE [:onboards]→org Subject; idempotent | Link missing (compact gate depends on it); re-seed re-write |
| 3 | POST /v1/onboarding/state/checkpoint (decide record) | API | Write | Integration (docker lane) | step decide-completed (FWW); last_decide_attempt LWW enum; 503-failed distinct from dismissed; retry reachable | Forged completion; failed un-completes |
| 4 | POST /v1/onboarding/seed (new) | API | Write | Integration (docker lane) | dual-auth; auth-context team; gaps/collisions → 200 no-write; names explicit → two Subjects + memberOf + step edge + onboards + gate eval; idempotent replay | Invented identity; writes before confirmation; silent merge |
| 5 | MCP tools (tortoise_onboarding_seed, tortoise_onboarding_checkpoint) | MCP | Write | Unit + mcp_http seam | onboarding group; retire on completion; delegate to hosted in-process helpers | Tool listing drift; completion-gated incorrectly |
| 6 | Fork-aware completion gate | State | — | Unit + integration | self = 2 Subjects + decide + connected; build = + catalog-once; compact = seed-lite + connected; dismissal alone never completes | complete set without gate |

**Bug Pattern Flags**
- **Silent function skips (HIGH):** completion without decide (dismissal path) → monotonic gate eval tests; `onboarding_complete` never set by seed alone on self fork.
- **Silent merge (HIGH):** collision check before MERGE — never attach refs to a distinct same-name identity; same-name ours (refs match) → reuse (idempotent replay), distinct → disambiguation surfaced.
- **Conditional guards (MEDIUM):** person-name email-derived → confirmation before write; include_person=not compact.

**Checklist Notes**
- **Idempotency:** seed re-run after success → same canonical ids, memberOf + onboards MERGE no-op, first-points-filed replay no-op (200).
- **Atomicity:** seed writes only when names fully resolved (gaps/collisions → zero writes); per-graph write serialization (FalkorDB) + per-org lock.
- **Boundary values:** no email (no derivation possible) → gap ask; empty/whitespace names rejected; unknown Subject kind never filed.

### Journey Test Map

### Journey: First-run one-sitting (DE2E-1/12 leg, docker) — register → fork → connect → seed → decide → complete
1. **Step:** register team → **Acceptance:** node exists (team-named) → **Test:** test_onboarding_state_split.py (existing)
2. **Step:** fork=self checkpoint → **Acceptance:** fork persisted set-once → **Test:** (existing)
3. **Step:** harness-connected checkpoint → **Acceptance:** step edge created → **Test:** (existing)
4. **Step:** POST /v1/onboarding/seed (email-derived person name) → **Acceptance:** 200 needs_confirmation, ZERO graph writes → **Test:** TestSeedEndpoint::test_email_derived_person_requires_confirmation
5. **Step:** POST /v1/onboarding/seed {person_name} → **Acceptance:** exactly 2 Subjects (organization/naturalPerson) + memberOf + onboards edge + org_subject_id + first-points-filed; status still active (self fork needs decide) → **Test:** TestSeedEndpoint::test_seed_files_two_subjects_member_of
6. **Step:** decide-completed checkpoint → **Acceptance:** status complete; wire onboarding_complete true → **Test:** TestSeedEndpoint::test_decide_completes_self_fork
7. **Step:** LLM-503 replay → **Acceptance:** last_decide_attempt 'failed' recorded; completed decide never regresses; retry reachable (edge idempotent) → **Test:** TestSeedEndpoint::test_llm503_decide_attempted_failed_distinct_from_dismissed

### Journey: Ontology-precise seed (DE2E-4) — collision + normalization + B1
1. **Step:** same-name distinct org Subject pre-filed → seed → **Acceptance:** collision status (no write, no silent merge) → **Test:** TestSeedEndpoint::test_same_name_collision_never_silent_merge
2. **Step:** pre-existing legacy person (subjectKind 'person') same email → seed → **Acceptance:** normalized to naturalPerson on MATCH, reused id → **Test:** TestSeedOntology::test_person_normalized_to_natural_person
3. **Step:** assert anchors never Object/Statement → **Acceptance:** exactly 2 Subject nodes; 0 Object/Statement with the anchor names → **Test:** TestSeedOntology::test_never_object_or_statement

### Journey: Build + compact forks
1. **Step:** fork=build → seed both + catalog-presented checkpoint → **Acceptance:** complete WITHOUT decide → **Test:** TestSeedEndpoint::test_build_fork_defers_decide_to_catalog
2. **Step:** compact org → seed (org anchor) → **Acceptance:** seed-lite completes on first-points-filed + connected; person not required → **Test:** TestSeedEndpoint::test_compact_seed_lite

### Failure Modes
- Same-name distinct identity → **Expected:** 200 {status: collision} + zero writes (never silent merge) → **Test:** test_same_name_collision_never_silent_merge
- Email-derived person name unconfirmed → **Expected:** needs_confirmation gap, no write → **Test:** test_email_derived_person_requires_confirmation
- LLM 503 at decide → **Expected:** last_decide_attempt 'failed' (distinct from dismissed); decide-completed never regresses; retry reaches complete → **Test:** test_llm503_decide_attempted_failed_distinct_from_dismissed
- Dismissal alone → **Expected:** never completes → **Test:** test_dismissal_alone_never_completes
- Re-seed replay → **Expected:** canonical ids stable, steps noop → **Test:** test_seed_replay_idempotent

**Tech Stack:** Python 3.12 (FastAPI, FalkorDB via existing SDK/projection), pytest (docker lane), MCP (FastMCP + tool_registry).

---

## Task 1: Shared seed core — `tortoise/onboarding/seed.py`

**Intent:** ONE graph-agnostic seed module (no hosted_api import) — ontology constants, pure normalization/derivation helpers, collision classification, and the two-Subject + memberOf seed performed via a duck-typed SDK (create_subject/create_edge/query), so hosted (this issue), MCP (this issue) and self-hosted W12 all consume identical ontology semantics.
**Acceptance:** Module imports without hosted_api; exports `normalize_person_kind` (person→naturalPerson, else unchanged), `derive_display_name_from_email` (title-cased local-part; None for no/empty local part), `SubjectCollision` exception, `find_subject_by_name`, `is_own_subject`, `seed_onboarding_anchors(sdk, *, org_name, person_name, org_id, user_id=None, person_email=None)` returning `{org_subject, user_subject, member_of, org_created, person_created, org_kind_normalized, person_kind_normalized}`; raises `SubjectCollision` on a same-name subject that is NOT this org/user (never silently merges); person-kind normalization happens on MATCH (existing ours person → naturalPerson); anchors are Subject nodes only (never Object/Statement — no such code path exists).
**Files:**
- Create: `tortoise/onboarding/seed.py`
- Create: `tests/test_onboarding_seed.py` (lane-agnostic unit tests — module hygiene, pure helpers, collision classification on an in-memory fake graph/SDK)

**Steps:**
1. Write the failing unit tests (module hygiene — no hosted_api import; normalize_person_kind; derive_display_name_from_email boundary cases; is_own_subject ours-vs-collision matrix; fake-SDK seed: 2 Subjects + memberOf + normalization-on-match + collision raise).
2. Run to verify fail — `uv run pytest tests/test_onboarding_seed.py -x` → FAIL (module missing).
3. Implement the module.
4. Run to verify pass.
5. Commit (via commit-workflow).

## Task 2: OnboardingState writer — onboards edge + org_subject_id (`state.py`)

**Intent:** Add the missing DM-1 node↔anchor writer W5 declared for W3: `write_onboards_edge(graph, org_id, subject_id)` sets `n.org_subject_id` and MERGEs `[:onboards]→(:Subject {id})`; returns `{created, subject_id}` (edge-new signal for W11). Idempotent (replay no-op; same subject_id re-SET harmless).
**Acceptance:** Writer exists; docker-lane test: after seed, node.org_subject_id == org Subject id and the onboards edge resolves; re-write replay does not duplicate edges (created=False).
**Files:**
- Modify: `tortoise/onboarding/state.py`
- Test: extend `tests/test_onboarding_state_split.py` (docker lane) — `TestOnboardsEdge`

**Steps:**
1. Write the failing docker-lane test (register → direct writer call → assert node.org_subject_id + edge; replay → created False).
2. Run to verify fail.
3. Implement `write_onboards_edge` (create-on-write seam first, per-org lock, `_relations_created` signal).
4. Run to verify pass.
5. Commit.

## Task 3: Hosted seed endpoint — `POST /v1/onboarding/seed` (hosted_api.py)

**Intent:** The interactive hosted seed write path. Auth-context anchor data (teams.name, team email, session_user_id/created_by); explicit names win; email-prefix derivation flagged for confirmation; collision → disambiguation surfaced; writes ONLY when fully resolved: two Subjects via the Task-1 core + memberOf + `write_onboards_edge` + `first-points-filed` step edge (created-signal) + gate eval; include_person = not node.compact (seed-lite); response carries merged onboarding projection + next-step hint (decide for self fork — the nudge trigger; catalog for build).
**Acceptance:** `POST /v1/onboarding/seed` dual-auth; `{}` → needs_confirmation with derived person name (no writes); `{person_name}` → seeded: 2 Subjects + memberOf + onboards + first-points-filed; same-name distinct Subject → 200 collision (no writes); compact org → org-anchor-only seed; replay idempotent; gate eval runs post-write (self stays active until decide; build completes on catalog; compact completes when connected).
**Files:**
- Modify: `tortoise/hosted_api.py` (seed runner `_run_onboarding_seed` + endpoint; `_team_name` control-plane read helper; supabase_control `team_name` seam)
- Test: `tests/test_onboarding_seed_endpoint.py` (docker-lane module-level skip guard)

**Steps:**
1. Write the failing docker-lane endpoint tests (TestSeedEndpoint class).
2. Run to verify fail.
3. Implement `_team_name` + `team_name` seam; the seed runner + endpoint.
4. Run to verify pass.
5. Commit.

## Task 4: MCP surfaces — `tortoise_onboarding_seed` + `tortoise_onboarding_checkpoint` (mcp_server.py + tool_registry.py)

**Intent:** Thin MCP onboarding tools so harness agents (W2 SKILL.md consumer) drive the seed + record decide outcomes over MCP. `tortoise_onboarding_seed(org_name=None, person_name=None)` delegates in-process to the hosted seed runner (session_recording pattern). `tortoise_onboarding_checkpoint(step=None, fork=None, last_decide_attempt=None)` delegates to the checkpoint write path (harness-connected / first-points-filed / decide-completed / last_decide_attempt) — the decide protocol's success/failure record surface.
**Acceptance:** Both tools registered in the onboarding group + `_ONBOARDING_TOOL_NAMES` retirement set + GROUP_BY_NAME; HTTP tool filter gates them on completion (fail-open); existing tool registry tests pass.
**Files:**
- Modify: `tortoise/tool_registry.py` (2 ToolDefinitions + RestSpec)
- Modify: `tortoise/mcp_server.py` (handlers + `_ONBOARDING_TOOL_NAMES` + cache invalidation on complete)
- Test: extend `tests/test_tool_registry.py` / onboarding-tool listing assertions

**Steps:**
1. Write failing registry tests (new tools listed under onboarding group; retirement-set membership).
2. Implement.
3. Run docker-lane + registry tests.
4. Commit. **NOTE: Tier-1 full matrix (tool_registry.py + mcp_server.py touched).**

## Task 5: Cross-W docker-lane E2E leg + journey tests (surfaces 1/7/8/14)

**Intent:** Prove DE2E-1/4/12's server-side slice over HTTP + graph: full self-fork journey (register→fork→connect→seed→decide→complete), collision + normalization + B1 (never Object/Statement), build defers decide to catalog, compact seed-lite, dismissal never completes, LLM-503 semantics, seed replay idempotency, W11 created-signals observable on first-points-filed/decide-completed edges.
**Acceptance:** Docker-lane suite green: exactly 2 Subjects (organization + naturalPerson) + memberOf + onboards; step edges + created signals; fork-aware completion per fork; decide-attempted-failed distinct from dismissed; retry reachable.
**Files:**
- Test: `tests/test_onboarding_seed_endpoint.py` (journey classes, docker-lane guard)
- Modify: `config/ci-surfaces.yml` (register `test_onboarding_seed.py` + `test_onboarding_seed_endpoint.py` under `onboarding`)

**Steps:**
1. Write the remaining journey/failure tests (already scaffolded in Task 3's file — complete the classes).
2. Full docker-lane run of the onboarding suite + carve-out run of the new unit file.
3. Ruff on changed files.
4. Commit (final, via commit-workflow).

## Task Template Fields (executing-plans handoff)

**Key constraints honored:**
- `tortoise/onboarding/seed.py` mirrors state.py hygiene (importable without hosted_api; graph-agnostic; SDK duck-typed for W12 reuse).
- Seed subjects ride the canonical SDK create_subject/create_edge (SubjectAdded events + journal + embedding + #452 name-MERGE) — never raw ad-hoc Cypher for entity creation.
- Collision check runs BEFORE any MERGE-with-refs; a distinct same-name identity raises SubjectCollision (disambiguation surfaced by caller); NEVER silent merge (DM-3 P1 fix).
- Anchors are Subject nodes only — no Object/Statement path exists in the core (B1 regression).
- Legacy subjectKind 'person' → normalized to 'naturalPerson' on MATCH (never validate-block; DM-3).
- Seed/decide completion observable: first-points-filed + decide-completed step edges (FWW created-signal = W11's hook), org_subject_id + onboards edge (DM-1), last_decide_attempt LWW.
- Gate eval is monotonic + fork-aware (self/build/compact) via the existing `_maybe_apply_completion`; decide is NOT required for build (catalog-presented) or compact (seed-lite + connected).
- Self-hosted two-prompt UI + prompts are W12's (#2007); the shared seed core is the reusable half (surface 15 seed half).
- MCP tools: onboarding group + retire-on-completion (existing mechanism); thin in-process delegates.
