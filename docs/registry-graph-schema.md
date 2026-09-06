---
title: "Registry Graph Schema"
type: engineering
domain: platform
doc_status: live
created: 2025-08-01
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutObjects:
- tortoise-hosted-platform
---

# Registry Graph Schema

The registry graph is a dedicated FalkorDB namespace (`registry`) storing control-plane entities for the Tortoise Hosted Platform. It is separate from tenant namespaces. Control-plane data migrates to Supabase under #669 (managed backups + PITR); until then it has no operator-controlled backup — see #596/#669.

## Entity Types

### Team

```
(:Team {
  id: string,              // ULID
  name: string,            // regex: [a-zA-Z0-9][a-zA-Z0-9_-]{0,63}
  tier: string,            // "free" | "solo" | "pro" | "team"
  created_at: datetime,
  stripe_customer_id: string?,  // null for free; set sync at checkout (#310)
  subscription_id: string?,
  subscription_status: string?, // "active" | "past_due" | "canceled" | "trialing" | "incomplete" | "unpaid" — derived mirror of Stripe
  current_period_end: float?,   // unix ts (webhook-sourced)
  grace_until: float?,          // unix ts = current_period_end + 72h on payment_failed
  customer_email: string?,      // webhook customer_details.email (provision-path identity)
  max_users: integer,      // 1 for free, 2 for pro
  max_teams: integer?,     // null = unlimited; 1 for free tier users
  max_graphs: integer?,    // null = unlimited; 1 for free tier
  max_api_keys: integer?,  // tier-derived from pricing.json (free=2)
  max_points: integer?,    // = pricing.json max_graph_nodes (points quota counts graph nodes)
  max_sessions: integer?,  // flat 1000 across tiers
  backup_enabled: boolean,
  backup_latest_at: datetime?
})
```

### WebhookEvent (idempotency markers — #310)

```
(:WebhookEvent {
  event_id: string,      // Stripe event.id — unique dedup key (SET-then-marker)
  type: string,          // "checkout.session.completed" | "invoice.payment_failed" | "customer.subscription.updated" | "customer.subscription.deleted"
  received_at: datetime,
  team_id: string?,      // bound team when resolvable
})
```

### Membership

```
(:Membership {
  id: string,       // ULID
  user_id: uuid,    // Supabase auth.users id
  team_id: string,  // references Team.id
  role: string,     // "owner" | "admin"
  joined_at: datetime
})
```

### Graph (epic #2083, C1/C2 — multi-graph tenancy)

```
(:Graph {
  id: string,        // g_<16 hex> (random hex — NOT a ULID); the DEFAULT
                     // graph's id is the DERIVED default (registry:
                     // kind='default' node; supabase: the literal 'default'
                     // — no graphs row, derived from teams.graph_name)
  team_id: string,   // references Team.id
  name: string,      // display name (default graph: "default")
  kind: string,      // "default" | "custom"
  namespace: string, // FalkorDB tenant namespace (the DEFAULT graph's ns
                     // IS the team namespace team_<name>; customs =
                     // team_<team_id>_<gid> — the graph SWITCHER/contexts
                     // ride this)
  status: string,    // "active" | "deleted" (tombstone; list filters)
  recording: boolean?,  // C6 #2115 session_recording override — true/false
                     // = per-graph override; NULL = inherit the team default
  created_at: datetime
})
```

Default-graph semantics (the no-migration contract):

- The default graph is **graph 0** — the team's pre-epic namespace. It is
  DERIVED, never materialized into a tenant data store: supabase mode reads
  `teams.graph_name`; registry mode has a `kind='default'` Graph node whose
  namespace IS the team namespace. **No backfill, no data move.**
- Existing (pre-epic) API keys have `graph_id` NULL and resolve to the
  default graph — legacy keys keep working untouched (E2E-5 zero-action).
- The default graph occupies quota slot 1 (`max_graphs_per_team`) and is
  NOT deletable (delete_graph 403s; the UI locks the row).
- Tenant namespaces carry their own `:Point`/graph data; only the registry
  namespace holds `:Graph` control-plane rows.
