/**
 * #3515 — the store-sync backstop's own suite (`node --test`).
 *
 * These are the rows that make each pinned behaviour DISCRIMINATING: every
 * test here fails if the rule it names is removed. Run by
 * `tests/test_store_sync_worker.py`, which is CI-selected for a change under
 * `tortoise/pi-hooks/`.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { chmodSync, existsSync, mkdtempSync, readFileSync, statSync, writeFileSync, appendFileSync, mkdirSync, renameSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  activeBranch,
  classifyStatus,
  completeThroughCheap,
  cutAtCompaction,
  planFile,
  readCompleteLines,
  runStoreSync,
  scanSessionFile,
  windowCapturedAt,
  windowTurns,
  AUTH_STATUS,
  BACKOFF_MS,
  LANE,
  PERMANENT_STATUS,
  RETAIN_STATUS,
} from "./store-sync.ts";
import {
  backupPath,
  cursorPath,
  loadCursor,
  saveCursor,
  emptyCursor,
} from "./cursor-store.ts";

// ── helpers ───────────────────────────────────────────────────────────────

const HEADER = (id) => JSON.stringify({ type: "session", version: 3, id, timestamp: "2026-10-23T10:00:00.000Z", cwd: "/tmp/x" });

function entry(id, parentId, role, text, extra = {}) {
  return JSON.stringify({
    type: "message",
    id,
    parentId,
    timestamp: extra.timestamp ?? "2026-10-23T10:00:01.000Z",
    message: { role, content: [{ type: "text", text }] },
  });
}

/** A tree entry that carries NO turn (Pi writes these too): a window made only
 *  of these is the `no_turns` case, which must be distinguishable from "up to
 *  date" — a user-only window already yields one turn. */
function NOTE(id, parentId) {
  return JSON.stringify({
    type: "label",
    id,
    parentId,
    timestamp: "2026-10-23T10:00:01.000Z",
    label: "checkpoint",
  });
}

function sessionFixture(dir, name, lines) {
  const path = join(dir, name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(path, `${lines.join("\n")}\n`);
  return path;
}

function tmp(prefix) {
  return mkdtempSync(join(tmpdir(), prefix));
}

/** A fetch stub: records every body, answers from a script, optionally throws. */
function stubFetch(script) {
  const calls = [];
  let index = 0;
  const fn = async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, body });
    const step = script[Math.min(index, script.length - 1)];
    index += 1;
    if (step === "throw") throw new Error("socket hang up");
    return {
      ok: step >= 200 && step < 300,
      status: step,
      json: async () => ({ detail: step < 300 ? undefined : `refused ${step}` }),
    };
  };
  fn.calls = calls;
  return fn;
}

/**
 * The credential and URL EVERY `runStoreSync` call must be given (#3721).
 *
 * Without them the worker falls through to `resolveConfig()`, which reads
 * `TORTOISE_API_KEY` / `~/.pi/agent/tortoise-config.json` — i.e. the MACHINE.
 * On a developer box that has a key the rows green while asserting the
 * developer's config; on a clean CI runner the empty key short-circuits the
 * POST before the injected `fetch` is ever called (`0 !== 1`). Supplying both
 * makes every row deterministic, and the sibling suites record the same
 * remedy (`tests/test_pi_capture_hooks.py::_scrubbed_env`,
 * `tortoise-capture.test.ts`'s TEST_ENV/NO_CONFIG_PATH).
 *
 * `apiUrl` points at a closed port on purpose: if a row ever ships without a
 * stubbed `fetch`, it FAILS loudly instead of reaching the network.
 */
const HERMETIC = { apiKey: "tt_test", apiUrl: "http://127.0.0.1:1" };

const FAST = { ...HERMETIC, sleep: async () => {}, log: () => {} };

// ── piece 2/§E: partial trailing line ─────────────────────────────────────

test("a partial trailing line is NOT consumed and NOT lost", () => {
  // Real Pi writes with appendFileSync; a kill can land between two writes, so
  // the last line is a prefix of a JSON object.
  const complete = `${HEADER("sid-1")}\n${entry("a", null, "user", "hi")}\n`;
  const partial = '{"type":"message","id":"b","parentId":"a","message":{"role":"assistant","con';
  const buf = Buffer.from(complete + partial, "utf8");

  const first = readCompleteLines(buf, 0);
  assert.equal(first.lines.length, 2, "only the two complete lines are parsed");
  assert.equal(first.partial, true, "the tail is reported as partial");
  // The cursor stops BEFORE the partial line: exactly the end of the last
  // complete line. Advancing past it is the loss this test exists to forbid.
  assert.equal(first.nextOffset, Buffer.byteLength(complete, "utf8"));
  assert.ok(
    first.nextOffset < buf.length,
    "the cursor must stay behind the partial tail, never past it",
  );

  // Second pass: the line finished (Pi appended the rest).
  const finished = Buffer.from(complete + '{"type":"message","id":"b","parentId":"a","message":{"role":"assistant","content":[{"type":"text","text":"done"}]}}\n', "utf8");
  const second = readCompleteLines(finished, first.nextOffset);
  assert.equal(second.lines.length, 1, "the completed line ships on the next pass");
  assert.equal(second.partial, false);
  const parsed = JSON.parse(second.lines[0]);
  assert.equal(parsed.message.content[0].text, "done", "the turn is whole, not truncated");
});

test("no newline at all leaves the cursor exactly where it was", () => {
  const buf = Buffer.from('{"type":"session","id":"x"}', "utf8"); // no \n
  const r = readCompleteLines(buf, 0);
  assert.deepEqual(r.lines, []);
  assert.equal(r.nextOffset, 0, "a file with no complete line contributes no offset");
  assert.equal(r.partial, true);
});

test("an offset past EOF reads nothing and never moves the cursor", () => {
  const buf = Buffer.from("a\nb\n", "utf8");
  const r = readCompleteLines(buf, 4096);
  assert.deepEqual(r.lines, []);
  assert.equal(r.nextOffset, buf.length, "clamped to EOF — a stall, never a skip");
});

// ── piece 9: cursor past EOF / file replaced ──────────────────────────────

test("planFile re-anchors to the header on cursor > file_size (truncation)", () => {
  const cursor = { offset: 900, sessionId: "sid-1", dev: 1, ino: 2, headerSha: "h" };
  const plan = planFile(cursor, { size: 120, dev: 1, ino: 2 }, { headerId: "sid-1", headerSha: "h" });
  assert.equal(plan.action, "reanchor");
  assert.equal(plan.offset, 0);
  assert.equal(plan.reason, "cursor_past_eof");
});

test("planFile re-anchors when the file is REPLACED under the same path", () => {
  const cursor = { offset: 90, sessionId: "sid-1", dev: 1, ino: 2, headerSha: "h" };
  // Same id, same size, different inode: a rewrite (Pi's `_rewriteFile`
  // truncates then rewrites) or a copy over the path.
  const plan = planFile(cursor, { size: 400, dev: 1, ino: 3 }, { headerId: "sid-1", headerSha: "h" });
  assert.equal(plan.action, "reanchor");
  assert.equal(plan.reason, "file_replaced");
});

