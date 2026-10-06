#!/usr/bin/env node
/**
 * Focused mock Supabase Auth for the /auth/password suite.
 *
 * Separate from the SHARED `mock_supabase.mjs` on purpose. That file's password
 * grant answers 200 unconditionally and records nothing, so it cannot show that
 * a wrong password yields 401, that GoTrue is NOT contacted for a malformed
 * request, or that a provider outage is reported as 503. A test that cannot
 * reach the branch it documents guards nothing — hence a focused mock rather
 * than an edit to the file the other BFF suites depend on.
 *
 * Emulates exactly the surface `/auth/password` uses:
 *   POST /auth/v1/token?grant_type=password
 *        Validates email+password against a fixture, so a 200 PROVES both were
 *        forwarded intact. Answers GoTrue's real refusal for a bad credential:
 *        `400 {"error_code":"invalid_credentials"}`, IDENTICAL for a wrong
 *        password and an unknown address — the property that keeps GoTrue (and
 *        therefore this BFF route) from being an account-existence oracle.
 *   GET  /auth/v1/user
 *        The profile endpoint the 200 shape's email/displayName come from.
 *
 * Every password grant is recorded so "GoTrue was NOT called" is observed
 * rather than inferred. The raw password is never stored — only whether one was
 * present and whether it was accepted (a 200 already proves the value).
 */
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_PORT ?? 9031);
const BASE = `http://127.0.0.1:${PORT}`;

/** The one account this mock knows. */
const USER = {
  email: "known@example.test",
  password: "correct-horse-battery-staple",
  id: "user-pw-1",
  displayName: "Known User",
};

/** Injectable faults — see /__mock/fault. */
const FAULTS = { passwordGrant: false, profile: false, malformed: false };

/** Every password grant, in order. Never contains the raw password. */
const passwordCalls = [];
let tokenCounter = 0;

const json = (res, status, body) => {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};

const issue = (sub, email) => {
  tokenCounter += 1;
  return {
    access_token: `mock-access-${tokenCounter}`,
    refresh_token: `mock-refresh-${tokenCounter}`,
    expires_in: 3600,
    user: { id: sub, email },
  };
};

const server = createServer((req, res) => {
  const url = new URL(req.url, BASE);

  // GET /auth/v1/user — the profile read behind the 200 body's displayName.
  if (url.pathname === "/auth/v1/user") {
    if (FAULTS.profile) return json(res, 500, { error: "injected_profile_outage" });
    const auth = req.headers.authorization ?? "";
    if (!/^Bearer\s+\S/.test(auth)) return json(res, 401, { error: "missing_token" });
    return json(res, 200, {
      id: USER.id,
      email: USER.email,
      user_metadata: { display_name: USER.displayName },
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

    if (url.pathname === "/auth/v1/token" && url.searchParams.get("grant_type") === "password") {
      const email = typeof parsed.email === "string" ? parsed.email : null;
      const password = typeof parsed.password === "string" ? parsed.password : "";
      const accepted = email === USER.email && password === USER.password;
      passwordCalls.push({
        grant: "password",
        email,
        hasPassword: password.length > 0,
        accepted,
        // A login has no session yet, so the route must key the call with the
        // ANON key. Recorded as a shape, never a value.
        authorization: req.headers.authorization ? "present" : null,
      });
      if (FAULTS.passwordGrant) return json(res, 500, { error: "injected_provider_outage" });
      if (!accepted) {
        return json(res, 400, {
          error_code: "invalid_credentials",
          msg: "Invalid login credentials",
        });
      }
      if (FAULTS.malformed) {
        // A 200 whose body honours the declared TokenResponse shape only
        // partially: no `user`, no `refresh_token`. `call()` accepts ANY 2xx as
        // `{ok:true,data}`, so an unvalidated route would throw a TypeError here
        // and surface a 500; it must answer 503 instead.
        return json(res, 200, { access_token: "mock-access-malformed", expires_in: 3600 });
      }
      return json(res, 200, issue(USER.id, USER.email));
    }

    // Observability the tests assert on: what GoTrue actually received.
    if (url.pathname === "/__mock/password-calls") {
      const calls = [...passwordCalls];
      if (parsed.reset) passwordCalls.length = 0;
      return json(res, 200, { calls, count: calls.length });
    }

    // Fault injection. POST {"passwordGrant":true} / {"profile":true} /
    // {"malformed":true}.
    if (url.pathname === "/__mock/fault") {
      for (const k of ["passwordGrant", "profile", "malformed"]) {
        if (typeof parsed[k] === "boolean") FAULTS[k] = parsed[k];
      }
      return json(res, 200, { ok: true, faults: { ...FAULTS } });
    }

    return json(res, 404, { error: "not_found", path: url.pathname });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`mock password supabase listening on ${BASE}\n`);
});
