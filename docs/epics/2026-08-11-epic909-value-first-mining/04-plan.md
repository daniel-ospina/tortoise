---
title: "Epic Plan — #909: Value-first mining system (v1)"
type: plan
domain: engineering
doc_status: draft
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
inputs: DESIGN.md (canonical), scope.md, research-r6-expansion-pack-mapping.md, research-r8-eval-thresholds-commit-endpoint.md, research-addendum-nand-policy-amendments.md, spec-classification-model.md, 2026-08-09-mining-system-requirements.md (R1-R9)
---

# Epic Plan — #909: Value-first mining system (v1)

> Plan stage output for epic #909. Eight substeps: User Journeys → Workflows →
> Prototype → Data Model → Architecture → Interfaces → Detailed E2E → Coherence Review.
> The DESIGN.md is the canonical design; this plan makes it buildable. Build order
> (from scope.md §5, dependency-ordered, gate first) is restated in §8.3.

**The hard gate (from align + scope):** the 2-window rubric validation is deliverable #1.
No extraction implementation proceeds until the rubric reproduces on the owner's real
workload. Window #1 is converged (mitigation coverage 0→29→100%); **window #2 remains to
be run** — scheduled in §2 W-6 and §8.1 as the first action of the build, before slice 6
(extractor) starts.

**Plan-stage resolutions PL1-PL4 (resolving gaps between the research docs and the codebase —
recorded here so implementation does not re-derive):**
- **PL1.** MITIGATES is a first-class payload op_type — edge-targeted via the
  (src, dst, op_type) identity triple (operator MERGE key; NO `op_<sha>` ids —
  `create_operator` hardcodes ULIDs and that is unchanged). Server-side maps to the
  EXISTING `mitigate_operator` mechanism: mitigation Point + `(m)-[:IMPL]->(op)` +
  `(op)-[:mitigated_by]->(m)`, `mitigation_strength` 0-1 (extractor bias 0.10-0.50
  maps into it). Not eval-only.
- **PL2.** L2 re-capture supersession maps to the EXISTING `supersede_point` mechanism
  (CORRECTS edge + `outdated` flag + edge transfer). The payload `reason: REVISES` is a
  semantic label; there is NO new REVISES edge in v1. `REPHRASE` is a dedup label only
  (no graph operator — matches scope.md §1).
- **PL3.** Hold queue is client-side in v1 (items returned in `held[]`, never dropped,
  never silently written). A commit with held items is NOT an L1 replay: re-submitting
  the same `client_commit_id` writes the held items (L1 `duplicate` applies only to
  fully-written commits). **Promotion semantics:** a re-submission of a previously-held
  payload is checked against the 50 CEILING only (already-written + delta ≤ 50 →
  write); the hard-25 band does NOT re-trigger for previously-held payloads (they were
  already adjudicated — the hold is a deferral, not a re-adjudication). Ceiling-raise
  /promote endpoint is v1.1.
- **PL4.** `write_ops` is billed +1 per NON-DUPLICATE commit call, EXCEPT overflow-to-hold
  commits which bill zero (their re-submission bills the single +1 — one logical
  payload is billed exactly once). An L1 replay bills zero write-ops (never
  double-charged). `nodes_written` += net-new non-episodic.

---

## 1. User Journeys

### 1.1 Personas

| Id | Persona | Context | Surface | Primary needs |
|---|---|---|---|---|
| P1 | **Dev (BYOK)** | An agent developer using Tortoise as their team's epistemic memory | CLI / capture extension / SDK | Conversations never leave their machine; memory contains decisions+claims, not event noise; extraction "just types things right" against their pack |
| P2 | **Team lead / admin** | Runs the team graph; watches quota and budget | Hosted API / logs (no dashboard in v1) | Capture doesn't lock the team out; budget overflow is held, never dropped; alarms on enforcement misconfiguration |
| P3 | **Owner / eval adjudicator** | Product owner who reviews extraction quality | Eval harness (local scripts + CI) | Knows extraction works before it's promised; a fast labeling loop that doesn't require a statistician |
| P4 | **API consumer** | Automates derived commits (CI, scripts, connectors) | REST API / SDK | Deterministic commit semantics: replay-safe, merge-safe, never double-charged; clear 4xx reasons |

### 1.2 Journey tables

#### J-1 — Capture a session with BYOK local extraction (E2E-2) — P1, P4

| Step | Actor | Action | System | Exit condition |
|---|---|---|---|---|
| 1 | P1 | Ends an agent work session in the capture extension; chooses "extract locally" (default) | — | Local extractor invoked with the user's provider key (BYOK) |
| 2 | System | Runs S0-S3/S5-S6 locally (S4 warrants DEFERRED — never runs): filter tool/boilerplate → value gate → classify → relations → ground → serialize | Returns derived commit payload (no raw conversation) | Payload validated against Layer-1 locally; no raw text in payload |
| 3 | System | POST /v1/sessions/commit with `client_commit_id` + `session_id` | Auth via tt_ key; Layer-1 re-validation; quota + budget check; metering | 200 with `{session_id, commit_id, nodes_created, ...}` |
| 4 | System | Graph writes: Session counters, Event agentSession, Document transcript, Source bridge, Points/entities/operators | MERGE idempotent writes | Team graph contains points/entities/operators with full provenance chain; **extraction-emitted NANDs are written `unidirectional` by default (new-claim-attacks-existing), with bidirectional ONLY for the rare explicit mutual-restatement case (addendum §1) — so a later query shows the attack direction correctly (E2E-6, prepares P9/A8 surfacing)** |
| 5 | P1 | Queries the team graph ("what did we decide in that session?") | Search/query surface | Decision points surface with source_ref → resolvable Source → Document |

