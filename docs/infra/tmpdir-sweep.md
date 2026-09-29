# Temp-Dir Sweep — tortoise-owned litter (issue #4069)

`$TMPDIR` on a working dev box churned to **37,765 top-level entries /
224,434 at depth 2 / 3.8 GB with nothing older than three days** — live
churn, zero cleanup. That is not just disk. The embedded-orphan census
(`tools/embedded_orphans.py`) and the reaper's stale-socket walk both `find`
this tree, so every entry multiplies a walk that already cost ~41% of a CPU
per call, once per census/reaper invocation, while many pytest processes run
in parallel.

The fix has two halves:

1. **Stop the normal-exit leak** — `tests/_tmpdir_hygiene.py` (re-exported by
   `tests/conftest.py`) tracks every `tempfile.mkdtemp` a test creates and
   removes it at teardown. Suite-wide, autouse, no per-call-site edit.
2. **Sweep the backlog** — `tools/tmpdir_sweep.py`, an operator-invoked,
   age-gated, prefix-matched sweep. This document covers it.

> Killed/watchdog-killed processes run no teardown at all; that residue stays
> the reaper's job (`docs/infra/embedded-reaper-cron.md`). The sweep is the
> backstop; the teardown fixture is the primary fix.

## Is it automatic?

**No — the sweep is operator-invoked.** It is deliberately not wired into
session start or the reaper cron: an automatic fleet-wide delete races every
concurrent suite, and how much to reclaim is a load decision the operator
owns. The *leak fix* is automatic (per-test teardown); the *sweep* is not.

## Usage

```bash
# Dry run (default) — reports candidates, deletes nothing, never walks a
# candidate subtree, ~1s CPU at 390k entries:
python3 tools/tmpdir_sweep.py

# Delete:
python3 tools/tmpdir_sweep.py --apply

# Conservative / aggressive age gate (hours):
python3 tools/tmpdir_sweep.py --older-than-hours 24
python3 tools/tmpdir_sweep.py --apply --older-than-hours 12

# Machine-readable, for a scheduled job's log:
python3 tools/tmpdir_sweep.py --apply --json
```

Exit codes: `0` ran cleanly (dry or apply), `2` refused / could not run / an
`--apply` removal failed.

## Safety by construction

| Guard | Behaviour |
|---|---|
| **Depth 1 only** | One `os.scandir` of the root. It never walks the tree, so it cannot become the load spike it exists to remove. |
| **Bounded root** | `--root` is realpath-resolved and refuses `/`, `$HOME` (raw **and** realpath spelling, so a symlinked `$HOME` cannot slip through), and any path containing `..`; every candidate is verified to resolve *inside* the root, and the check is repeated inside the removal call. |
| **Symlinks** | Never followed and never removed; a symlinked entry is reported `symlink`. Its target — inside or outside the root — is untouched. |
| **Prefix allowlist** | Only tortoise-owned creators match (`ask_`, `tortoise_`, `tortoise-`, `d3_session_`, `reaper_probe_`, `redislite_`, `lme-`, `battery_a4_`) plus the fixed-name `a_ours.py`. This is the *observed #4069 histogram* subset, deliberately not an exhaustive census of every committed `mkdtemp` prefix; widen with `--prefix` (an empty/whitespace prefix is rejected, since `startswith("")` matches every entry). Agent-tooling litter (`pi-commit-msg-*`, `pi-pr-body-*`, `wf-lock-*`, `admin-*`) is a **different owner** (cross-repo issue) and is not swept unless the operator passes `--prefix`. |
| **Age gate** | Default **12h** (`--older-than-hours`). A non-finite or negative value is refused (`age < nan` is always false, which would silently make every entry a candidate). A concurrent suite's per-test dirs are minutes old; its session-long dirs carry a live pid (below). |
| **Live-server guard** | A candidate whose `redis.pid` names a live process is protected; a pid file that is unreadable, unparseable, non-positive, out-of-range, or **present but not a regular file** (FIFO, dangling symlink, device, directory) fails closed (protected). The probe is `os.lstat`, so a path that cannot even be stat-ed protects too. A *dead* pid does not protect — that orphaned dir is exactly what the sweep reclaims. **Registry follow-up (#4479):** redislite keeps `redis.pid` in its own instance dir and records that path in `<dbfilename>.settings` *inside* the data dir, so a data dir with no pid file of its own is resolved through its registry and judged by the pid file the registry names — a live server's data dir is protected, a dead one's is still reclaimed. The teardown tracker's `_protected_reason` is the deliberate mirror of this guard; the two are pinned equal by `tests/test_tmpdir_sweep.py::test_the_two_guards_agree_on_every_shape`. |
| **Dry run by default** | `--apply` is required to delete. |
| **Idempotent** | A second `--apply` discovers zero candidates; removal tolerates an already-removed entry. |

`tt_` is **excluded** from the default allowlist: it is the #3752 per-session
temp root, removed wholesale by its own atexit teardown, and sweeping a live
suite's root would be the one genuinely destructive case.

## Scheduled use (optional)

If an operator wants the backlog cleared on a cadence, add a cron/launchd
entry — but keep it off the peak-load window and prefer the conservative
gate:

```cron
17 4 * * * cd /path/to/tortoise && /usr/bin/python3 tools/tmpdir_sweep.py --apply --older-than-hours 24 --json >> ~/.tortoise/tmpdir-sweep.log 2>&1
```

## Verifying

```bash
# Candidate count without deleting (must not change the top-level count):
python3 tools/tmpdir_sweep.py --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["candidates"])'

# A second --apply run is a no-op (idempotence):
python3 tools/tmpdir_sweep.py --apply; python3 tools/tmpdir_sweep.py --apply
```

Tests: `tests/test_tmpdir_sweep.py` (sweep behaviour, including the
symlink/escape/live-pid guards) and `tests/test_tmpdir_hygiene.py` (the
teardown tracker).
