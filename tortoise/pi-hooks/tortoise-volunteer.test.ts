// tortoise-volunteer.test.ts — behavioral tests for the in-repo Pi READ seam
// (#2119, epic #2080). Run with:
//   node --test tortoise/pi-hooks/tortoise-volunteer.test.ts
// (Node 22.18+ strips TypeScript types natively — default-on type stripping;
// on 22.7-22.17 the `--experimental-strip-types` flag is required. No build
// step, no deps.)
//
// What these pin, and why each one is a regression that actually happened
// somewhere in this seam family:
//
//   * the REGISTRATION — `before_agent_start` is the only per-turn injection
//     surface Pi documents for a user prompt; the `messageType !== "user"`
//     guard exists because Pi also fires the event for runs started by
//     `pi.sendMessage(..., {triggerTurn:true})`, and injecting there would let
//     this seam's own message start a second run (#10267/#5581 upstream).
//   * the INJECTION SHAPE — `{message: {customType, content, display}}` is the
//     documented result; nothing here may return a `systemPrompt` override
//     (that replaces the whole prompt for the run instead of appending).
//   * FAIL-OPEN on every direction (kill switch, empty turn, empty graph, a
//     dead reflex, a malformed `--json` body, a thrown handler) — a read seam
//     that can break a turn is worse than one that injects nothing.
//   * the CLI CONTRACT, run for real against a fake `tortoise` on PATH: the
//     seam must call `volunteer --json --session-id <id>` with the prompt on
//     STDIN and read `block`/`surfaced` out of the response.
//   * the CO-SOURCE rule (#2369 D1): a FILE-sourced key never takes the env
//     URL, so a poisoned environment cannot redirect a stored credential.
//   * the INERT BREADCRUMB (#4314): "no reflex resolved" leaves evidence in
//     the canonical `capture-errors/pi-install.json` slot; an empty graph
//     leaves NOTHING, because the two must stay distinguishable.

import { test } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import tortoiseVolunteer, {
  CUSTOM_TYPE,
  DEFAULT_TIMEOUT_MS,
  EXTENSION_NAME,
  HARNESS,
  MAX_INJECT_CHARS,
  MAX_PROMPT_CHARS,
  buildInjection,
  createDedupe,
  disabled,
  parseReflexJson,
  readVolunteerConfig,
  reflexCandidates,
  resolveBlock,
  runReflexCommand,
  sessionKeyOf,
} from "./tortoise-volunteer.ts";

interface Handler {
  (event: unknown, ctx: unknown): Promise<unknown> | unknown;
}

function mockPi(): { handlers: Record<string, Handler>; on: (e: string, h: Handler) => void } {
  const handlers: Record<string, Handler> = {};
  return {
    handlers,
    on(event: string, handler: Handler) {
      handlers[event] = handler;
    },
  };
}

function tmpHome(): string {
  return mkdtempSync(join(tmpdir(), "tt-volunteer-"));
}

const SESSION_CTX = (id = "sess-1") => ({
  sessionManager: { getSessionId: () => id },
});

test("registers exactly the per-turn injection surface, and nothing else", () => {
  const pi = mockPi();
  tortoiseVolunteer(pi as never, { env: {}, breadcrumbDir: tmpHome() });
  assert.deepEqual(Object.keys(pi.handlers), ["before_agent_start"]);
});

test("injects the reflex block as a SILENT custom message by default", async () => {
  const pi = mockPi();
  const seen: Array<[string, string]> = [];
  tortoiseVolunteer(pi as never, {
    env: {},
    breadcrumbDir: tmpHome(),
    reflexImpl: (prompt, sessionId) => {
      seen.push([prompt, sessionId]);
      return { block: "## Memory surfaced this turn\n- **CI** → point/p1", surfaced: ["CI"] };
    },
  });
  const result = (await pi.handlers.before_agent_start(
    { messageType: "user", prompt: "how does the deploy work?" },
    SESSION_CTX(),
  )) as { message?: { customType: string; content: string; display: boolean } };
  assert.ok(result?.message, "a block must be injected");
  assert.equal(result.message.customType, CUSTOM_TYPE);
  assert.equal(result.message.display, false, "silent default — the transcript must not grow");
  assert.match(result.message.content, /point\/p1/);
  assert.ok(
    !result.message.content.includes("Tortoise memory surfaced:"),
    "the disclosure marker belongs to the VISIBLE surface only",
  );
  assert.deepEqual(seen, [["how does the deploy work?", "sess-1"]]);
  assert.equal(HARNESS, "pi");
  assert.equal(EXTENSION_NAME, "tortoise-volunteer.ts");
});

