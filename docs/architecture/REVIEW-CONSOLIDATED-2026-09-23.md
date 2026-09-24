---
title: Consolidated review — storage + extractor architecture docs
type: log
domain: platform
doc_status: draft
created: 2026-09-23
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: STORAGE-ARCHITECTURE.md, EXTRACTOR-V4-ARCHITECTURE.md, review record
---

# Consolidated review — storage + extractor architecture docs

**Date:** 2026-09-23 · **Cycle:** 1 · **Reviewers:** 4 (2 per doc, fresh context, independent lenses)
**Docs under review:** `STORAGE-ARCHITECTURE.md` (559 lines) · `EXTRACTOR-V4-ARCHITECTURE.md` (862 lines)

| reviewer | doc | lens |
|---|---|---|
| S1 | storage | technical correctness & feasibility |
| S2 | storage | completeness, consistency, decision integrity |
| E1 | extractor | design coherence & code-evidence fidelity |
| E2 | extractor | gaps, risks, unverified assertions |

**Deduped: 6 P0 · 34 P1 · 18 P2.** Findings that two reviewers reached independently are marked ⧉ (higher confidence).
⚠️ **Every code citation below was verified by a reviewer against the tree — WHICH IS NOT THE SAME AS BEING CORRECT.** A second cycle found **five of the corrections this review produced to be FALSE**, three of them traced to this document (**B2.1**, **B2.18**, **B2.20**), and applying them damaged the artifacts. **⇒ READ §E AT THE END OF THIS FILE BEFORE APPLYING ANYTHING BELOW.** `unverified` marks a citation the reviewer did not check; **its absence does not mark a finding as confirmed.**

---

## A. P0 — blocking; the design does not hold as written

### A1 ⧉ The vector index has two mutually exclusive models — and 7 TB is unprovisionable
`storage §12.1` vs `§12.2b` vs `§2` · *S1 P0, S2 P0 (found independently)*
- §12.1: HNSW is **RAM-mandatory**, "does not follow the disk rule", "every vector is a tax on every query forever", **~7 TB** at 1,000 tenants.
- §12.2b: the same index is **partitionable/evictable** ("working set becomes one tenant's index") and DiskANN's disk latency is affordable.
- **Supabase's largest instance is 256 GB** — so §12.1's own number cannot be provisioned. §2/§12.2b contradict §12.1.
- **Resolution required:** pgvector HNSW is an ordinary Postgres index — disk-backed, page-cached, with a **latency cliff** when cold. Pick that model, restate §12.1's figure as a **hot working set**, and delete §12.1's claim that §5's arithmetic is invalidated.

### A2 The truth/derived boundary test is false, and `Document` sits in both layers
`storage §3` · *S2 P0*
- The stated test ("append-only, never updated") is **false for two of four truth types**: `Source.updatedAt` is set ON MATCH by `_upsert_source`; `Source.reliability` is a **derived** query-time cache stored on the node; `Document.updatedAt` + `doc_status` transitions (`captured`→`extracted`, `ingest.py:183`, `hosted_api.py:10864`).
- **`Document ⊂ Object`** (`ONTOLOGY.md` §1/§4.4/§6 — `objectKind: document`), so the doc places one class in **both** layers.
- **Resolution:** use the test that *does* hold — **"is it carried by the journal / rebuildable from it?"** (`Source`/`Document`/`Event` are not in `_GRAPH_EVENT_TYPES` → primary; `Point`/`Object`/operators are journaled → derived). Then place `Document` explicitly and explain why a mutable cache on a primary row does not break the invariant.

### A3 ⧉ `#2453` ALREADY LANDED — the doc describes the pre-fix tree
`extractor §7` · *E1 P0 · S2 P2 (independently)*
- Doc claims operational values are **"DROPPED as a mechanics token"**. **False.** Commit **`4a690d0be`** (2026-09-07) extended `STATE_VALUE_CARVE_OUT` (`extractor_v2.py:145-171`) with an **OPERATIONAL-VALUE CARVE-OUT** — *"a concrete value that is the SUBJECT of a decision, observation, or plan is DURABLE too … carried VERBATIM"* — with exactly the examples the doc calls dropped (*"the p95 hit 4.2 seconds"*, *"a ten minute TTL"*). A `VALUE_FIDELITY_RULE` (`:230`) renders into the S2/S4 `{anti_routine}` slot; `_granularity_text()` (`:732-744`) appends it to S1.
- **Resolution:** rewrite §7 against `4a690d0be`. What is *actually* missing is **mechanical enforcement** — `valueGate` still does not exist in code; the rule is prompt-only. Same fix in `storage §7`.

