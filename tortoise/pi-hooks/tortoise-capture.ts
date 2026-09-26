// tortoise-hook-version: 1
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
// Recording is ON by default (ToS-covered — the same default as the Claude
// hooks): the server refuses the capture POST with a 409 while the
// organization has agent sessions switched off (Memory sources > Agent
// sessions). There is deliberately NO `autoCapture`-style default-false flag —
// installing this extension IS the opt-in, so the shipped capture step can
// never silently do nothing.
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
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { homedir, hostname, userInfo } from "node:os";
import { basename, join } from "node:path";

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
/** Hosted per-turn stored window (tortoise _capture_turn_window). */
export const TURN_MAX_CHARS = 5000;
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
    turns.push({ role, content: text.slice(0, TURN_MAX_CHARS) });
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

/** The POST /v1/sessions payload — the same shape `tortoise session capture` sends. */
export function buildCapturePayload(args: {
  sessionId: string;
  turns: Turn[];
  source: string;
  machineId: string;
  model?: string;
}): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    harness: HARNESS,
    session_id: args.sessionId,
    source: args.source,
    conversation: args.turns,
    machine_id: args.machineId,
  };
  if (args.model) payload.model = args.model;
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
// byte bound is a size bound, not a clock: it discards the OLDEST entries when
// the spool would grow past its ceiling, and every discard is RECORDED with a
// reason (never a silent drop).
//
// Idempotency: `session_id` remains the SERVER's upsert key. On top of it the
// client derives a content-addressed capture key
// `sha256(session_id \0 sha256(turns))` — stable across replays, distinct when
// the conversation actually changed. A snapshot whose capture key was already
// filed short-circuits before the network, so replaying a spool twice files
// ONE session and issues ONE POST.

export const SPOOL_DIR = join(homedir(), ".tortoise", "capture-spool");
/**
 * Spool ceilings. Count AND bytes (the issue's contract). These are NOT a TTL —
 * see the #3870 note above.
 *
 * `SPOOL_MAX_ENTRY_BYTES` must exceed the SERVER's own legal maximum, or a
 * legal capture is discarded as oversized: the handler accepts `MAX_TURNS`
 * (500) turns of up to `TURN_MAX_CHARS` (5000) characters, and non-ASCII text
 * is up to 4 UTF-8 bytes per character → ~10 MB of JSON. 16 MiB leaves room for
 * the envelope. (A 4 MiB ceiling silently discarded legal CJK sessions.)
 */
export const SPOOL_MAX_ENTRIES = 1000;
export const SPOOL_MAX_TOTAL_BYTES = 256 * 1024 * 1024;
export const SPOOL_MAX_ENTRY_BYTES = 16 * 1024 * 1024;
/** Exponential backoff for TRANSIENT failures only (network / 5xx / retryable 4xx). */
export const RETRY_BASE_MS = 30_000;
export const RETRY_MAX_MS = 6 * 60 * 60 * 1000;
/** Mirrors the Python leg's `_MAX_ATTEMPTS`: the backoff saturates long before. */
export const MAX_ATTEMPTS = 64;

/** Discard reasons — every one is written to `discarded.jsonl` (observable). */
export const DISCARD_ENTRY_TOO_LARGE = "entry_too_large";
export const DISCARD_COUNT_EXCEEDED = "spool_count_exceeded";
export const DISCARD_BYTES_EXCEEDED = "spool_total_bytes_exceeded";
export const DISCARD_TRANSCRIPT_EMPTY = "transcript_empty";
export const DISCARD_CORRUPT = "corrupt_entry";
/** A filesystem error while writing the spool — recorded, never silent. */
export const DISCARD_WRITE_FAILED = "spool_write_failed";
/** The spool's own `entries/` listing cannot be read — every capture is invisible. */
export const DISCARD_SPOOL_UNREADABLE = "spool_unreadable";

