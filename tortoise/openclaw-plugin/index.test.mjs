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
  createContinuity,
  createDedupe,
  readConfig,
  reflexPayload,
  runCommand,
  runHosted,
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

test("createContinuity: the last block per session, '' when none", () => {
  const continuity = createContinuity();
  assert.equal(continuity.prior("s1"), "");
  continuity.remember("s1", "b1");
  continuity.remember("s1", "b2");
  assert.equal(continuity.prior("s1"), "b2");
  assert.equal(continuity.prior("s2"), "");
});

test("runCommand: pipes the reflex contract; a failing command yields ''", async () => {
  const dir = mkdtempSync(join(tmpdir(), "tortoise-oc-"));
  const capture = join(dir, "stdin.json");
  const good = join(dir, "good.sh");
  writeFileSync(good, `#!/bin/sh\ncat > ${capture}\nprintf 'block'\n`);
  chmodSync(good, 0o755);
  assert.equal(await runCommand(good, "hello", 2000), "block");
  assert.deepEqual(JSON.parse(readFileSync(capture, "utf8")), {
    window: [{ role: "user", content: "hello" }],
    session_id: null,
    prior_context: null,
  });

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

test("reflexPayload: the machine contract, with continuity in prior_context", () => {
  assert.deepEqual(JSON.parse(reflexPayload("hi")), {
    window: [{ role: "user", content: "hi" }],
    session_id: null,
    prior_context: null,
  });
  // A payload KEY (not an argv flag) is what makes an older `tortoise`
  // ignore continuity and still run — an unknown flag would exit non-zero and
  // the fail-open catch would inject nothing, silently, on every turn.
  assert.deepEqual(
    JSON.parse(reflexPayload("hi", { sessionId: "s1", priorContext: "blk" })),
    {
      window: [{ role: "user", content: "hi" }],
      session_id: "s1",
      prior_context: "blk",
    },
  );
});

test("register: hands the reflex the block it injected last turn (continuity)", async () => {
  const dir = mkdtempSync(join(tmpdir(), "tortoise-oc-"));
  const log = join(dir, "payloads.jsonl");
  const bin = join(dir, "fake-tortoise.sh");
  // Records the payload it was handed and answers with a block derived from
  // the prompt, so two turns produce two DIFFERENT blocks (the digest dedupe
  // cannot be what suppresses anything here).
  writeFileSync(
    bin,
    `#!${process.execPath}\n` +
      `const fs = require("node:fs");\n` +
      `let raw = "";\n` +
      `process.stdin.on("data", (c) => { raw += c; });\n` +
      `process.stdin.on("end", () => {\n` +
      `  fs.appendFileSync(${JSON.stringify(log)}, raw + "\\n");\n` +
      `  const body = JSON.parse(raw);\n` +
      `  process.stdout.write("block-for:" + body.window[0].content);\n` +
      `});\n`,
  );
  chmodSync(bin, 0o755);

  const api = fakeApi({ command: bin });
  register(api);
  const hook = api.hooks.get("before_prompt_build");

  const first = await hook({ prompt: "alpha" }, { trigger: "user", sessionKey: "s1" });
  assert.deepEqual(first, { prependContext: "block-for:alpha" });
  const second = await hook({ prompt: "beta" }, { trigger: "user", sessionKey: "s1" });
  assert.deepEqual(second, { prependContext: "block-for:beta" });

  // A different session starts clean — continuity is per session key.
  const other = await hook({ prompt: "gamma" }, { trigger: "user", sessionKey: "s2" });
  assert.deepEqual(other, { prependContext: "block-for:gamma" });

  const calls = readFileSync(log, "utf8").trim().split("\n").map((l) => JSON.parse(l));
  assert.equal(calls.length, 3);
  // Turn 1 has nothing to suppress; turn 2 carries TURN 1's block, which is
  // the reflex's only re-mention-suppression input.
  assert.deepEqual(calls[0], {
    window: [{ role: "user", content: "alpha" }],
    session_id: "s1",
    prior_context: null,
  });
  assert.deepEqual(calls[1], {
    window: [{ role: "user", content: "beta" }],
    session_id: "s1",
    prior_context: "block-for:alpha",
  });
  assert.deepEqual(calls[2], {
    window: [{ role: "user", content: "gamma" }],
    session_id: "s2",
    prior_context: null,
  });
});

test("runHosted: posts the documented /v1/context contract, not a bare prompt", async () => {
  const realFetch = globalThis.fetch;
  let seen = null;
  globalThis.fetch = async (url, options) => {
    seen = { url, options };
    return { ok: true, json: async () => ({ block: "hosted-block", pointers: [] }) };
  };
  try {
    const block = await runHosted(
      "https://api.test.example/",
      "k",
      "what do we know?",
      500,
      { sessionId: "s1", priorContext: "prev-block" },
    );
    assert.equal(block, "hosted-block");
    assert.equal(seen.url, "https://api.test.example/v1/context");
    assert.equal(seen.options.method, "POST");
    assert.equal(seen.options.headers.Authorization, "Bearer k");
    // `window` is REQUIRED by VolunteerContextRequest (tortoise/hosted_api.py):
    // the old `{prompt}` body is a 422, which the fail-open catch turns into a
    // silent no-injection with no signal at all.
    assert.deepEqual(JSON.parse(seen.options.body), {
      window: [{ role: "user", content: "what do we know?" }],
      session_id: "s1",
      prior_context: "prev-block",
    });
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("runHosted: no key, a refused request and a non-JSON body are all ''", async () => {
  const realFetch = globalThis.fetch;
  try {
    assert.equal(await runHosted("https://api.test", "", "p", 100), "");
    for (const response of [
      { ok: false, json: async () => ({ detail: "Invalid API key" }) },
      { ok: true, json: async () => { throw new Error("not json"); } },
    ]) {
      globalThis.fetch = async () => response;
      assert.equal(await runHosted("https://api.test", "k", "p", 100), "");
    }
    globalThis.fetch = async () => { throw new Error("unreachable"); };
    assert.equal(await runHosted("https://api.test", "k", "p", 100), "");
  } finally {
    globalThis.fetch = realFetch;
  }
});
