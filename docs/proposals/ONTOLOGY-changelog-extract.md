---
title: "ONTOLOGY-CHANGELOG.md — extract for v3.39 (implementation history removed from ONTOLOGY.md)"
type: draft
domain: data
doc_status: draft
created: 2026-11-23
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: ONTOLOGY.md, ONTOLOGY-CHANGELOG.md, canonical ontology
---

# `ONTOLOGY-CHANGELOG.md` extract — Class B/C material, ready to paste

> **What this is.** Every citation and every implementation-status clause the canonical draft
> removes from `docs/ONTOLOGY.md`, in the form it takes in `docs/ONTOLOGY-CHANGELOG.md`. It is
> **one new entry** (`v3.39`), formatted to paste at the **top** of the changelog, immediately
> below the changelog's own header block.
>
> **What it is not.** It is **not a retelling of each issue.** What each issue contained lives
> on the issue, which this entry does not paraphrase — a changelog that invents a summary of a
> tracker it did not read is worse than a changelog that carries only the trace. What is
> recorded here, per row, is exactly:
>
> - **which issue** was cited,
> - **where** in `ONTOLOGY.md` it stood (line at `c1c8df1a3817`),
> - **its class** under this change's policy,
> - **the verbatim clause** it annotated — so the trace and the sentence it explained are never
>   separated, and the rule that stayed in the model is recoverable from this file alone.
>
> **Paste target:** `docs/ONTOLOGY-CHANGELOG.md`, after the header block that ends with
> *"**CRITICAL RULE:** no modification to this file is allowed without explicit approval of the
> exact changes by Daniel Ospina."* and before the current top entry (`v3.38`).

---

<!-- PASTE-BEGIN -->

