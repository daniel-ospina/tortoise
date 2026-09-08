<!-- research-path: docs/research/2026-09-08-2165-solution-spec-v2.md -->

# Connected Assembly (what/when/why) Implementation Plan — #2165

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Ship a deterministic (zero-added-LLM-call) connected-assembly read path — "what (state) + when (dated timeline) + why (evidence with EP/provenance)" — as a real product feature: private pre-retrieval shape routing in `sdk.ask()`, assembled-block evidence rendered through the existing budget machinery, plus `sdk.ask_assembled()` and an eval v2-lane arm. Pre-customer product: this SHIPS (flag default OFF is a byte-identity safety posture, not a deferral).

**Team:** epistemic-team
**Architecture:** PRCA — a private pre-retrieval shape classifier (current-state / ordering / compare / interval / date-lookup) fires a pool-REPLACING branch in `ask()`. The block materializes as ordinary annotated hit dicts (state header + chronological dated spine + evidence) flowing through the UNCHANGED `assemble_context`/`render_context` machinery (whole-hit 8k-token/32-KiB caps, alignment invariant, D8 markers) — pure `tortoise/assembly.py` core (injected projection, function-level imports) + shared `_assemble_connected()` + public `sdk.ask_assembled()`. Supersession explicit from the #2242 fold cache; superseded excluded from current-state; as-of by validity math. Env `TORTOISE_ASK_CONNECTED_ASSEMBLY` default OFF. No new reader fragments, no qtype-enum change, no wire-field change.

