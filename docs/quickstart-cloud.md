---
title: "Tortoise Hosted Quickstart"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-08-08
ownedBy: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-cloud, tortoise-mcp, tortoise-rest
---

# Tortoise Hosted Quickstart

Tortoise is a graph engine for agent memory: claims are **Points**, relationships are **edges**, and belief scores are computed by propagating evidence through the graph. The hosted service at **api.premiselabs.co** runs it for you — no install, no ops. You connect your agent over MCP and start filing decisions and observations.

Want to run it yourself instead? See [quickstart-selfhosted.md](quickstart-selfhosted.md).

## 1. Sign up and get your API key

1. Go to **https://tortoise.premiselabs.co/signup** (Supabase sign-up — email or social login).
2. After signup, the welcome page shows your API key (starts with `tt_`) — copy it right away. It's shown **once** (like `POST /v1/team/keys`), so store it somewhere safe; if you lose it, create a new one via `POST /v1/team/keys`.

### Zero-email signup (CLI)

No email or dashboard? `tortoise signup` mints a free hosted team + key in one command and saves it to `.tortoise` in the current directory:

```bash
tortoise signup
# ✅ Free team created: agent-abc123
#    API key: tt_...
#    Config saved to .tortoise (shown once — store it)
```

2 free anonymous teams per IP per 24h (3rd → 429 with a retry window); on a shared network or need more? Contact support@premiselabs.co.

## 2. Connect your agent (MCP, streamable-http)

The hosted endpoint is `https://api.premiselabs.co/mcp/`, and it only speaks **streamable-http** — that's the only correct hosted pattern. Auth is a Bearer header with your API key.

Add this to your client's `.mcp.json` (Claude Code, Cursor, and most MCP clients read this file):

```json
{
  "mcpServers": {
    "tortoise": {
      "type": "streamable-http",
      "url": "https://api.premiselabs.co/mcp/",
      "headers": {
        "Authorization": "Bearer tt_YOUR_KEY"
      }
    }
  }
}
```

**Codex** instead:

```bash
codex mcp add tortoise --url https://api.premiselabs.co/mcp/ --bearer-token-env-var TORTOISE_API_KEY
# then export TORTOISE_API_KEY=tt_YOUR_KEY in your shell
```

Restart your client. You should now have Tortoise's tools (create points, query the graph, run evidence propagation, capture sessions).

Rate limit: **100 requests/min per key**.

## 3. Use the CLI (optional, for scripting)

```bash
pip install git+https://github.com/daniel-ospina/tortoise.git

tortoise init --api-key tt_YOUR_KEY    # connects this directory to Tortoise Cloud
```

⚠️ `tortoise init --api-key` saves the config (a `.tortoise` file with your key) to the **current directory** — run the other CLI commands from that same directory.

Smoke test:

```bash
tortoise team info       # shows your team and usage
```

Everyday commands:

```bash
tortoise create-point "The API keys are stored client-side" --kind statement
tortoise session capture --file transcript.txt     # file your agent sessions
tortoise session list                               # what's been captured
tortoise context                                    # memory digest for session-start hooks
```

