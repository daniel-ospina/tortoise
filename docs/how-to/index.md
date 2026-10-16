---
title: "How-To Guides — Task-Oriented Documentation"
type: index
domain: operations
doc_status: live
created: 2026-10-16
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# How-To Guides (Task-Oriented)

> **Diátaxis Quadrant:** How-To Guides are goal- and task-oriented recipes that guide a practitioner through solving a specific operational, deployment, or authoring problem.

New how-to guides and operational procedures should be added to `docs/how-to/`.

## Infrastructure & Operations Runbooks

| Guide | Path | Summary |
|---|---|---|
| **Hosted Platform Infra Runbook** | [`docs/infra-runbook.md`](../infra-runbook.md) | Provisioning, secrets rotation, workstation credential posture, rollback, health checks, autostop policy, availability watchdog, and deploy gates. |
| **Registry Backup & Disaster Recovery** | [`docs/ops/registry-backup-dr.md`](../ops/registry-backup-dr.md) | Backup schedules, restoration drills, and disaster recovery procedures for the registry graph. |
| **Multi-Graph Migration Runbook** | [`docs/ops/multi-graph-migration-runbook.md`](../ops/multi-graph-migration-runbook.md) | No-forced-migration path and rollback drill for multi-graph tenancy. |
| **Post-Flip Verification Runbook** | [`docs/ops/669-post-flip-verification.md`](../ops/669-post-flip-verification.md) | Verification steps following production cutovers. |
| **Temp-Dir Hygiene & Sweep** | [`docs/infra/tmpdir-sweep.md`](../infra/tmpdir-sweep.md) | Per-test `mkdtemp` teardown and operator-invoked, age-gated backlog sweep. |

## Verification, Quality Gates & Authoring

| Guide | Path | Summary |
|---|---|---|
| **Finding Provenance Gate** | [`CONTRIBUTING.md`](../../CONTRIBUTING.md#a-finding-must-carry-the-tree-it-was-measured-against) | How to stamp and validate defect reports with `tools/finding_provenance.py`. |
| **Ship-Test Instrument** | [`docs/runbook/3806-ship-test-instrument.md`](../runbook/3806-ship-test-instrument.md) | Per-deploy clean-browser onboarding walk (`signup → wizard → connected`). |
| **Activation Scorecard Runbook** | [`docs/runbook/b7-activation-scorecard.md`](../runbook/b7-activation-scorecard.md) | Measuring session activation vs. signup and cohort roll-ups. |
| **Blog E2E Residue Cleanup** | [`docs/runbook/4220-blog-e2e-residue-cleanup.md`](../runbook/4220-blog-e2e-residue-cleanup.md) | Dry-run, guards, and verification for cleaning up E2E test rows in production. |
| **Demand Capture Ops Checks** | [`docs/runbook/3514-demand-capture-ops.md`](../runbook/3514-demand-capture-ops.md) | Live Telegram send verification and monthly demand review procedure. |
| **Ask Pre-Ship Gate Runbook** | [`docs/runbook/1987-ask-abstention-check.md`](../runbook/1987-ask-abstention-check.md) | Pre-ship abstention check and routing/cost verification for the ask lane. |
| **Writing a Blog Post Body** | [`docs/blog-authoring.md`](../blog-authoring.md) | Supported Markdown subset, `[[toc]]` / `[[video: …]]` markers, and linking rules. |
| **Retrieval Latency Benchmark Runbook** | [`benchmarks/README.md`](../../benchmarks/README.md) | Running and interpreting retrieval latency benchmarks. |
