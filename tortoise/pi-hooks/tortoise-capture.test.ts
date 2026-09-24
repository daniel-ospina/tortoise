// tortoise-capture.test.ts — behavioral tests for the in-repo Pi capture
// extension (#3575). Run with: node --test tortoise/pi-hooks/tortoise-capture.test.ts
// (Node 22.18+ strips TypeScript types natively — default-on type stripping;
// on 22.7-22.17 the `--experimental-strip-types` flag is required. No build
// step, no deps.)
//
// These tests pin the two claims the dashboard's `HARNESS_CAPTURE_SUPPORT.pi`
// makes: (1) the extension fires the install-probe on load, and (2) it files
// sessions to /v1/sessions on shutdown — with the full conversation turns and
// the harness/session_id idempotency key, and NOTHING conversation-shaped in
// the probe.
import { mock, test } from "node:test";
import assert from "node:assert/strict";
import {
  appendFileSync,
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  utimesSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import {
  HARNESS,
  MAX_ATTEMPTS,
  MAX_TURNS,
  PROBE_TIMEOUT_MS,
  REQUEST_TIMEOUT_MS,
  RETRY_MAX_MS,
  backoffDelay,
  buildCapturePayload,
  captureKey,
  carriedWindow,
  clampAttempts,
  clampWindow,
  classifyFailure,
  contentDigest,
  deriveMachineId,
  discardEntry,
  entryKey,
  extractTurns,
  flushSpool,
  listSpoolEntries,
  modelLabel,
  postInstallProbe,
  readDiscards,
  readSpoolEntry,
  readSpoolTurns,
  resolveConfig,
  sourceName,
  writeSpoolEntry,
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

// A HERMETIC dependency set for every `tortoiseCapture(...)` under test.
// The extension resolves its credential through the REAL `resolveConfig`
// chain (env → `~/.pi/agent/tortoise-config.json`). That is ambient state: a
// machine with a key made these tests pass while asserting the machine — and
// on a clean CI runner, where neither source exists, `post()` short-circuited
// BEFORE the injected fetch was ever called, so `calls.length` read 0 and the
// "no capture happened" assertions passed VACUOUSLY (#3721). Inject the
// resolver's two INPUTS, never a stubbed config: the shipped co-source chain
// stays in the tested path (its own unit tests above pin its semantics).
const TEST_ENV = { TORTOISE_API_KEY: "tt_test", TORTOISE_API_URL: "https://h" };
const NO_CONFIG_PATH = "/nonexistent/tortoise-config.json";

/**
 * #3963: a FRESH spool directory per extension instance. The spool is now part
 * of the capture path, so leaving it at its default (`~/.tortoise/capture-spool`)
 * would make the suite read — and FILE — the developer machine's real captures.
 * Same ambient-state trap as #3721, one layer deeper.
 */
let spoolSeq = 0;
function tmpSpool(): string {
  return mkdtempSync(join(tmpdir(), `tortoise-capture-spool-${process.pid}-${spoolSeq++}-`));
}

function hermeticDeps(fetchImpl: unknown, spoolDir: string = tmpSpool()) {
  return {
    fetchImpl: fetchImpl as never,
    env: TEST_ENV,
    configPath: NO_CONFIG_PATH,
    spoolDir,
  };
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
  // #3721 P2-2: the model arg must reach the payload. The #2599 client-claimed
  // Session property is what the dashboard renders; dropping this line shipped
  // silently because no assertion read it (mutation: delete it — suite green).
  assert.equal(payload.model, "deepseek/deepseek-v4-flash");
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
  // #3721 P2-1: the server's `SessionRequest.model` is `max_length=128`
  // (hosted_api.py), so an UNCAPPED label 422s the live capture — silently,
  // because only the hosted leg sees it. Exercise the cap itself: a
  // >128-char provider/id must return EXACTLY 128 chars. (Mutation: drop the
  // `.slice(0, 128)` — before this case the 19-test suite stayed green.)
  const long = modelLabel({ provider: "p".repeat(200), id: "i".repeat(200) });
  assert.equal(long?.length, 128,
    "a >128-char model label must be capped to the server's max_length=128");
});

test("session_start fires the install-probe with NO conversation content", async () => {
  const pi = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl));
  await pi.handlers.session_start(undefined, mockCtx({ entries: [] }) as never);
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
  let dispatched = false;
  const fetchImpl = async () => {
    dispatched = true;
    await gate;
    return { ok: true, status: 200, json: async () => ({}) };
  };
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl));

  const outcome = await Promise.race([
    Promise.resolve(pi.handlers.session_start(undefined, mockCtx({ entries: [] }) as never)).then(() => "RESOLVED"),
    new Promise((resolve) => setTimeout(() => resolve("STALLED"), 250)),
  ]);
  assert.equal(outcome, "RESOLVED",
    "session_start must not await the probe fetch — a blackholed endpoint would block Pi");
  // #3721: the probe must have been DISPATCHED. Without this, the assertion
  // above also holds when the probe never fires at all — a no-credential
  // `postInstallProbe` resolves instantly, so the race passes vacuously and
  // the test could not RED. Dispatch is synchronous (post() awaits the fetch
  // it has already invoked), so it is observable by the time the handler
  // returns.
  assert.equal(dispatched, true,
    "the probe fetch must be dispatched (then NOT awaited) — an unfired probe passes the race vacuously");
  release();
  await new Promise((resolve) => setImmediate(resolve));
});

test("session_shutdown files the session's turns with harness=pi", async () => {
  const pi = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl));
  // #3721 P2-2: the ctx model must be wired INTO the payload through the real
  // `modelLabel(ctx.model)` → `buildCapturePayload({ model })` path, not just
  // accepted by the builder in isolation.
  await pi.handlers.session_shutdown(
    { reason: "quit" },
    mockCtx({ entries: PI_ENTRIES, model: { provider: "deepseek", id: "deepseek-v4-flash" } }),
  );

  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/v1\/sessions$/);
  assert.equal(calls[0].body.harness, "pi");
  assert.equal(calls[0].body.session_id, "sess-1");
  assert.equal(calls[0].body.source, "2026-01-01_abc");
  assert.equal(calls[0].body.model, "deepseek/deepseek-v4-flash",
    "the active model must be attributed on the capture (#2599)");
  assert.deepEqual(calls[0].body.conversation, [
    { role: "user", content: "Ship the capture seam." },
    { role: "assistant", content: "On it." },
  ]);
});

