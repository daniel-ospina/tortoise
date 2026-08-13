# Scoping — #1081 Robust abuse protection for zero-email signup

**Date:** 2026-08-13 · **Process:** issue-scoping v5.1 double diamond + verification gates
**Status:** scoped → planned → ready for implementation (8 tasks)

## Confirmed Problem
`POST /v1/agent/signup` already has a 3/hr shared register-bucket limiter (PR #983) + 100/min middleware. Missing: (a) separate configurable 2/24h per-IP limiter for the agent path only (NOT the shared store), (b) R8 signup_velocity rule → ops notify (anon teams NULL user_id → R3/R4 owner-notify dead → BILLING_NOTIFY_TO), (c) caps-binding assertion (reduced anon ceiling deferred to #1082), (d) rewrite 2 dead-semantics tests, (e) CLI 429 UX, (f) idx_audit_ip_time index now + durable sweeper deferred, (g) Task 0: client-IP resolution behind Fly proxy.

## Diamond Outputs
- **problem-diverge** (2 agents): 5 framings (economic / identity-binding / claim-gated / observability / original). Devil's advocate: stale baseline (limiter already wired 2026-08-12 by #983), test suite red on main, ceiling = one-way lockout without #1082.
- **problem-converge** (2 agents): verified all claims against code; confirmed problem above; confidence 78-82; split ceiling→#1082.
- **problem-verify** (2 verifiers × 2 cycles): P1s — shared-bucket blast radius, self-contradictory indicator 4 (tests assert dead semantics), stale baseline. All fixed.
- **solution-diverge** (2 agents): twin-limiter / DB-count / middleware / parametrize / in-memory tracker / notify-on-block.
- **solution-converge** (2 agents + controller merge): parametrized `_check_ip_bucket_rate_limit` + in-memory SignupVelocityTracker (threshold=allowance, bare-ip dedup) + index-now/sweeper-deferred + Task 0 Fly-Client-IP.
- **solution-verify** (2 verifiers × 5 cycles): P1s — sensitive-op composite keying lost in parametrization, success feed dead at `>`, create_task race, IPv6 normalization dead for tuples, plan-text lag. All fixed in plan blocks.
- **Qwen coherence** (fresh reviewer): P1 — #1082 body contradicted ceiling division; resolved by pinning contract in #1082 body. P2s — IPv6 collision, forged Fly-Client-IP probe, instance-count verification — incorporated.
- **Phase 7 parallel review** (2 agents): P2s — task retention, block-path email latency, CLI humanize + _cmd_fail + error_code tiering, quickstart doc — incorporated.

## Converged Architecture (plan §Architecture + Decision 1)
Two independent, precedented mechanisms, deliberately NOT coupled:

1. **Per-IP signup limiter** — a parametrized in-memory IP-bucket primitive (`_check_ip_bucket_rate_limit`) replacing the two existing duplicated limiter bodies (register + sensitive-op) and adding a THIRD call site for agent_signup with its OWN bucket store + env knobs (`TORTOISE_SIGNUP_IP_LIMIT`/`TORTOISE_SIGNUP_IP_WINDOW_S`, defaults 2/86400). The shared `_register_buckets` (3/hr) that /v1/register + /v1/signup/email depend on is untouched (locked by `test_shared_ip_bucket_3_per_hour`). 3rd signup in a rolling 24h → 429 + computed Retry-After + `over_signup_ip_rate_limit` + support pointer.
2. **R8 signup_velocity** — an in-memory `SignupVelocityTracker` in abuse.py mirroring the `ReadVelocityTracker` (R3) precedent: fed on the SUCCESSFUL mint path per IP (threshold = allowance, breach on `>=` — fires exactly when an IP consumes its entire anon allowance), plus a `record_block` feed on the limiter 429 (same bare-ip dedup key — one ops email per (ip, window)). Notify-only via `notify_abuse` (BILLING_NOTIFY_TO ops fallback — anon teams have no email), never suspends. `abuse_signup_velocity` added to KINDS + ALERT_TYPES; IP rendered in both email + Telegram channels.

**Client-IP resolution (Task 0, P1-FIX-11):** `ClientIPMiddleware` reads the non-spoofable `Fly-Client-IP` header (set/overwritten by Fly Proxy; XFF documented "treat with caution") into `request.state.client_ip`; all limiters key on `request.state.client_ip` (fallback `request.client.host`). Behind Fly without this, every per-IP limiter collapses to a GLOBAL cap (uvicorn trusts XFF only from 127.0.0.1). `--forwarded-allow-ips="*"` REJECTED (uvicorn parses the FIRST XFF entry — client-controlled → cap bypassable).

**Decision 1 — index ships NOW, sweeper deferred:** `idx_audit_ip_time` (20260813000003, `(ip_address, created_at DESC)`) is the cheap, idempotent half that satisfies indicator 2 verbatim; the durable read API + sweeper over audit_events needs an ops consumer that doesn't exist yet (multi-instance hosting or ops dashboard). Data already accruing (every signup's `_async_audit`).

**Dead per-identity count removed:** the membership_count_since block cost a DB round-trip + fail-closed 500 per signup for a count ≡ 0 by construction (#741 — server-side identity fresh per request).

## Rejected Alternatives
1. R8 on audit_events DB count — better when multi-instance/ops-dashboard live
2. abuse_events piggyback — team_id NOT NULL structurally wrong for IP rules
3. Per-device/device-ID fallback — #741 dead (client IDs ignored)
4. Global register-bucket tightening — breaks register/email contracts
5. Reduced anon ceiling in #1081 — one-way lockout without #1082 claim path
6. Rate-limit-only (no R8) — farms invisible (NULL user_id)
7. Durable sweeper NOW — no consumer, no scheduler host
8. Keep dead per-identity count — DB round-trip + fail-closed 500 for count ≡ 0
9. Notify-on-block only — weaker signal (no allowance-boundary review)

## Wiring Check
| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| agent_signup limiter | API | Task 1 | ✅ |
| shared register bucket | API contract | locked test | ✅ |
| sensitive-op limiters | API | regression test | ✅ |
| R8 tracker + feeds | abuse | Task 3 | ✅ |
| notify KINDS + BILLING_NOTIFY_TO | notify | Task 3 | ✅ |
| idx_audit_ip_time | migration | Task 5 | ✅ |
| CLI 429 UX | CLI | Task 6 | ✅ |
| caps binding | pricing/quota | Task 4 | ✅ |
| client-IP resolution | middleware | Task 0 | ✅ |
| #1082 claim path | dependency | contract pinned in #1082 | ✅ |
| #1086 dashboard deploy | deploy | separate issue | ⚠️ related |

## Deferred Contracts
- **Durable sweeper:** query audit_events (operation=agent_signup, ip, window) via service-role (RLS bypass); idx_audit_ip_time ships in #1081 Task 5. Durable threshold SHOULD default above allowance (allowance-boundary signal is in-memory).
- **#1082 (claim path):** ships the reduced anon ceiling + claim (same key, memories intact). Atomicity: ceiling must NEVER deploy without the claim path. Contract pinned in #1082 body 2026-08-13.
- **ASN awareness:** no ASN data source (R4 documents IPINFO_TOKEN follow-on); IP-only posture shipped.

## Complexity
Architecture: complex (security surface, cross-system). Config: standard (new env knobs). UX: low.
