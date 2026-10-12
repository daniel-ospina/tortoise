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
> workflow, sampling the latest completed push-to-main Python CI run. Never a gate.

## Sampled run

- run_id: `38203397437`
- head_sha: `b4ea10e25f673ecb48a69f43485c01b2b0ea6f44`
- created_at: `2026-10-12T04:20:44Z`
- conclusion: `success`
- sample_time: `2026-10-12T04:54:57Z`
- selection: latest completed `event=push&branch=main` run, `exclude_pull_requests=true`, cancelled skipped
- schema_version: `1`

## Step timings (Jobs API — real run)

Second-granularity timestamps: sub-10s steps read 0s — do not alarm on those.

| Job | Step | Duration (s) |
|---|---|---|
| manifest-integrity | Set up job | 1.0 |
| manifest-integrity | Run actions/checkout@v4 | 5.0 |
| manifest-integrity | Run actions/setup-python@v5 | 0.0 |
| manifest-integrity | Install pyyaml | 2.0 |
| manifest-integrity | Manifest integrity (drift gate, #1262) | 0.0 |
| manifest-integrity | Mergify protection invariants (config guard, #5215) | 3.0 |
| manifest-integrity | Registry integrity (union merge fails closed, #5373) | 1.0 |
| manifest-integrity | Required-set sync (#6144) | 1.0 |
| manifest-integrity | Post Run actions/setup-python@v5 | 0.0 |
| manifest-integrity | Post Run actions/checkout@v4 | 0.0 |
| manifest-integrity | Complete job | 0.0 |
| surface-guard | Set up job | 1.0 |
| surface-guard | Run actions/checkout@v4 | 5.0 |
| surface-guard | Run actions/setup-python@v5 | 0.0 |
| surface-guard | Install | 31.0 |
| surface-guard | Pin the gate's tooling to the base ref | 0.0 |
| surface-guard | The surface cannot expand without an explicit human decision | 3.0 |
| surface-guard | The ordering lint (AC13) | 19.0 |
| surface-guard | The list is generated from the manifest and cannot drift from it | 1.0 |
| surface-guard | Surface target compliance (informational, | 0.0 |
| surface-guard | Post Run actions/setup-python@v5 | 1.0 |
| surface-guard | Post Run actions/checkout@v4 | 0.0 |
| surface-guard | Complete job | 0.0 |
| workflow-lint | Set up job | 0.0 |
| workflow-lint | Run actions/checkout@v4 | 6.0 |
| workflow-lint | actionlint (workflow syntax + Actions-expression errors) | 3.0 |
| workflow-lint | Post Run actions/checkout@v4 | 0.0 |
| workflow-lint | Complete job | 0.0 |
| changes | Set up job | 1.0 |
| changes | Run actions/checkout@v4 | 11.0 |
| changes | Run actions/setup-python@v5 | 0.0 |
| changes | Install pyyaml | 2.0 |
| changes | Tiered selection | 0.0 |
| changes | Emit push matrix (#1472/#6135) | 1.0 |
| changes | Tier-2 duration split (#1473) | 0.0 |
| changes | Secrets scan (evidence artifacts, | 0.0 |
| changes | Post Run actions/setup-python@v5 | 0.0 |
| changes | Post Run actions/checkout@v4 | 1.0 |
| changes | Complete job | 0.0 |
| test-track-b | Set up job | 1.0 |
| test-track-b | Run actions/checkout@v4 | 5.0 |
| test-track-b | Run actions/setup-python@v5 | 2.0 |
| test-track-b | Install | 15.0 |
| test-track-b | Provision FalkorDB services on ephemeral host ports (#6673) | 13.0 |
| test-track-b | Generate coverage manifest (track-b, epic | 1.0 |
| test-track-b | Run Track B tests (explicit marker) | 4.0 |
| test-track-b | Skip-fail guard (track-b, epic | 0.0 |
| test-track-b | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-track-b | Upload track-b log (measurement artifact, | 1.0 |
| test-track-b | Stop FalkorDB services (#6673) | 0.0 |
| test-track-b | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test-track-b | Post Run actions/setup-python@v5 | 1.0 |
| test-track-b | Post Run actions/checkout@v4 | 0.0 |
| test-track-b | Complete job | 0.0 |
| test-concurrency-falkor | Set up job | 1.0 |
| test-concurrency-falkor | Run actions/checkout@v4 | 5.0 |
| test-concurrency-falkor | Run actions/setup-python@v5 | 0.0 |
| test-concurrency-falkor | Cache pip dependency caches (#7612) | 6.0 |
| test-concurrency-falkor | Run ./.github/actions/install-cpu-torch | 25.0 |
| test-concurrency-falkor | Install package + test extras | 38.0 |
| test-concurrency-falkor | Clean stale redislite orphans (issue | 0.0 |
| test-concurrency-falkor | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 2.0 |
| test-concurrency-falkor | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 6.0 |
| test-concurrency-falkor | Provision FalkorDB services on ephemeral host ports (#6673) | 10.0 |
| test-concurrency-falkor | Run live concurrency tests (real FalkorDB sidecar) | 27.0 |
| test-concurrency-falkor | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-concurrency-falkor | Upload pytest log (measurement artifact, | 1.0 |
| test-concurrency-falkor | Validate docker-compose.yml (ports + header edits) | 3.0 |
| test-concurrency-falkor | Docs-consistency guard (embedded must never be "default" again) | 0.0 |
| test-concurrency-falkor | Stop FalkorDB services (#6673) | 0.0 |
| test-concurrency-falkor | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test-concurrency-falkor | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-concurrency-falkor | Post Cache pip dependency caches (#7612) | 0.0 |
| test-concurrency-falkor | Post Run actions/setup-python@v5 | 0.0 |
| test-concurrency-falkor | Post Run actions/checkout@v4 | 0.0 |
| test-concurrency-falkor | Complete job | 0.0 |
| test-d14-hosted-api | Set up job | 1.0 |
| test-d14-hosted-api | Run actions/checkout@v4 | 5.0 |
| test-d14-hosted-api | Run actions/setup-python@v5 | 0.0 |
| test-d14-hosted-api | Cache pip dependency caches (#7612) | 3.0 |
| test-d14-hosted-api | Run ./.github/actions/install-cpu-torch | 24.0 |
| test-d14-hosted-api | Install package + test extras | 37.0 |
| test-d14-hosted-api | Run the embedded_only marker selection (URI unset — | 129.0 |
| test-d14-hosted-api | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-d14-hosted-api | Upload D14 log (measurement artifact, | 0.0 |
| test-d14-hosted-api | Post Cache pip dependency caches (#7612) | 1.0 |
| test-d14-hosted-api | Post Run actions/setup-python@v5 | 0.0 |
| test-d14-hosted-api | Post Run actions/checkout@v4 | 0.0 |
| test-d14-hosted-api | Complete job | 0.0 |
| packs-compile | Set up job | 0.0 |
| packs-compile | Run actions/checkout@v4 | 5.0 |
| packs-compile | Run actions/setup-python@v5 | 1.0 |
| packs-compile | Install package | 19.0 |
| packs-compile | Whole-registry compile (all packs, zero validation errors) | 0.0 |
| packs-compile | Post Run actions/setup-python@v5 | 0.0 |
| packs-compile | Post Run actions/checkout@v4 | 0.0 |
| packs-compile | Complete job | 0.0 |
| test-slow (a) | Set up job | 1.0 |
| test-slow (a) | Run actions/checkout@v4 | 5.0 |
| test-slow (a) | Run actions/setup-python@v5 | 1.0 |
| test-slow (a) | Cache pip dependency caches (#7612) | 2.0 |
| test-slow (a) | Run ./.github/actions/install-cpu-torch | 26.0 |
| test-slow (a) | Install package + test extras | 41.0 |
| test-slow (a) | Clean stale redislite orphans (issue | 0.0 |
| test-slow (a) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test-slow (a) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 5.0 |
| test-slow (a) | Drift guard — legs cover slow_files minus carve-out (#1471) | 1.0 |
| test-slow (a) | Provision FalkorDB services on ephemeral host ports (#6673) | 11.0 |
| test-slow (a) | Compute docker URI (epic | 0.0 |
| test-slow (a) | Generate coverage manifest (slow legs — expected nodeids, epic | 22.0 |
| test-slow (a) | Run slow test suite — leg a (known-slow files, | 934.0 |
| test-slow (a) | Skip-fail guard (slow legs, epic | 0.0 |
| test-slow (a) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-slow (a) | Assert no redislite orphans (issue | 0.0 |
| test-slow (a) | Upload pytest log (measurement artifact, | 1.0 |
| test-slow (a) | Stop FalkorDB services (#6673) | 0.0 |
| test-slow (a) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test-slow (a) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-slow (a) | Post Cache pip dependency caches (#7612) | 0.0 |
| test-slow (a) | Post Run actions/setup-python@v5 | 0.0 |
| test-slow (a) | Post Run actions/checkout@v4 | 0.0 |
| test-slow (a) | Complete job | 0.0 |
| test-carve-out (b) | Set up job | 0.0 |
| test-carve-out (b) | Run actions/checkout@v4 | 6.0 |
| test-carve-out (b) | Run actions/setup-python@v5 | 0.0 |
| test-carve-out (b) | Cache pip dependency caches (#7612) | 4.0 |
| test-carve-out (b) | Run ./.github/actions/install-cpu-torch | 25.0 |
| test-carve-out (b) | Install package + test extras | 39.0 |
| test-carve-out (b) | Clean stale redislite orphans (issue | 0.0 |
| test-carve-out (b) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test-carve-out (b) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 6.0 |
| test-carve-out (b) | Generate coverage manifest (carve-out — expected nodeids, epic | 6.0 |
| test-carve-out (b) | Run carve-out suite (embedded, URI unset — E2E-4) | 332.0 |
| test-carve-out (b) | Temp-dir guard tests (TMPDIR unset — the GitHub-runner spelling condition, #3776) | 5.0 |
| test-carve-out (b) | Skip-fail guard (carve-out, epic | 0.0 |
| test-carve-out (b) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-carve-out (b) | Assert no redislite orphans (issue | 0.0 |
| test-carve-out (b) | Upload carve-out log (measurement artifact, | 1.0 |
| test-carve-out (b) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-carve-out (b) | Post Cache pip dependency caches (#7612) | 0.0 |
| test-carve-out (b) | Post Run actions/setup-python@v5 | 0.0 |
| test-carve-out (b) | Post Run actions/checkout@v4 | 0.0 |
| test-carve-out (b) | Complete job | 0.0 |
| test-slow (b) | Set up job | 1.0 |
| test-slow (b) | Run actions/checkout@v4 | 6.0 |
| test-slow (b) | Run actions/setup-python@v5 | 0.0 |
| test-slow (b) | Cache pip dependency caches (#7612) | 5.0 |
| test-slow (b) | Run ./.github/actions/install-cpu-torch | 22.0 |
| test-slow (b) | Install package + test extras | 34.0 |
| test-slow (b) | Clean stale redislite orphans (issue | 0.0 |
| test-slow (b) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 2.0 |
| test-slow (b) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 5.0 |
| test-slow (b) | Drift guard — legs cover slow_files minus carve-out (#1471) | 1.0 |
| test-slow (b) | Provision FalkorDB services on ephemeral host ports (#6673) | 15.0 |
| test-slow (b) | Compute docker URI (epic | 0.0 |
| test-slow (b) | Generate coverage manifest (slow legs — expected nodeids, epic | 16.0 |
| test-slow (b) | Run slow test suite — leg b (known-slow files, | 926.0 |
| test-slow (b) | Skip-fail guard (slow legs, epic | 1.0 |
| test-slow (b) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-slow (b) | Assert no redislite orphans (issue | 0.0 |
| test-slow (b) | Upload pytest log (measurement artifact, | 1.0 |
| test-slow (b) | Stop FalkorDB services (#6673) | 0.0 |
| test-slow (b) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test-slow (b) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test-slow (b) | Post Cache pip dependency caches (#7612) | 0.0 |
| test-slow (b) | Post Run actions/setup-python@v5 | 0.0 |
| test-slow (b) | Post Run actions/checkout@v4 | 0.0 |
| test-slow (b) | Complete job | 0.0 |
| test (a) | Set up job | 1.0 |
| test (a) | Run actions/checkout@v4 | 5.0 |
| test (a) | Run actions/setup-python@v5 | 0.0 |
| test (a) | Run actions/setup-node@v4 | 1.0 |
| test (a) | Cache uv + pip dependency caches (#7612) | 7.0 |
| test (a) | Run ./.github/actions/install-cpu-torch | 25.0 |
| test (a) | Install package + test extras | 41.0 |
| test (a) | Clean stale redislite orphans (issue | 0.0 |
| test (a) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test (a) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 6.0 |
| test (a) | Provision FalkorDB services on ephemeral host ports (#6673) | 9.0 |
| test (a) | Compute docker URI (epic | 0.0 |
| test (a) | Generate coverage manifest (expected nodeids, epic | 39.0 |
| test (a) | Canary producer (epic | 0.0 |
| test (a) | Restore pytest last-failed state (#6142) | 0.0 |
| test (a) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (a) | Run fast test suite (slow files run in the test-slow job) | 329.0 |
| test (a) | Save pytest last-failed state (#6142) | 0.0 |
| test (a) | Upload pytest log (measurement artifact, | 1.0 |
| test (a) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (a) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (a) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (a) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (a) | Assert no redislite orphans (issue | 0.0 |
| test (a) | Stop FalkorDB services (#6673) | 1.0 |
| test (a) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (a) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test (a) | Post Cache uv + pip dependency caches (#7612) | 0.0 |
| test (a) | Post Run actions/setup-node@v4 | 0.0 |
| test (a) | Post Run actions/setup-python@v5 | 1.0 |
| test (a) | Post Run actions/checkout@v4 | 0.0 |
| test (a) | Complete job | 0.0 |
| test (g) | Set up job | 1.0 |
| test (g) | Run actions/checkout@v4 | 5.0 |
| test (g) | Run actions/setup-python@v5 | 0.0 |
| test (g) | Run actions/setup-node@v4 | 1.0 |
| test (g) | Cache uv + pip dependency caches (#7612) | 7.0 |
| test (g) | Run ./.github/actions/install-cpu-torch | 25.0 |
| test (g) | Install package + test extras | 40.0 |
| test (g) | Clean stale redislite orphans (issue | 0.0 |
| test (g) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test (g) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 6.0 |
| test (g) | Provision FalkorDB services on ephemeral host ports (#6673) | 10.0 |
| test (g) | Compute docker URI (epic | 0.0 |
| test (g) | Generate coverage manifest (expected nodeids, epic | 39.0 |
| test (g) | Canary producer (epic | 0.0 |
| test (g) | Restore pytest last-failed state (#6142) | 0.0 |
| test (g) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (g) | Run fast test suite (slow files run in the test-slow job) | 495.0 |
| test (g) | Save pytest last-failed state (#6142) | 0.0 |
| test (g) | Upload pytest log (measurement artifact, | 1.0 |
| test (g) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (g) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (g) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (g) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (g) | Assert no redislite orphans (issue | 0.0 |
| test (g) | Stop FalkorDB services (#6673) | 0.0 |
| test (g) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (g) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test (g) | Post Cache uv + pip dependency caches (#7612) | 0.0 |
| test (g) | Post Run actions/setup-node@v4 | 0.0 |
| test (g) | Post Run actions/setup-python@v5 | 0.0 |
| test (g) | Post Run actions/checkout@v4 | 0.0 |
| test (g) | Complete job | 0.0 |
| test (b) | Set up job | 2.0 |
| test (b) | Run actions/checkout@v4 | 5.0 |
| test (b) | Run actions/setup-python@v5 | 0.0 |
| test (b) | Run actions/setup-node@v4 | 1.0 |
| test (b) | Cache uv + pip dependency caches (#7612) | 6.0 |
| test (b) | Run ./.github/actions/install-cpu-torch | 22.0 |
| test (b) | Install package + test extras | 37.0 |
| test (b) | Clean stale redislite orphans (issue | 0.0 |
| test (b) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test (b) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 5.0 |
| test (b) | Provision FalkorDB services on ephemeral host ports (#6673) | 9.0 |
| test (b) | Compute docker URI (epic | 0.0 |
| test (b) | Generate coverage manifest (expected nodeids, epic | 23.0 |
| test (b) | Canary producer (epic | 0.0 |
| test (b) | Restore pytest last-failed state (#6142) | 0.0 |
| test (b) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (b) | Run fast test suite (slow files run in the test-slow job) | 259.0 |
| test (b) | Save pytest last-failed state (#6142) | 0.0 |
| test (b) | Upload pytest log (measurement artifact, | 1.0 |
| test (b) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (b) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (b) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (b) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (b) | Assert no redislite orphans (issue | 0.0 |
| test (b) | Stop FalkorDB services (#6673) | 0.0 |
| test (b) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test (b) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test (b) | Post Cache uv + pip dependency caches (#7612) | 0.0 |
| test (b) | Post Run actions/setup-node@v4 | 0.0 |
| test (b) | Post Run actions/setup-python@v5 | 0.0 |
| test (b) | Post Run actions/checkout@v4 | 0.0 |
| test (b) | Complete job | 0.0 |
| test-carve-out | Set up job | 1.0 |
| test-carve-out | Run actions/checkout@v4 | 5.0 |
| test-carve-out | Run actions/setup-python@v5 | 0.0 |
| test-carve-out | Cache pip dependency caches (#7612) | 5.0 |
| test-carve-out | Run ./.github/actions/install-cpu-torch | 16.0 |
| test-carve-out | Install package + test extras | 26.0 |
| test-carve-out | Clean stale redislite orphans (issue | 0.0 |
| test-carve-out | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 3.0 |
| test-carve-out | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test-carve-out | Generate coverage manifest (carve-out — expected nodeids, epic | 3.0 |
| test-carve-out | Run carve-out suite (embedded, URI unset — E2E-4) | 286.0 |
| test-carve-out | Temp-dir guard tests (TMPDIR unset — the GitHub-runner spelling condition, #3776) | 3.0 |
| test-carve-out | Skip-fail guard (carve-out, epic | 0.0 |
| test-carve-out | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-carve-out | Assert no redislite orphans (issue | 0.0 |
| test-carve-out | Upload carve-out log (measurement artifact, | 2.0 |
| test-carve-out | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-carve-out | Post Cache pip dependency caches (#7612) | 0.0 |
| test-carve-out | Post Run actions/setup-python@v5 | 0.0 |
| test-carve-out | Post Run actions/checkout@v4 | 0.0 |
| test-carve-out | Complete job | 0.0 |
| test-carve-out (c) | Set up job | 1.0 |
| test-carve-out (c) | Run actions/checkout@v4 | 5.0 |
| test-carve-out (c) | Run actions/setup-python@v5 | 0.0 |
| test-carve-out (c) | Cache pip dependency caches (#7612) | 6.0 |
| test-carve-out (c) | Run ./.github/actions/install-cpu-torch | 26.0 |
| test-carve-out (c) | Install package + test extras | 41.0 |
| test-carve-out (c) | Clean stale redislite orphans (issue | 0.0 |
| test-carve-out (c) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 2.0 |
| test-carve-out (c) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 5.0 |
| test-carve-out (c) | Generate coverage manifest (carve-out — expected nodeids, epic | 8.0 |
| test-carve-out (c) | Run carve-out suite (embedded, URI unset — E2E-4) | 424.0 |
| test-carve-out (c) | Temp-dir guard tests (TMPDIR unset — the GitHub-runner spelling condition, #3776) | 5.0 |
| test-carve-out (c) | Skip-fail guard (carve-out, epic | 0.0 |
| test-carve-out (c) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test-carve-out (c) | Assert no redislite orphans (issue | 0.0 |
| test-carve-out (c) | Upload carve-out log (measurement artifact, | 1.0 |
| test-carve-out (c) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-carve-out (c) | Post Cache pip dependency caches (#7612) | 1.0 |
| test-carve-out (c) | Post Run actions/setup-python@v5 | 0.0 |
| test-carve-out (c) | Post Run actions/checkout@v4 | 0.0 |
| test-carve-out (c) | Complete job | 0.0 |
| test (c) | Set up job | 2.0 |
| test (c) | Run actions/checkout@v4 | 5.0 |
| test (c) | Run actions/setup-python@v5 | 1.0 |
| test (c) | Run actions/setup-node@v4 | 3.0 |
| test (c) | Cache uv + pip dependency caches (#7612) | 6.0 |
| test (c) | Run ./.github/actions/install-cpu-torch | 20.0 |
| test (c) | Install package + test extras | 28.0 |
| test (c) | Clean stale redislite orphans (issue | 0.0 |
| test (c) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test (c) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test (c) | Provision FalkorDB services on ephemeral host ports (#6673) | 16.0 |
| test (c) | Compute docker URI (epic | 0.0 |
| test (c) | Generate coverage manifest (expected nodeids, epic | 23.0 |
| test (c) | Canary producer (epic | 0.0 |
| test (c) | Restore pytest last-failed state (#6142) | 0.0 |
| test (c) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (c) | Run fast test suite (slow files run in the test-slow job) | 165.0 |
| test (c) | Save pytest last-failed state (#6142) | 0.0 |
| test (c) | Upload pytest log (measurement artifact, | 1.0 |
| test (c) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (c) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (c) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (c) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (c) | Assert no redislite orphans (issue | 0.0 |
| test (c) | Stop FalkorDB services (#6673) | 0.0 |
| test (c) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 1.0 |
| test (c) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test (c) | Post Cache uv + pip dependency caches (#7612) | 0.0 |
| test (c) | Post Run actions/setup-node@v4 | 0.0 |
| test (c) | Post Run actions/setup-python@v5 | 0.0 |
| test (c) | Post Run actions/checkout@v4 | 0.0 |
| test (c) | Complete job | 0.0 |
| test (e) | Set up job | 1.0 |
| test (e) | Run actions/checkout@v4 | 5.0 |
| test (e) | Run actions/setup-python@v5 | 0.0 |
| test (e) | Run actions/setup-node@v4 | 2.0 |
| test (e) | Cache uv + pip dependency caches (#7612) | 9.0 |
| test (e) | Run ./.github/actions/install-cpu-torch | 29.0 |
| test (e) | Install package + test extras | 40.0 |
| test (e) | Clean stale redislite orphans (issue | 0.0 |
| test (e) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 3.0 |
| test (e) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 6.0 |
| test (e) | Provision FalkorDB services on ephemeral host ports (#6673) | 10.0 |
| test (e) | Compute docker URI (epic | 0.0 |
| test (e) | Generate coverage manifest (expected nodeids, epic | 29.0 |
| test (e) | Canary producer (epic | 0.0 |
| test (e) | Restore pytest last-failed state (#6142) | 0.0 |
| test (e) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (e) | Run fast test suite (slow files run in the test-slow job) | 269.0 |
| test (e) | Save pytest last-failed state (#6142) | 0.0 |
| test (e) | Upload pytest log (measurement artifact, | 1.0 |
| test (e) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (e) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 1.0 |
| test (e) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (e) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (e) | Assert no redislite orphans (issue | 0.0 |
| test (e) | Stop FalkorDB services (#6673) | 0.0 |
| test (e) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (e) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test (e) | Post Cache uv + pip dependency caches (#7612) | 0.0 |
| test (e) | Post Run actions/setup-node@v4 | 0.0 |
| test (e) | Post Run actions/setup-python@v5 | 0.0 |
| test (e) | Post Run actions/checkout@v4 | 0.0 |
| test (e) | Complete job | 0.0 |
| test (i) | Set up job | 1.0 |
| test (i) | Run actions/checkout@v4 | 5.0 |
| test (i) | Run actions/setup-python@v5 | 0.0 |
| test (i) | Run actions/setup-node@v4 | 1.0 |
| test (i) | Cache uv + pip dependency caches (#7612) | 8.0 |
| test (i) | Run ./.github/actions/install-cpu-torch | 21.0 |
| test (i) | Install package + test extras | 35.0 |
| test (i) | Clean stale redislite orphans (issue | 0.0 |
| test (i) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 2.0 |
| test (i) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test (i) | Provision FalkorDB services on ephemeral host ports (#6673) | 16.0 |
| test (i) | Compute docker URI (epic | 0.0 |
| test (i) | Generate coverage manifest (expected nodeids, epic | 21.0 |
| test (i) | Canary producer (epic | 0.0 |
| test (i) | Restore pytest last-failed state (#6142) | 0.0 |
| test (i) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (i) | Run fast test suite (slow files run in the test-slow job) | 244.0 |
| test (i) | Save pytest last-failed state (#6142) | 0.0 |
| test (i) | Upload pytest log (measurement artifact, | 1.0 |
| test (i) | Upload canary-producer artifacts (producer shard only, | 1.0 |
| test (i) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (i) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (i) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (i) | Assert no redislite orphans (issue | 1.0 |
| test (i) | Stop FalkorDB services (#6673) | 0.0 |
| test (i) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (i) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test (i) | Post Cache uv + pip dependency caches (#7612) | 0.0 |
| test (i) | Post Run actions/setup-node@v4 | 0.0 |
| test (i) | Post Run actions/setup-python@v5 | 1.0 |
| test (i) | Post Run actions/checkout@v4 | 0.0 |
| test (i) | Complete job | 0.0 |
| test (h) | Set up job | 1.0 |
| test (h) | Run actions/checkout@v4 | 6.0 |
| test (h) | Run actions/setup-python@v5 | 0.0 |
| test (h) | Run actions/setup-node@v4 | 1.0 |
| test (h) | Cache uv + pip dependency caches (#7612) | 6.0 |
| test (h) | Run ./.github/actions/install-cpu-torch | 22.0 |
| test (h) | Install package + test extras | 32.0 |
| test (h) | Clean stale redislite orphans (issue | 0.0 |
| test (h) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test (h) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 5.0 |
| test (h) | Provision FalkorDB services on ephemeral host ports (#6673) | 10.0 |
| test (h) | Compute docker URI (epic | 0.0 |
| test (h) | Generate coverage manifest (expected nodeids, epic | 22.0 |
| test (h) | Canary producer (epic | 0.0 |
| test (h) | Restore pytest last-failed state (#6142) | 0.0 |
| test (h) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (h) | Run fast test suite (slow files run in the test-slow job) | 287.0 |
| test (h) | Save pytest last-failed state (#6142) | 0.0 |
| test (h) | Upload pytest log (measurement artifact, | 1.0 |
| test (h) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (h) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (h) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (h) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (h) | Assert no redislite orphans (issue | 0.0 |
| test (h) | Stop FalkorDB services (#6673) | 0.0 |
| test (h) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (h) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test (h) | Post Cache uv + pip dependency caches (#7612) | 1.0 |
| test (h) | Post Run actions/setup-node@v4 | 0.0 |
| test (h) | Post Run actions/setup-python@v5 | 0.0 |
| test (h) | Post Run actions/checkout@v4 | 0.0 |
| test (h) | Complete job | 0.0 |
| test (d) | Set up job | 3.0 |
| test (d) | Run actions/checkout@v4 | 5.0 |
| test (d) | Run actions/setup-python@v5 | 0.0 |
| test (d) | Run actions/setup-node@v4 | 1.0 |
| test (d) | Cache uv + pip dependency caches (#7612) | 8.0 |
| test (d) | Run ./.github/actions/install-cpu-torch | 22.0 |
| test (d) | Install package + test extras | 32.0 |
| test (d) | Clean stale redislite orphans (issue | 0.0 |
| test (d) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test (d) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 5.0 |
| test (d) | Provision FalkorDB services on ephemeral host ports (#6673) | 13.0 |
| test (d) | Compute docker URI (epic | 0.0 |
| test (d) | Generate coverage manifest (expected nodeids, epic | 20.0 |
| test (d) | Canary producer (epic | 0.0 |
| test (d) | Restore pytest last-failed state (#6142) | 0.0 |
| test (d) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (d) | Run fast test suite (slow files run in the test-slow job) | 297.0 |
| test (d) | Save pytest last-failed state (#6142) | 0.0 |
| test (d) | Upload pytest log (measurement artifact, | 1.0 |
| test (d) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (d) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (d) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (d) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (d) | Assert no redislite orphans (issue | 0.0 |
| test (d) | Stop FalkorDB services (#6673) | 1.0 |
| test (d) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (d) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test (d) | Post Cache uv + pip dependency caches (#7612) | 0.0 |
| test (d) | Post Run actions/setup-node@v4 | 0.0 |
| test (d) | Post Run actions/setup-python@v5 | 0.0 |
| test (d) | Post Run actions/checkout@v4 | 1.0 |
| test (d) | Complete job | 0.0 |
| test (f) | Set up job | 3.0 |
| test (f) | Run actions/checkout@v4 | 5.0 |
| test (f) | Run actions/setup-python@v5 | 0.0 |
| test (f) | Run actions/setup-node@v4 | 4.0 |
| test (f) | Cache uv + pip dependency caches (#7612) | 8.0 |
| test (f) | Run ./.github/actions/install-cpu-torch | 22.0 |
| test (f) | Install package + test extras | 30.0 |
| test (f) | Clean stale redislite orphans (issue | 0.0 |
| test (f) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 2.0 |
| test (f) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 4.0 |
| test (f) | Provision FalkorDB services on ephemeral host ports (#6673) | 13.0 |
| test (f) | Compute docker URI (epic | 0.0 |
| test (f) | Generate coverage manifest (expected nodeids, epic | 21.0 |
| test (f) | Canary producer (epic | 0.0 |
| test (f) | Restore pytest last-failed state (#6142) | 0.0 |
| test (f) | Provide rsync for the deploy-step tests (#7026) | 0.0 |
| test (f) | Run fast test suite (slow files run in the test-slow job) | 268.0 |
| test (f) | Save pytest last-failed state (#6142) | 0.0 |
| test (f) | Upload pytest log (measurement artifact, | 1.0 |
| test (f) | Upload canary-producer artifacts (producer shard only, | 0.0 |
| test (f) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (f) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (f) | Dump tolerated tempdir-cleanup leaks (issue | 0.0 |
| test (f) | Assert no redislite orphans (issue | 0.0 |
| test (f) | Stop FalkorDB services (#6673) | 1.0 |
| test (f) | Post Provision FalkorDB services on ephemeral host ports (#6673) | 0.0 |
| test (f) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test (f) | Post Cache uv + pip dependency caches (#7612) | 0.0 |
| test (f) | Post Run actions/setup-node@v4 | 0.0 |
| test (f) | Post Run actions/setup-python@v5 | 0.0 |
| test (f) | Post Run actions/checkout@v4 | 1.0 |
| test (f) | Complete job | 0.0 |
| canary-streak | Set up job | 2.0 |
| canary-streak | Run actions/checkout@v4 | 5.0 |
| canary-streak | Run actions/setup-python@v5 | 1.0 |
| canary-streak | Download producer-shard artifacts (the P1-7 canary contract) | 1.0 |
| canary-streak | Download previous streak artifact (chain continuity) | 4.0 |
| canary-streak | Classify run + update the canary streak (epic | 0.0 |
| canary-streak | Upload canary streak artifact | 1.0 |
| canary-streak | Post canary-streak summary | 1.0 |
| canary-streak | Post Run actions/setup-python@v5 | 0.0 |
| canary-streak | Post Run actions/checkout@v4 | 0.0 |
| canary-streak | Complete job | 0.0 |
| python-ci-gate | Set up job | 1.0 |
| python-ci-gate | Aggregate matrix result | 0.0 |
| python-ci-gate | Complete job | 0.0 |

## Per-file durations (aggregated from --durations=15, slowest tests only)

> Derived, not measured: top-15 slowest tests per job grouped by file. Files whose
> tests are all below the top-15 cutoff are invisible; red runs are truncated by
> `--maxfail=20`. Use for relative regression detection, not absolute budgets.

| File | Tests measured | Total (s) | Max (s) |
|---|---|---|---|
| test_4314_inert_hooks.py | 1 | 20.1 | 20.1 |
| test_decide.py | 1 | 7.5 | 7.5 |
| test_env_truthy.py | 2 | 14.7 | 7.3 |
| test_entity_identity.py | 2 | 7.8 | 5.4 |
| test_session_import_codex.py | 2 | 5.0 | 3.0 |
| test_battery_real_deadline.py | 1 | 3.0 | 3.0 |
| test_bounded_raw_clients_3065.py | 1 | 3.0 | 3.0 |
| test_ci_timing.py | 2 | 4.9 | 2.5 |
| test_battery_executor_v2.py | 1 | 2.2 | 2.2 |
| test_eval_retrieval_budget.py | 1 | 2.0 | 2.0 |
| test_sdk_surface.py | 1 | 2.0 | 2.0 |
| test_uri_requiring_selection.py | 1 | 48.2 | 48.2 |
| test_fly_machines_guard.py | 1 | 4.6 | 4.6 |
| test_2884_ep_state_journaled.py | 1 | 4.5 | 4.5 |
| test_codex_capture_hook.py | 1 | 4.2 | 4.2 |
| test_from_uri_userinfo.py | 1 | 3.1 | 3.1 |
| test_admin_return_to.py | 1 | 2.7 | 2.7 |
| test_selfhost.py | 2 | 4.3 | 2.7 |
| test_agent_signup_idempotency.py | 1 | 2.6 | 2.6 |
| test_epic903_lifecycle.py | 2 | 4.3 | 2.2 |
| test_fly_secret_drift.py | 1 | 2.1 | 2.1 |
| test_tortoise_client.py | 1 | 1.7 | 1.7 |
| test_epic903_staleness.py | 1 | 1.7 | 1.7 |
| test_epic903_observability.py | 1 | 1.6 | 1.6 |
| test_pi_capture_hooks.py | 1 | 10.5 | 10.5 |
| test_uri_env_mutations_declared.py | 1 | 7.7 | 7.7 |
| test_write_path_operator_lane.py | 3 | 9.3 | 4.5 |
| test_vector_arm.py | 2 | 4.4 | 3.3 |
| test_battery_exposure_liveness.py | 2 | 3.8 | 2.9 |
| test_docs_lint_baseline.py | 2 | 2.5 | 1.4 |
| test_battery_determinism.py | 1 | 0.9 | 0.9 |
| test_integrity_gate_docker.py | 1 | 0.8 | 0.8 |
| test_longmem_preflight.py | 1 | 0.8 | 0.8 |
| test_auth_flip.py | 1 | 0.8 | 0.8 |
| test_reaper.py | 12 | 96.1 | 12.6 |
| test_guard.py | 2 | 16.5 | 10.5 |
| test_migrate_db.py | 1 | 5.6 | 5.6 |
| test_4921_construct_lock.py | 3 | 114.1 | 60.1 |
| test_longmem_runner.py | 9 | 49.7 | 18.3 |
| test_eval_ingest_retry.py | 2 | 12.2 | 8.5 |
| test_config.py | 1 | 4.0 | 4.0 |
| test_embedded_concurrency.py | 7 | 126.9 | 45.9 |
| test_smoke_embedded.py | 1 | 38.3 | 38.3 |
| test_embedded_lifecycle.py | 10 | 91.1 | 10.0 |
| test_event_store.py | 2 | 8.6 | 6.5 |
| test_retrieval.py | 4 | 26.1 | 12.1 |
| test_ep_local_395.py | 1 | 9.4 | 9.4 |
| test_quota.py | 2 | 9.5 | 5.5 |
| test_selfhost_health_probe_executor.py | 2 | 9.3 | 5.5 |
| test_capture_loop_responsiveness.py | 2 | 8.2 | 4.4 |
| test_onboarding_state_split_checkpoint.py | 3 | 11.3 | 4.1 |
| test_1162_add_operator_local_svbp.py | 1 | 3.7 | 3.7 |
| test_acl_graph_users.py | 1 | 11.6 | 11.6 |
| test_github_index_lifecycle.py | 1 | 7.9 | 7.9 |
| test_2206_confidence_surfaces.py | 1 | 6.2 | 6.2 |
| test_export_cli.py | 4 | 13.2 | 5.3 |
| test_cursor_capture_hook.py | 1 | 4.1 | 4.1 |
| test_retention_promise.py | 1 | 3.8 | 3.8 |
| test_onboarding_seed_endpoint.py | 5 | 12.9 | 3.1 |
| test_read_routes_loop_responsiveness.py | 1 | 2.2 | 2.2 |
| test_github_indexer.py | 9 | 61.7 | 21.5 |
| test_consolidation_4way.py | 2 | 9.9 | 5.2 |
| test_main_guards.py | 2 | 9.2 | 4.8 |
| test_2500_terminal_ep_backfill.py | 1 | 3.9 | 3.9 |
| test_tripwire.py | 1 | 3.8 | 3.8 |
| test_write_path_benchmark.py | 3 | 235.5 | 157.4 |
| test_destructive_wipe_guard.py | 1 | 12.9 | 12.9 |
| test_battery_corpus.py | 1 | 12.9 | 12.9 |
| test_pack_manifest_store_extraction.py | 1 | 4.5 | 4.5 |
| test_mergify_config_guard.py | 1 | 3.9 | 3.9 |
| test_website_docs_consistency.py | 2 | 6.6 | 3.9 |
| test_oauth_consent_pkce.py | 1 | 3.4 | 3.4 |
| test_graph_write_loop_responsiveness.py | 4 | 8.9 | 2.3 |
| test_index_docs_api.py | 1 | 2.2 | 2.2 |
| test_session_verify.py | 7 | 92.1 | 20.3 |
| test_extractor_reliability.py | 1 | 4.7 | 4.7 |
| test_3276_has_ep_measured.py | 1 | 4.6 | 4.6 |
| test_value_extractor.py | 1 | 4.1 | 4.1 |
| test_tool_registry.py | 1 | 3.8 | 3.8 |
| test_longmem_usage_e2e.py | 1 | 3.2 | 3.2 |
| test_heavy_import_guard.py | 3 | 6.1 | 2.1 |
| test_tmpdir_hygiene_4096.py | 1 | 9.2 | 9.2 |
| test_measure_temporal.py | 1 | 4.7 | 4.7 |
| test_dataplane_lane_loop_responsiveness.py | 4 | 13.2 | 4.1 |
| test_collision_preflight.py | 2 | 5.5 | 3.6 |
| test_embedder_provision.py | 2 | 6.1 | 3.0 |
| test_org_graph_open_tripwire.py | 2 | 4.7 | 2.5 |
| test_boot_regressions.py | 1 | 2.2 | 2.2 |
| test_kind_classifier.py | 1 | 2.1 | 2.1 |
| test_assembly_pure.py | 1 | 2.1 | 2.1 |
| test_run.py | 3 | 196.4 | 150.6 |
| test_source_inheritance_own.py | 1 | 34.3 | 34.3 |
| test_battery_setup.py | 3 | 64.3 | 27.2 |
| test_ci_selection.py | 2 | 45.8 | 23.9 |
| test_client_surface.py | 1 | 16.5 | 16.5 |
| test_ep_selector.py | 1 | 16.3 | 16.3 |
| test_graph_diagnostics.py | 1 | 15.6 | 15.6 |
| test_ingest_v2_consolidation.py | 1 | 15.5 | 15.5 |
| test_epic903_scheduler.py | 1 | 13.9 | 13.9 |
| test_harness_benchmark.py | 1 | 11.2 | 11.2 |
| test_write_path_runner.py | 3 | 90.4 | 30.4 |
| test_why_suite_benchmark.py | 2 | 35.2 | 24.3 |
| test_pack_shipping_wheel.py | 1 | 23.4 | 23.4 |
| test_markers.py | 1 | 21.5 | 21.5 |
| test_surface_manifest.py | 8 | 125.0 | 17.7 |

## Outcome (suite totals across jobs)

- passed: **27677** · failed: **0** · error: **0** · skipped: **137** · xfailed: 5 · xpassed: 0
- watchdog-killed mid-suite: no

## Flake signal

> Proxy: failed-test lists from consecutive weekly samples. A test that failed in one
> sample and is absent from the next sample's failed list is a *candidate flake*.
> Per-test rerun-based flake rate becomes exact once the retry protocol lands (#1477).

*No candidate flakes in the sampled window yet (needs ≥ 2 consecutive samples).*

## History (bounded to last 52 samples)

| Sample | Run | Conclusion | Passed | Failed | Error | Skipped | Steps max job (s) |
|---|---|---|---|---|---|---|---|
| 2026-10-12T04:54:57Z | 38203397437 | success | 27677 | 0 | 0 | 137 | 1052 |
