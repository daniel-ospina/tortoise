/**
 * #3515 — the store-sync BACKSTOP: Pi's own session log as the durable source.
 *
 * THE HALF IT IS. The in-process hook (`tortoise-capture.ts`) is the promptness
 * + attribution half: it sees `agent_end`/`turn_end` and knows which session,
 * which host, which model. It cannot help a session that died without firing
 * it — SIGKILL, a crashed process, a closed terminal. Nothing in-process is
 * durable against those.
 *
 * For those sessions the artifact this module reads is Pi's own session
 * `.jsonl`. This module reads that file with a per-file cursor
 * (`cursor-store.ts`) and ships
 * it as an ordinary capture — `harness: "pi"`, `capture_lane: "store_sync"` —
 * through the SAME payload builder and POST the hook uses. There is
 * deliberately NO second capture implementation here: `buildCapturePayload` /
 * `extractTurns` / `postCapture` are imported, so the two lanes cannot drift
 * into two contracts.
 *
 * DURABILITY POSTURE IS NOT STATED HERE. What this substrate does and does not
 * give you — the measured SIGKILL behaviour, the absent `fsync`, the
 * memory-only prefix before the first assistant message — belongs to
 * `docs/durability-posture.md`, which is the authority for every such claim in
 * this tree. It is linked, not restated, so the two cannot drift.
 *
 * WHAT IT IS NOT. It is not a scheduler and not a daemon. It runs when it is
 * invoked — at `session_start` (the recovery sweep, bounded), and from
 * `tortoise store-sync`. Polling is an explicit non-goal: a per-file cursor
 * plus a sweep at start covers every death mode the hook misses, and a poller
 * would be machinery built around behaviour instead of making the behaviour
 * trustworthy.
 *
 * Pinned rules this file implements, each with its reason:
 *
 *  - **Advance-on-2xx only** (piece 2). Never on read, never on any non-2xx.
 *    One deliberate exception, stated so the invariant is not quietly weaker
 *    than it reads: a file whose window holds NO shippable turn yet (nothing
 *    but a user message) advances past its complete lines WITHOUT a POST. That
 *    is not a delivery, so nothing is lost — and it stays true only because the
 *    turn window is re-derived from the WHOLE file on every run (`scanSessionFile`
 *    reads from 0). Shipping a delta instead would make this lossy.
 *  - **A partial trailing line is never consumed and never lost** (§E): it is
 *    excluded from this pass and the cursor stays BEHIND it, so the same line
 *    ships whole on the next run. Advancing past it would ship half a JSON
 *    object (a 4xx at best) or drop a real turn (permanent loss).
 *  - **`cursor > file_size` is a RE-READ, never a silent skip** (piece 9).
 *    Pi rewrites the file in place on a version migration or a label edit
 *    (`_rewriteFile` truncates then rewrites), so a shorter-than-cursor file is
 *    the normal degenerate case, not an anomaly. Re-ship is absorbed by the
 *    server's idempotent turn MERGE; a silent skip is not recoverable.
 *  - **`session_id` comes ONLY from the on-disk header `id`** (piece 6). A
 *    fork/resume mints a NEW file with a NEW filename UUID, so generating an
 *    id here would either double-write one conversation as two Sessions or
 *    mis-attribute turns.
 *  - **The tree is walked, not the byte stream** (piece 13). Pi's log is a
 *    tree (`id`/`parentId`); a byte cursor over a branched session ships
 *    abandoned branches as live. The window is the chain ending at the last
 *    entry, cut at a `compaction` entry's `firstKeptEntryId`.
 *  - **A permanent 4xx is dead-lettered, not retried forever** (piece 11) —
 *    otherwise one bad record head-of-line-blocks every later turn with no
 *    surfaced error. `PERMANENT` means the BYTES are refused; a CREDENTIAL
 *    refusal (401/403/407) is retained like 402, because crossing the cursor
 *    past it would destroy exactly the sessions this backstop exists for.
 *  - **A window may never SHRINK below what this file already delivered.**
 *    A shrink is not only a compaction: Pi's log is a TREE, so appending an
 *    entry whose `parentId` points back past the old leaf makes the active
 *    branch shorter while the FILE grows. Either way the turn store's
 *    shorter-re-capture rule hard-DELETES the turns beyond the new window, so a
 *    shrinking window is REFUSED (cursor retained, counted) rather than shipped.
 *    The length arm has ONE exception: a RE-ANCHORED plan (truncate-then-rewrite,
 *    dev/ino or header change) reset the cursor to 0 and is reading the file as a
 *    new one, so what was delivered before is not the same index space. It is
 *    still refused if it ALSO hits the unproven arm below (a window carrying a
 *    `compaction` entry this lane cannot prove it has delivered anything behind).
 *  - **Bounded per run** (piece 10): files SHIPPED, requests, and attempts. An
 *    up-to-date file is skipped from its `stat`, its header line and at most a
 *    64 KiB tail read (`completeThroughCheap`), so a sweep does not read whole
 *    session files to discover there is nothing to do. The file COUNT is
 *    deliberately not capped: examination is O(1) per file, and a cap there
 *    would starve later files behind an alphabetically-early backlog.
 */
import { createHash } from "node:crypto";
import {
  closeSync,
  existsSync,
  openSync,
  readFileSync,
  readdirSync,
  readSync,
  statSync,
} from "node:fs";
import { join } from "node:path";

import {
  buildCapturePayload,
  deriveMachineId,
  extractTurns,
  postCapture,
  resolveConfig,
  sourceName,
} from "./tortoise-capture.ts";
import type { Turn } from "./tortoise-capture.ts";
import {
  type CursorState,
  type DeadLetter,
  type FileCursor,
  cursorPath,
  fileIdentity,
  loadCursor,
  recordDeadLetter,
  readDeadLetters,
  saveCursor,
} from "./cursor-store.ts";

export const HARNESS = "pi";
export const LANE = "store_sync";

