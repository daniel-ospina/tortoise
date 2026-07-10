# E017 Scope

**Date:** 2026-07-10
**Pipeline:** experiment-workflow → Stage 5

## Boundaries

### In Scope
- Single reference graph: Nexus Analytics business case (31 points, 12 operators)
- 10 documents totaling ~40 pages
- 4 batch delivery (2-3 docs per batch)
- 4 order variants
- 2 arms: Control (sequential memory) vs Tortoise (incremental graph)
- Model: deepseek/deepseek-chat via OpenRouter, T=0.3
- Metrics: node overlap, edge overlap, confidence stability, cross-doc recall

### Out of Scope
- Multi-model comparison (DeepSeek only)
- Real-time graph visualization or UX polish
- Human evaluation of answer quality
- Long-running multi-session agent (single session per trial)
- Production Tortoise integration — the graph in this experiment is a simplified prompt-based structure

## E2E Test Cases

### E2E-1: Reference Graph Completeness
**Given** the Nexus Analytics reference graph (31 points, 12 operators)
**When** a human analyst reviews the graph
**Then** the conclusion (PIVOT) should be reachable from the graph alone, without external knowledge, AND the alternative (PERSEVERE) should also appear plausible from a subset of points

### E2E-2: Document Fidelity
**Given** the 10 generated documents
**When** mapped back to the reference graph
**Then** every reference graph point should appear in at least one document, AND every cross-document operator should require information from 2+ documents to discover

### E2E-3: Single-Document Ambiguity
**Given** any single document from the set
**When** an agent reads only that document
**Then** the agent should NOT be able to confidently determine the correct answer (>90% agreement with reference) — each document alone is ambiguous

### E2E-4: Batch Structure Completeness
**Given** the 4 batches
**When** each batch is processed
**Then** each batch should contain evidence from at least 2 different domains, preventing domain-level anchoring

### E2E-5: Graph Stability Validation (Primary)
**Given** a validation run (1 trial, 4 variants, 2 arms)
**When** Tortoise processes all batches across all 4 order variants
**Then** Tortoise should produce the SAME final answer (PIVOT) in ≥3 of 4 variants AND Control should produce at least 2 different answers across variants OR show confidence variance >15%

### E2E-6: Cross-Document Discovery
**Given** the validation run
**When** comparing Tortoise and Control outputs
**Then** Tortoise should identify at least 2 operators requiring cross-document information, while Control may identify 0-1

### E2E-7: Not Trivially Obvious
**Given** the validation run
**When** both arms process all documents
**Then** at least one arm should NOT get 100% correct (unlike E016 v1-v3, v5-v6) — the task must discriminate