test("planFile re-anchors when the header changes under the same path", () => {
  const cursor = { offset: 90, sessionId: "sid-1", dev: 1, ino: 2, headerSha: "old" };
  const plan = planFile(cursor, { size: 400, dev: 1, ino: 2 }, { headerId: "sid-1", headerSha: "new" });
  assert.equal(plan.action, "reanchor");
  assert.equal(plan.reason, "header_changed");
});

test("planFile re-anchors when a DIFFERENT session id appears in the same path", () => {
  const cursor = { offset: 90, sessionId: "sid-1", dev: 1, ino: 2, headerSha: "h" };
  const plan = planFile(cursor, { size: 400, dev: 1, ino: 2 }, { headerId: "sid-2", headerSha: "h" });
  assert.equal(plan.action, "reanchor");
  assert.equal(plan.reason, "session_id_changed");
});

test("planFile skips only when the cursor is AT the size, else ships", () => {
  const cursor = { offset: 100, dev: 1, ino: 2, headerSha: "h" };
  assert.equal(planFile(cursor, { size: 100, dev: 1, ino: 2 }, {}).action, "skip");
  assert.equal(planFile(cursor, { size: 140, dev: 1, ino: 2 }, {}).action, "ship");
  assert.equal(planFile(undefined, { size: 140, dev: 1, ino: 2 }, {}).action, "ship");
});

// ── piece 13: the shared branch contract ──────────────────────────────────

test("activeBranch ships the LIVE chain only — an abandoned branch is excluded", () => {
  // user a -> assistant b -> user c -> assistant d   (abandoned at c)
  //      \-> user c2 -> assistant d2                (the active leaf)
  const entries = [
    JSON.parse(HEADER("sid-1")),
    JSON.parse(entry("a", null, "user", "Q1")),
    JSON.parse(entry("b", "a", "assistant", "A1")),
    JSON.parse(entry("c", "b", "user", "abandoned question")),
    JSON.parse(entry("d", "c", "assistant", "abandoned answer")),
    JSON.parse(entry("c2", "b", "user", "live question")),
    JSON.parse(entry("d2", "c2", "assistant", "live answer")),
  ];
  const turns = windowTurns(activeBranch(entries));
  assert.deepEqual(
    turns.map((t) => t.content),
    ["Q1", "A1", "live question", "live answer"],
    "the abandoned branch's two turns must not be shipped as live",
  );
});

test("a compaction entry cuts the window at firstKeptEntryId", () => {
  const entries = [
    JSON.parse(HEADER("sid-1")),
    JSON.parse(entry("a", null, "user", "old question")),
    JSON.parse(entry("b", "a", "assistant", "old answer")),
    JSON.parse(entry("k", "b", "user", "kept question")),
    JSON.parse(entry("j", "k", "assistant", "kept answer")),
    JSON.parse(JSON.stringify({ type: "compaction", id: "z", parentId: "j", timestamp: "2026-10-23T11:00:00.000Z", summary: "S", firstKeptEntryId: "k" })),
  ];
  const window = activeBranch(entries);
  assert.deepEqual(windowTurns(window).map((t) => t.content), ["kept question", "kept answer"]);
  // And cutAtCompaction is the exported rule (so both lanes can call it).
  assert.equal(cutAtCompaction(window).length, window.length);
});

test("an entry with no id is never given a positional id", () => {
  const entries = [
    JSON.parse(HEADER("sid-1")),
    { type: "message", parentId: null, message: { role: "user", content: [{ type: "text", text: "ghost" }] } },
    JSON.parse(entry("a", null, "user", "real")),
  ];
  const window = activeBranch(entries);
  assert.deepEqual(window.map((w) => w.id), ["a"], "no synthesized id, no ghost turn");
});

// ── piece 12: the clock source ────────────────────────────────────────────

test("windowCapturedAt reads the entry's own timestamp, in seconds", () => {
  const entries = [
    JSON.parse(HEADER("sid-1")),
    JSON.parse(entry("a", null, "user", "x", { timestamp: "2026-10-23T10:00:05.000Z" })),
    JSON.parse(entry("b", "a", "assistant", "y", { timestamp: "2026-10-23T10:00:42.500Z" })),
  ];
  assert.equal(
    windowCapturedAt(activeBranch(entries)),
    Math.floor(Date.parse("2026-10-23T10:00:42.500Z") / 1000),
    "unix SECONDS, floored — the wire field is a seconds instant",
  );
});

// ── piece 11: status classification ──────────────────────────────────────

test("only a 2xx advances; a BYTES refusal is permanent; credential/timing refusals are retained", () => {
  assert.equal(classifyStatus(200), "advance");
  assert.equal(classifyStatus(204), "advance");
  // Refusals OF THE BYTES: retrying them forever is the head-of-line block.
  assert.equal(classifyStatus(400), "permanent");
  assert.equal(classifyStatus(413), "permanent");
  assert.equal(classifyStatus(422), "permanent");
  // Refusals OF THE MOMENT / OF THE CALLER: the cursor must not be crossed.
  assert.equal(classifyStatus(402), "retain"); // quota
  assert.equal(classifyStatus(409), "retain"); // "already in flight" is benign
  assert.equal(classifyStatus(429), "retain");
  assert.equal(classifyStatus(503), "retain");
  assert.equal(classifyStatus(undefined), "retain"); // transport
  // ⛔ AUTH. `permanent` here dead-letters AND crosses the cursor, so a rotated
  // or expired key would permanently drop every pending session — the exact
  // loss the backstop exists to prevent. These must be retained (a fixed key
  // re-ships), which is also what the file's own 402 reasoning demands.
  assert.equal(classifyStatus(401), "retain", "an expired key must not dead-letter a session");
  assert.equal(classifyStatus(403), "retain", "an entitlement change must not drop turns");
  assert.equal(classifyStatus(407), "retain", "a proxy credential refusal is the same class");
});

test("409 is not a member of PERMANENT_STATUS — the two tables cannot contradict", () => {
  // 409 lives in RETAIN_STATUS only. While it was in BOTH, the permanent member
  // was unreachable and an edit to either table could silently flip 409 into
  // the dead-letter-and-cross path.
  assert.equal(RETAIN_STATUS.has(409), true);
  assert.equal(PERMANENT_STATUS.has(409), false);
  for (const code of AUTH_STATUS) {
    assert.equal(PERMANENT_STATUS.has(code), false, `auth status ${code} must not be permanent`);
  }
});

test("the backoff schedule is bounded and monotone", () => {
  assert.ok(BACKOFF_MS.length > 0);
  for (let i = 1; i < BACKOFF_MS.length; i += 1) assert.ok(BACKOFF_MS[i] > BACKOFF_MS[i - 1]);
  assert.ok(BACKOFF_MS[BACKOFF_MS.length - 1] <= 60_000, "a bounded wait, not an unbounded loop");
});

// ── cursor store: atomic write, restart, corrupt fail-closed ──────────────

test("the cursor SURVIVES a restart and neither re-emits nor skips", () => {
  const dir = tmp("cursor-");
  const state = emptyCursor();
  state.files["/s/one.jsonl"] = { offset: 512, sessionId: "sid-1", dev: 1, ino: 2, delivered: 3 };
  const saved = saveCursor(dir, state);
  assert.equal(saved.ok, true);

  const reloaded = loadCursor(dir);
  assert.equal(reloaded.recovered, false);
  assert.equal(reloaded.state.files["/s/one.jsonl"].offset, 512);
  assert.equal(reloaded.state.files["/s/one.jsonl"].sessionId, "sid-1");
  assert.equal(reloaded.state.files["/s/one.jsonl"].delivered, 3);
});

