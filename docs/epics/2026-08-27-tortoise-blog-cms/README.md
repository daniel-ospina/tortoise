---
title: "Tortoise Blog + CMS — Epic Index"
type: index
domain: growth
doc_status: live
created: 2026-08-27
ownedBy: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: tortoise-website
epic: tortoise-blog-cms
---

# Epic: Tortoise Blog + CMS

Blog + CMS for tortoise.premiselabs.co — agent-publish → human-review, SSR public pages, ElDato-editor port, SEO/analytics/RSS. **Status: READY (planned, awaiting implementation).**

## Pipeline artifacts

| Stage | Doc | Gate |
|---|---|---|
| Align | [00-align.md](00-align.md) | PROCEED |
| Scope | [01-scope.md](01-scope.md) | Human-approved |
| Test-design | [02-test-design.md](02-test-design.md) + issue #1802 | Filed |
| Plan | [03-plan.md](03-plan.md) | Review CLEAN |
| Verify | [04-verify.md](04-verify.md) | READY |

## Child issues (dependency order)

| # | Issue | Depends on | Complexity |
|---|---|---|---|
| 1793 | data: blog CMS schema + RLS + storage | — | standard |
| 1794 | feat: blog SSR render (/blog + article) | 1793 | standard |
| 1795 | feat: agent publish/edit API (POST + PATCH) | 1793 | standard |
| 1796 | feat: blog SEO wiring (middleware prefix, sitemap, RSS, robots, site docs) | 1793 | standard |
| 1797 | feat: blog admin gate Function (JWT + is_admin, scoped fallback) | 1793 | standard |
| 1798 | feat: blog admin app (ElDato editor port, review queue, audit) | 1797 | complex |
| 1799 | feat: social share bar + PostHog analytics | 1794 | standard |
| 1800 | chore: blog deploy pipeline + E2E suite (E2E-1..15) | 1794-1799 | standard |
| 1801 | capstone: clickthrough verification | 1800 | standard |

## Key decisions

- **Repo:** plumbing in tortoise repo `website/` (public render shares the Pages project/domain); content in Supabase (private); self-host Dockerfile unaffected (verified).
- **Publish flow:** default = review queue (agent drafts); direct publish only when owner explicitly asks; agents may PATCH their own posts (created_by-scoped, 403 otherwise); generous rate limits (120/min + 2,000/day).
- **Editor:** ElDato TipTap editor ported wholesale (GuideEditor, GuidesList, toolbar, image node, drag handles + shadcn/ui); deal extensions stripped; markdown import/export.
- **Rendering:** Cloudflare Pages Functions edge-SSR (no React on public pages — AI-crawler-visible); markdown → sanitized HTML.
- **Analytics:** reuse existing consent.js + PostHog (project 548850, US Cloud).
- **SEO:** /blog prefix rule (tortoise-host-only), dynamic sitemap + RSS, canonical/OG/JSON-LD per post.

## Prototype

`docs/prototypes/2026-08-27-tortoise-blog/prototype.html` — index card grid, editorial article, share bar + mobile bottom bar, empty state (dark slate/cyan).

## Deployment ops checklist (required before the blog surface is live)

1. **Pages Function env bindings** on the `premise-labs` Cloudflare Pages project (dashboard → Settings → Environment variables):
   - `SUPABASE_URL` (https://ybetwichurajbfswfeqa.supabase.co)
   - `SUPABASE_ANON_KEY` (public anon key)
   - `SUPABASE_SERVICE_ROLE_KEY` (server-side secret — agent API + admin gate)
   - `SUPABASE_JWT_SECRET` (Supabase project JWT secret — admin gate)
2. **Repo secrets** (for the deploy build step): `SUPABASE_URL`, `SUPABASE_ANON_KEY` (GitHub → Settings → Secrets).
3. **Supabase migration** applied: `20260827000001_blog_cms.sql` (supabase-deploy CI applies it; verify `blog_posts` exists).
4. **Agent keys seeded**: `blog_agent_keys` INSERT (sha256 of the plaintext key, printed once) — the ops seed script.
5. **Owner seeded**: `blog_admins` INSERT with the owner's auth user id.
6. Post-deploy: run `RUN_BLOG_E2E=1 ALLOW_PROD=1 ... pytest tests/e2e/test_blog.py` (wired as the verify-blog job, warn-only).
