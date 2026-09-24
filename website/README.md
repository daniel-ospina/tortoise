# Premise Labs — Landing Page

Single-scroll landing page for **Premise Labs**, the AI lab behind
[Tortoise](https://github.com/daniel-ospina/tortoise).

**Live:** [premiselabs.co](https://premiselabs.co)

## Deploy

**CI owns the deploy.** A push to `main` touching `website/**` runs
`.github/workflows/deploy-pages.yml`, which stages an explicit upload set, `cd`s
into it, and uploads it. Abridged below to the load-bearing commands — CI also
runs `rm -rf "$STAGE"`, five survival guards that abort the step *before* the
upload if a load-bearing entry did not survive, and takes `RUNNER_TEMP` from the
runner (`${RUNNER_TEMP:?}` there; `mktemp -d` here, so the snippet is runnable).

Run this **from the repository root** — `rsync`'s `website/` source is resolved
relative to the current directory, and this README itself lives inside
`website/`, so copy-pasting from there fails with
`link_stat "website/" failed`:

```bash
cd "$(git rev-parse --show-toplevel)"
STAGE="$(mktemp -d)/pages-upload" && mkdir -p "$STAGE"
rsync -a --exclude='/apps/' --exclude='/migrations/' \
  --exclude='node_modules/' --exclude='/.wranglerignore' --exclude='*.md' \
  website/ "$STAGE/"
# wrangler resolves `functions/` from the CWD, so run FROM the upload root.
cd "$STAGE" && npx --yes wrangler@4 pages deploy "$STAGE" \
  --project-name=premise-labs --branch=main
```

### Why the upload root is staged

`wrangler pages deploy <dir>` uploads **every** file under the directory it is
given, and `.wranglerignore` is **not read by wrangler at all** — the Pages
upload path uses a hardcoded `IGNORE_LIST` and no ignore-file read exists. So a
`.wranglerignore` in the upload root is inert: the file was publicly served (`200`
at `/.wranglerignore`) while it claimed to exclude `apps/`. It was deleted in
#3620 and replaced by the staged upload below.

What the stage excludes, and why:

| Excluded | Why |
|---|---|
| `apps/` | Dashboard + blog-admin sources, including a committed `node_modules` tree. The dashboard deploys separately to `tortoise-dashboard` (`app.premiselabs.co`). |
| `migrations/` | The auth session DDL — internal. |
| `*.md` | Internal docs (`README.md` — this file, `website_architecture.md`, the re-auth plan). |
| `node_modules/` | Dependency tree (belt-and-braces; `/apps/` already prunes it). |
| `.wranglerignore` | Deleted; excluded too, so a re-added file cannot be served. |

What **must** stay in the upload root — the deploy step fails loudly if one is
missing, because each fails *silently* otherwise: `functions/`, plus the step
running with the upload root as its **cwd** (`wrangler pages deploy` resolves
`functions/` against `process.cwd()` — never against the directory it uploads —
so a wrong cwd deploys a site with **no Functions**: dead auth on a green
deploy), `_redirects` and `_headers` (consumed from the root), and `admin/`
(generated into the root by the blog-admin build step).

The post-deploy step `Post-deploy — internal paths are not publicly served
(#3620)` then asserts each internal path returns **404** (exactly 404 — a 5xx
fails the step, because "could not read it" is not "it is not served").

**Residual risk:** a *new* top-level entry under `website/` **is** staged unless
it is excluded in the deploy step — but it can no longer ship on a green deploy.
The deploy job runs `tools/check_pages_upload_root.py` **before** the upload,
which fails if any tracked top-level entry is missing from
`config/pages-upload-classification.txt`, and
`tests/test_pages_bindings.py::test_every_top_level_entry_under_website_is_classified`
pins the same table on the full test selection. A new **nested** file under an
already-public directory (e.g. `website/blog/internal.txt`) is **not** caught by
that classification (it is top-level only) — add it to the exclude list **and**
to the post-deploy 404 assertion step by hand (a nested `*.md` is at least
covered by the blanket `*.md` exclusion, but that is a coincidence, not a pin).

For local troubleshooting only, `npx wrangler pages deploy <dir>` still works —
but never point it at `website/` itself.

## Waitlist form (#373)

The CTA beat (`#beat-cta`) contains the waitlist form. Submissions are
collected end-to-end with the same Resend + Supabase edge function pattern
El Dato uses:

```text
form (index.html) ──fetch JSON──▶ waitlist-subscribe edge function
  ├─ validates email + honeypot + rate limit + Turnstile (optional)
  ├─ INSERT waitlist_subscribers (?on_conflict=email → dedup)
  └─ best-effort confirmation email via Resend (fresh inserts only)
```

**Endpoint:** the `WAITLIST_ENDPOINT` constant in `index.html` points at the
`waitlist-subscribe` edge function on the premise-labs Supabase project.

**Turnstile:** the `TURNSTILE_SITE_KEY` constant in `index.html` starts empty
— the widget + script are only injected when a real site key is set (see
[Human steps](#human-steps-launch-blocking)). Never use a placeholder literal.

**Storage:** `waitlist_subscribers` table (migration `0005`) — email (unique),
source, consented_at. Confirmation email includes an unsubscribe link.
Writes go through the function's service role only (RLS enabled, no anon
policy).

**Tests:** `tests/test_waitlist_form.py` (static) +
`tests/test_waitlist_subscribe.mjs` (Node behavioral harness,
`node --experimental-strip-types tests/test_waitlist_subscribe.mjs`).

## Contact form (#2409)

`/contact` (`website/contact.html`) is the public contact channel. It posts to
a Pages Function, `functions/contact/submit.ts`, which hands the message to the
transport seam as ONE queued item. The decided address
**`hello@premiselabs.co`** — the address the legal pages already name as the
designated outside-product channel (privacy §1, DPA §15, ToS §15.5,
aviso-privacidad) — is shown plainly on the page (lede, and the direct-mailto
fallback below the form), not only in an error path.

```text
contact.html ──POST JSON──▶ /contact/submit ──▶ enqueue() ──▶ CONTACT_INTAKE_URL
   fields: name, email (Reply-To), message      honeypot (hp) + per-IP rate limit
      └─ one item in the intake's envelope:
         { source, source_item_id, payload: { subject, body, name, replyTo,
                                              message, receivedAt } }
         sent with the header `x-inbound-secret`
         seam: functions/_shared/contact-transport.ts
```

**Transport — the intake leg is SETTLED and wired; the outbound-email leg is
an OPEN DECISION (a queue vs. email).** The endpoint is the intake that already
exists — one intake, many producers (premise-labs **#426**), per the relay
ruling recorded on **#2409** — so this form is one more producer into the same
store, with no new infrastructure. The owner has not chosen whether a
submission is ever *answered* by email, so nothing here is that choice: the one
network call is isolated behind ONE seam (`enqueue()`) and swapping the leg
later is a change to that module alone. That the seam speaks the existing
intake's dialect is what makes `CONTACT_INTAKE_URL` mean anything — the
contract, read from the function itself, is:

```text
POST <org-data>/functions/v1/inbound-ingest
x-inbound-secret: <INBOUND_SECRET_<SOURCE>>      (source uppercased)
{ source, source_item_id, payload }
  → 403 unknown_source  when no secret is set for that source
  → 401 unauthorized    when the secret does not match
  → dedupes on (source, source_item_id)
```

Three consequences follow, and all three are recorded in the seam:

1. **The queue leg carries a secret** — the intake's own *inbound* shared
   secret, which submits an item and nothing else (no send, no read). The
   earlier claim here that this leg needs *no* credential was premised on a
   credential-free intake; it is corrected, not deleted. What remains true, and
   is still forbidden, is a **send-capable** credential.
2. **The source name must be env-name-safe**, because the intake builds the
   variable name by uppercasing it: `website_contact`, never
   `website/contact` (which would ask for `INBOUND_SECRET_WEBSITE/CONTACT` —
   unsettable — and earn a `403`).
3. **The item is an envelope**, with `payload.subject`/`payload.body` carrying
   the readable text the intake's risk scan reads.

**The form is an intake producer — because receiving is the mechanism, and
sending is the exception.** The owner's architecture is *we receive*: customers
email `hello@premiselabs.co` and `support@premiselabs.co`, and those messages
are processed automatically through intake; no reply is required for the
product to work, and for most use cases we should not be sending email at all.
Outbound email has exactly **two legitimate homes** — replying to a user who
emailed us first, and **auth flows** (email+password login/sign-up), where
sending ourselves is deliberate because Supabase's auth mail would max its
quota and arrives from Supabase, which is confusing to a new sign-up. This form
routes **into intake**; it is not an email sender.

**The quota is managed, not avoided.** `premise-labs#393` is a **budget to
manage** — its objective is zero quota-rejected sends — never a reason to
refuse to build. An earlier revision of this section recorded the missing email
leg as quota-avoidance; that framing is **retracted**. Do **not** provision a
**send-capable** credential for this form: the absence of a sending key is the
shape of the unresolved email leg, not a quota verdict.

**Configuration:** the seam reads exactly two variables — `CONTACT_INTAKE_URL`
(the endpoint) and `CONTACT_INTAKE_SECRET` (the intake's inbound shared secret
for this producer, sent as `x-inbound-secret`). The secret is an *inbound*
credential: it submits an item and nothing else. **Fail-loud contract:** with
**either** unset the endpoint returns `503 not_configured` and the page shows
the visitor the direct-mailto fallback — it never returns a silent success.

**No promise of a reply.** The confirmation says the message was received and
nothing more. A reply is the exception (outbound email belongs to answering
the support channel, and to auth flows), and while the reader gap is open
(swarm#18407; the #426 interim copy is unbound) "we'll reply" is a commitment
this transport cannot keep. The email field's hint states the restriction on
use, not a commitment to write back.

**Spam:** hidden `hp` honeypot field (a filled one is answered with a generic
success so a bot learns nothing) plus a per-isolate rate limit (5 submissions /
10 min / IP). The rate limit is best-effort by nature — isolate-local state —
and is named as such in the function; the honeypot and validation are the
always-on layers.

**Local probe** (see the ops step below for why the first response is a 503):

```bash
cd website && npx wrangler@4 pages dev . --port 8788 --ip 127.0.0.1
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8788/contact/submit \
  -H 'Content-Type: application/json' \
  -d '{"name":"Ada","email":"ada@example.com","message":"hello"}'   # → 503
```

## Human steps (launch-blocking)

1. **Supabase secrets** (set via
   `supabase secrets set --project-ref ybetwichurajbfswfeqa`):
   `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `TURNSTILE_SECRET_KEY`.
2. **Pages env vars for the contact form** — on the `premise-labs` Cloudflare
   Pages project (production), bind **both** `CONTACT_INTAKE_URL` = the existing
   intake endpoint (`<org-data>/functions/v1/inbound-ingest`) and
   `CONTACT_INTAKE_SECRET` = the inbound secret a below step provisions. This is
   the pair that makes `/contact` accept submissions; until both are bound the
   endpoint answers `503 not_configured` exactly as designed. They are the two
   `CONTACT_INTAKE_*` entries in `config/required-bindings.yml`. **Do not bind a
   send-capable email credential.** (#393 is a budget to manage, never a reason
   to refuse to build — see the contact-form note above.)
3. **Intake secret for this producer** — provision
   `INBOUND_SECRET_WEBSITE_CONTACT` on the org-data intake function and check
   the `website_contact` source is accepted; the intake answers `403
   unknown_source` without it. Same credential as the Pages var above, on the
   receiving side. (Tracked on `swarm#18407`'s lane / the #2409 record.)
4. **Turnstile site key** → paste into the `TURNSTILE_SITE_KEY` constant in
   `index.html`.
5. **Deploy** — see `supabase/README.md` (CI workflow does it on merge once
   `SUPABASE_ACCESS_TOKEN` + `SUPABASE_DB_URL` repo secrets are set).
6. **Smoke test:** submit a test email → confirm the row appears in Supabase
   Studio and the confirmation email arrives within 30s.

## Tech

- GSAP + ScrollTrigger for canvas graph animation
- Dark slate/cyan palette with green/gold accents
- No framework, no build step — single HTML file
- No analytics/consent scripts on this page (legal constraint, see
  `tests/e2e/test_legal_pages.py`)
