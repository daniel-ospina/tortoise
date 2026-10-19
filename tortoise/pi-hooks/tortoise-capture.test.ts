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
  CAPTURE_LANES,
  COHORT_COST_CAP_CODE,
  DISCARD_COUNT_EXCEEDED,
  DISCARD_HTTP_REJECTED,
  HARNESS,
  MAX_ATTEMPTS,
  MAX_TURNS,
  PROBE_TIMEOUT_MS,
  REQUEST_TIMEOUT_MS,
  RETRY_MAX_MS,
  FLUSH_MAX_ATTEMPTS,
  FLUSH_MAX_CHAIN,
  SPOOL_MAX_ENTRIES,
  SPOOL_MAX_TOTAL_BYTES,
  SPOOL_STATUS_FILE,
  TRUNCATION_SENTINEL,
  TURN_MAX_CHARS,
  _CREDENTIAL_MODE_WARNED,
  backoffDelay,
  buildCapturePayload,
  captureHealthLine,
  captureKey,
  carriedWindow,
  clampAttempts,
  clampWindow,
  classifyFailure,
  clipTurnContent,
  contentDigest,
  countPendingEntries,
  deriveMachineId,
  discardEntry,
  entryKey,
  extractTurns,
  flushSpool,
  listSpoolEntries,
  modelLabel,
  normaliseLane,
  postInstallProbe,
  pruneSpool,
  quarantineEntry,
  readCaptureStatus,
  readDiscards,
  readSpoolEntry,
  readSpoolTurns,
  resolveConfig,
  sourceName,
  spoolEntryBytes,
  truncationMarker,
  warnIfCredentialStoreIsReadable,
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

test("extractTurns marks a turn cut at the hosted per-turn window (#4897)", () => {
  const long = "a".repeat(9000);
  const turns = extractTurns([
    { type: "message", message: { role: "user", content: long } },
  ]);
  // The cut fills the window exactly, and the marker lives INSIDE it so the
  // server's own cap re-application cannot remove it.
  assert.equal(Array.from(turns[0].content).length, TURN_MAX_CHARS);
  assert.ok(turns[0].content.includes(TRUNCATION_SENTINEL));
  assert.ok(turns[0].content.endsWith(truncationMarker(9000)));
  assert.ok(turns[0].content.includes("original length 9000"));
  // The body is the turn's own prefix — nothing synthetic was inserted.
  const body = turns[0].content.slice(0, turns[0].content.indexOf(TRUNCATION_SENTINEL) - 1);
  assert.ok(long.startsWith(body));
});

test("clipTurnContent marks only an actual cut (#4897)", () => {
  const cap = TURN_MAX_CHARS;
  for (const n of [0, 1, cap - 1, cap]) {
    const content = "a".repeat(n);
    assert.equal(clipTurnContent(content), content, `${n} chars fits`);
    assert.ok(!clipTurnContent(content).includes(TRUNCATION_SENTINEL));
  }
  const clipped = clipTurnContent("a".repeat(cap + 1));
  assert.equal(Array.from(clipped).length, cap);
  assert.ok(clipped.endsWith(truncationMarker(cap + 1)));
  // STABLE under re-application (the server re-applies the window).
  assert.equal(clipTurnContent(clipped), clipped);
});

test("clipTurnContent never splits a multi-byte character at the cut (#4897)", () => {
  const emoji = "\u{1F600}".repeat(6000);
  const clipped = clipTurnContent(emoji);
  assert.equal(Array.from(clipped).length, TURN_MAX_CHARS);
  assert.ok(clipped.includes(TRUNCATION_SENTINEL));
  assert.ok(clipped.includes("original length 6000"));
  // No lone surrogate anywhere in the result.
  for (let i = 0; i < clipped.length; i++) {
    const code = clipped.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = clipped.charCodeAt(i + 1);
      assert.ok(next >= 0xdc00 && next <= 0xdfff, "high surrogate not paired");
      i++;
    } else {
      assert.ok(!(code >= 0xdc00 && code <= 0xdfff), "lone low surrogate");
    }
  }
});

test("the marker records the true length and is code-point counted (#4897)", () => {
  assert.equal(truncationMarker(12000), ` ${TRUNCATION_SENTINEL} original length 12000 chars]`);
  const astr = "\u{1F600}".repeat(TURN_MAX_CHARS + 5);
  assert.ok(clipTurnContent(astr).includes(`original length ${TURN_MAX_CHARS + 5}`));
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
    captureLane: "hook",
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
  // #3516 §B: the ENTRY's lane reaches the wire. The in-process hook spools
  // with 'hook'; dropping this stores no lane and the hook-liveness check
  // reports a WORKING hook as not-live (mutation: delete it — must go red).
  assert.equal(payload.capture_lane, "hook");
});

test("buildCapturePayload forwards the ENTRY's lane, never a hardcoded one", () => {
  // #3516 §B: the lane describes the spool ENTRY's producer, not the process
  // doing the draining. flushSpool drains entries written by other legs, so a
  // hardcoded 'hook' would stamp a lane on a lane-less backfill/import entry —
  // the false-POSITIVE half of falsifiability.
  const base = {
    sessionId: "s",
    turns: [{ role: "user" as const, content: "x" }],
    source: "t",
    machineId: "m",
  };
  assert.equal(buildCapturePayload({ ...base, captureLane: "hook" }).capture_lane, "hook");
  assert.ok(
    !("capture_lane" in buildCapturePayload(base)),
    "a lane-less entry was stamped with a lane on the wire",
  );
});

test("buildCapturePayload forwards the ENTRY's client stamp, and omits it when absent", () => {
  // #3516 §B / #3515 piece 12: the stamp is the store-proven floor's INPUT.
  // Two opposite bugs, both fatal to falsifiability: inventing an instant on a
  // pre-#3516 entry makes the floor pass on nothing, and dropping one the entry
  // carries makes the floor read DISABLED for a session that WAS stamped.
  const base = {
    sessionId: "s",
    turns: [{ role: "user" as const, content: "x" }],
    source: "t",
    machineId: "m",
  };
  const withStamp = buildCapturePayload({
    ...base,
    clientCapturedAt: 1234.5,
    clientCapturedAtSource: "file_mtime",
  });
  assert.equal(withStamp.client_captured_at, 1234.5);
  assert.equal(withStamp.client_captured_at_source, "file_mtime");
  assert.ok(
    !("client_captured_at" in buildCapturePayload(base)),
    "a stamp-less entry was given an instant on the wire",
  );
  // A source must never ride the wire WITHOUT its instant — that is a
  // provenance recorded for a timestamp that does not exist.
  assert.ok(
    !("client_captured_at_source" in
      buildCapturePayload({ ...base, clientCapturedAtSource: "cli_observed" })),
    "a source rode the wire without its instant",
  );
});

test("the spooled stamp survives a drain, and a LATE stamp is re-delivered", async () => {
  // #3516 §B. The stamp is METADATA, not content — exactly like the lane — so
  // the filing marker must account for it. Otherwise a stamp that appears
  // after a filing is stranded on the spool forever and the floor reads
  // DISABLED for precisely the session the floor exists for. This mirrors
  // `fix(capture): the drain CAS must include the lane`, the same defect the
  // LANE field forced on this codebase.
  const spool = tmpSpool();
  const server = recordingServer();

  // (1) filed with a lane but NO stamp.
  writeSpoolEntry(spool, {
    ...snapshot("sess-stamp", [{ role: "user", content: "hi" }]),
    captureLane: "hook",
  });
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  assert.equal(server.posts(), 1);
  assert.ok(
    !("client_captured_at" in (server.sessions.get("sess-stamp") ?? {})),
    "a stamp-less entry was given an instant",
  );

  // (2) a byte-identical re-snapshot that DOES carry a stamp.
  writeSpoolEntry(spool, {
    ...snapshot("sess-stamp", [{ role: "user", content: "hi" }]),
    captureLane: "hook",
    clientCapturedAt: 1234.5,
    clientCapturedAtSource: "cli_observed",
  });
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 2 });
  assert.equal(
    server.posts(),
    2,
    "the stamped entry was SKIPPED — the stamp is stranded on the spool",
  );
  assert.equal(server.sessions.get("sess-stamp")?.client_captured_at, 1234.5);
  assert.equal(
    server.sessions.get("sess-stamp")?.client_captured_at_source,
    "cli_observed",
  );

  // (3) and now the marker covers the stamp: a third flush is a no-op.
  const third = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 3 });
  assert.equal(third.attempted, 0, "the stamped entry re-posted forever");
  assert.equal(server.posts(), 2);
});

test("the spool never relabels an instant with another writer's clock", () => {
  // #3516 §B: the instant and its source are ONE pair. A source names the clock
  // that PRODUCED the instant, so resolving them independently lets a later
  // writer hang its own clock on an earlier writer's reading. The victim is a
  // real observation: an absent source PASSES the floor, an explicit 'unknown'
  // DISABLES it — so the relabelling makes a verifiable session unverifiable.
  const spool = tmpSpool();
  const sid = "sess-pair";
  writeSpoolEntry(spool, {
    ...snapshot(sid, [{ role: "user", content: "hi" }]),
    captureLane: "hook",
    clientCapturedAt: 1000,
  });
  writeSpoolEntry(spool, {
    ...snapshot(sid, [
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]),
    captureLane: "store_sync",
    clientCapturedAt: 2000,
    clientCapturedAtSource: "unknown",
  });

  const meta = readSpoolEntry(spool, sid);
  assert.equal(meta?.client_captured_at, 1000, "the hook's instant was replaced");
  assert.equal(
    meta?.client_captured_at_source,
    undefined,
    "the backfill's clock was attached to the hook's instant",
  );
});

test("an unrecognised clock is normalised to 'unknown', never forwarded", () => {
  // A source the server refuses is a 422, a 422 is classified PERMANENT, and the
  // drain then unlinks the entry's turn log — the only copy. Normalising at the
  // writer makes that refusal unreachable from this leg.
  const spool = tmpSpool();
  writeSpoolEntry(spool, {
    ...snapshot("sess-badclock", [{ role: "user", content: "hi" }]),
    clientCapturedAt: 1000,
    clientCapturedAtSource: "wall_clock",
  });
  assert.equal(
    readSpoolEntry(spool, "sess-badclock")?.client_captured_at_source,
    "unknown",
    "an unrecognised clock was forwarded to the server",
  );
});

test("a non-finite instant is never written into the shared spool", () => {
  // The two legs share one directory. `JSON.stringify` turns NaN/Infinity into
  // `null`, so this leg must refuse them at the source rather than emit a `null`
  // instant the Python leg would read as an absent stamp.
  const spool = tmpSpool();
  writeSpoolEntry(spool, {
    ...snapshot("sess-nonfinite", [{ role: "user", content: "hi" }]),
    clientCapturedAt: Number.NaN,
    clientCapturedAtSource: "cli_observed",
  });
  assert.equal(
    readSpoolEntry(spool, "sess-nonfinite")?.client_captured_at,
    undefined,
    "a NaN was stored as a clock reading",
  );
});

