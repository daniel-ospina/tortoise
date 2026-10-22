-- ============================================================================
-- SQL-level verification for migration 20261021000001 (issue #1383 — blog
-- author identity: `author_image_url` + a real author default).
--
-- Run by the PGlite harness, which applies EVERY migration from disk and then
-- every suite named in its EXPLICIT list:
--   npm --prefix supabase/tests/pglite run validate
--
-- HOW THE ONE-TIME BACKFILL IS TESTED AT ALL: a migration's deploy-time UPDATE
-- is already history by the time a suite runs, so asserting "the backfill works"
-- afterwards would need the migration's own text replayed — which proves nothing
-- about what the DEPLOY did. The harness therefore seeds three rows IMMEDIATELY
-- BEFORE this migration is applied (validate.mjs, the #4216 precedent):
--   * `1383-preseed-anon`            — author = 'Tortoise team' (the RETIRED
--                                      default), with an explicit updated_at and
--                                      audit columns;
--   * `1383-preseed-named`           — author = 'Someone Else' (deliberately set);
--   * `1383-preseed-named-null-image` — author = 'Daniel Ospina' with NO avatar,
--                                      which ONLY the backfill's second pass can
--                                      fill (pass 1's guard is 'Tortoise team').
-- The assertions below then read what the migration actually did to them, so
-- deleting EITHER UPDATE reddens — the two passes have separate coverage.
--
-- The trigger state is asserted too (`pg_trigger.tgenabled`): deleting the
-- re-enable would leave updated_at frozen for every blog post from that deploy
-- forward, and no other assertion here can see it — a backfilled row whose
-- updated_at did NOT move looks identical whether the trigger was re-enabled or
-- left off for good.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;

-- ── 1. Schema ──────────────────────────────────────────────────────────────
SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public' AND table_name = 'blog_posts'
            AND column_name = 'author_image_url'),
  '1383: blog_posts.author_image_url exists');

SELECT tests.assert(
  (SELECT is_nullable FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'blog_posts'
      AND column_name = 'author_image_url') = 'YES',
  '1383: author_image_url is NULLABLE — NULL is the "render the name alone" case');

-- ⛔ The column must have NO default. An `''` default would be worse than NULL:
-- '' is not nullish, so the renderer's null-guard would pass it through as
-- <img src=""> — exactly the broken image the null path exists to prevent.
SELECT tests.assert(
  (SELECT column_default FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'blog_posts'
      AND column_name = 'author_image_url') IS NULL,
  '1383: author_image_url has NO default (not an empty string — NULL is the absent value)');

