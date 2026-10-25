/**
 * #3515 piece 2 — the per-file cursor store for the store-sync backstop.
 *
 * The store-sync worker (§ the hybrid in
 * `docs/architecture/PI-CAPTURE-ARCHITECTURE.md`) reads Pi's own session
 * `.jsonl` and ships its turns. To resume where it stopped, it keeps ONE
 * cursor per session FILE. The cursor is the *only* durable memory of the
 * backstop, so the rules here are the ones that decide whether a restart
 * re-emits or skips:
 *
 *  1. **Advance-on-2xx only.** `saveCursor` is called by the worker ONLY after
 *     the server answered 2xx for that file's window. Advancing on read (or on
 *     a failed POST) silently degrades at-least-once to at-most-once — those
 *     turns are dropped forever with no error anywhere.
 *  2. **Atomic write.** `tmp` + `fsync` + `rename`, so a crash mid-write can
 *     never leave a half-written `cursor.json` that a later run parses as a
 *     short offset (which would re-ship) or a long one (which would skip).
 *  3. **A last-good backup.** The previous good cursor is kept as
 *     `cursor.json.bak` BEFORE the rename, so a corrupt main file fails
 *     CLOSED — to the last good offset, with the recovery recorded — rather
 *     than to offset 0 (silent duplicate re-ship) or to EOF (silent loss).
 *  4. **Absolute offsets, never counts.** A line count is not a position: Pi
 *     rewrites the file on a version migration / label edit (`_rewriteFile`),
 *     which keeps the line count and changes the bytes.
 *
 * The store is a DIRECTORY (not one file) because it also holds the
 * dead-letter substrate (piece 11) and the honest status counters.
 */
import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

export const CURSOR_VERSION = 1;
export const CURSOR_FILE = "cursor.json";
export const CURSOR_BACKUP = "cursor.json.bak";
export const DEAD_LETTER_FILE = "dead-letter.jsonl";

/** One session file's position. `offset` is a BYTE offset into the file. */
export interface FileCursor {
  /** Bytes consumed — always the end of a COMPLETE line (never mid-line). */
  offset: number;
  /** The header `id` observed at this offset (piece 6: from the header ONLY). */
  sessionId?: string;
  /** `stat.dev` at the time the cursor was written (file identity). */
  dev?: number;
  /** `stat.ino` — together with `dev`, detects a file REPLACED under the path. */
  ino?: number;
  /** sha256 of the header line, so a replace under the same id is visible. */
  headerSha?: string;
  /** How many turns this file has shipped — an honest, monotone counter. */
  delivered?: number;
  /** How many turns the LAST shipped window carried. The store-sync worker
   *  refuses to ship a window SHORTER than this after a `/compact` cut: the
   *  turn store's shorter-re-capture rule would hard-delete the turns past the
   *  new window, so the shrink is detectable for free and must not be shipped. */
  lastWindow?: number;
  /** MONOTONE high-water mark: the largest window this file has ever shipped.
   *  `lastWindow` answers "how big was the last window"; this answers "how much
   *  may the SERVER already hold". They differ after a refusal (which keeps
   *  `lastWindow` and `offset` put) and after a rewind, and the difference is
   *  what makes the shrink guard hold on the FIRST ship of a file that was
   *  compacted before this lane ever ran — the ordinary upgrade path. */
  maxWindow?: number;
  /** Unix ms of the last successful advance. */
  updatedAt?: number;
  /** Unix ms of the last attempt (2xx or not) — drives the backoff floor. */
  attemptedAt?: number;
  /** Consecutive failures, reset on success. Bounds restarts, not attempts. */
  failures?: number;
}

export interface CursorState {
  version: number;
  files: Record<string, FileCursor>;
}

export interface LoadResult {
  state: CursorState;
  /** The main file was unusable and the last-good backup was used. */
  recovered: boolean;
  /** Why the main file was unusable — recorded, never silent. */
  error?: string;
}

export function emptyCursor(): CursorState {
  return { version: CURSOR_VERSION, files: {} };
}

