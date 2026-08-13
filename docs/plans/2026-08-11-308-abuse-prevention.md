---
title: "Implementation Plan #308 — Hosted Abuse Prevention"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted
aboutObjects: abuse-prevention, turnstile
created: 2026-08-11
---

<!-- research-path: docs/scoping/scoping-308-abuse-prevention.md -->

# Issue #308 — Abuse Prevention (Anomaly Detection + CAPTCHA) Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Durable abuse detection + enforcement on the Tortoise Hosted platform: two auto-suspend rules, exfiltration + new-IP owner notifications, 403 SUSPENDED with appeal link on both transports, Turnstile CAPTCHA on signup, and dashboard alerts.

**Architecture:** Approach A from the scoping double diamond (4 verifier cycles, gate clean): a durable Supabase substrate (`abuse_events` table + `teams.suspended_at` + `api_keys` INSERT trigger, migration 0015) with a request-path rule engine (`tortoise/abuse.py`), enforcement at the shared auth seams of both transports, and fire-and-forget event recording. Full design rationale, rejected alternatives, and ACs: `docs/scoping/scoping-308-abuse-prevention.md`.

### Pattern Research

Skipped — plan touches zero new third-party deps. Turnstile siteverify reuses the in-repo pattern (`supabase/functions/waitlist-subscribe/handle.ts`, widget pattern in `website/index.html`); HTTP via existing `httpx`; no new Python or npm packages.

### Integration Surface Map

| Surface | Type | Test layer | Bug patterns watched |
|---|---|---|---|
| migration 0015 (table+trigger+column+RPCs) | DB | FakeControlPlane trigger emulation (integration) + SQL review | trigger breaks signup RPC; backfill double-count |
| `resolve_api_key` → `suspended_at`/`email` | control plane | unit (test_supabase_control pattern) | fail-closed preserved; dict-shape drift |
| `get_current_team` / `_get_current_team_supabase` | REST auth | integration (TestClient + FakeControlPlane) | 403 detail shape; geo/read hooks gated on success only |
| `TeamResolutionMiddleware` | MCP auth | integration (TestClient on /mcp) | 60s cache vs suspension immediacy; JSON-RPC body re-read |
| `_quota_gated` abuse weights | MCP writes | unit + introspection membership test | weight drift; metering path untouched |
| `notify_abuse` | notify | unit (channels mocked) | never raises; owner email NULL fallback |
| signup/register siteverify | public endpoints | unit (httpx mocked) + integration | fail-open when secret unset; fail-closed when set |
| dashboard banner/alerts | UI | contract (response shape tests) | 403 detail parsing |
| CLI 403 parse | CLI | unit | JSON detail absent → old message |

### Failure Modes (per issue body)

- **False-positive suspension** → two-stage staging pinned by scoping delta 13 (single burst flags, never suspends; suspension needs breach persisting across a full window boundary). → `test_abuse.py::test_staging_*`
- **Rate-limit bypass** → durable state survives restarts (fresh app instance, same store); trigger counts signup-path key creates; bootstrap mints excluded (scoping delta 9). → `test_abuse_integration.py::test_suspension_survives_restart`, `test_signup_key_creates_counted`, `test_bootstrap_mints_excluded`
- **Exfiltration not detected** → per-key AND per-team read windows; write tools never counted as reads (explicit write set, scoping delta 11). → `test_exfiltration_per_key_notifies`, `test_exfiltration_fanout_team_notifies`, `test_writes_not_counted_as_reads`

**E2E alignment:** E2E-2-D (free-tier limits — abuse portion: R1/R2 → suspend → 403) and E2E-7-D (security baseline — exfiltration R3). The referenced epic docs (`docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md`, `05-test-design.md`) were lost in the repo migration; alignment reconstructed from the issue body (noted in PR).

**Tech Stack:** Python 3.11 / FastAPI / Pydantic v2, Supabase (PostgREST + RPC), vanilla JS dashboard (React JSX, no new deps), Turnstile.

---

## Task 1: Migration 0015 — abuse substrate

