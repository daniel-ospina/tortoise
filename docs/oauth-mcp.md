---
title: "OAuth 2.1 for Remote MCP Auth (hosted)"
type: engineering
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-oauth-mcp
domain: platform
doc_status: live
created: 2026-08-15
updated: 2026-09-11
---

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

The team is selected by the **client-declared RFC 8707 resource indicator**, or
by an explicit user choice on the consent page for resource-less clients
(#1701 R1):

| Resource | Team |
|---|---|
| `https://api.premiselabs.co/mcp` (or omitted, or the AS origin root `https://api.premiselabs.co`) | the user's **sole** active team; several active teams → the consent page shows a **team chooser** (ChatGPT etc. cannot declare an RFC 8707 resource); 0 active teams → error |
| `https://api.premiselabs.co/mcp/teams/{team_id}` | that team (must be an active membership, not suspended) |

The token row stores the bound `team_id`; the MCP boundary introspects it
directly (D6 — OAuth tokens are self-sufficient, no `tt_` key minting; the
session→key bridge stays for dashboard flows).

**Resource-less clients (#1701 R1):** the consent preview
(`/oauth/consent/preview`) returns the account's selectable (non-suspended)
teams when several active teams exist, and the page's picker binds only the
user's explicit selection (no silent default). Suspended teams are excluded
from the chooser and can never mint a code — preview, consent POST, and the
exchange-time backstop all reject them. The origin-root resource echo is
accepted by **exact equality** only; any other undeclared resource value is
still rejected (RFC 8707 §2).

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
  (30d), `TORTOISE_OAUTH_CODE_TTL` (600s).

## DCR capacity policy (#2866)

`POST /register` (RFC 7591) is an unauthenticated write surface that Anthropic
calls *per fresh connection*, so it needs a stated, testable capacity policy
rather than an implicit one. The stated policy, enforced by
`_check_oauth_dcr_rate_limit` in `tortoise/hosted_api.py`:

| Dimension | Default | Knob |
|---|---|---|
| Per bucket (per client IP; per `/64` for IPv6) | 20/hr | `TORTOISE_OAUTH_DCR_PER_HOUR` |
| Anonymous global aggregate (all non-exempt IPs) | 600/hr | `TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR` |
| Trusted-CIDR aggregate (per trusted network) | 1200/hr | `TORTOISE_OAUTH_DCR_TRUSTED_PER_HOUR` |
| Live-bucket store cap | 256 | `TORTOISE_OAUTH_DCR_STORE_CAP` |
| IPv6 store-key prefix | `/64` | `TORTOISE_OAUTH_DCR_IPV6_PREFIX` |
| Trusted CIDRs (comma-separated) | `160.79.104.0/21` | `TORTOISE_OAUTH_DCR_TRUSTED_CIDRS` |
| Sliding window | 3600 s | fixed |

Dimension membership: **trusted ⇒ per-CIDR aggregate only** (no per-key bucket,
no shared overflow, no anonymous aggregate); **anonymous ⇒ per-key bucket (or
shared overflow) AND the anonymous global aggregate**. The trusted carve-out is
evaluated *before* any per-key/overflow path, so the exemption is reachable
even under an anonymous flood.

Bounded store. A bucket is *active* iff it holds an in-window entry. Reclaim
pops inactive LRU-head buckets (store order is last-charge), so an active key
can never be evicted and a tracked key's charge stays O(1) — lookups do not
scan the store. When the cap is still full, a new key is denied its own bucket
and charged to one shared overflow bucket (cap = `PER_HOUR`) **and** the
anonymous aggregate. A 429 charges nothing and inserts nothing (all dimensions
are evaluated before any insert/charge).

Derived ceiling. The distinct-new-anonymous-key rate is bounded by
`STORE_CAP + PER_HOUR = 276/hr` at defaults (256 live keys + 20 overflow
charges). This is a *burst/concurrent-live* bound, derived from the constants
above — not a bound tested at shipped scale.

The default trusted CIDR `160.79.104.0/21` is Anthropic's published
outbound/MCP egress range (`platform.claude.com/docs/en/api/ip-addresses`).
`TORTOISE_OAUTH_DCR_TRUSTED_CIDRS` is read verbatim when set: an **empty value
means an empty trusted set** (the documented lever to disable the exemption) —
never the default. A malformed entry is skipped without aborting the list.

Scope vectors. `SCOPES_SUPPORTED` (`["mcp"]`) stays the client-facing default
and the RFC 9728 PRM document; `SCOPES_ACCEPTED` (`["mcp",
"offline_access"]`) is what the DCR gate and the RFC 8414 AS metadata accept, so
Claude's `offline_access` request no longer 400s.

Accepted limitations (see the code comment for the full list):

- The stores are **in-process**, so real capacity is `limit × machines` and
  resets on restart. Out-of-process limiting is #1677.
- `oauth_clients` row pruning is **not** part of this policy — #2853 owns it
  (owner @daniel-ospina, review date 2026-10-15); #3124 tracks the still
  unbounded shared per-IP bucket primitive.
- The limiter runs **before body parsing**, so an invalid-JSON or oversized
  POST still consumes budget (charges ≤ 600/hr anonymous + 1200/hr trusted);
  row writes are not bounded by it.
- `/register` also passes the generic `RateLimitMiddleware` (100/min, whose
  bucket store has no hard key cap — #3124).
- The exemption rests on the Fly edge overwriting any client-supplied
  `Fly-Client-IP`; #3126 is the dated re-verification (owner
  @daniel-ospina, 2026-11-15) and carries the operator recipe.
- Trusted traffic is not charged to the anonymous aggregate; unrelated
  protocol gaps found en route are filed as #3125 (`_check_claim_rate_limit`
  proxy-IP keying) and #3128 (unvalidated authorize/consent scope).
