---
title: "Scoping — Issue #1427 (orphaned path-based redislite servers)"
type: engineering
domain: capability
doc_status: live
created: 2026-08-18
subjects.team: epistemic-team
---

# Scoping — Issue #1427 (reaper protected-class leak)

## Confirmed Problem Definition

**The reaper classifies ANY redislite server with a registry `db_filename` (or
non-ephemeral registry `dir`) as `protected` (path-based) unconditionally —
`embedded_reaper.py` classification (module docstring; `_classify` Signal 1 +
Signal 2).** Test servers (TortoiseSDK / FalkorProjection with a temp or
user-home db file — the standard fixture pattern) register `dbfilename` →
`protected` → `NEVER_KILL`. When a test aborts / GC-skips the clean close
(issue #900/#1005 failure modes), the daemon leaks and the registry-recorded
owner pid dies; the reaper's dead-pid handling (phase1_probe) only runs for
`candidate` records, so protected servers are never even evaluated. Leaked
daemons accumulate (139–181 on the dev machine; 657+ leaked servers observed
in earlier CI audit, python-ci.yml:34-37); a stale daemon registry lock
repeatedly causes `EmbeddedStoreBusyError` flakes on `~/.tortoise/tortoise.db`.

## Reclassification Rule (implemented)

For any server that classifies `protected` because of a path-based signal
(Signal 1 — non-ephemeral registry `dir`; Signal 2 — named `db_filename` in a
non-ephemeral tree; old-format `.db`-present fallback), resolve the
registry-recorded owner pid (registry `pidfile` → pid file content):

- **owner pid provably dead** → **orphan** → reclassify as `candidate`
  (falls through to the cooldown check; a dead pid yields no uptime, so the
  boot cooldown does not re-protect it). The standard reap gates still apply
  (phase1 probe → liveness → CLIENT LIST = 0 clients), so nothing live is
  killed.
- **owner pid alive** → keep `protected` (live data must not be killed).
- **owner unresolvable** (no pidfile / pid file missing / unreadable /
  unparseable) → fail closed → keep `protected`.

Stable-singleton and boot-cooldown protections are untouched. The change is
**additive and complementary to #1383** (same liveness principle applied to the
`candidate` class; #1383's guarded rmtree removes the dead-pid leftovers that
#1427's reclassification now exposes via phase1 `stale_socket`).

## Indicators

1. Path-based server with dead registry pid → `candidate` (was `protected`).
2. Conftest hygiene sweep "skipping path-based/non-candidate server" logs stop
   being the majority once #1383 lands the removal half.
3. `EmbeddedStoreBusyError` stale-lock flakes disappear.

## Complexity

standard (reaper classification extension + hygiene verification; no
test-design epic). Domain ratings per issue: Architecture standard.

## Verification

`tests/test_reaper_orphan.py` (new — unit `_classify`, discover integration,
phase1 handoff) + `tests/test_reaper.py` + `tests/test_embedded_concurrency.py`.
Registered in `config/ci-surfaces.yml` (core + sdk surfaces).