test("a CORRUPT cursor fails closed to the last good offset (recorded, not silent)", () => {
  const dir = tmp("cursor-corrupt-");
  const state = emptyCursor();
  state.files["/s/one.jsonl"] = { offset: 111, sessionId: "sid-1" };
  saveCursor(dir, state);
  // Second save creates the .bak of the first good copy.
  state.files["/s/one.jsonl"].offset = 222;
  saveCursor(dir, state);

  writeFileSync(cursorPath(dir), '{"version":1,"files":{"\/s\/one.jsonl":{"offset":'); // torn
  const loaded = loadCursor(dir);
  assert.equal(loaded.recovered, true);
  assert.match(String(loaded.error), /failed closed/);
  assert.equal(
    loaded.state.files["/s/one.jsonl"].offset,
    111,
    "the last GOOD offset (the backup), not 0 and not EOF",
  );
  assert.ok(statSync(backupPath(dir)).size > 0);
});

test("a RECOVERY save does not overwrite the last good backup with the corrupt bytes", () => {
  // loadCursor(corrupt main) → the backup → … → saveCursor. The save used to
  // copy the CURRENT (still corrupt) main over `.bak` before renaming the good
  // state in, so the documented "fail closed to the last good offset" guarantee
  // held only until the next save.
  const dir = tmp("cursor-recover-save-");
  const state = emptyCursor();
  state.files["/s/one.jsonl"] = { offset: 111, sessionId: "sid-1" };
  saveCursor(dir, state);
  state.files["/s/one.jsonl"].offset = 222;
  saveCursor(dir, state); // .bak now holds 111

  writeFileSync(cursorPath(dir), '{"version":1,"files":{"\/s\/one.jsonl":{"offset":'); // torn
  const loaded = loadCursor(dir);
  assert.equal(loaded.recovered, true);
  assert.equal(loaded.state.files["/s/one.jsonl"].offset, 111);

  // The worker then saves the recovered state (a per-file checkpoint).
  const saved = saveCursor(dir, loaded.state);
  assert.equal(saved.ok, true);
  const backup = JSON.parse(readFileSync(backupPath(dir), "utf8"));
  assert.equal(
    backup.files["/s/one.jsonl"].offset,
    111,
    "the backup still holds the last GOOD offset — the corrupt main was not copied over it",
  );
  assert.equal(JSON.parse(readFileSync(cursorPath(dir), "utf8")).files["/s/one.jsonl"].offset, 111);
});

test("saveCursor prunes rows for session files that no longer exist", () => {
  const dir = tmp("cursor-prune-");
  const live = sessionFixture(tmp("prune-root-"), "live.jsonl", [HEADER("sid-live")]);
  writeFileSync(live, readFileSync(live)); // ensure it exists
  const state = emptyCursor();
  state.files[live] = { offset: 10, sessionId: "sid-live" };
  state.files["/s/gone.jsonl"] = { offset: 99, sessionId: "sid-gone" };
  // No prune by default: a partial caller must not drop other rows.
  saveCursor(dir, state);
  assert.equal(loadCursor(dir).state.files["/s/gone.jsonl"].offset, 99);
  // The worker prunes on its final save, so the store cannot grow without bound.
  saveCursor(dir, state, { prune: true });
  const pruned = loadCursor(dir).state;
  assert.equal(pruned.files["/s/gone.jsonl"], undefined, "a row for a deleted file is dropped");
  assert.equal(pruned.files[live].offset, 10, "a live file keeps its offset");
});

test("a negative or non-numeric offset in the cursor is dropped, never trusted", () => {
  const dir = tmp("cursor-invalid-");
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    cursorPath(dir),
    JSON.stringify({ version: 1, files: { "/s/a.jsonl": { offset: -5 }, "/s/b.jsonl": { offset: 7 } } }),
  );
  const loaded = loadCursor(dir);
  assert.equal(loaded.state.files["/s/a.jsonl"], undefined, "an invalid offset cannot skip content");
  assert.equal(loaded.state.files["/s/b.jsonl"].offset, 7);
});

test("a cursor from a future version is not silently interpreted", () => {
  const dir = tmp("cursor-version-");
  mkdirSync(dir, { recursive: true });
  writeFileSync(cursorPath(dir), JSON.stringify({ version: 99, files: { "/s/a.jsonl": { offset: 5 } } }));
  const loaded = loadCursor(dir);
  assert.equal(loaded.recovered, true);
  assert.deepEqual(loaded.state.files, {});
});

// ── the worker end-to-end (real files, stubbed server) ───────────────────

test("the worker ships a session file, stamps lane=store_sync, and advances on 2xx", async () => {
  const root = tmp("sessions-");
  const cursorDir = tmp("cursor-run-");
  const path = sessionFixture(root, "s.jsonl", [
    HEADER("sid-e2e"),
    entry("a", null, "user", "hello"),
    entry("b", "a", "assistant", "world", { timestamp: "2026-10-23T10:05:00.000Z" }),
  ]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });

  assert.equal(receipt.shipped, 1);
  assert.equal(receipt.deadLettered, 0);
  assert.equal(fetchImpl.calls.length, 1);
  const body = fetchImpl.calls[0].body;
  assert.equal(body.session_id, "sid-e2e", "piece 6: from the header, not a generated id");
  assert.equal(body.harness, "pi");
  assert.equal(body.capture_lane, LANE, "piece 8: the lane is the discriminator");
  assert.deepEqual(body.conversation.map((t) => t.content), ["hello", "world"]);
  // ISSUE 4 (#3515 review): pin the WIRING of the log's own clock, not just its
  // type. A regression that swaps `windowCapturedAt(window)` for `Date.now()`
  // keeps the `entry_timestamp` label while carrying the observation instant —
  // a false provenance claim, and the timestamp floor's falsifiability rests on
  // that label being true. `world` carries a DISTINCT timestamp on purpose.
  assert.equal(
    body.client_captured_at,
    Math.floor(Date.parse("2026-10-23T10:05:00.000Z") / 1000),
    "piece 12: the store-sync clock is the log's NEWEST entry timestamp, not now()",
  );
  assert.equal(body.client_captured_at_source, "entry_timestamp");
  assert.notEqual(
    body.client_captured_at,
    Math.floor(Date.now() / 1000),
    "the fixture timestamp is old on purpose: now() cannot satisfy this row",
  );

  const state = loadCursor(cursorDir).state;
  assert.equal(state.files[path].offset, statSync(path).size, "cursor advanced to the file size");
});

test("a failing POST does NOT advance the cursor — the batch survives the restart", async () => {
  const root = tmp("sessions-fail-");
  const cursorDir = tmp("cursor-fail-");
  const path = sessionFixture(root, "s.jsonl", [HEADER("sid-f"), entry("a", null, "user", "keep me")]);
  const failing = stubFetch([503]);
  const first = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: failing, ...FAST });
  assert.equal(first.shipped, 0);
  assert.equal(first.files[0].action, "failed");
  assert.equal(loadCursor(cursorDir).state.files[path].offset, 0, "cursor did NOT move");

  // Restart: the SAME content is retried and now lands. At-least-once.
  const good = stubFetch([200]);
  const second = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: good, ...FAST });
  assert.equal(second.shipped, 1);
  assert.equal(good.calls[0].body.conversation[0].content, "keep me");
});

