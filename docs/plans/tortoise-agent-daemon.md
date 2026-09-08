---
title: "Tortoise Agent Daemon — Implementation Plan"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-08
subjects.team: epistemic-team
---

<!-- research-path: docs/research/2026-09-08-tortoise-agent-daemon.md -->
<!-- scoping-path: docs/scoping/tortoise-agent-daemon.md -->

# Tortoise Agent Daemon — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Status:** Draft (pre plan-review)
**Complexity:** Standard (tier=Standard)
**Prerequisites:** Research brief (`docs/research/2026-09-08-tortoise-agent-daemon.md`) + Scoping (`docs/scoping/tortoise-agent-daemon.md`) — both clean.
**Goal:** A background daemon (`tortoise-agent`) that holds the user's API key in memory, proxies MCP requests from any agent harness to `api.premiselabs.co`, and is managed through the `tortoise` CLI. Zero per-harness configuration for the "self" fork user.

**Team:** epistemic-team
**Role:** platform-engineering

---

## 1. Design Decisions

### D1: Python async HTTP proxy using aiohttp (not stdlib http.server)

| Decision | Choice | Rejected alternatives |
|---|---|---|
| Server framework | aiohttp (async, battle-tested, streaming support) | `http.server` (sync, no SSE streaming); `flask` (sync, not suitable for long-lived proxy); `uvicorn + starlette` (heavier than aiohttp for this use case) |

**Rationale:** The daemon needs to forward SSE response streams from the API back to the agent. aiohttp supports `StreamResponse` natively — each MCP POST that gets an SSE response keeps the connection open, and aiohttp's async event loop streams chunks without blocking. This is the simplest async HTTP framework that handles streaming without additional dependencies.

The daemon is NOT a FastAPI/FastMCP server — it doesn't run Tortoise tools. It's a transparent proxy. aiohttp is the right tool.

### D2: Single in-memory key stored as plain string (not keyring/encrypted)

| Decision | Choice | Rejected alternatives |
|---|---|---|
| Key storage | Python `str` attribute on the daemon class | OS keyring (password-store, Secret Service); encrypted file; hardware TPM |

**Rationale:** The key enters the process via `TORTOISE_API_KEY` env var at startup. It's already in plaintext in the environment. Moving it to the OS keyring adds a native-code dependency (`keyring` library) and a runtime component (keyring daemon must be running, dbus session on Linux). For v1, the simpler approach is:
1. Read `TORTOISE_API_KEY` from env.
2. `os.unsetenv("TORTOISE_API_KEY")` immediately.
3. Store as `self._api_key: str`.

The key is never written to disk by the daemon. The keyring integration is a potential v2 enhancement.

**Tradeoff acknowledged:** The key remains in the process's memory space (`/proc/<pid>/mem` on Linux, swap, core dumps). This is the same risk profile as any application holding a secret in memory. Mitigation: `mlock()` advisory (Python's `ctypes` can call it, but it's not reliable across platforms) is deferred — the threat model is local key leakage from the user's own machine, not remote exploitation.

### D3: PID-based lifecycle management (not socket-activated)

| Decision | Choice | Rejected alternatives |
|---|---|---|
| Process lifecycle | PID file at `~/.tortoise/agent.pid` + OS service manager | launchd socket-activation; systemd socket-activated service |

**Rationale:** The daemon is always-on (it holds a key and listens for connections). Socket activation adds complexity — the service manager starts the daemon on the first connection, but the daemon needs to validate the key on startup. PID-based management is simpler: `tortoise agent start` writes the PID, `stop` sends SIGTERM, `status` checks `kill -0`. The OS service manager (launchd/systemd) restarts on crash via `KeepAlive`/`Restart=always`.

### D4: Config as TOML in `~/.tortoise/config.toml` (key NOT in config)

| Decision | Choice | Rejected alternatives |
|---|---|---|
| Config format | TOML (non-secret config only) | YAML (less strict); JSON (less human-editable); INI (no nested support) |

