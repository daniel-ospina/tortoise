---
title: "Research brief — entity attachment & disambiguation: what S3 RESOLVE should resolve to (#2730)"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-24
updated: 2026-09-24
subjects.team: epistemic-team
issue: "#2730"
aboutSubjects: Tortoise entity resolution (S3 RESOLVE), extractor v4 write path
aboutObjects: EXTRACTOR-V4-ARCHITECTURE.md, entity-attachment, #3584 opaque id, D10 #5013, D4/D9
---

# Entity attachment & disambiguation from context — what S3 RESOLVE should resolve *to*

**Issue:** [#2730](https://github.com/daniel-ospina/tortoise/issues/2730) · **Track:** research (input to E1 [#5087](https://github.com/daniel-ospina/tortoise/issues/5087), the Extractor v4 write path)
**Date:** 2026-09-24 · **Lane:** A (research) · **Status:** research brief — no code, no implementation proposal
**Predecessor brief:** `~/.swarm/research/2026-09-09-entity-attachment.md` (2026-09-09, 11 internal evidence points, 10 external precedents). **This brief does not repeat it — it reconciles it** against four decisions that post-date it: **#3584** (opaque id), **D10/#5013** (a document is a `:Source`), **D4/D9** (S3 owns the lookup), and the **EXTRACTOR-V4-ARCHITECTURE.md** design (PR #5016).

---

## 0. Research preflight

**Search tool: `web_search` with `model="sonar"` — rung 2.** Rung 1 was attempted first and is **unavailable on this machine**: `mcp_load seo-intelligence` → `Unknown MCP server 'seo-intelligence'`. The rung-2 model was named explicitly on every call; no `deep-research`/`reasoning-pro` model was used. Rung 3 (`web_fetch`) was available and not required. **6 external queries**, rate-limited once (HTTP 429) and re-run serially.

**Graph access:** the hosted MCP (`https://api.premiselabs.co/mcp/`) via `Authorization: Bearer $TORTOISE_MCP_API_KEY`. ⚠️ **The committed `.mcp.json` points at `${TORTOISE_API_KEY}`, which is unset in this environment** — the key present is `TORTOISE_MCP_API_KEY`. This is a **config gap, filed as [#5153](https://github.com/daniel-ospina/tortoise/issues/5153)** — the read above used the credential that exists; it did not change the plan or the config.

**Citation pin:** every code locator in this brief was re-read at **HEAD `c4580b083`** (2026-09-24) immediately before filing.

**Precedence (per the alignment material on #2730 and #4333):** `docs/ONTOLOGY.md` → **#5013 (D10)** → `docs/architecture/{STORAGE,EXTRACTOR-V4}-ARCHITECTURE.md` → this issue.

---

## 1. Step 0 — problem reframing

**The predecessor brief's reframe was right and still holds: the defect is not "resolution is dumb".** Entity identity is keyed on `name`, and the ingest contract carries no scope channel — so two entities that share a name are *the same node by construction*, and no resolution quality can fix that.

**What has changed since 2026-09-09 is the answer to "identity keyed on what".** `#3584` (merged as a **decision doc**, 2026-09-15) settled it:

> *"Stop deriving identity from the name. Mint an opaque id once at creation, carried in every journal event. The name becomes a mutable natural key held in a lookup index — not the identity."*

**⇒ The question is no longer "should identity be `(scope, kind, name)`?"** — that framing is **superseded**. The question this issue now owns is narrower and more useful:

> **Given that an entity's identity is an opaque id (`#3584`), what must S3 RESOLVE decide, what context must constrain its candidates, and what should a resolved mention be attached to?**

**5-Whys, applied to "why does this matter now":**
1. Why research attachment? → because 62.3 % of our Objects are bare definite descriptions (§3).
2. Why are they? → because resolution is name-keyed and context-blind, and the narrative is dropped at the resolution seam.
3. Why is context dropped? → because the resolution prompt receives only `id | name | kind` (§3, E4).
4. Why does that matter if each entity is unique by name? → **because unique-by-name is exactly the failure**: two real entities that share a name become *one node*, and one entity under two surface forms becomes *two nodes*.
5. Why not just scope the identity key? → **because `#3584` already decided identity is not the name — and the code has not implemented it** (§3, E2). Scoping the name-key re-litigates a settled decision.

**How Might We (two alternative framings):**
- **HMW bind a mention to an *id* rather than to a *string*?** → moves the problem from identity-minting to entity-linking (`mention → candidate → decision`), which is the field's shape (X2/X3).
- **HMW make the *candidate set* correct, rather than make the *decision* smarter?** → candidate recall is the dominant silent failure: if the right entity never enters the candidate table, no adjudicator can pick it (X1). This is the framing the predecessor brief already reached and it survives every decision since.

**Assumption map:**
| assumption | status |
|---|---|
| identity is the `name` | **`[invalidated]` by #3584** — the decision, though **not the code** (§3 E1/E2) |
| the graph needs a new scope key on `Object` | `[unverified]` — if the id decision lands, scope belongs in *candidate generation*, not in the key |
| entity names are the problem | `[partially validated]` — weak names are **already being addressed by the extractor upgrades** (owner, 2026-09-23); this research is **downstream**, not a parallel redesign |
| resolution is the only fix | `[invalidated]` — the first fix is **not minting** the non-referential mention (S2.2 VET, [#5005](https://github.com/daniel-ospina/tortoise/issues/5005)); resolution handles the survivors |

**Reframed problem statement:** *A knowledge-graph ingester is trying to bind each extracted mention to the right existing entity, but its candidate generation is global and context-blind and its identity key is still the name — so same-named entities conflate and one entity under two surface forms splits, which makes the “why / who / when” context around an entity wrong or absent.*

---

## 2. Method & scope

- **In scope:** what S3 RESOLVE should *resolve to*; what context should constrain it; how to measure it. The Jev-vs-prompt mechanism question is in scope (owner's 2026-09-23 note on #2730).
- **Out of scope (owner sequencing):** replacing, or designing a parallel, resolution step. Weak entity **names** are addressed by the extractor upgrades; this brief researches **what resolution should resolve to**, downstream of that.
- **Evidence-first:** every claim about a **class** of data below is preceded by real rows drawn from the live production graph, then the verdict.
- **Live graph read 2026-09-24** (hosted MCP): `Point 38,311 · Event 4,833 · Subject 1 · Object 7,867 · Document 0`.

---

## 3. Evidence — internal, shown before the verdict

### 3.1 The class under discussion — 7,859 real `:Object` rows, enumerated

Read: `tortoise_search(entity_type="object", query="*", limit=10000)` on the production graph, 2026-09-24. **It returned 7,859 unique ids across 7,859 rows (no duplicate names), against a taxonomy count of 7,867 — 99.9 % coverage.** The 8 missing are either created since the count or carry no retrievable text leg.

**20 real rows, in returned order (evidence, shown before any verdict):**

| id | objectKind | name |
|---|---|---|
| `obj-9de6e2c8008855a1006e9513d9` | `core:standard` | `M` |
| `obj-756442ff4303993cd928e4d409` | `dev:pullRequest` | `#3047` |
| `obj-25253505822c6c490a15e5209b` | `core:document` | `note` |
| `obj-cc7bcfb5ce0dae01e7e4296f09` | `dev:issue` | `#4054` |
| `obj-4fb94bea5e67fadbb241eff30d` | `core:decision` | `#3344` |
| `obj-e98ff208c011c834c67a935a69` | `core:document` | `§7.7` |
| `obj-2fa6487efcba9b62481dc78159` | `core:tool` | `REF_RE` |
| `obj-3a5d713a2d0eefdc1cd0910e9d` | `core:tool` | `_closure` |
| `obj-c72bfc2f4e90e2a503be5c8d0c` | `core:tool` | `clauses()` |
| `obj-729561d53e5b321f27f17383da` | `dev:risk` | `the # unmeasured row` |
| `obj-41c9b6bc2e1faab54fc99c2c07` | `dev:incident` | `the #1005 leaked-redislite-servers incident` |
| `obj-a7dd2b2e9912ff1360fc03cad6` | `core:document` | `the #1214 scoping draft` |
| `obj-4e215f37e744b60a173a4c69e7` | `dev:incident` | `the #1250 defect` |
| `obj-f9167849354bddbeacb581bfad` | `core:other` | `the !Content-Security-Policy detach check` |
| `obj-fdf369cdd37480892d6e79c82a` | `dev:code` | `the #1214 acceptance tests` |
| `obj-66c1a27c835ed0b2d32ccc9485` | `dev:incident` | `the #1261 merge rail` |
| `obj-ed35acf2bbb98b4ed34bf34bb5` | `core:decision` | `the #1003 HSTS stamping decision` |
| `obj-b214d0f4fd0a80d28ee958c2eb` | `dev:incident` | `the #101 incident class` |
| `obj-6bc4fba1f9f4ccd79b309eadcd` | `agent-ops:rule` | `the "these two must move together" contract` |
| `obj-f6a12dd02d6b1a526ca2b5dddb` | `core:document` | `the plan doc anchor` |

**The borderline cases are included on purpose** — `the #1250 defect` (a definite description *carrying* an identifier), `the "these two must move together" contract` (a quoted noun phrase), `M`, `note`, `§7.7`, `clauses()` (synthetic single tokens). A clean sample would hide the boundary; the boundary is the thing being judged.

### 3.2 The verdict on the class — after the rows

| measure | value | of what |
|---|---|---|
| `:Object` nodes | **7,867** (enumerated 7,859) | production graph, 2026-09-24 |
| names beginning `the ` | **4,900** | **62.3 % of Objects** |
| names that are a bare `#<n>` | **42** | 0.5 % |
| names matching `PR #<n>` / `issue #<n>` | **135** | 1.7 % |
| names containing a code token (`.py`, `` ` ``, `()`, `--`, `_`) | **708** | 9.0 % |
| duplicate names | **0** | — |

**Top `objectKind`s:** `dev:code` 1,556 · `core:tool` 1,017 · `core:other` 907 · `core:standard` 830 · `core:document` 707 · `dev:bug` 540 · `core:workflow` 299 · `agent-ops:rule` 221 · `dev:pullRequest` 205 · `dev:issue` 170.

**Verdict (evidence-first, stated only now — tier HIGH, a full enumeration of the live set):** **exactly 0 of 7,859 Objects share a name.** That is not a healthy uniqueness property — **it is the signature of a name-keyed `MERGE`.** Name-keyed merge can produce *only* unique names, so **the collision this issue worries about cannot show up as duplicate nodes.** It shows up as:

1. **CONFLATION** — two real-world things that share a surface form become **one node**. Invisible in every count we have.
2. **SPLIT** — one thing under two surface forms (formal vs short, initials vs full, a role description) becomes **two nodes**, and the `the <X>` class is exactly the population where this is most likely.

And the `the <X>` class is **mostly non-referential mentions promoted to nodes**, not entities: mainstream NLP treats a definite description as a **mention** to resolve or drop, never as an entity to mint (`STORAGE-ARCHITECTURE.md` §16.3). **4,900 of these = 62.3 % of Objects = 9.6 % of all labelled entity nodes** (4,900 / 51,012); Objects overall are **7,867 of the 51,012 labelled nodes (15.4 %)**.

### 3.3 Code evidence — the mechanisms behind the class *(each finding tagged; all locators re-read at `c4580b083`)*

| tier | # | finding | where |
|---|---|---|---|
| **HIGH** | **E1** | **Identity is still the name.** `MERGE (o:Object {name:$name})` — the MERGE key is `name`, and the id rides along as a property (`ON CREATE SET o.id=$id`, `ON MATCH SET o.id=coalesce($id,o.id)`). Same for `:Subject` (`MERGE (s:Subject {name:$name})`). | `projection/entities.py:1642`, `:2046`, `:2104` (Object); `:1573`, `:2006` (Subject) |
| **HIGH** | **E2** | **The id is a function of the name**, so it cannot disambiguate two incarnations that share a name. `_entity_name_id` = `sha256(f"{label}:{name}")[:26]` → `obj-<hex26>`. | `sdk.py:1790` (`_entity_name_id`), `:1799` (the `sha256`); `_ENTITY_ID_RE` at `:1430`; `_is_entity_id` at `:1433` |
| **HIGH** | **E3** | **`#3584` decided the opposite and is NOT implemented.** The decision doc (merged 2026-09-15) says *mint an opaque id once at creation; the name is a mutable natural key*. The projection still merges by name. **A decision/doc-vs-code divergence, not a proposal.** | `#3584`; E1/E2 |
| **HIGH** | **E4** | **The resolver prompt carries no context** — the candidate table is literally `id \| name \| kind`, and the new names are a bare list. No quote, no narrative, no roster, no scope, no source. | `extractor_v2.py:2752` (`_resolution_prompt`) |
| **HIGH** | **E5** | **Candidate lookup is kind-typed with a bare-form fallback**, and returns `ambiguous` (never guesses) when two namespaces collide on the bare form. Correct shape — but it can only see what `search_graph` gave it. | `extractor_v2.py:2717` (`_find_existing_entity`) |
| **HIGH** | **E6** | **The lookup runs after classification** — `resolve_entities` is **defined** at `:2767` and **called** at `:5178`, after the classify pass (`:4888` — the docstring states the order `S1 → S2 → classify(S2) → S3 → S4`). `search_graph` is defined at `:1965`, called at `:5060`. D9 confirmed in code. Moving it earlier degrades to the ambiguous bare-form path and **manufactures duplicates**. | `extractor_v2.py`; D9 |
| **HIGH** | **E7** | **The narrative is already upstream and already passed to S3** — S3's inputs are *surviving candidates + the narrative* (design §4.1). It is simply not forwarded into the resolution prompt (E4). | `EXTRACTOR-V4-ARCHITECTURE.md` §4.1, S3 |
| **HIGH** | **E8** | **S3 priors now carry a real `kind`** — `#4511` (CLOSED) fixed `_fts_rows` reading `kind` while the callee emits `point_kind`. Before that fix the candidate table's `kind` column was always blank on the real backend. | `#4511` |
| **HIGH** | **E9** | **Jev is already integrated in this repo, for a typed gate** — `tools/collision_preflight.py`: `JEV_MODEL = "jev-1.13.0"`, a `noul` question, calibrated thresholds (CLEAN `p < 0.50`, COLLISION `p ≥ 0.70`), batch ≤ 40, fail-closed degradation, cache keyed on prompt version. **The typed-decision seam is proven in-repo.** | `tools/collision_preflight.py:459+` |
| **MEDIUM** ⚠️ emerging | **E10** | **Entity reference is a *mention*, not a name** — the extractor doc already records that an Object's failure mode is **NON-REFERENCE** (an Object minted from a negated sentence, *"timeout is not on macOS"*), and that an **origins link is the detector**. | `EXTRACTOR-V4-ARCHITECTURE.md` §2.4.3 |
| **HIGH** | **E11** | **The connection layer is the product and is kept** — so a resolution fix may not "solve" volume by deleting entity links. The cost is solved in the storage layer. | `STORAGE-ARCHITECTURE.md` §11; `OVERRIDES:` on #4333 |

**Direction of the fix, from the evidence:** two of the three defects are *mechanical and already decided* (E1→E3 is #3584; E4 is a prompt/context change), and only the third (candidate generation scoped and recall-measured) is genuinely new design. **None of it requires replacing S3.**

---

## 4. External findings

> Confidence tiers per the research skill §5a. Sources are independent categories (academic / vendor doc / production engineering).

### X1 — Blocking is *the* mechanism that bounds ER; candidate recall is the metric that fails first **[HIGH — 3+ independent]**
ER surveys and practice papers converge: blocking assigns entities to signatures and compares only within blocks, and the correct measure of a blocking scheme is **pair completeness** (recall) first, then pair quality and reduction ratio. Practitioner post-mortems name **weak blocking, pair explosion, and transitive-closure compounding of matcher errors** as the canonical production failures.
*Sources:* Papadakis et al., *A Survey of Blocking and Filtering Techniques for ER* (arXiv 1905.06167); *Comparative Analysis of Approximate Blocking Techniques* (PVLDB 9); *Entity Resolution in Practice: Lessons from a Self-Serve Pipeline* (arXiv 2607.26298); *Benchmarking Filtering Techniques for ER* (arXiv 2202.12521).

**⇒ For us:** the candidate set is the first thing to fix and the first thing to measure. `search_graph` (E5) is our blocking step today; it has **no scope filter and a bounded `[:40]` render + ~15×3 FTS budget** (predecessor E5), so its recall is unknown and unmeasured.

### X2 — Production architectures split candidate retrieval from LLM judgment, and the LLM picks from a *supplied list* **[HIGH — 3+ independent]**
Elastic's production ER architecture separates **candidate retrieval** from **LLM judgment**, uses a small candidate set plus **constrained output** and explanations; the *SELECT* prompt family (choose the match from candidates) is a studied, mainstream pattern.
*Sources:* Elastic Search Labs, *Entity resolution & Elasticsearch: Solving challenges in production*; AvengER — *Ensembling and Fine-Tuning LLMs for SELECT Prompts in ER*; *Match, Compare, or Select? An Investigation of LLMs for Entity Matching* (arXiv 2405.16884).

**⇒ For us:** `_resolution_prompt` (E4) **already is a SELECT prompt** — it supplies a candidate table and asks for `resolves_to`. The gap is not the pattern; it is (a) the candidate set's recall and (b) the absence of the context the model needs to choose.

### X3 — False merges and false splits are *asymmetric*, and the conservative direction is fewer merges **[HIGH — 4 independent practitioner/analysis sources]**
A false merge is **invisible and hard to undo** (it propagates a wrong identity downstream); a false split is **visible and cheap to repair**. Guidance converges on setting match thresholds **conservatively** and prioritising merge precision.
*Sources:* logiciel.io *False merges vs false splits*; Eriksson *Entity Resolution* pattern library; Refonte *Govern ER Record Confidence Thresholds*; ReliableContext *ER Evaluation Beyond Pairwise F1*.

**⇒ For us:** this is the field's statement of the same rule `#1370` already carries — *"no subject > wrong subject"* — and it is why `_find_existing_entity`'s `ambiguous → None` (E5) is the right default and must not be "fixed" into a guess.

### X4 — LLM confidence in ER is usable but not trustworthy at the margin; grounded evidence beats self-reported confidence **[MEDIUM — 2 independent categories]**
Calibration work on LLM-based entity matching finds slight overconfidence (ECE ≈ 0.004–0.055); threshold sweeps put the best precision/recall balance at **0.85–0.90**, with a *review queue* for the middle band. The practical recommendation is to require a **grounded evidence clause** (verbatim quote + candidate id), not self-reported confidence.
*Sources:* arXiv 2509.19557 (confidence calibration in LLM ER); EMNLP-2025 findings — *Entity Profile Generation & Reasoning*; Zingg *Entity Resolution at Scale Part 4*.

**⇒ For us:** the Jev `Choice` should be gated on **a returned candidate id + the narrative quote that justifies it**, exactly as the predecessor brief's tier (d) says. Self-reported probability alone must not authorise an auto-link in the mid band.

### X5 — Category/uniform placeholders reset per document; only per-entity-unique pseudonyms are stable **[MEDIUM — 2 independent categories]**
The pseudonymization literature names three strategies — uniform, category-specific, and unique-per-entity — and **only the last is stable across documents**; the first two reset. Regulatory guidance distinguishes pseudonymisation (reversible, keyed) from anonymisation.
*Sources:* *Pseudonymization Strategies on Sensitive Classification Tasks* (PrivateNLP 2024); *Automated Anonymization of Parole Hearing Transcripts* (NLLP 2024); ICO / Irish DPC guidance.

**⇒ For us:** `"Produto 1"` is a **category/uniform placeholder**, so resolving it globally is **provably wrong**; the correct policy is **resolve within scope, quarantine across scope**, promoted only by an explicit declared mapping (Envelope `entityAliases`). This survives every decision since 2026-09-09.

### X6 — Typed / constrained-output decision models are a distinct pattern from free-text prompting **[MEDIUM — 2 independent categories]**
Classification-as-decision with **calibrated probabilities + explicit thresholds** is a standard shape; constrained output forces a value from a supplied set and removes a class of malformed/off-vocabulary emission.
*Sources:* scikit-learn *Tuning the Decision Threshold for Class Prediction*; TypeSafe-AI/Jev-alternatives survey (DataCamp); cost-sensitive classification literature (arXiv 2207.09196).

**⇒ For us:** Jev is this pattern. It **cannot invent a name** — the answer is a choice over the supplied candidate set, which is exactly the SELECT surface (X2). It removes a parsing failure class; it does **not** add a guarantee our closed vocabulary lacks. Its value is cost, latency, and typed routing — not accuracy.

### X7 — ⚠️ ADVERSARIAL: naive LLM adjudication degrades as the candidate set grows, and without fine-tuning/domain prompting **[MEDIUM — 2 independent categories]**
Candidate-selection accuracy falls as the candidate list grows; few-shot and domain-specific prompts materially outperform zero-shot; **fine-tuning** improves effectiveness further.
*Sources:* arXiv 2405.16884 (*Match, Compare, or Select?*); CEUR Vol-3931 paper 4 (*Entity Matching with 7B LLMs*); Pergamos/UoA (*ER with Small-Scale LLMs*).

**⇒ For us — this is the strongest argument *against* a naive "just add context to the prompt" fix.** A SELECT prompt is only as good as **the candidate list it is handed**. Supplying the narrative to a model choosing from a global, unranked top-40 will not fix same-name conflation; it will add a plausible-sounding wrong answer. **The scoped, high-recall candidate set is the load-bearing half — the adjudicator is the tail.**

### X8 — The field's biggest open KG-RAG project has exactly our defect and no shipped fix **[MEDIUM — 2 independent categories]**
Microsoft GraphRAG matches entities by **exact name and type only**; maintainers state that *"earlier experiments with name-variant resolution were not satisfactory"*.
*Sources:* GraphRAG default dataflow docs; microsoft/graphrag issue #1837; discussion #778.

**⇒ For us:** there is **no drop-in reference implementation** for scoped entity resolution (the predecessor brief's X9 still holds). We assemble it from ER primitives; expecting a product to copy is not realistic. This is a *build* item, not an *adopt* item.

---

## 5. The contradiction test — run FIRST, per finding

Each candidate practice below was tested against the recorded decisions **before** cost, quality, or convergence was weighed.

| candidate | contradicts? | verdict | why |
|---|---|---|---|
| **Scope-first identity key `(scope, kind, name)` as the entity id** (predecessor tier (a)) | ✅ **YES — `#3584`**: *"Stop deriving identity from the name… mint an opaque id once at creation."* | ⛔ **REFUSED — superseded. Not adoptable, not with a caveat.** | The predecessor brief predates #3584. Identity is the opaque id; a scoped name-key re-derives identity from the name, the exact thing #3584 ended. **The scope idea is still right — it moves to candidate generation (§6).** |
| **A new `scope` property on `:Object` in the core ontology** | ⚠️ **Partially — D10/D30 and the open Q3 on #2730** | **OWNER QUESTION**, not an adoption | `isPlaceholder`/`sameAs`/`scope` are identity primitives (upper-ontology, core) per the #2826 C4 recommendation — but the C4 verdict is `OPEN-ENGINEERING`. Not ours to take. |
| **Jev as the resolution adjudicator** | ❌ **No** — no recorded decision reaches it; the seam already exists in-repo (E9) and the extractor design already names Jev for S2.2/S2.3 | **ADOPT (scoped)** | Safe to proceed: it is a decision-only model over a supplied candidate list (X6), it replaces nothing, and the in-repo precedent is fail-closed. |
| **Enforcing the *candidate set* is scoped and recall-measured before any adjudicator is tuned** | ❌ **No** — but it constrains the fix scope against the owner's "downstream of the extractor" sequencing | **ADOPT** | This changes *how* S3 generates candidates; it does not replace S3, and writes stay fail-closed against an unmeasured recall. |
| **Replacing / forking the resolution step** | ✅ **YES — owner sequencing (2026-09-23): weak names are addressed by the extractor upgrades; this research is downstream** | ⛔ **REFUSED** | Explicitly out of scope. |
| **Embedding/similarity candidates over `:Object` (the vector leg)** | ⚠️ **CONDITIONAL — `STORAGE-ARCHITECTURE.md` §12.2c + `#4997`, and it RIDES ON V1** | **OWNER QUESTION — rides on V1, which is OPEN, not decided** | Entity vectors would index **7,859 strings that are 62.3 % low-information** (the storage doc's own measurement) — a cost on the dominant line. And all 7,859 Object embeddings **already exist but are unindexed** (`#4997`), so the mechanism is *present and paid for*. **§12.1c records V1 itself as *"NOT DECIDED — owner's call"* and §14.3 lists it under *"Open"***, so this is **not a decided question awaiting implementation and not a reopen** — it is a live owner question this research feeds. Using the vectors for candidate retrieval (a brute-forced 9.26 ms scan) is a **consequence of the V1 ruling**, not a separate adoption. |
| **Quarantine placeholders within a scope, promote only via a declared mapping** | ❌ **No** — the `OVERRIDES` on #4333 protects the *connection* layer, not the placeholder policy; X5 is externally convergent | **ADOPT** | Nothing in the record contradicts it. A `"Produto 1"` resolved globally is *provably wrong* (X5). |
| **Entity-keyed review queue, post-ingest, fail-closed writes meanwhile** | ❌ **No** | **ADOPT** | Matches `#1370` (*"no subject > wrong subject"*), `#2349` policy 3, and X3. |
| **A second constant for a similarity threshold** | ✅ **YES — an existing constant** (`tortoise.embeddings.DEFAULT_THRESHOLD = 0.72`) | ⛔ **REFUSED** | Do not invent a second constant; reuse the existing one if/when the vector leg is used. |

**Adoption gate (one line, per the template):** **adopt** the scoped-candidate + Jev-SELECT + placeholder-quarantine + entity-review-queue findings (no recorded decision reached, convergent, aligned); **owner question** for the entity-vector leg — it rides on **V1**, which `STORAGE-ARCHITECTURE.md` **§12.1c records as *"NOT DECIDED — owner's call"*** and §14.3 lists under **Open**, so it is an *open question this research feeds*, **not a decided one awaiting implementation and not a reopen**; **refuse** the scope-first *identity key* (contradicts #3584) and any replacement of the resolution step (owner sequencing).

---

## 6. Recommendation — what S3 should resolve *to*

**The one-sentence answer: resolve a *mention* to an *opaque entity id* (per `#3584`), chosen from a *scoped, kind-typed, high-recall candidate set*, using the *narrative and the source envelope* as the disambiguating context — and attach the new context to that id.**

Ordered by load-bearing weight (not by cost):

### 6.1 Identity — implement `#3584` before designing on top of it *(structural; highest weight)* **[HIGH]**
An entity's identity is the **opaque id minted at creation**; `name` is a mutable natural key in a lookup index; a rename is a journaled mutation on the stable id. **S3 then resolves *to an id*, not to a name.**

**Why this is first:** every other tier's correctness depends on it. Today `MERGE (o:Object {name})` + `sha256(label:name)` (E1/E2) means the candidate table identifies entities **by the very string that conflates two of them**. Adding context to a prompt cannot fix an identity key built from the ambiguous field.

**Evidence-honest caveat:** this is a **recorded decision whose code has not landed**. #2730's own comment record already marks C2 `SUPERSEDED` and routes the residual here — so the route is not a reopen; it is **implementation**, and this brief's job is to say nothing else should be built first without it.

### 6.2 Candidate generation — scope, kind, and *measure recall@k first* *(the dominant silent failure)* **[HIGH]**
- **Scope the candidate set** to the source/portfolio envelope where one is asserted (D10 makes the source the abstraction the entity layer is over; §6.4).
- **Keep the kind filter** (`_find_existing_entity`, E5) and its `ambiguous → None` default — do not "fix" it into a guess (X3).
- **Widen the candidate budget** beyond the current `[:40]` render + ~15×3 FTS (predecessor E5), and **measure candidate recall@k FIRST** — the right entity may never enter the table today, and no adjudicator can recover from that (X1, X7).
- **Narrative and slot context are already upstream** (E7) — forward the verbatim quote and the slot role into the resolution prompt (E4's gap), which is a prompt/contract change, not new extraction.

### 6.3 Adjudication — a Jev `Choice` SELECT over the candidate set, gated on grounded evidence **[MEDIUM]** ⚠️ emerging
- Keep the pattern that already exists (`_resolution_prompt` is a SELECT prompt; X2).
- **Split bundled questions** — Jev's own doctrine is atomic questions; *"is it new"* and *"is it well-named"* are two questions, not one.
- **Gate the auto-link on a returned candidate id + the narrative quote**, not on self-reported probability (X4).
- Because it cannot invent a name, Jev's failure mode is **"picks the wrong candidate"**, not "hallucinates an entity" — which is why §6.2's recall is where the risk lives.

### 6.4 Envelope — carry the entity *scope*, not just the meeting id **[MEDIUM]** ⚠️ emerging
The predecessor brief's list stands, and D10 gives it a cleaner home: `scope`/portfolio, `participants[{name, role, aliases[], initials}]`, `meeting_id`, `documentRole`, `startedAt`, `sourceUrl`, `language`, `graph`, plus (high-value) `entityAliases: {surfaceForm → canonicalRef}` and a per-block `scopeOverride`. **The scope must be *asserted*, not inferred from a filename** (predecessor Q2; #2826 C3 recommendation: asserted = authoritative, inferred = hypothesis). **Q2 is still an open engineering question and the recommendation is unchanged.**

### 6.5 Placeholders — resolve within scope, quarantine across scope **[MEDIUM]** ⚠️ emerging
Materialise a scope-local proxy (`isPlaceholder: true`), never auto-link it cross-scope, promote via a declared `entityAliases` mapping or a review verdict (X5). **The externally-corroborated fact is decisive: only unique-per-entity placeholders are stable across documents; category/uniform ones (`"Produto 1"`) reset.**

### 6.6 The ambiguous tail — an entity-keyed review queue, post-ingest, non-blocking **[HIGH]**
Today `tortoise_list_dedup_candidates` / `approve_merge` are **Point**-keyed (predecessor E8). The queue must be **entity**-keyed, evidence-carrying, agent-drained, never auto-resolved (`#2349` policy 3, `#2696` §2). **Writes stay fail-closed meanwhile** (`#1370`).

---

## 7. Answers to the issue's explicit questions

| question | answer | contradiction check |
|---|---|---|
| **Scoping or smarter resolution?** | **Both, but scoping is in *candidate generation*, not in the identity key** — because `#3584` removes the name from identity. Scoping prevents conflation by construction (blocking, X1); the ladder fixes the intra-scope problems scoping cannot touch. | supersedes the predecessor's tier (a) |
| **Should the Envelope carry entity scope?** | **Yes** — and it is a *prerequisite*, not an enhancement: a scope that is inferred is a hypothesis and must be marked as one. | no contradiction; Q2/Q3 stay owner questions |
| **Placeholder policy?** | **Resolve within scope, quarantine across scope**, promoted only by a declared mapping or review. | adopted (X5) |
| **Where does the review queue sit?** | After ingest; **entity-keyed**; fail-closed writes in the meantime. | adopted (`#2349`, `#1370`) |
| **What should resolution resolve *to*?** | **An opaque entity id** (`#3584`), from a scoped, kind-typed, recall-measured candidate set, adjudicated by Jev with the narrative as context. | **this is the brief's headline** |

---

## 8. Evaluation plan (falsifiable, in order)

1. **Candidate recall@k FIRST** — the dominant silent failure. If the right entity is not in the table, nothing downstream matters. Measure by hand-annotating a small sample of mentions against the candidate set the current `search_graph` emits.
2. **Precision / recall** of resolution, once recall@k is non-trivial.
3. **Cross-scope contamination = 0 (asserted).** A placeholder or a same-name entity from another portfolio must never link across scope.
4. **Unbound rate** and **misattribution rate** (the fail-closed direction: an unbound mention is a miss, not a defect of the same severity as a wrong link — X3).
5. **Per-scope fold correctness** — because the Object status fold is name-keyed today (predecessor E2), a conflation flips the other entity's status; assert the fold after the id lands.

**Fixtures:** F1 cross-scope name collision · F2 formal-vs-short alias · F3 initials ↔ full name · F4 role reference (`the CEO` → roster) · F5 placeholder reset (`Produto 1` in two portfolios) · F6 person recurrence (one node, three boards). **Test T6 = envelope-less ingest stays byte-identical.**

---

## 9. Open questions for the owner (a decision, not research)

1. **`#3584` implementation sequencing** — is the opaque-id projection change the prerequisite for the S3 work, or does it land in parallel? *(This brief's recommendation: prerequisite.)*
2. **Q2 — is a meeting's asserted `scope` authoritative (safe to auto-scope) or advisory?** (#2826 C3 — recommendation: asserted ⇒ authoritative, inferred ⇒ hypothesis. **Unchanged.**)
3. **Q3 — do `scope` / `isPlaceholder` / `sameAs` live in the core ontology or the venture pack?** (#2826 C4 — convergent answer: identity primitives are domain-neutral ⇒ core. **Needs an ONTOLOGY.md delta when it lands.**)
4. **Q4 — cross-scope entities (a joint venture): one node with several scopes, or one canonical node + membership links?** (#2826 C5 — convergent master-data pattern: canonical + membership. **Unchanged.**)
5. **Jev vs a prompt fix** — this brief's answer: **they are not alternatives.** Jev is the *adjudicator*; the narrative/slot context goes into *its* prompt. A prompt-only fix cannot repair candidate recall (X7).

---

## 10. Source confidence summary

| claim | tier | sources |
|---|---|---|
| 62.3 % of Objects carry a leading article; names are unique by construction | **HIGH** | live enumeration (§3.1–3.2), n = 7,859 |
| Identity is still name-keyed; `#3584` unimplemented | **HIGH** | code E1/E2 + the decision doc |
| The resolver prompt carries no context | **HIGH** | code E4 |
| Blocking/candidate recall is the first-order fix | **HIGH** | X1 (3+), plus X7 adversarial |
| LLM ER should pick from a supplied, bounded candidate list | **HIGH** | X2 (3+) |
| False merges are the costlier error | **HIGH** | X3 (4) |
| Placeholders reset per document unless unique-per-entity | **MEDIUM** ⚠️ emerging | X5 (2) |
| Jev's value is typed routing/cost, not accuracy | **MEDIUM** ⚠️ emerging | X6 + E9 |
| No off-the-shelf scoped-ER reference exists | **LOW** ⚠️ single-source | X8 + predecessor X9 |

**Adoption gate line:** `Adoption gate: adopt (scoped candidates · Jev SELECT · placeholder quarantine · entity review queue) | owner question (entity-vector leg — rides on V1, which §12.1c records as NOT DECIDED) | refuse (scope-first identity key — contradicts #3584; replacing S3 — owner sequencing)`.

---

*Research track. This brief proposes no code and changes no decision. Where it recommends work, it names the decision it does or does not contradict. Every class claim is preceded by real rows from the production graph.*
