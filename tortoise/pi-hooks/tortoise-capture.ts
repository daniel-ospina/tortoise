// tortoise-capture — the in-repo Pi capture extension (#3575, #1727 T1).
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
import { readFileSync } from "node:fs";
import { homedir, hostname, userInfo } from "node:os";
import { basename, join } from "node:path";

// ── Constants ─────────────────────────────────────────────────────────────

export const HARNESS = "pi";
export const DEFAULT_API_URL = "https://api.premiselabs.co";
export const CONFIG_PATH = join(homedir(), ".pi", "agent", "tortoise-config.json");
/** Hosted POST /v1/sessions limits (SessionRequest: max_length=1000). */
export const MAX_TURNS = 1000;
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

type Env = Record<string, string | undefined>;
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
  return createHash("sha256").update(`${host}\0${user}`, "utf8").digest("hex");
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
    if (turns.length >= MAX_TURNS) break;
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
): Promise<PostResult> {
  return post(cfg, "/v1/sessions", payload, fetchImpl);
}

// ── Extension wiring ──────────────────────────────────────────────────────

export interface CaptureDeps {
  fetchImpl?: FetchLike;
}

function log(message: string): void {
  // Pi surfaces extension stdout in its session log.
  console.log(`[tortoise-capture] ${message}`);
}

function warn(message: string): void {
  console.warn(`[tortoise-capture] ${message}`);
}

export default function tortoiseCapture(pi: ExtensionAPI, deps: CaptureDeps = {}): void {
  const cfg = resolveConfig();
  const doFetch: FetchLike = deps.fetchImpl ?? (fetch as unknown as FetchLike);

  if (!cfg.apiKey) {
    warn(
      "no TORTOISE_API_KEY — capture is INACTIVE. Add it to your shell profile " +
        "and restart Pi from a new terminal (a /reload keeps the old environment).",
    );
  }

  // #1727 T2-P1: the SERVER-VISIBLE install signal — the extension-on-load
  // probe the server's install-probe route documents. Fired per session start
  // (startup/reload/new/resume/fork), matching Claude's SessionStart hook.
  //
  // FIRE-AND-FORGET: Pi AWAITS this handler (AgentSession.bindExtensions →
  // `await emit("session_start")`), so awaiting the probe would stall startup
  // on a blackholed endpoint. The probe is best-effort telemetry — it is
  // dispatched with a short budget and its result is reported when it lands.
  pi.on("session_start", () => {
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
  });

  // File the session when it ENDS. A reload keeps the SAME session alive
  // (extensions are re-created around it), so capturing there would be a
  // premature duplicate — the real end (quit/new/resume/fork) captures it.
  pi.on("session_shutdown", async (event, ctx) => {
    if (event.reason === "reload") return;
    try {
      const manager = ctx.sessionManager;
      const entries = (manager.getEntries?.() ?? []) as Array<Record<string, unknown>>;
      const turns = extractTurns(entries);
      if (turns.length === 0) return;
      const sessionId = manager.getSessionId?.() ?? "";
      if (!sessionId) return;
      const payload = buildCapturePayload({
        sessionId,
        turns,
        source: sourceName(manager.getSessionFile?.()),
        machineId: deriveMachineId(),
        model: modelLabel(ctx.model),
      });
      const res = await postCapture(cfg, payload, doFetch);
      if (res.ok) {
        log(`captured session ${sessionId} (${turns.length} turns) → ${cfg.apiUrl}`);
      } else {
        warn(`capture FAILED for session ${sessionId} (${res.detail ?? res.status}) — it was NOT filed`);
      }
    } catch (err) {
      warn(`capture error: ${err instanceof Error ? err.message : String(err)}`);
    }
  });
}
