---
title: "Tortoise Blog + CMS — Integration Surface Map"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-27
ownedBy: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: tortoise-website
epic: tortoise-blog-cms
---

# Integration Surface Map — Tortoise Blog + CMS

Test-design gate output for the epic. Input: scope `01-scope.md` Customer Value Map + E2E tests.
Each scoped capability maps to ≥1 surface; every surface has an assigned test layer.

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | Supabase `blog_posts` | DB | Both | **Integration** (real Supabase) + **pgTAP** (RLS + status transitions) | row shape: slug UNIQUE, status CHECK (draft/published/archived), body markdown, meta_title/meta_description, published_by/published_at/reviewed_by/reviewed_at/hold_for_review | RLS bypass, illegal status transition, missing meta at publish, duplicate slug race |
| 2 | Supabase `blog_agent_keys` | DB (auth-adjacent) | Read (Function only) | **pgTAP** | agent_name UNIQUE, key_hash (hashed, never plaintext), active flag | hash compare flaw, unknown/disabled agent accepted |
| 3 | Supabase Storage `blog-images` | External service | Both | **Integration** | public CDN URL; path `{slug}/{file}`; MIME jpeg/png/webp; ≤5MB | oversize, wrong MIME, path traversal in filename |
| 4 | Pages Function — blog render (`/blog`, `/blog/:slug`) | Edge/SSR | In | **E2E** (curl, no JS) + **Integration** | SSR HTML w/ full head (title/meta/OG/Twitter/JSON-LD/canonical); published AND NOT hold_for_review only | draft/hold leak, markdown XSS (sanitize), missing canonical, 404 for archived |
| 5 | Pages Function — agent publish API | Edge/API | Both | **Integration** | per-agent key auth (header), zod schema validation, status transitions, audit write (`published_by`), slug uniqueness | unauthenticated write, invalid key, malformed body, concurrent same-slug insert |
| 6 | Middleware host routing (`/blog` prefix) | Edge/guard | Guard | **E2E** | tortoise host serves `/blog*`; premiselabs.co 301s `/blog*` to tortoise host (prefix rule) | prefix false-positives (`/blogpost`), trailing-slash, company-host leak |
| 7 | Supabase auth (admin session) | Auth | Guard | **Integration** + **E2E** | owner/admin role only on admin route; 401/redirect to `/auth` | non-owner access, expired/absent session, audit data exposure |
| 8 | PostHog (`consent.js` + events) | External service | Out | **E2E** (browser) | consent-gated: pageview on /blog + /blog/:slug, `share_click`, `article_read`; decline ⇒ zero events/cookies | event not firing, consent bypass, double init on SSR pages |
| 9 | Sitemap + RSS (Pages Function) | Edge/API | In | **Integration** | published-only URLs, valid XML, absolute canonical URLs, newest-first | draft leak, malformed XML, relative URLs |
| 10 | Social share URL builder | Pure logic | Out | **Unit** | `utm_source=<network>&utm_medium=share` per network; encode once | double-encoding, missing UTM, wrong network param |
| 11 | TipTap editor (ported from ElDato) | State/UI | Internal | **Unit** + **Integration** | markdown import/export roundtrip; image upload/delete; draft/publish save flows | markdown↔HTML roundtrip loss, stale closure on async save, image delete orphan |
| 12 | Deploy (deploy-pages.yml + supabase-deploy) | CI | — | **E2E** post-deploy | blog-admin builds + deploys; migration applies cleanly | stale bundle served, migration ordering, blog routes 404 after deploy |

## Bug Pattern Flags

- **SQL business logic** — `blog_posts` status transitions (draft→published→archived, publish-time audit writes) live in Postgres → **pgTAP required**, not TS mocks (historical #1 production-bug source).
- **Markdown XSS** — agent-authored markdown renders to HTML server-side; sanitize output (allowlist) + E2E assert script tags stripped.
- **Duplicate slug race** — two agents publishing the same slug concurrently → unique constraint + retry/409 on the Function, tested with concurrent requests.
- **Stale closures** — ported TipTap editor async save flows → unit test that save callback sees current form state, not captured stale state.
- **N+1 queries** — `/blog` index fetching posts + cover images → single batched query, flag in plan; test with N>1 posts and verify query count.
- **Silent function skips** — publish endpoint must reach the real Supabase write (no early return/fallback); integration test asserts row exists + audit columns set.

## Checklist Notes

- **Contract defined:** zod schemas for agent API (create/publish/update) + `blog_posts` row type shared between Function and admin app.
- **Boundary values:** slug length (1-100, charset), title/excerpt limits, tag count (0-10), image size (0.5KB-5MB), status enum values.
- **Empty vs null:** body empty at draft allowed, blocked at publish; meta_title/meta_description null ⇒ derived defaults (title pattern) server-side.
- **Failure modes per surface:** timeout/503 on Supabase (render degrades to 503 with cache headers), invalid agent key (401), duplicate slug (409), Storage 413 (size), PostHog blocked (no crash — analytics never blocks page render).
- **Atomicity:** publish = single row update (status + audit fields together) — one UPDATE, not multi-write; idempotency: same agent+publish called twice ⇒ second is a no-op/409, never duplicate content.

---
*Filed as epic artifact; the child issue (test-design: <epic> integration-surface map) is created at the decompose stage per epic-workflow.*
