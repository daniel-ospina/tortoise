<!-- research-path: issue-scoping artifact posted on #2833 (no epic brief — Level: task) -->

# Claude Desktop/Web OAuth Connect Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the hosted MCP resource server reachable and conformant for Claude's connector, and give the Claude Desktop / Claude Web onboarding wizard a key-less OAuth path — so a user on an account without the beta Request-headers field can connect by supplying only the Server URL.

**Team:** organisation-design-team
**Role:** product-implementer

**Architecture:** Three layers, in dependency order. (1) **Routing/identity** — eliminate the same-host 307 on the registered MCP URL with a raw-ASGI path rewrite, and canonicalize the *connector* URL on the no-trailing-slash form across every surface, so PRM's `resource`, the wizard's copy, and the served onboarding skills agree. (2) **Resource-server conformance** — the tenant-resolution `/mcp` 401s must carry the `WWW-Authenticate: Bearer resource_metadata="…"` challenge; the trailing-slash discovery URIs must return 200 rather than 307. (3) **Presentation** — the **live** `claude-desktop`/`claude-web` wizard branches move off the key-paste recipe onto a key-less OAuth recipe.

**Scope boundary (locked by scoping, 4 verifier cycles):** the authorization server itself (PKCE, DCR, consent, refresh rotation, RFC 8707 team mapping) **already exists** — shipped in #524 → #1701. This plan does not rebuild it. Four workstreams were deliberately split out and filed: **#2846** (loopback `redirect_uri` port-agnostic — Claude Code only), **#2847** (CIMD / `oauth_anthropic_creds` / admin-supplied client identity — SSRF dimension), **#2848** (full latency + cold-start envelope), **#2853** (`oauth_clients` and sibling-table pruning). **#2849** (false no-307 comment) is fixed *here* because Task 1/2 rewrite that code area.

### ⛔ Two facts the plan must not get wrong (established in plan-review cycle 1)

1. **`HARNESS_OAUTH` is not a live-render gate.** It has exactly one consumer — `main.jsx:6338` — inside `{LEGACY_WIZARD_ARCHIVED && welcomeOriented && (` at `main.jsx:6311`, and `LEGACY_WIZARD_ARCHIVED = false` (`main.jsx:2317`). The **live** connect step is `main.jsx:6009` → `:6072`, which renders `HARNESS_ORDER.filter(h => h !== 'chatgpt')`, and its claude-desktop/claude-web branches (`:6183`, `:6225`) render `harnessKey ? <key-paste recipe> : wizardNoKeyAffordance`, where `harnessKey = wizardDurableKey || durableConnect.key || ''` (`main.jsx:5675`). **Adding ids to `HARNESS_OAUTH` alone changes nothing user-visible** — the live JSX branches must be edited.
2. **The keyed-harness URL is a different surface from the Claude-connector URL.** `tortoise/__main__.py::_harness_mcp_config` (not `tortoise/mcp_server.py`, which only carries an error-message string) emits `api_url.rstrip("/") + "/mcp/"` for the 6 keyed CLI harnesses, and six test assertions pin that slash **deliberately** (`test_harness_mcp_config.py:29-31`: *"the hosted endpoint always carries the trailing slash — pinned here so a regression to the unsuffixed URL fails"*). Those surfaces keep the slash; only the **connector** surfaces change.

### Pattern Research

> **Findings date:** 2026-09-10
> **Gate skipped (Step B):** the plan introduces **zero third-party dependencies** — only Starlette, already a direct dependency, via `JSONResponse`/ASGI primitives already in-repo (`mcp_auth.py`, `hosted_api.py:993`). Per the `writing-plans` skip rule Step B does not fire. Step A ran: the scoping artifact's `### Axis Research` + `### Integration Docs` are the PRIOR_RESEARCH source.
> **Bucket canonical:** MCP 2025-06-18 (challenge MUST) / 2025-11-25 (relaxed to MUST-one-of-two; CIMD SHOULD, DCR MAY); RFC 9728 §5 (challenge is a MAY); RFC 8414; RFC 9110 §15.4.2 (a same-host 307 preserves method and `Authorization`).
> **Bucket pitfalls:** `claude-ai-mcp#217` (a server that **already returned** the challenge still failed on claude.ai web; closed as not planned ⇒ necessary-not-sufficient); Anthropic's redirect failure class is **host-scoped**; `offline_access` is only relevant to *registration* scope validation (`validate_authorize_params` takes no `scope` at all — do not repeat the cycle-1 claim that it would reject the request); `RATE_LIMIT_DISABLED=1` is default-on in `tests/conftest.py:22`, so a rate-limiter test that does not unset it **cannot fail**.
> **Bucket competitor-variance:** the `chatgpt` OAuth harness is the *copy* precedent (key-less recipe, sign-in → Authorize → org chooser) but **not** a live-render precedent — `chatgpt` is filtered out of the live chooser at `main.jsx:6072`.

### Integration Surface Map

| Surface | Kind | Test layer | Failure modes to cover |
|---|---|---|---|
| `/mcp` + `/mcp/` routing (raw-ASGI rewrite) | HTTP routing | `TestClient(app, follow_redirects=False)` status matrix, explicit `Accept` headers | (a) a step introduces a 3xx; (b) SSE-`Accept` GET (405 by design, `test_mcp_http.py:362-372`) is asserted as 200 |
| tenant-resolution `/mcp` 401s (4 sites) | HTTP response | header assertion on raw response; challenge target fetched | (a) one branch omits the header; (b) target itself 307s |
| self-host static-key 401s (`mcp_auth.py:479,486`) | HTTP response | assertion of **absence** of challenge | no AS exists there — a challenge would point at a 404 |
| `.well-known/*` trailing-slash forms (4) | HTTP metadata | 4 requests/doc, `follow_redirects=False`, doc-appropriate keys | (a) 307 instead of 200; (b) test asserts PRM-shaped body for the AS document |
| `parse_resource` / PRM `resource` | auth contract | unit table incl. **team-scoped** + canonicalized variants | (a) normalization asymmetry on the team prefix; (b) drift between PRM and the JSX/docs literals |
| DCR limiter + bucket store | in-process state | `monkeypatch.delenv("RATE_LIMIT_DISABLED")` + bucket reset fixture; aggregate + CIDR **plumbing** | (a) test passes because the limiter is disabled; (b) CIDR branch unreachable when `TORTOISE_TRUST_FLY_CLIENT_IP` unset; (c) bucket dict unbounded |
| **live** wizard connect branches | UI (React/JSX) | `wizardConnectTripwire.test.js` **source-slice** assertions + node test suite | (a) a `dist/` rebuild is forgotten so CI validates the old bundle; (b) the branch keeps a `harnessKey ?` requirement |
| committed dashboard bundle `dist/assets/*.js` | build artifact (tracked) | `npm run build` + commit + the e2e job's committed-dist tripwire | stale bundle ships the old URL **and** the old beta caveat |
| `harnesses.js` / `main.jsx` / `docs.html` URL literals | UI constants | one source-scan test asserting a **single** canonical connector constant and no stale connector literal | a missed JSX/docs literal re-creates the PRM mismatch while all other tests stay green |
| served skills (`skills/`→agent-infra, `tortoise/onboarding/`, `public/skills/`, `dist/skills/`) | static assets, parity-gated | `cmp` for the onboarding chain; `skill-sync` for the symlinked tree | (a) parity drift; (b) the `skills/` symlink is invisible to `grep -rn`/`git ls-files` |
| OAuth token lifecycle (revocation cache a 60 s LRU) | auth boundary | warm-cache revocation test on the **same** app instance | existing test builds a fresh app, so the cache gap is invisible |

