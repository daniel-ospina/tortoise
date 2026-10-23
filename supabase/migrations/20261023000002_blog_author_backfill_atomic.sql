-- ============================================================================
-- Migration 20261023000002: the blog attribution backfill, as ONE statement
--
-- 20261021000001_blog_author_identity.sql (#9578, issue #1383) shipped its
-- backfill as four TOP-LEVEL statements:
--
--     ALTER TABLE public.blog_posts DISABLE TRIGGER blog_posts_set_updated_at;
--     UPDATE ... WHERE author = 'Tortoise team';
--     UPDATE ... WHERE author = 'Daniel Ospina' AND author_image_url IS NULL;
--     ALTER TABLE public.blog_posts ENABLE TRIGGER blog_posts_set_updated_at;
--
-- The DISABLE…ENABLE pair is only in ONE transaction when the whole FILE is
-- sent as ONE simple query — `supabase db push`, the Management API's
-- single-query endpoint and the PGlite harness's `db.exec` all do; a bare
-- `psql -f file.sql` does NOT, it autocommits PER STATEMENT. So a failure
-- between them (a lock timeout, a dropped connection, an operator Ctrl-C)
-- commits the DISABLE and never reaches the ENABLE, leaving
-- `blog_posts_set_updated_at` off for every blog write afterwards, silently:
-- `updated_at` stops moving (JSON-LD dateModified freezes, the article page
-- never re-prints "Updated …") and the 30-minute no-cache window
-- (`isRecentlyEdited`) never opens again. Nothing reports it.
--
-- ⛔ That file is IMMUTABLE HISTORY and cannot be repaired in place:
--   * `migration-append-only` (#1095/#1001) rejects an `M` of a merged
--     migration outright — an applied-migration edit is how repo and prod
--     diverged before;
--   * and it would not reach the database anyway. Its version is recorded in
--     `supabase_migrations.schema_migrations` (the out-of-band apply procedure
--     in `supabase/README.md` REQUIRES recording it, or the drift gate blocks
--     every later app deploy), so `db push` will never re-read an edited copy.
--
-- So the repair is FORWARD, and it converges ANY environment on one end state:
-- one that applied the old file cleanly, and one whose truncated apply
-- committed the DISABLE (and possibly neither UPDATE) and stopped:
--
--   * the trigger is disabled only INSIDE this block (a BEFORE UPDATE trigger
--     cannot be preserved across an UPDATE any other way),
--   * both backfill passes run — idempotent, so on a converged database they
--     match nothing,
--   * the trigger is ENABLED again,
--   * and the block is the ONLY top-level statement in this file, so there is
--     no boundary a per-statement apply can stop at. That shape is ENFORCED,
--     not just asserted here: `supabase/tests/pglite/validate.mjs` replays this
--     file ONE TOP-LEVEL STATEMENT AT A TIME — what a per-statement autocommit
--     apply does — and requires the trigger to still be ENABLED after EVERY
--     prefix. It is the every-prefix form deliberately: a stop at the FIRST
--     statement of the four-statement shape above proves nothing, because that
--     statement is an `ADD COLUMN` and leaves the trigger on. It reddens on the
--     four-statement form at the prefix that commits the DISABLE.
--     (validate.mjs drill A = this prefix replay; drill B = the forward repair,
--     which executes the WHOLE file over a hand-built damaged state.)
--
-- ⛔ The mechanism that makes this safe is ONE-STATEMENT ATOMICITY, not an
-- assertion. There is deliberately no post-condition re-read of
-- `pg_trigger.tgenabled` at the end: `ENABLE TRIGGER` unconditionally writes
-- 'O' on a row this block has just proved exists, the two ALTERs hold ACCESS
-- EXCLUSIVE until commit so nothing can interleave, and an assertion there
-- could never fail. The only check that CAN fail is the PRE-condition below
-- (a missing trigger row), and it is stated as exactly that.
--
-- Idempotent / the one accepted data effect: pass 1 (`author = 'Tortoise
-- team'`) matches nothing on a converged database. Pass 2 (`author = 'Daniel
-- Ospina' AND author_image_url IS NULL`) RE-FILLS a deliberately blanked
-- avatar — the admin CAN blank one (`author_image_url.trim() || null`), so a
-- NULL is a reachable state. Accepted, because guessing which NULLs are
-- deliberate is worse and migrations are applied once; a live re-run of this
-- file will re-fill those rows.
--
-- The ALTERs are `EXECUTE`d: utility statements have no static PL/pgSQL form.
-- Do NOT add BEGIN;/COMMIT; here — the CLI has already opened a transaction, a
-- nested BEGIN only warns, and an inner COMMIT would end the CLI's transaction
-- early.
-- ============================================================================
DO $$
DECLARE
  trigger_state "char";
BEGIN
  -- Fail-closed PRE-condition. `SELECT INTO` leaves this NULL when no row
  -- matches, and the ALTERs below would otherwise raise a generic
  -- `undefined_object` that reads like a typo rather than a missing trigger.
  SELECT tgenabled INTO trigger_state
    FROM pg_trigger
   WHERE tgname = 'blog_posts_set_updated_at'
     AND tgrelid = 'public.blog_posts'::regclass;

  IF trigger_state IS NULL THEN
    RAISE EXCEPTION
      'blog_posts_set_updated_at does not exist on public.blog_posts — refusing to run an attribution backfill that could not leave updated_at enabled';
  END IF;

  EXECUTE 'ALTER TABLE public.blog_posts DISABLE TRIGGER blog_posts_set_updated_at';

  -- Pass 1: rows still carrying the retired anonymous default.
  UPDATE public.blog_posts
     SET author           = 'Daniel Ospina',
         author_image_url = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg'
   WHERE author = 'Tortoise team';

  -- Pass 2: posts that ALREADY carried the byline but never had an avatar
  -- (written after the name was set and before the column existed, so pass 1's
  -- guard skips them and they would render name-only). Guarded on
  -- `author = 'Daniel Ospina' AND author_image_url IS NULL`, so a post with a
  -- deliberately different author, or one whose image is set, is untouched.
  UPDATE public.blog_posts
     SET author_image_url = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg'
   WHERE author = 'Daniel Ospina'
     AND author_image_url IS NULL;

  EXECUTE 'ALTER TABLE public.blog_posts ENABLE TRIGGER blog_posts_set_updated_at';
END $$;
