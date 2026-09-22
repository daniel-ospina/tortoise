# Solution Diverge — Agent A · #2165 Connected Assembly (what/when/why)

**Date:** 2026-09-08 · **Mode:** solution-diverge (no winner — 3 distinct architectures)
**Role contract:** propose a distinct architecture set; where overlap with the other diverge agent's set exists, differentiate on the architecture axis (router surface / block entry / walker / subject resolution / module shape), not filenames. Companion doc: `2026-09-08-2165-connected-assembly-solutions-diverge-b.md` (Agent B). No winner picked.
**Problem (human-approved):** deterministic (zero-added-LLM) assembler on the product ask lane. Question → resolve subject(s) → walk the connected subgraph (Object state/lifecycle + dated Events + aboutObject-linked Points w/ EP + provenance) → render ONE dense structured block (state + timeline + evidence) → existing single reader LLM. Flag-off additive (byte-identical existing point-only path when off/unrouted). Acceptance = synthetic multi-session graph assembly tests + no regression on point-only retrieval. NOT the 13-Q measurement lane (#2578).
**Evidence base:** `docs/research/2026-09-08-2165-connected-assembly-synthesis.md` (constraints 1–9), the three `2026-09-08-*` research passes, Agent B's diverge doc, `docs/product/answer-surface.md`, `gh issue view 2165` + comments, targeted code reads. **[V]** = verified in source on this worktree.

---

## Verified code facts (A's additions to the shared surface — read before options)

All of Agent B's **[V]** facts reproduce on this read. A's independent verifications that CHANGE the architecture space:

- **[V] The v2-eval-lane Event spine has NO aboutObject edges to Objects.** `tools/longmem_eval/ingest_v2.py` event loop (~246–290) creates Events via `sdk.create_event(event_name, event_kind, sessionId=..., lme_question_id=..., lme_session_index=..., is_episodic=True, **event_props)` with **no about_entities / aboutObject wiring**; only the POINT loop MERGEs `(p)-[:aboutObject]->(o)` (ingest_v2:209–218). So an "Event-[:aboutObject]->Object" walk finds ZERO rows on the eval synthetic graph. The dated spine for a subject on that lane must be built from **aboutObject-linked Points dated by `when`/`validFrom`/session-date, plus the Events sharing those Points' `eventId`/`sessionId`** (the same join `annotate_ask_hits` already does: sdk.py ~11418 Event startedAt join). Hosted-lane Events DO carry aboutObject (`hosted_api.py` ~7920–7940, MERGE (e)-[:aboutObject]->(o)); the assembler must not assume which lane produced the graph. **Timeline-spine tension resolved by lane: spine = dated aboutObject Points ∪ session-joined Events, with hosted Event-aboutObject edges as an optional second surface when present.**
- **[V] Product ask-lane Points are already session-dated via an Event join.** `annotate_ask_hits` (sdk.py ~11418) joins `(ev:Event) WHERE ev.eventId = n.eventId` and stamps `session_date = startedAt[:10]`, `session_id`, `speaker` — so an assembled timeline can reuse the product's own per-point date decoration instead of inventing a new date source for Point rows.
- **[V] Object-name exact/id reads are index-backed at EVERY FalkorDB version; Object-name FTS is docker/≥4.x-only.** Range indexes on Object(id)/Object(name) are created unconditionally (projection/__init__.py ~2117); fulltext+vector index creation is gated to `_ver is None or _ver[0] >= 4` (~2139–2141), Object FTS fields = `["name"]` (~2156). Consequences: (a) exact-name `MATCH (o:Object {name:$name})` and id-keyed reads work on embedded; (b) any resolution depending on the Object-name **fulltext** leg (incl. the C2 `_entity_key_expansion_pass` anchor step, sdk.py ~12448 `run_fts_query(entity_type="object")`) silently no-ops on embedded/no-FTS. A docker-only resolver is a real portability cliff for a *product* lane — embedded self-host is a supported ask surface.
- **[V] ask() retrieves with `entity_type="point"` only** (sdk.py ~12751) and **`detect_question_type` runs AFTER assembly** (sdk.py ~12848) purely to pick a prompt fragment — qtype never gates retrieval today. `ASK_QUESTION_TYPES` (schemas.py:19) is a closed enum shared by local validation, hosted validation and the answer-surface docs; adding values is a contract change (400 list, hosted pipeline parity, answer-surface doc).
- **[V] The deterministic eval lane (13-Q lane) writes zero Objects** (issue comment) — every fired assembler must degrade to the legacy point-only path when subject resolution finds nothing, on BOTH lanes (a no-op guard, not an error).
- **[V] `_entity_key_expansion_pass` (sdk.py ~12390) is the shipped anchor-resolution precedent** — bounded Object-name FTS anchors (`_ENTITY_ANCHOR_LIMIT=3`, candidates 20), one batched aboutObject harvest query, additive fail-open re-run. It resolves subjects THROUGH THE INDEX (docker), never by exact name.
- **[V] Object rows fetched by `tortoise_fts_query(entity_type="object")`** carry `id/name/objectKind/status/superseded_by` (sdk.py ~12121); status values live/superseded/deprecated/archived/retracted; Object supersession fold (`commit_ops.py:297+`, `_fold_object_superseded`) stamps `status='superseded'`, `supersededBy` (successor NAME, truncated 200), `supersededAt`.
- **[V] ask reader evidence caps are hit-level and budget-level both** — `assemble_context` (8k est-token + 32 KiB byte caps, whole-hit drop) then `render_context` prepends `Current Date:` + per-hit `[session N] (session date D) [SUPERSEDED BY: …]` blocks via `_render_block`. Any architecture that emits the block as ORDINARY annotated hits inherits dedup/order/caps/W4/byte-accounting for free; any architecture that emits a monolithic string must re-implement budget math.

---

## OPTION A1 — Contract-native assembly: assembly shapes become first-class `question_type`s (router = the product's own type contract)

**Name:** "qtype-native routed replacement" — the opposite pole from B1 on the router axis.

### Description
Extend the ask lane's OWN type contract rather than adding a standalone pre-router: add assembly-owned shape values to the closed `ASK_QUESTION_TYPES` enum (schemas.py), extend `detect_question_type` (reader.py) with ordered compare/ordering/interval/current-state/date-lookup rules that return the new values, extend `_TYPE_FRAGMENTS`/`system_prompt_for` with per-shape reader fragments, and in `ask()` run detection **before retrieval** for the assembly-owned types, taking a widened retrieval+render branch that REPLACES the point pool with the assembled block. The wire response's `question_type` field truthfully reports which shape the reader saw. Hosted and local share schemas.py as the single validation source; the eval's dataset already carries `question_type` per question, so eval parity is dataset-driven (eval questions labeled `temporal-reasoning` map to the same assembly shapes internally).

### Files touched
- `tortoise/schemas.py` — `ASK_QUESTION_TYPES` gains the assembly shape values (or one `connected-assembly` value + internal shape field); validation list + docs update.
- `tortoise/reader.py` — `detect_question_type` new ordered rules + `_TYPE_FRAGMENTS` additions (assembly-reading fragments: "state lines first, dated lines chronological, superseded marked", per constraint 5's reader-decay evidence).
- `tortoise/sdk.py` `ask()` — reorder: detect early when the caller/env permits assembly; assembly-owned qtype → widened branch (subject resolution → typed walk → block replaces pool); else byte-identical steps 2–6. Hosted `_post_ask` and `/v1/ask` must implement the same branch server-side for contract parity.
- New `tortoise/assembly.py` — pure-first renderer + walker with injected projection (house rule; retrieval.py/reader.py stay pure of sdk).
- Eval `tools/longmem_eval/retrieve.py` — the TR arm already routes dataset `temporal-reasoning`; wire the same shared assembly function for the synthetic-graph runs.
- Tests: `tests/test_assembly_qtype_*.py` (synthetic multi-session graph), regression `tests/test_ask_*.py` (flag off = byte-identical), schema validation tests for the new enum values.

### Architecture
1. **Detection first, contract-visible**: `detect_question_type` (extended) runs pre-retrieval ONLY when assembly is armed (env `TORTOISE_ASK_CONNECTED_ASSEMBLY` or new qtypes passed explicitly). Ordering/interval/current-state shapes now return real qtypes; response `question_type` exposes them. Trade-off by design: contract churn is the point — the assembled context becomes auditable from the response alone.
2. **Subject resolution** (constraint 7): deterministic recall-first — exact Object-name/id probe (index-backed, works everywhere) FIRST; Object FTS (`run_fts_query(entity_type="object")`) as the fuzzy second surface when the exact probe misses (docker); `search_keys` alias harvest as third; multi-candidate admit with confidence tags; **no LLM fallback in v1** (documented follow-up). No resolvable subject → qtype degrades to legacy (byte-identical pool) — never an error.
3. **Typed walk** (constraint 4): per resolved Object, ≤2 hops over typed edges only — aboutObject (both directions), the Point/Event session join (for the dated spine, per A's verification), Point CORRECTS/supersession and Object `supersededBy` chain; IMPL/NAND only for why-shapes, in-chain-first, hub-damped.
4. **Slice builder → replace**: three typed slices (state / timeline / evidence) under one hard ~8–12k budget; the assembled block IS the reader evidence for the fired shape (replacement, B1-family posture), under the SAME 8k/32 KiB enforcement (reuse `estimate_tokens_ask` + byte discipline).
5. **Reader fragments**: `system_prompt_for` gains per-assembly-shape instructions (constraint 5's reader-decay + refusal evidence; TR/KU fragments today already counter hedging — assembly fragments extend the same mechanism).

### Risks
- **Contract blast radius is the headline risk**: new enum values ripple into hosted validation (`/v1/ask` 400 list), `_post_ask` client, answer-surface.md, and require the HOSTED server to implement the same assembly branch — otherwise a hosted caller sending the new qtype gets a 400 or divergent behavior. If hosted parity is out of scope for this issue, the new values cannot ship on the wire → the enum extension collapses back into B1's internal router (the honest fallback).
- **Moving detection pre-retrieval changes ask()'s validated ordering** (detection today is post-assembly and side-effect-free); the early detect must be provably identical to the post-assembly one for the 4 legacy types when the flag is off.
- Reader-fragment tuning cost: 4–5 new fragments need the same calibration discipline as TR/KU (regression-testable via `reader_prompt_constants`, but each is a prompt-quality surface).
- Eval dataset only carries the LEGACY 4 types — the new values fire product-side only; eval must map dataset types onto assembly shapes internally or the eval never exercises them (test-surface gap to close deliberately).

### Tradeoffs
+ The router is transparent and auditable: `response.question_type` says what the reader consumed; hosted/local share one validation source.
+ Reader prompts are type-tailored per assembly shape — the strongest lever against the documented refusal/hedging losses (constraint 5 + synthesis #2), reused machinery (`system_prompt_for`, `reader_prompt_constants`).
+ Cleanest replace semantics for shape-owned questions (one dense block, no pool noise).
− Largest cross-system contract surface of the three (schemas + hosted + docs + prompt constants).
− Hosted-lane parity doubles the implementation (server-side branch) or forces the wire fallback.
− Only fires on detectable morphology; a non-morphological question that would benefit from connected evidence gets nothing (same v1 limitation as B1, by design).

### Best-fit if
The team wants assembly to be a **named, contract-visible mode of the answer surface** (question_type on the wire changes), is willing to do hosted-parity or explicitly defer it with a wire fallback, and wants per-shape reader fragments as the primary accuracy lever. **Differentiator vs B1: the router is the PUBLIC qtype contract (enum + fragments + response field), not a private pre-router; detection runs inside the shipped detector, and the reader is told per-shape how to read the block.**

### Constraints 1–9 compliance
1 ✅ ISO date-sorted spine emitted by the sorter; never reader-ordered · 2 ✅ state block then dated evidence (fragments reinforce the two-layer read) · 3 ✅ supersession explicit + filtered from default current state, as-of via validity math · 4 ✅ shape→typed walk (aboutObject + session join + supersession chain), ≤2 hops, hub-damped — no blind BFS · 5 ✅ per-slice caps under the single hard budget; verbatim evidence; assembly fragments counter reader decay · 6 ✅ compare/ordering/interval/date-lookup are first-class qtypes → both subjects fetched by construction; diffs/ordering precomputed; relative→absolute resolved at assembly · 7 ✅ recall-first deterministic resolution (exact name → FTS → aliases), confidence-tagged multi-candidate, zero LLM v1 · 8 ✅ eval maps dataset qtypes onto the same assembly function; per-slice admission measurable; shared reader prompt constants keep drift visible · 9 ✅ no blind BFS, no embedding gate on graph-certain hits, no community summaries, no LLM consolidation.

---

## OPTION A2 — Synthesized-hit assembly: the block is expressed as ordinary annotated hits in the EXISTING pool (C2-anchor resolution + no new renderer)

**Name:** "Assembly as first-class hits" — the third entry for axis (b)/(e): neither a monolithic replace nor a standalone entrypoint.

### Description
Resolve the subject(s) (C2-style anchor resolution), walk the connected slice, and **materialize each timeline/evidence line as an annotated hit dict** (`{content, session_date, speaker, status, superseded_by, id: synthetic}`) that enters the EXISTING annotate→dedup→assemble→render flow as first-class pool members — with a state header hit first. No new renderer, no new budget math: `render_context`'s `_render_block` already renders date + `[SUPERSEDED BY]` markers on any hit carrying those keys; `assemble_context` already enforces the 8k/32 KiB whole-hit caps over the whole pool including the synthesized hits; W4 why-layer, byte-accounting and `context_tokens` alignment all work unchanged because the block is just more hits. The reader sees ONE dense structured block (state line → chronological dated lines → evidence lines), assembled by ordering the synthesized hits correctly — then the legacy pool follows (additive) or is dropped (replace), per knob.

### Files touched
- `tortoise/sdk.py` — a new `ask()` step between retrieval and dedup (or post-annotate pre-dedup): when armed and shape/subject fires, run the anchor resolver + slice harvester and inject synthesized hits at the head of the pool. `_entity_key_expansion_pass`'s anchor+harvest machinery is the template (same batched single-query discipline, same fail-open).
- New small `tortoise/assembly.py` — resolver + walker + **hit synthesizer** (pure; injected projection). Rendering is NOT here — it is the existing `_render_block`.
- `tortoise/retrieval.py` — no change (or a `synthesize_hit()` pure helper colocated with `_render_block` so the hit schema lives next to its renderer).
- Eval `tools/longmem_eval/retrieve.py` — the synthetic-graph arm injects the same synthesized hits into its pool (shared function).
- Tests: synthesized-hit schema tests (render byte-parity vs a real point with the same keys), injection-order + caps tests, dedup interaction tests, point-only regression (flag off = no injection).

### Architecture
1. **Trigger** = shape OR subject-presence gate, post-validation (reuses the existing qtype detector for shapes; subject resolution = exact-name probe first, Object FTS second — embedded-safe).
2. **Subject resolution** (constraint 7): deterministic, exact-name/id probe (index-backed at every version) → fuzzy FTS spine when available → alias harvest; multi-candidate admit, confidence-tagged; no LLM v1. **No resolvable subject → zero synthesized hits → legacy byte-identical** (fail-open matches the C2 contract).
3. **Typed slice walk** (constraint 4): per anchor Object, ≤2 hops over aboutObject + the Event session join (A's verified spine: dated aboutObject Points ∪ session-joined Events), supersession chain; per-Object fan-out cap + hub damping; IMPL/NAND only for why-shapes.
4. **Hit synthesis** — the load-bearing move: each state line and dated item becomes an annotated hit:
   - state line: `{id: "asm:state:<object_id>", content: "STATE <name>: live (superseded by <B> on <D>) …", session_date: <latest>, status: <obj.status>}`;
   - timeline line: `{id: "asm:evt:<n>", content: "<verbatim dated item>", session_date: <when|startedAt[:10]>, speaker, status/validity keys}`;
   - evidence line: point passthrough already has EP/quote/validity — reused verbatim.
   Order = the assembled order (state first, then date-sorted); `assemble_context` preserves pool order and whole-hit-drops by rank, so a near-relevant distractor below the cap is dropped exactly like any low-rank hit (constraint 4's budget mechanism, unchanged).
5. **Placement knob**: `additive` (synthesized hits + legacy pool under one cap) or `replace` (synthesized hits only for fired shapes) — same synthesizer, one env knob; additive is the default posture (both-not-either precedent), replace the shape-owned strict posture.

### Risks
- **Hit-schema fidelity is load-bearing**: a state header is NOT a session turn; rendering it via `_render_block` prefixes `[session N] (session date D)` from its keys — must carry honest keys (state line gets the latest session date, or `[session ?]` when undated, mirroring how undated points render today). Reader sees a heterogeneous evidence (structure + rank list) — ordering within the prompt matters (structure first).
- **Dedup interaction**: `dedup_pool` keys on session (ask's `_ask_session_key`) — synthesized hits share the subject's session keys and could be capped by `DEFAULT_MAX_CHUNKS_PER_SESSION=3`; synthesized hits must either carry distinct synthetic session keys (e.g. `asm:<object_id>`) exempt from the per-session cap, or injection happens POST-dedup (post-annotate, pre-boost/assemble). This is a real wiring subtlety the other options don't face.
- The "ONE dense structured block" goal is achieved by ordering + adjacent rendering, not by a literal contiguous string — a reviewer checking for a literal block may see the structure as "more hits" unless the state header clearly delimits it (delimiter line in content: `── ASSEMBLED (state/timeline/evidence) ──`).
- C2 anchor resolution needs Object FTS for fuzzy matches (docker); embedded relies on the exact-name probe — recall on paraphrase questions is weaker embedded (accepted; documented, matches the docker/embedded posture of the FTS indexes themselves).

### Tradeoffs
+ **Smallest new render/budget surface of the three**: byte accounting, caps, W4, speaker/date decoration, supersession markers, context_tokens alignment are all inherited from the hit schema — nothing re-implemented.
+ Strongest "flag-off additive" story: off = zero synthesized hits = byte-identical by construction (no router tuning, no branch replacing the pool).
+ Both add/replace postures available behind one knob; eval shares the exact function.
− State-as-hit is a schema stretch; the block is visually interleaved with the pool in additive mode (structure first mitigates).
− Dedup/session-key interplay must be designed explicitly (post-dedup injection or synthetic session keys).
− Weaker than A1/B1 for compare shapes' "both halves by construction" (subject resolution is anchor-based; two subjects require both anchors to resolve — same resolver quality, but no qtype guarantees the shape).

### Best-fit if
The team wants the **minimum-fidelity-risk integration** (reuse every existing rendering/budget invariant), a byte-identical default by construction, embedded parity (exact-name resolution works everywhere; FTS is only the fuzzy second surface), and a shared synthesizer both lanes call. **Differentiator vs B3 (pool-seeded additive): trigger is shape/subject anchor resolution (pre-pool, independent of RRF recall), not pool presence; the walker is the C2-style typed harvester, not a `SubgraphExpander` extension; the block is ordinary hits, not a separate AEP object — so dedup/caps/W4 apply to it natively.**

### Constraints 1–9 compliance
1 ✅ synthesized dated hits are emitted in date order at injection; `_render_block` shows each line's own session date; reader never orders · 2 ✅ state-header hit first, dated hits beneath — two-layer shape in hit form · 3 ✅ superseded hits carry `[SUPERSEDED BY]` via existing keys; current-state lines filtered by Object.status, history kept as-of · 4 ✅ anchor-gated typed walk ≤2 hops + hub damping; near-relevant distractors drop via the EXISTING whole-hit budget (rank-ordered) — no blind BFS · 5 ✅ caps inherited (8k est-token + 32 KiB byte), whole-hit drop, verbatim evidence with provenance quote/EP keys · 6 ⚠️ partial — both-halves depends on anchor resolution of both subjects + additive pool complement; ordering/diffs still precomputed; interval math out of the reader · 7 ✅ exact-name-first deterministic resolution, confidence tags, no LLM v1 · 8 ✅ eval consumes the same synthesized hits; per-slice admission = per-hit admission on the shared pool (existing reader_evidence machinery) · 9 ✅ no blind BFS, no embedding gate on graph-certain hits (anchor resolution is sparse/exact), no summaries, no LLM consolidation.

---

## OPTION A3 — Standalone `ask_assembled` entrypoint + pure module; `ask()` delegates via a thin branch (shared block dict, eval-first)

**Name:** "Assembled-read surface" — the standalone-entrypoint entry for axis (b), modeled on the recall_* family precedent.

### Description
Build assembly as its OWN SDK surface first — `sdk.ask_assembled(question, *, question_date, shape=…) -> dict` returning the assembled block as a structured dict `{subject_objects, shape, block_text, slices:{state,timeline,evidence}, admission:{…}}` — implemented by a pure `tortoise/assembly.py` module (injected projection, house rule) with its own independent subject resolver. The product `ask()` gets a THIN env-gated branch: when armed and the resolver finds a confident subject for an assembly shape, `ask()` delegates the evidence construction to `ask_assembled`'s shared block builder and continues to the SAME single reader call (steps 5–8 unchanged); otherwise byte-identical legacy. The eval lane calls `ask_assembled` DIRECTLY (no ask() dependency), giving a standalone, testable consumer of the block contract before any ask() wiring risk. Block placement inside ask() = replacement for the fired shape (posture knob as in A2).

### Files touched
- New `tortoise/assembly.py` (pure module: `resolve_subjects(proj, question)`, `walk_typed(proj, seeds)`, `render_block(...) -> dict`) — the single implementation of the block contract.
- `tortoise/sdk.py` — `ask_assembled()` method (new public surface; recall_* family precedent: `recall_state` 13327 / `recall_subgraph` 13794 are standalone surfaces with their own shapes); `ask()` thin branch (~post-validation): armed + shape/resolver fire → `block = self._assemble_evidence(...)`; evidence = `render_block` output under existing caps; else legacy untouched.
- `tools/longmem_eval/retrieve.py` — synthetic-graph arm calls `ask_assembled` (or its pure core) and measures per-slice admission from the returned `slices` dict.
- `docs/product/answer-surface.md` — document the new surface + the ask() branch (additive contract note).
- Tests: `tests/test_ask_assembled.py` (synthetic multi-session graph, block contract, admission slices), `tests/test_assembly_pure.py` (pure module unit tests — no SDK), ask() delegation + regression (flag off byte-identical).

### Architecture
1. **Standalone contract first**: `ask_assembled` returns a deterministic dict — no wire-format coupling to the 12-field ask response, no qtype-enum change. The eval consumes this dict; the product branch consumes `block_text`. This makes the assembler independently shippable and testable (the eval doesn't have to wait for ask() wiring).
2. **Independent resolver** (constraint 7): pure module, deterministic recall-first — exact-name/id probe (every-version index-backed) → Object FTS fuzzy (docker) → `search_keys` alias harvest; multi-candidate admit with confidence tags + a `confidence` on each subject; resolution failure → `ask_assembled` returns an explicit `{subjects: [], fired: false}` (honest absence) and the ask() branch falls through to legacy (never an error, never an empty answer).
3. **Walker**: typed ≤2 hops over aboutObject + Event session join (A's verified spine) + supersession chain, hub-damped, IMPL/NAND on why-shapes only; returns `slices` raw (state rows / dated spine rows / evidence point refs) so the SAME walker serves eval admission and the product renderer.
4. **Renderer**: pure function producing `block_text` (state header + chronological dated lines + evidence with EP/provenance quote) + per-slice item counts; product branch enforces the 8k/32 KiB caps over `block_text` (reuse `estimate_tokens_ask`).
5. **ask() delegation**: armed + fired → `evidence` = block (replacement posture) OR block + legacy pool capped together (additive posture); reader steps untouched (single call, same fragments via existing qtype); `question_type` unchanged on the wire (internal shape only — the A3 counterpart to A1's contract-visible router).

### Risks
- **Two surfaces to keep coherent**: `ask()` evidence vs `ask_assembled` block_text could drift unless both consume the SAME pure renderer (the design makes the pure module the single source; ask() must never re-implement rendering). Drift risk is the cost of the standalone surface.
- Product value is gated on the ask() branch, which this option deliberately keeps thin — if the branch is deferred, the assembler exists but the PRODUCT ask lane doesn't use it yet (scope risk vs the "product ask lane first" mandate — mitigate by shipping the branch in the same change, thin as it is).
- A standalone public surface invites contract expectations (response shape, error semantics, metering) that the recall_* family has set precedent for but that ask()'s 12-field surface does not share — document the boundary (ask_assembled is a retrieval/evidence surface, NOT a metered answer surface).
- Same embedded-vs-docker resolver posture as A2 (exact-name everywhere, FTS fuzzy docker-only).

### Tradeoffs
+ Cleanest separation: pure module + standalone surface = the eval and product each consume what they need; the assembler is testable and shippable independent of ask() wiring risk.
+ No qtype-enum or wire-contract change (the opposite pole from A1 on the contract axis); the answer-surface 12-field shape stays pinned.
+ `ask_assembled` returns raw slices + rendered text — admission measurement and human-readable evidence from one call (the eval's reader_evidence@k analog is direct).
− Highest surface count of the three (new public method + module + ask branch); drift discipline required between the two consumers.
− The "ONE dense structured block for the existing single reader" goal is realized through the thin ask() delegation, which is the riskiest few lines (must be provably flag-off byte-identical).

### Best-fit if
The team wants **eval-first iteration on the synthetic graph** with a standalone, contract-stable assembler surface; the product ask() integration is a deliberately thin delegation; and the answer-surface wire contract must stay pinned (no qtype/enum churn). **Differentiator vs B2: read-time, no fold-time snapshot machinery. Differentiator vs B1/A1: assembly is a standalone public surface with its own block contract; ask() delegates rather than hosting the router; question_type stays unchanged.**

### Constraints 1–9 compliance
1 ✅ dated spine sorted at render; ISO dates explicit · 2 ✅ state header + dated evidence in block_text (renderer enforces two layers) · 3 ✅ superseded excluded from default current-state lines, history retained; markers explicit · 4 ✅ typed ≤2-hop walk, hub-damped, shape-gated; no blind BFS; per-slice caps · 5 ✅ 8k/32 KiB caps enforced over block_text (product branch); verbatim evidence w/ EP+quote; reader fragments unchanged (generic/TR do the instruction work) · 6 ✅ compare/ordering shapes fetch BOTH subjects (resolver admits all confident anchors); ordering/diffs precomputed in the renderer; relative→absolute at assembly · 7 ✅ independent recall-first resolver with confidence tags; zero LLM v1; explicit `fired:false` on absence · 8 ✅ eval calls the surface directly and measures per-slice admission from `slices`; matched-control arms testable on the pure module · 9 ✅ no blind BFS, no embedding gate (sparse/exact resolution), no summaries, no LLM consolidation at write.

---

## Cross-option tension map (A set internal + vs B set)

| Axis | A1 (qtype-native) | A2 (synthesized hits) | A3 (standalone surface) | B's nearest |
|---|---|---|---|---|
| Router surface | Extends PUBLIC qtype enum + fragments (contract-visible) | Existing detector (internal shape) + subject-presence | Internal shape + resolver presence; no contract change | B1 = private pre-router; B2 = snapshot presence |
| Block entry | Replaces pool on owned shapes | Hits injected into existing pool (add/replace knob) | Standalone dict; ask() thin delegation | B3 = additive AEP section above pool |
| Walker | New typed walker (aboutObject + session join) | C2-style anchor + typed harvester | Pure typed walker, shared | B3 = `SubgraphExpander` typed mode |
| Subject resolution | exact-name → FTS → aliases | exact-name → FTS (C2 anchor) | exact-name → FTS → aliases (pure) | B1/B3 docker-FTS-leaning |
| Date spine | sorter at render | synthesized session_date keys | renderer sort | B2 = fold-time normalized |
| qtype/wire change | Yes (new values + fragments) | No | No | None of B's change it |
| Embedded/no-FTS | exact-name probe fallback | exact-name probe fallback | exact-name probe fallback | B2 native; B1/B3 need fallback |
| Eval consumption | dataset qtype → same fn | shared synthesizer | direct `ask_assembled` | B1 shared CAP; B3 AEP dict |
| Drift risk surface | prompts + hosted parity | hit schema + dedup keys | two consumers of one module | B2 snapshot vs graph |

All three keep ONE reader call (zero added LLM), respect the 8k/32 KiB answer-surface envelope, degrade to the legacy byte-identical path when off / unrouted / subject-less, and are testable on a synthetic multi-session graph with Object structure (v2-lane shape). The sets bracket the design space on every axis the issue flagged: A1 vs B1 bracket the router surface (public contract vs private pre-router); A2 vs B3 bracket block entry (synthesized pool members vs additive AEP object); A3 vs B2 bracket where the work happens (standalone read surface vs fold-time snapshot).

## Source index (verified on this worktree)
- `tools/longmem_eval/ingest_v2.py` — point aboutObject MERGE 209–218; event loop 246–290 (NO aboutObject wiring — spine must use session/event join); Object creation via `create_entity("object", …)` 118–128; supersessions via `commit_ops.apply_supersessions` 334–377.
- `tortoise/sdk.py` — `ask()` 12606 (retrieval point-only ~12751; qtype post-assembly ~12848); `annotate_ask_hits` 11418 (Event startedAt join → session_date/speaker/session_id); `tortoise_fts_query` 11498 (object decoration ~12121); `_entity_key_expansion_pass` 12390 (+ anchor consts 766–776); `recall_state` 13327 / `recall_subgraph` 13794 (standalone-surface precedent); `create_event` about* wiring 15816+; `create_object` 15807.
- `tortoise/reader.py` — `detect_question_type` 397 (TR→KU→MS→SSP→None); `system_prompt_for` 340; `_TYPE_FRAGMENTS` 271+; `reader_prompt_constants` (drift signal).
- `tortoise/schemas.py` — `ASK_QUESTION_TYPES` 19 (closed enum).
- `tortoise/search_engine.py` — `run_fts_query` 316 (Object FTS = name; index-missing degrade); SearchResult status/superseded_by/supersedes/valid_from 222–279.
- `tortoise/retrieval.py` — `_render_block` 438 (renders ANY hit with date/status/speaker keys); `_validity_marker` 398; `render_context` 551; `assemble_context` 471 (8k + 32 KiB whole-hit caps); `dedup_pool` 287 (per-session cap 3).
- `tortoise/projection/__init__.py` — Object range indexes ~2117 (all versions); FTS gate ≥4.x ~2139–2157 (Object name FTS docker-only).
- `tortoise/commit_ops.py` — `apply_supersessions` 297+; `_RECALL_OBJECT_EXCLUDED_STATUS` 32; Object fold → status/supersededBy/supersededAt.
- `docs/product/answer-surface.md` — 12-field contract, evidence caps, closed qtype enum, W4 additive-key precedent.
- Agent B's diverge doc `2026-09-08-2165-connected-assembly-solutions-diverge-b.md` (shared verified facts; B1/B2/B3).
