# CWD-Leak Orphan Cleanup (issue #176)

Repeatable procedure for cleaning **path-based** redis-server orphans left by
pre-migration relative-path scripts (Category-3 leaks). The reaper NEVER
touches these (they're classified `protected` — path-based), so they need
manual cleanup after the Child 2 migration is deployed.

> Run this AFTER `tortoise migrate-db` (Child 2) and AFTER the reaper's
> no-path bootstrap. Path-based orphans are leftovers from old scripts that
> used `FalkorProjection('tortoise.db')` resolving per-CWD — no live clients
> are expected post-migration.

## Step 1 — Enumerate path-based servers

```bash
lsof -i -U | grep redis-server
```

This lists ALL processes with unix sockets. Filter to redislite servers:

```bash
ps -eo pid,args | grep 'redislite/bin/redis-server'
```

Each line's `unixsocket:<path>` is the server's socket. **Path-based servers**
have their socket in a tempdir but their DB at a user path (CWD or
`~/.tortoise/...`). No-path orphans were already cleaned by the reaper.

## Step 2 — Verify 0 active clients per server

For each candidate socket path:

```bash
redis-cli -s <socket_path> CLIENT LIST
```

**Expect 0 rows** (or only your own fresh connection, age=0). Any real client
(age >= 2s or named) means the server is in use — SKIP it.

## Step 3 — SIGTERM, escalate to SIGKILL

```bash
# Find the server PID from the lsof/ps output
kill <pid>          # SIGTERM first
sleep 10
kill -0 <pid> 2>/dev/null && kill -9 <pid>   # escalate if still alive
```

Log any SIGKILL-ed orphans for post-mortem (they ignored a graceful shutdown).

## Step 4 — Gate

```bash
ps aux | grep redislite/bin/redis-server | grep -v grep | wc -l
```

**Proceed to ship only if ≤ 5 path-based orphans remain** AND all no-path
orphans are cleaned (reaper). **> 5 → STOP, escalate to manual cleanup** —
do not declare done.

## Notes

- The reaper (`python -m tortoise.embedded_reaper --no-dry-run`, 5-min
  cron) handles all FUTURE no-path orphans automatically.
- Path-based servers with live clients are never killed — they're
  legitimate (Docker-mode, stable singleton, or in-use dev DBs).
- The `# noqa: redis-guard` + pre-commit hook prevents NEW relative-path
  scripts from creating more Category-3 orphans.
