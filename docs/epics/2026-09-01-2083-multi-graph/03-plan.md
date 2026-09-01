---
title: "Epic #2083 Multi-Graph — Implementation Plan"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-01
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-hosted-platform
---

# Epic #2083 Implementation Plan — Pro-tier multi-graph teams: per-graph API keys + graph provisioning

**Inputs:** `00-align.md` (PROCEED), `docs/research/2026-09-01-2083-multi-graph/research.md` (findings 2026-09-01), `01-scope.md` (approved 2026-09-01, 11 in / 5 out, 9 high-level E2E), test-design issue **#2094** (integration-surface map, 13 surfaces), UX Design Decisions (recorded below).

## UX Design Decisions (recorded 2026-09-01, owner-approved)

| # | Decision | Owner Choice | Rationale |
|---|---|---|---|
| 1 | Dashboard graph management | **Enhance existing Graphs page** (not build) — create/list + quota check already exist (E5/E7); add per-graph key management, lifecycle, one-time reveal modal, quota meter | Existing surface verified in dashboard main.jsx + hosted_api.py |
| 2 | Graph-count visibility | **Persistent count + meter + cap-reject** — "3 graphs · ∞ cap" on Graphs page (free/solo: used/total; pro/team: usage with ∞), clear quota error at the cap with upgrade CTA; **the 80% soft-warning band is DEFERRED (unreachable at free=1/solo=2; lands with a finite pro/team cap if Gate #2 sets one)** | saasui.design/Cloudflare/Stripe precedent set (warn band applied when a finite cap exists) |
| 3 | Key reveal | **One-time reveal modal with copy button** (Stripe/AWS contract) + **multiple keys per graph, scoped** | Owner confirmed option 1 + expanded requirements |
| 4 | Tier gate (free/solo) | **Locked state + upgrade CTA** on Graphs page | Visible gating converts; error-only frustrates |
| 5 | Key model | **Unified scoped keys** — single `tk_` type, permission matrix, all scopes default OFF (see §5.4) | Owner-approved after pros/cons research (Neon/OpenAI split rejected) |
| 6 | `session_recording` scoping | **Per-graph override + team default** — a graph may override its team's recording setting; default stays ON (#1927 ToS-covered contract preserved); per #2082 Q5 leaning | Scope item 9 acceptance: decision recorded in plan |

---

# 1. User Journeys

Personas: **Dev** (developer building an app on Tortoise — the app_builder segment), **DevOps** (platform engineer managing a Tortoise team), **TeamOwner** (pro/team tier admin). Free/Solo users appear in the tier-gate journey as non-pro personas.

### J1 — Dev provisions a graph for a new end-customer
**Entry:** Dev has a pro-tier team + an existing key with `graphs:create`.
**Flow:** Dev calls `POST /v1/teams/{team_id}/graphs {name: "acme-prod"}` → response 201 with graph metadata + per-graph key (plaintext, once) → Dev wires the key into the customer's app config → app writes/reads the customer's memory through the graph key.
**Exit:** New end-customer memory graph live, isolated, key-accessible. Verification: E2E-1.

### J2 — Dev rotates/revokes a leaked customer key
**Entry:** A customer graph key is suspected compromised.
**Flow:** Dev calls the key-management endpoint with a `keys:manage`-scoped key → mints a replacement key for the same graph (same scopes) → swaps it in the customer's app → revokes the old key (immediate 401 on next use; append-only audit).
**Exit:** Compromised credential dead, customer app uninterrupted (overlap window). Verification: E2E-7, E2E-9.

### J3 — TeamOwner manages team + graphs from the dashboard
**Entry:** Pro team owner signs into the dashboard.
**Flow:** Owner opens the Graphs tab → sees graph list (default first) + graph count vs plan limit (∞ for pro) → creates a graph via the form → reveal modal shows the new graph's key once (copy button) → manages per-graph keys (list/revoke) → renames team from the team tab.
**Exit:** Team state fully manageable from the UI. Verification: E2E-1, E2E-8, E2E-9, ux-verification.

### J4 — Free user hits the tier gate
**Entry:** Free-tier user opens the Graphs tab (max 1 graph).
**Flow:** User sees the graph list (default graph only) with the create action locked + "Upgrade to Pro for multi-graph" CTA → API attempts to create a 2nd graph return a tier error.
**Exit:** Clear upgrade path; no silent cap. Verification: E2E-3, E2E-5.

### J5 — Existing team keeps working untouched (migration)
**Entry:** Team that predates the epic, existing team key (`tkm_` legacy class), data in the default graph.
**Flow:** Team's existing key reads/writes the default graph with no changes; the default graph shows as graph 0 (primary) and cannot be deleted; the team can optionally mint scoped `tk_` keys.
**Exit:** Zero-migration continuity. Verification: E2E-5.

### J6 — Agent session capture lands in the right graph
**Entry:** A customer app runs a session-hook flow (context + session capture) with a per-graph key.
**Flow:** `GET /v1/context` returns graph-scoped memory digest; `POST /v1/sessions` points session data into the graph (respecting per-graph `session_recording` inherited from team default).
**Exit:** Cross-customer context never bleeds. Verification: E2E-6.

### J7 — DevOps manages the team programmatically via a scoped key
**Entry:** DevOps holds a key with `team:manage` + `keys:manage` + `graphs:create` scopes.
**Flow:** DevOps renames the team via API, provisions a graph for a new team project, mints/revokes keys for it — all from CI/scripts, no dashboard.
**Exit:** Full API-driven team administration (the "team key = scoped key" model, no separate key type). Verification: E2E-9.

### J8 — End-customer runtime uses the graph key
**Entry:** A customer's app holds the per-graph key for its own memory graph.
**Flow:** The app reads/writes its graph through the key; a revoked key 401s mid-flight; a read-only key is denied writes; cross-graph access attempts are denied (app layer + data layer).
**Exit:** The customer's memory is isolated and key-bound — the isolation story the developer sells. Verification: E2E-2, E2E-7.

**Edge cases covered:** empty graph list (J3 empty state exists), graph-name collision (409), suspended team (create blocked, #1853 parity — data plane also 403s, see W4), revoked key mid-request (401), quota at cap (cap-reject 409 — v1 ships no 80% warn band), deleted graph with active keys (keys revoked with graph), default graph delete attempt (blocked — would strand legacy keys), per-graph key panel loading/empty/error states, reveal-modal failure modes (reveal consumed → re-mint required; clipboard failure → key still in API response, never re-revealed), create-form error surfacing (402/409 rendered inline on the Graphs tab), quota meter states (count vs cap/∞; no warn band in v1).

---

# 2. Workflows

System-level flows, handoff points, failure modes.

### W1 — Graph provisioning (key-driven mint)
```
caller key (graphs:create scope, NOT minted-by-provisioning)
  → resolve_api_key(token) → team_id + key + scopes + tier + limits
  → tier gate: pro+ (free/solo reject 402/409)
  → quota gate: graph_count < max_graphs (atomic count-then-insert; cap-reject 409 + X-Graph-Quota)
  → key-cap gate: minted key counts against max_api_keys — key-mint failure ROLLS BACK the graph (no graph-without-key, no orphan)
  → registry: graphs row (Supabase) / Graph node (registry mode) + namespace team_{team_id}_{gid}
  → mint per-graph key: scope set = caller-chosen graph scopes minus escalation scopes
       (graphs:create / graphs:delete / keys:manage are NEVER inherited — fixed child policy)
  → minted keys carry delegation_depth 0 (SPKI deleg=0): they cannot mint. Only
       keys WITH the keys:manage scope mint (their children are deleg=0) — the
       mint path never creates a deleg>0 key (E2E-4)
  → FalkorDB ACL user (defense-in-depth): ~tenant_<gid> +GRAPH.QUERY +GRAPH.RO_QUERY +PING
  → 201 {graph, key_metadata, key_plaintext (once)}
Handoff: dashboard J3 (reveal modal) | Dev J1 (API response).
Failure modes: quota cap (409 + X-Graph-Quota header — no warn band in v1), name collision (409), non-pro tier (402), minted-key provisioning attempt (403), ACL user creation failure (rollback graph+key — no orphan graphs, #1686/#1748 invariants).
```

### W2 — Key lifecycle (mint / scope-edit / rotate / revoke)
```
keys:manage-scoped key (or owner session user)
  → mint: create key row (hash-only), scopes fixed at mint
  → edit scopes: shrink anytime; expand = revoke+recreate (audit trail)
  → rotate: mint replacement → overlap window → revoke old
  → revoke: revoked_at set → resolve_api_key rejects (instant 401) → audit append
Handoff: dashboard per-graph key panel | API.
Failure modes: minting beyond max_api_keys (409), key minted with escalation scopes (403 — child policy), revoke of last key on a graph (allowed — graph outlives keys), concurrent mint (unique lookup_hash).
```

### W3 — Graph lifecycle (list / delete) — v1: no archive
```
owner/admin (session) or key with graphs:create/graphs:delete
  → list: default-first (graph 0 = primary — display label; status enum is active|deleted)
  → delete: custom graphs only; soft-delete tombstone (status='deleted') + cascade-revoke keys (401)
      + drop ACL user + free quota slot (E2E-8); name reusable via partial unique index
      graph 0 delete is BLOCKED (Neon non-deletable default precedent)
Handoff: dashboard Graphs tab (list exists; delete new) | API.
Failure modes: default-graph delete (403), delete with active keys (cascade, documented),
concurrent provision vs delete AND provision-vs-provision at the cap (atomic count-then-insert
under the same lock/transaction — no oversubscription), suspended team (blocked).
```

### W4 — Request path with per-graph tenancy (data plane)
```
per-graph key → resolve_api_key → team_id + graph_id + graph_namespace + scopes
  → scope check per surface (ask/analyze/search/MCP/sessions/context)
  → app-layer ownership check on select_graph (authoritative — works with ACL OFF)
  → graph namespace selected; writes/reads land in the graph only
  → team-wide/legacy key (graph_id NULL) → default graph (back-compat)
  → suspended team: 403 on BOTH control and data plane (existing get_current_team /
       key-resolution contract, #1853/#1828 parity); the /v1/team/alerts appeal flow stays open
Failure modes: cross-graph attempt (401/403 at app layer + NOPERM at ACL layer), read-only key write (403), deleted graph key (401), legacy key on multi-graph team (default graph only), suspended team (403 everywhere except alerts).
```

### W5 — Quota + billing surface
```
provision/create → count graphs from SOR (Supabase graphs table / registry Graph node)
  → 80% threshold: DEFERRED in v1 (unreachable at free=1/solo=2) — lands with a finite pro/team cap if Gate #2 sets one
  → cap: hard reject 409 + X-Graph-Quota header with upgrade CTA (existing UX-D4 pattern)
  → delete (v1 lifecycle: active/delete only — no archive) releases slots (E2E-8)
  → count-then-insert is ATOMIC (single lock/transaction covers provision-vs-provision
       at the cap AND provision-vs-delete — no oversubscription race)
Failure modes: dual-mode count drift (Supabase vs registry — shared seam test), tier downgrade with N>limit (warn, no silent delete).
```

---

**Review gate (Sub-steps 1–2):** journeys cover all 11 in-scope items (map: J1→provisioning, J2→key lifecycle, J3→dashboard, J4→tier gate, J5→migration, J6→delivery-shape; workflows W1–W5 cover data model/isolation/quota); personas appropriate (Dev/DevOps/TeamOwner/free-solo); edge cases enumerated per journey and workflow failure modes documented.

---

# 3. Prototype (markdown wireframe — dashboard Graphs-page delta)

The epic's only GUI surface is the existing Graphs tab (verified in `website/apps/dashboard/src/main.jsx` — create form + Name/Kind/Graph ID table + `max_graphs` card exist). The prototype below is the DELTA, per UX decisions 1-4. Full HTML prototype-review is not warranted (existing-app enhancement, decisions recorded); a prototype-review HTML would be produced by the child issue if the implementer needs it.

```
┌─ Dashboard: Graphs tab (pro+ team) ────────────────────────────────────────┐
│  Graphs                              [3 graphs · ∞ cap]  (pro: unlimited)   │
│  (no soft-warning banner in v1 — 80% warn band deferred; cap-reject only) │
│                                                                           │
│  [+ Create graph]  (name input — existing form, gains error inline:       │
│   409 duplicate / 402 tier-or-cap with upgrade CTA — existing UX-D4)      │
│                                                                           │
│  Name        Kind      Graph ID      Status     Keys      Actions         │
│  default     default   —            primary    —         (locked)         │
│  acme-prod   custom    g_ab12…      active     2 keys    [Keys] [Delete] │
│  (deleted graphs drop out of graph_list — tombstone persists, E2E-8)      │
│                                                                           │
│  ── Per-graph key panel (click [Keys]) ────────────────────────────────   │
│  Keys for acme-prod              [+ New key]                              │
│  key_1 · tk_…  · scopes: read,write        [Revoke]                      │
│  key_2 · tk_…  · scopes: read              [Revoke]                      │
│                                                                           │
│  ── One-time reveal modal (after create or + New key) ────────────────    │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  Your new API key — shown once                                      │  │
│  │  tk_live_xxxxxxxxxxxxxxxx        [Copy]  ⚠ You won't see this again │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────┘

Free/solo tier (J4): create button locked with 🔒 + "Upgrade to Pro for
multi-graph" CTA; graph list still shows their 1-2 graphs; meter shows
"1 of 1 used" (free) / "2 of 2" (solo).
```

> **DECIDED at Human Gate #2 (2026-09-01, owner): UNLIMITED.** Pro/team graph caps stay unlimited (pricing.json as-is, `max_graphs=null`); caps enforced for free=1/solo=2 only; no 80% warn band in v1 (dormant until a finite cap is ever set). The wireframe + §4.1 stand as-is.

States covered: loading (existing shimmer), empty (existing "No graphs yet"), error (terminal '—' pattern exists), banner n/a in v1 (warn band deferred), meter states (count vs cap/∞), reveal modal open/closed, key panel empty/loading/error, deleted row dimmed.

**Review gate (Sub-step 3):** wireframe matches J3/J4; all states represented (loading/empty/error/banner/meter/reveal); existing component patterns reused (cards, inline-form, table — no new design system).

---

# 4. Data Model

> **Findings date:** 2026-09-01. **Data Model Research Notes:** justified skip — the brief's Tech Stack Research section covers the Supabase control-plane gap + migrations 0006/0007 at sufficient granularity (verified column sets, RLS GUC pattern, #765 zero-registry-writes contract); RLS for the new `graphs` table follows the established 0002/0006 tenant-GUC pattern (not novel). Recorded schema decisions: scopes as JSONB allowlist on api_keys (Stripe-style, mutable via `keys:manage`); graph-count source = `graphs` table in Supabase mode, registry `Graph` nodes in selfhost (shared seam `graph_metadata`/`graph_list`).

## 4.1 Supabase mode (hosted) — new migration

```sql
-- graphs table (mirrors the registry Graph node; the hosted SOR for team→graph 1:N)
CREATE TABLE public.graphs (
  id          text PRIMARY KEY,          -- g_<16hex> — deterministic per (team_id, name) in Supabase mode (#765 zero-registry-writes)
  team_id     text NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  name        text NOT NULL,
  kind        text NOT NULL DEFAULT 'custom',   -- 'default' | 'custom'
  namespace   text NOT NULL,             -- team_{team_id}_{gid}
  status      text NOT NULL DEFAULT 'active',   -- 'active' | 'deleted' (v1: no archive — one-way state nothing consumes; delete = soft tombstone)
  recording   boolean,                   -- session_recording override; NULL = inherit team default (#1927 default-ON preserved)
  created_at  timestamptz NOT NULL DEFAULT now()
);
-- name reuse after soft-delete: partial unique index (tombstones don't squat names)
CREATE UNIQUE INDEX IF NOT EXISTS uq_graphs_team_name_active
  ON public.graphs (team_id, name) WHERE status <> 'deleted';

-- api_keys: graph scope + scopes (allowlist) + delegation lineage
ALTER TABLE public.api_keys
  ADD COLUMN graph_id text REFERENCES public.graphs(id) ON DELETE CASCADE,   -- NULL = team-wide key → default graph; CASCADE = keys die with the graph
  ADD COLUMN scopes  jsonb NOT NULL DEFAULT '{}'::jsonb,                    -- {"graphs":["read","write"],"team":["manage"]} — default {} = no scopes
  ADD COLUMN created_by_key_id text REFERENCES public.api_keys(id) ON DELETE SET NULL,  -- NULL = minted by owner session/bootstrap
  ADD COLUMN delegation_depth integer;                                                  -- 0 = minted (deleg=0, cannot mint); NULL = owner-minted

-- DB-invariant for the approved key model: MINTED keys (delegation_depth = 0) can NEVER hold
-- escalation scopes, graph-bound OR team-wide. Only owner-minted keys (delegation_depth IS NULL) may.
ALTER TABLE public.api_keys
  ADD CONSTRAINT chk_minted_key_no_escalation
  CHECK (delegation_depth IS NULL OR NOT (scopes ?| array['graphs:create','graphs:delete','keys:manage']))
```

- **Semantics:** `scopes` = allowlist (all-off default — Stripe rule). `graphs:write` implies `graphs:read` at enforcement. `graph_id` NULL + escalation scopes = team-management key. `graph_id` set = graph-bound key. **Delegation: `delegation_depth` 0 = minted (cannot hold escalation scopes — DB CHECK `chk_minted_key_no_escalation` covers graph-bound AND team-wide minted keys); NULL = owner-minted (may hold escalation scopes).**
- **RLS:** service role (backend) full access; tenant-scoped reads via the established GUC pattern (`app.current_team_id` set post-key-resolution; unset GUC = 0 rows deny-by-default) — mirrors 0006/0007.
- **Integrity:** default-graph guard is CODE-enforced in both modes (Supabase: the default is derived from `teams.graph_name` — no row exists to constrain; registry: `kind='default'` node guarded at the lifecycle layer) — no delete of the default graph. Graph delete = soft-delete (`status='deleted'` tombstone) + code-side cascade (revoke keys, drop ACL user, free quota slot); `ON DELETE CASCADE` covers the team-delete cascade + hard cleanup; the FK never silently un-scopes a key (no SET NULL). Partial unique index allows name reuse after delete.
- **Quota:** `graph_count(team_id)` = 1 (the default graph — always present: derived in Supabase mode, `kind='default'` node in registry mode) + `count(*) WHERE kind='custom' AND status='active'`. **Delete frees the slot (deleted rows excluded); v1 has no archive.** The default ALWAYS occupies slot 1 (free=1 → default only; solo=2 → default + 1 custom) — meter and cap use this ONE definition in both modes. **Caps: free=1, solo=2 finite; pro/team unlimited (DECIDED at Human Gate #2 2026-09-01 — pricing.json as-is, no change)**. Cap checked pre-insert; atomic count-then-insert (W5). **v1 ships cap-reject (409 + X-Graph-Quota header) only — the 80% soft-warning band is deferred (unreachable at free=1/solo=2; stays dormant unless a finite pro/team cap is ever set).**
- **Migration path:** existing teams keep `teams.graph_name` as the derived default (graph 0) — NO backfill row needed for the default; new graphs get rows. Reversible: drop tables/columns restores prior behavior (graph_metadata derives default only).

## 4.2 Registry mode (selfhost) — FalkorDB registry

- `Graph` node already exists (sdk `_graph_create`: id, team_id, name, kind, namespace, created_at) — add `status` + `recording` properties (default/back-compat; no archive status in v1).
- `APIKey` node: add `graph_id`, `scopes`, `created_by_key_id`, `delegation_depth` properties (nullable graph_id = team-wide).
- **Registry↔Supabase seam:** both modes emit the same registry-shaped row `{graph_id, team_id, name, kind, namespace, status}` — the existing `graph_metadata`/`graph_list` seam extends with status/recording. Shared seam test (test-design #2094 surface 10).

## 4.3 Entity summary

| Entity | Mode(s) | Key change |
|---|---|---|
| Graph | Supabase table + registry node | status, recording; default-graph guard |
| APIKey | Supabase table + registry node | +graph_id, +scopes (JSONB/property) |
| Team | both | max_graphs already present; no change (quota now enforced) |
| Membership | both | no change |
| FalkorDB ACL user | data plane | 1 user per graph (`~tenant_<gid> +GRAPH.QUERY +GRAPH.RO_QUERY +PING`) |

**Review gate (Sub-step 4):** data model supports W1–W5 (provision, keys, lifecycle, tenancy, quota); RLS covers tenant reads + service-role; integrity constraints at DB level (unique name, FK cascade, default guard); research-check satisfied (justified skip + schema decisions recorded).

---

# 5. Architecture

> **Findings date:** 2026-09-01. **Architecture Research Notes:** justified skip — the brief's Tech Stack Research section covers the architecture questions at sufficient granularity (FalkorDB ACL recipe + leaks, dual-mode seam env-gated `TORTOISE_CONTROL_PLANE`, tenancy touch-map ~30 `_make_sdk` sites, provisioning/migration patterns, one-level-deep by construction). Architecture decisions below follow the brief + owner-approved key model.

## 5.1 Target-state components

```
                    ┌─────────────────── CONTROL PLANE ───────────────────┐
   Dashboard (React) │  Supabase (hosted): teams·api_keys·graphs·memberships│
   + CLI/SDK callers │    └─ RLS: tenant GUC (app.current_team_id)          │
        │            │  Registry (selfhost): Team/APIKey/Graph nodes       │
        │            │    └─ shared seam: resolve_api_key·graph_metadata·  │
        ▼            │       graph_list·graph_count (TORTOISE_CONTROL_     │
 ┌─────────────────┐ │       PLANE=supabase|registry)                      │
 │  hosted_api      │ │                                                    │
 │  (FastAPI, /v1/*)│─┴─▶ AUTH: resolve_api_key → {team, graph_id, scopes, │
 │  ask/analyze/    │     limits} — the single tenancy resolution point    │
 │  search/MCP/     │     + per-request scope check (allowlist)            │
 │  sessions/context│                                                     │
 └───────┬─────────┘                                                     │
         │ graph selection: _make_sdk(namespace=graph_namespace)          │
         ▼            (ownership check BEFORE select_graph — app layer    │
 ┌─────────────────┐  is AUTHORITATIVE; passes with ACL OFF)              │
 │  FalkorDB        │                                                     │
 │  multi-graph     │── per-graph ACL user (defense-in-depth):            │
 │  team_{tid}_{gid}│   ~tenant_<gid> +GRAPH.QUERY +GRAPH.RO_QUERY +PING  │
 │  (+ default      │   deny GRAPH.LIST/KEYS/SCAN/CONFIG, secure default  │
 │   team_{tid})    │   user, aclfile+ACL SAVE (selfhost); custom ACL      │
 └─────────────────┘   strings on hosted cloud                             │
```

## 5.2 Provisioning service (mint flow — W1)

New endpoint + service function:
`POST /v1/teams/{team_id}/graphs` → checks (tier ≥ pro → quota via atomic count-then-insert → team non-suspended) → writes graphs row/node → mints key (scopes = caller-chosen ∩ child policy) → creates ACL user (selfhost) → 201 with one-time reveal. **Rollback contract:** any failure after graphs write → delete graph + key (no orphan graphs — #1686/#1748 invariants; audit event on every mint).

## 5.3 Tenancy enforcement (the spine — W4)

- `resolve_api_key` returns `graph_id` + `graph_namespace` + `scopes` (new).
- A per-request middleware reads the resolved scope once and pushes it into the query path (authz as pre-filter, #2082 principle 7 — never post-filter).
- All ~30 `_make_sdk(namespace=team_id)` call sites become `_make_sdk(namespace=<resolved graph_namespace>)`; ownership check on `select_graph`.
- MCP: `_current_team_id` ContextVar extends to carry graph scope (per-graph keys in MCP tools).
- Legacy/team-wide keys (graph_id NULL): resolve to the default graph (back-compat).

## 5.4 Key permission model (owner-approved, 2026-09-01)

- Single key type `tk_` (legacy `tkm_` = full-access class, kept valid, dashboard-marked "legacy").
- Scopes (allowlist, all-off default): `graphs:read`, `graphs:write` (implies read), `graphs:create`, `graphs:delete` (separate — irreversible), `team:manage`, `keys:manage`.
- Enforcement: GET/HEAD→read, POST/PUT/PATCH/DELETE→write + operation-level classification (write-implies-read); ownership guard on DELETE (key type + scope, not path shape); a key WITH `keys:manage` may mint (children deleg=0, `created_by_key_id` recorded); minted keys never inherit escalation scopes — **DB CHECK `chk_minted_key_no_escalation` covers graph-bound AND team-wide minted keys (delegation_depth=0)**.
- Lifecycle: mint (scopes fixed, reveal once, prefix; owner-minted keys have `delegation_depth` NULL) → use (per-key `last_used_at`; full request logs DEFERRED to a later surface — v1 = last_used_at only) → rotate (create→migrate→revoke; revoke-first on compromise) → revoke (immediate, permanent, audit).
- Scope mutability: shrink anytime; expand = revoke+recreate (audit trail).

## 5.5 Failure modes & resilience

- ACL user creation failure → rollback mint (no orphan graph); ACL layer down → app-layer spine still enforces (defense-in-depth, not single point of failure).
- FalkorDB GRAPH.LIST leak (#2652, tracked upstream): deny `+GRAPH.LIST` on all ACL users; do not assert on it in tests.
- Dual-mode drift → shared seam tests (surface 10).
- Quota atomicity → count-then-insert under one lock/transaction (W5).
- Suspended team → 403 both planes (existing contract), alerts appeal open.

**Review gate (Sub-steps 3–5):** prototype matches journeys; data model supports all workflows with RLS + constraints; architecture boundaries clean (control plane / data plane / ACL layer), interfaces named, failure modes addressed; research-check satisfied (justified skips with citations).

---

# 6. Interfaces (contract-first)

> **Findings date:** 2026-09-01. Light research hook: no gap — the brief's Workflow Pattern Research covers the mint/response contract (201 + one-time reveal, 409 LimitExceeded, prefix scheme) and revocation; versioning follows the existing /v1/* convention (additive fields only — no breaking change to team-scoped callers; per #2080 D3 the contract stays scope-by-team compatible).

## 6.1 resolve_api_key contract (extended — the tenancy resolution point)

```
Input:  token (opaque — legacy tt_/tkm_ + new tk_ prefixes)
Output: { team_id, key_id, tier, limits: {max_users, max_graphs, max_api_keys, max_points, max_sessions},
         graph_id: str|null,        // null = team-wide key → default graph
         graph_namespace: str|null, // team_{team_id}_{gid} or team_{team_id}
         scopes: { graphs: [read|write|create|delete], team: [manage], keys: [manage] },  // allowlist; write implies read
         legacy_full_access: bool,  // tkm_ class: all data-plane ops on the default graph + legacy management
         delegation_depth: int|null,// 0 = minted (cannot escalate); null = owner-minted
         key_prefix, created_via, created_by }
Errors:  None (401 on revoked/unknown) | raise on control-plane failure (fail-closed, #1096 degrade-safe additive)
```

## 6.2 Provisioning + lifecycle

### POST /v1/teams/{team_id}/graphs + POST /v1/graphs (ONE provisioning service, two auth faces)
```
Both endpoints route through the SAME service function — one tier gate, one quota gate, one mint,
one rollback, one response envelope. POST /v1/graphs is a thin session-authed alias for the
existing dashboard caller; POST /v1/teams/{team_id}/graphs is the key-driven path.
Auth:   POST /v1/teams/{team_id}/graphs → key with graphs:create scope
        POST /v1/graphs → owner/admin session user + membership (existing)
Body:   { "name": "acme-prod", "scopes": ["graphs:read","graphs:write"] }   // scopes = requested key scopes (default read)
201:    { "graph": {"id":"g_…","name":"acme-prod","kind":"custom","namespace":"team_…","status":"active","created_at":"…"},
         "key":  {"id":"key_…","graph_id":"g_…","scopes":[…],"created_at":"…"},
         "key_plaintext": "tk_live_…", "revealed_once": true }
Errors: 401 revoked/unknown · 403 no graphs:create scope · 403 suspended team · 402 tier below pro ·
        404 unknown team · 409 name collision · 409 quota cap (X-Graph-Quota header) ·
        409 max_api_keys reached (atomic: graph mint ROLLS BACK if the key mint fails — no graph-without-key) ·
        422 missing/invalid name
Notes:  plaintext appears ONCE (hash-only stored) · minted key scopes = requested ∩ child policy
        (escalation stripped at mint; DB CHECK enforces) · no orphan on failure (rollback) ·
        atomic count-then-insert quota gate · v1 ships cap-reject only (no 80% warn band)
```

### GET /v1/graphs (EXTENDED)
```
200: list default-first; rows gain status + key_count:
[{ "graph_id", "name", "kind", "namespace", "status", "key_count" }]   // point_count dropped (no consumer; a per-row data-plane count on every list)
```

### DELETE /v1/graphs/{graph_id} (NEW)
```
Auth:   key with graphs:delete or owner/admin session
204:    success — soft-delete tombstone (status='deleted') + cascade: revoke graph keys (401 on next use),
        drop the FalkorDB ACL user, free the quota slot; partial unique index allows name reuse
Errors: 403 default-graph delete (code guard) · 403 missing scope · 404 unknown graph · 401/403 suspended team
```

## 6.3 Key management

### POST /v1/teams/{team_id}/keys (NEW — mint a key; graph-bound or team-wide)
```
Auth:   key with keys:manage (or owner/admin session)
Body:   { "graph_id"?: "g_…", "scopes": ["graphs:read"], "name"?: "ci-prod" }
201:    { "key": {"id","graph_id","scopes","name","created_at","last_used_at":null},
         "key_plaintext": "tk_live_…", "revealed_once": true }
Errors: 401/403 no keys:manage · 403 escalation scopes on MINTED key (child policy: any minted key — graph-bound
        OR team-wide — cannot carry escalation; DB CHECK `chk_minted_key_no_escalation` enforces) ·
        404 unknown graph/team · 409 max_api_keys · 422 invalid scope
```

### GET /v1/teams/{team_id}/keys?graph_id=… + PATCH /v1/teams/{team_id}/keys/{key_id} + DELETE /v1/teams/{team_id}/keys/{key_id}
```
GET:     list keys (per-key metadata + last_used_at; full per-request logs DEFERRED) — auth: keys:manage or membership
PATCH:   shrink scopes only (expand = revoke+recreate) — auth: keys:manage
DELETE:  revoke → immediate 401 on next use; audit event — auth: keys:manage
Errors:  401 revoked/unknown · 403 wrong scope · 404 · 422 expand-attempt
```

### PATCH /v1/graphs/{graph_id} (NEW — per-graph settings incl. session_recording override)
```
Auth:   owner/admin session user or key with team:manage
Body:   { "recording": true|false|null }   // null = inherit team default (#1927 default-ON preserved)
200:    { "graph_id", "recording": true|false|null }   // null = inherit team default; default graph settable too (recording is per-graph, incl. graph 0)
Errors: 401/403 (scope) · 404 unknown graph
```

## 6.4 Data plane tenancy (per-graph keys)

```
ask/analyze/search/MCP/sessions/context: key resolves graph_id + graph_namespace + scopes;
  scope pre-filter (authorized scopes → constrained query — #2082 principle 7, never post-filter);
  ownership check before select_graph; read-only key → write op 403.
/v1/context (GET): graph-scoped session_context payload (graph key → its graph; team key → default)
POST /v1/sessions: session points land in the key's graph; session_recording resolved per-graph
  override → team default (#1927 default-ON); install-probe path unchanged (team-level gate)
Versioning: additive only; legacy tkm_ keys resolve to default graph (back-compat);
  no header/param breaking change — per #2080 D3 the tenancy contract stays scope-by-team compatible
```

## 6.5 Error contract (canonical body)

```
401 {detail:"Invalid or revoked API key"}             — resolve failure
403 {detail:"Insufficient scope: graphs:write"}       — scope/cross-graph/tier-mismatch on data ops
402 {detail:"Upgrade to Pro for multi-graph graphs"}  — tier gate (existing UX-D4 shape)
409 {detail:"Graph limit reached (used 2 of 2)"}      — quota cap (with X-Graph-Quota header)
409 {detail:"A graph named 'acme-prod' exists"}       — name collision
404 {detail:"Unknown team/graph"}                     — not found
422 {detail:"invalid_question|…"}                     — validation (existing shape)
```

---

# 7. Detailed E2E Test Cases

Setup anchors (per test-design #2094): both control-plane modes; live FalkorDB (docker) for data-layer assertions; the shared seam (`TORTOISE_CONTROL_PLANE=supabase|registry`) is the dual-mode toggle. Fixtures: pro team + owner session + minted keys; free/solo teams for tier tests; default graph present on every team. **State isolation: every E2E uses a DEDICATED team fixture (unique per test) or explicit teardown (delete graphs + revoke keys + drop ACL users at the end of each scenario); quota-state tests (E2E-3, E2E-7) never share fixture teams — parallel/sequential runs cannot collide.**

### E2E-1: Provision a graph with a per-graph key
- Setup: pro team, key K1 with `graphs:create`.
- Steps: `POST /v1/teams/{id}/graphs {name:"acme-prod"}` with K1 → 201; assert response contains graph.id, namespace `team_{tid}_g_{…}`, `key_plaintext` (tk_live_…), `revealed_once: true`; assert key row stores hash only (no plaintext).
- Assert: write a point + read it back via the minted key; second call with same body → 409; the minted key's scopes exclude escalation scopes; **ACL user asserted by CONFIG INSPECTION only (the ACL user exists with the configured permission set — `~tenant_<gid>` + GRAPH.QUERY/RO_QUERY/PING, no GRAPH.LIST/KEYS/SCAN/CONFIG; do NOT trigger the leak at runtime — GRAPH.LIST behavior is version-dependent (#2652), asserting on it would be flaky; the §5.5 stance holds).**
- Surfaces (test-design #): 1 (graphs table), 4 (mint), 6 (ACL user created + permission set asserted).

### E2E-2: Per-graph key isolation — cross-graph denial
- Setup: team with graphs A+B, keyA bound to A (read+write).
- Steps: keyA attempts ask/analyze/search/MCP-tool/Direct-SDK against graph B → each denied (401/403) at the app layer. **Sessions/context derive the graph from the key (no request-side override surface is built — §6.4); the cross-graph assertion for those surfaces = the data-layer probe under keyA's session (`db.select_graph('team_{tid}_{B}')` → NOPERM) plus a negative-scope check (keyA has no access to B's namespace via any path).**
- Data layer: with ACL OFF (proves app spine) the same attempts are denied; with ACL ON a graph-A-scoped FalkorDB credential gets NOPERM on B.
- Assert: zero cross-graph reads/writes across all 7 surfaces.
- Surfaces: 3, 6, 7, 8, 9.

### E2E-3: Tier gate — provisioning is pro+ (both modes)
- Setup: free team (max 1) + solo team (max 2), each with a team key; dedicated fixtures per test (state isolation).
- Steps: free team (at 1/1 — default fills slot 1) attempts any custom graph → **402 (tier gate checked FIRST per W1 ordering — pinned, not any-4xx; assert the upgrade-CTA error body)**; solo provisions its 1st custom (2 of 2) → allowed; solo attempts a 3rd → 409 quota (X-Graph-Quota header).
- **Tier downgrade scenario: pro team with 3 custom graphs downgrades to solo (max 2) → existing graphs remain readable/writable (no silent delete), new provisioning rejected (409), dashboard warns on the over-limit state.**
- Assert: error bodies carry the upgrade CTA shape; header present on cap; downgrade never deletes data.
- Surfaces: 1, 11 (API-side; dashboard banner UI covered by the surface-12 ux/e2e layer, not here).

### E2E-4: One-level-deep — minted keys cannot provision
- Setup: key K2 minted by provisioning (deleg=0).
- Steps: K2 calls graph-create, graph-delete, key-mint, key-revoke → all 403.
- Assert: DB CHECK constraint blocks escalation scopes on graph-bound keys (attempt direct INSERT into api_keys → constraint violation) — surfaces 2 (api_keys) + 4 (mint) + 11 (quota not exercised here).

### E2E-5: Existing-team migration — default graph keeps working
- Setup: pre-epic team with a `tkm_` legacy key + data in default graph (both modes).
- Steps: legacy key reads/writes default graph; `GET /v1/graphs` lists default first; delete of default → 403.
- Assert: zero migration actions required; legacy key scopes = full-access class; default occupies slot 1 in graph_count.
- Surfaces: 3, 5, 10, 13.

### E2E-6: Delivery-shape tenancy — context + sessions resolve per graph
- Setup: per-graph key for graph A (read+write) + graph B; **graph A's `session_recording` override set to false via `PATCH /v1/graphs/{A} {recording:false}` (interface §6.3) — the authorized principal is the OWNER SESSION USER (team:manage or session, per §6.3), named in the fixture.**
- Steps: `GET /v1/context` with keyA → graph-A-scoped digest (points from B absent); `POST /v1/sessions` with keyA → session points in graph A; graph-B key sees no session bleed.
- **Recording override assertion (measurable): after posting a session to graph A (recording=false override, team default ON), assert NO Session node appears in graph A; on a recording=true graph assert the Session node appears. This proves the override is honored (absence/presence of the Session node, not a flag read).**
- Assert: cross-graph context/session bleed = 0; override honored via Session-node absence/presence.
- Surfaces: 8, 9.

### E2E-7: Quota + revocation lifecycle (both modes; finite caps)
- Setup: dedicated solo team at 2 of 2 (default + 1 custom), one key to revoke; **billing observation point: Stripe test-mode + test clock IF already wired in the test env, else the billing seam/mock the quota gate already calls (the no-charge assertion must not depend on new infra — gate it on existing support).**
- Steps: provision a 3rd → 409 (assert `X-Graph-Quota` header on the cap; **the 80% warn band is DEFERRED in v1 — no X-Graph-Quota-Warn header; the soft-warning banner is a surface-12 dashboard concern, only when a finite cap ≥3 exists (Gate #2)**); revoke a key → immediate 401 on every surface; audit event recorded.
- **No-charge assertion: assert zero Stripe events/invoice lines captured for the rejected attempt (billing observation point) — the quota reject happens before any charge.**
- Assert: quota reject BEFORE any charge; revoked key dead across all 7 surfaces; audit trail complete.
- Surfaces: 3, 7, 11 (key-resolution + quota; graph-lifecycle/select_graph ownership covered in E2E-8/E2E-2).

### E2E-8: Graph lifecycle — list, delete, quota release + name reuse
- Setup: pro team, 2 custom graphs (active), per-graph key on one.
- Steps: `GET /v1/graphs` → default first + both customs (status, key_count); delete graph 1 → 204, key 401 on next use, quota slot released (provision succeeds); **recreate the SAME NAME after delete → 201 (partial unique index — tombstones don't squat names); default delete → 403.**
- Assert: lifecycle transitions + quota release + name reuse verified; no orphan KEY or ACL user (the graph's tombstone row persists with status='deleted' — soft-delete is the design; keys revoked + ACL user dropped, verified absent).
- Surfaces: 1, 5, 6, 11.

### E2E-9: Key scopes + legacy keys
- Setup: key T with graphs:create+delete+team:manage+keys:manage; read-only key R (graphs:read) for graph A; legacy `tkm_` key L; **plus an OWNER-SESSION-minted create-only team-wide key C (graphs:create, no delete) — the create-without-delete assertion needs a key that has create but not delete.**
- Steps: R writes → 403, reads → 200; T provisions + deletes a graph, renames team, mints + revokes another key; C creates a graph but delete → 403; L reads/writes default graph; a second key minted for A works independently.
- Assert: separate create/delete scopes (create without delete: delete → 403); minted keys deleg=0; multi-key-per-graph independence; legacy back-compat.
- Surfaces: 2, 3, 4, 5, 12 (API-side; surface-12 dashboard UI covered in the ux layer).

**Review gate (Sub-step 7):** 3 parallel reviewers (e2e-coverage, e2e-reproducibility, test-quality) — dispatched after this section lands.

### E2E-10: Suspended team — control + data plane locked, appeals open
- Setup: pro team with a per-graph key + a graph; team marked suspended (both modes).
- Steps: provision/create with the suspended team's key → 403; data-plane ops (ask/analyze/search/MCP/sessions/context) → 403; `/v1/team/alerts` appeal flow → still reachable (200).
- Assert: suspension 403s both planes (#1853/#1828 parity); alerts appeal unaffected.
- Surfaces: 3, 7, 11.

### E2E-11: Concurrent provisioning — no oversubscription
- Setup: solo team at 2 of 2; N=8 parallel `POST /v1/teams/{id}/graphs` requests (both modes).
- Steps: fire 8 concurrent mints.
- Assert: exactly 0 succeed (2-of-2 cap) — or with the cap at 2 + 1 slot free, exactly 1 succeeds and 7 get 409; graph_count never exceeds the cap (atomic count-then-insert under one lock/transaction, W5); no orphan graph/ACL user from the rejected attempts.
- Surfaces: 1, 4, 6, 11.

### E2E-12: Key management surfaces + session-user dashboard create
- Setup: pro team; key with `keys:manage`; owner session user; dedicated team fixture.
- Steps: `GET /v1/teams/{id}/keys` lists keys incl. `last_used_at` (updated after a call; full per-request logs deferred in v1); `PATCH /v1/teams/{id}/keys/{key_id}` shrink scopes (read+write → read) succeeds, and the shrunken key's writes 403; `PATCH` expand-attempt → 422; **session-user `POST /v1/graphs` (dashboard create, routed through the ONE provisioning service) returns `key_plaintext` + `revealed_once:true` (reveal modal contract) — minted once, hash-only stored, a second fetch shows no plaintext; the endpoint's tier/quota gating (402/409) matches the key-driven path; response log-redaction of `key_plaintext` verified (no plaintext in logs — R4).**
- Assert: key list/shrink/expand-reject surfaces work; dashboard-create reveal-once contract holds; gating parity between session-user and key-driven create.
- Surfaces: 2, 3, 4, 12 (API-side of 12; modal UI in the ux layer).


---

# 8. Coherence Review + Risk Analysis

## 8.1 Cross-substep coherence (traceability matrix)

| In-scope item | Journey | Workflow | Data model | Interface | E2E |
|---|---|---|---|---|---|
| 1 Dual-mode graph data model | J1/J3 | W1/W3 | §4.1 graphs table + registry node | §6.2 | 1, 5, 8 |
| 2 Unified scoped keys | J1/J2/J7 | W2 | §4.1 api_keys graph_id+scopes+CHECK | §6.1/6.3 | 1, 4, 9, 12 |
| 3 Legacy team-key back-compat | J5 | W4 | §4.1 graph_id NULL | §6.1/6.4 | 5, 9 |
| 4 Provisioning + lifecycle | J1/J3 | W1/W3 | §4.1 | §6.2 | 1, 8, 11 |
| 5 One-level-deep | J2 | W1/W2 | CHECK constraint | §6.2/6.3 | 4, 9 |
| 6 Isolation all surfaces | J8 | W4 | namespace derivation | §6.4 | 2, 6, 10 |
| 7 Tier gate | J4 | W1 | max_graphs | §6.5 | 3 |
| 8 Delivery-shape tenancy | J6 | W4 | — | §6.4 | 6 |
| 9 session_recording scoping | J6 | — | graphs.recording | §6.3 PATCH | 6 |
| 10 Graph-count limits + dashboard | J3/J4 | W5 | §4.1 quota | §6.5 headers | 3, 7, 8, 12 |
| 11 Migration + docs | J5 | W3/W4 | §4.1 default=slot1 | §6.4 | 5, 8 |

Vocabulary audit: `tk_`/`tkm_` consistent everywhere; no `graphs:provision`, no provision flag, no `tg_live_` in the plan. Scope doc 01-scope.md and plan agree on: 9→12 E2E (plan adds E2E-10/11/12 from review gates — high-level scope list noted as superseded), quota definition (default = slot 1), key-minting rule (keys WITH keys:manage mint, children deleg=0), pro/team unlimited pending Gate #2.

## 8.2 Risk register

| # | Risk | Severity | Mitigation | Verification |
|---|---|---|---|---|
| R1 | Cross-graph isolation leak (key A touches graph B) | P0 | Layered enforcement: app-layer ownership check on every `select_graph` (authoritative, works with ACL OFF) + FalkorDB ACL defense-in-depth + scope pre-filter on all 7 surfaces | E2E-2 (zero-violation, ACL ON+OFF), E2E-6, E2E-10 |
| R2 | FalkorDB GRAPH.LIST name leak (#2652, upstream open) | P1 | Deny `+GRAPH.LIST`/KEYS/SCAN on every ACL user; never rely on ACL for confidentiality of existence; track upstream fix | E2E-1 (ACL permission set assert) |
| R3 | Dual-mode drift (Supabase vs registry) | P1 | Shared seam (`graph_metadata`/`graph_list`/`graph_count`) + seam parity tests in both modes | E2E-3/5/7 dual-mode, surface 10 |
| R4 | One-time-reveal violation (plaintext recoverable / lands in logs) | P1 | Hash-only storage (existing pattern), no show-key UI, audit, reveal modal contract; **log-redaction of responses carrying `key_plaintext` (request/access logs, error telemetry, proxy buffers) — verified in E2E-12** | E2E-1, E2E-12 |
| R5 | Migration regression (legacy keys break existing teams) | P1 | Primary/default-graph pattern (graph 0, keys resolve to default), additive-only API changes, no forced migration | E2E-5 |
| R6 | Quota oversubscription (concurrent mints at cap) | P1 | Atomic count-then-insert under one lock/transaction | E2E-11 |
| R7 | Key compromise → escalation (minted key gains power) | P0 | deleg=0 by construction + DB CHECK `chk_minted_key_no_escalation` + revoke-first + append-only audit | E2E-4, E2E-9 |
| R8 | Pro/team graph-cap decision | P2 | RESOLVED at Human Gate #2 (2026-09-01): unlimited (pricing.json as-is); caps free=1/solo=2 only; warn band dormant | Decision record in §3/§4.1 |
| R9 | session_recording default-ON contract regression (#1927) | P1 | Per-graph override with NULL = team default (ON); opt-out never silently re-enabled | E2E-6 |
| R10 | Quota reject AFTER billing (charge for rejected mint) | P1 | Quota gate pre-insert + Stripe test-mode observation point | E2E-7 |
| R11 | Suspended team bypass (data-plane leak) | P1 | 403 both planes (existing #1853/#1828 contract), alerts appeal open | E2E-10 |
| R12 | ACL user creation failure → orphan graph/key | P1 | Mint rollback (delete graph+key+ACL on any post-write failure); #1686/#1748 invariants | E2E-11 (no orphans) |
| R13 | FalkorDB ACL user proliferation at scale (1 user per graph; pro/team unlimited) | P1 | Cap-or-compaction policy for ACL users (reap ACL users on graph delete — already cascade; monitor user count; periodic audit); scale test at N graphs | Scale test + E2E-8 (ACL user reaped on delete) |
| R14 | `max_api_keys` cap vs multi-key-per-graph + provisioning (key minted per graph) | P1 | Atomic provision: key-mint failure ROLLS BACK the graph mint (409 max_api_keys — no graph-without-key, no orphan); documented interplay | E2E-12 (key cap), E2E-1/8 (mint paths) |
| R15 | FalkorDB aclfile persistence (memory-only until ACL SAVE — restart wipes per-graph users → data-plane outage) | P1 | SAVE-on-mutate hook (selfhost) + startup ACL re-sync/verification (assert users exist after restart); hosted cloud: custom ACL strings via management API | Restart-recovery test (selfhost) |
| R16 | Registry-mode vs hosted ACL-layer divergence (create on provision, delete on graph delete, SAVE on both) | P1 | ACL lifecycle parity in the shared seam (one helper for both modes); E2E-1 pins which mode(s) assert the ACL user | E2E-1 dual-mode, surface 6 |
| R17 | Dual create-path divergence (POST /v1/graphs session path vs key-driven path) | P1 | ONE provisioning service function (one quota gate, one rollback, one envelope) — the session path is a thin alias | E2E-12 (gating parity) |
| R18 | Migration rollback (drop tables/columns/CHECK restores prior behavior) | P2 | Documented reversible migration + rollback drill in CI (apply → rollback → re-apply); no code ships without the DB (seam-gated) | Migration-safety tests (surface 13) |

## 8.3 Improvement opportunities

- **Consolidate the quota source** into one helper (`graph_count` via the shared seam) so meter/banner/cap/API header all read the same number — the E2E-3/7/8 assertions depend on it.
- **One provisioning service** (accepted): `POST /v1/teams/{id}/graphs` + `POST /v1/graphs` share one service function, envelope, quota gate, rollback (R17) — E2E-12 gating parity is then a single-path test.
- **v1 lifecycle = active/delete only** (accepted): archive dropped (one-way state nothing consumes; delete already soft-tombstones + cascades + frees quota). Deferred until a restore/retention need exists.
- **80% soft-warning band deferred** (accepted): unreachable at free=1/solo=2; ships with a finite pro/team cap if Gate #2 sets one (R8).
- **Key-model DB invariant extended** (accepted): `chk_minted_key_no_escalation` covers graph-bound AND team-wide minted keys via `delegation_depth` + `created_by_key_id`.
- **Decomposition ordering (for epic-decompose):** Phase 1 — key-model migration (api_keys graph_id+scopes+delegation+CHECK, resolve_api_key extension, graph_id NULL → default; pure additive, all existing tests stay green; exit gate E2E-5/9). Phase 2 — provisioning + key lifecycle + ACL users (new endpoints; API side of E2E-12). Phase 3 — data-plane tenancy spine (~30 `_make_sdk` sites + ownership check, W4/E2E-2) — riskiest, fully testable once the key model ships. Phase 4 — dashboard (surface 12). The archive/warn-band cuts shrink Phases 2/4.
- **Dashboard surface** (surface 12) intentionally minimal this epic: list + keys + reveal modal + meter + gate CTA; delete action on the dashboard can follow the API-first cut.

## Plan ready for decomposition (after Human Gate #2)

Pipeline position: Sub-steps 1-8 complete; per-substep gates: journeys+workflows ✓, prototype+data-model+architecture ✓ (converged), interfaces+detailed E2E ✓ (3-parallel reviewers, converged), **coherence review ✓ (3-parallel reviewers: cross-substep-drift + risk-completeness + improvement-opportunities — NO ISSUES FOUND after fix pass)**. Plan-doc status: all review gates CLEAN as of 2026-09-01.

**Human Gate #2** (pending): review the plan + the OPEN decision (R8 — finite pro/team graph caps vs unlimited; pricing.json change if finite). Then → epic-decompose.

---

# 9. Decomposition record (epic-decompose, 2026-09-01 — MECE CLEAN)

Child issues created via issue-creation, per-issue review gates converged (0 P0/P1 after fixes), MECE verification CLEAN:

| Issue | Title | Complexity | Depends | Phase |
|---|---|---|---|---|
| #2094 | test-design integration-surface map (13 surfaces) | standard | — | pre-plan |
| #2110 | C1 dual-mode graph + key data model | complex | — | 1 |
| #2111 | C2 unified provisioning service + graph lifecycle | complex | 2110, 2094 | 2 |
| #2112 | C3 key lifecycle endpoints | standard | 2110, 2111* | 2 |
| #2113 | C4 FalkorDB per-graph ACL layer | standard | 2110, 2111* | 2 |
| #2114 | C5 data-plane tenancy spine | complex | 2110, 2111, 2113 | 3 |
| #2115 | C6 delivery-shape + session_recording | standard | 2110, 2114 | 3 |
| #2116 | C7 dashboard Graphs enhancement | standard | 2111, 2112 | 4 |
| #2117 | C8 migration runbook + docs | standard | 2110, 2111, 2114 | 4 |
| #2118 | capstone clickthrough verification | complex | 2110–2117 | final |

(* = verification-time dependency.) Key ownership carve-outs from the MECE gate: C6 owns E2E-6 + surfaces 8/9 payload (C5 = resolution slice); C1 owns the rollback drill (C8 consumes); C3 owns the shared key-mint service (C2 wires); E2E-7 split (C2 quota half / C3 revocation half); C7 does NOT depend on the spine.
