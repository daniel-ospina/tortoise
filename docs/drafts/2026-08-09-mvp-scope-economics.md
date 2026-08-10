---
title: "DRAFT — MVP Scope, Constraint Strategy + Economics (exploration, NOT approved design)"
type: draft
domain: engineering
doc_status: draft
created: 2026-08-09
ownedBy: epistemic-team
governingAgreement: "#753 (review gate)"
---

> ⚠️ **EXPLORATION DRAFT.** Filed per product owner direction: explore and unpack, do NOT implement from this. See issue #753 for the review gate. **Pricing model note (product owner, 2026-08-09): tiers are feature baselines; usage is metered separately on top; NO capture caps. Cost-model integrity is the requirement — per-usage cost must stay below per-usage price at scale.**

# MVP Scope, Constraint Strategy + Economics

## MVP scope (proposal for review)

- **Launch pack: dev** (founder dogfood; agent-native domain). Other packs load as ontologies but are not extraction targets in v1.
- **Point kinds: exactly 2 extraction kinds** — `decision` (volitional; no confidence floor — decisions are made, not believed) and `claim` (evidential; floor-gated mean≥0.6 + variance≤0.04, else draft). `question` deferred (value is in its resolution). Strategy-ish kinds collapse into claim/decision in v1.
- **Entity kinds the extractor may anchor to**: 5 objects (dev:issue, epic, api, code, deployment) + 4 subjects (organization, team, role, naturalPerson).
- **Edge types (6)**: IMPL (Point→Point, +label), NAND (Point→Point, cue-gated), aboutObject/aboutSubject (the two-layer bridge), extractedFrom (mandatory provenance), CORRECTS (primitive only, never automatic). EVIDENCE as a separate type rejected (duplicates extractedFrom+IMPL).
- **Explicitly OUT of v1**: full argument trees, fallacy detection, cross-session consolidation, entity resolution at scale, procedural layer, question lifecycle, connectors (one write path: capture → value-first extraction). **Per-turn Point materialization REJECTED (product owner, 2026-08-09): the raw conversation is one Source (summary + story arc), derived points connect via extractedFrom. No turn nodes.**

## Constraint regime (guardrails)

| Guardrail | Value | Enforcement |
|---|---|---|
| Per-session node budget | Hard 25, soft target 15 | Fail-closed at write (quota.py pattern); over-budget → hold queue `budget_overflow`, never dropped |
| Edge ratio | ≤2 edges per node | Validator |
| Per-pack kinds | ≤12 objectKinds / ≤6 pointKinds / ≤8 relations | Manifest-load hard fail |
| Regex fallback budget | Same 25/session | Critical: self-host without LLM must respect the same budget |
| Schema modes | `strict` (hosted) / `permissive` (self-host) | SDK create_* + extractor + MCP |
| Out-of-schema | Hold queue, never drop; **3-strike auto-promotion** → pack-extension proposal | Governance loop |
| Confidence floor | claim → live iff mean≥0.6 AND variance≤0.04; else draft (inert) | Post-EP gate, lazy |

Sanity: single T2 source → Beta(3,1) mean 0.75 var 0.038 ✓ live. Single T3 → mean 0.67 var 0.056 → draft. Most single-session claims start draft — correct (they need corroboration; calibration debt surfaced honestly).

## Caps/pricing (REVISED per product owner)

**No capture caps.** Tiers = feature baselines; usage metered separately. What matters: **cost-model integrity** with value-first volumes.

- Heavy user (13 sessions/day) at ~33 nodes/session incl. scaffolding: **~13k nodes/mo ≈ 13MB ≈ $1/mo storage** (vs 10M nodes ≈ $730/mo today — ~775× collapse). LLM ~$1–2/mo per heavy user (p50 $0.028–0.032/session; p95 $0.092; heaviest $11–36/mo worst-case).
- Node caps (10k/25k/100k/600k) now act as honest growth levers: free fills ~2 weeks full-tilt, solo ~2 months, pro ~8 months.
- **Pricing tension to resolve deliberately (flag for review)**: at the current $5/10k write-op overage, one capture ≈ 39–55 write ops ≈ $0.02–0.0275 charged vs ~$0.03 marginal cost → **near break-even**. Capture needs either its own usage line or a write-op price that reflects LLM cost. Decide with real telemetry; the important fact is the cost side is now tiny and bounded.

**Instrument from day 1** (usage-metering infra exists): nodes_per_session (p50/p90/p95), extract_nothing_rate (target 10–40%), dedup_rate, cost_per_team (storage + LLM), hold_queue_rate (<10%), draft_vs_live_rate (<30%), sessions_per_user, point_reuse_rate (the value north-star), operator_ratio, budget_overflow_rate, extraction latency, cap-hit rate, warrant usage, cache-hit rate.

## Sequencing (proposal)

- **v1**: capture + value-first extraction (dev pack, 2 kinds, strict mode, hold queue, confidence gating, extraction-mode env) + NAND operator fix (directed) + quota episodic-exemption + the 12 instruments + golden-set harness.
- **v1.1**: entity resolution → cross-session consolidation → finer kinds + question lifecycle → connectors; confidence calibration (ECE ≤0.1).
- **v2**: procedural layer; argument trees on the consolidated graph; fallacy/weakness detection; NAND-camp visualization; contested-claim UX.
- Dependency logic: consolidation needs resolution; resolution needs v1's correct provenance anchors; fine kinds need correction machinery; argument trees need a consolidated graph; procedural needs connectors + populated state.

## Economics guardrails (summary — full doc: docs/drafts/2026-08-09-economics-guardrails.md)

- F1 (RESOLVED by ruling): no per-turn nodes — the conversation is one Source. Per-session node total ≈ 1 Source + ~10-25 knowledge ≈ ~12-26; a heavy user (13/day) ≈ ~500/mo — caps are trivially safe (no episodic exemption needed). Remaining latent bug: `_count_resource("sessions")` counts all nodes, not Session nodes (still to fix).
- Latency SLOs: capture p95 ≤1.5s; extraction p95 ≤5min (async, 4-tier degradation).
- Fail-closed matrix: budget enforced on ALL write paths (regex fallback, self-hosted), validate-then-write, TOCTOU concurrency.
- Launch go/no-go: measured cost ≤$0.05/session; blended GM ≥25%; cap fit ≥90% of segment with ≥20% headroom; enforcement + instrumentation + overage honesty.
