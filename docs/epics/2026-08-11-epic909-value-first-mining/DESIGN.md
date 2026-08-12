---
title: "DESIGN — Value-first mining system (epic #909) — the canonical design"
type: design
domain: engineering
doc_status: approved-as-design
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
supersedes: the exploratory docs/drafts/2026-08-09-* (this is the synthesis; those are the lineage)
---

# Value-first Mining System — Canonical Design

> The single authoritative design for epic #909. Synthesizes the requirements (R1-R9),
> the architecture decisions, the endpoint contract, the evaluation contract, and the
> build order. The Plan stage and implementation start from THIS document.

## 1. The model

**Four-node provenance chain (owner-confirmed, ontology-grounded):**

```text
Event (agentSession) ──produces──▶ Document (transcript: summary, story-arc, sessionId)
                                        ▲
Source (provenance bridge: sourceKind,   │  references
       credibilityTier, url, contentHash)─┘
      ▲
      └── extractedFrom ◀── Points (decisions/claims; operators IMPL/NAND,
                            MITIGATES edge-targeted — never point-targeted)
```

- **The conversation = a Document** (`documentKind: transcript`, summary/arc/sessionId — fields exist).
- **Source = the bridge** (provenance, NOT content) — per the owner's ontology reading.
- **Points = the epistemic layer** (beliefs; evidence lives in Sources, never as Points).
- **NAND semantics:** bidirectional default for API users (#807); `unidirectional` = agent-declared directed attack (no back-pressure). **Extraction-emitted NANDs are `unidirectional` by default** (new-claim-attacks-existing; surfacing depends on it), with `bidirectional` ONLY for the rare explicit mutual-restatement case (the conversation itself declares mutual exclusivity — research addendum §1).

**Capture architecture — Local Intelligence / Remote Graph:**

- Extraction runs on the user's machine with THEIR key + model (BYOK is THE default; we provide the process).
- The hosted product receives **derived commits only** (no raw conversation, no user's key).
- Opt-ins (NOT default): raw-transcript upload, managed-key extraction (in-housing later).
- Privacy posture: "we store derived knowledge + supporting quotes (≤200 chars, secret-scanned), never the full conversation."

## 2. The extraction pipeline (local, R1-R9 as behavior)

```text
S0  Deterministic preprocessing — tool/boilerplate filter (tool results = 53% of agent
    traffic; the biggest precision lever); segmentation into EDUs (atomic units).
S1  Value gate (cheap model) — keep/drop per the value brief (pack vocab); extract-
    nothing first-class; keep-ratio >40% → fail-closed to empty + alarm.
S2  Classification (two-axis) — decisions (commissive) vs events (past-perfective) vs
    claims (stative); "did/fixed/shipped" = event; "should" = recommendation (not
    decision); atomicity via EDU propositionize-before-classify (compound → split).
S3  Relations — IMPL (support) / NAND (attack, unidirectional by default when
    emitted; bidirectional only for explicit mutual-restatement) / MITIGATES
    (edge-relevance reduction, bias 0.10-0.50). Deep-miss convention:
    EXTRACT SUPPORT EDGES FIRST, then mitigations attach. Canonical: X "cheap" IMPL
    Option A; Z "we can raise the price" MITIGATES [X→A]; Y "not price-sensitive" IMPL Z.
S4  (DEFERRED) warrants/implicit premises — dangerous, research-grade; user-opted toggle later.
S5  Grounding/resolution — entity frequency gate; claim dedup (content-hash);
    supersede via supersede_point (CORRECTS edge + outdated flag + edge transfer;
    never hard-delete); process decisions (R3) → drop-with-log (work-item routing later).
S6  Derived-commit serializer — the payload for POST /v1/sessions/commit.
```

**Enforcement (R6 — soft, never frustrates agents):** WARN (accept + flag near-misses) /
RETRY once (aliases, shape; ≤5/session) / BLOCK (4 hazards: persistent kind-mint,
entity-type mismatch, missing provenance, malformed stream). Block-rate >15% → fail-closed.

## 3. The session-commit endpoint

`POST /v1/sessions/commit` (`tt_` auth). Derived payload — no raw text. Two-level idempotency
(content-addressed `client_commit_id` replay — duplicate only for fully-written commits,
zero writes, zero write-ops → zero writes; re-capture → MERGE by deterministic `pt_<sha>`;
supersede_point on changed content, `reason: REVISES` is the semantic label). Cumulative
per-session budget: soft 15 / hard 25 / ceiling 50 (overflow → hold queue, never dropped;
held re-submission re-checks the 50 ceiling only). Dual-counter metering (write_ops billed
unit unchanged — non-duplicate commits only; nodes_written cost driver). Layer-1
deterministic schema validation (retry once → 422 with reasons). Telemetry block
(extractor version, keep-ratio, counts, buckets — no conversation content). **The quota
fix ships with it** (`_count_resource` sessions branch + is_episodic exemption).
**MITIGATES is a first-class payload op_type** (edge-targeted → server-side
mitigate_operator semantics; REPHRASE = dedup label only).

## 4. The evaluation contract (stochastic system — two layers)

- **Layer 1 (deterministic, CI-blocking):** schema conformance, kind ∈ closed vocab,
  source_ref present, referential integrity. Binary pass/fail.
- **Layer 2 (statistical WATCH-GATES, not powered tests):** layer-correct ≥0.90,
  atomicity ≥0.85, kind-correctness ≥0.90, citation-correctness ≥0.90, mitigation recall
  ≥0.75 (measured 29% → 100% through the loop), empty-rate 20-40%. Bands: pass ≥ target
  on N≥30; fail < block on N≥12; between = watch. N≈109 for true 0.80-vs-0.90 separation
  (out of gold-set scope — the live judge is the power source post-launch).
- **The feedback loop is the calibration mechanism:** system generates → owner reviews →
  requirements update → regenerate, 2-3 iterations, until consistent. Validated on
  window #1 (mitigation coverage 0 → 29 → 100%, zero over-extraction).

## 5. Build order (dependency-ordered, gate first)

1. 2-window rubric validation + minimal tooling (THE GATE — window #1 converged; window #2 to broaden)
2. Quota fix + budget constants
3. Ontology amendments (agentSession, Document summary fields, capturedAt, NAND policy doc)
4. Pack manifest v3 (kindDefs, chains, extractable relations; domain_loader unification)
5. Session-commit endpoint (schema, idempotency, budget, metering, telemetry)
6. Local value-first extractor (S0-S3, S5; R1-R9; NAND policy; atomicity)
7. SDK commit producer + extension rework (local-extract → derived POST)
8. Evaluation: metrics.py + gold set + thresholds reconciliation + CI
9. Privacy (secret-scan, wording)

## 6. Open items carried forward (small)

- Window #2 of the validation (different session type).
- Source-vs-Document confirmed (Document = content, Source = bridge) — no action needed.
- Ontology amendments registration (item 3).
- Managed-key pricing — deferred entirely (not in v1).
- PRs #854 (design drafts home) and #870 (product-success eval) to close/decide.

## 7. References

Requirements: docs/drafts/2026-08-09-mining-system-requirements.md (R1-R9, v4)
Scope: docs/epics/2026-08-11-epic909-value-first-mining/scope.md
Research: docs/epics/2026-08-11-epic909-value-first-mining/*.md + docs/research/2026-08-11-classification-cues-*.md
Loop artifacts: docs/drafts/2026-08-09-probe-extraction-window1{,-v2,-v3}.md + mitigation-audit-window1.md
Align: docs/drafts/2026-08-11-epic909-align-decision.md
Handoff: docs/HANDOFF-2026-08-11.md
