---
title: "UX research — #1349: Hosted-vs-local embedding for Tortoise"
type: research
domain: capability
doc_status: live
created: 2026-08-17
issue: 1349
ownedBy: epistemic-team
---

# UX Research — #1349: Hosted-vs-Local Embedding for Tortoise

> Task 7 deliverable for `docs/plans/2026-08-17-1349-embedder-selection.md`. Findings
> draw on the Phase 1.5 research brief (`docs/research/2026-08-17-1349-embedder-selection.md`,
> Axis 3) and the scoping's decision rule. This document survives regardless of gate
> outcome — it is the launch-comms source for the hosted-vs-local answer and the
> tenant-visible-change surface, and it feeds the ADR-009 product-decision sections
> (PR1 deliverable, ships before any swap).

## The user's question

> **"Can hosted tortoise customers use their local machine for embedding?"**

Short answer, stated up front:

- **Hosted Tortoise (default tier): NO.** Embeddings run **server-side** — the model
  is baked into the hosted image and every query/document encode happens inside the
  service. This is the industry hosted answer (see precedent below). Customers
  control **model choice via configuration**, not compute location.
- **Self-hosted Tortoise: YES.** Self-hosted installs do **not** bake a model; the
  model downloads lazily on first use and every encode runs on the operator's own
  machine. This is the local story.
- **Encrypted tier (#265, pending-merge design): client-side as a special case.**
  The #265 encrypted-tier design carves out client-side embedding for
  `encryptionVersion>=1` teams (client-computed 384-dim vectors shipped into the
  shared index) — a deliberate exception to the server-side default, scoped to
  encrypted teams only.

The remaining open question is **only** the hosted default tier, and it is routed
to the user as a product call before the evidence burn (T8 stage-0) — a
customer-local verdict would void the burn and must not be discovered post-burn.

## Industry precedent: server-side is the hosted norm

Every major hosted memory product runs embedding **inside the service**; customer
control is model choice via config, **not** customer-local compute:

| Product | Hosted tier | Local embedding |
|---|---|---|
| **mem0** | Server-side baked — "We serve the model ourselves"; every fact + entity name embedded server-side on each add; queries embedded on each search; customers authenticate via API key; no customer-side model runtime | OSS/self-hosted tier defaults to OpenAI `text-embedding-3-small` via env — **still server-side in the customer's own deployment** (local = opt-in) |
| **Zep** | Defaults OpenAI Ada-002; **removed its bundled local embedding service in CE** (trend away from customer-local) | BYO-vectors supported manually only |
| **LangMem** | Server-side OpenAI default | No customer-local compute option |
| **Letta** | Server-side embedding | No customer-local compute option |

**Tortoise's current bake already matches this answer and is MORE local than mem0's
default** — the hosted image bakes the model at build time and encodes with zero
external embedding API calls (no data leaves the service for embedding).

**Customer-local-over-API/MCP pitfalls** (why the hosted tier stays server-side):

- Payload cost: 384-dim float32 ≈ 1.5KB/point across the API/MCP boundary.
- Model download: MiniLM ≈ 90MB; a Qwen3-class model ≈ 1.2GB+.
- Torch runtime ≈ 2GB+ on the customer side.
- HF download blocking on corporate networks.
- Version skew → mixed vector spaces in the same graph (index-consistency hazard).
- Per-query latency on the customer's connection; loses server-side caching/parallelism.
- E2E-8 ≤300ms p95 is only controllable server-side.

## Product decision (ADR-009): local embedding offered to hosted tenants — NO

> Recorded in `docs/adr/ADR-009-embedder-selection.md` (this is the launch-comms
> source for that decision).

**Decision: local embedding is NOT offered to hosted tenants.**

**Rationale:** (1) **industry precedent** — mem0/Zep/LangMem/Letta all run embedding
inside the service stack; the hosted tier is server-side baked, and the self-hosted
story is where "local" lives; (2) **server-side default** — the baked model already
matches the industry answer and is strictly more local than mem0's default (no
external embedding API call); customer control = model choice via config; (3)
**self-hosted = the local story** — operators who need on-machine embedding run
self-hosted Tortoise, where the model downloads and encodes locally by design.

## Tenant-visible changes at launch (ADR-009 checklist)

If the gate passes and the swap lands (PR2), this is the complete tenant-visible
surface at launch:

- [ ] **API/SDK surface unchanged** — no new parameters, no schema change; embedding
      remains an internal implementation detail of the service.