> **Changelog v3.39 (2026-11-23 — `ONTOLOGY.md` made canonical: implementation history, build
> status and provenance moved out of the model):**
>
> **Owner approval of the exact changes:** approved by Daniel Ospina on ________ (record the
> approval comment id here — `ONTOLOGY.md:41` requires explicit approval of the exact diff, and
> this entry is that diff's other half). The change is **its own approval request**, separate
> from **#8745**: that issue carries the approval for the 2026-10-15 move (the 730-line
> *"Moved out of `ONTOLOGY.md`"* block), and it does not cover this one.
>
> **What was wrong.** `ONTOLOGY.md` said, in its own header: *"This document states the model we
> WANT — not a status report on what is built… Read it as the specification, not as a description
> of the current build"* (`ONTOLOGY.md:19-24`), and *"No issue state, no change history. New
> prose here names no PR, no issue and no build status **as history**"* (`ONTOLOGY.md:36`).
> The body did not obey either sentence: measured at `c1c8df1a3817`, **324 tracker references
> across 192 of 1,568 lines** — 323 `#NNNN` plus one two-digit `#86` — decision provenance, per-field build status, private function
> names, test names, and open-issue pointers. The header even carried the escape hatch this
> change exercises: *"replacing the ones that are is a separate change."* **This is that separate
> change.**
>
> **What the model now says.** Nothing new about the ontology. Every rule is unchanged; what
> changed is that the model no longer argues for itself from its issue tracker. `ONTOLOGY.md`
> names no issue, no PR and no build status, and carries **one** pointer — in its header — saying
> that provenance lives here.
>
> **What did NOT change.** Every normative statement, every declared vocabulary, every field,
> every predicate, and the two owner-gated lines (`ONTOLOGY.md:41`, this file's own CRITICAL
> RULE). No field is added, retired or renamed. No rule is weakened. **No rule is moved into this
> file** — only citations and status. Where a sentence bundled a rule with build status, the rule
> was restated in canonical prose and the status moved here; those restatements are listed in the
> proposal (`docs/proposals/ONTOLOGY-canonical-draft.md`) row by row, per section, for approval.
>
> **One rule IS changed, and it is named here rather than filed under "removal".** The header
> used to carve out one category: *"A reference that records **where a term or a decision lives**
> (`validTo`'s registration, `c_cal`) is not history and **stays**"* (`ONTOLOGY.md:36`). This
> change removes that category too, so the model reaches zero numbers. That is a **policy
> change**, not an application of the old rule — the old rule protected it — and it is proposed
> as `P-1` in the draft. If `P-1` is rejected, **17** location references stay in the model (the `#NNNN §x.y` form)
> and every other part of this change is unaffected.
>
> **How the removal was checked.** Per section: the count of `must|shall|required|always|never`
> plus declared-vocabulary blocks (before → after), and a complete inventory of the rules that
> carried a citation, each marked *kept* or *moved*. Moved rules: **0**. Any section where a
> status clause was the whole content of a sentence is called out at its own row below, so the
> one lossy case in a section is visible rather than averaged away.

## Batches — the provenance extracted, in the order it was applied

### Batch 1 — header, §1, §2 (9 references)

**Header — 0 references, +2 rules, version bumped.** The header gains one normative sentence (new
prose names a location by section or by code symbol, never by an issue — **scoped to new prose**, so
it composes with `P-1` either way), **loses** the `ONTOLOGY.md:36`
carve-out that kept *"where a term or decision lives"* references in the model (draft `P-1`), and
moves `v3.38` → `v3.39` in the frontmatter `title:` and the H1 with `updated:` → `2026-11-23`
(draft `P-2`). Both policy items are listed here because they are the only places in this change
where the model's rules move rather than only its citations.

**§1 — 0 references.** No change.

**§2 — 9 references, all removed:**

| # | Issue | At | Class | The verbatim clause it annotated (now here) |
|---|-------|----|-------|---------------------------------------------|
| E-1 | **#4937** | `ONTOLOGY.md:68`, `:80` | **A** / **B** | *"`MITIGATES` is not an operator kind"* — the rule stays in §2; the citation of the ruling lives here. The second occurrence named it *"the F1 ruling"*, which is the decision's label, not its content. |
| E-2 | **#2193** | `ONTOLOGY.md:70` | **B** | *"folded keep-first per Object (divergent re-folds never blind-overwrite — #2193 resolved)"* — the live≠replay divergence class this resolution closed. The rule (keep-first, never blind-overwrite) stays in §2. |
| E-3 | **#2552** | `ONTOLOGY.md:80` | **B** | *"the F1 ruling on #2552"* — the question #4937 ruled on. Substance is E-1's. |
| E-4 | **#7902** | `ONTOLOGY.md:68`, `:105` | **C** | *"the `:Subject`/`:Object` operator endpoints are live (#7902); the write path admits all four"* — the *liveness* is build status and lives here; the rule (*the write path admits all four*) stays in §2. |
| E-5 | **#7813** | `ONTOLOGY.md:68`, `:103` | **B** | *"Point→Event operators propagate EP under the #7813 ruling — confidence crosses all four labels, so an Event target is epistemic (§8 OVERRIDES)"*. The owner ruling's *content* is the sentence itself and stays in §2; the attribution lives here. §8 carries the same ruling as an `OVERRIDES:` marker. |
| E-6 | **#2729** | `ONTOLOGY.md:70` | **A** | *"a pack's per-kind `kindDefs[].statuses`/`folds` declaration for its own kinds"* — the rule stays verbatim; the number was this change's class-A case (rule itself, citation decorative). |

**Nothing in Batch 1 was classified D** (open-issue pointer) — §1 and §2 carry none.

**Batch 1 scope note (dates).** A bare date that names a model state — e.g. *"state-centric
alignment (2026-08-12)"* — is **retained**: this change removes `#NNNN` references and
build-status/provenance clauses, not the model's dated state names (draft `D-6`). Widen that and
it is a different, much larger change.

---

### Batch 2 — §3.1–§3.9 (63 references)

**§3.7 carried none; §3.6 carried one and it is a whole-sentence move (B-3).** The rows below
group by issue. Where an issue appears several times in one section, all its locations are listed
and the verbatim clause is quoted once.

| # | Issue(s) | At | Class | The verbatim clause it annotated (now here) |
|---|----------|----|-------|---------------------------------------------|
| B-4 | **#7902** | §3.1:113, §3.8:225 | **C** | *"The `:Subject`/`:Object` operator endpoints are live (`#7902`); the write path admits all four"* / *"`:Subject`/`:Object` endpoints are live (`#7902`); the write path admits all four"*. The admitted-labels rule stays in the model. |
| B-5 | **#753** | §3.1:118 | **B** | *"`refutes` records `->` (the directed attack — attacker's truth penalizes the target, no back-pressure — #753)"*. |
| B-6 | **#2489** | §3.1:124, §3.2:159, §3.2:162, §3.9:339 | **B** | *"Structural-edge transfer is journaled + replayable (#2489)"* · *"(the `#2489` replay rules are in §3.1)"* · *"(the `#2489` split, like `extractedFrom`)"* · *"Per #2489 a predicate's label and its replay key are one unit"*. The replay contract and the label/key rule both stay in the model. |
| B-7 | **#9836** ×4 | §3.1:124 | **B** | *"the **ID-ADDRESSED** family (`#9836`) — `aboutAction`, `wasDerivedFrom` — transferred by 2b but NOT snapshot-derivable, so before `#9836` they moved live and journalled nothing: the journal route lost the edge while the record kept its pre-supersede row"*, *"that conflation is the defect `#9836` closed"*, and the second family reference in the resolution sentence. W-1 restates the first clause in the model's tense; the rest is provenance. |
| B-8 | **#9972**, **#9973**, **#10191** | §3.1:124 | **B** | *"**The RECORD applies a 2b descriptor as the same MOVE (#9972, #9973, #10191):**"* — the DELETE-then-INSERT contract (direct successor, one hop per descriptor) stays in the model. |
| B-9 | **#5509** | §3.1:124 | **B** | *"landing NOTHING when a name is held by two or more carriers (#5509)"*. |
| B-10 | **#9417** | §3.1:124 | **B** | *"keeps its OWN live value (`STORAGE-ARCHITECTURE.md` §13, #9417)"*. |
| B-11 | **#2498** | §3.1:124 | **B** | *"`supersede_point` refuses the closing hop — `#2498`: a dead target cannot be re-terminalized"*. |
| B-12 | **#2500** | §3.1:124 | **D** | *"rebuild does NOT repair pre-existing graphs; a #2500-style backfill is out of scope"* — the scope statement stays in the model, the tracker does not. |
| B-13 | **#909** ×4 | §3.1:143, §3.4:180 (×2), §3.9:362 | **B** | *"Extraction NAND direction policy (epic #909 §4.3 #5 / research addendum §1 — pipeline spec)"* · *"(producer extension in `link_source_to_entity`, #909 §4.3 #8)"* · *"(referential chain, #909)"* · *"the mitigation Point back-linking `-[:IMPL]->` the operator (#909 §4.3 #7 — registered)"*. Every rule stays. |
| B-14 | **#391** ×2 | §3.2:160, 161 | **B** | *"Creatable via `create_edge` (#391)"* · *"No automatic producer — creatable only via explicit `create_edge` (#391)"*. |
| B-15 | **#3856** | §3.2:162 | **B** | *"**A user-authored Tag is first-class DATA, not a derived label** — #3856 (OD-4) settled that by what a Tag *is* (*"shared, MERGEd, first-class nodes are data"*, so *"the owner is not asked"*)"*. |
| B-16 | **#2897** ×2 | §3.2:162 | **B** | *"tags survive `rebuild_all` (#2897, objective 6 "props, **tags** and EP state survive")"* · *"its fix is NOT retroactive for graphs seeded before #2897"*. |
| B-17 | **#7369** | §3.2:162 | **B** | *"a value the store cannot hold degrades to NO property (#7369)"*. |
| B-18 | **#8333** | §3.2:162 | **D** | *"the difference is filed as **#8333** (with the one-line fix, in `_sync_tags`, that closes it)"*. The divergence itself (live keeps a stale `TAGGED` edge; `rebuild_all` drops it; `rebuild(EventLog)` keeps it) stays in the model. |
| B-19 | **#1727**, **#3664**, **#4240**, **#2296**, **#4282** | §3.2:166 | **B** | *"Session as an aboutObject source (#1727 Slice 2, Task 12; SDK parity + durability #3664)"* · *"**#3664:** the SDK `capture_session` ran the same pass as hosted `_capture_session_impl` (it previously did not — a self-hosted capture was an unattached island)"* · *"the base dir wires a per-graph `event_log_path` when set, #4240"* · *"the edges stay live-only (#2296)"* · *"**#4282 retired that PUBLIC name**"* · *"`SessionRecorded` (#3664)"*. |
| B-20 | **#1417** ×2 | §3.2:168 | **A** / **B** | *"**`aboutEvent` is content-only (#1417):**"* — the rule. *"Pre-#1417 `aboutEvent`-as-provenance edges remain readable and are semantically reinterpreted (no migration)"* — the legacy-read rule, kept in the model as "Pre-existing". |
| B-21 | **#3263** | §3.3:174 | **B** | *"(amended v3.11, #3263: the edge is written per source — a claim extracted from several sessions carries one `extractedFrom` edge each; there is no upper bound)"*. |
| B-22 | **#398** ×2 | §3.4:182, 184 | **B** | *"(T0-T4 credibility tier — see §4.6, #398)"* · *"`sourceKind` is set on CREATE only — a pre-existing Source's kind is authoritative (#398 never-overwrite contract)"*. |
| B-23 | **#388** ×2 | §3.4:184, §3.5:197 | **B** | *"from connector event metadata (`sourceKind` + per-entity `sourceUrl`) — #388"* · *"the `references` hop is wired in the ingest path for connector entities at the `_upsert_event` choke point — since #388 — see §3.4"*. |
| B-24 | **#531** ×4 | §3.5:191, §3.8:222, 227 (×2) | **B** | *"(the #531 `humanApproval` pattern: an approval Event produces the decision Point that seeds its grounding)"* · *"an Object, or a decision Point (#531)"* · *"**#531 — canonical Event→Point pattern (`humanApproval`):**"* · *"Worked example (`file_human_approval`, #531)"*. Every pattern rule and the Cypher example stay. |
| B-25 | **#7813** | §3.8:225 | **B** | *"Point→Event propagates EP under #7813 (an Event target is epistemic — §8 OVERRIDES)"*. |
| B-26 | **#214** | §3.9:248 | **B** | *"**#214 (2026-08-06):** `instantiates` removed — Event→Action legacy from v2.5"* — W-3 restates it as *"`instantiates` is not a valid predicate"*. The enumerated predicate vocabulary is unchanged. |
| B-27 | **#5025** ×2 | §3.9:273, 337 | **B** | *"**It carries no epistemic weight, by decision (#5025, owner ruling 2026-09-27).**"* · *"this change only brings the traversal into line with the #5025 decision above"*. |
| B-28 | **#5547** | §3.9:320 | **B** | *"pinned by `tests/test_dry_run_preview.py` ("in NO transfer leg") and `test_related_is_neutral_by_construction` (#5547)"* — the test names stay as code symbols. |
| B-29 | **#5566** | §3.9:325 | **B** | *"⛔ **"Carries no epistemic weight" — the decision held, and the breach that undercut it is now closed (#5566).**"*. |
| B-30 | **#7551** | §3.9:346 | **B** | *"**The operator-label declared vocabulary (#7551).**"*. |
| B-31 | **#1934** | §3.9:353 | **B** | *"an undeclared label still warns without blocking (warn-not-block, #1934)"*. |
| B-32 | **#11083** | §3.9:357 | **B** | *"the transition map is the authority for that list, and `produces` left it when it became a meaning (#11083)"*. |
| B-33 | **#2315** ×2 | §3.9:364, 374 | **B** | *"**Hard rule (#2315, pinned 2026-09-07):**"* · *"**Strength semantics (#2315, product decision 2026-09-07 — mitigation is a GRADED DAMPENER, not a refutation):**"*. |
| B-34 | **#2199** | §3.9:384 | **B** | *"The strength is NOT fused into the mitigation point's own Beta prior (#2199 decision 3)."* |
| B-35 | **#4282** | §3.2:168, §3.6:208 | **B** / **C** | *"(its public name retired by #4282)"* — the retired-public-name rule stays in the model. The §3.6:208 occurrence is the whole-sentence move documented at **B-3**. |

**Moved whole — no model rule inside (each can be restored on its own):**

- **B-1 · `#2501`** (class C+D) — `ONTOLOGY.md:124`:
  *"Full `about*` parity additionally depends on #2501 (create_point never live-wires
  `aboutEntities` — the only lane where 2b sees live `about*` edges is rebuild→supersede→rebuild)."*
  A build gap and its tracker. The only `never` token Batch 2 removes (D1 31 → 30, counting §3.9's declared-vocabulary block).
- **B-2 · `#8325`** (class C) — `ONTOLOGY.md:162`:
  *"⚠️ Doc-vs-code gap, filed as **#8325**: the node is created as `MERGE (t:Tag {name:$tag})`, so
  it carries **no** `:Object` label and **no** `objectKind` — the Object-subclass characterisation
  above is the intended model, not the implemented one."*
  The model half (a Tag IS an `:Object` subclass with `objectKind: tag`) was **promoted into the
  model** as W-2; only the gap report moves here. **If the owner prefers the original hedged
  wording, restore this sentence and reject W-2.**
- **B-3 · `#4282`** (class C) — `ONTOLOGY.md:208`:
  *"`get_org_structure` queried `memberOf` for membership (Subject→Subject, member→org) — it was the
  predicate's ONLY served reader, and **#4282 deleted it** (one of the 26 dead public
  `TortoiseSDK` methods, 2026-10-27). No served code queries `memberOf` now: the remaining
  references are an allowlist entry (`tortoise/security.py`), a rel-type docstring, registry prose
  and the structural-predicate note in `ep.py`."*
  The rule it framed — canonicality rests on the predicate's modelling, not on a reader existing —
  **stays in the model**.

