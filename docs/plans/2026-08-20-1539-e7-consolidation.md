<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/04-plan.md -->

# #1539 E7 — Cross-Session Consolidation (4-way ADD/UPDATE/DELETE-soft/NOOP + Entity Resolution) Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Generalize the S3-retrieved-priors comparison in `execute_embed` from the current 2-way (NEW/REVISES) to the Mem0 4-way — **ADD** (new fact), **UPDATE** (same entity+attribute, new value, later date → REVISES/supersede), **DELETE-soft** (retraction → `retract_point` tombstone, never hard-delete), **NOOP** (duplicate → additive `duplicates` property on the existing point, NO new point, NO new edge) — plus a write-time **Graphiti two-phase entity-resolution pass** (deterministic exact/bare first, bounded LLM fallback for alias names that degrades to ADD and never blocks capture).

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** The 4-way decision lives where S3 priors and the embed list meet — `execute_embed` (S5) in `tortoise/extractor_v2.py` — consuming the E5-length-guarded `_token_overlap` and an E3 `search_keys`/E2 Tier-A value slot for entity+attribute identity. NOOP and DELETE-soft records travel at the **extractor-result level** (`result["noops"]`, `result["deletions"]`) and are applied by the eval write path (`tools/longmem_eval/ingest_v2.py`): NOOP stamps the additive `duplicates` property + CONTAINS link-only edge (existing edge types only), DELETE-soft calls the existing `sdk.retract_point` (status=`retracted`, no resurrect by construction). Entity resolution runs between S4 and S5 as a new bounded sub-step: deterministic `_find_existing_entity` first; one wall-clock-bounded LLM call only for the unmatched/ambiguous remainder when the backend is real and candidates exist; LLM failure/timeout → resolution skipped (degrade to ADD), never a block. The verify-gate fix (`_point_exists` N+1 at 500-Q scale) lands as a batch `_existing_point_ids` helper in the ingest lane, mirroring the existing `point_props_for_hits` pattern.

### Pattern Research

> **Findings date:** 2026-08-20 (sourced from the epic brief `02-research-brief.md`; no fresh gate queries fired).

**Mem0 4-way reconcile (canonical)** — Mem0's write path is two-phase: extract candidate facts per message pair → reconcile against existing memories by vector similarity, picking **ADD / UPDATE / DELETE / NOOP** per candidate (brief §1 line 21; write-path §2 line 37). E7's mapping: ADD = no prior; UPDATE = same entity+attribute, new value, later date → supersede (REVISES, existing `supersede_point`); DELETE = contradiction with no successor → soft invalidation; NOOP = duplicate → link, no new fact. Our write-time placement (consolidate on capture, before index) matches both Mem0 and Graphiti dedupe-at-ingest — contradiction/supersession is a write-time job, not a read-time fix (brief §2 line 38).

**Graphiti two-phase entity resolution (canonical)** — Graphiti runs deterministic-then-LLM entity resolution (brief §2 line 37: "3+N LLM calls per episode … with deterministic-then-LLM entity resolution"). E7 mirrors the two phases: exact/bare-form deterministic matching first (already in `_find_existing_entity`), LLM only for the ambiguous/unmatched remainder — the minimum that can link "Joe"/"Joseph".

**Hindsight observation consolidation (competitor variance)** — Hindsight's background consolidation layer folds typed facts into deduplicated, evidence-grounded observations with quote + proof counts, refined-not-overwritten (brief §2 line 37; Data Model Research Notes §4). That validates (a) the additive `duplicates` property as proof-count-style evidence on the canonical point and (b) "deduplicate at consolidation, never fragment recall" as the KU/MSR amplifier.

