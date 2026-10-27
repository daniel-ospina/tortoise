---
title: "Tortoise — Ontology Changelog"
type: engineering
domain: platform
doc_status: live
created: 2026-10-15
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: ONTOLOGY.md, canonical ontology, model versions
---

# Tortoise — Ontology Changelog

> **What this is.** The record of *what changed in `ONTOLOGY.md`, and why* — one
> entry per model change, newest first. It is implementation and change history:
> version numbers, the issue that drove each change, and the reasoning.
>
> **What `ONTOLOGY.md` is.** The model we want — the specification. It states the
> shape the system is being brought to, and it does not carry a status report.
> When you need to know *whether something is built* rather than *what it should
> be*, this file is where that history lives; the code and its tests are where
> the current truth lives.
>
> **Moved out of `ONTOLOGY.md` on 2026-10-15.** Owner approval of the exact diff is
> tracked at **#8745** (`ONTOLOGY.md` carries a CRITICAL RULE: no modification
> without Daniel Ospina's explicit approval). The two *architecture* documents'
> parallel move is the one authorised by **#8542**. The ontology carried
> this block inline — 730 lines, 37% of the file — against its own header, which
> declares it is *"not a status report on what is built."* The entries are moved
> verbatim; nothing is deleted.

**CRITICAL RULE: no modification to this file is allowed without explicit approval of the exact changes by Daniel Ospina.**

---

