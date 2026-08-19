---
title: "CI Process Effectiveness — is the gate stack counterproductive, and where can we go faster without losing quality?"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-19
subjects.team: epistemic-team
aboutSubjects: tortoise-ci
aboutObjects: ci-workflow, gates, test-sharding, redundant-runs, drift-prevention, test-isolation
---

# CI Process Effectiveness — Research Brief

> Trigger: maintainer question — "our CI process may have become counterproductive. Quality matters (dev customers), but repetitive gates that don't add value or a poorly configured setup that could run faster without quality loss should be improved."

## Reframed problem

The gates are earning their keep on the failures they catch (this week: a P0-class silent-merge hole, 4 real manifest drifts, a reaper-honesty bug, 6 silently-vacuous EP tests), but the setup has real fat: (a) one gate class (manifest drift) fires repeatedly for trivial reasons; (b) the same tests run 3x (PR + post-merge + nightly); (c) the suite's runtime is dominated by a test-infrastructure anti-pattern (per-test embedded DB servers). The question is marginal value per CI minute — and the deeper risk is the opposite failure: **gates that pass without testing anything** (vacuous tests), which is worse than slow gates.

## External findings (confidence tiers)

- **[HIGH] Duration-balanced sharding is the standard answer** — pytest-split with a committed `.test_durations` file; new/unknown tests fall back to average, so the file needs only ~biweekly refresh (jerry-git/pytest-split, simonwillison.net, blog.jerrycodes.com).
- **[HIGH] Redundant-run dedup is proven when the merge TREE is byte-identical to the tested PR tree** — dryotta's `check-redundant` job (fall-open on every error) and OpenTau's FF-skip pattern; the critical caveat: skipped runs must still report a passing check for branch protection.
- **[HIGH] A gate's value is trust + signal, not strictness** — Justin Abrahms' "deleted flaky tests, nothing broke" controlled experiment (80% of e2e skipped, defect rate unchanged — the tests "provided false confidence"); BugBrain ("once the override becomes routine, the gate is theater"); thehardparts ("gating risk or gating activity?"); GitPlumbers ("keep the PR pipeline under 10 minutes… quarantine flaky tests… track flake rate as debt").
- **[HIGH] DB-test isolation best practice = session-scoped shared server + per-test isolation** — MySQL Testcontainers case (50 containers → 1, 10 min → 1 min: "share the container, isolate the data"); Seedfast ("scope is the lever nobody adjusts").
- **[HIGH] CI speed is a measure → root-cause → remove discipline** — Shopify (p95 45→18 min; 68% overhead before tests; "the fastest code is the code that doesn't run"); Canva; Dropbox (affected-tests + status propagation).
- **[MEDIUM ⚠️ emerging] Merge queue (GitHub) batching** — throughput lever at scale.

## Corrected recommendations (ranked by value/effort, after ritual dissent)

1. **Split test-slow** — the serial ~38m job was the TRUE critical path of every main push (not the fast halves); a 2-leg duration split cut it ~40%. [SHIPPED #1471/#1481]
2. **Kill the dual manifest + auto-register** — derive the push matrix from ci-surfaces.yml (the hardcoded matrix.files were a second unchecked drift surface where a file silently never ran on push). [SHIPPED #1472/#1485]
3. **Duration-aware tier-2 PR split** — the parity split was duration-blind; LPT greedy pack with durations in the manifest. [SHIPPED #1473/#1487]
4. **Sound post-merge dedup** — comment/flag only when the push-to-main full run is same-tree green; fall-open otherwise (the naive PR-green skip is WRONG: PR CI is a tiered subset on a different install). [SHIPPED #1474/#1480]
5. **Finish the close-on-GC lifecycle root cause** — deterministic finalizer, cutting the leak surface and the reaper's job. [SHIPPED #1475/#1482]
6. **Extend the silent-skip guard** — already done on main by #1436 (the tier-2 path is covered). [CLOSED as done #1476]
7. **Persist step-level CI timing** — measurement artifact, never a gate. [SHIPPED #1477/#1483]

## Deliberately NOT recommended

Removing the watchdog/guaranteed-summary (it converts silent deaths into action), removing the integrity gate (drift is real), weakening the tiered gate, or raising timeouts to paper over budget (the override-routine trap).

## Withdrawn after dissent

- Extending the session-shared projection to the allowlisted files (backup/crash/chaos tests construct servers AS their test input; sharing breaks their semantics; the reaper is production code).
- A committed `.test_durations` file (a third drift-prone manifest) — durations live in the existing manifest instead.
- Post-merge skip on "PR green + tree equal" (would delete the full-suite + lean-install verification).

## Source Confidence Summary

| Claim | Tier | Sources |
|---|---|---|
| Duration sharding with periodic manifest | High | pytest-split + 3 practitioner write-ups |
| Tree-equality redundant-run skip | High | dryotta, OpenTau, costops |
| Gate trust/signal, false confidence | High | Justin Abrahms, BugBrain, thehardparts, GitPlumbers |
| Session-scoped DB + per-test isolation | High | MySQL Testcontainers, Seedfast, Magnus, MS EF Core |
| Measure→remove discipline | High | Shopify x2, Canva, Dropbox |
| Merge queue | Medium | GitHub blog (single-source) |
