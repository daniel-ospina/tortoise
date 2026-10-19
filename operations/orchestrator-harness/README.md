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

## What was ported, what was NOT, and what is therefore still here

This copy was old, **but not strictly behind** — it was behind the current agent-infra generation on
all six files it had, and **ahead on fixes in four of them** that agent-infra had never received.

**Read the "state" column as the state of agent-infra `main`, not of a branch.** Verified
2026-10-19; re-verify with `gh api repos/daniel-ospina/agent-infra/git/trees/main?recursive=1 --jq
'.tree[].path' | grep -F <name>` before changing this table.

| fix this copy was ahead on | state on agent-infra `main` | where the code is now |
|---|---|---|
| `lane.sh` — `Closes #N` required, a bare `Refs #N` refused for finished work (25 of 36 open P0-blockers already had merged PRs; nothing re-reads an issue whose PR merged) | **PORTED** | agent-infra `scripts/orchestrator-harness/lane.sh` |
| `merge-one.sh` — `reviewer_prompt()` must not send the reviewer hunting for an interpreter (#8602: ~35% of reviewer runs ended at the 1800 s cap with **no verdict**, the last tool call a `find $HOME`) | **NOT PORTED** — agent-infra's `port-provenance.txt` records it as not-yet-ported | **retained here** (`merge-one.sh`), see below |
| `merge-one.sh` — `verdict_kind()` / `apply_verdict_marker()`: a verdict-**LESS** run (stall, crash, timeout) must not authorise a fixer (#8612: three such markers parked a PR "needs a human" with the findings never addressed) | **NOT PORTED** — same ledger row | **retained here** (`merge-one.sh`) |
| `merge-one.sh` — the #8484 detached-reviewer sandbox (the reviewer runs in its own checkout, and the `AGENT_ALLOW_MAIN_EDITS` export is withdrawn) | **NOT PORTED** — blocked on files other lanes hold | **retained here** (`merge-one.sh`) |
| `merged-not-closed.sh` — the instrument that answers "which open issues already have a merged PR?", with repo resolution derived from the checkout instead of hardcoding this repo (agent-infra#2040's defect class) | **NOT PORTED** — no path on `main` matches `merged-not-closed`; it exists only on **agent-infra#2160**, which is **OPEN** | **retained here** (`merged-not-closed.sh`) |
| `orch-loop.sh` — the `p0_merge_priority_override` rule plus its `.prio` map (a P0-blocker at the backlog head clears *only* the merge-priority gate) | **NOT PORTED** — the canonical `orch-loop.sh` has no `p0_merge_priority_override`; the ledger records it as not-yet-ported | deleted here; recovery source is tortoise git history, and the port lives on agent-infra#2160 |
| `refresh-candidates.sh` — the `landed.txt` half and the `RETRY_MAX` bound (a LANDED verdict goes to `landed.txt`, never `checked.txt`, which would retire an OPEN issue from the backlog permanently and invisibly) | **NOT PORTED** — the canonical copy says so in its own body (`port-provenance.txt`: *NOT DONE*) | deleted here; recovery source is tortoise git history, and the port lives on agent-infra#2160 |

⛔ **The ordering matters, and the ledger states it in its own words:** *"The removal PR must either
wait for it to merge or port it."* Agent-infra **#2160** is still **OPEN**, so the two artifacts that
have **no other copy anywhere** are kept here rather than deleted:

* **`merged-not-closed.sh`** — nothing else in either repo's `main` holds it, and
  `tests/test_orchestrator_harness_retained.py` is its only test suite.
* **`merge-one.sh`** — the only copy of the three `merge-one.sh` fixes above.

Both are `ALLOWED` by name in `tools/check-harness-fork.sh`, so the exception is visible in the
instrument rather than load-bearing on someone's memory. **Port them, then delete them here and
remove them from that `ALLOWED` list.**

`merge-one.sh` is retained as **text, not as a runnable tool**: its exec bit is cleared, it says so
in its own header, and the `spawn-gate.sh` it sources was retired with the rest of the copy — so
running it exits 78 ("refusing to run an ungated reviewer") before doing any work. That is
fail-closed and deliberate. The contracts that matter (no provisioning/execution instruction in the
reviewer prompt; no authorising marker for a verdict-less run) are pinned by
`tests/test_orchestrator_harness_retained.py`, which drives the *shipped* function text.

The other two fixes above (`orch-loop.sh`'s P0 override, `refresh-candidates.sh`'s `landed.txt`
half) are NOT retained as files — keeping them would have kept most of the fork. Their only copy is
tortoise git history (recoverable at the commit that retired this directory) and agent-infra#2160,
and the canonical ledger names them as not-yet-ported. **If #2160 is closed or rewritten, those two
halves must be re-derived from tortoise history before this directory's history is ever squashed.**
Tracked by tortoise#8246.

## The rule this directory now enforces

**`tools/check-harness-fork.sh` fails CI if any harness script reappears here.** A pointer is the
intended state; a script is a fork. If you need to change the harness, change it in agent-infra —
a fix applied to a copy is not a fix.

The check runs as a step of the `manifest-integrity` job in `.github/workflows/python-ci.yml`, whose
red is part of the **required** `python-ci-gate`. It used to have a workflow of its own
(`harness-single-source.yml`); that was a fail-open, because a path-scoped workflow is not a required
context and a red on it blocked nothing.

To check by hand, from the repo root:

```bash
bash tools/check-harness-fork.sh operations/orchestrator-harness
# 0 clean · 1 a harness script reappeared · 2 UNKNOWN (cannot check — never a pass)
```

(The earlier revision of this file pointed at `agent-infra/scripts/orchestrator-harness/
check-single-source.sh --fork` and `agent-infra/bin/harness doctor`. Neither exists on agent-infra
`main` — they are on #2160 — so the pointer pointed at nothing. Use the in-repo script above until
that PR lands.)

The `operations/orchestrator-harness/` path is registered in `tools/ci_selection.py` (`CORE_ALSO`),
so a change to the guard or to a retained script runs the two hermetic suites that hold them.

Tracked by tortoise#8246.
