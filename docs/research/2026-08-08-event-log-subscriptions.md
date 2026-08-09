# Research: Event-Store Placement + Delivery Semantics for #432 Subscriptions

**Date:** 2026-08-08
**Domain:** engineering / architecture (Complicated)
**Context:** #432 (subscriptions + claim lifecycle) scoping decision — where the durable per-tenant event log lives, and delivery/retention semantics. User asked for best-practice + internal-setup research before recommending.
**Verifier note:** cycle 1 — fresh-session verifier caught 1 P1 (fly.toml mount fact) + 3 P2s (recommendation justification, SDK method name, missing adversarial) — fixed. Cycle 2 — 3 new P2s (emit-hook overclaim, unframed cost figure, stale line citations) — fixed in this revision.

## Reframed Problem

"[Implementer] trying to [pick where the #432 subscription change-feed is durably stored] but [the scoping default — per-team JSONL files — carries durability/ops risk on the hosted Fly deployment] which results in [a notification/audit system that can lose history or require a second data plane to operate]."

## What We Have Internally (verified)

| Finding | Evidence |
|---|---|
All three surfaces (MCP, hosted REST, local) **route through `TortoiseSDK`** — MCP wraps it (`mcp_server.py:14`), hosted REST instantiates it (`hosted_api.py:31/45`). → **Design requirement:** adding ONE emit hook to TortoiseSDK yields a uniform write path. This hook does NOT exist today — it is a known gap (sdk.py GAP-07 TODO "emit EventRecorded for provenance", ~:2168). The only `_emit` today is on EventAPI (`api.py:42`), used by CLI/ingest paths (`ingest.py`, `__main__.py`) writing to the EventLog JSONL — which hosted tenants never touch and which stays as-is. | `tortoise/mcp_server.py:14`; `hosted_api.py:31/45`; SDK methods `create_point` (sdk.py:382), `create_operator` (sdk.py:794), `supersede_point` (sdk.py:695), `annotate_operator` (sdk.py:857); `add_operator` exists only on EventAPI (`api.py:126`); GAP-07 TODO (sdk.py:2168) |
| Hosted container has a **mounted volume**: `[[mounts]] source="tortoise_api_data" destination="/data"` (fly.toml:44-46, added 2026-08-05). `/data` survives deploys but is a **single-machine** Fly volume. | `fly.toml`; `hosted_api.py` `/data` volume fallback comment |
| The repo already acknowledges embedded-DB loss on deploy: "Embedded redislite has no persistent volume → all data lost on deploy." | `sdk.py:189` |
| Production durability is exclusively FalkorDB Cloud (AOF + automated backups + multi-tenancy); entrypoint **fails loudly** if the URI is missing in prod. 2026-08-05 data-loss incident was the non-durable self-hosted sidecar (AOF off, no backups). | `entrypoint.sh` |
| Each team gets its own FalkorDB graph namespace at provisioning. | `hosted_api.py` provision: `db.select_graph(graph_name)` per team (lines 428/467/1077/1104) |
| Existing EventLog JSONL (`tortoise/log.py`) is append+read_all only, CLI/ingest path only — hosted tenants never touch it. | `log.py`; #432 scoping |
| `shared_state/` has flock (`locked_append`), dedup (`recovery.dedup_events`), EventCodec machinery. | `shared_state/event_log.py`, `concurrency.py` |
| Domain queries are **label-scoped** (`MATCH (p:Point ...)`, `MATCH (n:Point ...)`); the only label-less scans are WHERE-filtered (`(n:Object OR n:Document)`) or health checks (`RETURN count(n) LIMIT 1`). EP propagates over Point relationships. A `:GraphEvent` node with **zero relationships** is a graph island — invisible to traversals, label-scoped queries, and EP. | `sdk.py` query layer (lines 355/416/466/532/1369/2260/1774); `tortoise/ep.py` |
| No `TORTOISE_API_KEY` in this session → epistemic-graph checkpoint skipped gracefully. | — |

## External Findings (with confidence tiers)

**[HIGH — 3+ independent sources]** Fly.io root filesystem is ephemeral — files written outside a mounted volume are lost on deploy, restart, or machine move. Persistent storage requires Fly Volumes, but volumes are single-machine and "persistent, not durable long-term storage" (snapshots are a safety net, not the primary backup). Sources: fly.io/docs/volumes/overview, fly.io/docs/database-storage-guides, fly.io/blog, community.fly.io.

**[HIGH — 3+ sources]** Database-backed event stores are the most popular, durable choice for change feeds: ACID append, guaranteed ordering, indexing, and time-based partitioning make retention/archival a metadata operation. Caveats: RDBMS needs its own subscription/CDC plumbing on the read side; append-only workloads need tuning. Sources: timderzhavets.com Postgres event store, softwaremill.com, Azure Event Sourcing pattern, microservices.io.

**[MEDIUM — 2 sources]** Event nodes inside a graph database are a legitimate event-store pattern: queries are made over subgraphs and don't perturb the business domain. Sources: StackOverflow DDD/Neo4j; Azure event-sourcing pattern.

**[HIGH — 3+ sources]** JSONL append-only logs are cheap, human-readable, O(1) append, and crash-safe *with fsync* — but they hit three walls at scale: O(n) reads without an index, torn-tail corruption without length-prefix+CRC framing, and unbounded growth without segments+compaction. Sources: munderdiffl.in append-only log for agents, jsonl.help, ndjson.com, learn.padho.ai "three walls of append-only logs".

