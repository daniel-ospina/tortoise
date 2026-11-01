// tortoise-hook-version: 16
// tortoise-capture — the in-repo Pi capture extension (#3575, #1727 T1).
//
// The `tortoise-hook-version` marker above is the install-contract generation
// for this seam (see tortoise/hook_install.py): column-0, one per file, bumped
// on ANY behavioural edit. It is what lets `tortoise session verify --harness
// pi` tell an already-installed copy that it is stale — before #4680 the Pi
// seam carried no marker at all, so a copy predating a seam change kept
// capturing with the old logic: `session verify` called it UNVERIFIABLE-IN-CI
// rather than STALE, and `tortoise doctor` printed no freshness row for it at
// all. Generation 1 is the first contract for this seam.
//
// Generation 2 (#4897): `extractTurns` no longer cuts a >TURN_MAX_CHARS turn
// SILENTLY. A clipped turn now carries a truncation marker with its true
// length (see `clipTurnContent`), so the bytes this seam POSTs changed and
// every already-installed generation-1 copy must read as stale and be
// reinstalled — an old copy would keep storing an unmarked 5,000-char wall.
//
// Generation 3 (#5109): `resolveConfig` now WARNS (once per path, stderr, path
// + mode only, never refusing) when the FILE-sourced credential store is
// group/other-readable — the read side of a store our writers pin `0600`. That
// is a behavioural edit to this artifact, so the generation moves with it: an
// already-installed generation-2 copy must read as STALE (`found < expected`),
// not as `modified-artifact` ("marker matches but its bytes differ — local
// edit"), which would tell every existing Pi user they had edited a file they
// never touched and make the generation number incomparable across installs.
// Any later behavioural edit here moves it again — the marker is the install
// contract, not a changelog (see `hook_install.detect_artifact_install`).
//
// Generation 4 (#3512): a PROSE-ONLY edit — the note below said the second
// producer (`agent-infra`'s `reflect-hook`) was ungated, which agent-infra
// `7e77470e4` (2026-10-16, #803) had already closed by putting both recorders
// on `extensions/shared/capture-gate.ts`. No line of behaviour changed here,
// and the generation moves anyway, because `detect_artifact_install` compares
// BYTES once the markers agree: an installed generation-3 copy carrying the
// old comment would otherwise read as `modified-artifact` ("marker matches (3)
// but its bytes differ — local edit") — an accusation against a file the user
// never touched, which is exactly the harm this marker exists to prevent. A
// bump makes it read as STALE ("reinstall"), the correct advice whenever the
// shipped bytes move. `session verify`'s "it captures with older logic"
// wording is imprecise for this one generation; the classification and the
// repair it prompts are right.
//
// Generation 16 (capture-loss, #10479): this seam no longer DISCARDS an
// undelivered capture to make room, and it says so on the health line. Four
// behavioural edits, all pinned by tests: (a) `pruneSpool`'s candidate set is
// FILED entries only — an undelivered entry is the only copy of a conversation
// that never reached the graph, so a full spool now REFUSES new work
// (`spoolSaturated`, recorded as `spool_saturated`) instead of unlink-ing the
// oldest; (b) the per-entry bound is asked about what is ALREADY on disk, and a
// write that would push a legal entry past it is refused
// (`entry_growth_refused`) rather than marking a fileable transcript terminal;
// (c) `spoolSaturated` credits reclaimable ORPHAN bytes, so the refusal cannot
// outlive a condition `pruneSpool` is entitled to clear; (d) a sixth health
// state `capture: SATURATED` exists, because the other five could not say
// "refusing every new capture" — a ceiling filled by terminal entries read
// `OK (0 queued)`, byte-identical to a healthy box. Every one of these ships in
// the same bytes as the rule they implement, so an installed generation-15 copy
// must read as STALE rather than keep evicting undelivered captures.
//
// Generation 14 (#3515 review): the clock tolerance is ONE value shared by the
// two arms that compare a persisted `at` with our spawn stamp. Generation 13
// added it to the exit-0 arm only, leaving the precedence arm exact — so on a
// platform whose `at` resolves to a second (or after a backwards step) the
// extension stopped recognising the CLI's just-written record as THIS sweep's and
// overwrote `worker_missing` with `exit_1`, which is exactly the generation-11
// defect. Both arms now derive the slack from `CLOCK_SLACK_S`.
//
// Generation 13 (#3515 review): the exit-0 corroboration gained a ONE-SECOND
// tolerance on `at`. Without it, any writer whose stamp is second-granular in the
// same second read as older, and this branch OVERWROTE a healthy status and
// reported `exit_0_without_status` on a box whose backstop was working — a
// fail-closed over-report, every sweep. The reason fallback also strips the
// punctuation that breaks the health line's comma-delimited grammar (only known
// classes were tokenised before), and the CLI's writer now narrows an EXISTING
// store directory to 0700 the way the extension's does.
//
// Generation 12 (#3515 review): exit 0 is corroborated, and the health line's
// reason is a TOKEN. A sweep that exits 0 without writing a fresh status left the
// PREVIOUS verdict standing — the CLI's bookkeeping write is best-effort, and a
// `tortoise` on PATH that is not the shipped CLI can exit 0 without sweeping — so
// the extension now reads the status on a zero exit and records
// `exit_0_without_status` when it is absent or older than this spawn. The
// persisted `reason` is a TOKEN for the classes that are known
// (`no_credential`, `no_header_id`, `permission_denied`, `no_space`, `bad_json`)
// instead of the CLI's free-form stderr prose: the precedence fix is what made
// that prose reach the line, where it duplicated the line's own
// "no TORTOISE_API_KEY" and embedded an unquoted sentence in a comma-delimited
// field. Anything unrecognised has that punctuation replaced before it is
// persisted. The full text stays on stderr and in `--json`.
//
// Generation 11 (#3515 review): the extension's record is now SUBORDINATE to the
// CLI's own, and the spawn names the directory it writes to. `close` fires AFTER
// the CLI's `main` has already written `{ok: false, reason: "worker_missing"}`
// and exited non-zero, so an unconditional write replaced that named diagnosis
// with an opaque `exit_1` on every GRACEFUL failure — the seam's whole product,
// thrown away on its commonest path. A record whose `at` is at or after this
// sweep's spawn time is the CLI's, and it wins. The spawn also passes
// `--cursor-dir <derived store>`, because the CLI otherwise defaults to
// `$HOME/.tortoise/store-sync` while both legs of the line read
// `<spool-parent>/store-sync`: under the documented `TORTOISE_CAPTURE_SPOOL_DIR`
// override the two silently disagreed (the dead-letter count read 0 and the
// failure degraded to `exit_N`). The bookkeeping directory is created `0o700`
// with a `0o600` status file for the extension's own write (the CLI's writer sets
// its own mode).
//
// Generation 10 (#3515 review): a sweep's failure is recorded from EVERY path
// that can produce one, and a killed sweep names its SIGNAL. Generation 9 wired
// the recorder to the child's `exit`, which a CLI that is ABSENT from PATH never
// emits — it emits `error` (ENOENT) and `close` with a negative code — so a box
// whose `tortoise` CLI disappeared kept printing `capture: OK` over an inert
// crash/kill recovery. The listener is `close` (it fires for a child that ran
// and for one that never started); `code === null` means the sweep was KILLED
// (OOM, shutdown) and the reason is `signal_<SIG>` rather than the literal
// string `exit_null`; and the outer `catch` records a failed spawn too.
//
// Generation 9 (#3515 review): the extension now RECORDS a sweep failure it
// alone can observe. `sweep-status.json` was written only by the CLI's `main`,
// so a failure BEFORE it runs — a `node` too old to parse the shipped worker, a
// removed `node`, a truncated artifact — wrote nothing and left an EARLIER
// healthy status on disk: the line printed `capture: OK` over a backstop that
// had just died, which is the silent death the seam exists to end (and worse
// than the original, because the line now looks informed). `recordSweepFailure`
// writes `ok: false` from the child's exit path, and both legs of the line
// render it. (The listener moved from `exit` to `close` in generation 10, and
// generation 11 made the record subordinate to the CLI's own — see above.) Generation 8's bytes are preserved as the generation-8 digest; this is a
// new byte set, so it is a new generation rather than a rewritten entry.
//
// Generation 8 (#3515 review): the `capture:` health line now also reports the
// store-sync backstop's DEAD-LETTER count. A permanent refusal dead-letters a
// session's window and CROSSES its cursor, so those turns can never re-ship;
// the count lived only in the worker's receipt, and the one production caller
// spawns it with `stdio: "ignore"` — i.e. the loss was counted into a document
// nothing reads. The count belongs on the only line a user sees per session.
//
// Generation 7 (#3515): `session_start` now also starts the STORE-SYNC
// BACKSTOP — a detached one-shot `tortoise store-sync` that drains Pi's own
// session `.jsonl` (the death modes this hook cannot see: SIGKILL, crash,
// closed terminal), excluding the live session. Behaviour moved, so the
// generation moves with it: an already-installed generation-6 copy must read as
// STALE, or the box keeps the hook-only seam that loses killed sessions.
//
// Generation 5 (#2100): the drain is now BOUNDED and never runs on the turn's
// critical path, a quota refusal is a NAMED per-box state instead of a silent
// per-item backoff, and every `session_start` prints one `capture:` health
// line. agent-infra#2100 measured what the absence cost: a box whose spool had
// grown to 3.3 MB / 320 quota-deferred entries produced 259–262 B of
// banner-only output per lane and looked dead, while two sibling boxes with the
// same harness ran normally. Behaviour moved, so the generation moves — an
// already-installed generation-4 copy must read as STALE, or the box keeps the
// unbounded drain that filled its spool.
//
// This is the Pi leg of the capture-INSTALL seam. It is installed BY THE
// PRODUCT — `HARNESS_INSTALL.pi` copies this file into
// `~/.pi/agent/extensions/tortoise-capture.ts` — so a Pi user who clicks
// "Session recording" on the dashboard gets a real capture step, not an
// assertion. It is the Pi equivalent of `tortoise/claude-hooks/`:
//
//   session_start    → POST /v1/sessions/install-probe  { harness: "pi" }
//   session_shutdown → POST /v1/sessions               { harness, session_id,
//                                                        source, conversation }
//
// Installing this extension IS the opt-in, and it is the one IN-REPO capture
// seam that reads no TORTOISE_CAPTURE — unlike the in-repo Claude Code, Codex
// and Cursor hooks, which file nothing until the machine sets
// TORTOISE_CAPTURE=1 (#3615). (A second producer — agent-infra's `reflect-hook`
// — lives outside this repo and is NOT shipped to users. It uploaded on key
// presence alone until agent-infra `7e77470e4` (2026-10-16, #803), and now
// shares `extensions/shared/capture-gate.ts` there: an explicit `"cloud": true`
// opt-in *and* a key, with env and a repo-local config able to deny only. The
// measured disposition, and every module that must move when the two recorders
// consolidate, are in tortoise/pi-hooks/README.md §Migration (#3512).) There is
// deliberately NO `autoCapture`-style default-false flag, so the shipped
// capture step can never silently do nothing. The server can still refuse the
// capture POST with a 409 while the organization has agent sessions switched
// off (Memory sources > Agent sessions) — that toggle is default-ON
// (ToS-covered) and can only refuse, never enable.
//
// Self-contained on purpose: it talks to the same hosted API the generated
// `.mcp.json` points at, using `TORTOISE_API_KEY` / `TORTOISE_API_URL` (both
// already required by the Pi install) or `~/.pi/agent/tortoise-config.json`.
// It has NO dependency on agent-infra (which is not shipped to users) and does
// NOT require a local `tortoise` CLI or Python install. Success is logged ONLY
// on a 2xx — a failed capture is reported honestly, and never blocks Pi.
//
// Idempotency: `session_id` is the server's upsert key, so a re-capture of the
// same session converges to one Session (matching `tortoise session capture`).

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { createHash } from "node:crypto";
import {
  appendFileSync,
  chmodSync,
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { homedir, hostname, userInfo } from "node:os";
import { basename, dirname, join, resolve as resolvePath } from "node:path";
import { spawn } from "node:child_process";

// ── Constants ─────────────────────────────────────────────────────────────

export const HARNESS = "pi";
export const DEFAULT_API_URL = "https://api.premiselabs.co";
export const CONFIG_PATH = join(homedir(), ".pi", "agent", "tortoise-config.json");
/**
 * Hosted POST /v1/sessions turn cap — the bound the BFF HANDLER enforces
 * (`tortoise/quota.py::MAX_SESSION_TURNS`; `hosted_api._capture_session_impl`
 * raises HTTP 400 above it), NOT `SessionRequest.conversation`'s
 * `max_length=1000` (a Pydantic boundary the handler then rejects). The
 * backfill leg derives its cap from the same constant
 * (`tortoise/session_import/parsers.py::MAX_TURNS`); `tests/test_pi_capture_hooks.py`
 * pins this literal to it so the two legs cannot drift.
 */
export const MAX_TURNS = 500;
/** Hosted per-turn stored window (tortoise sdk._CAPTURE_TURN_CAP).
 *
 * ⛔ ONE number, owned by Python. The extension is shipped standalone (it
 * cannot import a Python constant), so this literal is the client's copy and
 * `tests/test_pi_capture_hooks.py` pins it to `sdk._CAPTURE_TURN_CAP` — a
 * divergence reds a test instead of storing a different window per lane. */
export const TURN_MAX_CHARS = 5000;
/**
 * Sentinel prefix of the truncation marker appended when a turn is clipped at
 * `TURN_MAX_CHARS` (#4897). MUST stay identical to the Python sentinel
 * (`sdk._CAPTURE_TRUNCATION_SENTINEL`): the server stores a client-clipped
 * turn verbatim, so a reader on either side has to recognise the other's
 * marker. Pinned by `tests/test_pi_capture_hooks.py`.
 */
export const TRUNCATION_SENTINEL = "…[truncated:";

/** The marker appended INSIDE the window when a turn is cut (#4897). */
export function truncationMarker(total: number): string {
  return ` ${TRUNCATION_SENTINEL} original length ${total} chars]`;
}

/**
 * Code points treated as BLANK by `clipTurnContent` — the exact Python set
 * (`tortoise/sdk.py::_CAPTURE_BLANK_CHARS`). Spelled out explicitly instead of
 * using `String.prototype.trim()` because this is the client half of a
 * TWO-LANGUAGE contract (#4897 review round 15, P3): `trim()` strips U+FEFF but
 * NOT U+001C-U+001F or U+0085, while Python's `str.strip()` does the reverse —
 * so the two clippers disagreed on exactly those five code points and the
 * "byte-identical stored turns" claim was false for them. This is the UNION of
 * the two sets, so no input either side called blank becomes non-blank on the
 * other. Pinned to the Python literal by the cross-language parity case in
 * `tests/test_pi_capture_hooks.py`.
 */
export const BLANK_CHARS =
  "\u0009\u000a\u000b\u000c\u000d" +          // tab, LF, VT, FF, CR
  "\u001c\u001d\u001e\u001f" +                // file/group/record/unit separator
  "\u0020\u0085\u00a0\u1680" +
  "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a" +
  "\u2028\u2029\u202f\u205f\u3000\ufeff";

/** `content` is blank iff this finds nothing (see `BLANK_CHARS`). */
const NON_BLANK_RE = new RegExp(`[^${BLANK_CHARS}]`);

/**
 * `content` unchanged when it fits `cap`; otherwise cut AND marked (#4897).
 *
 * Code-point safe on purpose: iteration is over `Array.from(content)`, so a
 * multi-byte character (a surrogate pair) straddling the cut is never split
 * into a lone surrogate, and counting CODE POINTS (not UTF-16 units) matches
 * the server, whose `len()` counts code points — so a client-clipped turn is
 * always `<= cap` on the server and the server's own cap re-application is a
 * no-op.
 *
 * The marker is reserved INSIDE the cap, so the result is `<= cap` code points
 * and `clip(clip(x)) === clip(x)`: a marker appended after a full-width cut
 * would be destroyed by the server's second application.
 */
export function clipTurnContent(
  content: string,
  cap: number = TURN_MAX_CHARS,
): string {
  const points = Array.from(content);
  if (points.length <= cap) return content;
  // ⛔ A BLANK RETENTION IS NOT MARKED — the SAME predicate as the Python
  // clipper (#4897 review round 14, P3; blankness unified in round 15). The
  // marker means "there is more"; for a body that holds nothing that statement
  // is misleading, and a marker-ONLY turn is what made the server's v1 extractor
  // mint a Point whose entire content was the marker itself. The server now skips
  // such a turn at extraction, and `NON_BLANK_RE` keeps the two clippers
  // byte-identical on blank input too — `trim()`/`strip()` disagree on five code
  // points, so the blankness test is the explicit shared set, not either
  // language's builtin.
  if (!NON_BLANK_RE.test(content)) return points.slice(0, cap).join("");
  let marker = truncationMarker(points.length);
  let keep = cap - Array.from(marker).length;
  if (keep < 0) {
    // `cap` too small to carry the full marker (never the production 5000).
    // Fall back to the bare sentinel so the cut stays VISIBLE.
    marker = TRUNCATION_SENTINEL;
    keep = Math.max(cap - Array.from(marker).length, 0);
  }
  const out = points.slice(0, keep).join("") + marker;
  // The degenerate branch can still exceed the cap by the sentinel's own
  // width; trim by code points (never a UTF-16 slice, which could split a
  // surrogate).
  const outPoints = Array.from(out);
  return outPoints.length <= cap ? out : outPoints.slice(0, cap).join("");
}
/** Bounded network budget — Pi must never be blocked by a capture. */
export const REQUEST_TIMEOUT_MS = 10_000;
/**
 * Install-probe budget — MUCH shorter than the capture's, because Pi AWAITS
 * `session_start` (`AgentSession.bindExtensions` → `await emit("session_start")`),
 * so a probe that hangs stalls startup. An endpoint that BLACKHOLES (SYN
 * dropped by a firewall / VPN / captive portal — not refused) would otherwise
 * burn the full capture budget on every start. Best-effort telemetry only.
 */
export const PROBE_TIMEOUT_MS = 2_000;

/**
 * #2100: how much filing work ONE drain pass may do before it yields.
 *
 * The defect these bound: a drain attempted EVERY entry that was past its
 * backoff window, on every opportunity, and the `session_start` call ran its
 * synchronous prologue (spool scan + turn reads) inside a handler Pi AWAITS.
 * A 3.3 MB / 320-entry quota-blocked spool therefore sat between a lane and
 * its first model call, and the lane produced nothing (259–262 B of banner).
 *
 * These are bounds on a PASS, not on retention and not a capture cadence: the
 * spool's own ceilings stay COUNT and BYTES only (#3870 — no TTL / max-age),
 * and "no N-turns cadence" above still holds. A pass that stops early leaves
 * every un-attempted entry in the spool, unfiled and un-dropped, and the
 * extension continues in the background (see `FLUSH_MAX_CHAIN`).
 *
 * `FLUSH_MAX_ATTEMPTS` of 8 with the 10 s `REQUEST_TIMEOUT_MS` keeps the WORST
 * case (a blackholed endpoint) at ~80 s of background work, which nothing
 * awaits — and the wall-clock budget usually binds first anyway.
 */
export const FLUSH_MAX_ATTEMPTS = 8;
/** Wall-clock budget for one pass, checked between entries (never mid-POST). */
export const FLUSH_BUDGET_MS = 5_000;
/** Background continuation passes after a bounded pass. Bounded: an always-full
 *  spool must not become a self-feeding drain loop. */
export const FLUSH_MAX_CHAIN = 4;
/** Delay before a continuation pass. `unref()`d, so it never holds Pi open. */
export const FLUSH_FOLLOW_UP_DELAY_MS = 250;

/** #2100: the per-box status file. Lives in the spool ROOT (never `entries/`),
 *  so it is invisible to the spool's own accounting, and is written atomically.
 *  It is the durable half of the health line: a fresh process (every Pi lane is
 *  one) must be able to say WHY the spool is not moving, without re-POSTing to
 *  find out. */
export const SPOOL_STATUS_FILE = "status.json";
/** The server's machine-readable plan-allowance category (`hosted_api` 402 body). */
export const QUOTA_EXCEEDED_CODE = "quota_exceeded";
/**
 * #2100: the OTHER 402 category — the org's COHORT SPEND CAP.
 *
 * Both arrive as 402 on `/v1/sessions`, and the server is explicit that a
 * caller must be able to tell them apart: reading a spend cap as "you are out
 * of plan allowance" would send the user to buy a bigger plan that cannot lift
 * it (`tortoise/cohort_cost.py`, `COHORT_COST_REFUSAL_CODE`). The classification
 * below (`classifyFailure`: 402 stays TRANSIENT → defer) is unchanged; only the
 * NAME the operator reads changes.
 */
export const COHORT_COST_CAP_CODE = "cohort_cost_cap";
/** Every 402 category this client can name. */
const REFUSAL_CODES = [QUOTA_EXCEEDED_CODE, COHORT_COST_CAP_CODE] as const;

export interface CaptureConfig {
  apiUrl: string;
  apiKey: string;
}

export interface Turn {
  role: "user" | "assistant";
  content: string;
}

export interface PostResult {
  ok: boolean;
  status?: number;
  detail?: string;
}

export type Env = Record<string, string | undefined>;
/** Cached — `deriveMachineId` runs on every `turn_end`. */
let cachedMachineId: string | undefined;
export type FetchLike = (
  input: string,
  init?: Record<string, unknown>,
) => Promise<{ ok: boolean; status: number; json: () => Promise<unknown> }>;

// ── Config ────────────────────────────────────────────────────────────────

// ── #5109: the READ side of the credential store ──────────────────────────

/**
 * Store paths already warned about, keyed by path — ONCE PER PROCESS. Every
 * `turn_end` resolves the same store, so a line per resolution would drown the
 * one warning that matters. Exported so a test can prove the key is the path
 * (not a global boolean) and clear it between cases.
 */
export const _CREDENTIAL_MODE_WARNED = new Set<string>();

/** #3515 piece 5: the CLI the sweep looks for, and a once-per-process flag so a
 *  machine without it warns ONE time instead of on every session start. */
export const CANDIDATE_CLIS = ["tortoise"];
let _SWEEP_UNAVAILABLE_WARNED = false;

/**
 * #5109 (the capture-hook half): say so when a credential store is
 * group/other-readable.
 *
 * EVERY writer of this store already pins it closed — the CLI writes it 0600
 * and the directory 0700 (#1708) — but nothing verified that on READ, so a
 * store whose mode had drifted (restored from a backup, re-created by hand,
 * `cp`'d without `-p`) was opened and its value used at whatever mode it
 * carried, silently. This is the sibling of
 * `tortoise/__main__.py::_warn_if_credential_store_is_readable`: same check,
 * same shape, the OTHER in-repo reader.
 *
 * What this is, and is not (mirrored from the CLI half, deliberately):
 *
 * * It WARNS, it does not refuse. A `0644` store behind a `0700` parent is not
 *   reachable by another user (#5109's own 2026-10-09 correction), so refusing
 *   would over-block a setup that is not exposed.
 * * It NEVER prints the credential — the path and the mode only. A warning
 *   that quotes what it warns about would write the secret into the very
 *   transcript this issue is about.
 * * It goes to **stderr** (`warn`), never stdout: a Pi extension's stdout is
 *   its log channel, and this is a hazard hint, not a capture event.
 * * It cannot fail the command. A store we cannot `stat` is not this check's
 *   business — credential resolution already succeeded by reading it.
 */
export function warnIfCredentialStoreIsReadable(path: string): void {
  let mode: number;
  try {
    mode = statSync(path).mode & 0o777;
  } catch {
    return; // absent / unstattable — nothing to check (never throw)
  }
  if ((mode & 0o077) === 0) return; // no group/other bits at all → silence
  if (_CREDENTIAL_MODE_WARNED.has(path)) return;
  _CREDENTIAL_MODE_WARNED.add(path);
  warn(
    `${path} is mode ${mode.toString(8).padStart(4, "0")} — group/other can ` +
      `read the credential stored in it. Fix: chmod 600 ${path}`,
  );
}

/**
 * Resolve apiUrl/apiKey: env first, then `~/.pi/agent/tortoise-config.json`.
 * The Pi install already exports both into the launching shell, so the happy
 * path needs no config file at all.
 *
 * CO-SOURCE (#2369 D1.1, mirrored from `tortoise/__main__.py::_resolve_config_path`):
 * one identity = one source chain. When the KEY came from the env, the env
 * `TORTOISE_API_URL` may apply (default when unset). When the key came from
 * the FILE, its URL resolves from the SAME file or the built-in default ONLY —
 * a poisoned env must never redirect a stored `tt_…` credential (and every
 * captured conversation with it) to an attacker host.
 */
export function resolveConfig(
  env: Env = typeof process === "undefined" ? {} : process.env,
  configPath: string = CONFIG_PATH,
): CaptureConfig {
  let file: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(readFileSync(configPath, "utf-8"));
    if (parsed && typeof parsed === "object") file = parsed as Record<string, unknown>;
  } catch {
    // absent / unreadable config — env vars or defaults apply
  }
  // Empty/whitespace env keys are treated as unset (the CLI's strip rule) so a
  // blank env key can never shadow a stored one or join a split-brain chain.
  const envKey = String(env.TORTOISE_API_KEY ?? "").trim();
  const fileKey = typeof file.apiKey === "string" ? file.apiKey.trim() : "";
  const fileUrl = typeof file.apiUrl === "string" ? file.apiUrl : "";

  const apiKey = envKey || fileKey;
  // #5109: a FILE-sourced key is the only case worth checking — an env key has
  // no store on disk to have drifted. Checked here rather than in the caller
  // because `resolveConfig` IS this runtime's credential-USE seam.
  if (!envKey && fileKey) warnIfCredentialStoreIsReadable(configPath);
  // ENV key → env URL chain; FILE key → file URL chain (never env).
  const apiUrl = (envKey ? env.TORTOISE_API_URL : fileUrl) || DEFAULT_API_URL;
  return { apiKey, apiUrl: String(apiUrl).replace(/\/+$/, "") };
}

