/**
 * blog-api — typed functions over supabase-js for the Tortoise blog CMS.
 *
 * All reads/writes ride the USER's PKCE session (RLS: blog_posts admin_all
 * policy gates on is_admin() membership — migration 20260827000001).
 * No service-role keys client-side.
 *
 * Status model (plan W4): draft → published → archived (terminal);
 * published → draft (unpublish / request-changes clears review state).
 * Review queue (W2) = agent-drafts (status=draft) + unreviewed published
 * (status='published' AND reviewed_at IS NULL) + held (hold_for_review=true).
 */

import { supabase, SUPABASE_URL } from '@/lib/supabase';
import type { BlogPostRow, BlogPostInsert, BlogPostUpdate } from '@/lib/types';

export type { BlogPostRow };

export type PostStatus = BlogPostRow['status'];

export interface QueueItem {
  kind: 'agent-draft' | 'unreviewed-published' | 'held';
}

export function queueKind(post: BlogPostRow): QueueItem['kind'] | null {
  if (post.hold_for_review && post.status !== 'archived') return 'held';
  if (post.status === 'published' && !post.reviewed_at) return 'unreviewed-published';
  if (post.status === 'draft') return 'agent-draft';
  return null;
}

export const POST_SELECT = `
  id, slug, title, body, excerpt, cover_image_url, tags, author, status,
  meta_title, meta_description, published_at, published_by, created_by,
  reviewed_by, reviewed_at, review_note, hold_for_review, created_at, updated_at
`;

/** All posts for the admin surface (admin RLS allows SELECT ALL). */
export async function listPosts(): Promise<BlogPostRow[]> {
  const { data, error } = await supabase
    .from('blog_posts')
    .select(POST_SELECT)
    .order('updated_at', { ascending: false });
  if (error) throw error;
  return (data ?? []) as BlogPostRow[];
}

/** Review queue — the three item kinds from plan W2 (archived rows excluded — archive is terminal). */
export async function listQueue(): Promise<BlogPostRow[]> {
  const { data, error } = await supabase
    .from('blog_posts')
    .select(POST_SELECT)
    .or(`status.eq.draft,and(status.eq.published,reviewed_at.is.null),hold_for_review.eq.true`)
    .neq('status', 'archived')
    .order('updated_at', { ascending: false });
  if (error) throw error;
  return (data ?? []) as BlogPostRow[];
}

export async function getPost(id: string): Promise<BlogPostRow | null> {
  const { data, error } = await supabase
    .from('blog_posts')
    .select(POST_SELECT)
    .eq('id', id)
    .maybeSingle();
  if (error) throw error;
  return (data as BlogPostRow | null) ?? null;
}

export async function createPost(input: Partial<BlogPostRow>): Promise<BlogPostRow> {
  const record = sanitizeInsert(input);
  const { data, error } = await supabase
    .from('blog_posts')
    .insert(record)
    .select(POST_SELECT)
    .single();
  if (error) throw error;
  return data as BlogPostRow;
}

export async function updatePost(id: string, patch: Partial<BlogPostRow>): Promise<BlogPostRow> {
  const record = sanitizeUpdate(patch);
  const { data, error } = await supabase
    .from('blog_posts')
    .update(record)
    .eq('id', id)
    .select(POST_SELECT)
    .single();
  if (error) throw error;
  return data as BlogPostRow;
}

// ── Status transitions (plan W2/W4 semantics) ─────────────────────────────

export interface AuditIdentity {
  /** user id (owner) — written to published_by / reviewed_by */
  userId: string;
}

/**
 * Publish: status → published with audit fields. Republish semantics (W2):
 * a subsequent publish re-enters the review queue (reviewed_at=NULL) — never
 * silently pre-reviewed. hold_for_review is NOT cleared here (E2E-2's
 * "clear hold" is its own action).
 */
export async function publishPost(post: BlogPostRow, identity: AuditIdentity): Promise<BlogPostRow> {
  return updatePost(post.id, {
    status: 'published',
    published_at: new Date().toISOString(),
    published_by: identity.userId,
    reviewed_by: null,
    reviewed_at: null,
  });
}

