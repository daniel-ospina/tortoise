/**
 * The consent bridge's BFF-side mechanics — the attempt record and the mint (#3524 §9 step 3).
 *
 * WHY THIS MODULE IS SEPARATE FROM `consent-flow.ts`
 * --------------------------------------------------
 * `consent-flow.ts` holds property 4's DECISION and is deliberately PURE: its guard
 * (`tests/test_oauth_session_flow_properties.py::_assert_consent_flow_is_pure`) fails if that
 * file's code contains `fetch(`, `D1Database` or `async`, because a decision that performs I/O
 * can no longer be tested as the guarantee itself. Everything here — the D1 table, the reads
 * and writes, the HMAC — is exactly that kind of I/O, so it lives beside it and not in it.
 * The decision module stayed untouched in shape; this module is additive.
 *
 * WHAT IT IS FOR (spec §2.1, §2.2, §2.3, §4)
 * ------------------------------------------
 * The consent page lives on `api.*` and holds no credential: the dashboard session is
 * `__Host-session` on `app.*`, host-only (never sent to `api.*`) and HttpOnly (unreadable by
 * script). Identity must therefore arrive as an ASSERTION about the visitor, minted by the one
 * origin that holds the session. This module is that assertion's minter, plus the record of
 * which flow it may speak for.
 *
 * ⛔ THE FIELD SET IS THE CONTRACT, NOT A CONVENIENCE. `mintConsentToken` mirrors
 * `tortoise/consent_token.py::mint_consent_token` field for field and defends each field the
 * way §2.2 does — see that function. `tests/test_oauth_consent_bff_routes.py` proves the two
 * halves agree by handing a token minted HERE to the PYTHON verifier, because the failure mode
 * of a drift is a production flow that answers `invalid_token` with no other symptom.
 */

// One `Env`, owned by `session.ts` — a second declaration would drift from the runtime's.
import { ensureColumn, type Env } from "./session";

export type { Env };

/**
 * §2.2 `aud` — deliberately NOT a Supabase audience. A Supabase session token (or any other
 * JWT signed with a shared key) must never be readable as a consent assertion.
 */
export const CONSENT_TOKEN_AUDIENCE = "tortoise-consent";

/** §2.2 — `exp = iat + CONSENT_TOKEN_TTL_S`. Bounds EXPOSURE; `jti` bounds REPLAY. */
export const CONSENT_TOKEN_TTL_S = 120;

/**
 * How long a STARTED authorize attempt stays redeemable.
 *
 * MUST outlive the sign-in round trip, or a visitor who spends a few minutes at the provider
 * comes back to `no_authorize_attempt` and has to start over. It is deliberately NOT the
 * token's TTL: that one bounds a MINTED assertion's exposure, this one bounds an UNMINTED flow.
 */
export const CONSENT_ATTEMPT_TTL_S = 15 * 60;

/**
 * Private-use redirect schemes a harness may register — the MIRROR of
 * `tortoise/oauth.py::_NATIVE_REDIRECT_SCHEMES`.
 *
 * An explicit allowlist for the same reason the AS uses one: the code is delivered by
 * NAVIGATING to the target, so a scheme the browser EXECUTES (`javascript:`, `data:`) must never
 * be registrable. `cursor` is the only native-scheme harness in the beta.
 *
 * ⛔ TWO COPIES, ONE MEANING. This file cannot import Python and the AS cannot import this, so the
 * pair is pinned by a cross-half table test in `tests/test_oauth_consent_bff_routes.py`, which
 * hands ONE uri list to both halves and asserts the verdicts agree. Drift is the failure that
 * matters: a BFF that refuses a redirect the AS accepts kills a flow AFTER the visitor consented.
 */
export const NATIVE_REDIRECT_SCHEMES = ["cursor"] as const;

/**
 * Expand an IPv6 literal (brackets already stripped) into its eight hextets, or `null` if it is not
 * one. A trailing dotted quad is folded into two hextets first, so `::ffff:127.0.0.1` and
 * `::ffff:7f00:1` come out identical.
 *
 * ⛔ THIS EXISTS BECAUSE ENUMERATING SPELLINGS IS UNBOUNDED. The previous version compared the host
 * to the literals `"::1"` and `"0:0:0:0:0:0:0:1"`, so `[::01]`, `[0::1]`, `[::0:0:1]`,
 * `[0000::1]` and 11 other zero-padded / compressed spellings were refused here while `ipaddress`
 * accepts every one of them — a registered `http://[::0001]:6277/cb` died at `/api/consent/start`
 * with a message saying the host was not loopback. Addresses are compared as ADDRESSES.
 */