### A4 Circular dependency — VET consumes S3's lookup, but S3 runs after VET
`extractor §4.2` + `D4` §9 vs `§4.2`/`§2.2` · *E1 P0*
> ⚠️ **DISPOSITION WAS CLAIMED, NOT VERIFIED — RE-CHECKED 2026-09-24 AND STILL OPEN.** The extractor doc's §16.1 claimed *"all 6 P0s corrected in place"*. **A3 and A6 were; A1 was by the research pass; A4 was NOT.** **`MERGE-INTO-EXISTING` is still in S2.2 VET's output vocabulary**, so the circularity below **stands as recorded** and the VET/S3 ownership question is unresolved. *(A5 was resolved by the owner withdrawing the volume target — O2, §16.3 of the extractor doc.)*
- D4 (narrowed): one neighbourhood lookup consumed by **the judgment**; where §4.2 (as §4.1b) quotes D4's reason as being about **discarding** → the judgment is **S2.2 VET**. But §4.2 puts **VET before S3**, and S3 owns the lookup.
- Compounds: §4.2 (then §4.1b) declares **"S3 is the single authority on whether a candidate is new or existing"**, yet VET's output vocabulary includes **`MERGE-INTO-EXISTING`** — that judgement, made by a non-authority.
- §4.2's constraint 3 ("the mechanical half of the gate needs NO graph") contradicts D4's granted neighbourhood outright.
- **Resolution:** either hoist the lookup above VET (**and re-price it on pre-VET volume**), or re-scope D4 so VET consumes only within-batch context and **remove `MERGE-INTO-EXISTING`/`RENARRATE` from VET's vocabulary** (they belong to S3/S1).

