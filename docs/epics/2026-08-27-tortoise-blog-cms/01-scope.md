---
title: "Tortoise Blog + CMS — Epic Scope"
type: engineering
domain: growth
doc_status: draft
created: 2026-08-27
ownedBy: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: tortoise-website
epic: tortoise-blog-cms
---

# Epic Scope — Tortoise Blog + CMS

**Base:** `docs/research/2026-08-27-tortoise-blog-cms/research.md` · `docs/epics/2026-08-27-tortoise-blog-cms/00-align.md` (PROCEED)
**Locked decisions:** repo A (plumbing in tortoise repo `website/`), TipTap ElDato-style editor, per-agent keys, URL `/blog`.

---

## Scope Boundaries

### In Scope

1. **Supabase schema + storage** — `blog_posts` table (slug UNIQUE, title, body **markdown**, excerpt, cover_image_url, tags text[], author, status draft/published/archived, meta_title, meta_description, published_at, published_by, created_by, reviewed_by, reviewed_at, hold_for_review) + `blog_agent_keys` table (agent_name UNIQUE, key_hash — per-agent credentials, never plaintext) + `blog-images` storage bucket + RLS (anon reads published only; writes via service-role Function + admin session) — as a repo migration in `supabase/migrations/`. **Image privacy tradeoff (documented):** the bucket is public-read (CDN), so `hold_for_review` hides the *page*, not the images; if strict image privacy is ever needed, revisit with a private bucket + signed URLs — not v1.
2. **Public blog pages** — `/blog` (index, latest + tag list) and `/blog/:slug` (article) rendered **server-side** via Cloudflare Pages Functions (edge SSR): markdown→HTML, full SEO head (title, meta description, OG, Twitter, JSON-LD BlogPosting + BreadcrumbList, canonical), site design language (dark slate/cyan).
3. **Agent publish API** — **locked: a Cloudflare Pages Function** (`website/functions/blog/api/…`) using the Supabase service-role key (stored as a Pages Function secret, server-side only). Rationale: keeps marketing-content endpoints off the product API surface (`api.premiselabs.co` is the Tortoise product API with its own auth/billing), and the site is already on Pages. **Per-agent credentials** validated against `blog_agent_keys` (key hash check in the Function). **Publish flow (owner refinement, 2026-08-27): default `status='draft'` → review queue; `status='published'` allowed when the owner explicitly asks for direct publishing (audited `published_by`); agents may `PATCH` their own posts (created_by scoped, 403 otherwise); generous rate limits (120 req/min, 2,000 req/day per key).** `hold_for_review` support; schema validation; reject unauthenticated/anon writes. A thin proxy on the hosted API (e.g., for MCP-based publishing) is explicitly deferred.
4. **Blog admin SPA** — React + Vite app (`website/apps/blog-admin`) with Tailwind 3 + shadcn/ui + TipTap. **Ports the ElDato editor code** (owner decision 2026-08-27): `GuideEditor`, `GuidesList`, `GuideEditorToolbar`, `ImageNode`, `BlockDragHandle` + the shadcn/ui primitives they use; deal extensions stripped; TipTap **markdown import/export** added so agent markdown flows into the editor. Views: review queue (unreviewed agent-published + hold_for_review), editor, actions (mark reviewed, edit, unpublish → draft, archive), audit view. Gated by Supabase auth (owner/admin) — **route returns 401/redirect for unauthenticated and non-owner sessions, never content**.
5. **SEO wiring** — `/blog*` added to the middleware **with prefix matching** (the existing `TORTOISE_ONLY` is an exact-match Set; `/blog` needs a prefix rule so `/blog/<slug>` on premiselabs.co 301s to the tortoise host — otherwise the company host serves duplicate article pages, recreating the 2026-08-17 "Alternate page" problem); dynamic sitemap coverage for published posts (Pages Function route, recommended option (a) from research §4.2); robots.txt cross-submission unchanged; canonical tags; optional IndexNow on publish.
6. **Social share buttons** — per-article share bar (ElDato pattern, `DealPage` share block + `CopyShareButton`): X/Twitter + Facebook intent URLs, LinkedIn share URL, WhatsApp (wa.me), copy-link, native share sheet on mobile — each share URL carries `utm_source=<network>&utm_medium=share` for attribution; share clicks captured as PostHog events.
7. **PostHog analytics** — blog pages load the existing consent-gated `website/consent.js` + PostHog (project 548850, US Cloud) exactly like the funnel pages: automatic pageviews on /blog and /blog/:slug, `share_click` events, and an `article_read` signal (scroll-depth or read-time) — all consent-gated; no new consent flow (reuse the existing one).
8. **RSS/Atom feed** — `/blog/feed.xml` (or `/feed.xml`) rendered server-side from published posts (same dynamic route as the sitemap; ~30 lines, zero new infra). Recommended include: feeds are used by newsletter/aggregator tools and increasingly by AI agents to watch sites.
9. **Deploy pipeline** — extend `deploy-pages.yml` (blog-admin build + deploy); `supabase-deploy.yml` applies the new migration; E2E/verify step for blog pages.
10. **Docs** — website_architecture.md updated (new surface); this epic's files.

