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
// RESOLVES at all — no CLI on PATH, none in `~/.tortoise/hook-src-dir`,
// `$VIRTUAL_ENV` empty, no hosted key — this seam writes an `install-inert`
// breadcrumb to the canonical `capture-errors/pi-install.json` slot (the same
// slot the Hermes plugin and the shell hooks use), latches itself off for the
// process, and still returns `undefined`.
//
// ⛔ WHAT IS DELIBERATELY *NOT* BREADCRUMBED (review finding 3 — this header
// used to claim the opposite, which sent a reader to the wrong conclusion):
// `timeout` and `refused`. Their `kind` would have to say "reinstall", and both
// can be TRUE OF A WORKING INSTALL — a reflex mid-upgrade, a momentarily locked
// graph, a 402 that clears — so a breadcrumb (and worse, the latch) would tell an
// operator to fix something that is not broken. The distinction is the CAUSE,
// not the symptom: `inert` means nothing resolved, the others mean the reflex
// RAN and declined. Their `detail` goes to stderr under
// `TORTOISE_VOLUNTEER_DEBUG=1` instead (`trace`) — `timeout`'s names the
// candidate and the budget, `refused`'s names the actual cause — so a persistent
// misconfiguration (a wrong venv, a missing extra, a 30 s budget below a healthy
// reflex) is diagnosable without being recorded as an install fault. `empty` is
// silent and carries no `detail`: a working reflex that cleared the confidence
// gate is the healthy no-injection case, and there is nothing to report.
// ⛔ Review finding 4: an earlier revision of this paragraph claimed the trace
// covered `empty` and `timeout` too, while only `refused` was traced — the same
// over-promise this section exists to remove. The tracing assertion in the suite
// now covers BOTH traced classes on BOTH transports, and it took a second
// correction to get there: `refused` reached the trace from the CLI leg, but
// `runReflexHosted`'s `catch` was BARE — the reason was discarded and the caller
// reported "returned no block", which CLAIMS THE ENDPOINT ANSWERED about a
// request that never got a response at all. The catch now binds the error
// (`describeError`) and one suite row drives a thrown `ENOTFOUND` through it and
// asserts the cause reaches the trace, so the sentence cannot drift again
// silently.
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

import { spawn } from "node:child_process";
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
 * paragraph). The reflex is bounded by this file's own deadline timer, which
 * SIGKILLs the child — the kill this file reads as `timedOut` — and the
 * embedder load is PER PROCESS (see above). So a machine whose reflex exceeds
 * this budget gets a silent no-injection on EVERY turn, forever: each turn
 * starts a fresh process, kills it mid-load, and keeps nothing. There is no
 * "one turn without injection on a cold start", and an earlier revision of
 * this comment claimed exactly that in place of the claim it had just removed.
 * A prewarm would have to be real — spawn detached, `unref()`, abandon the wait
 * instead of killing — and measured before this comment may describe it.
 *
 * (Round 4: the deadline was `execFile(..., {timeout})`, which SIGTERMs once and
 * cannot be escalated, so a reflex that ignored SIGTERM hung the turn forever.
 * The bound is now our own timer — see `runReflexCommand`.)
 *
 * What IS true: a timeout is a clean no-injection (`status: "timeout"`), it
 * neither latches the seam nor writes the `install-inert` breadcrumb, and the
 * turn costs one bounded wait. The remedy for a machine that exceeds it is to
 * raise `{"volunteer": {"timeoutMs": N}}` — not to reinstall anything.
 * `_LOAD_TIMEOUT_S` is the reflex's own model-load ceiling (90 s, above this
 * one); it is not a promise that this budget covers the load.
 *
 * Override per machine with `{"volunteer": {"timeoutMs": N}}` in
 * `~/.pi/agent/tortoise-config.json`. A timeout is a clean no-injection
 * (`status: "timeout"`), and it is NOT an inert install — see
 * :func:`resolveBlock`.
 */
export const DEFAULT_TIMEOUT_MS = 30000;

/** Cap on a reflex stream's accumulated output, mirroring `execFile`'s
 *  `maxBuffer` (which is per-stream in node 24 — measured: 800+800 bytes passes
 *  `maxBuffer: 1000`). `--json` output for one turn is small (surveyed at
 *  <10 KiB); this exists so a broken or hostile reflex cannot eat the harness's
 *  memory, and exceeding it is a FAILURE, never a silent truncation. */
export const MAX_OUTPUT_CHARS = 1 << 20;
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