test("a non-user run (custom message triggerTurn) is never searched", async () => {
  const pi = mockPi();
  let called = 0;
  tortoiseVolunteer(pi as never, {
    env: {},
    breadcrumbDir: tmpHome(),
    reflexImpl: () => {
      called += 1;
      return { block: "x", surfaced: [] };
    },
  });
  const result = await pi.handlers.before_agent_start(
    { messageType: "custom", message: { content: "custom trigger" } },
    SESSION_CTX(),
  );
  assert.equal(result, undefined);
  assert.equal(called, 0, "the reflex must not run for a run this seam did not originate");
});

test("fail-open: kill switch, empty prompt, empty graph, dead reflex, a throw", async () => {
  const cases: Array<[string, unknown, unknown]> = [
    ["kill switch", { messageType: "user", prompt: "anything" }, { block: "b", surfaced: [] }],
    ["empty prompt", { messageType: "user", prompt: "   " }, { block: "b", surfaced: [] }],
    ["empty graph", { messageType: "user", prompt: "q" }, { block: "", surfaced: [] }],
    ["dead reflex", { messageType: "user", prompt: "q" }, null],
  ];
  for (const [name, event, implOut] of cases) {
    const pi = mockPi();
    tortoiseVolunteer(pi as never, {
      env: name === "kill switch" ? { TORTOISE_VOLUNTEER_DISABLE: "1" } : {},
      breadcrumbDir: tmpHome(),
      reflexImpl: () => implOut as never,
    });
    assert.equal(
      await pi.handlers.before_agent_start(event, SESSION_CTX()),
      undefined,
      `${name} must inject nothing`,
    );
  }

  const pi = mockPi();
  tortoiseVolunteer(pi as never, {
    env: {},
    breadcrumbDir: tmpHome(),
    reflexImpl: () => {
      throw new Error("reflex exploded");
    },
  });
  assert.equal(
    await pi.handlers.before_agent_start({ messageType: "user", prompt: "q" }, SESSION_CTX()),
    undefined,
    "a throwing reflex must not throw into the turn",
  );
});

test("the prompt is bounded before it reaches the reflex", async () => {
  const pi = mockPi();
  let seenPrompt = "";
  tortoiseVolunteer(pi as never, {
    env: {},
    breadcrumbDir: tmpHome(),
    reflexImpl: (prompt) => {
      seenPrompt = prompt;
      return { block: "b", surfaced: [] };
    },
  });
  await pi.handlers.before_agent_start(
    { messageType: "user", prompt: "x".repeat(MAX_PROMPT_CHARS * 2) },
    SESSION_CTX(),
  );
  assert.equal(seenPrompt.length, MAX_PROMPT_CHARS);
});

test("cross-turn dedupe: the identical block is not re-injected in a session", async () => {
  const pi = mockPi();
  tortoiseVolunteer(pi as never, {
    env: {},
    breadcrumbDir: tmpHome(),
    reflexImpl: () => ({ block: "same block", surfaced: [] }),
  });
  const event = { messageType: "user", prompt: "q" };
  assert.ok(await pi.handlers.before_agent_start(event, SESSION_CTX("a")));
  assert.equal(await pi.handlers.before_agent_start(event, SESSION_CTX("a")), undefined);
  assert.ok(
    await pi.handlers.before_agent_start(event, SESSION_CTX("b")),
    "a different session starts with a fresh slate",
  );

  const dedupe = createDedupe();
  assert.equal(dedupe("s", "block"), true);
  assert.equal(dedupe("s", "block"), false);
  assert.equal(dedupe("s", "other"), true);
});

