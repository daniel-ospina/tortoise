---
title: "Source versioning & supersession — SOTA for tracking a changed source"
type: research
domain: data
status: draft
created: 2026-09-24
updated: 2026-09-24
ownedBy: epistemic-team
doc_status: draft
subjects.team: epistemic-team
---

# Research brief — source versioning and supersession

**Date:** 2026-09-24 · **Question (owner):** *"shouldn't we have some form of version tracking/control for sources? at least to know if they changed and we might need to re-infer the Entities... new versions should have supersede relation, so we can track version changes (what was believed before, what is believed in the new version). But research this to check SOTA approach."*

**Depth:** Medium–Deep (4 external queries, ~30 sources; internal: `ONTOLOGY.md` §4.6/§4.7, `#2489`, `#5024`, D7).

---

## 0. Problem reframing

**5 Whys.** Why track versions? → so stale entities don't silently stand. Why does that happen? → a source changed and we didn't notice. Why not? → **`extractedFrom` records the SOURCE, not the version read**. Why not the version? → the source layer was modelled as immutable-by-hash. Why that model? → because `contentHash` was framed as an *"idempotency anchor — skip re-extraction if unchanged"* rather than as a **version identity**.

**⇒ Reframed.** The real question is not *"how do we version sources"* but:

> **Where does supersession live when the evidence under a belief changes — on the source, on the extracted facts, or on both?**

**How Might We:** *"How might we track that a belief's evidence changed, **without** versioning the source layer at all?"* — the answer the literature gives is that you cannot: without a version edge you cannot tell *which* revision a fact came from.

**Reverse:** if we did nothing, re-fetching a changed source either **duplicates** entities (v1 and v2 facts standing side by side, mutually contradictory, nothing marking the conflict) or **never re-extracts** (beliefs silently pinned to superseded content).

---

## 1. Internal — what we already have

**⭐ The slots are ALREADY DECLARED and never written.** `ONTOLOGY.md` §4.6 declares on `:Source`:

| field | declared meaning **as read at research time** (pre-v3.15) | status |
|---|---|---|
| `validFrom` / `validTo` | *"Valid-time window — when the source content held in the world"* (§4.7, `#3642`) | **❌ not written by `_upsert_source`** |
| `expiredAt` | *"Transaction-time expiry — when our record of the Source stopped being current"* (§4.7, `#3642`) | **❌ not written** |
| `contentHash` | *"Idempotency anchor — **skip re-extraction if unchanged**"* | ✅ written — **§4.6 now states this as a *version anchor* (v3.15); the idempotency framing quoted here is the pre-v3.15 wording** |
| `updatedAt` | *"Last modified (set ON MATCH by `_upsert_source`)"* | ✅ written, **in place, unjournalled** (`#5024`) |
| `sourceDate` | *"Evidence-age clock for recency decay"* | ⚠️ |

**⇒ The version model is not a new invention. It is *populating declared slots*** — and §4.7 already separates the two axes correctly, which is the single most important thing to preserve.

**Supporting internal decisions:**
- **D7** — changes are handled by **correction events, not in-place edits**.
- **`#2489`** — the rebuild machinery: `aboutDocument` is snapshot-derivable and **re-created at the old point**; `aboutSource` is deliberately excluded and never resurrects.
- **`#5024` (T6)** — the version bump is **unjournalled**, so `derived = replay(journal)` is false for the source layer.
- **`#3998`** — *absent / erased* is a third value on the source record.

---

## 2. External findings

### 2.1 The convergence: **BOTH — with different jobs**

**The field does not choose between versioning the source and superseding the facts.** It does both, and assigns each a role:

| layer | what it tracks | mechanism (evidenced) |
|---|---|---|
| **the SOURCE / document** | *the raw artifact's lineage* | version node + validity periods — **`VERSION_OF`** relationships with temporal intervals (FourCorners); *"carry relationships forward to a new node version unless explicitly deleted or recreated"* (anyshift) |
| **the FACTS / edges** | *what was believed* | edge-level **`valid_at` / `invalid_at`**, `invalidated_by` (Graphiti, OpenAI temporal-agents cookbook, Zep); *"old facts are invalidated rather than deleted"* (Zep) |
| **the LINK between them** | *which revision a fact came from* | *"every entity/relationship traces back to the raw **episodes** that produced it"* (Graphiti); provenance **at the statement/edge level**, not only the dataset level (payzensecurity); `wasDerivedFrom` / `wasGeneratedBy` / `used` (PROV-O) |

