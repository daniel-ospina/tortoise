# Plan — #5038: sources are versioned and extraction is version-scoped — the `extractedFrom` anchor

<!--
Research path: docs/research/2026-09-24-source-versioning/research-brief.md (§2.1, §3, §4③, §4⑤, §5).
Scoping source: issue #5038 + its 4 comments (owner Policy B, precedence block, wording correction).
Status: SCOPING PLAN — the CREATE-path anchor is self-contained and shippable (child issue filed);
the SUPERSEDE/transfer semantics is deferred to owner decision O1. See §9.
SHIPPED (2026-09-25): **Task 1** is delivered as **#5256 → PR #5288** (stacked on #5207). One
correction the implementation forced, recorded here so §3/§4 are not read literally: the journaled
carrier must be keyed by the **RAW `extractedFrom` ref**, NOT by `resolve_source_key`'s live-time
resolution — keying it by a resolution LOST the anchor across `rebuild_all` for a URL-variant ref (the
two lanes can resolve the same ref to different node urls when an unjournalled stub precedes the
`:Source` record) and `check_consistency` could not see it. The change's own plan doc
(`docs/plans/2026-09-25-5256-extractedfrom-source-version.md` §10) is the authority for it.
Complexity: complex (Tier: Complex). See §7.
-->

**Issue:** #5038 · **Repo:** `daniel-ospina/tortoise` · **Branch:** `docs/5038-scoping` · **Base:** `origin/main @ acbe80f85`
**Epic:** #5088 (the D10 fold) · **Related:** #5093 (lane L1, the same files), #5024/#5048/#5089, #5199/PR #5207, #5213, #5214, #5026, #5025, #3644, #4282, #3998

---

## 1. Confirmed problem

**The version a Point was read from has no durable, replayable, honest home.**

