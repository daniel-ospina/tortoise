# Supabase — Tortoise / Premise Labs

Edge functions + migrations for the premise-labs Supabase project
(`ybetwichurajbfswfeqa`, East US). This project also hosts the hosted
Tortoise platform (auth hook, `user_teams`, `analytics_events`) — treat
migrations and function changes as production operations.

## Edge Functions

| Function           | Purpose                         | verify_jwt        |
|--------------------|---------------------------------|-------------------|
| tenant-provision   | Auth hook (after_user_created)  | false             |
| waitlist-subscribe | Landing waitlist capture (#373) | false (anon POST) |

### waitlist-subscribe contract

`POST /functions/v1/waitlist-subscribe` with a JSON body:

```json
{
  "email": "user@example.com",
  "hp": "",
  "cf-turnstile-response": "token"
}
```

`hp` is a honeypot (must stay empty for humans).
`cf-turnstile-response` is only sent when `TURNSTILE_SITE_KEY` is set.

Responses:

| Case                  | Response                                    |
|-----------------------|---------------------------------------------|
| Fresh subscribe       | 200 {ok:true} + confirmation email          |
| Already subscribed    | 200 {ok:true, message:"Already subscribed"} |
| Honeypot filled (bot) | 200 {ok:true} silently, nothing stored      |
| Rate limited          | 429 with Retry-After                        |
| Captcha failed        | 400                                         |
| Invalid email / body  | 400                                         |
| Bad origin / method   | 403 / 405                                   |

CORS: allowlist echoes premiselabs.co, tortoise.premiselabs.co,
premise-labs.pages.dev (+ *.premise-labs.pages.dev previews, localhost:8788),
with `Vary: Origin`, on every response path.

Anti-abuse: Turnstile (server-verified only when both the secret key and a
token are present — fail-open), in-memory IP rate limit (10/hr per first
`x-forwarded-for` entry, skipped when absent), honeypot field.

Storage: `waitlist_subscribers` (migration 0005) — `email` UNIQUE (dedup via
`?on_conflict=email` + `Prefer: resolution=ignore-duplicates`), `source`,
`consented_at`. RLS enabled, `service_role` policy only (no anon access).

## Secrets

Set on the function (never in the browser):

```bash
supabase secrets set --project-ref ybetwichurajbfswfeqa \
  RESEND_API_KEY=re_... \
  RESEND_FROM_EMAIL='Premise Labs <noreply@premiselabs.co>' \
  TURNSTILE_SECRET_KEY=0x...
```

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are injected automatically by
the Supabase platform.

## Deploy

Migrations + functions deploy via `.github/workflows/supabase-deploy.yml`
on `main` when `supabase/**` changes — gated on repo secrets
`SUPABASE_ACCESS_TOKEN` (personal access token) + `SUPABASE_DB_URL` (full
percent-encoded session-pooler connection string, port 5432).

Manual fallback:

```bash
supabase db push --project-ref ybetwichurajbfswfeqa
supabase functions deploy waitlist-subscribe \
  --project-ref ybetwichurajbfswfeqa --no-verify-jwt
```

## Post-deploy smoke checklist (#373)

1. Confirm `TURNSTILE_SECRET_KEY` is set (else captcha is silently off) and
   `TURNSTILE_SITE_KEY` is populated in `website/index.html`.
2. Submit a test email from the live landing page -> success state shown.
3. Verify the row in Supabase Studio (`waitlist_subscribers`).
4. Verify the confirmation email arrives within 30s (Resend dashboard log).
5. Resubmit the same email -> "Already subscribed" message, no second email.
