---
title: "API keys table should show real product keys — not session credentials"
type: engineering
subjects.team: epistemic-team
domain: platform
doc_status: draft
aboutSubjects: tortoise
aboutObjects: tortoise-api-keys, tortoise-dashboard
created: 2026-09-02
---

# Research: API keys table — session credentials vs real product keys

**Date:** 2026-09-02
**Request:** "Why do I see ephemeral keys / an active key I can't delete / revoked clutter on the API keys page? Research before scoping: the end state should be a conventional API-keys table — real keys used to access the product, no session credentials."

## Problem reframing (Step 0)

- **Reframed:** The user wants the API-keys surface to match the conventional mental model (table = machine credentials you deliberately create, revoke, rotate, label). We must know which live consumers depend on the auto-minted bootstrap ("ephemeral · session") key and what breaks if the browser stops minting/displaying it.
- **Domain classification:** Complicated (engineering). Internal mechanism is enumerable via code; external convention is checkable against SaaS practice.

## Internal findings

### Key producers (all mint sites)

| Flow | Endpoint | `created_via` | Durable? |
|---|---|---|---|
| "Create key" button | `POST /v1/team/keys` | `provisioned` | Durable, counts vs cap |
| Login auto-mint | `POST /v1/session/key` (purpose=bootstrap) | `bootstrap` | 24h expiry, cap-exempt, max 3 active |
| "Recover key" flow | `POST /v1/session/key` (purpose=recovery) | `recovery` | Durable, counts vs cap, auto-revokes oldest at cap |
| Welcome first-timer | tenant-provision + `reveal_api_key` RPC (A13 reveal-once) | `provisioned` | Durable |
| Agent signup (zero-email) | `/v1/agent/signup` | `provisioned` | Durable |
| Selfhost CLI / embedded | SDK `apikey_create` / `tortoise key create` | NULL (legacy) | Durable |
| Recovery RPC (supabase) | `recover_team_key` | `recovery` | Durable |

**Bootstrap keys are minted ONLY by the session-key endpoint** (`tortoise/hosted_api.py` `session_key`, both lanes). Dashboard caller: `mintSessionKey('bootstrap', …)` in `main.jsx`; also referenced as the keyless-team first-key path by SDK `apikey_create` docstring.

### Hosted dashboard browser auth (measured; fresh-session verifier reviewed)

- **37 of 38 `api()` call sites pass `useSession: true`** (session JWT). Caveat (verifier P2): the `api()` helper merges `opts.headers` LAST, so `mintKey` (POST /v1/team/keys) and `loadBackups` (GET /backups) send the tt_ key when one is held — but those endpoints are dual-auth, so this is *preference, not dependency*.
- `GET /v1/sessions/{id}` (fetchSessionDetail, `main.jsx:3650`) is the only purely key-authed call — **dead code** (never invoked; verifier P2).
- `loadAll` (Team/Keys/Sessions cards) is session-authed; `#1828` comment: *"overview reads ride the SESSION JWT … instead of the freshly-minted bootstrap key — the Team / Keys / Sessions cards render without a key mint."*
- The bootstrap key's remaining role in the browser: localStorage seed (`tortoise_api_key`), default Authorization fallback, and — importantly — the **copyable credential embedded in the onboarding wizard's setup commands** (`harnessKey = welcomeKey || apiKey`, `main.jsx:3907`).

### The keys table

- Data source `GET /v1/team/keys` (`list_api_keys`) returns **ALL api_keys rows incl. revoked, bootstrap, expired-until-swept** — no server-side filter. Session-authed.
- Client-side classification (`sessionKey.js`): `created_via='bootstrap'` OR `expires_at` set → **"ephemeral · session"** (no rename/toggle/delete); `revoked_at` set → "revoked"; else "active".
- `isActiveKey` guard: the key the current browser session is using (key_prefix match) is never deletable — even when durable.
- **Revoked-cruft sources:** (1) reconcile sweep `POST /v1/internal/reconcile` (external cron) revokes expired bootstrap keys → they show "revoked" forever; (2) recovery-mint at-cap auto-revoke of the oldest key; (3) user revoke. **No hard delete on the key-lifecycle path** (rows only hard-deleted by post-grace team purge and provision-rollback; verifier P2 scoping).

### Selfhost

