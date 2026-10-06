---
title: "#3528 — Unhandled auth flows: residual scope"
type: engineering
domain: platform
doc_status: live
ownedBy: organisation-design-team
aboutSubjects: auth, email-flows, session
aboutObjects: auth-confirm, supabase-redirect-allowlist
created: 2026-09-22
---

# #3528 — Unhandled auth flows: residual scope

> Issue: [#3528](https://github.com/daniel-ospina/tortoise/issues/3528) (child of #3501,
> filed as a Phase-7 adversarial residual). Companion: **#3503** — already shipped, see §0.

## 0. #3503 — already fixed; no work in this change

`website/assets/supabase-session.js` no longer discards `storeSession()`'s return value: the
fragment is retained when the write did not take, and the write is confirmed by read-back.
Shipped in **PR #3640**, main commit `d0b699bd7`, tests `tests/test_session_bridge_fragment_retention.py`.
Independent seam from this issue — **not folded in**.

## 1. Confirmed problem (problem diamond)

`/auth/confirm` is the single server-side completion path for every email-initiated flow that
arrives with a `token_hash`:

| Flow | Entry | Completion |
|---|---|---|
| F15 password recovery | `/auth/reset` → recovery email | `GET /auth/confirm?type=recovery` (interstitial) → `POST` mints |
| F16 email confirmation | `/auth/resend` → signup confirmation email | `GET /auth/confirm?type=email` |
| F14 invite | invite email | `GET /auth/confirm?type=invite` |
| stale-link (`token_hash`) | any pre-cutover `token_hash` link | same route; fragment-style links are `unsupported-by-decision` (§5.4, `confirm.ts` header) |

Three of #3528's five tasks are already satisfied in the code that shipped with **#4104**
(main commit `8f3286864`):

1. **F15/F16 as explicit flows** — recorded in the private `premise-labs` `engineering/auth/SCOPE.md` §5;
   `/auth/confirm` cites `SCOPE.md F15/F16, §5`.
2. **F13 resolved** — `confirm.ts`'s header states fragment-style pre-cutover links are an
   *explicit unsupported-by-decision* (`§5.4`), which is the "written decision" the issue asked for.
3. **Invite old-host handoff** — `website/functions/_middleware.ts` + `website/_redirects`
   redirect `/invite-accept{,/,\.html}` to `app.premiselabs.co` server-side, query-preserving.
5. **`next=` allowlist** — `safeNext()` in `_shared/auth/session.ts` (origin-resolve + result
   re-check; TAB/`//` bypasses covered) plus `returnToPath()` in `admin/[[path]].ts`; tests
   `test_next_destination_persists_and_is_honoured`, `test_next_rejects_cross_origin_destinations`,
   `test_next_accepts_a_legitimate_same_origin_path`.

**The residual — three concrete gaps (corrected after the problem-verify cycle):**

- **P0 — email confirmation does not complete at all.** `confirm.ts` was the pitfall: its `type=recovery`
  branch mints a pending record and a binding cookie of its own, but `email` (signup confirmation **and**
  magic link), `email_change` and `invite` fell to a class-8 branch requiring a `__Host-authflow` cookie
  matching a live `auth_flows` row — and **no email flow establishes one**. `FLOW_COOKIE` is minted only by
  `/auth/start` (OAuth) and `/auth/link` (identity linking); `/auth/signup`, `/auth/reset` and `/auth/resend`
  set `redirect_to=/auth/confirm` without one. A link opened from an inbox therefore arrived with no cookie,
  was answered `302 /auth?interstitial=1` — a redirect **nothing in the app consumes** — and the single-use
  `token_hash` was dropped. The suite encoded this as `test_confirm_without_flow_is_interstitial`, i.e. the
  broken behaviour was pinned by a green test. Evidence: `confirm.ts:257-273` (pre-change), the `FLOW_COOKIE`
  writers, `test_bff_flow.py:385-391` (pre-change), and `SCOPE.md` §8.4, which records the same break
  ("the design breaks its own happy path").
- **P1 — the completion path misreports an upstream fault.** `_shared/auth/supabase.ts::call()` classifies
  success by STATUS alone, so a malformed or partial 2xx reaches `.user.id` / `.refresh_token`. The recovery
  GET dereferences inside the pending-INSERT `try`, so it answers 503 with the **wrong slug**
  (`session_store_unavailable`); the non-recovery path dereferenced before `.catch()` was attached and could
  throw an unhandled **500**. `/auth/password` received the shape guard in #4104 (`password.ts:172-180`);
  `confirm.ts`, rewritten in the same PR, did not. Filed as **#4160**.
- **P2 — the Supabase-side contract has no in-repo artifact.** These flows depend on URL-Configuration entries
  and on email templates that build `{{ .RedirectTo }}?token_hash=…&type=…`. With the default
  `{{ .ConfirmationURL }}` the link routes through GoTrue's `/auth/v1/verify`, whose `?code=` / `#access_token=`
  shapes `/auth/confirm` **cannot** consume. Today that requirement lives only as an inline warning in
  `reset.ts` and a stale redirect-URL list in `docs/plans/2026-08-26-1765-ops-runbook.md`, which predates #4054
  and names neither `/auth/confirm` nor `token_hash`. #3528 task 4 asks for the audit.