**[MEDIUM — 2 sources]** Even fsync-based file durability is not bulletproof — an ACM ToS study found fsync failure handling in PostgreSQL/LMDB/LevelDB/SQLite/Redis insufficient in some failure modes. File-based event logs need framing + recovery discipline; best for infrequent writes / single-writer / small data. Sources: ACM Transactions on Storage 3450338; dev.to crash-safe JSON patterns.

**[HIGH — 3+ sources]** Delivery semantics: at-least-once + idempotent consumer is the canonical contract for event feeds. Requirements: unique idempotency keys (event_id), atomic dedup + side-effect (dedup set bounded by a TTL > max redelivery window), commit/ack after success. Retention windows are normal (Kafka default ~7d; 30d reasonable for change feeds) with compaction for bounded growth. Sources: microservices.io idempotent consumer, OneUptime, vabs.github.io, SystemOverflow.

## Recommendation (synthesis)

**#3 — Event store: FalkorDB event nodes in the team's graph namespace. JSONL on the /data volume is the documented alternative (not recommended).**

Honest trade-off (corrected after verifier):

- **JSONL on the existing `/data` volume** *would* survive deploys (the volume exists — my earlier draft wrongly called the disk ephemeral). But it inherits: single-machine risk (Fly volumes are "persistent, not durable long-term"; the volume has no primary backup discipline today — backup infra targets R2 + FalkorDB Cloud), the append-only file's three walls (cursor reads need an index anyway → you're building a mini storage engine), and a second data plane to operate (per-team files, rotation, compaction, disk monitoring).
- **FalkorDB event nodes** (`:GraphEvent {team_id, seq, ts, type, payload}` in the team's existing graph): durability inherited from **FalkorDB Cloud — the only store with AOF + automated backups** (the entrypoint treats it as sacred after the 2026-08-05 incident); **per-team namespacing already exists** (one graph per team at provision — no isolation work); **one uniform write path** across hosted and embedded modes (the SDK emit hook writes into whatever FalkorDB the SDK is connected to); **indexed cursor reads** (index on `(team_id, seq)` — avoids JSONL's O(n) read wall); **retention via Cypher DELETE** (`WHERE ts < now - 30d` — the equivalent of time-partition archival).
- **Zero-relationship guard (adversarial check, verified):** `:GraphEvent` nodes MUST be created with no relationships — they are then graph islands, invisible to the label-scoped query layer, traversals, counts, and EP propagation. Design requirement for the implementation plan (add a test asserting event nodes never gain edges and never appear in `tortoise_search`/`get_point`/counts).
- Cost at scale (framed in memory terms — what FalkorDB Cloud actually bills): event nodes are small (~300-500B). At the Pro tier's included ceiling (50k write ops/mo — internal tier limit, `product/pricing.json` `pro.included_write_ops_per_month`), that is ≈ 15-25MB/mo per team worst case in an in-memory graph; 30-day retention caps growth. FalkorDB Cloud bills by instance memory (STARTUP ~$73/1GB/mo, PRO ~$350/8GB/mo — external pricing check); an 8GB instance holds millions of event nodes.
- **Migration path:** if #669 (Supabase control-plane) lands, `:GraphEvent` maps 1:1 to a Postgres event table; the poll API is storage-agnostic (cursor interface).

**#4 — Delivery semantics: at-least-once + event_id dedup (unique constraint), 30-day retention, opaque cursor. Confirmed.** External check: FalkorDB supports `GRAPH.CONSTRAINT CREATE UNIQUE` (unique constraints on node properties) and composite indexes over multiple properties (e.g. `(team_id, seq)`) — the storage-layer dedup and indexed-cursor reads are implementable (verified by verifier cycle-2 external queries; docs.confirmed).
- At-least-once is the canonical contract (idempotent consumer pattern); enforce with a **unique constraint on `event_id`** in FalkorDB (dedup at the storage layer — mirrors "atomic dedup" best practice) instead of scan-based dedup. Retention bounds the dedup set automatically.
- 30-day retention + size cap via boot/scheduled Cypher DELETE; cursor = opaque token encoding `(seq)` so compaction never breaks clients; expired cursors get a clean 410-style "replay from tail" response.
- Append-before-projection + single-writer discipline (per team) stays per scoping; document at-least-once as the contract (clients must be idempotent on replay).

## Source Confidence Summary

| Claim | Tier | Sources |
|---|---|---|
| Fly root FS ephemeral; volumes single-machine, not long-term durable | HIGH | fly.io docs ×3, community ×1 |
| DB-backed event stores most popular/durable choice | HIGH | timderzhavets, softwaremill, Azure, microservices.io |
| Graph-DB event nodes = legitimate pattern | MEDIUM ⚠️ emerging | StackOverflow DDD, Azure pattern |
| JSONL fine at small scale; 3 walls at scale | HIGH | padho.ai, jsonl.help, ndjson, munderdifflin |
| fsync not bulletproof; framing + recovery needed | MEDIUM | ACM ToS, dev.to |
| At-least-once + idempotent consumer canonical; 30d retention reasonable | HIGH | microservices.io, OneUptime, vabs, SystemOverflow |
| FalkorDB Cloud durability + per-team namespaces + /data volume mount (internal) | HIGH | entrypoint.sh, hosted_api.py, fly.toml |
| :GraphEvent zero-relationship isolation (internal) | HIGH | sdk.py query layer, ep.py |

## Open Question for User
None blocking. If the team prefers file-based logs, the documented alternative is JSONL on the existing `/data` volume + accepting single-machine risk and building rotation/compaction — not recommended vs FalkorDB nodes.
