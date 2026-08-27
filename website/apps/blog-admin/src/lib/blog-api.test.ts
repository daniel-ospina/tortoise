/**
 * blog-api.test.ts — unit tests for the blog admin API layer (PR #1818 fixes):
 *  (a) sanitizeObjectKey path-traversal / control-char sanitization
 *  (b) uploadBlogImage slug-path validation (invalid slug → 'draft' folder)
 *  (c) stale-closure guard: the save handler reads CURRENT state at execution
 *  (d) tags capped at 10 (API contract)
 * plus: listQueue excludes archived, requestChangesPost refuses archived,
 * deleteBlogImage only touches THIS project's public blog-images objects.
 *
 * supabase is module-mocked (supabase.ts throws at import without env vars;
 * the SPA only ever runs behind the admin gate, so unit tests never hit a
 * real backend).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Supabase mock (hoisted so the blog-api module sees it at import) ──────

const { uploadMock, getPublicUrlMock, removeMock, queryChain } = vi.hoisted(() => {
  const uploadMock = vi.fn();
  const getPublicUrlMock = vi.fn();
  const removeMock = vi.fn();
  // Chainable query builder (listQueue / updatePost / getPost).
  const chain = {
    select: vi.fn(), or: vi.fn(), neq: vi.fn(), order: vi.fn(),
    update: vi.fn(), eq: vi.fn(), single: vi.fn(), maybeSingle: vi.fn(),
  } as Record<string, ReturnType<typeof vi.fn>>;
  chain.select.mockReturnValue(chain);
  chain.or.mockReturnValue(chain);
  chain.neq.mockReturnValue(chain);
  chain.update.mockReturnValue(chain);
  chain.eq.mockReturnValue(chain);
  chain.single.mockResolvedValue({ data: null, error: null });
  chain.maybeSingle.mockResolvedValue({ data: null, error: null });
  chain.order.mockResolvedValue({ data: [], error: null });
  return { uploadMock, getPublicUrlMock, removeMock, queryChain: chain };
});

vi.mock('@/lib/supabase', () => ({
  SUPABASE_URL: 'https://abc123.supabase.co',
  supabase: {
    storage: {
      from: vi.fn(() => ({
        upload: uploadMock,
        getPublicUrl: getPublicUrlMock,
        remove: removeMock,
      })),
    },
    from: vi.fn(() => queryChain),
  },
}));

// Partial mock of blog-api — keep the real logic, stub the two network writes
// so the save-handler tests can assert what was sent without a backend.
vi.mock('@/lib/blog-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/blog-api')>();
  return {
    ...actual,
    createPost: vi.fn(),
    updatePost: vi.fn(),
  };
});

import {
  sanitizeObjectKey, uploadBlogImage, deleteBlogImage, listQueue,
  requestChangesPost, isValidSlug, tagsError, parseTags, buildSaveRecord,
  createPost, updatePost, MAX_TAGS,
  type BlogPostRow,
} from '@/lib/blog-api';
import { createSaveHandler } from '@/lib/save';

const pngFile = () => new File(['fake-image'], 'img.png', { type: 'image/png' });

beforeEach(() => {
  vi.clearAllMocks();
  uploadMock.mockResolvedValue({ error: null });
  getPublicUrlMock.mockReturnValue({ data: { publicUrl: 'https://cdn.example.com/x.png' } });
  vi.mocked(createPost).mockResolvedValue(FAKE_ROW);
  vi.mocked(updatePost).mockResolvedValue(FAKE_ROW);
});

const FAKE_ROW = {
  id: 'p-1', slug: 'post', title: 'Post', body: 'body',
  excerpt: null, cover_image_url: null, tags: [], author: null,
  status: 'draft', meta_title: null, meta_description: null,
  published_at: null, published_by: null, created_by: null,
  reviewed_by: null, reviewed_at: null, review_note: null,
  hold_for_review: false, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
} as BlogPostRow;

// ── (a) sanitizeObjectKey ──────────────────────────────────────────────────

describe('sanitizeObjectKey', () => {
  it.each([
    ['plain', 'photo.png', 'photo.png'],
    ['posix traversal', '../../x.png', 'x.png'],
    ['deep traversal', '../../../../etc/passwd', 'passwd'],
    ['windows traversal', '..\\..\\secret.jpg', 'secret.jpg'],
    ['windows separators', 'a\\b\\c.png', 'c.png'],
    ['posix separators', 'dir/sub/file.png', 'file.png'],
    ['control char', 'evil\x00.png', 'evil_.png'],
    ['control char mid', 'a\x1fb.png', 'a_b.png'],
    ['del char', 'x\x7f.png', 'x_.png'],
    ['spaces', 'my file.png', 'my_file.png'],
    ['dots only', '..', ''],
  ])('%s → %j', (_name, input, expected) => {
    expect(sanitizeObjectKey(input)).toBe(expected);
  });

  it('never contains traversal or separator sequences', () => {
    for (const input of ['../../x.png', 'a\\b\\..\\c.png', 'dir/../evil.png']) {
      expect(sanitizeObjectKey(input)).not.toMatch(/\.\.|\/|\\/);
    }
  });
});

// ── (b) slug-path validation (invalid slug → 'draft' fallback) ─────────────

describe('uploadBlogImage slug handling', () => {
  it('uses the valid slug folder for a well-formed slug', async () => {
    await uploadBlogImage(pngFile(), 'my-blog-post');
    expect(uploadMock.mock.calls[0][0]).toMatch(/^my-blog-post\/\d+-img\.png$/);
  });

  it.each([
    ['path traversal', '../../etc/passwd'],
    ['uppercase', 'MyPost'],
    ['underscores', 'my_post'],
    ['spaces', 'my post'],
    ['too long', 'a'.repeat(101)],
    ['empty', ''],
  ])('falls back to draft folder for %s', async (_name, slug) => {
    await uploadBlogImage(pngFile(), slug);
    const path = uploadMock.mock.calls[0][0] as string;
    // Exact shape: draft/<ts>-img.png — no slug-derived segments.
    expect(path).toMatch(/^draft\/\d+-img\.png$/);
    expect(path).not.toContain('..');
  });

  it('sanitizes the filename in the stored key (traversal stripped)', async () => {
    const file = new File(['x'], '../../evil.png', { type: 'image/png' });
    await uploadBlogImage(file, 'ok-slug');
    const path = uploadMock.mock.calls[0][0] as string;
    const filename = path.split('/').pop()!;
    expect(filename).not.toMatch(/\.\.|\//);
    expect(filename).toContain('evil.png');
  });
});

describe('isValidSlug', () => {
  it('accepts lowercase alnum with single dashes', () => {
    expect(isValidSlug('tortoise-memory-engine')).toBe(true);
    expect(isValidSlug('a')).toBe(true);
    expect(isValidSlug('a1-b2')).toBe(true);
  });
  it('rejects invalid slugs', () => {
    expect(isValidSlug('MyPost')).toBe(false);
    expect(isValidSlug('my_post')).toBe(false);
    expect(isValidSlug('a--b')).toBe(false);
    expect(isValidSlug('-lead')).toBe(false);
    expect(isValidSlug('trail-')).toBe(false);
    expect(isValidSlug('a'.repeat(101))).toBe(false);
  });
});

// ── (c) stale-closure guard: save reads CURRENT state at execution ─────────

function makeState(overrides: Partial<Parameters<typeof createSaveHandler>[0]> = {}) {
  return {
    form: {
      title: 'Post', slug: 'post', excerpt: '', cover_image_url: '',
      tags: 'a, b', meta_title: '', meta_description: '', hold_for_review: false,
    },
    editorBody: 'body v1',
    userId: 'user-1',
    isNew: true,
    ...overrides,
  };
}

describe('save handler (stale-closure guard)', () => {
  it('uses the CURRENT editor body when invoked, not the snapshot at creation', async () => {
    const state = makeState();
    const handler = createSaveHandler(() => state);

    // The handler was created while the body was "v1"; the editor body
    // changes before the save actually executes (simulated stale closure).
    state.editorBody = 'body v2 edited after snapshot';

    await handler('draft');

    expect(createPost).toHaveBeenCalledWith(
      expect.objectContaining({ body: 'body v2 edited after snapshot', tags: ['a', 'b'] }),
    );
  });

  it('stamps published fields on publish mode', async () => {
    const state = makeState();
    const handler = createSaveHandler(() => state);
    await handler('publish');

    const record = vi.mocked(createPost).mock.calls[0][0];
    expect(record.status).toBe('published');
    expect(record.published_by).toBe('user-1');
    expect(record.reviewed_at).toBeNull();
    expect(record.reviewed_by).toBeNull();
  });

  it('stamps draft fields (unpublish semantics) on draft mode', async () => {
    const state = makeState({ isNew: false, id: 'p-1' });
    const handler = createSaveHandler(() => state);
    await handler('draft');

    expect(updatePost).toHaveBeenCalledWith(
      'p-1',
      expect.objectContaining({ status: 'draft', published_at: null, published_by: null }),
    );
  });

  it('creates with created_by on new posts', async () => {
    const state = makeState();
    const handler = createSaveHandler(() => state);
    await handler('draft');
    const record = vi.mocked(createPost).mock.calls[0][0];
    expect(record.created_by).toBe('user-1');
  });
});

// ── (d) tags capped at 10 ──────────────────────────────────────────────────

describe('tag cap (max 10)', () => {
  it('parseTags splits and trims commas', () => {
    expect(parseTags(' a,  b ,, c ')).toEqual(['a', 'b', 'c']);
  });

  it('tagsError flags >10 tags and passes at exactly 10', () => {
    const ten = Array.from({ length: 10 }, (_, i) => `t${i}`).join(',');
    const eleven = `${ten},t10`;
    expect(tagsError(eleven)).toMatch(new RegExp(`max ${MAX_TAGS}`));
    expect(tagsError(ten)).toBeNull();
    expect(tagsError('')).toBeNull();
  });

  it('buildSaveRecord throws on >10 tags (contract defense)', () => {
    const eleven = Array.from({ length: 11 }, (_, i) => `t${i}`).join(',');
    expect(() => buildSaveRecord(makeState().form, 'body', 'draft', 'u')).not.toThrow();
    expect(() => buildSaveRecord({ ...makeState().form, tags: eleven }, 'body', 'draft', 'u')).toThrow(
      new RegExp(`max ${MAX_TAGS}`),
    );
  });
});

// ── listQueue / requestChanges / deleteBlogImage guards ───────────────────

describe('queue + terminal-status guards', () => {
  it('listQueue excludes archived rows', async () => {
    await listQueue();
    expect(queryChain.neq).toHaveBeenCalledWith('status', 'archived');
  });

  it('requestChangesPost refuses archived rows', async () => {
    const archived = {
      id: 'a1', status: 'archived', slug: 'x', title: 'x', body: '',
      tags: [], author: null, excerpt: null, cover_image_url: null,
      meta_title: null, meta_description: null, published_at: null,
      published_by: null, created_by: null, reviewed_by: null,
      reviewed_at: null, review_note: null, hold_for_review: false,
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    } satisfies BlogPostRow;
    await expect(requestChangesPost(archived, 'fix it')).rejects.toThrow(/terminal/i);
  });

  it('requestChangesPost proceeds for non-archived rows', async () => {
    const draft = {
      ...FAKE_ROW,
      id: 'd1',
      status: 'draft',
    } as BlogPostRow;
    // The internal updatePost hits the mocked supabase chain (resolves null);
    // this asserts the archive guard does not block normal rows.
    await expect(requestChangesPost(draft, 'note')).resolves.toBeNull();
  });
});

describe('deleteBlogImage origin/path guard', () => {
  const objectUrl = (path: string) => `https://abc123.supabase.co/storage/v1/object/public/blog-images/${path}`;

  it('deletes only this project\'s public blog-images objects', async () => {
    removeMock.mockResolvedValue({ error: null });
    await deleteBlogImage(objectUrl('draft/1712345-img.png'));
    expect(removeMock).toHaveBeenCalledWith(['draft/1712345-img.png']);
  });

  it('ignores URLs from other origins', async () => {
    await deleteBlogImage('https://evil.example.com/storage/v1/object/public/blog-images/x.png');
    expect(removeMock).not.toHaveBeenCalled();
  });

  it('ignores same-origin URLs outside the blog-images public path', async () => {
    await deleteBlogImage('https://abc123.supabase.co/storage/v1/object/public/other-bucket/x.png');
    expect(removeMock).not.toHaveBeenCalled();
  });

  it('ignores unparseable URLs', async () => {
    await deleteBlogImage('not a url');
    expect(removeMock).not.toHaveBeenCalled();
  });

  it('ignores empty URLs', async () => {
    await deleteBlogImage('');
    expect(removeMock).not.toHaveBeenCalled();
  });
});