test("restarts do not MULTIPLY retries: the attempt count is persisted", async () => {
  const root = tmp("sessions-mult-");
  const cursorDir = tmp("cursor-mult-");
  const path = sessionFixture(root, "s.jsonl", [HEADER("sid-m"), entry("a", null, "user", "x")]);
  const first = stubFetch([503]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: first, ...FAST });
  const afterFirst = loadCursor(cursorDir).state.files[path];
  const second = stubFetch([503]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, ...FAST });
  const afterSecond = loadCursor(cursorDir).state.files[path];
  assert.equal(
    afterSecond.failures,
    (afterFirst.failures ?? 0) + 1,
    "a restart increments ONE in-flight retry record, it does not reset to zero",
  );
  // DISCRIMINATION: the schedule RESUMES where the previous run stopped, so a
  // restart makes FEWER attempts than a fresh file would. Without reading the
  // persisted counter both runs would make the full BACKOFF_MS.length attempts.
  assert.ok(
    second.calls.length < first.calls.length,
    `a restart must not multiply retries (first=${first.calls.length}, second=${second.calls.length})`,
  );
  assert.equal(afterSecond.offset, 0, "still not advanced");
});

test("a PERMANENT 4xx is dead-lettered and does NOT block a later file", async () => {
  const root = tmp("sessions-dl-");
  const cursorDir = tmp("cursor-dl-");
  sessionFixture(root, "a-bad.jsonl", [HEADER("sid-bad"), entry("a", null, "user", "poison")]);
  sessionFixture(root, "b-good.jsonl", [HEADER("sid-good"), entry("b", null, "user", "fine")]);
  // The staleness of the first answer is what makes head-of-line blocking
  // visible: a naive worker stops at the first permanent refusal.
  const fetchImpl = async (url, init) => {
    const body = JSON.parse(init.body);
    const status = body.session_id === "sid-bad" ? 422 : 200;
    return { ok: status === 200, status, json: async () => ({ detail: `refused ${status}` }) };
  };
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.equal(receipt.deadLettered, 1);
  assert.equal(receipt.shipped, 1, "the later file still ships — no head-of-line block");
  assert.equal(receipt.dropped, 1, "the drop is COUNTED, never silent");
  assert.equal(receipt.deadLetters.length, 1);
  assert.match(receipt.deadLetters[0].reason, /422/);
});

test("a half-written tail neither ships garbage nor blocks the next run", async () => {
  const root = tmp("sessions-tail-");
  const cursorDir = tmp("cursor-tail-");
  const path = join(root, "s.jsonl");
  writeFileSync(
    path,
    `${HEADER("sid-tail")}\n${entry("a", null, "user", "first")}\n{"type":"message","id":"b","parentId":"a","mess`,
  );
  const fetchImpl = stubFetch([200]);
  const first = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.equal(fetchImpl.calls.length, 1, "the complete part ships");
  assert.deepEqual(fetchImpl.calls[0].body.conversation.map((t) => t.content), ["first"]);
  const cursorAfter = loadCursor(cursorDir).state.files[path].offset;
  assert.ok(cursorAfter < statSync(path).size, "cursor is behind the partial tail");

  // Pi finishes the line; the second run picks it up from that offset.
  appendFileSync(path, 'age":{"role":"assistant","content":[{"type":"text","text":"second"}]}}\n');
  const second = stubFetch([200]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, ...FAST });
  assert.equal(second.calls.length, 1);
  assert.deepEqual(
    second.calls[0].body.conversation.map((t) => t.content),
    ["first", "second"],
    "the completed turn is present and whole",
  );
});

// ISSUE 1 (#3515 review): the SIGKILL artifact is a tail that NEVER completes.
// The row above completes the line before the second pass, so it passes even
// with the torn-tail guard absent. This row holds the tail torn across THREE
// passes — exactly what a killed `pi` leaves on disk — and asserts that the
// same window is not re-POSTed forever.
test("a torn trailing line does NOT re-ship the same window on every sweep", async () => {
  const root = tmp("sessions-torn-");
  const cursorDir = tmp("cursor-torn-");
  const path = join(root, "s.jsonl");
  writeFileSync(
    path,
    `${HEADER("sid-torn")}\n${entry("a", null, "user", "first")}\n${entry("b", "a", "assistant", "second")}\n{"type":"message","id":"c","parentId":"b","mess`,
  );

  const first = stubFetch([200]);
  const r1 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: first, ...FAST });
  assert.equal(first.calls.length, 1, "the complete part ships once");
  const after1 = loadCursor(cursorDir).state.files[path];
  assert.equal(after1.delivered, 2, "two turns delivered");

  // Two further sweeps with the tail STILL torn (the process is dead; nothing
  // will ever complete it). The window must not be re-POSTed.
  const second = stubFetch([200]);
  const r2 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, ...FAST });
  const third = stubFetch([200]);
  const r3 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: third, ...FAST });

  assert.equal(second.calls.length, 0, "pass 2 must POST nothing while the tail is torn");
  assert.equal(third.calls.length, 0, "pass 3 must POST nothing while the tail is torn");
  assert.equal(r2.shipped, 0);
  assert.equal(r3.shipped, 0);
  assert.equal(loadCursor(cursorDir).state.files[path].delivered, 2, "`delivered` must not inflate");
  assert.ok(
    loadCursor(cursorDir).state.files[path].offset < statSync(path).size,
    "the cursor still sits behind the torn tail — it is never crossed",
  );

  // DISCRIMINATION: completing the tail must still ship the whole line. The
  // guard suppresses a REPEAT, it does not suppress the real work.
  appendFileSync(path, 'age":{"role":"assistant","content":[{"type":"text","text":"third"}]}}\n');
  const done = stubFetch([200]);
  const r4 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: done, ...FAST });
  assert.equal(done.calls.length, 1, "the completed tail ships on the next pass");
  assert.deepEqual(
    done.calls[0].body.conversation.map((t) => t.content),
    ["first", "second", "third"],
  );
  assert.equal(r4.shipped, 1);
});

test("a TRUNCATED file is re-read from the header, not skipped forever", async () => {
  const root = tmp("sessions-trunc-");
  const cursorDir = tmp("cursor-trunc-");
  const path = sessionFixture(root, "s.jsonl", [
    HEADER("sid-t"),
    entry("a", null, "user", "one"),
    entry("b", "a", "assistant", "two"),
  ]);
  const first = stubFetch([200]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: first, ...FAST });
  const before = loadCursor(cursorDir).state.files[path].offset;
  assert.ok(before > 0);

  // Pi's `_rewriteFile` truncates then rewrites: the file is now SHORTER.
  writeFileSync(path, `${HEADER("sid-t")}\n${entry("a", null, "user", "one")}\n`);
  assert.ok(statSync(path).size < before, "the fixture must actually shrink");

  const second = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, ...FAST });
  assert.equal(second.calls.length, 1, "a silent skip would have made zero calls");
  assert.deepEqual(second.calls[0].body.conversation.map((t) => t.content), ["one"]);
  assert.equal(receipt.files[0].action, "ship");
});