test("session_shutdown on /reload does not capture (the session is still alive)", async () => {
  const pi = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl));
  await pi.handlers.session_shutdown({ reason: "reload" }, mockCtx({ entries: PI_ENTRIES }));
  assert.equal(calls.length, 0);
});

test("an empty session is never posted", async () => {
  const pi = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl));
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

// ═══════════════════════════════════════════════════════════════════════════
// #3963 — the durable spool: incremental capture, idempotent replay, bounds.
//
// Every test below names the mutation that REDs it in its own body. A test that
// cannot RED is not evidence, so each one asserts an observable effect of the
// behaviour (a spool file, a ledger line, a POST count) rather than a constant.
// ═══════════════════════════════════════════════════════════════════════════

const TEST_CFG = { apiUrl: "https://h", apiKey: "tt_test" };
const SPOOL_TURNS = extractTurns(PI_ENTRIES);

/** Let fire-and-forget flushes (dispatched by handlers) settle. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

/** A server that upserts by `session_id` (like `/v1/sessions`) and counts POSTs. */
function recordingServer() {
  const sessions = new Map<string, Record<string, unknown>>();
  let posts = 0;
  const fetchImpl = async (url: string, init?: Record<string, unknown>) => {
    const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
    if (/\/v1\/sessions$/.test(url)) {
      posts += 1;
      sessions.set(String(body.session_id), body);
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  return { fetchImpl: fetchImpl as never, sessions, posts: () => posts };
}

/** A fetch that answers with a fixed HTTP status + detail. */
function statusFetch(status: number, detail?: string) {
  const calls: string[] = [];
  const fetchImpl = async (url: string) => {
    calls.push(url);
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => (detail ? { detail } : {}),
    };
  };
  return { fetchImpl: fetchImpl as never, calls };
}

function snapshot(sessionId: string, turns = SPOOL_TURNS, source = "s.jsonl") {
  return { sessionId, turns, source, machineId: deriveMachineId() };
}

// ── (1) Interrupted mid-conversation → filed on the next opportunity ───────

test("an interrupted session (no session_shutdown) is filed at the next session_start", async () => {
  // The failure mode: SIGINT / laptop closed / a cancelled SessionEnd hook. No
  // `session_shutdown` ever fires for session A — only a `turn_end`.
  //
  // MUTATIONS THAT RED THIS:
  //   * delete the `pi.on("turn_end", …)` spool write → nothing to replay;
  //   * delete the `flush("session_start")` call   → never filed.
  const spool = tmpSpool();

  // Session A: one turn, then the process dies.
  const piA = mockPi();
  tortoiseCapture(piA as never, hermeticDeps(async () => {
    throw new Error("no network in this phase");
  }, spool));
  piA.handlers.turn_end(
    {},
    mockCtx({ entries: PI_ENTRIES, sessionId: "sess-A", sessionFile: "/tmp/sessions/a.jsonl" }),
  );
  // The cheap capture landed BEFORE any network attempt — this is the claim.
  const spooled = readSpoolEntry(spool, "sess-A");
  assert.ok(spooled, "the interrupted session's turns must be durable in the spool");
  assert.equal(spooled.turns_count, SPOOL_TURNS.length);
  assert.deepEqual(readSpoolTurns(spool, "sess-A"), SPOOL_TURNS);

  // Session B (a later Pi run) starts: the replay opportunity.
  const piB = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(piB as never, hermeticDeps(fetchImpl, spool));
  await piB.handlers.session_start(undefined, mockCtx({ entries: [] }) as never);
  await settle();

  const filed = calls.filter((c) => /\/v1\/sessions$/.test(c.url));
  assert.equal(filed.length, 1, "exactly one filing of the interrupted session");
  assert.equal(filed[0].body.session_id, "sess-A");
  assert.equal(filed[0].body.harness, "pi");
  assert.deepEqual(filed[0].body.conversation, SPOOL_TURNS);
});

// ── (2) Replaying a spooled session twice produces ONE session ─────────────

test("replaying a spooled session twice produces one session and posts once", async () => {
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-replay"));

  // MUTATIONS THAT RED THIS:
  //   * drop the `filed_key === capture_key` short-circuit → 2 POSTs;
  //   * make `capture_key` unstable (a timestamp/random salt) → the second
  //     flush sees a NEW key and re-posts → 2 POSTs.
  const server = recordingServer();
  const first = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  const second = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 2 });

  assert.equal(first.filed, 1);
  assert.equal(second.attempted, 0, "the replay must not re-attempt an identical capture");
  assert.equal(second.skipped, 1, "the replay must be a recorded skip, not a second POST");
  assert.equal(server.posts(), 1, "replaying the SAME content must issue ONE POST");
  assert.equal(server.sessions.size, 1, "and the server must hold ONE session");
});

test("the capture key is content-addressed: changed turns get a new key, a replay does not", () => {
  // MUTATION THAT REDS THIS: drop `session_id` (or the content digest) from
  // `captureKey` → a changed conversation reuses the old key and its new turns
  // are short-circuited as "already filed".
  const a = captureKey("s1", [{ role: "user", content: "one" }]);
  assert.equal(a, captureKey("s1", [{ role: "user", content: "one" }]), "stable across replays");
  assert.notEqual(a, captureKey("s1", [{ role: "user", content: "two" }]), "content-sensitive");
  assert.notEqual(a, captureKey("s2", [{ role: "user", content: "one" }]), "session-sensitive");
  assert.equal(contentDigest([{ role: "user", content: "one" }]).length, 64);
});

// ── (3) A spool entry past its bound is discarded WITH A REASON ────────────

test("an entry past its byte bound is discarded with a recorded reason", () => {
  const spool = tmpSpool();
  const big = Array.from({ length: 4 }, () => ({
    role: "assistant" as const,
    content: "x".repeat(4000),
  }));

  // MUTATION THAT REDS THIS: delete the per-entry `entryBytes > maxEntryBytes`
  // check → the entry is written, `discards` is empty, and the ledger stays
  // empty — the silent drop this issue exists to remove.
  const bounds = { maxEntries: 100, maxTotalBytes: 1_000_000, maxEntryBytes: 2_000 };
  const { written, discards } = writeSpoolEntry(spool, snapshot("sess-big", big), bounds);

  assert.equal(written, false, "an over-bound entry must not be spooled");
  assert.equal(discards.length, 1);
  assert.equal(discards[0].reason, "entry_too_large");
  assert.match(String(discards[0].detail), /maxEntryBytes=2000/);
  assert.equal(readSpoolEntry(spool, "sess-big"), undefined, "the entry is not on disk");

  // Observable: the append-only ledger names the session and the reason.
  const ledger = readDiscards(spool);
  assert.equal(ledger.length, 1);
  assert.equal(ledger[0].session_id, "sess-big");
  assert.equal(ledger[0].reason, "entry_too_large");
});

