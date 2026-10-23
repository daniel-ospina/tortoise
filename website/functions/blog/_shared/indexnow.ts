// IndexNow submission on the publish transition (#9680).
//
// WHAT THIS REACHES, AND WHAT IT DOES NOT. IndexNow is a notification protocol;
// its participating engines are Bing, Yandex, Seznam, Naver, Amazon and Yep, and
// a submission to the global endpoint is forwarded to all of them
// (indexnow.org/documentation, verified 2026-10-22).
//
// ⛔ GOOGLE DOES NOT CONSUME INDEXNOW. A submission here does NOT put a page in
// Google's index, does NOT shorten Google's crawl latency, and must never be
// described as "indexing on Google" — in a comment, a log line, a doc, a test
// name or a PR sentence. Google's equivalent is GSC request-indexing, an
// authenticated API that is not built here and is out of this issue's scope.
// The claim is the reason the tracker recorded this capability as shipped while
// no code existed (#7751).
//
// Verified against indexnow.org/documentation + /faq (2026-10-22):
//   • POST https://api.indexnow.org/indexnow, `Content-Type: application/json;
//     charset=utf-8`, UTF-8 body, `{host, key, keyLocation?, urlList}`; up to
//     10 000 URLs per request.
//   • key: 8–128 characters of [A-Za-z0-9-]. Ownership is proved by a UTF-8 text
//     file `{key}.txt` at the host ROOT containing the key, or by a file the
//     payload names as `keyLocation`. We serve the root file (the protocol's
//     recommended Option 1) AND send `keyLocation` pointing at it — the payload
//     field is explicitly permitted alongside Option 1, and it makes the file the
//     verifier will read unambiguous.
//   • Responses: 200 submitted · 202 received, key validation pending · 400 bad
//     request · 403 key not valid · 422 URL/key does not belong to the host ·
//     429 too many requests. 200 and 202 are both ACCEPTED, which is why success
//     is `res.ok` rather than `status === 200`: the FIRST submission of a new key
//     typically answers 202, so a 200-only test would log a false failure on the
//     very submission that worked and train the operator to ignore the log.
//   • ⚠️ NO SERVER-SIDE IDEMPOTENCY, and this is the one property that shapes the
//     code below. The protocol has no dedupe token and no "already submitted"
//     answer; the FAQ only asks that you "avoid submitting the same URL many
//     times a day". De-duplication is therefore OURS, and it needs DURABLE state:
//     a per-isolate Map would be correct only until the next isolate — green in a
//     test, a flood in production, which is the shape this repo keeps re-learning.
//     The state is `blog_posts.indexnow_submitted_at` (migration
//     20261022000001_blog_indexnow.sql). Every read or write of it is fail-soft
//     and LOGGED: the stamp is a rate limit, never a correctness dependency of
//     the publish.
//
// ZERO-DEPENDENCY (plain TS + Web APIs, like its siblings in this directory).

import type { Env } from "../_lib.ts";

/**
 * The IndexNow key — 32 lowercase hex characters (the protocol allows 8–128 of
 * [A-Za-z0-9-]; the issue pins 32 hex).
 *
 * ⛔ PUBLIC BY DESIGN, and checked in ON PURPOSE — this is not a secret. The
 * endpoint proves host ownership by FETCHING `${site}/${key}.txt` from our own
 * origin, so anyone may read it. Being in the repo is what keeps the key and the
 * served file (`website/<key>.txt`) ONE commit that cannot desync; an env-var
 * override would let the submitted key differ from the deployed file, and the
 * endpoint answers 403 for that — a silent, self-inflicted outage with no red
 * anywhere. Rotation = change this constant AND rename the file, together, in one
 * commit. `tests/test_blog_indexnow.py` fails if the two disagree.
 */
export const INDEXNOW_KEY = "a7c41f0e9b2d63584e1c0af7d3b925e6";

/** The global endpoint. A submission here is shared with every participant. */
export const INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow";

