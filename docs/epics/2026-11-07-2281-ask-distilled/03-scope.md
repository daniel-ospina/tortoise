---
title: "Scope + decision record — Epic #2281: the ask/answer surface reads ingest-time-distilled knowledge with raw-turn fallback"
type: scope
domain: product
doc_status: committed
created: 2026-09-05
committed: 2026-11-07
issues: "#11461 (this commit), #2281 (parent epic)"
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#2281, #9092, #9095, #9097, #11460, #11461, #2080, #2294, #1987, #2134, #2009"
governingAgreement: "epic #2281; docs/ONTOLOGY.md v3.31; STORAGE-ARCHITECTURE.md; EXTRACTOR-V4-ARCHITECTURE.md"
---

> **Provenance.** The scope-stage research below is the durable copy posted on `#2281` on 2026-09-06
> (comment `5559702884`; owner scope-stage decisions, human gate passed). It is committed by `#11461`
> because the durable copy itself promised it — *"Will be committed as `docs/epics/2281-*` during the
> epic Plan stage"* — and never was: on `main = dbbf5d874` (2026-11-07)
> `git ls-tree -r --name-only dbbf5d874 docs/epics/ | grep -c 2281` returned **0** (`--name-only` is
> load-bearing in that quotation — without it the count is `1`, because an unrelated doc's blob sha
> happens to contain the digits `2281`), so every governing fact about
> this epic lived only in comments and `gh api …/issues/2281/sub_issues` returned `[]`. (The earlier
> 2026-09-05 comment, `5552268035`, is the owner's scope-stage decision list; it points at
> `/tmp/2281-scope.md`, not at this copy.)
>
> `#11461` committed the 2026-09-06 text with mechanical changes only — a rewritten frontmatter;
> `markdownlint` fixes (blank lines, one line re-wrapped, one table cell whose `|` is escaped); and two
> bolding/wording edits (`#2080 W5` → `Workstream **#2080 W5**`, `#2104 indicator (4)` →
> `indicator **(4)** of #2104`) — and appended **§7** and **§8**: the decision-state table re-measured on
> `main = dbbf5d874`, and the workstream → child map.
>
> A later wave of the same lane corrected **§7**'s gate row and **§8**'s blocker list: both had been
> derived from the owner's 2026-11-02 "evals postponed" ruling, which the owner's **2026-11-07**
> finite-gate ruling (posted 1.5 h *before* this file was written) had already superseded. The finding and
> the corrected rows are recorded in place, not silently rewritten.
>
> ⚠️ **§1's `path:line` citations are as-of 2026-09-05 and have drifted since.** Read them as
> historical. §7 re-measures the **decisions**, not these citations; measured on this head,
> `_validity_marker` is `retrieval.py:1289` (not `:398`) and `invalidate_point` is `sdk.py:20126`
> (not `:3458`).

# Scope research — Epic #2281

> READ-ONLY scope-stage research (issue #2281). Repo: `daniel-ospina/tortoise` (shared checkout
> `/Users/danielospina/Documents/GitHub/tortoise` stayed clean — nothing written under it).
> Companion verdict that this research builds on: **#2294** — the READ side of the "current-state
> visibility" hypothesis is ALREADY shipped; the gap is the WRITE side (nothing emits the
> superseding Point at ingest at scale). This document confirms that verdict with code citations
> and decomposes the three #2281 draft workstreams against it.

**Bottom line up front:** Two of the three draft workstreams are *already substantially built* —
(2) ingest-side coverage eval = merged #2080 W2-a/W2-b (#2155/#2183), and (3) Ingest→Search→Evaluate
measurement = the existing `tools/longmem_eval/` harness (already Ingest→retrieve→judge, with a v2
production-extractor lane). The genuine, un-built gap is **workstream 1's read-side ordering** (the
ask lane currently interleaves distilled Points and raw chunks in *pure RRF rank order* with no
"distilled-first" preference) plus the **write-side enabler** (#2134 truncation + #2104 provenance
finalization) that makes the distilled leg trustworthy at scale. Scoping therefore should NOT
re-invent W2-b or the harness — it should (a) add a distilled-first read preference, (b) land #2134,
(c) extend the *existing* measurement surfaces with a distilled-corpus + planted-supersession fixture.

---

## 1. Existing-state inventory (citations)

### 1.1 READ side — the answer surface already renders the full state story (SHIPPED, tested)