test("the spool count bound evicts the OLDEST entry with a recorded reason", () => {
  const spool = tmpSpool();
  const bounds = { maxEntries: 2, maxTotalBytes: 1_000_000, maxEntryBytes: 10_000 };
  // MUTATION THAT REDS THIS: delete the `pruneSpool` call (or its count
  // condition) → all three entries survive → count is 3, ledger is empty.
  writeSpoolEntry(spool, { ...snapshot("s1"), turns: [{ role: "user", content: "1" }] }, bounds);
  writeSpoolEntry(spool, { ...snapshot("s2"), turns: [{ role: "user", content: "2" }] }, bounds);
  writeSpoolEntry(spool, { ...snapshot("s3"), turns: [{ role: "user", content: "3" }] }, bounds);

  assert.equal(readSpoolEntry(spool, "s1"), undefined, "oldest evicted");
  assert.ok(readSpoolEntry(spool, "s2"), "next-oldest kept");
  assert.ok(readSpoolEntry(spool, "s3"), "newest kept");
  const ledger = readDiscards(spool);
  assert.equal(ledger.length, 1);
  assert.equal(ledger[0].session_id, "s1");
  assert.equal(ledger[0].reason, "spool_count_exceeded");
});

test("the spool byte bound evicts with its own recorded reason", () => {
  const spool = tmpSpool();
  const chunk = [{ role: "user" as const, content: "y".repeat(2000) }];
  const bounds = { maxEntries: 100, maxTotalBytes: 6_000, maxEntryBytes: 10_000 };
  writeSpoolEntry(spool, { ...snapshot("b1"), turns: chunk }, bounds);
  writeSpoolEntry(spool, { ...snapshot("b2"), turns: chunk }, bounds);
  writeSpoolEntry(spool, { ...snapshot("b3"), turns: chunk }, bounds);

  // MUTATION THAT REDS THIS: drop the byte condition from pruneSpool → all
  // three entries stay and the ledger has no `spool_total_bytes_exceeded` line.
  assert.equal(readSpoolEntry(spool, "b1"), undefined, "oldest evicted under the byte bound");
  assert.ok(readDiscards(spool).some((d) => d.reason === "spool_total_bytes_exceeded"));
});

// ── (4) Endpoint unreachable → retry with backoff, never a loss ────────────

test("a network failure defers the entry (retry with backoff), it is never lost", async () => {
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-net"));
  const networkDown = async () => {
    throw new Error("ECONNREFUSED");
  };

  // MUTATION THAT REDS THIS: on a transient failure, delete the entry (or mark
  // it filed) instead of deferring → `readSpoolEntry` is gone and the later
  // flush files nothing.
  const first = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: networkDown as never, now: 1_000 });
  assert.equal(first.deferred, 1);
  assert.equal(first.filed, 0);
  assert.equal(first.discarded.length, 0);

  const meta = readSpoolEntry(spool, "sess-net");
  assert.ok(meta, "the entry survives a network failure");
  assert.equal(meta.attempts, 1);
  assert.equal(meta.next_attempt_at_ms, 1_000 + backoffDelay(1));
  assert.deepEqual(readSpoolTurns(spool, "sess-net"), SPOOL_TURNS, "the turns survive verbatim");

  // Inside the backoff window: skipped, NOT attempted (and NOT lost).
  const { fetchImpl: inWindowFetch, calls: inWindowCalls } = statusFetch(200);
  const insideWindow = await flushSpool(TEST_CFG, {
    dir: spool,
    fetchImpl: inWindowFetch,
    now: 1_000 + backoffDelay(1) - 1,
  });
  assert.equal(insideWindow.attempted, 0);
  assert.equal(insideWindow.skipped, 1);
  assert.equal(inWindowCalls.length, 0, "no POST inside the backoff window");

  // Next opportunity, after the window: the retry succeeds.
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  const retry = await flushSpool(TEST_CFG, {
    dir: spool,
    fetchImpl,
    now: 1_000 + backoffDelay(1),
  });
  assert.equal(retry.filed, 1);
  assert.equal(calls.length, 1);
});

test("backoff grows exponentially and is capped", () => {
  // MUTATION THAT REDS THIS: return a constant (no growth) or drop the cap →
  // either the retry storm or an unbounded wait.
  assert.ok(backoffDelay(2) > backoffDelay(1));
  assert.ok(backoffDelay(3) > backoffDelay(2));
  assert.ok(backoffDelay(100) <= 6 * 60 * 60 * 1000);
});

// ── (5) Permanent 4xx → discard WITH a reason, never retried ───────────────

test("a permanent 4xx discards with a recorded reason and is never retried", async () => {
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-400"));
  const { fetchImpl, calls } = statusFetch(400, "bad payload");

  // MUTATIONS THAT RED THIS:
  //   * classify 400 as retryable → no discard, attempts=1, entry stays;
  //   * discard WITHOUT recordDiscard → the ledger stays empty (silent drop).
  const first = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1_000 });
  assert.equal(first.discarded.length, 1);
  assert.equal(first.discarded[0].reason, "permanent_http_400");
  assert.equal(readSpoolEntry(spool, "sess-400"), undefined, "a permanent failure is not kept");
  assert.equal(readDiscards(spool).length, 1, "the discard is observable on disk");

  // A later opportunity must NOT retry what was permanently rejected.
  const second = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 10 ** 12 });
  assert.equal(second.attempted, 0);
  assert.equal(calls.length, 1, "exactly one POST, never a retry loop");
});