### Batch 3 — §4.1–§4.4 (70 references)

**§4.2's nineteen and §4.3's twenty are all citations.** §4.1 contributes six `C`-class references
(three clauses plus one status phrase) and §4.4 three (inside two status sentences); everything that
leaves the model is quoted verbatim under **Moved whole** below.

| # | Issue(s) | At | Class | The verbatim clause it annotated (now here) |
|---|----------|----|-------|---------------------------------------------|
| B-36 | **#909** ×4 | §4.1:398, 405, 406, 416 | **B** | *"(deterministic — the commit endpoint's idempotency anchor, #909 §4.3 #10)"* · *"(registered #909 §4.3 #11; written by the calibrated pipeline, slice 5+)"* · *"stored Point property per #909 §4.3 #11 (secret-scanned)"* · *"the `points` branch counts non-episodic only, #909 §4.3 #13/§4.4"* (the fourth, at `:416`). The `pt_<sha>` sanctioned-form rule and the `c_cal`/`quote` field meanings stay. |
| B-36b | **#947** | §4.1:416 | **C** | *"legacy nodes lack the flag — one-query backfill migration ships with #947"* — delivery status; the model now says *"a one-query backfill migration closes that"* (W-8). |
| B-37 | **#11824** ×2 | §4.1:403, 417 | **B** | *"one closed set of four for every knowledge kind (#11824; target model in `docs/plans/2026-11-15-state-lifecycle-and-pipelines.md`)"* · *"and it is unassigned (#11824)"*. |
| B-38 | **#2498** | §4.1:403 | **B** | *"the legacy `outdated=true` flag is terminal too (#2498)"*. |
| B-39 | **#12855** | §4.1:403 | **B** | *"all three survive only as read deltas over stored data (#12855)"*. |
| B-40 | **#1533** | §4.1:407 | **B** | *"(registered #1533 E1; written by extractor_v2 S5 from the session-date-anchored prompts; absent on timeless durable beliefs)"*. |
| B-41 | **#3654** | §4.1:409 | **B** | *"Mining W-4's undated leg (`ConversationMiner._temporal_wire`, #3654) writes **no** `validFrom`"*. |
| B-42 | **#5358**, **#4021**, **#5360** | §4.1:410 | **B** | *"`retract_point` is the window-agnostic route (#5358)"* · *"…is satisfiable by no query instant) (#4021)"* · *"the orderability residual is closed on the write path (#5360)"*. Every refusal condition in the `validTo` cell stays in the model. |
| B-43 | **#3086** | §4.1:415 | **B** | *"(the ONE shared turn store (`tortoise/sdk.py::_TURN_WRITE_CYPHER`, #3086))"*. |
| B-44 | **#909** | §4.3:456 | **B** | *"(registered #909 §4.3 #12; planned for the capture path, slice 5+)"*. |
| B-45 | **#7856** ×2 | §4.1:417, §4.3:457 | **B** | *"validated at the SDK write boundary (`GOAL_STATE_VALUES`, #7856)"*. The closed vocabulary and the ending-agreement refusal stay. |
| B-45b | **#11824** | §4.3:457 | **B** | *"and it is unassigned (#11824)"* — the third `#11824` in §4.3, beside the two in the `status` cell (`B-49`). |
| B-46 | **#11824**, **#1423** ×3, **#1350**, **#2194**, **#2193**, **#2977**, **#2164** | §4.2:430, 431 | **B** | *"and stored as `deprecated` (#11824). **Projection-owned since #1423**"* · *"— the #1350/#2194 clobber guard ported to Subject (before #1423 `status` rode the extras lane … the #2193 live≠replay class, pinned by `tests/test_subject_supersession_1423.py`)"* · *"precedent (#2977), where a read vocabulary kept `retracted`"* · *"#1423: successor NAME"* · *"— the #2164-P4 drift class the Object twin still exhibits"*. **Every rule in the `status` row survives** (W-9 restates the clobber guard as a rule). |
| B-47 | **#3642** ×2 | §4.2:432, 433 | **B** | *"(declared §4.7, #3642)"* — the internal §4.7 pointer stays as structure. |
| B-48 | **#2295** ×4, **#2296**, **#1918**, **#2061**, **#2194** | §4.2:434, 437 | **B** | The Subject registration-durability blockquote: *"**Subject registration durability (#2295)**"* · *"pre-#2295 journals (no backfill)"* · *"(#2296 scope hook: the durability write-surface invariant …)"* · *"(random-ulid, #1918)"* · *"Event-precedent parity, #2061/#2194"* · *"(pinned by the #2295 suite test 1)"*. All durability rules stay. |
| B-49 | **#2729**, **#2193**, **#11824** ×2, **#9974**, **#2792** | §4.3:449 | **B** | The Object `status` row: *"for the pack's own kinds — #2729"* · *"(divergent re-folds never blind-overwrite — #2193 resolved)"* · *"`superseded` (#11824)"* (twice in the cell — see also `B-45b`) · *"(#9974 R1 — never a fabricated `live` write)"* · *"(`pipelineStageId`, #2792)"* · *"rather than a wider status list (#11824)"*. All rules stay, including "never a fabricated `live` write". |
| B-50 | **#2165** ×2 | §4.3:450, 451 | **B** | *"R17 P3-1 (#2165): `supersededAt` is the connected-assembly state-header's date source"* · *"(R12/C6, #2165 Task 5)"*. The byte-golden rule and the name-only-annotation rule stay. |
| B-51 | **#3642**, **#2194** | §4.3:452, 454 | **B** | *"(declared §4.7, #3642)"* · *"adopted ON MATCH only when absent — #2194"*. |
| B-52 | **#2194** ×2, **#2295** ×2, **#2296**, **#2061**, **#2309** | §4.3:461 | **B** | The Object registration-durability blockquote, same treatment as B-48: *"**Object registration durability (#2194)**"* · *"pre-#2194 journals (no backfill)"* · *"**Subjects ride the same probe-gated design since #2295 (see the §4.2 note).**"* · *"(#2296 scope hook: …)"* · *"Event-precedent parity, #2061"* · *"(the is-None gate — #2309 review hardening, mirrored for Subjects in #2295)"*. |
| B-53 | **#5142** | §4.4:469 | **B** | *"**A document is a `:Source` — and it stays TWO nodes (`#5142`).**"* |
| B-54 | **#205** | §4.4:471 | **B** | *"distinct from the corpus `#205` provenance Source (`url = source_url`)"*. |
| B-55 | **#5013** ×2, **#5088** | §4.4:483 | **B** | *"A deliberate, **deferred** decision (#5013 §4.4 · #5088 · `OVERRIDES:` posted on #5013), not an accident of history."* — **the `OVERRIDES:` marker itself stays in the model**; only its tracker reference goes. |
| B-56 | **#5151** | §4.4:486 | **B** | *"**The self-loop is now refused at the writer, and that does not lift the deferral (`#5151`).**"* |
| B-57 | **#5140** | §4.4:500 | **B** | *"that inversion is exactly the defect #5140 fixed"*. |
| B-58 | **#2726** ×2, **#398** | §4.4:506, 513, 515 | **B** / **C** | *"(#2726) Meeting capture — Document ↔ Source pairing (TARGET STATE, writer not yet wired)"* · *"the tier question is deferred to #398"* · *"deliberately out of #2726's scope"*. The model claim (Document ↔ Source pairing, both kinds NEUTRAL) is stated directly in the model; the two `TODAY` status sentences are under **Moved whole** below. |

