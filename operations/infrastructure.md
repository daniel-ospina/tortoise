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
| tenant-provision   | Auth hook (after_user_created)  | false             |
| waitlist-subscribe | Landing waitlist capture (#373) | false (anon POST) |

## Supabase Tables

| Table                | Purpose                                            |
|----------------------|----------------------------------------------------|
| waitlist_subscribers | Waitlist emails (#373): unique email, consent, RLS |

## Waitlist Abuse Controls (#373)

The waitlist endpoint is public by design (`verify_jwt=false`). Abuse is
contained by:

- Cloudflare Turnstile captcha (server-verified when the secret key and a
  token are both present)
- IP rate limit (best-effort, keyed on the first `x-forwarded-for` entry)
- honeypot field

These are the real gates — the CORS origin allowlist only restricts browser
callers. Deploy checklist: confirm `TURNSTILE_SECRET_KEY` and the
`TURNSTILE_SITE_KEY` constant are set in production before launch, or
captcha is silently off.

## Related

- Landing page: `website/index.html`
- Edge function: `supabase/functions/waitlist-subscribe/`
- Migration: `supabase/migrations/0005_waitlist_subscribers.sql`
- Waitlist issue: [#373](https://github.com/daniel-ospina/tortoise/issues/373)
