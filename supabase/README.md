# Supabase — Tortoise / Premise Labs

Edge functions + migrations for the premise-labs Supabase project
(`ybetwichurajbfswfeqa`, East US). This project also hosts the hosted
Tortoise platform (auth hook, `user_teams`, `analytics_events`) — treat
migrations and function changes as production operations.

## Edge Functions

| Function           | Purpose                         | verify_jwt        |
|--------------------|---------------------------------|-------------------|
| tenant-provision   | Auth hook (after_user_created)  | false (self-auth) |
| waitlist-subscribe | Landing waitlist capture (#373) | false (anon POST) |

### tenant-provision caller auth (#802)

`verify_jwt=false` is required because the `after_user_created` auth hook
sends NO user JWT — Supabase Auth signs the hook request itself. Caller
auth is enforced inside the function:

- **Auth-hook calls** — the Standard-Webhooks signature (`webhook-id` /
  `webhook-timestamp` / `webhook-signature` headers) is verified against
  `AUTH_HOOK_SECRET` (the hook secret configured on `after_user_created`,
  format `v1,whsec_...`).
- **Direct calls** — must present a user JWT (`Authorization: Bearer`)
  whose `id` + `email` match the `user_id`/`email` being provisioned
  (a user can only provision for themselves).
- Everything else (incl. bare anon-key POSTs) → **401**.

Set the secret (dashboard step, #802):

```bash
supabase secrets set --project-ref ybetwichurajbfswfeqa \
  AUTH_HOOK_SECRET='v1,whsec_...'   # value from Dashboard → Auth → Hooks
```

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

| Case                     | Response                                    |
|--------------------------|---------------------------------------------|
| Fresh subscribe          | 200 {ok:true} + confirmation email          |
| Already subscribed       | 200 {ok:true, message:"Already subscribed"} |
| Honeypot filled (bot)    | 200 {ok:true} silently, nothing stored      |
| Rate limited             | 429 with Retry-After                        |
| Captcha failed / missing | 400 (when TURNSTILE_SECRET_KEY set)         |
| Invalid email / body     | 400                                         |
| Bad origin / method      | 403 / 405                                   |

CORS: allowlist echoes premiselabs.co, tortoise.premiselabs.co,
premise-labs.pages.dev (+ *.premise-labs.pages.dev previews, localhost:8788),
with `Vary: Origin`, on every response path.

Anti-abuse:

- Turnstile — when `TURNSTILE_SECRET_KEY` is set a valid token is REQUIRED
  (no token = 400); fail-open only while the secret is unprovisioned
- IP rate limit (10/hr, keyed on the gateway-appended XFF entry,
  best-effort per-isolate)
- per-email rate limit (5/hr, email-bomb guard)
- honeypot field

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

Migrations + functions deploy via `.github/workflows/supabase-deploy.yml`.

> ⚠️ **Migrations are MANUAL dispatch since #771 (flip gating).** A push to
> `main` touching `supabase/**` triggers the workflow but the migration +
> function steps run ONLY on `workflow_dispatch` — a push is recorded and
> never deploys. To apply pending migrations + redeploy the edge functions:
> `gh workflow run supabase-deploy.yml --ref main` (operator-executed).
> See the workflow header for the full flip sequence. Gated on repo secrets
> `SUPABASE_ACCESS_TOKEN` (personal access token) + `SUPABASE_DB_URL` (full
> percent-encoded session-pooler connection string, port 5432).

Manual fallback (equivalent):

```bash
supabase db push --project-ref ybetwichurajbfswfeqa
supabase functions deploy waitlist-subscribe \
  --project-ref ybetwichurajbfswfeqa --no-verify-jwt
```

## Post-deploy smoke checklist (#373)

1. Confirm `TURNSTILE_SECRET_KEY` is set (captcha is REQUIRED once set —
   the form blocks submit until a token exists) and `TURNSTILE_SITE_KEY` is
   populated in `website/index.html`.
2. First `supabase db push`: confirm the project's `supabase_migrations`
   history covers 0001-0004 (`supabase migration list`); if not, baseline
   with `supabase migration repair --status reverted 0001..0004` first so
   the chain cannot abort mid-replay. Also confirm any pre-existing
   `waitlist_subscribers` table has an `id` column (migration 0005 dedupes
   on it before adding the UNIQUE constraint).
3. Submit a test email from the live landing page -> success state shown.
4. Verify the row in Supabase Studio (`waitlist_subscribers`).
5. Verify the confirmation email arrives within 30s (Resend dashboard log).
6. Resubmit the same email -> "Already subscribed" message, no second email.
7. Unsubscribe is a mailto (`hello@premiselabs.co`) processed manually —
   the operator removes the row / excludes the address from launch sends.
