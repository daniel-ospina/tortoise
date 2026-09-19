#!/usr/bin/env node
/**
 * Minimal mock Supabase Auth for the /api/profile + /auth/set-email suite.
 *
 * Separate from `mock_supabase.mjs` on purpose: that file is shared with the
 * other BFF suites and does not record what a caller sent to
 * `PUT /auth/v1/user`, so "was the SERVER-held token attached?" and "what
 * attributes were written?" would be unobservable. Keeping this focused means
 * adding those observations cannot perturb the sign-in/recovery assertions in
 * the existing suite. (`test_link_flow.py` made the same call for the same
 * reason.)
 *
 * It emulates the surface the two routes actually use:
 *   GET /auth/v1/user  — the profile read (`_shared/auth/supabase.ts::
 *                        fetchUserProfile`), authenticated.
 *   PUT /auth/v1/user  — the profile/email write (vendored supabase-js
 *                        `updateUser`: `PUT ${url}/user`, Bearer auth),
 *                        authenticated.
 *
 * Nothing that matters is stubbed away:
 *   - the endpoint REQUIRES a Bearer token, so "did the route attach the
 *     session's credential?" is answered by the upstream, not assumed
 *   - the exact Authorization header is recorded, so a test can prove the
 *     SERVER-held token was used and a client-supplied one was not
 *   - `double_confirm_changes = true` is emulated faithfully: a NEW address
 *     comes back as `new_email` with `email` UNCHANGED, i.e. PENDING. A route
 *     that reported that as an immediate change would be caught.
 *
 * Control surface:
 *   POST /__mock/calls  { reset? }            -> the recorded calls
 *   POST /__mock/fault  { outage?, unauthorized?, rateLimit?, reject?,
 *                         immediateEmailChange? }
 *   POST /__mock/state  { reset? }            -> the current mock user
 */
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_PORT ?? 8991);

/** Every `/auth/v1/user` request, in order. */
const calls = [];

const DEFAULT_USER = () => ({
  id: "user-123",
  email: "user-123@example.test",
  user_metadata: { display_name: "Display user-123" },
});

let user = DEFAULT_USER();

const FAULTS = {
  /** any /auth/v1/user request -> 500 (provider outage) */
  outage: false,
  /** any /auth/v1/user request -> 401 (credential rejected upstream) */
  unauthorized: false,
  /** any /auth/v1/user request -> 429 (rate limited) */
  rateLimit: false,
  /** when set, a write -> 422 with this error code (e.g. "email_exists") */
  reject: null,
  /** when true a new email is applied immediately instead of going pending */
  immediateEmailChange: false,
};

const json = (res, status, body) => {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};

const server = createServer((req, res) => {
  let raw = "";
  req.on("data", (c) => (raw += c));
  req.on("end", () => {
    const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
    let parsed = {};
    try {
      parsed = raw ? JSON.parse(raw) : {};
    } catch {
      /* leave empty */
    }

    // --- control surface ---------------------------------------------------
    if (url.pathname === "/__mock/calls") {
      const seen = [...calls];
      if (parsed.reset) calls.length = 0;
      return json(res, 200, { calls: seen, count: seen.length });
    }

    if (url.pathname === "/__mock/fault") {
      for (const k of ["outage", "unauthorized", "rateLimit", "immediateEmailChange"]) {
        if (typeof parsed[k] === "boolean") FAULTS[k] = parsed[k];
      }
      if ("reject" in parsed) {
        FAULTS.reject = typeof parsed.reject === "string" ? parsed.reject : null;
      }
      return json(res, 200, { ok: true, faults: { ...FAULTS } });
    }

    if (url.pathname === "/__mock/state") {
      if (parsed.reset) user = DEFAULT_USER();
      return json(res, 200, { user });
    }

    // --- the endpoint under test ------------------------------------------
    if (url.pathname === "/auth/v1/user") {
      const authorization = req.headers.authorization ?? null;
      calls.push({
        method: req.method,
        path: url.pathname,
        authorization,
        body: raw ? parsed : null,
      });

      // The real endpoint is authenticated. Answering 401 here is what makes
      // "did the route attach the session's credential?" observable.
      if (!authorization || !/^Bearer\s+\S/.test(authorization)) {
        return json(res, 401, { error: "missing_token" });
      }
      if (FAULTS.unauthorized) return json(res, 401, { error: "invalid_token" });
      if (FAULTS.outage) return json(res, 500, { error: "injected_provider_outage" });
      if (FAULTS.rateLimit) return json(res, 429, { error: "over_request_rate_limit" });

      if (req.method === "GET" || req.method === "HEAD") {
        return json(res, 200, user);
      }

      if (FAULTS.reject) {
        return json(res, 422, { error_code: FAULTS.reject, message: "rejected upstream" });
      }

      const body = parsed ?? {};

      // Email change, `double_confirm_changes = true`: the requested address is
      // recorded as `new_email` and `email` stays the CURRENT one until the
      // confirmation lands. This is the state the route must not call "changed".
      if (typeof body.email === "string") {
        if (FAULTS.immediateEmailChange) {
          user = { ...user, email: body.email };
        } else if (body.email.toLowerCase() !== user.email.toLowerCase()) {
          user = { ...user, new_email: body.email, email_change_sent_at: new Date().toISOString() };
        }
        return json(res, 200, user);
      }

      // Metadata update (display name). GoTrue MERGES `data` into user_metadata.
      if (body.data && typeof body.data === "object") {
        user = { ...user, user_metadata: { ...(user.user_metadata ?? {}), ...body.data } };
        return json(res, 200, user);
      }

      // A password-only update (not exercised here) is a no-op success.
      if (typeof body.password === "string") return json(res, 200, user);

      return json(res, 400, { error: "unrecognized_update" });
    }

    return json(res, 404, { error: "not_found", path: url.pathname });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`mock profile supabase listening on http://127.0.0.1:${PORT}\n`);
});
