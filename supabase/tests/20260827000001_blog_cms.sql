-- ============================================================================
-- SQL-level verification for migration 20260827000001 (issue #1793 — blog CMS)
-- NOTE: assertion messages use the '0017' shorthand for this migration
-- (0017 == migration 20260827000001, blog CMS).
-- blog_posts lifecycle triggers, RLS, is_admin(), blog_agent_keys isolation.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (harness applies ALL migrations incl. 0017, then runs this suite with
--   ON_ERROR_STOP semantics — every assertion RAISEs on failure)
--
-- Test rows use the "-blog" suffix for safe cleanup.
-- ============================================================================

-- ── Assertion helper (matches harness convention) ──────────────────────────
CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;
GRANT USAGE ON SCHEMA tests TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION tests.assert(boolean, text) TO anon, authenticated, service_role;
-- Harness bootstrap does not grant auth schema usage; needed for the admin-seed inserts
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT ALL ON auth.users TO service_role;

-- ── 1. Schema presence ─────────────────────────────────────────────────────
SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.tables
          WHERE table_schema = 'public' AND table_name = 'blog_posts'),
  '0017: blog_posts table exists');
SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.tables
          WHERE table_schema = 'public' AND table_name = 'blog_agent_keys'),
  '0017: blog_agent_keys table exists');
SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public' AND table_name = 'blog_posts' AND column_name = 'review_note'),
  '0017: blog_posts.review_note exists');
SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public' AND table_name = 'blog_posts' AND column_name = 'hold_for_review'),
  '0017: blog_posts.hold_for_review exists');
SELECT tests.assert(
  (SELECT count(*) FROM pg_trigger WHERE tgname IN
     ('blog_posts_publish_guard','blog_posts_unpublish_review_state',
      'blog_posts_archive_terminal','blog_posts_set_updated_at')) = 4,
  '0017: all four blog_posts triggers exist');
SELECT tests.assert(
  EXISTS (SELECT 1 FROM storage.buckets WHERE id = 'blog-images' AND public = true),
  '0017: blog-images bucket exists and is public');

-- ── 2. Slug / status constraints ───────────────────────────────────────────
-- Invalid slug rejected (uppercase + underscore)
DO $$
BEGIN
  BEGIN
    INSERT INTO public.blog_posts (slug, title, status) VALUES ('Bad_Slug', 't', 'draft');
    RAISE EXCEPTION '0017: invalid slug NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN
    NULL; -- expected
  END;
END $$;

-- Valid slug accepted
INSERT INTO public.blog_posts (slug, title, status) VALUES ('valid-slug-blog', 't', 'draft');
SELECT tests.assert(
  (SELECT count(*) FROM public.blog_posts WHERE slug = 'valid-slug-blog') = 1,
  '0017: valid slug accepted');

-- Invalid status rejected
DO $$
BEGIN
  BEGIN
    INSERT INTO public.blog_posts (slug, title, status) VALUES ('bad-status', 't', 'bogus');
    RAISE EXCEPTION '0017: invalid status NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN
    NULL; -- expected
  END;
END $$;

-- ── 3. Publish guard trigger ───────────────────────────────────────────────
-- Published without audit fields → rejected
DO $$
BEGIN
  BEGIN
    INSERT INTO public.blog_posts (slug, title, status) VALUES ('publish-guard', 't', 'published');
    RAISE EXCEPTION '0017: publish without published_by NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN raise_exception THEN
    NULL; -- expected
  END;
END $$;

-- Published WITH audit fields → accepted
INSERT INTO public.blog_posts (slug, title, body, status, published_by, published_at)
VALUES ('publish-ok-blog', 'ok', 'body', 'published', 'test-agent', now());

-- Unpublish (published→draft) clears review state; review_note survives when supplied
UPDATE public.blog_posts SET reviewed_by = 'owner', reviewed_at = now()
  WHERE slug = 'publish-ok-blog';
UPDATE public.blog_posts SET status = 'draft', review_note = 'fix the intro'
  WHERE slug = 'publish-ok-blog';
SELECT tests.assert(
  (SELECT reviewed_by IS NULL AND reviewed_at IS NULL
     AND review_note = 'fix the intro' FROM public.blog_posts WHERE slug = 'publish-ok-blog'),
  '0017: unpublish clears review state, preserves request-changes note');

-- Republish re-enters the queue (reviewed_at NULL) — plan W2
UPDATE public.blog_posts SET status = 'published', published_at = now(), published_by = 'test-agent'
  WHERE slug = 'publish-ok-blog';
SELECT tests.assert(
  (SELECT reviewed_at IS NULL FROM public.blog_posts WHERE slug = 'publish-ok-blog'),
  '0017: republish leaves review state NULL (re-enters queue)');

-- ── 4. Archive terminal ────────────────────────────────────────────────────
UPDATE public.blog_posts SET status = 'archived' WHERE slug = 'publish-ok-blog';
DO $$
BEGIN
  BEGIN
    UPDATE public.blog_posts SET status = 'published' WHERE slug = 'publish-ok-blog';
    RAISE EXCEPTION '0017: transition out of archived NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN raise_exception THEN
    NULL; -- expected
  END;
END $$;

-- ── 5. RLS — public reads ──────────────────────────────────────────────────
-- Seed: one published, one draft, one held
INSERT INTO public.blog_posts (slug, title, body, status, published_by, published_at)
VALUES ('pub-blog', 'pub', 'b', 'published', 'test-agent', now());
INSERT INTO public.blog_posts (slug, title, body, status) VALUES ('draft-blog', 'd', 'b', 'draft');
INSERT INTO public.blog_posts (slug, title, body, status, published_by, published_at, hold_for_review)
VALUES ('held-blog', 'h', 'b', 'published', 'test-agent', now(), true);

