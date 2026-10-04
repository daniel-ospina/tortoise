---
title: "Auth email flows — Supabase URL configuration and template contract"
type: engineering
domain: platform
doc_status: live
ownedBy: organisation-design-team
aboutSubjects: auth, email-flows, supabase-ops
aboutObjects: auth-confirm, supabase-redirect-allowlist
created: 2026-09-22
---

# Auth email flows — Supabase URL configuration and template contract

> Issue: #3528 (task 4 — "audit Supabase's redirect allowlist and email templates against every flow").
> Project: `ybetwichurajbfswfeqa` · Dashboard: `https://supabase.com/dashboard/project/ybetwichurajbfswfeqa`
> Companions: `docs/scoping/2026-09-22-3528-auth-email-flows.md` (the scoping record),
> `docs/plans/2026-08-26-1765-ops-runbook.md` (the pre-#4054 redirect-URL list — **partly stale**: it predates
> the BFF move and names neither `/auth/confirm` nor `token_hash`).

## 1. What this document is

Every email-initiated auth flow reaches the app through ONE route, `/auth/confirm`
(`website/apps/dashboard/functions/auth/confirm.ts`). That route reads `token_hash` and `type`, calls GoTrue's
`POST /auth/v1/verify` **server-side**, and holds the verified token behind an explicit consent interstitial
before minting a session.

The route is correct only if two things outside this repo are correct: the **email templates** (they must build a
link the route can consume) and the **redirect allowlist** (GoTrue refuses a `redirect_to` it does not recognise).
Neither can be changed from this repository. This is the in-repo record of what they must be.

## 2. The template contract, per flow

A link the route can consume carries a single-use `token_hash` in the QUERY string:

```
{{ .RedirectTo }}?token_hash={{ .TokenHash }}&type=<hardcoded per template>
```

`{{ .EmailType }}` is **not** a Supabase template variable — `type` must be hardcoded in each template.
The default `{{ .ConfirmationURL }}` is NOT usable: it routes the click through GoTrue's `/auth/v1/verify`,
which answers with `?code=` (PKCE, needs a verifier held by the requesting browser) or `#access_token=`
(a fragment, which never reaches the server). The BFF can consume neither.

| Flow | GoTrue `type` | Template | `redirect_to` set by | Consumed by |
|---|---|---|---|---|
| Signup confirmation | `email` | Confirm signup | `/auth/resend` → `${APP_ORIGIN}/auth/confirm` | `/auth/confirm` |
| Magic link | (PKCE, no `type`) | Magic link | `/auth/start?provider=email` → GoTrue `/auth/v1/authorize`, `redirect_to = /auth/callback` | `/auth/callback` (`?code=`), **not** `/auth/confirm` |
| Password recovery (F15) | `recovery` | Reset password | `/auth/reset` → `${APP_ORIGIN}/auth/confirm` | `/auth/confirm` (then revokes other sessions) |
| Email change | `email_change` | Change email address | none from this repo — GoTrue uses the template's own `{{ .RedirectTo }}` / the project `SITE_URL` | `/auth/confirm` |
| Invite | `invite` | Invite user | invite producer | `/auth/confirm` |

> The GoTrue `email` type also serves a magic-link token *when a template is built with `token_hash`* — and
> `/auth/confirm` handles it. This app's own magic link is not that: it is PKCE via `/auth/start?provider=email`
> and lands on `/auth/callback`. Do not change the Magic link template without changing `start.ts` in the same
> change (§5).

⛔ `signup` and `magiclink` are **deprecated** verify types. Using them fails the exact flows this route exists
to serve. Canonical set: `email`, `recovery`, `invite`, `email_change`. (`SCOPE.md` §5.2, verified against
Supabase docs — this line has been corrected twice; re-verify before editing.)

## 3. Redirect allowlist