/** Bounded retry (piece 3): the schedule IS the bound — attempts == entries. */
export const BACKOFF_MS: readonly number[] = [1000, 2500, 5000, 10000, 20000];
export const MAX_ATTEMPTS = BACKOFF_MS.length;

/** Bounded sweep (piece 10): a start-of-day outage must not turn into a
 *  thundering herd against the server, and a long outage must not grow the
 *  work without limit. The remainder is drained by the NEXT run, by design. */
export const MAX_FILES_PER_RUN = 50;
export const MAX_REQUESTS_PER_RUN = 200;

/** HTTP codes that will never succeed for the same bytes. Retrying them
 *  forever is the head-of-line block piece 11 exists to end. */
export const PERMANENT_STATUS = new Set([400, 404, 405, 406, 413, 415, 422]);
/** 402 is the quota ceiling: it is a REFUSAL OF THE MOMENT, not of the bytes,
 *  so it is retryable (retained) rather than dead-lettered. 408/425/429 are
 *  timing, and 409 is "already in flight" — a concurrent producer writing the
 *  SAME turns, so dead-lettering it would drop what the other lane is filing. */
export const RETAIN_STATUS = new Set([402, 408, 409, 425, 429]);
/** 401/403/407 are the CREDENTIAL/entitlement refusals. They pass the file's
 *  own test for retention — the refusal is of the MOMENT and of the CALLER,
 *  never of the bytes — so they are retained, NOT dead-lettered. The permanent
 *  branch CROSSES the cursor, which for a session that has ended (the only
 *  kind this backstop recovers: an active file keeps growing) is final: the
 *  turns can never be re-shipped and nothing reports a failure. A rotated or
 *  expired key would then destroy exactly the sessions the backstop exists
 *  for. Retained means the fixed key re-ships them.
 */
export const AUTH_STATUS = new Set([401, 403, 407]);

export type FetchLike = (
  input: string,
  init?: Record<string, unknown>,
) => Promise<{ ok: boolean; status: number; json: () => Promise<unknown> }>;

// ── Pure cursor math (unit-tested by store-sync.test.mjs) ──────────────────

export interface CompleteLines {
  /** Raw text of each COMPLETE line, in order. */
  lines: string[];
  /** Byte offset of the END of the last complete line — the new cursor. When
   *  the tail is partial this is the START of that partial line, so the next
   *  pass re-reads it whole. */
  nextOffset: number;
  /** True when the buffer ended mid-line. */
  partial: boolean;
}

/**
 * Split `buf` from byte `offset` into COMPLETE lines.
 *
 * The partial-tail rule is the whole point: `nextOffset` never crosses a
 * line's terminating newline, so a half-written JSON object is neither parsed
 * (garbage) nor skipped (loss) — it is simply still ahead of the cursor.
 */
export function readCompleteLines(buf: Buffer, offset: number): CompleteLines {
  const lines: string[] = [];
  const start = Math.max(0, Math.min(offset, buf.length));
  let cursor = start;
  let lastEnd = start;
  while (cursor < buf.length) {
    const nl = buf.indexOf(0x0a, cursor);
    if (nl === -1) break;
    lines.push(buf.toString("utf8", cursor, nl));
    cursor = nl + 1;
    lastEnd = cursor;
  }
  const partial = lastEnd < buf.length;
  return {
    lines: lines.filter((l) => l.trim().length > 0),
    nextOffset: lastEnd,
    partial,
  };
}

export interface ScanResult {
  /** The header `id`. `undefined` when the first line is not a session header. */
  headerId?: string;
  /** sha256 of the header line — detects a replace under the same path. */
  headerSha?: string;
  /** Parsed entries: complete lines only. */
  entries: Array<Record<string, unknown>>;
  /** Index in `entries` of the header (0 for a well-formed Pi file). */
  headerIndex: number;
  partial: boolean;
  nextOffset: number;
}

/** Read a session file from byte 0 as entries. Used to build the turn WINDOW
 *  (a window is a whole-file property: a branch can be abandoned by a later
 *  entry, which no incremental delta can reveal on its own). */
export function scanSessionFile(path: string): ScanResult {
  const buf = readFileSync(path);
  const chunk = readCompleteLines(buf, 0);
  const entries: Array<Record<string, unknown>> = [];
  let headerIndex = -1;
  let headerId: string | undefined;
  let headerSha: string | undefined;
  let seen = 0;
  // Re-derive line offsets so the header sha is over the header LINE, not the
  // whole prefix.
  const lines = buf.toString("utf8").split("\n");
  for (const raw of lines) {
    seen += Buffer.byteLength(raw, "utf8") + 1;
    if (seen > chunk.nextOffset) break; // the partial tail (or EOF)
    const t = raw.trim();
    if (!t) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(t);
    } catch {
      continue; // a malformed interior line is skipped; it cannot be re-read
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) continue;
    const rec = parsed as Record<string, unknown>;
    if (headerIndex === -1 && rec.type === "session") {
      headerIndex = entries.length;
      if (typeof rec.id === "string" && rec.id) headerId = rec.id;
      headerSha = createHash("sha256").update(t).digest("hex");
    }
    entries.push(rec);
  }
  return {
    headerId,
    headerSha,
    entries,
    headerIndex,
    partial: chunk.partial,
    nextOffset: chunk.nextOffset,
  };
}

export interface WindowEntry {
  id?: string;
  parentId?: string;
  type: string;
  entry: Record<string, unknown>;
}

/**
 * THE SHARED BRANCH CONTRACT (piece 13) — both lanes select a window the same way.
 *
 * 1. The ACTIVE LEAF is the LAST entry carrying an `id`. (Pi advances the leaf
 *    on every append; abandoned branches never become the leaf again without a
 *    new append, which then IS the newest entry.)
 * 2. The window is the chain leaf → root via `parentId`, in file order.
 * 3. A `compaction` entry on the chain CUTS the window at its
 *    `firstKeptEntryId`: everything before that id was summarized, and Pi no
 *    longer sends it as context.
 *
 * Entries with no `id` cannot be positioned in the tree. They are dropped from
 * the window — never assigned a positional id (that would re-mint the R8
 * defect: a synthesized id that disagrees with the file).
 */