**Rationale:** TOML is Python's stdlib (3.11+ `tomllib`) and the project's convention (existing configs use TOML). The config stores only non-secret settings:
```toml
[daemon]
port = 9784
log_level = "info"
session_capture = false
```

The API key is NEVER in this file. It arrives via env var and lives in memory.

### D5: Not-in-scope for v1 — explicitly deferred

- **Session capture** — Phase 3. JSONL request/response journaling adds file-system writes and rotation logic. Not needed for the core proxy use case.
- **Offline resilience** — Phase 4. Requires buffer queue and replay logic. Not needed for core.
- **TLS** — localhost-only. No TLS needed.
- **Windows** — Deferred. No Windows launchd/systemd equivalent in the Python stdlib ecosystem.
- **Dashboard health integration** — Phase 2. The daemon exposes a health endpoint; the dashboard shows daemon status.

---

## 2. The Daemon CLI Interface

All subcommands under `tortoise agent`. Added to `tortoise/__main__.py`'s `AgentParser`.

```text
Usage: tortoise agent <command> [options]

Commands:
  start     Start the daemon (install + launch if not running)
            Options:
              --port PORT    Override default port (9784)
              --no-daemonize Run in foreground (for debugging)

  stop      Gracefully stop the daemon (SIGTERM)

  status    Show daemon status
            Output: running/stopped, PID, port, uptime, key status (loaded/missing)

  restart   Stop then start

  logs      Tail daemon logs
            Options:
              --follow      Follow (tail -f)
              --lines N     Show last N lines (default 50)

  config    View or set configuration
            Usage:
              tortoise agent config              → show current config
              tortoise agent config set <key> <value>  → set a config value
              tortoise agent config set session-capture true
```

Example output for `tortoise agent status`:
```
tortoise-agent: RUNNING
  PID:    88421
  Port:   9784
  Uptime: 3d 14h 22m
  Key:    loaded
  Ver:    0.1.0
```

---

## 3. Implementation Steps — Phase 1: Minimal Daemon

### Task 1: Core proxy daemon — `tortoise/agent_daemon.py`

**Intent:** Create the minimal HTTP proxy daemon that listens on localhost and forwards MCP requests.

**Acceptance:**
- `AgentDaemon` class with `start()`, `stop()`, and `async _proxy_request()` methods.
- Listens on `127.0.0.1:9784` (configurable via `--port`).
- `POST /mcp` forwards all request headers (except `Authorization` which it overrides) + body to `https://api.premiselabs.co/mcp`.
- Streams SSE responses back to the caller (Content-Type: `text/event-stream`).
- Returns `200` JSON for JSON responses, `202` for notifications.
- `GET /health` returns `{"status": "ok", "key_loaded": true/false, "uptime": ...}`.
- On shutdown (SIGTERM), completes in-flight requests with a 3-second timeout, then exits.

**Edge cases:**
- **API unreachable:** Returns HTTP 502 Bad Gateway with `{"error": "upstream unreachable"}`.
- **No key loaded:** Returns HTTP 503 Service Unavailable with `{"error": "no API key"}`.
- **Connection timeout:** 10-second default timeout on upstream requests, configurable.
- **SSE stream interrupted:** If the upstream SSE stream drops mid-response, the proxy drops the client connection too.

**Files:**
- Create: `tortoise/agent_daemon.py`
- Modify: None yet (CLI integration in Task 3)

**Test:**
- `tests/test_agent_daemon.py` (new) — unit tests with mock upstream server (aiohttp test client). Tests: proxy forwarding, header injection, SSE streaming, error handling, health endpoint.

```python
# sketch of the core class
class AgentDaemon:
    def __init__(self, api_key: str, port: int = 9784, upstream: str = "https://api.premiselabs.co"):
        self._api_key = api_key
        self._port = port
        self._upstream = upstream.rstrip("/")
        self._app = web.Application()
        self._app.router.add_post("/mcp", self._handle_mcp)
        self._app.router.add_get("/health", self._handle_health)
        self._start_time: float | None = None
        self._runner: web.AppRunner | None = None

    async def _handle_mcp(self, request: web.Request) -> web.StreamResponse | web.Response:
        # Forward headers (override Authorization)
        headers = dict(request.headers)
        headers["Authorization"] = f"Bearer {self._api_key}"
        # Forward body
        body = await request.read()
        # Proxy to upstream
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self._upstream}/mcp", headers=headers, data=body) as resp:
                # Stream back
                response = web.StreamResponse(status=resp.status, headers=resp.headers)
                await response.prepare(request)
                async for chunk in resp.content.iter_chunked(8192):
                    await response.write(chunk)
                return response
```

