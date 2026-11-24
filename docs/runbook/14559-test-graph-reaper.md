---
title: "14559 — Scheduled empty-test-graph reaper: cadence, band, and what to do when it refuses"
type: operations
domain: operations
doc_status: live
created: 2026-11-24
ownedBy: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: tortoise
---

# Scheduled empty-test-graph reaper (#14559)

> **Read this when** `test-graph-reaper` reds, files
> `test-graph-reaper: a run needs adjudication (#14559)`, or someone asks why a
> test graph is still there after the sweep.

## What runs, and how often

`tools/reap_empty_test_graphs.py` — unchanged — is driven by
`.github/workflows/test-graph-reaper.yml` **every 15 minutes**, in three passes:

| pass | command | artifact |
|---|---|---|
| plan | `--json` (dry run) | the retained rehearsal (`reapable`, refusals, per-bucket counts) |
| execute | `--execute --json` | deletions, failures, `used_memory` before → after (bytes freed) |
| verify | `--json` again | `reapable` **must be 0** — that is the sweep's invariant |

All three are uploaded per run (artifact `test-graph-reaper-<run>-<attempt>`,
90-day retention) and the same numbers go into the run's job summary. A run that
needs a human files **one** deduped issue (the title is the dedupe key; repeats
arrive as `Recurrence #N`) and that issue **closes itself on the next clean run**
(#8841) — do not close it by hand to silence a recurrence.

**Cadence arithmetic.** The measured burst was 109 graphs / 90 min ≈ 1.2
graphs/min, so a 15-minute sweep bounds a burst at ~18 graphs ≈ 26 MB resident
(1.46 MB/graph, measured **by deleting** — a create-probe reads ~346 KiB and is a
false negative). Hourly would let a burst bank ~70 graphs ≈ 100 MB against a
1 GB instance that already carried 138.

**The band** (checked every run, `REAPER_TOTAL_BAND`, default 200):

- `reapable` after the run = **0**;
- resident total ≤ **200** graphs. Peaks observed during leaks are 138–150, and at
  1.46 MB/graph 200 graphs is ~292 MB — ~29 % of a 1 GB instance even if every one
  were empty. Above the band the sweep is not keeping up with the leak **rate**,
  which is a test-org question (#13218), not a cleaning one.

## ⛔ Do not widen the gates

The reaper refuses, deliberately, and the schedule passes **no** `--test-org`,
`--force`, or any other widener. In particular:

- `refused-unresolved` — no `organizations` row owns the graph name. An orphan is
  an **adjudication**, not a cleaning job. Do not "fix" it by widening the name
  markers; find out which org it belonged to. **The one exception is when it is ALL
  of them** (`unresolved == total`): that is the control plane faulting, not a
  cluster of orphans, and the run reds for it.
- `kept-not-test-org` — the owner carries no test/agent marker. A customer called
  `Agent Test` lands here **on purpose** (the marker is anchored on a
  `-`/`_` separator so a real name cannot match). If such an org genuinely must be
  reaped, name its **id** explicitly with `--test-org` in a hand-run — never in the
  schedule.
- `kept-not-empty` — the graph holds at least one node. That is a data question.
- `refused-unreadable` — emptiness could not be read. The sweep is blind, which the
  schedule treats as a **failure** (not as "assume empty").

## Reading a red run

1. Open the run's summary — it carries the verdict, the counts, the freed MB, and
   the refused graphs with their reasons.
2. Open the artifact for the same run; `reaper-plan.json` is the dry-run report,
   `reaper-execute.json` the outcome, `reaper-verify.json` the post-run residue.
3. Match the `::error::` annotation to the table below.

| Annotation | Meaning | Action |
|---|---|---|
| `missing credential(s)` | `FALKORDB_CLOUD_URI` / `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` unset or rotated | fix the repo secret. ⛔ Without the guard an unset URI sends the tool to an **embedded** database and reports "0 graphs, nothing to reap" as a clean sweep |
| `returned 0 graphs` | the target answered but holds nothing | wrong target or an unreadable store — check `target:` in the "Guard the target" step |
| `control plane ANSWERED but resolved none` | every graph is `refused-unresolved` | the control plane is faulting (empty/restored project, a key scoped elsewhere, namespace drift), not 138 coincidental orphans — the sweep would be a silent no-op |
| `plan pass did not produce a report` | exit 2 — control plane or graph list unreadable | the reaper REFUSES rather than sweeping a partial view; re-run, then investigate the control plane |
| `execute pass produced no report` / `exited N` | the reap was killed (124/137) or refused (2) before writing its document | nothing can be read about that pass — its deletion count would be a guess; re-run |
| `N deletion(s) FAILED` | `GRAPH.DELETE` did not take | read `outcome[]` in the execute artifact |
| `still reapable AFTER the execute pass` | deletions did not converge | usually an ACL user that survived its drop — the reaper keeps the graph **on purpose** rather than orphan a live credential |
| `refused-unreadable` | emptiness could not be read for N graphs | the sweep is partly blind; that is a defect in the sweep |
| `resident total … above the stated band` | the leak outpaces the sweep | #13218 territory: the test orgs themselves. Do not raise the band to silence it |

## Rehearsing by hand

`workflow_dispatch` with `execute: false` runs plan + verify only and creates
nothing — safe on any box that holds the prod secrets. It is **green by
construction** (it deletes nothing, so the residue invariant does not apply), and
the incident issue is **not closed** by a rehearsal: closing is a claim about
reality, so only a run that actually reaped may make it. For a local rehearsal, run
the tool directly against a disposable instance (dry run is the default):

```bash
uv run python tools/reap_empty_test_graphs.py            # report only
uv run python tools/reap_empty_test_graphs.py --json     # machine-readable
```

⚠️ `--execute` against production is what the schedule is for. A hand-run
`--execute` on a box you are unsure about is the one way to make this document's
band statement false.

## Residuals (stated, not implied)

- **A disabled schedule is silent.** GitHub disables schedules on a repo inactive
  for ~60 days; a disabled schedule makes no request, fails nothing and pages
  nobody. Nothing here watches for a *missing* run (same residual as
  `billing-reconcile-cron`). Closing it needs a run stamp plus an out-of-band
  staleness check.
- **`used_memory` delta is instance-wide.** It carries concurrent tenant writes in
  the reap window; there is no per-graph memory figure on FalkorDB, and deriving
  one would reproduce the create-probe's false negative.
- **The ACL leg is a no-op on the hosted instance.** `fly.toml` declares
  `TORTOISE_ACL_LAYER=off` (#8617 — no credential the app holds carries `+ACL` on
  FalkorDB Cloud), so no per-graph `tenant_<gid>` user exists to drop there. The
  workflow does not pin the variable, so the leg stays real on a self-hosted
  target.
- **Refused graph names appear in the artifact and the job summary.** Graph names
  are org-id derived, not credentials; withholding them would defeat the
  adjudication the refusal asks for.
