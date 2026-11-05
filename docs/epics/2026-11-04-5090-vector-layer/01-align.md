---
title: "Strategy alignment — epic #5090, the vector layer"
type: decision
domain: planning
doc_status: draft
created: 2026-11-04
updated: 2026-11-04
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #5090, the vector layer, decision V1, embed gate, STORAGE-ARCHITECTURE.md §12"
---

# Strategy alignment — epic #5090

**Stage:** 1 of 6 (`epic-workflow` → `epic-align`). **Epic:** #5090 — *the vector layer — what earns an
embedding, and where it lives.* **Measured at head `641e5f1b37`.** **Review gate:** verifier (fresh
context) — **two cycles**, and **cycle 2 changed the decision from PROCEED to REPOINT**. The verdict log,
which supersedes this line, is **§6**.

> ⛔ **READ §8 FIRST.** It records the decisions the repo and the graph had **already settled** before this
> lane derived anything, and the three things this lane proposed that were **already refuted or on a
> do-not-re-invent list**. §1–§7 are kept as the reasoning trail; where they conflict with §8, **§8 wins**.

---

## 0. Three premises the lane brief asserts that do not reproduce

Recorded first, because the scope is wrong if they are taken on trust. Each was checked with a command.

| lane brief says | measured at this head |
|---|---|
| *"the runtime warns on every search"* | **The warning is SETUP-time, not per-search.** `_record_vector_index_inventory` (`projection/__init__.py:18583`) emits `log.warning` **once per (endpoint, graph) per process**, from `_ensure_indexes`, guarded by `_VECTOR_INDEX_GAP_WARNED`. The **query-time** fallback logs at **info/debug** (source comment cites #1359 for *"not warning"*). It *looks* per-search on any short-lived CLI process (each `goal-state.py --line` invocation is a fresh process); it is not per query. **The full-scan defect is real and is paid on every query — the warning framing is what is wrong.** |
| *"$73/GB"* as a per-byte cost of the *whole* graph | Correct as the recorded basis (`billing.cost_basis.falkordb_per_gb = 73.0`), but it must be applied to the **removable payload**, not to 195 MB. See §3. |
| the epic is *"3 of 4 DoD bullets met"* | **1 of 4 is MET.** `uv run python tools/dod_5090_probe.py` at this head: **`PARTIAL / PARTIAL / MET / NOT MET`**. Rows 1 and 2 are PARTIAL, row 4 NOT MET. |

---

## 1. Adversarial strategy test

### 1.1 What the epic claims, against what is there

| epic body says | at `641e5f1b37` | how verified |
|---|---|---|
| 141 MB graph, 45 MB indices, 25k nodes | **195 MB, 93 MB indices (48 %), 68,526 nodes** | `#10976` body, measured 2026-11-04 on graph `live-v4`; same figures as the lane brief |
| ≈50 MB of stored vector properties | **19.4 MB** (12,657 × 1,536 B = 19,441,152 B) — the 50 MB double-counted the one leg that *is* indexed | the epic's own 2026-10-08 comment |
| "no rule says what earns an embedding" | **the rule exists**; it is off by default | `embed_gate.RULE_SET is value_gate.RULES` → `True`; `embed_gate_enabled()` reads `TORTOISE_EMBED_GATE`, unset in the **4** shipped config surfaces that exist (the instrument walks 6; `docker-compose.yaml` and `Dockerfile` are *absent* from this tree) |
| "12,657 embeddings stored and unindexed" | **still true in substance** — index creation covers `Point` only | `projection/__init__.py:19765` (`createNodeIndex('Point',…)`), `:19783` (`CREATE VECTOR INDEX FOR (p:Point)`); no other label's vector DDL in `tortoise/`, `graph-scripts/`, `tools/`. (`graph_key_restore.py:452` **replays** a dumped catalogue; it never decides.) |
| a fallback query reports the fallback | **MET** | `#4999` / PR #5159 added `mechanism: index \| scan \| scan_fallback` |

**The labels, precisely.** The served/written set is `{Event, Object, Point, Source, Subject}`
(`ENTITY_TYPE_LABELS`). `Point` is indexed ⇒ **four** labels are unindexed ⇒ *"index them in the graph"*
means **four further resident HNSW structures**, not three. ⚠️ `STORAGE-ARCHITECTURE.md` §12.1c still says
*"a SECOND … and a THIRD"*; that count is wrong and is corrected in **§7.2**, not here.

### 1.2 Alternatives considered

| # | alternative | verdict |
|---|---|---|
| **A1** | **Discharge the epic as complete.** | **Rejected — on one reason, and it is the only one that survives checking.** Not *"closing would orphan `#4333`"* (that issue is independent; closing a parent removes a link, not the issue). Not *"it abandons row 4"* (V1 is carried OPEN by `STORAGE-ARCHITECTURE.md` §12.1c/§14.3, not by the epic). Not *"no capstone exists"* (a capstone is a Stage-6 artifact and cannot exist for an epic still in Stage 1 — its absence proves nothing). **The reason is `§14.3 V3`, whose row reads *"Rides on V1"*: the epic is the only ACTIONABLE record of that deferral.** ⚠️ **Cycle-2 withdrawal: the earlier wording claimed the epic was the *only* artifact binding `#4997` to V1. That is false** — `STORAGE-ARCHITECTURE.md:1520` states it, and so does the runtime warning text at `projection/__init__.py:18583-18586` (*"This is the gap that decision V1 … governs; indexing those labels is NOT done here"*). The epic is not the only *record*; it is the only one that can be **closed, reassigned or repointed** — a doc row and a log string cannot. Discharge it and the unindexed-label defect keeps its prose and loses its work item, which is how a deferred defect becomes an accepted one. **What follows is REPOINT (§7), not PROCEED and not discharge.** |
| **A2** | **Index the four unindexed labels in FalkorDB.** | **Rejected, and the brief forbids it without a stated non-reversal.** It is V1's content; it commits **four** further permanently-resident HNSW structures on a store billed at provisioned RAM with no eviction; and it deepens the thing the epic exists to undo. Measured benefit at today's volume: **~6 ms** (`2.74 ms` indexed vs `9.26 ms` full scan of 7,859 `:Object` vectors). |
| **A3** | **Answer V1 = move the vectors to Postgres now.** | **Rejected as premature, and the facts are worse for it than the epic assumes.** Verified: **no `pgvector` anywhere** (`grep -E 'pgvector\|vector(\|hnsw\|create extension' supabase/migrations/ tortoise/record_*.py` → zero); embeddings live in `embedding jsonb` on `point`/`source`/`event`; **`public.object`/`public.subject` have no embedding column and a CHECK forbidding one.** So branch 2 is build-from-nothing at the schema layer. And the record is **not armed** — but ⚠️ **not because the owner withheld it**: the owner **authorised arming on 2026-11-03T19:06Z**, and `#9548` is OPEN because lane `hosted-arm-9548` returned `BLOCKED-CRED / BLOCKED-ENV`. That is a **fixable credential/host wall**, not a scheduling signal — which makes it a *transient* blocker, and makes the sequencing argument (record before vectors) sharper, not softer. |
| **A4** | **Fold #5090 into `#10976` / `#7879`.** | **Rejected.** Three different claims: the cache's *egress*, the record's *authority*, the vector layer's *shape*. `#10976` names `#5090` as related, not parent; neither epic's acceptance covers *"what earns an embedding / where the index lives"*. |
| **A5** | **Reduce the epic to evidence + decision carriers, and let the neighbours dissolve the cost.** | **ADOPTED as the shape of the remainder.** If `#10000` removes raw turns from the graph and `#10976` bounds residency, a material share of the vector class leaves with them — so the epic's own job is the *shape* question, not the *volume* question. |

### 1.3 Anti-post-rationalisation — the strongest case NOT to proceed

- **The epic may be dissolved rather than solved.** Turns were ~80 % of a measured graph (`#10000`) and
  dominated the vector class. Ingress plus egress work could shrink the bill without the epic deciding
  anything.
- **The measured case for indexing is weak today.** ~6 ms, against four resident HNSW structures on a
  store that charges provisioned RAM with no eviction. §12.1c already concludes that is not worth it.
- **Most of what is left is an owner decision or a document.** Filing issues where the work is already
  owned is motion, not progress.

**The counter-case, and why it wins:** two of the four DoD rows are PARTIAL on *the same population*
(the 15 embedding writers with no recognised producer call), `#10541` — the issue that was to resolve
them — closed **COMPLETED** on 2026-11-04T00:49Z and **both rows are still PARTIAL at this head**. That is
a genuine, currently-unfiled gap: an issue closed as complete whose own remainder outlived it. Proceeding
is justified **only** to the extent the work either (a) makes V1 answerable, or (b) closes a genuinely
unfiled gap. Both are in scope; nothing else is.

---

## 2. Eisenhower matrix

|  | Urgent | Not Urgent |
|---|---|---|
| **Important** | `#10976` (P0 egress), `#10000` (P0 ingress) — both `P0-blocker`, both launch-gating | **← #5090 sits here: SCHEDULE** |
| **Not Important** | — | — |

**Placement: Important / Not Urgent → Schedule.**

⚠️ **`#10976` and `#10000` sit in Important/Urgent, not here:** both are `P0-blocker` and launch-gating, and importance is not inferred from ownership —
importance from **ownership** (*"other owners"*), which is not one of the two axes. A `P0-blocker` that
gates launch is **Important** by definition. They are also *more* urgent than this epic, which is part of
why this epic does not hold the critical path.

- **Important.** 48 % of the graph is index; the vector class is the dominant line on a provisioned-RAM
  bill at $73/GiB-month with no eviction; and the decision gets structurally more expensive once a
  customer's data has landed (the migration window closes permanently).
- **Not urgent — on one leg, not two.** Nothing gates launch on it (§2's own grid: the two P0s do, and
  they are elsewhere). The earlier *second* leg — *"the owner postponed arming, a deliberate scheduling
  signal"* — is **withdrawn**: the owner authorised arming on 2026-11-03 and the block is a
  credential/environment wall. That weakens "Not Urgent" and is stated rather than quietly dropped.
- **The one genuinely urgent item is cheap and is not the epic's build:** V1's evidence, which is **parked with V1** (`03-scope.md` §1 row 4) because no move is planned and it would therefore have no consumer.
  item 3) unblocks the decision and touches no production store.