### Task 2: Key management — inject, hold, validate

**Intent:** The daemon reads the API key from environment, validates it, and holds it in memory.

**Acceptance:**
- `TORTOISE_API_KEY` env var is read on daemon startup.
- `os.unsetenv("TORTOISE_API_KEY")` called immediately after reading.
- Key is stored as `self._api_key: str` — never written to disk.
- On startup, daemon makes a validation request to `GET https://api.premiselabs.co/v1/me` with the key.
- If validation fails (401/403), daemon exits with error code 1 and descriptive message.
- `mlock()` attempted as best-effort (optional — logged if not available).
- On shutdown, `self._api_key = ""` and optionally `ctypes.memset` for sanitization.

**Note on key validation:**
The `/v1/me` endpoint (or equivalent) must exist on the API. If it doesn't yet, the validation step is deferred — the daemon trusts the key at startup and validates on first proxy error.

**Files:**
- Modify: `tortoise/agent_daemon.py` (add `_load_key()`, `_validate_key()`, `_sanitize_memory()`)

**Tests:**
- Unit: key read from env, unsetenv verified, failed validation → exit
- Integration: daemon starts with valid key; daemon exits with invalid key

### Task 3: CLI surface — `tortoise agent` subcommand

**Intent:** Add `agent` subcommand to the existing `tortoise` CLI (`__main__.py`).

**Acceptance:**
- `tortoise agent start` launches the daemon process as a subprocess (daemon mode).
- `tortoise agent stop` sends SIGTERM to the PID in `~/.tortoise/agent.pid`.
- `tortoise agent status` checks PID file, `kill -0`, port liveness (`GET http://localhost:9784/health`).
- `tortoise agent logs` tails `~/.tortoise/agent.log`.
- All commands handle error states (not running, already running, permission denied).
- `tortoise agent --help` shows the command reference.

**CLI architecture:**
- New `AgentParser` in `__main__.py` or `tortoise/cli_agent.py` (to keep `__main__.py` manageable).
- Daemon launched as subprocess: `Popen([sys.executable, "-m", "tortoise", "agent", "_daemon", "--port", str(port)], env={"TORTOISE_API_KEY": key})`.
- The `_daemon` hidden subcommand runs `AgentDaemon().run_forever()`.

**Files:**
- Create: `tortoise/cli_agent.py` (agent CLI handler)
- Modify: `tortoise/__main__.py` (register `agent` subcommand, add `_daemon` hidden subcommand)

**Tests:**
- `tests/test_cli_agent.py` (new) — unit tests for PID management, signal handling, status parsing.
- Integration: start daemon → status shows running → stop → status shows stopped.

### Task 4: lifecycle management — launchd + systemd + profile fallback

**Intent:** The daemon auto-starts on login, restarts on crash, and is managed by the OS service manager.

**Acceptance:**
- macOS: `~/Library/LaunchAgents/co.premiselabs.tortoise-agent.plist` installed by `tortoise agent start` and removed by `tortoise agent stop`.
- Plist includes `KeepAlive=true`, `ThrottleInterval=5`, `RunAtLoad=true`.
- macOS: `launchctl bootstrap` / `launchctl bootout` used for install/uninstall.
- Linux: `~/.config/systemd/user/tortoise-agent.service` installed similarly.
- Linux: `systemctl --user enable/start` for activation.
- Fallback: `~/.profile` / `~/.bashrc` entry for systems without launchd/systemd (WSL, containers).
- `tortoise agent start --no-service` skips service manager install (for CI/containers).

