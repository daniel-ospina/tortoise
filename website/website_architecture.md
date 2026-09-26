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
| `tortoise.premiselabs.co` | Product page (`website/product.html` at `/`), docs, FAQ (`/faq`), blog, legal — **no session is minted here**; the admin-gated blog Functions (`/blog/api/purge`, `generate-seo`, `generate-cover`) still *accept* the legacy `sb-tortoise-auth-token` cookie on a fallback path (see the auth bullet below). The auth surfaces redirect to the app origin (302, except the `/auth` exact path — see the redirect notes below) | Cloudflare Pages project `premise-labs` (same project, host-routed) |
| `app.premiselabs.co` | **The one session-bearing origin (#4054):** the BFF, the dashboard SPA, `/auth*`, `/welcome`, `/invite-accept`, `/admin`, `/api/v1`, `/blog/api` | Cloudflare Pages project `tortoise-dashboard` (separate) |
| `api.premiselabs.co` | Hosted API (FastAPI, `tortoise/hosted_api.py`) | Fly.io app `tortoise-y4mjjq` |

Host routing lives in `website/functions/_middleware.ts`:

> **Functions root:** an unqualified `functions/…` path below is relative to
> `website/apps/dashboard/functions/` (the APP project) — the BFF moved there in #4054. The marketing
> project's `website/functions/` holds only `_middleware.ts` and `blog/**`, so read a bare path
> against the app tree unless it is prefixed.

- `tortoise.*` root → `product.html` (rewritten), everything else serves its own asset
- `premiselabs.co` + preview hosts root → `index.html` (company page)
- `/product` on non-tortoise hosts → 404 (product page never leaks onto the company host)
- **auth surfaces redirect to the app origin — and WHICH layer does it depends on the host:**
  - the middleware's `APP_ONLY` branch (`/auth`, `/signup`, `/welcome`, `/invite-accept` + `.html`
    twins) runs **only** on the exact `premiselabs.co` host (`COMPANY_HOSTS`),
  - `/auth` and `/auth.html` 301 **unconditionally** — the redirect #4054 shipped, left alone because
    it is already in browsers' caches and changing it is its own decision — while `/auth/*` answers
    **302** (`#4346`) and `/admin` **302**s (`#4409`), all **unconditionally** (those cover
    `tortoise.*` and previews/dev, so a request never falls through to a deleted asset). A NEW
    branch for a moved surface is 302, not 301: a 301 is browser-persistent and deploy-unreachable.
    Three surfaces, two answers, and the difference is deliberate
  - on the tortoise host the other three (`/signup`, `/welcome`, `/invite-accept` + twins) are 301'd
    by the **static** `website/_redirects`, not by the middleware. Deleting those rules breaks the
    tortoise host — the middleware does not cover it.
  A Function beats `_redirects` for inbound routing, so a route with a Function is never affected by
  a static rule; `_redirects` is what covers the paths without one
