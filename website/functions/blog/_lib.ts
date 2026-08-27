// Shared blog rendering helpers — used by functions/blog/[[path]].ts.
// Not routed (underscore-prefixed helper file).
//
// Env bindings required (Pages Function environment variables):
//   SUPABASE_URL       e.g. https://ybetwichurajbfswfeqa.supabase.co
//   SUPABASE_ANON_KEY  public anon key (client-safe — public reads only)
// Local dev: wrangler pages dev reads .env / .dev.vars

import { marked } from "marked";
import sanitizeHtml from "sanitize-html";

// ── Types ──────────────────────────────────────────────────────────────────
export interface BlogPost {
  id: string;
  slug: string;
  title: string;
  body: string; // markdown
  excerpt: string | null;
  cover_image_url: string | null;
  tags: string[];
  author: string | null;
  meta_title: string | null;
  meta_description: string | null;
  published_at: string | null;
  updated_at: string;
}

export interface Env {
  SUPABASE_URL?: string;
  SUPABASE_ANON_KEY?: string;
  ASSETS: { fetch: (req: Request | string) => Promise<Response> };
}

export const SITE_URL = "https://tortoise.premiselabs.co";
const HSTS = { "Strict-Transport-Security": "max-age=31536000; includeSubDomains" };

// ── Supabase REST (anon — published-only reads are RLS-gated) ──────────────
export function supabaseRql(env: Env, table: string): string {
  const base = env.SUPABASE_URL ?? "https://ybetwichurajbfswfeqa.supabase.co";
  return `${base}/rest/v1/${table}`;
}

export async function fetchPublishedPosts(env: Env): Promise<BlogPost[]> {
  const url = supabaseRql(env, "blog_posts");
  const res = await fetch(
    `${url}?select=id,slug,title,body,excerpt,cover_image_url,tags,author,meta_title,meta_description,published_at,updated_at` +
      `&status=eq.published&hold_for_review=eq.false&order=published_at.desc`,
    {
      headers: {
        apikey: env.SUPABASE_ANON_KEY ?? "",
        Authorization: `Bearer ${env.SUPABASE_ANON_KEY ?? ""}`,
        Accept: "application/json",
      },
    },
  );
  if (!res.ok) throw new Error(`supabase ${res.status}`);
  const rows = (await res.json()) as BlogPost[];
  return rows.map((r) => ({ ...r, tags: r.tags ?? [] }));
}

export async function fetchPostBySlug(env: Env, slug: string): Promise<BlogPost | null> {
  const url = supabaseRql(env, "blog_posts");
  const res = await fetch(
    `${url}?select=id,slug,title,body,excerpt,cover_image_url,tags,author,meta_title,meta_description,published_at,updated_at` +
      `&slug=eq.${encodeURIComponent(slug)}&status=eq.published&hold_for_review=eq.false&limit=1`,
    {
      headers: {
        apikey: env.SUPABASE_ANON_KEY ?? "",
        Authorization: `Bearer ${env.SUPABASE_ANON_KEY ?? ""}`,
        Accept: "application/json",
      },
    },
  );
  if (!res.ok) throw new Error(`supabase ${res.status}`);
  const rows = (await res.json()) as BlogPost[];
  return rows[0] ?? null;
}

// ── Markdown → sanitized HTML (plan §6 markdown contract) ──────────────────
marked.setOptions({ gfm: true, breaks: false });

const ALLOWED_TAGS = [
  "p", "h2", "h3", "h4", "ul", "ol", "li", "a", "img", "blockquote",
  "code", "pre", "strong", "em", "table", "thead", "tbody", "tr", "th", "td",
];

