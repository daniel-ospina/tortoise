/**
 * GET/POST /auth/confirm — complete an email flow server-side from a `token_hash`.
 *
 * SCOPE.md F15/F16, §5. This resolves three flows that previously had NO handler:
 *   - email confirmation (F16)
 *   - password recovery (F15)
 *   - remaining invite links (F14)
 *
 * `type` values are owned by SCOPE.md §5.2 and NOT restated here. The caller
 * passes the value Supabase put in the template.
 *
 * EVERY EMAIL FLOW COMPLETES THROUGH AN INTERSTITIAL (#3528)
 * ----------------------------------------------------------
 * `type=recovery` was the only type that could complete: its branch mints a
 * pending record and a binding cookie of its own. `email` (signup confirmation
 * AND magic link), `email_change` and `invite` fell to a class-8 branch that
 * required a `__Host-authflow` cookie matching a live `auth_flows` row — and
 * **no email flow establishes one**. `FLOW_COOKIE` is minted only by
 * `/auth/start` (OAuth/PKCE) and `/auth/link` (identity linking). `/auth/reset`
 * and `/auth/resend` set `redirect_to=/auth/confirm` without one, and
 * `/auth/signup` sends no confirmation email at all by default (the hosted API
 * creates the account with `email_confirm=true`, #801) — so the confirmation
 * flow is reachable through `/auth/resend`, or when that default is deliberately
 * flipped: see `docs/ops/auth-email-flows.md` §3.
 * A confirmation link opened from an inbox therefore arrived with no cookie and
 * was answered `302 /auth?interstitial=1` — which nothing consumes — dropping
 * the single-use `token_hash` on the floor. Email confirmation did not work at
 * all, same-browser or cross-device.
 *
 * The fix is to give every email type the recovery treatment instead of adding
 * a second mechanism. The interstitial is the ONLY one that can work: an emailed
 * link is routinely opened on a device that never started the flow, and
 * `SCOPE.md` §8.4's `/auth/start` binding — the alternative — cannot supply a
 * cookie to a browser that was not there to receive it. The binding here is
 * therefore *per pending record*, created by the GET and consumed by the POST in
 * the SAME browser, which is exactly the case §8.4 exists to protect and the
 * case §5.3 requires to be non-silent.
 *
 * This SUPERSEDES §8.4's "the confirm URL carries the flow id" for the email
 * types, as an explicit, reversible choice: §8.4 fixed only the same-browser
 * path and §8.4 itself records that the happy path breaks without a flow-start
 * route that signup/recovery/invite do not have. The security property §8.4 and
 * §9 class 8 protect — no silent login from a credential the browser did not
 * request — is preserved by informed consent instead: the page NAMES the account
 * the link belongs to and mints nothing until it is clicked.
 *
 * THE INTERSTITIAL (why the GET must not mint)
 * --------------------------------------------
 * **The consent step is the whole mitigation.** The `__Host-authflow` cookie
 * binding does NOT defend against the vector below — the GET hands a matching
 * cookie to ANY browser that opens a verified link, including the victim's, so a
 * future lane must not treat the binding as load-bearing and weaken the
 * interstitial. What the binding does is make the pending record single-use and
 * non-guessable, and stop a cross-browser/replayed POST. The protection against
 * the attacker-issued link is that the page NAMES the account and mints nothing
 * until it is deliberately confirmed.
 *
 * An attacker requests a password reset (or any email link) for THEIR OWN
 * address, receives a genuine, unexpired, single-use
 * `…/auth/confirm?token_hash=<attacker>&type=…`, and gets the victim to click it.
 * `verifyOtp` succeeds and a session for the ATTACKER's account is minted INTO
 * THE VICTIM'S BROWSER — where the victim then operates inside the attacker's
 * account. The `token_hash` cannot be the whole mitigation: it is unforgeable
 * and single-use, but the attacker can always obtain one FOR THEIR OWN ACCOUNT.
 * What the victim lacks is informed consent, so every type uses the interstitial
 * every major provider uses for magic links:
 *
 *   GET  /auth/confirm?…&type=<any>
 *        verifies the single-use `token_hash`, then renders a minimal page
 *        NAMING the account the link belongs to and mints NO session.
 *   POST /auth/confirm
 *        CSRF-guarded; `__Host-authflow` binds the pending record to THIS
 *        browser. It consumes the record, mints the session, and redirects to
 *        `/welcome` (recovery adds `?reset=1` and revokes the user's other
 *        sessions — F15).
 *
 * A victim who did not request the link sees an unexpected account and does not
 * continue. Both hops happen in the SAME browser, so the flow works
 * cross-device: the `__Host-authflow` cookie is created by the GET, not required
 * to pre-exist.
 *
 * Links already sent before the cutover are fragment-style and cannot be
 * redeemed here — that is an explicit unsupported-by-decision (§5.4), not an
 * oversight.
 */