- HSTS stamped on every middleware response (matches the API's value, #1003)

### Auth backbone

- Supabase project `ybetwichurajbfswfeqa.supabase.co` — PKCE OAuth (GitHub + Google) + email/password
- **Session (current, #4054):** a server-side BFF on the app origin. **As its BFF session**, the
  browser holds only an opaque
  **`__Host-session`** cookie — `HttpOnly; Secure; SameSite=Lax; Path=/`, and **no `Domain`
  attribute**, so the `__Host-` prefix makes it host-only by construction and it can never
  authenticate a second subdomain. The BFF's access/refresh tokens live server-side in D1 (the
  `SESSIONS` binding) and never reach the browser; revoking a session marks its row revoked
  (`revoked = 1`) — the row is retained, not deleted. Issued by
  `website/apps/dashboard/functions/_shared/auth/session.ts`. A **separate legacy** JS-readable
  parent-domain cookie is still issued and still accepted — see the ruling bullet below.
- **OVERRIDES:** the standard cross-subdomain session — a `Domain=.premiselabs.co` cookie shared by
  every subdomain — is **rejected**. It is JS-reachable from any subdomain and forfeits the `__Host-`
  prefix; one session-bearing origin is worth the extra 301. The recorded ruling is the auth-topology
  decision on **#3501 / #4054** (full rationale: the private `premise-labs` repo,
  `engineering/auth/SCOPE.md` §3, §4 W6, §13 — cited across this repo's Functions the same way, and
  deliberately marked as outside this one). The JS-readable bridge
  (`website/assets/supabase-session.js`, cookie `sb-tortoise-auth-token`) is
  **not** the session backbone: **no BFF page loads** it
  (`tests/test_cross_subdomain_cookie_sync.py` pins `PAGES = []`). That is not the
  whole story though — the cookie is still **issued** by the MCP consent page in
  `tortoise/oauth.py` (`/oauth/authorize` on `api.premiselabs.co`,
  `Domain=.premiselabs.co`, JS-readable) and still **accepted** by two live
  surfaces: the blog-admin console's supabase-js data layer
  (`website/apps/blog-admin/src/lib/supabase.ts`, which recovers the session from
  it on init) and `website/functions/blog/_shared/admin-auth.ts`'s legacy cookie
  fallback (Bearer first, then the cookie). So a JS-readable parent-domain session is in play, which is exactly
  what this `OVERRIDES` ruling exists to prevent. Stop issuing: **#3524**. Stop
  accepting: **#4178**. See `docs/auth-architecture.md` §2.1 “Legacy cohort” and
  §4 item 1 (which is OPEN, not closed).
- The raw API key (`tt_…`) **never** leaves app-origin (sessionStorage on `app.premiselabs.co` only)

---

## 2. Pages