export function activeBranch(entries: Array<Record<string, unknown>>): WindowEntry[] {
  const byId = new Map<string, WindowEntry>();
  let leafId: string | undefined;
  for (const entry of entries) {
    const id = typeof entry.id === "string" ? entry.id : undefined;
    if (!id) continue;
    const parentId = typeof entry.parentId === "string" ? entry.parentId : undefined;
    byId.set(id, { id, parentId, type: String(entry.type ?? ""), entry });
    leafId = id; // file order: the last id IS the active leaf
  }
  if (!leafId) return [];
  const chain: string[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined = leafId;
  while (cursor && byId.has(cursor) && !seen.has(cursor)) {
    seen.add(cursor);
    chain.push(cursor);
    cursor = byId.get(cursor)?.parentId;
  }
  chain.reverse();
  const window = chain.map((id) => byId.get(id) as WindowEntry);
  return cutAtCompaction(window);
}

/** Apply rule 3 of the shared contract. `firstKeptEntryId` names an EARLIER
 *  entry on the chain — everything between the compaction and that entry was
 *  summarized, so the window starts AT the kept entry. */
export function cutAtCompaction(window: WindowEntry[]): WindowEntry[] {
  let startAt = 0;
  for (let i = 0; i < window.length; i += 1) {
    if (window[i].type !== "compaction") continue;
    const kept = window[i].entry.firstKeptEntryId;
    if (typeof kept !== "string" || !kept) continue;
    const keptIndex = window.findIndex((w) => w.id === kept);
    if (keptIndex >= 0) startAt = Math.max(startAt, keptIndex);
  }
  return window.slice(startAt);
}

/** Turns for a WINDOW, through the SHIPPED extractor (clipping + tail cap), so
 *  the two lanes cannot produce different turn text. */
export function windowTurns(window: WindowEntry[]): Turn[] {
  return extractTurns(window.map((w) => w.entry));
}

/** The newest entry timestamp in the window, in unix SECONDS (piece 12: the
 *  store-sync lane's clock source is the log's OWN timestamp). */
export function windowCapturedAt(window: WindowEntry[]): number | undefined {
  for (let i = window.length - 1; i >= 0; i -= 1) {
    const ts = window[i].entry.timestamp;
    if (typeof ts !== "string") continue;
    const ms = Date.parse(ts);
    if (Number.isFinite(ms)) return Math.floor(ms / 1000);
  }
  return undefined;
}

export type PlanAction = "ship" | "skip" | "reanchor";

/** Enough bytes for a Pi session header line, and small enough that reading it
 *  costs nothing next to a full session file. */
const HEADER_READ_BYTES = 8192;

export interface HeaderScan {
  /** The header `id`, from the FIRST line only (Pi writes it first). */
  headerId?: string;
  /** sha256 of that line — the same value `scanSessionFile` records. */
  headerSha?: string;
  /** True when the first line is not terminated inside the read window. */
  partial: boolean;
}

/**
 * Read ONLY the header line — everything `planFile` needs to decide skip vs
 * ship/re-anchor.
 *
 * `scanSessionFile` reads the WHOLE file and JSON.parses every line, and a
 * `session_start` sweep runs over the user's entire session history: the
 * measured tree on a working box is gigabytes, re-read on every start and
 * multiplied by concurrent sessions. A file whose cursor is already at its
 * size needs neither read. The header is line 1 in Pi's own output, so one
 * small positional read answers the identity question; the full scan is paid
 * for only when the plan says ship or re-anchor.
 */
/**
 * The byte offset just past the LAST COMPLETE line, read from the file's TAIL.
 *
 * A sweep must not read a whole session tree to discover that a file is up to
 * date — and a torn trailing line is the ORDINARY state of a session whose
 * process was killed, so the cheap path has to know where the last newline is
 * without a full scan. A file ending in a newline is complete through its size;
 * otherwise the position comes from the last 64 KiB.
 *
 * `undefined` means the window held no newline at all (a single entry larger
 * than the window). That falls back to the full scan rather than guessing —
 * guessing high would cross a complete line, which is silent loss.
 */
export function completeThroughCheap(path: string, size: number): number | undefined {
  const WINDOW = 64 * 1024;
  const start = Math.max(0, size - WINDOW);
  const len = size - start;
  if (len <= 0) return 0;
  const buf = Buffer.alloc(len);
  const fd = openSync(path, "r");
  let read = 0;
  try {
    read = readSync(fd, buf, 0, len, start);
  } finally {
    closeSync(fd);
  }
  const nl = buf.subarray(0, Math.max(0, read)).lastIndexOf(0x0a);
  if (nl === -1) return start === 0 ? 0 : undefined;
  return start + nl + 1;
}

export function scanHeaderLine(path: string): HeaderScan {
  const fd = openSync(path, "r");
  let read = 0;
  const buf = Buffer.alloc(HEADER_READ_BYTES);
  try {
    read = readSync(fd, buf, 0, buf.length, 0);
  } finally {
    closeSync(fd);
  }
  const slice = buf.subarray(0, Math.max(0, read));
  const nl = slice.indexOf(0x0a);
  if (nl === -1) return { partial: true };
  const text = slice.subarray(0, nl).toString("utf8").trim();
  if (!text) return { partial: false };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { partial: false };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { partial: false };
  const rec = parsed as Record<string, unknown>;
  if (rec.type !== "session") return { partial: false };
  return {
    headerId: typeof rec.id === "string" && rec.id ? rec.id : undefined,
    // HAShed exactly as `scanSessionFile` does (`t` is the trimmed line).
    headerSha: createHash("sha256").update(text).digest("hex"),
    partial: false,
  };
}

export interface FilePlan {
  action: PlanAction;
  offset: number;
  /** Recorded reason for a re-anchor — never silent (piece 9). */
  reason?: string;
}

/** Decide what to do with one file, from the cursor + the file's identity. */
export function planFile(
  cursor: FileCursor | undefined,
  identity: { size: number; dev: number; ino: number },
  observed: { headerId?: string; headerSha?: string; completeThrough?: number },
): FilePlan {
  if (!cursor) return { action: "ship", offset: 0, reason: "no cursor" };
  if (
    cursor.dev !== undefined &&
    cursor.ino !== undefined &&
    (cursor.dev !== identity.dev || cursor.ino !== identity.ino)
  ) {
    return { action: "reanchor", offset: 0, reason: "file_replaced" };
  }
  if (
    observed.headerSha !== undefined &&
    cursor.headerSha !== undefined &&
    observed.headerSha !== cursor.headerSha
  ) {
    return { action: "reanchor", offset: 0, reason: "header_changed" };
  }
  if (
    observed.headerId !== undefined &&
    cursor.sessionId !== undefined &&
    observed.headerId !== cursor.sessionId
  ) {
    return { action: "reanchor", offset: 0, reason: "session_id_changed" };
  }
  if (cursor.offset > identity.size) {
    // Truncated / rotated / rewritten in place. NEVER a silent skip.
    return { action: "reanchor", offset: 0, reason: "cursor_past_eof" };
  }
  // A torn trailing line makes `offset === size` UNREACHABLE — and that is the
  // ordinary artifact of the SIGKILL this backstop exists for. `size` is not
  // the position a cursor can occupy; the end of the last COMPLETE line is.
  // Comparing against `size` alone therefore re-ships the identical window on
  // EVERY sweep, forever, for a dead session: its file never grows, so its tail
  // never completes. Nothing new is ahead of the cursor, so nothing ships; the
  // fragment ships whole once it completes (the file grows, and `completeThrough`
  // advances past it).
  if (observed.completeThrough !== undefined && cursor.offset >= observed.completeThrough) {
    return { action: "skip", offset: cursor.offset };
  }
  if (cursor.offset === identity.size) return { action: "skip", offset: cursor.offset };
  return { action: "ship", offset: cursor.offset };
}

// ── The worker ────────────────────────────────────────────────────────────

export interface SyncOptions {
  sessionDir: string;
  cursorDir: string;
  /** Explicit files to consider (default: every `*.jsonl` under sessionDir). */
  files?: string[];
  maxFiles?: number;
  maxRequests?: number;
  apiKey?: string;
  apiUrl?: string;
  fetchImpl?: FetchLike;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  log?: (line: string) => void;
  /** Report only — read the files and the cursor, POST nothing. */
  dryRun?: boolean;
  /** Skip the session that is LIVE right now (#3515 piece 5). Filing a live
   *  session makes the server skip extraction for every later turn, the same
   *  reason the spool drain excludes it. Matched on the FILENAME (which carries
   *  the same UUID as the header id), so no file has to be read to exclude it. */
  excludeSessionId?: string;
}

export interface FileOutcome {
  path: string;
  sessionId?: string;
  action: PlanAction | "failed" | "dead_letter" | "partial";
  offset: number;
  delivered: number;
  status?: number;
  reason?: string;
  attempts: number;
}

export interface RunReceipt {
  files: FileOutcome[];
  shipped: number;
  skipped: number;
  /** Files whose window holds no shippable turn yet (nothing is lost: the
   *  window is re-derived from the whole file on every run). Counted
   *  separately from `skipped`, which means "already up to date" — otherwise a
   *  sweep that examined real work reads as "0 shipped, 0 up to date". */
  noTurns: number;
  /** Files REFUSED because their window is shorter than what was already
   *  delivered — a `/compact` cut OR a tree append that re-points the branch at
   *  a shorter chain. Counted, never silent. */
  shrunk: number;
  dropped: number;
  deadLettered: number;
  requests: number;
  cursorRecovered?: string;
  cursorWriteError?: string;
  deadLetters: DeadLetter[];
  errors: string[];
  dryRun: boolean;
}

function defaultListFiles(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string, depth: number): void => {
    if (depth > 3) return;
    let names: string[];
    try {
      names = readdirSync(dir).sort();
    } catch {
      return;
    }
    for (const name of names) {
      const full = join(dir, name);
      let st;
      try {
        st = statSync(full);
      } catch {
        continue;
      }
      if (st.isDirectory()) walk(full, depth + 1);
      else if (name.endsWith(".jsonl")) out.push(full);
    }
  };
  walk(root, 0);
  return out;
}