test("a file REPLACED under the same path re-ships from the header", async () => {
  const root = tmp("sessions-replace-");
  const cursorDir = tmp("cursor-replace-");
  const path = sessionFixture(root, "s.jsonl", [HEADER("sid-r"), entry("a", null, "user", "old")]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: stubFetch([200]), ...FAST });
  assert.ok(loadCursor(cursorDir).state.files[path].offset > 0);

  // Replace atomically with a DIFFERENT session (fork/clone shape) of the same
  // length: only the inode (and the header hash) reveal it.
  const replacement = join(root, "tmp-replacement.jsonl");
  writeFileSync(replacement, `${HEADER("sid-r2")}\n${entry("z", null, "user", "new")}\n`);
  renameSync(replacement, path);

  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.equal(fetchImpl.calls.length, 1);
  assert.equal(fetchImpl.calls[0].body.session_id, "sid-r2", "the NEW header id, not the stale one");
  assert.equal(receipt.files[0].sessionId, "sid-r2");
});

test("a file with no session header id is REFUSED — an id is never invented", async () => {
  const root = tmp("sessions-nohdr-");
  const cursorDir = tmp("cursor-nohdr-");
  sessionFixture(root, "s.jsonl", [entry("a", null, "user", "no header here")]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.equal(fetchImpl.calls.length, 0);
  assert.equal(receipt.files[0].action, "failed");
  assert.equal(receipt.files[0].reason, "no_header_id");
  assert.equal(receipt.errors.length, 1);
});

test("the run is BOUNDED: maxFiles caps one pass and the rest drains later", async () => {
  const root = tmp("sessions-bound-");
  const cursorDir = tmp("cursor-bound-");
  for (let i = 0; i < 5; i += 1) {
    sessionFixture(root, `s${i}.jsonl`, [HEADER(`sid-${i}`), entry(`a${i}`, null, "user", `t${i}`)]);
  }
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, maxFiles: 2, ...FAST });
  assert.equal(fetchImpl.calls.length, 2, "at most maxFiles POSTs in one pass");
  const second = stubFetch([200]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, maxFiles: 2, ...FAST });
  assert.equal(second.calls.length, 2, "and the REMAINDER drains on the next run");
});

test("no credential is a NAMED error and does not burn the retry schedule", async () => {
  const root = tmp("sessions-nocred-");
  const cursorDir = tmp("cursor-nocred-");
  sessionFixture(root, "s.jsonl", [HEADER("sid-nocred"), entry("a", null, "user", "x")]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({
    ...FAST,
    sessionDir: root, cursorDir, fetchImpl,
    // `resolveConfig` is bypassed by supplying both: an empty key models an
    // unconfigured box without depending on the machine's own config.
    apiKey: "", apiUrl: "http://127.0.0.1:1",
  });
  assert.equal(fetchImpl.calls.length, 0, "no POST is attempted without a key");
  assert.equal(receipt.shipped, 0);
  assert.equal(receipt.errors.length, 1);
  assert.match(receipt.errors[0], /no TORTOISE_API_KEY/);
});

test("dry-run POSTs nothing and still reports the plan", async () => {
  const root = tmp("sessions-dry-");
  const cursorDir = tmp("cursor-dry-");
  sessionFixture(root, "s.jsonl", [HEADER("sid-d", ), entry("a", null, "user", "x")]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, dryRun: true, ...FAST });
  assert.equal(fetchImpl.calls.length, 0);
  assert.equal(receipt.dryRun, true);
  assert.equal(receipt.files[0].reason, "dry_run");
});

test("dry-run MOVES NO CURSOR — not even for a window with no turn yet", async () => {
  // The advertised contract (`tortoise store-sync --dry-run`: "Plan only —
  // POST nothing, move no cursor") has to hold on the branch where there is
  // nothing to POST: the no-turns path advances the cursor unconditionally in
  // the real run, so it needs its own guard.
  const root = tmp("sessions-dry2-");
  const cursorDir = join(tmp("cursor-dry2-parent-"), "cursor");
  // A window with NO user/assistant message at all: the `no_turns` branch.
  sessionFixture(root, "s.jsonl", [HEADER("sid-d2"), NOTE("a", null)]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, dryRun: true, ...FAST });

  assert.equal(fetchImpl.calls.length, 0);
  assert.equal(receipt.noTurns, 1);
  assert.equal(receipt.files[0].reason, "no_turns");
  assert.equal(existsSync(cursorDir), false, "a report-only run must not create the cursor store");

  // And the REAL run afterwards still ships from offset 0 (nothing was skipped).
  const real = stubFetch([200]);
  appendFileSync(join(root, "s.jsonl"),
    `${entry("b", "a", "user", "the question")}\n${entry("c", "b", "assistant", "the answer")}\n`);
  const second = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: real, ...FAST });
  assert.equal(second.shipped, 1);
  assert.deepEqual(real.calls[0].body.conversation.map((t) => t.content), ["the question", "the answer"]);
});

test("a 401/403 RETAINS the cursor — a rotated key must not drop a session", async () => {
  const root = tmp("sessions-auth-");
  const cursorDir = tmp("cursor-auth-");
  const path = sessionFixture(root, "s.jsonl", [
    HEADER("sid-auth"), entry("a", null, "user", "before the rotation"),
  ]);
  const denied = stubFetch([401]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: denied, ...FAST });

  assert.equal(receipt.files[0].action, "failed", `got ${JSON.stringify(receipt.files[0])}`);
  assert.equal(receipt.files[0].reason !== undefined, true);
  assert.equal(receipt.deadLettered, 0, "an auth refusal is NOT dead-lettered");
  assert.equal(receipt.dropped, 0, "nothing is dropped");
  assert.equal(
    loadCursor(cursorDir).state.files[path].offset,
    0,
    "the cursor must NOT be crossed past an auth refusal",
  );

  // The key is fixed: the SAME turns ship. Under `permanent` they were gone
  // forever — `planFile` returns `skip` once the cursor is at EOF.
  const fixed = stubFetch([200]);
  const second = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: fixed, ...FAST });
  assert.equal(second.shipped, 1, "a fixed key re-ships the retained window");
  assert.deepEqual(fixed.calls[0].body.conversation.map((t) => t.content), ["before the rotation"]);
});

test("the deploy-order DOWNGRADE still re-sends when `failures` has saturated", async () => {
  // The tolerance exists so a server that has not yet learned
  // `client_captured_at_source=entry_timestamp` does not dead-letter a real
  // session. It spends the CURRENT attempt on the downgraded re-send — which is
  // only sound if a later iteration carries the turns. `startAttempt` resumes at
  // the persisted `failures`, saturated at `BACKOFF_MS.length - 1` after any
  // outage of a few sweeps (402 quota, 401/403/407, 409, 429, 5xx, transport all
  // retain AND increment it), so at the ceiling the body ran exactly ONCE: the
  // `continue` ended the loop, `decision` stayed "retain", and the downgraded
  // payload was never sent — forever, for exactly the state it was written for.
  const root = tmp("sessions-downgrade-");
  const cursorDir = tmp("cursor-downgrade-");
  const path = sessionFixture(root, "s.jsonl", [
    HEADER("sid-downgrade"), entry("a", null, "user", "ship me anyway"),
  ]);

  // An outage saturates the persisted budget, then the outage ends and the
  // server turns out not to know the new member yet.
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: stubFetch([503]), ...FAST });
  const saturated = loadCursor(cursorDir).state;
  saturated.files[path].failures = BACKOFF_MS.length - 1;
  assert.equal(saveCursor(cursorDir, saturated).ok, true);
  assert.equal(loadCursor(cursorDir).state.files[path].failures, BACKOFF_MS.length - 1);

  const seen = [];
  const deployOrder = async (url, init) => {
    const body = JSON.parse(init.body);
    seen.push(body.client_captured_at_source);
    const stale = body.client_captured_at_source === "entry_timestamp";
    return {
      ok: !stale,
      status: stale ? 422 : 200,
      json: async () => (stale
        ? { detail: "unknown field client_captured_at_source" }
        : {}),
    };
  };
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: deployOrder, ...FAST });

  assert.deepEqual(seen, ["entry_timestamp", "unknown"], (
    "the downgraded re-send must still happen at the retry ceiling"
  ));
  assert.equal(receipt.shipped, 1, "the turns are carried, not stranded as `retained`");
  assert.equal(loadCursor(cursorDir).state.files[path].offset, statSync(path).size);
});

