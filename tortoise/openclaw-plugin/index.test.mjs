// Tortoise OpenClaw seam — the artifact's own suite (#2120).
//
// Run with `node --test tortoise/openclaw-plugin/index.test.mjs`; the Python
// gate `tests/test_openclaw_seam.py` runs it too, so a behavioural edit to the
// seam cannot land without the spool contract being re-pinned.
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, writeFileSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import register, {
  captureRecords,
  createDedupe,
  readConfig,
  runCommand,
  spoolDir,
  spoolFile,
  stateDir,
} from "./index.mjs";

function fakeApi(config = {}, home = "/nonexistent-home") {
  const hooks = new Map();
  return {
    home,
    config,
    on(name, handler) {
      hooks.set(name, handler);
    },
    hooks,
  };
}

test("readConfig: defaults, env fallbacks, and malformed values", () => {
  const bare = readConfig(undefined, {});
  assert.equal(bare.apiUrl, "https://api.premiselabs.co");
  assert.equal(bare.apiKey, "");
  assert.equal(bare.command, "");
  assert.equal(bare.timeoutMs, 800);
  assert.equal(bare.capture, true);

  const fromEnv = readConfig(undefined, {
    TORTOISE_API_URL: "http://localhost:9",
    TORTOISE_API_KEY: "k",
    TORTOISE_COMMAND: "/usr/local/bin/tortoise",
  });
  assert.equal(fromEnv.apiUrl, "http://localhost:9");
  assert.equal(fromEnv.apiKey, "k");
  assert.equal(fromEnv.command, "/usr/local/bin/tortoise");

  // Plugin config WINS over env; malformed values fall back, never throw.
  const explicit = readConfig(
    { apiUrl: " https://x.test ", apiKey: "k2", timeoutMs: -3, capture: false },
    { TORTOISE_API_URL: "http://env.test" },
  );
  assert.equal(explicit.apiUrl, "https://x.test");
  assert.equal(explicit.timeoutMs, 800);
  assert.equal(explicit.capture, false);
});

test("stateDir honours OPENCLAW_STATE_DIR (the documented override)", () => {
  assert.equal(stateDir({ OPENCLAW_STATE_DIR: "/tmp/oc" }, "/home/u"), "/tmp/oc");
  assert.equal(stateDir({}, "/home/u"), "/home/u/.openclaw");
  assert.equal(stateDir({ OPENCLAW_STATE_DIR: "  " }, "/home/u"), "/home/u/.openclaw");
  assert.equal(spoolDir({ OPENCLAW_STATE_DIR: "/tmp/oc" }, "/home/u"), "/tmp/oc/tortoise/turns");
  assert.equal(spoolDir({}, "/home/u"), "/home/u/.openclaw/tortoise/turns");
});

test("spoolFile: ONE file per session, filesystem-safe and collision-proof", () => {
  const a = spoolFile("agent:main:main", null, {}, "/home/u");
  const b = spoolFile("agent/other main", null, {}, "/home/u");
  assert.match(a, /^\/home\/u\/\.openclaw\/tortoise\/turns\/agent_main_main-[0-9a-f]{12}\.jsonl$/);
  // Two keys that slug IDENTICALLY still get different files — a shared file
  // would be filed by `sessions import` as one merged session.
  assert.notEqual(a, b);
  // No path traversal from a hostile session key.
  const nasty = spoolFile("../../etc/passwd", null, {}, "/home/u");
  assert.ok(nasty.startsWith("/home/u/.openclaw/tortoise/turns/"));
  assert.equal(nasty.includes(".."), false);
});

test("captureRecords: the spool contract the Python parser reads", () => {
  const records = captureRecords(
    { sessionId: "s-1", prompt: "  what do we know?  ", assistantTexts: ["a", "", "b"] },
    { sessionKey: "agent:main:main", agentId: "main" },
    Date.UTC(2026, 9, 11, 12, 0, 0),
  );
  assert.equal(records.length, 3);
  assert.deepEqual(records[0], {
    session_key: "agent:main:main",
    session_id: "s-1",
    agent_id: "main",
    ts: "2026-10-11T12:00:00.000Z",
    role: "user",
    text: "what do we know?",
  });
  assert.equal(records[1].role, "assistant");
  assert.equal(records[1].text, "a");
  assert.equal(records[2].text, "b");
  // Nothing to file ⇒ nothing written (never an empty-text turn).
  assert.deepEqual(captureRecords({}, {}, Date.now()), []);
});