/** Classify a response for the cursor decision. Only a 2xx advances it. */
export function classifyStatus(status: number | undefined): "advance" | "permanent" | "retain" {
  if (status === undefined) return "retain"; // transport failure — retryable
  if (status >= 200 && status < 300) return "advance";
  // 409 is ambiguous on this API: "already in flight" is benign and the
  // payload may land later; a shape 409 never will. The seam treats 409 as
  // retryable (a 409 from a concurrent producer must NOT be dead-lettered —
  // the same turns are being written by the other lane), so it is a member of
  // RETAIN_STATUS and the next run re-files.
  if (RETAIN_STATUS.has(status)) return "retain";
  // A credential/entitlement refusal is not a refusal of the BYTES: retaining
  // keeps the cursor where it is, so a fixed key re-ships (never a cross).
  if (AUTH_STATUS.has(status)) return "retain";
  if (PERMANENT_STATUS.has(status)) return "permanent";
  if (status >= 400 && status < 500) return "permanent";
  return "retain"; // 5xx / anything else: the moment, not the bytes
}

export async function runStoreSync(options: SyncOptions): Promise<RunReceipt> {
  const now = options.now ?? (() => Date.now());
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((res) => setTimeout(res, ms)));
  const log = options.log ?? (() => {});
  const dryRun = options.dryRun === true;
  const receipt: RunReceipt = {
    files: [],
    shipped: 0,
    skipped: 0,
    noTurns: 0,
    shrunk: 0,
    dropped: 0,
    deadLettered: 0,
    requests: 0,
    deadLetters: [],
    errors: [],
    dryRun,
  };

  const loaded = loadCursor(options.cursorDir);
  if (loaded.recovered && loaded.error) {
    receipt.cursorRecovered = loaded.error;
    log(`store-sync: ${loaded.error}`);
  }
  const state: CursorState = loaded.state;

  let cfg: { apiKey?: string; apiUrl: string } = {
    apiKey: options.apiKey,
    apiUrl: options.apiUrl ?? "",
  };
  if (!dryRun && (!cfg.apiKey || !cfg.apiUrl)) {
    const resolved = resolveConfig();
    cfg = {
      apiKey: options.apiKey ?? resolved.apiKey,
      apiUrl: options.apiUrl ?? resolved.apiUrl,
    };
  }
  const machineId = deriveMachineId();
  if (!dryRun && !cfg.apiKey) {
    // A cause that cannot change inside this run. Retrying it would burn the
    // whole bounded schedule (5 attempts, ~18.5 s) PER FILE on a process the
    // extension spawns on every session start — and report success.
    receipt.errors.push("no TORTOISE_API_KEY configured — the log half shipped nothing");
    const saved = saveCursor(options.cursorDir, state);
    if (!saved.ok) receipt.cursorWriteError = saved.error;
    receipt.deadLetters = readDeadLetters(options.cursorDir);
    return receipt;
  }

  const excludeSessionId = options.excludeSessionId;
  const candidates = (options.files ?? defaultListFiles(options.sessionDir))
    .filter((p) => existsSync(p))
    .filter((p) => !excludeSessionId || !p.includes(excludeSessionId));
  const maxFiles = options.maxFiles ?? MAX_FILES_PER_RUN;
  const maxRequests = options.maxRequests ?? MAX_REQUESTS_PER_RUN;

  // The cap bounds FILES SHIPPED per run (piece 10: "max records per run,
  // drained across runs"), not files EXAMINED — capping the examined prefix
  // would re-examine the same up-to-date prefix forever and starve the tail.
  let shippedThisRun = 0;
  for (const path of candidates) {
    if (receipt.requests >= maxRequests) break;
    if (shippedThisRun >= maxFiles) break;
    // Progress is persisted PER FILE: the enclosing CLI kills the worker on a
    // timeout, and a single end-of-run save would discard every advance made
    // before the kill — the sweep would then never get past its first file.
    const checkpoint = (): void => {
      const r = saveCursor(options.cursorDir, state);
      if (!r.ok && !receipt.cursorWriteError) receipt.cursorWriteError = r.error;
    };
    const identity = fileIdentity(path);
    if (!identity) continue;
    const prior = state.files[path];
    // Read the HEADER LINE ONLY first: a sweep runs over the user's whole
    // session history, and an up-to-date file must not be read (or JSON.parsed
    // line by line) to discover that. The full scan below is paid for only
    // when the plan says ship or re-anchor.
    let head: HeaderScan;
    try {
      head = scanHeaderLine(path);
    } catch (err) {
      receipt.errors.push(`${path}: ${err instanceof Error ? err.message : String(err)}`);
      continue;
    }
    const headPlan = planFile(prior, identity, {
      headerId: head.headerId,
      headerSha: head.headerSha,
      // Cheap, and load-bearing for COST rather than correctness: without it a
      // file whose tail is torn can never satisfy `offset === size`, so every
      // sweep would full-read it to conclude there is nothing to do.
      completeThrough: completeThroughCheap(path, identity.size),
    });
    if (headPlan.action === "skip" && head.headerSha !== undefined) {
      // The cheap path: `stat` identity + the header sha already agree and the
      // cursor is exactly at the file's size. Nothing to read, nothing to POST.
      receipt.skipped += 1;
      receipt.files.push({
        path,
        sessionId: head.headerId ?? prior?.sessionId,
        action: "skip",
        offset: headPlan.offset,
        delivered: prior?.delivered ?? 0,
        attempts: 0,
        reason: "up_to_date",
      });
      continue;
    }
    let scan: ScanResult;
    try {
      scan = scanSessionFile(path);
    } catch (err) {
      receipt.errors.push(`${path}: ${err instanceof Error ? err.message : String(err)}`);
      continue;
    }
    // The FULL scan is authoritative for the plan: it can find a session header
    // that is not the first line, which the header-only read cannot — and it is
    // the only place that knows where the last COMPLETE line ends, which is the
    // position a cursor can actually occupy (see planFile's torn-tail note).
    const plan = planFile(prior, identity, {
      headerId: scan.headerId,
      headerSha: scan.headerSha,
      completeThrough: scan.nextOffset,
    });
    if (plan.reason && plan.reason !== "no cursor") log(`store-sync: ${path}: ${plan.reason}`);
    if (plan.action === "skip") {
      receipt.skipped += 1;
      receipt.files.push({
        path,
        sessionId: scan.headerId,
        action: "skip",
        offset: plan.offset,
        delivered: prior?.delivered ?? 0,
        attempts: 0,
        reason: "up_to_date",
      });
      continue;
    }
    // Piece 6: the session id comes from the header, ONLY.
    const sessionId = scan.headerId;
    if (!sessionId && scan.headerIndex === -1 && (scan.partial || identity.size === 0)) {
      // The header line itself is half-written (a kill during Pi's
      // truncate-then-rewrite), OR the file is EMPTY — which is the normal
      // on-disk state between that truncate and the rewrite, and between an
      // explicit `--session` file being created and its first write. A 0-byte
      // buffer has no partial line to detect (`lastEnd(0) < len(0)` is false), so
      // `scan.partial` alone missed it and the sweep reported a HARD ERROR —
      // `receipt.errors` non-empty, `main()` exit 1 — on an ordinary transient. That is the partial-tail rule, not a defect:
      // defer it and leave the cursor where it is. A permanently red sweep for
      // one torn historical file would be noise, not signal.
      receipt.files.push({
        path, action: "partial", offset: plan.offset, delivered: 0, attempts: 0,
        reason: "header_incomplete",
      });
      continue;
    }
    if (!sessionId) {
      receipt.errors.push(`${path}: no session header id — refused (never invented)`);
      receipt.files.push({
        path,
        action: "failed",
        offset: plan.offset,
        delivered: 0,
        attempts: 0,
        reason: "no_header_id",
      });
      continue;
    }
    const window = activeBranch(scan.entries);
    const turns = windowTurns(window);
    const priorDelivered = prior?.delivered ?? 0;
    if (!turns.length) {
      // Nothing shippable yet (only a user message so far, or an empty
      // branch). A real run advances the cursor past the complete lines it did
      // read: there is no turn to lose, and re-reading them buys nothing.
      receipt.noTurns += 1;
      if (dryRun) {
        // A dry run is REPORT-ONLY — `tortoise store-sync --dry-run` is
        // documented as "POST nothing, move no cursor", so it must not create
        // the cursor store or move an offset either.
        receipt.files.push({
          path,
          sessionId,
          action: scan.partial ? "partial" : "skip",
          offset: plan.offset,
          delivered: priorDelivered,
          attempts: 0,
          reason: "no_turns",
        });
        continue;
      }
      const advance: FileCursor = {
        offset: scan.nextOffset,
        sessionId,
        dev: identity.dev,
        ino: identity.ino,
        headerSha: scan.headerSha,
        delivered: priorDelivered,
        lastWindow: prior?.lastWindow,
        maxWindow: prior?.maxWindow ?? prior?.lastWindow,
        updatedAt: now(),
      };
      state.files[path] = advance;
      checkpoint();
      receipt.files.push({
        path,
        sessionId,
        action: scan.partial ? "partial" : "skip",
        offset: scan.nextOffset,
        delivered: priorDelivered,
        attempts: 0,
        reason: "no_turns",
      });
      continue;
    }

    // THE SHRINK GUARD. A `compaction` entry cuts the window at
    // `firstKeptEntryId`, so a post-`/compact` window is STRICTLY SHORTER than
    // what this file already delivered. The turn store's shorter-re-capture
    // rule then hard-deletes every turn beyond the new window (#1920,
    // journaled as a delete) — so shipping it would DESTROY turns the hook
    // already filed. Refuse: retain the cursor, count it, and say why. The
    // hook lane never shrinks its window (Pi's entry array keeps the
    // pre-compaction entries), so this refusal costs nothing it would ship.
    const compacted = window.some((w) => w.type === "compaction");
    // THE HIGH-WATER MARK, not `lastWindow`. `lastWindow` only says how big the
    // PREVIOUS window was, so on a FIRST ship (`prior` undefined) it is 0 and the
    // guard could not fire — which is precisely how production invokes the
    // backstop: a session that was compacted before generation 8 ever ran on
    // this machine has no cursor, and a live session is excluded from every
    // sweep it starts, so its first sweep is after compaction. Shipping that
    // short window would let the turn store's shorter-re-capture rule
    // hard-delete the turns the HOOK already filed.
    //
    // So: a window carrying a `compaction` entry that this lane cannot PROVE it
    // has delivered something for is refused outright — the hook owns the
    // pre-compaction turns. The refusal is counted (`shrunk`) and logged, never
    // silent. A `maxWindow` high-water mark then keeps the guard honest across
    // refusals and rewinds, where `lastWindow` resets.
    //
    // "Proven" is NOT "a cursor row exists". Two ordinary paths write a row that
    // proves nothing — the `noTurns` advance and a first POST that is RETAINED
    // (402 quota, offline) — both at `delivered: 0` with no window recorded. A
    // `prior === undefined` test therefore stops guarding the moment either
    // happens, and a quota block that lifts after the user compacts would ship
    // the short window and hard-delete the hook's turns. The high-water mark and
    // the delivered count are the only two fields a real ship always sets
    // (`lastWindow = turns.length >= 1`), so they are what unproven means.
    const priorWindow = Math.max(prior?.maxWindow ?? 0, prior?.lastWindow ?? 0);
    const unproven = compacted && priorWindow === 0 && (prior?.delivered ?? 0) === 0;
    // `compacted` gates the UNPROVEN arm only, NOT the length arm. The invariant
    // this guard states is "a window may never shrink below what this file
    // already delivered", and a shrink is not a property of compaction: Pi's log
    // is a TREE, so appending an entry whose `parentId` points back past the old
    // leaf makes the active branch SHORTER while the file GROWS. A shipped
    // shrink is the same hard DETACH DELETE the guard exists to prevent.
    //
    // `reanchored` is the exception, and it is not a loophole: when the plan
    // re-anchored (`cursor_past_eof`, dev/ino, header sha/id), the cursor was
    // reset to 0 and this file is being read as a NEW file, so "what was
    // delivered" is not the same index space. That is exactly Pi's
    // `_rewriteFile` (truncate, then rewrite for a migration or a label edit) —
    // following it is the pinned behaviour of
    // `a TRUNCATED file is re-read from the header, not skipped forever`.
    //
    // The length arm cannot fire on legitimate GROWTH: `extractTurns` tail-caps
    // at MAX_TURNS, so `turns.length` saturates and is non-decreasing for a file
    // that only appends. A refusal is the safe direction — the cursor is
    // retained and the refusal is counted (`shrunk`), never silent.
    const reanchored = plan.action === "reanchor";
    if (unproven || (!reanchored && turns.length < priorWindow)) {
      // A dry run reports the refusal WITHOUT persisting it (see the dry-run
      // contract above): the report is the whole product of that mode.
      if (!dryRun) {
        if (unproven) {
          // STICKY BY CONSTRUCTION, and this is load-bearing. A compacted file
          // this lane has never shipped stays UNPROVEN, so the guard must keep
          // firing on every sweep. Writing a cursor record here would make the
          // NEXT run look like it had a prior, `priorWindow` would read 0, and
          // the short window would ship — the exact deletion this guard exists
          // to prevent, one run later. The row that would have caught it is the
          // DRY RUN below (it re-reads the same state).
          delete state.files[path];
          checkpoint();
        } else {
          state.files[path] = {
            offset: plan.offset,
            sessionId,
            dev: identity.dev,
            ino: identity.ino,
            headerSha: scan.headerSha,
            delivered: priorDelivered,
            lastWindow: priorWindow,
            maxWindow: priorWindow,
            updatedAt: prior?.updatedAt ?? 0,
            attemptedAt: now(),
            // `failures` is the POST retry schedule's position, so a REFUSAL
            // must not spend it: a refusal is not a failed POST, and since the
            // length arm now fires on any branch re-point (not only an
            // occasional `/compact`), incrementing here would let a handful of
            // routine tree-append refusals saturate the counter and leave the
            // first legitimate ship with ONE attempt instead of the full
            // schedule. Carried forward untouched.
            failures: prior?.failures ?? 0,
          };
          checkpoint();
        }
      }
      receipt.shrunk += 1;
      receipt.files.push({
        path,
        sessionId,
        action: "failed",
        offset: plan.offset,
        delivered: turns.length,
        attempts: 0,
        reason: "window_shrunk",
      });
      log(
        `store-sync: REFUSED a shrunk window for ${sessionId} ` +
          `(${turns.length} turns vs ${priorWindow || "an unknown"} already held — ` +
          `${compacted ? "a /compact cut" : "a shorter branch"}` +
          "; shipping it would delete the turns past the new window)",
      );
      continue;
    }

    const payload = buildCapturePayload({
      sessionId,
      turns,
      source: sourceName(path),
      machineId,
      model: undefined,
      // Piece 8: the persisted lane is the ONLY discriminator between the two
      // lanes — without it the hook-liveness check greens with a dead hook.
      captureLane: LANE,
      // Piece 12: the log's own timestamp; recorded under its own source so the
      // floor can tell this clock from an mtime or an observation instant.
      clientCapturedAt: windowCapturedAt(window),
      clientCapturedAtSource: "entry_timestamp",
    });

    if (dryRun) {
      receipt.shipped += 1;
      receipt.files.push({
        path,
        sessionId,
        action: "ship",
        offset: plan.offset,
        delivered: turns.length,
        attempts: 0,
        reason: "dry_run",
      });
      continue;
    }

    let attempts = 0;
    let lastStatus: number | undefined;
    let lastError: string | undefined;
    let decision: "advance" | "permanent" | "retain" = "retain";
    let downgradedSource = false;
    // Piece 11: N restarts must not MULTIPLY retries. A file that has already
    // failed `failures` times resumes at that attempt rather than restarting the
    // schedule from 0 — otherwise every restart pays the full budget again.
    const startAttempt = Math.min(prior?.failures ?? 0, BACKOFF_MS.length - 1);
    for (let i = startAttempt; i < BACKOFF_MS.length; i += 1) {
      if (receipt.requests >= maxRequests) break;
      attempts += 1;
      receipt.requests += 1;
      const res = await postCapture(cfg, payload, (options.fetchImpl ?? (fetch as unknown)) as never);
      lastStatus = res.ok ? 200 : res.status;
      lastError = res.ok ? undefined : res.detail;
      decision = classifyStatus(res.ok ? 200 : res.status);
      if (
        !res.ok &&
        downgradedSource === false &&
        typeof res.detail === "string" &&
        res.detail.includes("client_captured_at_source")
      ) {
        // DEPLOY-ORDER TOLERANCE, and nothing more. The worker and the server
        // that must accept `entry_timestamp` live in ONE repo but ship on
        // different clocks (the extension is installed on user machines; the
        // API deploys). A server that has not learned the member answers 422,
        // and treating that as permanent would DEAD-LETTER a real session — the
        // exact loss this backstop exists to prevent. So the clock claim
        // degrades to the honest gap the spec already defines for a turn whose
        // clock cannot be named (`unknown` ⇒ the timestamp floor is DISABLED for
        // this record, never silently passed) and the retry carries the turns.
        // One downgrade per run per file: a second refusal is a real failure.
        downgradedSource = true;
        payload.client_captured_at_source = "unknown";
        decision = "retain";
        log("store-sync: server does not know client_captured_at_source=entry_timestamp — " +
          "re-sending with 'unknown' (the floor is DISABLED for this record, not passed)");
        // The downgraded RE-SEND is not a schedule attempt, so it must not
        // CONSUME one. `startAttempt` resumes at the persisted `failures`, which
        // saturates at the ceiling (`BACKOFF_MS.length - 1`) after any outage of
        // a few sweeps — 402 quota, 401/403/407, 409, 429, 5xx and transport all
        // retain and all increment it. At the ceiling this loop body runs exactly
        // ONCE, so `continue` ended the loop with `decision` still "retain": the
        // downgraded payload was never sent, forever, which is precisely the
        // state the tolerance was written for. Rewind one slot (at most once per
        // run — `downgradedSource` is the guard) so the re-send happens on the
        // same budget slot.
        i = Math.max(i - 1, 0);
        continue;
      }
      if (decision !== "retain" || res.ok) break;
      if (i < BACKOFF_MS.length - 1) await sleep(BACKOFF_MS[i]);
    }

    if (decision === "advance") {
      state.files[path] = {
        offset: scan.nextOffset,
        sessionId,
        dev: identity.dev,
        ino: identity.ino,
        headerSha: scan.headerSha,
        delivered: (prior?.delivered ?? 0) + turns.length,
        // What THIS window delivered: the shrink guard compares the next window
        // against it, so a `/compact` cut is detectable without a re-read.
        lastWindow: turns.length,
        // Monotone: the most this file has EVER delivered, which is the most the
        // server may already hold. Never lowered by a rewind or a refusal.
        maxWindow: Math.max(prior?.maxWindow ?? 0, prior?.lastWindow ?? 0, turns.length),
        updatedAt: now(),
        attemptedAt: now(),
        failures: 0,
      };
      shippedThisRun += 1;
      checkpoint();
      receipt.shipped += 1;
      receipt.files.push({
        path,
        sessionId,
        action: "ship",
        offset: scan.nextOffset,
        delivered: turns.length,
        status: lastStatus,
        attempts,
      });
      log(`store-sync: shipped ${turns.length} turns of ${sessionId}`);
      continue;
    }

    if (decision === "permanent") {
      // Piece 11: dead-letter + CROSS the record, so one permanently refused
      // file cannot head-of-line-block every later turn. The drop is counted.
      recordDeadLetter(options.cursorDir, {
        path,
        sessionId,
        offset: plan.offset,
        status: lastStatus,
        reason: lastError ?? `HTTP ${lastStatus}`,
        at: now(),
      });
      state.files[path] = {
        offset: scan.nextOffset,
        sessionId,
        dev: identity.dev,
        ino: identity.ino,
        headerSha: scan.headerSha,
        delivered: prior?.delivered ?? 0,
        lastWindow: prior?.lastWindow,
        maxWindow: prior?.maxWindow ?? prior?.lastWindow,
        updatedAt: now(),
        attemptedAt: now(),
        failures: (prior?.failures ?? 0) + 1,
      };
      shippedThisRun += 1;
      receipt.deadLettered += 1;
      checkpoint();
      receipt.dropped += turns.length;
      receipt.files.push({
        path,
        sessionId,
        action: "dead_letter",
        offset: scan.nextOffset,
        delivered: turns.length,
        status: lastStatus,
        reason: lastError,
        attempts,
      });
      continue;
    }

    // Retained: the cursor does NOT move. The batch survives the restart, and
    // the attempt counter is persisted so N restarts do not multiply retries
    // (piece 11) — the next run starts its schedule where this one left off
    // rather than resetting to attempt 0 with a fresh post.
    state.files[path] = {
      offset: plan.offset,
      sessionId,
      dev: identity.dev,
      ino: identity.ino,
      headerSha: scan.headerSha,
      delivered: prior?.delivered ?? 0,
      lastWindow: prior?.lastWindow,
      maxWindow: prior?.maxWindow ?? prior?.lastWindow,
      updatedAt: prior?.updatedAt ?? 0,
      attemptedAt: now(),
      failures: (prior?.failures ?? 0) + 1,
    };
    receipt.files.push({
      path,
      sessionId,
      action: "failed",
      offset: plan.offset,
      delivered: turns.length,
      status: lastStatus,
      reason: lastError ?? "retained (cursor not advanced)",
      attempts,
    });
    log(`store-sync: retained ${turns.length} turns of ${sessionId} (${lastError ?? "retained"})`);
    checkpoint();
  }

  // A dry run is REPORT-ONLY: it must not create the cursor store or rewrite
  // any offset (the CLI advertises "POST nothing, move no cursor").
  if (!dryRun) {
    // `prune` drops rows for session files that no longer exist, so the cursor
    // store cannot grow without bound and every checkpoint stays O(live files).
    const saved = saveCursor(options.cursorDir, state, { prune: true });
    if (!saved.ok) receipt.cursorWriteError = saved.error;
  }
  receipt.deadLetters = readDeadLetters(options.cursorDir);
  return receipt;
}

