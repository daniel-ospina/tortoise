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

## Human steps (launch-blocking)

1. **Supabase secrets** (set via
   `supabase secrets set --project-ref ybetwichurajbfswfeqa`):
   `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `TURNSTILE_SECRET_KEY`.
2. **Turnstile site key** → paste into the `TURNSTILE_SITE_KEY` constant in
   `index.html`.
3. **Deploy** — see `supabase/README.md` (CI workflow does it on merge once
   `SUPABASE_ACCESS_TOKEN` + `SUPABASE_DB_URL` repo secrets are set).
4. **Smoke test:** submit a test email → confirm the row appears in Supabase
   Studio and the confirmation email arrives within 30s.

## Tech

- GSAP + ScrollTrigger for canvas graph animation
- Dark slate/cyan palette with green/gold accents
- No framework, no build step — single HTML file
- No analytics/consent scripts on this page (legal constraint, see
  `tests/e2e/test_legal_pages.py`)