/** Unpublish: status → draft, clears review state, drops stale review notes. */
export async function unpublishPost(post: BlogPostRow): Promise<BlogPostRow> {
  const updated = await updatePost(post.id, {
    status: 'draft',
    published_at: null,
    published_by: null,
    reviewed_by: null,
    reviewed_at: null,
    review_note: null,
  });
  void purgePostCache(post.slug); // #1865: stale 200 must not survive unpublish
  return updated;
}

/** Mark reviewed — leaves the queue (E2E-3). */
export async function markReviewedPost(post: BlogPostRow, identity: AuditIdentity): Promise<BlogPostRow> {
  return updatePost(post.id, {
    reviewed_by: identity.userId,
    reviewed_at: new Date().toISOString(),
  });
}

/**
 * Request changes (W2): status → draft + review_note (the note survives the
 * transition; the agent reads it and rewrites via the agent API PATCH).
 * Archived posts are terminal — refuse rather than corrupting the row.
 */
export async function requestChangesPost(post: BlogPostRow, note: string): Promise<BlogPostRow> {
  if (post.status === 'archived') {
    throw new Error('Archived posts are terminal — cannot request changes');
  }
  const updated = await updatePost(post.id, {
    status: 'draft',
    published_at: null,
    published_by: null,
    reviewed_by: null,
    reviewed_at: null,
    review_note: note.trim(),
  });
  void purgePostCache(post.slug); // #1865: request-changes → draft on a published post
  return updated;
}

/** Archive — terminal (no transitions out; trigger-enforced). */
export async function archivePost(post: BlogPostRow): Promise<BlogPostRow> {
  const updated = await updatePost(post.id, { status: 'archived' });
  void purgePostCache(post.slug); // #1865: archive must not leave a stale 200
  return updated;
}

/** Clear hold_for_review (E2E-2 after-clear step — post goes public everywhere). */
export async function clearHoldPost(post: BlogPostRow): Promise<BlogPostRow> {
  return updatePost(post.id, { hold_for_review: false });
}

// ── Input sanitization (mirrors the agent API zod surface) ────────────────

function sanitizeInsert(input: Partial<BlogPostRow>): BlogPostInsert {
  const record: BlogPostInsert = {
    title: input.title,
    slug: input.slug,
    body: input.body ?? '',
    tags: input.tags ?? [],
    author: input.author ?? 'Tortoise team',
    status: input.status ?? 'draft',
    hold_for_review: input.hold_for_review ?? false,
    created_by: input.created_by ?? null,
  };
  if (input.excerpt !== undefined) record.excerpt = input.excerpt;
  if (input.cover_image_url !== undefined) record.cover_image_url = input.cover_image_url;
  if (input.meta_title !== undefined) record.meta_title = input.meta_title;
  if (input.meta_description !== undefined) record.meta_description = input.meta_description;
  if (input.review_note !== undefined) record.review_note = input.review_note;
  if (input.published_at !== undefined) record.published_at = input.published_at;
  if (input.published_by !== undefined) record.published_by = input.published_by;
  return record;
}

function sanitizeUpdate(patch: Partial<BlogPostRow>): BlogPostUpdate {
  const record: BlogPostUpdate = {};
  if (patch.title !== undefined) record.title = patch.title;
  if (patch.slug !== undefined) record.slug = patch.slug;
  if (patch.body !== undefined) record.body = patch.body;
  if (patch.status !== undefined) record.status = patch.status;
  if (patch.author !== undefined) record.author = patch.author;
  if (patch.excerpt !== undefined) record.excerpt = patch.excerpt;
  if (patch.cover_image_url !== undefined) record.cover_image_url = patch.cover_image_url;
  if (patch.meta_title !== undefined) record.meta_title = patch.meta_title;
  if (patch.meta_description !== undefined) record.meta_description = patch.meta_description;
  if (patch.review_note !== undefined) record.review_note = patch.review_note;
  if (patch.published_at !== undefined) record.published_at = patch.published_at;
  if (patch.published_by !== undefined) record.published_by = patch.published_by;
  if (patch.reviewed_by !== undefined) record.reviewed_by = patch.reviewed_by;
  if (patch.reviewed_at !== undefined) record.reviewed_at = patch.reviewed_at;
  if (patch.tags !== undefined) record.tags = patch.tags;
  if (patch.hold_for_review !== undefined) record.hold_for_review = patch.hold_for_review;
  return record;
}

