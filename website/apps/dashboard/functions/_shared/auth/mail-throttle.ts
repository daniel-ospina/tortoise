/**
 * The BFF's own, ADDRESS-INDEPENDENT throttle for the mail-sending auth routes
 * (`/auth/reset`, `/auth/resend`) — issue #4161.
 *
 * ── THE DEFECT THIS CLOSES ──────────────────────────────────────────────────
 * Both routes fold a provider `429` into an enumeration-safe `200 {ok:true}`
 * (the #4104 trade-off). That kept the endpoints from being an account-existence
 * oracle, but it left the CLIENT with no way to say "nothing was sent": both
 * routes could only ever answer 200 for a well-formed address, so the page's
 * `resp.status === 429` branches were dead and a throttled user was told
 * "Reset link sent — check your inbox" when the provider had sent nothing.
 *
 * The fix is the one the issue names: the throttle decision has to be made HERE,
 * on a key that carries no account signal, so the `429` the page branches on is
 * one the BFF can honestly emit. `signup.ts` and `api-key.ts` never had this
 * problem — their 429s come from per-IP/per-project upstream buckets, which are
 * address-independent already — so they are deliberately untouched: adding a
 * second limiter to working routes would be machinery with no defect behind it.
 *
 * ── WHY THE KEY IS THE CLIENT AND NEVER THE ADDRESS ─────────────────────────
 * The oracle is not "a 429 exists"; it is "the ANSWER differs by address".
 * A limiter keyed on the client (or anything else request-scoped) answers the
 * same for a registered address and an unregistered one, so it leaks nothing —
 * which is what makes it safe to answer honestly on this surface.
 *
 * An address-keyed limiter would NOT be an oracle — it is uniform in the same
 * way (every caller gets `LIMIT` requests per address) — but it is the wrong key
 * for two concrete reasons, and the second is the serious one:
 *   1. it is bypassed by rotating addresses, so it does not bound a loop;
 *   2. it lets ANY caller charge a VICTIM's budget. Five requests to a
 *      colleague's address would lock that person out of password recovery for
 *      the rest of the window — a targeted denial of the one flow that exists to
 *      get them back in.
 * It would also re-import the per-address mental model this change exists to
 * retire (see the corrected mechanism below). `test_mail_throttle_bff.py`'s
 * `test_the_budget_is_charged_to_the_caller_not_the_address` is the pin: a second
 * caller asking for the SAME address must still be allowed.
 *
 * ⚠️ `CF-Connecting-IP` IS THE EDGE'S VALUE, NOT THE CLIENT'S. Cloudflare sets it
 * on every request to a proxied hostname and it cannot be set by the caller in
 * production (the repo already relies on this in `functions/contact/submit.ts`).
 * `X-Forwarded-For` is deliberately NOT consulted: it is a caller-supplied list,
 * so keying on it would make the limiter a no-op for anyone who rotates it.
 * A request with no `CF-Connecting-IP` at all (a local/CLI caller) shares ONE
 * bucket — conservative, and it cannot be used to escape the limit.
 *
 * ── SCOPE: A UX THROTTLE, NOT THE SECURITY BOUNDARY ─────────────────────────
 * Two honest limitations, stated so they are not mistaken for guarantees:
 *   • the window is FIXED, not sliding — a caller can spend `LIMIT` just before
 *     the boundary and `LIMIT` again just after. That is accepted: the point is
 *     to stop a retry loop from spending the shared email budget, not to meter
 *     an adversary.
 *   • the store is the SESSIONS D1 binding. When it is absent or unreachable the
 *     check FAILS OPEN (no throttle), because turning a throttle-store gap into
 *     a mail outage is the #3485 class — and failing open changes no security
 *     property: the route still folds the provider refusal exactly as before.
 *     The fault is logged, never silently swallowed.
 *
 * ── WHY THE CODE IS `over_email_send_rate_limit` ────────────────────────────
 * The client already maps that code to the hourly "email limit reached" tier
 * (`signup.html` → `isEmailBucketError`/`EMAIL_BUCKET_RATE_LIMIT_COPY`, pinned by
 * `tests/test_signup_form_safety.py`). What the client needs from a 429 is the
 * TIER — how long to wait — and this limiter enforces the same hourly email
 * budget the provider does, one layer closer to the caller. Reusing the contract
 * code keeps the tier mapping single-sourced; inventing a second code would mean
 * two places to teach, and the one the page does not know would silently degrade
 * to "try again in a minute" for an hourly limit.
 *
 * ⚠️ AND IT IS **NOT** A PER-ADDRESS LIMIT. The #4104 comments in `reset.ts` /
 * `resend.ts` said GoTrue's `over_email_send_rate_limit` "applies to the ADDRESS
 * being mailed". That is wrong, and it matters: `sendEmail` gates on
 * `a.limiterOpts.Email.Allow()` — `ratelimit.New(config.RateLimitEmailSent)`,
 * a limiter with **no key argument at all** (supabase/auth
 * `internal/api/mail.go`, `internal/api/apilimiter/apilimiter.go`,
 * `internal/ratelimit/ratelimit.go`; read 2026-10-13). It is ONE
 * project-wide bucket, which is exactly what `signup.html`'s #863 copy and
 * `_shared/auth/email-flows.ts` always said.
 *
 * The oracle is still real, for a different reason: `/recover` and `/resend`
 * answer `200 {}` EARLY for an address with no account (`recover.go` →
 * `FindUserByEmailAndAudience` → not found → `200`), while an address that DOES
 * exist reaches the email send and can be refused. So the differential is
 * "unknown address never reaches the limiter; known address can be refused by
 * it" — not an address-keyed bucket. Same conclusion, same fold, corrected
 * mechanism.
 */
