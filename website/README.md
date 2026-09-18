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
a Pages Function, `functions/api/contact.ts`, which delivers to
**`hello@premiselabs.co`** — the address the legal pages already name as the
designated outside-product channel (privacy §1, DPA §15, ToS §15.5,
aviso-privacidad). The recipient is a **code constant**, never a request
field, which is what keeps the form from being an open relay.

```text
contact.html ──POST JSON──▶ /api/contact ──▶ Resend ──▶ hello@premiselabs.co
   fields: name, email (Reply-To), message      honeypot (hp) + per-IP rate limit
```

**Credential:** `RESEND_API_KEY` (Pages project env var). **Fail-loud contract:**
with the key unset the endpoint returns `503 not_configured` and the page shows
the visitor the direct-mailto fallback — it never returns a silent success.
Never logged, never echoed, never placed in a URL.

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
2. **Pages env var for the contact form** — bind `RESEND_API_KEY` on the
   `premise-labs` Cloudflare Pages project (production). This is the ONE step
   that makes `/contact` deliver; until it is bound the endpoint answers
   `503 not_configured` exactly as designed. It is the
   `RESEND_API_KEY` entry in `config/required-bindings.yml`.
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
