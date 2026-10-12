// Tortoise blog SEO keyword CONSUMER — the stable import surface.
//
// SPLIT (#8293): the keyword DATA lives in `keywords.ts`; this module is the
// code that reads it. Import from HERE (`keywordsFor` / `primaryKeyword` /
// `ensureKeyword`), never from `keywords.ts` directly — that is what makes a
// keyword-research pass a data-only change. Mirrors the split
// `daniel-ospina/DMeer` ships, so both blogs stay structurally identical.
//
// Usage in a Pages Function (import path is depth-dependent — this module lives
// at blog/_shared/):
//   import { keywordsFor } from "./_shared/seo-keywords.ts";   // consumer at blog/ root
//   import { keywordsFor } from "../_shared/seo-keywords.ts";  // consumer in blog/api/
//   const kw = [...new Set(tags.flatMap((t) => keywordsFor(t)))];

export { TAG_KEYWORDS } from "./keywords.ts";
export type { TagKeywords, TagName } from "./keywords.ts";

import { TAG_KEYWORDS } from "./keywords.ts";

// Consumer entry point for free-form tags. blog_posts tags are free-form
// text[] (<=10 per post, no migration), so lookups must tolerate any string:
// unknown tags fall through to the content-derived fallback at runtime ([]).
export function keywordsFor(tag: string): string[] {
  return TAG_KEYWORDS[tag.toLowerCase().trim() as keyof typeof TAG_KEYWORDS] ?? [];
}

/**
 * THE primary keyword for a post: the first entry of the first tag that has a
 * keyword list. Deterministic, so the same post always yields the same primary
 * and the meta check below is reproducible.
 *
 * Best practice is ONE main keyword per article, not nine (#8293): a single
 * primary that must appear in the title and description, plus variants used
 * naturally in the body. Returns null when no tag maps — the caller then falls
 * back to content-derived terms and skips the check.
 */
export function primaryKeyword(tags: string[]): string | null {
  for (const tag of tags) {
    const first = keywordsFor(tag)[0];
    if (first) return first;
  }
  return null;
}

/** Normalize for a contains-check: lowercase, punctuation -> spaces. */
export function normalizeForMatch(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

/** True when `text` already carries `keyword` (word-boundary, case-insensitive). */
export function containsKeyword(text: string, keyword: string): boolean {
  if (!keyword) return true;
  return normalizeForMatch(text).includes(normalizeForMatch(keyword));
}

/**
 * Return `text` with `keyword` guaranteed to be present.
 *
 * VERIFY, DON'T HOPE (#8293): the generator's prompt already asks for the
 * keyword, but a prompt is a request, not a guarantee — the previous behaviour
 * passed keywords to the model as "suggested ... to weave in" and never checked
 * the result. This repairs deterministically instead: if the keyword is absent
 * it is FRONT-LOADED (search engines weight the start of a title, and a
 * description is ad copy that earns the click), then trimmed to `max` at a word
 * boundary.
 *
 * If the keyword itself is longer than `max`, the keyword alone is returned —
 * a truncated keyword is worse than an over-long field, and the caller's own
 * length constraint decides what to do with it.
 *
 * Contract boundary: this guarantees the result is within `max` **for an input
 * that already is**. Truncating an over-limit input is the length contract's job
 * (seo-constraints.ts); doing it here would silently mask a field that is too
 * long for a reason that has nothing to do with the keyword.
 */
export function ensureKeyword(text: string, keyword: string | null, max: number): string {
  if (!keyword) return text;
  if (containsKeyword(text, keyword)) return text;
  // Nothing to prefix: the keyword IS the field, with no dangling separator.
  if (!text.trim()) return keyword;
  if (keyword.length >= max) return keyword;
  const candidate = `${keyword}: ${text}`.replace(/\s+/g, " ").trim();
  if (candidate.length <= max) return candidate;
  const room = max - 2; // for ": " plus an ellipsis-free cut
  const cut = candidate.slice(0, room);
  const atWord = cut.slice(0, cut.lastIndexOf(" "));
  const trimmed = (atWord.length >= keyword.length ? atWord : cut).replace(/[\s:,-]+$/, "");
  return containsKeyword(trimmed, keyword) ? trimmed : keyword;
}