export function renderMarkdown(markdown: string): string {
  const raw = marked.parse(markdown, { async: false }) as string;
  return sanitizeHtml(raw, {
    allowedTags: ALLOWED_TAGS,
    allowedAttributes: {
      a: ["href", "target", "rel"],
      img: ["src", "alt"],
      th: ["align"],
      td: ["align"],
    },
    allowedSchemes: ["https", "http"],
    // Never allow javascript:/data: URLs
    allowProtocolRelative: false,
    transformTags: {
      a: (tagName: string, attribs: Record<string, string>): { tagName: string; attribs: Record<string, string> } => {
        const href = attribs.href ?? "";
        if (!/^https?:\/\//i.test(href)) return { tagName: "a", attribs: { href: "#" } };
        return { tagName, attribs: { ...attribs, target: "_blank", rel: "noopener" } };
      },
      img: (tagName: string, attribs: Record<string, string>): { tagName: string; attribs: Record<string, string> } => {
        const src = attribs.src ?? "";
        if (!/^https?:\/\//i.test(src)) return { tagName: "img", attribs: { src: "", alt: attribs.alt ?? "" } };
        return { tagName, attribs };
      },
    },
  });
}

// ── HTML shell ─────────────────────────────────────────────────────────────
export function jsonLd(obj: unknown): string {
  return `<script type="application/ld+json">${JSON.stringify(obj)}</script>`;
}

export function headHtml(opts: {
  title: string;
  description: string;
  url: string;
  image?: string | null;
  type?: "website" | "article";
  jsonLd?: unknown[];
  noindex?: boolean;
}): string {
  const { title, description, url, image = null, type = "website", jsonLd: schemas = [], noindex = false } = opts;
  const imageTag = image ? `<meta property="og:image" content="${image}">` : "";
  const noindexTag = noindex ? `<meta name="robots" content="noindex,nofollow">` : "";
  const schemaTags = schemas.map(jsonLd).join("\n");
  return `
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${escapeHtml(title)}</title>
<meta name="description" content="${escapeHtml(description)}">
<meta property="og:title" content="${escapeHtml(title)}">
<meta property="og:description" content="${escapeHtml(description)}">
<meta property="og:type" content="${type}">
<meta property="og:url" content="${escapeHtml(url)}">
${imageTag}
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${escapeHtml(title)}">
<meta name="twitter:description" content="${escapeHtml(description)}">
<link rel="canonical" href="${escapeHtml(url)}">
${noindexTag}
${schemaTags}`;
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// consent.js + PostHog snippet — statically included by the render Function
// head (#1794 owns the head; #1799 wires client-side events only).

export function htmlPage(opts: { head: string; body: string; extraHead?: string }): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
${opts.head}
<link rel="icon" href="/blog/favicon.svg" type="image/svg+xml">
${opts.extraHead ?? ""}
<style>
:root{--bg:#060b14;--bg-soft:#0b1220;--text:#cbd5e1;--text-dim:#94a3b8;--accent:#06b6d4;--border:#1e293b;--serif:Georgia,'Times New Roman',serif;--mono:'SF Mono','Cascadia Code','Fira Code',JetBrains Mono,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--mono);font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
nav{display:flex;align-items:center;justify-content:space-between;padding:18px 32px;border-bottom:1px solid var(--border)}
.brand{font-size:15px;letter-spacing:.08em;color:#fff;font-weight:700}.brand span{color:var(--accent)}
nav .links a{margin-left:24px;color:var(--text-dim);font-size:13px}nav .links a.active{color:var(--accent)}
nav .links .cta{background:var(--accent);color:#04141a;padding:6px 14px;border-radius:6px;font-weight:700}
.wrap{max-width:1080px;margin:0 auto;padding:56px 32px 80px}
.head h1{font-family:var(--serif);font-size:42px;color:#fff;font-weight:400}
.head p{color:var(--text-dim);margin-top:8px;max-width:560px}
.tags{display:flex;gap:8px;margin:28px 0;flex-wrap:wrap}
.tag{border:1px solid var(--border);color:var(--text-dim);padding:4px 12px;border-radius:999px;font-size:12px}
.tag.on{border-color:var(--accent);color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:24px;margin-top:8px}
.card{background:var(--bg-soft);border:1px solid var(--border);border-radius:12px;overflow:hidden;display:block;transition:border-color .15s}
.card:hover{border-color:var(--accent)}
.card .cover{height:160px;display:flex;align-items:flex-end;padding:12px 16px;font-size:11px;color:rgba(255,255,255,.85);background-size:cover;background-position:center;background-color:#0e7490}
.card .body{padding:16px}
.card h3{font-family:var(--serif);font-size:20px;color:#fff;font-weight:400;line-height:1.35}
.card p{color:var(--text-dim);font-size:13px;margin-top:8px}
.card .meta{display:flex;gap:12px;margin-top:12px;color:#64748b;font-size:11px}
.empty{border:1px dashed var(--border);border-radius:12px;padding:64px 24px;text-align:center;color:var(--text-dim);margin-top:24px}
.art{max-width:720px;margin:0 auto;padding:48px 32px 100px}
.crumb{color:var(--text-dim);font-size:12px}.crumb a{color:var(--text-dim)}.crumb a:hover{color:var(--accent)}.crumb .sep{margin:0 8px;opacity:.5}
h1.art-title{font-family:var(--serif);font-size:40px;line-height:1.2;color:#fff;font-weight:400;margin-top:16px}
.art-meta{display:flex;align-items:center;gap:14px;margin-top:14px;color:var(--text-dim);font-size:12px;flex-wrap:wrap}
.art-meta .dot{opacity:.4}
.share-bar{display:flex;align-items:center;gap:10px;margin:20px 0;padding:14px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.share-bar .label{color:var(--text-dim);font-size:12px;margin-right:4px}
.share-bar a,.share-bar button{width:34px;height:34px;border-radius:8px;border:1px solid var(--border);background:var(--bg-soft);color:var(--text-dim);display:flex;align-items:center;justify-content:center;font-size:14px;cursor:pointer}
.share-bar a:hover,.share-bar button:hover{color:var(--accent);border-color:var(--accent)}
.cover-img{margin:24px 0;border-radius:12px;border:1px solid var(--border);min-height:320px;background-size:cover;background-position:center;background-color:#0e7490}
.art-body{font-size:15px;line-height:1.75;color:var(--text)}
.art-body h2{font-family:var(--serif);font-size:26px;color:#fff;font-weight:400;margin:40px 0 14px}
.art-body h3{font-family:var(--serif);font-size:20px;color:#fff;font-weight:400;margin:28px 0 10px}
.art-body h4{font-family:var(--serif);font-size:17px;color:#fff;font-weight:400;margin:22px 0 8px}
.art-body p{margin:14px 0}
.art-body ul,.art-body ol{margin:14px 0 14px 22px}
.art-body li{margin:6px 0}
.art-body blockquote{border-left:2px solid var(--accent);padding-left:16px;color:var(--text-dim);margin:20px 0;font-style:italic}
.art-body code{background:#0b1220;border:1px solid var(--border);padding:1px 6px;border-radius:4px;font-size:13px;color:#4ade80}
.art-body pre{background:#0b1220;border:1px solid var(--border);border-radius:8px;padding:16px;overflow-x:auto;margin:16px 0}
.art-body pre code{border:0;background:transparent;padding:0;color:inherit}
.art-body table{border-collapse:collapse;margin:16px 0;width:100%}
.art-body th,.art-body td{border:1px solid var(--border);padding:8px 12px;text-align:left}
.art-body img{max-width:100%;border-radius:8px;border:1px solid var(--border);margin:20px 0}
.end-note{margin-top:40px;padding:20px;border:1px solid var(--border);border-radius:10px;background:var(--bg-soft);color:var(--text-dim);font-size:13px}
.end-note strong{color:var(--accent)}
.mobile-share{display:none}
@media(max-width:720px){
  .wrap{padding:36px 20px 60px}.head h1{font-size:32px}
  .art{padding:32px 20px 120px}h1.art-title{font-size:30px}
  .mobile-share{display:flex;position:fixed;bottom:0;left:0;right:0;background:rgba(6,11,20,.96);border-top:1px solid var(--border);padding:10px 16px;gap:10px;justify-content:center;z-index:40}
  .mobile-share a,.mobile-share button{width:38px;height:38px;border-radius:8px;border:1px solid var(--border);background:var(--bg-soft);color:var(--text-dim);display:flex;align-items:center;justify-content:center;cursor:pointer}
}
</style>
</head>
<body>
${opts.body}
</body>
</html>`;
}

export function navHtml(active: string): string {
  return `<nav>
<div class="brand">Tortoise<span>.</span></div>
<div class="links">
  <a href="https://tortoise.premiselabs.co/docs" >Docs</a>
  <a href="/blog" class="${active === "blog" ? "active" : ""}">Blog</a>
  <a href="https://tortoise.premiselabs.co/#pricing">Pricing</a>
  <a href="https://tortoise.premiselabs.co/auth" class="cta">Log in</a>
</div>
</nav>`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

export function shareBarHtml(url: string, title: string): string {
  const encoded = encodeURIComponent(url);
  const text = encodeURIComponent(title);
  const utm = (n: string) => `${url}?utm_source=${n}&utm_medium=share`;
  return `<div class="share-bar">
<span class="label">Share</span>
<a href="https://twitter.com/intent/tweet?text=${text}&url=${encodeURIComponent(utm("twitter"))}" target="_blank" rel="noopener" title="Share on X" data-share="twitter">𝕏</a>
<a href="https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(utm("linkedin"))}" target="_blank" rel="noopener" title="Share on LinkedIn" data-share="linkedin">in</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(utm("facebook"))}" target="_blank" rel="noopener" title="Share on Facebook" data-share="facebook">f</a>
<a href="https://wa.me/?text=${text}%20${encodeURIComponent(url)}" target="_blank" rel="noopener" title="Share on WhatsApp" data-share="whatsapp">wa</a>
<button type="button" title="Copy link" data-share="copy">⧉</button>
</div>
<div class="mobile-share">
<a href="https://twitter.com/intent/tweet?text=${text}&url=${encodeURIComponent(utm("twitter"))}" target="_blank" rel="noopener" data-share="twitter">𝕏</a>
<a href="https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(utm("linkedin"))}" target="_blank" rel="noopener" data-share="linkedin">in</a>
<a href="https://wa.me/?text=${text}%20${encodeURIComponent(url)}" target="_blank" rel="noopener" data-share="whatsapp">wa</a>
<button type="button" title="Copy link" data-share="copy">⧉</button>
</div>`;
}

// Shared-response helpers
export function ok(html: string, cache: string): Response {
  return new Response(html, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": cache, ...HSTS },
  });
}

export function notFound(): Response {
  return new Response("Not Found", { status: 404, headers: { "Cache-Control": "no-store", ...HSTS } });
}

export function upstreamError(): Response {
  return new Response("Service Unavailable", {
    status: 503,
    headers: { "Cache-Control": "no-store", ...HSTS },
  });
}
