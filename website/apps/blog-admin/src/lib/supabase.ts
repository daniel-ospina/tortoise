/**
 * Supabase client for the Tortoise blog admin SPA.
 *
 * Session contract (#3485): the parent-domain cookie `sb-tortoise-auth-token`
 * (JSON, same shape supabase-js persists: { access_token, refresh_token, ... })
 * is the ONLY source of truth. The admin gate Function
 * (website/functions/admin/[[path]].ts) verifies that cookie server-side, so
 * client-visible must imply server-visible: a session this SPA can see but the
 * gate cannot is not a session — with `autoRefreshToken` it would refresh
 * itself and RE-MINT the cookie a sign-out just cleared, resurrecting exactly
 * the signed-out state #3485 removes.
 *
 * Storage adapter (parent-domain cookie; localStorage only as a dev fallback):
 *  - getItem: the COOKIE first. localStorage is read only when no cookie exists
 *    AND `import.meta.env.DEV` — in a production bundle that branch is
 *    statically dead, so an origin-scoped stale session can neither outrank the
 *    cookie nor survive the cookie's removal.
 *  - setItem: writes the cookie, and keeps the localStorage copy only when the
 *    cookie verifiably took the value (a refused/oversized write drops the
 *    local copy rather than leaving a session the gate cannot see).
 *  - removeItem: clears both (sign-out), and the cookie is really gone — see
 *    clearCookie for why the old attribute list kept it.
 *
 * Keys other than STORAGE_KEY never touch the cookie: supabase-js stores
 * auxiliary values (e.g. a PKCE code verifier) under `<storageKey>-*`, and the
 * cookie holds the SESSION — serving it for those keys handed a verifier read
 * the session blob.
 *
 * RLS note: the SPA NEVER uses a service-role key. Reads/writes ride the
 * user's session; blog_posts RLS grants SELECT/ALL to is_admin() members
 * only (migration 20260827000001, issue #1793).
 */

import { createClient, type SupabaseClient, type SupportedStorage } from '@supabase/supabase-js';
import type { Database } from '@/lib/types';

export const STORAGE_KEY = 'sb-tortoise-auth-token';
export const AUTH_URL = 'https://tortoise.premiselabs.co/auth';

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;

export { SUPABASE_URL };

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  throw new Error(
    'Missing VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY — copy .env.example to .env (see website/apps/blog-admin/README.md)',
  );
}

function readCookie(): string | null {
  if (typeof document === 'undefined') return null;
  for (const part of document.cookie.split(';')) {
    const idx = part.indexOf('=');
    const name = (idx === -1 ? part : part.slice(0, idx)).trim();
    if (name === STORAGE_KEY) {
      try {
        return decodeURIComponent(part.slice(idx + 1).trim());
      } catch {
        return null;
      }
    }
  }
  return null;
}

/**
 * Cookie scope — parent-domain (.premiselabs.co) on real hosts, omitted in dev.
 * Deliberately carries NO expiry: the caller owns the lifetime, because one
 * attribute list reused for both write and clear is how the clear became a
 * no-op.
 */
function cookieScope(): string[] {
  const parts = ['path=/', 'samesite=lax'];
  if (typeof window !== 'undefined') {
    const host = window.location.hostname;
    if (host !== 'localhost' && host !== '127.0.0.1' && host.endsWith('premiselabs.co')) {
      parts.push('domain=.premiselabs.co');
    }
    if (window.location.protocol === 'https:') parts.push('secure');
  }
  return parts;
}

function writeCookie(value: string): void {
  const attrs = ['max-age=31536000', ...cookieScope()];
  document.cookie = `${STORAGE_KEY}=${encodeURIComponent(value)}; ${attrs.join('; ')}`;
}

/**
 * Clearing must not repeat the year-long max-age: a duplicated attribute is
 * resolved in favour of the LAST one, so the old string
 * (`max-age=0; …; max-age=31536000`) left the cookie ON the document with an
 * empty value instead of removing it. Expiry is the ONLY max-age here, which is
 * why the scope list above excludes it.
 */
function clearCookie(): void {
  const attrs = ['max-age=0', ...cookieScope()];
  document.cookie = `${STORAGE_KEY}=; ${attrs.join('; ')}`;
}

function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // localStorage unavailable (private mode / disabled)
  }
}

function writeLocal(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // ignore — the cookie already holds the session
  }
}

function removeLocal(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // ignore
  }
}

/** Exported for tests (supabase-auth-storage.test.ts). */
export const authStorage: SupportedStorage = {
  getItem: (key: string) => {
    if (key === STORAGE_KEY) {
      const cookie = readCookie();
      if (cookie) return cookie;
      // No cookie → the gate sees no session. Only dev may fall back to
      // localStorage (#3485): honouring it in production is what let a stale
      // origin-scoped session be refreshed and re-mint the cleared cookie.
      if (!import.meta.env.DEV) return null;
    }
    return readLocal(key);
  },
  setItem: (key: string, value: string) => {
    if (key !== STORAGE_KEY) {
      writeLocal(key, value); // auxiliary value — never the session cookie
      return;
    }
    writeCookie(value);
    // Keep the local copy ONLY when the cookie verifiably holds the value.
    // Otherwise the session would be visible to this SPA and invisible to the
    // gate — and autoRefreshToken would later re-mint it from the refresh
    // token, resurrecting a sign-out the server already performed.
    if (readCookie() === value) writeLocal(key, value);
    else removeLocal(key);
  },
  removeItem: (key: string) => {
    removeLocal(key);
    if (key === STORAGE_KEY) clearCookie();
  },
};

export const supabase: SupabaseClient<Database> = createClient<Database>(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    storageKey: STORAGE_KEY,
    storage: authStorage,
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: false,
  },
});