test("buildCapturePayload normalises a bad clock instead of forwarding it", () => {
  // The drain forwards whatever is on disk, and the two legs SHARE the spool
  // directory — so a meta the other leg (or a hand-edit) left with an
  // unrecognised token would otherwise be posted verbatim, and the server's 422
  // is classified PERMANENT, so `discardEntry` unlinks the only copy.
  const base = {
    sessionId: "s",
    turns: [{ role: "user" as const, content: "x" }],
    source: "t",
    machineId: "m",
  };
  assert.equal(
    buildCapturePayload({
      ...base,
      clientCapturedAt: 1000,
      clientCapturedAtSource: "wall_clock",
    }).client_captured_at_source,
    "unknown",
    "an unrecognised clock was forwarded to the server",
  );
  // A non-finite instant must not be posted as an instant either — `JSON.stringify`
  // would emit `null`, which the Python leg reads as an ABSENT stamp.
  assert.ok(
    !("client_captured_at" in buildCapturePayload({ ...base, clientCapturedAt: Number.NaN })),
    "a NaN reached the wire",
  );
});

test("a refused instant does not repost an already-filed entry", async () => {
  // #3516 §B: `stampUpgrade` is judged against `finiteInstant`, the SAME
  // authority the pair resolution uses. Judging it against a raw `!== undefined`
  // makes a REFUSED value look like a new stamp: the dedup is bypassed AND the
  // filing marker is dropped, so an already-filed, byte-identical entry is
  // re-POSTed while no stamp is written at all.
  const spool = tmpSpool();
  const sid = "sess-refused-upgrade";
  const turns = [{ role: "user" as const, content: "hi" }];
  writeSpoolEntry(spool, snapshot(sid, turns));

  const server = recordingServer();
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  assert.equal(server.posts(), 1);
  assert.ok(
    readSpoolEntry(spool, sid)?.filed_key,
    "the first filing did not stamp a marker — the test cannot prove anything",
  );

  const res = writeSpoolEntry(spool, {
    ...snapshot(sid, turns),
    clientCapturedAt: Number.NaN,
  });
  assert.equal(res.written, false, "a refused instant was treated as a stamp upgrade");
  assert.ok(
    readSpoolEntry(spool, sid)?.filed_key,
    "a refused instant dropped the filing marker",
  );

  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 2 });
  assert.equal(server.posts(), 1, "an already-filed entry was re-POSTed");
});

test("a refused STORED instant does not repost forever (skip + CAS normalise)", async () => {
  // A meta on disk carrying a value the guards refuse, while the payload posts the
  // NORMALISED one. Comparing the raw disk value against the normalised posted one
  // can never match — and the drain never rewrites the meta, so the entry would
  // re-POST its whole transcript on EVERY drain, forever (#4714). The two legs
  // share this directory, so a foreign or hand-edited meta is exactly the input
  // that reaches it. Python is fixed the same way
  // (`test_a_refused_stored_instant_does_not_repost_forever`).
  const spool = tmpSpool();
  const sid = "sess-refused-stored";
  const turns = [{ role: "user" as const, content: "hi" }];
  writeSpoolEntry(spool, { ...snapshot(sid, turns), clientCapturedAt: 1700000010 });

  const server = recordingServer();
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  assert.equal(server.posts(), 1);

  // Corrupt the STORED instant the way a foreign or hand-edited meta could.
  const dir = join(spool, "entries");
  const file = readdirSync(dir).find((f) => f.endsWith(".meta.json"));
  assert.ok(file, "no meta file was written — the test cannot prove anything");
  writeFileSync(
    join(dir, file as string),
    JSON.stringify({ ...readSpoolEntry(spool, sid), client_captured_at: "1700000000.5" }),
    "utf-8",
  );

  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 2 });
  const settled = server.posts();
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 3 });
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 4 });
  assert.equal(
    server.posts(),
    settled,
    "a refused stored instant re-POSTed on every drain — it is never marked filed",
  );
});

