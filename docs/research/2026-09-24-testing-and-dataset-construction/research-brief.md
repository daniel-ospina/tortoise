---
title: "Testing the substrate, and building its eval datasets bottom-up"
type: research
domain: engineering
doc_status: draft
created: 2026-09-24
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: eval dataset construction, error analysis, owner-in-the-loop labeling
---

# Testing the substrate, and building its eval datasets bottom-up

**Question (owner, 2026-09-24):** *"do ample research on testing and now that likely we'll need to do a good amount of bottom up dataset construction i.e. build it, run a sample, show me input+output and ask for feedback, repeat 1-3 more times while logging the feedback as a dataset"*

**Scope:** how to test a semantic pipeline (the extractor + the storage substrate) whose *correctness is a judgement*, and how to construct the labelled datasets that make that judgement checkable — from real rows, with the owner in the loop.

---

## Step 0 — Problem reframing

**As asked:** "research testing, and set up a bottom-up dataset construction loop."

**Reframed:** the substrate is a **judgement system**, not a deterministic one. A test can assert that a Cypher query returns; it cannot assert that the extraction *should* have kept a given claim. So the thing under test is not the code — it is **our definition of what belongs in the graph**. That definition currently lives in prose (`ONTOLOGY.md`, the architecture docs) and prose cannot be executed.

**⇒ The dataset IS the specification.** Each owner-labelled row converts one sentence of prose into one checkable instance. The loop the owner describes is not "QA" — it is **how the specification gets built**.

**Assumption map:**
- `[validated]` Prose rules are unverifiable without real rows — established by the owner's own METHODOLOGY 1 (below).
- `[validated]` The corpus contains real instances of every boundary we care about — the 25k-node graph is the source.
- `[unverified]` That a small sample **saturates** (stops surprising us). This is the hypothesis the loop tests. Stated methods give ~30 traces as a starting point; see the contradiction note in the adoption gate.
- `[unverified]` That owner labels are **stable over rounds** (no annotation drift). This is a named failure mode (below) and the reason the dataset needs a schema, not just a file.

---

## What we already have internally — and it is more than a starting point

The two methodologies below are **recorded owner rulings**, not preferences. They were posted to the issues (e.g. `#4899`, `#2714`) in September 2026 and they predate this research. The external findings do not replace them — they *supply the missing mechanics*.

### METHODOLOGY 1 — EVIDENCE-FIRST APPROVAL (owner ruling, 2026-09-23)

> Any claim about a **class** of data — what gets extracted, classified, kept, embedded, or stored — must be accompanied by **10–20 real instances drawn from the actual artifact**, shown **before** the summary verdict.
>
> - **Never "these are all X".** Show the rows, then give the verdict. The owner must be able to check the judgement, not take it.
> - **Where a boundary is being drawn, include the borderline cases** — the boundary is the thing being approved, and **a clean sample hides it by construction**.
> - **Claims about future behaviour need the same treatment**: show the candidate rows the new rule *would* keep and the rows it *would* drop.
> - The owner's ruling on any boundary is **recorded on the issue** as the decision, so it survives the session that produced it.

### METHODOLOGY 2 — SMALL SAMPLE FIRST, THEN BUILD (owner instruction, 2026-09-23)

> **Stage 1 — run it small and by hand, with the owner in the loop.** Take a small sample, apply the proposed rule **manually**, and **show the owner the input rows and the resulting output rows** for review — **including the rows that went the way we did not expect**. The owner corrects the rule *here*, while a correction costs a sentence. **Nothing is implemented until the owner has reviewed real output.**
>
> **Stage 2 — implement it in the system, then test again.** Build it, re-run it, and confirm the result matches the sample the owner reviewed. **A mismatch between stage 1 and stage 2 is an implementation defect — it is not grounds to reopen the rule.**
>
> **Sample size.** Small, and chosen by the owner: **tens, not thousands.** The review needs enough rows to expose the boundary cases, not statistical confidence.
>
> **Not the owner's job to pick the number.** The instruction was the *principle*, not a count. **Do not turn this into a fixed sample size.**

### The precedent dataset — the format already exists in the repo

`tests/fixtures/labeled_pairs.jsonl` (**148 rows**) is already shaped the way this loop needs:

```json
{"content_a": "Deployments must be automated for reliability",
 "content_b": "Automating deployments is required for reliability",
 "label": "NEAR_DUPLICATE", "band": "near-dup", "source": "anchor"}
```

| field | role |
|---|---|
| the content fields | **the input** |
| `label` | **the owner's verdict** |
| `band` | **the coverage category** — so the dataset can be audited for what it *doesn't* contain |
| `source` | **provenance** — where the row came from |

