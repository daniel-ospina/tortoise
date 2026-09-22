# Implementation Plan — Blog AI Suite (#1861, #1863, #1864, #1865, #1866)

**Date:** 2026-08-28 | **Owner:** organisation-design-team | **Worktree:** `.worktrees/feat/1861-blog-ai-suite`

Five tightly-coupled standard tasks on the blog surface, implemented as one branch with per-issue commits, one PR, one review. All scoping O/I/T on the issues is the source of truth; this plan adds design decisions + integration points.

## Issue map & dependency order

| # | Deliverable | Depends on |
|---|---|---|
| 1866 | Agent API meta contract: `meta_title` ≤200→**60**, `meta_description` ≤300→**155** (match editor/SSR) | none |
| 1865 | Edge cache purge by URL on unpublish/archive (PATCH write path) | 1866 (same file, do first) |
| 1861 | `generate-seo.ts` Pages Function + editor "Generate SEO" action (fields-based review) | 1862 keyword module (already on main), 1866 constants |
| 1864 | Crawler hardening: robots.txt `Disallow: /admin/`, `X-Robots-Tag: noindex` on non-published + admin shell, lifecycle E2E | none |
| 1863 | `generate-cover.ts` Pages Function + editor "Generate cover" action (founder-likeness) | 1861 pattern (same shape) |

## 1866 — Meta length contract (shared const + 2 files)