test("a /compact SHRINK is refused, never shipped (it would delete filed turns)", async () => {
  const root = tmp("sessions-shrink-");
  const cursorDir = tmp("cursor-shrink-");
  const compaction = (parentId, kept) => JSON.stringify({
    type: "compaction", id: "z", parentId, timestamp: "2026-10-23T11:00:00.000Z",
    summary: "S", firstKeptEntryId: kept,
  });
  const path = sessionFixture(root, "s.jsonl", [
    HEADER("sid-shrink"),
    entry("a", null, "user", "old question"),
    entry("b", "a", "assistant", "old answer"),
  ]);
  const first = stubFetch([200]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: first, ...FAST });
  assert.equal(first.calls.length, 1);
  const beforeCursor = loadCursor(cursorDir).state.files[path];
  assert.equal(beforeCursor.lastWindow, 2, "the window size is recorded");
  const before = beforeCursor.offset;

  // Pi compacts: the active window is now ONLY the kept pair, and the turn
  // store's shorter-re-capture rule would HARD-DELETE everything past it.
  appendFileSync(path, `${entry("k", "b", "user", "kept question")}\n`
    + `${compaction("k", "k")}\n`);
  const second = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, ...FAST });

  assert.equal(second.calls.length, 0, "a shrinking window must NOT be posted");
  assert.equal(receipt.shrunk, 1, "the refusal is COUNTED, never silent");
  assert.equal(receipt.files[0].reason, "window_shrunk");
  const state = loadCursor(cursorDir).state.files[path];
  assert.equal(
    state.offset,
    before,
    "the cursor stays exactly where the delivered window ended (retained, not crossed)",
  );
  assert.equal(state.lastWindow, 2, "the delivered window size is unchanged");

  // DISCRIMINATION: the guard is the ONLY reason this file is not shipped; the
  // plan for it is `ship` (the file grew), so a missing guard posts here.
  assert.equal(second.calls.length, 0);

  // And a DRY RUN reports the same refusal without persisting anything: the
  // shrink branch used to write the cursor + checkpoint unconditionally, which
  // would have re-introduced a durable side effect in "read-only" mode.
  const beforeDry = readFileSync(cursorPath(cursorDir), "utf8");
  const dry = stubFetch([200]);
  const dryReceipt = await runStoreSync({
    sessionDir: root, cursorDir, fetchImpl: dry, dryRun: true, ...FAST,
  });
  assert.equal(dry.calls.length, 0);
  assert.equal(dryReceipt.shrunk, 1, "a dry run still REPORTS the refusal");
  assert.equal(dryReceipt.files[0].reason, "window_shrunk");
  assert.equal(
    readFileSync(cursorPath(cursorDir), "utf8"),
    beforeDry,
    "the dry run must not rewrite the cursor store",
  );
});

// ISSUE 2 (#3515 review): the guard above needs a PRIOR cursor to fire, which is
// the one case production does NOT give it. The ordinary upgrade path is a long
// session that was compacted before generation 8 ever ran here, and a live
// session is excluded from every sweep it starts — so the backstop's FIRST sweep
// of a file is already post-compaction. Shipping that short window would let the
// turn store's shorter-re-capture rule hard-delete the pre-compaction turns the
// HOOK filed. It MUST refuse instead, with no prior to compare against.
test("a FIRST ship of an already-compacted file is refused (no cursor to compare)", async () => {
  const root = tmp("sessions-compact-first-");
  const cursorDir = tmp("cursor-compact-first-");
  const compaction = (parentId, kept) => JSON.stringify({
    type: "compaction", id: "z", parentId, timestamp: "2026-10-23T11:00:00.000Z",
    summary: "S", firstKeptEntryId: kept,
  });
  // The file itself carries the WHOLE conversation: two old turns, then Pi
  // compacts, then the two kept turns. The active window is only the kept pair.
  const path = sessionFixture(root, "s.jsonl", [
    HEADER("sid-compact-first"),
    entry("a", null, "user", "old question"),
    entry("b", "a", "assistant", "old answer"),
    entry("k", "b", "user", "kept question"),
    compaction("k", "k"),
    entry("m", "z", "assistant", "kept answer"),
  ]);
  assert.equal(existsSync(cursorPath(cursorDir)), false, "the premise: NO cursor at all");


  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });

  assert.equal(fetchImpl.calls.length, 0, "a compacted first ship must NOT be posted");
  assert.equal(receipt.shipped, 0);
  assert.equal(receipt.shrunk, 1, "the refusal is COUNTED, never silent");
  assert.equal(receipt.files[0].reason, "window_shrunk");
  const state = loadCursor(cursorDir).state.files[path];
  // NOT `state === undefined || state.offset === 0`: a persisted row at offset 0
  // is exactly the state that disarms the NEXT sweep, so accepting it would
  // accept the deletion one run later. No row at all is the invariant.
  assert.equal(
    state,
    undefined,
    "the refusal must leave NO cursor row — a row would make the next sweep look proven",
  );

  // And the SECOND real sweep must refuse again (stickiness, asserted rather
  // than inferred from the dry run's ordering).
  const again = stubFetch([200]);
  const second = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: again, ...FAST });
  assert.equal(again.calls.length, 0, "a sticky refusal must not lapse on the next sweep");
  assert.equal(second.shrunk, 1);

  // DISCRIMINATION: the plan for this file is `ship` (no cursor, file non-empty),
  // so with the first-ship arm of the guard removed this row posts. The only
  // reason it does not is the guard.
  assert.equal(fetchImpl.calls.length, 0);

  // And a DRY run reports the same refusal without creating a cursor store.
  const dry = stubFetch([200]);
  const dryReceipt = await runStoreSync({
    sessionDir: root, cursorDir, fetchImpl: dry, dryRun: true, ...FAST,
  });
  assert.equal(dry.calls.length, 0);
  assert.equal(dryReceipt.shrunk, 1);
});

