// Agent publish/edit API — issue #1795.
//
// POST /blog/api/posts        — CREATE (default status='draft' → review queue;
//                               status='published' only when the owner explicitly
//                               asked for direct publishing — audited).
// PATCH /blog/api/posts/:slug — UPDATE own posts (created_by = calling agent).
//
// Auth: X-Agent-Key header → sha256 vs blog_agent_keys (service_role read).
// Writes: service-role key (env SUPABASE_SERVICE_ROLE_KEY — server-side only).
// Zero-dep: hand-rolled validation + Web Crypto for sha256.
//
// Semantics (plan §6 contract):
//   - INSERT-only create: slug exists → 409 (no slug theft; updates via PATCH).
//   - PATCH scoped to created_by = calling agent_name → 403 otherwise.
//   - slug immutable; archived posts reject all PATCHes (409/400 terminal).
//   - Rate limit: 120 req/min + 2,000 req/day per key (in-memory counter —
//     per-isolate; unit-tested at lowered thresholds).

import {
  type Env, SLUG_RE, validUrl,
} from "../_lib.ts";

const HSTS = { "Strict-Transport-Security": "max-age=31536000; includeSubDomains" };
const SLUG_MAX = 100;
const TITLE_MAX = 200;
const EXCERPT_MAX = 300;
const BODY_MAX = 100_000;
const TAGS_MAX = 10;
const RATE_WINDOW_MS = 60_000;
const RATE_BURST = 120;
const RATE_DAY_MS = 86_400_000;
const RATE_DAY = 2_000;

// In-memory rate counters per isolate (Cloudflare isolates are per-request;
// this is best-effort, unit-tested at lowered thresholds — the durable guard is
// the per-agent key + audit trail).
const counters = new Map<string, { burst: number[]; day: number[] }>();

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...HSTS },
  });
}

function err(status: number, code: string, message: string): Response {
  return json({ error: code, message }, status);
}

