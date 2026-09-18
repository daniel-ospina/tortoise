# Premise Labs — Landing Page

Single-scroll landing page for **Premise Labs**, the AI lab behind
[Tortoise](https://github.com/daniel-ospina/tortoise).

**Live:** [premiselabs.co](https://premiselabs.co)

## Deploy

The page is a single static `index.html`. Deploys to Cloudflare Pages via
Direct Upload:

```bash
npx wrangler pages deploy . --project-name=premise-labs --branch=main
```

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
a Pages Function, `functions/api/contact.ts`, which hands the message to the
transport seam as ONE queued item. The decided address
**`hello@premiselabs.co`** — the address the legal pages already name as the
designated outside-product channel (privacy §1, DPA §15, ToS §15.5,
aviso-privacidad) — is shown plainly on the page (lede, and the direct-mailto
fallback below the form), not only in an error path.

```text
contact.html ──POST JSON──▶ /api/contact ──▶ enqueue() ──▶ CONTACT_INTAKE_URL
   fields: name, email (Reply-To), message      honeypot (hp) + per-IP rate limit
      └─ one JSON item: {name, replyTo, message, receivedAt, source}
         seam: functions/_shared/contact-transport.ts
```

**Transport — ⚠️ OPEN DECISION; NOT SETTLED (a queue vs. email).** The owner
has not chosen how a submission travels onward, so nothing here is that
choice. The one network call is isolated behind ONE seam —
`functions/_shared/contact-transport.ts`, entry point `enqueue()` — whose item
shape (name, replyTo, message, receivedAt, source) and result types are
transport-neutral. Swapping transport is a change to that module alone.

**There is no email leg — and that is a constraint, not a gap.** The product's
outbound email sender is already over budget: `premise-labs#393` records Resend
at **200% of its daily quota on two consecutive days**, with an objective of
zero quota-rejected sends. Pointing this form at that sender would add a second
producer to a saturated sender and would fail exactly when a customer needs it,
so the seam **does not send email**. Do **not** provision a send-capable
credential to restore one; the absence of a sending key is the intended state
until the transport decision says otherwise.

**Configuration:** the seam reads exactly one variable, `CONTACT_INTAKE_URL`
(Pages project env var) — the URL the JSON item is POSTed to. **It is not a
secret**, and no authorization header is sent. **Fail-loud contract:** with it
unset the endpoint returns `503 not_configured` and the page shows the visitor
the direct-mailto fallback — it never returns a silent success.

**Spam:** hidden `hp` honeypot field (a filled one is answered with a generic
success so a bot learns nothing) plus a per-isolate rate limit (5 submissions /
10 min / IP). The rate limit is best-effort by nature — isolate-local state —
and is named as such in the function; the honeypot and validation are the
always-on layers.

**Local probe** (see the ops step below for why the first response is a 503):

```bash
cd website && npx wrangler@4 pages dev . --port 8788 --ip 127.0.0.1
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8788/api/contact \
  -H 'Content-Type: application/json' \
  -d '{"name":"Ada","email":"ada@example.com","message":"hello"}'   # → 503
```

## Human steps (launch-blocking)

1. **Supabase secrets** (set via
   `supabase secrets set --project-ref ybetwichurajbfswfeqa`):
   `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `TURNSTILE_SECRET_KEY`.
2. **Pages env var for the contact form** — bind `CONTACT_INTAKE_URL` on the
   `premise-labs` Cloudflare Pages project (production) to the intake endpoint
   the owner chooses. This is the step that makes `/contact` accept
   submissions; until it is bound the endpoint answers `503 not_configured`
   exactly as designed. It is the `CONTACT_INTAKE_URL` entry in
   `config/required-bindings.yml`. **Do not bind a send-capable email
   credential** — the form has no email leg by design (see
   `premise-labs#393` below). (When the transport decision lands, this binding
   moves with the seam — see the seam note above.)
3. **Turnstile site key** → paste into the `TURNSTILE_SITE_KEY` constant in
   `index.html`.
4. **Deploy** — see `supabase/README.md` (CI workflow does it on merge once
   `SUPABASE_ACCESS_TOKEN` + `SUPABASE_DB_URL` repo secrets are set).
5. **Smoke test:** submit a test email → confirm the row appears in Supabase
   Studio and the confirmation email arrives within 30s.

## Tech

- GSAP + ScrollTrigger for canvas graph animation
- Dark slate/cyan palette with green/gold accents
- No framework, no build step — single HTML file
- No analytics/consent scripts on this page (legal constraint, see
  `tests/e2e/test_legal_pages.py`)
