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

1. Go to **<https://tortoise.premiselabs.co/signup>** (Supabase sign-up — email or social login).
2. After signup, the welcome page shows your API key (starts with `tt_`) — copy it right away. It's shown **once** (like `POST /v1/team/keys`), so store it somewhere safe; if you lose it, create a new one via `POST /v1/team/keys`.

### Zero-email signup (CLI)

No email or dashboard? `tortoise signup` mints a free hosted team + key in one command and saves it to `.tortoise` in the current directory:

```bash
tortoise signup
# ✅ Free team created: agent-abc123
#    API key: tt_...
#    Config saved to .tortoise (shown once — store it)
```

2 free anonymous teams per IP per 24h (3rd → 429 with a retry window); on a shared network or need more? Contact <support@premiselabs.co>.

## 2. Connect your agent (MCP over HTTP)

The hosted endpoint is `https://api.premiselabs.co/mcp/`. The transport is **Streamable HTTP**, and in
client JSON config its value is `"http"`.

> ⚠️ Never set an entry's `type` to `"streamable-http"`. That is a Claude Code alias for this
> transport, not a second one: Cursor's CLI can silently drop a whole config file that uses it,
> leaving you with no error and no connection. If your client requires a `type`, use `"http"`;
> Cursor and Pi omit `type` entirely and infer the transport from `url`.

Auth is a Bearer header that reads your key from the environment — never paste the literal key into a
config file, because config files get committed.

**Claude Code** — add to `.mcp.json` in your project. Claude Code requires `"type": "http"` (a `url`
entry with no `type` is read as stdio and the server is skipped):

```json
{
  "mcpServers": {
    "tortoise": {
      "type": "http",
      "url": "https://api.premiselabs.co/mcp/",
      "headers": {
        "Authorization": "Bearer ${TORTOISE_API_KEY}"
      }
    }
  }
}
```

A project-scope `.mcp.json` stays **⏸ Pending approval** in Claude Code until you
approve it once — start `claude` in the project and allow the prompt, or run
`/mcp` (`claude mcp reset-project-choices` resets the choice).

**Cursor** — add to `.cursor/mcp.json`. Cursor expands `${env:…}` and infers the transport from `url`,
so the entry carries **no** `type`:

```json
{
  "mcpServers": {
    "tortoise": {
      "url": "https://api.premiselabs.co/mcp/",
      "headers": {
        "Authorization": "Bearer ${env:TORTOISE_API_KEY}"
      }
    }
  }
}
```

Export the key you were shown in step 1 so the header resolves — the config file holds the *reference*,
not the secret:

```bash
export TORTOISE_API_KEY="tt_..."   # the key from step 1; add to your shell profile to persist
```

**Codex** instead:

```bash
codex mcp add tortoise --url https://api.premiselabs.co/mcp/ --bearer-token-env-var TORTOISE_API_KEY
# then export TORTOISE_API_KEY=tt_YOUR_KEY in your shell
```

Restart your client. You should now have Tortoise's tools (create points, query the graph, run evidence propagation, capture sessions).

Rate limit: **100 requests/min per key**.

### Already installed? Re-run the installer

The installer is idempotent **and it is the upgrade path** — re-running it is how an existing setup picks up a
newer standing block, a corrected instruction, or new skill text. Nothing else rewrites them: for a Codex
project the installer owns the `AGENTS.md` block, and a block written by an older version would otherwise be
re-read in every future session, forever.

```bash
curl -fsSL https://app.premiselabs.co/install-tortoise-skills.sh | bash -s -- --harness codex
```

