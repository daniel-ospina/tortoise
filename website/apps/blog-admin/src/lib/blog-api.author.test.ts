/**
 * blog-api.author.test.ts — the author identity round trip (#1383).
 *
 * The sibling `blog-api.test.ts` stubs `createPost`/`updatePost` to test the
 * save HANDLER; that is the wrong seam for this defect. The bug #1383 fixes is
 * that the editor had no author field at all, so a value the owner typed could
 * be dropped on ANY of three hops: the save record → the INSERT/PATCH payload →
 * the SELECT list. A test that only checks the form record proves hop 1.
 *
 * So this file exercises the REAL `createPost`/`updatePost`/`getPost` against a
 * mocked transport (`@/lib/backend`), and the mocked DB ECHOES what was written
 * back as the returned row — a value saved is a value read back, derived from
 * the payload rather than hand-written into a fixture.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

const { insertMock, updateMock, eqMock, dbRow, chain } = vi.hoisted(() => {
  const dbRow: Record<string, unknown> = {
    id: 'p-1',
    slug: 'post',
    title: 'Post',
    body: 'body',
    excerpt: null,
    cover_image_url: null,
    tags: [],
    author: 'Tortoise team',
    author_image_url: null,
    status: 'draft',
    meta_title: null,
    meta_description: null,
    published_at: null,
    published_by: null,
    created_by: null,
    reviewed_by: null,
    reviewed_at: null,
    review_note: null,
    hold_for_review: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };

  const insertMock = vi.fn();
  const updateMock = vi.fn();
  const selectMock = vi.fn();
  const eqMock = vi.fn();
  const chain: Record<string, ReturnType<typeof vi.fn>> = {
    insert: insertMock,
    update: updateMock,
    select: selectMock,
    eq: eqMock,
    single: vi.fn(),
    maybeSingle: vi.fn(),
    or: vi.fn(),
    neq: vi.fn(),
    order: vi.fn(),
  };

  // Echo whatever was written into the row, so the returned value is DERIVED
  // from the payload instead of asserted against a hand-written fixture.
  insertMock.mockImplementation((record: Record<string, unknown>) => {
    Object.assign(dbRow, record);
    return chain;
  });
  updateMock.mockImplementation((record: Record<string, unknown>) => {
    Object.assign(dbRow, record);
    return chain;
  });
  selectMock.mockReturnValue(chain);
  eqMock.mockReturnValue(chain);
  chain.single.mockImplementation(() => Promise.resolve({ data: { ...dbRow }, error: null }));
  chain.maybeSingle.mockImplementation(() => Promise.resolve({ data: { ...dbRow }, error: null }));

  return { insertMock, updateMock, eqMock, dbRow, chain };
});

vi.mock('@/lib/backend', () => ({
  SUPABASE_URL: 'https://abc123.supabase.co',
  bff: { from: vi.fn(() => chain) },
}));

import {
  createPost, updatePost, getPost, buildSaveRecord, POST_SELECT,
  DEFAULT_AUTHOR, DEFAULT_AUTHOR_IMAGE_URL, type SaveFormFields,
} from '@/lib/blog-api';
import { createSaveHandler } from '@/lib/save';

const AVATAR = 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg';

function form(overrides: Partial<SaveFormFields> = {}): SaveFormFields {
  return {
    title: 'Post', slug: 'post', excerpt: '', cover_image_url: '',
    tags: '', author: DEFAULT_AUTHOR, author_image_url: AVATAR,
    meta_title: '', meta_description: '', hold_for_review: false,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  dbRow.author = 'Tortoise team';
  dbRow.author_image_url = null;
});

describe('author identity round trip (#1383)', () => {
  it('POST_SELECT asks for author_image_url — without it the editor can never read the value back', () => {
    // Reverting the column out of the select list leaves every other assertion
    // here green while the editor hydrates the image as blank on every post.
    expect(POST_SELECT).toContain('author_image_url');
  });

  it('a saved author name + image URL is the row read back', async () => {
    const saved = await createPost({
      title: 'Post',
      author: 'Ada Lovelace',
      author_image_url: 'https://cdn.example.com/ada.jpg',
    });
    expect(insertMock).toHaveBeenCalledWith(
      expect.objectContaining({ author: 'Ada Lovelace', author_image_url: 'https://cdn.example.com/ada.jpg' }),
    );
    expect(saved.author).toBe('Ada Lovelace');
    expect(saved.author_image_url).toBe('https://cdn.example.com/ada.jpg');
  });

  it('an existing post hydrates the stored image, and a PATCH carries it back', async () => {
    dbRow.author = DEFAULT_AUTHOR;
    dbRow.author_image_url = AVATAR;

    const loaded = await getPost('p-1');
    expect(loaded?.author_image_url).toBe(AVATAR);

    await updatePost('p-1', { author: 'Daniel Ospina', author_image_url: 'https://cdn.example.com/new.jpg' });
    expect(eqMock).toHaveBeenCalledWith('id', 'p-1');
    expect(updateMock).toHaveBeenCalledWith(
      expect.objectContaining({ author_image_url: 'https://cdn.example.com/new.jpg' }),
    );
  });

  it('a create with no author gets the real author, and a NULL image (never an empty string)', async () => {
    await createPost({ title: 'Post' });
    const payload = insertMock.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.author).toBe(DEFAULT_AUTHOR);
    expect(payload.author).not.toBe('Tortoise team');
    expect(payload.author_image_url).toBeNull();
  });

  it('the editor save path carries both fields from the form to the payload', async () => {
    const state = { form: form(), editorBody: 'body', userId: 'user-1', isNew: true };
    await createSaveHandler(() => state)('draft');
    const payload = insertMock.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.author).toBe(DEFAULT_AUTHOR);
    expect(payload.author_image_url).toBe(AVATAR);
  });

  it('a blank form falls back to the shipped author and a NULL image — the name-alone byline', async () => {
    const record = buildSaveRecord(form({ author: '   ', author_image_url: '  ' }), 'body', 'draft', 'user-1');
    expect(record.author).toBe(DEFAULT_AUTHOR);
    expect(record.author_image_url).toBeNull();
  });

  it('the shipped avatar constant is the asset this change adds', () => {
    expect(DEFAULT_AUTHOR_IMAGE_URL).toBe(AVATAR);
  });
});