/** Bound on the outbound call. The publish has already been answered by then. */
export const INDEXNOW_TIMEOUT_MS = 5_000;

/**
 * Bound on the two Supabase round-trips (state read, stamp write).
 *
 * They need one for the same reason the POST does, but with a sharper
 * consequence: the state READ is awaited BEFORE the submission, so a stalled
 * response means nothing is notified at all — and because the task is then
 * abandoned at the runtime's grace boundary rather than the fetch rejecting, the
 * `catch` never runs and NO `[indexnow]` line is written. An operator following
 * the runbook ("filter the logs for [indexnow]") would find nothing, which is
 * the silent failure the module header calls the one that matters. A bounded
 * fetch converts the stall into the paths that already log. Matches the 3 s
 * bound `cloudflare-purge.ts` uses for the same kind of write-path side call.
 */
export const INDEXNOW_STATE_TIMEOUT_MS = 3_000;

/**
 * Minimum gap between two submissions of the same URL — 24 hours, not a calendar
 * day: a calendar day would still allow two submissions a minute apart across
 * midnight, and the requirement is "a submission, not a flood".
 *
 * ⚠️ WHAT IT DOES NOT COVER, stated so the claim in the docs is exactly true. The
 * stamp lives on the POST ROW, and it is read before the submission and written
 * after it, so the window is neither atomic nor keyed by URL:
 *   • two publish transitions of the same post that OVERLAP (both read the old
 *     stamp while the first submission is in flight) can each submit once;
 *   • a slug that is unpublished, deleted and re-created is a NEW row, so its
 *     first publish submits even if the previous row submitted minutes earlier.
 * Both need a human or an agent to act twice on one post inside seconds, so the
 * outcome is bounded at two submissions for a single publish event — but it is
 * not zero, and the conditional-PATCH reservation that WOULD make it atomic puts
 * the window rule into a PostgREST predicate, where the test harness can only
 * observe it by re-implementing the predicate in its stub. That trade is not
 * worth it for this case; the limit is documented instead of hidden.
 */
export const INDEXNOW_WINDOW_MS = 86_400_000;

// ── The decision (pure) ────────────────────────────────────────────────────
/**
 * Whether this publish may submit. PURE, so the rule is testable without a
 * runtime and has exactly one home.
 *
 * `lastSubmittedAt` carries three DIFFERENT states and they must not collapse:
 *   • `null`      — the row has never been submitted → submit;
 *   • ISO string  — submitted before → submit only once the window has passed;
 *   • `undefined` — the state could not be READ (missing column before the
 *                   migration is applied, an upstream fault) → do NOT submit.
 * The third state is the important one: submitting on an unknown state is how a
 * missing migration turns into a submission on every publish, and a publish is
 * exactly the transition we cannot afford to spam. A skipped submission is
 * recoverable (the operator's backfill, or the next publish); a flood is not
 * undoable.
 *
 * An UNPARSEABLE stored value is treated as "never submitted" rather than
 * skipping forever: garbage in the column should cost one submission, not
 * disable the capability permanently.
 */
export function shouldSubmit(state: {
  lastSubmittedAt: string | null | undefined;
  nowMs: number;
}): boolean {
  if (state.lastSubmittedAt === undefined) return false;
  if (!state.lastSubmittedAt) return true;
  const last = Date.parse(state.lastSubmittedAt);
  if (!Number.isFinite(last)) return true;
  return state.nowMs - last >= INDEXNOW_WINDOW_MS;
}

// ── The call (fail-soft, logged) ───────────────────────────────────────────
export interface SubmitResult {
  /** True only for an ACCEPTED submission (200 or 202). */
  ok: boolean;
  /** The endpoint's status, or `null` when no response arrived (timeout/network). */
  status: number | null;
}