export interface SpoolMeta {
  version: 1;
  session_id: string;
  harness: string;
  source: string;
  machine_id: string;
  model?: string;
  created_at: string;
  updated_at: string;
  turns_count: number;
  /** sha256 of the canonical turns — the dedup signal + the capture key's source. */
  content_digest: string;
  /** Stable client-generated idempotency key: sha256(session_id \0 content_digest). */
  capture_key: string;
  /** Consecutive TRANSIENT upload failures (permanent failures discard instead). */
  attempts: number;
  /** Epoch MILLISECONDS before which a retry must not be attempted. 0 = ready.
   *  Milliseconds in BOTH legs (they share this directory) — a seconds value
   *  written by one leg reads as "in backoff until the year 57000" to the other. */
  next_attempt_at_ms: number;
  /** capture_key of the last 2xx upload — a replay of identical content is a no-op. */
  filed_key?: string;
  filed_at?: string;
}

export interface DiscardRecord {
  at: string;
  session_id: string;
  capture_key?: string;
  reason: string;
  detail?: string;
}

export interface SpoolSnapshot {
  sessionId: string;
  turns: Turn[];
  source: string;
  machineId: string;
  model?: string;
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
  discarded: DiscardRecord[];
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
): { metas: SpoolMeta[]; discards: DiscardRecord[] } {
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
      const stem = name.replace(/\.meta\.json$/, "");
      discards.push(
        recordDiscard(dir, {
          session_id: `unreadable:${stem}`,
          reason: DISCARD_CORRUPT,
          detail: meta ? `meta has no usable session_id: ${path}` : `meta is not valid JSON: ${path}`,
        }),
      );
      // Remove BOTH halves: an orphaned turns log is invisible to the meta
      // scan, so it would never be counted or pruned and could grow the spool
      // past its byte ceiling forever.
      for (const orphan of [path, join(root, `${stem}.turns.jsonl`)]) {
        try {
          unlinkSync(orphan);
        } catch {
          // nothing more we can do — the record above is the observable signal
        }
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

/**
 * Record a discard — append to `discarded.jsonl` AND remove the entry files.
 * This is the "never a silent drop" contract: every removed/dropped entry has a
 * durable, greppable line naming the session and the reason.
 */
export function recordDiscard(
  dir: string,
  rec: { session_id: string; capture_key?: string; reason: string; detail?: string; at?: string },
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

  // Dedup: a snapshot that is byte-identical to what is stored already is a
  // no-op — no rewrite, no re-upload (incremental capture must not amplify
  // identical writes).
  const sameContent =
    prior !== undefined &&
    stored.length === snapshot.turns.length &&
    prior.content_digest === contentDigest(snapshot.turns);
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
    ...(prior?.filed_key && prior.content_digest === contentDigest(snapshot.turns)
      ? { filed_key: prior.filed_key, filed_at: prior.filed_at }
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
  // capture) is DISCARDED with a reason rather than written and forgotten.
  if (entryBytes > bounds.maxEntryBytes) {
    // `discardEntry` records the reason BEFORE removing the files. Unlinking by
    // hand first (the old code) left the SIGKILL / ENOSPC window open — in the
    // one branch that deletes a PREVIOUSLY SPOOLED entry.
    discards.push(
      discardEntry(
        dir,
        { session_id: snapshot.sessionId, capture_key: meta.capture_key } as SpoolMeta,
        DISCARD_ENTRY_TOO_LARGE,
        `entry is ${entryBytes} bytes > maxEntryBytes=${bounds.maxEntryBytes}`,
      ),
    );
    return { written: false, bytes: 0, discards };
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
 * Enforce the spool's COUNT and BYTE ceilings, evicting the oldest entries.
 * Excludes `keepSessionId` (the entry just written) so a write can never evict
 * itself. Returns one DiscardRecord per eviction, with the ceiling named.
 */
export function pruneSpool(
  dir: string,
  keepSessionId: string | undefined,
  bounds: SpoolBounds = DEFAULT_BOUNDS,
): DiscardRecord[] {
  const discards: DiscardRecord[] = [];
  const { metas } = listSpoolEntries(dir);
  const ranked = metas
    .filter((m) => m.session_id !== keepSessionId)
    .map((m) => ({ meta: m, bytes: spoolEntryBytes(dir, m.session_id) }))
    .sort((a, b) => compareAge(a.meta, b.meta));

  let count = metas.length;
  let total = metas.reduce((sum, m) => sum + spoolEntryBytes(dir, m.session_id), 0);
  for (const { meta, bytes } of ranked) {
    if (count <= bounds.maxEntries && total <= bounds.maxTotalBytes) break;
    const reason =
      total > bounds.maxTotalBytes ? DISCARD_BYTES_EXCEEDED : DISCARD_COUNT_EXCEEDED;
    const detail =
      reason === DISCARD_BYTES_EXCEEDED
        ? `spool is ${total} bytes > maxTotalBytes=${bounds.maxTotalBytes}`
        : `spool has ${count} entries > maxEntries=${bounds.maxEntries}`;
    discards.push(discardEntry(dir, meta, reason, detail));
    count -= 1;
    total -= bytes;
  }
  return discards;
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
 * turn. That bound is real but finite: sustained over-quota still evicts
 * oldest-first at the count/byte ceiling, with a recorded reason, so the two
 * legs describe the same policy (`capture_spool.py`).
 *
 * ⚠️ #4614 gave the refusal a machine-readable CATEGORY
 * (`detail.code === "quota_exceeded"`) so a caller no longer has to match the
 * message text. This classifier still keys on the STATUS, deliberately: the
 * category is for REPORTING and for surfaces that can act on it, and treating
 * a `quota_exceeded` 402 as terminal here would re-open #4714's data loss. The
 * two legs must keep answering this the same way (`capture_spool.py`).
 *
 * PERMANENT (discard + record): every other 4xx — a malformed payload or an
 * out-of-range turn count never becomes valid by waiting.
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

/**
 * Replay opportunity. For every spooled session whose content has not been
 * filed, attempt one POST. Transient failures back off; permanent 4xx are
 * discarded with a reason; entries in a backoff window are skipped (not lost).
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
    discarded: [],
  };

  const { metas, discards } = listSpoolEntries(dir);
  summary.discarded.push(...discards);
  const ranked = metas.sort(compareAge);

  for (const meta of ranked) {
    if (opts.onlySessionId && meta.session_id !== opts.onlySessionId) continue;
    if (opts.excludeSessionId && meta.session_id === opts.excludeSessionId) {
      summary.heldBack += 1;
      continue;
    }
    if (meta.filed_key && meta.filed_key === meta.capture_key) {
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
    busy?.add(meta.session_id);
    try {
      const turns = readSpoolTurns(dir, meta.session_id);
      if (turns.length === 0) {
        summary.discarded.push(
          discardEntry(dir, meta, DISCARD_TRANSCRIPT_EMPTY, "turn log is empty or unreadable"),
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
      });
      const res = await postCapture(cfg, payload, doFetch, opts.timeoutMs);
      if (res.ok) {
        // COMPARE-AND-SWAP, on the POSTED CONTENT. A `turn_end` can append to
        // this very entry while the POST is in flight (a resumed session; a
        // cross-process drain). Stamp `filed_key` only when what is on disk NOW
        // is exactly what was posted; otherwise leave the entry unfiled so the
        // next opportunity re-posts the longer conversation. Comparing
        // meta-to-meta was wrong: a writer killed between its log append and its
        // meta write leaves the meta digest stale, so the check passed while a
        // turn went unfiled forever.
        const onDisk = readSpoolEntry(dir, meta.session_id);
        const onDiskTurns = readSpoolTurns(dir, meta.session_id);
        if (onDisk && contentDigest(onDiskTurns) === postedDigest) {
          onDisk.filed_key = onDisk.capture_key ?? captureKey(meta.session_id, onDiskTurns);
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
        summary.discarded.push(
          discardEntry(
            dir,
            meta,
            `permanent_http_${res.status ?? "none"}`,
            res.detail ?? "permanent client error",
          ),
        );
        continue;
      }
      // Re-read before the backoff write-back for the same reason as the CAS
      // above: never clobber newer turns written while the POST was in flight.
      const pending = readSpoolEntry(dir, meta.session_id) ?? meta;
      if (pending.filed_key && pending.filed_key === pending.capture_key) {
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
}

function log(message: string): void {
  // Pi surfaces extension stdout in its session log.
  console.log(`[tortoise-capture] ${message}`);
}

function warn(message: string): void {
  console.warn(`[tortoise-capture] ${message}`);
}

/** Report a flush outcome — success lines on 2xx, every discard named. */
function reportFlush(summary: FlushSummary, where: string): void {
  // Deferrals are reported too: a quota-blocked spool is otherwise a steady
  // state that logs NOTHING while captures sit unfiled (#4714 review).
  if (summary.filed > 0 || summary.deferred > 0 || summary.heldByBackoff > 0) {
    log(
      `spool flush (${where}): filed ${summary.filed}, deferred ${summary.deferred}, ` +
        `skipped ${summary.skipped} (${summary.heldByBackoff} waiting on backoff), ` +
        `held back ${summary.heldBack}`,
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
      const sessionId = manager?.getSessionId?.() ?? "";
      if (!sessionId) return;
      const { discards } = writeSpoolEntry(
        spoolDir,
        {
          sessionId,
          turns,
          source: sourceName(manager?.getSessionFile?.()),
          machineId: deriveMachineId(),
          model: modelLabel(ctx?.model),
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

  const flush = (where: string, filter: { excludeSessionId?: string } = {}): void => {
    void flushSpool(cfg, {
      dir: spoolDir,
      fetchImpl: doFetch,
      now: clock(),
      bounds,
      inFlight,
      ...filter,
    })
      .then((summary) => reportFlush(summary, where))
      .catch((err) => warn(`spool flush error: ${err instanceof Error ? err.message : String(err)}`));
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
  pi.on("session_start", (_event, ctx) => {
    void postInstallProbe(cfg, doFetch)
      .then((res) => {
        if (res.ok) {
          log(`install probe recorded (harness=${HARNESS})`);
        } else if (res.detail === "no TORTOISE_API_KEY configured") {
          // Already warned at load; the probe is best-effort install telemetry.
        } else {
          warn(`install probe failed (${res.detail ?? res.status}) — capture status may stay "not installed yet"`);
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
    flush("session_start", { excludeSessionId: current || undefined });
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
  pi.on("agent_end", (_event, ctx) => {
    spoolSnapshot(ctx as never);
    const current = (ctx.sessionManager?.getSessionId?.() ?? "") as string;
    flush("agent_end", { excludeSessionId: current || undefined });
  });

  // The session is ending: snapshot the FINAL turns (durability even if Pi is
  // killed mid-handler), then — for a real end — attempt the filing as the
  // final flush. On `/reload` the SAME session stays alive, so we spool but do
  // not file: a premature file would freeze the capture (see `agent_end`).
  pi.on("session_shutdown", async (event, ctx) => {
    spoolSnapshot(ctx as never);
    if (event.reason === "reload") return;
    try {
      const summary = await flushSpool(cfg, {
        dir: spoolDir,
        fetchImpl: doFetch,
        now: clock(),
        bounds,
        inFlight,
      });
      if (summary.filed > 0) {
        log(`captured session(s) → ${cfg.apiUrl} (filed ${summary.filed})`);
      }
      reportFlush(summary, "session_shutdown");
    } catch (err) {
      warn(`capture error: ${err instanceof Error ? err.message : String(err)}`);
    }
  });
}