- Selfhost quickstart = CLI/MCP/embedded consumers; registry keys, **no Supabase session concept** (`docs/quickstart-selfhosted.md`). The web dashboard is a hosted artifact. The `/v1/session/key` endpoint + bootstrap purpose must remain (registry lane needs it for keyless teams; heavily tested). Any browser-mint removal is hosted-only.

### Precedents already in the codebase (toward the desired end state)

- **#1716 keyless provisioning** — "A minted key whose plaintext is never returned is an unrecoverable dead credential." Web onboarding should not mint unseen keys.
- **#1828** — overview reads moved to session JWT ("cards render without a key mint").
- **#1498/#1506/#1511** — session-first dashboard, key→session server-side exchange.
- **#1831** — wizard already falls back to a "create a key" message when no key exists.

### Contract tests pinning current behavior

- `tests/test_session_key_http.py` (registry lane): bootstrap 24h expiry, 3-active cap, cap-exemption, expired-doesn't-count; recovery persistent + at-cap auto-revoke tiers; 402 fail-closed; guards; round trips; concurrency.
- `tests/test_auth_flip.py` (supabase lane): same mint contracts via `rest_client`/`authed_user`.
- `tests/test_cli_team_keys.py` (CLI durable keys), agent-signup suites, dashboard e2e (keys classification, gates).

**Implication:** the mint *endpoint* contract is safe to keep; what would change is (a) the dashboard's *automatic* bootstrap mint at login and (b) keys-list *display* of session rows. Those are pinned mostly by dashboard e2e tests.

## External findings

**Conventional API-key UX (multiple independent sources):**
- Stripe Developers → API keys: create / reveal / expire / **rotate**; secret keys shown **once**; lost keys cannot be retrieved. The page is a lifecycle surface for user-managed credentials.
- GitHub: personal access tokens / fine-grained tokens — user-created, labeled, scoped, revocable.
- General auth guidance (dev.to, Tyk, LoginRadius, Momento, security.stackexchange): **sessions/tokens for browser; API keys for machines**; keys are long-lived, shown once, stored hashed; "using the wrong credential type creates security gaps"; API keys are *less* secure than scoped tokens and should not be the browser credential.
- **No mainstream SaaS mixes auto-generated session/system credentials into the user's API-key table.**

Confidence: **High** on the session-vs-key separation and keys-page conventions (≥3 independent sources). No contradicting source found.

## Epic #1976 alignment finding (added 2026-09-02)

While aligning with the in-flight onboarding epic #1976 (agent-driven onboarding, 12 waves), a concrete gap surfaced at the wizard connect step (W1 #1997 / W2 #1998):

