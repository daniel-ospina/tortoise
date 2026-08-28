/**
 * seo-ai.test.ts — unit tests for the server-side AI SEO constraint
 * enforcement (#1861) + edge-cache purge helper (#1865) + meta contract (#1866).
 *
 * Imports the zero-dep modules from website/functions/blog/_shared/ directly
 * (pure TS, no runtime deps). The meta-contract cross-check locks the editor's
 * literals to the shared constants (single source of truth, #1866).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── #1861: seo-constraints fixture corpus (≥10 posts) ─────────────────────
import { constraints } from '../../../../functions/blog/_shared/seo-constraints.ts';
import {
  META_TITLE_MAX, META_DESCRIPTION_MAX, EXCERPT_MAX, TAGS_MAX, TAG_LEN_MAX,
} from '../../../../functions/blog/_shared/meta-contract.ts';

// Contract lock (#1866): the editor SPA's literals must equal the shared
// server-side constants. If this fails, the editor and API have drifted.
const EDITOR_META_TITLE_MAX = 60; // PostEditor.tsx maxLength
const EDITOR_META_DESCRIPTION_MAX = 155; // PostEditor.tsx maxLength
const EDITOR_EXCERPT_MAX = 300; // PostEditor.tsx maxLength
const EDITOR_MAX_TAGS = 10; // blog-api.ts MAX_TAGS

describe('meta contract (#1866) — single source of truth', () => {
  it('shared constants match the editor literals', () => {
    expect(META_TITLE_MAX).toBe(EDITOR_META_TITLE_MAX);
    expect(META_DESCRIPTION_MAX).toBe(EDITOR_META_DESCRIPTION_MAX);
    expect(EXCERPT_MAX).toBe(EDITOR_EXCERPT_MAX);
    expect(TAGS_MAX).toBe(EDITOR_MAX_TAGS);
    expect(TAG_LEN_MAX).toBe(40);
  });
});

describe('seo-constraints (#1861) — enforce()', () => {
  const fixtures = [
    { title: 'Graph vs vector memory: why agents forget', body: 'A deep dive into context rot and the drift debate. '.repeat(40) },
    { title: 'Epistemic memory: claims with confidence', body: 'Belief propagation, provenance, and the why behind agent memory. '.repeat(30) },
    { title: 'Self-hosting Tortoise memory engine', body: 'Docker, FalkorDB, and your own belief graph. '.repeat(25) },
    { title: 'MCP memory server: the 2026 stack', body: 'Model Context Protocol servers for agent long-term memory. '.repeat(35) },
    { title: 'RAG vs knowledge graph for agents', body: 'Vectors, graphs, and the retrieval frontier. '.repeat(28) },
    { title: 'Agent memory without drift', body: 'The stale-200 problem, context rot, and self-correcting memory. '.repeat(32) },
    { title: 'Memory that learns: self-evolving agents', body: 'Continual learning and runtime memory updates. '.repeat(26) },
    { title: 'Belief graph database: a new category', body: 'Confidence scores, NAND edges, and evidence propagation. '.repeat(29) },
    { title: 'Why AI agents get dumber over time', body: 'Summarization drift and the case for structured memory. '.repeat(31) },
    { title: 'Autonomous agents need durable memory', body: 'Autonomy, supervision, and memory instead of model scale. '.repeat(33) },
    { title: 'Tortoise vs Mem0 vs Graphiti', body: 'A comparison of agent memory platforms in 2026. '.repeat(27) },
    { title: 'Semantic vs episodic memory for agents', body: 'What agents remember and how they use it. '.repeat(22) },
  ];

  it('produces 0 constraint violations across the fixture corpus', () => {
    for (const f of fixtures) {
      const result = constraints.enforce({
        slug: f.title,
        excerpt: f.body.slice(0, 200),
        tags: ['Agent Memory', 'graph', 'Graph', 'MEMORY', 'provenance', 'mcp', 'retrieval', 'belief', 'sessions', 'episodic', 'semantic'],
        meta_title: f.title,
        meta_description: f.body.slice(0, 180),
      }, f.title);
      expect(result.meta_title.length).toBeLessThanOrEqual(META_TITLE_MAX);
      expect(result.meta_description.length).toBeLessThanOrEqual(META_DESCRIPTION_MAX);
      expect(result.excerpt.length).toBeLessThanOrEqual(EXCERPT_MAX);
      expect(result.tags.length).toBeLessThanOrEqual(TAGS_MAX);
      expect(result.tags.length).toBeGreaterThan(0);
      for (const t of result.tags) {
        expect(t.length).toBeLessThanOrEqual(TAG_LEN_MAX);
        expect(t).toMatch(/^[a-z0-9-]+$/);
      }
      expect(result.slug).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
      expect(result.slug.length).toBeLessThanOrEqual(100);
    }
  });

  it('word-boundary truncates long values', () => {
    const long = 'word '.repeat(100); // 500 chars
    const r = constraints.enforce({ meta_title: long, meta_description: long, excerpt: long, slug: 'a', tags: [] });
    expect(r.meta_title.length).toBeLessThanOrEqual(META_TITLE_MAX);
    expect(r.meta_description.length).toBeLessThanOrEqual(META_DESCRIPTION_MAX);
    expect(r.excerpt.length).toBeLessThanOrEqual(EXCERPT_MAX);
    // Truncation is at a word boundary — never mid-word
    expect(r.meta_description.endsWith(' ')).toBe(false);
    expect(r.meta_description).not.toMatch(/[a-z]\d/);
  });

  it('truncates at the last space even when it falls in the first half', () => {
    // 'a b' + long tail — last space is early; must still cut at the word
    // boundary, not mid-word (review fix: old heuristic hard-cut mid-word).
    const hard = `short ${'y'.repeat(200)}`; // last space in first half
    const r = constraints.enforce({ meta_title: hard, meta_description: hard, excerpt: hard, slug: 'a', tags: [] });
    expect(r.meta_description.length).toBeLessThanOrEqual(META_DESCRIPTION_MAX);
    expect(r.meta_description).toBe('short'); // cut at the space, no partial word
  });

  it('caps + normalizes tags', () => {
    const r = constraints.enforce({
      slug: 's', excerpt: 'e', tags: ['A', 'A', 'b-c', '  spaced  ', '!@#', '1'.repeat(45), 'final'],
      meta_title: 't', meta_description: 'd',
    });
    // 'A' deduped → 'a'; '!@#' → empty (dropped); '1'.repeat(45) sliced to 40.
    expect(r.tags).toEqual(['a', 'b-c', 'spaced', '1'.repeat(40), 'final']);
    expect(r.tags.every(t => t.length <= TAG_LEN_MAX)).toBe(true);
  });

  it('brand suffix fits when there is room', () => {
    const r = constraints.enforce({ slug: 's', excerpt: 'e', tags: [], meta_title: 'Short title', meta_description: 'd' });
    expect(r.meta_title.endsWith(' | Tortoise')).toBe(true);
    expect(r.meta_title.length).toBeLessThanOrEqual(META_TITLE_MAX);
  });
});

// ── #1865: cloudflare-purge helper ─────────────────────────────────────────
describe('cloudflare-purge (#1865)', () => {
  let purgeUrl: typeof import('../../../../functions/blog/_shared/cloudflare-purge.ts').purgeUrl;
  let originalFetch: typeof fetch;

  beforeEach(async () => {
    originalFetch = globalThis.fetch;
    const mod = await import('../../../../functions/blog/_shared/cloudflare-purge.ts');
    purgeUrl = mod.purgeUrl;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('sends purge-by-URL with both variants and returns true on success', async () => {
    const calls: Array<{ url: string; body: string }> = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: String(init?.body) });
      return new Response(JSON.stringify({ success: true }), { status: 200 });
    }) as unknown as typeof fetch;

    const ok = await purgeUrl('https://tortoise.premiselabs.co/blog/my-post', {
      CF_API_TOKEN: 'token', CF_ZONE_ID: 'zone',
    });
    expect(ok).toBe(true);
    expect(calls.length).toBe(1);
    expect(calls[0].url).toContain('/zones/zone/purge_cache');
    const files = JSON.parse(calls[0].body).files as string[];
    expect(files).toContain('https://tortoise.premiselabs.co/blog/my-post');
    expect(files).toContain('https://tortoise.premiselabs.co/blog/my-post/');
  });

  it('fails open (false) when not configured — never throws', async () => {
    const ok = await purgeUrl('https://tortoise.premiselabs.co/blog/x', {});
    expect(ok).toBe(false);
    expect(globalThis.fetch).toBe(originalFetch); // no network call made
  });

  it('fails open (false) when the API errors — never throws', async () => {
    globalThis.fetch = vi.fn(async () => new Response('nope', { status: 500 })) as unknown as typeof fetch;
    const ok = await purgeUrl('https://tortoise.premiselabs.co/blog/x', { CF_API_TOKEN: 't', CF_ZONE_ID: 'z' });
    expect(ok).toBe(false);
  });
});
