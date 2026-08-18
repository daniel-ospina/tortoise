---
title: "Scoping: Sub-tenancy for embedded app builders (per-end-user isolation)"
type: decisions
domain: platform
doc_status: live
created: 2026-08-15
ownedBy: organisation-design-team
---
# Scoping: Sub-tenancy for embedded app builders (per-end-user isolation)

> **Issue:** #557 · **Status:** scoped (direction locked 2026-08-15 decision comment; build sequenced after #318) · **Owner decisions:** issue comment 2026-08-13 (graph-per-end-user under embedder's account; per-end-user billing aggregated into embedder's bill; OAuth #524 as identity dependency)

## O/I/T

- **Objective:** App Builders (P0) and Build Teams (P3b) embed Tortoise as memory inside their product. Each of their end-users gets an isolated team/graph namespace under the **embedder's** account — provisioned programmatically, with no Tortoise signup for the end-user. End-user identity rides the #524 OAuth bridge; usage metering aggregates per-end-user into the embedder's bill.
- **Indicators:**
  1. An embedder can programmatically create an isolated tenant (team + graph) for an end-user without end-user signup.
  2. Each end-user's graph is invisible to every other tenant (end-user, other end-users, other embedders' end-users).
  3. The embedder's bill aggregates all end-user tenants' usage (write-ops → overage, per `product/pricing.json` per-team model).
  4. An end-user can access **only** their own graph via OAuth tokens scoped to it.
- **Targets:** provisioning + identity + metering + isolation verified in hosted staging for one reference embedder (2 end-users, 1 cross-embedder negative test); zero regression to #318/#524 surfaces.

## Context

