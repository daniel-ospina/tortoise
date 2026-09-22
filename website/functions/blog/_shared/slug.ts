// Shared slug contract for server-side blog functions (#1863) — zero-dep.
// Mirrors the editor's isValidSlug (blog-api.ts) + SLUG_RE from _lib.ts.
// ZERO-DEPENDENCY (plain TS, no imports).

export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const SLUG_MAX = 100;

export function isValidSlug(slug: string): boolean {
  return SLUG_RE.test(slug) && slug.length <= SLUG_MAX;
}