- **Backups are per-graph since #2313:** every active graph (default +
  custom) is swept hourly with its own archive pool
  (`backups/{team}/{graph}/{ts}_{rnd}/…`; default graph segment = the
  literal `default`), per-graph state
  (`ops/teams/{team}/graphs/{graph_id}/state.json`), independent retention
  (24 hourly + 7 daily + 4 weekly) and per-graph staleness incidents.
  Deleted (tombstoned) graphs are never swept and their archives are
  restore-refused (tombstone guard; #2304 trash semantics). Their nested
  archive pools are therefore NEVER pruned and accumulate until #2304's
  purge decision lands (mechanism recorded post-#2313 audit, #2378).

### APIKey

```
(:APIKey {
  id: string,              // ULID
  team_id: string,         // references Team.id
  graph_id: string?,       // C1: NULL = team-wide key → default graph;
                           // set = bound to ONE custom graph (no per-graph
                           // key exists for the default graph — graph-bound
                           // mints 404 on kind='default' nodes)
  scopes: string[],        // C1: FLAT allowlist ["graphs:read",
                           // "graphs:write"] (escalation, owner-only:
                           // graphs:create/delete, keys:manage, team:manage);
                           // [] = legacy full-access class (auth-architecture §6)
  delegation_depth: integer?, // 0 = minted (deleg=0, cannot mint);
                           // NULL = owner-minted
  created_by_key_id: string?, // mint lineage (which key minted this one)
  key_hash: string,        // SHA-256(pepper + key) — never plaintext
  key_prefix: string,      // first 8 chars for display
  created_by: uuid,        // Supabase user who created it
  created_at: datetime,
  last_used_at: datetime?,
  revoked_at: datetime?
})
```

Key-class derivation (resolution, D2 — all three lanes agree):

- `legacy_full_access` ⇔ `delegation_depth IS NULL AND scopes = []` — the
  pre-epic owner class (full team access, tt_ prefix).
- `delegation_depth = 0` (minted child) can NEVER hold escalation scopes
  (`graphs:create/delete`, `keys:manage`, `team:manage`) — DB CHECK
  `chk_minted_key_no_escalation` + resolution both enforce (D1/D13).
- A scoped deleg-NULL key with an EMPTY array is the same footgun the
  shrink branch 422s: per-graph keys require ≥1 explicit scope.

### Invitation

```
(:Invitation {
  id: string,        // ULID
  team_id: string,   // references Team.id
  email: string,
  role: string,      // always "admin" — only admins can be invited
  token: uuid,
  created_by: uuid,
  created_at: datetime,
  expires_at: datetime,  // created_at + 7 days
  accepted_at: datetime?
})
```

## Relationships

```
(:Membership) -[:BELONGS_TO]-> (:Team)
(:APIKey) -[:BELONGS_TO]-> (:Team)
(:APIKey) -[:SCOPED_TO]-> (:Graph)     // graph-bound keys (C1); team-wide keys
                                       // (graph_id NULL) resolve to the default graph
(:Invitation) -[:FOR_TEAM]-> (:Team)
(:Graph) -[:BELONGS_TO]-> (:Team)      // every graph (default + custom)
```

## Quota definition (epic #2083)

`max_graphs_per_team` (Team.max_graphs) counts graph rows: the DEFAULT graph
occupies slot 1; customs occupy the rest. Tier caps (product/pricing.json):
free=1, solo=2, pro/team NULL (∞ — owner decision, Gate #2 2026-09-01). The
create flow gates: 402 when the tier is in the blocked set (free/anon),
409 when at cap (both modes; per-team lock serializes count-then-insert).

## Authorization Matrix

| Operation | Free Owner | Pro Owner | Pro Admin |
| ----------- | ----------- | ----------- | ----------- |
| Create/query Points | ✅ | ✅ | ✅ |
| Create graphs | ❌ (max 1) | ✅ | ✅ |
| Invite members | ❌ (max 1) | ✅ (max 2) | ❌ |
| Manage billing | ❌ | ✅ | ❌ |
| Generate/revoke API keys | ✅ | ✅ | ❌ |
| Export team data | ✅ | ✅ | ✅ |
| Delete team | ✅ | ✅ | ❌ |
| View backups | ❌ | ✅ | ✅ |
| Trigger restore | ❌ | ✅ | ❌ |
