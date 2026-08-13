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

> **Status (post-#832):** the auth hook is DISABLED and `AUTH_HOOK_SECRET`
> is not set in production — the JWT path is the only live consumer. The
> hook path fails CLOSED (401) while the secret is unset, so re-enabling the
> hook is a dashboard step + `secrets set` away (see #1120).

### tenant-provision required secrets (#1121)

Beyond `AUTH_HOOK_SECRET` (hook path only, currently unset), the success
(201) path reads these secrets — the deploy workflow
(`.github/workflows/supabase-deploy.yml`) verifies the three app-managed
ones exist before shipping (hard fail); `SUPABASE_SERVICE_ROLE_KEY` is a
platform-reserved secret auto-injected into edge functions (warn-only in
the check). A missing one surfaces as a 500/502 at signup:

```bash
supabase secrets set --project-ref ybetwichurajbfswfeqa \
  FASTAPI_URL='https://...'          # data plane (/internal/demo demo seed)
  FASTAPI_INTERNAL_KEY='...'         # Bearer for the data-plane call
  TORTOISE_SECRET_PEPPER='...'       # MUST match tortoise/auth.py (key hashing)
  # SUPABASE_SERVICE_ROLE_KEY — platform-reserved, auto-injected; no need to set
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
> never deploys (it runs the drift check only). To apply pending migrations +
> redeploy the edge functions: `gh workflow run supabase-deploy.yml --ref main`
> (operator-executed). Gated on the `SUPABASE_ACCESS_TOKEN` repo secret
> (token-based push since #883 — the `SUPABASE_DB_URL` secret was removed
> there; the drift gate and apply both use the token path).

**Migration drift gate (#1095):** `deploy-hosted.yml` runs
`.github/scripts/check-migration-drift` before shipping app code — a fail-closed
check that repo migrations are not ahead of the linked project's applied set
(reads `supabase_migrations.schema_migrations` via the Supabase Management API
with `SUPABASE_ACCESS_TOKEN`). A deploy with pending table/column/function/
unique-index migrations is BLOCKED until they are applied; index-only and
remote-ahead drift warn. Operator sequence: dispatch `supabase-deploy` → apply
GREEN → then deploy the app.

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
