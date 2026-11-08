// tortoise-hook-version: 18
// Tortoise volunteering-memory seam for Pi — the per-turn READ half
// (epic #2080, issue #2119).
//
// The `tortoise-hook-version` marker above is the INSTALL-CONTRACT generation
// of the Pi seam (see tortoise/hook_install.py): column-0, one per file, bumped
// on any change to the shipped bytes. This file ENTERS at 18 — the generation
// `tortoise-capture.ts` is at, not a fresh 1 — because the two artifacts are
// written by ONE install run and are graded as one seam:
// `hook_install.contract_version_for("pi")` reads both markers and refuses to
// answer when they disagree, so a half-upgraded pair is a repo defect the
// tests catch rather than an install that silently half-works.
// `hook_install._grade_optional_artifact` grades an INSTALLED copy of this
// file whenever it is present (absent is not drift — a capture-only install
// never opted into injection), so a stale read seam reads as `stale-artifact`:
// "reinstall with `tortoise install pi`".
//
// WHY THIS FILE EXISTS (#2119 re-measured 2026-11-08)
// ---------------------------------------------------
// `docs/research/2026-09-01-gbrain-learnings/platform-seams.md` used to say
// Pi had "no per-turn prompt hook in the extension API today — per-turn
// injection not reachable via extension API". That row was STALE. Measured on
// the installed pi-coding-agent (docs/extensions.md + the exported type in
// dist/core/extensions/types.d.ts):
//
//   event   `before_agent_start` — fired after the user submits a prompt and
//           before the agent loop. `BeforeAgentStartUserEvent` carries
//           `messageType: "user"` + `prompt` + `images`; a run started by
//           `pi.sendMessage(..., {triggerTurn:true})` arrives as
//           `messageType: "custom"` with `message` and NO `prompt`.
//   result  `BeforeAgentStartEventResult` — `{ message?: {customType,
//           content, display, details}, systemPrompt?: string }`. A returned
//           `message` is ADDED to the run's context; `display` controls
//           whether it is rendered in the transcript.
//
// So Pi has the same shape as Claude's `UserPromptSubmit` +
// `additionalContext`: a per-turn injection channel with no message
// rewriting to construct. This extension registers it and calls the ONE
// canonical reflex every other harness seam calls.
//
// ONE PIPELINE, TWO TRANSPORTS
// ---------------------------
// The block is produced by `tortoise volunteer --json` (the shared CLI over
// `tortoise/volunteer.py`), which itself resolves hosted `POST /v1/context`
// when a hosted key is configured and the local SDK `volunteer_context()`
// otherwise. A machine with no `tortoise` on PATH and a configured API key
// falls back to the hosted endpoint directly (#2119 indicator 2: "works in
// Pi's HTTP MCP mode and local mode"). Nothing here re-implements recall.
//
// FAIL-OPEN CONTENT, NEVER A BROKEN TURN
// --------------------------------------
// Every path returns `undefined` (inject nothing) on: the kill switch
// (`TORTOISE_VOLUNTEER_DISABLE`), an empty turn, a non-user run, an empty
// graph, nothing above the confidence gate, a reflex timeout, a malformed
// response, ANY thrown error. A read seam must never be able to break an
// agent turn — the reflex's own contract is content fail-open.
//
// AN INERT INSTALL MUST LEAVE EVIDENCE (#4314)
// -------------------------------------------
// Fail-open injects nothing on a healthy empty graph AND on a dead seam, so
// the two are indistinguishable without a record. When NO reflex candidate
// resolves, or every resolved candidate FAILS, this seam writes an
// `install-inert` breadcrumb to the canonical
// `capture-errors/pi-install.json` slot (the same slot the Hermes plugin and
// the shell hooks use) and still returns `undefined`.
//
// W4 USER-EXPOSURE GATE
// ----------------------
// Nothing here auto-registers: Pi loads extensions from
// `~/.pi/agent/extensions/`, and `tortoise install pi` is the only writer
// (`tortoise/capture_install.py::_install_pi`). Installing the extension IS
// the opt-in — the posture recorded in platform-seams.md §"W4 user-exposure
// opt-in: per-turn injection ships behind the `install` command". A host that
// never ran the install has no code here at all.
//
// Silently by default: the injected block is `display: false`, i.e. the model
// sees the why-context and the user's transcript does not grow a bubble for
// every turn. `"display": true` (or `TORTOISE_VOLUNTEER_DISPLAY=1`) renders it
// on the consent surface AND prefixes the reflex's `surfaced` list, which is
// the disclosure marker #2119's targets ask for.

import { execFile } from "node:child_process";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";