| Page | File | Purpose |
| --- | --- | --- |
| Company | `website/index.html` | Premise Labs brand page, waitlist form |
| Product | `website/product.html` | Tortoise marketing: features, pricing (Free/Solo/Builder/Team), self-hosted section |
| Self-hosted | `website/self-hosted.html` | Self-hosted setup guide at `/self-hosted` (install, daemon, onboarding, MCP connect, role memory); guarded by `test_harness_mcp_config.py`, crawled by `tests/e2e/test_legal_pages.py` |
| Blog | `website/functions/blog/[[path]].ts` (SSR at `/blog` + `/blog/:slug`) · `website/functions/blog/sitemap.xml.ts` (`/blog/sitemap.xml`) · `website/functions/blog/feed.xml.ts` (`/blog/feed.xml`) · `website/functions/blog/api/posts/[[path]].ts` (agent publish/edit, `/blog/api/posts`) · `website/blog/` (favicon, og-image) | Tortoise blog: server-rendered markdown posts (Supabase `blog_posts`), agent-published with review queue, PostHog + consent |
| Docs | `website/docs.html` | Static docs: what/how/quickstart/MCP/API |
| FAQ | `website/faq.html` | Design-objection FAQ at `/faq`: why relationships are stored, how EP confidence is computed, and pointers to pricing/legal for the commercial questions. Defers mechanism detail to `/docs` rather than restating it |
| Auth (single page) | `website/apps/dashboard/public/signup.html`, served at `/auth` by `website/apps/dashboard/functions/auth/index.ts` | Combined Log in / Sign up card — GitHub / Google / email+password (modal login + forgot-password) / API key; `/signin*` 301 → `/auth`; `/signup` is a redirect-free alias |
| Welcome | `website/apps/dashboard/public/welcome.html`, served at `/welcome` by `website/apps/dashboard/functions/welcome.ts` | **Recovery landing only.** The page renders `#loading` / `#error-state` / `#reset-panel` and nothing else — no team creation, no API-key reveal. Post-signup provisioning happens **in the dashboard** (first-run onboarding). `/welcome` 302s a **signed-in** visitor to the app (`/?claim=1` while a claim is pending); no cookie or a dead session 302s `/auth?next=…&stale=1`; an unreachable session store 503s. **Any `reset` present** + a valid session is the one case that renders the page — unless `env.ASSETS` is unbound, in which case it 503s instead |
| Invite accept | `website/apps/dashboard/public/invite-accept.html` | Public team-invite accept page (`/invite-accept?token=…`), reads `/v1/invites/info` |
| Dashboard | `website/apps/dashboard/` (React + Vite) | Session-gated app: Overview / API Keys / Graphs / Members / Billing / **Profile** (login methods + recovery banner, #1765) |
| Legal | `privacy.html` `tos.html` `license.html` `dpa.html` `security.html` `aviso-privacidad.html` | Footer-linked compliance pages |

---

## 3. Auth flows

### Auth (single page — /auth, #1490/#1493; on the app origin since #4054)

```
/auth on app.premiselabs.co (functions/auth/index.ts serves the /signup asset)
  → Supabase PKCE OAuth (GitHub/Google) or email+password or API key
  → app.premiselabs.co/?claim=1 when a tt_ claim marker is in flight
  → the dashboard runs first-run onboarding (team + API key, reveal-once)
```

- `/auth` is served **on the app origin**. Both layers route there, and which one applies depends on
  the host: `/auth` 301s **unconditionally** in `website/functions/_middleware.ts` (covering
  `tortoise.*` and previews/dev); `/signup`, `/welcome` and `/invite-accept` are 301'd by the
  middleware's `APP_ONLY` branch **only on the exact `premiselabs.co` host**, and on the tortoise host
  by the static `website/_redirects`. The visitor lands where the session cookie lives.
- `/signin`, `/signin/`, `/signin.html` → 301 `/auth` on the **tortoise** host. The company host takes
  one extra hop first (`premiselabs.co/signin` → `tortoise.premiselabs.co/signin`), and the app origin
  has no `/signin` route at all (it 404s) — `/signin` is a marketing-host alias, not a session route.
- `/signup` variants serve the same page as a legacy alias (canonical `/auth`).
- **`/welcome` is not a provisioning page and not a rendered page by default** —
  `functions/welcome.ts` decides: signed in → 302 to the app (with `?claim=1` when the claim marker
  is in flight); no cookie / dead session → 302 `/auth?next=…&stale=1`; store unreachable → 503
  (terminal — a database blip must never be reported as "signed out"); **any `?reset` parameter** +
  a signed-in session → serves the reset-panel asset, the **one** rendered case (§2 Welcome). Team +
  API-key provisioning is the dashboard's first-run onboarding, not this page.

- **Claim branch:** an unclaimed `tt_` key pasted on the dashboard sets a
  non-secret `tt_claim_pending` cookie → OAuth `redirectTo` becomes
  `app.premiselabs.co/?claim=1` (never mints a stray team on welcome).
- **Invite branch:** `?invite_token=` is stashed (sessionStorage on the
  dashboard) → accept fires once `/api/session` reports a session → green
  "Welcome to the organization! Your membership is active." banner.
- **Recovery branch:** forgot-password (auth-page login modal) → GoTrue
  recover email → `/auth/confirm` (verifies the single-use `token_hash`, mints the
  session) → `/welcome?reset=1` → the `#reset-panel` → `POST /auth/update-password`.
  The emailed link is deliberately NOT `/welcome?reset=1`: that route requires a
  session, and a recovering user has none.

### Dashboard session gate (#4054)

The dashboard no longer renders an inline auth card. `main.jsx` asks
`GET /api/session` — the single source of session truth (`200 {user}` / `401` not signed in / `503`
store unreachable) — shows "Checking your session…" while it waits, and on **401** calls
`bounceToAuth(search, errorHash)` (`main.jsx`), which delegates to the pure module
`src/authBounce.js::authBounceTarget({ pathname: window.location.pathname, search, errorHash })`. That
builds a **same-origin** `/auth?<search>&next=<pathname+query>#<errorHash>` target, and `replace()`
(not `push`) is used, so Back cannot land on the signed-out page and re-trigger the bounce. The
**query string is preserved** (it carries the #1224 OAuth error banner) and, since #3930, the
**requested pathname** rides as `/auth`'s `next` — the one carrier the auth page reads. The auth page
re-validates that value (origin comparison + the route allowlist mirrored from `authBounce.js`:
`/welcome` and `/team` as single pages, `/admin` as a console subtree) before navigating, so a
forged absolute or protocol-relative value is ignored and the visitor lands on the app root. The
**fragment** is forwarded only when it carries OAuth error params (#1909), never a token — under the
BFF no fragment holds a credential.

Session-gated teams that log in by API key still get a full-page **Protect your account** screen
(connect GitHub/Google for key rotation/recovery).

The **Profile tab** (#1765) is the post-claim identity surface: login-method
inventory (`GET /v1/user/identity`), add GitHub/Google/email+password
(link-intent → vendored `linkIdentity` → link-commit), unlink (atomic
never-below-2 floor), and a recovery banner for single-login-method users
("your account is protected by only one login method…"). `teams.email` is
demoted to a contact field — identity facts live on the Supabase user.

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
| Builder | 25 | ∞ | 2 | 10 | 50k |
| Team | 149 | ∞ | ∞ | 20 | 200k |

Overage: $5 per additional 10k write ops (Builder + Team). Billing is **per team**,
not per seat (#310/#432).

---

## 5. Target architecture (approved direction, 2026-08-14)

The user-approved end state for the auth/marketing surfaces:

1. **Product landing** — `tortoise.premiselabs.co` with a floating transparent
   top menu: only a cyan **Login** button top-right → routes to `/auth`.
2. **Auth page** — one page at `/auth` (combined login/signup card + Premise
   Labs logo; signin.html retired via 301); redirects to the dashboard on
   successful signup.
3. **Dashboard guard** — no OAuth session → redirect to `/auth` (replaces the
   inline auth card as the entry).
4. **Invite accept page** — same design language as the signup page.
5. **Dashboard** — `app.premiselabs.co` (React SPA).
6. **Stripe page** — subscription management (upgrade / downgrade / cancel),
   backed by `/v1/billing/checkout` + `/v1/billing/portal`.

> The new-design work was built into the **dashboard auth card** (#1148) and
> the **invite-accept prototype** (PR #1206: `docs/prototypes/` logo assets +
> `invite-accept.html` topbar with the Premise Labs logo). The combined-card
> design was subsequently applied to the static auth page (`/auth`, served
> from `signup.html` — #1287/#1490/#1493) and `/logo.png` ships from the
> website root (#1323).

---

## 6. Deploy pipeline

`.github/workflows/deploy-pages.yml` (on push to main touching `website/**`,
`tortoise/onboarding/**`, `product/pricing.json`, or the workflow file itself):

1. **deploy** — verifies the onboarding instructions mirror, syncs DNS, deploys
   `website/` → Pages project `premise-labs`. `admin/` is **not** staged here: the
   middleware 302s `/admin` to the app origin before any asset is read (#4171;
   the status is 302 not 301 per `SCOPE.md` §12 — #4409).
2. **deploy-dashboard** — two separate builds: `npm ci && npm run build` in
   `website/apps/dashboard` (the dashboard SPA), then the **blog admin** SPA in
   `website/apps/blog-admin`, copied into `website/apps/dashboard/dist/admin/`
   (#4171). It deploys `dist/` → Pages project `tortoise-dashboard`
   (the app origin: BFF + dashboard + console), then **polls
   app.premiselabs.co until the new bundle hash is served** (the #1086/#1109
   stale-bundle failure mode). Needs `SUPABASE_URL` / `SUPABASE_ANON_KEY` repo
   secrets — the blog-admin env guard throws at runtime, so a missing secret
   would ship a white-screen SPA
3. **verify-legal** — post-deploy E2E on legal pages + signup form safety

Manual deploy (not CI): `website/apps/dashboard/deploy.sh` (same wrangler
command) — the historical source of stale-bundle incidents (#1086, #1109);
avoid for the dashboard.

### Known-issues snapshot (2026-08-14) — all five resolved

The five issues tracked here at the time — #1280 (dashboard black screen), #1281 (topbar logo),
#1225 (post-signup cross-subdomain gap), #1151/#1190 (verify-legal red every deploy) — were all
closed by 2026-08-16. Current web-surface work is tracked on GitHub, not mirrored here; a snapshot
of closed issues reads as live breakage, which is why this table is now a note.

---

## 6.5 SEO & crawler surface (2026-08-17)

Multi-host sitemap + canonical setup added to fix the Google Search Console
coverage report (2026-08-17): "Alternate page with proper canonical tag" on
the 5 legal pages crawled via premiselabs.co, 404s on trailing-slash URLs,
duplicate product/index URLs without canonicals, and 401s on the gated
dashboard.

| Asset | Host | Notes |
| --- | --- | --- |
| `website/robots.txt` | both premise-labs hosts | Google **cross-submission**: lists all four sitemap locations; each sitemap contains only same-host URLs (protocol requirement) |
| `website/sitemap-company.xml` | `premiselabs.co` | single URL (`/`) — company page |
| `website/sitemap-product.xml` | `tortoise.premiselabs.co` | `/`, `/docs`, `/faq`, `/self-hosted`, `/security`, 5 legal pages (note: the auth URLs are NOT in this sitemap — `/signin` 301s to `/auth`, and `/auth` itself moved to the app origin in #4054) |
| `website/_redirects` | both premise-labs hosts | trailing-slash 301s → extensionless canonicals; `/index.html → /`; `.html` dedupe for non-auth pages |
| `website/apps/dashboard/public/{robots.txt,sitemap.xml}` | `app.premiselabs.co` | Vite copies `public/` → `dist/` at build time; `dist/` is itself a build artifact (untracked since #3775 — built by `deploy.sh`, `deploy-pages.yml` and the `dashboard-js-tests` CI job before it is served), so `public/` is the only committed copy |

Rules:
> ⚠️ **Part of this SEO contract is OPEN (#3521):** `/auth` (and `/signup`) now 301 to the app
> origin, so treating them as `tortoise.*` canonicals would hand Google a redirect. The **sitemap**
> side is already done — `website/sitemap-product.xml` omits `/auth` — but the **canonical / robots**
> side is tracked by #3521 and NOT yet done (`public/signup.html` still carries
> `<link rel="canonical" href="https://tortoise.premiselabs.co/auth">`, and `robots.txt`'s comment
> still lists auth on the tortoise host). The rest of this section describes the pre-#4054 contract
> for those URLs.

- **Host consolidation (the core fix):** legal pages + docs + FAQ + auth are
  canonical on `tortoise.premiselabs.co` (where the service operates and the
  product footer links them). The middleware 301s the tortoise-only pages
  from the exact `premiselabs.co` hostname to their canonical — a 301 is the
  strongest consolidation signal Google has, stronger than the canonical
  tags (which alone left the copies live and produced the coverage-report
  "alternate page" rows). Scoped to the exact company host: local dev
  (`127.0.0.1`) and `*.pages.dev` previews keep the pass-through (not
  indexed; E2E suite runs against a dev server). This supersedes the
  original "legal pages 200 on both hosts" decision (locked in
  `docs/plans/2026-08-08-657-legal-pages-plan.md` G-gate ③/⑨; the CI
  verify-legal poll + `tests/e2e/test_legal_pages.py` + `test_welcome_page.py`
  were updated to the new contract in the same change).
- **Canonical tags:** all indexable pages carry `<link rel="canonical">` — `index.html` → `https://premiselabs.co/`, `product.html` → `https://tortoise.premiselabs.co/` (served at `/`), plus docs/faq/self-hosted/auth. Legal pages already had them.
- **Auth-gated pages** (`welcome.html`, `invite-accept.html`) are `noindex,nofollow` and excluded from sitemaps. Signup stays indexable (a redirect-free alias of `/auth`); the legacy `/signin` URLs 301 → `/auth`. Keep `.html` auth URLs as-is — OAuth `redirectTo` and invite emails reference them directly.
- **Middleware** (runs before `_redirects`) 301s `/product`, `/product.html`, `/index.html` → `/` on the tortoise host (dedupe of the root rewrite); the company host keeps the 404 for `/product*`.
- **Search Console submission:** add all four sitemap URLs from robots.txt as separate properties (the four sitemaps cover three hosts — `tortoise.premiselabs.co` carries the product AND the blog sitemap).

## 6.6 Blog surface (2026-08-27)

- **Public:** `/blog` (index: card grid, tag filter, SSR) + `/blog/:slug` (article, SSR) via `website/functions/blog/[[path]].ts` — markdown→sanitized HTML, full SEO head (title/meta/OG/Twitter/JSON-LD BlogPosting + BreadcrumbList), canonical, consent.js + PostHog snippet, ASSETS fallback for static assets.
- **Agent API:** `website/functions/blog/api/posts/[[path]].ts` (#1795) — `X-Agent-Key` (sha256 vs `blog_agent_keys`); POST (default `draft` → review queue; `published` on explicit owner instruction, audited) + PATCH own posts; rate-limited.
- **SEO:** dynamic `sitemap.xml` (published only) + RSS `feed.xml` (published only); a `/blog` + `/blog/…` rule in middleware 301s the company host to the tortoise host (`/blog/api/*` excluded — a 301 would turn a POST into a GET and drop the body; exact path-SEGMENT prefix, so `/blogpost` must not match); robots.txt cross-submission now lists the blog sitemap.
- **Content:** Supabase `blog_posts`/`blog_agent_keys`/`blog_admins` (migration `20260827000001_blog_cms.sql`); admin SPA (ElDato editor port, #1798) served by the admin gate at `/admin` on the app origin — hash-routed (`#/`, `#/new`, `#/edit/:id`, `#/audit`), and for any `/admin/*` CLIENT route the gate falls through to `/admin/index.html` (real assets under `/admin/assets/…` pass through the asset router first); images in `blog-images` bucket.
- **Epic:** docs/epics/2026-08-27-tortoise-blog-cms/.

## 7. Key paths

| What | Where |
| --- | --- |
| Marketing pages | `website/*.html` |
| Auth pages (app origin) | `website/apps/dashboard/public/{signup,welcome,invite-accept}.html` |
| BFF + auth Functions | `website/apps/dashboard/functions/` |
| Host routing / auth 301s | `website/functions/_middleware.ts` |
| Security headers | `website/_headers` |
| Dashboard source | `website/apps/dashboard/src/main.jsx` |
| Dashboard build | `website/apps/dashboard/dist/` — a vite build artifact, untracked since #3775 (source is `src/` + `public/`; built by `deploy.sh`, `deploy-pages.yml` and the `dashboard-js-tests` CI job) |
| Brand logos | `website/assets/premiselabs-logo.png` · prototype copies `docs/prototypes/assets/logo-*.png` |
| Pricing (canonical) | `product/pricing.json` |
| Hosted API | `tortoise/hosted_api.py` (Fly `tortoise-y4mjjq`) |
| Billing | `tortoise/billing.py` |
| Deploy workflow | `.github/workflows/deploy-pages.yml` |
