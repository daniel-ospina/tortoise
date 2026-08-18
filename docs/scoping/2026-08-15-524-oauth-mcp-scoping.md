# Scoping: OAuth 2.1 for Remote MCP Auth (hosted Tortoise)

> **Issue:** #524 · **Status:** scoped (decisions locked 2026-08-15) · **Owner decisions:** issue comment 5287516212 + owner answers 2026-08-15

## O/I/T

- **Objective:** Remote MCP clients connect to the hosted service via OAuth 2.1 (authorization code + PKCE, Dynamic Client Registration), alongside the current tenant `tt_` bearer keys which remain a permanent documented fallback.
- **Indicators:**
  1. Authorization Server Metadata + Protected Resource Metadata endpoints exist (RFC 8414 / RFC 9728).
  2. Remote MCP clients can complete auth-code + PKCE flow against the tenant registry.
  3. `tt_` keys still work (documented fallback; additive, never breaking).
- **Targets:** OAuth working for at least one reference client (Claude-style `oauth_dcr` connector) against hosted staging; zero breakage of existing `tt_` flows.

## Context

- All blockers shipped: #338 (service-model migration) via PR #554; hosted provisioning #518/#519/#292 closed; user↔team decoupling (D-series, ec8cd71).
- Existing seams to reuse: `tortoise/mcp_auth.py` (Bearer validation), `tortoise/session_auth.py` (JWKS ES256+RS256 + `/v1/session/key` bridge).
- Ecosystem: MCP spec 2025-11-25 makes RFC 9728 PRM **MUST** for servers, RFC 8414/OIDC discovery **MUST** for clients; Claude.ai connectors support `oauth_dcr`; ChatGPT GPT Actions is the outlier (manual client_id/secret).
- DCR (RFC 7591) is **MAY/fallback** in the MCP spec; emerging preferred mechanism = Client ID Metadata Documents (draft-ietf-oauth-client-id-metadata-document-00).
- `fastmcp==3.4.6` ships a client-side OAuth 2.1 auth-code+PKCE handler; `authlib==1.7.2` already a transitive dep via `fastmcp-slim`.

## Approach (P1-P4)

1. **P1 - Metadata endpoints:** well-known Protected Resource Metadata + Authorization Server Metadata.
2. **P2 - Auth-code + PKCE** against the tenant registry, Supabase-auth-backed (decision D2).
3. **P3 - Client registration:** DCR `/register` now; adopt Client ID Metadata Documents when the draft stabilizes (decision D1).
4. **P4 - Token->team mapping:** RFC 8707 resource indicator, client-declared (decision D4); rotating refresh tokens per (user, team), revoked on team suspension (decision D5).

## Locked decisions (2026-08-15)

| # | Question | Decision |
|---|---|---|
| D1 | DCR vs CIMD | DCR `/register` now; CIMD when stable |
| D2 | AS: Authlib vs Supabase-auth | Supabase-auth-backed (reuse JWKS verify); branded consent = one custom HTML page, same pattern as existing signup/signin pages |
| D3 | OAuth covers REST `/v1/*`? | MCP-only now; REST stays `tt_` + session-JWT |
| D4 | Team selection (M:N users) | Client-declared resource indicator (RFC 8707), no picker UI |
| D5 | Refresh-token lifecycle | Rotating, per (user, team) pair, revoked on team suspension |
| D6 | Session->key bridge relationship | OAuth tokens self-sufficient at MCP boundary (introspection, no key minting); bridge stays for dashboard flows |

## Complexity

- Engineering: **complex** (auth protocol + endpoints + revocation)
- Ontology: low (no schema changes)

## Test plan

- Integration surfaces: `mcp_auth.py`, new metadata endpoints, PKCE flow handler, DCR register, RFC 8707 mapping, revocation on team suspension.
- Regression: all existing `tt_` bearer tests stay green.
- E2E: reference client completes OAuth against hosted staging; `tt_` fallback still works.

## Open at implementation time

- None blocking (all owner decisions locked). Implementation order: P1 -> P2 -> P4 -> P3 (P4 before P3 so registration can mint scoped tokens).
