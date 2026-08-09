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

### APIKey
```
(:APIKey {
  id: string,         // ULID
  team_id: string,    // references Team.id
  key_hash: string,   // SHA-256(pepper + key) — never plaintext
  key_prefix: string, // first 8 chars for display
  created_by: uuid,   // Supabase user who created it
  created_at: datetime,
  last_used_at: datetime?,
  revoked_at: datetime?
})
```

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
(:Invitation) -[:FOR_TEAM]-> (:Team)
```

## Authorization Matrix

| Operation | Free Owner | Pro Owner | Pro Admin |
|-----------|-----------|-----------|-----------|
| Create/query Points | ✅ | ✅ | ✅ |
| Create graphs | ❌ (max 1) | ✅ | ✅ |
| Invite members | ❌ (max 1) | ✅ (max 2) | ❌ |
| Manage billing | ❌ | ✅ | ❌ |
| Generate/revoke API keys | ✅ | ✅ | ❌ |
| Export team data | ✅ | ✅ | ✅ |
| Delete team | ✅ | ✅ | ❌ |
| View backups | ❌ | ✅ | ✅ |
| Trigger restore | ❌ | ✅ | ❌ |
