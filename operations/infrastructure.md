---
title: "Premise Labs — Infrastructure"
type: operations
domain: platform
created: 2026-07-28
ownedBy: epistemic-team
subjects.team: epistemic-team
doc_status: draft
---

## Supabase

| Project      | Reference ID         | Region  | Purpose           |
|--------------|----------------------|---------|-------------------|
| premise-labs | ybetwichurajbfswfeqa | East US | Waitlist + hosted |

## Domains

| Domain         | Provider                | Status |
|----------------|-------------------------|--------|
| premiselabs.co | Cloudflare              | Live   |
| Landing page   | CF Pages (premise-labs) | Live   |

## Email

| Service | Purpose               | Status                       |
|---------|-----------------------|------------------------------|
| Resend  | Waitlist confirmation | Pending (#373): key + domain |

## Supabase Edge Functions

| Function           | Purpose                         | verify_jwt        |
|--------------------|---------------------------------|-------------------|
| tenant-provision   | Auth hook (after_user_created)  | false (self-auth) |
| waitlist-subscribe | Landing waitlist capture (#373) | false (anon POST) |

`verify_jwt=false` on tenant-provision is required (auth hooks send no user
JWT); caller auth is enforced inside the function (#802): the hook's
Standard-Webhooks signature (`AUTH_HOOK_SECRET`) or a user JWT matching the
provisioning target. Bare anon-key calls → 401.

## Supabase Tables

| Table                | Purpose                                            |
|----------------------|----------------------------------------------------|
| waitlist_subscribers | Waitlist emails (#373): unique email, consent, RLS |

## Waitlist Abuse Controls (#373)

The waitlist endpoint is public by design (`verify_jwt=false`). Abuse is
contained by:

- Cloudflare Turnstile captcha — when `TURNSTILE_SECRET_KEY` is set, a valid
  token is REQUIRED (no token = 400); fail-open only while unprovisioned
- IP rate limit (10/hr, gateway-appended XFF entry, best-effort per-isolate)
- per-email rate limit (5/hr, guards email-bombing third parties)
- honeypot field

The CORS origin allowlist only restricts browser callers. Deploy checklist:
confirm `TURNSTILE_SECRET_KEY` + the `TURNSTILE_SITE_KEY` constant are set in
production before launch, or captcha stays disabled (rate limits + honeypot
remain as defense-in-depth).

## Related

- Landing page: `website/index.html`
- Edge function: `supabase/functions/waitlist-subscribe/`
- Migration: `supabase/migrations/0005_waitlist_subscribers.sql`
- Waitlist issue: [#373](https://github.com/daniel-ospina/tortoise/issues/373)
