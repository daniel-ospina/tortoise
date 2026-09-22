#!/usr/bin/env node
/**
 * Mock hosted API for the /auth/signup BFF suite (#4054 / #801).
 *
 * The BFF route under test POSTs account creation to
 * `${API_ORIGIN}/v1/signup/email` instead of calling GoTrue's anon-key
 * `/auth/v1/signup`. This mock emulates exactly that one upstream surface and
 * RECORDS every call, so "the BFF proxied the API" — and, crucially, "GoTrue's
 * email-sending /signup was NEVER called" — are observed rather than inferred.
 *
 * It is deliberately a SEPARATE origin from the GoTrue mock
 * (`mock_email_flows_supabase.mjs`), mirroring production: `API_ORIGIN` and
 * `SUPABASE_URL` are different hosts, and collapsing them in the test would
 * hide a route that reads the wrong binding.
 *
 * Contract emulated (tortoise/hosted_api.py `POST /v1/signup/email`,
 * `EmailSignupResponse`):
 *   default email                    -> 200 {user_id, email, email_confirm:true,
 *                                            message:"user_created"}
 *   confirm@example.test             -> 200 {…, email_confirm:false,
 *                                            message:"user_created"}
 *     (TORTOISE_SIGNUP_EMAIL_CONFIRM=false opt-in funnel — confirmation needed)
 *   existing@example.test            -> 409 {detail:{message:"already_registered",
 *                                            email}}
 *   weak@example.test                -> 422 {detail:"Password is too weak. …"}
 *
 * Fault injection (`POST /__api/fault`) makes the endpoint answer 429
 * (with `Retry-After`, the #863 tier code), 503, 500 or 401, or DROP the
 * connection ("network") so the BFF's transport-fault branch is reachable.
 */
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_PORT ?? 9042);
const BASE = `http://127.0.0.1:${PORT}`;

/** Every upstream signup call, in order. Never contains a raw password. */
const calls = [];

/** fault values: false | 429 | 500 | 503 | 401 | "network" */
const FAULTS = { signup: false };

const json = (res, status, body, extraHeaders = {}) => {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
    ...extraHeaders,
  });
  res.end(payload);
};

const server = createServer((req, res) => {
  const url = new URL(req.url, BASE);

  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    let parsed = {};
    try {
      parsed = body ? JSON.parse(body) : {};
    } catch {
      /* leave empty */
    }

    if (url.pathname === "/v1/signup/email") {
      const email = typeof parsed.email === "string" ? parsed.email : null;
      calls.push({
        flow: "signup_email",
        email,
        hasPassword: typeof parsed.password === "string" && parsed.password.length > 0,
        contentType: req.headers["content-type"] ?? null,
        authorization: req.headers.authorization ? "present" : null,
        // The Turnstile token the BFF must FORWARD (#4104). The hosted API's
        // `_check_turnstile` reads `cf-turnstile-response` (or `turnstile_token`)
        // and 400s when a secret is configured but the token is absent — so if
        // the route drops it, provisioning Turnstile breaks every signup.
        turnstile: parsed["cf-turnstile-response"] ?? parsed.turnstile_token ?? null,
      });

      if (FAULTS.signup === "network") {
        // No response at all: fetch rejects, exercising the transport-fault
        // branch rather than an HTTP-status branch.
        return req.socket.destroy();
      }
      if (FAULTS.signup === 429) {
        return json(
          res,
          429,
          {
            detail: {
              message: "Too many registration attempts. Please try again later.",
              error_code: "over_request_rate_limit_ip",
            },
          },
          { "Retry-After": "3600" },
        );
      }
      if (FAULTS.signup === 503) {
        return json(res, 503, {
          detail: "Email signup is not available on this deployment.",
        });
      }
      if (FAULTS.signup === 500) {
        return json(res, 500, { detail: "Signup service temporarily unavailable." });
      }
      if (FAULTS.signup === 401) {
        return json(res, 401, { detail: "Invalid API key" });
      }

      if (email === "existing@example.test") {
        return json(res, 409, {
          detail: { message: "already_registered", email },
        });
      }
      if (email === "weak@example.test") {
        return json(res, 422, {
          detail: "Password is too weak. Use at least 8 characters with a mix of letters, numbers, and symbols.",
        });
      }
      if (email === "confirm@example.test") {
        return json(res, 200, {
          user_id: "api-user-confirm-1",
          email,
          email_confirm: false,
          message: "user_created",
        });
      }
      return json(res, 200, {
        user_id: "api-user-new-1",
        email,
        email_confirm: true,
        message: "user_created",
      });
    }

    // Observability the tests assert on: what the API actually received.
    if (url.pathname === "/__api/calls") {
      const snapshot = [...calls];
      if (parsed.reset) calls.length = 0;
      return json(res, 200, { calls: snapshot, count: snapshot.length });
    }

    // Fault injection. POST {"signup":429|500|503|401|"network"} or false.
    if (url.pathname === "/__api/fault") {
      if ("signup" in parsed) FAULTS.signup = parsed.signup;
      return json(res, 200, { ok: true, faults: { ...FAULTS } });
    }

    return json(res, 404, { error: "not_found", path: url.pathname });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`mock signup API listening on ${BASE}\n`);
});