test("a quota-refused 402 defers the capture instead of destroying it (#4714)", async () => {
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-402"));
  // The hosted quota gate refuses a capture whose ESTIMATED point cost would
  // cross the org cap; that estimate comes from the INCOMING capture, so the
  // same capture succeeds once a node is freed.
  //
  // MUTATIONS THAT RED THIS:
  //   * drop 402 from the transient set → discarded, entry unlinked, capture GONE;
  //   * unlink the meta/log but still count it deferred → readSpoolEntry undefined.
  const quota = statusFetch(
    402,
    "Team points limit reached: 24956 in use + 48 estimated for this capture exceeds 25000. Upgrade your plan.",
  );
  const first = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: quota.fetchImpl, now: 1_000 });
  assert.equal(first.deferred, 1, "a quota refusal must be retried, not dropped");
  assert.equal(first.discarded.length, 0, "a quota refusal must never discard");
  assert.ok(readSpoolEntry(spool, "sess-402"), "the spool's only copy of the session was destroyed");

  // And it is genuinely retryable: once the quota allows it, the SAME entry
  // files without a re-capture. The spool is shared with the Python leg
  // (~/.tortoise/capture-spool), which classifies 402 retryable too — the two
  // classifiers are a parity contract, not two independent policies.
  const ok = statusFetch(200);
  const second = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: ok.fetchImpl, now: 10 ** 12 });
  assert.equal(second.filed, 1, "the deferred capture must file once the quota clears");
});

test("a new turn does not re-arm the retry window (#4714 review)", async () => {
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-grow", SPOOL_TURNS.slice(0, 1)));
  // The backoff belongs to the ENTRY, not to one snapshot. `writeSpoolEntry`
  // used to hard-code attempts=0 / next_attempt_at_ms=0, so a growing session
  // re-armed its own window every turn and a deferred 402 was re-POSTed at turn
  // cadence. The spool is shared with the Python leg, so any other session's
  // drain fires it too.
  //
  // MUTATION THAT REDS THIS: reset attempts/next_attempt_at_ms in
  // `writeSpoolEntry` → the window collapses and the entry is POSTed again
  // 1 ms later.
  const refused = statusFetch(402, "quota");
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: refused.fetchImpl, now: 1_000 });
  const armed = readSpoolEntry(spool, "sess-grow");
  assert.equal(armed?.attempts, 1);
  assert.ok(
    (armed?.next_attempt_at_ms ?? 0) > 1_000,
    "the refusal must arm a backoff window",
  );

  // ONE new turn — the entry grows, and the window must survive it.
  writeSpoolEntry(spool, snapshot("sess-grow", [...SPOOL_TURNS, SPOOL_TURNS[0]]));
  const grown = readSpoolEntry(spool, "sess-grow");
  assert.equal(grown?.attempts, 1, "a new turn reset the attempt counter");
  assert.equal(
    grown?.next_attempt_at_ms,
    armed?.next_attempt_at_ms,
    "a new turn re-armed the backoff",
  );

  // A drain 1 ms later must SKIP it, not re-POST.
  const again = statusFetch(402, "quota");
  const summary = await flushSpool(TEST_CFG, {
    dir: spool,
    fetchImpl: again.fetchImpl,
    now: 1_001,
  });
  assert.equal(summary.attempted, 0, "an entry inside its backoff window was re-POSTed");
  assert.equal(summary.skipped, 1);
  assert.equal(again.calls.length, 0);
});

test("a non-finite backoff can never make an entry un-fileable (#4714 review)", async () => {
  // The backoff is CARRIED across turns now, so a stored Infinity would be
  // preserved on every write and the entry would be skipped forever while every
  // surface reports "will retry". And `backoffDelay` computes 2 ** (n - 1), so
  // an absurd attempt count must saturate rather than attempt a huge exponent.
  //
  // MUTATION THAT REDS THIS: return the raw value from clampWindow /
  // clampAttempts instead of clamping.
  assert.equal(clampWindow(Number.POSITIVE_INFINITY), 0);
  assert.equal(clampWindow(Number.NaN), 0);
  assert.equal(clampWindow(-5), 0);
  assert.equal(clampWindow(1_700_000_030_000), 1_700_000_030_000);
  assert.equal(clampAttempts(Number.POSITIVE_INFINITY), 0);
  assert.equal(clampAttempts(1e9), MAX_ATTEMPTS);
  assert.ok(Number.isFinite(backoffDelay(Number.POSITIVE_INFINITY)));
  assert.equal(backoffDelay(1e9), RETRY_MAX_MS);

  // End to end: a corrupt on-disk window must not stop the entry being POSTed.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-inf"));
  const metaPath = join(
    spool,
    "entries",
    `${entryKey("sess-inf")}.meta.json`,
  );
  const corrupt = JSON.parse(readFileSync(metaPath, "utf-8"));
  corrupt.next_attempt_at_ms = null;   // JSON has no Infinity — this is the shape
  corrupt.attempts = "lots";
  writeFileSync(metaPath, `${JSON.stringify(corrupt, null, 2)}\n`);

  const ok = statusFetch(200);
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: ok.fetchImpl, now: 1_000 });
  assert.equal(summary.attempted, 1, "a corrupt backoff made the entry un-fileable");
  assert.equal(summary.filed, 1);
});

test("a deferred-only flush is counted, not silent (#4714 review)", async () => {
  // Moving 402 from "discard" to "defer" removed the flush's only signal for a
  // quota-blocked spool: a window-held entry is neither attempted nor
  // discarded, so `reportFlush` logged NOTHING while captures sat unfiled.
  // `heldByBackoff` is that missing signal.
  //
  // MUTATION THAT REDS THIS: drop the `heldByBackoff` increment.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-wait"));
  const first = statusFetch(402, "quota");
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: first.fetchImpl, now: 1_000 });

  const again = statusFetch(402, "quota");
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: again.fetchImpl, now: 1_001 });
  assert.equal(summary.attempted, 0);
  assert.equal(summary.skipped, 1);
  assert.equal(summary.heldByBackoff, 1, "a window-held entry is invisible to the flush");
  assert.equal(again.calls.length, 0);
});

test("an absurd finite window is treated as corrupt, not honoured (#4714 review)", () => {
  // A legitimate window is at most `written_at + RETRY_MAX_MS`; anything
  // further out is corrupt, and because the write path CARRIES it, honouring it
  // would strand the entry on every turn.
  //
  // MUTATION THAT REDS THIS: return the raw value from `carriedWindow`.
  assert.equal(carriedWindow(9.9e15), 0);
  assert.equal(carriedWindow(31_000), 31_000);
  assert.equal(carriedWindow(Number.POSITIVE_INFINITY), 0);
});

