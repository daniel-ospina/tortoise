# Tortoise for OpenClaw (#2120)

The two-way volunteering-memory seam for [OpenClaw](https://openclaw.ai):
per-turn why-context injection **in**, session capture **out**.

Install it with the product — do not copy files by hand:

```bash
tortoise install openclaw            # writes ~/.openclaw/extensions/tortoise/
tortoise session verify --harness openclaw
tortoise sessions import --harness openclaw
```

`tortoise install openclaw` copies this directory to
`~/.openclaw/extensions/tortoise/` (OpenClaw's documented global discovery
path — see `docs/plugins/manifest/package-json.md`) and merges the plugin's
config block into `~/.openclaw/openclaw.json`. Nothing auto-registers: the
install IS the W4 exposure opt-in.

## Verified contract

OpenClaw has **no shell-hook surface**, so this seam is a native plugin, not a
`volunteer-turn.sh` registration. The two hook points this plugin uses:

| Direction | OpenClaw hook | Payload this plugin reads | Output |
|---|---|---|---|
| Inject | `before_prompt_build` | `event.prompt` (string) | `{ prependContext }` |
| Capture | `llm_output` | `event.prompt`, `event.assistantTexts` | one JSONL spool file per session |

Primary sources: `docs/plugins/hooks/prompt-and-session.md` (hook semantics),
`src/plugins/hook-types.ts` (`PluginHookBeforePromptBuildEvent`,
`PluginHookLlmOutputEvent`), `docs/plugins/manifest.md` (`openclaw.plugin.json`).

The earlier `platform-seams.md` row for #2120 (`~/.openclaw/*.claw` +
`handle_AgentRequest`) named a contract that does not exist; it was corrected on
2026-10-11 against the upstream repository.

## Behaviour

* **Fail-open.** Every handler catches. Unreachable API, missing key, timeout,
  empty graph or nothing above the confidence gate ⇒ nothing is injected and the
  turn proceeds untouched. A reflex failure can never break an OpenClaw turn.
* **Cross-turn dedupe.** The same injected block is never repeated within one
  session key.
* **No credentials required for capture.** Captured turns go to a local spool,
  **one file per session** — `$OPENCLAW_STATE_DIR/tortoise/turns/<session>.jsonl`
  (default `~/.openclaw/tortoise/turns/`) — which
  `tortoise sessions import --harness openclaw <file>` parses, so consent,
  receipts, provenance and REPHRASE dedup stay in the one existing import
  pipeline. One file per session is load-bearing: `sessions import` files one
  session per file, so a shared spool would file every session as one merged
  conversation.

```bash
for f in ~/.openclaw/tortoise/turns/*.jsonl; do
  tortoise sessions import --harness openclaw "$f"
done
```

Ingestion inherits the shared import posture rather than re-implementing it: the
explicit-consent gate, the 2xx-only receipt, the idempotency key, and the #4714
spool-and-retry for a retryable refusal (402/408/425/429/5xx) all apply
unchanged. Like every sibling harness (`codex`, `pi`, `cursor`) ingestion is an
explicit step: OpenClaw's hook cannot POST on the user's behalf without a key in
the plugin config, so the plugin files nothing to the network itself.

## Config (in `~/.openclaw/openclaw.json`, `plugins.entries.tortoise.config`)

| Key | Default | Meaning |
|---|---|---|
| `command` | `$TORTOISE_COMMAND` | Local `tortoise` executable to run the reflex through (preferred — no key leaves the machine) |
| `apiKey` | `$TORTOISE_API_KEY` | Key for the hosted reflex (`POST /v1/context`) |
| `apiUrl` | `$TORTOISE_API_URL` or `https://api.premiselabs.co` | Hosted API base |
| `timeoutMs` | `800` | Per-turn reflex budget |
| `capture` | `true` | Append turns to the capture spool |

## Test

```bash
node --test tortoise/openclaw-plugin/index.test.mjs
```

`tests/test_openclaw_seam.py` runs the same suite and pins the installer and the
spool parser against it.
