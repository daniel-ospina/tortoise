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
// The `+` makes a repeated suffix ("… | Tortoise | Tortoise") collapse to one.
//
// ⛔ THE SUFFIX-ONLY CONTRACT — a DELIBERATE BEHAVIOUR CHANGE, measured, not a
// consistency cleanup. A title that is NOTHING BUT a brand tail returns "", and
// `generate-seo.ts` answers 502 `generation_incomplete` (it repairs only a
// TRUTHY meta_title, so an emptied one short-circuits).
//
//   input            parent 53a139f73   this head
//   "| Tortoise"     ""  -> 502         ""  -> 502
//   "]Tortoise_"     "]Tortoise | Tortoise" -> 200   "" -> 502
//
// So 502-on-suffix-only was ALREADY the contract for the non-`_` spellings, and
// `\b` merely EXEMPTED the `_` ones — by emitting a garbage title, which the
// keyword repair then overwrote into `"<keyword> | Tortoise"`. The change makes
// every spelling take the 502 path. That is the intent: a model that produced no
// usable title should be regenerated, not have the repair write one for it (which
// is the #8293 "the repair must not invent a keyword-only field" shape the parent
// was exhibiting here). Reversible — restoring recovery means teaching
// `generate-seo.ts` to repair an EMPTIED title, a change with its own review.
// ⛔ `metaTitle` is idempotent at THIS head: the post-truncate strip re-runs the
// full suffix pattern, so a tail that truncation EXPOSES is removed in the same
// pass and a second pass finds nothing. (`generate-seo.ts` does not depend on the
// property either way — it measures whether the REPAIR fired directly.) Do not
// reintroduce a second `enforce` pass as the source of `repairFired`.
const BRAND_SUFFIX_TAIL_RE = /(?:\s*[\p{P}\p{S}]\s*Tortoise[\p{P}\p{S}\s]*)+$/iu;

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
    // Stripping before appending removes the old `endsWith` shortcut's
    // double-append for every spelling rather than the exact one.
    // ⛔ STRIP FIRST, then truncate — the reverse order loses the suffix match.
    // `BRAND_SUFFIX_TAIL_RE` requires the COMPLETE word `Tortoise\b`, so if the
    // 60-char cut lands inside the suffix the fragment is left behind and neither
    // strip can touch it: `"a"×58 + "|Tortoise"` came back as `"a"×58 + "|T"`,
    // and through the handler `"epistemic memory guide|Tortois"` was returned as
    // `"…|Tortois | Tortoise"` beside `keyword_ok: true` — the round-6/7 symptom
    // again, this time from a partial token the model wrote. Stripping first means
    // the suffix is gone before the cut can split it, and the cut then only ever
    // sees the model's own prose.
    // ⛔ The trailing-separator strip lives on `fitted`, NOT here — one copy, and
    // the one that matters. `truncateAtWord` returns its input UNCHANGED when it
    // already fits, so only the post-truncate strip covers the common short-title
    // case; a second copy on `stripped` was redundant (deleting it left the suite
    // green, which is how the duplication was found in round 10).
    // ⛔ STRIP — TRUNCATE — STRIP AGAIN. Both strips are load-bearing and they
    // fix OPPOSITE halves of the same problem:
    //   · before truncating, so a cut can never SPLIT the suffix (a fragment like
    //     `"a"×58 + "|T"` cannot be matched back);
    //   · after truncating, so a cut can never EXPOSE a mid-title punctuation-led
    //     "Tortoise" as a brand-looking tail ("… knowledge graphs —  Tortoise
    //     explained" cut to "… knowledge graphs —  Tortoise").
    // Doing only the first leaked that exposed tail; doing only the second leaked
    // the split fragment. Together they leave a result that ends in neither, which
    // is also what makes the function fully idempotent again — a second pass finds
    // no trailing suffix to remove.
    const stripped = raw
      .trim()
      .replace(BRAND_SUFFIX_TAIL_RE, "")
      .trim();
    // ⛔ This class is SEPARATORS ONLY — deliberately NOT `[\p{P}\p{S}]`. A class
    // that broad DELETES legitimate terminal content, because it cannot tell a
    // separator from a closing bracket, a percent sign or an emoji:
    //   "What is a knowledge graph (KG)" -> "…(KG | Tortoise"  (unbalanced)
    //   "Improve recall by 40%"          -> "…40 | Tortoise"
    //   "Agent memory 🧠"                 -> "Agent memory"
    // These are user-visible in the SERP and the <title>, and the suffix strip's
    // argument does NOT carry over: that one matches a brand TOKEN (so it can be
    // class-based), this one strips whatever sits at the end.
    // One suffix strip here, not two: with `(?![A-Za-z])` and a class that consumes
    // `_`, the regex already matches a trailing run in full, so a re-strip after the
    // separator strip is unreachable — and an unreachable clause reads as coverage
    // while providing none (deleting it left the suite green).
    const fitted = truncateAtWord(stripped, META_TITLE_MAX)
      .replace(BRAND_SUFFIX_TAIL_RE, "")
      .replace(/[\s,.;:!?|\u00b7\u2022—–-]+$/, "")
      .trim();
    if (!fitted) return "";
    // Brand suffix when it fits — meta_title is the FINAL title (SSR renders the
    // stored value verbatim; the suffix is only appended to the title fallback
    // when meta_title is null). Editor maxLength=60 includes the suffix, so this
    // matches the contract.
    const withSuffix = `${fitted}${BRAND_SUFFIX}`;
    if (withSuffix.length <= META_TITLE_MAX) return withSuffix;
    // No room for the suffix — keep the base (SSR appends nothing). `fitted` is
    // the STRIPPED text, already truncated, with every trailing separator
    // removed, so a title ending in a bare "|" — user-visible in the SERP and
    // the <title> tag — cannot escape.
    return fitted;
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
