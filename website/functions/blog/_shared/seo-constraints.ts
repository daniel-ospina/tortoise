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
  if (lastSpace > 0) return cut.slice(0, lastSpace).replace(/[\s,.;:!?—-]+$/, "").trim();
  return cut.replace(/[\s,.;:!?—-]+$/, "").trim();
}

export const constraints: Constraints = {
  truncateAtWord,

  metaTitle(raw: string): string {
    let t = truncateAtWord(raw.trim(), META_TITLE_MAX);
    if (!t) return "";
    // Brand suffix when it fits — meta_title is the FINAL title (SSR renders
    // the stored value verbatim; the suffix is only appended to the title
    // fallback when meta_title is null). Editor maxLength=60 includes the
    // suffix, so this matches the contract.
    const withSuffix = `${t}${BRAND_SUFFIX}`;
    if (withSuffix.length <= META_TITLE_MAX) return withSuffix;
    // No room for the suffix — keep the truncated base (SSR appends nothing).
    return t;
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
