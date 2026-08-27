---
title: "#1714 Memory-Capture Onboarding — implementation plan"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-25
subjects.team: epistemic-team
aboutObjects: tortoise-memory-capture, tortoise-onboarding
---

<!-- research-path: docs/research/1714-solution-converge.md -->

# Memory-Capture Onboarding (issue #1714) Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
> **⛔ Read BOTH Plan Review Incorporations sections (Cycle-1 AND Cycle-2) at the bottom BEFORE executing any task** — they amend Tasks 2/3/4/5/8/9/11/12/13/14/16/17/18/19 and the execution order. **Cycle-3 body-folding (below in each task body) is already applied** — the task bodies are authoritative. Decomposition must keep the folded bodies.

**Goal:** Repair Tortoise's GitHub ingestion baseline (keyed, Events-as-truth, entity-linked, quota-fair), add remote GitHub-docs extraction, wire per-harness agent-session capture with a server-enforced consent gate, and deliver the honest memory-capture ask in the wizard + self-hosted prompt — mechanisms first, promise after.

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Family 3 — a shared stateless `tortoise/github_map.py` owns all GitHub ontology mapping (single eventId/eventKind vocabulary, statement extraction, lifecycle diff); `github_indexer.py` reworks in place into fetch+diff → project; `connectors/github.py` mappers become thin wrappers. Session capture uses the existing `/v1/sessions` black box (#909 boundary) plus a new MCP tool and a **server-enforced consent gate (403)**. All onboarding state rides jsonb (no migrations); `Session.harness` is graph-side. The wizard ask is off-by-default and mechanism-gated. 4 independently shippable slices, 17 TDD tasks + 2 config/sweep tasks (Tasks 10, 19). Contracts pinned in `docs/research/1714-solution-converge.md` (read before implementing — it carries the 16 amendments + Phase 7 incorporations).

### Pattern Research

**Skipped — plan touches zero third-party dependencies.** All mechanisms are in-repo: httpx fetch (`github_indexer.py:23-66`), SDK primitives (`create_point` explicit-id, `supersede_point`, `create_event`, `_get_proj`), projection (`_upsert_event`, `_materialize_connector_source`), MCP tool registration (`tool_registry.py` `http_policy` pattern), corpus pipeline (`file_indexer.py`). GitHub REST is an existing integration (no new SDK). Cursor storage determination is an in-slice research task (spike), not a dependency.

### Integration Surface Map

| Surface | Test Layer | Bug Pattern Flags | Verification |
|---|---|---|---|
| `github_map.py` (pure mapper) | unit | eventId/eventKind drift, externalId collision | eventId equality; externalId uniqueness; lifecycle diff; gh-CLI-shaped input |
| Indexer fetch+diff (hosted job) | unit + integration | sort-order blindness, cap truncation, cursor staleness | sort=updated pinned; "N beyond window" in status; re-run ⇒ 0 new nodes |
| Lifecycle writes (Events/status/statements) | integration (real embedded SDK) | CORRECTS-on-close amnesia, edit-revert tombstone, EP churn | close ⇒ Event+status only; revert ⇒ v3 current; `updatedAt` unchanged on re-run |
| Quota gate (index + docs) | unit + integration | vacuous gate (Document vs Point), cap blowout | 402 at cap; ONE-repo bounded first-run |
| `/v1/index/docs` + staging | integration | #236 user-path traversal, unset-base | fail-closed when base unset; staging under base only |
| `/v1/sessions` consent 403 | integration | consentless exfiltration (prompt injection) | un-opted POST ⇒ 403; opted ⇒ 200 |
| `Session.harness` + receipts + entity links | integration | harness vocab drift, silent link failure | harness Literal validated; link counters tracked |
| `tortoise_session_capture` MCP tool | contract + integration | gate bypass, stdio behavior | registered; same 403/402/503 gates; stdio honest error |
| Wizard step-1 + Memory sources panel | e2e (RUN_DASHBOARD_E2E) | false copy, toggle state machine, dangling poll | copy honest; toggles persist; bounded poll; re-ask once |
| `AGENT_ONBOARDING.md` Q3 | prompt e2e/manual | false-promise phrasing | drafted copy; grep clean; parity table |
| Claude Code hooks + Pi extension | ops/e2e | exit-0 violation, receipt missing | hook smoke; Pi 2xx leg observed |

### Journey Test Map

### Journey: First-timer opts into memory capture
1. **Step:** Sign up → provisioned (team+key) → wizard step 0 harness → **Acceptance:** key revealed once, harness chosen → **Test:** `test_onboarding_integration.py` (exists, extended)
2. **Step:** Step 1 "Memory sources" — connect GitHub → **Acceptance:** auto-index starts (bounded), honest "work items + lifecycle" copy; docs toggle-on reveals "Index docs" → `POST /v1/index/docs` job runs (Document-cap, fail-closed) → **Test:** `test_github_index_lifecycle.py::test_auto_index_after_connect` + `test_index_docs_api.py` + docs-leg dashboard e2e
3. **Step:** Toggle "Agent sessions" → **Acceptance:** consent flag set; toggle reflects per-harness status (install-probe-driven); web row disabled-with-reason until the Task 13 spike confirms a filing path → **Test:** `test_onboarding_endpoints.py` + dashboard e2e
4. **Step:** Session ends in Claude Code → **Acceptance:** hook fires, POST 200 (opted), Session.harness + receipt + entity links → **Test:** `test_capture_session.py::test_session_harness_and_links`

### Journey: Misled returning user (Q3 flag was set pre-fix)
1. **Step:** Opens dashboard (has points) → **Acceptance:** re-ask appears on Memory sources panel (once) → **Test:** `test_onboarding_integration.py::test_misled_user_reask_once`
2. **Step:** Answers → **Acceptance:** `capture_revised` set; no re-ask on next visit; Q3 skips in next prompt run → **Test:** `test_onboarding_endpoints.py::test_capture_revised_dedup`

### Failure Modes
- Un-opted team POSTs session → **Expected:** 403, no write → **Test:** consent-gate TDD (Slice 2 task)
- Re-index of edited issue → **Expected:** supersede (CORRECTS) + bi-temporal window, 0 dupes → **Test:** `test_github_indexer.py::test_edit_supersedes`
- Close/reopen cycle → **Expected:** Events + status projection, statement points untouched → **Test:** `test_github_indexer.py::test_close_no_point_mutation`
- Docs job with unset `TORTOISE_INGEST_BASE_DIR` → **Expected:** honest job failure, no writes → **Test:** `test_index_docs_api.py::test_unset_base_fails_closed`
- Legacy-True-consent team POSTs (pre-resolution) → **Expected:** grandfathered 200; after decline (re-ask NO / Q3 no) → 403, existing Sessions untouched → **Test:** `test_capture_session.py::test_decline_clears_consent_403` + Journey-2 tests

**Tech Stack:** Python 3.12 (tortoise SDK, httpx), FalkorDB (test: Docker lane), Supabase jsonb (onboarding state), React/Vite dashboard, bash hooks, GitHub REST.

---

## Slice 0 — Ingestion baseline repair (root)

### Task 1: `github_map.py` — the shared stateless mapper

**Intent:** Single source of truth for GitHub ontology mapping (the #1155 normalization): one eventId/eventKind vocabulary, statement extraction, lifecycle diff.
**Acceptance:** `github_map.py` exports `issue_to_object`, `issue_to_event` (eventId `github-issue-{repo}-{n}-{event}`, event ∈ {created,closed,reopened}; eventKind `github.issue.{state-or-action}`), `issue_to_subjects`, `pr_to_event`, `issue_to_statements` (externalId `github:issue:{repo}#{n}`, id `pt_gh_{repo}_{n}_{sha256(content)[:12]}_{v}` monotonic, aboutObject/extractedFrom), `diff_lifecycle`. Never emits `observation` or `github_state` props.
**Files:**
- Create: `tortoise/github_map.py`
- Test: `tests/test_github_map.py`

**Step 1:** Write `tests/test_github_map.py` — eventId equality (creation `-created`; transitions `-closed`/`-reopened`); eventKind values {open,closed,reopened}; externalId uniqueness per issue; statement props = `{externalId, extractedFrom, source, github_repo, github_number, github_url}` ONLY with `github_state IS NULL` asserted; gh-CLI-shaped input (field-name/casing) maps identically; `diff_lifecycle` open→closed→reopened.
**Step 2:** Run `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_github_map.py -v` — FAIL (module absent).
**Step 3:** Implement `tortoise/github_map.py` (pure functions; map gh-CLI + REST shapes to the same canonical dicts).
**Step 4:** Run test — PASS.
**Step 5:** Commit `feat(1714): shared GitHub ontology mapper`.

### Task 2: Indexer rework — fetch+diff (sort=updated, cursor, honest truncation)

**Intent:** Make the hosted index job cursor-correct at org scale (the 1,700-issue reality) and honest about truncation.
**Acceptance:** `github_indexer.py` fetches `sort=updated&direction=desc`, persists a per-repo **composite `(updated_at, number)` cursor** (onboarding jsonb — REGISTERED in `_ONBOARDING_DEFAULT_STATE` + `DEFAULT_ONBOARDING_STATE` + `_ALLOWED_STATE_KEYS`, else the filter silently drops it), reports "N issues beyond window" in job status; per-run cap parameterized. **Resume semantics pinned (cycle-3 P1-4):** `since = cursor.updated_at − 1s` + skip items with `number ≤ cursor.number` at the boundary (strict `since` alone leaves a permanent gap when run 1 is cap-truncated mid-boundary-second); mid-walk 401/429 ⇒ honest "failed" status with readable error, cursor NOT advanced past unprocessed items, re-run resumes without gaps/dupes, bounded retry-with-backoff on 429/5xx (cycle-3 P1-4 + T1-P13).
**Files:**
- Modify: `tortoise/indexer/github_indexer.py:23-66` (fetch params + cursor + cap)
- Modify: `tortoise/hosted_api.py:8116-8155` (`_run_indexing` cursor plumbing + status)
- Test: `tests/test_github_index_lifecycle.py`

**Step 1:** Write failing tests — fetch URL carries `sort=updated&direction=desc` (mock transport asserts query); cursor persists and stops the walk; truncation count in job status.
**Step 2:** Run — FAIL.
**Step 3:** Implement fetch params, cursor read/write (jsonb key `github_index_cursor`), cap parameterization, status field.
**Step 4:** Run — PASS.
**Step 5:** Commit `feat(1714): cursor-correct GitHub fetch with honest truncation`.

### Task 3: Lifecycle writes — Events-as-truth, never CORRECTS on close

**Intent:** Closed/reopened = Event + Object.status projection ONLY; statement points content/status-untouched; `invalidate_point` never called.
**Acceptance:** REWRITTEN `tests/test_github_indexer.py` (real embedded SDK, FakeSDK deleted): re-run ⇒ 0 new nodes; edit ⇒ supersede (CORRECTS, bi-temporal); close ⇒ Event `github.issue.closed` + `Object.status=completed`, **no point mutation**; reopen ⇒ status `open`; first-ingest of already-closed ⇒ `-created` only; edit→supersede→revert ⇒ v3 current, no error. **Legacy `-closed` backfill (T1-P1 + T2-P3, folded):** ONE-TIME — runs only while the `github_legacy_backfill_done` marker (REGISTERED state key) is absent; scans PRE-EXISTING `-created`(closed-kind/`endedAt`) Events ONLY (before minting fresh ones); mints `-closed`; sets the marker. Normal diff NEVER mints `-closed` for closed-without-`-closed` on re-runs (one-time backfill is the only source). TDD asserts **no double-mint on fresh first-runs**. If rejected → qualification recorded in the commit + task notes (deliver-or-defer owner).
**Files:**
- Modify: `tortoise/indexer/github_indexer.py` (Phase 2 write path via `github_map` + `sdk._get_proj().apply` for entities/events + `create_point(kind="statement", id=…, dedup=False, props=…)` two-phase)
- Modify: `tortoise/sdk.py` — no changes expected (primitives exist); verify dedup-probe-without-props path
- Test: rewrite `tests/test_github_indexer.py` (delete FakeSDK `:38-52`)
- Test: `tests/test_github_index_lifecycle.py` (revert case)

**Step 1:** Write failing tests (red: current code duplicates on re-run with `observation`).
**Step 2:** Run — FAIL (proves the baseline bug).
**Step 3:** Implement write path: probe without props → create with props on miss; lifecycle events via `create_event`/projection; statement version bump on edit; revert ⇒ v+1.
**Step 4:** Run full docker lane `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_github_indexer.py tests/test_github_map.py -v` — PASS; `updatedAt` byte-unchanged on unchanged re-run asserted.
**Step 5:** Commit `fix(1714): lifecycle-aware, keyed GitHub ingestion (Events-as-truth)`.

### Task 4: Quota fairness + bounded first-run

**Intent:** The index job gates like sessions do; auto-index cannot exhaust the team cap.
**Acceptance:** `_run_indexing` preflights `enforce_team_limit("points")` + per-batch re-check; first-run scope = ONE repo (pre-decided fallback) with "index more" affordance; job 402s honestly at cap. **Per-team single-flight (T2-P2 + cycle-3 P1-3, folded):** `_INDEX_JOBS` entries carry `team_id` + `started_at`; ordered algorithm = (1) guard-check FIRST — a `started` entry for the team is REUSED (return its job_id); (2) evict terminal entries or `started` older than the 30-min TTL (presumed-dead — a hung run never bricks the team; the just-reused in-flight entry is never evicted); (3) single-process assumption recorded (DB-backed lock only if Fly scales horizontally). TDD: `test_in_flight_single_flight_reuses` + `test_stuck_started_evicted`.
**Files:**
- Modify: `tortoise/hosted_api.py` (`_run_indexing` + `GitHubIndexRequest`)
- Test: `tests/test_github_index_lifecycle.py::test_quota_honest_fail` + `test_first_run_single_repo`
- Test: `tests/test_quota.py` (extend)

**Step 1:** Write failing tests (job writes past cap today).
**Step 2:** Run — FAIL.
**Step 3:** Implement preflight + per-batch gate + ONE-repo default + partial-completion status.
**Step 4:** Run — PASS.
**Step 5:** Commit `feat(1714): quota-fair GitHub indexing with bounded first run`.

### Task 5: Auto-index-after-connect + re-poll endpoint

**Intent:** Connect fires the first index (quota-gated); lifecycle stays live via diff-on-poll re-run.
**Acceptance:** `github_callback` (hosted_api.py:8027) enqueues `_run_indexing`; `POST /v1/index/github/re-poll` (the ONLY route shape — no query-param alternative) re-runs the diff; dashboard re-index button wired in Task 17.
**Files:**
- Modify: `tortoise/hosted_api.py` (callback + re-poll route)
- Test: `tests/test_github_index_lifecycle.py::test_auto_index_after_connect`

**Step 1:** Failing test — connect → index job appears.
**Step 2:** Run — FAIL.
**Step 3:** Implement.
**Step 4:** Run — PASS.
**Step 5:** Commit `feat(1714): auto-index after GitHub connect + re-poll`.

### Task 6: Connector wrappers + #1155 normalization + blast radius

**Intent:** `connectors/github.py` mappers become thin wrappers over `github_map`; the divergence note is deleted; downstream pm:card* consumers enumerated.
**Acceptance:** connector eventIds byte-identical (poll path); `test_github_connector.py:419` re-pinned to `("…-created", "github.issue.open")` (named, reviewed); `test_producers_share_event_id` extended to full eventKind+subject equality; `config/pipelines.yaml:17-18,47-48` + `graph-scripts/setup.py:932-933` kinds updated; external-consumer grep recorded in the migration note.
**Files:**
- Modify: `tortoise/connectors/github.py` (mappers → wrappers; delete :231-242 note)
- Modify: `config/pipelines.yaml`, `graph-scripts/setup.py`
- Test: extend `tests/test_github_connector.py` + `tests/test_connector_sources.py`

**Step 1:** Failing tests — wrappers must produce identical eventIds; :419 expects new kind.
**Step 2:** Run — FAIL.
**Step 3:** Implement wrappers + config updates + migration note.
**Step 4:** Run `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_github_connector.py tests/test_connector_sources.py -v` — PASS.
**Step 5:** Commit `refactor(1714): connector uses shared mapper; #1155 normalized`.

### Task 7: Historical duplicates + legacy migration (deliver-or-defer, owner recorded)

**Intent:** Prior live runs minted unkeyed `observation` duplicates; the decision is recorded, not silent.
**Acceptance:** `graph-scripts/1714_dedup_observation.py` ships (best-effort merge by github_url, default leave-as-is); decision + owner recorded in the task notes; falsification (a) verified against a real re-run.
**Files:**
- Create: `graph-scripts/1714_dedup_observation.py`
- Test: `tests/test_github_index_lifecycle.py::test_dedup_script_dry_run`

**Steps:** TDD (dry-run report → opt-in merge), commit `chore(1714): historical observation-dedup script (deliver-or-defer)`.

---

## Slice 1 — Remote GitHub-docs extraction

### Task 8: Docs fetcher + staging (Contents-API walk)

**Intent:** Remote `docs/` folders become corpus documents with hash dedup.
**Acceptance:** `github_docs.py` walks `GET /repos/{repo}/git/trees/{branch}?recursive=1` filtered to docs/, fetches changed blobs, stages under **`{TORTOISE_INGEST_BASE_DIR}/{team_id}/`** (team-partitioned — T2-P2b); `compute_file_hash` dedup; unchanged re-ingest ⇒ 0 new nodes (falsification (f)). **Input guards (T1-P16 + cycle-3, folded):** text-type guard (skip binary/non-UTF8 blobs, record skipped count) + max-blob-size constant (skip oversized, honest status) + atomic-or-reconciled staging with cleanup on partial failure (no stale files for the next corpus pass). TDD: `test_skip_binary_and_oversized`, `test_staging_cleanup_partial_failure`, `test_two_team_staging_isolation`.
**Files:**
- Create: `tortoise/indexer/github_docs.py`
- Test: `tests/test_docs_fetcher.py`

**Steps:** TDD (walk via mock transport; staging containment; hash dedup; re-ingest 0-new), commit `feat(1714): remote GitHub-docs fetcher with staged corpus`.

### Task 9: `/v1/index/docs` job + Document-aware quota gate

**Intent:** The docs job is quota-fair (the points gate is vacuous for Documents) and fail-closed when the sandbox is unset.
**Acceptance:** `POST /v1/index/docs` mirrors `/v1/index/github` (team-scoped `_INDEX_JOBS` isolation, cross-team poll 404 test); `_count_resource` gains a `documents` resource — **derived-constant cap (`max_documents` from `max_points` with a documented conversion factor — avoids the `tier_limits` KeyError ripple), discriminator `COALESCE(documentKind,'') != 'transcript'`** (NULL-kind docs COUNT — no leak; session transcripts excluded); 402 at Document cap; unset/escaping base ⇒ honest job failure, no writes; `github_docs_indexed` state key set (REGISTERED). TDD: `test_transcript_not_counted_docs_cap`, `test_null_kind_doc_counts`, `test_cross_team_job_poll_404`.
**Files:**
- Modify: `tortoise/hosted_api.py` (route + job + state key)
- Modify: `tortoise/quota.py` (`_count_resource`)
- Test: `tests/test_index_docs_api.py`

**Steps:** TDD (job poll; 402 at Document cap where points gate would NOT fire; unset-base fail-closed), commit `feat(1714): hosted docs index job with Document-aware quota + fail-closed sandbox`.

### Task 10: Deploy config + honest self-hosted parity

**Intent:** The sandbox is set in production; self-hosted path documented.
**Acceptance:** `entrypoint.sh`/`fly.toml` set `TORTOISE_INGEST_BASE_DIR`; AGENT_ONBOARDING.md Q5 copy carries the honest stdio-only difference (self-hosted = clone + corpus).
**Files:**
- Modify: `entrypoint.sh`, `fly.toml`, `tortoise/onboarding/AGENT_ONBOARDING.md` (Q5 note)
- Test: `tests/test_index_docs_api.py::test_unset_base_fails_closed` (already green from Task 9 — deploy config is the fix)

**Steps:** config edit + doc edit, commit `chore(1714): ingest sandbox in deploy config; honest docs parity`.

---

## Slice 2 — Session capture wiring (T1+T3 first, T2 backfill in-slice)

### Task 11: Session.harness + receipts + server-enforced consent 403

**Intent:** Capture is auditable per harness and consent is real at the data plane (the P0 — closes the prompt-injection exfiltration hole).
**Acceptance:** `SessionRequest` gains `harness` (OPTIONAL, Literal {claude, 'claude-desktop', 'claude-web', codex, cursor, pi}), `session_id` (idempotency key), `source`; both Session MERGEs (`hosted_api.py:4062`, `sdk.py:1973`) set `harness` **set-only-when-present (None never erases a stored value)**; `POST /v1/sessions` + `tortoise_session_capture` return **403 (checked FIRST in the gate stack, before provider 503/402) when the team's enforced `session_recording` flag is not true**; per-harness receipts `session_capture_receipt_{harness}` (None-guarded — plain `session_capture_receipt` when harness absent) set only on 2xx; legacy `session_recording=True` grandfathered as consent; Slice 2 ships the consent-set surface (existing onboarding PATCH). **STATE-KEY REGISTRATION TABLE (cycle-3 P1-2 + cycle-4, folded — the allowlist filter silently drops unregistered keys):** every new key — `github_index_cursor`, `github_legacy_backfill_done`, `github_docs_indexed`, `capture_revised`, `capture_ask_shown`, `session_capture_receipt` (bare member — legacy no-harness hooks) + `session_capture_receipt_{harness}`, `session_capture_last_error_{harness}` (cycle-4 P1-2 — set on non-2xx capture attempts, cleared on 2xx; the dashboard's per-harness last-attempt failure sub-line reads THIS, not client state), `install_probe_{claude|pi}` — added to BOTH `_ONBOARDING_DEFAULT_STATE` (:7552) and `DEFAULT_ONBOARDING_STATE` (:1850) + `_ALLOWED_STATE_KEYS` + PATCH model; dynamic per-harness keys pinned as explicit members; **parametrized allowlist-registration test `test_onboarding_endpoints.py::test_state_keys_registered_parametrized`** (every key in the table round-trips through PATCH + both defaults + allowed-keys — makes the P1-2 fix self-verifying); **failure-key transition test `test_capture_session.py::test_last_error_set_on_failure_cleared_on_2xx`**; DELETE the dead shadowed `OnboardingStatePatchRequest` (:1837); check `test_onboarding_analytics_patch.py:48` state-shape dependency. **Idempotency scope (T2-P2c):** re-POST same `session_id` ⇒ 0 new nodes for Session + turn Points (M2/LLM-extracted points NOT in scope — skip extraction when the Session already existed). **Post-decline (T2-P2e):** declined team POST ⇒ 403 while existing Sessions untouched. **Cross-surface vocab (T2-P2d):** `_HARNESS_ANALYTICS_VALUES` (gains claude-web/claude-desktop) ⊆ harness Literal; receipt keys per Literal member. Invalid-harness 422 test runs on an OPTED team (`harness='vim'` present ⇒ 422; absent ⇒ None).
**Files:**
- Modify: `tortoise/hosted_api.py` (SessionRequest, MERGE, consent gate, receipt)
- Modify: `tortoise/sdk.py:1973` (MERGE harness)
- Modify: `tortoise/hosted_api.py:1850/:7552` (BOTH live default-state dicts + `_ALLOWED_STATE_KEYS` + PATCH model)
- Test: `tests/test_capture_session.py` (extend), `tests/test_onboarding_endpoints.py` (extend)

**Steps:** TDD — harness persisted; un-opted POST ⇒ 403; opted ⇒ 200; receipt 2xx-only; jsonb round-trip incl. provisioned team; commit `feat(1714): Session harness + server-enforced consent gate`.

### Task 12: Entity-linking pass + ONTOLOGY registration + link outcomes

**Intent:** Captured sessions link to subject/project entities (amend 13) with tracked, honest outcomes.
**Acceptance:** after capture, Session + extracted episodic Points link via `aboutObject` (regex trigger pinned: `github.com/{org}/{repo}/issues/{n}`, `{repo}#{n}`, bare `#n` guarded; first-match per point, all-matches for Session; no-match ⇒ no link, honest); `entity_links_attempted`/`entity_links_created` tracked on Session; misses warn-logged; ONTOLOGY.md edge table registers Session as an `aboutObject` source.
**Files:**
- Modify: `tortoise/hosted_api.py` (linking post-process + **re-run on index completion** — owned by `_run_indexing`'s completion hook, T1-P15 folded), NEW `tortoise/session_link.py` resolver (resolve-to-current by externalId — aboutObject never dangles on supersede)
- Modify: `docs/ONTOLOGY.md` (edge table)
- Test: `tests/test_capture_session.py` (linking assertions)

**Steps:** TDD (links created for indexed entities; counters; no-match honest), commit `feat(1714): session→entity linking with tracked outcomes`.

### Task 13: `tortoise_session_capture` MCP tool + T3 workflows prompt

**Intent:** T3 gets an executable filing surface — no inert promise (Claude Web's workflows prompt can actually file).
**Acceptance:** tool registered in `tool_registry.py` (http_policy) + `mcp_server.py` handler; same 403/402/503/422 gates; stdio ⇒ honest "requires hosted mode" error; drafted claude-web prompt paragraph 4 ships with the opt-in conditional + 403 failure copy + "don't retry"; Claude-Web filing-path spike (MCP vs native HTTP POST) verdict recorded — disclosure-only is NOT a terminal state; the web sessions row is disabled-with-reason (not hidden) until a path is confirmed. **The spike verdict MUST define a server-visible web signal** (install-probe variant for web, or observed web-harness POSTs — "workflows-prompt presence" alone is client-side and unpinnable) before `HARNESS_CAPTURE_SUPPORT` flips web to enabled; any new web state key joins the Task 11 registration table. **GROUP_BY_NAME (tool_registry.py:1080) gains `tortoise_session_capture: "sessions"`** (else the tool groups under "memory" and is filtered out of sessions-group surfaces); named test `test_session_tool_grouped_sessions`.
**Files:**
- Modify: `tortoise/tool_registry.py`, `tortoise/mcp_server.py`
- Modify: `website/apps/dashboard/src/harnesses.js` (T3 prompt draft + `HARNESS_CAPTURE_SUPPORT` constant)
- Test: `tests/test_capture_session.py` (tool invoke with `TORTOISE_SESSION_LLM_MOCK=1`)

**Steps:** TDD (registered + gates honored + 403), commit `feat(1714): T3 session-capture MCP tool + workflows prompt`.

### Task 14: T1 wiring — Claude Code hooks (in-repo) + Pi extension copy-install

**Intent:** T1 automatic capture installs from the harness copy.
**Acceptance:** `HARNESS_INSTALL['claude']` includes the in-repo `tortoise/claude-hooks/session-{start,end}.sh` install (cp + `.claude/settings.json` SessionStart/SessionEnd entries); `HARNESS_INSTALL['pi']` includes the reflect-hook/tortoise-capture copy-install (outside-repo instructions); hooks pass `harness` through `_cmd_session_capture`; hook smoke test (exit-0 under failure + mocked POST → Session + receipt).
**Files:**
- Modify: `website/apps/dashboard/src/harnesses.js`, `tortoise/claude-hooks/session-end.sh` (harness passthrough), `tortoise/claude-hooks/session-start.sh` (**install-probe POST**), `tortoise/__main__.py` (`_cmd_session_capture` payload), `tortoise/hosted_api.py` (**`POST /v1/sessions/install-probe` route** — `get_current_team`-gated, writes `install_probe_{harness}` REGISTERED key, consent-gating decision: probe is UNCONDITIONAL install telemetry (harness + timestamp only, no content), NOT consent-gated; **self-hosted routing pin: probes target the configured `TORTOISE_API_URL`, never a hardcoded hosted host**), Pi extension-on-load probe (copy-install instructions)
- Test: `tests/test_session_capture_e2e.py` (hook smoke), manual Pi 2xx leg (ops checklist)

**Steps:** TDD hook smoke + **install-probe round-trip (`test_onboarding_endpoints.py::test_install_probe_round_trip`)** + **probe-before-enable ⇒ toggle-on lands directly in `waiting` (install-pending skipped; the off-state display is unaffected by a probe); NO probe yet ⇒ `install-pending` with the inline install steps (per Task 17's "waiting shown only after a probe")**; receipt ⇒ active (receipt authoritative over probe); **dist rebuild + commit IN THIS SLICE (T1-P4 folded): `npm run build` + commit `dist/` after the harness.js/session-start.sh wiring lands** — the shipped wizard must carry the T1/T3 install copy, not wait for Task 19; commit `feat(1714): T1 session capture wired into harness copies + install probe + dist`.

### Task 15: T2 backfill — `tortoise sessions import --harness codex|claude-desktop|pi`

**Intent:** Historical transcript backfill with 2xx-only receipts (scoped as backfill, NOT coupled to the wizard's capture acceptance).
**Acceptance:** import CLI stages parsed session locally (data preservation), POSTs, writes receipt only on 2xx; 403/402/503 ⇒ fail, no receipt, honest error; Codex + Desktop parsers idempotent on re-import; Cursor spike verdict recorded (ships or honest `unsupported`).
**Files:**
- Modify: `tortoise/__main__.py`
- Create: `tortoise/session_import/parsers.py` (codex, claude_desktop, cursor-gated; **pi reuses the codex parser — pi session JSONL is a tree-structured JSONL like codex's; named reuse + idempotency test**, or add `pi.py`)
- Test: `tests/test_session_import_codex.py`, `tests/test_session_import_desktop.py`

**Steps:** TDD (fixtures, idempotency, receipt semantics), commit `feat(1714): T2 session backfill import CLI`.

---

## Slice 3 — Honest ask

### Task 16: Wizard step-1 → "Memory sources" + copy fixes

**Intent:** The ask exists, off-by-default, mechanism-gated, honest copy.
**Acceptance:** step-1 renders three opt-in toggles (issues / docs / sessions) reusing `role="switch"`/`aria-checked`/`aria-label`; toggle state machine pinned (issues = off→on-but-not-connected inline Connect→connected+indexing; docs = disabled-with-reason until connected; sessions = per-harness: claude/pi enabled with install steps inline; **the SESSIONS toggle-on PATCH sets `capture_revised` (T1-P8 folded — scoped to the sessions toggle ONLY, never issues/docs; fresh opt-ins NEVER see the re-ask pane)**; **docs toggle-on reveals the explicit "Index docs" action wired to `POST /v1/index/docs` (T1-P7 folded)**; **Task 16 creates the shared 4-state capture-status component with the CANONICAL state names: `off → install-pending → waiting → active`** (probe-driven; Task 14/17 reference these exact names verbatim); **codex/claude-desktop disabled-with-reason ("backfill import only" / "not yet available") until an install path exists**; cursor follows its spike verdict; **web disabled-with-reason ("session capture for web is in progress — not available yet")** — flipped to enabled by the Task 13 spike verdict via `HARNESS_CAPTURE_SUPPORT`; docs row terminal states distinct ("N documents indexed" / failed-with-reason in-flight|base-unset|exhausted / "status expired — re-check")); copy fixed at `main.jsx:2350/:2437/:2439` ("issues become work items with a lifecycle record, plus claims extracted from their content"); bounded poll pattern (tries + terminal short-circuit, refs, per-team guard); toggle PATCH MERGE (no stale reads); failures render under the row (`role="alert"`), never the global 402-upgrade banner.
**Files:**
- Modify: `website/apps/dashboard/src/main.jsx`
- Modify: `website/apps/dashboard/src/harnesses.js` (`HARNESS_CAPTURE_SUPPORT` consumed)
- Test: dashboard e2e (RUN_DASHBOARD_E2E)

**Steps:** implement + e2e; commit `feat(1714): wizard memory-capture step with honest copy`.

### Task 17: Misled-user re-ask + later-opt-in "Memory sources" panel

**Intent:** Existing `session_recording=True` users are re-asked exactly once (both surfaces); later opt-in is a real dashboard surface.
**Acceptance:** ONE panel (re-ask variant "you previously enabled this — before this fix, recording never ran; nothing was captured" (past-scoped, never falsifiable by a post-fix capture)) on Overview; re-ask gate (`session_recording=True && !capture_revised`, once per visit until resolved — `capture_ask_shown` set on ANSWER only, **dismissal NEVER consumes the ask** (T2-P2f); `capture_revised` on any explicit resolution incl. decline) fires on wizard step-1 AND the panel; **4-state capture status: `off → install-pending → waiting → active` (CANONICAL names per Task 16's shared component)** (install-pending driven by the server-visible install-probe from Task 14; waiting shown only after a probe); **dashboard re-index button** (calls `POST /v1/index/github/re-poll`, bounded-poll pattern); per-harness receipts + per-harness last-attempt failure sub-line (reads `session_capture_last_error_{harness}` — REGISTERED, Task 11) drive status; **re-ask gate reads `!capture_ask_shown` too (the key is now READ, not write-only — show when `session_recording=True && !capture_revised && !capture_ask_shown`); decline NEVER clears probes or receipts — re-enable resolves receipt-authoritative: probe+receipt ⇒ active, probe only ⇒ waiting, neither ⇒ install-pending (TDD `test_reenable_with_receipt_active`)**; `aria-live="polite"` on status regions; re-ask pane `role="alertdialog"` + initial focus; normal panel variant = step-1 toggle set + state machine reused (shared component); empty/loading/error states.
**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (panel + re-ask)
- Test: `tests/test_onboarding_integration.py` (misled path), dashboard e2e

**Steps:** TDD misled-path; commit `feat(1714): misled-user re-ask + memory sources panel`.

### Task 18: AGENT_ONBOARDING.md Q3 rewiring (drafted copy)

**Intent:** The self-hosted prompt's Q3 is honest and writes the SAME consent keys as the wizard.
**Acceptance:** Q3 yes-branch writes the enforced consent flag + `capture_revised` and **skips the ASK when `capture_revised` is set — never skips the WRITE (a user-initiated enable always re-sets consent even after a decline)**; **Q3 no-branch clears the enforced consent flag + sets `capture_revised`** (mirrors wizard/panel decline — same keys); **self-hosted re-enable path pinned (cycle-4 P1-B): `tortoise_onboarding_session_recording(enable=true)` re-sets the consent flag regardless of `capture_revised`** — a stdio user who declined can re-enable via the MCP tool or by re-answering Q3 yes (TDD `test_q3_decline_then_reenable_consents`); drafted copy (mechanism-gated "enabled", what's recorded/where it goes — **incl. the install-probe beacon: harness + timestamp on session start, no content**, stdio variant); `tortoise_diary_write` fallback retired (honest self-hosted answer); tool table + error-recovery updated; parity table names webhook-only gap + stdio differences; false-promise grep broadened ("will be saved as memory", "Session recording enabled").
**Files:**
- Modify: `tortoise/onboarding/AGENT_ONBOARDING.md`
- Modify: `tortoise/mcp_server.py` (`tortoise_onboarding_session_recording` writes the new keys)
- Test: `tests/test_onboarding_endpoints.py` (prompt-path consent round-trip), grep gate

**Steps:** draft copy + rewire; commit `fix(1714): honest Q3 — real per-harness mechanism, single consent source`.

### Task 19: dist rebuild + full verification sweep

**Intent:** The shipped wizard matches the source; everything green.
**Acceptance:** `npm run build` in `website/apps/dashboard` regenerates `dist/` (committed); full docker lane + carve-out suites green; e2e (RUN_DASHBOARD_E2E) green; false-promise grep clean.
**Files:**
- Rebuild: `website/apps/dashboard/dist/`
- Run: full test suite

**Steps:** build, run `TORTOISE_DB_URI=… uv run pytest tests/ -v` (docker lane) + `TORTOISE_TEST_CARVE_OUT=1 uv run pytest <17 embedded files> -v`, commit `chore(1714): rebuild dashboard dist; verification sweep`.

---

## Execution Order & Sequencing

1. **Slices 0 → 1 → 2 → 3** (each independently shippable; Slice 0 is the root gate — nothing ships on the broken baseline). **Parallelizable within Slice 0: Task 1 ∥ Task 2 (disjoint files — mapper vs fetch plumbing; Task 3 is the merge point); Task 7 ∥ Tasks 4-6 (standalone script, only needs the deliver-or-defer owner decision).**
2. **T2 (Task 15)** lands inside Slice 2 after T1+T3 (per user staging).
3. **Cursor spike + Claude-Web filing-path spike** are research tasks inside their slices; verdicts recorded; web row disabled-with-reason until a server-visible signal is confirmed (Task 13 spike verdict) — never hidden.
4. **Pi hosted-2xx leg** is an ops checklist item (live key + `tortoise-config.json`), not a CI pytest.
5. Every commit through **commit-workflow** (pre-flight, PR, code-review gate).

## Runtime Prerequisites

Docker FalkorDB test lane · `GITHUB_CLIENT_ID/SECRET` + token `repo` scope · `TORTOISE_INGEST_BASE_DIR` SET in deploy · LLM provider key (provisioned #1358) + `TORTOISE_SESSION_LLM_MOCK=1` seam · Pi key + config for the 2xx leg · reference-org quota headroom numbers (deliver-or-defer owner).


---

# Plan Review Cycle-1 Incorporations (part of the plan — fold into tasks before execution)

> Cycle 1: 4 reviewers (Structural, Integration, UX, Failure-Mode) → 16 P1s + ~20 P2s. All incorporated below. Re-review cycle 2 dispatched after.

## P1 amendments

**T1-P1 (Task 3 + Task 6) — `-closed` backfill gets an owner.** Task 3 gains a step: on the FIRST post-change run, scan existing `-created` Events with `github.issue.closed` kind/`endedAt` (legacy self-hosted/pipeline_cli graphs only) and mint `-closed` (idempotent, no double-mint, real-SDK test). If rejected → the qualification is recorded in the commit message + task notes (owner: Slice-0 implementer). Never applies to new first-runs (Task 3 already pins `-created`-only).

**T1-P2 (Task 5 + Task 17) — re-index affordance owned + route pinned.** Task 5 pins the explicit `POST /v1/index/github/re-poll` route (no query-param ambiguity). Task 17's Memory sources panel gains the **re-index button** (calls re-poll, bounded-poll pattern) in its acceptance.

**T1-P3 (Task 11) — SessionRequest fields pinned: `harness` OPTIONAL (default None) + `session_id` + `source`.** Existing consumers (deployed `session-end.sh`, SDK `capture_session` callers) POST without `harness` — required would 422 every pre-installed hook. Receipt key None-guarded: `session_capture_receipt_{harness}` when harness present, else plain `session_capture_receipt`. `session_id` (Claude Code's real id, forwarded by the hook) is the idempotency key — re-POST same session_id ⇒ 0 new nodes, one Session, one receipt (TDD). `source` becomes a real field (transcript stem preserved).

**T1-P4 (Slice 2 + Task 19) — in-slice dist rebuild.** A dist rebuild+commit lands in Slice 2 AFTER Task 14 (so shipped T1/T3 harness copy is live, not inert); Task 19 remains the final full sweep. Honors the "same slice/commit as dist rebuild" pin.

**T1-P5 (Task 9) — Document-cap gate made implementable.** `documents` resource added to `_RESOURCE_LIMIT_KEYS` + `resolve_team_limits` + a `max_documents` pricing.json field (or documented derived constant from max_points with a conversion factor — pin one); count discriminator = `documentKind != 'transcript'` (session transcripts MERGE `:Document` with `documentKind='transcript'`, hosted_api.py:4456 — excluded); TDD asserts a session-captured Document does NOT consume the docs cap.

**T1-P6 (Task 16/17 + Task 14) — no "enabled-waiting" without an install path.** Sessions toggle-on surfaces the mechanism-install step INLINE (per-harness from HARNESS_INSTALL: Claude hooks cp + settings entries; Pi extension copy-install); capture status becomes **4-state `off → install-pending → waiting → active` (superseded by Task 16's canonical names + the server-visible install-probe — see T2-P1 and the Task 14/16/17 bodies; the file-existence detection below is SUPERSEDED by the install-probe)**.

**T1-P7 (Task 16 + Task 9) — docs toggle is not a dead end.** Docs toggle-on reveals an explicit **"Index docs" action** wired to `POST /v1/index/docs` (or auto-docs-index on connect — pin one; recommended: explicit action on toggle-on); Journey 1 step 2 gains the docs leg.

**T1-P8 (Task 17 + Task 11 + Task 18) — re-ask decline branch.** Answering NO clears the enforced consent flag AND sets `capture_revised` (wizard + panel + Q3); "declined ⇒ 403" added to the consent-gate test; **toggle-on PATCH also sets `capture_revised`** so fresh opt-ins never see the re-ask pane (exactly-once preserved across surfaces).

**T1-P9 (Task 16) — web sessions row = disabled-with-reason, not hidden.** "Session capture for web is in progress — not available yet" (consistent with the docs disabled pattern); the spike verdict flips it to enabled.

**T1-P10 (Task 3/5) — per-team in-flight guard.** `_run_indexing` rejects/reuses when a `_INDEX_JOBS` entry for the team is still "started" (single-flight — kills the TOCTOU probe→create duplicate); concurrency test: two concurrent `_run_indexing` tasks (mock transport) ⇒ exactly one point set, cursor advanced once.

**T1-P11 (Task 14/11) — Session idempotency via real session_id.** `session-end.sh` forwards Claude Code's `session_id` from hook metadata → `_cmd_session_capture` → `SessionRequest.session_id`; re-POST same id ⇒ 0 new nodes (TDD).

**T1-P12 (Task 11) — receipt↔Session invariant.** TDD asserts receipt ⇒ Session + turn Points exist (receipt never lands without durable data); simulate receipt-PATCH failure (mock `_update_onboarding_state`) ⇒ retry with same session_id converges to exactly one Session.

**T1-P13 (Task 2/5) — mid-walk GitHub failure semantics.** 401 (token expiry) / 429 mid-walk tests: honest "failed" status with readable error; cursor NOT advanced past unprocessed items; re-run resumes without gaps/dupes; bounded retry-with-backoff on 429/5xx in the fetcher.

**T1-P14 (Task 5/9) — job-status resilience.** `_INDEX_JOBS` is in-memory (Fly restart kills it): on enqueue, clear stale entries for the team; restart-mid-run ⇒ next connect/re-poll re-enqueues; poll-after-1h-eviction returns a terminal state the UI renders honestly (not an error).

**T1-P15 (Task 12 + Task 5) — links not dead-ended by index order.** Re-run the entity-linking pass on index completion (preferred — links resolve after entities materialize), OR record the decision; status distinguishes "active with 0 links" honestly (link counters surfaced).

**T1-P16 (Task 8) — docs walk input guards + staging hygiene.** Text-type guard (skip binary/non-UTF8 blobs, record skipped count in status) + max-blob-size constant (skip oversized, honest status) + atomic-or-reconciled staging with cleanup on partial failure (no stale files picked up by the next corpus pass).

## P2 amendments (concise)

- **Task 15:** pi sessions reuse the codex parser (name the reuse + idempotency test) or add `pi.py`.
- **Task 11:** `_HARNESS_ANALYTICS_VALUES` (hosted_api.py:7569) gains claude-web/claude-desktop + assertion; DELETE the dead shadowed `OnboardingStatePatchRequest` (:1837) + reconcile `DEFAULT_ONBOARDING_STATE` (:1850) with `_ONBOARDING_DEFAULT_STATE` (:7552) — check `tests/test_onboarding_analytics_patch.py:48` state-shape dependency; invalid-harness 422 test (`harness="vim"` ⇒ 422, no write).
- **Task 13:** `GROUP_BY_NAME` (tool_registry.py:1080) gains `tortoise_session_capture: "sessions"`.
- **Task 9:** docs cross-team job-poll isolation test (team B polls team A's docs job_id ⇒ 404); 0-repos/0-issue status tests (honest completed-with-0, not stuck).
- **Task 12/3:** supersede-dangling-link test — capture ⇒ edit issue ⇒ re-index ⇒ link target stays current (resolve-to-current on read or refresh).
- **Task 16:** poll terminal states pinned — "indexing complete (N issues)" success + "indexing failed — retry" (exhausted); capture-status + indexing-status announced via `aria-live="polite"`; re-ask pane = `role="alertdialog"` + initial focus on the yes/no buttons.
- **Task 17:** normal (non-re-ask) panel variant = step-1 toggle set + state machine reused (shared component, note the reuse); `capture_ask_shown` set on **ANSWER only — never on dismissal or render** (dismissal does not consume the ask; re-show until resolved). SUPERSEDES the earlier "set on dismissal/answer" wording (cycle-2 P2f).
- **Task 11:** consent 403 checked FIRST in the gate stack (before provider 503/402 — fail-fast, no quota work for un-opted teams); grandfathering failure mode added to the surface map + legacy-True-consent → re-ask-resolution test.

## Cycle-1 changelog
| # | Issue | Severity | Location | Fix |
|---|-------|----------|----------|-----|
| T1-P1..P16 | 16 P1s (backfill owner, re-index route+UI, SessionRequest fields, in-slice dist, docs gate, install-pending state, docs trigger, decline branch, web disabled, in-flight guard, session_id idempotency, receipt invariant, mid-walk failures, job resilience, link re-run, docs guards) | P1 | Tasks 2/3/5/8/9/11/12/13/14/16/17/19 | Incorporated above |
| T1-P2s | ~20 P2s (analytics values, :1837 delete, GROUP_BY_NAME, isolation tests, supersede links, poll terminal UI, a11y, panel reuse, ask_shown semantics, 403-first) | P2 | Tasks 9/11/12/13/15/16/17 | Incorporated above |


---

# Plan Review Cycle-2 Incorporations (part of the plan — fold into tasks before execution)

> Cycle 2: 3 fresh reviewers → 4 P1s + P2 batch. Body-drift edits above applied directly; new mechanisms below.

## P1 fixes (cycle 2)

**T2-P1 — Install detection gets a SERVER-VISIBLE signal (the flagship fix).** The browser dashboard cannot stat the user's filesystem. Fix (Task 14): the in-repo `session-start.sh` hook and the Pi extension each POST an **install-probe** to a new `POST /v1/sessions/install-probe` (harness + probe timestamp; onboarding state key `install_probe_{harness}`). Dashboard 4-state becomes `off → install-pending → waiting → active` (canonical names per Task 16; install-pending = no probe yet, waiting = probe seen no receipt, active = receipt). Per-harness detection sources: claude = SessionStart probe, pi = extension-on-load probe, codex/claude-desktop/cursor = N/A (disabled-with-reason rows until an install path exists), web = workflows-prompt presence (defined by the Task 13 spike). TDD: probe route round-trip; status transitions.

**T2-P2 — In-flight guard ordering (guard BEFORE stale-clear).** Ordered algorithm in `_run_indexing`: (1) guard-check FIRST — if a `_INDEX_JOBS` entry for the team is `started`, REUSE it (return its job_id; resolves the rejects/reuses OR); (2) only then evict entries that are terminal or older than a 30-min TTL (never the just-reused in-flight entry); (3) record the single-process assumption (per-event-loop atomic; document a DB-backed job lock only if Fly scales horizontally). Named test: `test_github_index_lifecycle.py::test_in_flight_single_flight_reuses`.

**T2-P3 — Backfill discriminator + one-time marker.** Fresh first-run `-created`(closed-kind) events are byte-identical to legacy artifacts — the scan must NOT double-mint. Fix (Task 3): backfill runs ONLY when the persisted one-time marker `github_legacy_backfill_done` is absent, scans PRE-EXISTING events only (before minting), mints `-closed`, sets the marker. Normal diff logic does NOT mint `-closed` for closed-without-`-closed` on re-runs (convergence defined: one-time backfill is the only source). TDD asserts no double-mint on fresh first-runs.

**T2-P4 — Composite cursor for 1s-granularity `updated_at`.** Cursor = `(updated_at, number)` composite (or page-number cursor with overlap dedup by issue number/eventId) — same-second boundary items across a cursor boundary indexed exactly once across two runs. TDD: `test_github_index_lifecycle.py::test_cursor_same_second_boundary`.

## P2 batch (cycle 2)

- **Named-test table (P2-8):** `test_github_index_lifecycle.py::{test_in_flight_single_flight_reuses, test_mid_walk_401_honest_fail, test_cursor_same_second_boundary, test_link_rerun_on_index_completion}`; `test_capture_session.py::{test_repost_same_session_id_zero_new, test_receipt_requires_durable_data, test_receipt_patch_failure_retry_converges, test_invalid_harness_422_opted_team, test_decline_clears_consent_403}`; `test_index_docs_api.py::{test_transcript_not_counted_docs_cap, test_null_kind_doc_counts, test_cross_team_job_poll_404}`; `test_docs_fetcher.py::{test_skip_binary_and_oversized, test_staging_cleanup_partial_failure, test_two_team_staging_isolation}`; `test_onboarding_endpoints.py::test_q3_and_wizard_write_same_keys`.
- **Document gate (T2-P2a):** pin the DERIVED-CONSTANT option (`max_documents` derived from `max_points` with a documented conversion factor — avoids the `tier_limits` KeyError ripple across all tiers + `_REQUIRED_LIMIT_KEYS`); discriminator `COALESCE(documentKind,'') != 'transcript'` (NULL-kind docs COUNT — no leak); TDD asserts a frontmatter-less docs-endpoint doc counts.
- **Staging team-partitioning (T2-P2b):** stage under `{TORTOISE_INGEST_BASE_DIR}/{team_id}/...` — team A blobs never picked up by team B; two-team isolation assertion.
- **Extraction-path idempotency scope (T2-P2c):** "re-POST same session_id ⇒ 0 new nodes" is scoped to Session + turn Points (M2/LLM-extracted points are not deterministically keyed — either skip extraction when the Session already existed, or scope the assertion).
- **Vocabulary sync (T2-P2d):** cross-surface test — `_HARNESS_ANALYTICS_VALUES ⊆` SessionRequest harness Literal, and receipt keys per Literal member.
- **Post-decline data outcome (T2-P2e):** honest copy says "already-captured sessions remain; new capture is blocked" — TDD: declined team POST ⇒ 403 while existing Sessions untouched.
- **Dismissal semantics (T2-P2f):** dismissal does NOT consume the exactly-once ask — re-show until resolved (the contradictory "set on dismissal" wording is removed; `capture_ask_shown` is set on ANSWER only).
- **Docs-row terminal states (T2-P2g):** docs row: success "N documents indexed"; failed-with-reason (in-flight / base-unset / exhausted — distinct copy, never a retry loop); eviction-expired = "status expired — re-check".
- **Grandfathering copy scope (T2-P2h):** re-ask copy scoped to the past ("before this fix, recording never ran") — never falsified by a mid-session capture.

## Cycle-2 changelog
| # | Issue | Severity | Location | Fix |
|---|-------|----------|----------|-----|
| T2-P1 | Install detection unimplementable from browser | P1 | Tasks 14/16/17 | install-probe route + per-harness detection sources |
| T2-P2 | Guard × stale-clear ordering | P1 | Task 3/5 | guard-first, reuse, then evict (30-min TTL) |
| T2-P3 | Backfill discriminator collision | P1 | Task 3 | one-time marker + pre-existing-only scan |
| T2-P4 | Cursor 1s-granularity boundary | P1 | Task 2/5 | composite (updated_at, number) cursor |
| T2-P2a..h | 8 P2s (doc gate, staging, extraction scope, vocab sync, decline data, dismissal, docs terminal, grandfather copy) | P2 | Tasks 9/11/12/16/17/18 | incorporated above |

<!-- plan-review: cycles=5, status=clean, version=2.3.0 (cycles 3-5 folded into task bodies; cycles 1-2 have incorporation sections) -->
<!-- final-verification: clean (2 P2s resolved post-gate) -->
