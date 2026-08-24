# Embedded Reaper — Periodic Execution (cron / launchd)

The reaper is a **safety net**: it cleans orphaned redislite redis-server
processes that accumulate when parent processes are SIGKILL'd. Run it
periodically (every 5 minutes matches agent_cron's 2-minute spawn pattern,
so orphans are cleaned within 2-3 spawn cycles).

> **#1642 (2026-08-23):** the reaper was designed to be scheduled (Task 3 of
> #176) but the schedule was never installed — suites that are
> SIGKILLed/watchdog-killed never sweep, so 456 orphans + 32k tempdir
> entries accumulated on the dev box. **Install it now:**
>
> ```bash
> tools/install-reaper-schedule.sh        # macOS launchd / Linux cron
> tools/install-reaper-schedule.sh --status
> ```
>
> Installs `python -m tortoise.embedded_reaper --no-dry-run --only-safe`
> every 10 minutes. `--only-safe` is the concurrency-safe cron mode: it
> kills only orphan-CONFIRMED live servers (persisted 0-client CLIENT LIST
> state ≥ 10 min with no live suite markers — the #1642 FIX 3
> discriminator that #1557's blanket live-pid protection lacked) plus
> stale_socket leftovers, so a running test suite's servers are never
> disturbed. The singleton lock (~/.tortoise/.reaper.lock) makes concurrent
> runs safe.

## Cron (Linux / macOS with cron)

```cron
*/10 * * * * /usr/bin/python3 -m tortoise.embedded_reaper --no-dry-run --only-safe --timeout 300 >> ~/.tortoise/reaper.log 2>&1
```

`--only-safe` is REQUIRED for a scheduled sweep — a full sweep could kill a
concurrent suite's between-tests idle 0-client server (#1005 hazard), and the
120s default timeout aborts mid-cleanup on a loaded box (#1642). Prefer
`tools/install-reaper-schedule.sh`, which installs this exact line.

**Post-install verification:** `crontab -l | grep embedded_reaper` shows the
line; `grep -c reaper ~/.tortoise/reaper.log` grows each run.

## launchd (macOS)

Create `~/Library/LaunchAgents/com.tortoise.embedded-reaper.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tortoise.embedded-reaper</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>-m</string>
    <string>tortoise.embedded_reaper</string>
    <string>--no-dry-run</string>
    <string>--only-safe</string>
    <string>--timeout</string>
    <string>300</string>
  </array>
  <key>StartInterval</key><integer>600</integer>
  <key>StandardOutPath</key><string>/Users/home/.tortoise/reaper.log</string>
  <key>StandardErrorPath</key><string>/Users/home/.tortoise/reaper.log</string>
</dict>
</plist>
```

Load: `launchctl load ~/Library/LaunchAgents/com.tortoise.embedded-reaper.plist`

**Post-install verification:** `plutil -lint ~/Library/LaunchAgents/com.tortoise.embedded-reaper.plist` → OK; `launchctl list | grep tortoise` shows the label.

## Safety

- Default is **dry-run** — only `--no-dry-run` actually mutates.
- `--timeout` (default 120s, env `TORTOISE_REAPER_TIMEOUT`) bounds each sweep.
  The install script schedules with `--timeout 300` (a 10-min cadence has
  room for a 5-min sweep; the 120s default is too tight for a multi-hundred
  orphan backlog on a loaded box — observed abort mid-cleanup).
- Singleton lock (`~/.tortoise/.reaper.lock`) prevents cron/manual overlap.
- Only **no-path tempdir orphans** are killed; path-based servers (stable
  singleton, CWD leaks) are NEVER touched (that's Child 2's migration job).
- `--no-dry-run` also **rmtrees dead-pid leftover dirs** (`stale_socket`
  classification, issue #1383): age-gated (≥30s), pidfile re-verified dead
  (zombie-aware), socket re-probed (ECONNREFUSED only), atomic rename-aside
  + post-rename re-probe, and the renamed quarantine is rmtree'd last — a
  live server's data can never be deleted (worst case leaves a
  `*.reaper-stale-*` quarantine dir, which later sweeps converge). Runs in
  ALL modes including `--only-safe`; dry-run reports them without mutating.
  `--json` output carries `dbdir`, `removed_dir`, and `quarantine_dir` keys
  for stale actions.