**Corroborated by 4 independent categories** (openreview paper, vendor engineering docs, OpenAI cookbook, industry practitioner blogs) ⇒ **HIGH confidence.**

### 2.2 ⭐ The lesson that changes the design: **document-level supersession alone is probably INSUFFICIENT**

⚠️ **Confidence: MEDIUM — one detailed practitioner account** (see §6). This is a single-source finding, and it is reported here as a *signal*, not a settled result. We adopt the design it points to because it also follows independently from our own architecture (the graph is a projection of the journal), not because the account settles the question.

That practitioner **started with a document-granularity supersession model and then revised it to move time onto the relationship edges**, with validity windows and ingest timestamps on the facts. ⚠️ **This brief does not name the account** — it is the one source above whose identity was not captured, which is itself a reason to treat the finding as a signal rather than a citation.

**⇒ The claim is that if you supersede only the document, you cannot answer "which of my beliefs changed?"** — the document is marked stale, but every extracted fact is untouched and still reads as current. **The owner's stated goal — *"what was believed before, what is believed in the new version"* — is a question about FACTS, and document-level supersession may not be able to answer it.** Treated as a design constraint rather than a proven law.

**⭐ Note on mechanism (this is where we depart from the cited pattern).** The HIGH-confidence evidence in §2.1 describes a **version NODE** (`VERSION_OF` with intervals). **We do not adopt that** — a node per version multiplies nodes against the volume problem, and our journal is already the append-only record. Our model keeps **one node per `url`** carrying the **current** version, and treats prior-version windows as **journal records**, which is what §1's invariant (`derived = replay(journal)`) already implies. The **convergence** is on *where time lives* (both layers); the storage mechanism is ours.

**Corollary:** doc-level supersession is **necessary but not sufficient**. It tells you *what to re-examine*; the fact-level windows tell you *what changed*.

### 2.3 Recommended practice, in four named patterns

1. **Additive supersession** — never overwrite; add the replacement and **link it**.
2. **Linking replacements** — the supersession is an explicit relation, not inferred from timestamps.
3. **Keep current and audit views separate** — the hot/current view stays small; the audit view is the full history.
4. **Provenance granularity is a choice** — dataset vs record vs **claim**; choose deliberately and state it.

### 2.4 ⚠️ Adversarial: how this FAILS in production (the named pitfalls)

