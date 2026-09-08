---
<<<<<<< HEAD
title: "Tortoise Agent Daemon — Scoping"
type: engineering
subjects.team: epistemic-team
domain: platform
doc_status: draft
aboutSubjects: tortoise
aboutObjects: tortoise-agent
created: 2026-09-08
---

<!-- issue-scoping: v5.1 double diamond + verify -->

# Tortoise Agent Daemon — Scoping

**Complexity rating:** Standard (tier=Standard)
**Prerequisites:** Research brief at `docs/research/2026-09-08-tortoise-agent-daemon.md`
**Written:** 2026-09-08

---

## 1. Problem Framing

### Problem statement

Every agent harness (Pi, Claude Code, Cursor, Codex, OpenCode) that wants to use Tortoise currently needs the user's **raw API key** configured somewhere — a config file, an environment variable, or a prompt template. This creates three compounding problems:

1. **Key leakage surface** — The key lives in config files (`~/.cursor/mcp.json`, `~/.claude/settings.json`, project `.cursor/rules/`, shell history, `.env` files) that may be committed, backed up, or exfiltrated. The project's existing installer (`install-tortoise-skills.sh`) installs skills but does NOT install a credential-holding daemon.
2. **Per-harness configuration friction** — A user with multiple agents must configure the key in each one separately. The "self" fork (personal agent use via MCP) requires one key per agent context.
3. **No session capture gateway** — Since the MCP request goes directly from agent → API, there's no interception point to automatically capture sessions for replay, debugging, or analytics.

### Users affected

