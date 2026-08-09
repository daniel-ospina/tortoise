<!-- issue-scoping: v5.1 double diamond + verify -->
# Scoping #432 — Subscriptions + Claim Lifecycle for Tortoise

> Session: single-pass scoping per exec instructions (read-only repo, one doc, ~20 min budget). Diamond verification gates were executed inline by the controller (fresh-context sub-agent dispatch deferred to the plan-review phase of `writing-plans`). Findings below are code-verified against origin/main.

## Confirmed Problem

**#432 ships a subscription surface through which a tenant can receive graph-change and claim-state-transition notifications without full-graph polling — which requires, as its prerequisite, that claim state transitions actually exist as durable, observable events. The claim lifecycle is not a parallel feature; it is the substrate the subscription mechanism consumes.**

Root cause (verified in code): **hosted/SDK mutations emit no events at all.** `TortoiseSDK.create_point/retract/supersede` (sdk.py:360, :583, :634) write Cypher directly to the FalkorDB graph — there is no event emission, no per-team event log, and no change record other than node `updatedAt`. The only durable graph event stream in the repo is the CLI/ingest path (`EventAPI._emit` → `EventLog`, tortoise/log.py, tortoise/api.py:42-52), which hosted tenants never touch. **A subscription mechanism with no event source is a notification endpoint with nothing to notify** — the issue's indicator 2 ("a subscription mechanism exists") cannot be satisfied without first creating the substrate (indicator 1 + a durable change stream). The two indicators are sequenced, not parallel.

The issue's framing also carries two assumptions that need correction:

1. **"Integrate with shared_state events.py EventCodec" is half-right.** `EventCodec` (versioned, registered types, upcasters) is real but is used *only* by the PM/card domain (`cardCreated`, `stepCompleted`, `gatePassed`…) and its self-checks — **zero graph domain events flow through it**. Graph events (`PointAdded`, `PointRetracted`, `PointsMerged`, `IngestStarted`) are raw dicts written by `EventAPI._emit` with a different field schema (`ts`, no `version`, no type registration). Convergence: the subscription surface reads the EventAPI/EventLog stream (the real graph source of truth), and **EventCodec is adopted for the new claim-state event types** — registering `ClaimStateChanged` (+ upcasters) gives the lifecycle versioned semantics and reuses the encoding/upcast machinery, satisfying indicator 3 without the fiction that graph events already flow through it.
2. **Claim lifecycle is not merely "partially emergent" — it is internally inconsistent.** Two divergent vocabularies coexist: EventAPI path births points `status:'live'` and `PointRetracted` **deletes the point from the projection** (`points.pop(ev["id"])`, projection/__init__.py:125 — no tombstone, no history); SDK/graph path births points `status:'draft'` (sdk.py:430), promotes to `'live'` on first operator edge (sdk.py:778), and has statuses `{'live','draft','outdated','archived'}` (sdk.py:26) — **no `challenged`, no `retracted`, no `superseded`**. A state model cannot be enforced on vocabulary that doesn't contain the states.

