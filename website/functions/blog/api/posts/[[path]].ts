// Agent publish/edit API — issue #1795.
//
// POST /blog/api/posts        — CREATE (default status='draft' → review queue;
//                               status='published' only when the owner explicitly
//                               asked for direct publishing — audited).
// PATCH /blog/api/posts/:slug — UPDATE own posts (created_by = calling agent).
// DELETE /blog/api/posts/:slug — DELETE own posts (#4220; created_by = calling
//                               agent, same ownership contract as PATCH). Added
//                               because the API had no way to remove a row: the
//                               E2E suite that creates posts had no cleanup path,
//                               so every deploy deposited drafts in the review
//                               queue forever (#4220).
//                               `archived` is terminal — DELETE refuses it (409),
//                               exactly as PATCH does. DRAFT-ONLY: the recorded
//                               lifecycle (plan W4) is draft → published →
//                               archived (terminal) with no published→deleted
//                               transition, so a published post must be
//                               unpublished first (409 otherwise).
//
// Auth: X-Agent-Key header → sha256 vs blog_agent_keys (service_role read).
// Writes: service-role key (env SUPABASE_SERVICE_ROLE_KEY — server-side only).
// Zero-dep: hand-rolled validation + Web Crypto for sha256.
//
// Semantics (plan §6 contract):
//   - INSERT-only create: slug exists → 409 (no slug theft; updates via PATCH).
//   - PATCH scoped to created_by = calling agent_name → 403 otherwise.
//   - DELETE scoped to created_by = calling agent_name → 403 otherwise (#4220).
//   - slug immutable; archived posts reject all PATCHes AND DELETEs (409/400 terminal).
//   - Rate limit: 120 req/min + 2,000 req/day per key (in-memory counter —
//     per-isolate; unit-tested at lowered thresholds).
//   - IndexNow (#9680): the PATCH that moves a post to published submits its
//     canonical URL once, in the background, fail-soft. It reaches Bing / Yandex
//     / Seznam / Naver / Amazon — NOT Google, which does not consume IndexNow.
//
// Author identity (#1383 — part of the same v1 contract, additive):
//   - `author?: string` (≤100 chars) is the byline NAME. Omitted →
//     `DEFAULT_AUTHOR` ('Daniel Ospina', the shipped author), never the retired
//     anonymous 'Tortoise team'.
//   - `author_image_url?: string` is the byline AVATAR and must be an absolute
//     http(s) URL (a relative/`javascript:`/`data:` value is 400). On CREATE,
//     omitting it inherits the shipped avatar when the post is by the shipped
//     author (so an agent-written post and an editor-written post render the
//     SAME byline) and NULL otherwise; an explicit `""` clears it to NULL, which
//     renders the name alone. On PATCH, `""` clears it and omission leaves it
//     untouched (a partial update).

import {
  type Env, validUrl, SITE_URL, DEFAULT_AUTHOR, DEFAULT_AUTHOR_IMAGE_URL,
} from "../../_lib.ts";
import {
  META_TITLE_MAX, META_DESCRIPTION_MAX, EXCERPT_MAX, TAGS_MAX, TAG_LEN_MAX,
} from "../../_shared/meta-contract.ts";
import { SLUG_MAX, SLUG_RE } from "../../_shared/slug.ts";
import { purgeUrl } from "../../_shared/cloudflare-purge.ts";
// IndexNow notification on the publish transition (#9680). ⛔ It reaches Bing /
// Yandex / Seznam / Naver / Amazon — NOT Google, which does not consume the
// protocol. See _shared/indexnow.ts for the verified vendor contract.
import { notifyPublished } from "../../_shared/indexnow.ts";
// Agent-key auth + rate limiting are shared with the AI SEO generator (#8293)
// so the key check exists ONCE — two copies drift, and a drifted key check is a
// fail-open waiting for a maintenance edit.
import {
  authenticate, rateLimited, type RateLimit,
} from "../../_shared/agent-auth.ts";

const HSTS = { "Strict-Transport-Security": "max-age=31536000; includeSubDomains" };
const TITLE_MAX = 200;
const BODY_MAX = 100_000;
const RATE_WINDOW_MS = 60_000;
const RATE_BURST = 120;
const RATE_DAY_MS = 86_400_000;
const RATE_DAY = 2_000;

// This endpoint's budget, applied through the shared limiter.
const POST_RATE: RateLimit = { windowMs: RATE_WINDOW_MS, burst: RATE_BURST, dayMs: RATE_DAY_MS, day: RATE_DAY };