/**
 * machine_id = sha256 hex of `{hostname}\0{username}` — mirrors
 * `tortoise/session_attribution.py::derive_machine_id` (never-throw). It is
 * client-claimed install attribution, never security-trusted (server stores it
 * verbatim as an additive Session property).
 */
export function deriveMachineId(): string {
  if (cachedMachineId !== undefined) return cachedMachineId;
  let host = "";
  let user = "";
  try {
    host = hostname();
  } catch {
    host = "unknown-host";
  }
  try {
    user = userInfo().username;
  } catch {
    user = "unknown";
  }
  if (!host) host = "unknown-host";
  if (!user) user = "unknown";
  cachedMachineId = createHash("sha256").update(`${host}\0${user}`, "utf8").digest("hex");
  return cachedMachineId;
}

// ── Turn extraction ───────────────────────────────────────────────────────

function flattenContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  const parts: string[] = [];
  for (const part of content) {
    if (part && typeof part === "object" && typeof (part as { text?: unknown }).text === "string") {
      parts.push((part as { text: string }).text);
    }
  }
  return parts.join("\n");
}

/**
 * Convert Pi session entries into the canonical hosted conversation turns.
 * Pi entries are `{ type: "message", message: { role, content } }`; system
 * prompts, tool results, bash/custom/compaction entries are context noise and
 * skipped (the same user/assistant-only convention as the Claude capture).
 */
export function extractTurns(entries: Array<Record<string, unknown>>): Turn[] {
  const turns: Turn[] = [];
  for (const entry of entries) {
    if (!entry || entry.type !== "message") continue;
    const message = (entry.message ?? {}) as Record<string, unknown>;
    const role = String(message.role ?? "");
    if (role !== "user" && role !== "assistant") continue;
    const text = flattenContent(message.content).trim();
    if (!text) continue;
    // #4897: clip WITH a marker rather than `slice(0, TURN_MAX_CHARS)`. A cut
    // turn now records its true length, so a reader can tell "the user said
    // this much" from "we cut it here" — the silent mid-word cut is the defect.
    turns.push({ role, content: clipTurnContent(text) });
    // Keep the MOST RECENT turns — matching the backfill leg's
    // `window_turns` (`turns[-MAX_TURNS:]`). Dropping the oldest is the
    // whole point: recent context is what memory wants. An early `break`
    // here would keep the OLDEST MAX_TURNS instead (#3707).
    if (turns.length > MAX_TURNS) turns.shift();
  }
  return turns;
}

/** `source` is the session-file stem (basename only — never a full path). */
export function sourceName(sessionFile: string | undefined | null): string {
  if (!sessionFile) return HARNESS;
  return basename(String(sessionFile)).replace(/\.jsonl$/i, "") || HARNESS;
}

/** Best-effort `provider/model` label from the active Pi model. */
export function modelLabel(model: unknown): string | undefined {
  if (!model || typeof model !== "object") return undefined;
  const m = model as { provider?: unknown; id?: unknown };
  const provider = typeof m.provider === "string" ? m.provider : "";
  const id = typeof m.id === "string" ? m.id : "";
  const label = provider && id ? `${provider}/${id}` : id || provider;
  // Server bound (model max_length=128) + control-char rejection.
  const clean = label.replace(/[\x00-\x1f]/g, "").trim();
  return clean ? clean.slice(0, 128) : undefined;
}

/** #3516 §B / #3515 piece 12: the closed set of clocks a leg may CLAIM. Pinned
 *  here as well as at the server (`_SESSION_CAPTURED_AT_SOURCE_VALUES`) because
 *  the spool is the leg that can lose the conversation: a source the server
 *  refuses is a 422, a 422 is classified PERMANENT, and the drain then unlinks
 *  the entry's turn log — the only copy. Normalising here makes that refusal
 *  unreachable from either spool leg. */
const CLIENT_CAPTURED_AT_SOURCES = new Set([
  "cli_observed",
  // #3515 piece 12: the store-sync backstop's clock is the JSONL entry's own
  // `timestamp` — its own member, because recording it as `file_mtime` would
  // state a provenance the value does not have.
  "entry_timestamp",
  "file_mtime",
  "unknown",
]);

/** #7708: the CLOSED producer-lane vocabulary, mirroring the server's
 *  `hosted_api._SESSION_CAPTURE_LANE_VALUES` (the set that produces the 422).
 *  Pinned here (not imported — this is a different language) for the same reason
 *  as `CLIENT_CAPTURED_AT_SOURCES`: the spool is the leg that can lose the
 *  conversation. Distinct from the EXTRACTION lane (m2/v2): same word, different
 *  meaning. A test asserts this equals the server's set, so drift reds. */
export const CAPTURE_LANES = new Set(["hook", "store_sync"]);

/** The ONE lane normalisation, used by the writer and every comparison site.
 *
 *  Returns the value iff it is a recognised lane, else `undefined` ("no lane").
 *  A non-string or an unrecognised token can therefore never be stored, posted,
 *  or used as part of a filing identity — the server's 422 for a bad lane is
 *  unreachable from either spool leg, and a stored falsy lane (`""`/`0`/`false`)
 *  normalises to the SAME `undefined` the payload omits, so the skip clause and
 *  the compare-and-swap agree with the wire (without this, `undefined === ""` is
 *  false on both and the entry re-POSTs its whole transcript on every drain —
 *  the #4714 amplification loop). */
export function normaliseLane(value: unknown): string | undefined {
  return typeof value === "string" && CAPTURE_LANES.has(value) ? value : undefined;
}

/** A usable client capture instant, or `undefined`.
 *
 *  `typeof !== "number"` already refuses `true`/`false` (a JSON bool is not a
 *  clock), and `Number.isFinite` refuses `NaN`/`Infinity` — which do NOT
 *  round-trip: `JSON.stringify` emits `null` for them, so the Python leg would
 *  read a stamp of `null` where this leg wrote a number. */
function finiteInstant(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** Resolve the client stamp as a PAIR: the instant with ITS OWN clock.
 *
 *  Never resolved independently. A source names the clock that PRODUCED the
 *  instant, so pairing a stored instant with a later writer's source relabels a
 *  real observation with a clock that did not make it — and because `unknown`
 *  DISABLES the floor while an absent source PASSES it, that relabelling flips
 *  a verifiable session to unverifiable (#3516 §B review: reproduced PASSED ->
 *  DISABLED).
 *
 *  Precedence is first-writer-wins on the INSTANT (matching the lane's monotone
 *  rule and the server's `coalesce`), with the source taken from the SAME
 *  writer. Falsiness is not absence: `0.0` is the epoch and is a real value. */
function stampPair(
  prior: SpoolMeta | undefined,
  snapshot: { clientCapturedAt?: number; clientCapturedAtSource?: string },
): [number | undefined, string | undefined] {
  const candidates: Array<[unknown, unknown]> = [
    [prior?.client_captured_at, prior?.client_captured_at_source],
    [snapshot.clientCapturedAt, snapshot.clientCapturedAtSource],
  ];
  for (const [instant, source] of candidates) {
    const at = finiteInstant(instant);
    if (at === undefined) continue;
    // piece 12 row 1: the Pi recorder always has a clock and claims NO source,
    // and an absent source PASSES the floor. Absent stays absent — never filled
    // in from the other writer.
    if (source === undefined || source === null) return [at, undefined];
    // An unrecognised token becomes `unknown`, which DISABLES the floor:
    // dropping it instead would leave the source absent, PASSING a session
    // whose clock we demonstrably cannot name.
    return [
      at,
      typeof source === "string" && CLIENT_CAPTURED_AT_SOURCES.has(source)
        ? source
        : "unknown",
    ];
  }
  return [undefined, undefined];
}

/** The server cap on the client-claimed attribution fields
 *  (`hosted_api.SessionRequest`): `machine_id` ≤256, `model` ≤128. Beyond
 *  either is a 422, which `flushSpool` classifies PERMANENT → the entry goes
 *  terminal. The WRITER bounds both (`deriveMachineId` emits 64 hex chars;
 *  `modelLabel` slices to 128), but the DRAIN forwards the on-disk meta — so a
 *  hand-edited / foreign / corrupt `meta.json` (the PR's own threat model) could
 *  put an over-long field on the wire and strand the capture, the same class the
 *  harness normalisation closed (#8531 review). TOTAL read: a non-string is
 *  OMITTED, and control chars / surrounding whitespace are stripped exactly as
 *  the Python leg's `sanitize_attribution_field` does, so the two legs agree. */
export function boundAttribution(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const clean = value.replace(/[\x00-\x1f]/g, "").trim();
  return clean ? clean.slice(0, maxLength) : undefined;
}

/** The wire's session id: a NON-BLANK string, or `undefined` (the server mints).
 *
 *  ⛔ The twin of the Python leg's `capture_spool._normalise_session_id`
 *  (#9599), and it is load-bearing, not tidying. A present but BLANK id
 *  (`"   "`, `"\t"`) is truthy, so every `?? ""`/`||` default upstream keeps
 *  it; the server now answers `POST /v1/sessions` with **422**, and
 *  `classifyFailure` calls a 422 PERMANENT — so an un-normalised blank would
 *  strand the capture in the spool FOREVER instead of letting the server mint
 *  an id. Sending `undefined` (the key is dropped by JSON.stringify) is the
 *  same "name no session, let the server mint" contract the Python spool
 *  wires, which is what the two legs must agree on.
 *
 *  A padded-but-REAL id (`"  s1  "`) passes through unchanged: it is a
 *  legitimate identity (#9599's recorded position), and trimming here would
 *  silently file the capture under a DIFFERENT session than the client named.
 *
 *  ⚠️ `String.prototype.trim` and Python's `str.strip` do NOT agree on the
 *  whitespace class: `trim` misses `\u001c-\u001f` and `\u0085`, which Python
 *  treats as blank, and it ADDS `\uFEFF` (a BOM), which Python does not. Both
 *  directions are wrong for this wire: the first would put a blank on the wire
 *  the server refuses PERMANENTLY, the second would silently replace a
 *  client-named id with a minted one. So the class is spelled out rather than
 *  borrowed from `trim` — `[^\S\uFEFF]` is "whitespace, but not the BOM", plus
 *  the two ranges Python has and JS `\s` lacks. The twin has to be the SAME
 *  rule, not a similar one, and both halves are pinned in the test.
 */
const BLANK_SESSION_ID = /^(?:[^\S\uFEFF]|[\u001c-\u001f\u0085])*$/;

export function normaliseSessionId(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  return BLANK_SESSION_ID.test(value) ? undefined : value;
}

/** A STABLE local session id for a session that arrived with none (#9599).
 *
 *  The exact twin of Python's `__main__._local_session_id` (#3963): anchored to
 *  the resolved transcript PATH — never its content — so every per-turn
 *  snapshot of one conversation addresses ONE spool entry, with the stem kept
 *  readable and the path digest disambiguating two files with one basename.
 *
 *  ⛔ It has to be the SAME derivation on both legs: they share one spool
 *  directory, so a different spelling would split one session across two
 *  entries (or, worse, collide two sessions onto one).
 *
 *  Returns `undefined` when there is no path to anchor to — the caller then
 *  treats the session as unnamed and declines to spool, which is what an
 *  absent id already did.
 */
/** Python's `Path(p).resolve()` — the LONGEST EXISTING PREFIX, then the rest.
 *
 *  ⛔ Node's `realpathSync` is not a drop-in: it throws ENOENT for a missing
 *  LEAF, so a fallback to the lexical `resolve()` would key a session on a
 *  different string than Python whenever the leaf does not exist yet (a hook
 *  spooling before its transcript is created) AND the path crosses a symlink —
 *  one session, two entries, once the file appears. `resolve(strict=False)`
 *  never raises for that: it resolves what exists and appends the remainder, so
 *  the same walk is done here.
 */
function pythonResolve(file: string): string {
  const whole = resolvePath(file);
  const remaining: string[] = [];
  let head = whole;
  for (;;) {
    try {
      const real = realpathSync(head);
      return remaining.length ? join(real, ...remaining.reverse()) : real;
    } catch {
      const parent = dirname(head);
      if (parent === head) return whole; // filesystem root: nothing resolved
      remaining.push(basename(head));
      head = parent;
    }
  }
}

export function localSessionId(sessionFile: unknown): string | undefined {
  if (typeof sessionFile !== "string" || !sessionFile) return undefined;
  // ⛔ The digest anchors on the REAL path, and the stem on the ORIGINAL one —
  // exactly what Python's `Path(p).resolve()` + `Path(p).stem` do. Getting
  // either wrong splits one session across two spool entries on the very
  // platforms that matter: a lexical anchor differs from Python's realpath on
  // macOS (`/tmp` -> `/private/tmp`) and anywhere a harness reaches its
  // transcript through a symlinked directory.
  const anchor = pythonResolve(sessionFile);
  const digest = createHash("sha256").update(anchor, "utf8").digest("hex").slice(0, 12);
  return `${pythonStem(sessionFile).slice(0, 240)}-${digest}`;
}

/** `pathlib.Path.stem` for the trailing-dot case Node's `extname` gets wrong.
 *
 *  `Path('a.jsonl.').stem` is `'a.jsonl.'` — Python treats a TRAILING dot as no
 *  suffix — while `basename(x, extname(x))` strips it, because Node's `extname`
 *  returns `'.'` for that name. And `Path('.gitignore').stem` is `.gitignore`,
 *  so a leading dot is not a suffix either.
 */
function pythonStem(file: string): string {
  const base = basename(file);
  const dot = base.lastIndexOf(".");
  if (dot <= 0 || dot === base.length - 1) return base;
  return base.slice(0, dot);
}

/** The POST /v1/sessions payload — the same shape `tortoise session capture` sends. */
export function buildCapturePayload(args: {
  sessionId: string;
  turns: Turn[];
  source: string;
  machineId: string;
  model?: string;
  /**
   * #3516 §B: the lane of the SPOOL ENTRY being drained — never a constant.
   * The lane describes the entry's PRODUCER, not the process doing the
   * draining: `flushSpool` drains entries written by other legs, and a
   * hardcoded 'hook' here would stamp a lane on a lane-less backfill/import
   * entry — defeating falsifiability in the false-POSITIVE direction. The
   * in-process hook passes 'hook' when it SPOOLS (see `spoolSnapshot`).
   */
  captureLane?: string;
  /** #3516 §B / #3515 piece 12: the client's OWN capture instant (unix seconds)
   *  and the clock it came from, for the timestamp floor. Omitted = absent, and
   *  a drain must forward exactly what the ENTRY carries — inventing an instant
   *  on a lane-less pre-#3516 entry would make the floor pass on nothing. */
  clientCapturedAt?: number;
  clientCapturedAtSource?: string;
}): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    harness: HARNESS,
    // #9599: NORMALISED at the WIRE, like the lane/stamp/machine_id below — a
    // blank id from the shared spool directory would otherwise be a permanent
    // 422 (classified PERMANENT, which strands the capture). Omitted rather
    // than `null`, matching how `machine_id`/`capture_lane` signal absence.
    // See `normaliseSessionId`.
    session_id: normaliseSessionId(args.sessionId),
    source: args.source,
    conversation: args.turns,
    // #8531 review: capped at the WIRE (`machine_id` ≤256, `model` ≤128 are
    // server bounds; exceeding either is a PERMANENT 422 that strands the
    // entry). Both reads are TOTAL and delegate to the writers' own rule, so the
    // writer and the wire cannot disagree.
    machine_id: boundAttribution(args.machineId, 256),
  };
  // #7708: NORMALISED at the WIRE, not only at the writer. The drain forwards
  // whatever is on disk, and the two legs share one directory — so a meta the
  // OTHER leg (or a hand-edit) left with an unrecognised token would otherwise
  // be posted verbatim, and the server's refusal is a 422. A stored falsy lane
  // normalises to the same `undefined` the skip clause and the CAS use, so all
  // three agree.
  const drainLane = normaliseLane(args.captureLane);
  if (drainLane) payload.capture_lane = drainLane;
  const postedAt = finiteInstant(args.clientCapturedAt);
  if (postedAt !== undefined) {
    payload.client_captured_at = postedAt;
    const src = args.clientCapturedAtSource;
    if (src !== undefined && src !== null) {
      payload.client_captured_at_source =
        typeof src === "string" && CLIENT_CAPTURED_AT_SOURCES.has(src)
          ? src
          : "unknown";
    }
  }
  const wireModel = boundAttribution(args.model, 128);
  if (wireModel) payload.model = wireModel;
  return payload;
}