**Moved whole — the only text that leaves the model in this batch:**

- **C-1 · `#522`, `#3154`** (class C) — `ONTOLOGY.md:413`:
  *"— `is_operator` is never indexed (#522 embedded stale bool type table; #3154 docker/server
  `GRAPH.COPY` drops the `false` postings of a copied boolean RANGE index, zeroing `is_operator =
  false` on copies whose index set carries it, and leaving the copy destination unable to rebuild
  it)"*. The **rule** — `is_operator` is never indexed — stays in the model.
- **C-2 · `#4282`, `#3554`** (class C) — `ONTOLOGY.md:415`:
  *"`TortoiseSDK.capture_session` until **#4282 retired that public name**, and
  `TortoiseSDK._capture_session` (private) since, which is the same body and the same shared
  primitive, so BOTH lanes still write it. The earlier claim that the SDK writes it alone was false
  (#3554)"*. The **rule** — both lanes write `speaker` through one shared turn store — stays.
- **C-3 · `#11915`** (class C) — `ONTOLOGY.md:421`:
  *"⚠️ **Status note on the clause above (2026-11-11, same change).** … **percent has a storing
  shape** since #11915 (`tortoise/percent.py` — `basisPoints` int 0–10000, plus `verbatim` only
  where the value came from prose, validated at the SDK's one props intake and at the journal-payload
  seam exactly as `money` is). … The approved sentence is left as approved and corrected here rather
  than rewritten — a dated status note is the record; rewriting an owner-approved clause is a new
  decision."*
  ⚠️ **This one is different: it records that a rewrite needs a new owner decision.** The proposal
  takes that decision openly as **P-4**. **Approving P-4 = the note and the reference are gone
  because the sentence now says the right thing. Rejecting P-4 = restore this note and its
  reference verbatim.**
- **C-4 · `#2726`** (class C) — `ONTOLOGY.md:506`:
  *"> **(#2726) Meeting capture — Document ↔ Source pairing (TARGET STATE, writer not yet
  wired):** once the capture/index writer emits the new kinds, a captured meeting will land as a
  Document…"*. "TARGET STATE / writer not yet wired / will land" is build status; the model now
  states the pairing directly.
- **B-59 · bare epic numbers — `epic 903` and `903-C4`** (class C) — `ONTOLOGY.md:134`, `:141` and
  `:413`: *"Edge properties (IMPL/NAND — EP message state, epic 903)"* · *"(`_load_cache` (warm-start
  seed, 903-C4))"* · *"Warm-start note (903-C4)"* · *"timestamp of the last EP write-back that
  **converged** on this claim (epic 903)"*. **Batch 2 missed the first three** (it verified `#NNNN`
  only) **and batch 3's §4.1 removal is the fourth** — all four are recorded here. None carries a
  rule; every heading and row keeps its content.
- **C-5 · `#398`** (at `:513`) and **`#2726`** (at `:515`) — (class C) — `ONTOLOGY.md:513`–`:515`:
  *"TODAY the index/classifier path still emits `meeting_summary` for a classified meeting file
  (`CLASSIFIER_TO_SOURCE_KIND`) — wiring the two new kinds into that writer is deliberately out of
  #2726's scope (registration + validation only), so `meeting_summary` remains the kind actually
  written."*

### Batch 4 — §4.5–§4.8 (105 references)