// SEO field-length contract — single source of truth (#1866): shared with the
// AI generator (seo-constraints.ts) and locked to the editor's literals.

// In-memory rate counters per isolate (Cloudflare isolates are per-request;
// this is best-effort, unit-tested at lowered thresholds — the durable guard is
// the per-agent key + audit trail). The counters live in _shared/agent-auth.ts.

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...HSTS },
  });
}

function err(status: number, code: string, message: string): Response {
  return json({ error: code, message }, status);
}

// 400 {field: message} shape (plan §6) — 422 for body content errors
function validationError(errors: Record<string, string>): Response {
  const status = errors.body_422 ? 422 : 400;
  return json(errors, status);
}

function balancedFences(markdown: string): boolean {
  let inFence = false;
  for (const line of markdown.split("\n")) {
    if (/^```/.test(line.trimStart())) inFence = !inFence;
  }
  return !inFence;
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
  author_image_url?: unknown;
  hold_for_review?: unknown;
  status?: unknown;
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

// The recorded lifecycle is draft → published → archived (plan W4). The union —
// not a bare `string` — is what keeps an unvalidated status out of the INSERT:
// `CreateValues.status` and `PatchValues.status` are BOTH typed by it, so the
// only way to build either is to have narrowed the raw input into it (#4311).
type PostStatus = "draft" | "published";

function validateCreate(input: PostInput): { ok: true; value: CreateValues } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  // Narrow ONCE, here. Below this point `status` is a valid member or the
  // default, so no later branch can hand a raw `string` to the database.
  let status: PostStatus = "draft";
  if (input.status !== undefined && input.status !== null) {
    if (typeof input.status !== "string") {
      errors.status = "must be a string";
    } else if (input.status === "draft" || input.status === "published") {
      status = input.status;
    } else {
      errors.status = "status must be draft | published";
    }
  }
  const title = str(input.title) ?? "";
  if (!title || title.length > TITLE_MAX) errors.title = `title required, max ${TITLE_MAX} chars`;

  const body = str(input.body) ?? "";
  if (body.length > BODY_MAX) errors.body_422 = `body max ${BODY_MAX} chars`;
  else if (body && !balancedFences(body)) errors.body_422 = "malformed markdown: unbalanced code fence";
  if (body.trim() === "" && status === "published") errors.body = "body required when status=published";

  const slug = str(input.slug) ?? "";
  if (slug && (slug.length > SLUG_MAX || !SLUG_RE.test(slug))) errors.slug = "slug must match ^[a-z0-9]+(?:-[a-z0-9]+)*$ (≤100)";

  const excerpt = str(input.excerpt);
  if (excerpt && excerpt.length > EXCERPT_MAX) errors.excerpt = `excerpt max ${EXCERPT_MAX} chars`;

  const cover = str(input.cover_image_url);
  if (cover && !validUrl(cover)) errors.cover_image_url = "cover_image_url must be an absolute https/http URL";

  const metaTitle = str(input.meta_title);
  const metaDesc = str(input.meta_description);
  if (metaTitle && metaTitle.length > META_TITLE_MAX) errors.meta_title = `meta_title max ${META_TITLE_MAX} chars`;
  if (metaDesc && metaDesc.length > META_DESCRIPTION_MAX) errors.meta_description = `meta_description max ${META_DESCRIPTION_MAX} chars`;

  const author = str(input.author);
  if (author && author.length > 100) errors.author = "author max 100 chars";

  const authorImage = str(input.author_image_url);
  if (authorImage && !validUrl(authorImage)) errors.author_image_url = "author_image_url must be an absolute https/http URL";

  if (status === "published" && input.hold_for_review === true) errors.hold_for_review = "hold_for_review cannot be combined with status=published";

  if (input.tags !== undefined && input.tags !== null) {
    if (!Array.isArray(input.tags) || input.tags.length > TAGS_MAX || !input.tags.every((t) => typeof t === "string" && t.length <= TAG_LEN_MAX)) {
      errors.tags = `tags must be an array of strings (max ${TAGS_MAX}, each ≤${TAG_LEN_MAX} chars)`;
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
      author_image_url: authorImage ?? null,
      hold_for_review: typeof input.hold_for_review === "boolean" ? input.hold_for_review : false,
      status,
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
  author_image_url: string | null;
  hold_for_review: boolean;
  status: PostStatus;
}

function validatePatch(input: PostInput): { ok: true; value: PatchValues } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const value: PatchValues = {};
  const stringKeys = ["title", "body", "excerpt", "cover_image_url", "meta_title", "meta_description", "author", "author_image_url"] as const;
  for (const k of stringKeys) {
    if (input[k] !== undefined && input[k] !== null && typeof input[k] !== "string") {
      errors[k] = "must be a string";
    }
  }
  if (input.status !== undefined && input.status !== null && typeof input.status !== "string") {
    errors.status = "must be a string";
  }
  if (input.hold_for_review !== undefined && input.hold_for_review !== null && typeof input.hold_for_review !== "boolean") {
    errors.hold_for_review = "must be a boolean";
  }

  const title = str(input.title);
  if (title !== null && (title.length === 0 || title.length > TITLE_MAX)) errors.title = `title max ${TITLE_MAX} chars`;
  else if (title !== null) value.title = title;

  const body = str(input.body);
  if (body !== null) {
    if (body.length > BODY_MAX) errors.body_422 = `body max ${BODY_MAX} chars`;
    else if (body && !balancedFences(body)) errors.body_422 = "malformed markdown: unbalanced code fence";
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

  // `""` clears the avatar to NULL, which is what makes the byline fall back to
  // the name alone — never `''`, which is not nullish and would emit <img src="">.
  const authorImage = str(input.author_image_url);
  if (authorImage !== null) {
    if (authorImage === "") value.author_image_url = null;
    else if (!validUrl(authorImage)) errors.author_image_url = "author_image_url must be an absolute https/http URL";
    else value.author_image_url = authorImage;
  }

  if (input.tags !== undefined) {
    if (!Array.isArray(input.tags) || input.tags.length > TAGS_MAX || !input.tags.every((t) => typeof t === "string" && t.length <= TAG_LEN_MAX)) {
      errors.tags = `tags must be an array of strings (max ${TAGS_MAX}, each ≤${TAG_LEN_MAX} chars)`;
    } else value.tags = input.tags as string[];
  }

  const metaTitle = str(input.meta_title);
  if (metaTitle !== null && metaTitle.length > META_TITLE_MAX) errors.meta_title = `meta_title max ${META_TITLE_MAX} chars`;
  else if (metaTitle !== null) value.meta_title = metaTitle;

  const metaDesc = str(input.meta_description);
  if (metaDesc !== null && metaDesc.length > META_DESCRIPTION_MAX) errors.meta_description = `meta_description max ${META_DESCRIPTION_MAX} chars`;
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
    else {
      if (status === "published" && input.hold_for_review === true) errors.hold_for_review = "hold_for_review cannot be combined with status=published";
      else value.status = status;
    }
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
  author_image_url?: string | null;
  hold_for_review?: boolean;
  status?: PostStatus;
}

// ── Handlers ────────────────────────────────────────────────────────────────
function serviceHeaders(env: Env): Record<string, string> {
  return {
    apikey: env.SUPABASE_SERVICE_ROLE_KEY ?? "",
    Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY ?? ""}`,
    "Content-Type": "application/json",
    Accept: "application/json",
    Prefer: "return=representation",
  };
}

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return err(503, "not_configured", "agent API not configured");
  }
  const auth = await authenticate(env, request);
  if (!auth.ok) return err(auth.code, auth.code === 404 ? "inactive_agent" : "unauthorized", "invalid or missing X-Agent-Key");
  const agent = auth.identity;
  if (rateLimited(agent.agentName, POST_RATE)) return err(429, "rate_limited", "rate limit exceeded");

  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (contentLength > 150_000) return err(422, "body_too_large", "request body too large");

  let input: PostInput;
  try {
    input = (await request.json()) as PostInput;
  } catch {
    return err(400, "invalid_json", "request body must be valid JSON");
  }

  const v = validateCreate(input);
  if (!v.ok) return validationError(v.errors);

  const slug = v.value.slug ?? slugify(v.value.title);
  if (!SLUG_RE.test(slug) || slug.length > SLUG_MAX) {
    return err(400, "validation", JSON.stringify({ slug: "auto-slug invalid — provide an explicit slug" }));
  }

  const now = new Date().toISOString();
  const isDirect = v.value.status === "published";
  const author = v.value.author ?? DEFAULT_AUTHOR;
  const row = {
    slug,
    title: v.value.title,
    body: v.value.body,
    excerpt: v.value.excerpt,
    cover_image_url: v.value.cover_image_url,
    tags: v.value.tags,
    meta_title: v.value.meta_title,
    meta_description: v.value.meta_description,
    author,
    // The avatar default follows the NAME default, and the two cases are
    // deliberately different (#1383):
    //  * OMITTED (`null` here) → the shipped avatar when the post is by the
    //    shipped author, exactly as the editor prefills it. The admin path and
    //    the agent path must not produce different bylines for the same author
    //    ("an avatar on 4 posts and none on 2"), which is the defect class this
    //    change removes for the name — so it must not be reintroduced for the
    //    image. A post by ANY OTHER author gets NULL (name-alone byline).
    //  * EXPLICIT `""` → NULL, never '': '' is not nullish, so storing it would
    //    emit <img src=""> on the byline. An explicit empty value is an explicit
    //    "no avatar" (the PATCH path does the same, so the write paths agree).
    author_image_url:
      v.value.author_image_url === null
        ? (author === DEFAULT_AUTHOR ? DEFAULT_AUTHOR_IMAGE_URL : null)
        : v.value.author_image_url || null,
    status: v.value.status,
    hold_for_review: v.value.hold_for_review,
    created_by: agent.agentName,
    published_by: isDirect ? agent.agentName : null,
    published_at: isDirect ? now : null,
  };

  let res: Response;
  try {
    res = await fetch(`${env.SUPABASE_URL}/rest/v1/blog_posts`, {
      method: "POST",
      headers: serviceHeaders(env),
      body: JSON.stringify(row),
    });
  } catch {
    return err(503, "upstream", "supabase unreachable");
  }

  if (res.status === 409) {
    return err(409, "slug_conflict", "slug already exists — use PATCH to update your own post, or a new slug");
  }
  if (!res.ok) {
    if (res.status >= 400 && res.status < 500) return err(res.status, "upstream_4xx", `supabase ${res.status}`);
    return err(503, "upstream", `supabase ${res.status}`);
  }

  const created = (await res.json()) as Array<{ id: string; slug: string }>;
  const post = created[0];
  return json(
    { id: post?.id, slug: post?.slug, url: post ? `${SITE_URL}/blog/${post.slug}` : null },
    201,
  );
};

