---
title: "#1714 codebase explorer — scout report"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-25
subjects.team: epistemic-team
aboutObjects: tortoise-memory-capture, tortoise-onboarding
---

# Codebase Explorer — Issue #1714 (scout report, feeds solution-diverge)

All paths relative to worktree root; line refs approximate (re-anchor at plan time against the current commit).

## Deliverable 1 — GitHub ingestion rebuild

- `tortoise/indexer/github_indexer.py` (129 lines): `:121` `create_point(kind="observation", ..., props)` — no dedup, removed kind, no externalId, no entity links. httpx REST fetch layer exists (`:23-66` rate-limit/pagination) — the REST fetch candidate.
- `tortoise/hosted_api.py`: `:8108` `_INDEX_JOBS` in-process; `:8116-8155` `_run_indexing` (ZERO quota calls — ungated); `:8156-8176` POST /v1/index/github; `:8178-8190` GET job poll.
- `tortoise/connectors/github.py` (568 lines, ORPHANED): ontology-correct path — `_issue_to_entities` `:200` (Object pm:issue, Event pm:cardCreated/Completed, Subjects, aboutSubject; entity_id `github-issue-{repo}-{n}` `:228`), `ingest(proj)` `:300`, `_issue_to_event` `:490` (eventKind github.issue.{state}, shared eventId pinned by test_github_connector.py:343), `_pr_to_event` `:530`, `_webhook_to_event` `:555`. **Fetch = gh CLI subprocess ONLY** (`:129,:150,:175`) — no REST. Runs only via `pipeline_cli.py:99-164`.
- `tortoise/projection/entities.py` (977 lines): `_upsert_event` `:474-547` (MERGE by eventId, auto structural edges, `_materialize_connector_source`), `:695-780` `_materialize_connector_source` (#388 never-overwrite), `:791-800` sourceObjectId → (Source)-[:references]->(Object). **Produces Objects/Events/Sources — NO statement Points from issue content** (net-new per amend 4).
- `tortoise/sdk.py`: `:1539` dedup default False; `:1540-1595` dedup matches content_hash+pointKind; `:1494-1670` create_point accepts explicit `id` (deterministic ids — LME pattern `_pid = pt_{content_hash[:62]}` in test_lme_ingest_v2_supersession.py); `:2741-2758` supersede(); `:2759-2910` supersede_point (CORRECTS + outdated + edge transfer + validFrom/validTo bi-temporal); `:2700-2736` invalidate_point (no edge transfer — the "content was true while open" path); `:11821` create_event().

## Deliverable 2 — GitHub docs extraction

- `tortoise/file_indexer.py`: `:86-97` compute_file_hash (SHA-256), `:189` derive_source_url, `:172-186` escape rejection (realpath containment), `:327` derive_document_id, `:349` classify_file, `:421` source_kind_for_classifier.
- `tortoise/tool_registry.py:565-571` tortoise_ingest_corpus: http_policy=False (EXCLUDED from tenant HTTP — the #236 exclusion).
- #236 layers: mcp_server.py:1582-1586 (http transport → excluded error), sdk.py:8723-8750 ingest_dir_is_safe + TORTOISE_INGEST_BASE_DIR sandbox, .env.example:298-302.
- **Hosted remote-docs: NO existing path.** Design: Contents-API fetch → server-side staging dir (under ingest base, exempt from user-supplied-path by construction) → internal ingest_corpus. Token scope `repo` (:8018) suffices.

## Deliverable 3 — Session capture + ask

- `tortoise/hosted_api.py` /v1/sessions `:3960-4230`: gate order provider 503 → turn cap 400 → empty 422 → quota 402 → team limit. `:4062-4066` Session MERGE: id/created_at/turn_count/is_episodic — **NO harness field** (net-new). Turn points `{session_id}_t{i}` `:4105-4122`; v2/M2 LLM extraction `:4140-4158`; sessionCaptured Event `:4168-4183`; agentSession Source `:4206-4218`. `/v1/sessions/commit` (derived, #909) exists `:4236+`.
- Onboarding state: `_ONBOARDING_DEFAULT_STATE` `:7556` (session_recording False), `_ALLOWED_STATE_KEYS` `:7565`, `_get/_write/_update_onboarding_state` `:7571-7636`, `OnboardingStatePatchRequest` `:7652-7665`, GET/PATCH `:7666-7697`, POST /v1/onboarding/session-recording `:7699-7712` (FLAG-ONLY). github connect `:7999-8025`, callback `:8027-8088`, status `:8090-8106`.
- **jsonb state → NO migration for new fields** (0006_teams.sql:43 onboarding_state jsonb): just default + allowed-keys + PATCH model. **Session harness field = extend 2 MERGEs (hosted_api.py:4062, sdk.py:1973), graph-side, no migration.**
- mcp_server.py: onboarding tools retire post-completion `:2351-2364`; `tortoise_onboarding_session_recording` `:2437-2445` (flag-only); github connect/status/index `:2447-2505`.
- AGENT_ONBOARDING.md: Q3 flag-only false promise `:76-88`; Q5 docs honest stdio note `:112-130`; tool dependency table `:256-263`. Variants: tortoise/onboarding/variants/{pi,claude-code,codex,cursor}-header.md.
- **`tortoise/claude-hooks/session-end.sh` (147 lines) + `session-start.sh` (36 lines) — T1 for Claude Code ALREADY SCRIPTED in-repo** (#564): end hook converts .jsonl transcript → text turns → `tortoise session capture` (hosted /v1/sessions); start hook injects memory digest; install = cp hooks + .claude/settings.json SessionEnd/SessionStart entries; always exits 0. NOT wired into any wizard copy.
- website/apps/dashboard/src/main.jsx (3106 lines, THE real source — Agent B's "shell" claim refuted): wizardSteps `:482`; wizardConnectGithub `:502-545` (connect + status poll only, NO index trigger); false copy "issues come in as Events" `:2350,:2439`; step-1 render `:2443-2465`. harnesses.js: HARNESS_INSTALL per harness (T1 install-step insertion point), HARNESS_STEPS (claude-web connector steps), HARNESS_ORDER.
- dist/ IS committed (rebuild + commit needed for wizard copy changes).

## Deliverable 4 — Quota

- tortoise/quota.py: MAX_SESSION_TURNS 500 `:94`, DEFAULT_MAX_SESSIONS 1000 `:118`, count_team_usage `:267` (points = non-episodic only `:394-401`), enforce_team_limit `:381`. hosted_api.py `_check_team_limit` `:1487-1520` (402). Sessions gated `:4029-4039`; index ungated `:8116-8155` (the asymmetry).

## Patterns
- Quota-gate an endpoint: _check_team_limit (request-time). Index job is a BACKGROUND task — needs explicit limit resolution before first create_point.
- Tests: tests/test_github_indexer.py FakeSDK masking `:38-52` (DELETE the fake; use real _embedded SDK per test_github_connector.py); TORTOISE_SESSION_LLM_MOCK=1 seam (test_capture_session.py); test_onboarding_endpoints.py PATCH pattern; test_lme_ingest_v2_supersession.py (supersession model).

## Dependencies
- #909 OPEN — /v1/sessions contract partially moving; consume as black box.
- LLM provider required for /v1/sessions (503 otherwise) — surfaced honestly in the ask.
- GITHUB_CLIENT_ID/SECRET env; token scope repo.
- TORTOISE_INGEST_BASE_DIR sandbox for staged docs.
- Pi T1 = extensions OUTSIDE repo (~/.pi/agent/extensions/reflect-hook.ts, tortoise-capture/) — install steps are copy/instructions; hosted 2xx leg unproven.