One row per **source line** that carried a reference, with every reference on that line named — so the
table reconciles to 105 by construction. **Where a line is a long table cell, the quote is an excerpt
marked with `…`**; the complete text of every such cell is quoted in the draft's disposition row and
is not lost. Nothing here is paraphrased.

| # | Issue(s) | Line | Class | The verbatim clause it annotated (now here) |
|---|----------|------|-------|---------------------------------------------|
| B4-01 | #909 | 522 | B | \| `eventId` \| ULID / content-addressed \| ✅ \| `dc:identifier` \| Unique occurrence ID — ULID by default; the **agentSession Event uses a content-addressed form** (hash of session_id + captured_at — deterministic MERGE anchor, #909 §4.3 #3) \| |
| B4-02 | #909 | 526 | B | \| `capturedAt` \| ISO8601 \| — \| — \| Event's **transaction-time start** (§4.7) — when our record of the occurrence was captured, the bi-temporal complement to `startedAt`/`endedAt` (valid time). Registered #909 §4.3 #2; written on `AgentSessi … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-03 | #1533, #1533 | 527 | B | \| `started_at` \| ISO date ≤40 \| — \| `prov:startedAtTime` \| **Payload-level** valid time on extracted-occurrence events (E1, #1533): the extractor emits it from the session-date anchor; graph property stays `startedAt` (coalesce `started_at … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-04 | #330, #900 | 530 | B | \| `file_hash` \| string \| — \| — \| SHA-256 of the file's UTF-8 text content (text-mode, universal newlines — CRLF-immune; #330/#900) \| |
| B4-05 | #909 | 531 | B | \| `is_episodic` \| bool \| — \| — \| Quota exemption discriminator — true on capture-path episodic Events (registered #909 §4.3 #13; planned for the capture path, slice 5+) \| |
| B4-06 | #909 | 533 | B | > **agentSession Event (#909 §4.3 #1):** the canonical eventKind for session-capture Events is **`AgentSession`** — EXACT code spelling (capital A; sdk.py `ingest_corpus`, session_indexer.py); `sessionCaptured` (the core kind still written … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-07 | #7853 | 544 | B | inert**: `_promote_incident_operators` (`tortoise/sdk.py`, #7853) reads an Event |
| B4-08 | #2788 | 548 | B | > **(#2788)** The names this lane historically carried — `name`, `eventStatus`, |
| B4-09 | #4282 | 552 | B | > dead public SDK methods deleted by `#4282` (2026-10-27, owner ruling) — so nothing in the |
| B4-10 | #7853 | 559 | B | > `_promote_incident_operators` (#7853) — see the note above. The pack-declared |
| B4-11 | #2729 | 560 | B | > fold generalization (#2729) is Object-scoped and does not extend to Events — |
| B4-12 | #2725, #11825 | 561 | B · D | > the Event-carries-state question was answered **no** in #2725. The accepted-and-half-honoured state it has today — the write is accepted, belief honours it, and every retrieval leg ignores it — is tracked at **#11825**. |
| B4-13 | #398, #2726 | 568 | B | \| `sourceKind` \| string \| ✅ \| — \| Extensible source TYPE vocabulary — **the core list and the pack extension point are in §5** + `source_credibility.SOURCE_KIND_DEFAULTS` are canonical; e.g. github_issue, github_pr, slack_message, linear_ca … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-14 | #5024, #5024, #5024 | 573 | B | \| `updatedAt` \| ISO8601 \| — \| `dc:modified` \| Last version transition. **Journalled since `#5024`:** a hash transition emits `SourceVersioned` carrying `previousContentHash` + the recorded instant, and the fold reads that instant — never th … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-15 | #3642, #3644, #3654, #3644, #5097, #3590 | 574 | B | \| `validFrom` / `validTo` \| ISO8601 \| — \| `prov:generatedAtTime` / `prov:invalidatedAtTime` \| **The CURRENT version's valid-time window** — when the content held in the world (declared §4.7, #3642). **`validFrom` is written since #3644**: ` … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-16 | #3642, #3590 | 575 | B | \| `expiredAt` \| ISO8601 \| — \| — \| Transaction-time expiry — when our record of this version stopped being current (declared §4.7, #3642). Still unwritten: no `:Source` removal path exists, and the removal field's meaning is exactly the deci … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-17 | #5127, #5142 | 577 | C | \| `format` \| string \| — \| `dc:format` \| Storage format (markdown, jsonl, yaml, cypher). In `_SOURCE_HANDLED` since D10 — **the model version is v3.15 (§4.4), the code half that moved it is PR #5127** (`#5142`) \| |
| B4-18 | #398 | 579 | B | \| `sourceDate` \| ISO8601 \| — \| `dc:date` \| Evidence-age clock for recency decay (falls back to `ingestedAt` — the pipeline-arrival proxy, #398) \| |
| B4-19 | #909 | 580 | B | \| `provenance_spans` \| JSON \| — \| — \| Window spans derived from the capture path's `provenance_refs` (plan-defined, #909 §4.3 #6; written by the capture path, slice 5+) \| |
| B4-20 | #909 | 581 | B | \| `is_episodic` \| bool \| — \| — \| Quota exemption discriminator — true on the session Source (registered #909 §4.3 #13; planned for the capture path, slice 5+) \| |
| B4-21 | #3998 | 583 | B | \| `rawState` / `rawStateAt` \| string / ISO8601 \| — \| — \| **The raw-availability axis** — the *third* value on a source record, after identity (`url`) and version (`contentHash`); `STORAGE-ARCHITECTURE.md` §9.4 ③ places it there, not as a fo … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-22 | #398 | 592 | B | \| `reliability` \| float 0..1 \| — \| — \| DERIVED query-time projection (mean of the modulated Beta prior) — documented cache, never authoritative (v3.2, #398) \| |
| B4-23 | #398 | 593 | B | \| `reliabilityComponents` \| JSON \| — \| — \| Cache metadata: tier, decay, factor, assessment_count, derivation time (#398) \| |
| B4-24 | #398 | 594 | B | \| `reliability_derived_at` \| ISO8601 \| — \| — \| Cache freshness stamp (#398) \| |
| B4-25 | #5082, #5142 | 596 | B | > **⚠️ Document `:Source` scoping — which §4.6 REQUIRED fields a document actually supplies (`#5082`; recorded `#5142`, closed here).** §4.4 says a document is a `:Source`, but a document `:Source` is written by **different paths** from the … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-26 | #5082, #398, #5082, #398 | 600 | B | > \| `sourceKind` \| ✅ **written since #5082 by every writer that creates a document `:Source`** — each stamps `'document'` **CREATE-ONLY** (`coalesce` writes only when the value is absent — #398's never-overwrite contract; the event branch a … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-27 | #5592, #5422, #900 | 601 | C | > \| `contentHash` \| ⚠️ **written on the path that READS content.** Since PR #5592 (`#5422`, `700960943`) the extraction-ingest path writes it through a preserve/replace gate: an absent or empty (`''`) hash preserves the stored one, an empty … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-28 | #5013, #5082 | 604 | B | > The recorded ruling is **#5013 Q2** (*"KEEP BOTH AXES. A source is `sourceKind: document` **and** carries a genre"*) plus §4.6's REQUIRED marking — the `#5082` write implements it; it is **not** a licence to read §4.4 as *"a document is a … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-29 | #8525 | 606 | B | > **What the write does and does not move downstream (`#8525` review).** It does **not** move credibility: a |
| B4-30 | #398 | 608 | B | > to `_apply_source_inheritance`'s aggregate; and the #398 word-point un-freeze requires a **resolved |
| B4-31 | #3644, #5097, #3644 | 630 | B | **A version's window is opened and the prior one closed in the same act (`#3644`).** The version's valid-time start is the instant it was **observed** — `create_source` mints it once, on the same clock as `updatedAt`, and `_upsert_source` w … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-32 | #5024, #3644, #3644, #3590, #3980, #4021, #5358, #5360 | 636 | B | **The version record — both halves** (the anchor half is `#5024`, the window half `#3644`). The *version-anchor* half: a hash transition journals a `SourceVersioned` record naming `previousContentHash` and the transition instant, a no-op re … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-33 | #3642 | 641 | B | third, separate fact. One canonical name per slot (issue #3642).** This section |
| B4-34 | #3654 | 650 | B | \| start \| `validFrom` \| `prov:generatedAtTime` \| Populated by the date-carrying write paths — the hosted commit path sets it from the payload `when`, mining W-4 from the session frontmatter date (§4.1). An undated session stamps **no** `val … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-35 | #1538, #3980, #5360, #4021, #5360, #5358 | 651 | B | \| end \| `validTo` \| `prov:invalidatedAtTime` \| On **supersession** set to the successor's `validFrom` **when the successor carries one** — the **contiguous Graphiti (Zep) window intent: the old fact stops being true when the new one starts … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-36 | #7818 | 653 | D | > **The coverage boundary is stated here because it was not (#7818).** The read |
| B4-37 | #5374 | 657 | B | > `commit_schema.validate_validity_window` declares (#5374), whose agreement test |
| B4-38 | #4021 | 669 | B | >   route: it makes the answer depend on the id you enter from, the #4021 defect |
| B4-39 | #5362 | 675 | B | >   makes a zero-length window genuinely empty; evaluated as R2 of the #5362 plan), |
| B4-40 | #7818 | 676 | D | >   accept the closed ambiguity, or a third shape — tracked at **#7818**. Until it |
| B4-41 | #5374 | 681 | B | >   `validTo == validFrom` (#5374), so a zero-length `[t, t]` window is already |
| B4-42 | #9202 | 685 | B | >   and only once `vT` names an instant (#9202 — a bound that names none is not |
| B4-43 | #5361 | 689 | B | >   #5361's `malformed` flag stays **silent**, because that detection is strictly |
| B4-44 | #8337, #8487 | 693 | B | >   (#8337's rename, then #8487's merge into the same file, +291 lines), and a |
| B4-45 | #9202 | 714 | B | > The END agrees on the **gate**, and its **state is settled** (#9202, owner |
| B4-46 | #9202, #9408 | 718 | B | > itself names no instant is covered too (#9202's coverage half, #9408) — **and it |
| B4-47 | #2349, #2350 | 730 | B | > fabricated-coverage hazard this vocabulary exists to prevent (#2349/#2350). |
| B4-48 | #2350 | 735 | B | **Per-end precision and the 3-state end (#2350, E-prec).** The window pair |
| B4-49 | #9244 | 752 | B | annotates a bound; it cannot summon one** (#9244) — so both the declared token |
| B4-50 | #9729 | 759 | B | **no** precision property (#9729). |
| B4-51 | #5024, #3644, #5097, #3644, #3590 | 803 | B | > † **A source's temporal slots are the Source column's.** No `:Object`-labelled write path reaches a Source, so the Object-labelled supersession fold (`_fold_object_superseded` / `apply_supersessions`, which `MATCH`es `(o:Object {id\|name}) … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-52 | #3654 | 807 | B | > (#3654), so an absent `validFrom` is an **open start** and the write wall |
| B4-53 | #2295 | 833 | B | \| status \| status \| ⚠️ SDK extra-prop (projection-owned planned, #2295) \| ✅ (fold cache — event-derived) \| — \| — \| |
| B4-54 | #5127, #5142 | 837 | C | \| format \| — \| — \| — \| format \| **✅ `format` belongs here** — moved into `_SOURCE_HANDLED` by D10 (the model version is v3.15, §4.4; the code half that moved it is PR #5127 — `#5142`) \| |
| B4-55 | #11827 | 843 | B | **A value carries no temporal slot of its own.** A stored number's instant is the carrier's valid-time start — `when`/`validFrom` on a `Point`, `startedAt` on an `Event` — never a field of the value. Where a value names no instant, the carr … *(excerpt — the full cell is quoted in the draft’s disposition row)* |
| B4-56 | #3551 | 910 | B | `tortoise/sdk.py::_write_session_and_turns` (#3551) plus the one turn store |
| B4-57 | #3086 | 911 | B | `_TURN_WRITE_CYPHER` (#3086); the hosted `POST /v1/sessions` handler holds no copy of the MERGE text and adds only |
| B4-58 | #4282 | 913 | B | door #4282 left in place of the retired public name; see the canonical |
| B4-59 | #3554 | 921 | C | The #3554 retrofit audit (`tortoise/session_schema.py::audit_session_retrofit`, |
| B4-60 | #2335 | 930 | B | `false` would re-label a store loss as an extraction failure and arm the #2335 |
| B4-61 | #10000, #10011 | 953 | C | (#10000)** — after `#10011` the capture writer stores a turn's text and |
| B4-62 | #3554 | 969 | C | section and at the `#3554 amendment` in |

