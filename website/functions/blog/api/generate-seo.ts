// AI SEO generator (#1861) — server-side, admin-gated, zero-dep.
//
// POST /blog/api/generate-seo { title, body, tags? } → fills slug/excerpt/
// tags/meta_title/meta_description from the article content + Tortoise SEO
// strategy. The editor calls this with the user's PKCE session (Bearer token);
// the function verifies the session + blog_admins membership (same fail-closed
// port as website/apps/dashboard/functions/admin/[[path]].ts). The post body is used ONLY to build
// the prompt and is never echoed back (prompt-injection surface stays
// server-side).
//
// Provider: OpenRouter chat completions — DeepSeek V4 Flash primary
// (json_object, temp 0.3) → Claude Haiku 4.5 fallback. Constraints enforced
// server-side (seo-constraints.ts) matching the editor + agent-API contract.
// Keyword injection from the #1862 module (TAG_KEYWORDS / keywordsFor),
// content-driven fallback when no tags match.

import { type Env, HSTS } from "../_lib.ts";
import { requireAdmin } from "../_shared/admin-auth.ts";
import { authenticate, rateLimited, type RateLimit } from "../_shared/agent-auth.ts";
import { constraints } from "../_shared/seo-constraints.ts";
import { META_TITLE_MAX, META_DESCRIPTION_MAX } from "../_shared/meta-contract.ts";
import {
  TAG_KEYWORDS,
  keywordsFor,
  primaryKeyword,
  resolvePrimaryKeyword,
  ensureKeyword,
  containsKeyword,
} from "../_shared/seo-keywords.ts";

const OPENROUTER = "https://openrouter.ai/api/v1/chat/completions";
const PRIMARY = "deepseek/deepseek-v4-flash";
const FALLBACK = "anthropic/claude-haiku-4.5";
// Per-M-token pricing (input/output) for cost estimation.
const PRICING: Record<string, { in: number; out: number }> = {
  [PRIMARY]: { in: 0.14, out: 0.28 },
  [FALLBACK]: { in: 1.0, out: 5.0 },
};
const MAX_BODY_PREVIEW = 2000;
const KEYWORDS_PER_TAG = 3;

// Two callers, two budgets. The human editor is a person clicking a button; the
// content pipeline (#8293) is a loop, so its ceiling is far tighter — every call
// spends an LLM request.
const ADMIN_RATE: RateLimit = { windowMs: 60_000, burst: 30, dayMs: 86_400_000, day: 500 };
const AGENT_RATE: RateLimit = { windowMs: 60_000, burst: 20, dayMs: 86_400_000, day: 300 };

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...HSTS },
  });
}

// ── Admin gate (shared — see _shared/admin-auth.ts) ───────────────────────
// ── Rate limiting (shared — see _shared/agent-auth.ts) ────────────────────

// ── Keyword injection (#1862) ──────────────────────────────────────────────
function injectKeywords(tags: string[]): string[] {
  const picked: string[] = [];
  const seen = new Set<string>();
  for (const tag of tags) {
    const norm = tag.toLowerCase().trim();
    // Object.hasOwn — 'in' would match Object.prototype members ('toString')
    if (!Object.hasOwn(TAG_KEYWORDS, norm)) continue;
    for (const kw of keywordsFor(norm).slice(0, KEYWORDS_PER_TAG)) {
      if (seen.has(kw)) continue;
      seen.add(kw);
      picked.push(kw);
      if (picked.length >= 9) return picked;
    }
  }
  return picked;
}

// ── Prompt ─────────────────────────────────────────────────────────────────
const SYSTEM_PROMPT = `You write SEO metadata for the Tortoise blog. Tortoise is an epistemic memory engine for AI agents — it gives agents long-term memory with confidence, provenance, and belief propagation (it "remembers what it knows and why it believes it"). The blog's positioning: agent memory without drift (graphs over naive vectors; claims with confidence instead of flat embeddings). The search debate in 2026 is "graph vs vector memory" — position Tortoise as the claims-with-confidence third position.

Rules:
- slug: lowercase alphanumeric + dashes, 2-8 words, no stopwords, no numbers unless meaningful.
- excerpt: one-two sentence summary that would make an AI-builder click. Specific, not generic.
- tags: 3-5 relevant terms from this vocabulary if they fit: agent-memory, epistemic-memory, knowledge-graph, semantic-memory, episodic-memory, mcp, self-hosting, retrieval, belief-propagation, sessions, memory-systems, provenance. Free-form tags allowed but keep them concrete.
- meta_title: under 60 chars INCLUDING " | Tortoise" — front-load the search term.
- meta_description: under 155 chars, active voice, include one concrete benefit.
Return ONLY a JSON object with keys: slug, excerpt, tags, meta_title, meta_description.`;