export const onRequestPatch: PagesFunction<Env> = async ({ request, env, params, waitUntil }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return err(503, "not_configured", "agent API not configured");
  }
  const auth = await authenticate(env, request);
  if (!auth.ok) return err(auth.code, auth.code === 404 ? "inactive_agent" : "unauthorized", "invalid or missing X-Agent-Key");
  const agent = auth.identity;
  if (rateLimited(agent.agentName, POST_RATE)) return err(429, "rate_limited", "rate limit exceeded");

  // [[path]] catch-all: params.path = [] for /blog/api/posts (create),
  // [slug] for /blog/api/posts/{slug} (PATCH). Only the PATCH surface reads it.
  const slug = ((params.path as string[] | undefined) ?? [])[0] ?? "";
  if (!SLUG_RE.test(slug)) return err(400, "validation", "invalid slug");

  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (contentLength > 150_000) return err(422, "body_too_large", "request body too large");

  let input: PostInput;
  try {
    input = (await request.json()) as PostInput;
  } catch {
    return err(400, "invalid_json", "request body must be valid JSON");
  }

  const v = validatePatch(input);
  if (!v.ok) return validationError(v.errors);
  if (Object.keys(v.value).length === 0) return err(400, "validation", "empty patch — nothing to update");

  // Load the post — check existence, ownership, terminal state
  let getRes: Response;
  try {
    getRes = await fetch(
      `${env.SUPABASE_URL}/rest/v1/blog_posts?select=id,status,created_by,body&slug=eq.${encodeURIComponent(slug)}&limit=1`,
      { headers: serviceHeaders(env) },
    );
  } catch {
    return err(503, "upstream", "supabase unreachable");
  }
  if (!getRes.ok) return err(503, "upstream", `supabase ${getRes.status}`);
  const rows = (await getRes.json()) as Array<{ id: string; status: string; created_by: string | null; body: string }>;
  const post = rows[0];
  if (!post) return err(404, "not_found", "post not found");
  if (post.status === "archived") return err(409, "archived", "archived is terminal");
  if (post.created_by !== agent.agentName) return err(403, "forbidden", "you can only edit posts you created");

  // Empty-body-at-publish guard: RESULTING body + RESULTING status must be valid
  const resultingBody = v.value.body ?? post.body;
  const resultStatus = v.value.status ?? post.status;
  if (resultStatus === "published" && resultingBody.trim() === "") {
    return err(400, "validation", "body required when status=published");
  }
  // No invisible limbo: hold_for_review=true on a published post (current or resulting) → 400
  if (resultStatus === "published" && v.value.hold_for_review === true) {
    return err(400, "validation", "hold_for_review cannot be combined with a published post");
  }

  // Build the PATCH body: status→published requires audit fields (trigger guard)
  const now = new Date().toISOString();
  const patch: Record<string, unknown> = { ...v.value };
  if (v.value.status === "published") {
    patch.published_by = agent.agentName;
    patch.published_at = now;
    patch.hold_for_review = false; // explicit publish clears hold — no invisible limbo
  } else if (v.value.status === "draft") {
    // Unpublish: clear audit columns to match the editor path (unpublishPost /
    // requestChangesPost null them) — the two write paths must produce the
    // same row shape for the same transition.
    patch.published_by = null;
    patch.published_at = null;
    patch.reviewed_by = null;
    patch.reviewed_at = null;
    patch.review_note = null;
  }

  // created_by + status=not.eq.archived make ownership and the terminal state
  // predicates on the write itself, not a read-then-trust gate: a row that
  // changed hands or was archived between GET and PATCH is left untouched.
  let res: Response;
  try {
    res = await fetch(
      `${env.SUPABASE_URL}/rest/v1/blog_posts?slug=eq.${encodeURIComponent(slug)}` +
        `&created_by=eq.${encodeURIComponent(agent.agentName)}&status=not.eq.archived`,
      {
        method: "PATCH",
        headers: serviceHeaders(env),
        body: JSON.stringify(patch),
      },
    );
  } catch {
    return err(503, "upstream", "supabase unreachable");
  }
  if (!res.ok) {
    if (res.status >= 400 && res.status < 500) return err(res.status, "upstream_4xx", `supabase ${res.status}`);
    return err(503, "upstream", `supabase ${res.status}`);
  }
  // TOCTOU: post archived between GET and PATCH → PostgREST returns 200 + [] rows
  let updated: Array<{ id: string; slug: string }> = [];
  try {
    updated = (await res.json()) as Array<{ id: string; slug: string }>;
  } catch {
    // 204 no-content (no-op) — already rejected above; treat empty as no-op success
    return json({ id: post.id, slug, url: `${SITE_URL}/blog/${slug}` }, 200);
  }
  if (updated.length === 0) return err(409, "archived", "post was archived concurrently");

  // #9680: IndexNow — submit the canonical URL when, and only when, THIS patch is
  // the draft→published transition. The transition predicate IS the trigger: an
  // edit that leaves the post published (both sides published), a draft write and
  // an unpublish all fail it and submit nothing. The once-per-24h window that
  // covers publish → unpublish → re-publish the same day lives in
  // notifyPublished, backed by blog_posts.indexnow_submitted_at.
  //
  // waitUntil, so the response does not wait on the vendor: the post is live
  // either way, a submission failure must not fail the publish, and there is no
  // retry in this path — the failure is logged with its status instead.
  if (resultStatus === "published" && post.status !== "published") {
    waitUntil(
      notifyPublished({
        env,
        id: post.id,
        url: `${SITE_URL}/blog/${slug}`,
        nowMs: Date.parse(now),
      }),
    );
  }

  // #1865: unpublish (resulting status = draft on a previously published post)
  // → purge the article URL from the edge cache (best-effort, fail-open). The
  // stale-200 window otherwise survives up to the cache TTL. waitUntil
  // guarantees the purge runs after the response (Cloudflare isolates are
  // torn down otherwise). Purge whenever the resulting status is non-published
  // — a draft→draft purge is a harmless fail-open no-op, and it also covers
  // the TOCTOU window where the post was published between GET and PATCH
  // (wasPublished would read stale-false).
  if (resultStatus === "draft") {
    waitUntil(
      purgeUrl(`${SITE_URL}/blog/${slug}`, env).then((purged) => {
        if (!purged) console.warn(`[purge] agent-api unpublish failed: /blog/${slug}`);
      }),
    );
  }

  return json({ id: post.id, slug, url: `${SITE_URL}/blog/${slug}` }, 200);
};

