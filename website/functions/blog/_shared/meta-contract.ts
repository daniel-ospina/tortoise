// Blog SEO field-length contract — SINGLE SOURCE OF TRUTH (#1866).
// Consumed by the agent API (posts.ts) and the AI SEO generator
// (generate-seo.ts / seo-constraints.ts). The blog-admin SPA is a separate
// app and cannot import from functions/ — its literals (PostEditor maxLength
// attrs, blog-api MAX_TAGS) are locked to these values by a cross-check test.
// ZERO-DEPENDENCY (plain TS, no imports).
//
// meta_title is the FINAL search-result title (≤60 chars), optionally
// including the " | Tortoise" brand suffix. SSR renders the stored value
// verbatim (`post.meta_title ?? \`${title} | Tortoise\``); the suffix is
// appended to the fallback title only. The AI generator (seo-constraints)
// appends the suffix when it fits (matching the editor's maxLength=60
// incl-suffix contract); manually-entered values are stored as typed.

export const META_TITLE_MAX = 60; // + " | Tortoise" suffix at SSR (SSR only)
export const META_DESCRIPTION_MAX = 155;
export const EXCERPT_MAX = 300;
export const TAGS_MAX = 10;
export const TAG_LEN_MAX = 40;
// NOTE: SLUG_RE/SLUG_MAX live in _shared/slug.ts (the slug contract's home).
