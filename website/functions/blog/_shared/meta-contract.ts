// Blog SEO field-length contract — SINGLE SOURCE OF TRUTH (#1866).
// Consumed by the agent API (posts.ts) and the AI SEO generator
// (generate-seo.ts / seo-constraints.ts). The blog-admin SPA is a separate
// app and cannot import from functions/ — its literals (PostEditor maxLength
// attrs, blog-api MAX_TAGS) are locked to these values by a cross-check test.
// ZERO-DEPENDENCY (plain TS, no imports).
//
// The " | Tortoise" brand suffix is appended at SSR time to the meta title;
// meta_title here is the pre-suffix field the editor/API store.

export const META_TITLE_MAX = 60; // + " | Tortoise" suffix at SSR (SSR only)
export const META_DESCRIPTION_MAX = 155;
export const EXCERPT_MAX = 300;
export const TAGS_MAX = 10;
export const TAG_LEN_MAX = 40;
export const SLUG_MAX = 100;
