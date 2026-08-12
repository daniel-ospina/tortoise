---
title: "Scope — Epic #909: Value-first mining system (v1)"
type: scope
domain: engineering
doc_status: draft
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
---

# Scope — Value-first Mining System (v1)

> **Hard gate (from align decision):** the 2-window rubric validation is deliverable #1.
> No extraction implementation proceeds until the rubric reproduces on the owner's real
> workload. This is the falsification gate for the whole premise.

## 1. Scope Boundaries

### In Scope (the v1 slice — everything that produces or validates derived commits)

1. **Pack manifest v3 + mapping** (R6) — kindDefs (descriptions/synonyms/examples/nearMisses),
   chains (useCase→feature→userJourney→workflow→requirement→architecture), extractable
   relations, per-pack extraction config; domain_loader unification (kill the dead code
   path — vocabulary actually reaches the LLM prompt); pack content updates
   (product-strategy gains `requirement` + `architecture`; chain encoded).
2. **The value-first extraction pipeline (local, BYOK)** — S0 tool/boilerplate filtering
   (the 53% lever); S1 value gate (keep/drop, extract-nothing first-class, keep-ratio
   fail-closed >40%); S2 classification (two-axis: decision/event/claim per the Searle
   model; atomicity via EDU propositionize-before-classify); S3 relations with the NAND
   direction policy (extraction-emitted NANDs → `unidirectional`); S5 grounding/resolution
   (entity frequency gate, claim dedup, REVISES/supersede); S6 derived-commit serializer.
   **R3 (process decisions): v1 behavior = classify, drop with logged reason (violation
   event); work-item routing deferred until an integration exists.** **S4 (warrants)
   explicitly OUT.** **Graph-state conditioning read surface: deferred in v1 — the
   server-side MERGE reconciliation (S5 + commit endpoint) suffices; the conditioning
   read API comes with v1.1 cross-session work.**
3. **Session-commit endpoint** — POST /v1/sessions/commit (derived payload schema, no raw
   text); Layer-1 deterministic validation (retry once → fail); two-level idempotency
   (content-addressed client_commit_id + MERGE reconciliation, never hard-delete);
   cumulative per-session budget (soft 15 / hard 25 / ceiling 50, held→hold-queue);
   dual-counter metering (write_ops billed unit unchanged + nodes_written cost driver);
   telemetry block (extractor version, keep-ratio, counts, buckets — no conversation
   content).
4. **Quota fix (P0, ships with the endpoint)** — `_count_resource("sessions")` counts
   Session nodes (not all nodes); `is_episodic` exemption for Session/Event/Source;
   MAX_VALUE_POINTS_PER_SESSION constants land in quota.py.
5. **SDK local commit producer** — reuses the local extraction; adds the derived-commit
   serializer + client_commit_id; the capture extension's cloud path switches to
   local-extract → POST derived (raw-conversation upload becomes the opt-in; managed-key
   stays server-side, not default).
6. **The evaluation harness (the gate)** — deliverable #1: **2-window rubric validation**
   (owner + frontier judge, κ + nothing-agreement); then the gold set (30 windows, owner
   adjudicates, judge agreement κ≥0.60); metrics.py (fuzzy matching, per-class rates, ECE);
   thresholds.yaml reconciliation (ONE file — the R8 layer-gates + the existing A1-A21,
   band semantics: watch-gates, not powered tests); CI eval workflow.
7. **Ontology amendments** — `agentSession` in §4.5; **the four-node model confirmed by
   the ontology (owner decision 2026-08-11): Event (agentSession) → produces → Document
   (transcript content: summary, story-arc, sessionId — fields already exist) ← references
   ← Source (provenance bridge: sourceKind, tier, url, contentHash) ← extractedFrom ←
   Points.** Source is the bridge, NOT the content holder; the conversation is a
   `documentKind: transcript` Document. `capturedAt` + content-addressed Event ID (§4.5);
   NAND direction policy documented in the pipeline spec (new-claim → unidirectional).
8. **Privacy guarantees** — derived commit carries no raw conversation; quotes ≤200 chars
   with secret-scan (credential-like patterns dropped); "we store derived knowledge and
   supporting quotes, never the full conversation" as the public wording; the raw-upload
   path default-off.

### Out of Scope (deferred — explicitly, with destinations)

- **Warrant/implicit-premise generation** — deferred (user-opted toggle later; research-grade,
  invented reasoning).
- **Full argument trees / fallacy detection** — research (67 F1 ceiling), deferred.
- **Cross-session consolidation** — v1.1 (needs resolution first).
- **Procedural layer** — v2.
- **Managed-key extraction + its pricing** — NOT in v1 at all (owner decision 2026-08-11: BYOK is THE default — their agent does the extraction, we provide the process; in-housing comes later). Deferred entirely.
- **Raw-transcript upload feature** — opt-in, later.
- **Entity resolution at scale** — v1.1.
- **Dashboard surfacing / memory-health dashboard** — separate work, gated on P9/A8.
- **Correction/reject surface** ('user says this is wrong') — v1.1 (hold queue is the v1 escape hatch).
- **Ledger/explanation endpoint** (why does the graph believe X) — v1.1.
- **Belief-history read surface** (was X believed at t1) — v1.1.
- **participatesIn (n-ary roles)** — v1 substitutes participants as Event properties (no new edge producer).
- **REPHRASE as a graph operator** — v1 uses it as a dedup label only (no ontology operator).
- **The 2-session "did it matter" experiment + ablation instrumentation** — post-v1
  (requires agent-loop surfacing).