The requirement is already canonical — `docs/ONTOLOGY.md` §4.6 (landed, PR #5022): *"Every **Point** derived from a source records **`sourceVersion`** — the `contentHash` of the version it was read from — on its `extractedFrom` link."* The code does not write it: `git grep -n sourceVersion -- '*.py'` on `origin/main @ acbe80f85` → **0 matches** (the #5199 branch anchors `references`, not `extractedFrom`).

### C1 — the edge is a replay-derived projection, re-created bare
`extractedFrom` ∈ `DERIVABLE_STRUCTURAL_RELS` (`tortoise/projection/edges.py:31-34`), ∈ `STRUCTURAL_REL_LABELS` (`edges.py:39-46`), ∈ `SUPERSEDE_STRUCTURAL_RELS` (`tortoise/sdk.py:153-156`). `rebuild_all` wipes the graph (`projection/__init__.py:3996`). Three sites create/transfer the edge, all bare (`MERGE …`, no `SET`):

| # | site | evidence | judgment |
|---|---|---|---|
| 1 | `_link_source` — the single CREATE writer (live + pass-2 resurrection) | `edges.py:431-435`; called from `entities.py:914-919` | **DEFECT.** The version IS knowable at create (the Source's `contentHash`) and is silently dropped on rebuild. This is the `derived = replay(journal)` violation. |
| 2 | **live** `supersede_point` transfer | `sdk.py:6364-6367` then `DELETE r` `6369-6372` | **O1-dependent, not necessarily a defect.** A superseded successor is normally *not re-read*; under §4.6's "the version **it was read from**", absent may be the honest value. |
| 3 | **pass-2b** transfer replay | `projection/__init__.py:5602-5605` (comment `5599`); delete-leg `5565-5568` | **O1-dependent** (mirror of site 2). |

⚠️ An earlier draft of this plan called sites 2–3 defects unconditionally. That overstates: whether a transfer should carry a version is exactly O1 (§8), and under the ontology's own words it may be correct to carry none.

### C2 — the version is not available at link time on the dominant capture path
Stubs mint `contentHash=''` (`edges.py:118-124`); connector Sources `''` (`entities.py:2222-2232`); the capture path links Points (`sdk.py:5059` → `sdk.py:3492`, inside `_extract_session_v2`, invoked `sdk.py:4145`) **before** `_materialize_session_source` sets the real `sha256(transcript)` (`sdk.py:4343` → `sdk.py:5454`). So on the majority writer no version exists when the edge is made — the honest value there is ABSENT (owner decision O6).

### C3 — honesty requires a third state, and it is NEW wording
The honest recorded value is ABSENT, never `''` (which compares equal to a source's `''` and reads as a false *current*; #5199's `_anchor_on_create` guard, branch `edges.py:249-254`). `ONTOLOGY.md:712` states the read per link and **binary** ("current only when **every** link is"); a link with no recorded version is neither → **`unknown` is an addition to §4.6**, and naming it is owner-gated (O5). The read can *express* unknown without the wording change; the wording names it for readers. `:Source.version` must not be a fallback anchor: `_upsert_source` sets `s.version=1` ON CREATE (`entities.py:2432`) and `s.version+1` ON MATCH (`2451-2453`), but `_mint_source_stub` never sets it (`edges.py:116-133`) → `NULL+1` stays NULL. Naming: `tortoise_stale` already exists with **time-since-update** semantics (`tool_registry.py:822-827`, `sdk.py:10277-10283`) — a currency read must not nest under it.

**Root-cause verdict.** The requirement is root-cause; the issue's literal edge-only mechanism is symptom-level. **#5024 does not substitute** — #5024 closes the SOURCE-side journal gap; this issue's gap is the per-Point read-version record. #5024 is a child of **#5048**; **#5089** is the durability epic whose contract (`derived = replay(journal)`) the mechanism must satisfy.

**Boundary — interval-closing is OUT of scope here.** The owner's ruling makes window-closing part of the same write (`validTo`/`expiredAt`, `ONTOLOGY.md:716`), and its code half is **#3644** ("implement the v3.12 temporal model — validFrom/validTo + expiredAt on Object/Subject/Source"). #5038 records the version read; closing intervals is #3644's.

---

## 2. Constraints the mechanism must satisfy

**X1 — chain-consistency (reasoned from code; §5(b) is the check).** A supersede chain A→B→C must yield the same value live and after rebuild. The pass-2b replay applies descriptors at `src_f = _final(src)` (`projection/__init__.py:5581`, MERGE `5602-5605`) **in journal order**, so a chain's descriptors collapse onto the final node; the live path applies per hop. Consequence: an `ON CREATE SET` transfer is **not** chain-consistent (replay is first-writer at the collapsed node, live is last-writer per hop). **This is moot under Design D** (record nothing on transfer).

**X2 — if the transfer carries a version, the live site must carry the identical form.** The live transfer is a separate writer (`sdk.py:6364-6367` + `6369-6372`); the same guard must be applied there and at the replay.

**X3 — the journal seam does not exist for a map.** `_emit_event("PointAdded", {...}, point=self.get_point(pid))` (`sdk.py:3553`) journals the **node-property** snapshot; `get_point` returns `properties(n)`; a dict is not persistable (`_is_persistable_prop_value`, `entities.py:43-58`); pass-2 reads `ev["point"]` only (`projection/__init__.py:5249-5265`). Resolution: mirror `extractedFrom` — a **declared node property with its own explicit SET clause** (`entities.py:829-832`), edge-authoritative, prop-as-transit.

**X4 — supersede successors have no `PointAdded`.** `supersede_point` emits no `PointAdded` for the successor (none in `sdk.py:5930-6460`), so a version a successor *inherits* has no creation-time snapshot to replay from.

**X5 — the read's CURRENT operand depends on #5024.** `_upsert_source`'s in-place `contentHash` bump is unjournalled (`entities.py:2429`, `2444-2447`), so post-rebuild the read can report `current` where live reported `stale`.

**X6 — batching.** `batch_id = derive_batch_id(bundle)` runs once at `sdk.py:8980` **before** writes, so a server-observed version is outside the hashed bundle **iff** a **fail-closed reject of caller-supplied `sourceVersions`** is added. The precedent reject is in `_check_item_shape` (`sdk.py:8002-8016`), reachable only from `ingest`; `create_point` reaches only `_sanitize_props` (def `sdk.py:1217`, called at `3060`), which does **not** reject the key — so add it there too (it also covers `create_document`, `sdk.py:20684`, and the inferred `session:<id>` path).

**X7 — dedupe.** `structural_seen` keys `(src, etype, resolved_internal)` (`projection/__init__.py:5452-5456`, `5580-5583`); a second descriptor for the same key with a different version is dropped silently — a fail-open-to-`current` path (only reachable if O1 = carry-a-version).

---

## 3. Candidate designs

- **Design D — the transfer records NOTHING (absent → `unknown`).** A superseded successor is normally not re-read, so under §4.6's "the version **it was read from**" the honest value is absent. **This makes tasks 2–3's transfer edits unnecessary and sites 2–3 correct.** *Cost:* a transfer-inherited belief reads `unknown` (no staleness signal) until re-inference gives it its own read.
- **Design A — inheritance.** Unconditional `SET` at both the live transfer and the replay; the descriptor carries the emission-time value. Chain-consistent. **Contradicts `ONTOLOGY.md:710`** for a re-inferred successor → a **REOPEN of §4.6**, not an `OVERRIDES` adoption; fails safe (stale, never false-current) but not free.
- **Design B — re-derivation.** The transfer stamps only when the successor carries no value; the descriptor journals the **write decision**. Matches §4.6. More machinery (edge-existence pre-check; the no-op must survive the idempotence/dedupe lanes).
- **Design C — dedicated journaled provenance record** (solution-diverge #4), kept open: a separate record folded inside the pass-2b branch avoids editing the transfer sites, at the cost of a second journal record type (tension with #5048's "one complete, ordered journal" and the A10 one-record-type precedent).
- **Rejected:** edge-only create stamp without a journal transit (fails X3, drops on rebuild); journal-fold read with the edge left bare (contradicts the owner ruling at `STORAGE-ARCHITECTURE.md:510` / `ONTOLOGY.md:710` — a reopen — and fails acceptance A4); observability-only (answers neither A1 nor A4 and re-introduces a read that looks like currency).

**O1 is RESOLVED — see §8.** The owner chose **"record nothing"** (this plan's option **D**), so a transfer carries **no** version and the successor reads `unknown` until it is itself read from a source. **Task 2's editing half is therefore NOT taken** — no transfer edit, no `DirectEdgeRepoint` schema change, no dedupe-key change. Task 2 is retained below only as the record of what *would* have been built under O1 = A/B.

---

## 4. Task breakdown

### Task 1: Record the read version on the create path  *(decision-free — ships first)*
**Intent:** give the create path a durable, replayable per-link read version.
**Acceptance:** a Point created against a Source with a non-empty `contentHash` carries the version on its `extractedFrom` edge **and** it survives `rebuild_all`; a Point whose Source has `''`/no hash carries **no** property; a caller-supplied version is rejected on every tenant surface; live == replay.
**Files:** Modify `tortoise/projection/entities.py` (explicit SET clause + `_POINT_HANDLED`/`_META_KEYS`/`_POINT_LIST_PROPS` declarations), `tortoise/projection/edges.py` (`_link_source` NULL-guarded SET reading the passed value — **never** `s.contentHash` at replay), `tortoise/sdk.py` (`_sanitize_props` + bundle validator reject), `tortoise/consistency.py` (`_NEVER_A_NODE_PROP`/`_EXCLUSION_REASONS` entry, #5004 precedent), `tortoise/commit_schema.py` (`extra="forbid"` field if the bundle is touched), `config/ci-surfaces.yml` (register the new test — hand-curated, `tools/ci_selection.py:1489`).
**Test:** new `tests/test_source_version_extractedfrom_5038.py` (precedent: `tests/test_provenance_extractedfrom_3263.py`).

### Task 2: Transfer semantics  *(O1 RESOLVED = record nothing → no work; retained as the record of the not-taken path)*
**Intent:** under O1 = this plan's **A/B/C** the transfer would carry or re-derive a version; **the owner chose record nothing (= this plan's D), so the transfer is left alone.** This section is kept so a later lane can see what the alternatives would have cost — do not implement it without a new owner ruling.
**Acceptance (not-taken path, for reference):** for a chain A→B→C, live and post-rebuild agree on the successor's `sourceVersion`; the anchor exists at the successor and NOT at old; if a version is carried, the identical guard is applied at **both** `sdk.py:6364-6367` and `projection/__init__.py:5602-5605`, and `structural_seen` includes the captured value.
**Files (not-taken path):** Modify `tortoise/sdk.py` (emission SELECT/descriptor/live transfer), `tortoise/projection/__init__.py` (consumer + dedupe key), the `DirectEdgeRepoint` schema. Test: extend `tests/test_pointsuperseded_rebuild.py`.

### Task 3: Currency read (tri-state)  *(O3 FULLY resolved — placement by Policy B, enforcement by the owner 2026-09-26; see §8)*
**Intent:** deliver issue deliverable 3. **O3 is resolved by the Policy B ruling:** the check is a **read** comparing the version recorded on the extraction link with the source's current `contentHash`, with **no stored `status`** field, reported on the **existing** read surfaces — so **no new tool and no new SDK method** (the #4282 tool/method rule is satisfied).
**Acceptance:** per Point/per link `current`/`stale`/`unknown`, **`unknown` when either side is `NULL`/`''`**; never nested under `tortoise_stale`; and the **decided enforcement form** (owner, 2026-09-26T11:17:30Z, `#5038` comment `5845802608`): **when a newer fact exists, the out-of-date fact is NOT returned as an answer — it is disclosed as an FYI carrying its source** (*"We should not return an out-of-date fact when we have a newer one"* … *"let the user know that (newer fact but no source, and older fact from source X) … so I can disambiguate"*). Reporting stays on the **existing** result row (no new tool, no new SDK method) and no `status` field is stored. This is a **deliberate departure** from the field's flag-alongside practice — see the `OVERRIDES:` line in §8 O3. The **write-time** notice the owner also asked for is **`lane:c1-capture`'s**, not this task's.
**Files:** Create `tools/source_currency.py` as the shared derivation helper (the read path consumes it; it is not a separate user-facing surface). Test: unit + integration.

### Task 4: Close the loop on the residuals  — ✅ DONE on the residuals it OWNS (2026-09-25); ⚠️ O5 remains outstanding by design
**Intent:** make the deferred decisions and gaps visible where the next lane reads.
**Status:** O1/O3 posted on **#5038** (the artifact the owner reads) ✔ · the re-inference-engine issue **filed as #5422** (acceptance A2's home) ✔ · the `#5024` dependency recorded in the §9.6 status pointer and in #5422 ✔ · the 2489 step-4 departure **moot under O1 = D** (O2 was only live if a version were carried) — recorded as moot rather than left implied ✔ · ⚠️ **O5 is NOT posted and is NOT closed**: it is an **owner-gated** ontology-wording change (§8 O5 — *this work ships no ontology text*), so this task is done on the three residuals it owns and **explicitly not** on the acceptance line's O5 clause. Closing it would require the owner to add the third state to `ONTOLOGY.md` §4.6; until then it stays outstanding (see R8).
**Acceptance:** O1/O3/O5 posted on **#5038**; a re-inference-engine issue filed (acceptance A2's home); the 2489 step-4 departure surfaced; the #5024 dependency recorded. **⚠️ Read the O5 clause as NOT met:** the task deliberately does not satisfy it, because the ontology wording is the owner's (O5). The remaining clauses are met.
**Files:** Modify `docs/architecture/STORAGE-ARCHITECTURE.md` §9.6 (a pointer — §4.6 itself is owner-gated and must NOT be edited).

---

## 5. Testing strategy
Integration + round-trip (the epic's own row 9, `docs/epics/2026-09-24-5088-d10-fold/01-test-design.md:34`; lane registry `config/ci-surfaces.yml`): (a) live==replay round-trip with an explicit hash; (b) **supersede chain A→B→C** — live and replay agree (single-hop is insufficient); (c) pass-2b zero-incident; (d) unknown-absent (`''` yields no property and reads `unknown`); (e) `ingest` twice across a version change → identical `batch_id`, no duplicates; (f) caller-supplied `sourceVersions` rejected on `ingest` **and** `create_point(**props)`; (g) equality: the key never lands as a stray node property; (h) live==replay for the **current** operand (known gap until #5024 lands). Also: `tests/test_consistency_divergence_5011.py` (the live-vs-replay gate) and the closed-key-set tests (`tests/test_ingest_conformance.py:38-41`, `tests/test_ingest_bundle.py:752`).

## 6. Verification plan
Docker lane (`TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`), the parity suites (`test_pointsuperseded_rebuild.py`, round-trip parity, supersede edges), then `tools/surface_manifest.py check` for surface drift.

## 7. Complexity
| Domain | Rating | Rationale |
|---|---|---|
| Ontology | high | the third state is new wording; Design A is a §4.6 reopen |
| Architecture | high | replay determinism, the journal seam, the batch surface |
| Code | high | `edges.py` + `entities.py` + `sdk.py` + `consistency.py` + replay consumer |
| Overall | **complex** | matches `complexity:complex` |

## 8. Owner decisions (context · options · analysis · recommendation)

> ⚠️ **LETTERING HAZARD — READ BEFORE USING THE LETTERS.** The question actually put to the owner (issue comment 5841491304, re-posted to pass the decision gate as 5842177935) used a **different lettering** from this section: there **A** = record nothing, **B** = carry forward, **C** = re-derive, **D** = a separate record. **This section's letters are shifted by one against it** (this section's **A** = inherit, **D** = record nothing). The owner answered **"1 a"** = **record nothing**, i.e. **this section's option D**. Quote the *semantics*, never the bare letter.

- **O1 — supersede semantics (gates Task 2).** ✅ **RESOLVED — the owner chose "record nothing"** (in session, 2026-09-25; recorded on #5038) = **this section's option D**, *not* this section's A. The successor records no version and reads `unknown` until it is itself read from a source; §4.6 is unmodified. **Task 2 therefore needs no transfer edit** — the ontology-default stands and the §4.6 reopen is not taken. *Original options:* **D** record nothing (the ontology-default for a non-re-read successor) / **A** inherit (a §4.6 **reopen**, not an `OVERRIDES`) / **B** re-derive (matches §4.6, needs a write-decision descriptor) / **C** a dedicated journaled record. *Original recommendation:* **D** — which is what was chosen.
- **O2 — 2489 step-4 departure.** `docs/plans/2026-09-07-2489-structural-edge-parity.md:71` is a plan **step** (rationale scoped to direction/confidence/weight). *Recommendation:* surface on #5038; only relevant if O1 = A/B. **O1 = D (record nothing) → this is now moot.**
- **O3 — the currency read's surface (gates Task 3).** ✅ **FULLY RESOLVED — placement by Policy B (2026-09-24), enforcement by the owner (2026-09-26).** **Placement:** the already-recorded **Policy B** ruling (comment `5814346672`) — *"A currency check reads the version recorded on the extraction link and compares it to the source's current `contentHash`. That is a **read** … **no stored `status` field**"* — i.e. on the **existing** read surfaces, adding **no new tool and no new SDK method**, so #4282's tool/method rule is satisfied and neither the in-repo-tool nor the SDK/MCP option is needed. **Enforcement (owner, 2026-09-26T11:17:30Z, comment `5845802608`):** *"We should not return an out-of-date fact when we have a newer one"* — the out-of-date fact is **withheld as an answer** and **disclosed as an FYI carrying its source** (*"let the user know that (newer fact but no source, and older fact from source X) … so I can disambiguate"*). This **refines rather than reverses** Policy B: its prohibition was on the fact being *"silently"* withdrawn, and the ruling requires exactly the disclosure that keeps it non-silent. **OVERRIDES:** the field's practice of returning a stale fact **alongside** its replacement with a flag (Zep/Graphiti's temporal fields on results; the RAG `is_latest` practitioner norm) — withheld-as-answer + disclosed-as-FYI is deliberate, because a visible flag is measurably not acted on (`arXiv 2609.08258`: five systems return the revoked fact and outrank its replacement; `arXiv 2605.06527`: 77.5% visible vs **3.3%** adjudicated). **The write-time notice** (*"ideally at write time it would have told me"*) is **`lane:c1-capture`'s**, not this lane's. Research report: `docs/research/2026-09-25-5038-source-currency-read-path.md`. **The same ruling opened O9 below.**
- **O4 — Documents.** *Options:* extend `create_document`'s `extractedFrom` (`sdk.py:20684`) / restrict to `label="Point"`. *Analysis:* §3.3 declares `extractedFrom` as `Point → Source`; #5026 retires `:Document`. *Recommendation:* restrict.
- **O5 — the §4.6 third-state wording.** `unknown` is an addition to §4.6's binary text; owner-gated. *Recommendation:* the owner adds it; this work ships no ontology text.
- **O6 — the capture-lane anchor.** *Options:* reorder materialization before extraction / accept `unknown`. *Recommendation:* accept `unknown` now; file the reorder with a named owner.
- **O7 — acceptance A2's home.** ✅ **RESOLVED — filed as #5422.** No re-inference-engine issue existed; A2 could never be marked complete and the plan pointed nowhere for it. #5422 is its home, filed as a scoping gap (not a design) with its precondition (#5024) and its already-decided constraints recorded so they are not re-opened. *Original recommendation:* file it.
- **O8 — sequencing.** PR #5207/#5199 is OPEN and `CONFLICTING` across the same three files; **#5093** ("lane L1: projection-keys — the replay keys (#5026 → #5025 → #5024)") is the containing lane; #5026 FIRST, #5025 SECOND; #5024 edits `projection/entities.py`; **#3644** owns interval-closing. *Recommendation:* land #5207 and the #5093 lane first, then rebase.
- **O9 — how mechanical validity reconciles with EP confidence. ✅ RESOLVED (research complete; no owner decision required) — raised BY THE OWNER** (2026-09-26T11:17:30Z, comment `5845802608`). His words: *"confidence is derived logically through logical relationships and evidence backing"* (epistemics) versus *"in traditional knowledge graphs, validity is derived mechanically assuming sources are the source of truth"* — *"So not sure how we reconcile the two here"*, with the stated risk of ending up with *"2 parallel mechanisms"*. *Why it matters:* if a fact's currency is decided by a source-hash comparison while its belief is decided by EP, the two can disagree — a source-fresh fact with weak evidence and a source-stale fact with strong evidence — and nothing yet says which wins or whether one silently overrides the other. ✅ **RESEARCHED (2026-09-26) — the answer is the gate, and the composition already ships.** **Finding:** the two cannot become parallel mechanisms, because confidence and validity are *already* combined by one explicit rule — `has_ep = measured AND NOT terminal` (`search_engine.py:2002`; computed `:1739`/`:1754`; `_alive_flag`, `live.py:139`) — and `terminal` is not a belief factor at all but a **participation gate**: `ep.py:1081` `participates = not is_terminal and (status != "draft" or include_draft)`, with terminal inputs *"DEAD for EP and NEVER participate"*. **Evidence quality already reaches EP** through `sdk.py::_apply_source_inheritance` → `_compute_source_prior` → the point's **Beta prior** (`ep_alpha`/`ep_beta` ≈ `log2(N+1) · decay · Σ pc_base(tier)`) — **not** an operator weight (`weights.py::compute_operator_weight` has no source-tier or decay input; an earlier version of this note said otherwise and was corrected). **Recommendation — no owner decision required, it contradicts nothing recorded:** (1) currency is a **gate** composed into the existing `AND` — `measured AND NOT terminal AND NOT (stale ∧ a-newer-fact-exists)` — **not** an EP factor; (2) evidence quality keeps flowing via the Beta prior, unchanged; (3) on the disagreement case (newer **unsourced** fact vs older **sourced** fact) **disclose, never silently adjudicate** — which is the owner's own ruling; (4) do **not** lower an unsourced fact's confidence as compensation — **label** its provenance in the result instead, because re-scoring belief from a validity fact is exactly the entanglement that produces the *"2 parallel mechanisms"* the owner named. *Evidence:* direct code verification + TMS (validity as a relabeling/justification layer; ATMS for multi-context) + AAAI *Marrying Uncertainty and Time in Knowledge Graphs* (probabilistic facts under hard constraints). Issue comments `5845980017` (finding) and `5845994202` (verifier correction).

## 9. Outcome of this scoping pass
Two halves with different readiness:
- **The create-path anchor (Task 1) is SAFE and self-contained** and is filed as one scoped child issue: it threads the value through the point's own journaled snapshot (X3's `extractedFrom`-style transit) so live == replay, with no transfer edit and no owner reopen.
- **The transfer semantics (Task 2) is NOT self-contained** — it forces a §4.6 reopen (O1) and touches the live transfer, the descriptor, and the dedupe key. **No implementation is dispatched for it**; it waits on O1, and O3/O5 gate the read/model wording.

## 10. Residuals (documented, not chased)
| # | severity | residual |
|---|---|---|
| R1 | P0 | O1: the transfer semantics is a §4.6 **reopen**, owner-only. |
| R2 | P0 | If O1 = A/B, the live transfer write site (`sdk.py:6364-6367`) must carry the identical guard. |
| R3 | P1 | The journal seam for a version (X3) must use the `extractedFrom` node-prop precedent or an envelope seam. |
| R4 | P1 | Supersede successors have no `PointAdded` (X4). |
| R5 | P1 | The caller-supplied reject must reach `_sanitize_props`, not only the bundle validator. |
| R6 | P1 | The read's current operand depends on #5024. |
| R7 | P1 | `structural_seen` dedupe can drop a differing version (only if O1 = A/B). |
| R8 | P2 | The tri-state's ontology wording (O5) is outstanding; `unknown` is currently expressible only as a read outcome. |
