/**
 * supabase-auth-storage.test.ts — the #3485 session contract for the blog admin
 * SPA's storage adapter.
 *
 * The /admin gate Function verifies the parent-domain cookie
 * `sb-tortoise-auth-token` SERVER-side, so client-visible must imply
 * server-visible. These tests pin the adapter to that:
 *   1. getItem prefers the cookie over an origin-scoped localStorage session;
 *   2. in production a cookie-less localStorage session is NOT returned — the
 *      stale-session class that autoRefreshToken re-mints the cookie from;
 *   3. removeItem really removes the cookie and the localStorage copy (a
 *      duplicated `max-age` used to keep the cookie on the document);
 *   4. setItem never leaves a local copy the server cannot see;
 *   5. a non-session key (supabase-js auxiliary values, e.g. a PKCE code
 *      verifier) never reads the session cookie.
 *
 * supabase.ts throws at import without env vars, so they are stubbed before the
 * dynamic import — hence the top-level await.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import type { SupportedStorage } from '@supabase/supabase-js';

vi.stubEnv('VITE_SUPABASE_URL', 'https://test.supabase.co');
vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'test-anon-key');

const { authStorage } = (await import('@/lib/supabase')) as {
  authStorage: SupportedStorage;
};

const KEY = 'sb-tortoise-auth-token';
const SESSION_COOKIE = JSON.stringify({ access_token: 'cookie-token', refresh_token: 'r1' });
const SESSION_LOCAL = JSON.stringify({ access_token: 'local-token', refresh_token: 'r2' });

/** `import.meta.env.DEV` is readonly to tsc, but mutable at runtime (vitest). */
const env = import.meta.env as unknown as { DEV: boolean };

function clearAll(): void {
  document.cookie = `${KEY}=; path=/; max-age=0`;
  localStorage.clear();
}

afterEach(() => {
  clearAll();
  env.DEV = true;
});

describe('#3485 session adapter — the cookie is the source of truth', () => {
  it('prefers the cookie over an origin-scoped localStorage session', () => {
    document.cookie = `${KEY}=${encodeURIComponent(SESSION_COOKIE)}; path=/`;
    localStorage.setItem(KEY, SESSION_LOCAL);
    // The gate can see the cookie and cannot see localStorage, so the cookie
    // wins even when the localStorage copy is present.
    expect(authStorage.getItem(KEY)).toBe(SESSION_COOKIE);
  });

  it('production: does not restore a session from localStorage when the cookie is gone', () => {
    env.DEV = false;
    localStorage.setItem(KEY, SESSION_LOCAL); // what a cleared sign-out leaves behind
    expect(authStorage.getItem(KEY)).toBeNull();
  });

  it('dev: still reads the localStorage session as a fallback', () => {
    env.DEV = true;
    localStorage.setItem(KEY, SESSION_LOCAL);
    expect(authStorage.getItem(KEY)).toBe(SESSION_LOCAL);
  });

  it('removeItem clears the cookie and the localStorage copy', () => {
    document.cookie = `${KEY}=${encodeURIComponent(SESSION_COOKIE)}; path=/`;
    localStorage.setItem(KEY, SESSION_LOCAL);
    authStorage.removeItem(KEY);
    expect(authStorage.getItem(KEY)).toBeNull();
    expect(document.cookie).not.toContain(KEY);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('setItem leaves no local copy the server cannot see', () => {
    const descriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie')!;
    // Simulate a cookie write the browser refuses (blocked, or over the 4 KB
    // cookie cap): nothing lands in document.cookie. The session must then not
    // survive in localStorage — a session the gate cannot read must not exist.
    Object.defineProperty(document, 'cookie', { configurable: true, get: () => '', set: () => {} });
    try {
      authStorage.setItem(KEY, SESSION_COOKIE);
      expect(localStorage.getItem(KEY)).toBeNull();
    } finally {
      Object.defineProperty(document, 'cookie', descriptor);
    }
  });

  it('never serves the session for a non-session key', () => {
    document.cookie = `${KEY}=${encodeURIComponent(SESSION_COOKIE)}; path=/`;
    // supabase-js stores auxiliary values under `<storageKey>-*`; the cookie
    // holds the SESSION, so a verifier read must not return the session blob.
    expect(authStorage.getItem(`${KEY}-code-verifier`)).toBeNull();
  });

  it('removeItem drops a NON-session key without clearing the session cookie', () => {
    document.cookie = `${KEY}=${encodeURIComponent(SESSION_COOKIE)}; path=/`;
    // supabase-js removes the PKCE verifier through removeItem(`${key}-code-verifier`).
    // Without the key guard that removal clears the live session cookie and signs
    // the user out mid-flow.
    authStorage.removeItem(`${KEY}-code-verifier`);
    expect(authStorage.getItem(KEY)).toBe(SESSION_COOKIE);
  });

  it('production: never writes the session to localStorage (nothing reads it there)', () => {
    env.DEV = false;
    authStorage.setItem(KEY, SESSION_COOKIE);
    // The cookie took the value, so the local copy would be the ONLY reason a
    // credential sits at rest in a production browser — and getItem's fallback is
    // DEV-gated, so nothing would ever read it back.
    expect(authStorage.getItem(KEY)).toBe(SESSION_COOKIE);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('sign-out clears BOTH cookie scopes (a host-only shadow survives a domain-only clear)', () => {
    // On a real host the adapter writes with `domain=.premiselabs.co`. Cookie
    // identity is (name, domain, path), so clearing ONLY that scope leaves a
    // HOST-ONLY cookie of the same name — still sent to this host, so the gate
    // still sees it, the SPA stays signed in after sign-out, and the next
    // autoRefreshToken re-mints the parent-domain copy.
    const writes: string[] = [];
    const descriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie')!;
    Object.defineProperty(document, 'cookie', {
      configurable: true,
      get: () => '',
      set: (v: string) => {
        writes.push(String(v));
      },
    });
    vi.stubGlobal('window', { location: { hostname: 'tortoise.premiselabs.co', protocol: 'https:' } });
    try {
      authStorage.removeItem(KEY);
    } finally {
      vi.unstubAllGlobals();
      Object.defineProperty(document, 'cookie', descriptor);
    }
    const clears = writes.filter((w) => w.startsWith(`${KEY}=`));
    const withDomain = clears.filter((w) => w.includes('domain=.premiselabs.co'));
    const hostOnly = clears.filter((w) => !w.includes('domain='));
    expect(withDomain, 'the parent-domain copy is cleared').toHaveLength(1);
    expect(hostOnly, 'the host-only shadow is cleared too').toHaveLength(1);
    expect(hostOnly[0]).toContain('max-age=0');
  });
});