### Boundary Rationale

v1 = **the pipeline that produces derived commits + the endpoint that receives them + the
eval that validates both.** Nothing that doesn't produce, receive, or validate a derived
commit. The gold-first gate is the boundary's spine: the eval harness ships with (before)
the extraction it validates. Deferrals follow the research: warrants (dangerous/unproven),
argument trees (research ceiling), consolidation (needs resolution), surfacing (needs a
consolidated graph + the P9/A8 gate).

## 2. Customer Value Map

| Scoped Capability | User-Visible Value |
|---|---|
| Pack v3 mapping | Devs get extraction that types entities correctly against their domain pack — "value-first extraction" is a feature, not a mystery |
| Value gate (extract-nothing) | Devs' graphs stay clean — noise never enters, empty sessions are normal |
| Decision/event/claim classification | Devs' memory contains decisions and claims, not a dump of "we fixed X" events |
| Local extraction (BYOK) | Devs use their own model/key; conversations never leave their machine |
| Session-commit endpoint | Devs' local extraction persists to their shared team graph |
| Quota fix + budget | Capture doesn't lock devs out after ~40 sessions; budget overflow is held, never dropped |
| NAND direction policy | Contradiction surfacing works — "you're now contradicting your own decision" fires |
| Evaluation harness + gold set | We (and devs) know extraction works before it's promised |
| Privacy guarantees | Devs can trust "we never see your full conversations" |

## 3. Complexity Ratings

| Axis | Rating | Rationale |
|---|---|---|
| UX | low | Dev-facing (CLI/extension config + API); no consumer UI in v1 |
| Architecture | high | New extraction pipeline + endpoint + SDK producer + enforcement + metering |
| Ontology | high | Pack schema redesign (manifest v3) + 5 ontology amendments + NAND policy |
| Research | high | Classification cues (resolved in research), thresholds (watch-gate bands), pack mapping (resolved) |
| Content | standard | Gold set + eval docs + privacy wording |

## 4. High-Level E2E Test Cases

| # | E2E | Scenarios (high level) |
|---|---|---|
| E2E-1 | **2-window rubric validation** (DELIVERABLE #1 — gate) | Owner labels 2 real windows; frontier judge labels same; κ ≥0.60 + nothing-verdict agreement; κ<0.50 → rubric revision, not progress |
| E2E-2 | **BYOK capture → derived commit → graph** | Session ends locally → value-first extraction with user's key → POST /v1/sessions/commit → points/entities/operators in team graph; raw conversation never uploaded |
| E2E-3 | **Layer-correct classification + R3 routing** | Real session windows: decisions vs events vs claims classified correctly (per-class rate, not blended); process decisions dropped with logged reason (no graph pollution) |
| E2E-4 | **Atomicity** | Compound decision ("A AND B AND C") splits into 3 atomic points |
| E2E-5 | **Source citation** | Every point has a resolvable source_ref → Source node exists; sources indexed (R4/R7); "where did this come from" always answerable |
| E2E-6 | **NAND direction policy** | New-claim-attacks-existing → unidirectional stored; P9/A8 contested-variance test green |
| E2E-7 | **Idempotent re-capture + budget + quota + Layer-1** | Re-capture same session → merge, zero double-charge; cumulative budget 15/25/50 (held → queue, >50 → 402); sessions quota counts Session nodes (no 402 at ~40 captures); malformed commit → retry once → 422 with field reasons |
| E2E-8 | **Extract-nothing** | Empty windows valid end-to-end; keep-ratio >40% → fail-closed to empty + alarm |
| E2E-9 | **Pack enforcement (soft)** | Out-of-vocab entity → warn/hold (not hard-block); agent proceeds un-frustrated; near-miss retry once |
| E2E-10 | **Privacy** | Derived commit contains no raw transcript; quotes ≤200 chars; secret-scan drops credential patterns |

## 5. Decomposition sketch (for the Decompose stage — dependency-ordered, gate first)

1. **2-window rubric validation + minimal tooling** (judge harness + kappa script) — THE GATE; everything below proceeds only if it passes
2. Quota fix + budget constants (unblocks the endpoint)
3. Ontology amendments (agentSession, Source fields, capturedAt, NAND policy doc) — needed by the endpoint payload (Source fields) + extractor
4. Pack manifest v3 (schema + validation + template + pack content updates + domain_loader unification)
5. Session-commit endpoint (schema + idempotency + budget + metering + telemetry)
6. Local value-first extractor (S0-S3, S5; NAND policy; atomicity; classification; R3 drop-with-log)
7. SDK commit producer + extension rework (local-extract → derived POST; raw-upload opt-in)
8. Evaluation: metrics.py + gold set (30 windows) + thresholds.yaml reconciliation + CI
9. Privacy (secret-scan, wording)

Dependencies: 3 → 5 (Source fields in payload), 4 → 6 (pack vocab reaches the extractor), 2 → 5 (quota), 6 → 7 (extractor feeds the producer).

## 6. Open decisions (carried to the human gate)

1. **Source-vs-Document** for the conversation content node (Source gains summary/arc fields vs Document+Source pair) — ontology decision.
2. **Enforcement exact levels** per the R6 taxonomy (warn/retry/block classes + thresholds) — final numbers at plan.
3. **The 2-window validation schedule** — owner availability; it's the gate, nothing proceeds until it's green.
4. **Managed-key path**: excluded from v1 enablement (BYOK first) — confirm.
