---
title: "Tortoise Self-Hosted Quickstart"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-08-08
ownedBy: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-cli, tortoise-mcp
---

# Tortoise Self-Hosted Quickstart

Tortoise is a graph engine for agent memory: claims are **Points**, relationships are **edges**, and belief scores are computed by propagating evidence through the graph. You run Tortoise as a service and your tools connect to it over MCP — like running MongoDB and connecting a driver.

This guide gets you from zero to a running server in about 5 minutes. **The recommended path uses Docker** (durable, multi-writer safe); a no-Docker embedded path is available for single-agent eval only.

Prefer a managed server? See [quickstart-cloud.md](quickstart-cloud.md) — sign up, paste an API key, done. Operator/infra notes (deploying, upgrading, backing up the daemon itself): [infra-runbook.md](infra-runbook.md).

## Prerequisites

- **Python ≥ 3.12** — check with `python3 --version` (only needed for the pip/embedded path; the Docker path needs no local Python)
- **Docker** — check with `docker --version` (recommended path)
- **Git** — check with `git --version`

## Which path?

| You are… | Use… |
|---|---|
| One agent, experimenting / laptop eval | **Embedded (Option C)** — single-writer, eval only |
| A team / multiple agents / production | **Docker (Option A or B)** — durable multi-writer |
| Zero-ops | [Hosted Cloud](quickstart-cloud.md) |

## 1. Install

Pick one:

```bash
# From source (clone)
git clone https://github.com/daniel-ospina/tortoise.git
cd tortoise
pip install -e .

# Or straight from GitHub (no clone)
pip install git+https://github.com/daniel-ospina/tortoise.git
```

- **Never** run `pip install tortoise` — the name is taken on PyPI by an unrelated turtle-graphics package. This project is published as `tortoise-graph`.
- Optional embeddings (vector search): `pip install -e '.[embeddings]'`.
- Use a venv: `python3 -m venv .venv && source .venv/bin/activate`. On Homebrew and Ubuntu, PEP 668 blocks bare `pip` installs into the system Python ("externally-managed-environment") — the venv sidesteps that.

## 2. Choose a database

### Option A — Docker compose (recommended, durable)

The repo ships a complete reference topology (`docker-compose.yml`): the daemon plus a **FalkorDB sidecar** with AOF on, a named volume, a healthcheck, and `TORTOISE_DB_URI` wired. This is the durable multi-writer path — safe for teams and multiple agents writing concurrently.

```bash
docker compose up -d          # daemon on http://localhost:8000 (MCP at /mcp)
```

- The sidecar is bound to `127.0.0.1:6379` (loopback only) so host-side tools can reach it with `TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise`.
- Set a strong `TORTOISE_API_KEY` in `docker-compose.yml` before exposing beyond localhost.

### Option B — Bare container (durable variant) *(requires Docker)*

Same image, standalone — useful when you don't want the full compose stack:

```bash
docker run -d --name tortoise-falkordb -p 127.0.0.1:6379:6379 \
  -e REDIS_ARGS="--requirepass falkordb --appendonly yes" \
  falkordb/falkordb-server:latest
```

Point Tortoise at it with `TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise`.

⚠️ Auth/AOF go via the **`REDIS_ARGS` env var** — the falkordb image entrypoint ignores command-line args. A bare `--requirepass` as a run arg silently starts a passwordless sidecar.

### Option C — Embedded (single-agent eval only, no Docker)

`tortoise init` auto-creates `~/.tortoise/tortoise.db` using **falkordblite** (a self-contained, SQLite-backed FalkorDB). Nothing to run, nothing to manage — the CLI handles it.

> ⚠️ **Embedded FalkorDBLite is SINGLE-WRITER / EVAL ONLY.** Concurrent writers (multiple agents) lose data on this engine. Fine for one agent evaluating Tortoise; for a team or production use Option A/B or Cloud.

## 3. Set the DB environment variable

