---
title: "Pi capture architecture — the hybrid (in-process hook + store-sync backstop)"
type: engineering
domain: platform
doc_status: live
created: 2026-10-23
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, pi
issue: daniel-ospina/tortoise#3515
objective: daniel-ospina/tortoise#1714
---

# Pi capture architecture — the hybrid

**This note REPLACES the spike-framing verdict** on Pi capture. #3515's title says
`RUN FIRST` because the shape pinned here is what every later capture task builds
on: a one-off demonstration that capture *can* work is not the deliverable, and a
design that only works when a quota happens to be available is not the pinned
architecture either.

Pinned in one sentence: **an in-process hook for promptness and attribution, plus a
store-sync backstop that reads Pi's own per-entry `.jsonl` with a per-file cursor** —
two producers of the SAME payload through the SAME builder, distinguished only by a
persisted `capture_lane` marker.

---

## 1. What Pi guarantees — measured, not asserted

Every row below was checked against Pi's shipped source **and** against a live `pi`
process on 2026-10-23. The "how" column is the method; a claim with no method is a
claim to re-measure, not to trust.

| # | Guarantee | How it was verified |
|---|---|---|
| L1 | The session file is `~/.pi/agent/sessions/--<cwd>--/<timestamp>_<uuid>.jsonl`; the header `id` **equals** the filename UUID. | Read a live file's first line: `{"type":"session","version":3,"id":"01a168e1-…","timestamp":…}` and matched the id to the filename. `session-format.md` §File Location + `session-manager.js::newSession` (the filename is built from the id). |
| L2 | **No file exists until the first assistant message.** Entries are buffered in memory before that; on the first assistant message the whole buffer + header is written at once (`openSync(file,"wx")`). | Live run with `PI_CODING_AGENT_SESSION_DIR` pointed at an empty dir, a 50 ms watcher: the file **first appeared at t=23.2 s with 6 lines / 2,656 B** — i.e. only after the first assistant turn, and as one bulk write. Code: `_persist` returns early while `!hasAssistant`. |
| L3 | After that, **one append per entry** (`appendFileSync`), monotonically growing. | Same watcher, remainder of the run: 6→7 lines at t=28.4, 8 at 29.9, 9 at 35.0, 10 at 36.7 (2,656→2,941→3,602→3,890→4,561 B). Never shrank. |
| L4 | The log is a **tree** (`id`/`parentId`); `/branch`-style navigation abandons a branch **in the same file**, and `/compact` appends a `compaction` entry carrying `firstKeptEntryId`. | `session-format.md` §Session Version + `session-manager.js::appendMessage` (every entry takes `parentId = leafId`), `appendCompaction`. Live file carried `session`, `model_change`, `thinking_level_change`, `message`, `custom_message` kinds with `id`/`parentId` on all but the header. |
| L5 | **No `fsync` anywhere in the persist path** ⇒ durable against process death (a `write(2)` reached the page cache), **not** against power loss. | `session-manager.js`: `appendFileSync` / `writeFileSync` only; no `fsync` import. |
| L6 | The file **can shrink**: `_rewriteFile()` truncates (`openSync(path,"w")`) then rewrites — on a version migration at load, on a label change, and when resuming an empty explicit `--session` file. | `_rewriteFile` + its three call sites (`_loadEntries`, label changes, `_setSessionFile`). This is why the cursor must treat "cursor > file size" as a **normal** case, not an anomaly. |
| L7 | `session_shutdown` fires on quit / Ctrl-C / Ctrl-D / SIGHUP / SIGTERM and **is awaited**; it does **not** fire on SIGKILL or a crash. | `extensions.md` §Lifecycle; `dist/core/extensions/runner.js` awaits each handler. Measured the consequence: a `pi -p` **SIGKILLed** at ~20 s left **10 complete entries, ending in a newline** on disk — and the shutdown hook never ran. |
| L8 | `agent_end` is **not** the end of a session (auto-retry, auto-compact-and-retry, queued follow-ups); `agent_settled` is the settled signal. | `extensions.md:569`. |
| L9 | `--no-session` persists nothing. | `sessions.md` §Session Storage. |

**The two rows that decide the architecture are L2 and L7.** L2 means a
file-reading backstop can say nothing about a session that has not yet produced an
assistant message. L7 means an in-process hook can say nothing about a session that
died. Neither half covers the other's hole, which is the entire argument for the
hybrid.

---

## 2. The previous verdict, claim by claim

The verdict this note replaces framed the question as a **spike**: *can a Pi
session be captured?* — and answered it with a hand-built demonstration. The
spike framing is rejected (epic plan v3.2 §B3). What matters is whether the
**shipped** path captures, on the death modes users actually hit, with evidence a
reader can re-run. Every claim in the old framing, re-measured:

| Claim in the old framing | What is actually true | Evidence |
|---|---|---|
| "Capture works: the hook posts and the server stores the session." | True **for one death mode** — a clean quit. The hook is `session_shutdown`-only in `reflect-hook.ts` (`reason === "quit"`), and the Pi extension files at `agent_end` fire-and-forget. | `reflect-hook.ts` handler gate; `tortoise-capture.ts` `postCapture(...)` not awaited. |
| "A demonstration proves the architecture." | False. A demonstration is a **transport** result: it says nothing about a SIGKILL, a crash, or a dead hook. That failure (`the check greens with a dead hook`) is what §6's part (ii)+(c) exists to end. | §6 below; `Session.capture_lane` is the only discriminator. |
| "The fallback spool is the durable record." | Half true. The spool (`~/.tortoise/session-events/`) is durable **but nothing retries it into silence for the hookless case** — measured on this machine: **2,266 records / 15 MB across 7 daily files**, and the per-session `entries/` dir is empty. (The issue's `~22,640` figure is stale for this tree — re-measured, not repeated.) | `cat ~/.tortoise/session-events/*.jsonl \| wc -l` = 2266; `du -sh` = 15M. |
| "`session_id` can be generated by the producer." | False and dangerous. A fork/resume mints a **new file with a new UUID**; a generated id double-writes one conversation as two Sessions, or mis-attributes turns. Pinned: **both lanes read the header `id`; neither generates one** (piece 6). | `newSession` (id → filename) + `_loadEntries` (`sessionId = header.id`). Test: `test_the_cli_never_invents_a_session_id`, `test_a_file_with_no_session_header_id_is_REFUSED`. |
| "A byte/line cursor over the log is sufficient." | False — the log is a **tree** (L4). A cursor over the byte stream ships abandoned branches as live turns. Pinned: one shared branch contract (`activeBranch` + `cutAtCompaction`). | `test_activeBranch_ships_the_LIVE_chain_only`; `test_a_compaction_entry_cuts_the_window_at_firstKeptEntryId`. |
| "A probe proves the install." | True for **transport only**: network, auth, endpoint, payload shape. It cannot prove the hook fires. | §6 part (i) — measured live: `{"ok":true,"status":200}` against `api.premiselabs.co`. |
| "`install_probe_{harness}` is emitted today." | It is emitted by the extension on `session_start`; the server-side key exists (`hosted_api.py`). Nothing in the old framing read it **back**, which is why part (i) requires 2xx **and** a server-side read-back. | `postInstallProbe` call site; §6. |
| "Capture is on." (implicit) | It is **off** on this box, by policy not by defect — see §8. | Live: `Hosted tortoise capture OFF (cloud-not-enabled)`. |

---

## 3. Current gaps that the hybrid closes

| Gap | Where | Consequence |
|---|---|---|
| Hook fires on `session_shutdown{reason:"quit"}` only | `agent-infra/extensions/reflect-hook.ts` | `/new`, `/resume`, `/fork`, `/reload`, Ctrl-C, kill: nothing filed. |
| Pi extension files at `agent_end` **fire-and-forget** | `tortoise/pi-hooks/tortoise-capture.ts` | An immediate quit can cut the POST; the local spool lands, and nothing re-sends it. |
| Nothing reads Pi's own `.jsonl` | — (this note's subject) | A SIGKILLed session (measured: 10 durable entries) is never filed. |
| No cursor anywhere | — | A re-read of a log either duplicates unboundedly or, worse, "remembers" nothing. |

---

## 4. The pinned hybrid, and what each half alone loses

**Hook alone loses** every session that ended without a hook: SIGKILL, crash,
closed terminal, a hook that was never trusted/installed. It also cannot re-read
history, so a single refused POST at the wrong moment is a permanent loss.

**Store-sync alone loses** the promptness and the attribution: it can only read what
Pi flushed (L2 — nothing before the first assistant message), it cannot see the
process's own identity observations, and its `client_captured_at` is a *log* clock,
not an observation clock. It also gives the product no in-session signal that capture
is working, which is exactly the "looks dead while it works" failure the health line
exists to fix.

**Together** each supplies what the other cannot, and neither is a second capture
path: the backstop imports the hook's `buildCapturePayload` / `extractTurns` /
`postCapture`, so the two lanes cannot drift into two payload shapes. A test pins
that import (`test_the_worker_does_not_carry_a_second_payload_builder`).

**One recorded consequence of that sharing — GUARDED, not resolved.**
The two lanes agree on the payload *shape* but can disagree on *content* after a
`/compact`: the backstop's window is strictly shorter (cut at
`compaction.firstKeptEntryId`), while the hook's payload grows. The turn store's
shorter-re-capture rule **DELETES the turns beyond the new window** (`sdk.py`; the
`#1920` behaviour, journaled as a delete) — so a post-compaction backstop ship can
remove turns the hook filed.

