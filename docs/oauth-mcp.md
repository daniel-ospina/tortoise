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
updated: 2026-09-20
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

   **`redirect_uri` acceptance at registration (#3579).** An entry is accepted
   when it is `https`, an `http` loopback URI, or a **private-use URI scheme**
   listed in `_NATIVE_REDIRECT_SCHEMES` — currently `cursor` alone, per
   RFC 8252 §7.1, because Cursor IDE's MCP OAuth DCR still sends
   `cursor://anysphere.cursor-mcp/oauth/callback` on its exthost path. It is a
   deliberate **allowlist**: registration is all-or-nothing (one rejected entry
   costs the client its `client_id`, and with it every sign-in path), and the
   consent page hands the code over by navigating to the raw value, so a scheme
   a browser executes (`javascript:`, `data:`) must never be registrable. A
   fragment is refused for every scheme (RFC 6749 §3.1.2) — tested as the raw
   `#` delimiter, so a bare trailing `#` (an empty fragment) is refused too.

   **`redirect_uri` matching (#2846).** For **loopback** redirect URIs the port
   is ignored when matching the registered value (RFC 8252 §7.3 — a native
   client binds an ephemeral port at request time and cannot know it at
   registration; Claude Code CLI depends on this). Scheme, host, path, params,
   query, fragment and userinfo must still match exactly, and every **non-loopback**
   URI keeps strict exact-string matching. Host is never relaxed:
   `localhost` and `127.0.0.1` are different hosts. A URI containing a raw
   backslash is refused at registration **and** at validation: WHATWG ends the
   authority at a backslash for special schemes but `urlsplit` does not, so the
   two parsers disagree about the host, and the code is delivered by navigating
   the browser to the raw string. Control characters are refused too, as defence
   in depth rather than because they are differential — `urlsplit` strips
   `\t`/`\r`/`\n` just as a browser does, and a browser refuses, percent-encodes,
   or (at the input's leading/trailing edge) strips the others.
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
| `https://api.premiselabs.co/mcp/organizations/{org_id}` | that team (must be an active membership, not suspended) |

The token row stores the bound `org_id`; the MCP boundary introspects it
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

## Authorization-code redemption state (#3027)

`oauth_codes.used_at` records that a request **claimed** a code; on its own it
cannot say what the claim *did*, so a failed or lost redemption was
indistinguishable from a replay. Migration
`20260925000002_oauth_redemption_state.sql` adds the durable outcome:

| `redemption_state` | Meaning | Redeemable? |
|---|---|---|
| `unclaimed` | not claimed (mirrors `used_at IS NULL`) | yes, while unexpired |
| `claimed` | an attempt owns the code; **outcome not yet recorded** | no — a second request is terminal, and the residue is settled by the reconciler |
| `minted` | the pair was handed to the response | no — replay-safe terminal |
| `burned` | terminal failure; never mints again | no |

A schema CHECK pins the state to the legacy flag **in one direction** —
`used_at IS NULL ⇒ redemption_state = 'unclaimed'`, i.e.
`used_at IS NOT NULL OR redemption_state = 'unclaimed'`. It is deliberately NOT
the biconditional: the pre-#3027 writer PATCHes `used_at` alone and leaves the
`'unclaimed'` default, and because this migration must be applied **before** the
new image ships (the fail-closed migration-drift gate), the old writer is live
against this schema during the rollout — a biconditional rejects it and every
authorization-code exchange then fails with `23514`. (A `NOT VALID` check does not
help: Postgres still enforces it on new writes.) The enforced direction is the one
the state machine relies on — a settled row always has a claim timestamp — and the
relaxed one is the shape `_observe_code` already treats as `claimed`. The claim
statement writes `used_at`, the state and a fresh `redemption_id` atomically,
alongside the existing `used_at IS NULL` CAS, so every write this code makes
satisfies the biconditional anyway.

**Rollout ordering.** Apply this migration before the new app image (the drift
gate enforces it), and apply it in a quiet window: the migration runs in one
transaction and takes `ACCESS EXCLUSIVE` on `oauth_codes`, `oauth_access_tokens`
and `oauth_refresh_tokens` until commit, so reads of the token tables block too.

**Terminal and recovery rules.** `minted` is written just before the pair is
returned — and **delivery is gated on winning that write**. Every **settle** is a
CAS on the claim identity (`redemption_state='claimed'` plus `id`, and
`redemption_id` when the settling view carries it), so only one of *{the owning
request, a reconciler that took the claim over}* can settle a claim. (The
`claimed → unclaimed` re-arm is a separate CAS on `code_hash`+`used_at`, made
in-process by the request that still owns its claim.) `burned` is
written where the residue is terminal: a pre-mint signal (bad PKCE,
client/redirect/resource mismatch, suspended org, or an expired code), or a
reconcile past the grace that ATTEMPTED to revoke a live orphan family (the
revoke is best-effort — a failure is captured and the row survives inert under a
now-`burned` code until the TTL sweep) or found none
**at probe time**.

An outcome the process could not settle stays **`claimed`**, and another
redemption of that code is answered **terminally** (`invalid_grant`) — never
retryably: the retry can terminate, because the sibling may still settle
`minted`, and #2863 records an outcome-unknown write state as never retryable.
The terminal answer also runs the lazy reconciler, which settles the residue once
it ages past the grace window (`TORTOISE_OAUTH_REDEMPTION_GRACE_S`, default 60s):

- within the grace window the claim may still be live, so **nothing is touched**
  (`inflight`) — least of all re-armed;
- past the grace, the reconciler first **takes the claim over with the same CAS**,
  then acts. If it loses that CAS the owner settled `minted` first, so the family
  is delivered and nothing is touched. If it wins and a LIVE family is linked to
  the code, the mint committed and was never delivered, so the family is
  soft-revoked (best-effort, and captured if the revoke fails) and the code is
  burned; if it wins and no family is linked, the claim
  left no live credential **at probe time** (the probe and the settle are not one
  transaction, so a family minted between them escapes) and the code is **burned**
  (`unresolved`) — fail safe; the client re-runs authorization;
- if the reconciler's own read fails, nothing is written.

The grace window does **not** prove the claim's owner is dead — the mutating
grant is awaited with no wall-clock bound, so a live sibling can outlive any
window; it bounds when a later request starts taking over. A live sibling that
outlives it loses the CAS and its pair is compensated (an aborted grant, not a
double grant). Resolution is **lazy**: it happens only when the code is presented
again, so a claim that is never retried stays `claimed`, and any orphan family
linked to it stays live until the retention sweep reaches its TTL.

**There is no cross-request re-arm.** An earlier revision re-armed a stale
no-family claim; that mints **two live families for one single-use code** when the
stalled owner is not in fact dead (the mutating grant is awaited with no
wall-clock bound, so no grace window proves otherwise). The verified-clean failure
re-arms **in process only**, via `_restore_code`, where the observation and the
write are the same request.

Minted rows carry `code_id` (the authorizing `oauth_codes.id`) on both the
access and refresh tables, and **rotation inherits it**, so "did this code
mint a family?" is answerable across a rotation chain. `code_id` is
`ON DELETE SET NULL` — the #3036 policy for OAuth provenance FKs (a bearer
credential is independent of the code that minted it).

**A retry never re-serves the same credential pair.** Tokens are stored hashed
only (below), so the plaintext cannot be re-issued. A response lost after the
`minted` write is therefore answered terminally and the client re-runs
`/oauth/authorize`; the family left behind is inert (nobody holds its
plaintext) and is reaped by the #3036 retention sweep. The invariant the state
machine guarantees is that a retry **never creates a second live family**.

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
  (30d), `TORTOISE_OAUTH_CODE_TTL` (600s); retention grace
  `TORTOISE_OAUTH_ACCESS_RETENTION_S` / `TORTOISE_OAUTH_REFRESH_RETENTION_S` /
  `TORTOISE_OAUTH_CODE_RETENTION_S` (each 86400s, positive-int validated — a
  malformed or non-positive override falls back to the default); redemption
  reconciler grace `TORTOISE_OAUTH_REDEMPTION_GRACE_S` (60s, same validation).
- Referential integrity + retention (#3036): `supabase/migrations/20260925000001_oauth_referential_integrity.sql`
  adds the two FKs 0016 omitted (`refresh_token_id`, `rotated_from`, both
  `ON DELETE SET NULL`) and `expires_at` indexes. A scheduled sweep
  (`tortoise/oauth.py::sweep_oauth_retention`, wired into `hosted_api` boot +
  `TORTOISE_EVENT_RETENTION_INTERVAL`) removes a row once its own `expires_at`
  is past by the grace. These windows are credential hygiene — a different axis
  from the user-content deletion promise; see `docs/retention-and-deletion.md`.
- Redemption state (#3027): `supabase/migrations/20260925000002_oauth_redemption_state.sql`
  adds `oauth_codes.redemption_state` / `redemption_id` / `redemption_settled_at`
  / `redemption_note`, the `code_id` provenance FKs (`ON DELETE SET NULL`),
  and the backfill that marks every already-consumed code `burned`. The
  state machine and the reconciler live in `tortoise/oauth.py`
  (`_settle_redemption`, `_observe_code`, `_reconcile_claimed_redemption`); the
  design record is `docs/scoping/2026-09-25-3027-oauth-redemption-state.md`.

## Client identity: CIMD (#2847)

Before this change the only client-identity path was Dynamic Client
Registration, which Anthropic calls *per fresh connection* on hosted Claude
surfaces — so the `oauth_clients` table grew with connections, not with
clients. The authorization-server metadata now also advertises **Client ID
Metadata Documents** (`draft-ietf-oauth-client-id-metadata-document-00`):

```json
"client_id_metadata_document_supported": true,
"token_endpoint_auth_methods_supported": ["none", "client_secret_post"]
```

Both values are required, not just the flag: Claude selects CIMD **only** when
the flag and `"none"` are both present (its CIMD client authenticates as a
public client at the token endpoint). If either is missing it falls back to
DCR. The flag is read at request time, so `TORTOISE_OAUTH_CIMD=0` reverts the
metadata and the fetch path in one env change — no deployment.

With CIMD the `client_id` **is** an HTTPS URL that the authorization server
fetches. It is fetched from `/oauth/authorize` *before* the user is
authenticated, and — because `resolve_client` is the one resolver shared with
the token path — also from `/oauth/consent` and from `/oauth/token` (both
grants) when the presented `client_id` does not resolve in the registry. That
is a server-side request forgery surface, so the fetch lives in
`tortoise/cimd.py` behind seven controls, each with a test in
`tests/test_cimd_ssrf.py`:

| # | Control | Implementation |
|---|---|---|
| 1 | URL validation | https, absolute, path present, no userinfo, no fragment, no literal *or* percent-encoded `.`/`..` segments, length + control-char caps |
| 2 | Host validation | every resolved address must be globally routable (no private / loopback / link-local / CGNAT / multicast / reserved / unspecified / NAT64) **and the socket connects to the vetted address** — see below |
| 3 | Redirects | never followed; a 3xx is a hard failure |
| 4 | Size + timeout | 64 KiB body cap, 3 s connect/read |
| 5 | Cache | successes only, 300 s TTL, LRU cap 128; errors and malformed documents are **never** cached (§4.3) |
| 6 | Rate limit | per-host 60/hr + aggregate 600/hr + live-store cap 256 |
| 7 | Total occupancy (#3669) | in-flight fetches capped process-wide (4), a per-fetch deadline bounding every socket phase (6 s: connect attempts, TLS, status/header and body reads — the 3 s read timeout is per-socket-read, not total; the OS resolver's `getaddrinfo` tail is the documented exception, see Limitations), and a per-window wall-clock budget (120 s / 3600 s) whose worst case is RESERVED at admission |

Control 2 is closed against **DNS rebinding** rather than narrowed: a custom
`httpcore` `NetworkBackend` resolves the host, refuses the whole resolution if
*any* address is non-public, and then connects the TCP socket to the vetted
address while TLS SNI and the `Host` header stay on the hostname (`httpcore`
passes `server_hostname=origin.host` to `start_tls`). A design that resolves,
validates, and then hands the *name* to the HTTP client leaves a TOCTOU window
in which the name re-resolves to an internal address between the two; pinning
removes the window. A proxy is deliberately not honoured (it would move egress
off the pinned socket), and unix sockets are refused.

Anthropic's rules beyond SSRF are applied too: the document must be
**self-referential** (its `client_id` must equal the URL it was served from),
`token_endpoint_auth_method` must be `none` (no shared secret can be
established), non-loopback `redirect_uris` must be same-origin with the
`client_id` URL, and the consent screen shows the client_id **host** — never
the document's self-asserted `client_name`, which would be a phishing surface.
The host must also be **ASCII (punycode)**: a non-ASCII host would render as a
homograph on the consent screen (`сlaude.ai` with a Cyrillic с), so the A-label
form is required. Loopback `redirect_uris` are exempt from the same-origin rule
(native clients declare an ephemeral port listener against a hosted client_id
URL; the port-agnostic match is #2846's `_redirect_uri_matches`).

### `oauth_clients` growth bound

| Identity path | Rows created | Bound |
|---|---|---|
| DCR (`POST /register`) | one per fresh connection | **O(connections)** — unbounded; gated by the #2866 limiter |
| CIMD | one per distinct `client_id` URL, deduplicated | **O(distinct URLs)** — a handful for a real client population |
| Operator-issued / `oauth_anthropic_creds` | one per issued credential | O(1) |

The CIMD row exists only because `oauth_codes` / `oauth_access_tokens` /
`oauth_refresh_tokens` carry a `REFERENCES oauth_clients(id)` foreign key, and
it is written **once per distinct URL**: three connections from the same
`client_id` URL produce exactly one row, however many times they connect —
which is the whole point of the change, and the property DCR lacks.

⚠️ **The "handful" bound is a property of honest clients, not a hard cap.** CIMD
changes the growth *driver* from connections to distinct `client_id` URLs; it
does not itself cap row growth, because anyone can mint a URL. The reachable
rate is bounded by the **CIMD fetch** limiter above (600/hr aggregate,
in-process) — **not** by the DCR limiter, which CIMD never touches — and, since
#3669, also by a process-wide in-flight cap and a per-window wall-clock budget
(see "Limitations"). Pruning for
the pre-existing DCR-generated rows remains owned by **#2853 / #1677 (owner
@daniel-ospina, dated 2026-10-15)**; CIMD adds one row per client
implementation in normal operation but does add to that backlog under abuse.

Idempotency under concurrency rests on the schema's `id text PRIMARY KEY`
(`supabase/migrations/0016_oauth.sql`): two simultaneous first authorizations of
the same URL race, and the loser's insert raises while the row is present, which
is not an error. Note the in-memory `FakeControlPlane` used by the tests does
**not** enforce the PK (its `POST` appends), so that claim is verified by
inspection against the migration rather than by a test.

### Knobs

| Knob | Default | Notes |
|---|---|---|
| `TORTOISE_OAUTH_CIMD` | `1` | `0`/`false`/`no`/`off` disables both the metadata flag and the fetch path |
| `TORTOISE_OAUTH_CIMD_SAME_ORIGIN` | `1` | `0` relaxes "non-loopback `redirect_uris` must be same-origin with the client_id URL" — the single lever if a future client's document legitimately spans hosts |

Both knobs resolve through the shared env-truthiness contract (`tortoise/env_truthy.py`, #4097),
so any truthy spelling (`1`/`true`/`yes`/`on`, any case) enables and any falsy spelling
(`0`/`false`/`no`/`off`) disables. **An EMPTY value (`TORTOISE_OAUTH_CIMD=`) or a
whitespace-only one means *unset*, i.e. the default — it does NOT disable the knob.**
Before #4097 an empty value silently disabled `TORTOISE_OAUTH_CIMD` (and, worse, silently
*relaxed* `TORTOISE_OAUTH_CIMD_SAME_ORIGIN`); set either to `0` to actually turn it off.

### Limitations (deliberate)

- The rate-limit, fetch-cache and #3669 occupancy stores are in-process, so the
  real bound is `limit × running machines` and resets on restart — the same
  accepted limitation as `_OAUTH_DCR_BUCKETS` (#2866; the shared primitive is
  #3124). The in-flight cap is per process (N machines ⇒ N×4), and the window
  budget is per process (N machines ⇒ N×120 s/window).
- The fetch is **synchronous** by construction, matching this path's existing
  control-plane style (`cp.query` is a blocking PostgREST call made from the
  same async handler). **#3669 moved the whole OAuth client resolution off the
  event loop** through the bounded `monitoring` offload seam on a dedicated
  `oauth` pool, so a fetch no longer occupies the loop (`Dockerfile.hosted` runs
  a single `uvicorn` process with no `--workers`). Total occupancy is bounded
  three ways, all charged in `resolve_client_metadata` — the one function all
  four unauthenticated front doors reach through `resolve_client`: a
  process-wide **in-flight cap** (`cimd.MAX_IN_FLIGHT_FETCHES`), a **per-fetch
  deadline** (`cimd.FETCH_MAX_S`; the per-read `READ_TIMEOUT_S` does not bound a
  trickled response, so `_DeadlineStream` caps every read/write/TLS timeout by
  the remaining deadline and the pinning backend caps each connect attempt — the
  OS resolver's own `getaddrinfo` timeout is the one unbounded tail), and a
  **wall-clock budget per window** (`cimd.FETCH_BUDGET_S`) whose worst case is
  reserved at admission and settled to the actual duration on return. Fetch
  COUNT alone never bounded the product (distinct `client_id` URLs share one
  aggregate budget; 600 fetches at the 6 s ceiling is ~the whole window).
  Ordering: `FETCH_MAX_S < CONTROL_PLANE_OFFLOAD_TIMEOUT_S`, so a fetch returns
  before its caller's offload bound.
- **The window budget is an admitted cost, and it is the reason a sustained
  attack can still starve a legitimate CIMD client.** It is a single
  process-wide 120 s / 3600 s allowance, so a hostile host that keeps ~20
  fetches alive near the `FETCH_MAX_S` ceiling exhausts it, after which every
  later cache-miss CIMD client is refused as an unknown client for the rest of
  the window (`invalid_client` at `/oauth/token`; `invalid_request` at
  `/oauth/authorize` and `/oauth/consent`). A cache hit, and any non-CIMD/DCR
  client, is unaffected. The fix removes the *unbounded* occupancy and keeps the
  AS responsive; it does not make CIMD fetch capacity attack-proof, and the
  600/hr aggregate limiter is the other ceiling on the same path. This is the
  residual the single-worker deployment carries until the limiter/budget moves
  to shared state (#3124).
- The `authorize` error path uses the client **stamped on the raised
  `OAuthError`** by `validate_authorize_params`, so an in-document
  `redirect_uri` is still honoured on error responses without a second
  resolution. Before #3669 it re-resolved, paying a second CIMD fetch and
  rate-limit charge on every *failed* request (the success path's cache
  absorbed it); a refused fetch there degrades to a JSON error rather than a
  redirect, which is the conservative direction.
- **Revocation:** the CIMD resolver re-reads through the revoked-filtered
  accessor, so a revoked `client_id` URL is refused at `/oauth/authorize` and
  `/oauth/consent` exactly as a revoked DCR client is (found in review; the
  provisioning insert's duplicate re-read used the raw row and would otherwise
  have resurrected it). Re-adding a revoked CIMD client requires clearing
  `revoked_at`, same as any other client.
- `oauth_anthropic_creds` (Anthropic-held credentials) remains the ops-side
  alternative and is **not** implemented here: it needs no Tortoise code, only
  an email to `mcp-review@anthropic.com` with a `client_id`/`client_secret`.
  CIMD is preferred because it is self-serve, works against any authorization
  server, and needs no vendor round-trip.

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
- **Charging doctrine:** the limiter charges at **check** time, not at the
  terminal outcome (unlike #1719's `defer_charge=True` callers). A
  control-plane 5xx from `register_client` therefore still consumes the
  caller's budget, and a post-recovery retry can meet a spurious 429 that
  masks the underlying failure — the #2051 failure class, which does not
  currently list DCR. Tracked, not silent.
- The **trusted aggregate (1200/hr) and anonymous aggregate (600/hr) are not
  measured against production volume** — the pre-#2866 model was
  `20/hr × distinct Anthropic egress IPs`, so 1200/hr could be either a large
  increase or a new single point of failure for the traffic the policy exists
  to protect. #3134 owns the dated measurement (owner @daniel-ospina,
  2026-11-15).
- `/register` also passes the generic `RateLimitMiddleware` (100/min, whose
  bucket store has no hard key cap — #3124).
- The exemption rests on the Fly edge overwriting any client-supplied
  `Fly-Client-IP`; #3126 is the dated re-verification (owner
  @daniel-ospina, 2026-11-15) and carries the operator recipe.
- Trusted traffic is not charged to the anonymous aggregate; unrelated
  protocol gaps found en route are filed as #3125 (`_check_claim_rate_limit`
  proxy-IP keying) and #3128 (unvalidated authorize/consent scope).