**Meeting transcripts:** the manual mining flow (transcript → meeting/decision/
friction events + draft Points) is a local CLI/SDK path — see the
meeting-transcripts section of [quickstart-selfhosted.md](quickstart-selfhosted.md).
Over hosted HTTP the `tortoise_mine_conversations` tool is stdio-only for
security (#1090); run `tortoise serve` locally and connect your agent to it
(stdio) to use it, or mine with the CLI against a local DB.

## 4. Use the REST API

Base URL `https://api.premiselabs.co`, header `Authorization: Bearer tt_YOUR_KEY` on every request.

| Method & path | Purpose |
|---|---|
| `POST /v1/points` | Create a Point |
| `GET /v1/points` | List Points |
| `GET /v1/points/{id}` | Fetch one Point |
| `GET /v1/search?q=<query>` | Search the graph |
| `GET /v1/team` | Team info and usage |
| `POST /v1/sessions` | Create / capture a session |
| `GET /v1/sessions` | List sessions |
| `GET /v1/context` | Memory digest for the current team |
| `POST /v1/team/keys` | Create an API key |
| `GET /v1/team/keys` | List API keys |
| `DELETE /v1/team/keys/{key_id}` | Revoke an API key |

```bash
curl -s https://api.premiselabs.co/v1/team \
  -H "Authorization: Bearer tt_YOUR_KEY"
```

## 5. Manage API keys

- **Create:** `POST /v1/team/keys` — the plaintext key is shown **once** in the response; store it immediately.
- **List:** `GET /v1/team/keys` — see all keys for the team (plaintext is not returned again).
- **Revoke:** `DELETE /v1/team/keys/{key_id}` — instantly invalidates that key. Losing a key? Revoke it and create a replacement.

## 6. Migrating from self-hosted to cloud

Running Tortoise yourself and moving to hosted? The primary path is **`tortoise export` → hosted import**: export your selfhost graph to a versioned, encrypted artifact (`tortoise-export-v1`), then import it into a fresh hosted team via `POST /v1/teams/{team_id}/import`. Point IDs and edge topology are preserved (belief scores are derived — EP recomputes server-side). Verified end-to-end by the **E2E-12-D** suite's `test_parity_export_import` case, which asserts structure parity (node/edge counts, Point IDs, operator topology). The manual **replay** path below remains the documented fallback (and the only path on versions without the export tool). See [quickstart-selfhosted.md](quickstart-selfhosted.md) for the daemon side.

> ✅ **Automated export → import is the primary path** — replay remains supported as a fallback.

**What carries over:** Points, edges (operators), Point IDs, and edge topology. Queries, `context` digests, and the MCP tools behave identically here.

**What does NOT carry over:** belief scores are not copied — EP recomputes over the imported graph. API keys are **not portable across surfaces**: a selfhost static key is rejected by the hosted API and a hosted key is rejected by your daemon (both 401 — keys are scoped per team/surface), so register a fresh hosted team + key below.

### Step-by-step

1. **Export the selfhost graph** (run on your selfhost machine):

   ```bash
   tortoise export --db ~/.tortoise/tortoise.db --output graph.tortoise
   ```

   Encrypted by default (AES-256-GCM). Set `TORTOISE_BACKUP_KEY` (base64 32-byte) to use a key you control, or keep the `key_b64` the CLI prints once on its stdout JSON line — you need it to import.
2. **Register a hosted account** — [tortoise.premiselabs.co/signup](https://tortoise.premiselabs.co/signup), or from the CLI: `tortoise signup` (mints a free hosted team + key, no email).
3. **Connect a working directory**: run `tortoise init --api-key tt_<your-key>` from the directory you'll use.
4. **Import the artifact** into the team graph (owner session auth):

   ```bash
   curl -X POST https://api.premiselabs.co/v1/teams/<team_id>/import \
     -H "Authorization: Bearer <owner-session-jwt>" \
     -H "Content-Type: application/vnd.tortoise.export.v1" \
     -H "X-Tortoise-Import-Key: <key_b64>" \
     --data-binary @graph.tortoise
   ```

   Re-importing the same artifact is idempotent (`{"imported":false,"already":true}`); a failed/tampered artifact is quarantined (422) and never touches the live graph.
5. **Verify parity** — the import response's `restored` counts should match your source graph; `tortoise team info` and `tortoise context` confirm the team and its memory digest, and the MCP tools `tortoise_check_structure` (chain integrity) and `tortoise_summarize_structure` (counts per gate) confirm the imported graph. Once hosted reaches parity, decommission the daemon at your leisure.

### Fallback: manual replay

If you are on a version without the export tool, replay your knowledge through the hosted ingest path — the path verified by the original E2E-12-D replay journey (content parity; Point IDs and edge topology are NOT carried over by replay):

```bash
tortoise session capture --file transcript.txt    # sessions captured while self-hosted
tortoise create-point "The decision was approved" --kind statement   # individual claims
```

For bulk, use the REST API (`POST /v1/points`) or the SDK — both accept the same content.


## 6.5 Expansion packs (optional)

Tortoise ships five starter packs by default (`dev`, `marketing`,
`product-strategy`, `pm`, `agent-ops`) — declarative YAML that extends the
core ontology with domain vocabulary, chains, and extraction guidance.
`tortoise_packs_list` shows your active packs.

- **Install a custom pack per team:** `POST /v1/packs/manifests` with the
  manifest YAML (or the `tortoise_pack_install` MCP tool) — validated against
  the shared schema; ontology-only v1 (no connectors/tools on tenant packs).
  Reserved starter namespaces are rejected.
- **Author one:** same manifest format as self-host
  ([docs/EXPANSION_PACKS.md](EXPANSION_PACKS.md)).

## 7. Beta feedback & bug reports

Part of the beta cohort? Bugs and feedback go through two channels (see [beta-feedback.md](beta-feedback.md) for the full guide and triage path):

- **Bug / unexpected behavior** → [file a bug report](https://github.com/daniel-ospina/tortoise/issues/new?template=bug_report.yml) (structured form: surface, expected vs actual, graph JSON)
- **Questions, ideas, general feedback** → [GitHub Discussions](https://github.com/daniel-ospina/tortoise/discussions)

Reports are acknowledged within 2 business days.
