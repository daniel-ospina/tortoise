// tortoise-capture.test.ts — behavioral tests for the in-repo Pi capture
// extension (#3575). Run with: node --test tortoise/pi-hooks/tortoise-capture.test.ts
// (Node 22+ strips TypeScript types natively — no build step, no deps.)
//
// These tests pin the two claims the dashboard's `HARNESS_CAPTURE_SUPPORT.pi`
// makes: (1) the extension fires the install-probe on load, and (2) it files
// sessions to /v1/sessions on shutdown — with the full conversation turns and
// the harness/session_id idempotency key, and NOTHING conversation-shaped in
// the probe.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  HARNESS,
  buildCapturePayload,
  deriveMachineId,
  extractTurns,
  modelLabel,
  postInstallProbe,
  resolveConfig,
  sourceName,
} from "./tortoise-capture.ts";
import tortoiseCapture from "./tortoise-capture.ts";

interface Call {
  url: string;
  body: Record<string, unknown>;
}

function mockPi() {
  const handlers: Record<string, (event?: unknown, ctx?: unknown) => unknown> = {};
  return {
    handlers,
    on(event: string, fn: (e?: unknown, c?: unknown) => unknown) {
      handlers[event] = fn;
    },
  };
}

function mockCtx(opts: {
  entries?: Array<Record<string, unknown>>;
  sessionId?: string;
  sessionFile?: string;
  model?: unknown;
} = {}) {
  return {
    sessionManager: {
      getEntries: () => opts.entries ?? [],
      getSessionId: () => opts.sessionId ?? "sess-1",
      getSessionFile: () => opts.sessionFile ?? "/tmp/sessions/2026-01-01_abc.jsonl",
    },
    model: opts.model,
  };
}

function mockFetch(responses: Array<{ ok: boolean; status?: number }>): {
  fetchImpl: (url: string, init?: Record<string, unknown>) => Promise<unknown>;
  calls: Call[];
} {
  const calls: Call[] = [];
  let i = 0;
  const fetchImpl = async (url: string, init?: Record<string, unknown>) => {
    const r = responses[Math.min(i++, responses.length - 1)];
    calls.push({
      url,
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return {
      ok: r.ok,
      status: r.status ?? (r.ok ? 200 : 500),
      json: async () => ({}),
    };
  };
  return { fetchImpl: fetchImpl as never, calls };
}

const PI_ENTRIES = [
  { type: "session", id: "h" },
  {
    type: "message",
    message: { role: "user", content: [{ type: "text", text: "Ship the capture seam." }] },
  },
  {
    type: "message",
    message: {
      role: "assistant",
      content: [
        { type: "thinking", thinking: "internal" },
        { type: "text", text: "On it." },
      ],
    },
  },
  { type: "message", message: { role: "toolResult", content: "noise" } },
  { type: "model_change", modelId: "x" },
  { type: "message", message: { role: "assistant", content: [] } },
];

test("extractTurns keeps user/assistant text only, in order", () => {
  assert.deepEqual(extractTurns(PI_ENTRIES), [
    { role: "user", content: "Ship the capture seam." },
    { role: "assistant", content: "On it." },
  ]);
});

test("extractTurns truncates at the hosted per-turn window", () => {
  const long = "a".repeat(9000);
  const turns = extractTurns([
    { type: "message", message: { role: "user", content: long } },
  ]);
  assert.equal(turns[0].content.length, 5000);
});

test("buildCapturePayload carries harness + session_id (the idempotency key)", () => {
  const payload = buildCapturePayload({
    sessionId: "sess-42",
    turns: [{ role: "user", content: "hi" }],
    source: "2026-01-01_abc",
    machineId: "deadbeef",
    model: "deepseek/deepseek-v4-flash",
  });
  assert.equal(payload.harness, HARNESS);
  assert.equal(payload.session_id, "sess-42");
  assert.equal(payload.source, "2026-01-01_abc");
  assert.equal(payload.machine_id, "deadbeef");
  assert.deepEqual(payload.conversation, [{ role: "user", content: "hi" }]);
});

test("sourceName is a basename only (never a full path)", () => {
  assert.equal(sourceName("/Users/x/.pi/agent/sessions/--p--/s.jsonl"), "s");
  assert.equal(sourceName(undefined), "pi");
});

test("deriveMachineId is a 64-char sha256 hex (mirrors session_attribution.py)", () => {
  assert.match(deriveMachineId(), /^[0-9a-f]{64}$/);
});

test("resolveConfig: env wins, then file, then hosted default", () => {
  assert.deepEqual(
    resolveConfig({ TORTOISE_API_KEY: " tt_env ", TORTOISE_API_URL: "https://h/" }, "/nope.json"),
    { apiKey: "tt_env", apiUrl: "https://h" },
  );
  assert.deepEqual(resolveConfig({}, "/nope.json"), {
    apiKey: "",
    apiUrl: "https://api.premiselabs.co",
  });
});

test("modelLabel renders provider/model and caps the server bound", () => {
  assert.equal(modelLabel({ provider: "deepseek", id: "deepseek-v4-flash" }),
    "deepseek/deepseek-v4-flash");
  assert.equal(modelLabel(undefined), undefined);
});

test("session_start fires the install-probe with NO conversation content", async () => {
  const pi = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(pi as never, { fetchImpl: fetchImpl as never });
  await pi.handlers.session_start();

  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/v1\/sessions\/install-probe$/);
  assert.deepEqual(calls[0].body, { harness: "pi" });
});

test("session_shutdown files the session's turns with harness=pi", async () => {
  const pi = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(pi as never, { fetchImpl: fetchImpl as never });
  await pi.handlers.session_shutdown({ reason: "quit" }, mockCtx({ entries: PI_ENTRIES }));

  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/v1\/sessions$/);
  assert.equal(calls[0].body.harness, "pi");
  assert.equal(calls[0].body.session_id, "sess-1");
  assert.equal(calls[0].body.source, "2026-01-01_abc");
  assert.deepEqual(calls[0].body.conversation, [
    { role: "user", content: "Ship the capture seam." },
    { role: "assistant", content: "On it." },
  ]);
});

test("session_shutdown on /reload does not capture (the session is still alive)", async () => {
  const pi = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(pi as never, { fetchImpl: fetchImpl as never });
  await pi.handlers.session_shutdown({ reason: "reload" }, mockCtx({ entries: PI_ENTRIES }));
  assert.equal(calls.length, 0);
});

test("an empty session is never posted", async () => {
  const pi = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(pi as never, { fetchImpl: fetchImpl as never });
  await pi.handlers.session_shutdown({ reason: "quit" }, mockCtx({ entries: [] }));
  assert.equal(calls.length, 0);
});

test("postInstallProbe refuses without a key (honest, no network call)", async () => {
  const calls: Call[] = [];
  const fetchImpl = async (url: string) => {
    calls.push({ url, body: {} });
    return { ok: true, status: 200, json: async () => ({}) };
  };
  const res = await postInstallProbe({ apiUrl: "https://h", apiKey: "" }, fetchImpl as never);
  assert.equal(res.ok, false);
  assert.equal(calls.length, 0);
});