- W2's connect step renders `UNIVERSAL_COMMAND[harness](key)`; `key` = the browser's current `apiKey` state.
- For **returning users** that is the **24h bootstrap key** minted at login → an agent configured with it (literal in Claude Desktop/Web configs, or as `TORTOISE_API_KEY` env value) **stops authenticating within 24h**.
- W2's env-var indirection solves *leakage into config files*, not *durability*.
- Fix folded into W2 (see comment on PR #2161): connect step must source a **durable** `provisioned` key (mint at connect, shown once, or route to the API Keys tab).

## Answers to the research questions

**Q1 — What breaks if the hosted dashboard stops minting bootstrap keys?**
Nothing live: the only purely key-authed browser call (`/v1/sessions/{id}` detail) is dead code. The wizard's copy-key moment must present a durable key instead (being folded into W2 #1998).

**Q2 — What breaks if session creds are hidden/filtered from the keys table?**
Nothing functional — the list is session-authed already; classification is client-side. Display-only. The `isActiveKey` guard stays correct while the browser holds any credential.

**Q3 — What are the "real keys" that belong in the table?**
Durable keys: Create-key button (`provisioned`), agent signups (`provisioned`), welcome reveal (`provisioned`), CLI/SDK (`NULL` legacy), recovery (`recovery`).

**Q4 — Selfhost?**
No browser session exists there. Keep `/v1/session/key` + bootstrap purpose. Any browser-mint removal is hosted-only.

## Recommendation

Two workstreams:
- **#2166 — Keys-table UX (UI model, ships first, no deps):** table shows only durable keys; session creds auto-cleared/hidden or in a separate informational section; plain labels; revoked behind a filter.
- **#2167 — Browser auth model:** drop the hosted dashboard's login-time bootstrap mint (browser = session JWT only); sequence after #1997/#1998 land; keep the endpoint + recovery + selfhost lane.

## Open questions (for scoping)

1. Hosted first-timer welcome: does it still reveal a durable key post-#1716 (provisioning RPC keyless for onboarding sub-teams)? Verify the tenant-provision edge function + `reveal_api_key` live.
2. Which non-dashboard callers use `POST /v1/session/key` purpose=bootstrap (SDK apikey_create path, MCP bridge, e2e fixtures)? Dependency audit before removing the dashboard caller.
3. Revoked-row retention: desired state = filter + optional hard-delete/archive of rows older than N.
4. Confirm no web UI ships with `tortoise serve --http` (docs say CLI/MCP only) → #2167 is hosted-only.

## Source confidence summary

| Claim | Tier | Sources |
|---|---|---|
| Dashboard ~session-authed; no live purely-key-authed call (the one found is dead code) | High | Code measurement + fresh-session verifier |
| Bootstrap keys minted only by session-key endpoint | High | Code |
| Keys list returns all rows incl. revoked/bootstrap; no lifecycle hard delete | High | Code |
| Revoked cruft from reconcile sweep + recovery-cap auto-revoke | High | Code |
| Conventional UX: sessions for browser, keys for machines; keys page = create/reveal/rotate; no session creds in table | High | Stripe, GitHub docs + 4+ auth guidance sources |
| Hosted welcome reveals durable key post-#1716 | Low (unverified) | Needs live check (Open Q1) |
| W2 connect step uses browser apiKey (24h bootstrap for returning users) → durability gap | High | Code (harnessKey ~3907; W2 plan rev 2) |

*Memory system offline — claims not persisted to epistemic graph.*

## Raw Notes

- **2026-09-03T23:20:43** [precedent] ## Axis Research — #2167 scoping (2026-09-04, dedup + justified-skip)
> Architecture (standard): Deduplicated — covered by this brief's External findings (session-vs-key separation, browser-auth convention, no-SaaS-mixes-session-creds-into-key-table, keys=create/reveal/rotate) + Recommendation (two workstreams). Codebase-first precedent scan (3+): in-repo migration precedents #1511 session-first, #1828 dual-auth reads, #2002-W6 sessions-detail dual-auth, #2166 durable-only table — the 'endpoint stays, dashboard caller removed' pattern is how prior issues shipped (server untouched). No novel pattern requiring external import.
> Security (standard): Deduplicated — covered by this brief's External findings (API keys less secure than scoped tokens, wrong-credential-type gaps, shown-once + hashed storage) + issue fail-safe (endpoint stays for external consumers). In-repo security precedent: #1148 dashboard-login gate session-authed by design; ungated dual-auth reads run least-privilege dormancy gates; reconcile sweep revokes expired bootstraps. Residual risk = key-over-session header shadow on dual-auth endpoints — internal, code-verified, no external fact needed.
> UX (low): No fire (connect-step copy owned by #2211; #2167 copy deltas = removal of dead mint-failure copy).
> Justified-skip basis: both medium+ axes covered at sufficient granularity by prior-brief sections cited; no third-party deps; no novel pattern. External research not demonstrated — skipped per activation rule.
- **2026-09-03T23:26:53** [precedent] ## Errata — 2026-09-04 (post-#2167-scoping verification)
1. Producer-table correction: the dashboard has FOUR bootstrap-mint callers, not one (login mount L2679, switchTeam L3486 + L3505 401-re-mint, revokeKey re-mint L4103). The 'dashboard caller: mintSessionKey in main.jsx' line is incomplete.
2. 'GET /v1/sessions/{id} (fetchSessionDetail) is dead code' is WRONG as of #2002-W6 — it is a LIVE Settings transcript view (onViewSession L5569) and session-authed client-side (main.jsx L4236) against a dual-auth endpoint (hosted_api.py L6960). Indicator 3 of #2167 is satisfied at baseline.
3. loadBackups (/backups, main.jsx L3761) is team-scoped by the KEY header, not ?team_id= — auth-dual but team-scoping-single. In the #2167 zero-key default state it must pin ?team_id= in session mode (multi-membership correctness).
4. Line numbers in this doc predate 2026-09-04 main (now 19a50ace) — do not trust them for future sessions; re-verify.