/**
 * POST one URL to IndexNow. NEVER THROWS: a notification must not be able to
 * fail the write that triggered it, and there is no retry in this path (a retry
 * loop on a failing vendor is a self-inflicted flood).
 *
 * The outcomes it logs, each with the URL:
 *   • accepted     → `[indexnow] submitted (status 200|202): <url>`
 *   • rejected     → `[indexnow] submit failed (status <n>): <url>`
 *   • no answer    → `[indexnow] submit failed (no status — timeout/network): <url>`
 *   • unusable url → `[indexnow] submit failed (unusable url): <url>` (SITE_URL
 *                    and the slug did not form a parseable URL — a config fault,
 *                    not a vendor one)
 * A silent failure is the failure mode that matters — the post is live either
 * way, so the log is the ONLY signal that the notification did not land.
 */
export async function submitUrl(
  url: string,
  opts: { key?: string; timeoutMs?: number } = {},
): Promise<SubmitResult> {
  const key = opts.key ?? INDEXNOW_KEY;
  let payload: string;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") throw new Error(`non-https url: ${url}`);
    payload = JSON.stringify({
      // `host` is the host WITHOUT a path (protocol requirement) and `urlList`
      // is exactly ONE url — the canonical one. A batch is deliberately absent:
      // #9680 item 5 keeps any backfill out of the publish handler.
      host: parsed.host,
      key,
      keyLocation: `${parsed.origin}/${key}.txt`,
      urlList: [parsed.toString()],
    });
  } catch {
    console.warn(`[indexnow] submit failed (unusable url): ${url}`);
    return { ok: false, status: null };
  }

  try {
    const res = await fetch(INDEXNOW_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: payload,
      signal: AbortSignal.timeout(opts.timeoutMs ?? INDEXNOW_TIMEOUT_MS),
    });
    if (!res.ok) {
      console.warn(`[indexnow] submit failed (status ${res.status}): ${url}`);
      return { ok: false, status: res.status };
    }
    // 202 included — see the header note on accepted statuses.
    console.log(`[indexnow] submitted (status ${res.status}): ${url}`);
    return { ok: true, status: res.status };
  } catch {
    // Timeout / DNS / TLS / a thrown fetch stub. Logged, never rethrown.
    console.warn(`[indexnow] submit failed (no status — timeout/network): ${url}`);
    return { ok: false, status: null };
  }
}