**Quality over convenience:** The easy path (issue's own framing) is bolting a `tortoise_subscribe`-style endpoint onto the existing REST/MCP surface and calling indicator 2 done. The right path is (a) defining and enforcing the state model in SDK/API paths (indicator 1), (b) making transitions emit durable events in **both** write paths (EventAPI and SDK/hosted), and (c) layering the subscription surface on that stream. Without (a)+(b), the subscription delivers stale or phantom notifications.

### Why This Framing
- **Issue's framing ("zero subscription code; add one") rejected** — the hard dependency (durable, tenant-scoped event stream) is unstated. Every solution approach must first solve "where do events live," which the issue assumes away.
- **Indicator 3's EventCodec framing corrected, not discarded** — the *machinery* (versioning/upcasters/JSONL) is the right substrate; the *claim* that graph events already use it is wrong. Scoping targets the true integration point.
- **Poll vs push is a solution question, not a problem question** — the confirmed problem is "tenant observes state changes without full-graph polling." Both poll and push satisfy it; the choice is resolved in the solution diamond on capability evidence (FastMCP 3.4.6 exposes **no** notification API — verified `dir(FastMCP)` has no notify/stream/subscribe surface).
- **"Challenged" as a state vs a NAND edge** is left open (see Open Questions) — the state model can ship draft/live/retracted/superseded and treat challenged as a first-class state added behind a flag if product confirms it.

### Falsification Check
This definition is wrong if any of:
1. Hosted/SDK mutations **do** emit durable events somewhere (grep for `_emit`/`EventLog`/`append` in hosted_api.py, mcp_server.py, sdk.py → **none found**; all SDK mutations are direct Cypher).
2. A subscription/notify/watch mechanism already exists outside `tortoise/*.py` (grep for subscribe/notify/watcher/listener/webhook → only connector webhooks in `tortoise/connectors/`, unrelated).
3. `PointRetracted` keeps a tombstone in the projection (projection/__init__.py:125 `points.pop` → **deletes**).
4. `POINT_STATUS_VALUES` already contains challenged/retracted/superseded (sdk.py:26 → **does not**).

### Confidence: 80
All four falsification checks code-verified on origin/main. Residual uncertainty: product semantics of `challenged`; hosted event-store placement (per-team JSONL vs FalkorDB event nodes); whether FastMCP gains notification support in a version we can adopt (verified absent at 3.4.6).

## Verification Gates

Single-session inline execution (per exec constraints — no sub-agent dispatch, no git ops).
- **problem-verify (inline):** 4 falsification checks run against code; 2 framing corrections (EventCodec integration point; event-source prerequisite) incorporated.
- **solution-verify (inline):** 3 approaches (below) checked for architectural distinctness; convergence selected Approach A on capability evidence (FastMCP notification gap verified in .venv), not on diff size.

## Solution Approaches

### Approach A — Durable tenant event log + cursor-based poll surface (RECOMMENDED)

**Architecture:** (1) Claim-state model lands in SDK/API (`POINT_STATUS_VALUES` extended; `retract_point` writes `status:'retracted'` tombstone instead of projection-delete; explicit `supersede_point` already exists at sdk.py:634 — align it with the state model). (2) Every state transition + Point/operator mutation emits `ClaimStateChanged`/`PointAdded`/`OperatorAdded`/`PointRetracted` events through a **tenant-scoped EventLog** (per-team JSONL in hosted mode, keyed `team_{id}`, written under the existing `locked_append` flock pattern). (3) New read surface: REST `GET /v1/events?after=<cursor>` + MCP tool `tortoise_events_poll` (readOnly, cursor returns `{next_cursor, events[]}`), event_id-dedup'd (reuse `shared_state/recovery.dedup_events`), at-least-once semantics. Optionally a Starlette SSE endpoint (`/v1/events/stream`) on the hosted REST app for push — SSE is plain HTTP, no MCP dependency.

**Files:** `tortoise/sdk.py` (status model + emit hook), `tortoise/api.py` (tombstone retraction + claim events), `tortoise/projection/__init__.py` (`_apply_one` retraction semantics), new `tortoise/subscriptions.py` (event log per team + cursor + poll + SSE), `tortoise/mcp_server.py` (poll tool, HTTP_ALLOWED), `tortoise/hosted_api.py` (REST routes), `tortoise/shared_state/events.py` (register claim event types + upcasters), tests.

**Risks/tradeoffs:** dual-write (graph + log) can diverge → single-writer discipline (emit inside the mutation call, append-before-projection), boot reconcile; JSONL growth → retention/compaction policy; poll latency is bounded by client cadence, not real-time → SSE available as an additive layer; per-tenant isolation depends on log path scoping (mirror the HTTP-mode `graph_name` injection guard, mcp_server.py:531).

**Best fit if:** correctness of the event substrate matters more than push latency; hosted tenants need a durable audit trail regardless of subscriptions (they do — nothing records graph mutations today).

### Approach B — MCP Streamable HTTP notifications (push via the #487 surface)

**Architecture:** Reuse the merged #487 `/mcp` Streamable HTTP mount; register `tortoise_subscribe(filter)` (MCP tool or resources/notifications) and push server→client notifications over the existing SSE session.

**Risks/tradeoffs:** **FastMCP 3.4.6 (pinned, requirements.txt:6) exposes no notification API** (verified — no notify/stream/subscribe/sse on the FastMCP class). Server-initiated notifications would require a FastMCP upgrade (capability unknown for pinned major), a fork, or low-level `mcp` protocol plumbing — that's an unplanned dependency rabbit hole for a standard project. Even then, notifications ride the client's open session: reconnect/offline clients lose events (needs the durable log anyway → Approach A's substrate is a prerequisite regardless). Stdio/pi sessions (the primary internal consumer) have no HTTP session to push to — they'd need polling regardless.

**Best fit if:** the pinned FastMCP gains first-class notifications AND the only consumers are long-lived HTTP MCP clients. Not true today; revisit after #432 ships the substrate.

### Approach C — Webhook push (tenant-registered callback URL)

**Architecture:** Tenant registers a callback URL + secret; server POSTs JSON events (HMAC-signed, pattern exists in `tortoise/connectors/github.py:428`).

**Risks/tradeoffs:** Needs outbound HTTP + retry/backoff queue + delivery receipt state — the heaviest operational surface of the three; no hosted consumer infrastructure exists; security surface (SSRF, secret rotation). **Still requires the Approach A event log as the delivery source** — so C is a delivery mode on top of A, not a substitute.

**Best fit if:** an external system (not an MCP client) must react to graph changes — a Phase-2 delivery mode, not the v1 surface.

### Claim-state model placement options (orthogonal axis)
- **SDK-first** (recommended): the SDK is the shared choke point both MCP tools and REST route through (`_get_team_sdk()`, mcp_auth.py) — one enforcement location covers all consumers. `update_point` already validates status (sdk.py:545-548); extend the valid-set + add transition guards there.
- **API-first:** enforce only in `EventAPI` (CLI/ingest path) — leaves hosted SDK path unenforced (the exact gap this issue exists to close). Rejected.
- **Parallel:** build subscriptions before the state model — delivers notifications of a lifecycle that doesn't exist. Rejected (issue's own indicator ordering makes this a non-sequitur).

## Converged Choice: Approach A + SDK-first state model

**Good > Easy:** A is the only approach whose core artifact (durable, tenant-scoped event stream) has value independent of the subscription feature — it fixes the verified root cause (hosted mutations are unobservable) and gives every future consumer (SSE push, webhooks, MCP notifications, audit, replay) the same substrate. B and C are **delivery modes**, not substitutes: both still require A's event log for offline/reconnect delivery, and B additionally blocks on a FastMCP capability that does not exist at the pinned version (verified). Selecting B/C now would be picking the shinier notification surface over the mechanism that makes notifications correct — convenience over quality.

**Rejected alternatives documented:** B — when FastMCP (or a successor) ships notifications and long-lived HTTP MCP clients dominate, add `tortoise_subscribe` push on top of A's log; the SSE endpoint in A is the interim push surface. C — when an external system needs graph reactions, add webhook delivery (reuse github connector's HMAC pattern) on top of A. API-only enforcement — rejected because it leaves the hosted path (the actual tenant surface) unenforced.