function buildUserPrompt(title: string, body: string, tags: string[], primary: string | null): string {
  const kw = injectKeywords(tags);
  // VERIFY, DON'T HOPE (#8293): a primary keyword NAMED as a requirement is a
  // different instruction from a bag of suggestions to "weave in", which is what
  // the previous prompt said. Best practice is one main keyword per article —
  // asking for nine is what pushes a model toward stuffing.
  const kwLine = primary
    ? `PRIMARY keyword: "${primary}". It MUST appear verbatim in meta_title and meta_description.\n` +
      `Other keywords — use naturally in tags or the excerpt, never stuffed: ${kw.filter((k) => k !== primary).join(", ") || "(none)"}`
    : "No mapped keywords — derive terms from the content itself.";
  const bodyPreview = body.slice(0, MAX_BODY_PREVIEW);
  return `Title: ${title}\n\nTags (comma-separated): ${tags.join(", ") || "(none)"}\n\n${kwLine}\n\nArticle content:\n${bodyPreview}`;
}

// ── OpenRouter call with fallback ──────────────────────────────────────────
interface GenResult {
  json: Record<string, unknown>;
  provider: string;
  inputTokens: number;
  outputTokens: number;
}

async function callProvider(
  env: Env,
  model: string,
  userPrompt: string,
): Promise<GenResult | null> {
  const body: Record<string, unknown> = {
    model,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: userPrompt },
    ],
    temperature: 0.3,
    max_tokens: 400,
  };
  // Structured output is supported by DeepSeek; the Haiku fallback may reject
  // response_format (OpenRouter docs: unsupported structured outputs error) —
  // the JSON-extraction regex + "Return ONLY a JSON object" prompt tolerate
  // plain JSON, so only send response_format on the primary.
  if (model === PRIMARY) body.response_format = { type: "json_object" };
  let res: Response;
  try {
    res = await fetch(OPENROUTER, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.OPENROUTER_API_KEY ?? ""}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    });
  } catch {
    return null;
  }
  if (!res.ok) return null;
  let payload: { choices?: Array<{ message?: { content?: string } }>; usage?: { prompt_tokens?: number; completion_tokens?: number } };
  try {
    payload = (await res.json()) as typeof payload;
  } catch {
    return null;
  }
  const content = payload.choices?.[0]?.message?.content ?? "";
  const m = /\{[\s\S]*\}/.exec(content);
  if (!m) return null;
  try {
    const parsed = JSON.parse(m[0]) as Record<string, unknown>;
    return {
      json: parsed,
      provider: model,
      inputTokens: payload.usage?.prompt_tokens ?? 0,
      outputTokens: payload.usage?.completion_tokens ?? 0,
    };
  } catch {
    return null;
  }
}

function costEstimate(provider: string, inputTokens: number, outputTokens: number): number {
  const p = PRICING[provider] ?? PRICING[FALLBACK];
  return (inputTokens / 1_000_000) * p.in + (outputTokens / 1_000_000) * p.out;
}

