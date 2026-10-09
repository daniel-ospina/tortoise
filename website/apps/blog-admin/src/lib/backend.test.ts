/**
 * backend.test.ts — the console's BFF credential seam (#4178, parent #3501).
 *
 * `proxiedFetch` is what makes supabase-js's `rest/v1` and `storage/v1` traffic
 * ride the same-origin `/api/sb/*` Token Handler instead of carrying the anon
 * key straight to the project. `blog-api.test.ts` module-mocks `@/lib/backend`,
 * so this behaviour had NO coverage at all — a rewrite that silently stopped
 * firing would reach the project origin unauthenticated and resolve to the
 * `anon` DB role (reads return the published subset, writes 42501), with every
 * other test still green.
 *
 * The client is constructed from `VITE_SUPABASE_URL` at module load, so the env
 * is stubbed and the module imported dynamically BEFORE the assertions. `fetch`
 * is the only stub — nothing here touches the network.
 */

import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';

const PROJECT_ORIGIN = 'https://abc123.supabase.co';
const PROXY_PREFIX = '/api/sb';

type Backend = typeof import('@/lib/backend');
let proxiedFetch: Backend['proxiedFetch'];
let bff: Backend['bff'];

const fetchMock = vi.fn();

beforeAll(async () => {
  vi.stubEnv('VITE_SUPABASE_URL', PROJECT_ORIGIN);
  const mod = await import('@/lib/backend');
  proxiedFetch = mod.proxiedFetch;
  bff = mod.bff;
});

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
});

describe('proxiedFetch — rest/v1 and storage/v1 ride the same-origin Token Handler', () => {
  it('rewrites a project-origin rest/v1 request to /api/sb/, preserving path and search', async () => {
    const init = { method: 'GET' };
    await proxiedFetch(
      `${PROJECT_ORIGIN}/rest/v1/blog_posts?select=*&status=eq.draft&order=created_at.desc`,
      init,
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, passedInit] = fetchMock.mock.calls[0];
    expect(url).toBe(
      `${PROXY_PREFIX}/rest/v1/blog_posts?select=*&status=eq.draft&order=created_at.desc`,
    );
    expect(String(url)).not.toContain(PROJECT_ORIGIN);
    expect(passedInit).toBe(init);
  });

  it('rewrites a project-origin storage/v1 request to /api/sb/, preserving path and search', async () => {
    await proxiedFetch(
      `${PROJECT_ORIGIN}/storage/v1/object/blog-images/draft/1712345-img.png?x=1`,
      { method: 'PUT' },
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      `${PROXY_PREFIX}/storage/v1/object/blog-images/draft/1712345-img.png?x=1`,
    );
  });

  it('rewrites a project-origin nested storage/v1 path without dropping segments', async () => {
    await proxiedFetch(`${PROJECT_ORIGIN}/storage/v1/object/blog-images/a/b/c.png`);
    expect(fetchMock.mock.calls[0][0]).toBe(
      `${PROXY_PREFIX}/storage/v1/object/blog-images/a/b/c.png`,
    );
  });

  it('leaves a non-proxied path untouched — the project origin is still reachable directly', async () => {
    const untouched = `${PROJECT_ORIGIN}/auth/v1/token?grant_type=password`;
    await proxiedFetch(untouched);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(untouched);
  });

  it('leaves a foreign-origin path untouched, even under a proxied prefix', async () => {
    const foreign = 'https://evil.example.com/rest/v1/blog_posts?select=*';
    await proxiedFetch(foreign);
    expect(fetchMock.mock.calls[0][0]).toBe(foreign);
  });

  it('does not route getPublicUrl through the proxy — the public blog must keep the real origin', () => {
    const { data } = bff.storage.from('blog-images').getPublicUrl('draft/1712345-img.png');
    expect(data.publicUrl).toBe(
      `${PROJECT_ORIGIN}/storage/v1/object/public/blog-images/draft/1712345-img.png`,
    );
    expect(data.publicUrl).not.toContain(PROXY_PREFIX);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
