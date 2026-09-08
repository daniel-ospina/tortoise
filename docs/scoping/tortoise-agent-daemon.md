---
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