**Intent:** Durable event log + suspension state + surface-complete key-create telemetry (the trigger is the only seam that sees both dashboard mints and the signup `provision_team` RPC).
**Acceptance:** `supabase/migrations/0015_abuse_events.sql` exists with: `abuse_events` table (+RLS service_role ALL, index `(team_id, event_type, created_at DESC)`) including `weight integer NOT NULL DEFAULT 1` (weighted app events store ONE row with weight; evaluation uses `SUM(weight)` — plan-review P1: row-per-point would put N synchronous INSERTs in the request path, and `count(*)` on one weighted row silently undercounts bulk ingests); `AFTER INSERT` trigger on `api_keys` skipping `created_via='bootstrap'` — trigger function `SECURITY DEFINER SET search_path=''` with FULLY-QUALIFIED `INSERT INTO public.abuse_events` (plan-review P1: `provision_team` runs with empty search_path — an unqualified INSERT makes EVERY signup fail), plain body, no external calls; `teams.suspended_at timestamptz` + `teams.flagged_at timestamptz` columns (flagged_at is the durable stage-1 state the staging machine needs across deploys — up to 24h for R2); `abuse_suspend`/`abuse_unsuspend` SECURITY DEFINER RPCs with service_role-only grants (0014 pattern; un-suspend clears both timestamps); `abuse_cleanup(p_days integer DEFAULT 90)` deletes old events (ops-run retention, documented in .env.example/PR — no scheduler in this PR). Backfill comment-guard present.
**Files:**
- Create: `supabase/migrations/0015_abuse_events.sql`

**Steps:** write SQL following 0014 conventions (RLS policy naming, REVOKE/GRANT, `SET search_path=''` on RPCs); trigger: `WHEN (NEW.created_via IS DISTINCT FROM 'bootstrap')` + `INSERT INTO abuse_events(team_id, event_type, key_id) VALUES (NEW.team_id,'key_create',NEW.id)`; `abuse_suspend` sets `suspended_at=now()` only when NULL + inserts `suspend` event; `abuse_unsuspend` clears it + inserts `unsuspend` event. Verify: SQL review + Task 11 migration-semantics tests via fake.

---

## Task 2: `tortoise/abuse.py` — rule engine + stores

**Intent:** Transport-agnostic abuse substrate: event stores (Supabase/Memory/Fake), the two-stage rule engine with pinned staging semantics, read-velocity tracker, geo resolver, and the process-wide suspension signal set.
**Acceptance:** Module exports `get_engine()` seam; `AbuseEngine.record_point_create(team_id, n)` records ONE weighted event row (`weight=n`) then evaluates R1 (>500/1h, `SUM(weight)`) AND piggybacks `evaluate_key_creates(team_id)` (R2, >10/24h) — plan-review P1 fix: trigger-recorded `key_create` events (signup RPC path, no app request) are thereby evaluated on the team's NEXT hooked request; R2 also evaluates after every mint/register. Stage 1 flags (store.flag_team writes durable `teams.flagged_at` + notify `abuse_flag`), stage 2 suspends (store.suspend_team + notify `abuse_suspended` + `mark_suspended`) only when `now >= flagged_at + window` and the sum still breaches; `ReadVelocityTracker.record_read(key_id, team_id)` (>100/5min per-key OR per-team → notify once/window); `GeoResolver` from `CF-IPCountry` (fail-open None); `check_new_country` records `auth_ip` + notifies `abuse_new_ip` on first unseen country (in-process seen-cache, 24h TTL); suspended set = invalidation signal (`mark_suspended`/`clear_suspended`/`is_suspended_signal`); store protocol includes `recent_alerts(team_id, limit)` (flag/suspend/auth_ip/velocity event rows → {type, at, message} list — consumed by Task 8) and `flagged(team_id)`; env overrides `TORTOISE_ABUSE_*` + kill-switch `TORTOISE_ABUSE_DISABLED=1`; thresholds 500/10/100 with windows 3600/86400/300. All store ops best-effort on the write path (record never raises into callers). `MemoryAbuseStore` accepts an optional registry-write callback; hosted_api wires it to `SET t.suspended_at/t.flagged_at` on the registry Team node (plan-review P2: registry-mode enforcement must be durable, not just the process-local signal — scoping delta 4). `notify_abuse` is stubbed/monkeypatched in Task 2 tests (kinds land in Task 4 — ordering note).
**Files:**
- Create: `tortoise/abuse.py`
- Test: `tests/test_abuse.py`

