---
title: "Epic Scope — #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4"
type: decisions
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-09
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Epic Scope — #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4

**Date:** 2026-08-09
**Status:** draft — awaiting Human Gate 1
**Decision context:** CONDITIONAL PROCEED (01-align.md); research brief (02-research-brief.md).

---

## 1. Scope Boundaries

### In Scope

**Phase 2 — Entity extraction & cross-session dedup**
- Entity extraction stage in the conversation-mining pipeline: extract entities (issues, PRs, tools, concepts, domain objects) from session transcripts/documents, mapped to `objectKind` vocabulary (Project, WorkItem, document, tag, user, skill, tool, agent, workflow, agreement, standard, other).
- Cross-session entity resolution (short-circuit chain): exact (normalized string) → fuzzy (edit distance, pre-filtered by mention detection) → semantic (embedding cosine with thresholds). v1 = fuzzy+semantic; perfect resolution is v2 non-goal.
- Object writes: `create_object` MERGE by deterministic canonical id (sha256 of canonical name — mirrors production `_connect_issue_objects` pattern); no auto-created duplicate Objects.
- Wiring: `(Point)-[:aboutObject]->(Object)` and `(Event)-[:aboutObject]->(Object)` for extracted entities. **Extracted-entity wiring uses deliberate `create_object`/`aboutObject` resolution ONLY — the legacy `_create_about_edges` auto-detect path (projection/edges.py:69-111, Subject-stub fallback) is bypassed for extraction so no stub `Subject` nodes are created for extracted entities.**
- Content dedup ("we already decided this"): semantic similarity across extracted `decision`-kind Points using the production `_semantic_dedup` two-tier pattern (content-hash + embedding-cosine); candidates surface for review, no auto-merge.
- **Drift fix:** replace `INSTANTIATES` Event→Object wiring with `aboutObject` in `_connect_issue_objects` (sdk.py:4340) + `session_indexer.py:555`; update `ranking.py` session graph_boost query (lines 172, 245) to the new edge; sync `security.py:84` whitelist.