### Out of Scope

- **Deal embeds / carousels / columns** (ElDato custom TipTap nodes) — N/A for Tortoise. — *deferred: never*
- **Multi-language / hreflang** — v1 is English only. — *deferred: future epic if needed*
- **Comments, likes, newsletter signup** — *deferred: future epic*
- **Scheduled publishing / content calendar** — agents publish now or hold. — *deferred: future*
- **Content taxonomy beyond tags (categories tree)** — *deferred: future*
- **Topic-research / content-pipeline automation agents** (auto topic selection, SEO keyword research) — *deferred: separate epic after v1*
- **Tortoise MCP publish tool** (agents publish from any agent harness via MCP instead of the REST Function) — *deferred: future (thin proxy on the agent API)*
- **Migrating existing static pages (docs, legal) into the CMS** — out; blog is additive.
- **Any change to the Tortoise engine, self-host image, or hosted API** — verified zero-impact; out.

### Boundary Rationale

The cut principle: **smallest system that satisfies agent-write → human-review → SEO-published on the existing stack.** Anything not needed for that loop (social, scheduling, taxonomy, i18n) is deferred, not built. ElDato's custom editor extensions (deal embeds/columns) are the reason its blog was "big"; Tortoise drops them. Content lives in Supabase (private), code in the repo (public) — repo privacy is not a content constraint.

---

## Customer Value Map

| Scoped Capability | User-Visible Value |
|-------------------|--------------------|
| Agent publish API | An agent can write and publish a blog post in minutes — no code, no human dependency |
| Public blog pages (SSR) | Visitors and AI search engines get fast, complete, crawlable article HTML at /blog |
| Review queue | The owner sees everything agents published, in one place, with preview |
| Post-publish review + unpublish | The owner can fix, request changes, or instantly take down any post |
| Hold-for-review flag | An agent can mark a risky post private until the owner clears it |
| TipTap editor w/ markdown import | The owner can refine agent drafts or write posts in a Google-Docs-like editor |
| Image upload (cover + inline) | Posts can carry a cover image and inline images, served from CDN |
| SEO fields per post | Every post gets meta title/description/OG/JSON-LD so it ranks and shares correctly |
| Social share buttons | Readers can share a post to X, LinkedIn, Facebook, WhatsApp, or copy the link — with tracked attribution |
| PostHog analytics | The owner sees real traffic/engagement data on blog posts (consent-gated, same as the rest of the site) |
| RSS feed | Subscribers/newsletter tools/AI agents get notified automatically when a new post publishes |
| Audit trail | The owner can see which agent published what, and when |

---

## Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | **medium** | TipTap editor + review queue + admin navigation; but single reviewer, no auth flows, no multi-user workflows |
| Architecture | **medium** | New Pages Functions SSR surface + agent API + admin SPA; every pattern (Functions, React app, Supabase, deploy) already exists in the repo |
| Ontology | **low** | One new content table in Supabase; no engine/graph ontology changes |
| Accessibility | **low** | Public pages are simple semantic server-rendered HTML; admin is internal-facing. Note: SSR is *crawlable*, not automatically *accessible* — heading structure, alt text, contrast, and editor keyboard/focus behavior are still plan-level checks (rating stays low: small surface, no complex widgets beyond the ported editor) |

### Axis Research Notes

> **Findings date:** 2026-08-27

Both `medium+` axes (UX, Architecture) are **justified skips** — the boundary questions are covered at sufficient granularity in the epic brief:
- **UX (editor depth):** research §3.4 (TipTap vs markdown trade-offs, ElDato precedent, markdown-import pattern) + §4.4 (admin UI options, review-queue throughput anti-patterns). Locked decision: TipTap with markdown import/export.
- **Architecture (render + API + repo):** research §3.2 (SSR required; Pages Functions fit), §3.3 (Supabase-as-CMS), §4.2 (public render), §4.3 (agent API + safety rails), §4.6 (repo placement). Locked decision: repo A, Pages Functions SSR, service-role Function for agent writes.
- No external queries fired — the brief already resolves every boundary question this scope needed.

---

## High-Level E2E Test Cases

> Written BEFORE user journeys — behavioral, not presentational. These anchor detailed E2E in plan + capstone verification.

### E2E-1: Agent publishes a post (two modes)
**Given:** a valid per-agent credential and a markdown post with meta (title, slug, excerpt, meta_title, meta_description, cover image uploaded)
**When (default/queue mode):** the agent calls the publish API without `status` (defaults to draft)
**Then:** the post is NOT public (`/blog/<slug>` → 404) and appears in the admin review queue
**And:** after the owner publishes it from the queue, E2E-1 direct-mode outcomes apply
**When (direct mode):** the agent calls with `status=published` (owner explicitly asked)
**Then:** the post is served at `tortoise.premiselabs.co/blog/<slug>` with rendered HTML (markdown→HTML), correct `<title>`/meta description/OG tags/JSON-LD BlogPosting, canonical URL
**And:** the post appears on `/blog` index (newest first)
**And:** tag-filtered listing (`/blog?tag=…`) shows only posts with that tag
**And:** the post URL appears in the sitemap
**And:** `blog_posts` row has `published_by=<agent>`, `published_at` set

### E2E-2: Hold-for-review keeps a post private
**Given:** an agent publishes with `hold_for_review=true`
**When:** a public visitor requests `/blog/<slug>`
**Then:** the post returns 404 (not public)
**And:** after the owner clears the flag in the admin, the post becomes public (E2E-1 outcomes apply)

### E2E-3: Review queue + audit
**Given:** 4 posts exist — 1 agent-draft (default queue), 1 unreviewed direct-published, 1 held, 1 reviewed
**When:** the owner opens the admin review queue
**Then:** the queue shows the agent-draft, the unreviewed-published post, and the held post; the reviewed post does not appear
**And:** the owner can mark an item reviewed → `reviewed_by`/`reviewed_at` set
**And:** the audit view lists each post with `published_by` agent identity and timestamp

### E2E-4: Unpublish takes a post down
**Given:** a published post exists and is indexed
**When:** the owner unpublishes it in the admin
**Then:** `/blog/<slug>` returns 404 for public visitors
**And:** the post disappears from `/blog` index and the sitemap

### E2E-5: Owner edits a post (TipTap) → change goes live
**Given:** a published post
**When:** the owner edits body/images in the editor and saves
**Then:** the public page reflects the edited content after save

### E2E-6: Image upload (cover + inline)
**Given:** an authenticated admin session
**When:** the owner uploads a cover image and an inline image in the editor
**Then:** both images upload to `blog-images` Storage, return CDN URLs, render in the post (cover at top, inline in body), and serve publicly