**Files:**
- Create: `tortoise/templates/launchd/co.premiselabs.tortoise-agent.plist` (macOS plist template)
- Create: `tortoise/templates/systemd/tortoise-agent.service` (Linux systemd unit)
- Modify: `tortoise/cli_agent.py` (service install/uninstall logic)
- Modify: `scripts/install-launchd.sh` (optionally register the Tortoise daemon plist)

**Tests:**
- Unit: plist/service file rendering with correct paths.
- Integration (manual/CI): install → daemon starts → kill → daemon restarts → uninstall → daemon stops.

### Task 5: Installer integration

**Intent:** The existing `curl ... | bash` installer (`install-tortoise-skills.sh`) also installs and starts the daemon.

**Acceptance:**
- Installer detects `--harness` flag and, for any harness that supports MCP HTTP (all), installs the daemon.
- Installer reads `TORTOISE_API_KEY` from environment or prompts for it (fallback — ideally the key is already set).
- Installer calls `tortoise agent start` with the key.
- Installer verifies daemon health via `GET http://localhost:9784/health`.
- Installer output includes: `[✓] tortoise-agent daemon started on port 9784`.
- Idempotent: re-installing updates the daemon binary, restarts the service.

**Key injection flow:**
```bash
# Inside install-tortoise-skills.sh, after skills are installed:
if command -v tortoise &>/dev/null; then
    echo "Installing tortoise-agent daemon..."
    TORTOISE_API_KEY="$TORTOISE_API_KEY" tortoise agent start
    if tortoise agent status | grep -q "RUNNING"; then
        echo "  ✓ daemon running on localhost:9784"
        echo "  Config: use http://localhost:9784/mcp as your MCP server URL"
    else
        echo "  ⚠ daemon failed to start — check ~/.tortoise/agent.log"
    fi
fi
```

**Files:**
- Modify: `scripts/install-tortoise-skills.sh` (add daemon install step, key injection)
- Modify: `scripts/install-launchd.sh` (add Tortoise daemon plist to managed templates)

**Tests:**
- Manual: run installer → verify daemon running → check key loaded → stop daemon.

### Task 6: Verification pass — typecheck, unit tests, integration

**Intent:** Full verification before committing Phase 1.

**Acceptance:**
- `mypy tortoise/agent_daemon.py tortoise/cli_agent.py` passes.
- `uv run pytest tests/test_agent_daemon.py tests/test_cli_agent.py -v` passes.
- Manual: daemon starts, proxies a real MCP call, stops cleanly.
- PID file management: start, stop, restart, stale PID recovery.
- Port conflict: start on occupied port → clear error.

### Task 6a: Subagent dispatch

Tasks 1–5 can be dispatched in dependency order:
1. Task 1 (core proxy) + Task 2 (key management) → independent, can be parallel
2. Task 3 (CLI) → depends on Task 1 + 2
3. Task 4 (lifecycle) → depends on Task 3
4. Task 5 (installer) → depends on Task 4

---

## 4. File Changes Needed

### New files

| File | Purpose |
|---|---|
| `tortoise/agent_daemon.py` | Core daemon: aiohttp proxy, key management, health endpoint |
| `tortoise/cli_agent.py` | CLI handlers for `tortoise agent start\|stop\|status\|restart\|logs\|config` |
| `tortoise/templates/launchd/co.premiselabs.tortoise-agent.plist` | macOS launchd plist template |
| `tortoise/templates/systemd/tortoise-agent.service` | Linux systemd user service unit |
| `tests/test_agent_daemon.py` | Unit + integration tests for the daemon |
| `tests/test_cli_agent.py` | CLI test: start/stop/status lifecycle |

### Modified files

| File | Change |
|---|---|
| `tortoise/__main__.py` | Add `agent` subcommand parser, `_daemon` hidden subcommand |
| `scripts/install-tortoise-skills.sh` | Add daemon install + key injection step |
| `scripts/install-launchd.sh` | Optionally register Tortoise agent plist |
| `tortoise/__init__.py` | Export `AgentDaemon` (or not — kept internal) |

