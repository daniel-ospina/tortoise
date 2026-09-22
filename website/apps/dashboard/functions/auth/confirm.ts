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
 * RECOVERY COMPLETES THROUGH AN INTERSTITIAL (#4104 review, cycle 2)
 * ------------------------------------------------------------------
 * The first version of this fix exempted `type=recovery` from the class-8
 * `__Host-authflow` binding and minted a full `__Host-session` from the
 * `token_hash` alone. That re-opened the exact session-fixation vector the
 * binding exists to stop: an attacker requests a password reset for THEIR OWN
 * address, receives a genuine, unexpired, single-use
 * `…/auth/confirm?token_hash=<attacker>&type=recovery`, and gets the victim to
 * click it. `verifyOtp` succeeds, `revokeAllForUser` revokes the attacker's OWN
 * sessions, and a session for the ATTACKER's account is minted INTO THE VICTIM'S
 * BROWSER — where the victim then operates inside the attacker's account.
 *
 * The `token_hash` cannot be the whole mitigation: it is unforgeable and
 * single-use, but the attacker can always obtain one FOR THEIR OWN ACCOUNT. What
 * the victim lacks is informed consent, so recovery uses the interstitial every
 * major provider uses for magic links:
 *
 *   GET  /auth/confirm?…&type=recovery
 *        verifies the single-use `token_hash`, then renders a minimal page
 *        NAMING the account the link belongs to and mints NO session.
 *   POST /auth/confirm
 *        CSRF-guarded; `__Host-authflow` binds the pending recovery to THIS
 *        browser. It consumes the pending record, revokes the user's other
 *        sessions, mints the session, and redirects to `/welcome?reset=1`.
 *
 * A victim who did not request a reset sees an unexpected account and does not
 * continue. The cookie binding is safe here even though the LINK is opened
 * cross-device: the GET and the POST both happen in the SAME browser, so the
 * `__Host-authflow` cookie set by the interstitial GET is present on its POST.
 *
 * Non-recovery flows (email confirmation, invite, magic link) KEEP the original
 * class-8 binding and complete in one GET — they are started in the browser that
 * must complete them.
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
import { verifyOtp } from "../_shared/auth/supabase";
import { cspNonce, strictCspWithNonce } from "../_shared/security-headers";

interface FlowRow {
  flow_id: string;
  kind: string;
  expires_at: number;
}

interface RecoveryRow {
  user_id: string;
  refresh_token: string;
  email: string | null;
  expires_at: number;
}

/** How long the verified-but-unconfirmed recovery stays redeemable. */
const RECOVERY_MAX_AGE_S = 10 * 60;

/** Rendered responses are Functions output, so `_headers` does not reach them. */
const HSTS = "max-age=31536000; includeSubDomains";

/**
 * A pending recovery: the outcome of a VERIFIED `token_hash`, held server-side
 * until the user confirms on the interstitial. Its own table (not `auth_flows`)
 * because the credential it carries — the refresh token — must never be exposed
 * to, or controllable by, a flow-start request.
 */
async function ensureRecoveryTable(db: D1Database): Promise<void> {
  await db.exec(
    "CREATE TABLE IF NOT EXISTS recovery_flows (" +
      "flow_id TEXT PRIMARY KEY, " +
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
export function recoveryInterstitial(email: string | null, pendingId: string): Response {
  const shown = email ? escapeHtml(email) : "your account";
  // #3525: this is the one self-rendered HTML page in the dashboard AND the one
  // with no inline event handlers, so it can be nonce-gated — the treatment the
  // MCP consent page already uses, and stricter (no external script is needed).
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
    <p>A password-recovery link was opened for <span class="email">${shown}</span>.
       If this is your account, continue to set a new password.</p>
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
        fetch('/auth/confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({})
        }).then(function (res) {
          if (res.ok) { window.location.assign('/welcome?reset=1'); return; }
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
    "Content-Security-Policy": strictCspWithNonce(nonce),
  });
  headers.append("Set-Cookie", buildCookie(FLOW_COOKIE, pendingId, RECOVERY_MAX_AGE_S));
  return new Response(html, { status: 200, headers });
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url);
  const tokenHash = url.searchParams.get("token_hash");
  const type = url.searchParams.get("type");
  const flowCookie = readCookie(request, FLOW_COOKIE);

  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  try {
    await ensureSchema(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!tokenHash || !type) return redirect("/auth?error=invalid_link");

  // ── RECOVERY: verify, then ask for consent. Never mint here. ──────────────
  if (type === "recovery") {
    const verified = await verifyOtp(env, tokenHash, type);
    if (!verified.ok) {
      const status = verified.retryable ? 503 : 401;
      return json({ error: "verification_failed", detail: verified.error }, { status });
    }

    try {
      await ensureRecoveryTable(env.SESSIONS);
    } catch {
      return json({ error: "session_store_unavailable" }, { status: 503 });
    }

    const pendingId = randomHandle();
    const now = Date.now();
    try {
      await env.SESSIONS.prepare(
        "INSERT INTO recovery_flows (flow_id,user_id,refresh_token,email,created_at,expires_at) " +
          "VALUES (?1,?2,?3,?4,?5,?6)",
      )
        .bind(
          pendingId,
          verified.data.user.id,
          verified.data.refresh_token,
          verified.data.user.email ?? null,
          now,
          now + RECOVERY_MAX_AGE_S * 1000,
        )
        .run();
    } catch {
      return json({ error: "session_store_unavailable" }, { status: 503 });
    }

    return recoveryInterstitial(verified.data.user.email ?? null, pendingId);
  }

  // --- flow binding (class-8) ------------------------------------------------
  // Non-recovery flows KEEP the `__Host-authflow` binding: they are started in
  // the browser that must complete them, so a token_hash with no matching live
  // flow on THIS browser is not honoured. A missing or stale cookie gets the
  // interstitial, never a silent session.
  let flow: FlowRow | null = null;
  if (flowCookie) {
    try {
      flow = await env.SESSIONS.prepare(
        "SELECT flow_id,kind,expires_at FROM auth_flows WHERE flow_id = ?1",
      )
        .bind(flowCookie)
        .first<FlowRow>();
    } catch {
      return json({ error: "session_store_unavailable" }, { status: 503 });
    }
  }

  if (!flowCookie) return redirect("/auth?interstitial=1");
  if (!flow || flow.expires_at <= Date.now()) {
    return redirect("/auth?interstitial=1", [clearCookie(FLOW_COOKIE)]);
  }

  // --- server-side completion ------------------------------------------------
  const result = await verifyOtp(env, tokenHash, type);
  if (!result.ok) {
    // A link can expire or already have been used. That is NOT a server fault,
    // and it is NOT the same as "your session ended" — say so explicitly.
    const status = result.retryable ? 503 : 401;
    return json({ error: "verification_failed", detail: result.error }, { status });
  }

  // Completing recovery must invalidate every other session (F15), so a stolen
  // session does not survive the reset. (The recovery TYPE now completes on the
  // interstitial POST; a flow row with kind `recovery` can still arrive from an
  // older link shape, so this remains.)
  //
  // This runs BEFORE the new session is minted, so a failure here must stop the
  // flow rather than be swallowed: `catch(() => 0)` asserted the F15 guarantee
  // while leaving the stolen session alive.
  if (flow.kind === "recovery") {
    try {
      await revokeAllForUser(env.SESSIONS, result.data.user.id);
    } catch {
      return json(
        {
          error: "revocation_failed",
          message: "Could not invalidate existing sessions. Please try the reset link again.",
        },
        { status: 503 },
      );
    }
  }

  const handle = await createSession(
    env.SESSIONS,
    result.data.user.id,
    result.data.refresh_token,
    SESSION_MAX_AGE_S,
  ).catch(() => null);
  if (!handle) return json({ error: "session_store_unavailable" }, { status: 503 });

  if (flowCookie) {
    await env.SESSIONS.prepare("DELETE FROM auth_flows WHERE flow_id = ?1").bind(flowCookie)
      .run()
      .catch(() => undefined);
  }

  return redirect("/welcome", [
    buildCookie(SESSION_COOKIE, handle, SESSION_MAX_AGE_S),
    clearCookie(FLOW_COOKIE),
  ]);
};

/**
 * POST /auth/confirm — the interstitial's explicit confirmation.
 *
 * This is the ONLY place a recovery session is minted. It requires:
 *   - a JSON Content-Type and a same-origin `Origin` (the shared CSRF guard), and
 *   - a `__Host-authflow` cookie that MATCHES a live pending-recovery row
 *     (the class-8 binding, restored for recovery).
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
  if (!pendingId) {
    return json(
      { error: "no_recovery_in_progress", message: "Open the recovery link from your email first." },
      { status: 400 },
    );
  }

  let row: RecoveryRow | null = null;
  try {
    await ensureRecoveryTable(env.SESSIONS);
    row = await env.SESSIONS.prepare(
      "SELECT user_id,refresh_token,email,expires_at FROM recovery_flows WHERE flow_id = ?1",
    )
      .bind(pendingId)
      .first<RecoveryRow>();
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!row || row.expires_at <= Date.now()) {
    // The link was never opened here, already confirmed, or expired. No session
    // is minted — the whole point of the interstitial.
    return json(
      { error: "recovery_expired", message: "This recovery link has expired. Request a new one." },
      { status: 400, cookies: [clearCookie(FLOW_COOKIE)] },
    );
  }

  // Single-use: consume the pending recovery before minting anything.
  await env.SESSIONS.prepare("DELETE FROM recovery_flows WHERE flow_id = ?1")
    .bind(pendingId)
    .run()
    .catch(() => undefined);

  // F15: completing recovery invalidates every other session for the user.
  try {
    await revokeAllForUser(env.SESSIONS, row.user_id);
  } catch {
    return json(
      {
        error: "revocation_failed",
        message: "Could not invalidate existing sessions. Please try the reset link again.",
      },
      { status: 503 },
    );
  }

  const handle = await createSession(
    env.SESSIONS,
    row.user_id,
    row.refresh_token,
    SESSION_MAX_AGE_S,
  ).catch(() => null);
  if (!handle) return json({ error: "session_store_unavailable" }, { status: 503 });

  return redirect("/welcome?reset=1", [
    buildCookie(SESSION_COOKIE, handle, SESSION_MAX_AGE_S),
    clearCookie(FLOW_COOKIE),
  ]);
};