### E2E-7: SSR + host isolation
**Given:** a published post
**When:** an unauthenticated crawler (curl, no JS) requests `/blog` and `/blog/<slug>` on tortoise.premiselabs.co
**Then:** both return complete server-rendered HTML with content and meta in the raw response
**And:** on premiselabs.co (company host), `/blog` **and `/blog/<slug>` (prefix rule, not just exact match)** are 301'd to the tortoise host (per the middleware contract change in scope item 5)

### E2E-8: Agent API rejects bad actors
**Given:** the publish API endpoint
**When:** (a) an unauthenticated write, (b) a write with an invalid/unknown agent credential, (c) a write with invalid schema (bad slug, missing body), (d) a PATCH by agent B on agent A's post is attempted
**Then:** (a)-(c) rejected (401/401/400), no row created; (d) rejected 403; no anonymous write path exists

### E2E-8b: Agent rewrite loop
**Given:** agent A created a post (default draft); the owner requested changes with a note
**When:** agent A PATCHes content and republishes (explicit status=published); agent B attempts a PATCH on the same post
**Then:** the revised content is public (no 409 on update); the review_note survived the draft transition; agent B's PATCH → 403

### E2E-9: Social share buttons work + attribute + track
**Given:** a published post
**When:** a visitor clicks each share action (X, LinkedIn, Facebook, WhatsApp, copy-link)
**Then:** each opens the correct share target with the post URL carrying `utm_source=<network>&utm_medium=share`
**And:** copy-link writes the plain post URL to clipboard
**And:** each click emits a `share_click` event to PostHog (when consent granted)

### E2E-10: PostHog captures blog traffic (consent-gated)
**Given:** consent granted (existing flow)
**When:** a visitor loads `/blog` and `/blog/<slug>` and reads past a scroll-depth/read-time threshold
**Then:** PostHog records pageviews for both pages
**And:** an `article_read` signal event fires for the read post
**And:** with consent declined, no events fire and no cookies set (existing consent contract holds)
**And:** consent.js is present on blog pages (same as funnel pages)

### E2E-11: RSS feed lists published posts
**Given:** published + draft + archived posts exist
**When:** a client requests `/blog/feed.xml`
**Then:** the feed returns valid XML containing only published posts (newest first, full URL + title + date + excerpt)
**And:** drafts/archived posts never appear in the feed

### E2E-12: Admin auth gate blocks non-owners
**Given:** an unauthenticated visitor and an authenticated non-owner user
**When:** either requests the admin route
**Then:** each is blocked (401/redirect to `/auth`), no post content, review queue, or audit data is returned
**And:** the owner session sees the full admin surface

### E2E-13: Archive retires a post everywhere
**Given:** a published post (public, indexed, in feed)
**When:** the owner archives it in the admin
**Then:** `/blog/<slug>` returns 404 publicly; the post leaves `/blog` index, sitemap, and RSS feed

---

## Epic Scope Ready for Review

**Scope:** 10 in-scope capabilities (schema/storage, public pages, agent API [Pages Function; queue-default + direct-publish + agent edit], admin SPA [ports ElDato editor], SEO wiring [prefix-matching middleware], social share, PostHog analytics, RSS feed, deploy, docs) · 9 out-of-scope deferrals (deal embeds, i18n, social/comments/engagement, scheduling, taxonomy, content automation, MCP publish tool, static-page migration, engine changes)
**Customer value map:** 12 capabilities mapped
**E2E test cases:** 14 drafted (agent publish two-mode, hold-review, review queue, unpublish, edit, images, SSR/host isolation [prefix rule], API rejection + rewrite loop, share buttons, PostHog + article_read, RSS, admin auth gate, archive)
**Complexity:** UX medium · Architecture medium · Ontology low · Accessibility low

Review the scope boundaries, customer value map, and E2E test cases. Reply **"proceed"** to continue to detailed planning, or give feedback.