test("a falsy stored source becomes 'unknown', matching the Python drain", () => {
  // The writer normalises `""`/`0`/`false` to `unknown`; a truthy guard at the
  // DRAIN would drop them instead, and an absent source PASSES the floor while
  // `unknown` DISABLES it — so the same on-disk entry would give opposite verdicts
  // depending on which leg drained it.
  const base = {
    sessionId: "s",
    turns: [{ role: "user" as const, content: "x" }],
    source: "t",
    machineId: "m",
  };
  for (const falsy of ["", 0, false]) {
    assert.equal(
      buildCapturePayload({
        ...base,
        clientCapturedAt: 1000,
        clientCapturedAtSource: falsy as never,
      }).client_captured_at_source,
      "unknown",
      `${JSON.stringify(falsy)}: a falsy source was dropped instead of normalised`,
    );
  }
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
  // Our own writers pin the store closed (`os.open(..., 0o600)`); mirror that so
  // the co-source tests below exercise the happy mode and stay stderr-quiet.
  // #5109's open-mode case is its own test (`chmodSync` it to 0644 there).
  chmodSync(path, 0o600);
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

// ── #5109: the READ side of the credential store ─────────────────────────
//
// Our writers pin the store closed (CLI: `os.open(..., 0o600)` + `chmod(home,
// 0o700)`), but nothing checked the mode on READ, so a store that had drifted
// (restored from a backup, `cp`'d without `-p`, re-created by hand) was used at
// whatever mode it carried, silently. This is the sibling of the CLI half
// (`tortoise/__main__.py::_warn_if_credential_store_is_readable`) for the OTHER
// in-repo reader of a credential store — `resolveConfig`, the seam every
// `turn_end` resolves through. Warn-only, for the reason the CLI half records:
// a 0644 store behind a 0700 parent is not reachable by another user.

/** Collect what the extension writes to stderr/warn while `fn` runs. */
function captureWarnings<T>(fn: () => T): { value: T; warnings: string[] } {
  const warnings: string[] = [];
  mock.method(console, "warn", (...args: unknown[]) => {
    warnings.push(args.map((a) => String(a)).join(" "));
  });
  try {
    return { value: fn(), warnings };
  } finally {
    mock.restoreAll();
  }
}

function withMode<T>(json: Record<string, unknown>, mode: number, fn: (path: string) => T): T {
  return withTempConfig(json, (path) => {
    chmodSync(path, mode);
    return fn(path);
  });
}

test("resolveConfig warns when the credential store is group/other-readable", () => {
  _CREDENTIAL_MODE_WARNED.clear();
  withMode({ apiKey: "tt_stored" }, 0o644, (path) => {
    const { value: cfg, warnings } = captureWarnings(() => resolveConfig({}, path));
    assert.equal(cfg.apiKey, "tt_stored", "the check must never refuse the credential");
    assert.equal(warnings.length, 1, `expected exactly one warning, got ${warnings.length}`);
    assert.ok(warnings[0]?.includes(path), "the warning must name the store path");
    assert.ok(warnings[0]?.includes("0644"), "the warning must name the mode");
  });
});

test("the store-mode warning never quotes the credential it warns about", () => {
  _CREDENTIAL_MODE_WARNED.clear();
  withMode({ apiKey: "tt_sentinel_key_0123456789abcdef" }, 0o644, (path) => {
    const { warnings } = captureWarnings(() => resolveConfig({}, path));
    assert.equal(warnings.length, 1);
    assert.ok(!warnings.join("\n").includes("tt_sentinel_key_0123456789abcdef"),
      "a warning that prints the key writes the secret into the transcript #5109 is about");
  });
});

test("a 0600 store is silent — the check fires on group/other bits only", () => {
  _CREDENTIAL_MODE_WARNED.clear();
  withMode({ apiKey: "tt_stored" }, 0o600, (path) => {
    const { warnings } = captureWarnings(() => resolveConfig({}, path));
    assert.deepEqual(warnings, [], "a correctly-permissioned store must not warn");
  });
});

test("an ENV key never reaches the store check (no store, nothing to have drifted)", () => {
  _CREDENTIAL_MODE_WARNED.clear();
  withMode({ apiKey: "tt_stored" }, 0o644, (path) => {
    const { value: cfg, warnings } = captureWarnings(() =>
      resolveConfig({ TORTOISE_API_KEY: "tt_env" }, path),
    );
    assert.equal(cfg.apiKey, "tt_env");
    assert.deepEqual(warnings, [], "the store is not the source here, so it is not checked");
  });
});

test("an unstattable store cannot warn or throw (the check never fails the command)", () => {
  _CREDENTIAL_MODE_WARNED.clear();
  // A DIRECTORY at the config path: `readFileSync` fails (so no key is sourced
  // and the check is not reached), and the assertion below is what keeps a
  // future refactor from turning `statSync` into a throw on the resolve path.
  const dir = mkdtempSync(join(tmpdir(), "tortoise-capture-cfgdir-"));
  try {
    const { value: cfg, warnings } = captureWarnings(() => resolveConfig({}, dir));
    assert.equal(cfg.apiKey, "");
    assert.deepEqual(warnings, []);
    assert.doesNotThrow(() => warnIfCredentialStoreIsReadable(join(dir, "absent.json")));
    // The DIRECT call gets its OWN capture window. `captureWarnings` restores
    // the `console.warn` mock in its `finally` — which runs as the arrow above
    // returns — so the earlier `warnings` array is detached by this point and
    // asserting on it would pass no matter what the function did (a dead
    // assertion: it could start warning about absent stores and stay green).
    // Capturing the call itself is what makes "an absent store is silent"
    // falsifiable.
    const direct = captureWarnings(() =>
      warnIfCredentialStoreIsReadable(join(dir, "absent.json")));
    assert.deepEqual(direct.warnings, [], "a store we cannot stat is not this check's business");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the warning is keyed by PATH — one line per store, not one per process", () => {
  _CREDENTIAL_MODE_WARNED.clear();
  withMode({ apiKey: "tt_a" }, 0o644, (a) => {
    withMode({ apiKey: "tt_b" }, 0o644, (b) => {
      const { warnings } = captureWarnings(() => {
        // Same path twice (every `turn_end` resolves it): ONE line, not two.
        resolveConfig({}, a);
        resolveConfig({}, a);
        // A different store is a different hazard: it gets its own line.
        resolveConfig({}, b);
      });
      assert.equal(warnings.length, 2,
        "warn once per path — repeated resolutions must not flood, two stores must both speak");
      assert.equal(warnings.filter((w) => w.includes(a)).length, 1);
      assert.equal(warnings.filter((w) => w.includes(b)).length, 1);
    });
  });
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

/**
 * A fetch that answers with a fixed HTTP status + detail.
 *
 * `detail` is `unknown`, not `string`: the real refusal body is
 * `{"detail": {"code": "quota_exceeded", …}}`, and `post()` serializes that
 * OBJECT into its searchable detail string — so the object form is what the
 * tests must model (review cycle 3 found the narrower annotation making the
 * suite a type error in a file it asserts on, invisible only because
 * `node --test` strips types without checking them).
 */
function statusFetch(status: number, detail?: string | Record<string, unknown>) {
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
  // #3516 §B: the in-process hook's entry claims its lane AT THE WRITE. Without
  // this assertion, deleting `captureLane: "hook"` from `spoolSnapshot` leaves
  // the whole suite green while a WORKING hook files as not-live.
  assert.equal(spooled.capture_lane, "hook", "the hook's spool entry lost its lane");
  // #3516 §B / piece 12 row 1: the in-process recorder stamps its OWN clock and
  // claims NO source. Without these two assertions, deleting
  // `clientCapturedAt: Date.now() / 1000` from `spoolSnapshot` leaves the whole
  // suite green while every Pi session files TIMELESS — and the floor then reads
  // DISABLED for exactly the sessions it exists for.
  assert.ok(
    typeof spooled.client_captured_at === "number" &&
      Number.isFinite(spooled.client_captured_at),
    "the hook's spool entry lost its client capture instant",
  );
  assert.equal(
    spooled.client_captured_at_source,
    undefined,
    "the recorder must claim NO clock source — its instant is its own (piece 12 row 1)",
  );
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
  // The stamp must survive to the WIRE, not merely to the spool's meta file.
  assert.ok(
    typeof filed[0].body.client_captured_at === "number",
    "the recorder's instant never reached the wire — the floor sees DISABLED",
  );
  assert.ok(
    !("client_captured_at_source" in filed[0].body),
    "the recorder has no clock to name — it must post NO source",
  );
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

test("the spooled lane survives a drain and is never fabricated", async () => {
  // #3516 §B. The Pi hook spools WITH a lane; the drain happens later and may
  // be run by the PYTHON leg (`tortoise session drain`) over this same
  // directory. Two falsifiability bugs, in opposite directions:
  //   (a) the entry's lane is dropped on the drain → a WORKING hook reads as
  //       not-live (false-NEGATIVE);
  //   (b) a lane-less import entry drained here is stamped 'hook' → a capture
  //       that never saw a hook reads as live (false-POSITIVE).
  const spool = tmpSpool();
  const server = recordingServer();
  writeSpoolEntry(spool, {
    ...snapshot("sess-laned", [{ role: "user", content: "hi" }]),
    captureLane: "hook",
  });
  writeSpoolEntry(spool, snapshot("sess-import", [{ role: "user", content: "yo" }]));
  // A lane-less re-snapshot must not ERASE a lane already on disk. The turns
  // must GROW: an identical-content re-snapshot short-circuits on the dedup
  // guard and never reaches the meta construction, so it would prove nothing.
  writeSpoolEntry(spool, {
    ...snapshot("sess-laned", [
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]),
  });

  assert.equal(
    readSpoolEntry(spool, "sess-laned")?.capture_lane,
    "hook",
    "a lane-less re-snapshot erased the lane",
  );

  const res = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  assert.equal(res.filed, 2, "both entries should be filed");
  assert.equal(server.sessions.get("sess-laned")?.capture_lane, "hook");
  assert.ok(
    !("capture_lane" in (server.sessions.get("sess-import") ?? {})),
    "a lane-less import entry was stamped with a lane on the wire",
  );
});

test("a lane upgrade survives the dedup and refiles an ALREADY FILED entry", async () => {
  // #3516 §B. `sameContent` and `filed_key` are both content-addressed and the
  // lane is not part of the content, so an entry first captured lane-less can
  // be covered by a byte-identical hook re-snapshot: without the upgrade rule
  // the write is a no-op AND the drain skips the entry forever, stranding a
  // working hook as not-live. Mirrors
  // tests/test_capture_lane_3516.py::test_spool_lane_upgrade_refiles_an_already_filed_entry.
  const spool = tmpSpool();
  const server = recordingServer();
  const turns = [{ role: "user" as const, content: "same" }];

  writeSpoolEntry(spool, snapshot("sess-upgrade", turns)); // lane-less
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  assert.ok(
    readSpoolEntry(spool, "sess-upgrade")?.filed_key,
    "the first filing left no marker — the test could not prove anything",
  );

  const res = writeSpoolEntry(spool, {
    ...snapshot("sess-upgrade", turns),
    captureLane: "hook",
  });
  assert.equal(res.written, true, "the identical-content dedup swallowed the lane upgrade");
  assert.equal(
    readSpoolEntry(spool, "sess-upgrade")?.filed_key,
    undefined,
    "the filing marker was not invalidated — the entry would be skipped forever",
  );

  const second = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 2 });
  assert.equal(second.filed, 1, "the lane upgrade never reached the wire");
  assert.equal(server.sessions.get("sess-upgrade")?.capture_lane, "hook");
});

test("a lane upgrade during the POST window is not swallowed", async () => {
  // The drain's CAS stamped `filed_key` on a CONTENT digest, which excludes the
  // lane — so an upgrade landing mid-POST was cancelled and the lane stranded
  // forever. `capture_key` is content-derived, so the CAS identity needs the
  // lane too.
  const spool = tmpSpool();
  const sid = "sess-cas";
  const turns = [{ role: "user" as const, content: "same" }];
  writeSpoolEntry(spool, snapshot(sid, turns)); // lane-less
  let first = true;
  const fetchImpl = async () => {
    if (first) {
      first = false;
      // the hook re-snapshots the SAME entry while this POST is in flight
      writeSpoolEntry(spool, { ...snapshot(sid, turns), captureLane: "hook" });
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: fetchImpl as never, now: 1 });
  const meta = readSpoolEntry(spool, sid);
  assert.equal(meta?.capture_lane, "hook");
  assert.notEqual(
    meta?.filed_key,
    meta?.capture_key,
    "the CAS re-filed a lane-upgraded entry — the lane can never be re-POSTed",
  );

  const server = recordingServer();
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 2 });
  assert.equal(
    server.sessions.get(sid)?.capture_lane,
    "hook",
    "the lane was STRANDED — the drain skipped the upgraded entry forever",
  );
});

test("a lane arriving AFTER a lane-less filing is never skipped", async () => {
  // `filed_key` is content-derived and says nothing about the LANE, so the skip
  // must also require the recorded `filed_lane` to match — otherwise a lane-less
  // filing masks a lane that appears later and the lane is stranded.
  const spool = tmpSpool();
  const sid = "sess-filed-lane";
  const turns = [{ role: "user" as const, content: "same" }];
  writeSpoolEntry(spool, snapshot(sid, turns));

  const server = recordingServer();
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  const filed = readSpoolEntry(spool, sid)!;
  assert.equal(filed.filed_key, filed.capture_key);
  // `JSON.stringify` drops an `undefined` value, so a lane-less filing leaves
  // the key absent — absent and `undefined` must compare equal in the skip.
  assert.equal(filed.filed_lane, undefined, "the filing did not record the delivered lane");
  // The lane appears after the filing (the CAS read→write instant can do this).
  const metaPath = join(spool, "entries", `${entryKey(sid)}.meta.json`);
  writeFileSync(metaPath, `${JSON.stringify({ ...filed, capture_lane: "hook" }, null, 2)}\n`);

  const later = recordingServer();
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: later.fetchImpl, now: 2 });
  assert.equal(
    later.sessions.get(sid)?.capture_lane,
    "hook",
    "a lane arriving after the filing was skipped — it is stranded",
  );
  assert.equal(readSpoolEntry(spool, sid)?.filed_lane, "hook");
});

test("a PYTHON-written null filed_lane still skips (the legs share one spool dir)", async () => {
  // The two legs read each other's meta. JSON has no `undefined`, so before the
  // fix the Python leg stored `"filed_lane": null` for a lane-less filing while
  // this leg omits the key — and this leg's strict `===` read `null !==
  // undefined`, bypassed the skip, and re-POSTed an entry Python had already
  // filed (a cross-leg amplification). Python now OMITS the key; this pins the
  // read-side tolerance that makes a Python-written `null` safe either way.
  const spool = tmpSpool();
  const sid = "sess-python-null";
  const turns = [{ role: "user" as const, content: "same" }];
  writeSpoolEntry(spool, snapshot(sid, turns));

  const first = recordingServer();
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: first.fetchImpl, now: 1 });
  const filed = readSpoolEntry(spool, sid)!;
  assert.equal(filed.filed_key, filed.capture_key, "not filed at all");
  const metaPath = join(spool, "entries", `${entryKey(sid)}.meta.json`);
  writeFileSync(metaPath, `${JSON.stringify({ ...filed, filed_lane: null }, null, 2)}\n`);
  assert.equal(readSpoolEntry(spool, sid)?.filed_lane, null, "fixture must carry null");

  const second = recordingServer();
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: second.fetchImpl, now: 2 });
  assert.equal(second.sessions.size, 0, "a Python-filed lane-less entry was re-POSTed");
  assert.equal(summary.skipped, 1);
});

test("an empty STORED lane is filled in (truthy, not nullish)", () => {
  // The only input that distinguishes `||` from `??`: a stored EMPTY lane with a
  // truthy new one. `"" || "hook"` -> "hook"; `??` would omit the key. An empty
  // lane is reachable only from a crafted/corrupt meta, written here directly.
  const spool = tmpSpool();
  const sid = "sess-empty-stored";
  writeSpoolEntry(spool, snapshot(sid, [{ role: "user", content: "a" }]));
  const crafted = readSpoolEntry(spool, sid)!;
  const metaPath = join(spool, "entries", `${entryKey(sid)}.meta.json`);
  writeFileSync(metaPath, `${JSON.stringify({ ...crafted, capture_lane: "" }, null, 2)}\n`);
  assert.equal(readSpoolEntry(spool, sid)?.capture_lane, "");

  writeSpoolEntry(spool, {
    ...snapshot(sid, [
      { role: "user", content: "a" },
      { role: "assistant", content: "b" },
    ]),
    captureLane: "hook",
  });
  assert.equal(
    readSpoolEntry(spool, sid)?.capture_lane,
    "hook",
    "an empty stored lane was not filled in — nullish semantics, not truthy",
  );
});

test("a later store_sync snapshot cannot DOWNGRADE a stored hook lane", () => {
  // Pins the pinned monotone contract (the delivery lane is monotone) plus
  // first-writer-wins parity with the server's coalesce: the spool's lane is
  // what gets DELIVERED, so a later snapshot may only FILL IN an absent lane.
  const spool = tmpSpool();
  writeSpoolEntry(spool, {
    ...snapshot("sess-mono", [{ role: "user", content: "a" }]),
    captureLane: "hook",
  });
  writeSpoolEntry(spool, {
    ...snapshot("sess-mono", [
      { role: "user", content: "a" },
      { role: "assistant", content: "b" },
    ]),
    captureLane: "store_sync",
  });
  assert.equal(
    readSpoolEntry(spool, "sess-mono")?.capture_lane,
    "hook",
    "a store_sync snapshot downgraded a stored hook lane",
  );
});