export const HARNESS = "pi";
/** Install name under `~/.pi/agent/extensions/` (hook_install derives it). */
export const EXTENSION_NAME = "tortoise-volunteer.ts";
/** The custom-message type this seam injects, so a transcript reader can tell
 *  volunteered context from user text and from capture. */
export const CUSTOM_TYPE = "tortoise-volunteer";
export const DEFAULT_API_URL = "https://api.premiselabs.co";
/**
 * The reflex's budget, in milliseconds — MEASURED, not a guess (#2119).
 *
 * The reflex is a local CLI call on the turn's critical path, and 3000 (the
 * first value here) was below the cost of a HEALTHY one: `tortoise volunteer`
 * constructs `EmbeddingModel.get()` for its dense leg, which loads 199 weight
 * tensors (~8.2 s per invocation on a 10-core box, load average 8 — the model
 * is per-PROCESS, so every turn pays it), and the whole call measured ~9.0 s
 * back to back. A budget under that does not bound a pathological seam; it
 * makes a healthy one INERT ON EVERY TURN — and, worse, reports it as an
 * `install-inert` breadcrumb, telling the operator to reinstall a working
 * install. Two end-to-end grades (`tests/test_pi_volunteer_seam.py`) caught
 * exactly that: 9 s of reflex, 3 s of budget, no injection.
 *
 * 30000 is the same ceiling Claude Code gives its equivalent per-turn
 * injection seam (`UserPromptSubmit` hooks default to 30 s; a timed-out hook
 * is cancelled and its injected context discarded — docs, fetched 2026-11-08).
 * It bounds a BROKEN reflex to one bounded wait; it does not set the latency of
 * a healthy one — that is ~9 s here and is the reflex's cost, not this
 * constant's.
 *
 * ⛔ It does NOT cover a COLD model build, and an earlier version of this
 * comment claimed it did ("sits below `_LOAD_TIMEOUT_S = 90 s` … so a cold
 * machine still fits"). 30 s does not fit inside the ~57 s cold load that same
 * sentence cites — the conclusion was inverted relative to its own number.
 *
 * ⛔ AND NOTHING WARMS UP (#11584 review, round 3's correction of this
 * paragraph). The reflex is bounded with `execFile(..., {timeout})`, which
 * SIGTERMs the child — the very kill this file reads as `timedOut` — and the
 * embedder load is PER PROCESS (see above). So a machine whose reflex exceeds
 * this budget gets a silent no-injection on EVERY turn, forever: each turn
 * starts a fresh process, kills it mid-load, and keeps nothing. There is no
 * "one turn without injection on a cold start", and an earlier revision of
 * this comment claimed exactly that in place of the claim it had just removed.
 * A prewarm would have to be real — spawn detached, `unref()`, abandon the wait
 * instead of killing — and measured before this comment may describe it.
 *
 * What IS true: a timeout is a clean no-injection (`status: "timeout"`), it
 * neither latches the seam nor writes the `install-inert` breadcrumb, and the
 * turn costs one bounded wait. The remedy for a machine that exceeds it is to
 * raise `{"volunteer": {"timeoutMs": N}}` — not to reinstall anything.
 * `_LOAD_TIMEOUT_S` is the reflex's own model-load ceiling (90 s, above this
 * one); it is not a promise that this budget covers the load. *
 * Override per machine with `{"volunteer": {"timeoutMs": N}}` in
 * `~/.pi/agent/tortoise-config.json`. A timeout is a clean no-injection
 * (`status: "timeout"`), and it is NOT an inert install — see
 * :func:`resolveBlock`.
 */
export const DEFAULT_TIMEOUT_MS = 30000;
/** The turn text is SEARCHED, never forwarded whole, and the bound is in
 *  BYTES because that is what the reflex's validator counts
 *  (`tortoise/volunteer.py`: `MAX_WINDOW_BYTES = 15 * 1024`, charged per turn
 *  as `len(content.encode("utf-8")) + PER_TURN_OVERHEAD_BYTES`).  A
 *  character-count bound is not the same test — 15 000 chars of a non-Latin
 *  script is 30–45 KiB — so a perfectly ordinary prompt was refused with
 *  `WINDOW_TOO_LARGE_BYTES`, the CLI exited 1, and the seam recorded a healthy
 *  install as `install-inert` (review finding 3; the exact #4314 confusion the
 *  breadcrumb exists to prevent). */
export const MAX_PROMPT_BYTES = 15 * 1024 - 64;
/** The reflex's own block budget is 8 KiB (`volunteer.BLOCK_MAX_BYTES`); a
 *  longer body would mean the reflex contract moved, so bound on the way in. */
export const MAX_INJECT_CHARS = 8000;
export const CONFIG_PATH = join(homedir(), ".pi", "agent", "tortoise-config.json");
/** The reflex CLI's source-dir record, written by the shell-hook installs. */
export const SRC_DIR_RECORD = join(homedir(), ".tortoise", "hook-src-dir");

