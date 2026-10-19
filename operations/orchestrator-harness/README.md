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

| file | agent-infra | this copy | live `~/orchestrator` |
|---|---|---|---|
| `orch-loop.sh` | `d61f1587` | `41c50478` | `d61f1587` |
| `spawn-gate.sh` | `f730dc87` | `99fd20a9` | `f730dc87` |
| `refresh-candidates.sh` | `c12cadfc` | `dfedd12a` | `c12cadfc` |
| `claims.sh` | `95a45308` | **absent** | `95a45308` |
| `nonblocking-dispatch.sh` | `dc9d555a` | **absent** | `dc9d555a` |
| `lane.sh` | `7aaa24d1` | `88b1a34b` | `7aaa24d1` |
| `install.sh` | `9b9ba9f3` | `dccba135` | `9b9ba9f3` |
| `selftest.sh` | `47189536` | `3b14e8e1` | `47189536` |

Three generations of one system: every fix had to be applied by hand in three places, each box ran
a different version, and "self-healing" was impossible because there was no authority to heal
toward. Retiring the copies is what makes one authority possible.

## What was NOT lost

This copy was old, **but not strictly behind** — it was behind on five files and **ahead on
`lane.sh`**. It had received fixes after the handover that agent-infra never got. Those that have
been ported IN (agent-infra PR #2160) are listed first; the one that has NOT been ported is listed
last, with its file still present here on purpose.

- `lane.sh` — `Closes #N` required; a bare `Refs #N` refused for finished work (25 of 36 open
  P0-blockers already had merged PRs; nothing re-reads an issue whose PR merged).
- `merged-not-closed.sh` — the instrument that answers "which open issues already have a merged
  PR?", ported with a repo resolution that derives from the checkout instead of hardcoding this
  repo (agent-infra#2040's defect class).
- The `p0_merge_priority_override` rule plus its `.prio` map — a P0-blocker at the backlog head
  clears *only* the merge-priority gate. The ported version additionally refuses to clear any
  reason that is not `merge-priority*`, and the canonical suite drives that case.
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