// ── Hosted POSTs ──────────────────────────────────────────────────────────

async function post(
  cfg: CaptureConfig,
  path: string,
  body: Record<string, unknown>,
  fetchImpl: FetchLike,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<PostResult> {
  if (!cfg.apiKey) return { ok: false, detail: "no TORTOISE_API_KEY configured" };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(`${cfg.apiUrl}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${cfg.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (res.ok) return { ok: true, status: res.status };
    let detail = `HTTP ${res.status}`;
    try {
      const parsed = (await res.json()) as { detail?: unknown };
      if (parsed && parsed.detail) detail += ` — ${JSON.stringify(parsed.detail)}`;
    } catch {
      // non-JSON error body — the status is enough
    }
    return { ok: false, status: res.status, detail };
  } catch (err) {
    const reason = err instanceof Error && err.name === "AbortError"
      ? `timed out after ${timeoutMs / 1000}s`
      : err instanceof Error
        ? err.message
        : String(err);
    return { ok: false, detail: reason };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Install-probe beacon — harness + timestamp ONLY, zero conversation content.
 * Budgeted at PROBE_TIMEOUT_MS: it is best-effort install telemetry, and Pi
 * awaits `session_start`, so it must never hold up startup.
 */
export function postInstallProbe(
  cfg: CaptureConfig,
  fetchImpl: FetchLike = fetch as unknown as FetchLike,
): Promise<PostResult> {
  return post(cfg, "/v1/sessions/install-probe", { harness: HARNESS }, fetchImpl, PROBE_TIMEOUT_MS);
}

/** File one session — the server upserts on `session_id`. */
export function postCapture(
  cfg: CaptureConfig,
  payload: Record<string, unknown>,
  fetchImpl: FetchLike = fetch as unknown as FetchLike,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<PostResult> {
  return post(cfg, "/v1/sessions", payload, fetchImpl, timeoutMs);
}

// ── Durable capture spool (#3963) ─────────────────────────────────────────
//
// The defect this closes: capture happened ONLY on `session_shutdown`, so an
// interrupted / killed / laptop-closed session filed NOTHING — invisibly. The
// transcript is already durable on disk; what is lost is the FILING.
//
// Cadence (see ~/.pi/agent/state/research-capture-cadence-precedent.md):
//   * cheap capture FREQUENTLY — each `turn_end` appends the new turn(s) to a
//     local write-ahead log. The append is O(new turns), NOT O(whole session).
//     (The snapshot itself is the harness's own O(conversation) read.)
//   * costly extraction DEFERRED — the network POST (which triggers server-side
//     LLM extraction) is attempted only at an opportunity AFTER the session is
//     no longer live: `session_start` (a later session replays an interrupted
//     one), `agent_end` for a DIFFERENT session, or `session_shutdown` as the
//     final flush.
//
// Deliberately NOT "every N turns": the field converges on per-turn capture
// with deferred extraction, and no product ships a product-defined N-turns
// cadence — an N is a tunable inside a named pattern, not the pattern itself
// (research §c.2). There is no N constant here.
//
// Bounds are COUNT and BYTES — never time. #3870 (owner) ruled that the local
// capture spool is type-2 user data kept "until the user deletes it", and that
// a TTL / max-age / "prune after N days" CONTRADICTS that ruling. A count or
// byte bound is a size bound, not a clock — and it is an ADMISSION bound, not an
// eviction trigger: only a FILED entry (whose content is already on the wire) or
// a quarantined `.orphan` (unreachable to every surface) may ever be reclaimed.
// A spool at a ceiling with nothing reclaimable is SATURATED: it REFUSES the
// write, records the refusal, keeps the backlog, and reports
// `capture: SATURATED`. The oldest undelivered entry is never reaped to make
// room (capture-loss, 2026-11-01; supersedes #6388).
//
// Idempotency: `session_id` remains the SERVER's upsert key. On top of it the
// client derives a content-addressed capture key
// `sha256(session_id \0 sha256(turns))` — stable across replays, distinct when
// the conversation actually changed. A snapshot whose capture key was already
// filed short-circuits before the network, so replaying a spool twice files
// ONE session and issues ONE POST.

export const SPOOL_DIR = join(homedir(), ".tortoise", "capture-spool");
/** The store-sync backstop's cursor store (whose dead-letter substrate is the
 *  count the health line reports). `tortoise/store_sync.py::default_cursor_dir`
 *  is the same path — one directory, two readers. */
export const STORE_SYNC_DIR = join(homedir(), ".tortoise", "store-sync");
/**
 * Spool ceilings. Count AND bytes (the issue's contract). These are NOT a TTL —
 * see the #3870 note above.
 *
 * `SPOOL_MAX_ENTRY_BYTES` must exceed the SERVER's own legal maximum, or a
 * legal capture is discarded as oversized: the handler accepts `MAX_TURNS`
 * (500) turns of up to `TURN_MAX_CHARS` characters — the client clip reserves
 * the #4897 truncation marker inside that window — and non-ASCII text
 * is up to 4 UTF-8 bytes per character → ~10 MB of JSON. 16 MiB leaves room for
 * the envelope. (A 4 MiB ceiling silently discarded legal CJK sessions.)
 */
// ⛔ The COUNT ceiling is an ADMISSION bound, not an eviction trigger: it may
// only ever evict a FILED entry (see `pruneSpool`). Measured on pi-cell-3,
// 2026-11-01: 1000 spool entries = 5,463,464 bytes (5.4 KiB each), i.e. the
// 1000-entry cap bound at ~5 MiB while the BYTE ceiling below allows 256 MiB —
// so the count cap provided no disk protection and was the only thing evicting
// UNDELIVERED captures (80 of them, `discarded.jsonl`). 20000 x 5.4 KiB ~= 108
// MiB, still under the byte ceiling, which makes the BYTES the effective bound
// and gives an indefinite keyless backlog a stated retention window: at the
// measured fleet rate (~333 sessions/day) 20000 entries ~= 60 days.
export const SPOOL_MAX_ENTRIES = 20000;
export const SPOOL_MAX_TOTAL_BYTES = 256 * 1024 * 1024;
export const SPOOL_MAX_ENTRY_BYTES = 16 * 1024 * 1024;
/** Exponential backoff for TRANSIENT failures only (network / 5xx / retryable 4xx). */
export const RETRY_BASE_MS = 30_000;
export const RETRY_MAX_MS = 6 * 60 * 60 * 1000;
/** Mirrors the Python leg's `_MAX_ATTEMPTS`: the backoff saturates long before. */
export const MAX_ATTEMPTS = 64;

/** Discard reasons — every one is written to `discarded.jsonl` (observable). */
/** An entry that can never be filed: its bytes exceed `maxEntryBytes`, so no
 *  attempt could ever succeed. RECORDED and KEPT when the entry already exists on
 *  disk (marked `terminal_failure`, never retried) — NEVER unlinked
 *  (capture-loss residual 5). It used to be the ONE branch that deleted a
 *  PREVIOUSLY SPOOLED capture: a 1 KB conversation was destroyed by one
 *  oversized turn, below every other ceiling. A fresh snapshot that is oversized
 *  was never on disk, so there the record alone is the whole story. */
export const DISCARD_ENTRY_TOO_LARGE = "entry_too_large";
/** A WRITE that would take a LEGAL, fileable entry past `maxEntryBytes`.
 *  Nothing is removed and nothing is abandoned: the stored entry (a PREFIX of
 *  this snapshot, under the bound) is left untouched and filable, and the next
 *  admitted write re-offers the refused turns. This is the arm that replaced a
 *  silent abandonment — marking the entry `terminal_failure` here meant a legal
 *  capture sat on disk that no drain would ever POST, no health line counted,
 *  and no instrument reported (capture-loss, #10479 review). */
export const DISCARD_ENTRY_GROWTH_REFUSED = "entry_growth_refused";
export const DISCARD_COUNT_EXCEEDED = "spool_count_exceeded";
export const DISCARD_BYTES_EXCEEDED = "spool_total_bytes_exceeded";
export const DISCARD_TRANSCRIPT_EMPTY = "transcript_empty";
/** The turn log EXISTS with bytes but yields no parseable turn — a torn UTF-8
 *  append, a non-UTF-8 tail, or a path that is not a readable file. The entry
 *  is RECORDED and KEPT (marked terminal), NEVER unlinked (#7708 follow-up): a
 *  read/decode failure must not delete the only copy of a conversation. */
export const DISCARD_TRANSCRIPT_TORN = "transcript_torn";
export const DISCARD_CORRUPT = "corrupt_entry";
/** A filesystem error while writing the spool — recorded, never silent. */
export const DISCARD_WRITE_FAILED = "spool_write_failed";
/** The spool's own `entries/` listing cannot be read — every capture is invisible. */
export const DISCARD_SPOOL_UNREADABLE = "spool_unreadable";
/** The spool is at a ceiling with NOTHING evictable, so a NEW session is
 *  REFUSED rather than deleting an undelivered one (capture-loss). A refusal is
 *  a DELAY, not a loss: the harness's own session log still holds the
 *  conversation, and a later drain can file it. A cap eviction is a LOSS — the
 *  turn log is unlinked (`_discard_entry` / `discardEntry`) and no surface can
 *  deliver it again. */
export const DISCARD_SPOOL_SATURATED = "spool_saturated";
/** #7708: a PERMANENT HTTP refusal. The entry is RECORDED and KEPT (the turn
 *  log is the only copy), and marked `terminal_failure` so the drain does not
 *  retry it — terminal, never destructive. */
export const DISCARD_HTTP_REJECTED = "http_rejected";
/** The refusal dedupe stamp, in the spool ROOT beside `status.json` (the
 *  Python leg's `CAPTURE_REFUSAL_STAMP_FILE` — one spool, two legs, so the file
 *  NAME is part of the contract). */
export const CAPTURE_REFUSAL_STAMP_FILE = ".refusal-stamp.json";

export interface SpoolMeta {
  version: 1;
  session_id: string;
  harness: string;
  source: string;
  machine_id: string;
  model?: string;
  /** #3516 §B: the PRODUCER lane ('hook'). Set-only-when-present, carried
   *  forward. The Python leg forwards it to `/v1/sessions` verbatim, so an
   *  entry spooled by the Pi hook must carry it — or a later drain files the
   *  capture lane-less and a WORKING hook reads as not-live (#3515 piece 8). */
  capture_lane?: string;
  /** #3516 §B / #3515 piece 12: the CLIENT capture instant and its clock. The
   *  floor compares this against the install time, so it must survive the spool
   *  and reach the wire; the two legs share this directory, so both must carry
   *  the keys or one leg drains the other's entry without them. */
  client_captured_at?: number;
  client_captured_at_source?: string;
  created_at: string;
  updated_at: string;
  turns_count: number;
  /** sha256 of the canonical turns — the dedup signal + the capture key's source. */
  content_digest: string;
  /** Stable client-generated idempotency key: sha256(session_id \0 content_digest). */
  capture_key: string;
  /** Consecutive TRANSIENT upload failures (a permanent refusal marks the
   *  entry terminal instead of discarding it — #7708). */
  attempts: number;
  /** Epoch MILLISECONDS before which a retry must not be attempted. 0 = ready.
   *  Milliseconds in BOTH legs (they share this directory) — a seconds value
   *  written by one leg reads as "in backoff until the year 57000" to the other. */
  next_attempt_at_ms: number;
  /** capture_key of the last 2xx upload — a replay of identical content is a no-op. */
  filed_key?: string;
  /** #3516 §B: the LANE the 2xx actually carried. `filed_key` is content-derived,
   *  so on its own it says the content was delivered — not that the lane was. An
   *  entry filed lane-less and then upgraded must be re-posted, or the lane is
   *  stranded and a working hook reads as not-live. Entries filed before this
   *  field existed have neither lane nor `filed_lane`, so they compare equal. */
  filed_lane?: string;
  /** #3516 §B: the STAMP the 2xx actually carried. Same reasoning as
   *  `filed_lane` — the stamp is metadata, not content, so a content-only
   *  marker would strand a stamp that appeared after the filing. Omitted when
   *  the wire carried none, so entries filed before this field existed compare
   *  equal. */
  filed_stamp?: number;
  filed_at?: string;
  /** #7708: set when a PERMANENT HTTP refusal was recorded. The entry is KEPT
   *  but `flushSpool` skips it — terminal, never destructive. Cleared when a
   *  later snapshot REBUILDS the meta (genuinely new content → a fresh,
   *  backoff-gated attempt); a byte-identical re-snapshot returns before the
   *  meta is rebuilt, so the marker survives and the entry is not re-POSTed. */
  terminal_failure?: { reason: string; detail?: string; at: string };
}

export interface DiscardRecord {
  at: string;
  session_id: string;
  capture_key?: string;
  reason: string;
  detail?: string;
  /** Whether the capture SURVIVED the record (kept on disk, still filable).
   *  Explicit because a reason can be shared by arms with opposite outcomes —
   *  `entry_too_large` keeps a previously spooled entry and never existed for a
   *  fresh oversized snapshot. A record that carries no flag is classified by
   *  its reason (`RETAINED_DISCARD_REASONS`, Python leg), so a line written by
   *  an older leg still classifies. */
  retained?: boolean;
}

export interface SpoolSnapshot {
  sessionId: string;
  turns: Turn[];
  source: string;
  machineId: string;
  model?: string;
  /** #3516 §B: the lane this snapshot's PRODUCER claims ('hook' for the
   *  in-process hook). Omitted/undefined = no lane — never fabricated. */
  captureLane?: string;
  clientCapturedAt?: number;
  clientCapturedAtSource?: string;
}

export interface SpoolBounds {
  maxEntries: number;
  maxTotalBytes: number;
  maxEntryBytes: number;
}

export const DEFAULT_BOUNDS: SpoolBounds = {
  maxEntries: SPOOL_MAX_ENTRIES,
  maxTotalBytes: SPOOL_MAX_TOTAL_BYTES,
  maxEntryBytes: SPOOL_MAX_ENTRY_BYTES,
};

export interface FlushSummary {
  attempted: number;
  filed: number;
  /** Transient failures left in the spool for a later opportunity. */
  deferred: number;
  /** Entries not attempted: already filed, or still inside a backoff window. */
  skipped: number;
  /**
   * Entries INSIDE their backoff window — deferred by a previous refusal, not
   * by anything wrong now. Counted separately from `skipped` because since
   * #4714 moved 402 from "discard" to "defer", a quota-blocked spool reaches a
   * steady state where EVERY flush skips and nothing is attempted: without this
   * counter the flush logs nothing at all and captures sit unfiled invisibly.
   */
  heldByBackoff: number;
  /**
   * Entries deliberately NOT attempted because they are the session that is
   * LIVE right now (`excludeSessionId`). Counted separately from `skipped`:
   * "already filed" and "held back for its own final flush" are different
   * states, and a silent hold-back reads as a drop in the log.
   */
  heldBack: number;
  /** #7708: entries marked TERMINAL by a previous permanent refusal — KEPT on
   *  disk (never unlinked) but never retried. Counted separately so a spool
   *  whose entries are all terminal still reports on every flush instead of
   *  reaching the silent steady state `heldByBackoff` was added to close. */
  terminal: number;
  /** #2100: entries that COULD be attempted but the pass never reached, because
   *  it stopped at its attempt cap, its time budget, or a quota refusal. They
   *  stay in the spool, unfiled and un-dropped — this counter is what proves a
   *  bounded pass lost nothing, and what tells the caller to continue. */
  remaining: number;
  /** #2100: why the pass stopped early. Absent on a pass that reached the end. */
  stopped?: "attempts" | "budget" | "quota";
  /** #2100: the quota refusal this pass hit, if any (402 / `quota_exceeded`).
   *  A quota-blocked box is a NAMED state (`capture: BLOCKED`), not a growing
   *  silent backlog of per-item deferrals. */
  quota?: QuotaRefusal;
  discarded: DiscardRecord[];
}

/** The server's quota refusal, as the client can name it (#2100). */
export interface QuotaRefusal {
  status: number;
  /** The server's machine-readable category when its body carries one
   *  (`quota_exceeded` | `cohort_cost_cap`). */
  code?: string;
  detail?: string;
}

/** The per-box capture state a health line reports (#2100). */
export type CaptureState =
  | "ok"
  | "draining"
  | "blocked"
  | "inactive"
  | "unreadable"
  /** At a ceiling with nothing evictable: every NEW capture is refused. The
   *  sixth state exists because the other five could not say it (capture-loss). */
  | "saturated";

/** What `status.json` holds. Advisory: it records the LAST refusal this leg saw;
 *  the health line always recomputes `queued` from the spool and treats an empty
 *  spool as `ok` regardless, so a stale marker can never claim a block that no
 *  longer exists. */
export interface CaptureStatus {
  version: 1;
  state: CaptureState;
  /** Short reason token for the operator (`quota_exceeded`, `no_api_key`…). */
  reason?: string;
  /** The HTTP status behind a `blocked` state, when there is one. */
  status?: number;
  detail?: string;
  /** Entries queued when the state was recorded (advisory, never the authority). */
  queued?: number;
  updated_at: string;
}

/** sha256 hex of the canonical turns (ordered `{role, content}` objects). */
export function contentDigest(turns: Turn[]): string {
  return createHash("sha256").update(JSON.stringify(turns), "utf8").digest("hex");
}

/** The client-generated idempotency key: stable per (session, content). */
export function captureKey(sessionId: string, turns: Turn[]): string {
  return createHash("sha256")
    .update(`${sessionId}\u0000${contentDigest(turns)}`, "utf8")
    .digest("hex");
}

/** Filesystem-safe, collision-resistant stem for a session id. */
export function entryKey(sessionId: string): string {
  return createHash("sha256").update(sessionId, "utf8").digest("hex").slice(0, 32);
}

function entriesDir(dir: string): string {
  return join(dir, "entries");
}
function metaPathFor(dir: string, sessionId: string): string {
  return join(entriesDir(dir), `${entryKey(sessionId)}.meta.json`);
}
function logPathFor(dir: string, sessionId: string): string {
  return join(entriesDir(dir), `${entryKey(sessionId)}.turns.jsonl`);
}
function discardPath(dir: string): string {
  return join(dir, "discarded.jsonl");
}

function atomicWrite(path: string, text: string): void {
  const tmp = `${path}.tmp-${process.pid}-${Date.now()}`;
  // 0600: the spool holds full conversation text (the rest of ~/.tortoise uses
  // the same private-file convention for credentials).
  //
  // fsync before the rename: the whole point of the spool is surviving a kill or
  // a power loss, and a rename without an fsync can leave a zero-length file.
  const fd = openSync(tmp, "w", 0o600);
  try {
    writeFileSync(fd, text, { encoding: "utf8" });
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(tmp, path);
}

/** Remove our own abandoned `*.tmp-*` scratch files (see the Python twin).
 *
 * A killed process leaves a temp that matches neither the meta glob nor
 * `spoolEntryBytes`, so it is never counted or pruned and could grow the spool
 * past its byte ceiling forever. These are OUR scratch artifacts, not user data
 * (the #3870 no-TTL ruling covers captures), so an mtime sweep is safe.
 */
function sweepStaleTempFiles(dir: string, maxAgeMs = 300_000): void {
  const now = Date.now();
  for (const directory of [dir, entriesDir(dir)]) {
    let names: string[] = [];
    try {
      names = readdirSync(directory);
    } catch {
      continue;
    }
    for (const name of names) {
      if (!name.includes(".tmp-")) continue;
      const path = join(directory, name);
      try {
        if (now - statSync(path).mtimeMs > maxAgeMs) unlinkSync(path);
      } catch {
        // best effort
      }
    }
  }
}

/** True when the turn log ends at a record boundary. A process killed mid-append
 * leaves a partial line; appending to it FUSES the new turn into the garbage
 * tail, the tail is skipped on read-back, and the lost turn is still covered by
 * the meta digest — a silent, unrecoverable loss. The caller rewrites instead.
 */
function logHasCompleteRecords(path: string): boolean {
  try {
    const buf = readFileSync(path);
    return buf.length === 0 || buf[buf.length - 1] === 0x0a;
  } catch {
    return false;
  }
}

function serialiseTurn(turn: Turn): string {
  return `${JSON.stringify({ role: turn.role, content: turn.content })}\n`;
}

/** Read the write-ahead turn log. Unparseable lines are skipped (best effort).
 *
 * Any role is returned: the store IS the record and the meta's `turns_count` /
 * `content_digest` are computed over it, so filtering a role here would post
 * fewer turns than the digest covers while still stamping the entry FILED.
 * Non-conversational roles are excluded at CAPTURE time (`extractTurns`).
 */
export function readSpoolTurns(dir: string, sessionId: string): Turn[] {
  const path = logPathFor(dir, sessionId);
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    // Absent, or unreadable (EACCES, a DIRECTORY in its place, ELOOP). Returning
    // [] lets `flushSpool` record a `transcript_empty` discard. Throwing here
    // aborted the WHOLE drain loop — one corrupt entry permanently wedged every
    // other session's filing, with no ledger line (the Python twin guards this).
    return [];
  }
  const turns: Turn[] = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    try {
      const parsed = JSON.parse(line) as { role?: unknown; content?: unknown };
      // #8531 review: `role.length > 0` is the writers' contract — neither leg
      // ever emits an empty role (`extractTurns` requires user/assistant;
      // Python filters to the same pair) — and Python now applies the SAME
      // non-empty predicate (`capture_spool.read_spool_turns`). Before that the
      // legs disagreed about the same bytes: TS skipped `{"role":"",...}` and
      // routed the log to `transcript_torn`, Python filed it as a turn.
      if (
        typeof parsed.role === "string" &&
        parsed.role.length > 0 &&
        typeof parsed.content === "string"
      ) {
        turns.push({ role: parsed.role, content: parsed.content });
      }
    } catch {
      // corrupt line — skipped; a wholly unreadable entry is discarded upstream
    }
  }
  return turns;
}

/** All spool metas (unreadable metas are surfaced as `corrupt_entry` discards). */
export function listSpoolEntries(
  dir: string,
  opts: { repair?: boolean } = {},
): { metas: SpoolMeta[]; discards: DiscardRecord[] } {
  // `repair: false` reads the same files and SKIPS the corrupt ones: no ledger
  // line, no quarantine, no unlink. For a READER that must not mutate the spool
  // it describes — the health line (`spoolHealthLine` → `spoolSaturated`), the
  // same contract `countPendingEntries` states. The repair belongs to the drain's
  // own scan. An unreadable `entries/` is still RECORDED even when `repair` is
  // false: that is not a change to an entry, and a spool that cannot be listed
  // must never read as an empty one. Mirrors `capture_spool.list_spool_metas`.
  const repair = opts.repair !== false;
  const metas: SpoolMeta[] = [];
  const discards: DiscardRecord[] = [];
  const root = entriesDir(dir);
  let names: string[];
  try {
    names = readdirSync(root);
  } catch (err) {
    // ENOENT is a fresh install — absent is not a failure.
    //
    // Deliberately NOT an `existsSync` pre-check: `existsSync` returns FALSE for
    // an unreadable directory (EACCES), so it reported "nothing to file" for a
    // spool it could not read, with no ledger line — the same silent-empty
    // failure the guard below exists to close. Any other error is recorded.
    if ((err as NodeJS.ErrnoException | undefined)?.code === "ENOENT") {
      return { metas, discards };
    }
    discards.push(
      recordDiscard(dir, {
        session_id: "spool",
        reason: DISCARD_SPOOL_UNREADABLE,
        detail: `cannot list ${root}: ${err instanceof Error ? err.message : String(err)}`,
      }),
    );
    return { metas, discards };
  }
  for (const name of names) {
    if (!name.endsWith(".meta.json")) continue;
    const path = join(root, name);
    let meta: SpoolMeta | undefined;
    try {
      meta = JSON.parse(readFileSync(path, "utf8")) as SpoolMeta;
    } catch {
      meta = undefined;
    }
    if (!meta || typeof meta.session_id !== "string" || !meta.session_id) {
      if (!repair) continue;
      const stem = name.replace(/\.meta\.json$/, "");
      discards.push(
        recordDiscard(dir, {
          session_id: `unreadable:${stem}`,
          reason: DISCARD_CORRUPT,
          detail: meta ? `meta has no usable session_id: ${path}` : `meta is not valid JSON: ${path}`,
        }),
      );
      // Remove the corrupt META, but QUARANTINE the turn log, not delete it
      // (#7708): the transcript has nothing to do with the corruption and is
      // the only copy, so a bad `meta.json` must not become data loss. An
      // absent/empty log is removed.
      quarantineLog(join(root, `${stem}.turns.jsonl`));
      try {
        unlinkSync(path);
      } catch {
        // nothing more we can do — the record above is the observable signal
      }
      continue;
    }
    metas.push(meta);
  }
  return { metas, discards };
}

/** Size on disk of one spool entry (meta + turn log). */
export function spoolEntryBytes(dir: string, sessionId: string): number {
  let total = 0;
  for (const path of [metaPathFor(dir, sessionId), logPathFor(dir, sessionId)]) {
    try {
      total += statSync(path).size;
    } catch {
      // absent half contributes 0
    }
  }
  return total;
}

/** Whether the turn log exists with bytes — the gate between the TWO
 *  empty-transcript outcomes (#7708). An ABSENT/zero-byte log is
 *  `transcript_empty` (removed — provably nothing to keep); a log with bytes is
 *  `transcript_torn` (recorded, marked terminal and KEPT). `statSync` does not
 *  need read permission, so a present-but-unreadable log counts as content. */
function turnLogHasContent(dir: string, sessionId: string): boolean {
  try {
    return statSync(logPathFor(dir, sessionId)).size > 0;
  } catch {
    return false;
  }
}

/** Move a turn log ASIDE instead of unlinking it — kept FOR FORENSICS.
 *  Used when the META is corrupt and the transcript (which the corruption never
 *  touched, and which is the only copy) must survive (#7708). An empty/absent
 *  log is removed. A numeric suffix keeps an earlier orphan from being
 *  clobbered. Best-effort: a rename failure leaves the log in place.
 *
 *  NOT re-fileable — the honesty this owes a reader who finds an `.orphan`: the
 *  entry key is `sha256(session_id)[:32]` and the corrupt meta (the only copy of
 *  the session id) is REMOVED, so no verb can rebuild the session id the server
 *  needs. The bytes are kept FOR FORENSICS, not for a retry. (The wording here
 *  once said "recoverable", which overstated what the name can deliver.) */
function quarantineLog(log: string): void {
  let size = 0;
  try {
    size = statSync(log).size;
  } catch {
    // absent — nothing to keep
  }
  if (size <= 0) {
    try {
      unlinkSync(log);
    } catch {
      // already gone
    }
    return;
  }
  let target = `${log}.orphan`;
  let suffix = 1;
  while (existsSync(target)) {
    suffix += 1;
    target = `${log}.orphan${suffix}`;
  }
  try {
    renameSync(log, target);
  } catch {
    // leave it in place — better than deleting it
  }
}

/** Quarantined turn logs in the entries dir, OLDEST first (#8531 review).
 *
 *  `quarantineLog` moves a corrupt-meta entry's transcript aside as
 *  `<key>.turns.jsonl.orphan[N]`. Nothing else reads that name, so without this
 *  accounting the bytes are invisible to `spoolEntryBytes` and to the meta scan:
 *  the spool could grow past `maxTotalBytes` without bound the moment a meta was
 *  quarantined — one full transcript per corrupt meta. A rename preserves mtime,
 *  so an orphan carries the age of the transcript it holds and oldest-first
 *  eviction is deterministic. */
function orphanPaths(dir: string): string[] {
  const root = entriesDir(dir);
  let names: string[];
  try {
    names = readdirSync(root);
  } catch {
    return [];
  }
  const found: { path: string; mtime: number; name: string }[] = [];
  for (const name of names) {
    if (!name.includes(".turns.jsonl.orphan")) continue;
    const path = join(root, name);
    try {
      const st = statSync(path);
      if (!st.isFile()) continue;
      found.push({ path, mtime: st.mtimeMs, name });
    } catch {
      continue;
    }
  }
  return found
    .sort((a, b) =>
      a.mtime !== b.mtime ? a.mtime - b.mtime : a.name < b.name ? -1 : a.name > b.name ? 1 : 0,
    )
    .map((entry) => entry.path);
}

/**
 * Record a discard — append to `discarded.jsonl` AND remove the entry files.
 * This is the "never a silent drop" contract: every removed/dropped entry has a
 * durable, greppable line naming the session and the reason.
 */
export function recordDiscard(
  dir: string,
  rec: {
    session_id: string;
    capture_key?: string;
    reason: string;
    detail?: string;
    at?: string;
    retained?: boolean;
  },
): DiscardRecord {
  const full: DiscardRecord = { at: rec.at ?? new Date().toISOString(), ...rec };
  try {
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    appendFileSync(discardPath(dir), `${JSON.stringify(full)}\n`, { encoding: "utf8", mode: 0o600 });
  } catch {
    // a full disk must not throw out of a capture path
  }
  return full;
}

function refusalStampPath(dir: string): string {
  return join(dir, CAPTURE_REFUSAL_STAMP_FILE);
}

/** The last stamped refusal, or `undefined` — a TOTAL read, never a throw. */
function readRefusalStamp(dir: string):
  { session_id?: string; reason?: string; entries_stamp?: number; record?: DiscardRecord } | undefined {
  try {
    const parsed = JSON.parse(readFileSync(refusalStampPath(dir), "utf8"));
    return parsed && typeof parsed === "object" ? parsed : undefined;
  } catch {
    return undefined;
  }
}

/** A cheap fingerprint of the spool state a refusal was measured against.
 *
 * `entries/`'s mtime moves on create, unlink and rename — exactly the
 * transitions that can change "is anything evictable?" — and does NOT move when
 * a refused session re-snapshots, because a refusal writes nothing at all. That
 * is what makes the stamp hold across a refused session's turns while still
 * letting the next refusal after a drain be recorded. */
function entriesDirStamp(dir: string): number {
  try {
    return statSync(entriesDir(dir)).mtimeMs;
  } catch {
    return 0;
  }
}

/** Record a REFUSAL once per (session, reason, spool state).
 *
 * A refusal is not a one-off event: a refused session re-snapshots on EVERY
 * `turn_end`, it has no `prior` to hang a dedupe marker on (nothing was ever
 * written for it), and an unconditional `recordDiscard` therefore appends one
 * line per turn for as long as the box stays saturated. `discarded.jsonl` lives
 * in the spool ROOT and is counted against no ceiling, so that is unbounded
 * growth in the very directory the "bounded disk" claim is about — the same
 * amplification class the `entry_too_large` branch already guards (#4714).
 *
 * The key is (session_id, reason, `entries/` stamp): the same session, refused
 * for the same reason, while the spool itself has not changed, is the SAME
 * event. One stamp stores the LAST refusal only, so it can suppress at most a
 * repeat of itself: a different session, a different reason, or any change in
 * the entry set is always recorded. The Python leg's `_record_refusal` is the
 * twin and keys identically, because the two legs share this one ledger.
 *
 * Returns the ledger line either way — the prior one when this is a suppressed
 * repeat — so a caller may report "the state" without inventing an event. */
export function recordRefusal(
  dir: string,
  rec: {
    session_id: string;
    capture_key?: string;
    reason: string;
    detail?: string;
    retained?: boolean;
  },
): DiscardRecord {
  const fingerprint = {
    session_id: rec.session_id,
    reason: rec.reason,
    entries_stamp: entriesDirStamp(dir),
  };
  const stamp = readRefusalStamp(dir);
  if (
    stamp &&
    Object.entries(fingerprint).every(([key, value]) =>
      stamp[key as keyof typeof fingerprint] === value,
    )
  ) {
    if (stamp.record && typeof stamp.record === "object") return stamp.record;
  }
  const full = recordDiscard(dir, rec);
  try {
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    atomicWrite(refusalStampPath(dir), `${JSON.stringify({ ...fingerprint, record: full })}\n`);
  } catch {
    // the stamp is an optimisation, never a precondition for recording
  }
  return full;
}

function removeEntryFiles(dir: string, sessionId: string): void {
  for (const path of [metaPathFor(dir, sessionId), logPathFor(dir, sessionId)]) {
    try {
      unlinkSync(path);
    } catch {
      // already gone
    }
  }
}

/**
 * Record a discard, then remove the entry files.
 *
 * `deps` is a testability seam in the same style as `CaptureDeps` / `fetchImpl`:
 * the ORDER of these two effects is the contract ("never a silent drop"), and
 * the only way to observe it in-process is to spy on both calls.
 */
export function discardEntry(
  dir: string,
  meta: SpoolMeta,
  reason: string,
  detail?: string,
  deps: {
    record: (dir: string, rec: Omit<DiscardRecord, "at"> & { at?: string }) => DiscardRecord;
    remove: (dir: string, sessionId: string) => void;
  } = { record: recordDiscard, remove: removeEntryFiles },
): DiscardRecord {
  // Record FIRST, then remove the files. The ledger line is the contract
  // ("never a silent drop"): unlinking first leaves a SIGKILL / ENOSPC window
  // in which an unfiled capture is deleted with NO record. A leftover entry
  // behind a record is at worst a duplicate line; a missing line is
  // unreconcilable.
  const record = deps.record(dir, {
    session_id: meta.session_id,
    capture_key: meta.capture_key,
    reason,
    detail,
  });
  deps.remove(dir, meta.session_id);
  return record;
}

/**
 * Record a TERMINAL HTTP refusal WITHOUT unlinking the entry (#7708).
 *
 * A permanent 4xx is a *filing* failure, not a *capture* failure: the turn log
 * on disk is still the only copy, and deleting it turns one refused payload
 * into permanent data loss. The ledger record is written FIRST — the same
 * "never a silent drop" ordering as `discardEntry` — then the entry is marked
 * `terminal_failure` and KEPT. `flushSpool` skips a terminal entry, so the drain
 * reaches a final state without the amplification an unmarked retry re-opens.
 *
 * `deps` is the same kind of testability seam as `discardEntry`'s.
 */
export function quarantineEntry(
  dir: string,
  meta: SpoolMeta,
  reason: string,
  detail: string,
  nowMs: number,
  deps: {
    record: (dir: string, rec: Omit<DiscardRecord, "at"> & { at?: string }) => DiscardRecord;
    read: (dir: string, sessionId: string) => SpoolMeta | undefined;
    write: (dir: string, meta: SpoolMeta) => void;
  } = { record: recordDiscard, read: readSpoolEntry, write: writeMeta },
): DiscardRecord {
  const record = deps.record(dir, {
    session_id: meta.session_id,
    capture_key: meta.capture_key,
    reason,
    detail,
  });
  const fresh = deps.read(dir, meta.session_id);
  // Guarded on the re-read: if a concurrent prune removed the entry, writing
  // the marker over the stale in-memory meta would resurrect an ORPHAN terminal
  // entry with no turn log. The ledger line is already recorded either way.
  if (fresh) {
    fresh.terminal_failure = { reason, detail, at: new Date(nowMs).toISOString() };
    // The marker is dropped when a later snapshot REBUILDS the meta, so advance
    // the window here too — otherwise the first flush after a content change
    // re-POSTs a still-refused payload at turn cadence rather than on the
    // backoff clock.
    fresh.attempts = clampAttempts(fresh.attempts) + 1;
    fresh.next_attempt_at_ms = nowMs + backoffDelay(fresh.attempts);
    deps.write(dir, fresh);
  }
  return record;
}

/**
 * Write (or extend) the spool entry for a session snapshot.
 *
 * Appends only the NEW turns when the incoming list extends the stored log
 * (the common case: the session grew by a turn). Falls back to a full rewrite
 * when history changed (a MAX_TURNS window shift, a branch, a compaction), so
 * the log can never silently diverge from the conversation.
 *
 * Returns the entry size and any discards triggered by the size bounds —
 * the caller reports them, they are never swallowed.
 */
export function writeSpoolEntry(
  dir: string,
  snapshot: SpoolSnapshot,
  bounds: SpoolBounds = DEFAULT_BOUNDS,
): { written: boolean; bytes: number; discards: DiscardRecord[] } {
  const discards: DiscardRecord[] = [];
  if (snapshot.turns.length === 0) {
    return { written: false, bytes: 0, discards };
  }
  try {
    mkdirSync(entriesDir(dir), { recursive: true, mode: 0o700 });
  } catch (err) {
    // A spool directory that cannot be created is a LOSS — record it.
    discards.push(
      recordDiscard(dir, {
        session_id: snapshot.sessionId,
        capture_key: captureKey(snapshot.sessionId, snapshot.turns),
        reason: DISCARD_WRITE_FAILED,
        detail: err instanceof Error ? err.message : String(err),
      }),
    );
    return { written: false, bytes: 0, discards };
  }
  const metaPath = metaPathFor(dir, snapshot.sessionId);
  const logPath = logPathFor(dir, snapshot.sessionId);

  let prior: SpoolMeta | undefined;
  try {
    prior = JSON.parse(readFileSync(metaPath, "utf8")) as SpoolMeta;
  } catch {
    prior = undefined;
  }
  const stored = prior ? readSpoolTurns(dir, snapshot.sessionId) : [];

  // #3516 §B: the entry's lane is set-only-when-present, FIRST-WRITER-WINS, and
  // carried forward — NOT `model`'s rule (that one is not carried forward).
  // `prior` wins when both are set, so a later snapshot can only FILL IN an
  // absent lane, never RELABEL one: a `store_sync` snapshot can never DOWNGRADE
  // a stored `hook` (the pinned "delivery lane is monotone" contract), which is
  // also the rule the SERVER applies via `sdk._write_session_and_turns`'
  // coalesce — the spool must not be the odd leg out, because its lane is what
  // gets delivered. The resolution is TRUTHY (`||`, not `??`), because BOTH
  // legs read and write this one directory and must state ONE rule. The input
  // that distinguishes them is a stored EMPTY lane with a truthy new one: `""
  // || "hook"` fills the lane in, while `??` keeps `""` and the spread below
  // then OMITS the key. (A stored empty lane is reachable only from a crafted
  // or corrupt meta — which is why it has its own test.)
  //
  // #7708: BOTH operands are NORMALISED BEFORE the comparison. Reading the raw
  // values would let a stored foreign lane ("bogus") count as "the entry has a
  // lane", so a valid snapshot lane would neither win the resolution nor set
  // `laneUpgrade` — and the byte-identical dedup early-return would then strand
  // the valid lane forever (the hook-reads-not-live false negative #3516
  // exists to remove). One normaliser, used everywhere.
  const priorLane = normaliseLane(prior?.capture_lane);
  const snapLane = normaliseLane(snapshot.captureLane);
  const lane = priorLane ?? snapLane;
  // #3516 §B / piece 12: the stamp and its clock resolve as a PAIR, so a later
  // writer can never relabel an earlier writer's instant with its own clock.
  // The store-sync backstop ships the SAME session after this leg, and a later,
  // weaker clock must not replace the hook's own observation.
  const [clientCapturedAt, clientCapturedAtSource] = stampPair(prior, snapshot);
  // A snapshot carrying a lane the entry has never had is an UPGRADE, not a
  // no-op. `sameContent` is content-addressed and the lane is NOT part of the
  // content, so without this bypass an entry first written lane-less (a
  // pre-#3516 spool, or an `sessions import` entry the hook then re-captured
  // byte-identically) would keep its lane-less meta forever — and a WORKING
  // hook would file as not-live, the false-negative this feature exists to
  // remove.
  const laneUpgrade = snapLane !== undefined && priorLane === undefined;
  // The SAME upgrade bypass for the stamp: it is metadata, not content, so a
  // byte-identical re-snapshot that newly carries one must not be deduped away
  // — the entry would stay timeless forever and the floor could never run.
  const stampUpgrade =
    finiteInstant(snapshot.clientCapturedAt) !== undefined &&
    finiteInstant(prior?.client_captured_at) === undefined;

  // Dedup: a snapshot that is byte-identical to what is stored already is a
  // no-op — no rewrite, no re-upload (incremental capture must not amplify
  // identical writes).
  const sameContent =
    prior !== undefined &&
    stored.length === snapshot.turns.length &&
    prior.content_digest === contentDigest(snapshot.turns) &&
    !laneUpgrade &&
    !stampUpgrade;
  if (sameContent) {
    return { written: false, bytes: spoolEntryBytes(dir, snapshot.sessionId), discards };
  }

  const extendsStored =
    stored.length > 0 &&
    // The log must end at a record boundary, or a torn append would fuse the
    // new turn into the garbage tail (see logHasCompleteRecords).
    logHasCompleteRecords(logPath) &&
    snapshot.turns.length > stored.length &&
    stored.every(
      (t, i) => t.role === snapshot.turns[i].role && t.content === snapshot.turns[i].content,
    );

  const logText = extendsStored
    ? snapshot.turns.slice(stored.length).map(serialiseTurn).join("")
    : snapshot.turns.map(serialiseTurn).join("");
  const appendedBytes = Buffer.byteLength(logText, "utf8");

  const now = new Date().toISOString();
  const meta: SpoolMeta = {
    version: 1,
    session_id: snapshot.sessionId,
    harness: HARNESS,
    source: snapshot.source,
    machine_id: snapshot.machineId,
    ...(snapshot.model ? { model: snapshot.model } : {}),
    ...(lane ? { capture_lane: lane } : {}),
    ...(clientCapturedAt !== undefined
      ? { client_captured_at: clientCapturedAt }
      : {}),
    ...(clientCapturedAt !== undefined && clientCapturedAtSource
      ? { client_captured_at_source: clientCapturedAtSource }
      : {}),
    created_at: prior?.created_at ?? now,
    updated_at: now,
    turns_count: snapshot.turns.length,
    content_digest: contentDigest(snapshot.turns),
    capture_key: captureKey(snapshot.sessionId, snapshot.turns),
    // The backoff belongs to the ENTRY, not to one snapshot. A session that
    // keeps growing writes a new meta on EVERY turn; resetting these here
    // re-armed the retry window each time, so a deferred 402 was re-POSTed at
    // turn cadence with no backoff at all (#4714) — the "capped cadence" this
    // module promises held only for a STATIC entry. A new turn is not a new
    // upload attempt, so carry them forward. The filing path resets them
    // (attempts=0) and a genuinely fresh entry starts at zero.
    attempts: clampAttempts(prior?.attempts),
    next_attempt_at_ms: carriedWindow(prior?.next_attempt_at_ms),
    // A lane UPGRADE also invalidates the filing marker: `filed_key` is
    // content-derived (the lane is not part of it), so an entry already filed
    // lane-less would otherwise keep its marker and be skipped by the drain
    // forever — the lane would never reach the wire. Mirrors the Python
    // writer's `and not lane_upgrade`.
    ...(prior?.filed_key &&
    prior.content_digest === contentDigest(snapshot.turns) &&
    !laneUpgrade &&
    // #3516 §B: the SAME invalidation for a stamp UPGRADE — the stamp is not
    // part of `filed_key` either, so an entry filed without one would keep its
    // marker and be skipped forever, and the stamp would never reach the wire.
    // Mirrors the Python writer's `and not stamp_upgrade`.
    !stampUpgrade
      ? { filed_key: prior.filed_key, filed_lane: prior.filed_lane,
          filed_stamp: prior.filed_stamp, filed_at: prior.filed_at }
      : {}),
  };
  const metaText = `${JSON.stringify(meta, null, 2)}\n`;
  // Size the entry as it will exist AFTER this write: the new meta, plus the
  // existing log when appending (or only the new log when rewriting).
  const logBytesAfter = extendsStored
    ? fileSize(logPath) + appendedBytes
    : appendedBytes;
  const entryBytes = Buffer.byteLength(metaText, "utf8") + logBytesAfter;

  // Per-entry bound: an entry that can never be filed (too large to be a legal
  // capture) is RECORDED with a reason rather than written and forgotten — and
  // NEVER unlinked when there is anything on disk to keep (residual 5).
  if (entryBytes > bounds.maxEntryBytes) {
    const detail = `entry is ${entryBytes} bytes > maxEntryBytes=${bounds.maxEntryBytes}`;
    // ⛔ THE BOUND IS ASKED TWICE, ABOUT TWO DIFFERENT THINGS. `entryBytes` is
    // what THIS WRITE would put on disk; what the drain would later POST is what
    // is ALREADY on disk. Marking the entry terminal abandons delivery forever
    // (`flushSpool` skips it, `countPendingEntries` excludes it), so it is only
    // correct when the STORED content is itself over the bound and no attempt
    // could ever file it. When the stored content is under the bound — the
    // reviewer's 1032-byte conversation, destroyed by one oversized turn — the
    // entry is legal and fileable, and all this write needs is to be REFUSED.
    // Mirrors the Python writer's `capture_spool.write_spool_entry`.
    if (prior) {
      const onDisk = spoolEntryBytes(dir, snapshot.sessionId);
      if (onDisk > bounds.maxEntryBytes) {
        // The stored entry can never be filed: RETAINED and marked terminal,
        // exactly like a permanent HTTP refusal (`quarantineEntry`, #7708). This
        // branch used to be the one place that deleted a PREVIOUSLY SPOOLED
        // capture: one oversized turn destroyed a small, perfectly fileable
        // transcript, below every other ceiling. A refusal is a delay the
        // operator can reverse (`--retry-terminal`), an unlink is a loss nobody
        // can.
        const marker = terminalMarker(prior);
        if (marker !== undefined && marker.reason === DISCARD_ENTRY_TOO_LARGE) {
          // Already recorded + terminal for THIS reason: a growing session
          // re-snapshots on every turn, and a transcript that cannot shrink below
          // the bound must not append a ledger line per turn.
          return { written: false, bytes: onDisk, discards };
        }
        discards.push(quarantineEntry(dir, prior, DISCARD_ENTRY_TOO_LARGE, detail, Date.now()));
        return { written: false, bytes: onDisk, discards };
      }
      // The stored content is UNDER the bound and perfectly fileable; it is THIS
      // WRITE that would cross it. Refuse the growth and leave the entry exactly
      // as it was — filable, counted, and delivered by the next drain. Marking
      // it terminal here was a silent abandonment: a legal capture on disk, never
      // filed, never counted, never reported.
      discards.push(
        recordRefusal(dir, {
          session_id: snapshot.sessionId,
          capture_key: meta.capture_key,
          reason: DISCARD_ENTRY_GROWTH_REFUSED,
          retained: true,
          detail:
            `this GROWTH (${entryBytes} bytes) would take the entry past` +
            ` maxEntryBytes=${bounds.maxEntryBytes}, but the content already on` +
            ` disk (${onDisk} bytes) is under that bound — the write is REFUSED` +
            " and the stored entry is left untouched and fileable (a refused" +
            " growth is a delay, not a loss)",
        }),
      );
      return { written: false, bytes: onDisk, discards };
    }
    // A FRESH snapshot that is oversized was never on disk — there is nothing to
    // retain, so the record alone is the whole story. `retained: false` says so ON
    // THE RECORD: this reason's other arm retains everything, and the reason alone
    // cannot tell the two apart. It is NOT de-duped: nothing was written for this
    // session, so it has no entry to carry a marker (same as the Python leg).
    discards.push(
      recordDiscard(dir, {
        session_id: snapshot.sessionId,
        capture_key: meta.capture_key,
        reason: DISCARD_ENTRY_TOO_LARGE,
        retained: false,
        detail,
      }),
    );
    return { written: false, bytes: 0, discards };
  }

  // ⛔ ADMISSION CONTROL — never eviction — when the spool is full of work that
  // has never been delivered (capture-loss). Refuse the write, loudly and in the
  // ledger, and KEEP the backlog: its turn logs are the only copy the spool
  // holds, while the conversation this write could not admit is still in the
  // harness's own session log. A cap that deletes undelivered work to admit new
  // work destroys exactly what the queue exists to deliver.
  //
  // The question is asked about the spool as it will EXIST AFTER this write
  // (`entryBytes`, and `replacesSessionId` for a GROWTH) — not about the spool as
  // it is now. Asking about the present left the ceilings unenforceable in both
  // directions: a new entry could cross the byte ceiling on its own, and an
  // EXISTING entry could grow past it with no eviction path at all (measured
  // 3.07x the byte ceiling with an empty ledger — capture-loss residual 6).
  if (
    spoolSaturated(dir, bounds, {
      projectedBytes: entryBytes,
      replacesSessionId: prior ? snapshot.sessionId : undefined,
    })
  ) {
    discards.push(
      recordRefusal(dir, {
        session_id: snapshot.sessionId,
        capture_key: meta.capture_key,
        reason: DISCARD_SPOOL_SATURATED,
        retained: true,
        detail:
          `spool is at its ceiling (maxEntries=${bounds.maxEntries},` +
          ` maxTotalBytes=${bounds.maxTotalBytes}) with NOTHING evictable —` +
          ` this ${prior ? "session GROWTH" : "NEW session"} was REFUSED rather` +
          " than evicting an UNDELIVERED entry; the turns the spool already" +
          " holds are kept, and any newer turn is still in the harness session log",
      }),
    );
    return {
      written: false,
      bytes: prior ? spoolEntryBytes(dir, snapshot.sessionId) : 0,
      discards,
    };
  }

  try {
    if (extendsStored) {
      // fsync the append: this is the path holding the MOST RECENT turns, and a
      // power loss (not just a process kill) must not drop them.
      const fd = openSync(logPath, "a", 0o600);
      try {
        appendFileSync(fd, logText, { encoding: "utf8" });
        fsyncSync(fd);
      } finally {
        closeSync(fd);
      }
    } else {
      atomicWrite(logPath, logText);
    }
    atomicWrite(metaPath, metaText);
    sweepStaleTempFiles(dir);
  } catch (err) {
    // A filesystem failure (full disk, read-only home) is a LOSS — record it
    // with a reason rather than returning an empty discard list (the silent
    // drop this issue exists to remove).
    discards.push(
      recordDiscard(dir, {
        session_id: snapshot.sessionId,
        capture_key: meta.capture_key,
        reason: DISCARD_WRITE_FAILED,
        detail: err instanceof Error ? err.message : String(err),
      }),
    );
    return { written: false, bytes: 0, discards };
  }

  // Post-write prune: keep the spool inside BOTH ceilings, oldest first. Never
  // evict the entry just written. Each eviction is recorded.
  discards.push(...pruneSpool(dir, snapshot.sessionId, bounds));
  return { written: true, bytes: spoolEntryBytes(dir, snapshot.sessionId), discards };
}

function fileSize(path: string): number {
  try {
    return statSync(path).size;
  } catch {
    return 0;
  }
}

function compareAge(a: SpoolMeta, b: SpoolMeta): number {
  // Deterministic oldest-first. `updated_at` alone ties for entries written in
  // the same clock tick, which made eviction depend on directory order;
  // `session_id` is the final, stable tiebreak.
  for (const key of ["updated_at", "created_at", "session_id"] as const) {
    const av = String(a[key] ?? "");
    const bv = String(b[key] ?? "");
    if (av < bv) return -1;
    if (av > bv) return 1;
  }
  return 0;
}

/**
 * Enforce the spool's COUNT and BYTE ceilings, evicting the SAFEST loss first.
 * Excludes `keepSessionId` (the entry just written) so a write can never evict
 * itself. Returns one DiscardRecord per eviction, with the ceiling named and
 * the CLASS of loss recorded.
 *
 * #6388: the spool is a DELIVERY QUEUE, not an age-ordered cache, so the two
 * classes are ranked BEFORE age — a **filed** entry's content is already on the
 * wire (and Pi's own session log still holds it locally), while an **unfiled**
 * entry is the only copy of a conversation the user never got into the graph.
 * Age alone inverts that priority whenever a MIXED spool holds a backlog older
 * than its successes, which is the shape any partial drain leaves behind: a
 * drain that files the newer sessions while older ones stay deferred (saturated
 * backoff; a keyless window whose entries are only retried once a key appears)
 * appends the successes ABOVE the backlog, and the cap then reaps the backlog it
 * exists to deliver. Within a class the order is UNCHANGED (oldest first).
 * WHEN EVERY CANDIDATE IS UNDELIVERED THE CAP REAPS NOTHING (capture-loss,
 * 2026-11-01): an undelivered entry is the only copy of a conversation that
 * never reached the graph, and evicting it UNLINKS it. The spool is SATURATED
 * instead (`spoolSaturated`), which refuses NEW work and records
 * `spool_saturated` — bounded disk without destroying the backlog. Mirrors
 * `capture_spool.py::prune_spool` (the two legs share one directory).
 *
 * A quarantined turn log (`quarantineLog`) has no meta, so it is absent from the
 * meta scan and from `spoolEntryBytes`: its bytes are counted HERE and it is
 * evicted (oldest first, by mtime) AFTER the filed-entry pass and before any
 * UNFILED live entry. Without this, the byte ceiling stopped applying the moment
 * a meta was quarantined and the spool could grow past it without bound (#8531
 * review). A quarantined log goes LAST of the evictable classes, not first: a
 * FILED entry's content is already on the wire, so reclaiming it is a CERTAIN
 * zero-cost choice, while an orphan's delivered-ness is unknowable
 * (`quarantineLog` removes the corrupt meta, which was the only copy of
 * `filed_key`) — the certain option must be spent before the possible loss. The
 * eviction is still a real loss, and its record names that class in the same
 * `loss` vocabulary the live entries use (#6388).
 */
export function pruneSpool(
  dir: string,
  keepSessionId: string | undefined,
  bounds: SpoolBounds = DEFAULT_BOUNDS,
): DiscardRecord[] {
  const discards: DiscardRecord[] = [];
  const { metas } = listSpoolEntries(dir);
  const orphans = orphanPaths(dir);
  const ranked = metas
    .filter((m) => m.session_id !== keepSessionId)
    .map((m) => ({ meta: m, bytes: spoolEntryBytes(dir, m.session_id) }))
    .sort((a, b) => {
      // #6388: the SAFE loss goes first — `entryFiled` is the SAME predicate the
      // drain's skip clause uses, so "already on the wire" here means exactly
      // what it means there. Within a class, oldest first (unchanged).
      const aFiled = entryFiled(a.meta) ? 0 : 1;
      const bFiled = entryFiled(b.meta) ? 0 : 1;
      if (aFiled !== bFiled) return aFiled - bFiled;
      return compareAge(a.meta, b.meta);
    });

  let count = metas.length;
  let total =
    metas.reduce((sum, m) => sum + spoolEntryBytes(dir, m.session_id), 0) +
    orphans.reduce((sum, path) => sum + fileSize(path), 0);
  // The quarantined-log pass runs AFTER the filed-entry pass below, never before
  // it: a FILED entry is a CERTAIN zero-cost reclaim (its content is on the
  // wire), while an orphan's delivered-ness is unknowable — so the certain
  // option is spent FIRST. An orphan is, however, still preferable to an
  // UNFILED live entry: it is unreachable to every surface (a corrupt meta moved
  // it aside, so no drain can file it and no reader can list it), while an
  // unfiled entry still has a surface that can deliver it — and an unfiled entry
  // is never evicted at all (capture-loss). The record names the class in the
  // same `loss` vocabulary the live entries use (#6388).
  for (const { meta, bytes } of ranked.filter(({ meta: m }) => entryFiled(m))) {
    if (count <= bounds.maxEntries && total <= bounds.maxTotalBytes) break;
    const reason =
      total > bounds.maxTotalBytes ? DISCARD_BYTES_EXCEEDED : DISCARD_COUNT_EXCEEDED;
    // #6388: name the CLASS of loss. The discard line is the only surface a cap
    // eviction has, and "spool has 1001 entries" alone read (fairly, from that
    // text) as "something was dropped", with no way to tell the safe eviction
    // from the irreversible one.
    // Unreachable-by-construction second arm kept out: the candidate set above is
    // `ranked.filter(entryFiled)`, so ONLY a filed entry can reach this line.
    const loss = "; evicted an already filed entry (its content is on the wire)";
    const detail =
      reason === DISCARD_BYTES_EXCEEDED
        ? `spool is ${total} bytes > maxTotalBytes=${bounds.maxTotalBytes}${loss}`
        : `spool has ${count} entries > maxEntries=${bounds.maxEntries}${loss}`;
    discards.push(discardEntry(dir, meta, reason, detail));
    count -= 1;
    total -= bytes;
  }
  for (const orphan of orphans) {
    if (total <= bounds.maxTotalBytes) break;
    const size = fileSize(orphan);
    discards.push(
      recordDiscard(dir, {
        session_id: `orphan:${basename(orphan)}`,
        reason: DISCARD_BYTES_EXCEEDED,
        detail:
          `spool is ${total} bytes > maxTotalBytes=${bounds.maxTotalBytes}` +
          "; evicted a quarantined log (UNREACHABLE: its meta is gone, so" +
          " whether it was ever filed is unknown)",
      }),
    );
    try {
      unlinkSync(orphan);
    } catch {
      // already gone
    }
    total -= size;
  }
  // ⛔ ONLY a FILED entry is evictable (capture-loss). Its content is already on
  // the wire, so dropping it costs nothing a reader cannot re-fetch; an
  // UNDELIVERED entry is the only copy of a conversation that never reached the
  // graph, and `discardEntry` UNLINKS it. The cap therefore stops here and the
  // spool reports SATURATED instead (`spoolSaturated`), which refuses NEW work
  // rather than destroying old work. Filed-first was already the #6388 ranking;
  // this makes it the whole candidate set.
  return discards;
}

/**
 * Is the spool at a ceiling with NOTHING evictable?
 *
 * `pruneSpool` may only evict ORPHANS and FILED entries, so a spool whose live
 * entries are all undelivered cannot shrink: it is SATURATED. That state is
 * exactly where a cap silently destroys the product's own data (measured:
 * 80 `spool_count_exceeded` evictions of never-filed sessions on pi-cell-3),
 * so it is a first-class, reported state rather than an eviction trigger.
 *
 * Both ceilings are checked, because either one can bind.
 *
 * ⛔ THE EVICTABLE SET IS `pruneSpool`'s, NOT "filed entries only".
 * `pruneSpool` reaps a quarantined `.orphan` under the byte ceiling, and this
 * predicate folds orphan bytes into `total` — so asking "is anything FILED?"
 * answered a DIFFERENT question than the one the total implies: a spool pushed
 * over its byte ceiling by evictable orphan bytes reported SATURATED, refused the
 * write, and therefore never reached the only call site that prunes (the write it
 * just refused), so the refusal OUTLIVED its own condition and every later
 * capture was refused for a reason prune was entitled to clear. Orphan bytes are
 * therefore CREDITED against the byte test — prune may delete them, so they are
 * room — while a COUNT overage is still only relievable by a filed entry,
 * because prune's orphan pass is byte-gated and never touches the count. A
 * quarantined orphan's delivered-ness is still unknowable (`quarantineLog`
 * removed the meta, the only copy of `filed_key`), which is why it is evicted
 * LAST; it is not a reason to call the spool unshrinkable. Mirrors
 * `capture_spool.spool_saturated`.
 *
 * PROJECTION (capture-loss residual 6): a write path does not ask about the spool
 * as it is, it asks whether the write ITSELF can land — pass its post-write size
 * as `projectedBytes`, and `replacesSessionId` when the write EXTENDS an existing
 * entry rather than adding one. Asking about the PRESENT state only leaves both
 * ceilings unenforceable in the two directions a write can cross them: a NEW
 * entry may be large enough to cross the byte ceiling on its own, and an EXISTING
 * entry may grow past it with no eviction path at all (measured 3.07x the byte
 * ceiling with an empty ledger). With a projection the comparison is `>` (the
 * write must not land OVER a ceiling); without one it stays `>=` (a spool exactly
 * AT a ceiling has no room for even a zero-byte entry). The Python twin
 * (`capture_spool.spool_saturated`) states the same rule.
 */
export function spoolSaturated(
  dir: string,
  bounds: SpoolBounds = DEFAULT_BOUNDS,
  projection: { projectedBytes?: number; replacesSessionId?: string } = {},
  opts: { repair?: boolean } = {},
): boolean {
  // `repair: false` keeps the meta scan from MUTATING the spool (no ledger line,
  // no quarantine, no unlink): the health line must describe the spool it reads
  // (`listSpoolEntries` is the drain's repairing scanner — the same rule
  // `countPendingEntries` follows). The write path keeps the repairing scan.
  const { metas } = listSpoolEntries(dir, opts);
  // Bytes `pruneSpool` may reclaim WITHOUT spending an undelivered entry: the
  // orphan class is byte-evictable only (see below).
  const reclaimableOrphanBytes = orphanPaths(dir).reduce((sum, path) => sum + fileSize(path), 0);
  let total =
    metas.reduce((sum, m) => sum + spoolEntryBytes(dir, m.session_id), 0) +
    reclaimableOrphanBytes;
  const { projectedBytes, replacesSessionId } = projection;
  if (projectedBytes === undefined && replacesSessionId === undefined) {
    // STRICT: the question is whether ADDING an entry would cross the ceiling, so
    // a spool exactly AT the count ceiling is already saturated.
    if (metas.length < bounds.maxEntries && total - reclaimableOrphanBytes < bounds.maxTotalBytes)
      return false;
  } else {
    const count = metas.length + (replacesSessionId === undefined ? 1 : 0);
    if (replacesSessionId !== undefined) {
      // A GROWTH replaces this entry's bytes instead of adding a second copy of
      // them, and adds no count.
      total -= spoolEntryBytes(dir, replacesSessionId);
    }
    total += projectedBytes ?? 0;
    if (count <= bounds.maxEntries && total - reclaimableOrphanBytes <= bounds.maxTotalBytes)
      return false;
  }
  // Over a ceiling that the ORPHAN CREDIT could not absorb. Only a FILED entry
  // may make room now: its content is already on the wire, so dropping it costs
  // nothing a reader cannot re-fetch, while an UNDELIVERED entry is the only copy
  // of a conversation that never reached the graph and `discardEntry` UNLINKS it.
  // The entry being written can never reclaim ITSELF: `pruneSpool` is told to keep
  // it (`keepSessionId`), so counting it would admit a write nothing can make room
  // for.
  return !metas.some((m) => entryFiled(m) && m.session_id !== replacesSessionId);
}

/**
 * Classify an upload failure.
 *
 * TRANSIENT (retry with backoff): no status (network / timeout), 5xx, 3xx (a
 * redirect on a stored api_url must not delete the capture), the retryable 4xx
 * family (402 quota-refusal, 408 request-timeout, 425 too-early, 429
 * rate-limit), and EVERY 409.
 *
 * "No status" is TOTAL, and it has to be: `undefined` (the fetch never
 * resolved), `null` (a transport/fetchImpl that models absence as null), and a
 * non-finite value (an unparseable status). JS's `null >= 300` and `null >= 500`
 * are both FALSE, so `null` used to fall through to "permanent" -> discardEntry
 * -> unlink the capture, while the Python leg returned "retry" for the same
 * input. A missing status is a network condition, never a server verdict.
 *
 * On this idempotent upsert a 409 is either #3713's in-flight concurrency
 * condition (retry then replays) or a policy state (recording disabled) that
 * the user can reverse — a capture must not be destroyed because recording was
 * briefly off. Prose-matching the server's in-flight message is deliberately
 * avoided: the client ships independently of the server, so a reworded detail
 * would silently turn #3713's benign 409 into a lost write.
 *
 * 402 is TRANSIENT (#4714), and the Python leg already classifies it so. The
 * hosted quota gate refuses a capture whose *estimated* point cost would cross
 * the org's cap, and `est` is computed from the INCOMING capture — so the
 * identical capture succeeds the moment a node is freed or the tier changes,
 * exactly the "becomes valid by waiting" property that defines transient here.
 * Classified permanent, `flushSpool` routed it to `discardEntry`, which
 * UNLINKS the meta and the turn log. Both legs share ONE spool directory
 * (`~/.tortoise/capture-spool`), so leaving 402 permanent here re-opens the
 * data loss the Python fix closes: a capture the Python drain correctly defers
 * is destroyed by the next Pi drain. Retry is bounded by the spool's count/byte
 * bound and the ENTRY's `backoffDelay` — carried across turns, so an
 * actively-growing session is retried on the backoff clock rather than once per
 * turn. Under sustained over-quota nothing can file, so nothing is evictable:
 * the ONLY evictable class is an already-filed entry (oldest-first within it),
 * and an all-undelivered spool SATURATES — new work is refused and recorded
 * (`spool_saturated`) while the backlog is kept (capture-loss, 2026-11-01). Both
 * legs state that one policy (`capture_spool.py`).
 *
 * ⚠️ #4614 gave the refusal a machine-readable CATEGORY
 * (`detail.code === "quota_exceeded"`) so a caller no longer has to match the
 * message text. This classifier still keys on the STATUS, deliberately: the
 * category is for REPORTING and for surfaces that can act on it, and treating
 * a `quota_exceeded` 402 as terminal here would re-open #4714's data loss. The
 * two legs must keep answering this the same way (`capture_spool.py`).
 *
 * PERMANENT (record + terminal + KEEP — #7708): every other 4xx — a malformed
 * payload or an
 * out-of-range turn count never becomes valid by waiting. The verdict is
 * unchanged; `flushSpool` no longer UNLINKS a permanently-refused entry.
 */
export function classifyFailure(
  status: number | null | undefined,
  detail?: string,
): "retry" | "permanent" {
  if (status === undefined || status === null || !Number.isFinite(status)) return "retry";
  if (status >= 300 && status < 400) return "retry";
  if (status >= 500) return "retry";
  if (status === 402 || status === 408 || status === 425 || status === 429) return "retry";
  if (status === 409) return "retry";
  return "permanent";
}

/** `attempts` as a small non-negative int, however corrupt the stored value is.
 *  Clamped because `backoffDelay` computes `2 ** (n - 1)` from it; the backoff
 *  saturates (30 s * 2**10 > 6 h) long before this bound. */
export function clampAttempts(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.min(Math.floor(value), MAX_ATTEMPTS)
    : 0;
}

/** `next_attempt_at_ms` as a FINITE epoch-ms value — 0 means "retry now".
 *  A non-finite window is not a window: `Infinity > nowMs` is true forever, and
 *  the carry-forward would preserve it across every turn, making a growing
 *  session permanently un-fileable while every surface says "will retry". */
export function clampWindow(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}

/** The prior entry's window, bounded to what the write path can produce.
 *  `clampWindow` makes it finite; this makes it PLAUSIBLE. A legitimate window
 *  is at most `written_at + RETRY_MAX_MS` (`backoffDelay` saturates there), so
 *  anything further out is corrupt — and because it is CARRIED forward it would
 *  be re-written on every turn and strand the entry permanently, silently. */
export function carriedWindow(value: unknown): number {
  const window = clampWindow(value);
  return window <= Date.now() + RETRY_MAX_MS ? window : 0;
}

/** Exponential backoff for attempt N (1-based), capped at RETRY_MAX_MS. */
export function backoffDelay(attempts: number): number {
  const exp = Math.max(0, clampAttempts(attempts) - 1);
  return Math.min(RETRY_BASE_MS * 2 ** exp, RETRY_MAX_MS);
}

function writeMeta(dir: string, meta: SpoolMeta): void {
  try {
    atomicWrite(metaPathFor(dir, meta.session_id), `${JSON.stringify(meta, null, 2)}\n`);
  } catch {
    // best effort — the entry simply replays again next opportunity
  }
}

/** The entry's `terminal_failure` marker, or `undefined` — the ONE total read.
 *
 *  A truthy NON-object value (a hand-edited / foreign / corrupt `meta.json`:
 *  `1`, `true`, `"yes"`, `[]`) is NOT a marker. Reading the field by truthiness
 *  alone made such an entry append to `summary.terminal` on EVERY flush — never
 *  retried, silent on every surface (#8531 review). The safe reading of an
 *  unusable marker is "not terminal, retry" (the `clampWindow` reading): the
 *  entry stays filable, and a genuine refusal re-marks it on the next attempt.
 *  `[]` is excluded explicitly — `typeof [] === "object"` and an empty array is
 *  truthy, so a bare object check would still strand it. */
function terminalMarker(meta: SpoolMeta): { reason?: string; detail?: string } | undefined {
  const marker: unknown = meta.terminal_failure;
  if (!marker || typeof marker !== "object" || Array.isArray(marker)) return undefined;
  return marker as { reason?: string; detail?: string };
}

/**
 * #2100: is this entry already FILED — has the server accepted EXACTLY this
 * content, for this lane and this client stamp?
 *
 * ONE definition, read by three places: the drain's skip clause, the
 * post-POST CAS re-read, and the per-box health count. A health line that
 * used a different notion of "filed" from the drain would describe a spool
 * nobody is draining — and a filed entry is NOT pending work: the success
 * path stamps `filed_*` and keeps the entry (nothing unlinks a filed
 * capture), so counting meta files measures spool OCCUPANCY, not backlog.
 */
function entryFiled(meta: SpoolMeta): boolean {
  return Boolean(
    meta.filed_key && meta.filed_key === meta.capture_key &&
      // Compared against the SAME normalisation the payload uses: the wire
      // carries `finiteInstant(...)`, so comparing the RAW disk value can never
      // match for a value the guards refuse — and the drain never rewrites the
      // meta, so such an entry would re-POST its full transcript on EVERY
      // drain, forever (#4714 amplification).
      normaliseLane(meta.filed_lane) === normaliseLane(meta.capture_lane) &&
      (meta.filed_stamp ?? undefined) === finiteInstant(meta.client_captured_at),
  );
}

/**
 * Replay opportunity. For every spooled session whose content has not been
 * filed, attempt one POST. Transient failures back off; a permanent 4xx is
 * recorded and the entry is marked terminal and KEPT (never unlinked — #7708);
 * entries in a backoff window are skipped (not lost).
 *
 * Filtering keeps the "deferred extraction" honest: `onlySessionId` files just
 * the current session at shutdown; `excludeSessionId` drains OTHER sessions
 * while the current one is still live (so a live session is never filed
 * mid-conversation and then silently frozen by the server's session_id replay).
 */
export async function flushSpool(
  cfg: CaptureConfig,
  opts: {
    dir?: string;
    fetchImpl?: FetchLike;
    now?: number;
    excludeSessionId?: string;
    onlySessionId?: string;
    timeoutMs?: number;
    bounds?: SpoolBounds;
    /** Session ids another flush in this process is already handling (#3963). */
    inFlight?: Set<string>;
    /** #2100: POST attempts this pass may make (default `FLUSH_MAX_ATTEMPTS`). */
    maxAttempts?: number;
    /** #2100: wall-clock budget for this pass (default `FLUSH_BUDGET_MS`).
     *  Checked BETWEEN entries, never mid-POST, so one slow POST still finishes
     *  and its outcome is recorded rather than lost. */
    budgetMs?: number;
    /** #2100: clock seam for the budget, separate from `now` (which is the
     *  BACKOFF clock a test may freeze at `1`; sharing it would make every pass
     *  look instantaneous or infinitely long). */
    budgetClock?: () => number;
  } = {},
): Promise<FlushSummary> {
  const dir = opts.dir ?? SPOOL_DIR;
  const doFetch = opts.fetchImpl ?? (fetch as unknown as FetchLike);
  const nowMs = opts.now ?? Date.now();
  const busy = opts.inFlight;
  const summary: FlushSummary = {
    attempted: 0,
    filed: 0,
    deferred: 0,
    skipped: 0,
    heldByBackoff: 0,
    heldBack: 0,
    terminal: 0,
    remaining: 0,
    discarded: [],
  };

  const maxAttempts =
    typeof opts.maxAttempts === "number" && Number.isFinite(opts.maxAttempts)
      ? Math.max(0, Math.floor(opts.maxAttempts))
      : FLUSH_MAX_ATTEMPTS;
  const budgetMs =
    typeof opts.budgetMs === "number" && Number.isFinite(opts.budgetMs)
      ? Math.max(0, opts.budgetMs)
      : FLUSH_BUDGET_MS;
  const budgetClock = opts.budgetClock ?? (() => Date.now());
  const startedAt = budgetClock();

  const { metas, discards } = listSpoolEntries(dir);
  summary.discarded.push(...discards);
  const ranked = metas.sort(compareAge);

  for (const meta of ranked) {
    if (opts.onlySessionId && meta.session_id !== opts.onlySessionId) continue;
    if (opts.excludeSessionId && meta.session_id === opts.excludeSessionId) {
      summary.heldBack += 1;
      continue;
    }
    if (terminalMarker(meta) !== undefined) {
      // A previous PERMANENT refusal (#7708). The entry is KEPT — terminal,
      // never destructive — and never retried. Counted separately so a spool
      // whose entries are all terminal still reports on every flush instead of
      // reaching the silent steady state `heldByBackoff` was added to close.
      // The read is TOTAL: a junk non-object marker is read as "not terminal"
      // so a corrupt value can never strand the entry (#8531 review).
      summary.terminal += 1;
      continue;
    }
    // Read tolerance: a meta written by an OLDER Python producer stored
    // `"filed_lane": null` for a lane-less filing. The Python leg now OMITS the
    // key, so both legs write alike — but a spool written before that fix, or
    // by any producer that emits JSON `null`, must still be read alike, because
    // JSON has no `undefined`: a raw `JSON.parse` yields `null`, and strict
    // `===` against this leg's ABSENT key would bypass the skip and re-POST an
    // entry the other leg had already filed (#3516 §B).
    // #7708: BOTH sides through ONE normaliser, so a stored falsy lane
    // (`""`/`0`/`false`) agrees with the `undefined` the payload omits — a raw
    // `undefined === ""` was false and the entry re-POSTed forever (#4714).
    // #2100: ONE definition of "filed", shared with the health count and the
    // CAS re-read below (see `entryFiled`).
    if (entryFiled(meta)) {
      summary.skipped += 1;
      continue;
    }
    let window = clampWindow(meta.next_attempt_at_ms);
    if (window > nowMs + RETRY_MAX_MS) {
      // No legitimately-written window is further out than now + RETRY_MAX:
      // `backoffDelay` saturates there. Beyond it the value is corrupt, and the
      // safe reading of an unusable window is "retry now" — honouring it would
      // strand the entry indefinitely.
      window = 0;
    }
    if (window > nowMs) {
      summary.heldByBackoff += 1;
      summary.skipped += 1;
      continue;
    }
    if (busy?.has(meta.session_id)) {
      summary.skipped += 1;
      continue;
    }
    // #2100: the three pass bounds. An entry we refuse to REACH is not
    // "skipped" — it was attemptable and is still queued, so it is counted in
    // `remaining` and left untouched (its window is neither armed nor reset).
    // A quota refusal is checked FIRST: once the organization's quota is
    // exhausted, every further POST in this pass is guaranteed to fail, and
    // attempting them only deepens each entry's backoff.
    if (summary.quota) {
      summary.stopped ??= "quota";
      summary.remaining += 1;
      continue;
    }
    if (summary.attempted >= maxAttempts) {
      summary.stopped ??= "attempts";
      summary.remaining += 1;
      continue;
    }
    if (budgetClock() - startedAt >= budgetMs) {
      summary.stopped ??= "budget";
      summary.remaining += 1;
      continue;
    }
    busy?.add(meta.session_id);
    try {
      const turns = readSpoolTurns(dir, meta.session_id);
      if (turns.length === 0) {
        // TWO outcomes, decided by whether the log still holds bytes (#7708).
        // An absent/zero-byte log is provably empty; a log with bytes may be
        // the only copy of the conversation, so it is RECORDED and KEPT
        // (terminal) rather than unlinked.
        summary.discarded.push(
          turnLogHasContent(dir, meta.session_id)
            ? quarantineEntry(
                dir, meta, DISCARD_TRANSCRIPT_TORN,
                "turn log has bytes but no parseable turn — kept", nowMs,
              )
            : discardEntry(dir, meta, DISCARD_TRANSCRIPT_EMPTY, "turn log is empty"),
        );
        continue;
      }

      summary.attempted += 1;
      // The digest of what will actually be POSTed (see the CAS below).
      const postedDigest = contentDigest(turns);
      const payload = buildCapturePayload({
        sessionId: meta.session_id,
        turns,
        source: meta.source,
        machineId: meta.machine_id,
        model: meta.model,
        // The ENTRY's lane, not this process's: a lane-less import entry
        // drained here must stay lane-less.
        captureLane: meta.capture_lane,
        // #3516 §B / piece 12: the ENTRY's stamp, exactly as it stands — a
        // lane-less pre-#3516 entry has none and must post without the keys.
        clientCapturedAt: meta.client_captured_at,
        clientCapturedAtSource: meta.client_captured_at_source,
      });
      const res = await postCapture(cfg, payload, doFetch, opts.timeoutMs);
      if (res.ok) {
        // COMPARE-AND-SWAP, on the POSTED CONTENT **AND LANE**. A `turn_end` can
        // append to this very entry while the POST is in flight (a resumed
        // session; a cross-process drain). Stamp `filed_key` only when what is
        // on disk NOW is exactly what was posted; otherwise leave the entry
        // unfiled so the next opportunity re-posts. Comparing meta-to-meta was
        // wrong: a writer killed between its log append and its meta write
        // leaves the meta digest stale, so the check passed while a turn went
        // unfiled forever.
        //
        // The read happens IMMEDIATELY before the write, and the WRITE APPLIES
        // TO THE FRESHLY-READ META — never to a snapshot taken earlier: writing
        // an earlier snapshot back would clobber any concurrent edit that landed
        // in between, including a lane upgrade, which is precisely the edit that
        // must survive.
        //
        // The LANE is part of this identity (#3516 §B): `capture_key` is
        // content-derived and the lane is not part of the content, so without
        // that clause a lane upgrade landing inside the POST window was
        // cancelled and the lane stranded on the wire.
        //
        // A RESIDUAL window remains: a lane-upgrading snapshot landing between
        // the read above and the write below is still clobbered, and the entry
        // then reads as FILED LANE-LESS until some later snapshot re-triggers
        // the upgrade (permanent only if the session never snapshots again). It
        // is accepted rather than closed — the window is two file reads plus one
        // digest (sub-ms for a typical session, up to ~150 ms at the 16 MB
        // `SPOOL_MAX_ENTRY_BYTES` bound), and the racer must be the FIRST lane-ful
        // snapshot of a previously lane-less entry. Closing it needs mutual
        // exclusion in the capture hot path (every turn_end) in two languages,
        // where a stale lock would BLOCK OR LOSE CAPTURES — a worse failure. If
        // it ever must be closed, the lock-free route is a SIDECAR marker file
        // for the drain-owned fields, so no read-modify-write touches meta.json.
        const onDisk = readSpoolEntry(dir, meta.session_id);
        const onDiskTurns = readSpoolTurns(dir, meta.session_id);
        const postedLane = payload.capture_lane as string | undefined;
        // #3516 §B: the stamp is part of the posted identity for the same
        // reason the lane is — metadata, not content — so a stamp appearing
        // while the POST is in flight must not be cancelled and stranded.
        const postedStamp = payload.client_captured_at as number | undefined;
        if (
          onDisk &&
          contentDigest(onDiskTurns) === postedDigest &&
          // #7708: normalised on BOTH sides, like the skip clause — the wire
          // carried the NORMALISED lane (`postedLane`), so a raw stored
          // falsy/foreign value could never equal it and the CAS would never
          // stamp `filed_key` (the re-POST loop).
          normaliseLane(onDisk.capture_lane) === postedLane &&
          // Normalised on BOTH sides of the CAS, for the same reason as the skip
          // clause: `postedStamp` is what the WIRE carried, so the raw disk value
          // can never equal it.
          finiteInstant(onDisk.client_captured_at) === postedStamp
        ) {
          onDisk.filed_key = onDisk.capture_key ?? captureKey(meta.session_id, onDiskTurns);
          onDisk.filed_lane = postedLane;
          // `undefined` is OMITTED by JSON.stringify, which is exactly the
          // "the wire carried none" encoding the skip clause expects.
          onDisk.filed_stamp = postedStamp;
          onDisk.filed_at = new Date(nowMs).toISOString();
          onDisk.attempts = 0;
          onDisk.next_attempt_at_ms = 0;
          writeMeta(dir, onDisk);
        }
        // else: the posted content WAS filed; the entry keeps the newer turns
        // and stays unfiled, so they are re-posted (and stored) next time.
        summary.filed += 1;
        continue;
      }
      if (classifyFailure(res.status, res.detail) === "permanent") {
        // TERMINAL, NEVER DESTRUCTIVE (#7708): record the refusal and mark the
        // entry terminal while KEEPING the turn log — `discardEntry` here used
        // to unlink the only copy. The `classifyFailure` VERDICT is deliberately
        // unchanged (the Python/Pi parity contract depends on it); what changed
        // is its consequence.
        summary.discarded.push(
          quarantineEntry(
            dir,
            meta,
            DISCARD_HTTP_REJECTED,
            `HTTP ${res.status ?? "none"}: ${res.detail ?? "permanent client error"}`,
            nowMs,
          ),
        );
        continue;
      }
      // Re-read before the backoff write-back for the same reason as the CAS
      // above: never clobber newer turns written while the POST was in flight.
      const pending = readSpoolEntry(dir, meta.session_id) ?? meta;
      // #7708: both sides through the normaliser (the same class as the skip
      // clause and the CAS).
      if (entryFiled(pending)) {
        // A CONCURRENT flush already filed this exact content while our POST was
        // in flight. Re-arming the backoff here would attach a window to content
        // that was never refused — and since writeSpoolEntry now CARRIES the
        // window, the next turn's NEW content would inherit it and wait up to
        // RETRY_MAX_MS with no attempt behind it (#4714 cycle-7 review).
        summary.skipped += 1;
        continue;
      }
      pending.attempts = clampAttempts(meta.attempts) + 1;
      pending.next_attempt_at_ms = nowMs + backoffDelay(pending.attempts);
      writeMeta(dir, pending);
      summary.deferred += 1;
      // #2100: name the quota refusal (reporting only — the deferral above is
      // unchanged, so the capture is still retried on its backoff clock).
      const quota = quotaRefusal(res);
      if (quota) summary.quota = quota;
    } finally {
      busy?.delete(meta.session_id);
    }
  }
  return summary;
}

/** Read every turn the spool holds for a session (for tests + diagnostics). */
export function readSpoolEntry(dir: string, sessionId: string): SpoolMeta | undefined {
  try {
    return JSON.parse(readFileSync(metaPathFor(dir, sessionId), "utf8")) as SpoolMeta;
  } catch {
    return undefined;
  }
}

/**
 * #2100: does this failed POST mean no further POST in this pass can succeed?
 *
 * `classifyFailure` deliberately keeps 402 TRANSIENT (the identical capture
 * succeeds once a node frees) — that verdict is unchanged. This is the other
 * question, asked for REPORTING: a 402 (or a body carrying one of the server's
 * 402 categories — `quota_exceeded` for the plan allowance, `cohort_cost_cap`
 * for the org's spend cap) means the refusal is the ORGANIZATION's, not this
 * payload's, so attempting the remaining entries only burns the budget and
 * deepens every entry's backoff. Naming it is what turns a silent backlog into
 * `capture: BLOCKED (n queued, 402 quota)`.
 */
export function quotaRefusal(res: {
  status?: number | null;
  detail?: string;
}): QuotaRefusal | undefined {
  const detail = typeof res.detail === "string" ? res.detail : "";
  const code = REFUSAL_CODES.find((c) => detail.includes(c));
  const status =
    typeof res.status === "number" && Number.isFinite(res.status) ? res.status : undefined;
  if (status !== 402 && code === undefined) return undefined;
  return {
    status: status ?? 402,
    ...(code ? { code } : {}),
    ...(detail ? { detail } : {}),
  };
}

function statusPathFor(dir: string): string {
  return join(dir, SPOOL_STATUS_FILE);
}

/**
 * #2100: how many entries are PENDING — a READ-ONLY count for the health line.
 *
 * "Pending" is what the drain would still try: an entry that is neither
 * TERMINAL (kept forever, never retried — #7708) nor FILED (the server already
 * accepted exactly this content; the entry is kept, not unlinked). Counting raw
 * meta files counts OCCUPANCY, and on a normal box every filed session stays in
 * `entries/` until the count ceiling evicts it — so an occupancy count reads
 * `DRAINING (n)` on a perfectly healthy box, makes `OK (0 queued)`
 * unreachable, and can never self-clear a stale `BLOCKED` marker.
 *
 * Deliberately NOT `listSpoolEntries`: that one is the drain's scanner, and it
 * REPAIRS as it reads (it records `corrupt_entry` discards and quarantines the
 * metas it cannot parse). A status line printed on every `session_start` must
 * not mutate the spool it is describing — a health check that deletes things is
 * its own defect — and it must not append a ledger line per session to a spool
 * it cannot read.
 * @returns the pending count, or `undefined` when the spool cannot be LISTED
 *   (any error other than ENOENT). An unreadable directory is NOT an empty one
 *   — the same rule `listSpoolEntries` follows with `DISCARD_SPOOL_UNREADABLE`,
 *   and the reason it matters here is that a transient EACCES/EMFILE read as
 *   "nothing pending" would both print `OK` and let the self-heal OVERWRITE the
 *   durable block marker with `ok` (found in review cycle 2).
 */
export function countPendingEntries(dir: string): number | undefined {
  let names: string[];
  try {
    names = readdirSync(entriesDir(dir));
  } catch (err) {
    // ENOENT is an absent spool: nothing has been captured, which IS empty.
    if ((err as NodeJS.ErrnoException | undefined)?.code === "ENOENT") return 0;
    return undefined; // unreadable is not empty
  }
  let pending = 0;
  for (const name of names) {
    if (!name.endsWith(".meta.json")) continue;
    let meta: SpoolMeta | undefined;
    try {
      meta = JSON.parse(readFileSync(join(entriesDir(dir), name), "utf8")) as SpoolMeta;
    } catch {
      meta = undefined;
    }
    // A meta we cannot read is UNKNOWABLE, not absent: count it conservatively
    // (it may be waiting) and leave the repair to the drain's own scan.
    if (!meta || typeof meta !== "object") {
      pending += 1;
      continue;
    }
    if (terminalMarker(meta) !== undefined) continue;
    if (entryFiled(meta)) continue;
    pending += 1;
  }
  return pending;
}

/** Read the recorded per-box status. Absent/corrupt reads as `undefined` (the
 *  health line then falls back to what the spool itself proves). */
export function readCaptureStatus(dir: string): CaptureStatus | undefined {
  try {
    const parsed = JSON.parse(readFileSync(statusPathFor(dir), "utf8")) as CaptureStatus;
    return parsed && typeof parsed === "object" ? parsed : undefined;
  } catch {
    return undefined;
  }
}

/** Record the per-box status. Best-effort: local bookkeeping must never fail a
 *  capture path (the same contract as `recordDiscard`). */
export function writeCaptureStatus(dir: string, status: CaptureStatus): void {
  try {
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    atomicWrite(statusPathFor(dir), `${JSON.stringify(status, null, 2)}\n`);
  } catch {
    // a health line that cannot be persisted is not a capture failure
  }
}

/**
 * #2100: the ONE per-box health line.
 *
 * An operator (or `pr-doctor`, or whoever reads the lane log after it produced
 * 259 B) must be able to tell a BLOCKED box from an idle one. The line is
 * printed once per `session_start` and again whenever a drain discovers a new
 * quota refusal.
 *
 * `queued` is the number of PENDING entries (see `countPendingEntries`) —
 * recomputed from the spool on every call — and the recorded marker is only
 * trusted while entries are still waiting, so a marker left behind by a quota
 * that has since lifted, or by the Python leg draining the same directory,
 * cannot keep claiming a block. Nothing pending is always `ok` (nothing is
 * blocked), and the marker is cleared when that is observed — including when
 * every remaining entry turned TERMINAL, which is a refusal with no quota
 * behind it. A spool that cannot be LISTED is `unreadable` and is never
 * reported as empty, nor healed.
 */
export interface CaptureHealth {
  state: CaptureState;
  queued: number;
  status?: number;
  line: string;
}

/** Rows in the store-sync backstop's dead-letter substrate. A missing file is
 *  0 (no drops), never an error: the health line must not fail because a
 *  bookkeeping file is absent. */
export function countStoreSyncDeadLetters(dir: string): number {
  try {
    const path = join(dir, "dead-letter.jsonl");
    if (!existsSync(path)) return 0;
    return readFileSync(path, "utf8").split("\n").filter((l) => l.trim()).length;
  } catch {
    return 0;
  }
}

/**
 * The per-box capture health line.
 *
 * `storeSyncDir` names the store-sync cursor store, whose DEAD LETTERS are
 * included in the line: a permanent refusal there crosses a per-file cursor, so
 * those turns can never re-ship — and the count otherwise exists only in the
 * worker's receipt, which the extension throws away (`stdio: "ignore"`). A
 * count nothing reads is a silent drop; this is the one line a user sees.
 */
export interface SweepStatus {
  ok: boolean;
  reason?: string;
  /** When the record was written (unix seconds). Consumed for PRECEDENCE: a
   *  record at or after this sweep's spawn time is the CLI's own name for the
   *  failure, and the extension must not replace it with its exit code. */
  at?: number;
}

/**
 * The last sweep's outcome, as the CLI persisted it.
 *
 * The extension spawns the worker with `stdio: "ignore"`, so the CLI's stderr —
 * where every post-spawn failure is named (`worker_status`, `no_receipt`, a
 * non-zero exit) — is discarded. Without this the box reports `capture: OK`
 * while the crash/kill recovery is inert: a dead backstop indistinguishable
 * from a working one. `undefined` means "no sweep has reported yet", which is
 * NOT a failure and must never be rendered as one.
 */
export function readSweepStatus(dir: string): SweepStatus | undefined {
  try {
    const path = join(dir, "sweep-status.json");
    if (!existsSync(path)) return undefined;
    const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
    const rec = parsed as Record<string, unknown>;
    const ok = rec.ok === true;
    const reason = typeof rec.reason === "string" && rec.reason ? rec.reason : undefined;
    const at = typeof rec.at === "number" && Number.isFinite(rec.at) ? rec.at : undefined;
    return { ok, reason, at };
  } catch {
    return undefined;
  }
}

/**
 * Record a sweep failure the CLI could not record itself.
 *
 * `sweep-status.json` is written by the CLI's own `main`, so a failure that
 * happens BEFORE it runs — `node` too old to parse the shipped worker, `node`
 * removed, a truncated artifact — writes NOTHING, and whatever an earlier
 * healthy sweep left stays on disk. That stale `ok: true` would render
 * `capture: OK` over a backstop that just died, which is the exact silent death
 * this seam exists to end. The extension is the only observer of the exit code,
 * so it records the failure rather than merely warning about it.
 *
 * Best-effort by contract: bookkeeping must never fail a sweep.
 */
export function recordSweepFailure(storeDir: string, reason: string): void {
  try {
    // 0o700/0o600 like every other `~/.tortoise` writer: the directory is
    // created here on the primary path (the CLI never ran, so it does not exist
    // yet), and a umask-derived 0755 would be the one place in the tree that is
    // world-readable.
    mkdirSync(storeDir, { recursive: true, mode: 0o700 });
    // `mkdirSync`'s `mode` applies ONLY when it creates the directory. The other
    // way this directory comes to exist is an EARLIER generation of this
    // extension, which created it with no mode at all (0755) — so on every
    // upgraded box the 0700 intent was silently unmet, permanently. Narrow the
    // existing one too; best-effort, inside the same try.
    try {
      chmodSync(storeDir, 0o700);
    } catch {
      // Windows / a directory we do not own: the write below still decides.
    }
    const payload = { version: 1, ok: false, reason, shipped: 0, at: Date.now() / 1000 };
    const tmp = join(storeDir, "sweep-status.json.tmp");
    writeFileSync(tmp, JSON.stringify(payload), { mode: 0o600 });
    renameSync(tmp, join(storeDir, "sweep-status.json"));
  } catch {
    // Nothing else to do: the health line simply keeps its previous reading.
  }
}

export function captureHealthLine(
  dir: string,
  opts: { apiKey?: string; storeSyncDir?: string } = {},
): CaptureHealth {
  const base = spoolHealthLine(dir, opts);
  // The store is DERIVED from the spool root (both live under `~/.tortoise/`)
  // rather than from `homedir()`: a caller that hands in a temp spool — every
  // test does — must not read the machine's real cursor store (#3721).
  const storeDir = opts.storeSyncDir ?? join(dirname(dir), "store-sync");
  const deadLettered = countStoreSyncDeadLetters(storeDir);
  const sweep = readSweepStatus(storeDir);
  const parts: string[] = [];
  if (deadLettered > 0) parts.push(`${deadLettered} store-sync dead-lettered`);
  // A sweep that RAN and failed is strictly worse than a dead-letter count: the
  // backstop is inert. Absence of the file is "not yet reported", never a fault.
  if (sweep && !sweep.ok) {
    parts.push(`store-sync FAILED${sweep.reason ? ` (${sweep.reason})` : ""}`);
  }
  if (!parts.length) return base;
  return { ...base, line: `${base.line}, ${parts.join(", ")}` };
}

function spoolHealthLine(dir: string, opts: { apiKey?: string } = {}): CaptureHealth {
  const pending = countPendingEntries(dir);
  if (pending === undefined) {
    // A spool we cannot list is UNKNOWN, not empty. Reporting `OK` here would be
    // a false pass on the acceptance's own surface, and it would also let the
    // self-heal below destroy a durable block marker that a later, readable
    // process still needs.
    return {
      state: "unreadable",
      queued: 0,
      line: `capture: UNREADABLE (cannot list ${entriesDir(dir)})`,
    };
  }
  const queued = pending;
  let recorded = readCaptureStatus(dir);
  if (queued === 0 && recorded && recorded.state !== "ok") {
    // Self-healing: the block is over (or another leg filed everything). Never
    // keep a stale verdict on disk that no evidence supports.
    recorded = { version: 1, state: "ok", queued: 0, updated_at: new Date().toISOString() };
    writeCaptureStatus(dir, recorded);
  }
  // A READ-ONLY question (`repair: false`): a health line must not quarantine,
  // record or unlink the spool it is describing.
  const saturated = spoolSaturated(dir, DEFAULT_BOUNDS, {}, { repair: false });
  // A marker we cannot UNDERSTAND is not a marker — the same rule this file
  // applies to `terminal_failure` and to a corrupt backoff window. A hand-edited
  // or foreign `status.json` carrying `{"state": "blocked"}` (or a status that
  // is a JSON string) must not be able to report a block that nothing observed:
  // the safe reading is "no recorded block", which the spool count still
  // governs. `recordOutcome` always writes a NUMBER, so nothing is lost.
  const blockedStatus =
    recorded !== undefined &&
    recorded.state === "blocked" &&
    typeof recorded.status === "number" &&
    Number.isFinite(recorded.status) &&
    recorded.status > 0
      ? recorded.status
      : undefined;
  // ⛔ PRECEDENCE — `UNREADABLE` > `BLOCKED` > `SATURATED` > `INACTIVE` >
  // `DRAINING` > `OK`, and the order of these branches IS the precedence. A
  // recorded refusal outranks INACTIVE because it names a CAUSE the operator can
  // act on; SATURATED outranks INACTIVE deliberately, so a keyless box —
  // precisely the one that saturates — keeps the keyless fact in the SATURATED
  // line instead of replacing it. Before this branch existed the five states
  // could not say "refusing every new capture": a working backlog read DRAINING,
  // and a ceiling filled by TERMINAL entries (never retried, never counted) read
  // `OK (0 queued)` — byte-identical to a healthy box.
  if (blockedStatus !== undefined && queued > 0) {
    return {
      state: "blocked",
      queued,
      status: blockedStatus,
      // #2100: 402 has TWO categories on `/v1/sessions`, and the server says a
      // caller must not read the spend cap as "out of plan allowance"
      // (`tortoise/cohort_cost.py` — buying a bigger plan would not lift it).
      // `blocked` alone would name the status and lose the cause, so the
      // marker's own recorded category is reported when it has one.
      line: `capture: BLOCKED (${queued} queued, ${blockedStatus} ${refusalLabel(recorded.reason)})`,
    };
  }
  if (saturated) {
    // `queued` is the PENDING count, so a ceiling filled by terminal entries
    // reads `SATURATED (0 queued, …)` — true on both counts: nothing will be
    // retried, and nothing new will be admitted. `session drain` names the
    // terminal count and `--retry-terminal` re-arms them.
    return opts.apiKey === ""
      ? {
          state: "saturated",
          queued,
          line: `capture: SATURATED (no TORTOISE_API_KEY, ${queued} queued, refusing new captures)`,
        }
      : {
          state: "saturated",
          queued,
          line: `capture: SATURATED (${queued} queued, refusing new captures)`,
        };
  }
  if (opts.apiKey === "") {
    // The count is NOT dropped here: a keyless box is exactly the box whose
    // spool grows forever (nothing can file it), so the backlog must stay
    // visible on the line that explains why.
    return {
      state: "inactive",
      queued,
      line: `capture: INACTIVE (no TORTOISE_API_KEY, ${queued} queued)`,
    };
  }
  if (queued > 0) {
    return { state: "draining", queued, line: `capture: DRAINING (${queued} queued)` };
  }
  return { state: "ok", queued, line: "capture: OK (0 queued)" };
}

/**
 * #2100: the cause word in the BLOCKED line.
 *
 * `quota` is the plain-plan-allowance reading and the string operator docs and
 * this issue's acceptance name, so an unlabelled 402 (or the org's points
 * allowance) stays exactly that. A DIFFERENT recorded category is named as
 * itself — a spend cap reported as "quota" sends the owner to buy a plan that
 * cannot lift it.
 */
function refusalLabel(reason: string | undefined): string {
  if (!reason || reason === QUOTA_EXCEEDED_CODE || reason === "402") return "quota";
  return reason;
}

/** Read the discard ledger (observable, append-only). */
export function readDiscards(dir: string): DiscardRecord[] {
  const path = discardPath(dir);
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8")
    .split("\n")
    .filter((l) => l.trim())
    .flatMap((l) => {
      try {
        return [JSON.parse(l) as DiscardRecord];
      } catch {
        // a torn/partial ledger line must not take down the reader
        return [];
      }
    });
}

// ── Extension wiring ──────────────────────────────────────────────────────

export interface CaptureDeps {
  fetchImpl?: FetchLike;
  /**
   * Test seam for `resolveConfig`, mirroring `fetchImpl`: the environment and
   * the config path the resolver reads. Omitted in production (the installer
   * passes no deps), where `undefined` triggers `resolveConfig`'s own
   * defaults — `process.env` and `CONFIG_PATH`.
   *
   * The behavioral suite MUST inject both (#3721): the extension resolves its
   * credential from the ambient env / `~/.pi/agent/tortoise-config.json`, so
   * a developer machine that has either made the suite pass while asserting
   * the MACHINE, not the extension — and fail on a clean CI runner, where the
   * empty key short-circuits `post()` before the injected fetch is ever
   * called. Injecting the resolver's two INPUTS (never a stubbed config) keeps
   * the real co-source chain in the tested path.
   */
  env?: Env;
  configPath?: string;
  /**
   * Spool directory + ceilings. Injected by the behavioral suite so a test can
   * never touch (or file) the developer machine's real
   * `~/.tortoise/capture-spool` — the same ambient-state trap as #3721.
   */
  spoolDir?: string;
  bounds?: SpoolBounds;
  /** Clock seam for backoff-window tests. */
  now?: () => number;
  /**
   * #3515 piece 5: the recovery SWEEP seam. On `session_start` the extension
   * asks the store-sync backstop to drain Pi's own `.jsonl` files (the death
   * modes the in-process hook cannot see: SIGKILL, crash, closed terminal).
   *
   * Injected so the behavioral suite never spawns a real `tortoise` process,
   * and so a machine with no Python install reports the skip instead of
   * pretending the sweep ran. `undefined` in production means the real
   * detached spawn.
   */
  sweepImpl?: (excludeSessionId?: string) => void;
}

function log(message: string): void {
  // Pi surfaces extension stdout in its session log.
  console.log(`[tortoise-capture] ${message}`);
}

function warn(message: string): void {
  console.warn(`[tortoise-capture] ${message}`);
}

/**
 * Report a flush outcome — success lines on 2xx, every discard named.
 *
 * `continuing` says whether a follow-up pass is actually scheduled. It is a
 * PARAMETER, not a guess: the line is the operator's only account of what the
 * drain will do next, and both `session_shutdown` (the process is exiting) and
 * the last pass of a spent chain schedule nothing — a claim that the drain
 * "continues in the background" there is a false statement on exactly the
 * surface #2100 is about.
 */
function reportFlush(
  summary: FlushSummary,
  where: string,
  opts: { continuing?: boolean } = {},
): void {
  // Deferrals are reported too: a quota-blocked spool is otherwise a steady
  // state that logs NOTHING while captures sit unfiled (#4714 review). A
  // terminal-only spool is the same shape (#7708).
  if (summary.filed > 0 || summary.deferred > 0 || summary.heldByBackoff > 0 ||
      summary.terminal > 0 || summary.remaining > 0) {
    log(
      `spool flush (${where}): filed ${summary.filed}, deferred ${summary.deferred}, ` +
        `skipped ${summary.skipped} (${summary.heldByBackoff} waiting on backoff), ` +
        `held back ${summary.heldBack}, ` +
        `terminal ${summary.terminal} (kept, not retried; re-file with ` +
        `'tortoise session drain --retry-terminal')` +
        // #2100: a bounded pass that left work queued says so, and names why.
        // A quota stop does NOT continue in the background (there is nothing to
        // gain from a second refusal), so the line must not claim it does — a
        // diagnostic that states the wrong next step is worse than a terse one.
        (summary.remaining > 0
          ? summary.stopped === "quota"
            ? `, paused ${summary.remaining} (quota refused; not retried in this pass)`
            : opts.continuing
              ? `, paused ${summary.remaining} (${summary.stopped ?? "budget"}; continues in the background)`
              : `, paused ${summary.remaining} (${summary.stopped ?? "budget"}; left for the next opportunity)`
          : ""),
    );
  }
  for (const d of summary.discarded) {
    warn(`spool DISCARD ${d.session_id} — ${d.reason}${d.detail ? ` (${d.detail})` : ""}`);
  }
}

export default function tortoiseCapture(pi: ExtensionAPI, deps: CaptureDeps = {}): void {
  const cfg = resolveConfig(deps.env, deps.configPath ?? CONFIG_PATH);
  const doFetch: FetchLike = deps.fetchImpl ?? (fetch as unknown as FetchLike);
  const spoolDir =
    deps.spoolDir ?? process.env.TORTOISE_CAPTURE_SPOOL_DIR ?? SPOOL_DIR;
  // Fail-closed isolation guard (#3721 trap, one layer deeper): an extension
  // under `node --test` that reaches the DEFAULT spool would write — and FILE —
  // the developer machine's real captures. Tests must inject `spoolDir`.
  if (
    !deps.spoolDir &&
    !process.env.TORTOISE_CAPTURE_SPOOL_DIR &&
    process.env.NODE_TEST_CONTEXT
  ) {
    throw new Error(
      "tortoise-capture: tests must inject `spoolDir` (or set " +
        "TORTOISE_CAPTURE_SPOOL_DIR) — refusing to touch the real capture spool",
    );
  }
  const bounds = deps.bounds ?? DEFAULT_BOUNDS;
  const clock = (): number => (deps.now ? deps.now() : Date.now());
  const inFlight = new Set<string>();

  if (!cfg.apiKey) {
    warn(
      "no TORTOISE_API_KEY — capture is INACTIVE. Add it to your shell profile " +
        "and restart Pi from a new terminal (a /reload keeps the old environment).",
    );
  }

  /**
   * The cheap, frequent half of the cadence (#3963): snapshot the session into
   * the durable spool. Synchronous by design — a `turn_end` handler that
   * returned before the write landed would lose the very turn it captured when
   * the process is killed next. Never touches the network.
   */
  const spoolSnapshot = (ctx: {
    sessionManager?: {
      getEntries?: () => Array<Record<string, unknown>>;
      getSessionId?: () => string;
      getSessionFile?: () => string | undefined;
    };
    model?: unknown;
  }): void => {
    try {
      const manager = ctx?.sessionManager;
      const entries = (manager?.getEntries?.() ?? []) as Array<Record<string, unknown>>;
      const turns = extractTurns(entries);
      if (turns.length === 0) return;
      // #9599: the LOCAL spool key is an identity too. `getSessionId()` handing
      // back a whitespace-only id is truthy, so two different blank-id sessions
      // landed in ONE entry (keyed `sha256("   ")[:32]`) and the second REPLACED
      // the first's turns — measured, not theoretical. A blank therefore falls
      // through to the path-anchored id, exactly as an absent one does on the
      // Python leg. (The WIRE is normalised separately by `buildCapturePayload`:
      // it must name no session so the server mints one.)
      const sessionFile = manager?.getSessionFile?.();
      const sessionId = normaliseSessionId(manager?.getSessionId?.())
        ?? localSessionId(sessionFile);
      if (!sessionId) return;
      const { discards } = writeSpoolEntry(
        spoolDir,
        {
          sessionId,
          turns,
          source: sourceName(sessionFile),
          machineId: deriveMachineId(),
          model: modelLabel(ctx?.model),
          // #3516 §B: this IS the in-process hook, so the entry it spools
          // claims 'hook' — the drain later forwards it verbatim.
          captureLane: "hook",
          // #3516 §B / piece 12 row 1: the in-process recorder stamps its OWN
          // clock at turn observation, and carries NO source — piece 12's row
          // reads "none — the recorder always has a clock". Unix SECONDS, the
          // unit the floor compares in (`Date.now()` is ms; do not pass it raw).
          clientCapturedAt: Date.now() / 1000,
        },
        bounds,
      );
      for (const d of discards) {
        warn(`spool DISCARD ${d.session_id} — ${d.reason}${d.detail ? ` (${d.detail})` : ""}`);
      }
    } catch (err) {
      warn(`spool error: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // ── #2100: the bounded drain + the per-box health line ────────────────

  /**
   * Record what a pass observed, and say it out loud when it learned something
   * the operator needs: a quota refusal turns the box `BLOCKED` (durable, so the
   * NEXT process — every lane is one — does not have to re-POST to find out),
   * and any 2xx proves the quota is not refusing, so it clears the marker.
   *
   * A marker is only ever `blocked` or `ok`; `draining`/`inactive` are derived
   * from the spool and the credential on each health line, never stored.
   */
  const recordOutcome = (summary: FlushSummary): void => {
    try {
      const previous = readCaptureStatus(spoolDir);
      if (summary.quota) {
        writeCaptureStatus(spoolDir, {
          version: 1,
          state: "blocked",
          reason: summary.quota.code ?? `${summary.quota.status}`,
          status: summary.quota.status,
          ...(summary.quota.detail ? { detail: summary.quota.detail.slice(0, 500) } : {}),
          updated_at: new Date().toISOString(),
        });
      } else if (summary.attempted > 0) {
        // Some POST was ATTEMPTED and none of them was refused for quota, so the
        // recorded cause is superseded: the marker is the LAST OBSERVED
        // 402, not a permanent verdict. Without this, a box that was
        // quota-blocked yesterday and is now `ECONNREFUSED`/500ing keeps
        // telling its operator to buy points — the same misdiagnosis as #2100
        // in mirror image (found in review cycle 2; `updated_at` was written
        // and never read).
        //
        // Note what this does NOT claim: a REPLAY 2xx is accepted without the
        // points gate (`hosted_api`: `if (not session_existed or
        // retry_failed_capture) and not store_only`), so a 2xx is evidence the
        // server accepted a capture, not proof the allowance lifted. The window
        // is one pass wide: a still-exhausted quota refuses the NEXT attempt
        // and re-marks it in the same pass that attempted it.
        writeCaptureStatus(spoolDir, {
          version: 1,
          state: "ok",
          updated_at: new Date().toISOString(),
        });
      }
      // Say the state out loud whenever it MOVED: the start-of-session line was
      // printed from the marker this pass has just superseded, and leaving that
      // as the last thing the lane logged is the misdiagnosis the supersede
      // exists to prevent.
      if (summary.quota || (previous?.state === "blocked" && summary.attempted > 0)) {
        log(captureHealthLine(spoolDir, { apiKey: cfg.apiKey }).line);
      }
    } catch (err) {
      warn(`spool status error: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  /**
   * #2100: dispatch ONE bounded drain pass and return immediately.
   *
   * NOT awaited by any handler that gates a turn. The pass itself is bounded
   * (`FLUSH_MAX_ATTEMPTS`, `FLUSH_BUDGET_MS`); what it could not reach stays in
   * the spool and is continued in the BACKGROUND, on an `unref()`d timer so a
   * pending continuation can never keep a finished Pi process alive (the
   * mirror image of the same bug: a capture that pins the event loop open).
   */
  const drain = (
    where: string,
    filter: { excludeSessionId?: string } = {},
    chain = 0,
  ): void => {
    void flushSpool(cfg, {
      dir: spoolDir,
      fetchImpl: doFetch,
      now: clock(),
      bounds,
      inFlight,
      ...filter,
    })
      .then((summary) => {
        // The report is told whether a continuation is ACTUALLY scheduled — it
        // runs before the decision, so asking it to guess produced "continues in
        // the background" on the last pass of a spent chain (#2100 review 2).
        const willChain =
          summary.remaining > 0 && !summary.quota && chain + 1 < FLUSH_MAX_CHAIN;
        reportFlush(summary, where, { continuing: willChain });
        recordOutcome(summary);
        if (willChain) {
          const timer = setTimeout(
            () => drain(where, filter, chain + 1),
            FLUSH_FOLLOW_UP_DELAY_MS,
          );
          timer.unref?.();
        }
      })
      .catch((err) => warn(`spool flush error: ${err instanceof Error ? err.message : String(err)}`));
  };

  /**
   * #3515 piece 5 — invoke the store-sync backstop, DETACHED.
   *
   * Why a spawn and not an implementation here: the backstop already exists as
   * `tortoise/pi-hooks/store-sync.ts` (per-file cursor, branch window,
   * advance-on-2xx) and re-implementing it inside the extension would be the
   * second capture path this architecture exists to forbid. Why detached and
   * unref'd: `session_start` is AWAITED by Pi, so the sweep must not be on the
   * startup path — a network drain there is the #2100 failure (a lane that
   * looked dead while it drained).
   *
   * A machine with no `tortoise` CLI has nothing to sweep with, and says so
   * ONCE — never silently. (Pi itself always has `node`; a Python CLI is only
   * present on a machine that installed the package.)
   */
  const sweepStoreSync = (excludeSessionId?: string): void => {
    if (deps.sweepImpl) {
      try {
        deps.sweepImpl(excludeSessionId);
      } catch (err) {
        warn(`store-sync sweep (injected) failed: ${err instanceof Error ? err.message : String(err)}`);
      }
      return;
    }
    const storeDir = join(dirname(spoolDir), "store-sync");
    // ONE failure record per sweep, carrying the FIRST reason. A PATH-missing CLI
    // reports BOTH `error` (ENOENT) and `close` (code -2) and NO `exit` event at
    // all, so a recorder wired to `exit` alone never fires on the likeliest way
    // this backstop stops working. Record first, warn second: the warn is
    // deduplicated per process, the record must not be.
    const sweepStartedAt = Date.now() / 1000;
    // ONE tolerance for comparing a persisted `at` (whatever resolution its
    // writer had) with our millisecond spawn stamp, used by BOTH arms below.
    // `time.time()` is a float here, but Python documents one-second resolution
    // on some platforms, and a backwards clock step is possible — so an
    // exact-to-the-millisecond comparison can read a record THIS sweep wrote as
    // older. Deriving it once is the point: the two arms drifted apart once
    // already, and the arm that was left exact reintroduced the gen-11 defect it
    // was written to prevent.
    const CLOCK_SLACK_S = 1;
    let failureRecorded = false;
    const noteFailure = (reason: string): void => {
      if (failureRecorded) return;
      failureRecorded = true;
      // SUBORDINATE to the CLI's own diagnosis. The CLI writes its
      // `{ok: false, reason: "worker_missing"}` BEFORE it exits non-zero, and
      // `close` fires after that — so writing unconditionally replaced the one
      // thing this seam exists to name with an opaque `exit_1` on every GRACEFUL
      // failure. A record stamped at or after this spawn is this sweep's, and it
      // wins.
      const existing = readSweepStatus(storeDir);
      if (existing && !existing.ok && (existing.at ?? 0) >= sweepStartedAt - CLOCK_SLACK_S) return;
      recordSweepFailure(storeDir, reason);
    };
    try {
      // `--cursor-dir` is EXPLICIT on purpose: the CLI otherwise defaults to
      // `$HOME/.tortoise/store-sync` while both legs of the line read
      // `<spool-parent>/store-sync`, so under the documented
      // `TORTOISE_CAPTURE_SPOOL_DIR` override the writer and the readers
      // silently disagreed (the dead-letter count read 0, the failure degraded
      // to `exit_N`). Naming it makes them agree by construction — and it is a
      // no-op under the default, where the two paths are the same.
      const args = ["store-sync", "--no-spool", "--cursor-dir", storeDir];
      if (excludeSessionId) args.push("--exclude-session-id", excludeSessionId);
      const child = spawn(CANDIDATE_CLIS[0], args, { detached: true, stdio: "ignore" });
      child.on("error", () => {
        noteFailure("cli_missing");
        if (_SWEEP_UNAVAILABLE_WARNED) return;
        _SWEEP_UNAVAILABLE_WARNED = true;
        warn("store-sync sweep: no `tortoise` CLI on PATH — killed/crashed sessions "
          + "are only caught when it is installed (`tortoise store-sync`)");
      });
      // `close` (not `exit`) is the listener that covers EVERY end: it fires for
      // a child that ran and failed (`node` too old for the shipped `.ts` worker,
      // missing artifact, no receipt) AND for one that never started. `signal` is
      // the only diagnosis for a swept that was KILLED (OOM, shutdown): `code` is
      // null there, and `exit_null` would throw the reason away exactly where it
      // matters.
      child.on("close", (code, signal) => {
        if (code === 0) {
          // EXIT 0 IS NOT PROOF THE SWEEP RAN. This seam's whole premise is that
          // the status file is the truth, and the CLI's own bookkeeping write is
          // best-effort (`except OSError: pass`) — so a healthy-looking exit can
          // leave NO record, and the line then renders the PREVIOUS verdict over
          // a backstop that never swept. A `tortoise` on PATH that is not the
          // shipped CLI does the same. Corroborate with data already in hand.
          const after = readSweepStatus(storeDir);
          // A coarse-but-FRESH record must not read as older, or this branch
          // OVERWRITES a healthy status and reports `exit_0_without_status` on a
          // box whose backstop works, every sweep. A record from the previous
          // sweep is far older than `CLOCK_SLACK_S`, so the tolerance cannot
          // mask a genuinely stale verdict.
          if (!after || (after.at ?? 0) < sweepStartedAt - CLOCK_SLACK_S) {
            noteFailure("exit_0_without_status");
            if (_SWEEP_UNAVAILABLE_WARNED) return;
            _SWEEP_UNAVAILABLE_WARNED = true;
            warn("store-sync sweep exited 0 but wrote no fresh status — killed/crashed "
              + "sessions may not be recovered; run `tortoise store-sync --json`");
          }
          return;
        }
        const why = code === null ? `signal_${String(signal)}` : `exit_${String(code)}`;
        noteFailure(why);
        if (_SWEEP_UNAVAILABLE_WARNED) return;
        _SWEEP_UNAVAILABLE_WARNED = true;
        warn("store-sync sweep FAILED (" + why.replace("_", " ") + ") — killed/crashed "
          + "sessions are not being recovered; run `tortoise store-sync --json`");
      });
      child.unref();
    } catch {
      noteFailure("spawn_failed");
      if (_SWEEP_UNAVAILABLE_WARNED) return;
      _SWEEP_UNAVAILABLE_WARNED = true;
      warn("store-sync sweep could not be started");
    }
  };

  /** The one health line. Cheap enough to print per session start (a spool scan,
   *  no network), and the only surface that distinguishes a BLOCKED box from an
   *  idle one in a lane log that otherwise produced 259 B. */
  const logHealth = (): void => {
    try {
      log(captureHealthLine(spoolDir, { apiKey: cfg.apiKey }).line);
    } catch (err) {
      warn(`spool health error: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // #1727 T2-P1: the SERVER-VISIBLE install signal — the extension-on-load
  // probe the server's install-probe route documents. Fired per session start
  // (startup/reload/new/resume/fork), matching Claude's SessionStart hook.
  //
  // #3963: `session_start` is also the REPLAY OPPORTUNITY. A prior session that
  // was interrupted (SIGINT, laptop closed, a cancelled shutdown hook) left its
  // turns in the spool; they are filed here — cleanly, before this session has
  // produced a turn of its own.
  //
  // FIRE-AND-FORGET: Pi AWAITS this handler (AgentSession.bindExtensions →
  // `await emit("session_start")`), so awaiting the network would stall startup
  // on a blackholed endpoint. Both the probe and the replay are dispatched with
  // bounded budgets and report when they land.
  //
  // #2100: and the REPLAY ITSELF MOVES OFF THIS TICK. An `async` function runs
  // its body synchronously up to its first `await`, and `flushSpool` scans the
  // spool and reads turn logs before that point — so `void flush(...)` was still
  // charging a 320-entry spool to the handler Pi awaits. `setImmediate` is what
  // makes "never blocks turn start" true for the SYNC half too, not just for
  // the network half.
  pi.on("session_start", (_event, ctx) => {
    void postInstallProbe(cfg, doFetch)
      .then((res) => {
        if (res.ok) {
          log(`install probe recorded (harness=${HARNESS})`);
        } else if (res.detail === "no TORTOISE_API_KEY configured") {
          // Already warned at load; the probe is best-effort install telemetry.
        } else {
          warn(`install probe failed (${res.detail ?? res.status}) — capture status may stay "not yet observed"`);
        }
      })
      .catch((err) => {
        warn(`install probe error: ${err instanceof Error ? err.message : String(err)}`);
      });
    // EXCLUDE THE LIVE SESSION. On `/reload` Pi emits
    // `session_shutdown{reason:"reload"}` (skipped — see below) THEN
    // `session_start{reason:"reload"}` for the SAME session, which `turn_end`
    // has already spooled. Filing it here would make the server replay it
    // (`session_existed` → extraction skipped) and `filed_key` would mark it
    // done — so every post-reload decision is stored but NEVER extracted. The
    // replay opportunity is for sessions that are no longer live.
    const current = (ctx.sessionManager?.getSessionId?.() ?? "") as string;
    setImmediate(() => {
      logHealth();
      drain("session_start", { excludeSessionId: current || undefined });
      // #3515 piece 5: the recovery sweep over Pi's OWN session log. It runs
      // here because this is the one moment the product is guaranteed to be
      // alive and idle — and it is a SPAWN, not a scheduler: fires once per
      // start, detached, and never awaited (Pi awaits `session_start`, so a
      // sweep in the handler would put a network drain on the startup path).
      // `excludeSessionId` is load-bearing for the same reason the spool drain
      // excludes the live session: filing THIS session now would make the
      // server skip extraction for every turn after this point.
      sweepStoreSync(current || undefined);
    });
  });

  // The cheap capture cadence (#3963): after EVERY turn, extend the spool. This
  // is what survives an interrupt — the turn is durable before the next one
  // starts, so a kill can lose at most the in-flight turn.
  pi.on("turn_end", (_event, ctx) => {
    spoolSnapshot(ctx as never);
  });

  // An agent run ENDED: snapshot it, and drain any OTHER session still waiting
  // in the spool. The live session is deliberately excluded — the server
  // replays a succeeded `session_id` as a no-op, so filing a still-growing
  // conversation would freeze the capture at that snapshot and silently drop
  // every later turn.
  // #2100: `agent_end` is awaited by Pi too (`agent-session.js` → `await
  // emit('agent_end')`), and `flushSpool`'s body runs SYNCHRONOUSLY up to its
  // first `await` — the spool scan, the sort and the first turn read all happen
  // on the handler's own tick. `setImmediate` is what makes "this handler never
  // blocks a turn" true for that sync half as well (measured: ~23 ms for a
  // 320-entry spool). The snapshot above stays INLINE — that is the durability
  // guarantee this handler exists for.
  pi.on("agent_end", (_event, ctx) => {
    spoolSnapshot(ctx as never);
    const current = (ctx.sessionManager?.getSessionId?.() ?? "") as string;
    setImmediate(() => drain("agent_end", { excludeSessionId: current || undefined }));
  });

  // The session is ending: snapshot the FINAL turns (durability even if Pi is
  // killed mid-handler), then — for a real end — attempt the filing as the
  // final flush. On `/reload` the SAME session stays alive, so we spool but do
  // not file: a premature file would freeze the capture (see `agent_end`).
  //
  // #2100: this one IS awaited (Pi runs shutdown handlers before exit), so it is
  // the pass where the bounds matter most — an unbounded drain here held a
  // finishing process open for one POST timeout per queued entry.
  pi.on("session_shutdown", async (event, ctx) => {
    spoolSnapshot(ctx as never);
    if (event.reason === "reload") return;
    try {
      // #2100: THE LIVE SESSION GOES FIRST. `flushSpool` ranks oldest-first, so
      // a bounded pass over a spool holding >= FLUSH_MAX_ATTEMPTS older entries
      // never reaches the one session that ONLY shutdown can file — it stays
      // spooled, which is fine on a persistent box and a LOSS on an ephemeral
      // cell whose spool dies with it. One `onlySessionId` pass files it first;
      // the bounded pass then drains the rest as before.
      const live = (ctx.sessionManager?.getSessionId?.() ?? "") as string;
      const liveSummary = live
        ? await flushSpool(cfg, {
            dir: spoolDir,
            fetchImpl: doFetch,
            now: clock(),
            bounds,
            inFlight,
            onlySessionId: live,
          })
        : null;
      const summary = await flushSpool(cfg, {
        dir: spoolDir,
        fetchImpl: doFetch,
        now: clock(),
        bounds,
        inFlight,
      });
      const filedTotal = summary.filed + (liveSummary?.filed ?? 0);
      if (filedTotal > 0) {
        log(`captured session(s) → ${cfg.apiUrl} (filed ${filedTotal})`);
      }
      if (liveSummary) reportFlush(liveSummary, "session_shutdown (live session)", { continuing: false });
      // No continuation exists on this path — the process is exiting, so the
      // un-attempted entries are left for the NEXT session, and the line says so.
      reportFlush(summary, "session_shutdown", { continuing: false });
      // The general pass is the LATER evidence, so it writes last (a quota
      // refusal it saw must win over the live session's 2xx).
      if (liveSummary) recordOutcome(liveSummary);
      recordOutcome(summary);
    } catch (err) {
      warn(`capture error: ${err instanceof Error ? err.message : String(err)}`);
    }
  });
}
