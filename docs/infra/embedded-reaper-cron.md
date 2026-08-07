# Embedded Reaper — Periodic Execution (cron / launchd)

The reaper is a **safety net**: it cleans orphaned redislite redis-server
processes that accumulate when parent processes are SIGKILL'd. Run it
periodically (every 5 minutes matches agent_cron's 2-minute spawn pattern,
so orphans are cleaned within 2-3 spawn cycles).

## Cron (Linux / macOS with cron)

```cron
*/5 * * * * /usr/bin/python3 -m tortoise.embedded_reaper --no-dry-run >> ~/.tortoise/reaper.log 2>&1
```

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
  </array>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>/Users/home/.tortoise/reaper.log</string>
  <key>StandardErrorPath</key><string>/Users/home/.tortoise/reaper.log</string>
</dict>
</plist>
```

Load: `launchctl load ~/Library/LaunchAgents/com.tortoise.embedded-reaper.plist`

**Post-install verification:** `plutil -lint ~/Library/LaunchAgents/com.tortoise.embedded-reaper.plist` → OK; `launchctl list | grep tortoise` shows the label.

## Safety

- Default is **dry-run** — only `--no-dry-run` actually kills.
- `--timeout` (default 120s, env `TORTOISE_REAPER_TIMEOUT`) bounds each sweep.
- Singleton lock (`~/.tortoise/.reaper.lock`) prevents cron/manual overlap.
- Only **no-path tempdir orphans** are killed; path-based servers (stable
  singleton, CWD leaks) are NEVER touched (that's Child 2's migration job).
