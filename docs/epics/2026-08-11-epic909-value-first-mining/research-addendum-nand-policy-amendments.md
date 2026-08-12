---
title: "Research addendum — NAND direction policy + ontology amendments (epic #909)"
type: research
domain: engineering
doc_status: research
created: 2026-08-11
governingAgreement: "#909, #753 (condition), #807"
---

> **Research-verifier correction (Research-Needed #4 — was 0% covered).** Absorbs the #753
> conditions: the extractor's NAND direction policy and the agentSession/Source ontology
> amendments.

## 1. NAND direction policy for extraction (the #753 condition)

**Context (from #753/#807, measured):** the mutual-contradiction coupling is weak in the EP
engine (+0.0024 — the contested detector, variance>0.04, cannot fire on genuine mutual
contradictions). Directed NAND (explicit `unidirectional`: attacker's truth penalizes the
target, no back-pressure) is the measured-correct mechanism. The surfacing feature
(contradiction detection) gates on the extractor writing the right direction.

**Policy (the extractor's rule — goes in the pipeline's S3):**
- **New-claim-attacks-existing-claim → `unidirectional`** (directed): "you now claim ¬D
  against D" is an attack on an existing belief — the new claim attacks the old. This is
  the common, measured-correct case and the one that makes surfacing work.
- **Mutual restatement → `bidirectional`**: when both claims are asserted together as
  mutually exclusive (e.g., the conversation itself declares "A and B can't both be true"),
  bidirectional is correct.
- **Default for extraction-emitted NANDs: `unidirectional`** — extraction is always
  asserting something NEW against something EXISTING; mutual is the rare explicit case.
  (The SDK creation default stays `bidirectional` per #807 — that's for API users; the
  EXTRACTOR explicitly sets the direction per this policy.)
- The `nand_precision` metric (thresholds.yaml A11) measures this: an extracted NAND
  against a live high-confidence claim must be directed (else it's invisible to EP).

**Gate:** the P9/A8 property test (contested variance >0.04 on a balanced contradiction)
must be green before the surfacing feature is claimed. Scoped as part of the extraction
pipeline + eval acceptance.

## 2. Ontology amendments (registration, not new design)

- **`agentSession` eventKind** → register in ONTOLOGY §4.5 core eventKind vocabulary
  (currently code-wired — sdk AgentSession indexing/search — but unregistered).
- **Source `summary` / `narrative_arc` / `topics` fields** → register in §4.6 Source
  (the capture architecture's Source node carries summary + story arc; the ontology's
  Source table lacks these fields; Document has them — reconcile: either Source gains
  the fields or the conversation content node is a Document subclass with the Source
  carrying provenance. **Decision needed at scope** — see below.)
- **`capturedAt` (transaction time)** + content-addressed Event ID → §4.5 amendments
  (bi-temporal capture).
- **`participatesIn`** (n-ary participants with roles) — §3.6 is spec-only (no producer,
  #7884); v1 substitute: participants as properties on the Event or a simple
  `hasParticipant` edge; decide at scope.

**Open scope decision (flagged):** Source-vs-Document for the conversation content node —
the three-layer model says Source (summary+arc), but the ontology's summary/search fields
live on Document. Either (a) Source gains the fields (ontology amendment), or (b) the
conversation is a Document (content) + a Source (provenance) pair. The capture architecture
doc leans (a); the verifier flagged the field gap. This is a scope-stage decision with the
ontology.
