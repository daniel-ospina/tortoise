---
title: "Epic research brief — #5087 Extractor v4"
type: research
domain: platform
doc_status: final
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#5087, EXTRACTOR-V4-ARCHITECTURE.md, STORAGE-ARCHITECTURE.md"
---

# 02 · Research brief — #5087 Extractor v4

**Search tool:** `web_search model="sonar"` — **rung 2** (rung 1 unavailable: `mcp_load seo-intelligence` → *Unknown MCP server*) — this pass: **4 queries attempted / 4 returned → 10 distinct sources across 6 ledger entries**; the epic's earlier comparables research is the in-repo record `EXTRACTOR-V4-ARCHITECTURE.md` §11–§16 (Hindsight at `f00ad1fe`, GraphRAG, Graphiti, Mem0 — cited there, not re-fetched here).
**Graph:** **0 usable hits over 2 queries** (`definite description object entity mention…`, `object kind reference test mint gate…`; rows at similarity 0.023–0.038) → *nothing held, re-derived*.
**Depth:** deep (epic scope). **Domain classification:** **Complicated** — the pipeline is knowable and the failure modes are enumerable; the irreducible uncertainty is model behaviour, which is measured rather than designed.

## Strategy Context

- **The product's differentiation is the epistemic layer, not the graph.** No comparable holds two conflicting claims in mutual tension with a typed support/attack relation and independent confidence (Hindsight shipped `opinion` + confidence, **deleted `opinion` on 2026-01-15** (≈one month after the paper) and **dropped `confidence_score` on 2026-04-02**; GraphRAG erases contradictions; Graphiti terminates the loser; Mem0 accumulates without a relation). The epic's bet is that holding tension is worth its cost — **argued, not measured** (§11 says so; the owner ruled keep on 2026-10-06).
- **Selection is one of the cheapest levers available — a narrower claim than "the cheapest"**: on FalkorDB cost is provisioned RAM, and the vendor exposes **no per-graph eviction** *as far as we can establish* (`STORAGE-ARCHITECTURE.md` §2.1 names confirming that with the vendor as the single most valuable thing to confirm). Writing fewer non-referents needs no rewrite of committed rows, but it is **not** the only migration-free lever in this design family: `S0a NORMALIZE+HASH` and `S0b SOURCE DEDUP` are the design's own named volume levers — declared as **NEW proposal steps** in §4.2 of the architecture doc, though the underlying mechanism is **already shipped**: `tortoise/source_identity.py`, recorded `state: implemented` in `config/v4-mechanisms.yml` with `tests/test_source_dedup_registration.py` behind it. So "there is no other lever" would be false even before this epic; what is proposal-stage is their place in the step list, not their code.
- **The measured premise is real, and it carries its date and its denominator**: **4,900 of 7,859 Objects (62.3%)** are `^(the )<X>`, enumerated **2026-09-24** (`docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md`, repeated in `…2026-09-24-2730-entity-attachment`); 37.7% are referenced by exactly one point; **zero** duplicate names — so the problem is *not* duplication, it is writing things that were never entities. ⚠️ **Node counts move** (the architecture doc warns each one is date-stamped at its source): the epic's current population is **5,314** Objects and it has **not** been re-enumerated, so the ratio is carried from a different population until `#10995` re-derives it.

## UX Pattern Research

**Deliberately thin, and the reason is structural:** this epic has **no user-visible surface of its own**. Its consumers are (a) the answer surface, which renders better because recall stops returning anaphora, and (b) the dashboard's graph views. There is no interaction to precedent-research. The one user-visible consequence is a *legibility* one — an entity named `the plan doc` is unusable as a node in any UI — and it is captured in the Customer Value Map of the scope doc rather than as a UX pattern. **No external UX queries were run; that is a declared skip, not an omission.**

## Workflow Pattern Research