**Moved whole — the only text that leaves the model in this batch (each carries no rule):**

- **C-6 · `#5127`, `#5142`** (class C) — `ONTOLOGY.md:577` and `:837`:
  *"In `_SOURCE_HANDLED` since D10 — **the model version is v3.15 (§4.4), the code half that moved it is
  PR #5127** (`#5142`)"* and *"(the model version is v3.15, §4.4; the code half that moved it is PR
  #5127 — `#5142`)"*. The rule — `format` belongs to the Source — stays.
- **C-7 · `#5592`, `#5422`, `#900`** (class C) — `ONTOLOGY.md:601`:
  *"Since PR #5592 (`#5422`, `700960943`) the extraction-ingest path writes it through a
  preserve/replace gate"* and *"The `#900` index path (`_doc_write`) and `--capture-metadata`
  deliberately emit none"*. The gate rule and the named paths stay.
- **C-8 · `#3554`** (class C) — `ONTOLOGY.md:921` and `:969`:
  *"The #3554 retrofit audit (`tortoise/session_schema.py::audit_session_retrofit`, reached via
  `tools/session_retrofit_audit.py retrofit`) quantifies three divergences …"* and *"points at this
  section and at the `#3554 amendment` in `docs/plans/2026-08-25-1714-memory-capture-onboarding.md`"*.
  The audit, its three verdicts and their reasoning all stay.
- **C-9 · `#10000`, `#10011`** (class C) — `ONTOLOGY.md:953`:
  *"**`turn_count` with no turn member at all — the store, not a shortfall (#10000)** — after `#10011`
  the capture writer stores a turn's text and vector in the **record** and writes **no turn node**
  whenever the record can hold them"*. The verdict — `retro_quantifiable: False`, the
  `turns_record_held` bucket, and the placement rule it defends — stays in full.
