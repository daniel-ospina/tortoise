-- ============================================================================
-- SQL-level verification for migration 20261023000002 (the #1383 attribution
-- backfill, repaired FORWARD as ONE statement).
--
-- Run by the PGlite harness, which applies EVERY migration from disk and then
-- every suite named in its EXPLICIT list:
--   npm --prefix supabase/tests/pglite run validate
--
-- WHAT THIS COVERS THAT THE 20261021000001 SUITE CANNOT: that suite proves the
-- ORIGINAL migration's backfill ran. It cannot prove the REPAIR carries it —
-- on this database 20261021000001 already backfilled everything, so a
-- 20261023000002 whose UPDATEs were deleted would print nothing. The harness
-- therefore seeds the FOUR rows below IMMEDIATELY BEFORE this migration is
-- applied (validate.mjs, the #4216/#3036/#3027 precedent), so each assertion
-- here reads what THIS migration did and reddens if the matching statement is
-- deleted:
--   * `1383b-preseed-anon`       — author = 'Tortoise team' (the retired
--                                  default) + explicit updated_at + audit
--                                  columns  → proves pass 1 ran, and that it
--                                  ran with the trigger DISABLED;
--   * `1383b-preseed-null-image` — author = 'Daniel Ospina', image NULL, own
--                                  updated_at → proves pass 2 ran (pass 1's
--                                  guard cannot touch it);
--   * `1383b-preseed-named`      — a deliberately different author WITH an
--                                  image → covers the AUTHOR guard of both
--                                  passes (pass 1's `author = 'Tortoise team'`
--                                  and pass 2's `author = 'Daniel Ospina'`).
--                                  It CANNOT cover pass 2's image clause: its
--                                  author is not 'Daniel Ospina', so the author
--                                  conjunct excludes it however the image
--                                  clause is written.
--   * `1383b-preseed-custom-image` — the SHIPPED author WITH a deliberately
--                                  different image → covers pass 2's `AND
--                                  author_image_url IS NULL` conjunct, and
--                                  nothing else: only that clause keeps this
--                                  row's avatar from being overwritten.
--
-- The ATOMICITY claim itself is not assertable from SQL (a migration's text is
-- history by the time a suite runs). validate.mjs proves it with TWO different
-- drills, and they are not interchangeable:
--   * drill A, the PREFIX drill — replays this file ONE TOP-LEVEL STATEMENT AT A
--     TIME and requires the trigger still ENABLED after EVERY prefix. The
--     every-prefix form is the point: on the four-statement shape the FIRST
--     statement is an `ADD COLUMN`, so a first-statement-only check proves
--     nothing;
--   * drill B, the FORWARD-REPAIR drill — constructs the damaged state (trigger
--     disabled, rows never backfilled) and executes the WHOLE file over it.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;

-- ── 1. Pass 1 — the retired anonymous default is gone ──────────────────────
SELECT tests.assert(
  (SELECT author FROM public.blog_posts WHERE slug = '1383b-preseed-anon') = 'Daniel Ospina',
  '1383b: pass 1 replaced the retired ''Tortoise team'' author (deleting the UPDATE reddens here)');

SELECT tests.assert(
  (SELECT author_image_url FROM public.blog_posts WHERE slug = '1383b-preseed-anon')
    = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg',
  '1383b: pass 1 set the author image URL');

SELECT tests.assert(
  (SELECT updated_at FROM public.blog_posts WHERE slug = '1383b-preseed-anon')
    = '2026-03-03 00:00:00+00'::timestamptz,
  '1383b: pass 1 did NOT bump updated_at — the trigger was disabled across it (deleting the DISABLE reddens here)');

SELECT tests.assert(
  (SELECT created_by FROM public.blog_posts WHERE slug = '1383b-preseed-anon') = 'agent-seeded'
  AND (SELECT published_by FROM public.blog_posts WHERE slug = '1383b-preseed-anon') IS NULL
  AND (SELECT reviewed_by FROM public.blog_posts WHERE slug = '1383b-preseed-anon') = 'owner-seeded',
  '1383b: the backfill left the audit columns (created_by/published_by/reviewed_by) untouched');

-- ── 2. Pass 2 — an already-named post gains the avatar it never had ────────
SELECT tests.assert(
  (SELECT author_image_url FROM public.blog_posts WHERE slug = '1383b-preseed-null-image')
    = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg',
  '1383b: pass 2 gave the avatar to a post that already carried the byline (deleting the second UPDATE reddens here)');

SELECT tests.assert(
  (SELECT updated_at FROM public.blog_posts WHERE slug = '1383b-preseed-null-image')
    = '2026-04-04 00:00:00+00'::timestamptz,
  '1383b: ...and pass 2 did not bump updated_at either');

-- ── 3. Neither pass is over-broad — clause by clause ────────────────────────
SELECT tests.assert(
  (SELECT author FROM public.blog_posts WHERE slug = '1383b-preseed-named') = 'Someone Else'
  AND (SELECT author_image_url FROM public.blog_posts WHERE slug = '1383b-preseed-named')
    = 'https://example.com/someone-else.jpg',
  '1383b: a deliberately different author WITH an image is preserved — covers the AUTHOR guard of both passes');

-- The row above cannot speak for pass 2's IMAGE clause: its author is not
-- 'Daniel Ospina', so the author conjunct excludes it whatever the image clause
-- says. This row carries the SHIPPED author and a CUSTOM avatar, so
-- `AND author_image_url IS NULL` is the only clause keeping it out — deleting
-- that conjunct overwrites its avatar with the shipped one and reddens HERE.
SELECT tests.assert(
  (SELECT author_image_url FROM public.blog_posts WHERE slug = '1383b-preseed-custom-image')
    = 'https://example.com/custom.jpg',
  '1383b: a post by the shipped author with a DELIBERATELY different avatar keeps it (deleting pass 2''s `AND author_image_url IS NULL` reddens here)');

SELECT tests.assert(
  (SELECT updated_at FROM public.blog_posts WHERE slug = '1383b-preseed-custom-image')
    = '2026-06-06 00:00:00+00'::timestamptz,
  '1383b: ...and no pass bumped that row''s updated_at');

-- ── 4. The trigger ends ENABLED ───────────────────────────────────────────
-- `tgenabled = 'O'` is "enabled, fires on origin". Deleting the ENABLE
-- TRIGGER leaves 'D' here: updated_at silently stops moving for every blog
-- post from that deploy forward, so the article page's "Updated …" line and
-- the 30-minute no-cache window freeze forever with no error anywhere.
SELECT tests.assert(
  (SELECT tgenabled FROM pg_trigger
    WHERE tgname = 'blog_posts_set_updated_at'
      AND tgrelid = 'public.blog_posts'::regclass) = 'O',
  '1383b: blog_posts_set_updated_at is ENABLED after this migration (deleting the ENABLE TRIGGER reddens here)');

-- ...and it really does fire afterwards: an UPDATE advances updated_at, so the
-- frozen timestamps above prove the DISABLE ran rather than an inert trigger.
--
-- The margin is the row's OWN prior value, re-baselined from the database
-- clock a moment ago — NOT a literal date. Comparing against the seeded
-- literal made this assertion depend on the host clock being later than the
-- seed (`2026-01-01`): a future-dated seed or a replayed fixture reds a
-- perfectly healthy trigger, and the seed and this literal can drift apart.
-- Parking the trigger to re-baseline makes "advanced" mean only what it says:
-- an inert (or still-disabled) trigger leaves the baseline exactly in place and
-- reds here, whatever the clock reads.
ALTER TABLE public.blog_posts DISABLE TRIGGER blog_posts_set_updated_at;
UPDATE public.blog_posts
   SET updated_at = now() - interval '1 hour'
 WHERE slug = '1383b-preseed-named';
ALTER TABLE public.blog_posts ENABLE TRIGGER blog_posts_set_updated_at;

UPDATE public.blog_posts SET title = title WHERE slug = '1383b-preseed-named';

SELECT tests.assert(
  (SELECT updated_at FROM public.blog_posts WHERE slug = '1383b-preseed-named')
    > now() - interval '1 hour',
  '1383b: after this migration the trigger DOES bump updated_at on an UPDATE (so the frozen timestamps above prove the disable, not an inert trigger)');

-- Cleanup — rows use the '1383b-' prefix so a rerun never collides.
DELETE FROM public.blog_posts WHERE slug LIKE '1383b-%';
