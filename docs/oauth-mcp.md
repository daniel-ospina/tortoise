# OAuth 2.1 for Remote MCP Auth (hosted)

> **Issue:** #524 · **Status:** implemented · **Scoping decisions (locked 2026-08-15):**
> `docs/scoping/2026-08-15-524-oauth-mcp-scoping.md`

The hosted MCP endpoint (`https://api.premiselabs.co/mcp`) accepts **two**
Bearer credential families, additive and never breaking (D3):

| Credential | Format | Purpose |
|---|---|---|
| Tenant API key (fallback) | `Bearer tt_<key>` | Pre-existing path — permanent, documented fallback (D3) |
| OAuth 2.1 access token | `Bearer oat_<token>` | Minted by the auth-code + PKCE flow below |

REST `/v1/*` is **unchanged** — `tt_` keys + session JWTs only (D3: MCP-only).

## Discovery (P1)

| Endpoint | Spec |
|---|---|
| `GET /.well-known/oauth-protected-resource` (+ `/mcp` variant) | RFC 9728 Protected Resource Metadata |
| `GET /.well-known/oauth-authorization-server` (+ `/mcp` variant) | RFC 8414 Authorization Server Metadata |

MCP SDK clients (Claude Code, Codex, fastmcp `OAuth()` provider) discover
these automatically — `claude mcp add tortoise https://api.premiselabs.co/mcp`
needs no client_id paste.

## Flow (P2)

1. Client discovers PRM + AS metadata, then DCR-registers (`POST /register`,
   RFC 7591, P3/D1) — no operator-issued client_ids.
2. Client opens `/oauth/authorize` with `response_type=code`,
   `code_challenge` (PKCE, S256 only), `redirect_uri`, and an optional
   RFC 8707 `resource`.
3. The branded consent page (D2 — one custom HTML page reusing the
   signup/signin pattern) signs the user in via supabase-js and confirms.
   The browser session JWT is verified server-side with the **existing JWKS
   / ES256+RS256 path** (`session_auth.verify_session_jwt` — D2: no new auth
   stack).
4. `POST /oauth/consent` binds a single-use, PKCE-bound authorization code
   to (user, team).
5. `POST /oauth/token` (`grant_type=authorization_code`) verifies the
   verifier + redirect_uri and issues `oat_` access + rotating `ort_` refresh
   tokens.

## Token → team mapping (P4, D4)

The team is selected by the **client-declared RFC 8707 resource indicator**
(no picker UI):

| Resource | Team |
|---|---|
| `https://api.premiselabs.co/mcp` (or omitted) | the user's **sole** active team; error if 0 or >1 (client must declare) |
| `https://api.premiselabs.co/mcp/teams/{team_id}` | that team (must be an active membership) |

The token row stores the bound `team_id`; the MCP boundary introspects it
directly (D6 — OAuth tokens are self-sufficient, no `tt_` key minting; the
session→key bridge stays for dashboard flows).

## Refresh + revocation (D5)

- Refresh tokens are **rotating per (user, team)**: each use revokes the
  presented token and mints a fresh pair.
- **Team suspension** revokes the user's whole (user, team) refresh family
  (`_revoke_team_family`) and rejects the grant; suspended teams' access
  tokens are rejected at the MCP boundary with the same 403 SUSPENDED
  semantics as `tt_` keys (#308).
- A lapsed membership revokes the presented refresh token.
- Clients may revoke explicitly: `POST /oauth/revoke` (RFC 7009).

## Implementation notes

- `tortoise/oauth.py` — protocol logic, control-plane seam (functions take
  `cp` explicitly; FakeControlPlane-compatible `query()` dialect).
- `tortoise/hosted_api.py` — endpoints: `/oauth/authorize`, `/oauth/consent`,
  `/oauth/consent/preview`, `/oauth/token`, `/oauth/revoke`, `/register`,
  well-known metadata.
- `tortoise/mcp_auth.py` — `TeamResolutionMiddleware` routes `oat_` tokens
  to `oauth.resolve_oauth_access_token` (hosted/Supabase mode only;
  registry/selfhost mode has no OAuth tables → 401, `tt_` unchanged).
- `supabase/migrations/0016_oauth.sql` — tables (clients / codes / access /
  refresh tokens), hash-only secret storage, RLS service_role.
- Storage: control-plane tables (hash-only token storage — mirrors
  `api_keys.lookup_hash`).
- OAuth is hosted-only: in registry/selfhost mode the functional endpoints
  fail closed with 503; metadata endpoints still serve static JSON.
- Env knobs: `TORTOISE_OAUTH_ACCESS_TTL` (3600s), `TORTOISE_OAUTH_REFRESH_TTL`
  (30d), `TORTOISE_OAUTH_CODE_TTL` (600s), `TORTOISE_OAUTH_DCR_PER_HOUR` (20/IP).