---

## 5. Testing Strategy

### Unit tests (`tests/test_agent_daemon.py`)

| Test | What it covers |
|---|---|
| `test_proxy_forwards_headers` | Request headers (except Authorization) forwarded |
| `test_proxy_injects_authorization` | `Authorization: Bearer <key>` added |
| `test_proxy_streams_sse` | SSE response from upstream streamed back |
| `test_proxy_json_response` | JSON response proxied correctly |
| `test_proxy_upstream_unreachable` | 502 on connection error |
| `test_proxy_no_key` | 503 when key not loaded |
| `test_health_endpoint` | `GET /health` returns status |
| `test_key_reads_from_env` | Key loaded from `TORTOISE_API_KEY` |
| `test_key_unsetenv` | Env var unset after read |
| `test_key_validation_success` | Validation request succeeds |
| `test_key_validation_failure` | Validation fails → daemon exits |
| `test_shutdown_graceful` | SIGTERM → cleanup within timeout |

### Integration tests (`tests/test_cli_agent.py`)

| Test | What it covers |
|---|---|
| `test_start_daemon` | `tortoise agent start` launches process |
| `test_status_running` | `tortoise agent status` shows RUNNING |
| `test_status_stopped` | `tortoise agent status` after stop shows STOPPED |
| `test_stop_daemon` | `tortoise agent stop` sends SIGTERM |
| `test_restart_daemon` | Stop + start |
| `test_pid_file_created` | `agent.pid` exists after start |
| `test_pid_file_removed` | `agent.pid` removed after stop |
| `test_port_override` | `--port 9785` works |
| `test_stale_pid_recovery` | PID file with dead process → status shows STOPPED |

### Manual tests

| Scenario | Steps |
|---|---|
| Fresh install | Run installer → daemon starts → check status → use with agent |
| Key validation failure | Set invalid `TORTOISE_API_KEY` → daemon exits with error |
| Port conflict | Start on port 9784, start another on same port → error |
| Crash restart | Kill daemon → launchd/systemd restarts within 5 seconds |
| Uninstall | Remove daemon → services cleaned up → no residual processes |

---

## 6. Phase Plan

### Phase 1 (v1, ~2 weeks): Minimal Daemon

| Task | Effort | Dependencies |
|---|---|---|
| 1. Core proxy daemon | 3 days | None |
| 2. Key management | 1 day | Task 1 |
| 3. CLI surface | 2 days | Tasks 1, 2 |
| 4. Lifecycle (launchd/systemd) | 2 days | Task 3 |
| 5. Installer integration | 1 day | Task 4 |
| 6. Tests + verification | 2 days | All above |

**Total: ~11 working days**

### Phase 2 (v2, ~1 week): Health endpoint + Dashboard integration

| Task | Effort |
|---|---|
| 1. Enhanced health endpoint (stats, request count, error rate) | 1 day |
| 2. Dashboard UI: daemon status card | 2 days |
| 3. Dashboard: port config, restart button | 1 day |
| 4. Dashboard: key status indicator | 1 day |
| 5. Tests | 2 days |

**Total: ~5 working days**

### Phase 3 (v3, ~1 week): Session capture

| Task | Effort |
|---|---|
| 1. JSONL request/response journal (opt-in) | 1 day |
| 2. Log rotation (max 10MB, oldest dropped) | 1 day |
| 3. `session-capture` config flag | 0.5 day |
| 4. Integration with `session-postmortem.sh` | 1 day |
| 5. Tests | 2 days |

**Total: ~5 working days**

### Phase 4 (v4, ~1 week): Offline resilience

| Task | Effort |
|---|---|
| 1. Outbox buffer (capped JSONL, capped entries) | 1 day |
| 2. Reconnect detection + replay | 2 days |
| 3. Stale-write rejection (buffer expires after 1 hour) | 1 day |
| 4. Config: `tortoise agent config set offline-buffer true` | 0.5 day |
| 5. Tests | 2 days |

**Total: ~5 working days**

### Total program