- **D-1 · `#7818`** (class D) — `ONTOLOGY.md:653` and `:676`:
  *"**The coverage boundary is stated here because it was not (#7818).**"* · *"evaluated as R2 of the
  #5362 plan), accept the closed ambiguity, or a third shape — tracked at **#7818**."* **The
  half-open-vs-closed question stays an open decision in the model**; only the tracker does not.
- **D-2 · `#11825`** (class D) — `ONTOLOGY.md:561`:
  *"The accepted-and-half-honoured state it has today — the write is accepted, belief honours it, and
  every retrieval leg ignores it — is tracked at **#11825**."* The behaviour stays; the tracker does not.

### Batch 5 — §5 (40 references)

One row per source line that carried a reference. **Five of these lines are inside declared
vocabulary blocks** — `B5-12`, `B5-13`, `B5-14` in the Event-kind block and `B5-22`, `B5-23` in the
Response-Contract block. Those five edits remove a trailing comment only, and every declared term is
unchanged.

| # | Issue(s) | Line | Class | The verbatim clause it annotated (now here) |
|---|----------|------|-------|---------------------------------------------|
| B5-01 | #1013 | 990 | B | > removed (issue #1013 — episodic records are Event nodes with eventKind |
| B5-02 | #7813, #7853 | 995 | B | > residue (#7813 owner ruling, implemented by #7853).** Four kinds sit in BOTH |
| B5-03 | #7813 | 1004 | B | > and targets may be any of the four labels. The departure from the pre-#7813 |
| B5-04 | #7902 | 1006 | C | > operator endpoints are live (`#7902`); the write path admits all four (§3.1). |
| B5-05 | #4369 | 1008 | B | > **Sanctioned gloss — "claim" (#4369).** Where **"claim"** names a belief node, it is a |
| B5-06 | #2727 | 1030 | B | > **Legacy extraction path (pinned, #2727):** the Phase-2 entity stage in |
| B5-07 | #7856 | 1053 | B | > **Goal state (`goalState`) — achievement is a SEPARATE axis (#7856).** The |
| B5-08 | #11824 | 1061 | B | > **Status note (2026-11-17, owner-approved — #11824):** `guideline` was |
| B5-09 | #6792 | 1062 | B | > **removed** from `goalState`. The owner's #6792 form ruling is **not |
| B5-10 | #131 | 1069 | B | > EP-inert** (wiring promotes only the SOURCE, #131) while `converged=True` is |
| B5-11 | #7856 | 1071 | B | > **born live** (issue #7856) — a goal that can never influence a verdict must |
| B5-12 | #531 | 1079 | B | documentCreated, roleCreated, pointAdded, sessionCaptured, AgentSession, humanApproval,  # #531 |
| B5-13 | #2767 | 1080 | B | milestone,          # #2767: a milestone is REACHED at a moment — core event (a |
| B5-14 | #1013 | 1083 | B | # turn = capture turn records (replaces pointKind 'event', issue #1013) |
| B5-15 | #909 | 1092 | B | > **#909 §4.3 #1:** `AgentSession` (EXACT code spelling — capital A; sdk.py `ingest_corpus`/session_indexer.py) is the canonical kind for session-capture Events; `sessionCaptured` (the core kind written by the regex capture path) … *(excerpt — full line quoted in the draft row)* |
| B5-16 | #2311, #2311 | 1109 | B | > **Account layer vs in-graph Subjects (#2311):** the `organization` / `team` kinds above (and their §6 subclasses) are **in-graph Subjects inside a memory** — semantically distinct from the control-plane account unit that owns th … *(excerpt — full line quoted in the draft row)* |
| B5-17 | #398, #388 | 1126 | B | > **v3.2 (#398):** `sourceKind` is the extensible source TYPE vocabulary — pack-declared kinds (github_issue, github_pr, linear_card, linear_cycle, slack_message, document, meeting_transcript, meeting_minutes...) resolve to a tier … *(excerpt — full line quoted in the draft row)* |
| B5-18 | #2726 | 1130 | B | > **(#2726) Meeting-capture kinds:** `meeting_transcript` (raw, first-hand) and |
| B5-19 | #398 | 1135 | B | > deliberately deferred to #398's mechanism; `register_source_kind_default` |
| B5-20 | #909, #398 | 1139 | B | > **#909 §4.3 #6:** `sourceKind: agentSession` is a registered source-type VALUE (the four-node capture model's session Source — the provenance bridge — carries it; the value belongs to the `sourceKind` vocabulary above, alongside … *(excerpt — full line quoted in the draft row)* |
| B5-21 | #2101, #2080 | 1141 | B | ### Response-Contract Vocabulary (W4 why-layer, #2101 / epic #2080 DM-12) |
| B5-22 | #11702 | 1154 | B | # null ⟺ the counter-claim has NO persisted belief (#11702): there is |
| B5-23 | #11702 | 1158 | B | # persisted belief (#11702 — never the 0.5 the `coalesce(…, 1.0)` |
| B5-24 | #432, #690 | 1167 | B | ### Point Status Vocabulary (canonical, #432/#690) |
| B5-25 | #1088, #1088 | 1171 | B | \| `draft` \| initial (opt-in, #1088) \| `live`, `deprecated`, `superseded` \| Inert for EP computation; promoted by `promote_point` / `update_point(status='live')`, and — for its OWN minted ids — by the capture ingest pass. An AUTHOR … *(excerpt — full line quoted in the draft row)* |
| B5-26 | #11824 | 1173 | B | \| `deprecated` \| terminal \| *(none)* \| **Ended, and nothing replaced it.** `archived` is accepted on write as a synonym and stored as this word (#11824) \| |
| B5-27 | #12855, #11824, #2498 | 1176 | B · C | > **`retracted`, `outdated` and `archived` are read-side legacy**, not statuses: **in this model** no verb produces them, while every reader still treats them as ended over stored rows (#12855). ⚠️ The code is not yet aligned: `re … *(excerpt — full line quoted in the draft row)* |
| B5-28 | #690, #1088, #1088, #432 | 1190 | B | > **Parity decision (#690, amended #1088):** SDK + EventAPI + CLI share a single status vocabulary (`POINT_STATUS_VALUES` in `sdk.py`). A no-status `create_point` is `live` and an explicit `draft` is honoured and never auto-promot … *(excerpt — full line quoted in the draft row)* |

**Moved whole — the only text that leaves the model in this batch:**

- **C-10 · `#11824`** (class C) — `ONTOLOGY.md:1176`:
  *"⚠️ The code is not yet aligned: `retract_point` **still writes `retracted` today** — a stated
  residual of #11824, not a description of the model."*
  The source says itself that this is *not a description of the model*, which is the definition of
  what does not belong here. **The model statement it contrasted with stays in full**: *"`retracted`,
  `outdated` and `archived` are read-side legacy, not statuses: in this model no verb produces them,
  while every reader still treats them as ended over stored rows."*

### Batch 6 — §6–§12 (37 references)

**§6 and §7 carry none.** One row per source line that carried a reference.

