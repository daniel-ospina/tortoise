// AI SEO constraint enforcement (#1861) — pure, zero-dep module.
// Server-side contract enforcement for AI-generated SEO fields. Mirrors the
// editor + agent-API contract exactly (see _shared/meta-contract.ts). Kept
// pure (no I/O) so it is unit-testable with a fixture corpus.
//
// ZERO-DEPENDENCY (plain TS, no imports).

import {
  META_TITLE_MAX,
  META_DESCRIPTION_MAX,
  EXCERPT_MAX,
  TAGS_MAX,
  TAG_LEN_MAX,
} from "./meta-contract.ts";
import { SLUG_MAX } from "./slug.ts";

export const BRAND_SUFFIX = " | Tortoise";

// ⛔ A REPEATING, punctuation-insensitive match for any brand-suffix spelling the
// model may have emitted, so the suffix is appended exactly once.
//
// Two earlier versions were wrong, and each was caught only by a review round:
//   1. `t.endsWith(BRAND_SUFFIX)` matched the exact string only, so `"|Tortoise"`
//      (no space) got a SECOND suffix — 92% of a near-miss matrix.
//   2. `/[\s|—–-]*[|—–-]\s*Tortoise\.?\s*$/i` allowed at most ONE trailing dot and
//      a fixed separator set, so `"| Tortoise.."`, `"| Tortoise!"`, `": Tortoise"`,
//      `"· Tortoise"` and an already-doubled title all still doubled (68%).
//
//   3. An explicit character list (`[|—–-·:•]`, then `[\s.,;:!?…·]*`) STILL only
//      covered the characters I happened to think of: `","` as a separator and
//      `"•"`, `")"`, `"]"`, `"}"`, `"”"`, `"*"`, `"/"`, and a trailing `-`/`–`/`—`
//      all doubled (21.2% of a 74,520-spelling matrix), so the fix kept failing
//      the same way one round later. Enumerating characters is the bug.
//
// Hence `[\p{P}\p{S}]`: ANY Unicode punctuation or symbol, in any run, on either
// side — a CLASS, not a list. A bare space is still NOT a separator, so a title
// that legitimately ends in the word ("Why Tortoise") is left alone, while every
// punctuation-led spelling and its repetitions are treated as the brand suffix.
// The `+` keeps it idempotent, which matters because `generate-seo.ts` enforces,
// repairs, then enforces AGAIN — a first pass that left a suffix behind would be
// appended to by the second, and the caller would receive a title that differs
// from the one `keyword_ok` was computed on.
const BRAND_SUFFIX_TAIL_RE = /(?:\s*[\p{P}\p{S}]\s*Tortoise\b[\p{P}\p{S}\s]*)+$/iu;

export interface SeoFields {
  slug: string;
  excerpt: string;
  tags: string[];
  meta_title: string;
  meta_description: string;
}

export interface Constraints {
  /** Truncate to max at a word boundary (never mid-word). */
  truncateAtWord(s: string, max: number): string;
  /** Enforce the meta_title contract: ≤60 + brand suffix when it fits. */
  metaTitle(raw: string): string;
  metaDescription(raw: string): string;
  excerpt(raw: string): string;
  /** Normalize + cap tags: lowercase alphanumeric-dash, ≤10, each ≤40. */
  tags(raw: string[]): string[];
  /** Slug: lowercase alphanumeric-dash, ≤100; re-slugify when invalid. */
  slug(raw: string, title?: string): string;
  /** Full pass over an AI-generated result — returns the enforced fields. */
  enforce(fields: Partial<SeoFields>, title?: string): SeoFields;
}

function truncateAtWord(s: string, max: number): string {
  if (s.length <= max) return s;
  const cut = s.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  // Always cut at the last word boundary when one exists (the old heuristic
  // kept the hard cut when the last space fell in the first half — mid-word).
  if (lastSpace > 0) return cut.slice(0, lastSpace).replace(/[\s,.;:!?|—-]+$/, "").trim();
  return cut.replace(/[\s,.;:!?|—-]+$/, "").trim();
}

