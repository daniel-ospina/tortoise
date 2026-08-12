---
title: "Scoping #308 — Hosted Abuse Prevention (double diamond)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted
aboutObjects: abuse-prevention, turnstile
created: 2026-08-11
---

# Scoping 308 — Hosted: Abuse Prevention — Alternative Approaches

> Divergence-phase output for issue #308 (branch `feat/308-abuse-prevention`). Three genuinely distinct architectures, evaluated against the confirmed problem. **No winner is selected here** — this document feeds the convergence decision.
>
> All line numbers verified against the working tree (2026-02) at time of writing.

## Confirmed problem

The hosted platform lacks durable, surface-complete abuse telemetry and enforcement. Requirements (from issue #308):

| # | Rule | Response |
|---|---|---|
| R1 | >500 Points created in <1h (team) | flag → **AUTO-SUSPEND** |
| R2 | >10 API key creations in 24h (team) | flag → **AUTO-SUSPEND** |
| R3 | >100 reads in <5min from a single API key | notify Owner |
| R4 | New-IP/geolocation access | notify Owner |
| R5 | Auto-suspend → API returns `403 SUSPENDED` + appeal link (REST **and** MCP) | — |
| R6 | CAPTCHA (Turnstile) on signup + server-side verification on mint endpoints | fail-open only when secret unset |
| R7 | Dashboard: suspicious-activity alert + one-click key revocation | — |

Constraints that shape every approach:

- **Deploy:** single Fly worker, auto-deploy on merge → any in-memory state resets on every deploy.
- **Control plane:** Supabase (seam: `tortoise/supabase_control.py`, `get_control_plane` monkeypatchable) + registry fallback (selfhost). Tests use `TestClient` + `app.dependency_overrides` + `tests/fake_control_plane.py` `FakeControlPlane` (query/seed/rpc) + `monkeypatch(tortoise.supabase_control.get_control_plane)`; `RATE_LIMIT_DISABLED=1` disables rate limiters in tests.
- **Write seams (verified):** REST writes call `record_write_ops` (hosted_api.py L1035–1036, inside `_check_team_limit`); MCP writes are wrapped by `_quota_gated` (mcp_server.py L400–426, metering at L420–421). Read tools have **no** seam on either transport.
- **Auth seams (verified):** REST `get_current_team` (hosted_api.py L820) → `_get_current_team_supabase` (L946) → `resolve_api_key` (supabase_control.py L303, shared with MCP). MCP `TeamResolutionMiddleware` (mcp_auth.py) resolves the same way, with a 60s per-token LRU cache.
- **Signup key mint bypasses the app:** `api_keys` INSERT happens in two places — `insert_api_key` (supabase_control.py L480, dashboard-created keys) and the `provision_team` RPC (L1018, called by the tenant-provision Edge Function on user signup). **Only a DB trigger sees both.**
- **Rate-limit template:** `_register_buckets` (hosted_api.py L1215–1245) and `MCPRateLimitMiddleware` (mcp_auth.py): `defaultdict(list)` timestamps + prune + cap + `asyncio.Lock` + `RATE_LIMIT_DISABLED`.
- **Notify:** `notify.py` `KINDS` is billing-only (L30); `_send_resend` (L76) private; `teams.email` nullable (NULL for anon agent-signup teams). `alert_store.py` = GH issue + Telegram incident alerts with dedup.
- **Audit:** `audit_events` table (migration 0002: `operation` column, index `(team_id, created_at DESC)`); Postgres-backed only when `TORTOISE_AUDIT_DSN` set, JSONL fallback otherwise; **no DSN in fly.toml** (must be confirmed via `fly secrets list`).
- **CLI:** `__main__.py` branches on HTTP error codes with hardcoded messages; 403 → `"key_rejected"`; response body `detail` not parsed → appeal link requires a detail parse.

Common to all approaches (not differentiated):

- **CAPTCHA (R6):** Turnstile widget already exists on `website/index.html` (L510–596, env-driven `TURNSTILE_SITE_KEY`, empty → widget hidden). Add the same widget to `website/signup.html`; server-side siteverify in `/v1/signup/email` (hosted_api L1150) and `/v1/register` (L1577). Fail-open **only** when `TURNSTILE_SECRET_KEY` is unset; fail-closed (400) when set and siteverify fails.
- **Appeal flow (R5):** appeal link + email-to-ops; operator clears `suspended_at` (SQL / RPC / internal endpoint); next auth succeeds.
- **Revocation (R7):** revoke button exists (dashboard `revokeKey` L814–870). Suspension gates the re-mint in `/v1/session/key` (hosted_api L4045) so a revoked key cannot be re-minted while suspended.

---

## Approach A — Supabase-centric durable substrate

### Description

The abuse control plane lives in Supabase: a new `abuse_events` table (migration 0015) is the single durable event log, populated by a **DB trigger on `api_keys` INSERT** (surface-complete for R2 — sees both `insert_api_key` and the `provision_team` RPC) plus best-effort app-seam writes for point-create and auth events. A rule engine (`tortoise/abuse.py`) evaluates rules **in the request path** on every recorded event, with a two-consecutive-window staging for auto-suspend. Suspension state is durable (`teams.suspended_at` column + registry `Team.suspended_at` prop) and enforced at the auth layer on both transports. Read velocity (R3) is in-memory per-key (window is 5 min; durability loss bounded). Geo (R4) is a fail-open resolver over the `CF-IPCountry` header, optional `IPINFO_TOKEN` resolver.

### Files touched

| File | Change |
|---|---|
| `supabase/migrations/0015_abuse_events.sql` | `abuse_events` table + RLS (service_role_all, mirrors 0014) + index `(team_id, event_type, created_at)` + trigger `trg_api_keys_abuse` (AFTER INSERT on api_keys → `key_create` event) + `teams.suspended_at` column + `abuse_suspend`/`abuse_unsuspend` SECURITY DEFINER RPCs |
| `tortoise/abuse.py` (new) | `AbuseEventStore` protocol + `SupabaseAbuseStore` / `FakeAbuseStore` (tests) / `MemoryAbuseStore` (registry mode); rule engine (`evaluate(window_count) → None | flag | suspend`); `ReadVelocityTracker` (shared REST+MCP in-memory window); `GeoResolver` interface + `HeaderGeoResolver` + `IpInfoGeoResolver` |
| `tortoise/supabase_control.py` | `get_abuse_store()` (monkeypatchable, same pattern as `get_control_plane`); `resolve_api_key` returns `suspended_at`; `record_abuse_event()` best-effort helper |
| `tortoise/hosted_api.py` | `get_current_team` + `_get_current_team_supabase`: post-auth geo check (R4) + 403 `SUSPENDED` when `suspended_at` set (R5); `record_write_ops` hook site (L1035): best-effort `point_create` event (R1); `GET /v1/team/alerts` (R7); `TeamInfoResponse.status` additive field (L1079); siteverify on signup/register (R6); `session_key` mint gated on suspension |
| `tortoise/mcp_auth.py` | `ERR_SUSPENDED = -32006`; `TeamResolutionMiddleware`: check `suspended_at` from resolution → JSON-RPC error with `data.appeal_url`, **bust the 60s cache entry** for that token; post-auth read-velocity increment for non-write `tools/call` methods (R3) |
| `tortoise/mcp_server.py` | `_quota_gated` (L400): best-effort `point_create` event after successful write (R1) |
| `tortoise/notify.py` | `KINDS` += `abuse_flag`, `abuse_suspended`, `abuse_new_ip`, `abuse_read_velocity`; `notify_abuse(kind, team, details)` → Resend to `teams.email` (fallback `BILLING_NOTIFY_TO` ops inbox when NULL) + Telegram + `alert_store` for auto-suspend |
| `tortoise/__main__.py` | 403 branch: parse `detail.code == "SUSPENDED"` → print appeal link (today hardcoded `"key_rejected"`) |
| `website/index.html`, `website/signup.html` | Turnstile widget (R6) |
| `website/functions/` (existing siteverify handler) | nothing new if siteverify already proxied; otherwise add secret check |
| `website/apps/dashboard/src/main.jsx` | suspended banner (existing error-banner pattern L1071–1077, CTA = appeal link), alerts list from `/v1/team/alerts`, status chip (R7) |
| `tests/fake_control_plane.py`, `tests/test_abuse.py`, `tests/test_abuse_suspension.py` | FakeAbuseStore + rule unit tests + REST/MCP suspension E2E |

### Architecture

**Event flow (R1, R2):**

```
[write path]                    [evaluation — request path, post-write]
REST endpoint ──record_write_ops──> abuse.record('point_create')  fire-and-forget
MCP _quota_gated ─────────────────> abuse.record('point_create')  fire-and-forget
api_keys INSERT (any path) ──trigger──> abuse_events('key_create')  (no code needed)
                                     │
                                     ▼
                          abuse.evaluate(team, type):
                            count = SELECT count(*) FROM abuse_events
                                    WHERE team_id=$1 AND event_type=$2
                                    AND created_at > now()-window
                            (indexed; ~1ms; in request path)
                            → stage 1 breach: flag row + notify
                            → stage 2 (consecutive window still breached):
                              RPC abuse_suspend(team) + notify + alert_store
```

Recording is best-effort fire-and-forget (never gates the write); **evaluation is synchronous in the request path** but is a single indexed count, not a scan.

**Two-stage response** (identical semantics across approaches):

1. **Stage 1 — flag (1st breach):** no enforcement change; row marked `flagged`; owner email + dashboard banner ("suspicious activity detected") + ops alert (deduped via `alert_store`). A single spike (bulk import script, test harness) stops here.
2. **Stage 2 — auto-suspend (2nd consecutive window still over threshold):** `abuse_suspend` RPC sets `teams.suspended_at`; owner + ops notified with appeal link; subsequent auth is rejected.
3. **Appeal:** email link / 403 link → operator runs `abuse_unsuspend` → `suspended_at = NULL` → immediate recovery. Audit trail: the events that triggered the flag remain queryable.

**Enforcement (R5):** `teams.suspended_at` read by `resolve_api_key` (one round-trip, already fetched) → `get_current_team` raises `HTTPException(403, detail={"code": "SUSPENDED", "appeal_url": ...})`; `TeamResolutionMiddleware` returns `-32006` with `data.appeal_url` and **pops the token's 60s cache entry** so suspension applies immediately even for cached MCP sessions. `session_key` mint checks `suspended_at` → re-mint impossible while suspended. Registry mode: `SET t.suspended_at` on the Team node (billing.py `SET t.subscription_status` precedent) + `get_current_team` registry path reads the prop.

**Reads (R3):** `ReadVelocityTracker` — shared module-level `defaultdict(list)` keyed by resolved `key_id`, 5-min window, prune + cap (mirrors `_register_buckets`). REST: increment in `get_current_team` post-success (every authed read passes through it; failed auths don't count). MCP: increment in `TeamResolutionMiddleware` post-resolution for `tools/call` methods **not** in `_QUOTA_GATED` (read tools). One tracker shared by both transports → same key counted once across REST+MCP. Breach → `notify_abuse('abuse_read_velocity')`, once per key per window (track notified window starts). Gated by `RATE_LIMIT_DISABLED=1` in tests (existing convention).

**Geo (R4):** `GeoResolver.resolve(request) → country | None`. Default = `CF-IPCountry` header passthrough (fail-open: no header → None → rule inactive). Optional `IPINFO_TOKEN` resolver for Fly-direct deployments. Post-auth: if country resolves and is not in the team's seen set (`SELECT DISTINCT country FROM abuse_events WHERE team_id=$1 AND event_type='auth_ip'`), record `auth_ip` event + notify owner. Seen-set cached in-process per team (24h TTL) to avoid a query per request; the durable check happens on cache miss.

**Dashboard (R7):** `GET /v1/team/alerts` returns recent `flagged`/suspend rows from `abuse_events` (joined to key id + timestamps). `TeamInfoResponse.status` = `"suspended" | "flagged" | "active"` (additive Pydantic field). Banner with appeal CTA renders for suspended; revoke button unchanged (re-mint gated server-side).

### Failure modes

- **False positive suspension:** two-consecutive-window staging means a burst alone can never auto-suspend; ops alert + appeal link give a recovery path; `abuse_unsuspend` is a one-liner; full event history is queryable for post-mortem. Thresholds configurable per env.
- **Rate-limit bypass:** durable counters cannot be reset by deploy (attacker cannot "wait out" a deploy); signup-path key creates are seen by the trigger (the path `insert_api_key`-only designs miss); suspension is enforced at the auth layer — there is no authed route that skips `get_current_team`/middleware; MCP cache-bust closes the 60s window. Residual: R3 is per-key per spec — rotating keys resets the read counter (see exfiltration).
- **Exfiltration not detected:** R3's 100 reads/5min/key misses slow drips (99 reads/5min sustained), multi-key fan-out, AND sequential key rotation (rotate keys every 5min at <100 reads each — each key's reads age out before the next ramps, so neither the per-key nor the team aggregate breaches). Mitigation: per-key + per-team aggregate catches concurrent fan-out; the sustained-velocity alert (>50% threshold for N consecutive windows) is the fix for slow-drip and sequential rotation — tracked as a follow-on (noted in the PR description; gh is rate-limited, so the issue is filed when limits clear).

### Risks

- **Trigger on `api_keys` INSERT** touches the provisioning path (`provision_team` RPC): a broken trigger breaks signup. Mitigation: trigger body is a plain INSERT with no external calls; covered by a migration test that exercises `provision_team` E2E. Cycle-2 test notes: (a) assert the `ON CONFLICT (lookup_hash) DO NOTHING` re-provision path inserts nothing → no duplicate `key_create` event; (b) comment-guard: a future migration that backfills `api_keys` rows would emit spurious events — backfills must disable the trigger for the statement.
- Migration 0015 adds DDL to a live control plane; 0014 precedent (metering) shows the pattern is safe, but a review gate is required.
- Per-request auth-latency: R4 seen-set is cached (no per-request query); R1/R2 evaluation is one indexed count per write — must be measured, keep threshold-count query lean (single `SELECT count(*)`).
- Fire-and-forget recording can drop events under load (bounded queue + drop-with-log, never backpressure).

### Tradeoffs

| Dimension | Assessment |
|---|---|
| Durability | Full: events + suspension state survive deploy/restart |
| Surface completeness | Full: trigger covers signup RPC key creates; both transports write-audited for R1 |
| Detection latency | Instant (request path) |
| Migration/DDL risk | Highest of the three (one new table + trigger + column + RPCs) |
| Ops burden | Low-moderate: alerts + un-suspend action; appeal is manual (same in all) |
| Test complexity | Moderate: FakeAbuseStore via monkeypatch; trigger tested against real migrations in integration |

### Best-fit-if

- Durable enforcement is non-negotiable (auto-suspend must survive deploys).
- The signup key-mint path must be counted (R2 surface completeness) without modifying the Edge Function.
- Team accepts one additive migration + a reviewed DB trigger.
- Detection latency should be seconds, not minutes (suspension is immediate on second breach).

---

## Approach B — Middleware-only in-memory (minimal)

### Description

No new table, no migration, no control-plane dependency for rules. Every rule is an in-process counter at an existing seam, following the proven `RateLimitMiddleware` / `MCPRateLimitMiddleware` pattern (`defaultdict(list)` timestamps + prune + `asyncio.Lock` + `RATE_LIMIT_DISABLED`). Suspension is an in-memory set of suspended team ids + best-effort registry `Team.suspended_at` prop (registry mode only); enforcement reads the set at the auth layer. The deploy-reset behavior is the defining weakness and is documented, not hidden.

### Files touched

| File | Change |
|---|---|
| `tortoise/abuse.py` (new, much smaller) | `AbuseCounter` (sliding window, prune/cap, Lock) + `SuspensionState` (in-memory set + registry prop write-through) + `ReadVelocityTracker` |
| `tortoise/hosted_api.py` | counter increments at `record_write_ops` hook (R1, L1035) and `insert_api_key` (R2, supabase_control L480); suspension check + 403 in `get_current_team`/`_get_current_team_supabase`; read-velocity increment post-auth (R3); geo check via header (R4); `GET /v1/team/alerts` (served from in-memory ring buffer); `TeamInfoResponse.status`; siteverify (R6); `session_key` gate |
| `tortoise/mcp_auth.py` | `ERR_SUSPENDED`; suspension check in `TeamResolutionMiddleware` + cache-bust; read-velocity for non-write `tools/call`; point-create counter for MCP writes |
| `tortoise/mcp_server.py` | `_quota_gated`: point-create counter (R1) |
| `tortoise/notify.py`, `tortoise/__main__.py`, `website/*`, `website/apps/dashboard/src/main.jsx` | same as Approach A |
| `tests/test_abuse_inmemory.py` | counter + suspension unit tests; RATE_LIMIT_DISABLED-style env gate for rule logic |

### Architecture

Rule evaluation is **100% request-path and in-process** — there is no durable store and no background task. Each seam increments a timestamp list; after increment, prune > window and compare length to threshold. Stage-1 breach → append to in-memory flagged set + notify. Stage-2 (next evaluation in a subsequent window still over threshold) → add team id to `SuspensionState` + notify. Enforcement: `get_current_team` / `TeamResolutionMiddleware` check the set first, before key resolution (cheap, O(1)), return 403 `SUSPENDED` / `-32006` with appeal link, bust the MCP cache entry. Reads and geo are identical to Approach A's in-memory pieces (shared `ReadVelocityTracker`; header-only geo resolver — no `IPINFO_TOKEN` path needed since there's no durable seen-set; instead an in-memory per-team seen-countries set with 24h TTL). CAPTCHA, dashboard, CLI, notify: identical to A.