// ── Durable state (fail-soft both ways) ────────────────────────────────────
function serviceHeaders(env: Env): Record<string, string> {
  return {
    apikey: env.SUPABASE_SERVICE_ROLE_KEY ?? "",
    Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY ?? ""}`,
    "Content-Type": "application/json",
    Accept: "application/json",
    Prefer: "return=representation",
  };
}

/**
 * Read `indexnow_submitted_at` for one row.
 *
 * Returns `undefined` (NOT `null`) when the value cannot be read — that is the
 * "unknown, so do not submit" state `shouldSubmit` refuses. The two causes are
 * deliberately the same answer: a missing column (this migration not yet applied)
 * and an upstream fault both mean "we cannot prove this URL was not submitted
 * today".
 */
async function readSubmittedAt(env: Env, id: string, url: string): Promise<string | null | undefined> {
  try {
    const res = await fetch(
      `${env.SUPABASE_URL}/rest/v1/blog_posts?select=indexnow_submitted_at&id=eq.${encodeURIComponent(id)}&limit=1`,
      { headers: serviceHeaders(env), signal: AbortSignal.timeout(INDEXNOW_STATE_TIMEOUT_MS) },
    );
    if (!res.ok) {
      // 400 here is the "column not in the schema" shape — the deploy ran
      // ahead of the migration. Visible, not silent: a publish that submits
      // nothing is a log line, not a dead capability nobody notices.
      console.warn(`[indexnow] skipped (state unreadable, supabase ${res.status}): ${url}`);
      return undefined;
    }
    const rows = (await res.json()) as Array<{ indexnow_submitted_at: string | null }>;
    const row = rows[0];
    if (!row) {
      // Zero rows is NOT "never submitted" — it is a state we could not read
      // (a concurrent delete, an odd PostgREST answer, a permissions fault).
      // Returning `undefined` there means "skip", and the skip is logged here
      // because `notifyPublished` deliberately suppresses its own skip line for
      // this state. Leaving it silent would put this path outside the module's
      // "every read and write is logged" contract and invisible to the
      // runbook's `grep [indexnow]`.
      console.warn(`[indexnow] skipped (state unreadable, no row): ${url}`);
      return undefined;
    }
    return row.indexnow_submitted_at ?? null;
  } catch {
    console.warn(`[indexnow] skipped (state unreadable, upstream unreachable): ${url}`);
    return undefined;
  }
}

/**
 * Record the attempt. A failed stamp costs at most one extra submission later.
 *
 * ⚠️ This is an UPDATE of the row, so the table's unconditional `BEFORE UPDATE`
 * trigger (`blog_posts_set_updated_at`) rewrites `updated_at` as a side effect.
 * `updated_at` is USER-FACING — the article's JSON-LD `dateModified`, the
 * sitemap's `<lastmod>` — and today that is harmless because the stamp always
 * co-occurs with a publish that just bumped it. It stops being harmless for any
 * future caller that stamps OUTSIDE a publish (a backfill, a "resubmit on a
 * material edit"), which would silently move a live article's published
 * modification date. Recorded in the migration's column comment too; fixing it
 * belongs in the trigger (skip when only internal columns changed), not here.
 */
async function stampSubmittedAt(env: Env, id: string, nowMs: number, url: string): Promise<void> {
  try {
    const res = await fetch(`${env.SUPABASE_URL}/rest/v1/blog_posts?id=eq.${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: serviceHeaders(env),
      body: JSON.stringify({ indexnow_submitted_at: new Date(nowMs).toISOString() }),
      signal: AbortSignal.timeout(INDEXNOW_STATE_TIMEOUT_MS),
    });
    if (!res.ok) console.warn(`[indexnow] stamp failed (supabase ${res.status}): ${url}`);
  } catch {
    console.warn(`[indexnow] stamp failed (upstream unreachable): ${url}`);
  }
}

/**
 * Submit one published article's canonical URL to IndexNow, at most once per
 * `INDEXNOW_WINDOW_MS` PER ROW — non-atomically, and not keyed by URL; the
 * constant documents the two escapes (overlapping transitions of one row, and a
 * deleted-and-recreated slug). Called from the agent API's publish transition
 * inside `waitUntil`, so the publish response never waits on the vendor.
 *
 * ⛔ This is the ONLY submission path in the codebase, and it takes ONE url. A
 * bulk/backfill loop does not belong in a publish handler (#9680 item 5): the
 * already-published URLs are submitted once, by hand, per the runbook in
 * `docs/blog-authoring.md`.
 *
 * NEVER THROWS — the caller has already answered 200 and the post is live.
 * A failure here is a log line about a notification, never a failed publish.
 */
export async function notifyPublished(input: {
  env: Env;
  id: string;
  url: string;
  nowMs: number;
}): Promise<void> {
  const { env, id, url, nowMs } = input;
  try {
    const lastSubmittedAt = await readSubmittedAt(env, id, url);
    if (!shouldSubmit({ lastSubmittedAt, nowMs })) {
      // Not an error: an edit that never reached this path, a state we cannot
      // read, or a real submission inside the window. Logged so "why did
      // nothing go out?" has an answer in the Cloudflare log.
      if (lastSubmittedAt !== undefined) {
        console.log(`[indexnow] skipped (submitted ${lastSubmittedAt}): ${url}`);
      }
      return;
    }
    await submitUrl(url);
    // Stamped on the ATTEMPT, whatever its outcome: a vendor outage must not
    // turn every subsequent re-publish into another attempt, and there is no
    // retry in this path by design.
    await stampSubmittedAt(env, id, nowMs, url);
  } catch {
    // Belt and braces. Everything above is already fail-soft; this guarantees a
    // future edit cannot turn a notification into an unhandled rejection in a
    // background task.
    console.warn(`[indexnow] unexpected failure: ${url}`);
  }
}
