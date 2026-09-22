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
 * Control endpoints (`/__mock/*`) let the test flip the admin verdict and read
 * what was seen. No route is stubbed in a way that would hide the property:
 * the credential really is attached server-side (the test asserts it), and the
 * upstream really does refuse the empty body.
 */
import http from "node:http";

const PORT = Number(process.env.MOCK_PORT || 9011);

let admin = true;
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
      return json(res, 200, admin);
    }

    if (req.method === "POST" && url.pathname.startsWith("/blog/api/")) {
      seen.push({ kind: "blog", path: url.pathname, auth, body, contentType: req.headers["content-type"] || "" });
      // The real purge route refuses an empty body with 400 invalid_slug — the
      // status matters: a 200 would let a broken proxy pass.
      return json(res, 400, { error: "invalid_slug" });
    }

    if (url.pathname === "/__mock/state") {
      return json(res, 200, { admin, seen });
    }
    if (url.pathname === "/__mock/admin") {
      admin = JSON.parse(body || "{}").value === true;
      return json(res, 200, { admin });
    }
    if (url.pathname === "/__mock/reset") {
      seen.length = 0;
      admin = true;
      return json(res, 200, { ok: true });
    }

    return json(res, 404, { error: "not_found", path: url.pathname });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`blog-admin rpc mock on ${PORT}`);
});