- **The field's pipeline shape is settled and ours matches it**: S0 read → S1 narrate → S2.1 extract → S2.2 vet → S2.3 classify → S3 resolve → S5 connect → S6 commit. The two structural differences from comparables are (i) we run an **adversarial** filter (VET) on candidates, and (ii) we hold tension instead of resolving.
- **Where the field's workflow differs, and it matters here:** Graphiti treats extraction as *match-or-`NEW_ENTITY`* (only genuine new entities create nodes); FalkorDB's GraphRAG-SDK runs a **generic-reference stoplist** before minting and records mention text rather than creating pronoun nodes; Microsoft GraphRAG prompts for **named** entities. Entity linking's canonical pipeline is mention → candidate → disambiguation with **NIL clustering** for "no referent". **Our pipeline has no equivalent step at all** — `OUTPUT_CONTRACT` has an `entities[]` array and no mention channel.
- **Ordering is a real design result, not a preference**: the correct rule is *cost per unit of safe rejection* (`c₁/r₁ < c₂/r₂`), which puts the free mechanical dedups before the model passes, and CLASSIFY **before** RESOLVE because `_find_existing_entity` uses `kind` as its exact-match discriminator.

## Tech Stack Research

- **No new library, no new service.** The reuse set is already in-tree: `vet_gate` (S2.2), `kind_classifier` (S2.3, kNN + margin tail), `near_dup_merge` (fusion), `s2_decision_seam` (the typed-decision adapter, `#5087`'s own), `value_gate`/`embed_gate` (one rule set, two questions), `source_identity`, `fanout` (cap 200), `commit_schema` (the commit shape), `failure_policy` (the lookup's state).
- **The typed-decision model** (Jev, `api.typesafe.ai`) is the only external dependency the design adds, and it is already integrated and **off by default**: it is decision-only (a `Choice`/`Score`/`Noul` cannot invent a value outside the schema the caller supplies), measured at ~$0.00047/call for a 40-item batch — ⚠️ **computed, not measured**: the seam's own request is 50,004 B ≈ 11,223 cl100k tokens for 40 items / **80 questions**, priced at the vendor's documented rate; **no live call has been made** (`S2-DECISION-SEAM.md` §7). ⚠️ Its `score` primitive has no caller of ours, and the request's element-attribution idiom is unverified against the vendor's documented form (`#10176` is the precondition of the first *armed* scored run).
- **What is absent in the stack, and is the epic's real gap:** there is no place to *put* a mention. `aboutObject` is Point→Object; the span layer addresses value-bearing points only (`#2684`); the narrative lives in Postgres and is referenced by its `Source` (D1). The mention's home is the open data-model question (`#11021`).

## Assumptions Register

| Assumption | Confidence | Source | Validation Plan |
|---|---|---|---|
| The fleet's capture door is `extractor_v2`, not the legacy rule extractor | **high** | `hosted_api.capture_session` → `sdk._extract_session_v2`; `extractor.py::_classify_point_kind` reachable only from `ingest.py` | already measured; the decomposition's census (`#10995`) keeps it true per door |
| 62.3% of Objects are `^(the )<X>` (4,900 / 7,859, enumerated 2026-09-24) and the class is a *mention* problem, not a duplication problem | **medium** — carried from the 7,859-row enumeration of 2026-09-24, of which today's **5,314** Objects is ~32% smaller (the enumerated population is ~48% larger); **not re-enumerated**, and the two figures are not composable (62.3% of 5,314 would be ~3,311, not 4,900). `#4894` is the *mechanism* audit (50 instances), not the ratio | `docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md` · `…2026-09-24-2730-entity-attachment` | `#10995` re-derives the share per door on today's population |
| A non-referential phrase can be separated from a referent well enough to act on | **medium** | converged practice (NIL / `NEW_ENTITY` / stoplist / named-entity prompt); **no measurement of our arbiter** | score the arbiter on the 25 owner-labelled rows (`tests/eval/write_path/salience_labels.jsonl`), then E1's row at n≥2 arms |
| `#1026` permits an arbiter-judged mention record | **medium** | the ruling's subject is manifest filters that discard artefacts | `OVERRIDES:` recorded on `#1026`; owner read requested (scope doc, Risks) |
| The existing 5,314-Object population cannot be improved without an owner ruling | **high** | `#5333` (a pass may add; no committed row is removed) | out of scope; `#5333` owns it |
| VET alone cannot fix the class | **high** | `apply_vet` Rule 2 (a referenced entity is never removed) + every Object is named by its own claim ⇒ share moves by exactly 0.0 | pinned by `tests/test_e1_e2_measurement.py` |
| The narrative adds value over raw text | **low** — *provisional, twice measured, neither run decisive* | `#7947` run 1 branch 3; run 2 branch 2 not certifiable; n=25 | not needed by this epic; the shipped shape (narrative as focus, raw as source) is what runs |

