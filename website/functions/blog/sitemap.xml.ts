// Dynamic blog sitemap — issue #1796.
// Rendered from Supabase at request time (published + !hold only) so it is
// never stale vs the deploy cycle (plan §4.2 option (a)).
// Route: /blog/sitemap.xml (longest-prefix beats the blog/[[path]] catch-all).

import { type Env, fetchPublishedPosts, SITE_URL, SLUG_RE, HSTS } from "./_lib.ts";

export const onRequestGet: PagesFunction<Env> = async (context) => {
  const { env } = context;
  try {
    const posts = await fetchPublishedPosts(env);
    const urls = posts
      .filter((p) => SLUG_RE.test(p.slug)) // defense-in-depth: DB CHECK constrains slugs, but never emit unvalidated
      .map(
        (p) =>
          `  <url><loc>${SITE_URL}/blog/${p.slug}</loc><lastmod>${(p.updated_at ?? "").slice(0, 10)}</lastmod></url>`,
      )
      .join("\n");
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>${SITE_URL}/blog</loc></url>
${urls}
</urlset>`;
    return new Response(xml, {
      status: 200,
      headers: { "Content-Type": "application/xml; charset=utf-8", "Cache-Control": "public, max-age=300", ...HSTS },
    });
  } catch {
    return new Response("Service Unavailable", {
      status: 503,
      headers: { "Cache-Control": "no-store", ...HSTS },
    });
  }
};
