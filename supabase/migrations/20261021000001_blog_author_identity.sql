-- ============================================================================
-- Migration 20261021000001: Blog author identity — issue #1383
--
-- `author` (text NOT NULL) has always existed, but the editor never exposed it,
-- so every post shipped carrying the table DEFAULT — the anonymous
-- 'Tortoise team'. This adds the missing half of an author identity (the profile
-- image) and retires the anonymous default for a real one.
--
-- The applied 20260827000001_blog_cms.sql is NOT edited (migration files are
-- immutable history; a fresh DB applies this one after it).
--
--   * author_image_url text NULL — NULL is a first-class value, not a failure:
--     the article byline then renders the NAME ALONE (no <img>, no empty
--     circle). An `''` default would be strictly worse — '' is not nullish, so
--     a null-check would pass it through as <img src="">, a broken image.
--   * author's DEFAULT becomes 'Daniel Ospina'. NOT NULL is unchanged.
--   * Backfill of the rows still carrying the retired anonymous default.
--     Guarded by `WHERE author = 'Tortoise team'`, so it is idempotent and
--     cannot overwrite a deliberately-set author. created_by / published_by /
--     reviewed_by are AUDIT columns and are not touched.
--
-- ⛔ One author name, FOUR places — they must agree, because a DB default that
-- disagrees with the renderer's fallback prints two different names for the
-- same post depending on which path wrote it:
--     1. this DEFAULT,
--     2. `DEFAULT_AUTHOR` in website/functions/blog/_lib.ts (SSR renderer),
--     3. the agent API's insert default (website/functions/blog/api/posts/[[path]].ts),
--     4. `DEFAULT_AUTHOR` in website/apps/blog-admin/src/lib/blog-api.ts.
--     The old 'Tortoise team' literal appears in none of them after this change.
--
-- Grants: this project grants at TABLE level (the 20260827000001 policies rely
-- on the default anon/authenticated SELECT on public tables), so a new column is
-- covered by the existing grant. Nothing to add here — unlike a per-column
-- default-deny project, where a new column is invisible to `anon` until granted.
-- ============================================================================

ALTER TABLE public.blog_posts
  ADD COLUMN IF NOT EXISTS author_image_url text;

COMMENT ON COLUMN public.blog_posts.author_image_url IS
  'Author profile image (absolute https URL) rendered as the byline avatar. NULL renders the byline name alone — never a broken <img>, never an empty circle.';

ALTER TABLE public.blog_posts
  ALTER COLUMN author SET DEFAULT 'Daniel Ospina';

COMMENT ON COLUMN public.blog_posts.author IS
  'Display name for the article byline (NOT a user id — created_by/published_by carry identity for audit).';

-- ---------------------------------------------------------------------------
-- Backfill: attribution only.
--
-- The updated_at trigger is disabled for the duration and re-enabled at the end
-- of this section. An attribution fix is NOT an editorial edit: bumping
-- updated_at would print "Updated <today>" beside every backfilled byline (the
-- article page renders it whenever updated_at != published_at) and open the
-- 30-minute no-cache window (`isRecentlyEdited`) on each of them.
--
-- ⛔ ATOMICITY HERE IS A PRECONDITION OF THE APPLY PATH, NOT A PROPERTY OF THIS
-- FILE. The DISABLE … ENABLE pair is only in ONE transaction when the whole file
-- is sent as ONE simple query — `supabase db push`, the Management API's
-- single-query endpoint, and the PGlite harness's `db.exec` all do (each wraps
-- the batch in one implicit transaction). A bare `psql -f file.sql` does NOT: it
-- autocommits PER STATEMENT, so if an UPDATE fails (a lock timeout, a dropped
-- connection, an operator Ctrl-C) the DISABLE is already committed and the
-- ENABLE is never reached — leaving blog_posts_set_updated_at off for every blog
-- write afterwards, silently ("Updated …" stops moving, the 30-minute no-cache
-- window never opens again, and nothing reports it).
--
-- Apply this file with `psql --single-transaction -v ON_ERROR_STOP=1 …`, or via
-- the CLI / the workflow — never a bare `psql -f`. Do NOT add BEGIN;/COMMIT;
-- instead: the CLI has already opened a transaction, so a nested BEGIN only
-- warns, and the inner COMMIT would END the CLI's transaction early.
-- `supabase/tests/20261021000001_blog_author_identity.sql` asserts the trigger is
-- ENABLED (`pg_trigger.tgenabled = 'O'`) after this migration, so removing the
-- ENABLE below reddens in the harness.
-- ---------------------------------------------------------------------------
ALTER TABLE public.blog_posts DISABLE TRIGGER blog_posts_set_updated_at;

UPDATE public.blog_posts
   SET author           = 'Daniel Ospina',
       author_image_url = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg'
 WHERE author = 'Tortoise team';

-- Second pass: posts that ALREADY carried the byline but never had an avatar
-- (they were written after the name was set and before the column existed, so
-- the guard above skips them and they would render name-only — a byline with
-- an avatar on 4 posts and none on 2). Guarded on `author = 'Daniel Ospina'
-- AND author_image_url IS NULL`, so a post with a deliberately different
-- author, or one whose image is set, is still untouched. Idempotent: a re-run
-- matches nothing.
UPDATE public.blog_posts
   SET author_image_url = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg'
 WHERE author = 'Daniel Ospina'
   AND author_image_url IS NULL;

ALTER TABLE public.blog_posts ENABLE TRIGGER blog_posts_set_updated_at;
