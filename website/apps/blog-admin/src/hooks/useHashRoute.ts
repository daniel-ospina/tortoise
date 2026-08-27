/**
 * useHashRoute — minimal hash router for the admin SPA.
 *
 * Routes (hash-based so it works behind any /admin/* server path served by
 * the admin gate Function):
 *   #/            → posts list (review queue)
 *   #/new         → new post editor
 *   #/edit/:id    → edit post editor
 *   #/audit       → audit view
 */

import { useEffect, useState } from 'react';

export interface HashRoute {
  path: string;
  segments: string[];
}

function parseHash(): HashRoute {
  const raw = window.location.hash.replace(/^#/, '');
  const segments = raw.split('/').filter(Boolean);
  return { path: raw || '/', segments };
}

export function useHashRoute(): HashRoute {
  const [route, setRoute] = useState<HashRoute>(parseHash);

  useEffect(() => {
    const onHashChange = () => setRoute(parseHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  return route;
}

export function navigate(hash: string): void {
  window.location.hash = hash;
}