test("a failure racing a successful filing does not re-arm the window (#4714 cycle-7 review)", async () => {
  // The failure write-back re-reads the meta but never checked whether the
  // content it failed to POST had since been FILED by a concurrent flush of the
  // same capture_key. Re-arming the backoff there attaches a window to content
  // that was never refused — and since writeSpoolEntry now CARRIES the window,
  // the next turn's NEW content inherits it and waits up to RETRY_MAX_MS with no
  // attempt behind it.
  //
  // MUTATION THAT REDS THIS: drop the filed_key guard in the write-back.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-race"));
  const metaPath = join(spool, "entries", `${entryKey("sess-race")}.meta.json`);

  const fetchImpl = async () => {
    // Simulate a CONCURRENT flush completing a 2xx while our POST is in flight.
    const onDisk = readSpoolEntry(spool, "sess-race")!;
    onDisk.filed_key = onDisk.capture_key;
    onDisk.attempts = 0;
    onDisk.next_attempt_at_ms = 0;
    writeFileSync(metaPath, `${JSON.stringify(onDisk, null, 2)}\n`);
    return { ok: false, status: 402, json: async () => ({ detail: "quota" }) };
  };

  const summary = await flushSpool(TEST_CFG, {
    dir: spool,
    fetchImpl: fetchImpl as never,
    now: 1_000,
  });
  assert.equal(summary.attempted, 1);
  assert.equal(summary.skipped, 1, "a failure for superseded content is not a retry");
  assert.equal(summary.deferred, 0);

  const after = readSpoolEntry(spool, "sess-race")!;
  assert.equal(after.attempts, 0, "the failure re-armed a window on filed content");
  assert.equal(after.next_attempt_at_ms, 0, "a resurrected window was attached");

  // And the NEXT turn must not inherit a window it never earned.
  writeSpoolEntry(spool, snapshot("sess-race", [...SPOOL_TURNS, SPOOL_TURNS[0]]));
  const grown = readSpoolEntry(spool, "sess-race")!;
  assert.equal(
    grown.next_attempt_at_ms,
    0,
    "new content inherited a backoff window it never earned",
  );
});

test("the server's in-flight 409 is retryable, not a lost write (#3713)", async () => {
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-409"));
  // MUTATION THAT REDS THIS: treat every 409 as permanent → the entry is
  // discarded and #3713's benign in-flight race becomes a lost capture.
  const { fetchImpl } = statusFetch(409, "a capture for this session_id is already in flight — retry shortly");
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1_000 });
  assert.equal(summary.deferred, 1);
  assert.equal(summary.discarded.length, 0);
  assert.ok(readSpoolEntry(spool, "sess-409"), "the entry is kept for the retry");
});

test("every 409 stays retryable — a policy-blocked session is never silently destroyed", () => {
  // A recording-disabled 409 is a REVERSIBLE policy state, and prose-matching
  // the server's in-flight message is fragile (the client ships independently).
  // MUTATION THAT REDS THIS: classify any 409 (or a non-matching 409) as
  // permanent → the capture is discarded and #3713's benign race loses a write.
  assert.equal(classifyFailure(409, "Session recording is disabled for this team."), "retry");
  assert.equal(classifyFailure(409, "already in flight"), "retry");
  assert.equal(classifyFailure(409), "retry");
  assert.equal(classifyFailure(undefined), "retry");
  assert.equal(classifyFailure(503), "retry");
  assert.equal(classifyFailure(429), "retry");
  // 402 is TRANSIENT (#4714, parity with the Python leg's classify_failure).
  // The quota estimate is computed from the INCOMING capture, so the identical
  // capture succeeds once a node is freed — permanent here unlinked the only
  // copy of a real 3-turn session, and both legs share one spool directory.
  assert.equal(
    classifyFailure(402, "Team points limit reached: 24956 in use + 48 estimated for this capture exceeds 25000."),
    "retry",
  );
  assert.equal(classifyFailure(402), "retry");
  // "No status" is TOTAL. JS's `null >= 300` and `null >= 500` are both false,
  // so `null` used to fall through to "permanent" -> discardEntry -> unlink,
  // while the Python leg returned "retry" for the same input. A missing status
  // is a network condition, never a server verdict.
  assert.equal(classifyFailure(null), "retry");
  assert.equal(classifyFailure(Number.NaN), "retry");
  // An ABSOLUTE pin, not only a leg-vs-leg comparison: 403 stays PERMANENT
  // (a suspended org is a reversible state, but deferring it is a separate
  // policy question — #4895). Pinned so a same-direction drift reds here.
  assert.equal(classifyFailure(403), "permanent");
  // A 3xx (a redirect on a stored api_url) must never delete the capture.
  assert.equal(classifyFailure(301), "retry");
  assert.equal(classifyFailure(422), "permanent");
});

// ── (6) Dedup / consolidation — incremental capture must not accumulate ────

test("an unchanged snapshot is not rewritten (no identical-write amplification)", () => {
  const spool = tmpSpool();
  const first = writeSpoolEntry(spool, snapshot("sess-same"));
  assert.equal(first.written, true);
  // MUTATION THAT REDS THIS: delete the `sameContent` short-circuit → the
  // second identical snapshot rewrites the entry (`written: true`).
  const second = writeSpoolEntry(spool, snapshot("sess-same"));
  assert.equal(second.written, false, "identical content must not be rewritten");
});

test("a history rewrite (window shift) never leaves stale turns fused onto the log", () => {
  const spool = tmpSpool();
  const t = (n: number) => ({ role: "user" as const, content: `t${n}` });
  writeSpoolEntry(spool, { ...snapshot("sess-shift"), turns: [t(0), t(1), t(2)] });
  // The stored timeline was REPLACED (e.g. the MAX_TURNS window shifted, or a
  // branch/compaction rewrote history) — not extended.
  writeSpoolEntry(spool, { ...snapshot("sess-shift"), turns: [t(1), t(2), t(3), t(4)] });
  // MUTATION THAT REDS THIS: trust length alone (`turns.length > stored.length`)
  // instead of comparing the stored prefix → [t0,t1,t2,t4] is returned.
  assert.deepEqual(
    readSpoolTurns(spool, "sess-shift").map((x) => x.content),
    ["t1", "t2", "t3", "t4"],
  );
});

test("a growing session appends and reconstructs every turn in order", () => {
  // MUTATION THAT REDS THIS: always rewrite the log from the LATER snapshot
  // only (drop the append/prefix check) → the earlier turns are lost.
  const spool = tmpSpool();
  writeSpoolEntry(spool, { ...snapshot("sess-grow"), turns: [{ role: "user", content: "a" }] });
  writeSpoolEntry(spool, {
    ...snapshot("sess-grow"),
    turns: [
      { role: "user", content: "a" },
      { role: "assistant", content: "b" },
      { role: "user", content: "c" },
    ],
  });
  assert.deepEqual(
    readSpoolTurns(spool, "sess-grow").map((x) => x.content),
    ["a", "b", "c"],
  );
});