export function cursorPath(dir: string): string {
  return join(dir, CURSOR_FILE);
}

export function backupPath(dir: string): string {
  return join(dir, CURSOR_BACKUP);
}

/** Validate one parsed cursor record. A malformed record is dropped (it can
 *  only cause a re-ship, which the server's idempotent MERGE absorbs) —
 *  whereas a malformed OFFSET that survived validation could skip content. */
function validCursor(raw: unknown): FileCursor | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const rec = raw as Record<string, unknown>;
  const offset = rec.offset;
  if (typeof offset !== "number" || !Number.isFinite(offset) || offset < 0) {
    return undefined;
  }
  const out: FileCursor = { offset: Math.floor(offset) };
  if (typeof rec.sessionId === "string" && rec.sessionId) out.sessionId = rec.sessionId;
  for (const key of [
    // `maxWindow` MUST be listed: an unlisted numeric field is silently DROPPED
    // on reload, which would reset the shrink guard's high-water mark every run
    // and turn the guard off without failing anything.
    "dev", "ino", "updatedAt", "attemptedAt", "failures", "delivered", "lastWindow",
    "maxWindow",
  ] as const) {
    const v = rec[key];
    if (typeof v === "number" && Number.isFinite(v)) out[key] = v;
  }
  if (typeof rec.headerSha === "string" && rec.headerSha) out.headerSha = rec.headerSha;
  return out;
}

function parseState(text: string): CursorState | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return undefined;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
  const obj = parsed as Record<string, unknown>;
  if (obj.version !== CURSOR_VERSION) return undefined;
  const files = obj.files;
  if (!files || typeof files !== "object" || Array.isArray(files)) return undefined;
  const state = emptyCursor();
  for (const [path, raw] of Object.entries(files as Record<string, unknown>)) {
    const rec = validCursor(raw);
    if (rec) state.files[path] = rec;
  }
  return state;
}

/**
 * Read the cursor store. Missing → empty (a first run). Unusable main file →
 * the last-good backup, `recovered: true`, and the reason recorded. Neither
 * usable → `recovered: true` with an empty state, which re-ships rather than
 * skips (at-least-once is the contract; a duplicate is absorbed server-side).
 */
export function loadCursor(dir: string): LoadResult {
  const main = cursorPath(dir);
  if (existsSync(main)) {
    const state = parseState(readFileSync(main, "utf8"));
    if (state) return { state, recovered: false };
    const backup = backupPath(dir);
    if (existsSync(backup)) {
      const fallback = parseState(readFileSync(backup, "utf8"));
      if (fallback) {
        return {
          state: fallback,
          recovered: true,
          error: "cursor.json unreadable — failed closed to the last-good backup",
        };
      }
    }
    return {
      state: emptyCursor(),
      recovered: true,
      error: "cursor.json AND its backup unreadable — re-shipping from the header",
    };
  }
  return { state: emptyCursor(), recovered: false };
}

/**
 * Persist the cursor store atomically. Never throws for a transient write
 * error — the worker's outcome must not depend on the bookkeeping write
 * succeeding, but the failure is RETURNED so the caller can surface it (a
 * cursor that cannot be written means the next run re-ships: safe, and must
 * be visible rather than assumed).
 */
export interface SaveOptions {
  /** Drop rows for session files that no longer exist, so the store cannot grow
   *  without bound (a deleted session file has nothing left to ship, and a row
   *  for it is dead weight rewritten at every checkpoint). Off by default: a
   *  caller that only touches part of the tree must not prune the rest. */
  prune?: boolean;
}

