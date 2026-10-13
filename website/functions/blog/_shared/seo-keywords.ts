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
    for (const candidate of keywordsFor(tag)) {
      // A keyword that normalises to nothing carries no term. Returning it would
      // make `containsKeyword` return true unconditionally (it treats an empty
      // needle as "already present") and `ensureKeyword` a silent no-op — the
      // #8320 vacuity, reachable from a DATA-ONLY edit to keywords.ts, which is
      // documented as the single file a keyword-research pass edits (#8446
      // review). Skip it rather than return it.
      if (normalizeForMatch(candidate)) return candidate;
    }
  }
  return null;
}

/**
 * THE primary keyword for a request, given what the CALLER supplied and what
 * the MODEL generated (#8320).
 *
 * The caller's tags win when present — they are an explicit statement of
 * intent. When the caller supplied none (the pipeline's normal shape: title +
 * body), the model's own generated tags are used instead, because otherwise the
 * primary keyword is null for every such call: the prompt has nothing to ask
 * for, the repair has nothing to enforce, and `keyword_ok` would report success
 * while checking nothing.
 *
 * Kept as a named function rather than inlined in the handler so the ordering
 * it encodes is testable without driving the whole HTTP handler.
 */
export function resolvePrimaryKeyword(suppliedTags: string[], generatedTags: string[]): string | null {
  return primaryKeyword(suppliedTags.length > 0 ? suppliedTags : generatedTags);
}

/**
 * Every keyword in the researched vocabulary, deduped, in a stable order.
 *
 * Used to SEED THE PROMPT when the caller supplied no tags (#8445): without it
 * the model is told "no mapped keywords", composes a fluent title with no
 * keyword in it, and the repair then forces one on — producing
 * `"<keyword>: <its own title>"`. The model cannot be asked for a keyword we
 * have not shown it, and it cannot be asked for the TAG-DERIVED one because
 * that is derived from the tags it has not written yet.
 *
 * ⛔ The default is the WHOLE vocabulary, deliberately. `canonicalKeyword` accepts
 * every one of these terms, so a seed list narrower than the validator makes the
 * two disagree: the prompt would present a partial list as the complete choice
 * set ("choose exactly ONE of these researched keywords") while the validator
 * would have accepted a term that was never offered.
 *
 * Order is ROUND-ROBIN ACROSS TAGS, not tag-then-keywords. `TAG_KEYWORDS` is a
 * plain object, so insertion order decided which tags survived a cap — with the
 * previous `limit = 60`, the first two tags consumed it and nine of the twelve
 * contributed nothing (#8446 review). A cap is still supported for callers that
 * want one, and round-robin makes any cap fair across tags instead of starving
 * whichever tags happen to be declared last.
 */
export function allKeywords(limit = Number.POSITIVE_INFINITY): string[] {
  const cap = Number.isFinite(limit) && limit > 0 ? Math.floor(limit) : Number.POSITIVE_INFINITY;
  const lists = Object.keys(TAG_KEYWORDS).map((tag) => keywordsFor(tag));
  const out: string[] = [];
  const seen = new Set<string>();
  for (let depth = 0; out.length < cap; depth++) {
    let sawAny = false;
    for (const list of lists) {
      if (depth >= list.length) continue;
      sawAny = true;
      const kw = list[depth];
      const norm = normalizeForMatch(kw);
      if (!norm || seen.has(norm)) continue;
      seen.add(norm);
      out.push(kw);
      if (out.length >= cap) return out;
    }
    if (!sawAny) break;
  }
  return out;
}

/**
 * The researched spelling of `keyword`, or null when it is not one of ours.
 *
 * The model returns its own spelling (`"LONG-TERM MEMORY FOR AI AGENTS"`,
 * `"Long  Term  Memory  for  AI  Agents"`), and the vocabulary check deliberately
 * normalises. But `primary_keyword` is documented as "the keyword the response is
 * verified against" and callers compare it against `TAG_KEYWORDS`/tags, so the
 * UN-normalised claim would not match anything the caller can look up — the same
 * article could return two different strings for one keyword (#8446 review).
 * Canonicalising here also bounds the value: a vocabulary term is at most
 * `TAG_LEN_MAX` characters, so no separate length guard is needed at the call
 * site.
 */
export function canonicalKeyword(keyword: string): string | null {
  const norm = normalizeForMatch(keyword);
  if (!norm) return null;
  for (const tag of Object.keys(TAG_KEYWORDS)) {
    for (const kw of keywordsFor(tag)) {
      if (normalizeForMatch(kw) === norm) return kw;
    }
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
  const needle = normalizeForMatch(keyword);
  // A keyword that normalises to nothing (punctuation only) carries no term.
  if (!needle) return true;
  // WORD-BOUNDARY, not substring (#8293). Normalisation maps every
  // non-alphanumeric run to a single space, and `needle` is therefore
  // [a-z0-9 ] only — no regex metacharacters — so matching on `\b` is exact:
  // "agent memory" matches "agent-memory systems", but NOT "agents memory"
  // or "xagent memory". The claim is "verbatim", so the check must be too.
  return new RegExp(`\\b${needle}\\b`).test(normalizeForMatch(text));
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
  // The strip class includes `|` (#8293): the brand suffix (" | Tortoise") that
  // `constraints.metaTitle` appends is introduced by `|`, so a cut that lands
  // inside the suffix used to survive as a dangling " |" — a broken title the
  // caller then stored verbatim. Every separator the code itself can emit is
  // stripped, not just the ones the old class happened to list.
  const trimmed = (atWord.length >= keyword.length ? atWord : cut).replace(/[\s:,|\-]+$/, "");
  return containsKeyword(trimmed, keyword) ? trimmed : keyword;
}