## Implementation Plan Outline (sequenced — substrate before surface)

**Step 1 — Claim state model (indicator 1).** Extend `POINT_STATUS_VALUES` (sdk.py:26) → `{draft, live, challenged, retracted, superseded, outdated, archived}` (challenged behind product confirmation). Change `EventAPI._point` default `live` → `draft` for parity with SDK (api.py:67) **or** document the deliberate difference; prefer parity. Change `_apply_one` PointRetracted from `points.pop` to tombstone `status:'retracted'` (projection/__init__.py:125) — retraction becomes observable instead of a deletion. Enforce transitions in `update_point` (sdk.py:545) + `retract_point`/`supersede_point` (sdk.py:634). Register claim event types in `shared_state/events.py` (`register_event_type("ClaimStateChanged", ...)` + upcasters).

**Step 2 — Durable per-tenant event emission (the root-cause fix).** Add an emit hook to SDK mutation paths (`create_point`, `create_operator`, `retract_point`, `supersede_point`, `annotate_*`) writing **`:GraphEvent` nodes into the team's FalkorDB graph namespace** (`{team_id, seq, ts, type, payload, event_id}`) — durability inherited from FalkorDB Cloud (AOF + backups; per-team namespaces already provisioned via `select_graph`), NOT container JSONL (see research: docs/research/2026-08-08-event-log-subscriptions.md). **Zero-relationship guard:** event nodes carry no edges → graph islands, invisible to label-scoped queries, traversals, and EP. Unique constraint on `event_id` (storage-layer dedup, `GRAPH.CONSTRAINT CREATE UNIQUE`); index on `(team_id, seq)` for cursor reads. Single-writer discipline per team (append-before-projection). EventAPI path: keep as-is (already emits), add claim-transition events at api.py:101/185.

