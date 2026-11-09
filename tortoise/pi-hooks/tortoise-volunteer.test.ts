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
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import tortoiseVolunteer, {
  CUSTOM_TYPE,
  DEFAULT_TIMEOUT_MS,
  EXTENSION_NAME,
  HARNESS,
  MAX_INJECT_CHARS,
  MAX_PROMPT_BYTES,
  MAX_SEEN_PER_SESSION,
  buildInjection,
  clipToBytes,
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

test("an inert verdict is LATCHED — one probe per process, not per turn", async () => {
  // Review finding 5: without a latch, every user turn re-walked the candidate
  // chain, re-spawned Python, and REWROTE the breadcrumb — so `recorded_at`
  // could only ever say "now" and could never date a dead install.
  const home = tmpHome();
  const crumbs = join(home, "capture-errors");
  const pi = mockPi();
  // Hermetic (#11584 review): WITHOUT `configPath` the factory falls back to
  // `CONFIG_PATH` = the OPERATOR's real `~/.pi/agent/tortoise-config.json`. On a
  // machine that carries a file-sourced `apiKey` this row would then take the
  // hosted transport and POST the test prompt to the production endpoint — and
  // the row would red, because a `refused` verdict writes no breadcrumb. The
  // counter makes the omission FAIL CLOSED instead of egressing a credential.
  let hostedCalls = 0;
  tortoiseVolunteer(pi as never, {
    env: { PATH: "" },
    home,
    configPath: join(home, "absent.json"),
    breadcrumbDir: crumbs,
    fetchImpl: (async () => {
      hostedCalls += 1;
      return { ok: false, status: 500, statusText: "Unexpected" } as Response;
    }) as typeof fetch,
  });
  await pi.handlers.before_agent_start({ messageType: "user", prompt: "one" }, SESSION_CTX());
  assert.equal(
    hostedCalls,
    0,
    "no configPath ⇒ no key ⇒ the hosted transport must never be reached; a row " +
      "that inherits the operator's config is not hermetic and leaks the prompt",
  );
  const breadcrumb = join(crumbs, "pi-install.json");
  assert.equal(existsSync(breadcrumb), true);
  const first = readFileSync(breadcrumb, "utf8");
  assert.match(first, /latched for the rest of this Pi process/);
  const stamped = JSON.parse(first).recorded_at;

  // …a second (and third) turn injects nothing and leaves the record — and its
  // timestamp — exactly as the first turn wrote them.
  for (const prompt of ["two", "three"]) {
    assert.equal(
      await pi.handlers.before_agent_start({ messageType: "user", prompt }, SESSION_CTX()),
      undefined,
    );
    assert.equal(readFileSync(breadcrumb, "utf8"), first, "the record must not be rewritten per turn");
  }
  assert.equal(JSON.parse(readFileSync(breadcrumb, "utf8")).recorded_at, stamped);
});

test("an EXHAUSTED chain is a timeout, never an inert install", async () => {
  // `resolveBlock`'s `remaining <= 0` branch: the chain is out of budget before
  // a candidate can start. It must report `timeout` — a no-injection that
  // neither latches the seam nor writes the `install-inert` breadcrumb — and
  // never `inert`, which would tell the operator to reinstall a working install
  // (#11584 finding 1's exact failure class).
  //
  // Reached with NO new seam: `timeoutMs: 0` makes `deadline` already past, and
  // the test builds the config object itself, so `positiveInt`'s `n > 0` clamp
  // (tortoise-volunteer.ts:209) is not in the path. Discriminating: mutating
  // this branch's `timeout` to `inert` reds this row.
  const home = tmpHome();
  const cli = fakeCli(join(home, "bin"), "sleep 1; exit 1");
  assert.ok(cli.endsWith("tortoise"));

  const verdict = await resolveBlock(
    "q",
    "",
    { ...readVolunteerConfig({}, join(home, "absent.json")), timeoutMs: 0 },
    [cli],
  );

  assert.equal(
    verdict.status,
    "timeout",
    "an exhausted chain is a clean no-injection, not an `inert` install",
  );
  assert.match(
    verdict.status === "timeout" ? verdict.detail : "",
    /spent its 0ms budget/,
    "the detail must name the exhausted budget rather than a candidate failure",
  );
});

test("the reflex chain shares ONE deadline across candidates", async () => {
  // Review finding 5: Pi counts as busy while `before_agent_start` runs, so a
  // budget PER candidate made the turn's wait N × timeoutMs.
  //
  // ⛔ SCOPE, stated rather than implied (#11584 review, round 7). This row
  // proves the SHARED-DEADLINE arithmetic: two failing candidates cannot spend
  // the budget twice. It does NOT reach `resolveBlock`'s `remaining <= 0`
  // branch (the exit-at-budget race) — that branch is owned by
  // `an EXHAUSTED chain is a timeout, never an inert install` below, which
  // reaches it directly. An earlier version of this comment called that branch
  // unreachable without a new clock/runner seam; that was false. A config
  // literal with `timeoutMs: 0` reaches it with no seam at all, because a test
  // builds the config object directly and so bypasses `positiveInt`'s `n > 0`
  // clamp (tortoise-volunteer.ts:209). Do not re-open that residual.
  const home = tmpHome();
  // ⛔ NON-TIMEOUT failures, on purpose (#11584 review, round 6). The first
  // version of this row used a `sleep 5` first candidate, which ABORTS the
  // chain under EITHER implementation — so reverting `resolveBlock` to a
  // per-candidate budget left the whole suite green (measured 23/23) while the
  // comment above claimed the shared deadline was pinned. Two candidates that
  // each FAIL (sleep, then non-zero exit) is what distinguishes them: measured
  // per-candidate 2424 ms / 1 fail vs shared-deadline 2005 ms / pass.
  const first = fakeCli(join(home, "a"), "sleep 1.2\nexit 1");
  const second = fakeCli(join(home, "b"), "sleep 1.2\nexit 1");
  const cfgPath = join(home, "tortoise-config.json");
  writeFileSync(cfgPath, JSON.stringify({ volunteer: { timeoutMs: 2000 } }));
  const started = Date.now();
  const verdict = await resolveBlock(
    "p",
    "",
    readVolunteerConfig({}, cfgPath),
    [first, second],
  );
  const elapsed = Date.now() - started;
  assert.equal(verdict.status, "timeout", JSON.stringify(verdict));
  assert.ok(
    elapsed < 2500,
    `the chain must not spend a budget per candidate (took ${elapsed}ms; ` +
      "2 x 2000ms of budget would exceed 3000ms)",
  );
});

test("a TIMEOUT aborts the chain rather than trying the next candidate", async () => {
  // The other half of the same bound, and the invariant the row above was
  // originally named for: a SLOW first candidate must not fall through to a
  // second one, which would multiply the turn's wait by the chain's size.
  const home = tmpHome();
  const slow = fakeCli(join(home, "slow"), "sleep 5");
  const quick = fakeCli(join(home, "quick"), `printf '%s' '{"block":"x"}'`);
  const cfgPath = join(home, "tortoise-config.json");
  writeFileSync(cfgPath, JSON.stringify({ volunteer: { timeoutMs: 300 } }));
  const started = Date.now();
  const verdict = await resolveBlock(
    "p",
    "",
    readVolunteerConfig({}, cfgPath),
    [slow, quick],
  );
  const elapsed = Date.now() - started;
  assert.equal(verdict.status, "timeout", JSON.stringify(verdict));
  assert.ok(elapsed < 1500, `the chain must abort on the first timeout (took ${elapsed}ms)`);
});

test("a reflex that IGNORES SIGTERM is SIGKILLed and the turn is still bounded", async () => {
  // Review finding 1 (P1). `execFile(..., {timeout})` sends ONE SIGTERM and
  // settles only when the child EXITS, so a reflex that traps TERM is never
  // bounded by it. Two measured outcomes, both wrong:
  //   * `sleep 5` (this row): the budget is blown ~17x and the run is reported
  //     as a SUCCESS — `{timeout: 300}` against this child returned
  //     `elapsed: 5007, err: null`, i.e. `ok: true` with an empty block;
  //   * `trap '' TERM; sleep 600`: the promise NEVER settles (measured: still
  //     pending at 6 s, node itself never exited), and Pi awaits handler
  //     promises with no hook timeout of its own — so the TURN hangs forever.
  // Discriminating: restoring `{timeout: timeoutMs}` on the `execFile` call and
  // disarming the watchdog reds this row on both `ok` and the elapsed bound. 
  const home = tmpHome();
  const stubborn = fakeCli(join(home, "bin"), "trap '' TERM; sleep 5");
  const cfgPath = join(home, "tortoise-config.json");
  writeFileSync(cfgPath, JSON.stringify({ volunteer: { timeoutMs: 300 } }));

  const started = Date.now();
  const result = await runReflexCommand(stubborn, "q", "", 300);
  const elapsed = Date.now() - started;

  assert.equal(result.ok, false, "a timed-out reflex is never ok");
  assert.equal(result.timedOut, true, "and it reports the TIMEOUT class");
  assert.ok(
    elapsed < 2000,
    `the turn must be bounded by OUR deadline, not the child's signal ` +
      `disposition (took ${elapsed}ms)`,
  );
});

test("the prompt is bounded in BYTES, not characters", async () => {
  // The reflex's validator charges `len(content.encode("utf-8"))` against a
  // 15 KiB window. A char-indexed bound passes ASCII and REFUSES the same
  // LENGTH of a 3-byte script — which is how a healthy install came to be
  // recorded as inert (review finding 3).
  assert.equal(Buffer.byteLength(clipToBytes("á".repeat(40000), MAX_PROMPT_BYTES), "utf8") <= MAX_PROMPT_BYTES, true);
  const clipped = clipToBytes("á".repeat(40000), MAX_PROMPT_BYTES);
  assert.ok(Buffer.byteLength(clipped, "utf8") <= MAX_PROMPT_BYTES);
  assert.equal(clipped.includes("\uFFFD"), false, "a multi-byte code point must never be split");
  assert.equal(clipToBytes("short", MAX_PROMPT_BYTES), "short", "a fitting prompt is untouched");

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
    { messageType: "user", prompt: "x".repeat(MAX_PROMPT_BYTES * 2) },
    SESSION_CTX(),
  );
  assert.equal(Buffer.byteLength(seenPrompt, "utf8"), MAX_PROMPT_BYTES);
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
  // ⛔ A, B, A: the stated contract is "never the same block twice in a
  // session", not "not the same as last time" (review finding 6 — the code
  // answered the weaker question while the comment claimed the stronger one).
  assert.equal(dedupe("s", "block"), false, "A must not come back after B");
});

