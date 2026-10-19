---
title: "Orchestrator harness — RETIRED: moved to agent-infra"
type: operations
domain: platform
created: 2026-10-12
updated: 2026-10-19
ownedBy: organisation-design-team
subjects.team: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: daniel-ospina/tortoise, agent-infra, tortoise#8246
doc_status: retired
---

# Orchestrator harness — retired

> **This directory no longer contains the harness.** It is a POINTER, kept so that anyone who
> remembers the path finds out where the code went instead of concluding it was lost.

## Where it went

**`agent-infra/scripts/orchestrator-harness/` is the single source of truth.**
<https://github.com/daniel-ospina/agent-infra/tree/main/scripts/orchestrator-harness>

## Why

This copy was the 2026-10-12 versioned handover of the ad-hoc `~/orchestrator/` harness, created
so the fleet could *evaluate* it. It was never the live copy. Over the following week the harness
continued to change in three places at once:

| file | agent-infra `main` | this copy | live `~/orchestrator` |
|---|---|---|---|
| `orch-loop.sh` | `e069af29` | `a1ffab3d` | `b4b89547` |
| `spawn-gate.sh` | `a7dba947` | `f7af7919` | `a7dba947` |
| `refresh-candidates.sh` | `a3ac5019` | `facc9a1d` | `a3ac5019` |
| `claims.sh` | `d1604686` | **absent** | `d1604686` |
| `nonblocking-dispatch.sh` | `5e3fe2b1` | **absent** | `5e3fe2b1` |
| `lane.sh` | `3dc22993` | `03400e8d` | `3dc22993` |
| `install.sh` | `3c0d14a8` | `de2ce83a` | `db02dfb3` |
| `selftest.sh` | `2318d1e0` | `04174f50` | `5675377c` |

(Blob hashes, 8 chars, measured 2026-10-19. The live copy was blob-identical to agent-infra
generation `2ddd5b5a1` on all eight — an unversioned copy one commit behind `main`, not a hand-fork.)

Three generations of one system: every fix had to be applied by hand in three places, each box ran
a different version, and "self-healing" was impossible because there was no authority to heal
toward. Retiring the copies is what makes one authority possible.

## What was NOT lost

This copy was old, **but not strictly behind** — it was behind the current agent-infra generation on
all six files it had, and **ahead on fixes in four of them** that agent-infra never got. All of them
have now been ported IN; the one that has NOT is listed last, with its file still present here on
purpose.

- `lane.sh` — `Closes #N` required; a bare `Refs #N` refused for finished work (25 of 36 open
  P0-blockers already had merged PRs; nothing re-reads an issue whose PR merged).
- `merged-not-closed.sh` — the instrument that answers "which open issues already have a merged
  PR?", ported with a repo resolution that derives from the checkout instead of hardcoding this
  repo (agent-infra#2040's defect class).
- The `p0_merge_priority_override` rule plus its `.prio` map — a P0-blocker at the backlog head
  clears *only* the merge-priority gate. The ported version additionally refuses to clear any
  reason that is not `merge-priority*`, and the canonical suite drives that case.
- `refresh-candidates.sh`'s `landed.txt` half and `RETRY_MAX` bound — a LANDED verdict goes to
  `landed.txt` (never `checked.txt`, which would retire an OPEN issue from the backlog permanently
  and invisibly), the set is named on every pass, the reaper settles CLOSED entries / releases
  OPEN ones / keeps unreadable ones, and a candidate that never answers is bounded instead of
  starving the pool behind it. ⛔ This one was found **late**: the first cut of this retirement
  deleted the file while these fixes were still unported, because the retirement's "everything was
  ported first" premise had never been checked against this file. The port now precedes the
  deletion, and `selftest.sh` drives the behaviour.
- `merge-one.sh` — `verdict_kind()` / `apply_verdict_marker()`: a verdict-LESS reviewer run (stall,
  crash, timeout) must not authorise a fixer. Ported, with driving tests in the canonical
  `selftest.sh` (measured cost of the bug: 7 of 7 cap-hits in one window were verdict-less and 4
  were then parked "needs a human", never reviewed).

**NOT ported, and therefore still present in this directory: `merge-one.sh`'s #8484 detached-reviewer
checkout.** The reviewer should run in its own checkout rather than the tree it reviews, and the
`AGENT_ALLOW_MAIN_EDITS` export should be withdrawn. That change is larger and is blocked on files
other lanes hold, so the file is kept rather than deleted: **deleting the only copy of an unported
fix is exactly the silent destruction this retirement exists to avoid.** `tools/check-harness-fork.sh`
allows `merge-one.sh` by name (the only allowed script) and says so in its output, so the exception is
visible instead of load-bearing on someone's memory. Port it, then delete it here and add it to that
guard's deny-set by removing it from `ALLOWED`.

The regression suite that guarded this copy (`tests/test_orchestrator_harness.py`) was retired with
it, after its coverage for the ported instrument moved into
`agent-infra/scripts/orchestrator-harness/merged-not-closed.test.sh` and the canonical
`selftest.sh`. That move was deliberate: the suite could not run without the copy it drove, and
retiring it before moving the coverage would have deleted the only tests the instrument ever had. The
canonical suite now also covers the `verdict_kind` port with driving tests.

## The rule this directory now enforces

**`harness-single-source.yml` fails CI if any harness script reappears here.** A pointer is the
intended state; a script is a fork. If you need to change the harness, change it in agent-infra —
a fix applied to a copy is not a fix.

To check a box by hand:

```bash
agent-infra/scripts/orchestrator-harness/check-single-source.sh \
  --fork <path-to-this-directory>
agent-infra/bin/harness doctor        # platform, supervisor, provenance, fork state
```

Tracked by tortoise#8246.