**Step 3 — Subscription surface (indicator 2, on the Step-2 stream).** `GET /v1/events?after=<cursor>&types=<filter>` (REST, team-scoped via existing auth) returning `{events[], next_cursor}` with at-least-once + event_id dedup; MCP tool `tortoise_events_poll` (readOnly, added to `HTTP_ALLOWED`, mcp_auth.py) for MCP/pi clients; optional additive `GET /v1/events/stream` SSE on the hosted FastAPI app for push. Cursor = (timestamp, event_id) pair or opaque token; replay = `after=""` returns tail. Retention policy (default 30d or N MB, config-driven) + compaction.

**Step 4 — Tests + docs.** New `tests/test_subscriptions.py` (emit→poll round-trip, cursor replay, dedup, tenant isolation — team A cannot read team B events, tombstone retraction, status transition guards, EventCodec round-trip for ClaimStateChanged). Extend `tests/test_api.py` (retraction tombstone), `tortoise/shared_state/tests/` (new event types). Document the state model + event catalog in `docs/` (ontology §5 vocabulary) and `docs/00_index.md`.

### Acceptance Criteria
1. **AC1:** Every SDK mutation (point/operator/retract/supersede) appends a durable, team-scoped event; `fold`-replay reconstructs retracted points with `status:'retracted'`.
2. **AC2:** A tenant polls `GET /v1/events?after=C` and receives exactly the events appended after C, dedup'd, across a retract→re-add race, without full-graph polling.
3. **AC3:** Team A's poll cursor never returns team B's events (HTTP-mode isolation test).
4. **AC4:** `tortoise_events_poll` works over stdio and Streamable HTTP, is readOnly-annotated, and respects the HTTP exclude list.
5. **AC5:** `POINT_STATUS_VALUES` transition guards reject illegal transitions (e.g., live→draft); claim states documented.
6. **AC6:** Replayed/duplicate events are delivered at-most-once per cursor (event_id dedup); at-least-once across cursor resets.

## Complexity

| Domain | Rating |
|--------|--------|
| Tier | **Standard (confirmed)** — issue's rating holds. Multi-surface (SDK status model, projection semantics, new subscriptions module, REST + MCP tool, hosted wiring) but each surface is small and well-bounded; the event log reuses existing flock/dedup machinery. **Escalation condition:** if per-tenant event emission requires a new store/queue (FalkorDB event nodes with fan-out, Kafka, durable queue) instead of the JSONL file pattern, escalate to Complex. |
| UX_RATING | low — no user-visible UI; two new developer/API surfaces (REST endpoint + MCP tool). No UX Prototype Gate. |
| ONTOLOGY_RATING | medium-high — `POINT_STATUS_VALUES` changes (new states), retraction semantics change (delete → tombstone) affecting existing folds/queries, new event types registered in EventCodec, ontology §5 vocabulary update. |
| ARCH_RATING | medium — new `subscriptions.py` module, per-tenant file layout, dual-write discipline (graph + log), SSE additive layer. No new external services. |

## O/I/T Check

| O/I/T | Covered By |
|---|---|
| **O:** agents/MCP clients subscribe to graph changes + claim-state transitions; claims move through an explicit state model | Confirmed Problem; Steps 1–3 |
| **I1:** claim states defined + enforced in SDK/API paths | Step 1 (AC5) |
| **I2:** subscription mechanism (poll/notify) surfaces claim-state transitions + new Point/operator events | Step 3 (AC2, AC4) |
| **I3:** integrates with shared_state event infra (events.py, EventCodec), not a parallel mechanism | Step 1 (EventCodec type registration) + Step 2 (locked_append/dedup reuse) (AC6) |

## Scope In/Out