/** The kill switch — same variable, same truthiness as the Hermes seam. */
const TRUTHY = new Set(["1", "true", "yes", "on"]);

export type Env = Record<string, string | undefined>;

export interface VolunteerConfig {
  apiUrl: string;
  apiKey: string;
  /** An explicit reflex command (`command` in the config, or
   *  `TORTOISE_COMMAND`). When set it is the ONLY candidate tried. */
  command: string;
  timeoutMs: number;
  /** `false` (default) = inject silently; `true` = render on the transcript
   *  with the `surfaced` disclosure marker. */
  display: boolean;
}

/** One reflex result: the injectable block, and what the reflex disclosed. */
export interface ReflexResult {
  block: string;
  surfaced: string[];
}

export interface VolunteerDeps {
  env?: Env;
  configPath?: string;
  home?: string;
  now?: () => number;
  /** Replace the reflex entirely (tests pin the seam, not the CLI). */
  reflexImpl?: (
    prompt: string,
    sessionId: string,
  ) => Promise<ReflexResult | null> | ReflexResult | null;
  /** Injected so a test never touches the operator's breadcrumb slot. */
  breadcrumbDir?: string;
  /** Injected so a test can drive the seam's SECOND transport (a hosted
   *  `POST /v1/context`) without a network call. The hosted path is otherwise
   *  unreachable from a test — it is only tried when no CLI resolves and a key
   *  is configured — and that is how a 402 could write an `install-inert`
   *  breadcrumb for a working install without any test noticing (#11584). */
  fetchImpl?: typeof fetch;
}

function truthy(value: unknown): boolean {
  return TRUTHY.has(String(value ?? "").trim().toLowerCase());
}

function positiveInt(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : fallback;
}

function textOf(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Truncate to at most `maxBytes` UTF-8 bytes WITHOUT splitting a code point.
 *
 * `String.prototype.slice` counts UTF-16 units and `TextEncoder` counts bytes,
 * so a char-indexed cap cannot express the reflex's window limit. The walk is
 * backwards from the byte cut over the trailing partial character (at most 3
 * continuation bytes into a 4-byte sequence), which is bounded and cannot
 * produce a lone surrogate — the thing a naive `slice(0, n)` on bytes would do
 * with a manual `Buffer.from(...).subarray()`.
 *
 * Returns the input unchanged when it already fits, so the common path
 * allocates nothing.
 */
export function clipToBytes(text: string, maxBytes: number): string {
  const buf = Buffer.from(text, "utf8");
  if (buf.length <= maxBytes) return text;
  let end = Math.max(0, maxBytes);
  // Back off over UTF-8 continuation bytes (10xxxxxx) to a lead byte.
  while (end > 0 && (buf[end] & 0xc0) === 0x80) end -= 1;
  return buf.subarray(0, end).toString("utf8");
}

/**
 * Resolve the seam's configuration.
 *
 * CO-SOURCE (#2369 D1, mirrored from `tortoise-capture.ts::resolveConfig` and
 * the shipped shell hooks): one identity = one source chain. When the KEY came
 * from the environment, the env `TORTOISE_API_URL` may apply. When the key came
 * from the FILE, its URL resolves from the SAME file or the built-in default
 * ONLY — a poisoned env must never redirect a stored `tt_…` credential, and
 * every prompt searched with it, to a host it chose.
 *
 * Never throws: an absent, unreadable or malformed config is "no config", which
 * is the ordinary state of a machine whose credentials live in the environment.
 */
export function readVolunteerConfig(
  env: Env = typeof process === "undefined" ? {} : process.env,
  configPath: string = CONFIG_PATH,
): VolunteerConfig {
  let file: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(readFileSync(configPath, "utf-8"));
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      file = parsed as Record<string, unknown>;
    }
  } catch {
    // absent / unreadable / malformed — env vars or defaults apply
  }
  // The seam's own settings live under `volunteer` so capture's keys and the
  // read seam's keys cannot shadow each other.
  const rawSeam = file.volunteer;
  const seam: Record<string, unknown> =
    rawSeam && typeof rawSeam === "object" && !Array.isArray(rawSeam)
      ? (rawSeam as Record<string, unknown>)
      : {};

  const envKey = String(env.TORTOISE_API_KEY ?? "").trim();
  const fileKey = typeof file.apiKey === "string" ? file.apiKey.trim() : "";
  const fileUrl = typeof file.apiUrl === "string" ? file.apiUrl : "";
  const apiKey = envKey || fileKey;
  const apiUrl = String((envKey ? env.TORTOISE_API_URL : fileUrl) || DEFAULT_API_URL);

  const command =
    textOf(seam.command).trim() || String(env.TORTOISE_COMMAND ?? "").trim();

  return {
    apiUrl: apiUrl.replace(/\/+$/, ""),
    apiKey,
    command,
    timeoutMs: positiveInt(seam.timeoutMs, DEFAULT_TIMEOUT_MS),
    display: truthy(seam.display ?? env.TORTOISE_VOLUNTEER_DISPLAY),
  };
}