test("createDedupe: a repeated block is injected once per session key", () => {
  const dedupe = createDedupe();
  assert.equal(dedupe("s1", "same"), true);
  assert.equal(dedupe("s1", "same"), false);
  assert.equal(dedupe("s1", "other"), true);
  assert.equal(dedupe("s2", "same"), true);
});

test("runCommand: reads the block off stdout; a failing command yields ''", async () => {
  const dir = mkdtempSync(join(tmpdir(), "tortoise-oc-"));
  const good = join(dir, "good.sh");
  writeFileSync(good, "#!/bin/sh\ncat >/dev/null\nprintf 'block:%s' \"$1\"\n");
  chmodSync(good, 0o755);
  assert.equal(await runCommand(good, "hello", 2000), "block:volunteer");

  const bad = join(dir, "bad.sh");
  writeFileSync(bad, "#!/bin/sh\nexit 3\n");
  chmodSync(bad, 0o755);
  assert.equal(await runCommand(bad, "hello", 2000), "");

  assert.equal(await runCommand(join(dir, "missing.sh"), "hello", 2000), "");
});

test("register: injects the reflex block as prependContext", async () => {
  const dir = mkdtempSync(join(tmpdir(), "tortoise-oc-"));
  const bin = join(dir, "fake-tortoise.sh");
  writeFileSync(bin, "#!/bin/sh\ncat >/dev/null\nprintf 'volunteered why-block'\n");
  chmodSync(bin, 0o755);

  const api = fakeApi({ command: bin });
  register(api);
  const hook = api.hooks.get("before_prompt_build");
  assert.equal(typeof hook, "function");

  const result = await hook(
    { prompt: "tell me about the deal" },
    { trigger: "user", sessionKey: "s1" },
  );
  assert.deepEqual(result, { prependContext: "volunteered why-block" });

  // Cross-turn dedupe: the identical block is not injected twice.
  assert.equal(
    await hook({ prompt: "tell me about the deal" }, { trigger: "user", sessionKey: "s1" }),
    undefined,
  );
});

test("register: non-user turns and empty prompts inject nothing", async () => {
  const api = fakeApi({ command: "/nonexistent/tortoise" });
  register(api);
  const hook = api.hooks.get("before_prompt_build");
  assert.equal(await hook({ prompt: "hi" }, { trigger: "heartbeat", sessionKey: "s" }), undefined);
  assert.equal(await hook({ prompt: "   " }, { trigger: "user", sessionKey: "s" }), undefined);
});

test("register: a broken reflex is fail-open — the turn still proceeds", async () => {
  const api = fakeApi({ command: "/nonexistent/tortoise", timeoutMs: 50 });
  register(api);
  const hook = api.hooks.get("before_prompt_build");
  assert.equal(await hook({ prompt: "hi" }, { trigger: "user", sessionKey: "s" }), undefined);
});

test("register: llm_output appends each session to its OWN spool file", async () => {
  const dir = mkdtempSync(join(tmpdir(), "tortoise-oc-"));
  const previous = process.env.TORTOISE_OPENCLAW_SPOOL_DIR;
  process.env.TORTOISE_OPENCLAW_SPOOL_DIR = dir;
  try {
    const api = fakeApi({ capture: true });
    register(api);
    const hook = api.hooks.get("llm_output");
    await hook(
      { sessionId: "s-9", prompt: "q", assistantTexts: ["a"] },
      { sessionKey: "agent:main:main", agentId: "main" },
    );
    await hook(
      { sessionId: "s-10", prompt: "q2", assistantTexts: ["a2"] },
      { sessionKey: "agent:work:main", agentId: "work" },
    );
    const files = readdirSync(dir).sort();
    assert.equal(files.length, 2, "one file per session");
    const first = readFileSync(join(dir, files.find((f) => f.includes("agent_main_main"))), "utf8")
      .trim().split("\n").map((l) => JSON.parse(l));
    assert.equal(first.length, 2);
    assert.equal(first[0].role, "user");
    assert.equal(first[0].text, "q");
    assert.equal(first[1].role, "assistant");
    assert.equal(first[0].session_key, "agent:main:main");
    assert.equal(first[0].agent_id, "main");

    const off = fakeApi({ capture: false });
    register(off);
    assert.equal(off.hooks.has("llm_output"), false);
  } finally {
    if (previous === undefined) delete process.env.TORTOISE_OPENCLAW_SPOOL_DIR;
    else process.env.TORTOISE_OPENCLAW_SPOOL_DIR = previous;
  }
});