export const constraints: Constraints = {
  truncateAtWord,

  metaTitle(raw: string): string {
    // ⛔ Strip ANY brand-suffix spelling the model may have emitted, then append
    // the canonical one ONCE. `t.endsWith(BRAND_SUFFIX)` recognised only the
    // exact string " | Tortoise", so any near-miss — "|Tortoise", "| tortoise",
    // "  |  Tortoise", "| Tortoise." — failed the check and got a SECOND suffix
    // appended, user-visible in the SERP and the <title> tag as
    // "… |Tortoise | Tortoise". Measured at 92% of green verdicts across a
    // near-miss suffix matrix (#8446 review).
    //
    // A separator is REQUIRED before the word, so a title that legitimately ends
    // in it ("Why Tortoise") is left alone; only "… |… Tortoise" is treated as the
    // brand suffix.
    //
    // Stripping BEFORE appending also makes the function idempotent in general,
    // which the `endsWith` shortcut achieved only for the exact spelling: the old
    // version could strip a trailing separator via `.trim()` and then append on a
    // SECOND pass, defeating its own `enforce(enforce(x)) === enforce(x)` claim in
    // ~0.16% of random inputs.
    const stripped = truncateAtWord(raw.trim(), META_TITLE_MAX)
      .replace(BRAND_SUFFIX_TAIL_RE, "")
      .replace(/[\s,.;:!?|—–-]+$/, "")
      .trim();
    if (!stripped) return "";
    // Brand suffix when it fits — meta_title is the FINAL title (SSR renders the
    // stored value verbatim; the suffix is only appended to the title fallback
    // when meta_title is null). Editor maxLength=60 includes the suffix, so this
    // matches the contract.
    const withSuffix = `${stripped}${BRAND_SUFFIX}`;
    if (withSuffix.length <= META_TITLE_MAX) return withSuffix;
    // No room for the suffix — keep the base (SSR appends nothing). `stripped`
    // has already had every trailing separator removed, so a title ending in a
    // bare "|" — user-visible in the SERP and the <title> tag — cannot escape.
    return stripped;
  },

  metaDescription(raw: string): string {
    return truncateAtWord(raw.trim(), META_DESCRIPTION_MAX);
  },

  excerpt(raw: string): string {
    return truncateAtWord(raw.trim(), EXCERPT_MAX);
  },

  tags(raw: string[]): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const t of raw) {
      if (out.length >= TAGS_MAX) break;
      const norm = t
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9-]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, TAG_LEN_MAX);
      if (!norm || seen.has(norm)) continue;
      seen.add(norm);
      out.push(norm);
    }
    return out;
  },

  slug(raw: string, title?: string): string {
    const candidate = raw || title || "";
    const slug = candidate
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[^\w\s-]/g, "")
      .replace(/[\s_]+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, SLUG_MAX);
    return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) ? slug : slugifyFallback(candidate);
  },

  enforce(fields: Partial<SeoFields>, title?: string): SeoFields {
    return {
      slug: this.slug(fields.slug ?? "", title),
      excerpt: this.excerpt(fields.excerpt ?? ""),
      tags: this.tags(fields.tags ?? []),
      meta_title: this.metaTitle(fields.meta_title ?? ""),
      meta_description: this.metaDescription(fields.meta_description ?? ""),
    };
  },
};

/** Slug fallback when the raw candidate cannot produce a valid slug. */
function slugifyFallback(text: string): string {
  // Deterministic hash-suffixed fallback so the slug is always valid + unique-ish.
  const base = text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^\w\s-]/g, "")
    .replace(/[\s_]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX - 8);
  const h = hash8(text);
  return base ? `${base}-${h}` : `post-${h}`;
}

function hash8(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return (h >>> 0).toString(36).padStart(8, "0").slice(0, 8);
}
