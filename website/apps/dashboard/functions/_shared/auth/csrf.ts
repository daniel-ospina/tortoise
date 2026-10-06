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
 *
 * SESSION-GATED MUTATIONS ARE NOT EXEMPT (#4104 review). A route that requires
 * the `__Host-session` cookie is NOT protected by `SameSite=Lax` alone: the Lax
 * exemption is same-SITE, and every `*.premiselabs.co` sibling is same-site, so
 * `tortoise.premiselabs.co` (or XSS there) can drive `/api/session`,
 * `/api/provision` or `/api/v1/*` with the victim's cookie attached. Those
 * routes therefore carry the guard too. The generic proxy uses the ORIGIN layer
 * only (`guardOrigin`) because it must forward the caller's Content-Type for
 * arbitrary API calls; every other state-changing route uses the full guard.
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

/**
 * Methods that can change server state. GET/HEAD/OPTIONS are safe and are
 * deliberately not routed through this guard (a prefetch must not be blocked,
 * and a safe method cannot be driven into a state change by a CSRF).
 */
const STATE_CHANGING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function isStateChangingMethod(method: string): boolean {
  return STATE_CHANGING_METHODS.has(method.toUpperCase());
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
 * Origin layer ONLY, for routes whose request body is not required to be JSON.
 *
 * The generic `/api/v1` proxy forwards the caller's Content-Type verbatim — the
 * upstream API may legitimately accept a non-JSON body — so it cannot use the
 * media-type layer without breaking real calls. For those routes the `Origin`
 * test is the whole CSRF protection, and it is sufficient: a browser attaches
 * `Origin` to every state-changing request (same-origin POSTs included) and
 * cannot be made to omit it, so a cross-site form post is refused here. An
 * ABSENT `Origin` is allowed for the same reason as above — it cannot be a
 * browser cross-site post, and genuinely server-side/test callers omit it.
 */
export function guardOrigin(request: Request, env: Env): Response | null {
  const origin = request.headers.get("Origin");
  if (origin !== null && !isSameOrigin(origin, appOrigin(env))) {
    return json(
      { error: "forbidden_origin", message: "Cross-origin request rejected." },
      { status: 403 },
    );
  }
  return null;
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
  return guardOrigin(request, env);
}