**Correction to the earlier draft of this scope.** An earlier revision declared the email flows shipped and
scoped only the status guard. The problem-verify cycle falsified that (P0), and it was right: only recovery
completed. The rejected-alternative row "the routes exist, so the flows exist" was the error.

### Assumptions

| Assumption | Status | Evidence |
|---|---|---|
| `call()` accepts any 2xx unvalidated | [validated] | `supabase.ts:57-72` |
| `confirm.ts` dereferences `.data.user.id` / `.refresh_token` unguarded | [validated] | `confirm.ts:238-239, 294, 308-309` |
| `/auth/password` already has the guard | [validated] | `password.ts:172-180` |
| Three of five #3528 tasks are already shipped | [validated] for tasks 2/3/5; **task 1 was NOT** | `8f3286864` (PR #4104): `confirm.ts` header decision, `_middleware.ts` + `_redirects`, `safeNext`. Task 1's *completion path* was broken — see the P0. The F15/F16 flow-map rows are in the private `premise-labs` `SCOPE.md` §6 and are asserted by code citation only; the off-repo doc was not inspected as a whole. |
| Live Supabase project config matches the required contract | [unverified] | needs the Management API token; not available in this worktree |

### Boundary & stakeholders

- **Out of scope:** the #3501 BFF re-architecture itself, the session store (#3522), the consent
  bridge (#3524), client-surface migration (#3559), threat-class tests (#3529) — other lanes.
- **Not in scope:** `website/assets/supabase-session.js` deletion (#3501 indicator 1).
- **Affected but unmentioned:** the Supabase project operator — the audit's live half is an
  operator action, not a code change.

## 2. Problem framing — rejected alternatives

| Framing | Verdict |
|---|---|
| "Add the missing recovery/confirmation flows" | **Rejected** — they exist (`/auth/confirm`, `/auth/reset`, `/auth/resend`). Building them again would duplicate a shipped capability. |
| "The flow map is wrong, so fix the map" | **Rejected as the primary work** — the map lives in a private repo outside this one; the in-repo consequence of a wrong map is the code path, which is what we can verify and fix. |
| "A malformed 2xx is a client error ⇒ 4xx" | **Rejected** — a malformed provider response is an infrastructure fault; 503 is the route's declared contract and `/auth/password` already sets the precedent. |
| "Guard each call site inline" | **Rejected as the only fix** — it repeats the shape four times and the next route reading `call()`'s data is exposed again. Kept as the *implementation detail*, hoisted to one shared helper. |

**Confirmed problem (one sentence):** the email-flow completion path reads its provider response
without validation, so an upstream fault is reported as an unhandled 500 rather than the declared
503 — and the Supabase-side URL/template contract those flows require is not recorded where an
operator can check it.

## 3. Solution diamond

### Approaches

| # | Approach | Tradeoffs |
|---|---|---|
| A | Inline `if (!x) return 503` at each dereference site in `confirm.ts` | Smallest diff; repeats the shape; the next route re-introduces the same trap |
| **B** | **One shared narrowing helper (`requireUserSession`) in `_shared/auth/supabase.ts`, used by `confirm.ts` and `callback.ts`** | One place to get right; does not change `call()` for routes whose 2xx body is legitimately empty (`/auth/recover` → `{}`); `password.ts` carries an identical hand-copied guard that can now adopt it. ⚠️ It is a RUNTIME check, not a type-level one: `call<T>()` still returns `data: T`, so a future route can still dereference it unguarded. `requireUserSession` removes the duplication and gives the guard a name — it does not arm the trap shut |
| C | Validate inside `call()` itself (generic decode) | Breaks legitimate non-session 2xx bodies (`/auth/v1/recover`, `/auth/v1/logout`, `/auth/v1/resend`); wrong layer |
| **D** | **Give every email `type` the recovery interstitial** (`email`, `email_change`, `invite` — recovery already has it) | One code path; works cross-device; reuses a mitigation already reviewed and shipped in #4104 |
| E | Implement `SCOPE.md` §8.4's flow-start binding: every email-triggering route mints a flow id, sets `__Host-authflow`, carries the id in the confirm URL, and `/auth/confirm` validates cookie ↔ flow id | Conformant with §8.4 as written; but fixes only the **same-browser** path (the cookie must pre-exist, and an emailed link is routinely opened where it does not), needs client changes in `/auth/signup` and `/auth/set-email` that are not available (the hosted API and GoTrue's `PUT /auth/v1/user` do not take a redirect here), and still leaves the cross-device case to an interstitial |

**Chosen: B + D.** A is rejected because it leaves the trap armed for the next route (the regression that
produced #4160). C is rejected because `call()` is generic over `T`. E is rejected on outcome: it cannot
deliver #3528's stated Outcome ("every email-initiated auth flow has a **working** completion path"), because
it fixes only the browser that requested the email — and an email link is the one credential whose whole point
is that it may be opened somewhere else. §8.4 records this itself: it exists because "signup, recovery and
invite do not start at the BFF", and its `Required:` sentence ends "Without this, §9 class 8's binding is
unimplementable".

**Decision recorded (supersedes §8.4's mechanism for the email types).** The interstitial is a *different*
mechanism from §8.4's flow id in the confirm URL, and this scope chooses it deliberately. The security property
§8.4 and §9 class 8 protect — no session is minted from a credential the browser did not ask for — is preserved
by informed consent instead of by a pre-existing cookie: the GET mints nothing and NAMES the account the link
belongs to, and the POST is CSRF-guarded and bound to a single-use pending record created in that same browser.
⚠️ This is a departure from a recorded specification. It is taken because E cannot meet the issue's Outcome:
it fixes only the browser that requested the email, and an emailed link is the one credential whose whole point
is that it may be opened somewhere else. (E is not literally unimplementable — `/auth/reset` and `/auth/resend`
do set `redirect_to` in-repo and could carry a per-flow id. It is insufficient, not impossible.) If the owner
prefers §8.4's mechanism, the interstitial for `recovery` is already a shipped deviation (#4104 review cycle 2)
and would have to be reverted with it. `OVERRIDES:` marker posted on #3528.

**What actually mitigates the fixation vector.** The consent step — not the cookie binding. The GET issues a
matching `__Host-authflow` to ANY browser that opens a verified link, including a victim's, so the binding is a
single-use/replay guard (non-guessable pending id, cookie-bound POST, one consumption) and contributes nothing
against "victim clicks the attacker's link". A future lane must not treat it as load-bearing and weaken the
interstitial.

### `### Axis Research`

> **Trigger assessment:** Architecture = **low** (a validation helper beside an existing
> `SupabaseResult` discriminant; no interface or topology change), UX = low, Ontology = low,
> Config = low. No new third-party dependency — `verifyOtp`/`call()` already exist and the guard
> shape already exists in `password.ts:172-180`. In-repo precedent for the helper pattern is the
> `SupabaseResult` union itself. External research not demonstrated — skipped per the activation rule.

### `### Integration Docs`

No new dependency. Consumed API surface: `verifyOtp(env, tokenHash, type)` from
`_shared/auth/supabase.ts` returning `SupabaseResult<TokenResponse>`; `TokenResponse` is the
vendored supabase-js v2.112.2 shape `{ access_token, refresh_token, expires_in, user: { id, email? } }`.

### `### Adversarial Threat Surface`

**(not adversarial)** — this is a status-code-correctness fix on a fail-closed path. The guarded
branch mints no session and returns 503; the failure mode being fixed is a misreported *status*,
not an attacker-reachable bypass. The security properties that bound this path (class-8
`__Host-authflow` binding, CSRF guard, recovery interstitial) are unchanged and remain covered by
their existing tests.

## 4. Acceptance criteria

1. A `type=email` / `email_change` / `invite` link renders the consent interstitial, mints **no** session on
   the GET, and mints exactly one session on the CSRF-guarded POST, landing on `/welcome` (never the reset
   panel) and revoking no other sessions. Test: `tests/e2e/auth/test_bff_flow.py`
   (`test_email_confirmation_completes_through_the_interstitial`) + `src/bffCsrfGuard.test.js`.
2. Recovery is unchanged: interstitial on GET, session + F15 bulk revoke + `/welcome?reset=1` on POST.
3. A malformed/partial 2xx from `/auth/v1/verify` yields **503 `provider_unavailable`**, never 500, on every
   email type; the test fails if the guard is removed (mutation-proved).
4. The Supabase URL-Configuration + email-template contract is recorded in-repo, per flow, with its
   live-verification steps named (`docs/ops/auth-email-flows.md`).

## OVERRIDES

**OVERRIDES:** the `SCOPE.md` §8.4 flow-start cookie binding (mint `__Host-authflow` at email-request time, carry the flow id in the confirm URL, validate cookie↔flow-id at `/auth/confirm`) — for the email `type` values it is replaced by a per-pending-record consent interstitial, because §8.4 fixes only the browser that requested the email and an emailed link may be opened anywhere. The marker is also posted on #3528.

## 5. Wiring check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `/auth/v1/verify` response shape | external API | `_shared/auth/supabase.ts` helper | ✅ |
| `/auth/confirm` GET (recovery + non-recovery) | edge function | `confirm.ts` | ✅ |
| `/auth/confirm` POST (interstitial) | edge function | `confirm.ts` POST | ✅ |
| Email-flow e2e suite | test | `tests/e2e/auth/test_bff_flow.py` + mock | ✅ |
| Supabase URL config / templates | external config | new ops doc + operator checklist | ✅ (documented) |