### Failure modes

- **False positive suspension:** easiest rollback of all three — a restart clears the suspended set; flag→suspend staging still applies; appeal link still works.
- **Rate-limit bypass:** the critical gap — **every deploy resets all counters and clears all suspensions** (auto-deploy on merge makes this a real, recurring event). An attacker can pace abuse to land between deploys, and a suspension evaporates on the next deploy. Signup-path key creates are invisible (the `provision_team` RPC bypasses `insert_api_key` — R2 counts only dashboard-created keys).
- **Exfiltration not detected:** same per-key counter and residual as A; the deploy-reset also resets read-velocity windows (5-min window bounds the damage).

### Risks

- Production rules are best-effort by construction: a deploy mid-window silently resets enforcement; nobody is notified when it happens (needs an ops heartbeat to even detect it).
- Multi-worker deployment (future scale-out) splits all counters — single-worker assumption is load-bearing.
- No historical evidence: post-incident forensics impossible without a store (violates the "durable telemetry" half of the confirmed problem).

### Tradeoffs

| Dimension | Assessment |
|---|---|
| Durability | None (by design): counters + suspension state are process-lifetime |
| Surface completeness | R2 blind on signup-path key creates; otherwise complete |
| Detection latency | Instant (request path) |
| Migration/DDL risk | Zero — no SQL, no trigger, no column |
| Ops burden | Lowest to build; highest to trust: rules silently vanish on deploy |
| Test complexity | Lowest: pure unit tests, no fake store needed |