/** Is the read seam switched off for this process? */
export function disabled(env: Env = typeof process === "undefined" ? {} : process.env): boolean {
  return truthy(env.TORTOISE_VOLUNTEER_DISABLE);
}

/**
 * The reflex entry points to try, in order.
 *
 * Mirrors `tortoise/hermes-plugin/__init__.py::_tortoise_candidates` (the same
 * chain, re-expressed in TS because Pi's hook is not a shell): PATH install →
 * the module dir an install recorded (`TORTOISE_SRC_DIR`, else
 * `~/.tortoise/hook-src-dir`) and its `.venv`, then the venv that OWNS that
 * checkout (`pyvenv.cfg`, for a wheel install), then the ambient
 * `VIRTUAL_ENV`. Each candidate is used only if it exists: a wrong candidate
 * is a skipped attempt, never an error on the agent path.
 *
 * `HOME` uses the shell hooks' `/nonexistent` sentinel: an unset HOME must
 * never resolve a CWD-relative `./.tortoise` in the agent's writable
 * workspace.
 */
export function reflexCandidates(
  env: Env = typeof process === "undefined" ? {} : process.env,
  home: string = homedir(),
): string[] {
  const candidates: string[] = [];
  const which = pathLookup("tortoise", env);
  if (which) candidates.push(which);

  const explicit = String(env.TORTOISE_SRC_DIR ?? "").trim();
  let src = explicit;
  if (!src) {
    const record = join(home || "/nonexistent", ".tortoise", "hook-src-dir");
    try {
      src = readFileSync(record, "utf-8").trim();
    } catch {
      src = "";
    }
  }
  if (src) {
    const venvBin = join(src, ".venv", "bin", "tortoise");
    if (isFile(venvBin)) {
      candidates.push(venvBin);
    } else {
      const owner = venvOwnerOf(src);
      if (owner && isFile(join(owner, "bin", "tortoise"))) {
        candidates.push(join(owner, "bin", "tortoise"));
      }
    }
  }
  const venv = String(env.VIRTUAL_ENV ?? "").trim();
  if (venv && isFile(join(venv, "bin", "tortoise"))) {
    candidates.push(join(venv, "bin", "tortoise"));
  }
  return [...new Set(candidates)];
}

/** The bounded ancestor walk that finds the venv owning a module dir (a wheel
 *  install has no `.venv` under it — `pyvenv.cfg` marks the venv root). */
function venvOwnerOf(moduleDir: string): string | null {
  let probe = moduleDir;
  for (let i = 0; i < 5; i += 1) {
    if (!probe || probe === "/") break;
    if (isFile(join(probe, "pyvenv.cfg"))) return probe;
    const parent = join(probe, "..");
    if (parent === probe) break;
    probe = parent;
  }
  return null;
}

/** `which` without a dependency, honouring the env a test injects. */
function pathLookup(name: string, env: Env): string {
  const path = String(env.PATH ?? "");
  for (const dir of path.split(":")) {
    if (!dir) continue;
    const candidate = join(dir, name);
    if (isFile(candidate)) return candidate;
  }
  return "";
}

function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

/**
 * Run `tortoise volunteer --json` for one turn.
 *
 * `--json` (not the hook-ready text mode) because the response contract also
 * carries `surfaced` — the pointer labels the disclosure marker renders — and
 * the reflex's own `degraded_reason`. `--json` reports a failure as
 * `{status: "error"}` on STDOUT with a non-zero exit, so BOTH are checked.
 */
export function runReflexCommand(
  command: string,
  prompt: string,
  sessionId: string,
  timeoutMs: number,
): Promise<{ ok: boolean; stdout: string; error: string; timedOut: boolean }> {
  return new Promise((resolve) => {
    const args = ["volunteer", "--json"];
    if (sessionId) args.push("--session-id", sessionId);
    const child = execFile(
      command,
      args,
      { timeout: timeoutMs, maxBuffer: 1 << 20 },
      (error, stdout, stderr) => {
        if (error) {
          // A TIMEOUT is not a failure of the install, and node reports both
          // through the same `error` — with an EMPTY stderr, so the existing
          // `stderr || error.message` fell through to the useless
          // "Command failed: …" body. `execFile` sets `killed` only when IT
          // killed the child, and leaves `code` null for a signal death;
          // `maxBuffer` overflow also sets `killed` but carries
          // `code: "ERR_CHILD_PROCESS_STDIO_MAXBUFFER"`, so excluding a non-null
          // code keeps a real overflow reportable (measured against node 24).
          const timedOut = error.killed === true && error.code === null &&
            error.signal === "SIGTERM";
          resolve({
            ok: false,
            stdout: String(stdout ?? ""),
            error: String(stderr ?? "").trim() ||
              (timedOut ? `timed out after ${timeoutMs}ms` : error.message),
            timedOut,
          });
          return;
        }
        resolve({ ok: true, stdout: String(stdout ?? ""), error: "", timedOut: false });
      },
    );
    // A CLI that exits before reading stdin raises EPIPE on the write; that is
    // the failure the callback already reports, never an uncaught throw.
    child?.stdin?.on?.("error", () => {});
    child?.stdin?.end?.(prompt);
  });
}

