---
title: "Epic align — #5087 Extractor v4, the selective write path"
type: engineering
domain: platform
doc_status: final
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#5087, #7949, EXTRACTOR-V4-ARCHITECTURE.md"
---

# 01 · Align — #5087 Extractor v4

## Strategy Alignment Decision

**Feature:** epic #5087 — the extractor decides what deserves to be in the graph *before* writing it (S0→S6), so the graph holds a map of the business instead of a flat catalogue of Objects.

**Two dated facts, kept apart because they were measured on different populations:**

- **5,314 Objects** as of **2026-11-04**, hosted `live-v4` — a **lane-brief reading with no committed artifact**.
- **62.3% of Objects are `^(the )<X>`, measured as 4,900 / 7,859 Objects**, enumerated **2026-09-24** (re-derived 4,901 / 7,870 in `PRE-REGISTRATION.md` and in `#5333`'s census).

⚠️ **They are not composable.** 62.3% of 5,314 would be ~3,311, not 4,900. The share has **not** been re-enumerated on today's population, and this document does not multiply one by the other. `#10995` is the issue that re-derives it per door.

**Decision: PROCEED — with one sequencing correction (see Alternative 3).**

### Alternatives considered

1. **Re-ingest / rebuild the graph from raw (the architecture doc's "Option D").** *Rejected.* It repairs the corpus without repairing the writer, so the defect returns with the next capture, and it destroys the only before-arm the capstone can pair against. The store is not rebuilt to fix this class.
2. **Retire the volume bar (E1) and close the epic on E2–E5.** *Rejected — and it was right to test, because it is what I first proposed.* Research (2026-11-05, #5087 comment 6648010176) found a **mechanism that can move the row** — a mention is not an entity, which is the field's converged treatment (entity-linking NIL clustering; Graphiti's `NEW_ENTITY`; FalkorDB's own GraphRAG-SDK generic-reference stoplist; Microsoft GraphRAG's named-entity prompt) — and it acts **before** the node exists, so it is selection, not deletion. ⚠️ **Reachable ≠ reached:** E1's frozen bar is **undecided on committed evidence** (one of its two before-arm pairings fails its bound), so retiring it was wrong *and* claiming it will pass would be wrong. `#11021` carries the mechanism; the row's verdict stays `#7949`'s.
3. **Do the hierarchy first (the owner's stated goal), then selection.** *Rejected, and this is the correction.* 9,910 of 15,534 statement points carry neither `IMPL` nor `NAND` — ⚠️ **an owner-supplied measurement from the 2026-11-04 lane brief, taken on the hosted `live-v4` graph; it has no committed artifact in this repository and is not re-derivable from any command here.** Writing claims that mostly have no edges into a hierarchy produces a tree over anaphora. ⚠️ **And the second leg of this argument is explicitly a judgement:** until 2026-11-05 the capstone's five rows contained **no row that measured a hierarchy** (`E1–E5` cover volume, retention, anchoring, operator edges, narrative fidelity), so "the hierarchy moves none of the rows" was true *by construction* — the test could not distinguish "wrong next step" from "never measured", and **the owner's goal had no acceptance criterion.** That gap is now **closed by owner ruling: a sixth row was approved and defined** (per-pack declared → writable → written, `#7949` comment `6664402530`; the audit found 4 of 36 declared relations bind no write path, all in `pm`; **0 of the 36 names `core:goal` as an endpoint** (`goal` *is* a core kind — the layer exists and is unreachable); **no relation starts from a Subject**; and `initiative` is only a *synonym* (`dev:theme`, `venture:program`) with no kind behind it — `#11083`, with `#11000` declaring the level and `#11001` materialising the path). The decomposition still sequences **G (#11001) last**, because the constraint is the direction the work must flow, not its importance — but its outcome is now watched.
4. **Extract less** (cheaper model, fewer sessions). *Rejected.* `E1b` is a **90-of-90** no-loss control: fewer writes is a win only if nothing real is lost, and the A/B evidence (`#10463`: created nodes 284 → 247 against a ≤ 0.75 bar, while recovered real facts fell 47 → 40 and operator edges 171 → 139) shows "smaller" was not "leaner".
5. **Ship the beta on the flat catalogue.** *Rejected.* The epistemic layer **is** the product (ONTOLOGY §2); a claim with no edges is a fact without context.

**Profit impact — the causal chain, and its honest limit.**
`selection at the mint` → fewer non-referents in the Object layer → (a) recall returns referents rather than anaphora, so the answer surface stops citing `the plan doc` as an entity; (b) EP confidence propagates over claims that are actually about something; (c) the vector class — roughly half the graph — stops growing on rows no semantic query wants (`STORAGE-ARCHITECTURE.md` §12.1b, §12.2). That chain is **testable at E1/E4** and **not** a revenue motion: this epic is a **beta gate**, and what it protects is retention on the product's core promise. Order of magnitude: **not applicable to revenue**; the cost side is a **step function, not a per-GB figure** — at one tenant the dogfood instance pays **$73/month** (1 GB STARTUP) whether the graph is 143 MB or 700 MB (`STORAGE-ARCHITECTURE.md` §17.4.3; the per-GB arithmetic elsewhere is a *rate*, not a bill).

**Eisenhower placement: Important + Urgent → Do now.** ⚠️ **The honest grounds, because two easier ones do not hold:**

- **Important:** `#5087` carries `P0-blocker`, and its capstone **#7949** is `P0-blocker` and **0 of 6 green** (E6, the pack-logic row, was added by owner ruling on 2026-11-05 and starts red by construction). (Not the parent `#5086` — that is **CLOSED** and labelled `not-launch-relevant`.)
- **Urgent:** the beta date, **plus** the fact that the cheapest deciding measurement is **one 25-row scoring run** — so *starting now* is urgent even though the epic's own discriminator is unvalidated. ⚠️ **The "blocked on decisions, not work" ground is wrong and is withdrawn:** E1 is blocked on an owner ruling (`#5333`) **and** on a second arm per arm (n ≥ 2); E2 is blocked on **work** — `#10360` (*no mechanical enforcement of the verbatim state-value guarantee; retention is prompt-only at 49.4% vs a 54.4% baseline*) — not on a decision.
- ⚠️ **The accumulation ground is also withdrawn:** "every week adds more of the class that must be selected out later" assumes a later removal that this document's own assumption 4 says is **not available** without a `#5333` ruling. The rows accumulating now are, on this epic's account, **not recoverable later** — which makes selection urgent for a different reason: it is the only door that stops the class at all.

### Key assumptions

- *The capture door is the fleet's arrow, and it is `extractor_v2`.* Confidence: **high** — measured (`hosted_api.capture_session` → `sdk.capture_session` → `_extract_session_v2`); the only alternative is the `TORTOISE_SESSION_EXTRACTOR=m2` branch (`sdk.py:14797`), which is test-only. The brief's "legacy rule extractor" claim was **false** and is corrected in the decomposition.
- *A non-referential noun phrase can be told apart from a referent well enough to act on.* Confidence: **medium** — the field converges on the treatment, but **our arbiter has never been scored**; the 25-row owner-labelled set is the first measurement, and it is all-negative (specificity only). **This is the epic's top risk.**
- *`#1026` does not forbid an arbiter-judged mention record.* Confidence: **medium** — the ruling's subject is manifest filters that discard artefacts; the departure is marked with `OVERRIDES:` on `#1026` (comment 6648049935) and needs one owner read.
- *The existing Object population is out of reach without an owner ruling.* Confidence: **high** — `#5333`: a pass may add, no committed row is removed. This epic therefore improves new captures and touches no committed row.

**Recommendation:** proceed. The epic's premise survives the adversarial pass in a **narrower** form than its body states: this is not "write less", it is "stop minting non-referents, and wire the edges the story already states". Both are measurable at the capstone's own rows — with the caveat that the owner's hierarchy goal is **not** among them, and that the epic's central discriminator (the arbiter) is a hypothesis until 25 rows say otherwise.

## Adversarial pass — what was argued against, and what survived

| the strongest argument against | does it survive? |
|---|---|
| "The graph's problem is noise, and noise is a *volume* metric — you are building measurement machinery for a number the owner has refused (O2)." | **Partly.** O2 forbids a *target*, not a measurement; its committed text says *"This constrains volume targets only"* (`EXTRACTOR-V4-ARCHITECTURE.md` §16.3), and the storage doc re-affirms that measuring how many durable claims were kept is wanted while the goal behind it stays deferred. Every child here is justified by a **correctness** test (does this name pick anything out? is this claim wired to its story?), not by a volume number. |
| "Every comparable solved this by deleting or by recency, and you are keeping both claims." | **Yes, survives** — and that is the epic's actual bet: no comparable holds two claims in tension with a typed relation (`EXTRACTOR-V4-ARCHITECTURE.md` §11). The bet is argued, not measured, and §11 says so. |
| "You cannot fix the existing graph, so the epic improves a fraction of future writes while the measured problem stays." | **True, and stated.** E1's row is reachable on a fresh graph, which is how the capstone measures it; the production population stays until `#5333` is ruled on. |
| "The arbiter is unmeasured, so the whole selection arm is a hypothesis." | **True, and it is the top risk.** The mitigation is not argument: it is one 25-row scoring run against labels that **pre-exist** — ⚠️ and the bar is an **O5 draft-run-refine decision taken after seeing the score**, not a pre-registration. That is itself a risk on the top risk, and it is recorded as one rather than dressed as a pre-registered test. |
