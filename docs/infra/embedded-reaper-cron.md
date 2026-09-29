---
title: "Embedded Reaper — Periodic Execution (cron / launchd)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-08-23
ownedBy: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-embedded-reaper
---

# Embedded Reaper — Periodic Execution (cron / launchd)

The reaper is a **safety net**: it cleans orphaned redislite redis-server
processes that accumulate when parent processes are SIGKILL'd. Run it
periodically (every 20 minutes).

> **Reclamation latency (measured trade, #4438 review):** the `#1642 FIX 3`
> 0-client window is observed ACROSS sweeps (`ZERO_CLIENT_CONFIRM_MINUTES =
> 10`), so a 20-min cadence means a 20–40 min minimum confirmation latency
> for an *uninstrumented* orphan (the `#3599` per-server owner signal and
> `#4487`'s constructor instrumentation confirm on the FIRST sweep, which is
> what makes this cadence acceptable). Do not raise the interval further
> without re-checking that trade.

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
> every 20 minutes. `--only-safe` is the concurrency-safe cron mode: it
> kills only orphan-CONFIRMED live servers (persisted 0-client CLIENT LIST
> state ≥ 10 min with no live suite markers — the #1642 FIX 3
> discriminator that #1557's blanket live-pid protection lacked) plus
> stale_socket leftovers, so a running test suite's servers are never
> disturbed. The singleton lock (`<tempdir>/.tortoise-reaper-<uid>/.reaper.lock`) makes concurrent
> runs safe.
>
> **Discovery is scoped (#4068).** Pass 2 enumerates depth-1 tempdir entries
> in the ephemeral namespace with an in-process `os.scandir` — no `find`
> subprocess, no depth-2 lstat storm — and logs a WARNING with a partial set
> if its budget expires (the sweep summary then reads `SCAN TRUNCATED`). The
> scoped set is exactly the set every **removal** path already requires, and
> live servers are enumerated name-independently by pass 1, so nothing
> removable or killable is skipped. `--full-scan` (env
> `TORTOISE_REAPER_FULL_SCAN=1`) restores the pre-#4068 **un-scoped**
> enumeration for operator forensics; it can reach nothing an earlier release
> could not, and the scheduled sweep stays scoped.
>
> **Provenance guard (#4136) — strict-only.** Every destruction path (the
> SIGTERM admission, the stale-dir rename/rmtree, the quarantine sweep, and
> the kill path's tempdir cleanup) acts only on a candidate directory owned
> by the **invoking effective uid**, re-checked at the point of action. This
> closes the T2/T3/T4 primitives of #4098's threat model on a shared
> world-writable tempdir: evidence authored by another local uid can no
> longer authorize a kill or an rmtree. The schedule above is documented as,
> and installed as, a **user** cron/launchd job — unaffected. A **root-run**
> sweep now reaps nothing (a non-root user's tempdir entries are not
> root-owned): there is deliberately **no override, config flag, or
> allowlist** to act on another uid's directory. Run the schedule as the
> user whose orphans you want reaped.

## Cron (Linux / macOS with cron)

```cron
*/20 * * * * cd /path/to/repo && /path/to/venv/bin/python -m tortoise.embedded_reaper --no-dry-run --only-safe --timeout 900 --jobs 16 >> ~/.tortoise/reaper.log 2>&1
```

`--only-safe` is REQUIRED for a scheduled sweep — a full sweep could kill a
concurrent suite's between-tests idle 0-client server (#1005 hazard), and the
120s default timeout aborts mid-cleanup on a loaded box (#1642). Prefer
`tools/install-reaper-schedule.sh`, which renders this line with the repo path
and interpreter resolved.

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
    <string>900</string>
    <string>--jobs</string>
    <string>16</string>
  </array>
  <key>WorkingDirectory</key><string>/path/to/repo</string>
  <key>StartInterval</key><integer>1200</integer>
  <key>StandardOutPath</key><string>/Users/home/.tortoise/reaper.log</string>
  <key>StandardErrorPath</key><string>/Users/home/.tortoise/reaper.log</string>
</dict>
</plist>
```

Load: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tortoise.embedded-reaper.plist`
(pre-existing installs must be reloaded, not just rewritten — `launchctl
bootout gui/$(id -u)/com.tortoise.embedded-reaper` first. That bootout +
bootstrap pair is what `tools/install-reaper-schedule.sh` does when the
rendered plist changes, which is why an upgrade off the old
`--timeout 300` schedule actually takes effect.)

**Post-install verification:** `plutil -lint ~/Library/LaunchAgents/com.tortoise.embedded-reaper.plist` → OK; `launchctl list | grep tortoise` shows the label.

## Safety

- Default is **dry-run** — only `--no-dry-run` actually mutates.
- `--timeout` (default 120s, env `TORTOISE_REAPER_TIMEOUT`) bounds each sweep.
  The install script schedules with `--timeout 900 --jobs 16`. The interval is
  derived from the budget (`REAPER_INTERVAL`, default `REAPER_TIMEOUT + 300`)
  so a fire is never refused mid-sweep; the script warns if a hand-set
  `REAPER_INTERVAL` is not greater than `REAPER_TIMEOUT`. The 120s default is
  too tight for a multi-hundred orphan backlog on a loaded box (observed abort
  mid-cleanup).
  `--jobs` sets the parallel per-candidate CLIENT LIST probe pool. The
  condition under which a backlog drains is tracked separately (`#4487` /
  `#4500`).
- Singleton lock (`<tempdir>/.tortoise-reaper-<uid>/.reaper.lock`) prevents cron/manual overlap.
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