### Journey Test Map

```markdown
### Journey: A field-less Claude account connects Tortoise with no API key
1. **Step:** User opens the wizard, picks Claude Desktop/Web → **Acceptance:** the LIVE branch
   shows a key-less OAuth recipe (Server URL + sign-in/Authorize), no key, no beta caveat
   → **Test:** `wizardConnectTripwire.test.js` source-slice assertion (+ node suite)
2. **Step:** User pastes the Server URL into Claude's connector → **Acceptance:** the value equals
   PRM's `resource` exactly and `POST` on it does not 3xx
   → **Test:** source-scan canonical-constant test + raw-status matrix test
3. **Step:** Claude probes → **Acceptance:** `401` + `WWW-Authenticate: …resource_metadata="…"`
   and every discovery form 200s
   → **Test:** 401-header test + discovery-status test
4. **Step:** Claude registers, the user authorizes → **Acceptance:** the replay reaches
   `tools/list` on the canonical URL → **Test:** Task 7 replay (post-deploy)

### Failure Modes
- Control plane fails after the auth code is consumed but before tokens are issued
  → **Expected:** a coherent OAuth error, and the code remains redeemable (or the client gets a
  retryable signal) → **Test:** Task 7 fault-injection leg
- An access token is revoked while warm in the 60 s LRU
  → **Expected:** rejected within the documented bound → **Test:** Task 7 warm-cache revocation
- Two concurrent refreshes with the same refresh token
  → **Expected:** exactly one 200; the loser is not forced into full re-authorization
  → **Test:** Task 7 concurrency leg
- A client sends the slashed `resource`, or the team-scoped form
  → **Expected:** both resolve → **Test:** `parse_resource` table incl. team-scoped variants
- A client requests `scope="mcp offline_access"`
  → **Expected:** documented behaviour, consistent with advertised `scopes_supported`
  → **Test:** scope round-trip test
```

**Tech Stack:** Python 3.12 / Starlette / FastMCP; React 18 + Vite (dashboard, committed `dist/`); pytest (docker FalkorDB lane); node test runner for the dashboard tripwires. **No new dependencies.**

### Dependency & Parallelism Map

```
Wave A (independent, parallel-safe):
  Task 1  tortoise/hosted_api.py  (middleware :993 + mount :21201)
  Task 3  tortoise/mcp_auth.py    (only mcp_auth.py — no hosted_api overlap)
  Task 7  docs/research/* + tools/* (new files only)

Wave B (after Task 1):
  Task 4  hosted_api.py :20943-20961   ─┐  both touch hosted_api.py in distinct
  Task 5  hosted_api.py :20920-20922   ─┘  regions; either serialise them OR land
                                           them as one "server metadata + DCR" commit

Wave C (after Task 2's constant decision + Task 4):
  Task 2  harnesses.js / main.jsx / docs.html / skills / keyed-docs
  Task 6  main.jsx LIVE branches + harnesses.js recipe + dist rebuild
          → Task 6 depends on Task 2 (same files; Task 2 defines the constant Task 6 uses)

Wave D (last):
  Task 8  verification sweep (needs every other task landed)
```

**Contention rule:** Tasks 2 and 6 both edit `harnesses.js` and `main.jsx` — **serialise them** (2 → 6). Tasks 1/4/5 share `hosted_api.py` — serialise or land as one commit. Task 3 is the only `mcp_auth.py` writer.

---

## Task 1: Redirect-free `/mcp` routing

**Intent:** Remove the same-host 307 on the registered MCP URL — the first-party `#985` incident (`hosted_api.py:993-1010`) shows this exact chain (scheme-downgraded `Location` → Fly 301 → POST→GET conversion → `GET /mcp/` 405) already broke the MCP TS SDK once.
**Acceptance:** with `follow_redirects=False`, `GET` (`Accept: */*`) → `200`, `POST` → `401`, `OPTIONS` → `405`; identical for `/mcp/`; **no 3xx on either**; `GET` with `Accept: text/event-stream` → `405` **on both forms** (preserved behaviour, see `tests/test_mcp_http.py:362-372`); a POST accepted by the rewrite still returns SSE-framed JSON-RPC.
**Files:**
- Modify: `tortoise/hosted_api.py` (add the rewrite middleware near `ForwardedProtoMiddleware` `:993`; mount area `:21197`)
- Test: `tests/test_mcp_http.py`, `tests/test_hosted_api.py`

