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
cp <path-to-tortoise>/tortoise/pi-hooks/tortoise-capture.ts \
   ~/.pi/agent/extensions/tortoise-capture.ts
```

Pi auto-discovers `~/.pi/agent/extensions/*.ts` on the next start. Capture is
**on by default** (ToS-covered, the same default as the Claude Code hooks); the
server refuses the capture POST with a 409 while the organization has agent
sessions switched off (Memory sources > Agent sessions). There is no
`autoCapture`-style default-false flag: installing the extension *is* the
opt-in.

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
