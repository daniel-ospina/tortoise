---
title: "Data Safety — Encryption in Transit and at Rest"
type: operations
domain: platform
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
related:
  - issue: 309
    note: "Public /security page — same claims, user-facing copy"
---

# Data Safety — Encryption in Transit and at Rest

> **Claim-accuracy discipline:** every statement below is code- or deployment-verifiable,
> consistent with the verified claim set in `docs/scoping/2026-08-13-309-security-page-scoping.md`.
> Do not strengthen claims without updating both this doc and the #309 spec.

## In transit — TLS everywhere on the hosted surface

| Surface | Protection | Evidence |
|---|---|---|
| Hosted API + MCP (`api.premiselabs.co` / `/mcp`) | TLS 1.2 + 1.3, terminated at the **Fly.io edge** (no version pinning; Fly proxy supports only 1.2+1.3) | `fly.toml` `handlers = ["tls", "http"]` on 443; live DNS `66.241.124.70` → NetName **FLYIO** (recorded 2026-08-13) |
| DB connection (production) | **FalkorDB Cloud** managed instance, connected via `rediss://` (TLS) — `FALKORDB_CLOUD_URI` secret resolved at runtime by `entrypoint.sh` | `tortoise/config.py` `SUPPORTED_URI_SCHEMES = ("docker", "redis", "rediss")` (#715 fixed `rediss://` routing); FalkorDB Cloud documents TLS on Startup/Pro/Enterprise tiers |
| Postgres (Supabase — registry, audit, dashboard auth) | TLS-only (`sslmode=require`) | `.env.example` "Add ?sslmode=require (psycopg2 needs it for Supabase TLS-only)" |
| Local / dev | `docker://` loopback sidecar with `--requirepass`; no TLS needed (loopback only) | `.env.example`, docker-compose |

## At rest — secrets protected, graph data at infrastructure level

| Data | Protection | Evidence |
|---|---|---|
| OAuth tokens (GitHub etc., stored on Team node) | **Fernet** (AES-128-CBC + HMAC-SHA256) symmetric encryption; key from `TORTOISE_ENCRYPTION_KEY` Fly secret; **fails closed** if key missing; encrypted columns revoked from anon/authenticated roles | `tortoise/crypto.py:3`; `github_token_enc` column REVOKEd (`hosted_api.py:4827`) |
| API keys | **Never stored reversible** — PBKDF2-HMAC-SHA256, per-key 32-byte random salt, 100,000 iterations, server-side pepper; separate peppered SHA-256 lookup digest; plaintext exists only at creation | `tortoise/auth.py:83-137` |
| Graph content (Points/objects, FalkorDB) | **Infrastructure-level only** — NOT Tortoise-app-level encrypted. Relies on the hosting provider disk encryption (FalkorDB Cloud managed storage; Fly volumes LUKS block-device encrypted at rest by default) | Fly docs: volume encryption at rest enabled by default (LUKS); #309 scope line 55 |
| Control-plane audit events | Postgres first (`audit_events`), local JSONL fallback (`audit_fallback.jsonl`), automatic replay on recovery | `audit_events.py` (`INSERT INTO audit_events`, `ON CONFLICT`, `_replay_fallback`); `hosted_api.py` `tenant_register` / `api_key_create` / `api_key_mint` / `auth_failure` |
| Dashboard account passwords | **Supabase Auth** salted hashing — belongs to the dashboard surface, not the Tortoise runtime (runtime has **no password system**: API keys + OAuth tokens only) | #309 scope credential-scoping block |

## The honest summary (what to tell users)

1. **Everything sent or received over the hosted service is TLS-encrypted in transit** (API edge TLS 1.2+1.3, TLS DB connections, TLS-only Postgres).
2. **Secrets are cryptographically protected at rest**: OAuth tokens Fernet-encrypted with a Fly-managed key; API keys stored only as salted+peppered hashes, never reversible.
3. **Graph content is encrypted at rest at the infrastructure level** (provider disk encryption — LUKS volumes / managed storage), **not** at the application level. Deliberate boundary: Tortoise does not currently do client-side or app-level content encryption of graph data. If a future customer requirement demands provider-cannot-read content, that is tracked (deferred) in issue #265.

## Why the deferred encryption (#265) doesn't change the above

#265 (client-side content encryption) is **deferred** (2026-08-13): the graph stores *legible points/objects* — that is the memory — while raw conversation/document content can live locally or on a self-managed server; the graph only needs to index it. Basic data safety (TLS everywhere, secrets at rest) is unaffected and already shipped.

## Keep in sync

- Update this doc when the `/security` page (issue #309) ships — same claim set, user-facing copy.
- `TORTOISE_ENCRYPTION_KEY` rotation and the FalkorDB-password-in-history hygiene check (#337 follow-up) are operational items, not claim changes.
