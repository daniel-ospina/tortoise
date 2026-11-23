---
title: "CI Timing Measurement Artifact"
type: engineering
domain: capability
doc_status: live
created: 2026-08-18
subjects.team: epistemic-team
---

# CI Timing Measurement Artifact

> Measurement-only artifact (#1477). Refreshed weekly by the `ci-timing.yml`
> workflow, sampling one completed push-to-main Python CI run — the run is named in the `Sampled run` list below, not assumed to be the newest. Never a gate.

## Sampled run

- run_id: `45071394329`
- head_sha: `588cf7aeb61f94cc0b5871eee6b2ed8cf2275f1a`
- created_at: `2026-11-22T21:18:50Z`
- conclusion: `failure`
- sample_time: `2026-11-23T05:52:50Z`
- selection: `event=push&branch=main`, `exclude_pull_requests=true`, cancelled skipped; this sample IS run `45071394329` (created `2026-11-22T21:18:50Z`), and it is not re-labelled as a newer run
- schema_version: `1`

## Step timings (Jobs API — real run)

Second-granularity timestamps: sub-10s steps read 0s — do not alarm on those.

| Job | Step | Duration (s) |
|---|---|---|
| surface-guard | Set up job | 1.0 |
| surface-guard | Run actions/checkout@v4 | 32.0 |
| surface-guard | Run actions/setup-python@v5 | 0.0 |
| surface-guard | Install | 21.0 |
| surface-guard | The baseline's recorded cut commit resolves | 24.0 |
| surface-guard | The archive's restore anchors resolve and hold their frozen bodies | 109.0 |
| surface-guard | The privatisation sweep's check reports no typed reference left | 59.0 |
| surface-guard | Pin the gate's tooling to the base ref | 0.0 |
| surface-guard | The surface cannot expand without an explicit human decision | 2.0 |
| surface-guard | The ordering lint (AC13), and the committed surface may only shrink with a record | 29.0 |
| surface-guard | The list is generated from the manifest and cannot drift from it | 1.0 |
| surface-guard | Surface target compliance (informational, | 0.0 |
| surface-guard | Post Run actions/setup-python@v5 | 1.0 |
| surface-guard | Post Run actions/checkout@v4 | 0.0 |
| surface-guard | Complete job | 0.0 |
| manifest-integrity | Set up job | 1.0 |
| manifest-integrity | Run actions/checkout@v4 | 3.0 |
| manifest-integrity | Run actions/setup-python@v5 | 0.0 |
| manifest-integrity | Install pyyaml | 1.0 |
| manifest-integrity | Manifest integrity (drift gate, #1262) | 2.0 |
| manifest-integrity | Deployment config STATES both switches (#12926 — why: tools/embed_switch_gate.py) | 1.0 |
| manifest-integrity | Mergify protection invariants (config guard, #5215) | 4.0 |
| manifest-integrity | Registry integrity (union merge fails closed, #5373) | 0.0 |
| manifest-integrity | Required-set sync (#6144) | 2.0 |
| manifest-integrity | Harness single source — operations/orchestrator-harness/ is a POINTER (#8246) | 0.0 |
| manifest-integrity | Post Run actions/setup-python@v5 | 0.0 |
| manifest-integrity | Post Run actions/checkout@v4 | 0.0 |
| manifest-integrity | Complete job | 0.0 |
| workflow-lint | Set up job | 2.0 |
| workflow-lint | Run actions/checkout@v4 | 15.0 |
| workflow-lint | actionlint (workflow syntax + Actions-expression errors) | 5.0 |
| workflow-lint | Post Run actions/checkout@v4 | 0.0 |
| workflow-lint | Complete job | 0.0 |
| changes | Set up job | 1.0 |
| changes | Run actions/checkout@v4 | 36.0 |
| changes | Run actions/setup-python@v5 | 1.0 |
| changes | Install pyyaml | 1.0 |
| changes | Tiered selection | 1.0 |
| changes | Emit push matrix (#1472/#6135) | 1.0 |
| changes | Non-gating lanes — what this gate does NOT run (#10194) | 1.0 |
| changes | Tier-2 duration split (#1473) | 0.0 |
| changes | Capstone pre-registration gate — the ordering half (#9075) | 6.0 |
| changes | Secrets scan (evidence artifacts, | 0.0 |
| changes | Post Run actions/setup-python@v5 | 0.0 |
| changes | Post Run actions/checkout@v4 | 0.0 |
| changes | Complete job | 0.0 |
| packs-compile | Set up job | 0.0 |
| packs-compile | Run actions/checkout@v4 | 13.0 |
| packs-compile | Run actions/setup-python@v5 | 1.0 |
| packs-compile | Install package | 45.0 |
| packs-compile | Whole-registry compile (all packs, zero validation errors) | 0.0 |
| packs-compile | Post Run actions/setup-python@v5 | 1.0 |
| packs-compile | Post Run actions/checkout@v4 | 0.0 |
| packs-compile | Complete job | 0.0 |
| test-carve-out (b) | Set up job | 0.0 |
| test-carve-out (b) | Resolve per-job pytest artifact paths (#10280) | 0.0 |
| test-carve-out (b) | Run actions/checkout@v4 | 15.0 |
| test-carve-out (b) | Run actions/setup-python@v5 | 0.0 |
| test-carve-out (b) | Restore pip dependency caches (#7612, RESTORE-ONLY — | 4.0 |
| test-carve-out (b) | Run ./.github/actions/install-cpu-torch | 18.0 |
| test-carve-out (b) | Install package + test extras | 27.0 |
| test-carve-out (b) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test-carve-out (b) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 2.0 |
| test-carve-out (b) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 3.0 |
| test-carve-out (b) | Generate coverage manifest (carve-out — expected nodeids, epic | 9.0 |
| test-carve-out (b) | Run carve-out suite (embedded, URI unset — E2E-4) | 499.0 |
| test-carve-out (b) | Temp-dir guard tests (TMPDIR unset — the GitHub-runner spelling condition, #3776) | 4.0 |
| test-carve-out (b) | Skip-fail guard (carve-out, epic | 0.0 |
| test-carve-out (b) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-carve-out (b) | Assert no redislite orphans (issue | 0.0 |
| test-carve-out (b) | Upload carve-out log (measurement artifact, | 1.0 |
| test-carve-out (b) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-carve-out (b) | Post Run actions/setup-python@v5 | 1.0 |
| test-carve-out (b) | Post Run actions/checkout@v4 | 0.0 |
| test-carve-out (b) | Complete job | 0.0 |
| test-track-b | Set up job | 1.0 |
| test-track-b | Resolve per-job pytest artifact paths (#10280) | 0.0 |
| test-track-b | Run actions/checkout@v4 | 3.0 |
| test-track-b | Run actions/setup-python@v5 | 9.0 |
| test-track-b | Install | 27.0 |
| test-track-b | Provision FalkorDB services on ephemeral host ports (#6673) | 14.0 |
| test-track-b | Generate coverage manifest (track-b, epic | 6.0 |
| test-track-b | Run Track B tests (explicit marker) | 7.0 |
| test-track-b | Skip-fail guard (track-b, epic | 0.0 |
| test-track-b | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-track-b | Upload track-b log (measurement artifact, | 1.0 |
| test-track-b | Stop FalkorDB services (#6673) | 0.0 |
| test-track-b | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test-track-b | Post Run actions/setup-python@v5 | 0.0 |
| test-track-b | Post Run actions/checkout@v4 | 0.0 |
| test-track-b | Complete job | 0.0 |
| test-record-postgres | Set up job | 1.0 |
| test-record-postgres | Run actions/checkout@v4 | 12.0 |
| test-record-postgres | Run actions/setup-python@v5 | 0.0 |
| test-record-postgres | Restore pip dependency caches (#7612, RESTORE-ONLY — | 3.0 |
| test-record-postgres | Install package + the postgres extra | 29.0 |
| test-record-postgres | Pin the PostgreSQL CLIENT to the servers' major (#12051) | 7.0 |
| test-record-postgres | Provision a throwaway Postgres on an ephemeral host port (#9551) | 11.0 |
| test-record-postgres | Provision the DRILL's scratch Postgres — a SECOND server (#9567 D8) | 1.0 |
| test-record-postgres | Run the record's Postgres legs (armed — | 184.0 |
| test-record-postgres | The DB legs RAN, not skipped (#9551) | 0.0 |
| test-record-postgres | Upload record-postgres log (measurement artifact) | 1.0 |
| test-record-postgres | Provision FalkorDB for the cache-graph end-to-end legs (#13337) | 6.0 |
| test-record-postgres | Run the cache-graph declaration's end-to-end legs (#13337 — both stores) | 6.0 |
| test-record-postgres | Stop FalkorDB for the cache-graph end-to-end legs (#13337) | 0.0 |
| test-record-postgres | Upload the cache-graph end-to-end log (#13337) | 1.0 |
| test-record-postgres | Stop Postgres (#9551) | 0.0 |
| test-record-postgres | Stop the drill's scratch Postgres (#9567 D8) | 0.0 |
| test-record-postgres | Post Provision FalkorDB for the cache-graph end-to-end legs (#13337) | 1.0 |
| test-record-postgres | Post Run actions/setup-python@v5 | 0.0 |
| test-record-postgres | Post Run actions/checkout@v4 | 0.0 |
| test-record-postgres | Complete job | 0.0 |
| test-d14-hosted-api | Set up job | 0.0 |
| test-d14-hosted-api | Run actions/checkout@v4 | 13.0 |
| test-d14-hosted-api | Run actions/setup-python@v5 | 0.0 |
| test-d14-hosted-api | Cache pip dependency caches (#7612) | 5.0 |
| test-d14-hosted-api | Run ./.github/actions/install-cpu-torch | 25.0 |
| test-d14-hosted-api | Install package + test extras | 39.0 |
| test-d14-hosted-api | Run the embedded_only marker selection (URI unset — | 366.0 |
| test-d14-hosted-api | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-d14-hosted-api | Upload D14 log (measurement artifact, | 2.0 |
| test-d14-hosted-api | Post Cache pip dependency caches (#7612) | 0.0 |
| test-d14-hosted-api | Post Run actions/setup-python@v5 | 0.0 |
| test-d14-hosted-api | Post Run actions/checkout@v4 | 0.0 |
| test-d14-hosted-api | Complete job | 0.0 |
| test-slow (d) | Set up job | 2.0 |
| test-slow (d) | Resolve per-job pytest artifact paths (#10028) | 1.0 |
| test-slow (d) | Run actions/checkout@v4 | 13.0 |
| test-slow (d) | Run actions/setup-python@v5 | 0.0 |
| test-slow (d) | Cache pip dependency caches (#7612) | 3.0 |
| test-slow (d) | Run ./.github/actions/install-cpu-torch | 22.0 |
| test-slow (d) | Install package + test extras | 29.0 |
| test-slow (d) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test-slow (d) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test-slow (d) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test-slow (d) | Drift guard — the expanded legs cover the gating slow pool (#1471/#9539/#10194) | 0.0 |
| test-slow (d) | Provision FalkorDB services on ephemeral host ports (#6673) | 11.0 |
| test-slow (d) | Compute docker URI (epic | 0.0 |
| test-slow (d) | Generate coverage manifest (slow legs — expected nodeids, epic | 9.0 |
| test-slow (d) | Run slow test suite — leg d (known-slow files, | 375.0 |
| test-slow (d) | Skip-fail guard (slow legs, epic | 0.0 |
| test-slow (d) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-slow (d) | Assert no redislite orphans (issue | 0.0 |
| test-slow (d) | Upload pytest log (measurement artifact, | 1.0 |
| test-slow (d) | Stop FalkorDB services (#6673) | 0.0 |
| test-slow (d) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test-slow (d) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-slow (d) | Post Cache pip dependency caches (#7612) | 0.0 |
| test-slow (d) | Post Run actions/setup-python@v5 | 0.0 |
| test-slow (d) | Post Run actions/checkout@v4 | 1.0 |
| test-slow (d) | Complete job | 0.0 |
| test-slow (e) | Set up job | 3.0 |
| test-slow (e) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test-slow (e) | Run actions/checkout@v4 | 15.0 |
| test-slow (e) | Run actions/setup-python@v5 | 0.0 |
| test-slow (e) | Cache pip dependency caches (#7612) | 7.0 |
| test-slow (e) | Run ./.github/actions/install-cpu-torch | 22.0 |
| test-slow (e) | Install package + test extras | 27.0 |
| test-slow (e) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test-slow (e) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 3.0 |
| test-slow (e) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test-slow (e) | Drift guard — the expanded legs cover the gating slow pool (#1471/#9539/#10194) | 0.0 |
| test-slow (e) | Provision FalkorDB services on ephemeral host ports (#6673) | 12.0 |
| test-slow (e) | Compute docker URI (epic | 0.0 |
| test-slow (e) | Generate coverage manifest (slow legs — expected nodeids, epic | 10.0 |
| test-slow (e) | Run slow test suite — leg e (known-slow files, | 325.0 |
| test-slow (e) | Skip-fail guard (slow legs, epic | 0.0 |
| test-slow (e) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-slow (e) | Assert no redislite orphans (issue | 0.0 |
| test-slow (e) | Upload pytest log (measurement artifact, | 1.0 |
| test-slow (e) | Stop FalkorDB services (#6673) | 1.0 |
| test-slow (e) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test-slow (e) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-slow (e) | Post Cache pip dependency caches (#7612) | 0.0 |
| test-slow (e) | Post Run actions/setup-python@v5 | 0.0 |
| test-slow (e) | Post Run actions/checkout@v4 | 0.0 |
| test-slow (e) | Complete job | 0.0 |
| test-concurrency-falkor | Set up job | 1.0 |
| test-concurrency-falkor | Resolve per-job pytest artifact paths (#10280) | 0.0 |
| test-concurrency-falkor | Run actions/checkout@v4 | 4.0 |
| test-concurrency-falkor | Run actions/setup-python@v5 | 0.0 |
| test-concurrency-falkor | Restore pip dependency caches (#7612, RESTORE-ONLY — | 3.0 |
| test-concurrency-falkor | Run ./.github/actions/install-cpu-torch | 25.0 |
| test-concurrency-falkor | Install package + test extras | 43.0 |
| test-concurrency-falkor | Reap ABANDONED redislite litter (#1005 / | 1.0 |
| test-concurrency-falkor | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test-concurrency-falkor | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 5.0 |
| test-concurrency-falkor | Provision FalkorDB services on ephemeral host ports (#6673) | 7.0 |
| test-concurrency-falkor | Run live concurrency tests (real FalkorDB sidecar) | 28.0 |
| test-concurrency-falkor | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-concurrency-falkor | Upload pytest log (measurement artifact, | 1.0 |
| test-concurrency-falkor | Validate docker-compose.yml (ports + header edits) | 0.0 |
| test-concurrency-falkor | Docs-consistency guard (embedded must never be "default" again) | 0.0 |
| test-concurrency-falkor | Stop FalkorDB services (#6673) | 0.0 |
| test-concurrency-falkor | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test-concurrency-falkor | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-concurrency-falkor | Post Run actions/setup-python@v5 | 0.0 |
| test-concurrency-falkor | Post Run actions/checkout@v4 | 0.0 |
| test-concurrency-falkor | Complete job | 0.0 |
| test-carve-out | Set up job | 1.0 |
| test-carve-out | Resolve per-job pytest artifact paths (#10280) | 0.0 |
| test-carve-out | Run actions/checkout@v4 | 3.0 |
| test-carve-out | Run actions/setup-python@v5 | 0.0 |
| test-carve-out | Restore pip dependency caches (#7612, RESTORE-ONLY — | 4.0 |
| test-carve-out | Run ./.github/actions/install-cpu-torch | 16.0 |
| test-carve-out | Install package + test extras | 30.0 |
| test-carve-out | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test-carve-out | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test-carve-out | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test-carve-out | Generate coverage manifest (carve-out — expected nodeids, epic | 13.0 |
| test-carve-out | Run carve-out suite (embedded, URI unset — E2E-4) | 434.0 |
| test-carve-out | Temp-dir guard tests (TMPDIR unset — the GitHub-runner spelling condition, #3776) | 4.0 |
| test-carve-out | Skip-fail guard (carve-out, epic | 0.0 |
| test-carve-out | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-carve-out | Assert no redislite orphans (issue | 0.0 |
| test-carve-out | Upload carve-out log (measurement artifact, | 1.0 |
| test-carve-out | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-carve-out | Post Run actions/setup-python@v5 | 0.0 |
| test-carve-out | Post Run actions/checkout@v4 | 1.0 |
| test-carve-out | Complete job | 0.0 |
| test-carve-out (c) | Set up job | 1.0 |
| test-carve-out (c) | Resolve per-job pytest artifact paths (#10280) | 0.0 |
| test-carve-out (c) | Run actions/checkout@v4 | 12.0 |
| test-carve-out (c) | Run actions/setup-python@v5 | 0.0 |
| test-carve-out (c) | Restore pip dependency caches (#7612, RESTORE-ONLY — | 4.0 |
| test-carve-out (c) | Run ./.github/actions/install-cpu-torch | 25.0 |
| test-carve-out (c) | Install package + test extras | 38.0 |
| test-carve-out (c) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test-carve-out (c) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 2.0 |
| test-carve-out (c) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 5.0 |
| test-carve-out (c) | Generate coverage manifest (carve-out — expected nodeids, epic | 16.0 |
| test-carve-out (c) | Run carve-out suite (embedded, URI unset — E2E-4) | 465.0 |
| test-carve-out (c) | Temp-dir guard tests (TMPDIR unset — the GitHub-runner spelling condition, #3776) | 7.0 |
| test-carve-out (c) | Skip-fail guard (carve-out, epic | 0.0 |
| test-carve-out (c) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-carve-out (c) | Assert no redislite orphans (issue | 0.0 |
| test-carve-out (c) | Upload carve-out log (measurement artifact, | 1.0 |
| test-carve-out (c) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-carve-out (c) | Post Run actions/setup-python@v5 | 0.0 |
| test-carve-out (c) | Post Run actions/checkout@v4 | 1.0 |
| test-carve-out (c) | Complete job | 0.0 |
| test-slow (b) | Set up job | 3.0 |
| test-slow (b) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test-slow (b) | Run actions/checkout@v4 | 14.0 |
| test-slow (b) | Run actions/setup-python@v5 | 0.0 |
| test-slow (b) | Cache pip dependency caches (#7612) | 3.0 |
| test-slow (b) | Run ./.github/actions/install-cpu-torch | 22.0 |
| test-slow (b) | Install package + test extras | 30.0 |
| test-slow (b) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test-slow (b) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test-slow (b) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test-slow (b) | Drift guard — the expanded legs cover the gating slow pool (#1471/#9539/#10194) | 0.0 |
| test-slow (b) | Provision FalkorDB services on ephemeral host ports (#6673) | 11.0 |
| test-slow (b) | Compute docker URI (epic | 0.0 |
| test-slow (b) | Generate coverage manifest (slow legs — expected nodeids, epic | 10.0 |
| test-slow (b) | Run slow test suite — leg b (known-slow files, | 345.0 |
| test-slow (b) | Skip-fail guard (slow legs, epic | 0.0 |
| test-slow (b) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-slow (b) | Assert no redislite orphans (issue | 0.0 |
| test-slow (b) | Upload pytest log (measurement artifact, | 1.0 |
| test-slow (b) | Stop FalkorDB services (#6673) | 0.0 |
| test-slow (b) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test-slow (b) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-slow (b) | Post Cache pip dependency caches (#7612) | 1.0 |
| test-slow (b) | Post Run actions/setup-python@v5 | 0.0 |
| test-slow (b) | Post Run actions/checkout@v4 | 0.0 |
| test-slow (b) | Complete job | 0.0 |
| test (f) | Set up job | 12.0 |
| test (f) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test (f) | Run actions/checkout@v4 | 1.0 |
| test (f) | Run actions/setup-python@v5 | 1.0 |
| test (f) | Run actions/setup-node@v4 | 0.0 |
| test (f) | Restore pip dependency cache (#7612, read-only — | 242.0 |
| test (f) | Restore uv dependency cache (#7612, read-only — | 138.0 |
| test (f) | Run ./.github/actions/install-cpu-torch | 2.0 |
| test (f) | Install package + test extras | 9.0 |
| test (f) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test (f) | Restore HF embedding model (BAAI/bge-small-en-v1.5) (#11063) | 22.0 |
| test (f) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 6.0 |
| test (f) | Provision FalkorDB services on ephemeral host ports (#6673) | 10.0 |
| test (f) | Compute docker URI (epic | 0.0 |
| test (f) | Generate coverage manifest (expected nodeids, epic | 72.0 |
| test (f) | Canary producer (epic | 0.0 |
| test (f) | Restore pytest last-failed state (#6142) | 0.0 |
| test (f) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (f) | Run fast test suite (slow files run in the test-slow job) | 1150.0 |
| test (f) | Save pytest last-failed state (#6142) | 0.0 |
| test (f) | Upload pytest log (measurement artifact, | 3.0 |
| test (f) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (f) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (f) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (f) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (f) | Assert no redislite orphans (issue | 0.0 |
| test (f) | Stop FalkorDB services (#6673) | 2.0 |
| test (f) | Prune the uv cache before saving it (#11063) | 10.0 |
| test (f) | Save uv dependency cache (#11063, BOUNDED) | 0.0 |
| test (f) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test (f) | Post Run actions/setup-node@v4 | 0.0 |
| test (f) | Post Run actions/setup-python@v5 | 0.0 |
| test (f) | Post Run actions/checkout@v4 | 0.0 |
| test (f) | Complete job | 0.0 |
| test (i) | Set up job | 1.0 |
| test (i) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test (i) | Run actions/checkout@v4 | 12.0 |
| test (i) | Run actions/setup-python@v5 | 0.0 |
| test (i) | Run actions/setup-node@v4 | 4.0 |
| test (i) | Restore pip dependency cache (#7612, read-only — | 4.0 |
| test (i) | Restore uv dependency cache (#7612, read-only — | 3.0 |
| test (i) | Run ./.github/actions/install-cpu-torch | 19.0 |
| test (i) | Install package + test extras | 29.0 |
| test (i) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test (i) | Restore HF embedding model (BAAI/bge-small-en-v1.5) (#11063) | 2.0 |
| test (i) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 3.0 |
| test (i) | Provision FalkorDB services on ephemeral host ports (#6673) | 12.0 |
| test (i) | Compute docker URI (epic | 0.0 |
| test (i) | Generate coverage manifest (expected nodeids, epic | 37.0 |
| test (i) | Canary producer (epic | 0.0 |
| test (i) | Restore pytest last-failed state (#6142) | 0.0 |
| test (i) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (i) | Run fast test suite (slow files run in the test-slow job) | 674.0 |
| test (i) | Save pytest last-failed state (#6142) | 0.0 |
| test (i) | Upload pytest log (measurement artifact, | 1.0 |
| test (i) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (i) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (i) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (i) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (i) | Assert no redislite orphans (issue | 0.0 |
| test (i) | Stop FalkorDB services (#6673) | 1.0 |
| test (i) | Prune the uv cache before saving it (#11063) | 0.0 |
| test (i) | Save uv dependency cache (#11063, BOUNDED) | 0.0 |
| test (i) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (i) | Post Run actions/setup-node@v4 | 0.0 |
| test (i) | Post Run actions/setup-python@v5 | 0.0 |
| test (i) | Post Run actions/checkout@v4 | 0.0 |
| test (i) | Complete job | 0.0 |
| test (g) | Set up job | 1.0 |
| test (g) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test (g) | Run actions/checkout@v4 | 12.0 |
| test (g) | Run actions/setup-python@v5 | 1.0 |
| test (g) | Run actions/setup-node@v4 | 0.0 |
| test (g) | Restore pip dependency cache (#7612, read-only — | 4.0 |
| test (g) | Restore uv dependency cache (#7612, read-only — | 4.0 |
| test (g) | Run ./.github/actions/install-cpu-torch | 21.0 |
| test (g) | Install package + test extras | 33.0 |
| test (g) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test (g) | Restore HF embedding model (BAAI/bge-small-en-v1.5) (#11063) | 2.0 |
| test (g) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test (g) | Provision FalkorDB services on ephemeral host ports (#6673) | 14.0 |
| test (g) | Compute docker URI (epic | 0.0 |
| test (g) | Generate coverage manifest (expected nodeids, epic | 46.0 |
| test (g) | Canary producer (epic | 0.0 |
| test (g) | Restore pytest last-failed state (#6142) | 0.0 |
| test (g) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (g) | Run fast test suite (slow files run in the test-slow job) | 786.0 |
| test (g) | Save pytest last-failed state (#6142) | 0.0 |
| test (g) | Upload pytest log (measurement artifact, | 2.0 |
| test (g) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (g) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (g) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (g) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (g) | Assert no redislite orphans (issue | 0.0 |
| test (g) | Stop FalkorDB services (#6673) | 0.0 |
| test (g) | Prune the uv cache before saving it (#11063) | 0.0 |
| test (g) | Save uv dependency cache (#11063, BOUNDED) | 0.0 |
| test (g) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test (g) | Post Run actions/setup-node@v4 | 0.0 |
| test (g) | Post Run actions/setup-python@v5 | 0.0 |
| test (g) | Post Run actions/checkout@v4 | 0.0 |
| test (g) | Complete job | 0.0 |
| test-slow (c) | Set up job | 1.0 |
| test-slow (c) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test-slow (c) | Run actions/checkout@v4 | 13.0 |
| test-slow (c) | Run actions/setup-python@v5 | 0.0 |
| test-slow (c) | Cache pip dependency caches (#7612) | 8.0 |
| test-slow (c) | Run ./.github/actions/install-cpu-torch | 21.0 |
| test-slow (c) | Install package + test extras | 26.0 |
| test-slow (c) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test-slow (c) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 4.0 |
| test-slow (c) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 3.0 |
| test-slow (c) | Drift guard — the expanded legs cover the gating slow pool (#1471/#9539/#10194) | 1.0 |
| test-slow (c) | Provision FalkorDB services on ephemeral host ports (#6673) | 12.0 |
| test-slow (c) | Compute docker URI (epic | 0.0 |
| test-slow (c) | Generate coverage manifest (slow legs — expected nodeids, epic | 8.0 |
| test-slow (c) | Run slow test suite — leg c (known-slow files, | 318.0 |
| test-slow (c) | Skip-fail guard (slow legs, epic | 0.0 |
| test-slow (c) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-slow (c) | Assert no redislite orphans (issue | 0.0 |
| test-slow (c) | Upload pytest log (measurement artifact, | 2.0 |
| test-slow (c) | Stop FalkorDB services (#6673) | 0.0 |
| test-slow (c) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test-slow (c) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-slow (c) | Post Cache pip dependency caches (#7612) | 0.0 |
| test-slow (c) | Post Run actions/setup-python@v5 | 0.0 |
| test-slow (c) | Post Run actions/checkout@v4 | 1.0 |
| test-slow (c) | Complete job | 0.0 |
| test-slow (a) | Set up job | 1.0 |
| test-slow (a) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test-slow (a) | Run actions/checkout@v4 | 12.0 |
| test-slow (a) | Run actions/setup-python@v5 | 0.0 |
| test-slow (a) | Cache pip dependency caches (#7612) | 5.0 |
| test-slow (a) | Run ./.github/actions/install-cpu-torch | 26.0 |
| test-slow (a) | Install package + test extras | 40.0 |
| test-slow (a) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test-slow (a) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 3.0 |
| test-slow (a) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 6.0 |
| test-slow (a) | Drift guard — the expanded legs cover the gating slow pool (#1471/#9539/#10194) | 1.0 |
| test-slow (a) | Provision FalkorDB services on ephemeral host ports (#6673) | 11.0 |
| test-slow (a) | Compute docker URI (epic | 0.0 |
| test-slow (a) | Generate coverage manifest (slow legs — expected nodeids, epic | 10.0 |
| test-slow (a) | Run slow test suite — leg a (known-slow files, | 579.0 |
| test-slow (a) | Skip-fail guard (slow legs, epic | 0.0 |
| test-slow (a) | Dump tolerated tempdir-cleanup leaks (issue | 1.0 |
| test-slow (a) | Assert no redislite orphans (issue | 0.0 |
| test-slow (a) | Upload pytest log (measurement artifact, | 1.0 |
| test-slow (a) | Stop FalkorDB services (#6673) | 0.0 |
| test-slow (a) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test-slow (a) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-slow (a) | Post Cache pip dependency caches (#7612) | 0.0 |
| test-slow (a) | Post Run actions/setup-python@v5 | 0.0 |
| test-slow (a) | Post Run actions/checkout@v4 | 0.0 |
| test-slow (a) | Complete job | 0.0 |
| test (j) | Set up job | 6.0 |
| test (j) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test (j) | Run actions/checkout@v4 | 2.0 |
| test (j) | Run actions/setup-python@v5 | 0.0 |
| test (j) | Run actions/setup-node@v4 | 1.0 |
| test (j) | Restore pip dependency cache (#7612, read-only — | 50.0 |
| test (j) | Restore uv dependency cache (#7612, read-only — | 30.0 |
| test (j) | Run ./.github/actions/install-cpu-torch | 4.0 |
| test (j) | Install package + test extras | 7.0 |
| test (j) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test (j) | Restore HF embedding model (BAAI/bge-small-en-v1.5) (#11063) | 7.0 |
| test (j) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 9.0 |
| test (j) | Provision FalkorDB services on ephemeral host ports (#6673) | 12.0 |
| test (j) | Compute docker URI (epic | 1.0 |
| test (j) | Generate coverage manifest (expected nodeids, epic | 104.0 |
| test (j) | Canary producer (epic | 0.0 |
| test (j) | Restore pytest last-failed state (#6142) | 0.0 |
| test (j) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (j) | Run fast test suite (slow files run in the test-slow job) | 1516.0 |
| test (j) | Save pytest last-failed state (#6142) | 0.0 |
| test (j) | Upload pytest log (measurement artifact, | 2.0 |
| test (j) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (j) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (j) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (j) | Dump tolerated tempdir-cleanup leaks (issue | 1.0 |
| test (j) | Assert no redislite orphans (issue | 0.0 |
| test (j) | Stop FalkorDB services (#6673) | 1.0 |
| test (j) | Prune the uv cache before saving it (#11063) | 1.0 |
| test (j) | Save uv dependency cache (#11063, BOUNDED) | 0.0 |
| test (j) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test (j) | Post Run actions/setup-node@v4 | 0.0 |
| test (j) | Post Run actions/setup-python@v5 | 0.0 |
| test (j) | Post Run actions/checkout@v4 | 0.0 |
| test (j) | Complete job | 0.0 |
| test (b) | Set up job | 13.0 |
| test (e) | Set up job | 1.0 |
| test (e) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test (e) | Run actions/checkout@v4 | 12.0 |
| test (e) | Run actions/setup-python@v5 | 0.0 |
| test (e) | Run actions/setup-node@v4 | 1.0 |
| test (e) | Restore pip dependency cache (#7612, read-only — | 3.0 |
| test (e) | Restore uv dependency cache (#7612, read-only — | 4.0 |
| test (e) | Run ./.github/actions/install-cpu-torch | 19.0 |
| test (e) | Install package + test extras | 29.0 |
| test (e) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test (e) | Restore HF embedding model (BAAI/bge-small-en-v1.5) (#11063) | 1.0 |
| test (e) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test (e) | Provision FalkorDB services on ephemeral host ports (#6673) | 12.0 |
| test (e) | Compute docker URI (epic | 0.0 |
| test (e) | Generate coverage manifest (expected nodeids, epic | 40.0 |
| test (e) | Canary producer (epic | 0.0 |
| test (e) | Restore pytest last-failed state (#6142) | 0.0 |
| test (e) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (e) | Run fast test suite (slow files run in the test-slow job) | 636.0 |
| test (e) | Save pytest last-failed state (#6142) | 0.0 |
| test (e) | Upload pytest log (measurement artifact, | 1.0 |
| test (e) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (e) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (e) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (e) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (e) | Assert no redislite orphans (issue | 0.0 |
| test (e) | Stop FalkorDB services (#6673) | 1.0 |
| test (e) | Prune the uv cache before saving it (#11063) | 0.0 |
| test (e) | Save uv dependency cache (#11063, BOUNDED) | 0.0 |
| test (e) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (e) | Post Run actions/setup-node@v4 | 0.0 |
| test (e) | Post Run actions/setup-python@v5 | 0.0 |
| test (e) | Post Run actions/checkout@v4 | 0.0 |
| test (e) | Complete job | 0.0 |
| test (k) | Set up job | 2.0 |
| test (k) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test (k) | Run actions/checkout@v4 | 14.0 |
| test (k) | Run actions/setup-python@v5 | 1.0 |
| test (k) | Run actions/setup-node@v4 | 2.0 |
| test (k) | Restore pip dependency cache (#7612, read-only — | 7.0 |
| test (k) | Restore uv dependency cache (#7612, read-only — | 5.0 |
| test (k) | Run ./.github/actions/install-cpu-torch | 19.0 |
| test (k) | Install package + test extras | 27.0 |
| test (k) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test (k) | Restore HF embedding model (BAAI/bge-small-en-v1.5) (#11063) | 4.0 |
| test (k) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 3.0 |
| test (k) | Provision FalkorDB services on ephemeral host ports (#6673) | 12.0 |
| test (k) | Compute docker URI (epic | 0.0 |
| test (k) | Generate coverage manifest (expected nodeids, epic | 40.0 |
| test (k) | Canary producer (epic | 0.0 |
| test (k) | Restore pytest last-failed state (#6142) | 0.0 |
| test (k) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (k) | Run fast test suite (slow files run in the test-slow job) | 600.0 |
| test (k) | Save pytest last-failed state (#6142) | 0.0 |
| test (k) | Upload pytest log (measurement artifact, | 2.0 |
| test (k) | Upload canary-producer artifacts (producer shard only, | 1.0 |
| test (k) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (k) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (k) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (k) | Assert no redislite orphans (issue | 0.0 |
| test (k) | Stop FalkorDB services (#6673) | 0.0 |
| test (k) | Prune the uv cache before saving it (#11063) | 0.0 |
| test (k) | Save uv dependency cache (#11063, BOUNDED) | 0.0 |
| test (k) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test (k) | Post Run actions/setup-node@v4 | 0.0 |
| test (k) | Post Run actions/setup-python@v5 | 0.0 |
| test (k) | Post Run actions/checkout@v4 | 0.0 |
| test (k) | Complete job | 0.0 |
| test (c) | Set up job | 1.0 |
| test (c) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test (c) | Run actions/checkout@v4 | 12.0 |
| test (c) | Run actions/setup-python@v5 | 0.0 |
| test (c) | Run actions/setup-node@v4 | 1.0 |
| test (c) | Restore pip dependency cache (#7612, read-only — | 6.0 |
| test (c) | Restore uv dependency cache (#7612, read-only — | 5.0 |
| test (c) | Run ./.github/actions/install-cpu-torch | 25.0 |
| test (c) | Install package + test extras | 39.0 |
| test (c) | Reap ABANDONED redislite litter (#1005 / | 1.0 |
| test (c) | Restore HF embedding model (BAAI/bge-small-en-v1.5) (#11063) | 2.0 |
| test (c) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 6.0 |
| test (c) | Provision FalkorDB services on ephemeral host ports (#6673) | 9.0 |
| test (c) | Compute docker URI (epic | 1.0 |
| test (c) | Generate coverage manifest (expected nodeids, epic | 74.0 |
| test (c) | Canary producer (epic | 0.0 |
| test (c) | Restore pytest last-failed state (#6142) | 0.0 |
| test (c) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (c) | Run fast test suite (slow files run in the test-slow job) | 1007.0 |
| test (c) | Save pytest last-failed state (#6142) | 0.0 |
| test (c) | Upload pytest log (measurement artifact, | 1.0 |
| test (c) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (c) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (c) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (c) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (c) | Assert no redislite orphans (issue | 1.0 |
| test (c) | Stop FalkorDB services (#6673) | 0.0 |
| test (c) | Prune the uv cache before saving it (#11063) | 0.0 |
| test (c) | Save uv dependency cache (#11063, BOUNDED) | 0.0 |
| test (c) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (c) | Post Run actions/setup-node@v4 | 0.0 |
| test (c) | Post Run actions/setup-python@v5 | 0.0 |
| test (c) | Post Run actions/checkout@v4 | 1.0 |
| test (c) | Complete job | 0.0 |
| test (h) | Set up job | 20.0 |
| test (h) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test (h) | Run actions/checkout@v4 | 2.0 |
| test (h) | Run actions/setup-python@v5 | 0.0 |
| test (h) | Run actions/setup-node@v4 | 0.0 |
| test (h) | Restore pip dependency cache (#7612, read-only — | 216.0 |
| test (h) | Restore uv dependency cache (#7612, read-only — | 105.0 |
| test (h) | Run ./.github/actions/install-cpu-torch | 2.0 |
| test (h) | Install package + test extras | 4.0 |
| test (h) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test (h) | Restore HF embedding model (BAAI/bge-small-en-v1.5) (#11063) | 49.0 |
| test (h) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 7.0 |
| test (h) | Provision FalkorDB services on ephemeral host ports (#6673) | 12.0 |
| test (h) | Compute docker URI (epic | 0.0 |
| test (h) | Generate coverage manifest (expected nodeids, epic | 73.0 |
| test (h) | Canary producer (epic | 0.0 |
| test (h) | Restore pytest last-failed state (#6142) | 0.0 |
| test (h) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (h) | Run fast test suite (slow files run in the test-slow job) | 847.0 |
| test (h) | Save pytest last-failed state (#6142) | 0.0 |
| test (h) | Upload pytest log (measurement artifact, | 3.0 |
| test (h) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (h) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (h) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (h) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (h) | Assert no redislite orphans (issue | 0.0 |
| test (h) | Stop FalkorDB services (#6673) | 1.0 |
| test (h) | Prune the uv cache before saving it (#11063) | 0.0 |
| test (h) | Save uv dependency cache (#11063, BOUNDED) | 0.0 |
| test (h) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test (h) | Post Run actions/setup-node@v4 | 0.0 |
| test (h) | Post Run actions/setup-python@v5 | 0.0 |
| test (h) | Post Run actions/checkout@v4 | 0.0 |
| test (h) | Complete job | 0.0 |
| test (a) | Set up job | 2.0 |
| test (a) | Resolve per-job pytest artifact paths (#10028) | 0.0 |
| test (a) | Run actions/checkout@v4 | 7.0 |
| test (a) | Run actions/setup-python@v5 | 0.0 |
| test (a) | Run actions/setup-node@v4 | 5.0 |
| test (a) | Restore pip dependency cache (#7612, read-only — | 3.0 |
| test (a) | Restore uv dependency cache (#7612, read-only — | 2.0 |
| test (a) | Run ./.github/actions/install-cpu-torch | 20.0 |
| test (a) | Install package + test extras | 31.0 |
| test (a) | Reap ABANDONED redislite litter (#1005 / | 0.0 |
| test (a) | Restore HF embedding model (BAAI/bge-small-en-v1.5) (#11063) | 1.0 |
| test (a) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 3.0 |
| test (a) | Provision FalkorDB services on ephemeral host ports (#6673) | 12.0 |
| test (a) | Compute docker URI (epic | 0.0 |
| test (a) | Generate coverage manifest (expected nodeids, epic | 50.0 |
| test (a) | Canary producer (epic | 0.0 |
| test (a) | Restore pytest last-failed state (#6142) | 0.0 |
| test (a) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (a) | Run fast test suite (slow files run in the test-slow job) | 366.0 |
| test (a) | Save pytest last-failed state (#6142) | 0.0 |
| test (a) | Upload pytest log (measurement artifact, | 1.0 |
| test (a) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (a) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (a) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (a) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (a) | Assert no redislite orphans (issue | 0.0 |
| test (a) | Stop FalkorDB services (#6673) | 0.0 |
| test (a) | Prune the uv cache before saving it (#11063) | 0.0 |
| test (a) | Save uv dependency cache (#11063, BOUNDED) | 0.0 |
| test (a) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (a) | Post Run actions/setup-node@v4 | 0.0 |
| test (a) | Post Run actions/setup-python@v5 | 0.0 |
| test (a) | Post Run actions/checkout@v4 | 1.0 |
| test (a) | Complete job | 0.0 |
| ci-introduced-failure | Set up job | 1.0 |
| ci-introduced-failure | Run actions/checkout@v4 | 21.0 |
| ci-introduced-failure | Check out the shared CI predicate (pinned, | 3.0 |
| ci-introduced-failure | Runner tool contract — provide gh (#9018) | 0.0 |
| ci-introduced-failure | Was a leg's failure introduced by this PR? (#9984) | 0.0 |
| ci-introduced-failure | Post Check out the shared CI predicate (pinned, | 1.0 |
| ci-introduced-failure | Post Run actions/checkout@v4 | 0.0 |
| ci-introduced-failure | Complete job | 0.0 |
| canary-streak | Set up job | 1.0 |
| canary-streak | Run actions/checkout@v4 | 24.0 |
| canary-streak | Run actions/setup-python@v5 | 0.0 |
| canary-streak | Runner tool contract — provide gh (#9018) | 0.0 |
| canary-streak | Download producer-shard artifacts (the P1-7 canary contract) | 1.0 |
| canary-streak | Download previous streak artifact (chain continuity) | 2.0 |
| canary-streak | Classify run + update the canary streak (epic | 0.0 |
| canary-streak | Upload canary streak artifact | 1.0 |
| canary-streak | Post canary-streak summary | 0.0 |
| canary-streak | Post Run actions/setup-python@v5 | 1.0 |
| canary-streak | Post Run actions/checkout@v4 | 0.0 |
| canary-streak | Complete job | 0.0 |
| python-ci-gate | Set up job | 0.0 |
| python-ci-gate | Aggregate matrix result | 0.0 |
| python-ci-gate | Complete job | 0.0 |

## Per-file durations (aggregated from --durations=15, slowest tests only)

> Derived, not measured: top-15 slowest tests per job grouped by file. Files whose
> tests are all below the top-15 cutoff are invisible; red runs are truncated by
> `--maxfail=20`. Use for relative regression detection, not absolute budgets.

| File | Tests measured | Total (s) | Max (s) |
|---|---|---|---|
| test_14002_record_only_journal.py | 3 | 47.4 | 34.5 |
| test_cursor_capture_hook.py | 1 | 4.1 | 4.1 |
| test_mergify_config_guard.py | 2 | 5.5 | 3.7 |
| test_embedder_provision.py | 2 | 6.0 | 3.0 |
| test_11609_write_containers.py | 1 | 2.9 | 2.9 |
| test_13035_goal_ending_consistency.py | 1 | 2.8 | 2.8 |
| test_read_routes_loop_responsiveness.py | 1 | 2.7 | 2.7 |
| test_index_docs_api.py | 1 | 2.2 | 2.2 |
| test_writer_inventory.py | 1 | 2.1 | 2.1 |
| test_runner_ports_6673.py | 1 | 2.1 | 2.1 |
| test_4041_breadcrumb_reader.py | 1 | 2.0 | 2.0 |
| test_8066_served_skill_relay_ratchet.py | 1 | 34.3 | 34.3 |
| test_dod_5090_probe.py | 4 | 96.0 | 32.1 |
| test_tmpdir_hygiene_4096.py | 1 | 27.0 | 27.0 |
| test_9078_record_written_by_production_path.py | 2 | 51.1 | 25.9 |
| test_derived_names.py | 2 | 29.5 | 16.1 |
| test_orchestrator_harness_retained.py | 2 | 28.8 | 14.4 |
| test_extractor_cost_measure.py | 2 | 18.1 | 9.4 |
| test_8057_probe_embedder_mask.py | 1 | 9.0 | 9.0 |
| test_smoke_embedded.py | 1 | 51.8 | 51.8 |
| test_embedded_lifecycle.py | 10 | 105.8 | 12.4 |
| test_guard.py | 3 | 28.6 | 10.5 |
| test_runner_tool_contract.py | 1 | 9.9 | 9.9 |
| test_embedded_concurrency.py | 3 | 65.2 | 37.7 |
| test_reaper.py | 8 | 84.7 | 12.5 |
| test_fork_safety_3845.py | 1 | 8.0 | 8.0 |
| test_13528_read_never_blocks_on_embedder.py | 4 | 31.6 | 7.9 |
| test_4921_construct_lock.py | 3 | 109.2 | 60.1 |
| test_longmem_runner.py | 9 | 61.4 | 15.6 |
| test_12067_three_mechanisms.py | 1 | 5.5 | 5.5 |
| test_10121_recency_tie.py | 1 | 4.9 | 4.9 |
| test_eval_ingest_retry.py | 1 | 3.8 | 3.8 |
| test_event_store.py | 2 | 10.0 | 7.1 |
| test_session_verify.py | 6 | 90.5 | 20.4 |
| test_13629_entity_vector_retire.py | 2 | 17.1 | 10.9 |
| test_7949_c2_door.py | 1 | 7.8 | 7.8 |
| test_quota.py | 1 | 7.8 | 7.8 |
| test_w4_why_enrichment.py | 1 | 5.8 | 5.8 |
| test_decide.py | 1 | 5.7 | 5.7 |
| test_from_uri_userinfo.py | 1 | 4.5 | 4.5 |
| test_10017_turn_carrier_replay.py | 1 | 4.4 | 4.4 |
| test_workflow_secret_interpolation.py | 1 | 4.1 | 4.1 |
| test_pi_volunteer_seam.py | 6 | 81.4 | 17.1 |
| test_11702_absent_belief_state.py | 1 | 15.6 | 15.6 |
| test_10047_record_every_lane.py | 1 | 14.7 | 14.7 |
| test_capture_secret_redaction_4911.py | 1 | 14.6 | 14.6 |
| test_why_layer_rate.py | 3 | 34.0 | 11.4 |
| test_session_schema_3554.py | 1 | 10.1 | 10.1 |
| test_retention_promise.py | 1 | 9.7 | 9.7 |
| test_14125_retired_name_calls.py | 2 | 129.3 | 84.7 |
| test_4314_inert_hooks.py | 1 | 20.1 | 20.1 |
| test_ci_timing.py | 8 | 73.0 | 14.1 |
| test_env_truthy.py | 2 | 19.2 | 9.6 |
| test_battery_exposure_liveness.py | 1 | 7.1 | 7.1 |
| test_durability_posture.py | 1 | 4.7 | 4.7 |
| test_ci_import_closure.py | 1 | 55.7 | 55.7 |
| test_openclaw_seam.py | 1 | 42.5 | 42.5 |
| test_uri_env_mutations_declared.py | 1 | 25.5 | 25.5 |
| test_main_guards.py | 3 | 40.9 | 25.3 |
| test_rerank_pool.py | 1 | 16.5 | 16.5 |
| test_ci_preview_isolation.py | 5 | 51.5 | 10.3 |
| test_longmem_usage_e2e.py | 1 | 9.2 | 9.2 |
| test_kind_eval_set.py | 1 | 7.3 | 7.3 |
| test_pack_manifest_store_extraction.py | 1 | 6.6 | 6.6 |
| test_deploy_workflow.py | 1 | 28.0 | 28.0 |
| test_store_sync_worker.py | 5 | 94.6 | 19.1 |
| test_11641_runtime_vector_label.py | 1 | 15.8 | 15.8 |
| test_pi_capture_hooks.py | 1 | 15.8 | 15.8 |
| test_v4_declared_mechanisms.py | 1 | 14.5 | 14.5 |
| test_8447_truncation_marker.py | 4 | 29.6 | 11.9 |
| test_entity_identity.py | 1 | 6.6 | 6.6 |
| test_tool_registry.py | 1 | 5.7 | 5.7 |
| test_destructive_wipe_guard.py | 1 | 65.5 | 65.5 |
| test_7948_about_object_read_sites.py | 5 | 156.8 | 42.5 |
| test_issue_process_lint.py | 1 | 28.6 | 28.6 |
| test_battery_corpus.py | 1 | 24.3 | 24.3 |
| test_ep_local_395.py | 1 | 16.4 | 16.4 |
| test_sufficiency_router.py | 1 | 11.7 | 11.7 |
| test_11609_create_arms.py | 1 | 11.3 | 11.3 |
| test_collision_preflight.py | 3 | 30.8 | 10.4 |
| test_export_cli.py | 1 | 10.2 | 10.2 |
| test_uri_requiring_selection.py | 1 | 101.1 | 101.1 |
| test_9124_declared_live_doors.py | 5 | 55.4 | 30.1 |
| test_9397_about_edge_properties.py | 1 | 25.8 | 25.8 |
| test_9567_record_capstone.py | 1 | 15.8 | 15.8 |
| test_state_value_verbatim_10360.py | 1 | 13.2 | 13.2 |
| test_capture_loop_responsiveness.py | 2 | 12.2 | 7.6 |
| test_consolidation_4way.py | 3 | 15.0 | 5.3 |
| test_6211_ep_scale.py | 1 | 4.6 | 4.6 |
| test_surface_manifest.py | 3 | 109.4 | 36.8 |
| test_ep_selector.py | 1 | 28.2 | 28.2 |
| test_graph_diagnostics.py | 2 | 28.5 | 19.0 |
| test_relationships_bounded.py | 3 | 31.8 | 11.4 |
| test_epic903_warmstart.py | 6 | 54.9 | 10.5 |
| test_write_verb.py | 1 | 5.8 | 5.8 |
| test_hosted_api.py | 4 | 15.7 | 5.1 |
| test_commit_endpoint.py | 1 | 4.5 | 4.5 |
| test_dr_endpoints.py | 2 | 7.2 | 4.1 |
| test_10371_nonpoint_lifecycle_guard.py | 1 | 3.5 | 3.5 |
| test_monitoring.py | 4 | 12.2 | 3.2 |
| test_index_cli.py | 2 | 5.8 | 2.9 |
| test_ci_selection.py | 5 | 53.1 | 35.5 |
| test_epic903_scheduler.py | 1 | 16.9 | 16.9 |
| test_skip_guard.py | 4 | 51.9 | 16.7 |
| test_markers.py | 2 | 13.9 | 7.6 |
| test_9649_update_entity_point_lifecycle.py | 1 | 4.2 | 4.2 |
| test_index_directory.py | 1 | 3.0 | 3.0 |
| test_ingest_mode.py | 1 | 2.8 | 2.8 |
| test_battery_setup.py | 5 | 124.4 | 55.9 |
| test_pack_shipping_wheel.py | 1 | 36.9 | 20.1 |
| test_11653_s3_hold_not_add.py | 4 | 24.2 | 9.8 |
| test_hosted_volunteer_context.py | 1 | 9.6 | 9.6 |
| test_13685_entity_vector_backfill.py | 1 | 3.2 | 3.2 |
| test_epic903_modes.py | 2 | 5.7 | 2.9 |
| test_source_inheritance_own.py | 1 | 33.3 | 33.3 |
| test_ingest_v2_consolidation.py | 1 | 16.1 | 16.1 |
| test_platform_seams.py | 7 | 72.9 | 14.3 |
| test_client_surface.py | 1 | 13.2 | 13.2 |

## Outcome (suite totals across jobs)

- passed: **45904** · failed: **27** · error: **10** · skipped: **324** · xfailed: 10 · xpassed: 0
- watchdog-killed mid-suite: no
- summary-less legs — no pytest summary line: `pytest-log-test-j/pytest.log`
  - their FAILURES are counted (from progress lines); their PASSES and SKIPS are not — a log without a summary cannot establish them. Read `passed` as a LOWER BOUND for the run, not as a total.

## Flake signal

> Proxy: failed-test lists from consecutive weekly samples. A test that failed in one
> sample and is absent from the next sample's failed list is a *candidate flake*.
> Per-test rerun-based flake rate becomes exact once the retry protocol lands (#1477).

| Test | Failed (run) | Passed again (run) |
|---|---|---|
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-`_HOLD_NONE` (the clause is negated)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-`_HOLD_NONE`'s tail lookahead (a qualifier after `hold none`)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-`_ONE_ENTRY` (`re.escape` \u2014 the filename's `.` is a wildcard unescaped)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-`_ONE_ENTRY` (a qualifier AFTER the name)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-`_ONE_ENTRY` (a quantifier replaces the colon)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-`_ONE_ENTRY`'s tail lookahead (a qualifier between the name and the clause)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| `tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-neighbour loop (a number on a line naming the ENTRY)]` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the `sentence_numbers` arm (a number with NO cite separator)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the `slow_files:` leak arm]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the `surfaces:`-above-`carve_out:` ordering arm]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the absent-or-empty `slow_files:` arm]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the basename comparison (a PATH-PREFIXED entry in `slow_files:`)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the different-capstone arm (`elif carve != [ENTRY]:`)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the doc-wide `_MANIFEST_CITE` arm (a YEAR-shaped number the other two refuse)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| `tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the duplicate-key refusal]` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the empty-manifest refusal (`_parse_manifest` returns None)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| `tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the holds-one arm on the manifest side]` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| ``tests/test_7949_capstone_measure.py::test_each_block_arm_reds_on_the_mutation_it_guards[<lambda>-the non-list shape guard (a VALID mapping-shaped `slow_files:`)]`` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| `tests/test_7949_capstone_measure.py::test_the_bundles_cites_resolve_where_the_bundle_says_they_do` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| `tests/test_7949_capstone_measure.py::test_the_epic_bundle_cites_the_branch_rederived_resolve` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| `tests/test_7949_capstone_verdict_check.py::test_the_probes_line_citations_resolve_too` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| `tests/test_7949_capstone_verdict_check.py::test_the_readmes_line_citations_resolve_to_the_symbols_they_name` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |
| `tests/test_demo_session_read_exclusion_8290.py::test_the_hosted_api_cite_notes_state_one_measurement_and_the_pivot` | `43792641690` | 2026-11-23T05:52:50Z (`45071394329`) |

## History (bounded to last 52 samples)

| Sample | Run | Conclusion | Passed | Failed | Error | Skipped | Steps max job (s) |
|---|---|---|---|---|---|---|---|
| 2026-11-23T05:52:50Z | 45071394329 | failure | 45904 | 27 | 10 | 324 | 1755 |
| 2026-11-16T04:44:19Z | 43792641690 | failure | 51282 | 23 | 0 | 257 | 1145 |
| 2026-11-09T04:46:24Z | 42546828652 | success | 49358 | 0 | 0 | 239 | 1694 |
| 2026-11-02T05:15:03Z | 41375596528 | success | 41514 | 0 | 0 | 185 | 893 |
| 2026-10-26T04:45:51Z | 40259128208 | failure | 36093 | 7 | 0 | 149 | 3377 |
| 2026-10-21T09:30:00Z | 39467226356 | failure | 35291 | 5 | 0 | 146 | 1346 |
| 2026-10-20T18:40:00Z | 39191149586 | failure | 30697 | 6 | 1 | 129 | 1300 |
| 2026-10-17T15:56:38Z | 39040632415 | success | 31284 | 0 | 0 | 139 | 1414 |
| 2026-10-13T03:18:43Z | 38337541559 | success | 28478 | 0 | 0 | 137 | 1232 |
| 2026-10-12T04:54:57Z | 38203397437 | success | 27677 | 0 | 0 | 137 | 1052 |