| Capability | Where | Status |
|---|---|---|
| Supersede machinery (CORRECTS edge + `outdated:true` + `expiredAt` + edge transfer IMPL/NAND/source/structural) | `tortoise/sdk.py:3458` (`invalidate_point`), `:3535` (`supersede_point`), `:3487` (`supersede` unified) | **SHIPPED** — `tests/test_supersede_edges.py` (40–356: transfer incl. NAND+source, double-supersede, window stamps additive) |
| Terminal statuses excluded from default reads | `tortoise/search_engine.py:23` `TERMINAL_EXCLUDED_STATUSES` (`retracted/superseded/outdated/archived/deprecated`); `_exclude_status_clause` `:28` | **SHIPPED** — `tests/test_supersede_edges.py:289-356` |
| Ask lane *includes* terminal hits **with markers** (`include_terminal=True`) | `tortoise/sdk.py:11471`; decoration `tortoise/search_engine.py:1667-1744` (`superseded_by`/`supersedes`) | **SHIPPED** |
| Marker rendering into reader context (`[SUPERSEDED BY: …]`, `[SUPERSEDES: …]`, `[valid since/to; expired …]`) | `tortoise/retrieval.py:398` `_validity_marker`, `:442` `_render_block`, `Current Date:` header `:558/:579` | **SHIPPED** — `tests/test_e2e9_point_in_time.py` (`test_rendered_context_valid_marker:188`, `test_undated_hit_renders_no_valid_marker:204`) |
| Fail-loud if decoration is missing on terminal hits (`retrieval_degraded=True`) | `tortoise/sdk.py:11658` `_ask_d8_decoration_unavailable`, fired `:11600` | **SHIPPED** — never a silent "current" render |
| NAND contradiction surfacing at the state surface (counter-evidence attached) | `sdk.recall_state` (`tortoise/sdk.py:12050`); `tests/test_recall_state.py` | **SHIPPED** (contested never demoted) |
| Source nodes + `sourcedBy` provenance, source edge survives supersede | ontology §3.3/§4.6; `tests/test_supersede_edges.py:97` (`test_supersede_transfers_source_edge`) | **SHIPPED** |
| Two-phase reader (presence-commit → abstain); abstention honesty | `tortoise/reader.py:214` `_ABSTRACTION_FRAGMENT`, `:442` `_looks_abstained`, `:514` `LLMReader` | **SHIPPED** — `tests/test_ask_sdk.py` |
| **#2280 collapse fix**: empty model output escalates once then fails loud — empty ≠ abstention | `tortoise/sdk.py:181-241` `_ask_reader_complete`; tests `test_collapse_escalates_and_answers:674`, `test_collapse_persists_fails_loud_not_abstention:694` | **SHIPPED** |