---

## 3. Profit-growth alignment

**Causal chain — recorded so it can be REFUTED, which it is.** The chain the epic assumed runs: vector
bytes sit in RAM-priced, no-eviction storage at `billing.cost_basis.falkordb_per_gb = 73.0`
(`product/pricing.json`) → every stored vector is **permanent** rent → the per-tenant storage allowance is
consumed by data that is either identifier-only (no retrieval value) or index overhead (48 % of the graph)
→ the free/pro working set is artificially small → a customer reaches the allowance before the product has
earned the price.

**⛔ Every arrow in that chain is broken on our stack, and §16.1 says so:** the bill is **provisioned** RAM,
so removing a byte changes it by **$0.00**; and under the 2026-10-08 billing ruling retained bytes are
**REVENUE**, so "store less" is not a cost strategy at all. The table below is kept **because the refutation
is the finding**, not because the chain is the justification.

**Order of magnitude — computed from the REMOVABLE figure, per tenant, not from the whole graph.**

| quantity | value | basis |
|---|---|---|
| removable vector payload today | **19.4 MB** | 12,657 × 1,536 B (the epic's own 2026-10-08 correction) |
| ⇒ rent avoided | **$0.00** | the bill is **provisioned** RAM, so neither the payload nor the index changes it (§16.1, HIGH) |
| at 10 tenants of this size | **still $0.00** | the tier is a machine size, not a data size |
| to reach **$1,000/month** | **≈ 14 GB** removable | ~730× today's payload |

⚠️ **This corrects two inflated figures in an earlier draft, and the correction matters.** That draft said
*"tens of dollars per month today"* — which is the whole 195 MB graph at $73/GiB (**$13.90**), including
the 93 MB of indices this epic explicitly defers to V1. And it said *"$100s–$1000s/month at ten or more
tenants"* — which does not reach $1000s: ten tenants is ~$140, and the storage doc's month-6 projection
(300–500k vectors × 2.05 KB = 0.6–1.0 GB) is **$45–75/month**, not thousands.

**⇒ The honest statement: this epic, as scoped, is worth about a dollar a month per tenant today, and
low tens of dollars a month at the sizes the storage doc projects for month six.** Its value is
**structural, not immediate**: it decides where a class that is 48 % of the graph lives, before customers
make that decision irreversible. It must **not** be sold as the volume fix — that is `#10000` and
`#10976`, and they are both larger.

**Faster/better path to the same profit outcome?** For the *bill*, yes: `#10000` and `#10976` remove or
bound bytes without deciding anything, and cost less than a store migration. For the *decision*, no —
there is no cheaper way to stop paying rent on four unindexed labels than to decide where they live.

---

## 4. Key assumptions

| # | assumption | confidence | what falsifies it |
|---|---|---|---|
| K1 | Vectors are stored in the graph and unindexed for four labels at HEAD | **high** — `ENTITY_TYPE_LABELS` = 5 served labels, `Point` indexed, `Point`-only DDL | a vector index appears for a non-`Point` label |
| K2 | The rule exists and is off by default | **high** — object identity, and a 4-surface config walk that finds it unset | `TORTOISE_EMBED_GATE` set in a shipped surface, or `embed_gate_enabled()` true without it |
| K3 | A cross-store vector move preserves recall on **our** query mix | **low — unmeasured, and it is the decision's hinge.** A comparable graph+`pgvector` shape measured recall@10 **0.63–0.72** after the same move; `pgvector`'s filtered ANN (`hnsw.iterative_scan`) is a capability we do not have at all today | **V1's evidence, parked with V1** (`03-scope.md` §1 row 4; `02-research.md` carries the numbers) — not this epic's work |
| K4 | The record can serve reads without the graph, so a vector move is safe | **medium.** 4 of 4 layers exist in schema and `record_*` modules exist; the record is **not armed** — owner-authorised 2026-11-03, blocked `BLOCKED-CRED / BLOCKED-ENV` — and hosted record writes are still rejected `22P02` | the record serving a query with the graph stopped |
| K5 | Arming the EMBED gate moves the write rate materially | **medium — unmeasured.** `#5008` ordered measurement first, deliberately | the rate measurement in scope item 1 |
| K6 | Object/Subject embeddings absent from the record are a *re-embed cost*, not data loss | **high** — an embedding is derivable from its text | a stored vector whose source text is itself gone |
| K7 | The two `PARTIAL` DoD rows can be closed without a new mechanism | **medium** — `#10541` closed COMPLETED with both rows still PARTIAL, so the remainder is a *reading* decision plus arming, not new code | the arming measurement showing the rate unchanged |

---

## 5. Decision

**Feature:** epic #5090 — the vector layer (what earns an embedding, and where it lives)

**Decision: REPOINT — the epic stays open with its acceptance reduced to the remainder in §7. It is not discharged, and it is not re-scoped upward.**

**Alternatives considered:** discharge as complete (A1 — rejected on `§14.3 V3 "rides on V1"` alone: the
epic is the only *actionable* record of the deferral — the doc row and the runtime warning state it but cannot be closed or reassigned — so discharging it converts a *deferred* defect
into an *accepted* one); index the four labels in the graph (A2 — rejected: V1's content, four resident
HNSW structures, ~6 ms); move vectors to Postgres now (A3 — rejected: no `pgvector` exists, the record
forbids `object`/`subject` embeddings, the record is unarmed on a cred/env wall); fold into a neighbour
(A4 — rejected: three different claims); let the neighbours dissolve the cost (A5 — adopted as the shape
of the remainder).

**Profit impact:** **none directly, and that is the finding.** The bill is on provisioned RAM (§16.1,
HIGH) and the 2026-10-08 billing ruling makes **retained bytes revenue** — so no byte-removal on this epic's
surface moves the bill or the price. What this epic is worth is **structural**: it makes the embedding rule
operative, makes the writer population's exemptions a property of the product, makes the store state its own
vector contract, and removes an inconsistency between three artifacts about a decision still open. §3's table
above is the arithmetic that **refutes** the cost story, kept because the refutation is the finding.
**Not the volume fix** (`#10000`/`#10976` are, and they are larger).

**Eisenhower placement:** Important / Not Urgent → **Schedule**. Nothing gates launch on it; the decision
gets more expensive once customer data lands; the only cheap-and-urgent item is the measurement. Its
"not urgent" leg rests on launch-gating, **not** on the owner postponing anything — that was wrong and is
withdrawn.

**Key assumptions:** K1 high · K2 high · **K3 low — the hinge** · K4 medium · K5 medium · K6 high · K7
medium.

**Recommendation:** proceed, but only to work that is either **genuinely unfiled** (the recall
measurement; the two mandated workflow artifacts; the writer-population remainder that outlived
`#10541`; the enforcement default) or **the owner's decision carrier** (V1). File nothing that duplicates
`#5008`, `#4997`, `#4333`, `#10465`, `#10541`, `#8419`, `#10976`, `#10000`, `#7879`.

**Routing:** PROCEED → `epic-research` (stage 2), then `epic-scope` (stage 3).

---

## 6. Review gate

**Cycle 1** — fresh-context reviewer, `epic-align` prompt + 8 independent fact-checks with commands.
Result: **1 P0, 4 P1, 3 P2, 1 P3**. All incorporated:

| sev | finding | disposition |
|---|---|---|
| **P0** | `#9548` was described as *"postponed by the owner"*; the owner **authorised** arming 2026-11-03T19:06Z and it is OPEN on `BLOCKED-CRED/BLOCKED-ENV` | corrected in §0's table, §1.2 A3, §2, §5, K4 |
| **P0** | *"the warning fires on every search"* is false — setup-time, once per (process, graph); query fallback logs at info/debug | §0 row 1; K1 restated |
| P1 | *"3 of 4 DoD bullets met"* is false; the probe returns **1 MET of 4** | §0 row 3, §1.1, §5 |
| P1 | DoD **row 2's** remainder omitted; `#10541` closed COMPLETED with both rows still PARTIAL | §1.3, K7, §5 routing |
| P1 | `#10976`/`#10000` (both `P0-blocker`) placed in *Not Important* | §2 grid + explicit correction |
| P1 | A1 rejected on three reasons that do not survive checking | §1.2 A1 — replaced with the `§14.3 V3` reason |
| P2 | profit inflated in the doc's own units ($13.90 from the whole graph; "$1000s" unreachable) | §3 recomputed from the removable figure per tenant |
| P2 | *"three further HNSW structures"* vs *"four unindexed labels"* | §1.1 — **four**; §12.1c's own "second…third" flagged |
| P2 | `§14.3`'s V1 row still carries the retracted ≈50 MB / 141 MB / 45 MB pair | **delivered in this lane — see §7**, together with §12.1c's *"SECOND … THIRD"* label count (a register correction, not a decision change) |
| P3 | *"6 shipped config surfaces"* — two are absent from the tree | §1.1 — *4 that exist, 6 walked* |

**Cycle 2** — fresh-context reviewer, corrections-only pass. Result: **2 P1 + 1 P3.** Incorporated:

| sev | finding | disposition |
|---|---|---|
| **P1** | **A1's sole surviving reason is falsified** — *"the epic is the only artifact binding `#4997` to V1"*; the doc row at `:1520` and the runtime warning at `projection/__init__.py:18583-18586` both state it | A1 re-argued on *actionability*, and **the decision changed from PROCEED to REPOINT** (§5, §7) |
| **P1** | the `§14.3` register correction was *recorded as incorporated* while no artifact carried it — the named target, scope §3.2, does not exist | **delivered for real in §7**, and both files corrected |
| P3 | §6 cited *"§0-preamble"*, where `#9548` appears nowhere | corrected to §0's table |

**Verified clean by the cycle-2 reviewer:** the `#9548` authorisation wording; the setup-time warning
characterisation; `1 of 4` MET; `#10541` CLOSED/COMPLETED with both rows still PARTIAL; both `P0-blocker`s
placed Important; `§14.3 V3` reads *"Rides on V1"*; the recomputed profit arithmetic; **four** unindexed
labels; *4 surfaces exist / 6 walked*.

---

## 7. REPOINT — what this epic's acceptance becomes, and the register correction

### 7.1 Why REPOINT rather than PROCEED

PROCEED would have implied the epic's existing acceptance still describes its work. It does not: one of
four DoD rows is MET, two are PARTIAL **by the measuring instrument's own design**
(`tools/dod_5090_probe.py:1264`: *"the census CLASSIFIES the unresolved set; it does not license moving
this verdict"*), and one is an owner decision. **REPOINT** is option **C** from `#7879`'s own
re-measurement comments — retire completed scope from the acceptance, keep the epic open on exactly what
remains, and let the owner close it when the decision lands. It is neither discharge (§1.2 A1) nor
re-scoping upward (inventing children the neighbours already own).

**Acceptance after repointing — the epic is done when, and only when:**

1. The embedding rule is operative on the shipped default, with the measured rate recorded (scope S1).
2. The exemption register has a production home, and DoD rows 1–2 are re-scored as a recorded decision (S2).
3. **The unretrievable vectors are censused and their removal carried with its surface-approval request**, and `#10976`/`#7879` are reconciled to **"pending V1"** (S3). **V1 itself is §14.3's and is NOT this epic's acceptance.**
4. The store reports its vector contract positively, not only when deficient (S4).
5. The test-design map and the capstone exist and pass (S5).

## 8. The prior record — what was already settled, and what this lane withdraws

**Added after the owner challenged the research depth. It was a fair challenge: this lane re-derived
things the record already held, and proposed two things the record had already refused.** Recorded in full
so the next lane does not repeat it.

### 8.1 Already settled — do not re-open

| ruling | date | home | what it settles |
|---|---|---|---|
| **D11 — stay on hosted FalkorDB; the optimisation is DEFERRED** | 2026-09-24 | **#4333** decision record | No engine migration. Self-hosting, if ever, is **priced first** — same engine, SSPLv1, no rewrite. ⚠️ **Owner, 2026-11-05: *"we're not self-hosting FalkorDB now. we have a lot of work that needs doing first."*** ⇒ **self-hosting is OUT of the option set, not deferred-and-available.** |
| **The billing model** | 2026-10-08 | graph `1a11c902dac` | Tiers are **capped, priced per feature**, not sized by volume; over cap = **overage**, not upgrade. Two metered units: **storage per byte**, **compute per extraction**. ⇒ *"fix the billing unit"* is no longer a candidate. **Corollary: because we charge per stored byte, retained bytes are REVENUE — so "store less" is NOT a strategy.** |
| **The unit** | 2026-10-25 | **#4333** | Cost is reported in exactly three things, all bytes/usage: **graph RAM bytes** (rows + indices + **the vector index**), **Postgres bytes**, **extraction usage**. *"we're moving away from nodes."* |
| **The engine's ROLE** | 2026-10-08 | §14.2 | FalkorDB **stays and is DEMOTED** to a derived hot cache. **"Shall we migrate to Postgres?" is not an open question and a lane must not re-open it.** |
| **V1's own framing** | amended 2026-10-08 | §14.3 | ⚠️ **"The hybrid makes V1 more urgent, not less"** — under the hybrid every cache byte is rent on something Postgres already stores. **V1 is a CACHE-TIER question, not a data-cost question.** |
| **V1 has a named measurement** | — | §15 **M5** | *"Where should the vector index live? — our query latency with the index resident, against a real `GRAPH.MEMORY USAGE` read."* The latency half was measured 2026-09-24 (§12.1d). |
| **The two-store cost model exists** | — | **§17.4** | Closed form: `multiplier = 1/(f + (1+ρ)/778.7)` → **0.99× at `f`=1.00, 4.83× at `f`=0.20**. §17.4.5: the split is **`f`-conditional** and **net-negative today**, because §2.0's *"keep the limit in falkor high"* **is `f = 1`**. |
| **`f` cannot be measured today** | 2026-10-17 | §17.4.6 (M8) | It needs a window over a tenant's real query mix; **hosted capture is off by owner ruling 2026-10-14** and no self-hosted tenant serves a workload. The instrument exists (`tortoise/read_ledger.py`); the reading does not. |
| **Per-graph offload is Enterprise-only** | 2026-09-24 + confirmed 2026-11-05 | #4333 (D11 record) | `falkordbe.idle-threshold-ms` (**default `0` = disabled**), `evict-interval-ms`, `/data/offload`, `GRAPH.LOAD` are documented **only** under Enterprise. ⚠️ **New in this lane:** Enterprise is offered as **BYOA inside our own AWS account**, so §16.1's *"(self-managed)"* is narrower than the vendor's offering — but **#4333 owns pricing it**, and it is **not this epic's**. |
| **Eviction cannot be a server policy** | 2026-11 | **#11004** (open) | Measured: `maxmemory-policy allkeys-lru` at a ceiling below resident size evicted **a whole graph — 20,000 → 0, silently**; `GRAPH.LIST` still named it. Every key had `ttl=-1`. That is why `noeviction` is pinned and why the sweep **refuses** until the record can serve. |
| **Who owns the storage architecture** | 2026-09-23 (re-scope) | **#4333** | Re-scoped by the owner from *investigate* to **own the storage architecture**. The changelog records as a **correction of a wrong owner**: *"§5090 is a **consumer** of the vector layer, not the enumeration"* — §17.5's placement enumeration is **#7955**'s, §17.1's routing half is **#7951**'s. |

### 8.2 What this lane withdraws

| withdrawn | why | the record that refutes it |
|---|---|---|
| **"Move the vectors to Postgres to save money"** | The bill is on **provisioned RAM**, and retained bytes are **revenue**. Removing bytes changes the bill by **$0.00**. | §16.1 (HIGH: *"cost is a function of the machine, not the data"*) · graph `1a118eb5b0a` · the 2026-10-08 billing ruling |
| **"Compress the embeddings, 8–32× smaller"** | **Already refused.** Quantization is explicitly listed as **not worth measuring — *"no engine to apply it to"***. FalkorDB's stored form is `vecf32`; there is no quantization surface. | **§15**, *"Not worth measuring (recorded so they are not re-invented)"* |
| **"Delete the unindexed vectors and re-measure the tier" — presented as new** | It is the **recorded** next move, with its own falsifier, and it was already an option on the cost decision. Presenting it as a finding was re-derivation. | graph option `1a11923050e` · the FIFTH OPTION point `1a11917857c` · the PRESCRIBED FIRST MOVE `1a118eba7b0` |
| **"There is no two-store cost model"** (implied) | §17.4 **is** that model, with measured inputs and a closed form — and it gives a sharper reason not to move than anything this lane offered. | **§17.4–§17.4.6** |
| **"Self-hosting is a live cost lever"** | **Owner, 2026-11-05: not now.** It stays recorded as D11's future option; it is **not** in this epic's option set. | D11 + owner ruling 2026-11-05 |
| **The `$1.32/month removable-payload` profit chain** | **The wrong kind of number twice over**: retained bytes are revenue (billing ruling), and the bill is provisioned RAM (V1 cost refutation). The arithmetic was corrected and the *model* was still wrong. | the 2026-10-08 billing ruling · §16.1 |

### 8.3 What survives, and it is why the decision stands

The epic's remainder is **not** a storage decision — that is `#4333`'s, and V1's home is **§14.3**. What
survives is what nobody else owns:

1. **The embedding rule is written and switched off** — a real defect, in nobody else's scope.
2. **The exemption authority lives only in a read-only measurement script** — three write paths have no
declared exemption in the product.
3. **Four of five served labels carry vectors with no index and no efficient query path** — and the *removal* of the
   unretrievable ones is authorised by the 2026-10-08 billing corollary (**later** than the 2026-09-24
   "keep the vectors" direction, and narrower: keep the searchable ones, remove the ones that can never be
   retrieved). ⚠️ This is a **contradiction-test result**, not an assumption — see scope §1.
4. **The epic has never had its two mandated workflow artifacts.**

**Decision unchanged: REPOINT.** The repointed acceptance is in §7.1, with S3 re-scoped per the above.

Two recorded numbers had gone stale in the decision register, and a stale register is read as an
instruction rather than as history — a failure class this repo has already been burned by. Neither
correction changes a decision; **V1 stays open**. Delivered in this lane:

- `docs/architecture/STORAGE-ARCHITECTURE.md` **§12.1c** — *"a SECOND … and a THIRD"* → **four**, because
the served set is `{Event, Object, Point, Source, Subject}` and only `Point` is indexed.
- `docs/architecture/STORAGE-ARCHITECTURE.md` **§14.3 V1 row** — *"≈50 MB of stored vectors + a 45 MB
  index total, on 141 MB"* → **19.4 MB of payload + a 93 MB index total, on 195 MB** (measured
  2026-11-04, graph `live-v4`). The ≈50 MB figure was retracted by this epic's own 2026-10-08 comment and
  survived in the register anyway.
