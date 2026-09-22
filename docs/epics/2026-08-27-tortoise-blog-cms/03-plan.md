---
title: "Tortoise Blog + CMS — Implementation Plan"
type: engineering
domain: growth
doc_status: draft
created: 2026-08-27
ownedBy: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: tortoise-website
epic: tortoise-blog-cms
---

# Implementation Plan — Tortoise Blog + CMS

**Inputs:** scope `01-scope.md` (14 E2E, human-gated) · test-design `02-test-design.md` (surface map; filed as issue **#1802**) · research `docs/research/2026-08-27-tortoise-blog-cms/research.md`
**Locked decisions:** repo A (`website/` in tortoise repo) · ElDato editor port · per-agent keys · `/blog` URL · share 1A/2A/3A (card grid, editorial article, share under title + mobile bottom bar) · PostHog reuse · RSS in scope · **publish flow (2026-08-27 refinement): default = review queue; agents publish directly only when the owner explicitly asks; agents may edit their own posts; generous rate limits**
**Rev:** 3 (publish-flow refinement: queue-default + direct-publish option + agent edit (PATCH, own posts) + generous rate limits)

---

## 1. User Journeys

### Personas
| ID | Persona | Goals | Trust level |
|----|---------|-------|-------------|
| P1 | Agent publisher (pi, other agents) | Write + publish blog posts without human dependency | High (owner-trusted, per-agent keys) |
| P2 | Owner/reviewer (Daniel) | Review what agents published; edit, fix, take down; see audit | Full (owner/admin) |
| P3 | Public visitor | Read Tortoise blog; share posts | Anonymous |
| P4 | AI crawler (GPTBot, PerplexityBot, Googlebot) | Discover + read content from raw HTML | Anonymous |

### Journeys
| Journey | Persona | Entry → Exit | Edge cases |
|---|---|---|---|
| J1 Agent publishes a post | P1 | has topic → writes markdown → calls publish API (per-agent key) → gets URL back → live (direct) or in review queue (default) | schema invalid, slug taken (409 on create), API down, hold_for_review set, rate limit |
| J2 Owner reviews queue | P2 | opens /admin/blog → review queue → preview → mark reviewed / edit / request changes (with note) / unpublish | empty queue, queue with hold_for_review items, non-owner blocked |
| J3 Owner edits a post (ported editor) | P2 | opens post in editor → markdown imported → refines → saves (draft or republish) | markdown roundtrip loss, image upload failure, autosave conflict |
| J4 Owner unpublishes / archives | P2 | opens post → unpublish (clears review state) or archive (terminal) → confirmation | already-archived post (rejected), republish re-enters review queue |
| J5 Visitor browses index | P3 | lands on /blog → card grid → filters by tag → opens post | empty blog, tag with no posts |
| J6 Visitor reads + shares | P3 | opens /blog/:slug → reads → shares via X/LinkedIn/FB/WhatsApp/copy | share network down (opens in new tab, non-blocking), mobile native share |
| J7 AI crawler reads raw HTML | P4 | requests /blog/:slug → receives SSR HTML + JSON-LD + meta | draft/hold leak (must 404), company-host redirect, cached-stale HTML on edited posts |

---

## 2. Workflows

### W1 — Agent publish pipeline (J1)
`Agent → POST /blog/api/posts (X-Agent-Key) → [Pages Function] validate key + zod schema → INSERT blog_posts → respond {id, slug, url}`
- **Publish mode (locked, 2026-08-27 refinement):** the API accepts `status`;
  - **default `'draft'`** — post lands in the **review queue** (not public) until the owner publishes;
  - **`'published'`** — only when the owner explicitly asked the agent to publish directly (recorded in audit via `published_by`/`published_at`). No manual step in that path — the agent's instruction IS the gate (owner-trusted agents, per-agent keys).
- **Agent edits (locked):** `PATCH /blog/api/posts/:slug` — an agent may update **posts it created** (`created_by = its agent_name`): content, meta, tags, status, hold_for_review. Slug is immutable after create. Rewrites/iteration are updates, never blocked by the create-slug 409.
- **Automation:** everything up to the response. **Manual triggers:** none for direct-publish; owner publish for queue posts.
- **Failure modes:** invalid key → 401; schema error → 400 + field errors; empty body at publish → 400; malformed markdown (unbalanced fences / body > 100KB) → 422; create-slug conflict → 409 (agents never steal another post's slug); edit of a post not created by this agent → 403; Supabase down → 503.
- **Rate limits (generous, per agent key):** 120 req/min burst, 2,000 req/day. A rewrite loop of N iterations is N requests over minutes — far under the limit. The limiter is unit-tested with lowered thresholds (non-deterministic across isolates at real limits).

### W2 — Review queue + post-publish review loop (J2/J3)
The **review queue** surfaces three kinds of items:
1. **Agent drafts** (`status='draft'`, `created_by` = agent) — the default path: agent writes, owner publishes.
2. **Unreviewed direct-published** (`status='published' AND reviewed_at IS NULL`) — post-publish review.
3. **Held** (`hold_for_review=true`) — stays fully private until cleared.

Owner actions: mark reviewed (sets `reviewed_by`/`reviewed_at`) | edit+republish | request changes (`status→draft` + `review_note` — agent can then rewrite via PATCH and republish) | unpublish (`status→draft`; clears review state) | archive (terminal).
- **Republish semantics:** after unpublish, a subsequent publish re-enters the review queue (`reviewed_at=NULL`) — never silently pre-reviewed.
- **Human gate:** unpublish / archive / request-changes are human-only. Publish is human for queue items; direct-published items were agent-published by explicit owner instruction.

### W3 — Hold-for-review (J1 variant)
`Agent sets hold_for_review=true → render Function excludes post from ALL public surfaces (article, index, sitemap, feed) → admin queue surfaces it as "held" → owner clears flag → post goes public everywhere`
- **Failure mode:** images still fetchable via CDN URL (documented tradeoff, scope item 1).

### W4 — Content lifecycle
`draft → published (owner publishes queue items; agent direct-publishes only on explicit instruction) | published → draft (unpublish / request-changes) | draft|published → archived (terminal — NO transitions out of archived) | agent PATCH: draft↔published on own posts, content edits on own posts`
- Enforced by status CHECK + triggers (pgTAP-tested). Slug immutable after create (update path edits content/meta/status, not slug).

### W5 — SEO/render pipeline
`Publish / unpublish / archive → /blog index + article render from Supabase (published, !hold, !archived) → dynamic sitemap (published only) → RSS feed (published only) → IndexNow ping (optional)`
- Sitemap + feed are dynamic routes (never stale vs the deploy cycle). `robots.txt` gains a line for `/blog/sitemap.xml` (discovery).
- **Cache discipline:** article/index responses carry `Cache-Control: public, max-age=300` with **`no-cache` (revalidate) when `updated_at` is within the last 30 minutes** (recently-edited posts) so E2E-5 freshness holds and edited content propagates promptly.

### W6 — Analytics pipeline
`Page load → consent.js (existing) → if granted: posthog pageview (auto) + share_click (on share) + article_read (scroll-depth ≥80% — deterministic trigger; read-time path is unit-tested, not E2E)`
- Consent decline → zero events, zero cookies (existing contract). Analytics never blocks render.

---

## 3. Prototype

**Public pages (new visual surface):** live HTML prototype → `docs/prototypes/2026-08-27-tortoise-blog/prototype.html` (dark slate/cyan tortoise design; index card grid + article editorial layout + share bar + mobile bottom bar + **empty state** view). See file.

**Admin (ported, not prototyped):** ElDato's `GuideEditor`/`GuidesList`/toolbar/image-node UI is ported as-is (owner-validated as polished); no new prototype needed. Review queue + audit views are simple list/table surfaces on the same design tokens.

**States covered in prototype:** index (posts present + **empty state**), article (cover, sections, inline image, share bar), mobile narrow viewport.

**UX decisions (recorded; provenance: epic-plan §3 gate):**
| # | Decision | Choice |
|---|---|---|
| 1A | Blog index layout | Card grid with cover images (Supabase/Vercel pattern) |
| 2A | Article layout | Cover top, centered narrow text column (editorial) |
| 3A | Share placement | Share bar under title + fixed bottom bar on mobile |

**Accessibility (plan-level checks, per scope rating low):** semantic heading structure (h1→h2→h3) on public pages; alt text on all images (cover + inline); WCAG AA contrast on dark background; ported editor keeps keyboard/focus behavior (TipTap defaults) — low-weight checklist item in the E2E suite (#1800).

---

## 4. Data Model

### blog_posts
```sql
CREATE TABLE public.blog_posts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' AND char_length(slug) <= 100),
  title text NOT NULL CHECK (char_length(title) <= 200),
  body text NOT NULL DEFAULT '',                -- markdown (canonical)
  excerpt text CHECK (char_length(excerpt) <= 300),
  cover_image_url text,
  tags text[] NOT NULL DEFAULT '{}',
  author text NOT NULL DEFAULT 'Tortoise team',
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  meta_title text,
  meta_description text,
  published_at timestamptz,
  published_by text,                            -- agent name or user id (audit)
  created_by text,                              -- agent_name or user id; scopes agent edit rights (PATCH allowed only when created_by = calling agent)
  reviewed_by text,
  reviewed_at timestamptz,
  review_note text,                             -- change-request note from owner (W2)
  hold_for_review boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
-- Publish guard (trigger, pgTAP-tested): status='published' requires published_at+published_by set.
-- Unpublish (trigger): status→draft clears reviewed_by/reviewed_at; clears review_note ONLY when the
--   new row doesn't carry one (NEW.review_note IS NULL) — so request-changes' note survives the
--   published→draft transition, while a plain unpublish (no note supplied) wipes review state.
-- Archive (trigger): status='archived' is terminal — rejects any transition out.
-- RLS:
--   anon SELECT WHERE status='published' AND hold_for_review=false
--   authenticated is_admin() SELECT ALL rows (admin SPA reads ride the owner's PKCE session)
--   service_role ALL (Function writes; is_admin() helper MUST be created in this migration — none exists today)
-- Indexes: idx_blog_posts_published (status, published_at DESC) WHERE status='published'; GIN(tags)
```

### blog_agent_keys
```sql
CREATE TABLE public.blog_agent_keys (
  agent_name text PRIMARY KEY,
  key_hash text NOT NULL,                       -- sha256 of key, never plaintext
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
-- RLS: no anon/authenticated access; service_role only (Function checks hash server-side)
-- Lifecycle: key issuance = seed/ops script (INSERT sha256 hash, print plaintext once); rotation = INSERT new key,
-- revoke = active=false. Admin UI for keys is OUT of scope (deferred).
```

### blog-images bucket
Public-read bucket (CDN); INSERT/DELETE via service_role + admin session; 5MB cap + jpeg/png/webp MIME enforced in Function + admin upload path; **object-key sanitization: basename only, strip `..`, `/`, backslash from filenames** (path-traversal guard, unit-tested).

### Migration
Single migration `supabase/migrations/0017_blog_cms.sql` — **next free sequential after 0016_oauth.sql (0011 is taken by `0011_teams_name_unique.sql`)**; timestamp-prefixed names also exist in the repo but the zero-padded sequential series is the primary convention. Includes tables, triggers, RLS, `is_admin()` helper, bucket + policies. Applied via existing `supabase-deploy.yml`.

---

## 5. Architecture

### Components
```
Cloudflare Pages (project premise-labs)                Supabase (existing project)
├─ website/functions/_middleware.ts        (add /blog + /blog/ prefix rule)   ├─ blog_posts, blog_agent_keys
├─ website/functions/blog/[[path]].ts      (SSR render: index+article;        ├─ blog-images bucket
│   ASSETS fallback for non-render paths)  └─ auth (PKCE; is_admin() gate)
├─ website/functions/blog/api/posts.ts     (agent API: POST create + PATCH own posts)
├─ website/functions/blog/sitemap.ts + feed.ts
├─ website/functions/admin/[[path]].ts     (admin gate: JWT-verify Supabase session,
│                                            serve admin SPA shell; 401 → /auth)
├─ website/apps/blog-admin/                (React SPA, ElDato editor port)
└─ website/blog/ static assets (favicons, og-image — served via ASSETS fallback)
```
- **Render Function (`blog/[[path]].ts`):** markdown→HTML (`marked` + DOMPurify sanitize, protocol allowlist per §6 markdown contract), full `<head>` injection, `Cache-Control` per §2 W5. **Catch-all must fall through to `context.env.ASSETS.fetch()` for non-post paths** (`/blog/og-image.png`, favicons) — Pages runs Functions ahead of static assets; without the fallback the catch-all swallows `/blog/*` static files.
- **Agent API (`blog/api/posts.ts`):** validates `X-Agent-Key` (sha256 vs `blog_agent_keys`), zod-validates; `POST` inserts (default `status='draft'` → review queue; `'published'` when explicitly instructed); `PATCH /:slug` updates posts where `created_by = agent_name` (403 otherwise); writes via service-role key (Pages Function secret). Rate limits 120/min + 2,000/day per key.
- **Admin gate (`admin/[[path]].ts`):** verifies the Supabase PKCE session JWT (HS256, JWT secret env) + `is_admin()` role; serves the SPA shell; unauthenticated/non-owner → 401/redirect `/auth`. **SPA fallback is scoped to `/admin/*` only** (via this Function or `_redirects` `/admin/* /admin/index.html 200`) — **never** project-wide `single-page-application` fallback, which would turn `/blog/:slug` 404s into index.html (breaks E2E-2/4/13 + the no-soft-404 render contract).
- **Analytics:** `consent.js` + PostHog snippet in SSR `<head>` (same as product.html); client-side events only.
- **Deploy:** extend `deploy-pages.yml` to build `apps/blog-admin` + deploy; supabase-deploy applies migration. Post-deploy E2E (blog pages + legal verify unchanged).

### Failure modes
- Supabase unreachable → render returns 503 + `Cache-Control: no-store` (never stale HTML); agent API returns retryable 503.
- PostHog/consent blocked → analytics never blocks render (async, errors swallowed).
- Slug race → UNIQUE constraint + 409 → agent retries with suffix.
- Markdown XSS → DOMPurify allowlist + **https-only protocol restriction for href/src** + E2E-14 assertion.
- Middleware false positives → prefix matches `/blog` and `/blog/` only (`pathname === '/blog' || pathname.startsWith('/blog/')`).
- Static-asset shadowing → ASSETS fallback in the catch-all + E2E assertion (og-image 200).
- Admin session expiry → 401 → `/auth` (same flow as dashboard).

---

## 6. Interfaces

### Agent API — implicit v1 (bump to `/blog/api/v2/…` on breaking change)
```ts
// POST /blog/api/posts — CREATE (default → review queue; direct-publish when explicitly instructed)
{ title: string (1..200), body: string (markdown, 1..100_000 chars at publish),
  slug?: string, excerpt?: string, cover_image_url?: string, tags?: string[] (≤10),
  meta_title?: string, meta_description?: string, author?: string,
  hold_for_review?: boolean, status?: 'draft' | 'published' }   // default 'draft' (review queue)
// Headers: X-Agent-Key: <per-agent key>
// 201: { id, slug, url }

// PATCH /blog/api/posts/:slug — UPDATE own posts (created_by = calling agent_name)
{ title?, body?, excerpt?, cover_image_url?, tags?, meta_title?, meta_description?,
  author?, hold_for_review?, status?: 'draft' | 'published' }   // slug immutable; partial update
// 200: { id, slug, url } · 403 if post not created by this agent · 404 unknown slug

// Errors (both):
//   400 {field: message}   — schema violation OR empty body at status=published
//   401 invalid/missing key · 404 inactive agent
//   403 not your post (PATCH only) · 409 slug already exists (POST only — slug theft guard)
//   422 malformed markdown (unbalanced code fence, or body > 100KB)
//   429 rate limit (120 req/min, 2,000 req/day per key) · 503 upstream
```
**Draft/queue semantics:** the default path is `status='draft'` → owner publishes from the queue. An agent sets `'published'` ONLY when the owner explicitly asked for direct publishing (audited via `published_by`/`published_at`). Rewrites are PATCH updates on the agent's own posts — never blocked by the create-slug 409.

### Render contract (`GET /blog`, `/blog/:slug`)
- 200 SSR HTML: full head (title=meta_title ?? `${title} | Tortoise`, meta description, canonical, OG, Twitter, JSON-LD BlogPosting + BreadcrumbList), semantic article body.
- 404 for draft/hold/archived/unknown (no soft-404 — ensured by scoped admin SPA fallback, §5). 301 to tortoise host on company host (`/blog` prefix rule incl. trailing slash).

### Sitemap (`/blog/sitemap.xml`) + RSS (`/blog/feed.xml`)
- Published-only, absolute canonical URLs, newest-first. **Discovery: `robots.txt` gains `/blog/sitemap.xml`** (cross-submission stays). Feed: RSS 2.0 with per-item title/link/description(excerpt)/pubDate.

### Share URLs
`https://tortoise.premiselabs.co/blog/<slug>?utm_source=<network>&utm_medium=share`
Networks: twitter (intent/tweet), linkedin (sharing), facebook (sharer.php), whatsapp (wa.me?text), plus copy-link + native share (no UTM on native/copy).

### PostHog events
`$pageview` (auto) · `share_click {network, post_slug}` · `article_read {post_slug, depth_pct}` — consent-gated. Test observation: **Playwright network interception of the PostHog ingest endpoint** (or PostHog API query against the test project).

### Markdown contract
Canonical storage = markdown. Render: `marked` → sanitize (DOMPurify allowlist: p, h2-4, ul/ol/li, a, img, blockquote, code/pre, strong/em, table; **href/src https:// or http:// only; external links `target=_blank` + `rel=noopener`; `javascript:`/`data:` stripped**). Admin: TipTap markdown import/export (`tiptap-markdown`, version-pinned) — **roundtrip integrity is a unit-tested invariant** (import(export(md)) ≡ md for the supported subset; E2E-5 asserts the DB body stays clean markdown after an edit cycle).

---

## 7. Detailed E2E Tests

**Shared fixtures (all tests):**
- Agent key K1 seeded in `blog_agent_keys` (sha256 of known plaintext); K2 inactive for 404 case.
- Owner test user seeded (Supabase auth) + `is_admin()` true; session provisioned per test (PKCE login against test project or injected session token).
- **Isolation:** per-run unique slug suffix (or teardown deleting created rows + resetting K1) so the suite is idempotent.
- PostHog observation: Playwright request interception on ingest endpoint; consent granted via the existing flow.

| E2E | Setup | Assertions |
|---|---|---|
| E2E-1 agent publish (two modes) | key K1 | **Default mode:** POST without status (or status=draft) → 201; `/blog/:slug` → **404 (in queue, not public)**; post appears in the admin review queue. **Direct mode:** POST status=published → 201; public 200 w/ rendered HTML + title/meta/JSON-LD/canonical; index newest-first; `/blog?tag=X` filters; sitemap includes URL; **og-image URL 200 (ASSETS fallback)**; row has published_by=K1 + published_at |
| E2E-2 hold-review | payload hold_for_review=true (either mode) | **While held:** 404 on /blog/:slug AND absent from /blog index, /blog/sitemap.xml, /blog/feed.xml. **After admin clears:** 200 + present in index/sitemap/feed |
| E2E-3 review queue | **4 posts: 1 agent-draft, 1 unreviewed-published, 1 held, 1 reviewed** | queue shows the agent-draft, the unreviewed-published, and the held item (reviewed excluded); mark the unreviewed one reviewed → reviewed_by/reviewed_at set; audit view lists published_by + ts |
| E2E-4 unpublish | published post | admin unpublish → public 404; absent from index, sitemap, **and feed**; republish (admin) → public again AND re-enters queue (reviewed_at NULL) |
| E2E-5 edit + roundtrip | published post, TipTap edit | public raw SSR contains edited string, pre-edit string gone (cache: no-cache on edited posts); **DB body remains clean markdown** (no HTML-escaped markup) after edit cycle |
| E2E-6 images | admin uploads cover + inline | Storage objects exist w/ sanitized keys; CDN URLs 200; rendered in post; **`../`/slash-in-filename upload rejected** |
| E2E-7 SSR/host | curl, no JS | /blog + /blog/:slug raw HTML contains content + meta; company host `/blog`, `/blog/` (trailing slash), `/blog/<slug>` → 301 to tortoise host; **`/blogpost`, `/blog-extra` NOT redirected** (prefix false-positive guard) |
| E2E-8 API rejects | no key / bad key / inactive key / bad schema / empty body at publish / dup slug (POST) / **PATCH other agent's post** | 401 / 401 / 404 / 400 / 400 / 409 / **403**; no rows created. **429 (rate limit) NOT in this E2E** — non-deterministic across isolates; unit-tested with lowered thresholds (5 req/10s) in the agent-API child issue |
| E2E-8b agent rewrite loop | K1 creates post (default draft); owner request-changes w/ note; K1 PATCHes content + republishes (explicit); K2 (another agent) attempts PATCH on same post | revised content public after republish (no 409 on update); **K2 PATCH → 403**; review_note preserved through the draft transition |
| E2E-9 share | published post; click each network + copy | correct target URL w/ utm_source=<network>&utm_medium=share; clipboard plain URL; share_click event intercepted |
| E2E-10 PostHog | consent granted + declined | granted: pageview on /blog + /blog/:slug; **article_read via simulated scroll to ≥80%** (deterministic; read-time path unit-tested); declined: zero events, zero cookies; consent.js in SSR head |
| E2E-11 RSS | published+draft+archived rows | valid XML; published only; newest-first; absolute URLs; **per-item title, pubDate, description(excerpt) present** |
| E2E-12 admin gate | anonymous + non-owner | both blocked (401/redirect /auth); no content/queue/audit in response; owner session renders queue + editor + audit views |
| E2E-13 archive | published post | admin archive → public 404; gone from index/sitemap/feed; **re-archive attempt and any transition out of archived rejected** |
| E2E-14 XSS sanitize | agent publishes body with `<script>`, `onerror=`, `javascript:` URL, `data:` img src | rendered /blog/:slug raw HTML has NO script tag, no event-handler attributes, no javascript:/data: href/src |
| E2E-15 503 no-store | Function Supabase env pointed at unreachable endpoint (test stub) | render returns 503 + `Cache-Control: no-store`; agent API returns 503 (retryable) |

**Edge cases (concrete):** empty blog → index 200 + empty-state element rendered · tag with no posts → 200 + empty filter note · malformed markdown (unbalanced code fence) → 422 with field error · image >5MB → 413 · concurrent same-slug creates (N parallel) → exactly one 201, rest 409 · non-ASCII slug → 400 · agent PATCH on archived post → 409/400 (terminal)..

---

## 8. Coherence Review + Risk Analysis

### Cross-substep consistency
- Create-slug 409 semantics consistent: POST insert UNIQUE conflict → 409 (W1 · §4 · §6 · E2E-8); updates go through PATCH, never blocked by 409 (E2E-8b). ✔
- Agent edit scope consistent: W1/W4 (PATCH own posts, created_by) · §4 (created_by comment) · §6 (PATCH contract, 403) · E2E-8/8b. ✔
- Queue-default + direct-publish consistent: W1/W2 (default draft; published on explicit instruction) · §6 (status default draft) · E2E-1 two modes. ✔
- Review state consistent: W2 (clear on unpublish, re-enter queue) · §4 triggers · E2E-4. ✔
- hold_for_review excludes from ALL public surfaces: W3 · §4 RLS · §5 render · E2E-2. ✔
- Per-agent keys: W1 · §4 (blog_agent_keys + lifecycle) · §6 (header) · E2E-1/8. ✔
- Middleware prefix (incl. trailing slash, false-positive guard): §5 · §6 render · E2E-7. ✔
- Admin gate + scoped SPA fallback (no soft-404): §5 · E2E-12 · render contract. ✔
- ASSETS fallback for static under /blog: §5 · E2E-1. ✔
- ElDato port + roundtrip integrity: J3 · §3 · §5 · §6 markdown · E2E-5. ✔
- XSS: §6 sanitize contract · E2E-14 · risk row. ✔

### Risks & mitigations
| Risk | P | I | Mitigation |
|---|---|---|---|
| Agent-published content quality dips | M | M | Post-publish review queue, one-click unpublish, hold_for_review self-gate, review_note feedback |
| Markdown XSS from agent content | L | H | DOMPurify allowlist + https-only href/src + E2E-14 |
| Markdown↔TipTap roundtrip corruption | M | M | tiptap-markdown version pin + roundtrip unit invariant + E2E-5 DB-body assertion |
| ElDato port friction (React 18→19, TipTap 3 same) | M | M | TipTap 3.x matches; radix/lucide/sonner support React 19; pin deps; port shadcn/ui primitives |
| Admin SPA client-rendered → not crawlable | L | L | Auth-gated, noindex, excluded from sitemaps (matches welcome/invite pattern) |
| Middleware prefix edge cases | L | M | Exact `/blog` + `/blog/` prefix only; E2E-7 false-positive cases |
| Static assets shadowed by catch-all | L | M | ASSETS fallback branch + E2E-1 og-image assertion |
| Agent key compromise | L | M | sha256 server-side, per-agent rotation (new key INSERT), active=false revocation, rate limit |
| Supabase outage during render | L | M | 503 + no-store (E2E-15); static site pages unaffected |
| Slug squatting/duplicate race | L | M | UNIQUE + 409 on create (slug theft guard); PATCH updates never blocked by it |
| Cross-agent clobbering | L | M | PATCH scoped to created_by = calling agent (403 otherwise); owner edits via admin |
| Rate limit blocking legitimate rewrites | L | M | 120 req/min + 2,000 req/day per key; rewrites are PATCH updates (cheap); unit-tested at lowered thresholds |

### Improvement opportunities (deferred, not dropped)
- IndexNow instant indexing (one POST on publish — include if trivial in the agent API)
- TOC sidebar for long posts (add when posts exceed ~1500 words)
- MCP publish tool (thin proxy) — listed out-of-scope
- OG image auto-generation per post (manual cover image in v1)
- Admin UI for agent-key management (currently ops/seed script)

---
**Human gate #2:** plan ready for review. Prototype: `docs/prototypes/2026-08-27-tortoise-blog/prototype.html`. Decomposition (child issues) proceeds after approval.