- **"Self" fork users** — Personal agent users who connect via MCP (the daemon's primary audience).
- **"Build" fork users** — SDK/REST API users — NOT affected; they don't use MCP, so the daemon doesn't apply.

### Success criteria

1. A user can run `curl -fsSL https://... | bash` and have the daemon running with zero manual key configuration.
2. `tortoise agent status` shows `running` within 1 second of install completion.
3. The API key is never written to disk by the daemon.
4. Any MCP-capable agent can use `http://localhost:9784/mcp` as its MCP server endpoint.

---

## 2. Constraints

| # | Constraint | Rationale |
|---|---|---|
| C1 | API key must NEVER be written to disk by daemon | Primary security property. Key is in-memory only. |
| C2 | Daemon must bind ONLY to localhost | Prevent network-exposed proxy (no auth other than the API key). MCP spec requires this for local servers. |
| C3 | Must not require root/sudo | User-level daemon. launchd/Systemd user services or profile-based auto-start. |
| C4 | Must be transparent to MCP protocol | Daemon does NOT parse/understand MCP messages — it forwards headers + body as-is. This guarantees forward-compatibility with protocol evolution. |
| C5 | Must not introduce new language dependencies | Python-only (same runtime as Tortoise SDK). No Go, Rust, or Node.js dependency. |
| C6 | Must coexist with multiple agent instances | Different agents connecting to the same localhost:9784 with different API keys → handled by the API server (multi-key). The daemon holds ONE key (the user's primary key). |
| C7 | Installer must remain a single curl pipe | The existing `curl ... | bash` installer must work. The daemon is an ADDITION to the installer, not a replacement. |

---

## 3. Alternatives Considered

### Alternative A: CLI forwarding only (no daemon)

**What:** Instead of a persistent daemon, modify the skills/tools to pipe MCP requests through a CLI command that injects the key.

**Pros:**
- No daemon lifecycle to manage.
- No port allocation.
- Existing `tortoise` CLI already handles some operations.

**Cons:**
- Every MCP call spawns a new Python process for auth → high latency (500ms+ per request).
- No session capture gateway (no persistent process to observe traffic).
- Per-process key handling is fragile (env var injection per call).
- Most agent harnesses expect HTTP MCP endpoints, not CLI commands.

**Verdict: REJECTED.** Lattoo slow, no session capture, poor harness compatibility.

### Alternative B: Environment variable injection (via skills installer)

**What:** The installer writes the API key to a well-known env file (`~/.tortoise/env`) with 0600 permissions, and skills reference it.

**Pros:**
- Simplest possible implementation (one file write).
- Works with every harness that supports env vars.

**Cons:**
- Key is on disk (violates C1).
- Multi-agent sharing still requires per-agent env config.
- No session capture gateway.
- No lifecycle management.

**Verdict: REJECTED.** Violates the primary security constraint.

### Alternative C: OS keychain daemon with MCP proxy (CHOSEN)

**What:** A persistent Python daemon (aiohttp) that:
- Reads API key from env var at startup (never writes to disk).
- Listens on localhost:9784.
- Forwards MCP requests to api.premiselabs.co.
- Managed via `tortoise agent start|stop|status`.
- Auto-started via launchd/systemd.

**Pros:**
- Key stays in-memory only.
- One install = zero per-harness config (any agent uses localhost:9784).
- Session capture gateway built into the proxy.
- Transparent MCP forwarding — no protocol awareness needed.
- Python-based — no new runtime dependencies.

**Cons:**
- Daemon lifecycle code (launchd/systemd templates).
- Port management (collision detection).
- Startup latency (but only once, not per-request).

**Verdict: SELECTED.** Best balance of security, simplicity, and extensibility.

### Alternative D: Rust/Go binary daemon

**What:** A compiled binary with smaller memory footprint and faster startup.

**Pros:**
- Lower memory (~5MB vs ~30MB Python runtime).
- Faster startup (milliseconds vs ~100ms).
- Single binary distribution (no Python dependency).

**Cons:**
- New build chain + language in the project.
- Cannot share code with existing Tortoise SDK (configuration, logging, etc.).
- Must implement MCP header handling from scratch.
- Maintenance burden of a new language ecosystem.

**Verdict: REJECTED.** The overhead of a new language doesn't justify the memory savings for a background daemon. Python's disadvantages (startup time, memory) are irrelevant for a long-running process.

---

## 4. MECE Decomposition

```
Tortoise Agent Daemon
├── 1. Core proxy engine
│   ├── 1.1 HTTP server (aiohttp, localhost:9784)
│   ├── 1.2 Request forwarding (POST → api.premiselabs.co/mcp)
│   ├── 1.3 Response streaming (SSE response passthrough)
│   ├── 1.4 Header management (add Authorization, forward others)
│   └── 1.5 Error handling (502 on unreachable, 503 on no key)
├── 2. Key management
│   ├── 2.1 Key injection (env var at startup)
│   ├── 2.2 In-memory storage (never flushed to disk)
│   ├── 2.3 Key validation (health check against API on startup)
│   └── 2.4 Memory sanitization (unsetenv, zero-on-exit)
├── 3. CLI surface
│   ├── 3.1 `tortoise agent start`
│   ├── 3.2 `tortoise agent stop`
│   ├── 3.3 `tortoise agent status`
│   ├── 3.4 `tortoise agent restart`
│   ├── 3.5 `tortoise agent logs`
│   └── 3.6 `tortoise agent config` (port, session-capture flag)
├── 4. Lifecycle management
│   ├── 4.1 macOS launchd plist (install + bootout on stop)
│   ├── 4.2 Linux systemd user service
│   ├── 4.3 Profile-based auto-start (fallback)
│   ├── 4.4 PID file management
│   └── 4.5 Graceful shutdown (SIGTERM handler)
├── 5. Health + diagnostics
│   ├── 5.1 GET /health endpoint
│   ├── 5.2 Key validation status
│   ├── 5.3 Proxy statistics (requests served, errors)
│   └── 5.4 Log output
├── 6. Installer integration
│   ├── 6.1 Daemon binary/script placed during install
│   ├── 6.2 launchd/systemd service registration
│   ├── 6.3 Key injection during install flow
│   └── 6.4 Idempotent re-install
├── 7. Session capture (PHASE 3)
│   ├── 7.1 JSONL request/response journal
│   ├── 7.2 Log rotation (size cap, oldest-drop)
│   ├── 7.3 Opt-in via config flag
│   └── 7.4 Integration with session-postmortem.sh
└── 8. Offline resilience (PHASE 4)
    ├── 8.1 Outbox buffer (capped JSONL)
    ├── 8.2 Reconnect detection + replay
    ├── 8.3 Read-only vs write mutation buffering
    └── 8.4 Stale write rejection
```

---

## 5. Integration Surfaces

| # | Surface | Type | Data Flow | Test Layer | Key Failure Modes |
|---|---|---|---|---|---|
| 1 | `tortoise/__main__.py` | CLI entry point | User command → agent subcommand → daemon process | Integration | Subcommand not found; daemon unreachable; permissions |
| 2 | `tortoise/agent_daemon.py` (new) | Core daemon | HTTP proxy: localhost:9784 → api.premiselabs.co | Unit + Integration | Bind failure; forwarding error; header not forwarded; SSE not streamed |
| 3 | `scripts/install-tortoise-skills.sh` | Installer | Adds daemon install step to curl pipe | Manual + CI | Port collision; launchd failure; key injection failure |
| 4 | `scripts/install-launchd.sh` | lifecycle | Installs plist, manages launchd | Integration (test) | Plist syntax; PATH resolution; crash loop detection |
| 5 | `~/.tortoise/config.toml` | config | Persists non-secret config | Unit | Parse error; missing defaults; port parse |
| 6 | `~/.tortoise/agent.pid` | PID file | Start writes, stop reads, status checks | Unit | Stale PID; race condition; permissions |
| 7 | `launchd plist` / `systemd service` | OS service mgmt | Auto-start, crash restart, stop on uninstall | Manual + CI | PATH wrong; HOME missing; environment inheritance |
| 8 | `GET /health` on daemon | HTTP endpoint | Agent/dashboard checks daemon status | Integration | Not reachable; returns error despite healthy |
| 9 | Session capture JSONL | file I/O | Writes MCP request/response pairs | Unit + Integration | Permissions; rotation; disk full |
| 10 | `session-postmortem.sh` | analysis | Reads session JSONL → postmortem | Integration | Format change; missing events |

### Dependency chain

```
install-tortoise-skills.sh
└── install-launchd.sh (existing, extended)
    └── tortoise-agent (new daemon binary)
        ├── tortoise/agent_daemon.py (core proxy)
        ├── tortoise/__main__.py (CLI: agent subcommand)
        └── ~/.tortoise/config.toml (config)
```

### Bug pattern flags

- **Race condition:** `tortoise agent start` checks PID → starts → PID file write. Two simultaneous `start` calls could race. Mitigation: atomic PID file write with `O_CREAT | O_EXCL`.
- **Stale PID:** Crash without cleanup → PID file points to dead process. Mitigation: `status` checks `/proc/<pid>` (Linux) or `kill -0 <pid>` (macOS).
- **Key leak:** Key in env var visible in `/proc/<pid>/environ`. Mitigation: unsetenv immediately on startup; document mitigation.
- **Memory dump risk:** Key in process memory could appear in crash dumps. Mitigation: Python `ctypes` `memset` on shutdown (best-effort; don't over-invest).
- **Log leakage:** Request/response content in daemon logs. Mitigation: logs only metadata (path, status, latency), not bodies.

---

## 6. Complexity Rating

**Rating: Standard (tier=Standard)**

Rationale:

| Dimension | Rating | Reasoning |
|---|---|---|
| **Architecture** | Medium | Async HTTP proxy. Well-understood pattern. Single moving part (the proxy loop). Config is local only. |
| **Engineering novelty** | Low | HTTP proxies are a solved problem. MCP header forwarding is documented. launchd/systemd are standard OS interfaces. |
| **Integration surface count** | Medium-High | 10 surfaces (CLI, installer, launchd, systemd, config, PID, health, session capture, logs, API). Most are simple file/process operations. |
| **Security sensitivity** | High | API key in memory. The threat model is key leakage via process memory, swapping, and core dumps. Mitigations are straightforward (unsetenv, mlock advisory, zero-on-exit). |
| **Cross-platform complexity** | Medium | macOS + Linux daemon lifecycle are different. launchd vs systemd vs profile fallback. Python itself is cross-platform; the lifecycle code is per-OS. |
| **Testing difficulty** | Medium | Integration tests need to start/stop a real daemon process. HTTP proxy tests need a mock upstream server. launchd/systemd tests need the real OS or a shim. |

**Why not "Complex"?** The core proxy is a few hundred lines of aiohttp. The complexity is in distribution (installer integration, lifecycle) and security (key management), not in the proxy logic itself. Each concern decomposes cleanly.

**Why not "Micro"?** The daemon touches 10 integration surfaces, requires cross-platform lifecycle management, and has security-sensitive key handling. The installer integration alone requires coordinating with an existing multi-step shell script.

---

## Verification Gates

### problem-verify

**Framing verified:** Key leakage + per-harness config friction + no session capture gateway. Three distinct problems. The daemon solves all three with one architectural change.

**User research gap:** No direct user research. Derived from internal pattern (every engineer manages keys per harness) and external precedent (Apify mcpc solves the same credential isolation problem).

### solution-verify

**Approach selected:** Alternative C (OS keychain daemon with MCP proxy).

**MECE check:** Yes — 8 components, non-overlapping, complete coverage of the feature space.

**Feasibility check:** Yes — reference implementations exist (mcpc, mcp-proxy). Python aiohttp is battle-tested. launchd/systemd lifecycle is established in this repo (`install-launchd.sh`).

**Complexity rating verified:** Standard. Every component has a clear implementation path.

---

## Edges

### What's explicitly NOT in scope

- **Multi-key management** — The daemon holds one key. Different keys = different daemon processes or key rotation. The API handles multi-key/multi-graph at the server.
- **TLS termination** — localhost only. No TLS needed.
- **Auth other than Bearer token** — The API uses Bearer tokens. No OAuth, no API-key-vs-session distinction at the proxy level.
- **MCP protocol parsing** — The daemon is a transparent proxy. It does not need to understand MCP messages.
- **Windows support** — The initial target is macOS + Linux. Windows (Windows Service API) is deferred.
- **Docker-based deployment** — The daemon is for local agent use. Dockerized Tortoise is a separate concern (the SDK image doesn't need a daemon).
- **Modifying the existing `install-tortoise-skills.sh` to remove the key-display step** — This is a separate concern. The installer currently prints the key during setup. The daemon eliminates the need for users to ever see the key, but the installer may still show it for legacy/fallback paths.

### Migration/back-compat

- Existing users with keys in config files continue to work — the daemon is additive, not replacement.
- The `TORTOISE_API_KEY` env var is shared between SDK and daemon (both read it).
- Tools/skills that reference `http://localhost:9784/mcp` instead of the API URL are forward-compatible.
- The skill files (`how-to-use-tortoise/`) should document both paths: direct API URL (legacy) and localhost daemon.
=======
title: "Epic Scoping (REVISED) — Keyless Agent Connect via per-agent OAuth + human attribution on the graph"
type: engineering
subjects.team: epistemic-team
domain: platform
doc_status: live
aboutSubjects: tortoise
aboutObjects: tortoise-agent
created: 2026-09-08
revised: 2026-09-08
epic: "#2554"
prereq: docs/research/2026-09-08-tortoise-agent-daemon.md (clean, review-gated) + docs/research/memory-attribution-industry-findings.md (sub-agent research, 2026-09-08)
---

<!-- epic-scope contract: Scope Boundaries → Customer Value Map → Complexity
Ratings → High-Level E2E → Axis Research Notes → Human Approval Gate. -->

# Epic Scoping (REVISED) — Keyless agent connect + graph attribution (#2554)

> **Revision note (2026-09-08):** the epic was redirected by research after a
> targeted second-pass review of the proposed architecture (research brief
> §"Second-Pass Architecture Verdict"). **The local credential-holding daemon
> (Option B) is demoted — it is NOT the auth backbone.** The epic's problem
> ("secure keyless setup") is solved by per-agent OAuth authorize-not-copy
> plus per-agent/per-graph scoped keys — which the hosted platform already
> ships (#524 OAuth 2.1 live; #2083/C2/C5 per-graph `tk_` keys live). What
> remains to BUILD is: (1) propagate the human actor server-side onto
> sessions/graph writes (the current resolver DROPS the token's user_id), and
> (2) close two client-side OAuth gaps (pi's mcp-client #4105; wizard routing
> #1701). A separate "credential-agnostic local capture/offline relay" idea
> was filed independently by the epic owner as an improvement issue and is
> OUT of this scope.
>
> Scope status: **APPROVED at human gate 2026-09-08.** SD-1 and SD-2
> resolved by research and confirmed; §7 records the gate resolutions.

## 0. Strategic Decisions (SD) — resolved by research, confirmed at the gate

### SD-1 — Auth backbone: per-agent OAuth authorize-not-copy (Option C′). Daemon demoted.

| Option | What | Verdict |
|---|---|---|
| A | Raw-key proxy daemon | Rejected: redundant with OAuth, anti-ADR-010. |
| B | OAuth-refreshing daemon (one machine identity, keyless localhost) | **Demoted.** Patches two fixable client gaps (pi mcp-client no-OAuth #4105; wizard not routing OAuth #1701) with always-on local infra; collapses N local harnesses into one upstream identity (breaks per-agent graph partitioning + client_id signal); forces us to own an OS keychain/lifecycle matrix (macOS login-keychain-locked-at-boot vs headless Linux no Secret Service → 0600 back door); nonstandard (SaaS ships OAuth, not credential daemons); the sandbox "phantom-token" precedent does not apply to trusted local agents. |
| **C′** | **Per-agent OAuth authorize-not-copy + per-graph scoped keys** | **Recommended v1.** Server already live (#524 `oat_` at `/mcp`, RFC 8707 resource → team; ADR-010 Tier-1). Each OAuth-capable harness authorizes as the human once and stores its own token in ITS keychain (already solved by Claude Code/Cursor). Non-OAuth/headless/CI use scoped `tk_` keys (#2083, live). Real remaining work = attribution plumbing + the two client gaps. |

**Identity model (owner-confirmed):** *harnesses (Claude Code, pi, Cursor) are
transport, not users.* The actor Tortoise needs is the **human user** (OAuth
`user_id` / key `created_by`). Agent model and machine are future
nice-to-haves (be schema-compatible now, build later).

### SD-2 — Graph attribution model: session→human (Option B-style) as the primary, event-level actor as the backstop. NOT per-node scalar stamping.

**Decision from research (industry code audit + literature; see
`docs/research/memory-attribution-industry-findings.md` and research brief
§Second-Pass ¶3):**

- **The industry does NOT stamp a per-record author on shared memory nodes.**
  - Zep/Graphiti: "whose" = `group_id` partition; provenance resolves through episode→entity edges. No `user_id` on nodes.
  - LangMem: `user_id` is a **namespace level**, never a per-memory field.
  - Letta: agent-scoped memory; human attribution is a server-validated request header, never written to memory nodes.
  - basic-memory: nullable audit columns on the entity table (no index); observations carry none.
  - Mem0 is the exception (per-record `user_id/agent_id/run_id`, payload-indexed) — and even there the field is the *retrieval scope key*, not decorative attribution.
- **PROV-compliant attribution is transitive:** entity `wasGeneratedBy` activity, activity `wasAssociatedWith` agent. A session node carrying the human IS the activity pattern; a direct per-node stamp is a *materialized inference*, not a compliance requirement. GDPR Art. 5(1)(c) data minimization actually disfavors persisting actor data resolvable transitively.
- **Tortoise code makes per-node scalar ownership structurally wrong:** same-content filings DEDUP to one node id (`pt_{content_hash[:62]}`). Two humans filing the same claim → ONE node. A scalar `owner` property cannot represent that; dedup/merge then needs author-as-edge/event anyway.
- **Cost:** per-node stamp adds an index write on the hottest path + a property on every node + plumbing every write surface, for a query ("all memories by X") that is equally served by session→user (index seek + 1 hop). Write amplification is the only material graph cost of per-node stamping, and it is strictly additive.

**Chosen model (SD-2):**
1. **Actor is resolved SERVER-SIDE from the credential** (OAuth token row `user_id`, JWT `sub`, or key `created_by`) — never client-claimed (Supabase/Stripe/Shopify pattern; client-claimed user IDs are rejected as untrusted).
2. **Session = the attribution anchor.** Captured sessions already group turns + extract points with provenance (`sourceSession`). Stamp the human on the session.
3. **Event-level actor as the backstop for non-session writes** (direct REST/MCP `create_point` with no session): the graph's journaled event already exists — carry the server-resolved actor there, so no write dangles unattributed. If "memories by X" ever becomes a measured hot path, denormalize ONE indexed `owner` property later (backfill from session/event) — one-way easy migration; the reverse (2A → 2B) is a lossy rewrite.

**Carriage decision (resolved at the human gate 2026-09-08, §7.2):**
stamp the actor on BOTH the Session node AND the write-event's actor field
(session = the human-readable anchor; event = the guaranteed-complete
backstop for sessionless writes; both are cheap). Session-only remains a
flagged contingency — if later chosen, the consequences are recorded in
E2E-4 / §1.1 item 3.

---

## 1. Scope Boundaries

### 1.1 In Scope (v1)

**Server-side (the "attribution plumbing" — the real missing piece):**

1. **Make the resolver return the human.** `resolve_oauth_access_token` (oauth.py:722) currently selects `user_id`/`client_id` but returns only the team dict via `_quota_fields` — the actor is DROPPED. Same for the key lanes (Supabase `resolve_api_key` has `created_by`; registry path has `k.created_by`). Change: attach `actor_user_id` (+ `client_id` for OAuth) to the resolved team dict, and expose it through `get_current_team*` + MCP resolution (ContextVar) so every downstream handler can stamp without touching every endpoint's signature.
2. **Stamp the actor on sessions + index it.** `_capture_session_impl`
   (hosted_api.py:6486+) writes the Session node (`MERGE (s:Session …)` with
   `created_at/turn_count/harness`) — add the server-resolved `actor_user_id`
   (and `harness` already there → the "which human + which tool" pair). Create
   an index on `Session.actor_user_id` so author-filtered retrieval stays an
   index seek + one hop (the cost bound is asserted deterministically in
   §1.1 item 7's integration tests against this index, not a label scan). **Pre-existing data disposition:** legacy Session nodes and
   journaled events carry NO actor (the resolver dropped it pre-epic) and no
   backfill is possible — the value was never persisted. Read paths treat a
   missing/null `actor_user_id` as unattributed (blank/"—" render, never a
   crash or a fabricated actor); see E2E-10's null-row sub-assertion and the
   §1.2 disposition row.
   Surface the human in the dashboard's existing Memory-sources capture
   row display — REUSING the existing row component: the read path
   (`GET /v1/sessions/{id}` / list-sessions) resolves `actor_user_id` →
   display name at READ time via the control-plane users lookup
   (email/name), failing soft to the id on a lookup miss and to
   unattributed on a null actor (matching
   list_sessions' existing fail-soft posture) — zero new UI surface, so no
   separate dashboard in-scope item is needed and the UX/Accessibility
   ratings stay coherent with §3. E2E-10 covers this. **Author-filter query
   consumer:** E2E-8's "show me memory filed by X" exercises a SMALL filter
   addition on the existing list-sessions surface (actor filter → provenance
   hop to that member's points) — scoped in Phase 1 as the consumer of the
   `Session.actor_user_id` index (no new query engine; reuse the graph's
   session/provenance edges).
3. **Event-level actor backstop + reject client-claimed actors.** Graph
   writes that create Points outside a session (direct REST `create_point`,
   MCP `create_point` tool calls) must not dangle unattributed: thread the
   resolved actor into the journaled write event's metadata
   (`initiated_by`/`agent_id` already exist on EventAPI construction; add the
   human actor alongside). **Coverage is ALL graph-write event types, not just
   Points** — every write event flows through the generic `EventAPI._emit`
   funnel (api.py:37, `_emit` carries `initiated_by`/`agent_id` for
   PointAdded/OperatorAdded/PointRevised/PointsMerged/SubjectAdded/etc.), so
   threading the actor at EventAPI construction stamps decision writes too
   (operator/NAND/mitigation/supersede) — honoring SD-2's "no write dangles
   unattributed" and the value map's "who filed/decided this".
   Cheap, one schema field on the event, guarantees
   "no unattributed write" without per-node properties. **Reject-vs-ignore
   contract:** the public API/SDK exposes NO client-settable actor field in
   v1 (none is added by this epic). Any forged actor claim smuggled through
   the props/metadata passthrough is stripped server-side and never stored as
   attribution — the stored actor is always the server-resolved one. This
   mirrors the existing server-managed-field sanitization (#329
   `_sanitize_props`), so it is a behavior extension of an established
   pattern, not a new 4xx surface. (If a documented client actor field is
   ever added later, it must be rejected with 4xx — noted here so the
   contract is forward-correct; E2E-3 tests today's strip-and-ignore
   reality.)
4. **No per-node `owner` property in v1** (SD-2). Do NOT add an indexed author property to Point/operator nodes now; revisit only if a measured hot query demands it (§0 SD-2).

**Client-side OAuth gaps (the "keyless" UX):**

5. **pi mcp-client OAuth support (#4105).** pi's mcp-client extension currently supports static headers only (verified: `~/.pi/agent/extensions/mcp-client/` + mirrored in agent-infra `extensions/mcp-client`, comment "See #4105"). Because agent-infra mirrors it, this epic (or a coordinated issue against agent-infra) adds OAuth (auto-discovery + PKCE + refresh) to that client. Owned surface: agent-infra.
6. **Wizard routing to authorize (#1701 generalize).** The dashboard
   self-fork connect step (wizardStep 2) must route OAuth-capable harnesses
   (Claude Code, Cursor; ChatGPT #1701 in progress) to the OAuth authorize
   flow instead of showing raw-key setup commands; the **fallback branch** —
   a harness where OAuth is unsupported (pi until #4105 lands, headless/CI
   on the hosted plane) — still shows the SDK-style key snippet and a freshly
   minted key that completes a write (verified by E2E-11, not left implicit).
   Coordinate with #1701 (do NOT duplicate; #2572 layout work already merged).
7. **Tests + live spike.** Unit/integration for resolver-returns-actor across
   ALL auth lanes (OAuth `resolve_oauth_access_token` user_id; Supabase
   `resolve_api_key` created_by; registry `created_by`), session stamping,
   event backstop, no-client-claimed-actor (forged claim → stripped
   server-side, never stored; assert stored actor == server-resolved, per
   §1.1 item 3 / E2E-3), and **index-usage cost bound for the actor filter:**
   assert via query-plan inspection (EXPLAIN) that the list-sessions actor
   filter uses the `Session.actor_user_id` index (index seek + one hop),
   never a label scan — no wall-clock timing (E2E-8's And-clause home). **Live
   hosted OAuth spike in Phase 1**, steps inlined here (the earlier draft
   doc is superseded/uncommitted — downstream plans must not chase it):
   (a) GET `https://api.premiselabs.co/.well-known/oauth-protected-resource/mcp`
   and `/.well-known/oauth-authorization-server/mcp` — both return 200 in
   production (research brief raw notes); (b) DCR against the AS metadata;
   (c) PKCE authorize (browserless/mocked exchange where CI cannot open a
   browser); (d) exchange code → `oat_` access + rotating refresh token;
   (e) call `/mcp/` with `Bearer oat_…` and confirm tools list;
   (f) refresh after TTL and confirm rotation.

**The residual local-relay idea is OUT of scope** (see §1.2) — filed separately by the epic owner.

### 1.2 Out of Scope (deferred or rejected, with reason)

| Item | Disposition |
|---|---|
| **Local credential-holding daemon / auth proxy** (original Option A/B) | REJECTED as auth backbone (SD-1). No localhost port, no keychain-owning process, no launchd/systemd lifecycle in this epic. |
| **Credential-agnostic local capture/offline relay** | Filed separately by the epic owner as an improvement issue (independent of this epic). If built later, hard rule: never holds credentials. |
| **Windows / per-OS keychain work** | Mostly Mooted — with no daemon, tokens live in harness keychains (Claude/Cursor already ship cross-platform). Only CLI-login (future) needs our own keychain, deferred. |
| **Machine / host attribution** | Future. Be schema-compatible (actor model above can gain `actor_type=machine` later); do not build now. |
| **Pre-existing (legacy) sessions/events with null actor** | No backfill possible or attempted (the actor was never persisted pre-epic). Read paths render missing actor as unattributed (blank/"—"), never fabricated; E2E-10 null-row sub-assertion guards it. |
| **Agent-model attribution** | Future (nice-to-have per owner). Session `harness` field is the seed; a model field can join capture metadata later. |
| **Per-agent identity AT the graph-write level in v1** (each tool call attributed to a distinct agent identity vs the human) | Deferred — the owner confirmed human-level attribution is the requirement; agent-level is future compatibility only. |
| **OAuth device flow / `tortoise login` CLI** | Future (headless/CI login nicety); v1 headless uses `tk_` keys. |
| **OAuth client_credentials grant** | Future builder feature (server-side, separate). |
| **Graph-scoped OAuth resources** (RFC 8707 resource → graph, not just team) | Future if per-agent OAuth graphs are needed; `tk_` per-graph keys cover partitioning today. |
| Modifying REST `/v1/*` auth failure semantics, hosted `/mcp` behavior | NOT touched beyond the resolver return-shape + stamping described above. |
| Session capture transport dedup / new hook surfaces | Not part of this epic (capture exists; we only stamp it). |

### 1.3 Boundary Rationale

The daemon was solving a problem OAuth 2.1 already solves. Removing it shrinks
the OS/keychain/lifecycle surface to zero and removes a shared-identity
anti-pattern that would have collided with the very attribution requirement
this epic now adds. What remains is genuinely missing: **the human actor is
dropped between auth and the graph** — a server bug-class with security and
memory-quality consequences (team memory cannot answer "who said/filed
this"). That is the core build. The client gaps (#4105, #1701) are the other
half of "keyless setup"; both are owned/coordinatable. Everything else is a
future option, never built speculatively.

---

## 2. Customer Value Map

| Scoped Capability | User-Visible Value |
|---|---|
| Per-agent OAuth connect (wizard routes capable harnesses to authorize) | Solo/team user connects Claude Code/Cursor/ChatGPT in ONE authorize click; no raw API key in any harness config, shell history, or prompt |
| pi mcp-client OAuth (#4105) | pi (our own harness) joins the keyless OAuth path instead of forcing static headers/key paste |
| Human attribution on sessions + write events | Team memory answers "who filed/decided this" — the shared-memory provenance that makes a team graph trustworthy |
| Server-side actor resolution (never client-claimed) | A user cannot forge another member's authorship; audit trail is trustworthy (security value) |
| Per-graph `tk_` keys (existing behavior) + optional wizard surfacing | Solo user keeps separate graphs per agent; builders/teams scope perms per graph without new server work. Surfacing delivery is gated on §7.5: wizard copy push if YES, Phase-3 docs if NO — the underlying behavior is already live (E2E-7) |
| No daemon | Zero new local attack surface, no background process to manage, no keychain matrix |

---

## 3. Complexity Ratings

| Axis | Rating | Rationale |
|---|---|---|
| Architecture | **medium** (medium-high IF §7.4 = owned) | No new service in the core. Resolver return-shape + actor threading through session/write-event paths touches shared auth middleware (careful, test-heavy) but is bounded. **Contingent:** if §7.4 pulls pi mcp-client OAuth (#4105) in as OWNED Phase-2 work, that client work (TS discovery→DCR→PKCE→rotating-refresh + token storage + re-auth UX) is the riskiest new code in the epic and rates Architecture **medium-high**; if coordinate-not-own, the epic's client surface is a thin wiring smoke (medium-low) and the risk homes in #4105. |
| UX | **medium** | Wizard branch copy/routing for OAuth harnesses (#1701 coordinate) + (implicit) pi config. No novel interaction. |
| Ontology | **low** | Two metadata fields (session actor, event actor). No new node/edge kinds, no EP-visible change. |
| Accessibility | **low** | No new UI surface in v1 beyond wizard text/branch reuse. |

---

## 4. High-Level E2E Test Cases

> Behavioral. Written for the redirected scope (per-agent OAuth + attribution).

### E2E-1: Keyless first connect via OAuth authorize (happy path)
**Given:** a solo self-fork user with an active account and an OAuth-capable
harness (Claude Code or Cursor).
**When:** they run the wizard connect step for that harness and complete the
one-time authorize.
**Then:** the harness is configured with the hosted `/mcp/` endpoint and NO
`tt_`/`tk_` key in its config; it lists Tortoise tools and completes a graph
write; the session capture attributes the write to the human.

### E2E-2: No raw key path for OAuth-capable harnesses
**Given:** E2E-1.
**When:** configs/shell/env are inspected.
**Then:** no raw key appears in harness config, shell profile, or process env;
a fresh env without `TORTOISE_API_KEY` still reaches Tortoise.

### E2E-3: Server-side actor — session attributed to the human
**Given:** a team member captures a session through an OAuth-authed harness.
**When:** the session lands.
**Then:** the Session node carries the server-resolved `actor_user_id` of that
member; the API/SDK exposes no client-settable actor field, and a forged
actor claim smuggled via props/metadata is stripped — never stored as
attribution, never trusted (strip-and-ignore per §1.1 item 3).
> Session-node assertions hold under BOTH §7.2 resolutions (session-only or
> stamp-both); the event-level assertions live in E2E-4 and are contingent
> there.

### E2E-4: No unattributed write (event backstop) — LANE MATRIX
**Given:** a sessionless (no-capture) write arriving via any auth lane.
**When:**
- (a) a `create_point` via the **MCP tool** over `/mcp`, authenticated with an
  OAuth `oat_` token (the OAuth surface — `oat_` is accepted ONLY at `/mcp`,
  never REST `/v1/*`);
- (b1) direct REST `create_point` authenticated with a `tk_`/`tt_` key on a
  **Supabase-backed** control plane;
- (b2) the same key-authenticated REST write on the **registry/selfhost**
  control plane (`TORTOISE_CONTROL_PLANE=registry`).
**Then:** in (a) the journaled write event carries the OAuth token's
server-resolved `user_id`; in (b1) it carries the Supabase key's
`created_by`; in (b2) it carries the registry key's `created_by` — in ALL
cases the point is resolvable to a human via the event, never silently
anonymous. (Lane matrix is explicit because the Supabase
`resolve_api_key`→`created_by` and registry `created_by` branches are distinct
code paths with their own bug risk. If a lane cannot be exercised in CI,
that gap is named in §1.1 item 7's integration tests rather than silently
claimed by this E2E.)
> **Contingency (recommended §7.2 resolution = stamp BOTH):** E2E-4's
> "event carries the actor" assertions hold ONLY under the stamp-both
> resolution. If the gate instead chooses session-only, the event-level
> backstop (§1.1 item 3) and this E2E's event assertions are dropped;
> sessionless writes then carry no actor and the "no unattributed write"
> property is scoped out. Flagged here so a gate change inverts the right
> surface (mirrors E2E-9's §7.4 ownership note).

### E2E-5: Dedup keeps attribution correct (two humans, same claim)
**Given:** two team members each file the same claim content.
**When:** the second filing dedups to the existing node
(`pt_{content_hash}`).
**Then:** the node is not duplicated; BOTH actors remain resolvable via their
sessions/events (no single-owner overwrite); the graph does not lose who
filed it.

### E2E-6: Team member revocation
**Given:** a member leaves the team / their OAuth token is revoked.
**When:** a post-revocation write attempt occurs.
**Then:** it is rejected (existing suspension/revocation machinery, #308);
previously attributed sessions/points are NOT lost or retroactively
rewritten.

### E2E-7: Per-graph isolation via tk_ key (partition use case)
**Given:** a solo user wants graph A for agent A and graph B for agent B.
**When:** they mint two per-graph `tk_` keys.
**Then:** agent A's key writes only to graph A; agent B's only to graph B
(C5 routing); a cross-graph write from the wrong key is scoped/blocked.

### E2E-8: Attribute query — "show me memory filed by X"
**Given:** a team graph with sessions/points from multiple members.
**When:** a member filters list-sessions by a specific human (the §1.1
item-2 actor-filter addition; §7.3 confirms this is the v1 audit read
surface, not hot retrieval).
**Then:** the query returns that member's sessions and their provenance-
linked points, excluding other members'.
**And (cost bound, deterministic):** the §1.1 item-7 integration tests assert
via query-plan inspection (EXPLAIN) that the actor-filter uses the
`Session.actor_user_id` index (index seek + one hop) — NOT a label scan.
Wall-clock timing is deliberately NOT used (flaky in CI); E2E-8 itself
asserts only the functional exclusion behavior.

### E2E-9: Client OAuth for pi — first authorize + refresh (contingent on §7.4)
**Given:** pi configured against hosted `/mcp/` with OAuth client support.
**When (a):** the user runs pi's first-time connect and completes
discovery→DCR→PKCE authorize.
**Then (a):** pi stores its token and lists Tortoise tools with no key paste.
**When (b):** the access token expires mid-session.
**Then (b):** pi's mcp-client transparently refreshes (rotating refresh
token); no key re-entry; failures surface as a clear re-auth signal, never a
silent raw-key fallback.
> **Ownership note:** E2E-9 is IN this epic's deliverable/test surface ONLY
> if §7.4 chooses *owned* (pull #4105 into Phase 2). If §7.4 chooses
> *coordinate-not-own*, the authorize+refresh client behavior is homed in
> #4105 (agent-infra) and this epic's E2E reduces to an integration smoke:
> pi wired against hosted `/mcp/` completes an authorize with no key, with
> #4105 owning the client-internals assertions.

### E2E-10: Dashboard session shows the human (display-name resolution)
**Given:** three sessions on the Memory-sources rows: (i) captured and
attributed to member M (E2E-3); (ii) a legacy pre-epic session whose
`actor_user_id` is null/missing; (iii) a session whose `actor_user_id` has
no control-plane users lookup entry (stale/unknown actor id).
**When:** a teammate views the Memory-sources capture rows / list-sessions.
**Then:** (i) displays M's identity — resolved at READ time via the
control-plane users lookup (email/name); (ii) renders as unattributed
(blank/"—", never a crash, never a fabricated actor) per §1.1 item 2's
pre-existing-data disposition; (iii) fails soft to showing the raw id
(no crash). The display never exposes another member's credentials and
exercises the SAME data the graph stores (no second copy).

### E2E-11: Wizard fallback branch — key snippet for non-OAuth harnesses
**Given:** a user on a harness where OAuth is unsupported (pi until #4105
lands, or headless/CI on the hosted plane) reaches the wizard connect step.
**When:** the wizard detects no OAuth capability for that harness.
**Then:** the fallback branch surfaces the SDK-style key snippet with a
freshly minted key; the user configures the harness with that key and a graph
write completes. (Complement of the keyless path — verifies the fallback
branch survives the copy/routing change, per §1.1 item 6.)

---

## 5. Axis Research Notes

> Research basis: research brief `2026-09-08-tortoise-agent-daemon.md`
> (second-pass verdict supersedes the original daemon framing) +
> `memory-attribution-industry-findings.md` (code-level audit: Graphiti, Mem0,
> Letta, LangMem, basic-memory).

**Architecture — actor resolution (who the actor is):**
- OAuth access-token row already stores `user_id` + `client_id` (oauth.py);
  resolution must return them (currently `_quota_fields` only).
- Key lanes already store `created_by` (registry Cypher `RETURN k.created_by`)
  and Supabase `resolve_api_key` has membership/owner context — one consistent
  `actor_user_id` on the resolved team dict unifies REST/MCP/session stamping.
- Pattern authority: Stripe activity logs derive `actor` from the API
  credential; Shopify derives identity from the token; Supabase RLS uses
  `auth.uid()` — server-side actor, client claims rejected.

**Ontology/attribution — session vs per-node (2A vs 2B):**
- Industry: no per-node author stamps on shared memory (Graphiti `group_id`
  partition; LangMem namespace; Letta server-validated header; basic-memory
  nullable entity audit columns). Mem0's per-record ids are the indexed
  retrieval scope — closest analog, but Tortoise's session+event model is
  closer to PROV's transitive pattern already.
- PROV/GDPR: transitive attribution via an activity node (session) is the
  compliant, data-minimizing shape; per-node stamps are materialized
  inference.
- Tortoise-specific structural fact: content-hash dedup merges same-content
  filings into one node → scalar owner cannot represent multi-filer; author
  must live on edges/events. (verified: `pt_{content_hash[:62]}` ids, sdk.py)

**Client OAuth — the two gaps:**
- pi mcp-client is static-headers-only with an in-code TODO (#4105); owned
  (agent-infra mirror). Adding OAuth to it is the difference between "pi
  forces key paste" and "pi is keyless."
- Wizard: #1701 (ChatGPT OAuth routing) is the pattern to generalize to
  Claude Code/Cursor for the self-fork connect step; #2572 fixed the fork
  branch layout (merged) and is the copy surface.
- Hosted discovery/DCR/PKCE/refresh are production-verified (#524, research
  brief); the live spike in Phase 1 de-risks the client work.

---

## 6. Phase Plan

| Phase | Scope | Effort (est.) |
|---|---|---|
| 1 | **Server attribution plumbing**: resolver returns actor (user_id/client_id/created_by) → sessions stamped (all event types via EventAPI._emit) → `Session.actor_user_id` index + small list-sessions actor filter (E2E-8 consumer) → write-event actor backstop; tests for forgery/dedup/revocation + EXPLAIN index-usage bound; live hosted OAuth spike | ~1.5–2 weeks |
| 2 | **Client OAuth**: pi mcp-client OAuth (#4105, agent-infra coordination) + wizard OAuth-routing generalization for Claude Code/Cursor (#1701 coordinate, no duplication) + IF §7.5 = YES, per-graph key-minting copy in the wizard (UX scope + E2E hook noted at the gate; otherwise Phase-3 docs carry it) | ~1.5–2 weeks |
| 3 | Docs + dashboard surfacing (sessions show human; graph docs explain attribution) | ~3–5 days |
| 4 | Future (no build now): machine/model attribution fields, OAuth device flow/`tortoise login`, graph-scoped OAuth resources, client_credentials grant, per-agent identity at write level | — |

Phases 1–3 are separate child issues at decompose; Phase 1 is the v1 gate.

---

## 7. Human-Gate Resolutions (approved 2026-09-08) — recorded for downstream

> All items below were approved by the epic owner on 2026-09-08 ("go ahead
> with all of it"). Conditional items record the RECOMMENDED resolution and
> the flag point if a different branch is later chosen. E2E/scope text
> reflects these resolutions.

1. **SD-1 — APPROVED: Option C′** (per-agent OAuth authorize-not-copy +
   per-graph `tk_` keys). Local daemon demoted; scope is written for C′.
2. **SD-2 — APPROVED: session→human + event-level actor backstop, NO
   per-node scalar owner in v1.** Stamp BOTH the Session node AND the
   write-event actor field (recommended). If session-only is later chosen,
   E2E-4's event assertions + §1.1 item 3's backstop drop (contingency
   noted in E2E-4).
3. **Team-memory query intent — CONFIRMED:** the v1 question is "which human
   filed/decided this" (audit + trust). The session-index actor filter
   (E2E-8) IS the v1 audit read surface — a small filter on existing
   list-sessions, not a hot retrieval engine. Only the denormalized per-node
   owner path (2A-as-cache) is deferred until/unless author-filtered
   retrieval becomes a measured hot path.
4. **Client-gap ownership — COORDINATE-NOT-OWN (recommended default):**
   this epic coordinates pi mcp-client OAuth (#4105, agent-infra surface)
   and wizard routing (#1701); it does NOT pull #4105 in as owned Phase-2
   work. If owned is later chosen, Architecture rating flips to medium-high
   and E2E-9 becomes an in-epic deliverable (both flagged in §3/E2E-9).
5. **Per-graph surfacing — PHASE-3 DOCS (recommended):** "graph per agent"
   and "different perms per graph" are served by existing `tk_` per-graph
   keys + Phase-3 docs; wizard copy does NOT actively push per-graph key
   minting in Phase 2 unless §6 Phase 2's IF-clause is later triggered.
>>>>>>> origin/main