/** Is the seam's diagnostic trace on for this process? */
export function tracing(env: Env = typeof process === "undefined" ? {} : process.env): boolean {
  return truthy(env.TORTOISE_VOLUNTEER_DEBUG);
}

/**
 * Emit a refused verdict's `detail` to stderr, where a developer can see it.
 *
 * ⛔ The canonical `capture-errors/pi-install.json` slot stays UNTOUCHED for
 * these classes (see the header): its `kind` is `install-inert`, whose advice is
 * "reinstall", and a 402 that clears in a minute must not send an operator to
 * reinstall a working install. But DISCARDING the detail — the only string that
 * names the real cause — left a persistently broken reflex (wrong venv, missing
 * extra) with no record anywhere at all (review finding 3). So it goes to
 * stderr, opt-in, and never to the canonical slot.
 */
function trace(detail: string, env: Env): void {
  if (!tracing(env)) return;
  try {
    (typeof process === "undefined" ? undefined : process.stderr)
      ?.write?.(`[tortoise-volunteer] ${detail}\n`);
  } catch {
    // A trace must never be the thing that breaks a turn.
  }
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
 *
 * ⛔ THE BOUND MUST NOT DEPEND ON THE CHILD'S SIGNAL DISPOSITION (review finding
 * 1). `execFile(..., {timeout})` sends ONE `killSignal` (default SIGTERM) and
 * settles only in the node callback, i.e. only when the child EXITS. A reflex
 * that ignores SIGTERM therefore never settles: measured on this box with a
 * child whose first line is `trap '' TERM; sleep 600` and `timeoutMs: 800`, the
 * promise was still pending at 6 s and the node process never exited (124 under
 * `timeout 25`). Pi awaits handler promises and has no hook timeout of its own,
 * so that hangs the TURN — the one thing this file promises can never happen
 * (`FAIL-OPEN CONTENT, NEVER A BROKEN TURN`). The bound is therefore OUR OWN
 * timer: it resolves the promise FIRST (so resolution can never depend on the
 * child) and only then kills.
 *
 * ⛔ AND THE KILL MUST REACH THE PROCESS GROUP (review finding 5). A SIGKILL to
 * the direct child is untrappable, but a reflex resolved from PATH is often a
 * SHIM that forked the real worker: killing the shim alone REPARENTS the worker
 * (measured: `sleep 600` survived with ppid 1), it keeps the stdout pipe open so
 * node cannot exit, and each timed-out turn leaks one runaway process. So the
 * child is spawned `detached` (its own process group, which its descendants
 * inherit) and the deadline kills `-pid`. `detached` also means a terminal
 * signal aimed at Pi no longer reaches a reflex mid-flight; that is acceptable
 * because the reflex is bounded by this timer, and it is the price of not
 * leaking orphans.
 *
 * ⛔ SO THIS USES `spawn`, NOT `execFile` (also review finding 5). `execFile`
 * silently does NOT honour `detached`: measured on node 24, a child spawned
 * `execFile(p, [], {detached: true})` reports `pgid=1829219` while its own pid is
 * `1829308` (the parent's group), so `process.kill(-pid)` fails with ESRCH and
 * the group kill is a no-op. The same child through `spawn(p, [], {detached:
 * true})` reports `pgid === pid`. spawn costs us `execFile`'s buffering, so the
 * stdout/stderr collection and the `maxBuffer` cap are done here — with the same
 * observable result the tests pin for every other failure class.
 */
export function runReflexCommand(
  command: string,
  prompt: string,
  sessionId: string,
  timeoutMs: number,
  priorContext: string = "",
): Promise<{ ok: boolean; stdout: string; error: string; timedOut: boolean }> {
  return new Promise((resolve) => {
    const args = ["volunteer", "--json"];
    if (sessionId) args.push("--session-id", sessionId);
    let settled = false;
    let overflowed = false;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    const settle = (r: {
      ok: boolean;
      stdout: string;
      error: string;
      timedOut: boolean;
    }): void => {
      if (settled) return;
      settled = true;
      if (watchdog !== undefined) clearTimeout(watchdog);
      resolve(r);
    };
    // Our timer owns the deadline (see the docstring), and `spawn` — not
    // `execFile` — is what actually honours `detached`, which is what makes the
    // group kill below reach a wrapper's forked worker.
    const child = spawn(command, args, {
      detached: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
    // ⛔ DECODE AS A STREAM, NEVER PER CHUNK (review finding 6). `buf.toString(
    // "utf8")` on each `data` event emits U+FFFD for a multi-byte character
    // that straddles a read boundary, and the reflex's block is free-form text
    // — so a split `ó` used to be INJECTED CORRUPTED (or, when the split abuts
    // JSON punctuation, made the body `unparseable`, which reads as a refused
    // reflex on every turn). `execFile` set a StringDecoder for us; `spawn`
    // does not, so it is set explicitly. Measured: the two bytes of `ó` split
    // across chunks give `corrupci\uFFFD\uFFFDi\u2026` per-chunk and
    // `corrupci\u00F3n` through the decoder.
    child.stdout?.setEncoding?.("utf8");
    child.stderr?.setEncoding?.("utf8");
    let out = "";
    let err = "";
    /** Kill the reflex's whole process GROUP, not just the direct child. */
    const killGroup = (): void => {
      const pid = child?.pid;
      try {
        if (typeof pid === "number" && pid > 0) {
          process.kill(-pid, "SIGKILL");
          return;
        }
      } catch {
        // ESRCH (already gone) or a platform without group signals — fall
        // through to the single-pid kill, which is the pre-finding-5 behaviour
        // rather than no kill at all.
      }
      try {
        child?.kill?.("SIGKILL");
      } catch {
        // A child that already exited is not an error: nothing to reap.
      }
    };
    const collect = (chunk: string, sink: "out" | "err"): void => {
      if (settled || overflowed) return;
      const next = (sink === "out" ? out : err) + chunk;
      if (next.length > MAX_OUTPUT_CHARS) {
        // `execFile`'s `maxBuffer` equivalent: a reflex that floods cannot eat
        // the harness's memory, and it is a FAILURE, never a silent truncation.
        overflowed = true;
        settle({
          ok: false,
          stdout: out,
          error: `reflex output exceeded ${MAX_OUTPUT_CHARS} characters`,
          timedOut: false,
        });
        killGroup();
        return;
      }
      if (sink === "out") out = next;
      else err = next;
    };
    child.stdout?.on("data", (chunk: string) => collect(chunk, "out"));
    child.stderr?.on("data", (chunk: string) => collect(chunk, "err"));
    // A spawn that never started (ENOENT, EACCES) reports here, not in `close`.
    child.on("error", (error: Error) => {
      settle({
        ok: false,
        stdout: out,
        error: err.trim() || error.message,
        timedOut: false,
      });
    });
    child.on("close", (code: number | null, signal: string | null) => {
      // The watchdog settles FIRST, so a timeout lands there and this is a
      // no-op: `settled` is the guard, never the signal's name.
      if (settled) return;
      if (code === 0) {
        settle({ ok: true, stdout: out, error: "", timedOut: false });
        return;
      }
      settle({
        ok: false,
        stdout: out,
        error: err.trim() ||
          (code === null
            ? `reflex was terminated by ${signal ?? "a signal"}`
            : `reflex exited with code ${code}`),
        timedOut: false,
      });
    });
    watchdog = setTimeout(() => {
      // Resolve FIRST — a settled promise is what keeps the turn moving; the
      // kill below is cleanup for a child we no longer wait on.
      settle({
        ok: false,
        stdout: out,
        error: `timed out after ${timeoutMs}ms`,
        timedOut: true,
      });
      killGroup();
    }, timeoutMs);
    // A pending budget must never be the reason Pi's process stays alive.
    if (typeof watchdog === "object" && typeof watchdog?.unref === "function") {
      watchdog.unref();
    }
    // A CLI that exits before reading stdin raises EPIPE on the write; that is
    // the failure the callback already reports, never an uncaught throw.
    child?.stdin?.on?.("error", () => {});
    // ⛔ THE STDIN PAYLOAD IS THE DOCUMENTED CONTRACT DICT, NOT BARE TEXT
    // (#11584 review, P1). `prior_context` is the reflex's re-mention-
    // suppression input and `session_id` is carried-but-not-consulted, so a
    // bare-text stdin silently disables the suppression. `_cmd_volunteer`
    // accepts both shapes, and a DICT beats a `--prior-context` flag for
    // forward compatibility: a `tortoise` older than this seam IGNORES an
    // unknown payload key and still runs, while an unknown FLAG is an argparse
    // error — which this seam's fail-open turns into no injection at all, on
    // every turn, silently. Same reasoning and same payload as the sibling
    // `tortoise/openclaw-plugin/index.mjs::reflexPayload`.
    child?.stdin?.end?.(
      JSON.stringify({
        window: [{ role: "user", content: prompt }],
        session_id: sessionId || null,
        prior_context: priorContext ? String(priorContext) : null,
      }),
    );
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

/** One thrown value as a single line, `code`/`name`/`message` only.
 *  Never a URL, never a header — a key cannot leak through this. */
function oneLine(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" && code
    ? `${error.name}/${code}: ${error.message}`
    : `${error.name}: ${error.message}`;
}

/** A thrown value as one line safe to put in a trace (never a turn).
 *
 *  ⛔ `cause` IS THE MESSAGE (measured on node v24.21.0, and the reason this is
 *  a second attempt). `fetch` reports EVERY transport failure as the same
 *  `TypeError: fetch failed` with `code === undefined`; the real reason lives
 *  ONLY on `cause`:
 *
 *    DNS    TypeError: fetch failed  →  cause: getaddrinfo ENOTFOUND …  (ENOTFOUND)
 *    conn   TypeError: fetch failed  →  cause: bad port                  (no code)
 *    TLS    TypeError: fetch failed  →  cause: certificate has expired    (CERT_HAS_EXPIRED)
 *
 *  Rendering the outer error alone therefore collapses a wrong `apiUrl`, a
 *  corporate TLS interceptor and a downed host into ONE identical string —
 *  which is precisely the "diagnosable" claim the caller makes about `refused`.
 *  One level is enough: undici's cause carries both the code and the sentence.
 */
function describeError(error: unknown): string {
  const own = oneLine(error);
  const cause = error instanceof Error ? (error as { cause?: unknown }).cause : undefined;
  if (cause === undefined || cause === null) return own;
  return `${own} (cause: ${oneLine(cause)})`;
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
  priorContext: string = "",
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
        // Same continuity input as the CLI leg (#11584 review, P1): the
        // documented `VolunteerContextRequest` field the reflex's re-mention
        // suppression reads. Omitting it made the hosted transport suppress
        // nothing while the seam's own comment claimed it did.
        prior_context: priorContext ? String(priorContext) : null,
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
  } catch (error) {
    // Same distinction as the CLI leg: our OWN abort means the endpoint did not
    // answer in budget (slow, not absent), which is not an inert install.
    if (controller.signal.aborted) {
      return { result: null, timedOut: true, refused: "" };
    }
    // ⛔ BIND IT (#11584 review finding 4), AND ITS CAUSE (round-2 finding).
    // A bare `catch` discarded the reason, and the caller then reported
    // `POST <url>/v1/context returned no block` — which CLAIMS THE ENDPOINT
    // ANSWERED. `describeError` also renders `cause`, because `fetch` wraps
    // every transport failure in the same `TypeError: fetch failed` and puts
    // DNS/conn/TLS on `cause` — without that, the three misconfigurations the
    // header promises are diagnosable stay one indistinguishable string. It
    // goes to stderr through `trace` under `TORTOISE_VOLUNTEER_DEBUG=1`, never
    // to the canonical `install-inert` slot (see `trace`).
    return {
      result: null,
      timedOut: false,
      refused: `request failed: ${describeError(error)}`,
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Cross-turn re-mention suppression at the SEAM (#2119 indicator 4, the same
 * rule `#2120` ships): never inject the same block twice in one session.
 *
 * ⛔ THE REFLEX'S OWN SUPPRESSION IS `prior_context`, NOT `session_id` — and an
 * earlier revision of this comment said the opposite (#11584 review, P1).
 * `tortoise/__main__.py` says it verbatim: *"the reflex's re-mention suppression
 * reads `prior_context` (the block a seam injected on an earlier turn) — never
 * `session_id`, which is carried but not consulted by the decision"*, and the
 * suppression set is `pointer_ids_in_text(prior_context)`
 * (`tortoise/volunteer.py`). With `prior_context` absent the set is EMPTY, so a
 * seam that passes only a session id suppresses nothing — exactly the false
 * premise `platform-seams.md` records for Hermes (#2120 / #8957). This seam now
 * supplies it via `createContinuity()` below; this digests set is the BELT
 * (identical blocks), and the continuity input is the BRACES (a block that
 * changed by one pointer).
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

/**
 * Cross-turn CONTINUITY (#2120 scope "Cross-turn dedupe") — the input the
 * reflex's stage-5 re-mention suppression actually reads.
 *
 * `createDedupe` above only catches an IDENTICAL block. A turn whose block
 * changes by one pointer (a second pointer clears the gate, or one drops out)
 * re-surfaces the pointers this session already saw — the false fire #2119's
 * own target (≤0.03) measures. The reflex suppresses it, but ONLY when it is
 * handed `prior_context`, so remember the block injected last turn and pass it
 * back as that input.
 *
 * One turn deep, not the whole session, and per session key — the same
 * contract as the sibling `tortoise/openclaw-plugin/index.mjs::createContinuity`,
 * so the two seams cannot drift into different depths.
 */
export function createContinuity() {
  const last = new Map<string, string>();
  return {
    prior: (sessionKey: string): string => last.get(sessionKey) ?? "",
    remember: (sessionKey: string, block: string): void => {
      if (!block) return;
      last.set(sessionKey, block);
    },
  };
}

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
  priorContext: string = "",
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
    const run = await runReflexCommand(command, prompt, sessionId, remaining, priorContext);
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
    priorContext,
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
  // ⛔ FAIL-CLOSED TEST ISOLATION (#11584 review, P2) — the same guard the
  // shipped sibling carries (`tortoise-capture.ts`'s `NODE_TEST_CONTEXT`
  // throw, #3721), for the same reason and one layer further out.
  //
  // Every input below defaults to the OPERATOR's machine: `configPath` is
  // `~/.pi/agent/tortoise-config.json` (which can carry a file-sourced
  // `apiKey`), `home` resolves the installer's `~/.tortoise/hook-src-dir`
  // record to the real CLI, and `env` is `process.env` (PATH, kill switch,
  // `TORTOISE_API_KEY`, `TORTOISE_API_URL`). A row written as
  // `tortoiseVolunteer(mockPi())` under `node --test` therefore asserts the
  // machine it runs on instead of the code — and on a box with a configured
  // key it takes the HOSTED transport and POSTs the test prompt to the
  // production endpoint. That trap FIRED during this PR's development (see
  // the note above `hermetic()` in `tortoise-volunteer.test.ts`), and the only
  // thing preventing a repeat was convention.
  //
  // `reflexImpl` is exempt because it replaces the resolver outright: with it
  // there is no config read, no CLI resolution and no hosted request, so no
  // path to the operator's machine exists.
  if (
    process.env.NODE_TEST_CONTEXT &&
    !deps.reflexImpl &&
    !(deps.env && deps.configPath && deps.home)
  ) {
    throw new Error(
      "tortoise-volunteer: under `node --test` you must inject `env`, " +
        "`configPath` and `home` (or a `reflexImpl`) — refusing to read the " +
        "operator's config, resolve their install, or post with their " +
        "credential",
    );
  }
  const env = deps.env ?? (typeof process === "undefined" ? {} : process.env);
  const configPath = deps.configPath ?? CONFIG_PATH;
  const home = deps.home ?? homedir();
  const cfg = readVolunteerConfig(env, configPath);
  const dedupe = createDedupe();
  // #2120 continuity: the reflex's re-mention suppression reads the block this
  // seam injected LAST TURN, never the session id (#11584 review, P1). See
  // `createContinuity` — the digest set above is the belt, this is the braces.
  const continuity = createContinuity();
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
        continuity.prior(sessionId || HARNESS),
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
    if (verdict.status === "timeout") {
      // Review finding 4: the single most operationally important failure this
      // file documents (a reflex that exceeds the budget injects nothing on
      // EVERY turn, forever) was undiagnosable even with the debug flag on —
      // `detail` names the candidate AND the budget. Trace it; keep it out of
      // the canonical slot and out of the latch, exactly as for `refused`.
      trace(`timeout: ${verdict.detail}`, env);
      return null;
    }
    // A refusal is the same NO-VERDICT class as a timeout: the seam ran, so the
    // canonical `install-inert` slot stays untouched and the inert latch is not
    // set — a 402 that clears must not disable the seam for the whole process.
    // The detail (which names the actual cause) is NOT discarded: it goes to
    // stderr under `TORTOISE_VOLUNTEER_DEBUG=1` (review finding 3).
    if (verdict.status === "refused") {
      trace(`refused: ${verdict.detail}`, env);
      return null;
    }
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
      // Remember what was INJECTED so the next turn can hand it back as
      // `prior_context` — the input the reflex's re-mention suppression reads.
      continuity.remember(sessionKey || HARNESS, result.block);
      return { message };
    } catch {
      // Fail-open: a read seam must never be able to break an agent turn.
      return undefined;
    }
  });
}