// ── (7) The live session is never filed mid-conversation ───────────────────

test("agent_end drains OTHER sessions but never the live one", async () => {
  // MUTATION THAT REDS THIS: drop `excludeSessionId` from the `agent_end`
  // flush → the live session is filed mid-conversation and the server replays
  // it, freezing extraction.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-old"));
  const pi = mockPi();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl, spool));

  // The CURRENT session ("sess-live") is spooled but must not be POSTed — the
  // server replays a succeeded session_id as a no-op, so filing it early would
  // freeze the capture and silently drop every later turn.
  await pi.handlers.agent_end(
    {},
    mockCtx({ entries: PI_ENTRIES, sessionId: "sess-live", sessionFile: "/tmp/sessions/live.jsonl" }),
  );
  await settle();

  const filed = calls.filter((c) => /\/v1\/sessions$/.test(c.url));
  assert.equal(filed.length, 1, "the stale session is drained");
  assert.equal(filed[0].body.session_id, "sess-old");
  assert.ok(readSpoolEntry(spool, "sess-live"), "the live session stays spooled, unfiled");
  assert.equal(readSpoolEntry(spool, "sess-live")?.filed_key, undefined);
});

// ── (8) The final flush still files, and the spool is written first ────────

test("session_shutdown spools BEFORE the network attempt (a cancelled flush loses nothing)", async () => {
  const spool = tmpSpool();
  const pi = mockPi();
  // A capture fetch that never settles — models the ~1.5s hook cancellation /
  // a blackholed endpoint. The snapshot must already be durable.
  const fetchImpl = () => new Promise(() => {});
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl, spool));

  void pi.handlers.session_shutdown(
    { reason: "quit" },
    mockCtx({ entries: PI_ENTRIES, sessionId: "sess-final", sessionFile: "/tmp/sessions/f.jsonl" }),
  );
  // MUTATION THAT REDS THIS: spool AFTER the POST, or not at all on the
  // shutdown path → no entry exists while the POST hangs.
  const spooled = readSpoolEntry(spool, "sess-final");
  assert.ok(spooled, "the final snapshot is durable before the network attempt");
  assert.deepEqual(readSpoolTurns(spool, "sess-final"), SPOOL_TURNS);
});

// ── (9) Review-hardening regressions (#3963 review P0/P1/P2) ──────────────

test("a turn written DURING the POST is not clobbered by the filed marker (CAS)", async () => {
  // The P0: the pre-POST meta was written back after a 2xx, stamping the NEWER
  // turns with the OLD capture key — so the very next flush skipped them
  // forever. A resumed session or a cross-process drain reproduces it.
  //
  // MUTATION THAT REDS THIS: drop the compare-and-swap (write `meta` directly)
  // → `filed_key` is set to the OLD key and `turns_count` on disk reverts to 1;
  // the second flush then skips (attempted === 0) and the new turn is lost.
  const spool = tmpSpool();
  writeSpoolEntry(spool, { ...snapshot("sess-race"), turns: [{ role: "user", content: "a" }] });

  const racingFetch = async (url: string) => {
    if (/\/v1\/sessions$/.test(url)) {
      // A concurrent `turn_end` appends while the POST is in flight.
      writeSpoolEntry(spool, {
        ...snapshot("sess-race"),
        turns: [
          { role: "user", content: "a" },
          { role: "assistant", content: "b" },
        ],
      });
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  const first = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: racingFetch as never, now: 1 });
  assert.equal(first.filed, 1);
  const after = readSpoolEntry(spool, "sess-race");
  assert.equal(after?.filed_key, undefined, "the newer/on-disk content must stay UNFILED");
  assert.equal(after?.turns_count, 2, "the on-disk entry must still hold the new turn");

  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 2 });
  assert.equal(calls.length, 1, "the grown conversation is posted at the next opportunity");
  assert.deepEqual(calls[0].body.conversation, [
    { role: "user", content: "a" },
    { role: "assistant", content: "b" },
  ]);
});

test("a server-legal maximum session is spooled, not discarded as oversized", () => {
  // The P1: 500 turns × 5000 chars of non-ASCII text is ~10 MB of JSON. A 4 MiB
  // cap silently discarded legal CJK sessions whole.
  //
  // MUTATION THAT REDS THIS: set SPOOL_MAX_ENTRY_BYTES back under ~8 MB.
  const spool = tmpSpool();
  const cjk = "漢".repeat(5000); // 3 UTF-8 bytes per char
  const turns = Array.from({ length: MAX_TURNS }, () => ({
    role: "assistant" as const,
    content: cjk,
  }));
  const res = writeSpoolEntry(spool, { ...snapshot("sess-max"), turns });
  assert.equal(res.written, true, "a legal server-max session must fit the per-entry cap");
  assert.equal(res.discards.length, 0);
});

test("a spool directory that cannot be created is discarded WITH a reason", () => {
  // The spool path's parent is a regular FILE, so `mkdir` fails with ENOTDIR.
  //
  // MUTATION THAT REDS THIS: swallow the mkdir error (no discard) → the capture
  // vanishes with no recorded reason — the one silent loss path left.
  const tmp = mkdtempSync(join(tmpdir(), "tortoise-capture-notdir-"));
  const asFile = join(tmp, "not-a-dir");
  writeFileSync(asFile, "x");
  const res = writeSpoolEntry(asFile, snapshot("sess-nowrite"));
  assert.equal(res.written, false);
  assert.equal(res.discards.length, 1);
  assert.equal(res.discards[0].reason, "spool_write_failed");
});

test("a corrupt meta takes its orphaned turn log with it", () => {
  // MUTATION THAT REDS THIS: unlink only the meta → the orphaned turns.jsonl
  // stays on disk, invisible to the meta scan and never counted or pruned.
  const spool = tmpSpool();
  mkdirSync(join(spool, "entries"), { recursive: true });
  writeFileSync(join(spool, "entries", "dead.meta.json"), "{not json");
  writeFileSync(join(spool, "entries", "dead.turns.jsonl"), "{}\n");

  const { metas, discards } = listSpoolEntries(spool);
  assert.equal(metas.length, 0);
  assert.equal(discards.length, 1);
  assert.equal(discards[0].reason, "corrupt_entry");
  assert.equal(existsSync(join(spool, "entries", "dead.turns.jsonl")), false,
    "the orphaned turn log must be removed with its meta");
});