function expandIpv6(host: string): number[] | null {
  let text = host;
  if (!text.includes(":")) return null;  // a plain IPv4 is not this function's job
  const dotted = /(^|:)(\d{1,3}(?:\.\d{1,3}){3})$/.exec(text);
  if (dotted) {
    const octets = dotted[2].split(".");
    // The embedded IPv4 must be canonical: `ipaddress` refuses `127.0.0.01` and `.256` here too.
    if (octets.some((o) => !/^\d{1,3}$/.test(o) || (o.length > 1 && o.startsWith("0")) ||
      Number(o) > 255)) {
      return null;
    }
    const [a, b, c, d] = octets.map(Number);
    text = text.slice(0, dotted.index) + ":" + ((a << 8) | b).toString(16) + ":" +
      ((c << 8) | d).toString(16);
    // `::ffff:127.0.0.1` → slice keeps `::ffff`, and the leading `:` was consumed by the match, so
    // the rejoined text is `::ffff:7f00:1`.
    if (text.startsWith(":::")) text = "::" + text.slice(3);
  }
  const halves = text.split("::");
  if (halves.length > 2) return null;
  const groups = (part: string): number[] | null => {
    if (part === "") return [];
    const out: number[] = [];
    for (const g of part.split(":")) {
      if (!/^[0-9a-fA-F]{1,4}$/.test(g)) return null;
      out.push(parseInt(g, 16));
    }
    return out;
  };
  const left = groups(halves[0]);
  const right = halves.length === 2 ? groups(halves[1]) : [];
  if (left === null || right === null) return null;
  if (halves.length === 2) {
    const fill = 8 - left.length - right.length;
    if (fill < 1) return null;
    return [...left, ...new Array<number>(fill).fill(0), ...right];
  }
  return left.length === 8 ? left : null;
}

/**
 * Loopback host, mirroring `tortoise/oauth.py::_is_loopback` (RFC 8252 §7.3).
 *
 * `localhost`, all of `127.0.0.0/8`, and `::1` — including every zero-padded/compressed spelling of
 * it and the IPv4-MAPPED forms of `127/8` — are loopback; nothing else is.
 *
 * ⛔ THE MAPPED FORMS ARE NOT AN EDGE CASE. `ipaddress.ip_address("::ffff:127.0.0.1").is_loopback`
 * is True, so the AS accepts such a redirect at REGISTRATION; a mirror that only knew `::1` would
 * refuse it here — after the visitor had consented — which is exactly the failure this function
 * exists to prevent.
 */
export function isLoopbackHost(hostname: string): boolean {
  let host = hostname;
  if (host.startsWith("[") && host.endsWith("]")) host = host.slice(1, -1);
  const lower = host.toLowerCase();
  if (lower === "localhost") return true;
  if (lower.includes(":")) {
    const hextets = expandIpv6(lower);
    if (hextets === null) return false;
    const isV6Loopback =
      hextets.slice(0, 7).every((h) => h === 0) && hextets[7] === 1;
    const mapped = hextets.slice(0, 5).every((h) => h === 0) && hextets[5] === 0xffff;
    // `::ffff:a.b.c.d` — the embedded IPv4's FIRST octet decides (127/8).
    return isV6Loopback || (mapped && hextets[6] >> 8 === 127);
  }
  return ipv4Loopback(lower);
}

function ipv4Loopback(host: string): boolean {
  const parts = host.split(".");
  if (parts.length !== 4) return false;
  for (const part of parts) {
    if (!/^\d{1,3}$/.test(part)) return false;
    // ⛔ A LEADING ZERO IS REFUSED, exactly as `ipaddress` refuses it (`127.0.0.01` is ambiguous:
    // octal or decimal?). WHATWG reads it as decimal and normalises to 127.0.0.1, so a looser
    // check here ACCEPTED a redirect the AS refuses — 60 of them in a 3k-uri sweep.
    if (part.length > 1 && part.startsWith("0")) return false;
    if (Number(part) > 255) return false;
  }
  return Number(parts[0]) === 127;
}