| Env var | Used with | Example |
|---|---|---|
| `TORTOISE_DB_URI` | Docker (Option A/B) | `docker://:falkordb@localhost:6379/tortoise` |
| `TORTOISE_DB_PATH` | Embedded (Option C) | `~/.tortoise/tortoise.db` (the default) |

⚠️ Set these in your **MCP client's `env` block** (step 5) — **not** in a `.env` file. A repo-root `.env` is only auto-loaded for editable installs; the CLI reads the process environment only. (With the compose daemon, the URI is already wired inside compose — host-side clients only need it if they talk to the sidecar directly.)

## 4. Create your first graph

```bash
tortoise init          # interactive — creates the graph, writes a welcome Point
tortoise init --yes    # same, no prompts (auto-indexes the repo you're inside, if any)
```

`tortoise init` resolves the DB target from `TORTOISE_DB_URI` (Docker) or `TORTOISE_DB_PATH` (embedded); the embedded success line labels itself single-writer eval only.

To index an existing repo's markdown files:

```bash
tortoise index github https://github.com/your/repo --db <path-or-uri>
```

`index github` clones the repo (or accepts a local path), extracts deterministically with offline mock models, and writes Points/Operators to the graph — idempotent across runs. For richer LLM-based extraction, use the standalone ingest CLI instead — `tortoise-ingest transcript.txt --db <path-or-uri>` (or `python -m tortoise.ingest`). It ingests a transcript file, requires `--db`, and defaults to offline mock models; pass `--point-model`/`--relation-model` (e.g. `ollama:llama3.2:3b`) to use a real LLM. `tortoise onboard` runs the full init → index → demo → doctor flow and passes the same resolved DB target to each step, so it works in embedded-only mode too (it used to crash; fixed in #705).

For a LOCAL corpus of `.md` files (your own notes, docs, session exports), use the
unified index path — [§4b Index a local corpus](#4b-index-a-local-corpus-of-markdown-files-the-unified-index-path) — which writes Sources + Events/Documents idempotently and is what the session-end hook's reconciliation sweep uses.

> **Indexing cost (real-world):** indexing is CPU-bound extraction, not I/O — a few hundred markdown files takes **minutes** (measured: ~280s for a 261-md repo on embedded with offline mock models). It is not hung; `tortoise onboard` Step 3 and the Q5 ingestion step run in the background until done.

## 4b. Index a local corpus of markdown files (the unified index path)

`tortoise index directory <corpus-dir> [--db URI] [--corpus-name NAME] [--metadata]`
walks a directory of `.md` files and writes **Sources + Events/Documents** (a
Source per file, plus a session/meeting Event or a Document per file) —
idempotent across runs (re-runs converge to `skipped`).

```bash
tortoise index directory ~/notes --db docker://:falkordb@localhost:6379/tortoise
# stdout: ONE JSON line = the honest summary — the machine contract
#   {"directory": ..., "corpus_name": ..., "file_count": 3, "indexed": 3,
#    "updated": 0, "skipped": 0, "failed": 0, "aborted": 0, "ignored": 0,
#    "errors": [], "by_kind": {"agentSession": 2, "document": 1}, ...}
# stderr: the human-readable rendering
```

- **Exit code 0 = the run COMPLETED** (even with `failed > 0` — per-file
  failures are *reported* in the summary, never encoded in the exit code).
  Exit 1 = a pre-walk argument error or an unreachable graph.
- **`--metadata`** (opt-in): run LLM metadata extraction + session embeddings.
  Default is the no-network mode (`extract_metadata=False`).
- **`--corpus-name`**: the corpus identity in the `corpus://<name>/…` urls.
  Default = the directory basename. **On a shared graph, give each corpus a
  unique `corpus_name`** — two corpora with the same basename collide on the
  same urls.
- **Env fallback**: with no positional dir, `TORTOISE_INGEST_BASE_DIR` is used;
  with both absent, the CLI exits 1 naming both surfaces.

### Env table (all seven indexing vars)

| Var | Default | Precedence / notes |
|-----|---------|--------------------|
| `TORTOISE_DB_URI` | (unset → embedded) | Docker URI for the durable multi-writer graph; concurrent writers verified at 2 processes |
| `TORTOISE_INGEST_BASE_DIR` | (unset → no sandbox) | SECURITY sandbox: corpus dirs + progress files + resolved symlink targets must stay under it. Not the corpus selector. A typo here = pre-walk error |
| `TORTOISE_SESSION_CORPUS` | `~/.tortoise/docs/conversations` | The session-end hook's sweep-corpus selector (passed positionally). ⚠️ The base-dir-vs-corpus confusion: setting this to the real corpus while the DB points at a test graph sweeps the whole corpus into the wrong DB |
| `TORTOISE_MAX_FILE_MB` | `50` | Two-layer size guard (float MB); at/over the limit → rejected before any read |
| `TORTOISE_EMBEDDING_REPAIR_BACKOFF_HOURS` | `24` | Across-run bound on embedding-repair retries after an outage (float hours) |
| `TORTOISE_INDEX_NO_NETWORK` | (unset) | TEST-ONLY: forces the no-network omission at the new-path boundary; never honored inside the shared embedding — the frozen legacy path still embeds |
| `TORTOISE_INDEX_CHILD_STDERR` | (unset) | Debug-redirect for the backgrounded sweep: full child output (stdout+stderr), truncate-on-open, fail-safe on relative/missing-parent targets |

### Corpus layout

- **Only `*.md` is indexed — case-sensitively.** A corpus that is mostly PDFs
  reports `{file_count: 0, ignored: N}` — a zero `file_count` on a non-empty
  dir means the corpus is not markdown.
- Default session corpus: `~/.tortoise/docs/conversations` (or
  `TORTOISE_SESSION_CORPUS`).
- Session files: frontmatter `sessionId` (or `file_<stem>` fallback). Meeting
  files: `fileType: meeting` + title/date. Everything else = document.

### Verify your index

```bash
tortoise list-sources          # flat rows: url + sourceKind + points
tortoise doctor                # health check incl. the session-indexing surface
```

`session_index_health` is **session-family-only** — meeting/doc files always
appear in `unindexed` by necessity (health buckets every sessionless file).
For mixed corpora, verify with `list_sources` (count + `by_kind`) instead;
use health for the session corpus.

### Operational gotchas (each is accept-and-document — known, pinned behavior)

1. **Embedded FTS returns `[]` silently.** The default embedded backend has no
   fulltext index — `tortoise_fts_query` returns empty results deterministically.
   Text search by title requires the server-mode (`bolt://`/docker) graph.
2. **After any `rebuild_all`, session/meeting provenance edges are gone** until
   you re-run `tortoise index directory` (the re-index is the repair oracle).
   Document edges survive rebuild.
3. **Renaming/moving a corpus forks Sources.** The `corpus://` url encodes the
   basename — a rename changes every url and doubles the Sources. Use the
   explicit `--corpus-name` to keep the identity.
4. **Resume fast-skip keys use (size, mtime).** A same-size edit within the
   same mtime second can be skipped as unchanged on a `progress_file` resume —
   re-run without the progress file to force the full read.
5. **`TORTOISE_INDEX_NO_NETWORK` is test-only.** An accidentally-set value
   disables LLM/embeddings (warnings only in `errors[]`).
6. **Deleting a corpus file leaves its Source/Event/edges live** (historical
   records — accept-and-document). Health shows no false stale/unindexed for
   it; `list_sources` still lists it.
7. **A failed background sweep is invisible by default** — the hook always
   exits 0. Set `TORTOISE_INDEX_CHILD_STDERR` proactively and check
   `list_sources`/health periodically (see below).

### Monitoring the background session-end hook

The session-end hook runs a corpus-wide reconciliation sweep at every session
close — backgrounded, always exits 0. A sweep failing every session end is
invisible by default. To monitor:

```bash
export TORTOISE_INDEX_CHILD_STDERR=~/.tortoise-index-child.log   # capture the sweep output
tortoise list-sources && tortoise doctor                         # periodic check
```

Run `tortoise doctor` after upgrades.

### Upgrading an existing hook install

Installed hooks are per-project copies (`.claude/hooks/session-end.sh`). If you
installed before the index-path migration, re-copy the current script and
verify:

```bash
cp tortoise/claude-hooks/session-end.sh .claude/hooks/session-end.sh
grep 'index directory' .claude/hooks/session-end.sh   # must match
```

The migrated script carries `# tortoise-hook-version: 2`. An un-upgraded copy
still invoking the legacy `index sessions` becomes `nohup command-not-found →
/dev/null` after the legacy CLI is removed — a silent failure.

### How to restore (backup → wipe → rebuild → re-index)

1. Back up: (1) the corpus files (source of truth), (2) the FULL `events/`
   JSONL directory (the sole replay source for Sources/Events/Documents),
   (3) the db file.
2. Restore onto a fresh graph — `rebuild_all` is line-tolerant (a torn
   trailing line from a crash is skipped, never fatal):

```bash
python -c 'from tortoise.sdk import TortoiseSDK; TortoiseSDK().rebuild_all("<events-dir>")'
```

3. Re-index the corpus:

```bash
tortoise index directory <corpus-dir>
```

4. **Verify — including an EDGE check.** `session_index_health` is edge-blind;
   declare success only after checking a recall/edge surface too:

```bash
tortoise list-sources                     # count == file_count
tortoise doctor                           # health
# edge check: a recall on an indexed url must return its neighbor
```

**Upgrading is forward-only** — there is no binary rollback: the old binary
replaying a new journal silently drops the new record kinds (and reintroduces
wipe-before-parse, turning one torn line into total loss). The restore path is
a pre-release backup per the drill above.

#
## 8. Expansion packs (optional)

Tortoise ships five starter expansion packs by default (`dev`, `marketing`,
`product-strategy`, `pm`, `agent-ops`) — YAML manifests that extend the core
ontology with a domain vocabulary, chains, and extraction guidance. The
starter set loads automatically on every install; `tortoise_packs_list` shows
your active packs.

- **Use a custom pack:** put `packs/<namespace>/manifest.yaml` under
  `TORTOISE_PACKS_DIR` and restart (missing/empty dirs warn and fall back —
  never a silent empty registry).
- **Author one:** `tortoise pack new mydomain` scaffolds from the template;
  `tortoise pack validate <dir>` checks it against the shared validator
  before you install.
- **Learn the format:** [docs/EXPANSION_PACKS.md](EXPANSION_PACKS.md) (behavior)
  + `packs/_template/manifest.yaml` (schema).

## Troubleshooting: why isn't my file indexed?

1. Re-run `tortoise index directory <dir>` manually and read the `errors[]`
   entries (each names the rel-path + a cause-class: `decode` / `size` /
   `escape` / `structural` / `filename`).
2. Check the file extension — only case-sensitive `*.md`.
3. Check `TORTOISE_MAX_FILE_MB` — an over-limit file is rejected before read.
4. Check symlinks — an out-of-base resolved target is rejected (`escape`).
5. Check health / `list_sources` for the session corpus.

### Supported topologies

- **One machine + embedded default** (eval): single-writer; two processes on
  one embedded file is the #6761 crash class.
- **One graph + N corpora**: give each corpus a unique `corpus_name`
  (same-basename corpora collide on the urls).
- **Concurrent writers**: `bolt://` (docker) — design-supported via
  MERGE-keyed writes; **integration-tested to 2 concurrent writers**. Two
  embedded DBs never sync (no replication exists); NFS/shared-volume
  multi-writer is untested.


## 5. Connect your agent (MCP)

### Docker path (recommended) — connect to the daemon

The compose daemon serves MCP at `http://localhost:8000/mcp`:

```bash
claude mcp add tortoise http://localhost:8000/mcp
```

> ℹ️ **Claude Code one-time approval:** servers registered at **project
> scope** (`.mcp.json` — `claude mcp add --scope project`, the default in
> older clients) show as **⏸ Pending approval** in `claude mcp list` until
> you approve them once — start `claude` in this project and allow the
> prompt (or use `/mcp`). The tools stay disabled until then; this is
> expected, not a failure. (The current `claude mcp add` default is *local*
> scope — active immediately, no approval.)

Or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "tortoise": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### Scripting / agent tooling — the thin Python driver (`tortoise-client`)

For Python scripts and agent integrations, install the thin driver (#526) —
it connects to the daemon over MCP and never embeds the engine:

```bash
pip install tortoise-client
# TORTOISE_MCP_URL defaults to http://localhost:8000/mcp — set TORTOISE_API_KEY if the daemon requires auth
tortoise-client status          # connectivity + tool-count probe
```

```python
# client-first import surface (tortoise.mcp_client also works)
from tortoise_client import status, call_tool
print(status())
```

### No-Docker path (single-agent eval) — stdio

Add a `tortoise` server to your MCP client's config (`.mcp.json` for Claude Code / Cursor, or the equivalent for your client):

```json
{
  "mcpServers": {
    "tortoise": {
      "command": "python3",
      "args": ["-m", "tortoise.mcp_server"],
      "cwd": "/abs/path/to/tortoise",
      "env": {
        "PYTHONPATH": "/abs/path/to/tortoise",
        "TORTOISE_DB_PATH": "~/.tortoise/tortoise.db"
      }
    }
  }
}
```

- This runs **embedded FalkorDBLite — single-writer, eval only**. A single agent is fine; two or more MCP clients sharing the embedded DB are concurrent writers and lose data. For multiple agents use the Docker path above.
- If you installed with `pip install git+...` (no clone), drop `cwd` and `PYTHONPATH` — the package is importable from anywhere.
- Inside a venv, `python3` must be the venv's interpreter (e.g. `/abs/path/to/tortoise/.venv/bin/python3`), not a different system Python.
- ⚠️ `tortoise serve` hard-exits unless `TORTOISE_DB_URI` or `TORTOISE_DB_PATH` is set — the config above always sets one.

### Dev-mode note: don't set TORTOISE_API_KEY for stdio

The stdio transport can't carry auth tokens. If `TORTOISE_API_KEY` is set, every MCP tool rejects your calls — and without `TORTOISE_SECRET_PEPPER` the server crashes at import. Leave both unset for local stdio.

**Authenticated local MCP (optional):** for real auth over localhost, use the HTTP server with tenant keys:

```bash
tortoise key create                 # prints a tt_... key once
tortoise serve --http --auth tenant # streamable-http on http://127.0.0.1:8000/mcp
```

Point your client at `http://127.0.0.1:8000/mcp` with header `Authorization: Bearer tt_<key>`.

> ℹ️ `serve --http --auth tenant` on an **embedded** DB is single-agent eval only — a durable team deployment uses Docker (Option A/B) or Cloud. (Compose users: the daemon already serves `/mcp` with auth via `TORTOISE_API_KEY`.)
> ℹ️ HTTP tenant mode uses a fresh `team_{id}` namespace — data you wrote over stdio stays in the `tortoise` graph. They're separate namespaces.

## 6. Verify and back up

```bash
tortoise doctor                                        # health check
tortoise backup --db ~/.tortoise/tortoise.db           # snapshot → backups/<timestamp>/
```

## 7. Migrating from self-hosted to cloud

Tortoise ships a first-class migration path: **`tortoise export` → hosted import**. Your graph — Points, operators, and edge topology — is exported as a versioned, encrypted artifact (`tortoise-export-v1`) and ingested by the hosted API, preserving **Point IDs and edge topology** (belief scores are derived, so EP recomputes server-side as on any surface). The journey is verified end-to-end by the **E2E-12-D** suite's `test_parity_export_import` case, which asserts structure parity — node/edge counts, Point IDs, operator topology — strictly stronger than the replay baseline below.

> ✅ **Automated export → import is the primary path.** The manual replay path below remains the documented fallback (and the only path on versions without the export tool).

**What carries over:** everything the artifact captures — Points, edges (operators), Point IDs, and edge topology. Queries, `context` digests, and the MCP tools behave identically on hosted.

**What does NOT carry over:** belief scores are not copied — EP recomputes over the imported graph. API keys are **not portable across surfaces**: a selfhost static key is rejected by hosted and a hosted key is rejected by your daemon (both 401 — keys are scoped per team/surface), so you register a fresh hosted team + key below.

### Automated path: `tortoise export` → import

1. **Export the selfhost graph** to a versioned, encrypted artifact:

   ```bash
   tortoise export --db ~/.tortoise/tortoise.db --output graph.tortoise
   # {"status":"ok","output":"graph.tortoise",...,"node_count":N,"edge_count":M}
   ```

   The artifact is encrypted by default (AES-256-GCM). Set `TORTOISE_BACKUP_KEY` (base64 32-byte) to use a key you control; otherwise the CLI generates a fresh key and prints it once as `key_b64` on the stdout JSON line — **keep it safe, you need it to import**. `--no-encrypt` exists but warns loudly (plaintext graph on disk).

2. **Register a hosted account** — [tortoise.premiselabs.co/signup](https://tortoise.premiselabs.co/signup) (Supabase sign-up), or mint a free hosted team + key with no email:

   ```bash
   tortoise signup
   # ✅ Free team created — API key printed once, saved to .tortoise
   ```

3. **Connect a working directory to cloud**:

   ```bash
   tortoise init --api-key tt_<your-key>   # saves .tortoise config in this directory
   ```

4. **Import the artifact** into the team graph (owner session auth — the import endpoint is owner-scoped, like export):

   ```bash
   curl -X POST https://api.premiselabs.co/v1/teams/<team_id>/import \
     -H "Authorization: Bearer <owner-session-jwt>" \
     -H "Content-Type: application/vnd.tortoise.export.v1" \
     -H "X-Tortoise-Import-Key: <key_b64>" \
     --data-binary @graph.tortoise
   # {"imported":true,"already":false,"id":"<sha>","restored":{"nodes":N,"edges":M}}
   ```

   Re-importing the same artifact is idempotent (`{"imported":false,"already":true}` — no double-swap), and a failed/tampered artifact never touches the live graph (quarantined, 422).

5. **Verify parity** — the counts returned by import (`restored`) should match your source graph, and the structure tools confirm it:

   ```bash
   tortoise doctor          # selfhost health
   tortoise team info       # hosted team + usage
   ```

   Then call the structure tools over MCP on each surface — `tortoise_check_structure` (chain integrity) and `tortoise_summarize_structure` (counts per gate) — and compare the hosted counts to your selfhost graph. When hosted reaches parity and answers your queries, decommission the daemon at your leisure.

### Fallback: manual replay

If you are on a version without the export tool, or you prefer to re-create knowledge rather than copy the graph, replay it through the hosted ingest path. Your selfhost daemon keeps serving the graph while you replay, and hosted answers with content parity (Point IDs and edge topology are NOT carried over by replay).

1. **Keep your selfhost daemon running** — the graph stays live and queryable while you set up cloud:

   ```bash
   docker compose up -d        # or leave your embedded DB in place
   tortoise doctor             # confirm the local graph is healthy
   ```

2. **Replay the knowledge** through the hosted ingest path (run from the connected directory):

   ```bash
   # Sessions/transcripts you captured while self-hosted
   tortoise session capture --file transcript.txt

   # Individual claims (or bulk via REST POST /v1/points or the SDK)
   tortoise create-point "The decision was approved" --kind statement
   ```

3. **Verify parity** — same checks as the automated path; replay reaches content parity (every replayed knowledge item present on hosted), the automated path additionally preserves Point IDs and edge topology.

## Meeting transcripts — manual ingestion

Beta flow (R4c): turn a meeting transcript into **Events + draft Points** with
source provenance. Mining is **manual by design** — you run it; sessions
capture is the automatic path. The extractor is deterministic and offline
(no LLM call): every `Speaker: text` line becomes a draft Point, and the
session produces ≥3 events: a **meeting** event, **decision** events (decision
language), and **friction**/**milestone** events (contradictions).

**Try it with the bundled sample** (repo checkout — `tests/sample_transcript.txt`):

```bash
tortoise mine-conversation tests/sample_transcript.txt \
  --source-id 2026-08-14-sync
```

Output:

- `mine-<source-id>.jsonl` — the mining event log in the current directory
- A printed summary: events (gate: ≥3), draft points, operators, and the
  per-event kind list

**Mine into your graph** — add `--db` to project Points into FalkorDB
(embedded path: `--db ~/.tortoise/tortoise.db`; Docker URI: `--db
docker://:falkordb@localhost:6379/tortoise`). Points land as **draft** in a
W-3-gated batch — the SDK/MCP path returns `batch_status` in its result; the
CLI prints event/point/operator counts (watch for `FalkorDB unavailable`
warnings, which mean projection failed and you're in log-only mode). Review
and promote drafts when you're ready (`tortoise_promote_point` MCP tool /
`TortoiseSDK.promote_point`).

**Your own transcript:** any plain-text file with `Name: statement` lines.
The sample transcript is an 8-line sync between `Connor` and `Spencer` that
demonstrates the full mix (meeting + decision + friction).

**Batch / SDK / MCP:**

- SDK: `TortoiseSDK(db_path).mine_corpus(directory)` — batch-mine a folder of
  transcripts (ingest + mine per file, resume + dedup built in).
- MCP (stdio): `tortoise_mine_conversations(transcript=..., source_id=...)` or
  `corpus_dir=...` for a batch. Hosted HTTP excludes
  `tortoise_mine_conversations` entirely (both forms, security #1090) — run
  `tortoise serve` locally (stdio) for either.

Mining details and the W-3 batch gate: `tortoise/mining.py`
(`ConversationMiner`, `mine_conversation`, `mine_corpus`); the onboarding
skill teaches the same flow at `tortoise/onboarding/SKILL.md` (the
AGENT_ONBOARDING.md prompt it replaced is archived under
`tortoise/onboarding/archive/`, M8).

## Troubleshooting

- **`pip` refuses to install ("externally-managed-environment")** — you're on Homebrew/Ubuntu system Python. Create and activate a venv first (step 1).
- **MCP client can't find the module / tools never load** — the `python3` in the MCP config is a different interpreter than the venv you installed into. Use the venv's absolute path.
- **`tortoise serve` exits immediately with "Neither TORTOISE_DB_URI nor TORTOISE_DB_PATH is set"** — set one in the MCP client's `env` block (step 5).
- **Port 6379 already in use** — another container/process (local redis, another compose stack) is on it. Remap the sidecar: `-p 127.0.0.1:16380:6379` (Option B) or edit the compose `ports:` entry, then use `docker://:falkordb@localhost:16380/tortoise`.
- **Upgrading** — clone: `git pull && pip install -e .`; direct install: `pip install -U git+https://github.com/daniel-ospina/tortoise.git`.
- **`tortoise doctor` shows a Docker ❌** — expected in embedded-only mode (no container running). The graph-health check runs against the resolved DB target; `doctor --db <uri|path>` and `doctor --path` explicitly target a specific DB, and the bare `doctor` invocation works without extra flags.

## 8. Beta feedback & bug reports

Part of the beta cohort? Bugs and feedback go through two channels (see [beta-feedback.md](beta-feedback.md) for the full guide and triage path):

- **Bug / unexpected behavior** → [file a bug report](https://github.com/daniel-ospina/tortoise/issues/new?template=bug_report.yml) (structured form: surface, expected vs actual, graph JSON)
- **Questions, ideas, general feedback** → [GitHub Discussions](https://github.com/daniel-ospina/tortoise/discussions)

Reports are acknowledged within 2 business days.