test("a lane-FUL identical re-snapshot is a no-op (upgrade is not a rewrite rule)", () => {
  // Pins the `!prior?.capture_lane` half of `laneUpgrade`. Without it EVERY
  // identical re-snapshot would rewrite the meta and clear `filed_key`, so the
  // same conversation would be re-POSTed at turn cadence — the amplification
  // the dedup guard exists to prevent.
  const spool = tmpSpool();
  const turns = [{ role: "user" as const, content: "same" }];
  writeSpoolEntry(spool, { ...snapshot("sess-noop", turns), captureLane: "hook" });
  const res = writeSpoolEntry(spool, { ...snapshot("sess-noop", turns), captureLane: "hook" });
  assert.equal(res.written, false, "a lane-ful identical re-snapshot must be a no-op");
  assert.equal(readSpoolEntry(spool, "sess-noop")?.capture_lane, "hook");
});

test("an empty-string lane carries the stored lane forward (TRUTHY, not nullish)", () => {
  // Pins the `||` (NOT `??`). `"" ?? prior` resolves to `""`, the spread then
  // omits the key, and this leg ERASES a lane its Python twin preserves — and
  // the legs share one spool directory.
  const spool = tmpSpool();
  writeSpoolEntry(spool, {
    ...snapshot("sess-empty", [{ role: "user", content: "hi" }]),
    captureLane: "hook",
  });
  writeSpoolEntry(spool, {
    ...snapshot("sess-empty", [
      { role: "user", content: "hi" },
      { role: "assistant", content: "grown" },
    ]),
    captureLane: "",
  });
  assert.equal(readSpoolEntry(spool, "sess-empty")?.capture_lane, "hook");
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

test("#8531: a quarantined orphan is COUNTED under the byte ceiling, not invisible to it", () => {
  // The `.orphan` turn log has no meta, so it is absent from the meta scan AND
  // from `spoolEntryBytes`: without explicit accounting it is never counted and
  // never pruned, and the spool grows past `maxTotalBytes` without bound — one
  // full transcript per corrupt meta (#8531 review).
  //
  // MUTATION THAT REDS THIS: drop the orphan branch from `pruneSpool` → the
  // orphan survives every byte ceiling and no `spool_total_bytes_exceeded` line
  // names it.
  const spool = tmpSpool();
  const dir = join(spool, "entries");
  const sid = "orphan-bounded";
  writeSpoolEntry(spool, snapshot(sid));
  const log = join(dir, `${entryKey(sid)}.turns.jsonl`);
  writeFileSync(log, "z".repeat(4096));
  writeFileSync(join(dir, `${entryKey(sid)}.meta.json`), "{not json");

  const { metas } = listSpoolEntries(spool);
  assert.equal(metas.length, 0, "the corrupt meta is quarantined, not listed");
  const orphan = `${log}.orphan`;
  assert.equal(readFileSync(orphan, "utf8").length, 4096, "the transcript is quarantined");

  const keep = "orphan-keep";
  writeSpoolEntry(spool, snapshot(keep));

  // Generous ceiling: the orphan survives (no over-eager delete)…
  assert.equal(
    pruneSpool(spool, keep, { maxEntries: 1000, maxTotalBytes: 1 << 30, maxEntryBytes: 1 << 20 })
      .length,
    0,
  );
  assert.ok(existsSync(orphan), "an orphan under the ceiling must survive");

  // …but, with the orphan COUNTED in the total, a ceiling just over the live
  // entry's own size must reap it and RECORD the loss (never a silent drop).
  const ceiling = spoolEntryBytes(spool, keep) + 1024;
  pruneSpool(spool, keep, { maxEntries: 1000, maxTotalBytes: ceiling, maxEntryBytes: 1 << 20 });
  assert.equal(existsSync(orphan), false, "the orphan must be reaped under the byte ceiling");
  assert.ok(
    readDiscards(spool).some(
      (d) => d.reason === "spool_total_bytes_exceeded" && d.session_id.startsWith("orphan:"),
    ),
    "the eviction must be recorded",
  );
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

test("a permanent 4xx is recorded terminal and KEEPS the transcript (never unlinked)", async () => {
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-400"));
  const { fetchImpl, calls } = statusFetch(400, "bad payload");

  // MUTATION THAT REDS THIS: route a permanent verdict to `discardEntry`
  // (record + UNLINK) → `readSpoolEntry` is undefined and the only copy is
  // GONE. `classifyFailure`'s verdict is deliberately unchanged (#7708).
  const first = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1_000 });
  assert.equal(first.discarded.length, 1);
  assert.equal(first.discarded[0].reason, DISCARD_HTTP_REJECTED);
  const meta = readSpoolEntry(spool, "sess-400");
  assert.ok(meta, "a permanent refusal must NOT unlink the transcript");
  assert.ok(meta.terminal_failure, "the entry must be marked terminal");
  assert.deepEqual(readSpoolTurns(spool, "sess-400"), SPOOL_TURNS,
    "the turn log must survive verbatim");
  assert.equal(readDiscards(spool).length, 1, "the refusal is observable on disk");

  // A later opportunity must NOT retry what was permanently rejected.
  const second = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 10 ** 12 });
  assert.equal(second.attempted, 0);
  assert.equal(second.terminal, 1, "a terminal entry must be counted, not silently skipped");
  assert.equal(calls.length, 1, "exactly one POST, never a retry loop");
  // ...and a byte-IDENTICAL re-snapshot must not resurrect the retry: the dedup
  // early-return does not rebuild the meta, so the marker survives.
  writeSpoolEntry(spool, snapshot("sess-400"));
  assert.ok(readSpoolEntry(spool, "sess-400")?.terminal_failure,
    "a byte-identical re-snapshot cleared the terminal marker");
  const third = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 10 ** 13 });
  assert.equal(third.attempted, 0);
  assert.equal(calls.length, 1);
});

test("#7708: a 422 does not unlink the only copy of the transcript", async () => {
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-422"));
  const { fetchImpl, calls } = statusFetch(422, 'invalid capture_lane "bogus"');
  const first = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1_000 });
  assert.equal(first.discarded.length, 1);
  assert.equal(first.discarded[0].reason, DISCARD_HTTP_REJECTED);
  assert.ok(readSpoolEntry(spool, "sess-422"), "a 422 UNLINKED the transcript");
  const second = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 10 ** 12 });
  assert.equal(second.terminal, 1);
  assert.equal(calls.length, 1, "a terminal entry was re-POSTed");
});

test("#8531: a non-object terminal_failure marker does not strand the entry", async () => {
  // The marker is read by SHAPE, not truthiness: a hand-edited / foreign /
  // corrupt meta can carry `1`, `true`, `"yes"` or `[]`. Read by truthiness
  // alone such an entry is counted terminal on EVERY flush — never retried,
  // silent except in `summary.terminal` (#8531 review).
  //
  // MUTATION THAT REDS THIS: restore `if (meta.terminal_failure)` → terminal=1,
  // zero calls, the capture stranded.
  const spool = tmpSpool();
  const sid = "junk-marker";
  writeSpoolEntry(spool, snapshot(sid));
  const metaPath = join(spool, "entries", `${entryKey(sid)}.meta.json`);
  const meta = JSON.parse(readFileSync(metaPath, "utf8")) as Record<string, unknown>;
  meta.terminal_failure = 1;
  writeFileSync(metaPath, JSON.stringify(meta));

  const { fetchImpl, calls } = statusFetch(200);
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1_000 });
  assert.equal(summary.terminal, 0, "a junk marker must not read as terminal");
  assert.equal(summary.attempted, 1);
  assert.equal(calls.length, 1, "the entry must be retried, not stranded");
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