| Phase | Effort | Value delivered |
|---|---|---|
| Phase 1 | ~2 weeks | Core daemon — key isolation, zero-config MCP proxy |
| Phase 2 | ~1 week | Dashboard visibility — daemon health from the web UI |
| Phase 3 | ~1 week | Session capture — automatic debugging data |
| Phase 4 | ~1 week | Offline resilience — write buffer + replay |

**Full program: ~5 weeks**

### Key milestones

| Milestone | Criteria |
|---|---|
| M1: Core proxy works | `tortoise agent start` → agent queries succeed → `tortoise agent stop` |
| M2: Auto-start on login | Reboot → daemon starts → agent reconnects without manual action |
| M3: Installer integration | `curl ... | bash` installs skills + daemon, user never touches API key |
| M4: Dashboard shows daemon | Dashboard has "Agent Daemon" status card |
| M5: Session capture live | Daemon writes session logs, `session-postmortem.sh` consumes them |
| M6: Offline tolerance | Network down → buffered writes → reconnect → replay |

---

## 7. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Key leak via process dump** | Low | High | `ctypes.memset` on shutdown; `mlock()` advisory; document threat model |
| **Port conflict** | Medium | Low | Clear error message; `--port` override; auto-detect port-in-use on start |
| **launchd/systemd config varies across OS versions** | Medium | Low | Test against macOS 14+ and Ubuntu 22.04+/Fedora 38+; CI matrix |
| **aiohttp SSL context issues** | Low | Medium | Default SSL context; `SSL_CERT_FILE` override support |
| **Daemon crashes silently** | Low | Medium | `KeepAlive`/`Restart=always`; daemon logs to `~/.tortoise/agent.log` |
| **SSE response streaming hangs** | Low | Medium | 10-second upstream timeout; daemon closes hung connections |
| **Installer script becomes fragile** | Medium | Medium | Idempotent install; test in CI pipeline |
| **Agent harness incompatibility** | Low | Low | Transparent proxy guarantees compatibility; test against Claude Code, Cursor, Pi |

---

## 8. Verification Plan

**Domain(s):** code (Python daemon + installer shell scripts). **Complexity:** Architecture=medium, Integration=medium-high, Security=high.

| # | Skill/Layer | Depth | Reason |
|---|---|---|---|
| 1 | pytest unit | Full | Proxy logic, key management, CLI parsing |
| 2 | pytest integration | Full | Daemon start/stop, HTTP forwarding against mock upstream |
| 3 | Manual E2E | Full | Real MCP call through daemon to api.premiselabs.co |
| 4 | OS lifecycle | Manual | launchd restart on crash; systemd user service |
| 5 | Security review | Required | Key storage, env var cleanup, memory sanitization, log leakage |
| 6 | Regression | Full | Full `python -m pytest tests/ -v` green at each phase gate |
| 7 | Documentation | Required | `tortoise agent --help`, README, AGENTS.md update |
| 8 | UX | Skip | No user-facing UI changes (daemon is backend + CLI) |

---

## 9. Open Items

1. **Does `GET /v1/me` (key validation endpoint) exist yet?** If not, the validation step in Task 2 is deferred — daemon validates on first proxy error instead.
2. **Is `~/.tortoise/` the canonical tortoise config directory?** Currently ad-hoc (some configs in `~/.config/tortoise/`, some in `~/.tortoise/`). Need to pick one. Recommend `~/.tortoise/` for consistency with the daemon's PID and log files.
3. **Does the API's `/mcp` endpoint support the Streamable HTTP transport?** (It should — MCP server uses FastMCP which supports Streamable HTTP. Verify before implementation.)
4. **Should `tortoise agent start` auto-select a port when 9784 is busy?** (No — fail with clear error. The user chose the port; they need to resolve the conflict.)
5. **Launchd plist `KeepAlive` vs `ThrottleInterval` interaction:** If the daemon crashes repeatedly (e.g., invalid key), launchd will keep restarting it. Should the daemon detect repeated crashes and back off? (Minimal v1 — launchd's `ThrottleInterval` handles this, but test the behavior.)

<!-- End of plan -->