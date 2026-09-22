# #2552 — owner decisions ledger

Decision index for the open owner decisions attached to **#2552**
(`fix(write-path): operator edges are not wired or persisted — measured 0/4 on the planted-operator
corpus`).

The authoritative decision site is the issue:
<https://github.com/daniel-ospina/tortoise/issues/2552>. This file is the ledger index.

**Status: open — awaiting an owner ruling. No `OVERRIDES:` marker is recorded yet**, because the
standing rule attaches a marker to a **decision**, and there is not one to mark. *(An earlier revision
of this file attached `OVERRIDES:` lines to recommendations as though they were rulings. Withdrawn —
a marker written before the ruling is a claim, not a record.)*

**Correction of record:** an earlier revision of this ledger asserted a "standing finding that
dogfooding is not a test substrate" and used it to rule an option out. **No such rule exists** — the
owner's actual position is that dogfooding *can* be a substrate when the case warrants it, is not an
ideal default, and is judged case by case. Nothing here rests on that claim.

---

## Q1 — should the extraction be measured against the new operator gold with a real LLM?

**What exists.** `tests/eval/write_path/` replays recorded sessions through the extraction path. The
real-LLM lane is real and has run: `runner run --session wp06_quarry_rollout --session
wp07_bluepeak_followup`, `extraction_mode=llm:deepseek-direct`, wp06 `extracted=11`, wp07
`extracted=14`.

**What is measured today.** The recall fix's live arms (wp06 + wp07) went **1/4 before → 1/4 after** on
those two sessions, and the recall fix itself was measured on the **real code path with no LLM** — i.e.
the operators were supplied and the question was whether they are stored. The corpus has since grown to
**15 planted operator edges across all seven sessions** (SUPPORTS 4 / MITIGATES 8 / NEGATE 2 /
SUPERSEDE 1), up from 4 in two sessions.

**What is not measured.** A **product-lane number** — a real model reading real sessions and producing
operators — over a denominator that can carry a signal. Before the gold grew, the graded result swung
`0, 1, 1, 1, 2 / 4` on identical code, so no behavioural claim could be separated from model variance.

**Cost.** The runner reports `cost_usd: 0.0` alongside a "mock/unknown adapter" note — the LLM lane is
confirmed real, so that zero is an **accounting gap, not a measurement of the cost**. Nobody can quote
the spend honestly today; making the cost visible is itself part of the work.

**Options.**
- **(a)** Run the real-LLM lane across all seven sessions against the 15-edge gold.
- **(b)** Run it on the sessions that carry the planted operators only — cheaper, partial denominator.
- **(c)** Don't run it; ship on the code-path evidence plus dogfooding captures.
- **(d)** Use dogfooding captures as the measurement substrate for now, and revisit later.

**Note on (c)/(d):** the owner's position is that dogfooding **can** be a substrate if needed, that it
is not an ideal default, and that it should be judged case by case. So (d) is a legitimate candidate,
not a contradiction — and if it is chosen, saying *why this case* is the thing that keeps it a decision
rather than a habit.

---

## Q2 — what does `MITIGATES` mean, and which shape is canonical?

**The problem: one name, two shapes, and only one of them documented.**

1. **Documented — a caveat attached to a relationship.** `mitigated_by`
   (`docs/ONTOLOGY.md:435-456`, §3.9): `(op:Point {is_operator:true})-[:mitigated_by]->(m:Point)`, with
   `mitigation_strength` in `[0.10, 0.50]` dampening the relationship's weight
   (`w_eff = w * (1 - strength)`). Backed by a **recorded product decision** (#2315, pinned
   2026-09-07, *"mitigation is a GRADED DAMPENER, not a refutation"*) and a hard rule that the edge may
   originate **only** from an `is_operator:true` Point.
2. **Undocumented — a plain edge between two points.** `sdk.create_operator` accepts
   `op_type="MITIGATES"` in the same allowlist as `IMPL`/`NAND` (`tortoise/sdk.py:6705`), i.e. as a
   generic Point → Point operator edge. The ontology's operator table (`:295-338`) defines `IMPL`,
   `NAND`, `hasPart`, `CORRECTS`, `TAGGED` — and contains **no `MITIGATES` row**.

**How they meet.** The extraction contract's F1 instruction (`tortoise/extractor_v2.py:281`) asks the
model for `{"op_type":"MITIGATES", "target_edge":{…}, "strength":…}` — the point-to-point payload shape
used to express meaning 1, with `target_edge` naming the relationship to dampen.

**Options.**
- **(a)** Reserve `MITIGATES` for meaning 1 (caveat-on-a-relationship); stop the generic writer
  accepting it as a bare point-to-point edge and route those writes through `mitigate_operator`; add
  the missing row to the ontology's operator table so the documented vocabulary matches what the code
  accepts.
- **(b)** Treat both shapes as legitimate and document both — cheapest, but the ontology table still
  gains a row, and every reader must handle two spellings of one name.
- **(c)** Rename one of them so the collision disappears.

---

## Not a decision — F2

An earlier revision of this ledger listed "a SUPERSEDE expressed as a point-level `CORRECTS`" as a
second question. **It is not one.** `CORRECTS` is already defined at `docs/ONTOLOGY.md:298` with its
supersession/invalidation semantics (§4.7), and the extraction contract's point-level supersede folds
into exactly that. Withdrawn.
