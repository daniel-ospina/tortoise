// Blog SSR render — /blog (index) + /blog/:slug (article) — issue #1794.
//
// Server-rendered HTML (no client JS required for content — AI-crawler
// visible). Renders published, non-held posts from Supabase; markdown→HTML
// sanitized (plan §6). Catch-all: non-post paths (static assets like
// og-image, favicons) fall through to ASSETS.
//
// NOTE: /blog/sitemap.xml and /blog/feed.xml are routed by more-specific
// Function files (#1796) — longest-prefix routing wins over this catch-all.

import {
  type Env, fetchPublishedPosts, fetchPostBySlug, renderMarkdown,
  headHtml, htmlPage, navHtml, formatDate, shareBarHtml, validCoverUrl,
  ok, notFound, upstreamError, SITE_URL, escapeHtml, SLUG_RE,
} from "./_lib.ts";

export const onRequestGet: PagesFunction<Env> = async (context) => {
  const { request, env, params } = context;
  const url = new URL(request.url);
  const path = url.pathname; // "/blog" or "/blog/<slug>"

  // ── ASSETS fallback for non-post paths (favicons, og-image, ...) ─────────
  // E2E-1 asserts /blog/og-image.png returns 200 via this fallback.
  if (path.includes(".")) {
    const res = await env.ASSETS.fetch(request);
    if (res.status !== 404) return res;
    return notFound();
  }

  try {
    const segments = (params.path as string[] | undefined) ?? [];
    const slug = segments.join("/");

    if (!slug) {
      return renderIndex(env, url);
    }
    return renderArticle(env, slug);
  } catch {
    // Supabase unreachable / upstream failure → never serve stale HTML
    return upstreamError();
  }
};

// ── /blog index ────────────────────────────────────────────────────────────
async function renderIndex(env: Env, url: URL): Promise<Response> {
  const posts = await fetchPublishedPosts(env);
  const tag = url.searchParams.get("tag");
  const filtered = tag ? posts.filter((p) => p.tags.includes(tag)) : posts;

  const tags = [...new Set(posts.flatMap((p) => p.tags))].sort();

  const cards = filtered.map((p) => {
    const coverUrl = p.cover_image_url && validCoverUrl(p.cover_image_url) ? p.cover_image_url : null;
    const cover = coverUrl ? `style="background-image:url('${escapeHtml(coverUrl)}')"` : "";
    const excerpt = p.excerpt ? `<p>${escapeHtml(p.excerpt)}</p>` : "";
    return `<a class="card" href="/blog/${escapeHtml(p.slug)}">
  <div class="cover" ${cover}></div>
  <div class="body">
    <h3>${escapeHtml(p.title)}</h3>
    ${excerpt}
    <div class="meta"><span>${formatDate(p.published_at)}</span></div>
  </div>
</a>`;
  }).join("\n");

  const tagRow = `<div class="tags">
<a class="tag ${tag ? "" : "on"}" href="/blog">All</a>
${tags.map((t) => `<a class="tag ${tag === t ? "on" : ""}" href="/blog?tag=${encodeURIComponent(t)}">${escapeHtml(t)}</a>`).join("")}
</div>`;

  const listHtml = filtered.length
    ? `<div class="grid">${cards}</div>`
    : posts.length
      ? `<div class="empty">No posts tagged “${escapeHtml(tag ?? "")}” yet.</div>`
      : `<div class="empty">No posts yet — the first one is being written by an agent right now.</div>`;

  const indexUrl = tag ? `${SITE_URL}/blog?tag=${encodeURIComponent(tag)}` : `${SITE_URL}/blog`;
  const head = headHtml({
    title: tag ? `Blog · ${tag} — Tortoise` : "Blog — Tortoise",
    description:
      "Memory for agents to remember why, not just what. Notes on the epistemic graph, EP belief propagation, and building agents that reason over what they know.",
    url: indexUrl,
    type: "website",
    jsonLd: [
      {
        "@context": "https://schema.org",
        "@type": "Blog",
        name: "Tortoise Blog",
        url: `${SITE_URL}/blog`,
        blogPost: posts.slice(0, 20).map((p) => ({
          "@type": "BlogPosting",
          headline: p.title,
          url: `${SITE_URL}/blog/${p.slug}`,
          datePublished: p.published_at,
        })),
      },
    ],
  });

  const body = `${navHtml("blog")}
<div class="wrap">
  <div class="head"><h1>Blog</h1><p>Memory for agents to remember why, not just what. Notes on the epistemic graph, EP belief propagation, and building agents that reason over what they know.</p></div>
  ${tagRow}
  ${listHtml}
</div>`;

  return ok(htmlPage({ head, body, extraHead: analyticsHead() }), "public, max-age=300");
}