// ── CLI ───────────────────────────────────────────────────────────────────

export const RECEIPT_SENTINEL = "RECEIPT_JSON:";

export function parseArgs(argv: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = { files: [] as string[] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const take = (): string | undefined => {
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) return undefined;
      i += 1;
      return next;
    };
    if (arg === "--session-dir") out.sessionDir = take();
    else if (arg === "--cursor-dir") out.cursorDir = take();
    else if (arg === "--file") {
      const v = take();
      if (v) (out.files as string[]).push(v);
    } else if (arg === "--max-files") out.maxFiles = Number(take());
    else if (arg === "--max-requests") out.maxRequests = Number(take());
    else if (arg === "--exclude-session-id") out.excludeSessionId = take();
    else if (arg === "--api-url") out.apiUrl = take();
    // The credential is normally passed through the ENVIRONMENT by the Python
    // CLI (`TORTOISE_API_KEY`), so it never appears in `ps`. The flag exists
    // for a caller that has no environment to hand.
    else if (arg === "--api-key") out.apiKey = take();
    else if (arg === "--dry-run") out.dryRun = true;
    else if (arg === "--json") out.json = true;
  }
  return out;
}

export async function main(argv: string[]): Promise<number> {
  const args = parseArgs(argv);
  const sessionDir = (args.sessionDir as string | undefined) ??
    join(process.env.HOME ?? "", ".pi", "agent", "sessions");
  const cursorDir = (args.cursorDir as string | undefined) ??
    join(process.env.HOME ?? "", ".tortoise", "store-sync");
  const files = args.files as string[];
  const receipt = await runStoreSync({
    sessionDir,
    cursorDir,
    files: files.length ? files : undefined,
    maxFiles: Number.isFinite(args.maxFiles as number) ? (args.maxFiles as number) : undefined,
    maxRequests: Number.isFinite(args.maxRequests as number)
      ? (args.maxRequests as number)
      : undefined,
    apiUrl: args.apiUrl as string | undefined,
    apiKey: args.apiKey as string | undefined,
    excludeSessionId: args.excludeSessionId as string | undefined,
    dryRun: args.dryRun === true,
    log: (line) => process.stderr.write(`${line}\n`),
  });
  process.stdout.write(`${RECEIPT_SENTINEL}${JSON.stringify(receipt)}\n`);
  // Exit 0 unless the store-sync backstop is STRUCTURALLY unable to work: a
  // retained batch is a normal, honest state (the outage is not the CLI's
  // error), and a dead-letter is reported in the receipt.
  return receipt.errors.length ? 1 : 0;
}

/** `ttys`-safe entry: the Python CLI (`tortoise store-sync`) spawns this file
 *  directly with `node`, so `argv[1]` ends with its own path. */
export function invokedDirectly(argv1: string | undefined): boolean {
  if (!argv1) return false;
  return /store-sync\.(ts|mjs|js)$/.test(argv1);
}

if (invokedDirectly(process.argv[1])) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (err) => {
      process.stderr.write(`store-sync: ${err instanceof Error ? err.stack : String(err)}\n`);
      process.exit(1);
    },
  );
}
