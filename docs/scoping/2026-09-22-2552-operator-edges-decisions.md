# #2552 — operator-edges decisions ledger

Decision index for the two owner decisions attached to **#2552**
(`fix(write-path): operator edges are not wired or persisted — measured 0/4 on the planted-operator
corpus`).

The authoritative decision site is the issue comment:
<https://github.com/daniel-ospina/tortoise/issues/2552#issuecomment-5783821438>.
This file is the ledger index — per the standing rule, a marker that lives only in the ledger is
invisible to the lane holding the vendor page, so **both carry the same `OVERRIDES:` line**.

Status: **awaiting owner ruling.** Nothing below is settled; the `OVERRIDES:` lines are written now so
that the ruling, when made, is already recorded against the common default rather than reading
afterwards like an accident of history.

---

## D1 — is the real-LLM measurement spend authorised?

**Question.** The recall fix landed (#4651 → `b8ded3646`) and the measurement-power prerequisite is
discharged — the operator gold now carries **15 planted edges across all seven sessions**
(SUPPORTS 4 / MITIGATES 8 / NEGATE 2 / SUPERSEDE 1), up from 4 in two sessions. Before it, the graded
result swung **0, 1, 1, 1, 2 / 4 on identical code**. The real-LLM lane has still never been measured
on a denominator that can carry a signal, and **nothing has been spent**.

**Options.** (a) authorise the real-LLM measurement against the 15-edge gold; (b) accept dogfooding
captures as the substrate; (c) defer and record the claim as unmeasured.

**Contradiction test — run first, and it settles (b).** There is a standing finding that **dogfooding
is not a test substrate**: our captures are one harness (`harness='pi'`) on our own tenant and cannot
carry a product-level claim. So (b) is **not a candidate for adoption at all** — not "adopt with a
caveat". Reopening that finding is the route if it is to change, never adopting around it.

**Recommendation: (a).** With the prerequisite discharged this is a cost question, not a validity one.
(c)'s residual risk is that the beta's core claim ("it remembers this **for you**") ships with no
product-lane measurement behind it — the exact outcome #2552 exists to prevent.

> **OVERRIDES:** the common practice of using your own product's usage as its test data ("dogfooding as
> the measurement substrate") — our captures are one harness on our own tenant, so they cannot carry a
> product-level claim; the measurement must run on a planted gold against the real-LLM path.

---

## D2 — which MITIGATES / SUPERSEDE form is canonical?

**Question.** The write path accepts two forms at once:

- **F1** — two MITIGATES forms for an operator edge expressing a mitigation.
- **F2** — a SUPERSEDE expressed as a point-level `CORRECTS`, where the operator vocabulary would say
  `SUPERSEDE`.

**Options.** (a) name one canonical form per construct and map the other at the boundary
(accept-and-normalise, reject only the unmappable); (b) accept both and document the equivalence;
(c) reject the non-canonical form loudly at write time.

**Contradiction test — run first.** The two documents that could govern it **predate the mint** (E7
landed `56d411450`, #1539, 2026-08-22; the mint landed in PR #4651, 2026-09-22) and **neither names
operator endpoints**, so no recorded rule settles it — which is what makes it a decision rather than
conformance. ⚠️ **Before adopting (a), check `docs/ONTOLOGY.md`**: if it already names a canonical form,
it governs and this is not a decision at all.

**Recommendation: (a).** (b) is the industry default and cheapest, but its cost is permanent — two
spellings of one intent live in the graph, so every downstream reader handles both forever. (c) is the
cleanest invariant but breaks existing callers this late.

> **OVERRIDES:** Postel's robustness principle on the write boundary ("be liberal in what you accept",
> i.e. accept both the alternate MITIGATES form and point-level `CORRECTS` for supersession) — because
> two accepted spellings of one intent push the ambiguity into every downstream reader permanently,
> where normalising at the boundary resolves it once.
