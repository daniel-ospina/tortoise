# #1026 calibration set — the pack extraction slots

**What this is.** The Stage-1 **deliverable** for #1026. #1026 is a **Class A** issue (a
judgement, not a mechanical change), and the lane that owns it states the rule plainly:
*for a Class A issue, the dataset is the deliverable, not the diff.* The rows below are the
evidence the owner ruled on, with the verdict and the reason attached to each row.

**Method:** `docs/engineering/DATASET-CONSTRUCTION-LOOP.md` · **Row schema:** §3 of that doc.

## The ruling

The owner answered **A** on #1026 (comment `5846390475`, 2026-09-26 12:47 UTC), against the
Q6 options list (comment `5837251490`):

> **A — keep the slot, as declared entity types only.** The pack declares which kinds its
> artefacts are; no name patterns; nothing is dropped at mint.

⚠️ **One note a successor needs.** #1026 carries **two option lists that both use the letters
A/B/C** — Q5 (posted in the boundary-set comment `5837200759`, about the *drop-rule shape*) and
Q6 (about re-scoping the issue). The bare answer `A` therefore keys to Q6 by position, but Q5's
`A` is *"no drop at step 4; keep `excludePatterns` out of v3.1"* — the **same substance**. The
two readings do not diverge on any action, which is why this set records the ruling as settled
rather than bouncing it back.

## Rule versioning

Every row's `source` carries the rule version that labelled it: **`v3.1-declarative-only`**.
The rule version this replaces (`v3-proposed-excludePatterns`) is recorded in the `output` field
of each row, because Stage 2 diffs against exactly the rows the owner reviewed.

Per §4 of the loop doc: when the rule changes, earlier rows are **re-labelled or explicitly
marked stale — never silently blended.** If the declarative surface later gains a drop mechanism,
this file must be superseded, not appended to.

## Bands

`band` makes *"we included the borderline cases"* auditable — you can see what the set lacks.

| band | rows | what it covers |
|---|---:|---|
| `spine` | 12 | The **twelve most-referenced Objects in the graph** — the rows that decide whether a drop rule is safe |
| `low-ref-must-keep` | 6 | The stage-1 must-keeps, **all at refs=1**, inside the class a count-based refinement would delete |
| `boundary-indistinguishable` | 2 | A **paired** borderline pair drawn from the middle of the class (`SKIP 900 LIMIT 30`, no head bias) — same shape, same count, opposite disposition |
| `incidental-pointer` | 2 | `#`-bearing rows that contradict the premise that refs are ephemeral |
| `failure-class` | 3 | The three failures the issue was opened on, each with the proposed fix and its verdict |

## Labels (closed vocabulary)

| label | meaning |
|---|---|
| `keep-object` | The ruling forbids dropping this row. **22 of 25 rows** carry it |
| `drop-fix` | The issue's proposed fix for this failure class is **dropped** |
| `keep-slot-declarative` | The slot survives — **declarative form only** (declared entity types, no patterns, nothing dropped at mint) |

`"looks good"` is not in the vocabulary, and is not a label. Every row carries an explicit label
**and** a reason, so a successor can dispute the *rule* rather than the *row*.

## The headline finding these rows encode

The pattern class a drop rule would target is **6,533 Objects — 83% of the graph — of which
3,517 are load-bearing (53.8%)**. **11 of the 12 most-referenced Objects match a drop pattern.**
The rule is evaluated at **mint** time, when resolution always fails, so it collapses to *drop* —
and **every one of those 2,570 load-bearing `the X` Objects was a first mention once.** The hubs
exist because the rule was absent. That is why the ruling rejects the **mechanism** rather than
tuning it: no threshold on ref count separates `the T2 guard` from `the T4-class write→rename
race`.

## Scope this set does **not** claim

- **Not** a claim about extraction quality in general — only about the three named failure classes.
- **Not** a licence to change a cap, a price, or a recorded decision.
- The quantity/COGS lever (Q5's `A`, and Q6's `B`) is **out of scope here by the ruling** and is
  filed separately, so this file does not become a rewrite of a cost question.