It replaces only what sits between its own `<!-- tortoise:start -->` / `<!-- tortoise:end -->` markers — **one
well-formed pair** — so the text you wrote around them is kept as written (two line-level caveats: a final line
with no newline gains one, and a CRLF file gets the block's own line endings, so a mixed-ending file stays mixed).
It prints which block version you ended up on, and what it did to get there:

| it prints | it means |
|---|---|
| `created (block vN)` | there was no Tortoise block to upgrade — no `AGENTS.md`, or one without markers |
| `upgraded (unversioned → vN)` | provisioned before versions existed — this is the migration |
| `upgraded vM → vN` | an older block was replaced |
| `refreshed (vN — same version, updated text)` | the version matched but the text did not |
| `already current` | the block on disk is byte-identical to the one just built, ignoring line endings (CRLF vs LF) |
| `written at vN (the file had vM …)` | your installer is OLDER than the block (a cached or rolled-back copy) — it says so instead of calling a downgrade an upgrade |
| `written at vN (the file's version "vM" is not a number this installer can compare …)` | the version line was malformed (e.g. `v2-rc1`); nothing is claimed about which is newer |
| `written at vN (… "vM" is too large to compare safely)` | the version has more than 18 digits |
| `MALFORMED … NOT rewritten` | the marker lines are not one clean start→end pair — two copies, a stray marker, or one with different internal spacing or an invisible character; it refuses rather than duplicate the block, prints per-marker counts (`start markers: N (first at line L), end markers: M (first at line …)`, plus how many are only indented / trailing-spaced / BOM-prefixed and how many are spacing-mangled), names the cause it actually found, and exits 3 — the SKILLS are installed at that point, only the block is not. A marker inside a fenced code block is an EXAMPLE: every fence CommonMark recognises (backticks or tildes, 3+, indented up to 3 spaces, a longer fence may contain a shorter one) is skipped for the count AND for the version read, the comparison and the rewrite, so documenting the markers does not block the install and the example is preserved verbatim. An UNCLOSED fence is refused ONLY when the blocking question is which markers are real: with a MALFORMED marker state the installer says `An UNTERMINATED code fence is hiding part of this file` and exits 3 without writing, because the markers below the fence cannot be told apart from an example. With NO markers at all there is no ambiguity to resolve — the block is appended where the file ends, which is INSIDE the unclosed fence, and the installer warns loudly (`the Tortoise block will be written inside it and read as code, not as instructions`) rather than refusing: refusing would change what the pre-#3429 installer did. Close the fence before re-running. A block that already sits BELOW the open fence is likewise written inside it: it is replaced in place, and the installer says so |

`already current` is a statement about the **text**, not the version number, so it is safe to trust. To see what
you have without changing anything:

```bash
sed -n '/<!-- tortoise:start -->/,/<!-- tortoise:end -->/p' AGENTS.md | grep -o 'tortoise:block v[0-9]*'
```

Empty output with both markers present means the block predates versioning — re-run the installer to upgrade it.
(A match found elsewhere in the file, outside those markers, means nothing: only the block's own version counts.)

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

### Session capture requires explicit consent

Filing a transcript to Tortoise Cloud is **off by default**, and what gates it
is **per-surface**, not uniform (#3615):

- **The consent-gated in-repo paths** fail closed on the explicit `TORTOISE_CAPTURE=1` opt-in
  (`tortoise/capture_consent.py`), which is credential-independent — exporting
  `TORTOISE_API_KEY` for the MCP `Authorization` header (section 2) does **not**
  enable capture there; it only authenticates the connection. The gated paths
  are the Claude Code `session-end.sh` hook (plus `tortoise session drain`,
  which its `SessionStart` counterpart runs in the background), `tortoise
  session capture` / `tortoise sessions import`, and the SDK's client
  transmission (`TortoiseSDK.commit_session` → `POST /v1/sessions/commit`).
- **The hosted MCP tool `tortoise_session_capture` is not gated by this
  variable** — it executes on the server, which cannot read the client host's
  `TORTOISE_CAPTURE`. It carries the server-side recording policy
  (`session_recording`, default-ON) instead. This is **decided, not open**
  (#3662, Option 1): an explicit, agent-invoked capture is itself the agreement
  to record the session — the client's switch cannot travel with the request,
  so requiring it here is unanswerable — and the tool description tells the
  agent to disclose the filing to you before it happens. This is a deliberate
  departure from the per-session-opt-in default; see the `OVERRIDES:` marker on
  #3662. `TortoiseSDK.capture_session` is likewise ungated,
  and it is **not** egress-free: it writes the graph backend named by
  `TORTOISE_DB_URI` **and** sends each turn verbatim to the configured BYOK
  extractor provider (BYOK is the documented default), so a remote
  `redis://`·`rediss://` backend is itself a data-egress choice and the
  provider leg transmits regardless of which backend is named.
- **The ambient indexer sweep is not gated, and it does egress.** The Claude
  Code `session-end.sh` hook runs `tortoise index directory <corpus> --metadata`
  **unconditionally** — outside its `TORTOISE_CAPTURE` branch — and
  `tortoise/session_indexer.py` posts conversation-derived text to an
  OpenAI-compatible endpoint whenever `OPENAI_API_KEY` is set (the one other
  suppression is the test-only `TORTOISE_INDEX_NO_NETWORK=1`). So everything
  above is a statement about the **consent-gated paths**; on this one an
  absolute "no transcript leaves the machine" claim is false (tracked with the
  other ungated seams, #3662).
- **The in-repo Pi capture extension is not gated either.**
  `tortoise/pi-hooks/tortoise-capture.ts` (installed by `tortoise capture
  install`, `harness: "pi"`) POSTs the conversation on `session_shutdown` and
  reads **no** `TORTOISE_CAPTURE` — installing it IS the opt-in. It differs
  from the in-repo Claude Code / Codex / Cursor hooks, which file nothing
  without this variable.
- **The Pi agent-harness `reflect-hook` is not gated that way.** It lives in
  `agent-infra` and starts hosted capture on **credential presence**, never
  reading `TORTOISE_CAPTURE` — so on a Pi host, exporting `TORTOISE_API_KEY` is
  a **data-sharing opt-in**, not a credential-only change. Open dependency:
  **agent-infra#1117**.

To let the Claude Code `session-end.sh` hook (or `tortoise session capture` /
`tortoise sessions import`) file sessions:

```bash
export TORTOISE_CAPTURE=1     # truthy: 1 / true / yes / on
```

Without it the hook files no session and prints a notice. That covers the
**capture** leg only: the same hook still runs the ambient indexer sweep
unconditionally (it sits outside the capture branch), so session content is not
necessarily machine-local — see the seam list above. The visible line repeats on
each session close while a legacy credential is present — only the durable copy
is one-time. This is a deliberate behavior
change: capture used to follow the
credential. The requirement is **host-agnostic** — it applies to a self-hosted
endpoint too (a self-hosted daemon is still data leaving the machine), so the
same opt-in is needed for `docs/quickstart-selfhosted.md`'s replay steps.
Existing installs must also re-copy the hook — `.claude/hooks/*.sh` are
per-project copies and do not update themselves:
`cp tortoise/claude-hooks/session-end.sh .claude/hooks/session-end.sh`.

A one-time notice is also written durably to
`~/.tortoise/capture-consent-notice` (a stale copied hook discards the CLI's
stderr, so the file is the channel that survives it), and `tortoise doctor`
reports the current consent state.

**Meeting transcripts:** the manual mining flow (transcript → meeting/decision/
friction events + draft Points) is a local CLI/SDK path — see the
meeting-transcripts section of [quickstart-selfhosted.md](quickstart-selfhosted.md).
Over hosted HTTP the `tortoise_mine_conversations` tool is stdio-only for
security (#1090); run `tortoise serve` locally and connect your agent to it
(stdio) to use it, or mine with the CLI against a local DB.

### The saved backlog: sessions captured without a provider key

A capture made with no LLM provider key — or while your team's extraction
toggle is off — **stores its turns and skips extraction** (#3892 / #4258). The
transcript is saved and searchable, but no memory was distilled from it, so
search feels thinner than the stored material suggests. Adding a key later does
not extract it by itself: **you choose** (#4197).

```bash
tortoise sessions backlog              # what is saved, what it would cost
tortoise sessions backlog --process    # extract it now (RECOMMENDED)
tortoise sessions backlog --future-only  # keep only future conversations
tortoise sessions backlog --forget <id>  # drop one record you cannot act on
```

`--forget` exists for the one record that has no way forward: a session that
`tortoise session drain` filed after the server stored it without extracting is
recorded **without a transcript path** (the spool keeps the turns, not the path
they came from), so `--process` can only report it as skipped. Dropping it is
your call to make explicitly — the record is the only evidence the session was
captured without extraction, so nothing removes it for you.

The report is the offer, and it prices the run **before** anything is spent
(~N estimated extraction units across M turns, with the provider that would be
used). Processing re-runs the capture for the saved transcripts through the
existing re-capture gate, so it also writes the turn vectors the dense
retrieval leg needs — the option fixes the retrieval gap it is offered for, not
just the extraction gap. `tortoise doctor` prints the same state (and the
recommendation) wherever it reports your provider key, so a backlog is visible
instead of staying mysterious.

The three states are distinguishable and the choice stays reversible: sessions
are reported as **not-processed**, **processed**, or the run is **declined**
(`--future-only`), and a later `--process` overrides a decline. The local
ledger lives beside your import receipts (`~/.tortoise/capture-backlog/`, or
under `$TORTOISE_IMPORT_RECEIPT_DIR`'s parent). It records the transcript path,
harness, turn count and estimate — never transcript content.

**Which capture paths populate it.** A deferral is recorded by `sessions
import`, by `session capture` (the Claude Code SessionEnd hook, which records
the durable Claude project transcript so a later `--process` can actually
re-read it), and by `session drain` when a session spooled by an interrupted
capture is filed and the server stores it without extracting. A *degraded*
success counts on all three — a 2xx whose body reports an extraction `error`,
an `empty` extraction, or a non-empty `errors` list stored the turns and
extracted nothing, so it is offered for a re-capture exactly like a store-only
one. The drain knows the spooled turns but not the transcript path they came
from, so its record has
no transcript path: `--process` reports it as skipped and tells you to re-run
that session's import with the transcript instead of pretending the backlog is
empty. One path does **not** write the ledger yet, so on it the report stays
silent until the session is imported explicitly: the Pi capture extension.

**This is a local record, not the server's verdict.** A session extracted by
another path since it was deferred — the hosted MCP `tortoise_session_capture`,
or `TortoiseSDK.capture_session` — is not visible in the local ledger, so a
`not-processed` entry can be stale until the next `--process` replays it.

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

Running Tortoise yourself and moving to hosted? The primary path is **`tortoise export` → hosted import**: export your selfhost graph to a versioned, encrypted artifact (`tortoise-export-v1`), then import it into a fresh hosted team via `POST /v1/organizations/{org_id}/import`. Point IDs and edge topology are preserved (belief scores are derived — EP recomputes server-side). Verified end-to-end by the **E2E-12-D** suite's `test_parity_export_import` case, which asserts structure parity (node/edge counts, Point IDs, operator topology). The manual **replay** path below remains the documented fallback (and the only path on versions without the export tool). See [quickstart-selfhosted.md](quickstart-selfhosted.md) for the daemon side.

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
   curl -X POST https://api.premiselabs.co/v1/organizations/<org_id>/import \
     -H "Authorization: Bearer <owner-session-jwt>" \
     -H "Content-Type: application/vnd.tortoise.export.v1" \
     -H "X-Tortoise-Import-Key: <key_b64>" \
     --data-binary @graph.tortoise
   ```

   Re-importing the same artifact is idempotent (`{"imported":false,"already":true}`); a failed/tampered artifact is quarantined (422) and never touches the live graph.
5. **Verify parity** — the import response's `restored` counts should match your source graph; `tortoise team info` and `tortoise context` confirm the team and its memory digest, and the MCP tools `tortoise_check_structure` (chain integrity) and `tortoise_summarize_structure` (counts: total points + per-gate breakdown) confirm the imported graph. Once hosted reaches parity, decommission the daemon at your leisure.

### Fallback: manual replay

If you are on a version without the export tool, replay your knowledge through the hosted ingest path — the path verified by the original E2E-12-D replay journey (content parity; Point IDs and edge topology are NOT carried over by replay):

```bash
export TORTOISE_CAPTURE=1                        # explicit consent (session capture)
tortoise session capture --file transcript.txt    # sessions captured while self-hosted
tortoise create-point "The decision was approved" --kind statement   # individual claims
```

(`tortoise session capture` requires the explicit consent opt-in — see
*Session capture requires explicit consent* in section 3.)

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