- **New `website/functions/blog/_shared/meta-contract.ts`** (zero-dep): `export const META_TITLE_MAX = 60; export const META_DESCRIPTION_MAX = 155; export const EXCERPT_MAX = 300; export const TAGS_MAX = 10; export const TAG_LEN_MAX = 40;` — **single source of truth** (verifier P1-1).
- **`website/functions/blog/api/posts.ts`:** replace the local `META_TITLE_MAX=200`/300 literals with the shared consts in BOTH `validateCreate` and `validatePatch` (error strings update to "meta_title max 60 chars"). `TITLE_MAX=200` stays (title is not part of the SEO contract).
- **Editor** is a separate app (can't import from `functions/`) — documented decision: literals live in JSX (`maxLength={60}`/`{155}`), locked by a unit test asserting blog-api.ts constants == meta-contract values.
- **DB check:** run query at implementation; if rows exceed 60/155 → one-off word-boundary trim UPDATE (decision: trim, not defer). Expect 0 rows.
- **Tests:** extend `tests/e2e/test_blog.py` agent-API negative cases — assert 400 on 61/156-char values, 200 on 60/155 (needs valid `BLOG_E2E_AGENT_KEY`).

## 1865 — Edge cache purge on unpublish/archive (BOTH write paths — verifier P0/P1-1)

- **New helper `website/functions/blog/_shared/cloudflare-purge.ts`** (zero-dep, underscore-prefixed):
  - `purgeUrl(url: string, env): Promise<boolean>` — POST `https://api.cloudflare.com/client/v4/zones/{zone}/purge_cache` `{ files: [url] }`, `Authorization: Bearer <token>`, 3s timeout, fire-and-forget, fail-open-but-logged (no token → false, never blocks the write).
- **Purge BOTH URLs** `/blog/{slug}` and `/blog/{slug}/` (verifier P2-4b — canonical + trailing-slash variants).
- **Path 1 — agent API (`posts.ts` PATCH):** after successful PATCH where resulting status is `draft` (unpublish) — archive is NOT reachable via PATCH (`validatePatch` accepts only draft|published) so the archive branch is dropped from here.
- **Path 2 — editor (PRIMARY surface):** the editor writes status directly via supabase-js (`blog-api.ts` `updatePost`). Add a **new admin-gated endpoint `website/functions/blog/api/purge.ts`** (same `verifySession` + `isAdmin` port as admin gate): `POST /blog/api/purge` `{ slug }` → calls `purgeUrl` on both variants → `{ purged: bool }`. `blog-api.ts` fires it best-effort (bearer token, fail-open) inside `unpublishPost`, `requestChangesPost` (status→draft), `archivePost`, and `buildSaveRecord` when `mode==='draft'` on a published post. Covers the W2 human gate + archive.
- **E2E:** lifecycle test must unpublish via BOTH paths (agent API AND a direct write simulating the editor) to prove the window is closed (verifier P0).

## 1861 — Generate SEO (new Function + editor action)

### `website/functions/blog/api/generate-seo.ts` (new, zero-dep)
- **Route:** `POST /blog/api/generate-seo` — admin-gated via ported `verifySession` + `isAdmin` (copy from `functions/admin/[[path]].ts` — they're module-private; same-origin SPA calls need no CORS). **Key the rate limit on the verified user id**, not IP (verifier P2-5); in-memory counter, best-effort per-isolate (posts.ts caveat). 30/min per user.
- **Body:** `{ title, body, tags? }` — body never echoed in response (injection surface stays server-side).
- **Provider:** OpenRouter `POST https://openrouter.ai/api/v1/chat/completions`: primary `deepseek/deepseek-v4-flash` (`response_format: {type:"json_object"}`, temp 0.3, max_tokens 400) → fallback `anthropic/claude-haiku-4.5` on non-2xx/parse-fail.
- **Prompt:** Tortoise SEO strategy + title + body preview (truncate ~2000 chars) + keyword injection: ≤3 keywords per matched tag via `keywordsFor(tag)` (first 3), content-driven fallback when no tags match. Request JSON: `{ slug, excerpt, tags: string[], meta_title, meta_description }`.
- **`enforceConstraints` lives in `website/functions/blog/_shared/seo-constraints.ts`** (pure, zero-dep — the natural home per #1861 scoping; imported by generate-seo.ts AND unit-tested): word-boundary truncate for meta_title ≤60 (+ " | Tortoise" suffix if room), meta_description ≤155, excerpt ≤300, tags ≤10/≤40 normalized lowercase-alphanumeric-dash, slug RE-validated (re-slugify if invalid). JSON extraction regex `/\{[\s\S]*\}/`; 502 on parse/fields-missing.
- **Fixture corpus:** unit-test `enforceConstraints` with ≥10 fixture posts spanning title/body/tag extremes (verifier P2-4c) — 0 constraint violations.
- **Cost surface:** return `{ provider, input_tokens, output_tokens, cost_estimate }` (DeepSeek $0.14/$0.28, Haiku $1.00/$5.00 per M).
- **HSTS + OPTIONS:** all responses carry `HSTS` from `_lib.ts`; add `onRequestOptions` → `Allow: POST, OPTIONS` (verifier P2-4).

### Editor (`PostEditor.tsx`) — fields ARE the review surface (owner decision; verifier P2-3 deviation note)
- **Deviation note (Indicator 2 out of scope):** publish-time auto-generate/block is intentionally dropped per owner decision 2026-08-28 — fields ARE the review surface; generation is a one-click action, all fields editable, normal publish.
- **"Generate SEO" button** in the SEO card: reads title from form + **body via `editorRef.current` → `editorToMarkdown(editorRef.current)`** (body is NOT in `PostFormData` — verifier P2-6) + tags → calls `/blog/api/generate-seo` with user's access token → fills slug (new posts only, `slugTouched` guard — don't overwrite typed slug), excerpt, tags, meta_title, meta_description. All editable afterward.
- Loading state + cost surfaced (`~$0.001`); error → toast, never blocks save.
- `blog-api.ts`: add `generateSeo(input)` using `supabase.auth.getSession()` token, `fetch('/blog/api/generate-seo')`.
- **E2E:** generate-action test — click → 5 fields populated + editable; slug immutable on edit (verifier P1-4).

## 1864 — Crawler hardening

- **`website/robots.txt`:** add `Disallow: /admin` (prefix form — covers both `/admin` and `/admin/`; matches scoping recommendation, verifier P2-4a). E2E asserts the shipped form.
- **`website/functions/blog/_lib.ts` `notFound()`:** add `X-Robots-Tag: noindex, nofollow` (single choke point).
- **Admin shell (`functions/admin/[[path]].ts` `serveShell`):** add `X-Robots-Tag: noindex, nofollow` to SPA-shell AND placeholder responses; placeholder `<head>` gets `<meta name="robots" content="noindex">` too (built SPA already has it).
- **E2E (`tests/e2e/test_blog.py`):** `test_publish_lifecycle_crawler_visibility` gated on `BLOG_E2E_AGENT_KEY`:
  - lifecycle: create draft → GET /blog/{slug} 404 + X-Robots-Tag → publish → 200 + no noindex → unpublish → 404 + noindex; **draft remains editable pre-publish** (indicator 4 assert)
  - **sitemap/feed published-only across the lifecycle** (indicator 3, verifier P1-2): draft absent / published present / unpublished absent from both; feed description = excerpt
  - robots.txt contains `Disallow: /admin`; admin shell carries X-Robots-Tag noindex
  - unpublish via BOTH agent API and direct-write (editor path) → immediate 404 both times (1865 P0)

## 1863 — Generate cover (new Function + editor action)

### `website/functions/blog/api/generate-cover.ts` (new, zero-dep)
- **Route:** `POST /blog/api/generate-cover` — admin-gated (same port). Rate cap 20/day per user id (verifier P2-5).
- **Body:** `{ title, tags?, mode: "founder" | "abstract" }` — **abstract toggle included per owner decision 2026-08-28 (verifier P1-3)**: abstract = dark-slate/cyan brand tokens, NO reference images; founder = reference-image identity anchor. Default `founder`. NO body markdown (injection risk).
- **Provider:** OpenRouter image generation — **verify endpoint shape against live docs at build time** (verifier P2-1: current docs use `POST /api/v1/images` with `modalities` or `input_references` for reference images, not inline `image_url` blocks): `google/gemini-3.1-flash-image-preview` primary → `black-forest-labs/flux.2-klein-4b` fallback (verifier P2-2 — `flux-2-klein` is not a valid slug). 16:9 (`aspect_ratio: "16:9"`) for OG.
- **Founder reference images (3 Cloudinary anchors):** `https://res.cloudinary.com/djzwqixjt/image/upload/eldato/carousels/references/{character-sheet.png,canonical-face-reference.jpg,canonical-portrait-reference.jpg}`.
- **QC before upload (verifier P2-1-1863):** validate mime ∈ {jpeg,png,webp}, ≤5MB, non-empty; retry ≤2 → 502. Deterministic QC only (no vision QC v1).
- **Upload:** download generated image → upload to `blog-images/{folder}/{timestamp}-generated-cover.png` where **folder = isValidSlug(slug) ? slug : 'draft'** (mirror uploadBlogImage fallback, verifier P2-7); sanitizeObjectKey contract. Service-role key server-side.
- **Response:** `{ image_url, provider, cost_estimate }`.
- **HSTS + OPTIONS:** same convention as 1861 (verifier P2-4).
- **Editor:** "Generate cover" button in Cover Image card + **mode toggle** (founder/abstract) → calls API → sets `cover_image_url`; regenerate + discard; failure never blocks save. `blog-api.ts`: `generateCover(input)`.
- **E2E:** cover set → public article og:image 200; no cover → ASSETS fallback 200 (verifier P1-4).

## Deploy config
- New Pages Function secrets (documented in epic README + deploy-pages.yml comment): `CF_API_TOKEN`, `CF_ZONE_ID` (1865), `OPENROUTER_API_KEY` (1861/1863), **`BLOG_E2E_AGENT_KEY` wired into the `verify-blog` step env** (verifier P2-3). No client-side keys (key exposure P0 avoided).

## Verification
- `npx esbuild` clean on all new/modified functions; `tsc --noEmit` where runnable
- Unit: cloudflare-purge helper (mock fetch), `enforceConstraints` fixture corpus (≥10 posts), meta-contract ↔ editor-literal cross-check, image QC
- E2E: existing suite green + new lifecycle/generate/og-image tests (RUN_BLOG_E2E=1, gated agent key)
- python: `pytest tests/ -k blog` + supabase pgTAP suite