// DELETE /blog/api/posts/:slug — remove a post the calling agent created (#4220).
//
// Owner-only destructive path. The e2e suite that writes to prod needs this: with
// no delete surface, its only "cleanup" was to leave the row as a draft, so every
// deploy added rows to the editorial review queue forever (#4220).
//
// Contract (mirrors PATCH):
//   - 401 no/invalid/inactive key; 429 rate-limited; 400 invalid slug.
//   - 404 unknown slug; 403 slug owned by a different agent.
//   - 409 archived — the terminal state is preserved; an archived record is not
//     deletable through the agent API (an operator can, via SQL).
//   - 409 published (any non-draft) — the recorded lifecycle (plan W4) is
//     draft → published → archived (terminal) and has NO published→deleted
//     transition, so this path never destroys a live article: a published post
//     must be unpublished (status→draft) first. created_by is the CREATOR, not
//     the publisher — an operator publishes with published_by while created_by
//     stays the creating agent — so without this guard the agent key could
//     irreversibly delete an operator-approved, already-published article.
//   - 200 {deleted:true, slug}.
// Both predicates are repeated IN the DELETE (created_by=eq.<agent> AND
// status=eq.draft) so a row that changed hands — or was published — between the
// ownership read and the delete cannot be removed: the checks are predicates on
// the destructive statement, not a read-then-trust gate.
export const onRequestDelete: PagesFunction<Env> = async ({ request, env, params, waitUntil }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return err(503, "not_configured", "agent API not configured");
  }
  const auth = await authenticate(env, request);
  if (!auth.ok) return err(auth.code, auth.code === 404 ? "inactive_agent" : "unauthorized", "invalid or missing X-Agent-Key");
  const agent = auth.identity;
  if (rateLimited(agent.agentName, POST_RATE)) return err(429, "rate_limited", "rate limit exceeded");

  const slug = ((params.path as string[] | undefined) ?? [])[0] ?? "";
  if (!SLUG_RE.test(slug)) return err(400, "validation", "invalid slug");

  // Ownership + terminal-state read (same shape as PATCH).
  let getRes: Response;
  try {
    getRes = await fetch(
      `${env.SUPABASE_URL}/rest/v1/blog_posts?select=id,status,created_by&slug=eq.${encodeURIComponent(slug)}&limit=1`,
      { headers: serviceHeaders(env) },
    );
  } catch {
    return err(503, "upstream", "supabase unreachable");
  }
  if (!getRes.ok) return err(503, "upstream", `supabase ${getRes.status}`);
  const rows = (await getRes.json()) as Array<{ id: string; status: string; created_by: string | null }>;
  const post = rows[0];
  if (!post) return err(404, "not_found", "post not found");
  if (post.status === "archived") return err(409, "archived", "archived is terminal");
  if (post.created_by !== agent.agentName) return err(403, "forbidden", "you can only delete posts you created");
  // DRAFT-ONLY guard (P1): the lifecycle has no published→deleted transition.
  if (post.status !== "draft") {
    return err(409, "published", "published posts must be unpublished before deletion");
  }

  let res: Response;
  try {
    res = await fetch(
      `${env.SUPABASE_URL}/rest/v1/blog_posts?slug=eq.${encodeURIComponent(slug)}` +
        `&created_by=eq.${encodeURIComponent(agent.agentName)}&status=eq.draft`,
      { method: "DELETE", headers: serviceHeaders(env) },
    );
  } catch {
    return err(503, "upstream", "supabase unreachable");
  }
  if (!res.ok) {
    if (res.status >= 400 && res.status < 500) return err(res.status, "upstream_4xx", `supabase ${res.status}`);
    return err(503, "upstream", `supabase ${res.status}`);
  }

  // Prefer: return=representation → the deleted row(s). Empty means the row
  // vanished, was published, or was archived between the read and the delete:
  // report 404 rather than claiming a delete that did not happen.
  let deleted: Array<{ id: string; slug: string }> = [];
  try {
    deleted = (await res.json()) as Array<{ id: string; slug: string }>;
  } catch {
    deleted = [];
  }
  if (deleted.length === 0) return err(404, "not_found", "post not found");

  // A previously-published post can still be served from the edge cache after the
  // row is gone — purge it, exactly as the unpublish path does (#1865).
  waitUntil(
    purgeUrl(`${SITE_URL}/blog/${slug}`, env).then((purged) => {
      if (!purged) console.warn(`[purge] agent-api delete failed: /blog/${slug}`);
    }),
  );

  return json({ deleted: true, slug }, 200);
};

export const onRequestOptions: PagesFunction<Env> = async () => {
  return new Response(null, {
    status: 204,
    headers: { Allow: "POST, PATCH, DELETE, OPTIONS", ...HSTS },
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
