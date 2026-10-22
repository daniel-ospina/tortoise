-- ============================================================================
-- Migration 20261022000001: IndexNow submission stamp on blog_posts — #9680
--
-- IndexNow (#9680) notifies Bing / Yandex / Seznam / Naver / Amazon / Yep when an
-- article is published. ⛔ It does NOT reach Google — Google does not consume
-- IndexNow. Nothing in this migration, its comments or its consumers may claim
-- otherwise; that claim is why #7751 was recorded as shipped while no code
-- existed.
--
-- ⚠️ WHY A COLUMN EXISTS AT ALL. The IndexNow protocol has NO server-side
-- idempotency: there is no dedupe token and no "already submitted" answer, and
-- its FAQ only asks that you avoid submitting the same URL many times a day
-- (verified against indexnow.org/documentation + /faq, 2026-10-22). So the "one
-- submission per URL per day" requirement (#9680 item 3) has to be enforced on
-- OUR side, and it has to survive an isolate restart — a per-isolate Map in the
-- Pages Function would be correct only until the next isolate, i.e. green in the
-- test and a flood in production. This column is that durable state.
--
--   * indexnow_submitted_at timestamptz NULL — when this post's canonical URL was
--     last SUBMITTED to IndexNow. NULL = never submitted. It records the ATTEMPT,
--     not the vendor's acceptance: the submission is fail-soft (a failed
--     notification must never fail the publish), so stamping only on success
--     would make a vendor outage re-attempt on every re-publish.
--
-- ⛔ NO BACKFILL here, and none in the publish handler (#9680 item 5). The four
-- already-published URLs stay NULL until the operator submits them ONCE, by
-- hand, after the key file is verified live — see the IndexNow section of
-- `docs/blog-authoring.md`. A bulk path in the publish handler is exactly the
-- flood this column exists to prevent.
--
-- Grants: nothing to add. This project relies on Supabase's ALTER DEFAULT
-- PRIVILEGES, which grant table-level privileges on public tables to anon /
-- authenticated / service_role, and a TABLE-level grant covers columns added
-- later — the same mechanism stated for a table at
-- 20261001000003_connectors.sql ("table-level ALL + BYPASSRLS from Supabase's
-- default privileges"). (No migration in this directory issues an explicit GRANT
-- ON blog_posts.) A future lane must not read this as "add column-level GRANTs to
-- be safe": that would NARROW what the table grant already covers.
--
-- ⚠️ `updated_at` SIDE EFFECT. `blog_posts_set_updated_at` is an unconditional
-- BEFORE UPDATE trigger, so writing this column also rewrites the row's
-- updated_at — which is user-facing (the article's JSON-LD dateModified and the
-- sitemap's lastmod). Harmless while every write co-occurs with a publish that
-- bumps it anyway; a future writer that stamps OUTSIDE a publish would move a
-- live article's modification date. Fixing that belongs in the trigger (skip
-- when only internal columns changed), not in this migration.
-- Applied migrations are immutable history; this is additive.
-- ============================================================================

ALTER TABLE public.blog_posts
  ADD COLUMN IF NOT EXISTS indexnow_submitted_at timestamptz;

COMMENT ON COLUMN public.blog_posts.indexnow_submitted_at IS
  'Last IndexNow submission ATTEMPT for this post''s canonical URL (#9680). '
  'NULL = never submitted. Set after the attempt regardless of the endpoint''s '
  'answer, because submission is fail-soft and is not retried in the request '
  'path. Enforces AT MOST one submission per row per 24h — not atomically and '
  'not per URL: two overlapping publish transitions, or a deleted-and-recreated '
  'slug, can submit again. IndexNow itself has no server-side idempotency. '
  'NOT part of the editorial record: writing it fires blog_posts_set_updated_at, '
  'so it bumps updated_at (dateModified / sitemap lastmod). Does NOT reach Google.';