test("a meta with no usable session_id is corrupt, not a crash", () => {
  // MUTATION THAT REDS THIS: accept any parsable JSON → `sessionKey(undefined)`
  // throws / `meta.session_id` is undefined and the drain dies.
  const spool = tmpSpool();
  mkdirSync(join(spool, "entries"), { recursive: true });
  writeFileSync(join(spool, "entries", "shape.meta.json"), JSON.stringify({ hello: "world" }));
  const { metas, discards } = listSpoolEntries(spool);
  assert.equal(metas.length, 0);
  assert.equal(discards[0].reason, "corrupt_entry");
});

test("a transient failure does not clobber turns written during the POST", async () => {
  // The P0's sibling: the backoff write-back must not revert the entry to its
  // pre-POST turn count while a `turn_end` added a turn mid-flight.
  //
  // MUTATION THAT REDS THIS: write the stale `meta` back on a transient failure
  // (`const pending = meta`) → turns_count reverts and the new turn is lost.
  const spool = tmpSpool();
  writeSpoolEntry(spool, { ...snapshot("sess-race-t"), turns: [{ role: "user", content: "a" }] });

  const racingFetch = async (url: string) => {
    if (/\/v1\/sessions$/.test(url)) {
      writeSpoolEntry(spool, {
        ...snapshot("sess-race-t"),
        turns: [
          { role: "user", content: "a" },
          { role: "assistant", content: "b" },
        ],
      });
    }
    return { ok: false, status: 503, json: async () => ({}) };
  };

  const res = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: racingFetch as never, now: 1 });
  assert.equal(res.deferred, 1);
  const after = readSpoolEntry(spool, "sess-race-t");
  assert.equal(after?.turns_count, 2, "the newer turn must survive a failed POST");
  assert.equal(readSpoolTurns(spool, "sess-race-t").length, 2);
});

test("a torn append is repaired by a full rewrite, never fused into the log", async () => {
  // A process killed mid-append leaves a partial line. Appending to it FUSES
  // the next turn into the garbage tail; the tail is skipped on read-back while
  // the meta digest still covers the lost turn — a silent, unrecoverable loss.
  //
  // MUTATION THAT REDS THIS: drop `logHasCompleteRecords(logPath)` from the
  // `extendsStored` condition → the log reads back 4 turns (one fused away).
  const spool = tmpSpool();
  const first = [
    { role: "user" as const, content: "t0" },
    { role: "assistant" as const, content: "t1" },
    { role: "user" as const, content: "t2" },
  ];
  const s = snapshot("sess-torn");
  writeSpoolEntry(spool, { ...s, turns: first });

  const entries = join(spool, "entries");
  const logName = readdirSync(entries).find((n) => n.endsWith(".turns.jsonl"));
  assert.ok(logName, "the spool must have a turn log");
  // A kill mid-append: half a record, no trailing newline.
  appendFileSync(join(entries, logName as string), '{"role":"assistant","content":"t3');

  const grown = [...first, { role: "assistant" as const, content: "t3" },
    { role: "user" as const, content: "t4" }];
  writeSpoolEntry(spool, { ...s, turns: grown });

  assert.deepEqual(readSpoolTurns(spool, "sess-torn"), grown,
    "the torn tail must not survive; the full conversation must be readable");

  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1 });
  assert.deepEqual(calls[0].body.conversation, grown,
    "the filed conversation must contain every turn");
});

test("session_start never files the LIVE session (a /reload must not freeze it)", async () => {
  // On `/reload` Pi emits session_shutdown{reload} (skipped) THEN
  // session_start{reload} for the SAME session that `turn_end` already spooled.
  // Filing it here makes the server replay it (extraction skipped) and stamps
  // filed_key, so every post-reload turn is stored but never extracted.
  //
  // MUTATION THAT REDS THIS: drop the `excludeSessionId` from the
  // `flush("session_start", …)` call → the live session is POSTed.
  const spool = tmpSpool();
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  const pi = mockPi();
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl, spool));
  const ctx = () => mockCtx({ entries: PI_ENTRIES, sessionId: "sess-live" }) as never;

  pi.handlers.turn_end({}, ctx());
  pi.handlers.session_shutdown({ reason: "reload" }, ctx());
  await pi.handlers.session_start(undefined, mockCtx({ entries: [], sessionId: "sess-live" }) as never);
  await settle();

  assert.equal(calls.filter((c) => /\/v1\/sessions$/.test(c.url)).length, 0,
    "the live session must NOT be filed at session_start");
  assert.equal(readSpoolEntry(spool, "sess-live")?.filed_key, undefined,
    "the live session must stay unfiled so a later opportunity can file it whole");
  assert.ok(readSpoolEntry(spool, "sess-live"), "its turns stay durable in the spool");
});

test("readSpoolTurns returns every stored role (the store is the record)", () => {
  // The meta's turns_count/content_digest describe the STORE. If read-back
  // filters a role, a flush POSTs fewer turns than the digest it marks filed —
  // a silent drop.
  //
  // MUTATION THAT REDS THIS: restore the role filter in `readSpoolTurns`.
  const spool = tmpSpool();
  writeSpoolEntry(spool, { ...snapshot("sess-role"), turns: [{ role: "user", content: "hi" }] });
  const logName = readdirSync(join(spool, "entries")).find((n) => n.endsWith(".turns.jsonl"));
  assert.ok(logName);
  appendFileSync(join(spool, "entries", logName as string),
    '{"role":"system","content":"policy"}\n');

  const out = readSpoolTurns(spool, "sess-role");
  assert.equal(out.length, 2, "every stored turn must be readable");
  assert.deepEqual(out.map((t) => (t as { role: string }).role), ["user", "system"]);
});

test("abandoned temp files are swept (they are invisible to the byte bound)", () => {
  // A killed process leaves `*.tmp-<pid>` scratch files that match neither the
  // meta glob nor `spoolEntryBytes`, so they would never be pruned.
  //
  // MUTATION THAT REDS THIS: remove the `sweepStaleTempFiles(dir)` call.
  const spool = tmpSpool();
  mkdirSync(join(spool, "entries"), { recursive: true });
  const stale = join(spool, "entries", "x.meta.json.tmp-99999");
  writeFileSync(stale, "{");
  const old = (Date.now() - 10_000_000) / 1000;  // ~2.8 h: past the sweep age
  utimesSync(stale, old, old);

  writeSpoolEntry(spool, snapshot("sess-sweep"));
  assert.equal(existsSync(stale), false, "an abandoned temp file must be swept");
});

