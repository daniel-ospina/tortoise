---
title: "Tortoise Blog + CMS — Research"
type: synthesis
domain: growth
doc_status: draft
created: 2026-08-27
ownedBy: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: tortoise-website
---

# Research Summary: Tortoise Blog + CMS (tortoise.premiselabs.co)

**Date:** 2026-08-27
**Status:** Decisions locked (owner, 2026-08-27) — see §6.1. Ready as epic base.
**Trigger:** User request — "check how ElDato built the in-app blog/guides and do the same for
tortoise.premiselabs.co; figure out repo architecture; is React needed; agents publish, humans
review in CMS; keep self-host download simple."

---

## 1. Reframed Problem Statement

> Tortoise is trying to ship a blog on tortoise.premiselabs.co (agent-writes → human-reviews →
> publish, with SEO done properly) but the assumed path ("fork ElDato's React blog") is uncertain,
> which results in risk of (a) destabilizing the critical tortoise product repo, (b) coupling a
> marketing blog into the self-hostable product download, and (c) building more CMS than is needed.

**Alternative framings considered:**
- HMW let agents publish reviewed content to *any* marketing surface, not just this blog?
- HMW give Tortoise a content publishing workflow *without* coupling it to the Tortoise product?
- HMW achieve agent-write + human-review + SEO with the *smallest* possible system?

**Assumption map:**
| Assumption | Status |
|---|---|
| Blog must live inside the webapp (same domain) | [unverified] — blog on same domain is best for SEO (domain authority), but a subdomain is possible |
| Blog must be React | [unverified] — public rendering does NOT need React; admin editor can be React |
| Needs a visual (WYSIWYG) editor | [unverified] — markdown + preview may suffice; TipTap is optional depth |
| Self-hosted Tortoise should ship the blog too | [**refuted**] — self-host Dockerfile copies only `tortoise/`; website never ships to self-host |
| Blog content should be in git | [**refuted**] — content belongs in Supabase (private, agent-writable); repo stays code-only |
| Agents must be human-gated before publishing (Scribe draft-only) | [**overridden by owner, 2026-08-27**] — owner decision: agents MAY publish directly; human review happens **after** publish, in the CMS |

---

## 2. What We Have Internally

### 2.1 ElDato blog/guides system (the reference implementation)

Full architecture recovered from the eldato repo (private):

| Layer | ElDato implementation |
|---|---|
| **Editor** | TipTap v3 (ProseMirror) React WYSIWYG: `src/components/guides/` — `GuideEditorToolbar`, `ImageNode`, `DealEmbedNode`, `DealCarouselNode`, `ColumnsExtension`, `BlockDragHandle` |
| **Admin UI** | `src/pages/admin/GuideEditor.tsx` (create/edit, auto-slug, image upload, draft/publish toggle) + `GuidesList.tsx` (CRUD list) |
| **Storage** | Supabase Postgres `guides` table: `slug` UNIQUE, `title`, `body` (TipTap HTML), `faqs` JSONB, `meta_title`, `meta_description`, `published` BOOL, `published_at`, `featured_image_url` |
| **Images** | Supabase Storage bucket `guide-images` (public read, admin write), path `{slug}/{filename}` |
| **Public render** | `/guias/:slug` — renders TipTap HTML, hydrates `<deal-embed>` custom elements, FAQ accordion |
| **SEO** | `SEOHead.tsx` (react-helmet-async): title, meta description, OG, Twitter, JSON-LD (BreadcrumbList), canonical, hreflang. Title pattern: `"{title} | El Dato"`. `FAQPageSchema`, sitemaps |
| **RLS** | Anyone reads published; `is_app_admin()` manages everything |
| **Agent path** | Content pipeline wrote **SQL migrations as drafts** (`content_*.sql` with "⚠️ REQUIRES HUMAN REVIEW before applying") — the human gate was applying the migration. Plus the admin editor for manual authoring |