### A5 The 10× target is unsupported, and the design cannot reproduce its own measurement
`extractor §1`/`§4.0`/`§4.1`/`§6` · *E2 P0*
- The doc **never states the target or any aggregate reduction**; they live outside it (`#4899`: "≈2.7 per session"; `#4917` §1.9: the ~10× = Jev's `save ≥ 0.5` **AND** `altitude = architecture`, from **n=28, 3 kept**).
- **The measured basis is a PER-ITEM conjunction. The redesign moves abstraction to a PER-BATCH verdict** — *"level of abstraction … is a property of the batch, not of one candidate"*. **A batch verdict cannot be ANDed per item ⇒ the design cannot reproduce the 9.3× that justifies it.**
- §4.0, §4.1 and §6 also give **three different question sets for S2.2**; the volume-producing questions (value gate, altitude) are missing from both canonical tables.
- **Resolution:** decide per-item vs per-batch altitude; state S2.2's questions **once**; add a per-class reduction budget with the sample size behind every coefficient.

### A6 "An Object is a name, so it cannot be deformed" is falsified by the doc's own evidence
`extractor §2.4.3` · *E2 P0*
- §2.4.3 justifies excluding `Object`s from span-provenance: *"An `Object` is a **name**: it either matches the text or it does not."*
- **Falsified by §1's own table:** `the timeout command` ← *"timeout is not on macOS"*; `the staging-only constraint` ← *"…staged only, no commit/push"*; `the lane ownership rule` ← *"Lane ownership: …"*. These names are **synthesised definite descriptions that do not appear in the source** — "command", "constraint", "rule" are **model-added**. §1 itself calls 62.3% of Objects *"definite descriptions … never entities"*, and §14 concedes a non-`the` Object can be a bare reference.
- **Objects carry exactly the deformation risk the 4th layer exists to catch.** The scope justification is load-bearing and false.
- **Resolution:** either scope the claim honestly (**"a name drawn verbatim from source has no span-fidelity question; a definite description does"**) or extend span-provenance to Object names.

---

## B. P1 — important; fix or decide

### B1 — Storage

| # | finding | where |
|---|---|---|
| B1.1 | **§1's "$460/month" is wrong** — 1 GB × $73/GB = **$73/month**. $460 implies ~6.3 GB, a horizon never stated | `§1` |
| B1.2 ⧉ | **§2 says vectors are "44 MB (~1/3)"**; §12.1 measures **95.6 MB (~68%)**. §2 counts only the index and omits the 51.6 MB of raw vectors — which are equally RAM-resident and the first thing a HNSW traversal touches | `§2` vs `§12.1` |
| B1.3 | **§6's "~15× blended" is an artifact of B1.2** — recomputing with the measured 68% vector share gives **~7.3×**; and if the vector leg is RAM-priced (not 5×) the blend collapses toward ~1.5× | `§6` |
| B1.4 ⧉ | **§5's cost table is superseded by §12.1 and never corrected** — §5 assumes 1,000 users = **1 TB**; §12.1 assumes **~14 GB/tenant**. ~14× apart. "10× headroom" also contradicts §5's own table (17×) | `§5` vs `§12.1`, `§13` |
| B1.5 | **§12.1's 7.1 GB uses 2.5M as the EMBEDDING count** but labels it **nodes**. Today 25,000 nodes carry 33,580 embeddings (1.34/node) ⇒ 2.5M nodes ≈ **3.36M embeddings ≈ 9.6 GB** — a 34% understatement of the headline blocker | `§12.1` |
| B1.6 ⧉ | **Embeddings in the journal? — undecided, and BOTH branches break a claim.** `projection/__init__.py:3336-3343` **strips `embedding`** from the journal-derived snapshot (*"the replay re-derives it"*), restoring it only from a live pre-wipe capture. If journaled → the truth layer duplicates the most RAM-expensive byte class, destroying §6's 580× story. If not → replay **re-embeds**, and by §3's own non-determinism argument does **not** reproduce the graph ⇒ the invariant and the `#3895` fix are false | `§3` |
| B1.7 ⧉ | **§11.5 vs §13 contradiction:** §11.5 says the fan-out cap is **ADOPTED, initial value 200**; §13 says *"adopted in principle; no value set yet"* | `§11.5` vs `§13` |
| B1.8 ⧉ | **§13 says the derived layer becomes "disposable"** — exactly the reading §3 forbids in bold. `#3895` is a **restore**; "disposable" would license dropping the only copy | `§3` vs `§13` |
| B1.9 | **DiskANN availability stated as settled in §2/§12.2b, "unverified" in §14** — and §12.2b's whole fallback rests on the settled reading | `§2`, `§12.2b` vs `§14` |
| B1.10 | **Per-tenant vector partitioning (§12.2b Lever 1) vs RLS row-scoping (§4) are different physical designs.** An RLS predicate (session GUC/auth claim) is **not a plan-time constant**, so the planner cannot match a per-tenant partial index — it would not be chosen. Needs declarative partitioning + pruning (or a per-connection constant). `service_role`/owner bypass never stated | `§4` vs `§12.2b` |
| B1.11 | **§9.1 puts the narrative in "Supabase storage" (object storage) and calls it "searchable"** — object storage has no FTS/vector search; making it searchable reintroduces the copy/sync the same section rejects | `§9.1` |
| B1.12 | **No backup/restore of the truth layer.** The journal is the *only* durable record; #3895 is a backup/restore defect. Absent from body and §14 | `§3`, `§14` |
| B1.13 | **No erasure/retention design.** An append-only journal that must stay the rebuild source vs GDPR erasure (plus the HNSW tombstone/`REINDEX` path) | `§3`, `§14` |
| B1.14 | **No divergence detection/repair.** What if the append succeeds and the projection fails? `#4240` shows the projection can silently drop edges. No detector, no repair, no read-consistency statement | `§3` |
| B1.15 | **§3 overclaims the ontology's support.** The two quotes support the **Event/status** fold only — not a four-type truth set. `Source`/`Document`/`GraphEvent` placement is **this doc's proposal**, not an existing fact | `§3` |
| B1.16 ⧉ | **§7 cites `#1509 §9` and `#1509 E2` — neither exists.** `#1509` has no §9/E2; the decision is a **comment** ("Decision requested (2.2) … A (recommended)") and E2 is **`#1534`**'s slot | `§7` |
| B1.17 | **§7's "today" column is stale** (same root cause as A3) | `§7` |
| B1.18 | **`§4.2` cited for the `Object.status` quote; it is `§4.3`.** And **`MITIGATES` is not a registered predicate** — the canonical mechanism is `mitigated_by` (ONTOLOGY §3.9, #2315) | `§3`, `§8` |
| B1.19 | **5.6 KB/quota-node overstates it** — the numerator is total instance RAM (includes ~19,944 quota-free turns, Events, Sessions, scaffolding, the full HNSW index). `#4333` estimates **2.5–4×**. Honest figure: **~3 KB/node** | `§1`, `§13` |
| B1.20 | **No cap-transition design.** What happens at the 25,000 cap during migration (refusal? does dropping the derived layer buy headroom? re-scale?). `#4614`: the failure is a **silent client-side drop** | `§1`, `§14` |

### B2 — Extractor

| # | finding | where |
|---|---|---|
| B2.1 | ⛔⛔ **THIS FINDING IS ITSELF WRONG — CORRECTED 2026-09-24, DO NOT APPLY IT.** It said *"CLASSIFY before RESOLVE because the LOOKUP needs KINDS" is FALSE*, citing `_derive_queries` (`:1744-1778`). **`_derive_queries` genuinely never reads `kind` — but it builds FTS query STRINGS; it is not the resolution lookup.** The lookup is **`resolve_entities` (`:2755`), called at `:4926`**, which passes `{"name":…, "kind":…}` per ref to **`_find_existing_entity(entities, name, kind)` (`:2705`)** — whose **exact-match branch folds and compares `kind`**, with a bare-form fallback only when unambiguous. **⇒ The ordering argument is TRUE: CLASSIFY runs at `:4803`/`:4888`, `resolve_entities` at `:4926`, so the code already classifies first — and `_find_existing_entity` needs those kinds.** Applying this finding moved the rule to "after S3" and **would have degraded entity resolution to the ambiguous bare-form path — the exact duplicate-manufacturing failure the design exists to prevent.** *(The one narrow true half: the chain enforcer and S4/S5 typed refs also need kinds.)* | `§4.2` constraint 4, `§2.3` — **reverted; see extractor doc §16.2** |
| B2.2 | **S3 and S5 share the duplicate question → both fail the §4.0 purpose test.** §4.2 puts against-priors dedup **inside S3**; §4.0's S5 row then asks **"duplicate?"** again. §5 splits one lifecycle decision across both | `§4.0`, `§4.1`, `§4.2`, `§5` |
| B2.3 | **Exact dedup AGAINST THE GRAPH is equally hash-cheap and needs no semantic judgement** — point ids are content-addressed (`_content_id` `:2560-2562`), merge key is content alone (`_merge_key` `:2265`). The doc limits "may run first" to **within-batch** and defers the graph-exact check to `classify_consolidation` inside `execute_embed` (`:3994`) | `§4.2` |
| B2.4 | **§4.2 constraint 3 contradicts the false-positive paragraph.** "The mechanical half needs NO graph" vs "only the lookup or a semantic judgment can tell them apart" | `§4.2` |
| B2.5 | **S2.2a DEDUP is mis-described as "entity-free".** A point carries `about_entities`, `search_keys`, `source_turn_id`, `quote` (`:4050-4057`); two content-identical points can carry **different attachments**, and a content-only first pass **silently drops one attachment set**. Also pre-empts `MERGE-INTO-EXISTING`, which the doc assigns to VET | `§4.2` constraint 1, S2.2a |
| B2.6 | **S0's mapping half is UNBUILT.** `_edus_from_conversation` (`:4641-4644`) is **transcript-only** and applies **no field→type mapping** — and §3 itself admits *"What is missing is the field→type mapping declaration."* §4.0's "confirmed mechanical" covers only the segmentation | `§3`, `§4.0`, S0 |
| B2.7 | **The `aboutObject` read-site enumeration is incomplete** — the ruling's evidence. Also read by `ranking.py` (`:461`, `:714` — SDK-reachable via `order_by='graph'`), `subgraph.py` (`:312`, `:319-320`, `:470`, `:481` — the `#3011` engine), `assembly.py` (`:573`, `:789`, `:809`, via `ask_lane.py:471`). `rg -ln aboutObject tortoise/` → **22 files** | `§13.6`, `§13.8` |
| B2.8 | **The reduction is unsupported for the dominant class.** 14,851 statements (**59.5%**) vs 7,863 Objects (**31.5%**) vs 2,233 operators (8.9%). The diagnosed mechanism removes **Objects** — a **~31.5% ceiling** — while §7 insists volume must come from *not writing claims*. No section quantifies point removal. **Also: §1's "37.7% single-referenced" and §14's "37.7% non-`the`" are different quantities presented as one** | `§1`, `§7`, `§14` |
| B2.9 | **"MODEL SWAP, not new code" for Jev is unsupported.** `llm_tail` (`:93`) is a **bool**; `:522` is the **offline-eval CLI disabling it**. The tail (`_adjudicate_batches` `:337-496`) is a hardcoded prompt + JSON parser. Jev's primitive API is a **different contract** ⇒ needs an adapter, prompt and parser | `§2.3`, `§4.2` |
| B2.10 | **The Jev "structural vocabulary enforcement" argument over-reads.** (a) A closed `Choice` converts *invented kind* into **confidently-wrong kind** when the right kind is absent — not discussed. (b) The **live tail already enforces** containment — `closed_vocab_rejects` rejects out-of-candidate picks and falls back to kNN top-1 (`:480-488`). (c) `#1026`'s requirement is about **`entityCues`**, and **`rg -n entityCues` → no matches**; entity cues come from the extraction prompt, not a classifier | `§2.3`, `§15` |
| B2.11 | **No step produces the narrative OR the raw-fact span.** S6 is "write entities + connections + metadata/lifecycle". §4.0 has no persistence purpose beyond S6. **D1 and §2.4 are not implementable from the steps as written** | `§4.0`, `§4.1` |
| B2.12 | **No embedding-decision step.** `storage §12.2a` **decides** the `EMBED` gate exists ("a row can be worth KEEPING but not worth EMBEDDING", "the lever is what gets an embedding") and reframes the central lever. The extractor doc **never mentions it**, and S2.3's kNN path silently assumes every candidate is embedded | `§4.1`, `§4.2` |
| B2.13 | **No per-step failure policy.** Only S2.2 ("fail-open — keep") and S2.3 ("never raises") state one. **If the S3 lookup errors, the natural fail-open marks candidates `new` — manufacturing duplicates**, the exact failure S3 exists to prevent. Partial S6 has no transaction/retry boundary vs `#4240`/`#4716`. **Re-narrate has no tie-break** for a worse second narrative | `§4.1`, `§4.2` |
| B2.14 | **`#4911` (no secret redaction) + an append-only immutable span layer = immortal secrets.** The doc lists `#4911` as an unrelated capture-path issue | `§2.4`, `§15` |
| B2.15 | **No migration for the ~25k existing nodes** — no backfill, no re-extraction path, no decision on the ~4,900 `the <X>` Objects. Also: what happens to the **live** S4 `merge_embed_lists` when S4 is deleted from the step list | `§1`, `§15` |
| B2.16 | **No evaluation or rollback for v4 itself.** No baseline metric (volume alone is gameable by dropping everything), no recall/precision floor, no threshold, no revert path. `#4894`'s "Gap B — nothing measures it" is unanswered | `§14`, `§15` |
| B2.17 | **The cost model omits S1, S2.1, the re-narrate pass and S5** — yet the re-narrate trigger is the safety valve whose cost justified the design. Per-item costs are also stated two ways ($0.000243/item vs $0.000144/question) with no combined per-session figure | `§4.2` |
| B2.18 | ⛔⛔ **THIS FINDING IS ITSELF WRONG — CORRECTED 2026-09-24, DO NOT APPLY IT.** It claimed the doc's `H1 ≥15` / `H2 ≥10` / `H3 (−5)` were *"not in the frozen H1–H3"* and were *"3 of 3 embellished"*. **Verified against the frozen file (`docs/experiments/2026-09-11-abc-context-assembly-experiment.md` §2, unchanged since 2026-09-11): `H1 \| C > B, ≥15 pts` · `H2 \| A > B, ≥10 pts` · `H3 \| C ≥ A − 5 pts` · `H4 \| B ≥ A − 5 pts` · `H5 \| B < A − 15 pts` and ≥ 80%.** The doc was **correct about all three**. **The premise was a misreading: "also present in H4 / also present in H5's falsifier" is not "absent from H1/H3" — and `−5` appears in BOTH H3 and H4.** Applying this finding **deleted three accurate numbers** from the doc's most load-bearing external citation. | `§2.4` — **reverted; thresholds restored; see extractor doc §16.2** |
| B2.19 | **§2's "~1,000× cheaper" is unsourced** (storage's measured figure is **580×**), and **"carries most of the retrieval value" has no measurement** — §10 itself says *"No head-to-head measurement exists"* | `§2`, `§10` |
| B2.20 | ⚠️ **FIX DID NOT RESOLVE IT — the same defect in a new form (re-checked 2026-09-24).** D4's status is stated two ways. §4.2 (formerly §4.1b): *"Owner decision required. Nothing has been changed"* — corrected to "PROPOSED"; §9: *"DECIDED, narrowed — confirmation pending"* — and the architecture **has** changed (S1.5 deleted, the lookup relocated, S3 declared sole authority). **⚠️ The corrected line still contradicts §9.** | `§4.2` (was §4.1b), `§9` |
| B2.21 | **D8 has two statuses across the two docs** — extractor §9 "DECIDED"; storage §7 "Open design question / proposal to test (not decided)". Neither has owner confirmation | `§7`, `§9` |
| B2.22 | **The `#4899` false-positive requirement has no mechanism.** The doc requires the gate to encode **decision-relevance** and says it runs **before the lookup** — i.e. before the only artifact that could supply the relevance judgement | `§7`, `§4.2` |
| B2.23 | **Two ownership overlaps unmarked:** S3 decides entity resolution while **`#2730`** is an open research issue covering exactly that; §2.4 **widens `#2684`'s scope** ("generalises from value-bearing points to all points") without recording the coordination | `§4.1`, `§2.4` |

---

## C. P2 — improvements

**Storage:** §12.3 heading says "TWO" but enumerates Tier 0/1/2; the 1.56× comparison is not like-for-like (the raw-vector term matches *exactly*; the **graph term is a 9.3× understatement**, and the measured 1.85× is **below** the cited 2–5× range); Lever 2's "~29%" has no derivation or stated quantity; halfvec/binary are **vector-byte** reductions, not index reductions (at M=16 the index falls ~1.4×/~8.9×, not 2×/30×); §5 omits the Pro plan base (~$25/mo), included disk, and egress ($0.09/GB); the journal grows without bound (a revised Point produces N full-payload events) and is absent from the cost model, as is replay cost; the "~10× headroom" doesn't match its own table; §12.2a calls the ephemeral declaration "identifier-only text, verbatim" but *"tool workarounds, sprint mechanics"* are **semantic** content — it is a **retention** policy, not a **meaning** predicate; §10.1's relocation interacts with HNSW tombstones (relocation = DELETE+INSERT ⇒ does **not** free RAM); §4 specifies no RLS policy shape, roles, or bypass rule; `Document`'s layer placement needs stating.

**Extractor:** `merge_embed_lists` cited at `:4838` (that is `stage_stats`) — **def `:2284`, call `:4845`**, cited 3×; `aboutObject` reported as 27,310 (§2.4.3) and 27,303 (§13.6); `:1864` is a **docstring**, not an edge definition; §2.4 defines the 4th layer but **no step produces it**; §1 says "4 active days" vs the measurements' **3**; **D6 and D7 are listed twice** in §9; the S2.2 note says "seven" while §4.0/§4.1 say **eight**; no "What this document does not decide" section; no tenancy/concurrency/observability (§4 makes tenancy DECIDED, and S3's lookup-then-create has no atomicity statement — two concurrent sessions can both create); the re-narrate "measured keep-rate" is never specified as an emitted metric.