- [ ] **Stored vectors re-embedded server-side during a maintenance window** — the
      backfill re-embed (`--force-re-embed`) runs on the service side across the
      6-label surface (Point/Subject/Object/Document/Event/Source); tenants take no
      action. During the batched window, mixed old/new-model vectors exist:
      dedup suggestions and review-connections cosine matching degrade until the
      re-embed completes (operator guidance: pause ingestion during the window or
      explicitly accept degraded dedup suggestions).
- [ ] **Retrieval quality direction per gate evidence** — the pre-registered gate
      (co-primary turn_recall@10 + nDCG@10, BH-FDR q=0.10 over m=6) determines the
      direction; the launch message reflects the measured verdict, never a promise.
- [ ] **Latency envelope ≤300ms p95** — E2E-8 verdict band holds post-swap
      (pre-swap E2E-8-with-candidate ≤300ms is a HARD PR2 precondition; per-encode
      slowdown ≈ +3-5ms, absorbed by the band).
- [ ] **No tenant action** — no re-auth, no client change, no re-download, no
      settings change.
- [ ] **Self-host one-time download** — self-hosted operators pull the new model
      once on first use after upgrading (lazy first-use download; see below).

## Self-hosted: the local story (no-bake, lazy download, failure behavior)

Self-hosted installs (Dockerfile.selfhost) are the **local** answer:

- **No bake.** Unlike Dockerfile.hosted (which bakes the model at build time and
  runs `HF_HUB_OFFLINE=1`), Dockerfile.selfhost has **no model bake** — the model
  downloads **on the operator's own Docker, on demand**.
- **Lazy first-USE, not first-boot.** The model downloads on the **first
  `EmbeddingModel.get()`** (first use of embeddings — search/ingest), not at boot.
  A boot-time download would replicate the **Fly-sandboxed-network hang** documented
  at Dockerfile.hosted:53 (the runtime download in 69aefae hung in Fly's sandboxed
  network and was reverted in 0da50a0 — hosted bakes precisely to avoid this class
  of failure). Lazy first-use keeps boot fast and offline-safe.
- **Failure behavior.** If the first-use download fails (blocked network, HF
  outage), behavior is a **documented silent degrade** (embeddings unavailable →
  search falls back to FTS+structural RRF; `EmbeddingModel.get()` returns None and
  retries on the next call, with a 60s negative-cache cooldown per embeddings.py)
  **or fail-fast FATAL** in the hosted image (entrypoint missing-cache check) — the
  self-hosted path degrades silently with clear logs; the hosted path fails fast.
- **Pin-old-image-tag escape for blocked networks.** Operators on networks that
  cannot reach HuggingFace can **pin the previous image tag** (whose model cache is
  already baked into the image layers) instead of attempting the download — the
  documented escape hatch; MiniLM remains the documented fallback model.

## Deployment feasibility on the 2GB VM

- **All candidates are CPU-feasible on the 2GB VM class** — the entire pool is ≤34M params (22.7M–33.4M): all-MiniLM-L6-v2 (22.7M), snowflake-arctic-embed-xs (22M), snowflake-arctic-embed-s (33M), bge-small-en-v1.5 (33.4M). The 768/1024-dim
  upgrade path (nomic-v1.5, Qwen3-Embedding-0.6B ≈ 1.2GB) is explicitly **not** a
  benchmark candidate — it collides with the #265 384-dim pin + 2GB VM budget.
- **Latency expectations.** The query-path encode lives at sdk.py:8718 inside the
  E2E-8 verdict band (≤300ms p95). bge-small / arctic-s encode ≈ **1.7-2× slower
  per encode than MiniLM** (sourced for bge-small; arctic-s is the same size
  class, arctic-xs ≈ MiniLM's 22.7M) ≈ **+3-5ms absolute** on a sub-5ms encode —
  **immaterial vs the 300ms band** (~30-60× headroom). Corpus re-embed is a
  one-time batch cost.
- **Cold-start.** The pre-warm is **model-agnostic** — hosted_api.py `_lifespan`
  spawns a non-blocking daemon-thread pre-warm calling `EmbeddingModel.get(
  load_timeout=300.0)` with no model literal, so a model swap does not change the
  cold-start mechanism; uvicorn binds immediately so /health passes on cold start,
  and a missed pre-warm window retries on the next call (FTS+structural fallback
  meanwhile).

## Bottom line

Hosted Tortoise = **server-side baked embedding** (industry precedent, controllable
latency, no mixed vector spaces). Self-hosted Tortoise = **the local story** (lazy
first-use download, on-machine encode). The #265 encrypted tier = the **client-side
exception**, scoped to encrypted teams. The hosted default tier offers **no**
customer-local embedding; the product call at the T7→T8 gate confirms this default
before the evidence burn.
