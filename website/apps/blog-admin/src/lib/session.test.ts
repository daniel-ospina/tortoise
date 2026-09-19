/**
 * session.test.ts — the `/api/session` client contract (#4171).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `fetchSession()` documents "never throws", and the app-origin migration made
 * that a load-bearing claim: when the console is not served from the session's
 * origin, `/api/session` falls through to the SPA shell and answers
 * `200 text/html`. The old code read the status as authoritative and called
 * `res.json()` OUTSIDE its try/catch, so the call REJECTED on HTML — the exact
 * opposite of the docstring — and the session gate bounced the operator to
 * sign-in. A `200` must be validated by BODY, not by status.
 *
 * These are behavioural tests over the real shipped module; `fetch` is the only
 * stub. A change that reverts the shape/content-type guard turns case 1 and 2
 * red (mutation-proven by removing the guard — see the PR notes).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchSession } from '@/lib/session';

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

function jsonResponse(body: unknown, status = 200, contentType = 'application/json; charset=utf-8') {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': contentType } });
}

function htmlResponse(status = 200) {
  return new Response('<!doctype html><html><body>app shell</body></html>', {
    status,
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });
}

describe('fetchSession — a 200 is validated by body, never taken on trust', () => {
  it('returns unavailable (not authenticated, no throw) for the HTML SPA shell', async () => {
    fetchMock.mockResolvedValue(htmlResponse());
    const result = await fetchSession();
    expect(result).toEqual({ state: 'unavailable', detail: 'malformed 200 response (not a session)' });
  });

  it('returns unavailable for a JSON 200 that is not a session object', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    const result = await fetchSession();
    expect(result.state).toBe('unavailable');
  });

  it('returns unavailable for a JSON 200 whose user has no id', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ user: { email: 'a@b.co' }, expiresAt: 1 }));
    const result = await fetchSession();
    expect(result.state).toBe('unavailable');
  });

  it('returns unavailable when the JSON content-type body is malformed', async () => {
    fetchMock.mockResolvedValue(
      new Response('<not json>', { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const result = await fetchSession();
    expect(result.state).toBe('unavailable');
  });

  it('returns authenticated for a well-formed session body', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ user: { id: 'user-1', email: 'admin@premiselabs.co' }, expiresAt: 123 }),
    );
    const result = await fetchSession();
    expect(result).toEqual({
      state: 'authenticated',
      session: { user: { id: 'user-1', email: 'admin@premiselabs.co' }, expiresAt: 123 },
    });
  });

  it('maps 401 to anonymous (not unavailable)', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 401 }));
    expect(await fetchSession()).toEqual({ state: 'anonymous' });
  });

  it('maps 503 to unavailable with the status detail — never anonymous', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 503 }));
    expect(await fetchSession()).toEqual({ state: 'unavailable', detail: 'HTTP 503' });
  });

  it('maps a network rejection to unavailable — never a throw', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    const result = await fetchSession();
    expect(result.state).toBe('unavailable');
  });

  it('asks for the session same-origin so it rides the session cookie', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ user: { id: 'u' }, expiresAt: 1 }));
    await fetchSession();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/session');
    expect(init.credentials).toBe('same-origin');
  });
});