SELECT tests.assert(
  (SELECT column_default FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'blog_posts'
      AND column_name = 'author') = '''Daniel Ospina''::text',
  '1383: author default is ''Daniel Ospina'' (the anonymous default is retired)');

SELECT tests.assert(
  (SELECT is_nullable FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'blog_posts'
      AND column_name = 'author') = 'NO',
  '1383: author is still NOT NULL (default change only — no semantic change)');

-- ── 2. The backfill, as the deploy actually ran it ─────────────────────────
SELECT tests.assert(
  (SELECT author FROM public.blog_posts WHERE slug = '1383-preseed-anon') = 'Daniel Ospina',
  '1383: the backfill replaced the retired ''Tortoise team'' author');

SELECT tests.assert(
  (SELECT author_image_url FROM public.blog_posts WHERE slug = '1383-preseed-anon')
    = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg',
  '1383: the backfill set the author image URL');

SELECT tests.assert(
  (SELECT updated_at FROM public.blog_posts WHERE slug = '1383-preseed-anon')
    = '2026-01-01 00:00:00+00'::timestamptz,
  '1383: the backfill did NOT bump updated_at (trigger disabled — attribution is not an editorial edit)');

SELECT tests.assert(
  (SELECT created_by FROM public.blog_posts WHERE slug = '1383-preseed-anon') = 'agent-seeded'
  AND (SELECT published_by FROM public.blog_posts WHERE slug = '1383-preseed-anon') IS NULL
  AND (SELECT reviewed_by FROM public.blog_posts WHERE slug = '1383-preseed-anon') = 'owner-seeded',
  '1383: the backfill left the audit columns (created_by/published_by/reviewed_by) untouched');

SELECT tests.assert(
  (SELECT author FROM public.blog_posts WHERE slug = '1383-preseed-named') = 'Someone Else',
  '1383: the backfill is NOT over-broad — a deliberately-set author is preserved');

SELECT tests.assert(
  (SELECT author_image_url FROM public.blog_posts WHERE slug = '1383-preseed-named') IS NULL,
  '1383: ...and that row keeps its NULL image (the name-alone byline)');

-- Second pass — a row that ALREADY carried the real author but had no avatar.
-- Pass 1 cannot touch it (`author = 'Tortoise team'`), so this is the only
-- assertion that can red when the second UPDATE is deleted.
SELECT tests.assert(
  (SELECT author FROM public.blog_posts WHERE slug = '1383-preseed-named-null-image') = 'Daniel Ospina',
  '1383: the second pass leaves an already-correct author name alone');

SELECT tests.assert(
  (SELECT author_image_url FROM public.blog_posts WHERE slug = '1383-preseed-named-null-image')
    = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg',
  '1383: the second pass gives the avatar to a post that already carried the byline (name-only → avatar; deleting this UPDATE reddens)');

SELECT tests.assert(
  (SELECT updated_at FROM public.blog_posts WHERE slug = '1383-preseed-named-null-image')
    = '2026-02-02 00:00:00+00'::timestamptz,
  '1383: ...and the second pass did not bump updated_at either (the trigger stays off for both passes)');

-- ── 2b. The updated_at trigger is ENABLED again ────────────────────────────
-- `tgenabled = 'O'` is "enabled, fires on origin". Deleting the migration's
-- `ENABLE TRIGGER` leaves 'D' here: updated_at silently stops moving for every
-- blog post from that deploy forward, so the article page's "Updated …" line
-- and the 30-minute no-cache window (`isRecentlyEdited`) freeze forever with no
-- error anywhere. Nothing else in the harness can see that — `20260827000001`
-- only counts the trigger's EXISTENCE (a DROP, not a DISABLE), and a backfilled
-- row whose updated_at did not move looks the same either way.
SELECT tests.assert(
  (SELECT tgenabled FROM pg_trigger
    WHERE tgname = 'blog_posts_set_updated_at'
      AND tgrelid = 'public.blog_posts'::regclass) = 'O',
  '1383: blog_posts_set_updated_at is ENABLED again after the backfill (deleting the ENABLE TRIGGER reddens here)');

-- ...and it really is a BEFORE UPDATE trigger, which is what makes the
-- untouched-updated_at assertions above mean "disabled for the backfill" and
-- not "the trigger does nothing anyway".
UPDATE public.blog_posts SET title = title WHERE slug = '1383-preseed-named';

SELECT tests.assert(
  (SELECT updated_at FROM public.blog_posts WHERE slug = '1383-preseed-named')
    > '2026-01-01 00:00:00+00'::timestamptz,
  '1383: after the migration the trigger DOES bump updated_at on an UPDATE (so the frozen timestamps above prove the disable, not an inert trigger)');

-- ── 3. Behaviour of the new defaults on write ──────────────────────────────
INSERT INTO public.blog_posts (slug, title)
VALUES ('1383-default-anon', 'Default author');

SELECT tests.assert(
  (SELECT author FROM public.blog_posts WHERE slug = '1383-default-anon') = 'Daniel Ospina',
  '1383: an INSERT omitting author gets the real author, not ''Tortoise team''');

SELECT tests.assert(
  (SELECT author_image_url FROM public.blog_posts WHERE slug = '1383-default-anon') IS NULL,
  '1383: an INSERT omitting author_image_url stays NULL (no empty-string default)');

INSERT INTO public.blog_posts (slug, title, author, author_image_url)
VALUES ('1383-explicit', 'Explicit author', 'Someone Else', 'https://example.com/a.jpg');

SELECT tests.assert(
  (SELECT author FROM public.blog_posts WHERE slug = '1383-explicit') = 'Someone Else',
  '1383: the default is a DEFAULT, not a clamp — an explicit author survives');

SELECT tests.assert(
  (SELECT author_image_url FROM public.blog_posts WHERE slug = '1383-explicit') = 'https://example.com/a.jpg',
  '1383: an explicit author_image_url round-trips');

-- Cleanup — test rows use the '1383-' prefix so a rerun never collides.
DELETE FROM public.blog_posts WHERE slug LIKE '1383-%';
