/**
 * Supabase client for the Tortoise blog admin SPA.
 *
 * Session contract: the site (tortoise.premiselabs.co) writes a parent-domain
 * PKCE session cookie named `sb-tortoise-auth-token` (JSON, same shape
 * supabase-js persists: { access_token, refresh_token, ... }). The admin gate
 * Function (website/functions/admin/[[path]].ts) verifies that cookie
 * server-side; this SPA reads the SAME session with the USER's own token via
 * a storage adapter keyed on `sb-tortoise-auth-token`.
 *
 * Storage adapter (localStorage + parent-domain cookie):
 *  - getItem: localStorage first, then the cookie (session written by the
 *    site's auth flow on another subdomain — localStorage is per-origin so
 *    the cookie is the cross-subdomain bridge).
 *  - setItem: localStorage + best-effort cookie write scoped to the parent
 *    domain (keeps the site and admin in sync after refresh flows).
 *  - removeItem: clears both (sign-out).
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

/** Cookie scope — parent-domain (.premiselabs.co) on real hosts, omitted in dev. */
function cookieAttributes(): string[] {
  const parts = ['path=/', 'samesite=lax', 'max-age=31536000'];
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
  document.cookie = `${STORAGE_KEY}=${encodeURIComponent(value)}; ${cookieAttributes().join('; ')}`;
}

function clearCookie(): void {
  document.cookie = `${STORAGE_KEY}=; path=/; max-age=0; ${cookieAttributes().join('; ')}`;
}

const authStorage: SupportedStorage = {
  getItem: (key: string) => {
    try {
      const ls = localStorage.getItem(key);
      if (ls) return ls;
    } catch {
      // localStorage unavailable (private mode / disabled) — cookie fallback below
    }
    return readCookie();
  },
  setItem: (key: string, value: string) => {
    try {
      localStorage.setItem(key, value);
    } catch {
      // ignore — cookie write below still keeps the session readable
    }
    writeCookie(value);
  },
  removeItem: (key: string) => {
    try {
      localStorage.removeItem(key);
    } catch {
      // ignore
    }
    clearCookie();
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