`tests/fixtures/kinds_gold.mini.jsonl` is the same shape for kind classification — **16 data rows** (the file's other 26 lines are `#` comments, not records). **So this is an extension of an existing convention, not a new one** — and `band` is the field that makes METHODOLOGY 1's "include the borderline cases" *auditable* rather than a promise.

**Existing infrastructure this can attach to:** `tests/eval/` (harness, metrics, retrieval, write_path, why_suite) and `battery/` (arms, config, differential, exposure).

---

## External findings

### 1. Error analysis is the method, and it is two-stage

The dominant practitioner methodology for LLM/agent systems is **error analysis on real traces**, and it has a specific shape:

1. **Collect real traces and log them first.** Build the dataset *from* production data, not from imagination.
2. **Open coding** — read traces and write freeform notes on what went wrong, row by row.
3. **Axial coding** — group those notes into a **failure taxonomy**.
4. **The taxonomy is updated as you annotate**, not fixed up front.
5. **Iterate toward saturation** — methods name *at least 30 traces* as a starting point.
6. **Active learning** selects the next examples worth labelling.

*Sources: Hamel Husain (field guide · evals FAQ · why-error-analysis-is-so-important), Arize ("evals fail before the evaluation begins"), Humanloop, Evidently AI.*

> **Why this matters here:** our owner's loop already **is** error analysis — but the two coding stages are what turn it from a review thread into a dataset. **Open coding is the owner's row-by-row verdict; axial coding is the `band` field.**

### 2. Golden vs silver datasets — and a calibration set across the spectrum

Three complementary ways to build labelled eval data, and one quality control that matters more than the choice:

- **Golden** — human-annotated, small, high-trust. **This is what the owner's loop produces.**
- **Silver** — synthetically generated, large, lower-trust. Useful for volume, never for the final verdict.
- **Calibration set** — human-labelled examples **spanning the quality spectrum**, used to check that an automated evaluator agrees with the human. *(freeCodeCamp; Braintrust; arXiv 2506.13023)*

> **Why this matters here:** a calibration set across the spectrum is the same idea as METHODOLOGY 1's "include the borderline cases". **Independent convergence on the same rule from a different direction** — which raises our confidence in it.

### 3. The human review loop, mechanised

The recurring description of the loop is: *reviewer picks an interesting trace → copies it into a dataset → fills in the **expected** value → so review becomes labelled data.* (Braintrust.) Evidently frames the same thing bottom-up: *collect real outputs → review manually → **label good/bad with reasoning** → convert recurring issues into test cases.*

> **"With reasoning"** is the part worth copying. A bare label cannot be re-audited; a label plus the reason can, and it is what lets a later reviewer disagree with the *rule* rather than the row.

### 4. Test layers for this kind of system

From `test-design` (already the governing skill here), adapted to a semantic pipeline:

| our surface | correct layer | why |
|---|---|---|
| the **rule** (what belongs in the graph) | **the labelled dataset** | the only artefact a semantic judgement can be checked against |
| replay / durability (`derived = replay(journal)`) | **integration** — replay a journal, diff the graph | mock cannot verify a replay |
| Cypher / quota / projection writes | **integration against a real FalkorDB** | mocks hide the schema, the cap and the resolver |
| the resolver (`stub_key`, `STRUCTURAL_REL_LABELS`) | **round-trip**: build → rebuild → diff | identity bugs are silent by construction |
| end-to-end capability | **E2E / capstone** (per `epic-workflow`) | the epic's clickthrough gate |

⚠️ **The "Testing Trophy" argument applies with unusual force here:** most of our complexity lives at **boundaries** (resolver, quota, replay), not in pure functions — so the integration layer should be the largest, and unit tests of the pure parts should be the smallest.

---

## ⚠️ Adversarial — how this loop fails, and the guard for each

Converged across multiple independent sources. Each row is a **named failure mode** with the mechanical guard:

| failure mode | what it looks like here | the guard |
|---|---|---|
| **Overfitting to the eval set** | we tune the extractor until it passes *our* 40 rows, then it fails on users | hold out a **sealed** set; never tune against the held-out rows |
| **Contamination** | the model saw our eval rows (or they came from the same session that wrote the rule) | version the dataset; record provenance per row; keep the sealed set **unseen** |
| **Small-sample bias** | ~15 rows give confident but meaningless scores | **the sample must span the bands**; report per-band, never a single blended number |
| **⭐ Annotation drift** | the owner's rule *evolves* across rounds, so round-3 labels and round-1 labels mean different things and the scores stop being comparable | **version the rule and the dataset together**; re-label earlier rows when the rule changes, or mark them stale |
| **The "vibe check" trap** | "looks good to me" becomes the acceptance criterion | every row carries an explicit `label`; "looks good" is not a label |
| **Evals that measure nothing** | a high score while real users still fail (generic metric, too-easy rows) | the dataset must be built from **real traces**, and every recurring failure gets a row |

**⇒ Annotation drift is the one that bites *this* loop specifically**, because the owner's stated flow is *repeat 1-3 more times* — and each repetition is an opportunity for the rule to move underneath the labels.

---

## Adoption gate (contradiction test FIRST)

Per `research` §5.6 and `AGENTS.md`, before adopting anything convergent:

| finding | recorded decision it might touch | verdict |
|---|---|---|
| **error analysis: open coding → axial coding → failure taxonomy** | none | ✅ **ADOPT** — it is the *mechanics* of the loop the owner already asked for |
| **golden + silver + calibration set across the spectrum** | METHODOLOGY 1 ("include the borderline cases") | ✅ **ADOPT** — **convergent, not contradictory**: the calibration set is the same rule from an independent direction |
| **label with reasoning** | METHODOLOGY 1 ("the owner's ruling is recorded as the decision") | ✅ **ADOPT** — a reason is what makes a ruling re-auditable |
| **version the dataset & rule together (annotation-drift guard)** | none | ✅ **ADOPT** — no decision mandates unversioned labels |
| **integration tests > unit tests for boundary-heavy systems** | none; already the `test-design` rule | ✅ **ADOPT** |
| **⛔ "start with at least 30 traces" as a sample size** | **METHODOLOGY 2, explicitly:** *"Sample size. Small, and chosen by the owner: tens, not thousands… **Not the owner's job to pick the number.** The instruction was the principle, not a count. **Do not turn this into a fixed sample size.**"* | ⛔ **NOT ADOPTED AS A NUMBER.** A recorded owner instruction forbids fixing the count. **This is a genuine contradiction** — the external method and the owner's ruling disagree on the numeric form. **The principle survives** (iterate until the sample stops surprising us); **the count does not become a rule.** Per the protocol this is a **reopen-or-refuse**, and the honest route is to **refuse the number and keep the owner's principle** — the owner's instruction is newer, explicit, and about this exact question |

**Adoption gate: adopt 5 · refuse 1 (as a number) — no recorded decision contradicted by the five adoptions; the sixth contradicts METHODOLOGY 2 and was refused on the number only, keeping its principle.**

> **Note on process:** the "30 traces" finding is *accurate and valuable* — it is the evidence for why iterating matters. The refusal tests its **authority as a rule**, not its accuracy. It is recorded here so the next reader does not re-derive it and quietly turn it into a fixed 30.

---

## Synthesis — the loop, with the mechanics filled in

The owner's loop, extended only where the external findings add a mechanic that the methodology did not already state:

```
┌─ ROUND N ─────────────────────────────────────────────────────────────┐
│ 1. SAMPLE      draw N rows from the REAL artifact (the graph / the    │
│                corpus) — deliberately across bands, incl. borderline  │
│ 2. APPLY       apply the rule BY HAND (stage 1 — not implemented)     │
│ 3. SHOW        input rows + output rows + the unexpected ones         │
│ 4. ASK         the owner rules on each; the reason is captured        │
│ 5. LOG         append labelled rows to the dataset (label/band/source)│
│ 6. CODIFY      open-coded reasons → axial codes → the failure         │
│                taxonomy; the taxonomy UPDATES the next sample         │
│ 7. DRIFT GUARD bump the dataset version + record which rule version   │
│                produced the labels                                    │
└──────────────────────┬────────────────────────────────────────────────┘
                       │  repeat while a band is still surprising
                       ▼  (owner: 1–3 more times — by surprise, not by count)
              ┌──────────────────────────────┐
              │ SATURATED → Stage 2: build    │
              │ it, re-run, diff against the  │
              │ rows the owner reviewed       │
              └──────────────────────────────┘
```

**What is new here (external) versus what was already ours (internal):**

| step | source |
|---|---|
| sample · apply by hand · show input+output · owner rules · Stage 2 diff | **ours** (METHODOLOGY 1 + 2) |
| `band` field for auditable coverage | **ours** (`labeled_pairs.jsonl` precedent) |
| **open → axial coding; a failure taxonomy that feeds the next sample** | **external** (Husain et al.) |
| **the reason is captured with the label** | **external** (Evidently) |
| **dataset + rule versioned together (the drift guard)** | **external** (the adversarial findings) |
| **stop on saturation, not on a count** | **ours**, reaffirmed against the external "30" |

**⇒ The loop is the owner's, unchanged. The external work adds the four bolded mechanics and one explicit refusal.**

## Source Confidence Summary

| claim | tier | sources |
|---|---|---|
| error analysis (open→axial→taxonomy) is the dominant method for LLM-system evals | **High** | 4+ independent (Husain, Arize, Humanloop, Evidently) |
| golden + silver + calibration-set framing | **High** | 3 independent (arXiv 2506.13023, freeCodeCamp, Braintrust) |
| human review → expected value → dataset | **High** | 3 independent (Braintrust, Evidently, Arize) |
| the adversarial failure list (overfit / contamination / small-sample / drift / vibe-check) | **High** | 5+ independent, mutually corroborating |
| integration > unit for boundary-heavy systems | **High** | `test-design` skill + Fowler + Testing Trophy |
| "start with ≥30 traces" | **Medium** ⚠️ | Husain's guidance — **and refused as a rule** (contradicts METHODOLOGY 2) |
| two-stage review + defect-rate checks on annotations | **Medium** ⚠️ emerging | eval.qa (single practitioner source) |

## Open questions for the owner

1. **Which artifact does round 1 sample from** — the live hosted graph (`org_3326a01e…`), the corpus, or a hand-built list? *(Recommend: the live graph — it is where the 62.3% `the <X>` noise actually lives, and it is the artifact the claims are about.)*
2. **Does the sealed/held-out set exist from round 1** — i.e. do we hold back rows the extractor is never tuned against? *(Recommend: yes, even at this size; it is the only guard against teaching to the test.)*