---

## D. Questions — decision-grade, requiring research before any answer

Per `AGENTS.md` decision protocol: **research first → contradiction test → adopt if convergent and uncontradicted, else present context/options/analysis/recommendation.**

| # | question | why it is decision-grade | from |
|---|---|---|---|
| **D1** | **What is the vector-index model — RAM-mandatory, or disk-backed with a latency cliff?** | Determines whether the whole pricing story and Lever 1 hold. Everything downstream depends on the answer. | A1 |
| **D2** | **What is the correct truth/derived boundary test?** | The doc's test is false and one class sits in both layers. | A2 |
| **D3** | **Where do embeddings live — are they journaled?** | Both branches break a stated claim (cost vs reproducibility). | B1.6 |
| **D4** | **Is abstraction (altitude) per-item or per-batch?** | The measured 10× rests on a **per-item conjunction**; the design moved it to per-batch. | A5 |
| **D5** | **Should `Object` names carry span-provenance?** | The "names cannot be deformed" premise is falsified. | A6 |
| **D6** | **Where does the mechanical DISCARD gate live — S2.2 (pre-lookup) or E7 (post-lookup)?** | The doc asserts both; and pre-lookup placement cannot satisfy `#4899`'s decision-relevance criterion. | B2.4, B2.22, E2 |
| **D7** | **Should exact dedup against the graph also run first (as a distinct step)?** | A missed cheapest-first step; likely yes, and cheap. | B2.3 |
| **D8** | **How is the reduction target set and evidenced?** | Currently unsourced and n=28; volume alone is a gameable metric. | A5, B2.8, B2.16 |