// ── Save contract helpers (shared with PostEditor's save mutation) ────────

/** Slug contract — lowercase letters/numbers separated by single dashes. */
export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const MAX_SLUG_LENGTH = 100;
export const MAX_TAGS = 10;

export function isValidSlug(slug: string): boolean {
  return SLUG_RE.test(slug) && slug.length <= MAX_SLUG_LENGTH;
}

/** Split a comma-separated tag input into a clean tag array. */
export function parseTags(raw: string): string[] {
  return raw
    .split(',')
    .map(t => t.trim())
    .filter(Boolean);
}

/** Client-side tag-cap check — returns an error message or null (max 10). */
export function tagsError(raw: string): string | null {
  return parseTags(raw).length > MAX_TAGS ? `Too many tags — max ${MAX_TAGS}` : null;
}

/** Form fields that flow into the saved record (subset of PostFormData). */
export interface SaveFormFields {
  title: string;
  slug: string;
  excerpt: string;
  cover_image_url: string;
  tags: string;
  meta_title: string;
  meta_description: string;
  hold_for_review: boolean;
}

export type SaveMode = 'draft' | 'publish';

/**
 * Build the record for a save (draft or publish) from form fields + the
 * editor's CURRENT body. Pure — no I/O, so it is unit-testable and the
 * mutation reads current state at execution time (stale-closure guard).
 */
export function buildSaveRecord(
  form: SaveFormFields,
  body: string,
  mode: SaveMode,
  userId: string,
): Partial<BlogPostRow> {
  const tags = parseTags(form.tags);
  if (tags.length > MAX_TAGS) {
    throw new Error(`Too many tags — max ${MAX_TAGS}`);
  }

  const record: Partial<BlogPostRow> = {
    title: form.title,
    slug: form.slug,
    body,
    excerpt: form.excerpt.trim() || null,
    cover_image_url: form.cover_image_url || null,
    tags,
    meta_title: form.meta_title.trim() || null,
    meta_description: form.meta_description.trim() || null,
    hold_for_review: form.hold_for_review,
  };

  if (mode === 'publish') {
    record.status = 'published';
    record.published_at = new Date().toISOString();
    record.published_by = userId;
    // Republish semantics (W2): a publish always re-enters the review
    // queue (reviewed_at=NULL) — never silently pre-reviewed.
    record.reviewed_by = null;
    record.reviewed_at = null;
  } else {
    // Save as draft. If the post was published this is an unpublish —
    // the caller confirms first (human gate, plan W2).
    record.status = 'draft';
    record.published_at = null;
    record.published_by = null;
    record.reviewed_by = null;
    record.reviewed_at = null;
  }

  return record;
}

// ── Image upload (plan §4 blog-images contract) ───────────────────────────

// ── Edge-cache purge (#1865) ──────────────────────────────────────────────
// Best-effort, server-side only (admin-gated /blog/api/purge). The editor
// writes status changes directly to Supabase, so unpublish/archive must
// explicitly purge the article URL from the Cloudflare edge cache or a stale
// 200 survives up to the cache TTL. Fail-open: purge errors never block save.
let purgeInFlight = new Map<string, Promise<void>>();

export async function purgePostCache(slug: string): Promise<void> {
  if (!slug) return;
  const key = slug;
  const existing = purgeInFlight.get(key);
  if (existing) return existing;
  const run = (async () => {
    try {
      const { data } = await supabase.auth.getSession();
      const token = data.session?.access_token;
      if (!token) return;
      await fetch('/blog/api/purge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ slug }),
      });
    } catch {
      // fail-open — purge is best-effort correctness, never blocks the write
    } finally {
      purgeInFlight.delete(key);
    }
  })();
  purgeInFlight.set(key, run);
  return run;
}

// ── AI generation (#1861 generate-seo, #1863 generate-cover) ─────────────
// Server-side only (admin-gated); the editor sends the user's access token.
// Fail-open for the caller: generation errors surface as thrown errors the
// editor catches (toast) — generation never blocks save.