Supabase matches redirect URLs by **string match, with `*` wildcards allowed where you put them** — and the BFF relies on the *exact* form, so list the full paths rather than a bare origin plus a hope. Required entries:

| Entry | Why |
|---|---|
| `https://app.premiselabs.co/auth/confirm` | the email-link completion route (§4) |
| `https://app.premiselabs.co/auth/callback` | the PKCE/OAuth return (§2 magic link + social sign-in) |
| `https://app.premiselabs.co/**` | the app's own navigations; the session origin |
| `https://tortoise.premiselabs.co` | legacy links; `website/_redirects` 301s the auth paths to the app origin |
| `https://tortoise.premiselabs.co/welcome.html` | pre-#4054 welcome links still in inboxes |

`mailer_autoconfirm = False` (email confirmations ON) is a prerequisite for the signup-confirmation flow to
exist at all. The hosted signup path creates accounts with `email_confirm=true` by default and sends NO email
(#801) — so the signup-confirmation flow is only reachable when that is deliberately flipped or when
`/auth/resend` is used.

## 4. What the app does with the link

1. `GET /auth/confirm?token_hash=…&type=…` — verifies the token server-side (`verifyOtp`), checks the response
   shape, and persists a **pending** record. It mints **no session** and renders a page naming the account.
2. `POST /auth/confirm` — CSRF-guarded, and bound to the pending record by TWO things that must agree: a host-only
   `__Host-authflow` cookie, and the `pending` id the page itself displayed. The cookie is per-browser, not per-tab,
   and every verified GET overwrites it, so the cookie alone would let a second link opened in another tab redirect
   the consent given on this page to a different account. A mismatch is refused (`400`), never coerced. The route
   then consumes the record (single-use, fail-closed) and mints one `__Host-session`. Recovery additionally revokes
   the user's other sessions (F15) and lands on `/welcome?reset=1`; every other type lands on `/welcome`.

The GET-then-POST shape is what makes the link work **cross-device** without a silent login: the cookie is
created by the GET and consumed by the POST, both in the same browser, so no flow cookie has to pre-exist.

## 5. Operator verification checklist (live — cannot be run from this repo)

This repository cannot read the project's Auth configuration; the checks below need the Supabase dashboard or
the Management API with `SUPABASE_ACCESS_TOKEN`. Record the result on #3528.

- [ ] **Reset Password** template builds `…?token_hash={{ .TokenHash }}&type=recovery`.
- [ ] **Confirm signup** template builds `…?token_hash={{ .TokenHash }}&type=email`.
- [ ] **Change email address** template builds `…?token_hash={{ .TokenHash }}&type=email_change`.
- [ ] Additional Redirect URLs contain `/auth/confirm` on the app origin (§3).
- [ ] Email confirmations are ON (`mailer_autoconfirm = False`) if the signup-confirmation flow is meant to fire.

> **Magic link is out of this audit.** `/auth/start?provider=email` starts a PKCE flow and GoTrue redirects to
> `/auth/callback?code=` (`start.ts` → `AUTH_CALLBACK_URL`), which the TYPED magic-link template from the
> default `{{ .ConfirmationURL }}` does not affect. Changing the Magic link template to a `token_hash` shape
> would route the token to `/auth/confirm` while the flow cookie is bound to a PKCE flow — do not do it without
> changing `start.ts` in the same change.

## 6. Known limits (deliberate, not oversights)

- **Links sent before the #4054 cutover are unsupported.** They carry `#access_token` in a fragment; a 302
  cannot preserve or redeem it. Explicit unsupported-by-decision (`SCOPE.md` §5.4). Near-empty at zero users,
  unrecoverable later.
- **`email_change` depends on the template's own `{{ .RedirectTo }}`.** `/auth/set-email` proxies GoTrue's
  `PUT /auth/v1/user`, which this repo does not give a redirect parameter — so the destination is whatever the
  project template says. If that template is left at the default, the change-email confirmation will not reach
  `/auth/confirm`.