> # ⛔ §E — READ THIS BEFORE APPLYING ANYTHING ABOVE: THIS REVIEW CONTAINED ERRORS, AND APPLYING THEM VERBATIM DAMAGED THE ARTIFACTS
>
> **A second review cycle (2026-09-24, four fresh-context reviewers + two gap analysts) checked the *corrections* this review produced, not the original text. Five were false, and three trace here.**
>
> | finding | verdict | what applying it did |
> |---|---|---|
> | **B2.1** | ⛔ **WRONG** | inverted the pipeline order — the doc now claims the lookup needs no kinds, which is false, and it contradicts its own §2.3 |
> | **B2.18** | ⛔ **WRONG** | **deleted three accurate thresholds** from the doc's most load-bearing external citation |
> | **A4** | ⚠️ **disposition was claimed, not verified** | the doc says "all 6 P0s corrected"; `MERGE-INTO-EXISTING` is still in VET's outputs, so the VET/S3 circularity **stands** |
> | **B2.20** | ⚠️ **fix replaced one two-ways status with another** | §4.2 (was §4.1b) now says "PROPOSED"; §9 says "DECIDED" |
> | — | a **lane inference**, not this review | "there is no `:Document` node and none is created" — `:Document` is written, read and **quota-counted** |
>
> **⇒ THE LESSON, and it is now binding (extractor doc §16.2): a review finding is a CLAIM, not a fact.** Applying one is a change to the artifact and must be **verified against the same primary source the finding cites** — otherwise the review becomes a **propagation vector for its own errors**, and the `⚠️ CORRECTED` marker makes the wrong text *more* credible than the right text it replaced. **"Also present elsewhere" is not "absent here"** — two of the five are that single misreading.
>
> **⚠️ And the counter-note, so this is not an argument against reviewing:** this review also produced the findings the design now depends on — `#4997`'s index drift, the four-layer placement, the four dedup keys, the `c/r` ordering rule and the ten `!`-corrections that hold. **The failure was unverified application, not review.**
>
> **Status of this document:** kept as the **cycle-1 evidence record**. Findings **B2.1** and **B2.18** are marked **⛔ DO NOT APPLY**; **B2.20** and **A4** carry an in-place correction showing the fix did not resolve them. **Everything else stands as a CLAIM recorded at the time** — not as verified fact. **Before applying any finding above: open the primary source it cites and confirm it.** ⚠️ **A1, A2, A3, A5, A6 and the ten `!`-corrections were confirmed and are reflected in the current docs. The five in the table above were not.**
