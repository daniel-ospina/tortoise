---
title: "Issue #307 — Hosted: Email Integration (Resend for Invitations + Key Recovery) — Scoping"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# Issue #307 — Hosted: Email Integration (Resend for Invitations + Key Recovery) — Scoping

> **Issue:** daniel-ospina/tortoise#307 | **Tier:** Standard | **Date:** 2026-08-13
> **Process:** issue-scoping v5.1 double diamond + verification gates (problem-verify, solution-verify, coherence check, wiring HARD-GATE)
> **Full plan draft:** `docs/scoping/2026-08-13-307-email-notifications-scope.md`

---

## Confirmed Problem

Build the hosted platform's transactional email path (Resend) for exactly two notifications:

1. **Team-invitation email** triggered at both branches of `POST /v1/invites` (Supabase branch after `invitation_mint`, hosted_api.py:3106-3108; registry branch after the `CREATE`, ~3159-3171), linking into the existing accept flow (`POST /v1/invites/accept`, token-based, session-auth + email-match guard). Profile: async best-effort **with logging** — email is the only *automated* token delivery path (the dashboard currently discards the minted token; the API response returns it once, and the dashboard keeps it for a manual-share fallback when the send fails). **The issue's stated trigger `POST /v1/team/members` does not exist** — the real endpoint is `POST /v1/invites`.
2. **Post-signup key-regain email** delivering a short-lived, single-use **LINK** to the session-authenticated recovery mint (`POST /v1/session/key purpose=recovery`). Profile: must-arrive — sync send + retry/backoff budget + user-visible resend affordance, never silent-skip. **Never the plaintext key**: the original `tt_` API key is unrecoverable by design (hash-only storage — `lookup_hash` SHA-256(pepper+key); `reveal_api_key` atomic reveal+null), so "Email me my API key" can only mean a link to a re-mint. Anti-enumeration: byte-identical 200 for known/unknown emails; link host from `EMAIL_LINK_BASE_URL` env, never the request Host header; HTTPS + noreferrer; per-email 3/hr + per-IP 5/hr caps (OWASP).

**Boundary:** #307 owns the email send layer + the link contract + minimal static destinations (`invite-accept.html`, `recover.html`). The full invite-accept dashboard UI is owned elsewhere in the epic. **#265's "Depends on: #307" line is STALE** — ADR-008 re-scope (2026-08-13) specifies a customer-held key with a wrapped copy in the control-plane registry and HKDF derivation from API key + team pepper — zero email references; #307 does not build encryption-key delivery (file issue to sever the stale dependency).