test("a corrupt meta QUARANTINES its intact turn log (never unlinks)", () => {
  // #7708: the transcript has nothing to do with a corrupt `meta.json` and is
  // the only copy, so deleting it turns a bad meta into permanent data loss.
  // The corrupt meta is removed (the ledger line is its evidence); the turn log
  // is moved aside as `*.orphan` — recoverable, not deleted.
  //
  // MUTATION THAT REDS THIS: unlink the turn log with the meta.
  const spool = tmpSpool();
  mkdirSync(join(spool, "entries"), { recursive: true });
  writeFileSync(join(spool, "entries", "dead.meta.json"), "{not json");
  const turns = '{"role":"user","content":"keep me"}\n';
  writeFileSync(join(spool, "entries", "dead.turns.jsonl"), turns);

  const { metas, discards } = listSpoolEntries(spool);
  assert.equal(metas.length, 0);
  assert.equal(discards.length, 1);
  assert.equal(discards[0].reason, "corrupt_entry");
  assert.equal(existsSync(join(spool, "entries", "dead.turns.jsonl")), false,
    "the turn log must no longer be addressable as the live log");
  assert.equal(readFileSync(join(spool, "entries", "dead.turns.jsonl.orphan"), "utf8"), turns,
    "the intact transcript must be QUARANTINED, not deleted");
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

test("#8531: the terminal operator message names the supported retry verb", async () => {
  // The Pi leg's only operator surface for a terminal entry must name the exit
  // (`tortoise session drain --retry-terminal`), as the Python drain does —
  // otherwise the message reports a dead end (#8531 review).
  //
  // MUTATION THAT REDS THIS: print `(kept, not retried)` alone → no line names
  // the verb an operator has to run.
  const spool = tmpSpool();
  const sid = "terminal-msg";
  writeSpoolEntry(spool, snapshot(sid));
  const metaPath = join(spool, "entries", `${entryKey(sid)}.meta.json`);
  const meta = JSON.parse(readFileSync(metaPath, "utf8")) as Record<string, unknown>;
  meta.terminal_failure = { reason: "http_rejected", detail: "422", at: "now" };
  writeFileSync(metaPath, JSON.stringify(meta));

  const { fetchImpl } = statusFetch(200);
  const pi = mockPi();
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl, spool));

  const lines: string[] = [];
  const original = console.log;
  console.log = (...args: unknown[]) => {
    lines.push(args.map(String).join(" "));
  };
  try {
    await pi.handlers.session_start(
      undefined,
      mockCtx({ entries: [], sessionId: "live" }) as never,
    );
    await settle();
  } finally {
    console.log = original;
  }
  assert.ok(
    lines.some((line) => line.includes("terminal 1") && line.includes("--retry-terminal")),
    `the terminal line must name the retry verb: ${JSON.stringify(lines)}`,
  );
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

test("an unreadable turn log is RECORDED and KEPT, not thrown out of the flush loop", async () => {
  // `readFileSync` used to escape `readSpoolTurns`: one entry whose log was a
  // DIRECTORY (or EACCES / ELOOP) aborted the WHOLE drain loop, so every
  // session sorted after it was never filed — permanently, with no ledger line.
  // It must be recorded and, since the path holds something, KEPT (a read
  // failure must never unlink the only copy — #7708).
  //
  // MUTATION THAT REDS THIS: drop the try/catch around `readFileSync` in
  // `readSpoolTurns` (or guard with `existsSync` only); or route a byte-bearing
  // log to `discardEntry` instead of `quarantineEntry`.
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
  assert.deepEqual(summary.discarded.map((d) => d.reason), ["transcript_torn"],
    "the unreadable entry must be recorded, never silently dropped");
  assert.equal(readDiscards(spool).at(-1)?.reason, "transcript_torn");
  assert.ok(existsSync(broken), "a byte-bearing log must NOT be unlinked");
  assert.ok(readSpoolEntry(spool, "aaa-broken")?.terminal_failure,
    "the kept entry must be terminal so the drain does not loop on it");
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

// ── #7708: ONE lane normalisation, every site ─────────────────────────────
//
// Site map, one discriminating test each; REMOVING the normaliser at a site
// reds that site's test:
//   1 WRITE          writer drops / heals a lane
//   2 DRAIN payload  buildCapturePayload
//   3 SKIP clause    flushSpool skip
//   4 SUCCESS CAS    flushSpool CAS
//   5 post-POST      flushSpool transient already-filed check
// The TS set is pinned to the server + Python sets by a Python node-driver
// test (tests/test_capture_lane_3516.py).

/** Write a `capture_lane` straight into the meta file (a crafted/foreign spool). */
function craftLane(spool: string, sid: string, lane: unknown): void {
  const crafted = readSpoolEntry(spool, sid)!;
  const metaPath = join(spool, "entries", `${entryKey(sid)}.meta.json`);
  writeFileSync(metaPath, `${JSON.stringify({ ...crafted, capture_lane: lane }, null, 2)}\n`);
}

test("the lane vocabulary is closed and normalises to undefined", () => {
  assert.deepEqual([...CAPTURE_LANES].sort(), ["hook", "store_sync"]);
  assert.equal(normaliseLane("hook"), "hook");
  assert.equal(normaliseLane("store_sync"), "store_sync");
  for (const bad of ["bogus", "recorder", "", "Hook", 0, false, null, undefined, [], ["hook"]]) {
    assert.equal(normaliseLane(bad), undefined, String(bad));
  }
});

test("#7708 site 1: the writer never stores an unrecognised lane", () => {
  const spool = tmpSpool();
  // MUTATION THAT REDS THIS: drop `normaliseLane` from the writer → "bogus" is
  // stored and later posted verbatim (the 422 the issue is about).
  writeSpoolEntry(spool, { ...snapshot("sess-bad-lane"), captureLane: "bogus" });
  assert.equal(readSpoolEntry(spool, "sess-bad-lane")?.capture_lane, undefined,
    "an unrecognised lane reached the stored meta");
  // positive control: a recognised lane IS stored.
  writeSpoolEntry(spool, { ...snapshot("sess-good-lane"), captureLane: "hook" });
  assert.equal(readSpoolEntry(spool, "sess-good-lane")?.capture_lane, "hook");
});

test("#7708 site 1: a stored foreign lane is healed by a valid snapshot", () => {
  const spool = tmpSpool();
  const sid = "sess-heal";
  const turns = [{ role: "user" as const, content: "same" }];
  writeSpoolEntry(spool, snapshot(sid, turns));
  craftLane(spool, sid, "bogus");
  // MUTATION THAT REDS THIS: resolve the RAW prior lane → it wins the truthy
  // `||` and `laneUpgrade` stays false, so the dedup early-return strands the
  // valid lane forever.
  const res = writeSpoolEntry(spool, { ...snapshot(sid, turns), captureLane: "hook" });
  assert.equal(res.written, true, "the dedup early-return stranded the valid lane");
  assert.equal(readSpoolEntry(spool, sid)?.capture_lane, "hook");
});

test("#7708 site 2: the drain never puts an unrecognised lane on the wire", async () => {
  const spool = tmpSpool();
  const sid = "sess-bad-wire";
  writeSpoolEntry(spool, snapshot(sid));
  craftLane(spool, sid, "bogus");
  const server = recordingServer();
  // MUTATION THAT REDS THIS: drop `normaliseLane` from `buildCapturePayload`
  // → the wire carries "bogus" and the server 422s.
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  assert.equal(server.posts(), 1);
  assert.equal(server.sessions.get(sid)?.capture_lane, undefined,
    "an unrecognised lane reached the wire");
});

test("#7708 site 3: a filed entry with a falsy lane is skipped, not re-POSTed", async () => {
  const spool = tmpSpool();
  const sid = "sess-falsy-skip";
  writeSpoolEntry(spool, snapshot(sid));
  const server = recordingServer();
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  assert.equal(server.posts(), 1);
  assert.ok(readSpoolEntry(spool, sid)?.filed_key);
  craftLane(spool, sid, ""); // filed_lane stays absent (a lane-less filing)
  // MUTATION THAT REDS THIS: raw `undefined === ""` is FALSE → the entry
  // re-POSTs its whole transcript on EVERY flush (#4714).
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 2 });
  assert.equal(summary.skipped, 1);
  assert.equal(server.posts(), 1, "the entry was re-POSTed");
});

test("#7708 site 4: a falsy stored lane is stamped filed on the first drain", async () => {
  const spool = tmpSpool();
  const sid = "sess-falsy-cas";
  writeSpoolEntry(spool, snapshot(sid));
  craftLane(spool, sid, "");
  const server = recordingServer();
  // MUTATION THAT REDS THIS: raw `"" === undefined` is FALSE in the CAS →
  // `filed_key` is never stamped and the entry re-POSTs every drain.
  await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 1 });
  assert.equal(server.posts(), 1);
  assert.equal(server.sessions.get(sid)?.capture_lane, undefined);
  assert.ok(readSpoolEntry(spool, sid)?.filed_key,
    "the CAS did not stamp filed_key for a falsy lane (re-POST loop)");
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl: server.fetchImpl, now: 2 });
  assert.equal(summary.skipped, 1);
  assert.equal(server.posts(), 1, "the entry was re-POSTed");
});

test("#7708 site 5: the transient already-filed check normalises the lane", async () => {
  const spool = tmpSpool();
  const sid = "sess-post-post";
  writeSpoolEntry(spool, snapshot(sid));
  craftLane(spool, sid, "");
  const metaPath = join(spool, "entries", `${entryKey(sid)}.meta.json`);
  const current = readSpoolEntry(spool, sid)!;
  // A concurrent flush files the exact content while our POST is in flight.
  const concurrent = { ...current, filed_key: current.capture_key };
  const fetchImpl = (async () => {
    writeFileSync(metaPath, `${JSON.stringify(concurrent, null, 2)}\n`);
    return { ok: false, status: 503, json: async () => ({}) };
  }) as never;
  // MUTATION THAT REDS THIS: the raw check (`undefined === ""`) is false → the
  // backoff is re-armed (deferred) instead of skipping an already-filed entry.
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1 });
  assert.equal(summary.skipped, 1);
  assert.equal(summary.deferred, 0);
});

test("#7708: quarantineEntry records FIRST and keeps the entry (no unlink)", () => {
  const spool = tmpSpool();
  const sid = "sess-quarantine";
  writeSpoolEntry(spool, snapshot(sid));
  const order: string[] = [];
  const meta = readSpoolEntry(spool, sid)!;
  quarantineEntry(spool, meta, DISCARD_HTTP_REJECTED, "HTTP 422: bad lane", 1_000, {
    record: (dir, rec) => {
      order.push("record");
      return { at: "", ...rec };
    },
    read: (dir, sessionId) => {
      order.push("read");
      return readSpoolEntry(dir, sessionId);
    },
    write: (dir, m) => {
      order.push("write");
      const p = join(dir, "entries", `${entryKey(m.session_id)}.meta.json`);
      writeFileSync(p, `${JSON.stringify(m, null, 2)}\n`);
    },
  });
  assert.deepEqual(order, ["record", "read", "write"], "record must precede the mark");
  assert.ok(readSpoolEntry(spool, sid), "quarantine must keep the entry");
});

test("#8531: buildCapturePayload caps the server-bounded attribution fields at the wire", () => {
  // `machine_id` ≤256 and `model` ≤128 are SERVER caps; exceeding either is a
  // 422, which `flushSpool` classifies PERMANENT → the entry goes terminal. The
  // WRITER bounds both, but the DRAIN forwards the on-disk meta, so a
  // hand-edited / foreign / corrupt `meta.json` (the PR's own threat model)
  // could strand the capture (#8531 review).
  //
  // MUTATION THAT REDS THIS: forward `args.machineId` / `args.model` verbatim.
  const over = buildCapturePayload({
    sessionId: "s",
    turns: [{ role: "user", content: "x" }],
    source: "t",
    machineId: "m".repeat(257),
    model: "x".repeat(200),
  });
  assert.equal((over.machine_id as string).length, 256);
  assert.equal((over.model as string).length, 128);

  // POSITIVE CONTROL: a legal value reaches the wire verbatim, so the cap
  // cannot pass by a leg that simply stopped forwarding either field.
  const legal = buildCapturePayload({
    sessionId: "s",
    turns: [{ role: "user", content: "x" }],
    source: "t",
    machineId: "deadbeef",
    model: "deepseek/deepseek-v4-flash",
  });
  assert.equal(legal.machine_id, "deadbeef");
  assert.equal(legal.model, "deepseek/deepseek-v4-flash");

  // A NON-STRING (a foreign meta) is OMITTED, never a crash or a verbatim post.
  const junk = buildCapturePayload({
    sessionId: "s",
    turns: [{ role: "user", content: "x" }],
    source: "t",
    machineId: 1 as unknown as string,
    model: ["x"] as unknown as string,
  });
  assert.equal(junk.machine_id, undefined);
  assert.equal("model" in junk, false);
});

test("#8531: an empty role is not a turn (the writers' contract on both legs)", () => {
  // Neither leg's writer ever emits an empty role (`extractTurns` requires
  // user/assistant; Python filters to the same pair), so `{"role":"",...}` is a
  // malformed line. Python used to file it while this leg skipped it and routed
  // the log to `transcript_torn` — the one place the legs disagreed about the
  // SAME bytes. Python now applies the same non-empty predicate.
  //
  // MUTATION THAT REDS THIS: accept an empty role → the malformed line becomes a
  // filed turn.
  const spool = tmpSpool();
  const sid = "empty-role";
  const entries = join(spool, "entries");
  mkdirSync(entries, { recursive: true });
  const log = join(entries, `${entryKey(sid)}.turns.jsonl`);
  writeFileSync(log, '{"role":"","content":"ghost"}\n');
  assert.deepEqual(readSpoolTurns(spool, sid), []);

  // POSITIVE CONTROL: a NON-EMPTY non-conversational role is still returned —
  // the reader is not a role-VALUE filter. Only EMPTINESS is refused.
  writeFileSync(log, '{"role":"system","content":"policy"}\n');
  assert.deepEqual(readSpoolTurns(spool, sid), [{ role: "system", content: "policy" }]);
});