import {
  type Env,
  FLOW_COOKIE,
  SESSION_COOKIE,
  SESSION_MAX_AGE_S,
  buildCookie,
  clearCookie,
  createSession,
  ensureSchema,
  json,
  randomHandle,
  readCookie,
  redirect,
  revokeAllForUser,
} from "../_shared/auth/session";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { requireUserSession, verifyOtp } from "../_shared/auth/supabase";
import { cspNonce, strictCspWithNonce } from "../_shared/security-headers";

/** A verified email-flow token awaiting the user's explicit confirmation. */
interface PendingRow {
  kind: string;
  user_id: string;
  refresh_token: string;
  email: string | null;
  expires_at: number;
}

/**
 * How long a verified-but-unconfirmed email link stays redeemable.
 *
 * Bounds REPLAY of the pending record, not fixation — only the consent step and
 * the single-use delete address fixation (see the header).
 */
const RECOVERY_MAX_AGE_S = 10 * 60;

/** Rendered responses are Functions output, so `_headers` does not reach them. */
const HSTS = "max-age=31536000; includeSubDomains";

/**
 * A pending email-flow confirmation: the outcome of a VERIFIED `token_hash`,
 * held server-side until the user confirms on the interstitial. Its own table
 * (not `auth_flows`) because the credential it carries — the refresh token —
 * must never be exposed to, or controllable by, a flow-start request.
 *
 * `kind` is the GoTrue `type` the link arrived with, and it is what makes
 * recovery-only behaviour (F15 bulk revoke, the reset panel) apply to recovery
 * and NOT to an ordinary confirmation or magic link.
 */
async function ensurePendingTable(db: D1Database): Promise<void> {
  await db.exec(
    "CREATE TABLE IF NOT EXISTS email_flow_pending (" +
      "flow_id TEXT PRIMARY KEY, " +
      "kind TEXT NOT NULL, " +
      "user_id TEXT NOT NULL, " +
      "refresh_token TEXT NOT NULL, " +
      "email TEXT, " +
      "created_at INTEGER NOT NULL, " +
      "expires_at INTEGER NOT NULL);",
  );
}

const HTML_ENTITIES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => HTML_ENTITIES[c]);
}

/**
 * The consent page. It shows WHICH account the link belongs to and requires an
 * explicit click; the POST it performs is the only thing that mints a session.
 *
 * Server-rendered and self-contained (matching the inline-HTML convention of the
 * sibling auth routes) so no new asset pipeline is introduced and the page
 * cannot be stale relative to the handler.
 */
