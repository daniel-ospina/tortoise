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