test("a cursor row that PROVES NOTHING cannot disarm the first-ship refusal", async () => {
  // #3515 review ISSUE 1: `prior === undefined` was the wrong test for "this
  // lane has never delivered anything". Two ordinary paths write a cursor row
  // with `delivered: 0` and no window — the `noTurns` advance and a first POST
  // that is RETAINED (402 quota, offline). Either one made the guard believe it
  // had a prior, so a quota block that lifts AFTER the user compacts would ship
  // the short window and hard-delete the hook's turns.
  const compaction = (parentId, kept) => JSON.stringify({
    type: "compaction", id: "z", parentId, timestamp: "2026-10-23T11:00:00.000Z",
    summary: "S", firstKeptEntryId: kept,
  });
  const fullLog = [
    HEADER("sid-unproven"),
    entry("a", null, "user", "old question"),
    entry("b", "a", "assistant", "old answer"),
    entry("k", "b", "user", "kept question"),
    compaction("k", "k"),
    entry("m", "z", "assistant", "kept answer"),
  ];

  // (a) a `no_turns` row: header + a label only, then the real history appears.
  {
    const root = tmp("sessions-unproven-nt-");
    const cursorDir = tmp("cursor-unproven-nt-");
    const path = join(root, "s.jsonl");
    writeFileSync(path, `${HEADER("sid-unproven")}\n${NOTE("x", null)}\n`);
    const warm = stubFetch([200]);
    const r1 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: warm, ...FAST });
    assert.equal(r1.noTurns, 1, "the premise: a no_turns row was written");
    assert.equal(loadCursor(cursorDir).state.files[path].delivered, 0, "and it delivered nothing");

    writeFileSync(path, `${fullLog.join("\n")}\n`);
    const ship = stubFetch([200]);
    const r2 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: ship, ...FAST });
    assert.equal(ship.calls.length, 0, "a no_turns row must not disarm the refusal");
    assert.equal(r2.shrunk, 1);
  }

  // (b) a RETAINED first POST (402 quota) that later succeeds.
  {
    const root = tmp("sessions-unproven-402-");
    const cursorDir = tmp("cursor-unproven-402-");
    const path = join(root, "s.jsonl");
    writeFileSync(path, `${HEADER("sid-unproven")}\n${entry("q", null, "user", "hi")}\n`);
    const quota = stubFetch([402]);
    const r1 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: quota, ...FAST });
    assert.equal(r1.shipped, 0);
    assert.equal(loadCursor(cursorDir).state.files[path].delivered, 0);

    writeFileSync(path, `${fullLog.join("\n")}\n`);
    const ship = stubFetch([200]);
    const r2 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: ship, ...FAST });
    assert.equal(ship.calls.length, 0, "a retained first POST must not disarm the refusal");
    assert.equal(r2.shrunk, 1);
  }
});

test("the maxWindow high-water mark SURVIVES a reload so the guard cannot drift off", async () => {
  const root = tmp("sessions-hw-");
  const cursorDir = tmp("cursor-hw-");
  const path = sessionFixture(root, "s.jsonl", [
    HEADER("sid-hw"),
    entry("a", null, "user", "one"),
    entry("b", "a", "assistant", "two"),
  ]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: stubFetch([200]), ...FAST });
  const recorded = loadCursor(cursorDir).state.files[path];
  assert.equal(recorded.lastWindow, 2);
  // `maxWindow` must be PERSISTED, not derived: validCursor() drops unlisted
  // numeric fields on reload, so an unlisted `maxWindow` would silently reset
  // the guard's basis every run — a fail-open with no failing test.
  assert.equal(recorded.maxWindow, 2, "maxWindow must round-trip through the cursor store");
});

test("the cheap complete-through NEVER crosses a torn tail", () => {
  // #3515 review (P3): this value feeds `planFile`'s cheap skip check, so a
  // value that CROSSES a partial line would make the sweep believe it has
  // settled a tail it has not. Returning `size` for a torn file is the
  // mutation: it costs nothing visible when the full scan re-plans afterwards
  // (that scan is authoritative), which is exactly why it needs a row of its
  // own — the damage is a wrong skip, and a wrong skip is silent.
  const root = tmp("cheap-tail-");
  const path = join(root, "t.jsonl");
  const complete = `${HEADER("sid-cheap")}\n${entry("a", null, "user", "hi")}\n`;
  const torn = '{"type":"message","id":"b","parentId":"a","message":{"role":"assistant","con';
  writeFileSync(path, complete + torn);
  const size = statSync(path).size;
  assert.equal(
    completeThroughCheap(path, size),
    Buffer.byteLength(complete, "utf8"),
    "a torn tail stops the cheap scan at the last COMPLETE line",
  );

  // A file that ends in a newline is complete through its size — the fast path
  // is the whole point of the function.
  writeFileSync(path, complete);
  assert.equal(completeThroughCheap(path, statSync(path).size), Buffer.byteLength(complete, "utf8"));

  // An empty file has nothing to settle.
  writeFileSync(path, "");
  assert.equal(completeThroughCheap(path, 0), 0);

  // A single line larger than the 64 KiB window holds no newline at all: the
  // honest answer is `undefined` (fall back to the full scan), never a guess.
  const huge = tmp("cheap-huge-");
  const hugePath = join(huge, "h.jsonl");
  writeFileSync(hugePath, "x".repeat(70 * 1024));
  assert.equal(
    completeThroughCheap(hugePath, statSync(hugePath).size),
    undefined,
    "no newline in the window is UNKNOWN, not 'complete'",
  );
});

test("a RE-ANCHORED plan that also hits the unproven arm is still REFUSED", async () => {
  // #3515 review (P2): the length arm's `reanchored` exception must NOT reach the
  // UNPROVEN arm. Re-anchoring means "this file is being read as a new file", so
  // a LENGTH comparison is meaningless (different index space) — but "we cannot
  // prove we ever delivered anything, and the window is compacted" is a fact
  // about THIS delivery, and following the rewrite there ships a short window
  // whose turns the store hard-DETACH-DELETEs (#1920).
  //
  // What this row pins is the OUTER condition at the refusal site
  // (`if (unproven || (!reanchored && turns.length < priorWindow))`): exempting
  // the whole arm when re-anchored — `(unproven && !reanchored) || ...` — leaves
  // every other row green AND posts the short window, so that is the fail-open
  // shape. The INNER `if (unproven)` (the persistence branch) is a different
  // mutation and it does NOT fail open: the outer test already refused, so the
  // window is still not shipped; what changes is that a prove-nothing cursor row
  // gets written instead of the refusal being sticky. That is pinned separately
  // below, by asserting NO row is written.
  const root = tmp("sessions-reanchor-unproven-");
  const cursorDir = tmp("cursor-reanchor-unproven-");
  const path = join(root, "s.jsonl");

  // 1. A header-only file advances at `delivered: 0` with NO window — a row that
  //    proves nothing (the same shape `noTurns` and a RETAINED first POST leave).
  writeFileSync(path, `${HEADER("sid-a")}\n`);
  const r0 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: stubFetch([200]), ...FAST });
  assert.equal(r0.noTurns, 1, "the premise: nothing to ship yet");
  const row0 = loadCursor(cursorDir).state.files[path];
  assert.equal(row0.delivered, 0, "the premise: a row that proves nothing");
  assert.equal(row0.maxWindow ?? undefined, undefined, "the premise: no window held");

  // 2. The path is REWRITTEN under a different session id — Pi's `_rewriteFile`
  //    shape, so the plan re-anchors to 0 — and the new window is COMPACTED.
  writeFileSync(path, [
    HEADER("sid-b"),
    entry("q0", null, "user", "kept question"),
    entry("a0", "q0", "assistant", "kept answer"),
    JSON.stringify({
      type: "compaction", id: "z", parentId: "a0",
      timestamp: "2026-10-23T11:00:00.000Z", summary: "S", firstKeptEntryId: "q0",
    }),
  ].join("\n") + "\n");

  const fetch2 = stubFetch([200]);
  const logLines = [];
  const r2 = await runStoreSync({
    sessionDir: root, cursorDir, fetchImpl: fetch2, ...FAST,
    log: (l) => logLines.push(l),
  });
  assert.equal(
    fetch2.calls.length, 0,
    "a re-anchored, UNPROVEN, compacted window must not be posted — the exception "
      + "covers the length arm only",
  );
  assert.equal(r2.shrunk, 1, "the refusal is COUNTED, never silent");
  assert.equal(r2.files[0].reason, "window_shrunk");
  assert.ok(
    logLines.some((l) => l.includes("REFUSED a shrunk window")),
    "the refusal is logged",
  );
  // STICKY BY CONSTRUCTION: an unproven refusal writes NO cursor row, so the
  // file stays unproven and the guard fires again on the next sweep. Writing a
  // row here would make the next run look like it had a prior, `priorWindow`
  // would read 0, and the short window would ship — the deletion this guard
  // exists to prevent, one run later. This is the inner `if (unproven)` branch's
  // discriminator (the outer condition is pinned by the no-POST assertion above).
  assert.equal(
    loadCursor(cursorDir).state.files[path],
    undefined,
    "an unproven refusal must leave NO row — the next sweep must stay unproven",
  );
});

