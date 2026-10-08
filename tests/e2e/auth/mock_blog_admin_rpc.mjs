/**
 * Mock for the #4171 admin-gate + /blog/api proxy suite.
 *
 * One process serves BOTH upstreams the app-origin Functions call, because the
 * test binds `SUPABASE_URL` and `BLOG_ORIGIN` to the same origin and their
 * paths do not collide:
 *
 *   POST /rest/v1/rpc/is_admin   — the gate's membership check. Returns the
 *                                  current admin verdict (a JSON boolean), and
 *                                  records the Authorization header so the test
 *                                  can prove the gate minted a token and sent it
 *                                  as the USER (not the anon key).
 *   POST /blog/api/*             — the /blog/api proxy's upstream. Returns a
 *                                  400 invalid_slug (the real route's own
 *                                  refusal) and records the attached credential
 *                                  + body, so the test can prove the proxy
 *                                  forwarded them rather than the browser.
 *
 *   GET/POST /rest/v1/blog_posts — the /api/sb/* Token Handler's PostgREST
 *   GET/POST /storage/v1/object/blog-images/*
 *                                — upstream (#4178). Records the method, path,
 *                                  search and the FULL credential headers the
 *                                  upstream actually saw, so the suite can prove
 *                                  the credential is server-minted and the
 *                                  CLIENT's cookie/authorization/apikey never
 *                                  arrived. Answers a benign 200.
 *
 * Control endpoints (`/__mock/*`) let the test flip the admin verdict, inject an
 * upstream 5xx on a chosen surface (`data` or `admin`, so the Token Handler's
 * two independent fault branches stay independently provable), and read what was
 * seen. No route is stubbed in a way that would
 * hide the property: the credential really is attached server-side (the test
 * asserts it), and the upstream really does refuse the empty body.
 */
import http from "node:http";

const PORT = Number(process.env.MOCK_PORT || 9011);

let admin = true;
// #4178/#3559: a 5xx, injectable per upstream SURFACE. The Token Handler
// answers a fault with 503 (never a sign-out) at TWO independent places — the
// `is_admin` membership check (`checkAdmin`) and the proxied DATA call — and a
// single whole-mock flag would let one test reach the fault through the other's
// branch, so neither branch would have an independent guard (#3559 review).
// ONE mechanism (`upstreamFault`), ONE control endpoint, with a target so each
// branch is provable on its own. `value: true` with no target keeps #4178's
// meaning and injects on the DATA endpoints only.
let upstreamFault = null; // null | "data" | "admin" | "all"
const faulted = (surface) => upstreamFault === surface || upstreamFault === "all";
const seen = [];

function json(res, status, body) {
  const buf = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { "content-type": "application/json", "content-length": buf.length });
  res.end(buf);
}

const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
    const auth = req.headers["authorization"] || "";

    if (req.method === "POST" && url.pathname === "/rest/v1/rpc/is_admin") {
      seen.push({ kind: "is_admin", auth });
      // #3559: the gate's OWN upstream is faultable too. `checkAdmin` must turn a
      // fault into 503 (unavailable), never 403 (not_admin) — conflating a store
      // fault with a signed-in non-admin is the #3485 class.
      if (faulted("admin")) return json(res, 500, { error: "upstream_fault" });
      return json(res, 200, admin);
    }

    if (req.method === "POST" && url.pathname.startsWith("/blog/api/")) {
      seen.push({ kind: "blog", path: url.pathname, auth, body, contentType: req.headers["content-type"] || "" });
      // The real purge route refuses an empty body with 400 invalid_slug — the
      // status matters: a 200 would let a broken proxy pass.
      return json(res, 400, { error: "invalid_slug" });
    }

    // #4178: the /api/sb/* Token Handler's upstream — the console's PostgREST
    // reads/writes on `blog_posts` and its Storage uploads. Every header the
    // upstream actually received is recorded, so the suite can assert the
    // credential is the SERVER's and the client's never arrived.
    if (
      (req.method === "GET" || req.method === "POST" || req.method === "PATCH" || req.method === "DELETE") &&
      (url.pathname === "/rest/v1/blog_posts" || url.pathname.startsWith("/rest/v1/blog_posts/"))
    ) {
      seen.push({
        kind: "blog_posts",
        method: req.method,
        path: url.pathname,
        search: url.search,
        auth,
        apikey: req.headers["apikey"] || "",
        cookie: req.headers["cookie"] || "",
        contentType: req.headers["content-type"] || "",
        body,
      });
      if (faulted("data")) return json(res, 500, { error: "upstream_fault" });
      return json(res, 200, []);
    }

    if (
      (req.method === "GET" || req.method === "POST" || req.method === "PUT") &&
      (url.pathname === "/storage/v1/object/blog-images" ||
        url.pathname.startsWith("/storage/v1/object/blog-images/"))
    ) {
      seen.push({
        kind: "storage",
        method: req.method,
        path: url.pathname,
        search: url.search,
        auth,
        apikey: req.headers["apikey"] || "",
        cookie: req.headers["cookie"] || "",
        contentType: req.headers["content-type"] || "",
        body,
      });
      if (faulted("data")) return json(res, 500, { error: "upstream_fault" });
      return json(res, 200, { Key: url.pathname.slice(1) });
    }

    if (url.pathname === "/__mock/state") {
      return json(res, 200, { admin, upstreamFault, seen });
    }
    if (url.pathname === "/__mock/admin") {
      admin = JSON.parse(body || "{}").value === true;
      return json(res, 200, { admin });
    }
    if (url.pathname === "/__mock/upstream-fault") {
      const p = JSON.parse(body || "{}");
      // No target on an ON call preserves #4178's data-only fault.
      upstreamFault = p.value === true ? p.target || "data" : null;
      return json(res, 200, { upstreamFault });
    }
    if (url.pathname === "/__mock/reset") {
      seen.length = 0;
      admin = true;
      upstreamFault = null;
      return json(res, 200, { ok: true });
    }

    return json(res, 404, { error: "not_found", path: url.pathname });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`blog-admin rpc mock on ${PORT}`);
});