async function sha256Hex(s: string): Promise<string> {
  const data = new TextEncoder().encode(s);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

interface AgentIdentity {
  agentName: string;
}

async function authenticate(env: Env, request: Request): Promise<AgentIdentity | null> {
  const key = request.headers.get("X-Agent-Key");
  if (!key) return null;
  const hash = await sha256Hex(key);
  const url = `${env.SUPABASE_URL ?? ""}/rest/v1/blog_agent_keys?select=agent_name,active&key_hash=eq.${hash}&limit=1`;
  const res = await fetch(url, {
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY ?? "",
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY ?? ""}`,
      Accept: "application/json",
    },
  });
  if (!res.ok) throw new Error(`supabase ${res.status}`);
  const rows = (await res.json()) as Array<{ agent_name: string; active: boolean }>;
  const row = rows[0];
  if (!row || !row.active) return null;
  return { agentName: row.agent_name };
}

// ── Validation (zero-dep) ───────────────────────────────────────────────────
interface PostInput {
  title?: unknown;
  body?: unknown;
  slug?: unknown;
  excerpt?: unknown;
  cover_image_url?: unknown;
  tags?: unknown;
  meta_title?: unknown;
  meta_description?: unknown;
  author?: unknown;
  hold_for_review?: unknown;
  status?: unknown;
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function validateCreate(input: PostInput): { ok: true; value: CreateValues } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const title = str(input.title) ?? "";
  if (!title || title.length > TITLE_MAX) errors.title = `title required, max ${TITLE_MAX} chars`;

  const body = str(input.body) ?? "";
  if (body.length > BODY_MAX) errors.body = `body max ${BODY_MAX} chars`;

  const slug = str(input.slug) ?? "";
  if (slug && (slug.length > SLUG_MAX || !SLUG_RE.test(slug))) errors.slug = "slug must match ^[a-z0-9]+(?:-[a-z0-9]+)*$ (≤100)";

  const excerpt = str(input.excerpt);
  if (excerpt && excerpt.length > EXCERPT_MAX) errors.excerpt = `excerpt max ${EXCERPT_MAX} chars`;

  const cover = str(input.cover_image_url);
  if (cover && !validUrl(cover)) errors.cover_image_url = "cover_image_url must be an absolute https/http URL";

  const metaTitle = str(input.meta_title);
  const metaDesc = str(input.meta_description);
  if (metaTitle && metaTitle.length > 200) errors.meta_title = "meta_title max 200 chars";
  if (metaDesc && metaDesc.length > 300) errors.meta_description = "meta_description max 300 chars";

  const author = str(input.author);
  if (author && author.length > 100) errors.author = "author max 100 chars";

  const status = str(input.status);
  if (status && status !== "draft" && status !== "published") errors.status = "status must be draft | published";

  if (input.tags !== undefined && input.tags !== null) {
    if (!Array.isArray(input.tags) || input.tags.length > TAGS_MAX || !input.tags.every((t) => typeof t === "string" && t.length <= 40)) {
      errors.tags = `tags must be an array of strings (max ${TAGS_MAX}, each ≤40 chars)`;
    }
  }

  if (input.hold_for_review !== undefined && typeof input.hold_for_review !== "boolean") {
    errors.hold_for_review = "hold_for_review must be a boolean";
  }

  if (Object.keys(errors).length) return { ok: false, errors };
  return {
    ok: true,
    value: {
      title,
      body,
      slug: slug || null,
      excerpt: excerpt ?? null,
      cover_image_url: cover ?? null,
      tags: Array.isArray(input.tags) ? (input.tags as string[]) : [],
      meta_title: metaTitle ?? null,
      meta_description: metaDesc ?? null,
      author: author ?? null,
      hold_for_review: typeof input.hold_for_review === "boolean" ? input.hold_for_review : false,
      status: status ?? "draft",
    },
  };
}

interface CreateValues {
  title: string;
  body: string;
  slug: string | null;
  excerpt: string | null;
  cover_image_url: string | null;
  tags: string[];
  meta_title: string | null;
  meta_description: string | null;
  author: string | null;
  hold_for_review: boolean;
  status: "draft" | "published";
}

function validatePatch(input: PostInput): { ok: true; value: PatchValues } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const allowed = ["title", "body", "excerpt", "cover_image_url", "tags", "meta_title", "meta_description", "author", "hold_for_review", "status"];
  const value: PatchValues = {};

  const title = str(input.title);
  if (title !== null && (title.length === 0 || title.length > TITLE_MAX)) errors.title = `title max ${TITLE_MAX} chars`;
  else if (title !== null) value.title = title;

  const body = str(input.body);
  if (body !== null) {
    if (body.length > BODY_MAX) errors.body = `body max ${BODY_MAX} chars`;
    else value.body = body;
  }

  const excerpt = str(input.excerpt);
  if (excerpt !== null) {
    if (excerpt.length > EXCERPT_MAX) errors.excerpt = `excerpt max ${EXCERPT_MAX} chars`;
    else value.excerpt = excerpt;
  }

  const cover = str(input.cover_image_url);
  if (cover !== null) {
    if (cover === "") value.cover_image_url = null;
    else if (!validUrl(cover)) errors.cover_image_url = "cover_image_url must be an absolute https/http URL";
    else value.cover_image_url = cover;
  }

  if (input.tags !== undefined) {
    if (!Array.isArray(input.tags) || input.tags.length > TAGS_MAX || !input.tags.every((t) => typeof t === "string" && t.length <= 40)) {
      errors.tags = `tags must be an array of strings (max ${TAGS_MAX}, each ≤40 chars)`;
    } else value.tags = input.tags as string[];
  }

  const metaTitle = str(input.meta_title);
  if (metaTitle !== null && metaTitle.length > 200) errors.meta_title = "meta_title max 200 chars";
  else if (metaTitle !== null) value.meta_title = metaTitle;

  const metaDesc = str(input.meta_description);
  if (metaDesc !== null && metaDesc.length > 300) errors.meta_description = "meta_description max 300 chars";
  else if (metaDesc !== null) value.meta_description = metaDesc;

  const author = str(input.author);
  if (author !== null) {
    if (author.length > 100) errors.author = "author max 100 chars";
    else value.author = author;
  }

  if (input.hold_for_review !== undefined) {
    if (typeof input.hold_for_review !== "boolean") errors.hold_for_review = "hold_for_review must be a boolean";
    else value.hold_for_review = input.hold_for_review;
  }

  const status = str(input.status);
  if (status !== null) {
    if (status !== "draft" && status !== "published") errors.status = "status must be draft | published";
    else value.status = status;
  }

  if (Object.keys(errors).length) return { ok: false, errors };
  return { ok: true, value };
}

interface PatchValues {
  title?: string;
  body?: string;
  excerpt?: string | null;
  cover_image_url?: string | null;
  tags?: string[];
  meta_title?: string | null;
  meta_description?: string | null;
  author?: string | null;
  hold_for_review?: boolean;
  status?: "draft" | "published";
}

// ── Rate limiting (best-effort per isolate) ─────────────────────────────────
function rateLimited(agentName: string): boolean {
  const now = Date.now();
  let c = counters.get(agentName);
  if (!c) {
    c = { burst: [], day: [] };
    counters.set(agentName, c);
  }
  c.burst = c.burst.filter((t) => now - t < RATE_WINDOW_MS);
  c.day = c.day.filter((t) => now - t < RATE_DAY_MS);
  if (c.burst.length >= RATE_BURST || c.day.length >= RATE_DAY) return true;
  c.burst.push(now);
  c.day.push(now);
  return false;
}

// ── Handlers ────────────────────────────────────────────────────────────────
function serviceHeaders(env: Env): Record<string, string> {
  return {
    apikey: env.SUPABASE_SERVICE_ROLE_KEY ?? "",
    Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY ?? ""}`,
    "Content-Type": "application/json",
    Prefer: "return=representation",
  };
}

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return err(503, "not_configured", "agent API not configured");
  }
  const agent = await authenticate(env, request);
  if (!agent) return err(401, "unauthorized", "invalid or missing X-Agent-Key");
  if (rateLimited(agent.agentName)) return err(429, "rate_limited", "rate limit exceeded");

  let input: PostInput;
  try {
    input = (await request.json()) as PostInput;
  } catch {
    return err(400, "invalid_json", "request body must be valid JSON");
  }

  const v = validateCreate(input);
  if (!v.ok) return err(400, "validation", JSON.stringify(v.errors));

  const slug = v.value.slug ?? slugify(v.value.title);
  if (!SLUG_RE.test(slug) || slug.length > SLUG_MAX) {
    return err(400, "validation", JSON.stringify({ slug: "auto-slug invalid — provide an explicit slug" }));
  }

  const now = new Date().toISOString();
  const isDirect = v.value.status === "published";
  const row = {
    slug,
    title: v.value.title,
    body: v.value.body,
    excerpt: v.value.excerpt,
    cover_image_url: v.value.cover_image_url,
    tags: v.value.tags,
    meta_title: v.value.meta_title,
    meta_description: v.value.meta_description,
    author: v.value.author ?? "Tortoise team",
    status: v.value.status,
    hold_for_review: v.value.hold_for_review,
    created_by: agent.agentName,
    published_by: isDirect ? agent.agentName : null,
    published_at: isDirect ? now : null,
  };

  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/blog_posts`, {
    method: "POST",
    headers: serviceHeaders(env),
    body: JSON.stringify(row),
  });

  if (res.status === 409) {
    return err(409, "slug_conflict", "slug already exists — use PATCH to update your own post, or a new slug");
  }
  if (!res.ok) return err(503, "upstream", `supabase ${res.status}`);

  const created = (await res.json()) as Array<{ id: string; slug: string }>;
  const post = created[0];
  return json(
    { id: post?.id, slug: post?.slug, url: post ? `https://tortoise.premiselabs.co/blog/${post.slug}` : null },
    201,
  );
};

