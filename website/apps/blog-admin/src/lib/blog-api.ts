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

import { supabase } from '@/lib/supabase';
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

/** Review queue — the three item kinds from plan W2. */
export async function listQueue(): Promise<BlogPostRow[]> {
  const { data, error } = await supabase
    .from('blog_posts')
    .select(POST_SELECT)
    .or(`status.eq.draft,and(status.eq.published,reviewed_at.is.null),hold_for_review.eq.true`)
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
  return updatePost(post.id, {
    status: 'draft',
    published_at: null,
    published_by: null,
    reviewed_by: null,
    reviewed_at: null,
    review_note: null,
  });
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
 */
export async function requestChangesPost(post: BlogPostRow, note: string): Promise<BlogPostRow> {
  return updatePost(post.id, {
    status: 'draft',
    published_at: null,
    published_by: null,
    reviewed_by: null,
    reviewed_at: null,
    review_note: note.trim(),
  });
}

/** Archive — terminal (no transitions out; trigger-enforced). */
export async function archivePost(post: BlogPostRow): Promise<BlogPostRow> {
  return updatePost(post.id, { status: 'archived' });
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

// ── Image upload (plan §4 blog-images contract) ───────────────────────────

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
  const safe = sanitizeObjectKey(file.name);
  const filename = `${Date.now()}-${safe || 'image'}`;
  const path = `${slug}/${filename}`;

  const { error } = await supabase.storage.from(IMAGE_BUCKET).upload(path, file, {
    contentType: file.type,
    cacheControl: '31536000',
  });
  if (error) throw error;

  const { data } = supabase.storage.from(IMAGE_BUCKET).getPublicUrl(path);
  return data.publicUrl;
}

/** Delete an image object from its public URL (cover removal). */
export async function deleteBlogImage(publicUrl: string): Promise<void> {
  const marker = `/${IMAGE_BUCKET}/`;
  const idx = publicUrl.indexOf(marker);
  if (idx === -1) return;
  const path = publicUrl.slice(idx + marker.length);
  const { error } = await supabase.storage.from(IMAGE_BUCKET).remove([path]);
  if (error) throw error;
}