// Exported for the regression guard (#3525): `src/securityHeaders.test.js` calls
// this and asserts the REAL response carries `no-store`, the flow cookie, and a
// nonce that the inline `<script>`/`<style>` actually match — a source-level
// regex on this file would also pass on a commented-out header.
export function emailInterstitial(email: string | null, pendingId: string, kind: string): Response {
  const shown = email ? escapeHtml(email) : "your account";
  // Recovery is the one type with a post-condition the user must act on, and the
  // one type that revokes the user's other sessions (F15). Everything else just
  // signs in.
  const isRecovery = kind === "recovery";
  const lede = isRecovery
    ? "A password-recovery link was opened for"
    : "A sign-in link was opened for";
  const action = isRecovery ? "continue to set a new password" : "continue to sign in";
  const dest = isRecovery ? "/welcome?reset=1" : "/welcome";
  // #3525: this is the one self-rendered HTML page in the dashboard AND the one
  // with no inline event handlers, so it can be nonce-gated — the treatment the
  // MCP consent page already uses. The policy is NOT nonce-ONLY: it carries the
  // platform-injected Cloudflare beacon origin beside the nonce (every policy
  // must — see `_shared/security-headers.ts`), so any script served from that
  // origin would run here un-nonced. What this page authors is a single inline
  // script, and that one still needs the nonce.
  const nonce = cspNonce();
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Confirm your sign-in — Tortoise</title>
<style nonce="${nonce}">
  :root { --bg:#060b14; --surface:#0d1a2d; --text:#cbd5e1; --dim:#94a3b8; --accent:#06b6d4; --red:#ef4444; --border:#1e293b;
          --mono:'SF Mono','Cascadia Code','Fira Code','JetBrains Mono',monospace; --serif:Georgia,'Times New Roman',serif; }
  *,*::before,*::after { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:var(--mono); font-size:14px; line-height:1.6;
         display:flex; align-items:center; justify-content:center; min-height:100vh; padding:1.5rem; }
  .card { width:100%; max-width:420px; background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:2rem; }
  .brand { font-family:var(--serif); font-size:1.3rem; text-align:center; margin-bottom:1.5rem; }
  .brand span { color:var(--accent); }
  h1 { font-family:var(--serif); font-weight:400; font-size:1.25rem; text-align:center; margin-bottom:1rem; }
  p { color:var(--dim); margin-bottom:1rem; }
  strong, .email { color:var(--text); word-break:break-all; }
  button { width:100%; padding:11px 16px; border:none; border-radius:8px; background:var(--accent); color:#04121a;
           font-family:var(--mono); font-size:14px; font-weight:700; cursor:pointer; }
  button:hover { background:#0891b2; }
  button:disabled { opacity:.6; cursor:default; }
  .error { color:var(--red); min-height:1.2em; margin-top:1rem; }
  .small { font-size:.8rem; margin-top:1rem; }
</style>
</head>
<body>
  <main class="card">
    <div class="brand">tortoise<span>·</span></div>
    <h1>Confirm it's you</h1>
    <p>${lede} <span class="email">${shown}</span>.
       If this is your account, ${action}.</p>
    <button id="continue" type="button">Sign in as <strong>${shown}</strong> — Continue</button>
    <p class="error" id="error" role="alert" aria-live="polite"></p>
    <p class="small">If this is not your account, close this page — no one is signed in yet.</p>
  </main>
  <script nonce="${nonce}">
    (function () {
      var btn = document.getElementById('continue');
      var err = document.getElementById('error');
      btn.addEventListener('click', function () {
        btn.disabled = true;
        err.textContent = '';
        // A JSON POST is required by the BFF's CSRF guard (a form cannot send
        // application/json). Follow the 302 so the Set-Cookie is applied, then
        // land on the reset panel.
        // The id of the record THIS page is asking the user to authorise. It is
        // sent back on the POST and must equal the cookie, so a second link
        // opened in another tab (the cookie is per-browser, not per-tab) cannot
        // redirect the consent the user gave HERE to a DIFFERENT account. A page
        // naming account A must never mint B.
        fetch('/auth/confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ pending: '${pendingId}' })
        }).then(function (res) {
          if (res.ok) { window.location.assign('${dest}'); return; }
          throw new Error('HTTP ' + res.status);
        }).catch(function () {
          btn.disabled = false;
          err.textContent = 'Could not complete sign-in. Re-open the link from your email and try again.';
        });
      });
    })();
  </script>
</body>
</html>`;
  const headers = new Headers({
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "Strict-Transport-Security": HSTS,
    // A consent page is only worth anything if the user reads WHICH account it
    // names, so it must not be framed. (`_headers` does not reach Functions
    // output, hence the explicit stamp. Not currently exploitable without this —
    // a framed cross-site POST carries no `SameSite=Lax` cookie and would 400.)
    // #4634: `X-Frame-Options` is the legacy enforcement arm of the same
    // requirement; `frame-ancestors 'none'` is NOT restated as a second CSP
    // header because `strictCspWithNonce` already carries it, and #3525's guard
    // pins the served policy to exactly that one value.
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": strictCspWithNonce(nonce),
  });
  headers.append("Set-Cookie", buildCookie(FLOW_COOKIE, pendingId, RECOVERY_MAX_AGE_S));
  return new Response(html, { status: 200, headers });
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url);
  const tokenHash = url.searchParams.get("token_hash");
  const type = url.searchParams.get("type");

  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  try {
    await ensureSchema(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!tokenHash || !type) return redirect("/auth?error=invalid_link");

  // ── EVERY type: verify, then ask for consent. Never mint here. ────────────
  const verified = await verifyOtp(env, tokenHash, type);
  if (!verified.ok) {
    // A link can expire or already have been used. That is NOT a server fault,
    // and it is NOT the same as "your session ended" — say so explicitly.
    const status = verified.retryable ? 503 : 401;
    return json({ error: "verification_failed", detail: verified.error }, { status });
  }

  // A 2xx is not proof of a usable body — `call()` classifies by status alone.
  // Without this the next lines throw a TypeError and the route answers an
  // unhandled 500 instead of the 503 its contract declares (#4160).
  const session = requireUserSession(verified.data);
  if (!session) return json({ error: "provider_unavailable" }, { status: 503 });

  try {
    await ensurePendingTable(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  const pendingId = randomHandle();
  const now = Date.now();
  try {
    await env.SESSIONS.prepare(
      "INSERT INTO email_flow_pending (flow_id,kind,user_id,refresh_token,email,created_at,expires_at) " +
        "VALUES (?1,?2,?3,?4,?5,?6,?7)",
    )
      .bind(
        pendingId,
        type,
        session.userId,
        session.refreshToken,
        session.email,
        now,
        now + RECOVERY_MAX_AGE_S * 1000,
      )
      .run();
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  return emailInterstitial(session.email, pendingId, type);
};

/**
 * POST /auth/confirm — the interstitial's explicit confirmation.
 *
 * This is the ONLY place an email-flow session is minted. It requires:
 *   - a JSON Content-Type and a same-origin `Origin` (the shared CSRF guard), and
 *   - a `__Host-authflow` cookie that MATCHES a live pending row
 *     (the class-8 binding, per pending record).
 * The pending row is single-use: it is deleted before the session is created.
 */
export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  // --- CSRF gate FIRST. A recovery confirmation is state-changing and must not
  // be forgeable by a sibling subdomain (SameSite=Lax is same-SITE, not
  // same-origin). No session is needed to reach here, so the cookie cannot be
  // the protection either.
  const csrf = guardStateChangingRequest(request, env);
  if (csrf) return csrf;

  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  const pendingId = readCookie(request, FLOW_COOKIE);

  // The id the PAGE displayed. The cookie alone is not enough: it is
  // per-browser, not per-tab, and every verified GET overwrites it — so without
  // this a second link opened in another tab would silently redirect the consent
  // the user gave on THIS page to that other account (a page naming account A
  // minting B). A missing or mismatched id is refused, never coerced.
  let requested: string | null = null;
  try {
    const body = (await request.json()) as { pending?: unknown };
    if (typeof body?.pending === "string") requested = body.pending;
  } catch {
    // Not JSON (the CSRF guard already rejected a non-JSON media type) or empty.
  }
  if (!pendingId || !requested || requested !== pendingId) {
    return json(
      {
        error: "no_email_flow_in_progress",
        message: "Open the link from your email again.",
      },
      { status: 400, cookies: [clearCookie(FLOW_COOKIE)] },
    );
  }

  let row: PendingRow | null = null;
  try {
    await ensurePendingTable(env.SESSIONS);
    row = await env.SESSIONS.prepare(
      "SELECT kind,user_id,refresh_token,email,expires_at FROM email_flow_pending WHERE flow_id = ?1",
    )
      .bind(pendingId)
      .first<PendingRow>();
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!row || row.expires_at <= Date.now()) {
    // The link was never opened here, already confirmed, or expired. No session
    // is minted — the whole point of the interstitial.
    return json(
      { error: "email_flow_expired", message: "This link has expired. Request a new one." },
      { status: 400, cookies: [clearCookie(FLOW_COOKIE)] },
    );
  }

  // Single-use: consume the pending record before minting anything, and FAIL
  // CLOSED if that consumption did not take. Checking the STATUS is not enough —
  // a DELETE that matches 0 rows is not an error, and a concurrent POST that
  // already consumed the record must not mint a second session.
  let consumed: { meta?: { changes?: number } };
  try {
    consumed = await env.SESSIONS.prepare("DELETE FROM email_flow_pending WHERE flow_id = ?1")
      .bind(pendingId)
      .run();
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }
  if ((consumed.meta?.changes ?? 0) !== 1) {
    return json(
      { error: "email_flow_expired", message: "This link has already been used." },
      { status: 400, cookies: [clearCookie(FLOW_COOKIE)] },
    );
  }

  // F15: completing RECOVERY invalidates every other session for the user, so a
  // stolen session does not survive the reset. A confirmation or magic link does
  // not touch other sessions — signing in on a new device must not sign the user
  // out everywhere else.
  //
  // This runs BEFORE the new session is minted, so a failure here must stop the
  // flow rather than be swallowed: `catch(() => 0)` asserted the F15 guarantee
  // while leaving the stolen session alive.
  if (row.kind === "recovery") {
    try {
      await revokeAllForUser(env.SESSIONS, row.user_id);
    } catch {
      return json(
        {
          error: "revocation_failed",
          message: "Could not invalidate existing sessions. Request a new reset link.",
        },
        { status: 503 },
      );
    }
  }

  const handle = await createSession(
    env.SESSIONS,
    row.user_id,
    row.refresh_token,
    SESSION_MAX_AGE_S,
  ).catch(() => null);
  if (!handle) return json({ error: "session_store_unavailable" }, { status: 503 });

  return redirect(row.kind === "recovery" ? "/welcome?reset=1" : "/welcome", [
    buildCookie(SESSION_COOKIE, handle, SESSION_MAX_AGE_S),
    clearCookie(FLOW_COOKIE),
  ]);
};
