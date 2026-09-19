/**
 * CSRF / login-CSRF guard for every state-changing BFF route.
 *
 * WHY THIS EXISTS (the login-CSRF / session-fixation class)
 * --------------------------------------------------------
 * The session-ISSUING routes (`/auth/password`, `/auth/signup`,
 * `/auth/api-key`) need no cookie to work — they are how you GET a session.
 * `SameSite=Lax` therefore protects nothing there: the attack does not need the
 * victim's cookie to be SENT, it needs a session ISSUED INTO the victim's
 * browser. An attacker page can auto-submit
 *
 *   <form action="https://app.premiselabs.co/auth/password" method="POST"
 *         enctype="text/plain">
 *     <input name='{"email":"attacker@example.com","password":"hunter2","x":"' value='"}'>
 *   </form>
 *
 * so the body is `{"email":"attacker@…","password":"hunter2","x":"="}` — VALID
 * JSON, because `enctype=text/plain` joins fields with `=` and newlines. The
 * route answers 200 with `Set-Cookie: __Host-session=<the ATTACKER's handle>`,
 * silently replacing the victim's session. The victim then operates inside the
 * attacker's account (session fixation), and everything they type into it is
 * the attacker's to read.
 *
 * TWO LAYERS, BOTH REQUIRED
 * -------------------------
 * 1. **Content-Type must be `application/json`.** An HTML form cannot send
 *    `application/json` (the attribute is limited to three legacy encodings),
 *    so this alone kills the vector — and it forces any `fetch` attempt into a
 *    CORS preflight, which these routes deliberately do not answer. A missing
 *    or wrong media type is refused with 415 BEFORE the body is parsed.
 * 2. **`Origin`, when present, must be the app origin.** Browsers attach
 *    `Origin` to every state-changing request, including same-origin POSTs and
 *    cross-origin form posts. An ABSENT `Origin` is allowed because it cannot
 *    be a browser cross-site form post (the browser would have sent one) and
 *    genuinely server-side/test callers do not send it; rejecting it would
 *    break legitimate non-browser callers while adding no browser protection.
 *
 * ONE HELPER, NOT N COPIES. A per-route check would drift — one route would
 * quietly lose its `Origin` test in a refactor and nothing would notice. The
 * rule lives here so it is testable in one place and cannot be half-applied.
 */
import { type Env, json } from "./session";

/** The dashboard host. Configured, never a literal — topology is config (§6). */
export function appOrigin(env: Env): string {
  return env.APP_ORIGIN ?? "https://app.premiselabs.co";
}

/**
 * Is this a JSON media type? Parameters (`; charset=utf-8`) are ignored, and
 * the media type is case-insensitive per RFC 9110, so a legitimate client is
 * never refused for spelling.
 */
function isJsonMediaType(raw: string | null): boolean {
  if (!raw) return false;
  return raw.split(";")[0].trim().toLowerCase() === "application/json";
}

/** Compare ORIGINS, not raw strings — normalises case, ports and any path. */
function isSameOrigin(candidate: string, expected: string): boolean {
  try {
    return new URL(candidate).origin === new URL(expected).origin;
  } catch {
    return false;
  }
}

/**
 * Refuse a request that did not come from our own JSON client, or `null` when
 * the request is allowed.
 *
 * Call this at the TOP of a state-changing handler (POST/PATCH/PUT/DELETE),
 * BEFORE reading the body. GET/HEAD are never state-changing here and are
 * deliberately not routed through this guard.
 */
export function guardStateChangingRequest(request: Request, env: Env): Response | null {
  // ---- layer 1: Content-Type -------------------------------------------------
  if (!isJsonMediaType(request.headers.get("Content-Type"))) {
    return json(
      {
        error: "unsupported_media_type",
        message: "State-changing requests must be sent as application/json.",
      },
      { status: 415 },
    );
  }

  // ---- layer 2: Origin, when the browser sent one ----------------------------
  const origin = request.headers.get("Origin");
  if (origin !== null && !isSameOrigin(origin, appOrigin(env))) {
    return json(
      { error: "forbidden_origin", message: "Cross-origin request rejected." },
      { status: 403 },
    );
  }

  return null;
}