export const onRequestPatch: PagesFunction<Env> = async ({ request, env, params }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return err(503, "not_configured", "agent API not configured");
  }
  const agent = await authenticate(env, request);
  if (!agent) return err(401, "unauthorized", "invalid or missing X-Agent-Key");
  if (rateLimited(agent.agentName)) return err(429, "rate_limited", "rate limit exceeded");

  const slug = (params.slug as string) ?? "";
  if (!SLUG_RE.test(slug)) return err(400, "validation", "invalid slug");

  let input: PostInput;
  try {
    input = (await request.json()) as PostInput;
  } catch {
    return err(400, "invalid_json", "request body must be valid JSON");
  }

  const v = validatePatch(input);
  if (!v.ok) return err(400, "validation", JSON.stringify(v.errors));

  // Load the post — check existence, ownership, terminal state
  const getRes = await fetch(
    `${env.SUPABASE_URL}/rest/v1/blog_posts?select=id,status,created_by,reviewed_at&slug=eq.${encodeURIComponent(slug)}&limit=1`,
    { headers: serviceHeaders(env) },
  );
  if (!getRes.ok) return err(503, "upstream", `supabase ${getRes.status}`);
  const rows = (await getRes.json()) as Array<{ id: string; status: string; created_by: string | null }>;
  const post = rows[0];
  if (!post) return err(404, "not_found", "post not found");
  if (post.status === "archived") return err(409, "archived", "archived is terminal");
  if (post.created_by !== agent.agentName) return err(403, "forbidden", "you can only edit posts you created");

  // Build the PATCH body: status→published requires audit fields (trigger guard)
  const now = new Date().toISOString();
  const patch: Record<string, unknown> = { ...v.value };
  if (v.value.status === "published") {
    patch.published_by = agent.agentName;
    patch.published_at = now;
  }

  const res = await fetch(
    `${env.SUPABASE_URL}/rest/v1/blog_posts?slug=eq.${encodeURIComponent(slug)}`,
    {
      method: "PATCH",
      headers: serviceHeaders(env),
      body: JSON.stringify(patch),
    },
  );
  if (!res.ok) return err(503, "upstream", `supabase ${res.status}`);

  return json({ id: post.id, slug, url: `https://tortoise.premiselabs.co/blog/${slug}` }, 200);
};

export const onRequestOptions: PagesFunction<Env> = async () => {
  return new Response(null, {
    status: 204,
    headers: { Allow: "POST, PATCH, OPTIONS", ...HSTS },
  });
};

function slugify(title: string): string {
  return title
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^\w\s-]/g, "")
    .replace(/[\s_]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX);
}