import { type Env, json } from "./session";

/**
 * Requests one client may make per window before the BFF refuses.
 *
 * A legitimate user needs one, maybe two (the mail did not arrive). This is
 * deliberately far below GoTrue's project-wide 30/hr custom-SMTP budget, so the
 * BFF is the binding constraint for a caller retrying in a loop — which is the
 * request pattern that produced the dead `429` branch — instead of that caller
 * silently spending a budget shared by every other user.
 */
export const MAIL_THROTTLE_LIMIT = 5;

/** One hour, matching the hourly email budget both this limiter and GoTrue meter. */
export const MAIL_THROTTLE_WINDOW_S = 3600;

/** The client contract code for the hourly email tier (see the header). */
export const MAIL_THROTTLE_CODE = "over_email_send_rate_limit";

const WINDOW_MS = MAIL_THROTTLE_WINDOW_S * 1000;

/**
 * Rows one DELETE may reclaim, and how many such statements one request may
 * spend draining.
 *
 * Reclamation runs on the REQUEST path (see its call sites), so it is bounded in
 * both dimensions: a burst of freshly-created buckets must not each issue a huge
 * delete, and a backlog must not be drained inside one user-facing request. A
 * drained window costs one statement (the next batch comes back short); a
 * backlog drains a bounded slice per request and continues on the next one.
 */
const PRUNE_LIMIT = 200;
const PRUNE_MAX_BATCHES = 25;

/**
 * Has DDL already run in this isolate?
 *
 * `CREATE TABLE IF NOT EXISTS` is idempotent, but it is still a statement on
 * every request; the sibling bootstraps (`ensureSchema`, `ensureFlowTable`) run
 * unguarded because their routes are low-traffic reads. This one runs on a
 * mail-sending path, so it is memoised after the first success. A failure does
 * NOT memoise — the next request retries.
 */
let tableReady = false;

async function ensureThrottleTable(db: D1Database): Promise<void> {
  if (tableReady) return;
  await db.exec(
    "CREATE TABLE IF NOT EXISTS auth_mail_throttle (" +
      "bucket TEXT PRIMARY KEY, " +
      "hits INTEGER NOT NULL, " +
      "window_start INTEGER NOT NULL);",
  );
  // The prune's predicate. Created with the table for the same reason every
  // other index here is: `CREATE TABLE IF NOT EXISTS` cannot add one to a table
  // that already exists, so a database established by the runtime alone would
  // never receive it.
  await db.exec(
    "CREATE INDEX IF NOT EXISTS idx_auth_mail_throttle_window " +
      "ON auth_mail_throttle(window_start);",
  );
  tableReady = true;
}

/**
 * The bucket identity: a PSEUDONYM of the client, salted with the window.
 *
 * The raw client address is never written to the store — only a hash, and a
 * different one every window, so the rows are not linkable across hours. This is
 * pseudonymisation, not anonymisation (an IPv4 space is enumerable), and it is
 * enough: the store's only reader is this counter.
 */
