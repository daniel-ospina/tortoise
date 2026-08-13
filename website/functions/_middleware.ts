// Host-based routing for premiselabs.co Pages project.
//
// Topology:
//   premiselabs.co          → company page   (index.html)
//   tortoise.premiselabs.co → product page   (product.html)
//   app.premiselabs.co      → dashboard      (separate tortoise-dashboard project)
//
// Cloudflare Pages serves one project per custom domain, so we route by
// Host header in a middleware: tortoise.* gets product.html, everything
// else (premiselabs.co + *.pages.dev previews) gets index.html.
// All other static assets (welcome.html, signup.html, signin.html) pass
// through unchanged.
//
// The product page lives ONLY on the tortoise host (served at its root via
// the rewrite below). The raw /product and /product.html paths are static
// assets on the same Pages project and would otherwise leak onto the
// company host — 404 them everywhere except the tortoise host.
export const onRequest: PagesFunction = async (context) => {
  const url = new URL(context.request.url);
  const host = context.request.headers.get("host") || "";

  // Product page must not be reachable on premiselabs.co (or previews).
  if (
    !host.startsWith("tortoise.") &&
    (url.pathname === "/product" || url.pathname === "/product.html")
  ) {
    return new Response("Not Found", { status: 404 });
  }

  // Only rewrite the root path — everything else serves its own asset.
  if (url.pathname !== "/") {
    return context.next();
  }

  if (host.startsWith("tortoise.")) {
    const res = await context.env.ASSETS.fetch(url.origin + "/product.html");
    return new Response(res.body, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "public, max-age=60",
      },
    });
  }

  // premiselabs.co (and any other host) → company page (index.html)
  return context.next();
};
