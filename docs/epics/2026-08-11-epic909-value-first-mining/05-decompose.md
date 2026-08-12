---
title: "Decompose — Epic #909: Value-first mining system (v1)"
type: decompose
domain: engineering
doc_status: draft
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
inputs: 04-plan.md (the approved plan), 2026-08-11 decompose run
---

# Decompose — Epic #909 child issues

> 19 issues, dependency-ordered, MECE-verified (mutually exclusive, collectively
> exhaustive, acyclic, gate-parallel set correct). Each issue created via the
> issue-creation skill with O/I/T, affiliation, domain-aware complexity, epic contract
> references, DE2E test alignment, and a verification checklist (the plan's DE2E suite
> is the test contract — no separate test-design issue exists for this epic).

## Dependency graph

```text
ROOT (gate-parallel with the owner-scheduled gate run #946):
  945 judge harness         947 quota fix + constants + backfill
  948 ontology amendments   949 pack manifest v3 (schema/validation/isolation/CI)

945 ──▶ 946 window #2 gate run (THE GATE)
949 ──▶ 950 pack content v3 ──▶ 954 ──▶ 955 extractor ──▶ 957 P9/A8 test
949 ──▶ 951 domain_loader ──▶ 955 ──▶ 958 SDK producer ──▶ 959 extension
947+948 ──▶ 952 commit_schema/:CommitRecord/budget ──▶ 953 commit endpoint ──▶ 963 privacy
947+948+949+950 ──▶ 954 value brief
946 ──▶ 954/955/956/957 (slice 6 gated on the gate — plan R-2)
954 ──▶ 956 enforcer ──▶ 958 (enforcement on the derived path)
956+947 ──▶ 960 metrics/thresholds ──▶ 961 gold set (owner-scheduled long pole)
960+953+957 ──▶ 962 CI eval + drift monitor (NO dep on 961 — skip-and-flag guard)
953+955 ──▶ 963 privacy
```

## Issue list (dependency-ordered)

| # | Issue | Slice | Complexity | Depends on | E2E |
| --- | --- | --- | --- | --- | --- |
| 945 | judge harness + kappa script | 1a | standard | — | DE2E-1 |
| 946 | window #2 gate run (THE GATE) | 1b | standard | 945 | DE2E-1 |
| 947 | quota fix + is_episodic + constants + backfill (P0) | 2 | standard | — | DE2E-7 |
| 948 | 13 ontology amendments | 3 | standard | — | DE2E-2/5/6 |
| 949 | pack manifest v3 schema + validation + isolation + CI | 4a | complex | — | DE2E-9 |
| 950 | pack content v3 (productDelivery chain + dev/marketing/pm) | 4b | standard | 949 | DE2E-9 |
| 951 | domain_loader unification + dead-call repair | 4c | standard | 949 | DE2E-9 |
| 952 | commit_schema + :CommitRecord + L1/L2 idempotency + budget | 5a | complex | 947, 948 | DE2E-7 |
| 953 | POST /v1/sessions/commit handler + metering + telemetry + rate bucket | 5b | standard | 952 | DE2E-2/5/6/7/10 |
| 954 | value_brief compiler | 6a | standard | 946, 949, 950, 948 | DE2E-9 |
| 955 | value-first extractor S0-S3/S5/S6 | 6b | complex | 946, 954, 951, 952, 956, 945 | DE2E-2/3/4/6/8/11 |
| 956 | extraction enforcer E1-E10 + guards | 6c | standard | 946, 954 | DE2E-9 |
| 957 | P9/A8 contested-variance test (live surface) | 6d | standard | 946, 955 | DE2E-6 |
| 958 | SDK commit_session + DerivedCommitPayload + hold_queue | 7a | standard | 953, 955, 956 | DE2E-2/5/7 |
| 959 | extension cloud path rework | 7b | standard | 958 | DE2E-2/10 |
| 960 | metrics.py + types + thresholds.yaml reconciliation | 8a | standard | 956, 947 | DE2E-1/3 |
| 961 | 30-window gold set (seeded, owner-adjudicated) | 8b | standard | 946, 960 | Layer-2 legs |
| 962 | CI eval workflow + drift monitor | 8c | standard | 960, 953, 957 | Layer-2 legs |
| 963 | privacy hardening (secret-scan, wording, telemetry schema) | 9 | standard | 953, 955 | DE2E-10 |

## MECE verification record

- **Mutual exclusivity:** 6 named overlap pairs checked clean (951/954, 952/953, 955/956,
  953/963, 960/961, 962/957); is_episodic partition resolved (947 = legacy backfill +
  regex-path flag; 953 = commit-path writes incl. Session node); telemetry three-way
  split stated (953 emit / 962 consume / 963 schema guard).
- **Collective exhaustiveness:** all 9 slices covered; R-15 smoke (#955), R-19 live-run
  artifact (#957), R-21 pre-flight (#947/#953), gold seeding (#961), backfill (#947),
  rate bucket (#953), load isolation + compile CI (#949), MAX constants (#947),
  session_indexer discoverability (#953), telemetry size cap (#962), DE2E-11 (#955/#953).
- **Dependency soundness:** acyclic (topologically sorted); edges match plan §8.3
  (incl. 4→6 = full slice 4 via 950→954, 2→8 via 947→960); slice 6 gated on the gate
  (#946) per plan R-2; 961→962 deliberately dropped (CI must not wait on the
  owner-scheduled gold set — skip-and-flag guard).
- **Parallelism:** 947/948/949 root and gate-parallel; 960 scaffoldable after the gate;
  946 is the only owner-scheduled blocker (R-2).

## Review gates

- Per-issue review: 5 fresh-context review batches (all 19 issues), ~15 findings fixed
  (dependency edges, test-alignment, verification-checklist, seam resolutions).
- MECE gate: 2 cycles — 4 findings (2 edges added, 1 edge dropped, is_episodic
  ownership) + 3 residuals (overlap scoping, Session-node flag, regex-path seam) —
  all fixed. **MECE CLEAN.**
