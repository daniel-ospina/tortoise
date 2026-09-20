#!/usr/bin/env node
/**
 * Focused mock Supabase Auth for the email-flow suites
 * (/auth/signup, /auth/reset, /auth/resend — #4054).
 *
 * Separate from the SHARED `mock_supabase.mjs` on purpose: that file is used by
 * the sign-in/recovery suites and does not implement `/signup`, `/recover` or
 * `/resend` with the refusals these routes turn on. Keeping a focused mock means
 * adding these endpoints cannot perturb the existing assertions.
 *
 * It emulates exactly the surface these routes use, and RECORDS every call so
 * "GoTrue was called" / "GoTrue was NOT called" is observed rather than inferred
 * from a status code. That recording is load-bearing for #801: the signup route
 * must create accounts through the hosted API (`/v1/signup/email`, mocked
 * separately in `mock_signup_api.mjs`) and NEVER through the anon-key
 * `/auth/v1/signup` that burns Supabase's project-wide SMTP bucket. The route
 * still signs the new user in server-side, which is the `/auth/v1/token` record.
 *
 *   POST /auth/v1/signup?redirect_to=…   body {email,password}
 *        - RETAINED as a regression trap: a correct #801 route never reaches
 *          it. If a call appears here the fix has been silently reverted.
 *        - `autoconfirm@example.test`  -> 200 flat TOKEN response (a session)
 *        - `existing@example.test`     -> 422 `user_already_exists` (refusal)
 *        - anything else               -> 200 bare USER object (confirmation
 *          required; NO token fields).
 *   POST /auth/v1/token?grant_type=password   body {email,password}
 *        - default -> 200 flat TOKEN response; `access_token` encodes the
 *          email so GET /auth/v1/user can resolve the same account's profile.
 *   POST /auth/v1/recover?redirect_to=…  body {email}
 *        - default                     -> 200 `{}` (identical for every address)
 *        - `refused@example.test`      -> 422 `user_not_found` — a NON-retryable
 *          provider refusal, which the route must still fold into a 200.
 *   POST /auth/v1/resend?redirect_to=…   body {email,type:"signup"}
 *        - same contract as /recover.
 *   GET  /auth/v1/user                   Bearer required; the profile behind the
 *        signed-in signup body.
 *
 * Fault injection (`/__mock/fault`) can make any endpoint answer 500 (retryable)
 * or 401 (`Invalid API key`, a configuration fault) so both 503 branches are
 * reachable. The raw password is never stored — only whether one was present.
 */
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_PORT ?? 9041);
const BASE = `http://127.0.0.1:${PORT}`;

const AUTO_USER = {
  id: "user-signup-auto-1",
  email: "autoconfirm@example.test",
  displayName: "Auto Confirmed",
};

/** Every upstream email-flow call, in order. Never contains a raw password. */
const calls = [];

/** fault values: false | 500 | 401 | 429 */
const FAULTS = { signup: false, recover: false, resend: false, profile: false, password: false };

const json = (res, status, body) => {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};

