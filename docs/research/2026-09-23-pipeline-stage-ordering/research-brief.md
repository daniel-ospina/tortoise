---
title: Research brief — the correct ordering of pipeline stages, and where dedup belongs
type: engineering
domain: platform
doc_status: draft
created: 2026-09-23
subjects.team: epistemic-team
issue: "#4894"
aboutSubjects: Tortoise memory graph
aboutObjects: EXTRACTOR-V4-ARCHITECTURE.md, pipeline stage ordering, dedup keys
---

# Research brief — the correct ordering of pipeline stages, and where dedup belongs

**Date:** 2026-09-23 · **Version:** 2 (cycle-2 corrected) · **Question owner:** extractor architecture (`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §4.2)
**Resolves:** review findings **B2.3** (exact against-graph dedup is hash-cheap but deferred) and **B2.5** (identity-only dedup drops attachments); decision question **D7** (and partly **D6**)
**Domain classification:** **Complicated × Complex** — the ordering principle is a solved, formally-stated problem (Complicated); its application interacts with content-addressed ids, attachments-as-sets, a currently-pure write stage, and a precision-<1 gate (Complex). Depth: **Deep** (8 external queries).
**Scope flags:** `--domain=engineering`

---

## 0. Problem reframing (Step 0)

**As asked:** *"What is the correct ORDER of the stages, and where does deduplication belong?"*

**5 Whys:** Why does the order matter? → total cost, since the extraction model dominates. → Why does that reduce to order? → because each stage's cost is paid per *surviving* item, so a stage's position sets how many items pay it. → Why treat that as "order by cost ascending"? → because a cheap stage that runs early shrinks the set. → Why is that not the answer? → because **a cheap stage that removes nothing shrinks nothing** — the cost rule is a *proxy* for a different objective. **The root is a proxy mistaken for the objective.**

**Reframed:** *"The pipeline must minimize E[total cost] = Σ cᵢ·(items reaching stage i) subject to (a) the final accepted set being identical to any other valid order's, and (b) no stage's loss being unrecoverable. Ordering is a derived quantity under those two constraints — not a first principle."* The load-bearing word in the original question is **"correct"**, and it means *two* things the doc conflates: **cost-correct** and **loss-correct**.

**Alternative framings (How Might We):**
- HMW make the order fall out of *measured* selectivity and cost instead of a hunch? → the ratio rule, with a measurement harness (query-planner discipline).
- HMW make each stage's correctness independent of its position? → then only cost decides; where that fails, the dependency is a real constraint and is the first thing to pin.

**Assumptions mapped:**

| # | Assumption | Tag |
|---|---|---|
| A1 | "The pipeline is a linear cascade with no feedback" | **holds only per-pass** — the bounded re-narrate (one retry, §4.2) is a real feedback edge, and §1.1's derivation is *per-pass*, not per-session |
| A2 | "Stage costs are additive and per-item" | **partly FALSE** — the kNN/LLM legs are *batched*, so marginal ≠ average cost (§1.2, P2) |
| A3 | "Dedup is one operation" | **REFUTED** — it is ≥4 operations (§2.3) |
| A4 | "Identity is content, so dedup needs no metadata" | **REFUTED for merge** — identity is content (points/events branch of `_merge_key`, `extractor_v2.py:2265`), but the *record* carries a set of attachments (`OUTPUT_CONTRACT` `:1021-1027`; `pt_entry` `:4050-4057`; review B2.5) |
| A5 | "The lookup needs kinds, so CLASSIFY must precede RESOLVE" | **REFUTED by the code** — `_derive_queries` reads section+text only (review B2.1) |
| A6 | "An exact dedup pass is lossless" | **TRUE for identity, CONDITIONAL for attachments** — lossless iff the merge is a union |

**Reverse the problem:** what if the dominant-cost stage ran *last*? Then it processes the most-reduced set — correct — but no earlier stage could have reduced a *raw input* duplicate, which is the one duplicate class that would have made the dominant cost itself cheaper. **The highest-value dedup is therefore at the input boundary, not among extracted candidates** — a placement the doc does not have (§2.3).

---

## 1. The ordering principle

### 1.1 "Cheapest first" is a special case. The rule is **cost per unit of rejection** [HIGH]

**Derived here, in two lines.** Two filters, cost `cᵢ` per item, rejection rate `rᵢ = 1 − sᵢ` (`sᵢ` = pass probability). Expected cost per item:

```
order (1,2): E = c₁ + s₁·c₂        order (2,1): E = c₂ + s₂·c₁
1 before 2  ⟺  c₁ + s₁c₂ < c₂ + s₂c₁
            ⟺  c₁(1 − s₂) < c₂(1 − s₁)
            ⟺  c₁/r₁ < c₂/r₂
```

**⇒ Sort ascending by `cᵢ / rᵢ` — cost per unit of rejection = "most SELECTIVE per unit cost first."** "Cheapest first" (`c₁ < c₂`) is the optimum **when `r₁ ≥ r₂`** — a *sufficient* condition, **not a necessary one**. The general condition is the biconditional above, `c₁r₂ < c₂r₁`: with `c₁ < c₂` **and** `r₁ < r₂`, cheapest-first can still win — e.g. `c = (1, 10)`, `r = (0.10, 0.50)` ⇒ ratios `10 < 20` ⇒ filter 1 first despite being less selective. What *is* safe to say is the converse: **a cheaper filter is not automatically first.** A filter with a near-zero rejection rate has a near-infinite ratio and correctly sorts **last**; at `r = 0` it is pure cost and should not run at all. **Consequence for this pipeline: no stage may be placed by cost alone — an unmeasured `r` makes the rule silent, not permissive.**

Corroboration (3 independent source categories; none is the sole support of the claim, and one of the three carries an unverified id): the filter-sequence-ordering literature defines the ratio **`ρᵢ = cᵢ/(1−pᵢ)`** — deliberately a *different symbol* from our `rᵢ` — and shows that "a cheaper filter is not always better if it is less selective" (IR local-optimality criteria, `cs/9809121`); filter-sequence optimization over `(c, p)` with parameters estimated from past statistics (DTIC ADA279603); and a modern applied cascade using an explicit selectivity-over-cost ordering (Task Cascades — **⚠️ unverified id**, see the source note). Cascade-taxonomy work independently reports that **greedy cost ordering does not optimize the cost-accuracy tradeoff** (Raykar KDD 2010; Emory cost-aware classification; CASCARO).

### 1.2 The preconditions — where the ratio rule stops being valid [HIGH]

The interchange argument is exact only under all five. **State them; they are the reason a "cheapest-first" rule of thumb keeps failing in practice.**

| # | Precondition | What breaks it here |
|---|---|---|
| P1 | **Independence / stationarity** — `rᵢ` is the same on the surviving sub-population | **The common failure.** Selectivity measured on the full set overstates it on a correlated earlier filter's survivors. Filtered-vector-search work reports predicate↔vector-space correlation changing the qualified-data distribution; DB optimizers live on this. |
| P2 | **Additive, per-item, constant marginal cost** | **Partly false here.** The kNN/LLM legs are *batched*, so the marginal cost of the 100th item ≠ the 1st; a stage that changes batch size changes the next stage's unit price. |
| P3 | **Order-commutativity of correctness** — running filter 2 on filter 1's survivors yields the same set as filter 2 alone | **False wherever a data dependency exists** (§1.3). Where false, the order is *pinned*, and the ratio rule only orders the remaining free pairs. |
| P4 | **Comparable item semantics** — every stage is a predicate on the same item | Extraction *transforms* the item; it is not a filter, so it is ordered by dependency, never by ratio. |
| P5 | **Greedy = global** | Only under P1–P3. With interactions the optimum needs the joint distribution — the reported reason greedy cascades are suboptimal. |

### 1.3 The second axis the doc is missing: **losslessness ranks above cost** [HIGH]

Cost is not the only ordering axis, and "subject to correctness" is too weak a name for the second one. Each stage has a **precision** on what it removes. Two consequences:

- **Among stages of comparable cost, the precision-1 (lossless) stage goes first.** It can only help, and it shrinks the population an imperfect stage must judge. This is *why* exact dedup outranks the mechanical gate even though both are ≈0 cost: dedup is provably lossless on identity, the gate has known false positives (#4899: *"a PR number a decision turned on is not [discardable]"*).
- **A stage with precision < 1 buys cost savings with unrecoverable loss.** Its false negatives are never seen by later stages that could have rescued them — the premature-filtering failure mode reported for candidate-set retrievers ("true nearest neighbours that satisfy the filter may never enter the candidate set"). So a precision-<1 stage must be either (i) placed after every lossless reduction, or (ii) gated with a recorded reason and a recoverable counterfactual — **and (ii) does not make its error visible; only a sample does (§6).**

**Sharpened rule — use this one:**

> **Order by cost per unit of *SAFE* rejection ascending, where "safe" means the stage's precision on what it removes is provably 1.0. A stage with precision < 1.0 runs after every lossless stage, behind an audit gate — and its admissible actions are KEEP / NOOP / DISCARD, never FUSE.**

### 1.4 The objective has a third term the doc ignores: **error compounding** [MEDIUM] ⚠️ emerging

A cascade's recall is the **product** of its stages' **recalls** (not their precisions — precision and recall are independent, and a 95%-precise, 100%-recall stage loses no true positives). Two ≈0-cost stages each at 95% **recall** cost you `1 − 0.95² ≈ 10%` of true positives before any expensive stage is reached, and nothing in the pipeline measures it. Reported for cascaded classifiers as the pass-on-probability/compounding effect. **Consequence:** the pipeline needs a **recall floor**, not just a volume target — and the doc's target ("~10× fewer items", from n=28, 3 kept) is volume-only, hence gameable by dropping everything (review A5/B2.16). **This is a real gap, not a nuance.**

---

## 2. Where dedup belongs — four dedups over four keys [HIGH]

### 2.1 The convergent ETL/RAG guidance: dedupe at the earliest boundary where the key is defined, before the expensive stage

Independent source categories agree:

- **Document-level first, then chunk-level**, hash **before embedding**, skip already-embedded content (RAG ingestion practice, two independent vendor guides).
- **Normalize → deduplicate → chunk → embed** (Unstructured's pipeline guidance).
- **Deduplicate documents as a preprocessing step**, MinHash for near-dupes, *before later pipeline stages* (Databricks/Azure RAG cookbook).
- **Exact → MinHash+LSH → embeddings+clustering**, in that order, with byte-exact first and fuzzy "optional, downstream" (NVIDIA NeMo Curator; and a byte-exact/MinHash paper calling them **complementary, not competitive**).

**⇒ Standard practice is dedupe-early, and the reason is the cost argument of §1 — dedup removes work from a stage that is priced per item.** Applied here, the boundary that matters most is the one the doc does not have: **the source (session/document) boundary, which sits *before* the dominant-cost extraction call.** A re-arriving identical source is caught for one hash and saves the largest cost in the pipeline entirely. The doc's dedup first appears *after* extraction, where the dominant cost has already been paid.

### 2.2 The counterargument that constrains it: the dedup key must exist [MEDIUM] ⚠️ emerging

The adversarial pass returns the correction, and it is a **dependency**, not a cost claim:

- *"A cascade pipeline should usually deduplicate after extraction, not before, if the deduplication key depends on extracted entities, relationships, or normalized fields — otherwise you risk dropping records before the extractor has produced the evidence needed to merge them correctly."*
- Practical rule offered: **extract → normalize → dedup on canonical representations → merge metadata/provenance explicitly → apply aggressive cost filters only after high-value extraction signals exist.**

**Reconciled:** both are right because they are about *different keys*. Dedup on a key that exists at the boundary (raw content hash) belongs at the boundary. Dedup on a key that only the extractor produces (normalized content, entity set, kind) belongs after extraction. **The correct statement is not "dedup is early" or "dedup is late" — it is "each dedup sits at the earliest point where its key is computable."**

### 2.3 The four dedups this pipeline needs — two of which it does not have yet [HIGH]

| # | dedup | key | earliest computable | precision on identity |
|---|---|---|---|---|
| D-a | **Source / document** | **URL** today (`MATCH (s:Source {url:$url})`, `sdk.py:8538`) — a **content-hash refinement is the proposal**: `derive_source_content_hash` (`file_indexer.py:315`) is a *provenance integrity anchor* (#4005, *"Integrity anchor of the RAW (not of the identity url)"*), **not a dedup key**, and no content-hash dedup runs pre-extraction | merge exists; **content-hash dedup before extraction does not** | **1.0** (URL) / 1.0 (proposed hash) |
| D-b | **Candidate exact, within-batch** | `_norm()`-normalized content in the in-memory batch — `n = _norm(content); if n in point_ids: continue` (`extractor_v2.py:3967-3969`) | after extraction | **1.0** |
| D-c | **Candidate exact, against the store** | **raw** content hash — `MATCH (n:Point {content_hash:$ch})` (`sdk.py:12375`) + a hash-less `content`+`pointKind` fallback | after extraction — but the store is not reachable today (below) | **1.0** *(raw-identical content only)* |
| D-d | **Semantic consolidation** (near-dup + entity-resolved — *not* a separate LSH rung; see §4) | `_norm` + `_token_overlap` / `_value_signature` / entity & attribute gates / `_fact_value_contradiction` (`extractor_v2.py:3021+`, step-1 `_norm` match at `:3065` — the **against-priors** normalized check) — **the matcher reads no embedding**; embeddings live in the separate retriever that supplies `priors` | only after the canonical key exists | **< 1.0** |

**Two content variants, so four keys not three** — and the difference between D-b's and D-c's key is load-bearing, not cosmetic. **⚠️ D-a's existing form is URL-keyed, so the pre-extraction content dedup §7 shows as `NEW S0b` genuinely does not exist.**

**Reviewer B2.3 is directionally correct and the doc's constraint-1 reasoning is wrong — but the convenient form of the claim ("one indexed hash lookup, exactly as cheap as within-batch") is NOT supported by the code. The hoist is a *proposal* whose acceptance conditions are the four below; the two observations that motivate it come first.**

**Observations (not conditions):**
- **The machinery exists — at the LAST stage.** `create_point(dedup=True)` resolves duplicates through `_find_point_by_content` (`sdk.py:12312`) — documented as *"the SINGLE source of truth for content-dedup resolution, shared by `create_point`, the v2 capture seam, `ingest_bundle`'s points loop, and `_content_exists`"* — whose primary clause is a direct node predicate, `MATCH (n:Point {content_hash:$ch})` (`:12375`). So the doc's "deferred to the very last stage" is accurate.
- **It is a cost lever of unmeasured size, not a volume lever** — see the corollary below.

**Acceptance conditions (all four must be met before D-c moves):**

1. **Key unification + tenancy scope.** D-c matches the **raw** content hash (`ch = _content_hash(content)` / `props["content_hash"] = ch`, `sdk.py:2822-2823`) while D-b and D-d match **`_norm()`-normalized** content. A raw-hash pre-check therefore **misses** normalized-equal-but-not-identical content. Also, the two "content ids" are different ids: `_content_id` truncates to 62 hex chars (`extractor_v2.py:2560-2562`) while `commit_schema.point_content_id` (`:1171`) uses the full 64. And `content_hash` is an **unsalted global** hash while graphs are per-tenant (`org_{id}` / `org_selfhost`) — so the proposal must name **which key is canonical**, how the two relate, and **whether it is tenant-salted** (§5).
2. **Sound-only.** A hit is decisive; a **miss is not a verdict of "new."** An early miss must fall through to the later normalized check. Never mark `new` from an early-stage miss (the failure shape of review B2.13: an S3 lookup error that fail-opens to `new` *manufactures* duplicates).
3. **Union-merge, never drop.** The check emits a *fold/attach* instruction that S3 executes, so attachments are unioned onto the existing point (B2.5's fix; §3).
4. **⚠️ An atomic write — which does not exist today.** *"A miss is not a verdict of new"* prevents a **wrong** verdict; it does **not** prevent two concurrent sessions from both missing and both creating. And the cited mechanism is **itself a read-then-write**: `if dedup:` (`sdk.py:2866`) → `_find_point_by_content` (`:2873`) → `CREATE (n:Point …)` (`:3092`), with **no uniqueness constraint on `Point`** (the only `GRAPH.CONSTRAINT … UNIQUE` in the tree is on `GraphEvent`, `event_store.py:52`) and only in-process `threading.Lock`s (`_source_merge_lock_for` `:1858`, `_index_run_lock_for` `:1747`) that key on neither `content_hash` nor a second process. **⇒ TOCTOU is an unresolved dependency of the proposal, not a satisfied condition** — it requires a uniqueness constraint / `MERGE` on the canonical key, or a cross-process lock keyed on the key.

**⚠️ Plus a plumbing cost the doc would otherwise miss.** `_find_point_by_content` is a `TortoiseSDK` method needing graph access, but the stage it would be hoisted into is **pure by construction**: `execute_embed(embed_list, search, *, …)` (`extractor_v2.py:3724`) receives no SDK and drives `classify_consolidation` from an already-materialised `search` dict — the seam `#4899` itself describes as *"a pure function ('no LLM, no graph I/O')"*. **Running an against-store hash query at S2.2a therefore requires threading store access (or a pre-fetched candidate-key set) into a stage that has none today — plumbing, not a call move.** Say so, and size it.

**Placement:** **D-a before extraction; D-b and D-c together immediately after extraction; D-d at its dependency point.**

**⚠️ Corollary for the 10× target.** Each of D-a/D-b/D-c suppresses a node that would otherwise have been written (no candidates ⇒ no writes; `continue` at `:3968`; NOOP at `:4017`), so **each does reduce store growth by its duplicate rate** — the partition is *not* cost-versus-growth, and the earlier framing of it as such was wrong. What makes exact dedup **not** the ~10× lever is **magnitude**: the duplicate rate is **unmeasured for all three legs**, and the honest reading is probably **marginal** — content-identical duplicates were largely already caught by the later against-store normalized check (D-c/D-d), so the hoist's *incremental* growth effect is small even where its cost effect is not. The target must come from the salience gate and D-d, against review B2.8's ~31.5% ceiling.

### 2.4 Why not re-use the more absolute wording?

Because the field's own ladder (§4) is a **precision** ladder, and this pipeline's dedup distance is a **key** ladder. They coincide on "exact first" and diverge after that: the pipeline's near-dup/semantic work is `_norm`/token/value-gate based, not embedding-based, and it is constrained by an owner ruling that fusion is out (§3).

---

## 3. The merge rule when records differ beyond identity [HIGH]

**The standard is field-level survivorship, and the choice is by attribute arity.** Master-data/entity-resolution practice:

- **Single-valued attribute** → a **survivorship rule** picks one value: **Most recent**, **Most frequent**, **Source priority** / trusted source, most complete. (Stibo, Hightouch, Semarchy all state these.)
- **Multi-valued attribute** → **union** the values from all source records (Stibo: *"For multivalued attributes, values may be unioned from all source records or governed by rules"*).
- **Never delete the source records** — matching **links** them into a golden record (*"each source record belongs to exactly one golden record"*), preserving provenance for the non-surviving values. Erasing the constituents is the anti-pattern; the golden record is a *view over* them.
- **ID survivorship is a separate decision** from attribute survivorship (Semarchy) — which maps to our content-addressed point id: identity is content, so the id is cheap and stable, but it must not be the *only* thing carried.

**Applied here:** the record is a pair `(identity, attachment-set)`. Identity is content (`_content_id`) → single-valued, and exact equality makes it unambiguous. **Attachments are multi-valued by nature** — several entities mentioned, several source refs, several quotes, several search keys — so **the merge is a UNION of attachments**, never "keep the richest record" and never "first wins."

**Two things fall out that the doc and the review both miss:**

1. **"Keep the richest record" is the wrong rule for exactly the fields at issue.** It is a *single-valued* survivorship heuristic; applied to set-valued attachments it discards values by construction. Union is both the standard and the order-independent choice — which matters, because winner-take-all makes the stored attachment set depend on **batch order**, i.e. a non-deterministic write.
2. **Exact identity is an equivalence relation; semantic similarity is not.** Pairwise fuzzy matches can chain (A~B, B~C, A≁C) and a transitive closure over-merges. Exact dedup cannot over-merge. **This is a second, independent reason the exact leg is safe to hoist and the semantic leg is not** — and it means D-d's clustering decision must be made on the *cluster*, not accumulated pairwise.

**⚠️ A recorded owner ruling makes this pipeline's merge rule NARROWER than generic MDM.** `#4899` carries an `OVERRIDES:` marker (**owner, 2026-09-23**): *"Any volume reduction on this issue MUST come from the DISCARD outcome (not writing a claim), **never from merging two claims into one**"* — because *"a fused claim cannot be argued about."* The three mechanical reasons given are decisive here: operators connect only epistemic targets (you cannot `NAND` a paragraph); EP confidence propagates over a factor graph of **atomic** claims (a fused claim hands one confidence number to several distinct beliefs); and relevance modulation requires the claims be separable. **⇒ A duplicate may be NOOP-linked** (same claim ⇒ do not re-write it; **union its attachments** onto the existing Point) **or DISCARDed** (different claim, not worth writing) — but two *distinct* claims must **never be fused into one content string.** The standard ER "union" applies to the **attachment set**, never to the **claim text**. This is the single most important constraint on the semantic tier, and it is what a "dedup merges records" framing does not observe.

---

## 4. Exact vs semantic dedup — where each belongs [HIGH]

Convergent, three independent categories:

- **NVIDIA NeMo Curator** prescribes the ladder: **exact → MinHash+LSH → embeddings+clustering.**
- **Milvus** frames the same three (exact / approximate LSH / semantic) and warns approximate methods can return **false positives or miss close matches.**
- **Fraunhofer** evaluation across algorithms: precision **0.833–0.985**, recall **0.247–0.989** — i.e. the fuzzy tier's precision is *not* safe by default.
- A RAG-specific paper calls byte-exact and MinHash-LSH **complementary, not competitive**, and places byte-exact first with fuzzy "optional… downstream."

**⇒ Exact dedup is early because its precision is 1 and its cost is a hash; fuzzy/near-dup is late, gated, and never in front of a stage whose output it needs. The doc's constraint 2 ("non-exact dedup needs semantic judgement ⇒ only in VET — never before it") is directionally right and its reason is right; it is stated as a prohibition on a single pass rather than as a ladder.**

**⚠️ A near-dup detection rung (LSH/MinHash) between exact and semantic is the standard ladder's middle rung — but its admissible form here is narrow.** What the `#4899` owner ruling forbids is **fusion-based** volume reduction: *"never from merging two claims into one."* It does **not** forbid reducing volume by *not writing* — that is the ruling's own sanctioned mechanism (`DISCARD`), and NOOP-link (same claim ⇒ don't re-write) is the same shape. So:

- **Permitted:** near-duplicate *detection* that produces **(a)** a NOOP-link (same claim, do not re-write; union attachments) or **(b)** a DISCARD candidate handed to the salience gate — because both reduce volume by **not writing**, which is exactly what the ruling requires. This means a near-dup rung **is** a volume lever, and legitimately so.
- **Forbidden:** any near-duplicate *fusion* — collapsing two records into one merged content string. That is a **reopen of `#4899`, not an adoption over it.**
- **Either way:** the rung must carry the same three controls `#4899` requires for its gate (flag default-off, rule/reason recorded, counterfactual recoverable) **plus an anti-collapse guard** (`keep` on ANY difference in a number/quantity, a named entity or language, a negation, or a condition — the rule the architecture doc already records from the field).

**⇒ Corrected reading: the standard ladder's *ordering* (exact → fuzzy → semantic) transfers; only its *fusion* semantics changes, and the change is an owner ruling, not a preference.**

---

## 5. Adversarial findings — how this ordering fails [HIGH]

| failure | what it looks like here |
|---|---|
| **Proxy for objective** | "cheapest first" orders a zero-rejection filter first and buys nothing; the doc's own list contains a ≈0-cost stage at position 5. |
| **Correlation breaks the ranking** | `rᵢ` measured on the full candidate set overstates selectivity on survivors of a correlated earlier stage. Any `rᵢ` in §1.1 must be **re-measured on the surviving population**. |
| **Greedy ≠ optimal** | reported directly: greedy cost-efficient cascades do not optimize the cost-accuracy tradeoff; CASCARO uses reward-based search for the optimal order. |
| **Error compounding** | recall multiplies; two 95%-*recall* ≈0-cost stages cost ~10% of true positives silently. |
| **Premature filtering** | an irreversible drop; the lost item never enters a later candidate set. The doc's gate has **known** false positives (#4899). |
| **Unmeasured denominator** | a 10× volume target with no recall floor is satisfiable by discarding everything (review A5/B2.16). |
| **Non-determinism** | winner-take-all merge makes the stored attachment set batch-order dependent. |
| **Reordering breaks a guarantee** | the doc warns any reorder that moves #4899's gate must preserve gate-flag + reason-id log + recoverable counterfactual. **The same discipline must apply to a hoisted D-c**, and the doc doesn't say so. |
| **TOCTOU on lookup-then-create** | **unresolved, not solved.** `create_point(dedup=True)` is itself a check-then-`CREATE`; there is no uniqueness constraint on `Point` and no cross-process lock on the key (§2.3 condition 4). A hoist must not inherit this. |
| **Tenancy scope of the key** | the runtime namespaces graphs per tenant (`org_{id}` / `org_selfhost`) while `content_hash` is an **unsalted global** hash; if any surface ever holds these rows together, an unsalted key collides across tenants. Each of D-a…D-d must state its scope and whether the key is tenant-salted. |

---

## 6. Second question — gating an expensive filter so it can be trusted [HIGH]

**Verdict: yes to all three of the reviewer's propositions — flag-gating, per-rejection reason logging, and a recoverable counterfactual. All three are already recorded decisions on `#4899` (with a fourth, two-directional regression tests), so the research *confirms* them rather than introducing them. It adds one requirement whose status is a *clarification to raise*, not a settled adoption.**

**Already decided (recorded on #4899, so adopt = confirm, not introduce):**
1. **Flag-gated, default-off on merge** — *"so the rate effect is measurable before it is trusted."*
2. **Every discard logged with the rule that caused it** — *"Every discard is recorded in the result-level channel with its reason, so the gate's own precision is auditable and the counterfactual is recoverable."*
3. **Counterfactual recoverable** — a result-level channel *"not written to the graph"*, plus a *"counterfactual report is producible."*
4. **Regression tests pin both directions** — the decision-relevant value survives **and** the bare process mechanic is discarded.

**Standard practice, corroborating:** **shadow mode / dark launch** — run the candidate in parallel, observe-only, log what it *would* have decided, *then* decide enforcement; phased rollout that measures **acceptance and override rates** before widening autonomy; and durable structured logging with audit trails. Shadow mode is the missing *validation mode* between "flag is off" and "trusted."

**The one addition, and its exact status:**

**A systematic random sample of the REJECTED population, human-labelled.** The reason it is needed: a reason-log records the decisions the gate **made** — `#4899` is right that this makes the gate's decisions *auditable* and the counterfactual *recoverable* (you can replay what it dropped and why). What a log of decisions cannot supply is a **denominator for omissions**: a record the gate never evaluated, or one it kept, is not in the reject log at all, and a false negative is only *observable* by looking at a sample of what would have been dropped. So the sample **supplements** the recorded rationale rather than denying it — the log gives the *what and why*, the sample gives the *how often it was wrong*.

**⚠️ Status: this is a proposed clarification to `#4899`'s stated rationale, and it must be raised there, not adopted silently.** `#4899` records that reason-logging makes the gate's *"own precision … auditable"*; a precision estimate strictly needs the sample. Whether that is a genuine conflict or a loose use of "auditable" is the owner's call, on the issue — **not a conclusion this brief may settle.** `#4899`+`#4894` already require an owner-reviewed sample of rows (small-sample-first / evidence-first approval), and those verdicts seed the labelled regression set (`tests/eval/write_path/salience_labels.jsonl`); the increment is that the sample be drawn at random **from the rejects specifically**.

**Reversibility is the strongest form of counterfactual.** Prefer *recompute* over *predict*: record the pre-filter population rather than predicting what it would have contained. This is the same conclusion the merge rule reaches independently (§3: link, never erase) and the same one the storage doc reached for invalidation (*"moves the row out of the active table… no state predicate"*).

---

## 7. Recommendation — the ordered pipeline

*(A recommendation, not a claim: its tiers are inherited from §§1–6. Two positions are marked **conditional** below, and the one element that contradicts a recorded decision — near-dup fusion — is routed to a reopen in §4 and the adoption gate.)*

**Stage numbering — map, do not renumber.** The architecture doc's labels are already cited in posted comments on `#4894`/`#4899`, so this brief **keeps them** and marks new stages with a `NEW`/`β` suffix: doc S2.2a = exact dedup · doc **S2.2b = VET** · doc **S2.3 = CLASSIFY**. `#4899`'s mechanical `DISCARD` is **not a new stage** — it is an outcome added to `classify_consolidation` (E7).

| # | stage | one-line reason for its position |
|---|---|---|
| **NEW S0a** | **NORMALIZE + HASH the source** | the dedup key exists before the dominant cost — a re-arriving source is caught for one hash. |
| **NEW S0b** | **SOURCE DEDUP (exact, vs source store)** | precision 1.0 and it removes the single most expensive item class (the whole extraction call) — the cheapest safe rejection available. |
| **S1 NARRATE** *(unchanged)* | the first model call | unchanged by this brief; listed because an "order" that omits a listed stage is not an order. Its bounded re-narrate retry is the only feedback edge (§0 A1). |
| **S2.1 EXTRACT** (dominant cost) | | pinned by dependency, not cost: every later dedup/classify key is produced here, and nothing upstream can filter what does not yet exist. |
| **S2.2a EXACT DEDUP** — within-batch **and** against the store *(store leg = **β**, proposal, 4 conditions §2.3)* | | justified by the **losslessness override** (§1.3), not by `c/r` — its duplicate rate is unmeasured, so a `c/r` ranking is **conditional** on that rate being non-trivial (§1.1). |
| **NEW S2.2a-β NEAR-DUP *detection*** — flag for NOOP-link / DISCARD, **never fuse** | | cheap token-level fuzz that finds what hashing cannot without embeddings; carries the anti-collapse guard and `#4899`'s three controls (§4). |
| **GATE (mechanical `DISCARD(reason=…)`)** *(brief label: **S2.2b-mech**; the doc calls it *"the mechanical half of the gate"*; `#4899` places it in **E7**)* — **position conditional (D6)** | | ≈0 cost and high rejection on a well-populated class; placed after exact dedup because it is lossy where dedup is not. **Whether it should be pre- or post-lookup is unresolved (review D6) — this position is provisional.** |
| **S2.2b VET** (doc's number) — small-model adversarial gate | | a precision-<1 stage ⇒ after every lossless reduction and before the remaining expensive ones. Its false negatives are **not** more observable than the gate's: **every precision-<1 stage's errors require the §6 rejects sample**, so VET's position rests on `c/r` + the losslessness override, not on observability. |
| **S2.3 CLASSIFY** (doc's number) — kNN fast path → small-model tail, **survivors only** — **position conditional (B2.1)** | | cheap fast path ⇒ after the gate (classifying a discard is wasted *and* pollutes the classifier's signals). Its position before RESOLVE is **NOT** established by "the lookup needs kinds" (review B2.1: false) — only by the chain enforcer / typed refs. **Re-derive before relying on it.** |
| **S3 RESOLVE → then D-d SEMANTIC CONSOLIDATION** | | needs the canonical key (extraction output) and, for the entity leg, the lookup ⇒ cannot precede S3; its **cluster-level** decision is the one semantic merge and must be union-merged, never fused, and audited. |
| **S5 CONNECT** | | consumes resolved ids and kinds. |
| **S6 COMMIT** | | the only writer; every drop above it must be recorded, not erased. |

**Changes from the doc's order, in order of consequence:**

1. **Add S0a/S0b** — dedup at the input boundary, before the dominant cost. The doc's first dedup is *after* the expensive call; **and today's source merge is URL-keyed (`sdk.py:8538`), with `derive_source_content_hash` a provenance anchor, not a dedup key** — so this is a genuinely absent lever, not a re-labelling.
2. **Extend S2.2a with the against-store leg (β)** (B2.3) — **as a proposal**, four conditions: key unification, sound-only, union-merge, **atomic write (which does not exist today — build it or fold the store probe into the S3 lookup where graph access already exists)**. Plus store access must be threaded into a currently-pure stage. **This is a reordering *plus* plumbing plus a new write guarantee — not "a call move."**
3. **Add the near-dup rung as DETECTION ONLY, never fusion** — the standard ladder's middle rung, admissible here only in the form that respects `#4899`'s `OVERRIDES:` ruling. It **is** a legitimate volume lever by NOT-WRITING (NOOP/DISCARD), which is the ruling's own mechanism.
4. **Replace the rule** with `c/r` + losslessness-over-cost + dependency-pins, and require `r` to be **measured on the surviving population** before any stage's position is settled by ratio.
5. **Restate constraint 4** — CLASSIFY-before-RESOLVE is not supported by the code (B2.1).
6. **State the merge rule** — union attachments, link sources, never fuse; determines determinism.
7. **Mark the two conditional positions** (GATE: D6 pre/post-lookup; CLASSIFY: B2.1 re-derivation) so the table does not read as settled where it is not.

**Residual open questions (not resolved by this research):**
- Whether the mechanical gate belongs pre- or post-lookup (review B2.4/B2.22/**D6**) — §1.3 says a lossy stage belongs after lossless ones, which argues *post*-lookup if the lookup is exact-matching; it does not settle whether *relevance* needs the lookup.
- Whether CLASSIFY must precede S3 at all (B2.1/B2.2 — S3 and S5 appear to ask "duplicate?" twice).
- The per-class reduction budget, the **duplicate rate** per dedup leg, and the **recall floor** (A5/B2.8/B2.16) — measurement tasks, not research ones.

---

## Source confidence summary

**Tier rule:** HIGH = 3+ independent source categories (or a self-contained derivation); MEDIUM = 2 (`⚠️ emerging`); LOW = 1 (`⚠️ single-source`); speculative = 0 (`⚠️ hypothesis`).

| claim | tier | sources |
|---|---|---|
| `cᵢ/rᵢ` ascending is the optimal filter order; "cheapest first" is its special case | **HIGH** | derivation (this brief) + IR local-optimality + DTIC filter-sequence + applied selectivity-ordering ⚠️ (one unverified id) |
| ratio rule fails under correlation / non-additive cost / dependency | **HIGH** | filtered-vector-search + cascade-taxonomy ⚠️ + §1.1 derivation |
| greedy cost-ordering ≠ cost-accuracy optimum | **HIGH** | Raykar KDD 2010 + Emory + CASCARO |
| dedupe early, before the expensive stage (document→chunk) | **HIGH** | RAG ingestion guides ×2 + Unstructured + Databricks |
| counter-constraint: dedup key must exist ⇒ post-extraction for entity-dependent keys | **MEDIUM** ⚠️ emerging | adversarial search synthesis (corroborated by §2.3's key analysis) |
| exact → near-dup → semantic ladder (ordering) | **HIGH** | NVIDIA NeMo + Milvus + Fraunhofer + RAG dedup paper |
| field-level survivorship; union multi-valued; link, never erase | **HIGH** | Stibo ×2 + Hightouch + Semarchy |
| near-dup fusion is forbidden here | **HIGH** | `#4899` `OVERRIDES:` owner ruling (primary, verified) — *no external source needed* |
| cascade recall compounds multiplicatively | **MEDIUM** ⚠️ emerging | cascaded-classifier literature ⚠️ (unverified id) |
| shadow mode + reason-code logs + recoverable counterfactual + override rates | **HIGH** | MLOps/practice sources ×3 + the recorded `#4899` decisions |
| an independent sample of rejects is needed for a precision estimate | **MEDIUM** ⚠️ emerging | shadow-mode validation pattern + override-rate practice (in tension with `#4899`'s rationale — see §6) |

**Sources — verified / unverified.** **⚠️ Unverified (treated as unconfirmed, none is the sole support of a HIGH claim):** arXiv `2606.07589v1`, `2605.09611v1`, `2602.11443`, `2601.05536v1`; and **Task Cascades**, **cascade-taxonomy**, and the **cascaded-classifier** literature — returned without a checkable id. §1.1 is a self-contained derivation; the ratio rule is additionally carried by IR local-optimality + DTIC; the dedup ladder by NVIDIA NeMo Curator + Milvus + Fraunhofer; survivorship by Stibo + Hightouch + Semarchy; the RAG placement by two vendor ingestion guides + Unstructured + Databricks; the audit practice by three MLOps/practice sources + the recorded `#4899` decisions.

**Verified source key:** IR local-optimality = `arxiv.org/html/cs/9809121`; DTIC filter-sequence = `apps.dtic.mil/sti/tr/pdf/ADA279603.pdf`; filtered vector search = `vldb.org/pvldb/vol18/p5488-caminal.pdf`; Raykar KDD 2010 = `umiacs.umd.edu/labs/cvl/pirl/vikas/publications/raykar_kdd2010_cascade_v3.pdf`; Emory cost-aware = `cs.emory.edu/~lzhao41/materials/papers/08880522.pdf`; CASCARO = `hal.science/hal-03294049/document`; NVIDIA NeMo Curator dedup = `docs.nvidia.com/nemo/curator/curate-text/process-data/deduplication`; Milvus MinHash-LSH = `milvus.io/docs/minhash-lsh.md`; Fraunhofer dedup evaluation = `publica.fraunhofer.de`; Stibo survivorship = `doc.stibosystems.com/doc/version/latest/web/content/mtchlnkmrg/survivorship/grsurvrules.html`; Hightouch golden record = `hightouch.com/docs/identity-resolution/golden-record`; Semarchy survivorship = `docs.semarchy.com/sdp/dm/certification/match-merge/survivorship-rules`; RAG content-hash guides = `app-lab.ai/blog/content-hash-deduplication/` and `datavidhya.com`; Unstructured = `unstructured.io/insights/rag-systems-best-practices-unstructured-data-pipeline`; Databricks RAG cookbook = `learn.microsoft.com/en-us/azure/databricks/agents/tutorials/ai-cookbook/quality-data-pipeline-rag`; shadow mode = `github.com/shimo4228/agent-observability-patterns` (`skills/shadow-mode-validation`) and `venturebeat.com/orchestration/shadow-mode-drift-alerts-and-audit-logs-inside-the-modern-audit-loop`; override/acceptance rates = `augmentcode.com/guides/ai-bug-triage`; MLOps shadow logging = `mljar.com/ai-prompts/mlops/`.

**Adoption gate (Step 5.6) — `adopt` in part, `reopen` in part:**

- **ADOPT:** the ordering rule (§1, replacing "cheapest first" with `c/r` + the losslessness override + dependency pins); the four-key dedup placement with **D-c hoisted as a proposal under four conditions** (§2.3); the attachment-union / never-fuse merge rule (§3); the exact→near-dup→semantic ladder **as ordering and detection, with fusion excluded** (§4). No recorded decision contradicts any of these — §4.2's owner comment is a *question* ("check the ordering makes most sense"), not a ruling, and `#4899`'s flag / reason-id / counterfactual requirements are **confirmations**.
- **⚠️ REOPEN #1 — a finding contradicts a recorded owner decision, so it is not adoptable in that form.** Near-duplicate **fusion** contradicts `#4899`'s `OVERRIDES:` ruling (*"Any volume reduction … never from merging two claims into one"* — owner, 2026-09-23). Route: reopen **on `#4899`** with the evidence. The admissible form — detection producing NOOP-link or DISCARD — is consistent and is adopted. This is the one place standard field practice (Microsoft GraphRAG's explicit consolidation step, which the ruling itself names) diverges from our decision, and the `OVERRIDES:` marker is what made the divergence findable.
- **⚠️ REOPEN #2 (clarification, not a reversal) — §6's sample requirement is in tension with `#4899`'s recorded rationale.** `#4899` records that reason-logging makes the gate's *"own precision … auditable"*; a precision **estimate** additionally needs a sample of the rejected population. This is a proposal to sharpen the rationale **on `#4899`**, with the owner deciding whether it is a conflict or a loose reading — **not** an adoption over it.
- **OWNER QUESTION: none beyond the two reopen notes above.** The residue is a **measurement** task (§7 residuals), not a decision.

---

## Verification status (Step 5.5 — cycle log, honest exit)

Three fresh-context verifier cycles ran. **The exit is not a clean `NO ISSUES FOUND`** — it is the skill's 2-fix-cycle cap, reached with findings still arriving in cycle 3. Recorded rather than smoothed over:

| cycle | findings | disposition |
|---|---|---|
| 1 | 10 (math necessity/sufficiency; D-c key claim unsupported; `_merge_key` misattribution; near-dup ungated and contradicting a recorded ruling; TOCTOU, tenancy, tiers, counts) | all 10 fixed |
| 2 | 14 (incl. `create_point` is itself check-then-CREATE so no atomic upsert exists; §6 premise contradicted `#4899`'s rationale; precision/recall slip; S-number collision with the doc; missing S1) | **v2 rewrite** — all fixed |
| 3 | 7 (**1 P1**: D-b's citation was the *against-priors* check, not the within-batch one — `:3065` vs `:3967-3969`; plus D-a is URL-keyed not content-hash; the corollary's cost-vs-growth partition; a false parenthetical; an off-by-one line; tenancy missing from the condition set; a coined `doc:` label) | all 7 fixed **by the author, not re-verified** |

**What is settled:** the mathematics (§1.1–§1.4) survived three independent re-derivations; the code citations were verified line-by-line by a verifier at `aadd68a9d`; the `#4899` `OVERRIDES:` ruling was read from the issue by two separate verifiers. **What is not:** the cycle-3 fixes have not been re-reviewed, so the D-b/D-a citations and the corrected corollary should be treated as *self-checked, not verified*. That is the honest residual, and it is recorded here rather than represented as a clean pass.
