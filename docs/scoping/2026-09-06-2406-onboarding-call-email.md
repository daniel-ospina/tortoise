---
title: "Issue #2406 — auto-send onboarding-call offer email to every new signup (scope + plan)"
type: decisions
domain: operations
aboutSubjects: tortoise-signup, onboarding
doc_status: live
created: 2026-09-06
ownedBy: epistemic-team
---

<!-- issue-scoping: double-diamond scope + implementation plan (task-workflow-standard) -->

# Issue #2406 — Onboarding-call offer email on every new hosted signup

> Status: **scope + plan draft** — pending scope-verify and plan-verify gates.
> Tier: Standard. Level: task. Team: epistemic-team.

## Confirmed Problem

Every new **hosted** Tortoise signup (a human who creates an account and is
provisioned their first organization on the platform) should receive a
personal, best-effort email from **daniel@premiselabs.co** offering a
personal onboarding call, fired automatically within minutes of signup,
**exactly once per user** (never on re-signin), with **verbatim copy**
carrying the exact booking URL
`https://cal.com/danielospina/tortoise-onboarding-call`, and email failure
must **never block signup** (fail soft).

The non-goal of this issue is any "not sure yet" fork trigger (#2360-family,
#2407) — that fires the *same* email later from a different trigger and is
explicitly out of scope (marker semantics in §#2407 below).

## Problem divergence (framings considered)

| Framing | Verdict |
|---|---|
| F1 "Fire from `POST /v1/signup/email`" (the hosted email-signup endpoint) | Only one door; no name data at that point; OAuth signups never hit it → incomplete coverage of "every new signup". Rejected. |
| F2 "Every **org creation** = the signup moment" (the real product unit) | **Confirmed.** Under #2323 name-first provisioning, every hosted human's account creation completes at first-org provisioning via the tenant-provision edge function (email + display name + org name known). Agent/CLI anon signups (`/v1/agent/signup`, selfhost register) carry no email → naturally excluded. |
| F3 "Fire at first authenticated session request post-provision" | Couples email to arbitrary endpoint traffic; timing/state fragility; replay window ambiguity. Rejected. |
| F4 Symptom-reading (email is a "marketing ops task", send it manually) | Violates "fires automatically", "within minutes". Rejected. |

Evidence for F2 (verified in-repo): the only live provisioning door for
**hosted human first-org signups** is the tenant-provision Supabase Edge
Function (`supabase/functions/tenant-provision/index.ts`), called with a
user JWT from the dashboard's name-first org-create step
(`website/apps/dashboard/src/main.jsx` `provisionInApp`; wizard label "Name
your organization"). The `after_user_created` auth-hook path is INERT
(AUTH_HOOK_SECRET removed, #832). The FastAPI data plane is invoked exactly
once right after provisioning — the demo seed (`POST /internal/demo`,
hosted_api.py:4994). The `provision_team` RPC writes the teams row with
`p_email` (user email) + `p_team_name` (wizard-typed org name) and its
`ON CONFLICT (id)` refresh preserves `created_at` (idempotent replays). Note
(scope precision): the RPC also serves the Q5 paid sub-org lane
(`POST /v1/onboarding/team`) and the agent-identity lane with `p_email`
NULL — those are NOT first-org signups and must never fire the email;
documented invariant: `teams.email` is set only by tenant-provision (and
legacy claim paths, now removed) — every other first-org door is email-NULL
and unreachable by this email by construction.

## Assumptions

| Assumption | Status |
|---|---|
| The email is for **hosted** users (Resend + premiselabs.co domain verified; ops key) — selfhost has no emails and is out of scope | [validated] — registry-mode signups carry no email |
| teams.email (set at provision) is the recipient | [validated] — provision_team `p_email` |
| "first name" is not captured anywhere at signup; greeting = best-effort per display_name (user-asserted OAuth metadata, may be absent) → email local-part → org token → "there" | [validated-by-design — see §Personalization] |
| The trigger (edge fn) is at-least-once → never-double is the hard requirement; marker + awaited send make delivery exactly-once for every in-band path | [validated] — see §Dedupe design |
| Reuse `tortoise/email_notify.py` Resend client + budget + redaction + task-registry patterns (per #307 decision: in-API monolith; edge-function senders rejected there) | [validated] — #307 scope doc §1, Approach A |
| Resend allows `from: daniel@premiselabs.co` on the shared key (premiselabs.co is a verified Resend domain; billing already sends from billing@premiselabs.co via the same account) | [validated] — #307 §8 + issue body |
| External best-practice research | [justified skip] — decision hinges on internal semantics (#2323 door, #307 precedent, in-repo Resend client); external transactional-email practice already embodied in email_notify.py |

### Personalization ("Hey [name]")

Data sources at trigger time (scope-verify cycle 1+2 — org-name primacy was
wrong): `teams.name` is the wizard-typed ORGANIZATION name, forced to a
whitespace-free slug by both the wizard and the edge fn (`TEAM_NAME_RE`
`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`) — it is NOT a person's first name and
must not drive the greeting. Person-ish sources: `display_name` (OAuth
`user_metadata.display_name`, threaded from the edge fn) and the user's
email local-part. **Provenance honesty (cycle-2 fix):** `display_name` is
USER-ASSERTED metadata, not verified identity — any JWT holder can set it
via `supabase.auth.updateUser`, and the dashboard writes the wizard subject
into it. Providers do not uniformly populate it (email/password signups:
none; GitHub private-email users additionally have a numeric relay email
`123456789+handle@users.noreply.github.com`). The greeting is therefore
**cosmetic-only copy** — never load-bearing. Algorithm (pure helper
`_onboarding_greeting_name`, unit-tested with REAL funnel vocabulary):

1. `display_name` threaded in the edge-fn POST body → first whitespace
   token **that passes the name-shape predicate** (2–24 chars, all
   letters) — slug handles ("daniel-ospina", "acme_inc") fall through.
2. Else email local-part, split on `[-_.+ ]`, first segment that passes the
   same name-shape predicate AND is not a role-mailbox word
   (denylist: info|hello|admin|support|contact|sales|billing|office|
   no-?reply|mailbox|postmaster|team) — "daniel@premiselabs.co" →
   "daniel".
3. Else the org token (title-cased) — fires for org mailboxes
   ("info@acme.com" + org "acme" → "Hey Acme") and GitHub numeric-relay
   users (relay segment fails the letters test).
4. Else "there".

First character upper-cased, inner case preserved; never raises;
HTML-escaped in the html body. Per-cohort expected greetings pinned in unit
tests: OAuth "Daniel Ospina" → "Daniel"; email/password
"daniel.ospina@gmail.com" → "Daniel"; "info@acme.com" + org "acme" →
"Acme"; GitHub relay + org slug → title-cased org token. (Capturing a
literal first name on the signup form is a candidate follow-up issue, NOT
absorbed.)

### Dedupe design (cycle-2 controller fix — send-then-stamp, synchronous)

Never-double is the hard requirement; the design closes every in-band
double-fire path **without relying on the provider's 24h Idempotency-Key**
as the primary guard:

- **Marker read gate** before sending (teams.onboarding_email_sent_at set →
  skip).
- **Synchronous awaited send + stamp-on-provider-accept**: the internal
  endpoint AWAITS the Resend POST (bounded: single attempt + one 0.5s
  transient retry, httpx timeout ~3s) and stamps
  `teams.onboarding_email_sent_at` in the SAME request, before returning.
  A crash between provider-accept and the stamp is a ~100ms window (same
  residual as the invites `email_sent_at` pattern); the provider
  Idempotency-Key `onboarding:{team_id}` collapses any retry inside 24h.
- **In-process in-flight gate** (set of team_ids currently sending): a
  concurrent/second POST for the same team skips while the first is in
  flight — closes the TOCTOU where the marker read passes twice and two
  send tasks start (edge-fn +2s retry after a lost response, double wizard
  tab). Cross-process concurrency (N Fly replicas) is collapsed by the
  provider Idempotency-Key.
- **Send failure never burns the marker**: the marker is stamped ONLY after
  the provider accepts; a sender-side skip (RESEND_API_KEY absent / budget
  exhausted / provider 4xx/5xx/timeout) returns a synchronous
  `{"status": "failed"|"skipped"}` with the marker UNSET, so the edge-fn
  retry (+2s) or any later trigger retries. Skips/failures are WARN-logged
  with team_id (ops-visible).
- **No freshness guard** (cycle-2 controller fix): the marker + provider key
  carry dedupe; a guard would turn incident-recovery retries (edge fn
  crashed between the RPC and the POST; user retries the org-create next
  day) into permanent silent misses and would block a future #2407 fork
  trigger. Old teams with an UNSET marker SHOULD be reachable — the only
  callers hold the internal key or are the edge fn at provision.
- Residual accepted (documented): a Resend outage / key misconfig exactly
  at the provision instant with no later trigger = missed email
  (best-effort, WARN-logged, marker unset → ops replay possible). Budget
  exhaustion at the provision instant behaves the same (shared 100/day +
  3000/mo guard with invites/OTP).

## Solution divergence (approaches)

| Approach | Verdict |
|---|---|
| A. Edge function sends the email itself (Deno + RESEND_API_KEY Supabase secret) | Rejected. Double implementation (Python sender already exists), duplicates #307's rejected "Approach B", dedupe machinery in Deno, no Deno test infra, Resend key lives as a **Fly** secret on the API runtime not in Supabase secrets (issue: "reads the same env/secrets the existing Resend path uses"). |
| B. Post-provision internal FastAPI endpoint fired by the edge function (mirror of the `/internal/demo` call), sender + dedupe in Python | **Chosen.** Single Resend implementation (email_notify.py), durable dedupe against the Supabase teams row, fail-soft at two layers (endpoint + edge fn), pytest-testable via FakeControlPlane + monkeypatched sender. |
| C. Fold the email into the existing `/internal/demo` handler (no new endpoint/request) | Rejected. Demo seeding has separate semantics + idempotent self-heal callers (`/v1/demo` key-authed), and #1860 carefully scoped its error contract; entangling email into it is a trap for future seeding callers. A dedicated internal endpoint keeps the email trigger explicit and independently guarded. |
| D. Durable outbox / worker / DB trigger | Rejected — volume is tiny; #307 already rejected this for transactional email. |
| E. Hook `POST /v1/signup/email` | Rejected (F1 above — partial coverage, no name). |
| F. Marker-claimed-before-send + fire-and-forget schedule | Rejected in cycle 1+2 verification: sender-side skips burned the marker (permanent miss), and delivered-but-unmarked crash windows + a marker-clearing ops replay could double-fire. Superseded by §Dedupe design. |

### Why B over A on outcome quality

B keeps all email logic (copy, from-address, budget, retry, redaction,
templates) in the versioned, pytest-covered Python module the team already
owns, reusing `RESEND_API_KEY`/budget envs and the send patterns. The edge
fn only fires a tiny internal POST (like it already does for demo seeding)
— a ~20-line, source-guard-tested change. Dedupe is durable (teams-row
marker + awaited send + in-flight gate) with no dependence on provider
semantics; A would have needed equivalent Deno machinery with no test infra.

## Confirmed solution

```
fresh hosted signup (wizard org-create, name-first)
  └─► tenant-provision edge fn ── provision_team RPC (teams row: email+name)
        └─► POST /internal/demo  {team_id}          (existing, demo seed)
        └─► POST /internal/onboarding-email {team_id, display_name?}  (NEW)
              │  [FastAPI, internal-key auth — mirror of /internal/demo]
              │  1. registry/selfhost mode            → skipped (no emails)
              │  2. unknown team / no email           → skipped
              │  3. marker read: onboarding_email_sent_at set → already_sent
              │  4. in-flight gate: team currently sending → in_flight skip
              │  5. send_onboarding_offer_email(email, display_name, team_name,
              │        team_id) AWAITED  (bounded ~3s; budget reserve/refund;
              │        one 0.5s transient retry; provider Idempotency-Key
              │        "onboarding:{team_id}"; from daniel@premiselabs.co
              │        env-tunable; verbatim copy; exact URL) — NEVER raises
              │  6. provider accepted → STAMP teams.onboarding_email_sent_at
              │        (rowcount-gated, same request) → {status: sent}
              │     provider failed/skipped → marker UNSET, WARN log w/
              │        team_id → {status: failed|skipped} (retryable)
              └─► edge fn: onboarding POST independent of the demo seed
                   (fires even if demo throws), 2 attempts 0s/+2s, bound +
                   log non-ok, NEVER fails provisioning
```

Fail-soft: any exception anywhere in the chain (endpoint 500, edge-fn fetch
error) only logs — provisioning already succeeded; signup is never blocked.

### Coverage boundary (scope-verify cycle 1 — explicit cohorts)

"Every new hosted signup" is bounded by the org-provisioning trigger; the
non-provisioning human cohorts and their verdicts:

| Cohort | Reaches provisioning? | Verdict |
|---|---|---|
| Email+password signup → org-create wizard | Yes | ✅ emailed at provision |
| GitHub/Google signup → org-create wizard | Yes | ✅ emailed at provision |
| Anon/CLI-first user who later CLAIMS their anon team (`/v1/claim*`) | No — org already exists | ❌ excluded: minted anonymously with no email at signup (claim_membership no longer writes teams.email — migration 20260827000002); follow-up if ops wants claim-time outreach |
| Fresh user whose first act is ACCEPTING an invite (no own org) | No | ❌ excluded in v1: joined through a human inviter; emailing needs auth.users.email + membership trigger → follow-up issue (not absorbed) |
| Account creator who abandons before org-create | No (never provisioned) | ❌ excluded: no org, nothing to onboard onto yet |
| Paid sub-org via `POST /v1/onboarding/team` / `POST /v1/teams` (Q5) | No (server lane, p_email NULL) | ❌ excluded: second-org doors, not signups; teams.email NULL by construction (documented invariant above) |
| Selfhost/registry + `/v1/agent/signup` | n/a | ❌ no email exists |

**Email-abuse posture (scope-verify cycle 1 decision, accepted):**
`/v1/signup/email` creates accounts with `email_confirm=true` (no mailbox
proof, #801) and provisioning fires a personal email to that address — a
bot defeating Turnstile + the 3/hr shared IP bucket could trigger an
unsolicited personal email to a third party's address. Accepted: the
onboarding toast contains only a booking link (no credentials/secrets);
volume is low; Turnstile + IP limits bound the blast; noted in the PR for
explicit review. A future "require confirmed email before onboarding email"
gate is a candidate follow-up.

## Files

| File | Change |
|---|---|
| `tortoise/email_notify.py` | + onboarding send profile: copy constants (verbatim, exact URL), **awaited** `send_onboarding_offer_email(email, display_name, team_name, team_id) -> dict` ({status: sent\|skipped\|failed, message_id}); budget reserve/refund around the awaited call; provider `Idempotency-Key: onboarding:{team_id}`; from `RESEND_ONBOARDING_FROM_EMAIL` default `daniel@premiselabs.co`; `_send_resend` gains optional `from_addr` param (default `_from_address()` — invite/OTP untouched); greeting-name helper `_onboarding_greeting_name` (display_name → email local-part w/ role-mailbox denylist → org token → "there"); html/text templates (html.escape, paragraph-faithful copy). |
| `tortoise/supabase_control.py` | + `set_team_onboarding_email_sent(cp, team_id) -> bool` (rowcount-gated PATCH `onboarding_email_sent_at=now()` WHERE id AND IS NULL, return=representation), + `team_onboarding_email_sent(cp, team_id) -> bool` (marker read), + add column to `team_by_id` select / additive select tier. |
| `tortoise/hosted_api.py` | + `POST /internal/onboarding-email` (internal-key `_check_internal`), body `{team_id, display_name?}`; skip matrix (mode/team/email/marker/in-flight); awaits the send; stamps marker on accept; in-flight set; catches everything → structured `{status}` response, never a surprise 500 to the edge fn. |
| `supabase/migrations/20260907000001_onboarding_email_sent.sql` | + `ALTER TABLE teams ADD COLUMN IF NOT EXISTS onboarding_email_sent_at timestamptz;` (additive; no RLS/column-grant change — not sensitive). ⚠️ Deploy ORDER: this migration must apply BEFORE the FastAPI + edge-fn code ships (a PGRST204 missing-column error degrades fail-soft to a logged miss). |
| `supabase/functions/tenant-provision/index.ts` | + fire `POST /internal/onboarding-email` body `{team_id, display_name}` where display_name = the edge fn's person display_name (caller/body, index.ts:~312) — NEVER `safeName`; fires independently of the demo seed (separate try/catch), 2 attempts 0s/+2s, bound + log non-ok, never fails provisioning. |
| `tests/test_provisioning_edge_function.py` | + source guards: calls `/internal/onboarding-email`; retries it; passes person display_name not safeName; fires even when the demo fetch throws. |
| `tests/fake_control_plane.py` | + teams fixtures/columns support if needed by the marker/dedupe assertions. |
| New `tests/test_onboarding_email_http.py` | Endpoint tests (see Verification). |
| `tests/test_email_notify.py` (or new unit module) | copy/URL/from/personalization unit tests. |

### Wiring check

| Touch point | Type | Covered by |
|---|---|---|
| Supabase teams row (email, name, marker column) | data | migration + supabase_control helpers + team_by_id select |
| tenant-provision edge fn | external trigger | index.ts + source-guard tests |
| FastAPI internal surface | API | new `/internal/onboarding-email` + `_check_internal` |
| Resend outbound | external service | email_notify (existing client/budget/redaction + from_addr param) |
| Env/secrets | config | reuse `RESEND_API_KEY` (Fly secret already present); optional `RESEND_ONBOARDING_FROM_EMAIL` env-tunable (default = required daniel@premiselabs.co) — no deploy change required |
| FakeControlPlane | test infra | teams marker column |
| Shutdown drain | runtime | awaited send inside the request → no background task to lose; no drain dependency for the onboarding profile |

### Acceptance criteria

1. New hosted signup (provisioned team) → edge fn fires `/internal/onboarding-email` → one email to teams.email within the request; from `daniel@premiselabs.co`; body copy verbatim incl. exact `https://cal.com/danielospina/tortoise-onboarding-call` (no `onbaording` typo); greeting personalised per the display_name → email → org-token heuristic.
2. Replay / re-provision / repeat POST for the same team → no second send or email: second POST sees the marker set (`already_sent`) or the in-flight gate; delivery at most once. HTTP tests: sequential replay → skip; concurrent replay while first send in flight → in_flight skip, sender invoked once per process.
3. Provider failure / Resend unconfigured / budget exhausted → endpoint returns `{status: failed|skipped}` (never 5xx to the edge fn), marker NOT stamped, WARN logged with team_id; a subsequent POST retries and succeeds → marker stamped.
4. Registry/selfhost mode, unknown team, null email → skipped no-op. Marker already set → already_sent no-op.
5. Unit tests assert exact URL + copy + paragraph fidelity + from + personalization with REAL funnel vocabulary (OAuth full names, email local-parts, role mailboxes, org slugs, numeric GitHub relays, hyphenated handles) + env-tunable from + Idempotency-Key header present; edge-fn source guards as listed.
6. Full docker-lane pytest green (`TORTOISE_DB_URI=docker://...`).

## #2407 (fork "not sure yet") — marker semantics

Scope-verify cycle 2: the #2407 relationship must NOT overclaim. Facts:
fork-opted users ARE provisioned at signup (F2), so they receive the signup
email and the marker is set for them; a fork trigger that reuses this
endpoint would therefore no-op for exactly the cohort that opted into the
fork. The #2407 scoping (out of scope here) must decide: (a) does the fork
choice SUPPRESS the signup auto-email (needs a provision-time branch this
design does not have), or (b) is the fork email ADDITIVE later (needs its
own Idempotency-Key namespace, e.g. `onboarding-fork:{team_id}`, and its
own marker column, since suppress semantics would be wrong once the signup
email already went out)? This doc only pins the shared invariants: the
send function is reusable; `onboarding_email_sent_at` = "signup email
accepted by provider"; a fork email must use a distinct key namespace and a
distinct marker. Pre-launch backfill of existing teams is OUT of scope
(issue targets NEW signups); ops may replay the internal endpoint for a
team with an unset marker (safe — marker unset means no accepted send; the
~100ms accepted-but-unstamped crash window is covered by the provider
Idempotency-Key for replays ≤24h).

## Rejected alternatives (with when they WOULD have been better)

- **A (Deno sender)** — would be better if the platform were Supabase-only
  with an established Deno test harness and the Resend key in Supabase
  secrets. None hold.
- **C (fold into /internal/demo)** — better if demo seeding were strictly
  once-per-provision with no self-heal callers and no separate error
  contract. Not the case (#1860, `/v1/demo`).
- **E (signup endpoint)** — better if only email-form signups existed and
  names were collected on the form. Neither holds.
- **D (outbox)** — better at tens of sends/minute + durable delivery needs.
- **F (marker-before-send + fire-and-forget)** — better if the platform had
  no crash windows and a human re-sender actor (the invites case). Neither
  holds for an unattended signup email.

## Open questions surfaced for the user (not blocking)

1. Subject line not pinned by the issue — implementation uses a constant
   (`ONBOARDING_SUBJECT`), ops-tweakable; flagged in the PR.
2. Greeting is best-effort per funnel data (display_name is user-asserted
   and often absent); capturing a literal first name on the signup form is a
   candidate follow-up issue (not absorbed).
