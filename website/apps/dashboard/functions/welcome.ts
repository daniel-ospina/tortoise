/**
 * GET /welcome — the post-auth landing, decided entirely on the server.
 *
 * WHY THE SERVER DECIDES
 * ----------------------
 * `welcome.html` used to decide this in the browser, via a hard gate that called
 * `readValidSession()`. Under the BFF there is no JS-readable session, so that
 * function returned null on EVERY load and the gate redirected to /auth on every
 * successful login — the #3485 loop, reproduced by construction for every user.
 *
 * The session cookie is HttpOnly. The server is the only thing that can
 * legitimately answer "is this visitor signed in?" — so the answer is a status
 * code here, and the page no longer participates in the decision at all.
 *
 * This endpoint therefore NO LONGER serves the page to an anonymous visitor.
 * It redirects. See the note on `redirect_uri` below for why that is safe.
 *
 * DECISIONS
 *   signed in                   -> 302 APP_ORIGIN[/?claim=1]   (straight to the app)
 *   no cookie / dead session    -> 302 /auth?next=…&stale=1    (genuinely signed out)
 *   ?reset=1 + signed in        -> serve the reset panel asset (the ONE rendered case)
 *   store unreachable           -> 503, terminal, NEVER a redirect
 *
 * The 503 case matters most: redirecting to /auth because the database blipped is
 * exactly how a transient fault becomes a login loop.
 */
import {
  type Env,
  SESSION_COOKIE,
  clearCookie,
  getSession,
  json,
  readCookie,
  redirect,
} from "./_shared/auth/session";
import { appOrigin } from "./_shared/auth/csrf";

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url);
  const handle = readCookie(request, SESSION_COOKIE);

  // ── Genuinely signed out ────────────────────────────────────────────────
  if (!handle) {
    // 302, not a rendered page: /welcome is a post-AUTH landing, so an
    // anonymous visitor belongs at the sign-in screen. This is deterministic
    // server-side routing, so it cannot loop the way the client gate did — the
    // client gate bounced SIGNED-IN users because it could not see the session.
    return redirect(
      `/auth?next=${encodeURIComponent(url.pathname + url.search)}&stale=1`,
    );
  }

  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  let row;
  try {
    row = await getSession(env.SESSIONS, handle);
  } catch {
    // Terminal 503. The browser must NOT be told it is signed out.
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!row || row.expires_at <= Date.now()) {
    return redirect(
      `/auth?next=${encodeURIComponent(url.pathname + url.search)}&stale=1`,
      [clearCookie(SESSION_COOKIE)],
    );
  }

  // ── Signed in ───────────────────────────────────────────────────────────

  // Recovery landing: the reset panel is the only case that renders. It is a
  // form; it posts to /auth/update-password, which is where the write happens.
  if (url.searchParams.get("reset") !== null) {
    if (!env.ASSETS) return json({ error: "assets unavailable" }, { status: 503 });
    // Fetch the ASSET BY NAME, never by re-requesting this URL.
    //
    // `ASSETS.fetch` runs the static-asset router, and that layer DOES apply
    // `_redirects` (unlike inbound routing, where a matching Function wins).
    // The app project USED to carry `/welcome / 200` — a rewrite for the
    // in-app first-run wizard (#1287, #1566) — and re-requesting
    // `/welcome?reset=1` then answered with the SPA document, so the recovery
    // landing rendered the dashboard shell with no panel in it.
    //
    // The protection is that the rule is GONE and this Function owns
    // `/welcome` — NOT that the file is named here. Measured: with the rewrite
    // present, `ASSETS.fetch("/welcome.html")` still resolved to the rewrite
    // target, because Pages 308-normalizes a `.html` URL back to `/welcome`
    // and the rewrite then applies. Naming the file is defence in depth (it
    // states the intent, "serve welcome.html", instead of depending on the
    // router resolving the path) and it cannot be relied on alone.
    return env.ASSETS.fetch(new URL("/welcome.html", url.origin).toString());
  }

  // Otherwise: straight to the app. A claim in flight (the `tt_claim_pending`
  // marker cookie the dashboard's claim card sets) routes to the claim card.
  // This used to be client-side; the server can read the same cookie, so the
  // routing no longer depends on JavaScript running before the redirect.
  const claimInFlight = /(?:^|;\s*)tt_claim_pending=/.test(request.headers.get("Cookie") ?? "");
  return redirect(claimInFlight ? `${appOrigin(env)}/?claim=1` : appOrigin(env));
};
