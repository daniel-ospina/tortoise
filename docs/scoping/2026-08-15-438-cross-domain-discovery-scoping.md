# Scoping: Cross-Domain Connection Discovery (bring-your-own-agent path)

> **Issue:** #438 · **Status:** scoped (decisions locked 2026-08-15) · **Owner decisions:** issue comment 5287516028 (BYOA) + relationship note 5287269894 (#901 routing semantics)

## O/I/T (re-scoped 2026-08-15)

- **Objective:** Complete the cross-domain pipeline via **bring-your-own-agent**: expose unverified cross-lens candidates (lens pair, similarity, point context) via MCP/SDK; customers' agents confirm and write operators through the normal API. No server-side LLM verifier (product decision 08-13).
- **Indicators:**
  1. MCP tool + SDK method surface unverified candidates read-only.
  2. Candidate payload adopts #901 truth-vs-relevance routing semantics (object vs operator), neutral - deciding semantics is the customer agent's job.
  3. Discovery cost proportional to new data, not total graph; bounded per cycle.
  4. Close-check (Slice 0) formally retires the "close as realized" path.
- **Targets:** 2+ separately ingested research streams produce cross-lens candidates; candidates exposed with cost bound documented.

## Context

- Landed: #399/#650 embedding candidate engine (`tortoise/cross_lens.py`, `find_cross_lens_matches`, recall-only, never writes, never decides semantics); cue-word gate in `MockExtractor.multi_source`.
- Gap: production mining path (`ConversationMiner._make_extractor` -> `LLMExtractor`) has **no** candidate stage; `_last_candidates` unconsumed (extractor.py:776-800); no MCP/SDK surface; no cross-stream discovery.
- 08-13 product decision: bring-your-own-agent, no server-side verifier. 08-15 relationship note (to #901): verifier/candidate contract adopts #901 routing rule so both systems emit interchangeable edge shapes.

## Approach (slices)

- **Slice 0 - Close-check:** formal decision record evaluating #399+mining as realization -> retire the close path (documented, per owner decision).
- **Slice 1 - Candidate-exposure surface:** MCP tool `find_cross_lens_candidates` + SDK method `get_cross_lens_candidates` (read-only; lens pair, similarity score, point context, dedup vs existing operators).
- **Slice 2 - Payload contract:** single `routing: "truth"|"relevance"` field per #901; candidates kept off-graph (no new predicate registration); no op_type hint (deciding semantics is the agent's job).
- **Slice 3 - Cross-stream discovery:** over existing HNSW index; gated on registered `sourceKind` (any tier - decision D3); cost cap 200 candidates/cycle (decision D4).

## Locked decisions (2026-08-15)

| # | Question | Decision |
|---|---|---|
| D1 | Tool naming | `find_cross_lens_candidates` (MCP) / `get_cross_lens_candidates` (SDK) |
| D2 | Contract shape | Single `routing` field, neutral (no op_type hint) |
| D3 | Source-tier gate | Registered `sourceKind` of **any tier** (owner: more suggestions over filtering) |
| D4 | Cost cap | 200 candidates/cycle, hard cap (predictable; local pre-filter experiment filed to #909 - NOT here) |
| D5 | Dedup territory | Relation candidates only (near-duplicate pairs out of scope; #1161/#784 territory) |
| D6 | Order vs #901 | Proceed with #901 routing semantics verbatim now |
| D7 | Verifier placement | No in-repo verifier; customer agents confirm via this surface (no overlap with #901) |
| D8 | Existence masking | Empty results (no error) for nothing-to-see; errors only for auth failures |

## Complexity

- Standard (Architecture/Data); low Ontology/UX/Deps.

## Test plan

- `cross_lens.py` unchanged (regression); new MCP tool + SDK method contracts; extractor/mining regression-only; two-cycle cross-stream E2E with path-provenance + tier-gate assertions; cost-cap assertion (<=200/cycle).

## Related experiment (filed elsewhere)

- Local-model candidate pre-filter + reliability/cost measurement -> **epic #909** (comment 5288808263). Not in #438 scope.
