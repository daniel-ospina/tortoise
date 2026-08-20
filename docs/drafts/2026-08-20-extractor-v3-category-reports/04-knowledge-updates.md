---
title: "Knowledge Updates category — v2 extractor LongMemEval full-run (2026-08-19)"
type: log
domain: capability
doc_status: draft
created: 2026-08-20
subjects.team: epistemic-team
---

# Knowledge Updates category — v2 extractor LongMemEval full-run (2026-08-19)

**Verdict: parity at 69.4% vs 70.8% (−1.4pp) is a FALSE result — the v2 extractor never ran (a Python 3.9 environment bug crashed S5 on every session: `TypeError: Unable to evaluate type annotation 'str | None'` — pydantic v2 on 3.9.6 can't eval the lazy annotation; the venv was 3.12 but the runner used system python3). The measured "v2" KU number is baseline-minus-turn-points-plus-bloat — a robustness property of the retained raw-transcript leg.**

## Critical findings
1. **Measurement hygiene:** the runner executed under 3.9.6 despite .python-version=3.12; n_ingest_errors ≈ 140/question across all 78 KU questions. The `n_ingest_errors > 0` signal should have tripped a run-invalid flag (0% evidence recall across EVERY category went unnoticed).
2. **Extraction philosophy vs KU content:** S2/S4's "durable world-model lesson" filter discards exactly the concrete STATE VALUES the KU benchmark queries. Verified: the emitted points for a 5K personal-best answer scored 0.00–0.25 overlap vs the answer turn (all fail the 0.4 has_answer threshold); a value-preserving point ("The user set a personal best time of 27:12 in a charity 5K run") scores 0.75. **The narrative-first philosophy distills episodic state values into generic durable lessons — and KU answers ARE the values.**
3. **Supersession chain is broken at three points (write→read):**
   - S2's supersession is entity-lifecycle-driven ("strategy-B supersedes strategy-A" — explicit language). KU updates are fact-value contradictions ("gym at 6pm now" vs "was 5pm") with no "supersedes" phrasing → derive_supersessions never fires.
   - The ingest boundary drops it: payload points carry reason:REVISES, but ingest_v2._write_payload → create_point never writes reason/supersedes props → no CORRECTS-style edges in the eval graph.
   - The read-side promotion is inert: [SUPERSEDED BY]/[SUPERSES] markers (#1367) read search-payload D8 fields that only exist with those edges; the decoration is Docker/HNSW-only — embedded/TF-IDF never decorates.
4. **Context bloat:** 6,028 → 26,740 tokens (4.4×); 4/6 regressions are reader failures under bloat (evidence present but buried).

## Competitor mechanisms
- **Graphiti** (closest): EntityEdge carries valid_at/invalid_at/expired_at; contradiction detection at ingest (a dedupe prompt returns duplicate_facts + contradicted_facts; a contradicted edge gets invalid_at = new fact's valid_at, expired_at = now — soft-delete, never delete); query-time SearchFilters on temporal fields (current-view excludes expired; point-in-time restores history).
- **Mem0:** LLM classifies each fact vs retrieved memories into ADD/UPDATE/DELETE/NOOP (UPDATE keeps ID + old_memory); v3 = ADD-only + linked_memory_ids (retrieval decides). Fact prompt explicitly extracts user state values ("Favourite movies are Inception and Interstellar").
- **Letta:** in-place memory-block edits (memory_rethink/replace/insert — current-by-construction); git-backed MemFS history.

## Recommendations (ranked)
1. **P0 — re-run under Python 3.12 + add a runner integrity gate:** n_ingest_errors > 0 or global evR = 0 → mark the run invalid.
2. **P0 — state-value extraction tier (the KU needle):** extend S2/S4 to emit fact-level attribute-value points preserving the value tokens ("personal best 5K time = 27:12, as of <date>"), backed by memory_granularity (personal-state values are durable). Directly fixes the evR≈0 marking gap (0.75 vs 0.25 overlap).
3. **P1 — fact-contradiction detection pass** (Graphiti's dedupe_edges adapted): in S4, compare new state points against S3-retrieved same-entity claims → NAND or reason:REVISES with an explicit supersedes link. Deterministic fallback: same entity+attribute, different value, later date → REVISES.
4. **P1 — persist supersession through ingest + materialize edges:** write reason/supersedes/superseded_by props in ingest_v2/commit + CORRECTS edges so the read-side promotion has data.
5. **P1 — validity windows** (valid_at/invalid_at on points; default retrieval excludes invalidated; point-in-time queries).
6. **P1 — co-retrieve the superseding claim:** whenever a superseded point enters top-k, pull its superseding neighbor (the judge's updated-answer rubric makes this the single highest-leverage read-path change for KU).
7. **P1 — KU-specific reader instruction:** "if a newer statement supersedes an older one about the same thing, answer from the newer statement and note the change."
8. **P2 — make the [SUPERSEDED BY]/[SUPERSEDES] markers work in embedded/TF-IDF mode** (currently Docker/HNSW-only).
9. **P2 — reader context hygiene:** cap tokens, prefer compact extracted points over verbatim transcripts when both exist.

**Bottom line:** without state-value extraction (#2), supersession has nothing to supersede on LongMemEval content — the 27:12/25:50 case never reaches the graph as a claim pair, so no read-side surfacing can save it.