**Step 1: Write the failing test.** Add a raw-status matrix test over `{GET(*/*), GET(text/event-stream), HEAD, POST, OPTIONS}` × `{"/mcp", "/mcp/"}` using `TestClient(app, follow_redirects=False)`. Expected per the acceptance line.
**Step 2: Run it** → **FAIL** (`POST /mcp` is 307).
**Step 3: Implement** a **raw ASGI** middleware (a `Middleware`-class callable — **not** `BaseHTTPMiddleware`, which buffers and can break the SSE transport) rewriting `scope["path"]` `/mcp` → `/mcp/` (and `raw_path` consistently) before routing. Do **not** add a second `Mount`/`add_route("/mcp", …)` — that yields `GET/HEAD /mcp` → 404 and regresses `test_mcp_route_mounted`.
**Step 4: Add the streaming assertion** as **`POST /mcp` with an `Accept` that includes `text/event-stream`**, asserting SSE-framed JSON-RPC. Do **not** assert that `GET` + `text/event-stream` streams — the handler answers that shape with 405 by design.
**Step 5: Run** `tests/test_mcp_http.py tests/test_mcp_server_auth_modes.py tests/test_hosted_api.py -v` → **PASS**, including `TestProxyProtoRedirect` **unchanged** (it runs against a synthetic mini-app that installs its own `ForwardedProtoMiddleware`, so it cannot detect changes to the real app — record that as the reason it is not decision input).
**Step 6: Record two consequences in the commit body**, with the *correct* mechanisms (plan-review cycle 1 corrected both):
   - `ForwardedProtoMiddleware` is **retained** — it still governs the `Location` scheme for trailing-slash 307s on ~9 other paths, incl. `/oauth/token/` and both well-known documents. Do not remove it.
   - **Rate-limit bucketing:** `_bucket_key` (`hosted_api.py:880-893`) keys on the Bearer token or `ip:<host>`, **not** on the path. So `/mcp` and `/mcp/` were never in separate buckets for a *keyed* request — but an **unauthenticated** request (no token) does fall back to the IP key, so the observable change is limited to that case. Assert this with a test rather than asserting a false mechanism.
   - Add the assertion to the test: one credential hitting `/mcp` then `/mcp/` lands in one bucket.
**Step 7: Commit.** `git add tortoise/hosted_api.py tests/test_mcp_http.py tests/test_hosted_api.py && git commit -m "fix(mcp): redirect-free /mcp routing via raw-ASGI path rewrite (#2833 W1)"`

---

## Task 2: Canonicalize the **connector** URL (and only the connector URL)