async function clientKey(request: Request, windowStart: number): Promise<string> {
  const ip = request.headers.get("CF-Connecting-IP")?.trim() || "unknown";
  const bytes = new TextEncoder().encode(`auth-mail-throttle:${windowStart}:${ip}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 32);
}

async function pruneExpired(db: D1Database, before: number): Promise<void> {
  // Bounded by construction: each statement reclaims at most `PRUNE_LIMIT` rows
  // (the inner LIMIT) and the loop stops as soon as a batch comes back short
  // (drained) or after `PRUNE_MAX_BATCHES`, so this can never try to drain a
  // long-unpruned backlog inside one user-facing request.
  for (let batch = 0; batch < PRUNE_MAX_BATCHES; batch++) {
    const result = await db
      .prepare(
        "DELETE FROM auth_mail_throttle WHERE bucket IN (" +
          "SELECT bucket FROM auth_mail_throttle WHERE window_start < ?1 LIMIT " +
          `${PRUNE_LIMIT})`,
      )
      .bind(before)
      .run();
    // `meta.changes` is D1's affected-row count — the same field `session.ts`
    // relies on for its CAS. A short batch means there is nothing left.
    if ((result.meta?.changes ?? 0) < PRUNE_LIMIT) return;
  }
}

/**
 * Charge this request against the caller's window and, when the budget is spent,
 * return the `429` the routes must send instead of calling the provider.
 *
 * Returns `null` when the request may proceed (under the limit, or the store is
 * unavailable). Never throws: a throttle fault must not break mail delivery.
 *
 * @param scope which route is charging — the two routes hold separate budgets so
 *   a reset loop cannot exhaust resends (and vice versa).
 */
export async function mailThrottle(
  request: Request,
  env: Env,
  scope: "reset" | "resend",
): Promise<Response | null> {
  const db = env.SESSIONS;
  if (!db) {
    // Fail open, but LOUDLY. The module header promises the fault is logged:
    // without this line a deployment missing the binding loses throttling with
    // no signal anywhere — the class the `ensureColumn` lesson in session.ts
    // exists to prevent.
    console.warn(
      `auth-mail-throttle: no SESSIONS binding, request not throttled (scope=${scope})`,
    );
    return null; // fail open — see the header
  }

  try {
    await ensureThrottleTable(db);

    const now = Date.now();
    const windowStart = Math.floor(now / WINDOW_MS) * WINDOW_MS;
    const bucket = `${scope}:${await clientKey(request, windowStart)}:${windowStart}`;

    // One round trip: the upsert increments and returns the new count. `hits`
    // is post-increment, so the LIMIT-th request is the last one allowed.
    const row = await db
      .prepare(
        "INSERT INTO auth_mail_throttle (bucket, hits, window_start) VALUES (?1, 1, ?2) " +
          "ON CONFLICT(bucket) DO UPDATE SET hits = auth_mail_throttle.hits + 1 " +
          "RETURNING hits",
      )
      .bind(bucket, windowStart)
      .first<{ hits: number }>();

    if (!row) {
      // `.first()` yielded no row for a statement that always RETURNS one.
      // Treated as "under the limit" (fail open), but LOGGED: silently, this is
      // indistinguishable from "no caller is over budget", so a permanent
      // no-op would report nothing anywhere (see the header).
      console.warn(
        `auth-mail-throttle: upsert returned no row, request not throttled (scope=${scope})`,
      );
      return null;
    }

    // Reclaim expired rows on the path NORMAL traffic reaches, not only on a
    // trip. Every (route, caller, window) creates exactly one row at `hits === 1`,
    // so reclamation gated on a trip would let a deployment where nobody ever
    // trips grow by one dead row per caller per window for ever — retention has
    // to be a function of TIME, not of the abuse pattern. The trip also prunes,
    // so an abuse burst reclaims too.
    if (row.hits === 1 || row.hits > MAIL_THROTTLE_LIMIT) {
      await pruneExpired(db, windowStart - WINDOW_MS);
    }

    if (row.hits <= MAIL_THROTTLE_LIMIT) return null;

    const retryAfterS = Math.max(1, Math.ceil((windowStart + WINDOW_MS - now) / 1000));
    const res = json(
      {
        error: "rate_limited",
        providerError: MAIL_THROTTLE_CODE,
        message:
          "Too many email requests from this connection. " +
          "Please try again in about an hour.",
      },
      { status: 429 },
    );
    res.headers.set("Retry-After", String(retryAfterS));
    return res;
  } catch (e) {
    // Fail open, but LOUDLY: a swallowed store fault is how a real breakage
    // stays invisible (the `ensureColumn` lesson in session.ts). No address (or
    // pseudonym) is logged — there is nothing here an operator needs to see one
    // of.
    console.warn(
      `auth-mail-throttle: store unavailable, request not throttled (scope=${scope}):`,
      e instanceof Error ? e.message : String(e),
    );
    return null;
  }
}
