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

Pi auto-discovers `~/.pi/agent/extensions/*.ts` on the next start. Capture is
**on by default** (ToS-covered, the same default as the Claude Code hooks); the
server refuses the capture POST with a 409 while the organization has agent
sessions switched off (Memory sources > Agent sessions). There is no
`autoCapture`-style default-false flag: installing the extension *is* the
opt-in.

## Migration: the install must not leave two capture producers (#3713)

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
discarded **with a recorded reason**, never silently.

The Claude Code leg implements the same contract in `tortoise/capture_spool.py`,
wired through `tortoise session capture` (write-before-upload) and
`tortoise session drain` (run by `session-start.sh`). See
`docs/plans/2026-09-17-3963-capture-spool.md`.
