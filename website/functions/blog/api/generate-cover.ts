// AI cover image generator (#1863) — server-side, admin-gated, zero-dep.
//
// POST /blog/api/generate-cover { title, tags?, mode, slug? } → generates a
// 16:9 OG-ready cover (founder-likeness by default, abstract toggle) via
// OpenRouter's Image API (/api/v1/images), uploads it to the blog-images
// bucket, and returns the CDN URL. The editor sets cover_image_url from it.
//
// Founder mode: three Cloudinary reference images anchor the identity
// (consistent founder likeness across generations). Abstract mode: brand-token
// art, no reference images. The post BODY is deliberately excluded from the
// prompt (injection risk — scoping decision).
//
// Deterministic QC (no vision QC in v1): mime ∈ {jpeg,png,webp}, ≤5MB,
// non-empty base64; retry once → 502. Upload uses the sanitizeObjectKey
// contract + the invalid-slug → 'draft' folder fallback (mirrors the editor's
// uploadBlogImage).

import { type Env, HSTS } from "../_lib.ts";
import { requireAdmin } from "../_shared/admin-auth.ts";
import { isValidSlug } from "../_shared/slug.ts";

const IMAGES_API = "https://openrouter.ai/api/v1/images";
const PRIMARY = "google/gemini-3.1-flash-image-preview";
// Stable sibling of the primary (non-preview). Verified in the OpenRouter
// catalog 2026-08-29 — the original flux.2-klein-4b fallback does not exist.
const FALLBACK = "google/gemini-3.1-flash-image";
const ASPECT = "16:9";
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const ALLOWED_MIME = new Set(["image/jpeg", "image/png", "image/webp"]);
const RATE_DAY_MS = 86_400_000;
const RATE_DAY = 20;

const REFERENCE_IMAGES = [
  "https://res.cloudinary.com/djzwqixjt/image/upload/eldato/carousels/references/character-sheet.png",
  "https://res.cloudinary.com/djzwqixjt/image/upload/eldato/carousels/references/canonical-face-reference.jpg",
  "https://res.cloudinary.com/djzwqixjt/image/upload/eldato/carousels/references/canonical-portrait-reference.jpg",
];

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...HSTS },
  });
}

// ── Admin gate (shared — see _shared/admin-auth.ts) ───────────────────────
// ── Rate limiting (best-effort per isolate, keyed on user id) ─────────────
const dailyCounts = new Map<string, number[]>();

function rateLimitedDay(userId: string): boolean {
  const now = Date.now();
  const hits = (dailyCounts.get(userId) ?? []).filter((t) => now - t < RATE_DAY_MS);
  if (hits.length >= RATE_DAY) {
    dailyCounts.set(userId, hits);
    return true;
  }
  hits.push(now);
  dailyCounts.set(userId, hits);
  return false;
}

// ── Prompt building ────────────────────────────────────────────────────────
const FOUNDER_STYLE = `Photorealistic professional portrait of the same founder, natural park-walk / restaurant-chat setting, universal modern clothing (NOT tropical), soft natural light, shallow depth of field, warm editorial look, 16:9 blog cover composition with clear space on the left for title text.`;

const ABSTRACT_STYLE = `Abstract conceptual illustration, dark slate and cyan brand palette, subtle graph-node and knowledge-flow motifs (nodes, edges, confidence dials), elegant minimal composition, 16:9 blog cover, clear space on the left for title text.`;

function buildPrompt(title: string, tags: string[], mode: string): string {
  const tagLine = tags.length ? `Theme tags: ${tags.join(", ")}.` : "";
  const subject = `${title}. ${tagLine}`;
  if (mode === "abstract") return `${subject}\n${ABSTRACT_STYLE}`;
  return `${subject}\n${FOUNDER_STYLE}`;
}

// ── OpenRouter image call (base64 response) ────────────────────────────────
interface ImageResult {
  b64: string;
  mediaType: string;
  provider: string;
  cost: number;
}

async function callImageProvider(
  env: Env,
  model: string,
  prompt: string,
  mode: string,
): Promise<ImageResult | null> {
  const body: Record<string, unknown> = {
    model,
    prompt,
    aspect_ratio: ASPECT,
  };
  if (mode === "founder") {
    // OpenRouter ContentPartImage schema: type must be the literal "image_url"
    // and image_url is an OBJECT ({url}) — not a string. The old shape
    // {type:"input_image", image_url:"https://..."} was rejected with a 400
    // (ZodError) → both providers "fail" → 502 on every founder-mode call.
    body.input_references = REFERENCE_IMAGES.map((image_url) => ({ type: "image_url", image_url: { url: image_url } }));
  }
  let res: Response;
  try {
    res = await fetch(IMAGES_API, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.OPENROUTER_API_KEY ?? ""}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(120_000),
    });
  } catch {
    return null;
  }
  if (!res.ok) return null;
  let payload: { data?: Array<{ b64_json?: string; media_type?: string }>; usage?: { cost?: number } };
  try {
    payload = (await res.json()) as typeof payload;
  } catch {
    return null;
  }
  const first = payload.data?.[0];
  if (!first?.b64_json) return null;
  return {
    b64: first.b64_json,
    mediaType: first.media_type ?? "image/png",
    provider: model,
    cost: payload.usage?.cost ?? 0,
  };
}