> **Changelog v3.31 (2026-10-26 — issue #9202 — the END's state is SETTLED, and the write door SHAPES or REFUSES):**
>
> **Owner rulings, 2026-10-26 (Daniel Ospina): the spec text approved verbatim, then the STRUCTURE of where it
> lives ruled on.** This entry records the model statement that replaces the sentence *"Whether that state
> should instead read `unknown` / `open` is **#9202's owner decision** and is NOT settled by the gate"* —
> the gate question that had been open since the 2026-10-20 coverage entry and the 2026-10-24 write-door
> entry below — and records the second ruling that split ONE approved package into its three homes.
> **`ONTOLOGY.md` is bumped v3.30 → v3.31** (a model change moves the version), the bump approved as its
> own item because that document's CRITICAL RULE gates its own version/`updated:` lines. **This file carries
> its own *"no modification without approval"* line; the 2026-10-26 ruling REQUIRES this entry and records
> that this file is not gated** — stated here so the conflict is visible rather than assumed away.
>
> **Where this lives (the 2026-10-26 structure ruling — this is a MOVED change, not a re-decided one).**
> `ONTOLOGY.md` is canonical and states the **model only**: the in-line §4.7 edit states that an end which is
> stored but names no instant is an un-datable END reading `end_state: "unknown"`, that `END_KNOWN` means
> **dated** not **present**, and that `END_OPEN` stays reserved for an ABSENT end. Everything that is
> **agent-UX behaviour** — the marker vocabulary, the shape rule, the receipt, the refusal wording, and the
> logging/counting — is annotated at the **decision site** (`validity.read_caller_end`,
> `validity.END_UNKNOWN_MARKERS`, `validity.END_WRITE_OUTCOMES`, `sdk._shape_or_refuse_caller_end`,
> `sdk._with_end_receipt`) and documented on the **tool surface** (`config/surface-manifest.yml`'s
> `tortoise_create_point` / `tortoise_update_point` `job` cells, and the `valid_to_shaped` rows in
> `docs/product/mcp-sdk-surface.md`). None of that text is new: it was already written at those homes, and
> the ontology's copy of it is withdrawn rather than re-homed. The `agent-UX` paragraph that stood in §4.7
> for one commit was 45 added lines (a marker catalogue, the receipt, the refusal narrative, the
> logging sentence and a test-name catalogue); the in-line replacement is 20 lines, and every sentence withdrawn
> from it is accounted for in the PR's triage table.
>
> **The model statement.** `END_KNOWN` now means **dated**, not **present**. `validity.end_state` gates
> it on the bound's own orderability (`_names_an_instant`, the `_created_sort_key` measure
> `effective_precision` and `_covers` already order with), so a stored end that names no instant reads
> **`end_state: "unknown"`** — an **un-datable END**: the fact ended, at a time we cannot pin. It is the
> same state the DECLARED marker (`validToPrecision = "unknown"`, no `validTo`) produces, and it fires
> the same reply-level `end_state_unknown` / `indeterminate_end_ids` flags. `END_OPEN` stays reserved
> for an **ABSENT** end: a column carrying a value is an ASSERTED end, so "no end was recorded" is a
> claim the record contradicts, and reading a present-but-un-datable end as still-true is the
> fabricated-coverage hazard the vocabulary exists to prevent (#2349/#2350). This also closes the
> contradiction where the same read block reported `valid_to_precision: null` beside `end_state:
> "known"`.
>
> **What ELSE changed — the write door, on the existing caller-bound gate.** `create_point` and
> `update_point` now read a caller's `validTo` for INTENT on the way in, on that same orderability
> measure, and tell three kinds of input apart. **A recognisable instant is accepted**, and
> representational variance is **normalised silently** (`2024-3-1`, `2024/03/01` → `2024-03-01`): the
> caller finished their sentence, so there is nothing to report. **A value that declares no date**
> — blank, or a human uncertainty marker (`TBD`, `TBA`, `TBC`, `unknown`, `N/A`, `NA`, `-`, `--`, `—`,
> `?`; case- and whitespace-insensitive) — is **SHAPED** into the canonical un-datable form: no end
> value plus `validToPrecision = "unknown"`, with **the caller's token echoed in the write reply** as
> `valid_to_shaped`. **A value that attempts an instant and fails, or carries no recoverable intent** —
> an impossible calendar day (`2026-02-30`), a month 13, an epoch supplied as a **string**,
> `"not-a-date"` — is **REFUSED**, naming the fault, the repair, and the forms the field accepts. Shape
> and refuse are both logged and **counted** (`validity.END_WRITE_OUTCOMES`): the ruling's own test for
> a vocabulary that is still too narrow is "the same refusal recurring for the same caller shape", and
> only a count can show that.
>
> **The receipt is what makes shaping defensible.** Shaping without an echo would merge a real typo
> (a `-` typed where a dash between two dates was meant) into "the author did not know", leaving no
> trace. The receipt is that trace, and it is why the *stored* form may be filled in at all: "ended, and
> I cannot say when" is exactly what the caller said, so the filled-in meaning and the caller's meaning
> are the same claim. That is the only place filling in is legitimate.
>
> **⛔ Two invariants that are not negotiable.** (1) **Every shaped token reads `unknown`, never
> `open`.** Mapping any token to `open` would alter a reading the 2026-10-25 ruling pinned — a second
> decision smuggled in as a side effect. (2) **Pre-existing rows are never re-validated and never
> migrated.** A row written before this change keeps its literal end and keeps reading
> `end_state: "unknown"`; the read path does not care which form a row carries, which is what keeps the
> two dialects byte-identical. A backfill would be a **new** decision, not a tidy-up.
>
> **OVERRIDES:** the repo's *"accept the caller's bound verbatim and only warn — never refuse"* (#6140,
> both ends) — a recognised uncertainty marker is shaped to the canonical un-datable form and an
> illegible attempted instant is refused, because a stored end is an **authored belief about when a fact
> stopped being true**, so finishing a caller's sentence for them can record a claim they never made.
> The protection that ruling existed for is untouched: it was there so a legacy record carrying junk
> would stay **supersedable**, and refusing here only ever blocks a value a caller supplies **now** —
> existing rows are never re-validated, and `supersede_point` resolves the predecessor's end from its own
> chain, not from a caller's `validTo`. **The constraint that produced the old rule is absent at this
> door**; that, not a change of mind, is why the reversal is safe.
>
> **Two reversals, recorded rather than quietly edited.** (1) *"stored verbatim"* (the write-door ruling
> of 2026-10-24, the entry below) — superseded by shaping. (2) *"a warning, never a refusal"* (#6140) —
> superseded **for this class only**, by the two refuse paths.
>
> **What did NOT change.** The `validFrom` (START) ruling: it keeps its #6140 **warning**, because that
> ruling named the start and nothing has reversed that half — shaping the start would re-open a reading
> it deliberately pinned. Coverage is unchanged (`_covers` skipped an unorderable end before and skips an
> absent one now — #9408). Every read surface is unchanged. `supersede_point`'s window resolution is
> unchanged: a predecessor whose end is canonically un-datable is dated by the successor's resolved
> start, and its `validToPrecision` marker is **cleared** rather than left beside the new date (measured;
> `supersede_point(..., valid_to_precision="unknown")` keeps it un-datable instead) — no inconsistent
> triple, and no caller `validTo` is consulted on that path.
>
> **Residuals, stated not fixed.** The unguarded `update_entity` Point arms remain a live route to a junk
> end (#9649), and `supersede_point`'s numeric-`valid_from` kwarg exemption is not a caller `validTo`
> write — so **the refusal is not airtight**. That caution is stated at the decision site (the
> `sdk._shape_or_refuse_caller_end` docstring) and here, not in §4.7: a residual is build state, and the
> canonical model document does not carry scope caveats about its own tool surface. The new reply field is
> a field on the two existing write doors, not a new tool or public SDK method; `tools/surface_manifest.py
> check` and `tools/surface-guard.py` both stay green, with the field recorded in the manifest's
> `response_fields` block.
>
> **Surface.** No tool or public method added, removed, or renamed. Two tool DESCRIPTIONS gained the
> accepted-`validTo` documentation §2 requires, which moves their derived `job` cells in
> `config/surface-manifest.yml` and their rendered rows in `docs/product/mcp-sdk-surface.md` — no
> re-cut, no counting change (81 tools / 147 public SDK methods unchanged).
>
> Pinned by `tests/test_validity_windows.py`: the **shaping matrix** over the marker vocabulary (canonical
> form + receipt), the **refusal matrix** (one row per named fault), a **control** that a real date and a
> readable format variant stay untouched and silent, and the reading pins left **green and unmoved** —
> plus `test_an_end_naming_no_instant_bounds_no_instant_even_for_an_unparseable_probe`,
> `test_an_un_datable_end_reads_unknown_and_fires_the_flags`,
> `test_the_two_un_datable_spellings_stay_distinguishable` and
> `test_the_un_datability_gate_does_not_widen_end_state`. §4.7 cites ONE of the four —
> `test_an_end_naming_no_instant_bounds_no_instant_even_for_an_unparseable_probe`, the coverage pin, in the
> same single-pointer style as the START paragraph above it — because a catalogue of test names is a build
> record, not the model. The write-door pin was **re-stated, never deleted**: under this ruling some of its
> rows are refused rather than stored, and a "simplification" that drops the matrix drops the only thing that
> would catch a silent reversion to verbatim storage.
>
> ⚠️ **Supersedes, in time order,** `## §4.7 — the write door says what an un-datable END means (#9202,
> write-door half, 2026-10-24)` near the end of this file, which still reads *"the END's reported state is
> still the owner's call"* and lists `end_state` under "What did NOT change". That entry was accurate on
> 2026-10-24 and is not rewritten; **its `end_state` claim and its "stored verbatim" shape are the two
> things this ruling reversed.**
>
> **Changelog v3.30 (2026-10-21 — issue #8745 — §4.6's `rawState` row states the model, not the build):**
>
> **The row no longer carries build status or tracker references.** Removed from it: *"no reader supplies an
> observation today (`#8740`, the tracked follow-up)"*; the `resolve_raw_availability(props, observed=…)` call
> signature; the `:Source`-property read-bag sentence that named the residual `#8739` and a source file path; the
> internal coverage list (`create_source`/`_upsert_source`, `_fold_source_versioned` → `_upsert_source`, the
> `SourceCreated` replay); and the module constant `raw_state.RAW_DELETION_MARKER_TTL` (now "the deletion-marker
> TTL", with the value and its rationale kept).
>
> **The row's retention authority is NAMED, not assumed.** Dropping the constant name removed the only thing on the
> line that tied its 10-day bound to an authority, so the row now cites **R1** *with the document that defines it* —
> `docs/retention-and-deletion.md`, whose `RAW_DELETION_MARKER_TTL` row is the **sole authority** for that guarantee
> window — beside §4.6's existing `(R2)` citation of `docs/durability-posture.md`, which lives in the version-record
> note below the table, not in the row. `R1` is the owner ruling *restore
> window ≠ retention window; no retention window on user **content***, stated in code at `tortoise/raw_state.py`, in
> `docs/retention-and-deletion.md`, and in `docs/plans/2026-09-18-4179-retention-deletion.md`; it is a resolvable
> citation, so it is **kept** — and it now resolves on sight instead of by lookup. (The constant NAME is build
> detail and belongs here; the ruling it implements is model, and the companion doc cites `R1` for this very row —
> a row that dropped it would leave that clause with no traceable basis.)
>
> **Two things KEPT because they are model authority, not issue state.** (1) The decision citation on the
> conditions clause, `(#3998 owner ruling, Option A, 2026-10-14)` — it records *why* the two-value vocabulary
> exists, and §4.6 cites decisions the same way elsewhere (`#3644`, `#5024`, `#3590`). The class the owner's ruling
> names is an issue cited as **build status** (*"no reader supplies one today (#8740)"*), not an issue cited as the
> **authority for a rule**. (2) The fail-closed sentence stays **scoped** — *"the availability resolution reads it
> fail-closed as `unrecognised`"*. An unqualified form would describe a write-off of a shipped read surface: the
> `:Source`-property read bag still serves a stored condition verbatim, which is the open gap at **#8739** — that
> fact is build state and lives here, not in the model.
>
> **Why.** `ONTOLOGY.md` states the model we want; *whether something is built* is this file's job, and its own
> header says so. The row had drifted into a status report — and a lane reading it as specification would take
> "no reader supplies one today" for a rule. Owner ruling, 2026-10-21.
>
> ⚠️ **Scoped to this row.** The ruling has NOT been applied section-wide: three other §4.6 rows still carry build
> status (`validFrom`'s "tracked on #3644 … no such path exists yet (#3590 S2)" and two version-record notes, "no
> `:Source` reader exists yet" / "**What remains open**"). Widening the approved span was not this change's to make
> — `ONTOLOGY.md` line 39 requires the owner's approval of the *exact* changes — so they are tracked at **#9497**,
> with the guard question (a section-wide negative arm needs an allow-list for authority citations) recorded there.
>
> **Nothing is lost.** The two open gaps the removed text described remain tracked at **#8740** (no reader
> supplies an observation yet) and **#8739** (the `:Source` read bag still serves a stored condition verbatim).
> Every *rule* in the row is unchanged: `rawState` recordable as `present`/`deleted`, **a deletion is an ACT**,
> the 10-day bound, `rawStateAt` minted on every state-carrying write, conditions never stored, the observation
> caller-supplied and never persisted, a recorded act winning over any observation, a stored condition read
> fail-closed as `unrecognised`, expiry authorising a journalled drop, and the property refused on the generic
> entity surface.
>
> **Changelog v3.29 (2026-10-17 — issue #3554 — the canonical `:Session` capture-container is declared, and §4.1's `speaker` row is corrected):**
>
> **A new model section (§4.8), recorded in its OWN release.** The `:Session` capture container
> had no canonical declaration: what a session node carries, which writer owns each property, and
> which writers can create one at all were re-derived per lane and answered differently. §4.8 now
> states the property set with its owning writer per property (mirroring the machine declaration
> `tortoise/session_schema.py`, whose writer/client inventories and scan predicate are re-runnable
> through `tools/session_retrofit_audit.py`), the convention the `id` row uses (an owner is a
> `SET <alias>.<prop>` writer; a `MERGE` key is **not** attributed), and the three D9
> **recorded-honest-gap** decisions #3554 owns: the turn-count shortfall that can hide under
> `capture_ok = true`, the capture_ok ordering against the post-verification read, and the
> non-monotone `entity_links_created` counter.
>
> - §4.8 (added by #3554, 2026-10-17): the canonical `:Session` capture-container schema and
>   its owning writers — the doc half of the machine declaration
>   `tortoise/session_schema.py`, plus the three D9 *recorded honest gap* decisions (the
>   turn-count shortfall under `capture_ok = true`, the pre/post-verification-read
>   `capture_ok` ordering, and the non-monotone `entity_links_created` counter). §4.1's
>   `speaker` row is corrected in the same change: BOTH hosted capture and the SDK write
>   it, via the one shared turn store (#3551/#3086), not the SDK alone.
>
> **Why this block exists as its own version.** The section was first recorded inside the
> `v3.24` block, which is four model versions behind the document that carries it — so the model
> version was never bumped, and a reader asking "when was §4.8 added?" was sent to a release
> whose date and issue list cannot contain it. A new model section is a model change: it gets its
> own block, and `tests/test_session_schema_3554.py` pins the block's version to the version the
> document itself declares (and pins it as the NEWEST block, so the misattribution cannot recur
> one header at a time).
>
> **Changelog v3.28 (2026-10-15 — issues #3998 + #2350 — the absent-raw `:Source` state recorded in §4.6, and per-end validity precision + the 3-state window end):**
>
> **A record of a decided rule, not a model change.** `tortoise/projection/entities.py` has written `s.rawState` / `s.rawStateAt` since #3998 (`36d3077fd`) and the §4.6 table had no row for either (measured on `origin/main` `c377fea26`: `rg -c rawState docs/ONTOLOGY.md` → 0). §4.6 now carries the row, and it encodes the owner's ruling of **2026-10-14 (Option A, stated on #3998)**: a **deletion is an act**, recorded durably as a marker **bounded by an expiry policy**; **`offline` / `access_revoked` are conditions, observed live, never stored**; and the marker carries the expiry policy. The row states the **read-time observation contract** (`raw_state.resolve_raw_availability`) and the precedence it enforces — a recorded act wins over any observation, so a probe can never un-delete a memory — so the next lane reads the placement axis and the contract instead of re-deriving them. This closes the S7 residual recorded as **#8474**.
>
> **An additive field layer on §4.7's window pair — no existing window is re-stamped and no
> parallel validity field is minted.** `validFromPrecision` / `validToPrecision` declare how
> precisely each end is known (`year` | `month` | `day`), and the end-only marker
> `validToPrecision = 'unknown'` closes a window whose end cannot be dated — the **3-state end**
> (`open` / `closed-at-known-date` / `closed-unknown`). A legacy bare-ISO window reads as `day`
> with no migration. The vocabulary, the ONE validator, and the shared read projection live in
> `tortoise/validity.py`; the marker is surfaced on the search/recall, assembly and
> `get_historical_knowledge` replies (§4.7, "Per-end precision and the 3-state end").
>
> **Changelog v3.27 (2026-10-14 — issue #7818 — the §4.7 coverage boundary, stated and left OPEN):**
>
> **A record of an undocumented rule, not a model change** — no field, marker or
> vocabulary moves. §4.7 stated no coverage rule for the query instant `t`, so two
> readings coexisted silently: the `validTo` row's prose (*"the old fact stops being
> true when the new one starts being true"*) describes a **half-open** `[vF, vT)`,
> while the only read path that answers *"what was true at `t`"* —
> `get_historical_knowledge`'s `_covers` — implements a **closed** `[vF, vT]`, the
> rule `commit_schema.validate_validity_window` declares and its #5374 agreement
> test pins (`t == vT` and a zero-length `[t, t]` cover). §4.7 now states the
> boundary and its consequence — a well-formed contiguous chain reports
> `status="ambiguous"` at the one shared instant — and records the choice between
> the two readings as **OPEN**, tracked at **#7818**. Two read-path contract defects
> are fixed alongside it: the `ambiguous` reply now carries an explicit `found`
> (True) instead of omitting the key, so a `.get("found")` consumer can no longer
> read two candidates as honest absence, and every reply carries a `status`
> (`"found"` | `"ambiguous"` | `"absent"`).
>
> **Changelog v3.26 (2026-10-14 — issue #7551 — the operator-label declared vocabulary is stated, and the write path consults the whole of it):**
>
> **A code/record alignment, not a model change** — no meaning, direction or vocabulary
> moves. §3.9 already said operators are *"`IMPL`, `NAND` (+ semantic label)"*, but the set
> `create_operator` checked a `label` against was the **pack relations alone**: every core
> predicate read as *undeclared*, so `supports` — this document's own evidence→claim meaning
> (§8) — returned the same `undeclared_relation` warning as an invented name. A lane with no
> reachable declared predicate therefore invented one (`measures`, ≈25 such edges). §3.9 now
> **states the declared vocabulary** — the mechanism pair ∪ the closed §8 semantics vocabulary
> ∪ every predicate an installed pack declares (`pack_registry.CORE_OPERATOR_LABELS`) — and
> the write path consults all three. **Warn-not-block is unchanged (#1934):** declaredness is
> what the check answers, not shape (the §8 reification table governs the latter), and a
> **retired** spelling (`opposes` / `produces` / `holds` / `funds`) deliberately keeps warning
> so the caller learns the canonical word. `measures` stays undeclared.
>
> **Changelog v3.25 (2026-10-13 — issue #5142 — the D10 code half's ONTOLOGY record: the `format` markers, the deferred node collapse, and the document `:Source` scoping):**
>
> **A record correction, not a model change** — no decision, vocabulary or semantics
> moves. What was missing was the *record* of facts already landed or already decided,
> found by the code review of PR #5127. Three gaps close here:
>
> - **§4.6/§4.7: the two `format` markers moved ⚠️ → ✅, and the `_SOURCE_HANDLED |=
>   format` fact, had no entry.** PR #5127 — the D10 code half, `294d5847e`, merged
>   2026-09-25 — flipped §4.6's field row and §4.7's matrix row because `format` moved
>   into `_SOURCE_HANDLED` (`tortoise/projection/entities.py`), and it touched no
>   changelog line: the document then asserted a landed code state with nothing saying
>   when or why it landed. Recorded here. ⚠️ **Read the rows' *"since D10 (v3.15)"* as
>   the MODEL's version, not the code's**: v3.15 is where §4.4 declares *a document is a
>   `:Source`*; the code half that flipped the markers is PR #5127.
> - **§4.4: the deferred node collapse is now stated, beside "a document is a
>   `:Source`", with its `OVERRIDES:` line.** D10 keeps **two** `:Source` nodes per
>   document — the document node (`url = <doc id>`) and the corpus `#205` provenance
>   Source (`url = source_url`) — and the **collapse is deliberately deferred**. The
>   reasoning lived only in `_upsert_document`'s docstring and the epic's test-design
>   doc, so §4.4 read as an invitation to merge the two nodes; the **`OVERRIDES:` line is
>   posted on #5013** and carried at §4.4.
> - **§4.6: the document `:Source` scoping is now stated at the row a reader trusts.**
>   §4.6 marks `sourceKind`/`contentHash`/`version` REQUIRED ✅, but a document `:Source`
>   is written by different paths (`_upsert_document` for the `DocumentCreated` fold, plus
>   the `objectType='Document'` event branch and the hosted `/v1/sessions` transcript MERGE)
>   and supplies them conditionally.
>   `contentHash` + `version` are written on the path that READS content (PR #5592 /
>   `700960943`), and **`sourceKind='document'` since #5082** (all three writers, CREATE-only,
>   #398) — so the note records which field is conditional on the read path instead of
>   leaving the absence quiet under the global "code may lag" caveat.
>
> **Changelog v3.23 (2026-10-10 — issues #7813 + #7853 — confidence propagates across all four epistemic labels):**
>
> - §5/§8: the point↔object and point↔event kind overlap is stated as the model,
>   not compatibility residue. The #7813 owner ruling is that **confidence (EP)
>   propagates across `:Point`, `:Subject`, `:Object` and `:Event`**. §5 now names
>   **both** overlapping event kinds (`decision` **and** `humanApproval`), and §8's
>   reification rule and its table row are widened from `Point↔Point` to the four
>   labels. The operator node itself stays `:Point {is_operator:true}`; only its
>   children/targets may carry any of the four labels.
> - §2/§3.1: the epistemic-target enumerations are widened to the four labels.
>   §2's Epistemic-layer row (BOTH its Entity and "How it works" cells) and the
>   Structural-vs-Epistemic table + Principle, and §3.1's heading and its
>   `IMPL`/`NAND` From→To cells.
> - §3.8: the `op: IMPL/NAND` row is widened to the four labels in BOTH its
>   From→To cell and its Meaning cell (the pre-#7813 Meaning said Point→Event was
>   **write-only in v1 with no EP propagation** — under #7813 an Event target is
>   epistemic and propagates). The departure is marked `OVERRIDES` at §8.
> - The write path admits `:Point`/`:Subject`/`:Object`/`:Event` operator
>   endpoints (`#7902`), so each declaration of a `:Subject`/`:Object` operator
>   endpoint is a live end-to-end shape.
>
> **Changelog v3.23 (2026-10-10 — issue #3644 — the §4.6 *Versioning* WINDOW half is implemented on `:Source`):**
> §4.6 declared the Source version window and `#5024` landed the *version-anchor*
> half (a hash transition journals a `SourceVersioned` record naming
> `previousContentHash`). The window half was *declared, never written*: no
> writer set `validFrom`/`validTo`/`expiredAt`, so *"the journal record that
> opens a new version closes the old one in the same act"* was an intention, not
> a recorded act. **Implemented now, exactly as §4.6 words it:** `create_source`
> mints the window instant **once**, on the same clock as `updatedAt`;
> `_upsert_source` writes the node's `validFrom` through the same hash-diff gate
> as `version`/`updatedAt` (create, stub completion, real transition — never a
> no-op re-check, never a hash-less write); and the `SourceVersioned` record
> carries **both** window facts — `validFrom` (the new version's open, the node's
> window start) and `previousValidTo` (the prior version's close), the **same
> instant by construction**, so the chain is exactly contiguous. **`previousValidTo`
> is record-only**: one node per `url`, the node holding the CURRENT version whose
> window is open (absent `validTo` IS the open end — Cypher cannot write NULL), so
> a prior version's close can only be a journal fact. An absent `validFrom` is an
> **open start** and is never synthesized at replay time (`$vf` is the recorded
> instant, never `coalesce($vf, $now)`), so every legacy journal line replays
> byte-identically to the source it was written from. §4.7's Source column moves
> `validFrom`/`validTo` ❌ → ⚠️ (§4.6 field table `❌` → `⚠️`), with the two
> different reasons stated in the † footnote. **Also not claimed, and deliberately
> so:** the invariant holds on the `create_source`/`_upsert_source` path only —
> `_upsert_document` and `_materialize_session_source` are further `:Source`
> `contentHash` writers that do **not** move the window, so a stamped source can
> be left with a present-but-stale start; extending the gate to them needs a
> minted instant that is also replayable (a separate clock/mint decision), is
> tracked on **#3644**, and is **pinned, not silenced**, by
> `tests/test_source_version_window_3644.py::test_pinned_gap__*`. **Not claimed, and deliberately so:**
> the node's `validTo`/`expiredAt` need a `:Source` REMOVAL path — that field's
> meaning is **#3590** S2's decision — and a caller-asserted valid-time start
> needs the inversion guards the Point window needed (#3980/#4021/#5358/#5360);
> `Subject`/`Object` windows remain ❌ (§4.2/§4.3).
>
> **Changelog v3.23 (2026-10-11 — issue #1423 — Subject status is projection-owned, and supersession reaches the Subject family):**
> §4.2's `status` cell recorded a **capability gap** ("projection-owned status is planned, NOT yet
> implemented on Subject", #2295) and the Subject family had no supersession path at all:
> `commit_ops.apply_supersessions` refused a record whose successor was a `:Subject` (the #1370
> warn-and-skip), so a replaced team/role/person stayed `live` and search-visible forever. Both are
> now built. `_upsert_subject` **seeds** `status` ON CREATE and `status` ∈ `_SUBJECT_HANDLED`, so the
> Object's #1350/#2194 clobber guard applies to Subjects too; the supersession write goes through the
> generic entity surface (`EntityMutated op="supersede"`, journaled with `status`/`supersededBy`/
> `supersededAt`), which the ONE `SET n += $state` arm replays in all four engines; and
> `commit_ops.SUBJECT_TERMINAL_STATUSES` excludes terminal Subjects from the four retrieval legs by
> default. §4.2's `status` row moves ⚠️ → ✅ and gains `supersededBy`/`supersededAt`. **No model
> change** — the Subject lifecycle vocabulary and the `superseded, never deleted` doctrine are
> unchanged; this is the declaration catching up to the build. (The Subject `validFrom`/`validTo`
> WINDOW is a separate row and stays ❌ — #1423 does not touch it.)
>
> **Changelog v3.22 (2026-10-10 — issue #7902 — the operator-endpoint WRITE path admits the four epistemic labels):**
> The owner ruling #7813 makes confidence propagate across :Point/:Subject/:Object/:Event. (#7853's
> read-widening branch is unmerged; on `main` the EP reads and the WRITE path are still Point/Event.)
> #7902 widens `create_operator`, `create_direct_edge` and the operator-dedup id sweep to the four
> labels (one declaration, `tortoise/live.py::EPISTEMIC_LABELS`), and widens the EP reads the write
> path depends on — `_affected_factors` Batches 1/2/3, `_affected_claims`/`_live_neighbors`, the
> `_load_cache`/`_flush_cache` node + message + back-message I/O, `_read_node`/`_write_node`,
> `invalidate_messages`, the `projection/edges.py` operator fold, and the sdk promotion/partial-absorb
> joins, so a newly-writable endpoint is not EP-inert in the writing process (the operator/direct edge
> is visible to the EP run and to `dream`'s affected-set traversal). **Not claimed, and deliberately
> so: the EP DIRTY MARKER is graph-persisted for `:Point` writers only.** `_mark_dirty`'s persist and
> zombie-classification queries, `_hydrate_dirty_roots`, `_sweep_dirty_roots` and
> `_prune_nonexistent_dirty_roots` all anchor `MATCH (n:Point)`, so a write whose endpoints are ALL
> non-`:Point` is EP-inert across the request/process boundary — and the write-triggered
> `dream(mode='local')` then returns an empty affected set. That is the residual split to **#7927**
> (the `:Point`-only epistemic surfaces outside the EP engine, which already names
> `analyze._bfs_select_operators`), extended there with the dirty-lifecycle sites, and it is **pinned,
> not silenced**, by `tests/test_7902_write_path_label_widening.py::test_pinned_gap__*`. Direct edges
> therefore connect any two plain epistemic nodes — **not** Point→Point only. #7853's branch converges
> on the same shared declaration.
>
> **#7925 then closed the INGEST half of that widening** (the boundary #7926 recorded rather than
> absorbed): `sdk._check_endpoints` (Phase 1) and `_check_endpoint_race` (Phase 2) still gated the
> DIRECT route on plain `:Point`, so `ingest()` refused a `(Subject)-[:IMPL]->(Object)` connection
> that `create_direct_edge` itself wrote — the validator was stricter than the primitive it calls.
> Both phases now read ONE declaration, `sdk._INGEST_ENDPOINT_LABELS` (≡ `EPISTEMIC_LABELS`), the
> same set the two write primitives accept; the `:Source` / operator-Point / not-addressed-by-`id`
> exclusions stand, and the endpoint-KIND gate (#8499) continues to choose the STRUCTURE
> (a Point-bearing pair is reified; Object↔Object and the other non-Point pairs keep the direct edge).
>
> ⚠️ **Three exclusions were WIDENED, not held** (measured against the merge-base `c7ae5c9ed`,
> which predates #8864/#8902 — the merge-key change below, so the holder rows are read against
> their post-#8864 meaning; fail-closed over-rejection where noted, and pinned by
> `tests/test_ingest_event_endpoint.py`):
>
> - an operator-shaped **entity item** is refused in both spellings — the top-level `is_operator`
>   AND `props: {is_operator: …}`, which the one-level read used to accept (no behaviour change in
>   the engine: the `props` spelling reaches the same `SET n += $extra` write);
> - an operator-shaped **merge target** is refused, and so is an operator-shaped same-name item
>   whose extra-props write taints it. The target is the node the WRITE lands on, not "any
>   holder": one carrier ⇒ the name merge lands on it (`ON MATCH`), while a name with 2+
>   carriers folds on the record's own `id` (#8864/#8902), so the item lands on the carrier that
>   already holds the deterministic `(label, name)` id, and MINTS its own node when none does —
>   the case where no holder axis applies. Within one bundle only the FIRST item naming a
>   `(label, name)` can mint, because every entity item writes that same deterministic id: a
>   later same-name item lands on it, so its declared status is dropped. A name-keyed merge also
>   ADOPTS the node that exists (rewriting its stored id to the deterministic one), so a bundle
>   ref and an external ref to that node's PRE-write id are the same endpoint, and a connection
>   endpoint still naming that pre-write id is refused in Phase 1 rather than dying in step 4;
> - the `gated` row-9 status read and Phase 2's draft default now read at the WRITE DOOR's fold
>   depth, so a double-wrapped `props: {props: {status: …}}` is refused / kept draft respectively —
>   previously the point could be born LIVE under `gated` (a fail-open, not an over-rejection).
>
> The operator-shaped exclusion is an **ingest admission rule, not a `create_operator` contract**:
> that primitive accepts an operator-shaped endpoint, and `weights.py` prices operator-targeting
> edges. `ingest()` refuses it because a bundle is a whole-graph write whose failure mode is a
> partial mutation. On the DIRECT route the same exclusion is the primitive's own contract
> (`create_direct_edge` refuses an operator endpoint outright), so nothing there is a candidate
> to reopen.
>
> **Changelog v3.22 (2026-10-10 — issue #2767 — `milestone` is a core EVENT kind, not an Object kind):**
> §5's event-kind vocabulary did not list `milestone`, while the `project-management` pack declared it
> as an `objectKind` and the miner emitted `event_kind="milestone"` — one bare name on three axes,
> which made a second pack declaring it fail the cross-pack `nearMisses` check. Owner ruling (on
> #2767, 2026-10-09): **use core → event** — a milestone is *reached* at a moment. §5 now lists
> `milestone` among the core event kinds, and `project-management` no longer declares it. **No new
> vocabulary:** the packs had the category wrong and core already dissolved it.
>
> **Changelog v3.22 (2026-10-09 — issue #7856 — a goal's achievement state is a separate axis):**
>
> - New field `goalState` (§4.1 Point props, §4.3 Object props): a goal's
>   **achievement** state, a CLOSED vocabulary (`met`, `active`, `blocked`,
>   `unmet`, `superseded`, `abandoned`, `guideline`) validated at the SDK write
>   boundary (`GOAL_STATE_VALUES`, `_sanitize_props`). It is **separate from
>   `status`** — the draft/live claim lifecycle; `status` says whether a node is
>   promoted for use, `goalState` says whether the goal is met. `None` clears.
> - A `goal`-kind **Point** created without an explicit status is now **born
>   live** (§5): a draft Point that is only ever a TARGET is EP-inert
>   (`create_operator` promotes only the SOURCE, #131) while `converged=True` is
>   still reported, so a goal that can never influence a verdict is no longer
>   creatable by default. The canonical representation of a goal remains an
>   **Object** (born live already); the born-live rule is the legacy-Point-leg
>   safety net. Under `promotion_policy='gated'` ingest still writes every
>   point — goal included — as `draft`.
>
> **Changelog v3.25 (2026-10-12 — issue #7852 — the four-value direction STORAGE vocabulary lands):**
> The Layer-1 direction is now stored in the model's four values — `->` (source→target),
> `<-` (target→source), `<->` (both ways), `-` (none: the connection exists and **no confidence
> transfers**) — on both write paths (`create_operator` → the operator node, `create_direct_edge` /
> the ingest contract → the edge) and interpreted by EP, the analyze BFS and the dedup lookup.
> The two legacy tokens remain **accepted and readable** (`unidirectional` ≡ `->`, `bidirectional` ≡
> `<->`); storage is **verbatim**, so **no data migration** is needed — existing rows keep the token
> they were written with and every reader normalizes through the shared `live.direction_flows`.
> The `semantics` chain now records the meaning's **model** direction directly, including `-` for the
> neutral meanings (`related`, `transacts`): previously the runtime could not store `-`, so those
> fell through to the mutual default and would have propagated confidence along an edge the model
> declares belief-free. `direction_basis` is unchanged (`explicit` / `semantics` / `default`), and
> the emission and payload examples below that still spell the legacy pair remain valid.
>
> **Changelog v3.24 (2026-10-12 — issue #7852 W1 — the Layer-2 `semantics` reaches the write path):**
> `create_operator` (SDK) and `tortoise_create_operator` (MCP) accept an additive, defaulted
> `semantics` input — the closed Layer-2 vocabulary. A declared meaning RESOLVES the direction (a
> one-way meaning records `unidirectional`, a mutual one `bidirectional`); the basis the resolution
> used is recorded on the operator as `direction_basis` — `explicit` (the caller stated a non-default
> direction), `semantics` (the declared Layer-2 meaning resolved it — the token names the LAYER that
> produced the value, never the Layer-3 `label` predicate, which is a different input), or `default`
> (nothing declared). The endpoint-kind step is a
> separate scoped change. An out-of-vocabulary word warns and the write proceeds — never refuses —
> and a retired word resolves to its canonical replacement. This is the chain step that had no way
> to run, so the direction no longer always falls through to the per-op_type default. The four-value
> **storage** vocabulary was unchanged at this step (the model's `-` mapped to no runtime value and
> `<-` was deliberately left unmapped rather than recorded as the opposite) — the migration landed in
> **v3.25** above.
>
> (The `:Session` capture-container declaration originally sat in this block; a new model section is
> a model change, so §4.8 moved to its own **v3.29** block above and this header is restored to the
> one issue that actually landed here.)
>
> **Changelog v3.22 (2026-10-11 — issue #7852 — the Layer-2 semantics vocabulary is closed, and validated):**
> §8 now states the semantics vocabulary as **the** canonical table — one list, with each value's
> recorded direction and its meaning — and the registry checks a pack's `relations[].semantics`
> against it, warning with the valid alternatives when a value is out of vocabulary, retired or
> missing (warn-not-block, W2). A relation declaration may carry **`nearMisses`**, mirroring
> `kindDefs`, resolved across loaded packs. The both-ways names state their direction
> (`mutuallySupportive`, `mutuallyExclusive`); the one-way names keep theirs (`addresses`, `supports`,
> `refutes`).
>
> **Changelog v3.21 (2026-10-09 — issues #7865 + #7813 + #7852 — direction is recorded, and comes from semantics):**
> §8 and §3.1 stated a blanket `default bidirectional` while the same document recorded a per-path
> policy and the code implemented a per-op_type canonicalization. **One statement replaces all three:**
> direction is **recorded on the write** — `->`, `<-`, `<->`, `-` — and **comes from the connection's
> semantics**. `-` means the connection exists and no confidence transfers along it. §8 now states
> **three** layers (mechanism + direction / semantics / predicate + tags). The §8 semantics table's
> direction column now states the recorded four values instead of a per-row default, and `produces`
> reads "the source created" — the event→artifact shape is the encouraged pattern, not a restriction.
>
> ✅ **Transition state RESOLVED (v3.25).** The four-value vocabulary is now the stored one:
> `create_operator`, `create_direct_edge`, the ingest contract and EP all accept and interpret
> `->` / `<-` / `<->` / `-`, with the two legacy tokens (`bidirectional` / `unidirectional`) still
> accepted and readable as aliases (`bidirectional` ≡ `<->`, `unidirectional` ≡ `->`), normalized at
> every read by `tortoise/live.py::direction_flows`. **No data migration was required:** storage is
> verbatim, so a row written before the migration keeps its token and is read correctly. **The write
> path reads the semantics and records the meaning's model direction verbatim** (basis in
> `direction_basis`). Payload examples below that still spell the legacy pair remain valid input —
> they are aliases, not a second vocabulary.
>
> **Changelog v3.20 (2026-09-29 — issue #5566 — the EP affected-set traversal is factor-bearing-only):**
>
> - `TortoiseEP._affected_claims` / `_live_neighbors` admitted a claim through **any**
>   edge onto an operator, so a structural predicate (`related`, `aboutSubject`,
>   `memberOf`, …) — or a reverse-only `IMPL` (the mitigation back-link
>   `(m)-[:IMPL]->(op)`) — pulled a factorless node into the run, where
>   `_update_claim_posterior` recomputed it as `Beta(1,1)` and **discarded its prior**.
>   The operator-mediated hops are now typed **and directed**
>   (`(n)<-[:IMPL|NAND]-(op)-[:IMPL|NAND]->(m)`): only operator **inputs** are admitted,
>   which are the relations `_affected_factors` turns into factors. **One residual
>   remains, and it is not reached through `related`:** a factor whose participating
>   inputs drop below two no-ops, so an *admitted* input can still be recomputed as
>   `Beta(1,1)` (`ep.py`, the KNOWN EXCEPTION noted at the affected-factor walk). The
>   §3.9 and §8 status notes are updated accordingly; `related` is now weight-free in
>   fact, not only by the #5025 decision.
> - `mitigate_operator`'s **idempotent** update branch now dirties the operator
>   (`_mark_dirty([mid, id])`), exactly as its CREATE branch does. With the hop now
>   factor-filtered, a mitigation-only dirty root no longer reaches the operator's
>   factor, so a re-mitigation whose strength changed would leave downstream
>   confidence unmoved on any graph whose `ep_dirty` flags are not journaled
>   (#5166). The root cause is closed in `_reverse_bfs_neighbors` itself: it matches
>   `(op)-[:IMPL|NAND]->(p)` **and** `(p)-[:IMPL|NAND]->(op)`, so a mitigation — the
>   SOURCE of `(mit)-[:IMPL]->(op)` — finds its operator, and ANY writer of a
>   strength reaches the operator's factor, not only the caller patched above.
>
> **Changelog v3.19 (2026-09-28, issue #3985 — a falsey-but-ORDERABLE stored `validFrom` is a real window start):**
>
> - §4.7 (`validTo`): the resolution branch and the read path now agree for every successor
>   start both can *order*. `_supersede_window_end` resolved with a truthiness test while
>   `_covers` gates on presence, so a stored `validFrom = 0` was a real epoch-0 start to the
>   read path (`[epoch 0, ∞)`) but was treated as undated by the write path, which stamped the
>   predecessor's `validTo` at the successor's `createdAt` — inside that window, so the
>   predecessor and the successor both covered and `restore_point_at` returned a two-candidate
>   `ambiguous` instead of the successor. The resolution now takes a present, **orderable**
>   start (`is not None` and `_created_sort_key(...)[0] == 0`), so `0`/`0.0` becomes the
>   predecessor's `validTo`; where it precedes the predecessor's own start the existing #4021
>   guard refuses the write rather than persisting an inverted window.
> - §4.7 (`validTo`): **narrows, and does not resolve,** the v3.13 sentence below. The
>   falsey-**and-unorderable** `""` still falls through to `createdAt`, so `write != read` for
>   `""` remains. Which of the two is intended — an OPEN window start or an ABSENT one — is an
>   owner decision over temporal semantics, not a predicate alignment, so it is left open and
>   tracked as **#6140** (deliberately not decided here; it is NOT #3982, which ruled on
>   date-only parsing — a different question).
> - Supersedes the v3.13 no-kwarg sentence's scope from "the falsey case" to "the
>   falsey-and-unorderable case", and supersedes v3.13's contrast that the **presence**
>   predicate is "the read path's … not the resolution branch's truthiness": the resolution
>   branch now ALSO gates on presence (plus the #5360 orderability conjunct), so that contrast no
>   longer holds.
>   This entry records only what the code now does. (The two v3.18 entries further below
>   are a pre-existing duplicated label — filed as **#7214**, not renumbered here.)
>
> **Changelog v3.18 (2026-09-27 — issue #5025, owner ruling — `related` is the neutral association link and carries no EP):**
>
> - §3.9: **`related` is defined.** It means *connected, and nothing more* — no
>   direction (symmetric), no chaining (non-transitive), and never a substitute for a
>   hierarchy relation (`hasPart` / `memberOf` / `reportsTo`). The shape follows the
>   SKOS Reference's associative link.
> - **It carries no epistemic weight, by decision.** Nothing that computes confidence
>   may traverse it — neither the support path (`IMPL`/`NAND`) nor the
>   source-credibility prior path (`_apply_source_inheritance`, which reads
>   `extractedFrom`). **Relevance is not expressed by this link; it is expressed on
>   the operator** (`IMPL`/`NAND`). The AIF scheme node (`RA-node`) is cited only as an
>   **illustrative** analogy for relevance-on-the-operator, and operator-less direct
>   `IMPL`/`NAND` edges also exist (§8).
> - Enforcement status is stated rather than implied: the three meanings are
>   **declarations** (`_VALID_EDGE_PREDICATES` is a flat membership set). What *is*
>   enforced is the **durability** half — `related` sits in **neither**
>   `DERIVABLE_STRUCTURAL_RELS` nor `SUPERSEDE_STRUCTURAL_RELS`, pinned by
>   `test_related_is_neutral_by_construction` (#5547). Wiring a producer requires
>   revisiting both sets first (#2489).
> - Precedent recorded: SKOS `skos:related` (W3C); ConceptNet `/r/RelatedTo` ("the
>   most general relation … can't determine what that relationship is"); and AIF's
>   scheme node, cited as an **illustrative** analogy for relevance-on-the-operator
>   only.
> - Known defect on the designated operator route, **as of 2026-09-27**: **#5566** — a
>   non-logical edge onto an operator pulled the node into the affected set, where its
>   prior was discarded (`Beta(1,1)`). The EP traversal was then unfiltered on relation,
>   so `related` was not yet weight-free in fact. **The traversal half of this is now
>   CLOSED** — the affected-set traversal is `IMPL`/`NAND`-filtered and direction-checked
>   as of v3.20 above, so a `related` edge can no longer pull a node in. The
>   belief-model half remains **owner-reserved** (DECISION-LEDGER §22), which is why
>   #5566 stays open.
>
> **Changelog v3.18 (2026-09-25, issue #4021 — the inverted predecessor window is refused):**
>
> - §4.7/§4.1 (`validTo`): the supersession end is now checked against the
>   **predecessor's own `validFrom`** before it is stamped. The resolution order is
>   unchanged (`valid_from` kwarg → successor `validFrom` → successor `createdAt`
>   → `now`), but a resolved end that sorts **strictly before** the predecessor's
>   start — same measure (`_created_sort_key`) and same `is not None` presence
>   predicate `restore_point_at`'s `_covers` uses — is refused with `ValueError`
>   **before any mutation**: no event journaled, no half-write. Equality is
>   well-formed (a zero-length predecessor window) and still supersedes. The
>   comparison is made only where **both** instants are orderable; a bound that is
>   present but **unorderable** names no instant and is not compared.
> - Why: **nothing on the write path read the predecessor's start**, so a
>   **backdated successor** persisted `validTo < validFrom`. An inverted window
>   satisfies `_covers` for **no** query instant, so `restore_point_at` reported
>   honest absence for the old fact at every instant — a silent, permanent
>   unreachability, with no error anywhere. This covers the **inverted**
>   direction only; the orderability residual (#5360) is closed on the WRITE path
>   by normalising an unorderable resolved bound to an orderable instant rather
>   than persisting it. The comparison still has TWO unorderable operands it
>   must not refuse on, and the supersession guard touches both: an unorderable
>   NUMERIC `valid_from` kwarg (the resolved end — the one route #5360 leaves
>   unnormalised, deliberately, because the guard measures the RESOLVED value
>   rather than the raw object; note this still leaves the predecessor's window
>   end unorderable, so `_covers` cannot order it — a residual #5360 does NOT
>   close), and an unparseable predecessor `validFrom`
>   (the comparison's other operand). Neither is compared — refusing on an
>   ordering-fallback artefact would make a point carrying a non-ISO start
>   impossible to supersede.
> - The MCP dry-run preview (`tortoise_supersede(dry_run=True)`) calls the same
>   helper, so it refuses a write the writer would refuse — a dry run that reports
>   a clean blast radius for a write that then raises is the fail-open direction
>   of the same defect.
> - **OVERRIDES:** the "be liberal / normalise the interval" default at the
>   supersession write — a silently inverted window (`validTo < validFrom`) is
>   unsatisfiable by every query instant while reporting honest absence, so the
>   write fails closed rather than persisting a corrupt interval. This is also a
>   deliberate narrowing of `supersede_point`'s **additive-only** promise ("no
>   behavior change for callers that don't pass the kwarg"): the input class whose
>   behaviour changes is the one that used to corrupt silently.
>
> **Changelog v3.17 (2026-09-24 — issue #4937, the F1 ruling recorded on #2552 — MITIGATES retires from the operator menu):**
>
> - §2: the operator KINDS are `IMPL`/`NAND` (+ declared labels). `MITIGATES`
>   leaves the generic operator menu: a **mitigation** is not a peer operator
>   but a Point that attaches to the IMPL/NAND operator bridge it damps
>   (`(op {is_operator:true})-[:mitigated_by]->(m)`, strength ∈ [0.10, 0.50],
>   `w_eff = w × (1 − strength)`, §3.9). An operator kind cannot express that —
>   it carries no strength and no bridge.
> - Code alignment: `sdk.create_operator` refuses `op_type="MITIGATES"` with an
>   explicit error naming `mitigate_operator` (the correct path), and the
>   generic label menu is `IMPL`/`NAND` (+ pack-declared relations). The
>   extractor/commit payload spelling `MITIGATES` (target + strength) is the
>   WIRE name of a bridge-attack record and is routed to the mitigation path
>   (`commit_ops.apply_payload_operators` → `mitigate_operator`), never to
>   `create_operator`.
> - Existing data: the retired entry was the built-in operator **label**
>   exemption (`label not in ("IMPL", "NAND", "MITIGATES")`); the `op_type`
>   allowlist already excluded `MITIGATES`, so `create_operator` with
>   `op_type="MITIGATES"` never created a node. Point-in-time measurement
>   (2026-09-24, the 9 reachable fleet FalkorDB stores / 5,131 graphs):
>   `MATCH (o:Point {is_operator:true}) WHERE o.op_type='MITIGATES'` → **0
>   hits** (the live mechanism is the `mitigated_by` edges the same sweep
>   found). The label-exemption removal is **warning-only** (warn-not-block):
>   an operator carrying `label='MITIGATES'` stays legal and readable, and the
>   change only stops a NEW one from being silently exempted. So **no migration
>   runs and no data is dropped** — the refusal is loud at the write boundary,
>   and the legacy PAYLOAD spelling keeps working.
> - **OVERRIDES:** the "be liberal in what you accept" default at the write
>   boundary — a second spelling of mitigation would make "why is this weaker?"
>   answerable two ways with the strength present in only one; the menu keeps a
>   single spelling and the mitigation attaches to the bridge it damps.
>
> **Changelog v3.16 (2026-09-24 — issues #2726 + #2727, meeting source kinds +
> object-kind alignment; original branch change dated 2026-09-09, renumbered from
> v3.11 on merge because main had taken v3.11 for #3263):**
>
> - §4.6/§5: sourceKind vocabulary gains `meeting_transcript` (raw, first-hand)
>   and `meeting_minutes` (structured, mediated) — the two meeting-capture
>   kinds. Both register **NEUTRAL** in `SOURCE_KIND_DEFAULTS` (precedent:
>   operational captures are neutral, like the connector kinds). The #398 tier
>   question — is a raw transcript first-hand evidence and are minutes a
>   mediated summary? — is deliberately **left open**: NEUTRAL changes no EP
>   inheritance and stays reversible via `register_source_kind_default`.
> - §4.6/§5: pack-declared `extraction.sourceTypes` now validate against the
>   registered source-kind registry (`KNOWN_SOURCE_TYPES ∪
>   SOURCE_KIND_DEFAULTS`, escape hatch unioned at the check) rather than a
>   narrower hardcoded list — a kind registered at runtime is a first-class
>   sourceKind for packs too (#2726). The operational captures `agentSession`
>   and `meeting_summary` moved from `file_indexer`'s import-time block into
>   `SOURCE_KIND_DEFAULTS`, so registry membership — and therefore pack
>   validation — no longer depends on `file_indexer`'s import order.
> - §1/§4.3/§5/§6 (code alignment + subclassability): `CANONICAL_OBJECT_KINDS`
>   (`tortoise/pack_registry.py`) gains `tag` + the commitment-state family
>   (`strategy`, `plan`, `goal`, `target`) so the runtime set, the extractor's
>   `CORE_OBJECT_KEYS`, §5, §6's core-subclass table, §1's parenthetical and
>   §4.3's `objectKind` row name the same object kinds; packs may now declare
>   `subclassOf` against any of them (#2727).
>   **Count correction on merge:** §5/§6/§1/§4.3 name **16** object kinds —
>   v3.15 (#5013) retired `document` from the Object vocabulary (a document is a
>   `:Source`, §4.4) and the resolutions below follow it. The code lag this note
>   used to record is now closed: the runtime constant (`CANONICAL_OBJECT_KINDS`
>   — 16 members) and `extractor_v2.CORE_OBJECT_KEYS` no longer carry
>   `document` (removed in `e5de6373c`, PR #5236 — the D10 code half tracked by
>   #5026).
>   The `subclassOf` PascalCase shape check is scoped to allow canonical
>   lowercase object kinds — superseding the R6 §1.1 "parent must be a core
>   PascalCase kind" contract (the `packs/agent-ops` `nearMisses: [standard]`
>   workaround remains valid and need not migrate).
>   **Migration note:** the five newly canonical names can no longer be declared
>   as bare `ontology.objectKinds` (the canonical-collision guard now fires);
>   packs that declared them must express the relationship via `subclassOf`
>   instead.
> - §5 note: the legacy Phase-2 entity stage (`tortoise/extractor.py
>   _OBJECT_KIND_VOCAB`) intentionally keeps a narrower 13-kind vocab —
>   `strategy`/`plan`/`goal`/`target` are extraction surfaces of
>   `extractor_v2.CORE_OBJECT_KEYS` (state-centric), so the legacy intersection
>   filter drops them from the prompt vocab and `_normalize_object_kind`
>   collapses any that still appear to `other` by design. Note added under §5's
>   Object Kind Vocabulary; pinned in tests.
>
> **Changelog v3.15 (2026-09-24, issue #5013 — a document is a `:Source`; sources are versioned):**
>
> - §4.4: **rewritten.** A document is a `:Source`, not an entity. The
>   `:Document` label and `objectKind: document` are retired; **`content` leaves
>   the graph**; **`doc_status` is retired** — liveness is a read of the entities
>   extracted from the source, never a stored field. **Supersedes the "Document is
>   an Object" model** in force from v3.0 through `PR #129`.
> - §4.4 + §6: the two classification axes are **kept and kept separate** —
>   `sourceKind` (what kind of source) and `documentKind` (what genre of
>   document). Neither absorbs the other.
> - §3.2: `aboutDocument` is **kept**; its target label moves to `:Source` and
>   its replay key becomes `url`. It is **not** merged into `aboutSource` — the
>   two differ in whether `rebuild_all` pass-2b resurrects them (`#2489`).
>   `Document` is removed as an edge source from the other `about*` predicates,
>   and one example that used `uses`→`:Document` is corrected to `:Object`
>   (`uses` is declared `Event → Object`). **(Edge rows: §3.2; the summary line:
>   §3.9.)**
> - §4.6: **source versioning stated.** Identity is `url`; `contentHash`
>   identifies a **version**; raw content is **append-only**; **extraction is
>   version-scoped**; `validFrom`/`validTo`/`expiredAt` become the version's
>   window; **supersession is additive and deferred until the replacement
>   exists**. `contentHash`'s role changes from an *idempotency anchor* to a
>   **version anchor**.
> - §4.7: the Document column is removed from both matrices; its temporal slots
>   are the Source column's, which gains the version window.
> - §6/§12: `document` is removed from the Object core-subclass list; the
>   `documentKind` vocabulary is the **genre axis** over `sourceKind: document`.
> - **Precedence:** `ONTOLOGY.md` (canonical) > `#5013` (the decision) >
>   `docs/architecture/STORAGE-ARCHITECTURE.md` +
>   `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` (PR #5016) > the
>   implementing issue.
> - **Implementation status:** `#5026` (the label migration) **landed** — PR
>   #5127 merged 2026-09-25 (`294d5847e`): the `:Document` label is retired, and
>   the legacy spelling survives only as a deprecated alias whose writes route to
>   `:Source` keyed `url`. `#5024` **landed** — a content-changing re-fetch now
>   journals a `SourceVersioned` record naming the superseded `contentHash`, a
>   no-op re-fetch journals nothing, and a replay reproduces the node (the
>   per-field split is in `docs/durability-posture.md` → R2). ~~Still open: the
>   version **window** `validFrom`/`validTo`/`expiredAt`, which no writer sets
>   (**#3644**)~~ — **since v3.23 the `:Source` window half is implemented**
>   (`validFrom` on the node; `validFrom` + `previousValidTo` on the
>   `SourceVersioned` record), and what stays open is the node's
>   `validTo`/`expiredAt` (**#3590** S2, the removal-field decision); and
>   `#5038` (the version model). `#5121` **landed** — PR #8528 corrected the MCP
>   surface descriptions that still spoke of a `:Document` graph node (of the
>   advertised tools, `tortoise_index_files` and `tortoise_create_document`;
>   `tortoise_taxonomy`/`tortoise_ingest_corpus`/`tortoise_get` are retired names
>   served only by the warning shim) and re-cut the `config/surface-manifest.yml`
>   baseline — no tool was added, removed or renamed. **The D10 code half's own
>   residuals are named and open:** **`#5082` closed** — the document `:Source` now
>   writes `sourceKind='document'` (CREATE-only, #398), the field §4.6 marks REQUIRED;
>   the remaining caveat is the read-path-conditional `contentHash`, recorded at §4.6;
>   and `#5140` (the ten
>   residuals outside the declared B1–B6 threat surface, including three reproduced
>   P1s). **Landed:** `#5126` — `graph-scripts/backfill_embeddings.py` now targets
>   `:Source` (PR `#8420`).
>
> **Changelog v3.14 (2026-09-20, issue #4369 — the "claim" gloss is declared):**
>
> - §5: **"claim"** is declared as the sanctioned user-facing **gloss** for a logic-layer
>   Point (the asserted belief — the logic layer's canonical kind is `pointKind: statement`;
>   the legacy write kinds remain valid Point kinds). It is **not a distinct kind**: no `claim` type and no
>   `claim` pointKind, and no canonical node write value — the SDK's kind vocabulary is
>   deliberately open (an unrecognized kind is accepted with a warning), so this states the
>   vocabulary rule, not an enforced write rejection; word-carrying identifiers (the EP slot
>   `claim_id`, the pack-manifest `storeAs: claim` bucket) are untouched. The canonical
>   machine vocabulary is unchanged (Point / `statement`) — the declaration makes the
>   document's belief-node usages of the noun resolve against a declared term instead of an
>   accretion. **No rename** (decision option A): the ~775 `claim`/`claims` occurrences in
>   `tortoise/**/*.py` (measured 2026-09-20; identifiers and prose alike),
>   `tortoise/weights.py`'s single-source docstring and the shipped skill keep the word.
>
> **Changelog v3.13 (2026-09-18, issue #3980 — the `valid_from` kwarg precondition):**
>
> - §4.7/§4.1 (`validTo`): the resolution order is unchanged
>   (`valid_from` kwarg → successor `validFrom` → successor `createdAt` → `now`),
>   but the kwarg is now a **claim** rather than an unconditional input. When the
>   successor carries a stored `validFrom` the two must be **parseable**
>   timestamps naming the **same instant** (compared by instant via
>   `_created_sort_key` — the measure `restore_point_at`'s `_covers` uses), else
>   `supersede_point` raises `ValueError` **before any mutation**. The refusal
>   exists because a disagreeing kwarg silently broke contiguity in either
>   direction: an EARLIER kwarg left a **gap** (a query instant covered by
>   neither window, so `restore_point_at` reports honest absence for a period
>   that *was* covered) and a LATER kwarg left an **overlap** (two covering
>   candidates ⇒ every instant inside it reads `ambiguous`). The kwarg is the
>   **sole** source when the successor carries no stored `validFrom` (an
>   undated successor) **unless it names no instant** — an unparseable STRING
>   kwarg falls through to `createdAt`, then `now` (#5360). The comparison keys the
>   value the write **persists**, `str(valid_from)`, because a numeric epoch
>   parses as an instant but its `str()` does not — a distinction that decides
>   whether the predecessor's `validTo` is orderable by `_covers` at all.
>   A date-only value anchors to **UTC** midnight, so the guard's verdict for a
>   date-only-vs-offset-aware pair no longer follows the reader's host — the
>   host-dependence was the defect, not the contract (issue **#3982**, which owns
>   the date-only semantics decision; now resolved, with a zone-less date-TIME
>   still read locally). The guard's
>   **presence** predicate is also the read path's (`stored_vf is not None`), not
>   the resolution branch's truthiness: a falsey-but-present stored `validFrom`
>   is a real window start to `_covers` (`0` keys as the parseable epoch-0
>   instant), so a kwarg against it is refused rather than written unchecked.
>   The **no-kwarg** falsey case keeps the pre-existing truthiness fallback —
>   that residual read/write divergence is tracked in **#3985**. Deliberate
>   departure from the #1538 plan's unconditional "explicit `valid_from` kwarg
>   wins" pin — recorded on #1538.
>
> **Changelog v3.12 (2026-09-15, issue #3642 — declared temporal model):**
>
> - §4.7: rewritten as the canonical statement of the temporal model — two
>   orthogonal axes (valid time `validFrom`/`validTo`; transaction time
>   `createdAt`/`expiredAt`), supersession as a **third, separate fact**
>   (`supersededAt` on Object; `status='superseded'` on Point), one canonical
>   name per slot, Event's `startedAt`/`endedAt` declared a named alias of the
>   valid-time pair, and a per-type presence matrix. **Fixes the contradiction
>   with §4.5:** the old map's `createdAt` row mapped Event's record-creation to
>   `startedAt`; Event's transaction-time start is `capturedAt`.
> - §4.7 (correction): Point supersession is `status='superseded'` — the
>   `outdated` flag + `CORRECTS` edge are shared with `invalidate_point` and do
>   not distinguish the two. §4.7's `validFrom` start is ⚠️ — populated by the
>   date-carrying write paths only (hosted commit `when`; mining W-4 session
>   date); an **undated** session stamps no `validFrom` at all, so
>   **absent ⇒ open/unbounded start** (#3654 — the ingest wall clock is never
>   borrowed as a valid-time start), and `when` documented as the occurrence-date
>   input that fills `validFrom` on the commit path, not a second slot.
> - §4.7 (correction): the Document column carries explicit per-cell markers,
>   not "inherits Object" — Document inheritance of the Object column is
>   **conceptual** (`objectKind: document`); Documents carry `:Document` and not
>   `:Object`, so the Object-labelled supersession fold never reaches them and
>   supersession is unreachable for a Document.
> - §4.7/§4.1/§12 (correction): `validTo` is the successor's `validFrom` **only
>   when the successor carries one** — `supersede_point` falls back to the
>   successor's `createdAt`, then `now` (monotone, never a gap), so exact
>   contiguity holds only for a dated successor and an undated one **overlaps**
>   (the companion to the open-start ⚠️ above). The §4.7 start row no longer
>   claims `create_point`'s CREATE map writes only `createdAt`/`updatedAt` — the
>   base map seeds no `validFrom`; caller props (including `validFrom`) are
>   appended.
> - §4.1: `expiredAt` description split — transaction-time expiry (the record
>   stopped being current) is not "when a supersession/withdrawal terminated the
>   record"; supersession is a separate fact (`status='superseded'`) and both
>   can apply. Point `validFrom` split from `validTo` with `prov:generatedAtTime`
>   / `prov:invalidatedAtTime` and ⚠️ partial-coverage marking.
> - §4.2/§4.3/§4.6: `validFrom`/`validTo` + `expiredAt` declared (❌ — the model
>   is declared, not built; implementation tracked separately).
> - §4.4: Document declared to inherit the Object temporal fields conceptually
>   (no duplicate rows; no `:Object` label — see §4.7 †).
> - §4.5: `capturedAt` moved from planned(❌) to the declared transaction-time
>   start (⚠️ — written by the hosted capture/commit + session-index paths, not
>   yet on every Event write path); cross-references §4.7.
> - §12: temporal standards mapping added — `prov:generatedAtTime`/
>   `prov:invalidatedAtTime`, OWL-Time, and the Graphiti/Zep bi-temporal lineage.
> - §10.5/§3.1 (correction, sweep): the §10.5 design-decisions row still
>   described `outdated` as set only by supersession and gave a four-value
>   status list; the §3.1 `CORRECTS` row likewise called the shared edge a
>   supersession. Corrected to the explicit terminalizing writes
>   (`supersede_point` / `invalidate_point`) and §5's six-value vocabulary,
>   with `CORRECTS` framed as the shared replacement marker (§4.7 ‡).
>
> **Changelog v3.11 (2026-09-12, issue #3263 — provenance written by construction):**
>
> - §3.3: `extractedFrom` cardinality amended **`many→1` → `many→many`**. The
>   edge is written per source; a claim extracted from several sessions carries
>   one `extractedFrom` edge each, with no upper bound. (`create_point` now
>   infers `session:<session_id>` from the write context and accepts a sequence
>   of refs.)
> - §4.1 note: the scalar `Point.extractedFrom` node property is a query
>   convenience only — the **edges are authoritative**. It holds a string when a
>   single string is passed (including the inference path) and an **array**
>   whenever a sequence is passed, *even a one-element one*. Arrays are not
>   equality-matchable (`WHERE n.extractedFrom = '<url>'` will not hit them), so
>   exact-match callers must pass the scalar or traverse the edge.
> - §4.6: `session:<id>` Sources are minted with `sourceKind: agentSession`
>   (already the registered value — this removes the need for the in-place
>   upgrade `_materialize_session_source` performed). NOTE: `agentSession` is
>   still registered tier-neutral, so `inherited-from-source` calibration is
>   NOT yet satisfiable; assigning the tier is a calibration-policy decision,
>   tracked separately.
>
> **Changelog v3.10 (2026-09-05, #2238 dirty-hub salvage landing — Problem family):**
>
> - §5: registers core object kind `Problem` (deviation between actual and desired
>   state) + Problem-family note — dev `bug`/`incident` and product-strategy
>   `customerProblem` subclass it; `risk` is its potential/claim form (dev point kind).
> - §11.x (*Object Confidence — Compositional Projection*, a **proposed** section that
>   has not been added to this document): **proposed, not yet implemented** (the
>   compositional read path ships separately).
> - §1/§4.3/§6: core-subclass enumerations extended with `Problem`.
>
> **Changelog v3.9 (2026-09-02, issue #2101 / epic #2080 — §5 response-contract vocabulary, W4 why-layer DM-12):**
>
> - §5: new Response-Contract Vocabulary section — additive response-contract
>   labels (dig_deeper kinds/labels, why-block sections, conflict severity,
>   degraded_reason), NOT new entity kinds. Registered so the W4 why-block
>   assembly (tortoise/why.py) and the S6 contract test share one vocabulary
>   (S15 schema-correctness review prevents drift).
> - §5: conflict severity boundary pinned — `high` when the counter-claim's
>   persisted EP mean ≥ 0.6, else `medium` (repo-wide high-confidence bar,
>   analyze.py consensus pattern).
>
> **Changelog v3.8 (2026-08-13, issue #388 — connector Source nodes):**
>
> - §3.4: connector events (GitHub/Linear/Slack poll + webhook + entity paths)
>   now materialize Source nodes at the projection choke point (`_upsert_event`,
>   projection/entities.py) — `(Source {url})-[:references]->(Event {eventId})`
>   (+ `(Source)-[:references]->(Object {id})` on the GitHub entity path via an
>   explicit `sourceObjectId` field). Gate fires only on a registered connector
>   `sourceKind` or an explicit `sourceUrl` — never on bare `source` (mining
>   events stay excluded). `sourceKind` is set on CREATE only (#398 never-
>   overwrite contract: an existing Source's kind is authoritative on re-MERGE);
>   re-materialization does not bump `version` (idempotent re-poll).
> - §3.4/§5: `sourceKind` vocabulary gains `github_pr` (PR events — previously
>   mislabeled `github_issue`) and `linear_cycle` (cycles — previously mislabeled
>   `linear_card`); both register neutral in SOURCE_KIND_DEFAULTS (no EP
>   inheritance change).
> - §3.4: `Source.url` may be a container-scope string when no per-entity URL
>   exists (`slack:{channel}` on permalink failure, `linear:{team_key}` for
>   cycles) — deliberate non-URL fallback keying.
>
> **Changelog v3.6 (2026-08-11, epic #909 slice 3 — 13 ontology amendments, issue #948):**
> Registration only — no new design (plan §4.3, numbered exactly 13):
>
> 1. §4.5/§5: eventKind `AgentSession` (EXACT code spelling — capital A, sdk.py/session_indexer.py) + `sessionCaptured` declared an alias of the same concept — both remain valid kinds, no migration.
> 2. §4.5: `capturedAt` field (transaction time — bi-temporal capture).
> 3. §4.5: content-addressed Event ID (deterministic MERGE anchor for the agentSession Event).
> 4. §4.4: `story_arc` field registered (summary = short, story_arc = arc continuation).
> 5. §3.1: NAND direction policy — extraction-emitted NANDs default `unidirectional`; `bidirectional` only for explicit mutual restatement (SDK creation default stays bidirectional, #807).
> 6. §4.6/§5: `provenance_spans` Source property + `sourceKind: agentSession` value (credibility-tier inheritance keyed on sourceKind, #398).
> 7. §3.9: `mitigated_by` predicate registered (existing `mitigate_operator` edge — currently unregistered).
> 8. §3.4: `references` target extended — Source allowed (producer extension `link_source_to_entity`).
> 9. §5: `pointKind: event` registered (episodic turn Points — regex capture path, sdk.py:924).
> 10. §4.1: content-addressed Point ids (`pt_<sha>`) sanctioned as an id form (ULID preference retained).
> 11. §4.1: `c_cal` (calibrated confidence) + stored `quote` (≤200 chars provenance quote) registered.
> 12. §4.3: `passes_frequency_gate` registered on Object (S5 gate-result flag).
> 13. §4.1/§4.5/§4.6: `is_episodic` registered on Point/Event/Source — plus the `:Session` capture node (§4.5 note) (quota exemption discriminator; NOT on Object — plan §4.3 #13 entity set, review P2 PR #973).
>
> **Changelog v3.5 (2026-08-11, epic #898 — reification rule):**
>
> - §8: Reification rule added — an edge carries an operator only when it needs
>   mitigation (or is a Point↔Point support/contradict). Structural edges stay
>   plain and carry confidence as an edge attribute. IMPL/NAND may be direct
>   Point→Point (operator-less). Direction lives on the operator node when
>   present, else on the edge. EP note: operator-less edges read direction from
>   the edge and initialize edge messages directly.
>
> **Changelog v3.4 (2026-08-10, issue #690 — status vocabulary reconciliation):**
>
> - §5: Point status vocabulary upgraded from narrative note to canonical table
>   (six statuses: draft, live, retracted, superseded, outdated, archived).
>   Every status has a defined write path, allowed transitions, and EP semantics.
>   Parity decision: SDK + EventAPI + CLI share a single vocabulary
>   (`POINT_STATUS_VALUES` in `sdk.py`). `challenged` remains a derived
>   condition, not a stored status.
>
> **Changelog v3.2 (2026-08-07, issue #398 — Source credibility):**
>
> - §4.6/§3.4: Source `sourceKind` clarified — it is the extensible source TYPE
>   vocabulary (connectors write `github_issue`, `slack_message`, `linear_card`);
>   the T0–T4 credibility TIER is carried by `credibilityTier` (the property the
>   inheritance adapter reads). Tier-form values written into `sourceKind`
>   (`create_source(url, "T0")`) mirror to `credibilityTier` (dual-write).
> - §4.6: Source gains `reliability`/`reliabilityComponents`/`reliability_derived_at`
>   (documented derivation cache — see §11) and `sourceDate` (evidence-age clock).
> - §5: pointKind vocabulary gains `assessment` (agent source evaluations).
> - §10: recency-modulation decision log (per-field/per-sourceType decay deferred).
>
> > **Changelog v3.7 (2026-08-12, core hypothesis — state-centric model):**
>
> - §2: state-centric model block — the graph stores STATE (Objects + lifecycle
>   - derived confidence) + POINTS (the logic) + EVENTS (timeline incl.
>   decision-as-event); decisions are NOT first-class Points.
> - §5 (v3.8): extraction point kind = `statement` ONLY; `observation` removed
>   (anything can be called one) and `hypothesis` folded into confidence
>   semantics (a conjecture is a low-confidence statement) — both join the
>   legacy write kinds.
> - §5 (v3.7): Point kinds — extraction write kinds = statement/observation/hypothesis;
>   decision/vision/strategy/plan/goal/target/humanApproval/event marked LEGACY
>   (write-compat only). Object kinds gain the commitment-state family
>   (strategy/plan/goal/target). Event kinds gain `occurrence` + `turn`; the
>   decision-as-event semantics documented.
> - Pack-mapping item: product-strategy option pointKinds (useCase/userJourney/
>   jobToBeDone/valueProposition) → objectKinds.

---

## Per-field implementation status

The `Impl` column that used to sit in `ONTOLOGY.md`'s §4 field tables. It answers
*"is this field built?"* — a status question, and therefore this file's business
rather than the model's. Legend: ✅ built · ⚠️ partial · ❌ not built.

**Removed from the field tables on 2026-10-15** (owner approval tracked at #8745). The `Field`,
`Type`, `Required` and `Meaning` columns are unchanged; only the status column
moved.

### §4.1 Point

| Field | Impl |
|---|---|
| `id` | ✅ |
| `content` | ✅ |
| `pointKind` | ✅ |
| `is_operator` | ✅ |
| `op_type` | ✅ |
| `status` | ✅ |
| `confidence` | ⚠️ |
| `c_cal` | ❌ |
| `quote` | ⚠️ |
| `when` | ⚠️ |
| `authoredBy` | ✅ |
| `validFrom` | ⚠️ |
| `validTo` | ✅ |
| `expiredAt` | ✅ |
| `createdAt` / `updatedAt` | ✅ |
| `lastDreamedAt` | ✅ |
| `embedding` | ✅ |
| `speaker` | ✅ |
| `is_episodic` | ❌ |
| `goalState` | ✅ |

### §4.2 Subject

| Field | Impl |
|---|---|
| `id` | ✅ |
| `name` | ⚠️ |
| `subjectKind` | ✅ |
| `status` | ✅ |
| `supersededBy` / `supersededAt` | ✅ |
| `validFrom` / `validTo` | ❌ |
| `expiredAt` | ❌ |
| `createdAt` | ✅ |
| `updatedAt` | ❌ |

### §4.3 Object

| Field | Impl |
|---|---|
| `id` | ✅ |
| `name` | ⚠️ |
| `objectKind` | ✅ |
| `title` | ✅ |
| `status` | ✅ |
| `supersededAt` | ✅ |
| `supersededBy` | ✅ |
| `validFrom` / `validTo` | ❌ |
| `expiredAt` | ❌ |
| `createdAt` | ✅ |
| `updatedAt` | ❌ |
| `passes_frequency_gate` | ❌ |
| `goalState` | ✅ |

### §4.5 Event

| Field | Impl |
|---|---|
| `eventId` | ✅ |
| `eventKind` | ✅ |
| `format` | ✅ |
| `startedAt` / `endedAt` | ✅ |
| `capturedAt` | ⚠️ |
| `started_at` | ⚠️ |
| `subject` | ✅ |
| `object_name` / `object_type` | ✅ |
| `file_hash` | ✅ |
| `is_episodic` | ❌ |

### §4.6 Source

| Field | Impl |
|---|---|
| `url` | ✅ |
| `sourceKind` | ✅ |
| `credibilityTier` | ✅ |
| `contentHash` | ✅ |
| `title` | ⚠️ |
| `ingestedAt` | ✅ |
| `updatedAt` | ✅ |
| `validFrom` / `validTo` | ⚠️ |
| `expiredAt` | ❌ |
| `documentKind` | ⚠️ |
| `format` | ✅ |
| `externalId` | ⚠️ |
| `sourceDate` | ⚠️ |
| `provenance_spans` | ❌ |
| `is_episodic` | ❌ |
| `version` | ✅ |
| `rawState` / `rawStateAt` | ✅ |
| `topics` | ✅ |
| `summary` | ✅ |
| `sessionId` | ✅ |
| `eventId` | ✅ |
| `story_arc` | ❌ |
| `sourcePath` | ✅ |
| `needs_extraction` | ✅ |
| `_searchText` | ✅ |
| `reliability` | ⚠️ |
| `reliabilityComponents` | ⚠️ |
| `reliability_derived_at` | ⚠️ |

---

## Body status-notes removed from `ONTOLOGY.md` (2026-10-15, #8745)

Implementation-status sentences that sat inside the model's body prose. The
model rule each one qualified is kept in `ONTOLOGY.md`; only the status half
moved here.

| Where | Removed |
|---|---|
| §3.1 `CORRECTS` row | "edge disposition is **restatement-scoped (the design was `#2421`, CLOSED `COMPLETED`)** … v1 still transfers, the triage is not yet implemented and has no open tracker" |
| §3.1 supersession semantics | "(#2421 — the design is CLOSED `COMPLETED`; the per-edge triage is NOT yet implemented and has no open tracker)" |
| §3.1 deterministic policy | "*Implementation status:* `supersede_point` currently performs the legacy universal transfer … the design was #2421 (CLOSED `COMPLETED`) and it has no open tracker." |
| §3.1 direction flag | "The direct-edge and ingest paths still *source* the direction from `op_type` rather than `semantics` — a separate gap from this storage vocabulary, tracked as `#7852` (`#7813` is CLOSED `COMPLETED`)." |
| Lifecycle table, `superseded` row | "(#2421 — the design, CLOSED `COMPLETED`: semantic edges are to be triaged per-edge … that triage is not yet implemented and has no open tracker)" |
| §8 direction note | "(Stored and interpreted on every path since v3.25; the legacy `bidirectional` / `unidirectional` tokens remain accepted and readable aliases — see the *Transition state* note in the changelog.)" |
| §10.5 direction-aware EP note | "(today's shipped code reads the semantics on the operator write path … `create_direct_edge` and the ingest path still resolve by `op_type`. See the *Transition state* note — it covers the v3.25 **storage** transition; the direction-*sourcing* gap is separate and tracked as `#7852` (`#7813` is CLOSED `COMPLETED`).)" |
| §4.2 Subject `validFrom` / `validTo` | "; not yet written by `_upsert_subject`" — the *"declared §4.7, #3642"* lead-in stays in the model. |
| §4.3 Object `validFrom` / `validTo` | "; not yet written by `_upsert_object`" — the *"declared §4.7, #3642"* lead-in stays in the model. |
| §3.6 edge table, `participatesIn` row | "⚠️ spec-only — no producer yet, tracked in issue #7884" — a producer exists: the event write path auto-creates the edge from the event's `participants`, falling back to the event's `subject` when no explicit list is given (`projection/entities.py:4491`, the `#212` auto-create, CLOSED `COMPLETED`). The cited `#7884` is a merged PR about hosted lifespan warm-up, not this predicate. |
| §3.2 Session/aboutObject note, closing sentence | "`aboutSubject` has no producer here: … the capture entity model mints Objects, not Subjects." — the capture spine routes §5 Subject kinds to the `:Subject` label and binds Points with `bind_point_subjects` (`#4934`, CLOSED `COMPLETED`), which `tortoise/subject_binding.py` declares the only confidence-gated `aboutSubject` producer. |

The table lists the status-notes taken out of the model body, not every line the
split moved — the complete text of the split is the commit that made it. Each
row's *Where* names the section the quoted text was in *before* the removal.

---

## §4.7 — a validity start that names no instant (#6140, 2026-10-19)

The read path and the write path disagreed about a stored `validFrom` that names no instant. The
read path (`get_historical_knowledge`'s `_covers`) gated the start on presence alone and then
ORDERED the value, so an empty value keyed after every parseable instant and the window covered
nothing: a dated query answered `status="absent"` for a claim genuinely in force. The write path
already treated the value as absent.

Ruling (owner, 2026-10-11): *"A and C (c should be a warning)"* —

- **A** — a start that names no instant means **no start recorded**, so the window is unbounded
  below. The read path now requires ORDERABILITY before a start bounds anything, which is the same
  answer the write path gave.
- **C** — the write boundary **warns** when a caller supplies such a value, and stores it: a
  warning, never a refusal, because a refusal would make a legacy point impossible to update or
  supersede until its window was repaired.

**OVERRIDES:** the comparable agent-memory products surveyed for this decision (Zep/Graphiti, Mem0,
Cognee) fall back to the record's creation time when a validity start is missing — we do not,
because §4.7 already forbids inventing a start from the write clock. Reading B was put to the owner
as a reopen of that rule and refused.

The model rule this settled is in §4.7; this entry is the record of how it got there. Two residuals
of the same family were filed rather than absorbed: an unparseable start still reported
`valid_from_precision: "day"` on the read surfaces (that residual is now **closed by #9201**,
which requires orderability before the legacy `day` default applies — a caller-declared token
still wins; #2350 carries the field semantics),
and a present-but-unparseable `validTo` covers every later instant while still reporting
`end_state: "known"` (#9202, the END side, untouched by this ruling).

---

## §4.7 — the END's coverage gate, and the flag that is still open (#9202, coverage half, 2026-10-20)

The recorded reading for an unorderable `validTo` — *"covers every later instant"* — was true only
when the QUERY string parsed. `get_historical_knowledge`'s `_covers` gated the START arm on
orderability (#6140) but ordered the END key unconditionally, and an unorderable end and an
unparseable probe both bucket as `(1, <text>)`, so the comparison fell through to the TEXT: a stored
`validTo="TBD"` **excluded** probe `"zzz"` (which sorts after `"TBD"`) while covering `"AAA"` and
every dated probe, and an empty end sorted before every **unparseable** probe. The two shapes were
broken differently, and neither excluded a dated query — bucket `(1, "")` sorts after every
parseable instant. One stored window answered `absent` for one junk probe and `found` for another.

What changed: the END arm now requires the END to name an instant before it bounds anything — the
same rule §4.7 already states for this value on the write side (*"an unorderable bound … names no
instant, so a refusal would rest on a lexicographic accident rather than a comparison"*). The gate is
orderability of the **END alone**: a parseable end still excludes an unparseable probe, which is the
pre-existing pinned reading and is deliberately left as it is.

What did NOT change, and is still a question: `end_state` for a present-but-un-datable end still
reads `known`, so the reply keeps asserting the end is dated while the window covers every instant.
That is the **flag half** of #9202 and it is an OPEN OWNER DECISION — #6140 ruled on the start only,
and the three documents that record the END side as Daniel's call (`docs/ONTOLOGY.md` §4.7,
`docs/product/mcp-sdk-surface.md`, the `#2350` pin in `tests/test_2350_validity_precision.py`) are
unchanged in that respect. The options put to him there (report `END_OPEN` / `END_UNKNOWN` /
flag the stored bound separately) are untouched by this entry.

Pinned by `tests/test_validity_windows.py`
(`test_an_end_naming_no_instant_bounds_no_instant_even_for_an_unparseable_probe`, parametrized over
the `"TBD"` and `""` shapes because they were broken differently).

---

## §4.7 — the write door says what an un-datable END means (#9202, write-door half, 2026-10-24)

**Build record only. The model is unchanged and the END's reported state is still the owner's
call** (§4.7: *"Whether that state should instead read `unknown` / `open` is #9202's owner decision
and is NOT settled by the gate"*).

The #6140 ruling (`"A and C (c should be a warning)"`) wired a write-door warning for ONE side of the
window: `sdk._refuse_inverted_point_window` told a caller that a supplied `validFrom` names no
instant, and said nothing about a supplied `validTo`. So the identical caller mistake was announced on
one end and wordless on the other, and the silent end is the more consequential one — the value is
stored verbatim, bounds nothing, and the reply then reports `end_state: "known"` for it.

What changed: that helper is generalised to `_warn_unorderable_caller_bound(prop, bound)` and fires for
both ends on the same gate (`_created_sort_key(bound)[0] == 0` — the measure `_covers`,
`validity.effective_precision` and the inversion guard order with, so the warning cannot disagree with
the read path about what an instant is). The END carries its own text: the window is UNBOUNDED
**above**, and a caller meaning *known-but-undatable* is steered to the declared
`validToPrecision="unknown"` mechanism (on an update that requires an explicit `validTo=None`, because
omitting the key keeps the stored value). A warning, never a refusal, and the value is stored
verbatim — the #6140 shape.

**Scope, stated because a build record that over-claims is worse than none.** The claim is exactly
*two caller-prop Point doors*: `create_point` and `update_point`, both through
`sdk._refuse_inverted_point_window`. It is NOT "no end can be stored wordlessly":

- `update_entity`'s Point arms write caller props (including `validTo`) with no lifecycle guard and no
  window guard — the same live route recorded by `docs/ONTOLOGY.md`'s Point Status Vocabulary entry
  (§5): *"the unguarded `update_entity` Point arms above remain a live route to the same window
  (#9649)"*. Tracked as **#9649**.
- `supersede_point` persists the predecessor's end from its resolution chain, including the ONE
  deliberate exemption in the `validTo` row of `docs/ONTOLOGY.md` §4.1 Point — a NUMERIC `valid_from`
  kwarg, `str()`-ed and left unorderable (`sdk._supersede_window_end`). That path is not a caller
  `validTo` write and does not warn; it is not in scope here.

What did NOT change: coverage (already ruled — a bound that names no instant is not a bound, #9408),
`end_state`, every read surface, and the stored values of records written before this change. A log
line has no persisted meaning to migrate, so no rescan or read-time rule applies: a record carrying an
unparseable end keeps reading exactly as it does today, and the reserved question covers it unchanged.

Two residuals recorded rather than absorbed:

- **The warning fires before `update_point`'s lifecycle guard** (#2498 / #7819). An update of a
  TERMINAL point therefore prints the value advice and is then refused for terminality. ⚠️ The advice
  is executable only while the claim is NON-terminal (live / draft): `_assert_lifecycle_guard` refuses
  every prop in `_TEMPORAL_RESTAMP_PROPS` — `validTo` included — on every terminal status, and that is
  pinned (`tests/test_lifecycle_guards.py`
  ::`test_update_point_refuses_a_window_on_every_terminal_status`, parametrized over the statuses
  including `superseded` and `retracted`). Superseding or retracting does NOT unlock the repair — the
  only post-terminalisation route is the unguarded `update_entity` arm listed above (#9649). The
  ordering itself is pre-existing and shared with the start arm; it is not reshaped here, because
  moving the window guard past the lifecycle guard would change which refusal a caller with both
  problems receives.
- **`docs/ONTOLOGY.md` §4.7's END paragraph still does not say the write door warns** — it says the END
  agrees on the gate and differs only on the state, which is true of the READ and silent about the
  write. That file carries its own rule (*"no modification … without explicit approval of the exact
  changes by Daniel Ospina"*), so the one sentence that would align it waits on that approval; this
  entry is the record until then.

Pinned by `tests/test_validity_windows.py`
(`test_an_unorderable_valid_to_warns_at_the_write_boundary` — an 11-row value matrix, and
`test_the_end_warning_is_scoped_like_the_start_warning` — caller-value-only, update leg, dedup
re-entry once).

⚠️ **Superseded in time order by the 2026-10-26 entry at the top of this file.** Its two load-bearing
claims are the two the owner reversed there: the END now reads `end_state: "unknown"` (settled, not the
owner's open call), and a caller end that declares no date is **shaped**, while one that attempts an
instant and fails is **refused** — no longer "stored verbatim, merely warned about". This entry stands as
the record of what was true on 2026-10-24; it is not rewritten.