test("the dedupe is bounded per session and isolated between sessions", () => {
  const dedupe = createDedupe();
  for (let i = 0; i < MAX_SEEN_PER_SESSION + 10; i += 1) {
    assert.equal(dedupe("s", `block-${i}`), true);
  }
  // The oldest entries have been evicted, so they may surface again; what must
  // hold is that the set never grows past the bound and recent blocks are
  // remembered.
  assert.equal(dedupe("s", `block-${MAX_SEEN_PER_SESSION + 9}`), false);
  assert.equal(dedupe("t", `block-${MAX_SEEN_PER_SESSION + 9}`), true);
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

test("a non-zero exit, a `{status:error}` body, and an unparseable body are fail-open and NEVER `inert`", async () => {
  // ⛔ These are `refused`, not `inert` (#11584 review finding 1, second half).
  // `inert` is the claim "your install is broken — reinstall it", and it
  // LATCHES the seam off for the rest of the process. A non-zero exit from
  // every candidate is what a momentarily locked graph or a reflex mid-upgrade
  // looks like on a perfectly sound install.
  const home = tmpHome();
  const exitFail = fakeCli(join(home, "a"), "exit 2");
  const failed = await resolveBlock(
    "q",
    "",
    readVolunteerConfig({ TORTOISE_COMMAND: exitFail }, join(home, "no.json")),
    [],
  );
  assert.equal(failed.status, "refused");
  assert.match(
    failed.status === "refused" ? failed.detail : "",
    /every resolved tortoise reflex failed \(.*a\/tortoise: /,
  );

  const errorBody = fakeCli(join(home, "b"), `printf '%s' '{"status":"error","error":"graph down"}'`);
  assert.equal(
    (await resolveBlock("q", "", readVolunteerConfig({ TORTOISE_COMMAND: errorBody }, join(home, "no.json")), []))
      .status,
    "refused",
  );

  const garbage = fakeCli(join(home, "c"), "printf '%s' 'not json'");
  assert.equal(
    (await resolveBlock("q", "", readVolunteerConfig({ TORTOISE_COMMAND: garbage }, join(home, "no.json")), []))
      .status,
    "refused",
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

test("a REFUSED verdict stays out of the canonical slot, but its detail is traceable", async () => {
  // Review finding 3 (P2). The module header claimed a breadcrumb when "every
  // resolved candidate FAILS"; the code deliberately writes none (that class is
  // true of a WORKING install — a 402 that clears, a reflex mid-upgrade), so the
  // header lied. The other half is real though: `verdict.detail` — the only
  // string naming the actual cause — was discarded, leaving a persistent
  // misconfiguration (wrong venv, missing extra) with no record anywhere. It now
  // goes to stderr under `TORTOISE_VOLUNTEER_DEBUG=1` and nowhere else.
  //
  // Mutation: drop the `trace(...)` call → the debug assertion REDs. Move the
  // refused branch onto `recordInstallInert` → the no-breadcrumb assertion REDs
  // (and the operator is told to reinstall a working install).
  const run = async (extraEnv: Record<string, string>) => {
    const home = tmpHome();
    const crumbs = join(home, "capture-errors");
    const broken = fakeCli(join(home, "bin"), "exit 2");
    const pi = mockPi();
    tortoiseVolunteer(pi as never, {
      env: { PATH: join(home, "bin"), TORTOISE_COMMAND: broken, ...extraEnv },
      home,
      configPath: join(home, "absent.json"),
      breadcrumbDir: crumbs,
    });
    const chunks: string[] = [];
    const stderr = process.stderr as unknown as { write: (s: string) => boolean };
    const original = stderr.write;
    stderr.write = (s: string): boolean => {
      chunks.push(String(s));
      return true;
    };
    try {
      assert.equal(
        await pi.handlers.before_agent_start({ messageType: "user", prompt: "q" }, SESSION_CTX()),
        undefined,
        "a refused reflex injects nothing",
      );
    } finally {
      stderr.write = original;
    }
    assert.equal(
      existsSync(join(crumbs, "pi-install.json")),
      false,
      "a REFUSED reflex is not an inert INSTALL — the advice would be wrong",
    );
    return chunks.join("");
  };

  const quiet = await run({});
  assert.equal(quiet, "", "the trace is opt-in, and silent by default");

  const traced = await run({ TORTOISE_VOLUNTEER_DEBUG: "1" });
  assert.match(traced, /refused: every resolved tortoise reflex failed/, traced);
  assert.match(traced, /bin\/tortoise/, "the detail names WHICH candidate failed");
});

test("a REFUSED hosted request is not an inert install: no breadcrumb, no latch", async () => {
  // A 401/402/403/5xx means the endpoint ANSWERED and refused us — so the
  // install is demonstrably working. Classifying that as `inert` wrote the
  // canonical `install-inert` slot, whose documented meaning is "reinstall
  // it": an operator whose hosted quota was exhausted was told to reinstall a
  // working install, and the cause was never named (#11584 review).
  const home = tmpHome();
  const crumbs = join(home, "capture-errors");
  const cfgPath = join(home, "tortoise-config.json");
  writeFileSync(cfgPath, JSON.stringify({ apiKey: "tt_live_key" }));
  // No CLI anywhere, so the seam falls through to the hosted transport.
  // The fetch is STATEFUL: the first turn is refused (402), every later turn
  // answers. That is what makes the no-latch assertion discriminating — a
  // handler that latches on `refused` would still pass a one-turn test, and a
  // latched seam silently disables per-turn injection for the whole Pi process
  // after a transient 402, which is the user-visible failure being fixed.
  let calls = 0;
  const fetchStub = (async () => {
    calls += 1;
    if (calls === 1) {
      return { ok: false, status: 402, statusText: "Payment Required" } as Response;
    }
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({ block: "## Memory\nquota restored", surfaced: [] }),
    } as unknown as Response;
  }) as typeof fetch;
  const pi = mockPi();
  tortoiseVolunteer(pi as never, {
    env: { PATH: join(home, "empty-bin") },
    home,
    configPath: cfgPath,
    breadcrumbDir: crumbs,
    fetchImpl: fetchStub,
  });

  assert.equal(
    await pi.handlers.before_agent_start({ messageType: "user", prompt: "q" }, SESSION_CTX()),
    undefined,
    "a refused hosted request injects nothing",
  );
  assert.equal(
    existsSync(join(crumbs, "pi-install.json")),
    false,
    "a REFUSED request must not read as an inert INSTALL — the operator would be " +
      "told to reinstall something that works",
  );

  // …and it must NOT latch: the very next turn, with the endpoint answering,
  // has to inject. A latched seam is dead for the rest of the process.
  const second = (await pi.handlers.before_agent_start(
    { messageType: "user", prompt: "another question" },
    SESSION_CTX(),
  )) as { message?: { content?: string } } | undefined;
  assert.ok(second?.message, "a `refused` turn must not latch the seam OFF");
  assert.match(
    String(second?.message?.content ?? ""),
    /quota restored/,
    "the second turn must reach the endpoint again after a transient 402",
  );

  // The verdict names the real cause, and is distinct from both `inert` and
  // `timeout` so the distinction survives at the API, not only at the file.
  const verdict = await resolveBlock(
    "q",
    "",
    readVolunteerConfig({ TORTOISE_API_KEY: "tt_live_key" }, cfgPath),
    [],
    (async () =>
      ({ ok: false, status: 402, statusText: "Payment Required" }) as Response) as typeof fetch,
  );
  assert.equal(verdict.status, "refused");
  assert.match(verdict.status === "refused" ? verdict.detail : "", /HTTP 402/);
});

test("a TRANSIENT CLI failure does not latch, and writes no install breadcrumb", async () => {
  // #11584 review finding 1, second half: the latch exists so a DEAD install
  // is not re-walked every turn, and its justification is "the remedy is an
  // install" — true only of `inert`. A locked graph or a reflex mid-upgrade is
  // a PER-TURN fact: classifying it `inert` disabled per-turn memory for the
  // rest of the Pi process and wrote `kind: install-inert` ("reinstall") about
  // an install that works. The hosted leg was split out separately; this is the
  // CLI leg, which is the ordinary transient on a laptop.
  //
  // The fake CLI fails ONCE (consuming its own flag) and then answers normally,
  // so a latched seam CANNOT pass: turn 2 would never call it.
  const home = tmpHome();
  const crumbs = join(home, "capture-errors");
  const flag = join(home, "still-broken");
  writeFileSync(flag, "");
  const flaky = fakeCli(
    join(home, "bin"),
    [
      `if [ -f ${JSON.stringify(flag)} ]; then rm -f ${JSON.stringify(flag)}; exit 2; fi`,
      `printf '%s' '{"block":"## Memory surfaced this turn","surfaced":[]}'`,
    ].join("\n"),
  );
  assert.notEqual(statSync(flaky).mode & 0o111, 0, "the stub must be executable");

  const pi = mockPi();
  tortoiseVolunteer(pi as never, {
    env: { PATH: join(home, "bin") },
    home,
    configPath: join(home, "absent.json"),
    breadcrumbDir: crumbs,
  });

  assert.equal(
    await pi.handlers.before_agent_start({ messageType: "user", prompt: "q" }, SESSION_CTX()),
    undefined,
    "a failed reflex injects nothing, fail-open",
  );
  assert.equal(
    existsSync(join(crumbs, "pi-install.json")),
    false,
    "a TRANSIENT failure is not evidence about the install — no `install-inert` " +
      "breadcrumb, or the operator is told to reinstall a working install",
  );

  const second = (await pi.handlers.before_agent_start(
    { messageType: "user", prompt: "q" },
    SESSION_CTX(),
  )) as { message?: { content?: unknown } } | undefined;
  assert.ok(second?.message, "turn 2 must retry: a transient CLI failure may NOT latch");
  assert.match(String(second?.message?.content ?? ""), /Memory surfaced this turn/);
});

test("a THROW out of the resolver is `refused`, not an `inert` latch", async () => {
  // #11584 review, second issue: the CLI-leg row above covers one half of the
  // finding; this covers the resolver's CATCH-ALL. Reverting
  // `{status: "refused"}` to `{status: "inert"}` here used to leave the whole
  // suite green (measured: 22/22), so the change was unguarded on one of its
  // two paths.
  //
  // A command carrying a NUL byte makes `execFile` reject SYNCHRONOUSLY
  // (`ERR_INVALID_ARG_VALUE`), so the rejection surfaces out of `resolveBlock`
  // and lands in the factory's catch-all. Measured against node 24.
  const home = tmpHome();
  const crumbs = join(home, "capture-errors");
  const pi = mockPi();
  tortoiseVolunteer(pi as never, {
    env: { PATH: "", TORTOISE_COMMAND: "bad\u0000cmd" },
    home,
    configPath: join(home, "absent.json"),
    breadcrumbDir: crumbs,
  });
  assert.equal(
    await pi.handlers.before_agent_start({ messageType: "user", prompt: "q" }, SESSION_CTX()),
    undefined,
    "a throwing resolver is fail-open",
  );
  // ⛔ WHAT THIS ROW DOES AND DOES NOT PROVE (#11584 review, round 3). It
  // proves the CLASSIFICATION: the catch-all must be `refused`, so no
  // `install-inert` breadcrumb is written. Mutating the catch-all back to
  // `inert` reds it (measured: 22 pass / 1 fail).
  //
  // It does NOT prove the absence of a LATCH, and the earlier version of this
  // block claimed it did. A NUL command throws identically on every turn, so
  // "turn 2 returns undefined" is true under either behaviour — adding
  // `inertLatched = true` to the catch-all (with no breadcrumb) left the whole
  // suite green, 23/23.
  //
  // ⛔ AND NOTHING ELSE OWNS IT EITHER (measured, round 4). The row
  // `a TRANSIENT CLI failure does not latch, and writes no install breadcrumb`
  // does NOT cover this class: it drives the SHARED `refused` branch, where a
  // latch is caught (21 pass / 2 fail). A latch written INSIDE the catch block
  // is caught by nobody — `verdict = {status:"refused",…}; inertLatched = true;`
  // there leaves the suite green 23/23, which is why an earlier version of this
  // paragraph was wrong to name that row as the owner while citing the very
  // measurement that disproved it. This is a KNOWN RESIDUAL with no owner.
  // It is not a hole in the shipped behaviour (the catch block does not latch);
  // it is a hole in the ability of the suite to catch someone making it latch.
  // Closing it needs a transient resolver throw — a stub that throws on turn 1
  // and answers on turn 2 — which this file does not yet have.
  // Assert only what is observable here rather than vouching for coverage this
  // row does not have.
  assert.equal(
    existsSync(join(crumbs, "pi-install.json")),
    false,
    "a bug in THIS seam is not evidence about the install: no `install-inert` " +
      "breadcrumb, or the operator is told to reinstall a working install",
  );
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
  // Hermetic for the same reason as the latch row above: this row is titled "no
  // reflex and no key", so inheriting the operator's config contradicts it —
  // and with a real key the prompt would be POSTed to the hosted endpoint.
  let hostedCalls = 0;
  tortoiseVolunteer(pi as never, {
    env: { PATH: "" },
    home,
    configPath: join(home, "absent.json"),
    breadcrumbDir: crumbs,
    fetchImpl: (async () => {
      hostedCalls += 1;
      return { ok: false, status: 500, statusText: "Unexpected" } as Response;
    }) as typeof fetch,
  });
  assert.equal(
    await pi.handlers.before_agent_start({ messageType: "user", prompt: "q" }, SESSION_CTX()),
    undefined,
  );
  assert.equal(hostedCalls, 0, "this row asserts NO KEY — it must never reach the network");
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
  // Assert something about the fake CLI that CAN fail: it is the seam's input,
  // and a non-executable stub would make this row green for the wrong reason
  // (review finding 7 — the previous line compared a constant to `true`).
  assert.equal(statSync(emptyCli).isFile(), true);
  assert.notEqual(statSync(emptyCli).mode & 0o111, 0, `${emptyCli} must be executable`);
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
