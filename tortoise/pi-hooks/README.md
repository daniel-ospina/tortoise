# tortoise/pi-hooks — the Pi capture-install seam

This directory is the **Pi leg of the capture-install seam** (#3575). Pi's only
hook surface is an extension (it has no `settings.json` shell-hook mechanism
like Claude Code), so the Pi seam is an in-repo extension instead of shell
scripts.

## Artifact

| File | Role |
|---|---|
| `tortoise-capture.ts` | the extension — install-probe on `session_start`, session filing on `session_shutdown` |
| `tortoise-capture.test.ts` | behavioral tests (`node --test tortoise/pi-hooks/tortoise-capture.test.ts`) |

## Install (done by the product, not by hand)

`HARNESS_INSTALL.pi` (and `HARNESS_CAPTURE_INSTALL.pi`) copy this file into
Pi's global extension directory:

```bash
mkdir -p ~/.pi/agent/extensions
# #3713 collision guard (see below): disable a pre-existing tortoise-capture/
if [ -L ~/.pi/agent/extensions/tortoise-capture ]; then
  rm ~/.pi/agent/extensions/tortoise-capture
elif [ -d ~/.pi/agent/extensions/tortoise-capture ]; then
  mv ~/.pi/agent/extensions/tortoise-capture ~/.pi/agent/extensions/.tortoise-capture.disabled
fi
cp <path-to-tortoise>/tortoise/pi-hooks/tortoise-capture.ts \
   ~/.pi/agent/extensions/tortoise-capture.ts
```

Pi auto-discovers `~/.pi/agent/extensions/*.ts` on the next start. Installing
the extension *is* the opt-in — there is deliberately no `autoCapture`-style
default-false flag — and it is the one IN-REPO capture seam that reads no
`TORTOISE_CAPTURE`, unlike the in-repo Claude Code, Codex and Cursor hooks,
which file nothing until the machine sets `TORTOISE_CAPTURE=1` (#3615). (The
agent-infra `reflect-hook` is a **second producer** and lives outside this repo:
until agent-infra `7e77470e4` (2026-10-16, #803) it uploaded on key presence
alone, and it now shares `extensions/shared/capture-gate.ts` — an explicit
`"cloud": true` opt-in *and* a key. The disposition of that copy, the measured
proof that it is **real code and not a symlink**, and **every module that must
move with the consolidation**, are in §Migration below.) The
server can still refuse the capture POST with a
409 while the organization has agent sessions switched off (Memory sources >
Agent sessions; that toggle is default-ON, ToS-covered, and can only refuse —
never enable).

## Migration: the install must not leave two capture producers (#3713, #3512)

Pi's extension loader (`collectAutoExtensionEntries`) enumerates directory
entries and does **no basename dedupe**: a top-level `tortoise-capture.ts` and
a `tortoise-capture/index.ts` are **two** independent extensions. On a host
that already has the legacy agent-infra extension at
`~/.pi/agent/extensions/tortoise-capture/` (a symlink to
`agent-infra/extensions/tortoise-capture/`, which registers its own
`agent_end` capture gated on `autoCapture`), installing this seam **alongside**
it makes both producers POST `/v1/sessions` for the same `session_id` —
doubled work, and the server's in-flight dedup hands the loser a 409.

The install step therefore disables the legacy entry before copying this one:

- a **symlink** is unlinked (`rm`, no `-r`) — the agent-infra checkout it
  points at is untouched;
- a **real directory** is moved to `~/.pi/agent/extensions/.tortoise-capture.disabled`
  — a dot-prefixed name the loader skips (`entry.name.startsWith(".")`), so
  its files are preserved but it no longer registers.

It never runs `rm -rf`. If you deliberately keep **both** producers, the
collision is the runtime problem tracked by **#3713** — the artifact-side
hardening (treat the server's `409 already in flight` as a benign "another
producer has this session_id" rather than `was NOT filed`, and/or a
server-side producer/idempotency key) belongs to that issue, **not** to this
install guard.

### The second producer, and the disposition of the agent-infra copy (#3512)

The guard above covers **one** of the two producers agent-infra installs. The
other is a **top-level file**, `~/.pi/agent/extensions/reflect-hook.ts` — a
second, independent extension whose only handler is the `session_shutdown`
(`quit`) capture: it writes a JSONL fallback into
`~/.tortoise/session-events/` and POSTs the same `/v1/sessions` endpoint.

**Its egress gate changed under this record.** Until agent-infra `7e77470e4`
(2026-10-16 UTC, agent-infra #803 / #848) `reflect-hook` uploaded whenever the API
key was present; it now shares `extensions/shared/capture-gate.ts` with the
other recorder — an explicit `"cloud": true` in the operator config *and* a
key, with env (`TORTOISE_CAPTURE_CLOUD=0`) and a repo-local
`{"cloud": false}` able to **deny only**. Re-measured **2026-10-17** against
`agent-infra@origin/main`, so that a lane does not have to trust a description:

| In `agent-infra` | Measured 2026-10-17 |
|---|---|
| `extensions/tortoise-capture/index.ts` + `index.test.ts` | **real files** (33,983 B + 39,290 B) — **not** a shim, **not** a symlink |
| `extensions/reflect-hook.ts` + `reflect-hook.test.ts` | **real files** (16,758 B + 21,584 B) — **not** a symlink |
| `extensions/shared/capture-gate.ts` | **added by agent-infra#803** — the one egress gate both recorders now share; imported by both recorders and by both recorder suites (`reflect-hook.test.ts`, `tortoise-capture/index.test.ts`) |
| `extensions/shared/capture-attribution.ts` | imported by both recorders and by `reflect-hook.test.ts` — no other extension imports it |
| CI | `reflect-hook.test.ts` has its own step in agent-infra's `.github/workflows/ci-main.yml` (agent-infra#611), and both `shared/*.test.ts` suites run through that job's `extensions/shared/*.test.ts` glob; `tortoise-capture/index.test.ts` is executed by **no** agent-infra workflow — the extension glob is `extensions/*/test*.mjs`, which does not match a `.ts` file |
| Ship-list | `manifest.json` lists **both** (`reflect-hook.ts`, `tortoise-capture/`), so `bin/agent-infra.js` materializes both on a farm machine |

**What must move with the consolidation — the complete inventory, measured.**
Both recorders import **both** shared modules: `extensions/tortoise-capture/index.ts`
imports `../shared/capture-attribution.js` + `../shared/capture-gate.js`, and
`extensions/reflect-hook.ts` imports `./shared/capture-attribution.js` +
`./shared/capture-gate.js`; **no other agent-infra extension imports either**.
A reducer who moves only `capture-attribution.ts` — the single module this
record named before `7e77470e4` added the gate — leaves both recorders
importing a module that is no longer beside them: exactly the "capture
installed but unwired" window the PR ordering in
`daniel-ospina/agent-infra#1671` exists to prevent.

**The modules' own suites move with them, and each recorder's suite with its
owner.** The inventory above is the runtime half; the test half is the same
pair, and it is CI-executed: `extensions/shared/capture-gate.test.ts` (20,873 B)
and `extensions/shared/capture-attribution.test.ts` (18,073 B) both import the
module they sit beside, and both run through the `extensions/shared/*.test.ts`
glob in `ci-main.yml`. Move the two `.ts` modules and leave these two behind and
agent-infra's CI goes red on a missing import — the mirror of the unwired
window, in the test half. Each recorder's own suite
(`tortoise-capture/index.test.ts`, `reflect-hook.test.ts`) moves with **its**
body, i.e. with the consolidation that is **#3515**'s.

**The disposition (recorded decision for #3512):** the agent-infra copy is **kept
as a shim** — it is reduced to a pointer at this directory and carries **no
capture code of its own**, so it cannot drift; the farm workflow keeps working
without a second code path. **Deletion is a later step, not this decision**, and
its trigger is the first product release that ships this extension as the
install source. The shim's "source of truth is `tortoise/pi-hooks/`" marker
**cannot live in the shim itself** — a symlink has no content — so it belongs in
a **sibling file** beside it.

> **Current state: the shim is the TARGET, not a description of the repo.**
> Measured 2026-10-17 (table above), **no copy is a shim yet** — both recorders
> are real files on `agent-infra@origin/main`. Their CI coverage is **not**
> equal, and the table above says so: `reflect-hook.test.ts` has its own
> `ci-main.yml` step, while `tortoise-capture/index.test.ts` is executed by
> **no** agent-infra workflow (the extension glob is `extensions/*/test*.mjs`,
> which does not match a `.ts` file). Reading "kept as a shim" as already-done
> is how a lane skips the reduction and leaves two live producers; the
> reduction itself is `daniel-ospina/agent-infra#1671`, and it is **open**.

The cross-repo deliverable (the shim, the dev-machine repoint and the **PR
ordering** so no window exists where capture is installed but unwired) is
**daniel-ospina/agent-infra#1671**. The **consolidation of the two recorder
bodies into ONE** is **#3515**'s — the hybrid architecture is what defines the
single recorder's shape — and the product-side handling of a live **second**
producer (this one, and the legacy directory above) is **#3713**'s.

The product half is already landed: the extension here is the artifact
`capture_install._install_pi` writes, it ships in the wheel, and
`tests/test_pi_capture_disposition.py` pins the **README** half of the record
(`#3512`) against this tree, so a `tortoise/pi-hooks/` edit cannot name a home
that does not exist. (The plan's own copy of the record is covered by the #3554
carve-out in `tools/ci_selection.py`: a plan-only edit selects `core` and runs
`test_pi_capture_disposition.py`, which pins the plan's copy too.)

## Verification — what is executable, what is manual-only

**This section is the canonical statement of the Pi verification status** — procedure, actor, pass
condition and blockers. Where another artifact summarizes it, this section governs.

### Executably verified (hermetic, runs in CI)

| Check | What it proves |
|---|---|
| `node --test tortoise/pi-hooks/tortoise-capture.test.ts` | the extension's full hermetic suite — `extractTurns`, truncation, payload, credential precedence, the spool, and the real `session_start` / `session_shutdown` handlers fired against a mock `pi` with an injected `fetch` (no network, no LLM) |
| `tests/test_pi_capture_hooks.py` | the source pins **plus the installed artifact**: it installs the seam into a temp `HOME` and loads/fires the file `capture_install` writes, asserting the capture receipt. A sibling anti-vacuity test proves that check would fail on a non-self-contained install |

`tests/test_pi_capture_hooks.py` is registered under `core` (and `onboarding`); a change under
`tortoise/pi-hooks/` selects `core` via the `tortoise/` fallback, so the guard runs on the PR that
edits the seam.

The `node`-backed checks need **Node ≥ 22.18** (default-on TypeScript type stripping and module-syntax

detection — this suite passes **no** `--experimental-strip-types` flag, so its floor tracks the version
where stripping became the default rather than the version that first accepted the flag).
Locally they *skip* when Node is missing or older, because the source-level pins above still ran. In
**CI the two installed-artifact checks FAIL instead of skipping** — they are the only executable proof
that the seam works at its install location, so a runner that cannot run them must fail by name rather
than report green with the check silently absent. The gate is self-enforcing: it fails whenever `CI`
is set, so no CI lane can execute this file without a usable Node and still report green — which is why
the lane that provisions Node (`actions/setup-node@v4`, Node 22) is not restated here as a claim to
keep in sync. The shape is the one `tests/test_pack_shipping_wheel.py` uses for its toolchain-gated
pack gate; the fail-never-skip ruling it serves is `bff_test_helpers.require_toolchain` (#3501), which
always fails and takes an explicit named opt-out (`AUTH_ALLOW_NO_TOOLCHAIN`) rather than a CI branch.
The pre-existing source-suite check (`test_extension_behavioral_suite`) keeps its own skip contract and
is deliberately **not** routed through that gate.

### Manual-only

**That a real `pi` process loads the installed extension and calls `turn_end` / `session_shutdown`
against the live API.** This needs a live harness and an LLM call, so it is not CI-able.

Procedure — run at release/installation time by a maintainer with a live `pi` install and a capture
credential (at 2026-09-22, the **B1 lane**, which owns objective 1's exit evidence:
`~/.pi/agent/state/lane-reports/B1-LIVE-FOUR-HARNESS-2026-09-22.md`):

1. Ensure `TORTOISE_API_KEY` is set and the organization's Agent-sessions toggle is **on** (otherwise
   the server refuses the capture with a 409 and no receipt prints).
2. Run a non-dogfood session with only this seam loaded:

   ```bash
   pi --no-extensions -e ~/.pi/agent/extensions/tortoise-capture.ts -p "<trivial prompt>"
   ```

   `--no-extensions` disables discovery **and** settings-registered extensions, so the probe is
   single-producer even on a host carrying the legacy `tortoise-capture/` (`#3713`).
3. **Pass condition = `retrievable` — read the specific captured content back.** A row in
   `GET /v1/sessions` alone proves only `captured` (the write landed), never `retrievable`. The
   authoritative session-scoped read is `GET /v1/sessions/{id}` (turns + extracted points). The
   alternative the scoping evidence measured — `GET /v1/search?q=<a distinctive phrase from the
   captured turns>` — is a **graph-wide** ranked read, not a session read: it proves `retrievable`
   only when a hit's `sessionId` equals the probed session's id, never from a non-empty result set
   alone. The `[tortoise-capture] captured session(s) → <apiUrl> (filed N≥1)` line is
   supporting evidence (a 2xx is implied, not printed).
4. **A read-back 504 is `UNMEASURABLE` — never PASS, never FAIL** (the read path is query-dependent,
   `#4661`). **No receipt line while the session IS present in `GET /v1/sessions` is the `#4675`
   post-commit-504 false negative, not a seam failure** — never score it FAIL; attempt the content
   read-back, else `UNMEASURABLE`.

### Blockers on a live verdict

- **`#4661`** — the read path 504s; a read-back is UNMEASURABLE.
- **`#4675`** — a post-commit 504 is indistinguishable from a pre-commit one, so the client records
  no receipt for a session that DID land (the client's terminality rule).
- **`#3713`** — launch-blocking for the claim *"Pi capture works"* (recorded decision
  2026-09-22T17:20Z), **not** for a clean-machine install; its sequencing blocker `#3971` has merged
  (`73acefddf`).

Until `#4661` and `#4675` are resolved the live leg can yield **no verdict**, so objective 1's
done-state must not read as verified. Automating the probe into `session verify` is **`#4710`**; the
version-contract route to stale-install detection is **`#4680`**.

## Configuration

The extension reads `TORTOISE_API_KEY` / `TORTOISE_API_URL` from the
environment (both are already required by the Pi MCP setup), falling back to
`~/.pi/agent/tortoise-config.json` (`apiKey` / `apiUrl`). The key and the URL
are **co-sourced** (mirroring `tortoise/__main__.py::_resolve_config_path`,
#2369 D1.1): an env key may use the env URL, but a **file**-sourced key
always resolves its URL from the same file or the built-in default — a
poisoned `TORTOISE_API_URL` can never redirect a stored credential (and the
captured conversations) to another host. No local `tortoise` CLI or Python
install is required, and there is **no `agent-infra` dependency** —
`agent-infra` is not shipped to users.

## Relation to the other capture surfaces

- `tortoise/claude-hooks/` — the Claude Code seam (SessionStart/SessionEnd
  shell hooks).
- `tortoise-capture.ts` (here) — the Pi seam. It POSTs to the same
  `/v1/sessions` endpoint with the same `harness` / `session_id` /
  `conversation` shape as `tortoise session capture`, so server-side
  idempotency and extraction are identical.
- `tortoise sessions import --harness pi --file <session.jsonl>` — historical
  backfill (separate from live capture).

## Capture reliability: the durable spool (#3963)

Capture is **not** session-end-only. The extension keeps the cadence the field
converges on — **cheap capture frequently, costly extraction deferred**:

| Pi event | Action |
|---|---|
| `session_start` | install probe **and** replay: file any session still in the spool (an interrupted/laptop-closed session is filed here) |
| `turn_end` | **append** the new turn(s) to the local spool (no network) |
| `agent_end` | snapshot + retry **other** sessions' pending filings (never the live one) |
| `session_shutdown` | snapshot the final turns, then the **final flush** |

The spool lives at `~/.tortoise/capture-spool/` (override
`TORTOISE_CAPTURE_SPOOL_DIR`): per session a small meta file plus an
**append-only** `*.turns.jsonl`. It is bounded by **count and bytes** — never a
TTL: #3870 (owner) ruled the spool is kept until the user deletes it, so a
count/byte ceiling evicts the oldest entry and **records the reason** in
`discarded.jsonl`.

Idempotency is `session_id` (the server's upsert key) plus a content-addressed
client key `sha256(session_id \0 sha256(turns))`, so replaying the same spool
files one session and issues one POST. Transient failures (network / 5xx /
retryable 4xx / #3713's in-flight 409) back off and retry; a permanent 4xx is
recorded **with a reason** and the entry is marked terminal and KEPT (never
unlinked — #7708), so a refused capture is never silently destroyed. A terminal
entry is skipped by later drains; clear the mark with
`tortoise session drain --retry-terminal` once the refusal's cause is fixed.

The Claude Code leg implements the same contract in `tortoise/capture_spool.py`,
wired through `tortoise session capture` (write-before-upload) and
`tortoise session drain` (run by `session-start.sh`). See
`docs/plans/2026-09-17-3963-capture-spool.md`.

### The drain is bounded, and a blocked box says so (#2100)

A capture feature is observability: it must never be able to make a machine's
capacity unusable. It could, and did — agent-infra#2100 measured one cloud cell
whose spool had grown to 3.3 MB / 320 **quota-deferred** entries, where every
lane produced 259–262 B of banner-only output and looked dead while two sibling
boxes ran normally.

Three properties close that, and they are all pinned in the suite:

* **Nothing the handlers do AFTER the durable snapshot runs on the tick Pi
  awaits.** `session_start` and `agent_end` write their snapshot inline (that is
  the #3963 durability guarantee — a kill must not lose the turn) and then only
  *schedule* (`setImmediate`) the health line and the replay. Fire-and-forget
  alone was not enough: an `async` body runs synchronously up to its first
  `await`, so `void flush(…)` still charged the spool scan and its turn reads to
  the turn's critical path.
  `session_shutdown` is the exception by nature — Pi runs it before exit — so
  there the bounds are what protect the exit, and the *live* session is filed
  first (one `onlySessionId` pass) rather than ranked behind older entries it
  can only be filed by shutdown.
* **One pass is bounded.** At most `FLUSH_MAX_ATTEMPTS` (8) POSTs and
  `FLUSH_BUDGET_MS` (5 s) of wall clock, checked between entries (never
  mid-POST, so a completed attempt's outcome is always recorded). Entries the
  pass never reached stay queued — counted as `remaining` and continued in the
  background on an `unref()`d timer, so a pending continuation can never keep a
  finished Pi process alive either. Removal on the bound is the spool's own
  count/byte ceiling, unchanged.
* **A quota refusal is a NAMED state, not a growing backlog.** The first 402
  carrying one of the server's categories (`quota_exceeded` for the plan
  allowance, `cohort_cost_cap` for the org spend cap — the two are named
  separately, because a spend cap reported as "quota" sends the owner to buy a
  plan that cannot lift it) stops the pass (the remaining entries cannot
  succeed either) and is recorded in `<spool>/status.json`, so the *next*
  process reports it without re-POSTing to rediscover it. A filed entry clears
  the marker — a stale `BLOCKED` would be its own lie — and so does observing
  nothing pending at all (see below).

Every `session_start` prints exactly one health line, which is the per-box
surface an operator (or a lane log that produced 259 B) reads:

```
capture: OK (0 queued)
capture: DRAINING (12 queued)
capture: BLOCKED (320 queued, 402 quota)
capture: INACTIVE (no TORTOISE_API_KEY, 3 queued)
capture: UNREADABLE (cannot list …/capture-spool/entries)
```

`queued` is **pending** work — recomputed from the spool on every call as
entries that are neither FILED (the server already accepted exactly that
content; the entry is kept, not unlinked) nor TERMINAL (kept, never retried —
#7708). Counting raw meta files would count spool *occupancy*, which only grows
on a healthy box: `OK (0 queued)` would never print and `BLOCKED` could never
clear. The recorded marker is only trusted while something is still pending, so
a quota that has since lifted, or a spool another leg has drained, cannot keep
claiming a block — nothing pending reads `OK` and clears the marker.

The Python leg's `flush_spool` carries no attempt cap or budget of its own: it
runs on explicit commands (`session capture`, `session drain`), never on a turn
path — `claude-hooks/session-start.sh` backgrounds the drain with
`nohup … >/dev/null 2>&1 &`, which is why this defect was Pi-only. It shares
everything else with the Pi leg, because both write the same spool **and the
same `<spool>/status.json`**: the same named `blocked` state (same two 402
categories, classified by STATUS and the server's code, never by prose) and the
same health line, byte for byte (a parity test executes this TypeScript against
a directory the Python leg wrote). That is deliberate — two records, or two
spellings, would let the two surfaces of one box disagree about whether it is
blocked, which is the false PASS #2100 removes.

Operators reach the same line from the CLI: `tortoise session health` prints it
(local-only, no credential, always exits 0 — the blocked box is exactly the one
that must be able to answer), `tortoise doctor` carries it as a `Capture spool`
row (`❌` blocked, `⚠️` unreadable, `ℹ️` draining/inactive — a pending capture on
a live box and a keyless install are normal states, and painting them as
warnings is how a real block gets ignored), and a hand-run `session drain` prints
it to stderr when the spool is blocked or unreadable.