**Binding requirements:** `docs/research/2026-09-08-2165-solution-spec-v2.md` R1-R17 (read it — this plan encodes each; every task's Acceptance cites its R#). Research base: the four `docs/research/2026-09-08-*.md` passes + synthesis.

### Pattern Research
Skipped — plan touches zero third-party dependencies (pure graph reads over the existing schema; no new libs). Prior research intake (Step A) consumed: spec v2 constraints 1-9 + competitor/GraphRAG/temporal/eval evidence in the four research docs.

### Integration Surface Map
| Surface | Test Layer | Notes / Bug-pattern flags |
|---|---|---|
| `tortoise/assembly.py` (new, pure) | unit (no DB via dict-stubbed GraphPort) + docker integration | classifier precision negative set; resolver chain order; date tier; sort determinism (R17 P3-6 secondary key) |
| `tortoise/sdk.py` ask() seam | integration (docker, FakeReader idiom from test_ask_sdk.py) | flag-off byte-identity (R17 goldens); fired-path locals (R11); qtype untouched; W4 explicit enrich (R5/R17); synthesized rows never fake point_id |
| `sdk.ask_assembled()` | integration | single-source `_assemble_connected`; hosted-delegated boundary (R14) |
| ingest_v2 Event-aboutObject | integration | R8 ~4-line MERGE; does NOT touch ingest.py (#2578 substrate) |
| Object resolution (exact probe / docker FTS / alias) | unit + docker | R7 honest chain; embedded degrade (R10 mock) |
| Supersession/as-of | unit | `_RECALL_OBJECT_EXCLUDED_STATUS` tuple (R17 P3-3); supersededAt returned by walk query (R17 P3-1) |
| Eval v2-lane arm | integration (docker) | R3 A-vs-B-vs-A-widened + matched control; R9 geometric fidelity; R15 census |
| config/ci-surfaces.yml | manifest | R4 same-commit registration of every new test file (#1262 drift gate) |
| W4 why-layer | integration | R5/R17: enrich_items BEFORE assemble_context on fired path |
| Docs (ONTOLOGY §4.3 supersededAt, answer-surface.md, .env.example) | contract | R17 P3-1 doc duty; env master-flag semantics |

### Failure Modes
- Fired + zero-Object graph / unresolved subject → `fired=False`, legacy byte-identical, no error/empty (R1/R7) → test per shape.
- One-half resolved on a compare → fired=False (R1 both-halves gate) → test.
- False-positive fire on two-named-option preference Q → fired=False (R16 misfire fixtures + precision floor) → test.
- Undated rows / 1970 sentinel → sentinel-strip then undated-last `[session ?]`, never a fabricated date (R2/R17 P3-2) → test.
- 200-char supersededBy-name truncation → explicit truncated-successor annotation, never a fabricated link (R12/C6) → test.
- Raw object-fetch hit (flat-string superseded_by) reaching `_validity_marker` → forbidden; state rows freshly synthesized with dict shape (R17 P2-1) → guard test.
- Budget overflow → slice-level truncation authoritative (oldest/lowest-EP first); `assemble_context` whole-hit no-starvation backstop (R12/R17 P3-4) → overflow fixture.
- `hits`/`leg_trace` unbound on fired path → bound stubs before branch (R11) → test.
- W4 flag on + fired → why entries survive via explicit enrich; synthesized rows contribute none (R5/R17) → test.
- Reader decay / distractor admission → pool replaced (no legacy noise), per-slice caps under ~8-12k, verbatim-over-summarize → caps property test.

**Tech Stack:** Python 3.12+, FalkorDB (docker lane) / embedded; pytest; no new deps.

---

## Task 1: Fixture graph + config ground truth + ingest_v2 Event-aboutObject fix

**Intent:** Standing synthetic multi-session Object-structured fixture mirroring the v2 lane (Objects incl. supersession chain, aboutObject Points with sparse `when`, dated sessions, distractors, deep-rank gold marks) + the env flag registered + the ingest_v2 gap fixed, so every later task has real substrate. (R2, R4, R8, R9, R15-census-seed, R17 P3-2)
**Acceptance:** `tests/_assembly_graph.py` + `tests/test_assembly_fixtures.py` exist and pass on the docker lane; fixture mirrors ingest_v2 faithfully (Points carry NO eventId; sparse `when`; createdAt sentinel-able; zero Event-aboutObject pre-fix); `TORTOISE_ASK_CONNECTED_ASSEMBLY` in `.env.example`; ingest_v2 event loop MERGEs `about_entities` (test: event with about_entities → edge exists); ci-surfaces.yml registers each new test file in the SAME commit (R4).
**Files:**
- Create: `tests/_assembly_graph.py` (underscore helper — excluded from ci-surfaces), `tests/test_assembly_fixtures.py`
- Modify: `tools/longmem_eval/ingest_v2.py` (event loop ~246-290: pass `about_entities` to create_event), `.env.example` (ask-lane block), `config/ci-surfaces.yml`
- Test: `tests/test_assembly_fixtures.py`

**Step 1:** Register the flag in `.env.example` (ask-lane `#2070` block): `TORTOISE_ASK_CONNECTED_ASSEMBLY` (ask_env_bool semantics; master flag for the eval arm; default OFF).
**Step 2:** Write the fixture builder `tests/_assembly_graph.py` — docker-lane idiom from `tests/test_entity_key_expansion.py` (`_fresh_uri`, `_no_embedder`): (a) ≥2 dated sessions; (b) Objects `couch`/`dog bed` (+ a supersession chain: `couch` superseded by `sofa` via `apply_supersessions` or direct fold); (c) aboutObject-linked Points across sessions with EP persisted through operator/EP-writing SDK paths (R12 EP-realism), sparse `when` (only some stamped), `createdAt` = session date; (d) Events with startedAt, ZERO aboutObject edges pre-fix (v2-lane faithful); (e) ≥12 distractor points; (f) deep-rank gold marking helper (a marker prop for the R9 geometric test).
**Step 3:** Write `tests/test_assembly_fixtures.py` asserting fixture shape (counts, no point-eventId, sparse-when distribution, sentinel-ability).
**Step 4:** Fix ingest_v2's event loop to pass `about_entities` through to `create_event` (which wires `(Event)-[:aboutObject]` from props); test: an ingest_v2 run on a session whose extractor events carry `about_entities` yields the edges.
**Step 5:** Register `tests/test_assembly_fixtures.py` (+ all files this plan creates: `tests/test_assembly_pure.py`, `tests/test_assembly_sdk.py`, `tests/longmem_eval/test_assembly_arm.py`) in `config/ci-surfaces.yml` under the appropriate surfaces — SAME commit (R4/#1262).
**Step 6:** Run docker-lane fixture tests green + `python3 tools/ci_selection.py --integrity` clean.
**Step 7:** Commit: `test(fixtures): #2165 synthetic multi-session Object graph + ingest_v2 Event-aboutObject wiring + env flag + ci-surfaces`.

## Task 2: Pure classifier — shape routing + subject extraction (R13, R15, R16)

**Intent:** Deterministic pre-retrieval router owns the fired decision. Shapes: current-state / ordering / compare / interval / date-lookup. 'ago'-relative + implicit-time do NOT match and fall through (R13). Precision is the primary lever (R16). (R15 census informs the pattern set.)
**Acceptance:** `tests/test_assembly_pure.py` green: positive set fires per shape; NEGATIVE set never fires (preference/advice with two named options "compare X and Y which should I pick" → None; single-session; "how many times"; 'ago'-relative "two weeks ago" → None even with state morphology "what was the status of X two weeks ago?" — reject-on-relative-offset guard R13-P3-2); both-subject extraction works for "which came first X or Y" / "days between A and B" incl. quotes/possessives ("my cousin's wedding or Michael's engagement party").
**Files:**
- Create: `tortoise/assembly.py` (module scaffold: `classify_question`, `extract_subject_terms`), `tests/test_assembly_pure.py`
- Test: `tests/test_assembly_pure.py`

**Step 1:** Failing tests: shape table (positive per shape + negative set + relative-offset-reject + both-subject extraction table).
**Step 2:** Run → FAIL (module absent).
**Step 3:** Implement `classify_question(question) -> AssemblyShape | None` (ordered high-precision regexes, no LLM) + `extract_subject_terms(question, shape) -> list[str]` (template split for two-subject shapes; single subject for current-state/date-lookup).
**Step 4:** Green. **Step 5:** Commit: `feat(assembly): #2165 shape classifier + subject-term extraction (RED→GREEN)`.

## Task 3: Resolver — recall-first, confidence-tagged, no LLM (R1, R7, R17 P3-3)

**Intent:** Question prose → canonical Object candidates deterministically, with both-halves gating (R1). Honest chain: exact-name/id index probe (embedded-safe) → docker Object name-FTS → second-pass alias amplifier from anchored points' search_keys. Never a silent single-match (R7).
**Acceptance:** Candidates carry `{object_id, name, confidence: high|med|low, source}`; both-subject shapes admit BOTH halves or fire nothing (R1); exact-probe path runs on a dict-stubbed GraphPort (no DB); docker FTS leg covered docker-lane; embedded degrade (FTS missing/empty → [] + no raise) via mock (R10); multi-candidate admitted with tags; collision case returns both with ids (R12/C7 — the renderer sectioning requirement is Task 4).
**Files:**
- Modify: `tortoise/assembly.py`
- Test: `tests/test_assembly_pure.py` (resolver section)

**Step 1:** Failing tests (stub-GraphPort exact probe; two-subject admit-both; one-half → fired=False signal; FTS absent → []).
**Step 2:** RED. **Step 3:** Implement `resolve_subjects(port, terms) -> list[SubjectCandidate]` with the R7 chain + confidence tags.
**Step 4:** Green (docker + stub). **Step 5:** Commit.

## Task 4: Typed walker + slice builder (R2, R3-8, R12, R17 P3-1/P3-6)

**Intent:** One batched typed walk (never N+1, never blind BFS — SubgraphExpander NOT used) over the connected subgraph: state slice (Object status/supersededBy/supersededAt incl. `RETURN o.supersededAt` R17 P3-1, exclusion by `_RECALL_OBJECT_EXCLUDED_STATUS` R17 P3-3), dated spine (aboutObject Points ∪ post-R8 Event-aboutObject edges ∪ product-lane eventId join), evidence slice (points with validity + EP). Per-lane date precedence when → createdAt (sentinel-stripped) → eventId-joined startedAt (R2); tier-tag rows; bounded pre-fetch caps (R12/C8); deterministic tiebreak on same-date rows (R17 P3-6).
**Acceptance:** slice builder returns typed rows with tier tags + admission metadata `{rows_requested, rows_admitted, truncated}`; v2-lane-faithful fixture (zero point-eventId) assembles a dated spine from tier-2 createdAt; hosted variant (Event-aboutObject present) superset; as-of window excludes post-D rows; undated → last, no sentinel masquerade; hub fan-out cap deterministic.
**Files:**
- Modify: `tortoise/assembly.py`
- Test: `tests/test_assembly_pure.py` (walker section, docker fixture)

**Step 1:** Failing tests per acceptance. **Step 2:** RED. **Step 3:** Implement `collect_slices(port, subjects, shape, question_date) -> AssemblySlices`. **Step 4:** Green. **Step 5:** Commit.

## Task 5: Renderer — synthesized hits, date-sorted block, deterministic ordering/diff (R1, R2, R3-5, R6, R17 P2-1)

**Intent:** Slices → ordinary annotated hit dicts the unchanged `assemble_context`/`_render_block` render (constraint 1/5). State header + per-subject sectioning (R12/C7) + chronological dated spine + evidence lines with dict-shaped `superseded_by={"content_snippet":…}` (R17 P2-1), real point rows carry honest `session_date`/`speaker`/point_id/validity keys; ordering/interval shapes precompute the ordering/diff line deterministically (constraint 6); dedupe by point id (constraint 4); superseded excluded from current-state view, history retained for as-of (constraint 3); under budget the slice-level truncation is authoritative (oldest/lowest-EP), `assemble_context` the no-starvation backstop (R12/R17 P3-4).
**Acceptance:** rendered line list byte-parity with the same point through the ask FTS path (docker, marker test); ordering line matches the test's own date-lib math; wrong-order / +superseded / duplicate matched-control arms produce the expected deltas (R3/constraint 8); per-candidate sectioning keeps two same-named entities separate (R12/C7); 200-char supersededBy annotated, not fabricated (R12/C6).
**Files:**
- Modify: `tortoise/assembly.py`
- Test: `tests/test_assembly_pure.py` (render section — pure over slices, no DB)

**Step 1:** Failing render tests (incl. byte-parity + controls). **Step 2:** RED. **Step 3:** Implement `synthesize_hits(slices, shape, question_date) -> list[dict]` (pure) + sorter + diff synthesis + dedupe. **Step 4:** Green. **Step 5:** Commit.

## Task 6: ask() branch + ask_assembled + W4 + byte-identity (R5, R6, R11, R14, R17)

**Intent:** The product seam. `_assemble_connected()` single-source for `ask()`'s branch (before retrieval) and public `sdk.ask_assembled()`. Fired path: classify → resolve (both-halves gate R1) → walk → render → decorate real rows (EP/D8) → explicit `why.enrich_items` when W4 on (R5/R17) → `assemble_context(caps)` → `render_context` → existing qtype detect (unchanged) → ONE reader call. Bound `hits`/`leg_trace` stubs (R11). Flag OFF / unrouted / unresolved → legacy byte-identical by construction (branch precedes retrieval).
**Acceptance:** flag-OFF golden evidence equality for fired-shape Qs (pre-branch golden capture committed with the branch — R17 invalidation policy: which config changes require re-capture); flag-ON + fired → assembled evidence under caps + alignment invariant + exactly ONE model call; both-flags-on → why survives on real evidence points, synthesized rows contribute none; fired-but-unresolved → legacy fallback no error; qtype on the wire unchanged (incl. caller override case pinned R17/P3-1); hosted Event-aboutObject-present graph test (R6); regression: test_ask_sdk.py / test_ask_api.py / test_reader.py / test_retrieval.py / test_ask_retrieval_levers.py green.
**Files:**
- Modify: `tortoise/sdk.py` (ask() seam ~12606 + new `ask_assembled`), `tortoise/assembly.py`
- Create: `tests/test_assembly_sdk.py`
- Test: `tests/test_assembly_sdk.py`

**Step 1:** Capture golden evidence strings (flag OFF, pre-branch) for the fired-shape question set on the fixture (deterministic text; no LLM in the evidence path) → commit them with this task's code (R17).
**Step 2:** Failing tests: byte-identity-off (golden equality + flag-ON-unrouted A/B), fired routing (state header + ≥2 dated lines from ≥2 sessions), one-half compare → legacy, misfire two-option preference → legacy (R16), caps property (oversized history → ≤32 KiB / ≤8k tokens, whole-line drops, alignment invariant), W4 both-flags, qtype pin, exactly-one-call spy, R6 hosted-shape.
**Step 3:** RED. **Step 4:** Implement `_assemble_connected`, the ask() branch (function-level imports; env gate first statement), `ask_assembled`. **Step 5:** Green + full ask regression. **Step 6:** Commit: `feat(ask): #2165 connected-assembly branch + ask_assembled (flag-off byte-identical)`.

## Task 7: Eval v2-lane arm + geometric fidelity + controls + docs (R3, R9, R15, R17 P3-1)

**Intent:** Measure assembly honestly: does the needed evidence reach the reader AND does the pinned reader convert it — assembled vs legacy (default caps) vs legacy (lane-1-parity widened caps), plus ≥1 matched control (R3/R15). R9 geometric-fidelity fixture reproduces the deep-rank diagnosis ON the Object lane (gold beyond legacy admission; legacy admits <2; assembled admits ≥2) — control for the A-widened arm, not default-legacy (R15).
**Acceptance:** arm reports per-slice admission (which gold point ids reached the post-cap line list) + reader-conversion delta (B ≥ A / B ≥ A-widened or pre-registered report-only abstention-aware delta); R9 fixture: legacy admits <2 gold rows, assembled admits ≥2; 133-Q shape census result recorded (which sub-classes match the 5 shapes — R15); supersededAt added to docs/ONTOLOGY.md §4.3 (R17 P3-1 doc duty); answer-surface.md additive note; plan's out-of-scope list (ago-relative, N-ary) recorded as follow-ups.
**Files:**
- Modify: `tools/longmem_eval/retrieve.py` (env-gated `TORTOISE_LME_ASSEMBLY` arm calling `ask_assembled`/`_assemble_connected` on the v2-lane graph), `docs/ONTOLOGY.md`, `docs/product/answer-surface.md`
- Create: `tests/longmem_eval/test_assembly_arm.py` (layout precedent `test_vector_arm.py`)
- Test: `tests/longmem_eval/test_assembly_arm.py`

**Step 1:** Failing test: gold turns across ≥2 sessions both in the post-cap line list on the fired shape (v2-lane graph); legacy control admits <2. **Step 2:** RED. **Step 3:** Wire the arm. **Step 4:** Green incl. A-widened comparison + matched-control test. **Step 5:** Docs (ONTOLOGY supersededAt; answer-surface note). **Step 6:** Full acceptance: docker-lane targeted suites + `uv lock --check` + ruff + `ci_selection.py --integrity`. **Step 7:** Commit + plan-status note.

---

## Out of scope (recorded follow-ups, per R13/R14/spec)
- 'ago'-relative / implicit-time resolution (needs LLM anchor+offset — constraint 6); N-ary decomposition beyond two named subjects; per-shape reader fragments (post-#2013); LLM resolution fallback; fold-time snapshot store (post-#2164/#2349); append-legacy knob behind measurement. The 13-Q measurement + admission-widening ablation is lane 1 (#2578). Density→reader-answers link measured but gated honestly (R3/R17).

## Accepted divergences (from issue body)
- Re-scoped per human gate 2026-09-08 (dual-lane); assembler acceptance is synthetic-graph + no-regression, NOT the 13-Q score. ask() ships (de-provisionalized same date). No new reader fragments/qtype/wire fields in v1.
