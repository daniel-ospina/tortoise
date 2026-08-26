# #1765 — Identity & profile: convergence plan (server-authority + invariant + phase-slice)

> Issue-scoping CONVERGENCE artifact for the confirmed problem: identity facts
> conflated across `teams.email` (globally-unique team attribute), the user
> anchor (`team_memberships.user_id` + GoTrue `auth.identities`), and
> `api_keys.created_by` (mixed attribution). Supersedes the divergence artifact
> (`2026-08-26-identity-linking-solution-approaches.md`) by SELECTING a winner.
> Verified against source 2026-08-26.

**Verdict — Family 2 (SERVER-AUTHORITY) as the core**, combined with:
- **Family 4 C2 invariant** ("identity flows never write `teams.email`") as a
  hard rule enforced by construction + a guard test; the C1 demotion is a
  probe-gated contingency with the consumer map already inventoried (§11).
- **Family 3's RPC mechanics** for the read path (one SECURITY DEFINER
  inventory RPC — pgTAP-testable, no GoTrue REST dependency for reads).
- **P1/P2 phase slice**: P1 = visibility + banner + username (zero auth-domain
  writes, no external toggle); P2 = linking/unlink (gated on the
  `enable_manual_linking` probe + ops flip).

Rejected-as-primary: Family 1 (client-first — advisory gates, no audit, and
its "no backend" premise is false: C3/C4 force a backend read anyway), Family 3
(data-plane — right read mechanics, wrong gate authority), Family 4 C1
(schema-first demotion — correct long-term model, wrong default: the
falsification probes are explicitly unconfirmed). "When each WOULD have been
better" is documented per family in §3.

---

## 1. Problem statement

The platform stores three different "who is the user" facts in three places:

1. **`teams.email`** — a globally-unique team attribute (`uq_teams_email`,
   `20260813000004` P3-FIX-S), written by signup (`provision_team`), the claim
   path (claim RPC Step 6, unconditional P1-FIX-B), and the onboarding email
   seam (`PATCH /v1/onboarding/state`, `hosted_api.py:8204`). It doubles as the
   signup idempotency key (`team_by_email`, `hosted_api.py:~3003`).
2. **The user anchor** — `team_memberships.user_id` + GoTrue
   `auth.identities` (provider rows). `#2085`: `updateUser({password})` adds
   password capability WITHOUT an `auth.identities` row, so password is a
   third, service-role-only signal (`auth.users.encrypted_password IS NOT
   NULL`).
3. **`api_keys.created_by`** — mixed attribution: session-minted keys carry the
   user UUID, provisioned keys carry `anon-*`/`reg-*` identity strings (which
   survive claim — claim does NOT migrate `created_by`), bootstrap keys carry
   `"api"`/NULL.

Every identity-adding operation collides with team-scoped uniqueness; there is
no user-level surface (`/v1/user` does not exist, C11), so the platform cannot
answer "what login methods does this user have, and are they one sign-in away
from lockout?"

**User deliverables:** (1) a profile page — add login methods (GitHub / Google
/ email+password over time) + set a username; (2) a dashboard recovery banner
for single-login-method users routing to the profile page.

**Outcome-quality bar (⛔ quality over convenience):** linking is the
highest-risk auth operation (account takeover vector) — its gates must be real
security properties, not UX polish. The banner must be computable AND honest
given #2085. The two-tab unlink race must be closed, not warned about.

---

## 2. Decision rationale — where the gates must LIVE

**Core claim: a security gate is only real if it is enforced by the code that
performs the state change, and the state change must be atomic with the check.**

In a browser-token architecture the client always holds the session token, so a
fully compromised client can call GoTrue directly regardless of our endpoints.
That fact defines what is achievable: server gates cannot stop a fully
compromised client, but they CAN (a) make the honest path correct, (b) make a
partial compromise (stale/leaked token, XSS that drives OUR endpoints)
fail closed, (c) make the floor atomic against races, and (d) leave an audit
trail. Family 2 maximizes all four; Family 1 achieves only (a) partially.

