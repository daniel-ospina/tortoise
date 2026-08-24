# Research — #1656: E2E-8 300ms band re-validation on a deployment-class box (Phase 1.5 artifact)

> Findings date: 2026-08-24 · Source tags: canonical / competitor-precedent / pitfalls
> Issue: #1656 (tortoise) · Pre-registered step: T15 of ADR-009 / #1349
> Purpose: what "production-class box" means for this service, how latency budgets are
> validated pre/post-deploy, whether the 300ms band should be re-derived for target hardware.

---

## Axis 1 — What "production-class box" means for Hosted Tortoise (Architecture, medium)

### The deployment class (canonical — repo config is primary)

- **The production deployment is a single Fly.io app**: `fly.toml` → `app = "tortoise-y4mjjq"`,
  `primary_region = "iad"`, **`cpu_kind = "shared"`, `cpus = 2`, `memory_mb = 4096`**, Docker build
  via `Dockerfile.hosted`, backend DB = **FalkorDB Cloud** (`entrypoint.sh`: "FalkorDB Cloud
  (managed) is the ONLY production database"; `FALKORDB_CLOUD_URI → TORTOISE_DB_URI`).
- **The pre-registered "2GB VM" class language is stale**: fly.toml comment documents the
  `512→2048→4096` history — "torch + sentence-transformers + the 90MB model + the app consistently
  OOM-crash-loops on 2GB cold boots (#545)". The pre-registration's "2GB-VM-class measurement
  host" (docs/scoping/2026-08-17-1349-embedder-selection-scoping.md, docs/plans/…-1349-….md) was
  written under the pre-#545 config. **The T15 pin operationalized for this issue is fly.toml's
  current class: shared-CPU 2 vCPU / 4GB.**
- **The pre-registered protocol CANNOT run on the actual deployed surface** (verified in repo):
  1. **No Docker daemon on Fly** — Fly runs Firecracker microVMs; the prod-parity measurement
     environment is **local Docker FalkorDB ≥4.x** (`benchmarks/README.md`; embedded FalkorDBLite
     is "NOT prod-parity — numbers can reverse").
  2. **The harness is not shipped**: `Dockerfile.hosted` copies only `tortoise/` +
     `product/pricing.json` — no `benchmarks/`, no `tools/embedder_probe.py`.
  3. **The probe seam is FATAL in production**: `entrypoint.sh` rejects
     `TORTOISE_EMBEDDER_OVERRIDE` (T11 design intent — a production container carrying the
     benchmark override is "misconfigured by construction").
  4. **The MiniLM control cannot load in the hosted image**: `HF_HUB_OFFLINE=1` with only
     `BAAI/bge-small-en-v1.5@5c38ec7c…` baked; MiniLM is not in the offline cache →
     `LocalEntryNotFoundError`.
  - **Consequence**: "production-class box" must be operationalized as a **spec'd box of the
    deployment class** running the repo **repo-direct** (git checkout + `uv sync --extra
    embeddings` + local Docker FalkorDB ≥4.x), **not** a staging deployment inside Fly. A staging
    Fly app would require net-new infra (benchmark-capable image carrying the probe seam + control
    model, staging app, DB graph) that (a) contradicts T11's hardening intent, (b) still cannot
    load the control inside the hosted image shape, and (c) is project-workflow scope.

### Fly shared-CPU behavior (canonical — Fly docs)

- **Fly.io CPU performance docs**: shared vCPUs have a **fixed baseline quota of 5ms per 80ms
  period per vCPU** and are **throttled** after it; performance CPUs get 80ms/80ms. Quotas are
  **shared across the machine's vCPUs** → a **shared-cpu-2x machine gets 10ms/80ms combined
  (≈12.5% of one core sustained)** with burst-to-100%-while-burst-balance-holds.
  (https://fly.io/docs/machines/cpu-performance/)
- Community/ops sources: shared CPUs can be contended by **up to ~16 co-tenants**; sustained CPU
  on shared machines is throttled; underlying hardware is **AMD EPYC under KVM**.
  (https://community.fly.io/t/fly-vms-specs/12602/2, https://fly.io/blog/new-vms-more-ram-extra-cpu-and-a-dollar-menu/)
- **Implication for the band**: the E2E-8 path (torch encode + FalkorDB query + RRF + EP annotation
  + serialization) sustains high CPU; on the shared-CPU class the measured p95 is **quota-shaped
  by design** — a sustained benchmark run is likely to be throttled to ~12.5% of one core. This is
  **the tenant experience**, not interference; it must be recorded as class evidence (via a
  cgroup `cpu.stat` snapshot-diff throttle probe over the measured window), and a **dedicated**
  (non-burstable) box is a *more generous* surface whose MET must carry a transfer caveat.

### FalkorDB Cloud surface (canonical — FalkorDB docs)

- FalkorDB Cloud is managed (higher tiers add TLS, VPC peering, HA, backups).
  (https://docs.falkordb.com/cloud) Monitoring tracks per-command latency and throughput.
  Self-hosted FalkorDB benchmark: ~21ms p50 / 46ms p95 at 20 clients (Intel).
  (https://benchmark.falkordb.com/)
- **Implication**: production E2E-8 includes a network/TLS hop to FalkorDB Cloud that is
  **structurally excluded** from every pre-registered local-Docker measurement (the burn's numbers
  and all run_report runs use local Docker FalkorDB). The T15 box measurement keeps the
  pre-registered surface (local Docker v4.16.7 — comparability) and records the cloud hop as an
  excluded caveat; the optional directional live-hosted leg (#1656 scoping, approach C) is the
  only way to include it.

## Axis 2 — How latency budgets are validated pre/post-deploy (canonical + competitor-precedent)

- **Industry practice is a two-phase pattern**: (1) **pre-deploy validation on production-class
  hardware** — load tests on production hardware / staging with **open-loop load**, **steady-state
  and soak tests** (p95/p99 over a defined window); (2) **post-deploy continuous verification** —
  shadow experiments, canary rollout, and **production monitoring** of the live p95 against the
  SLO. (https://treeifyai.com/docs/resources/50-non-functional/performance-p95-p99,
  https://genaiconsulting.services/blog/building-a-cost-and-latency-budget-for-production-rag-systems)
- RAG/retrieval stage budgets in the wild: query embedding 50–100ms, retrieval 50–200ms,
  reranking 100–300ms — i.e., a **~300ms end-to-end retrieval budget is industry-plausible** but
  tight once embedding + retrieval share it. (https://unrag.dev/docs/rag/08-production-and-ops/02-latency-budgets-and-fast-paths)
- Production RAG SLO compliance is often expressed as **p95 over a window** (e.g., a 5s p95 SLO
  with 95% compliance in the SAGE production-RAG study); tail-latency SLOs are validated with
  open-loop load, not closed-loop throughput. (https://arxiv.org/html/2608.08237v1)
- **Repo precedent (in-repo, canonical)**: post-deploy verification in this repo is done on the
  **live endpoint** — `docs/ops/deploy-embeddings-verification.md` (curl `/health`, `/v1/search`
  asserting `match_source: "rrf"`) and `docs/ops/669-post-flip-verification.md` (E2E-1…E2E-9
  against `api.premiselabs.co`). **There is no product-side E2E-8 latency monitor** — the T15
  verdict is a point-in-time certification; a continuous production-latency monitor is a
  documented gap (filed as follow-up).
- **Implication**: a dedicated deployment-class box running the pre-registered protocol is the
  legitimate pre-deploy-style validation surface for the band; the repo's own post-deploy culture
  supports an optional directional live-leg (approach C) as the transfer check. A box pass alone
  does not prove the deployed shared-CPU class under tenant load — that is exactly what the
  caveat annex and the C leg cover.

## Axis 3 — Should the 300ms band be re-derived for the target hardware? (decision, grounded)

**Answer: No — keep 300ms as the verdict criterion; re-derivation is a decision OUTPUT, not a
pre-measurement move.** Grounding:

1. **The band is triple-anchored**: (a) pre-registered in the #316 scoping ("Pre-registered
   numbers and verdict bands are **fixed** — do not change without a scoping revision",
   `benchmarks/bench_core.py` docstring; E2E-8 ≤300ms "achieved" band); (b) **tenant-visible** in
   ADR-009's launch checklist ("Latency envelope ≤300ms p95 (E2E-8 band holds post-swap)") and
   ux-research.md; (c) **user-confirmed** 2026-08-21 (option-1: proceed with the swap; "E2E-8 p95
   <=300ms on the production-class benchmark box" recorded in `verdict-final.json` override).
2. **The band's origin is pre-real-model**: #316 set it as a competitor-informed artifact
   (Neo4j p95 ~340ms single-source; Supermemory "sub-300ms" vendor marketing — both tagged
   ⚠️ in #316 research). **No committed #316 verdict report exists** (`docs/benchmarks/2026-08-hybrid-latency.md`
   never landed) — the band was never certified on any machine, and **no real-model measurement
   has ever met it**, including the per-encode-2×-faster MiniLM control (418–464ms on the
   contended laptop vs bge 344.6–460ms).
3. **Re-deriving pre-measurement decides the tenant contract on noise**: every "never met" datum
   is confounded with the contended 2-core laptop. The clean deployment-class measurement IS the
   pre-committed trigger — if the **control** also exceeds 300ms on a clean deployment-class box,
   that is evidence-backed grounds for a #316 scoping revision + product sign-off (the band is
   structurally unachievable on this class); if bge alone misses it while control meets it, the
   swap-specific delta governs (investigate/revert per the decision table). Re-derivation without
   the measurement is premature and discipline-breaking.
4. **The verdict must carry the band's history as interpretation context** (per #316's own
   framing: evaluate the target "for correctness AND achievability"), plus the #316 taxonomy's
   non-binary bands (≤300 achieved / 300–500 cap-dominated / >500 tail / >30% capped →
   inconclusive) — a "cap-dominated" verdict is a category with its own decision, not a pass/fail.

## Raw Notes (sources)

- Fly shared-CPU quota mechanics: fly.io/docs/machines/cpu-performance (5ms/80ms per vCPU,
  shared across vCPUs, throttled after quota; burst while balance holds).
- Fly co-tenancy ~16 users + AMD EPYC/KVM: community.fly.io/t/fly-vms-specs/12602/2.
- FalkorDB Cloud managed + TLS: docs.falkordb.com/cloud; self-hosted benchmark ~21ms p50/46ms p95:
  benchmark.falkordb.com (Intel, 20 clients).
- SLO practice: treeifyai.com (open-loop load, steady-state/soak); genaiconsulting.services
  (p50/p95 by query type, shadow/canary); SAGE production-RAG (arXiv 2608.08237v1, 5s-p95 SLO,
  95% compliance); stage budgets (embed 50–100 / retrieve 50–200 / rerank 100–300ms): unrag.dev.
- In-repo: docs/ops/deploy-embeddings-verification.md, docs/ops/669-post-flip-verification.md,
  docs/adr/ADR-009-embedder-selection.md (Status, Evidence Summary, tenant checklist),
  docs/scoping/2026-08-13-316-vector-benchmark-scoping.md (band origin, "dominated by
  non-strategy overhead", competitor refs), docs/research/2026-08-17-1349-embedder-selection.md
  (candidate pool, encode ratio ~1.7–2×), fly.toml (#545 OOM history), entrypoint.sh (FATAL
  guard), Dockerfile.hosted (bake/offline/shipments), benchmarks/README.md + run_report.py
  (verdict bands, provenance, --model/--load-timeout, Docker parity env).