// ── #2100: the spool can never make a box unusable ────────────────────────
//
// Measured on agent-infra#2100: one cloud cell's spool reached 3.3 MB /
// 320 quota-deferred entries, and EVERY lane on that box produced 259–262 B of
// banner-only output and nothing else — while two sibling boxes with the same
// harness ran lanes normally. Clearing the spool did not hold (it regrew to
// 136 KB in one session). The four defects were: (1) the `session_start` drain
// ran its synchronous spool scan on the tick Pi awaits and then attempted every
// ready entry, (2) a 402 quota refusal was a silent per-item backoff with no
// named state, (3) nothing reported the state per box (an operator saw a dead
// lane, not a blocked one), (4) — already true, now pinned — the spool's count
// bound is what keeps an exhausted quota from consuming unbounded disk.

/** Run one `session_start` on a fresh extension instance, returning its stdout lines. */
async function sessionStartLines(
  spool: string,
  fetchImpl: unknown,
  env: Record<string, string | undefined> = TEST_ENV,
): Promise<{ lines: string[]; calls: string[] }> {
  const calls: string[] = [];
  const counting = async (url: string, init?: Record<string, unknown>) => {
    calls.push(url);
    return (fetchImpl as (u: string, i?: Record<string, unknown>) => Promise<unknown>)(url, init);
  };
  const pi = mockPi();
  tortoiseCapture(pi as never, {
    fetchImpl: counting as never,
    env,
    configPath: NO_CONFIG_PATH,
    spoolDir: spool,
  });
  const lines: string[] = [];
  const original = console.log;
  console.log = (...args: unknown[]) => {
    lines.push(args.map(String).join(" "));
  };
  try {
    await pi.handlers.session_start(undefined, mockCtx({ entries: [], sessionId: "live" }) as never);
    await settle();
  } finally {
    console.log = original;
  }
  return { lines, calls };
}

test("#2100: session_start returns BEFORE the spool is touched (a queued spool cannot block turn start)", async () => {
  // THE measured failure: 320 queued entries between a lane and its first model
  // call. The spool scan + turn reads run synchronously inside an `async`
  // function up to its first `await`, so "fire-and-forget" alone was NOT enough
  // — the handler Pi awaits still paid for the scan.
  //
  // MUTATION THAT REDS THIS: call `drain(...)` directly in the handler (or
  // `await` it) instead of `setImmediate(() => …)` → a `capture:` health line is
  // already on stdout when the handler returns, which is the proof the spool was
  // read on the handler's own tick.
  const spool = tmpSpool();
  for (let i = 0; i < 30; i += 1) writeSpoolEntry(spool, snapshot(`sess-q${i}`));
  const pi = mockPi();
  // A BLACKHOLED endpoint: every POST hangs forever, so nothing can be filed
  // and the pass cannot finish. If the handler waited on the drain it would
  // never return.
  const hanging = () => new Promise(() => {});
  tortoiseCapture(pi as never, hermeticDeps(hanging, spool));

  const lines: string[] = [];
  const original = console.log;
  console.log = (...args: unknown[]) => {
    lines.push(args.map(String).join(" "));
  };
  let returned = false;
  try {
    const handler = Promise.resolve(
      pi.handlers.session_start(undefined, mockCtx({ entries: [], sessionId: "live" }) as never),
    ).then(() => {
      returned = true;
    });
    await Promise.race([handler, new Promise((resolve) => setTimeout(resolve, 200))]);
    assert.equal(returned, true, "session_start must return without waiting on the spool");
    assert.equal(
      lines.filter((l) => l.includes("capture:")).length,
      0,
      "no spool scan may happen on the handler's tick — the health line comes later",
    );
    await settle();
  } finally {
    console.log = original;
  }
  // The spool is untouched: 30 entries still queued, none filed, none dropped.
  const metas = listSpoolEntries(spool).metas;
  assert.equal(metas.length, 30, "a bounded pass loses nothing");
  assert.ok(metas.every((m) => m.filed_key === undefined));
  assert.ok(lines.some((l) => l.includes("capture: DRAINING (30 queued)")), JSON.stringify(lines));
});

test("#2100: one drain pass is bounded — the attempt cap leaves the rest queued, not dropped", async () => {
  // The pass must stop; the entries it never reached must stay fileable. This is
  // the difference between "bounded" and "bounded and lossy".
  //
  // MUTATION THAT REDS THIS: ignore `maxAttempts` → `attempted` becomes all 20.
  const spool = tmpSpool();
  const N = 20;
  for (let i = 0; i < N; i += 1) {
    writeSpoolEntry(spool, snapshot(`sess-b${String(i).padStart(2, "0")}`));
  }
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  const first = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1, maxAttempts: 5 });

  assert.equal(first.attempted, 5, "the cap binds");
  assert.equal(first.remaining, N - 5, "everything unreached is COUNTED, not silently ignored");
  assert.equal(first.stopped, "attempts");
  assert.equal(calls.length, 5, "no more than the cap left the box");
  assert.equal(listSpoolEntries(spool).metas.length, N, "nothing was dropped to satisfy the cap");

  // …and the next opportunity continues where this one stopped (the entries the
  // first pass filed are skipped, the next five are attempted).
  const second = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1, maxAttempts: 5 });
  assert.equal(second.attempted, 5);
  assert.equal(second.remaining, N - 10);
});

test("#2100: one drain pass is bounded in TIME too — the budget binds before the cap", async () => {
  // A pass of 8 slow POSTs is still ~80 s against a blackholed endpoint. The
  // clock stops the pass between entries (never mid-POST, so a completed
  // attempt's outcome is always recorded rather than lost).
  //
  // MUTATION THAT REDS THIS: drop the budget check → all 6 entries are attempted.
  const spool = tmpSpool();
  for (let i = 0; i < 6; i += 1) writeSpoolEntry(spool, snapshot(`sess-t${i}`));
  const { fetchImpl } = mockFetch([{ ok: true }]);
  let reads = 0;
  const summary = await flushSpool(TEST_CFG, {
    dir: spool,
    fetchImpl,
    now: 1,
    maxAttempts: 100,
    budgetMs: 5_000,
    // `0` at the start of the pass, `100` for the first entry's check (inside the
    // budget), then far past it — so entry 1 is attempted and entry 2 is not.
    budgetClock: () => {
      reads += 1;
      return reads === 1 ? 0 : reads === 2 ? 100 : 10_000;
    },
  });
  assert.equal(summary.attempted, 1, "the first entry is attempted, then the budget stops the pass");
  assert.equal(summary.stopped, "budget");
  assert.equal(summary.remaining, 5);
  assert.equal(listSpoolEntries(spool).metas.length, 6, "the unreached entries stay queued");
});

test("#2100: a 402 quota refusal is NAMED — BLOCKED, one attempt per pass, durable across processes", async () => {
  // The measured symptom was 320 entries "waiting on backoff" with nothing
  // naming WHY, so every lane looked dead rather than blocked. One refusal must
  // stop the pass (the remaining entries cannot succeed either), name the state
  // on the operator surface, and survive into the NEXT process — every lane is a
  // new process, and re-POSTing to rediscover the quota is the amplification.
  const spool = tmpSpool();
  for (let i = 0; i < 3; i += 1) writeSpoolEntry(spool, snapshot(`sess-quota${i}`));

  // (1) FIRST process: the server refuses with the machine-readable category.
  const refusing = statusFetch(402, { code: "quota_exceeded", message: "quota exceeded" });
  const first = await sessionStartLines(spool, refusing.fetchImpl);

  const posts = first.calls.filter((u) => /\/v1\/sessions$/.test(u));
  assert.equal(posts.length, 1, "ONE refusal stops the pass — the rest cannot succeed either");
  // …and the pass line must not promise the continuation a quota stop does not
  // schedule (the whole content of 90907b2d; unpinned until review cycle 3).
  //
  // MUTATION THAT REDS THIS: drop the `stopped === "quota"` branch → the line
  // says "continues in the background".
  assert.ok(
    first.lines.some((l) => l.includes("paused 2 (quota refused; not retried in this pass)")),
    `a quota stop schedules no continuation: ${JSON.stringify(first.lines)}`,
  );
  assert.ok(
    first.lines.some((l) => l.includes("capture: BLOCKED (3 queued, 402 quota)")),
    `the banner must name the quota block: ${JSON.stringify(first.lines)}`,
  );
  const recorded = readCaptureStatus(spool);
  assert.equal(recorded?.state, "blocked", "the block is durable, not just one line in a log");
  assert.equal(recorded?.status, 402);

  // (2) SECOND process, with a healthy endpoint that never answers in time: the
  // state is reported WITHOUT re-POSTing to discover it.
  const second = await sessionStartLines(spool, () => new Promise(() => {}));
  assert.ok(
    second.lines.some((l) => l.includes("capture: BLOCKED (3 queued, 402 quota)")),
    `a fresh process must report the recorded block: ${JSON.stringify(second.lines)}`,
  );

  // (3) THIRD process: the quota is back (a 2xx), which must CLEAR the marker —
  // a stale BLOCKED would be its own lie.
  const accepted = statusFetch(200);
  await sessionStartLines(spool, accepted.fetchImpl);
  assert.equal(readCaptureStatus(spool)?.state, "ok", "a 2xx clears the recorded block");
  const fourth = await sessionStartLines(spool, accepted.fetchImpl);
  assert.equal(
    fourth.lines.filter((l) => l.includes("capture: BLOCKED")).length,
    0,
    `a lifted quota must not keep reporting BLOCKED: ${JSON.stringify(fourth.lines)}`,
  );
});

test("#2100: an exhausted quota cannot grow the spool past its count bound (drop-OLDEST)", async () => {
  // The disk half of the defect. The bound already exists (`pruneSpool`); what
  // was never pinned is the QUOTA-BLOCKED steady state that #2100 measured — a
  // spool that never drains because every POST is refused. It must still be
  // bounded, and it must drop the OLDEST (the newest conversation is the one
  // worth keeping), recording every eviction.
  const spool = tmpSpool();
  const bounds = {
    maxEntries: 3,
    maxTotalBytes: 64 * 1024 * 1024,
    maxEntryBytes: 16 * 1024 * 1024,
  };
  const refusing = statusFetch(402, { code: "quota_exceeded" });
  for (let i = 0; i < 6; i += 1) {
    writeSpoolEntry(spool, snapshot(`sess-cap${i}`), bounds);
    // Every pass is refused, so the spool can never shrink on its own.
    await flushSpool(TEST_CFG, { dir: spool, fetchImpl: refusing.fetchImpl, now: 1 });
  }
  assert.equal(listSpoolEntries(spool).metas.length, 3, "the count bound holds under a dead quota");
  const kept = listSpoolEntries(spool)
    .metas.map((m) => m.session_id)
    .sort();
  assert.deepEqual(kept, ["sess-cap3", "sess-cap4", "sess-cap5"], "drop-OLDEST keeps the newest");
  assert.ok(
    readDiscards(spool).some((d) => d.reason === DISCARD_COUNT_EXCEEDED),
    "every eviction is recorded — never a silent drop",
  );
});