**Shipped and reusable:**
- **#318 pack isolation (MERGED, PR #1261):** `tortoise/pack_registry.py` + `pack_state.py` — shared catalog + per-tenant `PackInstall` activation records written graph-natively into `team_{team_id}` graphs; idempotent additive MERGE; read-only `GET /v1/packs` + MCP `packs_list`; D6 existence masking; `graph-scripts/backfill_pack_installs.py`. The tenant graph **is** the isolation boundary ("a query with tenant B's identity reads tenant B's graph — no tenant selector exists on any surface").
- **#524 OAuth 2.1 MCP auth (MERGED, PR #1264):** `tortoise/oauth.py` — auth-code + PKCE, DCR, RFC 8707 token→team mapping (`{origin}/mcp` single-team vs `{origin}/mcp/teams/{team_id}` team-scoped resource, membership-verified at token mint, unknown resources rejected per RFC 8707 §2), rotating refresh tokens revoked on team suspension.
- **Decoupling + provisioning foundations:** user↔team↔graph decoupling (#568/#615, ec8cd71); `/internal/provision` (hosted_api.py), `provision_team` RPC (supabase_control.py), tenant-provision Edge Function with caller-JWT auth (#802); per-team write-op metering (`tortoise/metering.py`, `:MeteringRecord` keyed `(team_id, period)`, registry graph); per-team pricing (`product/pricing.json`, overage $5/10k write-ops).

**Unbuilt (this epic):** embedder-scoped provisioning/impersonation, end-user tenants without signup, end-user identity via OAuth mapped to sub-tenants, per-end-user metering aggregated into the embedder's bill, sub-tenant abuse controls.

**Model (owner decision, 2026-08-13):** **graph-per-end-user** (Turso-style db-per-tenant) — each end-user = one team + one graph under the embedder, isolation structural via graph namespace. Rejected: shared-graph + RLS (Firebase/Supabase-style) — the graph is the memory (2026-08-12 graph-as-memory hypothesis), per-tenant graphs keep lifecycle/provenance queryable per user and make cross-tenant leakage structurally impossible.

## Approach (slices)

- **S1 — Embedder-scoped provisioning:** embedder-account-scoped endpoint/RPC creating an end-user team + graph under the embedder's account. Two candidate mechanisms (open Q1): (a) embedder-scoped service token (`embedder_{...}` capability) minting tenant-scoped child keys, vs (b) an impersonation-style internal RPC with caller-JWT auth (pattern: #802 tenant-provision Edge Function). Reuses `provision_team` + graph naming `team_{team_id}`; hooks #318 default-pack activation at the new provisioning site (idempotent MERGE).
- **S2 — End-user identity bridge (OAuth, reuse #524):** end-user tokens minted via #524's RFC 8707 mapping, scoped to the end-user's team resource; `scope=mcp` only; membership verified at mint; no Tortoise signup — the embedder's OAuth client declares the resource. Refresh tokens rotate and revoke on end-user tenant suspension.
- **S3 — Metering into embedder's bill:** extend `metering.py` to record per end-user team (existing `(team_id, period)` key works unchanged) and roll totals up to the embedder's account for invoicing/overage (open Q5 granularity). Embedder sees aggregated usage; end-users see nothing.
- **S4 — Introspection:** end-user `packs_list`/`GET /v1/packs` served from the embedder's catalog via #318's shared-catalog read; activation records stay per-tenant graph (reuse `pack_state` ensure-then-read, D6 masking).

## Complexity

- Engineering: **complex** — multi-surface (provisioning, OAuth scope, metering aggregation, isolation guarantees), security-sensitive.
- Ontology: low (no schema changes; reuses team/graph model).

## Security analysis (required)

- **Isolation boundary:** the tenant graph namespace (`team_{team_id}`) is structural (reuses #318's LANDED boundary; no tenant selector on any surface) — an embedder's end-user cannot read another end-user's graph, and cross-embedder isolation is the same boundary (graphs live under each embedder's namespace tree).
- **Impersonation risk (S1):** the embedder-scoped capability is the crown jewel — a stolen embedder token yields every end-user tenant. Mitigate: scoped child-key minting (tokens minted for one end-user tenant only, never a master), short-lived minting capability, audit events per mint, rate limits (abuse.py precedent).
- **End-user escalation:** end-user OAuth tokens are minted for exactly one team resource (RFC 8707 parse + membership check at mint, #524 D4) — an end-user token cannot reach other tenants or the embedder's account surfaces; scope fixed to `mcp` (no admin/registry scope exists for end-users).
- **Cross-embedder exfiltration:** end-user access is always relative to the token's resource — an end-user of embedder A presenting A's token reads only A's graph under A's namespace; embedder B's namespace is outside A's graph tree (same structural guarantee #318 verified in staging; #524's resource-tree rejection (`invalid_resource`) is the exfiltration guard precedent).
- **Masking:** D6 empty-masking reused — end-users with nothing in scope see empty results, never errors that leak existence of other tenants.

## Open questions (human review)

1. **S1 mechanism:** embedder-scoped service token minting per-end-user child keys vs impersonation-style internal RPC? **Default: embedder-scoped token with per-end-user key minting** (no master-tenant impersonation surface).
2. **End-user auth:** full OAuth login for end-users vs key-only (embedder mints a per-end-user key server-side)? **Default: key-only for v1** (no end-user signup/consent surface; OAuth stays for embedder-side MCP).
3. **Metering granularity:** aggregate write-ops per end-user, billed as one pool in the embedder's bill vs per-end-user line items? **Default: per-end-user line items rolled into the embedder's bill** (indicator 3).
4. **Pricing/tier gate:** sub-tenancy requires `pro`/`team` embedder tier, or available at free? **Default: `pro`+ (segments already list app_builder).**
5. **Beta timing:** does sub-tenancy block beta launch or ship post-launch? **Default: post-launch** (prerequisite user-journeys ships first; #557 has zero sub-issues — decompose after #318/#524 land on main).
6. **Abuse controls:** per-embedder provisioning rate limit + per-end-user write caps — in S1 or follow-up? **Default: rate limit in S1 (abuse.py pattern), per-end-user caps follow-up.**
7. **Pack source for end-user tenants:** embedder's custom packs (deferred per #318 note) vs fixed starter set? **Default: fixed starter set v1; custom packs follow #1154.**

## Test plan

- **Provisioning:** embedder creates end-user tenant → team + graph + default PackInstalls created; idempotent re-run no-ops; rate limit enforced; audit event per mint.
- **Isolation:** end-user A cannot read B's graph, cannot list other tenants, cross-embedder negative test (A's embedder's token cannot reach B's namespace); reuse #318 staging cross-tenant test shape.
- **OAuth scope:** end-user token minted for own resource only; `invalid_resource` for other teams; revocation on tenant suspension; refresh rotation.
- **Metering:** end-user write-ops recorded per `(end_user_team, period)`, aggregated into embedder bill total; dashboard shows aggregate; overage math matches pricing.json.
- **Introspection:** end-user `packs_list` returns starter packs (D6 masking on empty); read-only enforced.
- **Regression:** all #318 (`test_pack_state.py`) and #524 (`test_oauth_mcp.py`) suites stay green.