**Steps (TDD):** write `tests/test_abuse.py` first — staging machine (single-burst flag-only; boundary-crossing suspend; quiet-after-flag never suspends), threshold boundaries (500 no-flag / 501 flag; 10/11), tracker window expiry + notify dedup + team aggregate, geo resolver header present/absent, signal set semantics, env overrides + kill-switch. Run → FAIL → implement → PASS.

---

## Task 3: Control plane — suspended_at + store seam

**Intent:** Auth resolutions carry suspension state; abuse store is reachable through one monkeypatchable seam.
**Acceptance:** `_QUOTA_SELECT` adds `suspended_at`, `email`; `resolve_api_key` returns both (registry path: Team node prop read in hosted_api registry branch); `team_by_id` select adds `suspended_at`; `supabase_control.get_abuse_store()` returns `SupabaseAbuseStore(cp)` (Supabase mode) / `MemoryAbuseStore` (registry), lazy singleton, monkeypatchable; `FakeControlPlane` emulates the trigger — `rpc('provision_team')` and `api_keys` POST insert a `key_create` abuse_events row (weight 1) UNLESS `created_via='bootstrap'` (mirrors 0015); fake `rpc('abuse_suspend'|'abuse_unsuspend')` toggles `teams.suspended_at` (+ clears `flagged_at` on un-suspend); fake stores weighted event rows and serves the `gt` created_at filter so `SUM(weight)`-equivalent counting works in tests.
**Files:**
- Modify: `tortoise/supabase_control.py` (`_QUOTA_SELECT`, `team_by_id`, `get_abuse_store`)
- Modify: `tests/fake_control_plane.py` (trigger emulation + suspend RPCs)
- Test: `tests/test_abuse.py` additions (fake trigger semantics: provision → 1 event; re-provision ON CONFLICT DO NOTHING → no duplicate; bootstrap mint → 0 events)

---

## Task 4: Notifications — abuse kinds