/** Parse the reflex's `--json` contract into the two fields this seam needs.
 *  Anything unparseable is "no block" — never a throw into the turn. */
export function parseReflexJson(stdout: string): ReflexResult | null {
  let data: unknown;
  try {
    data = JSON.parse(stdout);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const body = data as Record<string, unknown>;
  if (body.status === "error") return null;
  const block = textOf(body.block);
  const surfaced = Array.isArray(body.surfaced)
    ? body.surfaced
        .map((entry) => {
          if (typeof entry === "string") return entry.trim();
          if (entry && typeof entry === "object") {
            const label = (entry as Record<string, unknown>).label;
            return typeof label === "string" ? label.trim() : "";
          }
          return "";
        })
        .filter((label) => label !== "")
    : [];
  return { block, surfaced };
}

/**
 * The hosted transport, for a machine with no `tortoise` CLI.
 *
 * Same endpoint the CLI's hosted mode posts to (`POST /v1/context`) and the
 * same request body (`_cmd_volunteer`), so the two transports cannot drift
 * into different contract rows.
 */
export async function runReflexHosted(
  apiUrl: string,
  apiKey: string,
  prompt: string,
  sessionId: string,
  timeoutMs: number,
  fetchImpl: typeof fetch = fetch,
): Promise<{ result: ReflexResult | null; timedOut: boolean; refused: string }> {
  if (!apiKey) return { result: null, timedOut: false, refused: "" };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${apiUrl.replace(/\/+$/, "")}/v1/context`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        window: [{ role: "user", content: prompt }],
        session_id: sessionId || null,
      }),
      signal: controller.signal,
    });
    // ⛔ A REFUSAL IS NOT "NOTHING COULD RUN". The install is demonstrably
    // working — it reached the endpoint and was answered. Classifying a
    // 401/402/403/5xx as `inert` wrote the canonical `install-inert`
    // breadcrumb, whose documented meaning is "this install resolved nothing,
    // reinstall it": an operator running a healthy install whose hosted
    // quota was exhausted was told to reinstall it, and the real cause was
    // never named. Name it (status + reason) and let the caller treat it as
    // the timeout-like non-verdict it is.
    if (!response.ok) {
      return {
        result: null,
        timedOut: false,
        refused: `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ""}`,
      };
    }
    const body = (await response.json().catch(() => null)) as Record<string, unknown> | null;
    if (!body || typeof body !== "object") {
      return { result: null, timedOut: false, refused: "unparseable response body" };
    }
    const block = textOf(body.block) || textOf(body.context);
    const surfaced = Array.isArray(body.surfaced)
      ? body.surfaced
          .map((entry) =>
            entry && typeof entry === "object"
              ? textOf((entry as Record<string, unknown>).label).trim()
              : textOf(entry).trim(),
          )
          .filter((label) => label !== "")
      : [];
    return { result: { block, surfaced }, timedOut: false, refused: "" };
  } catch {
    // Same distinction as the CLI leg: our OWN abort means the endpoint did not
    // answer in budget (slow, not absent), which is not an inert install.
    return { result: null, timedOut: controller.signal.aborted, refused: "" };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Cross-turn re-mention suppression at the SEAM (#2119 indicator 4, the same
 * rule `#2120` ships): never inject the same block twice in one session. The
 * reflex already suppresses re-mentioning a pointer it surfaced earlier when
 * it is given the session id; this is the belt to that braces, so a stateless
 * hosted call cannot re-inject an identical block.
 *
 * ⛔ A SET PER SESSION, NOT THE LAST DIGEST (review finding 6). The first cut
 * stored one digest per session key and answered "is this the same as LAST
 * time", so the sequence `A, B, A` re-injected `A` while the contract said
 * "never twice" — the comment described an invariant the code did not hold.
 * Bounded at `MAX_SEEN_PER_SESSION` so a long session cannot grow it without
 * limit: the reflex surfaces ≤5 pointers per turn, so 64 distinct blocks is
 * far past any real re-surface, and the oldest entry is dropped rather than
 * letting the map grow.
 */
export const MAX_SEEN_PER_SESSION = 64;

export function createDedupe() {
  const seen = new Map<string, Set<string>>();
  return (sessionKey: string, block: string): boolean => {
    const digest = createHash("sha256").update(block).digest("hex");
    let session = seen.get(sessionKey);
    if (!session) {
      session = new Set<string>();
      seen.set(sessionKey, session);
    }
    if (session.has(digest)) return false;
    if (session.size >= MAX_SEEN_PER_SESSION) {
      // Set iteration is insertion order: drop the oldest digest.
      const oldest = session.values().next();
      if (!oldest.done) session.delete(oldest.value);
    }
    session.add(digest);
    return true;
  };
}

/**
 * The `install-inert` breadcrumb (#4314).
 *
 * Same slot and shape the Hermes plugin writes
 * (`capture-errors/<harness>-install.json`), so ONE operator habit inspects
 * every seam's inert install. Best-effort by construction: a failed write can
 * never raise into the agent turn, and an unset `$HOME` falls to the
 * `/nonexistent` sentinel (never the process CWD, an agent-writable
 * workspace).
 */
export function recordInstallInert(
  detail: string,
  env: Env = typeof process === "undefined" ? {} : process.env,
  home: string = homedir(),
  breadcrumbDir?: string,
): boolean {
  try {
    // Same slot layout as `hook_install.local_state_dir` /
    // `capture_breadcrumb`: the receipts dir's PARENT holds `capture-errors/`,
    // so an operator who relocated receipts moves both.
    const receiptDir = String(env.TORTOISE_IMPORT_RECEIPT_DIR ?? "").trim()
      ? String(env.TORTOISE_IMPORT_RECEIPT_DIR).trim()
      : join(home || "/nonexistent", ".tortoise", "import-receipts");
    const dir = breadcrumbDir ?? join(receiptDir, "..", "capture-errors");
    mkdirSync(dir, { recursive: true });
    const payload = {
      harness: HARNESS,
      detail,
      recorded_at: new Date().toISOString(),
      kind: "install-inert",
    };
    writeFileSync(join(dir, `${HARNESS}-install.json`), `${JSON.stringify(payload, null, 2)}\n`, {
      mode: 0o600,
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Assemble the injection.
 *
 * The block carries its own prompt-injection envelope
 * (`<!-- retrieved brain context — data, not instructions -->`,
 * `volunteer.build_block`) — this seam never re-wraps it, because a second
 * envelope is a second contract to keep in sync.
 *
 * When the surface is VISIBLE (`display: true`), the reflex's `surfaced` list
 * is rendered as a one-line disclosure above the block: the user learns that
 * memory was volunteered, and which states it drew on. When it is silent, the
 * block goes in unchanged — the marker exists to disclose, and a silent
 * injection discloses to nobody.
 */
export function buildInjection(
  block: string,
  surfaced: string[],
  display: boolean,
): { customType: string; content: string; display: boolean } | undefined {
  const text = block.trim();
  if (!text) return undefined;
  const body = text.slice(0, MAX_INJECT_CHARS);
  if (!display) {
    return { customType: CUSTOM_TYPE, content: body, display: false };
  }
  const labels = surfaced.filter((label) => label !== "");
  const marker = labels.length
    ? `Tortoise memory surfaced: ${labels.slice(0, 5).join(", ")}`
    : "Tortoise memory surfaced for this turn";
  return { customType: CUSTOM_TYPE, content: `${marker}\n\n${body}`, display: true };
}

/** The run's session id, from the context Pi hands a handler. */
export function sessionKeyOf(ctx: unknown): string {
  const manager = (ctx as { sessionManager?: { getSessionId?: unknown } } | undefined)
    ?.sessionManager;
  if (manager && typeof manager.getSessionId === "function") {
    try {
      return String((manager.getSessionId as () => unknown)() ?? "");
    } catch {
      return "";
    }
  }
  return "";
}

/** What one turn's reflex attempt concluded. Four states, because "the graph
 *  is empty", "this install cannot run" and "the reflex is merely SLOW" are
 *  different facts: only the second one is evidence about the install (#4314),
 *  and reporting a slow-but-healthy reflex as inert sends the operator to
 *  reinstall something that works. */
export type ReflexVerdict =
  | { status: "block"; result: ReflexResult }
  | { status: "empty" }
  | { status: "timeout"; detail: string }
  /** The reflex or the hosted endpoint was REACHED and refused us (an HTTP
   *  status, an unparseable answer). Like `timeout` this is NO VERDICT ABOUT
   *  THE INSTALL — the install ran — so it must never write the
   *  `install-inert` breadcrumb (#11584 review). It is distinct from
   *  `timeout` only so the operator-facing detail can name the real cause. */
  | { status: "refused"; detail: string }
  | { status: "inert"; detail: string };

/**
 * Resolve one turn's block: the explicit command, else the CLI chain, else
 * the hosted endpoint.
 *
 * `empty` is the ordinary answer — the reflex RAN and nothing cleared the
 * confidence gate — and it stays silent. `timeout` means a reflex RAN and did
 * not finish in budget: silence, and NO evidence about the install, because the
 * install is demonstrably working (it started the process). `refused` means a
 * candidate was REACHED and answered nothing usable (an HTTP status, an
 * unparseable body) — also no evidence about the install. `inert` is reserved
 * for the one thing an install actually fixes: NOTHING COULD RUN AT ALL (no
 * candidate resolved AND no hosted key), which is the only class the caller may
 * latch on and breadcrumb.
 *
 * ⛔ A resolved CLI candidate that FAILED is `refused`, NOT `inert` (#11584
 * review finding 1, second half). "Every candidate failed" is what a
 * momentarily locked graph or a reflex mid-upgrade looks like on an install
 * that is entirely sound, and `inert` latches the seam off for the rest of the
 * process AND writes the `install-inert` breadcrumb telling the operator to
 * reinstall it. Only a chain that could not be formed at all is inert.
 *
 * A timeout ABORTS the chain rather than falling through to the next candidate.
 * A slow reflex is slow in the reflex, not in the path it was found on, so the
 * next candidate would spend the same budget again and multiply the turn's
 * wait by the size of the chain — the opposite of the bound this constant
 * exists to provide. Exhausting the chain is what `refused` is for.
 */
export async function resolveBlock(
  prompt: string,
  sessionId: string,
  cfg: VolunteerConfig,
  candidates: string[],
  fetchImpl: typeof fetch = fetch,
): Promise<ReflexVerdict> {
  const chain = cfg.command ? [cfg.command] : candidates;
  let lastError = "";
  // ⛔ ONE DEADLINE FOR THE WHOLE CHAIN, not one budget per candidate (review
  // finding 5). Pi is BUSY while `before_agent_start` runs (docs/extensions.md:
  // "Pi counts as busy while a run starts, including the compaction check and
  // `before_agent_start`"), so N slow candidates made the turn's wait
  // N × timeoutMs — a ceiling nobody wrote down. Each attempt gets what is LEFT
  // of the single budget; a chain that runs out is a timeout, which is a
  // no-injection, never an inert install.
  const deadline = Date.now() + cfg.timeoutMs;
  for (const command of chain) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      return {
        status: "timeout",
        detail: `the reflex chain spent its ${cfg.timeoutMs}ms budget before ${command}`,
      };
    }
    const run = await runReflexCommand(command, prompt, sessionId, remaining);
    if (run.timedOut) {
      return { status: "timeout", detail: `${command}: ${run.error}` };
    }
    if (!run.ok) {
      lastError = `${command}: ${run.error || "non-zero exit"}`;
      continue;
    }
    const parsed = parseReflexJson(run.stdout);
    if (!parsed) {
      lastError = `${command}: unparseable --json contract`;
      continue;
    }
    return parsed.block.trim() ? { status: "block", result: parsed } : { status: "empty" };
  }
  if (chain.length) {
    // ⛔ Not `inert` (#11584 review finding 1, second half). A non-zero exit
    // from every candidate is what a momentarily locked graph or a reflex
    // mid-upgrade looks like; `inert` would latch the seam OFF for the whole
    // process and write "reinstall" about an install that works.
    return {
      status: "refused",
      detail: `every resolved tortoise reflex failed (${lastError})`,
    };
  }
  // No CLI anywhere: a configured hosted key is the documented SECOND
  // transport (the CLI's own hosted mode posts the same body), not a fallback
  // invented here.
  if (!cfg.apiKey) {
    return {
      status: "inert",
      detail:
        "no tortoise reflex resolved (checked PATH, the installer's " +
        "~/.tortoise/hook-src-dir record and $VIRTUAL_ENV) and no hosted " +
        "API key is configured",
    };
  }
  const hosted = await runReflexHosted(
    cfg.apiUrl,
    cfg.apiKey,
    prompt,
    sessionId,
    cfg.timeoutMs,
    fetchImpl,
  );
  if (hosted.timedOut) {
    return { status: "timeout", detail: `POST ${cfg.apiUrl}/v1/context timed out` };
  }
  if (hosted.refused) {
    return {
      status: "refused",
      detail: `POST ${cfg.apiUrl}/v1/context refused (${hosted.refused})`,
    };
  }
  if (!hosted.result) {
    // Reached, answered, but carried nothing usable — still no evidence that
    // the install is broken, so still not `inert`.
    return {
      status: "refused",
      detail: `POST ${cfg.apiUrl}/v1/context returned no block`,
    };
  }
  return hosted.result.block.trim()
    ? { status: "block", result: hosted.result }
    : { status: "empty" };
}

/**
 * The extension factory. `tortoise install pi` copies this file into
 * `~/.pi/agent/extensions/`; Pi calls the default export with its
 * `ExtensionAPI`.
 */
export default function tortoiseVolunteer(
  pi: { on: (event: string, handler: (event: never, ctx: never) => unknown) => unknown },
  deps: VolunteerDeps = {},
): void {
  const env = deps.env ?? (typeof process === "undefined" ? {} : process.env);
  const configPath = deps.configPath ?? CONFIG_PATH;
  const home = deps.home ?? homedir();
  const cfg = readVolunteerConfig(env, configPath);
  const dedupe = createDedupe();
  // ⛔ AN INERT VERDICT IS LATCHED FOR THE PROCESS (review finding 5). Without
  // this, every user turn re-walked the whole candidate chain, re-spawned
  // Python, and REWROTE the breadcrumb — so its `recorded_at` always read
  // "now" and could never say how long the install had been dead, and a turn on
  // a machine whose `tortoise` shim hangs paid the budget again and again.
  // Latching is safe in the direction it errs: the remedy is an install, and
  // the install guidance already says to restart Pi from a NEW terminal (a
  // `/reload` keeps the environment). Only the `inert` class latches —
  // `timeout` and `empty` are per-turn facts and must be re-evaluated.
  let inertLatched = false;

  /** Run the reflex for one turn, fail-open in every direction. */
  const reflex = async (prompt: string, sessionId: string): Promise<ReflexResult | null> => {
    if (deps.reflexImpl) {
      try {
        return await deps.reflexImpl(prompt, sessionId);
      } catch {
        return null;
      }
    }
    if (inertLatched) return null;
    const candidates = reflexCandidates(env, home);
    let verdict: ReflexVerdict;
    try {
      verdict = await resolveBlock(
        prompt,
        sessionId,
        cfg,
        candidates,
        (deps.fetchImpl ??
          (typeof fetch === "function" ? fetch : undefined)) as typeof fetch,
      );
    } catch (error) {
      // An unexpected throw out of the resolver is a bug in THIS seam, not
      // evidence about the install, so it must not latch or breadcrumb
      // (#11584 review finding 1).
      verdict = { status: "refused", detail: `reflex raised: ${String(error)}` };
    }
    // An inert install must leave evidence; an empty graph must not (#4314) —
    // and neither must a SLOW one. A timeout says the reflex ran and the
    // install works, so the canonical `capture-errors/pi-install.json` slot
    // stays untouched: an operator who reads "reinstall" off a breadcrumb
    // written by a working install is being sent to fix nothing.
    if (verdict.status === "inert") {
      inertLatched = true;
      recordInstallInert(
        `${verdict.detail} — injected nothing. This is latched for the rest of ` +
          "this Pi process: re-run `tortoise install pi`, then restart Pi from " +
          "a NEW terminal (a /reload keeps the environment).",
        env,
        home,
        deps.breadcrumbDir,
      );
      return null;
    }
    if (verdict.status === "empty") return null;
    if (verdict.status === "timeout") return null;
    // A refusal is the same NO-VERDICT class as a timeout: the seam ran, so the
    // canonical `install-inert` slot stays untouched and the inert latch is not
    // set — a 402 that clears must not disable the seam for the whole process.
    if (verdict.status === "refused") return null;
    return verdict.result;
  };

  pi.on("before_agent_start", async (event: unknown, ctx: unknown) => {
    try {
      if (disabled(env)) return undefined;
      const typed = event as {
        messageType?: string;
        prompt?: unknown;
      } | undefined;
      // Only a USER prompt is a turn to search. A run started by a custom
      // message (`messageType: "custom"`) has no prompt, and injecting into it
      // would let this seam's own message start a second run.
      if (typed?.messageType !== "user") return undefined;
      const prompt = clipToBytes(textOf(typed.prompt), MAX_PROMPT_BYTES);
      if (!prompt.trim()) return undefined;

      const sessionKey = sessionKeyOf(ctx);
      const result = await reflex(prompt, sessionKey);
      if (!result) return undefined;
      const message = buildInjection(result.block, result.surfaced, cfg.display);
      if (!message) return undefined;
      if (!dedupe(sessionKey || HARNESS, message.content)) return undefined;
      return { message };
    } catch {
      // Fail-open: a read seam must never be able to break an agent turn.
      return undefined;
    }
  });
}