test("a window that shrinks with NO compaction entry is refused too", async () => {
  // #3515 review ISSUE 1: the length arm used to be gated on `compacted`, so the
  // code was NARROWER than the invariant it printed ("a window may never SHRINK
  // below what this file already delivered"). Pi's log is a TREE, so an active
  // branch can also shorten without a compaction entry — and a shipped 4->2
  // shrink is the same hard DETACH DELETE the guard exists to prevent.
  const root = tmp("sessions-noshrink-");
  const cursorDir = tmp("cursor-noshrink-");
  const path = join(root, "s.jsonl");
  const full = [
    HEADER("sid-noshrink"),
    entry("q0", null, "user", "q0"),
    entry("a0", "q0", "assistant", "a0"),
    entry("q1", "a0", "user", "q1"),
    entry("a1", "q1", "assistant", "a1"),
  ];
  writeFileSync(path, `${full.join("\n")}\n`);
  const first = stubFetch([200]);
  const r1 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: first, ...FAST });
  assert.equal(r1.shipped, 1);
  assert.equal(first.calls[0].body.conversation.length, 4, "four turns delivered");
  const before = statSync(path).size;

  // The file GROWS while the ACTIVE BRANCH shortens: a new entry whose parentId
  // is not the old leaf re-points the tree at a shorter chain. This is the
  // reachable shape — a file that got shorter would re-anchor instead, which is
  // the legitimate `_rewriteFile` case.
  appendFileSync(path, `${entry("x1", null, "user", "a different branch")}\n`);
  assert.ok(statSync(path).size > before, "the premise: the FILE grows");
  const second = stubFetch([200]);
  const shrinkLog = [];
  const r2 = await runStoreSync({
    sessionDir: root, cursorDir, fetchImpl: second, ...FAST,
    log: (l) => shrinkLog.push(l),
  });
  assert.equal(second.calls.length, 0, "a branch shrink must NOT be posted");
  assert.equal(r2.shrunk, 1, "the refusal is COUNTED, never silent");
  assert.equal(r2.files[0].reason, "window_shrunk");
  assert.equal(loadCursor(cursorDir).state.files[path].maxWindow, 4, "the high-water mark is kept");
  // ISSUE 4: `failures` is the POST retry SCHEDULE's position, and a refusal is
  // not a failed POST. Since the length arm now fires on any branch re-point,
  // spending the budget here would let routine refusals saturate it and leave
  // the eventual real ship with ONE attempt instead of the full schedule.
  assert.equal(
    loadCursor(cursorDir).state.files[path].failures ?? 0,
    0,
    "a refusal must not spend the POST retry budget",
  );
  // #3515 review (P3): the diagnosis is CAUSE-NEUTRAL. `compacted` is a fact
  // about the window, not about the shrink, so naming `/compact` categorically
  // sends a user hunting a compaction that never happened. Pinned as TEXT
  // because the message is the only surface that explains a refusal.
  assert.ok(
    shrinkLog.some((l) => l.includes("a shorter branch")),
    `the log names the real cause; got: ${JSON.stringify(shrinkLog)}`,
  );
  assert.ok(
    !shrinkLog.some((l) => l.includes("/compact")),
    `a tree-append shrink must not be blamed on /compact; got: ${JSON.stringify(shrinkLog)}`,
  );
});

test("a ZERO-BYTE session file is DEFERRED, not reported as a hard error", async () => {
  // #3515 review ISSUE 2: an empty `.jsonl` is the normal on-disk state while Pi
  // truncate-then-rewrites a file and between an explicit `--session` file being
  // created and its first write. A 0-byte buffer has no partial line to detect,
  // so `scan.partial` was false and the sweep reported `errors` + exit 1 on an
  // ordinary transient. It must defer (cursor unmoved), like a torn header.
  const root = tmp("sessions-empty-");
  const cursorDir = tmp("cursor-empty-");
  const path = join(root, "s.jsonl");
  writeFileSync(path, "");
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.deepEqual(receipt.errors, [], "an empty file is a transient, never an error");
  assert.equal(fetchImpl.calls.length, 0);
  assert.equal(receipt.files[0].action, "partial");
  assert.equal(loadCursor(cursorDir).state.files[path], undefined, "nothing is recorded for it");

  // A file that HAS complete lines and still carries no session header is a
  // genuinely foreign `.jsonl` and stays a hard error.
  const foreign = join(root, "foreign.jsonl");
  writeFileSync(foreign, `${entry("x", null, "user", "not a session")}\n`);
  const f2 = stubFetch([200]);
  const r2 = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: f2, ...FAST });
  assert.equal(r2.errors.length, 1, "a non-empty headerless file is still refused loudly");
});

test("a no-turns window is COUNTED as examined-but-awaiting, never as shipped", async () => {
  const root = tmp("sessions-noturns-");
  const cursorDir = tmp("cursor-noturns-");
  sessionFixture(root, "s.jsonl", [HEADER("sid-nt"), NOTE("a", null)]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.equal(fetchImpl.calls.length, 0, "nothing shippable yet");
  assert.equal(receipt.noTurns, 1, "examined files are reported, not silently dropped");
  assert.equal(receipt.skipped, 0, "`skipped` means up-to-date — a count is not a reason");
  assert.equal(receipt.files[0].reason, "no_turns");
});

test("scanSessionFile keeps the header sha stable across re-reads", () => {
  const root = tmp("scan-");
  const path = sessionFixture(root, "s.jsonl", [HEADER("sid-s"), entry("a", null, "user", "x")]);
  const a = scanSessionFile(path);
  const b = scanSessionFile(path);
  assert.equal(a.headerId, "sid-s");
  assert.equal(a.headerSha, b.headerSha);
  assert.equal(a.entries.length, 2);
});