test("#2100: the per-box health line is present in every state", async () => {
  // The operator surface. A box whose lanes produce 259 B must be readable as
  // BLOCKED — and a healthy box must NOT read as blocked (an over-block in the
  // other direction is the same lie).
  //
  // MUTATION THAT REDS THIS: drop `logHealth()` from `session_start`, or emit the
  // line only when a flush has something to report → an idle box logs nothing.
  const empty = await sessionStartLines(tmpSpool(), statusFetch(200).fetchImpl);
  assert.ok(
    empty.lines.some((l) => l.includes("capture: OK (0 queued)")),
    `an idle box says so: ${JSON.stringify(empty.lines)}`,
  );

  const queued = tmpSpool();
  writeSpoolEntry(queued, snapshot("sess-h1"));
  const draining = await sessionStartLines(queued, () => new Promise(() => {}));
  assert.ok(
    draining.lines.some((l) => l.includes("capture: DRAINING (1 queued)")),
    `a queued-but-fine box says DRAINING: ${JSON.stringify(draining.lines)}`,
  );

  const keyless = await sessionStartLines(tmpSpool(), statusFetch(200).fetchImpl, {});
  assert.ok(
    keyless.lines.some((l) => l.includes("capture: INACTIVE (no TORTOISE_API_KEY, 0 queued)")),
    `a keyless box is named INACTIVE, not silently fine: ${JSON.stringify(keyless.lines)}`,
  );

  // The pure helper is the ONE implementation of the line, so pin its states
  // directly instead of only through the wiring.
  const dir = tmpSpool();
  writeSpoolEntry(dir, snapshot("sess-h2"));
  assert.equal(captureHealthLine(dir, { apiKey: "tt_x" }).state, "draining");
  assert.equal(captureHealthLine(dir, { apiKey: "" }).state, "inactive");

  // A HEALTH CHECK MUST NOT MUTATE WHAT IT DESCRIBES. `listSpoolEntries` (the
  // drain's scanner) REPAIRS as it reads: it records `corrupt_entry` discards
  // and unlinks the metas it cannot parse. Reusing it here would mean a line
  // printed on every session start appends a ledger record per session and
  // deletes files — a health check with side effects.
  //
  // MUTATION THAT REDS THIS: count with `listSpoolEntries(dir).metas.length` →
  // the corrupt meta is removed and a discard is recorded.
  const dirty = tmpSpool();
  writeSpoolEntry(dirty, snapshot("sess-h3"));
  const entries = join(dirty, "entries");
  mkdirSync(entries, { recursive: true });
  writeFileSync(join(entries, "corrupt.meta.json"), "{not json");
  const health = captureHealthLine(dirty, { apiKey: "tt_x" });
  assert.equal(health.queued, 2, "every meta file counts, readable or not");
  assert.ok(existsSync(join(entries, "corrupt.meta.json")), "the corrupt meta is NOT deleted");
  assert.equal(readDiscards(dirty).length, 0, "the health read records no discards");

  // A marker we cannot UNDERSTAND is not a marker: a hand-edited / foreign
  // `status.json` with no numeric status must not be able to report a block
  // nothing observed (the same rule the file applies to `terminal_failure`).
  //
  // MUTATION THAT REDS THIS: trust `state === "blocked"` alone (default the
  // status to 402) → this box reads BLOCKED for a marker we cannot read.
  const forged = tmpSpool();
  writeSpoolEntry(forged, snapshot("sess-h4"));
  writeFileSync(join(forged, SPOOL_STATUS_FILE), JSON.stringify({ state: "blocked" }));
  assert.equal(captureHealthLine(forged, { apiKey: "tt_x" }).state, "draining");
  writeFileSync(
    join(forged, SPOOL_STATUS_FILE),
    JSON.stringify({ state: "blocked", status: "402" }),
  );
  assert.equal(captureHealthLine(forged, { apiKey: "tt_x" }).state, "draining");
});

// ── #2100 (review cycle 1): the health line describes WORK, not occupancy ──

test("#2100: the health line counts PENDING work — a filed entry is not `queued`", async () => {
  // Review finding: `queued` counted every `*.meta.json`. A FILED entry is
  // KEPT (the success path stamps `filed_*`; nothing unlinks a filed capture),
  // so occupancy only ever grows on a healthy box — `OK (0 queued)` was
  // unreachable, the `n` in `BLOCKED (n queued, …)` was not the blocked
  // backlog, and the `queued === 0` self-heal could never fire.
  //
  // MUTATION THAT REDS THIS: count meta files (occupancy) → `queued` is 1 on a
  // box with nothing left to do.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-filed"));
  const { fetchImpl } = mockFetch([{ ok: true }]);
  const summary = await flushSpool(TEST_CFG, { dir: spool, fetchImpl, now: 1 });
  assert.equal(summary.filed, 1);
  assert.ok(readSpoolEntry(spool, "sess-filed")?.filed_key, "the filed entry is KEPT, not unlinked");
  assert.equal(countPendingEntries(spool), 0, "a filed entry is not pending work");

  const health = captureHealthLine(spool, { apiKey: "tt_x" });
  assert.equal(health.state, "ok");
  assert.equal(health.line, "capture: OK (0 queued)");

  // …and a stale BLOCKED marker with nothing pending is cleared on the spot.
  writeFileSync(
    join(spool, SPOOL_STATUS_FILE),
    JSON.stringify({ version: 1, state: "blocked", status: 402, updated_at: "then" }),
  );
  const healed = captureHealthLine(spool, { apiKey: "tt_x" });
  assert.equal(healed.state, "ok", "a block with nothing pending is not evidence of a block");
  assert.equal(readCaptureStatus(spool)?.state, "ok", "the marker is cleared, not just ignored");
});

test("#2100: a spool of TERMINAL entries is not a live block", () => {
  // Review finding: terminal entries are KEPT forever (#7708, never retried),
  // so with occupancy-counting a box whose refusals were all PERMANENT read
  // `BLOCKED (n queued, 402 quota)` on every lane, forever, for a 402 that no
  // longer existed. Nothing pending means nothing is blocked.
  const spool = tmpSpool();
  const sid = "sess-terminal";
  writeSpoolEntry(spool, snapshot(sid));
  const metaPath = join(spool, "entries", `${entryKey(sid)}.meta.json`);
  const meta = JSON.parse(readFileSync(metaPath, "utf8")) as Record<string, unknown>;
  meta.terminal_failure = { reason: "http_rejected", detail: "422", at: "now" };
  writeFileSync(metaPath, JSON.stringify(meta));
  writeFileSync(
    join(spool, SPOOL_STATUS_FILE),
    JSON.stringify({ version: 1, state: "blocked", status: 402, updated_at: "then" }),
  );

  assert.equal(countPendingEntries(spool), 0, "a terminal entry is never retried, so never pending");
  assert.equal(captureHealthLine(spool, { apiKey: "tt_x" }).state, "ok");
});

test("#2100: agent_end returns BEFORE the spool is touched (Pi awaits this handler too)", async () => {
  // Review finding: Pi awaits EVERY handler (`agent-session.js` → `await
  // emit('agent_end')`), and `flushSpool`'s body runs synchronously up to its
  // first `await` — the scan, the sort and the first turn read all landed on
  // the awaited tick (~23 ms measured on a 320-entry spool). The durable
  // snapshot stays inline; only the drain moves to `setImmediate`.
  //
  // MUTATION THAT REDS THIS: call `drain(...)` directly (not in `setImmediate`)
  // → the first POST is already dispatched when the handler returns.
  const spool = tmpSpool();
  for (let i = 0; i < 30; i += 1) writeSpoolEntry(spool, snapshot(`sess-ae${i}`));
  const { fetchImpl, calls } = mockFetch([{ ok: true }]);
  const pi = mockPi();
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl, spool));

  await pi.handlers.agent_end(undefined, mockCtx({ entries: PI_ENTRIES, sessionId: "live" }));
  assert.ok(existsSync(join(spool, "entries", `${entryKey("live")}.meta.json`)),
    "the snapshot is durable BEFORE the handler returns — that is what it exists for");
  assert.equal(calls.length, 0, "no drain work may run on the handler's own tick");

  await settle();
  assert.ok(calls.length > 0, "the drain still runs, in the background");
});

test("#2100: session_shutdown files the LIVE session first, not last", async () => {
  // Review finding: the shipped bound ranks oldest-first, so a shutdown pass
  // over a spool holding >= FLUSH_MAX_ATTEMPTS older entries never reached the
  // one session ONLY shutdown can file. On an ephemeral cell (the #2100 box)
  // that spool dies with the box — the bound turned "slow" into a silent loss.
  //
  // MUTATION THAT REDS THIS: drop the `onlySessionId` live pass → `live-sess`
  // is ranked last and never attempted.
  const spool = tmpSpool();
  // More pending entries than ONE bounded pass may attempt.
  const older = FLUSH_MAX_ATTEMPTS + 2;
  for (let i = 0; i < older; i += 1) writeSpoolEntry(spool, snapshot(`sess-old${i}`));
  const { fetchImpl } = mockFetch([{ ok: true }]);
  const pi = mockPi();
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl, spool));

  await pi.handlers.session_shutdown(
    { reason: "quit" },
    mockCtx({ entries: PI_ENTRIES, sessionId: "live-sess" }),
  );

  assert.ok(
    readSpoolEntry(spool, "live-sess")?.filed_key,
    "the final capture of the session that is ending must be filed, not left behind the bound",
  );
  assert.ok(readSpoolEntry(spool, "sess-old0")?.filed_key, "the bounded pass still drains the rest");
});

test("#2100: the spend cap is named as ITSELF, not as a plan quota", async () => {
  // Review finding: `/v1/sessions` has TWO 402 categories, and the server says
  // a caller must not read the spend cap as "out of plan allowance" (buying a
  // bigger plan would not lift it). The line said "quota" for any 402.
  //
  // MUTATION THAT REDS THIS: hardcode `quota` in the line → the cause is lost.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-cap1"));
  const capped = statusFetch(402, { code: COHORT_COST_CAP_CODE, message: "cohort cap" });
  const { lines } = await sessionStartLines(spool, capped.fetchImpl);

  assert.ok(
    lines.some((l) => l.includes("capture: BLOCKED (1 queued, 402 cohort_cost_cap)")),
    `the spend cap must be named as itself: ${JSON.stringify(lines)}`,
  );
  assert.equal(readCaptureStatus(spool)?.reason, COHORT_COST_CAP_CODE);
});

// ── #2100 (review cycle 2): unreadable, stale, and honest next-step text ────

