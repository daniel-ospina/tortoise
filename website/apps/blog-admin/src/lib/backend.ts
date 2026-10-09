/**
 * backend — the BFF-backed Supabase client for the blog admin console.
 *
 * #4178 (parent #3501, SCOPE.md §4 W2): the console used to build its own
 * supabase-js client with a storage adapter keyed on the JS-readable,
 * parent-domain `sb-tortoise-auth-token` cookie (`src/lib/supabase.ts`, deleted
 * with this change). That cookie is a SECOND, non-revocable credential: it rides
 * alongside the BFF session and revocation cannot reach the access token it
 * carries.
 *
 * The credential model now matches every other migrated surface: the browser
 * holds exactly one credential — the HttpOnly, host-only `__Host-session` — and
 * the SERVER mints the Supabase access token. supabase-js is still used for its
 * query builder and Storage client (so the 11 PostgREST operations and their
 * filters are unchanged), but every `rest/v1` and `storage/v1` request is
 * rewritten to the same-origin `/api/sb/*` Token Handler
 * (`website/apps/dashboard/functions/api/sb/[[path]].ts`), which attaches the
 * credential server-side.
 *
 * `SUPABASE_URL` deliberately stays the REAL project origin:
 *   - `getPublicUrl()` derives the stored `cover_image_url` from the client's
 *     own base, and that column is rendered on the PUBLIC blog — pointing the
 *     client at the proxy would store a session-gated app-origin URL there;
 *   - `deleteBlogImage`'s origin guard compares against this origin.
 * Only the two proxied path prefixes are rewritten.
 *
 * `persistSession: false` + `autoRefreshToken: false` + no storage adapter: the
 * client holds no session and never refreshes one. There is nothing to leak and
 * nothing to resurrect after sign-out.
 */

import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import type { Database } from '@/lib/types';

/** Same-origin sign-in page (the console and `/auth` share the app origin). */
export const AUTH_URL = '/auth';

/** The real Supabase project origin — see the note above on why it is NOT the proxy. */
export const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;

if (!SUPABASE_URL) {
  throw new Error(
    'Missing VITE_SUPABASE_URL — copy .env.example to .env (see website/apps/blog-admin/README.md)',
  );
}

/**
 * supabase-js requires A key to construct a client, but it is never a
 * credential here — the proxy strips the client's `apikey`/`Authorization` and
 * sets its own server-side. A placeholder keeps `VITE_SUPABASE_ANON_KEY` out of
 * the browser bundle's requirements (#4178: it must not stay load-bearing).
 */
const PLACEHOLDER_KEY = 'bff-proxy';

/** Same-origin mount point of the Token Handler. */
const PROXY_PREFIX = '/api/sb';

const PROXIED_PREFIXES = ['/rest/v1/', '/storage/v1/'];

/**
 * Rewrite Supabase REST/Storage requests to the same-origin Token Handler,
 * leaving everything else (and `getPublicUrl`, which does no I/O) untouched.
 *
 * supabase-js calls its fetch as `(url, init)`; it is also handed a `Request`
 * in some code paths, so both shapes are handled.
 *
 * Exported for the unit test (`backend.test.ts`) — a module-internal export; it
 * is NOT part of any SDK/MCP surface.
 */
export function proxiedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  let href: string;
  let options = init;
  if (typeof input === 'string') {
    href = input;
  } else if (input instanceof URL) {
    href = input.toString();
  } else {
    href = input.url;
    if (!options) {
      // Preserve the Request's own method/headers/body when no init was given.
      options = {
        method: input.method,
        headers: input.headers,
        body: input.body,
        duplex: 'half',
      } as RequestInit;
    }
  }

  let target: URL;
  let project: URL;
  try {
    target = new URL(href, window.location.origin);
    project = new URL(SUPABASE_URL as string);
  } catch {
    return fetch(input as RequestInfo, options);
  }

  if (
    target.origin === project.origin &&
    PROXIED_PREFIXES.some((prefix) => target.pathname.startsWith(prefix))
  ) {
    return fetch(`${PROXY_PREFIX}${target.pathname}${target.search}`, options);
  }
  return fetch(input as RequestInfo, options);
}

export const bff: SupabaseClient<Database> = createClient<Database>(
  SUPABASE_URL,
  PLACEHOLDER_KEY,
  {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
    global: { fetch: proxiedFetch },
  },
);
