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
| deck-view          | Gated deck view capture (#8732) | false (anon POST) |

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
`source` is optional and allow-listed (`landing_page` — the default — or
`investors`): it records which surface the signup came from, since more than one
page feeds this list. An unrecognised value falls back to `landing_page`.

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
app.premiselabs.co, premise-labs.pages.dev (+ *.premise-labs.pages.dev
previews, localhost:8788), with `Vary: Origin`, on every response path. A
refused origin gets the default allowlist entry back, never an echo of the
caller's origin.

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
>
> 🔗 **The same dispatch also flips the app (#3627).** Its last step
> dispatches `deploy-hosted.yml` — the app flip is NOT a second operator
> action, because the tenancy rename (#3543) is a hard cut at the wire level
> and the app and schema must land in one window. The step runs only after
> every apply succeeded, and it fails closed on BOTH halves: if it cannot
> dispatch, the run goes RED; and having dispatched, it WATCHES that run and
> goes RED if it does not reach success — an ordinary merge to `main` touching
> `tortoise/**` starts a run in `deploy-hosted`'s single constant
> `concurrency` group, so a run landing while the flip is still PENDING
> supersedes it after the dispatch was accepted. `cancel-in-progress: false`
> (#10155) stops a RUNNING flip being killed, which is the worse failure; a
> pending one is still superseded by the newest push, because a shared group
> holds one pending run. Dispatch FROM `main` — a non-main run is refused
> before any migration is applied.

**Migration drift gate (#1095):** `deploy-hosted.yml` runs
`.github/scripts/check-migration-drift` before shipping app code — a fail-closed
check that repo migrations are not ahead of the linked project's applied set
(reads `supabase_migrations.schema_migrations` via the Supabase Management API
with `SUPABASE_ACCESS_TOKEN`). A deploy with pending table/column/function/
unique-index migrations is BLOCKED until they are applied; index-only and
remote-ahead drift warn. Operator sequence: dispatch `supabase-deploy` ONCE —
it applies the migrations and (since #3627) dispatches the app flip itself as
its last step, so step 2 and step 3 of the flip sequence share one run and
there is no second dispatch to remember. `deploy-hosted` re-runs this same
drift gate before it ships. If the gate reports `OUT OF ORDER` (a blocking
version older than prod's newest applied version), resolve those FIRST — a
forward migration, or `migration repair --linked --status applied <version>`
when the migration is provably already in prod — because the apply pushes the
whole pending set in filename order.

Manual fallback — for when the automated co-move has already FAILED. **The run
says which way, and the distinction that matters is whether the dispatch was
ACCEPTED.** Six arms exit 1 (`supabase-deploy.yml`, the co-move step), and only
TWO of them mean the flip was never started or is known to have failed:

**Safe to fall back — the dispatch did not land, or the flip is known to have
failed:**

- **`Could not dispatch deploy-hosted`** — the POST itself failed, so the flip
  was never started. Re-run, or flip by hand.
- **`did not reach success`** / **`did NOT flip`** — the dispatch WAS accepted and
  the run reached a terminal non-success state, or a newer push superseded it
  while it was still PENDING. This is the case the fallback below is for.

⛔ **NOT safe to fall back — the dispatch was accepted and the flip may be
RUNNING.** All four of these exit 1, so they must not be read as "nothing
happened":

- **`answered with an unreadable body`** — the app flip is **`UNCONFIRMED`**;
- **`did not name a run`** — the app flip is **`UNOBSERVED`**;
- **`did not FINISH within the … wait`** — the flip is **`UNKNOWN`, NOT failed**.
  The watch gave up while the run was still QUEUED or running (runner-queue
  latency is not ours to bound).
- **`reported a non-zero exit … its state is NOT terminal`** — the flip is
  **`UNKNOWN`, NOT failed**: the watch exited non-zero on a failed POLL (it
  returns the same exit code for a transport/API error as for a failed run, and
  over ~6,600 polls that is a realistic event) while the run's own
  `status`/`conclusion` say it has not finished.

In those four cases do **NOT** run the fallback and do **NOT** re-dispatch —
both queue behind that run and supersede its pending slot, which leaves the
schema applied and the app orphaned: exactly the #3627 window this section
exists to close. Watch it instead
(`gh run watch <id> --repo daniel-ospina/tortoise`), and if it is genuinely
stuck, cancel the `supabase-deploy` job — it holds the `supabase-deploy-apply`
lock for as long as it waits, so every later apply is queued behind it.

Do not run the last line alongside a healthy co-move — `deploy-hosted.yml`
serialises every run in ONE constant `concurrency` group, so a manual dispatch
QUEUES and supersedes the still-PENDING automated flip (it no longer cancels a
running one since `cancel-in-progress: false`, #10155), and the automated run
then reports that its flip was superseded.

```bash
supabase db push --project-ref ybetwichurajbfswfeqa
supabase functions deploy waitlist-subscribe \
  --project-ref ybetwichurajbfswfeqa --no-verify-jwt
supabase functions deploy tenant-provision \
  --project-ref ybetwichurajbfswfeqa --no-verify-jwt
supabase functions deploy deck-view \
  --project-ref ybetwichurajbfswfeqa --no-verify-jwt
gh workflow run deploy-hosted.yml --ref main   # the app flip, after a FAILED co-move
```

**Pre-merge apply of a single migration (out-of-band).** The automated path is
post-merge only: the `supabase-deploy` apply job refuses a non-`main` ref, so a
migration that must land in prod BEFORE its code merges (e.g. #1383, whose
`POST_COLS` cannot name a column the live project does not have yet) has to be
applied by hand. Two rules, both load-bearing:

1. **`--single-transaction`, never a bare `psql -f`.** `psql -f` autocommits per
   statement, so a file that toggles a trigger (`DISABLE TRIGGER … ENABLE
   TRIGGER`, the #1383 backfill) would commit the DISABLE and skip the ENABLE if
   the batch aborted halfway — leaving the trigger off forever, silently. The
   schema-drill harness (`supabase/tests/pglite`) sends each file as ONE query and
   cannot see this: the precondition belongs to the apply command.
2. **Record the version**, or the fail-closed drift gate blocks every later app
   deploy reporting a migration that is in fact already applied. `psql` writes
   the schema, NOT the ledger — that is owned by `db push` / `migration repair`.

```bash
# 1. apply the ONE file atomically, stopping on the first error
psql --single-transaction -v ON_ERROR_STOP=1 "$DATABASE_URL" \
  -f supabase/migrations/<version>_<name>.sql
# 2. record it in the ledger, or check-migration-drift stays RED (it cannot tell
#    "applied out-of-band" from "never applied")
supabase migration repair --linked --status applied <version>
supabase migration list --linked            # confirm: no repo-ahead version left
```

Do NOT add `BEGIN;`/`COMMIT;` to the migration file to get this: the CLI has
already opened a transaction, so a nested `BEGIN` only warns and the inner
`COMMIT` would end the CLI's transaction early.

## Post-deploy smoke checklist (#373)

1. Confirm `TURNSTILE_SECRET_KEY` is set (captcha is REQUIRED once set —
   the form blocks submit until a token exists) and `TURNSTILE_SITE_KEY` is
   populated in `website/index.html` and `website/investors.html`.
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
8. With a real Turnstile site key set: unlock the gate, then confirm the
   mailing-list widget renders correctly — on a gated first visit its container
   (`#stay-in-touch`) is `display: none` while the widget loads.
9. Verify the GATE's write path too: in a fresh/private window (the unlock is
   remembered in `localStorage`, so a browser that already unlocked for step 8
   will not render the gate again — clear `tortoise-investors-access` if you
   reused one), unlock with a test address, then confirm a row in `deck_views`
   (`deck = 'investors'`, `viewed_at` recent). The gate fails open on our own
   failures by design, so a broken write path — migration not applied,
   mis-scoped key, RLS misconfig — is otherwise invisible: the reader still
   sees the deck and no row is written, which looks exactly like nobody having
   visited.

### deck-view contract (#8732)

`POST /functions/v1/deck-view` with a JSON body:

```json
{
  "email": "reader@example.com",
  "hp": "",
  "deck": "investors"
}
```

Writes one row to `deck_views` (append-only) and returns 200 `{ok:true}`. It
sends **no** email and never touches `waitlist_subscribers`: opening a gated deck
records a view, and joining the mailing list stays a separate act (`POST
/functions/v1/waitlist-subscribe`, with `source: "investors"` when it comes from
the same page). `deck` is allow-listed and falls back to `investors`. There is no
captcha on this endpoint; the honeypot and a per-IP limit are what bound it.

Responses:

| Case                     | Response                     |
|--------------------------|------------------------------|
| Recorded view            | 200 {ok:true}                |
| Honeypot filled (bot)    | 200 {ok:true} silently       |
| CORS preflight           | 204, no body                 |
| Rate limited             | 429 with `Retry-After: 3600` |
| Invalid email / body     | 400                          |
| Bad origin / method      | 403 / 405                    |
| Storage failure          | 500                          |

CORS: the same allowlist as waitlist-subscribe (`https://premiselabs.co`,
`https://tortoise.premiselabs.co`, `https://app.premiselabs.co`,
`https://premise-labs.pages.dev`, `*.premise-labs.pages.dev` previews,
localhost:8788), with `Vary: Origin`, on every response path. A refused origin
gets the default allowlist entry back, never an echo of the caller's origin.

Anti-abuse:

- IP rate limit (30/hr, keyed on the gateway-appended XFF entry, best-effort
  per-isolate) — higher than the list's 10/hr because a deck view is a cheaper,
  more repeatable act than a subscribe
- honeypot field
- no captcha: this endpoint verifies no Turnstile token, which is why the gate
  form renders no widget (the mailing-list form renders one once a site key is
  provisioned, since `waitlist-subscribe` does verify)
