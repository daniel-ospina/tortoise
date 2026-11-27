---
title: "Proposed canonical ONTOLOGY.md — implementation history removed (draft for owner approval)"
type: draft
domain: data
doc_status: draft
created: 2026-11-23
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: ONTOLOGY.md, canonical ontology, ONTOLOGY-CHANGELOG.md
---

# Proposed canonical `ONTOLOGY.md` — implementation history removed

> **This is a PROPOSAL, not the document.** `docs/ONTOLOGY.md` is owner-gated
> (`ONTOLOGY.md:41`: *"no modification to this file is allowed without explicit approval of the
> exact changes by Daniel Ospina"*). Nothing here has been applied. Each batch below is
> **separable text**: any batch — or any single section inside a batch — can be approved on its
> own and the rest deferred or rejected without unpicking anything. The per-batch commits named
> in §6's table are **placeholders** for work that is cut on approval; this branch carries only
> the proposal itself (plus follow-up edits to this document), not six per-batch commits.

## 1. The instruction this draft implements

**Owner instruction (2026-11-23, verbatim):**

> "Ontology.md is unfortunately polluted with issue numbers and changelog when it shouldnt.
> Ontology.md should be simply the canonical ontology (the vision of what should be), and we
> have separate documents for changelog and implementation plans."
>
> "we need to fix that. making ontology canonical on the direction"

## 2. The authority being enforced (the document contradicts itself)

`ONTOLOGY.md:19-24` already declares the rule this draft applies, and the rest of the file
breaks it:

> "**⭐ This document states the model we WANT — not a status report on what is built.** It is
> the canonical target… **The code may lag it**… **Read it as the specification, not as a
> description of the current build.**"

`ONTOLOGY.md:36` states the norm even more precisely — and then explicitly postpones it:

> "**No issue state, no change history.** New prose here names no PR, no issue and no build
> status **as history**… A reference that records **where a term or a decision lives**
> (`validTo`'s registration, `c_cal`) is not history and stays; **replacing the ones that are is
> a separate change**."

**This draft is that separate change.** It is the one the sentence leaves open, not a new
policy.

**Measured (`docs/ONTOLOGY.md` @ `c1c8df1a3817`):** 1,568 lines; **324** tracker references on
**192** lines — 323 of the form `#NNNN` plus one two-digit `#86` (§10.5) that a three-digit
measurement misses. Worst offenders, by section: §4.6 Source (46), §5 (40), §4.7 Temporal (36),
§4.1 Point (21), §8 (21), §3.2 (20), §4.3 Object (20), §4.2 Subject (19), §3.1 (16), §4.5
Event (15), §3.9 (13), §4.4 (10), §10.5 (10).

## 3. Defaults chosen for this draft — correct any of them and the diff changes with it

| # | Default | Why it is a default and not a finding |
|---|---------|--------------------------------------|
| **D-1** | **This is its own approval request**, separate from **#8745** (which is specifically the #8738 approval). Approving this draft does not approve #8745, and vice versa. | Two different diffs. Bundling them would make the approval record ambiguous, which is the one thing an owner gate cannot afford. |
| **D-2** | **Class B provenance lands in `docs/ONTOLOGY-CHANGELOG.md`**, as a new entry (`v3.39`), not a new file. | The changelog is already the declared home (`ONTOLOGY.md:30` — *"The per-field **build record** and the history of **what changed and why** live in `ONTOLOGY-CHANGELOG.md`"*). A third document would be a second home for one thing. |
| **D-3** | **Target: strictly ZERO `#NNNN` in the model**, with **one** header pointer telling the reader that `ONTOLOGY-CHANGELOG.md` holds provenance. | A "some refs are fine" rule is unenforceable and re-pollutes within one lane. Zero is checkable (`grep -c '#[0-9]\{3,5\}'`) and the pointer keeps the reader oriented. |

Two further defaults this draft had to take, stated because they are not free:

| # | Default | Consequence if wrong |
|---|---------|----------------------|
| **D-4** | **Version bumps to `v3.39`**, and the changelog carries **one entry per model change** — the shipped extract is a single `v3.39` entry carrying one `### Batch N` subsection per batch. | If the batches are approved separately rather than together, each partial approval is its own model change and gets its own entry (`v3.39`, `v3.40`, …) — no model text changes, only the entry split. |
| **D-5** | **A rule stays even when the sentence carrying it is nearly all implementation detail.** The rule is restated in canonical prose and the detail moves. Only a sentence with *no* rule in it moves whole. | The alternative — moving whole sentences whenever they look technical — is how rules get lost. Every irreversible call of this kind is listed in that section's *Moved whole* row, so it can be reversed individually. |
| **D-6** | **Bare dates are OUT of scope.** This change removes `#NNNN` references and the clauses that report build status or provenance; it does **not** remove a year/month/day that *names a model state* (e.g. "state-centric alignment (2026-08-12)"). | Stated because the owner's instruction named "issue numbers and changelog" — a date sweep is a larger change with a much larger diff, and is **not** in this proposal. Widen it and batch 2–6 double in size; say so and it gets its own batch. |

## 4. Classification policy applied, per occurrence

| Class | What it is | Where it goes |
|---|---|---|
| **A** | **The rule itself** — e.g. "§6 is owner-gated", "a mitigation is not an operator kind" | **STAYS.** The issue number is dropped; the rule text is unchanged. |
| **B** | **Provenance / authority** — e.g. "(#7813 owner ruling, implemented by #7853)", "since #3644", "#2193 resolved" | **Moves to `docs/ONTOLOGY-CHANGELOG.md`.** The rule it annotated stays. |
| **C** | **Implementation status** — names a private function, a file, a test, or reports what the build does or does not do yet | **Moves out of the model** (to the changelog here; a code docstring or a plan doc where that is its better home). |
| **D** | **Open-issue pointer** — "filed as #NNNN", "tracked at #NNNN", "gated on #NNNN" | **Tracker only. Removed.** The model does not carry a work queue. |

**Rule of thumb, applied literally:**

> *Would this sentence still be true if every issue were deleted and every PR forgotten?*
> **Yes → Class A: keep it, drop the number. No → it moves.**

Cross-references **inside** this document (`§3.9`, `§8 OVERRIDES`) are **not** touched: they are
the model's own structure, carry no issue number, and survive the test above.

## 5. What "do not lose meaning" means concretely here

Removing a number is safe. Removing **the rule** a number annotated is not. So every batch
carries three artefacts, and a section is not proposed unless all three are complete:

1. **A per-section table** — refs before / removed, classes used, the normative count before →
   after, and anything unclassifiable.
2. **A sentence-level disposition table** — every sentence that carried a ref, its class, and
   what happened to its semantic content.
3. **A normative-statement inventory** — every rule in the section, marked *kept* or *moved*.
   **No rule may be moved**: classes B/C/D move *citations and status*, never a rule. Where a
   sentence bundles a rule with status, the rule is restated and the status moves (default
   D-5), and the restatement is shown in the disposition row.

### The mechanical metric (D1) — and why it cannot stand alone

**D1** = (occurrences of `must|shall|required|always|never`, case-insensitive, word-boundary) +
(count of fenced ```` ```text ```` declared-vocabulary blocks). It is deliberately crude and
deliberately reproducible:

```bash
python3 - <<'PY'
import re,sys
seg=open(sys.argv[1]).read()
print(len(re.findall(r'\b(must|shall|required|always|never)\b',seg,re.I)),
      len(re.findall(r'^```text$',seg,re.M)))
PY
```

**D1 is a cross-check, never the guarantee.** A section can keep every rule and lose no D1
token, or keep every D1 token and lose a rule (`§3.3` scores **0** and still carries a
normative statement). The **inventory** is the guarantee; D1 is what catches a silent mass
deletion. Every D1 delta is therefore named, never merely reported.

⚠️ **Extracting a PROPOSED block when it contains its own fences.** §3.9 declares the predicate
vocabulary in a ```` ```text ```` block, so the §3 proposal is wrapped in **four** backticks
(````markdown …````) rather than three. A reader re-running the extraction with a three-backtick
regex will silently get a **truncated** §3 ending at the vocabulary heading — which would make
the D1 figure look like 22 instead of 30. Use the four-backtick form for §3 (and for any later
batch whose section contains a fenced block).

## 6. How to approve this — per batch, per section

Each batch becomes its own commit **on approval**. Each section inside a batch is separately
approvable and carries this line:

> **Approve:** ☐ text as written · ☐ with changes (list them) · ☐ reject (section keeps its numbers)

**Rejecting one section does not block the others** — the batches are independent, and a batch
whose sections are partly rejected is simply re-cut. Nothing is applied to `ONTOLOGY.md` until
the owner's approval of the exact text is recorded.

| Batch | Planned commit (placeholder) | Sections | Refs | State |
|---|---|---|---|---|
| 1 | `batch 1` | header, §1, §2 | 9 | **proposed** |
| 2 | `batch 2` | §3.1–§3.9 | 63 | **proposed** |
| 3 | `batch 3` | §4.1–§4.4 | 70 | **proposed** |
| 4 | `batch 4` | §4.5–§4.8 | 105 | **proposed** |
| 5 | `batch 5` | §5 | 40 | **proposed** |
| 6 | `batch 6` | §6–§12 (+§10.5) | 37 | **proposed** |
| — | — | **total** | **324** | see **Final state** at the end |

⛔ **The `Planned commit` column is a placeholder, not a sha.** No per-batch commit exists on
this branch — it carries the proposal (plus follow-up edits to this document) and no per-batch
commits — and the six batches are cut as six commits only when, and if, they are approved.

---

## Batch 1 — the header, §1, §2

### 1.1 Per-section table

| Section | `ONTOLOGY.md` lines | Refs before | Refs removed | Classes | D1 before → after | Rules moved | Unclassifiable |
|---|---|---|---|---|---|---|---|
| Header + frontmatter | 1–43 | 0 | 0 | — | 1 → 2 | 0 (+1 **added**) | none |
| §1 Entity Types | 45–60 | 0 | 0 | — (no change) | 0 → 0 | 0 | none |
| §2 Four-Ontology Model | 61–108 | 9 | **9** | A 2 · B 5 · C 2 | 4 → 4 | 0 | none |

Header and §1 carry no `#NNNN`, so the header's only changes are the **one** changelog pointer
**D-3** requires and the **`v3.39` version bump in both places it is spelled** — the H1 and the
frontmatter `title:`/`updated:` lines, which are part of the exact change
(`ONTOLOGY-CHANGELOG.md:585` records that this document's CRITICAL RULE gates its own
version/`updated:` lines). **§1 is proposed verbatim: it needs no change at all**, and saying so
is part of the honest accounting.

> ⛔ **The tail of `ONTOLOGY.md:36` carries one rule this change has to remove, so it needs an
explicit decision, not a default** — see §1.8. The source says a *location* reference (`validTo`'s
> registration, `c_cal`) *"is not history and **stays**"*; D-3's strict-zero target removes it.
> That is a **policy change on the one category the current header protects**, and it is
> proposed, not assumed.

### 1.2 Sentence-level dispositions — §2 (9 occurrences)

Exhaustive: these **nine** rows are every `#NNNN` in lines 61–108 (re-measured per line, see
§1.7 for the command).

| # | Line | Sentence / clause as it stands (ref in **bold**) | Class | What happens to the semantic content |
|---|---|---|---|---|
| 1 | 68 | "the `:Subject`/`:Object` operator endpoints are live (**#7902**); the write path admits all four." | **C** | *Status* ("are live") removed. The rule — **the write path admits all four epistemic labels** — is kept verbatim in the same sentence. |
| 2 | 68 | "**`MITIGATES` is not an operator kind** (**#4937**)" | **A** | Rule kept verbatim; number dropped. |
| 3 | 68 | "**Point→Event operators propagate EP under the #7813 ruling** — confidence crosses all four labels, so an Event target is epistemic (§8 OVERRIDES)" | **B** | Rule kept; "under the #7813 ruling" → no attribution. The ruling's *content* is already in the sentence ("confidence crosses all four labels"), so nothing is lost. Provenance → changelog E-1. |
| 4 | 70 | "**a pack's per-kind `kindDefs[].statuses`/`folds` declaration for its own kinds — #2729**" | **A** | Rule kept verbatim; number dropped. |
| 5 | 70 | "folded keep-first per Object (divergent re-folds never blind-overwrite — **#2193 resolved**)" | **B** | Rule kept **verbatim character-for-character** ("divergent re-folds never blind-overwrite", "keep-first"); only " — #2193 resolved" is deleted. Provenance → changelog E-2. |
| 6 | 80 | "A **mitigation** is NOT a third operator kind (**#4937**, …)" | **B** | Rule kept verbatim; number and the "F1 ruling" attribution dropped. Provenance → changelog E-1. |
| 7 | 80 | "…the F1 ruling on **#2552**" | **B** | Second ref in the same parenthetical; both go together. The *substance* (a mitigation is not an operator kind) is row 6's rule and stays. Provenance → changelog E-3. |
| 8 | 103 | "both propagate EP under **#7813** (all four epistemic labels; the Point→Event direction is no longer write-only — §8 OVERRIDES)" | **B** | "under #7813" dropped; "no longer write-only" → **"is not write-only"** (the same rule in the model's tense; "no longer" is history). §8 `OVERRIDES` pointer kept — that is structure, not history. |
| 9 | 105 | "The `:Subject`/`:Object` operator endpoints are live (**#7902**); the write path admits all four." | **C** | Same as row 1: status removed, rule kept verbatim. |

### 1.3 Normative-statement inventory — §2

Every rule in §2, with its fate. **Moved rules: 0.**

| Rule | Fate |
|---|---|
| All four ontology layers are live mechanisms | kept, untouched |
| Operators connect epistemic targets `:Point`/`:Subject`/`:Object`/`:Event`; the write path admits all four | kept verbatim |
| `MITIGATES` is not an operator kind; a mitigation is a Point on the operator bridge it damps | kept verbatim |
| Point→Event operators propagate EP; confidence crosses all four labels; an Event target is epistemic (§8 OVERRIDES) | kept verbatim |
| Decision semantics remain on the Event timeline; decisions are not first-class Points | kept verbatim |
| `Object.status` is a write-through cache of lifecycle events (registered→live, superseded→superseded+`supersededBy`, connector work-item events→in_progress/completed) | kept verbatim |
| A pack's per-kind `kindDefs[].statuses`/`folds` declaration governs its own kinds | kept verbatim |
| The journal/event stream is the reconstruction source for `Object.status`; status is a performance cache | kept verbatim |
| Folding is keep-first per Object; divergent re-folds never blind-overwrite | kept verbatim |
| A kind that declares a lifecycle owns its state; the built-in connector fold does not apply to it (namespace dispatch) | kept verbatim |
| `open`/`closed` cannot be silently re-spelled by the connector vocabulary | kept verbatim |
| Point kinds `decision`/`vision`/`strategy`/`plan`/`goal`/`target`/`observation`/`hypothesis`/`humanApproval`/`event` are LEGACY write kinds; extraction emits `statement` only | kept verbatim |
| `pointKind: statement` is the only extraction point kind | kept verbatim |
| A mitigation weakens a relationship's relevance and contributes no confidence of its own | kept verbatim |
| `w_eff = w × (1 − strength)` (§3.9) | kept verbatim |
| The graph says "this state is based on these reasons", never "this decision was made because of these reasons" | kept verbatim |
| The decision dimension stays queryable as a timeline (events) but decisions are not first-class Points | kept verbatim |
| State confidence is derived at read time from attached Points' EP confidence, never stored independently on the Object | kept verbatim |
| Structural edges carry no confidence; epistemic edges carry EP confidence | kept verbatim |
| `:Subject`/`:Object` may also connect via plain structural edges | kept verbatim |
| Evaluations of subjects (expertise, reliability) are Statements with EP confidence, not edges; reputation is derived at query time | kept verbatim |
| `confidence` is EP's OUTPUT, not an input; EP seeds from `ep_alpha`/`ep_beta` and overwrites `n.confidence` every run; ranking reads the persisted value as `graph_boost` | kept verbatim |

**D1: 4 → 4.** All four tokens are `never`, at source lines **`:70`** ("never blind-overwrite"),
**`:75`** ("never the truth itself"), **`:88`** ("never *this decision was made…*") and **`:95`**
("never stored independently"). No token changes hands, because every sentence that lost a
citation kept its words — which is the whole point of classes A and B: *the number goes, the
sentence stays.*

**Header D1: 1 → 2, delta +1, and the delta is an ADDED rule, not a lost one.** The single
source token is `never` at **`:26`** ("never a licence to treat the model as wrong"). The added
token is the `never` in the D-3 pointer rule ("New prose here names a location by section or by
code symbol, and **never** an issue"). It is called out here, in §1.1, and on the approval line so
it cannot be mistaken for a removal.

The two sentences the header **gains** — D-3's changelog pointer and `P-3`'s naming rule — are
deliberately **scoped to new prose**, and the scope is load-bearing, not stylistic. `D-3`'s other
half is NOT scoped: its strict-zero target for the 323 existing `#NNNN` is the whole-model goal that
the batches below carry out (`:63`, and §1.8's `P-1`). The neighbouring `as history` clause is
guarded the same way (§1.4's change list, item 3) and for the same reason: an unconditional rule
would be false in every shape where a batch has not been approved. An unscoped version of the header
rule would also contradict the `ONTOLOGY.md:36` carve-out in the shape §F.3 offers as the
minimal-risk landing — **all six batches with `P-1` and `P-4` rejected**, where **18** references
stay. Scoped, `P-3` and `P-1` are independent: `P-1` decides whether the *existing* location refs
survive, `P-3` decides what *new* prose may mint.

**Deliberately NOT counted, and stated so it cannot look like an oversight:** the header also
gains *"Anything that records when something was decided… belongs in
`ONTOLOGY-CHANGELOG.md`"*, which uses no D1 token but is normative. D1 is a cross-check, not a
guarantee (§5) — this sentence is the reason that caveat exists.

### 1.4 Proposed text — header + frontmatter (replaces `ONTOLOGY.md` lines 1–43)

Changes, exhaustively — the reviewer of batch 1 found four undisclosed edits in the first cut of
this section, and each is now either restored or named here:

1. **Version → `v3.39` in BOTH places it is spelled**: the frontmatter `title:` (line 2) and the
   H1 (line 14), plus `updated: 2026-11-17` → `2026-11-23` (line 7). These lines are part of the
   exact change the owner approves — this document's CRITICAL RULE gates its own version and
   `updated:` lines (`ONTOLOGY-CHANGELOG.md:585`).
2. **ONE changelog pointer added** (D-3), inside the existing *"No issue state"* paragraph so the
   header does not grow a second place where the same thing is said twice.
3. **The `as history` qualifier is KEPT** — restoring the source wording. It is what makes the
   sentence true while batches are approved one at a time: the remaining `#NNNN` in an
   not-yet-approved section are still there, and the rule is a constraint on prose, not a
   claim that no number exists.
4. **`This file is the model.` and `What belongs here is only *what the model is*.` are KEPT**
   verbatim. They are the header's scope statements and the first cut deleted them without a
   disposition row; that is exactly the failure this proposal exists to avoid.
5. **The "location references stay" carve-out is REPLACED by a strict-zero rule** — a real
   **policy change** on the one category the current header protects. It is not a default and
   not a tidy-up: it is proposed for approval in **§1.8, P-1**, and the owner can keep the
   carve-out by rejecting that one line.

Everything else is verbatim, including line 41's CRITICAL RULE and the *"THIS DOCUMENT IS
RIGHT"* authority.

<!-- PROPOSED:header -->
```markdown
---
title: "Tortoise — Canonical Ontology v3.39"
type: data
domain: data
status: live
created: 2026-08-05
updated: 2026-11-23
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
doc_status: live
---

# Tortoise — Canonical Ontology v3.39

> **Status:** LIVE — canonical. Co-located with the code it governs (tortoise repo).
> **Supersedes:** ONTOLOGY_v2.5.md (eldato repo, deprecated).
>
> **⭐ This document states the model we WANT — not a status report on what is built.**
> It is the canonical target: the shape the system is being brought to. **The code
> may lag it, and implementation gaps may exist temporarily** — a field the model
> declares but nothing writes yet, a label the model retires but the code still
> emits, a rule stated but not yet enforced.
>
> **Read it as the specification, not as a description of the current build.** A
> gap between this document and the code is a **work item**, never a licence to
> treat the model as wrong or the code as authoritative — the code is what gets
> changed. Conversely, do not read a capability here as present-tense fact. The
> per-field **build record** and the history of **what changed and why**
> live in **`ONTOLOGY-CHANGELOG.md`** — not here. This file is the model.
>
> **⭐ If this document and the code disagree, THIS DOCUMENT IS RIGHT and the code
> has a defect.** The single exception is a *factual* error — the model itself
> being wrong — which is corrected here and recorded in `ONTOLOGY-CHANGELOG.md`.
>
> **No issue state, no change history.** New prose here names no PR, no issue and no build status **as history**: a reference to *when* something was decided, fixed or landed belongs in `ONTOLOGY-CHANGELOG.md`, and so does a sentence reporting the state of the build. What belongs here is only *what the model is*. **New prose here names a location by section or by code symbol, and never by an issue.** `ONTOLOGY-CHANGELOG.md` is the single home for provenance; anything this document still carries inline is what no batch has moved yet.

**Convention:** camelCase throughout. `kind` = classification tag on an entity. `predicate` = named edge between entities. Capture-path/pipeline fields keep their code spelling (snake_case, or a leading-underscore internal field) — e.g. `file_hash`, `is_episodic`, `c_cal`, `passes_frequency_gate`, `provenance_spans`, `_searchText`.

---
CRITICAL RULE: no modification to this file is allowed without explicit approval of the exact changes by Daniel Ospina.

---
```

> **Approve:** ☐ text as written · ☐ with changes (list them) · ☐ reject (header keeps its wording)

### 1.5 Proposed text — §1 Entity Types (lines 45–60): **UNCHANGED**

**Proposed: no change.** §1 carries zero `#NNNN` references and zero build status. It already
reads as the model. Approving this row is approving that §1 is left exactly as it is.

> **Approve:** ☐ §1 unchanged · ☐ §1 has changes I want (list them below)

### 1.6 Proposed text — §2 Four-Ontology Model (replaces lines 61–108)

<!-- PROPOSED:§2 -->
```markdown
## §2. Four-Ontology Model

Each layer answers a different question. All four are live mechanisms.

| Layer | Question | Entity | How it works |
|-------|----------|--------|--------------|
| **Semantic** | Who/what exists? | Subject, Object, Source (**incl. documents**) | Nouns. Standing structural relations (ownedBy, memberOf, hasPart) via plain edges. |
| **Epistemic** | What do we believe and why? | Point, Operator (IMPL/NAND + label + EP confidence), and the epistemic targets `:Subject`/`:Object`/`:Event` (§5 — objects ARE in the epistemic layer) | Operators connect epistemic targets (`:Point`, `:Subject`, `:Object`, `:Event`) — the write path admits all four. Belief strength = EP confidence, computed by propagation. **`MITIGATES` is not an operator kind**: a mitigation is a Point attached to the operator bridge it damps (`(op {is_operator:true})-[:mitigated_by]->(m)`, §3.9) — it weakens a relationship's relevance, it is not a peer operator. **Point→Event operators propagate EP — confidence crosses all four labels, so an Event target is epistemic (§8 OVERRIDES); decision semantics remain on the Event timeline, and decisions stay non-first-class Points.** |
| **Episodic** | What happened when? | Event | Verbs. Append-only, timestamped. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object). |
| **Procedural** | What is the current state of work? | Event + folded Object status | **Object.status is a write-through cache of lifecycle events** (ObjectRegistered→live; ObjectSuperseded→superseded + `supersededBy`; built-in connector work-item events→in_progress/completed; **a pack's per-kind `kindDefs[].statuses`/`folds` declaration for its own kinds**) — the journal/event stream is the reconstruction source for `Object.status` (§11), status is a performance cache, folded keep-first per Object (divergent re-folds never blind-overwrite). A kind that declares a lifecycle OWNS its state: the built-in connector fold does not apply to it (namespace dispatch on `ns:kind`), so `open`/`closed` cannot be silently re-spelled by the connector vocabulary (`tortoise/status_folds.py`). |

> **State-centric model (core hypothesis, 2026-08-12 — the graph stores STATE, not decisions):**
> The record is three layers. **State** — Objects/options carry their lifecycle
> (promoted/deprecated/superseded — the Episodic layer's events are the truth;
> status is a fold cache over the events — never the truth itself) and their **confidence** (derived from the
> attached Points). **Points** — the logic: statements (pointKind `statement` —
> the only extraction point kind; hypothesis folded into confidence) connected
> to the state they argue about (aboutObject); the IMPL/NAND **operators**
> among them move the object's confidence. A **mitigation** is NOT a third
> operator kind: it is a Point that attaches
> to the operator bridge it damps — `(op {is_operator:true})-[:mitigated_by]->
> (m)` — weakening the relationship's relevance by
> `w_eff = w × (1 − strength)` (§3.9). It is therefore not a peer in the
> operator menu, and it contributes no confidence of its own. **Events** — what
> happened, for
> context: occurrences AND the **decision-as-event** (eventKind `decision`,
> aboutObject → the object(s) it resolved). The graph says *"this state is
> based on these reasons"* — never *"this decision was made because of these
> reasons"*. The decision dimension stays queryable as a timeline (events),
> but decisions are NOT first-class Points. Point kinds `decision`/`vision`/
> `strategy`/`plan`/`goal`/`target`/`observation`/`hypothesis`/`humanApproval`/
> `event` are LEGACY write kinds (§5) — extraction emits `statement` Points only,
> Event nodes, and
> lifecycle writes on Objects. State confidence is derived at
> read time from the attached Points' EP confidence (§11) — never stored
> independently on the Object.

### Structural vs Epistemic Edges

| Edge | Type | Confidence | Example |
|------|------|-----------|---------|
| performs / produces / uses / authoredBy / ownedBy / memberOf / managedBy | **Structural** (plain) | None (factual) | (:Subject "Daniel")-[performs]->(:Event), (:Object "Customer Profile")-[ownedBy]->(:Subject "Daniel") |
| Any of `:Point`/`:Subject`/`:Object`/`:Event` ↔ same (e.g. Event↔Point, Point↔Point) | **Epistemic** (operator) | EP confidence | (Event:deployFailed)-[NAND]->(Point:"deploy succeeded") · (Point:"argument for X")-[IMPL]->(Event:decision-on-X) — both propagate EP (all four epistemic labels; the Point→Event direction is not write-only — §8 OVERRIDES) |

**Principle:** Operators connect epistemic targets (any of `:Point`/`:Subject`/`:Object`/`:Event` — see the §8 `OVERRIDES`); `:Subject`/`:Object` may **also** connect via plain structural edges. The write path admits all four. Evaluations of subjects (expertise, reliability) are Statements (Points) with EP confidence — not edges. Reputation is derived at query time. **`confidence` is EP's OUTPUT, not an input** — EP seeds from its own priors (`ep_alpha`/`ep_beta`) and overwrites `n.confidence` on every run, while ranking reads the persisted value as `graph_boost`; a caller who writes `confidence` asserts nothing about belief, because the next propagation replaces it.

---

```

> **Approve:** ☐ text as written · ☐ with changes (list them) · ☐ reject (§2 keeps its numbers)

### 1.7 Anything not classifiable

**Nothing in Batch 1 is unclassifiable.** The exhaustive per-line measurement is reproducible:

```bash
python3 - <<'PY'
import re
lines=open('docs/ONTOLOGY.md').read().split('\n')
ref=re.compile(r'#(\d{3,5})(?![0-9])')
for i in range(61,109):
    ms=ref.findall(lines[i-1])
    if ms: print(i, ms)
PY
# → 68 ['7902','4937','7813'] · 70 ['2729','2193'] · 80 ['4937','2552'] · 103 ['7813'] · 105 ['7902']
```

Two judgement calls are stated rather than buried:

1. **"are live (`#7902`)" (×2) — classified C, not A.** The words *"are live"* assert build
   status; *"the write path admits all four"* is the rule. Only the status clause was removed.
   If the owner reads the liveness itself as a rule, the restored sentence is
   *"Operators connect epistemic targets; the write path admits all four, and the
   `:Subject`/`:Object` endpoints are admitted"* — still no number needed.
2. **"the Point→Event direction is no longer write-only" → "is not write-only"** (row 8). The
   only place in Batch 1 where wording changed rather than a citation being deleted. "No
   longer" is a statement about the past (class B history); "is not" is the same rule in the
   model's tense. **Flagged so it can be rejected on its own.**

### 1.8 Policy changes this draft asks for — decisions, not defaults

These are **not** covered by the four defaults in §3. Each one changes the model's rules rather
than removing a citation from them, and each is separately rejectable.

| # | The change | Why it needs a decision | Rejecting it means |
|---|-----------|------------------------|--------------------|
| **P-1** | **The carve-out at the tail of `ONTOLOGY.md:36` is removed.** The source says a reference recording *where a term or decision lives* (`validTo`'s registration, `c_cal`) *"is not history and **stays**"*. D-3's strict-zero target removes that category too. | The source header protects this category explicitly. A change that removes it is a **policy change**, not an application of the existing rule — the header's own sentence even calls the removal of history refs *"a separate change"* while marking location refs as out of scope. Presenting this as "the change the sentence leaves open" would misrepresent it. | **Restore the carve-out**: location refs (`#909 §4.3 #11` at `:405`, `#909 §4.3 #10` at `:398`) stay, and the model keeps **17** references (the `#NNNN §x.y` form: 16 × `#909 §4.3`, 1 × `#5013 §4.4`). The rest of this proposal is unaffected, and **`P-3` does NOT have to be rejected with it**: `P-3`'s rule is scoped to *new prose*, so it governs what the document mints from here on and says nothing about the 17 pointers the carve-out keeps. |
| **P-2** | **`v3.38` → `v3.39`, in the frontmatter `title:` and the H1, with `updated:` → `2026-11-23`.** | Those lines are part of the exact change the owner approves (`ONTOLOGY-CHANGELOG.md:585`), and a proposal that bumps one and not the other leaves the file asserting two versions. | Keep `v3.38`; the diff then contains no version change and the changelog entry records the model change without a version number. |
| **P-3** | **The header gains one normative sentence** — new prose names a location by section or by code symbol, never by an issue — plus the single changelog pointer. The sentence is scoped to *new prose* on purpose: unscoped, it would contradict `P-1`'s carve-out in the shape §F.3 recommends (all six batches, `P-1` and `P-4` rejected — 18 refs stay). | It is the only place in this proposal where the model **gains** a rule, and additions to an owner-gated document deserve their own line. | The pointer stays in the changelog and this file names no rule about naming; the remaining batches then have no header to lean on for "no numbers, ever". |

**Nothing else in Batch 1 changes a policy.** If P-1, P-2 and P-3 are all rejected, Batch 1
reduces to: nine citations removed from §2, the header untouched, and §1 untouched.

---

## Batch 2 — §3.1–§3.9 (Edge Topology)

### 2.1 Per-section table

| Section | lines | Refs before | Refs removed | Classes | D1 before → after | Rules moved | Unclassifiable |
|---|---|---|---|---|---|---|---|
| §3.1 Operators | 109–148 | 16 | **16** | A 0 · B 13 · C 1 · D 2 | 12 → 11 | 0 | none |
| §3.2 Point ↔ Entity | 149–169 | 20 | **20** | A 1 · B 17 · C 1 · D 1 | 5 → 5 | 0 | none |
| §3.3 Point → Source | 170–175 | 1 | **1** | B 1 | 0 → 0 | 0 | none |
| §3.4 Source → Entity | 176–185 | 5 | **5** | B 5 | 3 → 3 | 0 | none |
| §3.5 Subject → Event → Object | 186–198 | 2 | **2** | B 2 | 0 → 0 | 0 | none |
| §3.6 Subject ↔ Subject | 199–209 | 1 | **1** | C 1 | 0 → 0 | 0 | none |
| §3.7 Object ↔ Object | 210–216 | 0 | 0 | — (no change) | 0 → 0 | 0 | none |
| §3.8 Event Edges | 217–236 | 5 | **5** | B 4 · C 1 | 1 → 1 | 0 | none |
| §3.9 Valid Predicate Vocabulary | 237–389 | 13 | **13** | B 13 | 10 → 10 | 0 | none |
| **Total** | — | **63** | **63** | A 1 · B 55 · C 4 · D 3 | 31 → 30 | **0** | none |

**D1 counts the one declared-vocabulary block in §3.9 too** (definition in §5), so the batch runs
31 → 30, not 30 → 29. **The single D1 delta is a moved build-status statement, not a lost
rule.** §3.1 goes 12 → 11
because one removed clause contained the word `never`: *"Full `about*` parity additionally depends
on #2501 (`create_point` never live-wires `aboutEntities` — the only lane where 2b sees live
`about*` edges is rebuild→supersede→rebuild)."* That is a statement about what the code does not
yet do (class **C**, a build gap) attached to an open issue (class **D**). The rule it gestured at
— *the structural-edge descriptor set has two families* — is stated in full immediately above it
and stays. **No normative rule leaves the model.** It is row `2-M1` below.

**§3.7 carries no references and is proposed unchanged**, like §1 in batch 1.

### 2.2 Dispositions — §3.1 (16)

| # | Ref(s) | Line | Class | What happened to the semantic content |
|---|---|---|---|---|
| 3.1-a | `#7902` | 113 | **C** | *"The `:Subject`/`:Object` operator endpoints are live (`#7902`); the write path admits all four."* → **"The write path admits all four epistemic targets."** Liveness is build status; the admitted-label rule is kept and made explicit by adding "epistemic targets". |
| 3.1-b | `#753` | 118 | **B** | *"`refutes` records `->` (the directed attack — attacker's truth penalizes the target, no back-pressure)"* — rule kept verbatim, citation dropped. |
| 3.1-c | `#2489` | 124 | **B** | Heading *"journaled + replayable (#2489)"* → *"journaled + replayable"*. The whole replay contract stays. |
| 3.1-d | `#9836` ×3 | 124 | **B** | Three occurrences: the ID-ADDRESSED family's name, and *"so before `#9836` they moved live and journalled nothing"*. The historical clause is **restated in the model's tense** (see 2.6, W-1): *"transferred by 2b but NOT snapshot-derivable, so a descriptor is the only thing that carries them on the journal route: without one, the journal loses the edge while the record keeps its pre-supersede row."* Rule preserved; the *history* of when it became true moved. |
| 3.1-e | `#9836` | 124 | **B** | *"that conflation is the defect `#9836` closed"* — deleted; the surviving clause *"Membership of the derivable set is not what decides whether a rel is journalled; the deciding pair is `STRUCTURAL_REPOINT_RELS = …`"* carries the whole rule. |
| 3.1-f | `#9972`, `#9973`, `#10191` | 124 | **B** | *"The RECORD applies a 2b descriptor as the same MOVE (#9972, #9973, #10191)"* → same sentence, no numbers. The DELETE-then-INSERT contract is untouched. |
| 3.1-g | `#5509` | 124 | **B** | *"landing NOTHING when a name is held by two or more carriers"* — rule kept. |
| 3.1-h | `#9417` | 124 | **B** | *"(`STORAGE-ARCHITECTURE.md` §13, #9417)"* → *"(`STORAGE-ARCHITECTURE.md` §13)"*. The cross-document pointer stays (it is structure); the issue does not. |
| 3.1-i | `#2498` | 124 | **B** | *"`supersede_point` refuses the closing hop — `#2498`: a dead target cannot be re-terminalized"* → same rule, citation dropped. |
| 3.1-j | `#2500` | 124 | **D** | *"a #2500-style backfill is out of scope"* → *"a backfill is out of scope"*. Scope statement kept; issue pointer removed. |
| 3.1-k | `#909` | 143 | **B** | *"(epic #909 §4.3 #5 / research addendum §1 — pipeline spec)"* → *"(pipeline spec)"*. The three canonical NAND-direction rules below the heading are untouched. |
| 2-M1 | `#2501` | 124 | **C + D** | ⚠️ **Sentence moved whole** — *"Full `about*` parity additionally depends on #2501 (create_point never live-wires `aboutEntities` — the only lane where 2b sees live `about*` edges is rebuild→supersede→rebuild)."* It states a build gap and names its tracker; it carries no model rule (the two-family rule it refers to is stated in the preceding clause). **This is the batch's only removed `never`.** Changelog `B-1`. |

### 2.3 Dispositions — §3.2 (20)

| # | Ref(s) | Line | Class | What happened to the semantic content |
|---|---|---|---|---|
| 3.2-a | `#2489` | 159 | **B** | *"(the `#2489` replay rules are in §3.1)"* → *"(the replay rules are in §3.1)"*. The `aboutDocument`/`aboutSource` distinction — the actual rule — is untouched. |
| 3.2-b | `#391` ×2 | 160, 161 | **B** | *"Creatable via `create_edge` (#391)"* / *"creatable only via explicit `create_edge` (#391)"* → same, no numbers. |
| 3.2-c | `#3856` | 162 | **B** | *"A user-authored Tag is first-class DATA, not a derived label — #3856 (OD-4) settled that by what a Tag is"* → *"… — settled by what a Tag is"*. The rule and the rationale both stay; only the attribution goes. |
| 3.2-d | `#2897` ×2 | 162 | **B** | *"tags survive `rebuild_all` (#2897, objective 6 "props, tags and EP state survive")"* → *"… (objective 6: "props, **tags** and EP state survive")"*; and *"its fix is NOT retroactive for graphs seeded before #2897"* → *"… for previously seeded graphs"*. Both rules kept. |
| 3.2-e | `#2489` | 162 | **B** | *"(the `#2489` split, like `extractedFrom`)"* → *"(the same split, like `extractedFrom`)"*. |
| 3.2-f | `#7369` | 162 | **B** | *"a value the store cannot hold degrades to NO property (#7369)"* — rule kept, citation dropped. |
| 3.2-g | `#8333` | 162 | **D** | *"the difference is filed as **#8333** (with the one-line fix, in `_sync_tags`, that closes it)"* → *"the difference is a known divergence between the engines (one-line fix, in `_sync_tags`)"*. The three-engine behaviour (live keeps the edge; `rebuild_all` drops it; `rebuild(EventLog)` keeps it) is **untouched** — this is a filed-defect pointer, not a rule. |
| 3.2-h | `#8325` | 162 | **C** | ⚠️ **Sentence replaced, model statement promoted** (see 2.6, W-2). *"⚠️ Doc-vs-code gap, filed as **#8325**: the node is created as `MERGE (t:Tag {name:$tag})`, so it carries no `:Object` label and no `objectKind` — the Object-subclass characterisation above is the intended model, not the implemented one."* → **"⚠️ A Tag is an `:Object` subclass carrying `objectKind: tag` — that is the model; a store that mints the node without those labels is a build gap, not a different model."** The model claim is *promoted* out of the gap report; the gap report itself moves to changelog `B-2`. |
| 3.2-i | `#1727`, `#3664` | 166 | **B** | *"**Session as an aboutObject source (#1727 Slice 2, Task 12; SDK parity + durability #3664):**"* → *"**Session as an aboutObject source:**"*. Every capture-path rule below it is untouched. |
| 3.2-j | `#3664`, `#4282` | 166 | **B** | *"**#3664:** the SDK `capture_session` ran the same pass as hosted `_capture_session_impl` (it previously did not — a self-hosted capture was an unattached island). **#4282 retired that PUBLIC name**…"* → *"The SDK runs the same pass as the hosted lane. The public name is retired; the body survives as the private `TortoiseSDK._capture_session` … so the pass runs on BOTH lanes."* Rule (both lanes run the pass) kept; the defect history and both numbers dropped. |
| 3.2-k | `#4240`, `#2296`, `#3664` | 166 | **B** | *"the base dir wires a per-graph `event_log_path` when set, #4240"* → no number; *"the edges stay live-only (#2296)"* → no number; *"`SessionRecorded` (#3664)"* → no number. All three rules kept. |
| 3.2-l | `#1417` | 168 | **A** | *"**`aboutEvent` is content-only (#1417):**"* → *"**`aboutEvent` is content-only:**"*. The rule itself. |
| 3.2-m | `#4282`, `#1417` | 168 | **B** | *"(its public name retired by #4282)"* → *"(the public name is retired)"*; *"Pre-#1417 `aboutEvent`-as-provenance edges"* → *"Pre-existing `aboutEvent`-as-provenance edges"*. Both rules kept; "pre-existing" is the same fact without the version stamp. |

### 2.4 Dispositions — §3.3–§3.8 (14)

| # | Ref(s) | Line | Class | What happened |
|---|---|---|---|---|
| 3.3-a | `#3263` | 174 | **B** | *"(amended v3.11, #3263: the edge is written per source …)"* → *"(the edge is written per source …)"*. The many→many rule, the per-source write and the "no upper bound" clause all stay. |
| 3.4-a | `#909` ×2 | 180 | **B** | *"(producer extension in `link_source_to_entity`, #909 §4.3 #8)"* → no number; *"(referential chain, #909)"* → *"(referential chain)"*. |
| 3.4-b | `#398` ×2 | 182, 184 | **B** | *"— see §4.6, #398)"* → *"— see §4.6)"*; *"(#398 never-overwrite contract)"* → *"(the never-overwrite contract)"*. The never-overwrite contract is **named**, not weakened. |
| 3.4-c | `#388` | 184 | **B** | *"from connector event metadata (`sourceKind` + per-entity `sourceUrl`) — #388."* → no number. The gate rule and its exclusions stay. |
| 3.5-a | `#531` | 191 | **B** | *"(the #531 `humanApproval` pattern: an approval Event produces the decision Point …)"* → *"(the `humanApproval` pattern: …)"*. Exemplar kept. |
| 3.5-b | `#388` | 197 | **B** | *"— since #388 — see §3.4.)"* → *"— see §3.4.)"*. |
| 3.6-a | `#4282` | 208 | **C** | ⚠️ **Sentence moved whole; the rule it framed is kept.** Removed: *"`get_org_structure` queried `memberOf` for membership … — it was the predicate's ONLY served reader, and **#4282 deleted it** (one of the 26 dead public `TortoiseSDK` methods, 2026-10-27). No served code queries `memberOf` now: the remaining references are an allowlist entry (`tortoise/security.py`), a rel-type docstring, registry prose and the structural-predicate note in `ep.py`."* **Kept:** *"The canonicality argument rests on the predicate's MODELLING — member→org is what the edge means, and the hosted auth-context seeding writes it — not on a reader existing."* plus `holdsRole` and `hasMember`. Changelog `B-3`. |
| 3.8-a | `#531` ×3 | 222, 227 | **B** | *"— an Object, or a decision Point (#531)"* → no number; *"**#531 — canonical Event→Point pattern (`humanApproval`):**"* → *"**The canonical Event→Point pattern (`humanApproval`):**"*; *"Worked example (`file_human_approval`, #531)"* → *"Worked example (`file_human_approval`)"*. Every pattern rule, the Cypher example and the unidirectional `approvedBy` rationale are untouched. |
| 3.8-b | `#7813` | 225 | **B** | *"Point→Event propagates EP under #7813 (an Event target is epistemic — §8 OVERRIDES)"* → *"Point→Event propagates EP (an Event target is epistemic — §8 OVERRIDES)"*. |
| 3.8-c | `#7902` | 225 | **C** | *"`:Subject`/`:Object` endpoints are live (`#7902`); the write path admits all four"* → *"The write path admits all four"*. Status dropped, rule kept. |

### 2.5 Dispositions — §3.9 (13)

| # | Ref(s) | Line | Class | What happened |
|---|---|---|---|---|
| 3.9-a | `#214` | 248 | **B** | ⚠️ **History restated as rule** (2.6, W-3): *"**#214 (2026-08-06):** `instantiates` removed — Event→Action legacy from v2.5"* → *"**`instantiates` is not a valid predicate** — Event→Action legacy from v2.5"*. Same fact, model tense; the enumerated predicate vocabulary that follows is untouched. |
| 3.9-b | `#5025` ×2 | 273, 337 | **B** | *"**It carries no epistemic weight, by decision (#5025, owner ruling 2026-09-27).**"* → *"…by decision.**"*; *"this change only brings the traversal into line with the #5025 decision above"* → *"the traversal is in line with the decision above"*. The rule and its cross-reference to the decision **in this document** stay. |
| 3.9-c | `#5547` | 320 | **B** | *"`test_related_is_neutral_by_construction` (#5547)"* → the test name stays (a code symbol), the issue goes. |
| 3.9-d | `#5566` | 325 | **B** | *"the breach that undercut it is now closed (#5566)"* → *"the breach that undercut it is closed"*. The breach's full description stays. |
| 3.9-e | `#2489` | 339 | **B** | *"Per #2489 a predicate's label and its replay key are one unit"* → *"A predicate's label and its replay key are one unit"*. |
| 3.9-f | `#7551` | 346 | **B** | *"**The operator-label declared vocabulary (#7551).**"* → *"**The operator-label declared vocabulary.**"*. The union rule, `CORE_OPERATOR_LABELS`, the warn-not-block behaviour and the `produces`/`measures` examples are untouched. |
| 3.9-g | `#1934` | 353 | **B** | *"(warn-not-block, #1934)"* → *"(warn-not-block)"*. The named behaviour stays. |
| 3.9-h | `#11083` | 357 | **B** | *"`produces` left it when it became a meaning (#11083)"* → *"(… when it became a meaning)"*. |
| 3.9-i | `#909` | 362 | **B** | *"(#909 §4.3 #7 — registered)"* → *"(registered)"*. |
| 3.9-j | `#2315` ×2 | 364, 374 | **B** | *"**Hard rule (#2315, pinned 2026-09-07):**"* → *"**Hard rule:**"*; *"**Strength semantics (#2315, product decision 2026-09-07 — mitigation is a GRADED DAMPENER, not a refutation):**"* → *"**Strength semantics (mitigation is a GRADED DAMPENER, not a refutation):**"*. Both rules, the `[0.10, 0.50]` band and `w_eff = w × (1 − strength)` are untouched. |
| 3.9-k | `#2199` | 384 | **B** | *"The strength is NOT fused into the mitigation point's own Beta prior (#2199 decision 3)."* → same sentence without the parenthetical. |

### 2.6 Wording changes (not citation deletions) — so they can be rejected individually

| # | Where | Before → after | Why |
|---|---|---|---|
| **W-1** | §3.1, `#9836` clause | *"so before `#9836` they moved live and journalled nothing"* → *"so a descriptor is the only thing that carries them on the journal route: without one, the journal loses the edge while the record keeps its pre-supersede row"* | "Before X" is history; the constraint it describes is a live rule. Same content, model tense. **This is the largest restatement in the batch.** |
| **W-2** | §3.2, `#8325` clause | *"… the Object-subclass characterisation above is the intended model, not the implemented one"* → *"A Tag is an `:Object` subclass carrying `objectKind: tag` — that is the model; a store that mints the node without those labels is a build gap"* | Promotes the model claim out of the gap report, which is what "canonical" means. The gap is not denied — it moves to the changelog. |
| **W-3** | §3.9, `#214` | *"#214 (2026-08-06): `instantiates` removed"* → *"`instantiates` is not a valid predicate"* | The predicate's status is a rule; the date it changed is history. |
| **W-4** | §3.1, `#7902` | *"… endpoints are live; the write path admits all four"* → *"The write path admits all four epistemic targets"* | Drops the liveness assertion (status). "epistemic targets" is added so the sentence still names what is admitted. |
| **W-5** | §3.2, `#1417` | *"Pre-#1417"* → *"Pre-existing"* | Same extension, without the version stamp. |

**No other wording changes.** Everything not named in 2.2–2.6 is the source text with a citation
removed.

### 2.7 Proposed text — §3.1–§3.9 (replaces `ONTOLOGY.md` lines 109–389)

<!-- PROPOSED:§3 -->
````markdown
## §3. Edge Topology

### §3.1 Epistemic ↔ Epistemic (Operators — `:Point`/`:Subject`/`:Object`/`:Event`)

The write path admits all four epistemic targets.

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `IMPL` | `:Point`/`:Subject`/`:Object`/`:Event` → same | recorded per write — `->`, `<-`, `<->` or `-` | N-ary | Epistemic (EP confidence) | A supports/implies B. Direction is recorded on the write. |
| `NAND` | `:Point`/`:Subject`/`:Object`/`:Event` → same | recorded per write — `->`, `<-`, `<->` or `-` | N-ary | Epistemic (EP confidence) | A contradicts B (logically mutual — "A and B can't both be true"). `mutuallyExclusive` records `<->` ("A and B can't both be true"); `refutes` records `->` (the directed attack — attacker's truth penalizes the target, no back-pressure). Direction is recorded on the write. |
| `hasPart` | Point → Point | `<->` (composition) | N-ary | Structural via operator label | A contains B (parts/whole cascade). |
| `CORRECTS` | Point → Point | unidirectional | 1→1 | — | New point **corrects/replaces** an outdated point — the shared structural replacement marker (supersession *or* invalidation, §4.7 ‡). Marks target `outdated: true`; edge disposition is **restatement-scoped** (see the shared replacement-edge semantics below — semantic edges are triaged carry/drop/pend). Created by `supersede_point` (sdk.py:4765) / `invalidate_point` (sdk.py:4654). |

> **Supersession / invalidation semantics (the shared `CORRECTS` edge):** `CORRECTS` is the structural replacement edge — both writes below create it, and only `status='superseded'` separates them (§4.7). `supersede_point(old, new)` = mark old `outdated:true` + create `(new)-[:CORRECTS]->(old)` + dispose of old's edges per the **restatement-vs-correction policy**. `invalidate_point(id, corrected_by)` = mark outdated + CORRECTS only (no edge transfer). Old point retains only the CORRECTS edge as provenance.
>
> **Structural-edge transfer is journaled + replayable:** the 2b structural-edge transfer (`extractedFrom` + snapshot-derivable `about*` edges) is journaled as flat `DirectEdgeRepoint` descriptors `{src=old_id, tgt=<replay key>, target_label, edge_type}` emitted **before** the transfer, and replayed by `rebuild_all` pass-2b (delete the pass-2 resurrection at old + create at the successor THAT SUPERSEDE NAMED — `succ[src]`, ONE HOP per descriptor, so a chain reaches its terminal node only as each hop's descriptor runs and a payload lands where live put it, NOT at `_final(src)`). The journaled set is **two families, and the pair is the rel set, not the derivable set**: (a) the **snapshot-derivable** rels — `extractedFrom`, `aboutSubject`/`aboutObject`/`aboutEvent`/`aboutDocument`/`aboutPoint` — whose edges rebuild pass-2 re-creates at old from a point's immutable `extractedFrom` prop / `aboutEntities` list, and whose descriptor is keyed by the label's NATURAL replay key (`name`, or `url` for a `:Source`); and (b) the **ID-ADDRESSED** family — `aboutAction`, `wasDerivedFrom` — transferred by 2b but NOT snapshot-derivable, so a descriptor is the only thing that carries them on the journal route: without one, the journal loses the edge while the record keeps its pre-supersede row. ❌ **Membership of the derivable set is not what decides whether a rel is journalled**; the deciding pair is `STRUCTURAL_REPOINT_RELS = DERIVABLE_STRUCTURAL_RELS ∪ ID_ADDRESSED_STRUCTURAL_RELS`. `aboutSource` is never snapshot-recreated AND is not in the transfer set, so it gets no descriptor on either route. **The ID-ADDRESSED family's target is addressed by the label the transfer OBSERVED, riding `target_label`** — by the node's own IDENTITY property for an epistemic node — `id`, or **`eventId` for an `:Event`** (the meeting guard's SUFFIX branch keeps `id = <candidate>`, so two distinct Events can share one `id`; `eventId` is what the record files an Event under) — never a natural key (a Point has no `name`, and `wasDerivedFrom`'s target label is not a function of the rel) and **by its `url` for a `:Source`** (a Source's identity IS its url, its record `node_key` is that url, and both routes resolve one by url — the same identity `extractedFrom`/`aboutDocument` use), with the scheme fixed per label (`ID_ADDRESSED_TARGET_ADDRESSING`), never chosen by the descriptor. Both routes resolve it the same way (`resolve_structural_target(by_id=…/by_url=…)` / `_endpoint_resolution`); "not addressable by `id`" is NOT "not addressable", and reading the id sweep's boundary as this family's boundary is exactly what left a `:Source`-targeted edge moved live and present on neither rebuild. Keys are label-scoped and resolved via the shared resolver in `projection/edges.py` (`stub_key`/`resolve_structural_target` — Subjects MERGE by `name`, Sources **and `aboutDocument`** by `url`; never `target.id`). **⚠️ A predicate's label and its key are one unit: a retargeted label with a stale key resolves to nothing and silently mis-points the rebuilt edge.** `aboutDocument` targets a `:Source` (§4.4), so it resolves by `url` — the same key a `:Source` resolves by. The 2b no-self-edge guard (target node == successor) emits a `delete_only` descriptor instead of a transfer. **The RECORD applies a 2b descriptor as the same MOVE:** `DELETE` the row at the superseded point, then `INSERT` it at the **direct successor** that supersede named — the point `superseded_by` points at (one hop, `depth = 2` of a `WITH RECURSIVE` walk over `public.point.props->>'superseded_by'`, `path`-guarded and depth-bounded) — ONE HOP per descriptor, so a chain reaches its **terminal** row only after every hop's descriptor has run: each later hop's `DELETE` removes the intermediate row exactly as live's supersede removes the intermediate edge, and the ordering that falls out is live's (a chain's LAST transfer created the final edge, two independent retired points onto one successor leave the FIRST creator's value standing) — resolving the target by the label's own REPLAY key (`name` for Subject/Object/Event, `props->>'name'` for Point, `url` **with** `document_kind IS NOT NULL` for `aboutDocument`, the canonical identity triple for `extractedFrom`) — or, for the ID-ADDRESSED family, by the node's own identity property (`id`; `eventId` for an `Event`) under the descriptor's `target_label` — or by its `url` when that label is `Source` (`node_key` alone, never the label's `id OR node_key` union) — via `_endpoint_resolution`, the same resolution every other edge endpoint uses, and landing NOTHING when a name is held by two or more carriers. ⛔ A `delete_only` descriptor (the no-self-edge guard) is applied as a DELETE for the ID-ADDRESSED family too: the graph deletes that edge, and the record already holds the row its `DirectEdgeCreated` fold wrote. Both halves are gated on `src` being terminal AND a destination being reachable (`_src_is_terminal`, restated in SQL), so a live re-created incarnation is a clean no-op rather than delete-then-insert churn; and the insert is `ON CONFLICT … DO NOTHING`, **never `DO UPDATE`**, because the graph's transfer carries the retired point's attributes with `ON CREATE` (`MERGE (new)-[nr:<rel>]->(t) ON CREATE SET nr += $attrs`), so an edge the successor already holds is left untouched and keeps its OWN live value (`STORAGE-ARCHITECTURE.md` §13). The descriptor carries **no successor field**: the record derives the destination from the `superseded_by` its own `PointSuperseded` record already wrote — the same hop pass-2b applies (`succ[src]`) — so the two rebuild routes agree on a linear chain, and on a CYCLIC supersede chain only in the ONE-HOP shape the PGlite `9972-cycle` case pins (a single journaled hop: both land on that hop's successor). ⚠️ They do NOT agree once a CYCLIC chain carries a descriptor per hop: the record's `path`-guarded walk hops once per descriptor and ends at the LAST hop's successor, while pass-2b's `_final` returns the first REPEATED node, so `_final(src) == src` and its create is SKIPPED — after that descriptor's delete-leg already removed the edge the previous hop created, leaving the rebuilt graph with NO edge (measured: `tests/test_pointsuperseded_rebuild.py::test_a_cycle_with_a_descriptor_per_hop_loses_the_edge_at_rebuild`). A live producer cannot emit that shape (`supersede_point` refuses the closing hop: a dead target cannot be re-terminalized), so every journal a live writer CAN produce keeps the journal alone sufficient (Interface A). The record never MINTS an endpoint (no stub), unlike the fold. **Pre-fix journal boundary:** descriptors exist only for supersedes journaled post-deploy — a pre-fix journal rebuilt with this consumer replays with no delete-leg, so old's pass-2 resurrection persists (rebuild does NOT repair pre-existing graphs; a backfill is out of scope).
>
> **Restatement-vs-correction policy:** two supersede cases look alike but demand opposite edge handling, so edge disposition is decided **per edge at write time** (never silently bulk-transferred):
>
> - **Case 1 — restatement:** the new point says the same thing more precisely (better source, fixed typo, merged duplicate). Every connection still applies; edges and belief transfer — belief-preservation is asserted (eval-spec P6.1/P6.2).
> - **Case 2 — substantive correction:** the new point exists *because* a refutation landed. The refuting NAND must **not** re-attach to the successor (it motivated the change); belief recomputes structurally with the refutation gone.
> Deterministic policy is safe only for content-independent edges (identity edges like `aboutObject` when the target is unchanged; merges carry everything). Semantic edges (IMPL/NAND) route to a carry / drop / **pend** triage with the rationale stored per edge; pended edges are non-voting placeholders surfaced by the ask lane only when an answer depends on them. A refutation that motivated a correction never re-attaches; a refutation that still applies is kept.
>
> **Direction flag:** direction is recorded on the operator Point (or on the edge when no operator is present) — four values: `->`, `<-`, `<->`, `-`. It comes from the connection's semantics; the recorded value is authoritative. Operators lacking the property are read as `<->`; the legacy tokens `bidirectional` / `unidirectional` remain valid aliases.
>
> **Edge properties (IMPL/NAND — EP message state):** these are **graph-persisted** belief-propagation messages written by `TortoiseEP._flush_cache` and read back by `_load_cache` (warm-start seed). They are load-bearing graph state — documented here so they are not treated as throwaway cache:
>
> | Property | Type | Written by | Meaning |
> |----------|------|-----------|---------|
> | `msg_alpha` / `msg_beta` | float | `TortoiseEP._flush_cache` (ep.py) | Forward EP message natural parameters, operator→claim slot `(op_id, claim_id, rel_type)` |
> | `back_msg_alpha` / `back_msg_beta` | float | `TortoiseEP._flush_cache` (ep.py) | Backward EP message natural parameters — separate slot for bidirectional / operator-less edges (the `back_msg_*` pair on the same edge) |
>
> **Warm-start note:** `run(warm_start=True)` loads these graph-persisted messages as seed and skips updates whose delta ≤ fixed threshold γ; the fast path (`compute_confidence`) runs `warm_start=False` and never touches γ-skip state.
>
> **Extraction NAND direction policy (pipeline spec):** the EXTRACTOR explicitly sets direction per this policy:
>
> - **New-claim-attacks-existing-claim → `->`** (directed): "you now claim ¬D against D" is an attack on an existing belief — the new claim attacks the old. This is the common, measured-correct case (the one that makes contradiction surfacing work; `nand_precision` A11 measures it).
> - **Mutual restatement → `<->`**: when both claims are asserted together as mutually exclusive (e.g., the conversation itself declares "A and B can't both be true").
> - **Extraction-emitted NANDs record `->`** — extraction asserts something NEW against something EXISTING; `<->` is the rare explicit mutual case.

### §3.2 Point ↔ Entity (Cross — Semantic ↔ Epistemic)

Per-type edges (chosen over single polymorphic edge — FalkorDB matrix-per-type architecture).

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `aboutSubject` | Point/Event → Subject | unidirectional | many→many | `schema:about` (typed) | What Subject this describes. A source does not carry this edge — what a source's *contents* are about is carried by the nodes extracted from it |
| `aboutObject` | Point/Event/**Session** → Object | unidirectional | many→many | `schema:about` (typed) | What Object this describes. A source does not carry this edge — see the row above |
| `aboutEvent` | Point → Event | unidirectional | many→many | `schema:about` (typed) | What Event this describes. Event is a target only — Events don't describe other Events. A source does not carry this edge |
| `aboutPoint` | Event → Point | unidirectional | many→many | `schema:about` (typed) | What Point this Event describes. Event-only edge |
| `aboutDocument` | Event → **Source** | unidirectional | many→many | `schema:about` (typed) | What document this Event is about. **⚠️ It is a distinct predicate from `aboutSource` and must not be merged with it:** `aboutDocument` is in the snapshot-derivable set (`DERIVABLE_STRUCTURAL_RELS`) and is therefore **rebuilt at the old point** by `rebuild_all` pass-2b, whereas `aboutSource` is deliberately excluded and never resurrects — merging them would silently delete that rebuild guarantee (the replay rules are in §3.1) |
| `aboutSource` | Point/Event → Source | unidirectional | many→many | `schema:about` (typed) | What Source this describes (e.g., an evaluation of a source). Creatable via `create_edge`; may coexist with `extractedFrom` when the claim's text was also retrieved from that source. **⚠️ NOT snapshot-derivable by design — it gets no replay descriptor** |
| `aboutAction` | Point → Point (legacy) | unidirectional | many→many | `schema:about` (typed) | Legacy predicate retained for pre-v3.0 Action edges (Action entity dissolved in v3.0). No automatic producer — creatable only via explicit `create_edge`; endpoints are whatever the resolver finds |
| `TAGGED` | Point → Tag | unidirectional | many→many | `schema:keywords` | Free-form label on a Point. Tags are `:Tag` nodes (Object subclass, `objectKind: tag`) shared across Points via MERGE. **A user-authored Tag is first-class DATA, not a derived label** — settled by what a Tag *is* (*"shared, MERGEd, first-class nodes are data"*, so *"the owner is not asked"*), which makes it durable Point content: tags survive `rebuild_all` (objective 6: "props, **tags** and EP state survive"). **Shape:** the Point's own `tags` property is the source and the `:Tag`/`TAGGED` half is **snapshot-derivable** from it (the same split, like `extractedFrom`). A LIST of names drives the edge half and is the supported form; a truthy non-list (a scalar) is stored verbatim and gets no edges, and a value the store cannot hold degrades to NO property. ⚠️ A truthy non-list (`tags` a number/string/map) leaves any EXISTING `TAGGED` edges in place — live keeps a membership whose property no longer lists it — while **`rebuild_all`** derives none from it and therefore drops that edge — ⚠️ and `rebuild(EventLog)` KEEPS it (it replays record-by-record through `apply`, so the creation's list-sync runs before the edit and no later sweep revisits it), so **live and `rebuild(EventLog)`** then hold an edge the property no longer lists; the difference is a known divergence between the engines (one-line fix, in `_sync_tags`). **Replay:** `_upsert_point_props` writes the property, `_upsert_point_edges` pass 2 re-derives the edges from the node's own final value, and `_revise_point` folds a tag edit *and* its explicit clear. **Live:** the SDK's `create_point`/`update_point` and that replay fold share ONE routine, `projection/edges.py::_sync_tags`. Producers: the SDK write path, hosted-api point ingestion (`hosted_api.py`), and the demo seeder (`_seed_demo_graph`) — ⚠️ the seeder is the one producer that still hand-rolls its `MERGE` loop instead of calling `_sync_tags` (the property it now writes is what makes its tags durable), and its fix is NOT retroactive for previously seeded graphs. Readable by tag via `TortoiseSDK.query_points_by_tag` / the MCP `tortoise_query(tag=…)` surface. ⚠️ **A Tag is an `:Object` subclass carrying `objectKind: tag`** — that is the model; a store that mints the node without those labels is a build gap, not a different model. |

> **Legacy:** `aboutEntities` property → per-type `about*` edges. `_create_about_edges()` auto-detects Subject/Object from the legacy property. `schema:about` is polymorphic; we split per-type for graph performance.
>
> **Session as an aboutObject source:** the capture path links `(s:Session)-[:aboutObject]->(o:Object)` and `(p:Point)-[:aboutObject]->(o:Object)` for episodic turn Points (the stored windowed turns — not LLM-extracted points), driven by a regex trigger over the conversation text (`github.com/{org}/{repo}/issues/{n}`, `{repo}#{n}`, bare `#n` with a false-positive guard). First-match per point is form-priority — the URL form before `{repo}#{n}` before bare `#n`, independent of textual position; all-matches for the Session node (deduped by target id). Name-suffix matches (org-ambiguous) link only when exactly ONE Object matches — zero or multiple ⇒ no-op, honest. Resolution is by the stable WorkItem Object id (`github-issue-{org}/{repo}-{n}` — computable only from the full URL form; suffix forms resolve by name), so aboutObject never dangles on supersede. Outcomes are tracked on the Session node (`entity_links_attempted` / `entity_links_created`) and journaled via a follow-up `SessionRecorded` so a wipe+replay restores them; the pass re-runs on index completion. The SDK runs the same pass as the hosted lane. The public name is retired; the body survives as the private `TortoiseSDK._capture_session` (the PRIVATE door — see `tortoise/sdk_retired.py`), so the pass runs on BOTH lanes. Edges the pass writes are journaled as an `EntityLinked` JSONL record (flat logical ids `{source_label, source_id, target_label, target_id, edge_type}`) **when the SDK is built with an `event_log_path`**; the record is folded idempotently by `FalkorProjection._fold_entity_linked`, and all four whole-journal replay engines (`rebuild_all`, and the `apply()`-based `rebuild`/`recover_from_log`/`backup.restore` JSONL fallback) defer it to a trailing sweep so a forward reference still folds; a link whose endpoint was hard-deleted after it is skipped (the record carries no `seq` — each engine pairs it with its journal position, which the hard-delete boundary is compared against), so a same-id re-creation does not resurrect the deleted link. A journal-less SDK — a hosted-lane SDK when `TORTOISE_EVENT_LOG_BASE_DIR` is unset (`_make_sdk`/`_data_sdk`; the base dir wires a per-graph `event_log_path` when set) — emits no record and the edges stay live-only. The extractor's `(claim Point)-[:aboutObject]->(Object)` edges ride the same journal **only when the resolved Object carries an `id`**; an id-less NAME-matched stub (the hosted path's `MERGE (o:Object {name:$name})` objects) is attached by a raw name-based `MERGE` that emits no `EntityLinked`, so it is live-only and does NOT survive replay. The `:Session` node's journal carrier is `SessionRecorded` — with a journal wired, the node (and any aboutObject edge from it) is recreated on rebuild. `aboutSubject` is produced by the capture spine's subject binding: it routes §5 Subject kinds to the `:Subject` label (`is_subject_kind`) and binds Points to them with `bind_point_subjects`, the only confidence-gated `aboutSubject` producer. The Session-linking pass described here writes `aboutObject` only (see the §3.2 table).
>
> **`aboutEvent` is content-only:** the edge means "this Point describes this Event" — never "this Point was produced by this Event". Capture-path provenance (a Point produced from a session/meeting) lives on the Point's **`eventId` property** (the provenance surface, stamped by `TortoiseSDK._capture_session` (the public name is retired), hosted `/v1/sessions`, and the mining path), with the full chain `(Point)-[:extractedFrom]->(Source)-[:references]->(Event)` (§3.3/§3.4). Pre-existing `aboutEvent`-as-provenance edges remain readable and are semantically reinterpreted (no migration); new writes must not mint them. NOTE: the D10 subject-resolution fallback (point's source-event's subject) now requires the `eventId` property — legacy aboutEvent-only points silently stop resolving that fallback (review P2).

### §3.3 Point → Source (Provenance)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `extractedFrom` | Point → Source | unidirectional | many→many | `pav:retrievedFrom` (inverse) | This claim was extracted from this source. One source backs many Points, **and one Point may be backed by several sources** (the edge is written per source — a claim extracted from several sessions carries one `extractedFrom` edge each; there is no upper bound). |

### §3.4 Source → Entity (Provenance)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `references` | Source → Event/Object/Source | unidirectional | 1→many | — | The source links to / references this entity — target may be an Event, an Object, **or another Source** (producer extension in `link_source_to_entity`). A document is itself a `:Source`, so it is covered by the Source target. Wired in the ingest path — the source a document arrived through is linked to it, and external artifacts referenced in a captured conversation become external Source nodes that the session Source `references` (referential chain). |

`(Point)-[:extractedFrom]->(Source)-[:references]->(Entity)` — layered provenance. Source carries `sourceKind` (extensible source TYPE vocabulary — canonical list: §5 + `SOURCE_KIND_DEFAULTS`; e.g. `github_issue`, `github_pr`, `linear_card`, `linear_cycle`, `slack_message`, `document`, `agentSession`, `meeting_summary`, `meeting_transcript`, `meeting_minutes`) and `credibilityTier` (T0-T4 credibility tier — see §4.6).

Connector entities (GitHub/Linear/Slack) get Source nodes at the projection choke point: `_upsert_event` (projection/entities.py) materializes `(Source {url})-[:references]->(Event {eventId})` from connector event metadata (`sourceKind` + per-entity `sourceUrl`). The gate fires only on a registered connector `sourceKind` or an explicit `sourceUrl` (never on bare `source` — mining events stay excluded); `sourceKind` is set on CREATE only — a pre-existing Source's kind is authoritative (the never-overwrite contract) — and re-materialization does not bump `version` (no churn on re-poll). When no per-entity URL exists, `Source.url` falls back to a container-scope string (`slack:{channel}`, `linear:{team_key}`) — the reference still resolves; the key is just coarser than a permalink. The GitHub entity path additionally wires `(Source)-[:references]->(Object {id})` via an explicit `sourceObjectId` event field (`event.object` is never used as an Object key — it is the entity title on poll/webhook paths).

### §3.5 Subject → Event → Object (Procedural)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `performs` | Subject → Event | unidirectional | N-ary | **`schema:agent` inverse** — schema.org's "direct performer or driver of the action", reversed (we go Agent→Activity) | X **did** this. The doing relation: subject executes the event. PROV has no Agent→Activity predicate (its `wasAssociatedWith` is Activity→Agent accountability); we name the performer-side verb ourselves, aligned to schema.org's performer concept. |
| `produces` | **any → Object or Point** | unidirectional | 1→many | `schema:result` (same direction) / `prov:wasGeneratedBy` inverse | Output artifact the source created — an **Object** (a report, a PR, a build) or a **decision Point** (the `humanApproval` pattern: an approval Event produces the decision Point that seeds its grounding). The event→artifact shape is the **encouraged pattern, not a restriction** |
| `uses` | Event → Object | unidirectional | N-ary | **`prov:used`** (W3C: Activity→Entity, direction-identical — canonical) / `schema:instrument` for mechanisms | Input the event consumed — **including the mechanism** (skill/tool/agent/workflow Object) that produced the output |
| `wasDerivedFrom` | Object → Object | unidirectional | N-ary | `prov:wasDerivedFrom` | Entity derivation (distinct from Source provenance) |

> **One edge, two names:** `uses` (graph predicate) = `prov:used` (PROV property). Same thing — present tense in our vocabulary, past tense in PROV's. `produces` = `schema:result` (Activity→Entity, matching direction); `prov:wasGeneratedBy` names the reverse (Entity→Activity).
>
> **Mechanism provenance ("how was it produced"):** the producing mechanism is a first-class Object linked via `uses` — `(Event)-[:uses]->(Object {objectKind: skill|tool|agent|workflow})`. The mechanism is therefore searchable and shared (finite skill set, not per-event). Mechanism *specifics* (version, model, config, pipeline hash) live in the immutable event-log record, reachable via the Event's `eventId` — they are NOT materialized as per-event graph nodes (avoids O(events) node growth at scale). Full lineage: `(Point)-[:extractedFrom]->(:Source)` — the source a point was read from, which carries the `eventId` of the Event that ingested it, reaching the log record; a session Source also `references` the document Sources it contains (`Source → Source`, §3.4). (The `references` hop is wired in the ingest path for connector entities at the `_upsert_event` choke point — see §3.4.)

### §3.6 Subject ↔ Subject (Organisational)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `participatesIn` | Subject → Event | unidirectional | N-ary | `schema:attendee` | Subjects involved in an event, created by the event write path from the event's participant list — falling back to the event's `subject` when no explicit participants are given |
| `memberOf` | Subject → Subject | unidirectional | N-ary | `org:membership` | Membership in team/group/organization. **Canonical** — generalizable to teams, orgs, any hierarchy. |
| `managedBy` | Entity → Subject | unidirectional | N-ary | RACI Responsible | Operational responsibility |
| `ownedBy` | Entity → (**Subject \| Object**) | unidirectional (per edge) | N-ary | RACI Accountable | Accountability, data boundary. **The target is an Entity (a Subject or an Object).** With one predicate covering "a person owns a thing", "a company owns a thing" and "a holding IS a stake in a company", the predicate alone can no longer say which end owns which — so **the per-edge `direction` carries the sense**: `->` = the TARGET owns the source (`investment --ownedBy--> fund`); `<-` = the SOURCE owns the target, read as a stake in it (`investment --ownedBy--> company`). ⚠️ Accepted residual: no code validates endpoint kinds by semantics, so the ruling is carried per declaration and **reported** (by `pack_registry.effective_direction` and the pack census), not enforced. |

> **memberOf is canonical.** The canonicality argument rests on the predicate's MODELLING — member→org is what the edge means, and the hosted auth-context seeding writes it — not on a reader existing. `holdsRole` is retained as a distinct concept (person→role is not membership). The legacy `hasMember` predicate (org→member) remains in valid_predicates for backward compatibility with existing graph edges.

### §3.7 Object ↔ Object

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `wasDerivedFrom` | Object → Object | unidirectional | N-ary | `prov:wasDerivedFrom` | Derivation |
| `hasPart` | Object → Object | `<->` | N-ary | `dcterms:hasPart` | Composition. Inverse traversal (`<-[:hasPart]-`) covers "part of" — no separate `partOf` edge. |

### §3.8 Event Edges

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `performs` (in) | Subject → Event | unidirectional | N-ary | `schema:agent` inverse | Actor — who did it |
| `produces` | **any → Object or Point** | unidirectional | 1→many | `schema:result` | Output artifact — an Object, or a decision Point. Event→artifact is the encouraged pattern |
| `uses` | Event → Object | unidirectional | N-ary | `prov:used` | Input consumed |
| `nextEvent` | Event → Event | unidirectional | 1→1 | — | Sequencing (Graphiti NextEpisode equivalent) — planned |
| `op: IMPL/NAND` | `:Point`/`:Subject`/`:Object`/`:Event` → same | recorded per write — `->`, `<-`, `<->` or `-` | N-ary | Epistemic | Outcome influence on belief (epistemic); Point→Event propagates EP (an Event target is epistemic — §8 OVERRIDES). The write path admits all four |

> **The canonical Event→Point pattern (`humanApproval`):** a human approval of a planning artifact is recorded as an Event (`eventKind: humanApproval`) + a decision Point (`pointKind: humanApproval`). The Event carries occurrence provenance (approver `performs`, artifact `uses`, claim `aboutPoint`, decision `produces`); the decision Point is a live epistemic claim that seeds the grounding a-vector and receives an EP evidence prior `Beta(10,1)` so dependent claims strengthen. Fan-out is `-[:IMPL {direction: "unidirectional", label: "approvedBy"}]->` per approved claim — deliberately unidirectional so claim weakness never back-propagates into the approval. No stored `approved` status on Objects — approval is derived from the event stream at query time. Worked example (`file_human_approval`):
>
> ```cypher
> (:Subject "Daniel")-[:performs]->(:Event {eventKind:"humanApproval", startedAt:T})
>   (:Event)-[:uses]->(:Object "Customer Profile CP-001")
>   (:Event)-[:aboutPoint]->(:Point "CP-001 targets SMB segment")
>   (:Event)-[:produces]->(:Point {pointKind:"humanApproval", content:"Approved: CP-001"})
> (:Point "Approved: CP-001")-[:IMPL {direction:unidirectional, label:"approvedBy"}]-> approved claim Points
> ```

### §3.9 Valid Predicate Vocabulary (code)

All structural edges must use one of (enforced in `_create_edge`):

```text
performs, produces, uses, authoredBy, ownedBy, managedBy,
hasMember, holdsRole, memberOf, reportsTo,
participatesIn, hasPart, related, dependsOn, references,
wasDerivedFrom
```

> **`instantiates` is not a valid predicate** — Event→Action legacy from v2.5;
> Action was dissolved in Ontology v3.0.
>
> **Vocabulary-only edges** (valid predicates with zero producers):
> `reportsTo` (org hierarchy, Subject→Subject), `dependsOn` (pack-declared —
> dev:api dependsOn dev:database; used by `list_relations()` for kind expansion),
> and `related` — **defined below**. All three remain valid for `create_edge()`.
>
> **`related` — the neutral association link.**
>
> Use it when two things are connected and you mean nothing more than that. It says
> "these belong in the same neighbourhood" and deliberately does not say how. It is
> not support, not contradiction, not provenance, and not a way of saying "part of"
> or "reports to"; where one of those is meant, one of those links must be used.
>
> Three meanings follow, and they are what make it safe to use freely:
>
> - **It has no direction.** "A is related to B" and "B is related to A" are the
>     same statement — so it can never be used to say "A adds information to B",
>     which does have a direction.
> - **It does not chain.** A related to B, and B related to C, tells you nothing
>     directly about A and C.
> - **It is not a hierarchy.** It never substitutes for "part of", "member of" or
>     "reports to".
>
> **It carries no epistemic weight, by decision.**
> Nothing that computes confidence may read this link: it must not be traversed when
> support is propagated (the `IMPL`/`NAND` path), and it must not be traversed when
> the system weighs how much to trust a source (`_apply_source_inheritance`, which
> reads `extractedFrom`). A structural link a confidence path walks is no longer
> neutral — it is an argument wearing a neutral name.
>
> **`related` is exempt from §8's "confidence edge attribute" row.** §8 gives a plain
> structural edge a `confidence` attribute; for `related` that attribute is **not** a
> weight, and no confidence path reads it. The two sections are consistent once read
> together: a `confidence` property may exist on the edge without the edge being an
> argument.
>
> **Where relevance is expressed: not here.** Relevance belongs on the **operator** —
> by attaching an `IMPL`/`NAND` to the operator's node. That matches the shape
> argumentation interchange uses: in AIF the *rationale for a relation* is carried by
> a **scheme node** (`RA-node`), not by the relation itself. **The analogy is
> illustrative, not authoritative** — AIF's `RA-node` is about inference-rule
> application; the point here is only that "why are these two connected" is a property
> of the node rather than of the pair. **Plain support or contradiction between two
> claims may also be an operator-less direct `IMPL`/`NAND` edge** (§8 below,
> `create_direct_edge`), so claims do **not** connect only through a scheme in this
> ontology, and the neutral link is not what makes relevance possible.
>
> **`related` — enforcement status (normative detail).** The three meanings above
> follow the SKOS Reference (W3C Recommendation): symmetric (`owl:SymmetricProperty`,
> S23); **not** transitive (§8.6.4 — "Note that `skos:related` is not a transitive
> property"); disjoint from the hierarchical relations (§8.6.10 — "fundamentally
> distinct in nature"). The same shape is the norm elsewhere: ConceptNet's
> `/r/RelatedTo` is documented as "the most general relation. There is some positive
> relationship between A and B, but ConceptNet can't determine what that relationship
> is based on the data", and is symmetric.
>
> **Declared, not machine-enforced.** Symmetry, non-transitivity and
> hierarchy-disjointness are declarations. `_VALID_EDGE_PREDICATES` is a flat
> frozenset, and **for `related`** `create_edge()` applies no symmetry, transitivity
> or disjointness logic — no test asserts those properties of `related`. (Transitivity
> logic *does* exist in the codebase for a **different** predicate: `create_edge`'s
> transitive cycle guard for `ownedBy` (`projection/edges.py:565-582`), tested by
> `test_transitive_cycle_rejected` and `test_direct_cycle_rejected`. This claim is
> scoped to `related`.)
>
> **The durability half IS enforced — the confidence half is NOT.** What is enforced
> is *transfer and replay*: `related` is in **neither** `DERIVABLE_STRUCTURAL_RELS`
> (no replay descriptor) **nor** `SUPERSEDE_STRUCTURAL_RELS` (a supersede does not
> transfer it — it stays at the old point), pinned by
> `tests/test_dry_run_preview.py` ("in NO transfer leg") and
> `test_related_is_neutral_by_construction`. **These two sets govern
> durability, not weight** — do not read their enforcement as the rule above being
> true in the code.
>
> ⛔ **"Carries no epistemic weight" — the decision held, and the breach that
> undercut it is closed.** The EP affected-set traversal was unfiltered
> on relation (`ep.py` `_affected_claims` / `_live_neighbors`), so a `related` edge
> landing on an operator *did* reach `_update_claim_posterior`, which recomputed that
> node as `Beta(1,1)` and **discarded its prior**. The operator-mediated hops are now
> **factor-bearing-only**: typed `IMPL|NAND` *and* directed
> (`(n)<-[r:IMPL|NAND]-(op)-[r2:IMPL|NAND]->(m)`), so only operator **inputs** — the
> relations `_affected_factors` actually turns into factors — are admitted. Neither a
> structural predicate (`related`, `aboutSubject`, `memberOf`, …) nor a reverse-only
> `IMPL` (the mitigation back-link `(m)-[:IMPL]->(op)`) can admit a claim. `related`
> therefore **cannot** change a belief number, in fact as well as by decision. Pinned
> by `tests/test_ep_local_395.py::test_ac3_max_hops_none_both_impls_and_run_contract`.
> *(The owner reservation on the belief model proper — DECISION-LEDGER §22 — stands;
> the traversal is in line with the decision above.)*
>
> **Wiring a producer requires revisiting both sets first.** A predicate's
> label and its replay key are one unit: `STRUCTURAL_REL_LABELS` holds the target
> label and the replay-key selection sits beside it in `stub_key`, so the two must
> move together, and the replay branch keys on `etype in DERIVABLE_STRUCTURAL_RELS`.

Epistemic edges (operators): `IMPL`, `NAND` (+ semantic label).

> **The operator-label declared vocabulary.** A `label` passed to
> `create_operator` / `tortoise_create_operator` is **declared** — written without an
> `undeclared_relation` warning — when it is `IMPL`/`NAND`, a value of the §8 Layer-2 semantics
> vocabulary, or a predicate an installed pack declares. The declared set is the **union** of
> those three, and it is **derived** (`pack_registry.CORE_OPERATOR_LABELS`), never
> re-transcribed, so it cannot drift from §8's table. **Declaredness is not shape:** the §8
> reification table governs which edges *ought* to carry an operator, and an undeclared label
> still warns without blocking (warn-not-block). A **retired** spelling (`opposes`,
> `holds`, `funds` — the transition map) is deliberately **not** declared, so it keeps warning and
> the caller learns the canonical word. **`produces` is not one of them**: it is a
> Layer-2 meaning (§8), so it is declared and does not warn — the transition map is the authority
> for that list, and `produces` left it when it became a meaning. `measures` is declared
> nowhere and
> was never a §8 meaning — it was invented by a lane that could find no reachable declared
> predicate, which is what this statement exists to prevent.

Mitigation edge: `mitigated_by` — Point → Point (operator → mitigation Point), written by `mitigate_operator` (`TortoiseSDK.mitigate_operator`): `(op:Point {is_operator:true})-[:mitigated_by]->(m:Point)`, with the mitigation Point back-linking `-[:IMPL]->` the operator (registered).

> **Hard rule:** a `mitigated_by` edge can ONLY
> originate from an `is_operator:true` Point. `mitigate_operator` is the
> SINGLE writer gate and enforces it (raises on non-operators); generic
> `create_edge` cannot write the predicate (its allowlist is the §3.9 set
> above, which excludes `mitigated_by`); rebuild/commit paths route through
> `mitigate_operator`. A raw graph write that attaches `mitigated_by` to a
> non-operator violates the ontology — no EP factor ever reads it (factor
> extraction addresses operators only), so such an edge would be dead
> structure.
>
> **Strength semantics (mitigation is a
> GRADED DAMPENER, not a refutation):** the mitigation Point's
> `mitigation_strength` property is the dampening strength in [0.10, 0.50]
> (0.10 minor caveat … 0.50 major counter-evidence = strongest; >0.50 would
> invert the claim — use NAND). EP reduces the operator's effective weight
> by `w_eff = w × (1 − strength)` in `compute_operator_weight`
> (tortoise/weights.py — single source of the convention; §8 has the
> operator-mediated (op-123) mitigation-anchor diagram showing where the
> operator mediates the IMPL/NAND edge between two claims).
> The strength is NOT fused into the mitigation point's own Beta prior.

About edges: `aboutSubject`, `aboutObject`, `aboutEvent`, `aboutPoint`, `aboutDocument`, `aboutSource` (Point/Event → Source — `aboutDocument` targets `:Source`), `aboutAction` (legacy).

---

````

> **Approve:** ☐ text as written · ☐ with changes (list them) · ☐ reject (§3 keeps its numbers)
>
> **Approve the wording changes separately:** ☐ W-1 ☐ W-2 ☐ W-3 ☐ W-4 ☐ W-5 · ☐ reject all

### 2.8 Anything not classifiable

**Nothing in §3.1–§3.9 is unclassifiable.** Ref coverage is exhaustive and checkable:

```bash
python3 - <<'PY'
import re
src='\n'.join(open('docs/ONTOLOGY.md').read().split('\n')[108:389])
print(sorted(set(re.findall(r'#(\d{3,5})(?![0-9])', src)), key=int))
PY
```

That prints 39 distinct issues. **Every one of them appears in 2.2–2.5.** The four cases where text
leaves the model at all (`#2501`, `#2500`, `#8333`, `#8325`) each have their removed text quoted
verbatim in the changelog extract, so nothing is lost and each can be restored individually.

---

## Batch 3 — §4.1–§4.4 (Point, Subject, Object, Document)

### 3.1 Per-section table

| Section | lines | Refs before | Refs removed | Classes | D1 before → after | Rules moved | Unclassifiable |
|---|---|---|---|---|---|---|---|
| §4.1 Point | 390–422 | 21 | **21** | B 15 · C 6 | 12 → 12 | 0 | none |
| §4.2 Subject | 423–440 | 19 | **19** | B 19 | 7 → 8 | 0 | none |
| §4.3 Object | 441–462 | 20 | **20** | B 20 | 11 → 11 | 0 | none |
| §4.4 Document | 463–517 | 10 | **10** | B 7 · C 3 | 5 → 5 | 0 | none |
| **Total** | — | **70** | **70** | B 61 · C 9 | 35 → 36 | **0** | none |

**§4.2's D1 goes 7 → 8 — an ADDED rule, not a lost one.** The added token is the `must` in the
restated clobber guard (W-9): the source described the *old bug* in the past tense ("a re-mention
… reset it to `live`"), which states no rule; the proposal states the rule that replaced it ("must
not reset it"). There are **two** D1 increases in the whole proposal — this one and batch 1's header (+1, P-3's
pointer rule); both are called out on their own approval lines.

**Nine references are class `C`**, and they are the only place text leaves the model in this batch:
six in §4.1 (`C-1` holds 2, `C-2` holds 2, `C-3` holds 1, and `#947` holds 1 — W-8) and three in §4.4
(inside the two status sentences `C-4`/`C-5`). Each is a status or rationale clause, never a rule,
and each is quoted verbatim in the changelog extract. Every other removal in this batch is a
citation deletion.

### 3.2 Dispositions — §4.1 Point (21)

| # | Ref(s) | Line | Class | What happened to the semantic content |
|---|---|---|---|---|
| 4.1-a | `#909` | 398 | **B** | *"content-addressed `pt_<sha>` ids are a **sanctioned id form** (deterministic — the commit endpoint's idempotency anchor, #909 §4.3 #10)"* → same without the number. The sanctioned-form rule stays. |
| 4.1-b | `#11824` | 403 | **B** | *"— one closed set of four for every knowledge kind (#11824; target model in `docs/plans/2026-11-15-state-lifecycle-and-pipelines.md`)"* → *"— one closed set of four for every knowledge kind."* ⚠️ **The plan-doc pointer goes too** (W-13): "target model in <plan>" is a statement that the model is not yet current — class C dressed as a link. The rule is unchanged. |
| 4.1-c | `#2498` | 403 | **B** | *"the legacy `outdated=true` flag is terminal too (#2498)"* → no number. Rule kept. |
| 4.1-d | `#12855` | 403 | **B** | *"all three survive only as read deltas over stored data (#12855)"* → no number. Rule kept. |
| 4.1-e | `#909` | 405 | **B** | *"(registered #909 §4.3 #11; written by the calibrated pipeline, slice 5+)"* → *"(written by the calibrated pipeline)"*. ⚠️ "slice 5+" (a delivery slice) is status and goes with it. |
| 4.1-f | `#909` | 406 | **B** | *"stored Point property per #909 §4.3 #11 (secret-scanned)"* → *"stored Point property (secret-scanned)"*. |
| 4.1-g | `#1533` | 407 | **B** | *"(registered #1533 E1; written by extractor_v2 S5 …)"* → *"(written by extractor_v2 S5 …)"*. |
| 4.1-h | `#3654` | 409 | **B** | *"(`ConversationMiner._temporal_wire`, #3654)"* → *"(`ConversationMiner._temporal_wire`)"*. The **absent ⇒ open/unbounded start** rule and the never-borrow-the-write-clock rule are untouched. |
| 4.1-i | `#5358`, `#4021`, `#5360` | 410 | **B** | Three citations inside the `validTo` row: *"`retract_point` is the window-agnostic route (#5358)"*, *"…satisfiable by no query instant) (#4021)"*, *"— an unorderable resolved bound is normalised … (#5360)"*. **Every validation rule in that cell is untouched** — the refusal conditions, the equality allowance, the NUMERIC exemption. |
| 4.1-j | `#522`, `#3154` | 413 | **C** | ⚠️ **Rationale removed** (W-15): *"— `is_operator` is never indexed (#522 embedded stale bool type table; #3154 docker/server `GRAPH.COPY` drops the `false` postings of a copied boolean RANGE index, zeroing `is_operator = false` on copies whose index set carries it, and leaving the copy destination unable to rebuild it)"* → *"— `is_operator` is never indexed"*. The rule survives; the engine-bug explanation moves to changelog `C-1`. |
| 4.1-k | `#3086` | 415 | **B** | *"(`tortoise/sdk.py::_TURN_WRITE_CYPHER`, #3086)"* → the symbol stays, the number goes. |
| 4.1-l | `#4282`, `#3554` | 415 | **C** | ⚠️ **History rewritten** (W-7): *"so does the SDK — `TortoiseSDK.capture_session` until **#4282 retired that public name**, and `TortoiseSDK._capture_session` (private) since, which is the same body and the same shared primitive, so BOTH lanes still write it. The earlier claim that the SDK writes it alone was false (#3554)"* → *"so does the SDK — through `TortoiseSDK._capture_session` (the private door, the same body and the same shared primitive), so BOTH lanes write it."* The rule (both lanes write `speaker` via one shared turn store) is preserved exactly; the retirement history and the correction history move to `C-2`. |
| 4.1-m | `#909`, `#947` | 416 | **B** / **C** | *"(the `points` branch counts non-episodic only, #909 §4.3 #13/§4.4; legacy nodes lack the flag — one-query backfill migration ships with #947)"* → *"(the `points` branch counts non-episodic only; legacy nodes lack the flag, and a one-query backfill migration closes that)"* (W-8). The quota rule and the legacy-gap statement both stay; only "ships with #947" becomes a plain statement. |
| 4.1-n | `#7856`, `#11824` | 417 | **B** | *"(`GOAL_STATE_VALUES`, #7856)"* → *"(`GOAL_STATE_VALUES`)"*; *"and it is unassigned (#11824)"* → *"and it is unassigned"*. The closed vocabulary, the `guideline` exclusion and the two-ending refusal rule are untouched. |
| 4.1-o | `epic 903` *(no `#`)* | 413 | **C** | ⚠️ **A bare tracker identity, not a `#NNNN`.** *"timestamp of the last EP write-back that **converged** on this claim (epic 903)"* → *"…on this claim."* The freshness-stamp rule, the NULL-ranks-stalest rule and the atomic-write rule are untouched. Recorded at changelog `B-59`. |
| **P-4** | `#11915` | 421 | **C** | ⚠️ **An owner-approved sentence is rewritten.** The source sentence lists *"Percent, ratio, duration and quantity"* as shapes with no storing implementation, then a **status note** corrects it. Proposal: the sentence names **percent** as a storing shape (with `basisPoints`/`verbatim`/validation) and lists only **ratio, duration and quantity** as unbuilt; the status note is deleted. The note itself says *"rewriting an owner-approved clause is a new decision"* — **that is exactly what this is, and it is proposed as P-4, not assumed.** Rejecting P-4 restores the approved sentence *and* the status note (and that one `#11915` reference stays). Changelog `C-3`. |

### 3.3 Dispositions — §4.2 Subject (19)

| # | Ref(s) | Line | Class | What happened |
|---|---|---|---|---|
| 4.2-a | `#11824`, `#1423` ×2, `#1350`, `#2194`, `#2193`, `#2977` | 430 | **B** | All six are inside the `status` row's mechanism narrative. *"(#11824)"* dropped; *"**Projection-owned since #1423**"* → *"**Projection-owned**"*; the clobber-guard parenthetical becomes a **rule** (W-9): *"— the clobber guard: a **re-mention of a superseded team/role/person must not reset it to `live` live-side while replay keeps the supersession** (the live≠replay class, pinned by `tests/test_subject_supersession_1423.py`)"*; *"precedent (#2977), where a read vocabulary…"* → no number. **Every rule in the row survives**: projection-owned cache, CREATE-seed, `_SUBJECT_HANDLED`, the write-side validation against `SUBJECT_STATUS_VALUES`, and the read-side `SUBJECT_TERMINAL_STATUSES` exclusion from the four retrieval legs. |
| 4.2-b | `#1423`, `#2164` | 431 | **B** | *"#1423: successor NAME …"* → *"Successor NAME …"*; *"— the #2164-P4 drift class the Object twin still exhibits"* → *"— the drift class the Object twin still exhibits"*. The restore-the-ORIGINAL-instant rule is untouched. |
| 4.2-c | `#3642` ×2 | 432, 433 | **B** | *"(declared §4.7, #3642)"* → *"(declared §4.7)"* — twice. The internal §4.7 pointer stays; it is structure. |
| 4.2-d | `#2295` | 434 | **B** | *"adopted ON MATCH only when absent — #2295"* → no number. |
| 4.2-e | `#2295` ×3, `#2296`, `#1918`, `#2061`, `#2194` | 437 | **B** | The registration-durability blockquote, de-numbered: heading; *"pre-#2295 journals (no backfill)"* → *"journals written before this contract existed (no backfill)"*; *"(#2296 scope hook: …)"* → *"(…)"*; *"(random-ulid, #1918)"* → *"(random-ulid)"*; *"Event-precedent parity, #2061/#2194"* → *"Event-precedent parity"*; *"(pinned by the #2295 suite test 1)"* → *"(pinned by the durability suite's first test)"*. **All durability rules survive**: the journal-when-wired rule, the probe-gate, the byte-identity scope, the residual non-durable classes, and the reserved-name narrowing. |

### 3.4 Dispositions — §4.3 Object (20)

| # | Ref(s) | Line | Class | What happened |
|---|---|---|---|---|
| 4.3-a | `#2729`, `#2193`, `#11824` ×2, `#9974`, `#2792` | 449 | **B** | The `status` row, de-numbered throughout: *"for the pack's own kinds — #2729"* → no number; *"(divergent re-folds never blind-overwrite — #2193 resolved)"* → *"(divergent re-folds never blind-overwrite)"*; *"`superseded` (#11824); an Object created without a status stores none and reads as `live` (#9974 R1 — never a fabricated `live` write)"* → same without both numbers, **keeping** "never a fabricated `live` write"; *"(`pipelineStageId`, #2792)"* → *"(`pipelineStageId`)"*; *"rather than a wider status list (#11824)"* → no number. **Every rule survives**: the fold directions, the cache doctrine, keep-first, the draft/live/deprecated/superseded set, `archived` as a synonym, positions-vs-lifecycle-words, and pack-declared validation. |
| 4.3-b | `#2165` ×2 | 450, 451 | **B** | *"R17 P3-1 (#2165): `supersededAt` is the connected-assembly state-header's date source"* → *"`supersededAt` is the connected-assembly state-header's date source"* (W-10: the `R17 P3-1` plan-reference is a plan slice, not a rule); *"(R12/C6, #2165 Task 5)"* → *"(R12/C6)"*. The byte-golden rule and the name-only-annotation rule are untouched. |
| 4.3-c | `#3642` | 452 | **B** | *"(declared §4.7, #3642)"* → *"(declared §4.7)"*. |
| 4.3-d | `#2194` | 454 | **B** | *"adopted ON MATCH only when absent — #2194"* → no number. |
| 4.3-e | `#909` | 456 | **B** | *"(registered #909 §4.3 #12; planned for the capture path, slice 5+)"* → *"(planned for the capture path)"*. The "false entities are still written, flagged" rule stays. |
| 4.3-f | `#7856`, `#11824` | 457 | **B** | Same treatment as §4.1 row `4.1-n`: the closed `goalState` vocabulary, the `guideline` exclusion and the ending-agreement refusal all stay. |
| 4.3-g | `#2194` ×2, `#2295` ×2, `#2296`, `#2061`, `#2309` | 461 | **B** | The Object registration-durability blockquote, de-numbered exactly as §4.2's: heading, *"pre-#2194 journals (no backfill)"* → *"journals written before this contract existed"*, *"since #2295"* → no number, *"(#2296 scope hook: …)"* → *"(…)"*, *"Event-precedent parity, #2061"* → no number, *"(the is-None gate — #2309 review hardening, mirrored for Subjects in #2295)"* → *"(the is-None gate, mirrored for Subjects)"*. **All durability rules survive.** |

### 3.5 Dispositions — §4.4 Document (10)

| # | Ref(s) | Line | Class | What happened |
|---|---|---|---|---|
| 4.4-a | `#5142` | 469 | **B** | *"**A document is a `:Source` — and it stays TWO nodes (`#5142`).**"* → no number. The two-node rule stands. |
| 4.4-b | `#205` | 471 | **B** | *"distinct from the corpus `#205` provenance Source"* → *"distinct from the corpus provenance Source"*. |
| 4.4-c | `#5013` ×2, `#5088` | 483 | **B** | *"A deliberate, **deferred** decision (#5013 §4.4 · #5088 · `OVERRIDES:` posted on #5013), not an accident of history."* → *"A deliberate, **deferred** decision, not an accident of history."* **The `OVERRIDES:` marker itself stays** (it is the model's own device); only its tracker reference goes. |
| 4.4-d | `#5151` | 486 | **B** | *"**The self-loop is now refused at the writer…**"* → *"**The self-loop is refused at the writer…**"* (W-12: "now" is history). The guard's rule and its "does not merge the nodes" consequence are untouched. |
| 4.4-e | `#5140` | 500 | **B** | *"that inversion is exactly the defect #5140 fixed"* → *"that inversion is exactly the defect to avoid"* (W-14). The read-order rule (`captured`/`extracted` outrank `needs_extraction`; ABSENT ≠ done) is untouched. |
| 4.4-f | `#2726` ×2, `#398` | 506–515 | **B** / **C** | ⚠️ **A "TARGET STATE" blockquote becomes canonical** (W-11). Source opened *"**(#2726) Meeting capture … (TARGET STATE, writer not yet wired):** once the capture/index writer emits the new kinds, a captured meeting **will land** as a Document…"* and closed with *"TODAY the index/classifier path still emits `meeting_summary` … so `meeting_summary` remains the kind actually written."* Proposal states the model directly: *"a captured meeting **lands** as a Document …; the tier question is left open (§5)"*. **The model claim is unchanged; the two status sentences move to `C-4`, `C-5`.** |

### 3.6 Wording changes (not citation deletions) — rejectable individually

| # | Where | Change | Why |
|---|---|---|---|
| **W-7** | §4.1 `speaker` | capture_session retirement history removed; the sentence now reads *"through `TortoiseSDK._capture_session` (the private door …), so BOTH lanes write it"* | Same rule, no history. |
| **W-8** | §4.1 `is_episodic` | *"one-query backfill migration ships with #947"* → *"a one-query backfill migration closes that"* | Delivery status → rule statement. |
| **W-9** | §4.2 `status` | past-tense bug description → normative *"must not reset it to `live`"* (**+1 D1**) | The old text stated no rule at all; the new text does. |
| **W-10** | §4.3 `supersededAt` | plan prefix *"R17 P3-1 (#2165):"* dropped | Plan slice label, not a rule. |
| **W-11** | §4.4 meeting capture | TARGET STATE → present-tense model; two "TODAY" sentences removed | The model is what the section is for; the build state moves. |
| **W-12** | §4.4 | *"is now refused"* → *"is refused"* | "Now" is history. |
| **W-13** | §4.1 `status` | `docs/plans/2026-11-15-state-lifecycle-and-pipelines.md` pointer dropped | It marks the model as not-yet-current. |
| **W-14** | §4.4 | *"the defect #5140 fixed"* → *"the defect to avoid"* | Same warning, no tracker. |
| **W-15** | §4.1 `lastDreamedAt` | engine-bug rationale for "`is_operator` is never indexed" removed | The rule is the sentence; the rationale is class C. |
| **W-16** | §4.2, §4.3 durability | *"pre-#2295 journals (no backfill)"* / *"pre-#2194 journals"* → *"journals written before this contract existed (no backfill)"* | Same class of journals, no version stamp. |
| **W-17** | §4.2 durability | *"(pinned by the #2295 suite test 1)"* → *"(pinned by the durability suite's first test)"* | ⚠️ A **weaker locator** than the source's. Rejecting W-17 restores a test identity that is not a `#NNNN`. |
| **W-18** | §4.4 meeting capture | *"the tier question is deferred to #398"* → *"the tier question is left open (§5)"* | ⚠️ This **adds a `§5` cross-reference the source did not have**, because the source-kind vocabulary is described there. Rejecting W-18 restores the original sentence shape. |
| **W-19** | §4.1 `c_cal`, `when` | *"(registered #909 §4.3 #11; written by … slice 5+)"* → *"(written by …)"*; *"(registered #1533 E1; written by …)"* → *"(written by …)"* | Drops both the registration marker and the delivery slice. |
| **W-20** | §4.3 `passes_frequency_gate` | *"(registered #909 §4.3 #12; planned for the capture path, slice 5+)"* → *"(planned for the capture path)"* | Same as W-19. |

#### 3.6bis Two tracker references batch 2 missed (corrected here)

Batch 2's review checked `#NNNN` and found none left; it did **not** check bare epic numbers, and they
survived in **four** places: `epic 903` in §3.1's EP message-state heading, `903-C4` twice in §3.1's
warm-start note, and `epic 903` again in §4.1's `lastDreamedAt` row (`ONTOLOGY.md:413`, row `4.1-o`).
They are tracker identities exactly like `#NNNN`, so **this batch's text removes all four occurrences**
— three edits in the §3 block, one in §4.1 — and none of them carries a rule. The changelog records
them at `B-59`. Batch 2's own counts are unaffected: none of the strings is a `#NNNN`, a D1 token, or
a rule.

This is also why the completion check is **two** greps, not one:

```bash
grep -nE '#[0-9]' docs/ONTOLOGY.md  # must print nothing — ANY digit run, not just 3-5
grep -nE '(epic|issue|PR) [0-9]{2,5}|[0-9]{3,5}-C[0-9]' docs/ONTOLOGY.md   # must print nothing
```

### 3.7 Policy item introduced by this batch

| # | The change | Why it needs a decision | Rejecting it means |
|---|-----------|------------------------|--------------------|
| **P-4** | **§4.1's *"Percent, ratio, duration and quantity …"* sentence is rewritten**, and its status note deleted. | The source's own note says *"rewriting an owner-approved clause is a new decision"*. This draft makes that decision explicitly rather than burying it in a batch. | Restore the approved sentence **and** its status note verbatim — the section then keeps one `#11915` reference, and the proposal's strict-zero target excludes §4.1's Values paragraph. Every other row in this batch is unaffected. |

### 3.8 Proposed text — §4.1–§4.4 (replaces `ONTOLOGY.md` lines 390–517)

<!-- PROPOSED:§4a -->
```markdown
## §4. Entity Metadata

> Columns: ISO/PROV/DC = standard alignment. Only the live field set is listed — legacy JSONL-only fields are dropped from the ontology. The per-field **build record** is in `ONTOLOGY-CHANGELOG.md`.

### §4.1 Point

| Field | Type | Required | ISO/PROV/DC | Meaning |
|-------|------|----------|-------------|---------|
| `id` | ULID | ✅ | `dc:identifier` | Unique identifier — ULID preferred (`create_point`); content-addressed `pt_<sha>` ids are a **sanctioned id form** (deterministic — the commit endpoint's idempotency anchor) |
| `content` | string | ✅ | `schema:text` | The claim text |
| `pointKind` | string | ✅ | — | Classification tag — extraction writes `statement` (option B); decision/vision/strategy/plan/goal/target/observation/hypothesis/humanApproval/event are legacy write kinds (§5) + pack pointKinds |
| `is_operator` | bool | — | — | true for operator Points |
| `op_type` | string | — | — | IMPL / NAND (operator Points only) |
| `status` | string | — | `pav:status` | Lifecycle: **`draft`, `live`, `deprecated`, `superseded`** — one closed set of four for every knowledge kind. **challenged is a derived condition** (presence of a NAND operator edge on a live point), not a stored status (§5). `draft` is inert for computation; `deprecated` (ended, nothing replaced it) and `superseded` (ended, replacement named) are terminal, and the legacy `outdated=true` flag is terminal too. `retracted`, `outdated` and `archived` are **no longer lifecycle words**: a retraction or invalidation ends the claim as `deprecated`, `archived` is a synonym of `deprecated` accepted on write and stored as `deprecated`, and all three survive only as read deltas over stored data |
| `confidence` | float 0..1 | — | — | EP posterior mean, computed by propagation |
| `c_cal` | float 0..1 | — | — | Calibrated confidence — calibrated counterpart to the EP posterior `confidence` (written by the calibrated pipeline) |
| `quote` | string ≤200 | — | — | Provenance quote — the source text this claim was drawn from; payload-level metadata today (SDK extraction path / EventAPI `provenance()` payloads — extractor.py, api.py), stored Point property (secret-scanned) |
| `when` | ISO date ≤40 | — | `prov:atTime` | Occurrence-time anchor — the conversation date a state-change/decision/date-bearing fact is "as of"; "" = undated (written by extractor_v2 S5 from the session-date-anchored prompts; absent on timeless durable beliefs) |
| `authoredBy` | SubjectID | — | `dc:creator` | Who created the claim |
| `validFrom` | ISO8601 | — | `prov:generatedAtTime` | Valid-time **start** — populated by the date-carrying write paths only (the hosted commit path sets it from the payload `when`; mining W-4 from the session date). Mining W-4's undated leg (`ConversationMiner._temporal_wire`) writes **no** `validFrom` — **absent ⇒ open/unbounded start** (`get_historical_knowledge`), never a start synthesized from the write wall clock. The `validFrom` → `createdAt` chain is a **render fallback** (`_render_date`), never a create-time stamp. §4.7 |
| `validTo` | ISO8601 | — | `prov:invalidatedAtTime` | Valid-time **end** — `supersede_point` stamps the successor's `validFrom` **when it carries one**; an undated successor falls back to its `createdAt`, then to `now` (monotone — never a gap), so the windows are exactly contiguous **only for a dated successor**. `invalidate_point` instead stamps `validTo = now` (no successor ⇒ no contiguity), and refuses with `ValueError` when a stored `validFrom` is after `now` — `retract_point` is the window-agnostic route. A `valid_from` **kwarg** is refused when it disagrees with a successor that **carries** a stored `validFrom` (same instant required, else `ValueError` before any write — §4.7). A successor whose resolved start is **parseable** and sorts **strictly before the predecessor's own parseable `validFrom`** is refused outright (`ValueError`, same measure, before any write — an inverted window is satisfiable by no query instant). A bound that is present but **unorderable** on EITHER side is not compared: it names no instant, so a refusal would rest on a lexicographic accident rather than a comparison (`_assert_window_start_not_inverted` makes the same choice for `invalidate_point`), and the orderability residual is closed on the write path — an unorderable resolved bound is normalised to an orderable instant rather than persisted, with ONE deliberate exemption: a NUMERIC `valid_from` kwarg, which is `str()`-ed and left unorderable, so `_covers` still cannot order that window end. §4.7 |
| `expiredAt` | ISO8601 | — | — | Transaction-time expiry — **when our record stopped being current** (termination), not *why* it did. Written by both `supersede_point` (replaced by a successor) and `invalidate_point` (withdrawn) — **the timestamp alone cannot tell the two apart**. Supersession is a separate fact: Points carry it as `status='superseded'` (Point has no `supersededAt`); the `outdated` flag + `CORRECTS` edge are shared with `invalidate_point` and do **not** distinguish the two. See §4.7. |
| `createdAt` / `updatedAt` | ISO8601 | ✅ | `dc:created` / `dc:modified` | Timestamps |
| `lastDreamedAt` | ISO8601 UTC | — | — | Freshness stamp — timestamp of the last EP write-back that **converged** on this claim. NULL = never dreamed — **ranks STALEST** in the stale-first scheduler (first-deploy/legacy/crash-mid-pass graphs drain across passes). Non-operator claims only (operators excluded from ranking/stamping). Written **atomically with `confidence`** in the dream write-back (single UNWIND — the write-back's own fields lastDreamedAt+updatedAt are all-or-nothing; `confidence` is also flushed independently by `ep.run`'s `_flush_cache`, per the epic plan's redundancy note); failed/non-converged runs never update it; operator-less claims get a trivial stamp via the scan path. Indexed via the plain `:Point(lastDreamedAt)` index, created idempotently at init on ALL engines — `is_operator` is never indexed |
| `embedding` | vector | — | — | Semantic embedding (FTS + vector search) |
| `speaker` | string | — | — | Role tag on episodic turn Points (user/assistant/…) — written by the ONE shared turn store (`tortoise/sdk.py::_TURN_WRITE_CYPHER`), so hosted capture (`POST /v1/sessions`) writes it, and so does the SDK — through `TortoiseSDK._capture_session` (the private door, the same body and the same shared primitive), so BOTH lanes write it. |
| `is_episodic` | bool | — | — | Quota exemption discriminator — true on episodic turn Points from the regex capture path (the `points` branch counts non-episodic only; legacy nodes lack the flag, and a one-query backfill migration closes that) |
| `goalState` | string | — | — | A goal's **achievement** state — a CLOSED vocabulary (`met`, `active`, `blocked`, `unmet`, `superseded`, `abandoned`), validated at the SDK write boundary (`GOAL_STATE_VALUES`). `guideline` is **not** an achievement: whether a goal is directional is a question about **priority**, and it is unassigned. The two endings are the lifecycle's endings on this axis — `abandoned` is `deprecated` for a goal, `superseded` is `superseded` — and a write whose ending disagrees with `status` is refused. **Separate from `status`** (the draft/live lifecycle): `status` says whether a goal is promoted for use, `goalState` says whether it is met. `None` clears. Only meaningful on `goal`/`target`/`strategy`/`plan` points (§5) |

**Values.** A *value* is a number plus a reference, kept as flat sibling properties on the entity that carries it (WS-C §2.1; realised in `tortoise/money.py`). It carries no confidence, no embedding and no numeric index — it is found by its entity, not by similarity. The reference's standard decides the fields: for **money**, ISO 4217 — `amountMinor` (integer minor units, never a float), `currency` (alpha-3, per amount, never a graph default), `minorUnitExponent` (checked against the pinned table), plus `verbatim` (the source text, required only where the value was derived from prose). **Percent has a storing shape** (`tortoise/percent.py` — `basisPoints` int 0–10000, plus `verbatim` only where the value came from prose, validated at the SDK's one props intake and at the journal-payload seam exactly as `money` is). Ratio, duration and quantity are declared value shapes with no storing implementation yet (`tortoise/pack_values.py`); **a date is not a value shape** — it is the valid-time slot (§4.7). A value's raw detail may live outside the graph, addressed by `:Source`; the graph records the observation and the crossing, never the series.

### §4.2 Subject

| Field | Type | Required | ISO/PROV/DC | Meaning |
|-------|------|----------|-------------|---------|
| `id` | string | ✅ | `dc:identifier` | Canonical identifier |
| `name` | string | ✅ | `foaf:name` / `schema:name` | Human-readable name |
| `subjectKind` | string | ✅ | `dcterms:type` | organization, team, role, legalPerson, naturalPerson, other |
| `status` | string | — | `pav:status` | Lifecycle: `draft`, `live`, `deprecated`, `superseded` — `archived` is accepted on write as a synonym of `deprecated` and stored as `deprecated`. **Projection-owned** — the Subject twin of the Object status cache (§11: the journal is the reconstruction source, the property is a cache). `_upsert_subject` SEEDS it ON CREATE (`status=coalesce($st,'live')`) and `status` ∈ `_SUBJECT_HANDLED`, so `_persist_extra_props` never rewrites it on MATCH — the clobber guard: a **re-mention of a superseded team/role/person must not reset it to `live` live-side while replay keeps the supersession** (the live≠replay class, pinned by `tests/test_subject_supersession_1423.py`). Supersession folds through the generic entity surface: `commit_ops.apply_supersessions` writes `status`/`supersededBy`/`supersededAt` and journals `EntityMutated op="supersede"`, which the ONE `SET n += $state` arm replays in all four engines. **Write side (review P1)**: `create_entity`/`update_entity` validate a Subject `status` against `commit_ops.SUBJECT_STATUS_VALUES` (this §4.2 lifecycle) and raise for anything else, so an undeclared value (e.g. `dead`) can neither be stored nor leak. Read side: `commit_ops.SUBJECT_TERMINAL_STATUSES` (superseded / deprecated / archived / retracted — `retracted` is not among the §4.2 declared verbs and the write guard REFUSES it, but the read set is the NET for a state a legacy/pre-fix SDK or a raw producer may already have stored, on the Object family's own precedent, where a read vocabulary kept `retracted` with no in-tree writer) excludes terminal Subjects from the four retrieval legs by default |
| `supersededBy` / `supersededAt` | string / ISO8601 | — | — | Successor NAME (stored verbatim) and the fold instant, written together with `status='superseded'` by the subject supersession lane. Carried in the journaled `EntityMutated` `state` map, so a wipe+rebuild restores the ORIGINAL instant rather than a rebuild-time one — the drift class the Object twin still exhibits (`_fold_object_superseded` falls back to `now` on the live path while replay reads the journaled `ts`). Absent on non-superseded Subjects |
| `validFrom` / `validTo` | ISO8601 | — | `prov:generatedAtTime` / `prov:invalidatedAtTime` | Valid-time window — when the Subject held in the world (declared §4.7) |
| `expiredAt` | ISO8601 | — | — | Transaction-time expiry — when our record of the Subject stopped being current (declared §4.7) |
| `createdAt` | ISO8601 | ✅ | `dc:created` | Timestamp (set ON CREATE; adopted ON MATCH only when absent) |
| `updatedAt` | ISO8601 | — | `dc:modified` | **Not written by `_upsert_subject`** — planned follow-up |

> **Subject registration durability** — SDK-created Subjects (`create_entity("subject")`, the capture/entity write path) journal a `SubjectAdded` event on their FIRST canonical registration **when the SDK is built with an `event_log_path`** (probe-gated on the deterministic `sub-<sha26(name)>` id + name — a canonical re-mention after the fix never double-journals; a probe failure fails open to a replay-safe duplicate line; an EventAPI `add_subject` mention under a random ulid between SDK creates re-ids the live node and double-registers by design — `EventAPI.add_subject` has no canonical-id override — it accepts `name`/`subject_kind` and mints a fresh `ulid()` (`tortoise/api.py:403`)), so SDK-created Subjects survive `rebuild_all` (the JSONL event stream is the rebuild source; replay consumers pre-date the fix because EventAPI always journaled). Byte-identity is **node-property scope**: Subject org/ownership edges ride the mirror but replay wires node properties only (EventAPI-parity). **Residual non-durable classes**: journals written before this contract existed (no backfill), unjournaled/legacy raw producers and journal-less SDK instances sharing the graph, deleted Subjects — `_delete_entity` leaves no tombstone, so a deleted Subject **resurrects** on the next rebuild and delete→recreate's replay first-wins the earlier incarnation's `createdAt`; and an EventAPI-first-then-SDK create (separate logs) replays a synthesized `createdAt` that differs from the live adopted node's pre-existing value under existing-wins (the durability write-surface invariant must cover the delete side of the lifecycle + the Object/Subject loss backstop). EventAPI `add_subject` remains a separate unconditional producer (random-ulid). **Reserved-name narrowing**: `point`/`payload` props passed to an SDK Subject create are dropped (unconditional, both lanes — Event-precedent parity); they are `_emit_event`-reserved kwargs and must never reach the journal mirror or live node. `is_episodic` is NOT a registered Subject quota discriminator (the §4.7 cell is `—`), but the internal `create_entity(..., is_episodic=)` extras lane does round-trip it on SDK-created Subjects (pinned by the durability suite's first test) — same extras quirk as Object.
>
> **Why no `subjectStatus`?** PROV-O, W3C ORG, and FOAF model agent existence temporally (`validFrom`/`validTo`) with termination events, not status enumerations. A simple shared `status` covers "is this team active?" while Events capture the how/why of changes.

### §4.3 Object

| Field | Type | Required | ISO/PROV/DC | Meaning |
|-------|------|----------|-------------|---------|
| `id` | string | ✅ | `dc:identifier` | Canonical identifier |
| `name` | string | ✅ | `schema:name` | Human-readable name (`_upsert_object` writes `title`; `name` aliased) |
| `objectKind` | string | ✅ | — | The §5 object-kind vocabulary (Project, WorkItem, Problem, tag, user, skill, tool, agent, workflow, agreement, standard, other, strategy, plan, goal, target) + pack objectKinds |
| `title` | string | — | `dc:title` | Display title (what `_upsert_object` actually stores) |
| `status` | string | — | `pav:status` | Write-through cache of lifecycle events (ObjectRegistered→live; ObjectSuperseded→superseded + `supersededBy` + `supersededAt`; built-in connector work-item events→in_progress/completed; **pack-declared `kindDefs[].statuses`/`folds` for the pack's own kinds**, dispatched on the namespaced kind and suppressed for built-in folds once a kind declares a lifecycle) — **the event stream is the reconstruction source for `Object.status` (§11 cache doctrine); the property is a performance cache, folded keep-first per Object (divergent re-folds never blind-overwrite)**. **Core Object types accept `draft`, `live`, `deprecated`, `superseded`; an Object created without a status stores none and reads as `live` (never a fabricated `live` write). `superseded` means replaced: it names the successor; `deprecated` is ended with nothing replacing it and needs no successor — `archived` is accepted as its synonym on write and stored as `deprecated`. A journal rebuild is unchanged and still restores a supersession recorded without a successor. `in_progress` and `completed` are **positions, not lifecycle words** — they become stages of the kind's declared pipeline (`pipelineStageId`), and the built-in connector folds that write them target those stages. `outdated` and `retracted` are not Object lifecycle words and are not retained. Pack-declared kinds validate against their own declared words, which become their pipeline stages rather than a wider status list.** |
| `supersededAt` | ISO8601 | — | — | The fold TIMESTAMP written by the supersession fold (`apply_supersessions` / `ObjectSuperseded` projection, pinned after the fold via `SET o.supersededAt` for byte-reproducible state headers). `supersededAt` is the connected-assembly state-header's date source — **byte-golden renders depend on it being a pinned story date, never a wall-clock fold time** (Task-1 fixture pins `2026-09-01T00:00:00Z`). Absent on non-superseded Objects. A separate fact from `expiredAt` — "replaced by a successor" and "our record stopped being current" are independent, and both can apply (§4.7). |
| `supersededBy` | string | — | — | Successor name written by the supersession fold (see `status`); the connected-assembly successor probe verifies a VISIBLE (non-recall-excluded) Object exists before rendering a full supersession clause — a never-created / excluded / empty successor renders a NAME-ONLY annotation, never a fabricated link (R12/C6). |
| `validFrom` / `validTo` | ISO8601 | — | `prov:generatedAtTime` / `prov:invalidatedAtTime` | Valid-time window — when the Object held in the world (declared §4.7) |
| `expiredAt` | ISO8601 | — | — | Transaction-time expiry — when our record of the Object stopped being current. **Separate from `supersededAt`** (see that row; §4.7) |
| `createdAt` | ISO8601 | ✅ | `dc:created` | Timestamp (set ON CREATE; adopted ON MATCH only when absent) |
| `updatedAt` | ISO8601 | — | `dc:modified` | **Not written by `_upsert_object`** — planned follow-up |
| `passes_frequency_gate` | bool | — | — | S5 frequency-gate result flag — false entities are still written, flagged (planned for the capture path) |
| `goalState` | string | — | — | A goal's **achievement** state — a CLOSED vocabulary (`met`, `active`, `blocked`, `unmet`, `superseded`, `abandoned`), validated at the SDK write boundary (`GOAL_STATE_VALUES`). `guideline` is **not** an achievement: whether a goal is directional is a question about **priority**, and it is unassigned. The two endings are the lifecycle's endings on this axis — `abandoned` is `deprecated` for a goal, `superseded` is `superseded` — and a write whose ending disagrees with `status` is refused. **Separate from `status`** (the lifecycle cache, above): `status` says whether the Object is current, `goalState` says whether the goal is met. `None` clears |

> **Responsibility fields (authoredBy / ownedBy / managedBy) are EDGES, not node properties** — see §3.5-3.6. `_upsert_object` does not store them as properties; they exist as graph edges to Subject nodes.
>
> **Object registration durability** — SDK-created Objects (`create_entity("object")`, the capture/entity write path) journal an `ObjectRegistered` event on their FIRST canonical registration **when the SDK is built with an `event_log_path`** (probe-gated on the deterministic `obj-<sha26(name)>` id + name — a canonical re-mention after the fix never double-journals; a probe failure fails open to a replay-safe duplicate line, and an EventAPI `add_object` mention under a non-canonical ulid followed by an SDK create of the same name double-registers by design), so capture-created Objects and their `ObjectSuperseded` folds survive `rebuild_all` (the JSONL event stream is the rebuild source). **Subjects ride the same probe-gated design (see the §4.2 note).** Byte-identity is **node-property scope**: `authoredBy`/`ownedBy`/`managedBy` ride the mirror but replay wires node properties only — responsibility edges are not durable for Object replay (EventAPI-parity, §3.5 edges). **Residual non-durable classes** (they apply without journaling, or were never registered — dropped by rebuild without a warning unless a fold references them): journals written before this contract existed (no backfill), unjournaled/legacy raw producers and journal-less SDK instances sharing the graph, and deleted Objects — `_delete_entity` leaves no tombstone, so a deleted Object **resurrects** on the next rebuild and a delete→recreate's replay first-wins the earlier incarnation's `createdAt`/fold state over the live one (the durability write-surface invariant must cover the delete side of the lifecycle). EventAPI `add_object` remains a separate unconditional producer. **Reserved-name narrowing**: `point`/`payload` props passed to an SDK Object create are dropped (unconditional, both lanes — Event-precedent parity); they are `_emit_event`-reserved kwargs and must never reach the journal mirror or live node. **createdAt synthesis**: first-registration Object creates synthesize `createdAt` pre-apply when the event carries none OR an explicit `None` (the is-None gate, mirrored for Subjects) so live == journal == replay; re-mentions and journal-less SDKs keep the projection's `coalesce($now)` behavior.

### §4.4 Document — a source, not an entity

A document is a **`:Source`** (§4.6). Its bytes live **outside the graph**, reached through the source; the Points, Events and claims **extracted** from it carry the epistemic weight, and `extractedFrom` ties each extracted Point to the source it was read from.

**A source's fields and its versioning rules are in §4.6.** This section states only what is specific to a document.

**A document is a `:Source` — and it stays TWO nodes.** D10 preserves node
TOPOLOGY: the document node (`url = <document id>`) remains **distinct** from the corpus
provenance Source (`url = source_url`). **The collapse is deliberately DEFERRED, not
done** — merging the two turns the `(corpus Source)-[:references]->(document Source)` edge
into a dropped self-loop, and the index-completeness gate's `edge` clause then reports every
doc unit **incomplete forever**. Read *"a document is a `:Source`"* as **a label and a field
model**, never as an invitation to merge the two nodes. The reasoning is pinned in
`tortoise/projection/entities.py::_upsert_document` and in the epic's test-design doc
(`docs/epics/2026-09-24-5088-d10-fold/01-test-design.md`, the *Idempotency* clause).

> **OVERRIDES:** the common *"one node per source"* default — collapsing a document and
> its provenance source onto a single node — **the document node and the corpus source stay
> two `:Source` nodes**, because the collapse drops the `references` edge as a self-loop and
> leaves every doc unit permanently incomplete for the index-completeness gate. A deliberate,
> **deferred** decision, not an accident of history.

**The self-loop is refused at the writer, and that does not lift the deferral.**
`link_source_to_entity` (`tortoise/projection/edges.py`) writes NO `references` edge when a
`Source` target resolves to the same node as the source — the node is still merged, only the
degenerate edge is suppressed — so a caller that hands it colliding keys can no longer mint
`(:Source {url:'X'})-[:references]->(:Source {url:'X'})`. That guard makes the *edge* safe; it
does **not** merge the two nodes, because the collapse's cost in the deferred decision above is
node TOPOLOGY (two addresses for one document), not merely the edge.

**`documentKind` — the genre axis.** When `sourceKind: document`, `documentKind` names the **genre** of the document. The core vocabulary and the pack extension point are in **§5** (single home).

**`documentKind` is not `sourceKind`.** `sourceKind` answers *what kind of source this is* (`document`, `conversation`, `github_issue`, `agentSession` — the full vocabulary is in §5); `documentKind` answers *what genre of document it is*. Different questions — **neither absorbs the other**.

**Liveness is a read, not a field.** *"Is this source still good?"* is answered from the entities extracted from it — high confidence, not superseded, not under a `NAND`. **The source inherits its health from its contents**, so nothing is stored to keep in sync and nothing can drift from the graph it describes.

**`doc_status` is RETIRED, not absent — a surviving one is READ, and `captured`/`extracted` outrank `needs_extraction`.** D10 removed the field from the write path: the post-D10 fold never sets it, and completion **REMOVEs** it (`SET s.needs_extraction = false REMOVE s.doc_status`), so a node this product writes today carries none. Retiring a field does not delete the data already on disk, so **both extraction readers honour a `doc_status` they find**, in this order: `captured` ⇒ pending (even beside the pre-D10 writer's boilerplate `needs_extraction: false`); `extracted` ⇒ done (even beside a stale `needs_extraction: true`); an explicit `needs_extraction: true` ⇒ pending (read *before* the generic retired-value arm, so a `draft` node carrying it is still discovered); any other retired value (`draft`) ⇒ pending for `--upgrade` **only** — `--upgrade-all` does not select it and `--include-unstamped` does not change that, since that flag widens only the *no-property* arm. With no `doc_status` and no `needs_extraction`, an **ABSENT** value is "no claim", never "done". So read absence of `doc_status` as *nothing retired to honour*, never as *the strongest possible signal* — that inversion is exactly the defect to avoid.

**Currency is also a read, and it needs an anchor.** Whether the extracted Points are *about the current content* is decided by the **version recorded on the extraction link** against the source's current version (§4.6) — not by any stored flag.

**`content` is not a graph property.** A document's bytes live in raw storage (D30, §2); the graph holds the entity that references them, never the text.

> **Meeting capture — Document ↔ Source pairing:** a captured meeting lands as
> a Document (`documentKind: transcript` for the
> raw `meeting_transcript` capture; `documentKind: meetingNotes` for structured
> `meeting_minutes`) whose provenance Source carries the matching `sourceKind`
> (`meeting_transcript` / `meeting_minutes` — §4.6/§5). Both Source kinds
> register NEUTRAL (no credibility inheritance); the tier question is left open
> (§5).

```

> **Approve:** ☐ text as written · ☐ with changes (list them) · ☐ reject (§4.1–§4.4 keep their numbers)
>
> **Approve the wording changes separately:** ☐ W-7 ☐ W-8 ☐ W-9 ☐ W-10 ☐ W-11 ☐ W-12 ☐ W-13 ☐ W-14 ☐ W-15 ☐ W-16 ☐ W-17 ☐ W-18 ☐ W-19 ☐ W-20 · ☐ reject all
>
> **P-4 (rewriting an owner-approved sentence):** ☐ approve ☐ reject (restore the sentence + its `#11915` status note)

### 3.9 Anything not classifiable

**Nothing in §4.1–§4.4 is unclassifiable.** All 70 references are covered above; the only text that
leaves the model is at rows `4.1-j`, `4.1-l`, `4.1-m`, `P-4`, and `4.4-f`'s two `TODAY` sentences —
each quoted verbatim in the changelog extract. The bare-epic removals (`4.1-o`, and batch 2's §3.1 correction at `B-59`) are the same class and are recorded there too.

⚠️ **Two issue numbers survive inside PATHS, not as citations**, and are left alone deliberately:
`tests/test_subject_supersession_1423.py` (§4.2) and the PGlite `9972-cycle` case name (§3.1) are
real identifiers in the tree. Renaming a test to satisfy a grep is a code change with its own risk
and is **not** part of this proposal. The acceptance check counts `#NNNN`, not bare digits, and
stays at zero.

---

## Batch 4 — §4.5–§4.8 (Event, Source, Temporal Model, Session)

### 4.1 Per-section table

| Section | lines | Refs before | Refs removed | Classes | D1 before → after | Rules moved | Unclassifiable |
|---|---|---|---|---|---|---|---|
| §4.5 Event | 518–562 | 15 | **15** | B 14 · D 1 | 3 → 3 | 0 | none |
| §4.6 Source | 563–637 | 46 | **46** | B 41 · C 5 | 31 → 31 | 0 | none |
| §4.7 Temporal Model | 638–846 | 36 | **36** | B 32 · C 2 · D 2 | 18 → 18 | 0 | none |
| §4.8 `:Session` | 847–973 | 8 | **8** | B 4 · C 4 | 2 → 2 | 0 | none |
| **Total** | — | **105** | **105** | B 91 · C 11 · D 3 | 54 → 54 | **0** | none |

**D1 is exactly unchanged: 54 → 54, with the same token mix (`never` 43 · `required` 5 · `must` 4 ·
`always` 2).** This is the batch where a silent rule loss would be most likely — 46 references in
§4.6 alone — and it is the batch where the count gives the strongest evidence nothing was dropped:
not one normative keyword moved, which means every clause that lost a citation kept its sentence.

**The three `D` occurrences (`#7818` ×2, `#11825`) are open-decision pointers inside prose**, not rules: one
names where a half-open/closed boundary decision is being tracked, the other where an
accepted-but-half-honoured state is tracked. Both sentences survive with the pointer removed; both
are quoted in the changelog.

### 4.2 Dispositions — §4.5 Event (15)

| # | Ref(s) | Line | Class | What happened to the semantic content |
|---|---|---|---|---|
| 4.5-a | `#909` | 522 | **B** | *"a content-addressed form (hash of session_id + captured_at — deterministic MERGE anchor, #909 §4.3 #3)"* → no number. The content-addressing rule stays. |
| 4.5-b | `#909` | 526 | **B** | *"Registered #909 §4.3 #2; written on `AgentSession` …"* → *"Written on `AgentSession` …"*. The bi-temporal-complement rule and the write-path list stay. |
| 4.5-c | `#1533` ×2 | 527 | **B** | *"(E1, #1533)"* → *"(E1)"*; *"Registered #1533 E1; the extractor path gates it …"* → *"The extractor path gates it …"*. |
| 4.5-d | `#330`, `#900` | 530 | **B** | *"(text-mode, universal newlines — CRLF-immune; #330/#900)"* → *"(… CRLF-immune)"*. The hash definition stays. |
| 4.5-e | `#909` | 531 | **B** | *"(registered #909 §4.3 #13; planned for the capture path, slice 5+)"* → *"(planned for the capture path)"*. |
| 4.5-f | `#909` | 533 | **B** | *"**agentSession Event (#909 §4.3 #1):**"* → *"**agentSession Event:**"*. The `AgentSession`/`sessionCaptured` alias rule and the "no migration" clause stay. |
| 4.5-g | `#7853` ×2 | 544, 559 | **B** | *"(`tortoise/sdk.py`, #7853)"* → *"(`tortoise/sdk.py`)"*; *"`_promote_incident_operators` (#7853)"* → no number. The **rule** — a writer-supplied `status` prop gates operator promotion like a draft Point — stays. |
| 4.5-h | `#2788` | 548 | **B** | *"**(#2788)** The names this lane historically carried — `name`, `eventStatus`, `createdAt` —"* → *"**The names this lane also carries** — `name`, `eventStatus`, `createdAt` —"* (W-21). Same three props, same rule that none is read back as Event state. |
| 4.5-i | `#4282` | 552 | **B** | *"one of the 26 dead public SDK methods deleted by `#4282` (2026-10-27, owner ruling) — so nothing …"* → *"one of the 26 dead public SDK methods — so nothing …"*. The **no-reader-left** claim and its consequence stay. |
| 4.5-j | `#2729` | 560 | **B** | *"The pack-declared fold generalization (#2729) is Object-scoped"* → no number. The scope rule stays. |
| 4.5-k | `#2725` | 561 | **B** | *"the Event-carries-state question was answered **no** in #2725."* → *"… answered **no**."* The answer (no) is the rule; the tracker goes. |
| 4.5-l | `#11825` | 561 | **D** | *"The accepted-and-half-honoured state it has today — the write is accepted, belief honours it, and every retrieval leg ignores it — is tracked at **#11825**."* → *"The accepted-and-half-honoured state it has today — the write is accepted, belief honours it, and every retrieval leg ignores it."* ⚠️ **The behavioural description stays** (it is the contract); only "tracked at" goes. |

### 4.3 Dispositions — §4.6 Source (46)

| # | Ref(s) | Line | Class | What happened |
|---|---|---|---|---|
| 4.6-a | `#398`, `#2726` | 568 | **B** | *"(dual-write, #398)"* → *"(dual-write)"*; *"register **NEUTRAL** (#2726)"* → no number. The tier-mirror rule and the NEUTRAL registration stay. |
| 4.6-b | `#5024` ×3 | 573 | **B** | *"**Journalled since `#5024`:**"* → *"**Journalled:**"*; *"Pre-`#5024` the write bumped it in place with no record of its own, so a rebuild re-stamped it (`#5024`)"* → *"bumping it in place with no record of its own would let a rebuild re-stamp it"* (W-22, past→conditional, same constraint). |
| 4.6-c | `#3642` ×2, `#3644` ×2, `#3654`, `#5097`, `#3590` | 574, 575 | **B** | The `validFrom`/`validTo` cell: *"(declared §4.7, #3642)"* → *"(declared §4.7)"* (twice); *"**`validFrom` is written since #3644**"* → *"**`validFrom` is written**"*; *"§4.1/#3654 forbids"* → *"§4.1 forbids"*; *"(tracked on **#3644**, pinned by …)"* → *"(pinned by …)"*; *"Since **#5097** the capture writer's metadata DOES replay"* → *"The capture writer's metadata DOES replay"*; *"no such path exists yet (#3590 S2)"* → *"no such path exists yet"*. **Every window rule survives**, including the observation-instant rule, the never-borrow-the-write-clock rule and the present-but-stale gap. |
| 4.6-d | `#3590` | 575 | **B** | *"the removal field's meaning is exactly the decision **#3590** S2 owns"* → *"the removal field's meaning is undecided"*, and the parenthetical after it is re-anchored to "the same open question" so it no longer points at the deleted referent. |
| 4.6-e | `#5127`, `#5142` | 577 | **C** | *"In `_SOURCE_HANDLED` since D10 — **the model version is v3.15 (§4.4), the code half that moved it is PR #5127** (`#5142`)"* → *"In `_SOURCE_HANDLED` since D10"*. The field-membership rule stays; the PR/issue history goes. |
| 4.6-f | `#398` | 579 | **B** | *"(falls back to `ingestedAt` — the pipeline-arrival proxy, #398)"* → no number. |
| 4.6-g | `#909` ×2 | 580, 581 | **B** | *"(plan-defined, #909 §4.3 #6; written by the capture path, slice 5+)"* → *"(plan-defined; written by the capture path)"*; *"(registered #909 §4.3 #13; planned …, slice 5+)"* → *"(planned for the capture path)"*. |
| 4.6-h | `#3998` | 583 | **B** | *"(#3998 owner ruling, Option A, 2026-10-14)"* deleted. ⚠️ The **rule stays intact**: `offline`/`access_revoked` are conditions, observed live, never stored, read fail-closed as `unrecognised`. Only the ruling's provenance goes. |
| 4.6-i | `#398` ×3 | 592, 593, 594 | **B** | *"(v3.2, #398)"* → *"(v3.2)"*; *"derivation time (#398)"* → *"derivation time"*; *"Cache freshness stamp (#398)"* → *"Cache freshness stamp"*. The "derived cache is never authoritative" rule stays. |
| 4.6-j | `#5082`, `#5142` | 596 | **B** | *"which §4.6 REQUIRED fields a document actually supplies (`#5082`; recorded `#5142`, closed here)"* → *"… actually supplies"*. The scoping note and its REQUIRED table stay. |
| 4.6-k | `#5082` ×2, `#398` ×2 | 600 | **B** | *"✅ **written since #5082 by every writer …**"* → *"✅ **written by every writer …**"*; *"— #398's never-overwrite contract"* → *"— the never-overwrite contract"*; *"**fills a pre-#5082 NULL**"* → *"**fills a NULL**"*; *"moves (#398)"* → *"moves"*. The three-writer list and the CREATE-ONLY rule stay. |
| 4.6-l | `#5592`, `#5422`, `#900` | 601 | **C** | *"Since PR #5592 (`#5422`, `700960943`) the extraction-ingest path"* → *"The extraction-ingest path"*; *"The `#900` index path"* → *"The index path"*. **The preserve/replace gate rule is untouched** — absent/empty preserves, empty stored is completed, a differing hash replaces on the SAME node. |
| 4.6-m | `#5013`, `#5082` | 604 | **B** | *"The recorded ruling is **#5013 Q2** (…)"* → *"The recorded ruling is (…)"* restructured (W-23); *"— the `#5082` write implements it"* → no number. **The ruling itself — KEEP BOTH AXES — stays verbatim.** |
| 4.6-n | `#8525` | 606 | **B** | *"**What the write does and does not move downstream (`#8525` review).**"* → *"**What the write does and does not move downstream.**"*. The no-tier / no-inheritance / D3-candidate-pool rules stay. |
| 4.6-o | `#398` | 608 | **B** | *"the #398 word-point un-freeze requires"* → *"the word-point un-freeze requires"*. |
| 4.6-p | `#3644` ×2, `#5097` | 630 | **B** | *"**… in the same act (`#3644`).**"* → *"**… in the same act.**"*; *"since **#5097** its METADATA block does replay"* → *"its METADATA block does replay"*; *"… tracked on **#3644**; the gap is pinned (never silenced) by"* → *"… and the gap is pinned (never silenced) by"*. The half-open `[validFrom, validTo)` contract and the pinned gap both stay. |
| 4.6-q | `#5024`, `#3644` ×2, `#3590`, `#3980`, `#4021`, `#5358`, `#5360` | 636 | **B** | *"(the anchor half is `#5024`, the window half `#3644`)"* → deleted; *"The *window* half (#3644):"* → *"The *window* half:"*; *"**#3590** S2's decision"* → *"undecided"*; *"needs the inversion guards the Point window needed (#3980, #4021, #5358, #5360)"* → *"… needed"*. **Every "what remains open" clause stays** — the node's `validTo`/`expiredAt` and the caller-asserted start. |

### 4.4 Dispositions — §4.7 Temporal Model (36)

| # | Ref(s) | Line | Class | What happened |
|---|---|---|---|---|
| 4.7-a | `#3642` | 641 | **B** | *"One canonical name per slot (issue #3642)."* → *"One canonical name per slot."*. The "this section is authoritative" rule stays. |
| 4.7-b | `#3654` ×2 | 650, 807 | **B** | *"(`ConversationMiner._temporal_wire`, #3654)"* → *"(`ConversationMiner._temporal_wire`)"*; *"(#3654), so an absent `validFrom` is an **open start**"* → *"so an absent `validFrom` is an **open start**"*. |
| 4.7-c | `#1538`, `#3980`, `#5360` ×2, `#4021`, `#5358` | 651 | **B** | The `validTo` cell: *"(`supersede_point`, E6 #1538)"* → *"(`supersede_point`)"*; *"gap or overlap the chain (#3980)"* → no number; *"lands on `now` (#5360)"* → no number; *"before any mutation (#4021)"* → no number; *"closed on the write path (#5360)"* → no number; *"the window-agnostic route (#5358)"* → no number. **All six validation rules survive** (instant equality, inversion refusal, orderability residual, exemption). |
| 4.7-d | `#7818` | 653 | **D** | *"**The coverage boundary is stated here because it was not (#7818).**"* → *"**The coverage boundary is stated here on purpose.**"* (W-24). The boundary statement itself is below it and untouched. |
| 4.7-e | `#5374` ×2 | 657, 681 | **B** | *"declares (#5374), whose agreement test"* → *"declares, whose agreement test"*; *"permits `validTo == validFrom` (#5374)"* → *"permits `validTo == validFrom`"*. The agreement test's pinning of `t == vT` and `[t,t]` is the sentence, and stays. |
| 4.7-f | `#4021` | 669 | **B** | *"the #4021 defect class"* → *"the inverted-window defect class"*. Same defect, named by what it is. |
| 4.7-g | `#5362` | 675 | **B** | *"evaluated as R2 of the #5362 plan)"* → *"evaluated as part of that decision)"* — `R2` was a work-stream label inside a plan this document does not name. ⚠️ The same unanchored label also appeared two paragraphs later as *"the signal R2 names"*, which now reads *"the signal that decision names"*; both are fixed together. |
| 4.7-h | `#7818` | 676 | **D** | *"or a third shape — tracked at **#7818**. Until it is decided"* → *"or a third shape. Until it is decided"*. **The three options and "which is canonical is an open decision" stay.** |
| 4.7-i | `#9202` ×3, `#9408` | 685, 714, 718 | **B** | *"(#9202 — a bound that names none is not a bound …)"* → same without the number; *"**state is settled** (#9202, owner ruling 2026-10-25, option B)"* → *"(owner ruling, option B)"*; *"is covered too (#9202's coverage half, #9408)"* → *"is covered too"*. Every END-state rule (`unknown`, `END_KNOWN` = dated not present, `END_OPEN` reserved for absent) stays. |
| 4.7-j | `#5361` | 689 | **B** | *"#5361's `malformed` flag"* → *"the `malformed` flag"*. |
| 4.7-k | `#8337`, `#8487` | 693 | **B** | *"(#8337's rename, then #8487's merge into the same file, +291 lines)"* → *"(a rename, then a merge into the same file, +291 lines)"*. ⚠️ Note this is the source's own admission that **line numbers rot** — the reason this document cites symbols. |
| 4.7-l | `#2349`, `#2350` ×2 | 730, 735 | **B** | *"hazard this vocabulary exists to prevent (#2349/#2350)"* → no numbers; *"**Per-end precision and the 3-state end (#2350, E-prec).**"* → *"**Per-end precision and the 3-state end.**"*. The three-valued END and the precision vocabulary stay. |
| 4.7-m | `#9244` | 752 | **B** | *"**A declaration annotates a bound; it cannot summon one** (#9244)"* → no number. The whole orderability-gate rule stays. |
| 4.7-n | `#9729` | 759 | **B** | *"**no** precision property (#9729)."* → no number. |
| 4.7-o | `#5024`, `#3644` ×2, `#5097`, `#3590` | 803 | **B** | The `†` footnote: *"since `#5024` it journals … and since `#3644` that record also carries …"* → *"it journals … and that record also carries …"*; *"has replayed since #5097"* → *"replays"*; *"— **#3644**, pinned by"* → *"— pinned by"*; *"**#3590** S2, which also owns `expiredAt`"* → *"which also owns `expiredAt`"*. **Both ⚠️ reasons and the `—` (unreachable) discipline stay.** |
| 4.7-p | `#2295` | 833 | **B** | *"⚠️ SDK extra-prop (projection-owned planned, #2295)"* → *"(projection-owned planned)"*. |
| 4.7-q | `#5127`, `#5142` | 837 | **C** | *"moved into `_SOURCE_HANDLED` by D10 (the model version is v3.15, §4.4; the code half that moved it is PR #5127 — `#5142`)"* → *"(the model version is v3.15, §4.4; moved with it)"*. |
| 4.7-r | `#11827` | 843 | **B** | *"`asOf` was retired for exactly this (#11827)."* → no number. The "a value carries no temporal slot" rule stays. |

### 4.5 Dispositions — §4.8 `:Session` (8)

| # | Ref(s) | Line | Class | What happened |
|---|---|---|---|---|
| 4.8-a | `#3551` | 910 | **B** | *"`tortoise/sdk.py::_write_session_and_turns` (#3551)"* → the symbol stays, the number goes. |
| 4.8-b | `#3086` | 911 | **B** | *"`_TURN_WRITE_CYPHER` (#3086)"* → no number. The one-write-primitive rule stays. |
| 4.8-c | `#4282` | 913 | **B** | *"(the private door #4282 left in place of the retired public name …)"* → *"(the private door in place of the retired public name …)"*. |
| 4.8-d | `#3554` ×2 | 921, 969 | **C** | *"The #3554 retrofit audit (…)"* → *"The retrofit audit (…)"*; *"at the `#3554 amendment`"* → *"at the retrofit amendment"*. The audit's three recorded gaps and their reasoning stay. |
| 4.8-e | `#2335` | 930 | **B** | *"arm the #2335 TRUE-retry gate"* → *"arm the TRUE-retry gate"*. The gate is **named**, not weakened. |
| 4.8-f | `#10000`, `#10011` | 953 | **C** | ⚠️ **A heading loses its tracker and a "after X" history clause** (W-25): *"**`turn_count` with no turn member at all — the store, not a shortfall (#10000)** — after `#10011` the capture writer stores …"* → *"**… the store, not a shortfall** — the capture writer stores …"*. The whole verdict — `retro_quantifiable: False`, the `turns_record_held` bucket, the placement rule — stays. |

### 4.6 Wording changes (not citation deletions) — rejectable individually

| # | Where | Change | Why |
|---|---|---|---|
| **W-21** | §4.5 `name`/`eventStatus`/`createdAt` note | *"**(#2788)** The names this lane historically carried"* → *"**The names this lane also carries**"* | The list is a current property set, not a history. |
| **W-22** | §4.6 `updatedAt` | *"Pre-#5024 the write bumped it in place with no record of its own, so a rebuild re-stamped it"* → *"bumping it in place with no record of its own would let a rebuild re-stamp it"* | Past failure → the constraint that prevents it. |
| **W-23** | §4.6 document ruling | *"The recorded ruling is **#5013 Q2** (*\"KEEP BOTH AXES…\"*)"* → *"The recorded ruling is *\"KEEP BOTH AXES…\"*"* | The ruling is the sentence; the tracker is not needed to state it. |
| **W-24** | §4.7 coverage boundary | *"stated here because it was not (#7818)"* → *"stated here on purpose"* | "Because it was not" is history of the document, not of the model. |
| **W-25** | §4.8 `turn_count` heading | tracker removed from the heading; *"after #10011"* → no clause | The verdict is the heading; the delivery order is history. |
| **W-26** | §4.7 END state | *"**state is settled** (#9202, owner ruling 2026-10-25, option B)"* → *"(owner ruling, option B)"* | Keeps the authority (an owner ruling, option B), drops the date and number. |

### 4.7 Proposed text — §4.5–§4.8 (replaces `ONTOLOGY.md` lines 518–973)

<!-- PROPOSED:§4b -->
```markdown
### §4.5 Event

| Field | Type | Required | ISO/PROV/DC | Meaning |
|-------|------|----------|-------------|---------|
| `eventId` | ULID / content-addressed | ✅ | `dc:identifier` | Unique occurrence ID — ULID by default; the **agentSession Event uses a content-addressed form** (hash of session_id + captured_at — deterministic MERGE anchor) |
| `eventKind` | string | ✅ | — | Core vocabulary in **§5** (meeting, decision, deployment, turn, humanApproval, …) + pack eventKinds |
| `format` | string | — | `dc:format` | Storage format (jsonl default, markdown) |
| `startedAt` / `endedAt` | ISO8601 | — | `prov:startedAtTime` / `schema:startDate` | Valid-time extent — Event's **named alias** of `validFrom`/`validTo` (§4.7), not a distinct axis |
| `capturedAt` | ISO8601 | — | — | Event's **transaction-time start** (§4.7) — when our record of the occurrence was captured, the bi-temporal complement to `startedAt`/`endedAt` (valid time). Written on `AgentSession` (+ extracted-occurrence) Events by the hosted capture/commit endpoint and the session indexer; not yet on every Event write path |
| `started_at` | ISO date ≤40 | — | `prov:startedAtTime` | **Payload-level** valid time on extracted-occurrence events (E1): the extractor emits it from the session-date anchor; graph property stays `startedAt` (coalesce `started_at → captured_at → now` server-side). The extractor path gates it via `_valid_iso_date` |
| `subject` | SubjectID | — | `prov:wasAssociatedWith` (inverse) | Who performed the event (mirrors `performs` edge) |
| `object_name` / `object_type` | string | — | `prov:used` | What was acted on / produced |
| `file_hash` | string | — | — | SHA-256 of the file's UTF-8 text content (text-mode, universal newlines — CRLF-immune) |
| `is_episodic` | bool | — | — | Quota exemption discriminator — true on capture-path episodic Events (planned for the capture path) |

> **agentSession Event:** the canonical eventKind for session-capture Events is **`AgentSession`** — EXACT code spelling (capital A; sdk.py `ingest_corpus`, session_indexer.py); `sessionCaptured` (the core kind still written by the regex capture path) is an **alias of the same concept** — both remain valid kinds, **no migration**. The capture graph's `:Session` node (session container, `CONTAINS` → turn Points) also carries `is_episodic: true` — the quota `sessions` branch counts `MATCH (s:Session)` (plan §4.4, slice 2).

**An Event is a dated fact — it carries no projection-owned state.** An Event
records *that something happened*. The current state of the thing it concerns
lives on the Object (or Subject) it names, never on the Event: the projection
declares **no Event `status` field and no Event state fold** — a later Event
does not rewrite an earlier one, and the consistency gate compares `status` only
for the Object/Subject families. The Event MERGE handles a declared field set and
the projection's extras lane carries the rest of the payload as **content the
writer supplied** — a prop lane, never a projection-owned state lane. A
writer-supplied `status` prop is therefore not projection-owned, but it is **not
inert**: `_promote_incident_operators` (`tortoise/sdk.py`) reads an Event
endpoint's `status` as a live/draft gate, so a `status` prop on an Event
suppresses operator promotion exactly as a draft Point does.

> **The names this lane also carries** — `name`, `eventStatus`,
> `createdAt` — are props, not projection state, and none of the three is read
> back as Event state. ⚠️ `name` has **no reader left**: its only reader was
> `sdk.backfill_about_entities` (it matched an Event against a Point's content), one of the 26
> dead public SDK methods — so nothing in the
> served package performs that match any more. `eventStatus` is an **ingest**
> marker (`scheduled` → `completed`) written by the session/meeting indexers,
> deliberately outside the consistency gate's `status` comparison (which is
> Object/Subject-only), and the typed commit path cannot set it at all
> (`CommitEvent` is `extra="forbid"`). The `status` key is distinct from those
> three: it is carried by the same extras lane, and it IS read as state by
> `_promote_incident_operators` — see the note above. The pack-declared
> fold generalization is Object-scoped and does not extend to Events —
> the Event-carries-state question was answered **no**. The accepted-and-half-honoured state it has today — the write is accepted, belief honours it, and every retrieval leg ignores it.

### §4.6 Source

| Field | Type | Required | ISO/PROV/DC | Meaning |
|-------|------|----------|-------------|---------|
| `url` | string | ✅ | `dc:source` / `pav:retrievedFrom` | Permalink back to original |
| `sourceKind` | string | ✅ | — | Extensible source TYPE vocabulary — **the core list and the pack extension point are in §5** + `source_credibility.SOURCE_KIND_DEFAULTS` are canonical; e.g. github_issue, github_pr, slack_message, linear_card, linear_cycle, document, agentSession, meeting_summary, meeting_transcript, meeting_minutes. Tier-form values (T0-T4) mirror to `credibilityTier` (dual-write). The meeting kinds register **NEUTRAL** — the tier question is deliberately left open |
| `credibilityTier` | string | — | — | T0-T4 credibility tier — the property the inheritance adapter reads (v3.2) |
| `contentHash` | string | ✅ | `premis:messageDigest` | **Version anchor** — the digest of the content read. A differing hash on re-fetch is a **new version**, not an edit (see *Versioning* below) |
| `title` | string | — | `dc:title` | Human-readable label. Defaults to url |
| `ingestedAt` | ISO8601 | ✅ | `pav:importedOn` | When Tortoise first saw this source — **Source's spelling of the canonical transaction-time start `createdAt`** (§4.7) |
| `updatedAt` | ISO8601 | — | `dc:modified` | Last version transition. **Journalled:** a hash transition emits `SourceVersioned` carrying `previousContentHash` + the recorded instant, and the fold reads that instant — never the replay clock. Bumping it in place with no record of its own would let a rebuild re-stamp it. |
| `validFrom` / `validTo` | ISO8601 | — | `prov:generatedAtTime` / `prov:invalidatedAtTime` | **The CURRENT version's valid-time window** — when the content held in the world (declared §4.7). **`validFrom` is written**: `_upsert_source` sets it to the version's OBSERVATION instant (the same instant `create_source` mints for `updatedAt`, one clock read, carried on the record) through the same hash-diff gate as `version`/`updatedAt` — a create, a stub completion, or a real transition, never a no-op re-check. A version has no content-side date of its own (a version *is* "the content as read at an instant"), which is why the observation instant is definitional here and not the borrowed write clock §4.1 forbids for a Point; a caller-asserted start (a known publication date) is a separate additive input, deliberately not accepted yet. Coverage is the `create_source`/`_upsert_source` path only — a stub minted by `_link_source` writes none, and **absent means an open/unbounded start** (the replay never stamps one: `$vf` is recorded, never `coalesce($vf, $now)`). **Two further `:Source` writers move the version anchor without moving the window** — `_upsert_document` (the `DocumentCreated` fold) and `_materialize_session_source` (the capture path) — so a source this path stamped can be left with a **present-but-stale** start (pinned by `test_pinned_gap__*`). The capture writer's metadata DOES replay (a trailing `SourceCreated` that carries the node's `validFrom` verbatim, so a rebuild no longer CLEARS a window); it still never MOVES one. **`validTo` is written as a JOURNAL fact, not a node property**: the node holds the CURRENT version, whose window is open (absent IS the open end — Cypher cannot write NULL), so the prior version's close lives on the `SourceVersioned` record as `previousValidTo`, equal to that record's `validFrom` by construction (§4.6's "closes the old one in the same act"). A `:Source` REMOVAL sets the node's `validTo`; no such path exists yet. A prior version's window is a journal record — see *Versioning* |
| `expiredAt` | ISO8601 | — | — | Transaction-time expiry — when our record of this version stopped being current (declared §4.7). Still unwritten: no `:Source` removal path exists, and the removal field's meaning is undecided — the same open question that leaves Point's `expiredAt` unable to distinguish *replaced* from *withdrawn*. A version transition does NOT set it — `validTo`/`previousValidTo` is the window fact |
| `documentKind` | string | — | `bibo:Document` subclasses | **Genre**, when `sourceKind: document` — the core vocabulary is in **§5**. Distinct from `sourceKind` (§4.4) |
| `format` | string | — | `dc:format` | Storage format (markdown, jsonl, yaml, cypher). In `_SOURCE_HANDLED` since D10 |
| `externalId` | string | — | `dc:identifier` (external) | System-of-record ID (Slack ts, GitHub issue #) |
| `sourceDate` | ISO8601 | — | `dc:date` | Evidence-age clock for recency decay (falls back to `ingestedAt` — the pipeline-arrival proxy) |
| `provenance_spans` | JSON | — | — | Window spans derived from the capture path's `provenance_refs` (plan-defined; written by the capture path) |
| `is_episodic` | bool | — | — | Quota exemption discriminator — true on the session Source (planned for the capture path) |
| `version` | integer | ✅ | — | **Monotonic version counter**, 1 at creation, +1 on each content-hash change. The cheap ordinal beside `contentHash`'s identity |
| `rawState` / `rawStateAt` | string / ISO8601 | — | — | **The raw-availability axis** — the *third* value on a source record, after identity (`url`) and version (`contentHash`); `STORAGE-ARCHITECTURE.md` §9.4 ③ places it there, not as a fourth kind of source. `rawState` is **recordable** as `present` (the documented way back) or `deleted` — **a deletion is an ACT**, recorded durably and BOUNDED by the deletion-marker TTL (**10 days** — Cassandra's `gc_grace_seconds` default; metadata about the link back to the raw, never a copy of it and never a retention period on user content — **R1**, whose authority is `docs/retention-and-deletion.md`). `rawStateAt` is the instant the state was **recorded** — minted on **every** state-carrying write, including `rawState='present'`; when `rawState` is `deleted`, the marker's expiry policy is derived from it. **`offline` / `access_revoked` are CONDITIONS, observed live and NEVER stored**: an observation is **caller-supplied** at read time and never persisted, so a read holding no observation reports only the recorded act, and a condition found in stored data is not part of this axis — the availability resolution reads it fail-closed as `unrecognised`. **Read-time contract:** a recorded act WINS over any observation, so a probe can never un-delete a memory; with no observation the read reports the record it holds and always emits the entry (never an empty result standing in for an unreachable raw); expiry authorises a LATER, JOURNALLED drop of the marker and never resurrects on read. The state rides the source write and, on a **version transition**, the **`SourceVersioned`** record; the property is refused on the generic entity surface and via `props` |
| `topics` | array | — | `dc:subject` | Topic list captured from the source |
| `summary` | string | — | `dc:description` | Summary captured from the source |
| `sessionId` | string | — | — | The session this source arrived in |
| `eventId` | string | — | — | The Event that ingested this source — the 1-hop audit hop to the mechanism snapshot (§3.5) |
| `story_arc` | string | — | — | Arc continuation, for a source captured as part of a longer session narrative |
| `sourcePath` | string | — | — | Filesystem path, for locally-ingested sources |
| `needs_extraction` | bool | — | — | Explicit signal that this source is awaiting extraction (`--upgrade-all` discovery) — read **after** `doc_status`'s `captured`/`extracted` arms but **before** the generic retired-value arm, so an explicit `true` is discovered even on a `draft` node, while `captured`/`extracted` outrank it (§4.4). `true` ⇒ pending, `false` ⇒ done, and an **absent** value means "no claim", not "done" (a document Source minted with no signal is selected only by `--upgrade` or by an explicit `--include-unstamped`) |
| `_searchText` | string | — | — | Derived full-text index field (coalesce-on-create, overwrite-on-hash-change) |
| `reliability` | float 0..1 | — | — | DERIVED query-time projection (mean of the modulated Beta prior) — documented cache, never authoritative (v3.2) |
| `reliabilityComponents` | JSON | — | — | Cache metadata: tier, decay, factor, assessment_count, derivation time |
| `reliability_derived_at` | ISO8601 | — | — | Cache freshness stamp |

> **⚠️ Document `:Source` scoping — which §4.6 REQUIRED fields a document actually supplies.** §4.4 says a document is a `:Source`, but a document `:Source` is written by **different paths** from the provenance `:Source` (`_upsert_source`/`create_source`): the `DocumentCreated` fold (`entities.py::_upsert_document`), the `objectType='Document'` event branch (`entities.py::_event_plain_merge`), and the hosted `/v1/sessions` transcript MERGE (`hosted_api.py::_execute_commit_writes`) — **all three** are named at the row below. The `sourceKind` gap this note recorded is **closed**; `contentHash` stays **conditional on the read path**:
>
> | REQUIRED field | on a document `:Source` |
> |---|---|
> | `sourceKind` | ✅ **written by every writer that creates a document `:Source`** — each stamps `'document'` **CREATE-ONLY** (`coalesce` writes only when the value is absent — the never-overwrite contract; the event branch also **fills a NULL** on its match arm): the `DocumentCreated` fold (`entities.py::_upsert_document`), the `objectType='Document'` event branch (`entities.py::_event_plain_merge`), and the hosted `/v1/sessions` transcript MERGE (`hosted_api.py::_execute_commit_writes`). A document node therefore carries the **TYPE** axis beside its `documentKind` **GENRE** axis (§4.4 — different questions, neither absorbs the other). The value is the registered §5 term and is **NEUTRAL** in `SOURCE_KIND_DEFAULTS`, so no credibility inheritance moves; it agrees with `_mint_source_stub`'s own non-`session:` default (`projection/edges.py`). `list_sources()` still filters on `documentKind` — a document node is not a *provenance* source |
> | `contentHash` | ⚠️ **written on the path that READS content.** The extraction-ingest path writes it through a preserve/replace gate: an absent or empty (`''`) hash preserves the stored one, an empty stored hash is completed, and a differing hash **replaces it on the SAME node** (never a second `:Source`). The index path (`_doc_write`) and `--capture-metadata` deliberately emit none (no content read — the latter skips extraction by design), so a document node can sit with `contentHash = NULL` until extraction; the `_required_sweep` harnesses scope the `contentHash` clause to provenance Sources for exactly this reason |
> | `version` | ✅ **written by the same clause**, advancing with the hash exactly as §4.6 specifies ("1 at creation, +1 on each content-hash change"), so the pair §4.6 calls a version anchor can no longer be half-written |
>
> The recorded ruling is *"KEEP BOTH AXES. A source is `sourceKind: document` **and** carries a genre"*, plus §4.6's REQUIRED marking; it is **not** a licence to read §4.4 as *"a document is a `:Source` that carries no `sourceKind`"*, and **not** a licence to close it by collapsing the two nodes (§4.4's `OVERRIDES`).
>
> **What the write does and does not move downstream.** It does **not** move credibility: a
> document node resolves **no tier** (`SOURCE_KIND_DEFAULTS['document'] is None`), so it contributes no group
> to `_apply_source_inheritance`'s aggregate; and the word-point un-freeze requires a **resolved
> evidence body** (a tiered source, or an ORIGINATING `agentSession` whose weak body is the evidence), so a
> word Point whose only source is a neutral document keeps its authored `set-by-author` baseline instead of
> being re-labelled `inherited-from-source` with nothing inherited — the false provenance report
> (`calibrate_summary` → "inherited from its source's trust rating" for a source with no rating) that the
> un-guarded write produced on the primary path. It **does** change candidate generation, and that is
> intended under **D3** ("registered kinds of any tier count"): `_cross_lens_pool` gates on
> `s.sourceKind IN $registered`, and §5 registers `'document'`, so Points extracted from a document are now
> eligible for the cross-lens candidate pool.

**Versioning.** Identity is `url`; `contentHash` identifies a **version** of that identity. A re-fetched source whose content differs is a **new version, never an edit** — so **identity is stable and version is per-read**, and the two are never conflated.

**The version history is append-only in the journal; the graph shows the current version.** The graph is a *projection* of the journal (§3), so the Source node carries **one** version — the current one — with its `contentHash`, its `version` ordinal, and the **current** version's valid-time window (`validFrom`/`validTo`). A version transition **appends a journal record** that closes the previous version's window and records the new one; it does not rewrite an older graph node, because there is no older graph node to rewrite. **A prior version's window is a journal fact, recoverable by replay** — never a second Source node per `url`.

**What supersedes is the FACTS, not the source.** A version change makes the previously-extracted entities out-of-date; it does not create a Source-to-Source link. The successor facts attach to the standing `:Source` (`extractedFrom` is keyed by `url`, which a version change does not move), and the earlier facts are replaced through the ordinary `CORRECTS` mechanism (§4.7 ‡). **The source is the identity; the entities are the belief.**

**Extraction is version-scoped — the link carries the version read.** Every **Point** derived from a source records **`sourceVersion`** — the `contentHash` of the version it was read from — on its `extractedFrom` link (declared `Point → Source`, §3.3). Currency is then a comparison of that recorded value against the source's current `contentHash`: **a read, never a stored flag** (§4.4), and unanswerable without the version on the link. Derived Events and Objects reach their source through their own links rather than `extractedFrom`, so **the version anchor is a Point-level guarantee** — a class whose provenance does not pass through an `extractedFrom` link is not version-scoped today.

**A Point with several sources is stale when ANY of its links is.** `extractedFrom` is many→many (§3.3), so a Point read from more than one source carries a `sourceVersion` **per link**; the Point is **stale if any** of those links is behind its source's current version, and **current only when every** link is. Partial re-extraction therefore surfaces as staleness rather than passing silently.

**Stale is a read, and `stale` is not `wrong`.** A **Point** is **stale** when its recorded `sourceVersion` differs from its source's current `contentHash` — a **derived predicate**, not a stored status, so nothing can drift. A stale entity **was true of the content that was read** and **remains standing**: supersession is **deferred until the replacement exists**, and when re-inference produces the successor the stale entity is superseded through `CORRECTS`. **Superseded, never deleted** — the previous belief stays queryable alongside the new one. This is the deliberate policy: **a stale belief is strictly better than no belief**, because immediate removal would leave the graph asserting nothing about a subject it previously had a position on.

**A version's window is opened and the prior one closed in the same act.** The version's valid-time start is the instant it was **observed** — `create_source` mints it once, on the same clock as `updatedAt`, and `_upsert_source` writes it only through the hash-diff gate, so a no-op re-check moves neither the node nor the journal. The `SourceVersioned` record carries **both** window facts: `validFrom` (the new version's open, the node's window start) and `previousValidTo` (the prior version's close). The two are the **same instant by construction** — one minted clock under two names, which is what *"closes the previous version's window and records the new one"* means — so the window chain is exactly contiguous rather than "contiguous modulo a fallback". The window is **half-open, `[validFrom, validTo)`**: `previousValidTo == validFrom` therefore places the shared instant in exactly one version — the new one — rather than in both. A `:Source` reader must **not** reuse the Point lane's closed predicate (`_covers`: `validFrom <= t <= validTo`), which would double-cover that instant; no `:Source` reader exists yet, so this is the contract the first one must implement. `previousValidTo` is a **record-only** key: there is one node per `url`, the node holds the current version, and its window is **open** (absent `validTo` is the open end), so the prior version's close can only live in the journal. An absent `validFrom` is an **open start** and is never synthesized at replay time — a legacy record (or a `_link_source` stub, which writes no window) replays to a source with no window, exactly as it was written. **The invariant holds on the `create_source`/`_upsert_source` path only.** Two other `:Source` writers move the version anchor (`contentHash`/`version`) without moving the window, so a source this path stamped can be left with a **present-but-stale** start: `_upsert_document` (the `DocumentCreated` fold, which bumps `contentHash` + `version` on the same hash-diff gate but writes no `validFrom`) and `_materialize_session_source` (the session-capture path, which rewrites `contentHash` without advancing `version` — its METADATA block does replay, through a trailing `SourceCreated` that `_upsert_source` folds as a STUB COMPLETION and that carries the node's `validFrom` verbatim, so a rebuild restores rather than clears the window; what it still does not do is MINT a window or advance `version`, and because the record declares itself a STUB COMPLETION (`_stub_completion`) the fold's hash-diff arm does not advance `version` on replay either — the live write never touches it, so neither does the replay). Extending the gate to them needs a minted instant that is **also replayable** — a clock/mint decision this change does not take — and the gap is pinned (never silenced) by `tests/test_source_version_window_3644.py::test_pinned_gap__*`.

**Closing the interval is part of the write.** An unclosed `validTo` reads as *"still true"* indefinitely — the failure mode the whole model exists to prevent — so the journal record that opens a new version closes the old one **in the same act**; it is never a later repair.

**`updatedAt` records the last version transition.** It is not a currency flag — currency is computed from `sourceVersion` against `contentHash`.

**The version record — both halves.** The *version-anchor* half: a hash transition journals a `SourceVersioned` record naming `previousContentHash` and the transition instant, a no-op re-fetch journals **nothing** (`STORAGE-ARCHITECTURE.md` §9.6's cost bound), and a `rebuild_all` replay reproduces the current node field for field. The *window* half: the same record now carries the version window — `validFrom` (the new version's open, written to the node through the same hash-diff gate) and `previousValidTo` (the prior version's close, a record-only fact) — so closing the prior window is a **recorded act**, and a rebuild reproduces the node's window; the prior window is recoverable from the journal alone. **What remains open** (and is deliberately not claimed): the node's `validTo`/`expiredAt`, which need a `:Source` **removal** path — the field's meaning is undecided; and a caller-asserted valid-time start (a known publication date), which needs the inversion guards the Point window needed. The per-field recorded/recomputable split lives in `docs/durability-posture.md` → *`:Source` — recorded vs recomputable, field by field* (R2).

### §4.7 Temporal Model (canonical)

**Every entity answers two orthogonal temporal questions, and supersession is a
third, separate fact. One canonical name per slot.** This section
is authoritative — never infer a temporal slot from a field's spelling.

**Axis 1 — Valid time: "when did this hold in the world?"** The fact's own
window, independent of when Tortoise learned it. Canonical pair:
`validFrom` / `validTo`.

| Slot | Canonical name | Standard | Notes |
|------|----------------|----------|-------|
| start | `validFrom` | `prov:generatedAtTime` | Populated by the date-carrying write paths — the hosted commit path sets it from the payload `when`, mining W-4 from the session frontmatter date (§4.1). An undated session stamps **no** `validFrom` at all (`ConversationMiner._temporal_wire`), so an **absent** `validFrom` means an **open/unbounded start** (`get_historical_knowledge`) — the write wall clock is never borrowed as a start. **A value that names no instant inherits the same reading** — *no start recorded*, not a lower bound, so the window is unbounded below (see the note under the coverage boundary below): the read path skips an unorderable lower bound, and the write boundary **warns** about such a value and stores it anyway, so a legacy point stays supersedable. `create_point`'s base CREATE map seeds no `validFrom`; caller props — including `validFrom` — are appended to it, so `create_point` itself never **synthesizes** a clock-stamped start either. The `validFrom` → `createdAt` chain is a **render fallback** (`_render_date`), never a create-time stamp |
| end | `validTo` | `prov:invalidatedAtTime` | On **supersession** set to the successor's `validFrom` **when the successor carries one** — the **contiguous Graphiti (Zep) window intent: the old fact stops being true when the new one starts being true** (`supersede_point`). An **undated** successor (absent `validFrom` ⇒ open start, row above) falls back to its `createdAt`, then to `now` — so the old `validTo` lands on the successor's `createdAt` and the windows **overlap** rather than being exactly contiguous. Exact contiguity requires a successor `validFrom` (`valid_from` kwarg → successor `validFrom` → successor `createdAt` → `now`). The kwarg is a **claim**, not an unconditional override: when the successor carries a stored `validFrom` the two must be **parseable** timestamps naming the **same instant** (compared by instant via `_created_sort_key`, the measure `_covers` uses), else `supersede_point` raises `ValueError` **before any mutation** — a disagreeing kwarg would otherwise gap or overlap the chain. The kwarg is the sole source for an undated successor **unless it names no instant** — a STRING kwarg that is unparseable falls through to the successor's `createdAt`, and that fallback itself requires an orderable value and otherwise lands on `now`. **The resolved end is additionally checked against the PREDECESSOR's own `validFrom`**: when BOTH order to an instant and the end is strictly earlier ⇒ `ValueError` before any mutation, because an inverted window (`validTo < validFrom`) is satisfied by **no** query instant, so the predecessor would be silently unreachable from every read surface — equality (a zero-length predecessor window) remains legal. An **unorderable** bound on either side is NOT compared even so: it names no instant, so a refusal would rest on a lexicographic accident rather than a comparison — the orderability residual is closed on the write path, except the deliberately-exempt NUMERIC `valid_from` kwarg (whose `str()` form `_covers` still cannot order). `invalidate_point` instead stamps `validTo = now` (no successor ⇒ no contiguity), and refuses with `ValueError` when a stored `validFrom` is after `now` — `retract_point` is the window-agnostic route |

> **The coverage boundary is stated here on purpose.** The read
> path that answers *"what was true at `t`"* (`get_historical_knowledge`'s
> `_covers`, `tortoise/sdk.py`) treats a window `[vF, vT]` as covering `t` iff
> `vF <= t <= vT`, an absent bound read as open — the rule
> `commit_schema.validate_validity_window` declares, whose agreement test
> pins `t == vT` **and** a zero-length `[t, t]` as covering. Two consequences, both
> stated so they are not rediscovered as defects:
>
> - **A well-formed contiguous chain has ONE shared instant.** For
>   `P[vP, vC] ← C[vC, ∞)` — the shape every dated `supersede_point` writes —
>   `t == vC` is covered by BOTH windows, so `get_historical_knowledge` reports
>   `status="ambiguous"` with two `candidates` there. That is the closed-interval
>   consequence, not corruption and not a silent wrong answer. The instant is also
>   **entry-point-dependent**: entering from `C` reports the two candidates, while
>   entering from `P` — whose CORRECTS walk is backwards-only — resolves to `P`
>   alone. A chain-local tie-break ("prefer the successor") is therefore **not** a
>   route: it makes the answer depend on the id you enter from, the inverted-window defect
>   class.
> - **The `validTo` row's prose describes a DIFFERENT (half-open) reading.**
>   *"The old fact stops being true when the new one starts being true"* is
>   `[vF, vT)`; the code is closed `[vF, vT]`. Which is canonical is an **open
>   decision** — flip to half-open (a global boundary-semantics change, which also
>   makes a zero-length window genuinely empty; evaluated as part of that decision),
>   accept the closed ambiguity, or a third shape. Until it
>   is decided, a shared boundary instant is a known property of the closed
>   reading, not a defect to be silently patched reader-by-reader. The flip is
>   **not a predicate-only change**, and the reason is measured rather
>   than argued. `commit_schema.validate_validity_window` permits
>   `validTo == validFrom`, so a zero-length `[t, t]` window is already
>   persistable and this closed `_covers` covers exactly one instant: at `t` the
>   read answers `status="found"` with the point as `valid_point` (measured
>   against `get_historical_knowledge`'s `_covers`, which rejects only `vT < t`,
>   and only once `vT` names an instant (a bound that names none is not
>   a bound, so it can reject nothing).
>   Under `[vF, vT)` that window covers **no** instant and the
>   same read answers `status="absent"` — the shape of honest absence — while
>   the `malformed` flag stays **silent**, because that detection is strictly
>   inverted (`validTo < validFrom`, `_inverted` returning `kt < kf`, so an
>   equal pair is not inverted) and a zero-length window is not inverted. Both
>   predicates are cited by **symbol**: the line numbers they carried rotted twice
>   (a rename, then a merge into the same file, +291 lines), and a
>   reader cannot tell a rotted line from a current one. So a half-open `_covers`
>   needs the well-formed-**empty** signal
>   that decision names *together with* it, as a reply state of its own; the predicate alone
>   would turn every already-persisted single-instant belief into a claim that was
>   never true at any instant, with no way for a reader to tell.
>
> **A start that names no instant means no start was recorded.** A stored
> `validFrom` that `_created_sort_key` cannot order — an empty value, or text
> that is not an instant — is not a lower bound. The window runs from the
> beginning of time, which is the same answer an absent property gives, and the
> write clock is **never** borrowed as a start. The read path therefore skips the
> lower bound rather than ordering the value: binding it would put such a window
> after every parseable instant, so it would cover **nothing** and a dated query
> would report honest absence for a claim genuinely in force. The write boundary
> **warns** when a caller supplies such a value and stores it anyway — a warning,
> never a refusal, because a legacy point must stay supersedable until its window
> is repaired. Rationale and the ruling behind it: `ONTOLOGY-CHANGELOG.md`.
> Pinned by `tests/test_validity_windows.py`
> (`test_a_start_naming_no_instant_leaves_the_window_open`,
> `test_restore_read_path_treats_falsey_but_present_valid_from_as_present` part b).
> The END agrees on the **gate**, and its **state is settled** (owner
> ruling, option B): an unorderable `validTo` bounds nothing —
> `_covers` skips the arm, because ordering a value that names no instant answers
> from a lexicographic comparison rather than from the data, so a probe that
> itself names no instant is covered too — **and it
> reports `end_state: "unknown"`**. An end that is stored but names no instant is
> an **un-datable END**: the fact ended, at a time we cannot pin — the same state
> the DECLARED marker (`validToPrecision = "unknown"` with no `validTo`) produces,
> firing the same reply-level `end_state_unknown` / `indeterminate_end_ids` flags,
> so a caller learns the window cannot be excluded rather than that it is still
> true. `END_KNOWN` therefore means **dated**, not **present**, gated by
> `validity.end_state` on the bound's own orderability so that a bound and the
> state reported for it cannot contradict each other; the reading depends on the
> bound, never on how a row spells it. **`END_OPEN` stays reserved for an ABSENT
> end**: an end column carrying a value is an **asserted** end, so "no end was
> recorded" is a claim the record contradicts, and reading it as still-true is the
> fabricated-coverage hazard this vocabulary exists to prevent.
> Rationale and the ruling behind it: `ONTOLOGY-CHANGELOG.md`. Pinned by
> `tests/test_validity_windows.py`
> (`test_an_end_naming_no_instant_bounds_no_instant_even_for_an_unparseable_probe`).
>
**Per-end precision and the 3-state end.** The window pair
above carries an **additive** precision layer: `validFromPrecision` /
`validToPrecision` ∈ {`year`, `month`, `day`}, plus the **end-only** marker
`validToPrecision = 'unknown'`. The vocabulary and the ONE validator live in
`tortoise/validity.py`. Precision is metadata about **how precisely the bound is
known** — the stored bound is unchanged, so `_created_sort_key` orders exactly
what it always did — and a legacy bare-ISO bound with no precision property
reads as `day` with **no migration**. The END is **three-valued**: `open`
(`validTo` absent, no precision — still true), `known` (`validTo` present and
**dated**), and `unknown` (the declared `validToPrecision = 'unknown'` with **no**
`validTo`, or a stored `validTo` that names no instant (§4.7); the window
ended but at a time we cannot pin, so **no date is fabricated**). Every
window-consuming read path emits the same `valid_from_precision` /
`valid_to_precision` / `end_state` block through `validity.window_read_fields`,
and a covering candidate whose end is `unknown` also sets `end_state_unknown` on
the reply — so an un-datable end is never read as still-true. `unknown` is
END-only: a window with no datable start is not a window. **A declaration
annotates a bound; it cannot summon one** — so both the declared token
and the legacy `day` default sit behind the same orderability gate on a PRESENT
bound, and `supersede_point` inherits the successor's declared START precision
onto the predecessor's END **only when that end came from the successor's own
`validFrom`** (or a `valid_from` kwarg naming the same instant — the write guard
forces equality with the stored start): when the resolution ladder falls through
to `createdAt`/`now` (the start names no instant), the end is stamped with
**no** precision property.

> **Point's `when` is not a second valid-time slot.** `when` (§4.1) is the
> **occurrence-date input** — the payload-level anchor the hosted commit path
> copies verbatim into `validFrom` on the same node
> (`point_props["validFrom"] = pr.point.when`, in `tortoise/hosted_api.py`).
> It is the same value under the §4.1 spelling, not a second slot — `validFrom` and the payload's occurrence date are **one slot under two spellings**, which is why they grouped together in the field map.

**Axis 2 — Transaction time: "when did our record of it exist?"** The
graph-write clock. Canonical pair: `createdAt` / `expiredAt`.

| Slot | Canonical name | Standard | Notes |
|------|----------------|----------|-------|
| start | `createdAt` | graph-write clock (not a PROV time property) | `ingestedAt` is **Source's** spelling of this slot; `capturedAt` is **Event's** — both are this slot, never a second axis |
| end | `expiredAt` | graph-write clock (not a PROV time property) | When our **record** stopped being current (termination), not *why* it did |

**Event's named alias (deliberate — do NOT rename).** Event spells the
valid-time pair `startedAt` / `endedAt`: for an occurrence, "started/ended" is
the natural reading. It is **the same concept as `validFrom`/`validTo`** — an
explicit alias, mapped to OWL-Time and `prov:startedAtTime` (§12) — not a third
axis. Event's transaction-time start is `capturedAt` (§4.5); Event declares no
transaction-time end.

**Supersession — a third, separate fact.** "When was it replaced?" (implies a
successor) is **not** the expiry axis. `supersededAt` (Object) is its own
concept and **coexists with `expiredAt`** — an Object can be superseded at T1
and expire at T2, so both apply and neither implies the other. Points record
the same fact differently: `status='superseded'` (set by `supersede_point`,
never by `invalidate_point`), **not** `supersededAt`. The `outdated` flag and
the `CORRECTS` edge are shared with `invalidate_point` — they do **not**
identify a supersession on their own, and a reader following them alone
classifies every invalidated Point as superseded (§4.1, §3.1, §5).

**Per-type presence (canonical matrix)** — ✅ implemented · ⚠️ partial · ❌
declared, not built (implementation is tracked separately):

| Slot | Point | Subject | Object | Event | Source |
|------|-------|---------|--------|-------|--------|
| valid start | `validFrom` ⚠️ | `validFrom` ❌ | `validFrom` ❌ | `startedAt` ✅ (alias) | `validFrom` ⚠️ † |
| valid end | `validTo` ✅ | `validTo` ❌ | `validTo` ❌ | `endedAt` ✅ (alias) | `validTo` ⚠️ † |
| txn start | `createdAt` ✅ | `createdAt` ✅ | `createdAt` ✅ | `capturedAt` ⚠️ | `ingestedAt` ✅ |
| txn end | `expiredAt` ✅ | `expiredAt` ❌ | `expiredAt` ❌ | — | `expiredAt` ❌ † |
| supersession | `status='superseded'` ✅ ‡ | — | `supersededAt` ✅ | — | — |

> † **A source's temporal slots are the Source column's.** No `:Object`-labelled write path reaches a Source, so the Object-labelled supersession fold (`_fold_object_superseded` / `apply_supersessions`, which `MATCH`es `(o:Object {id|name})`) **cannot stamp a Source** — **Source supersession is unreachable, not merely unimplemented (`—`).** A content-changing re-fetch is no longer a silent in-place mutation: it journals a `SourceVersioned` record naming the superseded `contentHash`, and that record also carries the **version window** (`validFrom` + `previousValidTo`, the same minted instant) while the node's `validFrom` is written through the same hash-diff gate — so a replay reproduces the node's window and the prior window is recoverable from the journal alone. Both Source window rows are **⚠️, not ✅, for two different reasons**: the valid **start** is written on the `create_source`/`_upsert_source` path only (a `_link_source` stub mints none, and absent ⇒ open/unbounded start; `_upsert_document` and `_materialize_session_source` move the version anchor **without** the window, so a stamped source can carry a present-but-stale start (the capture writer's metadata block replays, but the window half is unchanged) — pinned by `test_pinned_gap__*`), and the valid **end** is a **record-only** fact — the node holds the CURRENT version, whose window is open (absent = open end), so the prior version's `validTo` lives on the record as `previousValidTo` and a node `validTo` would need a `:Source` **removal** path that does not exist yet (which also owns `expiredAt`).
>
> **Point valid start is ⚠️, not ✅** — populated by the date-carrying write
> paths only; an undated session (mining W-4) stamps no `validFrom` at all,
> so an absent `validFrom` is an **open start** and the write wall
> clock is never borrowed as a start. `validFrom` → `createdAt`
> is a *render* fallback (`_render_date`), not a stamp. The open start is why the
> supersession **end** is conditional too: an undated successor contributes its
> `createdAt` (fallback chain above), not a `validFrom`, so its overlap with the
> old window is the open start's consequence — never read contiguity as
> unconditional. This matches the ⚠️ convention `capturedAt` follows; §4.1 marks
> `validFrom` and `validTo` separately.
>
> ‡ **Point supersession discriminator.** `supersede_point` and
> `invalidate_point` write the **same** four markers — `outdated=true`,
> `validTo`, `expiredAt`, and a `CORRECTS` edge. Only `status='superseded'`
> separates them (`supersede_point` sets it; `invalidate_point` never does) —
> §5's status vocabulary and §3.1 encode the same split. The `outdated` +
> `CORRECTS` pair alone classifies every invalidated Point as superseded.

**Cross-Entity Field Map** (non-temporal fields; the matrix above is
authoritative for the temporal slots. Event's transaction-time start is
`capturedAt`; `startedAt` is its occurrence-time start.)

| Field | Point | Subject | Object | Event | Source |
|-------|-------|---------|--------|-------|--------|
| `id` | ✅ | ✅ | ✅ | ✅ eventId | ✅ url |
| kind tag | pointKind | subjectKind | objectKind | eventKind | sourceKind **(+ `documentKind` — the GENRE axis, D10/Q2)** |
| name/title | — | name | name | — | title |
| updatedAt | ✅ | ❌ | ❌ | — | ✅ |
| status | status | ⚠️ SDK extra-prop (projection-owned planned) | ✅ (fold cache — event-derived) | — | — |
| responsibility | authoredBy | — | edge (§3.5) | — | — |
| ownership | — | — | edge (§3.5) | — | — |
| management | — | — | edge (§3.5) | — | — |
| format | — | — | — | format | **✅ `format` belongs here** — moved into `_SOURCE_HANDLED` by D10 (the model version is v3.15, §4.4; moved with it) |
| aboutEdges | ✅ | — | ✅ | ✅ | — |
| occurrence date | `when` (→ `validFrom`, §4.7) | — | — | — | — |
| is_episodic | ❌ | — | — | ❌ | ❌ |
| passes_frequency_gate | — | — | ❌ | — | — |

**A value carries no temporal slot of its own.** A stored number's instant is the carrier's valid-time start — `when`/`validFrom` on a `Point`, `startedAt` on an `Event` — never a field of the value. Where a value names no instant, the carrier's absent-start rule applies (§4.7 above) and the write path reports it rather than inventing one; `asOf` was retired for exactly this.

---

### §4.8 The `:Session` capture container (canonical)

The **`:Session`** node is the capture container: the durable record of one
captured session, holding the capture-attempt outcome and the entity-link
counters, and connecting its turn `Point`s by `CONTAINS`. Unlike the entity
types above, it is **written by many components, each owning a different
property subset** — the two capture lanes, the derived-commit pipeline, the
index-run re-link, the demo seeder, the longmem-eval ingestors, and the journal
folds that re-create the node on replay.

**The machine declaration is `tortoise/session_schema.py`** (`SESSION_PROPERTIES`
/ `SESSION_WRITERS` / `SESSION_PROPERTY_OWNERS`). It is authoritative for the
property set and the owner of each property; this section is its doc mirror,
and `tools/session_retrofit_audit.py inventory` re-runs the writer predicate
over the **tracked tree** (`git ls-files`) — minus the path prefixes and
third-party/cache directories `PREDICATE` declares out of scope — and fails on
drift in either direction (a new writer, or a declared one the predicate no longer finds). The
scan scope is part of that declared predicate, not a private allow-list inside
the tool. The per-property owners below **mirror that
declaration** and are pinned against it by `tests/test_session_schema_3554.py`
(an owner cell that disagrees with `SESSION_PROPERTY_OWNERS` fails the test); a
property with no owning writer is an import-time assertion failure, never a
silently-empty cell.

| Property | Owning writers (`<module>::<function/constant>`) |
|----------|--------------------------------------------------|
| `id` | `_rebuild_all_replay` |
| `created_at` | `_write_session_and_turns`, `_execute_commit_writes`, `_commit_region`, `_seed_demo_graph`, `merge_capture_session`, `ingest_haystack`, `_write_v2_phase_a`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `turn_count` | `_write_session_and_turns`, `_seed_demo_graph`, `merge_capture_session`, `ingest_haystack`, `_write_v2_phase_a`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `turn_max_index` | `_write_session_and_turns`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `is_episodic` | `_write_session_and_turns`, `_link_session`, `_fold_session_recorded`, `_execute_commit_writes`, `_commit_region`, `BACKFILL_STATEMENTS`, `merge_capture_session`, `ingest_haystack`, `_write_v2_phase_a`, `_rebuild_all_replay` |
| `harness` | `_write_session_and_turns`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `capture_lane` | `_write_session_and_turns`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `actor_user_id` | `_write_session_and_turns`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `machine_id` | `_write_session_and_turns`, `_rebuild_all_replay` |
| `model` | `_write_session_and_turns`, `_rebuild_all_replay` |
| `client_captured_at` | `_write_session_and_turns`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `client_captured_at_source` | `_write_session_and_turns`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `source` | `_write_session_and_turns`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `capture_redactions` | `_TURN_WRITE_CYPHER`, `_SESSION_REDACTIONS_CYPHER`, `_fold_session_recorded`, `merge_capture_session`, `_rebuild_all_replay` |
| `capture_ok` | `_capture_abandoned_marker`, `_report_capture`, `_rebuild_all_replay`, `_fold_session_recorded`, `_stamp_capture_attempt_state` |
| `capture_extractor` | `_capture_abandoned_marker`, `_report_capture`, `_rebuild_all_replay`, `_fold_session_recorded`, `_stamp_capture_attempt_state` |
| `capture_extraction_phase` | `_capture_abandoned_marker`, `_clear_capture_phase`, `_rebuild_all_replay`, `_report_capture`, `_write_capture_phase` |
| `capture_extraction_phase_at` | `_capture_abandoned_marker`, `_clear_capture_phase`, `_rebuild_all_replay`, `_report_capture`, `_write_capture_phase` |
| `entity_links_attempted` | `_link_off_loop`, `_stamp_entity_link_counters`, `_relink_sessions_after_index`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `entity_links_created` | `_link_off_loop`, `_stamp_entity_link_counters`, `_relink_sessions_after_index`, `_fold_session_recorded`, `_rebuild_all_replay` |
| `value_nodes_created` | `_execute_commit_writes`, `_rebuild_all_replay` |
| `value_nodes_held` | `_commit_region`, `_rebuild_all_replay` |
| `draft_count` | `_execute_commit_writes`, `_rebuild_all_replay` |
| `commit_count` | `_execute_commit_writes`, `_rebuild_all_replay` |
| `updated_at` | `_execute_commit_writes`, `_commit_region`, `_rebuild_all_replay` |
| `lme_question_id` | `ingest_haystack`, `_write_v2_phase_a`, `_rebuild_all_replay` |
| `lme_session_index` | `ingest_haystack`, `_write_v2_phase_a`, `_rebuild_all_replay` |
| `lme_source_session_id` | `ingest_haystack`, `_write_v2_phase_a`, `_rebuild_all_replay` |

**Who is an owner in this table.** A writer owns a property when it carries a
`SET <alias>.<prop>` clause for it. A property used only as a `MERGE` key is **not** attributed
— so the `id` row names `rebuild_all` (whose `SET s += $props` restores the whole
schema) and not the ten writers whose `MERGE (s:Session {id: …})` mints the key.
`id` is the Session's identity key, minted by every writer above; the cell records
who *sets* it.

**The one write primitive.** Every capture lane funnels through
`tortoise/sdk.py::_write_session_and_turns` plus the one turn store
`_TURN_WRITE_CYPHER`; the hosted `POST /v1/sessions` handler holds no copy of the MERGE text and adds only
its own **outcome** counters — as does `TortoiseSDK._capture_session` (the private
door in place of the retired public name; see the canonical
*Removed from the surface* record in
`docs/product/canonical-sdk-methods.md`). `rebuild_all` is the one writer whose property set IS
the whole schema (`SET s += $props` over the pre-wipe snapshot), declared as
such rather than enumerated.

#### D9 retrofit — the recorded honest gaps

The retrofit audit (`tortoise/session_schema.py::audit_session_retrofit`,
reached via `tools/session_retrofit_audit.py retrofit`) quantifies three
divergences on existing graphs. In all three the **decision is to record the
gap, not to rewrite the stored record** — the reasons live in
`RETROFIT_VERDICTS`, and are summarised here so the decision has a canonical
doc home:

- **`turn_count` shortfall under `capture_ok = true`** — `capture_ok` is the
  **extraction-attempt** outcome, never a store-integrity flag. Rewriting it to
  `false` would re-label a store loss as an extraction failure and arm the
  TRUE-retry gate, so the next same-session POST re-runs extraction and can mint
  duplicate claims. The shortfall is derivable from graph truth (stored
  `turn_count` vs the Session's live episodic turn members), which is what the
  audit reads. **Recorded gap.**
- **`capture_ok` pre/post-verification-read ordering** — the hosted lane
  computes `capture_ok` AFTER the post-write verification read (and downgrades
  on `skipped`); the SDK lane computes it BEFORE. No writer stamps which lane
  computed it (`harness` is written by both, `capture_extractor` names the
  extractor, and `machine_id`/`model` are hosted-claimed, not lane markers), so
  per-record retro-attribution is **not possible**. The class is bounded rather
  than invented: both lanes compute the same `ok` and the hosted lane can only
  be equal or more pessimistic, so only `capture_ok = true` records could
  differ from a re-computation. **Recorded gap.**
- **`entity_links_created` under-count** — the counter is non-monotone by
  construction: the capture-time pass writes this capture's numbers and the
  index-completion re-link (`_relink_sessions_after_index`) overwrites them with
  that run's, so a session can hold more `aboutObject` edges than the counter
  records. The counter is per-run **evidence**; rewriting it to the live edge
  count would destroy what the runs reported. The audit compares the recorded
  counter against the live edge count and reports the per-session under-count.
  **Recorded gap.**
- **`turn_count` with no turn member at all — the store, not a shortfall**
  — the capture writer stores a turn's text and
  vector in the **record** and writes **no turn node** whenever the record can
  hold them, so on an armed deployment `turn_count` is truth about Postgres and
  the graph has no episodic turn member to compare it against. A session with
  **zero** turn nodes is therefore **ambiguous graph-side** — a correctly-stored
  armed capture, or a genuine loss — and no query the graph can answer
  separates them. Such sessions are reported in their own `turns_record_held`
  bucket with `retro_quantifiable: False` (ids included, so the record can be
  asked) rather than as a shortfall: the shortfall class is what a
  retrofit/backfill acts on, and counting every armed capture into it would
  read as a mandate to write the transcript back into the graph — the placement
  §9.1/D1 of `STORAGE-ARCHITECTURE.md` forbids. Only the **partial** shortfall
  (`0 < stored turns < turn_count`, which the graph does witness) stays in the
  short class. **Not graph-quantifiable.**

Each verdict's `where_recorded` field in `RETROFIT_VERDICTS` points at this
section and at the retrofit amendment in
`docs/plans/2026-08-25-1714-memory-capture-onboarding.md`.

---

```

> **Approve:** ☐ text as written · ☐ with changes (list them) · ☐ reject (§4.5–§4.8 keep their numbers)
>
> **Approve the wording changes separately:** ☐ W-21 ☐ W-22 ☐ W-23 ☐ W-24 ☐ W-25 ☐ W-26 · ☐ reject all

### 4.8 Anything not classifiable

**Nothing in §4.5–§4.8 is unclassifiable.** Ref coverage is exhaustive:

```bash
python3 - <<'PY'
import re
src='\n'.join(open('docs/ONTOLOGY.md').read().split('\n')[517:973])
ids=sorted(set(re.findall(r'#(\d{3,5})(?![0-9])', src)), key=int)
print(len(ids), ids)
PY
```

That prints **51 distinct issues over 105 occurrences**. Every one appears in 4.2–4.5 and in the
changelog's Batch 4 rows.

⚠️ **Two traps in this batch, both handled rather than hidden:**

1. **`§4.6` cites `#909 §4.3 #6` and similar** — the trailing `#6`/`#10`/`#11`/`#12`/`#13` are
   *section numbers inside epic 909*, not separate issues. They are removed **with** their parent
   reference and are **not** counted in the 105 (which is the `#NNNN` count, and matches the brief's
   measurement exactly).
2. **`§4.7`'s END paragraph cites symbols, not line numbers**, and says why: *"the line numbers they
   carried rotted twice … a reader cannot tell a rotted line from a current one."* Removing the two
   numbers there (`#8337`, `#8487`) **keeps** that reasoning, which is the only place the document
   justifies its own citation style.

---

## Batch 5 — §5 Core Kind Vocabulary

### 5.1 Per-section table

| Section | lines | Refs before | Refs removed | Classes | D1 before → after | Rules moved | Unclassifiable |
|---|---|---|---|---|---|---|---|
| §5 (all sub-vocabularies) | 974–1193 | 40 | **40** | B 38 · C 2 | 23 → 23 | 0 | none |

**§5 is where the document's declared vocabularies live** — 40 references across 220 lines — and the
density is low because the section is mostly short definitional paragraphs: **eight
```` ```text ```` blocks**, all eight
preserved byte-for-byte except for **five comment-only lines across two of them** — `5-n`, `5-o`,
`5-p` in the Event-kind block, and `5-s`'s two lines in the Response-Contract block. D1 is unchanged
at 23, which counts those eight blocks.

**The five edits inside declared-vocabulary blocks are called out separately** because a change
inside a ```` ```text ```` block is a change to a *vocabulary*, not to prose: each removes only a
trailing `# #NNNN` comment and leaves every declared term exactly as it was.

### 5.2 Dispositions (40)

| # | Ref(s) | Line | Class | What happened to the semantic content |
|---|---|---|---|---|
| 5-a | `#1013` | 990 | **B** | *"> removed (issue #1013 — episodic records are Event nodes with eventKind `occurrence`/`turn`)"* → *"> removed (episodic records are Event nodes …)"*. The rule (why `event` is not a Point kind) stays. |
| 5-b | `#7813`, `#7853` | 995 | **B** | *"**The point↔object and point↔event kind overlap is CORRECT, not compatibility residue (#7813 owner ruling, implemented by #7853).**"* → *"…residue (owner ruling).**"*. **The rule — the overlap is the model, do NOT "clean up" it — is untouched**, and so is the four-label propagation statement. |
| 5-c | `#7813` | 1004 | **B** | *"The departure from the pre-#7813 'write-only' rule is marked at §8 (`OVERRIDES`)."* → *"The departure from the earlier 'write-only' rule …"* (W-30). The §8 `OVERRIDES` pointer stays. |
| 5-d | `#7902` | 1006 | **C** | *"the `:Subject`/`:Object` operator endpoints are live (`#7902`); the write path admits all four (§3.1)."* → *"the write path admits all four (§3.1)."* Same treatment as §2's and §3.1's identical sentence. |
| 5-e | `#4369` | 1008 | **B** | *"**Sanctioned gloss — "claim" (#4369).**"* → *"**Sanctioned gloss — "claim".**"*. The gloss rule, the "not a distinct kind" rule and the "never a canonical node write value" rule all stay. |
| 5-f | `#2727` | 1030 | **B** | *"**Legacy extraction path (pinned, #2727):**"* → *"**Legacy extraction path (pinned):**"*. The 12-kind subset rule and `test_legacy_extractor_vocab_is_a_documented_subset` stay. |
| 5-g | `#7856` ×2 | 1053, 1071 | **B** | *"— achievement is a SEPARATE axis (#7856)."* → *"— … axis."*; *"**born live** (issue #7856) —"* → *"**born live** —"*. **The two-axis rule and the born-live rule stay.** |
| 5-h | `#11824`, `#6792` | 1061, 1062 | **B** | *"**Status note (2026-11-17, owner-approved — #11824):**"* → *"**(2026-11-17, owner-approved):**"*; *"The owner's #6792 form ruling is **not** withdrawn"* → *"The owner's form ruling is **not** withdrawn"*. ⚠️ **The ruling's content is preserved in full** — `guideline` removed from `goalState`, reassigned to priority, "not a description of the model" caveat kept. |
| 5-i | `#131` | 1069 | **B** | *"(wiring promotes only the SOURCE, #131)"* → *"(wiring promotes only the SOURCE)"*. The EP-inert consequence stays. |
| 5-n | `#531` | 1079 | **B** | **Inside the Event-kind ```` ```text ```` block**: the trailing comment `# #531` is removed from the `humanApproval,` line. **The declared term is unchanged.** |
| 5-o | `#2767` | 1080 | **B** | **Inside the same block**: `milestone,          # #2767: a milestone is REACHED at a moment — core event (a` → `milestone,          # a milestone is REACHED at a moment — core event (a`. The term and its comment stay; only the number goes. |
| 5-p | `#1013` | 1083 | **B** | **Inside the same block**: `# turn = capture turn records (replaces pointKind 'event', issue #1013)` → `# turn = capture turn records (replaces pointKind 'event')`. |
| 5-j | `#909` | 1092 | **B** | *"**#909 §4.3 #1:** `AgentSession` (EXACT code spelling …) is the canonical kind …"* → *"**Canonical session-capture kind:** `AgentSession` (…)"* (W-27). The `AgentSession`/`sessionCaptured` alias rule and "no migration" stay. |
| 5-k | `#2311` ×2 | 1109 | **B** | *"**Account layer vs in-graph Subjects (#2311):**"* → *"**(…):**"*; *"Subject kinds are not renamed by #2311."* → *"Subject kinds are not renamed."*. The account-layer distinction and the registry definitions pointer stay. |
| 5-l | `#398`, `#388` | 1126 | **B** | *"**v3.2 (#398):**"* → *"**v3.2:**"*; *"connector Source materialization (#388) therefore never alters EP inheritance"* → *"… materialization therefore never alters …"*. The registration rule, the "unknown kinds stay neutral" rule and the Beta-prior table stay. |
| 5-m | `#2726`, `#398` | 1130, 1135 | **B** | *"**(#2726) Meeting-capture kinds:**"* → *"**Meeting-capture kinds:**"*; *"deliberately deferred to #398's mechanism"* → *"deliberately deferred to the source-credibility mechanism"* (W-29 — keeps the referent by naming the mechanism instead of the issue). |
| 5-q | `#909`, `#398` | 1139 | **B** | *"**#909 §4.3 #6:** `sourceKind: agentSession` is a registered source-type VALUE …"* → *"**The session Source's kind:** …"* (W-28); *"keyed on **sourceKind** (#398):"* → *"keyed on **sourceKind**:"*. The inheritance-keying rule stays. |
| 5-r | `#2101`, `#2080` | 1141 | **B** | *"### Response-Contract Vocabulary (W4 why-layer, #2101 / epic #2080 DM-12)"* → *"(W4 why-layer, DM-12)"* (W-31). The vocabulary block below it is untouched. |
| 5-s | `#11702` ×2 | 1154, 1158 | **B** | Inside the response-contract ```` ```text ```` block: *"NO persisted belief (#11702): there is"* → *"… belief: there is"*; *"persisted belief (#11702 — never the 0.5 …)"* → *"… belief — never the 0.5 …"*. ⚠️ **The comment's rule survives verbatim** — `null` rather than a fabricated neutral 0.5. |
| 5-t | `#432`, `#690` | 1167 | **B** | *"### Point Status Vocabulary (canonical, #432/#690)"* → *"### Point Status Vocabulary (canonical)"*. |
| 5-u | `#1088` ×2 | 1171 | **B** | *"\| `draft` \| initial (opt-in, #1088) \|"* → *"\| … (opt-in) \|"*; *"never retro-promoted (#1088)."* → *"never retro-promoted."*. **The "an author-explicit draft is never retro-promoted" rule stays.** |
| 5-v | `#11824` | 1173 | **B** | *"`archived` is accepted on write as a synonym and stored as this word (#11824)"* → *"… as this word"*. |
| 5-w | `#12855`, `#11824`, `#2498` | 1176 | **B** / **C** | *"ended over stored rows (#12855)"* → no number. ⚠️ **The `C`-class clause is moved whole**: *"⚠️ The code is not yet aligned: `retract_point` **still writes `retracted` today** — a stated residual of #11824, not a description of the model."* — the source says itself that it is *"not a description of the model"*, so it goes to changelog `C-10`. *"a second, flag-based dead marker (#2498) that readers … must continue to honour"* → no number, **and the "must continue to honour" rule stays**. |
| 5-x | `#690`, `#1088` ×2, `#432` | 1190 | **B** | *"**Parity decision (#690, amended #1088):**"* → *"**Parity decision:**"*; *"deliberately NOT SDK parity since #1088;"* → *"deliberately NOT SDK parity;"*; *"RESERVED for the #432 change-log stream"* → *"RESERVED for the change-log stream"*. **Every parity rule stays** — no-status→live, explicit draft honoured and never auto-promoted, EventAPI's own posture, CLI backfill, and the `:GraphEvent` reservation. |

### 5.3 Wording changes (not citation deletions) — rejectable individually

| # | Where | Change | Why |
|---|---|---|---|
| **W-27** | §5 `AgentSession` | heading *"#909 §4.3 #1:"* → *"Canonical session-capture kind:"* | With the number gone the heading needed to say what it is about. |
| **W-28** | §5 `agentSession` Source | heading *"#909 §4.3 #6:"* → *"The session Source's kind:"* | Same. |
| **W-29** | §5 meeting kinds | *"deferred to #398's mechanism"* → *"deferred to the source-credibility mechanism"* | Names the mechanism so the sentence has a referent. |
| **W-30** | §5 point↔object overlap | *"the pre-#7813 'write-only' rule"* → *"the earlier 'write-only' rule"* | Same rule, without the version stamp. |
| **W-31** | §5 response-contract heading | *"(W4 why-layer, #2101 / epic #2080 DM-12)"* → *"(W4 why-layer, DM-12)"* | Two trackers removed; the work-stream label that names the spec stays. |

### 5.4 Proposed text — §5 (replaces `ONTOLOGY.md` lines 974–1193)

<!-- PROPOSED:§5 -->
````markdown
## §5. Core Kind Vocabulary

### Point Kind Vocabulary (core)

```text
statement    # the LOGIC layer — THE extraction write kind (state-centric, option B 2026-08-12)
decision, vision, strategy, plan, goal, target, observation, hypothesis, humanApproval, event   # LEGACY write kinds (write-compat only)
```

> **State-centric alignment (2026-08-12, option B):** Points are the LOGIC layer
> only, and the logic is one kind: **`statement`** — the asserted belief.
> `hypothesis` is FOLDED INTO CONFIDENCE semantics (a conjecture is a
> low-confidence statement); `observation` is removed (anything can be called
> one). `decision`/`humanApproval` are TIMELINE kinds → Event nodes (eventKind
> `decision`/`humanApproval`); `vision`/`strategy`/`plan`/`goal`/`target` are
> STATE kinds → Object kinds (commitment-state family, below); `event` is
> removed (episodic records are Event nodes with eventKind
> `occurrence`/`turn`). The legacy kinds remain valid write kinds for
> compatibility; extraction emits `statement` only.
>
> **The point↔object and point↔event kind overlap is CORRECT, not compatibility
> residue (owner ruling).** Four kinds sit in BOTH
> the point and object sets — `goal`, `plan`, `strategy`, `target` — and
> `decision` and `humanApproval` sit in both the point and event sets. Under
> the ruling this is the
> model: **confidence (EP) propagates across `:Point`, `:Subject`, `:Object` and
> `:Event`**, so a belief written as an Object or Event is genuinely epistemic
> and must propagate. Do NOT "clean up" the overlap as legacy — it is the
> layer split being unwound (objects ARE in the epistemic layer). The
> *operator* node itself stays `:Point {is_operator:true}`; only its children
> and targets may be any of the four labels. The departure from the earlier
> "write-only" rule is marked at §8 (`OVERRIDES`). The write path admits all
> four (§3.1).
>
> **Sanctioned gloss — "claim".** Where **"claim"** names a belief node, it is a
> **logic-layer Point** — the asserted belief (the logic layer's canonical kind is
> `pointKind: statement`; the legacy write kinds remain valid Point kinds for write-compat,
> above). "Claim" is the sanctioned plain-English gloss for that node, used where "Point"
> would read as internal jargon to a customer; per option A a belief-node use of the noun is
> correct in canonical prose too, and the machine vocabulary is unchanged
> (Point / `statement`).
> **It is not a distinct kind:** there is no `claim` type and no `claim` pointKind, and
> `claim` is never a **canonical** node write value — the SDK's kind vocabulary is
> deliberately open (an unrecognized kind is accepted with a warning), so this is a
> vocabulary rule, not an enforced write rejection. Word-carrying identifiers are untouched —
> the EP slot `claim_id` (§3) and the pack-manifest `storeAs: claim` stream bucket are not
> node kinds; the bucket is a manifest label.

### Object Kind Vocabulary (core)

```text
Project, WorkItem, Problem, tag, user, skill, tool, agent, workflow, agreement, standard, other,
strategy, plan, goal, target    # commitment-state family (state-centric, 2026-08-12) — states that
                                # commitments produce; carry lifecycle + derived confidence
```

> **Legacy extraction path (pinned):** the Phase-2 entity stage in
> `tortoise/extractor.py` (`_OBJECT_KIND_VOCAB`) intentionally supports a
> narrower 12-kind subset only — it omits the four commitment-state kinds
> (`strategy`/`plan`/`goal`/`target`), which are extraction surfaces of
> `extractor_v2.CORE_OBJECT_KEYS` (state-centric); `_intersect_object_kinds`
> drops them from the prompt vocabulary and `_normalize_object_kind` collapses
> any that still appear to `other`. The test
> `test_legacy_extractor_vocab_is_a_documented_subset` pins exactly that gap.
> **Problem family (2026-08-31):** `Problem` = a deviation between actual and desired
> state (anchored in a core `standard`, SLO/target, or declared need). The problem-family
> parent: packs subclass it (dev:`bug` — code deviates from expected behavior,
> dev:`incident` — a service deviates from its SLO/standard, product-strategy:
> `customerProblem` — an unmet customer need) and `risk` is its potential form — a dev
> `risk` POINT (claim) that a problem may occur; when the problem materializes, the
> claim point is superseded/deprecated and the materialized `bug`/`incident` Object
> carries the state (Point supersession is Point→Point; Object creation is separate).
> **State-centric alignment (2026-08-12):** `strategy`/`plan`/`goal`/`target`
> are STATE objects (superseded when a new commitment lands — the old strategy
> is deprecated, the new one promoted). Pack pointKinds used as options
> (product-strategy: useCase, userJourney, jobToBeDone, valueProposition) are
> OPTION/STATE kinds — pack-mapping item: promote to objectKinds (near-miss
> convention until the pack amendment).
>
> **Goal state (`goalState`) — achievement is a SEPARATE axis.** The
> commitment-state kinds (`goal`/`target`/`strategy`/`plan`) carry two
> orthogonal fields. `status` is the claim **lifecycle** (draft/live/…, §4.1) —
> whether the node is promoted for use. `goalState` is the goal's
> **achievement** state — a CLOSED vocabulary validated at the SDK write
> boundary (`GOAL_STATE_VALUES` in `sdk.py`): `met`, `active`, `blocked`, `unmet`,
> `superseded` and `abandoned`.
>
> **Status note (2026-11-17, owner-approved):** `guideline` was
> **removed** from `goalState`. The owner's form ruling is **not
> withdrawn**; it is reassigned — whether a goal is directional with no
> pass/fail is a question about **priority**, not achievement, and priority has
> no design yet. So `<10 min of CI` is still a legitimate commitment to make; it
> is simply no longer expressed as a `goalState` word. The two must not be conflated: a goal that reads
> `status='met'` is a lifecycle violation, and `goalState` is never
> `draft`/`live`. Because a **draft Point that is only ever a TARGET is
> EP-inert** (wiring promotes only the SOURCE) while `converged=True` is
> still reported, a `goal`-kind **Point** created without an explicit status is
> **born live** — a goal that can never influence a verdict must
> not be creatable by default. The canonical representation of a goal remains
> an **Object**; the born-live rule is the legacy-Point-leg safety net.

### Event Kind Vocabulary (core)

```text
meeting, decision, experiment, deployment, review, friction, extraction,
documentCreated, roleCreated, pointAdded, sessionCaptured, AgentSession, humanApproval,
milestone,          # a milestone is REACHED at a moment — core event (a
                    # `pm:milestone` objectKind declaration was the deformation)
occurrence, turn    # state-centric (2026-08-12): occurrence = generic extracted occurrence;
                    # turn = capture turn records (replaces pointKind 'event')
```

> **State-centric alignment (2026-08-12):** eventKind `decision` is the
> TIMELINE record of a commitment (the resolution is expressed as lifecycle
> writes on the state objects); `occurrence` covers extracted happenings;
> `turn` covers capture turn records. The Episodic layer is the truth for
> lifecycle: Object status is a fold cache over its event stream — never authoritative on its own (§2).
>
> **Canonical session-capture kind:** `AgentSession` (EXACT code spelling — capital A; sdk.py `ingest_corpus`/session_indexer.py) is the canonical kind for session-capture Events; `sessionCaptured` (the core kind written by the regex capture path) is an **alias of the same concept** — both remain valid kinds, **no migration**.

### Document Kind Vocabulary — the GENRE axis (core)

> This is a **`documentKind`** vocabulary — the **GENRE** of a document, applied to a `:Source` whose `sourceKind` is `document`. It is **not** an Object subclass vocabulary, and it is **not** the same axis as `sourceKind`.

```text
research, reflectPostmortem, strategyDoc, visionDoc, planDoc, decisionDoc,
meetingNotes, experimentResults, evidenceLog, handoff, transcript, roadmap, brief
```

### Subject Kind Vocabulary (core)

```text
organization, team, role, legalPerson, naturalPerson, other
```

> **Account layer vs in-graph Subjects:** the `organization` / `team` kinds above (and their §6 subclasses) are **in-graph Subjects inside a memory** — semantically distinct from the control-plane account unit that owns the graph(s), the **organization account** (billing/tenure; legacy code/API/DB identifiers still read "team"). Subject kinds are not renamed. Definitions note: docs/registry-graph-schema.md ("Definitions — account layer vs in-graph Subjects").

### Source Type Vocabulary (`sourceKind`) + Credibility Tier

**The canonical `sourceKind` values** — what kind of source this is. The vocabulary is extensible: **core values are here**, pack kinds are declared in the pack manifests (§9) and registered at load time.

```text
conversation, document, agentSession,
github_issue, github_pr, slack_message, linear_card, linear_cycle
```

**Credibility tiers** — a *different* axis, carried on `credibilityTier`:

```text
T0 (meta-analysis), T1 (peer-reviewed), T2 (expert), T3 (anecdotal), T4 (unverified)
```

> **v3.2:** `sourceKind` is the extensible source TYPE vocabulary — pack-declared kinds (github_issue, github_pr, linear_card, linear_cycle, slack_message, document, meeting_transcript, meeting_minutes...) resolve to a tier ONLY via explicit registration (`register_source_kind_default`) or an explicit `credibilityTier` assignment; **unknown kinds stay neutral** (no inheritance). Connector kinds register explicitly neutral in `SOURCE_KIND_DEFAULTS` (`source_credibility.py`) — connector Source materialization therefore never alters EP inheritance. The T0–T4 tier semantics above live on `credibilityTier`. The Beta-prior mapping (T0=(10,1), T1=(5,1), T2=(3,1), T3=(2,1), T4=(1.1,1)) is the validated model (docs/ep-source-credibility-experiment.md §1.1).
>
> **Expansion-pack kinds live in the packs, not here.** Pack-declared kinds (dev:epic, product-strategy:product, etc.) are defined in their pack manifests (§9) and registered at load time via the pack registry. This file documents only the core vocabulary; it is not the home for pack kinds.
>
> **Meeting-capture kinds:** `meeting_transcript` (raw, first-hand) and
> `meeting_minutes` (structured, mediated) are registered source kinds as of
> v3.16, both **NEUTRAL** (`None`) — operational captures sit outside the
> research-evidence ladder, so they inherit no tier. The tier question (whether
> a raw transcript warrants a first-hand tier and minutes a mediated one) is
> deliberately deferred to the source-credibility mechanism; `register_source_kind_default`
> makes the eventual choice a one-line, reversible registration. Pack manifests
> may declare both in `extraction.sourceTypes` (the validator unions the registry).
>
> **The session Source's kind:** `sourceKind: agentSession` is a registered source-type VALUE (the four-node capture model's session Source — the provenance bridge — carries it; the value belongs to the `sourceKind` vocabulary above, alongside github_issue/slack_message/linear_card/…). Credibility-tier inheritance is keyed on **sourceKind**: the tier resolves via the kind's registered tier default (`register_source_kind_default`) or an explicit `credibilityTier` assignment; unregistered kinds stay neutral (no inheritance).

### Response-Contract Vocabulary (W4 why-layer, DM-12)

Additive response-contract labels — NOT new entity kinds. Registered so the
W4 why-block assembly and the S6 contract test share one vocabulary (vocabulary
drift is prevented by the S15 schema-correctness review).

```text
dig_deeper kinds   supports | nand | superseded | tradeoff     # dig_deeper[k].kind (deterministic labels, never LLM prose)
dig_deeper labels  read supports · read the counterargument (NAND)
                   · see what changed · weigh the alternatives  # derived from kind + target verb phrases (UXD 4)
why-block sections why · conflicts · supersession · tradeoffs · dig_deeper · warnings   # enriched-item additive keys (W4 why-layer spec §3.1.1/§6.1)
conflict severity  high | medium | null                         # deterministic from the counter-claim's persisted EP mean
                                                               # high ⟺ mean ≥ 0.6 (repo high-confidence bar); else medium
                                                               # null ⟺ the counter-claim has NO persisted belief: there is
                                                               # nothing to grade, so it sorts AFTER every graded severity instead of
                                                               # taking `medium` on a fabricated neutral 0.5
tradeoffs[].ep_weight  float | null                            # the alternative's persisted belief mean, or null when it has NO
                                                               # persisted belief — never the 0.5 the `coalesce(…, 1.0)`
                                                               # default produced). The ONE precedence every surface shares
                                                               # (`n.confidence` mirror first, then the persisted α/β mean), so this
                                                               # value always equals the `ep` block's `confidence_mean` for the same
                                                               # alternative. `null` sorts a tradeoff AFTER every weighed
                                                               # alternative, and `mitigation`/`operator_id` may be null beside it
degraded_reason    timeout | assembly_error | breaker_open      # degradations only; clean empty = null + empty arrays (W4 why-layer spec §3.1.3)
```

### Point Status Vocabulary (canonical)

| Status | Kind | Transitions to | Notes |
|--------|------|----------------|-------|
| `draft` | initial (opt-in) | `live`, `deprecated`, `superseded` | Inert for EP computation; promoted by `promote_point` / `update_point(status='live')`, and — for its OWN minted ids — by the capture ingest pass. An AUTHOR-explicit draft on a folded/pre-existing point is never retro-promoted. A no-status `create_point` is `live`, so draft must be asked for |
| `live` | active | `deprecated`, `superseded` | Full EP participation |
| `deprecated` | terminal | *(none)* | **Ended, and nothing replaced it.** `archived` is accepted on write as a synonym and stored as this word |
| `superseded` | terminal | *(none)* | **Ended because something replaced it** — the successor is named. Structural replacement via the CORRECTS edge + **restatement-scoped edge disposition** (semantic edges are triaged per edge, not bulk-transferred); `supersede_point` also sets the legacy `outdated` flag |

> **`retracted`, `outdated` and `archived` are read-side legacy**, not statuses: **in this model** no verb produces them, while every reader still treats them as ended over stored rows. The `outdated` boolean likewise remains a second, flag-based dead marker that readers — including EP's currency predicate — must continue to honour.
>
> **`challenged` is NOT a state** — it is a DERIVED condition emerging from the presence of a NAND operator edge on a live point, queryable as such:
>
> ```cypher
> MATCH (p:Point {status:'live'})<-[:NAND]-(:Point {is_operator:true}) RETURN p
> ```
>
> **Tombstone contract:** Retracted points stay in the graph (`get_point` returns them with `status='retracted'`). Default query surfaces (`query`, `paginated_query`) exclude them; pass `include_retracted=True` or an explicit `status='retracted'` filter to surface them. Deletion via `delete_point` hard-deletes (no tombstone).
>
> **A terminal claim's temporal record is immutable.** Once a claim reaches a terminal status, its valid-time window (`validFrom`, `validTo`, and their precision markers), its transaction-time expiry (`expiredAt`), and its occurrence anchor (`when`) no longer change. Content and metadata stay revisable: a revision is a newer assertion of the same identity, not a restatement of when the claim held.
>
> **Consequence:** a stored inverted window (`validTo` before `validFrom`) on a terminal claim cannot be corrected — it is reported, not repaired.
>
> **Parity decision:** SDK + EventAPI + CLI share a single status vocabulary (`POINT_STATUS_VALUES` in `sdk.py`). A no-status `create_point` is `live` and an explicit `draft` is honoured and never auto-promoted; EventAPI births `draft` as its own journal-composer posture — deliberately NOT SDK parity; CLI backfill promotes NULL-status legacy points to `live` (migration-only, not a drift). The `:GraphEvent` label is RESERVED for the change-log stream (`{seq, ts, type, payload, event_id}`, zero relationships — graph islands) — distinct from the `:Event` ontology entity with `eventId` (§3.4). See docs/event-catalog.md.

---

````

> **Approve:** ☐ text as written · ☐ with changes (list them) · ☐ reject (§5 keeps its numbers)
>
> **Approve the wording changes separately:** ☐ W-27 ☐ W-28 ☐ W-29 ☐ W-30 ☐ W-31 · ☐ reject all
>
> **Approve the five in-vocabulary edits separately:** ☐ 5-n (`#531`) ☐ 5-o (`#2767`) ☐ 5-p (`#1013`) ☐ 5-s line 1154 (`#11702`) ☐ 5-s line 1158 (`#11702`) · ☐ reject all

### 5.5 Anything not classifiable

**Nothing in §5 is unclassifiable.** Ref coverage is exhaustive:

```bash
python3 - <<'PY'
import re
src='\n'.join(open('docs/ONTOLOGY.md').read().split('\n')[973:1193])
ids=sorted(set(re.findall(r'#(\d{3,5})(?![0-9])', src)), key=int)
print(len(ids), ids)
PY
```

That prints **25 distinct issues over 40 occurrences**. Every one appears in 5.2 (and in the
changelog's Batch 5 rows).

⚠️ **One structural caution for whoever applies this batch.** Five of the edits — `5-n`, `5-o`,
`5-p` in the Event-kind block, and `5-s`'s two lines in the Response-Contract block — are **inside
```` ```text ```` vocabulary blocks**, and the block itself is wrapped in four
backticks in this proposal so the inner fences survive. Pasting the block without its outer fence is
fine; pasting it with a *three*-backtick outer fence truncates it at the first inner block. The
completion check for this section is therefore: eight ```` ```text ```` blocks still open and close,
and `diff` of each block against the source showing only the **five** comment-line changes (three in
the Event-kind block, two in the Response-Contract block).

---

## Batch 6 — §6–§12 (the tail: subclass model, equivalence, edge semantics, packs, recency, cascading invalidation, reputation, standards)

### 6.1 Per-section table

| Section | lines | Refs before | Refs removed | Classes | D1 before → after | Rules moved | Unclassifiable |
|---|---|---|---|---|---|---|---|
| §6 Subclass Model | 1194–1220 | 0 | 0 | — (no change) | 0 → 0 | 0 | none |
| §7 Equivalence Model | 1221–1233 | 0 | 0 | — (no change) | 0 → 0 | 0 | none |
| §8 Semantic-Epistemic Edge Model | 1234–1419 | 21 | **21** | B 20 · C 1 | 10 → 10 | 0 | none |
| §9 Expansion Pack Manifest | 1420–1468 | 3 | **3** | B 3 | 0 → 0 | 0 | none |
| §10 Recency Modulation | 1469–1482 | 1 | **1** | B 1 | 1 → 1 | 0 | none |
| §10.5 Cascading Invalidation | 1483–1538 | 10 | **10** | B 6 · C 4 | 5 → 5 | 0 | none |
| §11 Reputation | 1539–1558 | 1 | **1** | B 1 | 2 → 2 | 0 | none |
| §12 Relationship to Standards | 1559–1568 | 1 | **1** | B 1 | 0 → 0 | 0 | none |
| **Total** | — | **37** | **37** | B 32 · C 5 | 18 → 18 | **0** | none |

**§6 and §7 carry no references and are proposed unchanged.** So are §1, §3.7, §5's *Document Kind
Vocabulary* subsection and §8's *Pack Relation Declarations* subsection — **six** parts of the
document with nothing to remove.

**§10.5 is the one section where a whole paragraph moves**, because it is a **status paragraph**:
*"#2488 merge-blocker: … ships with #2488, so until it lands an invalidate→rebuild cycle resurrects
the frozen posterior"*. Its one rule — *rebuild-side fold decay is supersede-scoped* — is extracted
and kept; the shipping order, the gate and the blocker go to changelog `C-11`.

### 6.2 Dispositions — §8 (21)

| # | Ref(s) | Line | Class | What happened to the semantic content |
|---|---|---|---|---|
| 6-a | `#12067` ×4 | 1256, 1291, 1382, 1385 | **B** | *"Teaching the bare-edge leg to carry it is the open follow-up on #12067."* → *"… is an open follow-up."*; *"raising mid-bundle (#12067)"* → no number; *"carrying it on a bare edge is the #12067 follow-up"* → *"… an open follow-up"*; *"the same caveat … applies (#12067)"* → no number (W-32). ⚠️ **The "open-ness" survives at three of the four sites and does NOT at the fourth.** At `1256` (*"…is an open follow-up"*), `1382` (*"…is an open follow-up"*) and `1385` (*"the same caveat … applies"*) the statement that the capability is not yet available is explicit and stays. At **`1291`** the sentence described the ingest refusal path (*"instead of raising mid-bundle (#12067)"*), and removing the citation leaves *"instead of raising mid-bundle."* — a flat statement of present behaviour whose "follow-up" marker is gone. **That is a real, disclosed loss of nuance at one site**, flagged here so it can be restored by rejecting the `1291` half of this row. |
| 6-b | `#10306` | 1297 | **B** | *"and the projection's own edge fold (`#10306`)"* → *"and the projection's own edge fold."*. The exclusion (that the projection's fold is **not** covered by the closed-mechanism rule) stays. |
| 6-c | `#7813` ×3, `#7853` | 1303, 1308, 1309 | **B** | *"see §5 on the point↔object/point↔event overlap; #7813/#7853).**"* → *"… overlap).**"*; the `OVERRIDES` blockquote becomes *"the earlier \"Point→Event operators are write-only in v1, no EP propagation\" rule — confidence propagates across `:Point`/`:Subject`/`:Object`/`:Event`, so an Event target is epistemic and propagates."* (W-33). **The `OVERRIDES:` marker itself — the thing that makes the departure findable — stays.** |
| 6-d | `#5025` | 1320 | **B** | *"- **Neutral vs structural (#5025):**"* → *"- **Neutral vs structural:**"*. ⚠️ The whole neutrality argument, the `extractedFrom`-carries-weight contrast and the `#5566` closure below all stay. |
| 6-e | `#5566` | 1328 | **B** | *"closed by **#5566** (the affected-set traversal is now `IMPL`/`NAND`-filtered **and** directed …)"* → *"closed (the affected-set traversal is now …)"*. The rule — only operator **inputs** are admitted — stays verbatim. |
| 6-f | `#7902` | 1332 | **C** | *"(`:Point`/`:Subject`/`:Object`/`:Event` — #7902; no operator)"* → *"(`:Point`/`:Subject`/`:Object`/`:Event`; no operator)"*. ⚠️ Here the deletion is a **citation only** — the four labels are the rule and they remain. |
| 6-g | `#7852` | 1341 | **B** | *"… as `direction_basis` — `explicit` / `semantics` / `default` (#7852 W1)."* → *"… `default`."*. The three-value basis and the four-value direction vocabulary stay. |
| 6-h | `#753` | 1379 | **B** | *"the attacker's truth penalizes the target, with no back-pressure (#753)"* → no number. |
| 6-i | `#9963` | 1382 | **B** | *"the #9963 disclosure compares the write against THIS map"* → *"the map-consistency disclosure compares the write against THIS map"* (W-34). ⚠️ The warning and its *"correctly, about the map"* qualifier stay. |
| 6-j | `#11083` ×3, `#7848` ×2 | 1384, 1390 | **B** | *"Restored as a Layer-2 meaning by #11083 (owner 2026-11-05, cites `#7848`'s `OVERRIDES:`); the pre-#11083 mapping onto `causes` changed the meaning **and** inverted the direction."* → *"Restored as a Layer-2 meaning by owner ruling; mapping it onto `causes` changes the meaning **and** inverts the direction."* (W-35). **The `produces` ≠ `causes` distinction and the direction rationale stay**; the pointer to an `OVERRIDES:` **on #7848** is dropped rather than re-pointed at a §8 marker that says something else. At 1390, *"(#7848's correction of 2026-11-08 withdrew the earlier promotion)"* → *"(an earlier promotion was withdrawn)"*. |
| 6-k | `#2766` | 1390 | **B** | *"**A word that fails the layer test is a LABEL, not a meaning** — #2766's rule, which #11083 applies."* → *"**A word that fails the layer test is a LABEL, not a meaning.**"* (W-36). The rule and the `funds` worked example stay. |

### 6.3 Dispositions — §9, §10, §10.5, §11, §12 (16)

| # | Ref(s) | Line | Class | What happened |
|---|---|---|---|---|
| 6-l | `#909` | 1434 | **B** | **Inside the manifest ```` ```yaml ```` example**: `kindDefs:  # extractor prompt material (v3, epic #909)` → `(v3)`. The YAML key and its meaning are unchanged. |
| 6-m | `#7848` | 1461 | **B** | *"(`subclassOf: requirement: WorkItem`, #7848 owner ruling)"* → *"(…, owner ruling)"*. The ruling (requirement is a product-strategy Object kind, not a dev point kind) stays. |
| 6-n | `#1891` | 1465 | **B** | *"(ontology-only v1, epic #1891)"* → *"(ontology-only v1)"*. The allowlist/reject-on-upload rule stays. |
| 6-o | `#398` ×2 | 1473, 1551 | **B** | *"**Decision log (v3.2, #398 open question):**"* → *"**Decision log (v3.2):**"*; *"**Derived values may be CACHED, never authoritative (v3.2, #398):**"* → *"… (v3.2):**"*. Both rules — decay granularity deferred, cache never authoritative — stay. |
| 6-p | `#580` | 1500 | **B** | *"ep.contested in search, #580)"* → *"ep.contested in search)"* (inside the invalidation code-block). |
| 6-p2 | `#86` | 1533 | **B** | *"Direction-aware EP (§3.1, #86) is the prerequisite that makes reverse traversal well-defined"* → *"Direction-aware EP (§3.1) is the prerequisite…"*. ⚠️ **A two-digit reference, found only by the second pass** — the first sweep matched `#NNNN` and would have left it in place. The prerequisite statement stays. |
| 6-q | `#2490` ×2 | 1503, 1530 | **B** | *"**Terminal posterior vacuity decay (#2490):**"* → *"**Terminal posterior vacuity decay:**"*; *"+ rebuild fold (#2490)"* → *"+ rebuild fold"*. **The vacuity rule (0.5, posterior (1,1), atomically with the terminalizing write) stays in full.** |
| 6-r | `#2421` | 1507 | **B** | *"Decay is UNIFORM across #2421 Case-1 restatement and Case-2 correction"* → *"… across Case-1 restatement and Case-2 correction"*. Both cases stay. |
| 6-s | `#2488` ×3, `#2490` | 1517–1520 | **C** | ⚠️ **A status paragraph becomes one rule sentence** (W-37). Source: *"**#2488 merge-blocker:** the rebuild-side fold decay applies to the *supersede* fold only. The `PointInvalidated` rebuild fold (`_fold_point_invalidated`) ships with **#2488**, so until it lands an invalidate→rebuild cycle **resurrects the frozen posterior** (no fold re-applies decay). **#2490** is gated on #2488 and adds `decay_clause('n')` to `_fold_point_invalidated`'s SET when it lands."* → **"**Rebuild-side fold decay is supersede-scoped:** the rebuild-side fold decay applies to the *supersede* fold only."** The rule survives; the shipping order, the gate and the blocker move to `C-11`. |
| 6-t | `#12855` | 1529 | **B** | *"read-side legacy over stored rows (#12855)"* → no number. The status vocabulary and the "never auto-inferred" rule stay. |
| 6-u | `#398` | 1473 | — | (counted at `6-o`) |
| 6-v | `#5358` | 1567 | **B** | *"`retract_point` is the window-agnostic route (#5358) (§4.7)"* → *"… route (§4.7)"*. The bi-temporal row's whole rule set stays. |

### 6.4 Wording changes (not citation deletions) — rejectable individually

| # | Where | Change | Why |
|---|---|---|---|
| **W-32** | §8 `coherence` | three *"the #12067 follow-up"* phrasings → *"an open follow-up"* | The statement is that the capability is not available; the number only names where it is tracked. |
| **W-33** | §8 `OVERRIDES` | *"the pre-#7813 … rule — #7813 rules that confidence propagates"* → *"the earlier … rule — confidence propagates"* | Same override, no attribution. |
| **W-34** | §8 `hasPart` caveat | *"the #9963 disclosure"* → *"the map-consistency disclosure"* | Names the behaviour instead of the issue. |
| **W-35** | §8 `produces` | *"Restored by #11083 (owner 2026-11-05, cites `#7848`'s `OVERRIDES:`)"* → *"Restored by owner ruling"* | Keeps the authority (an owner ruling) and drops both numbers. ⚠️ The source's pointer was to an `OVERRIDES:` **on #7848**; that cannot be re-pointed at §8, whose two `OVERRIDES:` markers concern Point→Event propagation and the typing of every association, neither of which establishes `produces` as a Layer-2 meaning. The pointer is therefore dropped, not misdirected. |
| **W-36** | §8 layer test | *"— #2766's rule, which #11083 applies."* → deleted | The sentence is the rule; the two attributions are not. |
| **W-37** | §10.5 | the `#2488` merge-blocker paragraph → one rule sentence | It is a status paragraph by its own name ("merge-blocker"); its one rule is extracted. |

### 6.5 Proposed text — §6–§12 (replaces `ONTOLOGY.md` lines 1194–1568)

<!-- PROPOSED:§6 -->
````markdown
## §6. Subclass Model

Packs declare subclasses of core kinds via manifest `subclassOf`:

```yaml
objectKinds:
  - epic
  - issue
subclassOf:
  epic: Project
  issue: WorkItem
```

At query time, `expand_kind("Project")` returns `["Project", "dev:epic"]`. Queries filter by `pointKind IN [...expanded...]`.

**Core subclasses (no pack needed):**

| Parent | Core subclasses |
|--------|-----------------|
| Object | Project, WorkItem, Problem, tag, user, skill, tool, agent, workflow, agreement, standard, other, strategy, plan, goal, target |
| Document | **Not a subclass** — a document is a `:Source` (§4.4). Its vocabulary is the `documentKind` genre axis over `sourceKind: document` (§5) |
| Subject | organization, team, role, legalPerson, naturalPerson |

> No Object subclass has its own metadata table — they inherit Object fields verbatim. A document's fields are a source's fields, and they are in **§4.6**.

---

## §7. Equivalence Model

Packs declare equivalences between kinds across packs via `equivalentTo`:

```yaml
equivalentTo:
  issue: [pm:issue]
```

Bidirectional: querying `dev:issue` also returns `pm:issue`, and vice versa.

---

## §8. Semantic-Epistemic Edge Model

A relationship operates on **three layers** — **mechanism + direction** (how it acts, and which way
it runs), **semantics** (the meaning vocabulary), and **predicate + tags** (the specific verb, plus
optional free data). It carries an operator **only when it needs one**.

### The three epistemic mechanisms — support, attack, coherence

Every epistemic relation between two beliefs is exactly one of three mechanisms. The set is
**closed**, and a pair carries **at most one**.

| mechanism | asserts | behaviour | one-way | two-way |
|---|---|---|---|---|
| **support** | this is evidence for that | raises the other where it sits above it, and **lowers it where it sits below** (difference coupling — the one documented case where a SUPPORT edge lowers a belief, see below) | A is evidence for B | each is evidence for the other |
| **attack** | this counts against that | lowers the other, **never raises it** | A counts against B | mutually exclusive |
| **coherence** | A is a necessary condition for B | an unmet condition propagates **loss** to the dependent; a met one never raises either side | A is necessary for B (A falls ⇒ B falls) | both directions of necessity, priced in the stated order |

`IMPL` and `NAND` are the first two. **Coherence is the third**, and it is what a necessity
meaning (`dependsOn`, `hasPart`) is asking for. Both are still written as `IMPL` on the
operator-less direct-edge leg, because a bare edge carries its mechanism as its EDGE TYPE and
there are two (`IMPL`, `NAND`); coherence is reachable there as an **operator**
(`create_operator(op_type="coherence")`, which is what extraction mints). Teaching the bare-edge
leg to carry it is an open follow-up.

⛔ **Coherence is not "they move together".** Moving together is indistinguishable from support
and is what the old prose said: on `phi_coherence` the dependent never rises because of this
factor — only a necessary condition failing drags it down, which is the directed asymmetry that
makes the third mechanism worth having.

⛔ **Support can lower, and that is not a bug.** `phi_impl` couples the two beliefs by their
difference, so a source sitting BELOW its target pulls the target down. The direction flag decides
WHO receives a message, never the sign of the transfer, so "support only raises" was never true of
the shipped potential — it is recorded here rather than restated as an acceptance property
(`test_support_LOWERS_when_the_source_sits_BELOW_the_target` pins it).

**Direction is the only flag:** it says how far an assertion reaches, not what it means. Two-way
attack *is* mutual exclusivity; for coherence it selects which input is the necessary condition
(`->` = first, `<-` = second).

**Why closed.** Support and coherence agree while everything rises and diverge elsewhere: mutual
support inflates a well-evidenced pair (two reports of ONE fact are not two pieces of evidence),
coherence does not. Both on one pair silently over-states confidence, and the over-statement is
invisible because it looks like strong support. The set is closed so that state is unwritable:
`create_operator` and `create_direct_edge` each refuse a MIXED pair — an operator beside a bare edge of
the other mechanism, in either write order — through one shared reader, over live AND draft operators
(a draft is promoted, and the promotion re-checks nothing).

⛔ **The residual, stated rather than implied:** two BARE edges of different types on one pair
(`:IMPL` beside `:NAND`) are still writable. They do NOT share a message slot — a slot is keyed to an
actual edge (`(src, tgt, rel_type)`), so every shape here occupies its own — and the cost of leaving
them writable is the SAME one the refusal above exists to prevent: two factors pricing one pair with
different functional forms, i.e. an over-statement that looks like strong support. They are retained
only because refusing them changes a shape the engine has always allowed, so this table does not
claim they are refused and does not claim they are harmless. That shape needs the operator-less
`relation` route: a connection that carries `operator` writes an OPERATOR on every route, and an
operator that shares a **PRICED PAIR** with any existing mechanism — bare or operator — is refused,
including through `ingest`, whose Phase 1 reports the refusal as a `violations` entry instead of
raising mid-bundle. "Priced pair" is the unit the FACTOR prices, not the endpoint set
(`TortoiseEP._factor_pairs`, read by `sdk._priced_pairs`): `IMPL` is a STAR — `inputs[0]` → every
other input — so its TARGETS never price each other, and an n-ary `IMPL` does not conflict with a
mechanism on a pair BETWEEN two of its targets. A `NAND` or `coherence` whose direction flows both
ways prices a clique, so there the two units coincide. Not
covered here at all: `EventAPI.add_operator` (which accepts `IMPL`/`NAND` only, so the third
mechanism is unreachable there) and the projection's own edge fold.

### Reification rule — when an edge gets an operator

**An edge carries an operator iff it needs mitigation, or is an epistemic
support/contradict between epistemic nodes (any of `:Point`, `:Subject`,
`:Object`, `:Event` — see §5 on the point↔object/point↔event overlap).**
The operator node itself is always a `:Point {is_operator:true}`; its
children/targets may carry any of the four labels. All other edges stay plain
and carry confidence as an edge attribute.

> **OVERRIDES:** the earlier "Point→Event operators are write-only in v1, no
> EP propagation" rule — confidence propagates across
> `:Point`/`:Subject`/`:Object`/`:Event`, so an Event target is epistemic and
> propagates.

| Edge | Operator? | Confidence |
|---|---|---|
| Support / contradict (IMPL/NAND) between epistemic nodes (:Point/:Subject/:Object/:Event) | **Yes** | EP over the IMPL/NAND edge |
| Any edge needing mitigation (+/− relevance) | **Yes** — mitigations attach to the operator | EP over IMPL/NAND |
| Structural edge without mitigation (about\*, performs/produces/uses, memberOf/ownedBy, provenance) | **No** — plain edge | confidence edge attribute |
| `related` — the neutral association edge (§3.9) | **No** — plain edge | **none** — evidence-free by construction; no belief path may read it |

- **Neutral vs structural:** the `Structural edge without mitigation` row above
  permits a `confidence` attribute on a plain structural edge; for `related` that
  attribute is **not a weight** — the property may exist, and no confidence path reads
  it — because its neutrality is the point. "Structural" and "belief-free" are not
  synonyms:
  `extractedFrom` is structural and **does** carry weight, via the Beta prior set in
  `_apply_source_inheritance`. A predicate is neutral only when no read path traverses it
  — §3.9 records the breach that left `related`'s neutrality a target rather than a fact,
  closed (the affected-set traversal is now `IMPL`/`NAND`-filtered **and**
  directed, so only operator inputs are admitted).

- **Operator-less propagation:** an IMPL/NAND edge may be direct between any two plain epistemic
  nodes (`:Point`/`:Subject`/`:Object`/`:Event`; no operator); EP propagates over it the
  same way.
- **Direction:** four values, **recorded on the write**: `->` (source → target), `<-` (target →
  source), `<->` (both ways), `-` (none). Lives on the operator node when present, else on the
  edge. EP reads the operator node first, falls back to the edge.
- **Direction comes from semantics.** Each semantics value carries a direction, and the authoring
  path resolves and records it — a caller supplies the meaning and need not state a direction. The
  recorded direction is authoritative. *(Model: `create_operator` / `tortoise_create_operator`
  accept `semantics` and record the meaning's model direction verbatim, recording the basis on the
  operator as `direction_basis` — `explicit` / `semantics` / `default`. The four-value
  vocabulary is stored and interpreted on every path — `create_direct_edge`, the ingest contract,
  EP and the analyze BFS; the legacy `bidirectional` / `unidirectional` tokens remain
  accepted and readable aliases.)*
- `-` means the connection exists and **no confidence transfers along it**.
- **Lazy promotion:** a plain edge gains an operator only when mitigation
  becomes needed.
- **EP:** for operator-less edges, EP reads direction from the edge and
  initializes the edge message directly (operator-mediated edges compute
  messages on operator update).

Operator-mediated case (support/contradict, with mitigation anchor):

```text
Semantic:   (Feature) ──[addresses]──→ (CustomerNeed)    ← operator.label
Epistemic:        ↑ IMPL, confidence: 0.85               ← EP propagation
Operator:      (op-123)                                   ← mitigation anchor
```

| Layer | Where | What |
|-------|-------|------|
| **Mechanism + direction** | operator (`IMPL`/`NAND`/`coherence`) + the recorded direction | How it acts, and which way it runs: `->` `<-` `<->` `-` |
| **Semantics** | `semantics` field | The meaning: `addresses`, `supports`, `mutuallySupportive`, `mutuallyExclusive`, `refutes`, `causes`, `produces`, `hasPart`, `dependsOn`, `transacts`, `related`, `ownedBy`, `managedBy` |
| **Predicate + tags** | `operator.label` | The specific verb: `fixes`, `implements`, `deploysTo`, `bearsOn`… plus an optional free tag/note |
| Epistemic | IMPL/NAND edges | Confidence via EP (0-1 continuum) |
| Operator | Point (is_operator:true) | Mitigation target |

### The semantics vocabulary (Layer 2)

**Core is canonical, and a pack aligns to it.** The vocabulary is closed: a declaration outside it
is a typo, not a new meaning, and validation warns, naming the valid alternatives. The value is chosen
by the **co-variance test** — *does the relationship make the two ends' confidence move together?* —
and the answer is the direction the relation records.

| Semantics | Mechanism | Direction | Meaning |
|-----------|-----------|-----------|---------|
| `addresses` | IMPL | `->` | Aimed at / applies to — scope. A one-way link carries weak confidence: a pull request existing that addresses a bug is weak evidence the bug is real. |
| `supports` | IMPL | `->` | Positive evidence, one way |
| `refutes` | NAND | `->` | Negative evidence, one way — the directed attack: the attacker's truth penalizes the target, with no back-pressure |
| `mutuallySupportive` | IMPL | `<->` | Each end reinforces the other |
| `mutuallyExclusive` | NAND | `<->` | They rule each other out — a mutual restatement |
| `hasPart` | IMPL | `<->` | Composition — the whole REQUIRES its parts, so a part failing should lower the whole and a part being present must not inflate it. ⛔ The mechanism column says `IMPL` because that is the mechanism this table's map DECLARES and the bare-edge leg WRITES, and a bare edge carries its mechanism as its edge TYPE (`IMPL`/`NAND` only). The mechanism that prices necessity is `coherence` and it exists as an OPERATOR (`op_type="coherence"`); carrying it on a bare edge is an open follow-up. ⚠️ Do not pair `semantics="hasPart"` with a `coherence` operator and expect silence: the map-consistency disclosure compares the write against THIS map and will (correctly, about the map) report the mismatch |
| `causes` | IMPL | `->` | *A brought B about* — **untyped, any kind to any kind, no restriction.** ⛔ Not the same assertion as `produces` (next row) and not its replacement: W3C PROV states that generation does **not** assert a general causal relation. `causes` is the Layer-3-preferred spelling for a happening → the artifact it created, aligned to `schema:result` / `prov:wasGeneratedBy` (§3.8's structural Event→Object `produces` is a separate layer and does not conflict) |
| `produces` | IMPL | `<-` | **Production, entity → entity** — *the TARGET brought the source into existence.* Direction `<-` because the declared edge is `asset → program` while production runs producer → product, so `->` would assert the asset caused the program. Restored as a Layer-2 meaning by owner ruling; mapping it onto `causes` changes the meaning **and** inverts the direction. ⛔ Not a rename of `causes`: a produced thing may be unrelated to the producer causally, and a cause need not produce anything. |
| `dependsOn` | IMPL | `->` | A precondition — the condition bears on the dependent, one way; the dependent falls when the precondition is absent and is **not** raised by its presence, which is what `coherence` prices. ⛔ This map declares `IMPL` for the same reason the `hasPart` row does (bare-edge carriage), and the same caveat about pairing the meaning with a `coherence` operator applies |
| `transacts` | — | `-` | A trade or exchange — association only; no confidence either way |
| `related` | — | `-` | The deliberately ambiguous generic association, kept rare. **OVERRIDES:** the common practice of typing every association — a deliberate owner ruling, not an accident; no belief path reads it |
| `ownedBy` / `managedBy` | — | `->` | The core structural rows (§3.6) a pack may name as a relation's meaning. ⚠️ `ownedBy`'s `->` is only the DEFAULT: with its target left open (Subject **or** Object) a declaration carries the sense per edge — see §3.6 |

**A word that fails the layer test is a LABEL, not a meaning.** `funds` is the worked example: nothing in the machine changes behaviour because a relation is a funding relation, so it stays a **Layer-3 predicate** riding on an existing meaning, and it is deliberately **not** in this table (an earlier promotion was withdrawn). The movement of money is an **event** (`venture:disbursement`), not an edge.

**Same semantics + different predicates is correct.** `fixes`, `implements` and `deploysTo` all
carry `addresses`; the **predicate** (Layer 3) keeps them distinct. Semantics is a **meaning**
vocabulary, not a shape flag — `addresses` and `supports` are different meanings that share a
direction and are not merged into one word.

> **Direction is recorded on the write — four values: `->`, `<-`, `<->`, `-`.** It comes from the
> connection's semantics and the recorded value is authoritative. `-` means the connection exists and
> **no confidence transfers along it**, so direction and confidence-transfer are one axis. *(The
> legacy `bidirectional` / `unidirectional` tokens remain accepted and readable aliases.)*

A relation declaration may also carry **`nearMisses`** — the sibling predicates or semantics it is
confused with, mirroring `kindDefs`. Each target resolves to a declared predicate or a semantics
value.

### Pack Relation Declarations

```yaml
relations:
  - predicate: decomposesInto
    mechanism: IMPL
    semantics: hasPart
    fromKind: dev:epic
    toKind: dev:issue
    nearMisses: [related]
```

---

## §9. Expansion Pack Manifest Format

```yaml
namespace: dev
name: "Development"
version: "0.1.0"
tier: free
ontology:
  extends: core
  objectKinds: [epic, issue, code, bug, incident]
  subclassOf: {epic: Project, issue: WorkItem, bug: Problem, incident: Problem}
  equivalentTo: {issue: [pm:issue]}
  pointKinds: [technicalDebt, risk]
  documentKinds: [architectureDoc, apiSpec]
  kindDefs:                     # extractor prompt material (v3)
    epic:
      description: A large body of work decomposed into issues
      nearMisses: [issue]
  relations:
    - predicate: decomposesInto
      mechanism: IMPL
      semantics: hasPart
      fromKind: dev:epic
      toKind: dev:issue
      extractable: true
  chains:                       # business-logic paths (v3)
    - id: epicToCode
      steps: [epic, issue, code]
      enforcement: warn
  memory_granularity: 'Durable: the epic/issue state and the reasoning.
    Ephemeral: sprint mechanics, ticket logistics.'
extraction:                     # extraction activation + enforcement (v3)
  active: true
  sourceTypes: [conversation]
  enforcement:
    default: warn
    kinds:
      incident: retry
```

> `requirement` is declared once, by `product-strategy`, as an **Object** kind
> (`subclassOf: requirement: WorkItem`, owner ruling) — it is not a dev
> point kind. The epistemic claim about a requirement is a separate Point linked
> to it.

Packs are loaded via the pack registry (`PackRegistry`) at startup; their kinds extend the core vocabulary and are queryable via `expand_kind` / `equivalentTo`. The manifest is **declarative only — no code executes on load**; connector/tool entrypoints are code references that are allowlisted (starter packs) and rejected on hosted tenant uploads (ontology-only v1). Authoring guidance: `docs/EXPANSION_PACKS.md` (behavior) + `packs/_template/manifest.yaml` (machine-checkable schema).

---

## §10. Epistemic Recency Modulation

Evidence aging is **user-configurable with a light default** — NOT blunt time decay. Stable facts stay strong regardless of age. Interacts with sourceKind/credibility tier (T0 direct observation ages differently than T4 speculation). Never auto-deprecates old evidence.

> **Decision log (v3.2):** temporal decay granularity = **deferred**
> (per-field / per-sourceType decay curves NOT shipped). Retained: the validated
> `0.95^years` modulation, T0-exempt, keyed on `sourceDate` else `ingestedAt`, recomputed
> per EP run via provenance-marked inherited baselines (`baseline_source='inherited-from-source'`,
> per-point time gate). Differentiated per-tier aging (§10 "ages differently") is a
> §10-implied follow-up; the extension point is the source-type registry's per-kind
> default/tier slot.

---

## §10.5 Cascading Invalidation (Claims 6–7 of the patent)

When an evidence source's confidence changes, downstream propositions that
depend on it through operator chains are **re-evaluated, not stored-flagged**.
Invalidation is a *derived* cascade — consistent with the ontology's
"Object.status is a cache of the event stream, never authoritative" doctrine (§2, §11):

```text
supersede_point / invalidate_point          (§3.1: mark old outdated:true,
                                            create (new)-[:CORRECTS]->(old))
   → _mark_dirty(affected)                  (sdk.py — dirty roots queued)
   → EP re-propagation                      (ep.py _affected_claims: reverse
                                            BFS through IMPL|NAND, max_hops=2)
   → re-persist confidence to affected      (dream.py runs EP on dirty roots)
   → contested-claim detection at query     (get_contested_claims(variance),
     time                                   ep.py — variance from persisted
                                            α/β; also surfaced per-result as
                                            ep.contested in search)
```

**Terminal posterior vacuity decay:** every terminalizing write
(retract/supersede/invalidate/assess_source + the rebuild folds) decays the
terminal claim to VACUITY — `confidence=0.5`, posterior `(1,1)` — atomically
with the status/flag write, so an include-terminal surface never shows a
frozen pre-terminal posterior. Decay is UNIFORM across Case-1
restatement and Case-2 correction (the old claim is terminal either way; the
successor recomputes independently). `ep_alpha`/`ep_beta` are deliberately
retained as prior history — there is NO unsupersede path that recovers the
old claim's posterior, so the retained prior is the SOLE recovery vector.
Every contested computation (annotate_ep_batch, rankers, `get_contested_claims`,
`_review_prune`, why, analyze) excludes terminal claims via the shared
live.py predicate (status ∈ {retracted, superseded, outdated, archived,
deprecated} OR `outdated=true`).

**Rebuild-side fold decay is supersede-scoped:** the rebuild-side fold decay
applies to the *supersede* fold only.

**Design decisions (recorded for the patent filing):**

| Question | Decision |
|----------|----------|
| Ontology concept vs implementation detail? | **Derived behavior**, documented here; no new stored entity |
| Dedicated edge type (DEPENDS_ON)? | **No** — reverse traversal of IMPL/NAND operators is sufficient; a stored DEPENDS_ON edge would duplicate structure and drift |
| Representation of "potentially invalidated"? | **Elevated posterior variance** (v > 0.04 → contested), not a stored `pointStatus` — statuses are `{draft, live, deprecated, superseded}` (§5), with `retracted`/`outdated`/`archived` read-side legacy over stored rows; `outdated` is set only by an explicit terminalizing write (`supersede_point` / `invalidate_point`), never auto-inferred — it does **not** identify a supersession (§4.7) |
| Terminal claims' posterior after terminalization? | **Decay to vacuity** (0.5, posterior (1,1)) at the terminalizing write + rebuild fold — never a frozen pre-terminal posterior; `ep_alpha`/`ep_beta` retained as the sole recovery vector |
| Interaction with CORRECTS? | CORRECTS is the *structural* replacement; cascading invalidation is the *belief-level* consequence — both fire from the same write (`supersede_point` → `_mark_dirty`) |

Direction-aware EP (§3.1) is the prerequisite that makes reverse
traversal well-defined: `_affected_claims` follows the recorded direction
(`->`, `<-`, `<->` or `-`), which the connection's semantics determines.*

---

## §11. Reputation (derived, not stored)

`compute_reputation(subject_id)` is a query-time primitive:

```text
subject -[:performs]-> events → outcome operators (Event→Point IMPL/NAND)
  → aggregate success/failure, optionally weighted by recency
  → return reputation score
```

- Not stored (would go stale)
- Bridges procedural history to epistemic belief: "how much weight should this agent's claim carry?"
- **Derived values may be CACHED, never authoritative (v3.2):** Source
  `reliability` is a write-through projection of the query-time derivation
  (recomputed on write events, consistency-checked on read, stamped with
  `reliability_derived_at`) — the derivation is the truth, the cache is a
  performance artifact.

---

## §12. Relationship to Standards

| Standard | Alignment |
|----------|-----------|
| **PROV-O** | Subject→Agent, Event→Activity, Object→Entity. `uses`=prov:used (Activity→Entity, direction-identical), `produces`=prov:wasGeneratedBy inverse, `wasDerivedFrom`=prov:wasDerivedFrom, `performs`=inverse of prov:wasAssociatedWith (PROV names Activity→Agent accountability; we name the Agent→Activity doing verb). **Temporal:** the valid-time window (`validFrom`/`validTo`) ≈ `prov:generatedAtTime`/`prov:invalidatedAtTime` (the entity's existence window); transaction time (`createdAt`/`expiredAt`, Source `ingestedAt`, Event `capturedAt`) is the graph-write clock — a record-reification clock, not a PROV time property. |
| **Schema.org** | Event with startTime/endTime. Action pattern: `performs`=schema:agent inverse (the "direct performer or driver of the action"), `produces`=schema:result, `uses`=schema:instrument (mechanisms) / schema:input. |
| **BIBO** | `documentKind` genre vocabulary (v3.15/D10: an axis over `sourceKind: document`, not an Object subclass). |
| **OWL-Time** | Event is the temporal entity (`startedAt`/`endedAt` = the valid-time alias, §4.7). Non-event validity windows (`validFrom`/`validTo`) are intervals on the same axis. Transaction time is the record clock, not an OWL-Time interval. |
| **Bi-temporal (Graphiti/Zep)** | Two orthogonal axes — **valid time** (`validFrom`/`validTo`) and **transaction time** (`createdAt`/`expiredAt`) — plus supersession as a third, separate fact (`supersededAt` on Object; `status='superseded'` on Point — the `outdated` flag + `CORRECTS` edge are shared with invalidation and do not distinguish the two). Window contiguity on **supersession** is the Graphiti (Zep) **intent, conditional**: `validTo` = the successor's `validFrom` **when it carries one**, else its `createdAt`, else `now` (exact contiguity only for a dated successor); `invalidate_point` instead stamps `validTo = now` (no successor ⇒ no contiguity), and refuses with `ValueError` when a stored `validFrom` is after `now` — `retract_point` is the window-agnostic route (§4.7). |
| **RDF-star** | Operators are reified edges with metadata (label + confidence) — RDF-star-like reification for epistemic edges. |
````

> **Approve:** ☐ text as written · ☐ with changes (list them) · ☐ reject (§6–§12 keep their numbers)
>
> **Approve the wording changes separately:** ☐ W-32 ☐ W-33 ☐ W-34 ☐ W-35 ☐ W-36 ☐ W-37 · ☐ reject all

### 6.6 Anything not classifiable

**Nothing in §6–§12 is unclassifiable.** Ref coverage:

```bash
python3 - <<'PY'
import re
src='\n'.join(open('docs/ONTOLOGY.md').read().split('\n')[1193:1568])
# NOTE: `#[0-9]+`, not `#[0-9]{3,5}` — this section's only two-digit reference (#86)
# is invisible to the three-digit pattern, which is exactly how it survived.
print(sorted(set(re.findall(r'#(\d+)', src)), key=int))
PY
# → 23 distinct: ... '86' among them
```

⚠️ **The pattern above deliberately matches ANY digit run.** A `#[0-9]{3,5}` sweep of this range
prints 22 ids and omits `#86` — the very reference this batch exists to remove.

**§6 and §7 are proposed unchanged** (zero references), so approving this batch is also an
assertion that they need no change.

---

## Final state — the whole document

### F.1 What approval produces

If every batch is approved as written, **applying the seven `<!-- PROPOSED:… -->` blocks in order**
yields a `docs/ONTOLOGY.md` that:

| Measure | Before (`c1c8df1a3817`) | After | Note |
|---|---|---|---|
| tracker references (`#` + digits) | **324** | **0** | 323 `#NNNN` + `#86`; across 192 lines |
| bare tracker identities (`epic N`, `N-Cn`) | **4** | **0** | found in §3.1 and §4.1 only |
| lines (`wc -l`) | 1,568 | **1,557** | **−11**, itemized by batch in F.1b |
| declared-vocabulary blocks | 12 | **12** | all preserved; **6** lines changed inside them (3 Event-kind, 2 Response-Contract, 1 in §10.5's invalidation block) — every one a trailing comment, no declared term touched |
| D1 (normative tokens + vocabulary blocks) | **166** | **167** | **+1 net**: two adds (header +1, the §4.2 clobber guard +1 by W-9) against one loss (§3.1's moved build-gap sentence, 2-M1) |
| rules moved out of the model | — | **0** | verified section by section; the *only* moved text is status/provenance |

The assembled document is checkable end to end:

```bash
grep -nE '#[0-9]' docs/ONTOLOGY.md                                        # no output (any digit, not just 3-5)
grep -nE '(epic|issue|PR) [0-9]{2,5}|[0-9]{3,5}-C[0-9]' docs/ONTOLOGY.md # no output
```

The first grep deliberately matches **any** digit run: the `#86` above was invisible to a
`#[0-9]\{3,5\}` pattern, which is how it survived nine review cycles.

#### F.1b Where the −11 lines come from (per batch)

| Batch | lines in | lines out | Δ | what removed the lines |
|---|---|---|---|---|
| 1 (header, §2; §1 unchanged) | 91 | 91 | 0 | no line removed — the header and §2 edits are all in place |
| 2 (§3) | 281 | 280 | −1 | the `#2199` parenthetical folded away |
| 3 (§4.1–§4.4) | 128 | 121 | **−7** | §4.1 −2 (the `#11915` status-note line and the blank line after it) and §4.4 −5 (the `TODAY`/meeting-capture paragraph 6 → 2 = −4, the `#5013` deferred-ruling collapse −1; the `TARGET STATE` framing folds with no net line). §4.2 and §4.3 lose **no lines** — every edit there is inside an existing line |
| 4 (§4.5–§4.8) | 456 | 456 | 0 | citations and status only — **no line count change** |
| 5 (§5) | 220 | 220 | 0 | the `retract_point` residual sentence is removed inside an existing blockquote — no line lost |
| 6 (§6–§12) | 375 | 372 | −3 | the §10.5 status paragraph collapses 5 → 2 |
| **total** | **1,551** | **1,540** | **−11** | — |

*The total row counts only the replaced ranges; adding the unchanged lines 44–60 (§1, 17 lines)
gives the document totals: **1,568 → 1,557** (`wc -l`), measured by splicing the blocks into the
source rather than by adding up the parts.*

### F.2 What is NOT in this proposal (stated so it is not assumed to be)

| Excluded | Why | Consequence |
|---|---|---|
| **Bare dates** (`2026-08-12`, `2026-11-17`) | Default `D-6`: the owner named issue numbers and changelog, not dates, and a date sweep is a much larger diff. | The model keeps dated state names. Widen `D-6` and batches 2–6 roughly double. |
| **Lettered decision labels** (`D10`, `R12/C6`, `W-4`, `S5`, `P1`) | Same scope reason: they name a model state or a plan step, and removing them is a separate editorial pass with no issue-number content. | Most remain (`D10` appears throughout §4). **Some went with the clause they annotated** — `R17` (W-10), `E6` (§4.7), `E1` at one of its two sites, `R2` at two of its three (its survival is the `docs/durability-posture.md` record tag), and `S2`, `P3`, `P4`, `F1`, `OD-4`, `W1` — each named in the row that removed it. `R2` still survives at `docs/durability-posture.md`'s record tag. This is a consequence of removing the clause, not a separate label sweep. |
| **Digits inside real paths** (`tests/test_subject_supersession_1423.py`, the PGlite `9972-cycle` case name) | Renaming a test to satisfy a grep is a code change with its own risk. | These are **not** `#NNNN` and the greps in F.1 stay at 0. |
| **The four class-A refs inside `ONTOLOGY-CHANGELOG.md`** | That file is the changelog; numbers belong there. | n/a — this proposal only removes numbers from the *model*. |

### F.3 Open policy items collected (each rejectable on its own)

| # | Item | Batch | If rejected |
|---|---|---|---|
| **P-1** | Remove the carve-out at the tail of `ONTOLOGY.md:36` — *"A reference that records **where a term or a decision lives** … is not history and **stays**"* | 1 | **17** location references stay (the `#NNNN §x.y` form); strict-zero is unreachable for those. |
| **P-2** | Bump `v3.38` → `v3.39` in frontmatter `title:` *and* H1, `updated:` → `2026-11-23` | 1 | The model changes without a version change. |
| **P-3** | Add the header's one changelog pointer + the "new prose names a location by section or by code symbol, never by an issue" rule (scoped to new prose, so it composes with `P-1` either way) | 1 | No in-file rule against re-pollution. |
| **P-4** | Rewrite §4.1's owner-approved *"Percent, ratio, duration and quantity …"* sentence and delete its `#11915` status note | 3 | That sentence and its note return verbatim; §4.1 keeps one reference. |

**P-1, P-2 and P-4 change an EXISTING rule; P-3 ADDS one.** ⚠️ **A fourth rule is ADDED outside
the P-list**: batch 3's **W-9**, which restates §4.2's clobber guard as *"must not reset it to
`live`"* where the source only described the old bug — it is on batch 3's wording-approval line.
Those five are the complete set of rule-changing items; **everything else removes a citation or
moves status**, which is why D1 moves by only +1 net (two adds, one loss at §3.1's moved
build-gap sentence). If the owner wants a minimal-risk landing, approving **all six batches with P-1 and P-4 rejected**
still removes **306 of 324** references. The 18 that stay are the 17 `#NNNN §x.y` **location
pointers** that P-1's carve-out protects, plus §4.1's one `#11915`. **No rule moves** in that shape.

### F.4 How the per-section assertions were produced

Every count in this proposal is reproducible, and each was recomputed after every edit — several were
wrong on the first pass and were corrected (a truncated §3 extraction, an under-counted `#2194`, a
D1 that ignored vocabulary blocks). The checks, in the form they can be re-run:

```bash
# 1. refs per section
python3 - <<'PY'
import re
src=open('docs/ONTOLOGY.md').read().split('\n')
ref=re.compile(r'#(\d{3,5})(?![0-9])')
for a,b,name in [(1,43,'header'),(45,60,'§1'),(61,108,'§2'),(109,389,'§3'),(390,517,'§4a'),
                 (518,973,'§4b'),(974,1193,'§5'),(1194,1568,'§6-12')]:
    seg='\n'.join(src[a-1:b])
    print(f"{name:6s} {len(ref.findall(seg)):3d}")  # note: this pattern misses #86 — see F.1
PY

# 2. normative count (definition in §5 of this proposal)
python3 - <<'PY'
import re
seg=open('docs/ONTOLOGY.md').read()
print(len(re.findall(r'\b(must|shall|required|always|never)\b',seg,re.I)),
      len(re.findall(r'^```text$',seg,re.M)))
PY
```

**A caution for anyone re-running #2 against a PROPOSED block:** a block containing its own
```` ```text ```` or ```` ```yaml ```` fences must be extracted with the **four-backtick** form
(`§3`, `§5`, `§6`), or the extraction silently truncates at the first inner fence and reports a
much smaller number. That happened twice during authoring; both times the truncation was caught by
the count disagreeing with the per-section table, which is why the tables are per section rather
than one total.
