---
title: "How we build a dataset with the owner in the loop"
type: engineering
domain: engineering
doc_status: draft
created: 2026-09-24
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: eval dataset construction, owner-in-the-loop review, integration surface map
---

# How we build a dataset with the owner in the loop

**What this document is.** The repeatable method for turning *prose rules* ("a document is a `:Source`") into *checkable rows* that a machine can be held to. It formalises two owner rulings that were already in force, adds the mechanics external practice supplies, and defines the artefact each round produces.

**Why it exists.** Most of what this system does is a **judgement** — should this claim have been extracted, is this entity an entity, is this a source. No unit test can assert a judgement. So the definition of correct behaviour currently lives only in prose, and **prose cannot be executed, regression-tested, or diffed.** This loop is how the definition becomes executable. **The dataset is the specification.**

**Grounds.** Owner rulings METHODOLOGY 1 + METHODOLOGY 2 (recorded on #4899, #2714 and elsewhere); `docs/research/2026-09-24-testing-and-dataset-construction/research-brief.md`; the precedent schema in `tests/fixtures/labeled_pairs.jsonl`.

---

## 1. The two rulings this formalises (unchanged, verbatim in force)

### METHODOLOGY 1 — evidence first, verdict second
Any claim about a **class** of data must be preceded by **10–20 real instances from the actual artefact**, shown *before* the verdict. Where a line is being drawn, **the borderline cases must be in the sample — a clean sample hides the boundary by construction.** Claims about a future rule must show the rows it **would keep** and the rows it **would drop**.

### METHODOLOGY 2 — small sample first, then build
**Stage 1:** take a small sample, apply the proposed rule **by hand**, show the owner the **input rows and the output rows — including the ones that behaved unexpectedly**. The owner corrects the rule here, where a correction costs a sentence. **Nothing is implemented before the owner has reviewed real output.**
**Stage 2:** implement it, re-run, and confirm the output matches the sample the owner reviewed. A Stage-1/Stage-2 mismatch is an **implementation defect**, not grounds to reopen the rule.

> ⚠️ **Sample size is the owner's to choose, and it is "tens, not thousands".** External practice suggests a starting count (~30 traces); **we do not adopt the number.** The owner's instruction was the *principle* — **stop when the sample stops surprising you** — not a fixed size.

---

## 2. The loop

```
┌─ ROUND N ──────────────────────────────────────────────────────────────┐
│  1  SAMPLE   draw rows from the REAL artefact, deliberately across      │
│              bands — the ordinary cases AND the borderline ones           │
│  2  APPLY    apply the rule BY HAND  (Stage 1 — not implemented)         │
│  3  SHOW     input rows + output rows + every unexpected row             │
│  4  ASK      the owner rules on each; the REASON is captured             │
│  5  LOG      append {input, output, label, reason, band, source}          │
│  6  CODIFY   the reasons become the failure taxonomy; the taxonomy        │
│              CHOOSES the next round's sample                              │
│  7  DRIFT    bump the dataset version; record which RULE version          │
│              produced these labels                                        │
└───────────────────────┬────────────────────────────────────────────────┘
                        │  repeat while a band is still surprising
                        ▼
              SATURATED → Stage 2: build it, re-run,
              diff against the exact rows the owner reviewed
```

**Six of the seven steps are the owner's rulings. The external work supplies the mechanics:** the two-coding-stage taxonomy (step 6), the captured **reason** (step 4), and the drift guard (step 7).

---

## 3. The dataset row

```json
{
  "input":  "<the real row, as it exists in the artefact>",
  "output": "<what the rule produced — proposed, pre-label>",
  "label":  "<the owner's verdict>",
  "reason": "<WHY — so a later reader can disagree with the rule, not the row>",
  "band":   "<coverage category>",
  "source": "<provenance: issue, round, artefact, and the RULE VERSION>"
}
```

| field | why it is there |
|---|---|
| `input` / `output` | the pair the owner actually reviewed — Stage-2 diffs against exactly this |
| `label` | the verdict. **"Looks good to me" is not a label** |
| `reason` | a bare label cannot be re-audited six months later; the reason lets a successor dispute the *rule* instead of the *row* |
| `band` | makes "we included the borderline cases" **auditable** — you can see what the dataset lacks |
| `source` | provenance **and the rule version** — without it, labels from different rounds are not comparable |

This is the existing `labeled_pairs.jsonl` shape (`{content_a, content_b, label, band, source}`) with the two additions practice demands: the **reason**, and **the rule version inside `source`**.

---

## 4. The guard that bites *this* loop: annotation drift

The owner's flow is *repeat 1–3 more times*, and each repetition is a chance for the rule to move underneath the labels — after which round-1 and round-3 labels mean different things and any score computed across them is meaningless.

**Mechanisms, in order of preference:**
1. **The rule is versioned** and every row records which version labelled it.
2. **When the rule changes, earlier rows are re-labelled or explicitly marked stale** — never silently blended.
3. **Scores are reported per band, never as one blended number.** A single percentage over 20 rows across four bands is a number that measures nothing.

---

## 5. What makes a round *good* (the sample's obligations)

- **Drawn from the real artefact** — not invented. Real rows carry the shapes you did not imagine.
- **Spans the bands**, including at least one **unexpected** row. A round with no surprise is evidence the sample was too narrow.
- **Shows the rows the rule would drop**, not only the ones it keeps.
- **Small enough to read.** If the owner cannot read every row, the round is too big.
- **Sealed set from round 1.** Some rows are held back and never tuned against, or the loop degenerates into teaching to the test.

---

## 6. Known failure modes and their guards

| failure | guard |
|---|---|
| **Teaching to the test** — tuned until our 20 rows pass | a sealed held-out set, never tuned against |
| **Contamination** — eval rows came from the session that wrote the rule | record provenance per row; keep the sealed set unseen |
| **Small-sample bias** — 15 rows give a confident, meaningless number | span the bands; report per band |
| **Annotation drift** — the rule moves, labels stop being comparable | §4 |
| **The "vibe check"** — "looks good" becomes acceptance | every row carries an explicit label; a reason is required |
| **Measuring nothing** — a high score while users still fail | the sample comes from real traces; every recurring failure gets a row |

---

## 7. What this loop is *not*

- **Not a substitute for integration tests.** Replay, quota and resolver behaviour are deterministic and belong in the integration suite (`test-design`). This loop covers the layer those tests cannot reach: **whether the rule is right**.
- **Not a one-off.** The dataset is a living artefact; each epic that draws a line adds its band.
- **Not a metric exercise.** Per the owner's ruling (2026-09-24), findings are **not** converted into OKR-style targets and gates.

## 8. Where the artefacts live

| artefact | home |
|---|---|
| this method | `docs/engineering/DATASET-CONSTRUCTION-LOOP.md` |
| the research behind it | `docs/research/2026-09-24-testing-and-dataset-construction/research-brief.md` |
| a dataset | `tests/fixtures/<name>.jsonl` (existing convention) |
| an epic's surface map | `docs/epics/<date>-<issue>-<slug>/01-test-design.md` |
