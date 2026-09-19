/**
 * POST /auth/signup — email + password account creation, server-side.
 *
 * WHY THIS EXISTS
 * ---------------
 * `signup.html` (the front door) used to call `supabaseClient.auth.signUp()`
 * directly, which requires a JS-readable client session. Under the BFF the
 * browser holds only the opaque `__Host-session` handle and NO token, so that
 * call can never work again. The account is created server-side; the browser
 * never sees a token.
 *
 * WHY IT PROXIES THE HOSTED API RATHER THAN CALLING GOTRUE (#801)
 * --------------------------------------------------------------
 * An anon-key GoTrue `/auth/v1/signup` makes GoTrue send a confirmation email
 * through Supabase's built-in SMTP. That path is project-wide-bucketed (30
 * sends/hr shared by ALL users of the project): once the bucket is exhausted
 * EVERY signup from ANY network 429s (`over_email_send_rate_limit`) and no
 * account is created — the P1 production signup blocker (#801).
 *
 * The canonical P1-safe path is `POST ${API_ORIGIN}/v1/signup/email`
 * (`tortoise/hosted_api.py`, `#801`): the API creates the auth user server-side
 * via the GoTrue ADMIN API with `email_confirm=true` (default), so the account
 * is created confirmed and NO confirmation email is sent — the SMTP bucket is
 * never touched. The API is public (`SKIP_AUTH`) and rate-limited by the shared
 * `/v1/register` bucket (3/hour per IP). `TORTOISE_SIGNUP_EMAIL_CONFIRM=false`
 * opts back into the confirmation-email funnel; in that case the response
 * carries `email_confirm: false` and this route reports
 * `{confirmationRequired: true}` with no session.
 *
 * This route therefore makes ZERO anon-key GoTrue `/auth/v1/signup` calls. It
 * follows the sibling proxy routes (`/api/provision`, `/auth/api-key`) for how
 * `API_ORIGIN` is read and how upstream faults are classified.
 *
 * ONE SESSION-ISSUING PATH
 * ------------------------
 * When the API reports `user_created` with `email_confirm: true`, the account
 * exists but the API issued no session (it only creates the user). This route
 * signs the user in server-side with the password grant
 * (`signInWithPassword`), then turns the token response into a D1 session row
 * (`createSession`) plus the `__Host-session` cookie (`buildCookie`) — exactly
 * the primitives `/auth/password` uses. The page therefore lands signed in in
 * one round trip, and the routes cannot drift on TTL, cookie flags, or failure
 * semantics.
 *
 * THE `accountCreated` CONTRACT (the #3485 class, one layer deeper)
 * ----------------------------------------------------------------
 * Once the API answers `user_created`, the account EXISTS. If the follow-up
 * sign-in or the session write then fails, the signup MUST NOT be reported as a
 * refusal — the user would retry and see "email already exists", or be told
 * their request was bad when it was not. Both failures are answered **503** with
 * a body that names the account as created so the page can say so:
 *
 *   503 { error: "session_unavailable" | "session_store_unavailable",
 *         accountCreated: true, confirmationRequired: false,
 *         user: { id?, email }, message }
 *
 * `error` distinguishes a provider-side session failure
 * (`session_unavailable`) from a store write failure
 * (`session_store_unavailable`); both are retryable and both assert
 * `accountCreated: true`.
 *
 * STATUS DISCIPLINE (§8.2 — the #3485 class)
 *   200  accepted — read `confirmationRequired` to know which state
 *   400  malformed request, or a provider REFUSAL (already registered, weak
 *        password, …). Bad input, user-visible.
 *   405  not a POST
 *   429  the upstream shared `/v1/register` bucket refused (hour-scale).
 *        `Retry-After` is forwarded so the page can render the tier-aware wait.
 *   503  provider / session store / configuration unavailable, or the account
 *        was created but the session could not be established. NEVER 401/400.
 *
 * Signup has no 401: there is no credential to reject. A provider or store
 * fault is 503 and must never be reported as a request-level refusal.
 */
import {
  type Env,
  SESSION_COOKIE,
  SESSION_MAX_AGE_S,
  buildCookie,
  createSession,
  ensureSchema,
  json,
} from "../_shared/auth/session";
import { ensureSchemaTokenColumns } from "../_shared/auth/token";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { fetchUserProfile, signInWithPassword } from "../_shared/auth/supabase";

/** The hosted API origin is configuration, never a literal (topology as config). */
interface SignupEnv extends Env {
  API_ORIGIN?: string;
}

/**
 * A deliberately shallow address check. The authoritative validation is the
 * API's; this only rejects what is obviously not an address, so a malformed
 * value never becomes an upstream call. Length is capped at the RFC 5321
 * practical maximum (mirrors `/auth/set-email` and `/auth/password`).
 */
function emailProblem(raw: unknown): string | null {
  if (typeof raw !== "string") return "Please enter an email address.";
  if (!raw.trim()) return "Please enter an email address.";
  if (raw.length > 254) return "That email address is too long.";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw.trim())) return "That doesn't look like an email address.";
  return null;
}