| Security property | Family 1 client-first | Family 2 server-authority | Verdict |
|---|---|---|---|
| Identity-floor on unlink (never zero ways in) | client pre-check + advisory floor RPC — **pre-state read, race survives** | **atomic** permit-reservation + server-performed delete + post-state verify | **F2** |
| Re-auth on link (C1) | client UX discipline only | server-verified session `iat` freshness at link-intent/commit | **F2** |
| Audit trail | none | `audit_events` rows on every link/unlink/username change (0002/0004 pattern, claim precedent) | **F2** |
| Honest banner (#2085) | needs backend anyway (see §4) | server-assembled from authoritative sources, one testable computation | F2 (both need backend; F2 owns the computation) |
| Two-tab race | **not closed** | closed (§6.3) | **F2** |
| Client surface | more JS to drift (C16 parity, monolith) | thin client, gates in Python (FakeControlPlane-testable) | **F2** |

The unlink floor is the deciding property. "Two tabs both read '2 identities',
both unlink, 0 remain" is a real lockout; the divergence doc's "server floor
RPC backstop" in Family 1 is still a pre-state read — the client can race
between the RPC check and the GoTrue delete. Family 2's design (§6.3) makes
check-and-delete a single server-side critical section.

---

## 3. Rejected alternatives — when each WOULD have been better

### Family 1 (client-first) — rejected as primary
- **Would have been better:** if linking were a non-security-critical
  experiment (internal tool, no real accounts) and the team accepted
  client-dominant gates + zero audit. Also if the codebase had no service-role
  backend seam — but C3/C4 destroy that premise: the banner needs
  `auth.users.encrypted_password` (not exposed by GoTrue's REST/admin API) and
  the C10 `api_keys` attribution join, both of which are backend-only reads. So
  "client-first" still ships a backend inventory endpoint, then adds a second,
  drifting JS computation of the same facts.
- **Adopted from F1:** the P1/P2 phase discipline (value-now, linking later
  behind an ops gate).

### Family 3 (data-plane) — rejected as primary
- **Would have been better:** if P2 linking were dropped entirely and the
  profile were read-only — a `SECURITY DEFINER` view over `auth.identities` +
  RLS `auth.uid()` + a `has_password()` function is genuinely simpler than a
  new endpoint, and it is the natural shape for a self-serve read path.
- **Adopted from F3:** the inventory is implemented as a single SECURITY
  DEFINER RPC (`user_identity_inventory`), so the whole read path is
  pgTAP-testable in SQL and independent of GoTrue REST version drift. The
  endpoint is a thin wrapper.

### Family 4 C1 (schema-first demotion) — rejected as default, kept as contingency
- **Would have been better:** if the falsification probes had ALREADY
  confirmed real multi-team/one-identity demand. The `user_emails` mirror is
  the correct long-term model — one queryable, RLS-able identity fact store,
  teams stop being the user table, the conflation dies at the root.
- **Why not default:** the probes are falsification-gated precisely because a
  false positive churns the entire signup path (one-way door: `uq_teams_email`
  drop + `team_by_email` re-anchor + claim Step-6 re-point + `reg-*` anchor
  re-point) for an unproven hypothesis. Shipping it unprobed violates the
  constraint register's own conditional gate.
- **Adopted from F4:** the C2 invariant ("identity flows never write
  `teams.email`") as a hard rule + guard test — this is how the current bug
  class stays dead — and the C1 consumer map documented as the probed
  contingency (§11). The plan ALSO fixes one root of the conflation in P1: the
  claim RPC migrates `anon-*`/`reg-*` `created_by` keys to the claimer (§7
  step 1), making `api_keys.created_by` attribution exact.

---

## 4. How the banner stays honest (#2085, C3, C10)

The banner inventory has three tiers; two of them are **not client-readable**:

| Tier | Source | Who can read |
|---|---|---|
| Identities | `auth.identities` | service role only (not browser-queryable without RLS/RPC, C14) |
| Password capability | `auth.users.encrypted_password IS NOT NULL` (**#2085**: `updateUser({password})` leaves no identity row) | service role only — **GoTrue's REST/admin API does NOT expose it** |
| Keys as credentials | `api_keys` × `team_memberships` | control plane, with C10 exclusion |

So the banner **must** be assembled server-side; there is no client-computable
shortcut. `GET /v1/user/identity` returns the assembled inventory AND the
server-computed `banner.show` decision — one authoritative computation, tested
in Python against `FakeControlPlane`, identical on the dashboard banner and the
profile page (no JS drift, no C16 parity risk).

**Floor semantics (open decision #1, resolved):** the unlink floor and the
banner's "ways in" count = **strict login methods** (`count(identities) +
(has_password ? 1 : 0)`). Keys are displayed as a separate "Dashboard
credentials" tier with the honest explanation that a key is not a login method
(it cannot recover a forgotten password, and it is revocable). Rationale: the
recovery banner exists because one method = one provider-outage / one
password-reset away from lockout; keys don't fix that. Documented in the UI
copy, not just the code.

**Honesty guardrails:**
- The profile's identity list reads `auth.identities` via the RPC — **never**
  `teams.email` (a team attribute can differ from identity emails; `reg-*`
  identities exist without `auth.users` rows, C15).
- The banner does not render in the anon/claimable state (the #1148 Protect
  gate is the dominant surface) and must not promise a fix for the #1716
  keyless-anon cohort (C9) — those users are routed to the claim path, which
  the banner does not claim to replace.

---

## 5. Phase-slicing: P1 / P2 boundary

**Split is better than ship-everything.** Reasons:

1. **P2 is blocked on external state (C5).** `supabase/config.toml:173` sets
   `enable_manual_linking = false`; hosted state unknown; GoTrue returns 422
   when off. Shipping linking before the ops flip ships a feature that 422s.
2. **P1 has zero auth-domain writes** (bounded read-only inventory + username
   metadata). It cannot break signup, claim, or provisioning; P2 is the
   highest-risk operation in the auth stack and deserves its own review gate.
3. **P1's `GET /v1/user/identity` is exactly the substrate P2's gates build
   on** — the floor checks, post-state verification, and capability probe all
   read the same inventory. The slice is not an artificial seam; it is the
   dependency order.

| | P1 (this issue) | P2 (next issue, gated) |
|---|---|---|
| Inventory endpoint `GET /v1/user/identity` | ✅ | reused |
| Recovery banner + profile tab + username | ✅ | — |
| `anon-*`/`reg-*` `created_by` migration on claim | ✅ | — |
| Linking (add OAuth, add email+password) | — | ✅ |
| Unlink with atomic floor | — | ✅ |
| `enable_manual_linking` ops flip + probe | runbook only | ✅ hard gate |
| `user_unlink_permits` + `reserve_unlink` RPC | — | ✅ |
| Demotion probes + C1 branch | map documented (§11) | runbook |

---

## 6. Proposed solution

### 6.1 P1 architecture

```
[dashboard: RecoveryBanner]          [dashboard: ProfileTab (new tab)]
        │  GET /v1/user/identity           │  GET /v1/user/identity
        ▼                                  ▼
[hosted_api]  GET /v1/user/identity ──► user_identity_inventory(p_user_id)
[hosted_api]  POST /v1/user/username ─► reserve_username(p_user_id, p_username)
        │  (SECURITY DEFINER RPCs, service_role-only — C18 precedent)   │
        ▼                                                              ▼
auth.identities · auth.users(encrypted_password) · user_usernames   api_keys × team_memberships
api_keys(created_by, C10-filtered)                                 (keys tier)
```

**`GET /v1/user/identity`** (session-authed via existing `get_current_user`;
registry/selfhost mode → `{"unsupported": true}` — `claim_status` precedent):
```json
{
  "username": "daniel" | null,
  "identities": [{"provider": "github", "email": "…", "created_at": "…", "last_sign_in_at": "…"}],
  "has_password": true,
  "ways_in": 2,
  "credentials": {"keys": 3},
  "banner": {"show": true, "reason": "single_method"},
  "linking_enabled": false
}
```
`linking_enabled` mirrors an ops-controlled env flag (`TORTOISE_MANUAL_LINKING_ENABLED`)
that is flipped together with the GoTrue setting; P1 always returns the env
value (false default), P2 hard-gates on it and additionally fails closed on any
422 leaked by GoTrue.

**`POST /v1/user/username`** — two-phase with compensation:
1. `reserve_username` RPC: validate `^[a-z0-9_]{3,30}$` → INSERT into
   `public.user_usernames` (unique index → `username_taken`, mapped to 409;
   invalid → 422). The INSERT is the atomic claim — the unique index is the
   enforcement backstop, so two concurrent picks cannot both win.
2. Publish via GoTrue admin `PUT /admin/users/{id}` `user_metadata.username`
   (new `_gotrue_admin_update_user_metadata` beside `_gotrue_admin_get_user`
   :3237). On publish failure → `release_username` compensation + 502.
3. Audit `username_update` (`audit_events.detail`, 0004 pattern); per-IP rate
   limit (claim limiter precedent, 10/h).

**Username vs `display_name` (#1691, C7):** two namespaces, one rule —
`username = user_metadata.username`, single-writer (the profile page); the
wizard keeps writing `display_name` (graph Subject sync, main.jsx:578-581) and
NEVER touches `username`. Display precedence `username > display_name >
email-prefix` via one shared helper applied at every display site (main.jsx:556,
618-632, 839, 981-982) + a unit test — this is the "one-line miss resurrects
the clobber" guard.

### 6.2 P1 key flows

**Banner flow:** dashboard mount fetches `GET /v1/user/identity` alongside the
team load → if `banner.show` and not welcomeMode/claim-gate → render
`RecoveryBanner` above the tab content ("You have one way to sign in…") with
CTA → `setTab('profile')`. Server computes `show`; the client only renders.

**Username flow:** profile tab loads inventory → username editor prefilled →
submit → `POST /v1/user/username` → 200: local state + display sites update;
409: inline "username taken"; 422: inline validation. Banner unaffected
(username is not a way-in).

**Profile tab location (C12):** the dashboard is a router-less 3199-line
monolith with a working tab state machine (`tab === 'overview'|'keys'|'graphs'|
'members'|'billing'`). A **new `profile` tab** matches the existing pattern,
reuses the session state + `api()` helper + auth bootstrap (a standalone page
would duplicate the intricate 850-1100-line mount logic), and needs no second
entry page / no C16 head-gate story. To stop the monolith growing unbounded,
the UI ships as a new file `website/apps/dashboard/src/profile.jsx` exporting
`ProfileTab` + `RecoveryBanner` + `displayNameFor` (vite imports from `src/`),
rendered from a 2-line switch in main.jsx. Members/Billing tabs are the
precedent for tab-scoped render blocks.

### 6.3 P2 key flows (design locked now, shipped gated)

**Add OAuth (GitHub/Google):**
1. User clicks "Add GitHub login" → client re-auth challenge (password prompt,
   or an OAuth re-login round for passwordless accounts) → fresh access token.
2. `POST /v1/user/identity/link-intent {provider}` with the fresh token →
   server verifies: session valid + `iat` within `REAUTH_WINDOW`
   (`TORTOISE_REAUTH_WINDOW_SECONDS`, default 900s) + `linking_enabled` →
   mints a signed one-time nonce (HMAC, bound to `user_id+provider`, TTL 120s,
   `used_nonces` table with unique PK — replay-safe across workers).
3. Client runs vendored supabase-js `linkIdentity({provider})` (C13, already
   shipped unused) — the interactive OAuth popup leg only GoTrue can perform.
4. `POST /v1/user/identity/link-commit {nonce}` → server verifies nonce,
   re-reads `auth.identities` via the inventory RPC, verifies: new identity
   belongs to the session user, provider matches, email verified
   (provider-verified, claim-path invariant pattern). If the new identity's
   email matches another team's `teams.email` → **adoption signal** (audited +
   surfaced on the profile, never automated — the C2 rule). Audits
   `identity_linked`, returns updated inventory.

**Add email+password:** uses **change-email + confirmation + set-password** —
NOT OTP-sign-in (which would sign the user into whichever account owns the
email — account-confusion vector) and NEVER admin-create (C6 — no
`auth.users` INSERT, so the `handle_new_user` placeholder never fires):
1. Fresh-iat gate (same as link-intent) → client `updateUser({email})` →
   GoTrue change-email confirmation (the C2 verified-email gate; confirmations
   enabled, config.toml:249).
2. On confirmed email → `updateUser({password})` (no identity row — #2085 by
   design, has_password is the tracked signal).
3. Server verifies post-state via the inventory RPC + audits. **Implementation
   verify (staging):** confirm the deployed GoTrue creates/updates a
   `provider='email'` identity row on change-email confirmation; if the version
   doesn't, the fallback signals are `auth.users.email` (confirmed) +
   `has_password` — documented and tested either way.

**Unlink (the race-closing design):**
```
[tab A] POST /v1/user/identity/unlink {identity_id}   [tab B] same, concurrent
        │                                                       │
        ▼                                                       ▼
reserve_unlink(p_user_id, p_identity_id)  —  ONE SECURITY DEFINER transaction:
  INSERT INTO user_unlink_permits(user_id, identity_id)
  SELECT … WHERE floor_count(p_user_id) - pending_permits(p_user_id) >= 2
  AND p_identity_id belongs to p_user_id
  ── tab A: identities=2, permits=0 → 2-0≥2 → permit INSERTED
  ── tab B: identities=2, permits=1 → 1≥2 FALSE → 409 UNLINK_IN_PROGRESS
        │ (permit held — the in-flight marker)
        ▼
server performs GoTrue DELETE /user/identities/{id} forwarding the
VALIDATED session access token of THIS request (strictly bound: only this
request's token, narrow allowlist, never stored — BFF pattern)
        │
        ▼
post-state verify: inventory RPC → identity gone AND ways_in ≥ 1 (invariant)
        │
        ▼
consume permit (DELETE), audit identity_unlinked (identity_id, provider,
email, remaining ways_in)
```

**Why permit-reservation instead of an advisory lock (design decision):** the
control plane is PostgREST-only — a `pg_advisory_xact_lock` inside an RPC
releases when the RPC transaction commits, which is BEFORE the GoTrue HTTP call
(no direct DB session to hold a lock across it). The reservation pattern
(pending permits REDUCE the effective floor inside the same atomic
`INSERT … SELECT`) makes check-and-remove atomic without a lock spanning the
remote call. Stale permits (crash between reserve and consume) are cleaned by a
sweep and block new unlinks for that user until cleaned — surfaced as "an
unlink is in progress", never silently ignored. This closes the two-tab race
for honest clients and is itself pgTAP-simulatable (two concurrent reserves).

**GoTrue's native re-auth on unlink** (its own window, typically stricter than
ours) is documented as a complementary gate, not a replacement: the server
enforces OUR freshness window; GoTrue additionally enforces ITS window on the
delete it performs. Both must pass.

---

## 7. Ordered implementation steps (P1 — this issue)

1. **Migration `supabase/migrations/<new>_user_identity_profile.sql`**
   - `public.user_usernames (user_id uuid PK REFERENCES auth.users(id) ON
     DELETE CASCADE, username text NOT NULL UNIQUE, updated_at timestamptz
     DEFAULT now())`.
   - `user_identity_inventory(p_user_id uuid) RETURNS jsonb` — SECURITY
     DEFINER, GRANT service_role ONLY (C18): identities from
     `auth.identities`, `has_password` from `auth.users.encrypted_password`,
     keys tier from `api_keys` × `team_memberships` (attribution rule: `created_by
     = p_user_id` OR team-owner-claimed — see step 4 which makes this exact),
     C10 exclusions, `ways_in`, `banner.show`, username.
   - `reserve_username(p_user_id uuid, p_username text) RETURNS jsonb` /
     `release_username(p_user_id uuid, p_username text)` — validate +
     unique-INSERT + compensation, exceptions mapped by name
     (`username_taken`, `username_invalid`).
   - **Claim RPC additive step (conflation root-fix):** inside the claim
     transaction, `UPDATE public.api_keys SET created_by = p_user_id WHERE
     team_id = v_team_id AND (created_by LIKE 'anon-%' OR created_by LIKE
     'reg-%')` — post-claim the team has exactly one human owner
     (first-claim-wins), so the migration is unambiguous. Makes the keys tier
     exact.
   - pgTAP `supabase/tests/<new>_user_identity_profile.sql` (20260813000004
     test-file pattern): inventory counts/verification/C10/has_password both
     states; username valid/invalid/dup; claim `created_by` migration.

2. **`tortoise/supabase_control.py`** — seam helpers mirroring
   `team_email`/`update_team_email` (lines ~1204): `user_identity_inventory(cp,
   user_id)`, `reserve_username(cp, user_id, username)`, `release_username(cp,
   user_id, username)`; RPC-exception → Python-error mapping.

3. **`tortoise/hosted_api.py`**
   - `GET /v1/user/identity` (Depends `get_current_user`): Supabase mode → RPC
     (502 on seam transport, claim-status `unsupported:true` in registry mode).
   - `POST /v1/user/username`: validate → `reserve_username` (409/422) → admin
     PUT `user_metadata.username` (`_gotrue_admin_update_user_metadata` beside
     :3237) → compensation on failure (502) → audit + per-IP rate limit.

4. **Dashboard** — `website/apps/dashboard/src/profile.jsx` (new):
   `ProfileTab` (methods card: identities + password row + credentials tier;
   username card; linking disabled → fail-closed "contact support" affordance),
   `RecoveryBanner`, `displayNameFor(user)` precedence helper. `main.jsx`:
   profile tab button + 2-line render hook, banner render above tab content
   (state near :93, hidden in welcomeMode/claim-gate), 5 display sites
   (556/618-632/839/981-982) switched to `displayNameFor`.

5. **Tests (P1)** — see §8.

6. **Ops runbook note** (no code): `TORTOISE_MANUAL_LINKING_ENABLED` env
   documentation + the demotion probe queries (§11).

## 8. Testing strategy (mapped to existing infra)

| Test | Infra | Coverage |
|---|---|---|
| `tests/test_user_identity_inventory.py` | `FakeControlPlane` + `TestClient` (test_claim_endpoints.py pattern: env monkeypatch, RPC emulation) | 401 unauthenticated; 501/`unsupported` registry mode; inventory correctness (identities, has_password, ways_in, banner decisions incl. 0/1/2-method + password-only #2085 case); C10 exclusions; username 200/409/422 + audit rows + rate limit; **invariant guard: new endpoints never write `teams.email`** |
| `tests/fake_control_plane.py` (extend) | RPC dispatch by name (rpc() at :51 already routes `claim_membership`/`provision_team`) | emulation for `user_identity_inventory`, `reserve_username`, `release_username`, claim-`created_by` migration |
| `supabase/tests/<new>.sql` | pgTAP (20260813000004 pattern, docker lane) | inventory SQL correctness, username unique-INSERT atomicity, claim migration |
| `tests/e2e/hosted/test_14_profile.py` | Playwright, `RUN_HOSTED_E2E=1` hermetic server (test_13_claim pattern) | single-method signup → banner visible → CTA → profile tab → username set → persists after reload; two-method user → no banner; password+identity user → banner hidden |
| `displayNameFor` unit | node --test (repo's existing static-JS test infra; C16 parity test stays untouched — supabase-session.js is NOT modified in P1) | precedence `username > display_name > email-prefix` incl. null cases |
| Regression | existing claim/onboarding/signup suites | full docker-lane run (`TORTOISE_DB_URI` per AGENTS.md) |

**P2 additions (next issue):** `tests/test_user_identity_authority.py` —
intent expiry/replay, permit two-tab simulation, LAST_METHOD refusal, 422
probe fail-closed, audit rows; pgTAP for `reserve_unlink`/`consume_unlink`;
e2e link/unlink journeys.

## 9. Verification plan

- P1: docker-lane pytest (claim + onboarding + new suite) green; pgTAP file
  green; `vite build` green; Playwright e2e green under `RUN_HOSTED_E2E=1`;
  manual smoke: fresh signup (single method) → banner → username → reload.
- P2 gate (before linking ships): ops probe confirms hosted
  `enable_manual_linking = true` (staging canary), `TORTOISE_MANUAL_LINKING_ENABLED=1`
  set, OAuth redirect URLs registered for `app.premiselabs.co`; 422 leak test
  (toggle off → UI hides add-method, no dead button).

## 10. Acceptance criteria (P1)

1. `GET /v1/user/identity` returns the assembled inventory + server-computed
   banner for the session user; 401 without a session; 502 on auth-seam
   failure; `unsupported` in selfhost mode.
2. Recovery banner shows exactly when `ways_in ≤ 1` and the user is not in the
   anon/claim-gate or welcome state; CTA routes to the profile tab.
3. Profile tab lists login methods (identities + password row) and the
   credentials tier separately; **never** reads `teams.email` for the identity
   list.
4. Username: single-writer (profile page only), `user_metadata.username`
   namespace untouched by the wizard, uniqueness enforced (409 on duplicate),
   display precedence applied at all display sites.
5. Invariant guard test passes: the new identity/profile flows write nothing to
   `teams.email`.
6. No regressions in claim/onboarding/signup suites.

## 11. Runtime prerequisites

- **Ops toggle (P2 gate, no P1 impact):** hosted `enable_manual_linking` flip +
  verification; `TORTOISE_MANUAL_LINKING_ENABLED` env; OAuth redirect URLs;
  confirmations already enabled (config.toml:249).
- **Demotion probes (run at P2/demotion time, falsification-gated):**
  1. duplicate-email census (would `uq_teams_email` drop break?),
  2. `<2-identity` claimed-user census (the cohort the banner serves),
  3. toggle state (manual linking actually on),
  4. secondary-identity collisions (same email across teams).
  If ALL confirm multi-team demand → execute the C1 branch (see below).
- **C1 demotion consumer map (already inventoried — ready if probes fire):**
  `team_by_email` idempotency (`hosted_api.py:~3003`), claim RPC Step 6 email
  upsert (`20260813000004`), `provision_team` email params (0010:90,
  `20260825214233:92`), `_team_email`/`_write_team_email` seams
  (`hosted_api.py:8436/8452`) + onboarding PATCH (`:8204`), `uq_teams_email`
  drop + dedup pass, `reg-<sha256(email)[:12]>` anchor re-point (C15), and the
  `quota.py:144` anon proxy (already off the email proxy — verified clean).

## 12. Scope boundaries

**IN:** profile page (methods + username), recovery banner, `GET
/v1/user/identity` + inventory/username RPCs, claim `created_by` migration,
display-precedence helper, C2 invariant + guard test, P2 design locked in this
doc, demotion consumer map.
**OUT:** password *removal* flow; phone/passkey methods; multi-team identity
consolidation (C1 demotion — probe-gated); #1716 keyless-anon recovery (claim
path, separate work); anon-claim redesign (#1148); welcome-page changes;
selfhost profile parity (selfhost has no GoTrue — clean `unsupported`);
`supabase-session.js` modifications (C16 byte-parity preserved).

## 13. Open items for the implementer (resolved at implementation, not blocking)

1. GoTrue version behavior on change-email confirmation: identity row
   created/updated vs not (fallback signals defined in §6.3).
2. `REAUTH_WINDOW` default (900s) vs GoTrue's native unlink re-auth window —
   document the composite gate in the P2 plan.
3. Username charset policy (`^[a-z0-9_]{3,30}$` proposal) — confirm with owner
   before P1 ships the editor.