SET ROLE anon;
SELECT tests.assert(
  (SELECT count(*) FROM public.blog_posts) = 1
  AND (SELECT count(*) FROM public.blog_posts WHERE slug = 'pub-blog') = 1,
  '0017 RLS: anon sees published-only (1 row), no draft/hold');
RESET ROLE;

SET ROLE authenticated;
SELECT tests.assert(
  (SELECT count(*) FROM public.blog_posts) = 1
  AND (SELECT count(*) FROM public.blog_posts WHERE slug = 'pub-blog') = 1,
  '0017 RLS: authenticated non-admin sees published-only');
RESET ROLE;

-- ── 6. is_admin() + admin RLS ─────────────────────────────────────────────
-- Admin user: seeded into the blog_admins allowlist (seed as service_role)
SET ROLE service_role;
INSERT INTO auth.users (id, email) VALUES ('11111111-1111-1111-1111-111111111111', 'blog-admin@example.com');
INSERT INTO public.blog_admins (user_id) VALUES ('11111111-1111-1111-1111-111111111111');
RESET ROLE;

SET request.jwt.claim.sub = '11111111-1111-1111-1111-111111111111';
SET ROLE authenticated;
SELECT tests.assert(public.is_admin(), '0017: allowlisted user is_admin() = true');
SELECT tests.assert(
  (SELECT count(*) FROM public.blog_posts) >= 4,
  '0017 RLS: admin sees ALL rows (incl. draft + hold)');
UPDATE public.blog_posts SET title = 'draft-edited' WHERE slug = 'draft-blog';
SELECT tests.assert(
  (SELECT title = 'draft-edited' FROM public.blog_posts WHERE slug = 'draft-blog'),
  '0017 RLS: admin UPDATE allowed');
RESET ROLE;

-- Non-admin authenticated cannot UPDATE (RLS = silent 0 rows, not an error)
SET request.jwt.claim.sub = '00000000-0000-0000-0000-000000000000';
SET ROLE authenticated;
UPDATE public.blog_posts SET title = 'hacked' WHERE slug = 'pub-blog';
SELECT tests.assert(
  (SELECT title <> 'hacked' FROM public.blog_posts WHERE slug = 'pub-blog'),
  '0017 RLS: non-admin UPDATE blocked (0 rows affected)');
RESET ROLE;
RESET request.jwt.claim.sub;

-- Non-admin INSERT also blocked (FOR ALL WITH CHECK gate)
SET ROLE anon;
DO $$
BEGIN
  BEGIN
    INSERT INTO public.blog_posts (slug, title, status, published_by, published_at)
    VALUES ('anon-insert-blog', 'x', 'published', 'anon', now());
    RAISE EXCEPTION '0017 RLS: anon INSERT NOT blocked' USING ERRCODE = 'P0002';
  EXCEPTION WHEN raise_exception OR insufficient_privilege THEN
    NULL; -- expected (marker P0002 escapes)
  END;
END $$;
RESET ROLE;

-- ── 7. Storage RLS (harness storage.objects now has RLS enabled) ───────────
SET ROLE service_role;
INSERT INTO storage.objects (bucket_id, name) VALUES ('blog-images', 'test-img.png');
RESET ROLE;
-- public read allowed
SET ROLE anon;
SELECT tests.assert(
  (SELECT count(*) FROM storage.objects WHERE bucket_id = 'blog-images' AND name = 'test-img.png') = 1,
  '0017 storage: public read of blog-images object allowed');
-- anon INSERT denied (WITH CHECK is_admin())
DO $$
BEGIN
  BEGIN
    INSERT INTO storage.objects (bucket_id, name) VALUES ('blog-images', 'anon-img.png');
    RAISE EXCEPTION '0017 storage: anon INSERT NOT blocked' USING ERRCODE = 'P0002';
  EXCEPTION WHEN raise_exception OR insufficient_privilege THEN
    NULL; -- expected (marker P0002 escapes)
  END;
END $$;
RESET ROLE;

-- ── 8. blog_agent_keys isolation ───────────────────────────────────────────
SELECT tests.assert(
  has_table_privilege('anon', 'public.blog_agent_keys', 'SELECT') = false
  AND has_table_privilege('authenticated', 'public.blog_agent_keys', 'SELECT') = false,
  '0017: anon/authenticated have NO access to blog_agent_keys');

SET ROLE service_role;
INSERT INTO public.blog_agent_keys (agent_name, key_hash) VALUES ('test-agent-blog', 'abcdef123456');
SELECT tests.assert(
  (SELECT count(*) FROM public.blog_agent_keys WHERE agent_name = 'test-agent-blog') = 1,
  '0017: service_role can manage blog_agent_keys');
RESET ROLE;

-- ── Cleanup test rows ──────────────────────────────────────────────────────
SET ROLE service_role;
DELETE FROM public.blog_posts WHERE slug IN
  ('valid-slug-blog','publish-ok-blog','pub-blog','draft-blog','held-blog');
DELETE FROM public.blog_agent_keys WHERE agent_name = 'test-agent-blog';
DELETE FROM public.blog_admins WHERE user_id = '11111111-1111-1111-1111-111111111111';
DELETE FROM auth.users WHERE id = '11111111-1111-1111-1111-111111111111';
DELETE FROM storage.objects WHERE bucket_id = 'blog-images' AND name = 'test-img.png';
RESET ROLE;
