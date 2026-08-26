# Identity-linking & profile — Solution approaches (divergence)

> Issue-scoping solution alternatives for the confirmed problem: identity facts
> conflated across `teams.email` (globally-unique team attribute), the user
> anchor (`team_memberships.user_id` + GoTrue `auth.identities`), and
> `api_keys.created_by` (mixed attribution). Every identity-adding operation
> collides with team-scoped uniqueness. **No winner selected — this is the
> divergence artifact.** Verified against source 2026-08-26.
>
> User deliverables: (1) profile page — add login methods (GitHub / Google /
> email+password over time) + set username; (2) dashboard recovery banner for
> single-login-method users routing to the profile page.

## Constraint register (all approaches engage these)

Verified against `docs/auth-architecture.md`, `supabase/migrations/*`,
`tortoise/hosted_api.py`, `tortoise/supabase_control.py`,
`website/apps/dashboard/src/main.jsx`, `website/assets/supabase-session.js`,
`supabase/config.toml`:

| # | Constraint | Where verified |
|---|---|---|
| C1 | Re-auth required on link; identity-floor on unlink (never leave zero ways in) | scoping |
| C2 | Verified-email-only linking (OTP / provider-verified email) | scoping |
| C3 | Banner inventory = identities + password-capability + keys by `created_by` | scoping |
| C4 | **#2085 caveat:** `updateUser({password})` creates NO `auth.identities` row → password-capability is a separate signal (`auth.users.encrypted_password IS NOT NULL` via service role) | scoping |
| C5 | `enable_manual_linking` is an external Supabase toggle: `config.toml:173` = false; hosted state unknown; GoTrue returns **422** when off | supabase/config.toml |
| C6 | Add-email+password must NOT use admin-create — `handle_new_user` placeholder trigger (`team_id=''`, `key_hash='pending'`) fires on every `auth.users` INSERT → phantom team placeholder (0001/0003/0010) | migrations 0001/0003/0010 |
| C7 | Username collides with machine-synced `display_name` (**#1691** — wizard writes `display_name` = graph Subject, main.jsx:578-581) | main.jsx |
| C8 | Anon teams already covered by full-page Protect (**#1148** `dashboard_key_login` gate) | hosted_api / 20260813000005 |
| C9 | Keyless anon cohort exists (**#1716**) with no claim path | scoping |
| C10 | Agent principals (`st_*`/`api`/NULL `created_by`) excluded from human inventory — `mint_target_user_for_key` pattern (supabase_control.py:684) | supabase_control.py |
| C11 | No `/v1/user` endpoint exists — inventory must be served by a new surface | hosted_api.py route table |
| C12 | Dashboard is router-less single-file React (main.jsx ≈ 3.2k lines); banner state at :93 | main.jsx |
| C13 | supabase-js **2.112.2 vendored** with `linkIdentity`/`unlinkIdentity` support unused | public/vendor/supabase-2.112.2.min.js |
| C14 | `auth.identities` not browser-queryable without RLS/RPC → bounded RPC or backend read | scoping |
| C15 | `teams.email` is the signup idempotency key (`team_by_email`, hosted_api ~3003) + `uq_teams_email` partial unique index (20260813000004 P3-FIX-S); `reg-<sha256(email)[:12]>` identity rows exist WITHOUT `auth.users` rows | hosted_api / 20260813000004 |
| C16 | `supabase-session.js` shared helper must stay byte-parity across dashboard + website (static test `test_cross_subdomain_cookie_sync.py`) | tests/ |
| C17 | Ops surfaces: hosted `enable_manual_linking`, OAuth redirect URLs, email confirmations (`enable_confirmations = true` — OTP available) | config.toml |
| C18 | RPC precedent: mutations are SECURITY DEFINER + service_role-only (claim_membership/provision_team, auth binding INSIDE the RPC — never client-supplied team_id); reads are `auth.uid()`-bounded; audit via `audit_events.detail` jsonb | migrations |

**Shared semantic decision every approach must make (flagged, not resolved here):**
what counts as a "way in" for the banner/floor — (a) strict login methods
(identities + password-capability), or (b) plus dashboard-reachable keys
(session-mint means a key IS a way into the dashboard, #1511 §5.3). The banner
inventory (C3) is unambiguous (all three tiers); the *floor* that gates unlink
should be the strict (a) definition with keys displayed as a separate
"credentials" tier — but this is a product-semantics decision the implementer
must confirm with the owner, not assume.

---

## Approach A — Phase-sliced delivery (P1 read-only → P2 linking), client-side-first

**Framing lens: PRODUCT.** Deliver the user's three asks in two independently
shippable phases. P1 carries zero writes to the auth domain (bounded read-only
inventory + banner + username); P2 is the linking feature, deferred until the
external `enable_manual_linking` state is verified.

### Description

**P1 (ships alone):**
- New bounded inventory surface: `GET /v1/user/identity` (backend) returning,
  for the session user only: providers + verified emails + last_sign_in_at
  (from `auth.identities` via service role), password-capability
  (`auth.users.encrypted_password IS NOT NULL` — the C4 signal), and the
  user's keys by `created_by` across their teams (C10 exclusions: agent
  principals filtered).
- Dashboard: recovery banner when `ways_in == 1` (or 0) → CTA routes to
  `#profile` (hash view inside the single-file app — C12, no router).
- Profile page (P1 state): read-only method list + username editor.
- Username: writes `user_metadata.username`; display precedence
  `username > display_name > email-prefix`; the #1691 wizard keeps writing
  `display_name` and NEVER touches `username` (C7 — two namespaces, one rule).

**P2 (ships later, gated):**
- Linking via vendored `supabase.linkIdentity` (C13): OAuth popups
  (GitHub/Google) + email+password via OTP (verified-email-only, C2). No
  admin-create anywhere (C6 — `handle_new_user` placeholder never fires
  because no `auth.users` INSERT happens).
- Client-side gates: re-auth (force a fresh `signInWithPassword`/OAuth round
  before linking, C1) and a pre-unlink floor check against the P1 inventory.
- Capability probe: at P2 load, call `linkIdentity` once in a dry-probe mode
  (or a `GET /v1/user/linking-capability` that the backend probes) — a 422
  (C5) → linking UI hidden, banner keeps "contact support", **fail-closed**
  (hide the add-method affordance; never show a button that always errors).

### Files touched

- `tortoise/hosted_api.py` — new `GET /v1/user/identity` (+ `linking-capability` in P2); session-authed via existing `get_current_user`.
- `tortoise/supabase_control.py` — service-role helpers: identity rows read, password-capability read, keys-by-creator (C10 filter).
- `supabase/migrations/<new>.sql` — (P1) optional `auth.uid()`-bounded inventory RPC (C14) so the dashboard could read directly; (P2) unlink floor RPC (server double-check).
- `website/apps/dashboard/src/main.jsx` — banner (:93 area), `#profile` hash view, username editor, P2 link/unlink UI.
- `website/assets/supabase-session.js` + `website/apps/dashboard/public/assets/supabase-session.js` — any new helpers (re-auth marker, profile-navigation helper), keeping C16 parity.
- `supabase/config.toml` — local `enable_manual_linking = true` (P2); hosted flip is an ops runbook step (C17).
- `tests/` — `test_user_identity_inventory.py` (+ e2e banner→profile), pgTAP for any RPC.

### Architecture

```
P1: [dashboard] --GET /v1/user/identity--> [hosted_api] --service-role--> auth.identities
                                                                    + auth.users(encrypted_password)
                                                                    + api_keys.created_by (C10-filtered)
     banner = (identities + password) <= 1 ? show : hidden   # C3 tiers; floor semantics = decision above
     username = updateUser({data:{username}})  # client-side write, no backend
P2: [dashboard] --linkIdentity(provider)--> [GoTrue]          # OAuth popup / email OTP (C2, C13)
     [dashboard] --unlinkIdentity(identity)--> [GoTrue]       # after client floor pre-check + server floor RPC
```

### Risks

- **P2 external dependency (C5):** hosted `enable_manual_linking` unknown →
  P2 may ship to a 422 wall. Mitigation: ops runbook FIRST (flip + verify),
  capability probe SECOND, fail-closed UI THIRD.
- **Client-side floor is TOCTOU-prone** (two tabs, stale inventory). The
  unlink floor pre-check + a server floor RPC at commit narrows but doesn't
  atomically close the race (both checks are pre-state reads).
- **Username/display_name divergence (C7):** two namespaces is only safe if
  every display site implements the same precedence — a one-line miss
  resurrects the #1691 clobber. Needs a shared helper + a test.
- **Banner noise:** must filter human+session only (C8 anon teams go to the
  claim path; C9 keyless cohort is a separate recovery concern — the banner
  must not claim it can fix them).
- **Deferred risk:** P2's security review (re-auth placement, floor semantics)
  happens after P1 is live — a mid-P1 discovery can reshape P2's UI.

### Tradeoffs

- Fastest path to user value (banner + username in P1); linking risk isolated
  behind an ops gate.
- Two release trains; P2's re-auth/floor discipline lives mostly in client
  code + supabase-js (more surface than B).
- Cheapest: reuses vendored supabase-js (C13), no new auth state machine.

**Best-fit-if:** the recovery value (banner, username) must ship now, linking
is explicitly allowed to lag behind an ops verification, and the team accepts
a client-dominant gate posture with a server floor backstop.

---

## Approach B — Server-verified identity authority (`/v1/user` owns the gates)

**Framing lens: SECURITY.** Identity linking is the highest-risk operation in
the auth stack; a compromise of the flow must not grant account access. All
identity reads AND mutations flow through a new backend surface; the client
drives UX but never decides security outcomes.

### Description

- **Inventory (P1-equivalent, but backend-owned):** `GET /v1/user/identity`
  as in A — identical read shape, one authority, full audit trail.
- **Link intent state machine:** `POST /v1/user/identity/link-intent
  {provider}` → server verifies (1) valid session, (2) **re-auth freshness**
  — a fresh-login proof (session `issued_at` within window, or a forced
  one-time re-auth challenge, mirroring the #1511 exchange's strict-validity
  discipline), then mints a short-lived signed intent (nonce, provider,
  ~120s TTL, bound to user+session). The client then runs the GoTrue OAuth
  popup / email OTP leg (only GoTrue can do the interactive leg).
- **Link commit:** `POST /v1/user/identity/link-commit {intent, provider,
  provider_id}` → server verifies intent (signature/expiry/replay), reads the
  fresh `auth.identities` row via service role, verifies ownership
  (`user_id == session user`) and verified-email (C2), writes
  `audit_events.detail` (the 0004 jsonb pattern, C18), and applies the
  teams.email collision rule (see Data-model lens: the link NEVER writes
  `teams.email`; a match against another team's `teams.email` is an adoption
  signal, surfaced not automated).
- **Unlink with atomic floor:** `POST /v1/user/identity/unlink {identity_id}`
  → server takes a per-user advisory lock (`pg_advisory_xact_lock`),
  re-reads the floor (identities + password-capability, C4) minus the target,
  refuses at 0 (**LAST_METHOD**), then performs the GoTrue delete itself by
  forwarding the already-validated session `access_token` to
  `DELETE /user/identities/{id}` (the token is the session credential — the
  API already validates it in `get_current_user`; the server acts as the
  user's agent in the same request), re-verifies the post-state, audits. The
  floor is checked **by the code that performs the removal** — the two-tab
  race is closed by the advisory lock + post-state re-check.

### Files touched

- `tortoise/hosted_api.py` — `GET /v1/user/identity`, `POST /v1/user/identity/link-intent`, `link-commit`, `unlink`; rate limits (per-IP buckets precedent from #1511).
- `tortoise/supabase_control.py` — GoTrue REST-forwarding helpers (user-token `DELETE /user/identities/{id}`), identity-row reads, password-capability, intent signing/verify.
- `supabase/migrations/<new>.sql` — unlink-floor RPC (advisory lock + count), maybe intent table (or stateless signed nonce).
- `website/apps/dashboard/src/main.jsx` — banner (:93), `#profile` view, thin client: call intent → popup → commit; call unlink → render result.
- `website/assets/supabase-session.js` ×2 (C16 parity) — re-auth marker helper.
- `supabase/config.toml` — local toggle flip + ops runbook (C17).
- `tests/` — `test_user_identity_authority.py`: intent expiry/replay, floor atomicity (two-tab simulation), agent exclusion (C10), 422 probe, audit rows; e2e popup-flow.

### Architecture

```
[client] --link-intent--> [hosted_api]   (re-auth check → signed intent, TTL 120s)
[client] --OAuth popup/OTP--> [GoTrue]   (interactive leg — only GoTrue can)
[client] --link-commit{intent}--> [hosted_api] (verify identity row, ownership, verified-email, audit)
[client] --unlink{id}--> [hosted_api]    (advisory lock → floor check → GoTrue DELETE w/ user token → re-check → audit)
[client] --GET /v1/user/identity-->      (banner + profile render)
```

### Risks

- **Server forwards the user's `access_token` to GoTrue** — a new "server as
  user agent" surface. Bound strictly: only the token from the
  already-validated session of THIS request, never a stored token; narrow
  allowlist of forwarded endpoints; the endpoint must not be re-entrant into
  other user surfaces.
- **Intent state machine complexity:** issuance, expiry, replay, session
  rotation mid-intent — more moving parts than A; every branch needs a test.
- **GoTrue's own re-auth requirement on unlink** interacts with ours — two
  gates must be documented as complementary, not redundant (GoTrue's window
  may be stricter/looser than our freshness window).
- **C5 still bites:** the intent exchange cannot bypass GoTrue's
  `enable_manual_linking` 422 — the same probe/fail-closed UI as A is
  required before P2.
- **Advisory-lock contention:** per-user lock is cheap, but the handler must
  hold it for the full remote GoTrue call (latency inside the lock) — a
  slow GoTrue delete blocks that user's other identity ops (acceptable at
  human scale).

### Tradeoffs

- Strongest posture: every gate server-owned, atomic floor, full audit trail;
  client compromise has minimal payoff (popups only).
- Most backend surface + a new state machine; P1 inventory also waits on the
  backend (no browser-only shortcut).
- Best auditability for a security-sensitive feature — aligns with the
  claim_membership/provision_team service-role precedent (C18).

**Best-fit-if:** linking is treated as the highest-risk operation and the team
wants a single authority that owns re-auth, the floor, and the audit trail
before any linking ships — and accepts a bigger backend slice to get it.

---

## Approach C — Data-model re-anchor (identity mirror; demote-or-bounded-slice)

**Framing lens: DATA.** Fix the conflation at the schema root, then let the
profile/banner ride the corrected model. Two variants gated by the
falsification probes — the demotion is CONDITIONAL, the bounded slice is the
DEFAULT FALLBACK.

### Description

**Shared substrate — the identity mirror table:** a `public.user_emails`
(`user_id uuid`, `email text`, `provider text`, `verified_at timestamptz`,
written by a trigger on `auth.identities`, backfilled by a re-sync job).
Gives the platform a queryable, RLS-able identity fact store (C14 solved
permanently — no GoTrue-table reads in the browser path) and separates
"identity facts" from "team attributes" structurally.

**C1 — Demote `teams.email` (ONLY if falsification probes confirm multi-team
demand):**
- Migration: drop `uq_teams_email` (20260813000004 P3-FIX-S); `teams.email`
  becomes a nullable, non-unique per-team contact/display field; the signup
  idempotency key re-anchors from `team_by_email` (hosted_api ~3003) to "any
  verified identity for this email" (query `user_emails` / service-role
  `auth.identities`). `/v1/register`'s `reg-<sha256(email)[:12]>` anchor
  (C15) re-points at the mirror for the user path; agent/anon rows stay
  excluded.
- Multi-team-per-person becomes expressible (one user, N memberships — the
  M:N shape 0009 already models); the profile page reads the corrected model
  directly; the banner floor = mirror + password-capability + keys.

**C2 — Bounded slice (DEFAULT FALLBACK — keep `uq_teams_email`):**
- `teams.email` stays the anti-duplicate registry for SIGNUP only; a hard
  invariant: **identity flows never write `teams.email`** (lint/test guard +
  documented rule). The linking path creates `auth.identities` rows only;
  when a newly-linked email equals another team's `teams.email`, that is an
  adoption signal surfaced on the profile (not automated). Banner/profile
  read the mirror + backend inventory exactly as A/B.

### Files touched

- `supabase/migrations/<new>.sql` — mirror table + trigger on `auth.identities` + backfill; (C1) drop `uq_teams_email`, re-anchor indexes; (C2) invariant test only.
- `tortoise/hosted_api.py` — `/v1/register` idempotency re-anchor (C1); `GET /v1/user/identity` on the mirror; claim-path email-upsert re-point (0004's email logic — C1).
- `tortoise/supabase_control.py` — mirror reads; `team_by_email` re-anchor or retention-as-signup-only (C2).
- `supabase/migrations/20260813000004_claim_membership.sql` — touched ONLY in C1 (email uniqueness semantics).
- `website/apps/dashboard/src/main.jsx` — banner + `#profile` (same UX as A/B, data now from the corrected model).
- `supabase/config.toml` + ops runbook (C17) — unchanged role from A/B (P2 still needs the toggle).
- `tests/` — `test_user_emails_mirror.py` (trigger, backfill drift), `test_register_idempotency_reanchor.py` (C1), invariant guard test (C2), pgTAP.

### Architecture

```
auth.identities ──trigger──> public.user_emails (mirror; RLS: user_id = auth.uid())
teams.email (C1: contact field, non-unique) | (C2: signup registry only, never written by identity flows)
/register idempotency: (C1) mirror query | (C2) team_by_email unchanged
profile/banner ──> /v1/user/identity ──> mirror + password-capability + keys (C10-filtered)
```

### Risks

- **C1 is a one-way door:** dropping `uq_teams_email` requires a dedup pass
  to re-add; the claim path's email-upsert-or-409 (0004) and the reg- anchor
  both reference teams.email semantics and must be re-pointed atomically with
  the drop.
- **Trigger on GoTrue-owned `auth.identities`:** Supabase permits it, but
  GoTrue upgrades can collide with trigger maintenance → backfill/re-sync job
  + tolerated drift window; the mirror is a cache of truth, never the
  authority for auth decisions.
- **C1 is justified ONLY by the probes:** a false positive churns the whole
  signup path for a hypothesis — the conditional gate exists precisely to
  avoid that.
- **C2 keeps the latent conflation:** every future identity feature must
  remember "never write `teams.email`" — the invariant needs a test + a lint
  guard or it rots (this is exactly how the current bug was born).
- **C9/C8 unchanged:** anon/keyless cohorts still need the claim path and the
  #1716 recovery question regardless of schema.

### Tradeoffs

- C1: the "correct" long-term model — one identity fact store, teams stop
  being the user table; biggest blast radius, migration-train sized, probe-
  gated.
- C2: surgical, zero schema risk, ships the same user value; the underlying
  conflation persists as a documented invariant with a guard.
- Either variant still needs A's/B's P2 linking mechanics (GoTrue is the
  only link executor) — the data model and the flow security are orthogonal.

**Best-fit-if:** C1 — probes confirm real multi-team/one-identity demand and
a migration train is acceptable; C2 — the product stays
single-team-per-identity and the flows just need to stop colliding.

---

## Cross-approach comparison

| Axis | A: Phase-sliced (client-first) | B: Server-verified authority | C: Data-model re-anchor |
|---|---|---|---|
| Lens | PRODUCT | SECURITY | DATA |
| P1 (banner+username) ships | First, alone | First (backend-owned) | First (mirror-owned) |
| Linking executor | vendored supabase-js (C13) | GoTrue popup + server commits | same as A/B (GoTrue) |
| Re-auth gate (C1) | client-side re-login | server-verified freshness + intent | via chosen flow (A/B) |
| Unlink floor (C1) | client pre-check + server RPC backstop | atomic: advisory lock + server-performed delete | via chosen flow (A/B) |
| Floor semantics | explicit decision, client+server | strict server-side (identities+password) | explicit decision on the mirror |
| teams.email | untouched (invariant) | untouched + adoption-signal rule | demoted (C1) / invariant (C2) |
| #2085 password signal (C4) | service-role read | service-role read | mirror + service-role read |
| Agent exclusion (C10) | backend filter | backend filter | backend filter |
| External toggle risk (C5) | probe + fail-closed UI | probe + fail-closed UI | same (orthogonal) |
| New backend surface | small (`GET /v1/user/identity`) | large (intents, commit, unlink) | medium (+ register re-anchor) |
| Biggest risk | client floor race; display_name clobber | server-as-user-agent surface; state machine | C1 one-way door / trigger drift |
| Regression risk | low | medium | HIGHEST (C1) / low (C2) |
| Best-fit | value-now, linking later, accept client-dominant gates | security-critical linking, audit-first | C1: probe-confirmed multi-team; C2: stay single-team, stop colliding |

## Open decisions the winning approach must still answer (not resolved here)

1. **Floor semantics:** strict login methods (identities + password) vs
   + dashboard-reachable keys (session-mint makes keys a way in, #1511 §5.3).
2. **Re-auth freshness window:** what counts as "recent" for link/unlink
   (session `issued_at` age vs a one-time re-auth challenge).
3. **teams.email collision on link:** 409-with-guidance vs adoption-signal vs
   silent (both A and B need this rule even without a schema change).
4. **Banner for the #1716 keyless anon cohort:** the banner must not promise
   a fix the identity flows don't provide (separate recovery work item).
5. **Where `#profile` lives:** hash view in main.jsx (C12) vs a second entry
   page — affects C16 helper parity and the head-gate story.
