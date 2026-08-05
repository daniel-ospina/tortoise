<!-- research-path: docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md -->

# Supabase Auth + Signup Flow + Tenant Provisioning — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Enable developer signup via Supabase Auth (GitHub OAuth, Google OAuth, email/password) with automatic team + FalkorDB namespace + API key provisioning on user creation.

**Architecture:** Static HTML pages on Cloudflare Pages use Supabase JS client for auth. On user creation, a Supabase database trigger fires a Deno Edge Function which calls a Python FastAPI endpoint. The FastAPI endpoint uses the existing `TortoiseSDK.team_create()` to provision the FalkorDB namespace and generate API keys. The Edge Function stores the result in a `user_teams` table for the welcome page to display the API key once.

**Tech Stack:** Supabase Auth (OAuth + email/password), Deno Edge Functions, Python FastAPI (existing graph-viz server), FalkorDB (existing), Cloudflare Pages (existing static hosting).

---

## Pattern Research

**Skipped** — plan touches zero third-party deps beyond what's already integrated (Supabase, FalkorDB, FastAPI).

## Integration Surface Map

| # | Surface | Type | Test Layers | Key Failure Modes |
|---|---------|------|-------------|-------------------|
| S2 | Supabase Auth | Auth provider | integration (OAuth redirect flow) | OAuth failure, redirect URI mismatch, duplicate email |
| S3 | Supabase Edge Function (tenant-provision) | Serverless | unit (handler), integration (signup→provision) | timeout, FastAPI unreachable, cold start latency |
| S1 | FalkorDB | Graph DB | unit (SDK ops) | namespace collision, connection drop |
| S5 | FastAPI server | REST API | unit (endpoint auth), integration | service role auth failure, FalkorDB unavailable |
| S9 | Cloudflare Pages | Static hosting | E2E (Playwright) | stale cache, CORS |

## Journey Test Map

### Journey: Solo Dev — Signup → First Point
1. **Step:** Visit landing page, click "Start free" → **Acceptance:** Redirected to signup page → **Test:** E2E-1-D
2. **Step:** Click "Continue with GitHub" → **Acceptance:** OAuth redirect to GitHub → **Test:** E2E-1-D
3. **Step:** Authorize GitHub OAuth → **Acceptance:** Redirected to welcome page with API key → **Test:** E2E-1-D
4. **Step:** Copy API key, runs `tortoise init --api-key <key>` → **Acceptance:** CLI configures project → **Test:** E2E-1-D
5. **Step:** Runs `tortoise create-point "Hello world"` → **Acceptance:** Point created in hosted graph → **Test:** E2E-1-D

### Failure Modes
- GitHub OAuth denied → **Expected behavior:** Error message, can retry → **Test:** E2E-1-D (break case)
- Duplicate email signup → **Expected behavior:** "Email already registered" → **Test:** unit
- Edge function timeout → **Expected behavior:** Async fallback, "Setting up..." state → **Test:** unit
- API key copy failure → **Expected behavior:** "Copy" button with fallback "Email me my key" → **Test:** E2E-1-D

---

## Tasks

### Task 1: Supabase Auth Configuration

**Intent:** Enable GitHub OAuth, Google OAuth, and email/password signup in Supabase config.
**Acceptance:** `supabase/config.toml` has all 3 providers enabled with correct redirect URLs pointing to Cloudflare Pages.
**Files:**
- Modify: `supabase/config.toml`

### Task 2: Database Migration — user_teams table

**Intent:** Create a `user_teams` table in Supabase Postgres to store the mapping between auth users and their provisioned teams, including the API key for one-time display.
**Acceptance:** Table exists with columns: id, user_id (FK → auth.users), team_id, team_name, api_key (plaintext for welcome page), key_hash, graph_name, created_at. Trigger on auth.users INSERT calls edge function.
**Files:**
- Create: `supabase/migrations/0001_user_teams.sql`

### Task 3: FastAPI Provision Endpoint

**Intent:** Add `POST /api/provision` endpoint to the existing graph-viz FastAPI server that creates a team via `TortoiseSDK.team_create()` and returns the API key.
**Acceptance:** Endpoint accepts `{team_name, user_id}` (authenticated via Supabase service role key), creates team + namespace, returns `{team_id, api_key, graph_name}`.
**Files:**
- Modify: `apps/graph-viz/server/main.py`

### Task 4: Supabase Edge Function — tenant-provision

**Intent:** Deno Edge Function that receives auth.users INSERT webhook, sanitizes team name from user metadata, calls `POST /api/provision`, stores result in `user_teams`.
**Acceptance:** Edge function handles all 3 auth methods (GitHub, Google, email/password), derives team name correctly, stores result, handles errors gracefully with retry.
**Files:**
- Create: `supabase/functions/tenant-provision/index.ts`

### Task 5: Signup Page

**Intent:** Static HTML page with Supabase JS client that provides GitHub, Google, and email/password signup.
**Acceptance:** Page renders provider buttons + email form, redirects to welcome page on success, shows errors on failure.
**Files:**
- Create: `premise-labs/signup.html`

### Task 6: Welcome Page (API Key Reveal)

**Intent:** Post-signup page that fetches the user's API key from `user_teams` and displays it with a copy button and quickstart snippet.
**Acceptance:** Key displayed with `tt_` prefix, copy-to-clipboard button, Python quickstart snippet with key pre-filled, link to dashboard.
**Files:**
- Create: `premise-labs/welcome.html`

### Task 7: Landing Page Update

**Intent:** Add "Start free" CTA button to existing landing page that links to signup page.
**Acceptance:** CTA visible, links to `/signup.html`, doesn't break existing scroll narrative.
**Files:**
- Modify: `premise-labs/index.html`

### Task 8: Tests

**Intent:** Unit and integration tests covering the provision endpoint, edge function logic, team name sanitization, API key hashing, and duplicate handling.
**Acceptance:** Tests pass with `python -m pytest tests/ -v` for Python tests.
**Files:**
- Create: `tests/test_hosted_auth.py`