The worker therefore **REFUSES to ship a shrinking window** — of ANY cause, not only
a `/compact` cut: when the window is shorter than the high-water mark of what this
file may already have delivered, the cursor is RETAINED, nothing is posted, and the
refusal is counted (`receipt.shrunk`) and logged. A window carrying a `compaction`
entry that this lane **cannot prove it delivered anything behind** is refused too:
it cannot prove the server holds nothing older, and refusing is the only direction
that cannot destroy turns the hook filed. The length arm's ONE exception is a
**re-anchored** plan (Pi's truncate-then-rewrite), where the cursor was reset and the
old offset is not the same index space — and even then the unproven arm still applies. That
narrows the deletion path to the residual below without deciding the open design
question — posting the *union* of shipped + kept, or a server-side monotone window —
which stays on #3515 and is named in §9. Recovery for anything already filed is still
the journal, not the Session.

**One deliberate cost, stated so it cannot read as an oversight.** The refusal is per
FILE and STICKY: a compacted session whose window cannot grow back past what was
delivered stays refused on every later run. That is the intended trade — a refused
session is visible (`shrunk`) while a deleting ship is not. **Measured magnitude**
(one box, 2026-10-24): **55 of 292** real session files carry a `compaction` entry,
so about **19%** of a machine's history is refused on its first ship and left to the
hook lane and the journal. That is the size of the gap the union/`turn_offset` work in
§9 has to close — it is not a rounding error, and it is the reason the refusal is
counted rather than silent.

**The one field that distinguishes them** is the persisted `capture_lane`
(`hook` | `store_sync`). Without it the two lanes POST byte-identical payloads and a
hook-liveness check **cannot fail** — a store-sync-only run would green it with a
dead hook.

---

## 5. The required pieces

The issue's set is **thirteen** (its earlier revision said five; numbers are stable
and are not renumbered). Status against the tree at this commit:

| # | Piece | Status |
|---|---|---|
| 1 | Durable local record appended **per turn** | **landed before this lane** — `capture_spool.py` appends only the new turns per capture; the extension spools per snapshot. |
| 2 | **A cursor per session file, advance-on-2xx only** | **built here** — `cursor-store.ts` + `store-sync.ts`. |
| 3 | A retrying sync worker with **bounded** backoff | **built here** — `BACKOFF_MS` (5 attempts, ≤20 s), `MAX_ATTEMPTS` derived from it. |
| 4 | At-least-once delivery keyed by an **idempotency key** | **landed before this lane** — the server MERGEs turn Points on `{session_id}_t{i}`; the worker re-ships the window and relies on that MERGE. |
| 5 | **Recovery sweep on next start** — spool **and** unsynced files | **built here** — `tortoise store-sync` (one command, both substrates) + a detached one-shot spawn from the extension's `session_start`, excluding the live session. `--exclude-session-id` and `--file` reach BOTH halves (the spool half too: filing the LIVE session there makes the server skip extraction for every later turn of it). The spool leg carries the #3615 **consent** gate — this command is a fourth upload path — while the log leg keeps the Pi seam's per-install consent; the credential is resolved ONCE and handed to both halves (in the worker's environment, never its argv), so a key from one store is never paired with a URL from another (#2369 D1.1). |
| 6 | `session_id` from the **on-disk header id only** | **built here** for the backstop; the hook already passes the session's own id. A file with no header id is **refused**, never given a generated one. |
| 7 | Sweep-vs-hook concurrency guard, server-side extraction claim + lease TTL | **NOT built by this lane** — it is a server change (a claim keyed by `_session_capture_event_id` with a lease ≥ the ~819 s extraction deadline) and it is **the next task**, not a silent drop. Until it lands, a sweep+live-hook race can run extraction twice; the captures themselves converge (idempotent MERGE). Recorded in §9. |
| 8 | Persisted **lane/provenance** marker, end to end | **landed before this lane** — `SessionRequest.capture_lane`, the set-only-when-present MERGE, `capture_spool.CAPTURE_LANES`, the TS mirror, and `test_capture_lane_3516.py`. The backstop **stamps `store_sync`**. |
| 9 | **Cursor-past-EOF / file-replaced** handling | **built here** — `planFile`: `cursor_past_eof` → re-read from offset 0; `file_replaced` (dev/ino) and `header_changed` (header sha) likewise. Never a silent skip. |
| 10 | Bounded sweep rate + spool cap | **built here for the sweep** — `MAX_FILES_PER_RUN = 50` **files shipped** per run (the remainder drains next run), `MAX_REQUESTS_PER_RUN = 200`. The cap deliberately bounds SHIPPED files, not EXAMINED ones (capping the examined prefix would starve the tail), so an up-to-date file is skipped from its `stat` identity plus its HEADER LINE: a sweep never reads a whole session file it is going to skip. The spool cap is the existing count/byte bound in `capture_spool.DEFAULT_BOUNDS` (no TTL — the owner ruled the spool is kept until the user deletes it), and its eviction is **two-class then oldest-first**: an already-filed entry's bytes are on the wire (and the harness log still holds them), so it is reaped before any unfiled entry, which is the only copy of an undelivered conversation (#6388). A quarantined `*.turns.jsonl.orphan[N]` log is reaped ahead of both classes (a corrupt meta moved it out of every scan, so it is unreachable to every surface) and its record names that as `UNREACHABLE`, leaving `unknown` where the corrupt meta (the only copy of the filing stamp) took the answer with it. |
| 11 | Backoff exhaustion retains; restarts do not multiply; permanent 4xx → stall + dead-letter, non-blocking | **built here** — retention keeps the cursor at the last good offset and persists `failures`; a permanent 4xx writes a dead-letter row (folded to one row per file, capped at 500), advances past it, and **counts the drop** so a later file still ships. `PERMANENT` is the set that will never succeed **for the same BYTES** (400/404/405/406/413/415/422). A credential refusal (401/403/407) is **RETAINED**, never crossed: it refuses the CALLER, not the bytes, so a rotated/expired key would otherwise permanently drop every pending session — the exact loss the backstop exists to prevent. The dead-letter count is also printed on the extension's `capture:` health line — and on the **Python** leg's, which is the same pinned string (a checker may run either) — since the one production caller spawns the worker with `stdio: "ignore"`. |
| 12 | Clock source per lane | **landed before this lane** (`cli_observed` / `file_mtime` / `unknown`); **extended here** with `entry_timestamp` — the store-sync lane stamps the **log entry's own** `timestamp`, in unix seconds, under its own source name so the floor can tell the three clocks apart. Server vocabulary, the `capture_spool` mirror and the TS mirror move together; a test pins the TS mirror against the server's set. |
| 13 | **ONE shared turn-window / branch contract** | **built here** as `activeBranch` + `cutAtCompaction` (leaf → root via `parentId`, cut at `compaction.firstKeptEntryId`, no positional ids ever). The Python backfill reader (`session_import.parsers.parse_pi`) is **NOT yet branch-aware** — it walks lines — so the contract has one consumer and one known non-conformant reader, recorded in §9 rather than claimed. |

### Cursor semantics under concurrency — the four pinned cases

| Case | Behaviour | Discriminating test |
|---|---|---|
| **Half-written trailing line** | The cursor never crosses a line's terminating newline, so the partial line is neither parsed (garbage) nor skipped (loss). It ships whole on the next pass. | `a partial trailing line is NOT consumed and NOT lost`; `a half-written tail neither ships garbage nor blocks the next run` (process level). Mutation control: making the cursor jump to `buf.length` reds 3 rows. |
| **Restart** | The cursor is written `tmp`+`fsync`+`rename`, with the previous good copy kept as `.bak`. A restart resumes at the same byte and **POSTs nothing** for an up-to-date file. | `the cursor SURVIVES a restart and neither re-emits nor skips`; `test_cursor_survives_a_restart_and_does_not_re_emit` (two processes, zero POSTs on the second). |
| **Corrupt cursor** | Fails **closed to the last good offset** (`.bak`), with the recovery recorded — never 0 (duplicate storm) and never EOF (silent loss) **while the backup is usable**. When BOTH copies are unusable it re-ships from offset **0** and says so: at-least-once is the contract and the duplicate is absorbed by the server's turn MERGE. A save made straight after a recovery does not copy the still-corrupt main over the backup, so the fallback survives the next checkpoint. | `a CORRUPT cursor fails closed to the last good offset (recorded, not silent)`; `a cursor from a future version is not silently interpreted` (pins the empty state that IS the double-corrupt contract); `a RECOVERY save does not overwrite the last good backup with the corrupt bytes`. |
| **Shrinking window** | A window shorter than what this file already delivered is **REFUSED**: cursor retained, nothing posted, the refusal counted (`shrunk`). Shipping it would make the turn store hard-delete the turns past the new window (#1920). The rule is not gated on a `compaction` entry — a tree append whose `parentId` bypasses the old leaf shortens the branch too — with ONE exception: a **re-anchored** plan (truncate-then-rewrite, dev/ino or header change) resets the cursor to 0 and is shipped, because that is Pi's `_rewriteFile` and the old offset is not the same index space — unless it ALSO hits the unproven arm (a `compaction` window with no provable prior delivery), which still refuses it. | `a /compact SHRINK is refused, never shipped (it would delete filed turns)`. Mutation control: delete the guard → the row POSTs. |
| **Truncation / rotation / replacement** | `cursor > file_size` (L6), an inode change, or a changed header sha ⇒ **re-read from the header and re-ship**, reason recorded. | `planFile re-anchors to the header on cursor > file_size`; `…when the file is REPLACED under the same path`; `test_a_truncated_file_is_re_anchored_not_silently_skipped`. Mutation control: deleting the past-EOF branch reds the planFile row. |

Two workers over one file cannot corrupt the cursor (single `rename`), and both may
POST the same window: the server's turn MERGE is the idempotency boundary.
`--dry-run` is genuinely read-only for **BOTH halves**: it POSTs nothing, drains no
spool entry (so no `filed_key` / backoff / terminal stamp is written — the plan
stays reproducible), moves no offset, and does not create the cursor store —
including on the branch where the window holds no shippable turn yet.

### The sweep's VERDICT — "delivered nothing" is never `OK`

The status seam exists because the one production caller spawns the worker with
`stdio: "ignore"`, so `sweep-status.json` is the only place its outcome survives.
But a **retained** batch exits 0 with no `errors[]`, so the worker's own verdict is
*true* for a sweep that delivered **nothing**: `ok` meant "structurally able to
run", and the line read `capture: OK` over a backstop a rotated key, an exhausted
quota or a 429/5xx outage had silently dead-ended. The turns were safe (the cursor
did not move); the only user-visible surface was lying.

So the verdict is composed from what the log half actually did:

| Condition | `ok` | `withheld` token | Where it lands |
|---|---|---|---|
| nothing held back (shipped / up-to-date / awaiting a turn) | `true` | — | `capture: OK` |
| a batch the server retained (401/403/407/402/429/5xx) | `false` | `http_<status>` | `capture: …, store-sync FAILED (http_401)` |
| a retained batch with **no** status (a transport failure — DNS, refused connection, timeout) | `false` | the transport reason as the worker reported it (sanitised only where it is written to the status file) | `capture: …, store-sync FAILED (fetch failed)` |
| a window refused as shrunk (`window_shrunk`) | `true` | — (the `shrunk` count is on `stdout`/`--json`) | `capture: OK` |
| a `409` (in-flight duplicate **or** "recording disabled for this team") | `false` | `http_409` | `capture: …, store-sync FAILED (http_409)` |
| a cursor that could not be PERSISTED (`cursorWriteError`) | `false` | `cursor_write_failed` | `capture: …, store-sync FAILED (cursor_write_failed)` |
| turns DEAD-LETTERED and crossed (a permanent 4xx) | `true` | — | `capture: …, N store-sync dead-lettered` |
| the log half never ran (`worker_status`) | `false` | — | its own not-run reason, not "withheld" |

The token comes from the worker's per-file `action: "failed"` outcome: its integer
`status` when the server answered, otherwise the outcome's own `reason`. It is
derived once and rendered by BOTH legs from the same read; `main` also prints it
on `stderr` (a count that only exists under `--json` is a count nobody reads), and
the process **exit code follows the verdict** so a caller that ignores the file is
still told. `--dry-run` writes **no** status (a plan is not an outcome, and
overwriting a real failure with a plan was a bug once) and its exit code still
follows the verdict — but a plan never POSTs, so it cannot observe a retained batch:
its verdict covers only the structural failures (`worker_status`, `errors[]`).

**One refusal is deliberately NOT a failure, and the skip is PER FILE.**
`window_shrunk` refuses ONE dead file by design (shipping the shorter window would
let the turn store hard-delete past it) and the unproven refusal writes no cursor
row, so the same file is refused on every sweep for as long as it exists — 55 of
292 real session files carry a `compaction`. Failing the sweep for that would paint
a PERMANENT red over a backstop working exactly as designed, and a permanent red is
how a line stops being read. Because the skip is per file, an ACTIONABLE refusal in
the same sweep still wins: a box carrying both a compacted session and a rotated
key reports `http_401`, not the thing nobody can fix. **The known cost, stated
rather than hidden:** a box whose ONLY withheld state is a compacted refusal reports
`capture: OK` while that one dead session's turns do not ship — the refusal is the
recorded remainder on #3515 (monotone window across `/compact`), and surfacing it as
its own count segment needs the same change on both legs (`_with_store_sync_terms`
and `captureHealthLine`), which is that fix's job, not a `FAILED` verdict's.

**`409` is deliberately NOT benign, and `no_header_id` cannot mask anything.** The
server answers 409 for two different things — the in-flight duplicate (carries
`Retry-After`) and "Session recording is disabled for this team", an org policy the
user can reverse and which never clears by retrying — and the worker cannot tell
them apart (`postCapture` keeps no headers and the worker forbids matching prose).
Calling the code benign would report `capture: OK` for ever on a box whose recording
is switched off, so it is surfaced: fail-CLOSED, and self-clearing (an in-flight
collision clears on the next sweep; a disabled toggle clears when it is enabled).
`no_header_id` is different: the worker already pushes it into `errors[]`, so the
sweep is `ok: false` with that reason; but ``withheld`` is composed whenever the log
half RAN — even when `errors[]` already failed the sweep — precisely so that one
stray header-less `.jsonl` cannot turn every sweep's reason into the one thing
nobody can act on while a rotated key goes unreported on every surface the
detached caller reads. Dead-lettered turns are disclosed by their own COUNT term,
never silently: the verdict is not failed for them because a crossed file is
already reported, and failing the sweep as well would report one loss twice.

The store is **one directory, derived one way**: the CLI's own default is now
`<spool-parent>/store-sync` (it used to be `$HOME/.tortoise/store-sync`), which is
where both health-line readers look and where the extension already passed
`--cursor-dir`. A `$HOME` default made a manual sweep's verdict invisible AND gave
it a SECOND cursor over the same files — re-reading from 0 and re-shipping turns
the extension's cursor already covered, under the documented
`TORTOISE_CAPTURE_SPOOL_DIR` override. The CLI's status temp is per-process
(`sweep-status.json.tmp.<pid>`): the CLI and the extension's `noteFailure` share
that directory, and one shared temp name let a failure record be published away by
a concurrent healthy write — a lost update that reads `capture: OK`. The published
name is unchanged for both readers, so nothing can mistake a temp for the status.
The extension's own writer still uses the shared name (`tortoise-capture.ts` is
the pinned install seam, and moving its bytes for this would tell every installed
box the contract changed); that residual is extension-vs-extension only — two Pi
processes starting together can swap which FAILED *reason* is shown, never clear
the failure.

Tests: `test_a_RETAINED_batch_is_not_reported_OK`,
`test_a_shrunk_REFUSAL_does_not_fail_the_sweep_but_an_ACTIONABLE_one_still_wins`,
`test_a_409_is_SURFACED_because_one_of_its_two_meanings_is_a_policy_refusal`,
`test_an_ACTIONABLE_refusal_outranks_a_no_header_id_error`,
`test_a_no_header_id_ONLY_sweep_still_reports_no_header_id`,
`test_a_cursor_that_could_not_be_PERSISTED_is_a_withheld_verdict`,
`test_the_default_cursor_dir_is_BESIDE_the_spool_like_both_readers`,
`test_the_status_temp_path_is_PER_PROCESS`. Each fails with its fix reverted, and for
the last two the REVERTED WRITER is what reds them (not the helper): the
`withheld`/`ok`/exit-code assertions, the per-file skip ORDER (mixed sweep →
`http_401`), the 409 surfacing and its self-clearing, the errors-masking rule (the
header-less file SORTS FIRST, so `errors[0]` is the reason it must outrank), the
cursor-write verdict, the persisted status landing at the spool-derived path
instead of `$HOME` (it drives `main`), and the two pids' temp names surviving a
refused rename (it drives `write_sweep_status`).

---

## 6. Verification — two parts, and both can fail

A probe is a synthetic POST. It proves **transport** — network, auth, endpoint,
payload shape — and cannot prove the hook fires on a real session. So the
verification is two parts, and **one without the other is the failure this issue
exists to end**.

### Part (i) — transport, with a server-side read-back

*Fire the install probe; require **2xx AND a server-side key read-back**.*

Measured live on 2026-10-23 against `api.premiselabs.co` through the **shipped**
`postInstallProbe`: `{"ok":true,"status":200}`. The read-back half is asserted at
process level against a real HTTP collector
(`test_part_i_install_probe_reaches_the_server_and_is_read_back`: the server holds
`{"harness":"pi"}`, which is the evidence — the client's own success is not).

### Part (ii) — store-proven capture of a REAL session

*After the first real session: assert a synced record whose `session_id` equals the
header `id` of an actual `~/.pi/agent/sessions/**/*.jsonl`, stamped with the
**hook** lane (not the backstop alone).*

The three additions that make it **falsifiable** (all pinned in the issue):

- **(a) Negative control** — with capture disabled/uninstalled the check MUST FAIL.
  A check that also passes with capture off measures the worker, not the install.
- **(b) Timestamp floor** — the matched record's `client_captured_at` must be
  strictly after the install time (±5 min tolerance), with a pre-install negative
  fixture and a floor-mutation control (delete the floor ⇒ the verdict must change).
- **(c) Hook liveness** — a store-sync-only run with the hook stubbed no-op must
  report the hook **NOT live**. Only the persisted `capture_lane` makes this
  falsifiable; a lane-absent record reports **not confirmed**, never "live".

**Status, stated precisely — this is NOT a completed part (ii).**

- **(a), (b) and (c) are NOT BUILT.** No negative control, no pre-install floor
  fixture, no floor-mutation control, and no hook-liveness check exists in the
  tree. §9 carries them as the next task with the same names the issue uses.
- What DOES exist is the **first half of part (ii)'s evidence**, at process level:
  a real file from `~/.pi/agent/sessions/` reaches a real collector with
  `session_id == header.id` and `capture_lane == "store_sync"`
  (`test_part_ii_a_real_pi_session_file_is_captured_and_attributed`). That proves
  the **backstop** captures a real session and attributes it. It does **not**
  prove the **hook** is live — the assertion in that test is
  `lane == "store_sync"`, i.e. exactly the record part (ii) says must NOT be
  sufficient on its own. Read it as the backstop's proof, not as part (ii).
- The row **skips** when the machine has no Pi session directory, so on a bare CI
  runner it contributes nothing. It is an ops check there.

**Status on this machine:** the live attempt is **environmentally blocked**, not
failing — see §8. It is quoted there rather than reported as a pass.

---

## 7. Install — the symlink requirement, and what it became

The issue asks: *enforce symlinked installs and assert the resolved target at
startup, because hand-copied directories diverge silently on agent-infra updates.*

That requirement targeted the **agent-infra-hosted** extension. After #3512 the Pi
producer is **product-owned** (`tortoise/pi-hooks/tortoise-capture.ts`, shipped
inside the Python package and installed by `HARNESS_INSTALL.pi`), and the
divergence it feared is now **structurally impossible in the same way**: the
installed file is a **copy of a shipped artifact**, and the installer asserts its
bytes (`capture_install` atomic write + byte-equality `status`) so a drift is
*reported*, not silent. Making it a symlink into `site-packages` would be worse: an
upgrade that moves the package would dangle the symlink and lose capture entirely.

What remains and is **not** done here: an explicit **startup assert of the resolved
path** on the *user's* machine (as opposed to the installer's byte check). It is
recorded in §9 — it needs a decision about fail-loud vs warn on a machine whose
extension was installed by an older product version, which is a product-policy
decision, not a mechanism.

**#10241 (2026-10-29) — success is now VERIFIED, but the measured gap is not
closed.** The byte check above was the *pre-write* comparison
(`_is_regular_unchanged` answers "should I write?"), so an install could report
`installed …` — and `tortoise install pi` could print
`Pi capture extension installed.` — from its own write INTENT, over bytes it had
never read back. `capture_install.assert_seam_target` now runs after every real
`_install_pi` write (and on the no-op path), with `require_present=True` so a
missing file after a claimed write is a FAILURE rather than "another check owns
that": it RESOLVES the path Pi will load, sha256s it, compares that with the
shipped artifact, and fails the install with a message naming **both** paths (plus
the resolved target whenever resolving changes the path — a symlink, or a
symlinked ancestor such as macOS `/tmp`) and **both** digests. The generation
marker is read only to say which WAY the bytes differ, so an install from a newer
CLI is not accused of running older logic and told to downgrade. It also names
the REMEDY the installer accepts: for a seam symlinked outside the install home
the unconditional "re-run `tortoise install pi`" would refuse (`_symlink_escape`),
so the message hands out the unlink step first — computed from that same guard,
never re-derived (#5351, #4680).

**What that closes, stated exactly.** It closes "success claimed from write
intent": the verdict is now a fact about the bytes on disk, so a write that never
lands, a concurrent writer, and a future early return cannot be reported as an
install. On the ordinary paths it is a *tautology, on purpose* — the no-op path
already compared bytes in `_is_regular_unchanged`, and the write path lands
exactly `payload` via `os.replace` or raises (an immutable target raises
`PermissionError`, reported as `install failed … NOT fully installed`).

**What it does NOT close is the divergence measured above, and this section must
not be read as closing it.** The reference box's installed seam was generation 14
(149,736 B) against a shipped generation 15 (152,752 B) because that seam was
installed by an OLDER CLI and **no install ran on it since** — the very condition
install-time verification cannot observe. Pre-PR, `tortoise install pi` on that
box wrote generation 15 and printed `installed …`; post-PR it writes generation 15
and prints the same. The user-visible outcome for the measured state is therefore
UNCHANGED, and it stays open on **#10241** with the startup surface below.

The assertion never raises and never blocks: a non-regular artifact (a FIFO,
which `open` would block on) gets a sentence rather than a read — the same
report-without-reading posture `detect_artifact_install` takes — and an
unsearchable path or a symlink loop gets a sentence too. Presence and following
are errno-discriminating stats, because the detector's own probe does not survive
those two states: on an UNSEARCHABLE install directory it propagates
`PermissionError` on Python 3.12 and (worse) folds `EACCES` into "absent" and
reports a false `missing-artifact` on 3.14 — both inside this repo's
`requires-python = ">=3.12"` — and for a symlink LOOP it reports
`symlinked-artifact` / "is a broken symlink", naming a target that exists. That is
**#10317** (filed, not widened into this PR). What is still open is
only the STARTUP surface — reporting the same divergence at session start rather
than at the next `tortoise install pi` — which keeps the fail-loud-vs-warn decision
above; it stays on **#10241**, not on a closed issue.

---

## 8. GUARANTEED vs NOT GUARANTEED

| GUARANTEED by the hybrid | NOT GUARANTEED |
|---|---|
| Per-turn durable local record (hook path) | Anything on **SIGKILL / power loss** — no shutdown hook, and Pi never `fsync`s (L5) |
| Every session file shipped, even after kill/crash (store-sync) | Turns before the **first assistant message** — the file does not exist yet (L2, measured) |
| Ship resumes from a per-file cursor after restart, and does not re-emit | **`--no-session`** sessions — nothing persisted, out of scope (L9) |
| At-least-once delivery, idempotent server-side (`{session_id}_t{i}`) | Exactly-once delivery (explicitly not attempted) |
| Recovery sweep drains spool + unsynced files on next start | A **tree** log shipped by a naive byte cursor without branch awareness (L4 — solved here by the branch contract, not by the cursor) |
| A permanently-refused file is dead-lettered and **counted**, never blocking later turns | Extraction exactly once under a sweep-vs-hook race (piece 7 — next task) |

### Environmental constraints measured on this machine (NOT architecture)

- **Hosted capture is OFF on this box**: the second producer (`reflect-hook`) reports
  `Hosted tortoise capture OFF (cloud-not-enabled)` — its config carries
  `"cloud": false`. Live, this session.
- **The hosted API refuses captures with a quota ceiling**: a real store-sync attempt
  on a real session file answered
  `HTTP 402 — {"code":"quota_exceeded","message":"Team points limit reached: 25000 in use + 6 estimated for this capture exceeds 25000 …"}`.
  The worker did the right thing: **cursor retained at 0, `dropped: 0`,
  `deadLettered: 0`** — a 402 is a refusal of the moment, not of the bytes, so the
  turns survive and ship when the ceiling lifts. A capture path that only works
  because a quota happens to be available is not the pinned architecture; this is
  recorded as an environment fact, never designed around.
- The `tortoise` **Python CLI** has no credential on this box (`~/.tortoise/credentials.json`
  absent), so `tortoise session probe` refuses; the Pi extension's own config path is
  what part (i) used.

---

## 9. Deliberately NOT built (the four exclusions), and how they would be earned

Each is a thing this architecture could plausibly grow. Each is **not** built, and
the price of building it is named, because this repo has repeatedly paid for
machinery built *around* behaviour instead of making the behaviour trustworthy.

1. **Exactly-once semantics.** Not built. At-least-once + an idempotent server MERGE
   on `{session_id}_t{i}` already converges, and the residual duplicate costs one
   MERGE write. *To be justified:* a measured case where a duplicate is **not**
   absorbed — e.g. a side effect per POST that the MERGE does not cover (extraction
   is the known one, and it is piece 7's claim, not a reason for exactly-once).
2. **A distributed broker / queue.** Not built. The substrate is a file on one
   machine and the consumer is a process on the same machine; a broker adds a
   service and a failure mode to a problem that has one writer and one reader.
   *To be justified:* more than one shipping host needing to consume one session's
   log, or a measured fan-out the local sweep cannot bound.
3. **Per-turn `fsync`.** Not built. Pi itself does not `fsync` (L5), so fsyncing our
   own spool would raise durability *above* the source of truth and buy nothing
   against the death mode the backstop exists for (process death is already
   covered by the page cache; power loss loses Pi's log too, so the log half has
   nothing to ship). *To be justified:* a measurement showing the spool survives a
   failure the log does not — i.e. we would have to be *strictly* more durable than
   Pi for a reason the product can name.
4. **Event sourcing.** Not built. There is no replay requirement, no audit
   requirement at this layer, and no consumer asking for a fact log; capture is a
   delivery problem, and delivery needs a cursor and an idempotency key, not a
   ledger. *To be justified:* a named consumer that must reconstruct "what did this
   agent know at time T" — which would be a **different** product surface
   (`docs/architecture/STORAGE-ARCHITECTURE.md` is its home), not a capture change.

**And a fifth non-build, made explicit because it is the one this issue's own
brief warns about:** no **scheduler or poller**. The sweep runs on a session start
and on demand; a loop that polls the session directory is machinery standing in for
a behaviour (`session_start` already happens) and would compete with the hook for
the server's admission pool.

### Recorded remainders (not silent drops)

These all stay on **#3515**, which this PR references (`Refs`, not `Closes`) for
exactly this reason: they are the issue's own Indicators, and closing it while they
are unbuilt would read as done.  ⚠️ **#3515 has since been CLOSED** (2026-10-24,
when #9788 landed), so this list is the historical ledger, not a live pointer — the
current home of each remainder is named in its bullet where one exists.  **A
remainder without a live home is not done; it is unowned**, and the startup bullet
below is the one that matters here.

- **Piece 7** — the server-side extraction claim + lease: the next task. Its test
  set is already named in the issue.
- **Piece 13's second consumer** — `session_import.parsers.parse_pi` (the backfill
  reader) is not branch-aware. It is a **different invocation** (an explicit
  import), so it does not overwrite live turns; making it call the same contract is
  a follow-up.
- **Startup resolution assert** on the user's machine — §7. **Now held on
  #10241**: its install-time assertion landed (`capture_install.assert_seam_target`),
  which verifies a write THIS install performed and therefore cannot see a seam
  installed by an older CLI — the state §7 measured — so it does not close this
  bullet; and the startup half waits on the fail-loud-vs-warn decision §7/§9 record
  as the owner's. Closing #10241 without it would re-orphan the criterion it was
  filed for.
- **Part (ii)'s falsifiers (a)/(b)/(c)** — §6.
- **A monotone window across a `/compact`** — §4's recorded consequence. The
  deletion path is **narrowed, not closed**: the worker refuses a shrinking window
  AND refuses a compacted window it has never shipped, so the reachable residual is
  a file whose window grows back (a `compaction` entry that is no longer present in
  the active branch) over turns the server already holds. What remains is the design
  choice between posting the union and a server-side monotone window (a non-zero
  `turn_offset` append), plus the sticky-refusal cost recorded in §4.
- **Two operators over one cursor directory** — the sweep is spawned per
  `session_start` with no singleton or lock. Two workers cannot corrupt the cursor
  (one `rename`) and both may POST the same window (the server MERGE absorbs it), but
  a fleet box pays the sweep N times concurrently. Bounded, not fixed.