const server = createServer((req, res) => {
  const url = new URL(req.url, BASE);

  // GET /auth/v1/user — the profile read behind the signed-in signup body.
  if (url.pathname === "/auth/v1/user") {
    if (FAULTS.profile === 500) return json(res, 500, { error: "injected_profile_outage" });
    if (FAULTS.profile === 401) return json(res, 401, { error: "Invalid API key" });
    const auth = req.headers.authorization ?? "";
    if (!/^Bearer\s+\S/.test(auth)) return json(res, 401, { error: "missing_token" });
    const token = auth.replace(/^Bearer\s+/, "").trim();
    // `mock-access-<email>` is what the password grant below mints, so the
    // profile can be resolved for the exact account that signed up.
    const email = token.startsWith("mock-access-") ? token.slice("mock-access-".length) : null;
    if (email && email.includes("@")) {
      return json(res, 200, {
        id: `user-${email}`,
        email,
        user_metadata: { display_name: "Mock User" },
      });
    }
    // Backward-compatible: the legacy flat-token autoconfirm flow.
    return json(res, 200, {
      id: AUTO_USER.id,
      email: AUTO_USER.email,
      user_metadata: { display_name: AUTO_USER.displayName },
    });
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

    const redirectTo = url.searchParams.get("redirect_to");
    const email = typeof parsed.email === "string" ? parsed.email : null;

    if (url.pathname === "/auth/v1/signup") {
      calls.push({
        flow: "signup",
        email,
        hasPassword: typeof parsed.password === "string" && parsed.password.length > 0,
        redirectTo,
        authorization: req.headers.authorization ? "present" : null,
      });
      if (FAULTS.signup === 500) return json(res, 500, { error: "injected_provider_outage" });
      if (FAULTS.signup === 401) return json(res, 401, { error: "Invalid API key" });
      if (email === "existing@example.test") {
        return json(res, 422, { error_code: "user_already_exists", msg: "User already registered" });
      }
      if (email === AUTO_USER.email) {
        return json(res, 200, {
          access_token: "mock-access-signup-1",
          token_type: "bearer",
          expires_in: 3600,
          refresh_token: "mock-refresh-signup-1",
          user: { id: AUTO_USER.id, email: AUTO_USER.email },
        });
      }
      // Confirmation required: the BARE user object, no token fields.
      return json(res, 200, {
        id: "user-signup-1",
        email,
        confirmation_sent_at: new Date().toISOString(),
      });
    }

    if (url.pathname === "/auth/v1/token") {
      // POST /auth/v1/token?grant_type=password — the server-side password
      // grant the /auth/signup route runs AFTER the API creates the account.
      // Records the call so "did the BFF sign the new user in?" is observed,
      // and so a signup call can never masquerade as this one.
      calls.push({
        flow: "password",
        email,
        hasPassword: typeof parsed.password === "string" && parsed.password.length > 0,
        grantType: url.searchParams.get("grant_type"),
      });
      if (FAULTS.password === 500) return json(res, 500, { error: "injected_provider_outage" });
      if (FAULTS.password === 401) return json(res, 401, { error: "Invalid API key" });
      if (!email) return json(res, 400, { error_code: "validation_failed", msg: "missing email" });
      return json(res, 200, {
        access_token: `mock-access-${email}`,
        token_type: "bearer",
        expires_in: 3600,
        refresh_token: `mock-refresh-${email}`,
        user: { id: `user-${email}`, email },
      });
    }

    if (url.pathname === "/auth/v1/recover" || url.pathname === "/auth/v1/resend") {
      const flow = url.pathname === "/auth/v1/recover" ? "recover" : "resend";
      calls.push({
        flow,
        email,
        type: typeof parsed.type === "string" ? parsed.type : null,
        redirectTo,
        authorization: req.headers.authorization ? "present" : null,
      });
      const fault = FAULTS[flow];
      if (fault === 500) return json(res, 500, { error: "injected_provider_outage" });
      if (fault === 401) return json(res, 401, { error: "Invalid API key" });
      if (fault === 429) {
        // GoTrue's `over_email_send_rate_limit`, applied to the ADDRESS being
        // mailed. Real GoTrue brackets this with a Retry-After; the route must
        // fold it into the enumeration-safe 200 rather than leak it.
        const payload = JSON.stringify({
          error_code: "over_email_send_rate_limit",
          msg: "Email rate limit exceeded",
        });
        res.writeHead(429, { "Content-Type": "application/json", "Retry-After": "60" });
        return res.end(payload);
      }
      if (email === "refused@example.test") {
        return json(res, 422, { error_code: "user_not_found", msg: "No such user" });
      }
      if (email === "ratelimited@example.test") {
        // GoTrue's `over_email_send_rate_limit` is applied to the ADDRESS being
        // mailed, so it fires for THIS address and not for an unknown one — the
        // differential the route must not leak. Bracketed with Retry-After the
        // way real GoTrue does it.
        const limited = JSON.stringify({
          error_code: "over_email_send_rate_limit",
          msg: "Email rate limit exceeded",
        });
        res.writeHead(429, { "Content-Type": "application/json", "Retry-After": "60" });
        return res.end(limited);
      }
      // Identical body for every other address — known or unknown.
      return json(res, 200, {});
    }

    // Observability the tests assert on: what GoTrue actually received.
    if (url.pathname === "/__mock/calls") {
      const snapshot = [...calls];
      if (parsed.reset) calls.length = 0;
      return json(res, 200, { calls: snapshot, count: snapshot.length });
    }

    // Fault injection. POST {"signup":500,"recover":401,...} or false to clear.
    if (url.pathname === "/__mock/fault") {
      for (const k of ["signup", "recover", "resend", "profile", "password"]) {
        if (k in parsed) FAULTS[k] = parsed[k];
      }
      return json(res, 200, { ok: true, faults: { ...FAULTS } });
    }

    return json(res, 404, { error: "not_found", path: url.pathname });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`mock email-flows supabase listening on ${BASE}\n`);
});
