// RSS 2.0 feed for the blog — issue #1796.
// Published + !hold only, newest first, absolute URLs (E2E-11).
// Route: /blog/feed.xml (longest-prefix beats the blog/[[path]] catch-all).

import { type Env, fetchPublishedPosts, SITE_URL, escapeHtml, SLUG_RE, HSTS } from "./_lib.ts";

function rfc822(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toUTCString();
  } catch {
    return "";
  }
}

export const onRequestGet: PagesFunction<Env> = async (context) => {
  const { env } = context;
  try {
    const posts = await fetchPublishedPosts(env);
    const items = posts
      .filter((p) => SLUG_RE.test(p.slug))
      .map((p) => {
        const desc = escapeHtml(p.excerpt ?? p.title);
        return `  <item>
    <title>${escapeHtml(p.title)}</title>
    <link>${SITE_URL}/blog/${p.slug}</link>
    <guid isPermaLink="true">${SITE_URL}/blog/${p.slug}</guid>
    <pubDate>${rfc822(p.published_at)}</pubDate>
    <description>${desc}</description>
  </item>`;
      })
      .join("\n");
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>Tortoise Blog</title>
  <link>${SITE_URL}/blog</link>
  <description>Memory for agents to remember why, not just what — notes on the epistemic graph, EP belief propagation, and building agents that reason over what they know.</description>
  <atom:link href="${SITE_URL}/blog/feed.xml" rel="self" type="application/rss+xml"/>
${items}
</channel>
</rss>`;
    return new Response(xml, {
      status: 200,
      headers: { "Content-Type": "application/rss+xml; charset=utf-8", "Cache-Control": "public, max-age=300", ...HSTS },
    });
  } catch {
    return new Response("Service Unavailable", {
      status: 503,
      headers: { "Cache-Control": "no-store", ...HSTS },
    });
  }
};