**Intent:** PRM's `resource` must equal the URL the user is told to enter. Today PRM says `…/mcp` while every *connector-facing* surface says `…/mcp/`.
**Acceptance:** the connector surfaces carry one canonical no-slash constant, equal to `mcp_resource_url(base)`; the **keyed-harness** surfaces remain slashed and that split is recorded in a code comment replacing the false no-307 claim (#2849); `parse_resource` accepts slash, no-slash, canonicalized (case/port/fragment) **and team-scoped** forms; a source-scan test proves no stale connector literal survives; the migration/notification story is written down.
**Files — connector surfaces (→ canonical no-slash):**
- `website/apps/dashboard/src/harnesses.js` (new/reused canonical constant; replace the false `:7-8` comment)
- `website/apps/dashboard/src/main.jsx` (`:6199`, `:6232`, `:887`)
- `website/docs.html:172` **and `:180`**; and the wrong `type` literal at `:171` (`"streamable-http"` → `"http"` — cycle-1 dropped this)
- Served connector copy: `tortoise/onboarding/SKILL.md`, `website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md`, `website/apps/dashboard/dist/skills/tortoise-onboarding/SKILL.md`

**Files — served skill trees that ALSO carry the literal (route via `skill-sync`):**
- `skills/{how-to-use-tortoise,tortoise-decide,tortoise-file-finding,tortoise-onboarding}/SKILL.md` — **this is a symlink to the agent-infra repo**; `grep -rn` and `git ls-files` do not traverse it. It carries 11 occurrences. **Edit the source in agent-infra and sync**, do not edit the symlink path in this repo.
- `website/apps/dashboard/{public,dist}/skills/{how-to-use-tortoise,tortoise-decide,tortoise-file-finding}/SKILL.md` — these three have **no** source copy in this repo; the chain is public↔dist only. `tortoise-onboarding` is the one with a source→public→dist chain.

**Files — keyed-harness surfaces (LEAVE SLASHED, annotate only):** `tortoise/__main__.py::_harness_mcp_config`, `README.md`, `client/README.md`, `docs/quickstart-cloud.md`, `docs/INGEST_CONTRACT.md`, `tests/test_harness_mcp_config.py`, `tests/test_init_apikey_validation.py`, `tests/test_onboarding_variants.py`, `tests/e2e/test_welcome_page.py`.
**Test:** new canonical-constant source-scan test (pattern: `wizardConnectTripwire.test.js` reads source as text) + `parse_resource` table + a node assertion.

**Step 1: Write the failing tests.**
   (a) **Source-scan test**: read `main.jsx`, `harnesses.js`, `website/docs.html` as text; assert exactly one canonical connector URL constant and that **no** connector-surface literal ends in `/mcp/`. **Do not** compare against `MCP_URL` (the keyed constant) — that was the cycle-1 P0.
   (b) **PRM equality**: `protected_resource_metadata(base)["resource"]` equals the canonical connector constant, compared after canonicalization (not against a `http://testserver`-derived string).
   (c) **`parse_resource` table**: bare, trailing-slash, uppercase host, explicit `:443`, embedded fragment, **and team-scoped** (`…/mcp/teams/<id>`, with the case/port variants) → each returns the documented `(canonical, team_id)`.
**Step 2: Run** → **FAIL**.
**Step 3: Decide the constant.** `harnesses.js:10` already defines `CHATGPT_MCP_URL = 'https://api.premiselabs.co/mcp'` — the exact canonical value — and the shipped `chatgpt` copy uses it. **Reuse it by renaming to a neutral `CANONICAL_MCP_URL` and aliasing `CHATGPT_MCP_URL`**, so there is **one** connector constant rather than three. Keyed `MCP_URL` stays slashed. Record the rationale in the comment: the keyed CLI harnesses are not the Claude connector, and their slash is a #984-era convention pinned by six assertions; the connector surfaces must match PRM.
**Step 4: Add the drift test** — assert `CANONICAL_MCP_URL === mcp_resource_url(<origin>)` (path component) and that `MCP_URL` is intentionally its slashed sibling. Two constants cannot silently diverge.
**Step 5: Apply the literal changes** on the connector surfaces listed above. **Never blanket-replace `premiselabs.co/mcp/`** — the team-scoped form `…/mcp/teams/{team_id}` exists in `docs/oauth-mcp.md:64` and must not be flattened.
**Step 6: Annotate (do not change) the keyed surfaces** with a one-line comment: slashed by design (#984), intentionally different from the connector constant.
**Step 7: Extend `parse_resource` normalization** to lowercase scheme/host and strip default port and fragment — **applied to both sides of the comparison, including the `team_prefix` built from `base_mcp`** (cycle-1 finding: normalizing only the client side makes the team-scoped form fail while the bare form passes, silently excluding multi-team users).
**Step 8: Write the migration/notification clause** (required by scoping W1, missing from cycle 1): both URL forms keep working because Task 1 removes the 307; **no delete-and-re-add is required**; the previously-published slashed form remains accepted by `parse_resource`. State this in the PR body and reference it from Task 7's human script.
**Step 9: Run.** The source-scan test, the PRM equality test, the `parse_resource` table, `tests/test_oauth_mcp.py`, the node suite, and the four keyed files (which must still **pass unchanged**) → **PASS**.
**Step 10: Commit** referencing #2849.

---

## Task 3: `WWW-Authenticate` challenge on tenant-resolution `/mcp` 401s

**Intent:** MCP 2025-06-18 requires the 401 to carry `WWW-Authenticate: Bearer resource_metadata="…"`; Tortoise emits it nowhere, so Claude must fall back to well-known probing on every connect.
**Acceptance:** each of the **four tenant-resolution** 401 sites carries the challenge, and the `resource_metadata` URL returns 200; the **self-host static-key** 401s carry **no** challenge (no AS exists there); the revision status and the #217 necessary-not-sufficient caveat are stated in the PR; `GET`/`HEAD /mcp/` stays 200 as documented-public transport metadata; the `offline_access` position is recorded **with the correct premise**.
**Files:**
- Modify: `tortoise/mcp_auth.py:114-125` (`_jsonrpc_error` — add a `headers` parameter) and the challenge-emitting call sites; plus the GET/HEAD decision comment
- Test: `tests/test_oauth_mcp.py` (`TestMcpBoundary`, flow body `:1220+`), `tests/test_mcp_server_auth_modes.py`

**Step 1: Write the failing test.** Parametrize the challenge assertion over the **four tenant-resolution** 401 paths — missing header (`:172`), empty token (`:186`), bad prefix (`:193`), unknown credential (`:254`, which covers both an unknown `tt_` and an unknown `oat_`). Assert `www-authenticate` present and that its `resource_metadata` URL returns 200 with `follow_redirects=False`. **Separately** assert the self-host static-key 401s (`:479`, `:486`, `StaticKeyMiddleware`) carry **no** challenge, with a comment recording why: `tortoise/selfhost.py` mounts the MCP app but registers no `/.well-known/*` routes, so a challenge there would point at a 404.
**Step 2: Run it** → **FAIL** (no header anywhere).
**Step 3: Extend `_jsonrpc_error`** with a `headers` parameter (it has none today) and emit `WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource/mcp"` plus `error="invalid_token"`. Use the `…/mcp`-suffixed document path (the trailing-slash form 307s). **Fix the labelling collision**: in this plan "`…/mcp`-suffixed" means the path segment `/mcp`; Task 4's "trailing-slash forms" means a literal trailing `/`. Say so in the code comment.
**Step 4: Pin the suspended-team case separately** — it is a **403** (`mcp_auth.py:332`), not a 401, and is not one of the challenge sites. Record whether it should carry `error="insufficient_scope"`.
**Step 5: Record the `offline_access` position with the correct premise.** `validate_authorize_params` takes **no `scope` parameter at all** — the scope check lives in `register_client` (DCR) only. So the cycle-1 claim that advertising `offline_access` would make authorize reject the request is **wrong**. Add a scope round-trip test (`scope="mcp offline_access"` through authorize → consent → token) asserting documented behaviour and that `authorization_server_metadata()["scopes_supported"]` matches the set the authorize/consent path actually accepts.
**Step 6: Add the code comment for the GET/HEAD decision** recording: leave `GET`/`HEAD /mcp/` 200 because it is the documented #236 transport-metadata route and the #217 logs show a `POST` probe; trip-wire = if the Task 7 evidence shows a GET probe hitting the authless branch, flip it in a follow-up issue. **Task 7 must record whether such a probe occurs** — otherwise the trip-wire has no trigger.
**Step 7: Run** `tests/test_oauth_mcp.py tests/test_mcp_server_auth_modes.py tests/test_mcp_http.py -v` → **PASS** (the four `GET /mcp` → 200 assertions stay green).
**Step 8: Commit.**

---

## Task 4: Discovery metadata — trailing-slash forms return 200

**Intent:** `/…/oauth-protected-resource/mcp/` and `/…/oauth-authorization-server/mcp/` 307 today; a vendor troubleshooting item requires 200 with valid JSON.
**Acceptance:** all four **registered** routes and all four trailing-slash variants return 200 for `GET` with no 3xx; the AS document is asserted with AS-shaped keys (it has no `resource` field); the non-`GET` behaviour is decided and asserted; `docs/oauth-mcp.md` carries the vendor checklist; the `openid-configuration` alias is explicitly **not** added.
**Files:**
- Modify: `tortoise/hosted_api.py` (well-known routes `:20943-20961`) and/or the mount producing the 307
- Modify: `docs/oauth-mcp.md`
- Test: `tests/test_oauth_mcp.py` (metadata section `:226-273`)

**Step 1: Write the failing test — enumerate the URIs explicitly.** *The cycle-1 wording "all four URI forms" was ambiguous and would have passed immediately*: the four **registered** routes already return 200. The defect is the **trailing-slash** forms, which are not registered and are 307'd by Starlette's `redirect_slashes`. Test all **8** with `follow_redirects=False`:
   `/…/oauth-protected-resource`, `/…/oauth-protected-resource/`, `/…/oauth-protected-resource/mcp`, `/…/oauth-protected-resource/mcp/`, and the four `oauth-authorization-server` equivalents. Assert 200 + `application/json`; assert `resource` + `authorization_servers` for the **PRM** document and `issuer` + `authorization_endpoint` + `token_endpoint` + `registration_endpoint` for the **AS** document (do not assert `resource` on the AS document — it has none).
**Step 2: Run it** → **FAIL** (four trailing-slash forms 307).
**Step 3: Implement** so the trailing-slash forms serve directly rather than redirecting. State in a comment that a 404 on `/.well-known/openid-configuration` is **expected** per the vendor, and why an RFC 8414 alias there would be semantically wrong.
**Step 4: Decide and assert the non-`GET` behaviour** (W3 asked for this explicitly): state whether non-`GET` on these routes is 405 or 200 and assert it.
**Step 5: Write the checklist** into `docs/oauth-mcp.md` — one row per documented Claude-side requirement with source URL, a **revision qualifier** where the requirement changed between revisions, and a met/violated/unverified status.
**Step 6: Run** the metadata tests → **PASS**.
**Step 7: Commit.**

---

## Task 5: DCR capacity for Anthropic's shared egress

**Intent:** Claude re-registers per connection from Anthropic's shared egress (`160.79.104.0/21`) against a 20/hour limiter that is per-process, not global — real traffic will 429.
**Acceptance:** with the limiter **enabled**, registrations from the Anthropic CIDR are not throttled at production-plausible aggregate volume, a non-exempt IP is still throttled at the limit, the CIDR branch is proven **reachable** through the `Fly-Client-IP` plumbing, the bucket store is bounded, the code carries **no** CIMD fetch, and the multi-instance divergence plus the sibling-table growth bound are recorded against #2853 with an owner and date.
**Files:**
- Modify: `tortoise/hosted_api.py:20920-20922` (limiter) and the bucket store
- Modify/annotate: `tortoise/hosted_api.py:880-893` if the key derivation changes
- Test: new `tests/test_oauth_dcr_limit.py`

**Step 1: Write the failing tests — and make them able to fail.** `tests/conftest.py:22` sets `RATE_LIMIT_DISABLED=1` and `_check_ip_bucket_rate_limit` returns immediately when it is set (`hosted_api.py:2988`), so a naive test passes before implementation. Therefore: `monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)` and reset `hosted_api._OAUTH_DCR_BUCKETS` + `_OAUTH_DCR_LOCK` in a fixture (the pattern used by `test_agent_signup_idempotency.py:258`, `test_session_login.py:332`, `test_email_signup.py:265`).
   Assert **all** of: (a) the Nth registration from the Anthropic CIDR does **not** 429; (b) a non-exempt IP still 429s at the limit, with the 429 body/`Retry-After` shape Claude will parse; (c) an **aggregate** leg — many distinct IPs inside the CIDR at production-plausible volume (not just 21) — holds the intended policy; (d) a **plumbing** leg — with `TORTOISE_TRUST_FLY_CLIENT_IP=1` and `Fly-Client-IP: 160.79.104.11`, the request takes the Anthropic branch, and with the flag **unset** a spoofed `Fly-Client-IP` does **not** earn the exemption; (e) a **bound** leg — insert `> max_entries` distinct fresh keys and assert the store stays bounded and lookups do not degrade (fake clock to keep it fast), because the current stale-only pruning never evicts while entries are fresh and the endpoint is unauthenticated.
**Step 2: Run** → **FAIL**.
**Step 3: Implement** with a **stated** policy: the concrete new limit/segmentation for the CIDR, the anonymous fallback limit, and a hard cap/eviction for the bucket store (LRU or reject-new-key rather than stale-only pruning). Keep abuse protection for genuinely anonymous traffic. Do **not** implement CIMD (that is #2847 — an attacker-supplied-URL fetch needing validation, redirect policy, size/time caps and a cache).
**Step 4: Record the accepted limitations** in the code comment and the PR: the limiter is a module-level in-process dict ⇒ `limit × machines`, reset on restart — neither global nor reliably per-IP; a shared out-of-process limiter is out of scope here and belongs with **#1677** (multi-worker scaling), which must be referenced by number. State that raising the per-process limit increases the multi-instance divergence.
**Step 5: Record the growth bound and its owner/date.** #2853 covers `oauth_clients`; `oauth_codes`, `oauth_refresh_tokens` and `oauth_access_tokens` grow monotonically too (`oauth_codes` has no purge path at all; the token tables have TTLs but no sweep), so either extend #2853's scope by comment or file a sibling issue, and put the **owner + absolute date** in the code comment (AC 6 requires both — cycle 1 only referenced the number).
**Step 6: Run → PASS. Commit.**

---

## Task 6: Wizard — key-less OAuth on the **live** Claude Desktop / Claude Web branches

**Intent:** The two Claude tabs render only a key-paste recipe behind a beta caveat that diverts users to other tabs, so the stated indicator ("connect with only the Server URL") is unreachable.
**Acceptance:** on the **live** connect step, the `claude-desktop` and `claude-web` branches render a key-less OAuth recipe (Server URL + sign-in → Authorize → org chooser), require no `harnessKey`, contain no `Request headers` / `Authorization: Bearer` line and no beta caveat, name org-admin `static_headers` as the fallback **without** telling a non-admin to paste a credential, and no longer divert to other tabs; `wizardConnectTripwire.test.js` + the node suite pass; the committed `dist/` bundle is **rebuilt and committed**.
**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` — the **live** branches `:6183` (claude-desktop) and `:6225` (claude-web), their `harnessKey ?` gates, and the Continue labels `:6258`/`:6277`
- Modify: `website/apps/dashboard/src/harnesses.js` — `HARNESS_STEPS['claude-web']:75`, `HARNESS_INTRO`, `HARNESS_INSTALL['claude-desktop']:156`, **`UNIVERSAL_COMMAND['claude-desktop']:379` and `['claude-web']:394`** (missing from cycle 1), and `HARNESS_OAUTH:300`
- Modify: `website/apps/dashboard/src/harnesses.test.js` (`:104-113` asserts those blocks name `Authorization` — that assertion must change) and `website/apps/dashboard/src/wizardConnectTripwire.test.js`
- Regenerate + commit: `website/apps/dashboard/dist/index.html`, `website/apps/dashboard/dist/assets/*.js`
- Copy: the three `tortoise-onboarding/SKILL.md` copies

**Step 1: Write the failing tests.** (a) Extend `wizardConnectTripwire.test.js` (it already slices the live connect step as source text) with assertions over the claude-desktop/claude-web live branches: no `Authorization: Bearer`, no `harnessKey ?` requirement, no beta-caveat string, and a real sign-in/Authorize step present. (b) Update `harnesses.test.js` for the changed `UNIVERSAL_COMMAND` blocks. (c) Assert the tab does **not** fall through to `wizardNoKeyAffordance`. **Do not rely on `harnesses.test.js` alone** — it asserts exported constants and cannot observe JSX (cycle-1 P1).
**Step 2: Run** the node suite → **FAIL**.
**Step 3: Implement** by lifting the recipe from the shipped `chatgpt` **copy** (`harnesses.js:84-92`) into the **live** branches. Note explicitly that `HARNESS_OAUTH` is **not** a live-render gate (single consumer at `main.jsx:6338`, inside `LEGACY_WIZARD_ARCHIVED = false`), so both the constant **and** the live JSX must change. Guard against the #2710 class (a copy button writing an empty string) by reusing the existing no-key affordance.
**Step 4: Decide and state** what happens to the legacy exports (`UNIVERSAL_COMMAND` claude-desktop/claude-web, `HARNESS_PERSIST` connector text) — retired, kept-but-keyless, or left stale — and make the test assert that decision rather than leaving it ambiguous.
**Step 5: Update the three `tortoise-onboarding/SKILL.md` copies** for the new recipe, keeping the `cmp` gate green. Route the four `skills/*/SKILL.md` edits through `skill-sync` (agent-infra) if they carry connector copy.
**Step 6: Rebuild and commit `dist/`.** `cd website/apps/dashboard && npm run build`, then commit `dist/index.html` + `dist/assets/*`. **This is a repo convention, not optional**: `dist/` is tracked (`.gitignore:23`), `.github/workflows/ci.yml`'s `dashboard-e2e` job asserts against the committed bundle ("NO npm build inside CI … a stale/missing dist commit red-fails the job = tripwire"), and `ci.yml:183-197` names "a src render change that forgets its dist rebuild runs against the previous bundle" as a known blind spot. The stale bundle also ships the old URL **and** the old beta caveat.
**Step 7: Run** the node suite (`harnesses.test.js` + `wizardConnectTripwire.test.js` + `wizardFlow.test.js`) → **PASS**; run `tests/e2e/test_dashboard_onboarding.py` locally against the rebuilt dist (it is opt-in via `RUN_DASHBOARD_E2E` and is **not** wired into CI — name it as the runtime gate).
**Step 8: Commit.**

---

## Task 7: Post-deploy evidence — cold-start, replay with fault + lifecycle legs, human E2E

**Intent:** The indicator is not verifiable inside a PR; the scoping absorbed a minimal cold-start measurement so the issue cannot close on a warm-only run, and #217 proves a passing challenge does not guarantee a client connect. Cycle 1 showed the replay's listed legs miss the OAuth lifecycle's real failure modes.
**Acceptance:** a recorded cold-start TTFB table for five endpoints; a replay that reaches `tools/list` on the canonical URL **and** exercises fault injection, warm-cache revocation, refresh concurrency and the real scope string; a human E2E script with an evidence template, named owner, target date, an existing label, the retry leg recording the entered URL form, the `frame-ancestors` check, the stale-client-state assertion, and a record of whether any GET probe hits the authless branch.
**Files:**
- Create: `docs/research/2026-09-10-2833-claude-connector-e2e.md`
- Create: `tools/oauth_claude_replay.py`

**Step 1: Replay driver.** DCR with the Claude profile (`redirect_uris: ["https://claude.ai/api/mcp/auth_callback"]`, `token_endpoint_auth_method: "none"`, `grant_types: ["authorization_code","refresh_token"]`) → `/oauth/authorize` → `/oauth/consent` → `/oauth/token` (S256 + `resource`) → `initialize`/`tools/list` on `POST <canonical>` → refresh rotation → expired-token replay. Assert no redirect on the POST, challenge present, all eight discovery checks 200. **Use the client's real scope string (`mcp offline_access`), not bare `"mcp"`.**
**Step 2: Lifecycle legs the cycle-1 replay missed.**
   - **Fault injection:** raise on the Nth control-plane `query` during `_issue_tokens` (the code is consumed **before** tokens are issued, and `hosted_api.oauth_token` catches only `OAuthError`, so a PostgREST 5xx today yields a bare 500 and an **unrecoverable** grant — the client retries the same code and gets `invalid_grant`). Assert: a coherent OAuth error (not a bare 500), the code is still redeemable or the client gets a distinguishable retryable signal, and **no new refresh/access row remains live** after the failure (the orphan-credential variant).
   - **Warm-cache revocation:** on the **same** app/middleware instance, authenticate with the `oat_` token, `POST /oauth/revoke`, then immediately re-POST and assert 401 — or assert the intentional grace window explicitly and bound it. (`TeamResolutionMiddleware` holds a 60 s LRU keyed by raw token; the existing `test_revoked_oauth_token_401` builds a *fresh* app, so it cannot catch this.) Cover rotation the same way.
   - **Refresh concurrency:** two concurrent refresh requests with the same token → exactly one 200, and the loser's outcome must not force full re-authorization.
   - **GET-probe record:** record whether any GET/HEAD probe hits the documented-public branch (the trigger for Task 3's trip-wire).
**Step 3: Cold-start leg.** Scale to zero, then record TTFB + status for `/.well-known` × both documents, `POST /register`, `POST /oauth/token` on the first requests; emit a table. A warm-only pass must not be accepted as closure evidence — scoping already observed 503s and 12–25 s timeouts against the 10 s budget. #2848 (owner + date set) remains the full envelope + `min_machines_running` decision.
**Step 4: Human script** following the **format** of `docs/research/2026-09-10-1701-chatgpt-e2e-script.md` (its own template is unfilled — format only). Include the retry leg (record the entered URL form; these are the users holding a slashed stored URL — see Task 2 Step 8: no re-add required), the `frame-ancestors` check, and the "if the AS sees no traffic it is a discovery failure" diagnostic with the `ofid_` capture.
**Step 5: Record owner** (`@daniel-ospina` until reassigned, also named in the PR body) **and target** (10 business days after merge), using the existing `deferred` label (no `needs-human-e2e` label exists — do not invent one).
**Step 6: Commit.**

---

## Task 8: Verification sweep

**Intent:** Prove the acceptance criteria before claiming done.
**Acceptance:** every mechanical AC passes; the non-mechanical ones exist as artifacts; parity gates pass; `dist/` is fresh and consistent.
**Files:** none (verification only)

**Step 1: Python suites** — `uv run pytest tests/test_oauth_mcp.py tests/test_mcp_server_auth_modes.py tests/test_mcp_http.py tests/test_hosted_api.py tests/test_onboarding_variants.py tests/test_harness_mcp_config.py tests/test_init_apikey_validation.py tests/test_oauth_dcr_limit.py -v` with `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`.
**Step 2: Dashboard suites** — the node runner over `harnesses.test.js`, `wizardConnectTripwire.test.js`, `wizardFlow.test.js`; then `tests/e2e/test_welcome_page.py` (**`tests/e2e/test_welcome_page.py:91` was missing from the cycle-1 sweep** — either run it or state that it needs the e2e lane).
**Step 3: Parity gates** — `cmp` the three `tortoise-onboarding/SKILL.md` copies; `cmp` public↔dist for the other three skills. Count correctly: **9** tracked skill files matter here, not 12 (only `tortoise-onboarding` has a source→public→dist chain in this repo; the other three are public+dist only, and the `skills/` tree is the agent-infra symlink).
**Step 4: `dist/` freshness** — assert the committed bundle contains the canonical URL and none of the removed caveat strings.
**Step 5: Walk the scoping's 10 acceptance criteria** one by one; for each, name the test or the artifact that satisfies it. Any AC with neither → the work is not done.
**Step 6: Report `main`'s pre-existing reds separately** (#2834, `test-carve-out`) — do not attribute them to this change.

---

## Plan-review disposition — NOT CLEAN (requires human decision)

<!-- plan-review: cycles=2, status=stalled, version=2.3.0 -->

**Cycle log**

| Cycle | Reviewers | P0 | P1 | P2 | New vs previous |
|---|---|---|---|---|---|
| 1 | 3 (Structural+Efficiency, Integration, Failure-Mode) | 2 | 15 | 9 | — |
| 2 | 2 (Structural+Efficiency, Failure-Mode) | 1 | 13 | 11 | 5 P1 + 11 P2 genuinely new |

**Exit reason: `honest-stuck`** — issue count did not decrease and cycle 2 introduced
genuinely new findings rather than refining cycle-1 ones. Per the protocol this escalates to
the human **without** continuing the loop; it does not auto-exit with a clean status.

**Cycle-1 P0s: both FIXED (substantive).** P0-A (Task 2's unpassable test + self-firing
re-scope hatch) and P0-B (Task 6 built on `LEGACY_WIZARD_ARCHIVED` dead code) were verified
fixed against the code by an independent reviewer, including the line-level facts
(`main.jsx:2317` `= false`, `:6338` single consumer, `:6009`/`:6072` live step, `:5675`
`harnessKey`).

**Cycle-1 P1s: 19 of 22 fixed substantively; 1 was a restatement.**
- `P1-D` (**NOT fixed — restatement**): Task 5 says "put the owner + absolute date" for
  #2853 and supplies neither. #2853 has no assignee and no date. This is the *same*
  silent-rot defect cycle 1 raised, restated.
- `P1-C`, `P1-L`, `P1-O` partially fixed (see new items below).

### Open items — must be resolved before execution

**P0-2 (new, cycle 2) — an assertion with no implementation behind it.**
The consumed-code / orphan-credential failure mode is real and **unfixed in the code**:
`oauth.py::exchange_auth_code` calls `_consume_code` (`:508`) *before* `_issue_tokens`
(`:620`, `:633`), and `hosted_api.py::oauth_token` (`:21107`) catches only `OAuthError`, so a
control-plane 5xx yields a bare 500 **and** a permanently burned grant. The plan assigns the
assertion to `tools/oauth_claude_replay.py` — a post-deploy network replay — where the failure
**cannot be injected** (and Step 5 defers it 10 business days). No task modifies
`exchange_auth_code` / `_issue_tokens` / `oauth_token`.
→ **Disposition: this is implementation work, not a verification leg.** Choose one:
 (a) add a remediation Task (roll back the refresh row when the access insert fails; catch
 non-`OAuthError` at the boundary and return `temporarily_unavailable` with the code restored)
 plus a pytest fault-injection test parametrized over the failing step; **or**
 (b) file it as its own issue against the #524/#1701 code and drop the leg from this plan.

**P1-NEW-4 (new, cycle 2) — the OAuth path is unreachable for non-admins.**
`main.jsx:6069` gates the harness block on `isOwnerAdmin` (`:4355`); non-admins land on the
paste-a-key row (`:6266-6271`). An OAuth connect needs no mint, so the key-less recipe should
render for members too. AC 5 explicitly contemplates a non-admin.
→ **Disposition:** add the gate to Task 6 Files/Steps and assert it in the tripwire, **or**
record it as a deliberate deferral with a filed issue. Do not leave it silent.

**P1-NEW-3 (+ P1-D) — Task 5's DCR policy has no numbers.**
"Implement the concrete new limit/segmentation" with acceptance "holds the intended policy at
production-plausible volume" is unfalsifiable (exempt-to-infinity satisfies it) — the same
class as the cycle-1 "the TDD loop could not fail" defect, reintroduced in a new leg.
→ **Disposition:** fix the numbers in the plan (CIDR aggregate cap, anonymous per-IP cap,
store cap + eviction policy) **and** the #2853 owner/date. If the number is genuinely a
product decision, make it a **human gate before Task 5 Step 1**.

**P1-NEW-1 / R2-misclassification — `main.jsx:887` and `website/docs.html:172` are KEYED surfaces.**
- `main.jsx:887` is inside `wizardPromptText`, whose call sites are `pi`/`cursor` (`:6125`) and
  `claude`/`codex` (`:6175`) — no Claude-connector branch consumes it.
- `website/docs.html:168-177` is a `.mcp.json` block for Claude Code / Cursor (`"Authorization":
  "Bearer tt_YOUR_KEY"`); only the `:180` callout is connector-facing.
Listing these as connector surfaces would make Step 1(a)'s "no connector literal ends in `/mcp/`"
assertion force them off-slash, re-creating the docs↔code drift (#2849 class) in the opposite
direction, with no test able to catch it (the plan's own test defines them as connector surfaces).
→ **Disposition:** split both files — `main.jsx:887` → keyed group; `docs.html:172` → keyed group,
`docs.html:180` → connector group; extend the source-scan test to assert the **pair**.

**P1-NEW-5 — served SKILL.md equality is asserted by parity only.**
`cmp` proves the copies agree *with each other*, never that they equal PRM's `resource`. A
consistently-wrong value across all 9 tracked files passes every gate (and the `skills/` symlink
tree has no parity gate at all — it differs today).
→ **Disposition:** extend the source-scan test to read the 9 tracked SKILL.md files and assert
each carries the canonical connector literal.

**P1-R2-2 — warm-cache revocation also assigned to the unexecutable vehicle.**
`mcp_auth.py:152` caches by raw token for 60 s (`:201-220`), so `oauth_mcp`'s `revoked_at` check is
bypassed; the existing `test_revoked_oauth_token_401` builds a **fresh** app so it cannot observe
it. A rolling/multi-machine deploy cannot guarantee the same warm process.
→ **Disposition:** add `tests/test_mcp_token_cache.py` on a **single** app instance (revoke, then
re-POST → 401; repeat for rotation; patch `time.time` to pin the 60 s bound).

**P1-R2-3 — bucket-store eviction: both candidate policies re-open a bypass.**
LRU lets an attacker flood until their own bucket is evicted (limiter reset); reject-new-key fails
open for unknown keys (an IPv6 /64 is 2⁶⁴ keys). Step 1(e) only asserts "bounded", so both ship green.
→ **Disposition:** pick deterministic eviction that cannot evict an active key (reject-new +
shared overflow bucket), and assert 429 **after** the bound plus non-O(n) lookups.

**P1-R2-4 — Task 5 edits a SHARED rate-limit primitive (~15 call sites).**
`_check_ip_bucket_rate_limit` serves `/v1/register`, agent/email signup, session login,
export/team-delete, pack manifest, invites, claim, volunteer context. Task 8's sweep omits the 11
suites that exercise those with the limiter enabled, so a shared-primitive regression passes.
→ **Disposition:** give the DCR limiter its **own** bucket store (preferred) and leave the shared
function untouched; otherwise add all 11 suites to Task 8 Step 1.

**P1-R2-5 — the `offline_access` test is in the one place that cannot reject it.**
`/oauth/consent` (`hosted_api.py:21055`) forwards `scope` with **no validation** and
`validate_authorize_params` has no `scope` param, so an authorize→consent→token round-trip passes
unconditionally. The rejection point is `register_client` (`oauth.py:341-345`) — and if we advertise
`offline_access` without accepting it in DCR, **Claude's registration 400s before the journey starts**.
→ **Disposition:** move the test to DCR, parametrized over `scope=None | "mcp" | "mcp offline_access"`;
state the decided outcome; and add "record Claude's real DCR request body" to the Task 7 evidence template.

**P1-R2-6 — `wizardConnectTripwire.test.js` currently asserts the OPPOSITE of Task 6.**
Its `#2710` loop (`:103-115`) *requires* `) : (…wizardNoKeyAffordance` in all four harness blocks, and
`tests/e2e/test_dashboard_onboarding.py:684` parametrizes Claude Desktop/Web with a visible
`Create an API key` button. Task 6 must **modify** both, not "extend" the tripwire; neither file is in
Task 6's Files list.

**P1-R2-7 — `dist/` staleness has no regression net.**
Verified: CI's `dashboard-e2e` job (`ci.yml:729`, `:826-834`) runs only `test_keys_table_mixed.py` and
`test_graphs_management.py` — nothing compares `dist/` to a fresh build, so a **stale** dist passes
(a missing one fails). Task 8 Step 4 is a manual check.
→ **Disposition:** add a committed node test that reads the shipped `dist/index.html` +
`dist/assets/*.js` and asserts the canonical URL is present and the beta caveat is gone.

**P1-R2-8 — the refresh-loser contract is asserted but not implemented.**
The plan requires "the loser must not be forced into full re-authorization", but the implemented
behaviour is the opposite (`oauth.py:650-663` → `400 invalid_grant` "Refresh token already revoked").
Code-redemption concurrency (`_consume_code` double-issue) is untested too.
→ **Disposition:** decide and implement the loser's contract, then pin it — or relax the assertion to
"the client's documented recovery is one re-auth" and say so.

### Remaining P2s (recorded, not blocking)
`:6225`→`:6219` line fix; "extend" vs modify the tripwire loop; the cross-language drift test is
tautological as written (export the constant, assert from Python); the `<base>` origin for the
challenge is unspecified (`mcp_auth.py` has no base helper and `request.base_url` inside the
`/mcp`-mounted sub-app carries `root_path=/mcp` — assert the emitted URL explicitly, not merely
that some URL 200s); `tortoise/mcp_server.py:631` belongs in the keyed group and the dated `docs/**`
literals need an explicit deferral clause so AC 1's deferral arm is satisfied; AC 10's "re-pinned"
vs Task 2's "unchanged" needs one reconciling sentence; Task 1's traversal probes are defeated by
httpx's pre-ASGI dot-segment normalization, `DELETE /mcp` is missing, and the rewrite's exact-match
semantics (`/mcpfoo`, `/Mcp`) are unpinned; `fly.toml`'s `TORTOISE_TRUST_FLY_CLIENT_IP` is not pinned
by a config test despite the CIDR exemption depending on it; Task 1 Step 6's bucket assertion cannot
fail (`_bucket_key` never consults the path outside `path_limits`, and `RATE_LIMIT_DISABLED` leaves the
global app's limiter `_disabled=True` under pytest); the `frame-ancestors` check names no remediation
artifact (`tests/test_oauth_mcp.py:410` pins the CSP); the Dependency Map's Task 2←Task 4 edge is
unexplained.

### Structural conclusion — the tier is wrong

The plan spans 8 tasks over ~35 files: a new raw-ASGI middleware, a rate-limiter policy change with a
new segmentation and a shared-primitive hazard, an auth-boundary header contract, a UI rewrite across
4 surfaces plus a committed `dist/` rebuild and a 3-tree skill sync, a docs checklist, a post-deploy
cold-start measurement, a new replay tool, a human E2E script — **and now at least one genuine
authorization-server bug fix (P0-2) that the scoping did not account for.** Two design decisions are
still deferred to implementation time. This is project-shaped work carrying a `complexity:standard`
label.

**Recommended resolution (human decision):** re-tier #2833 to `project` and decompose along the
natural seams — (A) **server conformance** (Tasks 1, 3, 4, 5), (B) **wizard OAuth path** (Tasks 2, 6),
(C) **AS robustness** (P0-2 as its own issue against the #524/#1701 code), (D) **post-deploy evidence**
(Task 7), then Task 8 per-child. Alternatively, keep one issue and accept the `stalled` review status
with the open items above explicitly approved as-is.
