// Shared slug contract for server-side blog functions (#1863) — zero-dep.
// Mirrors the editor's isValidSlug (blog-api.ts) + SLUG_RE from _lib.ts.
// ZERO-DEPENDENCY (plain TS, no imports).

export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const SLUG_MAX = 100;

export function isValidSlug(slug: string): boolean {
  return SLUG_RE.test(slug) && slug.length <= SLUG_MAX;
}

// ── Anchor allocation for rendered headings (#8294) ───────────────────────
/**
 * Hand out unique anchor ids for a document's headings.
 *
 * The suffix cursor RESUMES per base, so a duplicate costs O(1) amortised
 * instead of rescanning from `-1` on every call. The rescanning form was O(N²):
 * 8 000 duplicate `## A` headings took ≈24 s (16 000 ≈131 s) — a CPU blowup
 * reachable from any post body, well under `BODY_MAX = 100_000`.
 *
 * It lives in `_shared/` rather than `_lib.ts` deliberately: `_lib.ts` is an
 * HTML-producing file, and the CSP stamp guard counts a `map.set(<computed
 * key>, …)` call there as an UNATTRIBUTABLE header stamp. That over-capture is
 * a named, accepted residual of the guard
 * (docs/plans/2026-09-22-3525-csp-no-store.md §Residuals, which names
 * `dailyCounts.set(userId, hits)`), and its remedy is code-level — so the
 * allocation lives here instead of the guard being weakened.
 *
 * State belongs to one allocator instance, so `_lib.ts` mints one per render —
 * no cross-render leakage.
 */
export function createAnchorAllocator(): (base: string) => string {
  const issued = new Set<string>();
  const nextSuffix = new Map<string, number>();
  return (base: string): string => {
    let n = nextSuffix.get(base) ?? 0;
    let candidate = n === 0 ? base : `${base}-${n}`;
    while (issued.has(candidate)) candidate = `${base}-${++n}`;
    issued.add(candidate);
    nextSuffix.set(base, n);
    return candidate;
  };
}
