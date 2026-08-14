---
title: "<!-- issue-scoping: v5.1 double diamond — Phase 5 solution-converge draft (pre-verify) -->"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

<!-- issue-scoping: v5.1 double diamond — Phase 5 solution-converge draft (pre-verify) -->

# Issue #307 — Transactional Email (Invites + Key Recovery) — Solution Converge + Plan Draft

> Status: **solution-converge draft** — pending solution-verify (Phase 5.5) and review gates.
> Tier: Standard. Epic: hosted-platform (#669 control-plane / #518 recovery lineage).

---

## 1. Approach Selection

### Decision: **Approach A — In-API Python monolith** (`email_notify.py` + `hosted_api.py` hooks), with two refinements lifted from C (bounded retry/backoff + Idempotency-Key on the recovery send; shared in-process send semaphore for both surfaces).

### Why A produces the better outcome

| Dimension | A (chosen) | B (edge functions) | C (worker + hosted templates) |
|---|---|---|---|
| **Outcome quality** | One email path, one implementation, one failure surface. Mirrors the proven `notify.py` precedent already in production for billing (#310). Templates in-repo = versioned + reviewable with the code. | Split-brain: Supabase mode uses Deno edge fns, registry/selfhost mode needs an in-API fallback (double implementation, double maintenance, double failure surface). | Templates unversioned in Resend (drift risk); queue not durable (crash drains sends); multiple Fly replicas ⇒ duplicate sends unless cross-replica idempotency (which requires the durable store C defers as "out of scope"). |
| **Edge cases** | Template render error → try/except + redacted log (invites) / 400-safe degrade (recovery). Bounce → 200≠delivered logged with Resend message id (webhooks explicitly deferred). Rate limit → shared semaphore paces both surfaces under Resend's 10 req/s/team; recovery retries 429 with backoff. Send timeout → `timeout=15.0` + bounded retry budget. Shutdown loss → module-level task registry drained in `_lifespan`. | Trigger wiring partially console-unversioned; at-least-once ⇒ dedup machinery needed in Deno; bounce/rate-limit handling reimplemented per-function. | Queue loss at crash is the killer: the invite email is the **ONLY** token delivery path — losing a queued send = token lost forever (dedup blocks re-invite). |
| **Failure mode coverage** | All four confirmed failure modes (render, bounce, rate limit, timeout) handled in one module with `redact_safe` logging discipline. Recovery: 4xx no-retry / 5xx-429-timeout retry distinction (photonconsole/DVARA research), exhaustion → code stays valid 30min + unconditional client resend affordance (never silent-skip). | Two codebases to cover; a bug in the edge fn or the fallback diverges behavior per-mode. | In-memory queue + multi-replica duplicate sends are genuine holes the plan itself flags. |
| **Future extensibility** | `email_notify.py` grows more send types (invoices, announcements) with the same two profiles. Genericized rate-limit helper (extracted from `_check_register_rate_limit`) is reusable. Webhooks can be layered later (svix-id dedup noted in research). | Scales edge fns independently — but only for Supabase mode; registry/selfhost forever divergent. | The worker + state machine is the correct long-term architecture for high volume — but this issue has exactly two rare, low-volume sends. Premature. |

### Rejected alternatives (with when they WOULD have been better)

**Approach B — Supabase edge-function sender.**
Rejected because: the hosted platform is dual-mode (Supabase + registry/selfhost both call `POST /v1/invites` in `hosted_api.py`), so B forces a Deno implementation PLUS an in-API fallback for registry mode — two implementations for one feature; the recovery flow must be awaited in-request anyway (must-arrive profile), so B's only real win (decoupling invites from the request via DB trigger) costs a second deploy surface, unversioned trigger wiring, and at-least-once dedup.
**It WOULD have been better if:** the platform were Supabase-only (no registry branch), or invite volume were high enough that request-path latency mattered (trigger → pg_notify decoupling), or the team already ran a fleet of edge functions with test infra. None hold here: invite volume is manual-admin rare; Deno test infra does not exist in this repo; registry mode is a first-class supported path.

**Approach C — Async worker + Resend-hosted templates + delivery state machine.**
Rejected because: the in-memory queue is not durable (a crash drains pending sends — unacceptable when the invite email is the ONLY token delivery path and re-invite is blocked by the 409 pending dedup), and multiple Fly replicas each run their own queue ⇒ duplicate sends unless idempotency is enforced against a durable store — exactly the outbox infrastructure C defers out of scope. Resend-hosted templates are unversioned state (drift risk, ops-doc burden) for two emails.
**It WOULD have been better if:** send volume grew past roughly tens of sends/minute, multiple async notification types (email + webhooks + Slack) needed a shared delivery pipeline, or a durable outbox were already present. The bounded retry/backoff + Idempotency-Key + semaphore pacing that C offers are cheaply absorbed into A (done below) without the queue.

**Decision-log note — folded-endpoint alternative (coherence-fix):** considered folding the recovery-email trigger into the existing `POST /v1/session/key` (a `purpose=recovery` with no code → mint + email the link) instead of a new `POST /v1/session/recovery-email`. Rejected: the trigger and the mint have different auth/session semantics (trigger = session only, must NOT mint; mint = session+code, mints), different rate-limit surfaces (per-email 3/hr + per-IP 5/hr on the trigger vs none on the mint), and the identical-200 anti-enumeration contract is cleanest on a dedicated endpoint with no mint side-effect. Separate endpoint keeps each contract simple; the new `recovery_codes` table is required either way (single-use + hash-only storage needs state).

### Refinements absorbed from C into A (justified, cheap, high-value)

1. **Recovery send retry budget**: awaited in-request; attempts at 0s / 0.5s / 2s / 8s on transient failures only (429, 408, 502, 503, 504, timeout, network error) with `Idempotency-Key: recovery:{code_hash}` (Resend dedups retries within 24h). 4xx never retried (invalid payload, 403, 422 — retrying burns reputation per photonconsole/DVARA). No 5xx to the client on send-failure paths, with the single exception of a **uniform 503 when the sender is unavailable-by-config** (deploy-time misconfiguration — applies to every request, enumeration-free; see §3 response contract).
2. **Shared send semaphore**: one in-process `asyncio.Semaphore(4)` limiting concurrency on both send profiles — cheap insurance with documented headroom: expected volume (a few sends/hr) sits far below Resend's 10 req/s/team ceiling (billing notify shares the same key) — a concurrency cap, not a rate limiter; no worker needed.
3. **Invite task registry**: module-level `_pending_email_tasks` set + drain in `_lifespan` shutdown — closes the `create_task` loss-at-shutdown risk (confirmed problem's stated risk for A).

---

## 2. Confirmed Problem (from Phase 2, verified)

Build the hosted platform's transactional email path (Resend) for exactly two notifications: **(1)** team-invitation email triggered at both branches of `POST /v1/invites` (Supabase branch after `invitation_mint` ~hosted_api.py:3106; registry branch after the `CREATE` ~3171) linking into the existing accept flow — profile: async best-effort **with logging**, because email is the ONLY token delivery path (dashboard `inviteMember()` at main.jsx:650 discards the token; admin has no fallback today); **(2)** post-signup key-regain email delivering a short-lived single-use **LINK** to the session-authenticated recovery mint (`POST /v1/session/key purpose=recovery`) — NEVER the plaintext key (hash-only at rest, reveal-null); profile: must-arrive — sync send + retry/backoff budget + user-visible resend affordance, never silent-skip. Failure modes: template render error, bounce, rate limit, send timeout. Anti-enumeration: identical response for known/unknown emails on the recovery trigger. Link host from config (`EMAIL_LINK_BASE_URL`), never request `Host` header. HTTPS + noreferrer on landing. Per-account 3–5/hr + per-IP caps per OWASP recovery guidance.

---

## 3. Proposed Solution

### Architecture (Approach A + refinements)

```
POST /v1/invites  ──(both branches)──►  email_notify.send_invite_email(invite)   [async best-effort]
   │  mint (Supabase invitation_mint / registry CREATE)                            │ create_task → first attempt + 1 retry (0.5s, transient only)
   │  email_sent_at ← stamped on provider accept (invitations.email_sent_at /      │ redacted WARNING on final failure; drained at shutdown
   │                     Invitation node property in registry)                     ▼
   └─────────────────────────────────────────────────────────────►  https://api.resend.com/emails
                                                                        Bearer RESEND_API_KEY · User-Agent · timeout=15.0

POST /v1/session/recovery-email  ──(session-authenticated trigger)──►  email_notify.send_recovery_link(email, code)   [must-arrive]
   │  per-email 3/hr + per-IP 5/hr buckets (genericized limiter)                   awaited in-request: 0/0.5/2/8s on transient, Idempotency-Key
   │  known email (teams.email match): mint CSPRNG ≥256-bit code,                  NEVER plaintext key; link = {EMAIL_LINK_BASE_URL}/recover.html?c=CODE
   │    store SHA-256(pepper+code) in recovery_codes, TTL 30min, single-use        identical 200 for known/unknown; 429 only on bucket exhaustion
   │  unknown email: identical 200, nothing minted/sent                            registry mode → 501 (hosted-only feature)
   ▼
{EMAIL_LINK_BASE_URL}/recover.html?c=CODE  ──►  static landing (HTTPS, noreferrer) ──►  app.premiselabs.co/?recover_code=CODE
   ▼
POST /v1/session/key {purpose:'recovery', recovery_code: CODE}  ──►  validate code (lookup_hash match, TTL, used_at IS NULL) → mint → null code

{EMAIL_LINK_BASE_URL}/invite-accept.html?token=TOKEN ──► static landing ──► app.premiselabs.co/?invite_token=TOKEN
   ▼
POST /v1/invites/accept {token} (existing endpoint, session-auth) — accept UI = minimal status line only
```

### New module: `tortoise/email_notify.py` (mirrors `notify.py`)

- `RESEND_URL`, `_env`, `_skip_channel` (once-per-process skip log), `redact_safe` — identical discipline to `notify.py:37-117`.
- `_send_resend(to, subject, html, text, idempotency_key)` → `httpx.post(RESEND_URL, headers={Authorization Bearer, Content-Type, User-Agent: "tortoise-api/…", **Idempotency-Key}, json={from: RESEND_FROM_EMAIL, to:[to], subject, html, text}, timeout=15.0)` → `raise_for_status()` → returns `{"id": …}`.
- **Send profiles:**
  - `send_invite_email(team_name, invitee_email, role, token, base_url, invitation_id)` — builds invite link `{base_url}/invite-accept.html?token={token}`; **never raises**; schedules via `asyncio.create_task` into `_pending_email_tasks`; task = first attempt + one 0.5s retry on transient; stamps `email_sent_at` via callback; redacted `WARNING` on final failure; `email_sent_at` recorded only on provider-accept (200).
  - `send_recovery_link(email, code, base_url)` — **awaited**; builds `{base_url}/recover.html?c={code}`; retry budget 0.5/2/8s on transient-only (429/408/502/503/504/timeout/network); `Idempotency-Key: recovery:{code_hash}`; 4xx → immediate fail (no retry); returns `(ok, message_id)` — exhaustion is NOT raised to the caller's response contract (see anti-enumeration).
- Templates: in-repo inline HTML + plain-text fallback (no jinja2 dep — `html.escape` for every interpolated value: team name, role, email). `text` body included alongside `html` (spam-filter/accessibility hardening over `notify.py`'s html-only).
- `FROM` from `RESEND_FROM_EMAIL` env (default `noreply@premiselabs.co`); link host from `EMAIL_LINK_BASE_URL` env (default `https://tortoise.premiselabs.co`).
- Shared `asyncio.Semaphore(4)` pacing both profiles (Resend 10 req/s/team headroom — #885/#801 precedent).

### Recovery-link mint flow (end-to-end)

1. **Trigger** — `POST /v1/session/recovery-email` (session-authenticated; `user["email"]` from JWT, never from body). Genericized rate limiter: per-email 3/hr + per-IP 5/hr (OWASP 3–5 / 5–10 ranges). Lookup `teams.email` (Supabase mode): known → mint code, store, send; unknown → identical 200, no mint, no send. `RATE_LIMIT_DISABLED=1` opts out (existing precedent).
2. **Code mint** — `secrets.token_urlsafe(32)` (≥256-bit CSPRNG); stored as `lookup_hash(code)` (SHA-256(pepper+code) — same scheme as api_keys/invitations) in new `recovery_codes` table; `expires_at = now + 30min`; `used_at NULL`. Stale-row cleanup: DELETE prior expired rows AND any prior unused valid rows for that email on re-mint (a resend invalidates the previous link — one live code per email; bounded table, no cron).
3. **Send** — awaited with retry budget (§1 refinement 1). Response contract: **always identical 200** (known/unknown, sent/failed-after-budget), **with one exception: a uniform 503 for ALL requests when the sender is unavailable-by-config** (RESEND_API_KEY / RESEND_FROM_EMAIL missing — deploy-time misconfiguration, short-circuits before the email lookup, so it leaks no existence signal; it is a loud deploy gate, not a per-send outcome). 429 only when the shared bucket is exhausted (bucket counts all requests — unknown emails consume it too, so 429 leaks no existence signal). No other 5xx from this endpoint (send failure ≠ enumeration signal).
4. **Landing** — `{EMAIL_LINK_BASE_URL}/recover.html?c=CODE` static page (HTTPS, `Referrer-Policy: no-referrer`, `rel=noreferrer` on any outbound link); reads `?c`, deep-links to dashboard preserving the code.
5. **Redeem** — dashboard (session-active) calls `POST /v1/session/key {purpose:'recovery', recovery_code: CODE}`; endpoint validates (hash match, `expires_at > now`, `used_at IS NULL`) then consumes (`UPDATE … SET used_at=now() WHERE used_at IS NULL` — rowcount gate, race-safe single-use) and mints per the existing recovery rules (max_api_keys cap, oldest-OTHER auto-revoke #750.10). Invalid/expired/used → 400 "Invalid or expired recovery link". Code omitted → existing in-session mint proceeds unchanged (backward compat — no regression on dashboard `recoverKey()` main.jsx:864).
6. **Resend affordance** — dashboard always shows "Check your inbox — no email? Resend" (unconditional, preserving the identical-response contract); 429 surfaces Retry-After copy.

### Invite email link contract + minimal destination

- Link: `{EMAIL_LINK_BASE_URL}/invite-accept.html?token={token}` (token = the existing minted invite token; hash-only at rest already — 0008 / registry node).
- **Minimal destination (in scope):** static `invite-accept.html` — "You've been invited to join a team on Tortoise" + [Sign in to accept → `app.premiselabs.co/?invite_token=…`] + [No account? Create one → `https://tortoise.premiselabs.co/signup.html`]. No accept form on the landing; **full accept UI OUT** — accept remains the existing `POST /v1/invites/accept` (session-authenticated, token-only, E4). Referrer-Policy no-referrer (securepatterns.dev), HTTPS.
- **No-account invitee branch:** landing routes to signup (existing page); after signup + provisioning, the dashboard auto-attempts accept from the stashed `invite_token` param (see dashboard tasks). Invite TTL stays 7 days.
- Dashboard `main.jsx` gains param handling (precedent: the `session_id`/`checkout` URLSearchParams effect at main.jsx:168, stripped via `history.replaceState` after handling): `invite_token` → accept attempt (session present) or stash-and-accept-after-sign-in; `recover_code` → recovery mint with code (same stash rule).

### `EMAIL_LINK_BASE_URL`

New env var, read by `email_notify.py` (both senders) and the recovery trigger. Default `https://tortoise.premiselabs.co` (the Cloudflare Pages site serving `website/`). **Never** the request `Host` header (Host-header injection — OWASP). Landing pages deep-link to the dashboard (`app.premiselabs.co`) by hardcoded host, matching `welcome.html:487`'s existing pattern.

---

## 4. Implementation Plan

> Conventions follow `docs/plans/*.md` (Intent/Acceptance/Files/Steps) and the 669-plan / 7714-data-model format.

### Integration Surface Map

| # | Surface | Type | Test Layers | Key Failure Modes |
|---|---------|------|-------------|-------------------|
| S1 | `tortoise/email_notify.py` (new) | HTTP outbound (Resend) | unit (monkeypatch `httpx.post` — test_notify.py pattern) | render error, 4xx/5xx distinction, timeout, rate limit, redaction, env-gated skip |
| S2 | `POST /v1/invites` (both branches) | API hook | HTTP (TestClient) both branches + FakeControlPlane (Supabase) | email failure must not fail mint; email_sent_at stamp; duplicate sends (idempotency key) |
| S3 | `POST /v1/session/recovery-email` (new) | API endpoint | HTTP + FakeControlPlane + rate-limit tests | identical-200 contract, anti-enumeration, buckets, registry-mode 501, send-exhaustion handling |
| S4 | `recovery_codes` table (migration 0011) | Postgres (Supabase) | FakeControlPlane rows + migration SQL review | single-use race, TTL, stale rows, code plaintext never stored |
| S5 | `POST /v1/session/key` recovery_code extension | API endpoint | HTTP (test_session_key_http.py extension) | invalid/expired/used code → 400; no-code backward compat; consume-before-mint |
| S6 | Landing pages (`website/invite-accept.html`, `website/recover.html`) | Static (Cloudflare Pages) | static checks (test_website_static.py pattern) + manual clickthrough | param loss on redirect, noreferrer/HTTPS, token leak in referrer |
| S7 | Dashboard (`main.jsx`) | React SPA | manual clickthrough (fork-mode — no SPA harness; stripe-plan precedent) | param re-submission on refresh (strip after handling), stash-across-sign-in, resend affordance |
| S8 | `_lifespan` shutdown drain | Runtime | unit (task registry) | create_task loss at shutdown |

**Bug Pattern Flags:**
- Resend 200 ≠ delivered — `email_sent_at` = provider-accepted, never "delivered"; message id logged for post-hoc lookup.
- Retrying 4xx burns reputation — 4xx never retried (photonconsole/DVARA).
- Duplicate sends under retry — Idempotency-Key on both profiles (recovery: `recovery:{code_hash}`; invite: `invite:{token_hash}` — unify with Task 3).
- Host-header injection — link host from `EMAIL_LINK_BASE_URL` only.
- Code reuse after email leak — single-use consumed via `UPDATE … WHERE used_at IS NULL` rowcount gate.
- Per-process env cache goes stale across tests — `email_notify._skip_logged` cleared in fixtures (test_notify.py precedent).

---

### Task 1: `email_notify.py` — Resend sender + templates + profiles

**Intent:** Add the transactional email module with the two send profiles, in-repo templates, env gating, and the shared send semaphore — the single send surface both features hook into. Zero behavior change to existing surfaces.

**Acceptance:** `send_invite_email` never raises and stamps `email_sent_at` only on provider-accept (200); `send_recovery_link` retries 0.5/2/8s on transient-only with `Idempotency-Key: recovery:{code_hash}`, no-retry on 4xx, returns `(ok, message_id)`; both honor `RESEND_API_KEY`/`RESEND_FROM_EMAIL`/`EMAIL_LINK_BASE_URL` envs with once-per-process skip logging; templates HTML-escape all interpolated values; secrets never appear in logs (redact_safe).

**Files:**
- Create: `tortoise/email_notify.py`
- Create: `tests/test_email_notify.py`

**Step 1:** Module skeleton mirroring `notify.py` — `RESEND_URL = "https://api.resend.com/emails"`, `_env`, `_skip_channel` (once-per-process set), `redact_safe` reuse.
**Step 2:** `_send_resend(to, subject, html, text, idempotency_key)` — `httpx.post` with Bearer + `Content-Type: application/json` + **`User-Agent: tortoise-api/0.1`** (documented as required by Resend — 403 without; NOTE: `notify.py` sends httpx's default UA and works in production — verify the claim at implementation and migrate notify.py to the explicit header if confirmed) + conditional `Idempotency-Key`; `timeout=15.0`; `raise_for_status()`; return `{"id": …}`. FROM = `RESEND_FROM_EMAIL` env (default `noreply@premiselabs.co`); link base = `EMAIL_LINK_BASE_URL` env (default `https://tortoise.premiselabs.co`).
**Step 3:** Templates — `_invite_html(team_name, role, link)`, `_invite_text(...)`, `_recovery_html(link)`, `_recovery_text(...)`: minimal inline-styled HTML with CTA link + plain-text bodies; `html.escape` on team_name/role/email/link; no jinja2.
**Step 4:** `send_invite_email(team_name, invitee_email, role, token, base_url, invitation_id, on_sent)` — builds link, wraps in `asyncio.create_task` registered in module-level `_pending_email_tasks`; task: first attempt + one 0.5s retry on transient-only; `on_sent(message_id)` callback on accept; redacted `WARNING` on final failure (with message id when available); never raises.
**Step 5:** `send_recovery_link(email, code, base_url)` — awaited; attempts 0/0.5/2/8s on transient-only (429/408/502/503/504/timeout/network); `Idempotency-Key: recovery:{code_hash}`; 4xx immediate fail; return `(ok, message_id)`; redacted `WARNING` on exhaustion.
**Step 6:** Shared `_send_semaphore = asyncio.Semaphore(4)` acquired in `_send_resend` — concurrency cap with documented headroom (volume ≪ 10 req/s/team; not a rate limiter).
**Step 7:** `drain_pending_sends(timeout=2.0)` — awaited at shutdown (Task 8 wiring).
**Step 8:** Unit tests (test_notify.py pattern): payload shape (to/from/subject/html/text/idempotency-key), User-Agent present, env-gated skip (absent key → no call + once-log), render error swallowed (invites), 4xx no-retry vs 5xx/429/timeout retry counts, Idempotency-Key stable across retries, redaction (secret in exception message never lands in caplog), semaphore serialization, `on_sent` only on 200.

---

### Task 2: Migration 0011 — `email_sent_at` + `recovery_codes`

**Intent:** Schema for send audit + recovery codes. `email_sent_at` on invitations (provider-accept timestamp, audit + ops triage for pending invites with no send); `recovery_codes` table (code hash at rest, TTL, single-use). Hosted-only (Supabase).

**Acceptance:** Migration applies idempotently (`supabase db push --linked --include-all` dry-run); `recovery_codes` has unique index on `code_hash`; invitations gains `email_sent_at timestamptz NULL` + GRANT update to service_role + GRANT SELECT of `email_sent_at` to authenticated; registry mode unaffected (email_sent_at = Invitation node property, set by the hook task).

**Files:**
- Create: `supabase/migrations/0011_transactional_email.sql`

**Step 1:** `ALTER TABLE public.invitations ADD COLUMN IF NOT EXISTS email_sent_at timestamptz;` + extend the existing `GRANT SELECT (…, email_sent_at)` and service_role ALL (already table-level).
**Step 2:** `CREATE TABLE IF NOT EXISTS public.recovery_codes (id text PRIMARY KEY, email text NOT NULL, code_hash text NOT NULL, expires_at timestamptz NOT NULL, used_at timestamptz, created_at timestamptz NOT NULL DEFAULT now());` + `CREATE UNIQUE INDEX IF NOT EXISTS uq_recovery_codes_code_hash ON public.recovery_codes (code_hash);` + `CREATE INDEX IF NOT EXISTS idx_recovery_codes_email ON public.recovery_codes (email);` + RLS: service_role ALL, no authenticated access (code hashes never readable by clients).
**Step 3:** Column protection on `recovery_codes.code_hash` (mirror 0008's REVOKE + column-grant pattern — hashes must never be exposed to anon/authenticated).

---

### Task 3: Invite email hooks (both branches of `POST /v1/invites`)

**Intent:** Wire `send_invite_email` after mint in the Supabase branch (hosted_api.py:3106-3108) and the registry branch (3159-3171); stamp `email_sent_at`; surface `email_status` so an unconfigured/failed send is never indistinguishable from success. Email failure must never fail the mint (best-effort, per confirmed profile).

**Acceptance:** Every successful mint (both branches) schedules exactly one invite email with the minted token; `email_sent_at` set on provider-accept (Supabase row / registry node property); a mocked send failure still returns the existing 200 mint response + `email_status: "failed"` (or `"unconfigured"`) + redacted WARNING — never silent (coherence-fix); dedup-safe with stable `Idempotency-Key: invite:{token_hash}` (coherence-fix: prevents double-send on crash-retry); registry-mode email works with the same module (no Supabase dependency for the invite hook); spoofed-Host requests still build links from `EMAIL_LINK_BASE_URL`.

**Files:**
- Modify: `tortoise/hosted_api.py` (invite_to_team, both branches)
- Modify: `tortoise/supabase_control.py` (email_sent_at write helper for invitations)
- Create: `tests/test_invites_email_http.py`

**Step 1:** In the Supabase branch after `inv = invitation_mint(...)` (before the return at 3108): call `send_invite_email(team.get("name") or "your team", email, role, inv["token"], email_link_base, inv["id"], on_sent=lambda mid: _set_invite_email_sent(cp, inv["id"]))`. Lazy-import `email_notify` inside the branch (matches the file's lazy-import convention).
**Step 2:** Add `_set_invite_email_sent(cp, invitation_id)` in `supabase_control.py` (PATCH `invitations` `email_sent_at = now()`; fail-closed: RuntimeError on query error per #851 contract).
**Step 3:** Registry branch after the `MERGE` (before return at 3171): schedule the same send with `iid`; on_sent sets `SET i.email_sent_at = $now` on the Invitation node.
**Step 4:** `email_link_base()` helper — reads `EMAIL_LINK_BASE_URL` once per request (no per-process cache, so tests can flip it); never touches `request.headers.get("host")`.
**Step 5:** HTTP tests (test_invites_http.py fixture pattern): registry branch mint → monkeypatched `send_invite_email` called once with token/link; send raises → mint still 200 + WARNING logged; Supabase branch via FakeControlPlane → same assertions; `email_sent_at` stamped only when send returns 200; link built from `EMAIL_LINK_BASE_URL` even with a spoofed Host header.

---

### Task 4: Recovery trigger — `POST /v1/session/recovery-email` + genericized rate limiter

**Intent:** The must-arrive recovery send surface with anti-enumeration, OWASP buckets, and the code mint. Refactor the register limiter into a reusable helper without changing register's behavior.

**Acceptance:** Identical 200 for known/unknown session emails (known = `teams.email` match in Supabase mode; registry mode → 501 with clear detail; session with no email claim → identical 200, no mint — uniform across all such sessions); **uniform 503 for ALL requests when the sender is unavailable-by-config** (RESEND_API_KEY / RESEND_FROM_EMAIL missing — no enumeration signal, deploy-time misconfiguration is loud; tested) (coherence-fix); per-email 3/hr + per-IP 5/hr buckets (429 with Retry-After on exhaustion; `RATE_LIMIT_DISABLED=1` opt-out; per-replica N× multiplier documented and accepted — single-use + 30-min TTL bounds harm; shared-store limiter = follow-up issue) (coherence-fix); known email → code minted (CSPRNG ≥256-bit, SHA-256(pepper+code) at rest, 30-min TTL) + link email sent awaited with the Task 1 retry budget; unknown → nothing minted/sent; code never in the response; link host from `EMAIL_LINK_BASE_URL`; stale rows for the email deleted on trigger.

**Files:**
- Modify: `tortoise/hosted_api.py` (limiter refactor + new endpoint)
- Modify: `tortoise/supabase_control.py` (recovery-code mint/consume helpers)
- Create: `tests/test_recovery_email.py`

**Step 1:** Genericize `_check_register_rate_limit` (1217-1247) → `_check_ip_rate_limit(request, buckets, key, max_per_hour, label)` + `_check_email_rate_limit(email, buckets, max_per_hour, label)`, preserving the memory bound (10k entries, dead-bucket sweep) and `RATE_LIMIT_DISABLED`; register keeps 3/hr with identical behavior (existing tests stay green).
> **Pepper note (coherence-fix):** recovery codes are hashed as `SHA-256(TORTOISE_SECRET_PEPPER + code)` via `lookup_hash` — `TORTOISE_SECRET_PEPPER` is an existing env (already a Fly secret; invitation `lookup_hash` uses it). No new secret. Rotation/persistence note: hashes must survive redeploys within the 30-min code TTL (pepper is stable by design).

**Step 2:** `recovery_mint_code(cp, email)` / `recovery_consume_code(cp, code)` / `recovery_code_lookup(cp, code)` in `supabase_control.py`: mint = DELETE stale expired rows AND any prior unused valid rows for that email (a resend invalidates the previous link — one live code per email), INSERT `{id, email, code_hash: lookup_hash(code), expires_at: now+30min}`; consume = `UPDATE … SET used_at=now() WHERE code_hash=$h AND expires_at > $now AND used_at IS NULL` rowcount gate (0 → invalid/expired/used); lookups return nothing but the code_hash (never email → timing-safe-ish; identical 400 downstream).
**Step 3:** New endpoint `POST /v1/session/recovery-email` (depends `get_current_user`): validate session email present; apply per-email + per-IP buckets; `is_supabase_enabled()` false → 501 (documented: hosted-only feature; selfhost uses in-session recovery); known-email lookup on `teams.email`; unknown → return the identical 200 payload; known → mint + `send_recovery_link(email, code, email_link_base())` awaited; **return the same 200 payload in every path** (send result only logged; code TTL bounds staleness; dashboard offers resend).
**Step 4:** Response payload: `{"status": "sent"}` (identical everywhere) — no code, no email-existence field.
**Step 5:** Tests: known/unknown identical 200 (assert bodies equal); unknown → no `send_recovery_link` call (monkeypatched) + no `recovery_codes` row (FakeControlPlane); buckets → 4th per-email / 6th per-IP request 429 + Retry-After, `RATE_LIMIT_DISABLED=1` bypass; registry mode 501; link host from env not Host header (spoofed Host test); code stored as lookup_hash only (assert plaintext absent from FakeControlPlane rows); stale rows purged on mint.

---

### Task 5: Recovery mint extension — `recovery_code` gate on `POST /v1/session/key`

**Intent:** Allow the email-link path to gate the existing session mint with the out-of-band code; preserve the existing in-session mint (no regression on `recoverKey()`).

**Acceptance:** `purpose=recovery` with valid `recovery_code` → code consumed (used_at set) and key minted per existing recovery rules; expired/used/wrong code → 400 "Invalid or expired recovery link" and NO mint; code omitted → mint proceeds exactly as today; bootstrap purpose with a code → 422 (code is recovery-only); registry mode with a code → 400 (codes are hosted-only; no table).

**Files:**
- Modify: `tortoise/hosted_api.py` (`session_key` + `_session_key_supabase`)
- Modify: `tests/test_session_key_http.py` (extend)
- Modify: `tests/test_auth_flip.py` (Supabase branch, if it covers session-key mint — verify at implementation)

**Step 1:** In `session_key` (and `_session_key_supabase`): accept optional `recovery_code`; if present, run ALL fail-prone validation (cap checks, membership, tier) FIRST, then `recovery_consume_code(cp, code)` (atomic rowcount-gated UPDATE) immediately before the mint — consume-then-mint, so a failed mint (e.g., 402 at cap) does not burn the code (user re-triggers within 30min).
**Step 2:** Registry-mode (non-Supabase) with a code → 400 (documented hosted-only).
**Step 3:** Extend HTTP tests: valid code → 200 mint + code single-use (second call with same code → 400); expired (FakeControlPlane row with past expires_at) → 400; wrong hash → 400; code + bootstrap purpose → 422; no code → existing mint tests unchanged (assert no regression); consume-before-cap: at max_api_keys with code → 402, then same code still valid (not burned).

---

### Task 6: Landing pages — `website/invite-accept.html` + `website/recover.html`

> **No-session recovery branch (coherence-fix):** recover.html for a user with no session links to sign-in; after sign-in the stashed `recover_code` (still valid within its 30-min TTL) resumes the mint via the dashboard path. Same stash-across-sign-in mechanism as invite_token. v1 requires session+code (code alone must not mint a key); relaxing to session-OR-code auth is a deliberate non-goal.

**Intent:** Minimal static destinations for both email links (hosted on the existing Pages site, versioned in-repo, HTTPS + noreferrer), deep-linking to the dashboard with the param preserved. Full accept UI OUT.

**Acceptance:** Both pages are static (no build step), set `Referrer-Policy: no-referrer` (meta), read their query param client-side and redirect to `https://app.premiselabs.co/?invite_token=…` / `?recover_code=…`; invite page also links signup for no-account invitees; no API calls from these origins (no CORS change needed); token/code never rendered into the DOM beyond the redirect (or logged).

**Files:**
- Create: `website/invite-accept.html`
- Create: `website/recover.html`
- Modify: `tests/test_website_static.py` (if it enumerates pages — verify; add parity checks for the two new pages: presence of referrer policy, param pass-through JS)

**Step 1:** `invite-accept.html` — "You've been invited to join a team on Tortoise"; [Sign in to accept] → `https://app.premiselabs.co/?invite_token=<token>`; [No account? Create one] → `https://tortoise.premiselabs.co/signup.html`; inline script: `new URLSearchParams(location.search).get('token')` guard (missing token → "This invite link is missing its token — ask the person who invited you to resend it").
**Step 2:** `recover.html` — "Recover your Tortoise API key"; [Open Dashboard] → `https://app.premiselabs.co/?recover_code=<code>`; missing `c` → friendly invalid-link copy.
**Step 3:** Meta tags: `<meta name="referrer" content="no-referrer">`, viewport, minimal inline CSS matching welcome.html's palette (no external assets).

---

### Task 7: Dashboard — param handling + resend affordance (`main.jsx`)

> **Manual-share fallback (coherence-fix):** the dashboard KEEPS the `token` from the POST /v1/invites response (currently discarded at main.jsx:659-666). On `email_status: failed|unconfigured` it shows an inline "Email failed to send — share this invite link manually" row with a copy-to-clipboard button for `{EMAIL_LINK_BASE_URL}/invite-accept.html?token=…`. The manual-share fallback is the admin-side counterpart of the best-effort contract — email remains the only AUTOMATED path; the API's returned token is the source.

**Intent:** Consume the email links (invite_token → accept; recover_code → recovery mint), add the "Email me a recovery link" trigger + unconditional resend affordance, and auto-accept stashed invites after sign-in. Minimal UI (status line / buttons) — full accept UI OUT.

**Acceptance:** With `?invite_token=…` + active session → accept attempted once, success/error surfaced, param stripped (`history.replaceState` precedent at main.jsx:168); with param + no session → stashed and accepted after sign-in; with `?recover_code=…` → recovery mint with code, key adopted via existing recoverKey flow; keys panel gains "Email me a recovery link" → POST `/v1/session/recovery-email` → "Check your inbox — no email? Resend" + 429 copy; param re-entry on refresh does not resubmit (stripped after first handling).

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx`

**Step 1:** Extend the URLSearchParams effect (main.jsx:168) with `invite_token` and `recover_code` handling + strip-after-handle.
**Step 2:** `invite_token`: session present → `POST /v1/invites/accept {token}` (existing endpoint) → success banner ("Welcome to the team!") + `loadMembers`; error → inline error; no session → stash in ref, hook accept after the sign-in success path sets `sessionTokenRef`.
**Step 3:** `recover_code`: session present → call the recovery mint with `{purpose:'recovery', recovery_code}` (extend the `recoverKey()` body construction at main.jsx:879) → adopt key via the existing post-mint block; no session → stash, run after sign-in.
**Step 4:** Keys panel: "Email me a recovery link" button beside "Generate a new one" (main.jsx:1138) → POST `/v1/session/recovery-email` → confirm line "Check your inbox — no email? Resend" with a Resend button (re-POST); 429 → Retry-After message.
**Step 5:** Manual clickthrough verification (fork-mode precedent — no SPA test harness in repo): signup → lose key → recovery link → redeem → key adopted; invite → accept → membership active; refresh-with-param → no duplicate submit.

---

### Task 8: Runtime wiring — env, deploy, shutdown drain

> **Resend sending-domain verification (coherence-fix):** premiselabs.co is verified on Resend per #832 (billing `billing@premiselabs.co` sends today; GoTrue SMTP smtp.resend.com:587 wired). Before rollout, run a pre-deploy probe `GET https://api.resend.com/domains` asserting the `RESEND_FROM_EMAIL` domain (default `noreply@premiselabs.co`) is verified — as a deploy-hosted.yml step or documented manual check. Unverified domain = every real send fails = the ONLY automated token delivery path is dead.

**Intent:** Make the feature runnable in prod: env vars, Fly secrets, `.env.example`, lifespan drain, docs.

**Acceptance:** `deploy-hosted.yml` passes `RESEND_FROM_EMAIL` + `EMAIL_LINK_BASE_URL` when set (pattern: BILLING_NOTIFY_TO lines 118-119); `.env.example` documents all three transactional vars; `_lifespan` (hosted_api.py:170) awaits `email_notify.drain_pending_sends(timeout=2.0)` on shutdown (create_task-loss mitigation); `RESEND_API_KEY` + `RESEND_FROM_EMAIL` documented as Fly secrets; migration 0011 applied via existing `supabase-deploy.yml` db push (no workflow change).

**Files:**
- Modify: `.github/workflows/deploy-hosted.yml`
- Modify: `.env.example`
- Modify: `tortoise/hosted_api.py` (`_lifespan`)
- Modify: `README.md` / `docs/` (ops note: transactional email vars; resend.dev test addresses for local integration)

**Step 1:** deploy-hosted.yml — append the two `[ -n "${{ secrets.X }}" ] && ARGS="$ARGS X=…"` lines next to RESEND_API_KEY.
**Step 2:** `.env.example` — new "Transactional email (#307)" section: `RESEND_API_KEY` (already documented for billing — reference), `RESEND_FROM_EMAIL=noreply@premiselabs.co`, `EMAIL_LINK_BASE_URL=https://tortoise.premiselabs.co`.
**Step 3:** `_lifespan` shutdown block — `await email_notify.drain_pending_sends(timeout=2.0)` wrapped in try/except (shutdown must never hang the deploy).
**Step 4:** ops doc note: delivery verification via Resend dashboard (`GET /emails/{id}` last_event) — see Verification Plan.
**Step 5:** confirm `TORTOISE_SECRET_PEPPER` is present as a Fly secret and STABLE across redeploys within the code TTL (no rotation during rollout — recovery-code hashes depend on it; see Task 4 pepper note).

---

### Task 9: Opt-in integration check (resend.dev test addresses)

> **Link-resolution assertion (coherence-fix):** the opt-in test also fetches the constructed `{EMAIL_LINK_BASE_URL}/invite-accept.html?token=…` and `/recover.html?c=…` URLs, asserting HTTP 200 + the correct landing page + `Referrer-Policy: no-referrer` — delivered-but-404 links are a false-green the terminal-event check alone would miss.

**Intent:** Prove real provider-acceptance + delivery against Resend's test addresses without requiring email in CI. False-green guard: 200 ≠ delivered.

**Acceptance:** `tests/test_email_integration_resend.py` marked `@pytest.mark.integration`, skipped unless `RESEND_API_KEY` + `RESEND_FROM_EMAIL` set; sends to `delivered@resend.dev` (expect last_event=delivered) and `bounced@resend.dev` (expect bounced) via the real `email_notify` module; polls `GET https://api.resend.com/emails/{id}` until terminal event (60s timeout); asserts `email_sent_at`-style semantics (provider-accepted 200 with id) AND the terminal event — never "delivered" from a bare 200.

**Files:**
- Create: `tests/test_email_integration_resend.py`

**Step 1:** Env-gated fixture (skip if no `RESEND_API_KEY`); use `_send_resend` with test addresses; retrieve + poll the email record; assert terminal events.
**Step 2:** Document run command in the file header (not in CI — python-ci.yml stays network-free for email).

---

## 5. Testing Strategy

Mirror repo patterns (no real network in CI):

| Layer | File | Pattern | Covers |
|---|---|---|---|
| Unit — sender | `tests/test_email_notify.py` | monkeypatch `httpx.post` (test_notify.py) | payload, UA, idempotency key, env skip, retry/no-retry matrix, redaction, semaphore, on_sent |
| HTTP — invite hooks | `tests/test_invites_email_http.py` | TestClient + temp FalkorDBLite (test_invites_http.py fixture) + FakeControlPlane | both branches, email_sent_at, failure isolation, link host from env |
| HTTP — recovery trigger | `tests/test_recovery_email.py` | TestClient + FakeControlPlane + `RATE_LIMIT_DISABLED` | identical-200, buckets, known-only mint, hash-at-rest, registry 501 |
| HTTP — mint extension | extend `tests/test_session_key_http.py` + `tests/test_auth_flip.py` | existing session-key fixtures | valid/expired/used/wrong code, backward compat, consume-before-cap |
| Static | extend `tests/test_website_static.py` | stdlib parse (test_website_static pattern) | landing pages: referrer policy, param JS, no external assets |
| Integration (opt-in) | `tests/test_email_integration_resend.py` | `@pytest.mark.integration` + env gate | resend.dev delivered/bounced, terminal-event polling |
| E2E (manual) | — | fork-mode clickthrough (stripe-plan precedent) | dashboard flows, param stripping, resend affordance |

`RATE_LIMIT_DISABLED=1` used in all HTTP fixtures (existing convention). New rate-limit tests opt back in per-case.

---

## 6. Verification Plan

Each acceptance criterion is verified by:

1. **Sender unit tests** — retry matrix, redaction, env gating, idempotency (Task 1 acceptance).
2. **Endpoint HTTP tests** — both invite branches, recovery trigger contract, mint extension (Tasks 3-5 acceptance). Run: `python -m pytest tests/test_email_notify.py tests/test_invites_email_http.py tests/test_recovery_email.py tests/test_session_key_http.py -v`.
3. **Full suite regression** — `python -m pytest tests/ -v` (FalkorDBLite, no Docker); python-ci.yml list extended with the new test modules (the workflow enumerates test files at line 159 — add `test_email_notify test_invites_email_http test_recovery_email`).
4. **Migration** — `supabase db push --linked --include-all` dry-run on staging; verify `email_sent_at` + `recovery_codes` land; RLS/column-protection review.
5. **E2E mapping** (from 669-scope.md + epic E2E docs):
   - **E2E-3 (invitation flow end-to-end) — email leg:** owner mints invite → invite email scheduled with token link (mock-verified) → invitee follows link → accepts (existing endpoint) → real membership with invited role; used/revoked invite cannot be re-accepted. Owned by Tasks 3 + 7.
   - **E2E-2 (session-key round trip) — recovery extension:** recovery mint with code lands in `api_keys` (lookup_hash, created_via) and resolves via `api_keys.lookup_hash`; code single-use. Owned by Task 5.
   - **E2E-6 (welcome key reveal) — recovery entry:** welcome page "Lost your key?" path (welcome.html:487) → dashboard → recovery-link email flow → key regained; original key remains unrecoverable (hash-only + reveal-null unchanged). Owned by Tasks 4/7.
6. **Recovery email verification (real delivery, opt-in):** opt-in integration leg asserts (a) provider-accepted (200 with Resend id) and (b) terminal event `delivered` via `GET /emails/{id}` polling — for `delivered@resend.dev`; `bounced@resend.dev` asserts the bounced event (delivery-failure visibility). **False-green guard:** no verification step ever equates a 200 with delivery — `email_sent_at` is documented and labeled provider-accepted; delivered requires the terminal-event poll or Resend dashboard lookup by logged message id.
7. **Host-header hardening check:** HTTP tests spoof `Host` and assert the link still uses `EMAIL_LINK_BASE_URL` (Tasks 4/5).

---

## 7. Acceptance Criteria

**Overall (#307):**
- [ ] Invite mint on both branches of `POST /v1/invites` schedules exactly one email carrying the accept link `{EMAIL_LINK_BASE_URL}/invite-accept.html?token=…`; send failure never fails the mint; `email_sent_at` reflects provider-acceptance only.
- [ ] `POST /v1/session/recovery-email` returns byte-identical 200 for known/unknown session emails (uniform 503 only when sender config absent); per-email 3/hr + per-IP 5/hr with Retry-After; known-only code mint (CSPRNG ≥256-bit, SHA-256 at rest, 30min TTL, single-use, re-mint invalidates prior code); link host from `EMAIL_LINK_BASE_URL`, never Host header.
- [ ] `purpose=recovery` mint accepts optional `recovery_code` (validated before mint, consumed atomically, single-use); no-code mint unchanged; invalid/expired/used → 400.
- [ ] Landing pages static, HTTPS, noreferrer; dashboard consumes `invite_token`/`recover_code`, strips params, offers unconditional resend affordance; never silent-skip.
- [ ] No new runtime deps (httpx only); no jinja2; no resend SDK; templates in-repo.
- [ ] All CI layers green incl. new tests; opt-in integration passes with real key; `email_sent_at`/`recovery_codes` migration applied.

**Per task:** as listed in each Task's Acceptance block (Tasks 1-9).

---

## 8. Runtime Prerequisites

| Prerequisite | Detail |
|---|---|
| `RESEND_API_KEY` | Already a Fly secret + `.env.example` (billing). Same key serves transactional (10 req/s/team is per-team, all keys). |
| `RESEND_FROM_EMAIL` | **New to the API runtime** (currently only in edge-function context, .env.example:144). Add to Fly secrets + deploy-hosted.yml. Default `noreply@premiselabs.co` (premiselabs.co already DKIM/SPF-verified on Resend for billing). |
| `EMAIL_LINK_BASE_URL` | **New.** Default `https://tortoise.premiselabs.co` (Pages site). Fly secret + deploy-hosted.yml. Never Host header. |
| `TORTOISE_SECRET_PEPPER` | **Existing** (already a Fly secret; invitations/api_keys `lookup_hash` use it). Recovery codes reuse it — confirm present + stable across redeploys (no rotation mid-TTL). |
| Migration | `supabase/migrations/0011_transactional_email.sql` via existing `supabase-deploy.yml` db push (workflow_dispatch). |
| CORS | **No change** — landings are static redirect shells (no API calls from `tortoise.premiselabs.co`); dashboard SPA origin already allowed (hosted_api.py:309). Note: if a future landing calls the API directly, add `https://tortoise.premiselabs.co` to `allow_origins`. |
| Rate-limit opt-outs | `RATE_LIMIT_DISABLED=1` (existing) covers new buckets in tests; prod has no opt-out. |
| Shutdown drain | `_lifespan` awaits `drain_pending_sends(timeout=2.0)` — invite task-loss mitigation. |
| Resend test addresses | `delivered@resend.dev` / `bounced@resend.dev` for opt-in integration only. |

---

## 9. Non-Goals (explicit)

- **Webhooks** (bounce/complaint → suppression): deferred — send failures are logged with message id; Resend dashboard/API is the post-hoc delivery check. Future issue: webhook receiver (svix-id dedup per research) + suppression list.
- **Full accept UI**: landing is a minimal destination; accept stays the existing token-only endpoint. **Boundary (coherence-fix):** the full invite-accept flow (dashboard accept screen, membership management) is owned elsewhere in the epic; #307 fixes only the email link contract + static destination. **#265 dependency severed:** stale — ADR-008 re-scope (customer-held key, wrapped copy in registry, HKDF from API key + team pepper) has zero email references; do NOT build encryption-key delivery here. Recovery codes require session+code; code-alone mint is a non-goal.
- **Durable outbox / worker**: rejected with C (volume doesn't justify; queue loss risk).
- **Resend-hosted templates**: rejected with C (unversioned drift).
- **Plaintext key by email**: never (hash-only + reveal-null contract preserved).
- **Registry-mode recovery email**: hosted-only (501 documented); selfhost keeps in-session recovery.

---

## 10. Extra Issues Filed During Scoping (per skill — no silent absorption)

> To file as separate GitHub issues (not absorbed):
- **Rate-limit the accept surface** (`POST /v1/invites/accept`): OWASP recommends per-token/IP/global caps; none today. Security hardening follow-up.
- **`welcome_url` hardcoded** at hosted_api.py:4801 — adjacent tech debt (EMAIL_LINK_BASE_URL makes a second hardcoded host visible); consolidate.
- **`notify.py` FROM hardcoded** (`billing@premiselabs.co`) — migrate to `RESEND_FROM_EMAIL` so one domain/identity is managed in env (out of scope here).
- **Resend send-budget awareness**: free-tier 100/day, 3000/mo — an admin bulk-invite blast could exhaust the monthly budget; pricing/ops follow-up (tier-gated sends).

---

## 11. ### Integration Docs

> Distinct from the plan's Integration Surface Map (§4). Drafted at solution-converge from Codebase Explorer DEPENDENCIES + Phase 1.5 findings.

**Dependency:** none new at runtime. Reuses `httpx>=0.27` (pyproject.toml:19, pinned `httpx==0.28.1` in requirements.txt) — the **plain-httpx client, not the `resend` Python SDK**, because (a) `notify.py:66-74` already ships the exact pattern in production (billing, #310) and (b) the SDK adds a dep for a one-POST surface with no async/retry value we don't already implement (research: resend SDK v2.35.0, MIT, py>=3.7, async via httpx extra — redundant). No jinja2 (templates are in-repo string templates, html.escape'd).

**Resend API surface (verified 2026-08-13, resend.com/docs):**
- Endpoint: `POST https://api.resend.com/emails` — auth `Authorization: Bearer re_<key>`; **`User-Agent` header documented as REQUIRED (403 without) — verify against live billing sender at implementation (`notify.py` sends httpx's default UA and works; migrate notify.py to the explicit header if confirmed)**; body: `from` (verified-sender), `to[]` (max 50), `subject`, `html`, `text`, `reply_to`, `bcc`, `cc`, `tags`, `template{id,variables}` (hosted — rejected: unversioned); response `200 {"id": "…"}`.
- Rate limits: 10 req/s per TEAM (all keys share) — concurrency cap (Semaphore(4)) with documented headroom; retry-on-429 (transient-only budget).
- Idempotency: `Idempotency-Key` header, 24h expiry, 256 chars — used on both profiles.
- Webhooks: `email.bounced|delivered|delivery_delayed|complained`, at-least-once, `svix-id` dedup, retry 5s/5m/30m/2h/5h/10h, order not guaranteed — deferred (Non-Goals).
- Test addresses: `delivered@resend.dev` / `bounced@resend.dev` / `complained@resend.dev` / `suppressed@resend.dev` simulate events without reputation damage; 422 on `@example.com`/`@test.com` — used in opt-in integration.
- Retrieval: `GET https://api.resend.com/emails/{id}` → `last_event` (queued→delivered/bounced/…) — the delivery-check channel for verification + post-hoc ops lookup.
- Domain: premiselabs.co verified for billing (`billing@premiselabs.co` sends today); transactional FROM `noreply@premiselabs.co` needs no new verification (same domain) — confirm subdomain/DKIM status at deploy.

**Migration surface:** `supabase/migrations/0011_transactional_email.sql` — `invitations.email_sent_at` (provider-accept stamp) + `recovery_codes` (code_hash unique index, RLS service_role-only, column-protected). Applied via existing `supabase-deploy.yml` (`supabase db push --linked --include-all`). No registry-graph migration (email_sent_at as node property; recovery codes hosted-only).

**API-surface findings (this repo):** `POST /v1/invites` hooks at hosted_api.py:3106-3108 (Supabase) / 3159-3171 (registry); `POST /v1/invites/accept` (token-only, session-auth — unchanged); `POST /v1/session/key` (4094+) extended with optional `recovery_code`; new `POST /v1/session/recovery-email`; `_lifespan` (170) gains the drain hook; rate-limit helper genericized from `_check_register_rate_limit` (1217-1247). No GoTrue SMTP usage (30/hr bucket — do not route transactional through GoTrue; #832 boundary respected).