### Best-fit-if

- The team wants zero migration/DDL risk and a shippable first slice today (interim while Approach A is staged).
- Auto-suspend is acceptable as best-effort — e.g., a small trust cohort where owner notification (not enforcement) is the primary requirement and deploys are infrequent.
- The confirmed problem is re-scoped to "notify owner + best-effort flag" rather than "durable enforcement."

---

## Approach C — Event-sourcing via existing `audit_events` (reuse)

### Description

No new event table: rules run as **scheduled SQL aggregations over the existing `audit_events` table** (migration 0002), executed by a background asyncio task in the FastAPI lifespan (1-minute cadence). Suspension state is durable via a small migration (`teams.suspended_at` + RPCs — the one piece of DDL this approach cannot avoid). Audit coverage is extended to MCP writes via `_quota_gated`. Reads (R3) and geo (R4) are handled separately with the same in-memory pieces as A/B — append-only audit is the wrong store for high-volume read events.

### Files touched

| File | Change |
|---|---|
| `supabase/migrations/0015_suspended_at.sql` | `teams.suspended_at` column + `abuse_suspend`/`abuse_unsuspend` RPCs + **composite index on `audit_events (team_id, operation, created_at DESC)`** (0002's `(team_id, created_at DESC)` index cannot serve the `operation`-filtered aggregates efficiently) |
| `tortoise/abuse.py` (new) | `AbuseRuleEngine` (threshold defs, two-stage state machine); `SuspensionStore` (Supabase RPC / registry prop); `ReadVelocityTracker` + `GeoResolver` (as A) |
| `tortoise/hosted_api.py` | lifespan: start `AbusePoller` background task (every 60s: `SELECT operation, count(*) FROM audit_events WHERE team_id=$1 AND created_at > now()-interval GROUP BY operation` per active team; evaluate R1/R2); suspension check + 403 in auth; `GET /v1/team/alerts` (computed from audit_events aggregates at request time); `TeamInfoResponse.status`; siteverify; `session_key` gate |
| `tortoise/mcp_server.py` | `_quota_gated`: best-effort `audit.record('point_create')` after successful writes (MCP audit gap today) |
| `tortoise/mcp_auth.py` | `ERR_SUSPENDED` + cache-bust (as A) |
| `tortoise/notify.py`, `tortoise/__main__.py`, `website/*`, `website/apps/dashboard/src/main.jsx` | same as A |
| `tests/test_abuse_poller.py` | poller fed by FakeControlPlane-seeded audit rows; state machine unit tests |

### Architecture

```
[lifespan]  AbusePoller (asyncio task, every 60s):
              for team in active_teams:
                for (operation, n) in SQL aggregate over audit_events:
                  R1: operation='point_create', window 1h, n > 500
                  R2: operation='api_key_create', window 24h, n > 10
                → two-stage state machine (same staging as A)
                → abuse_suspend RPC → teams.suspended_at → auth-layer 403
```

Rule evaluation runs in a **background task, not the request path** — zero write-path latency, but detection latency is up to ~60s per window, so auto-suspend lands roughly 1–2 minutes after the second breach instead of immediately. The poller needs a heartbeat: a periodic sentinel audit row (or `last_polled_at` in memory) that ops alerting checks, so a crashed poller is loud, not silent.

- **R2 surface completeness:** REST key creates are already audited; the `provision_team` RPC path is **not** audited (same gap as B). Optional fix: a trigger on `api_keys` INSERT writing `audit_events('api_key_create')` — reuses the existing table instead of creating `abuse_events`, at the cost of the trigger (which is A's differentiator for its own table).
- **R1:** REST writes are audited today; MCP writes are not → `_quota_gated` gains a best-effort audit record.
- **R3/R4:** in-memory `ReadVelocityTracker` + header geo resolver exactly as A — audit is append-only and high-volume read rows would bloat it and pollute the canonical history invariant (rows preserved across migration, per supabase_control L913 comments).

### Failure modes

- **False positive suspension:** two-window staging identical to A; un-suspend RPC identical; the audit rows themselves are the post-mortem evidence (no new store needed).
- **Rate-limit bypass:** depends on `TORTOISE_AUDIT_DSN` being set in production — if the audit pipeline is JSONL-only in prod (no DSN in fly.toml today; must be confirmed via `fly secrets list`), **no rule ever fires and nothing indicates it** (heartbeat alert is the only tripwire). MCP writes are only counted after the `_quota_gated` audit hook lands. Suspension state itself is durable once set (column + RPC), but detection is not guaranteed.
- **Exfiltration not detected:** same per-key counter and residual as A/B.

### Risks

- The DSN uncertainty is existential for this approach: the whole rule substrate rests on a configuration that may not exist in production. Pre-flight gate: confirm `TORTOISE_AUDIT_DSN` in `fly secrets list`; if unset, C is dead on arrival (JSONL fallback has no queryable surface).
- Poller liveness: background task can die silently → heartbeat/watchdog required (a new operational surface none of the other approaches needs).
- Detection latency: ~60s+ per evaluation cycle; auto-suspend is 1–2 minutes behind the second breach (vs request-path instant in A/B).
- Composite index migration on a live, growing table (0002) — safe (CREATE INDEX CONCURRENTLY-style review needed) but adds DDL risk A avoids by creating its own table.

### Tradeoffs

| Dimension | Assessment |
|---|---|
| Durability | High for suspension state; event history already durable **if DSN set** |
| Surface completeness | REST write coverage today; MCP needs the `_quota_gated` audit hook; signup RPC key creates need the trigger to be complete |
| Detection latency | ~60s window + evaluation cadence (1–2 min to suspension) |
| Migration/DDL risk | Low-moderate: one column + RPCs + composite index; trigger only if R2 completeness required |
| Ops burden | Highest: poller heartbeat + DSN verification + audit pipeline dependency |
| Test complexity | Moderate: poller fed with FakeControlPlane-seeded audit rows (no new fake store) |

### Best-fit-if

- `TORTOISE_AUDIT_DSN` is confirmed set in production (audit already double-writes to Postgres), so the substrate exists for free.
- The team prefers zero new tables and SQL-aggregate rules over a new event store, and accepts 1–2 minute detection latency.
- The audit pipeline is already operationally trusted (alerting on audit failure exists), so the poller dependency is a small extension.

---

## Comparison summary (facts, not verdict)

| | A — Supabase substrate | B — Middleware in-memory | C — audit_events polling |
|---|---|---|---|
| Event store | new `abuse_events` + trigger | none (process memory) | existing `audit_events` |
| Suspension durability | `teams.suspended_at` + registry prop | in-memory set (+ registry prop in selfhost) | `teams.suspended_at` + registry prop |
| Survives deploy? | yes | **no** (rules + suspension reset) | yes (suspension); events yes-if-DSN |
| R2 signup-path key creates | trigger sees all | **missed** (RPC bypasses seam) | missed unless trigger added |
| R1 REST/MCP coverage | hooks at both seams | hooks at both seams | REST audited; MCP needs hook |
| Detection latency | instant | instant | 1–2 min |
| R3 reads | in-memory per-key (shared REST+MCP) | same | same (audit not used for reads) |
| R4 geo | header + optional IPINFO, durable seen-set | header only, in-memory seen-set | header + optional IPINFO, computed from audit |
| DDL risk | highest (table+trigger+column+RPCs) | none | low (column+RPCs+index; trigger optional) |
| Ops burden | low-moderate | lowest build / highest trust | highest (poller heartbeat + DSN) |
| Test complexity | moderate (FakeAbuseStore) | lowest | moderate (poller + seeded audit) |
| Failure-mode coverage | best (durable, surface-complete, staging) | worst (deploy reset + R2 gap) | mid (DSN-dependent; staging retained) |

All three share: two-consecutive-window staging for auto-suspend, 403 `SUSPENDED`/`-32006` enforcement at the auth layer with MCP cache-bust, appeal flow via operator un-suspend, Turnstile on signup + siteverify, dashboard banner/alerts/revoke, CLI 403-detail parse, `RATE_LIMIT_DISABLED`-gated test paths.

Open questions to resolve at convergence (not decided here):

1. Is `TORTOISE_AUDIT_DSN` set in prod? (kills C outright if not)
2. Is R2 required to count signup-path key creates? (decides trigger necessity; A has it built-in)
3. Is deploy-reset of enforcement acceptable for any window? (B's core trade)
4. Is per-team read aggregation (fan-out detection) in scope for R3 or a follow-on?

---
---

# Phase 5 — Solution Convergence

> Convergence output for issue #308. Per the quality-over-convenience rule, selection is evaluated on outcome quality (durable enforcement, surface completeness, failure-mode coverage) — NOT on diff size or file count.

## Decision: Approach A — Supabase-centric durable substrate

### Resolving the open questions (from the divergence doc)

| # | Question | Answer | Consequence |
|---|---|---|---|
| 1 | Is `TORTOISE_AUDIT_DSN` set in prod? | **Unverifiable from the worktree** — `fly.toml` carries no DSN; `fly secrets list` requires Fly creds this session doesn't have. | C's substrate is uncertain → C cannot be the primary. A does not depend on it at all. |
| 2 | Must R2 count signup-path key creates? | **Yes.** The confirmed problem demands surface completeness, and the signup path (`provision_team` RPC) is the highest-volume key-create surface on the platform. | Only A covers it without app-code in the Edge Function (DB trigger). |
| 3 | Is deploy-reset of enforcement acceptable? | **No.** Auto-deploy fires on every merge to main; a suspension that evaporates on the next merge is not enforcement, it's a suggestion. The issue body says "auto-suspend" — a durable predicate. | B rejected as primary. |
| 4 | Per-team read aggregation for R3? | **Yes, in scope** — the tracker is in-memory; a second team-keyed dict is ~10 lines and closes the multi-key fan-out bypass documented in every approach's failure modes. Per-key stays the spec rule; team aggregate is an additional notification trigger (not a suspension trigger). | Closes a known exfiltration gap cheaply. |

### Why A wins (quality over convenience)

1. **Durability is the core of the confirmed problem.** Suspension state (`teams.suspended_at`) and the event evidence (`abuse_events`) survive deploys and restarts. B fails this by construction; C fails detection guarantees (DSN-dependent).
2. **Surface completeness.** The `api_keys` INSERT trigger sees BOTH key-create paths (dashboard `insert_api_key` AND signup `provision_team` RPC) — the one seam in the system that does. R2 is otherwise blind to the highest-volume path.
3. **Instant detection.** Request-path evaluation (one indexed count) suspends on the second consecutive breached window immediately — no 60s poller gap, no heartbeat/watchdog operational surface.
4. **Failure-mode coverage is strictly best** (per the comparison table): durable counters can't be waited out; auth-layer enforcement has no bypass route on either transport; MCP cache-bust closes the 60s resolution cache.
5. **The extra cost is one reviewed migration** (table + trigger + column + RPCs) — the same pattern 0014 (metering) already shipped safely. That is a known, bounded risk traded for unbounded enforcement gaps in B/C.

### Rejected alternatives

**Approach B — Middleware-only in-memory.** Rejected because deploy-reset makes enforcement unreliable on an auto-deploy platform, and R2 is blind to signup-path key creates. *Would have been better if* the platform had infrequent deploys AND abuse response were owner-notification-only (no enforcement requirement) — i.e., if the confirmed problem were re-scoped to "best-effort flag". It remains a useful fallback if migration 0015 is ever blocked: the rule-engine module (`tortoise/abuse.py`) is store-agnostic, so a `MemoryAbuseStore` could ship as an interim.

**Approach C — audit_events polling.** Rejected because its substrate (`TORTOISE_AUDIT_DSN`) is unconfirmed in production and its detection latency (1–2 min) is strictly worse for the same DDL budget. *Would have been better if* the audit DSN were confirmed set AND the team preferred zero new tables over instant detection. Its poller idea is salvaged nowhere — request-path evaluation is simpler and faster.

### Converged design deltas (vs. the divergence sketch)

1. **R3 team aggregate:** `ReadVelocityTracker` keeps per-key AND per-team windows; breach of either notifies (per-key is the spec rule; team aggregate catches fan-out). Neither suspends — R3 is notify-only per the issue body.
2. **Evaluation placement:** R1/R2 evaluation runs in the request path *after* the triggering write succeeds, gated by an env kill-switch (`TORTOISE_ABUSE_DISABLED=1`) that mirrors `RATE_LIMIT_DISABLED` for tests/local. Recording stays fire-and-forget.
3. **Suspension enforcement is ONE check in the shared resolution path**: `resolve_api_key` returns `suspended_at`; `get_current_team` (REST) and `TeamResolutionMiddleware` (MCP) both reject with 403 SUSPENDED / −32006 + appeal URL. Registry mode reads the `Team.suspended_at` prop. No per-endpoint checks (bypass-proof).
4. **Registry fallback store:** selfhost/registry mode gets a `MemoryAbuseStore` (rules run, enforcement via `Team.suspended_at` prop) so the code path is identical and tested, with documented deploy-reset semantics for selfhost. Known registry residual: the registry `session_key` mint writes APIKey nodes directly (not via `insert_api_key`), so R2 under-counts selfhost session mints — accepted degradation for selfhost.
5. **CAPTCHA:** signup.html gains the index.html Turnstile pattern verbatim; server-side siteverify in `/v1/signup/email` + `/v1/register` when `TURNSTILE_SECRET_KEY` is set; fail-open when unset (widget hidden, no verification) — matching the waitlist handler's existing behavior.
6. **CLI:** `__main__.py` 403 branches parse `detail.code == "SUSPENDED"` → print appeal URL instead of generic "key_rejected".
8. **R1 event purity (solution-verify P1):** `point_create` events are recorded ONLY for create-family ops — MCP `create_point`/`create_operator` tools (not the other 22 `_quota_gated` tools: update/supersede/invalidate/retract/etc.) and REST POST /v1/points (not capture_session). A sustained burst of edits/updates must never trip R1. **Cycle-2 fix:** MCP `tortoise_ingest` (the bulk path — one call can create hundreds of Points) records `point_create` WEIGHTED by the number of Points actually created in the bundle (sdk `ingest` return value); a 600-point ingest counts as 600 events. **Cycle-3 fix (boundary = actual Point creation, not tool name):** every `_quota_gated`-wrapped tool that creates Points records weighted — `checkpoint` (weight = items filed), `file_decision` (weight = 1 + options + evidence Points), `file_human_approval` (1), `diary_write` (1), `create_point`/`create_operator` (1 each); REST records from POST /v1/points (1) AND `capture_session` (weight = Points actually created — turns + extractions, which are Points too). An introspective test scans the wrapped tool set for Point-creating sdk calls and asserts recording membership, so a future Point-creating tool cannot silently re-open the bypass.
9. **R2 bootstrap exclusion (solution-verify P1):** the `api_keys` trigger skips rows with `created_via='bootstrap'` (24h ephemeral, cap-exempt dashboard session keys — normal session churn of 3–4 users would exceed 10/24h). Recovery mints, provision keys, and dashboard-created keys count.
10. **R4 on both transports (solution-verify P1):** the geo check runs in BOTH `get_current_team` (REST) and `TeamResolutionMiddleware` (MCP) — on EVERY post-auth request, cache-hit and cache-miss alike (cycle-2 fix: a check placed only after fresh resolution would skip requests served from the 60s MCP token cache, letting an IP-rotation burst evade R4). Cheap: the seen-set is an in-process cache with 24h TTL backed by a durable lookup on miss.
11. **R3 read definition (solution-verify P2):** REST counts GET requests only (writes are POST/PATCH/DELETE — a write burst must not fire R3). MCP counts `tools/call` for tools NOT in an EXPLICIT write-tool set — the `_quota_gated`-wrapped tools including `tortoise_ingest` (cycle-2 fix: the `_QUOTA_GATED` frozenset is NOT that set — `ingest` is wrapped but absent from it; the write set is derived from the actual wrapped tools, asserted by a membership test so no write tool is ever counted as a read). The middleware reads the JSON-RPC body once (Starlette caches `request.body()` — downstream re-reads the cache, no receive re-serving needed).
12. **Session-auth boundary (cycle-2 rewrite):** enforcement is scoped to API-key auth (both transports) + session-key mint gating. While suspended, ALL API-key-authed endpoints — including `DELETE /v1/team/keys/{id}` — return 403 SUSPENDED. This is deliberate: revoking keys mid-suspension is moot (every key is already rejected at auth), and the R7 banner + appeal CTA render FROM the 403 detail (`appeal_url`) via the existing dashboard error-banner pattern. `GET /v1/team/alerts` is a session-authed endpoint (`get_current_user` + membership check, same pattern as the other dashboard routes) so the alert history stays visible during suspension.
13. **Staging semantics pinned (cycle-2 P1):** evaluation is event-triggered (runs in the request that recorded the triggering event). Stage 1: window count > threshold → flag (row marked, notify; `flagged_at` recorded). Stage 2: an evaluation that occurs at or after `flagged_at + window` (i.e., the sliding window has fully advanced past the flag moment) AND still finds count > threshold → suspend. Consequence (the load-bearing guarantee): a single burst contained in one window can flag but can NEVER auto-suspend; suspension requires the breach to persist across a full window boundary. A team that flags and then goes quiet receives no further evaluations and is never suspended — intended: suspension requires continuing abuse; the flag stays visible in alerts.
14. **Suspension immediacy mechanism (cycle-2 P2, cycle-3 semantics):** a process-wide suspended-team set in `tortoise/abuse.py` is written at suspend time (evaluation always runs in-request, single worker). The set is a CACHE-INVALIDATION SIGNAL, never a rejection authority: membership forces a fresh resolution (MCP bypasses the LRU entry; REST always resolves fresh), and the durable `teams.suspended_at` from that resolution is the ONLY ground for a 403/−32006. The entry is removed when a fresh resolution returns `suspended_at = NULL` — so `abuse_unsuspend` (DB-side) self-heals on the very next request (AC8), and a worker restart starts with an empty set. Suspension is still immediate: the set is written in the same process before the suspending request returns, so the team's next request on either transport resolves fresh and is rejected. Tests: suspend → next request 403 on both transports (with warm LRU); un-suspend → next request 200 (with warm LRU).
7. **Appeal link:** static docs URL (`https://tortoise.premiselabs.co/docs.html#appeal`) + `mailto:` ops address from `BILLING_NOTIFY_TO`-adjacent env `ABUSE_APPEAL_EMAIL` (fallback support@premiselabs.co). Operator un-suspends via `abuse_unsuspend` RPC (one-liner, documented in the PR).

## Plan draft

### Problem statement

The Tortoise Hosted platform has no automated abuse detection or enforcement: a compromised or malicious key can create unbounded Points, mint keys, and exfiltrate graph data without any alert, suspension, or CAPTCHA barrier on signup.

### Proposed solution

A durable abuse substrate (`abuse_events` + `teams.suspended_at`, migration 0015) with a request-path rule engine (`tortoise/abuse.py`), auth-layer enforcement on both transports (403 SUSPENDED / −32006 with appeal link), owner notifications (Resend + Telegram + alert_store), Turnstile CAPTCHA on signup, and a dashboard alert surface with the existing one-click revoke.

### Rules + thresholds (env-overridable)

| Rule | Metric | Threshold | Window | Response |
|---|---|---|---|---|
| R1 | point_create / team | > 500 | 1h | stage-1 flag → stage-2 auto-suspend (2nd consecutive breached window) |
| R2 | key_create / team | > 10 | 24h | stage-1 flag → stage-2 auto-suspend |
| R3 | reads / key (and / team) | > 100 | 5min | notify Owner (no suspension) |
| R4 | auth from new country | first unseen country | — | notify Owner |

### Implementation plan (component order)

1. **Migration 0015** — `abuse_events` (+RLS, indexes), `api_keys` INSERT trigger, `teams.suspended_at`, `abuse_suspend`/`abuse_unsuspend` RPCs.
2. **`tortoise/abuse.py`** — store protocol (Supabase/Fake/Memory), rule engine (two-stage state machine, window counting), `ReadVelocityTracker`, `GeoResolver` (CF-IPCountry header, fail-open).
3. **Control plane** — `get_abuse_store()` seam, `resolve_api_key` + registry path return `suspended_at`, recording helpers.
4. **REST enforcement + recording** — `get_current_team` suspension 403 + geo check + read-velocity; `_record_write_op` → point_create event; `/v1/team/alerts`; `TeamInfoResponse.status`; session-key mint gated on suspension; siteverify on signup/register.
5. **MCP enforcement + recording** — `TeamResolutionMiddleware` suspension error + cache-bust + read-velocity; `_quota_gated` → point_create event.
6. **Notifications** — `notify.py` abuse kinds + `notify_abuse()` (owner email w/ ops fallback, Telegram, alert_store on suspend).
7. **Dashboard** — suspended banner + appeal CTA, alerts list, status chip.
8. **Signup CAPTCHA** — signup.html Turnstile widget (index.html pattern).
9. **CLI** — 403 SUSPENDED detail parse + appeal link.
10. **`.env.example`** — `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`, `ABUSE_APPEAL_EMAIL`, `IPINFO_TOKEN` (optional), rule threshold overrides.

### Testing strategy

- **Unit:** rule engine state machine (stage transitions, consecutive-window semantics, threshold boundaries 500/501), read-velocity tracker (per-key + per-team, window expiry, notify dedup), geo resolver (header present/absent), Turnstile siteverify (fail-open unset / fail-closed invalid / pass valid) with httpx mocked.
- **Integration (FakeControlPlane + TestClient):** suspension end-to-end on REST (point-burst → flag → suspend → 403 SUSPENDED w/ appeal link), MCP (−32006 + cache-bust), mint gated while suspended, un-suspend restores access; key-create trigger path via `provision_team` rpc in the fake; `/v1/team/alerts` contents; R3 notify on velocity breach; R4 notify on new country.
- **Failure-mode tests (per issue body):** (a) false-positive suspension — single burst flags but never suspends; un-suspend restores; (b) rate-limit bypass — suspension survives across "deploy" (fresh app instance, same store); trigger counts signup-path key creates; (c) exfiltration detection — 101 reads/5min from one key notifies; fan-out (50+51 across two keys) notifies via team aggregate.
- **E2E alignment:** E2E-2-D (free-tier abuse portion: R1/R2 limits → suspend → 403) and E2E-7-D (security baseline: exfiltration R3 detection). Note: the epic docs these reference (`docs/epics/2026-08-03-tortoise-hosted-platform/`) were lost in the repo migration; alignment is reconstructed from the issue body and stated in the PR.

### Verification plan

Full suite `python -m pytest tests/ -q` green (embedded FalkorDBLite; `RATE_LIMIT_DISABLED=1` convention). Migration SQL syntax-checked; trigger body is a plain INSERT (no external calls). No new runtime deps (httpx already in requirements).

### Acceptance criteria

- AC1: 501+ point creates by one team inside 1h flag the team (stage 1) and a breach persisting across a full window boundary suspends it (design delta 13); a single 600-point `ingest` (or equivalent `checkpoint`/`capture_session`) flags but never suspends; 500 updates/supersedes never flag (design delta 8); every Point-creating tool is in the R1 recording map (introspective membership test).
- AC2: 11+ key creates in 24h (including via signup RPC) flag/suspend identically.
- AC3: A suspended team's key gets HTTP 403 `{"code": "SUSPENDED", "appeal_url": ...}` (REST) and JSON-RPC −32006 with the same data (MCP), immediately — pre-cache suspended-set check (design delta 14).
- AC4: 101 reads in <5 min from one key notify the Owner once per window; team fan-out also notifies.
- AC5: First auth from a previously unseen country notifies the Owner (fail-open when country unresolvable).
- AC6: Signup page renders Turnstile when a site key is configured and server-side siteverify rejects bad tokens when the secret is set; both endpoints are fail-open when the secret is unset.
- AC7: Dashboard renders a suspended banner with appeal CTA from the 403 detail; the session-authed alert list shows flags/suspensions; one-click revoke works while NOT suspended (the R3/R4 response surface) and is intentionally unreachable via API-key auth during suspension (design delta 12); session-key re-mint is gated while suspended.
- AC8: Un-suspending (RPC/prop clear) restores API access on the next request — including with a warm MCP LRU entry (design delta 14 signal semantics).

### Runtime prerequisites

- Supabase migration 0015 applied (auto via existing migration flow).
- Fly secrets: `TURNSTILE_SECRET_KEY` (new), optionally `ABUSE_APPEAL_EMAIL`, `IPINFO_TOKEN`. The site key is a hand-edited literal in `website/signup.html` (mirrors `index.html` — static site, no env injection); the widget stays hidden until provisioned.
- No `TORTOISE_AUDIT_DSN` dependency (approach A is audit-independent).

### Wiring check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `abuse_events` table + trigger + RPCs | DB | migration 0015 (this PR) | ✅ |
| `teams.suspended_at` | DB | migration 0015 | ✅ |
| REST auth suspension | API | `get_current_team` / `_get_current_team_supabase` | ✅ |
| MCP suspension | API | `TeamResolutionMiddleware` | ✅ |
| R4 geo on MCP | API | `TeamResolutionMiddleware` post-resolution | ✅ |
| Key-create surface (signup RPC) | DB trigger | `trg_api_keys_abuse` | ✅ |
| Owner notification | Resend/Telegram | `notify.py` extension | ✅ |
| Ops alerting (suspend) | alert_store | `notify_abuse` on suspend | ✅ |
| Signup CAPTCHA | website | signup.html + siteverify | ✅ |
| Dashboard alerts/revoke | UI | main.jsx additions | ✅ |
| CLI 403 UX | CLI | `__main__.py` detail parse | ✅ |
| Fly/Pages env keys | deploy | .env.example + PR note | ✅ |
| Appeal process | ops | RPC documented in PR | ✅ |

No wiring gaps. Adjacent findings NOT absorbed (tracked as notes only): audit_events lacks MCP write coverage (C's gap, irrelevant to A); `_QUOTA_SELECT` has no `email` (owner notification uses a separate team fetch).

Follow-up (solution-verify P4): verify `TORTOISE_AUDIT_DSN` in `fly secrets list` at first deploy — only relevant if approach C is ever revived as a fallback.

---

## Phase 5.5 — solution-verify cycle log

### Cycle 1
- Verifier A: P0=0, P1=3, P2=1, P3=4, P4=2. Verifier B: timed out (no output) → re-dispatch.
- Controller action: FIXED all three P1s (design deltas 8–10 above) + P2 (delta 11) + P3 items (delta 12, Pages env mechanism corrected, wiring row added, stale line refs superseded by deltas, C-index claim noted); P4 DSN follow-up recorded.
- Cycle 2: both verifiers re-dispatched against the corrected plan.

### Cycle 2
- Verifier A (re-run): cycle-1 P1s confirmed fixed; NEW P1 (R1 bypass via `tortoise_ingest` bulk tool) + P2 (R3 write-set complement wrong — `ingest` wrapped but absent from `_QUOTA_GATED`) + P3 (R4 must run on cache-hit path too).
- Verifier B (fresh): P1 (two-window staging semantics undefined — the load-bearing false-positive guarantee), P1 (delta 12/AC7 contradiction: revoke unreachable via API-key auth while suspended), P2 (cache-bust mechanism unspecified), P2 (sequential-rotation residual unstated), P3/P4 nits.
- Controller action: FIXED — delta 8 (ingest weighting), delta 10 (every-request geo), delta 11 (explicit write set), delta 12 rewrite (revoke moot mid-suspension; banner from 403; alerts session-authed), delta 13 (staging semantics pinned), delta 14 (pre-cache suspended-set mechanism); AC1/AC3/AC7 rewritten; residuals + trigger test notes added.
- Cycle 3: focused confirmation pass with both verifiers.

### Cycle 3
- Verifier A: cycle-2 items 1–3 confirmed fixed; NEW P1 — R1 boundary by tool name misses other Point-creating tools (`checkpoint` unbounded items, `file_decision` N-per-call, `file_human_approval`, `diary_write`, REST `capture_session`).
- Verifier B: cycle-2 items 1/2/4 confirmed fixed; NEW P1 — pre-cache suspended set without eviction contradicts AC8 (un-suspend could never restore access).
- Controller action: FIXED — delta 8 rewritten (boundary = actual Point creation, weighted per seam, introspective membership test), delta 14 rewritten (set = cache-invalidation signal only; durable `suspended_at` is the sole authority; entry cleared on fresh resolution with NULL), AC1/AC8 updated.
- Cycle 4: final confirmation pass.

### Cycle 4
- Verifier A: NO ISSUES FOUND (delta 8 boundary fix verified against code; only a hypothetical P3 REST-introspection residual, incorporated as implementation note).
- Verifier B: NO ISSUES FOUND (delta 14 signal semantics verified structurally sound; immediacy holds under the documented single-worker premise; multi-worker scale-out noted as documented future risk).
- **GATE PASSED** — 4 cycles, P0 total: 0. All P1s fixed and re-verified.
- Note: Phase 7 (parallel review gates) is folded into the mandatory plan-review cycle on the implementation plan (next skill); wiring check is inline above (no gaps). gh CLI rate-limited — the plan comment to the issue is deferred to the PR description (which carries the same content).