// ── QC + upload ────────────────────────────────────────────────────────────
function mimeFromBase64(b64: string): string | null {
  let head: string;
  try {
    head = atob(b64.slice(0, 16));
  } catch {
    return null; // malformed base64 → QC failure (502, not a raw 500)
  }
  if (head.length < 8) return null;
  // JPEG \xff\xd8\xff, PNG \x89PNG\r\n\x1a\n, WebP RIFF....WEBP (bytes 8-11)
  const bytes = new Uint8Array([...head].map((c) => c.charCodeAt(0)));
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "image/jpeg";
  if (bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return "image/png";
  if (bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46) {
    const isWebp =
      bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50; // 'WEBP'
    if (isWebp) return "image/webp";
    return null; // RIFF but not WEBP (WAV/AVI polyglot) → QC failure
  }
  return null;
}

async function uploadImage(
  env: Env,
  b64: string,
  mime: string,
  slug: string,
): Promise<string | null> {
  try {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    if (bytes.length === 0 || bytes.length > MAX_IMAGE_BYTES) return null;
    if (!ALLOWED_MIME.has(mime)) return null;

    const ext = mime === "image/png" ? "png" : mime === "image/webp" ? "webp" : "jpg";
    const folder = isValidSlug(slug) ? slug : "draft"; // mirror uploadBlogImage fallback
    // Random suffix guarantees uniqueness even for same-ms concurrent/retry
    // generates (x-upsert: false would 400/502 on a key collision).
    const rand = crypto.randomUUID().slice(0, 8);
    const key = `${folder}/${Date.now()}-${rand}-generated-cover.${ext}`;

    const url = `${env.SUPABASE_URL}/storage/v1/object/blog-images/${key}`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        apikey: env.SUPABASE_SERVICE_ROLE_KEY ?? "",
        Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY ?? ""}`,
        "Content-Type": mime,
        "x-upsert": "false",
      },
      body: bytes,
      signal: AbortSignal.timeout(20_000),
    });
    if (!res.ok) return null;

    const publicUrl = `${env.SUPABASE_URL}/storage/v1/object/public/blog-images/${encodeURIComponent(folder)}/${encodeURIComponent(key.split("/")[1])}`;
    return publicUrl;
  } catch {
    return null;
  }
}

// ── Handler ────────────────────────────────────────────────────────────────
export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return json({ error: "not_configured" }, 503);
  }
  if (!env.OPENROUTER_API_KEY) {
    return json({ error: "not_configured", message: "OPENROUTER_API_KEY missing" }, 503);
  }
  const userId = await requireAdmin(env, request);
  if (!userId) return json({ error: "unauthorized", message: "Session expired or not an admin — refresh and log in again" }, 401);
  if (rateLimitedDay(userId)) return json({ error: "rate_limited", message: "Rate limit reached — try again shortly" }, 429);

  let input: { title?: unknown; tags?: unknown; mode?: unknown; slug?: unknown };
  try {
    input = (await request.json()) as typeof input;
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const title = typeof input.title === "string" ? input.title.trim() : "";
  const tags = Array.isArray(input.tags)
    ? input.tags.filter((t): t is string => typeof t === "string").slice(0, 10)
    : [];
  const mode = input.mode === "abstract" ? "abstract" : "founder";
  const slug = typeof input.slug === "string" ? input.slug : "";
  if (!title) return json({ error: "validation", message: "title required" }, 400);

  const prompt = buildPrompt(title, tags, mode);

  let result = await callImageProvider(env, PRIMARY, prompt, mode);
  if (!result) result = await callImageProvider(env, FALLBACK, prompt, mode);
  if (!result) return json({ error: "generation_failed", message: "both providers failed" }, 502);

  const mime = mimeFromBase64(result.b64);
  // Magic-byte sniff is the QC authority — NEVER fall back to the provider's
  // declared media_type (arbitrary non-image bytes could clear QC otherwise).
  if (!mime) return json({ error: "upload_failed", message: "generated image failed QC" }, 502);
  const publicUrl = await uploadImage(env, result.b64, mime, slug);
  if (!publicUrl) return json({ error: "upload_failed", message: "generated image failed QC or upload" }, 502);

  return json(
    {
      image_url: publicUrl,
      provider: result.provider,
      cost_estimate: result.cost,
      mode,
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