test("#2100: an UNLISTABLE spool is not an empty one (and never heals the marker)", () => {
  // Review finding: `countPendingEntries` read every readdir error as "0
  // pending", so a transient EACCES/EMFILE printed `OK` AND overwrote the
  // durable `blocked` marker with `ok` — erasing the only fact the next process
  // needs, and staying erased after permissions recovered. `listSpoolEntries`
  // already separates ENOENT from other errors (`spool_unreadable`); the health
  // read must too.
  //
  // MUTATION THAT REDS THIS: `catch { return 0 }` → state becomes `ok` and the
  // marker is destroyed.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-unreadable"));
  writeFileSync(
    join(spool, SPOOL_STATUS_FILE),
    JSON.stringify({ version: 1, state: "blocked", status: 402, reason: "quota_exceeded" }),
  );
  const entries = join(spool, "entries");
  const before = readFileSync(join(spool, SPOOL_STATUS_FILE), "utf8");
  chmodSync(entries, 0o000);
  try {
    assert.equal(countPendingEntries(spool), undefined, "an unlistable spool is UNKNOWN, not 0");
    const health = captureHealthLine(spool, { apiKey: "tt_x" });
    assert.equal(health.state, "unreadable");
    assert.match(health.line, /^capture: UNREADABLE \(cannot list /);
  } finally {
    chmodSync(entries, 0o700);
  }
  assert.equal(
    readFileSync(join(spool, SPOOL_STATUS_FILE), "utf8"),
    before,
    "a health read must not rewrite the marker it cannot justify",
  );
  // …and once readable again, the real state is back (the marker was preserved).
  assert.equal(captureHealthLine(spool, { apiKey: "tt_x" }).state, "blocked");
});

test("#2100: a marker is superseded by a NEWER attempt that saw no quota refusal", async () => {
  // Review finding: the marker was only cleared when something was FILED, so a
  // box that was quota-blocked yesterday and is now ECONNREFUSED/500ing kept
  // telling its operator `402 quota` forever (`updated_at` was written and
  // never read). The marker names the LAST OBSERVED cause.
  //
  // MUTATION THAT REDS THIS: clear only on `summary.filed > 0` → the stale
  // `402 quota` survives a 500.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-stale"));
  writeFileSync(
    join(spool, SPOOL_STATUS_FILE),
    JSON.stringify({ version: 1, state: "blocked", status: 402, reason: "quota_exceeded" }),
  );
  const offline = statusFetch(500);
  const { lines } = await sessionStartLines(spool, offline.fetchImpl);

  assert.equal(readCaptureStatus(spool)?.state, "ok", "a 500 is not a quota refusal");
  // The start-of-session line is printed from the marker this pass superseded,
  // so the LAST health line must be the superseding one — a lane log whose final
  // word is a cause the drain already disproved is the misdiagnosis itself.
  const healthLines = lines.filter((l) => l.includes("capture: "));
  assert.ok(healthLines.length >= 2, `the supersede must be reported: ${JSON.stringify(lines)}`);
  assert.ok(
    !healthLines[healthLines.length - 1].includes("BLOCKED"),
    `a superseded cause must not be the last word: ${JSON.stringify(healthLines)}`,
  );
  assert.equal(readSpoolEntry(spool, "sess-stale")?.attempts, 1, "the attempt happened (and deferred)");
});

test("#2100: the paused line never promises a continuation that is not scheduled", async () => {
  // Review finding: `reportFlush` ran BEFORE the chain decision, so the LAST
  // pass of a spent chain — and every `session_shutdown` pass, where no chain
  // exists — still said "continues in the background". On an ephemeral cell that
  // is the last thing the lane logs before the spool dies with the box.
  //
  // MUTATION THAT REDS THIS: drop the `continuing` argument (report unconditionally)
  // → the final line claims a continuation.
  const spool = tmpSpool();
  // Enough to SPEND the whole chain: the last permitted pass must still have
  // work left, or it prints no `paused` clause at all and asserts nothing.
  const total = FLUSH_MAX_ATTEMPTS * FLUSH_MAX_CHAIN + 5;
  for (let i = 0; i < total; i += 1) writeSpoolEntry(spool, snapshot(`sess-chain${i}`));
  const { fetchImpl } = mockFetch([{ ok: true }]);
  const pi = mockPi();
  tortoiseCapture(pi as never, hermeticDeps(fetchImpl, spool));

  const lines: string[] = [];
  const original = console.log;
  console.log = (...args: unknown[]) => {
    lines.push(args.map(String).join(" "));
  };
  try {
    await pi.handlers.session_start(undefined, mockCtx({ entries: [], sessionId: "live" }));
    // Let the whole bound→continue→bound chain run (each hop is 250 ms).
    await new Promise((resolve) => setTimeout(resolve, 250 * (FLUSH_MAX_CHAIN + 2)));
  } finally {
    console.log = original;
  }
  const flushes = lines.filter((l) => l.includes("spool flush (session_start)"));
  assert.ok(flushes.length >= 2, `the chain must actually run: ${JSON.stringify(lines)}`);
  const last = flushes[flushes.length - 1];
  assert.ok(
    last.includes("left for the next opportunity"),
    `the last pass has no successor and must not claim one: ${last}`,
  );
  // Every earlier pass really did continue.
  for (const earlier of flushes.slice(0, -1)) {
    assert.ok(earlier.includes("continues in the background"), earlier);
  }

  // The shutdown path never chains: its line must say so too.
  const shutdownLines: string[] = [];
  const pi2 = mockPi();
  const spool2 = tmpSpool();
  for (let i = 0; i < FLUSH_MAX_ATTEMPTS + 2; i += 1) {
    writeSpoolEntry(spool2, snapshot(`sess-sd${i}`));
  }
  tortoiseCapture(pi2 as never, hermeticDeps(mockFetch([{ ok: true }]).fetchImpl, spool2));
  console.log = (...args: unknown[]) => {
    shutdownLines.push(args.map(String).join(" "));
  };
  try {
    await pi2.handlers.session_shutdown({ reason: "quit" }, mockCtx({ entries: [], sessionId: "live" }));
  } finally {
    console.log = original;
  }
  const sd = shutdownLines.filter((l) => l.includes("spool flush (session_shutdown)"));
  assert.ok(sd.length >= 1, JSON.stringify(shutdownLines));
  for (const line of sd) {
    assert.ok(
      !line.includes("continues in the background"),
      `shutdown has no continuation to promise: ${line}`,
    );
  }
});

test("#2100: a KEYLESS box still shows its backlog (it can never drain)", () => {
  // Review finding: the INACTIVE branch returned before `queued` was mentioned,
  // so a box with no API key — precisely the box whose spool grows forever —
  // reported no backlog at all.
  //
  // MUTATION THAT REDS THIS: return the count-less INACTIVE line.
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-nokey1"));
  writeSpoolEntry(spool, snapshot("sess-nokey2"));
  const health = captureHealthLine(spool, { apiKey: "" });
  assert.equal(health.state, "inactive");
  assert.equal(health.queued, 2);
  assert.equal(health.line, "capture: INACTIVE (no TORTOISE_API_KEY, 2 queued)");
});

// ── #2100 (review cycle 3): pin the two branches the guards rest on ────────

test("#2100: a pass that ATTEMPTS nothing must not clear the block", () => {
  // Review finding: the `attempted > 0` guard was unpinned — mutating it to
  // `>= 0` kept the suite green, and that guard is the ONLY thing between the
  // measured #2100 box (320 entries, ALL inside their backoff window, so a pass
  // attempts zero) and a self-erasing marker: the next lane would print
  // `capture: DRAINING (320 queued)` instead of the named quota block.
  //
  // MUTATION THAT REDS THIS: `summary.attempted >= 0` (clear on every pass).
  const spool = tmpSpool();
  const sid = "sess-waiting";
  writeSpoolEntry(spool, snapshot(sid));
  // Everything is in its backoff window: nothing is attemptable this pass.
  const metaPath = join(spool, "entries", `${entryKey(sid)}.meta.json`);
  const meta = JSON.parse(readFileSync(metaPath, "utf8")) as Record<string, unknown>;
  meta.next_attempt_at_ms = Date.now() + 60_000;
  writeFileSync(metaPath, JSON.stringify(meta));
  writeFileSync(
    join(spool, SPOOL_STATUS_FILE),
    JSON.stringify({ version: 1, state: "blocked", status: 402, reason: "quota_exceeded" }),
  );

  const pi = mockPi();
  tortoiseCapture(pi as never, hermeticDeps(mockFetch([{ ok: true }]).fetchImpl, spool));
  return (async () => {
    await pi.handlers.session_start(undefined, mockCtx({ entries: [], sessionId: "live" }));
    await settle();
    assert.equal(
      readCaptureStatus(spool)?.state,
      "blocked",
      "no attempt means no new evidence — the block stands",
    );
    assert.equal(captureHealthLine(spool, { apiKey: "tt_x" }).state, "blocked");
  })();
});

test("#2100: a bare 402 with no machine-readable code is still a quota block", async () => {
  // Review finding: the `status !== 402` half of `quotaRefusal` was unpinned.
  // It is what names the block for a 402 whose body carries no `detail.code` —
  // a proxy/gateway 402, an older server, or a non-JSON body — which is exactly
  // the acceptance's `402 quota` string. "Cleanup" to `if (code === undefined)
  // return undefined` would record NO block at all for those.
  //
  // MUTATION THAT REDS THIS: `if (code === undefined) return undefined;`
  const spool = tmpSpool();
  writeSpoolEntry(spool, snapshot("sess-bare402"));
  const bare = statusFetch(402, "quota"); // a plain-string detail, no code
  const { lines } = await sessionStartLines(spool, bare.fetchImpl);

  assert.ok(
    lines.some((l) => l.includes("capture: BLOCKED (1 queued, 402 quota)")),
    `a code-less 402 is still the plan's quota: ${JSON.stringify(lines)}`,
  );
  const recorded = readCaptureStatus(spool);
  assert.equal(recorded?.state, "blocked");
  assert.equal(recorded?.status, 402);
});

test("#2100: the SHIPPED ceilings are finite and identical on both legs", () => {
  // The drop-oldest test above supplies its OWN bounds, so both shipped
  // constants could become Infinity and every suite would stay green — the
  // mechanism pinned, the BOUND unpinned. The two legs share one spool
  // directory, so they must also agree on the numbers: a divergence would evict
  // different captures depending on which harness wrote last, and a test on the
  // other leg (`tests/test_capture_spool.py`, which imports these two values
  // through a `node --experimental-strip-types` driver) pins the same pair.
  //
  // The values are deliberately NOT lowered to the size of the measured
  // incident (#2100): a blocked box's captures are the user's data, and
  // evicting them to make a count look better destroys exactly what the retry
  // policy exists to keep. The defect was the INLINE flush, not the ceiling.
  //
  // MUTATION THAT REDS THIS: raise either ceiling here only.
  assert.equal(SPOOL_MAX_ENTRIES, 1000);
  assert.equal(SPOOL_MAX_TOTAL_BYTES, 256 * 1024 * 1024);
});