export interface GenerateSeoResult {
  slug: string;
  excerpt: string;
  tags: string[];
  meta_title: string;
  meta_description: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cost_estimate: number;
}

export async function generateSeo(input: {
  title: string;
  body: string;
  tags: string[];
}): Promise<GenerateSeoResult> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error('No session');
  const res = await fetch('/blog/api/generate-seo', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) {
    throw new Error((body.message as string) || `Generation failed (${res.status})`);
  }
  return body as unknown as GenerateSeoResult;
}

export interface GenerateCoverResult {
  image_url: string;
  provider: string;
  cost_estimate: number;
  mode: 'founder' | 'abstract';
}

export async function generateCover(input: {
  title: string;
  tags: string[];
  mode: 'founder' | 'abstract';
  slug?: string;
}): Promise<GenerateCoverResult> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error('No session');
  const res = await fetch('/blog/api/generate-cover', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) {
    throw new Error((body.message as string) || `Generation failed (${res.status})`);
  }
  return body as unknown as GenerateCoverResult;
}


export const IMAGE_BUCKET = 'blog-images';
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024; // 5MB cap
export const IMAGE_MIME_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

/**
 * SanitizeObjectKey — path-traversal guard for storage object keys.
 * Contract (plan §4 / issue #1793): basename only, strip `..`, `/`,
 * backslash from filenames. Also strips control chars + spaces so the key
 * round-trips through URL encoding cleanly.
 */
export function sanitizeObjectKey(filename: string): string {
  // Basename only — strips any directory components (Windows + POSIX).
  let base = filename.replace(/\\/g, '/').split('/').pop() ?? '';
  // Remove any residual path-traversal sequences.
  base = base.replace(/\.\./g, '');
  base = base.replace(/[/\\]/g, '');
  // Control chars + whitespace → underscore (URL-safe keys).
  base = base.replace(/[\x00-\x1f\x7f\s]/g, '_');
  return base;
}

/**
 * Validate + upload an image to the blog-images bucket.
 * Path: {slug}/{timestamp}-{sanitized-filename}; 5MB cap; jpeg/png/webp only.
 * Returns the public CDN URL.
 */
export async function uploadBlogImage(file: File, slug: string): Promise<string> {
  if (!IMAGE_MIME_TYPES.has(file.type)) {
    throw new Error('Unsupported image type — use JPEG, PNG, or WebP');
  }
  if (file.size > MAX_IMAGE_BYTES) {
    throw new Error('Image too large — max 5MB');
  }
  // Slug is user-controlled until the post is saved — never trust it in the
  // storage key. Invalid slugs fall back to the 'draft' folder (the same
  // fallback the editor uses for unsaved posts).
  const folder = isValidSlug(slug) ? slug : 'draft';
  const safe = sanitizeObjectKey(file.name);
  const filename = `${Date.now()}-${safe || 'image'}`;
  const path = `${folder}/${filename}`;

  const { error } = await supabase.storage.from(IMAGE_BUCKET).upload(path, file, {
    contentType: file.type,
    cacheControl: '31536000',
  });
  if (error) throw error;

  const { data } = supabase.storage.from(IMAGE_BUCKET).getPublicUrl(path);
  return data.publicUrl;
}

const STORAGE_OBJECT_PREFIX = `/storage/v1/object/public/${IMAGE_BUCKET}/`;

/**
 * Delete an image object from its public URL (cover removal).
 * Only deletes when the URL belongs to THIS Supabase project and points at
 * the blog-images public object path — arbitrary external URLs are ignored
 * (never let an attacker point the delete at another bucket/object).
 */
export async function deleteBlogImage(publicUrl: string): Promise<void> {
  if (!publicUrl) return;
  let url: URL;
  try {
    url = new URL(publicUrl);
  } catch {
    return; // not a parseable URL — nothing to delete
  }

  const projectOrigin = new URL(SUPABASE_URL).origin;
  if (url.origin !== projectOrigin) return;
  if (!url.pathname.startsWith(STORAGE_OBJECT_PREFIX)) return;

  const path = url.pathname.slice(STORAGE_OBJECT_PREFIX.length);
  if (!path) return;
  const { error } = await supabase.storage.from(IMAGE_BUCKET).remove([path]);
  if (error) throw error;
}