| pitfall | source | relevance to us |
|---|---|---|
| **Accidental overwrites** | kindatechnical | ⚠️ **We already have this (#5024).** |
| **Forgetting to CLOSE intervals** | kindatechnical | ⚠️ **THE classic bug.** An unclosed `validTo` means "still true", forever. |
| **Mixing valid time with transaction time** | kindatechnical | §4.7 separates them correctly — **preserve that; do not blur it** |
| **Versioned KGs are resource-intensive** — storage for multiple versions | Construction of KGs survey (2024) | ⚠️ **Direct cost pressure** — we have a $9–19/month budget |
| **Patch-based versioning makes reconstruction/querying inefficient** | Bi-VAKs (TU Delft) | ⇒ prefer intervals over patch chains |
| **Querying large historical graphs is hard at scale — many joins** | systematic review (PMC) | ⇒ the current/audit split is a performance necessity, not a nicety |
| **Time treated as mere metadata → destructive overwriting, recency sorting, expensive per-ingestion LLM arbitration** | "Time is Not a Label" | ⚠️ do not make the model reason about time per item |
| **Graphs become stale historical artifacts when semantics stop evolving** | improvado | the version model must not become the only thing that evolves |
| **Neither a temporal match nor a related-version match is correct** in real linkage cases | CEUR-WS | version linkage is genuinely hard — **do not claim it is free** |

**Lowest-confidence area:** the adversarial set is 1 source each (`⚠️ single-source` per claim), **except** the multi-source convergence that **bitemporality adds real complexity and cost** — 2 academic surveys, tiered **MEDIUM** in §6 (not MEDIUM–HIGH: two surveys is not the 3+ independent categories the HIGH tier requires).

---

## 3. Adoption gate (contradiction test — run FIRST, as mandated)

**What the standard would touch:** `ONTOLOGY.md` §4.6 (`:Source` fields), §4.4 (document-as-source), §4.7 (temporal model), the extraction link, and the quota/cost model.

| recorded decision | verdict |
|---|---|
| **D7** — correction events, not in-place edits | ✅ **SUPPORTS** — the standard *is* D7 applied to sources |
| **§4.7 bitemporal model** — `validFrom`/`validTo` + `createdAt`/`expiredAt` declared on `:Source` | ✅ **SUPPORTS** — this populates declared slots; it does not add an axis |
| **Append-only evidence convergence** (*"new correction events, not in-place edits"*) | ✅ **SUPPORTS** |
| **`contentHash` as "skip re-extraction if unchanged"** | ✅ **SUPPORTS** — re-extraction-on-change was already anticipated, and v3.15 makes `contentHash` the version anchor outright |
| **D30 / `#3919`** — raw lives outside the graph | ✅ **SUPPORTS** — and it *bounds the cost*, see §4 |
| Any decision that sources are never versioned? | **NONE FOUND** |

**⇒ `Adoption gate: ADOPT — no recorded decision contradicted.`** The model populates §4.7's already-declared Source window, and D7 is its governing decision.

**⚠️ But it carries a cost constraint, not a contradiction.** The adversarial evidence names **resource-intensity as the top failure mode**, and this box already has a hard budget. **Adoption must be bounded** — see §4.

---

## 4. What this means for our design (the synthesis)

**① Version the source — but store the WINDOW and the HASH, not the content.**
`validFrom`/`validTo`/`expiredAt` are already declared. Writing them costs **three timestamps per version**, not a copy of the artifact — because **D30 already moved content out of the graph**. *(This is the single biggest reason the version model is affordable for us where it is expensive for others: the expensive part — multi-version content — is already outside the graph.)*

**② Supersede at the FACT level too — or the owner's goal is unreachable.**
Entities extracted from v1 must be superseded by their v2 counterparts, via correction events (D7). **Document-level supersession alone is probably insufficient to answer "what was believed before"** (§2.2) — a single-source finding, adopted as a design constraint rather than a proven law.

**③ Record the version on the extraction link.**
This is the missing piece: *which content was read*. Without it, "are these entities current?" is unanswerable from the graph.

**④ Two questions, two anchors — both reads.**

| question | anchor |
|---|---|
| *"are these entities trustworthy?"* | confidence, no `NAND`, not superseded |
| *"are these entities about the CURRENT content?"* | recorded version vs current version |

**⑤ ⚠️ Interval-closing must be automatic and journalled.**
*"Forgetting to close intervals"* is the field's most-named bug — **and it is precisely what `#5024` is about.** An unclosed `validTo` reads as "still true" forever. **This is the highest-risk part of the design and it is already filed.**

**⑥ Keep the current and audit views separate.**
Named as best practice and as a scaling necessity — and it is the same invariant our storage doc already rests on (*cost scales with the working set, not with total stored data*).

---

## 5. Recommendation

**Adopt: version the source AND supersede the facts, with the version recorded on the extraction link.**

- **Source level** — validity window + supersession relation: **what changed in the evidence**.
- **Fact level** — validity windows + correction events: **what we believed before vs now** *(this is the owner's stated goal)*.
- **The link** — the version read: **the anchor that makes both answerable**.

**Bound it as follows**, given the cost evidence: windows-and-hashes only (never content copies), intervals over patch chains, and the current/audit split.

**Open question for the owner:** the **decision policy for what a new version does to the old version's entities** — *supersede immediately on version change*, or *mark stale and supersede only when re-inference runs*. **Recommend the latter**: immediate supersession invalidates beliefs before their replacement exists, which would make the graph momentarily assert *nothing* about the subject.

---

## 6. Source confidence summary

| claim | tier |
|---|---|
| Bitemporal (valid + transaction time) is the field standard | **HIGH** — 4+ independent categories |
| Version the source AND supersede at the fact/edge level | **HIGH** — 4 independent categories |
| Superseded facts are closed, not deleted | **HIGH** — Graphiti, Zep, OpenAI cookbook |
| Provenance belongs at the statement/edge level | **MEDIUM** — payzensecurity + IntuitiveAI |
| Document-level supersession alone is insufficient | **MEDIUM** ⚠️ emerging — one detailed practitioner account (§2.2; the brief does not name it) |
| Additive supersession / linking replacements / separate current+audit views | **MEDIUM** — IntuitiveAI + improvado |
| Versioned KGs are resource-intensive; patch-based versioning is inefficient | **MEDIUM** ⚠️ emerging — 2 academic surveys |
| The specific pitfalls (accidental overwrite, unclosed intervals, mixing the axes) | **LOW** ⚠️ single-source each — but consistent with our own `#5024` |
| "Neither a temporal nor a related-version match is correct" in real linkage | **LOW** ⚠️ single-source |