// ── Handler ────────────────────────────────────────────────────────────────
export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  // Two callers, one endpoint. The human editor authenticates with a PKCE admin
  // session (Bearer); the content pipeline authenticates with X-Agent-Key
  // (#8293). Presence of the header selects the agent path, so an agent request
  // can never be satisfied by an ambient admin session and vice versa.
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return json({ error: "not_configured" }, 503);
  }
  const agentHeader = request.headers.get("X-Agent-Key");
  let callerId: string;
  if (agentHeader) {
    const agent = await authenticate(env, request);
    if (!agent.ok) {
      // 404 for a known-but-inactive key mirrors the posts API — an inactive key
      // stays indistinguishable from a missing route.
      return agent.code === 404
        ? json({ error: "not_found" }, 404)
        : json({ error: "unauthorized", message: "Unknown or missing agent key" }, 401);
    }
    callerId = `agent:${agent.identity.agentName}`;
    if (rateLimited(callerId, AGENT_RATE)) {
      return json({ error: "rate_limited", message: "Rate limit reached — try again shortly" }, 429);
    }
  } else {
    if (!env.SUPABASE_ANON_KEY) return json({ error: "not_configured" }, 503);
    const admin = await requireAdmin(env, request);
    if (!admin.ok) {
      // A store/database fault is NOT "you are signed out". Answering 401 here
      // would sign an admin out because a lookup blipped — the #3485 class.
      if (admin.reason === "unavailable") {
        return json({ error: "unavailable", message: "Session store or admin lookup unavailable — try again shortly" }, 503);
      }
      return json({ error: "unauthorized", message: "Session expired or not an admin — refresh and log in again" }, 401);
    }
    callerId = `admin:${admin.userId}`;
    if (rateLimited(callerId, ADMIN_RATE)) {
      return json({ error: "rate_limited", message: "Rate limit reached — try again shortly" }, 429);
    }
  }

  let input: { title?: unknown; body?: unknown; tags?: unknown };
  try {
    input = (await request.json()) as typeof input;
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const title = typeof input.title === "string" ? input.title.trim() : "";
  const body = typeof input.body === "string" ? input.body : "";
  const tags = Array.isArray(input.tags)
    ? input.tags.filter((t): t is string => typeof t === "string").slice(0, 10).map((t) => t.slice(0, 40))
    : [];
  if (!title) return json({ error: "validation", message: "title required" }, 400);
  if (!body.trim()) return json({ error: "validation", message: "body required" }, 400);
  if (!env.OPENROUTER_API_KEY) {
    return json({ error: "not_configured", message: "OPENROUTER_API_KEY missing" }, 503);
  }

  // The keyword the PROMPT is built around. It can only use the caller's tags,
  // because the model's own tags do not exist yet — see the note after
  // `enforced` for the keyword the RESPONSE is verified against. null when no
  // supplied tag maps to the research vocabulary.
  const promptPrimary = primaryKeyword(tags);
  const userPrompt = buildUserPrompt(title, body, tags, promptPrimary);

  let result = await callProvider(env, PRIMARY, userPrompt);
  if (!result) result = await callProvider(env, FALLBACK, userPrompt);
  if (!result) {
    return json({ error: "generation_failed", message: "both providers failed" }, 502);
  }

  const enforced = constraints.enforce(
    {
      slug: typeof result.json.slug === "string" ? result.json.slug : undefined,
      excerpt: typeof result.json.excerpt === "string" ? result.json.excerpt : undefined,
      tags: Array.isArray(result.json.tags)
        ? result.json.tags.filter((t): t is string => typeof t === "string")
        : [],
      meta_title: typeof result.json.meta_title === "string" ? result.json.meta_title : undefined,
      meta_description: typeof result.json.meta_description === "string" ? result.json.meta_description : undefined,
    },
    title,
  );

  // THE keyword the RESPONSE is verified against (#8320). The prompt above could
  // only use the caller's tags, but the model GENERATES the tags — so when the
  // caller supplied none (the pipeline's normal shape: title + body), the
  // prompt-time keyword was null, the repair below had nothing to enforce, and
  // `keyword_ok` returned `true` while checking nothing at all.
  //
  // Prefer the caller's tags when they exist (they are an explicit statement of
  // intent); otherwise use the model's own tags, which describe what the article
  // is actually about. Deterministic, so the repair below is reproducible.
  const primary = resolvePrimaryKeyword(tags, enforced.tags);

  // VERIFY, DON'T HOPE (#8293). The prompt above ASKS for the primary keyword;
  // a prompt is a request, not a guarantee, and the previous behaviour never
  // checked. Repair deterministically, then re-enforce so the repair itself
  // cannot exceed the field limits — the repair and the length contract are two
  // different jobs and both must hold.
  //
  // An EMPTY field is not repaired (#8293 review): `ensureKeyword("", primary,
  // max)` returns the bare keyword, which would turn a failed generation into a
  // 200 carrying a keyword-only title — the `generation_incomplete` 502 below
  // would stop firing for the meta fields. The repair exists to make a
  // *generated* title carry the keyword, not to invent one from nothing.
  const repaired = constraints.enforce(
    {
      ...enforced,
      meta_title: enforced.meta_title
        ? ensureKeyword(enforced.meta_title, primary, META_TITLE_MAX)
        : "",
      meta_description: enforced.meta_description
        ? ensureKeyword(enforced.meta_description, primary, META_DESCRIPTION_MAX)
        : "",
    },
    title,
  );

  // Reject rather than silently ship junk when generation produced nothing usable.
  if (!repaired.meta_title || !repaired.meta_description || !repaired.excerpt || repaired.tags.length === 0) {
    return json({ error: "generation_incomplete", message: "generated fields incomplete after constraints" }, 502);
  }

  return json(
    {
      ...repaired,
      // `null` means "no primary keyword could be derived, so nothing was
      // verified" — NOT success. Reporting `true` there was the vacuity #8320
      // fixed; a caller that needs a guarantee must read a non-null value.
      // `false` still means the field limits won the tie over the repair, which
      // is a real signal rather than a silent miss.
      primary_keyword: primary,
      keyword_ok:
        primary === null
          ? null
          : containsKeyword(repaired.meta_title, primary) &&
            containsKeyword(repaired.meta_description, primary),
      provider: result.provider,
      input_tokens: result.inputTokens,
      output_tokens: result.outputTokens,
      cost_estimate: costEstimate(result.provider, result.inputTokens, result.outputTokens),
    },
    200,
  );
};

export const onRequestOptions: PagesFunction<Env> = async () => {
  return new Response(null, {
    status: 204,
    headers: { Allow: "POST, OPTIONS", ...HSTS },
  });
};
