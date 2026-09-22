/**
 * useAuth — session gate for the blog admin SPA.
 *
 * #3501: rewritten for the BFF. It previously called
 * `supabase.auth.getSession()` and `supabase.rpc('is_admin')`, both of which
 * required a JS-readable token. Under the BFF the session cookie is HttpOnly, so
 * there is no token to read and no Supabase client to ask.
 *
 * `isAdmin` is now inferred rather than re-checked client-side: this SPA is only
 * ever served by `apps/dashboard/functions/admin/[[path]].ts`, which has ALREADY
 * enforced the blog_admins allowlist and answers a non-admin with 403 before any
 * asset is returned. Re-asking from the client would require handing the browser
 * a token — reintroducing exactly the exposure this redesign removes. RLS remains
 * the real authorization boundary; this is the UX gate.
 *
 * Redirect policy (the #3485 rule): redirect on 401 ONLY.
 * A 503 means the session store is unreachable — redirecting then would log the
 * user out because of a transient fault, and their retry would loop.
 */

import { useEffect, useState } from 'react';
import { AUTH_URL } from '@/lib/supabase';
import { fetchSession, type SessionInfo } from '@/lib/session';

interface AuthState {
  loading: boolean;
  session: SessionInfo | null;
  isAdmin: boolean;
  /** True when the session store could not be reached (retryable, NOT signed out). */
  unavailable: boolean;
}

const INITIAL: AuthState = { loading: true, session: null, isAdmin: false, unavailable: false };

/**
 * #3080: carry the current console path to /auth so sign-in returns HERE.
 *
 * PATHNAME only — parity with the server gate's returnToPath(), which also drops
 * the query. /auth rejects a `next` containing ':' or '\\' anywhere, so sending
 * pathname+search would silently drop the return-to for any admin URL carrying
 * such a query (e.g. ?t=12:00) and re-login would land on the app root.
 */
function authUrlWithReturn(): string {
  return `${AUTH_URL}?next=${encodeURIComponent(window.location.pathname)}`;
}

export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>(INITIAL);

  useEffect(() => {
    let cancelled = false;

    async function check() {
      const result = await fetchSession();
      if (cancelled) return;

      if (result.state === 'authenticated') {
        setState({ loading: false, session: result.session, isAdmin: true, unavailable: false });
        return;
      }

      if (result.state === 'anonymous') {
        setState({ loading: false, session: null, isAdmin: false, unavailable: false });
        window.location.replace(authUrlWithReturn());
        return;
      }

      // unavailable — deliberately NO redirect. Show the retryable state instead
      // of signing the user out over a transient fault.
      setState({ loading: false, session: null, isAdmin: false, unavailable: true });
    }

    void check();

    // No onAuthStateChange subscription: there is no client-side session to
    // change. Re-check on tab focus, which is the honest signal that something
    // may have happened elsewhere.
    function onFocus() {
      void check();
    }
    window.addEventListener('focus', onFocus);

    return () => {
      cancelled = true;
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  return state;
}