/** The hosted API's `POST /v1/signup/email` success body (`EmailSignupResponse`). */
interface EmailSignupResponse {
  user_id?: string | null;
  email?: string | null;
  email_confirm?: boolean | null;
  /** "user_created" | "already_registered" */
  message?: string | null;
}

/** FastAPI's error envelope: `{ detail: string | { message, error_code } }`. */
interface ApiErrorBody {
  detail?: unknown;
}

/** Pull the upstream machine-readable code out of a `detail` object, if any. */
function upstreamCode(detail: unknown): string | undefined {
  if (typeof detail !== "object" || detail === null) return undefined;
  const d = detail as { error_code?: unknown; code?: unknown };
  const raw = d.error_code ?? d.code;
  return typeof raw === "string" && raw ? raw : undefined;
}

/** Pull the upstream human message out of `detail` (string or object). */
function upstreamMessage(detail: unknown): string | undefined {
  if (typeof detail === "string" && detail) return detail;
  if (typeof detail !== "object" || detail === null) return undefined;
  const d = detail as { message?: unknown; msg?: unknown };
  const raw = d.message ?? d.msg;
  return typeof raw === "string" && raw ? raw : undefined;
}

/** Parse an upstream JSON body without ever throwing on a non-JSON response. */
function parseBody(raw: string): EmailSignupResponse & ApiErrorBody {
  try {
    return raw ? (JSON.parse(raw) as EmailSignupResponse & ApiErrorBody) : {};
  } catch {
    return {};
  }
}

/** The upstream refusal (`already registered`) always maps to this exact shape. */
function alreadyRegistered(): Response {
  return json({ error: "rejected", providerError: "user_already_exists" }, { status: 400 });
}

/**
 * The account EXISTS but no session could be established. A 503 (retryable),
 * never a refused signup — see the `accountCreated` contract in the header.
 */
function accountCreatedNoSession(userId: string | undefined, email: string | undefined): Response {
  return json(
    {
      error: "session_unavailable",
      accountCreated: true,
      confirmationRequired: false,
      user: { id: userId, email },
      message:
        "Your account was created, but we couldn't sign you in automatically. " +
        "Please sign in with your email and password.",
    },
    { status: 503 },
  );
}

