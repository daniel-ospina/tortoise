#!/usr/bin/env node
/**
 * Minimal mock Supabase Auth for the /auth/link suite.
 *
 * Separate from `mock_supabase.mjs` on purpose: that file is shared with the
 * other BFF suites, and this route only needs GoTrue's AUTHENTICATED
 * link-identity endpoint. Keeping it separate means adding the link endpoint
 * cannot perturb the sign-in/recovery assertions in the existing suite.
 *
 * It emulates the surface `/auth/link` actually uses:
 *   GET /auth/v1/user/identities/authorize  — the link flow starter.
 *       Requires `Authorization: Bearer <token>`; with
 *       `skip_http_redirect=true` returns JSON `{ url }`, which is what the
 *       real GoTrue returns and what supabase-js consumes. Every call is
 *       recorded so a test can prove both "GoTrue WAS called" and "GoTrue was
 *       NOT called" by observation rather than inference.
 *   POST /__mock/link-calls                 — read/reset that record.
 */
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_PORT ?? 8971);
const BASE = `http://127.0.0.1:${PORT}`;

/** Every authenticated link-identity request, in order. */
const linkCalls = [];

const json = (res, status, body, headers = {}) => {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(JSON.stringify(body));
};

const server = createServer((req, res) => {
  const url = new URL(req.url, BASE);

  if (url.pathname === "/auth/v1/user/identities/authorize") {
    const provider = url.searchParams.get("provider");
    const redirectTo = url.searchParams.get("redirect_to");
    const challenge = url.searchParams.get("code_challenge");
    const method = url.searchParams.get("code_challenge_method");
    const skip = url.searchParams.get("skip_http_redirect");
    const authorization = req.headers.authorization ?? null;

    linkCalls.push({
      provider,
      redirect_to: redirectTo,
      code_challenge: challenge,
      code_challenge_method: method,
      skip_http_redirect: skip,
      // Only the SHAPE is recorded — never a real token.
      authorization: authorization ? "present" : null,
    });

    // The real endpoint is authenticated. Answering 401 here is what makes the
    // "does /auth/link attach the session's credential?" question observable.
    if (!authorization || !/^Bearer\s+\S/.test(authorization)) {
      return json(res, 401, { error: "missing_token" });
    }
    if (!provider || !redirectTo || !challenge || !method) {
      return json(res, 400, { error: "missing_param" });
    }

    // Stand-in for the provider hop GoTrue would return. It echoes the PKCE
    // parameters so the test can assert on the browser-facing 302.
    const next = new URL(`${BASE}/auth/v1/link-continue`);
    next.searchParams.set("provider", provider);
    next.searchParams.set("redirect_to", redirectTo);
    next.searchParams.set("code_challenge", challenge);
    next.searchParams.set("code_challenge_method", method);

    if (skip === "true") return json(res, 200, { url: next.toString() });
    res.writeHead(302, { Location: next.toString() });
    return res.end();
  }

  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    let parsed = {};
    try {
      parsed = body ? JSON.parse(body) : {};
    } catch {
      /* leave empty */
    }

    if (url.pathname === "/__mock/link-calls") {
      const calls = [...linkCalls];
      if (parsed.reset) linkCalls.length = 0;
      return json(res, 200, { calls, count: calls.length });
    }

    return json(res, 404, { error: "not_found", path: url.pathname });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`mock link supabase listening on ${BASE}\n`);
});
