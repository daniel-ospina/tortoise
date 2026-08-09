# Honcho (Plastic Labs) — Competitor Profile

**Date:** 2026-08-07
**Status:** Tier 1 — Watch closely (direct agent-memory overlap, pure-usage pricing, fast-moving)
**Backend:** reasoning-based memory (not graph) — "memory beyond RAG," per-user belief modeling

---

## Product

- **Positioning:** "AI agent memory beyond RAG — reasoning-based memory that actually understands users."
- **Model:** reasoning-tiered memory writes; `context()` retrieval free/unlimited; advanced reasoning queries priced per-query tier ($0.001–$0.50 depending on reasoning depth).
- **Self-host:** Honcho OSS is free (self-hosted); managed cloud is paid.
- **Relevance to us:** Honcho competes on *reasoning about memory* (per-user models) rather than *epistemic graph structure*. No belief propagation across claims, no operator/NAND structure, no team graph. Different paradigm — but the same buyer (developers wiring memory into agents).

## Pricing (verified 2026-08-07 via honcho.dev + plasticlabs.ai blog)

| Component | Price | Notes |
|---|---|---|
| Ingestion | **$2.00 per M tokens** | The write-side charge |
| Retrieval (`context()`) | **Free, unlimited** | Reads unmetered |
| Reasoning queries | $0.001–$0.50 per query | Tiered by reasoning depth (minimal → max) |
| Free credits | **$100 on signup** | Conversion hook — notable pattern |
| Billing model | Pure usage — no subscription | No tiers, no seats |

## Pricing-shape analysis vs Tortoise

- **Honcho meters tokens** because its cost driver is the LLM reasoning at write-time — we don't run LLM-at-write on the base path, so our cost driver is storage + write throughput (we meter write ops + graph size instead). We track our actual cost; Honcho passes through LLM cost.
- **Tokens→ops bridge** (≈300–500 tokens/write): Honcho ≈ $6–8 per 10k writes equiv. Our $5/10k overage sits just below it; our Pro base ($25/50K) is cheaper entry than Honcho's pure-usage at any consistent volume, but Honcho wins the tiny user via $100 free credits + no base.
- **Reads free in both** — aligns with the "meter the costly primitive" norm (Zep charges 0 credits for retrieval; we keep search/context free).

## What to watch

- $100-free-credits conversion pattern (candidate for our Free tier — optional).
- Honcho 3.x reasoning tiers — if reasoning-at-write becomes cheaper, it pressures our "no LLM-at-write" cost advantage.
- OSS community growth (self-host free) — same funnel strategy as ours.

## Sources

- https://honcho.dev/ (pricing)
- https://plasticlabs.ai/blog/posts/Honcho-3 (pricing details, $2/M tokens)
- https://honcho.dev/docs/v3/documentation/introduction/quickstart ($100 free credits)

*Last checked: 2026-08-07*
