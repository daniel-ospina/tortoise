// tortoise-hook-version: 2
// Tortoise volunteering-memory seam for OpenClaw (epic #2080, issue #2120).
//
// The `tortoise-hook-version` marker above is the install-contract generation
// for this seam (see tortoise/hook_install.py): column-0, one per file, bumped
// on ANY behavioural edit. `tortoise session verify --harness openclaw` and
// `tortoise doctor` read it to tell an already-installed copy that it is stale.
//
// WHY A PLUGIN, NOT A SHELL HOOK (#2120 re-measure, 2026-10-11)
// ---------------------------------------------------------------
// OpenClaw has NO shell-hook surface. Its per-turn seam is the in-process
// plugin hook `before_prompt_build`, which returns `{ prependContext }`; the
// docs/research/2026-09-01-gbrain-learnings/platform-seams.md row that named
// `~/.openclaw/*.claw` + `handle_AgentRequest` was NOT a real contract (no
// `.claw` file extension and no `handle_AgentRequest` symbol exist anywhere in
// openclaw/openclaw). The verified contract this file implements:
//
//   inject  before_prompt_build -> { prependContext }   (docs/plugins/hooks/
//           prompt-and-session.md; payload types in src/plugins/hook-types.ts:
//           PluginHookBeforePromptBuildEvent.prompt is a string)
//   capture llm_output -> { prompt?: string, assistantTexts: string[] }
//           (PluginHookLlmOutputEvent) appended to a local JSONL spool that
//           `tortoise sessions import --harness openclaw` parses
//
// This mirrors the Pi capture extension (`tortoise/pi-hooks/tortoise-capture.ts`):
// it is installed BY THE PRODUCT (`tortoise install openclaw`), it is
// self-contained, and it requires no local `tortoise` CLI — the reflex can run
// over the hosted `POST /v1/context` with an API key.
//
// Fail-open: a reflex failure must NEVER break the agent turn. Every handler
// catches and returns nothing; nothing here throws into the host.
//
// W4 user-exposure gate: nothing auto-registers. Installing this plugin IS the
// opt-in, and the Installer is the only writer (`tortoise install openclaw`).

import { execFile } from "node:child_process";
import { appendFileSync, mkdirSync } from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const PLUGIN_ID = "tortoise";
export const DEFAULT_TIMEOUT_MS = 800;
export const DEFAULT_API_URL = "https://api.premiselabs.co";
export const MAX_PROMPT_CHARS = 15000;

/** OpenClaw's state dir: `$OPENCLAW_STATE_DIR`, else `~/.openclaw`. */
export function stateDir(env = process.env, home = homedir()) {
  const override = env?.OPENCLAW_STATE_DIR;
  return override && override.trim() ? override : join(home, ".openclaw");
}

/** The directory holding the capture spools (one file per session). */
export function spoolDir(env = process.env, home = homedir()) {
  const override = env?.TORTOISE_OPENCLAW_SPOOL_DIR;
  return override && override.trim()
    ? override
    : join(stateDir(env, home), "tortoise", "turns");
}

/**
 * The spool file for ONE session.
 *
 * One file per session is load-bearing, not cosmetic: `tortoise sessions
 * import` files ONE session per file (it hashes the file for its idempotency
 * key and POSTs one conversation), so a single shared spool would file every
 * session as one merged conversation — wrong provenance and a wrong
 * `session_id`, permanently. The name is a filesystem-safe slug plus a short
 * digest of the raw key so two keys that slug identically cannot collide.
 */
export function spoolFile(sessionKey, sessionId, env = process.env, home = homedir()) {
  const raw = String(sessionKey || sessionId || "unknown");
  const slug = raw
    .replace(/[^A-Za-z0-9._-]+/g, "_")
    .replace(/^[._-]+/, "")
    .slice(0, 80)
    .replace(/[._-]+$/, "");
  const digest = createHash("sha256").update(raw).digest("hex").slice(0, 12);
  return join(spoolDir(env, home), `${slug || "session"}-${digest}.jsonl`);
}

