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
-- An attribution fix is NOT an editorial edit: bumping updated_at would print
-- "Updated <today>" beside every backfilled byline (the article page renders it
-- whenever the formatted dates differ) and open the 30-minute no-cache window
-- (`isRecentlyEdited`) on each of them.
--
-- The updated_at trigger is a BEFORE UPDATE trigger, so no statement can
-- preserve updated_at itself — it MUST be disabled across the updates, which
-- makes the DISABLE→ENABLE window load-bearing.
--
-- ⛔ That window must not be able to outlive a failure. A bare
-- DISABLE … ENABLE pair is only atomic when the whole file is sent as ONE
-- simple query (`supabase db push`, the Management API's single-query endpoint,
-- and the PGlite harness's `db.exec` all do; a bare `psql -f file.sql` does NOT
-- — it autocommits per statement). A truncated batch persisting its leading
-- statements has been measured in the sibling DMeer repo (DMeer
-- `supabase/migrations/README.md`, the `20260925000002_revoke_businesses_insert_921.sql`
-- row). Left disabled, `blog_posts_set_updated_at` freezes JSON-LD dateModified
-- and permanently defeats the 30-minute no-cache window — silently.
--
-- So the backfill does not depend on the apply path: it is ONE `DO` statement,
-- there is no boundary a partial apply can stop at, and it ends by re-reading
-- pg_trigger.tgenabled and RAISING if it is anything but 'O' — fail-closed, and
-- checked AFTER the enable.
--
-- Apply via the CLI / the workflow / `psql --single-transaction -v ON_ERROR_STOP=1`,
-- never a bare `psql -f`. Do NOT add BEGIN;/COMMIT; inside: the CLI has already
-- opened a transaction, so a nested BEGIN only warns and an inner COMMIT would
-- end the CLI's transaction early. `supabase/tests/20261021000001_blog_author_identity.sql`
-- independently asserts the trigger is ENABLED (`tgenabled = 'O'`) afterwards.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  trigger_state "char";
BEGIN
  EXECUTE 'ALTER TABLE public.blog_posts DISABLE TRIGGER blog_posts_set_updated_at';

  UPDATE public.blog_posts
     SET author           = 'Daniel Ospina',
         author_image_url = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg'
   WHERE author = 'Tortoise team';

  -- Second pass: posts that ALREADY carried the byline but never had an avatar
  -- (they were written after the name was set and before the column existed, so
  -- the guard above skips them and they would render name-only — a byline with
  -- an avatar on 4 posts and none on 2). Guarded on `author = 'Daniel Ospina'
  -- AND author_image_url IS NULL`, so a post with a deliberately different
  -- author, or one whose image is set, is still untouched.
  --
  -- Stated behaviour, not silence: the admin CAN blank an avatar
  -- (`author_image_url.trim() || null`), so a NULL here is a reachable state.
  -- Re-applying this migration onto a live database would therefore RE-FILL a
  -- deliberately blanked avatar for these rows (verified: clear → re-apply →
  -- URL returns). Accepted, because migrations are applied ONCE — the drill
  -- harness re-applies onto a fresh database with no edited content — and
  -- because the alternative (guessing which NULLs are deliberate) is worse. If
  -- this file is ever re-run against live data, that re-fill is what it does.
  UPDATE public.blog_posts
     SET author_image_url = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg'
   WHERE author = 'Daniel Ospina'
     AND author_image_url IS NULL;

  EXECUTE 'ALTER TABLE public.blog_posts ENABLE TRIGGER blog_posts_set_updated_at';

  SELECT tgenabled INTO trigger_state
    FROM pg_trigger
   WHERE tgname = 'blog_posts_set_updated_at'
     AND tgrelid = 'public.blog_posts'::regclass;

  IF trigger_state IS DISTINCT FROM 'O' THEN
    RAISE EXCEPTION 'blog_posts_set_updated_at is not enabled (tgenabled=%) — refusing to commit a backfill that would silence updated_at', trigger_state;
  END IF;
END $$;