**Known pitfalls (from the epic's own hazard note)** — NOOP must NOT be an edge: the ontology's Point↔Point edges (IMPL/NAND/hasPart/CORRECTS) cannot express "duplicate of"; IMPL would couple EP weights (how-to-use-tortoise hazard, 04-plan §4). Prior art: REPHRASE is a dedup label, not a written operator. DELETE must NEVER hard-delete (brief §1 line 21: "contradicted facts invalidated, never deleted").

> **Gate skipped:** the plan touches zero third-party dependencies (all in-repo machinery: `extractor_v2.py`, `sdk.py`, `ingest*.py`); Mem0/Graphiti/Hindsight patterns are already triangulated in the epic brief. No library-version/usage/pitfall queries needed.

### Integration Surface Map

From test-design #1515 (28 surfaces), via the issue's verification checklist:

| Surface | Layer | Test | E7 delta |
|---|---|---|---|
| **S17 consolidation** | unit + integration | 4-way decision vs S3-retrieved priors; NOOP = additive `duplicates` (NOT a new edge, NOT IMPL); DELETE-soft via `retract_point`; entity resolution deterministic-first, LLM fallback degrades to ADD, never blocks; no double-count | the core of this issue (`tests/test_consolidation_4way.py`, `tests/test_ingest_v2_consolidation.py`) |
| **S12 graph writes** (bug-pattern flag: N+1) | integration | batch `_point_exists` lookups — NO N+1 at 500-Q run scale (verify P2, 2026-08-20) | `_existing_point_ids` batch helper in `ingest.py`/`ingest_v2.py` + query-count spy test |
| **S13 supersession CORRECTS** | integration | UPDATE rides E5's machinery end-to-end (payload reason=REVISES → new content-addressed id → `supersede_point`) | E7 only *feeds decisions*; E5 owns the write. E7 integration asserts the chain exists post-ingest |
| **S16 supersession derivation** | unit | the classifier inherits E5's length-guarded `_token_overlap` (no false REVISES at ≥0.6 on short points) | consume, do not re-derive |
| **S22/S23/S24 Layer-1 payload + supersessions + client_commit_id** | integration | NOOP/DELETE-soft records stay OUT of the Layer-1 payload in this issue → **⛔ conditional gate** for the production path (payload section + 3-site) | result-level records only |

**Journey alignment:** J1 (session captured → facts extracted; consolidation runs on capture), J3 (fact changes → new value wins; cross-session aggregation without double-count), E2E-6 (UPDATE chain) + E2E-11 (NOOP + UPDATE + DELETE-soft + no double-count + ambiguous-entity negative) + E2E-10's duplicate-paraphrase negative (NOOP collapse).

**Tech Stack:** Python 3.12, FalkorDBLite (tests), FalkorDB real (E2E), pytest, existing `tests/model_adapters.py` stub-model seam.

---

## 1. Design Decisions

### D1 — The 4-way classifier is a pure, deterministic function in S5

Replace `_find_point_match(points, content)` (`extractor_v2.py:777`) with `classify_consolidation(point, priors, *, entity_mentions, current_date) -> DecisionRecord`. It stays **pure** (no LLM, no graph I/O) so it is unit-testable with fixture priors and S5 keeps its "execution, not a prompt" contract (owner confirmation, #1350). The classifier produces one of:

| Decision | Trigger (in priority order) | Write-path effect |
|---|---|---|
| **NOOP (identical)** | normalized content equal to a prior (existing exact/content-hash dedup) | no payload point; `result["noops"]` record; write-time stamps `duplicates` + CONTAINS link |
| **UPDATE** | same entity + same attribute, value differs, overlap ≥ REVISES band **and length-guarded** (E5's guard), current session later | payload point reason=`REVISES` (existing E5 flow: new content-addressed id → `supersede_point`) |
| **NOOP (paraphrase)** | same entity + same attribute, value-signature equal (Tier-A, E2) OR overlap in the NOOP band [0.45, 0.6) with entity/attribute gate | no payload point; `duplicates` stamp + CONTAINS link |
| **ADD** | no prior match | payload point reason=`NEW` (unchanged) |
| **DELETE-soft** | NEVER from content alone — only from an explicit `retractions` ref resolved to a live prior | no payload point; `result["deletions"]` record; write-time `retract_point` |

Constants (module-level, tunable via the run protocol): `REVISES_MIN_OVERLAP = 0.6` (existing), `NOOP_MIN_OVERLAP = 0.45`, and the value-signature path for Tier-A points short-circuits both bands. **Ambiguous entity/attribute → NOOP only on high text overlap, else ADD — never UPDATE** (E2E-11 owned negative; supersede would wrongly terminalize a fact). **Self-match impossible by construction** (identical content → NOOP not UPDATE; changed content → new content-addressed id ≠ prior id) — existing `supersede_point` guard remains the backstop (E2E-11 negative).

### D2 — Entity identity = resolved-entity mention OR `aboutObject` link; attribute identity = `search_keys` OR value-signature

The "same entity+attribute" gate needs data S3 does not return today. Two additive inputs:
- **Entity:** the prior point's `aboutObject` Object names (production graph) **or** the resolved entity name appearing in the prior's content (eval graph, which today writes no `aboutObject` edges — see D7). Either suffices.
- **Attribute:** `search_keys` on the prior (E3) overlap with the candidate's `search_keys`; for Tier-A state-value points (E2), the master-list vocabulary anchors the attribute and the value-signature (`_value_signature`: normalized numeric/time tokens — "6pm"/"six pm", "27:12" → canonical form) decides NOOP-vs-UPDATE: **equal signature → NOOP, differing signature → UPDATE (later date)**.

### D3 — Entity resolution: two-phase, LLM-bounded, degrade-to-ADD

New `resolve_entities(entity_refs, search, model=None) -> ResolutionMap` in `extractor_v2.py`:
- **Phase 1 (deterministic):** the existing `_find_existing_entity` (exact → bare → ambiguous) for every embed entity.
- **Phase 2 (LLM fallback):** fires ONLY when `model is not None` AND `search` has entity candidates AND at least one embed entity is unmatched/ambiguous. One `_complete` call (existing 600s wall-clock-bounded thread pattern, temperature 0.0 via the `MODELS` seam), JSON contract `{"resolutions":[{"name","resolves_to"}]}` where `resolves_to` is an existing id or name. Every resolution is validated against the candidate list (id or normalized-name match) — invalid/ambiguous → dropped with a warning, never guessed.
- **Failure/timeout → degrade:** `warnings.append(...)`, empty phase-2 map, pipeline proceeds with phase-1 results (i.e., unresolved entities keep their names → ADD semantics). **Never blocks capture** (P1 invariant).
- Resolution is applied **between S4 and S5** in `extract_session_v2`: the map rewrites embed entity `name` + `about_entities` refs to the canonical existing name/id, so `execute_embed`'s link-before-create + server-side `aboutObject` MERGE-by-name (#452) land on the canonical Object. `execute_embed` itself stays deterministic — the resolver result is pre-applied, and unit tests pass `model=None`/a fake resolver.
- Skipped entirely when S3 is degraded (no candidates → nothing to resolve against).

### D4 — NOOP is an additive `duplicates` property + link-only CONTAINS edge

Approved contract (04-plan §4): additive `duplicates` list property on the **existing** point — NO new edge, NO new kind, NO IMPL (EP-coupling hazard). `duplicates` contains the session refs folded into the canonical point (eval: `lme:{qid}:s{si}`). The write path (ingest_v2) additionally creates the `(Session)-[:CONTAINS]->(Point)` edge — **link-only via existing edges** — so the folded session's provenance is traversable. Retrieval dedup is by construction: physically one point, so aggregation questions cannot double-count (E2E-11). Write is idempotent read-modify-write (set-merge; a re-run appends nothing new — E2E-11 "aggregation count unchanged" + ingest re-run no-op). Evidence marking OR-in: if the folded session had an answer turn (E2E-11's answer-bearing duplicate), `has_answer=true` is OR'd onto the canonical point (mirrors the existing #1369 P2 collision OR-in in `_write_payload`).

### D5 — DELETE-soft only from explicit `retractions`; write = `retract_point`

The embed list gains an **additive `retractions` field** in the S2/S4 `OUTPUT_CONTRACT` (list of `{content | id}` refs the conversation withdraws — "forget my gym schedule"). `execute_embed` resolves each ref against S3 priors with the existing never-guess discipline (`_resolve_superseded` pattern: by id, else content/name match filtered to the same kind; 0 or >1 matches → warn + skip, fail-open). Resolved refs become `result["deletions"] = [{point_id, evidence}]`. The write path calls `sdk.retract_point(point_id)` (status=`retracted`, tombstone, point stays in graph). **No resurrect on recall by construction**: default retrieval excludes terminal statuses (`include_terminal=False`, #1391) — asserted in tests. `retract_point` ValueError (missing/terminal) is caught and warned — the eval never dies on a delete. S3 only returns live priors (terminal excluded at the search layer), so deletions target live points.

### D6 — Verify-gate fix: batch `_point_exists` (surface 12 N+1)

New `_existing_point_ids(proj, ids) -> set[str]` in `tools/longmem_eval/ingest.py` — one `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id` (mirrors `point_props_for_hits`). Refactor all N+1 loops:
- `ingest_haystack` (deterministic leg): per-session, one call for the session's turn ids + raw id.
- `ingest_haystack_v2`: per-session raw id; `_write_payload` computes one set over payload point ids + operator src/dst ids (Point-node semantics preserved exactly — the current per-call check matches `:Point` only, so event-endpoint operators keep today's behavior).
Re-run idempotency semantics unchanged (the batch is just the existence probe). A query-count spy test pins ≤1 existence query per session.

### D7 — S3 returns the classifier's inputs (batch enrichment) + eval v2 ingest writes `aboutObject`

- `search_graph`/`_fts_rows`: after candidate collection, **one batched** Cypher fetches `aboutObject` names + `when`/`created_at` for candidate point ids and merges them into the search-result dicts (id → `{about_entities, when, created_at}`). Batch, not per-point (same anti-N+1 discipline).
- `ingest_v2._write_payload` currently drops `about_entities` from payload points → the eval graph has no `aboutObject` edges. E7 adds the `(p)-[:aboutObject]->(o)` write (the canonical predicate, hosted §4.2) — small parity fix that makes the classifier's entity gate real in the eval (and mirrors production).

### D8 — Records stay result-level; the Layer-1 payload does not grow in this issue

NOOP/DELETE-soft records live in `result["noops"]` / `result["deletions"]` (extractor output) and are consumed by the eval write path only. They are **NOT** added to the Layer-1 payload (`client_commit_id` canonical stays byte-identical for old clients; no 3-site churn). The production capture→commit application (payload sections + `compute_client_commit_id` inclusion + `reconcile` handling) is **⛔ conditional** (section 5). UPDATE rides E5's existing supersessions channel — already 3-site.

---

## 2. Implementation Steps

> Sequence: Task 1 → 2 → 3 → 4 → 5 → 6 → 7. Tasks 1–4 are `extractor_v2.py`; Tasks 5–6 are the write path; Task 7 is the E2E-11 integration gate. Each task: TDD (failing test → implement → pass), `uv run pytest <file> -v`, commit per task via the commit-workflow skill.

### Task 1: The 4-way classifier (pure, deterministic)

**Intent:** The decision core of E7 — one function that maps (candidate point + S3 priors + entity/attribute context) to ADD/UPDATE/NOOP/DELETE, replacing the 2-way `_find_point_match`.
**Acceptance:** `classify_consolidation` returns a `DecisionRecord` with `decision` ∈ {ADD, UPDATE, NOOP, DELETE}, `prior_id`, `overlap`, `reason`, `evidence`. Exact-content → NOOP(identical) with the prior id; length-guarded overlap ≥ 0.6 + value-differs + later-date → UPDATE; Tier-A equal value-signature OR band [0.45, 0.6) with entity+attribute gate → NOOP(paraphrase); otherwise ADD. Ambiguous → NOOP only when overlap ≥ NOOP band, else ADD — never UPDATE. Short-point guard (a 5-token point sharing 3 tokens with a 50-token point is neither REVISES nor NOOP) inherited from E5's `_token_overlap` length guard (consume it; if E5 has not landed, add the guard here with the same constant and a TODO cross-ref — see Open Questions Q1).
**Files:**
- Modify: `tortoise/extractor_v2.py` (replace `_find_point_match` at ~:777; add `classify_consolidation`, `_value_signature`, `DecisionRecord`, constants `REVISES_MIN_OVERLAP`/`NOOP_MIN_OVERLAP`)
- Test: `tests/test_consolidation_4way.py` (new)

**Step 1:** Write `tests/test_consolidation_4way.py` fixtures + cases: (a) ADD — no prior; (b) UPDATE — same entity+attr, "gym at 6pm" → "gym at 5pm", later date, overlap ≥ 0.6; (c) NOOP identical — exact normalized equality; (d) NOOP paraphrase — "workout at the gym at six pm" vs "gym at 6pm" with equal value-signature; (e) NOOP band — overlap in [0.45, 0.6) + entity mention gate; (f) length guard — 5-token vs 50-token shared-3 → ADD; (g) ambiguous entity + high overlap → NOOP, never UPDATE; (h) no self-match — identical → NOOP not UPDATE.
**Step 2:** Run `uv run pytest tests/test_consolidation_4way.py -v` — expect FAIL (no `classify_consolidation`).
**Step 3:** Implement `_value_signature` (normalize numeric/time tokens: "6pm"/"six pm"/"6:00 pm" → `"6:00pm"`; "27:12"/"27m12s" → `"27:12"`), `DecisionRecord`, and `classify_consolidation` with the D1 decision table. Keep `_find_point_match`'s callers compiling via a thin shim for this task (deleted in Task 2).
**Step 4:** Run — expect PASS.
**Step 5:** Commit (`feat(extractor): E7 4-way consolidation classifier`).

### Task 2: Wire the classifier into S5 + retractions + result records

**Intent:** `execute_embed` uses the 4-way decision; DELETE-soft gains its explicit trigger; NOOP/DELETE records are emitted at the result level.
**Acceptance:** For each embed point, ADD/UPDATE paths produce payload points exactly as today (reason NEW/REVISES); NOOP produces **no payload point** and appends `{"point_id", "session_ref", "overlap", "evidence", "reason": "identical"|"paraphrase"}` to `result["noops"]`; the embed list's additive `retractions` resolve via the never-guess discipline to `result["deletions"] = [{point_id, evidence}]` (unresolvable/ambiguous → warning, fail-open). `stats` gains `noops`/`deletions` counts. Existing tests `test_exact_point_match_dedups` (tests/test_extractor_v2.py:333) and `test_point_supersession_revises` (:321) are **deliberately updated**: exact-match now asserts the NOOP record (id preserved, no payload point) — the E2E-11 MECE boundary (identical-value re-assertion → NOOP, per E5's MECE fix); REVISES stays a payload point.
**Files:**
- Modify: `tortoise/extractor_v2.py` (execute_embed points loop ~:1086–1125 — the loop body that calls `_find_point_match`; payload assembly ~:1240; `__all__`), `tests/test_extractor_v2.py` (2 assertions), `tests/test_consolidation_4way.py`
- Test: extend `tests/test_consolidation_4way.py` with execute_embed-level cases

**Step 1:** Extend tests: (a) NOOP point absent from `payload["points"]` but present in `result["noops"]` with prior id + evidence; (b) `retractions: [{"content": "gym at 6pm"}]` resolves against a matching S3 prior → `deletions` record; (c) ambiguous retraction (two priors, no id) → warning, no deletion; (d) unresolvable retraction → warning, fail-open; (e) stats counts.
**Step 2:** Run — expect FAIL.
**Step 3:** Implement: delete the `_find_point_match` shim; in the points loop, compute the decision per point, branch NOOP/UPDATE/ADD, collect `noops`/`deletions`; resolve `retractions` with `_resolve_superseded`-style discipline (match by id, else kind-filtered content/name); thread `session_ref` (the `session_id` arg) into records; extend stats.
**Step 4:** Run — expect PASS (including the 2 updated assertions in test_extractor_v2.py).
**Step 5:** Commit.

### Task 3: Two-phase entity resolution (deterministic + bounded LLM fallback)

**Intent:** Link "Joe"/"Joseph" at write time — the entity alignment the 4-way needs to match facts across sessions.
**Acceptance:** `resolve_entities(entity_refs, search, model=None) -> ResolutionMap` (map: name → `{"id", "name"}`): phase 1 = `_find_existing_entity` on every ref; phase 2 = ONE `_complete` call (temperature 0.0) only when `model` set + real-candidate search + unmatched/ambiguous refs remain, with a strict JSON contract; every resolution validated against candidates; model failure/timeout → warning + phase-1-only map (degrade to ADD), never raises. `extract_session_v2` runs it between S4 and S5 and rewrites the complete embed list's entity names + `about_entities` refs to canonical names. The resolution evidence lands in `link_before_create` notes + a `resolution` list in the result.
**Files:**
- Modify: `tortoise/extractor_v2.py` (new `resolve_entities` + `_resolution_prompt`, `extract_session_v2` orchestration)
- Test: `tests/test_consolidation_4way.py` (resolution section)

**Step 1:** Tests with a fake resolver: (a) phase-1 exact match resolves without calling the model (spy asserts no call); (b) unmatched "Joe" + candidate "Joseph" → fake model returns the alias → embed list rewritten, `about_entities` refs canonicalized; (c) invalid resolution (`resolves_to` unknown) → dropped + warning; (d) model raises/returns garbage → warning + phase-1-only, `extract_session_v2` still returns a payload (never blocks); (e) S3 degraded (no candidates) → resolver skipped; (f) prompt content pins: candidates + names + the JSON contract in the prompt.
**Step 2:** Run — expect FAIL.
**Step 3:** Implement `_resolution_prompt` (system: "confident alias resolution only, never guess, ambiguous → null"; user: EXISTING ENTITIES `id | name | kind` lines + NEW ENTITY NAMES; return `{"resolutions":[...]}`), `resolve_entities` (phase 1 → phase 2 gating → validation), and the S4→resolve→S5 orchestration in `extract_session_v2` (canonical-name rewrite on a copy of the complete list; resolution notes surfaced in the result).
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

### Task 4: S3 batch enrichment (about_entities + when/created_at for prior points)

**Intent:** Give the classifier and resolver their inputs — prior points must carry entity links and dates.
**Acceptance:** `search_graph` merges, per candidate point id, `about_entities` (Object names via aboutObject) + `when`/`created_at` from **one batched** Cypher (no per-point queries). Row shape gains `about_entities: [...]`, `when`/`created_at`. Degraded-mode unchanged. `_index_search` preserves the new fields.
**Files:**
- Modify: `tortoise/extractor_v2.py` (`search_graph`, `_fts_rows`, `_index_search`)
- Test: `tests/test_extractor_v2.py` (search-section) + `tests/test_consolidation_4way.py`

**Step 1:** Test: mock `sdk` returns candidate points; assert the enrichment query runs once (query-count spy) and results carry `about_entities`/`when`; assert degraded search still returns the shape without enrichment.
**Step 2:** Run — expect FAIL.
**Step 3:** Implement `_enrich_point_priors(sdk, points)` — collect ids → one `MATCH (p:Point) WHERE p.id IN $ids OPTIONAL MATCH (p)-[:aboutObject]->(o:Object) RETURN p.id, collect(o.name), p.when, p.created_at` → merge into results. Call after the S3 query loop in `search_graph`; keep the first-query-failure degradation semantics intact.
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

### Task 5: Eval write path — apply NOOP (duplicates + CONTAINS link) + DELETE-soft (retract_point) + aboutObject parity

**Intent:** The consolidated decisions become graph state in the eval graph, without double-counting or resurrect.
**Acceptance:** `_write_payload` (ingest_v2) applies `result["noops"]` — for each record, one read-modify-write stamping `duplicates = set-merge(existing ∪ [session_ref])` on the canonical point (idempotent: re-run appends nothing) + `(s_node)-[:CONTAINS]->(point)` link + `has_answer` OR-in when the folded session had evidence turns. `ingest_haystack_v2` applies `result["deletions"]` — `sdk.retract_point(pid)` wrapped best-effort (ValueError → warning, run continues). Payload points now write `aboutObject` edges to their entities (canonical predicate; makes the classifier's entity gate real in the eval). Stats gain `noops_applied`/`deletions_applied`.
**Files:**
- Modify: `tools/longmem_eval/ingest_v2.py` (`_write_payload`, `ingest_haystack_v2`)
- Test: `tests/test_ingest_v2_consolidation.py` (new)

**Step 1:** Tests (FalkorDBLite via the `sdk_factory` fixture): (a) NOOP — two sessions, same fact different wording → ONE statement point; `duplicates` == the two session refs; both sessions CONTAINS it; `count(DISTINCT p)` over the fact == 1; (b) NOOP identical — re-asserted exact content → no second point, duplicates unchanged count-wise on re-run (idempotency); (c) DELETE-soft — retraction resolves → prior point `status == "retracted"`, default `tortoise_fts_query` does NOT surface it, `include_terminal=True` does (no resurrect); (d) UPDATE — payload REVISES point + E5 supersede applied → supersession chain in graph (skip assertion if E5's ingest apply not landed → mark xfail with an E5 blocker note; see Q1); (e) aboutObject edges exist for payload points.
**Step 2:** Run — expect FAIL.
**Step 3:** Implement `_apply_noops`, `_apply_deletions` in ingest_v2 + the aboutObject write in `_write_payload` (mirror hosted §4.2 `MERGE (p)-[:aboutObject]->(o)`).
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

### Task 6: Verify-gate fix — batch `_point_exists` (no N+1 at 500-Q scale)

**Intent:** Kill the per-turn/per-point existence probes the 500-Q run would multiply (surface 12 bug-pattern flag).
**Acceptance:** `_existing_point_ids(proj, ids) -> set[str]` in `ingest.py` (one `WHERE n.id IN $ids RETURN n.id` query). All call sites refactored: `ingest_haystack` (per-session turn + raw ids), `ingest_haystack_v2` (raw + `_write_payload` point/operator ids — operator checks preserve today's Point-only semantics). A query-count spy asserts ≤1 existence query per session. Idempotency unchanged (re-run no-op).
**Files:**
- Modify: `tools/longmem_eval/ingest.py`, `tools/longmem_eval/ingest_v2.py`
- Test: `tests/test_ingest_v2_consolidation.py` (+ the existing ingest idempotency tests must stay green)

**Step 1:** Test: wrap `proj.g.query` in a counting spy; run `ingest_haystack`/`ingest_haystack_v2` over a 2-session question; assert existence probes == 1 per session (not per turn); assert a re-run writes nothing new (idempotency preserved).
**Step 2:** Run — expect FAIL (N+1 count observed).
**Step 3:** Implement the helper + refactor the three call sites (compute the id set once per session, then membership checks; `_write_payload` accepts an optional precomputed set).
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

### Task 7: E2E-11 integration gate (consolidation on the shared real graph, FalkorDBLite + real mode)

**Intent:** Prove the epic's E2E-11 end-to-end on the eval graph — the issue's acceptance surface (S17).
**Acceptance:** A 3-session fixture (duplicate paraphrase / contradiction update / withdrawal retraction) ingested via `ingest_haystack_v2` produces: NOOP link (one point, `duplicates` stamped, both sessions linked, aggregation count 1); UPDATE (supersession chain, newer value live, E2E-6 assertions hold); DELETE-soft (retracted, no resurrect); and the owned negatives — ambiguous entity → NOOP never UPDATE, identical-value no-op → count unchanged, self-supersede → guarded. Real-mode smoke (docker FalkorDB, `TORTOISE_DB_URI` set) runs the same fixture and asserts graph state parity.
**Files:**
- Create: `tests/test_ingest_v2_consolidation.py` (E2E-11 section)
- Modify: (none beyond Tasks 1–6)

**Step 1:** Write the E2E-11 fixture + assertions (map each Given/When/Then + owned negative from 05-detailed-e2e.md E2E-11 to a concrete assertion; include the no-double-count aggregation query).
**Step 2:** Run — expect FAIL until Tasks 1–6 land (write it last, run after Task 6).
**Step 3:** N/A (implementation is Tasks 1–6) — this task's work is the gate itself; fix whatever it surfaces.
**Step 4:** Run `uv run pytest tests/test_ingest_v2_consolidation.py tests/test_consolidation_4way.py -v` — expect PASS.
**Step 5:** Commit.

---

## 3. Tests

| Test | Layer | File | Maps to |
|---|---|---|---|
| 4-way classification table (ADD/UPDATE/NOOP-identical/NOOP-paraphrase/DELETE) | unit | `tests/test_consolidation_4way.py` | E2E-11 4-way, S17 |
| Length guard / ambiguous-never-UPDATE / no self-match | unit | `tests/test_consolidation_4way.py` | E2E-6 + E2E-11 negatives |
| execute_embed: noops/deletions records, payload exclusion, retractions resolution, fail-open | unit | `tests/test_consolidation_4way.py` | S17, S16 |
| Entity resolution: phase-1-no-call, alias rewrite, invalid-drop, degrade-to-ADD, degraded-S3 skip | unit | `tests/test_consolidation_4way.py` | S17, P1 invariant |
| S3 batch enrichment (single query, field shape, degraded) | unit+integration | `tests/test_extractor_v2.py`, `tests/test_consolidation_4way.py` | S12 |
| NOOP write: duplicates prop, CONTAINS link, count==1, idempotent re-run, evidence OR-in | integration (FalkorDBLite) | `tests/test_ingest_v2_consolidation.py` | E2E-11 no double-count |
| DELETE-soft: retracted status, no resurrect, include_terminal opt-in | integration | `tests/test_ingest_v2_consolidation.py` | E2E-11, #1391 |
| UPDATE chain end-to-end (REVISES → supersede_point) | integration | `tests/test_ingest_v2_consolidation.py` | E2E-6 (E5-adjacent, xfail-if-E5-unlanded) |
| Batch `_point_exists`: ≤1 query/session, idempotency preserved | integration | `tests/test_ingest_v2_consolidation.py` | surface 12 verify-P2 |
| E2E-11 3-session gate + owned negatives + real-mode smoke | integration (FalkorDBLite + real) | `tests/test_ingest_v2_consolidation.py` | E2E-11 |

**Regression risk watch:** `test_exact_point_match_dedups` + `test_point_supersession_revises` change semantics deliberately (Task 2); `test_payload_passes_layer1` must stay green (payload schema untouched); the existing ingest idempotency tests must stay green after the batch refactor (Task 6).

## 4. Cross-Lane Interfaces

| Interface | Contract | Owner |
|---|---|---|
| `classify_consolidation(point, priors, *, entity_mentions, current_date) -> DecisionRecord` | pure; no LLM/graph I/O; constants module-level | E7 |
| `resolve_entities(entity_refs, search, model=None) -> ResolutionMap` | phase-1 deterministic; phase-2 LLM bounded (`_complete`, 600s, temp 0.0); failure → degrade-to-ADD, never raise | E7 (routes through P2/M3's model object + retry/backoff — reuse, don't re-invent) |
| `result["noops"]` / `result["deletions"]` | extractor-output records consumed by `ingest_v2` only in this issue | E7 |
| S2/S4 `OUTPUT_CONTRACT` | additive `retractions` field (list of refs) — additive, old models emit nothing | E7 (contract extension; S2/S4 prompts) |
| S3 search row shape | + `about_entities` (Object names), `when`/`created_at` on point rows — additive | E7 (R2/R5 consume the same rows; verify no shape break) |
| `_existing_point_ids(proj, ids)` | batch existence probe in `ingest.py`; Point-node semantics only | E7 (verify-P2) |
| Layer-1 payload / `client_commit_id` | **unchanged** in this issue (byte-identical for old clients); UPDATE rides E5's supersessions channel | E7 → E5 |
| `supersede_point` / `retract_point` / `create_point(**props)` / `tortoise_fts_query(include_terminal=…)` | existing SDK tools, no new tools (S13: "call the EXISTING canonical supersede()", same for retract) | E5/E7 |
| `_token_overlap` length guard | consumed from E5 (Q1: coordinate if E5 unlanded) | E5 → E7 |
| E1 `session_date`/`when`, E3 `search_keys`, E2 Tier-A value slot | classifier inputs (D1/D2); assume landed (dependencies) | E1/E2/E3 → E7 |

## 5. ⛔ CONDITIONAL GATE NOTES

> Anything here needs explicit owner/architecture approval before it can be implemented. Not approved by default.

1. **⛔ NOOP/DELETE-soft in the Layer-1 payload + 3-site (production capture→commit).** The `duplicates` **property on the graph is APPROVED** (04-plan §4) and lands in-scope via `ingest_v2`. What is NOT approved: adding `noops`/`retractions` **sections to the Layer-1 payload schema**, including them in `compute_client_commit_id`'s canonical, and applying them in hosted `reconcile` (D8). That is a production-path schema evolution + 3-site change (payload + client_commit_id + ingest) with P4-parity and quota implications (a NOOP commit writes zero new points — quota semantics for no-op deltas) — a separate issue/lane.
2. **⛔ A new "duplicate of" edge type.** Explicitly NOT allowed — a Point↔Point "duplicate of" edge is not in the ontology (existing Point↔Point edges: IMPL/NAND/hasPart/CORRECTS), and IMPL would couple EP weights (how-to-use-tortoise hazard; prior art: REPHRASE is a label, not a written operator). The plan uses the approved additive `duplicates` property + existing CONTAINS edges only. Any plan delta that introduces such an edge violates the epic contract and must be rejected.
3. **⛔ NOOP/DELETE applied to the shared production graph outside the eval harness.** The eval ingests per-question into fresh graphs (axis-2 isolation) — DELETE-soft can never touch another question's points. Production-capture retractions/duplicates on the shared real graph require the payload channel (gate 1) + operator-action audit; not in this issue.
4. **⛔ LLM entity-resolution on the capture latency path beyond the bounded deadline.** The resolver reuses the existing `_complete` 600s wall-clock bound; if that proves too slow for production capture (a capture is already 4 flash prompts), the resolver must be deferred/async or disabled via env — never extend capture latency unbounded. Flag for the run-protocol step (pilot) to measure.
5. **⛔ Ontology/kind additions for retraction or dedup semantics.** None — `retractions` is an embed-list field (additive), DELETE uses status machinery (existing), NOOP uses a property (approved). Any new kind/pack/expansion-pack proposal is out of scope (epic contract: "Ontology is sacred").

## 6. Open Questions

- **Q1 (coordination):** E5 (#1537) is OPEN and owns the `_token_overlap` length guard + ingest-time supersede application. E7 consumes both. If E5 lands first → consume; if not → this plan implements the guard with the agreed constant and marks the ingest supersede assertion `xfail` until E5 (Task 5d/Task 7 note). Owner: confirm sequencing or the guard constant (the epic's "≥0.6 on short points" band).
- **Q2 (thresholds):** `NOOP_MIN_OVERLAP = 0.45` is a first-cut band. The paraphrase-NOOP band is the one knob that can silently LOSE a point if it misfires (a "paraphrase" that is actually a different fact). Run-protocol steps (50-Q pilot → 500-Q) should validate; the constants are module-level for that tuning. Owner: acceptable to start at 0.45?
- **Q3 (value-signature):** Tier-A value normalization ("6pm"/"six pm", "27:12"/"27m12s") is deterministic-but-curated. Confirm E2's Tier-A points carry enough structure (verbatim value + `when` + state-value marker) for `_value_signature` to key off — or whether the classifier should key off `search_keys` alone initially.
- **Q4 (eval aboutObject parity):** Task 5 adds `aboutObject` writes to `ingest_v2._write_payload` (currently absent). This is a small eval-graph parity fix that E7's entity gate depends on — confirm it doesn't conflict with the #1369 v2-comparability constraint (same retrieval for deterministic vs v2 legs; entity edges are additive to v2's graph only).
- **Q5 (issue-scoping signature):** #1539 has no `<!-- issue-scoping:` comment (requirements were captured in the epic docs + this issue body). The plan-review gate should run with the epic contract as the requirements source.
- **Q6 (LLM resolver model):** the resolver should reuse the SAME model adapter/P2 routing as S1–S4 (temperature 0.0). Confirm it shares the flash model (not a larger model) so capture cost/latency stays bounded — or is a dedicated resolver model wanted (extra latency/cost)?

---

**Next gates:** plan-review (parallel reviewers, fresh context) → executing-plans → commit-workflow. Worktree: `.worktrees/1509-plans` (write-only, no commit from this session).
