/**
 * useAuth — session + is_admin() gate for the blog admin SPA.
 *
 * Reads the session via supabase-js (storage adapter keyed on the
 * sb-tortoise-auth-token parent-domain cookie). If there is no session, or
 * the user is not in the blog_admins allowlist (is_admin() RPC), redirect to
 * the site's auth page. RLS remains the real authorization boundary — this
 * is the client-side UX gate + fail-closed redirect.
 */

import { useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase, AUTH_URL } from '@/lib/supabase';

interface AuthState {
  loading: boolean;
  session: Session | null;
  isAdmin: boolean;
}

const INITIAL: AuthState = { loading: true, session: null, isAdmin: false };

export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>(INITIAL);

  useEffect(() => {
    let cancelled = false;

    async function check() {
      try {
        const { data } = await supabase.auth.getSession();
        if (cancelled) return;

        if (!data.session) {
          window.location.replace(AUTH_URL);
          setState({ loading: false, session: null, isAdmin: false });
          return;
        }

        let isAdmin = false;
        try {
          const { data: admin } = await supabase.rpc('is_admin');
          isAdmin = admin === true;
        } catch {
          isAdmin = false;
        }

        if (!isAdmin) {
          // Valid session but not an admin — do not leak the admin surface.
          window.location.replace(AUTH_URL);
        }
        setState({ loading: false, session: data.session, isAdmin });
      } catch {
        if (!cancelled) {
          window.location.replace(AUTH_URL);
          setState({ loading: false, session: null, isAdmin: false });
        }
      }
    }

    void check();

    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      if (cancelled) return;
      if (!session) {
        window.location.replace(AUTH_URL);
        setState({ loading: false, session: null, isAdmin: false });
      } else {
        // Session refreshed/restored — re-run the admin gate.
        void check();
      }
    });

    return () => {
      cancelled = true;
      sub.subscription.unsubscribe();
    };
  }, []);

  return state;
}