export function saveCursor(
  dir: string,
  state: CursorState,
  opts: SaveOptions = {},
): { ok: boolean; error?: string } {
  const target = cursorPath(dir);
  const tmp = `${target}.tmp.${process.pid}`;
  try {
    // `0o700`, like every other `~/.tortoise` subdirectory this tree creates
    // (`capture_spool.py`, `tortoise-capture.ts`, `index_lock.py`): the FILES
    // are `0o600` already, so this is consistency, not a leak.
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    const toWrite = opts.prune === true ? pruneMissing(state) : state;
    const body = `${JSON.stringify(serialise(toWrite), null, 1)}\n`;
    const fd = openSync(tmp, "w", 0o600);
    try {
      writeFileSync(fd, body);
      fsyncSync(fd); // page cache is not durability: a rotate/VM-kill must not lose it
    } finally {
      closeSync(fd);
    }
    if (existsSync(target)) {
      // Highest-precedence copy BEFORE the rename: if the rename leaves a
      // torn target (it cannot, but the backup costs one copy), the last good
      // offset is still here.
      //
      // ONLY from a target that PARSES. After a corrupt-main recovery this
      // save runs with the freshly recovered state, and copying the CURRENT
      // (still corrupt) main over the backup would destroy the last good
      // offset the recovery was made from — the documented fallback would then
      // hold only until the next save (P3: a FALSE guarantee).
      const current = readFileSync(target);
      if (parseState(current.toString("utf8")) !== undefined) {
        writeFileSync(backupPath(dir), current, { mode: 0o600 });
      }
    }
    renameSync(tmp, target);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/** Drop rows whose session file no longer exists. */
function pruneMissing(state: CursorState): CursorState {
  const files: Record<string, FileCursor> = {};
  for (const [path, cursor] of Object.entries(state.files)) {
    if (existsSync(path)) files[path] = cursor;
  }
  return { version: state.version, files };
}

function serialise(state: CursorState): CursorState {
  const files: Record<string, FileCursor> = {};
  for (const path of Object.keys(state.files).sort()) files[path] = state.files[path];
  return { version: CURSOR_VERSION, files };
}

/** One dead-letter row (piece 11). Written when a file's POST is a PERMANENT
 *  failure after the backoff is exhausted, so later files are not
 *  head-of-line-blocked by it. Bounded by `DEAD_LETTER_MAX_ROWS`. */
export interface DeadLetter {
  path: string;
  sessionId?: string;
  offset: number;
  status?: number;
  reason: string;
  at: number;
}

export const DEAD_LETTER_MAX_ROWS = 500;

/** Append a dead-letter row, folding the file to ONE row (a re-failing file
 *  replaces its own row rather than growing the substrate without bound). */
export function recordDeadLetter(dir: string, row: DeadLetter): void {
  const path = join(dir, DEAD_LETTER_FILE);
  try {
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    const rows: DeadLetter[] = [];
    if (existsSync(path)) {
      for (const line of readFileSync(path, "utf8").split("\n")) {
        const t = line.trim();
        if (!t) continue;
        try {
          const parsed = JSON.parse(t) as DeadLetter;
          if (parsed && typeof parsed === "object" && parsed.path !== row.path) rows.push(parsed);
        } catch {
          // a corrupt row is dropped: the substrate is a status surface, and
          // the honest counter below counts what survived
        }
      }
    }
    rows.push(row);
    const kept = rows.slice(-DEAD_LETTER_MAX_ROWS);
    writeFileSync(path, `${kept.map((r) => JSON.stringify(r)).join("\n")}\n`, { mode: 0o600 });
  } catch {
    // never throw into the worker: a dead-letter write failure must not become
    // a capture outage
  }
}

export function readDeadLetters(dir: string): DeadLetter[] {
  const path = join(dir, DEAD_LETTER_FILE);
  if (!existsSync(path)) return [];
  const out: DeadLetter[] = [];
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      out.push(JSON.parse(t) as DeadLetter);
    } catch {
      // ignore
    }
  }
  return out;
}

/** `stat` fields the identity check needs, or `undefined` when unreadable. */
export function fileIdentity(path: string): { size: number; dev: number; ino: number } | undefined {
  try {
    const st = statSync(path);
    if (!st.isFile()) return undefined;
    return { size: st.size, dev: st.dev, ino: st.ino };
  } catch {
    return undefined;
  }
}