| # | Issue(s) | Line | Class | The verbatim clause it annotated (now here) |
|---|----------|------|-------|---------------------------------------------|
| B6-01 | #12067 | 1256 | B | leg to carry it is the open follow-up on #12067. |
| B6-02 | #12067 | 1291 | B | raising mid-bundle (#12067). "Priced pair" is the unit the FACTOR prices, not the endpoint set |
| B6-03 | #10306 | 1297 | B | mechanism is unreachable there) and the projection's own edge fold (`#10306`). |
| B6-04 | #7813, #7853 | 1303 | B | `:Object`, `:Event` — see §5 on the point↔object/point↔event overlap; #7813/#7853).** |
| B6-05 | #7813 | 1308 | B | > **OVERRIDES:** the pre-#7813 "Point→Event operators are write-only in v1, no |
| B6-06 | #7813 | 1309 | B | > EP propagation" rule — #7813 rules that confidence propagates across |
| B6-07 | #5025 | 1320 | B | - **Neutral vs structural (#5025):** the `Structural edge without mitigation` row above |
| B6-08 | #5566 | 1328 | B | closed by **#5566** (the affected-set traversal is now `IMPL`/`NAND`-filtered **and** |
| B6-09 | #7902 | 1332 | C | nodes (`:Point`/`:Subject`/`:Object`/`:Event` — #7902; no operator); EP propagates over it the |
| B6-10 | #7852 | 1341 | B | operator as `direction_basis` — `explicit` / `semantics` / `default` (#7852 W1). The four-value |
| B6-11 | #753 | 1379 | B | \| `refutes` \| NAND \| `->` \| Negative evidence, one way — the directed attack: the attacker's truth penalizes the target, with no back-pressure (#753) \| |
| B6-12 | #12067, #9963 | 1382 | B | \| `hasPart` \| IMPL \| `<->` \| Composition — the whole REQUIRES its parts, so a part failing should lower the whole and a part being present must not inflate it. ⛔ The mechanism column says `IMPL` because that is the mecha … *(excerpt — full line in the draft row)* |
| B6-13 | #11083, #7848, #11083 | 1384 | B | \| `produces` \| IMPL \| `<-` \| **Production, entity → entity** — *the TARGET brought the source into existence.* Direction `<-` because the declared edge is `asset → program` while production runs producer → product, so `- … *(excerpt — full line in the draft row)* |
| B6-14 | #12067 | 1385 | B | \| `dependsOn` \| IMPL \| `->` \| A precondition — the condition bears on the dependent, one way; the dependent falls when the precondition is absent and is **not** raised by its presence, which is what `coherence` prices. ⛔ … *(excerpt — full line in the draft row)* |
| B6-15 | #2766, #11083, #7848 | 1390 | B | **A word that fails the layer test is a LABEL, not a meaning** — #2766's rule, which #11083 applies. `funds` is the worked example: nothing in the machine changes behaviour because a relation is a funding relation, so it … *(excerpt — full line in the draft row)* |
| B6-16 | #909 | 1434 | B | kindDefs:                     # extractor prompt material (v3, epic #909) |
| B6-17 | #7848 | 1461 | B | > (`subclassOf: requirement: WorkItem`, #7848 owner ruling) — it is not a dev |
| B6-18 | #1891 | 1465 | B | Packs are loaded via the pack registry (`PackRegistry`) at startup; their kinds extend the core vocabulary and are queryable via `expand_kind` / `equivalentTo`. The manifest is **declarative only — no code executes on lo … *(excerpt — full line in the draft row)* |
| B6-19 | #398 | 1473 | B | > **Decision log (v3.2, #398 open question):** temporal decay granularity = **deferred** |
| B6-20 | #580 | 1500 | B | ep.contested in search, #580) |
| B6-21 | #2490 | 1503 | B | **Terminal posterior vacuity decay (#2490):** every terminalizing write |
| B6-22 | #2421 | 1507 | B | frozen pre-terminal posterior. Decay is UNIFORM across #2421 Case-1 |
| B6-23 | #2488 | 1517 | C | **#2488 merge-blocker:** the rebuild-side fold decay applies to the *supersede* |
| B6-24 | #2488 | 1519 | C | with **#2488**, so until it lands an invalidate→rebuild cycle **resurrects the |
| B6-25 | #2490, #2488 | 1520 | C | frozen posterior** (no fold re-applies decay). **#2490** is gated on #2488 and |
| B6-26 | #12855 | 1529 | B | \| Representation of "potentially invalidated"? \| **Elevated posterior variance** (v > 0.04 → contested), not a stored `pointStatus` — statuses are `{draft, live, deprecated, superseded}` (§5), with `retracted`/`outdated` … *(excerpt — full line in the draft row)* |
| B6-27 | #2490 | 1530 | B | \| Terminal claims' posterior after terminalization? \| **Decay to vacuity** (0.5, posterior (1,1)) at the terminalizing write + rebuild fold (#2490) — never a frozen pre-terminal posterior; `ep_alpha`/`ep_beta` retained a … *(excerpt — full line in the draft row)* |
| B6-22b | #86 | 1533 | B | **Direction-aware EP (§3.1, #86) is the prerequisite that makes reverse traversal well-defined.** |
| B6-28 | #398 | 1551 | B | - **Derived values may be CACHED, never authoritative (v3.2, #398):** Source |
| B6-29 | #5358 | 1567 | B | \| **Bi-temporal (Graphiti/Zep)** \| Two orthogonal axes — **valid time** (`validFrom`/`validTo`) and **transaction time** (`createdAt`/`expiredAt`) — plus supersession as a third, separate fact (`supersededAt` on Object; … *(excerpt — full line in the draft row)* |

**Moved whole — the only text that leaves the model in this batch:**

- **C-11 · `#2488` ×2, `#2490`** (class C) — `ONTOLOGY.md:1517`–`:1520`:
  *"**#2488 merge-blocker:** the rebuild-side fold decay applies to the *supersede* fold only. The
  `PointInvalidated` rebuild fold (`_fold_point_invalidated`) ships with **#2488**, so until it lands
  an invalidate→rebuild cycle **resurrects the frozen posterior** (no fold re-applies decay).
  **#2490** is gated on #2488 and adds `decay_clause('n')` to `_fold_point_invalidated`'s SET when it
  lands."*
  ⚠️ **This is a paragraph about shipping order and gating, named a "merge-blocker" by its own
  heading.** Its single rule — *rebuild-side fold decay is supersede-scoped* — is extracted into the
  model and stays. The rest is a work item and lives here.

<!-- PASTE-END -->

## Notes for whoever pastes this

1. **One entry, six batches.** If all six batches are approved together, paste this entry once and
   append the later batches' row tables into it (their headings are `### Batch N — …`, matching
   the shape here). If batches are approved separately, paste one entry per approval and bump the
   version (`v3.39`, `v3.40`, …) — the changelog's convention is one entry per model change, and
   a partial approval *is* a separate model change.
2. **Approval record first.** The `approved by … on ________` line is the load-bearing one for
   `ONTOLOGY.md:41`. Fill it with the owner's approval comment id before (or in the same commit
   as) the model edit lands.
3. **Do not re-summarise the issues.** Rows E-1…E-6 quote the clause and name the issue. If a
   future reader needs what #4937 *said*, they open #4937. Keeping this rule makes the entry
   cheap to write and impossible to get wrong.