// ── /blog/:slug article ────────────────────────────────────────────────────
async function renderArticle(env: Env, slug: string): Promise<Response> {
  if (!SLUG_RE.test(slug)) return notFound();

  const post = await fetchPostBySlug(env, slug);
  if (!post) return notFound();

  const title = post.meta_title ?? `${post.title} | Tortoise`;
  const description = post.meta_description ?? (post.excerpt ?? post.title);
  const url = `${SITE_URL}/blog/${post.slug}`;
  const bodyHtml = renderMarkdown(post.body);
  const updated = formatDate(post.updated_at);
  const published = formatDate(post.published_at);
  const noCache = isRecentlyEdited(post.updated_at) ? "no-cache" : "public, max-age=300";

  const head = headHtml({
    title,
    description,
    url,
    image: post.cover_image_url,
    type: "article",
    jsonLd: [
      {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        headline: post.title,
        description,
        url,
        image: post.cover_image_url ?? undefined,
        author: { "@type": "Organization", name: post.author ?? "Tortoise team" },
        datePublished: post.published_at,
        dateModified: post.updated_at,
      },
      {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "Blog", item: `${SITE_URL}/blog` },
          { "@type": "ListItem", position: 2, name: post.title, item: url },
        ],
      },
    ],
  });

  const tags = post.tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join(" ");
  const cover = post.cover_image_url && validCoverUrl(post.cover_image_url)
    ? `<div class="cover-img" style="background-image:url('${escapeHtml(post.cover_image_url)}')"></div>`
    : "";

  const body = `${navHtml("blog")}
<div class="art">
  <div class="crumb"><a href="/blog">Blog</a><span class="sep">/</span><span>${escapeHtml(post.title)}</span></div>
  <h1 class="art-title">${escapeHtml(post.title)}</h1>
  <div class="art-meta">
    <span>By ${escapeHtml(post.author ?? "Tortoise team")}</span><span class="dot">·</span><span>${published}</span>
    ${updated && updated !== published ? `<span class="dot">·</span><span>Updated ${updated}</span>` : ""}
    ${tags ? `<span class="dot">·</span>${tags}` : ""}
  </div>
  ${shareBarHtml(url, post.title)}
  ${cover}
  <div class="art-body">${bodyHtml}</div>
  <div class="end-note"><strong>More from the blog →</strong> Follow Tortoise updates via <a href="/blog/feed.xml">RSS</a>.</div>
</div>`;

  return ok(htmlPage({ head, body, extraHead: analyticsHead() }), noCache);
}

function isRecentlyEdited(updatedAtIso: string): boolean {
  try {
    const updated = new Date(updatedAtIso).getTime();
    return Date.now() - updated < 30 * 60 * 1000; // 30-min no-cache window (plan W5)
  } catch {
    return false;
  }
}

function analyticsHead(): string {
  // consent.js + PostHog snippet — statically included (#1794 owns the head;
  // #1799 wires events only).
  return `<script src="/consent.js" defer></script>
<script>
(function () {
  var consent = window.consentState ? window.consentState() : null;
  if (consent === "granted") {
    !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.async=!0,p.src="https://us-assets.i.posthog.com/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;u._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
    posthog.init("phc_zvBi25UoCxrq79qS7cudZhfAS3XQwEfrzEoZfR2EHkjS", { api_host: "https://us.i.posthog.com" });
  }
})();
</script>`;
}