/**
 * The host as WRITTEN in the uri's authority (port and userinfo removed), or `null` when the uri
 * carries no explicit `//authority`.
 *
 * THIS IS THE HOST THE AS DECIDES ON: `urlsplit(...).hostname` reports the authority verbatim.
 * WHATWG instead NORMALISES it (`127.1` → `127.0.0.1`, decimal/hex addresses, a trailing dot
 * dropped, an IDN → punycode), which is why the loopback verdict below must read THIS function's
 * answer and not `parsed.hostname` — otherwise the BFF and the AS disagree about `127.1` and one
 * of them refuses what the other accepts.
 *
 * `null` when there is no `scheme://` at all: `urlsplit` yields `hostname=None` there (`WHATWG`
 * invents an authority for a special scheme), and the AS refuses such a uri, so we do too.
 */
function rawAuthorityHost(uri: string): string | null {
  const m = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/([^/?#]*)/.exec(uri);
  if (!m) return null;
  let authority = m[1];
  const at = authority.lastIndexOf("@");
  if (at !== -1) authority = authority.slice(at + 1);
  if (authority.startsWith("[")) {
    const end = authority.indexOf("]");
    return end === -1 ? null : authority.slice(0, end + 1);
  }
  const colon = authority.indexOf(":");
  return colon === -1 ? authority : authority.slice(0, colon);
}

/**
 * Why a `redirect_uri` is unacceptable, or `null` when it is fine.
 *
 * A MIRROR of the AS's `_unsafe_redirect_uri_bytes` + `_valid_redirect_uri`:
 *
 *   https          — any host the AS can see at all (its https branch does NOT consult
 *                    `_is_loopback`, so NORMALISED spellings are fine here and must not be refused);
 *   http           — only a LOOPBACK host, judged from the RAW authority (`127.1` is not loopback
 *                    to `ipaddress`, so the AS refuses it even though WHATWG rewrites it);
 *   native scheme  — allowlisted, and must name a destination.
 *
 * ⛔ THE ONE PLACE THIS IS STRICTER THAN THE AS is a uri WHATWG cannot PARSE at all (a port past
 * 65535, a non-numeric port). `urlsplit` never validates a port, so the AS accepts those; a browser
 * would never navigate one, so handing it out is worse than a 400 at start. That divergence is
 * named in the cross-half table so it cannot quietly grow into a class. (Leading/trailing SPACES are
 * NOT a divergence: they are stripped before the authority is matched, because both parsers strip
 * them.)
 */
export function redirectUriProblem(uri: string): string | null {
  // Bytes we refuse to reason about: a raw backslash is a real parser differential between the
  // server's urlparse and the browser, and control characters corrupt the Location header.
  for (const ch of uri) {
    if (ch === "\\" || ch.charCodeAt(0) < 0x20 || ch.charCodeAt(0) === 0x7f) {
      return "redirect_uri must not carry control characters or a backslash";
    }
  }
  // Leading/trailing SPACES are stripped by BOTH parsers before they see the uri (`urlsplit` in
  // 3.12 does, and WHATWG does), so they are not part of the authority — but `rawAuthorityHost` is
  // anchored at index 0, so an untrimmed `" https://client.example/cb"` looked authority-less here
  // and was refused while the AS accepted it. A registered-and-navigable redirect (registrable
  // because the AS accepts it) then died at `/api/consent/start` with a message blaming itself.
  const trimmed = uri.replace(/^ +/, "").replace(/ +$/, "");
  // ⛔ STRAY BRACKETS IN THE AUTHORITY ARE A PARSER SPLIT. `urlsplit` RAISES on a `[` or `]` anywhere
  // in the netloc that is not an IPv6 host (`_check_bracketed_netloc`), so the AS refuses such a uri;
  // WHATWG happily parses `http://u[ser@127.0.0.1/cb`, so an unguarded BFF ACCEPTS it and 302s to an
  // AS that answers 400. Only a hand-crafted request can reach it (registration runs the same
  // predicate, so no client can register one), which is why this is not worse — but the direction is
  // the one `validate()`'s contract forbids.
  const authorityMatch = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/([^/?#]*)/.exec(trimmed);
  if (authorityMatch !== null) {
    const authority = authorityMatch[1];
    const hostPart = authority.slice(authority.lastIndexOf("@") + 1);
    if (!hostPart.startsWith("[") && /[[\]]/.test(authority)) {
      return "redirect_uri must not carry brackets outside an IPv6 host";
    }
  }
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    // Includes the `:99999`-style port: WHATWG refuses to parse it (the documented divergence).
    return "redirect_uri must be an absolute URI";
  }
  // A fragment is never a valid redirect component (RFC 6749 §3.1.2): the code would land where
  // the browser never sends it. Tested as the literal delimiter, because a bare trailing `#`
  // parses to an EMPTY fragment which `parsed.hash` reports as "".
  if (trimmed.includes("#")) return "redirect_uri must not carry a fragment";
  const scheme = parsed.protocol.replace(/:$/, "");
  if ((NATIVE_REDIRECT_SCHEMES as readonly string[]).includes(scheme)) {
    // Not a network location, so "https or loopback" does not describe it. It must still name
    // something to hand the code to: an authority or at least a path.
    return parsed.host || parsed.pathname ? null : "redirect_uri must name a destination";
  }

  const rawHost = rawAuthorityHost(trimmed);
  if (rawHost === null || rawHost === "") {
    // No `//authority`: `urlsplit` sees no hostname and the AS refuses, whatever WHATWG invented.
    return "redirect_uri must be an absolute URI with an explicit authority";
  }
  if (parsed.protocol === "https:") return null;
  if (parsed.protocol === "http:" && isLoopbackHost(rawHost)) return null;
  return "redirect_uri must use https (http is allowed only for loopback)";
}

/** The four authorize params §2.2 binds, plus the scope/resource the flow was started with. */
export interface ConsentAttempt {
  state: string;
  client_id: string;
  redirect_uri: string;
  code_challenge: string;
  scope: string | null;
  resource: string | null;
  user_id: string | null;
  created_at: number;
  expires_at: number;
  /**
   * SHA-256 of the per-attempt key the CREATING browser holds (null on a row written before this
   * column existed). THIS COLUMN never holds the raw key — but that is a narrower claim than "the
   * raw key is never stored anywhere": the key must reach the browser, so it travels the sign-in
   * hop's return target, and `auth/start.ts` PERSISTS that return target in `auth_flows.next` in
   * this same D1 database (`FLOW_MAX_AGE_S`, 30 MINUTES — not the attempt's 15). So read access to
   * `auth_flows` is equivalent to holding the key for up to THIRTY minutes, while the attempt row
   * itself lives `CONSENT_ATTEMPT_TTL_S` (15) and is renewed only by the key's holder. Two numbers,
   * two carriers, and quoting the small one for both is how a 20-minute-old row gets written off as
   * inert. §2.3/§4 record the carriers as a known cost; do not restate them here as if they did not
   * exist — a claim that is wrong in EITHER direction stops the next lane looking for the residual.
   */
  attempt_key_hash: string | null;
}

/**
 * Idempotent bootstrap for the attempt table, next to the sessions it belongs to.
 *
 * Same argument as `ensureSchema` / `ensurePendingTable`: production gets tables from a
 * migration, but a database created by the runtime bootstraps alone never receives one — and
 * `CREATE TABLE IF NOT EXISTS` cannot add an INDEX to a table that already exists, so the index
 * is created here, with the table.
 */
export async function ensureConsentAttemptTable(db: D1Database): Promise<void> {
  await db.exec(
    "CREATE TABLE IF NOT EXISTS consent_attempts (" +
      "state TEXT PRIMARY KEY, " +
      "client_id TEXT NOT NULL, " +
      "redirect_uri TEXT NOT NULL, " +
      "code_challenge TEXT NOT NULL, " +
      "scope TEXT, " +
      "resource TEXT, " +
      "user_id TEXT, " +
      "created_at INTEGER NOT NULL, " +
      "expires_at INTEGER NOT NULL, " +
      "attempt_key_hash TEXT);",
  );
  // `CREATE TABLE IF NOT EXISTS` cannot add a COLUMN to a table that already exists, and SQLite
  // has no `ADD COLUMN IF NOT EXISTS` — so a database bootstrapped by an earlier runtime build
  // needs the ALTER. `ensureColumn` is the repo's rule for exactly this shape: only the
  // duplicate-column error is swallowed, and a GENUINE DDL failure propagates so the caller can
  // answer a typed 503 instead of leaving a table that silently never evolved (which showed up as
  // "the INSERT references a missing column" — every request 503ing with no signal anywhere).
  await ensureColumn(db, "consent_attempts", "attempt_key_hash", "TEXT");
  await db.exec(
    "CREATE INDEX IF NOT EXISTS idx_consent_attempts_expires " +
      "ON consent_attempts(expires_at);",
  );
}

/**
 * Record that an authorize attempt STARTED, and who started it.
 *
 * ⛔ `user_id` comes from the SESSION, never from the request: it is what lets the mint route
 * refuse a flow a different identity began. It is nullable because the first entry to `start`
 * happens BEFORE sign-in on a cold browser — the very case #3524 was reopened for — and the
 * return leg re-records the same row (`state` is the key) with the resolved owner.
 *
 * ⛔ AND THE OWNER IS ONLY RECORDED BY THE BROWSER THAT CREATED THE ATTEMPT (#10208). The owner
 * column alone was not enough: first-known-owner-wins protects a row that already HAS an owner,
 * so the OWNERLESS window stayed claimable — B's single signed-in `GET /api/consent/start` on a
 * flow A began cold became the first known owner, A's return leg could no longer fill the row,
 * A's mint answered 403, and B minted an assertion for A's `redirect_uri`/`code_challenge`/`state`.
 * So every row carries the hash of a per-attempt key that only the creating browser holds (it rides
 * in the return target, never in a cookie — §2.3 reconciles by the token, not a second cookie),
 * and the fill below is gated on it.
 *
 * Idempotent on `state`: a re-entry must not create a second row for one flow, the key is
 * INSERT-ONLY (a re-entry can never rotate it — rotation would be the takeover, one line down),
 * and the owner is filled only when the presented key matches.
 *
 * Returns whether the CALLER's key is the row's key. `start` uses it to decide whether that key may
 * be handed back in the return target at all: a key this request does not own must never be
 * propagated into someone else's URL.
 */
export async function recordConsentAttempt(
  db: D1Database,
  attempt: Omit<ConsentAttempt, "created_at" | "expires_at" | "attempt_key_hash">,
  attemptKey: string | null,
  now = Date.now(),
): Promise<{ row: ConsentAttempt; owns_flow: boolean }> {
  await ensureConsentAttemptTable(db);
  const keyHash = attemptKey === null ? null : await attemptKeyHash(attemptKey);
  const row: ConsentAttempt = {
    ...attempt,
    created_at: now,
    expires_at: now + CONSENT_ATTEMPT_TTL_S * 1000,
    attempt_key_hash: keyHash,
  };
  // ONE statement decides both properties, so neither can be lost to a refactor of the other:
  //
  //   * `attempt_key_hash` is written ONLY on INSERT. On conflict it is assigned to ITSELF, so a
  //     later caller can never rotate it — rotation is the takeover — and a row that predates the
  //     column (hash NULL) can never be ADOPTED either. COALESCE-ing a NULL here was a real hole:
  //     any caller could write its key into an old-build row and `owns_flow` would then be true
  //     against the value it had just installed, while the mint reads a NULL-hash row as nobody's
  //     — so the two legs disagreed about the same row and the flow was claimable for as long as
  //     old-build rows live (≤ the 15-minute attempt TTL, extended by each re-entry). Such a row
  //     is now simply DEAD (fail closed; the visitor restarts the flow), which is the same verdict
  //     the mint already gives it;
  //   * `user_id` is filled ONLY when the presented key hashes to the stored one. `excluded` first
  //     would be last-writer-wins (identity injection); the CASE is what makes a foreign caller's
  //     write a no-op on the owner column rather than a refusal — a refusal here would tell an
  //     attacker whether their `state` guess landed on a real flow.
  const back = await db
    .prepare(
      "INSERT INTO consent_attempts " +
        "(state,client_id,redirect_uri,code_challenge,scope,resource,user_id,created_at," +
        "expires_at,attempt_key_hash) " +
        "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10) " +
        "ON CONFLICT(state) DO UPDATE SET " +
        "attempt_key_hash=consent_attempts.attempt_key_hash, " +
        "user_id=CASE " +
        "WHEN consent_attempts.attempt_key_hash IS NOT NULL " +
        "AND consent_attempts.attempt_key_hash = excluded.attempt_key_hash " +
        "THEN COALESCE(consent_attempts.user_id, excluded.user_id) " +
        "ELSE consent_attempts.user_id END, " +
        // The TTL is refreshed ONLY by the key's holder. Without this, any caller could keep a row
        // — including a DEAD pre-column one — alive forever just by naming its `state`, so "expires
        // in 15 minutes" would bound nothing. A foreign or keyless re-entry leaves the clock alone,
        // which is also what makes a dead row actually die instead of shadowing the visitor's retry.
        "expires_at=CASE " +
        "WHEN consent_attempts.attempt_key_hash IS NOT NULL " +
        "AND consent_attempts.attempt_key_hash = excluded.attempt_key_hash " +
        "THEN excluded.expires_at " +
        "ELSE consent_attempts.expires_at END " +
        "RETURNING attempt_key_hash",
    )
    .bind(
      row.state,
      row.client_id,
      row.redirect_uri,
      row.code_challenge,
      row.scope,
      row.resource,
      row.user_id,
      row.created_at,
      row.expires_at,
      row.attempt_key_hash,
    )
    .first<{ attempt_key_hash: string | null }>();
  // Opportunistic bounded collection, exactly like `sweepExpiredFlows`: an ABANDONED flow
  // reaches no completion, so without this the table only grows. Never throws — collection is
  // housekeeping on a user path and must not fail the flow.
  await sweepExpiredConsentAttempts(db, now).catch(() => 0);
  const stored = back?.attempt_key_hash ?? null;
  return { row, owns_flow: keyHash !== null && stored === keyHash };
}

/**
 * SHA-256 hex of an opaque attempt key.
 *
 * The COLUMN holds this hash and never the raw key. That is all it buys: the raw key still travels
 * the browser's channels (the authorize URL, the sign-in return target that `auth_flows.next`
 * persists, and the AS page's `redirectTo`), which §2.3/§4 record. Do not upgrade this into "the raw
 * key is never stored" — it is measurably false, and a claim stronger than the code is how a real
 * residual stops being looked for.
 */
export async function attemptKeyHash(key: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", utf8(key));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** A fresh attempt key: 32 random bytes, base64url — the encoding both halves already share. */
export function newAttemptKey(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return b64url(bytes);
}

/**
 * The query parameter the attempt key travels in.
 *
 * It is carried in TWO places, which are both load-bearing: the `authorizeUrl` query (the consent
 * page is the mint's only caller, so it cannot present a key it never received) and the app-origin
 * sign-in return target. It is stripped only on the page's `redirectTo` hop (step 4) — do NOT read
 * "one carrier" into this constant, or that hop will drop it and every mint will answer
 * `403 no_authorize_attempt` after the visitor approved.
 */
export const ATTEMPT_KEY_PARAM = "attempt_key";

/** Delete expired attempts, bounded, never throwing (the `sweepExpiredFlows` contract). */
export async function sweepExpiredConsentAttempts(
  db: D1Database,
  now = Date.now(),
  limit = 500,
): Promise<number> {
  try {
    const res = await db
      .prepare(
        "DELETE FROM consent_attempts WHERE state IN (" +
          "SELECT state FROM consent_attempts WHERE expires_at < ?1 " +
          "ORDER BY expires_at LIMIT ?2)",
      )
      .bind(now, limit)
      .run();
    return res.meta?.changes ?? 0;
  } catch (e) {
    console.error("consent_attempts sweep failed", e);
    return 0;
  }
}

/**
 * Read the recorded attempt for `state`, or `null`.
 *
 * An EXPIRED row is `null`: the window for completing the flow has closed, so answering "start
 * over" one round trip later (after the AS refuses) is strictly worse than answering it now.
 */
export async function readConsentAttempt(
  db: D1Database,
  state: string,
  now = Date.now(),
): Promise<ConsentAttempt | null> {
  await ensureConsentAttemptTable(db);
  const row = await db
    .prepare(
      "SELECT state,client_id,redirect_uri,code_challenge,scope,resource,user_id," +
        "created_at,expires_at,attempt_key_hash FROM consent_attempts WHERE state = ?1",
    )
    .bind(state)
    .first<ConsentAttempt>();
  if (!row) return null;
  if (row.expires_at <= now) return null;
  return row;
}

/** base64url, no padding (§2.1) — the encoding both halves must agree on byte for byte. */
function b64url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function utf8(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

function randomHex(bytes: number): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return Array.from(buf)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Mint the §2.1 token — the minting half of the contract `tortoise/consent_token.py` verifies.
 *
 * ⛔ WHY EACH FIELD EXISTS (§2.2) — this is not a payload shape, it is the security argument:
 *   - `bind.client_id` — without it a token minted while approving client A redeems into an
 *     approval for client B;
 *   - `bind.redirect_uri` — without it THE CODE IS REDIRECTED TO AN ATTACKER ORIGIN (the
 *     classic mix-up);
 *   - `bind.code_challenge` — without it the attacker's own verifier completes a flow the
 *     victim approved;
 *   - `bind.state` — without it, cross-flow substitution;
 *   - `jti` — single-use: ONE TOKEN must not yield N authorization codes. ⚠️ Not the same claim as
 *     "one consent must not yield N codes": step 2 deliberately costs a RE-MINT rather than a
 *     re-login when `issue_auth_code` refuses after the nonce is spent (`hosted_api.py`, "step 4's
 *     page mints again on load"), and this route does not consume the attempt, so ONE APPROVAL CAN
 *     YIELD ONE CODE PER MINT CALL. `jti` bounds the token, not the approval. Closing that residual
 *     needs a single-use mark on `consent_attempts` — a step-2/step-3 contract change, not a line
 *     here — and the behaviour is pinned (`test_the_attempt_can_be_MINTED_AGAIN_for_step_4s_\
 *     recovery`) so it cannot be narrowed silently either;
 *   - `exp` — bounds exposure if the assertion leaks;
 *   - `aud`/`iss` — a Supabase-shaped token, or one minted by a look-alike origin, must not
 *     verify here;
 *   - `sub` — the identity, and the ONLY thing the AS may take from this token. `email` is
 *     display-only and never an authorization input.
 *
 * `kid` is fixed to `consent-1` here and accepted FOR PRESENCE ONLY by the verifier. §2.3 states a
 * `kid`-scoped rotation as the goal, but that allowlist is deliberately NOT BUILT (one
 * `CONSENT_TOKEN_SECRET` is configured, so a two-`kid` window cannot exist): a rotation today is a
 * VALUE SWAP that invalidates in-flight assertions for their 120 s TTL. Do not read this line as
 * the rotation property — it is the seam for it (docs/engineering/auth/consent-identity-bridge.md
 * §9 step 1 records the allowlist as unbuilt).
 */
export async function mintConsentToken(input: {
  secret: string;
  issuer: string;
  userId: string;
  email: string | null;
  bind: { client_id: string; redirect_uri: string; code_challenge: string; state: string };
  now?: number;
  jti?: string;
}): Promise<{ token: string; exp: number }> {
  if (!input.secret) {
    // Fail closed and named: an EMPTY key would sign a token anyone could forge, and a
    // hardcoded default would be a forgeable consent (§2.3). Callers answer 503 instead.
    throw new Error("CONSENT_TOKEN_SECRET is not set");
  }
  const issuedAt = Math.floor((input.now ?? Date.now()) / 1000);
  const exp = issuedAt + CONSENT_TOKEN_TTL_S;
  // Key order mirrors `mint_consent_token` exactly; the verifier recomputes the MAC over the
  // RECEIVED segment, so only the VALUES are its contract — but keeping the order identical is
  // what makes a cross-language diff of the two tokens readable.
  const header = { alg: "HS256", typ: "JWT", kid: "consent-1" };
  const payload = {
    iss: input.issuer,
    aud: CONSENT_TOKEN_AUDIENCE,
    iat: issuedAt,
    exp,
    jti: input.jti ?? randomHex(32),
    sub: input.userId,
    email: input.email,
    bind: {
      client_id: input.bind.client_id,
      redirect_uri: input.bind.redirect_uri,
      code_challenge: input.bind.code_challenge,
      state: input.bind.state,
    },
  };
  const signingInput = `${b64url(utf8(JSON.stringify(header)))}.${b64url(utf8(JSON.stringify(payload)))}`;
  const key = await crypto.subtle.importKey(
    "raw",
    utf8(input.secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, utf8(signingInput));
  return { token: `${signingInput}.${b64url(new Uint8Array(mac))}`, exp };
}