## Raw Notes

- `2026-11-05 · rung-2 · adversarial` — *"nodes inferred from **nearby text rather than true references** … inflate node counts, fragment entities, and distort graph metrics"* (Springer, *Understanding the Effect of KG Extraction Error on Downstream Graph Analyses*) — the literature names our exact class.
- `2026-11-05 · rung-2 · canonical` — entity linking = mention → candidate → disambiguation, with **NIL clustering** for no-referent mentions (*temporary, consistent identifiers* rather than a KB entity); *"the need to recognize when no candidate is correct instead of selecting the highest-scoring false match"* — **NASTyLinker** (Springer), the one source that sentence is pinned to. The row's other members, each with its own status: Slot Filling arXiv 2207.04862 (**preprint**), financial-entity extraction (Springer), the fashion knowledge graph (**CEUR workshop**), and the JHU entity-disambiguation paper (**dissertation** — not peer-reviewed).
- `2026-11-05 · internal · cost` — the seam's `vet-v2` request is 50,004 B ≈ 11,223 cl100k tokens for 40 items / **80 questions**; **≈$0.00047/call is computed** from the vendor's documented rate, **not measured** — no live call has been made (`docs/architecture/S2-DECISION-SEAM.md` §7).
- `2026-11-05 · rung-2 · competitor-precedent` — Graphiti/Zep: *"either matched to an existing node or classified as `NEW_ENTITY`; matches are merged, while only genuine new entities create nodes."*
- `2026-11-05 · rung-2 · vendor-SDK` — FalkorDB GraphRAG-SDK: *"a stoplist to exclude pronouns and generic references"*; *"records mention text … rather than creating separate pronoun nodes."*
- `2026-11-05 · rung-2 · lab` — Microsoft GraphRAG indexes **named** entities with descriptions.
- `2026-11-05 · rung-2 · pitfalls` — *"over-extraction … entity explosion"* is a named production failure (DEG-RAG, arXiv 2510.14271, **preprint**); the repair is entity resolution + a judge, not deletion of committed rows.
- `2026-11-05 · internal` — `sdk.py` carries the comment *"the 'non-referential mention promoted to a node'"*; `definite_description_backfill.py` exposes `is_bare_definite_description` and `is_positively_bare_definite_description`; **nothing in the write path consumes either.**

## Review record (epic-verify Step 4)

**Three fresh-context review rounds, all findings applied — no round was closed by convergence or by a cap.**

| round | issues found | what was wrong | disposition |
|---|---|---|---|
| 1 | **5** (2 P1, 3 P2) | the 62.3% share carried no date or denominator and was attributed to `#4894` (which is the *mechanism* audit, 50 instances, not the ratio); the jev cost figure was presented as measured; the source accounting did not reconcile (4 queries vs 10 sources); arXiv 2510.14271 was unlabelled as a preprint and the quoted sentence was not pinned to one source; the Hindsight timeline was wrong by two dates | all 5 applied — the ratio now names 4,900/7,859 measured 2026-09-24, the cost is **computed not measured**, the ledger reconciles to 10 sources / 6 entries, each source carries its status, and the timeline reads `opinion` deleted 2026-01-15 / `confidence_score` dropped 2026-04-02 |
| 2 | **2** (both P2) | a magnitude with an **inverted base** ("a population 32% larger" — 7,859 is 48% larger than 5,314, not the reverse); and `S0a`/`S0b` described as "proposals, not shipped" when the mechanism is `state: implemented` in `config/v4-mechanisms.yml` with `source_identity.py` and a test behind it | both applied — the base is stated the right way round and marked not-composable; the proposal-stage framing now attaches to the *step placement*, not the code |
| 3 | **1** (P2) | the base-inversion fix had not propagated to every sentence | applied; round 3 otherwise confirmed items 1–5 and the four align assumptions (high/medium/medium/high) |

**What this round could not do, stated rather than implied:** it has **no measurement of our own arbiter** — no row in this brief is a score of the referentiality judgement on our data. That is by design (`#11021` owns the probe), and the register's "medium" on that assumption is the honest reading until the probe exists.
