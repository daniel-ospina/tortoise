// tortoise-capture.test.ts — behavioral tests for the in-repo Pi capture
// extension (#3575). Run with: node --test tortoise/pi-hooks/tortoise-capture.test.ts
// (Node 22+ strips TypeScript types natively — no build step, no deps.)
//
// These tests pin the two claims the dashboard's `HARNESS_CAPTURE_SUPPORT.pi`
// makes: (1) the extension fires the install-probe on load, and (2) it files
// sessions to /v1/sessions on shutdown — with the full conversation turns and
// the harness/session_id idempotency key, and NOTHING conversation-shaped in
// the probe.
import { mock, test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import {
  HARNESS,
  MAX_TURNS,
  PROBE_TIMEOUT_MS,
  REQUEST_TIMEOUT_MS,
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

test("extractTurns keeps the MOST RECENT turns at the cap (matches the backfill leg)", () => {
  // #3707: an early `break` kept the OLDEST MAX_TURNS while the Python
  // backfill (`window_turns` = turns[-MAX_TURNS:]) kept the NEWEST — the two
  // legs of one seam filed opposite halves of a >MAX_TURNS session. Pin the
  // direction: the LAST turns must survive.
  const n = MAX_TURNS + 5;
  const entries = Array.from({ length: n }, (_, i) => ({
    type: "message",
    message: { role: i % 2 === 0 ? "user" : "assistant", content: `turn ${i}` },
  }));
  const turns = extractTurns(entries as never);
  assert.equal(turns.length, MAX_TURNS);
  assert.equal(turns[0].content, `turn ${n - MAX_TURNS}`,
    "the oldest turns must be dropped, not the newest");
  assert.equal(turns[turns.length - 1].content, `turn ${n - 1}`);
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

// #2369 D1.1 co-source, mirrored from tortoise/__main__.py::_resolve_config_path
// and tests/test_cli_resolver.py. The key and the URL are ONE source chain: a
// FILE-sourced key must never be redirected by the env URL (split-brain → the
// stored credential and every captured conversation leak to an attacker host).
function writeConfig(json: Record<string, unknown>): string {
  const dir = mkdtempSync(join(tmpdir(), "tortoise-capture-cfg-"));
  const path = join(dir, "tortoise-config.json");
  writeFileSync(path, JSON.stringify(json));
  return path;
}

function withTempConfig<T>(json: Record<string, unknown>, fn: (path: string) => T): T {
  const path = writeConfig(json);
  try {
    return fn(path);
  } finally {
    rmSync(dirname(path), { recursive: true, force: true });
  }
}

test("resolveConfig co-source: a FILE key ignores a poisoned env URL (built-in default)", () => {
  withTempConfig({ apiKey: "tt_stored" }, (path) => {
    const cfg = resolveConfig(
      { TORTOISE_API_URL: "http://attacker.example:9999" },
      path,
    );
    assert.equal(cfg.apiKey, "tt_stored");
    assert.equal(cfg.apiUrl, "https://api.premiselabs.co",
      "a stored key must NEVER resolve its URL from env");
  });
});

test("resolveConfig co-source: a FILE key keeps its own apiUrl over a poisoned env URL", () => {
  withTempConfig({ apiKey: "tt_stored", apiUrl: "https://selfhosted.example.com" }, (path) => {
    const cfg = resolveConfig(
      { TORTOISE_API_URL: "http://attacker.example:9999" },
      path,
    );
    assert.equal(cfg.apiKey, "tt_stored");
    assert.equal(cfg.apiUrl, "https://selfhosted.example.com");
  });
});

test("resolveConfig co-source: an ENV key uses the env URL (file identity loses)", () => {
  withTempConfig(
    { apiKey: "tt_stored", apiUrl: "https://selfhosted.example.com" },
    (path) => {
      const cfg = resolveConfig(
        { TORTOISE_API_KEY: "tt_env", TORTOISE_API_URL: "https://env-host.example.com" },
        path,
      );
      assert.equal(cfg.apiKey, "tt_env");
      assert.equal(cfg.apiUrl, "https://env-host.example.com");
    },
  );
});

test("the install probe is budgeted far shorter than the capture", () => {
  assert.ok(PROBE_TIMEOUT_MS > 0);
  assert.ok(PROBE_TIMEOUT_MS < REQUEST_TIMEOUT_MS,
    "the probe must not inherit the 10s capture budget — Pi awaits session_start");
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
  // Fire-and-forget: the fetch is dispatched synchronously, so it is already
  // recorded by the time the handler returns; its .then() settles next tick.
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/v1\/sessions\/install-probe$/);
  assert.deepEqual(calls[0].body, { harness: "pi" });
});

test("session_start resolves WITHOUT awaiting the probe (Pi awaits the handler at startup)", async () => {
  const pi = mockPi();
  // A probe fetch that never settles — models a BLACKHOLED endpoint (SYN
  // dropped by a firewall/VPN/captive portal; not a refusal). If the handler
  // awaited it, Pi's startup would stall for the full probe budget.
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = () => resolve();
  });
  const fetchImpl = async () => {
    await gate;
    return { ok: true, status: 200, json: async () => ({}) };
  };
  tortoiseCapture(pi as never, { fetchImpl: fetchImpl as never });

  const outcome = await Promise.race([
    Promise.resolve(pi.handlers.session_start()).then(() => "RESOLVED"),
    new Promise((resolve) => setTimeout(() => resolve("STALLED"), 250)),
  ]);
  assert.equal(outcome, "RESOLVED",
    "session_start must not await the probe fetch — a blackholed endpoint would block Pi");
  release();
  await new Promise((resolve) => setImmediate(resolve));
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

test("the probe ABORTS on its own budget — PROBE_TIMEOUT_MS, not the capture's", async () => {
  // Behavioural budget check (B3: a constant comparison is a spelling). The
  // fetch models a BLACKHOLED endpoint: it holds the socket open until the
  // signal aborts. Advancing the clock by ONLY the probe budget must fire the
  // abort — a probe wired to REQUEST_TIMEOUT_MS would still be pending here.
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    let aborted = false;
    const fetchImpl = (_url: string, init?: Record<string, unknown>) =>
      new Promise((_resolve, reject) => {
        (init?.signal as AbortSignal).addEventListener("abort", () => {
          aborted = true;
          const err = new Error("The operation was aborted");
          err.name = "AbortError";
          reject(err);
        });
      });

    const pending = postInstallProbe(
      { apiUrl: "https://h", apiKey: "tt_x" },
      fetchImpl as never,
    );
    mock.timers.tick(PROBE_TIMEOUT_MS);
    // Let the abort callback + the rejection propagate through `post`.
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(aborted, true,
      "the probe must abort at PROBE_TIMEOUT_MS — a 10s budget stalls Pi startup");
    const res = await pending;
    assert.equal(res.ok, false);
    assert.match(String(res.detail), /timed out after 2s/,
      "the reported budget must be the probe's, not the capture's");
  } finally {
    mock.timers.reset();
  }
});