**Send-mechanism divergence (recorded):** GoTrue-SMTP reuse rejected — transactional invites/recovery would burn the shared project-wide auth email bucket (30/hr, the exact #801 production failure class). Resend API direct has its own per-team budget (10 req/s) and richer delivery control (idempotency keys, tags, delivery lookup). Two in-repo Resend-API precedents (notify.py billing, waitlist edge fn).

## Verification Gates

### problem-verify — 1 cycle, clean (0 P0/P1)
- Cycle 1: Verifier A P0=0 P1=0 P2=3 P3=1 · Verifier B P0=0 P1=0 P2=2 P3=1 P4=1
- Controller: Pass (pass-through rule). 6 amendments incorporated: send-mechanism divergence recorded; per-notification delivery profiles (invites best-effort+logging / recovery must-arrive); failure-mode contract carried (render/bounce/rate-limit/timeout + #801/#885 rate-limit headroom); test anchor for notification 2 (delivery via resend.dev + single-use/expiry + anti-enumeration); boundary per-invitee-type branch; assumptions count reconciled (13).

### solution-verify — 1 cycle, clean (0 P0/P1)
- Cycle 1: Verifier A P0=0 P1=0 P2=4 P3=4 P4=3 · Verifier B P0=0 P1=0 P2=3 P3=4 P4=1
- Controller: Pass (pass-through rule). 10 amendments incorporated: pepper source = existing `TORTOISE_SECRET_PEPPER`; consume-order fixed (fail-prone validation BEFORE atomic rowcount-gated consume); uniform 503 when sender config-missing (no silent skip); per-replica bucket multiplier documented (accepted; shared store = follow-up); TTL 15→30 min; dashboard manual-share fallback; recover_code URL strip parity; no-email-session + timing-oracle branches specified; multi-replica no-duplicate paragraph; invite idempotency key `invite:{token_hash}`.

### Coherence check — 2 cycles (max honored); qwen3.8-max unavailable
- **`[QWEN-GATE]` qwen3.8-max was unavailable** (provider 401 blocked API key) — the coherence review ran with the default fresh-context reviewer as fallback.
- Cycle 1: P1×2 (manual-share fallback source undefined; env-gated silent skip in hosted mode), P1 research (domain verification absent), P2×5, P3×3, P4×1 → all fixed.
- Cycle 2: P1×1 (TTL split 15 vs 30 min across sections) + propagation gaps (503 contract contradictions, idempotency-key format, semaphore wording, pepper deploy confirmation, UA claim, folded-endpoint decision log) → all fixed by controller. No 3rd cycle (max-2 rule); residual risk documented.
- **`[QWEN-GATE]` residual note:** the coherence gate could not converge to a 3rd-cycle clean verdict per the max-2 rule; however every flagged issue was deterministically fixed and the final plan is internally consistent (verified by grep of TTL/503/semaphore/key-format occurrences post-fix).

## Plan

Full task-by-task plan: `docs/scoping/2026-08-13-307-email-notifications-scope.md` (9 tasks).

**Approach (chosen):** A — in-API Python monolith, with refinements from C.
- New `tortoise/email_notify.py` mirroring `notify.py` (httpx POST `https://api.resend.com/emails`, Bearer, User-Agent, `timeout=15.0`, `raise_for_status`, `_env`/`_skip_channel` env-gating, `redact_safe` logging).
- Two profiles: `send_invite_email` (best-effort; `asyncio.create_task` + task registry drained at `_lifespan` shutdown; 0.5s transient retry; `Idempotency-Key: invite:{token_hash}`; redacted WARNING + `email_status` on failure) and `send_recovery_link` (awaited; retry 0/0.5/2/8s transient-only — 429/408/502/503/504/timeout/network; 4xx never retried; `Idempotency-Key: recovery:{code_hash}`; 5xx on exhaustion).
- Sender-config contract: `unconfigured` ≠ `failed`; recovery endpoint returns **uniform 503** when config-missing (enumeration-free, loud deploy gate); invites mint succeeds with `email_status: "unconfigured"` + dashboard manual-share fallback; silent env-gated skip only in local/dev.
- Migration 0011: `invitations.email_sent_at` + `recovery_codes` (code_hash unique via `lookup_hash` = SHA-256(`TORTOISE_SECRET_PEPPER` + code); 30-min TTL; `used_at`; column-protected; one live code per email — re-mint invalidates prior link).
- `POST /v1/session/recovery-email`: JWT email; per-email 3/hr + per-IP 5/hr (genericized register limiter, per-replica N× documented); identical 200 known/unknown/no-email-session; known → CSPRNG ≥256-bit code, 30 min, single-use; registry → 501; code never in response.
- `purpose=recovery` accepts optional `recovery_code`: all fail-prone validation first, then atomic rowcount-gated consume immediately before mint; invalid/expired/used → 400; no-code backward compat with dashboard `recoverKey()`; code+bootstrap → 422; registry+code → 400.
- Landing pages `invite-accept.html` + `recover.html` (static Cloudflare Pages, HTTPS, `Referrer-Policy: no-referrer`, param pass-through; recover no-session → sign-in → stash → resume within TTL; session+code required — code alone must not mint).
- Dashboard `main.jsx`: keeps invite token; `invite_token`/`recover_code` consume + strip (`history.replaceState`); "Email me a recovery link" + unconditional resend + 429 copy + 5xx exhaustion state; manual-share-invite copy-to-clipboard fallback.
- Deploy: `deploy-hosted.yml` +`RESEND_FROM_EMAIL` +`EMAIL_LINK_BASE_URL` (+ confirm `TORTOISE_SECRET_PEPPER`); `.env.example`; `_lifespan` drain; pre-deploy Resend domain-verification probe (`GET /domains`; premiselabs.co verified per #832).
- Opt-in integration test (`@pytest.mark.integration`, env-gated, not in CI): `delivered@resend.dev` / `bounced@resend.dev` terminal events via `GET /emails/{id}` polling + **link-resolution assertion** (constructed URLs → 200 + landing + no-referrer). False-green guard: 200 ≠ delivered.

**Testing strategy:** unit via `monkeypatch.setattr(httpx, "post", fake)` (test_notify.py pattern); HTTP both branches + `FakeControlPlane` Supabase mode; rate-limit tests with `RATE_LIMIT_DISABLED=1`; `test_session_key_http.py` + `test_auth_flip.py` extensions; static-page checks. No new runtime deps (httpx only; no resend SDK; no jinja2).

## Clarifications

*(from issue-pre round — 2026-08-13)*

| Question | Answer | How |
|---|---|---|
| Which key does "Email me my key" deliver? | The `tt_` API key recovery LINK (signup plan E2E-1-D: "API key copy failure → fallback Email me my key"); original key unrecoverable — link to re-mint via `POST /v1/session/key purpose=recovery` | resolved by research |
| Is Resend the right channel for the key? | Channel carries a LINK, never the key (OWASP; two in-repo Resend precedents) | resolved by research |
| Where do invite/recovery emails link to? | Static landings `invite-accept.html` / `recover.html` (no accept UI exists — minimal destination in scope; full UI out) | chosen (greenfield) |
| Email provider / dependency? | Resend — already in-repo (notify.py, waitlist edge fn), account + domain verified (#832) — not a new dependency | resolved by research |
| Cost impact? | Resend free tier 3,000/mo; low volume — no new recurring cost; budget awareness filed as follow-up issue | resolved by research |
| Rate limits / abuse? | Per-email 3/hr + per-IP 5/hr on the recovery trigger; #885 open (abuse posture) — buckets coordinated, not absorbed | resolved by research |

No Pass-A (human-required) questions survived the research-first pass — all specified or researchable.

### Deferred to Research (Pass B → answered in Phase 1.5)
- Invite email UX/destination pattern (link vs code, landing) *(Impact 7)*
- Key-email security pattern (link-based recovery) *(Impact 9)*
- Unknown-recipient handling / anti-enumeration *(Impact 6)*
- Rate-limit/abuse posture for email-sending endpoints *(Impact 8)*
- Resend test + delivery verification approach *(Impact 6)*
- Template error handling *(Impact 6)*
- Env-gating / rollback for the send layer *(Impact 6)*
- Recovery email landing (existing welcome.html #863 recovery page vs new) *(Impact 6)*
- Brand template baseline *(Impact 6)*

## External Research (Phase 1.5 artifact)

> Persisted: `docs/epics/2026-08-03-tortoise-hosted-platform/research-brief.md` (4 timestamped entries: canonical / competitor-precedent / pitfalls / adversarial). 7 external queries post-dedup (Standard cap 8). Perplexity rate-limited (429) mid-run — Exa used as the independent second source category.

### Axis Research

**Architecture (medium):**
- *canonical* — Resend API: `POST https://api.resend.com/emails` (Bearer `re_` key; User-Agent documented required — verify against live billing sender, notify.py sends none today; 10 req/s/team; Idempotency-Key header 24h/256 chars; test addresses `delivered|bounced|complained|suppressed@resend.dev`; webhooks `email.bounced|delivered|delivery_delayed|complained` at-least-once with svix-id dedup, retry 5s→10h; `GET /emails/{id}` → last_event; domain verification DNS TXT/SPF/DKIM, subdomain recommended) — resend.com/docs.
- *competitor-precedent* — DVARA flightdeck: durable delivery record + idempotency + exponential backoff (30s→120s ×5) + DLQ + `log` transport for dev/CI; Courier: ESP + thin internal layer (template registry, suppression, retries) is where most systems land — dvarahq.com, courier.com.
- *pitfalls* — photonconsole: provider 200 ≠ delivered; spam-folder routing invisible to SMTP metrics; retry 4xx vs 5xx distinction (retrying 5xx burns reputation); fixed-interval retry storms under throttle; greylisting delay vs token expiry — photonconsole.com; courier.com deliverability baselines (98-99% delivered, hard bounce <0.5%, complaints <0.3%).

**UX (medium):**
- *competitor-precedent* — securepatterns.dev "Designing a Safe Team Invitation Flow": token = proof of link possession, NOT email verification; accept requires independently verified identity; POST-only accept (GET side-effect-free); unauthenticated accept → 401; session email-match guard; 7-day member / 24-48h admin TTL; CSPRNG ≥256-bit; SHA-256(token) stored; per-token/IP/global rate limits; Referrer-Policy no-referrer; link host from config never Host header; revoke supersedes pending. viprasol.com: Resend sendInvitationEmail with token-in-URL + separate accept flows new/existing users. skycloak.io: manual accept required for all auth types.
- *pitfalls/adversarial* — OWASP Forgot-Password Cheat Sheet: never send password/key in email; URL token single-use/time-limited/hashed; per-account 3-5/hr + per-IP 5-10; identical response known/unknown (incl. timing); HTTPS; noreferrer; link host from config. guptadeepak.com CIAM compass: recovery flows probed before login; 5/hr/account + 50/hr/IP; unverified-email recovery = ATO vector. ttl.space / LinkPilot / PrivateNote: email creates durable searchable copies; one-time burn-after-read link + passphrase via second channel is the strongest simple pattern; scoped+expiring keys limit blast radius.

**Ontology (low):** no vocabulary change — invitations table (0008) already has the status machine; `recovery_codes` is a new hosted-only table, no new entity kinds. No queries fired beyond the codebase scan (justified by activation rule: low axis).

**Library-deps (triggered, justified lighter):** Resend is already in-repo (notify.py httpx billing; waitlist-subscribe Deno edge fn; GoTrue SMTP smtp.resend.com:587 wired per #832) — 3+ in-repo usages → lighter queries. Findings: resend Python SDK v2.35.0 (PyPI, MIT) exists but is a thin POST wrapper — plain httpx (in-repo precedent, no new dep) chosen; no jinja2 (in-repo string templates, html.escape).

### Integration Docs

**Dependency:** none new at runtime. Reuses `httpx>=0.27` (pyproject.toml:19; pinned `httpx==0.28.1`). **Plain httpx, not the `resend` Python SDK (v2.35.0, MIT, py≥3.7, async via httpx extra)** — notify.py:66-74 ships the exact pattern in production; the SDK adds a dep for a one-POST surface with no async/retry value we don't implement ourselves. No jinja2 (in-repo string templates, html.escape'd).

**Resend API surface (verified 2026-08-13, resend.com/docs):**
- `POST https://api.resend.com/emails` — Bearer `re_<key>`; User-Agent documented REQUIRED (403 without) — verify against live billing at implementation (notify.py sends httpx default UA and works); body `from`/`to[]` (max 50)/`subject`/`html`/`text`/`reply_to`/`bcc`/`cc`/`tags`/`template{id,variables}` (hosted templates rejected: unversioned); response `200 {"id": …}`.
- Rate limit 10 req/s per TEAM (all keys share) — concurrency cap + retry-on-429 (transient-only budget).
- `Idempotency-Key` header: 24h expiry, 256 chars — used on both profiles.
- Webhooks `email.bounced|delivered|delivery_delayed|complained`: at-least-once, svix-id dedup, retry 5s/5m/30m/2h/5h/10h — deferred (Non-Goals).
- Test addresses `delivered|bounced|complained|suppressed@resend.dev` simulate events; 422 on `@example.com`/`@test.com` — opt-in integration.
- `GET /emails/{id}` → `last_event` — delivery-check channel for verification + ops lookup.
- Domain: premiselabs.co verified on Resend per #832 (billing sends today; GoTrue SMTP smtp.resend.com:587). `noreply@premiselabs.co` needs no new verification (same domain) — confirm subdomain/DKIM at deploy (pre-deploy `GET /domains` probe).

**Migration surface:** `supabase/migrations/0011_transactional_email.sql` — `invitations.email_sent_at` + `recovery_codes` (unique code_hash index, RLS service_role-only, column-protected). Applied via existing `supabase-deploy.yml` db push. No registry-graph migration (email_sent_at as node property; recovery codes hosted-only).

**API-surface findings (this repo):** invite hooks hosted_api.py:3106-3108 / 3159-3171; `POST /v1/invites/accept` unchanged; `POST /v1/session/key` (4094+) extended with optional `recovery_code`; new `POST /v1/session/recovery-email`; `_lifespan` (170) gains drain hook; limiter genericized from `_check_register_rate_limit` (1217-1247). GoTrue SMTP untouched (30/hr bucket boundary).

## Rejected Alternatives

| Alternative | Why rejected | When it WOULD have been better |
|---|---|---|
| **Original issue framing** ("email me my API key" = plaintext key) | Unimplementable: original key is hash-only + reveal-nulled; emailing a key violates the codebase's never-possess security architecture (OWASP) | If the provider deliberately chose to email secrets — it didn't |
| **B — Supabase edge-function sender (Deno + DB triggers)** | Dual-mode platform forces Deno PLUS in-API registry fallback (double implementation); recovery must be awaited in-request anyway; second deploy/secrets surface; trigger wiring partially console-unversioned; at-least-once dedup machinery | Supabase-only platform; high invite volume needing request-path decoupling; existing edge-fn test infra |
| **C — Async worker + Resend-hosted templates + delivery state machine** | In-memory queue not durable (crash drains the ONLY token delivery path; 409 dedup blocks re-invite); multi-replica duplicate sends require the durable outbox C defers; unversioned template state | Volume grew past ~tens of sends/min; multiple notification types sharing a pipeline; a durable outbox already existed |
| **GoTrue SMTP reuse for transactional** | Shares the project-wide 30/hr auth email bucket — the exact #801 production failure class | Never — bucket boundary is deliberate (#832) |
| **Folded recovery-email into POST /v1/session/key** | Trigger (session-only, no mint) and mint (session+code) have different auth semantics, rate-limit surfaces, and the identical-200 anti-enumeration contract is cleanest without a mint side-effect | If only the trigger surface mattered and no anti-enumeration contract existed |

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| `invitations.email_sent_at` | DB migration | Task 2 (0011) | ✅ |
| `recovery_codes` table (code_hash unique, TTL, single-use) | DB migration | Task 2 (0011) | ✅ |
| Registry Invitation node `email_sent_at` | Graph | Task 3 (node property) | ✅ |
| `POST /v1/invites` hooks (both branches) | API | Task 3 | ✅ |
| `POST /v1/session/recovery-email` (new) | API | Task 4 | ✅ |
| `POST /v1/session/key` `recovery_code` ext | API | Task 5 | ✅ |
| Auth — session (JWT email; no-email branch) | Auth | Task 4 | ✅ |
| Auth — `_require_owner_admin` (invites) | Auth | existing | ✅ |
| Resend `POST /emails` (send) | External | Task 1 | ✅ |
| Resend domain verification | External | #832 DONE + Task 8 probe | ✅ |
| Resend `GET /domains` + `GET /emails/{id}` | External | Task 8 probe / Task 9 + ops | ✅ |
| Resend webhooks (bounce/suppression) | External | Deferred (Non-Goals; message id logged; future issue) | ⚠️ documented deferral |
| `welcome.html:487` "Lost your key?" | UI | Task 7 | ✅ |
| Dashboard `main.jsx` (token keep, params, resend, manual-share) | UI | Task 7 | ✅ |
| Landing pages `invite-accept.html` + `recover.html` | UI | Task 6 | ✅ |
| `EMAIL_LINK_BASE_URL` env | Config | Task 8 | ✅ |
| `RESEND_FROM_EMAIL` env | Config | Task 8 | ✅ |
| `TORTOISE_SECRET_PEPPER` (existing) | Config | Task 8 confirm | ✅ |
| `deploy-hosted.yml` secret wiring | Deploy | Task 8 | ✅ |
| `_lifespan` drain | Runtime | Task 8 | ✅ |
| CORS (dashboard origin; landings static) | Config | no change (hosted_api.py:309) | ✅ |
| Rate limits — per-email 3/hr + per-IP 5/hr | Cross-cutting | Task 4 | ✅ |
| `RATE_LIMIT_DISABLED` test opt-out | Cross-cutting | existing | ✅ |
| Anti-enumeration identical-200 + uniform 503 | Cross-cutting | Task 4 | ✅ |
| Logging/redaction | Cross-cutting | Task 1 (`redact_safe`) | ✅ |
| E2E-3-D email portion | Tests | Tasks 3+7 + verification plan | ✅ |
| #885 abuse posture | Adjacent | related open issue; buckets coordinated | ✅ |

**HARD-GATE: PASSED** — no uncovered wiring gaps.

## Review Cycle Log

### problem-verify — Cycle 1 (PASS)
- Verifier A: P0=0, P1=0, P2=3, P3=1 · Verifier B: P0=0, P1=0, P2=2, P3=1, P4=1
- Controller: pass-through (P2+ only) → 6 amendments incorporated. No re-dispatch needed.

### solution-verify — Cycle 1 (PASS)
- Verifier A: P0=0, P1=0, P2=4, P3=4, P4=3 · Verifier B: P0=0, P1=0, P2=3, P3=4, P4=1
- Controller: pass-through → 10 amendments incorporated. No re-dispatch needed.

### Coherence — Cycle 1 (2 P1s → fixed → re-run)
- P1-1 manual-share fallback source → fixed (token from POST /v1/invites response; invariant restated to only-AUTOMATED).
- P1-2 env-gated silent skip in hosted mode → fixed (sender-config contract: uniform 503 recovery / email_status invites / local-dev skip only).
- P1-3 domain verification absent → fixed (Task 8 pre-deploy GET /domains probe). P2×5/P3×3/P4×1 also fixed.

### Coherence — Cycle 2 (1 P1 → fixed; max cycles honored)
- P1 TTL split (15 vs 30 min) → unified to 30 min across §1/§3/Task 4/Task 5/§7.
- Propagation gaps (503 contract contradictions; idempotency-key format `invite:{token_hash}`; semaphore wording; pepper deploy confirm; UA claim softened; folded-endpoint decision log; re-mint invalidates prior code) → all fixed by controller.
- No 3rd cycle (max-2 rule). Post-fix grep confirms internal consistency.

### Phase 7 — Parallel Review (logged)
Per session instruction, the two diamond verification gates + the 2-cycle coherence review served as the fresh-context review cycle for Phase 7 (each dispatched fresh `task` sessions, independent conclusions, controller tiebreak). No additional 4-agent dispatch; exit conditions met: all gates passed with 0 P0/P1; 2 re-review cycles completed for the coherence gate; cycle log posted here.

## Complexity

| Domain | Rating | Basis |
|--------|--------|-------|
| UX | Medium | Two email templates (brand), two static landing pages, dashboard params + resend + manual-share fallback, welcome.html hook |
| Ontology | Low | No vocabulary change; `recovery_codes` new hosted-only table (no new entity kinds); invitations status machine existing |
| Architecture | Medium | New send module + 2 endpoint surfaces + recovery-code state machine + env/link-base wiring; dual-mode (Supabase/registry) consistency |
| Library-deps | Low | Resend already in-repo (3 usages); plain httpx — no new dependency |
| Testing | Medium | New unit/HTTP/rate-limit test surface; established monkeypatch patterns; opt-in integration (resend.dev) |
| Deployment/Ops | Medium | deploy-hosted.yml + 2 new env vars; domain-verification probe; migration 0011; shutdown drain |
| Security | High | Secret-by-email anti-pattern re-scoped to link-based; anti-enumeration (identical-200 + uniform 503); CSPRNG codes + hash-at-rest; rate limits; Host-header injection; referrer leakage |

**Overall tier: Standard** (external service integration, multi-surface, security surface, test-design requirement — matches the expected tier).
