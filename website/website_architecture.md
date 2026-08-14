---
title: "Tortoise Website — Architecture"
type: doc
domain: platform
doc_status: live
created: 2026-08-14
ownedBy: organisation-design-team
---

# Tortoise Website — Architecture

Architecture and surface map for the Tortoise web presence: the marketing site,
auth pages, dashboard, and billing. Written 2026-08-14 from the current
`origin/main`; update as surfaces change.

---

## 1. Hosts & routing

| Host | Serves | Deployment |
| --- | --- | --- |
| `premiselabs.co` | Company page (`website/index.html`) | Cloudflare Pages project `premise-labs` |
| `tortoise.premiselabs.co` | Product page (`website/product.html` at `/`), docs, signin, signup, welcome, invite-accept, legal | Cloudflare Pages project `premise-labs` (same project, host-routed) |
| `app.premiselabs.co` | Dashboard (React SPA, `website/apps/dashboard`) | Cloudflare Pages project `tortoise-dashboard` (separate) |
| `api.premiselabs.co` | Hosted API (FastAPI, `tortoise/hosted_api.py`) | Fly.io app `tortoise-y4mjjq` |

Host routing lives in `website/functions/_middleware.ts`:

- `tortoise.*` root → `product.html` (rewritten), everything else serves its own asset
- `premiselabs.co` + preview hosts root → `index.html` (company page)
- `/product` on non-tortoise hosts → 404 (product page never leaks onto the company host)
- HSTS stamped on every middleware response (matches the API's value, #1003)

### Auth backbone

- Supabase project `ybetwichurajbfswfeqa.supabase.co` — PKCE OAuth (GitHub + Google) + email/password
- Session shared across subdomains via **parent-domain cookie** `sb-tortoise-auth-token` (`Domain=.premiselabs.co`, 7-day)
- The raw API key (`tt_…`) **never** leaves app-origin (sessionStorage on `app.premiselabs.co` only)

---

## 2. Pages

| Page | File | Purpose |
| --- | --- | --- |
| Company | `website/index.html` | Premise Labs brand page, waitlist form |
| Product | `website/product.html` | Tortoise marketing: features, pricing (Free/Solo/Pro/Team), self-hosted section |
| Docs | `website/docs.html` | Static docs: what/how/quickstart/MCP/API |
| Sign in | `website/signin.html` | GitHub / Google / email+password, forgot-password (GoTrue recover) |
| Sign up | `website/signup.html` | GitHub / Google / email+password, check-your-inbox confirmation branch |
| Welcome | `website/welcome.html` | Post-signup provisioning: team + API key (reveal-once), two-path chooser (one-click MCP prompt vs SDK quickstart), auto-redirect to dashboard |
| Invite accept | `website/invite-accept.html` | Public team-invite accept page (`/invite-accept?token=…`), reads `/v1/invites/info` |
| Dashboard | `website/apps/dashboard/` (React + Vite) | Session-gated app: Overview / API Keys / Graphs / Members, billing CTAs |
| Legal | `privacy.html` `tos.html` `license.html` `dpa.html` `security.html` `aviso-privacidad.html` | Footer-linked compliance pages |

---

## 3. Auth flows

### Sign up / sign in (current)

```
/signup or /signin
  → Supabase PKCE OAuth (GitHub/Google) or email+password
  → /welcome.html (provision team + key; claim-aware redirect target)
  → app.premiselabs.co (auto-redirect ~1.2s; manual "Open Dashboard →" fallback)
```

- **Claim branch:** an unclaimed `tt_` key pasted on the dashboard sets a
  non-secret `tt_claim_pending` cookie → OAuth `redirectTo` becomes
  `app.premiselabs.co/?claim=1` (never mints a stray team on welcome).
- **Invite branch:** `?invite_token=` is stashed (sessionStorage on the
  dashboard) → accept fires on `SIGNED_IN` → green "Welcome to the team!" banner.
- **Recovery branch:** forgot-password → GoTrue recover email →
  `/welcome#type=recovery` → reset form → back to signin.

### Dashboard auth card (#1148)

The dashboard renders a **combined login/signup card** when unauthenticated
(`website/apps/dashboard/src/main.jsx`, `!authed` branch), top to bottom:

1. **Log in / Sign up** toggle (`auth-mode-toggle`)
2. **Continue with GitHub / Google** (`auth-providers`)
3. **API key login** (`auth-apikey` — reveal-on-click "Log in with API key")
4. **Email + password** (`auth-email-form`)

Session-gated teams that log in by API key get a full-page **Protect your
account** screen (connect GitHub/Google for key rotation/recovery).

---

## 4. Billing (Stripe)

Backend: `tortoise/hosted_api.py` + `tortoise/billing.py`.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/billing/checkout` | Stripe Checkout session for a validated price (team auth, catalog-validated) |
| `POST /v1/billing/portal` | Stripe Billing Portal session (upgrade/downgrade/cancel) — 404 until a customer exists |

Dashboard surface: **Upgrade CTA** (per-tier `checkout_price_id`) + **Manage
billing** (portal) on the overview/keys tabs (#310 Task 9). Tiers and limits
are canonical in `product/pricing.json`:

| Tier | $/mo | Graphs | Users | API keys | Write ops/mo |
| --- | --- | --- | --- | --- | --- |
| Free | 0 | 1 | 1 | 2 | 10k |
| Solo | 9 | 2 | 1 | 5 | 10k |
| Pro | 25 | ∞ | 2 | 10 | 50k |
| Team | 149 | ∞ | ∞ | 20 | 200k |

Overage: $5 per additional 10k write ops (Pro + Team). Billing is **per team**,
not per seat (#310/#432).

---

## 5. Target architecture (approved direction, 2026-08-14)

The user-approved end state for the auth/marketing surfaces:

1. **Product landing** — `tortoise.premiselabs.co` with a floating transparent
   top menu: only a cyan **Login** button top-right → routes to `/signup`.
2. **Signup page** — new design (combined login/signup card + Premise Labs
   logo); redirects to the dashboard on successful signup.
3. **Dashboard guard** — no OAuth session → redirect to `/signup` (replaces the
   inline auth card as the entry).
4. **Invite accept page** — same design language as the signup page.
5. **Dashboard** — `app.premiselabs.co` (React SPA).
6. **Stripe page** — subscription management (upgrade / downgrade / cancel),
   backed by `/v1/billing/checkout` + `/v1/billing/portal`.

> The new-design work was built into the **dashboard auth card** (#1148) and
> the **invite-accept prototype** (PR #1206: `docs/prototypes/` logo assets +
> `invite-accept.html` topbar with the Premise Labs logo) but was **never
> applied to the static `signin.html` / `signup.html`** — those still show the
> old design. Also, `/logo.png` referenced by `invite-accept.html` is **not
> shipped** (only prototype copies exist under `docs/prototypes/assets/`), so
> the deployed logo silently hides via `onerror`.

---

## 6. Deploy pipeline

`.github/workflows/deploy-pages.yml` (on push to main touching `website/**`):

1. **deploy** — stages onboarding variants, syncs DNS, deploys
   `website/` → Pages project `premise-labs`
2. **deploy-dashboard** — `npm ci && npm run build` in
   `website/apps/dashboard`, deploys `dist/` → Pages project
   `tortoise-dashboard`, then **polls app.premiselabs.co until the new bundle
   hash is served** (the #1086/#1109 stale-bundle failure mode)
3. **verify-legal** — post-deploy E2E on legal pages + signup form safety

Manual deploy (not CI): `website/apps/dashboard/deploy.sh` (same wrangler
command) — the historical source of stale-bundle incidents (#1086, #1109);
avoid for the dashboard.

### Current known issues (2026-08-14)

| Issue | Surface | Symptom |
| --- | --- | --- |
| [#1280 P0](https://github.com/daniel-ospina/tortoise/issues/1280) | dashboard | Black screen: module-top-level `React.useState` (`main.jsx:19`) crashes the whole bundle — dashboard down for everyone |
| [#1281 UX](https://github.com/daniel-ospina/tortoise/issues/1281) | signin/signup/welcome | Topbar shows text "Tortoise." instead of the Premise Labs logo; `favicon.ico` serves HTML |
| [#1225 P1](https://github.com/daniel-ospina/tortoise/issues/1225) | sign in | Post-signup OAuth can land on the dashboard login card (cross-subdomain session gap) |
| [#1151/#1190 CI](https://github.com/daniel-ospina/tortoise/issues/1151) | deploy | verify-legal E2E red on every deploy — masks regression deploys |

---

## 7. Key paths

| What | Where |
| --- | --- |
| Marketing + auth pages | `website/*.html` |
| Host routing | `website/functions/_middleware.ts` |
| Security headers | `website/_headers` |
| Dashboard source | `website/apps/dashboard/src/main.jsx` |
| Dashboard build | `website/apps/dashboard/dist/` (committed) |
| Brand logos | `website/assets/premiselabs-logo.png` · prototype copies `docs/prototypes/assets/logo-*.png` |
| Pricing (canonical) | `product/pricing.json` |
| Hosted API | `tortoise/hosted_api.py` (Fly `tortoise-y4mjjq`) |
| Billing | `tortoise/billing.py` |
| Deploy workflow | `.github/workflows/deploy-pages.yml` |