**Key takeaway:** the deal-embed feature (ElDato's reason for the custom editor) is irrelevant to
Tortoise. Everything else (schema shape, SEO fields, storage bucket, publish/review flow) is
directly transferable and simpler.

### 2.2 Tortoise web presence (what exists today)

From `website/website_architecture.md` (2026-08-14) + live inspection:

| Host | Serves | Where |
|---|---|---|
| `premiselabs.co` | Company page | `website/index.html` (Cloudflare Pages project `premise-labs`) |
| `tortoise.premiselabs.co` | Product page (`product.html` at `/`), docs, auth, welcome, invite, legal | same Pages project, host-routed by `website/functions/_middleware.ts` |
| `app.premiselabs.co` | Dashboard (React+Vite SPA) | separate Pages project `tortoise-dashboard` |
| `api.premiselabs.co` | Hosted FastAPI | Fly.io |

Relevant existing infra:
- **SEO infra already in place:** `robots.txt` (cross-submission of 3 sitemaps), `sitemap-product.xml`, `sitemap-company.xml`, canonical tags on all indexable pages, `_redirects`, host-consolidation 301s, HSTS. Middleware has a `TORTOISE_ONLY` route set (blog routes would join it).
- **Supabase project managed in-repo:** `supabase/migrations/0001…0016+` with teams/memberships (`role`: owner/admin/member), api_keys, provisioning RPCs. No "site admin" concept yet — the blog admin gate needs a decision (owner/admin role vs allowlist).
- **Deploy:** `.github/workflows/deploy-pages.yml` deploys `website/**` changes to Pages on push to main touching `website/**`; `supabase-deploy.yml` applies migrations.
- **Analytics & consent (existing):** `website/consent.js` gates **PostHog** (project 548850, US Cloud) + GTM/GA4 on the funnel pages (product/signin/signup/welcome); consent flow = PostHog CMP pattern (opt-out default, opt-in on granted). **The blog reuses this exact setup** (scope #7, plan W6/§6): blog pages include consent.js + PostHog snippet server-side; events fire only on granted consent. No new consent flow.
- **Editor precedent in-stack:** the dashboard is already React + Vite (`website/apps/dashboard`).

### 2.3 Self-host isolation — VERIFIED, not a risk

- `Dockerfile.selfhost` (42 lines) copies **only** `tortoise/` (Python package) + requirements + pyproject. No website, no JS, no blog.
- `Dockerfile.hosted` likewise does not include the website.
- **Conclusion:** adding a blog to the website surface has *zero* effect on the self-host download or the hosted API image. The user's "don't ship a blog in the laptop download" concern is already structurally satisfied.

### 2.4 Repo visibility — important context

- `daniel-ospina/tortoise` is **PUBLIC** (`"private": false`).
- `daniel-ospina/eldato` is **private** (404 anonymous).
- Implication: any content stored in the tortoise repo (git files) is public. **Draft content must live in Supabase, not git.** Blog *code* in the repo is fine (it's public marketing-surface code, like the rest of `website/`).

### 2.5 Prior epistemic memory

Tortoise memory checkpoint: **unavailable** (no `TORTOISE_API_KEY` in env) — no prior claims retrieved.

---

## 3. External Findings (with confidence tiers)

### 3.1 The "Scribe pattern" — agents draft, humans publish **[HIGH — 5+ independent sources: Cosmic, KernelCMS, Abhishek Shankar CMS typology, Hygraph, Storyblok]**

The canonical agent-CMS workflow: **the agent never touches the publish state.** Agents create
content in `draft`, a human reviews and advances to `published`. Multiple sources converge on
hard rules:
- "Never publish from an automated step. Generation writes drafts. Only a person changes status to published." (Cosmic)
- KernelCMS: draft-only agent principal, `evalGate` (quality CI) + `requestReview` (human inbox) are the *only* advancement paths.
- Abhishek Shankar's typology: the **Scribe pattern** "works because it leaves the authority topology unchanged. The agent is, structurally, a very fast copywriter."
- Token/principal scoping: **read auto-approves, write requires confirmation, publish always requires explicit confirmation** — bake the boundary into the API/MCP config, not user discipline (Storyblok/Claude Code pattern).
- Anti-patterns: review UIs built for 20 drafts/day fail at 200/day; natural-language feedback fields don't work as agent-revision interfaces (structured fields do).

**→ For Tortoise (industry default):** a `status` state machine (draft → in_review → published/rejected) with an
API that agents can only ever use to create/update *drafts* is the load-bearing design. The
publish action is a human click (or an explicitly-gated endpoint).

**⛔ Owner override (2026-08-27):** the owner wants agents to be able to **publish directly** —
this repo's own agents (pi) are trusted actors operating under explicit instructions. The
Scribe rules are therefore relaxed to a **publish-with-post-hoc-review** model (maps to the
typology's *Scheduled Publisher / Workflow Participant* variants, with review moved after
publish). The safety property shifts from "agents cannot publish" to **everything an agent
publishes is (a) attributed, (b) reviewable, (c) instantly reversible, and (d) optionally
holdable**. The industry rules that remain non-negotiable: principal scoping (agents have their
own scoped credentials — never anonymous, never the admin UI's session), full audit of who
published what/when, and a one-click unpublish. An optional per-content "hold for review"
flag lets an agent self-gate content it is unsure about; the owner can also flip a global
review-gate toggle for sensitive content later — both are configuration, not architecture.

### 3.2 Public blog pages must be server-rendered (SSR/prerendered HTML) **[HIGH — Ranking Lens, Agentikas, Swazzy, Cloudflare docs, IGNAX]**

- Client-side React SPAs deliver an empty HTML shell; **AI crawlers (GPTBot, PerplexityBot, ClaudeBot) cannot execute JS at all**; Googlebot can but with days/weeks of render delay and limited render budget.
- "If the content needs to be discovered → SSR. If the content needs to be manipulated → CSR. Don't mix them." (Agentikas)
- Cloudflare Pages Functions (edge SSR) or build-time prerendering are both fine; static prerendered routes are sub-50ms TTFB; Workers SSR is ~free on the free tier.
- Hybrid pattern (Swazzy): static shell + Worker that owns only `/api/*`, `/media/*`, `/sitemap.xml`, `/feed*`, and permalink routes; injects per-post OG tags server-side.

**→ For Tortoise:** the public blog must be **server-rendered HTML**, which fits the existing
stack perfectly (Pages Functions already used for `_middleware.ts`). **Do not** make the public
blog a React SPA. React (if used at all) belongs in the *admin* surface only.

### 3.3 Supabase-as-CMS is a well-established pattern **[HIGH — RapidDev, Makerkit/Supamode, PawPress, dev.to, ElDato in-house]**

- posts table (slug, title, body, excerpt, cover_image_url, status draft/published, published_at, meta_title, meta_description) + Storage bucket for images + RLS (public read published, authenticated/admin write) + optional Edge Function REST API is a documented, repeated pattern.
- Content in Postgres = one infra bill, same auth as the rest of the app, SQL access, no vendor lock-in (pg_dump).
- Pitfalls to avoid: don't store base64 images in the DB (always Storage + CDN URL); lock INSERT to authenticated users; cap file size/MIME (5MB, jpeg/png/webp).

**→ For Tortoise:** reuse the existing Supabase project. ElDato's `guides` schema is a near-drop-in template minus deal-specific fields.

### 3.4 TipTap vs Markdown: both, in the right layers **[MEDIUM-HIGH — MakerStack, José Ortega, Ashby, Tiptap docs, dev.to]**

- TipTap is the de-facto custom-editor framework (GitLab, Substack, Ashby) — modular extensions, React-first, battle-tested ProseMirror core. But: "if you just need a simple text input for a blog post, Tiptap is overkill" (MakerStack); polish takes real dev time.
- **HTML is better than Markdown for *storage*** (Markdown is ambiguous in edge cases — tables, embedded HTML, footnotes), but **Markdown is what agents write naturally**, and TipTap has first-class markdown import/export; TipTap static renderer can render server-side.
- Precedent (dev.to, "I built my own blog publishing system instead of using a CMS"): markdown in Postgres + react-markdown render + a minimal private studio (draft/preview/publish) — deliberately skipped user accounts/comments/roles/scheduling/block editor.

**→ For Tortoise:** **store Markdown as the canonical content** (agents write markdown natively;
markdown renders to HTML server-side with `marked`/`markdown-it`).

**⛔ Owner override (2026-08-27): port the ElDato editor code, not just the pattern.** The owner
invested heavily in the ElDato editor UI/UX (toolbar, image handling, drag handles, shadcn/ui
primitives) and wants it reused. Compatibility verified: ElDato uses TipTap 3.19 (matches),
shadcn/ui + lucide-react + sonner + Tailwind 3 — all portable into a new blog-admin app.
Port: `GuideEditor.tsx`, `GuidesList.tsx`, `GuideEditorToolbar.tsx`, `ImageNode.tsx`,
`BlockDragHandle.tsx`, and the shadcn/ui primitives they depend on. Strip: `DealEmbedNode`,
`DealCarouselNode`, `DealSearchModal`, `DealCarouselModal`. Add: TipTap markdown
import/export (agent markdown flows into the editor and back out for storage).

### 3.5 "Don't build your own CMS" — the counterpoint, and why it's only partly applicable **[HIGH as general advice; applicability MEDIUM]**

- Strong general consensus (dev.to, Hygraph, ButterCMS, SitePoint): custom CMS builds become
  patchwork, costly to maintain, distract from core product. Most small teams should buy.
- **Why it's only partly applicable here:** (1) Tortoise already *has* the infra (Supabase
  project, auth, migrations pipeline, Cloudflare Pages) — the marginal build is a table +
  bucket + ~3 pages, not a CMS platform; (2) the requirement is *agent-publishable content*, and
  agents writing directly to your own Supabase REST API is the lowest-friction path; (3) ElDato
  already built the pattern in-house — this is a port, not an invention; (4) the user explicitly
  wants to avoid the cost/complexity of a CMS platform ("we shouldn't make the website so big").
- The dev.to precedent is the calibrated middle: build the minimal tool, then get out of the way.
- If a managed CMS were preferred anyway, the strongest fits would be **Sanity** (workflow states,
  MCP server, free tier) or **Payload** (self-hostable, Postgres) — but both add a second
  content store and a second bill to a stack that already has Supabase. ⚠️ single-source /
  unverified: Sanity/Payload feature claims are from model knowledge, not fetched docs — verify
  against vendor docs if this path is ever explored.

### 3.6 Blog-on-same-domain vs subdomain **[MEDIUM — standard SEO practice, corroborated by the tortoise site's own host-consolidation work]**

Same-domain paths (`/blog/…`) inherit domain authority and consolidate signals. The tortoise
site's 2026-08-17 SEO work already treats host consolidation as core (301s to canonical host).
A separate blog subdomain would repeat the "alternate page" problem they just fixed.

---

## 4. The Decision Space (for the epic)

### 4.1 Content storage — RECOMMEND: Supabase (existing project)

`blog_posts` table modeled on ElDato `guides` minus deal fields:

```
slug text UNIQUE, title text, body text (MARKDOWN canonical),
excerpt text, cover_image_url text,
status text CHECK IN ('draft','published','archived') DEFAULT 'draft',
meta_title text, meta_description text, tags text[],
author text, published_at timestamptz, updated_at timestamptz,
published_by text,            -- agent id/name or user id — audit: who published
created_by text, reviewed_by text, reviewed_at timestamptz,
hold_for_review boolean DEFAULT false   -- agent self-gate: stays private until human clears
```

(Status machine: `draft → published` (agent or human) and `published → draft` (unpublish,
human or agent) / `published → archived` (human). `hold_for_review=true` posts are never
served publicly regardless of status — the human clears the flag in the CMS, or the agent
sets it when unsure.)

Plus `blog-images` storage bucket (public read, gated write). RLS: anon reads only
`published=true`; writes only via service-role endpoint (see 4.3) or admin-authenticated UI.

### 4.2 Public rendering — RECOMMEND: Cloudflare Pages Functions (edge SSR)

- `website/functions/blog/index.ts` + `website/functions/blog/[slug].ts` (or a single
  `functions/blog/[[path]].ts`), fetching published rows from Supabase via REST, rendering
  markdown→HTML server-side, emitting full `<head>` (title, meta, OG, Twitter, JSON-LD
  BlogPosting + BreadcrumbList, canonical).
- Add `/blog*` to the middleware `TORTOISE_ONLY` set (tortoise-host-only, consistent with docs/legal).
- Extend sitemap coverage with published blog URLs, keep robots.txt cross-submission as-is.
  Consider **IndexNow** for instant publish notifications.
- **Sitemap-update mechanism (design decision):** posts publish outside the deploy cycle
  (Supabase rows), so a static `sitemap-product.xml` cannot know about them. Two options: (a)
  a dynamic `/sitemap-product.xml` (or `/blog/sitemap.xml`) Pages Function that renders
  published URLs from Supabase at request time — recommended; or (b) a deploy-time regen job
  that rewrites the sitemap whenever a post is published. Option (a) needs the middleware
  `TORTOISE_ONLY` set + robots.txt to keep pointing at the same sitemap URL.
- **RSS (recommended include, owner-approved 2026-08-27):** a `/blog/feed.xml` RSS 2.0 route on
  the same dynamic Function — ~30 lines, zero new infra, feeds newsletter/aggregator tools and
  AI agents that watch sites. In scope (epic scope #8).
- No React for the public blog. No framework migration of the existing static site.

### 4.3 Agent write path — RECOMMEND: scoped publish API (agent-publish model)

- Agents (me/pi, other tools) write AND publish via **a small authenticated endpoint** — either a
  Cloudflare Function using the Supabase service-role key (server-side only) or a new FastAPI
  endpoint on `api.premiselabs.co`. **Agents have their own scoped credentials** (per-agent
  tokens/keys) — never anonymous, never the admin UI's session.
- Agents may set `status='published'` (owner decision). Every publish writes `published_by` +
  `published_at` (audit). The endpoint rejects writes with no valid agent credential.
- **Safety rails (not blockers):** (1) audit trail — which agent published what, when; (2)
  `hold_for_review` flag an agent can set when unsure → post stays private until a human clears
  it in the CMS; (3) one-click unpublish in the CMS (reverts to draft, drops from sitemap);
  (4) optional global review-gate toggle for sensitive content (config, not architecture).
- Accepts markdown body + metadata (title, slug, excerpt, meta_title, meta_description, tags,
  cover image via Storage upload). Validates schema before storing.
- Optional later: Tortoise MCP tool so agents can publish from any agent harness via MCP.

### 4.4 Admin/review UI — RECOMMEND: small React SPA (matches existing dashboard pattern) OR minimal server-rendered pages

- **Option A (recommended):** React+Vite app (mirror `website/apps/dashboard` structure) at
  `website/apps/blog-admin`. **Port the ElDato editor code** (owner decision 2026-08-27):
  `GuideEditor`, `GuidesList`, `GuideEditorToolbar`, `ImageNode`, `BlockDragHandle` + the
  shadcn/ui primitives they use — deal extensions stripped, TipTap markdown import/export
  added. Views: (1) **Review queue** — published-by-agents content that hasn't been reviewed yet
  (and any `hold_for_review` items), with rendered preview; (2) edit (ported TipTap editor);
  (3) actions: approve/mark-reviewed, request changes (back to draft), **unpublish** (instant),
  archive; (4) audit view — what each agent published, when. Auth: existing Supabase auth,
  gated to owner/admin role (needs a role-decision, see 4.5).
- **Option B (smallest):** server-rendered admin pages (Functions) with forms — no build step,
  less polished.
- The review surface is the throughput-critical design (per §3.1 anti-patterns: bulk actions,
  quality signals, structured feedback).

### 4.5 Admin authorization — OPEN QUESTION

No `is_app_admin` exists in tortoise Supabase (ElDato had one). Options: (a) reuse
`team_memberships.role` (owner/admin), (b) a small allowlist table, (c) a dedicated
`blog_reviewers` table. Needs human decision — likely (a) or (c).

### 4.6 Repo placement — the big architectural question (needs human decision)

**Context:** blog *plumbing* (page templates, editor UI, functions) is code that changes rarely;
blog *posts* are Supabase rows + Storage images and never touch git. So the "blog churn in a
critical repo" concern is mostly resolved by architecture either way.

**Option A — blog plumbing in the tortoise repo `website/` (RECOMMENDED):**
- Pros: same Pages project = same domain path `/blog/…` (SEO-optimal, per §3.6); existing
  deploy-pages.yml pipeline; existing middleware/SEO/design tokens; one repo to reason about.
- Cons: PRs land in the critical public product repo (mitigated: additive `website/blog*`
  surface, existing CI ignores it except deploy-pages); public repo means any *code* is public
  (fine — content is in Supabase).
- Self-host impact: none (verified §2.3).

**Option B — separate repo + separate Pages project:**
- Pros: total isolation from the product repo; independent deploy cadence.
- Cons: cannot serve `/blog/…` on tortoise.premiselabs.co without a Worker proxy or host
  change; would force a subdomain (blog.premiselabs.co) which reopens the SEO consolidation
  problem (§3.6); duplicates middleware/design tokens; more infra to maintain.

**Recommendation: Option A** — blog in `website/`, content in Supabase. If isolation is still
desired after that, a separate repo can wrap only the *admin app* (which is genuinely separable
as its own Pages project, like the dashboard already is) — but the public `/blog` rendering
must stay in the shared project for the domain.

---

## 5. Recommendation (Synthesis)

**Build, don't buy; port ElDato's pattern, don't fork its code.**

1. **Do NOT fork ElDato.** Its value is architectural (schema shape, publish/review flow, SEO
   fields), not code — the deal-embed/columns extensions are dead weight for Tortoise. Port the
   *pattern* into the existing tortoise stack.
2. **Content:** Supabase `blog_posts` (markdown canonical) + `blog-images` bucket, in the
   existing Supabase project.
3. **Public blog:** Cloudflare Pages Functions edge-SSR at `/blog` + `/blog/:slug` — full SEO
   head, JSON-LD, canonical; join `TORTOISE_ONLY`; extend sitemap; IndexNow.
4. **Agent path:** scoped **publish-capable** API (owner decision 2026-08-27: agents publish
   directly) with audit (`published_by`/`published_at`), optional `hold_for_review` self-gate,
   and one-click human unpublish. Optional MCP tool later.
5. **Human path:** React admin (dashboard-style, **ElDato editor ported**) — review queue
   (agent-published, unreviewed), preview/edit (ported TipTap editor w/ markdown import),
   approve/request-changes/**unpublish**/archive, audit view. Gated by Supabase auth + owner/admin.
6. **Repo:** tortoise repo `website/` (recommended); alternative documented above.
7. **No React for the public site. No framework migration. No self-host impact.**

**Estimated shape of the epic:** new Supabase migration (table + bucket + RLS + agent-keys
store), 3-4 Pages Functions files (blog render, agent API, sitemap/feed), middleware prefix
change, sitemap/robots extension, **blog-admin SPA ported from ElDato's editor (TipTap +
shadcn/ui — deal extensions stripped)**, deploy-pages.yml additions, E2E/verify step. Smaller
than ElDato's original because deal-embeds, hreflang x2 languages, geographies, and the
editorial content pipeline are out of scope — but the editor UI itself carries ElDato's
polish forward.

---

## 6. Open Questions Requiring Human Decision

### 6.1 Locked decisions (owner, 2026-08-27)

| # | Question | Decision |
|---|---|---|
| 1 | Repo placement | **A** — blog plumbing in the tortoise repo `website/` (public render must stay in the shared Pages project for the domain; admin app separable later if wanted) |
| 2 | Editor depth | **ElDato-style** — TipTap WYSIWYG with markdown import/export (agents' markdown flows into the editor for human refinement) |
| 3 | Agent credentials | **Per-agent keys** (pi, etc.) — each agent has its own scoped credential, auditable per agent |
| 4 | Blog URL | **`/blog`** — `tortoise.premiselabs.co/blog` (index) + `/blog/:slug` |
| 5 | Publish flow (2026-08-27 refinement) | **Default = review queue** (agent posts land as drafts; owner publishes). **Direct publish** allowed when the owner explicitly asks (no manual step). **Agents may edit their own posts** (PATCH, `created_by`-scoped). **Rate limits generous** (120 req/min, 2,000 req/day per key) so rewrite loops never block |

### 6.2 Remaining minor decisions (defaults acceptable unless changed)

| # | Question | Default rec |
|---|---|---|
| 1 | Admin auth gate | team role owner/admin (fastest) |
| 2 | Author identity | both — author field carries agent or human name |
| 3 | Post-publish review UX | dedicated "unreviewed agent-published" queue |
| 4 | Review-gate toggle (global hold switch) | none v1 (agents trusted); easy to add later |

---

## 7. Source Confidence Summary

| Claim | Tier | Sources (categories) |
|---|---|---|
| Scribe pattern (agent drafts, human publishes) is canonical | HIGH | Cosmic (vendor doc), KernelCMS (vendor doc), CMS-as-agent-substrate typology (practitioner), Hygraph (vendor), Storyblok+Claude Code (practitioner) |
| AI crawlers don't execute JS; CSR blog = invisible to AI search | HIGH | Ranking Lens (SEO practitioner), Agentikas (practitioner), Swazzy (practitioner), Cloudflare docs |
| Cloudflare Pages Functions/edge SSR is the right render path | HIGH | IGNAX (practitioner), Swazzy (practitioner), Cloudflare docs, Sunstone (practitioner) |
| Supabase-as-CMS pattern is proven | HIGH | RapidDev, Makerkit/Supamode, PawPress, dev.to, **ElDato in-house** |
| TipTap is the custom-editor standard; overkill for simple inputs | MEDIUM-HIGH | MakerStack, José Ortega (practitioner), Ashby (practitioner), Tiptap docs |
| Markdown canonical + rendered HTML is a valid storage choice | MEDIUM | dev.to (personal-blog precedent), Makerkit (markdoc), Tiptap docs; ⚠️ single-category practitioner sources, low corpus |
| "Don't build your own CMS" advice | HIGH (general) / MEDIUM (applicability) | dev.to, Hygraph, ButterCMS, SitePoint; applicability argued from internal facts (infra exists, ElDato precedent, agent-write requirement) |
| Same-domain blog > subdomain for SEO | MEDIUM | standard SEO practice + tortoise's own host-consolidation work (internal) |

**Contradictions:** the only material tension is build-vs-buy (§3.5). Resolved by the
infrastructure-exists + port-not-invent + agent-write-reasoning above — but it is the right
question for the epic's align gate to revisit.

---

## 8. Sources

Internal: eldato `src/components/guides/`, `src/pages/admin/GuideEditor.tsx`,
`src/pages/GuidePage.tsx`, `supabase/migrations/20260222000000_create_guides_table.sql`,
`20260720000017_content_queue_table.sql`, `src/lib/seo/`, `src/components/SEOHead.tsx`;
tortoise `website/website_architecture.md`, `website/functions/_middleware.ts`,
`Dockerfile.selfhost`, `Dockerfile.hosted`, `supabase/migrations/`, `.github/workflows/`,
live inspection of tortoise.premiselabs.co.

External: Cosmic (2026-07-31), KernelCMS docs, Abhishek Shankar "A typology of CMS-as-agent-substrate
patterns" (2026-05-25), Hygraph PR-workflow (2026-03-03), Storyblok-Claude Code (2026-04-11),
Ranking Lens SPA SEO guide (2026-03-30), Agentikas SSR-vs-CSR (2026-04-24), Swazzy React-blog-on-
Cloudflare (2026-07-02), IGNAX Cloudflare Pages SEO (2026-05-27), Sunstone Astro indexing (2026-06-30),
MakerStack Tiptap review (2026-03-20), José Ortega custom Tiptap editor (2026-07-10), Ashby Tiptap
part 1, Tiptap static-renderer docs, RapidDev Supabase blog backend (2026-04-25), Makerkit Supabase
CMS (2026-02-06/2025-10-13), PawPress (GitHub), dev.to John Haab custom publishing system (2026-08-23),
dev.to custom-CMS-is-a-mistake (momciloo), Hygraph/ButterCMS/SitePoint build-vs-buy.