export const onRequestPost: PagesFunction<SignupEnv> = async ({ request, env }) => {
  // --- CSRF / login-CSRF gate FIRST: this route ISSUES a session ----------------
  // It needs no cookie, so `SameSite=Lax` protects nothing: a cross-site HTML
  // form can forge a valid JSON body and have the attacker's session issued into
  // the victim's browser. The shared guard (Content-Type + Origin) kills that
  // vector before the body is parsed.
  const csrf = guardStateChangingRequest(request, env);
  if (csrf) return csrf;

  // --- input validation FIRST: a malformed request must not reach the API ----
  let body: { email?: unknown; password?: unknown; "cf-turnstile-response"?: unknown };
  try {
    body = (await request.json()) as {
      email?: unknown;
      password?: unknown;
      "cf-turnstile-response"?: unknown;
    };
  } catch {
    return json({ error: "invalid_request" }, { status: 400 });
  }

  const email = typeof body.email === "string" ? body.email.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  // The Turnstile token MUST be forwarded (#4104 review). `signup.html` adds
  // `cf-turnstile-response` to this body when a site key is provisioned, and the
  // hosted API's `_check_turnstile` 400s when a `TURNSTILE_SECRET_KEY` is set but
  // the token is absent — so dropping it here made provisioning Turnstile break
  // EVERY signup. Forwarded under the same key the API reads, and only when
  // present, so a deployment without Turnstile is unaffected.
  const turnstile =
    typeof body["cf-turnstile-response"] === "string" ? body["cf-turnstile-response"] : "";
  const problem = emailProblem(body.email);
  if (problem) return json({ error: "invalid_email", message: problem }, { status: 400 });
  if (!password) {
    return json(
      { error: "invalid_request", message: "Email and password are required." },
      { status: 400 },
    );
  }
  // A forged oversized value must not become an upstream request. The address
  // cap is enforced in `emailProblem`; the password is never trimmed, but an
  // absurd one is refused rather than sent.
  if (password.length > 4096) {
    return json({ error: "invalid_request", message: "Password is too long." }, { status: 400 });
  }

  // --- store guard + self-establishing schema (mirrors /auth/password) -------
  // Checked BEFORE the upstream call: this route may need to mint a session, and
  // an unavailable store is a misconfiguration, never a rejected request.
  if (!env.SESSIONS) {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }
  try {
    await ensureSchema(env.SESSIONS);
    await ensureSchemaTokenColumns(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // A missing base URL is a SERVER misconfiguration, not a rejected request.
  if (!env.API_ORIGIN) {
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  // --- upstream account creation (NO anon-key GoTrue /signup — #801) ---------
  let upstream: Response;
  try {
    upstream = await fetch(`${env.API_ORIGIN}/v1/signup/email`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        email,
        password,
        ...(turnstile ? { "cf-turnstile-response": turnstile } : {}),
      }),
      redirect: "manual",
    });
  } catch {
    // Network/transport fault: retryable, and says nothing about the request.
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  const payload = parseBody(await upstream.text().catch(() => ""));
  const detailMessage = upstreamMessage(payload.detail);
  const detailCode = upstreamCode(payload.detail);
  // `already_registered` is a 409 on today's API; the response model also names
  // the same state in `message`, so both are honoured.
  const alreadyRegisteredUpstream =
    upstream.status === 409 ||
    payload.message === "already_registered" ||
    detailMessage === "already_registered";
  if (alreadyRegisteredUpstream) return alreadyRegistered();

  if (upstream.status === 429) {
    // The upstream shared bucket is hour-scale with a `Retry-After`. Forward
    // BOTH the status and the header: the page renders the exact wait, and a
    // 503 here would misreport a throttle as an outage. The machine code rides
    // `providerError` so the page's tier-aware lockout copy still works.
    const retryAfter = upstream.headers.get("Retry-After") ?? "3600";
    const res = json(
      {
        error: "rate_limited",
        ...(detailCode ? { providerError: detailCode } : {}),
        message: detailMessage ?? "Too many signup attempts. Please try again later.",
      },
      { status: 429 },
    );
    res.headers.set("Retry-After", retryAfter);
    return res;
  }

  if (upstream.status === 503) {
    // Supabase unconfigured on the API deployment (e.g. selfhost): a provider
    // fault, never a refused signup.
    return json(
      { error: "provider_unavailable", ...(detailMessage ? { message: detailMessage } : {}) },
      { status: 503 },
    );
  }

  if (upstream.status === 422 || upstream.status === 400) {
    // GoTrue's refusal family, relayed by the API (weak password, validation,
    // unrecognised signup error). A user-visible 400, never a 503.
    return json(
      {
        error: "rejected",
        ...(detailCode ? { providerError: detailCode } : {}),
        ...(detailMessage ? { message: detailMessage } : {}),
      },
      { status: 400 },
    );
  }

  if (upstream.status !== 200) {
    // 5xx, a 401/403/404 edge/config fault, or an unreadable body: retryable
    // infrastructure, NEVER a refused request (#3485). Status alone would
    // misclassify a wrong key as a bad signup.
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  // --- 200: the account exists; which product state? ------------------------
  // The opt-in email funnel: the account needs confirmation, no session exists.
  if (payload.email_confirm === false) {
    return json({ ok: true, confirmationRequired: true });
  }

  // `user_created` (default, email_confirm=true): sign the user in server-side.
  // The API created the account but issued no session, so this route mints it
  // from the password grant — exactly as /auth/password does.
  const grant = await signInWithPassword(env, email, password);
  if (!grant.ok) {
    // The account EXISTS. Reporting the follow-up fault as a refusal would send
    // the user to retry and hit "already exists" (#3485). Say what happened.
    return accountCreatedNoSession(payload.user_id ?? undefined, payload.email ?? email);
  }

  const session = grant.data;
  const userId = session.user?.id ?? payload.user_id ?? undefined;
  if (!userId || !session.refresh_token) {
    // A 200 grant with no usable session is upstream corruption — the account
    // was still created, so this must not read as a refused signup.
    return accountCreatedNoSession(userId, payload.email ?? email);
  }

  // --- mint the D1 session (identical to /auth/password) ---------------------
  const now = Date.now();
  const handle = await createSession(
    env.SESSIONS,
    userId,
    session.refresh_token,
    SESSION_MAX_AGE_S,
  ).catch(() => null);

  if (!handle) {
    // A store WRITE failure is terminal and retryable — and the account exists.
    return json(
      {
        error: "session_store_unavailable",
        accountCreated: true,
        confirmationRequired: false,
        user: { id: userId, email: payload.email ?? email },
        message:
          "Your account was created, but we couldn't start your session. " +
          "Please sign in with your email and password.",
      },
      { status: 503 },
    );
  }

  // The dashboard chrome needs email + display name and the D1 row stores
  // neither; the BFF holds the token, so it asks.
  const profile = await fetchUserProfile(env, session.access_token);

  return json(
    {
      ok: true,
      confirmationRequired: false,
      user: {
        id: userId,
        email: profile?.email ?? session.user?.email ?? payload.email ?? email,
        displayName: profile?.displayName,
      },
      // `now` is captured before createSession's own Date.now(), so the reported
      // expiry is never later than the row's.
      expiresAt: now + SESSION_MAX_AGE_S * 1000,
    },
    {
      status: 200,
      cookies: [buildCookie(SESSION_COOKIE, handle, SESSION_MAX_AGE_S)],
    },
  );
};
