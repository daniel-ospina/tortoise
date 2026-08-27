-- ============================================================================
-- Migration 20260827000001: Blog CMS — blog_posts, blog_agent_keys, blog-admins,
-- blog-images bucket
-- Epic: docs/epics/2026-08-27-tortoise-blog-cms/03-plan.md §4
-- Issue: #1793
--
-- SITE-WIDE marketing content (NOT tenant-scoped — the blog is the public
-- surface of tortoise.premiselabs.co). Access model:
--   anon/authenticated   → SELECT published AND NOT hold_for_review only
--   authenticated admin  → SELECT ALL + full CRUD (blog_admins allowlist)
--   service_role         → ALL (agent publish API + seed/ops scripts)
--
-- NOTE: named 20260827000001 (not 0017) — the timestamp prefix sorts after
-- the 2026 batch for fresh-DB applies (repo precedent renamed off numeric
-- prefixes after a fresh-DB ordering break).
--
-- Lifecycle (plan W4): draft → published → archived (terminal);
-- published → draft (unpublish / request-changes, clears review state but
-- preserves review_note on request-changes).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- blog_posts
-- ---------------------------------------------------------------------------
CREATE TABLE public.blog_posts (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug               text NOT NULL UNIQUE
                     CHECK (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' AND char_length(slug) <= 100),
  title              text NOT NULL CHECK (char_length(title) <= 200),
  body               text NOT NULL DEFAULT '',          -- markdown (canonical)
  excerpt            text CHECK (char_length(excerpt) <= 300),
  cover_image_url    text,
  tags               text[] NOT NULL DEFAULT '{}',
  author             text NOT NULL DEFAULT 'Tortoise team',
  status             text NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft', 'published', 'archived')),
  meta_title         text,
  meta_description   text,
  published_at       timestamptz,
  published_by       text,                              -- agent name or user id (audit)
  created_by         text,                              -- scopes agent PATCH rights
  reviewed_by        text,
  reviewed_at        timestamptz,
  review_note        text,                              -- request-changes note from owner
  hold_for_review    boolean NOT NULL DEFAULT false,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Indexes (plan §4)
CREATE INDEX idx_blog_posts_published
  ON public.blog_posts (published_at DESC)
  WHERE status = 'published' AND hold_for_review = false;
CREATE INDEX idx_blog_posts_tags ON public.blog_posts USING GIN (tags);
CREATE INDEX idx_blog_posts_created_by ON public.blog_posts (created_by);

-- ---------------------------------------------------------------------------
-- blog_agent_keys — per-agent credentials (sha256 hashes, never plaintext)
-- ---------------------------------------------------------------------------
CREATE TABLE public.blog_agent_keys (
  agent_name  text PRIMARY KEY,
  key_hash    text NOT NULL,                            -- sha256 hex of the raw key
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Keys are read ONLY by the agent API Function (service_role) — no client role
-- ever sees them.
REVOKE ALL ON public.blog_agent_keys FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- blog_admins — explicit allowlist for the SITE-WIDE blog admin surface.
-- Owner/user ids are seeded by the ops seed script (service_role) — team
-- ownership is NOT sufficient (team creation is open self-serve, so "any
-- team owner" would make every registered user a blog admin).
-- ---------------------------------------------------------------------------
CREATE TABLE public.blog_admins (
  user_id    uuid PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON public.blog_admins FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- Functions / triggers
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.blog_set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END $$;

-- Publish guard: status='published' requires audit fields (plan W1).
CREATE OR REPLACE FUNCTION public.blog_posts_publish_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'published' AND (NEW.published_at IS NULL OR NEW.published_by IS NULL) THEN
    RAISE EXCEPTION 'blog_posts: published requires published_at and published_by';
  END IF;
  RETURN NEW;
END $$;

-- Unpublish/request-changes: published→draft clears review state. review_note
-- is LEFT ALONE: a plain unpublish sends NULL (stays NULL), request-changes
-- sends a note (survives) — plan §4 trigger contract.
CREATE OR REPLACE FUNCTION public.blog_posts_unpublish_review_state()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'published' AND NEW.status = 'draft' THEN
    NEW.reviewed_by = NULL;
    NEW.reviewed_at = NULL;
  END IF;
  RETURN NEW;
END $$;

-- Archive is terminal (plan W4).
CREATE OR REPLACE FUNCTION public.blog_posts_archive_terminal()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'archived' AND NEW.status IS DISTINCT FROM 'archived' THEN
    RAISE EXCEPTION 'blog_posts: archived is terminal';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER blog_posts_set_updated_at
  BEFORE UPDATE ON public.blog_posts
  FOR EACH ROW EXECUTE FUNCTION public.blog_set_updated_at();
CREATE TRIGGER blog_posts_publish_guard
  BEFORE INSERT OR UPDATE ON public.blog_posts
  FOR EACH ROW EXECUTE FUNCTION public.blog_posts_publish_guard();
CREATE TRIGGER blog_posts_unpublish_review_state
  BEFORE UPDATE ON public.blog_posts
  FOR EACH ROW EXECUTE FUNCTION public.blog_posts_unpublish_review_state();
CREATE TRIGGER blog_posts_archive_terminal
  BEFORE UPDATE ON public.blog_posts
  FOR EACH ROW EXECUTE FUNCTION public.blog_posts_archive_terminal();

-- ---------------------------------------------------------------------------
-- is_admin() — blog admin gate: membership in the blog_admins allowlist.
-- (Team-ownership was rejected in review: open self-serve team creation
-- would grant every registered user full blog CRUD on the public surface.)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.is_admin()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.blog_admins WHERE user_id = auth.uid()
  );
$$;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
ALTER TABLE public.blog_posts ENABLE ROW LEVEL SECURITY;

CREATE POLICY blog_posts_anon_read_published ON public.blog_posts
  FOR SELECT TO anon
  USING (status = 'published' AND hold_for_review = false);

CREATE POLICY blog_posts_auth_read_published ON public.blog_posts
  FOR SELECT TO authenticated
  USING (status = 'published' AND hold_for_review = false);

CREATE POLICY blog_posts_admin_all ON public.blog_posts
  FOR ALL TO authenticated
  USING (public.is_admin())
  WITH CHECK (public.is_admin());

CREATE POLICY blog_posts_service_all ON public.blog_posts
  FOR ALL TO service_role
  USING (true)
  WITH CHECK (true);

ALTER TABLE public.blog_agent_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY blog_agent_keys_service_all ON public.blog_agent_keys
  FOR ALL TO service_role
  USING (true)
  WITH CHECK (true);

ALTER TABLE public.blog_admins ENABLE ROW LEVEL SECURITY;

CREATE POLICY blog_admins_service_all ON public.blog_admins
  FOR ALL TO service_role
  USING (true)
  WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- blog-images storage bucket (public CDN read; admin + service write)
-- ---------------------------------------------------------------------------
INSERT INTO storage.buckets (id, name, public)
VALUES ('blog-images', 'blog-images', true)
ON CONFLICT (id) DO NOTHING;

CREATE POLICY "blog_images_public_read" ON storage.objects
  FOR SELECT
  USING (bucket_id = 'blog-images');

CREATE POLICY "blog_images_admin_insert" ON storage.objects
  FOR INSERT
  WITH CHECK (bucket_id = 'blog-images' AND public.is_admin());

CREATE POLICY "blog_images_admin_update" ON storage.objects
  FOR UPDATE
  USING (bucket_id = 'blog-images' AND public.is_admin())
  WITH CHECK (bucket_id = 'blog-images' AND public.is_admin());

CREATE POLICY "blog_images_admin_delete" ON storage.objects
  FOR DELETE
  USING (bucket_id = 'blog-images' AND public.is_admin());

CREATE POLICY "blog_images_service_all" ON storage.objects
  FOR ALL TO service_role
  USING (bucket_id = 'blog-images')
  WITH CHECK (bucket_id = 'blog-images');

-- ---------------------------------------------------------------------------
-- Comments
-- ---------------------------------------------------------------------------
COMMENT ON TABLE public.blog_posts IS
  'Blog posts for tortoise.premiselabs.co — markdown body, SEO fields, review state. Epic docs/epics/2026-08-27-tortoise-blog-cms.';
COMMENT ON COLUMN public.blog_posts.slug IS 'URL slug — immutable after create; ^[a-z0-9]+(?:-[a-z0-9]+)*$';
COMMENT ON COLUMN public.blog_posts.status IS 'draft | published | archived (terminal). published requires published_at + published_by (trigger).';
COMMENT ON COLUMN public.blog_posts.hold_for_review IS 'When true the post is excluded from ALL public surfaces until an admin clears it.';
COMMENT ON COLUMN public.blog_posts.published_by IS 'Audit: agent name or user id that published (or direct-published).';
COMMENT ON COLUMN public.blog_posts.created_by IS 'Audit + agent edit scope: PATCH allowed only when created_by = calling agent_name.';
COMMENT ON TABLE public.blog_agent_keys IS 'Per-agent publish credentials — sha256 hashes only. Issued/rotated/revoked via seed/ops script (service_role).';
COMMENT ON TABLE public.blog_admins IS 'Blog admin allowlist — user ids seeded via ops script. Team ownership is NOT sufficient (open self-serve team creation).';
COMMENT ON FUNCTION public.is_admin() IS 'Blog admin gate: user_id present in blog_admins allowlist.';
