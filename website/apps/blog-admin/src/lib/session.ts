/**
 * Session client — the ONLY way the SPA learns about the session.
 *
 * Replaces `supabase.auth.getSession()` everywhere. Under the BFF the session
 * cookie is HttpOnly, so the browser cannot read it and `supabase-js` has
 * nothing to read. The server is the single source of truth.
 *
 * The 401/503 distinction is load-bearing and must not be flattened:
 *   - 401  -> genuinely signed out. It is correct to redirect to /auth.
 *   - 503  -> session store unreachable. It is NOT correct to redirect, because
 *             that turns a transient fault into a sign-out, and the retry turns
 *             it into the #3485 login loop.
 * Any caller that redirects on "not 200" reintroduces that bug.
 */

export interface SessionUser {
  id: string;
  email?: string;
}

export interface SessionInfo {
  user: SessionUser;
  expiresAt: number;
}

export type SessionResult =
  | { state: 'authenticated'; session: SessionInfo }
  | { state: 'anonymous' }
  | { state: 'unavailable'; detail: string };

/** Read the current session from the server. Never throws. */
export async function fetchSession(): Promise<SessionResult> {
  let res: Response;
  try {
    res = await fetch('/api/session', {
      method: 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
  } catch (err) {
    // Network failure is "unavailable", never "anonymous".
    return { state: 'unavailable', detail: err instanceof Error ? err.message : 'network error' };
  }

  if (res.status === 200) {
    const body = (await res.json()) as SessionInfo;
    return { state: 'authenticated', session: body };
  }
  if (res.status === 401) return { state: 'anonymous' };
  return { state: 'unavailable', detail: `HTTP ${res.status}` };
}

/**
 * Authenticated fetch. The cookie rides along automatically, so there is no
 * token to attach — and therefore no token to leak.
 */
export function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { ...init, credentials: 'same-origin' });
}

/** POST /api/session — revoke server-side, then the cookie is cleared. */
export async function signOut(): Promise<void> {
  try {
    await fetch('/api/session', { method: 'POST', credentials: 'same-origin' });
  } catch {
    /* best-effort: the cookie may be cleared client-side regardless */
  }
}

/** Begin a flow. Always goes through /auth/start so the flow id is bound. */
export function startSignIn(next?: string): void {
  const q = new URLSearchParams({ kind: 'signin' });
  if (next) q.set('next', next);
  window.location.assign(`/auth/start?${q.toString()}`);
}
