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

This copy was old, **but not strictly behind**. It had received fixes after the handover that
agent-infra never got, and those were ported IN rather than discarded (agent-infra PR #2160):

- `lane.sh` — `Closes #N` required; a bare `Refs #N` refused for finished work (25 of 36 open
  P0-blockers already had merged PRs; nothing re-reads an issue whose PR merged).
- `merged-not-closed.sh` — the instrument that answers "which open issues already have a merged
  PR?", ported with a repo resolution that derives from the checkout instead of hardcoding this
  repo (agent-infra#2040's defect class).
- The `p0_merge_priority_override` rule plus its `.prio` map — a P0-blocker at the backlog head
  clears *only* the merge-priority gate. The ported version additionally refuses to clear any
  reason that is not `merge-priority*`, and the canonical suite drives that case.

The regression suite that guarded this copy (`tests/test_orchestrator_harness.py`) was retired with
it, after its coverage for the ported instrument moved into
`agent-infra/scripts/orchestrator-harness/merged-not-closed.test.sh` and the canonical
`selftest.sh`.

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
