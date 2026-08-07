# Analytics Instrumentation — Implementation Plan

> **Issue:** [#501](../../../../issues/501) — tortoise#501
> **Epic:** [#235](../../../../issues/235) — Hosted Onboarding Journey
> **Complexity:** standard (Architecture, Ontology)
> **Dependencies:** #495 (artifact format — DONE), #496 (question set — DONE), #498 (API plan — DONE)
> **Plan author:** issue-scoping v5.1 double diamond | **Date:** 2026-08-08

---

## Confirmed Problem Definition

The hosted onboarding flow (#235) has no measurement. Without funnel analytics, we can't answer:
- **Where do users drop off?** (signup → key provisioned → artifact copied → agent connected → questions answered → complete)
- **Which questions cause friction?** (Q1 GitHub connect has OAuth complexity; Q2 indexing is async)
- **Does the onboarding actually work?** (completion rate, time-to-complete, error rate)
- **Which harnesses are users coming from?** (Claude Code vs Codex vs Cursor vs Pi)

The epic plan Tasks 6 + 14 define the event schema and instrumentation plan. This issue scopes those into implementation-ready tasks with the backend decision finalized.

**Root cause:** The onboarding was designed as a UX flow (#495 format, #496 questions, #498 API) but no measurement layer was specified. Without analytics, the team operates blind — can't prioritize fixes, can't measure impact, can't detect regressions.

---

## Funnel Events (Full Taxonomy)

All events carry `{team_id, timestamp, session_id}` implicitly. No PII (no email, no content, no key material, no prompt text).

| # | Event | Fires when | Properties | Surface |
|---|-------|-----------|------------|---------|
| 1 | `signup_complete` | User completes registration (account created) | `{method: "email"}` | Backend (`POST /v1/register`) |
| 2 | `key_provisioned` | API key generated and displayed on welcome page | `{elapsed_from_signup_s}` | Backend (key creation) or Frontend (key displayed) |
| 3 | `artifact_copied` | User clicks "Copy" on welcome page | `{harness: "claude"\|"codex"\|"cursor"\|"pi", section: "config"\|"prompt"}` | Frontend (welcome.html JS) |
| 4 | `prompt_pasted` | Agent successfully calls `tortoise_health` (MCP) — confirms the user pasted the prompt and the agent connected | `{harness, elapsed_from_copy_s}` | Backend (MCP `tortoise_health` call from onboarding context) |
| 5 | `question_answered` | User answers a yes/no question (fires per question) | `{question_id: "github_connect"\|"github_index"\|"session_recording"\|"demo_create"\|"ingest_docs", answer: "yes"\|"no"}` | Backend (MCP onboarding tools) |
| 6 | `first_memory_created` | First Point is created after onboarding (demo graph OR user's own) | `{source: "demo"\|"user", point_count}` | Backend (`POST /v1/points` or demo creation) |
| 7 | `session_recorded` | First session is captured after onboarding | `{session_id, message_count}` | Backend (`POST /v1/sessions`) |
| 8 | `onboarding_complete` | All questions answered, verification done (`POST /v1/onboarding/complete` or state `completed_at` set) | `{elapsed_time_s, questions: {github_connect: "yes"\|"no", ...}, steps_completed: N}` | Backend (onboarding state transition) |
| 9 | `onboarding_error` | Any step fails (OAuth denied, indexing timeout, network error) | `{step: string, error_type: string, error_message: string}` | Backend (error paths in onboarding tools) |

### Funnel Stages (Expected Conversion)

```
signup_complete (100%)
  → key_provisioned (expected: 95% — some may not verify email)
  → artifact_copied (expected: 70% — some may leave after getting key)
  → prompt_pasted (expected: 50% — MCP config friction)
  → question_answered × N (expected: 80% of pasted)
  → onboarding_complete (target: 40% of signups)
```

### Error Events (Not Funnel Steps)

Error events fire in parallel to the funnel — they don't advance the funnel, they annotate it:
- `onboarding_error` fires on any tool failure, OAuth denial, network timeout
- Properties include `step` (which question/operation), `error_type`, `error_message`
- Allows querying: "what % of users hit an error on github_connect?"

---

## Instrumentation Points

### 1. Welcome Page (Frontend — `premise-labs/welcome.html`)

**Events:** `artifact_copied`

**How:** Add a `data-track` attribute to copy buttons. A small inline `<script>` listens for clicks and fires `fetch('/v1/analytics/event', {method: 'POST', body: JSON.stringify({event_name, properties})})` to the hosted API. The API endpoint validates and inserts into the analytics table.

**Why not Supabase direct:** The welcome page already talks to Supabase for auth, but analytics events need server-side validation (team_id from session, not client-claimed). Routing through the hosted API ensures the team_id is authenticated.

**Harness detection:** Parse `navigator.userAgent` or use the harness selector on the welcome page (the user picks their harness to see the right config snippet — capture that selection).

### 2. Hosted API (Backend — `tortoise/hosted_api.py`)

**Events:** `signup_complete`, `key_provisioned`, `prompt_pasted`, `question_answered`, `first_memory_created`, `session_recorded`, `onboarding_complete`, `onboarding_error`

**How:** Add a `_track_analytics_event(team_id, event_name, properties)` helper that inserts into the `analytics_events` table via Supabase client (or an HTTP call to Supabase REST API). Call it at each instrumentation point:

| Endpoint / Tool | Event |
|----------------|-------|
| `POST /v1/register` success | `signup_complete` |
| Key displayed after provisioning | `key_provisioned` |
| `tortoise_health` (when `onboarding_session` flag present) | `prompt_pasted` |
| Each `tortoise_onboarding_*` tool call | `question_answered` (with question_id + answer) |
| `POST /v1/points` (first point after onboarding) | `first_memory_created` |
| `POST /v1/sessions` (first session after onboarding) | `session_recorded` |
| `POST /v1/onboarding/complete` or `completed_at` set | `onboarding_complete` |
| Any caught exception in onboarding tools | `onboarding_error` |

### 3. MCP Tools (Backend — `tortoise/mcp_server.py`)

**Events:** `question_answered` (wrapping the REST call), `prompt_pasted` (from `tortoise_health`)

**How:** MCP tools that wrap onboarding REST endpoints fire analytics after the REST call succeeds. This is a thin pass-through — the analytics event is emitted by the same code path as the REST endpoint handler. MCP tools don't add their own analytics layer; they rely on the REST layer's instrumentation.

### 4. Audit Events (Existing — `tortoise/audit_events.py`)

**Relationship:** The existing `AuditLogger` serves a different purpose — security/control-plane audit trail (who did what, from which IP). Analytics events serve product measurement (where do users drop off, what's the conversion rate). They have different schemas, query patterns, and retention policies. **They should not be merged.**

| Concern | Audit Events | Analytics Events |
|---------|-------------|-----------------|
| Purpose | Security, compliance | Product measurement |
| Schema | Fixed columns: `operation`, `resource_type`, `resource_id`, `ip_address`, `user_agent` | Flexible: `event_name` + `properties JSONB` |
| Query pattern | "Who deleted this point?" | "What's the funnel conversion from signup → complete?" |
| Retention | Long-term (compliance) | Rolling (product metrics) |
| PII | May contain IP, user agent | Strictly no PII |

---

## Backend Recommendation

### Options Considered

| Option | Description | Verdict |
|--------|-------------|---------|
| **A: PostHog** | External analytics SaaS. Auto-capture + custom events. Generous free tier (1M events/month). | ❌ External dependency. JS SDK on welcome page, Python SDK on hosted API. PII risk (must audit auto-capture). Adds 2 new dependencies. Overkill for v1 — we need ~8 event types on ~4 surfaces. |
| **B: Extend audit_events** | Add `properties JSONB` to existing `audit_events` table, use it for both audit and analytics. | ❌ Separation-of-concerns violation. Audit has fixed schema for security; analytics needs flexible JSONB. Different query patterns (audit = point lookups, analytics = funnel/aggregate). Different retention. Mixing them makes both worse. |
| **C: Custom analytics_events table in Supabase (RECOMMENDED)** | Dedicated table in the existing Supabase project. No new dependencies. | ✅ Zero additional cost (existing Supabase project). Full control over schema. No PII risk (we control every INSERT). Simple SQL funnel queries. No external dependency. Easy to migrate to PostHog later if needed — just change the `_track_analytics_event` implementation. |

### Recommendation: Option C — Custom `analytics_events` table in Supabase

**Why:**

1. **Zero new dependencies.** The Supabase project already exists (auth, edge functions). Adding one table costs nothing.
2. **Schema flexibility.** `properties JSONB` lets us add event-specific fields without migrations. The event taxonomy will evolve as we learn about user behavior.
3. **Simple funnel queries.** SQL window functions make funnel analysis straightforward:
   ```sql
   WITH funnel AS (
     SELECT team_id, event_name, MIN(created_at) as first_seen
     FROM analytics_events
     WHERE created_at > NOW() - INTERVAL '7 days'
     GROUP BY team_id, event_name
   )
   SELECT event_name, COUNT(*) FROM funnel GROUP BY event_name ORDER BY COUNT(*) DESC;
   ```
4. **PII control.** We control every INSERT statement. No auto-capture risks. No third-party has access.
5. **Migration path to PostHog.** The `_track_analytics_event(team_id, event_name, properties)` helper is a single choke point. If we outgrow the custom table, we swap the implementation to call PostHog's API instead of Supabase INSERT. No other code changes.

---

## PII Handling

**Rule:** Event payloads must be PII-free. No email, no content, no key material, no prompt text, no IP addresses, no user agents.

| Property | Allowed? | Reason |
|----------|----------|--------|
| `team_id` | ✅ Yes | Internal identifier, not PII |
| `question_id` | ✅ Yes | Enum value, not user data |
| `answer` ("yes"/"no") | ✅ Yes | Boolean, not content |
| `harness` | ✅ Yes | Product category |
| `elapsed_time_s` | ✅ Yes | Aggregate metric |
| `error_type`, `error_message` | ⚠️ Caution | Error messages must not contain user input. Use fixed enum values (`oauth_denied`, `network_timeout`, `tool_unavailable`), not raw exception strings. |
| Email | ❌ No | PII |
| API key | ❌ No | Secret material |
| Prompt content | ❌ No | May contain PII |
| IP address | ❌ No | PII |
| User agent | ❌ No | Fingerprinting risk |
| GitHub org name | ❌ No | Could identify user |

**Validation:** The `_track_analytics_event` helper validates that `properties` contains only allowed keys before inserting. Unknown keys are stripped. This is a server-side guarantee — the client can't bypass it.

---

## Implementation Plan — TDD Tasks

### Task 1: Create `analytics_events` Table (Supabase Migration)

**Intent:** Create the dedicated analytics events table in the existing Supabase project. This table stores all funnel events with flexible JSONB properties.
**Acceptance:** Table exists with correct schema + indices. Migration is idempotent (`IF NOT EXISTS`). Queryable via Supabase SQL editor.
**Files:**
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/migrations/001-analytics-events.sql`

**Steps:**
1. Write the `CREATE TABLE IF NOT EXISTS` migration with:
   - `id UUID DEFAULT gen_random_uuid() PRIMARY KEY`
   - `team_id TEXT NOT NULL`
   - `event_name TEXT NOT NULL`
   - `properties JSONB DEFAULT '{}'`
   - `created_at TIMESTAMPTZ DEFAULT now()`
2. Add indices: `idx_analytics_team_time ON analytics_events(team_id, created_at DESC)` and `idx_analytics_event ON analytics_events(event_name)`
3. Add a partial index for onboarding funnels: `idx_analytics_onboarding ON analytics_events(created_at) WHERE event_name IN ('signup_complete', 'key_provisioned', 'artifact_copied', 'prompt_pasted', 'question_answered', 'first_memory_created', 'session_recorded', 'onboarding_complete')`
4. Document the migration in the epic's migration registry

### Task 2: Backend Analytics Helper + Instrumentation

**Intent:** Add `_track_analytics_event()` to `hosted_api.py` and instrument all backend event emission points. The helper inserts into Supabase `analytics_events` table.
**Acceptance:** All 8 backend events fire at the correct moments. Events appear in the `analytics_events` table. No PII in payloads. Error events fire on failure paths. The helper validates and strips unknown property keys.
**Files:**
- Modify: `tortoise/hosted_api.py` — add `_track_analytics_event()` helper + instrumentation calls
- Create: `tests/test_analytics_events.py` — verify events fire

**Steps:**
1. Write `_track_analytics_event(team_id: str, event_name: str, properties: dict)` helper:
   - Validate `event_name` against allowed enum
   - Strip unknown keys from `properties` (PII guard)
   - Insert into `analytics_events` via Supabase client (fire-and-forget — don't block the response)
   - On Supabase insert failure: log warning, don't fail the request (analytics is best-effort, not critical path)
2. Instrument `POST /v1/register` → `signup_complete`
3. Instrument key provisioning path → `key_provisioned`
4. Instrument `tortoise_health` (when onboarding context detected) → `prompt_pasted`
5. Instrument each onboarding tool wrapper → `question_answered` (with question_id + answer)
6. Instrument `POST /v1/points` (first point after onboarding) → `first_memory_created`
7. Instrument `POST /v1/sessions` (first session) → `session_recorded`
8. Instrument `POST /v1/onboarding/complete` → `onboarding_complete`
9. Instrument error handlers in onboarding tools → `onboarding_error`
10. Write tests: `test_signup_fires_event`, `test_question_answered_event`, `test_onboarding_complete_event`, `test_error_event_on_failure`, `test_pii_stripped_from_properties`, `test_analytics_failure_doesnt_break_request`
11. Run tests → red → green → refactor

### Task 3: Frontend Analytics Instrumentation

**Intent:** Add client-side event tracking to `welcome.html` for `artifact_copied` events. Uses a `fetch` POST to the hosted API's analytics endpoint.
**Acceptance:** Copy button clicks fire `artifact_copied` events with correct harness + section. Events appear in `analytics_events` table. No PII in payloads. Page load fires `welcome_page_viewed` (optional, nice-to-have).
**Files:**
- Modify: `premise-labs/welcome.html` — add analytics tracking script
- Modify: `tortoise/hosted_api.py` — add `POST /v1/analytics/event` endpoint for client-side events

**Steps:**
1. Add `POST /v1/analytics/event` endpoint to `hosted_api.py`:
   - Accepts `{event_name, properties}` JSON body
   - Auth: Bearer `tt_` (same as other endpoints)
   - Validates + inserts via `_track_analytics_event()`
   - Returns `{status: "ok"}` (fire-and-forget)
2. Add inline `<script>` to `welcome.html`:
   - Listen for clicks on copy buttons (`[data-track]` attribute)
   - Read `data-track-harness` and `data-track-section` from the button
   - Fire `fetch('/v1/analytics/event', ...)` with Bearer token from session
   - Debounce: don't fire duplicate events for the same button within 1s
   - Error handling: `fetch` failure is silent (console.warn, not user-visible)
3. Add `data-track` attributes to MCP config + prompt copy buttons
4. Write Playwright test: `test_copy_fires_analytics_event`
5. Run tests → red → green → refactor

### Task 4: End-to-End Verification

**Intent:** Verify the full funnel fires correctly from signup → complete. A single integration test that simulates the onboarding flow and asserts all expected events exist in the analytics table.
**Acceptance:** Test creates a team, simulates the onboarding flow, queries `analytics_events`, and asserts the expected event sequence exists. No missing events. Order is correct (timestamps are monotonic).
**Files:**
- Create: `tests/test_analytics_e2e.py`

**Steps:**
1. Write test: `test_full_funnel_events`:
   - Provision a team via `POST /v1/register`
   - Assert `signup_complete` event exists
   - Simulate key display → assert `key_provisioned`
   - Simulate artifact copy → assert `artifact_copied`
   - Simulate health check → assert `prompt_pasted`
   - Simulate answering all questions → assert `question_answered` × 5
   - Simulate demo creation → assert `first_memory_created`
   - Complete onboarding → assert `onboarding_complete`
   - Query all events for this team, verify sequence
2. Write test: `test_error_path_events`:
   - Simulate GitHub OAuth failure → assert `onboarding_error` with correct `step` and `error_type`
3. Write test: `test_no_pii_in_events`:
   - Query all events, assert no email, key, or content in properties
4. Run tests → red → green → refactor

---

## Acceptance Criteria Summary

| # | Criterion | Verification |
|---|-----------|-------------|
| AC1 | Every funnel step emits its event | Query `analytics_events` after full onboarding simulation — all 9 event types present |
| AC2 | Error paths emit `onboarding_error` | Simulate OAuth failure, network timeout, tool unavailable — each produces an error event |
| AC3 | No PII in any event payload | Automated test scans all properties for email patterns, key patterns, content strings |
| AC4 | Analytics failure doesn't break onboarding | Simulate Supabase insert failure — onboarding continues, event is logged as warning |
| AC5 | Frontend copy events fire with correct harness | Playwright test: click copy button for Claude Code, assert event has `harness: "claude"` |
| AC6 | `onboarding_complete` carries full question summary | Properties include `questions: {github_connect: "yes", ...}` with all 5 answers |
| AC7 | Funnel query returns accurate conversion rates | SQL query against `analytics_events` returns the expected funnel shape |

---

## Rejected Alternatives

| Alternative | Why Rejected |
|-------------|-------------|
| PostHog for v1 | External dependency, JS+Python SDK overhead, PII audit required for auto-capture. Overkill for 8 event types. Easy to migrate later via `_track_analytics_event` swap. |
| Extend audit_events table | Schema mismatch (fixed columns vs JSONB), query pattern mismatch (point lookups vs funnel aggregates), retention policy mismatch (compliance vs product). Mixing them makes both worse. |
| JSONL-only (no database) | No queryability. Can't answer "what's the conversion rate?" without parsing files. Fine as a fallback, not as the primary store. |
| Client-side only (no backend events) | Can't track `signup_complete`, `question_answered`, or `onboarding_error` — these happen server-side. Frontend-only misses 80% of the funnel. |
| MCP tools emit events independently | Duplicates instrumentation. The REST layer already has the context (team_id, endpoint). MCP tools should rely on REST-layer instrumentation, not add their own. |

---

## Dependencies & Parallelization

| Dependency | Status | Blocks |
|-----------|--------|--------|
| #498 API plan | DONE | Task 2 (backend instrumentation — need to know which endpoints exist) |
| #495 Artifact format | DONE | Task 3 (welcome page — need to know copy button structure) |
| #496 Question set | DONE | Task 2 (question_answered event — need question_ids) |
| Supabase project | EXISTS | Task 1 (migration) |

**Can run parallel to:** #497 (welcome page), #499 (demo graph), #500 (GitHub OAuth). No code conflicts — analytics instrumentation is additive.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Supabase insert latency blocks response | Low | Medium | Analytics inserts are fire-and-forget (asyncio.create_task). Failure is logged, not raised. |
| Event taxonomy changes during Phase 2 | Medium | Low | `properties JSONB` is schema-flexible. Adding new fields doesn't require migration. |
| Analytics table grows unbounded | Low (v1 traffic) | Low | Add a retention policy later (DELETE WHERE created_at < NOW() - INTERVAL '90 days'). Not needed for v1. |
| Welcome page doesn't have Bearer token yet | Medium | Medium | The welcome page receives the API key during provisioning. Store it in sessionStorage for the analytics fetch. If not available, skip the event (best-effort). |

---

## Deliverables

1. **Migration file:** `docs/epics/2026-08-07-hosted-onboarding-235/migrations/001-analytics-events.sql`
2. **Backend instrumentation:** `tortoise/hosted_api.py` — `_track_analytics_event()` helper + event emission at 8 points
3. **Frontend instrumentation:** `premise-labs/welcome.html` — copy button tracking
4. **Tests:** `tests/test_analytics_events.py` + `tests/test_analytics_e2e.py` — 11+ test cases
