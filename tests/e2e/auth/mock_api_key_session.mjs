#!/usr/bin/env node
/**
 * Focused mock for the /auth/api-key BFF route (#3501/#4054).
 *
 * The route performs TWO server-side calls, both aimed at this mock (the tests
 * point `API_ORIGIN` and `SUPABASE_URL` here):
 *
 *   POST /v1/session/login   the hosted-API key→session exchange. The mock
 *                            keys its response on the VALUE of `api_key`, so a
 *                            200 proves the key was forwarded intact, and it can
 *                            reproduce every upstream outcome the route must map
 *                            distinctly: 200 session, 401 invalid, 403 policy
 *                            refusals (ANON_TEAM_NO_OWNER / KEY_NOT_USER_MINTED
 *                            / ACCOUNT_MISSING), 429 (hour-scale bucket), 500
 *                            outage, and a 200 with NO session (corruption).
 *   GET  /auth/v1/user       the one-shot profile read behind the 200 body's
 *                            email/displayName.
 *
 * Every exchange is recorded so "was the upstream contacted?" (and with what)
 * is OBSERVED rather than inferred. The raw key is never stored — only its
 * classified outcome.
 */
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_PORT ?? 9061);
const BASE = `http://127.0.0.1:${PORT}`;

const USER = { id: "user-apikey-1", email: "key@example.test", displayName: "Key User" };

const json = (res, status, body, headers = {}) => {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(JSON.stringify(body));
};

const sessionFor = (user) => ({
  access_token: "mock-access-apikey",
  refresh_token: "mock-refresh-apikey",
  expires_in: 3600,
  expires_at: Math.floor(Date.now() / 1000) + 3600,
  token_type: "bearer",
  user: { id: user.id, email: user.email, app_metadata: {}, user_metadata: {} },
});

/** Every exchange, in order. Never contains the raw key. */
const calls = [];

const server = createServer((req, res) => {
  const url = new URL(req.url, BASE);

  // GET /auth/v1/user — the profile the route reads with the token it will NOT
  // return to the browser.
  if (url.pathname === "/auth/v1/user") {
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

    if (url.pathname === "/v1/session/login") {
      const key = typeof parsed.api_key === "string" ? parsed.api_key : "";
      // Record the CLASSIFIED outcome, never the key itself.
      const outcome =
        key === "tt_valid_key"
          ? "valid"
          : key === "tt_anon_key"
            ? "anon_team"
            : key === "tt_minted_key"
              ? "minted"
              : key === "tt_account_missing"
                ? "account_missing"
                : key === "tt_ratelimited"
                  ? "rate_limited"
                  : key === "tt_outage"
                    ? "outage"
                    : key === "tt_empty_session"
                      ? "empty_session"
                      : "invalid";
      calls.push({
        path: url.pathname,
        hasKey: key.length > 0,
        outcome,
        // The key must arrive in the BODY (the upstream's key-auth contract),
        // not a header.
        authorization: req.headers.authorization ? "present" : null,
      });

      switch (outcome) {
        case "valid":
          return json(res, 200, sessionFor(USER));
        case "anon_team":
          return json(res, 403, {
            detail: {
              error_code: "ANON_TEAM_NO_OWNER",
              message: "This key belongs to an unclaimed team. Continue to claim it.",
            },
          });
        case "minted":
          return json(res, 403, {
            detail: { error_code: "KEY_NOT_USER_MINTED", message: "Minted keys cannot be used to sign in." },
          });
        case "account_missing":
          return json(res, 403, {
            detail: { error_code: "ACCOUNT_MISSING", message: "Account missing." },
          });
        case "rate_limited":
          return json(
            res,
            429,
            { detail: { error_code: "session_login_rate_limited", message: "Too many session logins." } },
            { "Retry-After": "3600" },
          );
        case "outage":
          return json(res, 500, { detail: "control plane unavailable" });
        case "empty_session":
          // A 200 with no refresh token — upstream corruption, not a sign-in.
          return json(res, 200, { user: { id: USER.id, email: USER.email } });
        default:
          return json(res, 401, { detail: "Invalid API key" });
      }
    }

    // Observability the tests assert on.
    if (url.pathname === "/__mock/calls") {
      const out = [...calls];
      if (parsed.reset) calls.length = 0;
      return json(res, 200, { calls: out, count: out.length });
    }

    return json(res, 404, { error: "not_found", path: url.pathname });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`mock api-key session listening on ${BASE}\n`);
});