**Edge cases:** (a) provider key missing → mode-dependent per `TORTOISE_SESSION_EXTRACTION`: `required` → fail-closed local error, NO regex fallback; `auto`/`regex` → deterministic regex capture baseline (capture still works); (b) empty session (no value) → J-2; (c) network failure mid-commit → client retries with same `client_commit_id`: if the previous attempt FULLY wrote (:CommitRecord fully_written), L1 returns `duplicate:true` (zero writes, zero write-ops billed); otherwise L2 MERGE reconciliation completes the remainder (`duplicate:false`) — safe either way (W-3 [2][3]); (d) LLM provider rate limit → retry with backoff, bounded (≤5 retries/session); (e) local conversation > local turn cap → fail-closed LOCAL error (the commit endpoint has no turns — the legacy turn cap is POST /v1/sessions behavior, not the commit endpoint's); (f) malformed derived payload (e.g., quote >200 chars, unknown kind — stale local Layer-1 mirror) → server 422 with field reasons → client retries ONCE with corrected shape → still failing → client surfaces the reasons; `code: calibration_mismatch` → client refreshes its value brief (commit_schema.py) and regenerates (W-3).

#### J-2 — Extract-nothing session (E2E-8) — P1

| Step | Actor | Action | System | Exit condition |
|---|---|---|---|---|
| 1 | P1 | Captures a session that was all tool chatter / small talk | Value gate (S1) evaluates each window against the value brief | Keep-ratio within the ≤40% operating range (S1 drops 75-95% of traffic; the 20-40% EMPTY-RATE band is the eval metric in W-6, not a per-session gate) |
| 2 | System | Returns the empty typed stream in PAYLOAD terms: `{points:[], entities:[], operators:[]}` (no top-level decisions/events/claims — events/claims are pointKinds in the payload; R1's typed-stream shape is the extractor's internal form) | Commit proceeds as a valid empty commit | 200, zero points, zero budget burn, Session counters updated (commit_count +1) |
| 3 | System | keep-ratio >40% → fail-closed to empty + alarm | Telemetry alarm (keep_ratio) | Session captured as empty; ops alarm raised; no noise ever enters the graph |

**Edge cases:** (a) degenerate-empty risk — a session that SHOULD have value emits nothing → covered by window-type minimum-signal assertions in the eval (spec-classification-model §6, W-6), not by blocking capture; (b) keep-ratio alarm fires repeatedly → pack/vocabulary misconfiguration signal (W-5 calibration loop).

#### J-3 — Review a classified session (E2E-3, E2E-4) — P1, P3

| Step | Actor | Action | System | Exit condition |
|---|---|---|---|---|
| 1 | P1 | After commit, browses the session's points in the team graph | Session → Document → Source → Points | Every point shows kind, quote (≤200 chars), source_ref, status |
| 2 | P3 | (Eval path — transition: P3 pulls the same session's window from the capture transcript into the judge harness) | Judge harness | Window labeled with the rubric; label recorded |
| 3 | System | (Eval path) Computes per-class rates: decisions/events/claims separately (never blended) | metrics.py | Layer-correct ≥0.90 target band per class; atomicity ≥0.85 |
| 4 | P1 | Finds a compound decision ("A AND B AND C") | — | Deterministic atomicity check flags it (coordination cues / >1 commissive predicate — an R8 Layer-1 field) |
| 5 | System | Atomicity violation → extractor retries ONCE with the split error → splits into N atomic points (pass) or the stream fails Layer-1 (E9 — run fails with reason) | Enforcement ladder E9 only (atomicity is a stream-shape class, not E2) | Either N atomic points written, or the run failed with the atomicity reason logged |

**Edge cases:** (a) process decision ("I'll fix both now") → R3: classified, dropped with logged reason — never a graph point; (b) "should" utterances → recommendation, not decision (44× measured false positive — cue table); (c) tool-quirk claims (durable but environment-specific) → accepted as claims with source_ref (flagged in eval); no special tier in v1; (d) retry cap (≤1 retry/item, ≤5/session) exceeded → accept-with-flag or drop per class.

#### J-4 — Trace provenance (E2E-5) — P1, P4

| Step | Actor | Action | System | Exit condition |
|---|---|---|---|---|
| 1 | P1 | Sees a claim in a query result and asks "where did that come from?" | — | Claim carries `source_ref` (always, deterministic — R4/R7) |
| 2 | System | Resolves extractedFrom → Source node (agentSession, with url/contentHash/tier) | — | Source node exists and is indexed |
| 3 | System | Resolves Source.references → Document transcript (summary, story arc, sessionId) | — | Conversation context answerable without the raw text; quote ≤200 chars supports the claim |

**Edge cases:** (a) missing source_ref → Layer-1 violation, retry once, then reject (R4 non-negotiable — E8 BLOCK); (b) external artifact referenced in conversation ("the pricing page says…") → external Source node with sourceKind + credibilityTier, session Source `references` it (§3.4 chain — the `references`→Source producer extension, amendment §4.3 #7).

#### J-5 — Idempotent re-capture (E2E-7, L1/L2) — P1, P4

| Step | Actor | Action | System | Exit condition |
|---|---|---|---|---|
| 1 | P4 | Re-captures the same session (retry after network failure, or re-run after extractor bump) | New commit, same `session_id` | — |
| 2 | System | Exact replay (same `client_commit_id`, previous commit fully written) → zero writes | L1 idempotency | 200 `{duplicate: true}`, zero write-ops billed (PL4) |
| 3 | System | Re-capture with changed commit_id (extractor version bump) → MERGE reconciliation | L2: points upsert by deterministic `pt_<sha>` (same id → MERGE bump updatedAt/version, keep createdAt); changed content → new id → **`supersede_point` (CORRECTS edge + outdated flag + edge transfer)**; entities/operators MERGE by key | No duplicate points; old versions superseded (never hard-deleted); MERGE hits burn zero budget |
| 4 | P4 | Receives duplicate:true / 402 / 422 and acts | — | duplicate:true → no retry needed; 422 → retry once with corrected shape; 402 → hold locally (same `client_commit_id` is replay-safe on retry) |

**Edge cases:** (a) same content re-committed after extractor bump → all points dedup by content-hash, zero net-new nodes; (b) partially changed session → only net-new non-episodic nodes counted against budget; (c) concurrent commits for same session → deterministic ids make same-content commits safe (identical pt_<sha> MERGE); different-content concurrent commits → tie-break = **server arrival order assigned at write time**; the later-arriving version supersedes (REVISES direction = arrival order); (d) a commit that overflowed into the hold queue is NOT an L1 replay on re-submission (PL3).

#### J-6 — Budget overflow is held, never dropped (E2E-7) — P2

| Step | Actor | Action | System | Exit condition |
|---|---|---|---|---|
| 1 | P2 | Team uses capture heavily in one session | Cumulative per-session counter (net-new non-episodic, computed post-reconciliation) | Counter crosses soft 15 → WARN telemetry |
| 2 | System | Counter crosses hard 25 | budget_overflow: net-new items returned in `held[]`, NOT written | Commit still 200 with `held[]` list; nothing dropped |
| 3 | System | Counter would exceed ceiling 50 | Fail-closed | 402 with budget reason; nothing written |
| 4 | P2 | Reviews held items (from `held[]`); the client re-commits the held payload with the same `client_commit_id` | Re-submission is NOT an L1 replay and is NOT re-adjudicated against the hard-25 band — only the 50 ceiling applies (PL3) | Held items written (already-written + delta ≤ 50); held items never dropped, never silently written; ceiling-raise/promote endpoint deferred to v1.1 |

**Edge cases:** (a) held items remain client-side in v1 (the response's `held[]` + client logs are the visibility surface — "queryable" means via those, not via the graph, since held items are not written); (b) quota (`max_sessions`) and budget are independent — budget is per-session cumulative, quota is per-team count of Session nodes (post-fix); (c) a held payload that never re-commits stays client-side forever — never dropped, never written (no server-side GC in v1).

#### J-7 — Enforcement near-miss (E2E-9) — P1

| Step | Actor | Action | System | Exit condition |
|---|---|---|---|---|
| 1 | P1 | Extractor emits a kind that is an alias/typo of a known kind (`usecase`) | Enforcer E2 | Deterministic fix if unique alias; else one corrective retry → accept-as-fixed or WARN-accept with flag |
| 2 | P1 | Extractor emits a genuinely confusable kind (`userJourney` vs `useCase`) | Enforcer E3 | WARN: accepted with flag + suggestion; no retry (both defensible) |
| 3 | P1 | Extractor mints a kind not in the compiled vocab (`productRoadmap`) | Enforcer E1 | **BLOCK is ITEM-LEVEL: retry once with brief re-emphasized → item dropped + reason + violation event; the RUN continues; the agent is never hard-stopped (E2E-9 "not hard-block")** |
| 4 | System | Block rate >15% in a session | Fail-closed guard | Session fails closed to extract-nothing + alarm (vocabulary misconfiguration, not LLM failure) |

**Edge cases:** (a) entity-type mismatch (objectKind value in claims stream) → E4 BLOCK item-level: auto-route if unambiguous, else drop + reason — run continues; (b) undeclared relation → E5 WARN (edge not written, intent logged); (c) chain bypass (useCase→architecture skipping 4 steps) → E6 WARN (nearest valid intermediates attached); (d) out-of-scope kind (pack inactive for source type) → E7 WARN demote to tag on Source; (e) **out-of-vocab ENTITY kind (E2E-9: "out-of-vocab entity → warn/hold, not hard-block")** → E1 BLOCK item-level: the item drops with reason, the run and the agent proceed un-frustrated — the "not hard-block" promise is honored because only the item is dropped, never the run (except E9 stream-shape).

#### J-8 — Validate windows (E2E-1 — THE GATE) — P3

| Step | Actor | Action | System | Exit condition |
|---|---|---|---|---|
| 1 | P3 | Owner labels 2 real windows with the rubric | Judge harness (local script) | Gold labels recorded |
| 2 | System | Frontier judge labels the same 2 windows | Judge harness | Second label set recorded |
| 3 | System | κ computed + nothing-verdict agreement | kappa script | κ ≥0.60 AND nothing-verdict agreement; κ<0.50 → rubric revision, not progress |
| 4 | P3 | (Window #2 — operational session type — the plan's first workstream) | Same harness, different session type | Generalization confirmed; 7 eval-harness requirements from window #1's ops session exercised; minimum-signal assertions per window type pass |
| 5 | P3 | Owner adjudicates the 30-window gold set | Gold-set labeling pass | 30 labeled windows; judge agreement κ≥0.60 |

**Edge cases:** (a) owner availability — the gate is owner-scheduled; plan protects it as the first workstream; (b) judge disagreement → adjudication on the disputed items only, rubric notes recorded; (c) degenerate-empty windows → minimum-signal assertion per window type (operational sessions must emit ≥N events — spec §6).

#### J-9 — Privacy assurance (E2E-10) — P1, P2

| Step | Actor | Action | System | Exit condition |
|---|---|---|---|---|
| 1 | P1 | Reads the product wording before enabling capture | Docs | "We store derived knowledge and supporting quotes, never the full conversation" |
| 2 | System | Derived commit serialization | S6 | No raw conversation in payload; quotes truncated ≤200 chars; secret-scan drops credential-like patterns; telemetry block contains no conversation content |
| 3 | P1 | (Opt-in, later) enables raw-upload | Raw-upload path default-off | v1: path absent/disabled |

**Edge cases:** (a) secret in a quote → dropped by secret-scan (credential-like patterns), quote omitted, violation logged; (b) quotes >200 chars → truncated at sentence boundary ≤200.

---

## 2. Workflows

> System-level operational flows. Each workflow lists automation points, failure modes,
> and handoffs. Journey mapping noted per workflow.

### W-1 — Local extraction pipeline execution (J-1, J-2, J-3)

```
[S0] Deterministic preprocessing
     tool/boilerplate filter (53% traffic lever) → segmentation into EDUs
     ↓
[S1] Value gate (cheap model) — keep/drop per value brief (pack vocab)
     extract-nothing first-class; keep-ratio >40% → fail-closed to empty + alarm
     ↓
[S2] Classification (two-axis) — commissive/past-perfective/stative
     DECISION = commissive ∧ product-knowledge-bearing (R1∧R3 conjunction)
     "did/fixed/shipped" → event · "should" → recommendation · compound → split (atomicity)
     ↓
[S3] Relations — IMPL / NAND (unidirectional by default when extraction-emitted;
     bidirectional only for the rare explicit mutual-restatement — addendum §1) /
     MITIGATES
     deep-miss convention: extract support edges FIRST, then mitigations attach
     MITIGATES targets an IMPL edge (payload op_type with edge target — R1)
     ↓
[S5] Grounding/resolution — entity frequency gate · claim dedup (content-hash) ·
     supersede via supersede_point (never hard-delete) · process decisions →
     drop-with-log (R3)
     ↓
[S6] Derived-commit serializer — the POST /v1/sessions/commit payload
```

**Automation points:** all S0-S6 run automatically at session end; S1 and S2 retries run on the cheap model only (≤1 retry/item, ≤5/session). **Manual intervention:** only enforcement-alarm review (W-2 step 5) and calibration (W-5).

**Failure modes:** (a) provider unavailable → mode-dependent per `TORTOISE_SESSION_EXTRACTION`: `required` → fail-closed local error, NO regex fallback; `auto`/`regex` → regex capture baseline (capture always works); (b) keep-ratio alarm → empty + alarm (never noise); (c) block-rate >15% → fail-closed to empty + alarm; (d) malformed stream (E9) → retry once → run fails with reason (the ONE run-level class — R8 Layer-1 contract).

**Handoffs:** S6 derived payload → W-3 (commit path); violation events → W-2 (ladder) → W-6 (metrics) and W-5 (pack calibration).

### W-2 — Enforcement ladder (J-7)

Deterministic validation AFTER the LLM stage (S2/S3 outputs), BEFORE write (S5/S6) — zero LLM cost. Taxonomy (research-r6 §5.3):

| Class | Error | Level | Action on failure |
|---|---|---|---|
| E1 | Kind mint (LLM-invented) | **BLOCK** | retry once → item dropped + reason + violation event; run continues |
| E2 | Kind near-miss (alias/typo) | **RETRY** | deterministic fix if unique alias; else corrective retry → accept-as-fixed or WARN-accept |
| E3 | Confusable choice | **WARN** | accept with flag + suggestion |
| E4 | Entity-type mismatch | **BLOCK** | auto-route if unambiguous → else item dropped + reason; run continues |
| E5 | Undeclared relation | **WARN** | don't write edge; log intent + suggestion |
| E6 | Chain bypass | **WARN** | don't write skip edge; attach nearest valid intermediates |
| E7 | Out-of-scope kind (inactive pack) | **WARN** | demote: store as Tag on Source |
| E8 | Missing provenance | **BLOCK** | retry once → item dropped + reason (R4 non-negotiable); run continues |
| E9 | Malformed stream (shape) | **BLOCK** | retry once → RUN FAILS (R8 Layer-1) — the ONLY run-level class |
| E10 | Vague superclass | **WARN** | accept + suggestion; feeds prompt-improvement loop |

> **Atomicity (R2) is an R8 Layer-1 field, enforced at S2 deterministically
> (propositionize-before-classify) and at the stream level:** coordination cues or >1
> commissive predicate in an emitted decision → retry once with the split error →
> split into N atomic points (pass) or the stream fails Layer-1 (E9 semantics). It is
> NOT an E2 near-miss.

**Caps/guards:** ≤1 retry per item; ≤5 retries per session; retries only on cheap model; block-rate >15%/session → fail-closed to extract-nothing + alarm. Every non-pass verdict writes an `ExtractionViolation` event `{class, kind, suggestion, source_ref}` → eval layer-2 measurement + calibration loop.

**Handoffs:** violation events → W-6 (metrics) and W-5 (pack calibration). **Manual intervention:** alarm review only.

### W-3 — Derived-commit write path (J-1, J-4, J-5, J-6)

```
Local: derived payload (S6) + client_commit_id = SHA-256(canonical(session_id,
       points, entities, operators, summary, story_arc) — canonical = sorted keys,
       arrays by id, floats 3dp; confidence/c_cal/status/reason/timestamps excluded;
       see §6.1)
       ↓
POST /v1/sessions/commit (tt_ auth)
       ↓
[1] Layer-1 deterministic validation (Pydantic mirrors; required fields; kind∈vocab;
    referential integrity; point count ≤ MAX; content ≤1000; quote ≤200; atomicity
    shape) → non-conforming → retry once → 422 with field reasons
       ↓
[2] Idempotency L1: :CommitRecord {client_commit_id} exists AND status ==
    fully_written → 200 {duplicate:true}, zero writes, zero write-ops billed (PL4).
    A previous commit with status held|partial is NOT "fully written" (PL3) — the
    :CommitRecord MERGE is the atomic concurrency serialization point.
       ↓
[3] L2 MERGE reconciliation computed IN MEMORY (no writes yet): points by pt_<sha>
    (same id → MERGE bump; changed content → new id + supersede_point candidate);
    entities by (name, kind); operators by (src, dst, op_type). Net-new non-episodic
    delta = the budget numerator.
       ↓
[4] Quota + budget check on the RECONCILED net-new delta: >50 ceiling → 402 fail-closed;
    >25 hard → overflow to hold queue (held[] in response, items NOT written);
    >15 soft → WARN telemetry. (Order matters: the ceiling check MUST run post-
    reconciliation, pre-write — it counts net-new, which only the reconciliation knows.)
       ↓
[5] Graph writes: Session counters (value_nodes_created/held, draft_count,
    commit_count) · Event agentSession (capturedAt, content-addressed eventId) ·
    Document transcript (summary, story-arc, sessionId — no raw content on derived
    path) · Source bridge (sourceKind, tier, url, contentHash) · Points (status
    draft|live, c_cal) · entities · operators:
      - IMPL / NAND (extraction-emitted NAND unidirectional by default; bidirectional
        only for explicit mutual restatement — addendum §1)
      - MITIGATES (edge-targeted: {op_type: MITIGATES, target: {src, dst, op_type:
        IMPL}, strength 0.10-0.50} — the target is the edge identity triple, the
        operator MERGE key is (src,dst,op_type); server-side maps to the existing
        mitigate_operator mechanism: a mitigation Point (pointKind statement,
        mitigation_strength) + (m)-[:IMPL]->(op) + (op)-[:mitigated_by]->(m) —
        bias applied to the IMPL edge, never to a point. v1: the target must be
        an EMITTED operator of the same commit (Layer-1 enforced — deep-miss
        convention attaches within the commit); cross-commit targets are v1.1)
      - REPHRASE = dedup label only (no graph operator)
      - supersession via supersede_point (CORRECTS edge + outdated flag + edge
        transfer) when a point's content changed (payload reason: REVISES is the
        semantic label; there is no REVISES edge in v1 — (PL2))
      - event-class items (past-perfective) serialize as points[] entries with their
        event pointKind (the four-node chain's Event = the agentSession container,
        not per-item events)
       ↓
[6] Metering + telemetry: write_ops +1 per NON-duplicate commit call (PL4) — an
    overflow-to-hold commit bills ZERO write_ops (nothing written) and its eventual
    re-submission bills the single +1: one logical payload is billed exactly once.
    nodes_written +net-new non-episodic (cost driver; 0 on hold commits; supersede-
    only deltas exempt — R-14). Telemetry block (extractor version/mode, keep-ratio,
    counts, histogram — NO conversation content; graph-side counts NOT sent — the
    server derives merge/supersede/held/draft/live from Session counters).
       ↓
200 {session_id, commit_id, nodes_created, nodes_merged, held[], duplicate}
```

**Handoffs:** S6 derived payload → this workflow (from W-1); budget_overflow hold queue → W-4 (accounting/review semantics); violation events → W-6.

**Failure modes:** (a) 422 → client retries once with corrected shape (the extractor's Layer-1 mirror — commit_schema.py — should prevent this); `code: calibration_mismatch` → client refreshes its value brief; (b) 402 → client holds locally (hold queue is client-side per PL3; retry is replay-safe); (c) 500 → fail-closed count; client retries with same client_commit_id (safe by L1); (d) 429 → retry with backoff (replay-safe by L1); (e) 401 → re-auth, same client_commit_id is replay-safe.

### W-4 — Budget + quota accounting (J-6)

- **Per-session cumulative budget** (net-new non-episodic nodes post-dedup, computed post-reconciliation): soft 15 / hard 25 / ceiling 50. Counters live on the Session node (`value_nodes_created`, `value_nodes_held`, `draft_count`, `commit_count`). MERGE hits + dedup burn zero budget.
- **Hold semantics (PL3):** >25 → `held[]` in the commit response; items NOT written; never dropped; re-submission of the same `client_commit_id` writes them — checked against the 50 CEILING only (the hard-25 band does not re-trigger for previously-held payloads). Overflow-to-hold commits bill zero `write_ops`; the re-submission bills the single +1 (one logical payload billed exactly once). v1 visibility = response + client logs; promotion/ceiling endpoint v1.1.
- **Quota fix (P0, ships with the endpoint):** `_count_resource` gains a `sessions` branch counting `MATCH (s:Session)` (currently falls through to `MATCH (n)` counting ALL nodes — ~40 captures × 25 nodes = false 402). `is_episodic: true` on Session/Event/Source/Point (decided scope — amendment §4.3 #12); the `points` branch counts non-episodic only (the Point-level flag is the discriminator). `MAX_VALUE_POINTS_PER_SESSION` + `MAX_PAYLOAD_POINTS` + `MAX_ENTITIES`/`MAX_OPERATORS` constants land in quota.py.
- **Metering (PL4):** `write_ops` +1 per NON-duplicate commit call (published billed unit — unchanged); `nodes_written` += net-new non-episodic (cost driver) on the existing MeteringRecord. Prevents the 25× per-node arbitrage vs create_point.

**Manual intervention:** hold-queue review (P2) — v1 surface is the `held[]` response + logs; dashboard is separate work.

### W-5 — Pack manifest compilation + calibration (J-7)

```
manifest.yaml (v3: kindDefs, chains, extractable relations, extraction config)
    ↓ pack_registry._validate (strict on new fields; backward compatible)
compile_value_brief(source_type, namespaces, max_tokens=2000)
    → activation (active ∧ sourceTypes) → merge kindDefs (core defaults migrated
      from extractor._DEFAULT_POINT_KIND_DESCRIPTIONS) → resolve chains → filter
      extractable relations → token cap (chains never truncated)
    → ValueBrief (prompt block + JSON validator form)
    ↓
extractor prompt (S1 value gate AND S2/S3 classification) + extraction_enforcer
validator (S3→S5 gate) — shared compiled vocabulary
```

**Calibration loop (feedback):** near-miss (E2/E3/E10) rates tracked per kind per pack → kinds with sustained confusability get better `description`/`nearMisses`/`examples` → pack manifest PR (small, registry-test covered). Domain_loader becomes a thin adapter over PackRegistry (dead `domain_kinds()`/`known_kinds()` calls fixed; three kind sources collapse to one).

**Failure modes:** (a) manifest validation error → pack fails to load at registry load time (strict); (b) ambiguous bare chain step → error at load; (c) token overflow → truncate non-chain kinds (chains never truncated); (d) `architecture: Document` subclass expansion gap (verified) → fix `_build_kind_expansions` to include core entity types (Document/Source/Subject/Point/Event) in slice 4.

### W-6 — Evaluation loop (J-3 eval path, J-8 — THE GATE)

```
Window #1 (DONE — converged: 0→29→100% mitigation coverage, canonical case green)
    ↓
Window #2 (PLAN: first workstream — different session type, short operational)
    judge harness + kappa script; κ ≥0.60 + nothing-verdict agreement; κ<0.50 → rubric revision
    ↓  GATE — nothing proceeds on the extractor until this is green
Gold set: 30 windows, owner adjudicates, judge agreement κ≥0.60
    ↓
metrics.py (skeleton: tests/aries_extraction.py — load_sample/evaluate/run_variant):
    fuzzy matching, per-class P/R/F1 (decisions/events/claims/mitigations
    SEPARATE — never blended), layer-correct, atomicity, citation-correctness,
    kind-correctness, entity P/R, empty-rate, ECE, mitigation recall (≥0.75 target),
    process-routing (≥0.95/<0.80 — warn-only until n≥20),
    + WINDOW-TYPE MINIMUM-SIGNAL ASSERTIONS (operational sessions ≥N events — the
    degenerate-empty defense, spec §5.8/§6)
    + THE R1∧R3 CONJUNCTION as the decision-class test (commissive ∧
    product-knowledge-bearing — the evals must exercise it, spec §6; band:
    decisions-FP ≤5% on the meta-mixed fixture set, N≥30)
    Gold set SEEDS: tests/gold/0323_excerpt, tests/gold_standard.json,
    tests/eval_results_v2.json, and window #1's converged labels
    (probe-extraction-window1-v3.md) as the first gold window — reduces owner
    labeling on the critical path.
    ↓
thresholds.yaml (ONE reconciled file): existing A1-A22 + new R8 rows, band semantics
    (watch-gates, not powered tests): pass ≥ target on N≥30; fail < block on N≥12;
    between = watch. N≈109 for true separation — out of gold-set scope; live judge
    (rolling N=20) is the post-launch power source.
    ↓
CI: Layer-1 deterministic gates (blockers) + Layer-2 watch-gates (warnings) +
    minimum-signal assertions
```

**Automation:** judge harness, kappa script, metrics, CI all automated. **Manual:** owner labeling (window #2 + 30-window gold adjudication) — the owner-scheduled dependency.

### W-7 — Privacy enforcement (J-9)

- S6 serialization guarantees: no raw conversation in payload; quotes ≤200 chars, truncated at sentence boundary; secret-scan drops credential-like patterns (quote omitted + violation logged); **provenance_refs.path = BASENAME only** (full local path never leaves the machine — §6.1 maps basename → Document.sourcePath, spans → Source.provenance_spans).
- Telemetry block: extractor {version, mode} (calibration_version lives top-level), model {provider,id,cfg_hash}, kept/candidate/segment/window counts, keep_ratio, empty_windows, dedup_hits, frontier_calls, llm_cost_usd (optional), extraction_ms, retry_count, last_error_code, confidence_histogram. **No conversation content. Graph-side counts (merge/supersede/held/draft/live) are NOT sent — the server derives them from Session counters. judge_summary DROPPED from v1** (BYOK 1-in-N judge aggregates are post-v1 — matches §6.1).
- Public wording: "we store derived knowledge and supporting quotes, never the full conversation." Raw-upload path: absent in v1 (opt-in later).

**Failure modes:** (a) quote contains secret → dropped, not truncated; (b) telemetry regression (content leak) → Layer-1 test asserts telemetry block schema has no text fields.

---

## 3. Prototype (markdown diagrams — non-GUI feature)

> Dev-facing CLI/extension + API; no consumer UI in v1. Prototype = topology +
> state machines + data flow. Validated against journeys J-1..J-9 and scope E2E-1..10.

### 3.1 Pipeline topology (target state)

```
┌────────────────────────── LOCAL (user's machine, BYOK) ──────────────────────────┐
│                                                                                  │
│  agent work session ──▶ capture extension ──▶ [S0] tool/boilerplate filter       │
│                                                 │ EDUs                            │
│            value_brief.py ◀──── packs (v3)      ▼                                 │
│                 │                              [S1] value gate  ──keep-ratio>40%─▶│
│                 │                               │ keep/drop       fail-closed    │
│                 ▼                               ▼                                 │
│            [S2] two-axis classifier ──▶ [S3] relations                            │
│                 │ (decisions/events/claims)     │ IMPL/NAND/MITIGATES             │
│                 ▼                               │                                 │
│      extraction_enforcer.py ◀───────────────────┘  (validates S2/S3 output,      │
│                 │                                  BEFORE S5/S6 — zero LLM cost) │
│                 ▼                                 │                                 │
│            [S5] grounding/resolution              │                                 │
│                 │                                 │                                 │
│            [S6] derived-commit serializer         │                                 │
│                 │ (no raw text, quotes ≤200)      │                                 │
└──────────────────────────────────────────────────┬────────────────────────────────┘
                                                   │ POST /v1/sessions/commit
                                                   ▼
┌────────────────────────────── HOSTED (derived commits only) ────────────────────┐
│  Layer-1 validation → L1 idempotency (client_commit_id, fully-written only)     │
│  → L2 MERGE reconciliation IN MEMORY (net-new delta) → quota+budget check on    │
│  delta → graph writes → metering (write_ops non-duplicate + nodes_written)      │
│       │                                                                          │
│       ▼                                                                          │
│  Team graph: (Event agentSession)-[produces]->(Document transcript)              │
│              ▲                                          ▲                        │
│  (Point)-[extractedFrom]->(Source bridge)-[references]──┘                        │
│  Points: decision/claim · operators: IMPL / NAND (unidirectional by default     │
│  when extraction-emitted) / MITIGATES (edge-targeted) / CORRECTS (via           │
│  supersede_point) · REPHRASE = dedup label only — no graph operator            │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Extraction point lifecycle (state machine)

```
                 ┌──────────────┐
                 │  extracted   │  S2/S3 emits (local)
                 └──────┬───────┘
                        │ enforcer verdict
          ┌─────────────┼──────────────┬──────────────────┐
          ▼             ▼              ▼                  ▼
      BLOCK (E1/E4/E8) RETRY (E2)   WARN (E3/E5/E6/E7/E10)  PASS
          │             │              │                  │
          ▼             ▼              ▼                  ▼
   1 corrective  1 corrective   accepted+flag      written (S5/S6)
   retry (E1/E8) call ──▶ pass/fail                 status: draft
          │             │        │                      │
          ▼             ▼        ▼                      │
   still failing:  WARN-accept  (item kept)            │
   item dropped +  (E2 only)                           │
   reason +                                          promoted to live on first
   violation event;                                 operator edge (existing rule)
   run continues                                           │
                                                            ▼
   ── L2 re-capture, content changed ──▶ supersede_point:  superseded
      CORRECTS edge + outdated flag + edge transfer         (outdated:true, status
      (old point stays in graph, never hard-deleted)        transitions per #432)
```

### 3.3 Commit idempotency (state machine)

```
                    ┌───────────────────────────────┐
                    │  derived payload + client_    │
                    │  commit_id (SHA-256 canonical)│
                    └──────────────┬────────────────┘
                                   ▼
                    Layer-1 valid? ──no──▶ retry once ──▶ 422 (field reasons)
                                   │yes
                                   ▼
                    replay? (:CommitRecord {client_commit_id} exists AND
                    status == fully_written)  ← the record is the atomic
                    serialization point (concurrency-safe)
                                   │yes ──▶ 200 {duplicate:true}, zero writes,
                                   │        zero write-ops billed
                                   │no (incl. status held|partial → NOT a replay)
                                   ▼
                    L2 reconciliation IN MEMORY (no writes):
                     pt_<sha> exists? ──yes, same content──▶ MERGE bump
                     │                 (updatedAt/version, keep createdAt)
                     │no               — zero budget
                     ▼
                     changed content ──▶ new id + supersede_point candidate
                     ▼
                     net-new delta = budget numerator
                     ▼
                     quota+budget check on delta ──>50──▶ 402 fail-closed
                     │                         >25 ──▶ held[] (not written, never
                     │                         │        dropped; zero write-ops
                     │                         │        billed; re-commit checks
                     │                         │        the 50 ceiling only — NOT
                     │                         │        a replay, NOT re-adjudicated)
                     │ok
                     ▼
                     write + counters + metering + telemetry
                     ▼
                     200 {nodes_created, nodes_merged, held[], duplicate:false}
```

### 3.4 Budget states (per-session cumulative, net-new non-episodic)

```
0 ───────────── 15 ──────────── 25 ──────────────── 50 ───────────▶
   OK (normal)    soft WARN      hard: hold queue     ceiling: 402
                 (telemetry)     (>25 → held[], 200)  (fail-closed)
```

### 3.5 Enforcement ladder decision flow (J-7)

```
item → membership in compiled vocab?
   ├─ no ──▶ synonym map / edit-distance ≤1?
   │           ├─ unique alias ──▶ deterministic fix ──▶ PASS
   │           ├─ ambiguous ──▶ 1 corrective retry ──▶ PASS or WARN-accept
   │           └─ unknown ──▶ BLOCK (E1): retry once → item dropped + reason;
   │                             run continues
   ├─ yes, nearMisses group ──▶ WARN: accept + flag + suggestion (E3)
   ├─ yes, wrong stream bucket ──▶ BLOCK (E4): auto-route or item dropped; run continues
   ├─ yes, relation not extractable ──▶ WARN: don't write, log intent (E5)
   ├─ yes, chain bypass ──▶ WARN: attach nearest valid intermediates (E6)
   ├─ yes, source_type inactive ──▶ WARN: demote to Tag (E7)
   ├─ provenance missing (source_ref absent) ──▶ BLOCK (E8): retry once →
   │     item dropped + reason; run continues
   └─ vague superclass chosen ──▶ WARN: accept + suggestion (E10)
guards: ≤1 retry/item, ≤5/session, block-rate >15% → fail-closed to empty + alarm
run-level: only E9 (malformed stream shape) fails the RUN — retry once → fail with reason
```

---

## 4. Data Model

> Graph entities — verified against ONTOLOGY.md v3.5 (§4.x tables) and the current
> codebase (sdk.py, quota.py, metering.py). All fields listed are what the four-node
> chain + commit endpoint actually read/write. RLS: not a Supabase schema — tenant
> isolation is the existing namespace-SDK pattern; no new RLS surface.

### 4.1 Entities

| Entity | Graph label | Status | Fields (v1) | Notes |
|---|---|---|---|---|
| **Session** | `:Session` | EXISTS (sdk.py:868, hosted_api.py) | `id` (existing), `created_at`, `turn_count`; **NEW: `value_nodes_created`, `value_nodes_held`, `draft_count`, `commit_count`, `is_episodic: true`** | Budget counters live here (W-4); `is_episodic` exempts from points quota |
| **CommitRecord** | `:CommitRecord` | **NEW** (slice 5) | `client_commit_id` (UNIQUE, MERGE key), `session_id`, `commit_id` (= client_commit_id), `status: fully_written\|held\|partial`, `written_at`, `write_ops_billed` | The L1 replay + PL3 adjudication record — required by W-3 [2], J-5, J-6 (see §5.4/§6.1); one node per commit attempt, MERGE on `client_commit_id` is also the concurrency serialization point. **Division of labor: CommitRecord = per-commit adjudication state (status, billing); Session counters = budget numerator + telemetry source (held count lives in `value_nodes_held`, NOT on the record — no held_point_ids[] on :CommitRecord)** |
| **Event** | `:Event` | EXISTS; **NEW eventKind** | `eventId` (content-addressed — hash of session_id+captured_at, deterministic for MERGE), `eventKind: AgentSession` (**amendment §4.3** — exact code spelling, capital A; reconciles with core `sessionCaptured`), `capturedAt` (**amendment §4.3**), `startedAt/endedAt`, `is_episodic: true`, participants as Event PROPERTIES in v1 (no `participatesIn` producer — scope §1) | Produces the Document |
| **Document** | `:Document` | EXISTS (ONTOLOGY §4.4) | `documentKind: transcript`, `title`, `summary`, **`story_arc` (NEW field — amendment §4.3)**, `topics`, `sessionId`, `eventId`, `sourcePath` (basename only — privacy), `doc_status` (captured/extracted/draft), `_searchText` — **NO `content` on the derived path** | Fields exist today; amendments = register capture usage (summary/story-arc/sessionId). Content only on the opt-in raw path (not v1) |
| **Source** | `:Source` | EXISTS (ONTOLOGY §4.6) | `url` (identity — basename/contentHash-derived, never the full local path), `sourceKind: agentSession` (value registered — amendment §4.3; external kinds for referenced artifacts), `credibilityTier`, `contentHash`, `title`, `ingestedAt`, `updatedAt`, `externalId`, `sourceDate`, `provenance_spans` (NEW property — amendment §4.3; window spans from provenance_refs), `is_episodic: true` | **Bridge, NOT content holder** (four-node model). The session Source carries provenance only |
| **Point** | `:Point` | EXISTS | `id` (`pt_<sha>` deterministic — content-addressed ids registered, amendment §4.3), `content` (≤1000), `pointKind` ∈ closed vocab (incl. `event` for event-class items — registered, amendment §4.3), `status` (draft→live), `confidence`, `c_cal` (NEW field — amendment §4.3), `quote` (≤200, secret-scanned — stored Point property, amendment §4.3), `source_ref` REQUIRED; `is_episodic: true` on episodic (regex-path) Points only — the quota discriminator | decision/claim kinds; event-class items → points with `pointKind: event` (R1 serialization rule) |
| **Entity (Object)** | `:Object` | EXISTS | `name`, `objectKind` (node property; payload calls it `kind` — mapped at write), `passes_frequency_gate` (NEW property — amendment §4.3; written WITH flag when false) | Entities MERGE by (name, objectKind) |
| **Operator** | `:Point {is_operator:true}` | EXISTS (sdk.py:1503) | `op_type`, `inputs`, direction flag; node id = ULID (unchanged); **MERGE key = (src, dst, op_type) tuple** — no `op_<sha>` ids (create_operator hardcodes ULID; adding an explicit-id path is NOT needed — (PL1)) | IMPL / NAND (unidirectional flag per S3 policy) / CORRECTS (via supersede_point) / **mitigation Point** (pointKind statement, `mitigation_strength` 0-1 — the existing mitigate_operator mechanism; extractor bias 0.10-0.50 maps into strength) |

### 4.2 Edges (the four-node chain + operators)

```
(Event:AgentSession)-[:produces]->(Document:transcript)         # Event → Document
(Document)<-[:references]-(Source)                               # Document ← Source
(Point)-[:extractedFrom]->(Source)                               # provenance (R4) — EXISTS (sdk.py:759)
(Point)-[:aboutObject]->(Object)                                 # entity linkage — the canonical
                                                                 # predicate (ONTOLOGY §3.2); aboutEntity
                                                                 # does NOT exist — never use it
(Point)-[:IMPL|:NAND {unidirectional}]->(Point)                  # epistemic operators — EXISTS
(mitigation Point m)-[:IMPL]->(op) + (op)-[:mitigated_by]->(m)   # MITIGATES mechanism — EXISTS
                                                                 # (mitigate_operator, sdk.py:1591);
                                                                 # mitigation_strength 0-1 (bias
                                                                 # 0.10-0.50 maps into it); R9 semantics
                                                                 # = edge-relevance reduction, never
                                                                 # point-targeted
(Point new)-[:CORRECTS]->(Point old) + outdated:true            # supersede_point — EXISTS (sdk.py:1296)
(Source)-[:references]->(Source external)                       # agentSession → external artifact
                                                                 # (§3.4 chain; producer extension in
                                                                 # link_source_to_entity — amendment §4.3)
```

No new edge predicates in v1 (REVISES/REPHRASE are payload labels, not edges — (PL2)).
`produces`/`references` follow the ontology §3 edge topology; confirm exact predicate
names at implementation against `sdk._link_source`/EventAPI (slice 5).

### 4.3 Ontology amendments (slice 3 — registration, not new design)

1. `AgentSession` registered in ONTOLOGY §4.5 core eventKind vocabulary — EXACT code spelling (capital A; sdk.py:3963, session_indexer.py); `sessionCaptured` (the legacy core kind, still written by the regex path) declared an alias of the same concept — both remain valid kinds, no migration.
2. `capturedAt` documented in §4.5 (bi-temporal capture).
3. Content-addressed Event ID documented in §4.5 (deterministic MERGE anchor for the agentSession Event).
4. Document `summary`/`story-arc`/`sessionId` — §4.4 fields; `story_arc` registered as an explicit §4.4 field (capture usage note; summary = short, story_arc = arc continuation).
5. NAND direction policy documented (pipeline spec — extraction-emitted default `unidirectional`, mutual-restatement exception).
6. Source = provenance bridge confirmation — PLUS: `sourceKind: agentSession` value registered in the §5 source-type vocabulary (with its credibility-tier resolution — #398 inheritance is keyed on sourceKind); `provenance_spans` registered as a §4.6 Source property.
7. `mitigated_by` predicate registered (ONTOLOGY §3.9 valid-predicate list — used by the existing mitigate_operator; currently unregistered).
8. `references` target extended: Source allowed as a `references` target (producer extension in `link_source_to_entity`, which today validates entity_label ∈ {Document, Event, Object} — projection/edges.py:212).
9. `pointKind: event` registered in the §5 Point Kind Vocabulary (code uses it today for episodic turn points — sdk.py:883 — but it is NOT in the ontology vocab; registration also puts it in the compiled value brief so the enforcer does not block event-class items).
10. Content-addressed Point ids (`pt_<sha>`) registered as a sanctioned id form (§4.1 — create_point's ULID preference note; deterministic ids are the commit-endpoint idempotency anchor).
11. NEW Point fields registered in §4.1: `c_cal` (calibrated confidence) and stored `quote` (provenance quote ≤200 chars — today only payload-level metadata).
12. `passes_frequency_gate` registered on §4.3 Object (S5 gate-result flag; false entities are still written, flagged).
13. `is_episodic` registered on Session/Event/Source/Point (quota exemption discriminator — §4.4; episodic Points from the regex path carry it too).

### 4.4 Quota + budget data (slice 2 — P0)

- `MAX_VALUE_POINTS_PER_SESSION = {soft: 15, hard: 25, ceiling: 50}` — NEW constants in quota.py (budget = net-new non-episodic delta, post-reconciliation).
- `MAX_PAYLOAD_POINTS = 50` — NEW constant (Layer-1 RAW payload point count → 422; deliberately NAMED differently from the budget ceiling to prevent wiring the wrong 50).
- `MAX_ENTITIES = 500` / `MAX_OPERATORS = 500` — NEW per-type payload caps (→ 400/422; independent, not summed).
- `_count_resource` NEW `sessions` branch: `MATCH (s:Session) RETURN count(s)` (today falls through to `MATCH (n)` counting ALL nodes — the P0).
- `is_episodic: true` on Session/Event/Source AND on episodic Points (the points branch counts non-episodic only — the Point-level flag is what makes the filter computable; amendment §4.3 #12). **Backfill: legacy regex-path nodes lack the flag → a one-query Cypher migration ships with slice 2 (R-18); DE2E-7 has a legacy-node fixture.**
- MeteringRecord gains `nodes_written` (net-new non-episodic, post-dedup); `write_ops` semantics per PL4.
- **`:CommitRecord`** (new label, §4.1) — the L1 replay + PL3 adjudication record; lives in the existing tenant graph (no new service/store — §5.3).

### 4.5 Constraints (DB-level / Layer-1)

| Constraint | Enforcement | Where |
|---|---|---|
| `pointKind` ∈ closed vocab (compiled brief); `sourceKind` ∈ ontology §5 source-type vocabulary (amendment #5) | BLOCK item (E1) / 422 at API | enforcer + Pydantic mirror (`commit_schema.py` — §5.1) |
| Entity/operator payload caps: entities ≤ `MAX_ENTITIES`, operators ≤ `MAX_OPERATORS` | 422 | Layer-1 (§6.1 — caps resolution: per-type caps are Layer-1; 400 is reserved for missing required payload fields) |
| `source_ref` present on every point | BLOCK item (E8) / 422 | Layer-1 |
| Atomicity (no coordination cues, ≤1 commissive predicate) | retry once → run fails (E9) | Layer-1 |
| Required fields present (schema_version, session_id, client_commit_id, captured_at, extractor, points, telemetry) | 422 | Layer-1 |
| Referential integrity: operator src/dst ∈ emitted point ids; `about_entities` ⊆ entities; `source_ref` ∈ {session source} ∪ emitted `sources[]` | 422 | Layer-1 |
| MITIGATES shape: `target` ∈ emitted IMPL operator keys; `strength` ∈ [0.10, 0.50] | 422 | Layer-1 |
| NAND `direction` REQUIRED (extractor sets it; absent → 422) | 422 | Layer-1 |
| Raw payload point count ≤ `MAX_PAYLOAD_POINTS` (50) | 422 | Layer-1 (independent of budget) |
| `content` ≤1000, `quote` ≤200 | 422 | Layer-1 |
| Net-new non-episodic delta ≤ budget ceiling 50 | 402 | budget check (§6.1) |
| Deterministic ids (`pt_<sha>`; operator MERGE key (src,dst,op_type); content-addressed eventId) | MERGE idempotency | S6 + endpoint |
| Never hard-delete extracted points | supersede_point only | S5/endpoint |

## 5. Architecture

### 5.1 Components

| Component | Layer | Module (new/existing) | Responsibility |
|---|---|---|---|
| **Value-brief compiler** | Local | `tortoise/value_brief.py` (NEW) | Compiles pack v3 manifests → ValueBrief (prompt block + validator JSON); core-kind-defs table (migrates `extractor._DEFAULT_POINT_KIND_DESCRIPTIONS`); token cap 2000 |
| **Value-first extractor** | Local | `tortoise/value_extractor.py` (NEW; reuses extractor.py LLM scaffolds) | S0-S3/S5/S6: filter → value gate → two-axis classify → relations (IMPL/NAND/MITIGATES) → ground/resolve → serialize. Extract-nothing first-class |
| **Extraction enforcer** | Local | `tortoise/extraction_enforcer.py` (NEW) | Deterministic E1-E10 ladder on S2/S3 output, before S5/S6; violation events; caps/guards |
| **Commit producer** | Local | `sdk.py` `commit_session()` (NEW) + capture extension rework | Derived-commit serializer + client_commit_id; capture extension cloud path: local-extract → POST derived (raw-upload = opt-in, not v1) |
| **Commit endpoint** | Hosted | `hosted_api.py` `POST /v1/sessions/commit` (NEW) | Layer-1 validation (via `commit_schema.py`), L1/L2 idempotency (:CommitRecord), budget, quota, metering, telemetry |
| **Commit schema (shared)** | Shared | `tortoise/commit_schema.py` (NEW) | Pydantic models for the payload + the closed vocab compiled from PackRegistry — the SAME module imported by the local extractor (Layer-1 mirror) and the hosted endpoint (server-side 422s); this is what makes §5.2 item 4 true across the local/hosted seam |
| **Quota/budget** | Hosted | `quota.py` (FIX) | sessions branch, is_episodic, MAX_VALUE_POINTS_PER_SESSION, MAX_PAYLOAD_POINTS, MAX_ENTITIES/MAX_OPERATORS |
| **Ontology registration** | Shared | `docs/ONTOLOGY.md` + `verify_ontology.py` (slice 3) | The §4.3 amendment list (13 items): eventKind AgentSession, capturedAt, story_arc, sourceKind agentSession, provenance_spans, mitigated_by, references→Source, pointKind event, pt_<sha>, c_cal, quote, passes_frequency_gate, is_episodic |
| **Pack registry v3** | Shared | `pack_registry.py` (EXTEND) | kindDefs/chains/extractable/extraction parsing + validation; core-entity expansion fix |
| **domain_loader** | Shared | `domain_loader.py` (UNIFY) | Thin adapter over PackRegistry; dead `domain_kinds()`/`known_kinds()` calls fixed |
| **Eval harness + drift monitor** | Dev tooling | `tools/` (NEW; slice 1: judge harness + kappa; slice 8: metrics.py, gold set, thresholds, CI, drift monitor) | Judge harness + kappa script (slice 1 — THE GATE); metrics.py (skeleton: aries_extraction.py), gold set (seeded), thresholds.yaml, CI workflow; **drift monitor consumes commit-endpoint telemetry → deterministic/ops floors in v1 (keep-ratio, block-rate, fail-closed rate, Layer-1 floor); the Layer-2 live-floor watch (needs judge aggregates) arrives post-v1 with the live judge — alerts on 2-of-3 consecutive misses (R-17) — R8 build order item 5** |
| **Privacy** | Local + docs | S6 serializer (secret-scan, quote truncation) + `docs/` (public wording, slice 9) | Owned by the commit-producer row; enforcement tests in Layer-1 |

### 5.2 Boundaries (clean, no leaky seams)

1. **Local vs hosted (the privacy seam):** the hosted product receives derived commits only — no raw conversation, no user's key. The boundary is the payload schema (S6 + Layer-1 mirror). Raw-upload and managed-key are explicitly NOT in v1.
2. **Enforcement granularity:** item-level (E1-E8, E10 — drop/flag, run continues) vs run-level (E9 stream shape — retry once → fail). This is what makes R6's "strong but not 100% hard" real.
3. **Eval vs production:** thresholds.yaml band semantics are watch-gates in CI; Layer-1 schema is the only production hard gate (422/402). No eval threshold ever blocks a production commit.
4. **Kind sources collapse to ONE:** pack_registry is canonical; domain_loader and config/domain_manifest.yaml become adapters/legacy (R6 §5.4).

### 5.3 Deployment topology

- **Local:** pip SDK + CLI/extension on the user's machine; BYOK provider key from user env/config; extraction never leaves the machine; the derived commit is the only network egress (plus telemetry).
- **Hosted:** FastAPI (existing hosted_api.py) behind existing auth (tt_ keys, `get_current_team`); graph = existing FalkorDB tenant namespace; no new services. The `:CommitRecord` label is the ONLY new graph artifact (replay/adjudication state, §4.1). Hold queue = response semantics + Session counters (client-side items, PL3).
- **Consumers of the commit endpoint (enumerated):** SDK `commit_session` (dev machines, CI), capture extension cloud path; **self-hosted deployments** = run `hosted_api` locally (the endpoint is part of the existing selfhost surface) — there is NO local-graph-write fallback for derived commits in v1; `capture_session` (regex path) remains the local-only capture for self-hosts without the endpoint.
- **Eval:** standalone scripts + GitHub Actions CI (Layer-1 blockers + watch-gate reports + minimum-signal assertions); gold set lives in the repo. **Drift monitor:** a small scheduled job consumes commit-endpoint telemetry (the telemetry block lands in the existing telemetry/analytics store) and runs the rolling N=20 live-floor watch → alert on <90% (R8 build order item 5; slice 8).

### 5.4 Failure modes & mitigations

| Failure | Detection | Mitigation |
|---|---|---|
| LLM provider unavailable (local) | mode check | `required` → fail-closed; `auto`/`regex` → regex baseline |
| keep-ratio >40% (S1 over-keeps) | telemetry alarm | fail-closed to empty + alarm; calibration (W-5) |
| Block-rate >15% (vocab misconfigured) | violation-event stats | fail-closed to empty + alarm |
| LLM nondeterminism | Layer-1 vs Layer-2 split | deterministic schema gates (CI blockers) + statistical watch-gates (eval) |
| Retry storms (5xx/rate limits) | telemetry retry_count | bounded retries (≤5/session), replay-safe client_commit_id |
| Budget overflow | Session counters | held[], never dropped (PL3) |
| Quota false-402 | fixed `_count_resource` | sessions branch ships with the endpoint (P0) |
| Dead code paths (`domain_kinds`, `known_kinds` 2-arg) | slice-4 tests | domain_loader unification fixes them |
| `architecture: Document` subclass expansion gap | slice-4 tests | `_build_kind_expansions` gains core entity types |
| L1 replay undecidable / held re-submission loops | :CommitRecord (unique MERGE key) | status fully_written\|held\|partial per commit; hard-25 band applies at first adjudication only (PL3) |
| Concurrent commits (TOCTOU on duplicate check) | :CommitRecord MERGE = atomic serialization point; Session.commit_count atomic increment | loser sees existing record → duplicate:true; commit_count sequence defines supersede direction |
| Stale local brief → 422 on valid kinds | calibration_version in payload | 422 with `code: calibration_mismatch` → client refreshes the brief (J-1 edge f) |
| 429 rate limit (RateLimitMiddleware, 100 req/min/key) | middleware | retry with backoff; replay-safe via client_commit_id; consider a higher bucket for commit (batch op) |

## 6. Interfaces (contract-first)

### 6.1 POST /v1/sessions/commit (new endpoint)

```
POST /v1/sessions/commit
Auth: tt_ key (get_current_team) — same as POST /v1/sessions

Request body (derived payload — NO raw conversation, NO user key):
{
  schema_version: "1",
  session_id: str,                       # client-stable
  client_commit_id: str,                 # SHA-256 over canonical JSON (see below)
  captured_at: ISO8601,
  extractor: {                           # SINGLE copy — telemetry references it, no dup
    version: "value@<semver>+<prompt_hash>+<model_cfg_hash>",
    mode: "byok",
    calibration_version: str,
  },
  summary: str,                          # Document.summary
  story_arc: str,                        # Document.story_arc (registered field)
  provenance_refs: [{path, spans}],      # path = BASENAME only (privacy — full path never
                                         #   leaves the machine; W-7) → Document.sourcePath;
                                         #   spans → Source.provenance_spans
  sources: [{sourceKind, url,            # EXTERNAL referenced artifacts (R4 chain: the
             credibilityTier, contentHash}]  #   session Source references them) — source_ref
                                         #   resolves against this list or the session Source
  entities: [{name, kind, passes_frequency_gate: bool}],
  points: [{
    id: "pt_<sha>", content: str (≤1000), pointKind: str (closed vocab,
    incl. event),
    reason: "NEW|REVISES",               # CONNECTS/RESOLVES CUT in v1 (no defined
                                         #   server behavior; 422) — deferred with the
                                         #   cross-session work
    confidence: float, c_cal: float,
    about_entities: [entity_name],       # ⊆ entities
    source_ref: str (REQUIRED),          # resolvable → Source
    quote: str (≤200, secret-scanned),
    status: "live|draft",
  }],
  operators: [{
    src: point_id, dst: point_id,        # referential integrity vs points[]
    op_type: "IMPL|NAND|MITIGATES",
    direction: "unidirectional|bidirectional",  # REQUIRED on NAND (extractor default
                                         #   unidirectional; mutual-restatement explicit)
    target: {src, dst, op_type: "IMPL"},  # MITIGATES only — the edge identity triple;
                                         #   operator MERGE key is (src,dst,op_type) —
                                         #   NO op_<sha> ids
    strength: float,                     # MITIGATES only — [0.10, 0.50] (extractor bias;
                                         #   maps into mitigation_strength 0-1)
  }],
  telemetry: {  # no conversation content; graph-side counts NOT sent (server derives
             #   merge/supersede/held/draft/live from Session counters — no second
             #   source of truth)
    extractor: {version, mode},          # calibration_version lives top-level
    model: {provider, id, cfg_hash},
    counts: {kept, candidate, segment, window, empty_windows},
    keep_ratio: float, dedup_hits: int,
    frontier_calls: int, llm_cost_usd: float|null, extraction_ms: int,
    retry_count: int, last_error_code: str|null,
    confidence_histogram: [10 x int],   # 0.1 buckets
  },
  # judge_summary DROPPED from v1 telemetry (BYOK 1-in-N judge is post-v1)
}

Canonicalization (client_commit_id — deterministic across clients):
  canonical = deterministic JSON over (session_id, points, entities, operators,
  summary, story_arc): sorted keys at every level, arrays sorted by point/entity/
  operator id, floats rounded to 3 decimals, and confidence/c_cal/status/reason
  EXCLUDED (LLM artifacts — same rationale as timestamps). The server RECOMPUTES
  the hash and 422s on mismatch (code: commit_id_mismatch) — the id is not opaque.
  Compute via the EXISTING ids.content_hash() helper (ids.py:15 — SHA-256 hex,
  already the idempotency-key primitive). :CommitRecord EXTENDS the IngestKey/
  begin_ingest event-log pattern (idempotency.py) rather than replacing it: the
  event-log scan cannot carry held|partial adjudication status — exactly why a
  status-bearing record is needed.

Responses:
  200 {session_id, commit_id (= client_commit_id — stable per logical commit),
       nodes_created, nodes_merged, held: [point_ids], duplicate: bool}
      # duplicate:true ⇒ zero writes, zero write-ops billed (PL4)
      # held non-empty ⇒ overflow (PL3): client-side, re-commit checks 50-ceiling only
  400  payload-level errors — {detail: str} (missing session_id/client_commit_id —
       the per-type caps MAX_PAYLOAD_POINTS/MAX_ENTITIES/MAX_OPERATORS are LAYER-1
       → 422, NOT 400; constants in quota.py §4.4)
      # NOTE: the derived payload has NO turns — the legacy turn cap does not apply
  401  invalid/missing tt_ key — {detail: str}; client re-auths; the SAME
       client_commit_id is replay-safe on retry
  402  budget ceiling (net-new delta > 50 — fail-closed) OR sessions quota
       (post-fix count) — {detail: str}
  422  Layer-1 schema violations — {detail: {field: [reasons], code?: ...}};
       retry ONCE allowed; code: calibration_mismatch / commit_id_mismatch
       → client must refresh its value brief / recompute the id
  429  rate-limited (RateLimitMiddleware, 100 req/min/key) — retry with backoff;
       replay-safe via client_commit_id (higher bucket for commit: TBD slice 5)
  500  fail-closed (count), redacted — {detail: str}; client retries with same
       client_commit_id (safe by L1)

Layer-1 validation (deterministic, via commit_schema.py — the shared module):
  required fields; kind ∈ closed vocab (pointKind AND sourceKind — the ontology §5
  source-type vocabulary, amendment §4.3 #5); referential integrity (operators
  src/dst ∈ emitted point ids; about_entities ⊆ entities; source_ref ∈ {session
  source} ∪ emitted sources[]); MITIGATES: target ∈ emitted operator keys ∧
  target.op_type == "IMPL" ∧ strength ∈ [0.10, 0.50]; NAND direction REQUIRED;
  raw payload point count ≤ MAX_PAYLOAD_POINTS (50 → 422 — independent of the
  budget ceiling which counts NET-NEW delta → 402); content ≤1000; quote ≤200;
  atomicity shape; source_ref present; client_commit_id recomputed and matched.

Idempotency:
  L1: :CommitRecord {client_commit_id} exists AND status == fully_written → replay
      (200 duplicate:true; zero writes; zero write-ops). The :CommitRecord MERGE
      is the atomic serialization point (concurrency-safe: the loser of the MERGE
      sees the winner's record → duplicate:true).
  L2: re-capture (new commit_id, same session_id) → MERGE by pt_<sha>; changed
      content → supersede_point (CORRECTS + outdated + edge transfer); entities
      (name, objectKind); operators (src,dst,op_type). Never hard-delete.
      Tie-break: Session.commit_count atomic increment — the recorded sequence
      number defines supersede direction (later-arriving version supersedes).

Budget (per session, net-new non-episodic post-dedup) — THE AUTHORITATIVE
SEMANTICS (references elsewhere point here):
  - soft 15 → WARN telemetry; items still written.
  - >25 → held[] (the hard band applies at FIRST adjudication of a client_commit_id
    only — re-submissions of a seen-but-not-fully-written commit (status
    held|partial) are checked against the 50 CEILING only, PL3).
  - >50 → 402, nothing written. A re-submission that would push cumulative past 50
    ALSO 402s — the held items remain held client-side (never dropped) until the
    v1.1 promote endpoint; this ceiling-exceeded case is DE2E-7 Session C.
  - Supersede-only deltas (a new point that supersedes an existing same-session
    point) do NOT increment net-new (R-14 mitigation). MERGE/dedup burn zero.
  - The RAW payload cap (MAX_PAYLOAD_POINTS → 422) applies to every submission
    independently — a held payload already fit it.

Metering: write_ops +1 per non-duplicate commit call; overflow-to-hold commits
bill 0 (recorded as write_ops_billed: 0 on the :CommitRecord); nodes_written +=
net-new non-episodic. (PL4)
```

### 6.2 SDK surface

```python
# NEW — local derived-commit producer (slice 7)
class DerivedCommitPayload:            # the §6.1 request body as a dataclass — the
    ...                                 # canonical type for serialization + hashing

class CommitRejectedError(Exception):  # carries status (400/401/402/422/429/500)
    ...                                 # + detail + reasons; raised by commit_session

# NEW — local derived-commit producer (slice 7)
def commit_session(
    payload: DerivedCommitPayload,       # OR (conversation, extractor=None) convenience:
    session_id: str | None = None,       #   pipeline runs S0-S3/S5/S6 locally, then builds
    *,
    extractor: ValueExtractor | None = None,   # default: S0-S3/S5/S6 pipeline
    mode: str = "byok",                  # BYOK is THE default
    client_commit_id: str | None = None, # auto: SHA-256 canonical (§6.1)
    hold_queue: HoldQueue | None = None, # optional local store: persists held[] payloads
) -> dict:                               # {session_id, commit_id, nodes_created,
    ...                                  #  nodes_merged, held, duplicate}
    # error contract: CommitRejectedError(status, detail, reasons) on 4xx/5xx;
    # held items: payloads returned in held[] are persisted to hold_queue if given,
    #   and re-submitted with the SAME DerivedCommitPayload (same canonical bytes →
    #   same client_commit_id → PL3 ceiling-only adjudication)

# UNCHANGED — existing (slice 7 note only)
def capture_session(...) -> dict:        # KEEPS its current shape {session_id, turns,
    ...                                 #   extracted, points} — regex/local-only capture
                                        #   (the non-BYOK fallback + self-host path); the
                                        #   cloud path rework routes BYOK captures through
                                        #   commit_session, NOT through a changed return

# NEW — value brief (slice 4/6)
def compile_value_brief(source_type: str = "conversation",
                        namespaces: list[str] | None = None,
                        max_tokens: int = 2000) -> ValueBrief:
    # ValueBrief = {pointKinds[], objectKinds[], subjectKinds[], eventKinds[],
    #               chains[], relations[], enforcement{default, perKind, perRelation,
    #               perChain}}  → prompt block + JSON validator form

# NEW — enforcer (slice 6)
def validate_item(item: ExtractedItem, brief: ValueBrief) -> EnforcementVerdict:
    # EnforcementVerdict = {level: pass|warn|retry|block, kind, suggestion, reason}
    # violation event: event-log type ExtractionViolation
    #   {class: E1..E10, kind, suggestion, source_ref}
```

### 6.3 Eval interfaces (slice 8)

```python
# tests/eval/types.py
@dataclass
class Window:
    session_id: str
    window_id: str
    edus: list[str]                    # the EDUs to classify (transcript-derived)
    gold_labels: list[Label] | None    # None for pred windows (model output)

@dataclass
class Label:                            # a gold/pred verdict per EDU
    edu_index: int
    class_: str                         # decision|event|claim|process|none (extract-nothing)
    kind: str | None                    # pack kind when classified as entity-bearing
    atomicity: bool                     # true = single commitment
    source_ref: str | None
    relations: list[RelationLabel]      # IMPL/NAND/MITIGATES edges incl. the canonical case

@dataclass
class MetricsReport:
    per_class: dict[str, dict[str, float]]   # {kind: {p, r, f1}} — decisions/events/
                                             # claims/mitigations SEPARATE, never blended
    layer_correct: float; atomicity: float
    citation_correctness: float; kind_correctness: float
    entity_p_r: tuple[float, float]
    empty_rate: float; ece: float; mitigation_recall: float
    min_signal: dict[str, bool]        # {window_type: passed} — degenerate-empty defense
    r1r3_conjunction: dict[str, float] # the DECISION-class test stats (spec §6)

compute_metrics(gold: list[Window], pred: list[Window]) -> MetricsReport
kappa(a_labels: list[Label], b_labels: list[Label]) -> float

# thresholds.yaml — ONE reconciled file (band semantics: watch-gates, not powered tests)
#   existing rows A1-A22: point_precision_raw 0.65/0.55, point_precision_live 0.80/0.70,
#     per_kind_f1 0.60, nand_precision 0.70, empty 20-40%, live floor 95/90,
#     nodes 15/25/50, failclose 0.40  —  unchanged semantics (A-rows: A1-A22)
#   NEW R8 rows (same band semantics): layer-correct ≥0.90/<0.80, atomicity ≥0.85/<0.70,
#     kind-correctness ≥0.90/<0.80, citation-correctness ≥0.90/<0.80,
#     mitigation recall ≥0.75/<0.60 (block value PROVISIONAL — from the R9 audit's
#       fail-closed posture; confirm at gold-set calibration), entity P/R ≥0.80/≥0.65
#       & <0.65/<0.50, process-routing ≥0.95/<0.80 (warn-only until n≥20 — research-r8;
#       block is a WATCH, monitor-only by class rarity), empty-rate 20-40% (already
#       A-rows — not duplicated; reconciliation note: A7's canonical fail edges are
#       <15%/>50% over 4 weeks and A17's keep-ratio trigger is ×3 windows — the
#       per-session interpretation in DE2E-8 is the plan's, reconciled INTO ONE file
#       at slice 8), R1∧R3 conjunction: decisions false-positive rate ≤5% on the
#       meta-discussion-mixed fixture set (N≥30 — the DE2E-3 Layer-2 band)
#   band semantics: pass ≥ target on N≥30; fail < block on N≥12; between = watch;
#   EXCEPT live floor: rolling N=20 (research-r8) — the post-launch power source
#     (v1 drift monitor consumes only the deterministic/ops floors; the Layer-2
#     live-floor watch arrives post-v1 with the live judge — §5.1)
```

### 6.4 Versioning & compatibility

- Payload `schema_version: "1"` in the body; unknown versions → 422 (no silent drift).
- Pack manifest v3 is backward compatible by construction (no new required fields; a
  manifest without the new sections behaves exactly as today — verified against the
  current registry, research-r6 §6.2a).
- `extractor.version` (semver + prompt_hash + model_cfg_hash) is TRACKED metadata:
  telemetry + change detection. **L2 reconciliation triggers ONLY when the canonical
  hash changes (content change) — never on version alone** (a version-bump-only re-send
  is an L1 replay, DE2E-7).
- The existing POST /v1/sessions (regex capture) stays unchanged for the non-BYOK
  fallback path; the new endpoint is additive.

---

## 7. Detailed E2E Test Cases

> One-to-one with scope.md §4 E2E-1..10 (plus DE2E-11, the spec-mandated R9 canonical
> probe). Each: purpose, setup (concrete), steps, assertions (Layer-1 deterministic =
> CI blockers; Layer-2 statistical = watch-gates, bands from §6.3), negative cases.
>
> **Test-surface conventions (apply to ALL DE2Es):**
> - Layer-1 fixtures run with the DETERMINISTIC mock model (MockModel/MockExtractor,
>   extractor.py:206/419) — LLM output never appears in Layer-1 assertions. Real-model
>   runs happen only in the slice-8 gold-set eval (Layer-2).
> - Layer-2 assertions run against the slice-8 gold set (30 windows, owner-labeled)
>   via metrics.py — they are eval-workflow tests, not fixture tests. Per-class N rule:
>   a window contributes to a class's N iff it contains ≥1 gold item of that class;
>   if a class's N < 12 → skip (watch) with the class flagged in the report.
> - Tests needing cross-connection concurrency (DE2E-7 neg a) run against LIVE
>   FalkorDB (docker:// URI, skip-guarded on embedded — conftest convention,
>   test_ep_directional.py precedent).
> - Surface: pytest + FalkorDBLite (embedded) unless stated otherwise.

### DE2E-1 — 2-window rubric validation (THE GATE — deliverable #1)

**Purpose:** The falsification gate — the rubric must reproduce on the owner's real workload before any extractor work. | Scope E2E-1

**Setup:** judge harness script + kappa script (**slice-1 tooling — the GATE owns them; the full eval suite lands in slice 8**: `tools/judge_harness.py`, `tools/kappa.py`); window #1 (design session, already converged) + window #2 (operational session — **blocking external input**: owner-provided transcript, utterance-tagged format defined in the harness); owner labels both, frontier judge labels both.

**Steps:**
1. Owner labels windows #1/#2 with the rubric → gold labels.
2. Frontier judge labels the same windows (independent run) → judge labels.
3. Compute κ (kappa) + nothing-verdict agreement (both said "nothing" on the same EDUs).
4. Record per-class agreement on the windows.

**Assertions:**
- Layer-2 (watch): κ ≥0.60 AND nothing-verdict agreement; per-class agreement recorded per class (no threshold in v1 for per-class agreement at N=2 — recorded for the calibration loop).
- Gate semantics: κ<0.50 → rubric revision (NOT progress) — the workflow stops and the rubric is amended. **Middle band: 0.50 ≤ κ < 0.60 → NOT green — expand labeling to more windows before re-evaluating; the owner may proceed to adjudication but the gate is not satisfied.** This is a documented GO/REVISE decision, not a red CI.

**Negative cases:** (a) judge emits empty labels on a non-empty window → flagged (degenerate labeling); (b) both judges agree on nothing for an operational window that should have events → minimum-signal assertion fails (window-type check) → window #2 does not count as green.

### DE2E-2 — BYOK capture → derived commit → graph (four-node chain)

**Purpose:** The default path end-to-end. | Scope E2E-2

**Setup:** FalkorDBLite tenant; SDK `commit_session(payload=DerivedCommitPayload)` with a fixture conversation; mock provider key (BYOK local extraction, no server-side key).

**Steps:**
1. Run local extraction (S0-S3/S5/S6) on the fixture → derived payload (assert: no raw conversation fields).
2. POST /v1/sessions/commit (or `commit_session`) → 200.
3. Query the graph: Session counters; Event `AgentSession` (capturedAt, content-addressed eventId); Document transcript (summary, story_arc, sessionId); Source (sourceKind agentSession, contentHash); Points (decision/claim, quote ≤200, source_ref); operators.
4. **Discoverability (free integration check): run the EXISTING session_indexer AgentSession search path (session_indexer.py:441-459/638) — the committed session MUST be findable through it (catches predicate-name mismatches in the four-node chain).**

**Assertions:**
- Layer-1 (deterministic): all four chain nodes exist; `(Event)-[:produces]->(Document)`; `(Document)<-[:references]-(Source)`; every `(Point)-[:extractedFrom]->(Source)` resolves (R4/R7); **entities present as `:Object` nodes with `objectKind`; every `about_entities` entry resolves via `(Point)-[:aboutObject]->(Object)`; duplicate entity names MERGE by (name, objectKind); `passes_frequency_gate: false` → written WITH flag**; **operator edges exist with the (src,dst,op_type) MERGE key**; payload contains no raw turn text (byte-level check).
- Layer-2 (watch): layer-correct ≥0.90 (N≥30 across the gold set — prerequisite: slice-8 gold set exists); per-class rates recorded.

**Negative cases:** (a) provider key missing with `TORTOISE_SESSION_EXTRACTION=required` (pin the env in the test) → fail-closed local error, no commit; (b) raw conversation string in payload → 422 (schema has no such field) + Layer-1 privacy test; (c) secret-like quote (e.g., `sk-...` pattern) → dropped by secret-scan, quote omitted, violation logged.

### DE2E-3 — Layer-correct classification + R3 routing

**Purpose:** Decisions vs events vs claims per-class (never blended); process decisions never hit the graph. | Scope E2E-3

**Setup:** fixture windows with known mixes: "we fixed X" (event), "we decided Y" (decision), "the cost is Z" (claim), "I'll fix both now" (process — R3), "we should A" (recommendation — NOT a decision); PLUS the spec §5 failure-mode fixtures: **FM2 meta-discussion** ("we decided the extractor should support X" — must NOT become a point), **FM4 narrated-repeat dedup** (same task completion narrated 4× + one distinct task → exactly 2 event points, per-task granularity), **FM5 tool-quirk** ("the bash tool needs X" — accepted as claim + flagged), **FM7 conditional commitment** ("don't delete YET" — still a decision, condition preserved in content).

**Steps:**
1. Extract each window; collect per-class outputs + R3 drop log.
2. For the compound: "we will ship A and B and C" → atomicity check fires → retry once → 3 atomic points.
3. FM4 window: assert S5 within-session content-hash dedup → exactly 2 event points + `dedup_hits` telemetry incremented.
4. FM5 window: assert the tool-quirk claim IS written with source_ref AND carries the eval flag (violation event / flag field — the J-3 resolution's "flagged in eval" half).

**Assertions:**
- Layer-1: every emitted item has kind ∈ closed vocab; process items (incl. FM2 meta-discussion) absent from points[] with a logged reason (violation event `class: R3-drop`); "should" items never classified decision; atomicity: no emitted decision contains coordination cues or >1 commissive predicate (deterministic); FM7 emitted as a decision with the condition preserved (≤1 commissive predicate).
- Layer-2 (watch, per class — NEVER blended): decisions P/R, events P/R, claims P/R per §6.3 bands; atomicity ≥0.85/<0.70; process-routing ≥0.95/<0.80 (warn-only until n≥20); the R1∧R3 conjunction test (spec §6) stats recorded — **with a thresholds.yaml band: decisions-FP ≤5% on the meta-discussion-mixed fixture set (N≥30 — the DE2E-3 Layer-2 band)**.

**Negative cases:** (a) degenerate-empty: an operational fixture that emits zero events → minimum-signal assertion fails; (b) trigger-less decision ("I'm leaning toward B") must be KEPT (value gate) — assert it is present.

### DE2E-4 — Atomicity (compound split)

**Purpose:** One point = one decision (R2). | Scope E2E-4

**Setup:** fixture: "A AND B AND C" serial-list compound; "X because Y" (rationale subordination); a single clean decision (control).

**Steps:** propositionize-before-classify → split candidates; deterministic atomicity validator on emitted stream.

**Assertions:**
- Layer-1: 3 atomic points for the compound (each ≤1 commissive predicate); "X because Y" → decision X + claim Y IMPL-linking; control stays single; any still-compound emission → retry once → run fails (E9) with the atomicity reason.
- Layer-2: atomicity ≥0.85 on gold matching (gold matching, not self-report — §6.3).

**Negative cases:** the extractor refuses to split after retry → run fails (E9) — assert the failure reason is the atomicity shape.

### DE2E-5 — Source citation (provenance chain)

**Purpose:** Every claim/decision cites a source; sources indexed (R4/R7); "where did this come from" always answerable. | Scope E2E-5

**Setup:** fixture with an external-artifact reference ("the pricing page says GLM is $0.60/M") — the payload carries the artifact in the `sources[]` block (§6.1: {sourceKind, url, credibilityTier, contentHash}).

**Steps:**
1. Commit; verify Source nodes created for the session AND the external artifact (from `sources[]`).
2. Resolve each point's extractedFrom → Source → references → Document.

**Assertions:**
- Layer-1 (deterministic): `source_ref` present on 100% of points (missing → E8 BLOCK / 422); every source_ref resolves to an existing Source node (the session Source or an emitted `sources[]` entry — referential integrity, §4.5); external Source has sourceKind + credibilityTier; the session Source `references` the external Source (producer extension, amendment §4.3 #7); dangling source_ref (no matching Source/sources[] entry) → 422.
- Layer-2 (watch): citation-correctness ≥0.90/<0.80 (quote/span backs the claim) on the gold set (prerequisite: slice-8 gold set).

**Negative cases:** (a) point without source_ref → retry once → dropped with reason (never written); (b) `sources[]` entry with an unknown sourceKind → 422 (vocab); (c) source_ref pointing at a `sources[]` entry that is NOT referenced by the session Source → 422 (the chain must connect).

### DE2E-6 — NAND direction policy

**Purpose:** Extraction-emitted NANDs attack existing claims with the right direction; surfacing prep (P9/A8). | Scope E2E-6

**Setup:** fixture: session asserts D; a later session (or same) asserts ¬D ("we decided X" … "actually X is wrong"); plus the mutual-restatement case ("A and B can't both be true" asserted together).

**Steps:**
1. Commit both claims → assert the NAND edge direction.
2. Query the graph for the NAND edge + direction property.

**Assertions:**
- Layer-1: new-claim-attacks-existing → NAND `unidirectional` (attacker→target); mutual restatement → `bidirectional`; `direction` REQUIRED on all emitted NANDs (absent → 422).
- P9/A8 gate: the contested-variance property test (variance >0.04 on a balanced contradiction — fixture: claim P and ¬P with matched evidence, repeated EP runs) runs in CI against the pinned calibration surface (tests/test_ep_directional.py documents embedded-vs-live EP numeric divergence — the threshold is calibrated on the live surface, Docker skip-guarded in CI; `tests/ep_e2e_patterns.py`'s existing `var < 0.02` check is the precursor and is REPLACED/reconciled by the slice-6 test). Green before the surfacing feature is claimed (part of slice 6 acceptance).

**Negative cases:** (a) NAND without direction → 422; (b) zero-NAND sessions are normal (window-2 observation) — a fixture with no contradiction must pass cleanly with zero NANDs (no bias toward emission).

### DE2E-7 — Idempotent re-capture + budget + quota + Layer-1

**Purpose:** Replay-safe, merge-safe, never double-charged; budget held never dropped; quota counts Sessions. | Scope E2E-7

**Setup:** fixture session; commit twice (exact replay); re-capture where point P1's content changes X→Y (NOT a version-bump-only re-capture — extractor.version is excluded from the canonical hash, so a version-only re-send is an L1 replay, not an L2 re-capture); budget fixtures with EXPLICIT session counter states: **Session A — prior commit of 20 net-new non-episodic nodes → commit 2 with 10 new points → cumulative 30 → held[10]** (also exercises soft-15 WARN: a session crossing 15 with items still written → 200 + WARN telemetry); **Session B — prior commit of 45 → commit 2 with 10 new → cumulative 55 → 402, nothing written**; **Session C — held re-submission that would push cumulative past 50 → 402, items remain held client-side (never dropped; the v1.1 promote endpoint is the only way out)**; **bump-then-re-capture fixture (R-14): after a brief/calibration change, re-extract an old session — the supersede-only delta (new points superseding same-session points) must NOT increment net-new (budget does not exhaust)**; **legacy fixture (R-18): a pre-existing regex-path Session/Point WITHOUT is_episodic → the slice-2 backfill migration applies the flag → the points quota counts it as episodic**; quota fixture: **inject `max_sessions=40` on the Team node (direct write, conftest convention — billing.py defaults are 1000 flat, no tier gives 40) → create 40 sessions via 40 minimal valid commits → 41st commit → 402; assert `_count_resource('sessions')` returns 40, NOT the all-nodes count (the P0 regression)**; stale-brief fixture for `calibration_mismatch`.

**Steps/Assertions:**
- L1 replay: second POST with same payload → 200 `{duplicate: true}`, zero writes, zero write-ops billed (assert `:CommitRecord` status fully_written; metering counter unchanged).
- L2 re-capture: changed content (P1 X→Y) → new pt id + supersede_point (CORRECTS new→old + outdated flag + edge transfer); MERGE hits burn zero budget; Session.commit_count incremented (tie-break order).
- Budget (Session A): commit 2 → 200 with `held[10]`, items NOT written; **re-submission (same payload) → checked against 50-ceiling only → written (assert: no infinite hold; :CommitRecord held → fully_written)**; **soft-15 WARN: a session crossing 15 with items still written → 200 + WARN telemetry (keep_ratio/telemetry field) + Session counter recorded**.
- Budget (Session B): commit 2 → 402, nothing written (delta would exceed 50).
- Budget (Session C): held re-submission exceeding the ceiling → 402, items remain held client-side (never dropped).
- Bump-then-re-capture (R-14): supersede-only delta does NOT increment net-new → budget unchanged after the re-capture; superseded points carry outdated:true.
- Legacy nodes (R-18): backfilled is_episodic flag → quota counts legacy episodic points as episodic (no false 402).
- Held-path billing (PL4): overflow commit → MeteringRecord.write_ops unchanged + `write_ops_billed: 0` on the held :CommitRecord; re-submission → exactly +1.
- Quota: 41st commit → 402; count query returns Session-node count (post-fix) — pre-fix all-nodes count regression test on `_count_resource`.
- Layer-1: malformed payload → 422 with field reasons; retry once with corrected shape → 200; client_commit_id mismatch (recomputed hash differs) → 422 `commit_id_mismatch`; **stale-brief payload (kind valid in old brief, absent in new) → 422 `{code: calibration_mismatch}` → client refreshes brief → regenerates → 200**; **51-point payload → 422 (MAX_PAYLOAD_POINTS raw cap — independent of the budget ceiling)**.

**Negative cases:** (a) concurrent identical commits — LIVE FalkorDB only (Docker, skip-guarded): the loser of the :CommitRecord MERGE sees duplicate:true (serialization point test); (b) concurrent different-content commits — commit_count ordering defines the supersede direction; (c) held payload never re-submitted → no server-side GC, nothing dropped.

### DE2E-8 — Extract-nothing + keep-ratio fail-closed

**Purpose:** Empty sessions are normal and valid; noise never enters. | Scope E2E-8

**Setup:** fixture of pure tool chatter; fixture mixing TOOL-RESULT EDUs (data-bearing command output containing claim-like text — the S0 filter's 53% lever) with genuine conversation; keep-ratio fail-closed fixture (deterministic mechanism: a scripted S1 stub returning ≥41% keeps — test-only, consistent with the MockModel convention).

**Steps/Assertions:**
- Empty window → valid empty commit `{points:[], entities:[], operators:[]}` → 200, zero budget burn, commit_count +1.
- **S0 filter leakage (FM1): in the mixed fixture, NO point/claim in the payload derives from tool-result EDUs (content-origin check; kept/segment telemetry excludes tool EDUs).**
- keep-ratio >40% (scripted S1 stub) → fail-closed to empty + alarm (telemetry keep_ratio flagged; ops alarm event) — assert no points written despite the over-keep.
- Degenerate-empty guard: an operational window that should emit events but emits nothing → eval minimum-signal assertion fails (W-6) — eval-side, not capture-blocking.
- Layer-2 (watch): empty-rate within the 20-40% band on N≥30 gold-set windows (fail <20% or >40% on N≥12 — per-session interpretation; A7's canonical fail edges (<15%/>50% over 4 weeks) and A17's ×3-window keep-ratio trigger are reconciled INTO ONE thresholds.yaml at slice 8 — §6.3) — CI eval workflow.

**Negative cases:** the extractor returns non-empty after fail-closed → test fails (fail-closed is deterministic).

### DE2E-9 — Pack enforcement (soft)

**Purpose:** R6 — typed by pack business logic, softly enforced; agents un-frustrated. | Scope E2E-9

**Setup:** product-strategy pack manifest v3 fixture (**prerequisite: slice 4** — the v3 schema + converted manifest land with the pack slice; fixture at `tests/fixtures/pack_v3/product-strategy.yaml` + chain defs; **prerequisite: slice 6** — the enforcer + value-brief compiler); extractor emitting: alias `usecase` (E2), confusable `userJourney` vs `useCase` (E3), minted `productRoadmap` (E1), chain bypass useCase→architecture (E6), out-of-vocab entity kind (E1 item-level), entity-type mismatch (E4), **undeclared relation (E5), inactive-pack kind (E7 — demote to Tag on Source), vague superclass `statement` (E10)**.

**Steps/Assertions:**
- E2 → deterministic fix or one corrective retry → accept-as-fixed / WARN-accept with flag.
- E3 → WARN accept + flag + suggestion (no retry).
- E1 → retry once → item dropped + reason + violation event; run continues (assert the rest of the stream committed).
- E6 → edge not written; nearest valid intermediates attached; violation event.
- E4 → auto-route if unambiguous, else drop + reason; run continues.
- **E5 → edge not written, intent logged + violation event.**
- **E7 → kind stored as Tag on the Source node, NOT as a point.**
- **E10 → accepted + suggestion; feeds the prompt-improvement loop (violation event).**
- Block-rate >15% in a session → fail-closed to empty + alarm (assert) — fixture: ≥16 blocked of 100 items.
- **Retry cap: a session exceeding ≤5 retries → cap enforced (assert retry_count = 5, subsequent retries refused).**

**Negative cases:** (a) any BLOCK stopping the run (except E9) → test fails (item-level only); (b) a blocked item silently re-appearing in the graph → test fails (dropped with reason, not written).

### DE2E-10 — Privacy

**Purpose:** Derived commit contains no raw transcript; quotes bounded; secrets dropped; telemetry clean. | Scope E2E-10

**Setup:** fixture conversation containing: a raw paragraph (must NOT appear), a >200-char quote, a credential-like string (`sk-...`), a full local path in provenance_refs.

**Steps/Assertions:**
- Byte-level: no raw conversation substring in payload, telemetry block, or graph nodes (Document.content absent on derived path).
- Quotes: truncated ≤200 chars at sentence boundary; credential pattern → quote dropped + violation logged (never truncated-and-sent).
- provenance_refs: basename only (no username/project full paths); Source.url derived from basename/contentHash.
- Telemetry schema test (Layer-1): the telemetry block has NO text-bearing fields (schema-level assertion).
- Public wording present in docs ("we store derived knowledge and supporting quotes, never the full conversation").

**Negative cases:** a telemetry field containing conversation content → Layer-1 test fails (regression guard).

### DE2E-11 — MITIGATES: the canonical R9 probe + verification-edge fixtures (spec §6 deterministic probe)

**Purpose:** R9 is a PRIMARY extraction target (owner-flagged under-identification, 29%→100% through the loop). The canonical case is the deterministic probe; without it the mitigation-recall ≥0.75 band (§6.3) has no fixture to feed. | Spec §6 + R9 (not in scope's E2E list — the plan's dedicated probe test)

**Setup:** MockModel fixtures. Canonical: X "it's cheap" IMPL A ("Option A"); Z "we can raise the price" MITIGATES [X→A]; Y "customers aren't price-sensitive" IMPL Z. Verification-edge (FM6): X = status claim ("the build passes") IMPL A; Z "watch-gate, not a statistical test" MITIGATES [X→A]. Deep-miss negative: a fixture where the target IMPL edge is NOT emitted.

**Steps:**
1. Commit the canonical fixture in ONE payload: assert all three operators emitted (X→A IMPL; Z MITIGATES target == the (X,A,IMPL) identity triple; Y→Z IMPL).
2. Query the graph for the mitigation artifact: mitigation Point (pointKind statement, `mitigation_strength` in the 0.10-0.50-mapped range), `(m)-[:IMPL]->(op)`, `(op)-[:mitigated_by]->(m)`.
3. Verification-edge fixture: assert the mitigation targets the (X,A,IMPL) edge and the status claim remains an untargeted claim (never a MITIGATES target itself).
4. Deep-miss fixture: target IMPL edge absent → the mitigation must NOT serialize/attach (support-edge-first convention — extraction order guarantee).

**Assertions:**
- Layer-1 (deterministic): canonical probe passes byte-for-byte (the three operators with correct targets); bias attaches to the IMPL edge, never the point; graph artifact matches the §4.2 mechanism; deep-miss → mitigation dropped with reason.
- Layer-1 shape negatives: MITIGATES with target ∉ emitted operator keys → 422; target.op_type != IMPL → 422; strength 0.05 or 0.60 → 422; missing strength → 422.
- Layer-2 (watch): mitigation recall ≥0.75/<0.60 (gold set, N≥30; per-class N rule — windows containing ≥1 gold mitigation); verification-edge mitigation recall recorded as its own line.

**Negative cases:** (a) a mitigation serialized without its support edge → test fails (deep-miss convention); (b) any MITIGATES emitted against a non-IMPL operator → test fails (Layer-1).

---

## 8. Coherence Review + Risk Analysis

> Final gate. Cross-substep drift detection, risk register, improvement scan, and the
> restated build order for the Decompose stage. Reviewed by parallel fresh-context
> reviewers (cross-substep-drift, risk-completeness, improvement-opportunities).

### 8.1 Cross-substep coherence (drift scan)

**Terminology — single source per concept (drift-checked):**

| Concept | Canonical term | Where defined | Consistent across |
|---|---|---|---|
| Four-node chain | Event AgentSession → Document transcript ← Source bridge ← extractedFrom Points | §4.2 + §4.3 #1 | J-1/J-4, W-3 [5], 3.1, DE2E-2 |
| NAND direction | unidirectional DEFAULT; bidirectional only for explicit mutual restatement | PL-block, §4.2, DESIGN §1/§2 | J-1 step 4, W-1 S3, W-3 [5], DE2E-6 |
| MITIGATES | payload op_type, target = (src,dst,op_type) triple; mitigation Point + mitigated_by | PL1, §4.1/§4.2, §6.1 | W-3 [5], DE2E-11 |
| Supersession | supersede_point: CORRECTS new→old + outdated + edge transfer; `reason: REVISES` = label only | PL2, §4.1/§4.2 | J-5 step 3, W-3 [5], 3.2/3.3, DE2E-7 |
| L1 replay | :CommitRecord status fully_written; duplicate:true → zero writes + zero write-ops | PL3/PL4, §4.1, §6.1 | J-1(c), J-5, W-3 [2], 3.3, DE2E-7 |
| Budget | net-new non-episodic delta; soft 15 / hard 25 (first adjudication only) / ceiling 50 | PL3, §4.4, §6.1 | J-6, W-3 [4], W-4, 3.4, DE2E-7 |
| Event-class items | serialize as points[] with `pointKind: event` (registered) | §4.1, §4.3 #8 | J-2 step 2, W-3 [5], §6.1 |
| Enforcement | E1-E10 ladder; item-level EXCEPT E9 run-level; caps ≤1 retry/item ≤5/session, block-rate >15% | W-2, §4.5 | J-7, 3.5, DE2E-9 |
| Billing | write_ops +1 per non-duplicate commit; held commits bill 0; nodes_written cost driver | PL4, §4.4, §6.1 | J-5, W-3 [6], W-4, DE2E-7 |

**Forward/reverse pass (conclusions):** every journey step has a workflow or DE2E home;
every DE2E traces to a scope E2E (or spec §6 probe); every §6.1 payload field is
consumed by §4.1/§4.2/W-3 [5] or explicitly cut (CONNECTS/RESOLVES, judge_summary,
the 5 graph-side telemetry counts).

**Cross-doc residuals vs the research docs (all resolved IN the plan — enumerated so
Decompose does not re-derive them):**
(a) research-r8's "point count ≤ MAX_VALUE_POINTS_PER_SESSION" Layer-1 wording → the
    plan decouples MAX_PAYLOAD_POINTS (raw → 422) from the budget ceiling (net-new →
    402) — §4.4;
(b) research-r8's canonical hash omits story_arc → the plan includes it (§6.1);
(c) research-r8's telemetry carries judge_summary + graph-side counts → the plan cuts
    both (§6.1/W-7);
(d) scope E2E-9's "warn/hold (not hard-block)" → implemented as E1 item-level BLOCK
    (drop item, run continues; the run is never blocked except E9) — J-7 edge (e);
(e) research-r8/scope "A1-A21" → reconciled to A1-A22 (§6.3);
(f) research-r8 process-routing "no block" reading → restored to ≥0.95/<0.80,
    warn-only until n≥20 (§6.3).
DESIGN.md §1/§2 updated in this pass (NAND "always" wording, REVISES→supersede_point,
MITIGATES edge-targeting).

### 8.2 Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner slice |
|---|---|---|---|---|---|
| R-1 | **The gate fails**: window #2 κ <0.50 or nothing-verdict disagreement — the premise doesn't reproduce on operational sessions | Med | Critical (premise falsified) | Gate-first: nothing else proceeds; rubric revision loop (already converged once: 0→29→100%); the 2-window design is cheaper than the 30-window gold | 1 |
| R-2 | Window #2 owner-scheduling slips (owner availability) | High | Schedule | The gate is the FIRST workstream; parallelizable with slices 2-4 (quota, ontology, pack schema — none depend on the rubric); gold set (30 windows) is the long pole — start labeling early | 1, 8 |
| R-3 | Layer-2 thresholds overpromised (watch-gates read as powered tests) | Med | Reputational | Band semantics documented everywhere (§6.3, W-6); N≈109 separation math is explicitly out of scope; live judge (rolling N=20) is the post-launch power source | 8 |
| R-4 | Keep-ratio drift >40% → classification difficulty rises toward the 59-73% research range (the coupling warning) | Low-Med | Quality | keep_ratio telemetry alarm = leading indicator (W-1); fail-closed to empty; calibration loop (W-5) | 6, 8 |
| R-5 | Vocabulary drift between local brief and hosted mirror (stale client → valid kinds 422) | Med | DX friction | commit_schema.py shared module (single source); `calibration_mismatch` code + refresh path (J-1 edge f, DE2E-7) | 5, 6 |
| R-6 | LLM nondeterminism breaks CI | High (inherent) | Test trust | Two-layer contract: Layer-1 assertions run on MockModel; real-model runs only in the gold-set eval with band semantics | 6, 8 |
| R-7 | Concurrency edge cases (TOCTOU duplicate check, supersede direction) | Low | Data integrity | :CommitRecord MERGE = atomic serialization point; commit_count ordering; live-FalkorDB tests (DE2E-7 neg a/b) | 5 |
| R-8 | Privacy regression (content leaks into telemetry/provenance paths) | Low | Trust, legal | Byte-level tests (DE2E-10); telemetry schema test (no text fields); basename-only paths; secret-scan | 5, 7, 9 |
| R-9 | Held items lost client-side (never re-submitted) | Med | Value loss | Never dropped by design; `held[]` in every response; SDK hold_queue helper; no server GC (documented) | 5, 7 |
| R-10 | Enforcement ladder frustrates agents despite "soft" promise | Low-Med | DX | Item-level drops only (E9 the sole run-level class); WARN/retry dominant (7 of 10 classes); block-rate fail-closed = misconfiguration signal, not agent punishment | 6 |
| R-11 | Self-host users have no derived-commit path | Med | Adoption | Documented: run hosted_api locally (endpoint is part of the selfhost surface); capture_session regex path unchanged as the fallback | 7 |
| R-12 | Pack v3 validation too strict/too loose (manifest authoring burden) | Med | Adoption | Backward compatible by construction (verified against the current registry); template gains commented sections; calibration loop improves descriptions | 4 |
| R-13 | **Rate limits / provider unavailability** — BYOK LLM provider 429/timeout; endpoint 429 at 100 req/min/key (catch-up commits after offline capture + hold re-submissions can exceed it) | Med | DX, capture failure | Mode-dependent fail-closed vs regex baseline; bounded retries (≤5/session) on the cheap model; replay-safe retries via client_commit_id; **RESOLVED at plan: the commit endpoint gets a dedicated higher bucket (300 req/min/key, decided + tested in slice 5)** | 5, 6 |
| R-14 | **L2 re-capture churn** — after an extractor/pack/calibration bump, re-extraction drifts LLM output → mass supersede + full delta counts as net-new → cumulative budget burns → legitimate re-captures hit the 50 ceiling (402) | Med-High (once the calibration loop is live) | Value loss via 402 | **Supersede-only deltas do NOT increment net-new** (a new point superseding an existing same-session point is exempt — authoritative budget block §6.1); documented generation-depth cap for supersession chains; DE2E-7 bump-then-re-capture fixture | 5, 6, 8 |
| R-15 | **Extractor ships before any real-model quality measurement** — Layer-1 fixtures are MockModel-only; the first P/R measurement (slice-8 gold set) lands after slice 7 exposes real users to the pipeline | Med-High | Quality, broken Layer-1 contracts in production | **Slice-6 acceptance: run the 2-window rubric material through the NEW S0-S6 pipeline with a REAL model (extends the existing judge harness — zero new tooling); gate slice 7 on that smoke** | 6, 8 |
| R-16 | **Cross-team pack content changes break the shared registry/brief** — dev/marketing manifest PRs (slice 4) resolve cross-pack post-load; strict load-time validation makes one team's manifest a single point of failure for the extractor/enforcer/endpoint vocab | Med | Brief availability, CI | **Per-pack load isolation (fail the pack, not the registry)** + a whole-registry compile CI job on every pack PR (extends existing registry tests) | 4 |
| R-17 | **Watch-gate alert fatigue** — the plan's own math: a healthy 0.90 system trips the <0.80 block 11% of the time (N=12) and the rolling N=20 live floor ~1-in-3 checks | High (by design math) | Gate atrophies into ignored noise | Drift monitor alerts only on consecutive misses (2-of-3 rolling); CI watch-gate reports grouped weekly; "warn ≠ failure" UX in the eval workflow | 8 |
| R-18 | **Legacy nodes lack `is_episodic`** — pre-existing regex-path captures (Session/Event/Source/Points without the flag) over-count under the post-fix points quota → false 402s persist for the teams that already use capture | High (existing tenants) | False 402s | Backfill migration ships WITH slice 2 (one Cypher query); DE2E-7 legacy-node fixture | 2 |
| R-19 | **P9/A8 live-surface gate skip-guarded in CI** — embedded EP numerics diverge; a Docker-less CI run never executes the test yet reports green | High (default CI lacks Docker) | Surfacing claimed on unverified numbers | Slice-6 acceptance REQUIRES a recorded live-surface run (scheduled Docker job writes a live-run artifact to the PR before the surfacing claim is allowed) | 6 |
| R-20 | **Telemetry volume/retention unbounded** — every commit posts a telemetry block into the analytics store; bytes/node is the economic swing variable | Med | Cost | Per-commit telemetry SIZE cap asserted in the Layer-1 schema test; retention + aggregation policy defined at slice 8 | 5, 8 |
| R-21 | **Workspace/tooling hazards (operational)** — stashed pricing refactor touches quota.py/hosted_api.py (EXACT slice 2/5 files); stale checkout base; agent-infra divergence; gh rate limits (HANDOFF §9) | Med | Silent corruption of slices 2/5 | **Slice-2 pre-flight: verify fresh worktree from origin/main + stash isolation + diff review vs the stash; budget gh rate-limit retries; do not push agent-infra until divergence reconciled** (§8.4) | 2, 5 |

### 8.3 Build order (restated for Decompose — dependency-ordered, gate first)

| # | Slice | Key dependencies | Produces |
|---|---|---|---|
| 1 | **2-window rubric validation + tooling** (judge harness + kappa script) — THE GATE | window #2 transcript (owner) | gate decision GO/REVISE |
| 2 | Quota fix + budget constants (sessions branch, is_episodic, MAX_VALUE_POINTS_PER_SESSION, MAX_PAYLOAD_POINTS, MAX_ENTITIES/MAX_OPERATORS) | — | unblocks 5 |
| 3 | Ontology amendments (§4.3 — 13 registrations) | — | needed by 5 (payload fields) + 6 (vocab) |
| 4 | Pack manifest v3 (kindDefs/chains/extractable/extraction + validation + template + product-strategy/dev/marketing content + domain_loader unification + core-entity expansion fix) | — | unblocks 6 (value brief) |
| 5 | Session-commit endpoint (commit_schema.py + :CommitRecord + Layer-1 + idempotency + budget + metering + telemetry) | 2, 3 | the receiver |
| 6 | Local value-first extractor (value_brief.py + value_extractor.py S0-S3/S5/S6 + extraction_enforcer.py + P9/A8 test) | 3, 4 | unblocks 7 |
| 7 | SDK commit producer (DerivedCommitPayload + commit_session + hold_queue + extension rework) | 5, 6 | the producer |
| 8 | Evaluation (metrics.py — SKELETON from tests/aries_extraction.py's load_sample/evaluate/run_variant + gold set 30 windows SEEDED from tests/gold/0323_excerpt + gold_standard.json + eval_results_v2.json + window #1's converged labels (probe-extraction-window1-v3.md) + thresholds.yaml reconciliation A1-A22 + CI + drift monitor) | 6 (violation events), 1 (gate), 2 (constants) | the gatekeeper |
| 9 | Privacy (secret-scan hardening, wording, telemetry schema test) | 5, 6 | the guarantee |

**Dependencies:** 2→5, 3→5, 3→6, 4→6, 6→7, 1→8 (gate feeds the gold-set design), 6→8 (ExtractionViolation events feed metrics), 5→9 (endpoint telemetry is the privacy test target), 2→8 (constants feed thresholds reconciliation).

**Sequencing notes (critical path = the owner wait):** the gate (slice 1) is first;
slice-8 scaffolding (types.py, compute_metrics, kappa, thresholds.yaml skeleton — all
specified in §6.3, extractor-independent) MAY start right after the gate, in parallel
with slices 2-4; gold-set window selection happens DURING the gate (slice 1.5 owner
workstream: window selection + labeling starts at gate-green); slice 7 is gated on the
slice-6 real-model smoke (R-15).

### 8.4 Readiness for decomposition (MECE sketch)

The 9 slices decompose into issues with these boundaries (each slice = 1-3 issues; gate slice = 2 issues: harness + gate run):
1. judge harness + kappa script → gate run (owner-labeled) — 2 issues
2. quota sessions branch + is_episodic + constants + **legacy is_episodic backfill migration** — 1-2 issues
3. ONTOLOGY.md amendments + verify_ontology — 1 issue
4. pack_registry v3 schema/validation/template + pack content PRs (product-strategy, dev, marketing) + domain_loader unification + **per-pack load isolation + whole-registry compile CI job** — 2-3 issues
5. commit endpoint (schema + idempotency + budget + metering + telemetry + **dedicated rate-limit bucket** + :CommitRecord) — 2 issues (contract + handler)
6. value_brief compiler (#954) + extractor pipeline (#955) + enforcer (#956) + P9/A8 test (#957) — 4 issues
7. SDK producer + extension rework — 2 issues
8. metrics.py (**skeleton: aries_extraction.py**) + gold set (**seeds: tests/gold/*, eval_results_v2, window-1 labels**) + thresholds reconciliation + CI (**weekly watch-gate reports; 2-of-3 consecutive-miss drift alerts**) + drift monitor (**deterministic floors only in v1**) — 3 issues
9. privacy — 1 issue

**Operational pre-flight (R-21, applies to slice 2/5 work):** fresh worktree from origin/main (the local checkout is stale, ~130 commits behind, on a stray branch); the stashed pricing refactor touches quota.py/hosted_api.py — diff-review against stash@{0} before committing; budget gh rate-limit retries; do not push agent-infra until the divergence is reconciled.

**Open items carried to Decompose/Verify (not blocking the plan):** PR #854 stays the living doc home (this plan lands there); #870 decision; window #2 scheduling is the first dependency; the mitigation-recall block value is provisional until gold-set calibration; managed-key pricing remains fully deferred (not in v1).

---

*Plan complete — all 8 substeps with review gates.*

## Review record (verification audit, 2026-08-11)

| Phase | Reviewers (fresh-context) | Cycles | Findings | Outcome |
|---|---|---|---|---|
| §1-3 (journeys/workflows/prototype) | ux-coverage, ux-consistency, ux-realism | 2 rounds (parallel + convergence) | ~21 | all fixed (P0×2: hold-queue promotion, MITIGATES payload gap) |
| §4-6 (data model/architecture/interfaces) | schema-correctness, ontology-alignment, architectural-soundness, integration, contract-completeness | 2 rounds (3 parallel + convergence) | ~35 | all fixed (P0×2: CORRECTS direction, aboutEntity; P0: :CommitRecord storage) |
| §7 (detailed E2E) | e2e-coverage, e2e-reproducibility, test-quality | 1 round (3 parallel) — fixes re-verified via the §8 reviews (not a separate round) | ~30 | all fixed (P0: canonical MITIGATES probe missing → DE2E-11 added; Layer-2 N-source + fixtures hardened) |
| §8 (coherence/risk) | cross-substep-drift, risk-completeness, improvement-opportunities | 1 round (3 parallel) — fixes re-verified via the decompose MECE + verify audit (not a separate round) | ~30 | all fixed (P1s: PL4 hold-billing, version-trigger, process-routing band; R-13..R-21 added) |
| Decompose | per-issue batches (×5) + MECE (×2) | 3 rounds (5 parallel + MECE ×2) | ~22 | all fixed → MECE CLEAN (05-decompose.md) |
| Verify audit | verification reviewer + convergence reviewer | 2 rounds | 13×P2 | all fixed (this record, PL1-PL4 rename, table rebuild, cite corrections) |

Final state: 0 open P0/P1 across all gates (P2s resolved in-line or recorded as v1.1 deferrals). Total: 11 reviewer rounds (plan 6 + decompose 3 + verify 2) + 1 mechanical arithmetic fix, ~151 findings (116 + 22 + 13).