test("the CAS compares the POSTED turns, not the stale meta digest", async () => {
  // A writer killed between its log append and its meta write leaves the meta
  // digest STALE. Comparing the on-disk meta to the pre-POST meta stamped
  // filed_key even though the appended turn was never posted — and every later
  // flush short-circuits on the filed key, losing its filing forever.
  //
  // MUTATION THAT REDS THIS: compare `onDisk.content_digest === meta.content_digest`.
  const spool = tmpSpool();
  writeSpoolEntry(spool, { ...snapshot("sess-cas"), turns: [{ role: "user", content: "a" }] });
  const logName = readdirSync(join(spool, "entries")).find((n) => n.endsWith(".turns.jsonl"));
  assert.ok(logName);

  const racingFetch = async (url: string) => {
    if (/\/v1\/sessions$/.test(url)) {
      appendFileSync(join(spool, "entries", logName as string),
        '{"role":"assistant","content":"b"}\n');
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: racingFetch as never, now: 1 });
  assert.equal(readSpoolEntry(spool, "sess-cas")?.filed_key, undefined,
    "content that was never posted must not be marked filed");
  assert.equal(readSpoolTurns(spool, "sess-cas").length, 2);

  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 2 });
  assert.equal(calls.length, 1, "the full conversation is posted at the next opportunity");
  assert.equal((calls[0].body.conversation as unknown[]).length, 2);
});

// ── Cycle-4 review fixes (P1/P2) ───────────────────────────────────────────

test("an unreadable turn log is RECORDED, not thrown out of the flush loop", async () => {
  // `readFileSync` used to escape `readSpoolTurns`: one entry whose log was a
  // DIRECTORY (or EACCES / ELOOP) aborted the WHOLE drain loop, so every
  // session sorted after it was never filed — permanently, with no ledger line.
  //
  // MUTATION THAT REDS THIS: drop the try/catch around `readFileSync` in
  // `readSpoolTurns` (or guard with `existsSync` only).
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("aaa-broken"));
  const broken = join(spool, "entries", `${entryKey("aaa-broken")}.turns.jsonl`);
  rmSync(broken);
  mkdirSync(broken); // EISDIR on read
  writeSpoolEntry(spool, snapshot("zzz-good"));

  const { fetchImpl, calls } = mockFetch([{ ok: true }, { ok: true }]);
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1 });

  assert.deepEqual(calls.map((c) => c.body.session_id), ["zzz-good"],
    "a corrupt entry must not wedge the sessions sorted after it");
  assert.deepEqual(summary.discarded.map((d) => d.reason), ["transcript_empty"],
    "the unreadable entry must be recorded, never silently dropped");
  assert.equal(readDiscards(spool).at(-1)?.reason, "transcript_empty");
});

test("a discard records its reason BEFORE removing the entry files", () => {
  // The ledger line is the contract ("never a silent drop"). Removing first
  // leaves a SIGKILL / ENOSPC window where an unfiled capture is deleted with
  // NO record. `deps` is the seam that makes the order observable.
  //
  // MUTATION THAT REDS THIS: swap the two calls in `discardEntry`.
  const spool = tmpSpool();
  const order: string[] = [];
  const rec = discardEntry(spool, snapshot("sess-order") as never, "permanent_http_400", "nope", {
    record: (_dir, r) => {
      order.push("record");
      return { at: "now", ...r };
    },
    remove: () => {
      order.push("remove");
    },
  });
  assert.deepEqual(order, ["record", "remove"],
    "the reason must be durable before the data is removed");
  assert.equal(rec.reason, "permanent_http_400");
});

test("the LIVE session held back from a flush is COUNTED, not silent", async () => {
  // `heldBack` exists so a deliberate hold-back is not indistinguishable from
  // "already filed" in the flush report — a silent hold-back reads as a drop.
  //
  // MUTATION THAT REDS THIS: `continue` on the exclude branch without
  // incrementing `summary.heldBack`.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-live"));
  writeSpoolEntry(spool, snapshot("sess-other"));
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);

  const summary = await flushSpool(TEST_CFG, {
    dir: spool,
    fetchImpl,
    now: 1,
    excludeSessionId: "sess-live",
  });

  assert.deepEqual(calls.map((c) => c.body.session_id), ["sess-other"]);
  assert.equal(summary.heldBack, 1,
    "the held-back session must be reported, not silently skipped");
  assert.equal(readSpoolEntry(spool, "sess-live")?.filed_key, undefined,
    "and it stays unfiled for its own final flush");
});

test("an unlistable spool is RECORDED, not reported as an empty spool", async () => {
  // A mode-000 `entries/` is `existsSync`-true, so the old unguarded
  // `readdirSync` threw out of `flushSpool` and aborted the whole run with no
  // ledger line; "nothing to file" must never be the silent answer for a spool
  // that cannot be read.
  //
  // MUTATION THAT REDS THIS: drop the try/catch around `readdirSync` in
  // `listSpoolEntries`.
  if (process.getuid?.() === 0) return; // root ignores mode bits
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-hidden"));
  chmodSync(join(spool, "entries"), 0o000);
  try {
    const { fetchImpl, calls } = mockFetch([{ ok: true }]);
    const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1 });
    assert.deepEqual(calls, [], "nothing can be filed from an unreadable spool");
    assert.deepEqual(summary.discarded.map((d) => d.reason), ["spool_unreadable"]);
    assert.equal(readDiscards(spool).at(-1)?.reason, "spool_unreadable",
      "the reason must be durable in the ledger");
  } finally {
    chmodSync(join(spool, "entries"), 0o700);
  }
});

test("an unreadable spool ROOT is RECORDED, not reported as an empty spool", async () => {
  // `existsSync` returns FALSE for an EACCES directory, so the old
  // `if (!existsSync(root)) return …` pre-check reported "nothing to file" for
  // a spool it could not read — no throw, no ledger line, no clue.
  //
  // MUTATION THAT REDS THIS: restore the `if (!existsSync(root)) return …`
  // pre-check (or drop the ENOENT carve-out from the catch).
  if (process.getuid?.() === 0) return; // root ignores mode bits
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-behind-root"));
  chmodSync(spool, 0o000);
  try {
    const { fetchImpl, calls } = mockFetch([{ ok: true }]);
    const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1 });
    assert.deepEqual(calls, [], "nothing can be filed from an unreadable spool");
    assert.deepEqual(summary.discarded.map((d) => d.reason), ["spool_unreadable"],
      "an unreadable ROOT is not an empty spool");
  } finally {
    chmodSync(spool, 0o700);
  }
});