function positiveInt(value, fallback) {
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

/** Normalize the plugin config block; never throws on malformed values. */
export function readConfig(apiConfig, env = process.env) {
  const raw = apiConfig && typeof apiConfig === "object" ? apiConfig : {};
  return {
    apiUrl:
      typeof raw.apiUrl === "string" && raw.apiUrl.trim()
        ? raw.apiUrl.trim()
        : env?.TORTOISE_API_URL || DEFAULT_API_URL,
    apiKey:
      typeof raw.apiKey === "string" && raw.apiKey.trim()
        ? raw.apiKey.trim()
        : env?.TORTOISE_API_KEY || "",
    command:
      typeof raw.command === "string" && raw.command.trim()
        ? raw.command.trim()
        : env?.TORTOISE_COMMAND || "",
    timeoutMs: positiveInt(raw.timeoutMs, DEFAULT_TIMEOUT_MS),
    capture: raw.capture !== false,
  };
}

/**
 * The reflex stdin payload for one turn — the JSON contract `tortoise
 * volunteer` documents (a `{window, …}` dict), NOT a flag set.
 *
 * ``prior_context`` is the CONTINUITY input (#2120 indicator 4): the block
 * this seam injected on an earlier turn, which the reflex's own stage-5
 * re-mention suppression reads. ``session_id`` is carried (validated, sent)
 * but is NOT consulted by the decision — the suppression set comes from
 * ``prior_context`` alone, so a seam that passes only a session id suppresses
 * nothing.
 *
 * A dict on stdin (rather than `--prior-context` argv flags) is deliberate:
 * a `tortoise` older than this seam ignores an unknown payload key and still
 * runs, while an unknown FLAG is an argparse error — which `runCommand`'s
 * fail-open turns into no injection at all, silently, on every turn.
 */
export function reflexPayload(prompt, { sessionId, priorContext } = {}) {
  return JSON.stringify({
    window: [{ role: "user", content: prompt }],
    session_id: sessionId ? String(sessionId) : null,
    prior_context: priorContext ? String(priorContext) : null,
  });
}

/** Run the ONE canonical reflex: `tortoise volunteer` over one turn's window. */
export function runCommand(command, prompt, timeoutMs, continuity = {}) {
  return new Promise((resolve) => {
    const child = execFile(
      command,
      ["volunteer"],
      { timeout: timeoutMs, maxBuffer: 1 << 20 },
      (error, stdout) => resolve(error ? "" : String(stdout ?? "")),
    );
    if (!child.stdin) {
      resolve("");
      return;
    }
    child.stdin.on("error", () => {});
    child.stdin.end(reflexPayload(prompt, continuity));
  });
}

/**
 * Run the reflex over the hosted pipeline: `POST /v1/context`.
 *
 * The body is the DOCUMENTED request contract
 * (``VolunteerContextRequest`` in ``tortoise/hosted_api.py``): a ``window`` of
 * ``{role, content}`` turns (required — a bare ``{prompt}`` is a 422 and the
 * fail-open catch would silently inject nothing) plus the optional
 * ``session_id`` / ``prior_context`` continuity fields.
 */
export async function runHosted(apiUrl, apiKey, prompt, timeoutMs, continuity = {}) {
  if (!apiKey) return "";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${apiUrl.replace(/\/+$/, "")}/v1/context`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        window: [{ role: "user", content: prompt }],
        session_id: continuity.sessionId ? String(continuity.sessionId) : null,
        prior_context: continuity.priorContext
          ? String(continuity.priorContext)
          : null,
      }),
      signal: controller.signal,
    });
    if (!response.ok) return "";
    const body = await response.json().catch(() => null);
    const block = body?.context ?? body?.block ?? "";
    return typeof block === "string" ? block : "";
  } catch {
    return "";
  } finally {
    clearTimeout(timer);
  }
}

/** Cross-turn dedupe (#2120 indicator 4): never re-inject the same block twice. */
export function createDedupe() {
  const seen = new Map();
  return (sessionKey, block) => {
    const digest = createHash("sha256").update(block).digest("hex");
    if (seen.get(sessionKey) === digest) return false;
    seen.set(sessionKey, digest);
    return true;
  };
}

/**
 * Cross-turn CONTINUITY (#2120 indicator 4).
 *
 * The digest dedupe above only catches an IDENTICAL block. A turn whose block
 * changes by one pointer (a second pointer clears the gate, or one drops out)
 * re-surfaces the pointers this session already saw — the false fire the reflex
 * itself suppresses, but only when it is handed `prior_context`. So remember
 * the last block per session key and pass it back as that input.
 */
export function createContinuity() {
  const last = new Map();
  return {
    prior: (sessionKey) => last.get(sessionKey) ?? "",
    remember: (sessionKey, block) => {
      last.set(sessionKey, block);
    },
  };
}

function textOf(value) {
  return typeof value === "string" ? value : "";
}

/** Append captured turns to their session's spool; best-effort, never throws. */
export function appendTurn(record, env = process.env, home = homedir()) {
  try {
    const target = spoolFile(record?.session_key, record?.session_id, env, home);
    mkdirSync(dirname(target), { recursive: true });
    appendFileSync(target, `${JSON.stringify(record)}\n`, { mode: 0o600 });
    return true;
  } catch {
    return false;
  }
}

/**
 * Build the capture records for one model call.
 *
 * Exported so the seam's own `node --test` suite pins the spool contract the
 * Python parser reads (`tortoise/session_import/parsers.py::parse_openclaw`) —
 * the two halves of one file format must not drift.
 */
export function captureRecords(event, ctx, now = Date.now()) {
  const prompt = textOf(event?.prompt).trim();
  const replies = Array.isArray(event?.assistantTexts)
    ? event.assistantTexts.filter((text) => typeof text === "string" && text.trim())
    : [];
  if (!prompt && replies.length === 0) return [];
  const base = {
    session_key: textOf(ctx?.sessionKey) || null,
    session_id: textOf(event?.sessionId) || null,
    agent_id: textOf(ctx?.agentId) || null,
    ts: new Date(now).toISOString(),
  };
  const records = [];
  if (prompt) records.push({ ...base, role: "user", text: prompt });
  for (const reply of replies) {
    records.push({ ...base, role: "assistant", text: reply });
  }
  return records;
}

export default function register(api) {
  const config = readConfig(api?.config);
  const dedupe = createDedupe();
  const continuity = createContinuity();

  api.on("before_prompt_build", async (event, ctx) => {
    try {
      if (ctx?.trigger && ctx.trigger !== "user") return undefined;
      const prompt = textOf(event?.prompt).slice(0, MAX_PROMPT_CHARS);
      if (!prompt.trim()) return undefined;
      const sessionKey = textOf(ctx?.sessionKey) || "openclaw";
      const session = {
        sessionId: sessionKey,
        priorContext: continuity.prior(sessionKey),
      };
      const block = config.command
        ? await runCommand(config.command, prompt, config.timeoutMs, session)
        : await runHosted(config.apiUrl, config.apiKey, prompt, config.timeoutMs, session);
      const text = textOf(block).trim();
      if (!text) return undefined;
      if (!dedupe(sessionKey, text)) return undefined;
      continuity.remember(sessionKey, text);
      return { prependContext: text };
    } catch {
      return undefined;
    }
  });

  if (config.capture) {
    api.on("llm_output", async (event, ctx) => {
      try {
        for (const record of captureRecords(event, ctx)) {
          appendTurn(record);
        }
      } catch {
        // Fail-open: a capture failure never breaks the turn.
      }
    });
  }
}