**In:** claim state vocabulary + transition enforcement; retraction tombstone semantics; per-tenant durable event log for SDK mutations; cursor-based poll surface (REST + MCP tool); EventCodec registration of claim events; retention/compaction; tests + docs.

**Out (file separately):** MCP notification push (FastMCP capability gap — approach B); webhook delivery (approach C); SSE streaming endpoint if not needed for the target (additive, small); subscription **filters/queries** beyond event-type + team scoping (the issue's target is "subscribe to a query/filter" — v1 ships topic/type filters; arbitrary Cypher-query filters are a follow-up); per-tenant event-store migration off JSONL files; audit-trail UI/consumer beyond the poll API.

## Risks & Mitigations (top 5)

1. **Graph/log divergence (dual-write)** — SDK writes graph + appends log in two steps; a crash between them loses or orphans an event. *Mitigation:* append-before-projection, single writer per team (flock), boot reconcile comparing log cursor vs graph `updatedAt` watermark; document that log is source of truth for change history, graph for state.
2. **Log growth unbounded** — every mutation appends forever; hosted storage bloat. *Mitigation:* retention window + size cap with compaction (config-driven, default 30d), cursor invalidated gracefully (`410`-style "cursor expired — replay from `after=tail`"), reuse `verify_hash` integrity checks if compaction truncates.
3. **Race between state change and notification** — poller reads log before graph projection applied, or projection applied before log append. *Mitigation:* single-order discipline (append then project in the same mutation call — EventAPI already does this at api.py:48-51; replicate in SDK hook), sequence (timestamp, event_id) cursors so a replay-after-reset is safe.
4. **Per-tenant isolation leak** — events from team B delivered to team A via a mis-scoped log path or HTTP-mode cursor. *Mitigation:* team id derived server-side from auth (never client-supplied, mirroring the graph_name injection guard at mcp_server.py:531/548), per-team log directories, isolation test (AC3), poll tool added to `HTTP_ALLOWED` default-deny list explicitly.
5. **Replay/idempotency** — duplicate delivery on cursor reset or client retry. *Mitigation:* event_id dedup on write (`locked_append` dedup_key, recovery.dedup_events on read), at-least-once contract documented, AC6.

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| `POINT_STATUS_VALUES` + `update_point` transition guards | SDK | Step 1 (AC5) | ✅ |
| `_apply_one` PointRetracted tombstone | Projection | Step 1 | ✅ |
| `retract_point`/`supersede_point`/`create_point`/`add_operator` emit hooks | SDK | Step 2 | ✅ |
| Per-team JSONL event log (`data/events/team_{id}/`) | Data | Step 2 (`subscriptions.py`, flock reuse) | ✅ |
| `shared_state/events.py` type registration + upcasters | Shared infra | Step 1 | ✅ |
| `GET /v1/events` REST (team-scoped auth) | API | Step 3 | ✅ |
| `tortoise_events_poll` MCP tool + `HTTP_ALLOWED` | MCP | Step 3 | ✅ |
| SSE stream endpoint (optional) | API | Step 3 (additive) | ⚠️ optional |
| Tenant isolation guard (server-derived team_id) | Cross-cutting | Step 2/3 (AC3) | ✅ |
| Ontology §5 vocabulary + docs index | Docs | Step 4 | ✅ |
| Tests: `test_subscriptions.py`, `test_api.py`, `shared_state/tests` | Tests | Step 4 | ✅ |
| MCP notification push (#487 follow-up) | Boundary | Out of scope (Approach B, FastMCP gap) | ⚠️ known |

## Discovery: Extra Issues to File (reported, NOT created)

📋 Extra issues identified during scoping of #432 (do NOT absorb):

1. **`tortoise/log.py` has no tail/stream primitive** — `EventLog` is append + read_all only; its own docstring says "streaming tail arrive in M1/M4" (never landed). The poll cursor needs a `read_after(cursor)` primitive — small, but a distinct log-infra gap worth tracking (tech debt in touched area).
2. **`PointRetracted` = hard delete in the projection; retracted points are unrecoverable** — `_apply_one` pops the point (projection/__init__.py:125); existing graphs that retracted content have lost it irrecoverably. Even after #432 tombstones future retractions, historical retracted points cannot be reconstructed. (Adjacent data-loss bug, exposed by this work.)
3. **SDK vs EventAPI status vocabulary drift** — SDK defaults `draft` + promotes on edge; EventAPI defaults `live`; `'outdated'/'archived'` exist only in the SDK set. Pre-existing ontology drift (sdk.py:26/430 vs api.py:67) that #432's state model will partially fix but deserves its own documented-decision issue (docs/ONTOLOGY §5).
4. **FastMCP notification capability gap** — pinned fastmcp==3.4.6 exposes no server→client notification API; blocks MCP push subscriptions. Track as a dependency upgrade issue + #487 follow-up (soft dependency for #432's Phase 2 push; hard dependency for Approach B ever being viable).
5. **No read-only event/audit access for tenants today** — even before subscriptions, there is no way to replay "what changed in my graph" (SDK `get_point` returns state, not history). The poll surface (Step 3) partially fills this; a dedicated audit-replay consumer (REST or dashboard) is a separate product decision.

## Open Scoping Questions (for user)

1. **Which claim states are real for the product?** Confirm the full set `draft → live → challenged → retracted/superseded` — in particular, is **challenged** a state (needs an explicit transition path, e.g., via NAND edge creation) or an emergent property of having a NAND operator attached? v1 can ship without `challenged` if it's an edge-derived property.
2. **Retraction semantics:** tombstone (`status:'retracted'`, point stays in graph, queryable with a filter) vs the current hard delete? Tombstone is assumed for the state model — confirm it's acceptable to change existing fold behavior (existing consumers that rely on retraction-as-absence, e.g., `split`, query filters).
3. **Event-store placement in hosted mode:** per-team JSONL files (recommended — reuses flock machinery, zero new infra, trivially team-scoped) vs FalkorDB event nodes (queryable, no file layout, but needs graph writes for events + fan-out concerns). File-based is the standard-complexity choice.
4. **Delivery semantics for v1:** at-least-once with event_id dedup (recommended) acceptable? Is a 30-day default retention window + size cap OK, or is a specific SLA needed?
5. **Filter scope:** the issue target says "subscribe to a query/filter." v1 = filter by event type + team scope (recommended). Do you need arbitrary property/pointKind filters or Cypher-query subscriptions in v1, or is topic-based sufficient?
6. **Push surface priority:** is the REST `GET /v1/events` poll + `tortoise_events_poll` MCP tool sufficient for the target consumer (agents/pi sessions poll; long-lived HTTP MCP clients can use an SSE endpoint later)? Or should the SSE stream ship in v1?
7. **Cursor semantics:** opaque token vs (timestamp, event_id) composite? Opaque token is recommended (survives compaction without breaking clients).


## User Decisions (2026-08-08) — recorded after human approval gate + research

| # | Question | Decision |
|---|----------|----------|
| 1 | Is **challenged** a real state? | **No — derived.** Not in the state vocabulary. `challenged` emerges from the presence of a NAND operator edge on a live point (queryable as a derived condition). Ship `draft → live → retracted → superseded` as enforced states; document challenged-as-derived in ontology §5. |
| 2 | Retraction semantics | **Tombstone confirmed** — `status:'retracted'`, point stays in graph, queryable with a filter; changes existing fold behavior (retraction-as-absence consumers must be checked). |
| 3 | Event-store placement | **FalkorDB event nodes in the team's graph namespace** (per research recommendation — `:GraphEvent`, zero-relationship, unique constraint on event_id, index on (team_id, seq)). NOT container JSONL. See docs/research/2026-08-08-event-log-subscriptions.md (verified CLEAN after 2 review cycles). |
| 4 | Delivery semantics | **At-least-once + event_id unique-constraint dedup + 30-day retention + opaque cursor** (per research). Expired cursors → 410-style "replay from tail". Document at-least-once as the contract (clients idempotent on replay). |

Research provenance: docs/research/2026-08-08-event-log-subscriptions.md — internal setup verified (Fly /data volume mount, FalkorDB Cloud as only AOF+backup store, per-team namespaces, SDK-routed surfaces, GAP-07 emit-hook gap), external best practices (DB-backed event stores dominant; JSONL three walls; at-least-once + idempotent-consumer canonical), fresh-session verifier CLEAN after 2 fix cycles.
