---
title: "Epic Plan — #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4"
type: decisions
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-09
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Epic Plan — #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4

**Date:** 2026-08-09
**Status:** draft — substeps 1–8 complete (review gates: 2 rounds)
**Decision context:** CONDITIONAL PROCEED (01-align.md); research brief (02-research-brief.md); scope + 7 high-level E2E (03-scope.md).
**Execution gates:** Gate A (#320 STABLE) + Gate B (calibration milestone) — all child issues blocked on both.

---

## 1. User Journeys

> System actors: **Operator** (human running the mining pipeline), **Agent/CLI user** (querying the graph), **MCP caller** (automation), **Reviewer** (human validating extraction/dedup candidates). This is an API/CLI/MCP feature — no GUI.

### J-1: Batch mine a session corpus (Operator)
| Step | Actor | Action | System response | Exit state |
|---|---|---|---|---|
| 1 | Operator | Run `mine_corpus` over session files (or MCP `tortoise_mine_conversations`) | Pipeline walks files, per-session extraction | Sessions queued |
| 2 | System | For each session: extract Points (Phase 1) + entities (Phase 2) + derive Events | — | Extraction batch complete |
| 3 | System | Resolve entities cross-session (exact→fuzzy→semantic chain) | Objects MERGEd by canonical id; dedup hits logged | Objects created/updated |
| 4 | System | Wire aboutObject/produces/uses; create Points as `status: draft` | — | Wiring complete, EP-safe |
| 5 | System | Emit summary `{sessions, entities, objects, dedup_hits, drafts}` | Operator sees counts | Batch report |
| **Edge cases:** empty corpus (0 sessions → empty report), malformed session (skipped + counted), LLM failure (keyword/rule fallback), duplicate session file (file_hash skip).

### J-2: Verify a dedup candidate (Reviewer)
| Step | Actor | Action | System response | Exit state |
|---|---|---|---|---|
| 1 | Reviewer | Query dedup candidates (`list_dedup_candidates`) | Returns pairs {existing, candidate, similarity, method} | Candidate list |
| 2 | Reviewer | Approve merge | Surviving Object gains `canonical_id`/`resolved_from`; dedupe event logged | Merge recorded |
| 3 | Reviewer | Reject | Candidate stays separate; marked `reviewed` to skip re-surfacing | No merge |
| **Edge cases:** empty queue, candidate already reviewed, merge of Point-level content dedup ("we already decided this") surfaces as read-only link, not destructive merge.

### J-3: Query "what exists" after mining (Agent/CLI user)
| Step | Actor | Action | System response | Exit state |
|---|---|---|---|---|
| 1 | Agent | Search `tortoise_search "port migration"` | Returns Points + Events + Objects | Results |
| 2 | System | AboutObject-connected sessions still ranked (boost query migrated) | — | Ranking intact |
| 3 | Agent | Traverse `get_object(name)` → aboutObject Points → session Events | Connected subgraph | Traversal |
| **Edge cases:** no objects found (empty result), object exists but no Points (orphan — flagged), draft Points hidden from live EP queries.

### J-4: Track a belief over time (Agent/CLI user)
| Step | Actor | Action | System response | Exit state |
|---|---|---|---|---|
| 1 | Agent | Query belief timeline for a topic | Points sorted by `validFrom` with NAND/supersede links | Timeline |
| 2 | Agent | See "16379 (D1) → contradicted 16380 (D2)" | Dated evidence chain | Understanding |
| **Edge cases:** single decision (no timeline), contradictory decisions without dates (validFrom fallback to session ingest).

### J-5: Promote mined content to live (Reviewer)
| Step | Actor | Action | System response | Exit state |
|---|---|---|---|---|
| 1 | Reviewer | List draft extraction Points (`list_drafts` / `list_dedup_candidates` type=content) | Draft Points with provenance + dedup context | Queue |
| 2 | Reviewer | Approve promotion (`promote_point`) | `status: draft → live`; EP run now includes it; searchable; incident draft operators promoted once all endpoints live | Point live |
| 3 | Reviewer | Quarantine recovery: re-review a quarantined batch (drift fixed) | Batch re-runs W-3; on pass → points join draft queue | Batch un-quarantined |
| **Edge cases:** empty queue; point already promoted (no-op); promote on quarantined batch → blocked with `{blocked, reason, batch_id}` (promote_point contract); EP drift on promote → blocked until the batch's W-3 grounding snapshot passes.

### J-6: Automated batch via MCP (MCP caller)
| Step | Actor | Action | System response | Exit state |
|---|---|---|---|---|
| 1 | MCP caller | Invoke `tortoise_mine_conversations` (scheduled) | Batch runs with per-file error reporting | Batch report |
| 2 | System | Partial failure (1 file fails) | `{status:"error", message, failures:[{file,error}]}` non-fatal, rest processed | Degraded success |
| 3 | MCP caller | Query `tortoise_belief_timeline` / search | Dated results | Automation consumes |
| **Edge cases:** full batch failure (all files fail → quarantine, no partial writes); MCP auth failure; rate limits.

---

## 2. Workflows

### W-1: Entity extraction (per session)
```
transcript/document
  → [Phase-1 extractor: Points + operators]          (existing, reused)
  → [NEW Phase-2 entity stage: extend _SemanticStage (extractor.py:636) with span + canonical_candidates prompt params]
  → [rules: issue/PR refs repo#NNN → objectKind workitem] (reuse EXISTING issues/prs metadata regex, session_indexer.py:204 — NOT _graph_entity_keywords, which is a name-substring matcher against existing graph entities, valid only as the known-Object pre-filter for R4 cost control)
  → [resolve each entity: exact → difflib.SequenceMatcher fuzzy → semantic]
       exact / high-confidence (sim ≥ auto_merge_threshold) → AUTO-MERGE + DedupeRecorded log
       ambiguity band (auto_merge_threshold > sim ≥ review_threshold) → J-2 review queue (candidate_type: entity)
       below review_threshold → NEW Object (canonical id)
  → [write: create_object(id=canonical) — extends existing create_object (sdk.py:4089, emits ObjectRegistered event, reuse _connect_issue_objects MERGE-by-id pattern); extraction Objects avoid the status:'live' default via explicit status param]
  → [wire: (Point)-[:aboutObject]->(Object), (Event)-[:aboutObject]->(Object)]
  → [fallback: LLM failure → rule/keyword entity detection only]
```
**Automation points:** batch MCP tool; per-session extraction independent (parallelizable, throttled via `mine_corpus(max_concurrency=...)`). **Manual intervention:** dedup-candidate review queue (J-2) for the ambiguity band and content-dedup candidates. **Failure modes:** LLM parse error → fallback; embedding model unavailable → exact+fuzzy only (no semantic tier); objectKind unknown → `other`; auto-merge false positive → reviewer rejection (`DedupeRejected`/`reviewed:true`).

### W-2: Content dedup ("we already decided this")
```
extracted decision Points (new batch, all draft)
  → [tier 1: content hash vs existing decision Points]   (reuse _content_hash / _content_exists, pointKind-scoped)
  → [tier 2: embedding cosine vs existing decision Points, threshold θ]  (generalized _semantic_dedup: pointKind param, pairs-returning mode)
  → [candidate prior is DRAFT → auto-link draft-to-draft via IMPL "already decided" unidirectional]
  → [candidate prior is LIVE → surface to review queue (no auto-link — a draft must never wire an operator to a live Point)]
  → [hit above merge-threshold: reviewer decides; keep candidate draft until promotion]
```
**Failure modes:** embedding threshold mis-calibrated (θ from calibration milestone); false merge → reviewer rejection path; no embedding model → hash-only dedup (degraded but safe).

### W-3: EP-safe batch commit (extraction batch)
```
batch complete
  → [verify all extraction Points status: draft]
  → [verify no operator auto-wire (SDK #131 override active: create_operator(promote_source=False))]
  → [run EP on live Points only (draft excluded at factor-extraction time — shared filter in TortoiseEP + SVBP + analyze paths)]
  → [snapshot mean grounding pre/post (≤2% mean absolute)]  (Gate B tooling)
  → [pass: batch committed, drafts enter review queue]; [fail: BATCH quarantined (batch-level), no promotion, re-review via J-5]
```
**Failure modes:** any live-status Point found in extraction output → batch block; EP drift >2% → batch quarantine + re-review; quarantine recovery = fix cause + re-run batch (resumable progress file).

### W-4: Temporal belief wiring
```
new decision Point (draft) with validFrom = session date
  → [search prior decision Points on same aboutObject/topic]
  → [contradictory prior is DRAFT → create NAND via create_operator(promote_source=False) — both endpoints draft, operator node itself also status: draft]
  → [contradictory prior is LIVE → do NOT wire at extraction; surface temporal-link candidate to review queue; create NAND only after the new draft is promoted to live (live→live)]
  → [explicit replacement? ("supersede"/"we changed our mind") → supersede_point(CORRECTS) after review (live path)]
  → [else: no temporal edge]
```
**#438 boundary carve-out:** W-4 creates point-to-point NAND/CORRECTS edges between a NEW conversation-extracted draft and EXISTING Points. This is conversation-triggered and draft-side (no discovery over the graph's own structure), so it stays in #264; #438 remains discovery of connections between EXISTING Points without a conversation trigger. The carve-out is auditable in code: TemporalWire only runs inside the mining post-pass with an explicit `source_session` provenance.
**Failure modes:** prior decision not found (partial index — Gate A risk); dates missing → validFrom = session ingest date (documented fallback); live prior → review-queue routing (never a silent skip).

---

## 3. Prototype (markdown diagram — non-GUI feature)

### Pipeline topology (target state)
```
┌─ Session files (~/.tortoise/docs/conversations/) ─┐
│                                                   │
│  ingest_corpus (AgentSession) ──► Event{AgentSession} + metadata   [Phase 1, #320]
│  mine_conversation (ConversationMiner) ──► Points + Operators + Events  [Phase 1, #416]
│                                                 │
│  [NEW Phase 2] EntityStage ──► entities ──► resolve(exact→fuzzy→semantic)
│                                        └─► create_object(MERGE canonical_id)
│                                        └─► aboutObject wiring
│  [NEW Phase 2] ContentDedup ──► tier1 hash ──► tier2 embedding ──► candidates→review
│  [NEW Phase 4] EpSafeCommit ──► draft-only, EP excludes draft, grounding snapshot
│  [NEW Phase 4] TemporalWire ──► validFrom + NAND + supersede(CORRECTS)
│  [FIX] _connect_issue_objects ──► aboutObject (was INSTANTIATES)
│
└─► FalkorDB graph: Object / Point / Event / Source ──► tortoise_search / analyze / EP
```

### State machine (extraction Point lifecycle)
```
extracted (draft) ──► review queue ──► live        [promotion ONLY via explicit reviewer approval]
     └── batch quarantine (EP drift / batch fail) ──► re-review ──► re-run batch (resumable)
```
**Draft→live:** ONLY via `promote_point` (reviewer-approved API), never via SDK #131 edge auto-promotion for extraction paths (`create_operator(promote_source=False)`).
**Status vocabulary:** stored status stays within existing `POINT_STATUS_VALUES = {live, draft, outdated, archived}` (sdk.py:28) — **no new stored status**. "Reviewed" is a DERIVED flag (a `ReviewRecorded` event / `reviewed: true` property), not a stored status. Quarantine is BATCH-level (W-3), not a Point state — a quarantined batch's Points stay `draft` until re-review.

---

## 4. Data Model

> No new node types — reuses ONTOLOGY v3.2 entities. Changes are new properties, edges, and lifecycle rules.

### 4.1 Object (existing, new write semantics)
- `id`: deterministic canonical id = `obj_` + sha256(normalized_canonical_name)[:12] via the existing content-hash helper (`ids.content_hash`, domain-separated input `f"obj:{normalized}"` — no third sha256 helper; consolidate `_content_hash`/`ids.content_hash` duplication opportunistically).
- `canonical_name`: normalized entity name (lowercase, whitespace-collapsed, punctuation-stripped for matching; display `title` preserves original).
- `resolved_from`: list of pre-merge names (dedup audit trail) — set on merge.
- `objectKind`: existing vocab (Project, WorkItem, document, tag, user, skill, tool, agent, workflow, agreement, standard, other).
- No stored `status` (derived per ONTOLOGY §2/§4.3 — projected from event stream at query time; mirrors §3 state machine in this plan).

### 4.2 Point (existing, new extraction rules)
- `status`: `draft` on creation by extraction (explicit output contract). **Promotion to `live` ONLY via `promote_point` — the SDK #131 auto-promotion (`create_operator` SET s.status='live', sdk.py:1010-1012) is bypassed for extraction paths via `create_operator(promote_source=False)`.** No new stored status values (POINT_STATUS_VALUES unchanged); "reviewed" is a derived flag (`reviewed: true` property or `ReviewRecorded` event).
- Dedup/batch properties (pending-queue persistence, §4.4): `dedup_candidate: true`, `dedup_method`, `dedup_similarity`, `dedup_target_id` on candidate Points; `batch_id` on every extraction Point (quarantine lock + stranded-batch detection).
- `validFrom`: real session date (from session frontmatter `date`/`startedAt`; fallback `ingestedAt`).
- `pointKind`: existing vocab (decision, observation, hypothesis, statement…).
- Provenance: `extractedFrom` → Source; `aboutEvent` → session Event (Phase 4).

### 4.3 Edges (existing predicates — no new predicates)
| Edge | From→To | Used for | Notes |
|---|---|---|---|
| `aboutObject` | Point/Event → Object | entity wiring | replaces INSTANTIATES for issue/PR |
| `aboutEvent` | Point → Event | session occurrence provenance | in-scope Phase 4 |
| `produces` | Event → Object (artifacts); Event → Point (decision claims, #531 pattern) | procedural wiring | §3.5 + #531 |
| `uses` | Event → Object | tool/artifact consumption | §3.5 |
| `IMPL`/`NAND` | Point → Point | content dedup link ("already decided"), contradiction, temporal | draft-side only until review |
| `CORRECTS` | Point → Point | supersede (explicit replacement) | existing `supersede_point` |
| `references` | Source → Entity | ingest provenance | existing |
| `extractedFrom` | Point → Source | claim provenance | existing |

### 4.4 Constraints
- **No `INSTANTIATES` writes** anywhere (removed #214) — `_connect_issue_objects`, `session_indexer.py`, `ranking.py`, `security.py` migrated.
- **Extraction never auto-wires operators to live Points** — operator edges from extraction connect only draft endpoints, created via `create_operator(promote_source=False)`; content dedup links draft-to-draft only (live prior → review queue, W-2); temporal NAND links draft-to-draft only (live prior → review queue, W-4).
- **Deterministic canonical id, single scheme:** `obj_` + sha256(normalized canonical name)[:12] via the existing content-hash helper (domain-separated input); the resolver's exact tier ALSO recognizes legacy `issue_*`/`pr_*` prefixed ids (from `_connect_issue_objects`, sdk.py:4336) so the same entity never becomes two Objects across paths.
- Dedup artifacts: `DedupeRecorded` event in event log (type `DedupeRecorded`) OR `canonical_id`/`resolved_from` property — E2E-2 asserts at least one. Rejection state: `DedupeRejected` event type OR `reviewed: true` property on the candidate (J-2 skip-re-surfacing).

### 4.5 EP integrity
- **Draft exclusion at FACTOR-EXTRACTION TIME (not just TortoiseEP):** a shared status filter (`status <> 'draft'`) applied in ALL of: `TortoiseEP._affected_claims`/`_affected_factors` (ep.py:338/363) — filter draft as targets AND strip draft ids from `input_ids` AND filter draft operator sources (`o.status <> 'draft'`); `extract_svbp_factors` (projection/__init__.py:907 — graph-wide, currently no filter); `_bfs_select_operators` (analyze.py:326); `_select_subgraph` (sdk.py:1832). A draft-connected operator must change NO live claim's posterior. **Operator nodes created by extraction ALSO carry `status: 'draft'`** (today `create_operator` writes no status property, sdk.py:1003, and the event path defaults to `live` via `coalesce($st, n.status, 'live')`, projection/entities.py:113) — `create_operator(promote_source=False)` sets both the operator node's status AND skips the #131 source promotion. The shared filter is parameterized: `_live_only(clause, include_draft=False)` so `run(include_draft=True)` can re-include drafts consistently across all four call sites.
- **Behavior-change note (not back-compat):** flipping EP's default to exclude drafts IS an intentional behavior change — `create_point` defaults new Points to `status: 'draft'` (sdk.py:456) and current EP applies no status filter, so any existing draft Point wired into an operator chain currently contributes to posteriors. The flip is gated by Gate B (drift check ≤2% mean grounding); callers that need legacy behavior pass `include_draft=True`.
- Grounding snapshot: `analyze.py` gains a pre/post batch mean-grounding query (Gate B tooling; sample = full live Point set).

---

## 5. Architecture

### 5.1 Components
| Component | File | Responsibility | New/Existing |
|---|---|---|---|
| EntityStage | extend `tortoise/extractor.py` `_SemanticStage` (extractor.py:636) — add span + canonical_candidates prompt params | LLM entity extraction (domain-aware kind vocab via domain_loader) + rules pre-filter via existing issues/prs metadata (session_indexer.py:204) | EXTEND |
| EntityResolver | `tortoise/entity_resolver.py` (new) | exact→fuzzy (difflib.SequenceMatcher)→semantic chain; canonical id via existing ids.content_hash | NEW |
| ContentDedup | extend `tortoise/sdk.py` `_semantic_dedup` path | tier-1 hash + tier-2 embedding for decision Points | EXTEND |
| EpSafeCommit | `tortoise/mining.py` + `tortoise/ep.py` | draft lifecycle, EP draft filter, grounding snapshot | NEW+MOD |
| TemporalWire | `tortoise/mining.py` (post-pass) | validFrom + NAND + supersede | NEW |
| Wiring fix | `tortoise/sdk.py`, `tortoise/session_indexer.py`, `tortoise/ranking.py`, `tortoise/security.py` | INSTANTIATES → aboutObject (ranking rewrite Event-anchored) | MOD |
| Structural wiring (produces/uses) | existing #531 capability (sdk.py:1582 Event→Point produces; projection/entities.py Event-dict-driven produces/uses Event→Object) + W-1/W-3 mining step that emits these edges | produce/use edges from mined sessions | OWN+WIRE |
| Promotion | `tortoise/sdk.py` (new `promote_point`) | draft→live reviewer-gated promotion; **promotes incident draft operators once all their endpoints are live** (zombie-operator prevention); batch quarantine lock; returns {blocked, reason, batch_id} | NEW |
| Gates | workflow layer (issue-workflow skill / CLI helper — NOT core SDK) | `check_gates(child_issue)` reads GitHub deps; SDK exposes only local `calibration_passed()` marker | NEW |
| MCP surface | `tortoise/mcp_server.py` | `tortoise_mine_conversations`, `tortoise_list_dedup_candidates`, `tortoise_approve_merge`, `tortoise_promote_point`, `tortoise_belief_timeline` | NEW |

### 5.2 Boundaries
- **Extraction (Phase 2) vs discovery (#438):** #264 writes Objects/Points from conversation text; #438 discovers IMPL/NAND between existing Points. No shared component; `entity_resolver` is not a graph-walking connector.
- **Dedup vs EP:** content dedup links draft Points only; promotion to live is a separate reviewer-gated step. EP never sees drafts.
- **Wiring vs ingestion:** `ingest_corpus` keeps file-level indexing; entity wiring is a post-extraction pass (idempotent MERGE).
- **Failure isolation:** LLM failure → rule fallback; embedding failure → exact+fuzzy; batch failure → quarantine (no partial live writes).

### 5.3 Deployment
- Same package (`pip install -e .`); no new services. Batch via MCP/CLI; no real-time requirement (scope non-goal).
- Calibration milestone runs in an isolated batch (FalkorDBLite or a staging graph) before touching production graph (Gate B).

---

## 6. Interfaces (contract-first)

### 6.1 Python API (SDK)
```
# Phase 2
# RENAMED to avoid collision with extractor.py:861 extract_entities (S7 document semantics)
def extract_conversation_entities(self, transcript: str, source_id: str, api: EventAPI,
                                  *, model=None, domain: str | None = None,
                                  entity_stage=None) -> list[dict]:
    """→ [{name, canonical_candidates, objectKind, span, confidence}]  (Object-only; S7 extract_entities untouched)
    entity_stage: injectable deterministic mock for tests (EntityStageMock); None = LLM stage"""
def resolve_entity(self, name: str, *, objectKind: str = "other") -> dict:
    """→ {object_id, canonical_name, resolution: exact|fuzzy|semantic|new, similarity}
    exact tier recognizes legacy issue_*/pr_* ids (single canonical scheme, §4.4)"""
def create_object(self, name: str, objectKind: str = "other", *, id: str | None = None, **props) -> dict:
    """extended: id override for deterministic canonical id (obj_sha256); default ulid() unchanged"""
def create_operator(self, ..., *, promote_source: bool = True) -> dict:
    """extended: promote_source=False for extraction paths — operator node created with status:'draft',
    draft endpoints stay draft (bypasses #131 source promotion AND the event-path live default, entities.py:113)"""
def mine_conversation(..., extract_entities: bool = True, entity_stage=None,
                      content_dedup: bool = True, dedup_threshold: float | None = None) -> dict:
    """extended, BACK-COMPAT: retains existing keys {events, points, operators, event_ids} + adds {entities, objects, dedup_hits, drafts}
    content_dedup: always-on for decision Points by default (θ from calibration milestone);
    explicit param for tests to pin threshold (DE2E-3) or disable"""
def mine_corpus(self, directory: str, *, extract_entities: bool = True, progress_file: str | None = None) -> dict:
    """→ {sessions, ingested, updated, skipped, failed, entities, objects, dedup_hits, drafts, errors:[{file,error,retryable}]}"""
def quarantine_batch(self, batch_id: str, *, reason: str) -> dict:
    """test/ops primitive: mark a batch quarantined (blocks promote_point); un-quarantine on re-run pass"""
def check_gates(self, child_issue: str) -> dict:
    """→ {blocked: bool, reasons: ["#320 not STABLE", "calibration not passed"]} — reads GitHub deps (DE2E-7 contract)"""

# Phase 4
promote_point(self, point_id: str) -> dict          # draft→live, reviewer-gated; blocks if batch quarantined;
                                                     # returns {blocked, reason, batch_id} on block;
                                                     # promotes incident draft operators once ALL endpoints live (zombie prevention)
list_drafts(self, *, limit: int = 50) -> list[dict] # {id, content, pointKind, provenance, dedup_context, batch_id}
def list_dedup_candidates(self, *, limit: int = 50, candidate_type: str = "entity") -> list[dict]:
    """→ [{candidate_id, existing_id, similarity, method, candidate_type: entity|content, status: pending|reviewed|merged}]"""
def approve_merge(self, candidate_id: str, target_id: str, *, action: "merge"|"reject") -> dict:
    """merge → canonical_id/resolved_from + DedupeRecorded; reject → DedupeRejected / reviewed:true"""
def belief_timeline(self, topic: str) -> list[dict]:
    """→ [{content, pointKind, validFrom, status, linked_by: NAND|CORRECTS, related}]"""
```

### 6.2 MCP tools
| Tool | Inputs | Output |
|---|---|---|
| `tortoise_mine_conversations` | `directory`, `extract_entities`=true, `llm_model`, `progress_file` | batch summary + per-file failures |
| `tortoise_list_dedup_candidates` | `limit`, `candidate_type` | candidate list |
| `tortoise_approve_merge` | `candidate_id`, `target_id`, `action` | merge result |
| `tortoise_promote_point` | `point_id` | promotion result (reviewer-gated) |
| `tortoise_belief_timeline` | `topic` | dated belief chain |

### 6.3 Internal contracts
- `_semantic_dedup` generalization (sdk.py:2348 — currently hardcoded to `pointKind:'checkpoint-item'`, returns below-threshold candidates only): add `pointKind` param + `return_pairs: bool` mode (returns {candidate, existing, similarity} pairs for above-threshold hits) + `similarity_out`. W-2 tier 2 calls with `pointKind='decision'`, `return_pairs=True`, threshold θ from calibration milestone.
- `_content_exists` (sdk.py:2260 — currently matches ANY non-operator Point by content_hash): add optional `pointKind` scoping so a duplicate observation never suppresses a decision (W-2 tier 1).
- `ep.py`/SVBP draft filter: shared `_live_only(clause, include_draft=False)` predicate at factor-extraction time (signature matches §4.5; the clause is parameterized across ALL four call sites — TortoiseEP._affected_claims/_affected_factors, extract_svbp_factors, _bfs_select_operators, _select_subgraph); `run(operator_ids, ..., include_draft: bool = False)` default excludes draft. **Signature back-compatible** (all existing callers — dream.py:80, sdk.py:1913, ingest.py:103/573 — call without include_draft) but **behaviorally a gated change** (see §4.5 note; Gate B drift check).
- Error responses: all MCP tools return `{status: "error", message}` on failure; batch tools report per-file failures non-fatally; full-batch failure → quarantine, no partial writes.
- **Gate state (Gates A+B) is workflow-layer state:** `check_gates(child_issue)` lives in the issue-workflow skill / CLI helper (already GitHub-coupled), NOT the core SDK. The SDK exposes only a local `calibration_passed()` marker (reads a stored milestone marker) so DE2E-7 tests the local contract without a GitHub dependency.
- **Sequencing (per-issue gates, not blanket):** (1) Gate B tooling (mean_grounding + drift snapshot) runs first, ungated; (2) EP draft filter + draft-operator status + DE2E-4 — ungated (FalkorDBLite-only, de-risks the nuclear R1/R7/R8); (3) INSTANTIATES→aboutObject wiring fix + ranking + DE2E-5 — ungated (self-contained, R3); then #320-dependent work (extraction stage, resolver, dedup) gated on Gate A; temporal + promotion + MCP follow. Each child issue carries its own gate list.
- Ranking migration shape (ranking.py:245): rewrite `INSTANTIATES` count as Event-anchored `MATCH (e:Event)-[:aboutObject]->(o:Object)` — Point/Document-origin aboutObject edges must not inflate session boosts.

---

## 7. Detailed E2E Test Cases

> Aligned 1:1 with scope E2E-1..7. Each is implementable as an automated pytest against FalkorDBLite.

**Deterministic-fixture preamble (applies to ALL extraction tests):** Phase-2 entity extraction is LLM-driven. Every DE2E that exercises entity extraction injects a **deterministic entity-stage mock** (new `EntityStageMock` returning fixed `{name, objectKind, canonical_candidates}` sets per seed transcript — same pattern as `MockExtractor` in extractor.py:206) via `mine_conversation(..., entity_stage=EntityStageMock)`. LLM-dependent outcomes are NEVER asserted against live model output. Embedding similarity in the semantic tier is mocked to fixed values where tier behavior is asserted (no dependence on model availability). Threshold constants are PINNED in a test fixture (`AUTO_MERGE_THRESHOLD=0.92`, `REVIEW_THRESHOLD=0.60`) — calibration keeps production values within the pinned band; tests assert against pinned values only.

### DE2E-1: Session → Entity Objects with provenance
**Setup:** FalkorDBLite; seed one mined session transcript (contains "port 16379", "FalkorDB", "tortoise#123"); `EntityStageMock` returns {port 16379 → other, FalkorDB → tool, tortoise#123 → workitem}.
**Steps:**
1. Run `mine_conversation(transcript, "s1", api, extract_entities=True, entity_stage=EntityStageMock)`.
2. Query `MATCH (o:Object) WHERE o.canonical_name IN ["port 16379","falkordb","tortoise#123"] RETURN o`.
3. Query `(p:Point)-[:aboutObject]->(o:Object)` and `(e:Event)-[:aboutObject]->(o:Object)`.
4. Query `MATCH (p:Point)-[:aboutEvent]->(e:Event)` for session-occurrence Points (provenance anchor).
5. Query `(p:Point)-[:extractedFrom]->(:Source)-[:references]->(:Document|Event)` full chain.
6. Assert `NOT EXISTS (s:Subject {name:"port 16379"})` and no `:Subject` stub for any extracted entity.
**Assertions:** ≥1 Object per entity (objectKind tool/other/workitem); aboutObject edges exist Point+Event side; aboutEvent edges exist for occurrence Points; full provenance chain `extractedFrom → references` present; no Subject stubs.

### DE2E-2: Cross-session entity dedup
**Setup:** transcripts A ("port migration"), B ("port 16379 change"), same effort — `EntityStageMock` returns canonical candidates that resolve the same entity; PLUS a legacy session ingested via `ingest_corpus` with an issue ref (exercises legacy `issue_*` id path). A third entity pair sits in the ambiguity band (sim pinned 0.75, between REVIEW 0.60 and AUTO 0.92).
**Steps:** mine A and B; count `:Object` nodes whose canonical_name resolves to the same entity; check `DedupeRecorded` log event or `resolved_from` property on survivor; re-run mining of B → no new Object; verify a legacy `issue_*`-id Object and a new-scheme Object for the same entity are recognized as the same by the exact tier; run `list_dedup_candidates(candidate_type="entity")` for the ambiguity-band pair; `approve_merge(action="merge")` on it; then `approve_merge(action="reject")` on a second ambiguity pair.
**Assertions:** exactly ONE Object for the entity across BOTH paths; both sessions' Points wire via aboutObject; dedup artifact exists (DedupeRecorded/canonical_id/resolved_from); idempotent (re-mine adds 0 Objects); legacy/new canonical schemes unified; ambiguity-band pair surfaces as `candidate_type: entity`; merge → `canonical_id`/`resolved_from` + DedupeRecorded; **reject → candidate stays a separate Object, no canonical_id/resolved_from written, `reviewed:true`/`DedupeRejected` present, `list_dedup_candidates` no longer returns it**.

### DE2E-3: Content dedup — "we already decided this"
**Setup:** prior session D1 contains decision "change default port to 16379" (LIVE, post-review); new session D2 restates it verbatim (hash-tier detectable); D2b paraphrase variant (embedding tier, mocked sim 0.88).
**Steps:** mine D2 with content dedup (tier1 hash + tier2 embedding, pointKind='decision'); check D2's decision Point is draft; query candidates; re-run mining of D2 (idempotency).
**Assertions:** no new live decision Point created for the duplicate; candidate surfaced with method and `candidate_type: content`; D2 Point remains `draft`; because D1 is LIVE, NO IMPL operator auto-wired from D2 to D1 (W-2 live-prior rule) — link is pending review via the candidate queue; **re-run → candidate count unchanged, no duplicate IMPL link, no new DedupeRecorded event**.
**Variant A (draft-to-draft):** D1 still draft (fixture: mined and NOT promoted via `create_point(status="draft")`) → an IMPL "already decided" unidirectional operator links D2→D1 via `create_operator(promote_source=False)` and BOTH remain draft.
**Variant B (reject):** `approve_merge(action="reject")` on the content candidate → D2 stays separate, `reviewed:true`, not re-surfaced.
**Variant C (approve vs live prior):** `approve_merge(action="merge")` on the content candidate where D1 is LIVE → NON-destructive: no Object merge (Points don't merge), the IMPL "already decided" link is scheduled and wired at D2's PROMOTION time (live→live, per W-2 approve semantics); assert after promote: exactly one IMPL wired, both Points live, no duplicate decision Point.

### DE2E-4: Extracted Points are EP-safe (non-vacuous)
**Setup:** batch of 5 sessions mined; ≥2 sessions contain contradictory decisions (so a W-4 NAND exists between two draft Points); a DELIBERATE LEAK fixture: a pre-existing LIVE operator from a live claim to one draft Point is constructed; EP run with `include_draft=False`; mean-grounding snapshot query (new `mean_grounding()` helper, formula: mean over `confidence` of live non-operator Points, sampled pre/post batch).
**Steps:** assert all extraction Points `status: draft`; assert all extraction-created OPERATOR nodes have `status: 'draft'`; assert no draft-to-live wiring; snapshot mean grounding before/after; run EP on the leak graph with `include_draft=False` AND a CONTROL run with `include_draft=True`; run SVBP path (`extract_svbp_factors`) with a draft in the factor universe; run `_bfs_select_operators` (analyze.py) and `_select_subgraph` (sdk.py) paths with a draft in scope.
**Assertions:** 100% extraction Points + operator nodes draft; grounding delta ≤2% mean absolute; **leak scenario: with include_draft=False the live claim's posterior is invariant; with include_draft=True the same graph CHANGES the posterior — proving the filter (not the wiring) is causal**; SVBP path AND `_bfs_select_operators` AND `_select_subgraph` independently exclude drafts (all four §4.5 call sites tested, not just TortoiseEP+SVBP); W-4 NAND between two draft Points leaves both `draft`.

### DE2E-5: produces/uses wiring + INSTANTIATES drift removal
**Setup:** session with decision + edited artifact (`redis.conf`, EntityStageMock returns it as objectKind document); issue/PR-bearing session through `ingest_corpus(eventKind="AgentSession")`.
**Steps:** mine session; run ingest path; grep graph for `INSTANTIATES`; run ranking query on aboutObject-connected session; check security whitelist.
**Assertions:** decision Event `produces` decision Point; session Event `uses` artifact Object; zero `INSTANTIATES` edges on both paths; **aboutObject session appears in top-5 ranking results AND ranking score with aboutObject edges > score without them (observable baseline)**; whitelist has no INSTANTIATES.
**Variant (Point-origin negative):** a graph with ONLY Point-origin aboutObject edges (no Event-origin) → session boost UNCHANGED (Point/Document-origin aboutObject edges must not inflate session boosts — Event-anchored rewrite, §6.3).

### DE2E-6: Temporal belief tracking
**Setup:** D1 "use port 16379" (date T1), D2 "revert to 16380, 16379 was wrong" (date T2>T1); D3 explicit replacement case "supersede the port decision".
**Steps:** mine all; query belief timeline.
**Assertions:** D1/D2 linked by NAND; validFrom=T1/T2 on respective Points; timeline shows ordered chain; D3 branch → CORRECTS + D1 `outdated:true` via `supersede_point`.
**Variant (live prior):** D1 promoted to live first, then D2 mined — D2's temporal candidate surfaces to the review queue (no NAND wired at extraction, W-4 live-prior rule); after D2 is promoted, the NAND is created live→live and the timeline shows both.
**Negative:** session with NO frontmatter date → validFrom == ingestedAt (documented fallback).

### DE2E-7: Gate gating (pytest-level contract)
**Setup:** `check_gates(child_issue)` is a WORKFLOW-LAYER helper (issue-workflow skill / CLI — GitHub-coupled); the SDK exposes local `calibration_passed()` marker. Unit-test `calibration_passed()` with mocked milestone marker set/unset; test `check_gates` in the CLI helper with mocked #320/calibration states.
**Steps:** call `calibration_passed()` with marker absent → False; marker present → True; invoke `check_gates` with #320 open + calibration open → blocked; both closed → clear.
**Assertions:** `check_gates` returns blocked=true with reasons when either gate open; blocked=false when both closed; the workflow refuses to start implementation while blocked; no graph writes. (Full issue-workflow skill integration verified manually at execution time — the pytest contract is `calibration_passed` + `check_gates` CLI unit.)

### DE2E-8: Promotion gate + quarantine recovery + zombie operators
**Setup:** extraction batch produced draft Points; one quarantined batch exists via explicit `quarantine_batch(batch_id)` API (test-side primitive, §6.1) OR by forcing the documented trigger (EP drift >2% via the leak fixture); one draft-to-draft NAND between two draft Points exists (W-4).
**Steps:** call `promote_point` on a draft; call `promote_point` on a Point in a quarantined batch; fix the drift cause; re-run the quarantined batch via resumable progress file; assert un-quarantine; query EP/search for the promoted Point; promote BOTH endpoints of the draft NAND.
**Assertions:** promoted Point becomes `live` and appears in EP + search; promote on quarantined batch is BLOCKED with `{blocked, reason, batch_id}`; draft Points without promotion stay invisible to EP and search; **recovery loop: batch un-quarantined after re-run passes W-3, its Points enter the review queue and remain draft until promotion**; **after both NAND endpoints promoted, the incident operator node is ALSO live (no zombie draft operator — EP now propagates the contradiction)**.

### Negative cases (each in DE2E format, deterministic fixtures)
- **DE2E-N1 Malformed session** (missing frontmatter, unparseable body) → skipped, counted in `failed`, batch continues.
- **DE2E-N2 LLM extraction failure** (mock that raises on first call) → rule/keyword fallback produces entities or empty list, no crash; fallback correctness asserted (known refs extracted).
- **DE2E-N3 Embedding model unavailable** (mock raises on embedding) → exact+fuzzy only, semantic tier skipped with log entry.
- **DE2E-N4 Dedup candidate already reviewed** → not re-surfaced.
- **DE2E-N5 Empty corpus** → `{sessions:0, ...}` no error.
- **DE2E-N6 Full-batch failure** → batch quarantined, no partial live writes.
- **DE2E-N7 objectKind unknown** (mock returns "wibble") → Object created with `objectKind: "other"`, no error.
- **DE2E-N8 Duplicate session file** (same file mined twice via `mine_corpus`) → second run reports `skipped` via file_hash, adds no new entities/objects.
- **DE2E-N9 Promote already-live Point** → no-op, no error.
- **DE2E-N10 Empty dedup candidate queue** → `list_dedup_candidates` returns empty list.
- **DE2E-N11 `_content_exists` pointKind scoping** → a duplicate observation Point never suppresses a decision Point (W-2 tier 1 scoping works).
- **DE2E-N12 `extract_conversation_entities` vs S7 `extract_entities`** → both coexist; no signature clash (renamed).
- **DE2E-N13 `_semantic_dedup` back-compat** → existing checkpoint-item callers (default params) get identical behavior after generalization (R14).

---

## 8. Coherence Review + Risk Analysis

### 8.1 Cross-substep drift checkpoints
- **Journey↔Workflow↔DE2E mapping (explicit):**
  | Journey | Workflow | DE2E |
  |---|---|---|
  | J-1 batch mine | W-1 entity extraction, W-2 dedup, W-3 EP-safe commit | DE2E-1, 2, 3, 4, 7 |
  | J-2 dedup review | W-1 ambiguity-band routing, W-2 | DE2E-2, 3 |
  | J-3 query exists | W-1 wiring, W-3, ranking migration | DE2E-5 |
  | J-4 belief timeline | W-4 temporal | DE2E-6 |
  | J-5 promotion | W-3 post-pass (promotion gate) | DE2E-8 |
  | J-6 MCP automation | W-1..W-4 via MCP | DE2E-7, negative cases |
- Scope E2E-1..7 ↔ Detailed DE2E-1..8: 1:1 correspondence (scope E2E-5's ingest-path assertion carried into DE2E-5; scope E2E-6's CORRECTS branch in DE2E-6; promotion gate added as DE2E-8 from reviewer feedback).
- Data model §4 ↔ Interfaces §6: `canonical_name`/`resolved_from`/`validFrom`/`candidate_type`/`reviewed` properties referenced in both; no interface references an undefined field; `promote_point`/`promote_source`/`create_object(id=)`/`mine_corpus` contracts defined.
- Architecture §5 ↔ Data model §4: EntityResolver writes Objects (single canonical scheme); EpSafeCommit owns the draft filter at factor-extraction time (TortoiseEP + SVBP + analyze); no ownership overlap.

### 8.2 Risks + Mitigations
| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Graph pollution from low-quality extraction degrades EP (nuclear risk) | High | Draft-status Points excluded from EP; no auto-wire; calibration Gate B before batch; quarantine on drift |
| R2 | Dedup false merges create wrong canonical Objects | High | Review queue for ambiguity band; `resolved_from` audit; exact/fuzzy/semantic short-circuit; Gate B threshold calibration |
| R3 | INSTANTIATES→aboutObject migration breaks search ranking silently | Medium | DE2E-5 ranking assertion; ranking.py query updated in same PR as wiring change |
| R4 | LLM cost at 4,190 sessions × entity passes | Medium | Two-tier (rules first for known refs, LLM for open entities); batch throttled; resumable progress file |
| R5 | Session `startedAt` is ingest time, not real session time | Medium | Read real date from session frontmatter for validFrom; fallback documented; DE2E-6 asserts dates |
| R6 | #320 incomplete → dedup over partial universe (Gate A) | High | Child issues blocked on #320; no execution before STABLE |
| R7 | SDK #131 auto-promotion defeats draft mitigation | High | `create_operator(promote_source=False)` for extraction; EP source-side draft filter as second barrier; DE2E-4 asserts both |
| R8 | EP draft leak via SVBP/analyze paths (filter scoped to TortoiseEP only) | High | Shared live-only filter at factor-extraction time in ALL paths (§4.5); DE2E-4 asserts SVBP + TortoiseEP |
| R9 | Legacy vs new canonical id divergence → duplicate Objects | Medium | Single `obj_sha256` scheme + legacy `issue_*`/`pr_*` recognition in exact tier; DE2E-2 asserts |
| R10 | Promotion gate missing → mined content never goes live (EP/search dead end) | Medium | `promote_point` + `tortoise_promote_point` + J-5 + DE2E-8 |
| R11 | Live contradictory prior: temporal NAND never wired (silent gap) or wired draft→live (pollution) | Medium | W-4 live-prior rule (review-queue routing, wire after promotion); DE2E-6 live-prior variant |
| R12 | EP default flip (exclude drafts) changes existing posteriors | Medium | Behavior change gated by Gate B drift check (≤2% mean AND ≤5% max single-point delta — mean-absolute can mask per-point flips); legacy callers pass include_draft=True |
| R13 | Content-dedup approve against LIVE prior undefined | Medium | Approve semantics for candidate_type:content = non-destructive IMPL wired at PROMOTION time (not merge); DE2E-3 approve variant |
| R14 | _semantic_dedup generalization regresses checkpoint-item callers | Medium | Default-param back-compat DE2E (existing checkpoint callers get identical behavior); risk surfaced in §8.3 |
| R15 | Review-queue backlog at 4,190 sessions | Medium | Queue size (band width) is a calibration target — band tuned to bound daily candidate volume |
| R16 | Zombie draft operators (promoted endpoints, draft operator → contradiction never propagates) | Medium | promote_point promotes incident operators once all endpoints live; DE2E-8 asserts |
| R17 | mine_corpus duplicates security-sensitive ingest machinery | Medium | mine_corpus COMPOSES ingest_corpus (security, resume, file_hash) — no parallel walker |

### 8.3 Improvement opportunities
- Reuse `_semantic_dedup` (not reinvent) — reduces Phase 2 scope to adapter+threshold.
- Entity pre-filter via existing issues/prs metadata (session_indexer.py:204) cuts LLM calls for known refs.
- Batch summary report gives operators a pollution health check per run (early drift detection).
- EntityStage extends `_SemanticStage` (domain-aware kinds) instead of a parallel stage.
- `mine_corpus` composes `ingest_corpus` (security, resume, file_hash) instead of a second walker.

### 8.4 Additive drift acknowledged (coherence reviewer, accepted)
- `check_gates`/`quarantine_batch`/`list_quarantined`/`calibration_passed` are NOT in scope (03-scope.md) — they are justified additions: quarantine implements W-3's drift-fail path; check_gates makes E2E-7 pytest-testable; DE2E-8 acknowledges the promotion gate. `check_gates` stays in the workflow layer (no new SDK GitHub dependency); SDK exposes only the local `calibration_passed()` marker. Scope E2E-7's "no graph writes until gates satisfied" is enforced by the workflow refusing to start (DE2E-7 unit-tests `calibration_passed` + CLI `check_gates`; full skill integration verified at execution time).
- Scope E2E-4 wording amended: "no auto-wiring to LIVE Points" (draft-to-draft permitted with draft operator nodes under EP draft exclusion) — narrows the align guard ("no auto-mitigation wiring from extraction") with justification: draft operators are EP-inert (excluded at factor-extraction time, §4.5), so auto-linking drafts to drafts does not propagate pollution; it surfaces contradiction links for review while keeping the graph safe.
- NOT-NOW (recorded, no scope creep): autonomous high-confidence merges (post-calibration), alias/co-reference lexicon (v2), batch-optimized entity pre-filter, whole-session near-dup detection, reviewer UI for candidate queue, one-time backfill of 4,190 historical sessions.