**Intent:** Owner/ops notification for all four abuse outcomes; never blocks, never raises.
**Acceptance:** `notify.KINDS` += `abuse_flag`, `abuse_suspended`, `abuse_new_ip`, `abuse_read_velocity`; `notify_abuse(kind, team, details)` sends Resend to `team.get('email')` — missing OR NULL both fall back to `BILLING_NOTIFY_TO` (plan-review P2: the registry team dict has no `email` key at all; KeyError would swallow every channel) — + Telegram; `abuse_suspended` additionally best-effort `alert_store.open_incident` via FUNCTION-LEVEL `from tortoise import hosted_api` + `hosted_api._alert_store_from(hosted_api._backup_config_safe())` inside try/except (import-direction + no-config safe). Callers in async REST context invoke via `await asyncio.to_thread(notify_abuse, ...)` (#310 pattern — sync httpx must not block the loop); MCP tool context already runs in a worker thread. Unit tests mock channels + assert no-raise on channel failure, NULL-email fallback, and MISSING-email fallback.
**Files:**
- Modify: `tortoise/notify.py`
- Test: `tests/test_abuse.py` additions

---

## Task 5: REST enforcement + recording

**Intent:** R5 on REST (403 SUSPENDED + appeal URL), R1/R2 recording+evaluation at REST seams, R3 GET counting, R4 geo, session-key mint gating.
**Acceptance:** `_get_current_team_supabase` + registry path: durable `suspended_at` → `HTTPException(403, detail={"code":"SUSPENDED","message":...,"appeal_url":...})` (suspended-signal set only forces fresh resolution — never rejects by itself); post-success geo check (best-effort) + read-velocity increment on `request.method == "GET"` only with key_id/team_id from the resolution; POST `/v1/points` success → `record_point_create(team_id, 1)` (which piggybacks R2 evaluation — this is what makes trigger-recorded signup-path key creates evaluate); `capture_session` success → weighted `len(conversation)+len(extracted)` (`/v1/demo` and demo-seed excluded — bounded fixed seed of ~20 points; documented decision); session-key mint (both registry + `_session_key_supabase`) rejects 403 SUSPENDED when the team is suspended (checked via the resolved team row); R2 `evaluate_key_creates` additionally runs after successful mints (recovery + dashboard-created) and after `register_user` provision. Registry mode: engine's registry-write callback wired to `_make_sdk(namespace='registry')` for durable `Team.suspended_at/flagged_at` writes. All hooks best-effort (try/except), gated by `TORTOISE_ABUSE_DISABLED`; notifications via `asyncio.to_thread`.
**Files:**
- Modify: `tortoise/hosted_api.py`
- Test: `tests/test_abuse_integration.py`

---

## Task 6: MCP enforcement + recording

**Intent:** R5 on MCP (−32006 + appeal), suspension immediacy vs the 60s LRU, R3 read counting with explicit write set, R1 weighted recording at `_quota_gated`.
**Acceptance:** `ERR_SUSPENDED = -32006`; `TeamResolutionMiddleware`: cache-hit entry whose team is in the suspended-signal set is treated as a miss (forces fresh resolution); fresh resolution with `suspended_at` → JSON-RPC −32006, `data={"code":"SUSPENDED","appeal_url":...}`, token's LRU entry popped; fresh resolution with NULL clears the signal entry (AC8 self-heal); geo check on EVERY post-auth request (cache-hit and miss) — middleware does NOT read the body; read-velocity increments at `_wrapped_call_tool` (mcp_server ~L199, plan-review P2: it already receives the tool name + team ContextVar — drops the per-POST body buffer/parse entirely): tool ∉ `WRITE_TOOL_NAMES` → `record_read(key_id from _current_team_limits, team_id)`; `WRITE_TOOL_NAMES` = explicit frozenset of all `_quota_gated`-wrapped tools INCLUDING `tortoise_ingest` + `tortoise_onboarding_demo_create` (demo is a write — keeps it out of R3 reads); `_quota_gated(fn, resource, abuse_weight=None)` records weighted `point_create` after successful write: `create_point`=1, `create_operator`=1, `mitigate_operator`=1 (plan-review P2: creates a Point via raw Cypher — PointAdded emission), `ingest`=`result['created']['points']`, `checkpoint`=`result['filed']`, `file_decision`=`1+len(options)+len(evidence)` from args, `file_human_approval`=1, `diary_write`=1; every other wrapped tool records nothing. `create_subject/object/entity/document/source/edge` are explicitly OUT (non-Point nodes — documented).
**Files:**
- Modify: `tortoise/mcp_auth.py`, `tortoise/mcp_server.py`
- Test: `tests/test_abuse_integration.py` (MCP section)

---

## Task 7: Signup CAPTCHA (Turnstile)

**Intent:** R6 — reuse the waitlist pattern: widget on signup.html, server-side siteverify on both mint-adjacent public endpoints, fail-open only when the secret is unset.
**Acceptance:** `website/signup.html` gains the `index.html` widget pattern (site-key literal, hidden when empty, token callbacks, `cf-turnstile-response` in the POST body to `/v1/signup/email`); `_verify_turnstile(token, ip)` in hosted_api: unset `TURNSTILE_SECRET_KEY` → True (logged once); set → siteverify via httpx in `asyncio.to_thread`, success field required; `/v1/signup/email` + `/v1/register` reject 400 ("Please complete the security check") when the secret is set and the token is missing/invalid, and pass through when unset.
**Files:**
- Modify: `website/signup.html`, `tortoise/hosted_api.py`
- Test: `tests/test_abuse.py` (siteverify unit, httpx mocked: unset→open, set+bad→400, set+ok→pass) + signup form safety pattern check

---

## Task 8: Dashboard — suspended banner + alerts

**Intent:** R7 — surface suspension + suspicious activity to the owner; keep one-click revoke live for the non-suspended response path.
**Acceptance:** API error parsing in `main.jsx` recognizes `detail.code === "SUSPENDED"` (dict detail, not string) → banner with the appeal link (from `detail.appeal_url`) instead of the generic error — applied in BOTH `api()` AND `mintSessionKey`'s own raw-fetch error path (plan-review P2: the mint path is the primary load path; its swallowed 403 would leave a suspended team with no banner); overview tab fetches session-authed `GET /v1/team/alerts` and renders an alerts list (type, time, message) when non-empty; `TeamInfoResponse.status` ∈ {"active","flagged"} over HTTP — "suspended" can never be returned by /v1/team (the 403 fires first); the suspended state renders exclusively from the 403 detail (plan-review P3 — no dead third state); revoke button unchanged. `GET /v1/team/alerts` added to hosted_api: session-authed (`get_current_user` + membership), returns `store.recent_alerts(team_id)` (flags, suspensions, velocity/geo notifications) — reachable during suspension by design (scoping delta 12).
**Files:**
- Modify: `website/apps/dashboard/src/main.jsx`, `tortoise/hosted_api.py`, `tortoise/abuse.py` (`recent_alerts` consumer contract)
- Test: `tests/test_abuse_integration.py::test_alerts_endpoint` (session override)

---

## Task 9: CLI — SUSPENDED detail parse

**Intent:** Agents see the appeal path, not a generic "key_rejected".
**Acceptance:** `__main__.py` 403 branches (init validate ~L324, team keys ~L873, team keys create ~L934, team keys REVOKE ~L999 — plan-review P3: a suspended team's revoke 403s and today prints the misleading "different team" message): parse JSON body; `detail.code == "SUSPENDED"` → failure message includes `appeal_url` and "team suspended" wording; unparseable bodies keep today's behavior.
**Files:**
- Modify: `tortoise/__main__.py`
- Test: `tests/test_abuse.py` (parse helper unit tests)

---

## Task 10: `.env.example` + docs

**Intent:** Deploy prerequisites are discoverable.
**Acceptance:** `.env.example` gains `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`, `ABUSE_APPEAL_EMAIL`, optional `IPINFO_TOKEN` (future), `TORTOISE_ABUSE_*` threshold overrides + `TORTOISE_ABUSE_DISABLED` with comments (which are Fly secrets vs static literals).
**Files:**
- Modify: `.env.example`

---

## Task 11: Integration suite (failure modes per issue)

**Intent:** The three named failure modes are tested end-to-end, plus E2E-2-D/E2E-7-D alignment.
**Acceptance:** `tests/test_abuse_integration.py` (TestClient + FakeControlPlane via `monkeypatch(tortoise.supabase_control.get_control_plane)` + `is_supabase_enabled` True + `RATE_LIMIT_DISABLED=1`):
1. **false positive:** 501 point POSTs in-window → flagged, NOT suspended; second-window breach (advance fake clock) → suspended; un-suspend → next request 200.
2. **bypass:** suspension survives fresh app instance (same fake store); signup-path counting: seed 11 `key_create` events via repeated fake `provision_team` rpc for ONE team (trigger emulation) → team's next hooked request (any point create — the R2 piggyback) raises the flag/suspend path (plan-review P1: register_user can't produce a per-team breach — 1 call = 1 new team); 11 bootstrap mints → no flag.
3. **exfiltration:** 101 GETs one key → `abuse_read_velocity` notify once; 60+51 across two keys → team notify; POST-heavy burst never fires R3.
4. **geo:** first `CF-IPCountry: US` → notify + event; repeat US → silent; DE → notify.
5. **REST suspension:** 403 detail code+appeal_url; **MCP suspension:** −32006 with same data, warm-cache immediacy, un-suspend warm-cache restore.
6. **R1 weights:** ingest-weighted burst flags at threshold; 500 updates never flag.
7. **mint gate + alerts endpoint + TeamInfo status.**
Introspection tests (dual membership): (a) every name in the R1 weight map IS a `_quota_gated`-wrapped tool, (b) the verified Point-creator list (create_point, create_operator, mitigate_operator, ingest, checkpoint, file_decision, file_human_approval, diary_write) ⊆ weight map, (c) `WRITE_TOOL_NAMES` ⊇ all wrapped tools → no write tool is ever counted as an R3 read. Malformed-JSON POST to /mcp never breaks auth.
**Files:**
- Create: `tests/test_abuse_integration.py`

---

## Task 12: Full suite + cleanup

**Intent:** No regressions; suite green on embedded FalkorDBLite.
**Acceptance:** `python -m pytest tests/ -q` green; `RATE_LIMIT_DISABLED` convention respected; no new deps in requirements.txt.

---

## Runtime prerequisites (PR note)

- Migration 0015 auto-applies via the existing Supabase migration flow.
- Fly secrets to set: `TURNSTILE_SECRET_KEY` (new), optionally `ABUSE_APPEAL_EMAIL`. Turnstile site key = hand-edited literal in signup.html/index.html (static site).
- No `TORTOISE_AUDIT_DSN` dependency.
- Deploy note: merge auto-deploys via `.github/workflows/deploy-hosted.yml` (tortoise/** changes in this PR are covered by the paths filter).

<!-- plan-review: status=clean cycles=2 reviewers=3(structural,integration,efficiency) date=2026-08-11 issue=308 -->