test("visible surface: display renders the reflex's `surfaced` marker", () => {
  const silent = buildInjection("## block", ["CI"], false);
  assert.deepEqual(silent, { customType: CUSTOM_TYPE, content: "## block", display: false });

  const visible = buildInjection("## block", ["CI", "pricing"], true);
  assert.equal(visible?.display, true);
  assert.match(visible!.content, /^Tortoise memory surfaced: CI, pricing\n\n## block$/);

  assert.equal(buildInjection("", [], true), undefined, "no block, no message");
  const long = buildInjection("y".repeat(MAX_INJECT_CHARS * 2), [], false);
  assert.equal(long?.content.length, MAX_INJECT_CHARS);
});

test("reflexCandidates: PATH, the src-dir record, and $VIRTUAL_ENV — never a CWD guess", () => {
  const home = tmpHome();
  const bin = join(home, "bin");
  mkdirSync(bin, { recursive: true });
  writeFileSync(join(bin, "tortoise"), "#!/bin/sh\n");
  chmodSync(join(bin, "tortoise"), 0o755);

  const src = join(home, "checkout");
  mkdirSync(join(src, ".venv", "bin"), { recursive: true });
  writeFileSync(join(src, ".venv", "bin", "tortoise"), "#!/bin/sh\n");
  mkdirSync(join(home, ".tortoise"), { recursive: true });
  writeFileSync(join(home, ".tortoise", "hook-src-dir"), `${src}\n`);

  const venv = join(home, "venv");
  mkdirSync(join(venv, "bin"), { recursive: true });
  writeFileSync(join(venv, "bin", "tortoise"), "#!/bin/sh\n");

  const found = reflexCandidates(
    { PATH: `${bin}:/usr/bin`, VIRTUAL_ENV: venv },
    home,
  );
  assert.deepEqual(found, [
    join(bin, "tortoise"),
    join(src, ".venv", "bin", "tortoise"),
    join(venv, "bin", "tortoise"),
  ]);

  // No HOME record, no VIRTUAL_ENV, nothing on PATH → no candidate at all.
  // The point: `./.tortoise` in the agent's own (writable) workspace is never
  // consulted, so a repo cannot point the seam at an interpreter of its choosing.
  const bare = tmpHome();
  assert.deepEqual(reflexCandidates({ PATH: "" }, bare), []);
});

test("readVolunteerConfig: file-sourced key never takes the env URL (co-source, #2369 D1)", () => {
  const home = tmpHome();
  const cfgPath = join(home, "tortoise-config.json");
  writeFileSync(
    cfgPath,
    JSON.stringify({
      apiKey: "tt_file_key",
      apiUrl: "https://file.example",
      volunteer: { display: true, timeoutMs: 1234 },
    }),
  );
  const fromFile = readVolunteerConfig({ TORTOISE_API_URL: "https://attacker.example" }, cfgPath);
  assert.equal(fromFile.apiKey, "tt_file_key");
  assert.equal(fromFile.apiUrl, "https://file.example", "the FILE's URL, never the env's");
  assert.equal(fromFile.display, true);
  assert.equal(fromFile.timeoutMs, 1234);

  const fromEnv = readVolunteerConfig(
    { TORTOISE_API_KEY: "tt_env_key", TORTOISE_API_URL: "https://env.example" },
    cfgPath,
  );
  assert.equal(fromEnv.apiUrl, "https://env.example", "an env key may use the env URL");

  const defaults = readVolunteerConfig({}, join(home, "absent.json"));
  assert.equal(defaults.apiKey, "");
  assert.equal(defaults.display, false);
  assert.equal(defaults.timeoutMs, DEFAULT_TIMEOUT_MS);

  // A malformed config is "no config", never a throw.
  writeFileSync(cfgPath, "{not json");
  assert.equal(readVolunteerConfig({}, cfgPath).apiKey, "");

  assert.equal(disabled({ TORTOISE_VOLUNTEER_DISABLE: "yes" }), true);
  assert.equal(disabled({}), false);
});

function fakeCli(dir: string, body: string): string {
  mkdirSync(dir, { recursive: true });
  const path = join(dir, "tortoise");
  writeFileSync(path, `#!/bin/sh\n${body}\n`);
  chmodSync(path, 0o755);
  return path;
}

test("the CLI contract, run for real: `volunteer --json --session-id`, prompt on stdin", async () => {
  const home = tmpHome();
  const argsFile = join(home, "args.txt");
  const promptFile = join(home, "prompt.txt");
  const cli = fakeCli(
    join(home, "bin"),
    [
      `printf '%s' "$*" > ${JSON.stringify(argsFile)}`,
      `cat > ${JSON.stringify(promptFile)}`,
      `printf '%s' '{"pointers":[{"id":"p1","label":"CI"}],"why":[],"surfaced":[{"label":"CI","band":"measured"}],"block":"## Memory surfaced this turn\\n- **CI** → point/p1","degraded_reason":null}'`,
    ].join("\n"),
  );
  const cfg = readVolunteerConfig({ TORTOISE_COMMAND: cli }, join(home, "absent.json"));
  const verdict = await resolveBlock("how does CI deploy?", "sess-9", cfg, []);
  assert.equal(verdict.status, "block");
  if (verdict.status === "block") {
    assert.match(verdict.result.block, /point\/p1/);
    assert.deepEqual(verdict.result.surfaced, ["CI"]);
  }
  assert.equal(readFileSync(argsFile, "utf-8"), "volunteer --json --session-id sess-9");
  assert.equal(readFileSync(promptFile, "utf-8"), "how does CI deploy?");
});

test("a non-zero exit, a `{status:error}` body, and an unparseable body are all fail-open", async () => {
  const home = tmpHome();
  const exitFail = fakeCli(join(home, "a"), "exit 2");
  const failed = await resolveBlock(
    "q",
    "",
    readVolunteerConfig({ TORTOISE_COMMAND: exitFail }, join(home, "no.json")),
    [],
  );
  assert.equal(failed.status, "inert");
  assert.match(
    failed.status === "inert" ? failed.detail : "",
    /every resolved tortoise reflex failed \(.*a\/tortoise: /,
  );

  const errorBody = fakeCli(join(home, "b"), `printf '%s' '{"status":"error","error":"graph down"}'`);
  assert.equal(
    (await resolveBlock("q", "", readVolunteerConfig({ TORTOISE_COMMAND: errorBody }, join(home, "no.json")), []))
      .status,
    "inert",
  );

  const garbage = fakeCli(join(home, "c"), "printf '%s' 'not json'");
  assert.equal(
    (await resolveBlock("q", "", readVolunteerConfig({ TORTOISE_COMMAND: garbage }, join(home, "no.json")), []))
      .status,
    "inert",
  );

  // Clean silence: the reflex ran, nothing cleared the gate. NOT inert.
  const clean = fakeCli(join(home, "d"), `printf '%s' '{"pointers":[],"surfaced":[],"block":""}'`);
  assert.deepEqual(
    await resolveBlock("q", "", readVolunteerConfig({ TORTOISE_COMMAND: clean }, join(home, "no.json")), []),
    { status: "empty" },
  );

  assert.equal(parseReflexJson("{}")?.block, "");
  assert.equal(parseReflexJson("[]"), null);
  assert.equal(parseReflexJson("nope"), null);
});

test("a command that never reads stdin still resolves (EPIPE is not a throw)", async () => {
  const home = tmpHome();
  const cli = fakeCli(join(home, "bin"), `printf '%s' '{"block":""}'`);
  const run = await runReflexCommand(cli, "x".repeat(200000), "", 5000);
  assert.equal(run.ok, true);
});

test("the reflex budget covers a HEALTHY reflex — the 3 s default made this seam inert", () => {
  // #2119 measured: `tortoise volunteer` constructs `EmbeddingModel.get()`,
  // which loads 199 weight tensors (~8.2 s, per PROCESS — every turn pays it),
  // so a whole healthy call is ~9.0 s. The seam first shipped a 3000 ms budget,
  // i.e. below the cost of a WORKING reflex: it injected nothing on every
  // turn, wrote a false `install-inert` breadcrumb, and the two end-to-end
  // grades in `tests/test_pi_volunteer_seam.py` failed on exactly that.
  //
  // The floor is the guard, not the exact number: tightening the budget back
  // under a measured healthy call re-creates inert-by-construction, and
  // nothing else in this file would notice.
  assert.ok(
    DEFAULT_TIMEOUT_MS >= 10000,
    `DEFAULT_TIMEOUT_MS is ${DEFAULT_TIMEOUT_MS}ms — below the ~9.0 s a HEALTHY ` +
      `reflex measures (8.2 s of it is the embedder load), so the seam would ` +
      `be inert on every turn`,
  );
});

test("a TIMEOUT is not an inert install: no breadcrumb, and the chain stops", async () => {
  const home = tmpHome();
  const crumbs = join(home, "capture-errors");
  // Candidate 1 never finishes inside the budget; candidate 2 would succeed
  // and touch `ran`, which must NOT exist — a slow reflex is slow in the
  // reflex, so re-running the same budget on the next candidate would only
  // multiply the turn's wait.
  const slow = fakeCli(join(home, "bin"), `sleep 5`);
  const ran = join(home, "venv-ran");
  const venvCli = join(home, "venv", "bin", "tortoise");
  mkdirSync(join(home, "venv", "bin"), { recursive: true });
  writeFileSync(
    venvCli,
    `#!/bin/sh\ntouch ${JSON.stringify(ran)}\nprintf '%s' '{"block":"## Memory"}'\n`,
  );
  chmodSync(venvCli, 0o755);
  assert.ok(slow.endsWith("tortoise"));

  const cfgPath = join(home, "tortoise-config.json");
  writeFileSync(cfgPath, JSON.stringify({ volunteer: { timeoutMs: 300 } }));
  const pi = mockPi();
  tortoiseVolunteer(pi as never, {
    env: { PATH: join(home, "bin"), VIRTUAL_ENV: join(home, "venv") },
    home,
    configPath: cfgPath,
    breadcrumbDir: crumbs,
  });
  assert.equal(
    await pi.handlers.before_agent_start({ messageType: "user", prompt: "q" }, SESSION_CTX()),
    undefined,
    "a timed-out reflex injects nothing",
  );
  assert.equal(existsSync(ran), false, "the chain must abort on the first timeout");
  assert.equal(
    existsSync(join(crumbs, "pi-install.json")),
    false,
    "a SLOW-but-working reflex must not read as an inert INSTALL (#4314) — the " +
      "operator would be told to reinstall something that works",
  );

  // …and the verdict says so, so the distinction is not only observable at the
  // breadcrumb: `inert` still means "nothing could run at all".
  const verdict = await resolveBlock(
    "q",
    "",
    readVolunteerConfig({ TORTOISE_COMMAND: slow }, cfgPath),
    [],
  );
  assert.equal(verdict.status, "timeout");
  assert.match(verdict.status === "timeout" ? verdict.detail : "", /timed out after 300ms/);
});

test("runReflexCommand reports a timeout as a TIMEOUT, a non-zero exit as a failure", async () => {
  const home = tmpHome();
  const slow = await runReflexCommand(fakeCli(join(home, "a"), "sleep 5"), "q", "", 250);
  assert.equal(slow.ok, false);
  assert.equal(slow.timedOut, true, "execFile's SIGTERM-kill is the timeout signal");
  assert.match(slow.error, /timed out after 250ms/, "not the opaque `Command failed: …`");

  const broken = await runReflexCommand(fakeCli(join(home, "b"), "exit 3"), "q", "", 5000);
  assert.equal(broken.ok, false);
  assert.equal(broken.timedOut, false, "a real failure is NOT a timeout");

  const fine = await runReflexCommand(
    fakeCli(join(home, "c"), `printf '%s' '{"block":""}'`),
    "q",
    "",
    5000,
  );
  assert.equal(fine.ok, true);
  assert.equal(fine.timedOut, false);
});

test("no reflex and no key: an inert breadcrumb lands in the canonical slot; an empty graph leaves none", async () => {
  const home = tmpHome();
  const crumbs = join(home, "capture-errors");
  const pi = mockPi();
  tortoiseVolunteer(pi as never, { env: { PATH: "" }, home, breadcrumbDir: crumbs });
  assert.equal(
    await pi.handlers.before_agent_start({ messageType: "user", prompt: "q" }, SESSION_CTX()),
    undefined,
  );
  const crumb = join(crumbs, "pi-install.json");
  assert.ok(existsSync(crumb), "an inert install must leave evidence (#4314)");
  const record = JSON.parse(readFileSync(crumb, "utf-8")) as Record<string, unknown>;
  assert.equal(record.kind, "install-inert");
  assert.equal(record.harness, "pi");
  assert.match(String(record.detail), /injected nothing/);

  // A healthy, empty graph writes NO breadcrumb — silence is not a defect.
  const home2 = tmpHome();
  const emptyCli = fakeCli(join(home2, "bin"), `printf '%s' '{"pointers":[],"surfaced":[],"block":""}'`);
  const pi2 = mockPi();
  tortoiseVolunteer(pi2 as never, {
    env: { PATH: `${join(home2, "bin")}:/usr/bin` },
    home: home2,
    configPath: join(home2, "absent.json"),
    breadcrumbDir: join(home2, "capture-errors"),
  });
  assert.equal(
    await pi2.handlers.before_agent_start({ messageType: "user", prompt: "q" }, SESSION_CTX()),
    undefined,
  );
  assert.equal(
    existsSync(join(home2, "capture-errors", "pi-install.json")),
    false,
    "an empty graph must stay silent",
  );
  assert.equal(emptyCli.length > 0, true);
});

test("sessionKeyOf tolerates every ctx shape Pi may hand a handler", () => {
  assert.equal(sessionKeyOf(SESSION_CTX("abc")), "abc");
  assert.equal(sessionKeyOf({}), "");
  assert.equal(sessionKeyOf(undefined), "");
  assert.equal(sessionKeyOf({ sessionManager: {} }), "");
  assert.equal(
    sessionKeyOf({
      sessionManager: {
        getSessionId: () => {
          throw new Error("gone");
        },
      },
    }),
    "",
  );
});