**Phase 4 — Cross-ontology integration**
- about*/structural wiring on extracted content: **`produces`/`uses` are Event→Object for artifacts and tools per §3.5** (e.g., session Event `uses` Object for the tool/artifact); **decision/claim Points are wired `(Event)-[:produces]->(:Point)` per the canonical #531 pattern** (the decision Event produces the decision Point); Point→Event `aboutEvent` where a Point describes a session occurrence.
- EP propagation on extracted Points **gated**: extracted Points created `status: draft`; no auto-promotion to `live` on extraction wiring (SDK #131 default change or explicit flag); EP excludes draft Points (`ep.py` change); no auto-mitigation (NAND/IMPL) wiring from extraction until review.
- Temporal belief tracking: extracted Points carry `validFrom` = real session date (read from session frontmatter, not ingest time); NAND operators link contradictory cross-session decisions; belief-over-time query ("decided X on D1, contradicted on D2"); reuse `supersede_point`/CORRECTS for explicit replacement.

**Cross-cutting**
- Calibration milestone issue (Gate B carrier): 50-session labeled sample → ≥70% extraction precision, dedup threshold calibration, EP grounding before/after snapshot (≤2% mean absolute, full live Point set).
- Child issues created **blocked on Gates A+B** (`Depends on: #320 + calibration`).

### Out of Scope

- **Phase 1 (Point extraction)** — DONE and piloted (#416). Not re-planned.
- **Phase 3 (Action Recognition)** — DELETED per ONTOLOGY v3.2 (Action dissolved v3.0; `instantiates` removed #214). Replaced by Event→Object `aboutObject`/`uses`/`produces` wiring (§3.5).
- **Real-time extraction** — batch processing only (original non-goal, preserved).
- **Perfect entity resolution / semantic dedup v2** — v1 fuzzy matching is the target; semantic-only resolution deferred.
- **Auto-creating Points without provenance** — every extracted Point traces to source session Event (original non-goal, preserved).
- **Automated cross-domain connection discovery** — #438 (SEPARATE epic; graph-driven IMPL/NAND discovery between existing Points). #264 is conversation-driven extraction; the two stay distinct.
- **Full-text indexing of session content in the graph** — files are the source of truth (#320 non-goal, preserved).
- **Search UI / UX surface** — API/CLI/MCP only.

### Boundary Rationale

The cut principle is **conversation-driven extraction with provenance, gated for graph safety**: everything in scope converts unstructured session text into graph content with traceability, and every graph-pollution risk (EP propagation, entity dedup errors, draft/live lifecycle) is explicitly gated or deferred. Anything that discovers connections *within the existing graph* (not from text) belongs to #438. Anything already delivered by Phase 1 (Point extraction) is not re-planned.

---

## 2. Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | low | API/CLI/MCP only — no UI surface (search integration is #320's scope) |
| Architecture | complex | Entity extraction stage, 3-tier resolution chain, semantic dedup, EP draft-filter change, INSTANTIATES→aboutObject migration touching sdk/ranking/security |
| Ontology | complex | about* wiring on extracted content, produces/uses Event→Object edges, temporal belief tracking (validFrom/NAND/belief-over-time), draft-status EP semantics |
| Config | standard | LLM model selection for entity stage, dedup threshold calibration, extraction prompts |

---

## 3. High-Level E2E Test Cases

### E2E-1: Session → Entity Objects with provenance
**Given:** a mined session transcript containing references to "port 16379", "FalkorDB", and issue "tortoise#123"
**When:** the Phase-2 entity extraction runs on the session
**Then:** an Object node exists for each referenced entity with `objectKind` matching its class (tool/workitem/other)
**And:** each Object is connected via `aboutObject` to the extracted Points that mention it
**And:** each Object traces to the source session Event (provenance chain intact — Point `extractedFrom` Source `references` Document/Event; extracted Points describing a session occurrence also carry `aboutEvent` to the session Event)
**And:** no stub `Subject` node is created for any extracted entity (legacy auto-detect bypassed)

### E2E-2: Cross-session entity dedup (same entity, two sessions)
**Given:** session A mentions "port migration" and session B (different day) mentions "port 16379 change" referring to the same effort
**When:** both sessions run through Phase-2 resolution
**Then:** exactly ONE Object node exists for the entity (no duplicate)
**And:** both sessions' Points wire to the same Object via `aboutObject`
**And:** the merge is auditable via a deterministic artifact — a `dedupe` event in the event log OR a `canonical_id`/`resolved_from` property on the surviving Object (assert the artifact's existence)

### E2E-3: Content dedup — "we already decided this"
**Given:** a new session restates a decision already extracted from a prior session ("change default port to 16379")
**When:** Phase-2 content dedup runs on the new session's decision Points
**Then:** the duplicate decision Point is NOT auto-created as a new live Point
**And:** the candidate is surfaced as a dedup hit (review state), linking to the prior decision Point

### E2E-4: Extracted Points are EP-safe (draft, no pollution)
**Given:** Phase-2 extraction creates Points for a batch of sessions
**When:** the extraction batch completes
**Then:** every extraction-created Point has `status: draft`
**And:** no extraction-created Point auto-wires IMPL/NAND operators or auto-promotes to `live` (SDK #131 lifecycle overridden for extraction)
**And:** EP propagation excludes draft Points — snapshot mean grounding of all `live` Points changes ≤2% (mean absolute) vs the pre-batch snapshot

### E2E-5: Event→Object procedural wiring (produces/uses) + INSTANTIATES drift removal
**Given:** a session where a decision "port 16379" was reached and a config file `redis.conf` was edited; and an issue/PR-referencing session goes through the full ingest path (`ingest_corpus` / MCP `tortoise_index_sessions`)
**When:** Phase-4 structural wiring runs on the mined session AND the full session ingest path runs for the issue/PR-bearing session
**Then:** the decision Event `produces` the decision Point
**And:** the session Event `uses` the Object for the edited artifact / tool used
**And:** no `instantiates` edge exists anywhere in the resulting graph (drift removed — checked on BOTH the mined-session path and the ingest path, covering `_connect_issue_objects` + `session_indexer.py`)
**And:** an aboutObject-connected session still receives graph boost in ranking output (ranking.py boost query migrated — no silent search-quality regression)
**And:** the predicate whitelist (security.py) no longer permits `INSTANTIATES` writes

### E2E-6: Temporal belief tracking across sessions
**Given:** session D1 states "use port 16379" and session D2 (later) states "revert to 16380, 16379 was wrong"
**When:** both sessions are mined and Phase-4 temporal wiring runs
**Then:** the D1 and D2 decision Points are linked by NAND (contradiction)
**And:** both Points carry `validFrom` set to their real session dates
**And:** a belief-over-time query returns "port decision: 16379 (D1) → contradicted 16380 (D2)" with both dated
**And (replacement branch):** when D2 explicitly supersedes D1 ("replace the old decision"), a `CORRECTS` edge + `outdated: true` on D1 is created via `supersede_point` instead of (or in addition to) NAND

### E2E-7: Gate gating — child work cannot start before gates
**Given:** #320 is not STABLE or the calibration milestone has not passed
**When:** any Phase-2/4 child issue is processed by issue-workflow
**Then:** the child issue's dependency chain (Depends on #320 + calibration) blocks execution
**And:** no graph writes occur until both gates are satisfied

---

## 4. Human Approval Gate

## Epic Scope Ready for Review

**Scope:** Phase 2 (entity extraction + cross-session dedup + INSTANTIATES drift fix) + Phase 4 (about*/produces/uses wiring, gated EP propagation, temporal belief tracking); Phase 3 deleted; #438 boundary preserved; all child issues gated on Gates A+B.
**E2E test cases:** 7 drafted (provenance, dedup×2, EP-safety, procedural wiring, temporal, gate-gating)
**Complexity:** UX low / Architecture complex / Ontology complex / Config standard

Review the scope boundaries and E2E test cases. Reply "proceed" to continue to detailed planning, or give feedback.