**Verdict (mirrors #2294):** there is **no read-side gap**. If knowledge is stored as Points with
supersession, a current-state ask cannot mislead the reader. The `830ce83f` recency miss happened
because the spot-check fixture seeds *raw episodic turns*, which are never superseded (see 1.4).

### 1.2 WRITE side — ingest-time supersession machinery EXISTS in the v2 pipeline (SHIPPED, but truncation-broken at scale)

The "nothing emits the superseding Point at ingest" claim in #2294 is **too strong**: the v2
extractor + Layer-1 commit path *does* emit and execute ingest-time supersession. It is gated behind
the v2 pipeline, which #2134 shows truncates on large multi-session haystacks.

| Capability | Where | Status |
|---|---|---|
| S5 execution emits point-level supersession records (reason `REVISES` — "fact-value contradiction, later session value change") | `tortoise/extractor_v2.py:2848` `execute_embed`; `:3120` (`reason = "REVISES"`); `REVISES_MIN_OVERLAP = 0.6` `:2048` | **SHIPPED** |
| Entity-level supersession derivation (state-centric Object lifecycle) | `tortoise/extractor_v2.py:2549` `_resolve_superseded`, `:2689` `derive_supersessions`, `_supersession_records` `:2604` | **SHIPPED** |
| Layer-1 commit payload carries `supersessions` channel | `tortoise/commit_schema.py:464` `SupersessionRecord`, `:498` `CommitPayload.supersessions` | **SHIPPED** |
| **Execution** of supersession records at write time: point-level → `sdk.supersede()` (CORRECTS+outdated+edge transfer); entity-level → `_fold_object_superseded` (Object.status fold cache) | `tortoise/commit_ops.py:146` `apply_supersessions` (called after entity/point write, `tortoise/sdk.py:2833-2834`) | **SHIPPED** |
| SDK commit entry point threads supersessions | `tortoise/sdk.py:2149` `commit_session` (`:2248-2300`) | **SHIPPED** |
| Value-first state-centric extractor also emits `CHANGED → supersedes` | `tortoise/value_extractor.py:374-382` (`ground` delta: `NEW\|CHANGED\|EXISTS`, `supersedes`), `:657` `extract_session`, `:488` `construct_graph` | **SHIPPED** |
| Legacy conversation miner supersede cues | `tortoise/mining.py:408-433` `_temporal_wire` (`supersede`/`changed our mind` cues → CORRECTS) | **SHIPPED** (legacy path) |
| Eval v2 ingest uses the production extractor *with supersession since #1394* | `tools/longmem_eval/ingest_v2.py` (header + `extract_session_v2` via `tortoise/extractor_v2.py:3531`) | **SHIPPED** |

**The real write-side gap is NOT "no supersession" — it is the #2134 truncation class** (see 1.6):
on large multi-session haystacks the S2/S4 embed list is truncated (`finish_reason == "length"`),
the tail is dropped, and the supersession + facts in the tail never reach the graph.

### 1.3 WRITE side — provenance / EP-on-ingest / REPHRASE dedup / write verb (#2104) — largely MERGED

Workstream **#2080 W5** (issue #2104) is **mostly merged** via the `feat/2080-w5-*` PR wave:

| Phase | Content | PR | Status |
|---|---|---|---|
| A+B | `memory_write_v1` write verb + provenance REQUIRED (`PROVENANCE_MISSING`) + verified gate order | #2247 | **MERGED** |
| C | EP-on-ingest (live-at-capture + bounded ingest EP pass) | #2256; `tortoise/sdk.py:878` `_apply_capture_ingest_ep`, `:2603`; `tortoise/hosted_api.py:6529` | **MERGED** |
| E | ingestion toggle contract S11 + disclosure marker `surfaced` | #2283 | **MERGED** |
| F | deterministic `sessionCaptured` Event id + SDK mirror replay parity | #2293 | **DRAFT (in flight)** |
| D | (phase-D: id-ingest-calibrate list, `tortoise/sdk.py:847`) | worktree `feat/2080-w5-phase-d` | **unmerged** |

REPHRASE-linked dedup (a paraphrase links via REPHRASE and counts as survival, not a new point) is
indicator **(4)** of #2104 — the consolidation classification already distinguishes `identical` vs
`paraphrase` in `classify_consolidation` (the `NOOP` decision folds both, `tortoise/extractor_v2.py:3120-3150`).

### 1.4 WRITE side — the ask measurement fixtures seed RAW TURNS, not distilled points (the confound)

`tools/ask_spotcheck.py` header (`:6-52`): the product-lane spot-check seeds **"one Point per haystack
turn + an Event per session"** — i.e. raw episodic turn Points (`pointKind event`), *not* the v2
distilled `statement` Points. Same for the deterministic leg `tools/longmem_eval/ingest.py` (`:1-40`:
per-turn episodic Points + `session-transcript` raw chunks). This is precisely why `830ce83f`
(knowledge-update) and `0a995998` (multi-session) miss: **the fixture has no supersession chain and
no typed/dated/entity-linked Points for the reader to see.** The #2281 measurement leg must replace
this fixture (or add a parallel distilled-corpus fixture) to measure the write path.

### 1.5 Harness — Ingest → Search → Evaluate (mem0 shape) ALREADY EXISTS

`tools/longmem_eval/` is a full Ingest→retrieve→judge pipeline:

- **Ingest:** deterministic leg (`ingest.py`) and v2 production extractor leg (`ingest_v2.py`, the
  production `extract_session_v2` with supersession).
- **Search:** hybrid retrieval (FTS + vector + structural) with RRF, evidence marks (`has_answer`,
  `answer_session_ids`), recall@k (`evidence.py`, `retrieve.py`).
- **Evaluate:** official gpt-4o answer-check judge (`judge.py`); retrieval recall@k and read-path
  accuracy both reported (`report.py`).
- **Protocol:** 9-step resumable `run_protocol.py` (step 5 = 500-Q baseline; step 8 = 1k benchmark;
  step 9 = R6/E6 follow-up); `w7_publish.py` publishes official `recall_all@5` with sealed keys + erratum.

**→ The "extend the harness to mem0 shape" workstream (3) is already true for the benchmark lane.**
What is *missing* is the **ask-surface** (product `sdk.ask`) measurement over the distilled corpus —
`ask_spotcheck.py` currently measures the raw-corpus lane only (see 1.4).

### 1.6 The #2134 truncation class (OPEN — the binding write-side blocker)

`tortoise/extractor_v2.py:1016` `_complete` retry ladder, `:1036-1084`: `finish_reason == "length"`
records `truncated_parse_error`/`partial_parse` and **skips the retry** (same-prompt retry would
truncate again), but the **partial embed list is still used** — the tail is dropped, degrading the
retrieval pool and making the reader abstain when gold *is* in the haystack. Reproduced on
`0100672e` (42+ sessions): 13/139 S2/S4 calls truncated. **This is the same gap as #2281's "if ingest
loses or truncates knowledge, no retrieval fixes it."** Issue #2134 OPEN (no PR); its sibling #2136
(empty-response None-guard crash half) has PR `fix/2134-reader-none-guard` OPEN (not yet merged).

---

## 2. Ontology alignment (§ refs)

Every #2281 workstream is expressible in canonical `docs/ONTOLOGY.md` v3.9 terms. Nothing fights the
ontology *if* expressed correctly; the one anti-pattern to avoid is treating raw sessions as
first-class state.

| Workstream | Ontology expression | § refs | Fights ontology? |
|---|---|---|---|
| **WS1 — ask reads distilled knowledge** | The reader should prefer extracted **`statement` Points** (the LOGIC layer, `pointKind: statement` — the only extraction point kind) that carry `validFrom/validTo/expiredAt` (§4.1) and `aboutObject` entity links (§3.2), over **raw `session-transcript` chunks** and **episodic turn Points** (`pointKind event` → legacy/`turn` Event, §5). "Latest wins" is expressed as the `CORRECTS` edge + `outdated:true` + `validTo`/`expiredAt` on the superseded Point (§3.1, §4.1) — already rendered to the reader as `[SUPERSEDED BY: …]`/`[valid … expired …]` (`retrieval.py:398`). | §2 (state-centric model), §3.1, §3.2, §4.1, §5 | **No** — but see anti-pattern below |
| **WS2 — write-path coverage eval** | Measures salient-unit **survival into the graph** (do the planted facts become live `statement` Points with provenance `extractedFrom → Source → references → Event`, §3.3/§3.4) and **supersession survival** (does a later planted value produce a `CORRECTS` supersession, §3.1). | §3.3, §3.4, §4.6 | **No** |
| **WS3 — ask measurement over real pipeline** | The benchmark must measure the reader answering from the **distilled graph** (statement Points + state Objects + validity windows), not from raw-turn fixture points. "0% superseded points appear as live answers" is the product-success-eval §7 staleness gate. | §2, §5, §11 | **No** |

**Anti-pattern to flag (the #2281 draft's phrasing "raw turns as the verbatim fallback" is fine, but
"raw sessions" must not become first-class state):** raw session turns are **episodic** — `:Session`
nodes + `pointKind event`/`turn` records, `documentKind transcript` Documents (§4.4/§4.5), quota-
exempt via `is_episodic` (§4.1/§4.5/§4.6). They are the verbatim evidence fallback, **never**
lifecycle-managed Points. The correct expression of the draft's "raw-turn fallback" is: the raw
chunk/turn leg is retained **as retrieval evidence** (it already is — `retrieval.py:280-284`
`is_raw_chunk`, `session-transcript` pointKind), while *current state* lives exclusively in
`statement` Points + `CORRECTS`/`validFrom`. Do **not** mint a new pointKind or status for "raw" —
`challenged` is already the canonical example of a *derived* condition that is NOT a stored status
(§5), and the same discipline applies here: "raw" is a retrieval provenance, not a state.

**Correct expression of the draft's "falls back to raw turns for verbatim questions":** a verbatim
question is answered from the raw chunk leg *when no typed/dated/entity-linked statement Point
matches* — this is a **retrieval-ordering** decision, not an ontology change. The ontology already
distinguishes the two legs (`is_raw_chunk` at `retrieval.py:280`).

---

## 3. Product-vision alignment

Direction (from `docs/product-success-eval.md`, `docs/00_index.md` → `docs/product/answer-surface.md`,
and the #1987 decision records): the hosted product sells **search + evidence**; the READING is
**BYOK** (customer's own model/tokens — no hosted LLM arbitrage); the ask lane must stay **honest**
(#2280: empty output never an abstention; #2294: no silent current-state rendering). The direction is
**distilled, dated, ontology-organized memory a reader can answer from, with raw turns as the verbatim
fallback.**

| Workstream | Serves the direction how | Must NOT do |
|---|---|---|
| **WS1** | Distilled-first read ordering means the customer's BYOK reader answers from compact, dated, entity-linked `statement` Points + state story, not from fishing a long raw haystack — this is exactly the "organized so a reader can answer" claim. The raw-turn fallback preserves verbatim fidelity where a paraphrase would be wrong. | Must NOT re-introduce a hosted-LLM dependency (ordering is deterministic ranking + rendering, no new model call); must NOT break the #2280 "empty ≠ abstention" honesty or the D8 fail-loud decoration (`retrieval_degraded`). Must NOT add a new recall *surface* — #2080 W4's constraint: enrich the existing ask lane in place. |
| **WS2** | Write-path eval is the *evidence* the memory is organized (salient-unit + supersession survival). Gating the ask surface on it is the product-success-eval §1 "proves the mechanism" discipline. | Must NOT re-litigate #2080 W2 — it's merged; extend it. Must NOT set a target before the first run (benchmark-first, per the 2026-09-01 #2080 decision). |
| **WS3** | Ingest→Search→Evaluate over the *real* pipeline measures what customers get, not an idealized raw corpus — the product-success-eval delta principle (measure the treatment, not the plumbing). | Must NOT publish benchmark numbers as product-accuracy claims without methodology (comparison-systems.md discipline, #2080 W7). |

**What scoping must move the product toward, concretely:** a customer asking "where do we live now?"
gets the **current** `statement` Point (entity-linked, `validFrom`-stamped, its superseded predecessor
marked) plus raw turns *only when verbatim evidence is needed* — never a raw-turn-first answer that
commits a stale value because the fixture happened to be raw sessions.

---

## 4. Gap analysis vs the 3 draft workstreams

### WS1 — "Ask lane reads distilled knowledge with raw-turn fallback" → **CONFIRM + REFINE (the real gap is ordering + the write enabler)**

**What exists:** the ask lane already retrieves *both* distilled `statement` Points and raw chunks, in
**pure RRF rank order with no distilled-first preference** (`tortoise/retrieval.py:479-484`
`assemble_context`: "points and raw chunks in true RRF rank order"; `is_raw_chunk` `:280`). The
distilled points, when present, are already decorated with `[SUPERSEDED BY/SUPERSEDES/valid …]`
markers (`retrieval.py:398`). So the reader *can* see current state — it just isn't *preferred* over
raw chunks, and (in the spot-check fixture) the distilled points don't exist at all (raw-turn seeding).

**What's missing:**

1. **A distilled-first ordering signal** — no code today boosts `statement` Points (typed/dated/entity-linked/validFrom-bearing) over raw `session-transcript` chunks / episodic turn Points in the ask pool. (There *is* a raw-chunk cap `dedup_pool` `retrieval.py:288-318` and evidence-mark boost `apply_evidence_boost`, but neither expresses "prefer current-state distilled knowledge".)
2. **The ingest enabler** — the distilled leg is only as good as the v2 extractor, which #2134 shows truncates on large multi-session haystacks. WS1's read-side ordering is unmeasurable-against-real-distilled-graphs until #2134 lands.

**Dependencies / sequencing:** WS1 read-side ordering code is **ready now** (pure retrieval/assembly
change, no new surface); its *measurement* over a distilled corpus is **blocked on #2134**. Sequences
**after** #2293 (W5 Phase F) and alongside #2134; inherits #2080 W4's "no new surface" constraint.
**#2009 (detector) is NOT a blocker** — WS1 must NOT route on `detect_question_type` (0.284 agreement,
OPEN); route on retrieval composition instead (see Decision 6).

### WS2 — "Ingest-side coverage eval (write path)" → **LARGELY EXISTS (MERGED); REFINE to add supersession survival + truncation guard**

**What exists:** #2080 W2-a planted-gold corpus + sealed gold (**#2155**, MERGED); W2-b benchmark
runner + blind judges + CI-gated baseline + first published number (**#2183**, MERGED). This is
already the gbrain Cat-35 port: salient-unit survival, junk leakage, provenance, quote fidelity.

**What's missing (the #2281 delta):**

1. **Supersession survival as a first-class W2 metric** — planted *value-change* golds (same entity,
   later session asserts a new value) must assert the OLD point becomes `outdated`/`superseded` with a
   `CORRECTS` edge + `validTo`/`expiredAt`, and the NEW point survives. This is the exact `830ce83f`
   class, and it is the write-side analogue of product-success-eval §7's "0% superseded points appear
   as live answers". The existing W2-b measures *survival*, not *supersession correctness*.
2. **The #2134 truncation guard** — a CI assertion that the multi-session-haystack ingest produces
   zero `partial_parse`/`truncated_parse_error` with `valid=true` (the #2134 target verbatim).

**Dependencies:** W2-b (merged) + #2134 (the fix this workstream gates on). **Ready now** to add the
supersession fixture/assertion to W2-b; the truncation guard is **blocked on the #2134 fix**.
**Sequencing:** WS2 does not depend on WS1; it *gates* WS1's measurement leg (you can't trust the
distilled-corpus ask measurement until write-path survival/supersession is CI-green).

### WS3 — "Ask measurement over the real pipeline (Ingest→Search→Evaluate)" → **REFINE: the harness exists; the ask-surface leg is the delta**

**What exists:** `tools/longmem_eval/` already runs Ingest→Search→Evaluate for the *benchmark* lane,
including a v2 production-extractor ingest (`ingest_v2.py`) with supersession since #1394.
`w7_publish.py` and `run_protocol.py` cover the benchmark discipline. `tools/ask_spotcheck.py` runs the *product* `sdk.ask`
lane but seeds **raw turns** (see 1.4).

**What's missing:** an **ask-surface measurement over a distilled corpus**:

1. A `--ingest-mode v2` (or new distilled-fixture) mode in `ask_spotcheck.py` that seeds the v2
   extractor's output (typed/dated/entity-linked `statement` Points + state Objects + `CORRECTS`
   supersession chains), then runs the product `sdk.ask` and asserts the reader commits **current**
   state on the planted knowledge-update/multi-session questions (the `830ce83f`/`0a995998` classes).
2. A planted-supersession-chain fixture asserting "reader picks current state" (the #2294 "optional
   next" item — it becomes #2281's measurement leg).

**Dependencies:** #2134 (distilled corpus must not be truncated) + #2069 (strong reader model — already
CLOSED, qwen3.8-max routing + M5 pin landed). **Ready now** to *build* the fixture/harness; **executable
end-to-end** only after #2134.

### MECE check (the three workstreams are clean, with two adjustments)

- WS1 (read-side ordering) and WS2 (write-side eval) are **orthogonal** (read vs write) — no overlap.
- WS3 (measurement) is the **integration of 1+2** — it overlaps WS1's measurement and WS2's corpus by
  design. **Adjustment:** WS3 should be scoped as the *measurement leg* that WS1 and WS2 both feed, not
  a third independent build.
- **Net decomposition for planning:** (A) WS1 read-side ordering [ready]; (B) #2134 truncation fix
  [blocker, own issue already]; (C) WS2 supersession-survival extension to W2-b [ready]; (D) WS3
  distilled-corpus + planted-supersession ask fixture [build ready, run gated on B].

### Blocked vs ready-now summary

- **Ready now:** (A) distilled-first ordering; (C) W2-b supersession extension; (D) harness/fixture build.
- **Blocked:** end-to-end measurement of WS1 over real distilled graphs → **#2134**; and (D) execution → #2134.
- **Sequence against:** #2293 (W5 Phase F, DRAFT) — land before/with; #2104 Phase D (unmerged); #2136 (crash half — merge its PR).

---

## 5. Measurement approach per workstream

Ingest→Search→Evaluate (mem0 shape) maps cleanly onto the existing harness; the write path is already
planted-gold (gbrain Cat-35) via W2-a/W2-b.

### WS1 (read-side ordering)

- **Shape:** product-lane `sdk.ask` spot-check (`tools/ask_spotcheck.py`) + `tests/test_ask_sdk.py`
  (deterministic fakes pin the ordering). Assert the assembled context prefers `statement` Points over
  raw chunks for current-state questions; raw chunks still reach the context for verbatim questions.
- **Reuse:** `ask_spotcheck.py`'s full-semantic judge (`_grade`, #2071) and the ctx gold-session recall
  readout (already separates retrieval from reading).
- **Key metric:** on the planted knowledge-update class (`830ce83f`), the reader commits the **latest**
  value (the superseding `statement` Point) ≥ threshold; on verbatim questions, the raw-chunk leg still
  provides the answer (no regression to the raw-corpus spot-check aggregate 19/21 = 0.90).

### WS2 (write-path coverage eval)

- **Shape:** extend `tools/longmem_eval/`-adjacent W2-b benchmark (`feat/2080-w2b-benchmark-runner`,
  merged #2183) with a **supersession-survival** metric + a **truncation census gate**.
- **Reuse:** W2-a planted-gold corpus + sealed gold (#2155); W2-b blind judges + CI-gated baseline;
  the #2134 census fields (`empty_embed_list`/`partial_parse`/`truncated_parse_error`) already flow
  through the report.
- **Key metrics:** salient-unit survival (existing); **supersession correctness** (new: planted
  value-change → old point `outdated`/`superseded` + `CORRECTS` + `validTo`/`expiredAt`); zero
  truncation on the recorded-failure multi-session set (the #2134 target).
- **gbrain Cat-35 style:** the W2-a/W2-b fixture already carries planted salient units + verbatim
  anchors + distractors + attribution hazards; add the *value-change gold* (same unit asserted twice
  with a later value) to exercise the supersession reflex.

### WS3 (ask over the real pipeline)

- **Shape:** a new `ask_spotcheck.py` **distilled-corpus mode** (or a sibling script) that seeds the v2
  extractor output + a planted supersession chain, runs the product `sdk.ask`, grades with the official
  judge. This is the #2294 "optional next" made concrete.
- **Reuse:** `tools/longmem_eval/ingest_v2.py` (production extractor + supersession) for the ingest leg;
  `ask_spotcheck.py`'s judge + composition fixture (extend `tests/fixtures/ask_spotcheck_composition.json`
  with planted current-state questions); `w7_publish.py` discipline if any number is published.
- **Key metrics:** reader picks current state on planted supersession chains (target = the #2294
  hypothesis, e.g. ≥ threshold correct-current-state); retrieval recall@k over the distilled corpus
  (`has_answer`/`answer_session_ids` marks already in `evidence.py`); read-path accuracy vs the
  raw-corpus spot-check baseline.

**What the harness already provides (so scoping does NOT build them):** dataset caching
(`dataset.py`), per-question fresh-graph isolation (`ingest.py`), evidence marks + recall@k
(`evidence.py`), official gpt-4o judge (`judge.py`), resumable 9-step protocol (`run_protocol.py`),
sealed-key publication + erratum (`w7_publish.py`), CI smoke (`--mock` + `longmemeval_mini.json`).

---

## 6. Open decisions for the human gate

**D1 — WS1 delivery shape: how to make the ask lane "distilled-first"?**

- (a) Additive **ranking boost** for `statement` Points (typed/dated/entity-linked/validFrom-bearing) over raw chunks in the ask pool.
- (b) Additive **"current state" header block**: render the latest `statement` Point per matched entity above the raw context (no ranking change).
- (c) **Route by question_type** (verbatim → raw-first, else distilled-first) — *blocked by #2009* (0.284 detector agreement).
- **Recommendation:** (b) now (least risk, no retrieval regression, directly expresses "latest wins" via `CORRECTS`/`validFrom`), with (a) as a measured follow-up. Avoid (c) until #2009 is real.

**D2 — Raw-turn fallback: one pass or two-pass (abstain-then-fallback)?**

- (a) **Single pass** with both legs in context (current), plus the D1 ordering.
- (b) **Two-pass**: answer from distilled only; on abstain, re-run with raw turns appended.
- **Recommendation:** (a). Two-pass doubles reader cost and risks violating the #2280 invariant (empty output is a *fail-loud* escalation, not an abstention signal to re-query). The raw leg stays in-context as the verbatim fallback.

**D3 — Ontologically, what does "distilled" prefer over what?**

- (a) `statement` Points (entity-linked, dated, validFrom-stamped) **> episodic turn Points > raw `session-transcript` chunks**.
- (b) `statement` Points **> raw chunks only** (turn Points treated as raw).
- **Recommendation:** (a) — matches `retrieval.py:280-284` (`is_raw_chunk` already separates `session-transcript` from "extracted statements, episodic turn points"); keeps the episodic layer as the mid-tier verbatim evidence, not a fallback we lose.

**D4 — WS2 supersession eval: extend W2-b or build a parallel benchmark?**

- (a) **Extend W2-b** with a supersession-survival metric + value-change golds.
- (b) Build a separate planted-supersession benchmark.
- **Recommendation:** (a) — W2-a/W2-b are merged and CI-gated (#2155/#2183); a parallel benchmark violates "inventory first" and forks the sealed-gold discipline.

**D5 — #2134 truncation: hard blocker for WS1/WS3, or parallel?**

- (a) **Hard blocker** — land #2134 before measuring WS1/WS3 over a distilled corpus.
- (b) Parallel — build WS1 read-side code + fixtures now, run the *raw-turn* fixture; flip to distilled corpus when #2134 lands.
- **Recommendation:** (b) for the *code* (ordering is a pure read-side change), (a) for the *measurement* (a truncated distilled corpus invalidates the WS3 number). I.e., code parallel, measurement gated.

**D6 — Routing for verbatim-vs-current-state: depend on `detect_question_type`?**

- (a) Use `detect_question_type` (OPEN #2009, 0.284 agreement).
- (b) Route on **retrieval composition** (are there dated/entity-linked `statement` Points matching the question?) — no detector dependency.
- **Recommendation:** (b). #2009 stays parked; a question that resolves to a current-state Point gets the distilled story, everything else keeps the raw leg. Deterministic, testable, no detector.

**D7 — WS3 harness placement: extend `ask_spotcheck.py` or add a `run_protocol` step?**

- (a) Extend `tools/ask_spotcheck.py` with a `--ingest-mode v2` / distilled-corpus mode + planted-supersession fixture.
- (b) Add a step to `tools/longmem_eval/run_protocol.py` (the 500-Q benchmark lane).
- **Recommendation:** (a) for the *product* ask-lane measurement (smallest, already the product-lane harness); (b) only if/when a *published* ask-over-distilled benchmark number is wanted (then it inherits `w7_publish.py` sealed-key discipline).

**D8 — What number gates the ask surface on ingest quality?**

- (a) W2-b salient-unit survival baseline (existing CI gate) + **supersession correctness ≥ X** (new, set from first run).
- (b) Reuse the product-success-eval §7 "**0% superseded points appear as live answers**" as the hard gate, plus a planted-supersession "reader picks current state" pass ≥ threshold.
- **Recommendation:** (b) as the *product* gate (it's the honest customer-facing invariant already in the launch spec), with (a)'s survival/supersession numbers as the *benchmark* gate feeding it. Do not pre-set X before the first W2-b supersession run (benchmark-first, per the 2026-09-01 #2080 decision).

---

## 7. Decision state — D1–D8 re-measured on `main = dbbf5d874` (2026-11-07)

Every row's evidence is a tracked path (a `path:line` where a line identifies the construct), a quoted
command's output, or an issue — no row cites a comment. ✔ = verified this
wave by reading the tree, not inherited from a prior comment.

| # | decision (2026-09-05) | state | evidence |
|---|---|---|---|
| **D1** | Option B — additive dated current-state block above raw evidence, no global ranking change | **LANDED** ✔ | `tortoise/assembly.py` renders `STATE (couch): superseded by sofa` — the f-string at `:2985` in `_state_header_hit()` (def at `:2956`; it sat at `:2767` on this doc's measurement base and #11451 moved it, so cite the symbol, not the line); pinned by `tests/test_assembly_sdk.py::test_fired_routing_exact_goldens` |
| **D2** | single pass (one LLM call per ask; empty output escalates and fails loud, #2280) | **CLOSED BY CONTRACT** | product contract — the abstention label cannot gate a second pass: `tortoise/ask_lane.py:714` computes `abstained` via `_looks_abstained` (`tortoise/reader.py:759`), published as `"abstained"` at `ask_lane.py:824`, and that helper's own docstring says *"measurement/UX sugar — NEVER a gate; the two-phase prompt is authoritative"* (`reader.py:760-762`). A second pass therefore has no gate to hook, and #2280's rule is that empty output escalates and fails loud instead |
| **D3** | 3-tier read ordering: statement > turn Point > raw chunk | **LANDED** (#9092) ✔ | `tortoise/retrieval.py:3311-3313` (`_PACKAGE_TYPE_STATEMENT/_TURN/_CHUNK = 0/1/2`), `_package_type_tier()` `:3316-3336`, `_package_tier()` `:3339`, which returns `(_package_type_tier(h), marked, _pkg_rank)` at `:3353` so hit type is the primary key. The 2026-10-19 comment's `:1948` citation has drifted; the behaviour has not |
| **D4** | extend W2-b (sealed-gold discipline); no parallel benchmark | **SATISFIED BY SCOPE** | the W2-b sealed-gold discipline is `tests/eval/write_path/` — `gold/` (per-fixture `.gold.json`), `baselines/` (`main.json`, `m2.json`) and `_manifest.json`, 73 tracked files; no parallel benchmark was built |
| **D5** | code parallel, measurement gated on #2134 | **SATISFIED** ✔ | #2134 **CLOSED** 2026-09-06 — the measurement gate is clear |
| **D6** | route on retrieval composition; #2009 detector stays parked (0.284) | **HONOURED** | the D3 ordering keys on **hit type**, never on the detector (`_package_type_tier` takes only the hit, `retrieval.py:3316`); the lane still picks its reader prompt by `detect_question_type` (`ask_lane.py:685`/`:934`, `reader.py:532`), which D6 did not forbid. **#2009 is now CLOSED 2026-09-28** — the 2026-09-05 record above calls it OPEN; that is historical text, not a claim about today |
| **D7** | build the distilled-corpus ask measurement, then promote to the sealed benchmark | **LANDED** (#9095) ✔ | `tools/longmem_eval/measure_ask_distilled.py` + `tests/eval/ask/` (pre-registration, sealed gold, manifest, fixtures) |
| **D8** | product gate = launch spec §7 "0% superseded points appear as live answers" + planted-supersession current-state pass; benchmark-side bar from the first published run | **PARTLY UNMET** | the benchmark half is *built and sealed* (`tests/eval/ask/preregistration.json` regression clauses) but **no publication exists** — see §8 |
| **Q3 exit bar** | a **published**, sealed-benchmark measurement of the ask lane over a distilled + planted-supersession corpus | **UNMET** ✔ | `git ls-tree -r origin/main docs/benchmarks/` → 4 entries, **no `ask-distilled/`** |

### The two decision records that are *not* in D1–D8, and outrank it

- **D1-JUDGE, 2026-11-02 (owner):** the eval judge is **DeepSeek**; re-registering the pre-registration's
  judge line is **authorised**; the receipt records `unpinned:<deepseek-model>` so no number claims
  official-judge comparability. Carries its `OVERRIDES:` line on `#2281` and on `#11460`.
- **Eval gate, 2026-11-07 (owner) — this SUPERSEDES the 2026-11-02 "evals postponed" form.** Owner,
  verbatim: *"if we have other p1,p0,p2 that we know affect the eval, then let's first fix those. And
  then we run the eval. what i dont want is to waste an eval while we know what's broken"* — relayed with
  its measured affected set on **#8486** (`comment 6710955512`, **2026-11-07T17:54Z**, i.e. *before* this
  document was written; the local reading for this epic's measurement is #9097's latest comment). The
  gate is therefore **finite and named**, not a queue depth: the run waits on the closed set of items a
  recorded mechanism ties to a quantity this benchmark scores — **eight**, all still OPEN on
  `dbbf5d874`: `#10360` (P1, verbatim state-value retention **49.4 % vs a 54.4 % baseline**), `#11070`
  (P1, pre-`#10852` `:Source` prose still on searchable text), `#10538` (P1, time-aware query expansion,
  the 53 %-of-misses partial-evidence class), `#2684` (P2, verbatim value-spans), `#2683` (P2,
  dedup/cap/order the reader window), `#2518` (P2, entity/fact-augmented keys), `#2516` (P2,
  per-category `evidence_recall@5` + official `recall_all@5` gates), `#8775` (P2, `_RE_RECENCY`'s bare
  `latest` guard rejects a genuine interval question).
  **The eval issues are outside the gate** — a measurement is not a defect that affects the measurement —
  which is what removes the circularity the 2026-11-02 form had. Counting open `P0`/`P1` is **not** the
  gate any more. This row previously did exactly that and concluded "gate still closed"; that reading was
  superseded by the ruling above and is recorded here as the correction, not quietly replaced. The
  ruling's own measured state at its time of writing: **6** `P0-blocker`, **9** `P1-severe`, **1** `P1`.

Per `docs/ONTOLOGY.md` (v3.31) and the precedence recorded on `#2281`: the ontology outranks
`#5013`/D10, which outranks `STORAGE-ARCHITECTURE.md` + `EXTRACTOR-V4-ARCHITECTURE.md`, which outrank
this document. D3 is a retrieval-ordering key (`retrieval.py:3339`/`:3353`) — it mints no `pointKind` and no
status, so it does not touch §4.4/§4.5.

## 8. Decomposition — workstream → child, and the exit bar as a predicate

Children are **real GitHub sub-issues** of `#2281` (`gh api …/issues/2281/sub_issues` returns the five:
`9092 closed · 9095 closed · 9097 open · 11460 open · 11461 open`; the counts come from the issue
object — `gh api …/issues/2281 --jq .sub_issues_summary` → `total 5, completed 2`).

| epic workstream | home | state |
|---|---|---|
| **WS1** — ask lane reads distilled knowledge with raw-turn fallback | **#9092** (D3 ordering, CLOSED) + **#9095** (D7 instrument, CLOSED); the fallback is the tier-2 leg of `_package_type_tier()` | built; **measured only by the exit bar** |
| **WS2** — ingest-side coverage eval (write path) | **#2080 W2** (epic, P2-moderate) — *not* a child of this epic | #2134 truncation clause **SATISFIED** (#2134 CLOSED 2026-09-06); the 2026-09-07 seam memo (`docs/epics/2026-09-06-2335-seam-inventory-geometry.md`) is a WS2 input |
| **WS3** — ask measurement over the real pipeline | **#9095** (instrument) → **#11460** (judge re-registration) → **#9097** (seal + publish) | instrument built; publication blocked |
| **epic's own record** | **#11461** (this document) | committed |

**Dependency edges:** `#9092 → #9095 → #9097` (closed, closed, open) · `#11460 → #9097` (the record must
name the judge the run uses, or the run is `inconclusive: config_mismatch` by the protocol's own rule) ·
`#11461` independent. No two children edit the same file family.

**The exit bar, as a testable predicate** (`tests/eval/ask/preregistration.json` → `decision_rule`):
the corpus manifest verifies **and** the run's resolved reader/judge spec, pin, rubric hash, seed flags,
arms and gate arm match the pre-registration **and** every case ran in both arms **and** neither
regression clause fired (`D8`: no superseded claim rendered live, no planted claim unobserved; verbatim:
no case committed on the raw arm and not the distilled one). The knowledge-update rate is **reported,
not thresholded** — the benchmark-side bar comes from the first published run.

The pre-registration does **not** yet carry the per-category rows (`evidence_recall@5`, type-stratified),
nor the **D14/#5085 two-number split** (`tests/eval/write_path/runner.py:15-24`) stated explicitly
(mechanical checks authoritative; the judge block never enters `metrics`). Both clauses are `#11460`'s to
write into `tests/eval/ask/preregistration.json` — Indicators 5–6, with `Targets` extended to permit the
`decision_rule`/`scoring` move; the gate **mechanism** those rows mirror stays `#2516`'s, which `#11460`
declares as an edge rather than re-implementing. A headline-only bar cannot show which class moved.

**What blocks it, after the 2026-11-07 ruling** — the gate is no longer the queue, so the blocker is a
short sequence, and only the first of them is this epic's own **pre-publication** work — the run itself is
`#9097`, the epic's publication child:

1. **#11460, the pre-registration.** It pins `openai:gpt-4o-2024-08-06` /
   `ask-exit-anscheck-official-v1` while the owner ruled the judge is DeepSeek (D1-JUDGE), and
   `measure_ask_distilled.py:1694-1700` compares both byte-exact — so a DeepSeek-graded run returns
   `inconclusive: config_mismatch` however green it is.
2. **The eight named fixes above, `#2516` first** — the relay's recommended order, because the reporting
   surface is what lets a per-category effect register at all.
3. **Then** the run, under the unchanged `HARD_STOP_USD = 10.00` (`tools/longmem_eval/spend_guard.py:63`).

**Why this document exists at all** (the #9701 class, re-measured here): a decomposition recorded only as
prose in comments is invisible to every selector that reads the API. `gh api …/issues/2281/sub_issues`
returned `[]` while the epic had three children, and a **2026-10-23** comment said so in its own words —
*"the sub-issue API returns 0 because the children are tracked in prose"* (`6359826172`; it cites the
2026-10-19 decomposition comment `6257642547` for the children themselves, which does not mention the
sub-issue API at all). The 2026-11-07 wave that produced
this file also attached all five children as real sub-issues.
